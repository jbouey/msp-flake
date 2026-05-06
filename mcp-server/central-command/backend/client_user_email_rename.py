"""Client-user email rename — partner / substrate / self endpoints.

Round-table 2026-05-05 task #23 closure
(.agent/plans/22-client-user-email-rename-roundtable-2026-05-05.md).

Until this module shipped, `client_users.email` had ZERO mutator
endpoints anywhere — partner, substrate, or self. North Valley test org
2026-05-05 was unreachable except by raw psql. This file lands the four
endpoints + the auto-provision hook in `client_signup` so future
customers don't get stranded.

Endpoints (all single-step, no state machines — see Brian's veto in the
round-table doc: any "target-confirms-via-magic-link-to-NEW-mailbox"
gate would deadlock the very recovery cases this exists to fix; the
self-service path uses magic-link confirm because the user controls
both mailboxes):

  Self-service (auth: require_client_user, ≥0ch reason):
    POST /api/client/users/me/change-email          — initiate (sends
         magic-link confirmation to NEW address; old keeps working)
    POST /api/client/users/me/change-email/confirm  — token-only confirm

  Partner (auth: require_partner_role admin, ≥20ch reason):
    POST /api/partners/me/clients/{org}/users/{user}/change-email

  Substrate (auth: require_auth admin, ≥40ch reason):
    POST /api/admin/client-users/{user}/change-email

Cryptographic chain (CLAUDE.md INVIOLABLE rule):
  - 4 events in ALLOWED_EVENTS: client_user_email_changed_by_self,
    _by_partner, _by_substrate, email_change_reversed (reserved).
  - Ed25519 attestation per state transition, anchored to org's primary
    site_id (or `client_org:<id>` synthetic when no sites yet).
  - Append-only ledger row in client_user_email_change_log.
  - Operator-alert hook with chain-gap escalation pattern uniform with
    sessions 215/216 work — owner-rename is P0 (Maya P1 2026-05-05),
    everything else is P1.

Steve mitigations (round-table 2026-05-05):
  M1: session invalidation in same txn (privilege-retention defense)
  M2: dual-notification (old + new addresses)
  M3: rate limit 3 changes / 30d / user
  M4: refuse if owner-transfer pending_target_accept exists for user
  M5: refuse if mfa_revocation_pending open for user
  M6: P0 alert tier on owner-role rename (Maya P1)
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import text

from . import auth as auth_module
from .client_portal import require_client_user
from .fleet import get_pool
from .partners import require_partner_role
from .privileged_access_attestation import (
    PrivilegedAccessAttestationError,
    create_privileged_access_attestation,
)
from .shared import execute_with_retry, get_db
from .tenant_middleware import admin_connection

logger = logging.getLogger(__name__)

# Three routers — mounted separately in main.py.
email_rename_self_router = APIRouter(
    prefix="/api/client/users/me", tags=["client-portal", "email-rename"],
)
email_rename_partner_router = APIRouter(
    prefix="/api/partners", tags=["partner-portal", "email-rename"],
)
email_rename_substrate_router = APIRouter(
    prefix="/api/admin", tags=["admin", "email-rename"],
)

# Friction defaults per round-table — keep in sync with mig 277 CHECK.
MIN_REASON_PARTNER = 20
MIN_REASON_SUBSTRATE = 40

# Self-service magic-link confirmation window (Steve M2 — user must
# confirm control of NEW mailbox before old is detached).
SELF_CONFIRM_WINDOW_HOURS = 24

# Steve M3: rate-limit changes per user.
RATE_LIMIT_WINDOW_DAYS = 30
RATE_LIMIT_MAX_CHANGES = 3

BASE_URL = os.getenv("FRONTEND_URL", "https://www.osiriscare.net")


# ─── Request models ───────────────────────────────────────────────


class EmailChangeSelfInitiate(BaseModel):
    new_email: EmailStr
    # Re-auth check (Steve self-service M2 — fresh password proves
    # session isn't compromised at the moment of action).
    current_password: str = Field(..., min_length=1)


class EmailChangeSelfConfirm(BaseModel):
    token: str = Field(..., min_length=1)


class EmailChangePartner(BaseModel):
    new_email: EmailStr
    reason: str = Field(..., min_length=MIN_REASON_PARTNER)
    confirm_phrase: str = Field(...,
        description="Type the literal CHANGE-CLIENT-EMAIL")


class EmailChangeSubstrate(BaseModel):
    new_email: EmailStr
    reason: str = Field(..., min_length=MIN_REASON_SUBSTRATE)
    confirm_phrase: str = Field(...,
        description="Type the literal SUBSTRATE-CLIENT-EMAIL-CHANGE")


# ─── Helpers ──────────────────────────────────────────────────────


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize(email: str) -> str:
    return email.strip().lower()


# Round-table 32 (2026-05-05) DRY closure — chain-attestation primitives
# delegated to chain_attestation.py. Thin shims preserve call-site
# signatures across all 5 endpoints in this module.
from .chain_attestation import (
    emit_privileged_attestation as _emit_privileged_attestation_canonical,
    resolve_client_anchor_site_id as _resolve_client_anchor_site_id,
    send_chain_aware_operator_alert as _send_chain_aware_operator_alert,
)


async def _emit_attestation(
    conn,
    anchor_site_id: str,
    event_type: str,
    actor_email: str,
    reason: str,
    target_user_id: str,
    origin_ip: Optional[str] = None,
) -> Optional[str]:
    """Thin shim → chain_attestation.emit_privileged_attestation."""
    _failed, bundle_id = await _emit_privileged_attestation_canonical(
        conn,
        anchor_site_id=anchor_site_id,
        event_type=event_type,
        actor_email=actor_email,
        reason=reason,
        target_user_id=target_user_id,
        origin_ip=origin_ip,
    )
    return bundle_id


def _send_operator_visibility(
    event_type: str,
    severity: str,
    summary: str,
    details: dict,
    actor_email: Optional[str],
    site_id: str,
    attestation_failed: bool,
) -> None:
    """Thin shim → chain_attestation.send_chain_aware_operator_alert."""
    _send_chain_aware_operator_alert(
        event_type=event_type,
        severity=severity,
        summary=summary,
        details=details,
        actor_email=actor_email,
        site_id=site_id,
        attestation_failed=attestation_failed,
    )


async def _check_rate_limit(conn, user_id: str) -> None:
    """Steve M3: refuse if 3+ changes in 30d for this user."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RATE_LIMIT_WINDOW_DAYS)
    row = await conn.fetchrow(
        """
        SELECT COUNT(*) AS n
          FROM client_user_email_change_log
         WHERE client_user_id = $1::uuid
           AND changed_at > $2
        """,
        user_id, cutoff,
    )
    if row and row["n"] >= RATE_LIMIT_MAX_CHANGES:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit: {RATE_LIMIT_MAX_CHANGES} email changes "
                f"per user per {RATE_LIMIT_WINDOW_DAYS} days. "
                f"Subsequent changes must wait."
            ),
        )


