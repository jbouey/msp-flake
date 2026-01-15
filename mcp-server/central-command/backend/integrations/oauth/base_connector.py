"""
Base OAuth 2.0 + PKCE Connector.

SECURITY REQUIREMENT: All OAuth flows MUST use:
- PKCE (Proof Key for Code Exchange) with S256 challenge
- Single-use state tokens via OAuthStateManager
- Site-bound state validation
- Token refresh before expiry (5 minute buffer)
- SecureCredentials wrapper for all tokens

This base class provides the common OAuth flow logic.
Provider-specific connectors extend this class.

Usage:
    class GoogleWorkspaceConnector(BaseOAuthConnector):
        PROVIDER = "google_workspace"
        AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
        TOKEN_URL = "https://oauth2.googleapis.com/token"
        SCOPES = ["admin.directory.user.readonly", ...]

        async def collect_resources(self) -> List[IntegrationResource]:
            # Provider-specific resource collection
            ...
"""

import base64
import hashlib
import secrets
import logging
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from urllib.parse import urlencode, parse_qs, urlparse

import httpx

from ..secure_credentials import SecureCredentials, OAuthTokens
from ..oauth_state import OAuthStateManager, StateInvalidError, StateSiteMismatchError
from ..credential_vault import CredentialVault
from ..audit_logger import IntegrationAuditLogger
from ..tenant_isolation import TenantIsolation

logger = logging.getLogger(__name__)


# Configuration
TOKEN_REFRESH_BUFFER_SECONDS = 300  # Refresh 5 minutes before expiry
PKCE_CODE_VERIFIER_LENGTH = 64  # 64 bytes = 512 bits
HTTP_TIMEOUT_SECONDS = 30
MAX_RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = [1, 2, 5]


class OAuthError(Exception):
    """Base exception for OAuth errors."""
    pass


class TokenExchangeError(OAuthError):
    """Failed to exchange authorization code for tokens."""
    pass


class TokenRefreshError(OAuthError):
    """Failed to refresh access token."""
    pass


class TokenExpiredError(OAuthError):
    """Token has expired and refresh failed."""
    pass


class ProviderAPIError(OAuthError):
    """Provider API returned an error."""
    def __init__(self, message: str, status_code: int = None, response: dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


@dataclass
class OAuthConfig:
    """Configuration for an OAuth provider."""
    client_id: str
    client_secret: SecureCredentials
    redirect_uri: str
    scopes: List[str] = field(default_factory=list)
    extra_params: Dict[str, str] = field(default_factory=dict)


@dataclass
class TokenResponse:
    """OAuth token response."""
    access_token: str
    token_type: str
    expires_in: int
    refresh_token: Optional[str] = None
    scope: Optional[str] = None
    id_token: Optional[str] = None
    expires_at: Optional[datetime] = None

    def __post_init__(self):
        if not self.expires_at and self.expires_in:
            self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.expires_in)


@dataclass
class PKCEChallenge:
    """PKCE code verifier and challenge pair."""
    code_verifier: str
    code_challenge: str
    code_challenge_method: str = "S256"


@dataclass
class IntegrationResource:
    """A resource collected from an integration."""
    resource_type: str
    resource_id: str
    name: str
    raw_data: Dict[str, Any]
    compliance_checks: Dict[str, Any] = field(default_factory=dict)
    risk_level: str = "unknown"
    last_synced: datetime = field(default_factory=datetime.utcnow)


