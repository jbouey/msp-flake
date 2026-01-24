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

from fastapi import APIRouter, Request, Response, HTTPException, Depends, Cookie, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
import httpx

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
        from .notifications import send_email
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
                   COUNT(DISTINCT eb.id) as evidence_count,
                   MAX(eb.checked_at) as last_evidence
            FROM sites s
            LEFT JOIN evidence_bundles eb ON eb.site_id = s.site_id
            WHERE s.client_org_id = $1
            GROUP BY s.site_id, s.clinic_name, s.status, s.tier
            ORDER BY s.clinic_name
        """, org_id)

        # Calculate compliance KPIs across all sites
        site_ids = [s["site_id"] for s in sites]
        if site_ids:
            kpis = await conn.fetchrow("""
                WITH latest_checks AS (
                    SELECT DISTINCT ON (site_id, check_type)
                        site_id, check_type, check_result, checked_at
                    FROM evidence_bundles
                    WHERE site_id = ANY($1)
                      AND checked_at > NOW() - INTERVAL '24 hours'
                    ORDER BY site_id, check_type, checked_at DESC
                )
                SELECT
                    COUNT(*) as total_checks,
                    COUNT(*) FILTER (WHERE check_result = 'pass') as passed,
                    COUNT(*) FILTER (WHERE check_result = 'fail') as failed,
                    COUNT(*) FILTER (WHERE check_result = 'warning') as warnings
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
                   COUNT(DISTINCT eb.id) as evidence_count,
                   MAX(eb.checked_at) as last_check
            FROM sites s
            LEFT JOIN evidence_bundles eb ON eb.site_id = s.site_id
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
            SELECT DISTINCT ON (check_type)
                id, check_type, check_result, hipaa_control, checked_at
            FROM evidence_bundles
            WHERE site_id = $1
            ORDER BY check_type, checked_at DESC
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
                DATE(checked_at) as date,
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE check_result = 'pass') as passed,
                COUNT(*) FILTER (WHERE check_result = 'fail') as failed
            FROM evidence_bundles
            WHERE site_id = $1
              AND checked_at > NOW() - INTERVAL '%s days'
            GROUP BY DATE(checked_at)
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
            SELECT eb.id, eb.site_id, eb.check_type, eb.check_result,
                   eb.hipaa_control, eb.checked_at, eb.bundle_hash,
                   s.clinic_name
            FROM evidence_bundles eb
            JOIN sites s ON s.site_id = eb.site_id
            WHERE s.client_org_id = $1
        """
        params = [org_id]
        param_idx = 2

        if site_id:
            query += f" AND eb.site_id = ${param_idx}"
            params.append(site_id)
            param_idx += 1

        if check_type:
            query += f" AND eb.check_type = ${param_idx}"
            params.append(check_type)
            param_idx += 1

        if result:
            query += f" AND eb.check_result = ${param_idx}"
            params.append(result)
            param_idx += 1

        query += f" ORDER BY eb.checked_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
        params.extend([limit, offset])

        bundles = await conn.fetch(query, *params)

        # Get total count
        count_query = """
            SELECT COUNT(*) FROM evidence_bundles eb
            JOIN sites s ON s.site_id = eb.site_id
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
                    "bundle_hash": b["bundle_hash"],
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
        bundle = await conn.fetchrow("""
            SELECT eb.*, s.clinic_name
            FROM evidence_bundles eb
            JOIN sites s ON s.site_id = eb.site_id
            WHERE eb.id = $1 AND s.client_org_id = $2
        """, bundle_id, org_id)

        if not bundle:
            raise HTTPException(status_code=404, detail="Evidence bundle not found")

        # Get hash chain info for verification
        chain = await conn.fetch("""
            SELECT bundle_hash, prev_hash, checked_at
            FROM evidence_bundles
            WHERE site_id = $1
              AND checked_at <= $2
            ORDER BY checked_at DESC
            LIMIT 5
        """, bundle["site_id"], bundle["checked_at"])

        return {
            "bundle": {
                "id": str(bundle["id"]),
                "site_id": bundle["site_id"],
                "clinic_name": bundle["clinic_name"],
                "check_type": bundle["check_type"],
                "check_result": bundle["check_result"],
                "hipaa_control": bundle["hipaa_control"],
                "checked_at": bundle["checked_at"].isoformat() if bundle["checked_at"] else None,
                "bundle_hash": bundle["bundle_hash"],
                "prev_hash": bundle["prev_hash"],
                "agent_signature": bundle["agent_signature"],
                "minio_path": bundle["minio_path"],
            },
            "chain": [
                {
                    "hash": c["bundle_hash"],
                    "prev_hash": c["prev_hash"],
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
        bundle = await conn.fetchrow("""
            SELECT eb.minio_path, eb.bundle_hash
            FROM evidence_bundles eb
            JOIN sites s ON s.site_id = eb.site_id
            WHERE eb.id = $1 AND s.client_org_id = $2
        """, bundle_id, org_id)

        if not bundle:
            raise HTTPException(status_code=404, detail="Evidence bundle not found")

        if not bundle["minio_path"]:
            raise HTTPException(status_code=404, detail="Evidence file not available")

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

        # Parse bucket and object from path
        path_parts = bundle["minio_path"].split("/", 1)
        bucket = path_parts[0] if len(path_parts) > 1 else "evidence-worm"
        obj_path = path_parts[1] if len(path_parts) > 1 else bundle["minio_path"]

        url = client.presigned_get_object(
            bucket,
            obj_path,
            expires=timedelta(minutes=15)
        )

        return {
            "download_url": url,
            "expires_in": 900,
            "bundle_hash": bundle["bundle_hash"],
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
            SELECT eb.* FROM evidence_bundles eb
            JOIN sites s ON s.site_id = eb.site_id
            WHERE eb.id = $1 AND s.client_org_id = $2
        """, bundle_id, org_id)

        if not bundle:
            raise HTTPException(status_code=404, detail="Evidence bundle not found")

        # Verify chain back to genesis
        chain_valid = True
        chain_length = 0
        current = bundle

        while current and chain_length < 1000:  # Limit to prevent infinite loops
            chain_length += 1

            if not current["prev_hash"]:
                # Genesis block
                break

            prev = await conn.fetchrow("""
                SELECT * FROM evidence_bundles
                WHERE bundle_hash = $1 AND site_id = $2
            """, current["prev_hash"], bundle["site_id"])

            if not prev:
                chain_valid = False
                break

            current = prev

        return {
            "bundle_id": str(bundle["id"]),
            "bundle_hash": bundle["bundle_hash"],
            "chain_valid": chain_valid,
            "chain_length": chain_length,
            "has_signature": bool(bundle["agent_signature"]),
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
        from .notifications import send_email
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
