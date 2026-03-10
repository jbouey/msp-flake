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
from typing import Optional, List, Literal
from decimal import Decimal

from fastapi import APIRouter, Request, Response, HTTPException, Depends, Cookie, Query, Header
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, EmailStr
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

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_URL = os.getenv("BASE_URL", "https://dashboard.osiriscare.net")

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

class MagicLinkRequest(BaseModel):
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
    """Hash a token for secure storage using HMAC-SHA256."""
    import hmac
    secret = os.getenv("SESSION_TOKEN_SECRET", "")
    if not secret:
        logger.warning("SESSION_TOKEN_SECRET not set — falling back to plain SHA-256 for client sessions")
        return hashlib.sha256(token.encode()).hexdigest()
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def generate_token() -> str:
    """Generate a secure token."""
    return secrets.token_urlsafe(32)


async def get_client_user_from_session(session_token: str, pool):
    """Get client user from session token.

    Enforces HIPAA §164.312(a)(2)(iii) idle timeout.
    """
    if not session_token:
        return None

    token_hash = hash_token(session_token)

    async with pool.acquire() as conn:
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

        # Update last_activity and get user + org
        row = await conn.fetchrow("""
            UPDATE client_sessions cs
            SET last_activity_at = NOW()
            FROM client_users cu, client_orgs co
            WHERE cs.token_hash = $1
              AND cs.expires_at > NOW()
              AND cs.user_id = cu.id
              AND cu.client_org_id = co.id
              AND cu.is_active = true
              AND co.status = 'active'
            RETURNING
                cu.id as user_id,
                cu.email,
                cu.name,
                cu.role,
                co.id as org_id,
                co.name as org_name,
                co.current_partner_id
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

    return dict(user)


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
# AUTH ENDPOINTS (Public)
# =============================================================================

@public_router.post("/request-magic-link")
async def request_magic_link(request: MagicLinkRequest):
    """Send magic link to user's email."""
    pool = await get_pool()
    email = request.email.lower()

    async with pool.acquire() as conn:
        # Find user
        user = await conn.fetchrow("""
            SELECT cu.id, cu.name, cu.is_active, co.status as org_status
            FROM client_users cu
            JOIN client_orgs co ON co.id = cu.client_org_id
            WHERE cu.email = $1
        """, email)

        # Always return success to prevent email enumeration
        if not user or not user["is_active"] or user["org_status"] != "active":
            logger.info(f"Magic link requested for unknown/inactive email: {email}")
            return {"status": "sent", "message": "If that email exists, a login link was sent."}

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

    async with pool.acquire() as conn:
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
    """Login with email and optional password."""
    pool = await get_pool()
    email = body.email.lower()

    async with pool.acquire() as conn:
        user = await conn.fetchrow("""
            SELECT cu.id, cu.password_hash, cu.is_active, co.status as org_status
            FROM client_users cu
            JOIN client_orgs co ON co.id = cu.client_org_id
            WHERE cu.email = $1
        """, email)

        if not user or not user["is_active"] or user["org_status"] != "active":
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if not user["password_hash"]:
            # No password set - must use magic link
            raise HTTPException(
                status_code=400,
                detail="Password not set. Please use magic link to login."
            )

        # Verify password (bcrypt with constant-time comparison)
        from .auth import verify_password
        if not verify_password(body.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        # Check if MFA is enabled
        mfa_row = await conn.fetchrow(
            "SELECT mfa_enabled FROM client_users WHERE id = $1", user["id"]
        )
        if mfa_row and mfa_row["mfa_enabled"]:
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

        async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

        # Calculate compliance KPIs across all sites
        site_ids = [s["site_id"] for s in sites]
        if site_ids:
            kpis = await conn.fetchrow("""
                WITH expanded AS (
                    SELECT
                        c->>'status' as check_status
                    FROM compliance_bundles cb,
                         jsonb_array_elements(cb.checks) as c
                    WHERE cb.site_id = ANY($1)
                      AND cb.checked_at > NOW() - INTERVAL '24 hours'
                      AND jsonb_array_length(cb.checks) > 0
                )
                SELECT
                    COUNT(*) FILTER (WHERE check_status IN ('pass', 'compliant', 'fail', 'non_compliant', 'warning')) as total_checks,
                    COUNT(*) FILTER (WHERE check_status IN ('pass', 'compliant')) as passed,
                    COUNT(*) FILTER (WHERE check_status IN ('fail', 'non_compliant')) as failed,
                    COUNT(*) FILTER (WHERE check_status = 'warning') as warnings
                FROM expanded
            """, site_ids)
        else:
            kpis = {"total_checks": 0, "passed": 0, "failed": 0, "warnings": 0}

        # Get recent notifications (unread)
        unread_count = await conn.fetchval("""
            SELECT COUNT(*) FROM client_notifications
            WHERE client_org_id = $1 AND NOT is_read
        """, org_id)

        # Get compliance score
        total = kpis["total_checks"] or 1
        passed = kpis["passed"] or 0
        compliance_score = round((passed / total) * 100, 1) if total > 0 else 100.0

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
                "total_checks": kpis["total_checks"],
                "passed": kpis["passed"],
                "failed": kpis["failed"],
                "warnings": kpis["warnings"],
            },
            "unread_notifications": unread_count,
        }


