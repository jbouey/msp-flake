"""Tests for non-engagement escalation."""
import os
import sys
import types
import uuid

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio-password")
os.environ.setdefault("SIGNING_KEY_FILE", "/tmp/test-signing.key")

# ---------------------------------------------------------------------------
# Stub heavy dependencies so we can import alert_router without a full stack
# ---------------------------------------------------------------------------

for mod_name in (
    "fastapi", "pydantic", "sqlalchemy", "sqlalchemy.ext",
    "sqlalchemy.ext.asyncio", "aiohttp", "structlog",
    "nacl", "nacl.signing", "nacl.encoding", "minio",
    "redis", "redis.asyncio",
    "dashboard_api.email_alerts",
    "dashboard_api.fleet",
    "dashboard_api.tenant_middleware",
    "dashboard_api.shared",
):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

# SQLAlchemy async stubs required by shared.py imports
sys.modules["sqlalchemy.ext.asyncio"].create_async_engine = lambda *a, **kw: None
sys.modules["sqlalchemy.ext.asyncio"].AsyncSession = object
sys.modules["sqlalchemy.ext.asyncio"].async_sessionmaker = lambda *a, **kw: None

from unittest.mock import AsyncMock, MagicMock, patch

_email_mod = sys.modules["dashboard_api.email_alerts"]
_email_mod.send_digest_email = MagicMock(return_value=True)
_email_mod.is_email_configured = MagicMock(return_value=True)

_fleet_mod = sys.modules["dashboard_api.fleet"]
_fleet_mod.get_pool = AsyncMock(return_value=None)

_tenant_mod = sys.modules["dashboard_api.tenant_middleware"]


class _FakeAdminConn:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        pass


_tenant_mod.admin_connection = lambda pool: _FakeAdminConn(MagicMock())
_tenant_mod.admin_transaction = lambda pool: _FakeAdminConn(MagicMock())

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import importlib

_pkg = types.ModuleType("dashboard_api")
_pkg.__path__ = [backend_dir]
_pkg.__package__ = "dashboard_api"
if not hasattr(sys.modules.get("dashboard_api", None), "__file__") or \
        sys.modules.get("dashboard_api", None).__file__ is None:
    sys.modules["dashboard_api"] = _pkg

import pytest

_alert_router_mod = importlib.import_module("dashboard_api.alert_router")
_check_non_engagement = _alert_router_mod._check_non_engagement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**kwargs):
    """Return a dict-like object mimicking an asyncpg Record."""
    return dict(**kwargs)


ORG_ID = str(uuid.uuid4())
PARTNER_ID = str(uuid.uuid4())
ORG_NAME = "North Valley Health"


# =============================================================================
# Tests
# =============================================================================

class TestNonEngagement:

    @pytest.mark.asyncio
    async def test_no_unacted_alerts_no_escalation(self):
        """No pending rows → no INSERT, no email."""
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock()
        conn.execute = AsyncMock()

        await _check_non_engagement(conn)

        conn.execute.assert_not_called()
        conn.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_unacted_alerts_creates_partner_notification(self):
        """Unacted alert with partner → INSERT into partner_notifications + email."""
        conn = AsyncMock()

        # First fetch: unacted alert rows
        conn.fetch = AsyncMock(return_value=[
            {
                "org_id": ORG_ID,
                "org_name": ORG_NAME,
                "current_partner_id": PARTNER_ID,
                "unacted_count": 3,
                "oldest_alert": "2026-04-04T10:00:00+00:00",
            }
        ])

        # fetchrow calls: first = dedup check (None = no prior escalation),
        #                 second = partner email lookup
        conn.fetchrow = AsyncMock(side_effect=[
            None,  # no existing partner_notification
            {"name": "MSP Partner", "email": "partner@msp.example.com"},
        ])
        conn.execute = AsyncMock()

        await _check_non_engagement(conn)

        # Must have called execute exactly once (the INSERT)
        conn.execute.assert_called_once()
        call_sql = conn.execute.call_args[0][0]
        assert "INSERT INTO partner_notifications" in call_sql

        # Email should have been sent
        _email_mod.send_digest_email.assert_called()
        call_kwargs = _email_mod.send_digest_email.call_args
        # subject should mention the org name
        subject = call_kwargs[1].get("subject") or call_kwargs[0][2]
        assert ORG_NAME in subject or "non-engagement" in subject.lower()

    @pytest.mark.asyncio
    async def test_dedup_prevents_re_escalation_within_7_days(self):
        """Existing partner_notification within 7 days → no new INSERT."""
        conn = AsyncMock()

        conn.fetch = AsyncMock(return_value=[
            {
                "org_id": ORG_ID,
                "org_name": ORG_NAME,
                "current_partner_id": PARTNER_ID,
                "unacted_count": 2,
                "oldest_alert": "2026-04-04T10:00:00+00:00",
            }
        ])

        # Dedup check returns an existing row
        conn.fetchrow = AsyncMock(return_value={"id": str(uuid.uuid4())})
        conn.execute = AsyncMock()

        _email_mod.send_digest_email.reset_mock()

        await _check_non_engagement(conn)

        # No INSERT should happen
        conn.execute.assert_not_called()
        # No email should be sent
        _email_mod.send_digest_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_partner_id_skipped(self):
        """Row with current_partner_id = None is skipped entirely."""
        conn = AsyncMock()

        conn.fetch = AsyncMock(return_value=[
            {
                "org_id": ORG_ID,
                "org_name": ORG_NAME,
                "current_partner_id": None,
                "unacted_count": 5,
                "oldest_alert": "2026-04-04T10:00:00+00:00",
            }
        ])
        conn.fetchrow = AsyncMock()
        conn.execute = AsyncMock()

        _email_mod.send_digest_email.reset_mock()

        await _check_non_engagement(conn)

        # No dedup check, no INSERT, no email
        conn.fetchrow.assert_not_called()
        conn.execute.assert_not_called()
        _email_mod.send_digest_email.assert_not_called()
