"""
Comprehensive tests for device_sync.py -- device inventory sync from appliance checkins.

Tests cover:
- Device upsert (new creation + existing update)
- OUI lookup / MAC enrichment
- Device archiving (stale device cleanup)
- Workstation linkage (discovered devices -> workstations table)
- IPv4Address to string casting (Session 183 fix)
- Compliance status derivation from incidents
- Edge cases: empty device list, malformed MACs, missing fields
- Batch sync with mixed success/failure
- Device status auto-classification (AD-joined, take-over available)
"""

import sys
import os
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call
from contextlib import asynccontextmanager
from ipaddress import IPv4Address

# Add mcp-server to sys.path so dashboard_api (symlink to central-command/backend)
# is importable.
_MCP_SERVER_DIR = str(Path(__file__).resolve().parents[3] / "mcp-server")
if _MCP_SERVER_DIR not in sys.path:
    sys.path.insert(0, _MCP_SERVER_DIR)


# ---------------------------------------------------------------------------
# Helpers -- mock DB objects that behave like asyncpg
# ---------------------------------------------------------------------------


class FakeRecord(dict):
    """Dict that supports bracket access like asyncpg.Record."""

    def __getitem__(self, key):
        return super().__getitem__(key)


def _rec(data: dict) -> FakeRecord:
    return FakeRecord(data)


class FakeConn:
    """Minimal mock of asyncpg.Connection with call tracking."""

    def __init__(self):
        self.fetchrow = AsyncMock(return_value=None)
        self.fetchval = AsyncMock(return_value=None)
        self.fetch = AsyncMock(return_value=[])
        self.execute = AsyncMock()

    @asynccontextmanager
    async def transaction(self):
        yield


@asynccontextmanager
async def _fake_admin(pool):
    yield pool._conn


def _pool_with(conn: FakeConn):
    """Create a pool mock wired to the given FakeConn."""
    pool = MagicMock()
    pool._conn = conn

    @asynccontextmanager
    async def acquire():
        yield conn

    pool.acquire = acquire
    return pool


# ---------------------------------------------------------------------------
# Pydantic model factories
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)
_YESTERDAY = _NOW - timedelta(days=1)


def _device_entry(**overrides):
    from dashboard_api.device_sync import DeviceSyncEntry

    defaults = dict(
        device_id="dev-001",
        hostname="ws01",
        ip_address="192.168.88.100",
        mac_address="00:03:93:AB:CD:EF",
        device_type="workstation",
        os_name="Windows 11",
        os_version="10.0.22000",
        medical_device=False,
        scan_policy="standard",
        manually_opted_in=False,
        compliance_status="compliant",
        open_ports=[22, 3389],
        compliance_details=[],
        discovery_source="nmap",
        first_seen_at=_YESTERDAY,
        last_seen_at=_NOW,
        last_scan_at=_NOW,
        os_fingerprint=None,
        distro=None,
        probe_ssh=None,
        probe_winrm=None,
        probe_snmp=None,
        ad_joined=None,
    )
    defaults.update(overrides)
    return DeviceSyncEntry(**defaults)


def _report(devices=None, **overrides):
    from dashboard_api.device_sync import DeviceSyncReport

    devs = devices if devices is not None else [_device_entry()]
    defaults = dict(
        appliance_id="appliance-001",
        site_id="site-clinic-001",
        scan_timestamp=_NOW,
        devices=devs,
        total_devices=len(devs),
        monitored_devices=len(devs),
        excluded_devices=0,
        medical_devices=0,
        compliance_rate=100.0,
    )
    defaults.update(overrides)
    return DeviceSyncReport(**defaults)


# ===================================================================
# 1. DEVICE UPSERT -- new device creation
# ===================================================================


@pytest.mark.asyncio
async def test_sync_creates_new_device():
    """sync_devices inserts a new device when none exists for the appliance + local_device_id."""
    from dashboard_api.device_sync import sync_devices

    conn = FakeConn()
    conn.fetchrow.side_effect = [
        _rec({"id": 42}),                     # appliance lookup
        None,                                   # device not found -> create
        _rec({"device_status": None}),          # status check after insert
    ]
    conn.fetchval.return_value = 99  # INSERT RETURNING id

    pool = _pool_with(conn)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin):
        result = await sync_devices(_report())

    assert result.status == "success"
    assert result.devices_created == 1
    assert result.devices_updated == 0
    assert result.devices_received == 1
    assert "1 new" in result.message


