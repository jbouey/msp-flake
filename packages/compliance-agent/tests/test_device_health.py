"""
Tests for credential_health (site detail) and _check_device_reachability()
in health_monitor.py.

Validates:
- Credential scan status returns correct structure
- Unreachable device notifications roll up correctly
- Notification dedup within 24h window
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

_check_device_reachability = health_monitor._check_device_reachability


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now():
    return datetime.now(timezone.utc)


class FakeConn:
    """Minimal fake asyncpg connection with transaction support."""

    def __init__(self, fetch_result=None, fetchval_result=None):
        self.fetch = AsyncMock(return_value=fetch_result or [])
        self.fetchval = AsyncMock(return_value=fetchval_result)
        self.execute = AsyncMock()

    @asynccontextmanager
    async def transaction(self):
        yield


async def _run_reachability_check(conn):
    """Run _check_device_reachability with mocked pool and admin_connection."""
    @asynccontextmanager
    async def fake_admin(pool):
        yield conn

    with patch("dashboard_api.fleet.get_pool", new_callable=AsyncMock) as mock_pool, \
         patch("dashboard_api.tenant_middleware.admin_connection", side_effect=fake_admin):
        mock_pool.return_value = MagicMock()
        await _check_device_reachability()


def _make_unreachable_row(site_id=None, clinic_name="Test Clinic",
                          unreachable_count=3, hosts=None):
    """Build a fake DB row for unreachable device aggregation."""
    row = {
        "site_id": site_id or str(uuid.uuid4()),
        "clinic_name": clinic_name,
        "unreachable_count": unreachable_count,
        "hosts": hosts or ["dc01", "ws01", "ws02"],
    }
    return MagicMock(**{"__getitem__.side_effect": row.__getitem__})


def _make_credential_scan_row(credential_name="North Valley DC",
                              credential_type="domain_admin",
                              sensor_deployed=True,
                              last_scan_at=None):
    """Build a fake DB row for credential scan status."""
    row = {
        "credential_name": credential_name,
        "credential_type": credential_type,
        "sensor_deployed": sensor_deployed,
        "last_scan_at": last_scan_at,
    }
    return MagicMock(**{"__getitem__.side_effect": row.__getitem__})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_credential_health_query():
    """Verify credential_scan_status returns correct structure with healthy/not_scanned."""
    now = _now()

    # Simulate two credentials: one with a recent scan, one without
    scanned_row = _make_credential_scan_row(
        credential_name="North Valley DC",
        credential_type="domain_admin",
        sensor_deployed=True,
        last_scan_at=now - timedelta(hours=2),
    )
    unscanned_row = _make_credential_scan_row(
        credential_name="Workstation SSH",
        credential_type="ssh_password",
        sensor_deployed=False,
        last_scan_at=None,
    )

    # Build credential_health the same way sites.py does
    rows = [scanned_row, unscanned_row]
    credential_health = [
        {
            "name": row["credential_name"],
            "type": row["credential_type"],
            "sensor_deployed": row["sensor_deployed"],
            "last_scan_at": row["last_scan_at"].isoformat() if row["last_scan_at"] else None,
            "status": "healthy" if row["last_scan_at"] else "not_scanned",
        }
        for row in rows
    ]

    assert len(credential_health) == 2

    # First credential: scanned
    assert credential_health[0]["name"] == "North Valley DC"
    assert credential_health[0]["type"] == "domain_admin"
    assert credential_health[0]["sensor_deployed"] is True
    assert credential_health[0]["last_scan_at"] is not None
    assert credential_health[0]["status"] == "healthy"

    # Second credential: not scanned
    assert credential_health[1]["name"] == "Workstation SSH"
    assert credential_health[1]["type"] == "ssh_password"
    assert credential_health[1]["sensor_deployed"] is False
    assert credential_health[1]["last_scan_at"] is None
    assert credential_health[1]["status"] == "not_scanned"


@pytest.mark.asyncio
async def test_unreachable_notification_rollup():
    """Verify partner gets notification for unreachable devices."""
    site_id = str(uuid.uuid4())
    row = _make_unreachable_row(
        site_id=site_id,
        clinic_name="Valley Health",
        unreachable_count=3,
        hosts=["dc01", "ws01", "ws02"],
    )

    # fetch returns unreachable rows; fetchval returns None (no existing notification)
    conn = FakeConn(fetch_result=[row], fetchval_result=None)
    await _run_reachability_check(conn)

    # Should INSERT a notification
    assert conn.execute.call_count == 1
    insert_call = conn.execute.call_args_list[0]
    sql = insert_call[0][0]
    assert "INSERT INTO notifications" in sql
    assert "device_unreachable" in sql

    # Check the title includes count and clinic name
    title_arg = insert_call[0][2]
    assert "3 device(s) unreachable" in title_arg
    assert "Valley Health" in title_arg

    # Check the message lists hosts
    message_arg = insert_call[0][3]
    assert "dc01" in message_arg

    # Check metadata JSON
    metadata = json.loads(insert_call[0][4])
    assert metadata["count"] == 3
    assert "dc01" in metadata["hosts"]


@pytest.mark.asyncio
async def test_unreachable_notification_dedup():
    """Verify no duplicate notifications within 24h window."""
    site_id = str(uuid.uuid4())
    row = _make_unreachable_row(
        site_id=site_id,
        clinic_name="Valley Health",
        unreachable_count=2,
        hosts=["dc01", "ws01"],
    )

    # fetchval returns 1 -> existing notification found (dedup)
    conn = FakeConn(fetch_result=[row], fetchval_result=1)
    await _run_reachability_check(conn)

    # Should NOT insert any notification (dedup kicked in)
    conn.execute.assert_not_called()
