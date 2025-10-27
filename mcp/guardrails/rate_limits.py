"""
Rate Limiting - Prevent runbook thrashing and abuse
Implements cooldown periods per client/host/action combination
"""
import time
import redis
from typing import Optional, Dict
from datetime import datetime, timedelta


class RateLimiter:
    """Rate limiting for runbook execution"""

    def __init__(
        self,
        redis_client: Optional[redis.Redis] = None,
        cooldown_seconds: int = 300  # 5 minutes default
    ):
        """
        Initialize rate limiter

        Args:
            redis_client: Redis client for distributed rate limiting
            cooldown_seconds: Default cooldown period
        """
        self.redis = redis_client
        self.cooldown_seconds = cooldown_seconds

        # In-memory fallback if Redis unavailable
        self.local_cache: Dict[str, float] = {}

    def check_and_set(
        self,
        client_id: str,
        hostname: str,
        runbook_id: str,
        cooldown_override: Optional[int] = None
    ) -> Dict:
        """
        Check if action is allowed and set cooldown if it is

        Args:
            client_id: Client identifier
            hostname: Target hostname
            runbook_id: Runbook being executed
            cooldown_override: Custom cooldown period for this action

        Returns:
            {
                "allowed": bool,
                "reason": str,
                "retry_after_seconds": int
            }
        """
        cooldown = cooldown_override or self.cooldown_seconds
        key = self._make_key(client_id, hostname, runbook_id)

        # Check if in cooldown
        if self.redis:
            return self._check_redis(key, cooldown)
        else:
            return self._check_local(key, cooldown)

    def remaining_cooldown(
        self,
        client_id: str,
        hostname: str,
        runbook_id: str
    ) -> int:
        """
        Get remaining cooldown time in seconds

        Returns:
            Seconds remaining (0 if not in cooldown)
        """
        key = self._make_key(client_id, hostname, runbook_id)

        if self.redis:
            ttl = self.redis.ttl(key)
            return max(0, ttl) if ttl > 0 else 0
        else:
            expiry = self.local_cache.get(key, 0)
            remaining = expiry - time.time()
            return max(0, int(remaining))

    def reset_cooldown(
        self,
        client_id: str,
        hostname: str,
        runbook_id: str
    ):
        """Force reset cooldown (admin override)"""
        key = self._make_key(client_id, hostname, runbook_id)

        if self.redis:
            self.redis.delete(key)
        else:
            self.local_cache.pop(key, None)

    def _make_key(self, client_id: str, hostname: str, runbook_id: str) -> str:
        """Generate cache key"""
        return f"rate:{client_id}:{hostname}:{runbook_id}"

    def _check_redis(self, key: str, cooldown: int) -> Dict:
        """Check rate limit using Redis"""
        try:
            # Check if key exists
            if self.redis.exists(key):
                ttl = self.redis.ttl(key)
                return {
                    "allowed": False,
                    "reason": "Rate limited - action in cooldown",
                    "retry_after_seconds": ttl
                }

            # Set cooldown
            self.redis.setex(key, cooldown, "1")

            return {
                "allowed": True,
                "reason": "Rate limit check passed",
                "retry_after_seconds": 0
            }

        except Exception as e:
            print(f"[rate_limiter] Redis error: {e}, falling back to local")
            return self._check_local(key, cooldown)

    def _check_local(self, key: str, cooldown: int) -> Dict:
        """Check rate limit using local memory"""
        now = time.time()

        # Check if key exists and not expired
        if key in self.local_cache:
            expiry = self.local_cache[key]
            if now < expiry:
                remaining = int(expiry - now)
                return {
                    "allowed": False,
                    "reason": "Rate limited - action in cooldown",
                    "retry_after_seconds": remaining
                }

        # Set cooldown
        self.local_cache[key] = now + cooldown

        # Cleanup expired keys periodically
        if len(self.local_cache) % 100 == 0:
            self._cleanup_expired()

        return {
            "allowed": True,
            "reason": "Rate limit check passed",
            "retry_after_seconds": 0
        }

    def _cleanup_expired(self):
        """Remove expired entries from local cache"""
        now = time.time()
        expired_keys = [k for k, v in self.local_cache.items() if v < now]

        for key in expired_keys:
            del self.local_cache[key]

        if expired_keys:
            print(f"[rate_limiter] Cleaned up {len(expired_keys)} expired entries")


