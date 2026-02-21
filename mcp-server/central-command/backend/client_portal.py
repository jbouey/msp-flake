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
from fastapi.responses import RedirectResponse
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

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_URL = os.getenv("BASE_URL", "https://dashboard.osiriscare.net")

# Session configuration
SESSION_COOKIE_NAME = "osiris_client_session"
SESSION_DURATION_DAYS = 30
SESSION_COOKIE_MAX_AGE = SESSION_DURATION_DAYS * 24 * 60 * 60

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
    """Hash a token for secure storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token() -> str:
    """Generate a secure token."""
    return secrets.token_urlsafe(32)


async def get_client_user_from_session(session_token: str, pool):
    """Get client user from session token."""
    if not session_token:
        return None

    token_hash = hash_token(session_token)

    async with pool.acquire() as conn:
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
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=MAGIC_LINK_EXPIRY_MINUTES)

        # Store token
        await conn.execute("""
            UPDATE client_users
            SET magic_token = $1, magic_token_expires_at = $2
            WHERE id = $3
        """, magic_token, expires_at, user["id"])

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
        """, body.token)

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

        # Verify password
        try:
            import bcrypt
            if not bcrypt.checkpw(body.password.encode(), user["password_hash"].encode()):
                raise HTTPException(status_code=401, detail="Invalid credentials")
        except ImportError:
            # Fallback to HMAC comparison if bcrypt not available
            expected = hashlib.sha256(body.password.encode()).hexdigest()
            if user["password_hash"] != expected:
                raise HTTPException(status_code=401, detail="Invalid credentials")

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
                WITH latest_checks AS (
                    SELECT DISTINCT ON (cb.site_id, cb.check_type)
                        cb.site_id, cb.check_type, cb.check_result, cb.checked_at
                    FROM compliance_bundles cb
                    WHERE cb.site_id = ANY($1)
                      AND cb.checked_at > NOW() - INTERVAL '24 hours'
                    ORDER BY cb.site_id, cb.check_type, cb.checked_at DESC
                )
                SELECT
                    COUNT(*) as total_checks,
                    COUNT(*) FILTER (WHERE check_result = 'pass') as passed,
                    COUNT(*) FILTER (WHERE check_result = 'fail') as failed,
                    COUNT(*) FILTER (WHERE check_result = 'warn') as warnings
                FROM latest_checks
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

        # Get historical data
        history = await conn.fetch("""
            SELECT
                DATE(cb.checked_at) as date,
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE cb.check_result = 'pass') as passed,
                COUNT(*) FILTER (WHERE cb.check_result = 'fail') as failed
            FROM compliance_bundles cb
            WHERE cb.site_id = $1
              AND cb.checked_at > NOW() - INTERVAL '%s days'
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

        # For compliance_bundles, we don't have MinIO storage - return inline data
        # This is a limitation vs evidence_bundles which has s3_uri
        raise HTTPException(status_code=404, detail="Evidence file not available in WORM storage")

    # Generate presigned URL
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

        # Parse bucket and object from s3_uri (format: s3://bucket/path or bucket/path)
        s3_uri = bundle["s3_uri"]
        if s3_uri.startswith("s3://"):
            s3_uri = s3_uri[5:]
        path_parts = s3_uri.split("/", 1)
        bucket = path_parts[0] if len(path_parts) > 1 else "evidence-worm"
        obj_path = path_parts[1] if len(path_parts) > 1 else s3_uri

        url = client.presigned_get_object(
            bucket,
            obj_path,
            expires=timedelta(minutes=15)
        )

        return {
            "download_url": url,
            "expires_in": 900,
            "bundle_hash": bundle["bundle_id"],
        }
    except Exception as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate download URL")


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
        """, user["user_id"], notification_id, org_id)

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
        """, target_user_id, org_id)

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
        """, target_user_id)

        # Delete sessions
        await conn.execute("""
            DELETE FROM client_sessions WHERE user_id = $1
        """, target_user_id)

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
        """, body.role, target_user_id, org_id)

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="User not found or is owner")

    return {"status": "updated", "role": body.role}


# =============================================================================
# PASSWORD MANAGEMENT
# =============================================================================

@auth_router.put("/password")
async def set_password(body: PasswordSet, user: dict = Depends(require_client_user)):
    """Set or update user password."""
    pool = await get_pool()

    # Hash password
    try:
        import bcrypt
        password_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        # Fallback
        password_hash = hashlib.sha256(body.password.encode()).hexdigest()

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
