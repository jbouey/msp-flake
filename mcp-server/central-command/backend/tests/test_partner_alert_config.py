"""Tests for partner alert config endpoints.

Tests:
- GET /api/partners/me/orgs/{org_id}/alert-config
- PUT /api/partners/me/orgs/{org_id}/alert-config
- PUT /api/partners/me/sites/{site_id}/alert-config
"""

import os
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Environment setup — must happen before any dashboard_api imports
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

# Remove any stubs that earlier tests may have injected for real libraries
_stub_prefixes = ("fastapi", "pydantic", "sqlalchemy", "aiohttp", "starlette")
for _mod_name in list(sys.modules):
    if any(_mod_name == p or _mod_name.startswith(p + ".") for p in _stub_prefixes):
        _mod = sys.modules[_mod_name]
        if not hasattr(_mod, "__file__") or _mod.__file__ is None:
            del sys.modules[_mod_name]

from fastapi import HTTPException  # noqa: E402


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

PARTNER_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
SITE_ID = str(uuid.uuid4())

FAKE_PARTNER = {
    "id": PARTNER_ID,
    "name": "Test MSP",
    "slug": "test-msp",
    "status": "active",
    "user_role": "admin",
}


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
    """Fake asyncpg connection with configurable responses."""

    def __init__(self, fetchrow_return=None, fetch_return=None):
        self._fetchrow_return = fetchrow_return
        self._fetch_return = fetch_return or []
        self.executed = []

    async def fetchrow(self, query, *args):
        self.executed.append(("fetchrow", query, args))
        return self._fetchrow_return

    async def fetch(self, query, *args):
        self.executed.append(("fetch", query, args))
        return self._fetch_return

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


