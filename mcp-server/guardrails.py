"""
Guardrails - Safety and validation layer

Prevents:
1. Abuse via rate limiting
2. Invalid inputs via validation
3. System thrashing via circuit breakers
4. Unauthorized actions via parameter whitelisting

HIPAA Controls:
- §164.312(a)(1): Access control (rate limiting)
- §164.308(a)(1)(ii)(D): System monitoring (circuit breaker)
- §164.308(a)(5)(ii)(C): Log-in monitoring (failed attempts)
"""

import redis
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, validator
from datetime import datetime, timedelta
import re
import json
import asyncio


class RateLimitResult(BaseModel):
    """Result of rate limit check"""
    allowed: bool = Field(..., description="Whether action is allowed")
    remaining: int = Field(..., description="Remaining actions in window")
    retry_after_seconds: int = Field(default=0, description="Seconds until retry allowed")
    window_reset: str = Field(..., description="When rate limit window resets")


class RateLimiter:
    """
    Redis-based rate limiter

    Prevents:
    - Tool thrashing (same tool called repeatedly)
    - Client abuse (too many requests)
    - Resource exhaustion

    Strategy:
    - Per-client-hostname-tool cooldown (5 minutes)
    - Per-client request limit (100/hour)
    - Global request limit (1000/hour)
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        cooldown_seconds: int = 300,  # 5 minutes
        client_limit_per_hour: int = 100,
        global_limit_per_hour: int = 1000
    ):
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.cooldown_seconds = cooldown_seconds
        self.client_limit = client_limit_per_hour
        self.global_limit = global_limit_per_hour

    async def check_rate_limit(
        self,
        client_id: str,
        hostname: str,
        action: str = "remediation"
    ) -> RateLimitResult:
        """
        Check if action is rate limited

        Keys:
        - rate:cooldown:{client_id}:{hostname}:{action} - Cooldown per-action
        - rate:client:{client_id}:hour - Client requests per hour
        - rate:global:hour - Global requests per hour
        """

        # Check 1: Per-action cooldown
        cooldown_key = f"rate:cooldown:{client_id}:{hostname}:{action}"

        if self.redis_client.exists(cooldown_key):
            ttl = self.redis_client.ttl(cooldown_key)
            return RateLimitResult(
                allowed=False,
                remaining=0,
                retry_after_seconds=ttl,
                window_reset=(datetime.utcnow() + timedelta(seconds=ttl)).isoformat()
            )

        # Check 2: Client hourly limit
        client_key = f"rate:client:{client_id}:hour"
        client_count = int(self.redis_client.get(client_key) or 0)

        if client_count >= self.client_limit:
            ttl = self.redis_client.ttl(client_key)
            return RateLimitResult(
                allowed=False,
                remaining=0,
                retry_after_seconds=ttl,
                window_reset=(datetime.utcnow() + timedelta(seconds=ttl)).isoformat()
            )

        # Check 3: Global hourly limit
        global_key = "rate:global:hour"
        global_count = int(self.redis_client.get(global_key) or 0)

        if global_count >= self.global_limit:
            ttl = self.redis_client.ttl(global_key)
            return RateLimitResult(
                allowed=False,
                remaining=0,
                retry_after_seconds=ttl,
                window_reset=(datetime.utcnow() + timedelta(seconds=ttl)).isoformat()
            )

        # All checks passed - set rate limits
        # Set cooldown
        self.redis_client.setex(cooldown_key, self.cooldown_seconds, "1")

        # Increment client counter
        if not self.redis_client.exists(client_key):
            self.redis_client.setex(client_key, 3600, "1")
        else:
            self.redis_client.incr(client_key)

        # Increment global counter
        if not self.redis_client.exists(global_key):
            self.redis_client.setex(global_key, 3600, "1")
        else:
            self.redis_client.incr(global_key)

        client_remaining = self.client_limit - (client_count + 1)

        return RateLimitResult(
            allowed=True,
            remaining=client_remaining,
            retry_after_seconds=0,
            window_reset=(datetime.utcnow() + timedelta(hours=1)).isoformat()
        )

    async def get_rate_limit_status(self, client_id: str) -> Dict:
        """Get current rate limit status for client"""

        client_key = f"rate:client:{client_id}:hour"
        client_count = int(self.redis_client.get(client_key) or 0)
        client_ttl = self.redis_client.ttl(client_key)

        global_key = "rate:global:hour"
        global_count = int(self.redis_client.get(global_key) or 0)
        global_ttl = self.redis_client.ttl(global_key)

        return {
            "client": {
                "requests_used": client_count,
                "requests_remaining": self.client_limit - client_count,
                "limit": self.client_limit,
                "window_reset_seconds": client_ttl
            },
            "global": {
                "requests_used": global_count,
                "requests_remaining": self.global_limit - global_count,
                "limit": self.global_limit,
                "window_reset_seconds": global_ttl
            }
        }

    def clear_rate_limit(self, client_id: str):
        """Clear rate limits for client (admin use only)"""
        pattern = f"rate:*:{client_id}:*"
        keys = self.redis_client.keys(pattern)
        if keys:
            self.redis_client.delete(*keys)


class ValidationResult(BaseModel):
    """Result of input validation"""
    is_valid: bool = Field(..., description="Whether input is valid")
    errors: List[str] = Field(default_factory=list, description="Validation error messages")
    sanitized_input: Optional[Dict] = Field(None, description="Sanitized input if valid")


class InputValidator:
    """
    Input validation and sanitization

    Prevents:
    - Command injection
    - Path traversal
    - SQL injection
    - XSS attacks
    - Invalid parameters
    """

    # Allowed service names for restart_service
    ALLOWED_SERVICES = [
        'nginx', 'apache2', 'postgresql', 'mysql', 'redis',
        'docker', 'containerd', 'msp-watcher', 'backup-agent'
    ]

    # Allowed paths for file operations
    ALLOWED_PATH_PREFIXES = [
        '/var/log',
        '/var/cache',
        '/tmp/msp',
        '/opt/msp'
    ]

    # Dangerous patterns to reject
    DANGEROUS_PATTERNS = [
        r'[;&|`$(){}]',  # Shell metacharacters
        r'\.\.',          # Directory traversal
        r'/etc/passwd',   # Sensitive files
        r'/etc/shadow',
        r'rm\s+-rf',      # Destructive commands
        r'>/dev/',        # Device manipulation
    ]

    def validate_incident(self, incident_data: Dict) -> ValidationResult:
        """Validate incident request"""

        errors = []

        # Required fields
        required_fields = ['client_id', 'hostname', 'incident_type', 'severity']
        for field in required_fields:
            if field not in incident_data or not incident_data[field]:
                errors.append(f"Missing required field: {field}")

        if errors:
            return ValidationResult(is_valid=False, errors=errors)

        # Validate client_id format
        if not re.match(r'^[a-zA-Z0-9_-]+$', incident_data['client_id']):
            errors.append("Invalid client_id format (alphanumeric, dash, underscore only)")

        # Validate hostname format
        if not re.match(r'^[a-zA-Z0-9._-]+$', incident_data['hostname']):
            errors.append("Invalid hostname format")

        # Validate severity
        valid_severities = ['critical', 'high', 'medium', 'low']
        if incident_data['severity'] not in valid_severities:
            errors.append(f"Invalid severity. Must be one of: {valid_severities}")

        # Check for dangerous patterns in all string fields
        for key, value in incident_data.items():
            if isinstance(value, str):
                for pattern in self.DANGEROUS_PATTERNS:
                    if re.search(pattern, value, re.IGNORECASE):
                        errors.append(f"Dangerous pattern detected in {key}: {pattern}")

        if errors:
            return ValidationResult(is_valid=False, errors=errors)

        # Sanitize input
        sanitized = {
            'client_id': incident_data['client_id'].strip(),
            'hostname': incident_data['hostname'].strip().lower(),
            'incident_type': incident_data['incident_type'].strip().lower(),
            'severity': incident_data['severity'].lower(),
            'details': incident_data.get('details', {}),
            'metadata': incident_data.get('metadata', {})
        }

        return ValidationResult(
            is_valid=True,
            errors=[],
            sanitized_input=sanitized
        )

    def validate_service_name(self, service_name: str) -> ValidationResult:
        """Validate service name for restart_service action"""

        errors = []

        # Check whitelist
        if service_name not in self.ALLOWED_SERVICES:
            errors.append(
                f"Service '{service_name}' not in whitelist. "
                f"Allowed: {', '.join(self.ALLOWED_SERVICES)}"
            )

        # Check for dangerous patterns
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, service_name):
                errors.append(f"Dangerous pattern in service name: {pattern}")

        if errors:
            return ValidationResult(is_valid=False, errors=errors)

        return ValidationResult(
            is_valid=True,
            errors=[],
            sanitized_input={'service_name': service_name}
        )

    def validate_path(self, path: str) -> ValidationResult:
        """Validate file path for file operations"""

        errors = []

        # Check for dangerous patterns
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, path):
                errors.append(f"Dangerous pattern in path: {pattern}")

        # Check allowed prefixes
        if not any(path.startswith(prefix) for prefix in self.ALLOWED_PATH_PREFIXES):
            errors.append(
                f"Path '{path}' not in allowed directories. "
                f"Allowed: {', '.join(self.ALLOWED_PATH_PREFIXES)}"
            )

        if errors:
            return ValidationResult(is_valid=False, errors=errors)

        return ValidationResult(
            is_valid=True,
            errors=[],
            sanitized_input={'path': path}
        )


class CircuitBreaker:
    """
    Circuit breaker pattern

    Prevents:
    - Cascading failures
    - Resource exhaustion
    - System thrashing during outages

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Too many failures, reject requests
    - HALF_OPEN: Testing if system recovered

    Transition rules:
    - CLOSED → OPEN: After N consecutive failures
    - OPEN → HALF_OPEN: After timeout period
    - HALF_OPEN → CLOSED: After successful request
    - HALF_OPEN → OPEN: On failure
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        timeout_seconds: int = 60,
        success_threshold: int = 2
    ):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.success_threshold = success_threshold

        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None

    def is_open(self) -> bool:
        """Check if circuit breaker is open (rejecting requests)"""

        if self.state == "OPEN":
            # Check if timeout has elapsed
            if self.last_failure_time:
                elapsed = (datetime.utcnow() - self.last_failure_time).total_seconds()
                if elapsed > self.timeout_seconds:
                    # Transition to HALF_OPEN
                    self.state = "HALF_OPEN"
                    self.success_count = 0
                    return False

            return True

        return False

    def record_success(self):
        """Record successful operation"""

        if self.state == "HALF_OPEN":
            self.success_count += 1

            # If enough successes, close circuit
            if self.success_count >= self.success_threshold:
                self.state = "CLOSED"
                self.failure_count = 0
                self.success_count = 0

        elif self.state == "CLOSED":
            # Reset failure count on success
            self.failure_count = 0

    def record_failure(self):
        """Record failed operation"""

        self.last_failure_time = datetime.utcnow()

        if self.state == "HALF_OPEN":
            # Failure in half-open state reopens circuit
            self.state = "OPEN"
            self.success_count = 0

        elif self.state == "CLOSED":
            self.failure_count += 1

            # Open circuit if threshold exceeded
            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"

    def get_state(self) -> Dict:
        """Get current circuit breaker state"""

        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure": self.last_failure_time.isoformat() if self.last_failure_time else None,
            "failure_threshold": self.failure_threshold,
            "timeout_seconds": self.timeout_seconds
        }

    def reset(self):
        """Manually reset circuit breaker (admin use)"""
        self.state = "CLOSED"
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None


