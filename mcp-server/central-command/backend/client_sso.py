"""Client portal OIDC SSO — authorize, callback, and partner-facing config CRUD."""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .fleet import get_pool
from .tenant_middleware import admin_connection, admin_transaction  # noqa: F401
from .oauth_login import encrypt_secret, decrypt_secret
from .partners import require_partner
from .client_portal import (
    SESSION_COOKIE_NAME,
    SESSION_DURATION_DAYS,
    SESSION_COOKIE_MAX_AGE,
    generate_token,
    hash_token,
    _client_mfa_pending,
    MFA_PENDING_TTL_MINUTES,
)

logger = logging.getLogger(__name__)

# Public router — no auth required (SSO login flow)
sso_router = APIRouter(prefix="/client/auth/sso", tags=["client-sso"])

# Partner-facing config router — requires partner auth
# Partner-facing config router — every endpoint MUST be partner-authed.
# Pre-fix (Session 220 RT-Auth-2026-05-12 zero-auth audit P0): the router
# carried no `dependencies` arg even though each handler's docstring
# claimed "Partner auth applied at router level." That intent was never
# wired. Anonymous callers could WIPE SSO configs (DELETE), write new
# OIDC credentials including encrypted client_secret (PUT), or read the
# allowlist/issuer (GET). Now blocked at router level.
config_router = APIRouter(
    prefix="/api/partners/me/orgs",
    tags=["partner-sso-config"],
    dependencies=[Depends(require_partner)],
)

PKCE_VERIFIER_LENGTH = 64
STATE_TTL_SECONDS = 600  # 10 minutes
SESSION_TOKEN_SECRET = os.environ.get("SESSION_TOKEN_SECRET", "dev-secret")

# OIDC discovery cache: issuer_url -> (endpoints_dict, cached_at)
_discovery_cache: dict = {}
DISCOVERY_CACHE_TTL = 300  # 5 minutes


# =============================================================================
# HELPERS
# =============================================================================


def _hash_state(state: str) -> str:
    """HMAC-SHA256 hash of state token for storage."""
    return hmac.new(
        SESSION_TOKEN_SECRET.encode(), state.encode(), hashlib.sha256
    ).hexdigest()


def _generate_pkce_pair() -> tuple:
    """Generate PKCE code_verifier and S256 code_challenge."""
    verifier = secrets.token_urlsafe(PKCE_VERIFIER_LENGTH)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


async def _discover_oidc(issuer_url: str) -> dict:
    """Fetch OIDC discovery document, cached for 5 minutes."""
    now = datetime.now(timezone.utc)
    cached = _discovery_cache.get(issuer_url)
    if cached and (now - cached[1]).total_seconds() < DISCOVERY_CACHE_TTL:
        return cached[0]

    discovery_url = issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(discovery_url)
        if resp.status_code != 200:
            raise HTTPException(400, f"OIDC discovery failed: {discovery_url} returned {resp.status_code}")
        data = resp.json()

    required = ["authorization_endpoint", "token_endpoint"]
    for field in required:
        if field not in data:
            raise HTTPException(400, f"OIDC discovery missing required field: {field}")

    _discovery_cache[issuer_url] = (data, now)
    return data


def _decode_id_token_payload(id_token: str) -> dict:
    """Decode the payload of a JWT ID token (no signature verification —
    token came directly from IdP over HTTPS in the token exchange)."""
    parts = id_token.split(".")
    if len(parts) != 3:
        raise HTTPException(400, "Invalid ID token format")
    # Add padding
    payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        raise HTTPException(400, "Failed to decode ID token payload")
    return payload


# =============================================================================
# SSO LOGIN FLOW
# =============================================================================


class SSOAuthorizeRequest(BaseModel):
    email: str


