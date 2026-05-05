"""MFA admin overrides for client + partner portals.

Round-table 2026-05-05 task #19 closure (Camila + Brian + Linda +
Steve + Adam + Maya 2nd-eye). Provides 6 endpoints + 2 reversal
endpoints across both portals:

  Client side (auth: require_client_owner / require_client_admin):
    PUT  /api/client/org/mfa-policy            — toggle org mfa_required
    POST /api/client/users/{uid}/mfa-reset     — clear secret + enabled
    POST /api/client/users/{uid}/mfa-revoke    — clear + 24h reversible
    POST /api/client/mfa/restore?token=...     — target self-restores

  Partner side (auth: require_partner_role admin):
    PUT  /api/partners/me/mfa-policy
    POST /api/partners/{pid}/users/{uid}/mfa-reset
    POST /api/partners/{pid}/users/{uid}/mfa-revoke
    POST /api/partners/me/mfa/restore?token=...

Plus a sweep loop `mfa_revocation_expiry_sweep` (60s cadence) that
marks expired pending revocations.

Privileged-action chain elements (per CLAUDE.md INVIOLABLE rule):
  - reason ≥20ch on policy + reset
  - reason ≥40ch on revoke (Steve P3 — higher friction; revoke can be
    the attack vector itself)
  - typed `confirm_phrase` on revoke (anti-misclick)
  - Ed25519 attestation per state transition (8 events)
  - Operator-visibility email on every transition with chain-gap
    escalation (P0-CHAIN-GAP if attestation broke)
  - Audit row via _audit_client_action / log_partner_activity

Steve P3 mitigation D: owner-transfer state machines (mig 273 + 274)
refuse `initiate` while ANY pending revocation exists for ANY in-org
user. Implemented at the call sites as a precondition check.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .client_portal import (
    _audit_client_action,
    require_client_admin,
    require_client_owner,
    require_client_user,
)
from .fleet import get_pool
from .partner_activity_logger import (
    PartnerEventType,
    log_partner_activity,
)
from .partners import require_partner, require_partner_role
from .privileged_access_attestation import (
    PrivilegedAccessAttestationError,
    create_privileged_access_attestation,
)
from .tenant_middleware import admin_connection

logger = logging.getLogger(__name__)

# Two routers — one per portal. Mounted separately in main.py.
mfa_admin_client_router = APIRouter(
    prefix="/api/client", tags=["client-portal", "mfa-admin"],
)
mfa_admin_partner_router = APIRouter(
    prefix="/api/partners", tags=["partner-portal", "mfa-admin"],
)

# Friction defaults
MIN_REASON_CHARS = 20
MIN_REVOKE_REASON_CHARS = 40  # Steve mit B
REVOKE_RESTORE_WINDOW_HOURS = 24

BASE_URL = os.getenv("FRONTEND_URL", "https://www.osiriscare.net")


# ─── Request models ───────────────────────────────────────────────


class MfaPolicyUpdate(BaseModel):
    required: bool
    reason: str = Field(..., min_length=MIN_REASON_CHARS)


class MfaResetRequest(BaseModel):
    reason: str = Field(..., min_length=MIN_REASON_CHARS)


class MfaRevokeRequest(BaseModel):
    confirm_phrase: str = Field(...,
        description="Type the literal string CONFIRM-MFA-REVOKE")
    reason: str = Field(..., min_length=MIN_REVOKE_REASON_CHARS)


# ─── Helpers ──────────────────────────────────────────────────────


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _emit_attestation(
    conn,
    anchor_site_id: str,
    event_type: str,
    actor_email: str,
    reason: str,
    target_user_id: str,
    origin_ip: Optional[str] = None,
) -> Optional[str]:
    """Best-effort Ed25519 attestation. Returns bundle_id or None."""
    try:
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
                "target_user_id": target_user_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }],
        )
        return att.get("bundle_id")
    except PrivilegedAccessAttestationError:
        logger.error(
            "mfa_admin_attestation_failed",
            exc_info=True,
            extra={
                "event_type": event_type,
                "target_user_id": target_user_id,
            },
        )
        return None


def _send_operator_visibility(
    event_type: str,
    severity: str,
    summary: str,
    details: dict,
    actor_email: Optional[str],
    site_id: str,
    attestation_failed: bool,
) -> None:
    """Chain-gap escalation pattern (uniform with the rest of session 216
    operator alerts). On attestation failure: severity → P0-CHAIN-GAP +
    [ATTESTATION-MISSING] subject suffix."""
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
            site_id=site_id,
            actor_email=actor_email,
        )
    except Exception:
        logger.error(
            "operator_alert_dispatch_failed_mfa_admin",
            exc_info=True,
        )


async def _resolve_client_anchor_site_id(conn, org_id: str) -> str:
    """Same anchor pattern as client_user_role_changed."""
    row = await conn.fetchrow(
        """
        SELECT site_id FROM sites
         WHERE client_org_id = $1::uuid
         ORDER BY created_at ASC LIMIT 1
        """,
        org_id,
    )
    return row["site_id"] if row else f"client_org:{org_id}"


async def _send_revoke_email_to_target(
    target_email: str,
    org_or_partner_name: str,
    revoking_admin_email: str,
    restore_token: str,
    expires_at: datetime,
    user_kind: str,
) -> None:
    """Send the 24h-reversible-link email to the user whose MFA was
    revoked. Best-effort. Per Steve P3 mit B: if MFA revocation was
    the attack vector itself (compromised admin), the target needs
    a path back."""
    try:
        from .email_service import send_email
        kind_label = ("OsirisCare" if user_kind == "client_user"
                      else f"the {org_or_partner_name} partner portal")
        restore_path = ("client" if user_kind == "client_user"
                        else "partners/me")
        restore_url = (
            f"{BASE_URL}/{restore_path}/mfa/restore"
            f"?token={restore_token}"
        )
        body = (
            f"Your multi-factor authentication for {kind_label} has "
            f"been revoked by {revoking_admin_email}.\n"
            f"\n"
            f"If this was NOT expected (e.g. your admin's account "
            f"may be compromised), you can restore your MFA within "
            f"24 hours by clicking:\n"
            f"\n"
            f"  {restore_url}\n"
            f"\n"
            f"Link expires: {expires_at.isoformat()}\n"
            f"\n"
            f"After restoration, the substrate records a "
            f"`mfa_revocation_reversed` attestation visible in your "
            f"auditor kit. The revoking actor's identity is also "
            f"recorded in admin_audit_log.\n"
            f"\n"
            f"If your MFA was revoked at your own request (lost "
            f"device, etc.), ignore this email. After 24 hours the "
            f"link will expire and you can re-enroll on your next "
            f"login.\n"
            f"\n"
            f"---\n"
            f"OsirisCare — substrate-level account access notice"
        )
        await send_email(
            target_email,
            f"MFA revoked: 24h restore link",
            body,
        )
    except Exception:
        logger.error("mfa_revoke_email_failed", exc_info=True)


# ─── Owner-transfer interlock predicate ───────────────────────────


async def has_active_mfa_revocation(
    conn,
    user_kind: str,
    scope_id: str,
) -> bool:
    """Steve P3 mit D: owner-transfer state machines call this at
    initiate-time. If True, the transfer is refused with a 409 +
    operator-visible reason. user_kind = 'client_user' OR
    'partner_user'; scope_id = client_org_id OR partner_id.
    """
    row = await conn.fetchrow(
        """
        SELECT 1 FROM mfa_revocation_pending
         WHERE user_kind = $1
           AND scope_id = $2::uuid
           AND restored_at IS NULL
           AND expires_at > NOW()
         LIMIT 1
        """,
        user_kind, scope_id,
    )
    return row is not None


# ─── Client-side endpoints ───────────────────────────────────────


@mfa_admin_client_router.put("/org/mfa-policy")
async def client_update_mfa_policy(
    body: MfaPolicyUpdate,
    request: Request,
    user: dict = Depends(require_client_owner),
) -> Dict[str, Any]:
    """Toggle org-level mfa_required (owner only)."""
    pool = await get_pool()
    org_id = str(user["org_id"])
    actor_email = user.get("email") or "unknown"

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            prior = await conn.fetchval(
                "SELECT mfa_required FROM client_orgs WHERE id = $1::uuid",
                org_id,
            )
            if prior is None:
                raise HTTPException(status_code=404, detail="Org not found")
            await conn.execute(
                "UPDATE client_orgs SET mfa_required = $2 WHERE id = $1::uuid",
                org_id, body.required,
            )
            anchor_site_id = await _resolve_client_anchor_site_id(conn, org_id)
            bundle_id = await _emit_attestation(
                conn, anchor_site_id,
                event_type="client_org_mfa_policy_changed",
                actor_email=actor_email,
                reason=body.reason,
                target_user_id=org_id,  # org-scoped, not user-scoped
                origin_ip=(request.client.host if request.client else None),
            )
            await _audit_client_action(
                conn, user,
                action="MFA_POLICY_CHANGED",
                target=org_id,
                details={
                    "prior_required": bool(prior),
                    "new_required": body.required,
                    "reason": body.reason,
                },
                request=request,
            )

    weakening = bool(prior) and not body.required
    _send_operator_visibility(
        event_type="client_org_mfa_policy_changed",
        severity=("P1" if weakening else "P2"),
        summary=(
            f"Client-org MFA policy changed by {actor_email}: "
            f"required={prior}→{body.required}"
            + (" [POLICY-WEAKENED]" if weakening else "")
        ),
        details={
            "org_id": org_id,
            "prior_required": bool(prior),
            "new_required": body.required,
            "weakening": weakening,
            "attestation_bundle_id": bundle_id,
        },
        actor_email=actor_email,
        site_id=f"client_org:{org_id}",
        attestation_failed=(bundle_id is None),
    )
    return {
        "status": "updated",
        "mfa_required": body.required,
        "attestation_bundle_id": bundle_id,
    }


@mfa_admin_client_router.post("/users/{target_user_id}/mfa-reset")
async def client_user_mfa_reset(
    target_user_id: str,
    body: MfaResetRequest,
    request: Request,
    user: dict = Depends(require_client_admin),  # owner OR admin
) -> Dict[str, Any]:
    """Clear target user's MFA. They re-enroll on next login. Owner+admin can do this."""
    pool = await get_pool()
    org_id = str(user["org_id"])
    actor_email = user.get("email") or "unknown"

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                """
                SELECT id, email, mfa_enabled FROM client_users
                 WHERE id = $1::uuid AND client_org_id = $2::uuid
                """,
                target_user_id, org_id,
            )
            if not target:
                raise HTTPException(status_code=404,
                    detail="Target user not found in your org")

            await conn.execute(
                """
                UPDATE client_users
                   SET mfa_secret = NULL, mfa_enabled = false,
                       updated_at = NOW()
                 WHERE id = $1::uuid
                """,
                target_user_id,
            )

            anchor_site_id = await _resolve_client_anchor_site_id(conn, org_id)
            bundle_id = await _emit_attestation(
                conn, anchor_site_id,
                event_type="client_user_mfa_reset",
                actor_email=actor_email,
                reason=body.reason,
                target_user_id=target_user_id,
                origin_ip=(request.client.host if request.client else None),
            )
            await _audit_client_action(
                conn, user,
                action="MFA_RESET",
                target=target_user_id,
                details={
                    "target_email": target["email"],
                    "had_mfa_enabled": bool(target["mfa_enabled"]),
                    "reason": body.reason,
                },
                request=request,
            )

    _send_operator_visibility(
        event_type="client_user_mfa_reset",
        severity="P1",
        summary=(
            f"Client user MFA reset: {target['email']} by {actor_email} "
            f"(had_mfa={bool(target['mfa_enabled'])})"
        ),
        details={
            "target_user_id": target_user_id,
            "target_email": target["email"],
            "had_mfa_enabled": bool(target["mfa_enabled"]),
            "reason": body.reason,
            "attestation_bundle_id": bundle_id,
        },
        actor_email=actor_email,
        site_id=f"client_org:{org_id}",
        attestation_failed=(bundle_id is None),
    )
    return {
        "status": "reset",
        "target_email": target["email"],
        "attestation_bundle_id": bundle_id,
    }


