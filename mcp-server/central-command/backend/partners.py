"""Partner management endpoints.

Provides API endpoints for partner (MSP/reseller) management:
- Partner CRUD operations
- Partner authentication
- Provision code generation and claiming
- Partner-scoped site listing

IMPORTANT: Route order matters in FastAPI - /me routes must be defined
before /{partner_id} routes to avoid /me being captured as a partner_id.
"""

import json
import os
import re
import html
import secrets
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Header, Depends, Response, Cookie, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import uuid as _uuid

from .fleet import get_pool
from .auth import require_admin
from .tenant_middleware import tenant_connection, admin_connection, admin_transaction
from .partner_auth import hash_session_token
from .db_utils import _uid
from .partner_activity_logger import (
    log_partner_activity,
    log_partner_site_action,
    log_partner_credential_action,
    log_partner_provision_action,
    PartnerEventType,
    get_partner_activity,
    get_partner_activity_stats,
)

logger = logging.getLogger("dashboard_api.partners")

# API endpoint from environment variable
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.osiriscare.net")

# SECURITY: bcrypt is required
try:
    import bcrypt
except ImportError:
    raise RuntimeError("bcrypt library required. Install with: pip install bcrypt")

# Try to import qrcode for QR generation
try:
    import qrcode
    from io import BytesIO
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


router = APIRouter(prefix="/api/partners", tags=["partners"])

# Valid values for client_alert_mode on orgs and sites
VALID_ALERT_MODES = {"self_service", "informed", "silent"}


# =============================================================================
# MODELS
# =============================================================================

class PartnerCreate(BaseModel):
    """Model for creating a new partner."""
    name: str
    slug: str  # subdomain identifier
    contact_email: str
    contact_phone: Optional[str] = None
    brand_name: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = "#4F46E5"
    revenue_share_percent: int = 40


class PartnerUpdate(BaseModel):
    """Model for updating partner info."""
    name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    brand_name: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    revenue_share_percent: Optional[int] = None
    status: Optional[str] = None


class PartnerUserCreate(BaseModel):
    """Model for creating a partner user."""
    email: str
    name: Optional[str] = None
    role: str = "admin"  # admin, tech, billing


class ProvisionCreate(BaseModel):
    """Model for creating a provision code."""
    client_name: Optional[str] = None
    target_site_id: Optional[str] = None
    expires_days: int = 30


class ProvisionBulkEntry(BaseModel):
    """Single row in a bulk provision-code request."""
    client_name: Optional[str] = None
    target_site_id: Optional[str] = None


class ProvisionBulkCreate(BaseModel):
    """Bulk create provision codes. One code per entry; empty list rejected.
    100-entry cap matches the rate-limit budget for a single partner action."""
    entries: List[ProvisionBulkEntry]
    expires_days: int = 30


class ProvisionClaim(BaseModel):
    """Model for claiming a provision code."""
    provision_code: str
    mac_address: str
    hostname: Optional[str] = None


class BrandingUpdate(BaseModel):
    """Model for updating partner white-label branding."""
    brand_name: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    tagline: Optional[str] = None
    support_email: Optional[str] = None
    support_phone: Optional[str] = None
    email_from_display_name: Optional[str] = None
    email_reply_to_address: Optional[str] = None


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

_HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
_STRIP_HTML_RE = re.compile(r'<[^>]+>')


def _validate_hex_color(value: str) -> str:
    """Validate that a string is a valid #XXXXXX hex color."""
    if not _HEX_COLOR_RE.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid hex color format: {value}")
    return value


def _sanitize_text(value: str) -> str:
    """Strip HTML tags and decode entities from user-provided text."""
    return html.unescape(_STRIP_HTML_RE.sub('', value)).strip()

def hash_api_key(api_key: str) -> str:
    """Hash an API key for secure storage.

    Uses HMAC-SHA256 with server secret for secure hashing that allows lookup.
    Note: bcrypt is not used for API keys because they need to be looked up by hash.
    """
    secret = os.getenv("API_KEY_SECRET")
    if not secret:
        # SECURITY: Fail if no secret is configured
        logger.error("API_KEY_SECRET environment variable must be set for secure API key hashing")
        raise RuntimeError("API_KEY_SECRET not configured - set this environment variable")
    return hashlib.sha256(f"{secret}:{api_key}".encode()).hexdigest()


def verify_api_key(api_key: str, api_key_hash: str) -> bool:
    """Verify an API key against its stored hash."""
    computed_hash = hash_api_key(api_key)
    return secrets.compare_digest(computed_hash, api_key_hash)


def generate_api_key() -> str:
    """Generate a new API key."""
    return secrets.token_urlsafe(32)


def generate_provision_code() -> str:
    """Generate a short provision code for QR."""
    return secrets.token_hex(8).upper()  # 16 char hex string


def generate_magic_token() -> str:
    """Generate a magic link token."""
    return secrets.token_urlsafe(32)


async def get_partner_from_api_key(api_key: str):
    """Validate API key and return partner."""
    pool = await get_pool()
    key_hash = hash_api_key(api_key)

    async with admin_connection(pool) as conn:
        partner = await conn.fetchrow("""
            SELECT id, name, slug, status, api_key_expires_at
            FROM partners
            WHERE api_key_hash = $1
        """, key_hash)

        if not partner:
            return None
        if partner['status'] != 'active':
            return None
        # Check API key expiry
        if partner['api_key_expires_at']:
            from datetime import datetime, timezone
            if datetime.now(timezone.utc) > partner['api_key_expires_at']:
                logger.warning("Expired API key used for partner %s", partner['id'])
                return None
        return partner


async def require_partner(
    request: Request = None,
    x_api_key: str = Header(None),
    osiris_partner_session: Optional[str] = Cookie(None)
):
    """Dependency to require valid partner authentication.

    Supports two auth methods:
    1. API Key via X-API-Key header
    2. OAuth session via osiris_partner_session cookie

    Mismatch detection (P0 hardening, 2026-04-28 round-table finding):
    when BOTH credentials are present, they MUST resolve to the same
    partner.id. If they don't, log ERROR and 401 — never silently let
    one win. The 2026-04-28 CSRF rewrite (commits 0c81fef6 + efe413cf)
    started sending both unconditionally on every partner mutation,
    which made a leaked X-API-Key able to silently override whoever's
    session was in the browser. Detection makes that explicit.
    """
    pool = await get_pool()

    # Step 1: resolve API key (if present).
    api_key_partner = None
    if x_api_key:
        api_key_partner = await get_partner_from_api_key(x_api_key)
        if not api_key_partner:
            raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    # Step 2: resolve session cookie (if present). Wrapped in
    # try/except so a transient session-DB outage doesn't 500 a
    # request that an api-key path could otherwise have authed
    # cleanly (round-table angle 3 P1 — race robustness).
    session_partner = None
    if osiris_partner_session:
        try:
            session_hash = hash_session_token(osiris_partner_session)
            async with admin_connection(pool) as conn:
                session_row = await conn.fetchrow("""
                    SELECT ps.partner_id, p.id, p.name, p.slug, p.status,
                           pu.role AS user_role, pu.id AS partner_user_id
                    FROM partner_sessions ps
                    JOIN partners p ON p.id = ps.partner_id
                    LEFT JOIN partner_users pu ON pu.id = ps.partner_user_id
                    WHERE ps.session_token_hash = $1
                      AND ps.expires_at > NOW()
                      AND p.status = 'active'
                      AND (p.pending_approval IS NULL OR p.pending_approval = false)
                """, session_hash)
                if session_row:
                    session_partner = session_row
        except Exception:
            # Degraded session backend → fall through to api-key path.
            # Logged at WARNING (read-side, eat-but-record) per
            # Session 205 "reads may eat exceptions; writes log-and-raise".
            logger.warning("session_lookup_failed", exc_info=True)

    # Step 3: mismatch detection — both present, different partners.
    if api_key_partner and session_partner:
        api_pid = str(api_key_partner['id'])
        sess_pid = str(session_partner['id'])
        if api_pid != sess_pid:
            # Capture source IP for forensics — mismatch is security-
            # relevant per round-table P1.
            client_ip = None
            try:
                if request is not None and request.client is not None:
                    client_ip = request.client.host
            except Exception:
                client_ip = None
            logger.error(
                "auth_token_partner_mismatch",
                extra={
                    "api_key_partner_id": api_pid,
                    "session_partner_id": sess_pid,
                    "api_key_partner_slug": api_key_partner.get('slug'),
                    "session_partner_slug": session_partner.get('slug'),
                    "client_ip": client_ip,
                },
            )
            raise HTTPException(
                status_code=401,
                detail=(
                    "Token mismatch: X-API-Key and session cookie resolve to "
                    "different partners. Re-authenticate with one credential."
                ),
            )

    # Step 4: API key takes precedence when present (preserves existing
    # behavior; mismatch would have already 401'd above).
    if api_key_partner:
        result = dict(api_key_partner)
        # Derive role from the API key creator if tracked, otherwise default to admin.
        # API keys created via the dashboard have created_by_user_id (Migration 152).
        # Legacy keys without a creator default to admin for backwards compatibility.
        api_key_role = "admin"
        try:
            async with admin_connection(pool) as conn:
                import hashlib as _hl
                key_hash = _hl.sha256(x_api_key.encode()).hexdigest()
                creator = await conn.fetchval("""
                    SELECT pu.role FROM api_keys ak
                    JOIN partner_users pu ON pu.id = ak.created_by_user_id
                    WHERE ak.key_hash = $1 AND ak.active = true
                """, key_hash)
                if creator:
                    api_key_role = creator
        except Exception:
            pass  # Fall back to admin on any error
        result["user_role"] = api_key_role
        return result

    # Step 5: session-only path. Reuses the row resolved in Step 2 — no
    # duplicate query.
    if session_partner:
        result = {
            'id': session_partner['id'],
            'name': session_partner['name'],
            'slug': session_partner['slug'],
            'status': session_partner['status'],
        }
        result["user_role"] = session_partner.get("user_role") or "admin"  # NULL = legacy session = admin
        result["partner_user_id"] = str(session_partner["partner_user_id"]) if session_partner.get("partner_user_id") else None
        return result

    raise HTTPException(status_code=401, detail="Authentication required")


def require_partner_role(*allowed_roles):
    """Dependency factory that checks partner_users.role against allowed roles.
    Returns 403 for unauthorized roles."""
    async def _check(partner: dict = Depends(require_partner)):
        if partner.get("user_role") not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions for this action",
            )
        return partner
    return Depends(_check)


# =============================================================================
# AUDIT LOGGING (Session 203 H3)
# =============================================================================
#
# Partner mutations flow through the existing `log_partner_activity()`
# infrastructure in partner_activity_logger.py, which writes to the
# `partner_activity_log` table. New event types were added to
# PartnerEventType for Session 203 (DRIFT_CONFIG_UPDATED,
# MAINTENANCE_WINDOW_SET, etc.). Do NOT create a parallel audit helper
# here — DRY: single source of truth for partner audit events.
#
# Usage pattern in mutating endpoints:
#
#   from .partner_activity_logger import log_partner_activity, PartnerEventType
#   await log_partner_activity(
#       partner_id=str(partner["id"]),
#       event_type=PartnerEventType.DRIFT_CONFIG_UPDATED,
#       target_type="site",
#       target_id=site_id,
#       event_data={"check_count": len(checks)},
#       ip_address=request.client.host if request.client else None,
#       user_agent=request.headers.get("user-agent", "")[:500],
#       request_path=str(request.url.path),
#       request_method=request.method,
#   )


# =============================================================================
# PUBLIC ENDPOINTS (no auth required)
# =============================================================================

@router.post("/claim")
async def claim_provision_code(claim: ProvisionClaim):
    """Claim a provision code (called by appliance during setup)."""
    pool = await get_pool()

    # admin_transaction (wave-5): 5 admin reads/writes must pin to one PgBouncer backend.
    async with admin_transaction(pool) as conn:
        # Find and validate provision code
        provision = await conn.fetchrow("""
            SELECT id, partner_id, target_site_id, client_name, status, expires_at
            FROM appliance_provisions
            WHERE provision_code = $1
        """, claim.provision_code.upper())

        if not provision:
            raise HTTPException(status_code=404, detail="Invalid provision code")

        if provision['status'] != 'pending':
            raise HTTPException(status_code=400, detail=f"Provision code already {provision['status']}")

        if provision['expires_at'] and provision['expires_at'] < datetime.now(timezone.utc):
            # Mark as expired
            await conn.execute("""
                UPDATE appliance_provisions SET status = 'expired' WHERE id = $1
            """, provision['id'])
            raise HTTPException(status_code=400, detail="Provision code expired")

        # Get partner info for response
        partner = await conn.fetchrow("""
            SELECT slug, brand_name, primary_color, logo_url
            FROM partners WHERE id = $1
        """, provision['partner_id'])

        # Generate site_id if not pre-assigned
        site_id = provision['target_site_id']
        if not site_id:
            # Generate from client name or MAC
            base = provision['client_name'] or claim.hostname or claim.mac_address
            site_id = base.lower().replace(' ', '-').replace(':', '-')[:50]
            site_id = f"{site_id}-{secrets.token_hex(3)}"

        # Generate appliance_id
        appliance_id = f"{site_id}-{claim.mac_address.upper().replace(':', '%3A')}"

        # Create site if doesn't exist
        await conn.execute("""
            INSERT INTO sites (site_id, clinic_name, partner_id, status, onboarding_stage)
            VALUES ($1, $2, $3, 'pending', 'provisioning')
            ON CONFLICT (site_id) DO NOTHING
        """, site_id, provision['client_name'] or site_id.replace('-', ' ').title(), provision['partner_id'])

        # Mark provision as claimed
        await conn.execute("""
            UPDATE appliance_provisions
            SET status = 'claimed',
                claimed_at = NOW(),
                claimed_by_mac = $1,
                claimed_appliance_id = $2
            WHERE id = $3
        """, claim.mac_address.upper(), appliance_id, provision['id'])

        return {
            "status": "claimed",
            "site_id": site_id,
            "appliance_id": appliance_id,
            "partner": {
                "slug": partner['slug'],
                "brand_name": partner['brand_name'],
                "primary_color": partner['primary_color'],
                "logo_url": partner['logo_url'],
            },
            "api_endpoint": API_BASE_URL,
            "message": "Appliance provisioned successfully"
        }


# =============================================================================
# PARTNER SELF-SERVICE ENDPOINTS (Partner-authenticated)
# These must come BEFORE /{partner_id} routes!
# =============================================================================

@router.get("/me/users")
async def list_my_partner_users(
    partner: dict = require_partner_role("admin", "tech", "billing"),
):
    """List partner_users in the caller's partner_org. Task #18 phase 3
    (2026-05-05) — backs the new PartnerUsersScreen frontend.

    Read-access: any authenticated partner user (admin/tech/billing).
    Write paths (invite / role-change / remove) live elsewhere and
    enforce admin-only at their own gate; this endpoint is read-only.

    Returns an array of users sorted by created_at ASC so the
    org-creating admin appears first by default.
    """
    pool = await get_pool()
    partner_id = str(partner["id"])
    async with admin_connection(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, email, name, role, status,
                   mfa_enabled, mfa_required,
                   last_login_at, created_at
              FROM partner_users
             WHERE partner_id = $1::uuid
             ORDER BY created_at ASC
            """,
            partner_id,
        )
    return {
        "users": [
            {
                "id": r["id"],
                "email": r["email"],
                "name": r["name"],
                "role": r["role"],
                "status": r["status"],
                "mfa_enabled": bool(r["mfa_enabled"]),
                "mfa_required": bool(r["mfa_required"]),
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


class PartnerUserRoleUpdate(BaseModel):
    """PATCH /me/users/{id}/role body."""
    role: str = Field(..., pattern="^(admin|tech|billing)$")
    reason: str = Field(..., min_length=20)


class PartnerUserDeactivate(BaseModel):
    """DELETE /me/users/{id} body — DELETE with body is unusual but
    needed to capture reason for audit."""
    reason: str = Field(..., min_length=20)
    confirm_phrase: str = Field(...,
        description="Type the literal DEACTIVATE-PARTNER-USER")


# Round-table 32 (2026-05-05) DRY closure — partner_user attestation
# delegated to chain_attestation.py canonical helpers. The signature
# of `_emit_partner_user_attestation` is preserved so the 3 call
# sites (create / role-change / deactivate) need no changes.
async def _emit_partner_user_attestation(
    pool, partner_id: str, event_type: str,
    actor_email: str, reason: str,
    target_user_id: str, target_email: str,
    new_role: Optional[str] = None,
    request: Optional[Request] = None,
) -> tuple[bool, Optional[str]]:
    """Thin shim → chain_attestation.emit_privileged_attestation
    with partner-org anchor namespace + partner-specific approvals
    payload (target_email + optional new_role)."""
    from .chain_attestation import emit_privileged_attestation
    approvals = [{
        "stage": "applied",
        "actor": actor_email,
        "target_user_id": target_user_id,
        "target_email": target_email,
    }]
    if new_role is not None:
        approvals[0]["new_role"] = new_role
    async with admin_connection(pool) as att_conn:
        return await emit_privileged_attestation(
            att_conn,
            anchor_site_id=f"partner_org:{partner_id}",
            event_type=event_type,
            actor_email=actor_email or "unknown",
            reason=reason,
            approvals=approvals,
            origin_ip=(request.client.host
                       if request and request.client else None),
        )


def _partner_user_op_alert(
    event_type: str, severity: str, summary: str,
    details: dict, actor_email: Optional[str],
    site_id: str, attestation_failed: bool,
):
    """Thin shim → chain_attestation.send_chain_aware_operator_alert."""
    from .chain_attestation import send_chain_aware_operator_alert
    send_chain_aware_operator_alert(
        event_type=event_type,
        severity=severity,
        summary=summary,
        details=details,
        actor_email=actor_email,
        site_id=site_id,
        attestation_failed=attestation_failed,
    )


@router.post("/me/users")
async def self_create_partner_user(
    body: PartnerUserCreate,
    request: Request,
    partner: dict = require_partner_role("admin"),
):
    """Self-scoped partner_user creation — admin-only. Mirrors the
    operator-class POST /{partner_id}/users but caller's partner_id is
    the implicit target. Task #18 phase 3 follow-up (Session 217).
    """
    pool = await get_pool()
    partner_id = str(partner["id"])
    actor_email = (partner.get("email") or "").lower()

    # Round-table 31 P1 reactivate-path (2026-05-05): pre-fix any
    # existing row (active OR inactive) returned 400, leaving deactivated
    # users with no self-service path back. Inactive rows now reactivate
    # via UPDATE; active rows still 400 (duplicate). Idempotent +
    # audited the same way as fresh-create.
    async with admin_connection(pool) as conn:
        existing = await conn.fetchrow(
            """
            SELECT id::text, status, role FROM partner_users
             WHERE partner_id = $1::uuid AND email = $2
            """,
            partner_id, body.email.lower(),
        )
        if existing and existing["status"] == "active":
            raise HTTPException(
                status_code=400,
                detail="Email already exists as active partner_user",
            )

        magic_token = generate_magic_token()
        magic_expires = datetime.now(timezone.utc) + timedelta(days=7)
        is_reactivation = bool(existing)

        if is_reactivation:
            row = await conn.fetchrow(
                """
                UPDATE partner_users
                   SET status = 'active',
                       role = $2,
                       name = COALESCE($3, name),
                       magic_token = $4,
                       magic_token_expires_at = $5,
                       updated_at = NOW()
                 WHERE id = $1::uuid
                 RETURNING id::text, email, name, role
                """,
                existing["id"], body.role, body.name,
                magic_token, magic_expires,
            )
        else:
            row = await conn.fetchrow(
                """
                INSERT INTO partner_users (
                    partner_id, email, name, role,
                    magic_token, magic_token_expires_at
                ) VALUES ($1::uuid, $2, $3, $4, $5, $6)
                RETURNING id::text, email, name, role
                """,
                partner_id,
                body.email.lower(),
                body.name,
                body.role,
                magic_token,
                magic_expires,
            )

    # Maya final sweep (Session 217): emit a distinct event_type for
    # the reactivate branch so auditor chain readers can distinguish
    # net-new user from deactivate-then-reactivate.
    event_type = (
        "partner_user_reactivated" if is_reactivation
        else "partner_user_created"
    )
    reason = (
        f"Self-service partner_user {row['email']} "
        f"{'reactivated' if is_reactivation else 'created'} "
        f"with role={row['role']}"
    )
    failed, bundle_id = await _emit_partner_user_attestation(
        pool, partner_id, event_type,
        actor_email, reason,
        row["id"], row["email"], new_role=row["role"], request=request,
    )

    # Round-table 31 P2 (Maya parity gap): operator-class POST
    # /{partner_id}/users calls log_partner_activity but the new
    # self-scoped endpoints didn't. Closing the parity gap so the
    # human-readable PartnerAuditLog reflects these mutations.
    try:
        from .partner_activity_logger import (
            log_partner_activity, PartnerEventType,
        )
        await log_partner_activity(
            partner_id=partner_id,
            event_type=PartnerEventType.PARTNER_UPDATED,
            target_type="partner_user",
            target_id=row["id"],
            event_data={
                "action": (
                    "partner_user_reactivated" if is_reactivation
                    else "partner_user_self_created"
                ),
                "new_user_email": row["email"],
                "new_user_role": row["role"],
                "actor_email": actor_email,
            },
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            request_path=str(request.url.path),
            request_method=request.method,
        )
    except Exception:
        logger.error("partner_user_self_create_audit_failed", exc_info=True)

    summary_verb = "reactivated" if is_reactivation else "created"
    _partner_user_op_alert(
        event_type, "P2",
        f"Partner-admin self-service {summary_verb} partner_user "
        f"{row['email']} (role={row['role']})",
        {
            "partner_id": partner_id,
            "new_user_id": row["id"],
            "new_user_email": row["email"],
            "new_user_role": row["role"],
            "is_reactivation": is_reactivation,
            "attestation_bundle_id": bundle_id,
        },
        actor_email, f"partner_org:{partner_id}", failed,
    )
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "is_reactivation": is_reactivation,
        "attestation_bundle_id": bundle_id,
    }


@router.patch("/me/users/{user_id}/role")
async def self_change_partner_user_role(
    user_id: str,
    body: PartnerUserRoleUpdate,
    request: Request,
    partner: dict = require_partner_role("admin"),
):
    """Self-scoped role change. The 1-admin-min DB trigger from mig 274
    (trg_enforce_min_one_admin_per_partner) catches the
    last-admin-demote case at the schema level."""
    pool = await get_pool()
    partner_id = str(partner["id"])
    actor_email = (partner.get("email") or "").lower()
    actor_user_id = str(partner.get("partner_user_id") or "")

    if user_id == actor_user_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot change your own role — use the admin transfer flow instead",
        )

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                """
                SELECT id::text, email, role FROM partner_users
                 WHERE id = $1::uuid AND partner_id = $2::uuid
                 FOR UPDATE
                """,
                user_id, partner_id,
            )
            if not target:
                raise HTTPException(status_code=404,
                    detail="Partner user not found in your partner_org")
            if target["role"] == body.role:
                raise HTTPException(status_code=400,
                    detail=f"User already has role {body.role}")
            old_role = target["role"]
            try:
                await conn.execute(
                    """
                    UPDATE partner_users
                       SET role = $1, updated_at = NOW()
                     WHERE id = $2::uuid
                    """,
                    body.role, user_id,
                )
            except Exception as e:
                # The 1-admin-min trigger raises; surface as a
                # user-readable 409 not a 500.
                msg = str(e)
                if "min_one_admin" in msg or "admin" in msg.lower():
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Demoting this user would leave the "
                            "partner_org with zero admins. Promote "
                            "another user to admin first, or use the "
                            "admin transfer flow."
                        ),
                    )
                raise

    failed, bundle_id = await _emit_partner_user_attestation(
        pool, partner_id, "partner_user_role_changed",
        actor_email, body.reason, user_id, target["email"],
        new_role=body.role, request=request,
    )
    # Round-table 31 P2 — log_partner_activity parity gap closure.
    try:
        from .partner_activity_logger import (
            log_partner_activity, PartnerEventType,
        )
        await log_partner_activity(
            partner_id=partner_id,
            event_type=PartnerEventType.PARTNER_UPDATED,
            target_type="partner_user",
            target_id=user_id,
            event_data={
                "action": "partner_user_role_changed",
                "target_email": target["email"],
                "old_role": old_role,
                "new_role": body.role,
                "reason": body.reason,
                "actor_email": actor_email,
            },
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            request_path=str(request.url.path),
            request_method=request.method,
        )
    except Exception:
        logger.error("partner_user_role_change_audit_failed", exc_info=True)

    severity = "P1" if old_role == "admin" or body.role == "admin" else "P2"
    _partner_user_op_alert(
        "partner_user_role_changed", severity,
        f"Partner-admin changed partner_user role: {target['email']} ({old_role}→{body.role})",
        {
            "partner_id": partner_id,
            "target_user_id": user_id,
            "target_email": target["email"],
            "old_role": old_role,
            "new_role": body.role,
            "attestation_bundle_id": bundle_id,
        },
        actor_email, f"partner_org:{partner_id}", failed,
    )
    return {
        "id": user_id,
        "email": target["email"],
        "old_role": old_role,
        "new_role": body.role,
        "attestation_bundle_id": bundle_id,
    }


@router.delete("/me/users/{user_id}")
async def self_deactivate_partner_user(
    user_id: str,
    request: Request,
    body: PartnerUserDeactivate,
    partner: dict = require_partner_role("admin"),
):
    """Self-scoped deactivation (sets status='inactive'). Same
    1-admin-min trigger gate as role-change. Hard-delete is NOT
    available — partner_users carries audit/FK references."""
    if body.confirm_phrase != "DEACTIVATE-PARTNER-USER":
        raise HTTPException(
            status_code=400,
            detail="confirm_phrase must be exactly 'DEACTIVATE-PARTNER-USER'",
        )
    pool = await get_pool()
    partner_id = str(partner["id"])
    actor_email = (partner.get("email") or "").lower()
    actor_user_id = str(partner.get("partner_user_id") or "")

    if user_id == actor_user_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot deactivate your own account",
        )

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                """
                SELECT id::text, email, role, status FROM partner_users
                 WHERE id = $1::uuid AND partner_id = $2::uuid
                 FOR UPDATE
                """,
                user_id, partner_id,
            )
            if not target:
                raise HTTPException(status_code=404,
                    detail="Partner user not found in your partner_org")
            if target["status"] == "inactive":
                raise HTTPException(status_code=400,
                    detail="User already inactive")
            # Round-table 31 P1 race-guard (2026-05-05): if this user
            # is the initiator OR target of a pending admin-transfer,
            # the partial unique index `idx_partner_admin_transfer_one_pending`
            # would be permanently locked because the deactivate kills
            # the user's session but doesn't expire the transfer row.
            # Refuse 409 with operator-actionable detail.
            pending = await conn.fetchval(
                """
                SELECT 1 FROM partner_admin_transfer_requests
                 WHERE partner_id = $1::uuid
                   AND status = 'pending_target_accept'
                   AND (initiated_by_user_id = $2::uuid
                        OR target_user_id = $2::uuid)
                 LIMIT 1
                """,
                partner_id, user_id,
            )
            if pending:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Cannot deactivate: a pending admin-transfer "
                        "request involves this user. Cancel or accept "
                        "the transfer first."
                    ),
                )
            try:
                await conn.execute(
                    """
                    UPDATE partner_users
                       SET status = 'inactive', updated_at = NOW()
                     WHERE id = $1::uuid
                    """,
                    user_id,
                )
            except Exception as e:
                msg = str(e)
                if "min_one_admin" in msg or "admin" in msg.lower():
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Deactivating this user would leave the "
                            "partner_org with zero admins. Promote "
                            "another user to admin first."
                        ),
                    )
                raise
            # Kill any active sessions for that user.
            await conn.execute(
                """
                DELETE FROM partner_sessions
                 WHERE partner_user_id = $1::uuid
                """,
                user_id,
            )

    failed, bundle_id = await _emit_partner_user_attestation(
        pool, partner_id, "partner_user_deactivated",
        actor_email, body.reason, user_id, target["email"],
        new_role=None, request=request,
    )
    # Round-table 31 P2 — log_partner_activity parity gap closure.
    try:
        from .partner_activity_logger import (
            log_partner_activity, PartnerEventType,
        )
        await log_partner_activity(
            partner_id=partner_id,
            event_type=PartnerEventType.PARTNER_UPDATED,
            target_type="partner_user",
            target_id=user_id,
            event_data={
                "action": "partner_user_deactivated",
                "target_email": target["email"],
                "former_role": target["role"],
                "reason": body.reason,
                "actor_email": actor_email,
            },
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            request_path=str(request.url.path),
            request_method=request.method,
        )
    except Exception:
        logger.error("partner_user_deactivate_audit_failed", exc_info=True)

    severity = "P1" if target["role"] == "admin" else "P2"
    _partner_user_op_alert(
        "partner_user_deactivated", severity,
        f"Partner-admin deactivated partner_user {target['email']} (was {target['role']})",
        {
            "partner_id": partner_id,
            "target_user_id": user_id,
            "target_email": target["email"],
            "former_role": target["role"],
            "attestation_bundle_id": bundle_id,
        },
        actor_email, f"partner_org:{partner_id}", failed,
    )
    return {
        "id": user_id,
        "email": target["email"],
        "status": "inactive",
        "attestation_bundle_id": bundle_id,
    }


@router.get("/me")
async def get_my_partner(request: Request, partner: dict = require_partner_role("admin", "tech", "billing")):
    """Get current partner's info (self-service)."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        row = await conn.fetchrow("""
            SELECT id, name, slug, contact_email, contact_phone,
                   brand_name, logo_url, primary_color,
                   revenue_share_percent, status, created_at
            FROM partners
            WHERE id = $1
        """, partner['id'])

        # Get site count
        site_count = await conn.fetchval("""
            SELECT COUNT(*) FROM sites WHERE partner_id = $1
        """, partner['id'])

        # Get provision stats
        provision_stats = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'pending') as pending,
                COUNT(*) FILTER (WHERE status = 'claimed') as claimed
            FROM appliance_provisions
            WHERE partner_id = $1
        """, partner['id'])

        await log_partner_activity(
            partner_id=str(partner['id']),
            event_type=PartnerEventType.PROFILE_VIEWED,
            target_type="partner",
            target_id=str(partner['id']),
            event_data={"partner_name": partner['name']},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            request_path=str(request.url.path),
            request_method=request.method,
        )

        return {
            'id': str(row['id']),
            'name': row['name'],
            'slug': row['slug'],
            'contact_email': row['contact_email'],
            'contact_phone': row['contact_phone'],
            'brand_name': row['brand_name'],
            'logo_url': row['logo_url'],
            'primary_color': row['primary_color'],
            'revenue_share_percent': row['revenue_share_percent'],
            'status': row['status'],
            'site_count': site_count,
            'provisions': {
                'pending': provision_stats['pending'] if provision_stats else 0,
                'claimed': provision_stats['claimed'] if provision_stats else 0,
            },
            'created_at': row['created_at'].isoformat(),
            # MAJ-2 fix (audit 2026-05-08): expose the caller's
            # partner_user role + id so PartnerAdminTransferModal
            # (Session 216) can defensively gate the initiate form
            # on the client side. Backend gates remain authoritative
            # (require_partner_role("admin")); this is UX defense
            # in depth — non-admins should not see a form that the
            # server will reject.
            'user_role': partner.get('user_role'),
            'partner_user_id': partner.get('partner_user_id'),
        }