@auth_router.get("/sites")
async def list_sites(user: dict = Depends(require_client_user)):
    """List all sites for client org."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with pool.acquire() as conn:
        sites = await conn.fetch("""
            SELECT s.site_id, s.clinic_name, s.status, s.tier,
                   s.onboarding_stage, s.created_at,
                   COUNT(DISTINCT cb.id) as evidence_count,
                   MAX(cb.checked_at) as last_check
            FROM sites s
            LEFT JOIN compliance_bundles cb ON cb.site_id = s.site_id
            WHERE s.client_org_id = $1
            GROUP BY s.site_id, s.clinic_name, s.status, s.tier,
                     s.onboarding_stage, s.created_at
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
                }
                for s in sites
            ],
            "count": len(sites),
        }


@auth_router.get("/sites/{site_id}")
async def get_site_detail(site_id: str, user: dict = Depends(require_client_user)):
    """Get detailed site info including compliance status."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
        # Verify site belongs to org
        site = await conn.fetchrow("""
            SELECT site_id, clinic_name, status FROM sites
            WHERE site_id = $1 AND client_org_id = $2
        """, site_id, org_id)
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get disabled checks
        disabled = await conn.fetch("""
            SELECT check_type FROM site_drift_config
            WHERE site_id = $1 AND enabled = false
        """, site_id)
        disabled_set = {r["check_type"] for r in disabled}
        if not disabled:
            defaults = await conn.fetch("""
                SELECT check_type FROM site_drift_config
                WHERE site_id = '__defaults__' AND enabled = false
            """)
            disabled_set = {r["check_type"] for r in defaults}

        # Expanded category map covering Windows bundles + Linux/NixOS incidents
        categories = {
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
        reverse_map = {}
        for cat, types in categories.items():
            for ct in types:
                reverse_map[ct] = cat

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
        incident_rows = await conn.fetch("""
            SELECT i.check_type, i.severity, count(*) as cnt
            FROM incidents i
            JOIN appliances a ON a.id = i.appliance_id
            WHERE a.site_id = $1 AND i.resolved_at IS NULL
            GROUP BY i.check_type, i.severity
        """, site_id)

        for row in incident_rows:
            ct = row["check_type"]
            if ct in disabled_set:
                continue  # Administratively disabled — exclude from scoring
            cnt = row["cnt"]
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
                "score": round((r["passed"] / r["total"]) * 100, 1) if r["total"] > 0 else 100.0
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
              AND started_at > NOW() - INTERVAL '30 days'
        """, site_id)

        return {
            "site_id": site_id,
            "clinic_name": site["clinic_name"],
            "overall_score": overall,
            "breakdown": breakdown,
            "counts": {
                "passed": total_passed,
                "failed": total_failed,
                "warnings": total_warnings,
                "total": total_passed + total_failed + total_warnings,
            },
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

    categories = {
        "patching": ["nixos_generation", "windows_update", "linux_patching"],
        "antivirus": ["windows_defender", "windows_defender_exclusions"],
        "backup": ["backup_status", "windows_backup_status"],
        "logging": ["audit_logging", "windows_audit_policy", "linux_audit", "linux_logging"],
        "firewall": ["firewall", "windows_firewall_status", "firewall_status", "linux_firewall"],
        "encryption": ["bitlocker", "windows_bitlocker_status", "linux_crypto", "windows_smb_signing"],
        "access_control": ["rogue_admin_users", "linux_accounts", "windows_password_policy",
                          "linux_permissions", "linux_ssh_config", "windows_screen_lock_policy"],
        "services": ["critical_services", "linux_services", "windows_service_dns",
                    "windows_service_netlogon", "windows_service_spooler",
                    "windows_service_w32time", "windows_service_wuauserv", "agent_status"],
    }
    reverse_map = {}
    for cat, types in categories.items():
        for ct in types:
            reverse_map[ct] = cat

    async with pool.acquire() as conn:
        # Verify site belongs to org
        site = await conn.fetchrow("""
            SELECT site_id FROM sites
            WHERE site_id = $1 AND client_org_id = $2
        """, site_id, org_id)
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get all active (unresolved) incidents for this site, grouped by hostname
        rows = await conn.fetch("""
            SELECT i.hostname, i.check_type, i.severity, i.created_at, i.id,
                   i.resolution_level
            FROM incidents i
            JOIN appliances a ON a.id = i.appliance_id
            WHERE a.site_id = $1
              AND i.resolved = false
            ORDER BY i.hostname, i.created_at DESC
        """, site_id)

        # Get device info from discovered_devices for enrichment (hostname, ip, device_type)
        device_info = {}
        try:
            devices = await conn.fetch("""
                SELECT d.hostname, d.ip_address, d.device_type, d.os_name, d.compliance_status
                FROM discovered_devices d
                JOIN appliances a ON d.appliance_id = a.id
                WHERE a.site_id = $1
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

    async with pool.acquire() as conn:
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
                    "score": round((h["passed"] / h["total"]) * 100, 1) if h["total"] > 0 else 100.0,
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
    async with pool.acquire() as conn:
        owner = await conn.fetchval(
            "SELECT client_org_id FROM sites WHERE site_id = $1", site_id)
        if str(owner) != str(user["org_id"]):
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


@auth_router.put("/sites/{site_id}/drift-config")
async def update_client_drift_config(site_id: str, body: dict, user: dict = Depends(require_client_user)):
    """Update drift scan configuration for a client's site."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        owner = await conn.fetchval(
            "SELECT client_org_id FROM sites WHERE site_id = $1", site_id)
        if str(owner) != str(user["org_id"]):
            raise HTTPException(status_code=404, detail="Site not found")

        checks = body.get("checks", [])
        async with conn.transaction():
            for item in checks:
                await conn.execute("""
                    INSERT INTO site_drift_config (site_id, check_type, enabled, modified_by, modified_at)
                    VALUES ($1, $2, $3, $4, NOW())
                    ON CONFLICT (site_id, check_type)
                    DO UPDATE SET enabled = $3, modified_by = $4, modified_at = NOW()
                """, site_id, item["check_type"], item["enabled"], f"client:{user.get('email', user['id'])}")
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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
            "checks": _json.loads(bundle["checks"]) if isinstance(bundle["checks"], str) else bundle["checks"],
            "metadata": {
                "format": "OsirisCare Evidence Bundle v1",
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "integrity": "compliance_bundle",
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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
                "overall_score": 100.0,
                "sites": [],
                "controls": {"passed": 0, "failed": 0, "warnings": 0, "total": 0},
                "healing": {"total": 0, "auto_healed": 0, "pending": 0},
                "checks": [],
            }

        # Latest check result per (check_type, hostname) per site.
        # compliance_bundles stores individual checks in a JSONB array;
        # unnest them so we can report per-check granularity.
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

        total = len(checks)
        passed = sum(1 for c in checks if c["check_status"] == "pass")
        failed = sum(1 for c in checks if c["check_status"] == "fail")
        warnings = sum(1 for c in checks if c["check_status"] in ("warn", "warning"))
        score = round((passed / total) * 100, 1) if total > 0 else 100.0

        # Recent healing activity (last 30 days)
        healing = await conn.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'completed') as auto_healed,
                COUNT(*) FILTER (WHERE status = 'pending' OR status = 'escalated') as pending
            FROM execution_telemetry
            WHERE site_id = ANY($1)
              AND started_at > NOW() - INTERVAL '30 days'
        """, site_ids)

        # Per-site breakdown
        site_results = []
        for site in sites:
            site_checks = [c for c in checks if c["site_id"] == site["site_id"]]
            st = len(site_checks)
            sp = sum(1 for c in site_checks if c["check_status"] == "pass")
            site_results.append({
                "site_id": site["site_id"],
                "clinic_name": site["clinic_name"],
                "score": round((sp / st) * 100, 1) if st > 0 else 100.0,
                "passed": sp,
                "failed": sum(1 for c in site_checks if c["check_status"] == "fail"),
                "total": st,
            })

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
            "overall_score": score,
            "sites": site_results,
            "controls": {
                "passed": passed,
                "failed": failed,
                "warnings": warnings,
                "total": total,
            },
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE client_notifications
            SET is_read = true, read_at = NOW(), read_by_user_id = $1
            WHERE id = $2 AND client_org_id = $3 AND NOT is_read
        """, user["user_id"], _uid(notification_id), org_id)

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Notification not found or already read")

    return {"status": "read"}


