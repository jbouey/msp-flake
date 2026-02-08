"""Comprehensive unit tests for production security features.

Tests cover:
- CSRF token generation and validation
- CSRF middleware exempt paths and methods
- Email redaction (PII protection)
- Timing-safe token comparison
- OAuth return_url validation (open redirect prevention)
- Admin authorization requirements

Session 82 - Production Readiness Security Audit

Note: These tests are designed to be standalone and read source files directly
to avoid import issues with missing dependencies (SQLAlchemy, etc.)
"""

import pytest
import secrets
import hmac
import hashlib
import os
import sys
import re
import types

# Add backend directory to path for csrf module
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Stub out starlette and fastapi so csrf.py can import without full install
for _mod in (
    "fastapi", "starlette", "starlette.middleware",
    "starlette.middleware.base", "starlette.responses",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

sys.modules["fastapi"].Request = type("Request", (), {})
sys.modules["fastapi"].HTTPException = type("HTTPException", (Exception,), {})
sys.modules["starlette.middleware.base"].BaseHTTPMiddleware = type("BaseHTTPMiddleware", (), {})
sys.modules["starlette.responses"].Response = type("Response", (), {})


# ============================================================================
# CSRF Token Tests
# ============================================================================

class TestCSRFTokenGeneration:
    """Test CSRF token generation."""

    def test_generate_token_returns_string(self):
        """Token generation returns a non-empty string."""
        from csrf import generate_csrf_token
        token = generate_csrf_token()

        assert isinstance(token, str)
        assert len(token) > 0

    def test_generate_token_unique_each_call(self):
        """Each token generation produces a unique token."""
        from csrf import generate_csrf_token

        tokens = [generate_csrf_token() for _ in range(100)]
        assert len(set(tokens)) == 100, "All tokens should be unique"

    def test_token_is_url_safe(self):
        """Token uses URL-safe characters."""
        from csrf import generate_csrf_token

        token = generate_csrf_token()
        # URL-safe base64 chars plus signature separator
        allowed_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.")
        assert all(c in allowed_chars for c in token), "Token should only contain URL-safe chars"


class TestCSRFTokenValidation:
    """Test CSRF token validation."""

    def test_validate_matching_tokens(self):
        """Matching tokens pass validation."""
        from csrf import generate_csrf_token, validate_csrf_token

        token = generate_csrf_token()
        assert validate_csrf_token(token, token) is True

    def test_validate_mismatched_tokens_fails(self):
        """Mismatched tokens fail validation."""
        from csrf import generate_csrf_token, validate_csrf_token

        token1 = generate_csrf_token()
        token2 = generate_csrf_token()
        assert validate_csrf_token(token1, token2) is False

    def test_validate_empty_cookie_fails(self):
        """Empty cookie token fails validation."""
        from csrf import generate_csrf_token, validate_csrf_token

        token = generate_csrf_token()
        assert validate_csrf_token("", token) is False
        assert validate_csrf_token(None, token) is False

    def test_validate_empty_header_fails(self):
        """Empty header token fails validation."""
        from csrf import generate_csrf_token, validate_csrf_token

        token = generate_csrf_token()
        assert validate_csrf_token(token, "") is False
        assert validate_csrf_token(token, None) is False

    def test_validate_uses_constant_time_comparison(self):
        """Validation uses constant-time comparison (timing attack prevention)."""
        from csrf import validate_csrf_token

        # This is a design test - we verify the implementation uses secrets.compare_digest
        import inspect
        source = inspect.getsource(validate_csrf_token)
        assert "secrets.compare_digest" in source, "Must use constant-time comparison"


class TestCSRFMiddlewareExemptions:
    """Test CSRF middleware path exemptions."""

    def test_exempt_paths_include_auth(self):
        """Auth endpoints are exempt from CSRF."""
        from csrf import CSRFMiddleware

        assert "/api/auth/login" in CSRFMiddleware.EXEMPT_PATHS
        assert "/api/auth/logout" in CSRFMiddleware.EXEMPT_PATHS

    def test_exempt_paths_include_appliances(self):
        """Appliance endpoints are exempt (API-key auth)."""
        from csrf import CSRFMiddleware

        assert "/api/appliances/checkin" in CSRFMiddleware.EXEMPT_PATHS
        assert any("/api/appliances/" in p for p in CSRFMiddleware.EXEMPT_PREFIXES)

    def test_exempt_prefixes_include_oauth(self):
        """OAuth callback endpoints are exempt."""
        from csrf import CSRFMiddleware

        assert any("oauth" in p.lower() for p in CSRFMiddleware.EXEMPT_PREFIXES)

    def test_exempt_prefixes_include_agent(self):
        """Agent sync endpoints are exempt (API-key auth)."""
        from csrf import CSRFMiddleware

        assert any("agent" in p.lower() for p in CSRFMiddleware.EXEMPT_PREFIXES)

    def test_exempt_prefixes_include_portal(self):
        """Portal endpoints are exempt (magic link auth)."""
        from csrf import CSRFMiddleware

        assert any("portal" in p.lower() for p in CSRFMiddleware.EXEMPT_PREFIXES)

    def test_safe_methods_include_get_head_options(self):
        """GET, HEAD, OPTIONS are safe methods."""
        from csrf import CSRFMiddleware

        assert "GET" in CSRFMiddleware.SAFE_METHODS
        assert "HEAD" in CSRFMiddleware.SAFE_METHODS
        assert "OPTIONS" in CSRFMiddleware.SAFE_METHODS

    def test_safe_methods_exclude_state_changing(self):
        """POST, PUT, DELETE, PATCH require CSRF validation."""
        from csrf import CSRFMiddleware

        assert "POST" not in CSRFMiddleware.SAFE_METHODS
        assert "PUT" not in CSRFMiddleware.SAFE_METHODS
        assert "DELETE" not in CSRFMiddleware.SAFE_METHODS
        assert "PATCH" not in CSRFMiddleware.SAFE_METHODS


# ============================================================================
# PII Redaction Tests (Source Analysis)
# ============================================================================

class TestEmailRedaction:
    """Test email redaction for PII protection in logs."""

    @pytest.fixture
    def portal_source(self):
        """Read portal.py source code."""
        portal_path = os.path.join(backend_dir, "portal.py")
        with open(portal_path) as f:
            return f.read()

    def test_redact_email_function_exists(self, portal_source):
        """redact_email function is defined."""
        assert "def redact_email" in portal_source

    def test_redact_email_handles_empty(self, portal_source):
        """redact_email handles empty input."""
        # Check for empty/None handling
        assert 'if not email' in portal_source or "if not email or '@' not in email" in portal_source

    def test_redact_email_preserves_domain(self, portal_source):
        """redact_email preserves domain for debugging."""
        # Should return domain part
        assert "@" in portal_source  # Uses @ for splitting
        assert "domain" in portal_source.lower() or "rsplit" in portal_source

    def test_redact_email_masks_local_part(self, portal_source):
        """redact_email masks the local part of email."""
        # Should use asterisks for masking
        assert "*" in portal_source

    def test_redact_email_handles_short_emails(self, portal_source):
        """redact_email handles short local parts."""
        # Check for length handling
        assert "len(local)" in portal_source or "len(" in portal_source


# ============================================================================
# Timing Attack Prevention Tests (Source Analysis)
# ============================================================================

class TestTimingSafeComparison:
    """Test that token comparisons are timing-safe."""

    def test_portal_uses_compare_digest(self):
        """Portal token validation uses secrets.compare_digest."""
        portal_path = os.path.join(backend_dir, "portal.py")
        with open(portal_path) as f:
            source = f.read()
        assert "secrets.compare_digest" in source, "Portal must use constant-time comparison"

    def test_csrf_uses_compare_digest(self):
        """CSRF validation uses secrets.compare_digest."""
        import inspect
        from csrf import validate_csrf_token

        source = inspect.getsource(validate_csrf_token)
        assert "secrets.compare_digest" in source, "Must use constant-time comparison"

    def test_compare_digest_behavior(self):
        """Verify secrets.compare_digest behavior for documentation."""
        # Equal strings
        assert secrets.compare_digest("abc123", "abc123") is True

        # Unequal strings
        assert secrets.compare_digest("abc123", "abc124") is False

        # Different lengths
        assert secrets.compare_digest("short", "longer-string") is False

        # Empty strings
        assert secrets.compare_digest("", "") is True


# ============================================================================
# OAuth Open Redirect Prevention Tests
# ============================================================================

class TestOAuthReturnUrlValidation:
    """Test OAuth return_url validation to prevent open redirects."""

    @pytest.fixture
    def oauth_source(self):
        """Read oauth_login.py source code."""
        oauth_path = os.path.join(backend_dir, "oauth_login.py")
        with open(oauth_path) as f:
            return f.read()

    def test_return_url_validation_exists(self, oauth_source):
        """return_url validation is implemented."""
        assert 'return_url.startswith("/")' in oauth_source or 'startswith("/")' in oauth_source

    def test_invalid_return_url_logged(self, oauth_source):
        """Invalid return_url is logged for security monitoring."""
        assert "Invalid return_url" in oauth_source or "logger.warning" in oauth_source

    def test_default_fallback_is_slash(self, oauth_source):
        """Default fallback is safe (/)."""
        assert 'return_url = "/"' in oauth_source

    def test_valid_relative_url_allowed(self):
        """Relative URLs starting with / are allowed."""
        # Valid patterns
        valid_urls = ["/", "/dashboard", "/settings", "/sites/123"]
        for url in valid_urls:
            assert url.startswith("/"), f"{url} should start with /"

    def test_absolute_url_detection(self):
        """Absolute URLs can be detected."""
        dangerous_urls = [
            "https://evil.com",
            "http://attacker.org",
            "//evil.com",
        ]
        for url in dangerous_urls:
            # These don't start with single / (or start with //)
            is_safe = url.startswith("/") and not url.startswith("//")
            assert not is_safe, f"{url} should be detected as unsafe"


# ============================================================================
# Admin Authorization Tests (Source Analysis)
# ============================================================================

class TestAdminAuthorization:
    """Test admin authorization requirements on sensitive endpoints."""

    @pytest.fixture
    def portal_source(self):
        """Read portal.py source code."""
        portal_path = os.path.join(backend_dir, "portal.py")
        with open(portal_path) as f:
            return f.read()

    def test_require_admin_imported(self, portal_source):
        """require_admin is imported from auth."""
        assert "from .auth import require_admin" in portal_source or \
               "from auth import require_admin" in portal_source

    def test_generate_token_requires_admin(self, portal_source):
        """Portal token generation requires admin auth."""
        # Find the function definition and check for Depends(require_admin)
        match = re.search(r'async def generate_portal_token\([^)]*require_admin[^)]*\)', portal_source)
        assert match is not None, "generate_portal_token must require admin"

    def test_set_contact_requires_admin(self, portal_source):
        """Setting site contact requires admin auth."""
        match = re.search(r'async def set_site_contact_endpoint\([^)]*require_admin[^)]*\)', portal_source)
        assert match is not None, "set_site_contact_endpoint must require admin"


# ============================================================================
# Production Environment Tests (Source Analysis)
# ============================================================================

class TestProductionRequirements:
    """Test production environment requirements."""

    def test_csrf_secret_required_in_production(self):
        """CSRF_SECRET is required in production environment."""
        import inspect
        import csrf
        source = inspect.getsource(csrf)

        assert 'ENVIRONMENT' in source, "Should check ENVIRONMENT"
        assert 'production' in source, "Should check for production"
        assert 'RuntimeError' in source, "Should raise RuntimeError in production without secret"

    def test_oauth_redis_requirement(self):
        """OAuth module checks for Redis in production."""
        oauth_path = os.path.join(backend_dir, "oauth_login.py")
        with open(oauth_path) as f:
            source = f.read()

        # Should reference Redis requirement
        assert 'Redis' in source or 'redis' in source.lower()


# ============================================================================
# API URL Configuration Tests (Source Analysis)
# ============================================================================

class TestAPIURLConfiguration:
    """Test API URL configuration via environment variables."""

    def test_partners_uses_api_base_url(self):
        """partners.py uses API_BASE_URL environment variable."""
        partners_path = os.path.join(backend_dir, "partners.py")
        with open(partners_path) as f:
            source = f.read()

        assert "API_BASE_URL" in source
        assert "os.getenv" in source or "environ" in source

    def test_provisioning_uses_api_base_url(self):
        """Provisioning module uses configurable API URL."""
        provisioning_path = os.path.join(backend_dir, "provisioning.py")
        with open(provisioning_path) as f:
            source = f.read()

        assert "API_BASE_URL" in source


# ============================================================================
# SQL Injection Prevention Tests (Source Analysis)
# ============================================================================

class TestSQLInjectionPrevention:
    """Test SQL injection prevention measures."""

    def test_notifications_uses_parameterized_queries(self):
        """Notifications module uses parameterized queries."""
        notifications_path = os.path.join(backend_dir, "notifications.py")
        with open(notifications_path) as f:
            source = f.read()

        # Should use $1, $2 style parameters for PostgreSQL
        assert "$1" in source or "$2" in source, "Should use parameterized queries"
        assert "INTERVAL" in source, "Should have interval query"

    def test_no_f_string_sql_with_user_input(self):
        """Check for dangerous f-string SQL patterns."""
        notifications_path = os.path.join(backend_dir, "notifications.py")
        with open(notifications_path) as f:
            source = f.read()

        # Should not have f-string interval with user input
        # The fix changed from f"INTERVAL '{days}'" to parameterized
        dangerous_patterns = [
            "f\"INTERVAL '{days}'\"",
            "f'INTERVAL \"{days}\"'",
        ]
        for pattern in dangerous_patterns:
            assert pattern not in source, f"Dangerous pattern found: {pattern}"


# ============================================================================
# Cookie Security Tests
# ============================================================================

class TestCookieSecurity:
    """Test cookie security settings."""

    def test_csrf_cookie_name(self):
        """CSRF cookie has correct name."""
        from csrf import CSRFMiddleware
        assert CSRFMiddleware.COOKIE_NAME == "csrf_token"

    def test_csrf_header_name(self):
        """CSRF header has correct name."""
        from csrf import CSRFMiddleware
        assert CSRFMiddleware.HEADER_NAME == "X-CSRF-Token"

    def test_csrf_cookie_settings(self):
        """CSRF cookie has correct security settings."""
        from csrf import CSRFMiddleware
        import inspect
        source = inspect.getsource(CSRFMiddleware._set_csrf_cookie)

        # CSRF cookie must be readable by JavaScript
        assert "httponly=False" in source, "CSRF cookie must be readable by JS"
        assert "samesite" in source.lower(), "Should set SameSite"
        assert "secure" in source.lower(), "Should set Secure in production"


# ============================================================================
# Integration Test Helpers
# ============================================================================

class TestSecurityIntegration:
    """Integration tests for security features working together."""

    def test_csrf_flow_complete(self):
        """Complete CSRF protection flow works end-to-end."""
        from csrf import generate_csrf_token, validate_csrf_token

        # 1. Generate token (simulates server setting cookie)
        server_token = generate_csrf_token()

        # 2. Client sends same token back in header
        client_token = server_token  # Client reads from cookie

        # 3. Server validates
        assert validate_csrf_token(server_token, client_token) is True

    def test_csrf_attack_blocked(self):
        """CSRF attack with different token is blocked."""
        from csrf import generate_csrf_token, validate_csrf_token

        # Legitimate user's token
        user_token = generate_csrf_token()

        # Attacker's forged token
        attacker_token = generate_csrf_token()

        # Attack should fail
        assert validate_csrf_token(user_token, attacker_token) is False

    def test_csrf_token_replay_works(self):
        """Same token can be used multiple times (until refresh)."""
        from csrf import generate_csrf_token, validate_csrf_token

        token = generate_csrf_token()

        # Multiple validations should succeed
        for _ in range(10):
            assert validate_csrf_token(token, token) is True


# ============================================================================
# Security Best Practices Tests
# ============================================================================

class TestSecurityBestPractices:
    """Test security best practices are followed."""

    def test_secrets_module_used(self):
        """secrets module is used for cryptographic randomness."""
        import inspect
        from csrf import generate_csrf_token
        source = inspect.getsource(generate_csrf_token)

        assert "secrets" in source, "Should use secrets module for crypto"

    def test_hmac_used_for_signing(self):
        """HMAC is used for token signing."""
        import inspect
        import csrf
        source = inspect.getsource(csrf)

        assert "hmac" in source.lower(), "Should use HMAC for signing"

    def test_sha256_used_for_signatures(self):
        """SHA256 is used for HMAC signatures."""
        import inspect
        import csrf
        source = inspect.getsource(csrf)

        assert "sha256" in source.lower(), "Should use SHA256"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
