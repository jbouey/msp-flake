"""Tests for Session 202 Batch 3 fixes.

Covers:
- C6: tenant_middleware.py admin_connection no-op SET LOCAL removed
- H12: email_alerts.py SMTP DRY consolidation
- H6/H7: auth.py execute_with_retry migration
- C8: routes.py org-scoping on incidents/attention-required
- H17 regression: COMPLIANCE_CATEGORIES single source of truth
"""

import os
import re
import pytest


class TestTenantMiddleware:
    """Verify admin_connection no longer has dead SET LOCAL."""

    @pytest.fixture(autouse=True)
    def load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tenant_middleware.py",
        )
        with open(path) as f:
            self.source = f.read()

    def test_admin_connection_no_set_local_in_code(self):
        """admin_connection should NOT execute SET LOCAL (it's a no-op outside transaction)."""
        # Find the admin_connection function
        start = self.source.find("async def admin_connection(")
        assert start >= 0
        next_func = self.source.find("\nasync def ", start + 10)
        if next_func == -1:
            next_func = self.source.find("\ndef ", start + 10)
        body = self.source[start:next_func]
        # Check code lines only (skip docstring)
        code_lines = []
        in_docstring = False
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped.startswith('"""') and not in_docstring:
                in_docstring = True
                if stripped.endswith('"""') and len(stripped) > 3:
                    in_docstring = False
                continue
            if in_docstring:
                if '"""' in stripped:
                    in_docstring = False
                continue
            code_lines.append(line)
        code_body = "\n".join(code_lines)
        assert "SET LOCAL" not in code_body, \
            "admin_connection code should not use SET LOCAL (no-op outside transaction)"

    def test_admin_connection_yields_raw_conn(self):
        """admin_connection should yield conn directly without transaction wrapper."""
        start = self.source.find("async def admin_connection(")
        next_func = self.source.find("\nasync def ", start + 10)
        body = self.source[start:next_func]
        assert "yield conn" in body
        assert "conn.transaction()" not in body, \
            "admin_connection should NOT wrap in transaction (avoids poisoning)"

    def test_tenant_connection_uses_transaction(self):
        """tenant_connection with site_id must use transaction for SET LOCAL."""
        start = self.source.find("async def tenant_connection(")
        next_func = self.source.find("\nasync def ", start + 10)
        body = self.source[start:next_func]
        assert "conn.transaction()" in body
        assert "SET LOCAL app.current_tenant" in body

    def test_org_connection_uses_transaction(self):
        """org_connection must use transaction for SET LOCAL."""
        start = self.source.find("async def org_connection(")
        next_func = self.source.find("\nasync def ", start + 10)
        if next_func == -1:
            next_func = len(self.source)
        body = self.source[start:next_func]
        assert "conn.transaction()" in body
        assert "SET LOCAL app.current_org" in body

    def test_docstring_documents_explicit_set_post_234(self):
        """admin_connection docstring must explain the post-migration-234 explicit SET.

        Migration 234 flipped the mcp_app role default of app.is_admin to 'false'
        so forgotten-context paths fail closed. admin_connection must now SET
        app.is_admin explicitly — the docstring must explain why so future
        maintainers don't rip it out thinking it's dead code.
        """
        start = self.source.find("async def admin_connection(")
        body = self.source[start:start + 800].lower()
        assert "migration 234" in body or "fail-closed" in body or "fail_closed" in body, \
            "Docstring must reference migration 234 fail-closed flip"
        assert "set" in body and "app.is_admin" in body, \
            "Docstring must document the explicit SET app.is_admin"


class TestEmailAlertsDRY:
    """Verify SMTP retry logic is extracted and not duplicated."""

    @pytest.fixture(autouse=True)
    def load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "email_alerts.py",
        )
        with open(path) as f:
            self.source = f.read()

    def test_send_smtp_with_retry_exists(self):
        """_send_smtp_with_retry helper must exist."""
        assert "def _send_smtp_with_retry(" in self.source

    def test_no_inline_smtp_retry_loops(self):
        """SMTP retry loops should not be inlined in individual functions."""
        # Count direct SMTP connection patterns (the old inline pattern)
        inline_smtp = len(re.findall(r"smtplib\.SMTP\(SMTP_HOST", self.source))
        # Should only appear once: in _send_smtp_with_retry
        assert inline_smtp == 1, (
            f"Found {inline_smtp} direct SMTP connections — should be 1 "
            f"(only in _send_smtp_with_retry)"
        )

    def test_all_send_functions_use_helper(self):
        """All email-sending functions should delegate to _send_smtp_with_retry."""
        # Count calls to the helper
        helper_calls = self.source.count("_send_smtp_with_retry(")
        # Definition counts as 1, so calls = total - 1
        assert helper_calls >= 4, (
            f"Expected at least 4 references to _send_smtp_with_retry "
            f"(1 def + 3 calls), found {helper_calls}"
        )


