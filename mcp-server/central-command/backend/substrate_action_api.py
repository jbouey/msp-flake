"""POST /api/admin/substrate/action — scoped, non-operator-safe admin actions.

Dispatches into the SUBSTRATE_ACTIONS registry (substrate_actions.py).
No fleet order dispatch, no customer infra mutation.

See spec: docs/superpowers/specs/2026-04-19-substrate-operator-controls-design.md Section 2.

Guarantees:
  - Feature flag SUBSTRATE_ACTIONS_ENABLED must be "true" to process requests.
  - Admin session auth required via require_auth.
  - Idempotency: (actor_email, Idempotency-Key) replay within 24h returns
    the prior result with status="already_completed".
  - Writes one admin_audit_log row + one substrate_action_invocations row
    per successful invocation, inside a single transaction with the handler.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
from datetime import datetime, timezone
from typing import Any, Optional

from asyncpg.exceptions import UniqueViolationError
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

# Dual-path imports: bare-name works when backend/ is directly on sys.path
# (pytest from tests/), relative works when loaded via the dashboard_api.
# package path (main.py). See runbook_config.py for the same pattern.
try:
    from auth import require_auth
    from assertions import ALL_ASSERTIONS, _DISPLAY_METADATA
    from shared import check_rate_limit
    from substrate_actions import (
        SUBSTRATE_ACTIONS,
        SubstrateActionError,
        TargetNotActionable,
        TargetNotFound,
        TargetRefInvalid,
    )
    from tenant_middleware import admin_connection
except ImportError:
    from .auth import require_auth
    from .assertions import ALL_ASSERTIONS, _DISPLAY_METADATA
    from .shared import check_rate_limit
    from .substrate_actions import (
        SUBSTRATE_ACTIONS,
        SubstrateActionError,
        TargetNotActionable,
        TargetNotFound,
        TargetRefInvalid,
    )
    from .tenant_middleware import admin_connection

# fleet.py itself has `from .tenant_middleware import admin_connection` at
# module scope, so it is ONLY importable in the packaged runtime context
# (production main.py loads as dashboard_api.main). In the pytest context
# (backend/ on sys.path directly, no parent package), loading fleet.py fails
# and we intentionally leave get_pool as None — none of the non-DB-gated
# tests exercise the DB path, and DB-gated tests provide their own pool via
# fixture + admin_connection(pool). Hoisted to module scope for clarity over
# the Task-6 lazy-inside-handler pattern.
try:
    from .fleet import get_pool  # type: ignore[attr-defined]
except ImportError:
    try:
        from fleet import get_pool  # type: ignore[no-redef]
    except ImportError:
        get_pool = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/substrate", tags=["admin", "substrate"])


class ActionBody(BaseModel):
    """Request body for POST /api/admin/substrate/action."""

    action_key: str = Field(..., min_length=1, max_length=64)
    target_ref: dict[str, Any]
    reason: str = Field(default="")


# Per-action rate limits: (window_seconds, max_requests_per_actor_per_window).
# Keyed by the registered action_key in SUBSTRATE_ACTIONS. Tuned for the
# Phase-1 operator set — a single human hitting dozens of install-session
# rows after a v38 fleet reflash is normal; dozens of platform-account
# unlocks in an hour is not.
#
# Rate key is "substrate.<action_key>" scoped by actor_email (not site_id).
# Rationale: the operator is the unit of abuse, not the customer site —
# install_loop cleanups cut across sites.
RATE_LIMITS: dict[str, tuple[int, int]] = {
    "cleanup_install_session": (3600, 60),
    "unlock_platform_account": (3600, 10),
    "reconcile_fleet_order":   (3600, 20),
}


def _feature_flag_enabled() -> bool:
    """Read SUBSTRATE_ACTIONS_ENABLED at request-time (not module-load).

    Flip-without-restart is a rollout requirement — do NOT hoist this to
    module scope.
    """
    return os.getenv("SUBSTRATE_ACTIONS_ENABLED", "false").lower() == "true"


def _derive_idempotency_key(
    body: ActionBody,
    actor_email: str,
    header_key: Optional[str],
) -> str:
    """If client sent Idempotency-Key, use it verbatim. Otherwise derive a
    deterministic key from (actor, action, target, UTC date) so accidental
    double-clicks within a single UTC day dedupe, but the same logical
    action the next day gets a fresh key.
    """
    if header_key:
        return header_key
    day = datetime.now(timezone.utc).date().isoformat()  # e.g. "2026-04-19"
    # Canonicalize target_ref so clients sending the same logical payload with
    # different key orderings / whitespace still dedupe. Python's dict repr is
    # insertion-ordered (3.7+) but is NOT a canonical wire form across
    # languages; json.dumps(sort_keys=True, separators=(',', ':')) is.
    material = (
        f"{actor_email}|{body.action_key}|"
        f"{json.dumps(body.target_ref, sort_keys=True, separators=(',', ':'))}"
        f"|{day}"
    )
    return hashlib.sha256(material.encode()).hexdigest()


@router.post("/action")
async def post_substrate_action(
    body: ActionBody,
    request: Request,
    user: dict = Depends(require_auth),
):
    """Dispatch a substrate action from the SUBSTRATE_ACTIONS registry.

    Handler exception → HTTP mapping:
      TargetRefInvalid    → 400
      TargetNotFound      → 404
      TargetNotActionable → 409
      SubstrateActionError (base) → 500 (with exc_info=True logging)
    """
    if not _feature_flag_enabled():
        logger.info("substrate_action_rejected_flag_off")
        raise HTTPException(
            status_code=503,
            detail={
                "reason": "substrate_actions_disabled",
                "flag": "SUBSTRATE_ACTIONS_ENABLED",
                "message": (
                    "Substrate action endpoint is off. Set "
                    "SUBSTRATE_ACTIONS_ENABLED=true on mcp-server to enable."
                ),
            },
        )

    action = SUBSTRATE_ACTIONS.get(body.action_key)
    if action is None:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "unknown action_key",
                "valid_keys": sorted(SUBSTRATE_ACTIONS.keys()),
            },
        )

    if len(body.reason) < action.required_reason_chars:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": (
                    f"reason must be >= {action.required_reason_chars} chars"
                ),
            },
        )

    actor_email = user.get("email") or user.get("username") or ""
    if not actor_email:
        raise HTTPException(
            status_code=401,
            detail="no actor email on session",
        )

    rl_config = RATE_LIMITS.get(body.action_key)
    if rl_config is not None:
        rl_window, rl_max = rl_config
        allowed, retry_after = await check_rate_limit(
            actor_email,
            f"substrate.{body.action_key}",
            window_seconds=rl_window,
            max_requests=rl_max,
        )
        if not allowed:
            logger.info(
                "substrate_action_rate_limited",
                extra={
                    "actor": actor_email,
                    "action_key": body.action_key,
                    "retry_after": retry_after,
                },
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "reason": "rate_limit_exceeded",
                    "action_key": body.action_key,
                    "window_seconds": rl_window,
                    "max_requests": rl_max,
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(max(retry_after, 1))},
            )

    idem_key = _derive_idempotency_key(
        body,
        actor_email,
        request.headers.get("Idempotency-Key"),
    )

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # 1) Idempotency replay check — belt + suspenders ORDER BY LIMIT 1.
        # The unique index (actor_email, idempotency_key) guarantees at most
        # one row; ORDER BY/LIMIT protects against the edge case where a row
        # outside the 24h window exists and the index constraint fires.
        prior = await conn.fetchrow(
            "SELECT id, result_body FROM substrate_action_invocations "
            "WHERE actor_email = $1 AND idempotency_key = $2 "
            "  AND created_at > now() - INTERVAL '24 hours' "
            "ORDER BY created_at DESC LIMIT 1",
            actor_email,
            idem_key,
        )
        if prior is not None:
            # asyncpg decodes JSONB → dict on fetch — don't json.loads again.
            reply = dict(prior["result_body"])
            reply["status"] = "already_completed"
            reply["action_id"] = str(prior["id"])
            return reply

        # 2) Run handler + write audit + write invocation in ONE transaction
        # so partial failures roll back cleanly. HTTPException inside the
        # context manager propagates up and rolls back before re-raising.
        try:
            async with conn.transaction():
                try:
                    summary = await action.handler(
                        conn, body.target_ref, body.reason
                    )
                except TargetRefInvalid as e:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "reason": "target_ref_invalid",
                            "message": str(e),
                        },
                    )
                except TargetNotFound as e:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "reason": "target_not_found",
                            "message": str(e),
                        },
                    )
                except TargetNotActionable as e:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "reason": "target_not_actionable",
                            "message": str(e),
                        },
                    )
                except SubstrateActionError:
                    # Drop str(e) from the wire; operators read the full
                    # message (incl. SQL fragments / hostnames) via the
                    # log line below.
                    logger.error(
                        "substrate_action_failed",
                        exc_info=True,
                        extra={
                            "action_key": body.action_key,
                            "actor": actor_email,
                        },
                    )
                    raise HTTPException(
                        status_code=500,
                        detail={"reason": "internal_error"},
                    )

                # admin_audit_log: direct INSERT — no wrapper module exists.
                # target VARCHAR(255): stable synthetic ref, not a Python repr.
                # details JSONB: json.dumps + ::jsonb cast (asyncpg won't
                # auto-encode dict → JSONB without a codec).
                audit_id = await conn.fetchval(
                    "INSERT INTO admin_audit_log "
                    "(user_id, username, action, target, details, ip_address) "
                    "VALUES (NULL, $1, $2, $3, $4::jsonb, $5) "
                    "RETURNING id",
                    actor_email,
                    action.audit_action,
                    f"substrate_action:{body.action_key}"[:255],
                    json.dumps(
                        {
                            "reason": body.reason,
                            "target_ref": body.target_ref,
                            "result": summary,
                        }
                    ),
                    request.client.host if request.client else None,
                )

                # Invocation row — idempotency checkpoint + pointer to audit
                # row. UniqueViolationError here is caught OUTSIDE this
                # transaction so it rolls back cleanly (including the audit
                # row written above).
                result_body = {"status": "completed", "details": summary}
                inv_id = await conn.fetchval(
                    "INSERT INTO substrate_action_invocations "
                    "(idempotency_key, actor_email, action_key, target_ref, "
                    " reason, result_status, result_body, admin_audit_id) "
                    "VALUES ($1, $2, $3, $4::jsonb, $5, 'completed', "
                    "        $6::jsonb, $7) "
                    "RETURNING id",
                    idem_key,
                    actor_email,
                    body.action_key,
                    json.dumps(body.target_ref),
                    body.reason,
                    json.dumps(result_body),
                    audit_id,
                )
        except UniqueViolationError:
            # Race: a prior row existed outside our 24h SELECT window, or a
            # concurrent request won the INSERT. The unique index
            # (actor_email, idempotency_key) is the last-line defense. The
            # transaction above has rolled back (audit row + any handler
            # side effects gone). Re-read with NO time filter and return
            # the prior row as a replay — same shape as the pre-flight hit.
            replay = await conn.fetchrow(
                "SELECT id, result_body FROM substrate_action_invocations "
                "WHERE actor_email = $1 AND idempotency_key = $2",
                actor_email,
                idem_key,
            )
            if replay is None:
                # Shouldn't happen — the unique index fired but no row is
                # visible. Surface for alerting.
                logger.error(
                    "substrate_action_idem_collision_but_no_row",
                    extra={
                        "actor_email": actor_email,
                        "idem_key": idem_key,
                    },
                )
                raise HTTPException(
                    status_code=500,
                    detail={"reason": "idempotency_inconsistent"},
                )
            reply = dict(replay["result_body"])
            reply["status"] = "already_completed"
            reply["action_id"] = str(replay["id"])
            return reply

        return {
            "action_id": str(inv_id),
            **result_body,
        }


# ---------------------------------------------------------------------------
# GET /api/admin/substrate/runbook/{invariant} — serves the markdown stub
# for the named invariant. Consumed by the RunbookDrawer on the frontend.
#
# Security: invariant name must match ^[a-z0-9_]+$ (blocks traversal). The
# name must also appear in ALL_ASSERTIONS — stale/unknown names 404 rather
# than sniff the filesystem. The path is joined from a fixed _DOCS_DIR so
# the regex guard is the only attack surface.
# ---------------------------------------------------------------------------

# substrate_action_api.py lives at
#   mcp-server/central-command/backend/substrate_action_api.py
# parents[3] reaches the repo root.
_DOCS_DIR = pathlib.Path(__file__).resolve().parents[3] / "docs" / "substrate"

# Built once at import. If ALL_ASSERTIONS mutates at runtime (it doesn't —
# the list is module-level immutable in practice), restart required.
_KNOWN_INVARIANTS = {a.name for a in ALL_ASSERTIONS}
_INVARIANT_SEVERITY = {a.name: a.severity for a in ALL_ASSERTIONS}

_SAFE_INVARIANT_NAME = re.compile(r"^[a-z0-9_]+$")


@router.get("/runbook/{invariant}")
async def get_runbook(invariant: str, user: dict = Depends(require_auth)):
    """Return the runbook markdown + metadata for the named invariant.

    Response shape:
        {
          "invariant": "install_loop",
          "display_name": "Box is reboot-looping at install stage",
          "severity": "sev1",
          "markdown": "# install_loop\\n\\n…"
        }

    Errors:
        400 — invariant name fails the ^[a-z0-9_]+$ regex.
        404 — invariant not in ALL_ASSERTIONS, or doc file missing.
    """
    if not _SAFE_INVARIANT_NAME.match(invariant):
        raise HTTPException(
            status_code=400,
            detail="invariant name must match ^[a-z0-9_]+$",
        )
    if invariant not in _KNOWN_INVARIANTS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown invariant: {invariant}",
        )
    path = _DOCS_DIR / f"{invariant}.md"
    if not path.exists():
        # Doc lockstep test catches this in CI, but guard at runtime in
        # case something got deleted post-deploy.
        raise HTTPException(
            status_code=404,
            detail=f"doc missing: docs/substrate/{invariant}.md",
        )
    meta = _DISPLAY_METADATA.get(invariant, {})
    # Severity is authoritative on the Assertion object, not the metadata.
    return {
        "invariant": invariant,
        "display_name": meta.get("display_name", invariant),
        "severity": _INVARIANT_SEVERITY[invariant],
        "markdown": path.read_text(),
    }


# Whitelist: invariant -> registered handler key in SUBSTRATE_ACTIONS.
# Kept deliberately short. Mirrors the frontend INVARIANT_ACTIONS map in
# AdminSubstrateHealth.tsx. Changing one requires changing the other.
_INVARIANT_ACTION_WHITELIST: dict[str, str] = {
    "install_loop": "cleanup_install_session",
    "install_session_ttl": "cleanup_install_session",
    "auth_failure_lockout": "unlock_platform_account",
    "agent_version_lag": "reconcile_fleet_order",
}


@router.get("/runbooks")
async def list_runbooks(user: dict = Depends(require_auth)):
    """Return every registered invariant as a runbook-library entry.

    Response shape:
        {"items": [
            {"invariant": "install_loop", "display_name": "…",
             "severity": "sev1", "has_action": true,
             "action_key": "cleanup_install_session"},
            ...
        ]}

    Powers the frontend Runbook Library page (Task 16). The runbook prose
    itself still loads via GET /api/admin/substrate/runbook/{invariant}.
    """
    items = []
    for a in ALL_ASSERTIONS:
        meta = _DISPLAY_METADATA.get(a.name, {})
        action_key = _INVARIANT_ACTION_WHITELIST.get(a.name)
        items.append({
            "invariant": a.name,
            "display_name": meta.get("display_name", a.name),
            "severity": a.severity,
            "has_action": action_key is not None,
            "action_key": action_key,
        })
    return {"items": items}