# ----------------------------------------------------------------------
# Partner fleet appliance view (RT33 P0, 2026-05-05)
# ----------------------------------------------------------------------
# Cross-site fleet-wide appliance roll-up. Solves the "200 sites in
# the book, can't answer 'which appliances are offline' without
# clicking each one" problem flagged by Linda in RT33.
#
# Partner-class is operator-class so the field set is broader than the
# client portal: includes mac_address + l2_mode for fleet ops context.
# Still READ-ONLY — operator-class mutations (toggle l2_mode, fleet
# orders, clear-stale) live on central command, not the partner portal.
# Pinned by `tests/test_partner_fleet_appliances_no_mutation`.
@router.get("/me/appliances")
async def get_my_fleet_appliances(
    cursor: str = "",
    limit: int = 50,
    status_filter: str = "",
    site_id_filter: str = "",
    partner: dict = require_partner_role("admin", "tech", "billing"),
):
    """Fleet appliance view across all sites under this partner.

    Cursor pagination ASC by appliance_id, hard cap 100.
    Optional server-side filters: status, site_id.
    """
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 100",
        )
    if status_filter and status_filter not in ("online", "stale", "offline"):
        raise HTTPException(
            status_code=400,
            detail="status_filter must be 'online', 'stale', or 'offline'",
        )
    pool = await get_pool()

    # Single-query CTE shape (Dana DBA fix from RT33 P3 review):
    # The base CTE computes the per-appliance live_status ONCE via the
    # LATERAL heartbeat join. The summary aggregates over the entire
    # base set (unfiltered) and is attached to every row. Pagination /
    # status_filter / site_filter apply only to the outer SELECT, so
    # filtered views still see the un-filtered total/online/offline KPI.
    # Replaces the prior two-query pattern that scanned heartbeats twice.
    async with admin_connection(pool) as conn:
        rows = await conn.fetch(
            """
            WITH fleet AS (
                SELECT sa.appliance_id,
                       sa.site_id,
                       COALESCE(sa.display_name, sa.hostname, sa.appliance_id) AS display_name,
                       sa.mac_address,
                       sa.agent_version,
                       sa.l2_mode,
                       sa.last_checkin,
                       hb.max_observed_at AS last_heartbeat_at,
                       s.clinic_name,
                       CASE
                           WHEN hb.max_observed_at IS NULL THEN 'offline'
                           WHEN hb.max_observed_at > NOW() - INTERVAL '90 seconds' THEN 'online'
                           WHEN hb.max_observed_at > NOW() - INTERVAL '5 minutes' THEN 'stale'
                           ELSE 'offline'
                       END AS status
                FROM site_appliances sa
                JOIN sites s ON s.site_id = sa.site_id AND sa.deleted_at IS NULL
                LEFT JOIN LATERAL (
                    SELECT MAX(observed_at) AS max_observed_at
                    FROM appliance_heartbeats
                    WHERE appliance_id = sa.appliance_id
                      AND observed_at > NOW() - INTERVAL '24 hours'
                ) hb ON true
                WHERE s.partner_id = $1 AND s.status != 'inactive'
            )
            SELECT f.*,
                   (SELECT COUNT(*) FROM fleet) AS _total,
                   (SELECT COUNT(*) FROM fleet WHERE status = 'online') AS _online,
                   (SELECT COUNT(*) FROM fleet WHERE status IN ('offline','stale')) AS _offline
            FROM fleet f
            WHERE ($2 = '' OR f.appliance_id > $2)
              AND ($3 = '' OR f.site_id = $3)
              AND ($4 = '' OR f.status = $4)
            ORDER BY f.appliance_id ASC
            LIMIT $5
            """,
            partner['id'], cursor, site_id_filter, status_filter, limit + 1,
        )

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = rows[-1]["appliance_id"] if (has_more and rows) else None

    # Summary attached to every row by the CTE; pull from row[0] or
    # zero-default when the result set is empty so the partner still
    # sees a "0/0" KPI banner instead of nothing.
    summary = (
        {
            "total": rows[0]["_total"],
            "online": rows[0]["_online"],
            "offline": rows[0]["_offline"],
        }
        if rows
        else {"total": 0, "online": 0, "offline": 0}
    )

    return {
        "appliances": [
            {
                "appliance_id": r["appliance_id"],
                "site_id": r["site_id"],
                "site_name": r["clinic_name"],
                "display_name": r["display_name"],
                "mac_address": r["mac_address"],
                "agent_version": r["agent_version"],
                "l2_mode": r["l2_mode"],
                "status": r["status"],
                "last_heartbeat_at": (
                    r["last_heartbeat_at"].isoformat()
                    if r["last_heartbeat_at"] else None
                ),
                "last_checkin": (
                    r["last_checkin"].isoformat()
                    if r["last_checkin"] else None
                ),
            }
            for r in rows
        ],
        "summary": summary,
        "next_cursor": next_cursor,
        "limit": limit,
    }


@router.get("/me/sites")
async def get_my_sites(request: Request, partner: dict = require_partner_role("admin", "tech", "billing")):
    """Get sites belonging to this partner."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        rows = await conn.fetch("""
            SELECT s.site_id, s.clinic_name, s.status, s.tier,
                   s.onboarding_stage, s.created_at,
                   COUNT(DISTINCT sa.id) as appliance_count,
                   MAX(sa.last_checkin) as last_checkin,
                   COALESCE(gas.total_agents, 0) as agent_count,
                   COALESCE(gas.overall_compliance_rate, 0) as agent_compliance_rate,
                   gas.last_event as agent_last_event
            FROM sites s
            LEFT JOIN site_appliances sa ON s.site_id = sa.site_id AND sa.deleted_at IS NULL
            LEFT JOIN site_go_agent_summaries gas ON s.site_id = gas.site_id
            WHERE s.partner_id = $1
              AND s.status != 'inactive'
            GROUP BY s.site_id, s.clinic_name, s.status, s.tier,
                     s.onboarding_stage, s.created_at,
                     gas.total_agents, gas.overall_compliance_rate, gas.last_event
            ORDER BY s.clinic_name
        """, partner['id'])

        sites = []
        for row in rows:
            sites.append({
                'site_id': row['site_id'],
                'clinic_name': row['clinic_name'],
                'status': row['status'],
                'tier': row['tier'],
                'onboarding_stage': row['onboarding_stage'],
                'appliance_count': row['appliance_count'],
                'last_checkin': row['last_checkin'].isoformat() if row['last_checkin'] else None,
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                'agent_count': row['agent_count'],
                'agent_compliance_rate': float(row['agent_compliance_rate']),
                'agent_last_event': row['agent_last_event'].isoformat() if row['agent_last_event'] else None,
            })

        await log_partner_activity(
            partner_id=str(partner['id']),
            event_type=PartnerEventType.SITES_LISTED,
            target_type="partner",
            target_id=str(partner['id']),
            event_data={"site_count": len(sites)},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            request_path=str(request.url.path),
            request_method=request.method,
        )

        return {'sites': sites, 'count': len(sites)}


@router.get("/me/dashboard")
async def get_partner_dashboard(
    request: Request,
    partner: dict = require_partner_role("admin", "tech", "billing"),
):
    """Partner portal HERO dashboard (Session 206 round-table P0).

    Different audience, different psychology than the client portal:
      - Partner is time-starved, technical, defensively-informed
      - Wants to know which clients need attention THIS WEEK
      - Wants to see their own work attributed back to them
      - Trust-breaker if we ever leak another partner's sites

    Response:
      * attention_list: top N sites ranked by risk (chronic +
        unack'd ack + open L3 + mesh unhealthy). Max 10.
      * activity_24h: flat event stream across all this partner's
        sites, 30 most recent. Each event has actor attribution —
        system OR a named partner user.
      * book_of_business: self_heal_rate, total_clients, active_alerts
        across entire roster.
      * trend_7d: daily self_heal_rate across all clients.

    MUST enforce partner_id isolation — cross-partner leakage is a
    trust-ending incident. See test_cross_partner_isolation.
    """
    pool = await get_pool()
    partner_id = partner["id"]

    # admin_transaction (wave-10): 4 admin reads — pin to one PgBouncer backend.
    async with admin_transaction(pool) as conn:
        # ─── Attention list: risk-ordered ──────────────────────
        # Score per site: chronic_patterns*3 + open_l3*5 + ack_pending*2
        # + appliance_offline*4. Top 10.
        attention_rows = await conn.fetch(
            """
            WITH site_scope AS (
                SELECT site_id, clinic_name
                FROM sites WHERE partner_id = $1 AND status != 'inactive'
            ),
            risk_agg AS (
                SELECT
                    ss.site_id,
                    ss.clinic_name,
                    COALESCE((
                        SELECT COUNT(*) FROM incident_recurrence_velocity v
                        WHERE v.site_id = ss.site_id AND v.is_chronic = TRUE
                    ), 0) AS chronic,
                    COALESCE((
                        SELECT COUNT(*) FROM incidents i
                        WHERE i.site_id = ss.site_id
                          AND i.status NOT IN ('resolved', 'closed')
                          AND i.resolution_tier = 'L3'
                    ), 0) AS open_l3,
                    COALESCE((
                        SELECT COUNT(*) FROM promoted_rules pr
                        WHERE pr.site_id = ss.site_id
                          AND pr.operator_ack_required = TRUE
                          AND pr.operator_ack_at IS NULL
                    ), 0) AS ack_pending,
                    COALESCE((
                        SELECT COUNT(*) FROM site_appliances sa
                        WHERE sa.site_id = ss.site_id
                          AND sa.deleted_at IS NULL
                          AND (sa.last_checkin IS NULL OR sa.last_checkin < NOW() - INTERVAL '15 minutes')
                    ), 0) AS offline_appliances
                FROM site_scope ss
            )
            SELECT site_id, clinic_name, chronic, open_l3, ack_pending, offline_appliances,
                   (chronic * 3 + open_l3 * 5 + ack_pending * 2 + offline_appliances * 4) AS risk_score
            FROM risk_agg
            WHERE (chronic + open_l3 + ack_pending + offline_appliances) > 0
            ORDER BY risk_score DESC, clinic_name ASC
            LIMIT 10
            """,
            partner_id,
        )
        attention_list = [
            {
                "site_id": r["site_id"],
                "clinic_name": r["clinic_name"],
                "risk_score": int(r["risk_score"]),
                "chronic_patterns": int(r["chronic"]),
                "open_l3": int(r["open_l3"]),
                "ack_pending": int(r["ack_pending"]),
                "offline_appliances": int(r["offline_appliances"]),
            }
            for r in attention_rows
        ]

        # ─── Activity feed: 30 most recent across the partner's sites
        activity_rows = await conn.fetch(
            """
            SELECT i.created_at, i.site_id, i.incident_type,
                   i.severity, i.resolution_tier, i.status,
                   s.clinic_name
            FROM incidents i
            JOIN sites s ON s.site_id = i.site_id
            WHERE s.partner_id = $1
              AND i.created_at > NOW() - INTERVAL '24 hours'
            ORDER BY i.created_at DESC
            LIMIT 30
            """,
            partner_id,
        )
        activity_24h = [
            {
                "when": r["created_at"].isoformat() if r["created_at"] else None,
                "site_id": r["site_id"],
                "clinic_name": r["clinic_name"],
                "incident_type": r["incident_type"],
                "severity": r["severity"],
                "resolution_tier": r["resolution_tier"],
                "status": r["status"],
            }
            for r in activity_rows
        ]

        # ─── Book-of-business rollup: self-heal rate across all client sites
        bob = await conn.fetchrow(
            """
            SELECT COUNT(DISTINCT s.site_id) AS total_clients,
                   COUNT(DISTINCT s.site_id) FILTER (
                     WHERE sa.last_checkin > NOW() - INTERVAL '15 minutes'
                   ) AS clients_online_now,
                   COUNT(i.id) AS incidents_24h,
                   COUNT(i.id) FILTER (WHERE i.resolution_tier = 'L1') AS l1_24h,
                   COUNT(i.id) FILTER (WHERE i.resolution_tier = 'L2') AS l2_24h,
                   COUNT(i.id) FILTER (WHERE i.resolution_tier = 'L3') AS l3_24h
            FROM sites s
            LEFT JOIN site_appliances sa ON sa.site_id = s.site_id AND sa.deleted_at IS NULL
            LEFT JOIN incidents i ON i.site_id = s.site_id
                                 AND i.created_at > NOW() - INTERVAL '24 hours'
            WHERE s.partner_id = $1 AND s.status != 'inactive'
            """,
            partner_id,
        )
        total_incidents_24h = int(bob["incidents_24h"] or 0) if bob else 0
        l1_24h = int(bob["l1_24h"] or 0) if bob else 0
        # None when total==0: the self-heal rate is undefined with zero
        # incidents; emitting 100 would mean "every drift was auto-healed"
        # when nothing was observed. Frontend renders the empty state.
        self_heal_24h_pct = (
            round(100.0 * l1_24h / total_incidents_24h, 1)
            if total_incidents_24h > 0 else None
        )
        book_of_business = {
            "total_clients": int(bob["total_clients"] or 0) if bob else 0,
            "clients_online_now": int(bob["clients_online_now"] or 0) if bob else 0,
            "incidents_24h": total_incidents_24h,
            "l1_24h": l1_24h,
            "l2_24h": int(bob["l2_24h"] or 0) if bob else 0,
            "l3_24h": int(bob["l3_24h"] or 0) if bob else 0,
            "self_heal_24h_pct": self_heal_24h_pct,
            "active_alerts": len(attention_list),
        }

        # ─── 7-day trend: daily self-heal rate across all partner sites
        trend_rows = await conn.fetch(
            """
            SELECT DATE_TRUNC('day', i.created_at) AS day,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE i.resolution_tier = 'L1') AS l1
            FROM incidents i
            JOIN sites s ON s.site_id = i.site_id
            WHERE s.partner_id = $1
              AND i.created_at > NOW() - INTERVAL '7 days'
            GROUP BY 1 ORDER BY 1 ASC
            """,
            partner_id,
        )
        trend_7d = [
            {
                "date": r["day"].date().isoformat() if r["day"] else None,
                "total": int(r["total"] or 0),
                "l1": int(r["l1"] or 0),
                "pct": (
                    round(100.0 * int(r["l1"] or 0) / int(r["total"]), 1)
                    if r["total"] and int(r["total"]) > 0 else None
                ),
            }
            for r in trend_rows
        ]

    # Audit log entry — every partner dashboard pull is a partner_activity row
    try:
        await log_partner_activity(
            partner_id=str(partner_id),
            event_type=PartnerEventType.DASHBOARD_VIEWED
                if hasattr(PartnerEventType, "DASHBOARD_VIEWED")
                else PartnerEventType.SITES_LISTED,
            target_type="partner",
            target_id=str(partner_id),
            event_data={
                "total_clients": book_of_business["total_clients"],
                "active_alerts": book_of_business["active_alerts"],
            },
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            request_path=str(request.url.path),
            request_method=request.method,
        )
    except Exception:
        pass  # audit failure must not block dashboard

    return {
        "attention_list": attention_list,
        "activity_24h": activity_24h,
        "book_of_business": book_of_business,
        "trend_7d": trend_7d,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/me/search")
async def partner_global_search(
    q: str,
    limit: int = 12,
    partner: dict = require_partner_role("admin", "tech", "billing"),
):
    """Cmd-K omnibox — fuzzy search across the partner's book of business.

    Scopes to sites owned by this partner (partner_id isolation — same
    contract as /me/dashboard; see test_partner_dashboard_isolation).

    Searches:
      * sites (site_id, clinic_name)
      * incidents (incident_type, severity) — limited to last 7d for speed
      * promoted_rules (rule_id) — partner's sites only

    Returns a flat list of hits, each with `kind`, `title`, `subtitle`,
    and a frontend-navigable `href`.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return {"hits": [], "query": q}
    limit = max(1, min(limit, 25))
    pattern = f"%{q.lower()}%"
    partner_id = partner["id"]

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        site_rows = await conn.fetch(
            """
            SELECT site_id, clinic_name
            FROM sites
            WHERE partner_id = $1
              AND status != 'inactive'
              AND (LOWER(site_id) LIKE $2 OR LOWER(COALESCE(clinic_name, '')) LIKE $2)
            ORDER BY clinic_name ASC NULLS LAST, site_id ASC
            LIMIT $3
            """,
            partner_id, pattern, limit,
        )
        incident_rows = await conn.fetch(
            """
            SELECT i.id, i.incident_type, i.severity, i.status,
                   i.site_id, s.clinic_name, i.created_at
            FROM incidents i
            JOIN sites s ON s.site_id = i.site_id
            WHERE s.partner_id = $1
              AND i.created_at > NOW() - INTERVAL '7 days'
              AND (LOWER(i.incident_type) LIKE $2 OR LOWER(COALESCE(i.severity, '')) LIKE $2)
            ORDER BY i.created_at DESC
            LIMIT $3
            """,
            partner_id, pattern, limit,
        )
        rule_rows = await conn.fetch(
            """
            SELECT pr.rule_id, pr.site_id, pr.lifecycle_state, s.clinic_name
            FROM promoted_rules pr
            JOIN sites s ON s.site_id = pr.site_id
            WHERE s.partner_id = $1
              AND LOWER(pr.rule_id) LIKE $2
            ORDER BY pr.created_at DESC NULLS LAST
            LIMIT $3
            """,
            partner_id, pattern, limit,
        )

    hits = []
    for r in site_rows:
        hits.append({
            "kind": "site",
            "title": r["clinic_name"] or r["site_id"],
            "subtitle": r["site_id"],
            "href": f"/partner/site/{r['site_id']}",
        })
    for r in incident_rows:
        hits.append({
            "kind": "incident",
            "title": r["incident_type"] or "incident",
            "subtitle": (
                f"{r['clinic_name'] or r['site_id']} · {r['severity'] or 'n/a'} · "
                f"{r['status']} · {r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else ''}"
            ),
            "href": f"/partner/site/{r['site_id']}?incident={r['id']}",
        })
    for r in rule_rows:
        hits.append({
            "kind": "rule",
            "title": r["rule_id"],
            "subtitle": f"{r['clinic_name'] or r['site_id']} · {r['lifecycle_state'] or 'n/a'}",
            "href": f"/partner/site/{r['site_id']}?rule={r['rule_id']}",
        })

    return {"hits": hits[: limit * 3], "query": q}


