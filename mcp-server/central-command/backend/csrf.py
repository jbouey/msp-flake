"""CSRF (Cross-Site Request Forgery) protection for Central Command API.

Provides double-submit cookie pattern for CSRF protection:
1. Server generates a CSRF token and sets it in a cookie
2. Client must send the token in a header (X-CSRF-Token) on state-changing requests
3. Server validates that cookie and header match

This protects against CSRF attacks where an attacker's page tries to submit
requests to our API - they can't read our cookies, so they can't include the header.
"""

import secrets
import hashlib
import hmac
import os
import logging
from typing import Optional
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Get secret for HMAC signing of tokens
CSRF_SECRET = os.getenv("CSRF_SECRET", os.getenv("SESSION_TOKEN_SECRET", ""))


def generate_csrf_token() -> str:
    """Generate a cryptographically secure CSRF token."""
    token = secrets.token_urlsafe(32)
    # Sign with HMAC to detect tampering
    if CSRF_SECRET:
        signature = hmac.new(CSRF_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()[:16]
        return f"{token}.{signature}"
    return token


def validate_csrf_token(cookie_token: str, header_token: str) -> bool:
    """Validate that CSRF tokens match and are properly signed."""
    if not cookie_token or not header_token:
        return False

    # Constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(cookie_token, header_token):
        return False

    # Validate signature if secret is configured
    if CSRF_SECRET and "." in cookie_token:
        token, signature = cookie_token.rsplit(".", 1)
        expected = hmac.new(CSRF_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()[:16]
        if not secrets.compare_digest(signature, expected):
            return False

    return True


class CSRFMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for CSRF protection.

    Uses double-submit cookie pattern:
    - Sets CSRF token cookie on all responses
    - Validates X-CSRF-Token header matches cookie on POST/PUT/DELETE/PATCH
    """

    # Paths exempt from CSRF (API-key authenticated, webhooks, etc.)
    EXEMPT_PATHS = {
        "/health",
        "/api/appliances/checkin",  # Appliance API-key auth
        "/api/appliances/order",
        "/api/appliances/evidence",
        "/api/partners/claim",       # Appliance provision
        "/api/webhook",              # Webhooks have their own auth
    }

    # Exempt path prefixes (for API-key authenticated endpoints)
    EXEMPT_PREFIXES = (
        "/api/appliances/",
        "/api/webhook/",
    )

    # Safe methods that don't require CSRF validation
    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    COOKIE_NAME = "csrf_token"
    HEADER_NAME = "X-CSRF-Token"

    async def dispatch(self, request: Request, call_next):
        """Process request with CSRF protection."""
        path = request.url.path
        method = request.method

        # Skip CSRF for safe methods
        if method in self.SAFE_METHODS:
            response = await call_next(request)
            return self._set_csrf_cookie(request, response)

        # Skip CSRF for exempt paths
        if path in self.EXEMPT_PATHS:
            return await call_next(request)

        for prefix in self.EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Validate CSRF for state-changing methods
        cookie_token = request.cookies.get(self.COOKIE_NAME)
        header_token = request.headers.get(self.HEADER_NAME)

        if not validate_csrf_token(cookie_token, header_token):
            logger.warning(f"CSRF validation failed for {method} {path}")
            raise HTTPException(
                status_code=403,
                detail="CSRF validation failed. Refresh the page and try again."
            )

        response = await call_next(request)
        return self._set_csrf_cookie(request, response)

    def _set_csrf_cookie(self, request: Request, response: Response) -> Response:
        """Set or refresh CSRF cookie on response."""
        # Only set if not already present or about to expire
        existing = request.cookies.get(self.COOKIE_NAME)
        if not existing:
            token = generate_csrf_token()
            response.set_cookie(
                self.COOKIE_NAME,
                token,
                max_age=86400,      # 24 hours
                httponly=False,     # Must be readable by JavaScript
                secure=os.getenv("ENVIRONMENT", "development") == "production",
                samesite="strict",  # Strict same-site policy
                path="/",
            )
        return response


def get_csrf_token_for_template(request: Request) -> str:
    """Get CSRF token for embedding in HTML forms/templates.

    Returns existing cookie token or generates a new one.
    """
    return request.cookies.get(CSRFMiddleware.COOKIE_NAME) or generate_csrf_token()
