"""
Secure OAuth state token management.

SECURITY REQUIREMENT: OAuth state tokens MUST be:
- Cryptographically random (secrets.token_urlsafe)
- Single-use (Redis GETDEL for atomic consume)
- Time-limited (10 minute TTL)
- Site-bound (state includes site_id, validated on callback)

This prevents CSRF attacks and ensures OAuth callbacks are legitimate.

Usage:
    state_mgr = OAuthStateManager(redis_client)

    # Generate state for OAuth redirect
    state = await state_mgr.generate(
        site_id="site-123",
        provider="google_workspace",
        return_url="/integrations"
    )

    # Validate on callback (single-use)
    result = await state_mgr.validate(
        state=state,
        expected_site_id="site-123"
    )
    # result = {"provider": "google_workspace", "return_url": "/integrations"}
"""

import secrets
import json
import logging
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Configuration
STATE_TTL_SECONDS = 600  # 10 minutes
STATE_TOKEN_BYTES = 32   # 256 bits of entropy


class OAuthStateError(Exception):
    """Base exception for OAuth state errors."""
    pass


class StateExpiredError(OAuthStateError):
    """State token has expired."""
    pass


class StateInvalidError(OAuthStateError):
    """State token is invalid or already used."""
    pass


class StateSiteMismatchError(OAuthStateError):
    """State token was created for a different site."""
    pass


@dataclass
class OAuthStateData:
    """Data stored with OAuth state token."""
    site_id: str
    provider: str
    return_url: Optional[str]
    created_at: datetime
    nonce: str
    integration_name: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = None


