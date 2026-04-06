"""Integration tests for the incident pipeline.

Tests the flow: POST /incidents -> L1 rule match -> healing order creation.
Also tests L3 escalation and dedup behaviour.

These tests mock the database layer (SQLAlchemy AsyncSession) and Redis
so we can exercise the FastAPI endpoint logic without a real Postgres/Redis.
"""

import json
import os
import sys
import types
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

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

# Restore real fastapi/sqlalchemy/pydantic if earlier tests stubbed them.
# Other test files (test_auth.py, test_evidence_chain.py) inject stub modules
# into sys.modules at collection time. Remove those stubs so main.py can
# import the real packages.
_stub_prefixes = ("fastapi", "pydantic", "sqlalchemy", "aiohttp", "starlette")
for _mod_name in list(sys.modules):
    if any(_mod_name == p or _mod_name.startswith(p + ".") for p in _stub_prefixes):
        _mod = sys.modules[_mod_name]
        if not hasattr(_mod, "__file__") or _mod.__file__ is None:
            del sys.modules[_mod_name]

# Pre-import main at module level so it's cached before other test files
# can corrupt sys.modules further.
import main as _main_module  # noqa: E402, F401


# ---------------------------------------------------------------------------
# Helpers: mock DB result rows
# ---------------------------------------------------------------------------

