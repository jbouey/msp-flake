"""Redis-backed rate limiter for distributed deployments.

Provides sliding-window rate limiting using Redis INCR + EXPIRE.
Imported by rate_limiter.py's RateLimitMiddleware._ensure_redis().
"""

import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class RedisRateLimiter:
    """Redis-backed rate limiter with sliding window counters."""

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

    async def _get_redis(self):
        """Lazy-load Redis client from shared module."""
        if self._redis is None:
            try:
                from .shared import get_redis_client
            except ImportError:
                from shared import get_redis_client  # type: ignore[no-redef]
            self._redis = get_redis_client()
        return self._redis

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
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)

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
        burst_count = await redis.incr(burst_key)
        if burst_count == 1:
            await redis.expire(burst_key, 1)
        if burst_count > self.burst_limit:
            return False, 1, {"backend": "redis", "window": "burst"}

        # Per-minute check
        min_key = f"rl:min:{client_key}"
        min_count = await redis.incr(min_key)
        if min_count == 1:
            await redis.expire(min_key, 60)
        if min_count > self.requests_per_minute:
            ttl = await redis.ttl(min_key)
            return False, max(1, ttl if ttl > 0 else 60), {"backend": "redis", "window": "minute"}

        # Per-hour check
        hr_key = f"rl:hr:{client_key}"
        hr_count = await redis.incr(hr_key)
        if hr_count == 1:
            await redis.expire(hr_key, 3600)
        if hr_count > self.requests_per_hour:
            ttl = await redis.ttl(hr_key)
            return False, max(1, ttl if ttl > 0 else 3600), {"backend": "redis", "window": "hour"}

        return True, None, {"backend": "redis"}
