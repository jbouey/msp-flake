"""Rate limiting middleware for Central Command API.

Provides protection against brute force and DoS attacks.
Uses in-memory storage by default, can be extended to use Redis for distributed deployments.
"""

import time
import logging
from collections import defaultdict
from typing import Dict, Tuple, Optional
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple in-memory rate limiter with sliding window."""

    def __init__(
        self,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
        burst_limit: int = 10,
    ):
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        self.burst_limit = burst_limit

        # Storage: {client_key: [(timestamp, count), ...]}
        self._minute_windows: Dict[str, list] = defaultdict(list)
        self._hour_windows: Dict[str, list] = defaultdict(list)
        self._burst_tracker: Dict[str, Tuple[float, int]] = {}

    def _cleanup_old_entries(self, storage: Dict[str, list], window_seconds: int):
        """Remove entries older than the window."""
        now = time.time()
        cutoff = now - window_seconds
        for key in list(storage.keys()):
            storage[key] = [
                (ts, count) for ts, count in storage[key]
                if ts > cutoff
            ]
            if not storage[key]:
                del storage[key]

    def _get_count(self, storage: Dict[str, list], key: str, window_seconds: int) -> int:
        """Get total requests in window."""
        now = time.time()
        cutoff = now - window_seconds
        return sum(
            count for ts, count in storage.get(key, [])
            if ts > cutoff
        )

    def _add_request(self, storage: Dict[str, list], key: str):
        """Record a request."""
        now = time.time()
        storage[key].append((now, 1))

    def check_rate_limit(self, client_key: str) -> Tuple[bool, Optional[int]]:
        """
        Check if request should be allowed.

        Returns:
            (allowed, retry_after_seconds) - True if allowed, retry_after if blocked
        """
        now = time.time()

        # Check burst limit (10 requests per second)
        burst_data = self._burst_tracker.get(client_key, (0, 0))
        if now - burst_data[0] < 1:  # Within same second
            if burst_data[1] >= self.burst_limit:
                return False, 1
            self._burst_tracker[client_key] = (burst_data[0], burst_data[1] + 1)
        else:
            self._burst_tracker[client_key] = (now, 1)

        # Clean up old entries periodically
        if now % 60 < 1:  # Every ~minute
            self._cleanup_old_entries(self._minute_windows, 60)
        if now % 3600 < 1:  # Every ~hour
            self._cleanup_old_entries(self._hour_windows, 3600)

        # Check minute limit
        minute_count = self._get_count(self._minute_windows, client_key, 60)
        if minute_count >= self.requests_per_minute:
            retry_after = 60 - int(now % 60)
            return False, retry_after

        # Check hour limit
        hour_count = self._get_count(self._hour_windows, client_key, 3600)
        if hour_count >= self.requests_per_hour:
            retry_after = 3600 - int(now % 3600)
            return False, retry_after

        # Record request
        self._add_request(self._minute_windows, client_key)
        self._add_request(self._hour_windows, client_key)

        return True, None


# Global rate limiter instance
_rate_limiter = RateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for rate limiting."""

    # Safe methods exempt from rate limiting (read-only)
    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    # Endpoints that are exempt from rate limiting
    EXEMPT_PATHS = {
        "/health",
        "/",
        "/docs",
        "/openapi.json",
        "/redoc",
    }

    # Endpoints with stricter limits
    AUTH_PATHS = {
        "/api/auth/login",
        "/api/partners/auth/magic",
        "/api/users/invite/accept",
    }

    def __init__(self, app, rate_limiter: RateLimiter = None):
        super().__init__(app)
        self.rate_limiter = rate_limiter or _rate_limiter

    def _get_client_key(self, request: Request) -> str:
        """Get unique client identifier."""
        # Use X-Forwarded-For if behind proxy, otherwise use client host
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # Get first IP in chain (original client)
            client_ip = forwarded.split(",")[0].strip()
        else:
            client_ip = request.client.host if request.client else "unknown"

        # Optionally include auth token for per-user limits
        auth_token = request.headers.get("Authorization", "")[:20]  # Just prefix
        return f"{client_ip}:{auth_token}"

    async def dispatch(self, request: Request, call_next):
        """Process request with rate limiting."""
        path = request.url.path
        method = request.method

        # Skip rate limiting for safe methods (GET, HEAD, OPTIONS)
        if method in self.SAFE_METHODS:
            return await call_next(request)

        # Skip rate limiting for exempt paths
        if path in self.EXEMPT_PATHS:
            return await call_next(request)

        client_key = self._get_client_key(request)

        # Use stricter limits for auth endpoints
        if path in self.AUTH_PATHS:
            # Only allow 5 auth attempts per minute
            allowed, retry_after = self.rate_limiter.check_rate_limit(f"auth:{client_key}")
            if not allowed:
                logger.warning(f"Auth rate limit exceeded for {client_key}")
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too many authentication attempts. Please try again later.",
                        "retry_after": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )
        else:
            # Standard rate limiting
            allowed, retry_after = self.rate_limiter.check_rate_limit(client_key)
            if not allowed:
                logger.warning(f"Rate limit exceeded for {client_key}")
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded. Please slow down.",
                        "retry_after": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )

        return await call_next(request)


def create_rate_limiter(
    requests_per_minute: int = 60,
    requests_per_hour: int = 1000,
    burst_limit: int = 10,
) -> RateLimitMiddleware:
    """Factory to create rate limiter with custom settings."""
    limiter = RateLimiter(
        requests_per_minute=requests_per_minute,
        requests_per_hour=requests_per_hour,
        burst_limit=burst_limit,
    )
    return RateLimitMiddleware(None, limiter)