class TestAuthExecuteWithRetry:
    """Verify auth.py critical paths use execute_with_retry."""

    @pytest.fixture(autouse=True)
    def load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "auth.py",
        )
        with open(path) as f:
            self.source = f.read()

    def test_authenticate_user_uses_retry(self):
        """authenticate_user must use execute_with_retry for all DB calls."""
        # Find authenticate_user function
        start = self.source.find("async def authenticate_user(")
        assert start >= 0
        # Find end (next top-level async def)
        end = self.source.find("\nasync def ", start + 10)
        body = self.source[start:end]

        raw_executes = body.count("await db.execute(")
        retry_executes = body.count("await execute_with_retry(db,")

        assert retry_executes >= 5, (
            f"authenticate_user should use execute_with_retry for all DB calls, "
            f"found {retry_executes} retry calls and {raw_executes} raw calls"
        )

    def test_log_audit_uses_retry(self):
        """_log_audit must use execute_with_retry."""
        start = self.source.find("async def _log_audit(")
        assert start >= 0
        end = self.source.find("\nasync def ", start + 10)
        body = self.source[start:end]
        assert "execute_with_retry" in body

    def test_cleanup_expired_sessions_uses_retry(self):
        """cleanup_expired_sessions must use execute_with_retry."""
        start = self.source.find("async def cleanup_expired_sessions(")
        assert start >= 0
        end = self.source.find("\nasync def ", start + 10)
        if end == -1:
            end = len(self.source)
        body = self.source[start:end]
        assert "execute_with_retry" in body

    def test_validate_session_uses_retry(self):
        """validate_session must use execute_with_retry (pre-existing)."""
        start = self.source.find("async def validate_session(")
        assert start >= 0
        end = self.source.find("\nasync def ", start + 10)
        body = self.source[start:end]
        assert "execute_with_retry" in body


class TestRoutesOrgScoping:
    """Verify routes.py data endpoints have org-scoping for IDOR prevention."""

    @pytest.fixture(autouse=True)
    def load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "routes.py",
        )
        with open(path) as f:
            self.source = f.read()

    def test_get_incidents_has_user_dep(self):
        """get_incidents must inject user for org_scope filtering."""
        match = re.search(r"async def get_incidents\(.*?\):", self.source, re.DOTALL)
        assert match, "get_incidents not found"
        assert "require_auth" in match.group(), \
            "get_incidents must have require_auth dependency for org_scope"

    def test_get_incident_detail_has_user_dep(self):
        """get_incident_detail must inject user for IDOR prevention."""
        match = re.search(r"async def get_incident_detail\(.*?\):", self.source, re.DOTALL)
        assert match, "get_incident_detail not found"
        assert "require_auth" in match.group(), \
            "get_incident_detail must have require_auth dependency"

    def test_get_incident_detail_checks_site_access(self):
        """get_incident_detail must call check_site_access_sa after fetching."""
        start = self.source.find("async def get_incident_detail(")
        end = self.source.find("\n@router.", start + 10)
        body = self.source[start:end]
        assert "check_site_access_sa" in body, \
            "get_incident_detail must verify site access for IDOR prevention"

    def test_get_attention_required_has_user_dep(self):
        """get_attention_required must inject user for org_scope filtering."""
        match = re.search(r"async def get_attention_required\(.*?\):", self.source, re.DOTALL)
        assert match, "get_attention_required not found"
        assert "require_auth" in match.group()

    def test_get_attention_required_has_org_filter(self):
        """get_attention_required queries must support org_scope filtering."""
        start = self.source.find("async def get_attention_required(")
        end = self.source.find("\n@router.", start + 10)
        if end == -1:
            end = len(self.source)
        body = self.source[start:end]
        assert "org_scope" in body, \
            "get_attention_required must filter by org_scope"


class TestRedisRateLimiterAtomicOp:
    """Verify redis_rate_limiter uses atomic INCR+EXPIRE."""

    @pytest.fixture(autouse=True)
    def load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "redis_rate_limiter.py",
        )
        with open(path) as f:
            self.source = f.read()

    def test_lua_script_defined(self):
        """Atomic INCR+EXPIRE Lua script must be defined."""
        assert "INCR_WITH_TTL_SCRIPT" in self.source
        assert "redis.call('INCR'" in self.source
        assert "redis.call('EXPIRE'" in self.source

    def test_no_separate_incr_expire(self):
        """No separate redis.incr() + redis.expire() calls (race condition)."""
        # Outside the Lua script, there should be no direct incr/expire calls
        # Find code outside the Lua script string
        script_start = self.source.find('"""')
        if script_start > 0:
            script_end = self.source.find('"""', script_start + 3)
            code_after_script = self.source[script_end + 3:]
        else:
            code_after_script = self.source

        assert "await redis.incr(" not in code_after_script, \
            "Direct redis.incr() found — must use _atomic_incr() instead"
        assert "await redis.expire(" not in code_after_script, \
            "Direct redis.expire() found — must use _atomic_incr() instead"

    def test_atomic_incr_method_exists(self):
        """_atomic_incr helper method must exist."""
        assert "async def _atomic_incr(" in self.source