@router.get("/me/rollup/weekly")
async def get_partner_weekly_rollup(
    partner: dict = require_partner_role("admin", "tech", "billing"),
):
    """Session 206 round-table P2 — precomputed weekly rollup per site.

    Reads from the `partner_site_weekly_rollup` materialized view
    (migration 185, refreshed every 30 min by weekly_rollup_refresh_loop).
    Filters server-side by partner_id. Single indexed read vs. 7
    aggregate queries in /me/dashboard.

    Returns {sites: [...], computed_at: ts, total_sites: N}.
    Empty sites[] if the view doesn't exist yet (pre-migration).
    """
    pool = await get_pool()
    partner_id = partner["id"]

    async with admin_connection(pool) as conn:
        # Guard against pre-migration deploys — endpoint shouldn't 500 if
        # the view hasn't been created yet. Pick the latest computed_at
        # across partner's sites as the rollup's logical timestamp.
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_matviews WHERE matviewname = 'partner_site_weekly_rollup'"
        )
        if not exists:
            return {"sites": [], "computed_at": None, "total_sites": 0, "stale": True}

        rows = await conn.fetch(
            """
            SELECT site_id, clinic_name,
                   incidents_7d, l1_7d, l2_7d, l3_7d,
                   incidents_24h, l1_24h,
                   self_heal_rate_7d_pct, computed_at
            FROM partner_site_weekly_rollup
            WHERE partner_id = $1
            ORDER BY self_heal_rate_7d_pct ASC NULLS LAST, incidents_7d DESC
            """,
            partner_id,
        )

    sites = [
        {
            "site_id": r["site_id"],
            "clinic_name": r["clinic_name"],
            "incidents_7d": int(r["incidents_7d"] or 0),
            "l1_7d": int(r["l1_7d"] or 0),
            "l2_7d": int(r["l2_7d"] or 0),
            "l3_7d": int(r["l3_7d"] or 0),
            "incidents_24h": int(r["incidents_24h"] or 0),
            "l1_24h": int(r["l1_24h"] or 0),
            "self_heal_rate_7d_pct": float(r["self_heal_rate_7d_pct"] or 100.0),
        }
        for r in rows
    ]
    computed_at = rows[0]["computed_at"].isoformat() if rows and rows[0]["computed_at"] else None
    return {"sites": sites, "computed_at": computed_at, "total_sites": len(sites), "stale": False}


@router.get("/me/sites/{site_id}/consent")
async def get_partner_site_consents(
    site_id: str,
    partner: dict = require_partner_role("admin", "tech", "billing"),
):
    """Session 206 Migration 184 Phase 2 — partner view of consent state.

    Read-only for partners; only the client can grant or revoke.
    Shows which classes are covered by active consent + which are
    exposed (no consent = L1/L2 will skip-and-log in shadow mode,
    block in enforce).

    Scoped to partner_id at SQL layer (same isolation contract as
    /me/dashboard).
    """
    pool = await get_pool()
    partner_id = partner["id"]

    async with admin_connection(pool) as conn:
        # Ownership check.
        own = await conn.fetchval(
            "SELECT 1 FROM sites WHERE site_id = $1 AND partner_id = $2",
            site_id, partner_id,
        )
        if not own:
            raise HTTPException(status_code=404, detail="Site not in your book of business")

        try:
            class_rows = await conn.fetch(
                """
                SELECT class_id, display_name, risk_level, hipaa_controls
                FROM runbook_classes
                ORDER BY risk_level, class_id
                """,
            )
        except Exception:
            class_rows = []

        try:
            consent_rows = await conn.fetch(
                """
                SELECT consent_id, class_id, consented_by_email, consented_at,
                       consent_ttl_days, revoked_at,
                       (consented_at + (consent_ttl_days || ' days')::INTERVAL) AS expires_at
                FROM runbook_class_consent
                WHERE site_id = $1
                ORDER BY consented_at DESC
                """,
                site_id,
            )
        except Exception:
            consent_rows = []

    active_by_class: dict = {}
    for r in consent_rows:
        if r["revoked_at"] is None and (r["expires_at"] is None or r["expires_at"] > datetime.now(timezone.utc)):
            active_by_class[r["class_id"]] = {
                "consent_id": str(r["consent_id"]),
                "consented_by_email": r["consented_by_email"],
                "consented_at": r["consented_at"].isoformat() if r["consented_at"] else None,
                "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
            }

    classes = [
        {
            "class_id": r["class_id"],
            "display_name": r["display_name"],
            "risk_level": r["risk_level"],
            "hipaa_controls": list(r["hipaa_controls"] or []),
            "active_consent": active_by_class.get(r["class_id"]),
        }
        for r in class_rows
    ]
    covered = sum(1 for c in classes if c["active_consent"])
    return {
        "site_id": site_id,
        "classes": classes,
        "total_classes": len(classes),
        "covered_classes": covered,
        "coverage_pct": round(100.0 * covered / len(classes), 1) if classes else 0.0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/me/sites/{site_id}/consent/request")
async def partner_consent_request(
    site_id: str,
    body: dict,
    request: Request,
    partner: dict = require_partner_role("admin", "tech", "billing"),
):
    """Phase 4 — partner asks the client to approve a class-level consent.

    Generates a single-use token (raw → SHA256 at rest), inserts a row
    in `consent_request_tokens` with 72h expiry, and emails the raw
    token to the specified customer email. The client visits the portal
    via a magic-link URL with the token and approves.

    Body: `{class_id, requested_for_email, ttl_days?}`.

    Partners CANNOT grant consent themselves — only request it.
    """
    class_id = (body.get("class_id") or "").strip()
    for_email = (body.get("requested_for_email") or "").strip()
    ttl_days = int(body.get("ttl_days") or 365)
    partner_id = partner["id"]
    partner_email = (partner.get("email") or partner.get("contact_email") or "").strip()

    if not class_id:
        raise HTTPException(status_code=400, detail="class_id required")
    if "@" not in for_email:
        raise HTTPException(status_code=400, detail="requested_for_email must be valid")
    if ttl_days < 30 or ttl_days > 3650:
        raise HTTPException(status_code=400, detail="ttl_days must be 30..3650")
    if not partner_email or "@" not in partner_email:
        raise HTTPException(status_code=400, detail="partner contact_email must be set to request consent")

    pool = await get_pool()
    # admin_transaction (wave-5): 5 admin reads/writes must pin to one PgBouncer backend.
    async with admin_transaction(pool) as conn:
        # Site ownership — same contract as the rest of /me/sites/{id}
        own = await conn.fetchval(
            "SELECT 1 FROM sites WHERE site_id = $1 AND partner_id = $2",
            site_id, partner_id,
        )
        if not own:
            raise HTTPException(status_code=404, detail="Site not in your book of business")

        klass = await conn.fetchrow(
            """SELECT class_id, display_name, description, risk_level
               FROM runbook_classes WHERE class_id = $1""",
            class_id,
        )
        if not klass:
            raise HTTPException(status_code=400, detail=f"Unknown class_id {class_id!r}")

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=72)

        # Reject duplicate active (unconsumed, non-expired) requests for
        # the same (site_id, class_id, for_email) triple — prevents
        # a partner from spamming the customer.
        dup = await conn.fetchval(
            """
            SELECT 1 FROM consent_request_tokens
            WHERE site_id = $1 AND class_id = $2 AND requested_for_email = $3
              AND consumed_at IS NULL AND expires_at > NOW()
            """,
            site_id, class_id, for_email,
        )
        if dup:
            raise HTTPException(
                status_code=409,
                detail="An active consent request already exists for this site+class+email — wait for expiry or cancel",
            )

        await conn.execute(
            """
            INSERT INTO consent_request_tokens
                (token_hash, site_id, class_id,
                 requested_by_email, requested_for_email,
                 requested_ttl_days, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            token_hash, site_id, class_id,
            partner_email, for_email, ttl_days, expires_at,
        )

        # Fetch partner branding for the email body
        p_brand = await conn.fetchrow(
            """SELECT COALESCE(NULLIF(brand_name,''), name, 'OsirisCare') AS brand_name,
                      logo_url, COALESCE(primary_color, '#4F46E5') AS primary_color
               FROM partners WHERE id = $1""",
            partner_id,
        )

    # Send the email (best-effort — don't fail the API if SMTP is down;
    # the token is persisted and the partner can resend).
    try:
        from dashboard_api.email_alerts import send_consent_request_email
        send_consent_request_email(
            to_email=for_email,
            raw_token=raw_token,
            site_id=site_id,
            class_display_name=klass["display_name"],
            class_description=klass["description"],
            class_risk_level=klass["risk_level"],
            partner_brand=p_brand["brand_name"] if p_brand else "OsirisCare",
            partner_logo_url=p_brand["logo_url"] if p_brand else None,
            primary_color=p_brand["primary_color"] if p_brand else "#4F46E5",
            partner_contact_email=partner_email,
            ttl_days=ttl_days,
        )
    except Exception:
        logger.exception(f"consent request email send failed for {for_email} / {site_id}/{class_id}")

    return {
        "ok": True,
        "site_id": site_id,
        "class_id": class_id,
        "expires_at": expires_at.isoformat(),
        "requested_for_email": for_email,
    }


@router.get("/me/sites/{site_id}/topology")
async def get_partner_site_topology(
    site_id: str,
    partner: dict = require_partner_role("admin", "tech", "billing"),
):
    """Session 206 round-table P3 — mesh topology for one site.

    Returns site + all appliances (with status/last_checkin/display_name)
    + discovered devices (targets) + a per-appliance target-count
    computed by replaying the hash ring the same way the checkin flow
    does. Frontend renders this as a grid/radial visualization.

    Scope: partner_id enforced on sites row (same contract as
    /me/dashboard + /me/search).
    """
    pool = await get_pool()
    partner_id = partner["id"]

    async with admin_connection(pool) as conn:
        site = await conn.fetchrow(
            """
            SELECT s.site_id, s.clinic_name
            FROM sites s
            WHERE s.site_id = $1 AND s.partner_id = $2 AND s.status != 'inactive'
            """,
            site_id, partner_id,
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not in your book of business")

        appliances = await conn.fetch(
            """
            SELECT appliance_id, hostname, display_name, mac_address, status,
                   last_checkin, agent_version
            FROM site_appliances
            WHERE site_id = $1 AND deleted_at IS NULL
            ORDER BY hostname NULLS LAST, appliance_id
            """,
            site_id,
        )
        devices = await conn.fetch(
            """
            SELECT id, hostname, ip_address, device_type, last_seen,
                   device_status, owner_appliance_id
            FROM discovered_devices
            WHERE site_id = $1
            ORDER BY ip_address, hostname
            LIMIT 200
            """,
            site_id,
        )

    # Replay the hash ring across currently-online appliances. If only one
    # appliance is online it scans everything; if multiple, the ring splits
    # the target set deterministically.
    try:
        from dashboard_api.hash_ring import HashRing, normalize_mac_for_ring
    except Exception:  # pragma: no cover — import guard only
        HashRing = None
        normalize_mac_for_ring = None

    online_macs = [
        normalize_mac_for_ring(a["mac_address"])
        for a in appliances
        if a["status"] == "online" and a["mac_address"] and normalize_mac_for_ring
    ]
    target_ips = sorted(
        d["ip_address"] for d in devices if d["ip_address"]
    )
    mac_to_targets: dict = {}
    if HashRing and online_macs:
        ring = HashRing(online_macs)
        for mac in online_macs:
            mac_to_targets[mac] = set(ring.targets_for_node(mac, target_ips))

    appliance_view = []
    for a in appliances:
        norm = normalize_mac_for_ring(a["mac_address"]) if a["mac_address"] and normalize_mac_for_ring else ""
        scan_count = len(mac_to_targets.get(norm, ())) if a["status"] == "online" else 0
        appliance_view.append({
            "appliance_id": a["appliance_id"],
            "hostname": a["hostname"],
            "display_name": a["display_name"] or a["hostname"],
            "mac_address": a["mac_address"],
            "status": a["status"],
            "agent_version": a["agent_version"],
            "last_checkin": a["last_checkin"].isoformat() if a["last_checkin"] else None,
            "scan_target_count": scan_count,
        })

    # Group devices by the appliance they're assigned to (hash ring result).
    # If no online appliances, `assigned_to` is None.
    ip_to_mac = {}
    for mac, targets in mac_to_targets.items():
        for t in targets:
            ip_to_mac[t] = mac

    device_view = []
    for d in devices:
        assigned_mac = ip_to_mac.get(d["ip_address"]) if d["ip_address"] else None
        device_view.append({
            "id": d["id"],
            "hostname": d["hostname"],
            "ip_address": d["ip_address"],
            "device_type": d["device_type"],
            "device_status": d["device_status"],
            "last_seen": d["last_seen"].isoformat() if d["last_seen"] else None,
            "assigned_mac": assigned_mac,
            "owner_appliance_id": str(d["owner_appliance_id"]) if d["owner_appliance_id"] else None,
        })

    return {
        "site_id": site["site_id"],
        "clinic_name": site["clinic_name"],
        "appliances": appliance_view,
        "devices": device_view,
        "online_appliance_count": len(online_macs),
        "total_appliance_count": len(appliances),
        "total_devices": len(devices),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/me/digest-prefs")
async def get_partner_digest_prefs(
    partner: dict = require_partner_role("admin", "tech", "billing"),
):
    """Return whether the partner is opted into the weekly digest."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            "SELECT COALESCE(digest_enabled, TRUE) AS digest_enabled, "
            "contact_email FROM partners WHERE id = $1",
            partner["id"],
        )
    return {
        "digest_enabled": bool(row["digest_enabled"]) if row else True,
        "contact_email": row["contact_email"] if row else None,
    }


@router.put("/me/digest-prefs")
async def set_partner_digest_prefs(
    body: dict,
    request: Request,
    partner: dict = require_partner_role("admin", "tech", "billing"),
):
    """Toggle the weekly digest on/off for this partner."""
    enabled = bool(body.get("enabled", True))
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE partners SET digest_enabled = $1 WHERE id = $2",
                enabled, partner["id"],
            )
    # Maya parity finding 2026-05-04 — digest-prefs mutation was
    # previously inert on the audit trail. log_partner_activity is
    # fire-and-forget per the helper contract.
    try:
        from .partner_activity_logger import (
            log_partner_activity, PartnerEventType,
        )
        await log_partner_activity(
            partner_id=str(partner["id"]),
            event_type=PartnerEventType.PARTNER_UPDATED,
            target_type="partner",
            target_id=str(partner["id"]),
            event_data={"field": "digest_enabled",
                        "new_value": enabled,
                        "actor_email": partner.get("email") or "unknown"},
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            request_path=str(request.url.path),
            request_method=request.method,
        )
    except Exception:
        logger.error("partner_digest_prefs_audit_failed", exc_info=True)
    return {"ok": True, "digest_enabled": enabled}


@router.get("/me/digest/preview")
async def preview_partner_digest(
    partner: dict = require_partner_role("admin", "tech", "billing"),
):
    """Render a preview of this week's digest (HTML, for a partner to
    see what their Friday email will look like). Doesn't send mail.
    """
    from fastapi.responses import HTMLResponse
    from dashboard_api.background_tasks import _gather_partner_digest_data

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        payload = await _gather_partner_digest_data(conn, partner["id"])
        p = await conn.fetchrow(
            """SELECT COALESCE(NULLIF(brand_name, ''), name, 'OsirisCare') AS brand_name,
                      logo_url,
                      COALESCE(primary_color, '#4F46E5') AS primary_color
               FROM partners WHERE id = $1""",
            partner["id"],
        )

    # Render the same HTML body the digest emailer builds by calling
    # into email_alerts' helper via a shim — or rebuild inline. For
    # the preview we just format a simple HTML payload that matches
    # the content of the email.
    from dashboard_api.email_alerts import send_partner_weekly_digest  # noqa: F401
    # Import the html body by copying the same template. To avoid
    # code duplication, we call the public send function in dry-run
    # style isn't possible — instead expose a helper. For now,
    # return the raw payload as JSON so the UI can render it; HTML
    # preview is deferred.
    return {
        "partner": {
            "brand_name": p["brand_name"] if p else "OsirisCare",
            "primary_color": p["primary_color"] if p else "#4F46E5",
            "logo_url": p["logo_url"] if p else None,
        },
        **payload,
    }


@router.get("/me/sites/{site_id}/qbr")
async def get_partner_qbr_pdf(
    site_id: str,
    quarter: Optional[str] = None,
    partner: dict = require_partner_role("admin", "tech", "billing"),
):
    """Session 206 round-table P2 — Quarterly Business Review PDF.

    `quarter` format: 'YYYY-Qn' (e.g. '2026-Q1'). Defaults to the
    most-recently-completed quarter so a partner kicking off a QBR
    meeting gets the right period by default.

    Scopes by partner_id at SQL layer. Partner's brand + logo + color
    get rendered at the top of the PDF.
    """
    from fastapi.responses import Response
    from dashboard_api.report_generator import generate_qbr_pdf, is_pdf_generation_available

    if not is_pdf_generation_available():
        raise HTTPException(status_code=501, detail="PDF generation unavailable on this server")

    pool = await get_pool()
    partner_id = partner["id"]

    # Resolve quarter window. If caller passes "2026-Q1", use that; else
    # default to the most-recently-completed quarter relative to NOW().
    now = datetime.now(timezone.utc)
    if quarter:
        try:
            year_str, q_str = quarter.split("-Q", 1)
            year = int(year_str)
            q_num = int(q_str)
            if q_num not in (1, 2, 3, 4):
                raise ValueError("quarter must be 1..4")
        except Exception:
            raise HTTPException(status_code=400, detail="quarter must be 'YYYY-Qn' (1..4)")
    else:
        # Most-recently-completed quarter
        q_now = (now.month - 1) // 3 + 1
        if q_now == 1:
            year, q_num = now.year - 1, 4
        else:
            year, q_num = now.year, q_now - 1

    q_start_month = (q_num - 1) * 3 + 1
    quarter_start = datetime(year, q_start_month, 1, tzinfo=timezone.utc)
    # Exclusive upper bound — start of next quarter
    if q_num == 4:
        quarter_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        quarter_end = datetime(year, q_start_month + 3, 1, tzinfo=timezone.utc)
    quarter_label = f"{year}-Q{q_num}"

    # admin_transaction (wave-10): 4 admin reads (site, bundles, incidents,
    # remediations) — pin to one PgBouncer backend.
    async with admin_transaction(pool) as conn:
        # Verify partner owns this site (cross-partner isolation — same
        # contract as /me/dashboard, /me/search).
        site = await conn.fetchrow(
            """
            SELECT s.site_id, s.clinic_name
            FROM sites s
            WHERE s.site_id = $1 AND s.partner_id = $2 AND s.status != 'inactive'
            """,
            site_id, partner_id,
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found in your book of business")

        # KPIs across the quarter
        kpi_row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE resolution_tier = 'L1') AS l1,
                   COUNT(*) FILTER (WHERE resolution_tier = 'L2') AS l2,
                   COUNT(*) FILTER (WHERE resolution_tier = 'L3') AS l3
            FROM incidents
            WHERE site_id = $1
              AND created_at >= $2
              AND created_at < $3
            """,
            site_id, quarter_start, quarter_end,
        )
        total = int(kpi_row["total"] or 0)
        l1 = int(kpi_row["l1"] or 0)
        l2 = int(kpi_row["l2"] or 0)
        l3 = int(kpi_row["l3"] or 0)
        self_heal_pct = (100.0 * l1 / total) if total > 0 else 100.0

        # Top incident categories
        top_rows = await conn.fetch(
            """
            SELECT incident_type,
                   COUNT(*) AS n,
                   MODE() WITHIN GROUP (ORDER BY resolution_tier) AS tier
            FROM incidents
            WHERE site_id = $1
              AND created_at >= $2
              AND created_at < $3
              AND incident_type IS NOT NULL
            GROUP BY incident_type
            ORDER BY n DESC
            LIMIT 15
            """,
            site_id, quarter_start, quarter_end,
        )
        incidents_summary = [
            {
                "type": r["incident_type"],
                "count": int(r["n"]),
                "outcome": (
                    "auto-healed" if r["tier"] == "L1"
                    else "assisted" if r["tier"] == "L2"
                    else "escalated" if r["tier"] == "L3"
                    else "pending"
                ),
            }
            for r in top_rows
        ]

        # Chronic patterns broken (recurrence_broken_at inside quarter)
        chronic_broken = await conn.fetchval(
            """
            SELECT COUNT(*) FROM incident_recurrence_velocity
            WHERE site_id = $1
              AND recurrence_broken_at IS NOT NULL
              AND recurrence_broken_at >= $2
              AND recurrence_broken_at < $3
            """,
            site_id, quarter_start, quarter_end,
        ) or 0

    minutes_per_issue = 20
    value_summary = {
        "auto_heals": l1,
        "minutes_per_issue": minutes_per_issue,
        "hours_saved": round(l1 * minutes_per_issue / 60.0, 1),
    }
    kpis = {
        "incidents_total": total,
        "l1_count": l1,
        "l2_count": l2,
        "l3_count": l3,
        "self_heal_pct": self_heal_pct,
        "chronic_broken": int(chronic_broken),
    }

    pdf_bytes = generate_qbr_pdf(
        partner_brand=partner.get("brand_name") or partner.get("display_name") or "OsirisCare",
        partner_logo_url=partner.get("logo_url"),
        primary_color=partner.get("primary_color") or "#4F46E5",
        client_name=site["clinic_name"] or site_id,
        site_id=site_id,
        quarter_label=quarter_label,
        kpis=kpis,
        incidents_summary=incidents_summary,
        value_summary=value_summary,
    )
    if not pdf_bytes:
        raise HTTPException(status_code=500, detail="PDF generation failed")

    filename = f"QBR-{site_id}-{quarter_label}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/me/orgs")
async def get_my_orgs(request: Request, partner: dict = require_partner_role("admin", "tech", "billing")):
    """Get organizations managed by this partner with consolidated health."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        rows = await conn.fetch("""
            SELECT
                co.id, co.name, co.primary_email, co.practice_type,
                co.provider_count, co.status, co.created_at,
                COUNT(DISTINCT s.site_id) as site_count,
                COUNT(DISTINCT sa.appliance_id) as appliance_count,
                MAX(sa.last_checkin) as last_checkin,
                COUNT(DISTINCT sa.id) FILTER (
                    WHERE sa.last_checkin > NOW() - INTERVAL '15 minutes'
                ) as online_count,
                COALESCE(SUM(gas.total_agents), 0) as total_agents,
                CASE WHEN COUNT(gas.site_id) > 0
                     THEN ROUND(AVG(gas.overall_compliance_rate)::numeric, 1)
                     ELSE 0 END as avg_agent_compliance
            FROM client_orgs co
            LEFT JOIN sites s ON s.client_org_id = co.id
                AND s.partner_id = $1 AND s.status != 'inactive'
            LEFT JOIN site_appliances sa ON sa.site_id = s.site_id AND sa.deleted_at IS NULL
            LEFT JOIN site_go_agent_summaries gas ON gas.site_id = s.site_id
            WHERE co.current_partner_id = $1
            GROUP BY co.id
            ORDER BY co.name
        """, partner['id'])

        orgs = []
        for row in rows:
            orgs.append({
                'id': str(row['id']),
                'name': row['name'],
                'primary_email': row['primary_email'],
                'practice_type': row['practice_type'],
                'provider_count': row['provider_count'],
                'status': row['status'],
                'site_count': row['site_count'],
                'appliance_count': row['appliance_count'],
                'online_count': row['online_count'],
                'last_checkin': row['last_checkin'].isoformat() if row['last_checkin'] else None,
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                'total_agents': row['total_agents'],
                'avg_agent_compliance': float(row['avg_agent_compliance']),
            })

        await log_partner_activity(
            partner_id=str(partner['id']),
            event_type=PartnerEventType.SITES_LISTED,
            target_type="organizations",
            target_id=str(partner['id']),
            event_data={"org_count": len(orgs)},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            request_path=str(request.url.path),
            request_method=request.method,
        )

        return {'organizations': orgs, 'count': len(orgs)}


@router.get("/me/orgs/{org_id}/drift-config")
async def get_partner_org_drift_config(
    org_id: str,
    partner: dict = require_partner_role("admin", "tech", "billing")
):
    """Get drift config for all sites in an org (org-level view)."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Verify partner owns this org
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Get all site drift configs in this org
        rows = await conn.fetch("""
            SELECT s.site_id, s.clinic_name, sdc.disabled_checks
            FROM sites s
            LEFT JOIN site_drift_config sdc ON sdc.site_id = s.site_id
            WHERE s.client_org_id = $1 AND s.partner_id = $2
            ORDER BY s.clinic_name
        """, org_id, partner['id'])

        sites = []
        for row in rows:
            disabled = row['disabled_checks'] or []
            if isinstance(disabled, str):
                import json
                disabled = json.loads(disabled)
            sites.append({
                'site_id': row['site_id'],
                'clinic_name': row['clinic_name'],
                'disabled_checks': disabled,
            })

        return {'org_id': org_id, 'sites': sites}


