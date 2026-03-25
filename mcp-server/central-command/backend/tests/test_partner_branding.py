"""Tests for partner white-label branding endpoints.

Tests:
- GET /api/portal/branding/{slug} — public branding (no auth)
- GET /api/partners/me/branding — partner self-service branding read
- PUT /api/partners/me/branding — partner self-service branding update
- Client session context includes partner_branding
"""

import os
import sys
import uuid
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


class FakeRecord(dict):
    """Mimics asyncpg Record — subscriptable by name and attribute access."""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


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
        return "UPDATE 1"

    def transaction(self):
        return _FakeTransaction()


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


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


def _pool_patches(pool):
    """Patch get_pool in relevant modules."""
    async def _get_pool():
        return pool

    from contextlib import contextmanager

    @contextmanager
    def _patches():
        with patch("dashboard_api.partners.get_pool", new=_get_pool), \
             patch("dashboard_api.fleet.get_pool", new=_get_pool):
            yield

    return _patches()


def _branding_record(**overrides):
    """Build a fake partner branding record."""
    defaults = {
        "brand_name": "Acme IT",
        "logo_url": "https://acme.com/logo.png",
        "primary_color": "#FF5733",
        "secondary_color": "#33FF57",
        "tagline": "IT Done Right",
        "support_email": "help@acme.com",
        "support_phone": "570-555-1234",
        "slug": "acme-it",
    }
    defaults.update(overrides)
    return FakeRecord(**defaults)


def _build_branding_public_app(pool=None):
    """Build a minimal FastAPI app with the public branding router."""
    from fastapi import FastAPI
    from dashboard_api.partners import branding_public_router

    app = FastAPI()
    app.include_router(branding_public_router)
    return app


def _build_partners_app(partner_override=None, pool=None):
    """Build a minimal FastAPI app with partners router."""
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


# ---------------------------------------------------------------------------
# Tests — Public branding endpoint
# ---------------------------------------------------------------------------


class TestPublicBranding:
    """GET /api/portal/branding/{partner_slug} — no auth required."""

    @pytest.mark.asyncio
    async def test_active_partner_returns_branding(self):
        """Returns partner branding for an active partner slug."""
        record = _branding_record()
        conn = FakeConn({"FROM partners WHERE slug": record})
        pool = FakePool(conn)
        app = _build_branding_public_app(pool)

        with _pool_patches(pool):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/portal/branding/acme-it")

        assert resp.status_code == 200
        data = resp.json()
        assert data["brand_name"] == "Acme IT"
        assert data["primary_color"] == "#FF5733"
        assert data["secondary_color"] == "#33FF57"
        assert data["tagline"] == "IT Done Right"
        assert data["support_email"] == "help@acme.com"
        assert data["support_phone"] == "570-555-1234"
        assert data["logo_url"] == "https://acme.com/logo.png"
        assert data["partner_slug"] == "acme-it"

    @pytest.mark.asyncio
    async def test_unknown_slug_returns_defaults(self):
        """Unknown slugs return OsirisCare defaults without revealing non-existence."""
        conn = FakeConn({})  # No responses — slug not found
        pool = FakePool(conn)
        app = _build_branding_public_app(pool)

        with _pool_patches(pool):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/portal/branding/nonexistent")

        assert resp.status_code == 200
        data = resp.json()
        assert data["brand_name"] == "OsirisCare"
        assert data["primary_color"] == "#0D9488"
        assert data["secondary_color"] == "#6366F1"
        assert data["tagline"] == "HIPAA Compliance Simplified"
        assert data["partner_slug"] == "nonexistent"

    @pytest.mark.asyncio
    async def test_null_fields_get_defaults(self):
        """Null branding fields fall back to OsirisCare defaults."""
        record = _branding_record(
            brand_name=None,
            primary_color=None,
            secondary_color=None,
        )
        conn = FakeConn({"FROM partners WHERE slug": record})
        pool = FakePool(conn)
        app = _build_branding_public_app(pool)

        with _pool_patches(pool):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/portal/branding/acme-it")

        assert resp.status_code == 200
        data = resp.json()
        assert data["brand_name"] == "OsirisCare"
        assert data["primary_color"] == "#0D9488"
        assert data["secondary_color"] == "#6366F1"


# ---------------------------------------------------------------------------
# Tests — Partner branding CRUD (authenticated)
# ---------------------------------------------------------------------------