@mfa_admin_client_router.post("/users/{target_user_id}/mfa-revoke")
async def client_user_mfa_revoke(
    target_user_id: str,
    body: MfaRevokeRequest,
    request: Request,
    user: dict = Depends(require_client_owner),  # owner ONLY (Steve mit B)
) -> Dict[str, Any]:
    """Revoke target user's MFA + send them a 24h reversible-link email.

    Owner-only. Higher friction than reset (≥40ch reason + confirm_phrase)
    because revoke can be the attack vector itself.
    """
    if body.confirm_phrase != "CONFIRM-MFA-REVOKE":
        raise HTTPException(status_code=400,
            detail="confirm_phrase must be exactly 'CONFIRM-MFA-REVOKE'")

    pool = await get_pool()
    org_id = str(user["org_id"])
    actor_email = user.get("email") or "unknown"

    if target_user_id == str(user["user_id"]):
        raise HTTPException(status_code=400,
            detail="Cannot revoke your own MFA")

    restore_token = secrets.token_urlsafe(32)
    restore_token_hash = _hash_token(restore_token)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=REVOKE_RESTORE_WINDOW_HOURS)

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                """
                SELECT id, email, mfa_enabled FROM client_users
                 WHERE id = $1::uuid AND client_org_id = $2::uuid
                """,
                target_user_id, org_id,
            )
            if not target:
                raise HTTPException(status_code=404,
                    detail="Target user not found in your org")

            # Refuse if a pending revocation already exists for this
            # user (the partial unique index would also catch this,
            # but explicit error is clearer).
            existing = await conn.fetchrow(
                """
                SELECT id FROM mfa_revocation_pending
                 WHERE target_user_id = $1::uuid
                   AND restored_at IS NULL
                   AND expires_at > NOW()
                """,
                target_user_id,
            )
            if existing:
                raise HTTPException(status_code=409,
                    detail="A pending revocation already exists for this user")

            await conn.execute(
                """
                UPDATE client_users
                   SET mfa_secret = NULL, mfa_enabled = false,
                       updated_at = NOW()
                 WHERE id = $1::uuid
                """,
                target_user_id,
            )

            row = await conn.fetchrow(
                """
                INSERT INTO mfa_revocation_pending (
                    target_user_id, user_kind, scope_id, target_email,
                    revoked_by_email, expires_at, reversal_token_hash,
                    reason
                ) VALUES (
                    $1::uuid, 'client_user', $2::uuid, $3, $4, $5, $6, $7
                ) RETURNING id::text
                """,
                target_user_id, org_id, target["email"], actor_email,
                expires_at, restore_token_hash, body.reason,
            )
            revocation_id = row["id"]

            anchor_site_id = await _resolve_client_anchor_site_id(conn, org_id)
            bundle_id = await _emit_attestation(
                conn, anchor_site_id,
                event_type="client_user_mfa_revoked",
                actor_email=actor_email,
                reason=body.reason,
                target_user_id=target_user_id,
                origin_ip=(request.client.host if request.client else None),
            )
            if bundle_id:
                await conn.execute(
                    """
                    UPDATE mfa_revocation_pending
                       SET attestation_bundle_ids =
                           attestation_bundle_ids || to_jsonb($2::text)
                     WHERE id = $1::uuid
                    """,
                    revocation_id, bundle_id,
                )

            await _audit_client_action(
                conn, user,
                action="MFA_REVOKED",
                target=target_user_id,
                details={
                    "target_email": target["email"],
                    "revocation_id": revocation_id,
                    "reason": body.reason,
                    "expires_at": expires_at.isoformat(),
                },
                request=request,
            )

    # Outside txn: send the 24h-reversible link to the target +
    # operator alert. Operator severity is always P0-MFA-REVOKE-class
    # (Steve mit B): even a legitimate revoke is incident-response-class.
    await _send_revoke_email_to_target(
        target_email=target["email"],
        org_or_partner_name=user.get("org_name", "your organization"),
        revoking_admin_email=actor_email,
        restore_token=restore_token,
        expires_at=expires_at,
        user_kind="client_user",
    )
    _send_operator_visibility(
        event_type="client_user_mfa_revoked",
        severity="P0-MFA-REVOKE",
        summary=(
            f"Client user MFA REVOKED: {target['email']} by {actor_email} "
            f"(24h restore window)"
        ),
        details={
            "target_user_id": target_user_id,
            "target_email": target["email"],
            "revocation_id": revocation_id,
            "reason": body.reason,
            "expires_at": expires_at.isoformat(),
            "attestation_bundle_id": bundle_id,
        },
        actor_email=actor_email,
        site_id=f"client_org:{org_id}",
        attestation_failed=(bundle_id is None),
    )

    return {
        "status": "revoked",
        "revocation_id": revocation_id,
        "target_email": target["email"],
        "expires_at": expires_at.isoformat(),
        "attestation_bundle_id": bundle_id,
    }