# ===================================================================
# 2. DEVICE UPSERT -- existing device update
# ===================================================================


@pytest.mark.asyncio
async def test_sync_updates_existing_device():
    """sync_devices updates an existing device instead of inserting."""
    from dashboard_api.device_sync import sync_devices

    conn = FakeConn()
    conn.fetchrow.side_effect = [
        _rec({"id": 42}),                     # appliance lookup
        _rec({"id": 99}),                     # device exists
        _rec({"device_status": "discovered"}), # current status
    ]

    pool = _pool_with(conn)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin):
        result = await sync_devices(_report())

    assert result.status == "success"
    assert result.devices_updated == 1
    assert result.devices_created == 0


# ===================================================================
# 3. UNKNOWN SITE -- graceful error
# ===================================================================


@pytest.mark.asyncio
async def test_sync_unknown_site_returns_error():
    """sync_devices returns error status when site_id is not found."""
    from dashboard_api.device_sync import sync_devices

    conn = FakeConn()
    conn.fetchrow.return_value = None  # appliance not found

    pool = _pool_with(conn)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin):
        result = await sync_devices(_report(site_id="nonexistent"))

    assert result.status == "error"
    assert result.devices_created == 0
    assert "Unknown site_id" in result.message


# ===================================================================
# 4. OUI LOOKUP / MAC ENRICHMENT -- known manufacturer
# ===================================================================


@pytest.mark.asyncio
async def test_get_site_devices_enriches_mac_with_oui():
    """get_site_devices adds manufacturer_hint from OUI lookup for known MACs."""
    from dashboard_api.device_sync import get_site_devices

    conn = FakeConn()
    device_row = _rec({
        "id": 1, "hostname": "macbook", "ip_address": "192.168.88.50",
        "mac_address": "00:03:93:AB:CD:EF",  # Apple OUI
        "device_type": "workstation", "compliance_status": "compliant",
        "appliance_hostname": "app-1", "site_id": "site-001",
    })
    conn.fetch.side_effect = [
        [device_row],  # devices
        [],            # go_agents
        [],            # site_credentials
        [],            # appliance IPs
    ]

    pool = _pool_with(conn)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin), \
         patch("dashboard_api.device_sync.decrypt_credential", return_value="{}"):
        devices = await get_site_devices("site-001")

    assert len(devices) == 1
    hint = devices[0]["manufacturer_hint"]
    assert hint["manufacturer"] == "Apple"
    assert hint["device_class"] == "workstation"
    assert hint["confidence"] == "oui_match"


# ===================================================================
# 5. OUI LOOKUP -- unknown MAC returns null hint
# ===================================================================


@pytest.mark.asyncio
async def test_unknown_mac_returns_null_hint():
    """Unknown MAC addresses produce null manufacturer hint."""
    from dashboard_api.device_sync import get_site_devices

    conn = FakeConn()
    device_row = _rec({
        "id": 1, "hostname": "mystery", "ip_address": "10.0.0.1",
        "mac_address": "FF:FF:FF:00:00:01",  # not in OUI table
        "device_type": "unknown", "compliance_status": "unknown",
        "appliance_hostname": "app-1", "site_id": "s",
    })
    conn.fetch.side_effect = [[device_row], [], [], []]
    pool = _pool_with(conn)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin), \
         patch("dashboard_api.device_sync.decrypt_credential", return_value="{}"):
        devices = await get_site_devices("s")

    assert devices[0]["manufacturer_hint"]["manufacturer"] is None
    assert devices[0]["manufacturer_hint"]["confidence"] is None


# ===================================================================
# 6. NO MAC -- null hint without calling OUI
# ===================================================================


