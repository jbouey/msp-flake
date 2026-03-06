"""Integration tests for Partner auth flows.

Tests the /api/partners/ endpoints:
- POST /api/partners/auth/magic — validate magic link token, return API key
- GET /api/partners/me — get partner info (requires require_partner)
- POST /api/partners/claim — claim partner invitation / provision code
- POST /api/partners/me/sites/{site_id}/credentials/{credential_id}/validate — WinRM validation

Also tests:
- Partner OAuth /api/partner-auth/ session endpoints
- Auth rejection for unauthenticated requests
"""

import json
import os
import sys
import uuid
import secrets
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio-password")
os.environ.setdefault("SIGNING_KEY_FILE", "/tmp/test-signing.key")
os.environ.setdefault("API_KEY_SECRET", "test-api-key-secret")

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mcp_server_dir = os.path.dirname(os.path.dirname(backend_dir))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
if mcp_server_dir not in sys.path:
    sys.path.insert(0, mcp_server_dir)

# Restore real fastapi/sqlalchemy/pydantic if earlier tests stubbed them.
_stub_prefixes = ("fastapi", "pydantic", "sqlalchemy", "aiohttp", "starlette")
for _mod_name in list(sys.modules):
    if any(_mod_name == p or _mod_name.startswith(p + ".") for p in _stub_prefixes):
        _mod = sys.modules[_mod_name]
        if not hasattr(_mod, "__file__") or _mod.__file__ is None:
            del sys.modules[_mod_name]

import httpx
from httpx import ASGITransport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PARTNER_ID = "44444444-4444-4444-4444-444444444444"
PARTNER_UUID = uuid.UUID(PARTNER_ID)
PARTNER_USER_ID = "55555555-5555-5555-5555-555555555555"
SITE_ID = "test-site-001"
CRED_ID = "66666666-6666-6666-6666-666666666666"
PROVISION_ID = "77777777-7777-7777-7777-777777777777"


class FakeConn:
    """Fake asyncpg connection that records queries and returns canned data."""

    def __init__(self, responses=None):
        self._responses = responses or {}
        self.executed = []

    async def fetch(self, query, *args):
        self.executed.append(("fetch", query, args))
        for key, val in self._responses.items():
            if key in query:
                return val if isinstance(val, list) else [val]
        return []

    async def fetchrow(self, query, *args):
        self.executed.append(("fetchrow", query, args))
        for key, val in self._responses.items():
            if key in query:
                if isinstance(val, list):
                    return val[0] if val else None
                return val
        return None

    async def fetchval(self, query, *args):
        self.executed.append(("fetchval", query, args))
        for key, val in self._responses.items():
            if key in query:
                return val
        return 0

    async def execute(self, query, *args):
        self.executed.append(("execute", query, args))
        return "INSERT 0 1"


class FakePool:
    """Fake asyncpg pool that yields a FakeConn."""

    def __init__(self, conn=None):
        self._conn = conn or FakeConn()

    def acquire(self):
        return _FakeAcquire(self._conn)


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass


class FakeRecord(dict):
    """Mimics asyncpg Record — subscriptable by name and attribute access."""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


def _partner_record(**overrides):
    defaults = {
        "id": PARTNER_UUID,
        "name": "Test MSP",
        "slug": "test-msp",
        "contact_email": "admin@testmsp.com",
        "status": "active",
    }
    defaults.update(overrides)
    return FakeRecord(**defaults)


def _build_partners_app(partner_override=None, pool=None):
    """Build a minimal FastAPI app with partners router.

    If pool is provided, patches get_pool at the module level so all
    direct calls (not just Depends) use the fake pool.
    """
    from fastapi import FastAPI
    from dashboard_api.partners import router as partners_router
    from dashboard_api.partners import require_partner

    app = FastAPI()
    app.include_router(partners_router)

    if partner_override is not None:
        async def _mock_partner():
            return partner_override

        app.dependency_overrides[require_partner] = _mock_partner

    return app


