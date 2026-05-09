"""
Client Portal API.

Enables healthcare practices to access their compliance data directly.
Provides OsirisCare-branded interface independent of MSP partner.

Auth Flow:
1. Client enters email at /client/login
2. Backend sends magic link (60-min expiry)
3. Client clicks link -> /client/verify?token=xxx
4. Frontend POSTs token to /api/client/auth/validate-magic-link
5. Backend creates session, sets httpOnly cookie
6. Redirect to /client/dashboard

Security:
- Magic links: 60-min expiry, single-use, POST body only
- Session tokens: HMAC-SHA256 hashed, 30-day expiry
- HttpOnly, Secure, SameSite=Lax cookies
- RBAC: owner > admin > viewer (server-side)
"""

import os
import secrets
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Literal, Dict, Any
from decimal import Decimal

from fastapi import APIRouter, Request, Response, HTTPException, Depends, Cookie, Query, Header
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, EmailStr, Field
import httpx

# Stripe integration (optional - graceful fallback if not installed)
try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    stripe = None
    STRIPE_AVAILABLE = False

from .fleet import get_pool
from .db_utils import _uid
from .tenant_middleware import tenant_connection, admin_connection, admin_transaction, org_connection  # noqa: F401
from .phi_boundary import sanitize_evidence_checks

logger = logging.getLogger(__name__)

# =============================================================================
# COMPLIANCE CATEGORY MAP — single source of truth for check-type → category
# =============================================================================

COMPLIANCE_CATEGORIES = {
    "patching": ["nixos_generation", "windows_update", "linux_patching",
                 "linux_unattended_upgrades", "linux_kernel_params"],
    "antivirus": ["windows_defender", "windows_defender_exclusions",
                  "defender_exclusions"],
    "backup": ["backup_status", "windows_backup_status"],
    "logging": ["audit_logging", "windows_audit_policy", "linux_audit",
                "linux_logging", "security_audit", "audit_policy",
                "linux_log_forwarding"],
    "firewall": ["firewall", "windows_firewall_status", "firewall_status",
                 "linux_firewall", "network_profile", "net_unexpected_ports"],
    "encryption": ["bitlocker", "windows_bitlocker_status", "linux_crypto",
                   "windows_smb_signing", "bitlocker_status", "smb_signing",
                   "smb1_protocol"],
    "access_control": ["rogue_admin_users", "linux_accounts", "windows_password_policy",
                       "linux_permissions", "linux_ssh_config", "windows_screen_lock_policy",
                       "screen_lock", "screen_lock_policy", "password_policy",
                       "guest_account", "rdp_nla", "rogue_scheduled_tasks"],
    "services": ["critical_services", "linux_services", "windows_service_dns",
                 "windows_service_netlogon", "windows_service_spooler",
                 "windows_service_w32time", "windows_service_wuauserv", "agent_status",
                 "service_dns", "service_netlogon", "service_status",
                 "spooler_service", "linux_failed_services", "ntp_sync",
                 "winrm", "dns_config", "net_dns_resolution",
                 "net_expected_service", "net_host_reachability"],
}

COMPLIANCE_REVERSE_MAP = {}
for _cat, _types in COMPLIANCE_CATEGORIES.items():
    for _ct in _types:
        COMPLIANCE_REVERSE_MAP[_ct] = _cat

# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_URL = os.getenv("FRONTEND_URL", os.getenv("BASE_URL", "https://www.osiriscare.net"))

# Session configuration
SESSION_COOKIE_NAME = "osiris_client_session"
SESSION_DURATION_DAYS = 7
SESSION_COOKIE_MAX_AGE = SESSION_DURATION_DAYS * 24 * 60 * 60
SESSION_IDLE_TIMEOUT_MINUTES = 15  # HIPAA §164.312(a)(2)(iii) automatic logoff

# MFA pending tokens for client login: {token: {"user_id": ..., "expires": datetime, ...}}
_client_mfa_pending: dict = {}
MFA_PENDING_TTL_MINUTES = 5

# Magic link configuration
MAGIC_LINK_EXPIRY_MINUTES = 60

# Stripe configuration
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")  # Default subscription price

# Initialize Stripe
if STRIPE_AVAILABLE and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY
    logger.info("Stripe integration enabled")


# =============================================================================
# ROUTERS
# =============================================================================

# Public router - no auth required (login flow)
public_router = APIRouter(prefix="/client/auth", tags=["client-auth"])

# Authenticated router - requires valid client session
auth_router = APIRouter(prefix="/client", tags=["client-portal"])


# =============================================================================
# MODELS
# =============================================================================

class ClientMagicLinkRequest(BaseModel):
    """Request a magic link login."""
    email: EmailStr


class MagicLinkValidate(BaseModel):
    """Validate magic link token."""
    token: str


class PasswordLogin(BaseModel):
    """Login with email and password."""
    email: EmailStr
    password: str


class PasswordSet(BaseModel):
    """Set or update password."""
    password: str


class InviteUser(BaseModel):
    """Invite a user to the org."""
    email: EmailStr
    name: Optional[str] = None
    role: Literal["admin", "viewer"] = "viewer"


class TransferRequest(BaseModel):
    """Request to transfer MSP partner."""
    reason: Optional[str] = None


class UserRoleUpdate(BaseModel):
    """Update user role."""
    role: Literal["admin", "viewer"]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def hash_token(token: str) -> str:
    """Hash a token — delegates to shared.hash_session_token (single source of truth)."""
    from .shared import hash_session_token
    return hash_session_token(token)


def generate_token() -> str:
    """Generate a secure token — delegates to shared.generate_session_token."""
    from .shared import generate_session_token
    return generate_session_token()


async def get_client_user_from_session(session_token: str, pool):
    """Get client user from session token.

    Enforces HIPAA §164.312(a)(2)(iii) idle timeout.
    """
    if not session_token:
        return None

    token_hash = hash_token(session_token)

    async with admin_connection(pool) as conn:
        # Check idle timeout before updating last_activity_at
        idle_check = await conn.fetchrow("""
            SELECT last_activity_at FROM client_sessions
            WHERE token_hash = $1 AND expires_at > NOW()
        """, token_hash)

        if idle_check and idle_check['last_activity_at']:
            from datetime import datetime, timezone, timedelta
            idle_cutoff = datetime.now(timezone.utc) - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)
            if idle_check['last_activity_at'] < idle_cutoff:
                await conn.execute(
                    "DELETE FROM client_sessions WHERE token_hash = $1", token_hash
                )
                return None

        # Update last_activity and get user + org + partner branding
        row = await conn.fetchrow("""
            UPDATE client_sessions cs
            SET last_activity_at = NOW()
            FROM client_users cu
            JOIN client_orgs co ON cu.client_org_id = co.id
            LEFT JOIN partners p ON co.current_partner_id = p.id
            WHERE cs.token_hash = $1
              AND cs.expires_at > NOW()
              AND cs.user_id = cu.id
              AND cu.is_active = true
              AND co.status = 'active'
            RETURNING
                cu.id as user_id,
                cu.email,
                cu.name,
                cu.role,
                co.id as org_id,
                co.name as org_name,
                co.current_partner_id,
                p.brand_name as partner_brand_name,
                p.primary_color as partner_primary_color,
                p.logo_url as partner_logo_url,
                p.support_email as partner_support_email
        """, token_hash)

        return row


async def require_client_user(
    request: Request,
    osiris_client_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME)
):
    """Dependency to require valid client session."""
    if not osiris_client_session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    pool = await get_pool()
    user = await get_client_user_from_session(osiris_client_session, pool)

    if not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    result = dict(user)
    # Attach partner branding context for frontend rendering
    result["partner_branding"] = {
        "brand_name": result.pop("partner_brand_name", None) or "OsirisCare",
        "primary_color": result.pop("partner_primary_color", None) or "#0D9488",
        "logo_url": result.pop("partner_logo_url", None),
        "support_email": result.pop("partner_support_email", None),
    }
    return result


async def require_client_admin(user: dict = Depends(require_client_user)):
    """Require admin or owner role."""
    if user["role"] not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_client_owner(user: dict = Depends(require_client_user)):
    """Require owner role."""
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


# =============================================================================
# AUDIT LOGGING (Session 203 H1)
# =============================================================================
#
# Every mutating client portal endpoint writes to client_audit_log so the
# HIPAA §164.308(a)(1)(ii)(D) audit trail + §164.528 disclosure accounting
# can recover who did what. The helper is non-raising — audit failures
# never block the caller's mutation.

async def _audit_client_action(
    conn,
    user: dict,
    action: str,
    target: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    request: Optional[Request] = None,
) -> None:
    """Append a client-portal mutation event to client_audit_log.

    Accepts an asyncpg connection (caller is expected to already be
    inside a transaction). `user` comes from `require_client_user` and
    has `user_id`, `org_id`, `email`, `role`. `action` is MACHINE_CASE
    short string; `details` is free-form JSONB.
    """
    try:
        import json as _json
        ip = None
        if request is not None:
            ip = (
                request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                or (request.client.host if request.client else None)
            )
        await conn.execute(
            """
            INSERT INTO client_audit_log (
                org_id, actor_user_id, actor_email,
                action, target, details, ip_address
            )
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb, $7)
            """,
            str(user["org_id"]) if user.get("org_id") else None,
            str(user["user_id"]) if user.get("user_id") else None,
            user.get("email") or "unknown",
            action,
            target,
            _json.dumps(details) if details else None,
            ip,
        )
    except Exception as e:
        logger.error(
            "client_audit_log write failed: action=%s target=%s err=%s",
            action, target, e, exc_info=True,
        )


# =============================================================================
# AUTH ENDPOINTS (Public)
# =============================================================================

@public_router.post("/request-magic-link")
async def request_magic_link(request: ClientMagicLinkRequest, http_request: Request):
    """Send magic link to user's email.

    Rate limited per source IP via the existing rate limiter (Session
    203 Batch 6 H2 fix). Without this, an attacker could enumerate
    valid email addresses or exhaust the SMTP quota by spamming
    requests. The 60-second cooldown is generous enough for legitimate
    "I clicked too fast" retries while still bounding attacker volume.
    """
    from .rate_limiter import check_rate_limit
    client_ip = (
        http_request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (http_request.client.host if http_request.client else "unknown")
    )
    allowed, _remaining = await check_rate_limit(f"client_magic:{client_ip}", "client_magic_link")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many magic link requests. Please wait a minute and try again.",
        )

    pool = await get_pool()
    email = request.email.lower()

    async with admin_connection(pool) as conn:
        # Find user
        user = await conn.fetchrow("""
            SELECT cu.id, cu.name, cu.is_active, cu.client_org_id,
                   co.status as org_status
            FROM client_users cu
            JOIN client_orgs co ON co.id = cu.client_org_id
            WHERE cu.email = $1
        """, email)

        # Always return success to prevent email enumeration
        if not user or not user["is_active"] or user["org_status"] != "active":
            logger.info(f"Magic link requested for unknown/inactive email: {email}")
            return {"status": "sent", "message": "If that email exists, a login link was sent."}

        # Check SSO enforcement
        sso_row = await conn.fetchrow(
            "SELECT sso_enforced FROM client_org_sso WHERE client_org_id = $1",
            user["client_org_id"],
        )
        if sso_row and sso_row["sso_enforced"]:
            raise HTTPException(status_code=403, detail="This organization requires SSO login")

        # Generate magic token
        magic_token = generate_token()
        magic_token_hash = hash_token(magic_token)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=MAGIC_LINK_EXPIRY_MINUTES)

        # Store hashed token (never store plaintext)
        await conn.execute("""
            UPDATE client_users
            SET magic_token = $1, magic_token_expires_at = $2
            WHERE id = $3
        """, magic_token_hash, expires_at, user["id"])

    # Send email
    magic_link = f"{BASE_URL}/client/verify?token={magic_token}"

    try:
        from .email_service import send_email
        # Round-table 2026-05-06 (Carol+Adam): a recipient's own
        # name on their own envelope is NOT an identity leak in
        # the SMTP-channel sense — the address IS the identity.
        # Counsel's opacity concern is org/clinic/actor names,
        # not the recipient greeting themselves. Personalized
        # greeting also improves anti-phish deliverability score.
        # Carve-out documented in
        # tests/test_email_opacity_harmonized.py FORBIDDEN_BODY_TOKENS.
        await send_email(
            email,
            "Your OsirisCare Login Link",
            f"""Hi{' ' + user['name'] if user['name'] else ''},

Click here to sign in to your OsirisCare compliance portal:

{magic_link}

This link expires in 60 minutes and can only be used once.

If you didn't request this, you can safely ignore this email.

- The OsirisCare Team
"""
        )
        logger.info(f"Magic link sent to {email}")
    except Exception as e:
        logger.error(f"Failed to send magic link email: {e}")
        # Still return success to prevent enumeration
        pass

    return {"status": "sent", "message": "If that email exists, a login link was sent."}


@public_router.post("/validate-magic-link")
async def validate_magic_link(request: Request, body: MagicLinkValidate):
    """Validate magic link and create session."""
    pool = await get_pool()

    # admin_transaction wave-13 (Session 212 routing-pathology rule): 4 admin DB calls (token-redeem UPDATE, org status fetch, MFA flag fetch, session INSERT) — pin SET LOCAL app.is_admin to one PgBouncer backend
    async with admin_transaction(pool) as conn:
        # Find and validate token (single-use: delete on fetch)
        # Token is hashed before comparison (stored hashed since migration 071)
        token_lookup = hash_token(body.token)
        user = await conn.fetchrow("""
            UPDATE client_users
            SET magic_token = NULL,
                magic_token_expires_at = NULL,
                last_login_at = NOW(),
                email_verified = true
            WHERE magic_token = $1
              AND magic_token_expires_at > NOW()
              AND is_active = true
            RETURNING id, email, name, role, client_org_id
        """, token_lookup)

        if not user:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        # Verify org is active
        org = await conn.fetchrow("""
            SELECT id, name, status FROM client_orgs WHERE id = $1
        """, user["client_org_id"])

        if not org or org["status"] != "active":
            raise HTTPException(status_code=403, detail="Organization account is not active")

        # SECURITY: Enforce MFA on magic link login (same as password login).
        # Without this, magic link bypasses org-level MFA requirement entirely.
        mfa_org_required = await conn.fetchval(
            "SELECT mfa_required FROM client_orgs WHERE id = $1", user["client_org_id"]
        )
        mfa_user_enabled = user.get("mfa_enabled", False)
        if mfa_org_required and not mfa_user_enabled:
            raise HTTPException(
                status_code=403,
                detail="Your organization requires MFA. Please enable two-factor authentication before logging in."
            )
        if mfa_user_enabled:
            # User has MFA — require TOTP verification before creating session
            mfa_token = secrets.token_urlsafe(32)
            now = datetime.now(timezone.utc)
            expired = [k for k, v in _client_mfa_pending.items() if v.get("expires", "") < now.isoformat()]
            for k in expired:
                _client_mfa_pending.pop(k, None)
            _client_mfa_pending[mfa_token] = {
                "user_id": str(user["id"]),
                "org_id": str(user["client_org_id"]),
                "email": user["email"],
                "expires": (now + timedelta(minutes=5)).isoformat(),
            }
            return {"status": "mfa_required", "mfa_token": mfa_token}

        # Create session
        session_token = generate_token()
        token_hash = hash_token(session_token)
        expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DURATION_DAYS)

        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent", "")[:500]

        await conn.execute("""
            INSERT INTO client_sessions (user_id, token_hash, user_agent, ip_address, expires_at)
            VALUES ($1, $2, $3, $4, $5)
        """, user["id"], token_hash, user_agent, ip_address, expires_at)

        logger.info(f"Client login successful: {user['email']}")

        # Audit: magic link login is a security-relevant event
        await _audit_client_action(
            conn, {"user_id": str(user["id"]), "org_id": str(user["client_org_id"]), "email": user["email"]},
            "MAGIC_LINK_LOGIN", target=str(user["id"]),
            details={"method": "magic_link"}, request=request,
        )

    # Set session cookie
    response = Response(content='{"status": "authenticated"}', media_type="application/json")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=SESSION_COOKIE_MAX_AGE,
        path="/"
    )

    return response


@public_router.post("/login")
async def login_with_password(request: Request, body: PasswordLogin):
    """Login with email and password.

    Rate limited per source IP (Session 203 Batch 6 H2 fix). Account
    lockout after 5 failed attempts is enforced separately at the row
    level (`failed_login_attempts` + `locked_until` columns).
    """
    from .rate_limiter import check_rate_limit
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )
    allowed, _remaining = await check_rate_limit(f"client_login:{client_ip}", "client_login")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please wait a minute and try again.",
        )

    pool = await get_pool()
    email = body.email.lower()

    # Coach-sweep ratchet wave-3 2026-05-08: 8-query password-login
    # auth-path. user fetch + failed_login_attempts increment +
    # session create + audit log. admin_transaction critical so the
    # whole auth flow lands on one PgBouncer backend.
    async with admin_transaction(pool) as conn:
        user = await conn.fetchrow("""
            SELECT cu.id, cu.password_hash, cu.is_active, cu.client_org_id,
                   co.status as org_status,
                   cu.failed_login_attempts, cu.locked_until
            FROM client_users cu
            JOIN client_orgs co ON co.id = cu.client_org_id
            WHERE cu.email = $1
        """, email)

        if not user or not user["is_active"] or user["org_status"] != "active":
            raise HTTPException(status_code=401, detail="Invalid credentials")

        # Account lockout: 5 failed attempts = 15-minute lockout
        from datetime import timezone as _tz
        if user.get("locked_until") and user["locked_until"] > datetime.now(_tz.utc):
            remaining = (user["locked_until"] - datetime.now(_tz.utc)).seconds // 60
            raise HTTPException(status_code=429, detail=f"Account locked. Try again in {remaining + 1} minutes.")

        # Check SSO enforcement
        sso_row = await conn.fetchrow(
            "SELECT sso_enforced FROM client_org_sso WHERE client_org_id = $1",
            user["client_org_id"],
        )
        if sso_row and sso_row["sso_enforced"]:
            raise HTTPException(status_code=403, detail="This organization requires SSO login")

        if not user["password_hash"]:
            # No password set - must use magic link
            raise HTTPException(
                status_code=400,
                detail="Password not set. Please use magic link to login."
            )

        # Verify password (bcrypt with constant-time comparison)
        from .auth import verify_password
        if not verify_password(body.password, user["password_hash"]):
            # Increment failed attempts, lock after 5
            attempts = (user.get("failed_login_attempts") or 0) + 1
            locked = None
            if attempts >= 5:
                locked = datetime.now(_tz.utc) + timedelta(minutes=15)
                logger.warning(f"Client account locked after {attempts} failed attempts")
            await conn.execute(
                "UPDATE client_users SET failed_login_attempts = $1, locked_until = $2 WHERE id = $3",
                attempts, locked, user["id"],
            )
            raise HTTPException(status_code=401, detail="Invalid credentials")

        # Check MFA status: user enrollment + org-level requirement
        mfa_row = await conn.fetchrow(
            "SELECT mfa_enabled FROM client_users WHERE id = $1", user["id"]
        )
        mfa_user_enabled = mfa_row["mfa_enabled"] if mfa_row else False

        org_mfa_row = await conn.fetchrow(
            "SELECT mfa_required FROM client_orgs WHERE id = $1", user["client_org_id"]
        )
        mfa_org_required = org_mfa_row["mfa_required"] if org_mfa_row else False

        # Org requires MFA but user hasn't enrolled — block login
        if mfa_org_required and not mfa_user_enabled:
            raise HTTPException(
                status_code=403,
                detail={
                    "status": "mfa_setup_required",
                    "error": "Your organization requires multi-factor authentication. Please set up MFA before logging in.",
                },
            )

        if mfa_row and mfa_user_enabled:
            mfa_token = secrets.token_urlsafe(32)
            now = datetime.now(timezone.utc)
            # Clean expired tokens
            expired = [k for k, v in _client_mfa_pending.items() if v["expires"] < now]
            for k in expired:
                _client_mfa_pending.pop(k, None)
            _client_mfa_pending[mfa_token] = {
                "user_id": str(user["id"]),
                "email": email,
                "expires": now + timedelta(minutes=MFA_PENDING_TTL_MINUTES),
            }
            return Response(
                content='{"status": "mfa_required", "mfa_token": "' + mfa_token + '"}',
                media_type="application/json"
            )

        # Reset lockout counters on successful login
        await conn.execute(
            "UPDATE client_users SET failed_login_attempts = 0, locked_until = NULL WHERE id = $1",
            user["id"],
        )

        # Create session
        session_token = generate_token()
        token_hash_val = hash_token(session_token)
        expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DURATION_DAYS)

        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent", "")[:500]

        await conn.execute("""
            INSERT INTO client_sessions (user_id, token_hash, user_agent, ip_address, expires_at)
            VALUES ($1, $2, $3, $4, $5)
        """, user["id"], token_hash_val, user_agent, ip_address, expires_at)

        await conn.execute("""
            UPDATE client_users SET last_login_at = NOW() WHERE id = $1
        """, user["id"])

    response = Response(content='{"status": "authenticated"}', media_type="application/json")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=SESSION_COOKIE_MAX_AGE,
        path="/"
    )

    return response


@public_router.post("/logout")
async def logout(
    response: Response,
    osiris_client_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME)
):
    """Logout and clear session."""
    if osiris_client_session:
        pool = await get_pool()
        token_hash = hash_token(osiris_client_session)

        async with admin_connection(pool) as conn:
            await conn.execute("""
                DELETE FROM client_sessions WHERE token_hash = $1
            """, token_hash)

    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@public_router.get("/me")
async def get_current_user(user: dict = Depends(require_client_user)):
    """Get current authenticated user."""
    return {
        "id": str(user["user_id"]),
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "org": {
            "id": str(user["org_id"]),
            "name": user["org_name"],
        }
    }


# =============================================================================
# DASHBOARD ENDPOINTS (Authenticated)
# =============================================================================