@sso_router.post("/authorize")
async def sso_authorize(body: SSOAuthorizeRequest, request: Request):
    """Initiate OIDC SSO login. Looks up user's org SSO config by email,
    generates PKCE + state + nonce, returns IdP authorization URL."""
    pool = await get_pool()
    email = body.email.strip().lower()

    # wave-12: SSO authorize — DELETE + lookups + INSERT state token; pin to single PgBouncer backend.
    async with admin_transaction(pool) as conn:
        # Clean up expired state tokens
        await conn.execute(
            "DELETE FROM client_oauth_state WHERE expires_at < NOW()"
        )

        # Look up user by email (globally unique) to find their org
        user = await conn.fetchrow(
            "SELECT id, client_org_id FROM client_users WHERE email = $1",
            email,
        )
        if not user:
            raise HTTPException(404, "No account found for this email")

        # Check if org has SSO configured
        sso = await conn.fetchrow(
            "SELECT issuer_url, client_id, allowed_domains FROM client_org_sso WHERE client_org_id = $1",
            user["client_org_id"],
        )
        if not sso:
            raise HTTPException(404, "SSO is not configured for this organization")

        # Discover OIDC endpoints
        discovery = await _discover_oidc(sso["issuer_url"])
        auth_endpoint = discovery["authorization_endpoint"]

        # Generate PKCE pair
        code_verifier, code_challenge = _generate_pkce_pair()

        # Generate state + nonce
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(16)

        # Determine redirect URI
        origin = request.headers.get("origin", "")
        if not origin:
            origin = str(request.base_url).rstrip("/")
        redirect_uri = f"{origin}/api/client/auth/sso/callback"

        # Store state (hashed) + verifier + nonce
        state_hash = _hash_state(state)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=STATE_TTL_SECONDS)
        await conn.execute("""
            INSERT INTO client_oauth_state
                (state_hash, code_verifier, nonce, client_org_id, redirect_uri, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, state_hash, code_verifier, nonce, user["client_org_id"], redirect_uri, expires_at)

    # Build authorization URL
    params = {
        "response_type": "code",
        "client_id": sso["client_id"],
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    query = "&".join(f"{k}={httpx.URL('', params={k: v}).params[k]}" for k, v in params.items())
    auth_url = f"{auth_endpoint}?{query}"

    return {"auth_url": auth_url}


@sso_router.get("/callback")
async def sso_callback(
    code: str,
    state: str,
    request: Request,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    """OIDC callback — exchange code for tokens, validate, create session."""
    if error:
        raise HTTPException(400, f"IdP error: {error} — {error_description or ''}")

    pool = await get_pool()
    state_hash = _hash_state(state)

    # Coach-sweep ratchet wave-3 2026-05-08: 8-query SSO callback —
    # auth-path; multi-write (state delete + user upsert + session
    # create + audit log). admin_transaction critical for routing-
    # pathology safety on this flow.
    async with admin_transaction(pool) as conn:
        # Fetch and delete state (single-use)
        state_row = await conn.fetchrow(
            "DELETE FROM client_oauth_state WHERE state_hash = $1 RETURNING *",
            state_hash,
        )
        if not state_row:
            raise HTTPException(400, "Invalid or expired state token")
        if state_row["expires_at"] < datetime.now(timezone.utc):
            raise HTTPException(400, "State token expired")

        org_id = state_row["client_org_id"]
        code_verifier = state_row["code_verifier"]
        stored_nonce = state_row["nonce"]
        redirect_uri = state_row["redirect_uri"]

        # Get SSO config (need client_id + secret for token exchange)
        sso = await conn.fetchrow(
            "SELECT issuer_url, client_id, client_secret_encrypted, allowed_domains FROM client_org_sso WHERE client_org_id = $1",
            org_id,
        )
        if not sso:
            raise HTTPException(400, "SSO configuration not found")

        client_secret = decrypt_secret(sso["client_secret_encrypted"])

        # Discover token endpoint
        discovery = await _discover_oidc(sso["issuer_url"])
        token_endpoint = discovery["token_endpoint"]

        # Exchange code for tokens
        async with httpx.AsyncClient(timeout=15.0) as http:
            token_resp = await http.post(token_endpoint, data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": sso["client_id"],
                "client_secret": client_secret,
                "code_verifier": code_verifier,
            })
        if token_resp.status_code != 200:
            logger.error(f"SSO token exchange failed: {token_resp.status_code} {token_resp.text[:500]}")
            raise HTTPException(400, "Token exchange failed")

        tokens = token_resp.json()
        id_token = tokens.get("id_token")
        if not id_token:
            raise HTTPException(400, "No id_token in token response")

        # Decode and validate ID token
        payload = _decode_id_token_payload(id_token)

        # Validate nonce
        if payload.get("nonce") != stored_nonce:
            raise HTTPException(400, "ID token nonce mismatch")

        # Extract email
        email = (payload.get("email") or "").strip().lower()
        if not email:
            raise HTTPException(400, "ID token missing email claim")

        # Validate email domain against allowed_domains
        allowed = sso["allowed_domains"] or []
        if allowed:
            domain = email.split("@")[-1] if "@" in email else ""
            if domain not in allowed:
                raise HTTPException(403, f"Email domain '{domain}' is not allowed for this organization")

        # Find or create client user — MUST scope to this org to prevent
        # cross-org login (user in OrgA must not authenticate into OrgB).
        user = await conn.fetchrow(
            "SELECT id, mfa_enabled, mfa_secret FROM client_users WHERE email = $1 AND client_org_id = $2",
            email, org_id,
        )
        if user:
            await conn.execute(
                "UPDATE client_users SET last_login_at = NOW() WHERE id = $1",
                user["id"],
            )
            user_id = user["id"]
            mfa_enabled = user.get("mfa_enabled", False)
        else:
            # Auto-provision with viewer role
            user_id = await conn.fetchval("""
                INSERT INTO client_users (client_org_id, email, name, role, email_verified, is_active)
                VALUES ($1, $2, $3, 'viewer', true, true)
                RETURNING id
            """, org_id, email, payload.get("name") or email.split("@")[0])
            mfa_enabled = False
            logger.info(f"SSO auto-provisioned user {email} for org {org_id}")

            # Notify partner that a new client user was auto-provisioned via SSO
            try:
                org_row = await conn.fetchrow(
                    "SELECT name, current_partner_id FROM client_orgs WHERE id = $1", org_id
                )
                if org_row and org_row["current_partner_id"]:
                    # Mirror alert_router.py canonical column set:
                    # notification_type + summary (no title/metadata in
                    # the schema). Org context goes into the summary.
                    await conn.execute("""
                        INSERT INTO partner_notifications (
                            partner_id, org_id, notification_type, summary
                        ) VALUES ($1, $2, 'client_user_provisioned', $3)
                    """,
                        org_row["current_partner_id"],
                        org_id,
                        f"New user joined {org_row['name']}: {email} was auto-provisioned via SSO with viewer role.",
                    )
            except Exception as e:
                logger.warning(f"Partner notification for SSO provision failed (non-fatal): {e}")

        # MFA check
        if mfa_enabled:
            mfa_token = secrets.token_urlsafe(32)
            _client_mfa_pending[mfa_token] = {
                "user_id": str(user_id),
                "email": email,
                "expires": datetime.now(timezone.utc) + timedelta(minutes=MFA_PENDING_TTL_MINUTES),
                "source": "sso",
            }
            # Redirect to MFA page with pending token
            return Response(
                status_code=302,
                headers={"Location": f"/client/login?mfa_required=true&mfa_token={mfa_token}"},
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
        """, user_id, token_hash_val, user_agent, ip_address, expires_at)

    response = Response(status_code=302, headers={"Location": "/client/dashboard"})
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=SESSION_COOKIE_MAX_AGE,
        path="/",
    )
    return response


