"""Redis-backed rate limiter for distributed deployments.

Provides sliding-window rate limiting using Redis INCR + EXPIRE.
Imported by rate_limiter.py's RateLimitMiddleware._ensure_redis().
"""

import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class RedisRateLimiter:
    """Redis-backed rate limiter with sliding window counters."""

    # Atomic INCR+EXPIRE Lua script — prevents orphaned keys on crash between the two ops
    _INCR_WITH_TTL_SCRIPT = """
    local count = redis.call('INCR', KEYS[1])
    if count == 1 then
        redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    return count
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
        burst_limit: int = 10,
        auth_requests_per_minute: int = 5,
    ):
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        self.burst_limit = burst_limit
        self.auth_requests_per_minute = auth_requests_per_minute
        self._redis = None
        self._incr_script = None

    async def _get_redis(self):
        """Lazy-load Redis client from shared module."""
        if self._redis is None:
            try:
                from .shared import get_redis_client
            except ImportError:
                from shared import get_redis_client  # type: ignore[no-redef]
            self._redis = get_redis_client()
        return self._redis

    async def _atomic_incr(self, redis, key: str, ttl: int) -> int:
        """Atomically INCR a key and set TTL on first creation via Lua script.

        Prevents orphaned keys (no TTL) if the process crashes between
        separate INCR and EXPIRE calls.
        """
        if self._incr_script is None:
            self._incr_script = redis.register_script(self._INCR_WITH_TTL_SCRIPT)
        return await self._incr_script(keys=[key], args=[ttl])

    async def check_rate_limit(
        self, client_key: str, is_auth: bool = False
    ) -> Tuple[bool, Optional[int], dict]:
        """Check if request should be allowed.

        Returns:
            (allowed, retry_after_seconds, info_dict)
        """
        redis = await self._get_redis()
        if not redis:
            return True, None, {"backend": "none"}

        try:
            if is_auth:
                return await self._check_auth_limit(redis, client_key)
            return await self._check_general_limit(redis, client_key)
        except Exception as e:
            logger.warning("Redis rate limit check failed: %s", e)
            return True, None, {"backend": "error"}

    async def _check_auth_limit(
        self, redis, client_key: str
    ) -> Tuple[bool, Optional[int], dict]:
        """Stricter limit for auth endpoints."""
        key = f"rl:auth:{client_key}"
        count = await self._atomic_incr(redis, key, 60)

        if count > self.auth_requests_per_minute:
            ttl = await redis.ttl(key)
            retry_after = max(1, ttl if ttl > 0 else 60)
            return False, retry_after, {"backend": "redis", "count": count}

        return True, None, {"backend": "redis", "count": count}

    async def _check_general_limit(
        self, redis, client_key: str
    ) -> Tuple[bool, Optional[int], dict]:
        """General rate limit: per-minute and per-hour windows."""
        # Burst check (per-second)
        burst_key = f"rl:burst:{client_key}"
        burst_count = await self._atomic_incr(redis, burst_key, 1)
        if burst_count > self.burst_limit:
            return False, 1, {"backend": "redis", "window": "burst"}

        # Per-minute check
        min_key = f"rl:min:{client_key}"
        min_count = await self._atomic_incr(redis, min_key, 60)
        if min_count > self.requests_per_minute:
            ttl = await redis.ttl(min_key)
            return False, max(1, ttl if ttl > 0 else 60), {"backend": "redis", "window": "minute"}

        # Per-hour check
        hr_key = f"rl:hr:{client_key}"
        hr_count = await self._atomic_incr(redis, hr_key, 3600)
        if hr_count > self.requests_per_hour:
            ttl = await redis.ttl(hr_key)
            return False, max(1, ttl if ttl > 0 else 3600), {"backend": "redis", "window": "hour"}

        return True, None, {"backend": "redis"}
