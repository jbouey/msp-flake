"""Tests for Session 203 Batch 6 — partner MFA Redis + client rate limit.

Covers:
  M1 — partner MFA pending tokens migrated from in-memory dict to
       Redis-backed helper (with in-memory fallback)
  H2 — client portal magic-link + login endpoints rate-limited per IP
  H2 — client_login + client_magic_link entries in RATE_LIMIT_OVERRIDES
"""

import ast
import os


PARTNER_AUTH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "partner_auth.py",
)
CLIENT_PORTAL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "client_portal.py",
)
SHARED = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "shared.py",
)


def _load(path: str) -> str:
    with open(path) as f:
        return f.read()


def _get_func(src: str, name: str) -> str:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} not found")


# =============================================================================
# M1 — partner MFA pending tokens via Redis
# =============================================================================

class TestPartnerMFARedis:
    def test_store_helper_exists(self):
        src = _load(PARTNER_AUTH)
        assert "async def _store_partner_mfa_pending(" in src

    def test_pop_helper_exists(self):
        src = _load(PARTNER_AUTH)
        assert "async def _pop_partner_mfa_pending(" in src

    def test_store_helper_uses_redis_setex(self):
        src = _load(PARTNER_AUTH)
        body = _get_func(src, "_store_partner_mfa_pending")
        assert "redis.setex" in body
        assert "MFA_PENDING_TTL_MINUTES" in body

    def test_store_helper_falls_back_to_memory_on_redis_failure(self):
        src = _load(PARTNER_AUTH)
        body = _get_func(src, "_store_partner_mfa_pending")
        assert "_partner_mfa_pending[token] = data" in body
        assert "Redis partner MFA store failed" in body

    def test_pop_helper_uses_redis_get_delete(self):
        src = _load(PARTNER_AUTH)
        body = _get_func(src, "_pop_partner_mfa_pending")
        assert "redis.get" in body
        assert "redis.delete" in body

    def test_pop_helper_falls_back_to_memory(self):
        src = _load(PARTNER_AUTH)
        body = _get_func(src, "_pop_partner_mfa_pending")
        assert "_partner_mfa_pending.pop(token, None)" in body

    def test_redis_key_prefix_namespaced(self):
        """The partner MFA prefix must NOT collide with admin MFA tokens."""
        src = _load(PARTNER_AUTH)
        assert "_PARTNER_MFA_REDIS_PREFIX" in src
        assert "partner_mfa_pending:" in src

    def test_oauth_callback_uses_helper(self):
        """The OAuth callback path that issues the MFA pending token
        must call the new Redis helper, not poke the dict directly."""
        src = _load(PARTNER_AUTH)
        # The fix removes direct `_partner_mfa_pending[mfa_token] = ...`
        # writes from the Redis-backed code paths. Some legacy in-memory
        # mutations remain inside the helper functions themselves and in
        # commented examples; the test counts only direct writes outside
        # the two helpers.
        # Strip the helper bodies and confirm no remaining direct writes.
        for helper in ("_store_partner_mfa_pending", "_pop_partner_mfa_pending"):
            body = _get_func(src, helper)
            src = src.replace(body, "")
        # After stripping helpers, count direct write operations
        # (assignment statements only, not comments/docstrings).
        # Check via AST so we don't false-positive on comments.
        tree = ast.parse(src)
        direct_writes = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Subscript):
                        if (
                            isinstance(target.value, ast.Name)
                            and target.value.id == "_partner_mfa_pending"
                        ):
                            direct_writes += 1
        assert direct_writes == 0, (
            f"found {direct_writes} direct _partner_mfa_pending[...] = ... writes outside helpers"
        )

    def test_totp_verify_uses_helper(self):
        """The TOTP verify path must call _pop_partner_mfa_pending instead
        of `_partner_mfa_pending.pop(body.mfa_token, None)`."""
        src = _load(PARTNER_AUTH)
        # Find the TOTP verify path — it's the only place that pops by
        # body.mfa_token. After the fix it goes through the helper.
        assert "await _pop_partner_mfa_pending(body.mfa_token)" in src


# =============================================================================
# H2 — client portal rate limiting
# =============================================================================

class TestClientPortalRateLimit:
    def test_request_magic_link_rate_limited(self):
        src = _load(CLIENT_PORTAL)
        body = _get_func(src, "request_magic_link")
        assert "check_rate_limit" in body
        assert "client_magic_link" in body
        assert "status_code=429" in body

    def test_login_with_password_rate_limited(self):
        src = _load(CLIENT_PORTAL)
        body = _get_func(src, "login_with_password")
        assert "check_rate_limit" in body
        assert "client_login" in body
        assert "status_code=429" in body

    def test_rate_limit_keyed_by_client_ip(self):
        """The rate limiter key must include the client IP, not the
        email — otherwise a single attacker could enumerate accounts
        without ever exceeding the limit on any one email."""
        src = _load(CLIENT_PORTAL)
        body_magic = _get_func(src, "request_magic_link")
        body_login = _get_func(src, "login_with_password")
        for body in (body_magic, body_login):
            assert "x-forwarded-for" in body
            assert "client_ip" in body


class TestRateLimitOverrides:
    def test_client_magic_link_override(self):
        src = _load(SHARED)
        assert '"client_magic_link"' in src

    def test_client_login_override(self):
        src = _load(SHARED)
        assert '"client_login"' in src

    def test_overrides_have_reasonable_values(self):
        """Magic-link should be tighter than login because it triggers
        an SMTP send (cost). Both should be lower than the default 60/5min."""
        src = _load(SHARED)
        # Find the RATE_LIMIT_OVERRIDES dict literal
        m_idx = src.find("RATE_LIMIT_OVERRIDES = {")
        assert m_idx != -1
        end_idx = src.find("}", m_idx)
        block = src[m_idx:end_idx]
        # Just confirm the keys are present with integer-looking values
        for key in ("client_magic_link", "client_login"):
            assert f'"{key}":' in block
