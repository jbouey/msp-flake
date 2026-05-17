"""Admin endpoint: batch bearer revocation primitive (#123 Sub-B).

Per audit/coach-123-batch-bearer-revocation-gate-a-2026-05-17.md +
audit/coach-123-sub-b-design-gate-a-2026-05-17.md (BLOCK, redesigned
to v2) + audit/coach-123-sub-b-design-gate-a-recheck-2026-05-17.md
(APPROVE-WITH-FIXES). Design at .agent/plans/123-sub-b-design-
2026-05-17.md.

POST /api/admin/sites/{site_id}/appliances/revoke-bearers

See the design doc for the full atomic sequence. Briefly: inside
one admin_transaction, lock the rows, partition into to-flip vs
already-revoked, write the privileged_access attestation, flip
columns for to-flip only, write admin_audit_log row.

BAA enforcement: bulk_bearer_revoke is registered in
baa_enforcement._DEFERRED_WORKFLOWS — emergency workforce-access
revocation MUST NOT be gated on BAA-on-file because the perverse
outcome of blocking revocation DURING a BAA-related breach defeats
the security purpose.

Privileged-chain registration (Sub-A foundation):
  - fleet_cli.PRIVILEGED_ORDER_TYPES
  - privileged_access_attestation.ALLOWED_EVENTS
  - mig 329 v_privileged_types
  - PYTHON_ONLY allowlist (daemon never receives this order; its
    next checkin hits 401 via shared.py:614-640 short-circuit)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field, field_validator

try:
    from .fleet import get_pool
    from .tenant_middleware import admin_transaction
    from .auth import require_admin
    from .privileged_access_attestation import (
        create_privileged_access_attestation,
        count_recent_privileged_events,
        PrivilegedAccessAttestationError,
    )
except ImportError:  # pragma: no cover — production package path
    from fleet import get_pool  # type: ignore[no-redef]
    from tenant_middleware import admin_transaction  # type: ignore[no-redef]
    from auth import require_admin  # type: ignore[no-redef]
    from privileged_access_attestation import (  # type: ignore[no-redef]
        create_privileged_access_attestation,
        count_recent_privileged_events,
        PrivilegedAccessAttestationError,
    )


logger = logging.getLogger("bulk_bearer_revoke_api")

router = APIRouter(prefix="/api/admin", tags=["bulk-bearer-revoke"])


# Gate A v2 P0-1: appliance_id is text/varchar in prod (verified via
# prod_column_types.json). Pydantic uses List[str] + regex shape-check;
# SQL uses ::text[]. NEVER ::uuid[] — pinned by
# tests/test_no_uuid_cast_on_text_column.py.
_APPLIANCE_ID_RE = re.compile(r"^[a-fA-F0-9-]{32,40}$")

# Banned-actor list mirrors vault_key_approval_api.py:87-89 +
# privileged-chain CLAUDE.md rule "actor MUST be a named human email".
_BANNED_ACTORS = frozenset(
    {"system", "admin", "operator", "fleet-cli", ""}
)

# Gate B P1-1 (audit/coach-123-sub-b-impl-gate-b-2026-05-17.md):
# per-site rate limit caps a compromised-admin nuclear-loop.
# Mirrors the fleet_cli.PRIVILEGED_RATE_LIMIT_PER_WEEK = 3 cap for
# CLI-issued privileged orders. 3 emergency bearer revocations at one
# site in a 7-day window is already extraordinary — anything higher
# is either a real incident wave (operator should call a separate
# admin-recovery flow) or a compromised admin spinning the endpoint.
_RATE_LIMIT_WINDOW_DAYS = 7
_RATE_LIMIT_PER_WINDOW = 3


class RevokeBearersRequest(BaseModel):
    """Body for the revocation endpoint.

    max_length=50 caps blast radius (mirrors #118 fan-out cap). A 250-
    appliance fleet needs a 5-call sequence — operator confirms each
    in turn rather than one nuclear button. For full-fleet revocation,
    see deferred task #124 (--all-at-partner needs OWN Gate A).
    """

    appliance_ids: List[str] = Field(min_length=1, max_length=50)
    actor_email: EmailStr
    reason: str = Field(min_length=20, max_length=1000)
    incident_correlation_id: Optional[str] = None

    @field_validator("appliance_ids")
    @classmethod
    def _check_shape(cls, v: List[str]) -> List[str]:
        for aid in v:
            if not _APPLIANCE_ID_RE.match(aid):
                raise ValueError(
                    f"appliance_id has wrong shape: {aid!r}"
                )
        return v


@router.post("/sites/{site_id}/appliances/revoke-bearers")
async def revoke_bearers(
    site_id: str,
    req: RevokeBearersRequest,
    request: Request,
    admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Batch revoke appliance bearers at {site_id}.

    Atomic single-txn sequence:
      1. SELECT FOR UPDATE the rows (TOCTOU lock + partition)
      2. 404 if any appliance_id is missing OR soft-deleted (identical
         body — Gate A v2 P0-3 existence-oracle fix)
      3. Validate actor_email is a named human
      4. create_privileged_access_attestation with site_id anchor +
         target_appliance_ids=req.appliance_ids
      5. UPDATE site_appliances.bearer_revoked = TRUE for to_flip[]
      6. UPDATE api_keys.active = FALSE for to_flip[]
      7. admin_audit_log row with not_actionable denormalized for
         forensics (admin-context only)
    """
    actor = (req.actor_email or "").strip().lower()
    if "@" not in actor or actor in _BANNED_ACTORS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"actor_email must name a real human (got {actor!r}). "
                f"Banned values: {sorted(_BANNED_ACTORS)!r}."
            ),
        )

    appliance_ids = sorted(set(req.appliance_ids))  # dedup + canonical order

    pool = await get_pool()
    # admin_transaction per CLAUDE.md "admin_transaction for multi-
    # statement admin paths" rule — PgBouncer transaction-pool mode
    # would route SET LOCAL + subsequent statements to different
    # backends under admin_connection.
    async with admin_transaction(pool) as conn:
        # Step 1: lock + lookup.
        # NOTE: appliance_id is `text`/`character varying` in prod —
        # use ::text[] cast (Gate A v2 P0-1). NEVER ::uuid[].
        existing = await conn.fetch(
            """
            SELECT appliance_id, hostname, bearer_revoked, deleted_at
              FROM site_appliances
             WHERE site_id = $1
               AND appliance_id = ANY($2::text[])
             ORDER BY appliance_id
             FOR UPDATE
            """,
            site_id, appliance_ids,
        )

        # Step 2: 404 unification for missing OR soft-deleted
        # (Gate A v2 P0-3 existence-oracle fix). Distinction logged
        # to admin_audit_log.details only.
        live_rows = [r for r in existing if r["deleted_at"] is None]
        live_set = {r["appliance_id"] for r in live_rows}
        not_actionable = sorted(set(appliance_ids) - live_set)
        if not_actionable:
            # Audit the failed call before raising — operator
            # forensic trail even for invalid attempts. Best-effort:
            # don't let audit-write failure shadow the 404.
            try:
                await conn.execute(
                    """
                    INSERT INTO admin_audit_log
                        (username, action, target, details, ip_address, created_at)
                    VALUES ($1, 'bulk_bearer_revoke_rejected', $2, $3::jsonb,
                            $4, NOW())
                    """,
                    actor,
                    site_id,
                    json.dumps({
                        "reason_code": "appliance_ids_not_actionable",
                        "not_actionable_count": len(not_actionable),
                        # NOTE: ID details denormalized for admin-only
                        # forensics. Caller's 404 body does NOT echo
                        # this back (existence-oracle defense).
                        "not_actionable": not_actionable,
                        "operator_reason": req.reason,
                    }),
                    request.client.host if request.client else None,
                )
            except Exception:
                logger.warning(
                    "bulk_bearer_revoke audit-on-404 failed site=%s actor=%s",
                    site_id, actor, exc_info=True,
                )
            raise HTTPException(
                status_code=404,
                detail="one or more appliance_ids not found at this site",
            )

        # Idempotency partition (P1-5): to_flip[] are rows we'll
        # mutate; already_revoked[] get the fresh attestation without
        # column flips. Both are included in the attestation's
        # target_appliance_ids (auditor sees the full operator intent).
        to_flip = sorted(
            r["appliance_id"] for r in live_rows
            if not r["bearer_revoked"]
        )
        already_revoked = sorted(
            r["appliance_id"] for r in live_rows
            if r["bearer_revoked"]
        )
        hostnames_by_id = {r["appliance_id"]: r["hostname"] for r in live_rows}

        # Gate B P1-1 rate-limit check (compromised-admin defense).
        # Counts privileged_access attestations with this event_type
        # at this site in the last 7 days. Cap = 3. Operator who hits
        # the cap during a real incident wave should escalate via
        # CISO-paged emergency admin-recovery flow (out of scope).
        recent = await count_recent_privileged_events(
            conn,
            site_id=site_id,
            days=_RATE_LIMIT_WINDOW_DAYS,
            event_type="bulk_bearer_revoke",
        )
        if recent >= _RATE_LIMIT_PER_WINDOW:
            logger.warning(
                "bulk_bearer_revoke rate-limited site=%s actor=%s "
                "recent=%d cap=%d",
                site_id, actor, recent, _RATE_LIMIT_PER_WINDOW,
            )
            raise HTTPException(
                status_code=429,
                detail=(
                    f"rate limit exceeded: {recent} bulk_bearer_revoke "
                    f"events at this site in the last "
                    f"{_RATE_LIMIT_WINDOW_DAYS} days (cap "
                    f"{_RATE_LIMIT_PER_WINDOW}). Escalate via CISO-paged "
                    f"emergency admin-recovery flow if this is a real "
                    f"incident wave."
                ),
            )

        # Step 4: write attestation FIRST so a failure aborts the txn
        # cleanly before any UPDATEs land.
        try:
            attestation = await create_privileged_access_attestation(
                conn,
                site_id=site_id,
                event_type="bulk_bearer_revoke",
                actor_email=actor,
                reason=req.reason.strip(),
                origin_ip=request.client.host if request.client else None,
                target_appliance_ids=appliance_ids,
                approvals=[{
                    "target_appliance_ids": appliance_ids,
                    "to_flip": to_flip,
                    "already_revoked": already_revoked,
                    "incident_correlation_id": req.incident_correlation_id,
                }],
            )
        except PrivilegedAccessAttestationError as e:
            logger.error(
                "bulk_bearer_revoke attestation failed site=%s actor=%s err=%s",
                site_id, actor, e, exc_info=True,
            )
            raise HTTPException(
                status_code=502,
                detail=f"attestation failed: {e}",
            )

        # Step 5 + 6: column flips for to_flip[] only.
        if to_flip:
            await conn.execute(
                """
                UPDATE site_appliances
                   SET bearer_revoked = TRUE
                 WHERE site_id = $1
                   AND appliance_id = ANY($2::text[])
                """,
                site_id, to_flip,
            )
            await conn.execute(
                """
                UPDATE api_keys
                   SET active = FALSE
                 WHERE site_id = $1
                   AND appliance_id = ANY($2::text[])
                """,
                site_id, to_flip,
            )

        # Step 7: operator-visible audit row.
        await conn.execute(
            """
            INSERT INTO admin_audit_log
                (username, action, target, details, ip_address, created_at)
            VALUES ($1, 'bulk_bearer_revoke', $2, $3::jsonb,
                    $4, NOW())
            """,
            actor,
            site_id,
            json.dumps({
                "site_id": site_id,
                "to_flip_count": len(to_flip),
                "to_flip": to_flip,
                "already_revoked_count": len(already_revoked),
                "already_revoked": already_revoked,
                "attestation_bundle_id": attestation["bundle_id"],
                "incident_correlation_id": req.incident_correlation_id,
                "operator_reason": req.reason,
            }),
            request.client.host if request.client else None,
        )

    logger.warning(
        "bulk_bearer_revoke site=%s actor=%s to_flip=%d "
        "already_revoked=%d bundle=%s",
        site_id, actor, len(to_flip), len(already_revoked),
        attestation["bundle_id"],
    )
    return {
        "ok": True,
        "site_id": site_id,
        "actor": actor,
        "attestation_bundle_id": attestation["bundle_id"],
        "chain_position": attestation["chain_position"],
        "revoked": [
            {"appliance_id": aid, "hostname": hostnames_by_id.get(aid)}
            for aid in to_flip
        ],
        "already_revoked": [
            {"appliance_id": aid, "hostname": hostnames_by_id.get(aid)}
            for aid in already_revoked
        ],
    }