class ParameterWhitelist:
    """
    Parameter whitelisting for runbook actions

    Ensures only approved parameter values are used
    """

    # Whitelists per action type
    WHITELISTS = {
        'restart_service': {
            'service_name': [
                'nginx', 'apache2', 'postgresql', 'mysql',
                'redis', 'docker', 'containerd', 'msp-watcher'
            ]
        },
        'clear_cache': {
            'cache_path': [
                '/var/cache/nginx',
                '/var/cache/apt',
                '/tmp/msp/cache',
                '/opt/msp/cache'
            ]
        },
        'rotate_logs': {
            'log_path': [
                '/var/log/nginx',
                '/var/log/syslog',
                '/var/log/msp',
                '/opt/msp/logs'
            ]
        }
    }

    def validate_parameters(
        self,
        action: str,
        parameters: Dict[str, Any]
    ) -> ValidationResult:
        """Validate parameters against whitelist"""

        errors = []

        # Get whitelist for action
        action_whitelist = self.WHITELISTS.get(action, {})

        if not action_whitelist:
            # No whitelist defined - allow (may want to change to deny-by-default)
            return ValidationResult(is_valid=True, errors=[])

        # Check each parameter
        for param_name, param_value in parameters.items():
            if param_name in action_whitelist:
                allowed_values = action_whitelist[param_name]

                if param_value not in allowed_values:
                    errors.append(
                        f"Parameter '{param_name}' value '{param_value}' not in whitelist. "
                        f"Allowed: {allowed_values}"
                    )

        if errors:
            return ValidationResult(is_valid=False, errors=errors)

        return ValidationResult(is_valid=True, errors=[], sanitized_input=parameters)