class BaseOAuthConnector(ABC):
    """
    Base class for OAuth 2.0 + PKCE connectors.

    Provides:
    - PKCE challenge generation
    - Authorization URL building
    - Token exchange
    - Token refresh with automatic retry
    - Authenticated API requests

    Subclasses must implement:
    - PROVIDER: Provider identifier
    - AUTH_URL: OAuth authorization endpoint
    - TOKEN_URL: OAuth token endpoint
    - SCOPES: Required OAuth scopes
    - collect_resources(): Provider-specific resource collection
    """

    # Override in subclasses
    PROVIDER: str = ""
    AUTH_URL: str = ""
    TOKEN_URL: str = ""
    SCOPES: List[str] = []
    USER_INFO_URL: Optional[str] = None

    def __init__(
        self,
        integration_id: str,
        site_id: str,
        config: OAuthConfig,
        credential_vault: CredentialVault,
        state_manager: OAuthStateManager,
        audit_logger: IntegrationAuditLogger,
        tokens: Optional[OAuthTokens] = None
    ):
        """
        Initialize the OAuth connector.

        Args:
            integration_id: Unique integration ID
            site_id: Site this integration belongs to
            config: OAuth configuration
            credential_vault: For encrypting/decrypting tokens
            state_manager: For OAuth state management
            audit_logger: For audit trail
            tokens: Existing tokens (for refresh)
        """
        self.integration_id = integration_id
        self.site_id = site_id
        self.config = config
        self.credential_vault = credential_vault
        self.state_manager = state_manager
        self.audit_logger = audit_logger
        self._tokens = tokens
        self._http_client: Optional[httpx.AsyncClient] = None
        self._pkce: Optional[PKCEChallenge] = None

    @classmethod
    def generate_pkce_challenge(cls) -> PKCEChallenge:
        """
        Generate PKCE code verifier and challenge.

        Uses S256 method (SHA256 hash of verifier).

        Returns:
            PKCEChallenge with verifier and challenge
        """
        # Generate cryptographically random code verifier
        code_verifier = secrets.token_urlsafe(PKCE_CODE_VERIFIER_LENGTH)

        # Create S256 challenge: BASE64URL(SHA256(code_verifier))
        verifier_bytes = code_verifier.encode('ascii')
        sha256_digest = hashlib.sha256(verifier_bytes).digest()
        code_challenge = base64.urlsafe_b64encode(sha256_digest).rstrip(b'=').decode('ascii')

        return PKCEChallenge(
            code_verifier=code_verifier,
            code_challenge=code_challenge,
            code_challenge_method="S256"
        )

    async def get_authorization_url(
        self,
        return_url: Optional[str] = None,
        integration_name: Optional[str] = None,
        extra_params: Optional[Dict[str, str]] = None
    ) -> Tuple[str, str, PKCEChallenge]:
        """
        Generate authorization URL for OAuth flow.

        Args:
            return_url: Where to redirect after OAuth completion
            integration_name: Name for the new integration
            extra_params: Additional provider-specific parameters

        Returns:
            Tuple of (authorization_url, state_token, pkce_challenge)
        """
        # Generate PKCE challenge
        pkce = self.generate_pkce_challenge()
        self._pkce = pkce

        # Generate state token (single-use, site-bound)
        state = await self.state_manager.generate(
            site_id=self.site_id,
            provider=self.PROVIDER,
            return_url=return_url,
            integration_name=integration_name,
            extra_data={
                "code_verifier": pkce.code_verifier,
                "integration_id": self.integration_id
            }
        )

        # Build authorization URL
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.SCOPES if self.SCOPES else self.config.scopes),
            "state": state,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": pkce.code_challenge_method,
            # Add provider-specific params
            **self.config.extra_params,
            **(extra_params or {})
        }

        # Some providers need access_type for refresh tokens
        if "access_type" not in params:
            params["access_type"] = "offline"

        # Prompt for consent to ensure refresh token is returned
        if "prompt" not in params:
            params["prompt"] = "consent"

        auth_url = f"{self.AUTH_URL}?{urlencode(params)}"

        logger.info(
            f"Generated auth URL for {self.PROVIDER}: "
            f"integration={self.integration_id} site={self.site_id}"
        )

        return auth_url, state, pkce

    async def exchange_code(
        self,
        code: str,
        state: str,
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> TokenResponse:
        """
        Exchange authorization code for tokens.

        Validates state token (single-use, site-bound) and uses PKCE.

        Args:
            code: Authorization code from OAuth callback
            state: State token from OAuth callback
            user_id: User who initiated the flow (for audit)
            ip_address: Client IP (for audit)

        Returns:
            TokenResponse with access_token, refresh_token, etc.

        Raises:
            StateInvalidError: Invalid or reused state
            StateSiteMismatchError: State belongs to different site
            TokenExchangeError: Failed to exchange code
        """
        # Validate state (single-use, site-bound)
        try:
            state_data = await self.state_manager.validate(
                state=state,
                expected_site_id=self.site_id
            )
        except (StateInvalidError, StateSiteMismatchError) as e:
            # Log security event
            await self.audit_logger.log_oauth_failure(
                site_id=self.site_id,
                integration_id=self.integration_id,
                provider=self.PROVIDER,
                error=str(e),
                error_code="state_validation_failed",
                user_id=user_id,
                ip_address=ip_address
            )
            raise

        # Get PKCE verifier from state
        code_verifier = state_data.get("extra_data", {}).get("code_verifier")
        if not code_verifier:
            await self.audit_logger.log_oauth_failure(
                site_id=self.site_id,
                integration_id=self.integration_id,
                provider=self.PROVIDER,
                error="Missing PKCE code verifier in state",
                error_code="missing_pkce_verifier",
                user_id=user_id,
                ip_address=ip_address
            )
            raise TokenExchangeError("Missing PKCE code verifier in state")

        # Exchange code for tokens
        token_data = {
            "grant_type": "authorization_code",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret.get("client_secret"),
            "code": code,
            "redirect_uri": self.config.redirect_uri,
            "code_verifier": code_verifier
        }

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            try:
                response = await client.post(
                    self.TOKEN_URL,
                    data=token_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )

                if response.status_code != 200:
                    error_data = response.json() if response.content else {}
                    error_msg = error_data.get("error_description", error_data.get("error", "Unknown error"))

                    await self.audit_logger.log_oauth_failure(
                        site_id=self.site_id,
                        integration_id=self.integration_id,
                        provider=self.PROVIDER,
                        error=error_msg,
                        error_code=error_data.get("error", "token_exchange_failed"),
                        user_id=user_id,
                        ip_address=ip_address
                    )

                    raise TokenExchangeError(f"Token exchange failed: {error_msg}")

                data = response.json()

            except httpx.RequestError as e:
                await self.audit_logger.log_oauth_failure(
                    site_id=self.site_id,
                    integration_id=self.integration_id,
                    provider=self.PROVIDER,
                    error=str(e),
                    error_code="network_error",
                    user_id=user_id,
                    ip_address=ip_address
                )
                raise TokenExchangeError(f"Network error during token exchange: {e}")

        # Create token response
        token_response = TokenResponse(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_in=data.get("expires_in", 3600),
            refresh_token=data.get("refresh_token"),
            scope=data.get("scope"),
            id_token=data.get("id_token")
        )

        # Store tokens securely
        self._tokens = SecureCredentials(
            access_token=token_response.access_token,
            refresh_token=token_response.refresh_token,
            token_type=token_response.token_type,
            expires_at=token_response.expires_at.isoformat() if token_response.expires_at else None,
            scope=token_response.scope
        )

        # Log success
        await self.audit_logger.log_oauth_success(
            site_id=self.site_id,
            integration_id=self.integration_id,
            provider=self.PROVIDER,
            user_id=user_id,
            ip_address=ip_address
        )

        logger.info(
            f"OAuth token exchange successful: provider={self.PROVIDER} "
            f"integration={self.integration_id}"
        )

        return token_response

    async def refresh_tokens(self) -> TokenResponse:
        """
        Refresh the access token using the refresh token.

        Uses automatic retry with exponential backoff.

        Returns:
            TokenResponse with new access_token

        Raises:
            TokenRefreshError: Failed to refresh after retries
            TokenExpiredError: Refresh token is invalid/expired
        """
        if not self._tokens or not self._tokens.get("refresh_token"):
            raise TokenRefreshError("No refresh token available")

        refresh_token = self._tokens.get("refresh_token")

        token_data = {
            "grant_type": "refresh_token",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret.get("client_secret"),
            "refresh_token": refresh_token
        }

        last_error = None

        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
                    response = await client.post(
                        self.TOKEN_URL,
                        data=token_data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"}
                    )

                    if response.status_code == 200:
                        data = response.json()

                        token_response = TokenResponse(
                            access_token=data["access_token"],
                            token_type=data.get("token_type", "Bearer"),
                            expires_in=data.get("expires_in", 3600),
                            # Some providers return new refresh token
                            refresh_token=data.get("refresh_token", refresh_token),
                            scope=data.get("scope")
                        )

                        # Update stored tokens
                        self._tokens = SecureCredentials(
                            access_token=token_response.access_token,
                            refresh_token=token_response.refresh_token,
                            token_type=token_response.token_type,
                            expires_at=token_response.expires_at.isoformat() if token_response.expires_at else None,
                            scope=token_response.scope
                        )

                        # Log refresh
                        await self.audit_logger.log_token_refresh(
                            site_id=self.site_id,
                            integration_id=self.integration_id,
                            provider=self.PROVIDER
                        )

                        logger.info(
                            f"Token refresh successful: provider={self.PROVIDER} "
                            f"integration={self.integration_id}"
                        )

                        return token_response

                    elif response.status_code == 400:
                        error_data = response.json() if response.content else {}
                        error_code = error_data.get("error", "")

                        # Invalid grant means refresh token is expired/revoked
                        if error_code == "invalid_grant":
                            await self.audit_logger.log_token_refresh(
                                site_id=self.site_id,
                                integration_id=self.integration_id,
                                provider=self.PROVIDER,
                                success=False
                            )
                            raise TokenExpiredError(
                                "Refresh token is invalid or expired. Re-authentication required."
                            )

                        last_error = TokenRefreshError(
                            f"Token refresh failed: {error_data.get('error_description', error_code)}"
                        )
                    else:
                        last_error = TokenRefreshError(
                            f"Token refresh failed with status {response.status_code}"
                        )

            except httpx.RequestError as e:
                last_error = TokenRefreshError(f"Network error during refresh: {e}")

            # Backoff before retry
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt])

        # All retries failed
        await self.audit_logger.log_token_refresh(
            site_id=self.site_id,
            integration_id=self.integration_id,
            provider=self.PROVIDER,
            success=False
        )
        raise last_error or TokenRefreshError("Token refresh failed after retries")

    async def ensure_valid_token(self) -> str:
        """
        Ensure we have a valid access token, refreshing if needed.

        Refreshes token if it expires within TOKEN_REFRESH_BUFFER_SECONDS.

        Returns:
            Valid access token

        Raises:
            TokenExpiredError: If refresh fails
        """
        if not self._tokens:
            raise TokenExpiredError("No tokens available")

        expires_at_str = self._tokens.get("expires_at")
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str)
            buffer = timedelta(seconds=TOKEN_REFRESH_BUFFER_SECONDS)

            if datetime.now(timezone.utc) + buffer >= expires_at:
                logger.debug(
                    f"Token expiring soon, refreshing: provider={self.PROVIDER} "
                    f"integration={self.integration_id}"
                )
                await self.refresh_tokens()

        return self._tokens.get("access_token")

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create authenticated HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=HTTP_TIMEOUT_SECONDS,
                headers={"Accept": "application/json"}
            )
        return self._http_client

    async def api_request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Make an authenticated API request to the provider.

        Automatically handles token refresh and retries.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Full API URL
            params: Query parameters
            json: JSON body
            headers: Additional headers

        Returns:
            JSON response data

        Raises:
            ProviderAPIError: API returned an error
            TokenExpiredError: Auth failed and refresh failed
        """
        access_token = await self.ensure_valid_token()

        client = await self._get_http_client()

        request_headers = {
            "Authorization": f"Bearer {access_token}",
            **(headers or {})
        }

        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json,
                    headers=request_headers
                )

                if response.status_code == 401:
                    # Try refreshing token
                    if attempt < MAX_RETRY_ATTEMPTS - 1:
                        try:
                            access_token = (await self.refresh_tokens()).access_token
                            request_headers["Authorization"] = f"Bearer {access_token}"
                            continue
                        except TokenExpiredError:
                            raise
                    raise TokenExpiredError("Authentication failed after token refresh")

                if response.status_code >= 400:
                    error_data = response.json() if response.content else {}
                    raise ProviderAPIError(
                        f"API error: {error_data.get('error', {}).get('message', response.status_code)}",
                        status_code=response.status_code,
                        response=error_data
                    )

                return response.json()

            except httpx.RequestError as e:
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS[attempt])
                    continue
                raise ProviderAPIError(f"Network error: {e}")

        raise ProviderAPIError("Request failed after retries")

    async def api_paginate(
        self,
        method: str,
        url: str,
        items_key: str,
        params: Optional[Dict[str, Any]] = None,
        page_token_param: str = "pageToken",
        next_page_key: str = "nextPageToken",
        max_items: int = 5000
    ) -> List[Dict[str, Any]]:
        """
        Paginate through API results.

        Args:
            method: HTTP method
            url: API URL
            items_key: Key in response containing items
            params: Query parameters
            page_token_param: Parameter name for page token
            next_page_key: Response key containing next page token
            max_items: Maximum items to fetch (default 5000)

        Returns:
            List of all items
        """
        all_items = []
        params = dict(params) if params else {}

        while True:
            response = await self.api_request(method, url, params=params)

            items = response.get(items_key, [])
            all_items.extend(items)

            # Check limit
            if len(all_items) >= max_items:
                logger.warning(
                    f"Reached max items limit ({max_items}): provider={self.PROVIDER} "
                    f"integration={self.integration_id} url={url}"
                )
                all_items = all_items[:max_items]
                break

            # Check for next page
            next_page = response.get(next_page_key)
            if not next_page:
                break

            params[page_token_param] = next_page

        return all_items

    async def get_encrypted_tokens(self) -> bytes:
        """
        Get tokens encrypted with integration-specific key.

        Returns:
            Encrypted token data
        """
        if not self._tokens:
            raise ValueError("No tokens to encrypt")

        token_data = {
            "access_token": self._tokens.get("access_token"),
            "refresh_token": self._tokens.get("refresh_token"),
            "token_type": self._tokens.get("token_type"),
            "expires_at": self._tokens.get("expires_at"),
            "scope": self._tokens.get("scope")
        }

        return await self.credential_vault.encrypt_credentials(
            integration_id=self.integration_id,
            credentials=token_data
        )

    async def load_encrypted_tokens(self, encrypted_data: bytes) -> None:
        """
        Load tokens from encrypted data.

        Args:
            encrypted_data: Encrypted token data from storage
        """
        token_data = self.credential_vault.decrypt_credentials(
            self.integration_id,
            encrypted_data
        )

        # token_data is already SecureCredentials, use it directly
        self._tokens = token_data

    async def close(self) -> None:
        """Close HTTP client and cleanup."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    @abstractmethod
    async def collect_resources(self) -> List[IntegrationResource]:
        """
        Collect resources from the provider.

        Must be implemented by subclasses.

        Returns:
            List of IntegrationResource objects
        """
        pass

    @abstractmethod
    async def test_connection(self) -> Dict[str, Any]:
        """
        Test the connection and return account info.

        Must be implemented by subclasses.

        Returns:
            Dict with connection status and account info
        """
        pass

    @property
    def has_valid_tokens(self) -> bool:
        """Check if we have tokens (may need refresh)."""
        return self._tokens is not None and self._tokens.get("access_token") is not None

    @property
    def provider_name(self) -> str:
        """Get human-readable provider name."""
        return self.PROVIDER.replace("_", " ").title()