async def _check_interlocks(conn, user_id: str) -> None:
    """Steve M4 + M5: refuse if owner-transfer accept-pending OR MFA
    revocation open. Either condition makes a rename a redirect-the-
    magic-link attack vector."""
    # M4 — owner-transfer interlock
    row = await conn.fetchrow(
        """
        SELECT 1 FROM client_org_owner_transfer_requests
         WHERE (initiated_by_user_id = $1::uuid OR target_user_id = $1::uuid)
           AND status IN ('pending_current_ack', 'pending_target_accept')
         LIMIT 1
        """,
        user_id,
    )
    if row:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot rename: an owner-transfer request involving this "
                "user is in flight. Resolve or cancel it first."
            ),
        )
    # M5 — MFA revocation interlock
    row = await conn.fetchrow(
        """
        SELECT 1 FROM mfa_revocation_pending
         WHERE target_user_id = $1::uuid
           AND restored_at IS NULL
           AND expires_at > NOW()
         LIMIT 1
        """,
        user_id,
    )
    if row:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot rename: an MFA revocation is pending for this "
                "user. Resolve restore/expiry first."
            ),
        )


async def _check_email_collision(
    conn, new_email: str, current_user_id: str,
) -> None:
    """Camila #1: idx_client_users_email is GLOBAL-unique (not per-org).
    Pre-flight check; 409 with a friendly detail rather than letting the
    UPDATE fail with UniqueViolationError."""
    row = await conn.fetchrow(
        """
        SELECT 1 FROM client_users
         WHERE LOWER(email) = $1
           AND id != $2::uuid
         LIMIT 1
        """,
        new_email, current_user_id,
    )
    if row:
        raise HTTPException(
            status_code=409,
            detail=(
                "Email already in use on another client_users row. "
                "Pick a different address or retire the existing user "
                "first."
            ),
        )