class TestPartnerBrandingRead:
    """GET /api/partners/me/branding — requires partner auth."""

    @pytest.mark.asyncio
    async def test_get_branding(self):
        """Returns current branding config."""
        partner = FakeRecord(id=PARTNER_UUID, name="Test MSP", slug="test-msp",
                             status="active", user_role="admin")
        record = _branding_record()
        conn = FakeConn({"FROM partners WHERE id": record})
        pool = FakePool(conn)
        app = _build_partners_app(partner_override=partner, pool=pool)

        with _pool_patches(pool):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/partners/me/branding")

        assert resp.status_code == 200
        data = resp.json()
        assert data["brand_name"] == "Acme IT"
        assert data["primary_color"] == "#FF5733"
        assert data["secondary_color"] == "#33FF57"
        assert data["partner_slug"] == "acme-it"

    @pytest.mark.asyncio
    async def test_get_branding_unauthenticated(self):
        """Unauthenticated requests return 401."""
        app = _build_partners_app()  # No partner override
        conn = FakeConn({})
        pool = FakePool(conn)

        with _pool_patches(pool):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/partners/me/branding")

        assert resp.status_code == 401


class TestPartnerBrandingUpdate:
    """PUT /api/partners/me/branding — admin only."""

    @pytest.mark.asyncio
    async def test_update_valid_branding(self):
        """Updates branding with valid data."""
        partner = FakeRecord(id=PARTNER_UUID, name="Test MSP", slug="test-msp",
                             status="active", user_role="admin")
        conn = FakeConn({})
        pool = FakePool(conn)
        app = _build_partners_app(partner_override=partner, pool=pool)

        with _pool_patches(pool), \
             patch("dashboard_api.partners.log_partner_activity", new_callable=AsyncMock):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put("/api/partners/me/branding", json={
                    "brand_name": "New Brand",
                    "primary_color": "#AABBCC",
                    "secondary_color": "#112233",
                    "tagline": "New tagline",
                    "logo_url": "https://example.com/logo.png",
                    "support_email": "support@example.com",
                    "support_phone": "570-555-9999",
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert "brand_name" in data["updated_fields"]
        assert "primary_color" in data["updated_fields"]
        assert "secondary_color" in data["updated_fields"]
        assert "tagline" in data["updated_fields"]
        assert "logo_url" in data["updated_fields"]

        # Verify the UPDATE was executed
        update_calls = [c for c in conn.executed if c[0] == "execute" and "UPDATE partners" in c[1]]
        assert len(update_calls) == 1

    @pytest.mark.asyncio
    async def test_invalid_hex_color_rejected(self):
        """Invalid hex colors return 400."""
        partner = FakeRecord(id=PARTNER_UUID, name="Test MSP", slug="test-msp",
                             status="active", user_role="admin")
        conn = FakeConn({})
        pool = FakePool(conn)
        app = _build_partners_app(partner_override=partner, pool=pool)

        with _pool_patches(pool):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put("/api/partners/me/branding", json={
                    "primary_color": "not-a-color",
                })

        assert resp.status_code == 400
        assert "hex color" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_http_logo_url_rejected(self):
        """Non-HTTPS logo URLs return 400."""
        partner = FakeRecord(id=PARTNER_UUID, name="Test MSP", slug="test-msp",
                             status="active", user_role="admin")
        conn = FakeConn({})
        pool = FakePool(conn)
        app = _build_partners_app(partner_override=partner, pool=pool)

        with _pool_patches(pool):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put("/api/partners/me/branding", json={
                    "logo_url": "http://insecure.com/logo.png",
                })

        assert resp.status_code == 400
        assert "HTTPS" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_html_stripped_from_brand_name(self):
        """HTML tags are stripped from brand_name."""
        partner = FakeRecord(id=PARTNER_UUID, name="Test MSP", slug="test-msp",
                             status="active", user_role="admin")
        conn = FakeConn({})
        pool = FakePool(conn)
        app = _build_partners_app(partner_override=partner, pool=pool)

        with _pool_patches(pool), \
             patch("dashboard_api.partners.log_partner_activity", new_callable=AsyncMock):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put("/api/partners/me/branding", json={
                    "brand_name": "<script>alert('xss')</script>Clean Name",
                })

        assert resp.status_code == 200
        # Verify the sanitized value was used (check what was passed to execute)
        update_calls = [c for c in conn.executed if c[0] == "execute" and "UPDATE partners" in c[1]]
        assert len(update_calls) == 1
        # The first param should be the sanitized brand name
        assert update_calls[0][2][0] == "alert('xss')Clean Name"

    @pytest.mark.asyncio
    async def test_empty_body_rejected(self):
        """Empty update body (no fields) returns 400."""
        partner = FakeRecord(id=PARTNER_UUID, name="Test MSP", slug="test-msp",
                             status="active", user_role="admin")
        conn = FakeConn({})
        pool = FakePool(conn)
        app = _build_partners_app(partner_override=partner, pool=pool)

        with _pool_patches(pool):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put("/api/partners/me/branding", json={})

        assert resp.status_code == 400
        assert "No fields" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_empty_brand_name_rejected(self):
        """Setting brand_name to empty/whitespace returns 400."""
        partner = FakeRecord(id=PARTNER_UUID, name="Test MSP", slug="test-msp",
                             status="active", user_role="admin")
        conn = FakeConn({})
        pool = FakePool(conn)
        app = _build_partners_app(partner_override=partner, pool=pool)

        with _pool_patches(pool):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put("/api/partners/me/branding", json={
                    "brand_name": "   ",
                })

        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Tests — Helpers
# ---------------------------------------------------------------------------


class TestBrandingHelpers:
    """Unit tests for _validate_hex_color and _sanitize_text."""

    def test_valid_hex_colors(self):
        from dashboard_api.partners import _validate_hex_color
        assert _validate_hex_color("#AABBCC") == "#AABBCC"
        assert _validate_hex_color("#000000") == "#000000"
        assert _validate_hex_color("#ffffff") == "#ffffff"
        assert _validate_hex_color("#0D9488") == "#0D9488"

    def test_invalid_hex_colors(self):
        from dashboard_api.partners import _validate_hex_color

        with pytest.raises(Exception) as exc_info:
            _validate_hex_color("red")
        assert getattr(exc_info.value, "status_code", None) == 400

        with pytest.raises(Exception):
            _validate_hex_color("#GGG")

        with pytest.raises(Exception):
            _validate_hex_color("AABBCC")  # Missing #

        with pytest.raises(Exception):
            _validate_hex_color("#AABB")  # Too short

    def test_sanitize_text(self):
        from dashboard_api.partners import _sanitize_text
        assert _sanitize_text("Hello") == "Hello"
        assert _sanitize_text("<b>Bold</b>") == "Bold"
        assert _sanitize_text("<script>evil()</script>Clean") == "evil()Clean"
        assert _sanitize_text("  spaces  ") == "spaces"
        assert _sanitize_text("&amp; entity") == "& entity"


# ---------------------------------------------------------------------------
# Tests — Client session branding context
# ---------------------------------------------------------------------------


class TestClientSessionBranding:
    """Verify require_client_user includes partner_branding."""

    @pytest.mark.asyncio
    async def test_partner_branding_in_session_context(self):
        """Client session includes partner branding from joined partner."""
        from dashboard_api.client_portal import require_client_user

        fake_row = FakeRecord(
            user_id=uuid.uuid4(),
            email="client@practice.com",
            name="Dr. Smith",
            role="admin",
            org_id=uuid.uuid4(),
            org_name="Smith Practice",
            current_partner_id=PARTNER_UUID,
            partner_brand_name="Acme IT",
            partner_primary_color="#FF5733",
            partner_logo_url="https://acme.com/logo.png",
            partner_support_email="help@acme.com",
        )

        with patch("dashboard_api.client_portal.get_pool", new_callable=AsyncMock) as mock_pool, \
             patch("dashboard_api.client_portal.get_client_user_from_session", new_callable=AsyncMock) as mock_session:
            mock_session.return_value = fake_row

            from unittest.mock import MagicMock
            request = MagicMock()
            # Call the function directly (it's an async def, not a Depends wrapper)
            result = await require_client_user(
                request=request,
                osiris_client_session="fake-token",
            )

        assert "partner_branding" in result
        branding = result["partner_branding"]
        assert branding["brand_name"] == "Acme IT"
        assert branding["primary_color"] == "#FF5733"
        assert branding["logo_url"] == "https://acme.com/logo.png"
        assert branding["support_email"] == "help@acme.com"

        # Ensure raw partner_ fields are NOT in the top level
        assert "partner_brand_name" not in result
        assert "partner_primary_color" not in result

    @pytest.mark.asyncio
    async def test_null_partner_branding_defaults(self):
        """When partner fields are null, defaults to OsirisCare."""
        from dashboard_api.client_portal import require_client_user

        fake_row = FakeRecord(
            user_id=uuid.uuid4(),
            email="client@practice.com",
            name="Dr. Smith",
            role="admin",
            org_id=uuid.uuid4(),
            org_name="Smith Practice",
            current_partner_id=None,
            partner_brand_name=None,
            partner_primary_color=None,
            partner_logo_url=None,
            partner_support_email=None,
        )

        with patch("dashboard_api.client_portal.get_pool", new_callable=AsyncMock), \
             patch("dashboard_api.client_portal.get_client_user_from_session", new_callable=AsyncMock) as mock_session:
            mock_session.return_value = fake_row

            from unittest.mock import MagicMock
            request = MagicMock()
            result = await require_client_user(
                request=request,
                osiris_client_session="fake-token",
            )

        branding = result["partner_branding"]
        assert branding["brand_name"] == "OsirisCare"
        assert branding["primary_color"] == "#0D9488"
        assert branding["logo_url"] is None
        assert branding["support_email"] is None
