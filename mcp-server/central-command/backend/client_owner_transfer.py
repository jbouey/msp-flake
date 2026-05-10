"""Owner-transfer state machine for client_orgs.

Round-table 2026-05-04 (Camila/Brian/Linda/Steve/Adam): closes punch-
list item #8 from the 5/4 ownership/email gaps audit. Pre-ship: there
was no code path to transfer client_org ownership; a compromised owner
account or a departed-employee owner left the org permanently locked.

Two-step + 24h cooling-off + any-admin-cancel + 1-owner-min DB trigger
(mig 273). Each state transition writes an Ed25519 attestation bundle
(privileged_access_attestation.ALLOWED_EVENTS holds the six event_types).
NOT in fleet_cli.PRIVILEGED_ORDER_TYPES — admin-API class, not fleet-
order class. Lockstep checker permits this asymmetry.

Endpoints (mounted at /api/client/users/owner-transfer/):
  POST   /initiate              — current owner kicks off
  POST   /{id}/ack               — current owner re-confirms
  POST   /accept?token=...       — target accepts via magic link
  POST   /{id}/cancel            — any in-org admin cancels
  GET    /{id}                   — read state (any in-org user)

State machine:
  pending_current_ack  →ack→  pending_target_accept
  pending_target_accept →accept→ (waits cooling_off_until) → completed
  any pending          →cancel→ canceled
  any pending          →(expires_at passed)→ expired
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
from pydantic import BaseModel, EmailStr, Field

from .client_portal import (
    _audit_client_action,
    require_client_admin,
    require_client_owner,
    require_client_user,
)
from .fleet import get_pool
from .privileged_access_attestation import (
    PrivilegedAccessAttestationError,
    create_privileged_access_attestation,
)
from .tenant_middleware import admin_connection, admin_transaction

logger = logging.getLogger(__name__)

owner_transfer_router = APIRouter(
    prefix="/client/users/owner-transfer",
    tags=["client-portal", "owner-transfer"],
)


# Cooling-off + expiry defaults. Per-org overrides land via
# PUT /api/client/users/transfer-prefs (mig 275, task #20). The
# constants below are the fallback when no per-org row sets them
# AND match the historic behavior shipped in mig 273.
DEFAULT_COOLING_OFF_HOURS = 24
DEFAULT_EXPIRY_DAYS = 7


async def _resolve_org_transfer_prefs(conn, org_id: str) -> tuple[int, int]:
    """Read per-org cooling_off_hours + expiry_days from client_orgs
    (mig 275). Returns (cooling_off_hours, expiry_days). Falls back
    to the module DEFAULT_* constants if the row is missing OR the
    columns are NULL (defensive — mig 275 sets NOT NULL but a
    pre-275 deploy snapshot would lack them entirely)."""
    row = await conn.fetchrow(
        """
        SELECT transfer_cooling_off_hours, transfer_expiry_days
          FROM client_orgs
         WHERE id = $1::uuid
        """,
        org_id,
    )
    if not row:
        return (DEFAULT_COOLING_OFF_HOURS, DEFAULT_EXPIRY_DAYS)
    cooling = (row["transfer_cooling_off_hours"]
               if row["transfer_cooling_off_hours"] is not None
               else DEFAULT_COOLING_OFF_HOURS)
    expiry = (row["transfer_expiry_days"]
              if row["transfer_expiry_days"] is not None
              else DEFAULT_EXPIRY_DAYS)
    return (int(cooling), int(expiry))

# Frontend URL for the accept link. Mirrors client_portal.BASE_URL but
# we keep an independent default so this module is self-contained.
BASE_URL = os.getenv("FRONTEND_URL", "https://www.osiriscare.net")

# Reason-validation friction — same shape as the rest of the privileged
# chain (CLAUDE.md privileged-access-chain rule).
MIN_REASON_CHARS = 20


# ─── Request/response models ──────────────────────────────────────


class InitiateOwnerTransferRequest(BaseModel):
    target_email: EmailStr
    reason: str = Field(..., min_length=MIN_REASON_CHARS)


class AckOwnerTransferRequest(BaseModel):
    # Same human re-affirms the same intent. Cheap friction; closes
    # the "compromised current session click-through" attack.
    confirm_phrase: str = Field(...,
        description="Type the literal string CONFIRM-OWNER-TRANSFER")


class AcceptOwnerTransferRequest(BaseModel):
    token: str = Field(..., min_length=20)


class CancelOwnerTransferRequest(BaseModel):
    cancel_reason: str = Field(..., min_length=MIN_REASON_CHARS)


class TransferPrefsUpdate(BaseModel):
    """Per-org config for owner-transfer friction levels (mig 275).

    cooling_off_hours: 0..168 (max 1 week)
    expiry_days: 1..30 (max 30 days)
    reason: ≥20 chars (changes to friction levels are privileged)
    """
    cooling_off_hours: int = Field(..., ge=0, le=168)
    expiry_days: int = Field(..., ge=1, le=30)
    reason: str = Field(..., min_length=MIN_REASON_CHARS)


# ─── Helpers ──────────────────────────────────────────────────────


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _expire_stale_transfers(conn, client_org_id: str) -> int:
    """Mark any pending transfers for this org as expired if their
    expires_at has passed. Returns the number of rows affected. Used at
    the start of the initiate endpoint so a stale row doesn't block
    re-initiation (the unique partial index would otherwise refuse)."""
    rows = await conn.fetch(
        """
        UPDATE client_org_owner_transfer_requests
           SET status = 'expired'
         WHERE client_org_id = $1::uuid
           AND status IN ('pending_current_ack', 'pending_target_accept')
           AND expires_at < NOW()
        RETURNING id
        """,
        client_org_id,
    )
    return len(rows)


# Round-table 32 (2026-05-05) DRY closure — chain-attestation primitives
# delegated to chain_attestation.py. This module's _emit_attestation
# carries the extra `transfer_id` arg + the post-attest UPDATE on
# client_org_owner_transfer_requests.attestation_bundle_ids — preserved
# in the shim. Anchor-namespace + chain-gap rule live in chain_attestation.
from .chain_attestation import (
    emit_privileged_attestation as _emit_privileged_attestation_canonical,
    resolve_client_anchor_site_id,
    send_chain_aware_operator_alert as _send_chain_aware_operator_alert,
)


async def _emit_attestation(
    conn,
    client_org_id: str,
    event_type: str,
    actor_email: str,
    reason: str,
    transfer_id: str,
    origin_ip: Optional[str] = None,
) -> Optional[str]:
    """Owner-transfer attestation: shared chain-emit + the per-row
    attestation_bundle_ids JSONB update specific to this state machine.
    """
    anchor_site_id = await resolve_client_anchor_site_id(conn, client_org_id)
    failed, bundle_id = await _emit_privileged_attestation_canonical(
        conn,
        anchor_site_id=anchor_site_id,
        event_type=event_type,
        actor_email=actor_email,
        reason=reason,
        approvals=[{
            "stage": event_type.split("_")[-1],
            "actor": actor_email,
            "transfer_id": transfer_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
        origin_ip=origin_ip,
    )
    if bundle_id:
        await conn.execute(
            """
            UPDATE client_org_owner_transfer_requests
               SET attestation_bundle_ids =
                   attestation_bundle_ids || to_jsonb($2::text)
             WHERE id = $1::uuid
            """,
            transfer_id, bundle_id,
        )
    return bundle_id


def _send_operator_visibility(
    event_type: str,
    severity: str,
    summary: str,
    details: dict,
    actor_email: Optional[str],
    org_id: str,
    attestation_failed: bool,
) -> None:
    """Thin shim → chain_attestation.send_chain_aware_operator_alert
    with synthetic client_org:<id> site_id (owner-transfer events
    always use the synthetic anchor for the operator-alert side; the
    real site_id is on the cryptographic chain via _emit_attestation
    above)."""
    _send_chain_aware_operator_alert(
        event_type=event_type,
        severity=severity,
        summary=summary,
        details=details,
        actor_email=actor_email,
        site_id=f"client_org:{org_id}",
        attestation_failed=attestation_failed,
    )


#
# OPAQUE-MODE rationale (task #42, harmonized 2026-05-06):
# Subject lines and body content do NOT include org_name, target_email,
# initiator_email, or reason text. Recipients click a magic-link to
# the authenticated client portal where the full context (organization
# name, proposed new owner, current owner, reason, cooling-off window)
# is visible only after authentication.
#
# Same posture as cross_org_site_relocate emails (RT21 v2.3, counsel-
# approved). Helper signatures dropped the verbose parameters; the
# portal serves rich context behind authentication.
#


async def _send_initiator_confirmation_email(
    initiator_email: str,
    transfer_id: str,
) -> None:
    """Email the current owner: 'You initiated an action. Cancel
    here if this wasn't you.' Opaque mode (task #42 harmonization).

    The initiator already has the operational context (they just
    submitted the request). The email's value is the cancel-link
    for the unauthorized-initiation case — that's still actionable
    without identifying details in plaintext."""
    try:
        from .email_service import send_email
        cancel_url = f"{BASE_URL}/client/owner-transfer/{transfer_id}/cancel"
        portal_url = f"{BASE_URL}/client/owner-transfer/{transfer_id}"
        body = (
            "Hello,\n"
            "\n"
            "You initiated an account access change request on one of "
            "your OsirisCare client organizations. To review the "
            "request, log in via the portal:\n"
            f"  {portal_url}\n"
            "\n"
            f"Reference: transfer-{transfer_id}\n"
            "\n"
            "Why this email omits identifying information:\n"
            "We minimize identifying information in unauthenticated "
            "channels (email transit, third-party SMTP relays). Full "
            "details are visible only inside the authenticated portal "
            "session.\n"
            "\n"
            "If you did NOT initiate this request, your account may be "
            "compromised. Cancel immediately:\n"
            f"  {cancel_url}\n"
            "\n"
            "---\n"
            "OsirisCare — substrate-level account access notice"
        )
        await send_email(
            initiator_email,
            "Account change request received",
            body,
        )
    except Exception:
        logger.error("initiator_confirmation_email_failed", exc_info=True)


async def _send_target_accept_email(
    target_email: str,
    transfer_id: str,
    accept_token: str,
    expires_at: datetime,
) -> None:
    """Email the proposed new owner with the magic-link accept URL.
    Opaque mode (task #42): subject + body omit org_name +
    initiator_email + reason. Portal renders all context after
    authentication."""
    try:
        from .email_service import send_email
        accept_url = (
            f"{BASE_URL}/client/owner-transfer/accept"
            f"?token={accept_token}&id={transfer_id}"
        )
        body = (
            "Hello,\n"
            "\n"
            "An action is requested for one of your OsirisCare client "
            "organizations: you have been proposed as the new owner. "
            "To review the request and take action (accept or "
            "decline), click here within 7 days. The link redirects "
            "you through OsirisCare portal authentication, where the "
            "full context (organization name, current owner, reason, "
            "cooling-off window, ownership scope) is visible:\n"
            f"  {accept_url}\n"
            "\n"
            f"Link expires: {expires_at.isoformat()}\n"
            f"Reference: transfer-{transfer_id}\n"
            "\n"
            "Why this email omits identifying information:\n"
            "We minimize identifying information in unauthenticated "
            "channels (email transit, third-party SMTP relays). Full "
            "details are visible only inside the authenticated portal "
            "session.\n"
            "\n"
            "If you did not expect this email, do not click the link. "
            "Contact your OsirisCare account representative.\n"
            "\n"
            "---\n"
            "OsirisCare — substrate-level account access notice"
        )
        await send_email(
            target_email,
            "Confirm an account change request",
            body,
        )
    except Exception:
        logger.error("target_accept_email_failed", exc_info=True)


# ─── Endpoints ────────────────────────────────────────────────────


@owner_transfer_router.post("/initiate")
async def initiate_owner_transfer(
    body: InitiateOwnerTransferRequest,
    request: Request,
    user: dict = Depends(require_client_owner),
) -> Dict[str, Any]:
    """Current owner initiates an ownership transfer.

    Validation:
      - target_email != initiator email (no self-transfer)
      - target must be in same client_org (cross-org not supported)
      - reason ≥20 chars
      - no other active transfer exists for this org
    """
    target_email = body.target_email.lower().strip()
    if target_email == (user.get("email") or "").lower():
        raise HTTPException(
            status_code=400,
            detail="Cannot transfer ownership to yourself",
        )

    pool = await get_pool()
    org_id = str(user["org_id"])
    initiator_email = user["email"]
    accept_token = secrets.token_urlsafe(32)
    accept_token_hash = _hash_token(accept_token)
    now = datetime.now(timezone.utc)

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            # Steve P3 mit D (task #19): refuse owner-transfer initiate
            # while ANY pending MFA revocation exists for ANY in-org
            # user. Race scenario this closes: A revokes B's MFA,
            # then immediately initiates owner-transfer to attacker
            # before B can click the 24h reversal link.
            from .mfa_admin import has_active_mfa_revocation
            if await has_active_mfa_revocation(conn, "client_user", org_id):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Cannot initiate owner-transfer while a pending "
                        "MFA revocation exists in this org. Wait for "
                        "the user to self-restore (or for the 24h "
                        "window to expire), then re-initiate."
                    ),
                )

            # Mig 275 (task #20): read per-org cooling-off + expiry
            # config. Captured at initiate time so a mid-transfer
            # config change doesn't shift the lifecycle of an
            # in-flight transfer.
            cooling_hours, expiry_days = await _resolve_org_transfer_prefs(
                conn, org_id,
            )
            expires_at = now + timedelta(days=expiry_days)
            cooling_off_until = now + timedelta(
                days=expiry_days, hours=cooling_hours,
            )  # cooling-off starts AFTER target accepts; this is a hard cap.

            # Sweep stale rows so the unique partial index doesn't block
            await _expire_stale_transfers(conn, org_id)

            # Reject if a real pending exists
            existing = await conn.fetchrow(
                """
                SELECT id, status FROM client_org_owner_transfer_requests
                 WHERE client_org_id = $1::uuid
                   AND status IN ('pending_current_ack',
                                  'pending_target_accept')
                """,
                org_id,
            )
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"An owner-transfer is already in flight for this "
                        f"org (id={existing['id']}, status="
                        f"{existing['status']}). Cancel it first."
                    ),
                )

            # Resolve target_user_id if the target already has a
            # client_users row in this org. NULL is acceptable — accept
            # flow creates the row.
            target_row = await conn.fetchrow(
                """
                SELECT id FROM client_users
                 WHERE LOWER(email) = $1
                   AND client_org_id = $2::uuid
                """,
                target_email, org_id,
            )
            target_user_id = (str(target_row["id"]) if target_row else None)

            # Create the request row
            row = await conn.fetchrow(
                """
                INSERT INTO client_org_owner_transfer_requests (
                    client_org_id, initiated_by_user_id, target_email,
                    target_user_id, reason, accept_token_hash,
                    expires_at, cooling_off_until
                )
                VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5, $6, $7, $8)
                RETURNING id::text
                """,
                org_id, str(user["user_id"]), target_email,
                target_user_id, body.reason, accept_token_hash,
                expires_at, cooling_off_until,
            )
            transfer_id = row["id"]

            await _audit_client_action(
                conn, user,
                action="OWNER_TRANSFER_INITIATED",
                target=transfer_id,
                details={
                    "target_email": target_email,
                    "target_user_id": target_user_id,
                    "reason": body.reason,
                    "expires_at": expires_at.isoformat(),
                },
                request=request,
            )

            # Attestation bundle (state transition #1)
            bundle_id = await _emit_attestation(
                conn, org_id,
                event_type="client_org_owner_transfer_initiated",
                actor_email=initiator_email,
                reason=body.reason,
                transfer_id=transfer_id,
                origin_ip=(request.client.host if request.client else None),
            )

    # Operator visibility + initiator confirmation email — outside txn
    _send_operator_visibility(
        event_type="client_org_owner_transfer_initiated",
        severity="P1",
        summary=(
            f"Owner-transfer initiated for org {user.get('org_name', org_id)}: "
            f"{initiator_email} → {target_email}"
        ),
        details={
            "transfer_id": transfer_id,
            "target_email": target_email,
            "reason": body.reason,
            "attestation_bundle_id": bundle_id,
        },
        actor_email=initiator_email,
        org_id=org_id,
        attestation_failed=(bundle_id is None),
    )
    await _send_initiator_confirmation_email(
        initiator_email=initiator_email,
        transfer_id=transfer_id,
    )

    return {
        "transfer_id": transfer_id,
        "status": "pending_current_ack",
        "expires_at": expires_at.isoformat(),
        "next_step": (
            "Re-authenticate via the dashboard, then POST /ack with "
            "confirm_phrase='CONFIRM-OWNER-TRANSFER' to send the accept "
            "link to the target. Cancel anytime via POST /{id}/cancel."
        ),
        "attestation_bundle_id": bundle_id,
    }


@owner_transfer_router.post("/{transfer_id}/ack")
async def ack_owner_transfer(
    transfer_id: str,
    body: AckOwnerTransferRequest,
    request: Request,
    user: dict = Depends(require_client_owner),
) -> Dict[str, Any]:
    """Current owner re-confirms the transfer. This unblocks the
    target-accept email send. Anti-accident friction: typed-literal
    confirm_phrase + initiator-must-match-current-user check.
    """
    if body.confirm_phrase != "CONFIRM-OWNER-TRANSFER":
        raise HTTPException(
            status_code=400,
            detail=(
                "confirm_phrase must be exactly 'CONFIRM-OWNER-TRANSFER' "
                "— case-sensitive, no quotes"
            ),
        )

    pool = await get_pool()
    org_id = str(user["org_id"])
    initiator_email = user["email"]
    accept_token = secrets.token_urlsafe(32)
    accept_token_hash = _hash_token(accept_token)

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT t.id::text, t.client_org_id, t.initiated_by_user_id,
                       t.target_email, t.reason, t.expires_at, t.status
                  FROM client_org_owner_transfer_requests t
                 WHERE t.id = $1::uuid
                   AND t.client_org_id = $2::uuid
                """,
                transfer_id, org_id,
            )
            if not row:
                raise HTTPException(status_code=404,
                    detail="Transfer not found in your org")
            if row["status"] != "pending_current_ack":
                raise HTTPException(status_code=409,
                    detail=f"Transfer is in status {row['status']}, "
                           f"cannot ack")
            if row["expires_at"] < datetime.now(timezone.utc):
                raise HTTPException(status_code=410,
                    detail="Transfer has expired")
            # The ack must come from the SAME initiator. A different
            # owner can't piggyback on someone else's initiation.
            if str(row["initiated_by_user_id"]) != str(user["user_id"]):
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Only the user who initiated this transfer "
                        "may ack it. Other owners can cancel."
                    ),
                )

            # Rotate the accept_token_hash on ack — closes any leak
            # window of the initial generation.
            await conn.execute(
                """
                UPDATE client_org_owner_transfer_requests
                   SET status = 'pending_target_accept',
                       current_ack_at = NOW(),
                       accept_token_hash = $2
                 WHERE id = $1::uuid
                """,
                transfer_id, accept_token_hash,
            )

            await _audit_client_action(
                conn, user,
                action="OWNER_TRANSFER_ACKED",
                target=transfer_id,
                details={"target_email": row["target_email"]},
                request=request,
            )

            bundle_id = await _emit_attestation(
                conn, org_id,
                event_type="client_org_owner_transfer_acked",
                actor_email=initiator_email,
                reason=row["reason"],
                transfer_id=transfer_id,
                origin_ip=(request.client.host if request.client else None),
            )

    # Send target the accept email + operator alert — outside txn
    _send_operator_visibility(
        event_type="client_org_owner_transfer_acked",
        severity="P1",
        summary=(
            f"Owner-transfer acked: {initiator_email} → "
            f"{row['target_email']} (target accept email dispatched)"
        ),
        details={
            "transfer_id": transfer_id,
            "target_email": row["target_email"],
            "attestation_bundle_id": bundle_id,
        },
        actor_email=initiator_email,
        org_id=org_id,
        attestation_failed=(bundle_id is None),
    )
    await _send_target_accept_email(
        target_email=row["target_email"],
        transfer_id=transfer_id,
        accept_token=accept_token,
        expires_at=row["expires_at"],
    )

    return {
        "transfer_id": transfer_id,
        "status": "pending_target_accept",
        "next_step": (
            f"Accept email sent to {row['target_email']}. They have "
            f"until {row['expires_at'].isoformat()} to click the link. "
            f"After they accept, a 24h cooling-off begins."
        ),
        "attestation_bundle_id": bundle_id,
    }


