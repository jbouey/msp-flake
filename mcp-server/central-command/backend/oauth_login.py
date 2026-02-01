"""
OAuth Login Module for Google and Microsoft authentication.

Provides OAuth 2.0 + PKCE authentication for the Central Command dashboard.
Supports:
- Google (Gmail / Workspace accounts)
- Microsoft (Azure AD / Microsoft 365 accounts)

Security features:
- PKCE (Proof Key for Code Exchange) with S256 challenge
- Single-use state tokens via Redis
- Domain whitelist validation
- Admin approval for new users
- Comprehensive audit logging
"""

import base64
import hashlib
import secrets
import logging
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from cryptography.fernet import Fernet

from .auth import (
    generate_session_token,
    hash_token,
    require_auth,
    require_admin,
    SESSION_DURATION_HOURS,
)

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

# OAuth endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

MICROSOFT_AUTH_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
MICROSOFT_TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
MICROSOFT_USERINFO_URL = "https://graph.microsoft.com/v1.0/me"

# OAuth scopes (minimal for login)
GOOGLE_SCOPES = ["openid", "email", "profile"]
MICROSOFT_SCOPES = ["openid", "email", "profile", "User.Read"]

# PKCE configuration
PKCE_CODE_VERIFIER_LENGTH = 64  # 64 bytes = 512 bits

# State token configuration
STATE_TTL_SECONDS = 600  # 10 minutes

# Frontend URLs
FRONTEND_BASE_URL = os.getenv("FRONTEND_URL", "https://dashboard.osiriscare.net")


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class PKCEChallenge:
    """PKCE code verifier and challenge pair."""
    code_verifier: str
    code_challenge: str
    code_challenge_method: str = "S256"


@dataclass
class OAuthUserInfo:
    """User information from OAuth provider."""
    provider: str
    provider_user_id: str
    email: str
    name: Optional[str] = None
    picture_url: Optional[str] = None


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def generate_pkce_challenge() -> PKCEChallenge:
    """Generate PKCE code verifier and S256 challenge."""
    code_verifier = secrets.token_urlsafe(PKCE_CODE_VERIFIER_LENGTH)
    verifier_bytes = code_verifier.encode('ascii')
    sha256_digest = hashlib.sha256(verifier_bytes).digest()
    code_challenge = base64.urlsafe_b64encode(sha256_digest).rstrip(b'=').decode('ascii')

    return PKCEChallenge(
        code_verifier=code_verifier,
        code_challenge=code_challenge,
        code_challenge_method="S256"
    )