@pytest.mark.asyncio
async def test_no_mac_returns_null_hint():
    """Devices without a MAC address get a null manufacturer hint."""
    from dashboard_api.device_sync import get_site_devices

    conn = FakeConn()
    device_row = _rec({
        "id": 1, "hostname": "headless", "ip_address": "10.0.0.2",
        "mac_address": None, "device_type": "server",
        "compliance_status": "unknown", "appliance_hostname": "a", "site_id": "s",
    })
    conn.fetch.side_effect = [[device_row], [], [], []]
    pool = _pool_with(conn)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin), \
         patch("dashboard_api.device_sync.decrypt_credential", return_value="{}"):
        devices = await get_site_devices("s")

    assert devices[0]["manufacturer_hint"] == {
        "manufacturer": None, "device_class": None, "confidence": None
    }


# ===================================================================
# 7. DEVICE ARCHIVING -- stale device cleanup
# ===================================================================


@pytest.mark.asyncio
async def test_sync_runs_archive_sweep():
    """sync_devices archives devices not seen in 30 days."""
    from dashboard_api.device_sync import sync_devices

    conn = FakeConn()
    conn.fetchrow.side_effect = [
        _rec({"id": 42}),              # appliance
        None,                           # device -> create
        _rec({"device_status": None}),  # status
    ]
    conn.fetchval.return_value = 99

    pool = _pool_with(conn)
    admin_calls = {"n": 0}

    @asynccontextmanager
    async def counting_admin(p):
        admin_calls["n"] += 1
        yield conn

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=counting_admin):
        await sync_devices(_report())

    # 4 admin_connection calls: main sync, workstation linkage, archive sweep, credential IP update
    assert admin_calls["n"] == 4

    archive_sql = [c for c in conn.execute.call_args_list if "archived" in str(c)]
    assert len(archive_sql) >= 1, "Expected archive sweep UPDATE"


# ===================================================================
# 8. WORKSTATION LINKAGE -- creates workstation entries
# ===================================================================


@pytest.mark.asyncio
async def test_link_devices_creates_workstation_entries():
    """_link_devices_to_workstations upserts workstation rows from discovered devices."""
    from dashboard_api.device_sync import _link_devices_to_workstations

    conn = FakeConn()
    conn.fetch.side_effect = [
        [_rec({
            "id": 1, "hostname": "ws01", "ip_address": "192.168.88.100",
            "mac_address": "AA:BB:CC:DD:EE:FF", "os_name": "Windows 11",
            "os_version": "10.0", "compliance_status": "compliant",
            "last_seen_at": _NOW, "device_type": "workstation",
        })],
        [],  # per-host incidents
        [],  # platform incidents
    ]

    await _link_devices_to_workstations(conn, "site-001")

    ws_inserts = [c for c in conn.execute.call_args_list if "workstations" in str(c)]
    assert len(ws_inserts) >= 1


@pytest.mark.asyncio
async def test_link_devices_empty_list_is_noop():
    """No workstation/server devices -> no INSERT."""
    from dashboard_api.device_sync import _link_devices_to_workstations

    conn = FakeConn()
    conn.fetch.return_value = []

    await _link_devices_to_workstations(conn, "site-001")
    assert conn.execute.call_count == 0


# ===================================================================
# 9. IPv4Address TO STRING CASTING (Session 183 fix)
# ===================================================================


@pytest.mark.asyncio
async def test_ipv4address_cast_in_agent_lookup():
    """asyncpg returns IPv4Address objects; code must cast to str before string ops."""
    from dashboard_api.device_sync import get_site_devices

    conn = FakeConn()
    device_row = _rec({
        "id": 1, "hostname": "server01",
        "ip_address": IPv4Address("192.168.88.100"),
        "mac_address": "AA:BB:CC:DD:EE:FF", "device_type": "server",
        "compliance_status": "compliant", "appliance_hostname": "a", "site_id": "s",
    })
    agent_row = _rec({
        "hostname": "SERVER01",
        "ip_address": IPv4Address("192.168.88.100"),
        "status": "active", "last_heartbeat": _NOW, "agent_version": "1.2.3",
    })
    appliance_row = _rec({
        "ip_address": IPv4Address("192.168.88.241"),
    })

    conn.fetch.side_effect = [
        [device_row], [agent_row], [], [appliance_row],
    ]

    pool = _pool_with(conn)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin), \
         patch("dashboard_api.device_sync.decrypt_credential", return_value="{}"):
        devices = await get_site_devices("s")

    assert len(devices) == 1
    # Same /24 as appliance -> managed
    assert devices[0]["managed_network"] is True
    # Agent matched via str(IPv4Address)
    assert devices[0]["agent_coverage"]["level"] == "agent"