async def _send_dual_notification(
    old_email: str,
    new_email: str,
) -> None:
    """Steve M2: notify BOTH old + new addresses. Best-effort.

    Opaque mode (task #42 harmonization, 2026-05-06):
      - Subject + body do NOT include org_name, actor_kind, or
        actor email (the actor-omission was already in place per
        Maya P1-2's anti-phishing-quotation concern; harmonization
        extends to also drop org_name).
      - Old address gets a security alert with a portal link. They
        log in to see what's been changed and to whom.
      - New address gets a welcome confirmation. They log in to
        complete setup.

    Posture matches RT21 v2.3 cross-org relocate emails (counsel-
    approved opaque defaults). Helper signature dropped `org_name`
    + `actor_kind` parameters; the portal serves identifying
    context after authentication.
    """
    try:
        from .email_service import send_email
        # OLD address — security alert (opaque)
        await send_email(
            old_email,
            "OsirisCare: login email change on your account",
            (
                "Hello,\n"
                "\n"
                "The login email on one of your OsirisCare accounts "
                "has been changed to a different address.\n"
                "\n"
                "To see what was changed and confirm whether you "
                "initiated it, log in via the portal:\n"
                f"  {BASE_URL}/client/login\n"
                "\n"
                "If this was YOU (or your administrator on your "
                "behalf), no action is required. You will use the "
                "new email going forward; the full audit trail is "
                "in your auditor kit + admin_audit_log.\n"
                "\n"
                "If this was NOT expected, contact your provider AND "
                "OsirisCare support immediately. Your prior sessions "
                "have been invalidated as a precaution.\n"
                "\n"
                "Why this email omits identifying information:\n"
                "We minimize identifying information in unauthenticated "
                "channels (email transit, third-party SMTP relays). "
                "Full details — including the new address and the "
                "actor — are visible only inside the authenticated "
                "portal session.\n"
                "\n"
                "---\n"
                "OsirisCare — substrate-level account access notice"
            ),
        )
    except Exception:
        logger.error(
            "email_rename_old_notify_failed",
            exc_info=True,
            extra={"old_email": old_email, "new_email": new_email},
        )

    try:
        from .email_service import send_email
        # NEW address — welcome (opaque)
        await send_email(
            new_email,
            "OsirisCare: login email is now set on your account",
            (
                "Hello,\n"
                "\n"
                "Your OsirisCare login email has been set to this "
                "address. You can sign in immediately at:\n"
                f"  {BASE_URL}/client/login\n"
                "\n"
                "If you did not expect this, contact OsirisCare "
                "support — someone may have used the platform's "
                "administrative path to redirect this account.\n"
                "\n"
                "Why this email omits identifying information:\n"
                "We minimize identifying information in unauthenticated "
                "channels. Full details — organization, role, prior "
                "address — are visible only inside the authenticated "
                "portal session after you sign in.\n"
                "\n"
                "---\n"
                "OsirisCare — login email update"
            ),
        )
    except Exception:
        logger.error(
            "email_rename_new_notify_failed",
            exc_info=True,
            extra={"old_email": old_email, "new_email": new_email},
        )


def _severity_for_role(role: str) -> str:
    """Maya P1 2026-05-05: owner rename is highest blast radius — P0
    operator-visibility tier, everything else P1."""
    return "P0-OWNER-RENAME" if role == "owner" else "P1"


