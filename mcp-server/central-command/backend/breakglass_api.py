"""breakglass_api.py — Session 207 Phase R.

Per-appliance break-glass passphrase surface.

Two endpoints:

1. POST /api/provision/breakglass-submit
   Called by msp-first-boot.service once at provisioning. Takes the
   random passphrase the appliance generated locally, Fernet-encrypts
   it via credential_crypto, upserts into appliance_breakglass_
   passphrases. Authenticated via the per-appliance bearer (same key
   main daemon uses for /api/appliances/checkin).

2. GET /api/admin/appliance/{appliance_id}/break-glass
   Admin-only. Writes a privileged admin_audit_log row with the
   requesting actor_email + reason BEFORE decrypting. Decrypts and
   returns plaintext in the response body exactly once. Retrieval
   count + last_retrieved_at are bumped so the operator's usage
   pattern is visible to the customer via the Phase H6 feed.

Both surfaces fail closed — missing CREDENTIAL_ENCRYPTION_KEY on the
backend is a hard error, not a silent bypass.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .fleet import get_pool
from .tenant_middleware import admin_connection
from .shared import require_appliance_bearer_full
from .credential_crypto import encrypt_credential, decrypt_credential
from .auth import require_admin

logger = logging.getLogger("breakglass_api")

breakglass_provision_router = APIRouter(prefix="/api/provision", tags=["breakglass"])
breakglass_admin_router = APIRouter(prefix="/api/admin", tags=["breakglass-admin"])


class BreakglassSubmit(BaseModel):
    site_id: str = Field(..., min_length=1, max_length=64)
    appliance_id: str = Field(..., min_length=1, max_length=255)
    passphrase: str = Field(..., min_length=16, max_length=256)


class BreakglassRetrievalRequest(BaseModel):
    reason: str = Field(..., min_length=20, max_length=500,
                        description="Operator-provided reason ≥20 chars. "
                                    "Appears on the customer's privileged-"
                                    "action feed + admin_audit_log row.")


@breakglass_provision_router.post("/breakglass-submit")
async def submit_breakglass(
    req: BreakglassSubmit,
    request: Request,
    bearer: tuple = Depends(require_appliance_bearer_full),
) -> Dict[str, Any]:
    """Main-daemon posts the locally-generated passphrase after first
    boot. Bearer must be the main daemon's per-appliance key
    (bearer_aid == req.appliance_id, no -watchdog suffix).

    Upsert: re-provisioning an appliance (flash + reinstall) generates
    a NEW passphrase and replaces the old one. passphrase_version
    increments so the admin history shows rotations.
    """
    bearer_site, bearer_aid = bearer
    if bearer_site != req.site_id:
        raise HTTPException(status_code=403, detail="auth_site_id ≠ request site")
    if not bearer_aid:
        raise HTTPException(
            status_code=403,
            detail="break-glass submit requires per-appliance bearer (not site-level)",
        )
    if bearer_aid != req.appliance_id:
        raise HTTPException(
            status_code=403,
            detail=f"bearer_aid {bearer_aid!r} != request appliance_id {req.appliance_id!r}",
        )
    if bearer_aid.endswith("-watchdog"):
        raise HTTPException(
            status_code=400,
            detail="breakglass submit must come from main daemon, not watchdog",
        )

    enc = encrypt_credential(req.passphrase)

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        async with conn.transaction():
            existed = await conn.fetchrow(
                "SELECT passphrase_version FROM appliance_breakglass_passphrases "
                "WHERE appliance_id = $1 FOR UPDATE",
                req.appliance_id,
            )
            if existed:
                new_version = int(existed["passphrase_version"]) + 1
                await conn.execute(
                    """
                    UPDATE appliance_breakglass_passphrases
                       SET encrypted_passphrase = $1,
                           passphrase_version   = $2,
                           rotated_at           = NOW()
                     WHERE appliance_id = $3
                    """,
                    enc, new_version, req.appliance_id,
                )
                action = "BREAKGLASS_ROTATED"
            else:
                new_version = 1
                await conn.execute(
                    """
                    INSERT INTO appliance_breakglass_passphrases
                        (site_id, appliance_id, encrypted_passphrase, passphrase_version)
                    VALUES ($1, $2, $3, $4)
                    """,
                    req.site_id, req.appliance_id, enc, new_version,
                )
                action = "BREAKGLASS_SUBMITTED"

            await conn.execute(
                """
                INSERT INTO admin_audit_log
                    (username, action, target, details, created_at)
                VALUES ($1, $2, $3, $4::jsonb, NOW())
                """,
                f"appliance:{req.appliance_id}",
                action,
                f"breakglass:{req.appliance_id}",
                json.dumps({
                    "site_id": req.site_id,
                    "passphrase_version": new_version,
                    "source": "msp-first-boot",
                }),
            )

    logger.info(
        "breakglass %s site=%s aid=%s version=%d",
        action, req.site_id, req.appliance_id, new_version,
    )
    return {
        "ok": True,
        "appliance_id": req.appliance_id,
        "passphrase_version": new_version,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }


@breakglass_admin_router.get("/appliance/{appliance_id}/break-glass")
async def retrieve_breakglass(
    appliance_id: str,
    request: Request,
    reason: str = "",
    admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Admin retrieves the plaintext passphrase once per call. Every
    retrieval writes to admin_audit_log (visible on the customer's
    /api/client/privileged-actions feed via Phase H6) and bumps
    retrieval_count + last_retrieved_at for trend visibility.

    The retrieval is NOT rate-limited at the HTTP layer — spamming it
    is itself an audit-log-visible signal worth surfacing. If it
    becomes a problem, add a count-in-last-24h check here.

    Reason is REQUIRED (≥20 chars) per the chain-of-custody rule.
    Passed via query string so this endpoint remains safe under
    browser auto-retry semantics (no body → no replay).
    """
    actor = admin.get("email") or admin.get("username")
    if not actor:
        raise HTTPException(status_code=403, detail="admin identity missing")
    if not reason or len(reason.strip()) < 20:
        raise HTTPException(
            status_code=400,
            detail="reason query parameter required (≥20 chars) for chain-of-custody",
        )

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT site_id, encrypted_passphrase, passphrase_version,
                       generated_at, rotated_at, retrieval_count
                  FROM appliance_breakglass_passphrases
                 WHERE appliance_id = $1
                 FOR UPDATE
                """,
                appliance_id,
            )
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"no break-glass passphrase on file for {appliance_id}",
                )
            await conn.execute(
                """
                UPDATE appliance_breakglass_passphrases
                   SET last_retrieved_at = NOW(),
                       retrieval_count   = retrieval_count + 1
                 WHERE appliance_id = $1
                """,
                appliance_id,
            )
            await conn.execute(
                """
                INSERT INTO admin_audit_log
                    (username, action, target, details, created_at)
                VALUES ($1, 'BREAKGLASS_RETRIEVED', $2, $3::jsonb, NOW())
                """,
                actor,
                f"breakglass:{appliance_id}",
                json.dumps({
                    "site_id": row["site_id"],
                    "appliance_id": appliance_id,
                    "passphrase_version": int(row["passphrase_version"]),
                    "reason": reason.strip(),
                    "retrieval_count_after": int(row["retrieval_count"]) + 1,
                    "ip_address": request.client.host if request.client else None,
                }),
            )

    try:
        plaintext = decrypt_credential(bytes(row["encrypted_passphrase"]))
    except Exception as e:
        logger.error("breakglass decrypt failed aid=%s err=%s", appliance_id, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="decrypt failed — CREDENTIAL_ENCRYPTION_KEY rotated or missing",
        )

    logger.warning(
        "BREAKGLASS_RETRIEVED actor=%s aid=%s version=%d reason=%s",
        actor, appliance_id, int(row["passphrase_version"]), reason[:60],
    )
    return {
        "appliance_id": appliance_id,
        "site_id": row["site_id"],
        "passphrase": plaintext,
        "passphrase_version": int(row["passphrase_version"]),
        "generated_at": row["generated_at"].isoformat(),
        "rotated_at": row["rotated_at"].isoformat() if row["rotated_at"] else None,
        "actor_email": actor,
        "reason": reason.strip(),
    }
