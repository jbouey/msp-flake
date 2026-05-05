"""Partner-admin transfer state machine.

Round-table 2026-05-04 (Camila/Brian/Linda/Steve/Adam + Maya 2nd-eye).
Closes the partner-side analog of mig 273's owner-transfer gap. Pre-fix:
if a partner_org's principal admin was compromised or departed, the
recovery path was DB surgery.

Per Maya: the SHAPE differs from client_org_owner_transfer because
partners are operators (per feedback_non_operator_partner_posture.md).
Operator-class flows tolerate less friction:
  - 2-step (initiate → target accepts = complete) instead of 3-step
  - NO cooling-off (operators need fast incident response)
  - NO magic-link (partners use OAuth/SSO; target re-authenticates in
    their own existing session)
  - NO target-creation (target must already be a partner_user with
    role!=admin in the same partner_org)

What stays the same (per CLAUDE.md privileged-access chain rule):
  - reason ≥20ch
  - Ed25519 attestation per state transition (4 event_types in
    ALLOWED_EVENTS)
  - operator-visibility email on every transition
  - 1-admin-min DB trigger (Brian's non-negotiable, mig 274)
  - any-current-admin-can-cancel (Steve P3 lateral defense)
  - confirm_phrase on initiate (anti-misclick)

Endpoints (mounted at /api/partners/me/admin-transfer/):
  POST   /initiate              — current admin kicks off
  POST   /{id}/accept           — target re-auths + accepts; role swap
                                   is immediate (no cooling-off)
  POST   /{id}/cancel           — any current admin cancels
  GET    /{id}                  — read state (any partner_user in-org)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from .fleet import get_pool
from .partner_activity_logger import (
    PartnerEventType,
    log_partner_activity,
)
from .privileged_access_attestation import (
    PrivilegedAccessAttestationError,
    create_privileged_access_attestation,
)
from .tenant_middleware import admin_connection

# Re-import the partner-auth dependency from partners.py to keep auth
# cohesion. Not great that partners.py owns this — future cleanup
# might move require_partner_role into a shared partner_auth module —
# but for now we delegate.
from .partners import require_partner_role

logger = logging.getLogger(__name__)

partner_admin_transfer_router = APIRouter(
    prefix="/api/partners/me/admin-transfer",
    tags=["partner-portal", "admin-transfer"],
)


DEFAULT_EXPIRY_DAYS = 7
MIN_REASON_CHARS = 20


class InitiatePartnerAdminTransferRequest(BaseModel):
    target_email: EmailStr
    reason: str = Field(..., min_length=MIN_REASON_CHARS)
    confirm_phrase: str = Field(...,
        description="Type the literal string CONFIRM-PARTNER-ADMIN-TRANSFER")


class AcceptPartnerAdminTransferRequest(BaseModel):
    confirm_phrase: str = Field(...,
        description="Type the literal string ACCEPT-PARTNER-ADMIN")


class CancelPartnerAdminTransferRequest(BaseModel):
    cancel_reason: str = Field(..., min_length=MIN_REASON_CHARS)


# ─── Helpers ──────────────────────────────────────────────────────


async def _expire_stale_transfers(conn, partner_id: str) -> int:
    rows = await conn.fetch(
        """
        UPDATE partner_admin_transfer_requests
           SET status = 'expired'
         WHERE partner_id = $1::uuid
           AND status = 'pending_target_accept'
           AND expires_at < NOW()
        RETURNING id
        """,
        partner_id,
    )
    return len(rows)


async def _emit_attestation(
    conn,
    partner_id: str,
    event_type: str,
    actor_email: str,
    reason: str,
    transfer_id: str,
    origin_ip: Optional[str] = None,
) -> Optional[str]:
    """Write a privileged_access attestation bundle. Returns bundle_id
    on success, None on failure. Anchor namespace: partner_org:<id>."""
    try:
        anchor_site_id = f"partner_org:{partner_id}"
        att = await create_privileged_access_attestation(
            conn,
            site_id=anchor_site_id,
            event_type=event_type,
            actor_email=actor_email,
            reason=reason,
            origin_ip=origin_ip,
            duration_minutes=None,
            approvals=[{
                "stage": event_type.split("_")[-1],
                "actor": actor_email,
                "transfer_id": transfer_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }],
        )
        bundle_id = att.get("bundle_id")
        if bundle_id:
            await conn.execute(
                """
                UPDATE partner_admin_transfer_requests
                   SET attestation_bundle_ids =
                       attestation_bundle_ids || to_jsonb($2::text)
                 WHERE id = $1::uuid
                """,
                transfer_id, bundle_id,
            )
        return bundle_id
    except PrivilegedAccessAttestationError:
        logger.error(
            "partner_admin_transfer_attestation_failed",
            exc_info=True,
            extra={
                "transfer_id": transfer_id,
                "event_type": event_type,
                "partner_id": partner_id,
            },
        )
        return None


def _send_operator_visibility(
    event_type: str,
    severity: str,
    summary: str,
    details: dict,
    actor_email: Optional[str],
    partner_id: str,
    attestation_failed: bool,
) -> None:
    """Same chain-gap escalation pattern as client_owner_transfer."""
    try:
        from .email_alerts import send_operator_alert
        if attestation_failed:
            severity = "P0-CHAIN-GAP"
            summary = f"{summary} [ATTESTATION-MISSING]"
        details = {**details, "attestation_failed": attestation_failed}
        send_operator_alert(
            event_type=event_type,
            severity=severity,
            summary=summary,
            details=details,
            site_id=f"partner_org:{partner_id}",
            actor_email=actor_email,
        )
    except Exception:
        logger.error(
            "operator_alert_dispatch_failed_partner_admin_transfer",
            exc_info=True,
        )


def _audit_request_metadata(request: Request) -> dict:
    return {
        "ip_address": (request.client.host if request.client else None),
        "user_agent": request.headers.get("user-agent"),
        "request_path": str(request.url.path),
        "request_method": request.method,
    }


# ─── Endpoints ────────────────────────────────────────────────────


@partner_admin_transfer_router.post("/initiate")
async def initiate_partner_admin_transfer(
    body: InitiatePartnerAdminTransferRequest,
    request: Request,
    partner: dict = require_partner_role("admin"),
) -> Dict[str, Any]:
    """Current admin proposes target_email as new admin.

    Validation:
      - confirm_phrase exact-match (anti-misclick)
      - target_email != initiator email (no self-transfer)
      - target must be an EXISTING partner_user in same partner_org
      - target's current role MUST NOT be 'admin'
      - reason ≥20 chars
      - no other active transfer for this partner_org
    """
    if body.confirm_phrase != "CONFIRM-PARTNER-ADMIN-TRANSFER":
        raise HTTPException(
            status_code=400,
            detail=(
                "confirm_phrase must be exactly "
                "'CONFIRM-PARTNER-ADMIN-TRANSFER'"
            ),
        )
    target_email = body.target_email.lower().strip()
    initiator_email = partner.get("email", "").lower()
    if target_email == initiator_email:
        raise HTTPException(
            status_code=400,
            detail="Cannot transfer admin role to yourself",
        )

    pool = await get_pool()
    partner_id = str(partner["id"])
    initiator_user_id = str(partner["user_id"])
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=DEFAULT_EXPIRY_DAYS)

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            await _expire_stale_transfers(conn, partner_id)

            existing = await conn.fetchrow(
                """
                SELECT id FROM partner_admin_transfer_requests
                 WHERE partner_id = $1::uuid
                   AND status = 'pending_target_accept'
                """,
                partner_id,
            )
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"An admin-transfer is already in flight for this "
                        f"partner (id={existing['id']}). Cancel it first."
                    ),
                )

            target_row = await conn.fetchrow(
                """
                SELECT id, role, status FROM partner_users
                 WHERE LOWER(email) = $1
                   AND partner_id = $2::uuid
                """,
                target_email, partner_id,
            )
            if not target_row:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "Target email is not a partner_user in this "
                        "partner_org. Add them via "
                        "POST /api/partners/{id}/users first."
                    ),
                )
            if target_row["role"] == "admin":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Target is already an admin. Use a regular "
                        "role-management flow instead."
                    ),
                )
            if target_row["status"] != "active":
                raise HTTPException(
                    status_code=409,
                    detail="Target partner_user is not active.",
                )
            target_user_id = str(target_row["id"])

            row = await conn.fetchrow(
                """
                INSERT INTO partner_admin_transfer_requests (
                    partner_id, initiated_by_user_id, target_email,
                    target_user_id, reason, expires_at
                ) VALUES (
                    $1::uuid, $2::uuid, $3, $4::uuid, $5, $6
                )
                RETURNING id::text
                """,
                partner_id, initiator_user_id, target_email,
                target_user_id, body.reason, expires_at,
            )
            transfer_id = row["id"]

            bundle_id = await _emit_attestation(
                conn, partner_id,
                event_type="partner_admin_transfer_initiated",
                actor_email=initiator_email,
                reason=body.reason,
                transfer_id=transfer_id,
                origin_ip=(request.client.host if request.client else None),
            )

    # Audit (outside txn — log_partner_activity uses its own conn)
    try:
        await log_partner_activity(
            partner_id=partner_id,
            event_type=PartnerEventType.PARTNER_UPDATED,
            target_type="partner_admin_transfer",
            target_id=transfer_id,
            event_data={
                "action": "transfer_initiated",
                "target_email": target_email,
                "target_user_id": target_user_id,
                "actor_email": initiator_email,
                "reason": body.reason,
            },
            **_audit_request_metadata(request),
        )
    except Exception:
        logger.error("partner_admin_transfer_audit_failed", exc_info=True)

    _send_operator_visibility(
        event_type="partner_admin_transfer_initiated",
        severity="P1",
        summary=(
            f"Partner-admin transfer initiated: "
            f"{initiator_email} → {target_email} (partner_id={partner_id})"
        ),
        details={
            "transfer_id": transfer_id,
            "target_email": target_email,
            "reason": body.reason,
            "attestation_bundle_id": bundle_id,
        },
        actor_email=initiator_email,
        partner_id=partner_id,
        attestation_failed=(bundle_id is None),
    )

    return {
        "transfer_id": transfer_id,
        "status": "pending_target_accept",
        "expires_at": expires_at.isoformat(),
        "next_step": (
            f"Target {target_email} must POST /accept with "
            f"confirm_phrase='ACCEPT-PARTNER-ADMIN' from their own "
            f"authenticated session. Role swap is immediate on accept "
            f"(no cooling-off — operator class). Cancel anytime via "
            f"POST /{transfer_id}/cancel."
        ),
        "attestation_bundle_id": bundle_id,
    }


@partner_admin_transfer_router.post("/{transfer_id}/accept")
async def accept_partner_admin_transfer(
    transfer_id: str,
    body: AcceptPartnerAdminTransferRequest,
    request: Request,
    partner: dict = require_partner_role("admin", "tech", "billing"),
) -> Dict[str, Any]:
    """Target accepts. Role swap is IMMEDIATE — no cooling-off.

    Auth: any authenticated partner_user; the endpoint validates
    actor matches target_email + same partner_org.
    """
    if body.confirm_phrase != "ACCEPT-PARTNER-ADMIN":
        raise HTTPException(
            status_code=400,
            detail="confirm_phrase must be exactly 'ACCEPT-PARTNER-ADMIN'",
        )

    pool = await get_pool()
    actor_email = (partner.get("email") or "").lower()
    actor_user_id = str(partner["user_id"])
    partner_id = str(partner["id"])

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id::text, partner_id::text AS partner_id,
                       initiated_by_user_id::text AS initiator_id,
                       target_email, target_user_id::text AS target_id,
                       reason, status, expires_at
                  FROM partner_admin_transfer_requests
                 WHERE id = $1::uuid
                """,
                transfer_id,
            )
            if not row:
                raise HTTPException(status_code=404,
                    detail="Transfer not found")
            if row["partner_id"] != partner_id:
                raise HTTPException(status_code=403,
                    detail="Transfer is for a different partner_org")
            if row["status"] != "pending_target_accept":
                raise HTTPException(status_code=409,
                    detail=f"Cannot accept from status {row['status']}")
            if row["expires_at"] < datetime.now(timezone.utc):
                raise HTTPException(status_code=410,
                    detail="Transfer has expired")
            if actor_email != (row["target_email"] or "").lower():
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "This transfer is bound to a specific email; "
                        "you are signed in as a different user."
                    ),
                )

            # Promote target FIRST, then demote initiator. Order
            # matters per the 1-admin-min trigger (mig 274) — going
            # through a zero-admin intermediate state would fire it.
            await conn.execute(
                """
                UPDATE partner_users
                   SET role = 'admin', updated_at = NOW()
                 WHERE id = $1::uuid
                """,
                actor_user_id,
            )
            # Demote initiator. Find a sensible default for the
            # ex-admin's new role: 'tech' (mid-privilege) is the
            # least-surprise choice — partners.py has no explicit
            # "ex-admin" role.
            await conn.execute(
                """
                UPDATE partner_users
                   SET role = 'tech', updated_at = NOW()
                 WHERE id = $1::uuid
                """,
                row["initiator_id"],
            )
            # Mark transfer completed (immediate — no cooling-off)
            await conn.execute(
                """
                UPDATE partner_admin_transfer_requests
                   SET status = 'completed',
                       completed_at = NOW()
                 WHERE id = $1::uuid
                """,
                row["id"],
            )

            bundle_id = await _emit_attestation(
                conn, partner_id,
                event_type="partner_admin_transfer_completed",
                actor_email=actor_email,
                reason=row["reason"],
                transfer_id=row["id"],
                origin_ip=(request.client.host if request.client else None),
            )

    try:
        await log_partner_activity(
            partner_id=partner_id,
            event_type=PartnerEventType.PARTNER_UPDATED,
            target_type="partner_admin_transfer",
            target_id=row["id"],
            event_data={
                "action": "transfer_completed",
                "new_admin_email": actor_email,
                "demoted_initiator_id": row["initiator_id"],
            },
            **_audit_request_metadata(request),
        )
    except Exception:
        logger.error("partner_admin_transfer_audit_failed", exc_info=True)

    _send_operator_visibility(
        event_type="partner_admin_transfer_completed",
        severity="P1",
        summary=(
            f"Partner-admin transfer COMPLETED: {actor_email} is now "
            f"admin of partner_org {partner_id} "
            f"(initiator demoted to tech)"
        ),
        details={
            "transfer_id": row["id"],
            "new_admin_email": actor_email,
            "attestation_bundle_id": bundle_id,
        },
        actor_email=actor_email,
        partner_id=partner_id,
        attestation_failed=(bundle_id is None),
    )

    return {
        "transfer_id": row["id"],
        "status": "completed",
        "new_admin_email": actor_email,
        "attestation_bundle_id": bundle_id,
    }