@pytest.mark.asyncio
async def test_ipv4address_cast_in_device_counts():
    """get_site_device_counts must handle IPv4Address from appliance IP rows."""
    from dashboard_api.device_sync import get_site_device_counts

    conn = FakeConn()
    conn.fetch.return_value = [_rec({"ip_address": IPv4Address("192.168.88.241")})]
    conn.fetchrow.return_value = _rec({
        "total": 5, "compliant": 3, "drifted": 1, "unknown": 1,
        "medical": 0, "workstations": 3, "servers": 1,
        "network_devices": 1, "printers": 0,
    })

    pool = _pool_with(conn)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin):
        result = await get_site_device_counts("s")

    assert result["total"] == 5
    assert result["compliant"] == 3


# ===================================================================
# 10. COMPLIANCE STATUS FROM INCIDENTS -- open -> drifted
# ===================================================================


@pytest.mark.asyncio
async def test_compliance_derived_from_open_incidents():
    """Devices with open incidents derive 'drifted' compliance status."""
    from dashboard_api.device_sync import _link_devices_to_workstations

    conn = FakeConn()
    conn.fetch.side_effect = [
        [_rec({
            "id": 1, "hostname": "ws01", "ip_address": "192.168.88.100",
            "mac_address": None, "os_name": "Windows 11", "os_version": "10.0",
            "compliance_status": "compliant",  # scanner says compliant
            "last_seen_at": _NOW, "device_type": "workstation",
        })],
        [_rec({
            "hostname": "192.168.88.100",
            "open_count": 3, "resolved_count": 0,
            "last_incident_at": _NOW,
        })],
        [],  # platform status
    ]

    await _link_devices_to_workstations(conn, "site-001")

    ws_insert = [c for c in conn.execute.call_args_list if "INSERT INTO workstations" in str(c)]
    assert len(ws_insert) >= 1
    # status=$9 in the INSERT args
    status_arg = ws_insert[0][0][9]
    assert status_arg == "drifted"


# ===================================================================
# 11. COMPLIANCE STATUS -- platform-level fallback
# ===================================================================


@pytest.mark.asyncio
async def test_compliance_platform_fallback():
    """When no per-host incidents exist, platform-level incidents are used as fallback."""
    from dashboard_api.device_sync import _link_devices_to_workstations

    conn = FakeConn()
    conn.fetch.side_effect = [
        [_rec({
            "id": 1, "hostname": "ws02", "ip_address": "192.168.88.101",
            "mac_address": None, "os_name": "Windows 10", "os_version": "10.0",
            "compliance_status": "unknown", "last_seen_at": _NOW,
            "device_type": "workstation",
        })],
        [],  # per-host empty
        [_rec({
            "platform": "windows", "open_count": 0, "resolved_count": 5,
            "last_incident_at": _NOW,
        })],
    ]

    await _link_devices_to_workstations(conn, "site-001")

    ws_insert = [c for c in conn.execute.call_args_list if "INSERT INTO workstations" in str(c)]
    assert len(ws_insert) >= 1
    # Platform fallback: resolved windows incidents -> compliant
    status_arg = ws_insert[0][0][9]
    assert status_arg == "compliant"


# ===================================================================
# 12. EMPTY DEVICE LIST
# ===================================================================


@pytest.mark.asyncio
async def test_sync_empty_device_list():
    """Syncing an empty device list succeeds with zero counts."""
    from dashboard_api.device_sync import sync_devices

    conn = FakeConn()
    conn.fetchrow.return_value = _rec({"id": 42})  # appliance exists

    pool = _pool_with(conn)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin):
        result = await sync_devices(_report(devices=[]))

    assert result.status == "success"
    assert result.devices_received == 0
    assert result.devices_created == 0