def _pool_patches(pool):
    """Return a combined patch context for get_pool across all modules."""
    async def _get_pool():
        return pool

    from contextlib import contextmanager

    @contextmanager
    def _patches():
        with patch("dashboard_api.partners.get_pool", new=_get_pool), \
             patch("dashboard_api.fleet.get_pool", new=_get_pool):
            yield

    return _patches()


def _build_partner_auth_app():
    """Build a minimal FastAPI app with partner-auth OAuth session router."""
    from fastapi import FastAPI
    from dashboard_api.partner_auth import public_router

    app = FastAPI()
    app.include_router(public_router, prefix="/api")
    return app


# ---------------------------------------------------------------------------
# Tests — Magic link authentication
# ---------------------------------------------------------------------------


class TestMagicLinkAuth:
    """POST /api/partners/auth/magic — validate magic link token."""

    @pytest.mark.asyncio
    async def test_valid_magic_token(self):
        """A valid magic token returns partner info and API key."""
        app = _build_partners_app()
        now = datetime.now(timezone.utc)
        magic_token = secrets.token_urlsafe(32)
        user_row = FakeRecord(
            id=uuid.UUID(PARTNER_USER_ID),
            partner_id=PARTNER_UUID,
            email="tech@testmsp.com",
            name="Tech User",
            role="admin",
            magic_token_expires=now + timedelta(hours=24),
            api_key_hash="dummy_hash",
            partner_name="Test MSP",
            slug="test-msp",
            partner_status="active",
        )
        conn = FakeConn({
            "partner_users": user_row,
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                "/api/partners/auth/magic",
                json={"token": magic_token},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["success"] is True
                assert "api_key" in data
                assert data["partner"]["name"] == "Test MSP"
                assert data["partner"]["slug"] == "test-msp"
                assert data["user"]["email"] == "tech@testmsp.com"

    @pytest.mark.asyncio
    async def test_invalid_magic_token(self):
        """An invalid magic token returns 401."""
        app = _build_partners_app()
        conn = FakeConn({})  # No matching user
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                "/api/partners/auth/magic",
                json={"token": "bogus-token"},
                )
                assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_magic_token(self):
        """An expired magic token returns 401."""
        app = _build_partners_app()
        expired_time = datetime.now(timezone.utc) - timedelta(hours=1)
        user_row = FakeRecord(
            id=uuid.UUID(PARTNER_USER_ID),
            partner_id=PARTNER_UUID,
            email="tech@testmsp.com",
            name="Tech User",
            role="admin",
            magic_token_expires=expired_time,
            api_key_hash="dummy_hash",
            partner_name="Test MSP",
            slug="test-msp",
            partner_status="active",
        )
        conn = FakeConn({
            "partner_users": user_row,
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                "/api/partners/auth/magic",
                json={"token": "some-expired-token"},
                )
                assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_inactive_partner_403(self):
        """A magic token for a suspended partner returns 403."""
        app = _build_partners_app()
        now = datetime.now(timezone.utc)
        user_row = FakeRecord(
            id=uuid.UUID(PARTNER_USER_ID),
            partner_id=PARTNER_UUID,
            email="tech@testmsp.com",
            name="Tech User",
            role="admin",
            magic_token_expires=now + timedelta(hours=24),
            api_key_hash="dummy_hash",
            partner_name="Test MSP",
            slug="test-msp",
            partner_status="suspended",
        )
        conn = FakeConn({
            "partner_users": user_row,
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                "/api/partners/auth/magic",
                json={"token": "some-token"},
                )
                assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests — Partner self-service (GET /me)
# ---------------------------------------------------------------------------


class TestPartnerMe:
    """GET /api/partners/me — get current partner info."""

    @pytest.mark.asyncio
    async def test_get_my_partner(self):
        """Authenticated partner gets their profile."""
        partner = _partner_record()
        app = _build_partners_app(partner_override=partner)
        now = datetime.now(timezone.utc)
        full_row = FakeRecord(
            id=PARTNER_UUID, name="Test MSP", slug="test-msp",
            contact_email="admin@testmsp.com", contact_phone="+1-555-1234",
            brand_name="Test MSP Brand", logo_url=None,
            primary_color="#4F46E5", revenue_share_percent=40,
            status="active", created_at=now,
        )
        provision_stats = FakeRecord(pending=3, claimed=1)
        conn = FakeConn({
            "partners": full_row,
            "sites": 5,  # fetchval for COUNT(*)
            "appliance_provisions": provision_stats,
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            with patch("dashboard_api.partners.log_partner_activity", new_callable=AsyncMock):
                transport = ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.get("/api/partners/me")
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["name"] == "Test MSP"
                    assert data["slug"] == "test-msp"
                    assert data["status"] == "active"
                    assert "provisions" in data

    @pytest.mark.asyncio
    async def test_unauthenticated_partner_401(self):
        """Request without credentials returns 401."""
        app = _build_partners_app()  # No partner override — real require_partner runs
        conn = FakeConn({})
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/partners/me")
                assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests — Provision code claiming
# ---------------------------------------------------------------------------


class TestPartnerClaim:
    """POST /api/partners/claim — claim provision code."""

    @pytest.mark.asyncio
    async def test_claim_valid_code(self):
        """Claiming a valid pending provision code returns site info."""
        app = _build_partners_app()
        now = datetime.now(timezone.utc)
        provision_row = FakeRecord(
            id=uuid.UUID(PROVISION_ID),
            partner_id=PARTNER_UUID,
            target_site_id="clinic-001",
            target_client_name="North Valley Dental",
            status="pending",
            expires_at=now + timedelta(days=7),
        )
        partner_row = FakeRecord(
            slug="test-msp", brand_name="Test MSP Brand",
            primary_color="#4F46E5", logo_url=None,
        )
        conn = FakeConn({
            "appliance_provisions": provision_row,
            "partners": partner_row,
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                "/api/partners/claim",
                json={
                    "provision_code": "AABB1122CCDD3344",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                    "hostname": "nixos-appliance",
                },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "claimed"
                assert data["site_id"] == "clinic-001"
                assert "appliance_id" in data
                assert data["partner"]["slug"] == "test-msp"

    @pytest.mark.asyncio
    async def test_claim_invalid_code_404(self):
        """Claiming a nonexistent provision code returns 404."""
        app = _build_partners_app()
        conn = FakeConn({})
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                "/api/partners/claim",
                json={
                    "provision_code": "NONEXISTENT12345",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                },
                )
                assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_claim_already_claimed_400(self):
        """Claiming an already-claimed provision code returns 400."""
        app = _build_partners_app()
        provision_row = FakeRecord(
            id=uuid.UUID(PROVISION_ID),
            partner_id=PARTNER_UUID,
            target_site_id="clinic-001",
            target_client_name="North Valley Dental",
            status="claimed",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        conn = FakeConn({
            "appliance_provisions": provision_row,
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                "/api/partners/claim",
                json={
                    "provision_code": "AABB1122CCDD3344",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                },
                )
                assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_claim_expired_code_400(self):
        """Claiming an expired provision code returns 400."""
        app = _build_partners_app()
        expired_time = datetime.now(timezone.utc) - timedelta(hours=1)
        provision_row = FakeRecord(
            id=uuid.UUID(PROVISION_ID),
            partner_id=PARTNER_UUID,
            target_site_id="clinic-001",
            target_client_name="North Valley Dental",
            status="pending",
            expires_at=expired_time,
        )
        conn = FakeConn({
            "appliance_provisions": provision_row,
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                "/api/partners/claim",
                json={
                    "provision_code": "AABB1122CCDD3344",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                },
                )
                assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests — WinRM credential validation
# ---------------------------------------------------------------------------


class TestCredentialValidation:
    """POST /api/partners/me/sites/{site_id}/credentials/{cred_id}/validate"""

    @pytest.mark.asyncio
    async def test_validate_credential_queues_order(self):
        """Valid credential validation queues an order to the appliance."""
        partner = _partner_record()
        app = _build_partners_app(partner_override=partner)
        now = datetime.now(timezone.utc)
        cred_row = FakeRecord(
            id=uuid.UUID(CRED_ID), site_id=SITE_ID,
            credential_type="domain_admin", hostname="192.168.88.250",
        )
        appliance_row = FakeRecord(appliance_id=f"{SITE_ID}-AA:BB:CC:DD:EE:FF")
        conn = FakeConn({
            "site_credentials": cred_row,
            "site_appliances": appliance_row,
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            with patch("dashboard_api.partners.log_partner_activity", new_callable=AsyncMock):
                with patch("dashboard_api.order_signing.sign_admin_order", return_value=("nonce", "sig" * 21 + "ab", '{"order":"payload"}')):
                    transport = ASGITransport(app=app)
                    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                        resp = await client.post(
                            f"/api/partners/me/sites/{SITE_ID}/credentials/{CRED_ID}/validate",
                        )
                        assert resp.status_code == 200
                        data = resp.json()
                        assert data["validation_status"] == "pending"
                        assert data["credential_id"] == CRED_ID

    @pytest.mark.asyncio
    async def test_validate_credential_not_found_404(self):
        """Validation for nonexistent credential returns 404."""
        partner = _partner_record()
        app = _build_partners_app(partner_override=partner)
        conn = FakeConn({})  # No matching credential
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                fake_cred_id = str(uuid.uuid4())
                resp = await client.post(
                f"/api/partners/me/sites/{SITE_ID}/credentials/{fake_cred_id}/validate",
                )
                assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_validate_no_appliance_still_succeeds(self):
        """Validation without an active appliance returns success with warnings."""
        partner = _partner_record()
        app = _build_partners_app(partner_override=partner)
        cred_row = FakeRecord(
            id=uuid.UUID(CRED_ID), site_id=SITE_ID,
            credential_type="domain_admin", hostname="192.168.88.250",
        )
        conn = FakeConn({
            "site_credentials": cred_row,
            # No site_appliances entry — no appliance available
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            with patch("dashboard_api.partners.log_partner_activity", new_callable=AsyncMock):
                transport = ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post(
                        f"/api/partners/me/sites/{SITE_ID}/credentials/{CRED_ID}/validate",
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["validation_status"] == "pending"
                    assert any("No active appliance" in e or "No appliance" in e
                               for e in data["result"]["errors"])


# ---------------------------------------------------------------------------
# Tests — Partner OAuth session endpoints
# ---------------------------------------------------------------------------


class TestPartnerOAuthSession:
    """Tests for /api/partner-auth/ session endpoints."""

    @pytest.mark.asyncio
    async def test_get_providers(self):
        """GET /api/partner-auth/providers returns provider availability."""
        app = _build_partner_auth_app()

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/partner-auth/providers")
            assert resp.status_code == 200
            data = resp.json()
            assert "providers" in data
            assert "microsoft" in data["providers"]
            assert "google" in data["providers"]

    @pytest.mark.asyncio
    async def test_get_me_no_session_401(self):
        """GET /api/partner-auth/me without session cookie returns 401."""
        app = _build_partner_auth_app()
        conn = FakeConn({})
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/partner-auth/me")
                assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_me_invalid_session_401(self):
        """GET /api/partner-auth/me with invalid session cookie returns 401."""
        app = _build_partner_auth_app()

        conn = FakeConn({})  # No matching session
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                "/api/partner-auth/me",
                cookies={"osiris_partner_session": "invalid-session-token"},
                )
                assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_me_valid_session(self):
        """GET /api/partner-auth/me with valid session returns partner data."""
        app = _build_partner_auth_app()
        now = datetime.now(timezone.utc)

        # Simulate valid session — get_partner_from_session checks idle timeout
        # then returns partner row
        idle_row = FakeRecord(last_used_at=now)
        partner_session_row = FakeRecord(
            id=PARTNER_UUID, name="Test MSP", slug="test-msp",
            oauth_email="admin@testmsp.com", contact_email="admin@testmsp.com",
            auth_provider="microsoft", oauth_tenant_id="tenant-123",
            brand_name="Test MSP Brand",
        )
        conn = FakeConn({
            "partner_sessions": partner_session_row,
            "last_used_at": idle_row,
        })
        pool = FakePool(conn)

        with _pool_patches(pool):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                "/api/partner-auth/me",
                cookies={"osiris_partner_session": "valid-session-token"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["name"] == "Test MSP"
                assert data["slug"] == "test-msp"
                assert data["auth_provider"] == "microsoft"

    @pytest.mark.asyncio
    async def test_logout(self):
        """POST /api/partner-auth/logout clears session cookie."""
        app = _build_partner_auth_app()
        conn = FakeConn({})
        pool = FakePool(conn)

        with _pool_patches(pool):
            with patch("dashboard_api.partner_auth.log_partner_activity", new_callable=AsyncMock):
                transport = ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/api/partner-auth/logout",
                        cookies={"osiris_partner_session": "some-session-token"},
                    )
                    assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_microsoft_login_unconfigured_503(self):
        """GET /api/partner-auth/microsoft returns 503 when not configured."""
        app = _build_partner_auth_app()

        # Ensure the env vars are empty (default)
        with patch("dashboard_api.partner_auth.MICROSOFT_CLIENT_ID", ""), \
             patch("dashboard_api.partner_auth.MICROSOFT_CLIENT_SECRET", ""):
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
                resp = await client.get("/api/partner-auth/microsoft")
                assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests — PKCE helpers (unit)
# ---------------------------------------------------------------------------


class TestPKCEHelpers:
    """Unit tests for PKCE utility functions."""

    def test_generate_pkce_pair(self):
        from dashboard_api.partner_auth import generate_pkce_pair
        verifier, challenge = generate_pkce_pair()
        assert len(verifier) > 40  # Base64 of 64 random bytes
        assert len(challenge) > 20
        assert verifier != challenge

    def test_hash_session_token_deterministic(self):
        from dashboard_api.partner_auth import hash_session_token
        token = "test-session-token"
        h1 = hash_session_token(token)
        h2 = hash_session_token(token)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex digest

    def test_generate_state_token_unique(self):
        from dashboard_api.partner_auth import generate_state_token
        t1 = generate_state_token()
        t2 = generate_state_token()
        assert t1 != t2

    def test_is_domain_allowed(self):
        from dashboard_api.partner_auth import is_domain_allowed
        assert is_domain_allowed("user@company.com", ["company.com"]) is True
        assert is_domain_allowed("user@other.com", ["company.com"]) is False
        assert is_domain_allowed("user@company.com", []) is False

    def test_is_domain_allowed_case_insensitive(self):
        from dashboard_api.partner_auth import is_domain_allowed
        assert is_domain_allowed("user@Company.COM", ["company.com"]) is True


# ---------------------------------------------------------------------------
# Tests — Partner API key helpers (unit)
# ---------------------------------------------------------------------------


class TestPartnerAPIKeyHelpers:
    """Unit tests for API key management."""

    def test_hash_api_key_deterministic(self):
        from dashboard_api.partners import hash_api_key
        key = "test-api-key-12345"
        h1 = hash_api_key(key)
        h2 = hash_api_key(key)
        assert h1 == h2
        assert len(h1) == 64

    def test_verify_api_key(self):
        from dashboard_api.partners import hash_api_key, verify_api_key
        key = "test-api-key-12345"
        key_hash = hash_api_key(key)
        assert verify_api_key(key, key_hash) is True
        assert verify_api_key("wrong-key", key_hash) is False

    def test_generate_api_key_unique(self):
        from dashboard_api.partners import generate_api_key
        k1 = generate_api_key()
        k2 = generate_api_key()
        assert k1 != k2
        assert len(k1) > 20

    def test_generate_provision_code_format(self):
        from dashboard_api.partners import generate_provision_code
        code = generate_provision_code()
        assert len(code) == 16
        assert code == code.upper()  # All uppercase hex


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