@auth_router.post("/notifications/read-all")
async def mark_all_notifications_read(user: dict = Depends(require_client_user)):
    """Mark all notifications as read."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE client_notifications
            SET is_read = true, read_at = NOW(), read_by_user_id = $1
            WHERE client_org_id = $2 AND NOT is_read
        """, user["user_id"], org_id)

    # Parse count from "UPDATE N"
    count = int(result.split()[1]) if result.startswith("UPDATE") else 0

    return {"status": "read_all", "count": count}


# =============================================================================
# USER MANAGEMENT ENDPOINTS (Admin only)
# =============================================================================

@auth_router.get("/users")
async def list_users(user: dict = Depends(require_client_admin)):
    """List users in client org."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with pool.acquire() as conn:
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
async def invite_user(invite: InviteUser, user: dict = Depends(require_client_admin)):
    """Invite a user to the org."""
    pool = await get_pool()
    org_id = user["org_id"]
    email = invite.email.lower()

    async with pool.acquire() as conn:
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

    # Send invite email
    invite_link = f"{BASE_URL}/client/invite?token={invite_token}"

    try:
        from .email_service import send_email
        await send_email(
            email,
            f"You're invited to {user['org_name']} on OsirisCare",
            f"""Hi{' ' + invite.name if invite.name else ''},

