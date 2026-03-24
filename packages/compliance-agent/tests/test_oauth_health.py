"""
Tests for _check_oauth_health() in health_monitor.py.

Validates OAuth integration health status transitions, notification dedup,
and recovery behavior.
"""

import json
import sys
import types
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: health_monitor.py uses relative imports (from dashboard_api.fleet,
# from dashboard_api.tenant_middleware) that require a parent package.
# Stub them out so the module can be imported standalone.
# ---------------------------------------------------------------------------
_BACKEND_DIR = "/Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend"
sys.path.insert(0, _BACKEND_DIR)

_pkg_name = "dashboard_api"
if _pkg_name not in sys.modules:
    _pkg = types.ModuleType(_pkg_name)
    _pkg.__path__ = [_BACKEND_DIR]
    _pkg.__package__ = _pkg_name
    sys.modules[_pkg_name] = _pkg

# Stub dependencies
for _sub in ("fleet", "tenant_middleware", "email_alerts"):
    _fqn = f"{_pkg_name}.{_sub}"
    if _fqn not in sys.modules:
        _mod = types.ModuleType(_fqn)
        _mod.__package__ = _pkg_name
        if _sub == "fleet":
            _mod.get_pool = AsyncMock()
        elif _sub == "tenant_middleware":
            @asynccontextmanager
            async def _stub_admin(pool):
                yield MagicMock()
            _mod.admin_connection = _stub_admin
        elif _sub == "email_alerts":
            _mod.send_critical_alert = MagicMock()
        sys.modules[_fqn] = _mod

import importlib

_hm_fqn = f"{_pkg_name}.health_monitor"
if _hm_fqn in sys.modules:
    del sys.modules[_hm_fqn]
_spec = importlib.util.spec_from_file_location(
    _hm_fqn,
    f"{_BACKEND_DIR}/health_monitor.py",
    submodule_search_locations=[],
)
health_monitor = importlib.util.module_from_spec(_spec)
health_monitor.__package__ = _pkg_name
sys.modules[_hm_fqn] = health_monitor
sys.modules["health_monitor"] = health_monitor
_spec.loader.exec_module(health_monitor)

_check_oauth_health = health_monitor._check_oauth_health


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now():
    return datetime.now(timezone.utc)


def _make_row(
    provider="google_workspace",
    health_status="healthy",
    token_expires_at=None,
    consecutive_failures=0,
    last_sync_success_at=None,
):
    """Build a fake DB row dict for an OAuth integration."""
    row = {
        "id": uuid.uuid4(),
        "site_id": uuid.uuid4(),
        "provider": provider,
        "name": f"Test {provider}",
        "health_status": health_status,
        "access_token_expires_at": token_expires_at,
        "consecutive_failures": consecutive_failures,
        "last_sync_success_at": last_sync_success_at,
    }
    # Make it behave like asyncpg Record (subscriptable)
    return MagicMock(**{"__getitem__.side_effect": row.__getitem__})


class FakeConn:
    """Minimal fake asyncpg connection with transaction support."""

    def __init__(self, fetch_result=None, fetchval_result=None):
        self.fetch = AsyncMock(return_value=fetch_result or [])
        self.fetchval = AsyncMock(return_value=fetchval_result)
        self.execute = AsyncMock()

    @asynccontextmanager
    async def transaction(self):
        yield


async def _run_check(conn):
    """Run _check_oauth_health with mocked pool and admin_connection."""
    @asynccontextmanager
    async def fake_admin(pool):
        yield conn

    with patch("dashboard_api.fleet.get_pool", new_callable=AsyncMock) as mock_pool, \
         patch("dashboard_api.tenant_middleware.admin_connection", side_effect=fake_admin):
        mock_pool.return_value = MagicMock()
        await _check_oauth_health()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthy_integration_no_update():
    """A healthy integration with valid token and recent sync stays healthy."""
    now = _now()
    row = _make_row(
        health_status="healthy",
        token_expires_at=now + timedelta(hours=1),
        consecutive_failures=0,
        last_sync_success_at=now - timedelta(hours=1),
    )
    conn = FakeConn(fetch_result=[row])
    await _run_check(conn)

    # No UPDATE should be called (status unchanged)
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_expired_token_becomes_degraded():
    """An integration with an expired token transitions to degraded."""
    now = _now()
    row = _make_row(
        health_status="healthy",
        token_expires_at=now - timedelta(hours=1),  # expired
        consecutive_failures=0,
        last_sync_success_at=now - timedelta(hours=1),  # recent
    )
    conn = FakeConn(fetch_result=[row], fetchval_result=None)
    await _run_check(conn)

    # Should UPDATE health_status and INSERT notification
    assert conn.execute.call_count == 2  # 1 UPDATE + 1 INSERT notification
    update_call = conn.execute.call_args_list[0]
    assert "UPDATE integrations" in update_call[0][0]
    assert update_call[0][1] == "degraded"


@pytest.mark.asyncio
async def test_consecutive_failures_becomes_unhealthy():
    """3+ consecutive failures + stale sync -> unhealthy (2 problems)."""
    now = _now()
    row = _make_row(
        health_status="healthy",
        token_expires_at=now + timedelta(hours=1),  # valid
        consecutive_failures=5,
        last_sync_success_at=now - timedelta(hours=48),  # stale
    )
    conn = FakeConn(fetch_result=[row], fetchval_result=None)
    await _run_check(conn)

    update_call = conn.execute.call_args_list[0]
    assert update_call[0][1] == "unhealthy"

    # Notification should be critical
    insert_call = conn.execute.call_args_list[1]
    assert "INSERT INTO notifications" in insert_call[0][0]
    assert insert_call[0][2] == "critical"  # severity


@pytest.mark.asyncio
async def test_stale_sync_becomes_degraded():
    """Integration with no sync in >24h becomes degraded."""
    now = _now()
    row = _make_row(
        health_status="healthy",
        token_expires_at=now + timedelta(hours=1),  # valid
        consecutive_failures=0,
        last_sync_success_at=now - timedelta(hours=30),  # stale
    )
    conn = FakeConn(fetch_result=[row], fetchval_result=None)
    await _run_check(conn)

    update_call = conn.execute.call_args_list[0]
    assert update_call[0][1] == "degraded"


@pytest.mark.asyncio
async def test_notification_dedup():
    """Don't re-notify if a notification already exists within 24h."""
    now = _now()
    row = _make_row(
        health_status="healthy",
        token_expires_at=now - timedelta(hours=1),  # expired
        consecutive_failures=0,
        last_sync_success_at=now - timedelta(hours=1),  # recent
    )
    # fetchval returns 1 -> existing notification found
    conn = FakeConn(fetch_result=[row], fetchval_result=1)
    await _run_check(conn)

    # Should only have UPDATE (no INSERT notification due to dedup)
    assert conn.execute.call_count == 1
    assert "UPDATE integrations" in conn.execute.call_args_list[0][0][0]


@pytest.mark.asyncio
async def test_recovery_clears_status():
    """An unhealthy integration recovering to healthy updates status, no notification."""
    now = _now()
    row = _make_row(
        health_status="unhealthy",
        token_expires_at=now + timedelta(hours=1),  # valid
        consecutive_failures=0,
        last_sync_success_at=now - timedelta(hours=1),  # recent
    )
    conn = FakeConn(fetch_result=[row])
    await _run_check(conn)

    # Should UPDATE to healthy but NOT insert notification
    assert conn.execute.call_count == 1
    update_call = conn.execute.call_args_list[0]
    assert update_call[0][1] == "healthy"


@pytest.mark.asyncio
async def test_null_sync_treated_as_stale():
    """Integration with last_sync_success_at = NULL is treated as stale."""
    now = _now()
    row = _make_row(
        health_status="healthy",
        token_expires_at=now + timedelta(hours=1),  # valid
        consecutive_failures=0,
        last_sync_success_at=None,  # never synced
    )
    conn = FakeConn(fetch_result=[row], fetchval_result=None)
    await _run_check(conn)

    update_call = conn.execute.call_args_list[0]
    assert update_call[0][1] == "degraded"