class FakeRow:
    """Simulate a SQLAlchemy result row with index and attribute access."""

    def __init__(self, values):
        self._values = values

    def __getitem__(self, idx):
        return self._values[idx]

    def __getattr__(self, name):
        # Allow attribute-style access for named columns
        if name.startswith("_"):
            raise AttributeError(name)
        for i, v in enumerate(self._values):
            if i == 0 and name in ("id",):
                return v
        raise AttributeError(name)


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
    """Create a mock AsyncSession that returns configurable results."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIncidentPipelineL1Match:
    """Test that an incident matching an L1 rule creates a healing order."""

    @pytest.mark.asyncio
    async def test_l1_rule_match_creates_order(self):
        """POST /incidents with a matching L1 rule should create a healing order."""
        # We test the logic by calling the endpoint function directly,
        # since importing the full app requires live Redis/MinIO/DB.

        # Import the main module (triggers global setup)
        import main

        appliance_uuid = str(uuid.uuid4())
        canonical_id = "test-site-001-aa:bb:cc:dd:ee:ff"

        # Configure mock DB responses in order of execute() calls:
        # 1. Rate limit check (redis) - patch separately
        # 2. SELECT id FROM appliances WHERE site_id = ...
        # 3. SELECT appliance_id FROM site_appliances ...
        # 4. SELECT id, status FROM incidents WHERE ... (dedup check)
        # 5. INSERT INTO incidents
        # 6. SELECT runbook_id FROM l1_rules WHERE ...
        # 7. INSERT INTO orders
        # 8. UPDATE incidents SET resolution_tier ...
        # 9. db.commit()
        # 10. Notification dedup check
        # 11. INSERT INTO notifications

        db = make_mock_db()

        call_count = 0
        async def mock_execute(query, params=None):
            nonlocal call_count
            call_count += 1
            query_str = str(query) if not isinstance(query, str) else query

            # Appliance lookup
            if "SELECT id FROM appliances" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            # Canonical appliance_id lookup
            if "SELECT appliance_id FROM site_appliances" in query_str:
                return FakeResult([FakeRow([canonical_id])])
            # Dedup check - no existing incident
            if "SELECT id, status FROM incidents" in query_str:
                return FakeResult([])
            # Chronic drift check - return 0 (not chronic)
            if "SELECT COUNT(*) FROM incidents" in query_str:
                return FakeResult([FakeRow([0])], scalar_value=0)
            # L1 rule match
            if "SELECT runbook_id FROM l1_rules" in query_str:
                return FakeResult([FakeRow(["RB-AUTO-SERVICE_RESTART"])])
            # Notification dedup
            if "SELECT id FROM notifications" in query_str:
                return FakeResult([])
            # All other queries (INSERT, UPDATE) - return empty
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        # Create incident report
        incident = main.IncidentReport(
            site_id="test-site-001",
            host_id="appliance-01",
            incident_type="service_stopped_windows_firewall",
            severity="high",
            check_type="service_monitor",
            details={"service": "Windows Firewall", "state": "stopped"},
            pre_state={"running": False},
        )

        # Mock request object
        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.100"

        # Patch redis rate limiting to always allow
        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            with patch.object(main, "sign_data", return_value="a" * 128):
                with patch("main.create_notification_with_email", new_callable=AsyncMock):
                    result = await main.report_incident(incident, mock_request, db)

        assert result["status"] == "received"
        assert result["resolution_tier"] == "L1"
        assert result["runbook_id"] == "RB-AUTO-SERVICE_RESTART"
        assert result["order_id"] is not None
        assert result["incident_id"] is not None

    @pytest.mark.asyncio
    async def test_l1_escalation_rule_triggers_l3(self):
        """An L1 rule with ESC- prefix should escalate to L3 without creating an order."""
        import main

        appliance_uuid = str(uuid.uuid4())
        db = make_mock_db()

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT id FROM appliances" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT appliance_id FROM site_appliances" in query_str:
                return FakeResult([FakeRow(["test-site-002-aa:bb:cc:dd:ee:ff"])])
            if "SELECT id, status FROM incidents" in query_str:
                return FakeResult([])
            if "SELECT COUNT(*) FROM incidents" in query_str:
                return FakeResult([FakeRow([0])], scalar_value=0)
            if "SELECT runbook_id FROM l1_rules" in query_str:
                return FakeResult([FakeRow(["ESC-RANSOMWARE-001"])])
            if "SELECT id FROM notifications" in query_str:
                return FakeResult([])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        incident = main.IncidentReport(
            site_id="test-site-002",
            host_id="appliance-02",
            incident_type="ransomware_detected",
            severity="critical",
            details={"threat": "ransomware"},
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.101"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            with patch("main.create_notification_with_email", new_callable=AsyncMock):
                result = await main.report_incident(incident, mock_request, db)

        assert result["status"] == "received"
        assert result["resolution_tier"] == "L3"
        assert result["order_id"] is None
        assert result["runbook_id"] is None


class TestIncidentPipelineL3Escalation:
    """Test L3 escalation when no L1 or L2 rules match."""

    @pytest.mark.asyncio
    async def test_no_rule_match_escalates_to_l3(self):
        """When no L1 rule matches and L2 is unavailable, escalate to L3."""
        import main

        appliance_uuid = str(uuid.uuid4())
        db = make_mock_db()

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT id FROM appliances" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT appliance_id FROM site_appliances" in query_str:
                return FakeResult([FakeRow(["test-site-003-ff:ff:ff:ff:ff:ff"])])
            if "SELECT id, status FROM incidents" in query_str:
                return FakeResult([])
            if "SELECT COUNT(*) FROM incidents" in query_str:
                return FakeResult([FakeRow([0])], scalar_value=0)
            if "SELECT runbook_id FROM l1_rules" in query_str:
                return FakeResult([])  # No L1 match
            if "SELECT id FROM notifications" in query_str:
                return FakeResult([])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        incident = main.IncidentReport(
            site_id="test-site-003",
            host_id="appliance-03",
            incident_type="unknown_exotic_failure_xyz",
            severity="medium",
            details={"description": "Something unusual happened"},
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.102"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            # Patch L2 as unavailable (imported inside the handler from dashboard_api.l2_planner)
            with patch("dashboard_api.l2_planner.is_l2_available", return_value=False):
                with patch("main.create_notification_with_email", new_callable=AsyncMock):
                    result = await main.report_incident(incident, mock_request, db)

        assert result["status"] == "received"
        assert result["resolution_tier"] == "L3"
        assert result["order_id"] is None


class TestIncidentDeduplication:
    """Test that duplicate incidents within 4h window are suppressed."""

    @pytest.mark.asyncio
    async def test_duplicate_incident_is_suppressed(self):
        """Same incident_type within 4h should return 'deduplicated'."""
        import main

        appliance_uuid = str(uuid.uuid4())
        existing_incident_id = str(uuid.uuid4())
        db = make_mock_db()

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT id FROM appliances" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT appliance_id FROM site_appliances" in query_str:
                return FakeResult([FakeRow(["test-site-004-aa:bb:cc:dd:ee:ff"])])
            # Dedup check — new query selects 4 columns (id, status, severity, appliance_id)
            # and uses dedup_key for cross-appliance matching.
            if "dedup_key" in query_str and "SELECT" in query_str and "INSERT" not in query_str:
                return FakeResult([FakeRow([existing_incident_id, "open", "high", appliance_uuid])])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        incident = main.IncidentReport(
            site_id="test-site-004",
            host_id="appliance-04",
            incident_type="service_stopped_dns_client",
            severity="high",
            details={"service": "DNS Client"},
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.103"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            result = await main.report_incident(incident, mock_request, db)

        assert result["status"] == "deduplicated"
        assert result["incident_id"] == existing_incident_id

    @pytest.mark.asyncio
    async def test_resolved_incident_is_reopened(self):
        """Recently resolved incident of same type should be reopened."""
        import main

        appliance_uuid = str(uuid.uuid4())
        existing_incident_id = str(uuid.uuid4())
        db = make_mock_db()

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT id FROM appliances" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT appliance_id FROM site_appliances" in query_str:
                return FakeResult([FakeRow(["test-site-005-aa:bb:cc:dd:ee:ff"])])
            # Dedup check — new query selects 4 columns and uses dedup_key
            if "dedup_key" in query_str and "SELECT" in query_str and "INSERT" not in query_str:
                return FakeResult([FakeRow([existing_incident_id, "resolved", "high", appliance_uuid])])
            # resolved_at grace period check
            if "SELECT resolved_at FROM incidents" in query_str:
                # Return None to simulate expired grace period (force reopen)
                return FakeResult([FakeRow([None])])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        incident = main.IncidentReport(
            site_id="test-site-005",
            host_id="appliance-05",
            incident_type="service_stopped_dns_client",
            severity="high",
            details={"service": "DNS Client"},
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.104"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            result = await main.report_incident(incident, mock_request, db)

        assert result["status"] == "reopened"
        assert result["incident_id"] == existing_incident_id


class TestIncidentKeywordFallback:
    """Test keyword-based L1 matching when no DB rule matches."""

    @pytest.mark.asyncio
    async def test_backup_keyword_matches_runbook(self):
        """incident_type containing 'backup' should match RB-WIN-BACKUP-001."""
        import main

        appliance_uuid = str(uuid.uuid4())
        db = make_mock_db()

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query

            if "SELECT id FROM appliances" in query_str:
                return FakeResult([FakeRow([appliance_uuid])])
            if "SELECT appliance_id FROM site_appliances" in query_str:
                return FakeResult([FakeRow(["test-site-006-aa:bb:cc:dd:ee:ff"])])
            if "SELECT id, status FROM incidents" in query_str:
                return FakeResult([])
            if "SELECT COUNT(*) FROM incidents" in query_str:
                return FakeResult([FakeRow([0])], scalar_value=0)
            if "SELECT runbook_id FROM l1_rules" in query_str:
                return FakeResult([])  # No DB rule
            if "SELECT id FROM notifications" in query_str:
                return FakeResult([])
            return FakeResult([], rowcount=1)

        db.execute = AsyncMock(side_effect=mock_execute)

        incident = main.IncidentReport(
            site_id="test-site-006",
            host_id="appliance-06",
            incident_type="backup_failure_detected",
            severity="high",
            details={"backup": "failed"},
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.105"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            with patch.object(main, "sign_data", return_value="b" * 128):
                with patch("main.create_notification_with_email", new_callable=AsyncMock):
                    result = await main.report_incident(incident, mock_request, db)

        assert result["status"] == "received"
        assert result["resolution_tier"] == "L1"
        assert result["runbook_id"] == "RB-WIN-BACKUP-001"
        assert result["order_id"] is not None


class TestIncidentUnregisteredAppliance:
    """Test incident reporting from an unregistered appliance."""

    @pytest.mark.asyncio
    async def test_unregistered_appliance_returns_404(self):
        """POST /incidents from unregistered appliance should raise 404."""
        import main
        from fastapi.exceptions import HTTPException

        db = make_mock_db()

        async def mock_execute(query, params=None):
            query_str = str(query) if not isinstance(query, str) else query
            if "SELECT id FROM appliances" in query_str:
                return FakeResult([])  # No appliance found
            return FakeResult([], rowcount=0)

        db.execute = AsyncMock(side_effect=mock_execute)

        incident = main.IncidentReport(
            site_id="nonexistent-site",
            host_id="unknown-host",
            incident_type="service_stopped",
            severity="high",
        )

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.200"

        with patch.object(main, "check_rate_limit", new_callable=AsyncMock, return_value=(True, 0)):
            with pytest.raises(HTTPException) as exc_info:
                await main.report_incident(incident, mock_request, db)
            assert exc_info.value.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
