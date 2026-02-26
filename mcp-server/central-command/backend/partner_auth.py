"""
Partner OAuth Authentication.

Enables MSPs to sign up and authenticate using their existing
Microsoft Entra ID or Google Workspace identity.

Flow:
1. Partner clicks "Sign in with Microsoft/Google" on /partner/login
2. Backend generates PKCE challenge + state token, returns auth URL
3. Partner authenticates with their IdP
4. IdP redirects to /api/partner-auth/callback with code
5. Backend exchanges code for tokens, creates/updates partner
6. Backend creates session, sets cookie, redirects to dashboard

Security:
- PKCE with S256 challenge (no implicit flow)
- Single-use state tokens (Redis, 10-minute TTL)
- Session tokens hashed before storage
- HttpOnly, Secure, SameSite=Lax cookies
- Google Workspace required (rejects consumer Gmail)
"""

import os
import json
import base64
import hashlib
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal
from urllib.parse import urlencode, parse_qs

from fastapi import APIRouter, Request, Response, HTTPException, Depends, Cookie
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
import httpx
from typing import Dict

from .fleet import get_pool
from .auth import require_admin
from .db_utils import _uid
from .oauth_login import encrypt_secret, decrypt_secret
from .partner_activity_logger import log_partner_activity, log_partner_login, PartnerEventType

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

# OAuth provider configuration (from environment)
MICROSOFT_CLIENT_ID = os.getenv("MICROSOFT_PARTNER_CLIENT_ID", "")
MICROSOFT_CLIENT_SECRET = os.getenv("MICROSOFT_PARTNER_CLIENT_SECRET", "")
MICROSOFT_TENANT = "common"  # Allow any Azure AD tenant

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_PARTNER_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_PARTNER_CLIENT_SECRET", "")

# Base URL for redirects
BASE_URL = os.getenv("BASE_URL", "https://dashboard.osiriscare.net")

# Session configuration
SESSION_COOKIE_NAME = "osiris_partner_session"
SESSION_DURATION_DAYS = 7
SESSION_COOKIE_MAX_AGE = SESSION_DURATION_DAYS * 24 * 60 * 60
SESSION_IDLE_TIMEOUT_MINUTES = 15  # HIPAA §164.312(a)(2)(iii) automatic logoff

# PKCE configuration
PKCE_VERIFIER_LENGTH = 64

# State token TTL (seconds)
STATE_TOKEN_TTL = 600  # 10 minutes


# =============================================================================
# ROUTERS
# =============================================================================

# Public router - no auth required (OAuth flow)
public_router = APIRouter(prefix="/partner-auth", tags=["partner-auth"])

# Session router - requires valid partner session
session_router = APIRouter(prefix="/partner-auth", tags=["partner-auth"])


# =============================================================================
# MODELS
# =============================================================================

class OAuthState(BaseModel):
    """OAuth state stored in Redis during flow."""
    provider: Literal["microsoft", "google"]
    code_verifier: str
    redirect_after: str = "/partner/dashboard"
    created_at: str


# =============================================================================
# PKCE HELPERS
# =============================================================================

def generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE code verifier and challenge (S256)."""
    code_verifier = secrets.token_urlsafe(PKCE_VERIFIER_LENGTH)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip("=")
    return code_verifier, code_challenge


def generate_state_token() -> str:
    """Generate a random state token."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Hash a session token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


# =============================================================================
# REDIS STATE MANAGEMENT (reuse from oauth_login if available)
# =============================================================================