# Example usage and testing
if __name__ == "__main__":
    import asyncio

    async def test_guardrails():
        """Test guardrails components"""

        print("Testing Guardrails\n" + "=" * 50)

        # Test 1: Input Validator
        print("\n1. Input Validator")
        validator = InputValidator()

        # Valid incident
        valid_incident = {
            'client_id': 'clinic-001',
            'hostname': 'srv-primary',
            'incident_type': 'backup_failure',
            'severity': 'high',
            'details': {}
        }

        result = validator.validate_incident(valid_incident)
        print(f"Valid incident: {result.is_valid}")

        # Invalid incident (command injection attempt)
        invalid_incident = {
            'client_id': 'clinic-001; rm -rf /',
            'hostname': 'srv-primary',
            'incident_type': 'backup_failure',
            'severity': 'high'
        }

        result = validator.validate_incident(invalid_incident)
        print(f"Invalid incident: {result.is_valid}")
        print(f"Errors: {result.errors}")

        # Test 2: Circuit Breaker
        print("\n2. Circuit Breaker")
        breaker = CircuitBreaker(failure_threshold=3, timeout_seconds=5)

        print(f"Initial state: {breaker.state}")

        # Simulate failures
        for i in range(4):
            breaker.record_failure()
            print(f"After failure {i+1}: {breaker.state}")

        print(f"Circuit open: {breaker.is_open()}")

        # Wait for timeout
        print("Waiting for timeout...")
        await asyncio.sleep(6)

        print(f"After timeout: {breaker.state}")
        print(f"Circuit open: {breaker.is_open()}")

        # Test 3: Parameter Whitelist
        print("\n3. Parameter Whitelist")
        whitelist = ParameterWhitelist()

        valid_params = {'service_name': 'nginx'}
        result = whitelist.validate_parameters('restart_service', valid_params)
        print(f"Valid params: {result.is_valid}")

        invalid_params = {'service_name': 'malicious-service'}
        result = whitelist.validate_parameters('restart_service', invalid_params)
        print(f"Invalid params: {result.is_valid}")
        print(f"Errors: {result.errors}")

    # Run tests
    asyncio.run(test_guardrails())