@mfa_admin_client_router.post("/mfa/restore")
async def client_user_mfa_restore(
    request: Request,
    token: str,
    user: dict = Depends(require_client_user),
) -> Dict[str, Any]:
    """Target user clicks the 24h restoration link. Restores MFA
    state (well, lets them re-enroll on next login — we just clear
    the revocation row + write the reversal attestation).

    Auth: any logged-in client_user; endpoint validates token
    matches AND token's target_email matches the actor's session.
    """
    token_hash = _hash_token(token)
    pool = await get_pool()
    actor_email = (user.get("email") or "").lower()

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id::text, scope_id::text, target_user_id::text,
                       target_email, expires_at, restored_at, reason
                  FROM mfa_revocation_pending
                 WHERE reversal_token_hash = $1
                   AND user_kind = 'client_user'
                """,
                token_hash,
            )
            if not row:
                raise HTTPException(status_code=404,
                    detail="Token invalid or revocation not found")
            if row["restored_at"] is not None:
                raise HTTPException(status_code=409,
                    detail="This revocation has already been restored")
            if row["expires_at"] < datetime.now(timezone.utc):
                raise HTTPException(status_code=410,
                    detail="Restoration window expired")
            if (row["target_email"] or "").lower() != actor_email:
                raise HTTPException(status_code=403,
                    detail="Token bound to a different user")

            await conn.execute(
                """
                UPDATE mfa_revocation_pending
                   SET restored_at = NOW(),
                       restored_by_email = $2,
                       reversal_token_hash = ''
                 WHERE id = $1::uuid
                """,
                row["id"], actor_email,
            )

            anchor_site_id = await _resolve_client_anchor_site_id(
                conn, row["scope_id"],
            )
            bundle_id = await _emit_attestation(
                conn, anchor_site_id,
                event_type="client_user_mfa_revocation_reversed",
                actor_email=actor_email,
                reason=f"User self-restored revocation: {row['reason']}",
                target_user_id=row["target_user_id"],
                origin_ip=(request.client.host if request.client else None),
            )
            if bundle_id:
                await conn.execute(
                    """
                    UPDATE mfa_revocation_pending
                       SET attestation_bundle_ids =
                           attestation_bundle_ids || to_jsonb($2::text)
                     WHERE id = $1::uuid
                    """,
                    row["id"], bundle_id,
                )
            await _audit_client_action(
                conn, user,
                action="MFA_REVOCATION_REVERSED",
                target=row["target_user_id"],
                details={"revocation_id": row["id"]},
                request=request,
            )

    _send_operator_visibility(
        event_type="client_user_mfa_revocation_reversed",
        severity="P1",
        summary=(
            f"Client user self-restored MFA: {actor_email} clicked "
            f"the 24h reversal link"
        ),
        details={
            "revocation_id": row["id"],
            "target_email": row["target_email"],
            "attestation_bundle_id": bundle_id,
        },
        actor_email=actor_email,
        site_id=f"client_org:{row['scope_id']}",
        attestation_failed=(bundle_id is None),
    )
    return {
        "status": "restored",
        "revocation_id": row["id"],
        "next_step": "Re-enroll MFA on your next login.",
        "attestation_bundle_id": bundle_id,
    }


# ─── Partner-side endpoints ───────────────────────────────────────


@mfa_admin_partner_router.put("/me/mfa-policy")
async def partner_update_mfa_policy(
    body: MfaPolicyUpdate,
    request: Request,
    partner: dict = require_partner_role("admin"),
) -> Dict[str, Any]:
    """Toggle partner-org mfa_required (admin only)."""
    pool = await get_pool()
    partner_id = str(partner["id"])
    actor_email = (partner.get("email") or "").lower()

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            prior = await conn.fetchval(
                "SELECT mfa_required FROM partners WHERE id = $1::uuid",
                partner_id,
            )
            if prior is None:
                raise HTTPException(status_code=404,
                    detail="Partner not found")
            await conn.execute(
                "UPDATE partners SET mfa_required = $2 WHERE id = $1::uuid",
                partner_id, body.required,
            )
            bundle_id = await _emit_attestation(
                conn, f"partner_org:{partner_id}",
                event_type="partner_mfa_policy_changed",
                actor_email=actor_email,
                reason=body.reason,
                target_user_id=partner_id,
                origin_ip=(request.client.host if request.client else None),
            )

    try:
        await log_partner_activity(
            partner_id=partner_id,
            event_type=PartnerEventType.PARTNER_UPDATED,
            target_type="partner",
            target_id=partner_id,
            event_data={
                "action": "mfa_policy_changed",
                "prior_required": bool(prior),
                "new_required": body.required,
                "reason": body.reason,
                "actor_email": actor_email,
            },
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            request_path=str(request.url.path),
            request_method=request.method,
        )
    except Exception:
        logger.error("partner_mfa_policy_audit_failed", exc_info=True)

    weakening = bool(prior) and not body.required
    _send_operator_visibility(
        event_type="partner_mfa_policy_changed",
        severity=("P1" if weakening else "P2"),
        summary=(
            f"Partner MFA policy changed by {actor_email}: "
            f"required={prior}→{body.required}"
            + (" [POLICY-WEAKENED]" if weakening else "")
        ),
        details={
            "partner_id": partner_id,
            "prior_required": bool(prior),
            "new_required": body.required,
            "weakening": weakening,
            "attestation_bundle_id": bundle_id,
        },
        actor_email=actor_email,
        site_id=f"partner_org:{partner_id}",
        attestation_failed=(bundle_id is None),
    )
    return {
        "status": "updated",
        "mfa_required": body.required,
        "attestation_bundle_id": bundle_id,
    }


@mfa_admin_partner_router.post("/{partner_id}/users/{user_id}/mfa-reset")
async def partner_user_mfa_reset(
    partner_id: str,
    user_id: str,
    body: MfaResetRequest,
    request: Request,
    partner: dict = require_partner_role("admin"),
) -> Dict[str, Any]:
    """Clear partner_user MFA. Admin only."""
    if str(partner["id"]) != partner_id:
        raise HTTPException(status_code=403,
            detail="Cannot reset MFA in a different partner_org")

    pool = await get_pool()
    actor_email = (partner.get("email") or "").lower()

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                """
                SELECT id, email, mfa_enabled FROM partner_users
                 WHERE id = $1::uuid AND partner_id = $2::uuid
                """,
                user_id, partner_id,
            )
            if not target:
                raise HTTPException(status_code=404,
                    detail="Target user not found in your partner_org")
            await conn.execute(
                """
                UPDATE partner_users
                   SET mfa_secret = NULL, mfa_enabled = false,
                       updated_at = NOW()
                 WHERE id = $1::uuid
                """,
                user_id,
            )
            bundle_id = await _emit_attestation(
                conn, f"partner_org:{partner_id}",
                event_type="partner_user_mfa_reset",
                actor_email=actor_email,
                reason=body.reason,
                target_user_id=user_id,
                origin_ip=(request.client.host if request.client else None),
            )

    try:
        await log_partner_activity(
            partner_id=partner_id,
            event_type=PartnerEventType.PARTNER_UPDATED,
            target_type="partner_user",
            target_id=user_id,
            event_data={
                "action": "mfa_reset",
                "target_email": target["email"],
                "had_mfa_enabled": bool(target["mfa_enabled"]),
                "reason": body.reason,
                "actor_email": actor_email,
            },
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            request_path=str(request.url.path),
            request_method=request.method,
        )
    except Exception:
        logger.error("partner_mfa_reset_audit_failed", exc_info=True)

    _send_operator_visibility(
        event_type="partner_user_mfa_reset",
        severity="P1",
        summary=(
            f"Partner user MFA reset: {target['email']} by {actor_email}"
        ),
        details={
            "partner_id": partner_id,
            "target_user_id": user_id,
            "target_email": target["email"],
            "had_mfa_enabled": bool(target["mfa_enabled"]),
            "reason": body.reason,
            "attestation_bundle_id": bundle_id,
        },
        actor_email=actor_email,
        site_id=f"partner_org:{partner_id}",
        attestation_failed=(bundle_id is None),
    )
    return {
        "status": "reset",
        "target_email": target["email"],
        "attestation_bundle_id": bundle_id,
    }


@mfa_admin_partner_router.post("/{partner_id}/users/{user_id}/mfa-revoke")
async def partner_user_mfa_revoke(
    partner_id: str,
    user_id: str,
    body: MfaRevokeRequest,
    request: Request,
    partner: dict = require_partner_role("admin"),
) -> Dict[str, Any]:
    """Revoke partner_user MFA + send 24h reversible-link email."""
    if body.confirm_phrase != "CONFIRM-MFA-REVOKE":
        raise HTTPException(status_code=400,
            detail="confirm_phrase must be exactly 'CONFIRM-MFA-REVOKE'")
    if str(partner["id"]) != partner_id:
        raise HTTPException(status_code=403,
            detail="Cannot revoke MFA in a different partner_org")
    if user_id == str(partner.get("user_id", "")):
        raise HTTPException(status_code=400,
            detail="Cannot revoke your own MFA")

    pool = await get_pool()
    actor_email = (partner.get("email") or "").lower()
    restore_token = secrets.token_urlsafe(32)
    restore_token_hash = _hash_token(restore_token)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=REVOKE_RESTORE_WINDOW_HOURS)

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                """
                SELECT id, email, mfa_enabled FROM partner_users
                 WHERE id = $1::uuid AND partner_id = $2::uuid
                """,
                user_id, partner_id,
            )
            if not target:
                raise HTTPException(status_code=404,
                    detail="Target user not found in your partner_org")
            existing = await conn.fetchrow(
                """
                SELECT id FROM mfa_revocation_pending
                 WHERE target_user_id = $1::uuid
                   AND restored_at IS NULL
                   AND expires_at > NOW()
                """,
                user_id,
            )
            if existing:
                raise HTTPException(status_code=409,
                    detail="A pending revocation already exists for this user")
            await conn.execute(
                """
                UPDATE partner_users
                   SET mfa_secret = NULL, mfa_enabled = false,
                       updated_at = NOW()
                 WHERE id = $1::uuid
                """,
                user_id,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO mfa_revocation_pending (
                    target_user_id, user_kind, scope_id, target_email,
                    revoked_by_email, expires_at, reversal_token_hash,
                    reason
                ) VALUES (
                    $1::uuid, 'partner_user', $2::uuid, $3, $4, $5, $6, $7
                ) RETURNING id::text
                """,
                user_id, partner_id, target["email"], actor_email,
                expires_at, restore_token_hash, body.reason,
            )
            revocation_id = row["id"]
            bundle_id = await _emit_attestation(
                conn, f"partner_org:{partner_id}",
                event_type="partner_user_mfa_revoked",
                actor_email=actor_email,
                reason=body.reason,
                target_user_id=user_id,
                origin_ip=(request.client.host if request.client else None),
            )
            if bundle_id:
                await conn.execute(
                    """
                    UPDATE mfa_revocation_pending
                       SET attestation_bundle_ids =
                           attestation_bundle_ids || to_jsonb($2::text)
                     WHERE id = $1::uuid
                    """,
                    revocation_id, bundle_id,
                )

    try:
        await log_partner_activity(
            partner_id=partner_id,
            event_type=PartnerEventType.PARTNER_UPDATED,
            target_type="partner_user",
            target_id=user_id,
            event_data={
                "action": "mfa_revoked",
                "target_email": target["email"],
                "revocation_id": revocation_id,
                "reason": body.reason,
                "expires_at": expires_at.isoformat(),
                "actor_email": actor_email,
            },
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            request_path=str(request.url.path),
            request_method=request.method,
        )
    except Exception:
        logger.error("partner_mfa_revoke_audit_failed", exc_info=True)

    await _send_revoke_email_to_target(
        target_email=target["email"],
        org_or_partner_name=partner.get("name", "your partner organization"),
        revoking_admin_email=actor_email,
        restore_token=restore_token,
        expires_at=expires_at,
        user_kind="partner_user",
    )
    _send_operator_visibility(
        event_type="partner_user_mfa_revoked",
        severity="P0-MFA-REVOKE",
        summary=(
            f"Partner user MFA REVOKED: {target['email']} by "
            f"{actor_email} (24h restore window)"
        ),
        details={
            "partner_id": partner_id,
            "target_user_id": user_id,
            "target_email": target["email"],
            "revocation_id": revocation_id,
            "reason": body.reason,
            "expires_at": expires_at.isoformat(),
            "attestation_bundle_id": bundle_id,
        },
        actor_email=actor_email,
        site_id=f"partner_org:{partner_id}",
        attestation_failed=(bundle_id is None),
    )
    return {
        "status": "revoked",
        "revocation_id": revocation_id,
        "target_email": target["email"],
        "expires_at": expires_at.isoformat(),
        "attestation_bundle_id": bundle_id,
    }