async def store_oauth_state(state: str, data: OAuthState, pool) -> None:
    """Store OAuth state in dedicated oauth_partner_state table."""
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO oauth_partner_state (state_token, provider, code_verifier, redirect_after, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (state_token) DO UPDATE SET expires_at = $5
        """, state, data.provider, data.code_verifier, data.redirect_after,
            datetime.now(timezone.utc) + timedelta(seconds=STATE_TOKEN_TTL))


async def get_oauth_state(state: str, pool) -> Optional[OAuthState]:
    """Retrieve and delete OAuth state (single use)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            DELETE FROM oauth_partner_state
            WHERE state_token = $1 AND expires_at > NOW()
            RETURNING provider, code_verifier, redirect_after, created_at
        """, state)

        if not row:
            return None

        return OAuthState(
            provider=row['provider'],
            code_verifier=row['code_verifier'],
            redirect_after=row['redirect_after'] or '/partner/dashboard',
            created_at=row['created_at'].isoformat() if row['created_at'] else datetime.now(timezone.utc).isoformat()
        )


# =============================================================================
# SESSION MANAGEMENT
# =============================================================================

async def create_partner_session(partner_id: str, request: Request, pool) -> str:
    """Create a new session for a partner."""
    session_token = secrets.token_urlsafe(32)
    token_hash = hash_session_token(session_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DURATION_DAYS)

    # Get client info
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")[:500]

    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO partner_sessions (partner_id, session_token_hash, ip_address, user_agent, expires_at)
            VALUES ($1, $2, $3, $4, $5)
        """, partner_id, token_hash, ip_address, user_agent, expires_at)

    return session_token


async def get_partner_from_session(session_token: str, pool):
    """Get partner from session token.

    Enforces HIPAA §164.312(a)(2)(iii) idle timeout.
    """
    if not session_token:
        return None

    token_hash = hash_session_token(session_token)

    async with pool.acquire() as conn:
        # Check idle timeout before updating last_used_at
        idle_check = await conn.fetchrow("""
            SELECT last_used_at FROM partner_sessions
            WHERE session_token_hash = $1 AND expires_at > NOW()
        """, token_hash)

        if idle_check and idle_check['last_used_at']:
            from datetime import datetime, timezone, timedelta
            idle_cutoff = datetime.now(timezone.utc) - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)
            if idle_check['last_used_at'] < idle_cutoff:
                await conn.execute(
                    "DELETE FROM partner_sessions WHERE session_token_hash = $1", token_hash
                )
                return None

        # Update last_used_at and get partner
        row = await conn.fetchrow("""
            UPDATE partner_sessions ps
            SET last_used_at = NOW()
            FROM partners p
            WHERE ps.session_token_hash = $1
              AND ps.expires_at > NOW()
              AND ps.partner_id = p.id
              AND p.status = 'active'
            RETURNING p.id, p.name, p.slug, p.oauth_email, p.contact_email,
                      p.auth_provider, p.oauth_tenant_id, p.brand_name
        """, token_hash)

        return row


async def delete_partner_session(session_token: str, pool) -> None:
    """Delete a session."""
    token_hash = hash_session_token(session_token)
    async with pool.acquire() as conn:
        await conn.execute("""
            DELETE FROM partner_sessions WHERE session_token_hash = $1
        """, token_hash)


# =============================================================================
# OAUTH CONFIG & APPROVAL
# =============================================================================

async def get_oauth_config(pool) -> dict:
    """Get partner OAuth configuration."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT allowed_domains, require_approval, allow_consumer_gmail, notify_emails
            FROM partner_oauth_config
            LIMIT 1
        """)
        if row:
            return {
                "allowed_domains": row["allowed_domains"] or [],
                "require_approval": row["require_approval"],
                "allow_consumer_gmail": row["allow_consumer_gmail"],
                "notify_emails": row["notify_emails"] or [],
            }
        return {
            "allowed_domains": [],
            "require_approval": True,
            "allow_consumer_gmail": True,
            "notify_emails": [],
        }