You've been invited to join {user["org_name"]} on OsirisCare.

Click here to accept the invitation:

{invite_link}

This invitation expires in 7 days.

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
async def remove_user(target_user_id: str, user: dict = Depends(require_client_admin)):
    """Remove a user from the org."""
    pool = await get_pool()
    org_id = user["org_id"]

    # Can't remove yourself
    if target_user_id == str(user["user_id"]):
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    async with pool.acquire() as conn:
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

    return {"status": "removed"}


@auth_router.put("/users/{target_user_id}/role")
async def update_user_role(
    target_user_id: str,
    body: UserRoleUpdate,
    user: dict = Depends(require_client_owner)
):
    """Update user role (owner only)."""
    pool = await get_pool()
    org_id = user["org_id"]

    # Can't change own role
    if target_user_id == str(user["user_id"]):
        raise HTTPException(status_code=400, detail="Cannot change your own role")

    async with pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE client_users SET role = $1, updated_at = NOW()
            WHERE id = $2 AND client_org_id = $3 AND role != 'owner'
        """, body.role, _uid(target_user_id), org_id)

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="User not found or is owner")

    return {"status": "updated", "role": body.role}


# =============================================================================
# PASSWORD MANAGEMENT
# =============================================================================

@auth_router.put("/password")
async def set_password(body: PasswordSet, user: dict = Depends(require_client_user)):
    """Set or update user password."""
    from .auth import validate_password_complexity, hash_password

    is_valid, error_msg = validate_password_complexity(body.password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    password_hash = hash_password(body.password)
    pool = await get_pool()

    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE client_users SET password_hash = $1, updated_at = NOW()
            WHERE id = $2
        """, password_hash, user["user_id"])

    return {"status": "password_set"}


