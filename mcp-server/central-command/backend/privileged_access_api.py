"""Privileged-access request API (Phase 14 T1 — Session 205).

Two surfaces: partner side (initiate + partner-approve) and client side
(client-approve, consent configuration). Every state transition writes
an attestation bundle via privileged_access_attestation.create_…
so the request lifecycle IS the audit trail.

Design: the *request table* is coordination state (pending → approved
→ activated). The *compliance_bundles* rows are the cryptographic
ledger. On each state change we write a new bundle that chains back to
the prior one, so the record of the approval sequence is itself
signed + OTS-anchored + customer-verifiable.

Non-goals:
  - NOT a replacement for fleet_cli (that's still the mechanical
    signer). This API issues the request + gathers approvals; when
    approvals are complete it calls into the same underlying signer.
  - NOT a bypass of the existing attestation. Every approval writes
    a bundle. Empty approvals cannot activate.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_auth
from .shared import get_db
from .partners import require_partner_role

logger = logging.getLogger(__name__)

partner_router = APIRouter(
    prefix="/api/partners/me/privileged-access",
    tags=["partners"],
)
client_router = APIRouter(
    prefix="/api/client/privileged-access",
    tags=["client"],
)
admin_router = APIRouter(
    prefix="/api/admin/privileged-access",
    tags=["admin"],
)


# ─── Schemas ──────────────────────────────────────────────────────

class InitiateRequest(BaseModel):
    site_id: str = Field(..., description="Target site_id")
    event_type: str = Field(..., pattern=r"^(enable|disable)_emergency_access$")
    reason: str = Field(..., min_length=20, max_length=2000)
    duration_minutes: Optional[int] = Field(60, ge=5, le=1440)


class ApproveRequest(BaseModel):
    request_id: str
    notes: Optional[str] = Field(None, max_length=1000)


class RejectRequest(BaseModel):
    request_id: str
    reason: str = Field(..., min_length=10, max_length=1000)


class ConsentConfig(BaseModel):
    client_approval_required: bool
    approval_timeout_minutes: int = Field(30, ge=5, le=240)
    emergency_bypass_allowed: bool = True
    emergency_bypass_max_per_month: int = Field(1, ge=0, le=10)
    notify_client_emails: List[str] = Field(default_factory=list)


# ─── Helpers ──────────────────────────────────────────────────────

async def _partner_owns_site(db: AsyncSession, partner_id: str, site_id: str) -> bool:
    row = (await db.execute(text("""
        SELECT 1 FROM sites s
        JOIN client_orgs co ON co.id = s.client_org_id
        WHERE s.site_id = :sid AND co.current_partner_id = :pid
    """), {"sid": site_id, "pid": partner_id})).fetchone()
    return row is not None


async def _consent_config(db: AsyncSession, site_id: str) -> Dict[str, Any]:
    row = (await db.execute(text("""
        SELECT client_approval_required, approval_timeout_minutes,
               emergency_bypass_allowed, emergency_bypass_max_per_month,
               notify_client_emails
        FROM privileged_access_consent_config WHERE site_id = :sid
    """), {"sid": site_id})).fetchone()
    if not row:
        return {
            "client_approval_required": False,
            "approval_timeout_minutes": 30,
            "emergency_bypass_allowed": True,
            "emergency_bypass_max_per_month": 1,
            "notify_client_emails": [],
        }
    return dict(row._mapping)


async def _write_attestation(
    db: AsyncSession,
    site_id: str,
    event_type: str,
    actor_email: str,
    reason: str,
    fleet_order_id: Optional[str] = None,
    duration_minutes: Optional[int] = None,
    approvals: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Write a hash-chained attestation bundle via the core module
    (same code path as fleet_cli). Uses the raw asyncpg conn from the
    SQLAlchemy session to match the module's signature."""
    from .privileged_access_attestation import (
        create_privileged_access_attestation,
        PrivilegedAccessAttestationError,
    )
    from .fleet import get_pool
    from .tenant_middleware import admin_connection

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        try:
            return await create_privileged_access_attestation(
                conn,
                site_id=site_id,
                event_type=event_type,
                actor_email=actor_email,
                reason=reason,
                fleet_order_id=fleet_order_id,
                duration_minutes=duration_minutes,
                approvals=approvals,
            )
        except PrivilegedAccessAttestationError as e:
            raise HTTPException(status_code=500, detail=f"attestation failed: {e}")