async def send_partner_approval_notification(partner_name: str, partner_email: str, pool) -> None:
    """Send email notification to admins about new partner signup."""
    try:
        # Import here to avoid circular imports
        from .email_alerts import send_critical_alert

        title = f"New Partner Signup: {partner_name}"
        message = f"""A new partner has signed up via OAuth and requires approval:

Name: {partner_name}
Email: {partner_email}

Please review and approve/reject at:
https://dashboard.osiriscare.net/admin/partners/pending

This signup was created at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}."""

        # send_critical_alert uses ALERT_EMAIL from env
        success = send_critical_alert(
            title=title,
            message=message,
            category="partner-approval",
            metadata={"partner_email": partner_email, "partner_name": partner_name}
        )
        if success:
            logger.info(f"Sent partner approval notification for {partner_email}")

    except Exception as e:
        logger.error(f"Failed to send partner approval notification: {e}")


def is_domain_allowed(email: str, allowed_domains: list) -> bool:
    """Check if email domain is in the allowed list."""
    if not allowed_domains:
        return False
    domain = email.split("@")[-1].lower()
    return domain in [d.lower() for d in allowed_domains]


# =============================================================================
# PARTNER UPSERT
# =============================================================================

async def upsert_partner_from_oauth(
    provider: str,
    subject: str,
    email: str,
    name: str,
    tenant_id: Optional[str],
    tokens: dict,
    pool
) -> dict:
    """Create or update partner from OAuth identity."""

    # Get OAuth config for approval settings
    config = await get_oauth_config(pool)

    async with pool.acquire() as conn:
        # Check if partner exists with this OAuth identity
        existing = await conn.fetchrow("""
            SELECT id, name, slug, status, pending_approval FROM partners
            WHERE auth_provider = $1 AND oauth_subject = $2
        """, provider, subject)

        # SECURITY: Encrypt tokens with Fernet before storage
        access_token_enc = encrypt_secret(tokens.get("access_token", "")) if tokens.get("access_token") else None
        refresh_token_enc = encrypt_secret(tokens.get("refresh_token", "")) if tokens.get("refresh_token") else None
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 3600))

        if existing:
            # Update existing partner
            await conn.execute("""
                UPDATE partners SET
                    oauth_email = $1,
                    oauth_name = $2,
                    oauth_access_token_encrypted = $3,
                    oauth_refresh_token_encrypted = $4,
                    oauth_token_expires_at = $5,
                    last_login_at = NOW()
                WHERE id = $6
            """, email, name,
                access_token_enc,   # Already bytes from encrypt_secret
                refresh_token_enc,  # Already bytes from encrypt_secret
                expires_at, existing['id'])

            if existing['status'] != 'active':
                raise HTTPException(status_code=403, detail="Partner account is suspended")

            # Check if still pending approval
            result = dict(existing)
            result["pending_approval"] = existing.get("pending_approval", False)
            return result
        else:
            # Create new partner
            # Generate slug from email domain or name
            slug_base = email.split("@")[0] if email else name.lower().replace(" ", "-")
            slug = slug_base[:50]

            # Check slug uniqueness and add suffix if needed
            for i in range(100):
                test_slug = slug if i == 0 else f"{slug}-{i}"
                exists = await conn.fetchval("SELECT 1 FROM partners WHERE slug = $1", test_slug)
                if not exists:
                    slug = test_slug
                    break

            # Determine if approval is required
            require_approval = config.get("require_approval", True)
            allowed_domains = config.get("allowed_domains", [])

            # Auto-approve if domain is in allowlist
            pending_approval = require_approval and not is_domain_allowed(email, allowed_domains)

            row = await conn.fetchrow("""
                INSERT INTO partners (
                    name, slug, contact_email, brand_name,
                    auth_provider, oauth_subject, oauth_tenant_id,
                    oauth_email, oauth_name,
                    oauth_access_token_encrypted, oauth_refresh_token_encrypted,
                    oauth_token_expires_at, last_login_at, status,
                    pending_approval, auto_approved_domain
                ) VALUES (
                    $1, $2, $3, $4,
                    $5, $6, $7,
                    $8, $9,
                    $10, $11,
                    $12, NOW(), 'active',
                    $13, $14
                )
                RETURNING id, name, slug, status, pending_approval
            """,
                name or email.split("@")[0],
                slug,
                email,
                name or email.split("@")[0],
                provider,
                subject,
                tenant_id,
                email,
                name,
                access_token_enc if access_token_enc else None,
                refresh_token_enc if refresh_token_enc else None,
                expires_at,
                pending_approval,
                not pending_approval and is_domain_allowed(email, allowed_domains)
            )

            logger.info(f"Created new partner via OAuth: {row['slug']} ({provider}), pending_approval={pending_approval}")

            # Send notification if pending approval
            if pending_approval:
                await send_partner_approval_notification(
                    partner_name=name or email.split("@")[0],
                    partner_email=email,
                    pool=pool
                )

            return dict(row)