@router.put("/me/orgs/{org_id}/drift-config")
async def update_partner_org_drift_config(
    org_id: str,
    request: Request,
    partner: dict = require_partner_role("admin", "tech")
):
    """Apply drift config to ALL sites in an org (bulk operation)."""
    pool = await get_pool()
    body = await request.json()
    disabled_checks = body.get("disabled_checks", [])

    if not isinstance(disabled_checks, list):
        raise HTTPException(status_code=400, detail="disabled_checks must be a list")

    # Safety bounds: prevent disabling critical compliance checks
    from .routes import CRITICAL_DRIFT_CHECKS
    blocked = [c for c in disabled_checks if c in CRITICAL_DRIFT_CHECKS]
    if blocked:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot disable critical compliance checks: {', '.join(sorted(blocked))}. "
            f"These checks are required for HIPAA compliance monitoring.",
        )

    async with admin_connection(pool) as conn:
        # Verify partner owns this org
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Get org's sites
        site_rows = await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1 AND partner_id = $2",
            org_id, partner['id']
        )

        # site_drift_config is keyed (site_id, check_type) — one row per
        # (site, check) pair. The original code referenced a `disabled_checks`
        # JSON column + `ON CONFLICT (site_id)` which neither exists nor is
        # a valid unique key. Surfaced 2026-04-25 by audit Task #167.
        # The sibling endpoint at partners.py:3756 uses the correct shape;
        # mirror it here.
        actor = f"partner:{partner.get('org_name', partner['id'])}"
        updated = 0
        async with conn.transaction():
            for row in site_rows:
                for check_type in disabled_checks:
                    await conn.execute("""
                        INSERT INTO site_drift_config
                            (site_id, check_type, enabled, modified_by, modified_at)
                        VALUES ($1, $2, false, $3, NOW())
                        ON CONFLICT (site_id, check_type)
                        DO UPDATE SET enabled = false,
                                      modified_by = $3,
                                      modified_at = NOW()
                    """, row['site_id'], check_type, actor)
                updated += 1

        await log_partner_site_action(
            partner_id=str(partner['id']),
            site_id=org_id,
            event_type=PartnerEventType.ASSET_UPDATED,
            event_data={
                "action": "bulk_drift_config",
                "disabled_checks": disabled_checks,
                "sites_updated": updated,
            },
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            request_path=str(request.url.path),
            request_method=request.method,
        )

        return {'status': 'updated', 'sites_updated': updated}


# =============================================================================
# ORG / SITE ALERT CONFIG (partner-managed client notification settings)
# =============================================================================

@router.get("/me/orgs/{org_id}/alert-config")
async def get_partner_org_alert_config(
    org_id: str,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Return alert email config and per-site overrides for a partner org."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            """SELECT alert_email, cc_email, client_alert_mode
               FROM client_orgs
               WHERE id = $1 AND current_partner_id = $2""",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        site_rows = await conn.fetch(
            """SELECT s.site_id, s.clinic_name AS name, s.client_alert_mode
               FROM sites s
               WHERE s.client_org_id = $1
                 AND s.partner_id = $2
                 AND s.client_alert_mode IS NOT NULL""",
            org_id, partner['id']
        )

    return {
        "alert_email": org["alert_email"],
        "cc_email": org["cc_email"],
        "client_alert_mode": org["client_alert_mode"],
        "site_overrides": [
            {
                "site_id": r["site_id"],
                "name": r["name"],
                "client_alert_mode": r["client_alert_mode"],
            }
            for r in site_rows
        ],
    }


@router.put("/me/orgs/{org_id}/alert-config")
async def update_partner_org_alert_config(
    org_id: str,
    request: Request,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Update alert email and/or client_alert_mode for a partner org."""
    body = await request.json()

    # Validate mode if provided
    mode = body.get("client_alert_mode")
    if mode is not None and mode not in VALID_ALERT_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"client_alert_mode must be one of: {', '.join(sorted(VALID_ALERT_MODES))}",
        )

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Build dynamic UPDATE for only the provided fields
        allowed_fields = {"alert_email", "cc_email", "client_alert_mode"}
        updates = {k: v for k, v in body.items() if k in allowed_fields}
        if updates:
            set_clauses = ", ".join(
                f"{col} = ${i + 1}" for i, col in enumerate(updates)
            )
            values = list(updates.values())
            values.append(org_id)
            await conn.execute(
                f"UPDATE client_orgs SET {set_clauses}, updated_at = NOW() WHERE id = ${len(values)}",
                *values,
            )

    return {"status": "updated"}


@router.put("/me/sites/{site_id}/alert-config")
async def update_partner_site_alert_config(
    site_id: str,
    request: Request,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Set per-site client_alert_mode override (null = inherit from org)."""
    body = await request.json()
    mode = body.get("client_alert_mode")

    # null is explicitly allowed (clears the override)
    if mode is not None and mode not in VALID_ALERT_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"client_alert_mode must be one of: {', '.join(sorted(VALID_ALERT_MODES))} or null",
        )

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        site = await conn.fetchrow(
            "SELECT site_id FROM sites WHERE site_id = $1 AND partner_id = $2",
            site_id, partner['id']
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        await conn.execute(
            "UPDATE sites SET client_alert_mode = $1, updated_at = NOW() WHERE site_id = $2",
            mode, site_id,
        )

    return {"status": "updated", "client_alert_mode": mode}


# =============================================================================
# ORG-LEVEL INVENTORY (partner view — multi-appliance aggregation)
# =============================================================================

@router.get("/me/orgs/{org_id}/devices")
async def get_partner_org_devices(
    org_id: str,
    partner: dict = require_partner_role("admin", "tech"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Aggregate device inventory across all sites in a partner's org."""
    pool = await get_pool()
    # admin_transaction (wave-5): 5 admin reads must pin to one PgBouncer backend.
    async with admin_transaction(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        site_ids = [r['site_id'] for r in await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1 AND partner_id = $2",
            org_id, partner['id']
        )]
        if not site_ids:
            return {"devices": [], "summary": {"total": 0, "compliant": 0, "drifted": 0, "unknown": 0, "compliance_rate": 0}, "total": 0}

        devices = await conn.fetch("""
            SELECT d.id, d.site_id, s.clinic_name, d.hostname, d.ip_address,
                   d.device_type, d.os_name, d.compliance_status, d.device_status,
                   d.last_seen_at
            FROM discovered_devices d
            JOIN sites s ON d.site_id = s.site_id
            WHERE d.site_id = ANY($1)
            ORDER BY d.ip_address
            LIMIT $2 OFFSET $3
        """, site_ids, limit, offset)

        total = await conn.fetchval(
            "SELECT count(*) FROM discovered_devices WHERE site_id = ANY($1)", site_ids
        )
        summary = await conn.fetchrow("""
            SELECT count(*) as total,
                count(*) FILTER (WHERE compliance_status = 'compliant') as compliant,
                count(*) FILTER (WHERE compliance_status = 'drifted') as drifted,
                count(*) FILTER (WHERE compliance_status IS NULL OR compliance_status = 'unknown') as unknown
            FROM discovered_devices WHERE site_id = ANY($1)
        """, site_ids)

    return {
        "devices": [
            {
                "id": str(d['id']), "site_id": d['site_id'], "clinic_name": d['clinic_name'],
                "hostname": d['hostname'], "ip_address": d['ip_address'],
                "device_type": d['device_type'], "os_name": d['os_name'],
                "compliance_status": d['compliance_status'] or 'unknown',
                "device_status": d['device_status'],
                "last_seen": d['last_seen_at'].isoformat() if d['last_seen_at'] else None,
            }
            for d in devices
        ],
        "summary": {
            "total": summary['total'], "compliant": summary['compliant'],
            "drifted": summary['drifted'], "unknown": summary['unknown'],
            "compliance_rate": round(summary['compliant'] / summary['total'] * 100, 1) if summary['total'] > 0 else 0,
        },
        "total": total, "limit": limit, "offset": offset,
    }


@router.get("/me/orgs/{org_id}/workstations")
async def get_partner_org_workstations(
    org_id: str,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Aggregate workstation compliance across all sites in a partner's org."""
    pool = await get_pool()
    # admin_transaction (wave-10): 4 admin reads (org, sites, workstations,
    # workstation_alerts) — pin to one PgBouncer backend.
    async with admin_transaction(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        site_ids = [r['site_id'] for r in await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1 AND partner_id = $2",
            org_id, partner['id']
        )]
        if not site_ids:
            return {"workstations": [], "summary": None}

        ws_rows = await conn.fetch("""
            SELECT w.id, w.site_id, s.clinic_name, w.hostname, w.ip_address,
                   w.os_name, w.online, w.compliance_status, w.compliance_percentage, w.last_seen
            FROM workstations w
            JOIN sites s ON w.site_id = s.site_id
            WHERE w.site_id = ANY($1) ORDER BY w.hostname
        """, site_ids)

        summary = await conn.fetchrow("""
            SELECT count(*) as total,
                count(*) FILTER (WHERE online) as online,
                count(*) FILTER (WHERE compliance_status = 'compliant') as compliant,
                count(*) FILTER (WHERE compliance_status = 'drifted') as drifted,
                count(*) FILTER (WHERE compliance_status = 'error') as error,
                count(*) FILTER (WHERE compliance_status IS NULL OR compliance_status = 'unknown') as unknown
            FROM workstations WHERE site_id = ANY($1)
        """, site_ids)

    total = summary['total'] or 0
    return {
        "workstations": [
            {
                "id": str(ws['id']), "site_id": ws['site_id'], "clinic_name": ws['clinic_name'],
                "hostname": ws['hostname'], "ip_address": ws['ip_address'], "os_name": ws['os_name'],
                "online": ws['online'], "compliance_status": ws['compliance_status'] or 'unknown',
                "compliance_percentage": float(ws['compliance_percentage'] or 0),
                "last_seen": ws['last_seen'].isoformat() if ws['last_seen'] else None,
            }
            for ws in ws_rows
        ],
        "summary": {
            "total_workstations": total,
            "online_workstations": summary['online'] or 0,
            "compliant_workstations": summary['compliant'] or 0,
            "drifted_workstations": summary['drifted'] or 0,
            "unknown_workstations": summary['unknown'] or 0,
            "overall_compliance_rate": round((summary['compliant'] or 0) / total * 100, 1) if total > 0 else 0,
        },
    }


@router.get("/me/orgs/{org_id}/agents")
async def get_partner_org_agents(
    org_id: str,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Aggregate Go agent status across all sites in a partner's org."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        site_ids = [r['site_id'] for r in await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1 AND partner_id = $2",
            org_id, partner['id']
        )]
        if not site_ids:
            return {"agents": [], "summary": {"total": 0, "active": 0}}

        now = datetime.now(timezone.utc)
        rows = await conn.fetch("""
            SELECT g.agent_id, g.hostname, g.ip_address, g.site_id, s.clinic_name,
                   COALESCE(NULLIF(g.os_version, ''), g.os_name) AS os_version,
                   g.agent_version, g.last_heartbeat, g.compliance_percentage
            FROM go_agents g
            JOIN sites s ON g.site_id = s.site_id
            WHERE g.site_id = ANY($1)
            ORDER BY g.last_heartbeat DESC NULLS LAST
        """, site_ids)

    agents = []
    summary = {"active": 0, "stale": 0, "offline": 0, "never": 0}
    for r in rows:
        hb = r['last_heartbeat']
        if hb is None:
            derived = "never"
        else:
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            age = now - hb
            derived = "active" if age < timedelta(minutes=5) else "stale" if age < timedelta(hours=1) else "offline"
        summary[derived] += 1
        agents.append({
            "agent_id": r['agent_id'], "hostname": r['hostname'], "site_id": r['site_id'],
            "clinic_name": r['clinic_name'], "os_version": r['os_version'],
            "agent_version": r['agent_version'], "derived_status": derived,
            "last_heartbeat": r['last_heartbeat'].isoformat() if r['last_heartbeat'] else None,
            "compliance_percentage": float(r['compliance_percentage'] or 0),
        })

    return {"agents": agents, "summary": {**summary, "total": len(agents)}}


@router.get("/me/orgs/{org_id}/evidence-witnesses")
async def get_partner_org_witnesses(
    org_id: str,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Witness attestation stats for a partner's org."""
    pool = await get_pool()
    # admin_transaction (wave-5): 5 admin reads must pin to one PgBouncer backend.
    async with admin_transaction(pool) as conn:
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id = $1 AND current_partner_id = $2",
            org_id, partner['id']
        )
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        site_ids = [r['site_id'] for r in await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1 AND partner_id = $2",
            org_id, partner['id']
        )]

        total_att = await conn.fetchval("""
            SELECT count(*) FROM witness_attestations wa
            WHERE wa.bundle_id IN (SELECT bundle_id FROM compliance_bundles WHERE site_id = ANY($1))
        """, site_ids) or 0

        recent = await conn.fetchval("""
            SELECT count(*) FROM witness_attestations wa
            WHERE wa.created_at > NOW() - interval '24h'
            AND wa.bundle_id IN (SELECT bundle_id FROM compliance_bundles WHERE site_id = ANY($1))
        """, site_ids) or 0

        total_bundles = await conn.fetchval("""
            SELECT count(DISTINCT bundle_id) FROM compliance_bundles
            WHERE site_id = ANY($1) AND checked_at > NOW() - interval '24h'
        """, site_ids) or 0

    return {
        "total_attestations": total_att,
        "attestations_24h": recent,
        "coverage_pct": round(recent / total_bundles * 100, 1) if total_bundles > 0 else 0,
        "total_bundles_24h": total_bundles,
    }


@router.post("/me/provisions")
async def create_provision_code(
    request: Request,
    provision: ProvisionCreate,
    partner: dict = require_partner_role("admin")
):
    """Create a new provision code for appliance onboarding."""
    pool = await get_pool()

    code = generate_provision_code()
    expires_at = datetime.now(timezone.utc) + timedelta(days=provision.expires_days)

    async with admin_connection(pool) as conn:
        row = await conn.fetchrow("""
            INSERT INTO appliance_provisions (
                partner_id, provision_code, target_site_id,
                client_name, expires_at
            ) VALUES ($1, $2, $3, $4, $5)
            RETURNING id, provision_code, created_at
        """,
            partner['id'],
            code,
            provision.target_site_id,
            provision.client_name,
            expires_at
        )

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.PROVISION_CREATED,
        target_type="provision",
        target_id=str(row['id']),
        event_data={"client_name": provision.client_name, "provision_code": row['provision_code']},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        "id": str(row['id']),
        "provision_code": row['provision_code'],
        "qr_content": f"osiris://{code}",  # For QR code generation
        "client_name": provision.client_name,
        "target_site_id": provision.target_site_id,
        "expires_at": expires_at.isoformat(),
        "created_at": row['created_at'].isoformat(),
    }


@router.post("/me/provisions/bulk")
async def bulk_create_provision_codes(
    request: Request,
    payload: ProvisionBulkCreate,
    partner: dict = require_partner_role("admin")
):
    """Bulk create N provision codes in one call. Accepts JSON or
    CSV-derived list from the UI. All-or-nothing insert in a single
    transaction so partial failure doesn't leave orphan codes."""
    if not payload.entries:
        raise HTTPException(status_code=400, detail="entries must not be empty")
    if len(payload.entries) > 100:
        raise HTTPException(status_code=400, detail="max 100 entries per bulk request")

    pool = await get_pool()
    expires_at = datetime.now(timezone.utc) + timedelta(days=payload.expires_days)
    results = []

    async with admin_connection(pool) as conn:
        async with conn.transaction():
            for entry in payload.entries:
                code = generate_provision_code()
                row = await conn.fetchrow("""
                    INSERT INTO appliance_provisions (
                        partner_id, provision_code, target_site_id,
                        client_name, expires_at
                    ) VALUES ($1, $2, $3, $4, $5)
                    RETURNING id, provision_code, created_at
                """,
                    partner['id'],
                    code,
                    entry.target_site_id,
                    entry.client_name,
                    expires_at,
                )
                results.append({
                    "id": str(row['id']),
                    "provision_code": row['provision_code'],
                    "qr_content": f"osiris://{code}",
                    "client_name": entry.client_name,
                    "target_site_id": entry.target_site_id,
                    "expires_at": expires_at.isoformat(),
                    "created_at": row['created_at'].isoformat(),
                })

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.PROVISION_CREATED,
        target_type="provision_bulk",
        target_id=f"bulk:{len(results)}",
        event_data={"count": len(results), "expires_days": payload.expires_days},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {"count": len(results), "provisions": results}


