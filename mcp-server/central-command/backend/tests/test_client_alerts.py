"""Tests for client portal alert list + approve/dismiss endpoints.

Tests:
- GET /client/alerts
- POST /client/alerts/{alert_id}/action
"""

import os
import sys
import uuid
from datetime import datetime, timezone
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

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mcp_server_dir = os.path.dirname(os.path.dirname(backend_dir))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
if mcp_server_dir not in sys.path:
    sys.path.insert(0, mcp_server_dir)

# Remove stub modules that may have been injected by earlier tests
_stub_prefixes = ("fastapi", "pydantic", "sqlalchemy", "aiohttp", "starlette")
for _mod_name in list(sys.modules):
    if any(_mod_name == p or _mod_name.startswith(p + ".") for p in _stub_prefixes):
        _mod = sys.modules[_mod_name]
        if not hasattr(_mod, "__file__") or _mod.__file__ is None:
            del sys.modules[_mod_name]

from fastapi import HTTPException  # noqa: E402

# ---------------------------------------------------------------------------
# Stub heavy dependencies that client_portal imports at module level
# ---------------------------------------------------------------------------

import types as _types
from contextlib import asynccontextmanager as _asynccontextmanager

for _mod_name in (
    "structlog", "aiohttp",
    "nacl", "nacl.signing", "nacl.encoding", "minio",
    "redis", "redis.asyncio",
    "dashboard_api.fleet",
    "dashboard_api.tenant_middleware",
    "dashboard_api.phiscrub",
    "dashboard_api.shared",
):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = _types.ModuleType(_mod_name)

# Ensure dashboard_api package is set up
backend_dir_local = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = _types.ModuleType("dashboard_api")
_pkg.__path__ = [backend_dir_local]
_pkg.__package__ = "dashboard_api"
if not hasattr(sys.modules.get("dashboard_api", None), "__file__") or \
        sys.modules.get("dashboard_api", None).__file__ is None:
    sys.modules["dashboard_api"] = _pkg

# Stub fleet
_fleet_mod = sys.modules["dashboard_api.fleet"]
_fleet_mod.get_pool = AsyncMock(return_value=None)

# Stub tenant_middleware with the three connections client_portal needs
_tenant_mod = sys.modules["dashboard_api.tenant_middleware"]

@_asynccontextmanager
async def _noop_tenant_connection(pool, *, site_id=None):
    yield MagicMock()

@_asynccontextmanager
async def _noop_admin_connection(pool):
    yield MagicMock()

@_asynccontextmanager
async def _noop_admin_transaction(pool):
    yield MagicMock()

@_asynccontextmanager
async def _noop_org_connection(pool, *, org_id=None):
    yield MagicMock()

_tenant_mod.tenant_connection = _noop_tenant_connection
_tenant_mod.admin_connection = _noop_admin_connection
_tenant_mod.admin_transaction = _noop_admin_transaction
_tenant_mod.org_connection = _noop_org_connection

# Stub phiscrub
_phiscrub_mod = sys.modules["dashboard_api.phiscrub"]
_phiscrub_mod.sanitize_evidence_checks = lambda x: x

# Stub shared
_shared_mod = sys.modules["dashboard_api.shared"]
_shared_mod.require_client_user = MagicMock()
_shared_mod.require_appliance_bearer = MagicMock()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

ORG_ID = str(uuid.uuid4())
SITE_ID = str(uuid.uuid4())
ALERT_ID = str(uuid.uuid4())
INCIDENT_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())

NOW = datetime.now(timezone.utc)

FAKE_USER = {
    "user_id": USER_ID,
    "email": "client@practice.com",
    "org_id": ORG_ID,
    "role": "admin",
    "partner_branding": {},
}


class FakeRecord(dict):
    """Mimics asyncpg Record — subscriptable by name."""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class FakeConn:
    """Fake asyncpg connection with configurable responses.

    fetchrow_return may be:
      - a single value (returned for all fetchrow calls), OR
      - a list of values (consumed in order; last value repeated once exhausted)
    """

    def __init__(self, fetchrow_return=None, fetch_return=None):
        if isinstance(fetchrow_return, list):
            self._fetchrow_queue = list(fetchrow_return)
        else:
            self._fetchrow_queue = None
            self._fetchrow_return = fetchrow_return
        self._fetch_return = fetch_return or []
        self.executed = []

    async def fetchrow(self, query, *args):
        self.executed.append(("fetchrow", query, args))
        if self._fetchrow_queue is not None:
            if self._fetchrow_queue:
                return self._fetchrow_queue.pop(0)
            return None
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