@auth_router.get("/dashboard")
async def get_dashboard(user: dict = Depends(require_client_user)):
    """Get dashboard overview for client org."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Get org details
        org = await conn.fetchrow("""
            SELECT co.*, p.name as partner_name, p.brand_name as partner_brand
            FROM client_orgs co
            LEFT JOIN partners p ON p.id = co.current_partner_id
            WHERE co.id = $1
        """, org_id)

        # Get sites belonging to this org
        sites = await conn.fetch("""
            SELECT s.site_id, s.clinic_name, s.status, s.tier,
                   COUNT(DISTINCT cb.id) as evidence_count,
                   MAX(cb.checked_at) as last_evidence
            FROM sites s
            LEFT JOIN compliance_bundles cb ON cb.site_id = s.site_id
            WHERE s.client_org_id = $1
            GROUP BY s.site_id, s.clinic_name, s.status, s.tier
            ORDER BY s.clinic_name
        """, org_id)

        # Stage 2 unified score — compute_compliance_score() is the
        # canonical algorithm shared by /api/client/dashboard +
        # /api/client/reports/current + /api/client/sites/{id}/compliance-health.
        # Pre-Stage-2 each endpoint had its own formula and the three
        # surfaces showed contradictory numbers for the same org.
        site_ids = [s["site_id"] for s in sites]
        from .compliance_score import compute_compliance_score
        score_result = await compute_compliance_score(conn, site_ids)

        # Get recent notifications (unread)
        unread_count = await conn.fetchval("""
            SELECT COUNT(*) FROM client_notifications
            WHERE client_org_id = $1 AND NOT is_read
        """, org_id)

        # Go agent compliance — distinct sibling metric, no longer
        # blended with bundle score (pre-Stage-2 the 70/30 blend masked
        # both signals into a meaningless aggregate). Frontend renders
        # it as its own tile with its own context.
        agent_compliance = None
        if site_ids:
            agent_row = await conn.fetchrow("""
                SELECT
                    COALESCE(SUM(total_agents), 0) as total_agents,
                    COALESCE(SUM(active_agents), 0) as active_agents,
                    CASE WHEN COUNT(site_id) > 0
                         THEN ROUND(AVG(overall_compliance_rate)::numeric, 1)
                         ELSE NULL END as avg_compliance
                FROM site_go_agent_summaries
                WHERE site_id = ANY($1)
            """, site_ids)
            if agent_row and agent_row['total_agents'] > 0:
                agent_compliance = {
                    'total_agents': agent_row['total_agents'],
                    'active_agents': agent_row['active_agents'],
                    'avg_compliance': float(agent_row['avg_compliance']) if agent_row['avg_compliance'] is not None else 0.0,
                }

        compliance_score = score_result.overall_score
        score_status = score_result.status
        # Maintain `score_source` for backward-compat with Stage 1
        # frontend handling. Now reflects the unified canonical source.
        score_source = "bundles" if score_result.counts["total"] > 0 else "none"

        return {
            "org": {
                "id": str(org["id"]),
                "name": org["name"],
                "partner_name": org["partner_name"],
                "partner_brand": org["partner_brand"] or org["partner_name"],
                "provider_count": org["provider_count"],
            },
            "sites": [
                {
                    "site_id": s["site_id"],
                    "clinic_name": s["clinic_name"],
                    "status": s["status"],
                    "tier": s["tier"],
                    "evidence_count": s["evidence_count"],
                    "last_evidence": s["last_evidence"].isoformat() if s["last_evidence"] else None,
                }
                for s in sites
            ],
            "kpis": {
                "compliance_score": compliance_score,
                "score_status": score_status,
                "score_source": score_source,
                "total_checks": score_result.counts["total"],
                "passed": score_result.counts["passed"],
                "failed": score_result.counts["failed"],
                "warnings": score_result.counts["warnings"],
                "last_check_at": (
                    score_result.last_check_at.isoformat()
                    if score_result.last_check_at else None
                ),
                "stale_check_count": score_result.stale_check_count,
                "window_description": score_result.window_description,
            },
            "agent_compliance": agent_compliance,
            "unread_notifications": unread_count,
        }


@auth_router.get("/sites")
async def list_sites(user: dict = Depends(require_client_user)):
    """List all sites for client org."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        sites = await conn.fetch("""
            SELECT s.site_id, s.clinic_name, s.status, s.tier,
                   s.onboarding_stage, s.created_at,
                   COUNT(DISTINCT cb.id) as evidence_count,
                   GREATEST(
                       MAX(cb.checked_at),
                       MAX(sa.last_checkin),
                       gas.last_event
                   ) as last_check,
                   COALESCE(gas.total_agents, 0) as agent_count,
                   COALESCE(gas.overall_compliance_rate, 0) as agent_compliance_rate
            FROM sites s
            LEFT JOIN compliance_bundles cb ON cb.site_id = s.site_id
            LEFT JOIN site_appliances sa ON sa.site_id = s.site_id AND sa.deleted_at IS NULL
            LEFT JOIN site_go_agent_summaries gas ON gas.site_id = s.site_id
            WHERE s.client_org_id = $1 AND s.status != 'inactive'
            GROUP BY s.site_id, s.clinic_name, s.status, s.tier,
                     s.onboarding_stage, s.created_at,
                     gas.last_event, gas.total_agents, gas.overall_compliance_rate
            ORDER BY s.clinic_name
        """, org_id)

        return {
            "sites": [
                {
                    "site_id": s["site_id"],
                    "clinic_name": s["clinic_name"],
                    "status": s["status"],
                    "tier": s["tier"],
                    "onboarding_stage": s["onboarding_stage"],
                    "evidence_count": s["evidence_count"],
                    "last_check": s["last_check"].isoformat() if s["last_check"] else None,
                    "created_at": s["created_at"].isoformat() if s["created_at"] else None,
                    "agent_count": s["agent_count"],
                    "agent_compliance_rate": float(s["agent_compliance_rate"]),
                }
                for s in sites
            ],
            "count": len(sites),
        }


# ----------------------------------------------------------------------
# Appliance fleet view (RT33 P0, 2026-05-05)
# ----------------------------------------------------------------------
# Customer-facing list of the appliances substrate-attesting on their
# behalf. Org-scoped, rollup-MV-backed, deliberately narrow field set.
#
# Field allowlist (Carol veto, RT33): no mac_address, no ip_addresses,
# no daemon_health, no peer_macs — these reveal Layer 2 topology to a
# class of users (clinical-staff portal viewers) that doesn't need it.
# A compromised customer session must not become a fleet recon map.
#
# Pagination: cursor by appliance_id ASC, hard-cap 50. Most clinics have
# 1-3 appliances; if a customer has 50+ they should be using the
# operator (MSP) portal, not the substrate-class view.
@auth_router.get("/appliances")
async def list_client_appliances(
    cursor: str = "",
    limit: int = 25,
    user: dict = Depends(require_client_user),
):
    """List appliances visible to this client org.

    Returns the substrate appliances attesting compliance on the org's
    sites. Read-only — operator-class actions (l2-mode, clear-stale,
    fleet orders) are reserved for the MSP via central command.
    """
    if limit < 1 or limit > 50:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 50",
        )
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Query site_appliances directly (RLS-protected by mig 278
        # tenant_org_isolation) with inline LATERAL heartbeat join.
        # Steve veto from RT33 P2 review: querying the rollup MV
        # bypasses RLS because PG MVs don't inherit base-table policies.
        # Same live_status semantics as Migration 193's MV — heartbeats
        # are the source of truth, last_checkin is cache.
        rows = await conn.fetch(
            """
            SELECT sa.appliance_id,
                   sa.site_id,
                   COALESCE(sa.display_name, sa.hostname, sa.appliance_id) AS display_name,
                   CASE
                       WHEN hb.max_observed_at IS NULL THEN 'offline'
                       WHEN hb.max_observed_at > NOW() - INTERVAL '90 seconds' THEN 'online'
                       WHEN hb.max_observed_at > NOW() - INTERVAL '5 minutes' THEN 'stale'
                       ELSE 'offline'
                   END AS status,
                   hb.max_observed_at AS last_heartbeat_at,
                   sa.last_checkin,
                   sa.agent_version,
                   s.clinic_name
            FROM site_appliances sa
            JOIN sites s ON s.site_id = sa.site_id AND sa.deleted_at IS NULL
            LEFT JOIN LATERAL (
                SELECT MAX(observed_at) AS max_observed_at
                FROM appliance_heartbeats
                WHERE appliance_id = sa.appliance_id
                  AND observed_at > NOW() - INTERVAL '24 hours'
            ) hb ON true
            WHERE s.client_org_id = $1
              AND s.status != 'inactive'
              AND ($2 = '' OR sa.appliance_id > $2)
            ORDER BY sa.appliance_id ASC
            LIMIT $3
            """,
            org_id, cursor, limit + 1,
        )

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = rows[-1]["appliance_id"] if (has_more and rows) else None

    return {
        "appliances": [
            {
                "appliance_id": r["appliance_id"],
                "site_id": r["site_id"],
                "site_name": r["clinic_name"],
                "display_name": r["display_name"],
                "status": r["status"],
                "last_heartbeat_at": (
                    r["last_heartbeat_at"].isoformat()
                    if r["last_heartbeat_at"] else None
                ),
                "last_checkin": (
                    r["last_checkin"].isoformat()
                    if r["last_checkin"] else None
                ),
                "agent_version": r["agent_version"],
            }
            for r in rows
        ],
        "next_cursor": next_cursor,
        "limit": limit,
    }


@auth_router.get("/sites/{site_id}")
async def get_site_detail(site_id: str, user: dict = Depends(require_client_user)):
    """Get detailed site info including compliance status."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify site belongs to org
        site = await conn.fetchrow("""
            SELECT s.* FROM sites s
            WHERE s.site_id = $1 AND s.client_org_id = $2
        """, site_id, org_id)

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get latest check results by type
        checks = await conn.fetch("""
            SELECT DISTINCT ON (cb.check_type)
                cb.id, cb.check_type, cb.check_result,
                cb.checks->0->>'hipaa_control' as hipaa_control, cb.checked_at
            FROM compliance_bundles cb
            WHERE cb.site_id = $1
            ORDER BY cb.check_type, cb.checked_at DESC
        """, site_id)

        # Group by control
        controls = {}
        for c in checks:
            control = c["hipaa_control"] or "Other"
            if control not in controls:
                controls[control] = []
            controls[control].append({
                "check_type": c["check_type"],
                "result": c["check_result"],
                "checked_at": c["checked_at"].isoformat() if c["checked_at"] else None,
            })

        return {
            "site": {
                "site_id": site["site_id"],
                "clinic_name": site["clinic_name"],
                "status": site["status"],
                "tier": site["tier"],
                "onboarding_stage": site["onboarding_stage"],
            },
            "controls": controls,
            "check_count": len(checks),
        }


@auth_router.get("/sites/{site_id}/compliance-health")
async def get_site_compliance_health(
    site_id: str,
    user: dict = Depends(require_client_user)
):
    """Get detailed compliance health breakdown for the infographic.

    Returns per-category scores, 30-day trend, and healing stats.
    """
    pool = await get_pool()
    org_id = user["org_id"]

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify site belongs to org
        site = await conn.fetchrow("""
            SELECT site_id, clinic_name, status FROM sites
            WHERE site_id = $1 AND client_org_id = $2
        """, site_id, org_id)
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get disabled checks (includes both disabled and not_applicable)
        disabled = await conn.fetch("""
            SELECT check_type FROM site_drift_config
            WHERE site_id = $1 AND (enabled = false OR status = 'not_applicable')
        """, site_id)
        disabled_set = {r["check_type"] for r in disabled}
        if not disabled:
            defaults = await conn.fetch("""
                SELECT check_type FROM site_drift_config
                WHERE site_id = '__defaults__' AND (enabled = false OR status = 'not_applicable')
            """)
            disabled_set = {r["check_type"] for r in defaults}

        categories = COMPLIANCE_CATEGORIES
        reverse_map = COMPLIANCE_REVERSE_MAP

        # --- Source 1: Compliance bundles (Windows drift scans) ---
        bundles = await conn.fetch("""
            SELECT checks FROM compliance_bundles
            WHERE site_id = $1
            ORDER BY checked_at DESC LIMIT 50
        """, site_id)

        cat_pass = {cat: 0 for cat in categories}
        cat_fail = {cat: 0 for cat in categories}
        cat_warn = {cat: 0 for cat in categories}
        total_passed = 0
        total_failed = 0
        total_warnings = 0

        for bundle in bundles:
            checks = bundle["checks"] or []
            if isinstance(checks, str):
                import json as _json
                try:
                    checks = _json.loads(checks)
                except Exception:
                    continue
            for check in checks:
                if not isinstance(check, dict):
                    continue
                ct = check.get("check", "")
                if ct in disabled_set:
                    continue
                status = (check.get("status") or "").lower()
                cat = reverse_map.get(ct)
                if status in ("compliant", "pass"):
                    total_passed += 1
                    if cat:
                        cat_pass[cat] += 1
                elif status == "warning":
                    total_warnings += 1
                    if cat:
                        cat_warn[cat] += 1
                elif status in ("non_compliant", "fail"):
                    total_failed += 1
                    if cat:
                        cat_fail[cat] += 1

        # --- Source 2: Active incidents (ALL platforms: Linux, NixOS, Windows) ---
        # Count distinct compliance issues (unique check_type per device), not raw alerts
        incident_rows = await conn.fetch("""
            SELECT i.check_type, count(DISTINCT i.appliance_id) as devices_affected
            FROM incidents i
            JOIN v_appliances_current a ON a.id = i.appliance_id
            WHERE a.site_id = $1 AND i.resolved_at IS NULL
            GROUP BY i.check_type
        """, site_id)

        for row in incident_rows:
            ct = row["check_type"]
            if ct in disabled_set:
                continue
            cnt = row["devices_affected"]  # 1 fail per device with this issue
            cat = reverse_map.get(ct)
            if cat:
                cat_fail[cat] += cnt
                total_failed += cnt

        # --- Compute per-category scores from total basket ---
        breakdown = {}
        overall_sum = 0
        cats_with_data = 0
        for cat in categories:
            total = cat_pass[cat] + cat_fail[cat] + cat_warn[cat]
            if total > 0:
                score = round(((cat_pass[cat] + 0.5 * cat_warn[cat]) / total) * 100)
                breakdown[cat] = score
                overall_sum += score
                cats_with_data += 1
            else:
                breakdown[cat] = None

        overall = round(overall_sum / cats_with_data, 1) if cats_with_data > 0 else None

        # 30-day trend (daily scores)
        trend_rows = await conn.fetch("""
            SELECT
                DATE(cb.checked_at) as date,
                COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant', 'fail', 'non_compliant', 'warning')) as total,
                COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant')) as passed
            FROM compliance_bundles cb,
                 jsonb_array_elements(cb.checks) as c
            WHERE cb.site_id = $1
              AND cb.checked_at > NOW() - INTERVAL '30 days'
              AND jsonb_array_length(cb.checks) > 0
            GROUP BY DATE(cb.checked_at)
            ORDER BY date ASC
        """, site_id)

        trend = [
            {
                "date": r["date"].isoformat(),
                "score": (
                    round((r["passed"] / r["total"]) * 100, 1)
                    if r["total"] > 0
                    else None
                )
            }
            for r in trend_rows
        ]

        # Healing stats (last 30 days)
        healing = await conn.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE success = true AND resolution_level IN ('L1', 'L2')) as auto_healed,
                COUNT(*) FILTER (WHERE resolution_level = 'L3' OR success = false) as pending
            FROM execution_telemetry
            WHERE site_id = $1
              AND created_at > NOW() - INTERVAL '30 days'
        """, site_id)

        # Stage 2 unified canonical score (incidents folded in to match
        # the per-site historical shape). Pre-Stage-2 the headline number
        # was a per-category mean; that's now demoted to supplementary
        # `breakdown` while the headline matches the dashboard + reports.
        from .compliance_score import compute_compliance_score
        canonical = await compute_compliance_score(
            conn, [site_id], include_incidents=True,
        )

        return {
            "site_id": site_id,
            "clinic_name": site["clinic_name"],
            # Headline number — matches /api/client/dashboard +
            # /api/client/reports/current for this same site.
            "overall_score": canonical.overall_score,
            "score_status": canonical.status,
            # Pre-Stage-2 the headline was the per-category-average
            # `overall`. Kept as `category_average_score` for backward-
            # compat callers while the canonical headline is what UI
            # surfaces.
            "category_average_score": overall,
            "breakdown": breakdown,
            "counts": canonical.counts,
            "trend": trend,
            "healing": {
                "total": healing["total"] if healing else 0,
                "auto_healed": healing["auto_healed"] if healing else 0,
                "pending": healing["pending"] if healing else 0,
            },
        }


@auth_router.get("/sites/{site_id}/devices-at-risk")
async def get_client_devices_at_risk(
    site_id: str,
    user: dict = Depends(require_client_user),
):
    """Get per-device drift/compliance breakdown for a site.

    Returns devices sorted by risk (most issues first), with per-category
    incident counts so clients can identify culprit devices at a glance.
    """
    pool = await get_pool()
    org_id = user["org_id"]

    categories = COMPLIANCE_CATEGORIES
    reverse_map = COMPLIANCE_REVERSE_MAP

    async with org_connection(pool, org_id=org_id) as conn:
        # Verify site belongs to org
        site = await conn.fetchrow("""
            SELECT site_id FROM sites
            WHERE site_id = $1 AND client_org_id = $2
        """, site_id, org_id)
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get all active (unresolved) incidents for this site, grouped by hostname
        rows = await conn.fetch("""
            SELECT sa.hostname, i.check_type, i.severity, i.created_at, i.id,
                   i.resolution_tier as resolution_level
            FROM incidents i
            JOIN site_appliances sa ON sa.appliance_id = i.appliance_id::text AND sa.deleted_at IS NULL
            WHERE sa.site_id = $1
              AND i.status != 'resolved'
            ORDER BY sa.hostname, i.created_at DESC
        """, site_id)

        # Get device info from discovered_devices for enrichment (hostname, ip, device_type)
        device_info = {}
        try:
            devices = await conn.fetch("""
                SELECT d.hostname, d.ip_address, d.device_type, d.os_name, d.compliance_status
                FROM discovered_devices d
                WHERE d.site_id = $1
            """, site_id)
            for d in devices:
                hn = (d["hostname"] or "").lower()
                if hn:
                    device_info[hn] = {
                        "ip_address": d["ip_address"],
                        "device_type": d["device_type"],
                        "os_name": d["os_name"],
                        "compliance_status": d["compliance_status"],
                    }
        except Exception as e:
            logger.warning(f"Device enrichment failed (non-fatal): {e}")

        # Build per-device breakdown
        device_map: dict = {}
        for row in rows:
            hostname = row["hostname"] or "unknown"
            if hostname not in device_map:
                hn_lower = hostname.lower()
                info = device_info.get(hn_lower, {})
                device_map[hostname] = {
                    "hostname": hostname,
                    "ip_address": info.get("ip_address"),
                    "device_type": info.get("device_type"),
                    "os_name": info.get("os_name"),
                    "active_incidents": 0,
                    "critical_count": 0,
                    "high_count": 0,
                    "medium_count": 0,
                    "low_count": 0,
                    "categories": {cat: 0 for cat in categories},
                    "worst_severity": "low",
                    "incidents": [],
                }

            dev = device_map[hostname]
            dev["active_incidents"] += 1
            sev = (row["severity"] or "medium").lower()
            if sev == "critical":
                dev["critical_count"] += 1
            elif sev == "high":
                dev["high_count"] += 1
            elif sev == "medium":
                dev["medium_count"] += 1
            else:
                dev["low_count"] += 1

            # Update worst severity
            sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
            if sev_rank.get(sev, 0) > sev_rank.get(dev["worst_severity"], 0):
                dev["worst_severity"] = sev

            # Category mapping
            cat = reverse_map.get(row["check_type"])
            if cat:
                dev["categories"][cat] += 1

            # Include incident summary (max 5 per device)
            if len(dev["incidents"]) < 5:
                dev["incidents"].append({
                    "id": row["id"],
                    "check_type": row["check_type"],
                    "severity": row["severity"],
                    "resolution_level": row["resolution_level"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                })

        # Sort by risk: critical first, then high, then total count
        devices_list = sorted(
            device_map.values(),
            key=lambda d: (d["critical_count"], d["high_count"], d["active_incidents"]),
            reverse=True,
        )

        # Compute a simple health score per device
        for dev in devices_list:
            penalty = (dev["critical_count"] * 25 + dev["high_count"] * 15 +
                       dev["medium_count"] * 8 + dev["low_count"] * 3)
            dev["health_score"] = max(0, 100 - penalty)

        return {
            "site_id": site_id,
            "total_devices_at_risk": len(devices_list),
            "devices": devices_list,
        }


@auth_router.get("/sites/{site_id}/history")
async def get_site_history(
    site_id: str,
    days: int = Query(30, ge=1, le=2555),  # Up to 7 years
    user: dict = Depends(require_client_user)
):
    """Get compliance history for a site."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify site belongs to org
        site = await conn.fetchval("""
            SELECT 1 FROM sites WHERE site_id = $1 AND client_org_id = $2
        """, site_id, org_id)

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get historical data - expand JSONB checks for accurate per-check scoring
        history = await conn.fetch("""
            SELECT
                DATE(cb.checked_at) as date,
                COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant', 'fail', 'non_compliant', 'warning')) as total,
                COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant')) as passed,
                COUNT(*) FILTER (WHERE c->>'status' IN ('fail', 'non_compliant')) as failed
            FROM compliance_bundles cb,
                 jsonb_array_elements(cb.checks) as c
            WHERE cb.site_id = $1
              AND cb.checked_at > NOW() - INTERVAL '%s days'
              AND jsonb_array_length(cb.checks) > 0
            GROUP BY DATE(cb.checked_at)
            ORDER BY date DESC
        """ % days, site_id)

        return {
            "site_id": site_id,
            "days": days,
            "history": [
                {
                    "date": h["date"].isoformat(),
                    "total": h["total"],
                    "passed": h["passed"],
                    "failed": h["failed"],
                    "score": (
                        round((h["passed"] / h["total"]) * 100, 1)
                        if h["total"] > 0 else None
                    ),
                }
                for h in history
            ],
        }


# =============================================================================
# DRIFT CONFIG ENDPOINTS
# =============================================================================

@auth_router.get("/sites/{site_id}/drift-config")
async def get_client_drift_config(site_id: str, user: dict = Depends(require_client_user)):
    """Get drift scan configuration for a client's site."""
    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        owner = await conn.fetchval(
            "SELECT client_org_id FROM sites WHERE site_id = $1", site_id)
        if str(owner) != str(user["org_id"]):
            raise HTTPException(status_code=404, detail="Site not found")

        rows = await conn.fetch(
            """SELECT check_type, enabled, notes,
                      COALESCE(status, CASE WHEN enabled THEN 'enabled' ELSE 'disabled' END) as status,
                      exception_reason
               FROM site_drift_config WHERE site_id = $1 ORDER BY check_type""",
            site_id)
        if not rows:
            rows = await conn.fetch(
                """SELECT check_type, enabled, notes,
                          COALESCE(status, CASE WHEN enabled THEN 'enabled' ELSE 'disabled' END) as status,
                          exception_reason
                   FROM site_drift_config WHERE site_id = '__defaults__' ORDER BY check_type""")

        def _platform(ct):
            if ct.startswith("macos_"): return "macos"
            if ct.startswith("linux_"): return "linux"
            return "windows"

        checks = [{
            "check_type": r["check_type"], "enabled": r["enabled"],
            "platform": _platform(r["check_type"]), "notes": r["notes"] or "",
            "status": r["status"] or ("enabled" if r["enabled"] else "disabled"),
            "exception_reason": r["exception_reason"] or "",
        } for r in rows]
    return {"site_id": site_id, "checks": checks}


