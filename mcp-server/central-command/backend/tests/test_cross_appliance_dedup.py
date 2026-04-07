"""Tests for cross-appliance incident deduplication.

When two appliances on the same site report the same incident_type against
the same hostname, the second report should be deduplicated against the
first — regardless of which appliance sent it.

Also covers: severity upgrades on dedup, fallback to appliance-scoped dedup
when no hostname is present, and non-dedup of different hostnames.
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Environment setup (must happen before any app imports)
# ---------------------------------------------------------------------------

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio-password")
os.environ.setdefault("SIGNING_KEY_FILE", "/tmp/test-signing.key")

# Ensure the backend package is importable
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mcp_server_dir = os.path.dirname(os.path.dirname(backend_dir))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
if mcp_server_dir not in sys.path:
    sys.path.insert(0, mcp_server_dir)

# Remove any stub modules that other test files may have injected
_stub_prefixes = ("fastapi", "pydantic", "sqlalchemy", "aiohttp", "starlette",
                  "nacl", "minio", "redis", "structlog", "dashboard_api")
for _mod_name in list(sys.modules):
    if any(_mod_name == p or _mod_name.startswith(p + ".") for p in _stub_prefixes):
        _mod = sys.modules[_mod_name]
        if not hasattr(_mod, "__file__") or _mod.__file__ is None:
            del sys.modules[_mod_name]

import main as _main_module  # noqa: E402, F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeRow:
    """Simulate a SQLAlchemy result row with index access."""

    def __init__(self, values):
        self._values = values

    def __getitem__(self, idx):
        return self._values[idx]


class FakeResult:
    """Simulate SQLAlchemy execute() result."""

    def __init__(self, rows=None, rowcount=0, scalar_value=None):
        self._rows = rows or []
        self.rowcount = rowcount
        self._scalar_value = scalar_value

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def scalar(self):
        if self._scalar_value is not None:
            return self._scalar_value
        row = self.fetchone()
        return row[0] if row else None


def make_mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCrossApplianceDedup:
    """Cross-appliance incident deduplication tests."""

    @pytest.mark.asyncio
    async def test_second_appliance_is_deduplicated(self):
        """Appliance B reporting same incident_type + hostname as an existing
        open incident from Appliance A should return {'status': 'deduplicated'}."""
        import main

        appliance_uuid = str(uuid.uuid4())
        existing_incident_id = str(uuid.uuid4())
        db = make_mock_db()

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT id FROM appliances" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT appliance_id FROM site_appliances" in query_str:
                return FakeResult([FakeRow(["site-001-aa:bb:cc:dd:ee:ff"])])
            # Cross-appliance dedup path: query uses dedup_key
            if "dedup_key" in query_str:
                # Return id, status, severity, appliance_id
                return FakeResult([FakeRow([existing_incident_id, "open", "medium", appliance_uuid])])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        incident = main.IncidentReport(
            site_id="site-001",
            host_id="workstation-01",
            incident_type="av_disabled",
            severity="medium",
            details={"hostname": "workstation-01", "av": "disabled"},
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.10"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            result = await main.report_incident(incident, mock_request, db)

        assert result["status"] == "deduplicated"
        assert result["incident_id"] == existing_incident_id

    @pytest.mark.asyncio
    async def test_severity_upgrade_on_dedup(self):
        """When the second report has a higher severity, the existing incident
        should be upgraded via UPDATE and the response still shows 'deduplicated'."""
        import main

        appliance_uuid = str(uuid.uuid4())
        existing_incident_id = str(uuid.uuid4())
        db = make_mock_db()

        update_called = False

        async def mock_execute(query, params=None):
            nonlocal update_called
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT id FROM appliances" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT appliance_id FROM site_appliances" in query_str:
                return FakeResult([FakeRow(["site-002-aa:bb:cc:dd:ee:ff"])])
            if "dedup_key" in query_str:
                # Existing incident is "low", incoming is "critical" → upgrade expected
                return FakeResult([FakeRow([existing_incident_id, "open", "low", appliance_uuid])])
            if "UPDATE incidents SET severity" in query_str:
                update_called = True
                return FakeResult([], rowcount=1)
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        incident = main.IncidentReport(
            site_id="site-002",
            host_id="workstation-02",
            incident_type="firewall_disabled",
            severity="critical",
            details={"hostname": "workstation-02"},
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.11"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            result = await main.report_incident(incident, mock_request, db)

        assert result["status"] == "deduplicated"
        assert result["incident_id"] == existing_incident_id
        assert update_called, "Expected UPDATE incidents SET severity to be called for severity upgrade"

    @pytest.mark.asyncio
    async def test_no_hostname_falls_back_to_appliance_scoped(self):
        """When details has no 'hostname' and host_id is empty/None, dedup_key
        should be None and the fallback query (appliance_id-scoped) is used."""
        import main

        appliance_uuid = str(uuid.uuid4())
        existing_incident_id = str(uuid.uuid4())
        db = make_mock_db()

        fallback_query_used = False

        async def mock_execute(query, params=None):
            nonlocal fallback_query_used
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT id FROM appliances" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT appliance_id FROM site_appliances" in query_str:
                return FakeResult([FakeRow(["site-003-aa:bb:cc:dd:ee:ff"])])
            # Fallback path uses appliance_id = :appliance_id (not dedup_key)
            if "appliance_id = :appliance_id" in query_str and "incident_type" in query_str:
                fallback_query_used = True
                return FakeResult([FakeRow([existing_incident_id, "open", "medium", appliance_uuid])])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        incident = main.IncidentReport(
            site_id="site-003",
            host_id="",          # empty — triggers fallback
            incident_type="disk_space_low",
            severity="medium",
            details={},          # no hostname key
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.12"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            result = await main.report_incident(incident, mock_request, db)

        assert result["status"] == "deduplicated"
        assert fallback_query_used, "Expected appliance_id-scoped fallback dedup query"

    @pytest.mark.asyncio
    async def test_different_hosts_not_deduplicated(self):
        """Same incident_type but different hostnames → different dedup_key
        → no match → new incident is created (INSERT happens)."""
        import main

        appliance_uuid = str(uuid.uuid4())
        db = make_mock_db()

        insert_called = False

        async def mock_execute(query, params=None):
            nonlocal insert_called
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT id FROM appliances" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT appliance_id FROM site_appliances" in query_str:
                return FakeResult([FakeRow(["site-004-aa:bb:cc:dd:ee:ff"])])
            # INSERT must be checked before the dedup_key check because the INSERT
            # query itself contains the word "dedup_key" in its column list.
            if "INSERT INTO incidents" in query_str:
                insert_called = True
                return FakeResult([], rowcount=1)
            # Dedup check: no match for this hostname's dedup_key
            if "dedup_key" in query_str:
                return FakeResult([])
            if "SELECT COUNT(*) FROM incidents" in query_str:
                return FakeResult([FakeRow([0])], scalar_value=0)
            if "SELECT runbook_id FROM l1_rules" in query_str:
                return FakeResult([])
            if "SELECT id FROM notifications" in query_str:
                return FakeResult([])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        incident = main.IncidentReport(
            site_id="site-004",
            host_id="workstation-99",
            incident_type="av_disabled",
            severity="high",
            details={"hostname": "workstation-99"},
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.13"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            with patch("main.create_notification_with_email", new_callable=AsyncMock):
                result = await main.report_incident(incident, mock_request, db)

        assert result["status"] == "received"
        assert insert_called, "Expected INSERT INTO incidents for a new unique hostname"
