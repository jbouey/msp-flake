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
import re
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .fleet import get_pool
from .tenant_middleware import admin_connection
from .shared import require_appliance_bearer_full, check_rate_limit
from .credential_crypto import encrypt_credential, decrypt_credential
from .auth import require_admin
from .privileged_access_attestation import (
    create_privileged_access_attestation,
    PrivilegedAccessAttestationError,
)

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
            # R+S follow-up (c): INSERT…ON CONFLICT DO UPDATE collapses
            # the prior SELECT-FOR-UPDATE + branch into one atomic write.
            # Two simultaneous submits can no longer race into 500s.
            # RETURNING exposes whether this was an insert or an update
            # (xmax = 0 means fresh insert; non-zero means update).
            row = await conn.fetchrow(
                """
                INSERT INTO appliance_breakglass_passphrases
                    (site_id, appliance_id, encrypted_passphrase, passphrase_version)
                VALUES ($1, $2, $3, 1)
                ON CONFLICT (appliance_id) DO UPDATE
                    SET encrypted_passphrase = EXCLUDED.encrypted_passphrase,
                        passphrase_version   = appliance_breakglass_passphrases.passphrase_version + 1,
                        rotated_at           = NOW()
                RETURNING passphrase_version, (xmax = 0) AS is_insert
                """,
                req.site_id, req.appliance_id, enc,
            )
            new_version = int(row["passphrase_version"])
            action = "BREAKGLASS_SUBMITTED" if row["is_insert"] else "BREAKGLASS_ROTATED"

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


# R+S follow-up (b): reject trivially low-entropy reasons. The length
# check alone (≥20) is gameable with "aaaaaaaaaaaaaaaaaaaa". We require
# at least one alphabetic run + ≥5 distinct characters so the operator
# has to type something meaningful. Not a defense against lies —
# defense against lazy copy-paste that erodes the audit trail's value.
_REASON_ALPHANUM_RE = re.compile(r"[A-Za-z]{3,}")


def _validate_reason(reason: str) -> str:
    """Raise HTTPException(400) if reason fails the chain-of-custody bar.
    Returns the stripped, validated reason on success."""
    if not reason:
        raise HTTPException(
            status_code=400,
            detail="reason query parameter required (≥20 chars) for chain-of-custody",
        )
    r = reason.strip()
    if len(r) < 20:
        raise HTTPException(
            status_code=400,
            detail="reason must be ≥20 chars",
        )
    if len(r) > 500:
        raise HTTPException(status_code=400, detail="reason must be ≤500 chars")
    if len(set(r)) < 5:
        raise HTTPException(
            status_code=400,
            detail="reason has <5 distinct characters — describe the incident",
        )
    if not _REASON_ALPHANUM_RE.search(r):
        raise HTTPException(
            status_code=400,
            detail="reason must contain an alphabetic word (≥3 letters)",
        )
    return r


@breakglass_admin_router.get("/appliance/{appliance_id}/break-glass")
async def retrieve_breakglass(
    appliance_id: str,
    request: Request,
    reason: str = "",
    admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Admin retrieves the plaintext passphrase once per call. Every
    retrieval writes:
      - admin_audit_log row (visible on the customer's /api/client/
        privileged-actions feed via Phase H6)
      - privileged_access_attestation bundle (Ed25519-signed, hash-
        chained, OTS-anchored; flows into the auditor kit)

    Rate limit: 5/hr per appliance_id (R+S follow-up a). Spam past that
    returns 429 with Retry-After. Passphrase is still logged as a
    RETRIEVAL_RATE_LIMITED admin_audit_log row so the pattern is visible
    to the customer even when blocked.

    Reason is REQUIRED (R+S follow-up b): ≥20 chars, ≥5 distinct chars,
    must contain an alphabetic word. Passed via query string so browser
    auto-retry semantics don't replay a body.
    """
    actor = admin.get("email") or admin.get("username")
    if not actor:
        raise HTTPException(status_code=403, detail="admin identity missing")
    reason_clean = _validate_reason(reason)

    allowed, retry_after = await check_rate_limit(
        site_id=f"breakglass:{appliance_id}",
        action="breakglass_retrieval",
        window_seconds=3600,
        max_requests=5,
    )
    if not allowed:
        logger.warning(
            "BREAKGLASS_RATE_LIMITED actor=%s aid=%s retry_after=%ds",
            actor, appliance_id, retry_after,
        )
        pool = await get_pool()
        async with admin_connection(pool) as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO admin_audit_log
                    (username, action, target, details, created_at)
                VALUES ($1, 'BREAKGLASS_RATE_LIMITED', $2, $3::jsonb, NOW())
                """,
                actor,
                f"breakglass:{appliance_id}",
                json.dumps({
                    "appliance_id": appliance_id,
                    "retry_after_seconds": int(retry_after),
                    "ip_address": request.client.host if request.client else None,
                }),
            )
        raise HTTPException(
            status_code=429,
            detail=f"break-glass retrieval rate-limited (5/hr); retry in {retry_after}s",
            headers={"Retry-After": str(retry_after)},
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
                    "reason": reason_clean,
                    "retrieval_count_after": int(row["retrieval_count"]) + 1,
                    "ip_address": request.client.host if request.client else None,
                }),
            )

            # R+S follow-up (d): write a privileged_access attestation
            # bundle so this retrieval flows into the auditor kit + is
            # cryptographically hash-chained to the site's prior
            # evidence. Do NOT raise the retrieval if attestation fails
            # — the admin_audit_log above is the immediate audit trail;
            # the attestation is the durable evidence. Log and continue.
            attestation_bundle_id = None
            try:
                att = await create_privileged_access_attestation(
                    conn,
                    site_id=row["site_id"],
                    event_type="break_glass_passphrase_retrieval",
                    actor_email=actor,
                    reason=reason_clean,
                    origin_ip=request.client.host if request.client else None,
                )
                attestation_bundle_id = att["bundle_id"]
            except PrivilegedAccessAttestationError as e:
                logger.error(
                    "breakglass attestation failed aid=%s actor=%s err=%s",
                    appliance_id, actor, e, exc_info=True,
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
        "BREAKGLASS_RETRIEVED actor=%s aid=%s version=%d bundle=%s reason=%s",
        actor, appliance_id, int(row["passphrase_version"]),
        attestation_bundle_id or "none", reason_clean[:60],
    )
    try:
        from .email_alerts import send_operator_alert
        send_operator_alert(
            event_type="break_glass_passphrase_retrieval",
            severity="P0",
            summary=f"Break-glass passphrase retrieved for appliance {appliance_id} by {actor}",
            details={
                "appliance_id": appliance_id,
                "passphrase_version": int(row["passphrase_version"]),
                "reason": reason_clean,
                "attestation_bundle_id": attestation_bundle_id,
                "retrieval_count_after": int(row["retrieval_count"]) + 1,
            },
            site_id=row["site_id"],
            actor_email=actor,
        )
    except Exception:
        logger.error("operator_alert_dispatch_failed_breakglass", exc_info=True)
    return {
        "appliance_id": appliance_id,
        "site_id": row["site_id"],
        "passphrase": plaintext,
        "passphrase_version": int(row["passphrase_version"]),
        "generated_at": row["generated_at"].isoformat(),
        "rotated_at": row["rotated_at"].isoformat() if row["rotated_at"] else None,
        "actor_email": actor,
        "reason": reason_clean,
        "attestation_bundle_id": attestation_bundle_id,
    }