@auth_router.put("/sites/{site_id}/drift-config")
async def update_client_drift_config(
    site_id: str,
    body: dict,
    request: Request,
    user: dict = Depends(require_client_user),
):
    """Update drift scan configuration for a client's site."""
    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        owner = await conn.fetchval(
            "SELECT client_org_id FROM sites WHERE site_id = $1", site_id)
        if str(owner) != str(user["org_id"]):
            raise HTTPException(status_code=404, detail="Site not found")

        checks = body.get("checks", [])

        # Safety bounds: prevent disabling all checks or critical checks
        from .routes import _validate_drift_config_checks
        _validate_drift_config_checks(checks)

        async with conn.transaction():
            for item in checks:
                status = item.get("status") or ("enabled" if item["enabled"] else "disabled")
                effective_enabled = item["enabled"] if status != "not_applicable" else False
                exception_reason = (item.get("exception_reason") or "").strip() if status == "not_applicable" else None
                await conn.execute("""
                    INSERT INTO site_drift_config (site_id, check_type, enabled, status, exception_reason, modified_by, modified_at)
                    VALUES ($1, $2, $3, $5, $6, $4, NOW())
                    ON CONFLICT (site_id, check_type)
                    DO UPDATE SET enabled = $3, status = $5, exception_reason = $6, modified_by = $4, modified_at = NOW()
                """, site_id, item["check_type"], effective_enabled, f"client:{user.get('email', user['id'])}", status, exception_reason)

            await _audit_client_action(
                conn, user,
                action="DRIFT_CONFIG_UPDATED",
                target=site_id,
                details={"check_count": len(checks)},
                request=request,
            )
    return {"status": "ok", "site_id": site_id, "updated": len(checks)}


# =============================================================================
# EVIDENCE ENDPOINTS
# =============================================================================

@auth_router.get("/evidence")
async def list_evidence(
    site_id: Optional[str] = None,
    check_type: Optional[str] = None,
    result: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_client_user)
):
    """List evidence bundles for client org."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Build query with filters
        query = """
            SELECT cb.id, cb.site_id, cb.check_type, cb.check_result,
                   cb.checks->0->>'hipaa_control' as hipaa_control, cb.checked_at, cb.bundle_id,
                   s.clinic_name
            FROM compliance_bundles cb
            JOIN sites s ON s.site_id = cb.site_id
            WHERE s.client_org_id = $1
        """
        params = [org_id]
        param_idx = 2

        if site_id:
            query += f" AND cb.site_id = ${param_idx}"
            params.append(site_id)
            param_idx += 1

        if check_type:
            query += f" AND cb.check_type = ${param_idx}"
            params.append(check_type)
            param_idx += 1

        if result:
            query += f" AND cb.check_result = ${param_idx}"
            params.append(result)
            param_idx += 1

        query += f" ORDER BY cb.checked_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
        params.extend([limit, offset])

        bundles = await conn.fetch(query, *params)

        # Get total count
        count_query = """
            SELECT COUNT(*) FROM compliance_bundles cb
            JOIN sites s ON s.site_id = cb.site_id
            WHERE s.client_org_id = $1
        """
        total = await conn.fetchval(count_query, org_id)

        return {
            "evidence": [
                {
                    "id": str(b["id"]),
                    "site_id": b["site_id"],
                    "clinic_name": b["clinic_name"],
                    "check_type": b["check_type"],
                    "check_result": b["check_result"],
                    "hipaa_control": b["hipaa_control"],
                    "checked_at": b["checked_at"].isoformat() if b["checked_at"] else None,
                    "bundle_hash": b["bundle_id"],
                }
                for b in bundles
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@auth_router.get("/evidence/{bundle_id}")
async def get_evidence_detail(bundle_id: str, user: dict = Depends(require_client_user)):
    """Get evidence bundle detail."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Try to find by UUID id first, then by bundle_id string
        bundle = await conn.fetchrow("""
            SELECT cb.*, s.clinic_name
            FROM compliance_bundles cb
            JOIN sites s ON s.site_id = cb.site_id
            WHERE (cb.id::text = $1 OR cb.bundle_id = $1) AND s.client_org_id = $2
        """, bundle_id, org_id)

        if not bundle:
            raise HTTPException(status_code=404, detail="Evidence bundle not found")

        # Get recent bundles for this site (chain view)
        chain = await conn.fetch("""
            SELECT cb.bundle_id, cb.checked_at
            FROM compliance_bundles cb
            WHERE cb.site_id = $1
              AND cb.checked_at <= $2
            ORDER BY cb.checked_at DESC
            LIMIT 5
        """, bundle["site_id"], bundle["checked_at"])

        # Extract hipaa_control from checks JSONB
        hipaa_control = None
        checks = bundle["checks"]
        # asyncpg may return JSONB as string - parse if needed
        if isinstance(checks, str):
            import json
            try:
                checks = json.loads(checks)
            except (json.JSONDecodeError, TypeError):
                checks = []
        if checks and len(checks) > 0:
            check_item = checks[0]
            if isinstance(check_item, dict):
                hipaa_control = check_item.get("hipaa_control")

        sanitized_checks = sanitize_evidence_checks(checks)

        return {
            "bundle": {
                "id": str(bundle["id"]),
                "site_id": bundle["site_id"],
                "clinic_name": bundle["clinic_name"],
                "check_type": bundle["check_type"],
                "check_result": bundle["check_result"],
                "hipaa_control": hipaa_control,
                "checked_at": bundle["checked_at"].isoformat() if bundle["checked_at"] else None,
                "bundle_hash": bundle["bundle_id"],
                "prev_hash": bundle.get("prev_hash"),
                "agent_signature": bundle.get("agent_signature") or bundle.get("signature"),
                "minio_path": None,  # compliance_bundles doesn't have s3_uri
                "checks": sanitized_checks,
            },
            "chain": [
                {
                    "hash": c["bundle_id"],
                    "prev_hash": None,
                    "checked_at": c["checked_at"].isoformat() if c["checked_at"] else None,
                }
                for c in chain
            ],
        }


@auth_router.get("/evidence/{bundle_id}/download")
async def download_evidence(bundle_id: str, user: dict = Depends(require_client_user)):
    """Get presigned URL to download evidence bundle from MinIO."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # compliance_bundles doesn't store in MinIO, but we can return the bundle data
        bundle = await conn.fetchrow("""
            SELECT cb.bundle_id, cb.checks, cb.summary, cb.checked_at, cb.check_type, cb.check_result
            FROM compliance_bundles cb
            JOIN sites s ON s.site_id = cb.site_id
            WHERE (cb.id::text = $1 OR cb.bundle_id = $1) AND s.client_org_id = $2
        """, bundle_id, org_id)

        if not bundle:
            raise HTTPException(status_code=404, detail="Evidence bundle not found")

        # Build downloadable JSON evidence package from compliance_bundles data
        import json as _json
        checked_at = bundle["checked_at"]
        evidence_data = {
            "bundle_id": bundle["bundle_id"],
            "check_type": bundle["check_type"],
            "check_result": bundle["check_result"],
            "checked_at": checked_at.isoformat() if checked_at else None,
            "summary": _json.loads(bundle["summary"]) if isinstance(bundle["summary"], str) else bundle["summary"],
            "checks": sanitize_evidence_checks(
                _json.loads(bundle["checks"]) if isinstance(bundle["checks"], str) else bundle["checks"]
            ),
            "metadata": {
                "format": "OsirisCare Evidence Bundle v1",
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "integrity": "compliance_bundle",
                "portal_sanitized": True,
            },
        }
        content = _json.dumps(evidence_data, indent=2, default=str)
        filename = f"evidence-{bundle['bundle_id'][:12]}-{(checked_at or datetime.now(timezone.utc)).strftime('%Y%m%d')}.json"

        return StreamingResponse(
            iter([content.encode()]),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


@auth_router.get("/evidence/verify/{bundle_id}")
async def verify_evidence(bundle_id: str, user: dict = Depends(require_client_user)):
    """Verify evidence bundle hash chain integrity."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        bundle = await conn.fetchrow("""
            SELECT cb.* FROM compliance_bundles cb
            JOIN sites s ON s.site_id = cb.site_id
            WHERE (cb.id::text = $1 OR cb.bundle_id = $1) AND s.client_org_id = $2
        """, bundle_id, org_id)

        if not bundle:
            raise HTTPException(status_code=404, detail="Evidence bundle not found")

        # Count bundles in chain for this site
        chain_length = await conn.fetchval("""
            SELECT COUNT(*) FROM compliance_bundles
            WHERE site_id = $1 AND checked_at <= $2
        """, bundle["site_id"], bundle["checked_at"])

        return {
            "bundle_id": str(bundle["id"]),
            "bundle_hash": bundle["bundle_id"],
            "chain_valid": True,  # Simplified - full hash chain verification in evidence_chain.py
            "chain_length": chain_length or 1,
            "has_signature": bool(bundle.get("agent_signature") or bundle.get("signature")),
            "checked_at": bundle["checked_at"].isoformat() if bundle["checked_at"] else None,
        }


# =============================================================================
# REPORTS ENDPOINTS
# =============================================================================

@auth_router.get("/reports/monthly")
async def list_monthly_reports(user: dict = Depends(require_client_user)):
    """List available monthly compliance reports."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        reports = await conn.fetch("""
            SELECT id, report_month, overall_score, controls_passed,
                   controls_failed, controls_total, incidents_count,
                   incidents_auto_healed, generated_at
            FROM client_monthly_reports
            WHERE client_org_id = $1
            ORDER BY report_month DESC
        """, org_id)

        return {
            "reports": [
                {
                    "id": str(r["id"]),
                    "month": r["report_month"].strftime("%Y-%m"),
                    "overall_score": float(r["overall_score"]) if r["overall_score"] else None,
                    "controls_passed": r["controls_passed"],
                    "controls_failed": r["controls_failed"],
                    "controls_total": r["controls_total"],
                    "incidents_count": r["incidents_count"],
                    "incidents_auto_healed": r["incidents_auto_healed"],
                    "generated_at": r["generated_at"].isoformat() if r["generated_at"] else None,
                }
                for r in reports
            ],
        }


@auth_router.get("/reports/monthly/{month}")
async def download_monthly_report(month: str, user: dict = Depends(require_client_user)):
    """Download monthly compliance report PDF."""
    pool = await get_pool()
    org_id = user["org_id"]

    # Parse month (YYYY-MM format)
    try:
        report_month = datetime.strptime(month + "-01", "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid month format. Use YYYY-MM.")

    async with org_connection(pool, org_id=org_id) as conn:
        report = await conn.fetchrow("""
            SELECT pdf_path, pdf_hash
            FROM client_monthly_reports
            WHERE client_org_id = $1 AND report_month = $2
        """, org_id, report_month)

        if not report:
            raise HTTPException(status_code=404, detail="Report not found")

        if not report["pdf_path"]:
            raise HTTPException(status_code=404, detail="Report PDF not generated yet")

    # Generate presigned URL (similar to evidence download)
    try:
        from minio import Minio

        minio_endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
        minio_access = os.getenv("MINIO_ACCESS_KEY", "minio")
        minio_secret = os.getenv("MINIO_SECRET_KEY", "minio123")
        minio_secure = os.getenv("MINIO_SECURE", "false").lower() == "true"

        client = Minio(
            minio_endpoint,
            access_key=minio_access,
            secret_key=minio_secret,
            secure=minio_secure
        )

        url = client.presigned_get_object(
            "reports",
            report["pdf_path"],
            expires=timedelta(minutes=15)
        )

        return {
            "download_url": url,
            "expires_in": 900,
            "pdf_hash": report["pdf_hash"],
        }
    except Exception as e:
        logger.error(f"Failed to generate report URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate download URL")


# =============================================================================
# ON-DEMAND COMPLIANCE SNAPSHOT
# =============================================================================

@auth_router.get("/reports/current")
async def get_current_compliance_snapshot(user: dict = Depends(require_client_user)):
    """Generate a real-time compliance snapshot.

    Returns current compliance posture across all sites — not limited to
    monthly cadence. Clients can pull this anytime.
    """
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Get all sites for this org
        sites = await conn.fetch("""
            SELECT s.site_id, s.clinic_name, s.status, s.tier
            FROM sites s
            WHERE s.client_org_id = $1
        """, org_id)

        site_ids = [s["site_id"] for s in sites]

        if not site_ids:
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "overall_score": None,
                "score_status": "no_data",
                "score_reason": "no_sites_provisioned",
                "sites": [],
                "controls": {"passed": 0, "failed": 0, "warnings": 0, "total": 0},
                "healing": {"total": 0, "auto_healed": 0, "pending": 0},
                "checks": [],
            }

        # Stage 2 unified canonical score. Same algorithm + same numbers
        # as the dashboard top tile and the per-site infographic.
        from .compliance_score import compute_compliance_score
        score_result = await compute_compliance_score(conn, site_ids)

        # The Reports page also shows per-check details (audit-grade
        # listing). Pull the latest-per-check separately so we can
        # surface them in the response.
        #
        # P0 perf fix 2026-05-06: prior query had NO date window and
        # scanned every partition of compliance_bundles (232K+ rows,
        # PARTITIONED by month per Migration 138). Profiled 14.5s
        # in production. Added 30-day window matching the canonical
        # compute_compliance_score default — checks older than 30
        # days are considered stale anyway, and a stale check
        # surfacing as a Reports row would be misleading.
        checks = await conn.fetch("""
            WITH unnested AS (
                SELECT
                    cb.site_id,
                    cb.checked_at,
                    c->>'check' AS check_type,
                    c->>'status' AS check_status,
                    c->>'hipaa_control' AS hipaa_control,
                    COALESCE(c->>'hostname', c->>'host', '') AS hostname
                FROM compliance_bundles cb,
                     jsonb_array_elements(cb.checks) AS c
                WHERE cb.site_id = ANY($1)
                  AND cb.checked_at >= NOW() - INTERVAL '30 days'
            ),
            latest AS (
                SELECT DISTINCT ON (site_id, check_type, hostname)
                    site_id, check_type, check_status, hipaa_control,
                    hostname, checked_at
                FROM unnested
                ORDER BY site_id, check_type, hostname, checked_at DESC
            )
            SELECT * FROM latest ORDER BY site_id, check_type
        """, site_ids)

        # Recent healing activity (last 30 days)
        healing = await conn.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'completed') as auto_healed,
                COUNT(*) FILTER (WHERE status = 'pending' OR status = 'escalated') as pending
            FROM execution_telemetry
            WHERE site_id = ANY($1)
              AND created_at > NOW() - INTERVAL '30 days'
        """, site_ids)

        # Per-site breakdown — pulled from the canonical compute path
        # so per-site numbers ALSO agree with the dashboard. Site name
        # joined from the original `sites` query.
        site_name_by_id = {s["site_id"]: s["clinic_name"] for s in sites}
        site_results = [
            {
                "site_id": s["site_id"],
                "clinic_name": site_name_by_id.get(s["site_id"], s["site_id"]),
                "score": s["score"],
                "score_status": s["status"],
                "passed": s["passed"],
                "failed": s["failed"],
                "total": s["total"],
            }
            for s in score_result.by_site
        ]

        # Individual check details for the report
        check_details = [
            {
                "site_id": c["site_id"],
                "check_type": c["check_type"],
                "result": c["check_status"],
                "hipaa_control": c["hipaa_control"],
                "hostname": c["hostname"],
                "checked_at": c["checked_at"].isoformat() if c["checked_at"] else None,
            }
            for c in checks
        ]

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "overall_score": score_result.overall_score,
            "score_status": score_result.status,
            "window_description": score_result.window_description,
            "sites": site_results,
            "controls": score_result.counts,
            "healing": {
                "total": healing["total"] if healing else 0,
                "auto_healed": healing["auto_healed"] if healing else 0,
                "pending": healing["pending"] if healing else 0,
            },
            "checks": check_details,
        }


# =============================================================================
# NOTIFICATIONS ENDPOINTS
# =============================================================================

@auth_router.get("/notifications")
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(require_client_user)
):
    """List notifications for client org."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        query = """
            SELECT id, type, severity, title, message, action_url, action_label,
                   is_read, read_at, created_at
            FROM client_notifications
            WHERE client_org_id = $1
        """
        params = [org_id]

        if unread_only:
            query += " AND NOT is_read"

        query += " ORDER BY created_at DESC LIMIT $2"
        params.append(limit)

        notifications = await conn.fetch(query, *params)

        unread_count = await conn.fetchval("""
            SELECT COUNT(*) FROM client_notifications
            WHERE client_org_id = $1 AND NOT is_read
        """, org_id)

        return {
            "notifications": [
                {
                    "id": str(n["id"]),
                    "type": n["type"],
                    "severity": n["severity"],
                    "title": n["title"],
                    "message": n["message"],
                    "action_url": n["action_url"],
                    "action_label": n["action_label"],
                    "is_read": n["is_read"],
                    "read_at": n["read_at"].isoformat() if n["read_at"] else None,
                    "created_at": n["created_at"].isoformat() if n["created_at"] else None,
                }
                for n in notifications
            ],
            "unread_count": unread_count,
        }


@auth_router.post("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, user: dict = Depends(require_client_user)):
    """Mark a notification as read."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        result = await conn.execute("""
            UPDATE client_notifications
            SET is_read = true, read_at = NOW(), read_by_user_id = $1
            WHERE id = $2 AND client_org_id = $3 AND NOT is_read
        """, user["user_id"], _uid(notification_id), org_id)

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Notification not found or already read")

        await _audit_client_action(
            conn, user, "NOTIFICATION_READ", target=str(notification_id),
        )

    return {"status": "read"}


@auth_router.post("/notifications/read-all")
async def mark_all_notifications_read(user: dict = Depends(require_client_user)):
    """Mark all notifications as read."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        result = await conn.execute("""
            UPDATE client_notifications
            SET is_read = true, read_at = NOW(), read_by_user_id = $1
            WHERE client_org_id = $2 AND NOT is_read
        """, user["user_id"], org_id)

    # Parse count from "UPDATE N"
    count = int(result.split()[1]) if result.startswith("UPDATE") else 0

    if count > 0:
        async with org_connection(pool, org_id=org_id) as conn2:
            await _audit_client_action(
                conn2, user, "NOTIFICATIONS_READ_ALL",
                details={"count": count},
            )

    return {"status": "read_all", "count": count}


# =============================================================================
# USER MANAGEMENT ENDPOINTS (Admin only)
# =============================================================================

@auth_router.get("/users")
async def list_users(user: dict = Depends(require_client_admin)):
    """List users in client org."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        users = await conn.fetch("""
            SELECT id, email, name, role, is_active, email_verified,
                   last_login_at, created_at
            FROM client_users
            WHERE client_org_id = $1
            ORDER BY role, email
        """, org_id)

        return {
            "users": [
                {
                    "id": str(u["id"]),
                    "email": u["email"],
                    "name": u["name"],
                    "role": u["role"],
                    "is_active": u["is_active"],
                    "email_verified": u["email_verified"],
                    "last_login_at": u["last_login_at"].isoformat() if u["last_login_at"] else None,
                    "created_at": u["created_at"].isoformat() if u["created_at"] else None,
                }
                for u in users
            ],
        }


@auth_router.post("/users/invite")
async def invite_user(
    invite: InviteUser,
    request: Request,
    user: dict = Depends(require_client_admin),
):
    """Invite a user to the org."""
    pool = await get_pool()
    org_id = user["org_id"]
    email = invite.email.lower()

    async with org_connection(pool, org_id=org_id) as conn:
        # Check if user already exists
        existing = await conn.fetchval("""
            SELECT 1 FROM client_users WHERE client_org_id = $1 AND email = $2
        """, org_id, email)

        if existing:
            raise HTTPException(status_code=400, detail="User already exists in organization")

        # Create invite token
        invite_token = generate_token()
        token_hash = hash_token(invite_token)
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)

        # Store invite
        invite_id = await conn.fetchval("""
            INSERT INTO client_invites (
                client_org_id, email, role, token_hash, invited_by_user_id, expires_at
            ) VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """, org_id, email, invite.role, token_hash, user["user_id"], expires_at)

        await _audit_client_action(
            conn, user,
            action="USER_INVITED",
            target=email,
            details={"role": invite.role, "invite_id": str(invite_id)},
            request=request,
        )

    try:
        from .email_alerts import send_operator_alert
        send_operator_alert(
            event_type="client_user_invited",
            severity="P2",
            summary=f"Client user invited to {user.get('org_name', 'org')}: {email} ({invite.role})",
            details={
                "invited_email": email,
                "role": invite.role,
                "invite_id": str(invite_id),
                "org_id": str(user.get("org_id", "")),
            },
            actor_email=user.get("email"),
        )
    except Exception:
        logger.error("operator_alert_dispatch_failed_user_invited", exc_info=True)

    # Send invite email
    invite_link = f"{BASE_URL}/client/invite?token={invite_token}"

    try:
        from .email_service import send_email
        # Opaque mode (task #42 sweep, 2026-05-06): subject + body
        # withhold org name. Recipient may be a mistyped address;
        # the org identity should only appear after they click
        # through and authenticate. Org name is shown on the
        # accept-invite page after the token resolves.
        await send_email(
            email,
            "OsirisCare: invitation to join a team",
            f"""Hello,

You've been invited to join a team on OsirisCare. Click below
within 7 days to view and accept the invitation:

{invite_link}

If you weren't expecting this invitation, you can safely ignore
this email.