@router.get("/me/provisions")
async def list_provision_codes(
    status: Optional[str] = None,
    partner: dict = require_partner_role("admin", "tech")
):
    """List provision codes for this partner."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        if status:
            rows = await conn.fetch("""
                SELECT id, provision_code, target_site_id, client_name,
                       status, claimed_at, claimed_by_mac, expires_at, created_at
                FROM appliance_provisions
                WHERE partner_id = $1 AND status = $2
                ORDER BY created_at DESC
            """, partner['id'], status)
        else:
            rows = await conn.fetch("""
                SELECT id, provision_code, target_site_id, client_name,
                       status, claimed_at, claimed_by_mac, expires_at, created_at
                FROM appliance_provisions
                WHERE partner_id = $1
                ORDER BY created_at DESC
            """, partner['id'])

        provisions = []
        for row in rows:
            provisions.append({
                'id': str(row['id']),
                'provision_code': row['provision_code'],
                'qr_content': f"osiris://{row['provision_code']}",
                'target_site_id': row['target_site_id'],
                'client_name': row['client_name'],
                'status': row['status'],
                'claimed_at': row['claimed_at'].isoformat() if row['claimed_at'] else None,
                'claimed_by_mac': row['claimed_by_mac'],
                'expires_at': row['expires_at'].isoformat() if row['expires_at'] else None,
                'created_at': row['created_at'].isoformat(),
            })

        return {'provisions': provisions, 'count': len(provisions)}


@router.delete("/me/provisions/{provision_id}")
async def revoke_provision_code(
    request: Request,
    provision_id: str,
    partner: dict = require_partner_role("admin")
):
    """Revoke a provision code."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        result = await conn.fetchrow("""
            UPDATE appliance_provisions
            SET status = 'revoked'
            WHERE id = $1 AND partner_id = $2 AND status = 'pending'
            RETURNING provision_code
        """, _uid(provision_id), partner['id'])

        if not result:
            raise HTTPException(
                status_code=404,
                detail="Provision code not found or already claimed/revoked"
            )

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.PROVISION_REVOKED,
        target_type="provision",
        target_id=str(provision_id),
        event_data={"provision_code": result['provision_code']},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        "status": "revoked",
        "provision_code": result['provision_code'],
    }


@router.get("/me/provisions/{provision_id}/qr")
async def get_provision_qr_code(
    provision_id: str,
    size: int = 200,
    partner: dict = require_partner_role("admin", "tech")
):
    """Generate QR code image for a provision code.

    Returns a PNG image that can be scanned by the appliance during setup.
    The QR contains the provision URL: osiris://PROVISION_CODE

    Args:
        provision_id: UUID of the provision
        size: QR code size in pixels (default 200, max 500)
    """
    if not HAS_QRCODE:
        raise HTTPException(
            status_code=501,
            detail="QR code generation not available - qrcode library not installed"
        )

    # Limit size to prevent abuse
    size = min(max(size, 100), 500)

    pool = await get_pool()

    async with admin_connection(pool) as conn:
        provision = await conn.fetchrow("""
            SELECT provision_code, status
            FROM appliance_provisions
            WHERE id = $1 AND partner_id = $2
        """, _uid(provision_id), partner['id'])

        if not provision:
            raise HTTPException(status_code=404, detail="Provision not found")

        if provision['status'] != 'pending':
            raise HTTPException(
                status_code=400,
                detail=f"Cannot generate QR for {provision['status']} provision"
            )

    # Generate QR code
    qr_content = f"osiris://{provision['provision_code']}"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_content)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Resize to requested size
    img = img.resize((size, size))

    # Convert to bytes
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="image/png",
        headers={
            "Content-Disposition": f"inline; filename=provision-{provision['provision_code']}.png",
            "Cache-Control": "no-cache",
        }
    )


@router.get("/provision/{provision_code}/qr")
async def get_provision_qr_by_code(
    provision_code: str,
    size: int = 200,
):
    """Generate QR code for a provision code (public endpoint).

    This is a public endpoint that can be used to regenerate QR codes
    for valid (pending) provision codes. Used in printed setup instructions.

    Args:
        provision_code: The 16-character provision code
        size: QR code size in pixels (default 200, max 500)
    """
    if not HAS_QRCODE:
        raise HTTPException(
            status_code=501,
            detail="QR code generation not available"
        )

    size = min(max(size, 100), 500)

    pool = await get_pool()

    async with admin_connection(pool) as conn:
        provision = await conn.fetchrow("""
            SELECT status FROM appliance_provisions
            WHERE provision_code = $1
        """, provision_code.upper())

        if not provision:
            raise HTTPException(status_code=404, detail="Invalid provision code")

        if provision['status'] != 'pending':
            raise HTTPException(
                status_code=400,
                detail=f"Provision code is {provision['status']}"
            )

    qr_content = f"osiris://{provision_code.upper()}"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_content)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img = img.resize((size, size))

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="image/png",
        headers={
            "Content-Disposition": f"inline; filename=provision-{provision_code}.png",
            "Cache-Control": "no-cache",
        }
    )


# =============================================================================
# PARTNER BRANDING ENDPOINTS (Partner-authenticated)
# =============================================================================

@router.get("/me/branding")
async def get_my_branding(partner: dict = require_partner_role("admin", "tech", "billing")):
    """Get own branding config."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow("""
            SELECT brand_name, logo_url, primary_color, secondary_color,
                   tagline, support_email, support_phone, slug,
                   email_from_display_name, email_reply_to_address
            FROM partners WHERE id = $1
        """, partner['id'])

    if not row:
        raise HTTPException(status_code=404, detail="Partner not found")

    # Migration 232 added email_from_display_name + email_reply_to_address;
    # tolerate test fixtures that don't surface them (pre-232 FakeRecords).
    def _row_get(r, key):
        try:
            return r[key]
        except (KeyError, IndexError, TypeError):
            return None

    return {
        "brand_name": row["brand_name"] or "OsirisCare",
        "logo_url": row["logo_url"],
        "primary_color": row["primary_color"] or "#0D9488",
        "secondary_color": row["secondary_color"] or "#6366F1",
        "tagline": row["tagline"],
        "support_email": row["support_email"],
        "support_phone": row["support_phone"],
        "partner_slug": row["slug"],
        "email_from_display_name": _row_get(row, "email_from_display_name"),
        "email_reply_to_address": _row_get(row, "email_reply_to_address"),
    }


@router.put("/me/branding")
async def update_my_branding(
    request: Request,
    body: BrandingUpdate,
    partner: dict = require_partner_role("admin"),
):
    """Update branding. Admin only."""
    updates = {}

    # Validate colors
    if body.primary_color is not None:
        _validate_hex_color(body.primary_color)
        updates["primary_color"] = body.primary_color
    if body.secondary_color is not None:
        _validate_hex_color(body.secondary_color)
        updates["secondary_color"] = body.secondary_color

    # Validate logo_url must be HTTPS if provided
    if body.logo_url is not None:
        if body.logo_url and not body.logo_url.startswith("https://"):
            raise HTTPException(
                status_code=400,
                detail="logo_url must use HTTPS",
            )
        updates["logo_url"] = body.logo_url

    # Sanitize text fields — strip HTML
    if body.brand_name is not None:
        sanitized = _sanitize_text(body.brand_name)
        if not sanitized:
            raise HTTPException(status_code=400, detail="brand_name cannot be empty")
        updates["brand_name"] = sanitized[:255]
    if body.tagline is not None:
        updates["tagline"] = _sanitize_text(body.tagline)[:500]

    # Support contact fields (light validation)
    if body.support_email is not None:
        updates["support_email"] = body.support_email[:255] if body.support_email else None
    if body.support_phone is not None:
        updates["support_phone"] = body.support_phone[:50] if body.support_phone else None

    # Email-from branding (display name + Reply-To). Envelope From stays on
    # OsirisCare's SMTP identity for DKIM/SPF alignment — see migration 232.
    if body.email_from_display_name is not None:
        sanitized = _sanitize_text(body.email_from_display_name)
        updates["email_from_display_name"] = (sanitized[:120] if sanitized else None)
    if body.email_reply_to_address is not None:
        addr = body.email_reply_to_address.strip() if body.email_reply_to_address else None
        if addr and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', addr):
            raise HTTPException(
                status_code=400,
                detail="email_reply_to_address must look like an email",
            )
        updates["email_reply_to_address"] = addr[:255] if addr else None

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Build dynamic UPDATE
    set_clauses = []
    params = []
    for i, (col, val) in enumerate(updates.items(), start=1):
        set_clauses.append(f"{col} = ${i}")
        params.append(val)
    params.append(partner['id'])
    set_sql = ", ".join(set_clauses)

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        await conn.execute(
            f"UPDATE partners SET {set_sql}, updated_at = NOW() WHERE id = ${len(params)}",
            *params,
        )

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.BRANDING_UPDATED,
        target_type="partner",
        target_id=str(partner['id']),
        event_data={"updated_fields": list(updates.keys())},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {"status": "updated", "updated_fields": list(updates.keys())}


# =============================================================================
# COMMISSION DASHBOARD
# =============================================================================
# Lets a partner see exactly what they're earning: MRR from active
# subscriptions, effective revenue-share rate (from migration 233's tiered
# curve), estimated monthly + YTD commission, and per-month breakdown.
#
# This is READ-ONLY and deliberately computes from subscription state + the
# shared PARTNER_PLAN_CATALOG. Real paid-out commission history sits in
# partner_invoices once Stripe Connect ships — for now lifetime_paid is
# reported as 0 and the UI is explicit that the commission figure is
# estimated pending reconciliation.

@router.get("/me/commission")
async def get_my_commission(
    partner: dict = require_partner_role("admin", "billing")
):
    """Return commission summary for this partner.

    Response shape:
        {
          "active_clinic_count": int,
          "mrr_cents": int,                   # monthly recurring across active subs
          "ytd_mrr_cents": int,               # sum of MRR months YTD (approx)
          "effective_rate_bps": int,          # from compute_partner_rate_bps()
          "estimated_monthly_commission_cents": int,
          "ytd_estimated_commission_cents": int,
          "lifetime_paid_cents": int,         # always 0 until Stripe Connect
          "currency": "USD",
          "monthly_breakdown": [{month: 'YYYY-MM', mrr_cents, commission_cents}, ...]
        }
    """
    # Price map — one source of truth with client_signup.PLAN_CATALOG
    # Inlined here to avoid a cross-module import cycle.
    MONTHLY_AMOUNT_CENTS = {
        "essentials": 49900,
        "professional": 79900,
        "enterprise": 129900,
        # pilot is one-time; doesn't contribute to MRR
    }

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        subs = await conn.fetch(
            """
            SELECT plan, status, current_period_start, current_period_end,
                   site_id, created_at
              FROM subscriptions
             WHERE partner_id = $1::uuid
               AND status IN ('active','trialing','past_due')
            """,
            str(partner['id']),
        )

        # Active clinic count = distinct non-null site_id from the partner's
        # active subs. Treat each active sub as one "clinic seat".
        active_clinic_count = len({s['site_id'] for s in subs if s['site_id']})
        if active_clinic_count == 0:
            # Fallback: if no site_id stamped yet (webhook race), count subs.
            active_clinic_count = sum(
                1 for s in subs if s['status'] in ('active', 'trialing')
            )

        mrr_cents = sum(
            MONTHLY_AMOUNT_CENTS.get(s['plan'], 0)
            for s in subs
            if s['plan'] in MONTHLY_AMOUNT_CENTS and s['status'] in ('active', 'trialing')
        )

        # F5: compute_partner_rate_bps() can return NULL (no tier row for this
        # partner, rate table misconfigured, migration 233 not applied, etc.).
        # Never silently fall back to 40% — the UI would confidently render a
        # fabricated commission figure the partner would then expect to be
        # paid. Log at ERROR + surface rate_unavailable=true so the frontend
        # renders "—" and the partner calls us.
        rate_row = await conn.fetchrow(
            "SELECT compute_partner_rate_bps($1::uuid, $2::int) AS bps",
            str(partner['id']),
            active_clinic_count,
        )
        rate_unavailable = (
            rate_row is None
            or rate_row['bps'] is None
        )
        if rate_unavailable:
            logger.error(
                "partners.commission.rate_bps_null",
                extra={
                    "partner_id": str(partner['id']),
                    "active_clinic_count": active_clinic_count,
                },
            )
            effective_rate_bps = 0  # never compute a number from this
        else:
            effective_rate_bps = int(rate_row['bps'])

        # F6: single-query monthly breakdown via generate_series + LEFT JOIN.
        # Previously fired 12 separate `SELECT plan FROM subscriptions`
        # queries per request — hot dashboard endpoint.
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        month_rows = await conn.fetch(
            """
            WITH months AS (
              SELECT generate_series(
                date_trunc('month', $2::timestamptz - INTERVAL '11 months'),
                date_trunc('month', $2::timestamptz),
                INTERVAL '1 month'
              ) AS month_start
            )
            SELECT
              to_char(m.month_start, 'YYYY-MM') AS month,
              COALESCE(SUM(CASE s.plan
                WHEN 'essentials'   THEN 49900
                WHEN 'professional' THEN 79900
                WHEN 'enterprise'   THEN 129900
                ELSE 0 END), 0)::bigint AS month_mrr_cents
              FROM months m
              LEFT JOIN subscriptions s
                ON s.partner_id = $1::uuid
               AND s.created_at <= m.month_start
               AND (s.canceled_at IS NULL OR s.canceled_at > m.month_start)
               AND s.status <> 'incomplete'
             GROUP BY m.month_start
             ORDER BY m.month_start ASC
            """,
            str(partner['id']),
            now,
        )
        breakdown = [
            {
                "month": r['month'],
                "mrr_cents": int(r['month_mrr_cents']),
                "commission_cents": (
                    (int(r['month_mrr_cents']) * effective_rate_bps) // 10000
                    if not rate_unavailable else None
                ),
            }
            for r in month_rows
        ]

    estimated_monthly = (
        (mrr_cents * effective_rate_bps) // 10000
        if not rate_unavailable else None
    )
    current_year = now.year
    ytd_months = [b for b in breakdown if b["month"].startswith(str(current_year))]
    ytd_mrr = sum(b["mrr_cents"] for b in ytd_months)
    ytd_commission = (
        sum(b["commission_cents"] for b in ytd_months)
        if not rate_unavailable else None
    )

    return {
        "active_clinic_count": active_clinic_count,
        "mrr_cents": mrr_cents,
        "ytd_mrr_cents": ytd_mrr,
        "effective_rate_bps": effective_rate_bps if not rate_unavailable else None,
        "rate_unavailable": rate_unavailable,
        "estimated_monthly_commission_cents": estimated_monthly,
        "ytd_estimated_commission_cents": ytd_commission,
        "lifetime_paid_cents": 0,
        "currency": "USD",
        "monthly_breakdown": breakdown,
        "note": (
            "Commission is estimated from active Stripe subscriptions and the "
            "tiered revenue-share rate. Actual payouts reconcile once Stripe "
            "Connect is activated — see partner_payouts for paid history."
        ),
    }


# =============================================================================
# PUBLIC BRANDING ENDPOINT (no auth — for login page rendering)
# =============================================================================

# Separate router for the public branding endpoint (no partner auth prefix)
branding_public_router = APIRouter(tags=["portal-branding"])


@branding_public_router.get("/api/portal/branding/{partner_slug}")
async def get_portal_branding(partner_slug: str):
    """Public endpoint — returns partner branding for login page rendering."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow("""
            SELECT brand_name, logo_url, primary_color, secondary_color,
                   tagline, support_email, support_phone, slug
            FROM partners WHERE slug = $1 AND status = 'active'
        """, partner_slug)

    if not row:
        # Return OsirisCare defaults — don't reveal partner doesn't exist
        return {
            "brand_name": "OsirisCare",
            "logo_url": None,
            "primary_color": "#0D9488",
            "secondary_color": "#6366F1",
            "tagline": "HIPAA Compliance Simplified",
            "support_email": None,
            "support_phone": None,
            "partner_slug": partner_slug,
        }

    return {
        "brand_name": row["brand_name"] or "OsirisCare",
        "logo_url": row["logo_url"],
        "primary_color": row["primary_color"] or "#0D9488",
        "secondary_color": row["secondary_color"] or "#6366F1",
        "tagline": row["tagline"],
        "support_email": row["support_email"],
        "support_phone": row["support_phone"],
        "partner_slug": row["slug"],
    }


# =============================================================================
# ADMIN ENDPOINTS (for OsirisCare staff)
# =============================================================================

@router.post("")
async def create_partner(request: Request, partner: PartnerCreate, admin: dict = Depends(require_admin)):
    """Create a new partner (admin only)."""
    pool = await get_pool()

    # Generate API key
    api_key = generate_api_key()
    api_key_hash = hash_api_key(api_key)

    async with admin_connection(pool) as conn:
        # Check slug uniqueness
        existing = await conn.fetchval(
            "SELECT 1 FROM partners WHERE slug = $1",
            partner.slug.lower()
        )
        if existing:
            raise HTTPException(status_code=400, detail=f"Slug '{partner.slug}' already exists")

        # Insert partner (API key expires in 1 year by default)
        from datetime import timedelta
        api_key_expires = datetime.now(timezone.utc) + timedelta(days=365)
        row = await conn.fetchrow("""
            INSERT INTO partners (
                name, slug, contact_email, contact_phone,
                brand_name, logo_url, primary_color,
                revenue_share_percent, api_key_hash,
                api_key_created_at, api_key_expires_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), $10)
            RETURNING id, name, slug, created_at
        """,
            partner.name,
            partner.slug.lower(),
            partner.contact_email,
            partner.contact_phone,
            partner.brand_name or partner.name,
            partner.logo_url,
            partner.primary_color,
            partner.revenue_share_percent,
            api_key_hash,
            api_key_expires,
        )

    await log_partner_activity(
        partner_id=str(row['id']),
        event_type=PartnerEventType.PARTNER_CREATED,
        target_type="partner",
        target_id=str(row['id']),
        event_data={"partner_name": row['name'], "slug": row['slug'], "admin_user": admin.get("sub", "unknown")},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        "id": str(row['id']),
        "name": row['name'],
        "slug": row['slug'],
        "api_key": api_key,  # Only returned once at creation!
        "created_at": row['created_at'].isoformat(),
        "message": "Save this API key - it cannot be retrieved later"
    }


@router.get("")
async def list_partners(
    status: Optional[str] = None,
    search: Optional[str] = Query(None, min_length=1, max_length=200),
    sort_by: str = Query("name", regex="^(name|created_at|status|site_count|revenue_share_percent)$"),
    sort_dir: str = Query("asc", regex="^(asc|desc)$"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(require_admin),
):
    """List partners with pagination, search, and sorting (admin only)."""
    pool = await get_pool()

    # admin_transaction (wave-10): 4 admin reads (count + page + status agg +
    # site totals) — pin to one PgBouncer backend.
    async with admin_transaction(pool) as conn:
        # Build WHERE clause
        conditions = []
        params: list = []
        idx = 1

        ALLOWED_STATUSES = {"active", "suspended", "inactive", "pending"}
        if status:
            if status not in ALLOWED_STATUSES:
                raise HTTPException(status_code=400, detail=f"Invalid status filter: {status}")
            conditions.append(f"p.status = ${idx}")
            params.append(status)
            idx += 1

        if search:
            conditions.append(
                f"(p.name ILIKE ${idx} OR p.slug ILIKE ${idx} OR p.contact_email ILIKE ${idx} OR p.brand_name ILIKE ${idx})"
            )
            params.append(f"%{search}%")
            idx += 1

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Sort mapping (site_count is computed, needs special handling)
        sort_col = {
            "name": "p.name",
            "created_at": "p.created_at",
            "status": "p.status",
            "revenue_share_percent": "p.revenue_share_percent",
            "site_count": "site_count",
        }.get(sort_by, "p.name")
        order = f"{sort_col} {'DESC' if sort_dir == 'desc' else 'ASC'}"

        # Get total count for pagination
        total_row = await conn.fetchrow(
            f"SELECT COUNT(*) as total FROM partners p {where}", *params
        )
        total_count = total_row['total'] if total_row else 0

        # Get aggregate stats (all partners, ignoring filters)
        stats_row = await conn.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'active') as active,
                COUNT(*) FILTER (WHERE status = 'suspended') as suspended,
                COUNT(*) FILTER (WHERE status = 'inactive') as inactive
            FROM partners
        """)

        total_sites_row = await conn.fetchrow(
            "SELECT COUNT(*) as total FROM sites WHERE partner_id IS NOT NULL"
        )

        # Fetch page with site counts joined
        params_page = list(params)
        params_page.extend([limit, offset])
        rows = await conn.fetch(f"""
            SELECT p.id, p.name, p.slug, p.contact_email, p.brand_name,
                   p.revenue_share_percent, p.status, p.created_at,
                   COALESCE(sc.cnt, 0) as site_count
            FROM partners p
            LEFT JOIN (
                SELECT partner_id, COUNT(*) as cnt
                FROM sites
                WHERE partner_id IS NOT NULL
                GROUP BY partner_id
            ) sc ON sc.partner_id = p.id
            {where}
            ORDER BY {order}
            LIMIT ${idx} OFFSET ${idx + 1}
        """, *params_page)

        partners = []
        for row in rows:
            partners.append({
                'id': str(row['id']),
                'name': row['name'],
                'slug': row['slug'],
                'contact_email': row['contact_email'],
                'brand_name': row['brand_name'],
                'revenue_share_percent': row['revenue_share_percent'],
                'status': row['status'],
                'site_count': row['site_count'],
                'created_at': row['created_at'].isoformat(),
            })

        return {
            'partners': partners,
            'count': len(partners),
            'total': total_count,
            'limit': limit,
            'offset': offset,
            'stats': {
                'total': stats_row['total'] if stats_row else 0,
                'active': stats_row['active'] if stats_row else 0,
                'suspended': stats_row['suspended'] if stats_row else 0,
                'inactive': stats_row['inactive'] if stats_row else 0,
                'total_sites': total_sites_row['total'] if total_sites_row else 0,
            },
        }


@router.get("/activity/all")
async def get_all_partner_activity_log(
    partner_id: Optional[str] = Query(None),
    event_category: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(require_admin),
):
    """Get all partner activity logs (admin only)."""
    logs = await get_partner_activity(
        partner_id=partner_id,
        event_type=event_type,
        event_category=event_category,
        limit=limit,
        offset=offset,
    )
    stats = await get_partner_activity_stats()
    return {"logs": logs, "stats": stats}


# =============================================================================
# ADMIN PARTNER-SPECIFIC ENDPOINTS
# These come AFTER /me routes since they use /{partner_id} parameter
# =============================================================================

@router.get("/{partner_id}")
async def get_partner(partner_id: str, admin: dict = Depends(require_admin)):
    """Get partner details (admin only)."""
    pool = await get_pool()

    # admin_transaction (Session 212): 6 admin reads — partner row +
    # users + sites + recent activity + billing snapshot.
    async with admin_transaction(pool) as conn:
        row = await conn.fetchrow("""
            SELECT id, name, slug, contact_email, contact_phone,
                   brand_name, logo_url, primary_color,
                   revenue_share_percent, status, created_at, updated_at
            FROM partners
            WHERE id = $1
        """, _uid(partner_id))

        if not row:
            raise HTTPException(status_code=404, detail="Partner not found")

        # Get sites for this partner
        sites = await conn.fetch("""
            SELECT site_id, clinic_name, status, tier
            FROM sites
            WHERE partner_id = $1
            ORDER BY clinic_name
        """, _uid(partner_id))

        # Get users for this partner
        users = await conn.fetch("""
            SELECT id, email, name, role, status, last_login
            FROM partner_users
            WHERE partner_id = $1
            ORDER BY email
        """, _uid(partner_id))

        # Get appliance stats per site
        appliance_stats = await conn.fetch("""
            SELECT sa.site_id, COUNT(*) as appliance_count,
                   MAX(sa.last_checkin) as last_checkin
            FROM site_appliances sa
            JOIN sites s ON s.site_id = sa.site_id
            WHERE s.partner_id = $1 AND sa.deleted_at IS NULL
            GROUP BY sa.site_id
        """, _uid(partner_id))
        app_map = {r['site_id']: {'count': r['appliance_count'], 'last_checkin': r['last_checkin']} for r in appliance_stats}

        # Get incident counts (best-effort — incidents may not link cleanly to partner sites)
        try:
            incident_row = await conn.fetchrow("""
                SELECT COUNT(*) as total,
                       COUNT(*) FILTER (WHERE i.status = 'open') as open_count
                FROM incidents i
                JOIN site_appliances sa ON sa.appliance_id = i.appliance_id AND sa.deleted_at IS NULL
                WHERE sa.site_id IN (SELECT site_id FROM sites WHERE partner_id = $1)
            """, _uid(partner_id))
        except Exception:
            incident_row = {'total': 0, 'open_count': 0}

        # Get recent activity count
        activity_row = await conn.fetchrow("""
            SELECT COUNT(*) as total,
                   COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as recent
            FROM partner_activity_log
            WHERE partner_id = $1
        """, _uid(partner_id))

        return {
            'id': str(row['id']),
            'name': row['name'],
            'slug': row['slug'],
            'contact_email': row['contact_email'],
            'contact_phone': row['contact_phone'],
            'brand_name': row['brand_name'],
            'logo_url': row['logo_url'],
            'primary_color': row['primary_color'],
            'revenue_share_percent': row['revenue_share_percent'],
            'status': row['status'],
            'created_at': row['created_at'].isoformat(),
            'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None,
            'sites': [
                {
                    'site_id': s['site_id'],
                    'clinic_name': s['clinic_name'],
                    'status': s['status'],
                    'tier': s['tier'],
                    'appliance_count': app_map.get(s['site_id'], {}).get('count', 0),
                    'last_checkin': app_map.get(s['site_id'], {}).get('last_checkin', None).isoformat() if app_map.get(s['site_id'], {}).get('last_checkin') else None,
                }
                for s in sites
            ],
            'users': [
                {
                    'id': str(u['id']),
                    'email': u['email'],
                    'name': u['name'],
                    'role': u['role'],
                    'status': u['status'],
                    'last_login': u['last_login'].isoformat() if u['last_login'] else None,
                }
                for u in users
            ],
            'stats': {
                'total_sites': len(sites),
                'total_users': len(users),
                'total_appliances': sum(a['count'] for a in app_map.values()),
                'total_incidents': incident_row['total'] if incident_row else 0,
                'open_incidents': incident_row['open_count'] if incident_row else 0,
                'total_activity': activity_row['total'] if activity_row else 0,
                'recent_activity': activity_row['recent'] if activity_row else 0,
            },
        }


@router.put("/{partner_id}")
async def update_partner(request: Request, partner_id: str, update: PartnerUpdate, admin: dict = Depends(require_admin)):
    """Update partner info (admin only)."""
    pool = await get_pool()

    updates = []
    values = []
    param_num = 1

    if update.name is not None:
        updates.append(f"name = ${param_num}")
        values.append(update.name)
        param_num += 1

    if update.contact_email is not None:
        updates.append(f"contact_email = ${param_num}")
        values.append(update.contact_email)
        param_num += 1

    if update.contact_phone is not None:
        updates.append(f"contact_phone = ${param_num}")
        values.append(update.contact_phone)
        param_num += 1

    if update.brand_name is not None:
        updates.append(f"brand_name = ${param_num}")
        values.append(update.brand_name)
        param_num += 1

    if update.logo_url is not None:
        updates.append(f"logo_url = ${param_num}")
        values.append(update.logo_url)
        param_num += 1

    if update.primary_color is not None:
        updates.append(f"primary_color = ${param_num}")
        values.append(update.primary_color)
        param_num += 1

    if update.revenue_share_percent is not None:
        updates.append(f"revenue_share_percent = ${param_num}")
        values.append(update.revenue_share_percent)
        param_num += 1

    if update.status is not None:
        updates.append(f"status = ${param_num}")
        values.append(update.status)
        param_num += 1

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append(f"updated_at = ${param_num}")
    values.append(datetime.now(timezone.utc))
    param_num += 1

    values.append(_uid(partner_id))

    query = f"""
        UPDATE partners
        SET {', '.join(updates)}
        WHERE id = ${param_num}
        RETURNING id, name, slug
    """

    async with admin_connection(pool) as conn:
        result = await conn.fetchrow(query, *values)
        if not result:
            raise HTTPException(status_code=404, detail="Partner not found")

    await log_partner_activity(
        partner_id=str(partner_id),
        event_type=PartnerEventType.PARTNER_UPDATED,
        target_type="partner",
        target_id=str(partner_id),
        event_data={"updated_fields": [u.split(" = ")[0].strip() for u in updates if " = " in u], "admin_user": admin.get("sub", "unknown")},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        "status": "updated",
        "id": str(result['id']),
        "name": result['name'],
    }