def encrypt_secret(plaintext: str) -> bytes:
    """Encrypt a secret for database storage.

    Requires either OAUTH_ENCRYPTION_KEY or SESSION_TOKEN_SECRET environment variable.
    """
    key = os.getenv("OAUTH_ENCRYPTION_KEY")
    if not key:
        session_secret = os.getenv("SESSION_TOKEN_SECRET")
        if not session_secret:
            raise RuntimeError(
                "OAUTH_ENCRYPTION_KEY or SESSION_TOKEN_SECRET environment variable must be set. "
                "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        key = base64.urlsafe_b64encode(hashlib.sha256(session_secret.encode()).digest())
    else:
        key = key.encode() if isinstance(key, str) else key

    fernet = Fernet(key)
    return fernet.encrypt(plaintext.encode())


def decrypt_secret(ciphertext: bytes) -> str:
    """Decrypt a secret from database storage.

    Requires either OAUTH_ENCRYPTION_KEY or SESSION_TOKEN_SECRET environment variable.
    """
    key = os.getenv("OAUTH_ENCRYPTION_KEY")
    if not key:
        session_secret = os.getenv("SESSION_TOKEN_SECRET")
        if not session_secret:
            raise RuntimeError(
                "OAUTH_ENCRYPTION_KEY or SESSION_TOKEN_SECRET environment variable must be set. "
                "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        key = base64.urlsafe_b64encode(hashlib.sha256(session_secret.encode()).digest())
    else:
        key = key.encode() if isinstance(key, str) else key

    fernet = Fernet(key)
    return fernet.decrypt(ciphertext).decode()


async def get_db_session():
    """Get database session."""
    try:
        from main import async_session
    except ImportError:
        from server import async_session
    return async_session


async def get_redis_client():
    """Get Redis client for state management."""
    try:
        from main import redis_client
        return redis_client
    except ImportError:
        try:
            from server import redis_client
            return redis_client
        except (ImportError, AttributeError):
            # Fallback: create a simple in-memory state store
            logger.warning("Redis not available, using in-memory state store (not recommended for production)")
            return None


# =============================================================================
# STATE MANAGEMENT (Redis-backed)
# =============================================================================

class OAuthLoginStateManager:
    """Manages OAuth state tokens for login flow."""

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self._memory_store: Dict[str, str] = {}  # Fallback for no Redis

    def _make_key(self, state: str) -> str:
        return f"oauth_login_state:{state}"

    async def generate(
        self,
        provider: str,
        code_verifier: str,
        return_url: Optional[str] = None,
        link_to_user_id: Optional[str] = None,
    ) -> str:
        """Generate a new state token."""
        state = secrets.token_urlsafe(32)

        data = json.dumps({
            "provider": provider,
            "code_verifier": code_verifier,
            "return_url": return_url,
            "link_to_user_id": link_to_user_id,
            "created_at": datetime.utcnow().isoformat(),
        })

        if self.redis:
            await self.redis.setex(self._make_key(state), STATE_TTL_SECONDS, data)
        else:
            self._memory_store[state] = data

        return state

    async def validate_and_consume(self, state: str) -> Dict[str, Any]:
        """Validate and consume state token (single-use)."""
        key = self._make_key(state)

        if self.redis:
            data_json = await self.redis.getdel(key)
        else:
            data_json = self._memory_store.pop(state, None)

        if not data_json:
            raise HTTPException(
                status_code=400,
                detail="Invalid or expired state token"
            )

        return json.loads(data_json)


# =============================================================================
# OAUTH PROVIDER CLIENTS
# =============================================================================

async def exchange_code_for_tokens(
    provider: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Exchange authorization code for tokens."""

    if provider == "google":
        token_url = GOOGLE_TOKEN_URL
    elif provider == "microsoft":
        tenant = tenant_id or "common"
        token_url = MICROSOFT_TOKEN_URL_TEMPLATE.format(tenant=tenant)
    else:
        raise ValueError(f"Unknown provider: {provider}")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_url,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )

        if response.status_code != 200:
            logger.error(f"Token exchange failed: {response.status_code} {response.text}")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to exchange authorization code: {response.text}"
            )

        return response.json()


async def get_user_info(
    provider: str,
    access_token: str,
) -> OAuthUserInfo:
    """Get user information from OAuth provider."""

    if provider == "google":
        userinfo_url = GOOGLE_USERINFO_URL
    elif provider == "microsoft":
        userinfo_url = MICROSOFT_USERINFO_URL
    else:
        raise ValueError(f"Unknown provider: {provider}")

    async with httpx.AsyncClient() as client:
        response = await client.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
        )

        if response.status_code != 200:
            logger.error(f"User info request failed: {response.status_code}")
            raise HTTPException(
                status_code=400,
                detail="Failed to get user information from provider"
            )

        data = response.json()

    if provider == "google":
        return OAuthUserInfo(
            provider="google",
            provider_user_id=data.get("sub"),
            email=data.get("email"),
            name=data.get("name"),
            picture_url=data.get("picture"),
        )
    elif provider == "microsoft":
        return OAuthUserInfo(
            provider="microsoft",
            provider_user_id=data.get("id"),
            email=data.get("mail") or data.get("userPrincipalName"),
            name=data.get("displayName"),
            picture_url=None,  # Microsoft requires separate call for photo
        )


# =============================================================================
# DATABASE OPERATIONS
# =============================================================================

async def get_oauth_config(db: AsyncSession, provider: str) -> Optional[Dict[str, Any]]:
    """Get OAuth provider configuration."""
    result = await db.execute(
        text("""
            SELECT client_id, client_secret_encrypted, tenant_id, enabled,
                   allow_registration, default_role, require_admin_approval, allowed_domains
            FROM oauth_config
            WHERE provider = :provider
        """),
        {"provider": provider}
    )
    row = result.fetchone()

    if not row:
        return None

    client_id, client_secret_enc, tenant_id, enabled, allow_reg, default_role, require_approval, allowed_domains = row

    # Don't decrypt if not configured
    client_secret = None
    if client_secret_enc and client_secret_enc != b'\x00':
        try:
            client_secret = decrypt_secret(client_secret_enc)
        except Exception:
            pass

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "tenant_id": tenant_id,
        "enabled": enabled,
        "allow_registration": allow_reg,
        "default_role": default_role,
        "require_admin_approval": require_approval,
        "allowed_domains": allowed_domains or [],
    }


async def find_user_by_oauth_identity(
    db: AsyncSession,
    provider: str,
    provider_user_id: str,
) -> Optional[Dict[str, Any]]:
    """Find user by OAuth identity."""
    result = await db.execute(
        text("""
            SELECT u.id, u.username, u.display_name, u.role, u.status, u.pending_approval
            FROM admin_oauth_identities oi
            JOIN admin_users u ON u.id = oi.user_id
            WHERE oi.provider = :provider AND oi.provider_user_id = :provider_user_id
        """),
        {"provider": provider, "provider_user_id": provider_user_id}
    )
    row = result.fetchone()

    if not row:
        return None

    return {
        "id": str(row[0]),
        "username": row[1],
        "display_name": row[2],
        "role": row[3],
        "status": row[4],
        "pending_approval": row[5],
    }


async def find_user_by_email(db: AsyncSession, email: str) -> Optional[Dict[str, Any]]:
    """Find user by email address."""
    result = await db.execute(
        text("""
            SELECT id, username, display_name, role, status, pending_approval
            FROM admin_users
            WHERE email = :email
        """),
        {"email": email}
    )
    row = result.fetchone()

    if not row:
        return None

    return {
        "id": str(row[0]),
        "username": row[1],
        "display_name": row[2],
        "role": row[3],
        "status": row[4],
        "pending_approval": row[5],
    }


async def create_oauth_user(
    db: AsyncSession,
    user_info: OAuthUserInfo,
    role: str,
    pending_approval: bool,
) -> str:
    """Create a new user from OAuth information."""
    # Generate username from email
    base_username = user_info.email.split("@")[0].lower()
    username = base_username

    # Handle duplicates
    counter = 1
    while True:
        result = await db.execute(
            text("SELECT 1 FROM admin_users WHERE username = :username"),
            {"username": username}
        )
        if not result.fetchone():
            break
        username = f"{base_username}{counter}"
        counter += 1

    # Create user
    result = await db.execute(
        text("""
            INSERT INTO admin_users (username, email, display_name, role, status, pending_approval)
            VALUES (:username, :email, :display_name, :role, :status, :pending_approval)
            RETURNING id
        """),
        {
            "username": username,
            "email": user_info.email,
            "display_name": user_info.name or username,
            "role": role,
            "status": "active" if not pending_approval else "active",
            "pending_approval": pending_approval,
        }
    )
    user_id = str(result.fetchone()[0])

    # Create OAuth identity link
    await db.execute(
        text("""
            INSERT INTO admin_oauth_identities (user_id, provider, provider_user_id, provider_email, provider_name, provider_picture_url)
            VALUES (:user_id, :provider, :provider_user_id, :email, :name, :picture)
        """),
        {
            "user_id": user_id,
            "provider": user_info.provider,
            "provider_user_id": user_info.provider_user_id,
            "email": user_info.email,
            "name": user_info.name,
            "picture": user_info.picture_url,
        }
    )

    await db.commit()
    return user_id


async def link_oauth_identity(
    db: AsyncSession,
    user_id: str,
    user_info: OAuthUserInfo,
) -> None:
    """Link OAuth identity to existing user."""
    await db.execute(
        text("""
            INSERT INTO admin_oauth_identities (user_id, provider, provider_user_id, provider_email, provider_name, provider_picture_url)
            VALUES (:user_id, :provider, :provider_user_id, :email, :name, :picture)
            ON CONFLICT (user_id, provider) DO UPDATE SET
                provider_user_id = EXCLUDED.provider_user_id,
                provider_email = EXCLUDED.provider_email,
                provider_name = EXCLUDED.provider_name,
                provider_picture_url = EXCLUDED.provider_picture_url,
                last_login_at = NOW()
        """),
        {
            "user_id": user_id,
            "provider": user_info.provider,
            "provider_user_id": user_info.provider_user_id,
            "email": user_info.email,
            "name": user_info.name,
            "picture": user_info.picture_url,
        }
    )
    await db.commit()


async def update_oauth_last_login(db: AsyncSession, provider: str, provider_user_id: str) -> None:
    """Update last login timestamp for OAuth identity."""
    await db.execute(
        text("""
            UPDATE admin_oauth_identities
            SET last_login_at = NOW()
            WHERE provider = :provider AND provider_user_id = :provider_user_id
        """),
        {"provider": provider, "provider_user_id": provider_user_id}
    )
    await db.commit()


async def create_session(
    db: AsyncSession,
    user_id: str,
    ip_address: Optional[str],
    user_agent: Optional[str],
) -> str:
    """Create a session for the user."""
    session_token = generate_session_token()
    token_hash = hash_token(session_token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_DURATION_HOURS)

    await db.execute(
        text("""
            INSERT INTO admin_sessions (user_id, token_hash, ip_address, user_agent, expires_at)
            VALUES (:user_id, :token_hash, :ip, :ua, :expires)
        """),
        {
            "user_id": user_id,
            "token_hash": token_hash,
            "ip": ip_address,
            "ua": user_agent,
            "expires": expires_at,
        }
    )

    # Update last login
    await db.execute(
        text("UPDATE admin_users SET last_login = :now WHERE id = :id"),
        {"now": datetime.now(timezone.utc), "id": user_id}
    )

    await db.commit()
    return session_token


async def log_oauth_audit(
    db: AsyncSession,
    user_id: Optional[str],
    username: str,
    action: str,
    details: Optional[Dict],
    ip_address: Optional[str],
) -> None:
    """Log an OAuth-related audit event."""
    await db.execute(
        text("""
            INSERT INTO admin_audit_log (user_id, username, action, target, details, ip_address)
            VALUES (:user_id, :username, :action, 'oauth', :details, :ip)
        """),
        {
            "user_id": user_id,
            "username": username,
            "action": action,
            "details": json.dumps(details) if details else None,
            "ip": ip_address,
        }
    )
    await db.commit()


# =============================================================================
# API ROUTER - PUBLIC ENDPOINTS
# =============================================================================

public_router = APIRouter(tags=["OAuth Login"])


@public_router.get("/oauth/config")
async def get_enabled_providers():
    """Get list of enabled OAuth providers (public, for login page)."""
    async_session = await get_db_session()

    async with async_session() as db:
        result = await db.execute(
            text("SELECT provider, enabled FROM oauth_config WHERE enabled = TRUE")
        )
        rows = result.fetchall()

    return {
        "providers": {
            "google": any(r[0] == "google" for r in rows),
            "microsoft": any(r[0] == "microsoft" for r in rows),
        }
    }


@public_router.get("/oauth/{provider}/authorize")
async def oauth_authorize(
    provider: str,
    request: Request,
    return_url: Optional[str] = Query(None),
):
    """
    Start OAuth authorization flow.

    Returns auth URL for frontend to redirect to.
    """
    if provider not in ("google", "microsoft"):
        raise HTTPException(status_code=400, detail="Invalid provider")

    async_session = await get_db_session()

    async with async_session() as db:
        config = await get_oauth_config(db, provider)

    if not config or not config["enabled"]:
        raise HTTPException(status_code=400, detail=f"{provider} login is not enabled")

    if not config["client_id"] or config["client_id"] == "not-configured":
        raise HTTPException(status_code=500, detail=f"{provider} OAuth is not configured")

    # Generate PKCE challenge
    pkce = generate_pkce_challenge()

    # Generate state token
    redis = await get_redis_client()
    state_mgr = OAuthLoginStateManager(redis)
    state = await state_mgr.generate(
        provider=provider,
        code_verifier=pkce.code_verifier,
        return_url=return_url,
    )

    # Build redirect URI
    redirect_uri = f"{FRONTEND_BASE_URL}/api/auth/oauth/callback"

    # Build authorization URL
    if provider == "google":
        auth_url = GOOGLE_AUTH_URL
        params = {
            "client_id": config["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "state": state,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "select_account",
        }
    else:  # microsoft
        tenant = config.get("tenant_id") or "common"
        auth_url = MICROSOFT_AUTH_URL_TEMPLATE.format(tenant=tenant)
        params = {
            "client_id": config["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(MICROSOFT_SCOPES),
            "state": state,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }

    full_auth_url = f"{auth_url}?{urlencode(params)}"

    return {"auth_url": full_auth_url, "state": state}


@public_router.get("/oauth/callback")
async def oauth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
):
    """
    Handle OAuth callback from provider.

    Redirects to frontend with token or error.
    """
    # Handle error from provider
    if error:
        logger.warning(f"OAuth error from provider: {error} - {error_description}")
        return RedirectResponse(
            url=f"{FRONTEND_BASE_URL}/login?oauth_error={error}&error_description={error_description or ''}"
        )

    if not code or not state:
        return RedirectResponse(
            url=f"{FRONTEND_BASE_URL}/login?oauth_error=missing_params"
        )

    # Validate and consume state
    redis = await get_redis_client()
    state_mgr = OAuthLoginStateManager(redis)

    try:
        state_data = await state_mgr.validate_and_consume(state)
    except HTTPException:
        return RedirectResponse(
            url=f"{FRONTEND_BASE_URL}/login?oauth_error=invalid_state"
        )

    provider = state_data["provider"]
    code_verifier = state_data["code_verifier"]
    return_url = state_data.get("return_url") or "/"
    link_to_user_id = state_data.get("link_to_user_id")

    async_session = await get_db_session()
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    async with async_session() as db:
        # Get OAuth config
        config = await get_oauth_config(db, provider)
        if not config:
            return RedirectResponse(
                url=f"{FRONTEND_BASE_URL}/login?oauth_error=provider_not_configured"
            )

        # Build redirect URI (must match authorize)
        redirect_uri = f"{FRONTEND_BASE_URL}/api/auth/oauth/callback"

        # Exchange code for tokens
        try:
            tokens = await exchange_code_for_tokens(
                provider=provider,
                code=code,
                code_verifier=code_verifier,
                redirect_uri=redirect_uri,
                client_id=config["client_id"],
                client_secret=config["client_secret"],
                tenant_id=config.get("tenant_id"),
            )
        except Exception as e:
            logger.error(f"Token exchange failed: {e}")
            await log_oauth_audit(db, None, "unknown", "OAUTH_LOGIN_FAILED", {"reason": "token_exchange_failed", "provider": provider}, ip_address)
            return RedirectResponse(
                url=f"{FRONTEND_BASE_URL}/login?oauth_error=token_exchange_failed"
            )

        # Get user info from provider
        try:
            user_info = await get_user_info(provider, tokens["access_token"])
        except Exception as e:
            logger.error(f"Failed to get user info: {e}")
            await log_oauth_audit(db, None, "unknown", "OAUTH_LOGIN_FAILED", {"reason": "userinfo_failed", "provider": provider}, ip_address)
            return RedirectResponse(
                url=f"{FRONTEND_BASE_URL}/login?oauth_error=userinfo_failed"
            )

        # Check domain whitelist
        if config["allowed_domains"]:
            email_domain = user_info.email.split("@")[1].lower()
            allowed = [d.lower() for d in config["allowed_domains"]]
            if email_domain not in allowed:
                await log_oauth_audit(db, None, user_info.email, "OAUTH_LOGIN_FAILED", {"reason": "domain_not_allowed", "domain": email_domain}, ip_address)
                return RedirectResponse(
                    url=f"{FRONTEND_BASE_URL}/login?oauth_error=domain_not_allowed"
                )

        # Check if OAuth identity exists
        existing_user = await find_user_by_oauth_identity(db, provider, user_info.provider_user_id)

        if existing_user:
            # Existing OAuth user - login
            if existing_user["status"] != "active":
                await log_oauth_audit(db, existing_user["id"], existing_user["username"], "OAUTH_LOGIN_FAILED", {"reason": "account_disabled"}, ip_address)
                return RedirectResponse(
                    url=f"{FRONTEND_BASE_URL}/login?oauth_error=account_disabled"
                )

            if existing_user["pending_approval"]:
                await log_oauth_audit(db, existing_user["id"], existing_user["username"], "OAUTH_LOGIN_FAILED", {"reason": "pending_approval"}, ip_address)
                return RedirectResponse(
                    url=f"{FRONTEND_BASE_URL}/login?oauth_error=pending_approval"
                )

            # Create session
            await update_oauth_last_login(db, provider, user_info.provider_user_id)
            session_token = await create_session(db, existing_user["id"], ip_address, user_agent)
            await log_oauth_audit(db, existing_user["id"], existing_user["username"], "OAUTH_LOGIN_SUCCESS", {"provider": provider}, ip_address)

            # SECURITY: Set session token as HTTP-only cookie instead of URL param
            response = RedirectResponse(
                url=f"{FRONTEND_BASE_URL}/auth/oauth/success?return_url={return_url}"
            )
            response.set_cookie(
                "session_token",
                session_token,
                max_age=86400,      # 24 hours
                httponly=True,      # Not accessible via JavaScript
                secure=os.getenv("ENVIRONMENT", "development") == "production",
                samesite="lax",     # Allow top-level navigation
                path="/",
            )
            return response

        # No existing OAuth identity - check if email exists
        existing_email_user = await find_user_by_email(db, user_info.email)

        if existing_email_user:
            # Email exists but no OAuth link - require explicit linking
            await log_oauth_audit(db, existing_email_user["id"], user_info.email, "OAUTH_LOGIN_FAILED", {"reason": "email_exists_not_linked", "provider": provider}, ip_address)
            return RedirectResponse(
                url=f"{FRONTEND_BASE_URL}/login?oauth_error=email_exists&email={user_info.email}"
            )

        # New user - check if registration allowed
        if not config["allow_registration"]:
            await log_oauth_audit(db, None, user_info.email, "OAUTH_LOGIN_FAILED", {"reason": "registration_disabled", "provider": provider}, ip_address)
            return RedirectResponse(
                url=f"{FRONTEND_BASE_URL}/login?oauth_error=registration_disabled"
            )

        # Create new user
        pending = config["require_admin_approval"]
        user_id = await create_oauth_user(
            db,
            user_info,
            role=config["default_role"],
            pending_approval=pending,
        )

        await log_oauth_audit(db, user_id, user_info.email, "OAUTH_USER_CREATED", {"provider": provider, "pending_approval": pending}, ip_address)

        if pending:
            return RedirectResponse(
                url=f"{FRONTEND_BASE_URL}/login?oauth_error=pending_approval&new_user=true"
            )

        # Create session for new user
        session_token = await create_session(db, user_id, ip_address, user_agent)
        await log_oauth_audit(db, user_id, user_info.email, "OAUTH_LOGIN_SUCCESS", {"provider": provider, "new_user": True}, ip_address)

        # SECURITY: Set session token as HTTP-only cookie instead of URL param
        response = RedirectResponse(
            url=f"{FRONTEND_BASE_URL}/auth/oauth/success?return_url={return_url}"
        )
        response.set_cookie(
            "session_token",
            session_token,
            max_age=86400,      # 24 hours
            httponly=True,      # Not accessible via JavaScript
            secure=os.getenv("ENVIRONMENT", "development") == "production",
            samesite="lax",     # Allow top-level navigation
            path="/",
        )
        return response


# =============================================================================
# API ROUTER - AUTHENTICATED ENDPOINTS
# =============================================================================

router = APIRouter(prefix="/oauth", tags=["OAuth Login"])


@router.get("/identities")
async def get_my_oauth_identities(user: Dict = Depends(require_auth)):
    """Get OAuth identities linked to current user."""
    async_session = await get_db_session()

    async with async_session() as db:
        result = await db.execute(
            text("""
                SELECT provider, provider_email, provider_name, linked_at, last_login_at
                FROM admin_oauth_identities
                WHERE user_id = :user_id
            """),
            {"user_id": user["id"]}
        )
        rows = result.fetchall()

    return {
        "identities": [
            {
                "provider": row[0],
                "email": row[1],
                "name": row[2],
                "linked_at": row[3].isoformat() if row[3] else None,
                "last_login_at": row[4].isoformat() if row[4] else None,
            }
            for row in rows
        ]
    }


@router.post("/link/{provider}")
async def link_oauth_to_account(
    provider: str,
    request: Request,
    return_url: Optional[str] = Query(None),
    user: Dict = Depends(require_auth),
):
    """Start OAuth flow to link provider to current account."""
    if provider not in ("google", "microsoft"):
        raise HTTPException(status_code=400, detail="Invalid provider")

    async_session = await get_db_session()

    async with async_session() as db:
        # Check if already linked
        result = await db.execute(
            text("SELECT 1 FROM admin_oauth_identities WHERE user_id = :user_id AND provider = :provider"),
            {"user_id": user["id"], "provider": provider}
        )
        if result.fetchone():
            raise HTTPException(status_code=400, detail=f"{provider} is already linked to your account")

        config = await get_oauth_config(db, provider)

    if not config or not config["enabled"]:
        raise HTTPException(status_code=400, detail=f"{provider} login is not enabled")

    # Generate PKCE and state with link_to_user_id
    pkce = generate_pkce_challenge()

    redis = await get_redis_client()
    state_mgr = OAuthLoginStateManager(redis)
    state = await state_mgr.generate(
        provider=provider,
        code_verifier=pkce.code_verifier,
        return_url=return_url or "/settings",
        link_to_user_id=user["id"],
    )

    redirect_uri = f"{FRONTEND_BASE_URL}/api/auth/oauth/callback"

    if provider == "google":
        auth_url = GOOGLE_AUTH_URL
        params = {
            "client_id": config["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "state": state,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    else:
        tenant = config.get("tenant_id") or "common"
        auth_url = MICROSOFT_AUTH_URL_TEMPLATE.format(tenant=tenant)
        params = {
            "client_id": config["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(MICROSOFT_SCOPES),
            "state": state,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }

    full_auth_url = f"{auth_url}?{urlencode(params)}"

    return {"auth_url": full_auth_url}


@router.delete("/unlink/{provider}")
async def unlink_oauth_from_account(
    provider: str,
    request: Request,
    user: Dict = Depends(require_auth),
):
    """Unlink OAuth provider from current account."""
    if provider not in ("google", "microsoft"):
        raise HTTPException(status_code=400, detail="Invalid provider")

    async_session = await get_db_session()
    ip_address = request.client.host if request.client else None

    async with async_session() as db:
        # Check if identity exists
        result = await db.execute(
            text("SELECT 1 FROM admin_oauth_identities WHERE user_id = :user_id AND provider = :provider"),
            {"user_id": user["id"], "provider": provider}
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail=f"{provider} is not linked to your account")

        # Check if user has password or another OAuth
        result = await db.execute(
            text("SELECT password_hash FROM admin_users WHERE id = :id"),
            {"id": user["id"]}
        )
        has_password = bool(result.fetchone()[0])

        result = await db.execute(
            text("SELECT COUNT(*) FROM admin_oauth_identities WHERE user_id = :user_id"),
            {"user_id": user["id"]}
        )
        oauth_count = result.scalar()

        if not has_password and oauth_count <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot unlink last authentication method. Set a password first."
            )

        # Delete the identity
        await db.execute(
            text("DELETE FROM admin_oauth_identities WHERE user_id = :user_id AND provider = :provider"),
            {"user_id": user["id"], "provider": provider}
        )
        await db.commit()

        await log_oauth_audit(db, user["id"], user["username"], "OAUTH_ACCOUNT_UNLINKED", {"provider": provider}, ip_address)

    return {"status": "success", "message": f"{provider} has been unlinked from your account"}


# =============================================================================
# API ROUTER - ADMIN ENDPOINTS
# =============================================================================

admin_router = APIRouter(prefix="/admin/oauth", tags=["OAuth Admin"])


@admin_router.get("/config")
async def get_oauth_admin_config(user: Dict = Depends(require_admin)):
    """Get OAuth configuration (admin only)."""
    async_session = await get_db_session()

    async with async_session() as db:
        result = await db.execute(
            text("""
                SELECT provider, client_id, tenant_id, enabled, allow_registration,
                       default_role, require_admin_approval, allowed_domains, created_at, updated_at
                FROM oauth_config
            """)
        )
        rows = result.fetchall()

    return {
        "providers": {
            row[0]: {
                "client_id": row[1] if row[1] != "not-configured" else None,
                "tenant_id": row[2],
                "enabled": row[3],
                "allow_registration": row[4],
                "default_role": row[5],
                "require_admin_approval": row[6],
                "allowed_domains": row[7] or [],
                "created_at": row[8].isoformat() if row[8] else None,
                "updated_at": row[9].isoformat() if row[9] else None,
            }
            for row in rows
        }
    }


@admin_router.put("/config/{provider}")
async def update_oauth_config(
    provider: str,
    request: Request,
    user: Dict = Depends(require_admin),
):
    """Update OAuth provider configuration (admin only)."""
    if provider not in ("google", "microsoft"):
        raise HTTPException(status_code=400, detail="Invalid provider")

    body = await request.json()
    async_session = await get_db_session()
    ip_address = request.client.host if request.client else None

    async with async_session() as db:
        # Build update fields
        updates = []
        params = {"provider": provider}

        if "client_id" in body:
            updates.append("client_id = :client_id")
            params["client_id"] = body["client_id"]

        if "client_secret" in body and body["client_secret"]:
            updates.append("client_secret_encrypted = :client_secret")
            params["client_secret"] = encrypt_secret(body["client_secret"])

        if "tenant_id" in body:
            updates.append("tenant_id = :tenant_id")
            params["tenant_id"] = body["tenant_id"]

        if "enabled" in body:
            updates.append("enabled = :enabled")
            params["enabled"] = body["enabled"]

        if "allow_registration" in body:
            updates.append("allow_registration = :allow_registration")
            params["allow_registration"] = body["allow_registration"]

        if "default_role" in body:
            if body["default_role"] not in ("admin", "operator", "readonly"):
                raise HTTPException(status_code=400, detail="Invalid role")
            updates.append("default_role = :default_role")
            params["default_role"] = body["default_role"]

        if "require_admin_approval" in body:
            updates.append("require_admin_approval = :require_admin_approval")
            params["require_admin_approval"] = body["require_admin_approval"]

        if "allowed_domains" in body:
            updates.append("allowed_domains = :allowed_domains")
            params["allowed_domains"] = body["allowed_domains"]

        if updates:
            await db.execute(
                text(f"UPDATE oauth_config SET {', '.join(updates)} WHERE provider = :provider"),
                params
            )
            await db.commit()

        await log_oauth_audit(db, user["id"], user["username"], "OAUTH_CONFIG_UPDATED", {"provider": provider, "changes": list(body.keys())}, ip_address)

    return {"status": "success"}


@admin_router.get("/pending")
async def get_pending_oauth_users(user: Dict = Depends(require_admin)):
    """Get users pending OAuth approval (admin only)."""
    async_session = await get_db_session()

    async with async_session() as db:
        result = await db.execute(
            text("""
                SELECT u.id, u.username, u.email, u.display_name, u.role, u.created_at,
                       oi.provider, oi.provider_email
                FROM admin_users u
                LEFT JOIN admin_oauth_identities oi ON oi.user_id = u.id
                WHERE u.pending_approval = TRUE
                ORDER BY u.created_at DESC
            """)
        )
        rows = result.fetchall()

    return {
        "pending_users": [
            {
                "id": str(row[0]),
                "username": row[1],
                "email": row[2],
                "display_name": row[3],
                "role": row[4],
                "created_at": row[5].isoformat() if row[5] else None,
                "oauth_provider": row[6],
                "oauth_email": row[7],
            }
            for row in rows
        ]
    }


@admin_router.post("/approve/{user_id}")
async def approve_pending_user(
    user_id: str,
    request: Request,
    user: Dict = Depends(require_admin),
):
    """Approve a pending OAuth user (admin only)."""
    async_session = await get_db_session()
    ip_address = request.client.host if request.client else None

    async with async_session() as db:
        # Check user exists and is pending
        result = await db.execute(
            text("SELECT username, pending_approval FROM admin_users WHERE id = :id"),
            {"id": user_id}
        )
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        if not row[1]:
            raise HTTPException(status_code=400, detail="User is not pending approval")

        # Approve
        await db.execute(
            text("""
                UPDATE admin_users
                SET pending_approval = FALSE, approved_by = :approved_by, approved_at = NOW()
                WHERE id = :id
            """),
            {"id": user_id, "approved_by": user["id"]}
        )
        await db.commit()

        await log_oauth_audit(db, user_id, row[0], "OAUTH_USER_APPROVED", {"approved_by": user["username"]}, ip_address)

    return {"status": "success", "message": "User has been approved"}


@admin_router.delete("/reject/{user_id}")
async def reject_pending_user(
    user_id: str,
    request: Request,
    user: Dict = Depends(require_admin),
):
    """Reject (delete) a pending OAuth user (admin only)."""
    async_session = await get_db_session()
    ip_address = request.client.host if request.client else None

    async with async_session() as db:
        # Check user exists and is pending
        result = await db.execute(
            text("SELECT username, pending_approval FROM admin_users WHERE id = :id"),
            {"id": user_id}
        )
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        if not row[1]:
            raise HTTPException(status_code=400, detail="User is not pending approval")

        username = row[0]

        # Delete (cascade deletes OAuth identities)
        await db.execute(
            text("DELETE FROM admin_users WHERE id = :id"),
            {"id": user_id}
        )
        await db.commit()

        await log_oauth_audit(db, None, username, "OAUTH_USER_REJECTED", {"rejected_by": user["username"]}, ip_address)

    return {"status": "success", "message": "User has been rejected"}