# ===================================================================
# 13. MALFORMED MAC ADDRESSES
# ===================================================================


def test_oui_normalize_short_mac():
    """Short/empty MACs produce empty prefix, not crashes."""
    from dashboard_api.oui_lookup import normalize_mac_for_oui, get_manufacturer_hint

    assert normalize_mac_for_oui("AA") == ""
    assert normalize_mac_for_oui("") == ""

    result = get_manufacturer_hint("XX")
    assert result["manufacturer"] is None


def test_oui_normalize_various_formats():
    """MAC addresses in different formats normalize to uppercase colon-separated."""
    from dashboard_api.oui_lookup import normalize_mac_for_oui

    assert normalize_mac_for_oui("00:03:93:AB:CD:EF") == "00:03:93"
    assert normalize_mac_for_oui("00-03-93-AB-CD-EF") == "00:03:93"
    assert normalize_mac_for_oui("0003.93AB.CDEF") == "00:03:93"
    assert normalize_mac_for_oui("000393ABCDEF") == "00:03:93"
    assert normalize_mac_for_oui("00:03:93:ab:cd:ef") == "00:03:93"


# ===================================================================
# 14. MISSING OPTIONAL FIELDS
# ===================================================================


@pytest.mark.asyncio
async def test_sync_device_with_minimal_fields():
    """Devices with only required fields sync correctly."""
    from dashboard_api.device_sync import sync_devices

    conn = FakeConn()
    conn.fetchrow.side_effect = [
        _rec({"id": 42}),  # appliance
        None,               # create
        _rec({"device_status": None}),
    ]
    conn.fetchval.return_value = 99

    pool = _pool_with(conn)
    dev = _device_entry(hostname=None, mac_address=None, os_name=None, os_version=None)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin):
        result = await sync_devices(_report(devices=[dev]))

    assert result.status == "success"
    assert result.devices_created == 1


# ===================================================================
# 15. BATCH SYNC -- mixed success/failure
# ===================================================================


@pytest.mark.asyncio
async def test_batch_sync_mixed_success_failure():
    """When some devices fail, status is 'partial' and errors are counted."""
    from dashboard_api.device_sync import sync_devices

    conn = FakeConn()

    n = {"call": 0}

    async def fetchrow_seq(*args, **kwargs):
        n["call"] += 1
        if n["call"] == 1:
            return _rec({"id": 42})          # appliance
        if n["call"] == 2:
            return None                       # dev-ok: create
        if n["call"] == 3:
            return _rec({"device_status": None})
        if n["call"] == 4:
            raise Exception("connection lost")  # dev-fail: blows up
        return None

    conn.fetchrow = AsyncMock(side_effect=fetchrow_seq)
    conn.fetchval.return_value = 99

    pool = _pool_with(conn)
    devs = [
        _device_entry(device_id="dev-ok"),
        _device_entry(device_id="dev-fail", ip_address="10.0.0.2"),
    ]

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin):
        result = await sync_devices(_report(devices=devs))

    assert result.status == "partial"
    assert result.devices_created == 1
    assert "1 errors" in result.message


# ===================================================================
# 16. DEVICE STATUS AUTO-CLASSIFICATION -- AD-joined -> ad_managed
# ===================================================================


@pytest.mark.asyncio
async def test_device_status_ad_managed():
    """AD-joined device without active go_agent gets status 'ad_managed'."""
    from dashboard_api.device_sync import sync_devices

    conn = FakeConn()
    conn.fetchrow.side_effect = [
        _rec({"id": 42}),              # appliance
        None,                           # create
        _rec({"device_status": None}),  # not managed
    ]
    conn.fetchval.side_effect = [
        99,  # INSERT RETURNING id
        0,   # go_agents COUNT = 0
    ]

    pool = _pool_with(conn)
    dev = _device_entry(ad_joined=True)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin):
        await sync_devices(_report(devices=[dev]))

    status_updates = [c for c in conn.execute.call_args_list if "device_status = $2" in str(c)]
    assert len(status_updates) >= 1
    assert status_updates[0][0][2] == "ad_managed"