@owner_transfer_router.post("/accept")
async def accept_owner_transfer(
    body: AcceptOwnerTransferRequest,
    request: Request,
    user: dict = Depends(require_client_user),
) -> Dict[str, Any]:
    """Target user accepts the transfer via magic-link token.

    Auth: requires the target to be a logged-in client_user. If the
    target_email had no client_users row at initiate time, they must
    accept a separate invite first to provision themselves into the
    org, then accept the transfer.
    """
    token_hash = _hash_token(body.token)
    pool = await get_pool()
    actor_email = user["email"]

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT t.id::text, t.client_org_id::text AS client_org_id,
                       t.target_email, t.reason, t.expires_at,
                       t.status, t.cooling_off_until
                  FROM client_org_owner_transfer_requests t
                 WHERE t.accept_token_hash = $1
                   AND t.status = 'pending_target_accept'
                """,
                token_hash,
            )
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail="Token invalid, expired, or transfer already "
                           "in a terminal state",
                )
            if row["expires_at"] < datetime.now(timezone.utc):
                raise HTTPException(status_code=410,
                    detail="Transfer has expired")

            # The accepting user must match the target_email AND must be
            # in the same client_org. If their email doesn't match the
            # one the initiator typed, reject.
            if (actor_email or "").lower() != (row["target_email"] or "").lower():
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "This accept link is bound to a specific email; "
                        "you are signed in as a different user."
                    ),
                )
            if str(user["org_id"]) != row["client_org_id"]:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Target must be a member of the originating "
                        "organization. Accept the org invite first."
                    ),
                )

            # Mig 275 (task #20): read the per-org cooling-off config
            # (NOT the value captured at initiate time — if the org
            # admin tightened the friction post-initiate, the tighter
            # value applies; if they relaxed it post-initiate, the
            # original cooling_off_until on the row caps it).
            cooling_hours, _ = await _resolve_org_transfer_prefs(
                conn, row["client_org_id"],
            )
            # Cooling-off begins NOW. Cap at expires_at if expires_at
            # is earlier than now+cooling_off (edge case if initiator
            # sat on it for 6.9 days then target accepted with 2h to
            # expiry).
            cooling_off_target = (
                datetime.now(timezone.utc)
                + timedelta(hours=cooling_hours)
            )
            if cooling_off_target > row["expires_at"]:
                cooling_off_target = row["expires_at"]

            await conn.execute(
                """
                UPDATE client_org_owner_transfer_requests
                   SET target_accept_at = NOW(),
                       target_user_id = $2::uuid,
                       cooling_off_until = $3,
                       accept_token_hash = NULL
                 WHERE id = $1::uuid
                """,
                row["id"], str(user["user_id"]), cooling_off_target,
            )
            # NB: status stays 'pending_target_accept' until the
            # complete sweep runs after cooling_off_until elapses.
            # This keeps the unique-partial-index gate active during
            # the cooling-off window so a concurrent re-initiate is
            # blocked.

            await _audit_client_action(
                conn, user,
                action="OWNER_TRANSFER_ACCEPTED",
                target=row["id"],
                details={
                    "cooling_off_until": cooling_off_target.isoformat(),
                },
                request=request,
            )

            bundle_id = await _emit_attestation(
                conn, row["client_org_id"],
                event_type="client_org_owner_transfer_accepted",
                actor_email=actor_email,
                reason=row["reason"],
                transfer_id=row["id"],
                origin_ip=(request.client.host if request.client else None),
            )

    _send_operator_visibility(
        event_type="client_org_owner_transfer_accepted",
        severity="P1",
        summary=(
            f"Owner-transfer accepted by {actor_email}; "
            f"cooling-off until {cooling_off_target.isoformat()}"
        ),
        details={
            "transfer_id": row["id"],
            "cooling_off_until": cooling_off_target.isoformat(),
            "attestation_bundle_id": bundle_id,
        },
        actor_email=actor_email,
        org_id=row["client_org_id"],
        attestation_failed=(bundle_id is None),
    )

    return {
        "transfer_id": row["id"],
        "status": "pending_target_accept",
        "cooling_off_until": cooling_off_target.isoformat(),
        "next_step": (
            f"Cooling-off in progress. Either party (or any in-org "
            f"admin) can cancel via POST /{row['id']}/cancel until "
            f"{cooling_off_target.isoformat()}. After cooling-off, "
            f"the role swap completes automatically (sweep loop)."
        ),
        "attestation_bundle_id": bundle_id,
    }


@owner_transfer_router.post("/{transfer_id}/cancel")
async def cancel_owner_transfer(
    transfer_id: str,
    body: CancelOwnerTransferRequest,
    request: Request,
    user: dict = Depends(require_client_admin),  # owner OR admin
) -> Dict[str, Any]:
    """Cancel a pending transfer. ANY admin in-org can cancel — Steve
    P3: lateral defense against compromised-owner attack."""
    pool = await get_pool()
    org_id = str(user["org_id"])
    actor_email = user["email"]

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id::text, status, target_email, reason
                  FROM client_org_owner_transfer_requests
                 WHERE id = $1::uuid AND client_org_id = $2::uuid
                """,
                transfer_id, org_id,
            )
            if not row:
                raise HTTPException(status_code=404,
                    detail="Transfer not found in your org")
            if row["status"] not in (
                'pending_current_ack', 'pending_target_accept'
            ):
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot cancel from terminal status {row['status']}",
                )

            await conn.execute(
                """
                UPDATE client_org_owner_transfer_requests
                   SET status = 'canceled',
                       canceled_at = NOW(),
                       canceled_by = $2,
                       cancel_reason = $3,
                       accept_token_hash = NULL
                 WHERE id = $1::uuid
                """,
                transfer_id, actor_email, body.cancel_reason,
            )

            await _audit_client_action(
                conn, user,
                action="OWNER_TRANSFER_CANCELED",
                target=transfer_id,
                details={
                    "cancel_reason": body.cancel_reason,
                    "target_email": row["target_email"],
                },
                request=request,
            )

            bundle_id = await _emit_attestation(
                conn, org_id,
                event_type="client_org_owner_transfer_canceled",
                actor_email=actor_email,
                reason=body.cancel_reason,
                transfer_id=transfer_id,
                origin_ip=(request.client.host if request.client else None),
            )

    _send_operator_visibility(
        event_type="client_org_owner_transfer_canceled",
        severity="P1",
        summary=(
            f"Owner-transfer canceled by {actor_email}; "
            f"target was {row['target_email']}"
        ),
        details={
            "transfer_id": transfer_id,
            "cancel_reason": body.cancel_reason,
            "attestation_bundle_id": bundle_id,
        },
        actor_email=actor_email,
        org_id=org_id,
        attestation_failed=(bundle_id is None),
    )

    return {
        "transfer_id": transfer_id,
        "status": "canceled",
        "canceled_by": actor_email,
        "attestation_bundle_id": bundle_id,
    }