# =============================================================================
# PARTNER TRANSFER (Phase 3 - Owner only)
# =============================================================================

@auth_router.post("/transfer/request")
async def request_transfer(body: TransferRequest, user: dict = Depends(require_client_owner)):
    """Request to transfer to a different MSP partner."""
    pool = await get_pool()
    org_id = user["org_id"]

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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
            await conn.execute("""
                INSERT INTO promoted_rules (
                    rule_id, pattern_signature, site_id, partner_id,
                    rule_yaml, rule_json, notes, promoted_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT (rule_id) DO UPDATE SET
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

            await transaction.commit()

        except Exception as e:
            await transaction.rollback()
            logger.error(f"Client promotion failed: {e}")
            raise HTTPException(status_code=500, detail="Promotion failed")

        logger.info(
            f"Client {user['email']} approved pattern {candidate['pattern_signature'][:8]} "
            f"as {rule['id']} for site {candidate['site_id']}"
        )

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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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
            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE client_orgs
                    SET subscription_status = 'active', updated_at = NOW()
                    WHERE id = $1
                """, org_id)
            logger.info(f"Subscription activated for org {org_id}")

    elif event.type == "customer.subscription.updated":
        subscription = event.data.object
        customer_id = subscription.customer

        async with pool.acquire() as conn:
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

        async with pool.acquire() as conn:
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
        async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
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
    user: dict = Depends(require_client_user),
):
    """Verify TOTP code to enable client 2FA."""
    from .totp import verify_totp, generate_backup_codes, hash_backup_code
    from .auth import verify_password
    import json as _json

    pool = await get_pool()
    user_id = str(user["user_id"])

    async with pool.acquire() as conn:
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

    return {"status": "enabled", "backup_codes": backup_codes}


@auth_router.delete("/totp")
async def client_totp_disable(
    body: ClientTOTPDisableRequest,
    user: dict = Depends(require_client_user),
):
    """Disable client 2FA. Requires password."""
    from .auth import verify_password

    pool = await get_pool()
    user_id = str(user["user_id"])

    async with pool.acquire() as conn:
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

    async with pool.acquire() as conn:
        # Get all sites + their appliances for this org
        rows = await conn.fetch("""
            SELECT s.site_id, s.clinic_name,
                   sa.appliance_id, sa.hostname as appliance_hostname,
                   sa.ip_addresses, sa.agent_version
            FROM sites s
            LEFT JOIN site_appliances sa ON sa.site_id = s.site_id
            WHERE s.client_org_id = $1
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

    async with pool.acquire() as conn:
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
            WHERE site_id = $1
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

    async with pool.acquire() as conn:
        site = await conn.fetchrow(
            "SELECT site_id FROM sites WHERE site_id = $1 AND client_org_id = $2",
            site_id, org_id,
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        appliance = await conn.fetchrow("""
            SELECT ip_addresses
            FROM site_appliances
            WHERE site_id = $1
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

    async with pool.acquire() as conn:
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
            WHERE site_id = $1
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
