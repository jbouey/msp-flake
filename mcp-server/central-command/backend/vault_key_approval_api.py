"""Admin endpoint: mark a vault_signing_key_versions row as known_good.

#116 Sub-B per audit/coach-116-vault-admin-approval-gate-a-2026-05-17.md.

Wires the operator-facing "Mark Known-Good" workflow to:
  1. privileged_access_attestation chain (synthetic anchor
     `vault:<key_name>:v<key_version>`)
  2. mig 328 schema CHECK (attestation_bundle_id IS NOT NULL when
     known_good=TRUE)
  3. sev1 substrate invariant `vault_key_version_approved_without_
     attestation`

The Vault key is the trust root for the entire fleet's signing
pathway — every approval generates a chain-anchored attestation
so auditors can walk the chain from any compliance bundle back to
the human + reason that authorized the Vault key behind its
signature.

Why ALLOWED_EVENTS-only (NOT fleet_order): admin-API class with
no daemon consumer + no site anchor for mig 175 to gate. See Gate
A `Option B vs A` rationale.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from .fleet import get_pool
    from .tenant_middleware import admin_transaction
    from .auth import require_admin
    from .privileged_access_attestation import (
        create_privileged_access_attestation,
        PrivilegedAccessAttestationError,
    )
except ImportError:  # pragma: no cover — production package path
    from fleet import get_pool  # type: ignore[no-redef]
    from tenant_middleware import admin_transaction  # type: ignore[no-redef]
    from auth import require_admin  # type: ignore[no-redef]
    from privileged_access_attestation import (  # type: ignore[no-redef]
        create_privileged_access_attestation,
        PrivilegedAccessAttestationError,
    )


logger = logging.getLogger("vault_key_approval_api")

router = APIRouter(prefix="/api/admin", tags=["vault-key-approval"])


class MarkKnownGoodRequest(BaseModel):
    """Body for the approval endpoint.

    `expected_pubkey_hex` defends against TOCTOU per Gate A P0-4:
    the operator MUST out-of-band verify the Vault key + pubkey
    (e.g. via `vault read -format=json transit/keys/<name>`) BEFORE
    submitting. The endpoint refuses if the DB row's stored
    pubkey_hex doesn't match — catches the case where the row was
    first-observed honest but Vault state changed between first-
    observation and operator approval (rotation, compromise).
    """

    actor_email: str = Field(
        ..., min_length=5, max_length=255,
        description="Named-human email of the approving admin. "
                    "Audit-actor naming rule: NEVER 'system' / "
                    "'admin' / 'operator' / 'fleet-cli'.",
    )
    reason: str = Field(
        ..., min_length=20, max_length=500,
        description="Operational reason ≥20ch. Appears on auditor "
                    "kit + chain-of-custody record.",
    )
    expected_pubkey_hex: str = Field(
        ..., min_length=64, max_length=128,
        description="Ed25519 pubkey (hex) the operator out-of-band "
                    "observed in Vault. Must match the row's stored "
                    "pubkey_hex (TOCTOU defense).",
    )


_BANNED_ACTORS = frozenset(
    {"system", "admin", "operator", "fleet-cli", ""}
)


@router.post("/vault/key-versions/{key_version_id}/mark-known-good")
async def mark_vault_key_version_known_good(
    key_version_id: int,
    req: MarkKnownGoodRequest,
    request: Request,
    admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Approve a vault_signing_key_versions row as known_good=TRUE.

    Atomic single-txn sequence per Gate A P0-4:
      1. SELECT FOR UPDATE the row (lock against concurrent approval)
      2. Idempotency: 409 if known_good=TRUE already
      3. TOCTOU defense: expected_pubkey_hex == row.pubkey_hex
      4. Named-actor validation: req.actor_email NOT in _BANNED_ACTORS
      5. create_privileged_access_attestation with anchor
         'vault:<key_name>:v<key_version>'
      6. UPDATE the row SET known_good=TRUE, approved_by,
         approved_at, attestation_bundle_id
      7. admin_audit_log row for operator-visible UI
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

    pool = await get_pool()
    # Per Gate B P1-1 (audit/coach-116-sub-b-gate-b-2026-05-17.md) +
    # tenant_middleware.py:147-157 routing-risk caveat: this is a
    # multi-statement admin path (SELECT FOR UPDATE → attestation
    # writes → UPDATE → audit_log INSERT). PgBouncer transaction-pool
    # mode can route SET LOCAL app.is_admin + subsequent statements
    # to different backends under admin_connection — bug class
    # documented in commit 303421cc. admin_transaction is the
    # canonical helper that pins all queries to one backend within
    # one explicit transaction.
    async with admin_transaction(pool) as conn:
        # Step 1: lock the row.
        row = await conn.fetchrow(
            """
            SELECT id, key_name, key_version, pubkey_hex,
                   known_good, approved_by, approved_at,
                   attestation_bundle_id
              FROM vault_signing_key_versions
             WHERE id = $1
             FOR UPDATE
            """,
            key_version_id,
        )
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"vault_signing_key_versions row {key_version_id} not found",
            )

        # Step 2: idempotency.
        if row["known_good"]:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"row already known_good=TRUE (approved_by="
                    f"{row['approved_by']!r} at {row['approved_at']}). "
                    f"No-op to avoid overwriting the prior attestation."
                ),
            )

        # Step 3: TOCTOU defense — operator must have observed the
        # SAME pubkey at approval time as the row recorded at first
        # observation. Case-insensitive compare on hex.
        if (req.expected_pubkey_hex or "").lower() != (row["pubkey_hex"] or "").lower():
            raise HTTPException(
                status_code=409,
                detail=(
                    f"expected_pubkey_hex does not match row pubkey_hex "
                    f"— Vault state may have rotated since first "
                    f"observation. Re-fetch the Vault key + reissue "
                    f"the approval against the NEWER row (or refuse "
                    f"if the rotation was unauthorized)."
                ),
            )

        # Synthetic anchor per Gate A P0-3.
        anchor = f"vault:{row['key_name']}:v{row['key_version']}"

        # Step 5: write the attestation BEFORE the UPDATE so a
        # failure here aborts the txn cleanly (UPDATE doesn't fire).
        try:
            attestation = await create_privileged_access_attestation(
                conn,
                site_id=anchor,
                event_type="vault_key_version_approved",
                actor_email=actor,
                reason=req.reason.strip(),
                origin_ip=request.client.host if request.client else None,
                approvals=[
                    {
                        "vault_key_version_id": int(row["id"]),
                        "key_name": row["key_name"],
                        "key_version": int(row["key_version"]),
                        "pubkey_hex": row["pubkey_hex"],
                    }
                ],
            )
        except PrivilegedAccessAttestationError as e:
            logger.error(
                "vault_key_approval attestation failed id=%s err=%s",
                key_version_id, e, exc_info=True,
            )
            raise HTTPException(
                status_code=502,
                detail=f"attestation failed: {e}",
            )

        # Step 6: flip the row. Mig 328 CHECK enforces the trio.
        await conn.execute(
            """
            UPDATE vault_signing_key_versions
               SET known_good = TRUE,
                   approved_by = $1,
                   approved_at = NOW(),
                   attestation_bundle_id = $2
             WHERE id = $3
            """,
            actor,
            attestation["bundle_id"],
            key_version_id,
        )

        # Step 7: operator-visible audit row.
        await conn.execute(
            """
            INSERT INTO admin_audit_log
                (username, action, target, details, ip_address, created_at)
            VALUES ($1, 'vault_key_version_approved', $2, $3::jsonb,
                    NULL, NOW())
            """,
            actor,
            anchor,
            json.dumps({
                "vault_key_version_id": int(row["id"]),
                "key_name": row["key_name"],
                "key_version": int(row["key_version"]),
                "attestation_bundle_id": attestation["bundle_id"],
                "pubkey_hex": row["pubkey_hex"],
            }),
        )

    logger.warning(
        "vault_key_version_approved id=%s key_name=%s v=%s actor=%s "
        "bundle=%s",
        key_version_id, row["key_name"], row["key_version"], actor,
        attestation["bundle_id"],
    )
    return {
        "ok": True,
        "vault_key_version_id": int(row["id"]),
        "key_name": row["key_name"],
        "key_version": int(row["key_version"]),
        "actor": actor,
        "attestation_bundle_id": attestation["bundle_id"],
        "chain_position": attestation["chain_position"],
        "anchor": anchor,
    }