async def _complete_transfer(conn, row: dict) -> Optional[str]:
    """Atomically perform the role swap that completes an accepted
    transfer. Caller already holds the txn. Returns the attestation
    bundle_id (or None on attestation failure)."""
    org_id = str(row["client_org_id"])
    transfer_id = row["id"] if isinstance(row["id"], str) else str(row["id"])
    target_user_id = (str(row["target_user_id"])
                      if row["target_user_id"] else None)
    initiator_user_id = str(row["initiated_by_user_id"])

    # Resolve target_user_id if NULL (target accepted but the row
    # was created post-initiate). The accept endpoint already sets
    # target_user_id, so this branch is defensive.
    if not target_user_id:
        tgt = await conn.fetchrow(
            """
            SELECT id FROM client_users
             WHERE LOWER(email) = LOWER($1)
               AND client_org_id = $2::uuid
               AND is_active = true
            """,
            row["target_email"], org_id,
        )
        if not tgt:
            logger.error(
                "owner_transfer_complete_target_missing",
                extra={
                    "transfer_id": transfer_id,
                    "target_email": row["target_email"],
                    "client_org_id": org_id,
                },
            )
            return None
        target_user_id = str(tgt["id"])

    # The role swap. The 1-owner-min trigger (mig 273) means we must
    # promote target BEFORE demoting initiator — otherwise an
    # intermediate state would have zero owners and the trigger fires.
    await conn.execute(
        """
        UPDATE client_users
           SET role = 'owner', updated_at = NOW()
         WHERE id = $1::uuid
        """,
        target_user_id,
    )
    await conn.execute(
        """
        UPDATE client_users
           SET role = 'admin', updated_at = NOW()
         WHERE id = $1::uuid
        """,
        initiator_user_id,
    )
    await conn.execute(
        """
        UPDATE client_org_owner_transfer_requests
           SET status = 'completed',
               completed_at = NOW()
         WHERE id = $1::uuid
        """,
        transfer_id,
    )

    bundle_id = await _emit_attestation(
        conn, org_id,
        event_type="client_org_owner_transfer_completed",
        actor_email="system:owner_transfer_sweep",
        reason=f"Cooling-off elapsed; transfer {transfer_id} completed",
        transfer_id=transfer_id,
    )
    return bundle_id