# ===================================================================
# 17. DEVICE STATUS -- take_over_available from SSH probe
# ===================================================================


@pytest.mark.asyncio
async def test_device_status_take_over_from_ssh():
    """Device with SSH probe but not AD-joined gets 'take_over_available'."""
    from dashboard_api.device_sync import sync_devices

    conn = FakeConn()
    conn.fetchrow.side_effect = [
        _rec({"id": 42}),              # appliance
        None,                           # create
        _rec({"device_status": None}),  # not managed
    ]
    conn.fetchval.return_value = 99

    pool = _pool_with(conn)
    dev = _device_entry(probe_ssh=True, ad_joined=False)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin):
        await sync_devices(_report(devices=[dev]))

    status_updates = [c for c in conn.execute.call_args_list if "device_status = $2" in str(c)]
    assert len(status_updates) >= 1
    assert status_updates[0][0][2] == "take_over_available"


# ===================================================================
# 18. MANAGED STATES NOT OVERWRITTEN
# ===================================================================


@pytest.mark.asyncio
async def test_managed_status_not_overwritten():
    """Devices in managed states (agent_active, deploying, ...) keep their status."""
    from dashboard_api.device_sync import sync_devices

    conn = FakeConn()
    conn.fetchrow.side_effect = [
        _rec({"id": 42}),                          # appliance
        _rec({"id": 99}),                          # device exists
        _rec({"device_status": "agent_active"}),   # MANAGED state
    ]

    pool = _pool_with(conn)
    dev = _device_entry(ad_joined=True, probe_ssh=True)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin):
        await sync_devices(_report(devices=[dev]))

    # No device_status update should have been issued
    status_updates = [
        c for c in conn.execute.call_args_list
        if "device_status = $2" in str(c) and ("ad_managed" in str(c) or "take_over" in str(c))
    ]
    assert len(status_updates) == 0


# ===================================================================
# 19. COMPLIANCE CHECK DETAILS UPSERT
# ===================================================================


@pytest.mark.asyncio
async def test_compliance_details_upserted():
    """Compliance check details from the report are upserted into device_compliance_details."""
    from dashboard_api.device_sync import sync_devices, ComplianceCheckDetail

    conn = FakeConn()
    conn.fetchrow.side_effect = [
        _rec({"id": 42}),              # appliance
        None,                           # create
        _rec({"device_status": None}),
    ]
    conn.fetchval.return_value = 99

    pool = _pool_with(conn)
    check = ComplianceCheckDetail(
        check_type="firewall", hipaa_control="164.312(e)(1)",
        status="fail", details='{"enabled": false}', checked_at=_NOW,
    )
    dev = _device_entry(compliance_details=[check])

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin):
        await sync_devices(_report(devices=[dev]))

    compliance_sql = [c for c in conn.execute.call_args_list if "device_compliance_details" in str(c)]
    assert len(compliance_sql) == 1


# ===================================================================
# 20. MANAGED NETWORK SUBNET TAGGING
# ===================================================================


@pytest.mark.asyncio
async def test_managed_network_subnet_tagging():
    """Devices on the same /24 as an appliance are tagged managed_network=True."""
    from dashboard_api.device_sync import get_site_devices

    conn = FakeConn()
    same = _rec({
        "id": 1, "hostname": "ws01", "ip_address": "192.168.88.100",
        "mac_address": None, "device_type": "workstation",
        "compliance_status": "compliant", "appliance_hostname": "a", "site_id": "s",
    })
    diff = _rec({
        "id": 2, "hostname": "ws02", "ip_address": "10.0.0.50",
        "mac_address": None, "device_type": "workstation",
        "compliance_status": "compliant", "appliance_hostname": "a", "site_id": "s",
    })
    appliance_ip = _rec({"ip_address": "192.168.88.241"})

    conn.fetch.side_effect = [[same, diff], [], [], [appliance_ip]]
    pool = _pool_with(conn)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin), \
         patch("dashboard_api.device_sync.decrypt_credential", return_value="{}"):
        devices = await get_site_devices("s")

    assert devices[0]["managed_network"] is True
    assert devices[1]["managed_network"] is False