@mfa_admin_partner_router.post("/me/mfa/restore")
async def partner_user_mfa_restore(
    request: Request,
    token: str,
    partner: dict = Depends(require_partner),
) -> Dict[str, Any]:
    """Partner user clicks the 24h reversal link."""
    token_hash = _hash_token(token)
    pool = await get_pool()
    actor_email = (partner.get("email") or "").lower()
    partner_id = str(partner["id"])

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id::text, scope_id::text, target_user_id::text,
                       target_email, expires_at, restored_at, reason
                  FROM mfa_revocation_pending
                 WHERE reversal_token_hash = $1
                   AND user_kind = 'partner_user'
                """,
                token_hash,
            )
            if not row:
                raise HTTPException(status_code=404,
                    detail="Token invalid or revocation not found")
            if row["restored_at"] is not None:
                raise HTTPException(status_code=409,
                    detail="This revocation has already been restored")
            if row["expires_at"] < datetime.now(timezone.utc):
                raise HTTPException(status_code=410,
                    detail="Restoration window expired")
            if (row["target_email"] or "").lower() != actor_email:
                raise HTTPException(status_code=403,
                    detail="Token bound to a different user")
            if row["scope_id"] != partner_id:
                raise HTTPException(status_code=403,
                    detail="Token bound to a different partner_org")

            await conn.execute(
                """
                UPDATE mfa_revocation_pending
                   SET restored_at = NOW(),
                       restored_by_email = $2,
                       reversal_token_hash = ''
                 WHERE id = $1::uuid
                """,
                row["id"], actor_email,
            )
            bundle_id = await _emit_attestation(
                conn, f"partner_org:{partner_id}",
                event_type="partner_user_mfa_revocation_reversed",
                actor_email=actor_email,
                reason=f"User self-restored revocation: {row['reason']}",
                target_user_id=row["target_user_id"],
                origin_ip=(request.client.host if request.client else None),
            )
            if bundle_id:
                await conn.execute(
                    """
                    UPDATE mfa_revocation_pending
                       SET attestation_bundle_ids =
                           attestation_bundle_ids || to_jsonb($2::text)
                     WHERE id = $1::uuid
                    """,
                    row["id"], bundle_id,
                )

    try:
        await log_partner_activity(
            partner_id=partner_id,
            event_type=PartnerEventType.PARTNER_UPDATED,
            target_type="partner_user",
            target_id=row["target_user_id"],
            event_data={
                "action": "mfa_revocation_reversed",
                "revocation_id": row["id"],
                "actor_email": actor_email,
            },
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            request_path=str(request.url.path),
            request_method=request.method,
        )
    except Exception:
        logger.error("partner_mfa_restore_audit_failed", exc_info=True)

    _send_operator_visibility(
        event_type="partner_user_mfa_revocation_reversed",
        severity="P1",
        summary=(
            f"Partner user self-restored MFA: {actor_email} clicked "
            f"the 24h reversal link"
        ),
        details={
            "revocation_id": row["id"],
            "target_email": row["target_email"],
            "attestation_bundle_id": bundle_id,
        },
        actor_email=actor_email,
        site_id=f"partner_org:{partner_id}",
        attestation_failed=(bundle_id is None),
    )
    return {
        "status": "restored",
        "revocation_id": row["id"],
        "next_step": "Re-enroll MFA on your next login.",
        "attestation_bundle_id": bundle_id,
    }


# ─── Sweep loop (expire stale revocations) ───────────────────────


async def mfa_revocation_expiry_sweep_loop():
    """60s cadence; marks pending revocations expired when their
    24h window passes without restoration. No state mutation on
    user_users table needed (the MFA was already cleared at revoke
    time); this just closes the audit row."""
    import asyncio
    from .bg_heartbeat import record_heartbeat
    while True:
        try:
            record_heartbeat("mfa_revocation_expiry_sweep")
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                expired = await conn.fetch(
                    """
                    UPDATE mfa_revocation_pending
                       SET reversal_token_hash = ''
                     WHERE restored_at IS NULL
                       AND expires_at <= NOW()
                       AND reversal_token_hash <> ''
                    RETURNING id::text, user_kind, scope_id::text,
                              target_email
                    """,
                )
                for row in expired:
                    logger.info(
                        "mfa_revocation_expired",
                        extra={
                            "revocation_id": row["id"],
                            "user_kind": row["user_kind"],
                            "target_email": row["target_email"],
                        },
                    )
        except Exception:
            logger.error("mfa_revocation_sweep_iteration_failed",
                         exc_info=True)
        await asyncio.sleep(60)