async def _do_rename_in_txn(
    conn,
    user_row: dict,
    new_email: str,
    actor_kind: str,        # 'self' | 'partner' | 'substrate'
    actor_email: str,
    reason: str,
    event_type: str,
    request: Request,
) -> tuple[Optional[str], str]:
    """Core rename txn — same shape across all three actor classes.

    Returns (attestation_bundle_id_or_None, anchor_site_id).

    Caller must have already done: rate-limit + interlocks + collision
    check, all inside the same transaction or at least with the same
    advisory lock posture.
    """
    user_id = str(user_row["id"])
    old_email = user_row["email"]
    org_id = str(user_row["client_org_id"])
    role = user_row["role"]

    # Steve M1 — session invalidation in same txn.
    await conn.execute(
        "DELETE FROM client_sessions WHERE user_id = $1::uuid",
        user_id,
    )

    # The rename itself.
    await conn.execute(
        """
        UPDATE client_users
           SET email = $1,
               email_verified = $2,
               updated_at = NOW()
         WHERE id = $3::uuid
        """,
        new_email,
        # Self-service has the magic-link confirmation gate so the
        # NEW address is verified; partner + substrate are not.
        actor_kind == "self",
        user_id,
    )

    # Audit ledger — append-only via trigger.
    await conn.execute(
        """
        INSERT INTO client_user_email_change_log
            (client_user_id, client_org_id, old_email, new_email,
             changed_by_kind, changed_by_email, reason)
        VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7)
        """,
        user_id, org_id, old_email, new_email,
        actor_kind, actor_email, reason,
    )

    # Cryptographic anchor.
    anchor_site_id = await _resolve_client_anchor_site_id(conn, org_id)
    bundle_id = await _emit_attestation(
        conn, anchor_site_id,
        event_type=event_type,
        actor_email=actor_email,
        reason=reason,
        target_user_id=user_id,
        origin_ip=(request.client.host if request.client else None),
    )
    if bundle_id:
        await conn.execute(
            """
            UPDATE client_user_email_change_log
               SET attestation_bundle_id = $2
             WHERE client_user_id = $1::uuid
               AND changed_at = (
                   SELECT MAX(changed_at) FROM client_user_email_change_log
                    WHERE client_user_id = $1::uuid
               )
            """,
            user_id, bundle_id,
        )

    return bundle_id, anchor_site_id