async def owner_transfer_sweep_loop():
    """Background loop: complete accepted transfers whose cooling-off
    has elapsed; mark stale pending transfers expired.

    Cadence: every 60s. Idempotent — both branches are no-ops when
    no rows match.

    Records a heartbeat each iteration via bg_heartbeat.record_heartbeat
    so the substrate `bg_loop_silent` invariant (Session 214 Block 2)
    catches stuck-await states. EXPECTED_INTERVAL_S=60 in lockstep with
    the sleep cadence — drift is a Session-214-class false-positive
    source.
    """
    import asyncio
    from .bg_heartbeat import record_heartbeat
    while True:
        try:
            record_heartbeat("owner_transfer_sweep")
            pool = await get_pool()
            # admin_transaction (wave-42): owner_transfer_sweep_loop
            # issues 2 admin statements (ready scan + UPDATE complete).
            async with admin_transaction(pool) as conn:
                # Branch 1: complete accepted+cooled-off transfers
                ready = await conn.fetch(
                    """
                    SELECT id::text, client_org_id::text, target_email,
                           initiated_by_user_id, target_user_id,
                           reason
                      FROM client_org_owner_transfer_requests
                     WHERE status = 'pending_target_accept'
                       AND target_accept_at IS NOT NULL
                       AND cooling_off_until <= NOW()
                     ORDER BY cooling_off_until ASC
                     LIMIT 50
                    """,
                )
                for row in ready:
                    try:
                        async with conn.transaction():
                            bundle_id = await _complete_transfer(
                                conn, dict(row)
                            )
                        # Operator alert outside the txn
                        _send_operator_visibility(
                            event_type="client_org_owner_transfer_completed",
                            severity="P1",
                            summary=(
                                f"Owner-transfer COMPLETED: "
                                f"{row['target_email']} is now owner of "
                                f"client_org {row['client_org_id']}"
                            ),
                            details={
                                "transfer_id": row["id"],
                                "target_email": row["target_email"],
                                "attestation_bundle_id": bundle_id,
                            },
                            actor_email="system:owner_transfer_sweep",
                            org_id=row["client_org_id"],
                            attestation_failed=(bundle_id is None),
                        )
                    except Exception:
                        logger.error(
                            "owner_transfer_complete_failed",
                            exc_info=True,
                            extra={"transfer_id": row["id"]},
                        )

                # Branch 2: mark expired
                expired = await conn.fetch(
                    """
                    UPDATE client_org_owner_transfer_requests
                       SET status = 'expired'
                     WHERE status IN ('pending_current_ack',
                                      'pending_target_accept')
                       AND expires_at <= NOW()
                    RETURNING id::text, client_org_id::text,
                              target_email, reason
                    """,
                )
                for row in expired:
                    try:
                        bundle_id = await _emit_attestation(
                            conn, row["client_org_id"],
                            event_type="client_org_owner_transfer_expired",
                            actor_email="system:owner_transfer_sweep",
                            reason=(
                                f"Transfer expired without completion; "
                                f"reason on initiate was: "
                                f"{row['reason'][:160]}"
                            ),
                            transfer_id=row["id"],
                        )
                        _send_operator_visibility(
                            event_type="client_org_owner_transfer_expired",
                            severity="P2",
                            summary=(
                                f"Owner-transfer EXPIRED: target was "
                                f"{row['target_email']}"
                            ),
                            details={
                                "transfer_id": row["id"],
                                "target_email": row["target_email"],
                                "attestation_bundle_id": bundle_id,
                            },
                            actor_email="system:owner_transfer_sweep",
                            org_id=row["client_org_id"],
                            attestation_failed=(bundle_id is None),
                        )
                    except Exception:
                        logger.error(
                            "owner_transfer_expire_failed",
                            exc_info=True,
                            extra={"transfer_id": row["id"]},
                        )
        except Exception:
            logger.error("owner_transfer_sweep_iteration_failed",
                         exc_info=True)
        await asyncio.sleep(60)