class OAuthStateManager:
    """
    Redis-backed OAuth state token manager.

    Ensures state tokens are single-use, time-limited, and site-bound.
    """

    def __init__(self, redis_client):
        """
        Initialize the state manager.

        Args:
            redis_client: Async Redis client instance
        """
        self.redis = redis_client
        self.ttl = STATE_TTL_SECONDS

    def _make_key(self, state: str) -> str:
        """Create Redis key for state token."""
        return f"oauth_state:{state}"

    def _hash_state(self, state: str) -> str:
        """Create a hash of the state for logging (don't log actual state)."""
        return hashlib.sha256(state.encode()).hexdigest()[:16]

    async def generate(
        self,
        site_id: str,
        provider: str,
        return_url: Optional[str] = None,
        integration_name: Optional[str] = None,
        extra_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Generate a new OAuth state token.

        Args:
            site_id: Site this OAuth flow is for
            provider: OAuth provider (google_workspace, okta, azure_ad)
            return_url: URL to redirect after OAuth completion
            integration_name: Name for the new integration
            extra_data: Additional data to store with state

        Returns:
            Cryptographically random state token (URL-safe)
        """
        # Generate cryptographically secure token
        state = secrets.token_urlsafe(STATE_TOKEN_BYTES)
        nonce = secrets.token_hex(16)

        # Create state data
        state_data = OAuthStateData(
            site_id=site_id,
            provider=provider,
            return_url=return_url,
            created_at=datetime.utcnow(),
            nonce=nonce,
            integration_name=integration_name,
            extra_data=extra_data
        )

        # Serialize to JSON
        data_json = json.dumps({
            "site_id": state_data.site_id,
            "provider": state_data.provider,
            "return_url": state_data.return_url,
            "created_at": state_data.created_at.isoformat(),
            "nonce": state_data.nonce,
            "integration_name": state_data.integration_name,
            "extra_data": state_data.extra_data,
        })

        # Store with TTL
        key = self._make_key(state)
        await self.redis.setex(key, self.ttl, data_json)

        logger.info(
            f"OAuth state generated: site={site_id} provider={provider} "
            f"hash={self._hash_state(state)} ttl={self.ttl}s"
        )

        return state

    async def validate(
        self,
        state: str,
        expected_site_id: str
    ) -> Dict[str, Any]:
        """
        Validate and consume OAuth state token (single-use).

        Args:
            state: State token from OAuth callback
            expected_site_id: Site ID that should own this state

        Returns:
            Dict with provider, return_url, integration_name, extra_data

        Raises:
            StateInvalidError: State doesn't exist or already used
            StateSiteMismatchError: State belongs to different site
        """
        key = self._make_key(state)
        state_hash = self._hash_state(state)

        # Atomic get-and-delete (single-use)
        data_json = await self.redis.getdel(key)

        if not data_json:
            logger.warning(
                f"OAuth state validation failed: hash={state_hash} "
                f"reason=not_found_or_already_used"
            )
            raise StateInvalidError(
                "State token is invalid or has already been used"
            )

        # Parse state data
        try:
            data = json.loads(data_json)
        except json.JSONDecodeError:
            logger.error(f"OAuth state corrupted: hash={state_hash}")
            raise StateInvalidError("State token data is corrupted")

        # Verify site binding
        if data["site_id"] != expected_site_id:
            logger.warning(
                f"OAuth state site mismatch: hash={state_hash} "
                f"expected={expected_site_id} actual={data['site_id']}"
            )
            # Log security event
            await self._log_security_event(
                event="state_site_mismatch",
                state_hash=state_hash,
                expected_site_id=expected_site_id,
                actual_site_id=data["site_id"]
            )
            raise StateSiteMismatchError(
                "State token was created for a different site"
            )

        logger.info(
            f"OAuth state validated: site={expected_site_id} "
            f"provider={data['provider']} hash={state_hash}"
        )

        return {
            "provider": data["provider"],
            "return_url": data.get("return_url"),
            "integration_name": data.get("integration_name"),
            "extra_data": data.get("extra_data"),
            "created_at": data["created_at"],
        }

    async def validate_exists(self, state: str) -> bool:
        """
        Check if state token exists without consuming it.

        Useful for pre-validation before OAuth redirect.

        Args:
            state: State token to check

        Returns:
            True if state exists and is valid
        """
        key = self._make_key(state)
        return await self.redis.exists(key) > 0

    async def get_state_info(self, state: str) -> Optional[Dict[str, Any]]:
        """
        Get state info without consuming it (for debugging).

        Args:
            state: State token

        Returns:
            State data or None if not found
        """
        key = self._make_key(state)
        data_json = await self.redis.get(key)

        if not data_json:
            return None

        try:
            return json.loads(data_json)
        except json.JSONDecodeError:
            return None

    async def revoke(self, state: str) -> bool:
        """
        Manually revoke a state token.

        Args:
            state: State token to revoke

        Returns:
            True if token was revoked, False if not found
        """
        key = self._make_key(state)
        result = await self.redis.delete(key)

        if result > 0:
            logger.info(f"OAuth state revoked: hash={self._hash_state(state)}")
            return True
        return False

    async def cleanup_expired(self) -> int:
        """
        Clean up expired state tokens (Redis handles this via TTL).

        This method is mainly for metrics/logging.

        Returns:
            Count of active state tokens
        """
        # Redis TTL handles expiration automatically
        # This just counts active tokens for monitoring
        cursor = 0
        count = 0

        while True:
            cursor, keys = await self.redis.scan(
                cursor=cursor,
                match="oauth_state:*",
                count=100
            )
            count += len(keys)

            if cursor == 0:
                break

        logger.debug(f"Active OAuth states: {count}")
        return count

    async def _log_security_event(
        self,
        event: str,
        state_hash: str,
        **kwargs
    ) -> None:
        """
        Log security-relevant events for monitoring.

        Args:
            event: Event type
            state_hash: Hash of the state token
            **kwargs: Additional event data
        """
        # Store in Redis for security monitoring
        event_data = {
            "event": event,
            "state_hash": state_hash,
            "timestamp": datetime.utcnow().isoformat(),
            **kwargs
        }

        key = f"oauth_security_event:{datetime.utcnow().strftime('%Y%m%d%H%M%S')}:{secrets.token_hex(4)}"
        await self.redis.setex(
            key,
            86400,  # Keep security events for 24 hours
            json.dumps(event_data)
        )

    @property
    def ttl_seconds(self) -> int:
        """Return configured TTL in seconds."""
        return self.ttl


class OAuthStateManagerSync:
    """
    Synchronous version of OAuthStateManager for non-async contexts.

    Uses the same Redis key format for compatibility.
    """

    def __init__(self, redis_client):
        """
        Initialize the synchronous state manager.

        Args:
            redis_client: Sync Redis client instance
        """
        self.redis = redis_client
        self.ttl = STATE_TTL_SECONDS

    def _make_key(self, state: str) -> str:
        """Create Redis key for state token."""
        return f"oauth_state:{state}"

    def generate(
        self,
        site_id: str,
        provider: str,
        return_url: Optional[str] = None
    ) -> str:
        """Generate a new OAuth state token (sync version)."""
        state = secrets.token_urlsafe(STATE_TOKEN_BYTES)
        nonce = secrets.token_hex(16)

        data_json = json.dumps({
            "site_id": site_id,
            "provider": provider,
            "return_url": return_url,
            "created_at": datetime.utcnow().isoformat(),
            "nonce": nonce,
        })

        key = self._make_key(state)
        self.redis.setex(key, self.ttl, data_json)

        return state

    def validate(
        self,
        state: str,
        expected_site_id: str
    ) -> Dict[str, Any]:
        """Validate and consume OAuth state token (sync version)."""
        key = self._make_key(state)

        # Use pipeline for atomic get-delete
        pipe = self.redis.pipeline()
        pipe.get(key)
        pipe.delete(key)
        results = pipe.execute()

        data_json = results[0]

        if not data_json:
            raise StateInvalidError(
                "State token is invalid or has already been used"
            )

        data = json.loads(data_json)

        if data["site_id"] != expected_site_id:
            raise StateSiteMismatchError(
                "State token was created for a different site"
            )

        return {
            "provider": data["provider"],
            "return_url": data.get("return_url"),
        }