async def _load_user_with_lock(conn, user_id: str) -> dict:
    """Pessimistic-lock the row inside the txn so concurrent rename
    attempts serialize cleanly."""
    row = await conn.fetchrow(
        """
        SELECT id, client_org_id, email, role, is_active
          FROM client_users
         WHERE id = $1::uuid
         FOR UPDATE
        """,
        user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Client user not found")
    return dict(row)


async def _load_org_name(conn, org_id: str) -> str:
    row = await conn.fetchrow(
        "SELECT name FROM client_orgs WHERE id = $1::uuid",
        org_id,
    )
    return row["name"] if row else "your organization"


async def _verify_partner_owns_org(conn, partner_id: str, org_id: str) -> None:
    row = await conn.fetchrow(
        """
        SELECT 1 FROM client_orgs
         WHERE id = $1::uuid AND current_partner_id = $2::uuid
         LIMIT 1
        """,
        org_id, partner_id,
    )
    if not row:
        raise HTTPException(
            status_code=403,
            detail=(
                "Cannot rename: this client_org is not currently "
                "managed by your partner organization."
            ),
        )


# ─── Self-service initiate ────────────────────────────────────────


@email_rename_self_router.post("/change-email")
async def self_initiate_email_change(
    body: EmailChangeSelfInitiate,
    request: Request,
    user: dict = Depends(require_client_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Step 1 of self-service rename. Verifies password (re-auth check
    per Steve self-service M2), then sends a magic-link to the NEW
    address. The OLD email keeps working until the link is clicked."""
    new_email = _normalize(body.new_email)

    # Re-auth check via shared.py SQLAlchemy session.
    pw_row = await execute_with_retry(
        db,
        text("SELECT password_hash FROM client_users WHERE id = :uid"),
        {"uid": user["user_id"]},
    )
    pw_h = pw_row.fetchone()
    if not pw_h or not pw_h.password_hash:
        raise HTTPException(
            status_code=400,
            detail="Set a password before changing your email.",
        )
    from passlib.hash import bcrypt
    if not bcrypt.verify(body.current_password, pw_h.password_hash):
        raise HTTPException(status_code=401, detail="Wrong password")

    pool = await get_pool()
    actor_email = (user.get("email") or "").lower()

    if new_email == actor_email:
        raise HTTPException(
            status_code=400,
            detail="New email is the same as the current one.",
        )

    confirm_token = secrets.token_urlsafe(32)
    confirm_token_hash = _hash_token(confirm_token)
    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(hours=SELF_CONFIRM_WINDOW_HOURS)
    )

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            user_row = await _load_user_with_lock(conn, user["user_id"])
            await _check_rate_limit(conn, user["user_id"])
            await _check_interlocks(conn, user["user_id"])
            await _check_email_collision(
                conn, new_email, user["user_id"],
            )

            # Stash the pending change on the user row using the
            # existing magic_token columns. Two safety properties:
            # (a) prefix with `email-confirm:` so a stolen login magic-
            #     link cannot be replayed at the confirm endpoint and
            #     vice versa (different scope tag = different hash);
            # (b) bind new_email INTO the hashed token so the confirm
            #     endpoint can't be steered to a different address by
            #     swapping the new_email query param at click time.
            await conn.execute(
                """
                UPDATE client_users
                   SET magic_token = $1,
                       magic_token_expires_at = $2,
                       updated_at = NOW()
                 WHERE id = $3::uuid
                """,
                _hash_token(f"email-confirm:{confirm_token}:{new_email}"),
                expires_at,
                user["user_id"],
            )

    # Send the confirmation link to the NEW address. The OLD address is
    # NOT notified yet — the rename hasn't happened. If user reconsiders
    # they can simply not click; the token expires in 24h.
    try:
        from .email_service import send_email
        confirm_url = (
            f"{BASE_URL}/client/users/me/change-email/confirm"
            f"?token={confirm_token}&new_email={new_email}"
        )
        await send_email(
            new_email,
            "Confirm your new OsirisCare login email",
            (
                f"You requested to change your OsirisCare login email "
                f"to this address.\n\n"
                f"Click within {SELF_CONFIRM_WINDOW_HOURS} hours to "
                f"confirm:\n\n"
                f"  {confirm_url}\n\n"
                f"Until you click, your existing login email will keep "
                f"working. If you did not request this, ignore the "
                f"email — the link expires automatically.\n\n"
                f"---\n"
                f"OsirisCare — email change confirmation"
            ),
        )
    except Exception:
        logger.error("self_email_change_confirm_send_failed", exc_info=True)

    return {
        "status": "pending_confirmation",
        "new_email": new_email,
        "expires_at": expires_at.isoformat(),
        "next_step": (
            f"Click the link emailed to {new_email} within "
            f"{SELF_CONFIRM_WINDOW_HOURS} hours."
        ),
    }


@email_rename_self_router.post("/change-email/confirm")
async def self_confirm_email_change(
    body: EmailChangeSelfConfirm,
    request: Request,
) -> Dict[str, Any]:
    """Step 2 of self-service rename: token-only auth (the user might
    not currently be logged in; clicking the magic link from their new
    mailbox is the proof of mailbox-control). Same primitive as MFA
    restore (#19 P0-2)."""
    pool = await get_pool()
    # Need the new_email out of the token-bound payload. The frontend
    # passes ?new_email=... back; we re-derive the scoped hash.
    new_email = _normalize(
        request.query_params.get("new_email", "")
    )
    if not new_email:
        raise HTTPException(
            status_code=400,
            detail="new_email query param required",
        )
    expected_hash = _hash_token(
        f"email-confirm:{body.token}:{new_email}"
    )

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, client_org_id, email, role, is_active,
                       magic_token, magic_token_expires_at
                  FROM client_users
                 WHERE magic_token = $1
                 FOR UPDATE
                """,
                expected_hash,
            )
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail="Token invalid, expired, or already used",
                )
            if not row["is_active"]:
                raise HTTPException(
                    status_code=403,
                    detail="Account inactive",
                )
            if (
                not row["magic_token_expires_at"]
                or row["magic_token_expires_at"] < datetime.now(timezone.utc)
            ):
                raise HTTPException(
                    status_code=410,
                    detail="Confirmation window expired",
                )
            await _check_rate_limit(conn, str(row["id"]))
            await _check_interlocks(conn, str(row["id"]))
            await _check_email_collision(conn, new_email, str(row["id"]))

            # Clear the magic token IN the same step as renaming.
            await conn.execute(
                """
                UPDATE client_users
                   SET magic_token = NULL,
                       magic_token_expires_at = NULL
                 WHERE id = $1::uuid
                """,
                row["id"],
            )

            user_dict = dict(row)
            bundle_id, anchor_site_id = await _do_rename_in_txn(
                conn,
                user_dict,
                new_email,
                actor_kind="self",
                actor_email=(row["email"] or "").lower(),
                reason="Self-service email change confirmed via emailed token",
                event_type="client_user_email_changed_by_self",
                request=request,
            )

            org_name = await _load_org_name(conn, str(row["client_org_id"]))

    # Post-txn: dual-notify + operator alert.
    await _send_dual_notification(
        old_email=row["email"],
        new_email=new_email,
    )

    # Maya P0 (round-table 2026-05-05): self-service path MUST fire
    # operator-alert to the partner. Severity P2 (lowest tier) because
    # self-action is operational, not privileged.
    severity = _severity_for_role(row["role"])
    if severity == "P0-OWNER-RENAME":
        # Owner role overrides the self-service P2 floor — owner is
        # always P0 visibility per Maya.
        op_severity = "P0-OWNER-RENAME"
    else:
        op_severity = "P2"
    _send_operator_visibility(
        event_type="client_user_email_changed_by_self",
        severity=op_severity,
        summary=(
            f"Client user self-changed login email "
            f"({org_name}, role={row['role']})"
        ),
        details={
            "client_user_id": str(row["id"]),
            "old_email": row["email"],
            "new_email": new_email,
            "attestation_bundle_id": bundle_id,
        },
        actor_email=(row["email"] or "").lower(),
        site_id=anchor_site_id,
        attestation_failed=(bundle_id is None),
    )

    return {
        "status": "completed",
        "new_email": new_email,
        "attestation_bundle_id": bundle_id,
    }


# ─── Partner endpoint ─────────────────────────────────────────────


@email_rename_partner_router.post(
    "/me/clients/{client_org_id}/users/{user_id}/change-email"
)
async def partner_change_client_email(
    client_org_id: str,
    user_id: str,
    body: EmailChangePartner,
    request: Request,
    partner: dict = require_partner_role("admin"),
) -> Dict[str, Any]:
    """Partner-admin renames a client_user under their org."""
    if body.confirm_phrase != "CHANGE-CLIENT-EMAIL":
        raise HTTPException(
            status_code=400,
            detail="confirm_phrase must be exactly 'CHANGE-CLIENT-EMAIL'",
        )
    new_email = _normalize(body.new_email)
    actor_email = (partner.get("email") or "").lower()
    partner_id = str(partner["id"])

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        async with conn.transaction():
            await _verify_partner_owns_org(conn, partner_id, client_org_id)
            user_row = await _load_user_with_lock(conn, user_id)
            if str(user_row["client_org_id"]) != client_org_id:
                raise HTTPException(
                    status_code=404,
                    detail="User not found in that org",
                )
            if (user_row["email"] or "").lower() == new_email:
                raise HTTPException(
                    status_code=400,
                    detail="New email matches current",
                )
            await _check_rate_limit(conn, user_id)
            await _check_interlocks(conn, user_id)
            await _check_email_collision(conn, new_email, user_id)

            old_email = user_row["email"]
            bundle_id, anchor_site_id = await _do_rename_in_txn(
                conn,
                user_row,
                new_email,
                actor_kind="partner",
                actor_email=actor_email,
                reason=body.reason,
                event_type="client_user_email_changed_by_partner",
                request=request,
            )

            org_name = await _load_org_name(conn, client_org_id)

    await _send_dual_notification(
        old_email=old_email,
        new_email=new_email,
    )

    severity = _severity_for_role(user_row["role"])
    if severity == "P0-OWNER-RENAME":
        op_severity = "P0-OWNER-RENAME"
    else:
        op_severity = "P1"
    _send_operator_visibility(
        event_type="client_user_email_changed_by_partner",
        severity=op_severity,
        summary=(
            f"Partner-admin renamed client_user login email "
            f"({org_name}, role={user_row['role']})"
        ),
        details={
            "client_user_id": user_id,
            "client_org_id": client_org_id,
            "old_email": old_email,
            "new_email": new_email,
            "attestation_bundle_id": bundle_id,
        },
        actor_email=actor_email,
        site_id=anchor_site_id,
        attestation_failed=(bundle_id is None),
    )

    return {
        "status": "completed",
        "user_id": user_id,
        "new_email": new_email,
        "attestation_bundle_id": bundle_id,
    }


# ─── Substrate endpoint ──────────────────────────────────────────


@email_rename_substrate_router.get(
    "/orgs/{org_id}/client-users"
)
async def admin_list_client_users(
    org_id: str,
    user: dict = Depends(auth_module.require_auth),
) -> Dict[str, Any]:
    """Operator-side list of client_users in an org. Task #18 phase 4
    (2026-05-05) — backs the substrate email-rename UI on the operator
    org-dashboard.

    Read-access: any authenticated admin_user (substrate operator).
    Returns minimal identity fields needed to populate the rename
    modal's target picker.
    """
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, email, name, role, is_active,
                   email_verified, last_login_at, created_at
              FROM client_users
             WHERE client_org_id = $1::uuid
             ORDER BY created_at ASC
            """,
            org_id,
        )
    return {
        "users": [
            {
                "id": r["id"],
                "email": r["email"],
                "name": r["name"],
                "role": r["role"],
                "is_active": bool(r["is_active"]),
                "email_verified": bool(r["email_verified"]),
                "last_login_at": (
                    r["last_login_at"].isoformat()
                    if r["last_login_at"] else None
                ),
                "created_at": (
                    r["created_at"].isoformat()
                    if r["created_at"] else None
                ),
            }
            for r in rows
        ],
        "count": len(rows),
    }


@email_rename_substrate_router.post(
    "/client-users/{user_id}/change-email"
)
async def substrate_change_client_email(
    user_id: str,
    body: EmailChangeSubstrate,
    request: Request,
    user: dict = Depends(auth_module.require_auth),
) -> Dict[str, Any]:
    """Central-command admin renames any client_user as a substrate-
    class recovery action. Higher friction (≥40ch) per Steve. Partner
    receives a P0-OWNER-RENAME alert if the row is owner-class, P1
    otherwise.

    Posture preservation: this does NOT make a clinical/operational
    decision on behalf of the operator — it's identity-management on a
    substrate-managed table. Same shape as the MFA admin override path
    (#19) the substrate already runs.
    """
    if body.confirm_phrase != "SUBSTRATE-CLIENT-EMAIL-CHANGE":
        raise HTTPException(
            status_code=400,
            detail=(
                "confirm_phrase must be exactly "
                "'SUBSTRATE-CLIENT-EMAIL-CHANGE'"
            ),
        )
    new_email = _normalize(body.new_email)
    actor_email = (user.get("email") or user.get("username") or "").lower()
    if not actor_email or "@" not in actor_email:
        raise HTTPException(
            status_code=403,
            detail=(
                "actor_email must be a named human admin (chain-of-"
                "custody requirement)"
            ),
        )

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        async with conn.transaction():
            user_row = await _load_user_with_lock(conn, user_id)
            if (user_row["email"] or "").lower() == new_email:
                raise HTTPException(
                    status_code=400,
                    detail="New email matches current",
                )
            await _check_rate_limit(conn, user_id)
            await _check_interlocks(conn, user_id)
            await _check_email_collision(conn, new_email, user_id)

            old_email = user_row["email"]
            bundle_id, anchor_site_id = await _do_rename_in_txn(
                conn,
                user_row,
                new_email,
                actor_kind="substrate",
                actor_email=actor_email,
                reason=body.reason,
                event_type="client_user_email_changed_by_substrate",
                request=request,
            )

            org_name = await _load_org_name(
                conn, str(user_row["client_org_id"]),
            )

    await _send_dual_notification(
        old_email=old_email,
        new_email=new_email,
    )

    # Substrate action — owner-rename is P0, every other role is P1
    # (substrate-class rename is incident-recovery, ALWAYS at-least-P1).
    op_severity = (
        "P0-OWNER-RENAME"
        if user_row["role"] == "owner"
        else "P1"
    )
    _send_operator_visibility(
        event_type="client_user_email_changed_by_substrate",
        severity=op_severity,
        summary=(
            f"SUBSTRATE renamed client_user login email "
            f"({org_name}, role={user_row['role']})"
        ),
        details={
            "client_user_id": user_id,
            "client_org_id": str(user_row["client_org_id"]),
            "old_email": old_email,
            "new_email": new_email,
            "attestation_bundle_id": bundle_id,
            "substrate_actor": actor_email,
        },
        actor_email=actor_email,
        site_id=anchor_site_id,
        attestation_failed=(bundle_id is None),
    )

    return {
        "status": "completed",
        "user_id": user_id,
        "new_email": new_email,
        "attestation_bundle_id": bundle_id,
    }


# ─── Auto-provision-on-signup helper ─────────────────────────────


async def auto_provision_owner_on_signup(
    conn,
    client_org_id: str,
    signup_email: str,
    partner_id: Optional[str] = None,
) -> Optional[str]:
    """Called from client_signup._complete_signup. Honors the partner-
    level toggle (default true). Returns the user_id of the
    newly-created owner row, or None if skipped.

    Caller must already be inside a transaction (Brian's flag from
    round-table — partial-signup must not strand customers).
    """
    if partner_id:
        prow = await conn.fetchrow(
            """
            SELECT auto_provision_owner_on_signup
              FROM partners WHERE id = $1::uuid
            """,
            partner_id,
        )
        if prow and not prow["auto_provision_owner_on_signup"]:
            logger.info(
                "auto_provision_skipped",
                extra={
                    "client_org_id": client_org_id,
                    "partner_id": partner_id,
                    "signup_email": signup_email,
                },
            )
            return None

    norm = _normalize(signup_email)
    # Idempotent: if a client_users row already exists at signup-completion
    # we don't try to re-create.
    existing = await conn.fetchrow(
        """
        SELECT id FROM client_users
         WHERE client_org_id = $1::uuid
           AND LOWER(email) = $2
         LIMIT 1
        """,
        client_org_id, norm,
    )
    if existing:
        return str(existing["id"])

    # Generate a magic-token they'll click to set a password. Reuses
    # the existing client_portal magic-link verify flow — the token is
    # NOT scoped here so the standard flow handles it natively.
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=72)

    new_id_row = await conn.fetchrow(
        """
        INSERT INTO client_users
            (client_org_id, email, role, is_active, email_verified,
             magic_token, magic_token_expires_at, name)
        VALUES ($1::uuid, $2, 'owner', true, false, $3, $4, $5)
        RETURNING id::text
        """,
        client_org_id, norm, token_hash, expires_at,
        "Owner (auto-provisioned)",
    )

    # Email the magic link (best-effort — the row exists either way).
    try:
        from .email_service import send_email
        login_url = (
            f"{BASE_URL}/client/verify?token={token}"
        )
        await send_email(
            norm,
            "Set up your OsirisCare login",
            (
                f"Welcome to OsirisCare. Your account has been "
                f"created. Click within 72 hours to sign in and set "
                f"a password:\n\n"
                f"  {login_url}\n\n"
                f"This link is single-use. After sign-in, you can set "
                f"a permanent password from Settings → Account.\n\n"
                f"---\n"
                f"OsirisCare — welcome"
            ),
        )
    except Exception:
        logger.error(
            "auto_provision_welcome_email_failed",
            exc_info=True,
            extra={"signup_email": norm, "client_org_id": client_org_id},
        )

    logger.info(
        "auto_provision_owner_created",
        extra={
            "client_user_id": new_id_row["id"],
            "client_org_id": client_org_id,
            "signup_email": norm,
        },
    )
    return new_id_row["id"]
