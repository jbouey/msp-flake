"""Security headers middleware for Central Command API.

Provides protection against common web vulnerabilities by adding
security headers to all responses.
"""

import os
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to all responses."""

    # Allow customization via environment
    CSP_REPORT_URI = os.getenv("CSP_REPORT_URI", "")

    # Default Content Security Policy
    # Restrictive by default, but allows our React app to function
    DEFAULT_CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "  # React requires unsafe-eval in dev
        "style-src 'self' 'unsafe-inline'; "  # Styled components need unsafe-inline
        "img-src 'self' data: https:; "  # Allow images from self, data URIs, and HTTPS
        "font-src 'self' data:; "  # Allow fonts from self and data URIs
        "connect-src 'self' https://api.osiriscare.net wss://api.osiriscare.net; "  # API connections
        "frame-ancestors 'none'; "  # Prevent clickjacking
        "base-uri 'self'; "  # Prevent base tag hijacking
        "form-action 'self'; "  # Only allow form submissions to self
        "object-src 'none'; "  # Prevent plugin loading
        "upgrade-insecure-requests"  # Force HTTPS
    )

    # Production CSP (strict)
    PRODUCTION_CSP = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self' wss://dashboard.osiriscare.net; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'; "
        "upgrade-insecure-requests"
    )

    def __init__(self, app, production_mode: bool = None):
        super().__init__(app)
        # Default to production mode unless explicitly set to development.
        # This ensures hardened CSP even when ENVIRONMENT is not set.
        if production_mode is None:
            env = os.getenv("ENVIRONMENT", "production")
            production_mode = env.lower() not in ("development", "dev", "test")
        self.production_mode = production_mode
        self.csp = self.PRODUCTION_CSP if production_mode else self.DEFAULT_CSP
        if production_mode:
            logger.info("Security headers: PRODUCTION mode (strict CSP, HSTS preload)")
        else:
            logger.warning("Security headers: DEVELOPMENT mode (relaxed CSP with unsafe-eval)")

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request and add security headers to response."""
        response = await call_next(request)

        # Defense-in-depth: set CSP at app level (Caddy also sets it at edge)
        response.headers["Content-Security-Policy"] = self.csp

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Enable XSS protection (legacy, but still useful for older browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Only allow site to be served over HTTPS
        # In production, set max-age to 1 year and include subdomains
        if self.production_mode:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )
        else:
            # Development: still enforce HSTS but without preload
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # Permissions Policy (formerly Feature-Policy)
        # Restrict access to sensitive browser features
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), "
            "camera=(), "
            "geolocation=(), "
            "gyroscope=(), "
            "magnetometer=(), "
            "microphone=(), "
            "payment=(), "
            "usb=()"
        )

        # Prevent cross-origin information leakage
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"

        # Override server identification to hide uvicorn
        response.headers["Server"] = "OsirisCare"

        return response


def create_security_headers_middleware(production_mode: bool = None) -> SecurityHeadersMiddleware:
    """Factory to create security headers middleware.

    Args:
        production_mode: Whether to use production-level restrictions.
                        If None, auto-detects from ENVIRONMENT env var.

    Returns:
        Configured SecurityHeadersMiddleware instance
    """
    return SecurityHeadersMiddleware(None, production_mode=production_mode)