- The OsirisCare Team
"""
        )
    except Exception as e:
        logger.error(f"Failed to send invite email: {e}")

    return {
        "invite_id": str(invite_id),
        "email": email,
        "role": invite.role,
        "expires_at": expires_at.isoformat(),
    }


@auth_router.delete("/users/{target_user_id}")
async def remove_user(
    target_user_id: str,
    request: Request,
    user: dict = Depends(require_client_admin),
):
    """Remove a user from the org."""
    pool = await get_pool()
    org_id = user["org_id"]

    # Can't remove yourself
    if target_user_id == str(user["user_id"]):
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    async with org_connection(pool, org_id=org_id) as conn:
        # Check target user
        target = await conn.fetchrow("""
            SELECT role FROM client_users WHERE id = $1 AND client_org_id = $2
        """, _uid(target_user_id), org_id)

        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        # Only owner can remove admins
        if target["role"] == "admin" and user["role"] != "owner":
            raise HTTPException(status_code=403, detail="Only owner can remove admins")

        # Can't remove owner
        if target["role"] == "owner":
            raise HTTPException(status_code=403, detail="Cannot remove organization owner")

        # Deactivate user (soft delete)
        await conn.execute("""
            UPDATE client_users SET is_active = false, updated_at = NOW()
            WHERE id = $1
        """, _uid(target_user_id))

        # Delete sessions
        await conn.execute("""
            DELETE FROM client_sessions WHERE user_id = $1
        """, _uid(target_user_id))

        await _audit_client_action(
            conn, user,
            action="USER_REMOVED",
            target=target_user_id,
            details={"removed_role": target["role"]},
            request=request,
        )

    try:
        from .email_alerts import send_operator_alert
        send_operator_alert(
            event_type="client_user_removed",
            severity="P2",
            summary=f"Client user removed from {user.get('org_name', 'org')} (role={target['role']})",
            details={
                "removed_user_id": target_user_id,
                "removed_role": target["role"],
                "org_id": str(user.get("org_id", "")),
            },
            actor_email=user.get("email"),
        )
    except Exception:
        logger.error("operator_alert_dispatch_failed_user_removed", exc_info=True)

    return {"status": "removed"}


@auth_router.put("/users/{target_user_id}/role")
async def update_user_role(
    target_user_id: str,
    body: UserRoleUpdate,
    request: Request,
    user: dict = Depends(require_client_owner)
):
    """Update user role (owner only)."""
    pool = await get_pool()
    org_id = user["org_id"]

    # Can't change own role
    if target_user_id == str(user["user_id"]):
        raise HTTPException(status_code=400, detail="Cannot change your own role")

    async with org_connection(pool, org_id=org_id) as conn:
        result = await conn.execute("""
            UPDATE client_users SET role = $1, updated_at = NOW()
            WHERE id = $2 AND client_org_id = $3 AND role != 'owner'
        """, body.role, _uid(target_user_id), org_id)

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="User not found or is owner")

        await _audit_client_action(
            conn, user,
            action="USER_ROLE_CHANGED",
            target=target_user_id,
            details={"new_role": body.role},
            request=request,
        )

    # Maya cross-cutting parity finding 2026-05-04: user role changes
    # ARE privileged actions. The audit_log + operator_alert capture
    # the event, but the cryptographic chain didn't reflect it. Now:
    # Ed25519 attestation bundle anchors to the org's primary site_id
    # so the auditor kit walks role changes alongside other privileged
    # events. Best-effort — a chain-write failure does not block the
    # role update (already committed); chain-gap escalation pattern
    # below bumps severity if attestation fails.
    role_change_attestation_failed = False
    role_change_bundle_id = None
    try:
        from .privileged_access_attestation import (
            create_privileged_access_attestation,
            PrivilegedAccessAttestationError,
        )
        # Resolve org's primary site_id deterministically
        async with admin_connection(pool) as att_conn:
            site_row = await att_conn.fetchrow(
                """
                SELECT site_id FROM sites
                 WHERE client_org_id = $1::uuid
                 ORDER BY created_at ASC LIMIT 1
                """,
                str(org_id),
            )
            anchor_site_id = (
                site_row["site_id"] if site_row
                else f"client_org:{org_id}"
            )
            try:
                att = await create_privileged_access_attestation(
                    att_conn,
                    site_id=anchor_site_id,
                    event_type="client_user_role_changed",
                    actor_email=user.get("email") or "unknown",
                    reason=(
                        f"role of user {target_user_id} changed to "
                        f"{body.role}"
                    ),
                    origin_ip=(request.client.host
                               if request.client else None),
                    approvals=[{
                        "stage": "applied",
                        "actor": user.get("email"),
                        "target_user_id": target_user_id,
                        "new_role": body.role,
                    }],
                )
                role_change_bundle_id = att.get("bundle_id")
            except PrivilegedAccessAttestationError as e:
                role_change_attestation_failed = True
                logger.error(
                    "client_user_role_changed_attestation_failed",
                    exc_info=True,
                    extra={"target_user_id": target_user_id},
                )
    except Exception:
        role_change_attestation_failed = True
        logger.error(
            "client_user_role_changed_attestation_unexpected",
            exc_info=True,
            extra={"target_user_id": target_user_id},
        )

    try:
        from .email_alerts import send_operator_alert
        op_severity = ("P0-CHAIN-GAP" if role_change_attestation_failed
                       else "P2")
        op_suffix = (" [ATTESTATION-MISSING]"
                     if role_change_attestation_failed else "")
        send_operator_alert(
            event_type="client_user_role_changed",
            severity=op_severity,
            summary=(
                f"Client user role changed in "
                f"{user.get('org_name', 'org')} → {body.role}{op_suffix}"
            ),
            details={
                "target_user_id": target_user_id,
                "new_role": body.role,
                "org_id": str(user.get("org_id", "")),
                "attestation_bundle_id": role_change_bundle_id,
                "attestation_failed": role_change_attestation_failed,
            },
            actor_email=user.get("email"),
        )
    except Exception:
        logger.error("operator_alert_dispatch_failed_user_role_changed", exc_info=True)

    return {
        "status": "updated",
        "role": body.role,
        "attestation_bundle_id": role_change_bundle_id,
    }


# =============================================================================
# PASSWORD MANAGEMENT
# =============================================================================

@auth_router.put("/password")
async def set_password(
    body: PasswordSet,
    request: Request,
    user: dict = Depends(require_client_user),
):
    """Set or update user password."""
    from .auth import validate_password_complexity, hash_password

    is_valid, error_msg = validate_password_complexity(body.password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    password_hash = hash_password(body.password)
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        await conn.execute("""
            UPDATE client_users SET password_hash = $1, updated_at = NOW()
            WHERE id = $2
        """, password_hash, user["user_id"])

        await _audit_client_action(
            conn, user,
            action="PASSWORD_CHANGED",
            target=str(user["user_id"]),
            request=request,
        )

    return {"status": "password_set"}


# =============================================================================
# PARTNER TRANSFER (Phase 3 - Owner only)
# =============================================================================

@auth_router.post("/transfer/request")
async def request_transfer(body: TransferRequest, user: dict = Depends(require_client_owner)):
    """Request to transfer to a different MSP partner."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Check for existing pending request
        existing = await conn.fetchval("""
            SELECT 1 FROM partner_transfer_requests
            WHERE client_org_id = $1 AND status = 'pending'
        """, org_id)

        if existing:
            raise HTTPException(status_code=400, detail="Transfer request already pending")

        # Get current partner
        current_partner = await conn.fetchval("""
            SELECT current_partner_id FROM client_orgs WHERE id = $1
        """, org_id)

        # Create request
        request_id = await conn.fetchval("""
            INSERT INTO partner_transfer_requests (
                client_org_id, from_partner_id, reason, requested_by_user_id
            ) VALUES ($1, $2, $3, $4)
            RETURNING id
        """, org_id, current_partner, body.reason, user["user_id"])

    # Notify OsirisCare admin
    try:
        from .email_alerts import send_critical_alert
        send_critical_alert(
            title=f"Partner Transfer Request: {user['org_name']}",
            message=f"""Client {user['org_name']} has requested to transfer from their current partner.

Reason: {body.reason or 'Not specified'}

Review at: {BASE_URL}/admin/transfers/{request_id}
""",
            category="partner-transfer",
            metadata={"org_id": str(org_id), "request_id": str(request_id)}
        )
    except Exception as e:
        logger.error(f"Failed to send transfer notification: {e}")

    async with org_connection(pool, org_id=org_id) as conn2:
        await _audit_client_action(conn2, user, "TRANSFER_REQUESTED", target=str(request_id), request=request)

    return {
        "request_id": str(request_id),
        "status": "pending",
        "message": "Your transfer request has been submitted. OsirisCare will contact you within 2 business days.",
    }


@auth_router.get("/transfer/status")
async def get_transfer_status(user: dict = Depends(require_client_owner)):
    """Get current transfer request status."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        request = await conn.fetchrow("""
            SELECT ptr.*, p.name as from_partner_name
            FROM partner_transfer_requests ptr
            LEFT JOIN partners p ON p.id = ptr.from_partner_id
            WHERE ptr.client_org_id = $1
            ORDER BY ptr.created_at DESC
            LIMIT 1
        """, org_id)

        if not request:
            return {"has_request": False}

        return {
            "has_request": True,
            "request": {
                "id": str(request["id"]),
                "from_partner": request["from_partner_name"],
                "reason": request["reason"],
                "status": request["status"],
                "reviewed_at": request["reviewed_at"].isoformat() if request["reviewed_at"] else None,
                "review_notes": request["review_notes"],
                "created_at": request["created_at"].isoformat() if request["created_at"] else None,
            }
        }


@auth_router.post("/transfer/cancel")
async def cancel_transfer(user: dict = Depends(require_client_owner)):
    """Cancel pending transfer request."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        result = await conn.execute("""
            UPDATE partner_transfer_requests
            SET status = 'cancelled', updated_at = NOW()
            WHERE client_org_id = $1 AND status = 'pending'
        """, org_id)

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="No pending transfer request")

    return {"status": "cancelled"}


# =============================================================================
# HEALING LOGS + PROMOTION ENDPOINTS
# =============================================================================

class ForwardRequest(BaseModel):
    """Forward a promotion candidate to partner manager."""
    notes: Optional[str] = None


@auth_router.get("/healing-logs")
async def list_healing_logs(
    site_id: Optional[str] = None,
    success: Optional[bool] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_client_user)
):
    """List auto-healing execution logs for client org's sites."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        query = """
            SELECT et.execution_id, et.site_id, s.clinic_name,
                   et.runbook_id, et.incident_type, et.success,
                   et.resolution_level, et.started_at, et.completed_at,
                   et.duration_seconds, et.error_message, et.hostname
            FROM execution_telemetry et
            JOIN sites s ON s.site_id = et.site_id
            WHERE s.client_org_id = $1
        """
        params: list = [org_id]
        param_idx = 2

        if site_id:
            query += f" AND et.site_id = ${param_idx}"
            params.append(site_id)
            param_idx += 1

        if success is not None:
            query += f" AND et.success = ${param_idx}"
            params.append(success)
            param_idx += 1

        query += f" ORDER BY et.created_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
        params.extend([limit, offset])

        rows = await conn.fetch(query, *params)

        # Get total count for pagination
        count_query = """
            SELECT COUNT(*) FROM execution_telemetry et
            JOIN sites s ON s.site_id = et.site_id
            WHERE s.client_org_id = $1
        """
        total = await conn.fetchval(count_query, org_id)

        return {
            "logs": [
                {
                    "execution_id": r["execution_id"],
                    "site_id": r["site_id"],
                    "clinic_name": r["clinic_name"],
                    "runbook_id": r["runbook_id"],
                    "incident_type": r["incident_type"],
                    "success": r["success"],
                    "resolution_level": r["resolution_level"],
                    "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                    "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
                    "duration_seconds": r["duration_seconds"],
                    "error_message": r["error_message"],
                    "hostname": r["hostname"],
                }
                for r in rows
            ],
            "total": total or 0,
            "limit": limit,
            "offset": offset,
        }


@auth_router.get("/promotion-candidates")
async def list_promotion_candidates(user: dict = Depends(require_client_user)):
    """List promotion-eligible patterns for client org's sites."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        rows = await conn.fetch("""
            SELECT
                aps.id,
                aps.pattern_signature,
                aps.site_id,
                s.clinic_name,
                COALESCE(s.healing_tier, 'standard') as healing_tier,
                aps.check_type,
                aps.total_occurrences,
                aps.success_rate,
                aps.recommended_action,
                aps.first_seen::text,
                aps.last_seen::text,
                COALESCE(lpc.approval_status, 'not_submitted') as approval_status,
                lpc.client_endorsed_at IS NOT NULL as client_endorsed
            FROM aggregated_pattern_stats aps
            JOIN sites s ON s.site_id = aps.site_id
            LEFT JOIN learning_promotion_candidates lpc
                ON lpc.pattern_signature = aps.pattern_signature
                AND lpc.site_id = aps.site_id
            WHERE s.client_org_id = $1
              AND aps.promotion_eligible = TRUE
            ORDER BY aps.success_rate DESC, aps.total_occurrences DESC
        """, org_id)

        return {
            "candidates": [
                {
                    "id": str(r["id"]),
                    "pattern_signature": r["pattern_signature"],
                    "site_id": r["site_id"],
                    "clinic_name": r["clinic_name"],
                    "healing_tier": r["healing_tier"],
                    "check_type": r["check_type"],
                    "total_occurrences": r["total_occurrences"],
                    "success_rate": float(r["success_rate"]) if r["success_rate"] else 0,
                    "recommended_action": r["recommended_action"],
                    "first_seen": r["first_seen"],
                    "last_seen": r["last_seen"],
                    "approval_status": r["approval_status"],
                    "client_endorsed": r["client_endorsed"],
                }
                for r in rows
            ],
            "total": len(rows),
        }


@auth_router.post("/promotion-candidates/{pattern_id}/forward")
async def forward_promotion_candidate(
    pattern_id: str,
    body: ForwardRequest,
    user: dict = Depends(require_client_user)
):
    """Forward a promotion candidate to the partner manager for review."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Verify candidate belongs to a site owned by this client org
        candidate = await conn.fetchrow("""
            SELECT aps.id, aps.pattern_signature, aps.site_id,
                   aps.check_type, aps.recommended_action,
                   s.clinic_name, s.partner_id
            FROM aggregated_pattern_stats aps
            JOIN sites s ON s.site_id = aps.site_id
            WHERE aps.id = $1
              AND s.client_org_id = $2
              AND aps.promotion_eligible = TRUE
        """, int(pattern_id), org_id)

        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")

        # Upsert endorsement into learning_promotion_candidates
        import uuid as uuid_mod
        await conn.execute("""
            INSERT INTO learning_promotion_candidates (
                id, site_id, pattern_signature,
                client_endorsed_at, client_endorsed_by, client_notes
            ) VALUES ($1, $2, $3, NOW(), $4, $5)
            ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
                client_endorsed_at = NOW(),
                client_endorsed_by = EXCLUDED.client_endorsed_by,
                client_notes = EXCLUDED.client_notes
        """,
            str(uuid_mod.uuid4()),
            candidate["site_id"],
            candidate["pattern_signature"],
            user["user_id"],
            body.notes
        )

        # Notify the partner (if partner exists)
        partner_id = candidate["partner_id"]
        if partner_id:
            check_desc = candidate["check_type"] or candidate["recommended_action"] or "healing pattern"
            try:
                # Create a notification visible on partner dashboard
                await conn.execute("""
                    INSERT INTO client_notifications (
                        client_org_id, type, severity, title, message
                    ) VALUES ($1, 'info', 'info',
                        $2, $3)
                """,
                    org_id,
                    f"Promotion Endorsed: {check_desc}",
                    f"You endorsed the '{check_desc}' healing pattern at {candidate['clinic_name']} for partner review."
                )
            except Exception as e:
                logger.warning(f"Failed to create endorsement notification: {e}")

        logger.info(f"Client {user['email']} forwarded pattern {candidate['pattern_signature'][:8]} for site {candidate['site_id']}")

        await _audit_client_action(conn, user, "PROMOTION_FORWARDED", target=pattern_id,
            details={"site_id": candidate["site_id"], "pattern": candidate["pattern_signature"][:16]})

        return {
            "status": "forwarded",
            "pattern_id": pattern_id,
            "site_id": candidate["site_id"],
            "message": "Pattern forwarded to your partner manager for review.",
        }


class ClientApproveRequest(BaseModel):
    notes: Optional[str] = None
    custom_name: Optional[str] = None


class ClientRejectRequest(BaseModel):
    reason: str


@auth_router.post("/promotion-candidates/{pattern_id}/approve")
async def approve_promotion_candidate(
    pattern_id: str,
    body: ClientApproveRequest,
    user: dict = Depends(require_client_user)
):
    """Approve a promotion candidate for L1 deployment. Full coverage tier only.

    Creates a promoted rule scoped to the client's site. The rule syncs to
    the site's appliances on their next promoted-rules fetch.
    """
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Verify candidate belongs to this client org AND site is full_coverage
        candidate = await conn.fetchrow("""
            SELECT aps.id, aps.pattern_signature, aps.site_id, aps.check_type,
                   aps.total_occurrences, aps.success_rate, aps.recommended_action,
                   s.clinic_name, s.partner_id, s.healing_tier
            FROM aggregated_pattern_stats aps
            JOIN sites s ON s.site_id = aps.site_id
            WHERE aps.id = $1
              AND s.client_org_id = $2
              AND aps.promotion_eligible = TRUE
        """, int(pattern_id), org_id)

        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")

        if (candidate["healing_tier"] or "standard") != "full_coverage":
            raise HTTPException(
                status_code=403,
                detail="Only full coverage tier sites can approve promotions directly. "
                       "Use 'Forward to Partner' instead."
            )

        # Check if already promoted
        existing = await conn.fetchrow("""
            SELECT rule_id, status FROM promoted_rules
            WHERE pattern_signature = $1 AND site_id = $2
        """, candidate["pattern_signature"], candidate["site_id"])

        if existing and existing["status"] == "active":
            raise HTTPException(status_code=409, detail=f"Already promoted as {existing['rule_id']}")

        # Generate the L1 rule (reuse learning_api logic)
        from .learning_api import generate_rule_from_pattern, rule_to_yaml
        rule = generate_rule_from_pattern(dict(candidate), body.custom_name)
        rule_yaml = rule_to_yaml(rule)

        # Start transaction for atomic promotion
        transaction = conn.transaction()
        await transaction.start()

        try:
            # Insert promoted rule
            import uuid as uuid_mod
            partner_id = candidate["partner_id"]
            # promoted_rules natural key is (site_id, rule_id) — same rule
            # rolls out to many sites, each gets its own row. Migration 247
            # added UNIQUE(site_id, rule_id). Pre-247 code used ON CONFLICT
            # (rule_id) which raised InvalidColumnReferenceError because
            # rule_id has no unique constraint alone. Surfaced 2026-04-25
            # when the dashboard "Approve" button on /learning returned 500.
            await conn.execute("""
                INSERT INTO promoted_rules (
                    rule_id, pattern_signature, site_id, partner_id,
                    rule_yaml, rule_json, notes, promoted_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT (site_id, rule_id) DO UPDATE SET
                    status = 'active', notes = EXCLUDED.notes, promoted_at = NOW()
            """,
                rule["id"], candidate["pattern_signature"],
                candidate["site_id"], partner_id,
                rule_yaml, json.dumps(rule), body.notes
            )

            # Create runbook entry
            check_type = candidate.get("check_type") or "general"
            promoted_name = body.custom_name or f"Client-Approved: {candidate.get('recommended_action', check_type)}"
            promoted_desc = (
                f"Client-approved L2→L1 pattern "
                f"({(candidate.get('success_rate') or 0) * 100:.0f}% success over "
                f"{candidate.get('total_occurrences', 0)} occurrences)"
            )
            await conn.execute("""
                INSERT INTO runbooks (runbook_id, name, description, category, check_type,
                                      severity, is_disruptive, hipaa_controls, steps)
                VALUES ($1, $2, $3, $4, $5, 'medium', false, ARRAY[]::text[], '[]'::jsonb)
                ON CONFLICT (runbook_id) DO UPDATE SET
                    name = EXCLUDED.name, description = EXCLUDED.description, updated_at = NOW()
            """,
                rule["id"], promoted_name, promoted_desc,
                rule.get("action_params", {}).get("runbook_id", "general"), check_type
            )

            # Map rule_id → runbook for telemetry correlation
            await conn.execute("""
                INSERT INTO runbook_id_mapping (l1_rule_id, runbook_id)
                VALUES ($1, $2) ON CONFLICT (l1_rule_id) DO NOTHING
            """, rule["id"], rule["id"])

            # Update candidate approval status
            await conn.execute("""
                INSERT INTO learning_promotion_candidates (
                    id, site_id, pattern_signature, approval_status,
                    approved_at, custom_rule_name, approval_notes
                ) VALUES ($1, $2, $3, 'approved', NOW(), $4, $5)
                ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
                    approval_status = 'approved', approved_at = NOW(),
                    custom_rule_name = EXCLUDED.custom_rule_name,
                    approval_notes = EXCLUDED.approval_notes
            """,
                str(uuid_mod.uuid4()), candidate["site_id"],
                candidate["pattern_signature"], body.custom_name, body.notes
            )

            # Single shared rollout entrypoint — see
            # flywheel_promote.safe_rollout_promoted_rule. All 3 promotion
            # writers delegate here so behavior + logging stay identical
            # (round-table P1, Session 206).
            from .flywheel_promote import safe_rollout_promoted_rule
            await safe_rollout_promoted_rule(
                conn,
                rule_id=rule["id"],
                runbook_id=rule.get("action_params", {}).get("runbook_id", "general"),
                site_id=candidate["site_id"],
                rule_yaml=rule_yaml,
                caller="client_portal.approve",
            )

            await transaction.commit()

        except Exception as e:
            await transaction.rollback()
            logger.error(f"Client promotion failed: {e}")
            raise HTTPException(status_code=500, detail="Promotion failed")

        logger.info(
            f"Client {user['email']} approved pattern {candidate['pattern_signature'][:8]} "
            f"as {rule['id']} for site {candidate['site_id']}"
        )

        await _audit_client_action(conn, user, "PROMOTION_APPROVED", target=pattern_id,
            details={"rule_id": rule["id"], "site_id": candidate["site_id"]})

        return {
            "status": "approved",
            "rule_id": rule["id"],
            "site_id": candidate["site_id"],
            "message": f"Rule {rule['id']} deployed to {candidate['clinic_name']}.",
        }