def _make_admin_connection_patch(conn):
    """Return a context-manager patch for admin_connection that yields `conn`."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_admin_connection(pool):
        yield conn

    return _fake_admin_connection


# ---------------------------------------------------------------------------
# Test: GET /me/orgs/{org_id}/alert-config
# ---------------------------------------------------------------------------


class TestGetOrgAlertConfig:
    """GET /api/partners/me/orgs/{org_id}/alert-config"""

    @pytest.mark.asyncio
    async def test_returns_config(self):
        """Returns alert_email, cc_email, client_alert_mode, and site_overrides."""
        from dashboard_api.partners import get_partner_org_alert_config

        org_record = FakeRecord(
            alert_email="alerts@practice.com",
            cc_email="cc@practice.com",
            client_alert_mode="informed",
        )
        site_overrides = [
            FakeRecord(site_id=SITE_ID, name="Branch 1", client_alert_mode="silent"),
        ]
        conn = FakeConn(fetchrow_return=org_record, fetch_return=site_overrides)

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.partners.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.partners.admin_connection",
                   new=_make_admin_connection_patch(conn)):
            result = await get_partner_org_alert_config(
                org_id=ORG_ID,
                partner=FAKE_PARTNER,
            )

        assert result["alert_email"] == "alerts@practice.com"
        assert result["cc_email"] == "cc@practice.com"
        assert result["client_alert_mode"] == "informed"
        assert len(result["site_overrides"]) == 1
        assert result["site_overrides"][0]["site_id"] == SITE_ID
        assert result["site_overrides"][0]["client_alert_mode"] == "silent"

    @pytest.mark.asyncio
    async def test_404_when_org_not_found(self):
        """Returns 404 when org is not owned by partner."""
        from dashboard_api.partners import get_partner_org_alert_config

        conn = FakeConn(fetchrow_return=None, fetch_return=[])

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.partners.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.partners.admin_connection",
                   new=_make_admin_connection_patch(conn)):
            with pytest.raises(HTTPException) as exc_info:
                await get_partner_org_alert_config(
                    org_id=ORG_ID,
                    partner=FAKE_PARTNER,
                )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_site_overrides_when_none(self):
        """Returns empty site_overrides when no sites have per-site overrides."""
        from dashboard_api.partners import get_partner_org_alert_config

        org_record = FakeRecord(
            alert_email=None,
            cc_email=None,
            client_alert_mode="self_service",
        )
        conn = FakeConn(fetchrow_return=org_record, fetch_return=[])

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.partners.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.partners.admin_connection",
                   new=_make_admin_connection_patch(conn)):
            result = await get_partner_org_alert_config(
                org_id=ORG_ID,
                partner=FAKE_PARTNER,
            )

        assert result["site_overrides"] == []


# ---------------------------------------------------------------------------
# Test: PUT /me/orgs/{org_id}/alert-config
# ---------------------------------------------------------------------------


class TestPutOrgAlertConfig:
    """PUT /api/partners/me/orgs/{org_id}/alert-config"""

    @pytest.mark.asyncio
    async def test_invalid_mode_returns_422(self):
        """Invalid client_alert_mode returns HTTPException 422."""
        from dashboard_api.partners import update_partner_org_alert_config

        request = MagicMock()
        request.json = AsyncMock(return_value={"client_alert_mode": "invalid_mode"})

        with pytest.raises(HTTPException) as exc_info:
            await update_partner_org_alert_config(
                org_id=ORG_ID,
                request=request,
                partner=FAKE_PARTNER,
            )

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_valid_mode_updates(self):
        """Valid fields are saved via UPDATE query."""
        from dashboard_api.partners import update_partner_org_alert_config

        org_record = FakeRecord(id=ORG_ID)
        conn = FakeConn(fetchrow_return=org_record)

        request = MagicMock()
        request.json = AsyncMock(return_value={
            "alert_email": "new@practice.com",
            "client_alert_mode": "silent",
        })

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.partners.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.partners.admin_connection",
                   new=_make_admin_connection_patch(conn)):
            result = await update_partner_org_alert_config(
                org_id=ORG_ID,
                request=request,
                partner=FAKE_PARTNER,
            )

        assert result["status"] == "updated"
        update_calls = [c for c in conn.executed if c[0] == "execute" and "UPDATE" in c[1]]
        assert len(update_calls) == 1

    @pytest.mark.asyncio
    async def test_404_when_org_not_found(self):
        """Returns 404 when partner does not own the org."""
        from dashboard_api.partners import update_partner_org_alert_config

        conn = FakeConn(fetchrow_return=None)
        request = MagicMock()
        request.json = AsyncMock(return_value={"alert_email": "x@x.com"})

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.partners.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.partners.admin_connection",
                   new=_make_admin_connection_patch(conn)):
            with pytest.raises(HTTPException) as exc_info:
                await update_partner_org_alert_config(
                    org_id=ORG_ID,
                    request=request,
                    partner=FAKE_PARTNER,
                )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cc_email_and_alert_email_both_saved(self):
        """Both alert_email and cc_email are included in the UPDATE."""
        from dashboard_api.partners import update_partner_org_alert_config

        org_record = FakeRecord(id=ORG_ID)
        conn = FakeConn(fetchrow_return=org_record)

        request = MagicMock()
        request.json = AsyncMock(return_value={
            "alert_email": "primary@practice.com",
            "cc_email": "cc@practice.com",
        })

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.partners.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.partners.admin_connection",
                   new=_make_admin_connection_patch(conn)):
            result = await update_partner_org_alert_config(
                org_id=ORG_ID,
                request=request,
                partner=FAKE_PARTNER,
            )

        assert result["status"] == "updated"
        update_calls = [c for c in conn.executed if c[0] == "execute" and "UPDATE" in c[1]]
        assert len(update_calls) == 1
        # Both fields should appear in the query
        query = update_calls[0][1]
        assert "alert_email" in query
        assert "cc_email" in query


# ---------------------------------------------------------------------------
# Test: PUT /me/sites/{site_id}/alert-config
# ---------------------------------------------------------------------------


class TestPutSiteAlertConfig:
    """PUT /api/partners/me/sites/{site_id}/alert-config"""

    @pytest.mark.asyncio
    async def test_invalid_mode_returns_422(self):
        """Invalid client_alert_mode returns HTTPException 422."""
        from dashboard_api.partners import update_partner_site_alert_config

        request = MagicMock()
        request.json = AsyncMock(return_value={"client_alert_mode": "bad_mode"})

        with pytest.raises(HTTPException) as exc_info:
            await update_partner_site_alert_config(
                site_id=SITE_ID,
                request=request,
                partner=FAKE_PARTNER,
            )

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_null_mode_clears_override(self):
        """null client_alert_mode is allowed and means inherit from org."""
        from dashboard_api.partners import update_partner_site_alert_config

        site_record = FakeRecord(site_id=SITE_ID)
        conn = FakeConn(fetchrow_return=site_record)

        request = MagicMock()
        request.json = AsyncMock(return_value={"client_alert_mode": None})

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.partners.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.partners.admin_connection",
                   new=_make_admin_connection_patch(conn)):
            result = await update_partner_site_alert_config(
                site_id=SITE_ID,
                request=request,
                partner=FAKE_PARTNER,
            )

        assert result["status"] == "updated"
        assert result["client_alert_mode"] is None

    @pytest.mark.asyncio
    async def test_valid_mode_updates_site(self):
        """Valid mode saves to DB and is echoed back."""
        from dashboard_api.partners import update_partner_site_alert_config

        site_record = FakeRecord(site_id=SITE_ID)
        conn = FakeConn(fetchrow_return=site_record)

        request = MagicMock()
        request.json = AsyncMock(return_value={"client_alert_mode": "self_service"})

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.partners.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.partners.admin_connection",
                   new=_make_admin_connection_patch(conn)):
            result = await update_partner_site_alert_config(
                site_id=SITE_ID,
                request=request,
                partner=FAKE_PARTNER,
            )

        assert result["status"] == "updated"
        assert result["client_alert_mode"] == "self_service"
        update_calls = [c for c in conn.executed if c[0] == "execute" and "UPDATE" in c[1]]
        assert len(update_calls) == 1

    @pytest.mark.asyncio
    async def test_404_when_site_not_found(self):
        """Returns 404 when partner does not own the site."""
        from dashboard_api.partners import update_partner_site_alert_config

        conn = FakeConn(fetchrow_return=None)
        request = MagicMock()
        request.json = AsyncMock(return_value={"client_alert_mode": "informed"})

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.partners.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.partners.admin_connection",
                   new=_make_admin_connection_patch(conn)):
            with pytest.raises(HTTPException) as exc_info:
                await update_partner_site_alert_config(
                    site_id=SITE_ID,
                    request=request,
                    partner=FAKE_PARTNER,
                )

        assert exc_info.value.status_code == 404