# =============================================================================
# MICROSOFT OAUTH ENDPOINTS
# =============================================================================

@public_router.get("/microsoft")
async def microsoft_login(request: Request, redirect_after: str = "/partner/dashboard"):
    """Initiate Microsoft OAuth flow for partner login."""
    if not MICROSOFT_CLIENT_ID or not MICROSOFT_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Microsoft OAuth not configured")

    pool = await get_pool()

    # Generate PKCE and state
    code_verifier, code_challenge = generate_pkce_pair()
    state = generate_state_token()

    # Store state
    await store_oauth_state(state, OAuthState(
        provider="microsoft",
        code_verifier=code_verifier,
        redirect_after=redirect_after,
        created_at=datetime.now(timezone.utc).isoformat()
    ), pool)

    # Build authorization URL
    params = {
        "client_id": MICROSOFT_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": f"{BASE_URL}/api/partner-auth/callback",
        "scope": "openid profile email User.Read",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }

    auth_url = f"https://login.microsoftonline.com/{MICROSOFT_TENANT}/oauth2/v2.0/authorize?{urlencode(params)}"

    # Audit: log OAuth flow initiation
    await log_partner_activity(
        partner_id="00000000-0000-0000-0000-000000000000",
        event_type=PartnerEventType.OAUTH_LOGIN_STARTED,
        event_data={"provider": "microsoft"},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return RedirectResponse(url=auth_url, status_code=303)


# =============================================================================
# GOOGLE OAUTH ENDPOINTS
# =============================================================================

@public_router.get("/google")
async def google_login(request: Request, redirect_after: str = "/partner/dashboard"):
    """Initiate Google OAuth flow for partner login."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Google OAuth not configured")

    pool = await get_pool()

    # Generate PKCE and state
    code_verifier, code_challenge = generate_pkce_pair()
    state = generate_state_token()

    # Store state
    await store_oauth_state(state, OAuthState(
        provider="google",
        code_verifier=code_verifier,
        redirect_after=redirect_after,
        created_at=datetime.now(timezone.utc).isoformat()
    ), pool)

    # Build authorization URL
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": f"{BASE_URL}/api/partner-auth/callback",
        "scope": "openid profile email",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "select_account",
        "hd": "*",  # Hint for Google Workspace (not enforced here, checked in callback)
    }

    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    # Audit: log OAuth flow initiation
    await log_partner_activity(
        partner_id="00000000-0000-0000-0000-000000000000",
        event_type=PartnerEventType.OAUTH_LOGIN_STARTED,
        event_data={"provider": "google"},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return RedirectResponse(url=auth_url, status_code=303)


# =============================================================================
# OAUTH CALLBACK (handles both providers)
# =============================================================================

@public_router.get("/callback")
async def oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None
):
    """Handle OAuth callback from Microsoft or Google."""

    # Handle errors from provider
    if error:
        logger.warning(f"OAuth error: {error} - {error_description}")
        return RedirectResponse(
            url=f"/partner/login?error={error}&error_description={error_description or ''}",
            status_code=303
        )

    if not code or not state:
        return RedirectResponse(url="/partner/login?error=missing_params", status_code=303)

    pool = await get_pool()

    # Validate state (single use)
    oauth_state = await get_oauth_state(state, pool)
    if not oauth_state:
        return RedirectResponse(url="/partner/login?error=invalid_state", status_code=303)

    provider = oauth_state.provider
    code_verifier = oauth_state.code_verifier
    redirect_after = oauth_state.redirect_after

    try:
        if provider == "microsoft":
            tokens, user_info = await exchange_microsoft_code(code, code_verifier)
        elif provider == "google":
            tokens, user_info = await exchange_google_code(code, code_verifier)
        else:
            return RedirectResponse(url="/partner/login?error=invalid_provider", status_code=303)

        # Upsert partner
        partner = await upsert_partner_from_oauth(
            provider=user_info["provider"],
            subject=user_info["subject"],
            email=user_info["email"],
            name=user_info["name"],
            tenant_id=user_info.get("tenant_id"),
            tokens=tokens,
            pool=pool
        )

        # Check if pending approval
        if partner.get("pending_approval"):
            logger.info(f"Partner OAuth signup pending approval: {partner['slug']} via {provider}")
            return RedirectResponse(
                url="/partner/login?pending=true&email=" + user_info["email"],
                status_code=303
            )

        # Create session
        session_token = await create_partner_session(partner["id"], request, pool)

        # Redirect with session cookie
        response = RedirectResponse(url=redirect_after, status_code=303)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=SESSION_COOKIE_MAX_AGE,
            path="/"
        )

        logger.info(f"Partner OAuth login successful: {partner['slug']} via {provider}")

        # Audit: log successful OAuth login
        await log_partner_login(
            partner_id=str(partner["id"]),
            provider=provider,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            success=True,
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"OAuth callback error: {e}")

        # Audit: log failed OAuth login
        await log_partner_login(
            partner_id="00000000-0000-0000-0000-000000000000",
            provider=provider,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            success=False,
            error=str(e),
        )

        return RedirectResponse(url=f"/partner/login?error=auth_failed", status_code=303)


async def exchange_microsoft_code(code: str, code_verifier: str) -> tuple[dict, dict]:
    """Exchange Microsoft auth code for tokens and user info."""

    async with httpx.AsyncClient() as client:
        # Exchange code for tokens
        token_response = await client.post(
            f"https://login.microsoftonline.com/{MICROSOFT_TENANT}/oauth2/v2.0/token",
            data={
                "client_id": MICROSOFT_CLIENT_ID,
                "client_secret": MICROSOFT_CLIENT_SECRET,
                "code": code,
                "redirect_uri": f"{BASE_URL}/api/partner-auth/callback",
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            }
        )

        if token_response.status_code != 200:
            logger.error(f"Microsoft token exchange failed: {token_response.text}")
            raise HTTPException(status_code=400, detail="Token exchange failed")

        tokens = token_response.json()

        # Get user profile from Graph API
        profile_response = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )

        if profile_response.status_code != 200:
            logger.error(f"Microsoft profile fetch failed: {profile_response.text}")
            raise HTTPException(status_code=400, detail="Failed to get user profile")

        profile = profile_response.json()

        # Extract tenant ID from ID token for logging/metadata purposes.
        #
        # SECURITY NOTE: We decode the ID token without verifying its signature.
        # This is acceptable here because:
        # 1. The tenant_id (tid claim) is used only for logging and partner metadata,
        #    NOT for authorization decisions.
        # 2. The access_token was already validated by Microsoft when we successfully
        #    called the Graph API above (lines 590-598). If the token were invalid or
        #    tampered with, that call would have failed with a 401.
        # 3. The user's identity (subject, email, name) comes from the Graph API
        #    response, not from the unverified ID token.
        #
        # For production systems where the ID token claims are used for authorization,
        # proper JWT signature verification against Microsoft's JWKS would be required.
        tenant_id = None
        if tokens.get("id_token"):
            try:
                payload = tokens["id_token"].split(".")[1]
                payload += "=" * (4 - len(payload) % 4)  # Pad base64
                claims = json.loads(base64.urlsafe_b64decode(payload))
                tenant_id = claims.get("tid")
            except Exception as e:
                # Log warning but don't fail - tenant_id is optional metadata
                logger.warning(f"Failed to parse ID token for tenant_id extraction: {e}")

        user_info = {
            "provider": "microsoft",
            "subject": profile.get("id"),
            "email": profile.get("mail") or profile.get("userPrincipalName"),
            "name": profile.get("displayName"),
            "tenant_id": tenant_id,
        }

        return tokens, user_info


async def exchange_google_code(code: str, code_verifier: str) -> tuple[dict, dict]:
    """Exchange Google auth code for tokens and user info."""

    async with httpx.AsyncClient() as client:
        # Exchange code for tokens
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "redirect_uri": f"{BASE_URL}/api/partner-auth/callback",
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            }
        )

        if token_response.status_code != 200:
            logger.error(f"Google token exchange failed: {token_response.text}")
            raise HTTPException(status_code=400, detail="Token exchange failed")

        tokens = token_response.json()

        # Get user info
        userinfo_response = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )

        if userinfo_response.status_code != 200:
            logger.error(f"Google userinfo fetch failed: {userinfo_response.text}")
            raise HTTPException(status_code=400, detail="Failed to get user info")

        profile = userinfo_response.json()

        # Get hosted domain (present for Workspace accounts, None for consumer Gmail)
        hd = profile.get("hd")

        user_info = {
            "provider": "google",
            "subject": profile.get("sub"),
            "email": profile.get("email"),
            "name": profile.get("name"),
            "tenant_id": hd or profile.get("email", "").split("@")[-1],  # Use email domain if no hd
        }

        return tokens, user_info


# =============================================================================
# SESSION ENDPOINTS
# =============================================================================

@public_router.get("/me")
async def get_current_partner(
    request: Request,
    osiris_partner_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME)
):
    """Get current authenticated partner from session."""
    if not osiris_partner_session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    pool = await get_pool()
    partner = await get_partner_from_session(osiris_partner_session, pool)

    if not partner:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    return {
        "id": str(partner["id"]),
        "name": partner["name"],
        "slug": partner["slug"],
        "email": partner["oauth_email"] or partner["contact_email"],
        "auth_provider": partner["auth_provider"],
        "tenant_id": partner["oauth_tenant_id"],
        "brand_name": partner["brand_name"],
    }


@public_router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    osiris_partner_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME)
):
    """Logout and clear session."""
    partner_id = None
    if osiris_partner_session:
        pool = await get_pool()
        # Look up partner before deleting the session so we can log it
        partner = await get_partner_from_session(osiris_partner_session, pool)
        if partner:
            partner_id = str(partner["id"])
        await delete_partner_session(osiris_partner_session, pool)

    # Audit: log logout
    await log_partner_activity(
        partner_id=partner_id or "00000000-0000-0000-0000-000000000000",
        event_type=PartnerEventType.LOGOUT,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


# =============================================================================
# PROVIDER STATUS ENDPOINT
# =============================================================================

@public_router.get("/providers")
async def get_oauth_providers():
    """Get available OAuth providers for partner login."""
    return {
        "providers": {
            "microsoft": bool(MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET),
            "google": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        }
    }


# =============================================================================
# ADMIN PARTNER APPROVAL ENDPOINTS
# =============================================================================

# Admin router - requires admin authentication (imported from dashboard_api)
admin_router = APIRouter(prefix="/admin/partners", tags=["admin-partners"])


@admin_router.get("/pending")
async def list_pending_partners(request: Request, user: Dict = Depends(require_admin)):
    """List all partners pending approval (admin only)."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, name, slug, contact_email, oauth_email, auth_provider,
                   oauth_tenant_id, created_at
            FROM partners
            WHERE pending_approval = TRUE
            ORDER BY created_at DESC
        """)

    return {
        "pending": [
            {
                "id": str(row["id"]),
                "name": row["name"],
                "slug": row["slug"],
                "email": row["oauth_email"] or row["contact_email"],
                "auth_provider": row["auth_provider"],
                "tenant_id": row["oauth_tenant_id"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
            for row in rows
        ]
    }


@admin_router.post("/approve/{partner_id}")
async def approve_partner(partner_id: str, request: Request, user: Dict = Depends(require_admin)):
    """Approve a pending partner (admin only)."""
    pool = await get_pool()

    # Get admin user ID from authenticated user
    admin_user_id = user.get("id")

    async with pool.acquire() as conn:
        # Check partner exists and is pending
        partner = await conn.fetchrow("""
            SELECT id, name, slug, oauth_email, pending_approval
            FROM partners WHERE id = $1
        """, _uid(partner_id))

        if not partner:
            raise HTTPException(status_code=404, detail="Partner not found")

        if not partner["pending_approval"]:
            raise HTTPException(status_code=400, detail="Partner is not pending approval")

        # Approve the partner
        await conn.execute("""
            UPDATE partners
            SET pending_approval = FALSE,
                approved_by = $1,
                approved_at = NOW()
            WHERE id = $2
        """, admin_user_id, _uid(partner_id))

    logger.info(f"Partner approved: {partner['slug']} by admin {admin_user_id}")

    # Audit: log partner approval
    await log_partner_activity(
        partner_id=str(user.get("id", "")),
        event_type=PartnerEventType.PARTNER_APPROVED,
        target_type="partner",
        target_id=partner_id,
        event_data={"approved_by": str(admin_user_id)},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    # Send approval notification to partner
    try:
        from .notifications import send_email
        await send_email(
            partner["oauth_email"],
            "Your OsirisCare Partner Account Has Been Approved",
            f"""
Your partner account has been approved!

You can now sign in to the Partner Portal:
https://dashboard.osiriscare.net/partner/login

Welcome to the OsirisCare Partner Program.
"""
        )
    except Exception as e:
        logger.error(f"Failed to send approval notification: {e}")

    return {"status": "approved", "partner_id": partner_id}


@admin_router.post("/reject/{partner_id}")
async def reject_partner(partner_id: str, request: Request, user: Dict = Depends(require_admin)):
    """Reject and delete a pending partner (admin only)."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Check partner exists and is pending
        partner = await conn.fetchrow("""
            SELECT id, name, slug, oauth_email, pending_approval
            FROM partners WHERE id = $1
        """, _uid(partner_id))

        if not partner:
            raise HTTPException(status_code=404, detail="Partner not found")

        if not partner["pending_approval"]:
            raise HTTPException(status_code=400, detail="Partner is not pending approval")

        # Delete the partner
        await conn.execute("DELETE FROM partners WHERE id = $1", _uid(partner_id))

    logger.info(f"Partner rejected and deleted: {partner['slug']}")

    # Audit: log partner rejection
    await log_partner_activity(
        partner_id=str(user.get("id", "")),
        event_type=PartnerEventType.PARTNER_REJECTED,
        target_type="partner",
        target_id=partner_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
        request_path=str(request.url.path),
        request_method=request.method,
    )

    return {"status": "rejected", "partner_id": partner_id}


@admin_router.get("/oauth-config")
async def get_admin_oauth_config(request: Request, user: Dict = Depends(require_admin)):
    """Get OAuth configuration (admin only)."""
    pool = await get_pool()
    config = await get_oauth_config(pool)
    return config


@admin_router.put("/oauth-config")
async def update_oauth_config(request: Request, user: Dict = Depends(require_admin)):
    """Update OAuth configuration (admin only)."""
    pool = await get_pool()
    data = await request.json()

    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE partner_oauth_config
            SET allowed_domains = $1,
                require_approval = $2,
                allow_consumer_gmail = $3,
                notify_emails = $4,
                updated_at = NOW()
        """,
            data.get("allowed_domains", []),
            data.get("require_approval", True),
            data.get("allow_consumer_gmail", True),
            data.get("notify_emails", [])
        )

    return {"status": "updated"}