@owner_transfer_router.put("/transfer-prefs")
async def update_transfer_prefs(
    body: TransferPrefsUpdate,
    request: Request,
    user: dict = Depends(require_client_owner),
) -> Dict[str, Any]:
    """Configure per-org cooling-off and expiry on owner-transfers.

    Privileged action: changes to friction levels are themselves
    attested. Weakening cooling-off from 24h to 0h reduces the attack
    window operators have to notice + cancel a malicious transfer —
    so the change MUST land in the cryptographic chain alongside the
    underlying transfer events.

    Auth: owner-only (requires the same role that initiates transfers).
    Audit: client_audit_log row + Ed25519 attestation + operator alert.
    Friction: reason ≥20ch + CHECK constraints (mig 275) bound ranges.
    """
    pool = await get_pool()
    org_id = str(user["org_id"])
    actor_email = user.get("email") or "unknown"
    new_cooling = body.cooling_off_hours
    new_expiry = body.expiry_days

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            # Snapshot prior values for the audit diff
            prior = await conn.fetchrow(
                """
                SELECT transfer_cooling_off_hours, transfer_expiry_days
                  FROM client_orgs
                 WHERE id = $1::uuid
                """,
                org_id,
            )
            if not prior:
                raise HTTPException(
                    status_code=404,
                    detail="Client org not found",
                )
            prior_cooling = int(prior["transfer_cooling_off_hours"])
            prior_expiry = int(prior["transfer_expiry_days"])

            await conn.execute(
                """
                UPDATE client_orgs
                   SET transfer_cooling_off_hours = $2,
                       transfer_expiry_days = $3
                 WHERE id = $1::uuid
                """,
                org_id, new_cooling, new_expiry,
            )

            await _audit_client_action(
                conn, user,
                action="TRANSFER_PREFS_CHANGED",
                target=org_id,
                details={
                    "prior_cooling_off_hours": prior_cooling,
                    "new_cooling_off_hours": new_cooling,
                    "prior_expiry_days": prior_expiry,
                    "new_expiry_days": new_expiry,
                    "reason": body.reason,
                },
                request=request,
            )

            # Ed25519 attestation — anchor at org's primary site_id
            # (matches client_user_role_changed precedent).
            site_row = await conn.fetchrow(
                """
                SELECT site_id FROM sites
                 WHERE client_org_id = $1::uuid
                 ORDER BY created_at ASC LIMIT 1
                """,
                org_id,
            )
            anchor_site_id = (
                site_row["site_id"] if site_row
                else f"client_org:{org_id}"
            )
            try:
                att = await create_privileged_access_attestation(
                    conn,
                    site_id=anchor_site_id,
                    event_type="client_org_transfer_prefs_changed",
                    actor_email=actor_email,
                    reason=body.reason,
                    origin_ip=(request.client.host
                               if request.client else None),
                    approvals=[{
                        "stage": "applied",
                        "actor": actor_email,
                        "prior_cooling_off_hours": prior_cooling,
                        "new_cooling_off_hours": new_cooling,
                        "prior_expiry_days": prior_expiry,
                        "new_expiry_days": new_expiry,
                    }],
                )
                bundle_id = att.get("bundle_id")
                attestation_failed = False
            except PrivilegedAccessAttestationError:
                bundle_id = None
                attestation_failed = True
                logger.error(
                    "client_org_transfer_prefs_attestation_failed",
                    exc_info=True,
                    extra={"org_id": org_id},
                )

    # Operator-visibility outside txn. Severity escalates whenever
    # cooling_off is being weakened OR attestation failed — both are
    # signals an operator should see in real time.
    weakening = new_cooling < prior_cooling
    try:
        from .email_alerts import send_operator_alert
        if attestation_failed:
            op_severity = "P0-CHAIN-GAP"
            op_suffix = " [ATTESTATION-MISSING]"
        elif weakening:
            op_severity = "P1"  # weakening is operator-watch-class
            op_suffix = " [FRICTION-WEAKENED]"
        else:
            op_severity = "P2"
            op_suffix = ""
        send_operator_alert(
            event_type="client_org_transfer_prefs_changed",
            severity=op_severity,
            summary=(
                f"Client org transfer-prefs changed by {actor_email}: "
                f"cooling_off {prior_cooling}h→{new_cooling}h, "
                f"expiry {prior_expiry}d→{new_expiry}d{op_suffix}"
            ),
            details={
                "org_id": org_id,
                "prior_cooling_off_hours": prior_cooling,
                "new_cooling_off_hours": new_cooling,
                "prior_expiry_days": prior_expiry,
                "new_expiry_days": new_expiry,
                "reason": body.reason,
                "weakening": weakening,
                "attestation_bundle_id": bundle_id,
                "attestation_failed": attestation_failed,
            },
            site_id=f"client_org:{org_id}",
            actor_email=actor_email,
        )
    except Exception:
        logger.error(
            "operator_alert_dispatch_failed_client_transfer_prefs",
            exc_info=True,
        )

    return {
        "status": "updated",
        "cooling_off_hours": new_cooling,
        "expiry_days": new_expiry,
        "attestation_bundle_id": bundle_id,
    }