@router.post("/{partner_id}/regenerate-key")
async def regenerate_api_key(request: Request, partner_id: str, admin: dict = Depends(require_admin)):
    """Regenerate partner API key (admin only)."""
    pool = await get_pool()

    api_key = generate_api_key()
    api_key_hash = hash_api_key(api_key)

    from datetime import timedelta
    api_key_expires = datetime.now(timezone.utc) + timedelta(days=365)

    async with admin_connection(pool) as conn:
        result = await conn.fetchrow("""
            UPDATE partners
            SET api_key_hash = $1, api_key_created_at = NOW(),
                api_key_expires_at = $3, updated_at = NOW()
            WHERE id = $2
            RETURNING name
        """, api_key_hash, _uid(partner_id), api_key_expires)

        if not result:
            raise HTTPException(status_code=404, detail="Partner not found")

    await log_partner_activity(
        partner_id=str(partner_id),
        event_type=PartnerEventType.API_KEY_REGENERATED,
        target_type="partner",
        target_id=str(partner_id),
        event_data={"partner_name": result['name'], "admin_user": admin.get("sub", "unknown")},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    # Maya P1-1 closure 2026-05-04: api_key_regenerated promoted to
    # full Ed25519 chain. API key regeneration grants the bearer
    # full partner-API access for 365 days — privileged action.
    api_key_attestation_failed = False
    api_key_bundle_id = None
    try:
        from .privileged_access_attestation import (
            create_privileged_access_attestation,
            PrivilegedAccessAttestationError,
        )
        async with admin_connection(pool) as att_conn:
            try:
                att = await create_privileged_access_attestation(
                    att_conn,
                    site_id=f"partner_org:{partner_id}",
                    event_type="partner_api_key_regenerated",
                    actor_email=(
                        admin.get("email") or admin.get("username")
                        or "unknown"
                    ),
                    reason=(
                        f"partner API key regenerated for "
                        f"{result['name']}"
                    ),
                    origin_ip=(request.client.host
                               if request.client else None),
                    approvals=[{
                        "stage": "applied",
                        "actor": admin.get("email") or admin.get("sub"),
                        "partner_id": str(partner_id),
                        "expires_at": api_key_expires.isoformat(),
                    }],
                )
                api_key_bundle_id = att.get("bundle_id")
            except PrivilegedAccessAttestationError:
                api_key_attestation_failed = True
                logger.error(
                    "partner_api_key_attestation_failed",
                    exc_info=True,
                    extra={"partner_id": str(partner_id)},
                )
    except Exception:
        api_key_attestation_failed = True
        logger.error(
            "partner_api_key_attestation_unexpected",
            exc_info=True,
            extra={"partner_id": str(partner_id)},
        )

    try:
        from .email_alerts import send_operator_alert
        op_severity = ("P0-CHAIN-GAP"
                       if api_key_attestation_failed else "P1")
        op_suffix = (" [ATTESTATION-MISSING]"
                     if api_key_attestation_failed else "")
        send_operator_alert(
            event_type="partner_api_key_regenerated",
            severity=op_severity,
            summary=(
                f"Partner API key regenerated: {result['name']} "
                f"(partner_id={partner_id}){op_suffix}"
            ),
            details={
                "partner_id": str(partner_id),
                "partner_name": result["name"],
                "expires_at": api_key_expires.isoformat(),
                "attestation_bundle_id": api_key_bundle_id,
                "attestation_failed": api_key_attestation_failed,
            },
            site_id=f"partner_org:{partner_id}",
            actor_email=admin.get("email") or admin.get("username"),
        )
    except Exception:
        logger.error(
            "operator_alert_dispatch_failed_partner_api_key",
            exc_info=True,
        )

    return {
        "status": "regenerated",
        "api_key": api_key,
        "expires_at": api_key_expires.isoformat(),
        "attestation_bundle_id": api_key_bundle_id,
        "message": "Save this API key - it cannot be retrieved later. Expires in 1 year."
    }


@router.delete("/{partner_id}")
async def delete_partner(request: Request, partner_id: str, admin: dict = Depends(require_admin)):
    """Delete a partner (admin only). Cascades to sessions, provisions, etc."""
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Get partner info for audit log before deleting
        partner = await conn.fetchrow(
            "SELECT id, name, slug FROM partners WHERE id = $1",
            _uid(partner_id),
        )
        if not partner:
            raise HTTPException(status_code=404, detail="Partner not found")

        # Unlink sites (set partner_id to NULL rather than deleting sites)
        await conn.execute(
            "UPDATE sites SET partner_id = NULL WHERE partner_id = $1",
            _uid(partner_id),
        )

        # Delete the partner (cascades to sessions, provisions, etc.)
        await conn.execute("DELETE FROM partners WHERE id = $1", _uid(partner_id))

    await log_partner_activity(
        partner_id=str(partner_id),
        event_type=PartnerEventType.PARTNER_UPDATED,
        target_type="partner",
        target_id=str(partner_id),
        event_data={
            "action": "deleted",
            "partner_name": partner["name"],
            "partner_slug": partner["slug"],
            "admin_user": admin.get("sub", "unknown"),
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    # Maya P1-1 closure 2026-05-04: partner_org_deleted promoted to
    # full Ed25519 chain. Destructive cascade — every downstream
    # client_org loses its partner relationship + all sessions /
    # provisions purged. Highest-friction privileged action on the
    # partner-portal surface; absolutely needs the cryptographic
    # record for incident-response.
    delete_attestation_failed = False
    delete_bundle_id = None
    try:
        from .privileged_access_attestation import (
            create_privileged_access_attestation,
            PrivilegedAccessAttestationError,
        )
        pool_for_att = await get_pool()
        async with admin_connection(pool_for_att) as att_conn:
            try:
                att = await create_privileged_access_attestation(
                    att_conn,
                    site_id=f"partner_org:{partner_id}",
                    event_type="partner_org_deleted",
                    actor_email=(
                        admin.get("email") or admin.get("username")
                        or admin.get("sub") or "unknown"
                    ),
                    reason=(
                        f"partner_org {partner['name']} "
                        f"(slug={partner['slug']}) deleted"
                    ),
                    origin_ip=(request.client.host
                               if request.client else None),
                    approvals=[{
                        "stage": "applied",
                        "actor": admin.get("email") or admin.get("sub"),
                        "partner_id": str(partner_id),
                        "partner_name": partner["name"],
                        "partner_slug": partner["slug"],
                    }],
                )
                delete_bundle_id = att.get("bundle_id")
            except PrivilegedAccessAttestationError:
                delete_attestation_failed = True
                logger.error(
                    "partner_org_deleted_attestation_failed",
                    exc_info=True,
                    extra={"partner_id": str(partner_id)},
                )
    except Exception:
        delete_attestation_failed = True
        logger.error(
            "partner_org_deleted_attestation_unexpected",
            exc_info=True,
            extra={"partner_id": str(partner_id)},
        )

    try:
        from .email_alerts import send_operator_alert
        op_severity = ("P0-CHAIN-GAP"
                       if delete_attestation_failed else "P1")
        op_suffix = (" [ATTESTATION-MISSING]"
                     if delete_attestation_failed else "")
        send_operator_alert(
            event_type="partner_org_deleted",
            severity=op_severity,
            summary=(
                f"Partner org DELETED: {partner['name']} "
                f"(slug={partner['slug']}, id={partner_id}){op_suffix}"
            ),
            details={
                "partner_id": str(partner_id),
                "partner_name": partner["name"],
                "partner_slug": partner["slug"],
                "actor": admin.get("email") or admin.get("sub"),
                "attestation_bundle_id": delete_bundle_id,
                "attestation_failed": delete_attestation_failed,
            },
            site_id=f"partner_org:{partner_id}",
            actor_email=admin.get("email") or admin.get("sub"),
        )
    except Exception:
        logger.error(
            "operator_alert_dispatch_failed_partner_delete",
            exc_info=True,
        )

    return {
        "status": "deleted",
        "id": str(partner_id),
        "name": partner["name"],
        "attestation_bundle_id": delete_bundle_id,
    }


@router.get("/{partner_id}/activity")
async def get_partner_activity_log(
    partner_id: str,
    event_category: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(require_admin),
):
    """Get activity log for a specific partner (admin only)."""
    logs = await get_partner_activity(
        partner_id=partner_id,
        event_category=event_category,
        limit=limit,
        offset=offset,
    )
    stats = await get_partner_activity_stats(partner_id=partner_id)
    return {"logs": logs, "stats": stats}


@router.post("/{partner_id}/users")
async def create_partner_user(
    partner_id: str,
    user: PartnerUserCreate,
    request: Request,
    admin: dict = Depends(require_admin),
):
    """Create a user for a partner (admin only).

    Maya parity finding 2026-05-04 (round-table verdict E):
    partner_user creation is a privileged action — sets the new
    user's role + magic-link path. Pre-fix: NO audit, NO Ed25519
    attestation, NO operator alert. Now: audit + cryptographic chain
    + operator visibility, matching the client_user role-change
    treatment in client_portal.py.
    """
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Verify partner exists
        partner = await conn.fetchval("SELECT 1 FROM partners WHERE id = $1", _uid(partner_id))
        if not partner:
            raise HTTPException(status_code=404, detail="Partner not found")

        # Check email uniqueness within partner
        existing = await conn.fetchval("""
            SELECT 1 FROM partner_users
            WHERE partner_id = $1 AND email = $2
        """, _uid(partner_id), user.email.lower())
        if existing:
            raise HTTPException(status_code=400, detail="Email already exists for this partner")

        # Generate magic token for initial login
        magic_token = generate_magic_token()
        magic_expires = datetime.now(timezone.utc) + timedelta(days=7)

        row = await conn.fetchrow("""
            INSERT INTO partner_users (
                partner_id, email, name, role, magic_token, magic_token_expires_at
            ) VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, email, name, role
        """,
            _uid(partner_id),
            user.email.lower(),
            user.name,
            user.role,
            magic_token,
            magic_expires
        )

    # Audit row — log_partner_activity is fire-and-forget
    try:
        from .partner_activity_logger import (
            log_partner_activity, PartnerEventType,
        )
        await log_partner_activity(
            partner_id=str(partner_id),
            event_type=PartnerEventType.PARTNER_UPDATED,
            target_type="partner_user",
            target_id=str(row["id"]),
            event_data={
                "action": "partner_user_created",
                "new_user_email": row["email"],
                "new_user_role": row["role"],
                "actor_email": admin.get("email") or admin.get("username"),
            },
            ip_address=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            request_path=str(request.url.path),
            request_method=request.method,
        )
    except Exception:
        logger.error("partner_user_create_audit_failed", exc_info=True)

    # Ed25519 attestation — privileged-action chain.
    create_attestation_failed = False
    create_bundle_id = None
    try:
        from .privileged_access_attestation import (
            create_privileged_access_attestation,
            PrivilegedAccessAttestationError,
        )
        # Anchor the chain to a synthetic site_id keyed on partner_id;
        # partner-org events don't have an obvious site to anchor at,
        # so we use a stable namespace prefix. Auditor kit walks
        # partner-event chains by this namespace.
        anchor_site_id = f"partner_org:{partner_id}"
        async with admin_connection(pool) as att_conn:
            try:
                att = await create_privileged_access_attestation(
                    att_conn,
                    site_id=anchor_site_id,
                    event_type="partner_user_created",
                    actor_email=(
                        admin.get("email") or admin.get("username")
                        or "unknown"
                    ),
                    reason=(
                        f"partner_user {row['email']} created with "
                        f"role={row['role']}"
                    ),
                    origin_ip=(request.client.host
                               if request.client else None),
                    approvals=[{
                        "stage": "applied",
                        "actor": admin.get("email") or admin.get("username"),
                        "new_user_id": str(row["id"]),
                        "new_user_email": row["email"],
                        "new_user_role": row["role"],
                    }],
                )
                create_bundle_id = att.get("bundle_id")
            except PrivilegedAccessAttestationError as e:
                create_attestation_failed = True
                logger.error(
                    "partner_user_create_attestation_failed",
                    exc_info=True,
                    extra={"partner_id": partner_id, "new_user_email": row["email"]},
                )
    except Exception:
        create_attestation_failed = True
        logger.error(
            "partner_user_create_attestation_unexpected",
            exc_info=True,
            extra={"partner_id": partner_id},
        )

    # Operator-visibility alert (chain-gap escalation pattern)
    try:
        from .email_alerts import send_operator_alert
        op_severity = ("P0-CHAIN-GAP" if create_attestation_failed
                       else "P2")
        op_suffix = (" [ATTESTATION-MISSING]"
                     if create_attestation_failed else "")
        send_operator_alert(
            event_type="partner_user_created",
            severity=op_severity,
            summary=(
                f"Partner user created: {row['email']} (role={row['role']})"
                f" in partner_id={partner_id}{op_suffix}"
            ),
            details={
                "partner_id": str(partner_id),
                "new_user_id": str(row["id"]),
                "new_user_email": row["email"],
                "new_user_role": row["role"],
                "attestation_bundle_id": create_bundle_id,
                "attestation_failed": create_attestation_failed,
            },
            site_id=f"partner_org:{partner_id}",
            actor_email=admin.get("email") or admin.get("username"),
        )
    except Exception:
        logger.error(
            "operator_alert_dispatch_failed_partner_user_create",
            exc_info=True,
        )

    return {
        "id": str(row['id']),
        "email": row['email'],
        "name": row['name'],
        "role": row['role'],
        "magic_link": f"{os.getenv('FRONTEND_URL', 'https://www.osiriscare.net')}/partner/login?token={magic_token}",
        "expires": magic_expires.isoformat(),
        "attestation_bundle_id": create_bundle_id,
    }


@router.post("/{partner_id}/users/{user_id}/magic-link")
async def generate_user_magic_link(partner_id: str, user_id: str, admin: dict = Depends(require_admin)):
    """Generate a new magic login link for a partner user (admin only)."""
    pool = await get_pool()

    magic_token = generate_magic_token()
    magic_expires = datetime.now(timezone.utc) + timedelta(hours=24)

    async with admin_connection(pool) as conn:
        result = await conn.fetchrow("""
            UPDATE partner_users
            SET magic_token = $1, magic_token_expires_at = $2
            WHERE id = $3 AND partner_id = $4
            RETURNING email
        """, magic_token, magic_expires, _uid(user_id), _uid(partner_id))

        if not result:
            raise HTTPException(status_code=404, detail="User not found")

    return {
        "magic_link": f"{os.getenv('FRONTEND_URL', 'https://www.osiriscare.net')}/partner/login?token={magic_token}",
        "expires": magic_expires.isoformat(),
        "email": result['email'],
    }


# =============================================================================
# DISCOVERY & CREDENTIAL ENDPOINTS
# =============================================================================

class PartnerCredentialCreate(BaseModel):
    """Model for creating site credentials."""
    name: str
    credential_type: str  # domain_admin, service_account, local_admin
    domain: Optional[str] = None
    username: str
    password: str


class DiscoveredAssetUpdate(BaseModel):
    """Model for updating a discovered asset."""
    monitoring_status: Optional[str] = None  # discovered, monitored, ignored
    asset_type: Optional[str] = None


@router.get("/me/audit-log")
async def get_my_audit_log(
    request: Request,
    event_category: Optional[str] = Query(None, description="Filter by category: auth/admin/site/credential/etc"),
    days: int = Query(30, ge=1, le=2555, description="Lookback window in days (max 7 years)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    partner: dict = require_partner_role("admin", "tech", "billing"),
):
    """Return the partner's own activity audit log (Tier 3 H7-partner).

    Mirrors the client-side `GET /api/client/audit-log` shipped in Batch 7.
    Self-service: a partner sees only their own org's audit trail, not
    other partners'. Categories: auth, admin, site, provision, credential,
    asset, discovery, learning. The full event types live in
    `partner_activity_logger.PartnerEventType`.

    Lookback window can be set up to 2,555 days (7 years) to satisfy the
    HIPAA §164.316(b)(2)(i) retention requirement during periodic audits.
    """
    pool = await get_pool()

    # Get the recent activity rows scoped to this partner
    rows = await get_partner_activity(
        partner_id=str(partner['id']),
        event_category=event_category,
        limit=limit,
        offset=offset,
    )

    # Filter by lookback window — get_partner_activity doesn't support a
    # date filter, so we filter on the application side. The OR clause keeps
    # rows whose created_at is missing (shouldn't happen but defensive).
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    filtered = [
        r for r in rows
        if not r.get("created_at") or
        datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) >= cutoff
    ]

    # Total count of all events for this partner (for pagination UI)
    async with admin_connection(pool) as conn:
        total = await conn.fetchval(
            """
            SELECT COUNT(*) FROM partner_activity_log
            WHERE partner_id = $1::uuid
              AND created_at >= $2
            """,
            str(partner['id']),
            cutoff,
        ) or 0

    return {
        "partner_id": str(partner['id']),
        "partner_name": partner.get('name'),
        "events": filtered,
        "total": total,
        "limit": limit,
        "offset": offset,
        "days_lookback": days,
        "category_filter": event_category,
    }


@router.get("/me/sites/{site_id}")
async def get_partner_site_detail(request: Request, site_id: str, partner=Depends(require_partner)):
    """Get detailed site info including assets and credentials."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify site belongs to partner
        site = await conn.fetchrow("""
            SELECT s.*, p.brand_name as partner_brand
            FROM sites s
            JOIN partners p ON p.id = s.partner_id
            WHERE s.site_id = $1 AND s.partner_id = $2
        """, site_id, partner['id'])

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get discovered assets
        assets = await conn.fetch("""
            SELECT id, ip_address, hostname, asset_type, os_info,
                   confidence, discovery_method, open_ports, detected_services,
                   monitoring_status, last_seen_at, last_check_status
            FROM discovered_assets
            WHERE site_id = (SELECT id FROM sites WHERE site_id = $1)
            ORDER BY
                CASE asset_type
                    WHEN 'domain_controller' THEN 1
                    WHEN 'sql_server' THEN 2
                    WHEN 'backup_server' THEN 3
                    WHEN 'file_server' THEN 4
                    ELSE 10
                END,
                hostname NULLS LAST
        """, site_id)

        # Get credentials (without password)
        credentials = await conn.fetch("""
            SELECT id, name, credential_type, domain, username,
                   is_primary, validation_status, last_validated_at
            FROM site_credentials
            WHERE site_id = (SELECT id FROM sites WHERE site_id = $1)
            ORDER BY is_primary DESC, name
        """, site_id)

        # Get recent discovery scans
        scans = await conn.fetch("""
            SELECT id, scan_type, triggered_by, started_at, completed_at,
                   status, assets_found, new_assets, error_message
            FROM discovery_scans
            WHERE site_id = (SELECT id FROM sites WHERE site_id = $1)
            ORDER BY started_at DESC
            LIMIT 10
        """, site_id)

        await log_partner_activity(
            partner_id=str(partner['id']),
            event_type=PartnerEventType.SITE_VIEWED,
            target_type="site",
            target_id=site_id,
            event_data={"clinic_name": site['clinic_name'], "asset_count": len(assets), "credential_count": len(credentials)},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            request_path=str(request.url.path),
            request_method=request.method,
        )

        return {
            'site': {
                'site_id': site['site_id'],
                'clinic_name': site['clinic_name'],
                'status': site['status'],
                'tier': site['tier'],
                'onboarding_stage': site['onboarding_stage'],
                'partner_brand': site['partner_brand'],
            },
            'assets': [
                {
                    'id': str(a['id']),
                    'ip_address': str(a['ip_address']),
                    'hostname': a['hostname'],
                    'asset_type': a['asset_type'] or 'unknown',
                    'os_info': a['os_info'],
                    'confidence': a['confidence'],
                    'discovery_method': a['discovery_method'],
                    'open_ports': a['open_ports'] or [],
                    'detected_services': a['detected_services'] or {},
                    'monitoring_status': a['monitoring_status'],
                    'last_seen_at': a['last_seen_at'].isoformat() if a['last_seen_at'] else None,
                    'last_check_status': a['last_check_status'],
                }
                for a in assets
            ],
            'credentials': [
                {
                    'id': str(c['id']),
                    'name': c['name'],
                    'credential_type': c['credential_type'],
                    'domain': c['domain'],
                    'username': c['username'],
                    'is_primary': c['is_primary'],
                    'validation_status': c['validation_status'],
                    'last_validated_at': c['last_validated_at'].isoformat() if c['last_validated_at'] else None,
                }
                for c in credentials
            ],
            'recent_scans': [
                {
                    'id': str(s['id']),
                    'scan_type': s['scan_type'],
                    'triggered_by': s['triggered_by'],
                    'started_at': s['started_at'].isoformat() if s['started_at'] else None,
                    'completed_at': s['completed_at'].isoformat() if s['completed_at'] else None,
                    'status': s['status'],
                    'assets_found': s['assets_found'],
                    'new_assets': s['new_assets'],
                    'error_message': s['error_message'],
                }
                for s in scans
            ],
            'asset_count': len(assets),
            'credential_count': len(credentials),
        }


@router.post("/me/sites/{site_id}/credentials")
async def add_site_credentials(
    request: Request,
    site_id: str,
    credential: PartnerCredentialCreate,
    partner: dict = require_partner_role("admin", "tech")
):
    """Add credentials for a site."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify site belongs to partner and get internal ID
        site = await conn.fetchrow("""
            SELECT id FROM sites
            WHERE site_id = $1 AND partner_id = $2
        """, site_id, partner['id'])

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Encrypt password using Fernet
        from .oauth_login import encrypt_secret
        encrypted = encrypt_secret(credential.password)

        # Check if this is the first credential (make it primary)
        existing = await conn.fetchval("""
            SELECT COUNT(*) FROM site_credentials WHERE site_id = $1
        """, site['id'])
        is_primary = existing == 0

        row = await conn.fetchrow("""
            INSERT INTO site_credentials (
                site_id, name, credential_type, domain, username,
                password_encrypted, is_primary
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, name, credential_type, validation_status
        """,
            site['id'],
            credential.name,
            credential.credential_type,
            credential.domain,
            credential.username,
            encrypted,
            is_primary
        )

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.CREDENTIAL_ADDED,
        target_type="credential",
        target_id=str(row['id']),
        event_data={"site_id": site_id, "credential_name": credential.name, "credential_type": credential.credential_type},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        'id': str(row['id']),
        'name': row['name'],
        'credential_type': row['credential_type'],
        'validation_status': row['validation_status'],
        'is_primary': is_primary,
        'message': 'Credential saved. Use /validate to test connectivity.',
    }


@router.post("/me/sites/{site_id}/credentials/{credential_id}/validate")
async def validate_credential(
    request: Request,
    site_id: str,
    credential_id: str,
    partner: dict = require_partner_role("admin", "tech")
):
    """Validate a credential by testing connectivity."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify site belongs to partner
        cred = await conn.fetchrow("""
            SELECT sc.*, s.site_id
            FROM site_credentials sc
            JOIN sites s ON s.id = sc.site_id
            WHERE sc.id = $1 AND s.partner_id = $2
        """, _uid(credential_id), partner['id'])

        if not cred:
            raise HTTPException(status_code=404, detail="Credential not found")

        # Update validation status to pending
        await conn.execute("""
            UPDATE site_credentials
            SET validation_status = 'pending',
                last_validated_at = NOW()
            WHERE id = $1
        """, _uid(credential_id))

        # Find an active appliance to run the validation
        appliance = await conn.fetchrow("""
            SELECT appliance_id FROM site_appliances
            WHERE site_id = $1 AND status = 'online' AND deleted_at IS NULL
            ORDER BY last_checkin DESC NULLS LAST
            LIMIT 1
        """, site_id)

        if not appliance:
            appliance = await conn.fetchrow("""
                SELECT appliance_id FROM site_appliances
                WHERE site_id = $1 AND deleted_at IS NULL
                ORDER BY last_checkin DESC NULLS LAST
                LIMIT 1
            """, site_id)

        validation_result = {
            'can_connect': None,
            'can_read_ad': None,
            'is_domain_admin': None,
            'servers_found': [],
            'servers_accessible': [],
            'warnings': [],
            'errors': [],
        }

        if appliance:
            import secrets
            from datetime import timedelta
            from .order_signing import sign_admin_order

            order_id = f"ORD-{secrets.token_hex(8).upper()}"
            now_ts = datetime.now(timezone.utc)
            expires_at = now_ts + timedelta(hours=1)
            validate_params = {
                'credential_id': str(cred['id']),
                'hostname': cred.get('hostname', ''),
                'credential_type': cred['credential_type'],
            }

            nonce, signature, signed_payload = sign_admin_order(
                order_id, 'validate_credential', validate_params, now_ts, expires_at,
                target_appliance_id=appliance['appliance_id'],
            )

            await conn.execute("""
                INSERT INTO admin_orders (
                    order_id, appliance_id, site_id, order_type,
                    parameters, priority, status, created_at, expires_at,
                    nonce, signature, signed_payload
                ) VALUES ($1, $2, $3, 'validate_credential', $4::jsonb, 1, 'pending', $5, $6, $7, $8, $9)
            """,
                order_id,
                appliance['appliance_id'],
                site_id,
                json.dumps(validate_params),
                now_ts,
                expires_at,
                nonce,
                signature,
                signed_payload,
            )
            validation_result['warnings'].append('Validation order queued to appliance')
        else:
            validation_result['warnings'].append('No appliance available — validation will run on next checkin')
            validation_result['errors'].append('No active appliance found for this site')

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.CREDENTIAL_VALIDATED,
        target_type="credential",
        target_id=str(credential_id),
        event_data={"site_id": site_id, "validation_status": "pending"},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        'credential_id': credential_id,
        'validation_status': 'pending',
        'result': validation_result,
        'message': 'Validation queued. Appliance will test on next sync.',
    }


@router.delete("/me/sites/{site_id}/credentials/{credential_id}")
async def delete_credential(
    request: Request,
    site_id: str,
    credential_id: str,
    partner: dict = require_partner_role("admin", "tech")
):
    """Delete a site credential."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        result = await conn.execute("""
            DELETE FROM site_credentials sc
            USING sites s
            WHERE sc.id = $1
            AND sc.site_id = s.id
            AND s.partner_id = $2
        """, _uid(credential_id), partner['id'])

        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Credential not found")

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.CREDENTIAL_DELETED,
        target_type="credential",
        target_id=str(credential_id),
        event_data={"site_id": site_id},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {'status': 'deleted', 'credential_id': credential_id}


@router.get("/me/sites/{site_id}/drift-config")
async def get_partner_drift_config(site_id: str, partner=Depends(require_partner)):
    """Get drift scan configuration for a partner-managed site."""
    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify partner owns this site
        owner = await conn.fetchval(
            "SELECT partner_id FROM sites WHERE site_id = $1", site_id)
        if str(owner) != str(partner["id"]):
            raise HTTPException(status_code=404, detail="Site not found")

        rows = await conn.fetch(
            "SELECT check_type, enabled, notes FROM site_drift_config WHERE site_id = $1 ORDER BY check_type",
            site_id)
        if not rows:
            rows = await conn.fetch(
                "SELECT check_type, enabled, notes FROM site_drift_config WHERE site_id = '__defaults__' ORDER BY check_type")

        def _platform(ct):
            if ct.startswith("macos_"): return "macos"
            if ct.startswith("linux_"): return "linux"
            return "windows"

        checks = [{"check_type": r["check_type"], "enabled": r["enabled"], "platform": _platform(r["check_type"]), "notes": r["notes"] or ""} for r in rows]
    return {"site_id": site_id, "checks": checks}


@router.put("/me/sites/{site_id}/drift-config")
async def update_partner_drift_config(
    site_id: str,
    body: dict,
    request: Request,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Update drift scan configuration for a partner-managed site.

    RBAC: admin or tech role only. A billing-scoped partner user has no
    business flipping compliance scan configuration, and the Session 203
    round-table flagged this as a gap (the prior dep was plain
    `require_partner` which defaulted everyone to admin).
    """
    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        owner = await conn.fetchval(
            "SELECT partner_id FROM sites WHERE site_id = $1", site_id)
        if str(owner) != str(partner["id"]):
            raise HTTPException(status_code=404, detail="Site not found")

        checks = body.get("checks", [])

        # Safety bounds: prevent disabling all checks or critical checks
        from .routes import _validate_drift_config_checks
        _validate_drift_config_checks(checks)

        async with conn.transaction():
            for item in checks:
                await conn.execute("""
                    INSERT INTO site_drift_config (site_id, check_type, enabled, modified_by, modified_at)
                    VALUES ($1, $2, $3, $4, NOW())
                    ON CONFLICT (site_id, check_type)
                    DO UPDATE SET enabled = $3, modified_by = $4, modified_at = NOW()
                """, site_id, item["check_type"], item["enabled"], f"partner:{partner.get('org_name', partner['id'])}")

    await log_partner_activity(
        partner_id=str(partner["id"]),
        event_type=PartnerEventType.DRIFT_CONFIG_UPDATED,
        target_type="site",
        target_id=site_id,
        event_data={
            "check_count": len(checks),
            "checks": [
                {"check_type": c["check_type"], "enabled": c["enabled"]}
                for c in checks
            ],
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )
    return {"status": "ok", "site_id": site_id, "updated": len(checks)}


# =============================================================================
# MAINTENANCE MODE (partner-scoped)
# =============================================================================

class PartnerMaintenanceRequest(BaseModel):
    duration_hours: float
    reason: str


@router.put("/me/sites/{site_id}/maintenance")
async def set_partner_maintenance(
    site_id: str,
    body: PartnerMaintenanceRequest,
    request: Request,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Set a maintenance window for a partner-managed site."""
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=422, detail="reason is required")
    if body.duration_hours < 0.5 or body.duration_hours > 48:
        raise HTTPException(status_code=422, detail="duration_hours must be between 0.5 and 48")

    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        owner = await conn.fetchval(
            "SELECT partner_id FROM sites WHERE site_id = $1", site_id)
        if str(owner) != str(partner["id"]):
            raise HTTPException(status_code=404, detail="Site not found")

        set_by = f"partner:{partner.get('name', partner['id'])}"
        await conn.execute("""
            UPDATE sites
            SET maintenance_until = NOW() + make_interval(hours => $1),
                maintenance_reason = $2,
                maintenance_set_by = $3
            WHERE site_id = $4
        """, body.duration_hours, body.reason.strip(), set_by, site_id)

    await log_partner_activity(
        partner_id=str(partner["id"]),
        event_type=PartnerEventType.MAINTENANCE_WINDOW_SET,
        target_type="site",
        target_id=site_id,
        event_data={
            "duration_hours": body.duration_hours,
            "reason": body.reason.strip(),
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    logger.info(
        "Partner maintenance window set",
        extra={
            "site_id": site_id,
            "duration_hours": body.duration_hours,
            "partner_id": str(partner['id']),
        },
    )

    return {"status": "ok", "site_id": site_id, "duration_hours": body.duration_hours}


@router.delete("/me/sites/{site_id}/maintenance")
async def cancel_partner_maintenance(
    site_id: str,
    request: Request,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Cancel an active maintenance window for a partner-managed site."""
    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        owner = await conn.fetchval(
            "SELECT partner_id FROM sites WHERE site_id = $1", site_id)
        if str(owner) != str(partner["id"]):
            raise HTTPException(status_code=404, detail="Site not found")

        await conn.execute("""
            UPDATE sites
            SET maintenance_until = NULL,
                maintenance_reason = NULL,
                maintenance_set_by = NULL
            WHERE site_id = $1
        """, site_id)

    await log_partner_activity(
        partner_id=str(partner["id"]),
        event_type=PartnerEventType.MAINTENANCE_WINDOW_CANCELLED,
        target_type="site",
        target_id=site_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    logger.info(
        "Partner maintenance window cancelled",
        extra={
            "site_id": site_id,
            "partner_id": str(partner['id']),
        },
    )

    return {"status": "ok", "site_id": site_id, "maintenance_until": None}


@router.get("/me/onboarding")
async def get_partner_onboarding(request: Request, partner=Depends(require_partner)):
    """Get onboarding pipeline for partner's sites (excludes active/compliant)."""
    from datetime import timezone
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        rows = await conn.fetch("""
            SELECT s.site_id, s.clinic_name, s.contact_name, s.contact_email,
                   s.onboarding_stage, s.notes, s.blockers, s.created_at,
                   s.lead_at, s.discovery_at, s.proposal_at, s.contract_at,
                   s.intake_at, s.creds_at, s.shipped_at, s.received_at,
                   s.connectivity_at, s.scanning_at, s.baseline_at, s.active_at,
                   COUNT(sa.id) as appliance_count,
                   MAX(sa.last_checkin) as last_checkin,
                   (SELECT COUNT(*) FROM site_credentials sc WHERE sc.site_id = s.site_id) as credential_count
            FROM sites s
            LEFT JOIN site_appliances sa ON s.site_id = sa.site_id AND sa.deleted_at IS NULL
            WHERE s.partner_id = $1 AND s.status != 'inactive'
            GROUP BY s.site_id, s.clinic_name, s.contact_name, s.contact_email,
                     s.onboarding_stage, s.notes, s.blockers, s.created_at,
                     s.lead_at, s.discovery_at, s.proposal_at, s.contract_at,
                     s.intake_at, s.creds_at, s.shipped_at, s.received_at,
                     s.connectivity_at, s.scanning_at, s.baseline_at, s.active_at
            ORDER BY s.created_at DESC
        """, partner['id'])

        stage_progress = {
            'lead': 10, 'discovery': 20, 'proposal': 30, 'contract': 40,
            'intake': 50, 'creds': 60, 'shipped': 70, 'received': 80,
            'connectivity': 85, 'scanning': 90, 'baseline': 95,
            'compliant': 100, 'active': 100,
        }
        stage_col_map = {
            'lead': 'lead_at', 'discovery': 'discovery_at', 'proposal': 'proposal_at',
            'contract': 'contract_at', 'intake': 'intake_at', 'creds': 'creds_at',
            'shipped': 'shipped_at', 'received': 'received_at', 'connectivity': 'connectivity_at',
            'scanning': 'scanning_at', 'baseline': 'baseline_at', 'active': 'active_at',
        }

        from datetime import datetime
        now = datetime.now(timezone.utc)
        pipeline = []
        for row in rows:
            stage = row['onboarding_stage'] or 'lead'
            ts_col = stage_col_map.get(stage)
            stage_entered = row[ts_col] if ts_col and row.get(ts_col) else row['created_at']
            days_in_stage = (now - stage_entered).days if stage_entered else 0

            # Auto-detect blockers
            blockers = []
            if row['blockers']:
                try:
                    blockers = json.loads(row['blockers']) if isinstance(row['blockers'], str) else row['blockers']
                except (json.JSONDecodeError, TypeError):
                    pass

            # Add system-detected blockers
            if stage in ('creds', 'intake') and row['credential_count'] == 0:
                blockers.append("No credentials configured")
            if stage in ('connectivity', 'scanning') and row['appliance_count'] == 0:
                blockers.append("No appliance connected")
            if stage in ('received', 'shipped') and row['last_checkin'] is None:
                blockers.append("Appliance has not checked in yet")

            pipeline.append({
                'site_id': row['site_id'],
                'clinic_name': row['clinic_name'],
                'contact_name': row['contact_name'],
                'contact_email': row['contact_email'],
                'stage': stage,
                'progress_percent': stage_progress.get(stage, 0),
                'days_in_stage': days_in_stage,
                'stage_entered_at': stage_entered.isoformat() if stage_entered else None,
                'blockers': blockers,
                'notes': row['notes'],
                'appliance_count': row['appliance_count'],
                'credential_count': row['credential_count'],
                'last_checkin': row['last_checkin'].isoformat() if row['last_checkin'] else None,
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
            })

    # Split into active pipeline vs completed
    in_progress = [s for s in pipeline if s['stage'] not in ('active', 'compliant')]
    completed = [s for s in pipeline if s['stage'] in ('active', 'compliant')]

    return {
        'pipeline': in_progress,
        'completed': completed,
        'total': len(pipeline),
        'in_progress_count': len(in_progress),
        'completed_count': len(completed),
    }


@router.post("/me/sites/{site_id}/trigger-checkin")
async def trigger_site_checkin(
    request: Request,
    site_id: str,
    partner: dict = require_partner_role("admin", "tech")
):
    """Request immediate checkin from site's appliance (creates force_checkin order)."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify site belongs to partner
        site = await conn.fetchrow(
            "SELECT id FROM sites WHERE site_id = $1 AND partner_id = $2",
            site_id, partner['id']
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Find appliance
        appliance = await conn.fetchrow("""
            SELECT appliance_id FROM site_appliances
            WHERE site_id = $1 AND deleted_at IS NULL
            ORDER BY last_checkin DESC NULLS LAST LIMIT 1
        """, site_id)
        if not appliance:
            raise HTTPException(status_code=400, detail="No appliance connected to this site")

        # Create force_checkin order
        import uuid
        from datetime import timedelta
        order_id = str(uuid.uuid4())
        now_ts = datetime.now(timezone.utc)
        exp = now_ts + timedelta(hours=1)

        await conn.execute("""
            INSERT INTO admin_orders (id, appliance_id, order_type, parameters, status, expires_at, created_at)
            VALUES ($1, $2, 'force_checkin', '{}'::jsonb, 'pending', $3, $4)
        """, uuid.UUID(order_id), appliance['appliance_id'], exp, now_ts)

    return {'status': 'queued', 'order_id': order_id, 'message': 'Checkin request queued for next poll cycle.'}


@router.get("/me/sites/{site_id}/assets")
async def list_site_assets(
    site_id: str,
    status: Optional[str] = None,
    partner=Depends(require_partner)
):
    """List discovered assets for a site."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify site belongs to partner
        site = await conn.fetchrow("""
            SELECT id FROM sites WHERE site_id = $1 AND partner_id = $2
        """, site_id, partner['id'])

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        query = """
            SELECT id, ip_address, hostname, asset_type, os_info,
                   confidence, discovery_method, open_ports, detected_services,
                   monitoring_status, last_seen_at, last_check_status, ad_info
            FROM discovered_assets
            WHERE site_id = $1
        """
        params = [site['id']]

        if status:
            query += " AND monitoring_status = $2"
            params.append(status)

        query += " ORDER BY asset_type, hostname NULLS LAST"

        assets = await conn.fetch(query, *params)

        return {
            'assets': [
                {
                    'id': str(a['id']),
                    'ip_address': str(a['ip_address']),
                    'hostname': a['hostname'],
                    'asset_type': a['asset_type'] or 'unknown',
                    'os_info': a['os_info'],
                    'confidence': a['confidence'],
                    'discovery_method': a['discovery_method'],
                    'open_ports': a['open_ports'] or [],
                    'detected_services': a['detected_services'] or {},
                    'monitoring_status': a['monitoring_status'],
                    'last_seen_at': a['last_seen_at'].isoformat() if a['last_seen_at'] else None,
                    'last_check_status': a['last_check_status'],
                    'ad_info': a['ad_info'],
                }
                for a in assets
            ],
            'count': len(assets),
        }


@router.patch("/me/sites/{site_id}/assets/{asset_id}")
async def update_asset(
    request: Request,
    site_id: str,
    asset_id: str,
    update: DiscoveredAssetUpdate,
    partner: dict = require_partner_role("admin", "tech")
):
    """Update a discovered asset (e.g., set monitoring status)."""
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify ownership
        asset = await conn.fetchrow("""
            SELECT da.id FROM discovered_assets da
            JOIN sites s ON s.id = da.site_id
            WHERE da.id = $1 AND s.partner_id = $2
        """, _uid(asset_id), partner['id'])

        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        updates = []
        values = []
        param_num = 1

        if update.monitoring_status:
            if update.monitoring_status not in ('discovered', 'monitored', 'ignored'):
                raise HTTPException(status_code=400, detail="Invalid monitoring_status")
            updates.append(f"monitoring_status = ${param_num}")
            values.append(update.monitoring_status)
            param_num += 1

        if update.asset_type:
            updates.append(f"asset_type = ${param_num}")
            values.append(update.asset_type)
            param_num += 1

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        updates.append(f"updated_at = ${param_num}")
        values.append(datetime.now(timezone.utc))
        param_num += 1

        values.append(_uid(asset_id))

        query = f"""
            UPDATE discovered_assets
            SET {', '.join(updates)}
            WHERE id = ${param_num}
            RETURNING id, monitoring_status, asset_type
        """

        result = await conn.fetchrow(query, *values)

    await log_partner_activity(
        partner_id=str(partner['id']),
        event_type=PartnerEventType.ASSET_UPDATED,
        target_type="asset",
        target_id=str(asset_id),
        event_data={"site_id": site_id, "monitoring_status": result['monitoring_status'], "asset_type": result['asset_type']},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {
        'id': str(result['id']),
        'monitoring_status': result['monitoring_status'],
        'asset_type': result['asset_type'],
        'status': 'updated',
    }


@router.post("/me/sites/{site_id}/discovery/trigger")
async def trigger_discovery(
    request: Request,
    site_id: str,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Trigger a network discovery scan for a site.

    RBAC: admin or tech role only. Billing-scoped partner users should
    not be able to trigger scans — Session 203 round-table fix.
    """
    pool = await get_pool()

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify site belongs to partner
        site = await conn.fetchrow("""
            SELECT id FROM sites WHERE site_id = $1 AND partner_id = $2
        """, site_id, partner['id'])

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Audit note: the `log_partner_activity` call for the discovery
        # event already exists further down — once we know whether an
        # appliance was queued or the scan was left pending. That call
        # captures scan_id + order_id + status, which is richer than a
        # bare "discovery triggered" here. So no additional audit call
        # at this early point.

        # Get active appliance for this site
        appliance = await conn.fetchrow("""
            SELECT appliance_id FROM site_appliances
            WHERE site_id = $1 AND status = 'online' AND deleted_at IS NULL
            ORDER BY last_checkin DESC NULLS LAST
            LIMIT 1
        """, site_id)

        if not appliance:
            # Try any appliance if none online
            appliance = await conn.fetchrow("""
                SELECT appliance_id FROM site_appliances
                WHERE site_id = $1 AND deleted_at IS NULL
                ORDER BY last_checkin DESC NULLS LAST
                LIMIT 1
            """, site_id)

        # Create discovery scan record
        scan = await conn.fetchrow("""
            INSERT INTO discovery_scans (site_id, scan_type, triggered_by)
            VALUES ($1, 'full', 'manual')
            RETURNING id, started_at
        """, site['id'])

        # Queue order to appliance to run discovery
        if appliance:
            import secrets
            from datetime import timedelta
            from .order_signing import sign_admin_order
            order_id = f"ORD-{secrets.token_hex(8).upper()}"
            now = datetime.now(timezone.utc)
            expires_at = now + timedelta(hours=24)  # Discovery can take time
            discovery_params = {
                'scan_id': str(scan['id']),
                'scan_type': 'full',
                'triggered_by': partner.get('email', 'partner')
            }

            nonce, signature, signed_payload = sign_admin_order(
                order_id, 'run_discovery', discovery_params, now, expires_at,
                target_appliance_id=appliance['appliance_id'],
            )

            await conn.execute("""
                INSERT INTO admin_orders (
                    order_id, appliance_id, site_id, order_type,
                    parameters, priority, status, created_at, expires_at,
                    nonce, signature, signed_payload
                ) VALUES ($1, $2, $3, 'run_discovery', $4::jsonb, 1, 'pending', $5, $6, $7, $8, $9)
            """,
                order_id,
                appliance['appliance_id'],
                site_id,
                json.dumps(discovery_params),
                now,
                expires_at,
                nonce,
                signature,
                signed_payload,
            )

            await log_partner_activity(
                partner_id=str(partner['id']),
                event_type=PartnerEventType.DISCOVERY_TRIGGERED,
                target_type="site",
                target_id=site_id,
                event_data={"scan_id": str(scan['id']), "order_id": order_id, "status": "queued"},
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent", "")[:500],
                request_path=str(request.url.path),
                request_method=request.method,
            )

            return {
                'scan_id': str(scan['id']),
                'order_id': order_id,
                'status': 'queued',
                'started_at': scan['started_at'].isoformat(),
                'message': 'Discovery scan queued. Appliance will execute on next check-in.',
            }
        else:
            await log_partner_activity(
                partner_id=str(partner['id']),
                event_type=PartnerEventType.DISCOVERY_TRIGGERED,
                target_type="site",
                target_id=site_id,
                event_data={"scan_id": str(scan['id']), "status": "pending_no_appliance"},
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent", "")[:500],
                request_path=str(request.url.path),
                request_method=request.method,
            )

            return {
                'scan_id': str(scan['id']),
                'status': 'pending',
                'started_at': scan['started_at'].isoformat(),
                'message': 'Discovery scan created but no appliance available. Will execute when appliance comes online.',
            }


# =============================================================================
# PARTNER AUTHENTICATION ENDPOINTS
# =============================================================================

class MagicTokenValidate(BaseModel):
    """Model for validating a magic link token."""
    token: str


@router.post("/auth/magic")
async def validate_magic_link(request: MagicTokenValidate):
    """Validate a magic link token and return API key for partner login.

    SECURITY: Token is sent in request body (not URL) to avoid exposure in logs.

    Args:
        request: Contains the magic link token

    Returns:
        Partner info and API key on success
    """
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Find user with this magic token
        user = await conn.fetchrow("""
            SELECT pu.id, pu.partner_id, pu.email, pu.name, pu.role,
                   pu.magic_token_expires_at, p.api_key_hash, p.name as partner_name,
                   p.slug, p.status as partner_status
            FROM partner_users pu
            JOIN partners p ON p.id = pu.partner_id
            WHERE pu.magic_token = $1
        """, request.token)

        if not user:
            raise HTTPException(status_code=401, detail="Invalid or expired magic link")

        # Check expiration
        if user['magic_token_expires_at'] and user['magic_token_expires_at'] < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Magic link has expired")

        # Check partner is active
        if user['partner_status'] != 'active':
            raise HTTPException(status_code=403, detail="Partner account is not active")

        # Clear the magic token (single use)
        await conn.execute("""
            UPDATE partner_users
            SET magic_token = NULL, magic_token_expires_at = NULL, last_login = NOW()
            WHERE id = $1
        """, user['id'])

        # Generate a new API key for this session
        api_key = generate_api_key()
        api_key_hash = hash_api_key(api_key)

        # Update the partner's API key (1-year expiry)
        from datetime import timedelta as _td
        _expires = datetime.now(timezone.utc) + _td(days=365)
        await conn.execute("""
            UPDATE partners SET api_key_hash = $1, api_key_created_at = NOW(),
                api_key_expires_at = $3 WHERE id = $2
        """, api_key_hash, user['partner_id'], _expires)

    return {
        "success": True,
        "api_key": api_key,
        "partner": {
            "id": str(user['partner_id']),
            "name": user['partner_name'],
            "slug": user['slug'],
        },
        "user": {
            "id": str(user['id']),
            "email": user['email'],
            "name": user['name'],
            "role": user['role'],
        },
    }


# =============================================================================
# NOTIFICATION ENDPOINTS
# =============================================================================


@router.get("/me/notifications")
async def get_partner_notifications(
    partner: dict = Depends(require_partner),
):
    """Get partner notifications (newest 50), with unread count."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        rows = await conn.fetch(
            """SELECT id, org_id, notification_type, summary, created_at, read_at,
                      escalated_to_admin_at
               FROM partner_notifications
               WHERE partner_id = $1
               ORDER BY created_at DESC
               LIMIT 50""",
            partner["id"],
        )
        unread_count = sum(1 for r in rows if not r["read_at"])
        return {
            "notifications": [
                {
                    "id": str(r["id"]),
                    "org_id": str(r["org_id"]) if r["org_id"] else None,
                    "notification_type": r["notification_type"],
                    "summary": r["summary"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "is_read": r["read_at"] is not None,
                }
                for r in rows
            ],
            "unread_count": unread_count,
        }


@router.put("/me/notifications/{notification_id}/read")
async def mark_partner_notification_read(
    notification_id: str,
    partner: dict = Depends(require_partner),
):
    """Mark a partner notification as read."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        await conn.execute(
            """UPDATE partner_notifications SET read_at = NOW()
               WHERE id = $1 AND partner_id = $2 AND read_at IS NULL""",
            notification_id, partner["id"],
        )
    return {"status": "ok"}


# =============================================================================
# P-F5 — Partner Portfolio Attestation Letter (round-table 2026-05-08)
# =============================================================================
# Greg-the-MSP-owner's website-trust-badge gap. Aggregate-only PDF
# proving "MSP X runs N HIPAA-grade clinics on the OsirisCare
# substrate" — without leaking which clinics. NO PHI, NO clinic
# names. Counts + chain roots only.

@router.get("/me/portfolio-attestation")
async def issue_partner_portfolio_attestation_pdf(
    request: Request,
    partner: dict = require_partner_role("admin"),
):
    """Issue + stream the Portfolio Attestation PDF. admin-role
    only (CLAUDE.md RT31 — partner-org-state class). Each call
    issues a NEW attestation (supersedes any prior active).

    Per-(partner, user) rate-limit 5/hr.
    """
    try:
        from .partner_portfolio_attestation import (
            issue_portfolio_attestation,
            html_to_pdf,
            UnableToIssuePortfolio,
        )
    except ImportError:
        from partner_portfolio_attestation import (  # type: ignore
            issue_portfolio_attestation,
            html_to_pdf,
            UnableToIssuePortfolio,
        )
    from fastapi.responses import Response
    try:
        from .shared import check_rate_limit
        from .tenant_middleware import admin_transaction
    except ImportError:
        from shared import check_rate_limit  # type: ignore
        from tenant_middleware import admin_transaction  # type: ignore

    pool = await get_pool()
    partner_id = str(partner["id"])
    caller_user_id = (
        partner.get("partner_user_id")
        or partner.get("user_id")
        or partner_id
    )

    allowed, retry_after_s = await check_rate_limit(
        site_id=partner_id,
        action="partner_portfolio_attestation_issue",
        window_seconds=3600,
        max_requests=5,
        caller_key=f"partner_user:{caller_user_id}",
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Portfolio attestation issuance is rate-limited "
                f"(5/hr per user). Retry in {retry_after_s}s."
            ),
            headers={"Retry-After": str(retry_after_s)},
        )

    async with admin_transaction(pool) as conn:
        try:
            result = await issue_portfolio_attestation(
                conn=conn,
                partner_id=partner_id,
                issued_by_user_id=str(caller_user_id) if caller_user_id else None,
                issued_by_email=partner.get("oauth_email") or partner.get("contact_email"),
            )
        except UnableToIssuePortfolio as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            cls_name = type(e).__name__
            if "UniqueViolation" in cls_name or "IntegrityError" in cls_name:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Another portfolio attestation issuance is in "
                        "flight for this partner. Retry in a moment."
                    ),
                )
            raise

    # Steve P1-A: WeasyPrint render off the event loop.
    import asyncio as _asyncio
    pdf_bytes = await _asyncio.to_thread(html_to_pdf, result["html"])

    safe_brand = "".join(
        c if c.isalnum() or c in "-_" else "-"
        for c in result["presenter_brand"]
    )[:80]
    issue_date = result["issued_at"].strftime("%Y-%m-%d")
    filename = f"portfolio-attestation-{safe_brand}-{issue_date}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Attestation-Hash": result["attestation_hash"],
            "X-Letter-Valid-Until": result["valid_until"].isoformat(),
        },
    )


# =============================================================================
# P-F7 — Technician Weekly Digest PDF (round-table 2026-05-08)
# =============================================================================
# Lisa-the-technician's "what'd you do this week" Monday review.
# Internal artifact — operational metrics only. NOT for forwarding
# to insurance carriers, auditors, or boards.

@router.get("/me/rollup/weekly.pdf")
async def issue_partner_weekly_digest_pdf(
    request: Request,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Stream the weekly digest PDF. admin OR tech role per
    CLAUDE.md RT31 (operational artifact, not org-state class)."""
    try:
        from .partner_weekly_digest import render_weekly_digest, html_to_pdf
    except ImportError:
        from partner_weekly_digest import render_weekly_digest, html_to_pdf  # type: ignore
    from fastapi.responses import Response
    try:
        from .shared import check_rate_limit
        from .tenant_middleware import admin_transaction
    except ImportError:
        from shared import check_rate_limit  # type: ignore
        from tenant_middleware import admin_transaction  # type: ignore

    pool = await get_pool()
    partner_id = str(partner["id"])
    caller_user_id = (
        partner.get("partner_user_id")
        or partner.get("user_id")
        or partner_id
    )
    technician_name = (
        partner.get("user_name")
        or partner.get("name")
        or "your team"
    )

    # 10/hr per (partner, user) — digest is cheaper to render than
    # the attestation letter; technicians may pull a few per day.
    allowed, retry_after_s = await check_rate_limit(
        site_id=partner_id,
        action="partner_weekly_digest",
        window_seconds=3600,
        max_requests=10,
        caller_key=f"partner_user:{caller_user_id}",
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Weekly digest rate-limited. Retry in {retry_after_s}s.",
            headers={"Retry-After": str(retry_after_s)},
        )

    # Coach-ultrathink-sweep D-2 fix-up 2026-05-08: Session 212 rule —
    # `admin_transaction()` for multi-statement admin paths.
    # render_weekly_digest issues 5+ admin queries (partner row,
    # active_orders, alerts_triaged, escalations, mttr_median, top
    # noisy sites). admin_connection allows PgBouncer to route the
    # second+ query to a different backend without
    # `app.is_admin='true'` (mig 234 default = false) → silent
    # zero-row results in production. admin_transaction pins the
    # SET LOCAL + queries to one backend.
    async with admin_transaction(pool) as conn:
        result = await render_weekly_digest(
            conn=conn,
            partner_id=partner_id,
            technician_name=technician_name,
        )

    # Steve P1-A: WeasyPrint off the event loop.
    import asyncio as _asyncio
    pdf_bytes = await _asyncio.to_thread(html_to_pdf, result["html"])

    safe_brand = "".join(
        c if c.isalnum() or c in "-_" else "-"
        for c in result["presenter_brand"]
    )[:80]
    week_date = result["week_end"].strftime("%Y-%m-%d")
    filename = f"weekly-digest-{safe_brand}-{week_date}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# =============================================================================
# P-F6 — BA Compliance Attestation + downstream-BAA roster
# =============================================================================
# Tony-the-MSP-HIPAA-lead's three-party BAA chain artifact. The
# Letter (rendered live from the active roster) lists each
# downstream BAA + cross-references monitored sites + cites the
# OsirisCare→MSP subcontractor BAA from partner_agreements.

@router.get("/me/ba-roster")
async def list_partner_baa_roster(
    partner: dict = require_partner_role("admin", "tech"),
):
    """Read the active downstream BAA roster (revoked_at IS NULL).
    admin OR tech roles may read; only admin may add/revoke."""
    try:
        from .partner_ba_compliance import list_active_roster
        from .tenant_middleware import admin_connection
    except ImportError:
        from partner_ba_compliance import list_active_roster  # type: ignore
        from tenant_middleware import admin_connection  # type: ignore
    pool = await get_pool()
    partner_id = str(partner["id"])
    async with admin_connection(pool) as conn:
        rows = await list_active_roster(conn, partner_id)
    return {
        "roster": [
            {
                "id": str(r["id"]),
                "counterparty_org_id": (
                    str(r["counterparty_org_id"])
                    if r.get("counterparty_org_id") else None
                ),
                "counterparty_practice_name": r.get("counterparty_practice_name"),
                "executed_at": r["executed_at"].isoformat() if r.get("executed_at") else None,
                "expiry_at": r["expiry_at"].isoformat() if r.get("expiry_at") else None,
                "scope": r["scope"],
                "signer_name": r["signer_name"],
                "signer_title": r["signer_title"],
                "signer_email": r.get("signer_email"),
                "attestation_bundle_id": (
                    str(r["attestation_bundle_id"])
                    if r.get("attestation_bundle_id") else None
                ),
            }
            for r in rows
        ],
    }


@router.post("/me/ba-roster")
async def add_partner_baa_to_roster(
    request: Request,
    partner: dict = require_partner_role("admin"),
):
    """Add a per-clinic BAA to the partner's roster. admin-only.

    Body: counterparty_org_id (UUID, optional) OR counterparty_practice_name (str, optional)
          executed_at (ISO date), expiry_at (ISO date or null),
          scope (≥20 chars), signer_name, signer_title,
          signer_email (optional), doc_sha256 (optional)
    """
    try:
        from .partner_ba_compliance import add_baa_to_roster, BAComplianceError
        from .tenant_middleware import admin_transaction
    except ImportError:
        from partner_ba_compliance import add_baa_to_roster, BAComplianceError  # type: ignore
        from tenant_middleware import admin_transaction  # type: ignore

    body = await request.json()
    org_id_raw = body.get("counterparty_org_id")
    practice_name = (body.get("counterparty_practice_name") or "").strip() or None
    executed_at_iso = body.get("executed_at")
    expiry_at_iso = body.get("expiry_at")
    scope = (body.get("scope") or "").strip()
    signer_name = (body.get("signer_name") or "").strip()
    signer_title = (body.get("signer_title") or "").strip()
    signer_email = (body.get("signer_email") or "").strip() or None
    doc_sha256 = (body.get("doc_sha256") or "").strip() or None

    if not executed_at_iso:
        raise HTTPException(status_code=400, detail="executed_at required (ISO date)")
    try:
        from datetime import datetime as _dt
        executed_at = _dt.fromisoformat(executed_at_iso.replace("Z", "+00:00"))
        expiry_at = (
            _dt.fromisoformat(expiry_at_iso.replace("Z", "+00:00"))
            if expiry_at_iso else None
        )
    except (ValueError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=f"invalid date format: {e}")

    pool = await get_pool()
    partner_id = str(partner["id"])
    caller_user_id = (
        partner.get("partner_user_id")
        or partner.get("user_id")
    )
    caller_email = (
        partner.get("oauth_email") or partner.get("contact_email") or ""
    )

    async with admin_transaction(pool) as conn:
        try:
            new_row = await add_baa_to_roster(
                conn=conn,
                partner_id=partner_id,
                counterparty_org_id=str(org_id_raw) if org_id_raw else None,
                counterparty_practice_name=practice_name,
                executed_at=executed_at,
                expiry_at=expiry_at,
                scope=scope,
                signer_name=signer_name,
                signer_title=signer_title,
                signer_email=signer_email,
                doc_sha256=doc_sha256,
                uploaded_by_user_id=str(caller_user_id) if caller_user_id else None,
                uploaded_by_email=caller_email,
            )
        except BAComplianceError as e:
            raise HTTPException(status_code=400, detail=str(e))

    return {
        "status": "ok",
        "id": str(new_row["id"]),
        "attestation_bundle_id": (
            str(new_row.get("attestation_bundle_id"))
            if new_row.get("attestation_bundle_id") else None
        ),
    }


@router.delete("/me/ba-roster/{roster_id}")
async def revoke_partner_baa_from_roster(
    roster_id: str,
    request: Request,
    partner: dict = require_partner_role("admin"),
):
    """Revoke a roster entry. admin-only. Body: { reason: str ≥20 chars }."""
    try:
        from .partner_ba_compliance import revoke_baa_from_roster, BAComplianceError
        from .tenant_middleware import admin_transaction
    except ImportError:
        from partner_ba_compliance import revoke_baa_from_roster, BAComplianceError  # type: ignore
        from tenant_middleware import admin_transaction  # type: ignore

    body = await request.json()
    reason = (body.get("reason") or "").strip()

    pool = await get_pool()
    partner_id = str(partner["id"])
    caller_user_id = partner.get("partner_user_id") or partner.get("user_id")
    caller_email = (
        partner.get("oauth_email") or partner.get("contact_email") or ""
    )

    async with admin_transaction(pool) as conn:
        try:
            revoked = await revoke_baa_from_roster(
                conn=conn,
                partner_id=partner_id,
                roster_id=roster_id,
                revoking_user_id=str(caller_user_id) if caller_user_id else None,
                revoking_user_email=caller_email,
                reason=reason,
            )
        except BAComplianceError as e:
            raise HTTPException(status_code=400, detail=str(e))

    if revoked is None:
        return {"status": "noop", "message": "No active roster entry to revoke."}
    return {"status": "ok", "revoked_id": str(revoked["id"])}


@router.get("/me/ba-attestation")
async def issue_partner_ba_compliance_attestation_pdf(
    request: Request,
    partner: dict = require_partner_role("admin"),
):
    """Render + stream the BA Compliance Attestation PDF.
    admin-only. Re-renders live from the current roster on each
    call (no separate "issued attestation" table — the auditor
    wants the current snapshot)."""
    try:
        from .partner_ba_compliance import (
            issue_ba_compliance_attestation,
            html_to_pdf,
            BAComplianceError,
        )
        from .tenant_middleware import admin_transaction
        from .shared import check_rate_limit
    except ImportError:
        from partner_ba_compliance import (  # type: ignore
            issue_ba_compliance_attestation,
            html_to_pdf,
            BAComplianceError,
        )
        from tenant_middleware import admin_transaction  # type: ignore
        from shared import check_rate_limit  # type: ignore
    from fastapi.responses import Response

    pool = await get_pool()
    partner_id = str(partner["id"])
    caller_user_id = partner.get("partner_user_id") or partner.get("user_id") or partner_id

    allowed, retry_after_s = await check_rate_limit(
        site_id=partner_id,
        action="partner_ba_attestation_issue",
        window_seconds=3600,
        max_requests=5,
        caller_key=f"partner_user:{caller_user_id}",
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate-limited. Retry in {retry_after_s}s.",
            headers={"Retry-After": str(retry_after_s)},
        )

    # Coach-ultrathink-sweep D-2 fix-up 2026-05-08: Session 212 rule.
    # issue_ba_compliance_attestation issues 5+ admin queries
    # (partner row, agreements, roster, per-roster site_count +
    # client_orgs lookup, INSERT, UPDATE supersede) — admin_connection
    # was at risk of PgBouncer routing pathology (silent zero-row
    # second queries). admin_transaction pins SET LOCAL + queries.
    async with admin_transaction(pool) as conn:
        try:
            result = await issue_ba_compliance_attestation(
                conn=conn,
                partner_id=partner_id,
                issued_by_user_id=str(caller_user_id) if caller_user_id else None,
                issued_by_email=partner.get("oauth_email") or partner.get("contact_email"),
            )
        except BAComplianceError as e:
            raise HTTPException(status_code=409, detail=str(e))

    import asyncio as _asyncio
    pdf_bytes = await _asyncio.to_thread(html_to_pdf, result["html"])

    safe_brand = "".join(
        c if c.isalnum() or c in "-_" else "-"
        for c in result["presenter_brand"]
    )[:80]
    issue_date = result["issued_at"].strftime("%Y-%m-%d")
    filename = f"ba-compliance-{safe_brand}-{issue_date}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Attestation-Id": result["attestation_id"],
            "X-Attestation-Hash": result["attestation_hash"],
            # Sibling-parity with P-F5 portfolio (partners.py:5580) +
            # F1 letter (client_portal.py:5517). Without this header the
            # PartnerAttestations Card B summary shows no validity
            # window — round-table-at-gates Coach DENY 2026-05-08.
            "X-Letter-Valid-Until": result["valid_until"].isoformat(),
        },
    )


@router.get("/me/incidents/{incident_id}/timeline.pdf")
async def render_partner_incident_timeline_pdf(
    incident_id: str,
    request: Request,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Render + stream a per-incident response timeline PDF
    (P-F8). Lisa's 2am owner-call artifact: 1-page chronological
    view of how the substrate detected, planned, executed, and
    resolved an incident.

    admin OR tech (operational artifact, not a state-change). Read-
    only — no chain attestation, no migration. Re-rendered live on
    every call from the authoritative incidents +
    execution_telemetry tables."""
    try:
        from .partner_incident_timeline import (
            render_incident_timeline,
            html_to_pdf,
            IncidentTimelineError,
        )
        from .tenant_middleware import admin_transaction
        from .shared import check_rate_limit
    except ImportError:
        from partner_incident_timeline import (  # type: ignore
            render_incident_timeline,
            html_to_pdf,
            IncidentTimelineError,
        )
        from tenant_middleware import admin_transaction  # type: ignore
        from shared import check_rate_limit  # type: ignore
    from fastapi.responses import Response

    # Reject obviously-malformed UUIDs before hitting the DB.
    h = incident_id.strip().lower()
    if len(h) < 8 or len(h) > 64 or not all(
        c in "0123456789abcdef-" for c in h
    ):
        raise HTTPException(status_code=400, detail="malformed incident_id")

    pool = await get_pool()
    partner_id = str(partner["id"])
    caller_user_id = (
        partner.get("partner_user_id") or partner.get("user_id") or partner_id
    )

    # 60/hr per (partner, user) — operational artifact, looser than
    # attestation issuance but still bounded.
    allowed, retry_after_s = await check_rate_limit(
        site_id=partner_id,
        action="partner_incident_timeline",
        window_seconds=3600,
        max_requests=60,
        caller_key=f"partner_user:{caller_user_id}",
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate-limited. Retry in {retry_after_s}s.",
            headers={"Retry-After": str(retry_after_s)},
        )

    # Coach-ultrathink-sweep D-2 fix-up 2026-05-08: Session 212 rule.
    # render_incident_timeline issues 4+ admin queries (incident
    # row + ownership JOIN, partner row, execution_telemetry rows,
    # plus internal admin_audit_log future-reads). admin_transaction
    # closes the PgBouncer routing-pathology window.
    async with admin_transaction(pool) as conn:
        try:
            result = await render_incident_timeline(
                conn=conn,
                partner_id=partner_id,
                incident_id=incident_id,
            )
        except IncidentTimelineError as e:
            raise HTTPException(status_code=404, detail=str(e))

    import asyncio as _asyncio
    pdf_bytes = await _asyncio.to_thread(html_to_pdf, result["html"])

    safe_brand = "".join(
        c if c.isalnum() or c in "-_" else "-"
        for c in result["presenter_brand"]
    )[:80]
    filename = (
        f"incident-timeline-{safe_brand}-{result['incident_id_short']}.pdf"
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Incident-Id-Short": result["incident_id_short"],
            "X-Site-Label": result["site_label"],
        },
    )


# Public-verify router for partner portfolio attestation (sister to F4
# client-letter verify). Anna-the-sales-lead hands this URL to a
# prospect. The prospect confirms the portfolio attestation is real
# WITHOUT learning which clinics or any operational detail beyond
# aggregate counts.
partner_public_verify_router = APIRouter(prefix="/api/verify", tags=["public-verify"])


@partner_public_verify_router.get("/portfolio/{attestation_hash}")
async def public_verify_partner_portfolio_attestation(
    attestation_hash: str,
    request: Request,
):
    """Public — NO AUTH. Returns OCR-grade aggregate payload for
    the given hash. NO partner_id leak, NO clinic names. Hash is
    64 hex chars (SHA-256), unguessable. 32-char prefix accepted
    with ambiguity detection (Steve P1-D pattern from F4)."""
    h = attestation_hash.strip().lower()
    if not all(c in "0123456789abcdef" for c in h):
        return {"valid": False, "reason": "malformed_hash"}
    if len(h) not in (32, 64):
        return {"valid": False, "reason": "malformed_hash_minimum_32_hex_chars"}

    try:
        from .shared import check_rate_limit
    except ImportError:
        from shared import check_rate_limit  # type: ignore
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )
    allowed, retry_after_s = await check_rate_limit(
        site_id=client_ip,
        action="public_verify_partner_portfolio",
        window_seconds=3600,
        max_requests=60,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Verification rate limit reached. Retry in {retry_after_s}s.",
            headers={"Retry-After": str(retry_after_s)},
        )

    try:
        from .partner_portfolio_attestation import get_portfolio_by_hash
        from .tenant_middleware import admin_connection
    except ImportError:
        from partner_portfolio_attestation import get_portfolio_by_hash  # type: ignore
        from tenant_middleware import admin_connection  # type: ignore

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        if len(h) == 64:
            row = await get_portfolio_by_hash(conn, h)
        else:
            full_rows = await conn.fetch(
                """
                SELECT attestation_hash FROM partner_portfolio_attestations
                 WHERE attestation_hash LIKE $1 || '%'
                 LIMIT 2
                """,
                h,
            )
            if len(full_rows) > 1:
                return {
                    "valid": False,
                    "reason": "ambiguous_prefix_supply_full_64_hex_hash",
                }
            if not full_rows:
                row = None
            else:
                row = await get_portfolio_by_hash(conn, full_rows[0]["attestation_hash"])

    if not row:
        return {"valid": False, "reason": "not_found"}

    return {
        "valid": True,
        "attestation_hash": row["attestation_hash"],
        "issued_at": row["issued_at"].isoformat() if row["issued_at"] else None,
        "valid_until": row["valid_until"].isoformat() if row["valid_until"] else None,
        "is_expired": bool(row["is_expired"]),
        "is_superseded": bool(row["is_superseded"]),
        "period_start": row["period_start"].isoformat() if row["period_start"] else None,
        "period_end": row["period_end"].isoformat() if row["period_end"] else None,
        "site_count": row["site_count"],
        "appliance_count": row["appliance_count"],
        "workstation_count": row["workstation_count"],
        "control_count": row["control_count"],
        "bundle_count": row["bundle_count"],
        "ots_anchored_pct": float(row["ots_anchored_pct"]),
        "chain_root_hex": row["chain_root_hex"],
        "presenter_brand": row["presenter_brand"],
    }


@partner_public_verify_router.get("/ba-attestation/{attestation_hash}")
async def public_verify_partner_ba_attestation(
    attestation_hash: str,
    request: Request,
):
    """Public — NO AUTH. Returns OCR-grade payload for the given
    BA Compliance Attestation hash. NO partner_id leak, NO
    counterparty names, NO roster detail — only aggregate counts +
    presenter brand. The detailed roster is partner-portal-only.

    Coach retroactive sweep 2026-05-08 — convergence with P-F5
    portfolio verify route. Same 32-char-floor + ambiguity-detection
    + X-Forwarded-For + 60/hr per-IP rate-limit posture."""
    h = attestation_hash.strip().lower()
    if not all(c in "0123456789abcdef" for c in h):
        return {"valid": False, "reason": "malformed_hash"}
    if len(h) not in (32, 64):
        return {"valid": False, "reason": "malformed_hash_minimum_32_hex_chars"}

    try:
        from .shared import check_rate_limit
    except ImportError:
        from shared import check_rate_limit  # type: ignore
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )
    allowed, retry_after_s = await check_rate_limit(
        site_id=client_ip,
        action="public_verify_partner_ba_attestation",
        window_seconds=3600,
        max_requests=60,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Verification rate limit reached. Retry in {retry_after_s}s.",
            headers={"Retry-After": str(retry_after_s)},
        )

    try:
        from .partner_ba_compliance import get_ba_attestation_by_hash
        from .tenant_middleware import admin_connection
    except ImportError:
        from partner_ba_compliance import get_ba_attestation_by_hash  # type: ignore
        from tenant_middleware import admin_connection  # type: ignore

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        if len(h) == 64:
            row = await get_ba_attestation_by_hash(conn, h)
        else:
            full_rows = await conn.fetch(
                """
                SELECT attestation_hash FROM partner_ba_compliance_attestations
                 WHERE attestation_hash LIKE $1 || '%'
                 LIMIT 2
                """,
                h,
            )
            if len(full_rows) > 1:
                return {
                    "valid": False,
                    "reason": "ambiguous_prefix_supply_full_64_hex_hash",
                }
            if not full_rows:
                row = None
            else:
                row = await get_ba_attestation_by_hash(
                    conn, full_rows[0]["attestation_hash"]
                )

    if not row:
        return {"valid": False, "reason": "not_found"}

    return {
        "valid": True,
        "attestation_hash": row["attestation_hash"],
        "issued_at": row["issued_at"].isoformat() if row["issued_at"] else None,
        "valid_until": row["valid_until"].isoformat() if row["valid_until"] else None,
        "is_expired": bool(row["is_expired"]),
        "is_superseded": bool(row["is_superseded"]),
        "subcontractor_baa_dated_at": (
            row["subcontractor_baa_dated_at"].isoformat()
            if row["subcontractor_baa_dated_at"] else None
        ),
        "roster_count": row["roster_count"],
        "total_monitored_sites": row["total_monitored_sites"],
        "onboarded_counterparty_count": row["onboarded_counterparty_count"],
        "presenter_brand": row["presenter_brand"],
    }


# =============================================================================
# Sprint-N+2 D4 — Partner→client-portal magic-link mint
# =============================================================================
# Lisa-the-MSP-MD's "open this clinic's portal as the practice owner"
# workflow. Round-table .agent/plans/37-partner-per-site-drill-down-
# roundtable-2026-05-08.md D4 RESOLVED — chain-attested via
# ALLOWED_EVENTS. Mint endpoint:
#   * admin OR tech role (operational debug — RT31 site-state class)
#   * 5/hr per (partner, partner_user) — sensitive
#   * reason ≥20 chars
#   * 15-min single-use token in partner_client_portal_links (mig 293)
#   * privileged_access_attestation row at partner_org:<partner_id>
#   * admin_audit_log mirror (the chain-of-custody mirror parallel to
#     the cryptographic record — same pattern as P-F6 BAA roster).
# Sibling-parity headers per feedback_multi_endpoint_header_parity.md:
#   X-Attestation-Hash + X-Letter-Valid-Until (we reuse the
#   X-Letter-Valid-Until header as our 'token expires_at' since the
#   sibling artifact-issuance endpoints use it for valid_until).

@router.post("/me/sites/{site_id}/client-portal-link")
async def mint_partner_client_portal_link(
    request: Request,
    site_id: str,
    partner: dict = require_partner_role("admin", "tech"),
):
    """Mint a 15-min single-use magic link to the client portal scoped
    to this site. Chain-attested + admin-audit-logged.

    Body: { reason: str ≥ 20 chars }

    Response (200):
      {
        "url": "<frontend-url>/portal/site/<site_id>?magic=<token>",
        "expires_at": ISO8601,
        "magic_link_id": UUID,
        "attestation_bundle_id": UUID | null,
        "attestation_hash": <hex>,
      }
    Headers:
      X-Attestation-Hash: <bundle_hash>
      X-Letter-Valid-Until: <expires_at ISO8601>

    Errors:
      400  — reason missing or <20 chars
      404  — site not found OR not owned by this partner
      429  — rate-limited (5/hr per partner_user)
      503  — chain attestation could not be written (refuse on
             attestation failure per privileged-access invariant —
             never mint without the chain link).
    """
    try:
        from .privileged_access_attestation import (
            create_privileged_access_attestation,
            PrivilegedAccessAttestationError,
        )
        from .shared import check_rate_limit
    except ImportError:
        from privileged_access_attestation import (  # type: ignore
            create_privileged_access_attestation,
            PrivilegedAccessAttestationError,
        )
        from shared import check_rate_limit  # type: ignore

    body = await request.json()
    reason = (body.get("reason") or "").strip()
    if not reason or len(reason) < 20:
        raise HTTPException(
            status_code=400,
            detail=(
                "reason required (min 20 chars — describe why the "
                "practice owner's portal is being opened)"
            ),
        )

    pool = await get_pool()
    partner_id = str(partner["id"])
    caller_user_id_raw = (
        partner.get("partner_user_id") or partner.get("user_id")
    )
    caller_user_id = str(caller_user_id_raw) if caller_user_id_raw else partner_id
    caller_email = (
        partner.get("oauth_email")
        or partner.get("contact_email")
        or partner.get("email")
        or ""
    )
    client_ip = request.client.host if request.client else None

    # Rate limit — 5/hr per (partner, partner_user). Sensitive flow:
    # if a partner-admin's token leaks, the per-user bucket bounds the
    # magic-link mint volume an attacker can pump out.
    allowed, retry_after_s = await check_rate_limit(
        site_id=partner_id,
        action="partner_client_portal_link_mint",
        window_seconds=3600,
        max_requests=5,
        caller_key=f"partner_user:{caller_user_id}",
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Magic-link mint rate-limited for this partner_user. "
                f"Retry in {retry_after_s}s."
            ),
            headers={"Retry-After": str(retry_after_s)},
        )

    # Verify site belongs to this partner. Use partner-scoped tenant
    # connection so RLS double-checks the site→partner ownership.
    async with tenant_connection(pool, site_id=site_id) as conn:
        site = await conn.fetchrow(
            """
            SELECT s.site_id, s.clinic_name, s.partner_id, s.client_org_id
            FROM sites s
            WHERE s.site_id = $1
              AND s.partner_id = $2
              AND s.status != 'inactive'
            """,
            site_id, partner["id"],
        )
        if not site:
            raise HTTPException(
                status_code=404,
                detail="Site not found or not owned by this partner.",
            )

    # 15-minute TTL
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=15)
    token = secrets.token_urlsafe(32)

    # Chain attestation BEFORE the DB write — failure here MUST refuse
    # the mint per privileged-access invariant (never mint without
    # the cryptographic record).
    attestation_bundle_id: Optional[str] = None
    attestation_hash: Optional[str] = None
    try:
        async with admin_connection(pool) as att_conn:
            try:
                att = await create_privileged_access_attestation(
                    att_conn,
                    site_id=f"partner_org:{partner_id}",
                    event_type="partner_client_portal_link_minted",
                    actor_email=caller_email or f"partner_user:{caller_user_id}",
                    reason=reason,
                    origin_ip=client_ip,
                    approvals=[{
                        "stage": "minted",
                        "actor": caller_email or f"partner_user:{caller_user_id}",
                        "partner_id": partner_id,
                        "site_id": site_id,
                        "expires_at": expires_at.isoformat(),
                    }],
                )
                attestation_bundle_id = att.get("bundle_id")
                attestation_hash = att.get("bundle_hash")
            except PrivilegedAccessAttestationError as e:
                logger.error(
                    "partner_client_portal_link_attestation_failed",
                    exc_info=True,
                    extra={
                        "partner_id": partner_id,
                        "site_id": site_id,
                    },
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Cryptographic attestation unavailable; magic-"
                        "link mint refused. Try again or contact "
                        "operator. Detail: " + str(e)
                    ),
                )
    except HTTPException:
        raise
    except Exception:
        logger.error(
            "partner_client_portal_link_attestation_unexpected",
            exc_info=True,
            extra={"partner_id": partner_id, "site_id": site_id},
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Cryptographic attestation step failed unexpectedly; "
                "magic-link mint refused."
            ),
        )

    # Persist the magic-link row + admin-audit mirror in a single
    # admin_transaction (multi-statement → admin_transaction per
    # CLAUDE.md Session 212 routing rule).
    try:
        from .tenant_middleware import admin_transaction
    except ImportError:
        from tenant_middleware import admin_transaction  # type: ignore

    magic_link_id: Optional[str] = None
    async with admin_transaction(pool) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO partner_client_portal_links (
                partner_id, partner_user_id, site_id, token,
                expires_at, minted_by_email, minted_by_ip, reason,
                attestation_bundle_id
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8,
                CASE WHEN $9::text = '' THEN NULL ELSE $9::uuid END
            )
            RETURNING id
            """,
            partner["id"],
            (caller_user_id_raw if caller_user_id_raw else None),
            site_id,
            token,
            expires_at,
            (caller_email or None),
            client_ip,
            reason,
            (attestation_bundle_id or ""),
        )
        magic_link_id = str(row["id"]) if row else None

        # admin_audit_log mirror — the privileged_access_attestation
        # helper writes a generic mirror; this row carries the
        # partner-specific (partner_user_id, client_org_id, site_id,
        # magic_link_id, ttl_seconds) shape Maya pinned in plan 37
        # round-table verdict (D4-Maya conditions).
        await conn.execute(
            """
            INSERT INTO admin_audit_log (username, action, target, details, ip_address, created_at)
            VALUES ($1, $2, $3, $4::jsonb, $5, NOW())
            """,
            (caller_email or f"partner_user:{caller_user_id}"),
            "PARTNER_CLIENT_PORTAL_LINK_MINTED",
            f"site:{site_id}",
            json.dumps({
                "partner_id": partner_id,
                "partner_user_id": caller_user_id,
                "client_org_id": (
                    str(site["client_org_id"])
                    if site["client_org_id"] else None
                ),
                "site_id": site_id,
                "magic_link_id": magic_link_id,
                "ttl_seconds": 15 * 60,
                "attestation_bundle_id": attestation_bundle_id,
                "attestation_hash": attestation_hash,
                "reason": reason,
            }),
            client_ip,
        )

    # Build the URL the partner copies into clipboard. Frontend serves
    # /portal/site/<site_id>/login + ?magic=<token> consume path; the
    # partner-mint token is handed off via the same query param the
    # client-portal email-magic-link flow uses.
    frontend_url = os.getenv("FRONTEND_URL", "https://www.osiriscare.net")
    portal_url = (
        f"{frontend_url}/portal/site/{site_id}/login?magic={token}"
    )

    return Response(
        content=json.dumps({
            "url": portal_url,
            "expires_at": expires_at.isoformat(),
            "magic_link_id": magic_link_id,
            "attestation_bundle_id": attestation_bundle_id,
            "attestation_hash": attestation_hash,
        }),
        media_type="application/json",
        headers={
            # Sibling-parity per feedback_multi_endpoint_header_parity.md
            # — both X-Attestation-Hash + X-Letter-Valid-Until shipped on
            # F1 + P-F5 + P-F6 issuance endpoints. We mirror the same two
            # header keys for the magic-link mint so frontend parity
            # tests treat this as a sibling artifact-issuance response.
            "X-Attestation-Hash": (attestation_hash or ""),
            "X-Letter-Valid-Until": expires_at.isoformat(),
        },
    )