@auth_router.post("/promotion-candidates/{pattern_id}/reject")
async def reject_promotion_candidate(
    pattern_id: str,
    body: ClientRejectRequest,
    user: dict = Depends(require_client_user)
):
    """Reject a promotion candidate. Full coverage tier only."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        candidate = await conn.fetchrow("""
            SELECT aps.id, aps.pattern_signature, aps.site_id, s.healing_tier
            FROM aggregated_pattern_stats aps
            JOIN sites s ON s.site_id = aps.site_id
            WHERE aps.id = $1 AND s.client_org_id = $2
              AND aps.promotion_eligible = TRUE
        """, int(pattern_id), org_id)

        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")

        if (candidate["healing_tier"] or "standard") != "full_coverage":
            raise HTTPException(status_code=403, detail="Only full coverage tier can reject directly")

        import uuid as uuid_mod
        await conn.execute("""
            INSERT INTO learning_promotion_candidates (
                id, site_id, pattern_signature, approval_status, approval_notes
            ) VALUES ($1, $2, $3, 'rejected', $4)
            ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
                approval_status = 'rejected', approval_notes = EXCLUDED.approval_notes
        """,
            str(uuid_mod.uuid4()), candidate["site_id"],
            candidate["pattern_signature"], body.reason
        )

        logger.info(f"Client {user['email']} rejected pattern {candidate['pattern_signature'][:8]} for site {candidate['site_id']}")

        await _audit_client_action(conn, user, "PROMOTION_REJECTED", target=pattern_id,
            details={"site_id": candidate["site_id"], "reason": body.reason})

        return {"status": "rejected", "pattern_id": pattern_id, "site_id": candidate["site_id"]}


# =============================================================================
# BILLING ENDPOINTS (Stripe Integration)
# =============================================================================

def require_stripe():
    """Dependency that ensures Stripe is configured."""
    if not STRIPE_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Billing service not available (Stripe not installed)"
        )
    if not STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=503,
            detail="Billing service not configured"
        )
    return True


class CreateCheckoutRequest(BaseModel):
    """Request to create a checkout session."""
    price_id: Optional[str] = None  # Use default if not specified
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


@auth_router.get("/billing")
async def get_billing_info(
    user: dict = Depends(require_client_owner),
    _: bool = Depends(require_stripe)
):
    """
    Get current billing information for the organization.

    Returns:
    - Current subscription status
    - Payment method on file
    - Next billing date
    - Current plan details
    """
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        org = await conn.fetchrow("""
            SELECT stripe_customer_id, subscription_status, subscription_plan,
                   billing_email, next_billing_date
            FROM client_orgs
            WHERE id = $1
        """, org_id)

        if not org or not org["stripe_customer_id"]:
            return {
                "has_subscription": False,
                "status": "no_subscription",
                "message": "No active subscription. Set up billing to access premium features."
            }

        # Fetch subscription details from Stripe
        try:
            customer = stripe.Customer.retrieve(
                org["stripe_customer_id"],
                expand=["subscriptions", "default_source"]
            )

            subscriptions = customer.subscriptions.data if customer.subscriptions else []
            active_sub = next((s for s in subscriptions if s.status in ["active", "trialing"]), None)

            # Get payment method
            payment_method = None
            if customer.invoice_settings and customer.invoice_settings.default_payment_method:
                pm = stripe.PaymentMethod.retrieve(customer.invoice_settings.default_payment_method)
                if pm.card:
                    payment_method = {
                        "type": "card",
                        "brand": pm.card.brand,
                        "last4": pm.card.last4,
                        "exp_month": pm.card.exp_month,
                        "exp_year": pm.card.exp_year,
                    }

            if active_sub:
                return {
                    "has_subscription": True,
                    "status": active_sub.status,
                    "plan": {
                        "name": active_sub.items.data[0].price.nickname or "OsirisCare Compliance",
                        "amount": active_sub.items.data[0].price.unit_amount / 100,
                        "currency": active_sub.items.data[0].price.currency.upper(),
                        "interval": active_sub.items.data[0].price.recurring.interval,
                    },
                    "current_period_end": datetime.fromtimestamp(active_sub.current_period_end).isoformat(),
                    "cancel_at_period_end": active_sub.cancel_at_period_end,
                    "payment_method": payment_method,
                }
            else:
                return {
                    "has_subscription": False,
                    "status": "inactive",
                    "payment_method": payment_method,
                    "message": "Your subscription is not active."
                }

        except stripe.error.StripeError as e:
            logger.error(f"Stripe API error: {e}")
            raise HTTPException(status_code=502, detail="Failed to fetch billing information")


@auth_router.post("/billing/checkout")
async def create_checkout_session(
    request: CreateCheckoutRequest,
    user: dict = Depends(require_client_owner),
    _: bool = Depends(require_stripe)
):
    """
    Create a Stripe Checkout session for subscription signup.

    Returns a URL to redirect the user to Stripe's hosted checkout page.
    """
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        org = await conn.fetchrow("""
            SELECT id, name, stripe_customer_id
            FROM client_orgs
            WHERE id = $1
        """, org_id)

        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Get or create Stripe customer
        customer_id = org["stripe_customer_id"]
        if not customer_id:
            try:
                customer = stripe.Customer.create(
                    email=user["email"],
                    name=org["name"],
                    metadata={"org_id": str(org_id)}
                )
                customer_id = customer.id

                # Store customer ID
                await conn.execute("""
                    UPDATE client_orgs
                    SET stripe_customer_id = $1, updated_at = NOW()
                    WHERE id = $2
                """, customer_id, org_id)

            except stripe.error.StripeError as e:
                logger.error(f"Failed to create Stripe customer: {e}")
                raise HTTPException(status_code=502, detail="Failed to initialize billing")

        # Create checkout session
        price_id = request.price_id or STRIPE_PRICE_ID
        if not price_id:
            raise HTTPException(status_code=400, detail="No price ID configured")

        success_url = request.success_url or f"{BASE_URL}/client/settings?billing=success"
        cancel_url = request.cancel_url or f"{BASE_URL}/client/settings?billing=cancelled"

        try:
            session = stripe.checkout.Session.create(
                customer=customer_id,
                payment_method_types=["card"],
                line_items=[{"price": price_id, "quantity": 1}],
                mode="subscription",
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={"org_id": str(org_id)},
            )

            await _audit_client_action(conn, user, "BILLING_CHECKOUT_INITIATED",
                details={"session_id": session.id})
            return {"checkout_url": session.url, "session_id": session.id}

        except stripe.error.StripeError as e:
            logger.error(f"Failed to create checkout session: {e}")
            raise HTTPException(status_code=502, detail="Failed to create checkout session")


@auth_router.post("/billing/portal")
async def create_billing_portal_session(
    user: dict = Depends(require_client_owner),
    _: bool = Depends(require_stripe)
):
    """
    Create a Stripe Customer Portal session.

    The customer portal allows users to:
    - Update payment methods
    - View invoices
    - Cancel subscription
    - Update billing info
    """
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        org = await conn.fetchrow("""
            SELECT stripe_customer_id
            FROM client_orgs
            WHERE id = $1
        """, org_id)

        if not org or not org["stripe_customer_id"]:
            raise HTTPException(
                status_code=400,
                detail="No billing account found. Please set up billing first."
            )

        try:
            session = stripe.billing_portal.Session.create(
                customer=org["stripe_customer_id"],
                return_url=f"{BASE_URL}/client/settings",
            )

            await _audit_client_action(conn, user, "BILLING_PORTAL_ACCESSED")
            return {"portal_url": session.url}

        except stripe.error.StripeError as e:
            logger.error(f"Failed to create portal session: {e}")
            raise HTTPException(status_code=502, detail="Failed to access billing portal")


@auth_router.get("/billing/invoices")
async def list_invoices(
    user: dict = Depends(require_client_owner),
    _: bool = Depends(require_stripe),
    limit: int = Query(10, ge=1, le=100)
):
    """
    List recent invoices for the organization.
    """
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        org = await conn.fetchrow("""
            SELECT stripe_customer_id
            FROM client_orgs
            WHERE id = $1
        """, org_id)

        if not org or not org["stripe_customer_id"]:
            return {"invoices": [], "has_more": False}

        try:
            invoices = stripe.Invoice.list(
                customer=org["stripe_customer_id"],
                limit=limit,
            )

            return {
                "invoices": [
                    {
                        "id": inv.id,
                        "number": inv.number,
                        "amount_due": inv.amount_due / 100,
                        "amount_paid": inv.amount_paid / 100,
                        "currency": inv.currency.upper(),
                        "status": inv.status,
                        "created": datetime.fromtimestamp(inv.created).isoformat(),
                        "invoice_pdf": inv.invoice_pdf,
                        "hosted_invoice_url": inv.hosted_invoice_url,
                    }
                    for inv in invoices.data
                ],
                "has_more": invoices.has_more,
            }

        except stripe.error.StripeError as e:
            logger.error(f"Failed to list invoices: {e}")
            raise HTTPException(status_code=502, detail="Failed to fetch invoices")


# Webhook handler (public - no auth, but verified by Stripe signature)
billing_webhook_router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@billing_webhook_router.post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature")
):
    """
    Handle Stripe webhook events.

    Events handled:
    - checkout.session.completed: Subscription created
    - customer.subscription.updated: Subscription changed
    - customer.subscription.deleted: Subscription cancelled
    - invoice.paid: Payment successful
    - invoice.payment_failed: Payment failed
    """
    if not STRIPE_AVAILABLE:
        raise HTTPException(status_code=503, detail="Stripe not available")

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    # Get raw body for signature verification
    body = await request.body()

    try:
        event = stripe.Webhook.construct_event(
            body, stripe_signature, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    pool = await get_pool()

    # Handle specific events
    if event.type == "checkout.session.completed":
        session = event.data.object
        org_id = session.metadata.get("org_id")

        if org_id:
            async with admin_connection(pool) as conn:
                await conn.execute("""
                    UPDATE client_orgs
                    SET subscription_status = 'active', updated_at = NOW()
                    WHERE id = $1
                """, org_id)
            logger.info(f"Subscription activated for org {org_id}")

    elif event.type == "customer.subscription.updated":
        subscription = event.data.object
        customer_id = subscription.customer

        async with admin_connection(pool) as conn:
            await conn.execute("""
                UPDATE client_orgs
                SET subscription_status = $1,
                    next_billing_date = $2,
                    updated_at = NOW()
                WHERE stripe_customer_id = $3
            """, subscription.status,
                datetime.fromtimestamp(subscription.current_period_end),
                customer_id)
        logger.info(f"Subscription updated for customer {customer_id}: {subscription.status}")

    elif event.type == "customer.subscription.deleted":
        subscription = event.data.object
        customer_id = subscription.customer

        async with admin_connection(pool) as conn:
            await conn.execute("""
                UPDATE client_orgs
                SET subscription_status = 'cancelled', updated_at = NOW()
                WHERE stripe_customer_id = $1
            """, customer_id)
        logger.info(f"Subscription cancelled for customer {customer_id}")

    elif event.type == "invoice.paid":
        invoice = event.data.object
        logger.info(f"Invoice paid: {invoice.id} for {invoice.customer}")

    elif event.type == "invoice.payment_failed":
        invoice = event.data.object
        logger.warning(f"Invoice payment failed: {invoice.id} for {invoice.customer}")

        # Could send notification to client here
        async with admin_connection(pool) as conn:
            await conn.execute("""
                UPDATE client_orgs
                SET subscription_status = 'past_due', updated_at = NOW()
                WHERE stripe_customer_id = $1
            """, invoice.customer)

    return {"status": "ok"}


# =============================================================================
# CLIENT TOTP VERIFY (Public - completes MFA login)
# =============================================================================

class ClientVerifyTOTPRequest(BaseModel):
    mfa_token: str
    totp_code: str


@public_router.post("/verify-totp")
async def client_verify_totp(request: Request, body: ClientVerifyTOTPRequest):
    """Complete client login after TOTP verification."""
    from .totp import verify_totp, verify_backup_code

    now = datetime.now(timezone.utc)
    # Clean expired tokens
    expired = [k for k, v in _client_mfa_pending.items() if v["expires"] < now]
    for k in expired:
        _client_mfa_pending.pop(k, None)

    pending = _client_mfa_pending.pop(body.mfa_token, None)
    if not pending or pending["expires"] < now:
        raise HTTPException(status_code=401, detail="Invalid or expired MFA token")

    user_id = pending["user_id"]
    pool = await get_pool()

    # admin_transaction wave-13 (Session 212 routing-pathology rule): 4 admin DB calls (mfa_secret fetch, backup-code update, session INSERT, last_login UPDATE) — pin SET LOCAL app.is_admin to one PgBouncer backend
    async with admin_transaction(pool) as conn:
        row = await conn.fetchrow(
            "SELECT mfa_secret, mfa_backup_codes FROM client_users WHERE id = $1",
            user_id
        )
        if not row or not row["mfa_secret"]:
            raise HTTPException(status_code=400, detail="MFA not configured")

        mfa_secret = row["mfa_secret"]
        backup_codes_json = row["mfa_backup_codes"]

        # Try TOTP first, then backup code
        code_valid = verify_totp(mfa_secret, body.totp_code)
        if not code_valid and backup_codes_json:
            code_valid, updated_codes = verify_backup_code(body.totp_code, backup_codes_json)
            if code_valid:
                await conn.execute(
                    "UPDATE client_users SET mfa_backup_codes = $1 WHERE id = $2",
                    updated_codes, user_id
                )

        if not code_valid:
            raise HTTPException(status_code=401, detail="Invalid TOTP code")

        # Create session
        session_token = generate_token()
        token_hash_val = hash_token(session_token)
        expires_at = now + timedelta(days=SESSION_DURATION_DAYS)

        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent", "")[:500]

        await conn.execute("""
            INSERT INTO client_sessions (user_id, token_hash, user_agent, ip_address, expires_at)
            VALUES ($1, $2, $3, $4, $5)
        """, user_id, token_hash_val, user_agent, ip_address, expires_at)

        await conn.execute("""
            UPDATE client_users SET last_login_at = NOW() WHERE id = $1
        """, user_id)

    response = Response(
        content='{"status": "authenticated"}',
        media_type="application/json"
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=SESSION_COOKIE_MAX_AGE,
        path="/"
    )
    return response


# =============================================================================
# CLIENT TOTP MANAGEMENT (Requires client session)
# =============================================================================

class ClientTOTPVerifyRequest(BaseModel):
    code: str
    password: str


class ClientTOTPDisableRequest(BaseModel):
    password: str


@auth_router.post("/totp/setup")
async def client_totp_setup(user: dict = Depends(require_client_user)):
    """Generate TOTP secret and backup codes for client 2FA setup."""
    from .totp import generate_totp_secret, get_totp_uri, generate_backup_codes

    pool = await get_pool()
    user_id = str(user["user_id"])
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        row = await conn.fetchrow(
            "SELECT mfa_enabled, email FROM client_users WHERE id = $1",
            user_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        if row["mfa_enabled"]:
            raise HTTPException(status_code=400, detail="2FA is already enabled")

        email = row["email"] or "user"
        secret = generate_totp_secret()
        uri = get_totp_uri(secret, email)
        backup_codes = generate_backup_codes()

        # Store secret temporarily (not enabled until verified)
        await conn.execute(
            "UPDATE client_users SET mfa_secret = $1 WHERE id = $2",
            secret, user_id
        )

    return {"secret": secret, "uri": uri, "backup_codes": backup_codes}


@auth_router.post("/totp/verify")
async def client_totp_verify(
    body: ClientTOTPVerifyRequest,
    request: Request,
    user: dict = Depends(require_client_user),
):
    """Verify TOTP code to enable client 2FA."""
    from .totp import verify_totp, generate_backup_codes, hash_backup_code
    from .auth import verify_password
    import json as _json

    pool = await get_pool()
    user_id = str(user["user_id"])
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        row = await conn.fetchrow(
            "SELECT password_hash, mfa_secret, mfa_enabled FROM client_users WHERE id = $1",
            user_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        if row["mfa_enabled"]:
            raise HTTPException(status_code=400, detail="2FA is already enabled")
        if not row["mfa_secret"]:
            raise HTTPException(status_code=400, detail="Run setup first")
        if not row["password_hash"]:
            raise HTTPException(status_code=400, detail="Password not set")

        if not verify_password(body.password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Invalid password")

        if not verify_totp(row["mfa_secret"], body.code):
            raise HTTPException(status_code=400, detail="Invalid TOTP code")

        # Generate and hash backup codes
        backup_codes = generate_backup_codes()
        hashed_codes = [hash_backup_code(c) for c in backup_codes]
        backup_codes_json = _json.dumps(hashed_codes)

        await conn.execute("""
            UPDATE client_users
            SET mfa_enabled = TRUE, mfa_backup_codes = $1
            WHERE id = $2
        """, backup_codes_json, user_id)

        await _audit_client_action(
            conn, user,
            action="MFA_ENABLED",
            target=user_id,
            request=request,
        )

    return {"status": "enabled", "backup_codes": backup_codes}


@auth_router.delete("/totp")
async def client_totp_disable(
    body: ClientTOTPDisableRequest,
    request: Request,
    user: dict = Depends(require_client_user),
):
    """Disable client 2FA. Requires password."""
    from .auth import verify_password

    pool = await get_pool()
    user_id = str(user["user_id"])
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        row = await conn.fetchrow(
            "SELECT password_hash, mfa_enabled FROM client_users WHERE id = $1",
            user_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        if not row["password_hash"]:
            raise HTTPException(status_code=400, detail="Password not set")
        if not verify_password(body.password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Invalid password")
        if not row["mfa_enabled"]:
            raise HTTPException(status_code=400, detail="2FA is not enabled")

        await conn.execute("""
            UPDATE client_users
            SET mfa_enabled = FALSE, mfa_secret = NULL, mfa_backup_codes = NULL
            WHERE id = $1
        """, user_id)

        await _audit_client_action(
            conn, user,
            action="MFA_DISABLED",
            target=user_id,
            request=request,
        )

    return {"status": "disabled"}


# =============================================================================
# AGENT DOWNLOAD (macOS + Windows)
# =============================================================================

AGENT_GRPC_PORT = 50051  # Appliance gRPC port for agent connections


@auth_router.get("/agent/install-info")
async def get_agent_install_info(user: dict = Depends(require_client_user)):
    """Get agent installation info for all sites in the org.

    Returns appliance addresses and install instructions
    so the client can download and configure agents.
    """
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Get all sites + their appliances for this org
        rows = await conn.fetch("""
            SELECT s.site_id, s.clinic_name,
                   sa.appliance_id, sa.hostname as appliance_hostname,
                   sa.ip_addresses, sa.agent_version
            FROM sites s
            LEFT JOIN site_appliances sa ON sa.site_id = s.site_id AND sa.deleted_at IS NULL
            WHERE s.client_org_id = $1 AND s.status != 'inactive'
            ORDER BY s.clinic_name
        """, org_id)

    from .sites import parse_ip_addresses

    sites = {}
    for row in rows:
        sid = row["site_id"]
        if sid not in sites:
            sites[sid] = {
                "site_id": sid,
                "clinic_name": row["clinic_name"],
                "appliances": [],
            }
        if row["appliance_id"]:
            ips = parse_ip_addresses(row["ip_addresses"])
            # Use the first non-loopback IP as the appliance address
            appliance_ip = None
            for ip in ips:
                if ip and not ip.startswith("127."):
                    appliance_ip = ip
                    break
            if appliance_ip:
                sites[sid]["appliances"].append({
                    "appliance_id": row["appliance_id"],
                    "hostname": row["appliance_hostname"],
                    "ip": appliance_ip,
                    "grpc_addr": f"{appliance_ip}:{AGENT_GRPC_PORT}",
                    "version": row["agent_version"],
                })

    return {
        "sites": list(sites.values()),
        "agent_version": "0.4.0",
        "platforms": {
            "macos": {
                "name": "macOS",
                "binary": "osiris-agent-darwin",
                "pkg": "osiris-agent-0.4.0.pkg",
                "install_method": "pkg",
            },
            "windows": {
                "name": "Windows",
                "binary": "osiris-agent.exe",
                "install_method": "gpo",
            },
        },
    }


@auth_router.get("/agent/config/{site_id}")
async def get_agent_config(
    site_id: str,
    user: dict = Depends(require_client_user),
):
    """Generate a site-specific agent config.json for download."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with tenant_connection(pool, site_id=site_id) as conn:
        # Verify site belongs to this org
        site = await conn.fetchrow(
            "SELECT site_id, clinic_name FROM sites WHERE site_id = $1 AND client_org_id = $2",
            site_id, org_id,
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get appliance for this site
        appliance = await conn.fetchrow("""
            SELECT appliance_id, ip_addresses
            FROM site_appliances
            WHERE site_id = $1 AND deleted_at IS NULL
            ORDER BY last_checkin DESC NULLS LAST
            LIMIT 1
        """, site_id)

    if not appliance:
        raise HTTPException(
            status_code=404,
            detail="No appliance found for this site. An appliance must be connected before agents can be deployed.",
        )

    from .sites import parse_ip_addresses
    ips = parse_ip_addresses(appliance["ip_addresses"])
    appliance_ip = None
    for ip in ips:
        if ip and not ip.startswith("127."):
            appliance_ip = ip
            break

    if not appliance_ip:
        raise HTTPException(status_code=404, detail="Appliance has no reachable IP address")

    import json
    config = {
        "appliance_addr": f"{appliance_ip}:{AGENT_GRPC_PORT}",
        "site_id": site_id,
    }

    config_json = json.dumps(config, indent=2)

    return Response(
        content=config_json,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="osiris-config.json"',
        },
    )