def _org_connection_patch(conn):
    """Return a context-manager patch for org_connection that yields `conn`."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_org_connection(pool, *, org_id):
        yield conn

    return _fake_org_connection


# ---------------------------------------------------------------------------
# Tests: GET /client/alerts
# ---------------------------------------------------------------------------


class TestGetClientAlerts:
    """GET /client/alerts"""

    @pytest.mark.asyncio
    async def test_get_alerts_returns_list(self):
        """Returns alert list with correct shape; actions_available reflects mode."""
        from dashboard_api.client_portal import get_client_alerts

        rows = [
            FakeRecord(
                id=ALERT_ID,
                site_id="site-001",
                site_name="North Valley",
                alert_type="patch_drift",
                summary="3 critical patches missing",
                severity="high",
                created_at=NOW,
                sent_at=None,
                dismissed_at=None,
                incident_id=INCIDENT_ID,
                effective_mode="self_service",
            ),
            FakeRecord(
                id=str(uuid.uuid4()),
                site_id="site-002",
                site_name="Main Office",
                alert_type="backup_drift",
                summary="Backup overdue",
                severity="medium",
                created_at=NOW,
                sent_at=NOW,
                dismissed_at=None,
                incident_id=None,
                effective_mode="informed",
            ),
        ]
        conn = FakeConn(fetch_return=rows)

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.client_portal.org_connection",
                   new=_org_connection_patch(conn)):
            result = await get_client_alerts(user=FAKE_USER)

        assert "alerts" in result
        assert len(result["alerts"]) == 2

        first = result["alerts"][0]
        assert first["site_name"] == "North Valley"
        assert first["alert_type"] == "patch_drift"
        assert first["actions_available"] is True
        assert first["status"] == "pending"
        assert first["incident_id"] == str(INCIDENT_ID)

        second = result["alerts"][1]
        assert second["actions_available"] is False
        assert second["status"] == "sent"
        assert second["incident_id"] is None

    @pytest.mark.asyncio
    async def test_get_alerts_dismissed_status(self):
        """Alert with dismissed_at gets status='dismissed'."""
        from dashboard_api.client_portal import get_client_alerts

        rows = [
            FakeRecord(
                id=ALERT_ID,
                site_id="site-003",
                site_name="Branch",
                alert_type="firewall_drift",
                summary="Firewall off",
                severity="critical",
                created_at=NOW,
                sent_at=NOW,
                dismissed_at=NOW,
                incident_id=None,
                effective_mode="self_service",
            ),
        ]
        conn = FakeConn(fetch_return=rows)

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.client_portal.org_connection",
                   new=_org_connection_patch(conn)):
            result = await get_client_alerts(user=FAKE_USER)

        assert result["alerts"][0]["status"] == "dismissed"

    @pytest.mark.asyncio
    async def test_get_alerts_empty(self):
        """Returns empty list when no alerts exist."""
        from dashboard_api.client_portal import get_client_alerts

        conn = FakeConn(fetch_return=[])

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.client_portal.org_connection",
                   new=_org_connection_patch(conn)):
            result = await get_client_alerts(user=FAKE_USER)

        assert result["alerts"] == []


# ---------------------------------------------------------------------------
# Tests: POST /client/alerts/{alert_id}/action
# ---------------------------------------------------------------------------


class TestActionClientAlert:
    """POST /client/alerts/{alert_id}/action"""

    def _alert_record(self, effective_mode="self_service"):
        return FakeRecord(
            id=ALERT_ID,
            site_id=SITE_ID,
            incident_id=INCIDENT_ID,
            org_id=ORG_ID,
            effective_mode=effective_mode,
        )

    def _make_request(self, body: dict):
        request = MagicMock()
        request.json = AsyncMock(return_value=body)
        return request

    @pytest.mark.asyncio
    async def test_approve_creates_audit_record(self):
        """action='approved' with self_service mode inserts into client_approvals."""
        from dashboard_api.client_portal import action_client_alert

        # Two fetchrow calls: (1) alert lookup, (2) idempotency check → None
        conn = FakeConn(fetchrow_return=[self._alert_record("self_service"), None])

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.client_portal.org_connection",
                   new=_org_connection_patch(conn)):
            result = await action_client_alert(
                alert_id=ALERT_ID,
                request=self._make_request({"action": "approved", "notes": "Looks fine"}),
                user=FAKE_USER,
            )

        assert result["status"] == "ok"
        assert result["action_taken"] == "approved"
        assert result["approval_id"] is not None
        assert result["incident_id"] == str(INCIDENT_ID)

        insert_calls = [
            c for c in conn.executed
            if c[0] == "execute" and "client_approvals" in c[1]
        ]
        assert len(insert_calls) == 1

        # Approved should also update incidents table
        incident_update_calls = [
            c for c in conn.executed
            if c[0] == "execute" and "client_approved" in c[1]
        ]
        assert len(incident_update_calls) == 1

    @pytest.mark.asyncio
    async def test_approve_blocked_for_informed_mode(self):
        """action='approved' on an informed-mode site returns 403."""
        from dashboard_api.client_portal import action_client_alert

        conn = FakeConn(fetchrow_return=self._alert_record("informed"))

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.client_portal.org_connection",
                   new=_org_connection_patch(conn)):
            with pytest.raises(HTTPException) as exc_info:
                await action_client_alert(
                    alert_id=ALERT_ID,
                    request=self._make_request({"action": "approved"}),
                    user=FAKE_USER,
                )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_action_returns_422(self):
        """Unknown action value raises 422 before any DB access."""
        from dashboard_api.client_portal import action_client_alert

        conn = FakeConn()

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.client_portal.org_connection",
                   new=_org_connection_patch(conn)):
            with pytest.raises(HTTPException) as exc_info:
                await action_client_alert(
                    alert_id=ALERT_ID,
                    request=self._make_request({"action": "invalid"}),
                    user=FAKE_USER,
                )

        assert exc_info.value.status_code == 422
        # No DB calls should have been made
        assert conn.executed == []

    @pytest.mark.asyncio
    async def test_dismiss_updates_dismissed_at(self):
        """action='dismissed' inserts approval record AND updates dismissed_at."""
        from dashboard_api.client_portal import action_client_alert

        # Two fetchrow calls: (1) alert lookup, (2) idempotency check → None
        conn = FakeConn(fetchrow_return=[self._alert_record("self_service"), None])

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.client_portal.org_connection",
                   new=_org_connection_patch(conn)):
            result = await action_client_alert(
                alert_id=ALERT_ID,
                request=self._make_request({"action": "dismissed"}),
                user=FAKE_USER,
            )

        assert result["status"] == "ok"
        assert result["action_taken"] == "dismissed"

        dismissed_update_calls = [
            c for c in conn.executed
            if c[0] == "execute" and "dismissed_at" in c[1]
        ]
        assert len(dismissed_update_calls) == 1

    @pytest.mark.asyncio
    async def test_alert_not_found_returns_404(self):
        """Returns 404 when alert does not belong to org."""
        from dashboard_api.client_portal import action_client_alert

        conn = FakeConn(fetchrow_return=None)

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.client_portal.org_connection",
                   new=_org_connection_patch(conn)):
            with pytest.raises(HTTPException) as exc_info:
                await action_client_alert(
                    alert_id=ALERT_ID,
                    request=self._make_request({"action": "acknowledged"}),
                    user=FAKE_USER,
                )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_ignored_updates_dismissed_at(self):
        """action='ignored' also sets dismissed_at (same code path as dismissed)."""
        from dashboard_api.client_portal import action_client_alert

        # Two fetchrow calls: (1) alert lookup, (2) idempotency check → None
        conn = FakeConn(fetchrow_return=[self._alert_record("self_service"), None])

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.client_portal.org_connection",
                   new=_org_connection_patch(conn)):
            result = await action_client_alert(
                alert_id=ALERT_ID,
                request=self._make_request({"action": "ignored"}),
                user=FAKE_USER,
            )

        assert result["action_taken"] == "ignored"

        dismissed_update_calls = [
            c for c in conn.executed
            if c[0] == "execute" and "dismissed_at" in c[1]
        ]
        assert len(dismissed_update_calls) == 1

    @pytest.mark.asyncio
    async def test_duplicate_action_returns_idempotent_response(self):
        """Duplicate action on same alert+action returns existing approval without new INSERT."""
        from dashboard_api.client_portal import action_client_alert

        existing_approval_id = str(uuid.uuid4())
        existing_approval = FakeRecord(id=existing_approval_id)

        # Two fetchrow calls: (1) alert lookup, (2) idempotency check → existing record
        conn = FakeConn(fetchrow_return=[
            self._alert_record("self_service"),
            existing_approval,
        ])

        async def _fake_get_pool():
            return object()

        with patch("dashboard_api.client_portal.get_pool", new=_fake_get_pool), \
             patch("dashboard_api.client_portal.org_connection",
                   new=_org_connection_patch(conn)):
            result = await action_client_alert(
                alert_id=ALERT_ID,
                request=self._make_request({"action": "approved"}),
                user=FAKE_USER,
            )

        assert result["status"] == "ok"
        assert result["action_taken"] == "approved"
        assert result["approval_id"] == existing_approval_id
        assert result.get("note") == "already_actioned"

        # No INSERT into client_approvals should have been made
        insert_calls = [
            c for c in conn.executed
            if c[0] == "execute" and "client_approvals" in c[1]
        ]
        assert len(insert_calls) == 0