@partner_admin_transfer_router.post("/{transfer_id}/cancel")
async def cancel_partner_admin_transfer(
    transfer_id: str,
    body: CancelPartnerAdminTransferRequest,
    request: Request,
    partner: dict = require_partner_role("admin"),
) -> Dict[str, Any]:
    """Cancel a pending transfer. ANY current admin in-org can cancel
    (Steve P3 lateral defense — same logic as client side)."""
    pool = await get_pool()
    actor_email = (partner.get("email") or "").lower()
    partner_id = str(partner["id"])

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id::text, status, target_email, reason
                  FROM partner_admin_transfer_requests
                 WHERE id = $1::uuid AND partner_id = $2::uuid
                """,
                transfer_id, partner_id,
            )
            if not row:
                raise HTTPException(status_code=404,
                    detail="Transfer not found in your partner_org")
            if row["status"] != "pending_target_accept":
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot cancel from terminal status {row['status']}",
                )

            await conn.execute(
                """
                UPDATE partner_admin_transfer_requests
                   SET status = 'canceled',
                       canceled_at = NOW(),
                       canceled_by = $2,
                       cancel_reason = $3
                 WHERE id = $1::uuid
                """,
                transfer_id, actor_email, body.cancel_reason,
            )

            bundle_id = await _emit_attestation(
                conn, partner_id,
                event_type="partner_admin_transfer_canceled",
                actor_email=actor_email,
                reason=body.cancel_reason,
                transfer_id=transfer_id,
                origin_ip=(request.client.host if request.client else None),
            )

    try:
        await log_partner_activity(
            partner_id=partner_id,
            event_type=PartnerEventType.PARTNER_UPDATED,
            target_type="partner_admin_transfer",
            target_id=transfer_id,
            event_data={
                "action": "transfer_canceled",
                "canceled_by": actor_email,
                "cancel_reason": body.cancel_reason,
                "target_email": row["target_email"],
            },
            **_audit_request_metadata(request),
        )
    except Exception:
        logger.error("partner_admin_transfer_audit_failed", exc_info=True)

    _send_operator_visibility(
        event_type="partner_admin_transfer_canceled",
        severity="P1",
        summary=(
            f"Partner-admin transfer canceled by {actor_email}; "
            f"target was {row['target_email']}"
        ),
        details={
            "transfer_id": transfer_id,
            "cancel_reason": body.cancel_reason,
            "attestation_bundle_id": bundle_id,
        },
        actor_email=actor_email,
        partner_id=partner_id,
        attestation_failed=(bundle_id is None),
    )

    return {
        "transfer_id": transfer_id,
        "status": "canceled",
        "canceled_by": actor_email,
        "attestation_bundle_id": bundle_id,
    }


@partner_admin_transfer_router.get("/{transfer_id}")
async def get_partner_admin_transfer(
    transfer_id: str,
    partner: dict = require_partner_role("admin", "tech", "billing"),
) -> Dict[str, Any]:
    pool = await get_pool()
    partner_id = str(partner["id"])

    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            """
            SELECT id::text, status, target_email, reason,
                   initiated_by_user_id::text AS initiator_id,
                   target_user_id::text AS target_id,
                   completed_at, canceled_at, canceled_by,
                   cancel_reason, expires_at, created_at,
                   jsonb_array_length(attestation_bundle_ids)
                       AS attestation_count
              FROM partner_admin_transfer_requests
             WHERE id = $1::uuid AND partner_id = $2::uuid
            """,
            transfer_id, partner_id,
        )
    if not row:
        raise HTTPException(status_code=404,
            detail="Transfer not found in your partner_org")
    out = dict(row)
    for k in ("completed_at", "canceled_at", "expires_at", "created_at"):
        if out.get(k) is not None:
            out[k] = out[k].isoformat()
    return out


# ─── Sweep loop (expired-transition) ─────────────────────────────


async def partner_admin_transfer_sweep_loop():
    """Background loop: mark stale pending transfers expired.

    Cadence: every 60s. Idempotent.

    Records a heartbeat each iteration so the substrate
    `bg_loop_silent` invariant catches stuck-await states.
    EXPECTED_INTERVAL_S=60 in lockstep with the sleep cadence.
    """
    import asyncio
    from .bg_heartbeat import record_heartbeat
    while True:
        try:
            record_heartbeat("partner_admin_transfer_sweep")
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                expired = await conn.fetch(
                    """
                    UPDATE partner_admin_transfer_requests
                       SET status = 'expired'
                     WHERE status = 'pending_target_accept'
                       AND expires_at <= NOW()
                    RETURNING id::text, partner_id::text,
                              target_email, reason
                    """,
                )
                for row in expired:
                    try:
                        bundle_id = await _emit_attestation(
                            conn, row["partner_id"],
                            event_type="partner_admin_transfer_expired",
                            actor_email="system:partner_admin_sweep",
                            reason=(
                                f"Transfer expired without acceptance; "
                                f"original reason: {row['reason'][:160]}"
                            ),
                            transfer_id=row["id"],
                        )
                        _send_operator_visibility(
                            event_type="partner_admin_transfer_expired",
                            severity="P2",
                            summary=(
                                f"Partner-admin transfer EXPIRED: target "
                                f"was {row['target_email']}"
                            ),
                            details={
                                "transfer_id": row["id"],
                                "target_email": row["target_email"],
                                "attestation_bundle_id": bundle_id,
                            },
                            actor_email="system:partner_admin_sweep",
                            partner_id=row["partner_id"],
                            attestation_failed=(bundle_id is None),
                        )
                    except Exception:
                        logger.error(
                            "partner_admin_transfer_expire_failed",
                            exc_info=True,
                            extra={"transfer_id": row["id"]},
                        )
        except Exception:
            logger.error(
                "partner_admin_transfer_sweep_iteration_failed",
                exc_info=True,
            )
        await asyncio.sleep(60)
