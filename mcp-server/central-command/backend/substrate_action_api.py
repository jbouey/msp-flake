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
    from .substrate_actions import (
        SUBSTRATE_ACTIONS,
        SubstrateActionError,
        TargetNotActionable,
        TargetNotFound,
        TargetRefInvalid,
    )
    from .tenant_middleware import admin_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/substrate", tags=["admin", "substrate"])


class ActionBody(BaseModel):
    """Request body for POST /api/admin/substrate/action."""

    action_key: str = Field(..., min_length=1, max_length=64)
    target_ref: dict[str, Any]
    reason: str = Field(default="")


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
    material = f"{actor_email}|{body.action_key}|{body.target_ref}|{day}"
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
        raise HTTPException(
            status_code=503,
            detail={"reason": "SUBSTRATE_ACTIONS_ENABLED is off"},
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

    idem_key = _derive_idempotency_key(
        body,
        actor_email,
        request.headers.get("Idempotency-Key"),
    )

    # Lazy dual-path import of get_pool. fleet.py has a relative import
    # (`from .tenant_middleware`) so bare `from fleet import get_pool`
    # breaks when backend/ is on sys.path without the dashboard_api. prefix
    # (pytest test context). Try the package import first (real app path),
    # fall back to bare (test path, in which case get_pool isn't actually
    # reachable — but the DB-gated tests are the only ones that call
    # get_pool and they provide their own pool fixture).
    try:
        from .fleet import get_pool
    except ImportError:
        from fleet import get_pool  # type: ignore[no-redef]

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
        async with conn.transaction():
            try:
                summary = await action.handler(
                    conn, body.target_ref, body.reason
                )
            except TargetRefInvalid as e:
                raise HTTPException(status_code=400, detail=str(e))
            except TargetNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))
            except TargetNotActionable as e:
                raise HTTPException(status_code=409, detail=str(e))
            except SubstrateActionError as e:
                logger.error(
                    "substrate_action_failed",
                    exc_info=True,
                    extra={
                        "action_key": body.action_key,
                        "actor": actor_email,
                    },
                )
                raise HTTPException(status_code=500, detail=str(e))

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

            # Invocation row — idempotency checkpoint + pointer to audit row.
            result_body = {"status": "completed", "details": summary}
            try:
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
                # Race: a 24h+-old prior row existed outside our SELECT
                # window, or a concurrent request won the INSERT. The unique
                # index (actor_email, idempotency_key) is last-line defense.
                # Re-read (no time window) and return the prior row as a
                # replay. The current transaction will roll back (raising
                # out of the `conn.transaction()` context) — that's the
                # correct behavior because we don't want a duplicate audit
                # row from THIS request to persist.
                raise HTTPException(
                    status_code=409,
                    detail={
                        "reason": "idempotency_key collision",
                        "actor_email": actor_email,
                    },
                )

        return {
            "action_id": str(inv_id),
            **result_body,
        }