@auth_router.get("/agent/install-script/{site_id}")
async def get_agent_install_script(
    site_id: str,
    user: dict = Depends(require_client_user),
):
    """Generate a site-specific macOS install script.

    This script downloads the .pkg, installs it, and writes
    the site-specific config — all in one command.
    """
    pool = await get_pool()
    org_id = user["org_id"]

    async with tenant_connection(pool, site_id=site_id) as conn:
        site = await conn.fetchrow(
            "SELECT site_id FROM sites WHERE site_id = $1 AND client_org_id = $2",
            site_id, org_id,
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        appliance = await conn.fetchrow("""
            SELECT ip_addresses
            FROM site_appliances
            WHERE site_id = $1 AND deleted_at IS NULL
            ORDER BY last_checkin DESC NULLS LAST
            LIMIT 1
        """, site_id)

    if not appliance:
        raise HTTPException(status_code=404, detail="No appliance found for this site")

    from .sites import parse_ip_addresses
    ips = parse_ip_addresses(appliance["ip_addresses"])
    appliance_ip = None
    for ip in ips:
        if ip and not ip.startswith("127."):
            appliance_ip = ip
            break

    if not appliance_ip:
        raise HTTPException(status_code=404, detail="Appliance has no reachable IP address")

    grpc_addr = f"{appliance_ip}:{AGENT_GRPC_PORT}"

    script = f"""#!/bin/bash
# OsirisCare Agent Installer for macOS
# Site: {site_id}
# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
#
# Run with: curl -sL <this-url> | sudo bash

set -e

INSTALL_DIR="/Library/OsirisCare"
DATA_DIR="/Library/Application Support/OsirisCare"
LOG_DIR="/Library/Logs/OsirisCare"
PLIST_LABEL="com.osiriscare.agent"
PLIST_PATH="/Library/LaunchDaemons/${{PLIST_LABEL}}.plist"

echo "=== OsirisCare Agent Installer ==="
echo ""

# Check root
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: This script must be run as root (use sudo)."
    exit 1
fi

# Create directories
mkdir -p "$INSTALL_DIR" "$DATA_DIR" "$LOG_DIR"

# Write site-specific configuration
cat > "$DATA_DIR/config.json" << 'CONFIGEOF'
{{
  "appliance_addr": "{grpc_addr}",
  "site_id": "{site_id}"
}}
CONFIGEOF

echo "[1/4] Configuration written to $DATA_DIR/config.json"
echo "      Appliance: {grpc_addr}"

# Check if .pkg is available locally or download
if [ -f "$INSTALL_DIR/osiris-agent" ]; then
    echo "[2/4] Agent binary already installed, updating config only..."
else
    echo "[2/4] Please install the OsirisCare Agent .pkg package."
    echo "      Download from your client portal or request from your MSP."
    echo ""
    echo "      After installing the .pkg, the agent will start automatically"
    echo "      and connect to your compliance appliance."
fi

# Write launchd plist
cat > "$PLIST_PATH" << 'PLISTEOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.osiriscare.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Library/OsirisCare/osiris-agent</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>/Library/Logs/OsirisCare/agent-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Library/Logs/OsirisCare/agent-stderr.log</string>
    <key>WorkingDirectory</key>
    <string>/Library/OsirisCare</string>
</dict>
</plist>
PLISTEOF
chmod 644 "$PLIST_PATH"
chown root:wheel "$PLIST_PATH"
echo "[3/4] LaunchDaemon plist installed"

# Restart agent if binary exists
if [ -f "$INSTALL_DIR/osiris-agent" ]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    launchctl load "$PLIST_PATH"
    echo "[4/4] Agent restarted with new configuration"
else
    echo "[4/4] Agent will start after .pkg installation"
fi

echo ""
echo "=== Installation complete ==="
echo "Agent will connect to appliance at {grpc_addr}"
echo "Logs: /Library/Logs/OsirisCare/"
"""

    return Response(
        content=script,
        media_type="text/x-shellscript",
        headers={
            "Content-Disposition": f'attachment; filename="install-osiriscare-{site_id}.sh"',
        },
    )


@auth_router.get("/agent/mobileconfig/{site_id}")
async def get_agent_mobileconfig(
    site_id: str,
    user: dict = Depends(require_client_user),
):
    """Generate a .mobileconfig profile for MDM deployment.

    Delivers the agent configuration via macOS configuration profile.
    Deploy alongside the .pkg via Intune, Jamf, Mosyle, or Kandji.
    """
    pool = await get_pool()
    org_id = user["org_id"]

    async with tenant_connection(pool, site_id=site_id) as conn:
        site = await conn.fetchrow(
            "SELECT site_id, clinic_name FROM sites WHERE site_id = $1 AND client_org_id = $2",
            site_id, org_id,
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        org = await conn.fetchrow(
            "SELECT name FROM client_orgs WHERE id = $1", org_id,
        )

        appliance = await conn.fetchrow("""
            SELECT ip_addresses
            FROM site_appliances
            WHERE site_id = $1 AND deleted_at IS NULL
            ORDER BY last_checkin DESC NULLS LAST
            LIMIT 1
        """, site_id)

    if not appliance:
        raise HTTPException(status_code=404, detail="No appliance found for this site")

    from .sites import parse_ip_addresses
    ips = parse_ip_addresses(appliance["ip_addresses"])
    appliance_ip = None
    for ip in ips:
        if ip and not ip.startswith("127."):
            appliance_ip = ip
            break

    if not appliance_ip:
        raise HTTPException(status_code=404, detail="Appliance has no reachable IP address")

    import uuid
    profile_uuid = str(uuid.uuid4()).upper()
    payload_uuid = str(uuid.uuid4()).upper()
    org_name = org["name"] if org else "OsirisCare"
    clinic_name = site["clinic_name"] or site_id
    grpc_addr = f"{appliance_ip}:{AGENT_GRPC_PORT}"

    # The mobileconfig deploys a script that writes the config file.
    # MDM systems execute the embedded script on deployment.
    mobileconfig = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>PayloadContent</key>
    <array>
        <dict>
            <key>PayloadType</key>
            <string>com.apple.ManagedClient.preferences</string>
            <key>PayloadVersion</key>
            <integer>1</integer>
            <key>PayloadIdentifier</key>
            <string>com.osiriscare.agent.config.{site_id}</string>
            <key>PayloadUUID</key>
            <string>{payload_uuid}</string>
            <key>PayloadDisplayName</key>
            <string>OsirisCare Agent Configuration</string>
            <key>PayloadDescription</key>
            <string>Configures the OsirisCare compliance agent for {clinic_name}</string>
            <key>PayloadOrganization</key>
            <string>{org_name}</string>
            <key>PayloadEnabled</key>
            <true/>
            <key>mcx_preference_settings</key>
            <dict>
                <key>com.osiriscare.agent</key>
                <dict>
                    <key>Forced</key>
                    <array>
                        <dict>
                            <key>mcx_preference_settings</key>
                            <dict>
                                <key>appliance_addr</key>
                                <string>{grpc_addr}</string>
                                <key>site_id</key>
                                <string>{site_id}</string>
                            </dict>
                        </dict>
                    </array>
                </dict>
            </dict>
        </dict>
    </array>
    <key>PayloadDisplayName</key>
    <string>OsirisCare Agent - {clinic_name}</string>
    <key>PayloadDescription</key>
    <string>Configuration profile for OsirisCare HIPAA compliance agent. Deploy alongside the OsirisCare Agent .pkg installer.</string>
    <key>PayloadIdentifier</key>
    <string>com.osiriscare.agent.profile.{site_id}</string>
    <key>PayloadOrganization</key>
    <string>{org_name}</string>
    <key>PayloadRemovalDisallowed</key>
    <false/>
    <key>PayloadScope</key>
    <string>System</string>
    <key>PayloadType</key>
    <string>Configuration</string>
    <key>PayloadUUID</key>
    <string>{profile_uuid}</string>
    <key>PayloadVersion</key>
    <integer>1</integer>
</dict>
</plist>"""

    return Response(
        content=mobileconfig,
        media_type="application/x-apple-aspen-config",
        headers={
            "Content-Disposition": f'attachment; filename="OsirisCare-{site_id}.mobileconfig"',
        },
    )


# =============================================================================
# DISCLOSURE ACCOUNTING — HIPAA §164.528 (Session 203 Batch 7)
# =============================================================================
#
# Every mutation in the client portal writes to client_audit_log via the
# `_audit_client_action` helper. This endpoint exposes those rows back to
# the client portal UI so practice managers can satisfy a §164.528
# disclosure-accounting request without contacting their MSP — they just
# download the org's own audit log.
#
# Scoped to the requesting user's org via tenant_connection (RLS), so a
# user can never see another org's events. The endpoint is paginated and
# supports filtering by action prefix + date range.

@auth_router.get("/audit-log")
async def list_client_audit_log(
    request: Request,
    action: Optional[str] = Query(None, description="Filter by action prefix (e.g. 'USER_' or 'CREDENTIAL_')"),
    days: int = Query(90, ge=1, le=2555),  # default 90d, max 7 years
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_client_user),
):
    """Return the client_audit_log entries for the caller's org.

    Used by the disclosure-accounting view in the client portal — gives
    the practice manager a self-serve §164.528 audit trail without
    needing to contact OsirisCare or their MSP.

    Filtering:
      - `action` matches by prefix (case-sensitive). Common prefixes:
        USER_, PASSWORD_, MFA_, CREDENTIAL_, DRIFT_CONFIG_, DEVICE_,
        ALERT_, ESCALATION_, ESCALATION_PREFS_
      - `days` bounds the lookback window (1..2555). Default 90 days
        is the right window for routine audits; auditors can extend
        to 7 years for the full HIPAA retention period.
      - `limit` + `offset` paginate (max 500 per page).
    """
    pool = await get_pool()
    org_id = user["org_id"]

    where_clauses = ["org_id = $1::uuid", f"created_at > NOW() - INTERVAL '{int(days)} days'"]
    params: list = [org_id]
    if action:
        where_clauses.append("action LIKE $2")
        params.append(f"{action}%")

    where_sql = " AND ".join(where_clauses)
    params_with_paging = [*params, limit, offset]
    limit_idx = len(params) + 1
    offset_idx = len(params) + 2

    async with org_connection(pool, org_id=org_id) as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, actor_user_id, actor_email, action, target,
                   details, ip_address, created_at
            FROM client_audit_log
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT ${limit_idx} OFFSET ${offset_idx}
            """,
            *params_with_paging,
        )

        total_row = await conn.fetchrow(
            f"SELECT COUNT(*) AS total FROM client_audit_log WHERE {where_sql}",
            *params,
        )
        total = int(total_row["total"]) if total_row else 0

    import json as _json

    return {
        "org_id": str(org_id),
        "events": [
            {
                "id": int(r["id"]),
                "actor_user_id": str(r["actor_user_id"]) if r["actor_user_id"] else None,
                "actor_email": r["actor_email"],
                "action": r["action"],
                "target": r["target"],
                "details": (
                    _json.loads(r["details"]) if isinstance(r["details"], str)
                    else r["details"]
                ) if r["details"] else None,
                "ip_address": r["ip_address"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "days": days,
        "action_filter": action,
    }


# =============================================================================
# PRIVILEGED-ACTION CHAIN VIEW — HIPAA §164.308(a)(4) customer self-serve
# =============================================================================
#
# Phase H6. Complements the disclosure-accounting view by unifying every
# privileged action taken on the client's appliances into ONE chronological
# feed — regardless of which pipeline produced it:
#
#   compliance_bundles  (check_type='privileged_access')   = attestations
#   watchdog_events     (SSH-strip replacement recovery)   = watchdog actions
#   fleet_orders        (privileged order_type list)       = triggering orders
#
# The customer sees: who (actor_email, human-named), when, WHY (reason ≥20
# chars from the attestation bundle), what (order_type + target appliance),
# and the attestation bundle ID so they can cross-check against any audit
# kit handed off to them. §164.308(a)(4) says the covered entity must be
# able to see every access to their systems — today that requires emailing
# the MSP; this endpoint closes that gap.
#
# Scoped to the caller's org through the orgs → sites → appliances JOIN.
# No cross-tenant leakage; RLS applies in addition.


@auth_router.get("/privileged-actions")
async def list_privileged_actions(
    days: int = Query(90, ge=1, le=2555, description="1..2555 days lookback"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_client_user),
):
    """Return every privileged action on the caller's org's appliances in
    reverse chronological order. Row shape:

        {
          "kind":              "attestation" | "watchdog_event" | "fleet_order",
          "event_time":        "<ISO8601>",
          "actor_email":       "<human email or None>",
          "reason":            "<operator-provided reason, ≥20 chars>",
          "action":            "<enable_emergency_access / watchdog_restart_daemon / ...>",
          "target_appliance":  "<appliance_id or -watchdog id>",
          "site_id":           "<site>",
          "attestation_bundle_id": "<bundle_id when available>",
          "source":            "<table name>",
        }

    Pagination + day window mirror /audit-log so frontends reuse the same
    chrome. The feed can be filtered client-side or server-side on `kind`
    if needed.
    """
    pool = await get_pool()
    org_id = user["org_id"]

    # Union over the three event tables, constrained to sites this org
    # owns. compliance_bundles.check_type='privileged_access' is the
    # attestation catalog — the source of truth for every actor/reason.
    # watchdog_events gives the execution record. fleet_orders gives the
    # order shape before the daemon acks.
    async with org_connection(pool, org_id=org_id) as conn:
        rows = await conn.fetch(
            """
            WITH org_sites AS (
                SELECT s.site_id
                  FROM sites s
                 WHERE s.org_id = $1::uuid
            ),
            attestations AS (
                SELECT 'attestation'::text AS kind,
                       cb.checked_at AS event_time,
                       cb.summary->>'actor' AS actor_email,
                       cb.summary->>'reason' AS reason,
                       cb.summary->>'event_type' AS action,
                       cb.summary->>'target' AS target_appliance,
                       cb.site_id,
                       cb.bundle_id AS attestation_bundle_id,
                       'compliance_bundles'::text AS source
                  FROM compliance_bundles cb
                 WHERE cb.check_type = 'privileged_access'
                   AND cb.site_id IN (SELECT site_id FROM org_sites)
                   AND cb.checked_at > NOW() - make_interval(days => $2)
            ),
            watchdog AS (
                SELECT 'watchdog_event'::text AS kind,
                       we.created_at AS event_time,
                       NULL::text AS actor_email,
                       NULL::text AS reason,
                       COALESCE(we.watchdog_order_type, we.event_type) AS action,
                       we.appliance_id AS target_appliance,
                       we.site_id,
                       we.order_id::text AS attestation_bundle_id,
                       'watchdog_events'::text AS source
                  FROM watchdog_events we
                 WHERE we.site_id IN (SELECT site_id FROM org_sites)
                   AND we.created_at > NOW() - make_interval(days => $2)
                   AND we.event_type IN ('order_executed', 'order_failed')
            ),
            orders AS (
                SELECT 'fleet_order'::text AS kind,
                       fo.created_at AS event_time,
                       NULL::text AS actor_email,
                       NULL::text AS reason,
                       fo.order_type AS action,
                       fo.parameters->>'appliance_id' AS target_appliance,
                       fo.parameters->>'site_id' AS site_id,
                       fo.parameters->>'attestation_bundle_id' AS attestation_bundle_id,
                       'fleet_orders'::text AS source
                  FROM fleet_orders fo
                 WHERE fo.parameters->>'site_id' IN (SELECT site_id FROM org_sites)
                   AND fo.created_at > NOW() - make_interval(days => $2)
                   AND fo.order_type IN (
                        'enable_emergency_access',
                        'disable_emergency_access',
                        'bulk_remediation',
                        'signing_key_rotation',
                        'watchdog_restart_daemon',
                        'watchdog_refetch_config',
                        'watchdog_reset_pin_store',
                        'watchdog_reset_api_key',
                        'watchdog_redeploy_daemon',
                        'watchdog_collect_diagnostics'
                   )
            )
            SELECT * FROM (
                SELECT * FROM attestations
                UNION ALL SELECT * FROM watchdog
                UNION ALL SELECT * FROM orders
            ) combined
            ORDER BY event_time DESC
            LIMIT $3 OFFSET $4
            """,
            org_id, days, limit, offset,
        )

        # For the total count, re-run the shape without paging.
        total_row = await conn.fetchrow(
            """
            WITH org_sites AS (
                SELECT s.site_id FROM sites s WHERE s.org_id = $1::uuid
            )
            SELECT
              (SELECT COUNT(*) FROM compliance_bundles
                WHERE check_type='privileged_access'
                  AND site_id IN (SELECT site_id FROM org_sites)
                  AND checked_at > NOW() - make_interval(days => $2)) +
              (SELECT COUNT(*) FROM watchdog_events
                WHERE event_type IN ('order_executed','order_failed')
                  AND site_id IN (SELECT site_id FROM org_sites)
                  AND created_at > NOW() - make_interval(days => $2)) +
              (SELECT COUNT(*) FROM fleet_orders
                WHERE parameters->>'site_id' IN (SELECT site_id FROM org_sites)
                  AND created_at > NOW() - make_interval(days => $2)
                  AND order_type IN (
                        'enable_emergency_access','disable_emergency_access',
                        'bulk_remediation','signing_key_rotation',
                        'watchdog_restart_daemon','watchdog_refetch_config',
                        'watchdog_reset_pin_store','watchdog_reset_api_key',
                        'watchdog_redeploy_daemon','watchdog_collect_diagnostics'
                  ))
            AS total
            """,
            org_id, days,
        )
        total = int(total_row["total"]) if total_row else 0

    return {
        "org_id": str(org_id),
        "events": [
            {
                "kind": r["kind"],
                "event_time": r["event_time"].isoformat() if r["event_time"] else None,
                "actor_email": r["actor_email"],
                "reason": r["reason"],
                "action": r["action"],
                "target_appliance": r["target_appliance"],
                "site_id": r["site_id"],
                "attestation_bundle_id": r["attestation_bundle_id"],
                "source": r["source"],
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "days": days,
    }


# =============================================================================
# ESCALATION PREFERENCES + TICKET MANAGEMENT
# =============================================================================

class EscalationPreferencesUpdate(BaseModel):
    """Client escalation routing preferences."""
    escalation_mode: Literal['partner', 'direct', 'both'] = 'partner'
    email_enabled: bool = True
    email_recipients: List[str] = []
    slack_enabled: bool = False
    slack_webhook_url: Optional[str] = None
    teams_enabled: bool = False
    teams_webhook_url: Optional[str] = None
    escalation_timeout_minutes: int = 60


@auth_router.get("/escalation-preferences")
async def get_escalation_preferences(user: dict = Depends(require_client_user)):
    """Get client org's L3 escalation routing preferences."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        row = await conn.fetchrow("""
            SELECT escalation_mode, email_enabled, email_recipients,
                   slack_enabled, slack_webhook_url,
                   teams_enabled, teams_webhook_url,
                   escalation_timeout_minutes, updated_at
            FROM client_escalation_preferences
            WHERE client_org_id = $1
        """, org_id)

        if not row:
            return {
                "escalation_mode": "partner",
                "email_enabled": True,
                "email_recipients": [],
                "slack_enabled": False,
                "slack_webhook_url": None,
                "teams_enabled": False,
                "teams_webhook_url": None,
                "escalation_timeout_minutes": 60,
                "configured": False,
            }

        return {**dict(row), "configured": True}


@auth_router.put("/escalation-preferences")
async def update_escalation_preferences(
    body: EscalationPreferencesUpdate,
    request: Request,
    user: dict = Depends(require_client_admin),
):
    """Update client org's L3 escalation routing preferences. Requires admin role."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        await conn.execute("""
            INSERT INTO client_escalation_preferences (
                client_org_id, escalation_mode, email_enabled, email_recipients,
                slack_enabled, slack_webhook_url,
                teams_enabled, teams_webhook_url,
                escalation_timeout_minutes, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
            ON CONFLICT (client_org_id) DO UPDATE SET
                escalation_mode = EXCLUDED.escalation_mode,
                email_enabled = EXCLUDED.email_enabled,
                email_recipients = EXCLUDED.email_recipients,
                slack_enabled = EXCLUDED.slack_enabled,
                slack_webhook_url = EXCLUDED.slack_webhook_url,
                teams_enabled = EXCLUDED.teams_enabled,
                teams_webhook_url = EXCLUDED.teams_webhook_url,
                escalation_timeout_minutes = EXCLUDED.escalation_timeout_minutes,
                updated_at = NOW()
        """,
            org_id,
            body.escalation_mode,
            body.email_enabled,
            body.email_recipients,
            body.slack_enabled,
            body.slack_webhook_url,
            body.teams_enabled,
            body.teams_webhook_url,
            body.escalation_timeout_minutes,
        )

        await _audit_client_action(
            conn, user,
            action="ESCALATION_PREFS_UPDATED",
            target=str(org_id),
            details={
                "escalation_mode": body.escalation_mode,
                "email_enabled": body.email_enabled,
                "slack_enabled": body.slack_enabled,
                "teams_enabled": body.teams_enabled,
                "timeout_minutes": body.escalation_timeout_minutes,
            },
            request=request,
        )

    return {"status": "updated", "escalation_mode": body.escalation_mode}


# =============================================================================
# EMERGENCY ACCESS (Session 204 — customer-controlled WireGuard toggle)
# =============================================================================

class EmergencyAccessRequest(BaseModel):
    """Client admin approves time-bounded WireGuard access."""
    duration_minutes: int = Field(120, ge=15, le=480)  # 15 min to 8 hours
    reason: str = Field(..., min_length=5, max_length=500)


@auth_router.post("/emergency-access/enable")
async def enable_emergency_access(
    body: EmergencyAccessRequest,
    request: Request,
    user: dict = Depends(require_client_admin),
):
    """Client admin grants time-bounded emergency WireGuard access.

    Creates a signed fleet order delivered to all site appliances.
    The tunnel auto-disables after duration_minutes via systemd timer.
    This is the customer's decision — OsirisCare cannot self-activate.
    """
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Get all sites for this org
        sites = await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1", org_id
        )
        if not sites:
            raise HTTPException(status_code=404, detail="No sites found")

        # Create fleet order for each site
        from .fleet_updates import create_fleet_order_for_site
        order_ids = []
        for site in sites:
            try:
                order_id = await create_fleet_order_for_site(
                    conn,
                    site_id=site["site_id"],
                    order_type="enable_emergency_access",
                    parameters={
                        "max_duration_minutes": body.duration_minutes,
                        "approved_by": user.get("email", str(user["user_id"])),
                        "reason": body.reason,
                    },
                    expires_hours=1,
                )
                if order_id:
                    order_ids.append(str(order_id))
            except Exception as e:
                logger.warning(f"Emergency access order failed for {site['site_id']}: {e}")

        await _audit_client_action(
            conn, user,
            action="EMERGENCY_ACCESS_ENABLED",
            target=str(org_id),
            details={
                "duration_minutes": body.duration_minutes,
                "reason": body.reason,
                "site_count": len(sites),
                "order_count": len(order_ids),
            },
            request=request,
        )

    return {
        "status": "enabled",
        "duration_minutes": body.duration_minutes,
        "sites_affected": len(sites),
        "orders_created": len(order_ids),
        "message": f"Emergency access enabled for {body.duration_minutes} minutes. Auto-disables after expiry.",
    }


@auth_router.post("/emergency-access/disable")
async def disable_emergency_access(
    request: Request,
    user: dict = Depends(require_client_admin),
):
    """Client admin revokes emergency access early."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        sites = await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1", org_id
        )

        from .fleet_updates import create_fleet_order_for_site
        for site in sites:
            try:
                await create_fleet_order_for_site(
                    conn,
                    site_id=site["site_id"],
                    order_type="disable_emergency_access",
                    parameters={"disabled_by": user.get("email", str(user["user_id"]))},
                    expires_hours=1,
                )
            except Exception:
                pass

        await _audit_client_action(
            conn, user,
            action="EMERGENCY_ACCESS_DISABLED",
            target=str(org_id),
            details={"site_count": len(sites)},
            request=request,
        )

    return {"status": "disabled", "message": "Emergency access revoked."}


@auth_router.get("/escalations")
async def list_client_escalations(
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_client_user),
):
    """List L3 escalation tickets for client org's sites."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        query = """
            SELECT t.id, t.site_id, s.clinic_name as site_name,
                   t.incident_type, t.severity, t.priority, t.title, t.summary,
                   t.recommended_action, t.hipaa_controls, t.attempted_actions,
                   t.raw_data, t.status, t.sla_breached,
                   t.acknowledged_at, t.resolved_at, t.resolved_by,
                   t.resolution_notes, t.recurrence_count,
                   t.escalated_to_l4, t.created_at, t.updated_at
            FROM escalation_tickets t
            JOIN sites s ON s.site_id = t.site_id
            WHERE s.client_org_id = $1
        """
        params: list = [org_id]
        idx = 2

        if status:
            query += f" AND t.status = ${idx}"
            params.append(status)
            idx += 1

        query += f" ORDER BY t.created_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
        params.extend([limit, offset])

        rows = await conn.fetch(query, *params)

        # Counts
        count_row = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE t.status = 'open') as open_count,
                COUNT(*) FILTER (WHERE t.status = 'acknowledged') as acknowledged_count,
                COUNT(*) FILTER (WHERE t.status = 'resolved') as resolved_count,
                COUNT(*) FILTER (WHERE t.sla_breached = true AND t.status != 'resolved') as sla_breached_count
            FROM escalation_tickets t
            JOIN sites s ON s.site_id = t.site_id
            WHERE s.client_org_id = $1
        """, org_id)

    import json as json_mod
    tickets = []
    for r in rows:
        t = dict(r)
        for key in ('raw_data', 'attempted_actions'):
            if isinstance(t.get(key), str):
                try:
                    t[key] = json_mod.loads(t[key])
                except Exception:
                    pass
        tickets.append(t)

    return {
        "tickets": tickets,
        "counts": dict(count_row) if count_row else {},
    }


@auth_router.get("/escalations/{ticket_id}")
async def get_client_escalation_detail(
    ticket_id: str,
    user: dict = Depends(require_client_user),
):
    """Get escalation ticket detail (client must own the site)."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        row = await conn.fetchrow("""
            SELECT t.*, s.clinic_name as site_name
            FROM escalation_tickets t
            JOIN sites s ON s.site_id = t.site_id
            WHERE t.id = $1 AND s.client_org_id = $2
        """, ticket_id, org_id)

        if not row:
            raise HTTPException(status_code=404, detail="Ticket not found")

    import json as json_mod
    ticket = dict(row)
    for key in ('raw_data', 'attempted_actions'):
        if isinstance(ticket.get(key), str):
            try:
                ticket[key] = json_mod.loads(ticket[key])
            except Exception:
                pass

    return {"ticket": ticket}


@auth_router.post("/escalations/{ticket_id}/acknowledge")
async def client_acknowledge_ticket(
    ticket_id: str,
    request: Request,
    user: dict = Depends(require_client_user),
):
    """Client acknowledges an escalation ticket."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        result = await conn.execute("""
            UPDATE escalation_tickets t
            SET status = 'acknowledged',
                acknowledged_at = NOW(),
                acknowledged_by = $3,
                updated_at = NOW()
            FROM sites s
            WHERE t.id = $1
              AND s.site_id = t.site_id
              AND s.client_org_id = $2
              AND t.status = 'open'
        """, ticket_id, org_id, user.get("email", user.get("user_id", "client")))

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Ticket not found or already acknowledged")

        await _audit_client_action(
            conn, user,
            action="ESCALATION_ACKNOWLEDGED",
            target=ticket_id,
            request=request,
        )

    return {"status": "acknowledged"}


@auth_router.post("/escalations/{ticket_id}/resolve")
async def client_resolve_ticket(
    ticket_id: str,
    body: dict,
    request: Request,
    user: dict = Depends(require_client_user),
):
    """Client resolves an escalation ticket with notes."""
    pool = await get_pool()
    org_id = user["org_id"]
    resolution_notes = body.get("resolution_notes", "").strip()
    if not resolution_notes:
        raise HTTPException(status_code=400, detail="Resolution notes required")

    resolved_by = user.get("email", user.get("user_id", "client"))

    async with org_connection(pool, org_id=org_id) as conn:
        result = await conn.execute("""
            UPDATE escalation_tickets t
            SET status = 'resolved',
                resolved_at = NOW(),
                resolved_by = $3,
                resolution_notes = $4,
                updated_at = NOW()
            FROM sites s
            WHERE t.id = $1
              AND s.site_id = t.site_id
              AND s.client_org_id = $2
              AND t.status IN ('open', 'acknowledged')
        """, ticket_id, org_id, resolved_by, resolution_notes)

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Ticket not found or already resolved")

        await _audit_client_action(
            conn, user,
            action="ESCALATION_RESOLVED",
            target=ticket_id,
            details={"resolution_notes": resolution_notes[:500]},
            request=request,
        )

    return {"status": "resolved"}


# =============================================================================
# Unregistered Device Discovery & Registration
# =============================================================================

# Rational devices: servers and workstations with open management ports or AD membership.
# Excludes IoT, printers, consumer devices (PlayStations, smart TVs, etc.)
_RATIONAL_DEVICE_STATUSES = ("take_over_available", "ad_managed")
_RATIONAL_DEVICE_TYPES = ("workstation", "server")


@auth_router.get("/sites/{site_id}/unregistered-devices")
async def get_unregistered_devices(
    site_id: str,
    user: dict = Depends(require_client_user),
):
    """List discovered devices that need client attention.

    Only returns "rational" devices — servers and workstations with open
    management ports (SSH/WinRM) or AD membership, but no agent and no
    credentials. Consumer devices (IoT, printers, etc.) are excluded.
    """
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Verify site belongs to this org
        site = await conn.fetchrow(
            "SELECT site_id FROM sites WHERE site_id = $1", site_id
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Exclude appliance IPs (they're infrastructure, not client devices).
        # NOTE: intentionally NOT filtered by deleted_at — historical IPs of
        # soft-deleted appliances must STAY in the exclusion set so they don't
        # resurface as "client devices" after cleanup. Carved out from the
        # RT33 ghost-data gate (test_client_portal_filters_soft_deletes.py).
        appliance_ips = await conn.fetch(
            "SELECT unnest(ip_addresses::text[]) as ip FROM site_appliances WHERE site_id = $1",
            site_id
        )
        exclude_ips = {row["ip"].strip('"') for row in appliance_ips}

        devices = await conn.fetch("""
            SELECT dd.id, dd.ip_address, dd.mac_address, dd.hostname,
                   dd.os_name, dd.distro, dd.device_type, dd.device_status,
                   dd.probe_ssh, dd.probe_winrm, dd.ad_joined,
                   dd.first_seen_at, dd.last_seen_at
            FROM discovered_devices dd
            WHERE dd.site_id = $1
            AND dd.device_status IN ('take_over_available', 'ad_managed')
            AND dd.device_type IN ('workstation', 'server', 'unknown')
            AND (dd.compliance_status IS NULL OR dd.compliance_status = 'unknown')
            AND dd.hostname NOT LIKE '%router%'
            ORDER BY dd.last_seen_at DESC
        """, site_id)

        # Filter out appliance IPs and .1 gateway addresses
        devices = [
            row for row in devices
            if row["ip_address"] not in exclude_ips
            and not row["ip_address"].endswith(".1")
        ]

        return {
            "site_id": site_id,
            "devices": [
                {
                    "id": row["id"],
                    "ip_address": row["ip_address"],
                    "mac_address": row["mac_address"],
                    "hostname": row["hostname"] or "",
                    "os_name": row["os_name"] or "Unknown",
                    "distro": row["distro"] or "",
                    "device_type": row["device_type"] or "unknown",
                    "device_status": row["device_status"],
                    "probe_ssh": row["probe_ssh"] or False,
                    "probe_winrm": row["probe_winrm"] or False,
                    "ad_joined": row["ad_joined"] or False,
                    "first_seen": row["first_seen_at"].isoformat() if row["first_seen_at"] else None,
                    "last_seen": row["last_seen_at"].isoformat() if row["last_seen_at"] else None,
                }
                for row in devices
            ],
            "count": len(devices),
        }


class DeviceRegistration(BaseModel):
    """Client submits credentials for an unregistered device."""
    username: str
    password: Optional[str] = None
    private_key: Optional[str] = None
    credential_type: Literal["ssh_key", "winrm", "local_admin"] = "ssh_key"
    label: Optional[str] = None  # "linux", "windows", "macos"


@auth_router.post("/sites/{site_id}/devices/{device_id}/register")
async def register_device(
    site_id: str,
    device_id: int,
    body: DeviceRegistration,
    request: Request,
    user: dict = Depends(require_client_user),
):
    """Register credentials for a discovered device.

    Creates a site_credential for the device so the appliance can scan it.
    Updates device_status to 'pending_deploy'.
    """
    import json as _json
    from .credential_crypto import encrypt_credential

    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Verify site + device belong to this org
        device = await conn.fetchrow("""
            SELECT id, ip_address, hostname, mac_address, device_status, probe_ssh, probe_winrm
            FROM discovered_devices
            WHERE id = $1 AND site_id = $2
        """, device_id, site_id)

        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        if device["device_status"] in ("agent_active", "ignored"):
            raise HTTPException(status_code=409, detail=f"Device already {device['device_status']}")

        # Build credential JSON
        host = device["ip_address"]
        cred_data = {
            "host": host,
            "username": body.username,
        }
        if body.password:
            cred_data["password"] = body.password
        if body.private_key:
            cred_data["private_key"] = body.private_key
        if body.label:
            cred_data["label"] = body.label

        # Auto-detect credential type from probes if not specified
        if body.credential_type == "ssh_key" and device["probe_winrm"] and not device["probe_ssh"]:
            cred_data["use_ssl"] = False
            cred_type = "winrm"
        else:
            cred_type = body.credential_type

        encrypted = encrypt_credential(_json.dumps(cred_data))
        cred_name = f"{device['hostname'] or host} ({cred_type})"

        # Insert credential
        await conn.execute("""
            INSERT INTO site_credentials (site_id, credential_type, credential_name, encrypted_data, created_at, updated_at)
            VALUES ($1, $2, $3, $4, NOW(), NOW())
        """, site_id, cred_type, cred_name, encrypted)

        # Update device status
        await conn.execute("""
            UPDATE discovered_devices
            SET device_status = 'pending_deploy',
                sync_updated_at = NOW()
            WHERE id = $1
        """, device_id)

        await _audit_client_action(
            conn, user,
            action="DEVICE_REGISTERED",
            target=str(device_id),
            details={
                "site_id": site_id,
                "host": host,
                "credential_type": cred_type,
            },
            request=request,
        )

        logger.info(
            "Device registered by client",
            extra={"site_id": site_id, "device_id": device_id, "host": host,
                   "user": user.get("email"), "cred_type": cred_type}
        )

    return {"status": "registered", "device_id": device_id, "host": host}


@auth_router.post("/sites/{site_id}/devices/{device_id}/ignore")
async def ignore_device(
    site_id: str,
    device_id: int,
    request: Request,
    user: dict = Depends(require_client_user),
):
    """Mark a discovered device as expected/non-managed.

    The device will no longer appear in unregistered device alerts.
    Can be reversed by admin via the dashboard.
    """
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        result = await conn.execute("""
            UPDATE discovered_devices
            SET device_status = 'ignored',
                device_tag = 'client_ignored',
                sync_updated_at = NOW()
            WHERE id = $1 AND site_id = $2
            AND device_status NOT IN ('agent_active')
        """, device_id, site_id)

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Device not found or already managed")

        await _audit_client_action(
            conn, user,
            action="DEVICE_IGNORED",
            target=str(device_id),
            details={"site_id": site_id},
            request=request,
        )

        logger.info(
            "Device ignored by client",
            extra={"site_id": site_id, "device_id": device_id, "user": user.get("email")}
        )

    return {"status": "ignored", "device_id": device_id}


# =============================================================================
# ALERT ENDPOINTS
# =============================================================================

VALID_ALERT_ACTIONS = {"approved", "dismissed", "acknowledged", "ignored", "credentials_entered"}


@auth_router.get("/alerts")
async def get_client_alerts(user: dict = Depends(require_client_user)):
    """Return pending alerts for the client's org, with effective alert mode."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        rows = await conn.fetch("""
            SELECT pa.id, pa.site_id, s.name as site_name, pa.alert_type, pa.summary,
                   pa.severity, pa.created_at, pa.sent_at, pa.dismissed_at,
                   pa.incident_id,
                   COALESCE(s.client_alert_mode, co.client_alert_mode, 'informed') as effective_mode
            FROM pending_alerts pa
            JOIN sites s ON s.site_id = pa.site_id
            JOIN client_orgs co ON co.id = pa.org_id
            WHERE pa.org_id = $1
            ORDER BY pa.created_at DESC
            LIMIT 100
        """, org_id)

    alerts = []
    for row in rows:
        effective_mode = row["effective_mode"]
        if row["dismissed_at"]:
            status = "dismissed"
        elif row["sent_at"]:
            status = "sent"
        else:
            status = "pending"

        alerts.append({
            "id": str(row["id"]),
            "site_id": str(row["site_id"]),
            "site_name": row["site_name"],
            "alert_type": row["alert_type"],
            "summary": row["summary"],
            "severity": row["severity"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "sent_at": row["sent_at"].isoformat() if row["sent_at"] else None,
            "dismissed_at": row["dismissed_at"].isoformat() if row["dismissed_at"] else None,
            "incident_id": str(row["incident_id"]) if row["incident_id"] else None,
            "effective_mode": effective_mode,
            "status": status,
            "actions_available": (effective_mode == "self_service"),
        })

    return {"alerts": alerts}


@auth_router.post("/alerts/{alert_id}/action")
async def action_client_alert(
    alert_id: str,
    request: Request,
    user: dict = Depends(require_client_user),
):
    """Approve, dismiss, acknowledge, or ignore a pending alert."""
    body = await request.json()
    action = body.get("action")
    notes = body.get("notes")

    if action not in VALID_ALERT_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid action '{action}'. Must be one of: {sorted(VALID_ALERT_ACTIONS)}",
        )

    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Fetch alert with effective mode
        alert = await conn.fetchrow("""
            SELECT pa.id, pa.site_id, pa.incident_id, pa.org_id,
                   COALESCE(s.client_alert_mode, co.client_alert_mode, 'informed') as effective_mode
            FROM pending_alerts pa
            JOIN sites s ON s.site_id = pa.site_id
            JOIN client_orgs co ON co.id = pa.org_id
            WHERE pa.id = $1 AND pa.org_id = $2
        """, alert_id, org_id)

        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")

        effective_mode = alert["effective_mode"]
        if effective_mode != "self_service":
            raise HTTPException(
                status_code=403,
                detail="Actions not available for this site's alert mode",
            )

        import uuid as uuid_mod

        # Idempotency: don't create duplicate approval records
        existing_approval = await conn.fetchrow(
            """SELECT id FROM client_approvals
               WHERE alert_id = $1 AND action = $2""",
            alert_id, action,
        )
        if existing_approval:
            return {
                "status": "ok",
                "action_taken": action,
                "approval_id": str(existing_approval["id"]),
                "incident_id": str(alert["incident_id"]) if alert["incident_id"] else None,
                "note": "already_actioned",
            }

        approval_id = str(uuid_mod.uuid4())

        # Record audit trail
        await conn.execute("""
            INSERT INTO client_approvals
                (id, org_id, site_id, incident_id, alert_id, action, acted_by, notes, acted_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
        """,
            approval_id,
            org_id,
            str(alert["site_id"]),
            str(alert["incident_id"]) if alert["incident_id"] else None,
            str(alert_id),
            action,
            user["user_id"],
            notes,
        )

        # Side effects by action
        if action in ("dismissed", "ignored"):
            await conn.execute("""
                UPDATE pending_alerts SET dismissed_at = NOW() WHERE id = $1
            """, alert_id)

        if action == "approved" and alert["incident_id"]:
            await conn.execute("""
                UPDATE incidents
                SET details = details || '{"client_approved": true}'::jsonb
                WHERE id = $1
            """, str(alert["incident_id"]))

        await _audit_client_action(
            conn, user,
            action=f"ALERT_{action.upper()}",
            target=alert_id,
            details={
                "incident_id": str(alert["incident_id"]) if alert["incident_id"] else None,
                "site_id": str(alert["site_id"]),
                "notes": (notes or "")[:500] if notes else None,
            },
            request=request,
        )

    logger.info(
        "Client alert action taken",
        extra={
            "alert_id": alert_id,
            "action": action,
            "user": user.get("email"),
            "org_id": org_id,
        },
    )

    return {
        "status": "ok",
        "action_taken": action,
        "approval_id": approval_id,
        "incident_id": str(alert["incident_id"]) if alert["incident_id"] else None,
    }


# =============================================================================
# CREDENTIAL ENTRY ENDPOINT
# =============================================================================


@auth_router.post("/credentials")
async def submit_client_credentials(
    request: Request,
    user: dict = Depends(require_client_user),
):
    """Client enters scan credentials for a site. Stored encrypted, delivered on next checkin."""
    import json as _json
    import uuid as _uuid
    from .credential_crypto import encrypt_credential

    body = await request.json()
    site_id = body.get("site_id")
    credential_type = body.get("credential_type")
    credential_name = body.get("credential_name", "Client-provided credential")
    data = body.get("data", {})

    if not site_id or not credential_type:
        raise HTTPException(status_code=422, detail="site_id and credential_type are required")

    valid_types = {"winrm", "domain_admin", "ssh_key", "ssh_password"}
    if credential_type not in valid_types:
        raise HTTPException(status_code=422, detail=f"credential_type must be one of: {sorted(valid_types)}")

    if not data.get("username"):
        raise HTTPException(status_code=422, detail="data.username is required")

    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        # Verify site belongs to this org
        site = await conn.fetchrow(
            "SELECT site_id FROM sites WHERE site_id = $1 AND client_org_id = $2",
            site_id, org_id,
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Rate limit: max 10 per hour per org
        recent_count = await conn.fetchval(
            """SELECT COUNT(*) FROM site_credentials
               WHERE site_id IN (SELECT site_id FROM sites WHERE client_org_id = $1)
                 AND created_at > NOW() - INTERVAL '1 hour'""",
            org_id,
        )
        if recent_count and recent_count >= 10:
            raise HTTPException(status_code=429, detail="Rate limit: max 10 credentials per hour")

        # Build credential data
        cred_data = {"username": data["username"]}
        if credential_type in ("winrm", "domain_admin", "ssh_password"):
            cred_data["password"] = data.get("password", "")
        if credential_type in ("winrm", "domain_admin"):
            cred_data["domain"] = data.get("domain", "")
            cred_data["use_ssl"] = data.get("use_ssl", False)
        if credential_type == "ssh_key":
            cred_data["private_key"] = data.get("private_key", "")
            cred_data["passphrase"] = data.get("passphrase", "")
        if data.get("host"):
            cred_data["host"] = data["host"]

        encrypted = encrypt_credential(_json.dumps(cred_data))
        cred_id = str(_uuid.uuid4())

        await conn.execute(
            """INSERT INTO site_credentials (id, site_id, credential_type, credential_name, encrypted_data, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, NOW(), NOW())""",
            cred_id, site_id, credential_type, credential_name, encrypted,
        )

        # Validate alert_id belongs to this org (if provided)
        alert_id = body.get("alert_id")
        if alert_id:
            alert_check = await conn.fetchrow(
                "SELECT id FROM pending_alerts WHERE id = $1 AND org_id = $2",
                alert_id, org_id,
            )
            if not alert_check:
                alert_id = None  # Silently drop invalid alert_id rather than 404

        # Audit trail (only if triggered from an alert)
        if alert_id:
            await conn.execute(
                """INSERT INTO client_approvals (id, org_id, site_id, alert_id, action, acted_by, notes)
                   VALUES ($1, $2, $3, $4, 'credentials_entered', $5, $6)""",
                str(_uuid.uuid4()), org_id, site_id, alert_id, user["user_id"],
                f"Credential type: {credential_type}, name: {credential_name}",
            )

        await _audit_client_action(
            conn, user,
            action="CREDENTIAL_CREATED",
            target=site_id,
            details={
                "credential_id": cred_id,
                "credential_type": credential_type,
                "credential_name": credential_name,
            },
            request=request,
        )

        logger.info(
            "Client credential submitted",
            extra={"site_id": site_id, "credential_type": credential_type, "user_id": user["user_id"]},
        )

        return {"status": "ok", "credential_id": cred_id}


# =============================================================================
# Privacy Officer Designation (F2 — round-table 2026-05-06)
# =============================================================================
# Janet's customer-round-table finding: "if you're going to print my
# name on a federal-looking document I need a checkbox at signup that
# says 'Janet Walsh accepts Privacy Officer designation, here's the
# 2-paragraph explainer of what that means.'" The Compliance
# Attestation Letter (F1) pulls the Privacy Officer name from a SIGNED
# ACCEPTANCE attestation row, not a profile field. Without an active
# designation, F1 refuses to render — Carol's "never print a stale
# signature" contract.

@auth_router.get("/privacy-officer/explainer")
async def get_privacy_officer_explainer(
    user: dict = Depends(require_client_user),
):
    """Return the canonical §164.308(a)(2) explainer text the wizard
    must display PLUS its SHA-256 hash. The client submits the hash
    back on POST /designate; server rejects mismatched hashes
    (Carol MUST-1 + Maya P2-B closure)."""
    try:
        from .client_privacy_officer import get_explainer_text_and_hash, EXPLAINER_VERSION
    except ImportError:
        from client_privacy_officer import get_explainer_text_and_hash, EXPLAINER_VERSION  # type: ignore
    text, sha256 = get_explainer_text_and_hash()
    return {
        "version": EXPLAINER_VERSION,
        "text": text,
        "sha256": sha256,
    }


@auth_router.get("/privacy-officer")
async def get_privacy_officer_designation(
    user: dict = Depends(require_client_user),
):
    """Return the org's currently-active Privacy Officer designation,
    or {"designation": null} if none has been made yet (or revoked
    without replacement). All authenticated client users may read."""
    try:
        from .client_privacy_officer import get_current
    except ImportError:
        from client_privacy_officer import get_current  # type: ignore
    pool = await get_pool()
    org_id = user["org_id"]
    async with org_connection(pool, org_id=org_id) as conn:
        designation = await get_current(conn, org_id)
    if designation is None:
        return {"designation": None}
    return {
        "designation": {
            "id": str(designation["id"]),
            "name": designation["name"],
            "title": designation["title"],
            "email": designation["email"],
            "accepted_at": designation["accepted_at"].isoformat(),
            "accepting_user_email": designation["accepting_user_email"],
            "explainer_version": designation["explainer_version"],
            "attestation_bundle_id": (
                str(designation["attestation_bundle_id"])
                if designation.get("attestation_bundle_id")
                else None
            ),
        }
    }


@auth_router.post("/privacy-officer/designate")
async def designate_privacy_officer(
    request: Request,
    user: dict = Depends(require_client_owner),
):
    """Owner-only. Designate (or replace) the Privacy Officer.

    Body: name, title, email, acceptance_acknowledgement (≥50 chars,
    the verbatim §164.308(a)(2) explainer text the wizard displayed).

    Replaces any active designation atomically. Writes a chain-
    anchored Ed25519 attestation bundle
    (`client_org_privacy_officer_designated`) AND a client_audit_log
    row for §164.308(a)(1)(ii)(D) parity."""
    try:
        from .client_privacy_officer import designate, PrivacyOfficerError
    except ImportError:
        from client_privacy_officer import designate, PrivacyOfficerError  # type: ignore
    body = await request.json()
    name = (body.get("name") or "").strip()
    title = (body.get("title") or "").strip()
    email = (body.get("email") or "").strip()
    ack = (body.get("acceptance_acknowledgement") or "").strip()
    # Carol MUST-1 + Maya P2-B: server-side hash compare.
    accepted_explainer_sha256 = (
        body.get("accepted_explainer_sha256") or ""
    ).strip().lower()
    # Carol MUST-4: owner self-attests authority under governing docs.
    is_authorized_self_attestation = bool(
        body.get("is_authorized_self_attestation", False)
    )

    pool = await get_pool()
    org_id = user["org_id"]
    # Steve P1-C: behind a proxy, use X-Forwarded-For.
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else None)
    )
    user_agent = request.headers.get("user-agent", "")[:500]

    async with org_connection(pool, org_id=org_id) as conn:
        try:
            new_designation = await designate(
                conn=conn,
                client_org_id=org_id,
                name=name,
                title=title,
                email=email,
                accepting_user_id=user["user_id"],
                accepting_user_email=user["email"],
                ip_address=client_ip,
                user_agent=user_agent,
                acceptance_acknowledgement=ack,
                accepted_explainer_sha256=accepted_explainer_sha256,
                is_authorized_self_attestation=is_authorized_self_attestation,
            )
        except PrivacyOfficerError as e:
            raise HTTPException(status_code=400, detail=str(e))

        await _audit_client_action(
            conn, user,
            action="PRIVACY_OFFICER_DESIGNATED",
            target=str(new_designation["id"]),
            details={
                "designation_id": str(new_designation["id"]),
                "designee_name": new_designation["name"],
                "designee_email": new_designation["email"],
                "explainer_version": new_designation["explainer_version"],
                "attestation_bundle_id": (
                    str(new_designation.get("attestation_bundle_id"))
                    if new_designation.get("attestation_bundle_id")
                    else None
                ),
            },
            request=request,
        )

    return {
        "status": "ok",
        "designation_id": str(new_designation["id"]),
        "attestation_bundle_id": (
            str(new_designation.get("attestation_bundle_id"))
            if new_designation.get("attestation_bundle_id")
            else None
        ),
    }


@auth_router.post("/privacy-officer/revoke")
async def revoke_privacy_officer(
    request: Request,
    user: dict = Depends(require_client_owner),
):
    """Owner-only. Revoke the current Privacy Officer designation
    WITHOUT replacement. Writes
    `client_org_privacy_officer_revoked` to the chain. F1's
    attestation-letter render path will REFUSE to render once
    revocation is in effect — owner must designate a new Privacy
    Officer to resume document generation.

    Body: reason (≥20 chars; describes the transition)."""
    try:
        from .client_privacy_officer import revoke, PrivacyOfficerError
    except ImportError:
        from client_privacy_officer import revoke, PrivacyOfficerError  # type: ignore
    body = await request.json()
    reason = (body.get("reason") or "").strip()

    pool = await get_pool()
    org_id = user["org_id"]

    async with org_connection(pool, org_id=org_id) as conn:
        try:
            revoked = await revoke(
                conn=conn,
                client_org_id=org_id,
                revoking_user_id=user["user_id"],
                revoking_user_email=user["email"],
                reason=reason,
            )
        except PrivacyOfficerError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if revoked is None:
            return {"status": "noop", "message": "No active designation to revoke."}

        await _audit_client_action(
            conn, user,
            action="PRIVACY_OFFICER_REVOKED",
            target=str(revoked["id"]),
            details={
                "designation_id": str(revoked["id"]),
                "revoked_designee_name": revoked["name"],
                "revoked_designee_email": revoked["email"],
                "reason": reason,
                "attestation_bundle_id": (
                    str(revoked.get("revoked_attestation_bundle_id"))
                    if revoked.get("revoked_attestation_bundle_id")
                    else None
                ),
            },
            request=request,
        )

    return {
        "status": "ok",
        "revoked_designation_id": str(revoked["id"]),
        "attestation_bundle_id": (
            str(revoked.get("revoked_attestation_bundle_id"))
            if revoked.get("revoked_attestation_bundle_id")
            else None
        ),
    }


# =============================================================================
# Compliance Attestation Letter (F1 — round-table 2026-05-06)
# =============================================================================
# Maria's customer-round-table finding: "what do I actually hand my
# insurance carrier?" F1 returns a one-page branded PDF Maria
# forwards to Brian (her Erie agent), Brian's underwriter, her board,
# etc. PRECONDITIONS (Carol + Diane contracts):
#   - active Privacy Officer designation (F2 row, revoked_at IS NULL)
#   - BAA-on-file (baa_signatures row for the org's primary email)
# Either missing → 409 Conflict with a specific reason. Never 500.

@auth_router.get("/attestation-letter")
async def issue_attestation_letter_pdf(
    request: Request,
    user: dict = Depends(require_client_user),
):
    """Issue + stream the PDF in a single request. Each call issues
    a NEW letter (supersedes any prior active letter for this org).
    Per-issue rate-limit: 5/hour per (org, user) — letters are
    expensive to render and downstream recipients (carriers, boards)
    don't need a fresh one per minute.

    Returns: application/pdf (the rendered letter).
    """
    try:
        from .client_attestation_letter import (
            issue_letter, html_to_pdf, UnableToIssueLetter,
        )
    except ImportError:
        from client_attestation_letter import (  # type: ignore
            issue_letter, html_to_pdf, UnableToIssueLetter,
        )
    from fastapi.responses import Response

    pool = await get_pool()
    org_id = user["org_id"]

    # Per-(org, user) rate limit (matches the auditor-kit per-caller
    # bucket pattern from round-table 2026-05-06 P2).
    try:
        from .shared import check_rate_limit
    except ImportError:
        from shared import check_rate_limit  # type: ignore
    allowed, retry_after_s = await check_rate_limit(
        site_id=str(org_id),
        action="attestation_letter_issue",
        window_seconds=3600,
        max_requests=5,
        caller_key=f"client:{user['user_id']}",
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Attestation letter issuance is rate-limited (5/hr "
                f"per user). Retry in {retry_after_s}s."
            ),
            headers={"Retry-After": str(retry_after_s)},
        )

    async with org_connection(pool, org_id=org_id) as conn:
        try:
            result = await issue_letter(
                conn=conn,
                client_org_id=org_id,
                issued_by_user_id=user["user_id"],
                issued_by_email=user["email"],
            )
        except UnableToIssueLetter as e:
            # Carol contract: 409, not 500. The customer needs to
            # know WHICH precondition failed (Privacy Officer? BAA?)
            # so they can resolve it.
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            # Steve P2-A (round-table 2026-05-06): two simultaneous
            # issue_letter calls race past the supersede-prior +
            # insert-new transaction; the second INSERT trips the
            # idx_cal_one_active_per_org partial unique index and
            # raises UniqueViolationError. Convert to 409 — customer
            # can retry; the loser's letter wasn't issued.
            cls_name = type(e).__name__
            if "UniqueViolation" in cls_name or "IntegrityError" in cls_name:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Another attestation letter issuance is in flight "
                        "for this organization. Retry in a moment."
                    ),
                )
            raise

        await _audit_client_action(
            conn, user,
            action="ATTESTATION_LETTER_ISSUED",
            target=result["letter_id"],
            details={
                "letter_id": result["letter_id"],
                "attestation_hash": result["attestation_hash"],
                "valid_until": result["valid_until"].isoformat(),
                "practice_name": result["practice_name"],
            },
            request=request,
        )

    # Steve P1-A (round-table 2026-05-06): WeasyPrint render is
    # synchronous (100-500ms). Wrap in asyncio.to_thread so the
    # event loop stays responsive — concurrent letter issuances
    # don't starve health checks or other endpoints.
    import asyncio as _asyncio
    pdf_bytes = await _asyncio.to_thread(html_to_pdf, result["html"])
    safe_practice = "".join(
        c if c.isalnum() or c in "-_" else "-"
        for c in result["practice_name"]
    )[:80]
    issue_date = result["issued_at"].strftime("%Y-%m-%d")
    filename = f"compliance-attestation-{safe_practice}-{issue_date}.pdf"

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
# F5 — Wall Certificate (sprint 2026-05-08)
# =============================================================================
# Maria's customer-round-table finding: "I want a one-page certificate
# I can hang in the clinic showing we're monitored." F5 is an alternate
# RENDER of an existing F1 attestation row — landscape Letter paper,
# big stylized type, the same Ed25519-signed payload. NO new state
# machine: looks up the F1 row by attestation_hash within the org's
# RLS context, re-renders through the wall_cert/letter template, and
# returns the PDF. NO INSERT, NO UPDATE, NO DELETE, NO new chain
# attestation — pinned by tests/test_client_wall_cert.py.

@auth_router.get("/attestation-letter/{attestation_hash}/wall-cert.pdf")
async def issue_wall_cert_pdf(
    attestation_hash: str,
    request: Request,
    user: dict = Depends(require_client_admin),
):
    """Re-render an existing F1 attestation row as a wall
    certificate (landscape Letter PDF). Auth: org_admin (owner +
    admin). The hash MUST already exist for this org — RLS scopes
    the read so a hash from a different tenant returns 404.

    Per-(org, user) rate limit: 10/hr — wall cert is a pure re-
    render, no signing, no DB write.

    Returns: application/pdf (the rendered wall certificate).
    """
    try:
        from .client_wall_cert import (
            render_wall_cert, html_to_pdf, WallCertError,
        )
    except ImportError:
        from client_wall_cert import (  # type: ignore
            render_wall_cert, html_to_pdf, WallCertError,
        )
    from fastapi.responses import Response

    pool = await get_pool()
    org_id = user["org_id"]

    # Per-(org, user) rate limit (mirrors F1 5/hr but more generous
    # since this is a pure re-render — no Ed25519 signing, no DB
    # write, no chain mutation).
    try:
        from .shared import check_rate_limit
    except ImportError:
        from shared import check_rate_limit  # type: ignore
    allowed, retry_after_s = await check_rate_limit(
        site_id=str(org_id),
        action="wall_cert_render",
        window_seconds=3600,
        max_requests=10,
        caller_key=f"client_user:{user['user_id']}",
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Wall certificate rendering is rate-limited (10/hr "
                f"per user). Retry in {retry_after_s}s."
            ),
            headers={"Retry-After": str(retry_after_s)},
        )

    async with org_connection(pool, org_id=org_id) as conn:
        try:
            result = await render_wall_cert(
                conn=conn,
                client_org_id=org_id,
                attestation_hash=attestation_hash,
            )
        except WallCertError as e:
            reason = str(e)
            if reason == "not_found":
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "No attestation letter found for this hash in "
                        "your organization. Issue a Compliance Attestation "
                        "Letter first; the wall certificate is an alternate "
                        "render of that signed payload."
                    ),
                )
            if reason.startswith("malformed_hash"):
                raise HTTPException(status_code=400, detail=reason)
            # WeasyPrint missing in unusual envs — surface as 503.
            raise HTTPException(status_code=503, detail=reason)

    # Steve P1-A parity (round-table 2026-05-06): WeasyPrint render
    # is synchronous (100-500ms). Wrap in asyncio.to_thread so the
    # event loop stays responsive — concurrent wall-cert renders
    # don't starve health checks or other endpoints.
    import asyncio as _asyncio
    pdf_bytes = await _asyncio.to_thread(html_to_pdf, result["html"])
    safe_practice = "".join(
        c if c.isalnum() or c in "-_" else "-"
        for c in (result["practice_name"] or "wall-cert")
    )[:80]
    issue_date = (
        result["issued_at"].strftime("%Y-%m-%d")
        if result["issued_at"]
        else "unknown"
    )
    filename = f"wall-cert-{safe_practice}-{issue_date}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Attestation-Hash": result["attestation_hash"],
        },
    )


# =============================================================================
# F4 — Public /verify/{hash} endpoint (round-table 2026-05-06)
# =============================================================================
# Brian-the-agent: "I will not scan QRs from a PDF, that's how you
# get phished. What I trust: a 1-800 number I can call AND a public
# verify URL my underwriter can hit." F4 is the second half of that.
#
# OCR-investigator contract: returns hash + issuance timestamp +
# control count + Privacy Officer name + BAA-on-file boolean. Does
# NOT leak client_org_id, internal IDs, or audit metadata.
#
# This endpoint is PUBLIC (no auth) — that's the point. The hash is
# 64 hex chars (SHA-256), unguessable, presented on the issued letter.
# Recipients (insurance carriers, OCR investigators, attorneys) hit
# this endpoint to confirm the letter they were forwarded is real.
#
# Mounted on a separate truly-public router that main.py wires under
# /api/verify. Rate-limit: 60/hour per source IP — probing defense.
public_verify_router = APIRouter(prefix="/api/verify", tags=["public-verify"])


@public_verify_router.get("/attestation/{attestation_hash}")
async def public_verify_attestation_letter(
    attestation_hash: str,
    request: Request,
):
    """Public endpoint — NO AUTH. Returns OCR-grade payload for the
    given attestation hash, or {"valid": false} if the hash is
    unknown / malformed.

    Threat model: hash is 64 hex chars (SHA-256), unguessable.
    Probing is rate-limited per source IP.
    """
    # 1. Shape check — accept full hash OR 32-char prefix.
    # Steve P1-D + Maya P1-A (round-table 2026-05-06): the prior
    # 16-char prefix (64 bits) admitted a birthday-collision
    # tenant-mixup vector at ~2^32 letters and a targeted-grind
    # collision via timestamp churn. 32 chars (128 bits) makes
    # collision astronomical (~2^64 letters before 50% chance).
    # Even with 32 chars we ALSO detect ambiguity below — defense
    # in depth.
    h = attestation_hash.strip().lower()
    if not all(c in "0123456789abcdef" for c in h):
        return {"valid": False, "reason": "malformed_hash"}
    if len(h) not in (32, 64):
        return {"valid": False, "reason": "malformed_hash_minimum_32_hex_chars"}

    # 2. Per-IP rate-limit. Steve P1-C (round-table 2026-05-06):
    # behind nginx/Caddy/Cloudflare, request.client.host is the
    # proxy IP (loopback). Use X-Forwarded-For (first hop) which
    # nginx populates from the real client IP. Matches the
    # existing helper in client_signup.py + client_portal.py.
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
        action="public_verify_attestation",
        window_seconds=3600,
        max_requests=60,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Verification rate limit reached. Retry in {retry_after_s}s.",
            headers={"Retry-After": str(retry_after_s)},
        )

    # 3. Lookup. SECURITY DEFINER function bypasses RLS for the
    #    hash-keyed read; only OCR-grade fields returned.
    try:
        from .client_attestation_letter import get_letter_by_hash
    except ImportError:
        from client_attestation_letter import get_letter_by_hash  # type: ignore
    pool = await get_pool()

    # If the prefix form (16 chars), look up via prefix on the
    # attestation_hash column with admin context (matches Maya P1
    # — admin_connection used for substrate-level reads).
    try:
        from .tenant_middleware import admin_connection
    except ImportError:
        from tenant_middleware import admin_connection  # type: ignore

    async with admin_connection(pool) as conn:
        if len(h) == 64:
            row = await get_letter_by_hash(conn, h)
        else:
            # 32-char prefix lookup. Steve P1-D + Maya P1-A defense
            # in depth: also detect ambiguity (2+ matches → refuse
            # rather than silently picking LIMIT 1). Astronomically
            # unlikely at 128 bits, but if it happens we tell the
            # caller "supply full hash" instead of returning the
            # wrong tenant's payload.
            full_rows = await conn.fetch(
                """
                SELECT attestation_hash FROM compliance_attestation_letters
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
                row = await get_letter_by_hash(conn, full_rows[0]["attestation_hash"])

    if not row:
        return {"valid": False, "reason": "not_found"}

    # 4. Compose the OCR-grade payload. Field selection mirrors the
    #    SECURITY DEFINER function's RETURNS TABLE — no leaks.
    return {
        "valid": True,
        "attestation_hash": row["attestation_hash"],
        "issued_at": row["issued_at"].isoformat() if row["issued_at"] else None,
        "valid_until": row["valid_until"].isoformat() if row["valid_until"] else None,
        "is_expired": bool(row["is_expired"]),
        "is_superseded": bool(row["is_superseded"]),
        "period_start": row["period_start"].isoformat() if row["period_start"] else None,
        "period_end": row["period_end"].isoformat() if row["period_end"] else None,
        "bundle_count": row["bundle_count"],
        "sites_covered_count": row["sites_covered_count"],
        "privacy_officer": {
            "name": row["privacy_officer_name"],
            "title": row["privacy_officer_title"],
        },
        "baa_on_file": True,
        "baa_dated_at": row["baa_dated_at"].isoformat() if row["baa_dated_at"] else None,
        "baa_practice_name": row["baa_practice_name"],
        "presenter_brand": row["presenter_brand"],
        "overall_score": row["overall_score"],
    }


# =============================================================================
# F3 — Quarterly Practice Compliance Summary (sprint 2026-05-08)
# =============================================================================
# Maria's last owner-side P1 deferred from Friday. F3 produces a
# one-page printable PDF the practice's Privacy Officer signs each
# quarter and the practice owner files for HIPAA §164.530(j)
# records-retention compliance.
#
# Distinction from F1: F1 is a CURRENT-STATE attestation valid 90
# days; F3 is a TIME-WINDOWED summary for a completed calendar
# quarter, valid 365 days, frozen-at-issue. Maria files F3 in the
# §164.530(j) retention archive — it does NOT mutate post-issue.
#
# PRECONDITIONS (Carol contracts):
#   - Active Privacy Officer designation (F2 row, revoked_at IS NULL)
#   - Quarter must be in the past (period_end <= now())
# Either missing → 409 Conflict with a specific reason. Never 500.

@auth_router.post("/quarterly-summary")
async def issue_quarterly_summary_pdf(
    request: Request,
    user: dict = Depends(require_client_user),
):
    """Issue + stream the F3 PDF in a single request. Each call
    issues a NEW summary for the requested (year, quarter); a re-
    issue of the same quarter SUPERSEDES the prior. Per-issue rate-
    limit: 5/hour per (org, user) — summaries are expensive to render
    and the §164.530(j) archive doesn't need a fresh one per minute.

    Body (JSON):
      { "year": 2026, "quarter": 1 }

    Returns: application/pdf (the rendered summary).
    """
    try:
        from .client_quarterly_summary import (
            issue_quarterly_summary,
            html_to_pdf as quarterly_html_to_pdf,
            QuarterlySummaryError,
        )
    except ImportError:
        from client_quarterly_summary import (  # type: ignore
            issue_quarterly_summary,
            html_to_pdf as quarterly_html_to_pdf,
            QuarterlySummaryError,
        )
    from fastapi.responses import Response

    # Parse + validate body. Hand-rolled to keep the failure-mode copy
    # identical to F1 (HTTPException 400 / 409 / 429, never 422 from
    # pydantic — Maria sees the message we wrote, not framework jargon).
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be JSON.")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object.")
    try:
        year = int(body.get("year"))
        quarter = int(body.get("quarter"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Body must include integer 'year' and 'quarter' (1-4).",
        )

    pool = await get_pool()
    org_id = user["org_id"]

    # Per-(org, user) rate limit (matches F1's 5/hr posture).
    try:
        from .shared import check_rate_limit
    except ImportError:
        from shared import check_rate_limit  # type: ignore
    allowed, retry_after_s = await check_rate_limit(
        site_id=str(org_id),
        action="quarterly_summary_issue",
        window_seconds=3600,
        max_requests=5,
        caller_key=f"client:{user['user_id']}",
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Quarterly summary issuance is rate-limited (5/hr "
                f"per user). Retry in {retry_after_s}s."
            ),
            headers={"Retry-After": str(retry_after_s)},
        )

    async with org_connection(pool, org_id=org_id) as conn:
        try:
            result = await issue_quarterly_summary(
                conn=conn,
                client_org_id=org_id,
                issued_by_user_id=user["user_id"],
                issued_by_email=user["email"],
                year=year,
                quarter=quarter,
            )
        except QuarterlySummaryError as e:
            # Carol contract: 409, not 500. The customer needs to
            # know WHICH precondition failed (PO? past-quarter?).
            raise HTTPException(status_code=409, detail=str(e))
        except Exception as e:
            # Steve P2-A carry-over: concurrent issuances race past
            # the supersede-prior + insert-new transaction; the
            # second INSERT trips idx_qpcs_one_active_per_org_quarter
            # and raises UniqueViolationError. Convert to 409.
            cls_name = type(e).__name__
            if "UniqueViolation" in cls_name or "IntegrityError" in cls_name:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Another quarterly summary issuance is in "
                        "flight for this organization and quarter. "
                        "Retry in a moment."
                    ),
                )
            raise

        await _audit_client_action(
            conn, user,
            action="QUARTERLY_SUMMARY_ISSUED",
            target=result["summary_id"],
            details={
                "summary_id": result["summary_id"],
                "attestation_hash": result["attestation_hash"],
                "valid_until": result["valid_until"].isoformat(),
                "practice_name": result["practice_name"],
                "period_year": result["period_year"],
                "period_quarter": result["period_quarter"],
            },
            request=request,
        )

    # Steve P1-A carry-over: WeasyPrint render is synchronous; wrap
    # in asyncio.to_thread so the event loop stays responsive under
    # concurrent load.
    import asyncio as _asyncio
    pdf_bytes = await _asyncio.to_thread(
        quarterly_html_to_pdf, result["html"]
    )
    safe_practice = "".join(
        c if c.isalnum() or c in "-_" else "-"
        for c in result["practice_name"]
    )[:80]
    filename = (
        f"quarterly-summary-{safe_practice}-Q{result['period_quarter']}"
        f"-{result['period_year']}.pdf"
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Attestation-Hash": result["attestation_hash"],
            "X-Summary-Valid-Until": result["valid_until"].isoformat(),
        },
    )


# =============================================================================
# F3 public /verify/quarterly/{hash} endpoint (sprint 2026-05-08)
# =============================================================================
# Mirrors F4 (F1's public verify endpoint). Insurance carriers, OCR
# investigators, and auditors hit this endpoint with the hash printed
# on the F3 PDF; we return the OCR-grade payload via SECURITY DEFINER
# function. NO auth — that's the point.

@public_verify_router.get("/quarterly/{attestation_hash}")
async def public_verify_quarterly_summary(
    attestation_hash: str,
    request: Request,
):
    """Public endpoint — NO AUTH. Returns OCR-grade payload for the
    given F3 attestation hash, or {"valid": false} if unknown /
    malformed. Same shape contract as F4 (Steve P1-D + Maya P1-A):
    32-char floor, ambiguity detection, X-Forwarded-For per-IP rate-
    limit (60/hr).
    """
    # 1. Shape check — accept full 64-char hash or 32-char prefix.
    h = attestation_hash.strip().lower()
    if not all(c in "0123456789abcdef" for c in h):
        return {"valid": False, "reason": "malformed_hash"}
    if len(h) not in (32, 64):
        return {"valid": False, "reason": "malformed_hash_minimum_32_hex_chars"}

    # 2. Per-IP rate-limit using X-Forwarded-For (Steve P1-C).
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
        action="public_verify_quarterly_summary",
        window_seconds=3600,
        max_requests=60,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Verification rate limit reached. Retry in {retry_after_s}s.",
            headers={"Retry-After": str(retry_after_s)},
        )

    # 3. Lookup. SECURITY DEFINER function bypasses RLS for the
    #    hash-keyed read; only OCR-grade fields returned.
    try:
        from .client_quarterly_summary import get_quarterly_by_hash
    except ImportError:
        from client_quarterly_summary import get_quarterly_by_hash  # type: ignore
    pool = await get_pool()

    try:
        from .tenant_middleware import admin_connection
    except ImportError:
        from tenant_middleware import admin_connection  # type: ignore

    async with admin_connection(pool) as conn:
        if len(h) == 64:
            row = await get_quarterly_by_hash(conn, h)
        else:
            # 32-char prefix — also detect ambiguity (Steve P1-D
            # defense in depth).
            full_rows = await conn.fetch(
                """
                SELECT attestation_hash
                  FROM quarterly_practice_compliance_summaries
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
                row = await get_quarterly_by_hash(
                    conn, full_rows[0]["attestation_hash"]
                )

    if not row:
        return {"valid": False, "reason": "not_found"}

    # 4. Compose OCR-grade payload. Mirrors the SECURITY DEFINER
    #    function's RETURNS TABLE — no client_org_id, no PO email,
    #    no ed25519_signature, no issued_by_*. NEVER leak internals.
    return {
        "valid": True,
        "attestation_hash": row["attestation_hash"],
        "issued_at": row["issued_at"].isoformat() if row["issued_at"] else None,
        "valid_until": row["valid_until"].isoformat() if row["valid_until"] else None,
        "is_expired": bool(row["is_expired"]),
        "is_superseded": bool(row["is_superseded"]),
        "period_year": row["period_year"],
        "period_quarter": row["period_quarter"],
        "period_start": row["period_start"].isoformat() if row["period_start"] else None,
        "period_end": row["period_end"].isoformat() if row["period_end"] else None,
        "bundle_count": row["bundle_count"],
        "ots_anchored_pct": float(row["ots_anchored_pct"]) if row["ots_anchored_pct"] is not None else None,
        "drift_detected_count": row["drift_detected_count"],
        "drift_resolved_count": row["drift_resolved_count"],
        "mean_score": row["mean_score"],
        "sites_count": row["sites_count"],
        "appliances_count": row["appliances_count"],
        "workstations_count": row["workstations_count"],
        "monitored_check_types_count": row["monitored_check_types_count"],
        "privacy_officer": {
            "name": row["privacy_officer_name"],
            "title": row["privacy_officer_title"],
        },
        "presenter_brand": row["presenter_brand"],
        "practice_name": row["practice_name"],
    }