class AdaptiveRateLimiter(RateLimiter):
    """
    Adaptive rate limiter that adjusts cooldown based on failure patterns
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        super().__init__(redis_client, cooldown_seconds=300)

        # Track failure counts
        self.failure_counts: Dict[str, int] = {}
        self.last_failure: Dict[str, float] = {}

    def record_execution_result(
        self,
        client_id: str,
        hostname: str,
        runbook_id: str,
        success: bool
    ):
        """
        Record execution result and adjust future cooldown

        Args:
            client_id: Client identifier
            hostname: Target hostname
            runbook_id: Runbook executed
            success: Whether execution succeeded
        """
        key = self._make_key(client_id, hostname, runbook_id)

        if success:
            # Reset failure count on success
            self.failure_counts.pop(key, None)
            self.last_failure.pop(key, None)
        else:
            # Increment failure count
            self.failure_counts[key] = self.failure_counts.get(key, 0) + 1
            self.last_failure[key] = time.time()

    def get_adaptive_cooldown(
        self,
        client_id: str,
        hostname: str,
        runbook_id: str
    ) -> int:
        """
        Calculate adaptive cooldown based on failure history

        Returns:
            Cooldown in seconds (increases with repeated failures)
        """
        key = self._make_key(client_id, hostname, runbook_id)

        # Base cooldown
        base_cooldown = 300  # 5 minutes

        # Get failure count
        failures = self.failure_counts.get(key, 0)

        if failures == 0:
            return base_cooldown
        elif failures == 1:
            return base_cooldown * 2  # 10 minutes
        elif failures == 2:
            return base_cooldown * 4  # 20 minutes
        else:
            return base_cooldown * 8  # 40 minutes (max)

    def check_and_set(
        self,
        client_id: str,
        hostname: str,
        runbook_id: str,
        cooldown_override: Optional[int] = None
    ) -> Dict:
        """Override to use adaptive cooldown"""

        if cooldown_override is None:
            # Use adaptive cooldown
            cooldown_override = self.get_adaptive_cooldown(
                client_id, hostname, runbook_id
            )

        return super().check_and_set(
            client_id, hostname, runbook_id, cooldown_override
        )


# Helper functions
def check_rate_limit(
    client_id: str,
    hostname: str,
    runbook_id: str,
    redis_client: Optional[redis.Redis] = None
) -> bool:
    """
    Quick check if action is rate limited

    Returns:
        True if allowed, False if rate limited
    """
    limiter = RateLimiter(redis_client)
    result = limiter.check_and_set(client_id, hostname, runbook_id)
    return result["allowed"]


# Testing
if __name__ == "__main__":
    print("Testing Rate Limiter\n")

    # Test with local cache (no Redis)
    limiter = RateLimiter(cooldown_seconds=5)

    client_id = "clinic-001"
    hostname = "server01"
    runbook_id = "RB-BACKUP-001"

    # First attempt should succeed
    result1 = limiter.check_and_set(client_id, hostname, runbook_id)
    print(f"Attempt 1: {result1}")
    assert result1["allowed"], "First attempt should be allowed"

    # Second attempt should be rate limited
    result2 = limiter.check_and_set(client_id, hostname, runbook_id)
    print(f"Attempt 2: {result2}")
    assert not result2["allowed"], "Second attempt should be rate limited"

    # Check remaining cooldown
    remaining = limiter.remaining_cooldown(client_id, hostname, runbook_id)
    print(f"Remaining cooldown: {remaining}s")
    assert remaining > 0, "Should have remaining cooldown"

    # Wait for cooldown to expire
    print(f"Waiting {remaining + 1}s for cooldown to expire...")
    time.sleep(remaining + 1)

    # Third attempt should succeed
    result3 = limiter.check_and_set(client_id, hostname, runbook_id)
    print(f"Attempt 3: {result3}")
    assert result3["allowed"], "Third attempt should be allowed after cooldown"

    print("\n✅ All rate limiter tests passed!")

    # Test adaptive rate limiter
    print("\nTesting Adaptive Rate Limiter\n")

    adaptive = AdaptiveRateLimiter()

    # Record failures
    adaptive.record_execution_result(client_id, hostname, runbook_id, success=False)
    cooldown1 = adaptive.get_adaptive_cooldown(client_id, hostname, runbook_id)
    print(f"Cooldown after 1 failure: {cooldown1}s")

    adaptive.record_execution_result(client_id, hostname, runbook_id, success=False)
    cooldown2 = adaptive.get_adaptive_cooldown(client_id, hostname, runbook_id)
    print(f"Cooldown after 2 failures: {cooldown2}s")

    adaptive.record_execution_result(client_id, hostname, runbook_id, success=False)
    cooldown3 = adaptive.get_adaptive_cooldown(client_id, hostname, runbook_id)
    print(f"Cooldown after 3 failures: {cooldown3}s")

    # Record success - should reset
    adaptive.record_execution_result(client_id, hostname, runbook_id, success=True)
    cooldown4 = adaptive.get_adaptive_cooldown(client_id, hostname, runbook_id)
    print(f"Cooldown after success: {cooldown4}s (reset)")

    assert cooldown2 > cooldown1, "Cooldown should increase with failures"
    assert cooldown3 > cooldown2, "Cooldown should keep increasing"
    assert cooldown4 == 300, "Cooldown should reset on success"

    print("\n✅ All adaptive rate limiter tests passed!")