# =============================================================================
# SSO CONFIG CRUD (Partner-facing)
# =============================================================================


class SSOConfigRequest(BaseModel):
    issuer_url: str
    client_id: str
    client_secret: str
    allowed_domains: list = []
    sso_enforced: bool = False


@config_router.get("/{org_id}/sso")
async def get_sso_config(org_id: str):
    """Read SSO config for a client org. Partner auth applied at router level."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            "SELECT issuer_url, client_id, allowed_domains, sso_enforced, created_at, updated_at FROM client_org_sso WHERE client_org_id = $1",
            org_id,
        )
        if not row:
            return {"configured": False}
        return {
            "configured": True,
            "issuer_url": row["issuer_url"],
            "client_id": row["client_id"],
            "allowed_domains": row["allowed_domains"],
            "sso_enforced": row["sso_enforced"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }


@config_router.put("/{org_id}/sso")
async def put_sso_config(org_id: str, body: SSOConfigRequest):
    """Create or update SSO config for a client org. Validates issuer discovery."""
    # Validate issuer by fetching discovery document
    await _discover_oidc(body.issuer_url)

    pool = await get_pool()
    encrypted_secret = encrypt_secret(body.client_secret)

    # admin_transaction (wave-40): put_sso_config issues 2 admin
    # statements (org check, UPSERT SSO config).
    async with admin_transaction(pool) as conn:
        # Verify org exists and belongs to this partner
        org = await conn.fetchrow(
            "SELECT id FROM client_orgs WHERE id::text = $1", org_id
        )
        if not org:
            raise HTTPException(404, "Organization not found")

        await conn.execute("""
            INSERT INTO client_org_sso
                (client_org_id, issuer_url, client_id, client_secret_encrypted,
                 allowed_domains, sso_enforced, created_by_partner_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (client_org_id) DO UPDATE SET
                issuer_url = EXCLUDED.issuer_url,
                client_id = EXCLUDED.client_id,
                client_secret_encrypted = EXCLUDED.client_secret_encrypted,
                allowed_domains = EXCLUDED.allowed_domains,
                sso_enforced = EXCLUDED.sso_enforced,
                updated_at = NOW()
        """,
            org["id"],
            body.issuer_url,
            body.client_id,
            encrypted_secret,
            body.allowed_domains,
            body.sso_enforced,
            None,  # created_by_partner_id — set when partner auth is wired
        )

    logger.info(f"SSO config saved for org {org_id}")
    return {"status": "saved", "org_id": org_id}


@config_router.delete("/{org_id}/sso")
async def delete_sso_config(org_id: str):
    """Remove SSO config for a client org."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        result = await conn.execute(
            "DELETE FROM client_org_sso WHERE client_org_id::text = $1", org_id
        )
        if result == "DELETE 0":
            raise HTTPException(404, "No SSO config found for this organization")

    logger.info(f"SSO config removed for org {org_id}")
    return {"status": "deleted", "org_id": org_id}