@owner_transfer_router.get("/active")
async def get_active_owner_transfer(
    user: dict = Depends(require_client_user),
) -> Dict[str, Any]:
    """Return the in-flight (non-terminal) owner-transfer for this org,
    or 404 if none. Task #18 phase 1 (2026-05-05) — frontend modal
    polls this on open to render the right step in the state machine.

    Mig 273 enforces at-most-one-pending per org via the partial unique
    index `idx_owner_transfer_one_pending` so this query returns at
    most one row.
    """
    pool = await get_pool()
    org_id = str(user["org_id"])
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            """
            SELECT id::text, status, target_email, reason,
                   initiated_by_user_id::text AS initiator_id,
                   target_user_id::text AS target_id,
                   current_ack_at, target_accept_at,
                   completed_at, canceled_at, canceled_by,
                   cancel_reason, expires_at, cooling_off_until,
                   created_at AS initiated_at,
                   jsonb_array_length(attestation_bundle_ids)
                       AS attestation_count
              FROM client_org_owner_transfer_requests
             WHERE client_org_id = $1::uuid
               AND status IN ('pending_current_ack', 'pending_target_accept')
             ORDER BY created_at DESC LIMIT 1
            """,
            org_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="No active transfer")
    out = dict(row)
    for k in ("current_ack_at", "target_accept_at", "completed_at",
              "canceled_at", "expires_at", "cooling_off_until",
              "initiated_at"):
        if out.get(k) is not None:
            out[k] = out[k].isoformat()
    # Frontend modal expects an `ack_at` alias for current_ack_at.
    if out.get("current_ack_at"):
        out["ack_at"] = out["current_ack_at"]
    return out


@owner_transfer_router.get("/{transfer_id}")
async def get_owner_transfer(
    transfer_id: str,
    user: dict = Depends(require_client_user),
) -> Dict[str, Any]:
    """Read state of a transfer (any in-org user can read)."""
    pool = await get_pool()
    org_id = str(user["org_id"])

    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            """
            SELECT id::text, status, target_email, reason,
                   initiated_by_user_id::text AS initiator_id,
                   target_user_id::text AS target_id,
                   current_ack_at, target_accept_at,
                   completed_at, canceled_at, canceled_by,
                   cancel_reason, expires_at, cooling_off_until,
                   created_at,
                   jsonb_array_length(attestation_bundle_ids)
                       AS attestation_count
              FROM client_org_owner_transfer_requests
             WHERE id = $1::uuid AND client_org_id = $2::uuid
            """,
            transfer_id, org_id,
        )
    if not row:
        raise HTTPException(status_code=404,
            detail="Transfer not found in your org")
    out = dict(row)
    for k in ("current_ack_at", "target_accept_at", "completed_at",
              "canceled_at", "expires_at", "cooling_off_until",
              "created_at"):
        if out.get(k) is not None:
            out[k] = out[k].isoformat()
    return out