# ===================================================================
# 21. WORKSTATION ONLINE STATUS from last_seen_at
# ===================================================================


@pytest.mark.asyncio
async def test_workstation_online_status():
    """Workstation is online if last_seen_at < 30 minutes ago, offline otherwise."""
    from dashboard_api.device_sync import _link_devices_to_workstations

    conn = FakeConn()
    recent = _rec({
        "id": 1, "hostname": "ws-recent", "ip_address": "192.168.88.100",
        "mac_address": None, "os_name": "Windows 11", "os_version": "10.0",
        "compliance_status": "compliant",
        "last_seen_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        "device_type": "workstation",
    })
    stale = _rec({
        "id": 2, "hostname": "ws-stale", "ip_address": "192.168.88.101",
        "mac_address": None, "os_name": "Windows 10", "os_version": "10.0",
        "compliance_status": "compliant",
        "last_seen_at": datetime.now(timezone.utc) - timedelta(hours=2),
        "device_type": "workstation",
    })

    conn.fetch.side_effect = [[recent, stale], [], []]

    await _link_devices_to_workstations(conn, "site-001")

    assert conn.execute.call_count >= 2
    # online is the 7th positional arg ($7)
    assert conn.execute.call_args_list[0][0][7] is True   # recent -> online
    assert conn.execute.call_args_list[1][0][7] is False   # stale -> offline


# ===================================================================
# 22. WORKSTATION SUMMARY UPDATE
# ===================================================================


@pytest.mark.asyncio
async def test_update_workstation_summary():
    """_update_workstation_summary writes to site_workstation_summaries."""
    from dashboard_api.device_sync import _update_workstation_summary

    conn = FakeConn()
    conn.fetchrow.return_value = _rec({
        "total": 10, "online": 7, "compliant": 8, "drifted": 1,
        "error": 0, "unknown": 1,
    })

    await _update_workstation_summary(conn, "site-001")

    summary_sql = [c for c in conn.execute.call_args_list if "site_workstation_summaries" in str(c)]
    assert len(summary_sql) == 1

    # compliance_rate = 8/10 * 100 = 80.0 ($9)
    assert summary_sql[0][0][9] == 80.0


@pytest.mark.asyncio
async def test_workstation_summary_skips_empty():
    """_update_workstation_summary does nothing when total = 0."""
    from dashboard_api.device_sync import _update_workstation_summary

    conn = FakeConn()
    conn.fetchrow.return_value = _rec({
        "total": 0, "online": 0, "compliant": 0, "drifted": 0,
        "error": 0, "unknown": 0,
    })

    await _update_workstation_summary(conn, "site-001")
    assert conn.execute.call_count == 0


# ===================================================================
# 23. CREDENTIAL-BASED COVERAGE ENRICHMENT
# ===================================================================


@pytest.mark.asyncio
async def test_agent_coverage_from_credentials():
    """Devices with WinRM credentials get 'remote' coverage level."""
    from dashboard_api.device_sync import get_site_devices

    conn = FakeConn()
    device_row = _rec({
        "id": 1, "hostname": "ws-remote", "ip_address": "192.168.88.100",
        "mac_address": None, "device_type": "workstation",
        "compliance_status": "compliant", "appliance_hostname": "a", "site_id": "s",
    })
    cred_row = _rec({
        "credential_name": "winrm-admin", "credential_type": "winrm",
        "encrypted_data": b'{"host": "192.168.88.100", "username": "admin"}',
    })

    conn.fetch.side_effect = [[device_row], [], [cred_row], []]
    pool = _pool_with(conn)

    with patch("dashboard_api.device_sync.get_pool", return_value=pool), \
         patch("dashboard_api.device_sync.admin_connection", side_effect=_fake_admin), \
         patch("dashboard_api.device_sync.decrypt_credential",
               return_value='{"host": "192.168.88.100", "username": "admin"}'):
        devices = await get_site_devices("s")

    cov = devices[0]["agent_coverage"]
    assert cov["level"] == "remote"
    assert "winrm" in cov["methods"]