# ─── Partner endpoints ────────────────────────────────────────────

@partner_router.post("/requests")
async def initiate_request(
    body: InitiateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    partner: dict = require_partner_role("admin", "tech"),
) -> Dict[str, Any]:
    """Partner admin/tech initiates a privileged-access request. Writes
    the initial attestation bundle, creates the request row, and returns
    the request_id + approval requirements."""
    if not await _partner_owns_site(db, partner["id"], body.site_id):
        raise HTTPException(status_code=404, detail="Site not in your partner scope")

    cfg = await _consent_config(db, body.site_id)
    client_required = bool(cfg["client_approval_required"])
    timeout = int(cfg["approval_timeout_minutes"])
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=timeout)

    # Write the INITIATION attestation bundle — this records the request
    # even if no one ever approves it.
    att = await _write_attestation(
        db, body.site_id, body.event_type,
        actor_email=partner.get("email", "unknown"),
        reason=body.reason,
        duration_minutes=body.duration_minutes,
        approvals=[{
            "role": "partner_" + partner.get("role", "unknown"),
            "email": partner.get("email"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage": "initiated",
        }],
    )

    row = (await db.execute(text("""
        INSERT INTO privileged_access_requests (
            site_id, event_type, initiator_email, initiator_role,
            reason, duration_minutes, expires_at,
            partner_approver_email, partner_approver_at, partner_approver_role,
            status, attestation_bundle_id
        ) VALUES (
            :sid, :etype, :iemail, :irole, :reason, :dur, :exp,
            :pemail, NOW(), :prole,
            :status, :bid
        ) RETURNING id::text, status, expires_at
    """), {
        "sid": body.site_id,
        "etype": body.event_type,
        "iemail": partner.get("email", "unknown"),
        "irole": "partner_" + (partner.get("role") or "unknown"),
        "reason": body.reason,
        "dur": body.duration_minutes,
        "exp": expires_at,
        # Partner initiator also counts as the partner_approver (saves a
        # round-trip when client-approval is not required).
        "pemail": partner.get("email", "unknown"),
        "prole": "partner_" + (partner.get("role") or "unknown"),
        "status": "pending" if client_required else "approved",
        "bid": att["bundle_id"],
    })).fetchone()
    await db.commit()

    return {
        "request_id": row.id,
        "status": row.status,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "client_approval_required": client_required,
        "attestation_bundle_id": att["bundle_id"],
        "attestation_chain_position": att["chain_position"],
        "next_step": (
            "Awaiting client admin approval"
            if client_required
            else "Fully approved; issue fleet order via fleet_cli with this request_id"
        ),
    }


@partner_router.get("/requests")
async def list_partner_requests(
    request: Request,
    db: AsyncSession = Depends(get_db),
    partner: dict = require_partner_role("admin", "tech", "billing"),
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    sites_rows = (await db.execute(text("""
        SELECT s.site_id FROM sites s
        JOIN client_orgs co ON co.id = s.client_org_id
        WHERE co.current_partner_id = :pid
    """), {"pid": partner["id"]})).fetchall()
    site_ids = [r.site_id for r in sites_rows]
    if not site_ids:
        return []

    sql = """
        SELECT id::text AS id, site_id, event_type, initiator_email,
               reason, status, requested_at, expires_at,
               client_approver_email, client_approver_at,
               attestation_bundle_id, fleet_order_id::text AS fleet_order_id
        FROM privileged_access_requests
        WHERE site_id = ANY(:sites)
    """
    params: Dict[str, Any] = {"sites": site_ids}
    if status:
        sql += " AND status = :status"
        params["status"] = status
    sql += " ORDER BY requested_at DESC LIMIT 100"

    rows = (await db.execute(text(sql), params)).fetchall()
    return [dict(r._mapping) for r in rows]


# ─── Client endpoints ─────────────────────────────────────────────

async def _execute_client_approval(
    db: AsyncSession,
    request_id: str,
    client_user_email: str,
    client_user_role: str,
    client_user_id: Optional[str] = None,
    via: str = "session",  # 'session' | 'magic_link'
) -> Dict[str, Any]:
    """Shared approval logic used by both the session-auth API and the
    magic-link consume path. Caller is responsible for verifying the
    client_user has appropriate permission BEFORE calling this.

    Writes the chained attestation bundle + flips the request row.
    Returns dict with status + bundle metadata. Raises HTTPException
    on not-pending / not-found.
    """
    row = (await db.execute(text("""
        SELECT id, site_id, event_type, initiator_email, reason,
               duration_minutes, status
        FROM privileged_access_requests
        WHERE id = :id AND status = 'pending'
        FOR UPDATE
    """), {"id": request_id})).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Request not pending")

    att = await _write_attestation(
        db, row.site_id, row.event_type,
        actor_email=client_user_email,
        reason=f"Client approval of request {row.id}: {row.reason}",
        duration_minutes=row.duration_minutes,
        approvals=[{
            "role": "client_" + (client_user_role or "unknown"),
            "email": client_user_email,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage": "client_approved",
            "approved_request": str(row.id),
            "via": via,
        }],
    )
    await db.execute(text("""
        UPDATE privileged_access_requests
        SET client_approver_email = :email,
            client_approver_at = NOW(),
            client_approver_role = 'client_admin',
            status = 'approved'
        WHERE id = :id
    """), {"email": client_user_email, "id": row.id})
    await db.commit()
    return {
        "request_id": str(row.id),
        "status": "approved",
        "client_attestation_bundle_id": att["bundle_id"],
        "client_attestation_chain_position": att["chain_position"],
        "via": via,
    }


async def _execute_client_rejection(
    db: AsyncSession,
    request_id: str,
    client_user_email: str,
    client_user_role: str,
    reason: str,
    via: str = "session",
) -> Dict[str, Any]:
    """Shared rejection logic; mirrors _execute_client_approval."""
    row = (await db.execute(text("""
        SELECT id, site_id, event_type, reason FROM privileged_access_requests
        WHERE id = :id AND status = 'pending'
    """), {"id": request_id})).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Request not pending")

    att = await _write_attestation(
        db, row.site_id, row.event_type,
        actor_email=client_user_email,
        reason=f"REJECTED request {row.id}: {reason}",
        approvals=[{
            "role": "client_" + (client_user_role or "unknown"),
            "email": client_user_email,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage": "client_rejected",
            "rejected_request": str(row.id),
            "rejection_reason": reason,
            "via": via,
        }],
    )
    await db.execute(text("""
        UPDATE privileged_access_requests
        SET status = 'rejected',
            rejected_by = :email,
            rejection_reason = :reason
        WHERE id = :id
    """), {"email": client_user_email, "reason": reason, "id": row.id})
    await db.commit()
    return {
        "request_id": str(row.id),
        "status": "rejected",
        "rejection_attestation_bundle_id": att["bundle_id"],
        "via": via,
    }


async def _resolve_client_user(
    db: AsyncSession, user: dict, site_id: str,
) -> Any:
    """Look up the client_user for this session AND site; return None if
    they don't have access. Used by session-auth endpoints."""
    return (await db.execute(text("""
        SELECT cu.role, cu.email, co.id AS org_id
        FROM client_users cu
        JOIN client_orgs co ON co.id = cu.client_org_id
        JOIN sites s ON s.client_org_id = co.id
        WHERE cu.id = :uid AND s.site_id = :sid
    """), {"uid": user.get("id"), "sid": site_id})).fetchone()


@client_router.post("/approve")
async def client_approve(
    body: ApproveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Client admin approves a pending privileged-access request.
    Session-auth path — see /magic-link/consume for the email-click
    path. Same security model: verify client user has access +
    admin/owner role before calling the shared approval executor."""
    lookup = (await db.execute(text("""
        SELECT site_id FROM privileged_access_requests WHERE id = :id
    """), {"id": body.request_id})).fetchone()
    if not lookup:
        raise HTTPException(status_code=404, detail="Request not found")

    cu = await _resolve_client_user(db, user, lookup.site_id)
    if not cu:
        raise HTTPException(status_code=403, detail="Not authorized for this site")
    if cu.role not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Role 'admin' or 'owner' required")

    return await _execute_client_approval(
        db, body.request_id, cu.email, cu.role, via="session",
    )


@client_router.post("/reject")
async def client_reject(
    body: RejectRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Client admin rejects a pending privileged-access request.
    Session-auth path. See /magic-link/consume for email-click path."""
    lookup = (await db.execute(text("""
        SELECT site_id FROM privileged_access_requests WHERE id = :id
    """), {"id": body.request_id})).fetchone()
    if not lookup:
        raise HTTPException(status_code=404, detail="Request not found")

    cu = await _resolve_client_user(db, user, lookup.site_id)
    if not cu or cu.role not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Not authorized")

    return await _execute_client_rejection(
        db, body.request_id, cu.email, cu.role, body.reason, via="session",
    )


# ─── Magic-link consume endpoint (Phase 14 T2.1 Part 2) ─────────────

class MagicLinkConsumeRequest(BaseModel):
    token: str = Field(..., min_length=16, max_length=256)
    rejection_reason: Optional[str] = Field(
        None, max_length=1000,
        description="Required for reject-action tokens",
    )


@client_router.post("/magic-link/consume")
async def consume_magic_link(
    body: MagicLinkConsumeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Consume a magic-link token + perform the approve/reject action
    it authorizes. Security invariant: token authorizes the action but
    the ATTESTED ACTOR is the current authenticated session user —
    the token is a deep-link convenience, not a bypass of auth.

    Flow:
      1. HMAC-verify the token, check expiry, check single-use
      2. Assert session user email == token's target_user_email
      3. Execute via the same _execute_client_* helpers as session path
      4. Same attestation bundle written, tagged with via='magic_link'
    """
    from .privileged_magic_link import verify_and_consume, MagicLinkError
    from .fleet import get_pool
    from .tenant_middleware import admin_connection

    session_email = (user.get("email") or "").strip().lower()
    if not session_email:
        raise HTTPException(
            status_code=400,
            detail="Session has no email; magic-link consume requires authenticated session",
        )

    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")[:512]

    # Parse token_id from the dotted format and peek at the stored
    # action BEFORE calling verify_and_consume (which requires
    # expected_action as an input).
    token_id = body.token.split(".", 1)[0] if "." in body.token else ""
    if not token_id:
        raise HTTPException(status_code=400, detail="malformed token")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            "SELECT action FROM privileged_access_magic_links WHERE token_id = $1",
            token_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="token not found")

        try:
            verified = await verify_and_consume(
                conn,
                token=body.token,
                expected_action=row["action"],
                session_user_email=session_email,
                client_ip=client_ip,
                user_agent=user_agent,
            )
        except MagicLinkError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Token verified + consumed atomically. Now execute the action.
    # The authenticated session user IS the attested actor.
    cu = (await db.execute(text("""
        SELECT cu.role, cu.email, co.id AS org_id
        FROM client_users cu
        JOIN client_orgs co ON co.id = cu.client_org_id
        JOIN sites s ON s.client_org_id = co.id
        JOIN privileged_access_requests par ON par.site_id = s.site_id
        WHERE cu.id = :uid AND par.id = :req
    """), {"uid": user.get("id"), "req": verified.request_id})).fetchone()
    if not cu:
        raise HTTPException(
            status_code=403,
            detail="Authenticated user does not have access to this site",
        )
    if cu.role not in ("admin", "owner"):
        raise HTTPException(
            status_code=403,
            detail="admin/owner role required to approve privileged access",
        )

    if verified.action == "approve":
        return await _execute_client_approval(
            db, verified.request_id, cu.email, cu.role, via="magic_link",
        )
    elif verified.action == "reject":
        # For magic-link rejections, the "reason" is auto-generated
        # unless the client provided one in the body. Pre-populated
        # reason: "rejected via email link".
        reason = (body.rejection_reason or "Rejected via email magic-link").strip()
        return await _execute_client_rejection(
            db, verified.request_id, cu.email, cu.role, reason, via="magic_link",
        )
    else:
        raise HTTPException(status_code=500, detail=f"unexpected action {verified.action}")


@client_router.get("/consent-config/{site_id}")
async def get_consent_config(
    site_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Client admin views their consent configuration for a site."""
    # Authz: user must be on the org that owns the site
    client_user = (await db.execute(text("""
        SELECT cu.role, cu.email FROM client_users cu
        JOIN client_orgs co ON co.id = cu.client_org_id
        JOIN sites s ON s.client_org_id = co.id
        WHERE cu.id = :uid AND s.site_id = :sid
    """), {"uid": user.get("id"), "sid": site_id})).fetchone()
    if not client_user:
        raise HTTPException(status_code=403, detail="Not authorized for this site")
    return await _consent_config(db, site_id)


@client_router.put("/consent-config/{site_id}")
async def update_consent_config(
    site_id: str,
    body: ConsentConfig,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Client admin updates consent config. The *change itself* is
    attested (RBAC/policy changes are evidence events)."""
    client_user = (await db.execute(text("""
        SELECT cu.role, cu.email FROM client_users cu
        JOIN client_orgs co ON co.id = cu.client_org_id
        JOIN sites s ON s.client_org_id = co.id
        WHERE cu.id = :uid AND s.site_id = :sid
    """), {"uid": user.get("id"), "sid": site_id})).fetchone()
    if not client_user or client_user.role not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="admin role required")

    await db.execute(text("""
        INSERT INTO privileged_access_consent_config (
            site_id, client_approval_required, approval_timeout_minutes,
            emergency_bypass_allowed, emergency_bypass_max_per_month,
            notify_client_emails, updated_at, updated_by
        ) VALUES (
            :sid, :car, :ato, :eba, :ebm, :emails, NOW(), :by
        )
        ON CONFLICT (site_id) DO UPDATE SET
            client_approval_required = EXCLUDED.client_approval_required,
            approval_timeout_minutes = EXCLUDED.approval_timeout_minutes,
            emergency_bypass_allowed = EXCLUDED.emergency_bypass_allowed,
            emergency_bypass_max_per_month = EXCLUDED.emergency_bypass_max_per_month,
            notify_client_emails = EXCLUDED.notify_client_emails,
            updated_at = NOW(),
            updated_by = EXCLUDED.updated_by
    """), {
        "sid": site_id,
        "car": body.client_approval_required,
        "ato": body.approval_timeout_minutes,
        "eba": body.emergency_bypass_allowed,
        "ebm": body.emergency_bypass_max_per_month,
        "emails": body.notify_client_emails,
        "by": client_user.email,
    })
    await db.commit()

    # Attest the config change itself — RBAC/policy events are evidence
    # per the Session 205 round-table accountability principle.
    try:
        att = await _write_attestation(
            db, site_id, "enable_emergency_access",  # category for the chain
            actor_email=client_user.email,
            reason=(
                f"CONSENT_CONFIG_CHANGED by client admin: "
                f"client_approval_required={body.client_approval_required}, "
                f"timeout={body.approval_timeout_minutes}m, "
                f"bypass_allowed={body.emergency_bypass_allowed}"
            ),
            approvals=[{
                "role": "client_" + (client_user.role or "unknown"),
                "email": client_user.email,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "stage": "consent_config_changed",
                "new_config": body.dict(),
            }],
        )
        return {"status": "updated", "attestation_bundle_id": att["bundle_id"]}
    except HTTPException:
        # Config update succeeded, attestation failed — report both.
        return {"status": "updated", "attestation_error": True}


# ─── Admin views ──────────────────────────────────────────────────

@admin_router.get("/requests")
async def admin_list_requests(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
    status: Optional[str] = None,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """Admin view across all partners."""
    sql = """
        SELECT id::text AS id, site_id, event_type, initiator_email,
               initiator_role, reason, status, requested_at,
               partner_approver_email, partner_approver_at,
               client_approver_email, client_approver_at,
               attestation_bundle_id,
               fleet_order_id::text AS fleet_order_id
        FROM privileged_access_requests
        WHERE requested_at > NOW() - make_interval(days => :days)
    """
    params: Dict[str, Any] = {"days": days}
    if status:
        sql += " AND status = :status"
        params["status"] = status
    sql += " ORDER BY requested_at DESC LIMIT 200"

    rows = (await db.execute(text(sql), params)).fetchall()
    return [dict(r._mapping) for r in rows]
