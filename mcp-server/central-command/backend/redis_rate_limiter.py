"""Distributed rate limiting using Redis.

Provides a Redis-backed sliding window rate limiter that works across
multiple API server instances. Falls back to in-memory if Redis unavailable.

Uses sorted sets for efficient sliding window implementation:
- Key: rate:{client_key}:{window}
- Score: request timestamp
- Value: unique request ID
"""

import time
import os
import logging
from typing import Optional, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)

# Try to import Redis
try:
    import redis.asyncio as redis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False
    logger.warning("redis library not installed, using in-memory rate limiting")


class RedisRateLimiter:
    """Distributed rate limiter using Redis sorted sets."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
        burst_limit: int = 10,
        auth_requests_per_minute: int = 5,
    ):
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        self.burst_limit = burst_limit
        self.auth_requests_per_minute = auth_requests_per_minute

        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._redis: Optional[redis.Redis] = None
        self._use_fallback = False

        # In-memory fallback
        self._fallback_minute: dict = defaultdict(list)
        self._fallback_hour: dict = defaultdict(list)
        self._fallback_burst: dict = {}

    async def _get_redis(self) -> Optional[redis.Redis]:
        """Get Redis connection, lazily initialized."""
        if self._use_fallback:
            return None

        if not HAS_REDIS:
            self._use_fallback = True
            return None

        if self._redis is None:
            try:
                self._redis = await redis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                )
                # Test connection
                await self._redis.ping()
                logger.info("Redis rate limiter connected")
            except Exception as e:
                logger.warning(f"Redis connection failed, using in-memory fallback: {e}")
                self._use_fallback = True
                return None

        return self._redis

    async def check_rate_limit(
        self,
        client_key: str,
        is_auth_endpoint: bool = False
    ) -> Tuple[bool, Optional[int], dict]:
        """
        Check if request should be allowed.

        Args:
            client_key: Unique client identifier (IP:token_prefix)
            is_auth_endpoint: Use stricter limits for auth endpoints

        Returns:
            (allowed, retry_after_seconds, metadata) - True if allowed
        """
        redis_client = await self._get_redis()

        if redis_client:
            return await self._check_redis(redis_client, client_key, is_auth_endpoint)
        else:
            return self._check_fallback(client_key, is_auth_endpoint)

    async def _check_redis(
        self,
        redis_client: redis.Redis,
        client_key: str,
        is_auth_endpoint: bool
    ) -> Tuple[bool, Optional[int], dict]:
        """Check rate limit using Redis sorted sets."""
        now = time.time()
        now_ms = int(now * 1000)

        # Keys for different windows
        minute_key = f"rate:minute:{client_key}"
        hour_key = f"rate:hour:{client_key}"
        burst_key = f"rate:burst:{client_key}"

        pipe = redis_client.pipeline()

        # Remove old entries
        minute_cutoff = now - 60
        hour_cutoff = now - 3600

        pipe.zremrangebyscore(minute_key, 0, minute_cutoff)
        pipe.zremrangebyscore(hour_key, 0, hour_cutoff)

        # Count current entries
        pipe.zcard(minute_key)
        pipe.zcard(hour_key)

        # Check burst (requests in last second)
        pipe.get(burst_key)

        results = await pipe.execute()
        minute_count = results[2]
        hour_count = results[3]
        burst_data = results[4]

        # Determine limits
        minute_limit = self.auth_requests_per_minute if is_auth_endpoint else self.requests_per_minute

        # Check burst limit
        if burst_data:
            burst_ts, burst_count = burst_data.split(":")
            if now - float(burst_ts) < 1 and int(burst_count) >= self.burst_limit:
                return False, 1, {"reason": "burst", "count": int(burst_count)}

        # Check minute limit
        if minute_count >= minute_limit:
            retry_after = 60 - int(now % 60)
            return False, retry_after, {"reason": "minute", "count": minute_count}

        # Check hour limit
        if hour_count >= self.requests_per_hour:
            retry_after = 3600 - int(now % 3600)
            return False, retry_after, {"reason": "hour", "count": hour_count}

        # Record request
        pipe2 = redis_client.pipeline()
        pipe2.zadd(minute_key, {str(now_ms): now})
        pipe2.zadd(hour_key, {str(now_ms): now})
        pipe2.expire(minute_key, 120)  # Expire after 2 minutes
        pipe2.expire(hour_key, 7200)   # Expire after 2 hours

        # Update burst counter
        if burst_data:
            burst_ts, burst_count = burst_data.split(":")
            if now - float(burst_ts) < 1:
                pipe2.set(burst_key, f"{burst_ts}:{int(burst_count) + 1}", ex=2)
            else:
                pipe2.set(burst_key, f"{now}:1", ex=2)
        else:
            pipe2.set(burst_key, f"{now}:1", ex=2)

        await pipe2.execute()

        return True, None, {"minute_count": minute_count + 1, "hour_count": hour_count + 1}

    def _check_fallback(
        self,
        client_key: str,
        is_auth_endpoint: bool
    ) -> Tuple[bool, Optional[int], dict]:
        """Fallback to in-memory rate limiting."""
        now = time.time()

        # Determine limits
        minute_limit = self.auth_requests_per_minute if is_auth_endpoint else self.requests_per_minute

        # Clean old entries
        minute_cutoff = now - 60
        hour_cutoff = now - 3600

        self._fallback_minute[client_key] = [
            ts for ts in self._fallback_minute[client_key] if ts > minute_cutoff
        ]
        self._fallback_hour[client_key] = [
            ts for ts in self._fallback_hour[client_key] if ts > hour_cutoff
        ]

        # Check burst
        burst_data = self._fallback_burst.get(client_key, (0, 0))
        if now - burst_data[0] < 1:
            if burst_data[1] >= self.burst_limit:
                return False, 1, {"reason": "burst", "count": burst_data[1]}
            self._fallback_burst[client_key] = (burst_data[0], burst_data[1] + 1)
        else:
            self._fallback_burst[client_key] = (now, 1)

        minute_count = len(self._fallback_minute[client_key])
        hour_count = len(self._fallback_hour[client_key])

        # Check minute limit
        if minute_count >= minute_limit:
            retry_after = 60 - int(now % 60)
            return False, retry_after, {"reason": "minute", "count": minute_count}

        # Check hour limit
        if hour_count >= self.requests_per_hour:
            retry_after = 3600 - int(now % 3600)
            return False, retry_after, {"reason": "hour", "count": hour_count}

        # Record request
        self._fallback_minute[client_key].append(now)
        self._fallback_hour[client_key].append(now)

        return True, None, {"minute_count": minute_count + 1, "hour_count": hour_count + 1, "fallback": True}

    async def get_client_stats(self, client_key: str) -> dict:
        """Get rate limit statistics for a client."""
        redis_client = await self._get_redis()

        if redis_client:
            now = time.time()
            minute_key = f"rate:minute:{client_key}"
            hour_key = f"rate:hour:{client_key}"

            pipe = redis_client.pipeline()
            pipe.zcard(minute_key)
            pipe.zcard(hour_key)
            results = await pipe.execute()

            return {
                "minute_count": results[0],
                "minute_limit": self.requests_per_minute,
                "hour_count": results[1],
                "hour_limit": self.requests_per_hour,
                "using_redis": True,
            }
        else:
            return {
                "minute_count": len(self._fallback_minute.get(client_key, [])),
                "minute_limit": self.requests_per_minute,
                "hour_count": len(self._fallback_hour.get(client_key, [])),
                "hour_limit": self.requests_per_hour,
                "using_redis": False,
            }

    async def reset_client(self, client_key: str) -> bool:
        """Reset rate limits for a client (admin function)."""
        redis_client = await self._get_redis()

        if redis_client:
            await redis_client.delete(
                f"rate:minute:{client_key}",
                f"rate:hour:{client_key}",
                f"rate:burst:{client_key}",
            )
            return True
        else:
            self._fallback_minute.pop(client_key, None)
            self._fallback_hour.pop(client_key, None)
            self._fallback_burst.pop(client_key, None)
            return True


# Global instance
_redis_rate_limiter: Optional[RedisRateLimiter] = None


async def get_redis_rate_limiter() -> RedisRateLimiter:
    """Get or create global Redis rate limiter instance."""
    global _redis_rate_limiter
    if _redis_rate_limiter is None:
        _redis_rate_limiter = RedisRateLimiter()
    return _redis_rate_limiter
