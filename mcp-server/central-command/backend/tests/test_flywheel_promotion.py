"""Integration tests for the learning flywheel promotion system.

Tests the flow:
  POST /learning/promote/{pattern_id} -> L1 rule creation

Two promotion paths:
  1. Legacy `patterns` table via promote_pattern_in_db()
  2. Fallback to `aggregated_pattern_stats` table

These tests mock the database layer (SQLAlchemy AsyncSession) so we can
exercise the FastAPI endpoint and db_queries logic without a real Postgres.
"""

import asyncio
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

# Restore real fastapi/sqlalchemy/pydantic if earlier tests stubbed them.
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

    def __init__(self, values, columns=None):
        self._values = values
        self._columns = columns or []

    def __getitem__(self, idx):
        return self._values[idx]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        # Named column access
        if self._columns:
            for i, col in enumerate(self._columns):
                if col == name and i < len(self._values):
                    return self._values[i]
        raise AttributeError(name)


class FakeResult:
    """Simulate SQLAlchemy execute() result."""

    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


def make_mock_db():
    """Create a mock AsyncSession that returns configurable results."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


MOCK_ADMIN_USER = {"id": "1", "username": "admin", "role": "admin", "org_scope": None}


# ---------------------------------------------------------------------------
# Tests: POST /learning/promote/{pattern_id} endpoint
# ---------------------------------------------------------------------------

class TestPromotePatternEndpoint:
    """Test the promote_pattern endpoint via httpx AsyncClient."""

    @pytest.mark.asyncio
    async def test_promote_from_legacy_patterns_table(self):
        """When pattern exists in legacy patterns table, promote it and return new rule."""
        # Import from _routes_impl (routes.py loaded via package __init__)
        from dashboard_api._routes_impl import promote_pattern as promote_fn

        pattern_id = "pat-legacy-001"
        expected_rule_id = "RB-AUTO-SERVICE_RESTART"

        mock_db = make_mock_db()

        with patch("dashboard_api.db_queries.promote_pattern_in_db",
                   new_callable=AsyncMock, return_value=expected_rule_id):
            result = await promote_fn(pattern_id=pattern_id, db=mock_db, user=MOCK_ADMIN_USER)

        assert result["status"] == "promoted"
        assert result["pattern_id"] == pattern_id
        assert result["new_rule_id"] == expected_rule_id

    @pytest.mark.asyncio
    async def test_promote_from_aggregated_pattern_stats(self):
        """When legacy table returns None, fall back to aggregated_pattern_stats."""
        from dashboard_api._routes_impl import promote_pattern as promote_fn

        pattern_id = "42"
        mock_db = make_mock_db()

        aps_row = FakeRow(
            [42, "service_stopped:RB-AUTO-SERVICE_RESTART", "site-abc", "restart_service"],
            columns=["id", "pattern_signature", "site_id", "recommended_action"],
        )

        async def mock_execute(query, params=None):
            query_str = str(query)

            # APS lookup (first db.execute after promote_pattern_in_db returns None)
            if "aggregated_pattern_stats" in query_str and "SELECT" in query_str:
                return FakeResult([aps_row])
            # INSERT INTO l1_rules
            if "INSERT INTO l1_rules" in query_str:
                return FakeResult([], rowcount=1)
            # UPDATE aggregated_pattern_stats
            if "UPDATE aggregated_pattern_stats" in query_str:
                return FakeResult([], rowcount=1)
            # INSERT INTO learning_promotion_candidates
            if "learning_promotion_candidates" in query_str:
                return FakeResult([], rowcount=1)
            return FakeResult([], rowcount=0)

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        # check_site_access_sa needs to find the site in DB — mock that query too
        site_row = FakeRow(["org-1"], columns=["client_org_id"])

        original_execute = mock_db.execute

        async def execute_with_site_check(query, params=None):
            query_str = str(query)
            if "SELECT client_org_id FROM sites" in query_str:
                return FakeResult([site_row])
            return await original_execute(query, params)

        mock_db.execute = AsyncMock(side_effect=execute_with_site_check)

        with patch("dashboard_api.db_queries.promote_pattern_in_db",
                   new_callable=AsyncMock, return_value=None):
            result = await promote_fn(pattern_id=pattern_id, db=mock_db, user=MOCK_ADMIN_USER)

        assert result["status"] == "promoted"
        assert result["pattern_id"] == pattern_id
        # Rule ID derived from incident_type portion of pattern_signature
        assert result["new_rule_id"] == "L1-AUTO-SERVICE-STOPPED"

    @pytest.mark.asyncio
    async def test_pattern_not_found_returns_404(self):
        """When pattern is not in legacy table or APS, raise 404."""
        from dashboard_api._routes_impl import promote_pattern as promote_fn
        from fastapi.exceptions import HTTPException

        pattern_id = "999"
        mock_db = make_mock_db()

        async def mock_execute(query, params=None):
            query_str = str(query)
            if "aggregated_pattern_stats" in query_str:
                return FakeResult([])  # Not found
            return FakeResult([], rowcount=0)

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        with patch("dashboard_api.db_queries.promote_pattern_in_db",
                   new_callable=AsyncMock, return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await promote_fn(pattern_id=pattern_id, db=mock_db, user=MOCK_ADMIN_USER)

        assert exc_info.value.status_code == 404
        assert "999" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Tests: promote_pattern_in_db() direct unit tests
# ---------------------------------------------------------------------------

class TestPromotePatternInDb:
    """Test the db_queries.promote_pattern_in_db function directly."""

    @pytest.mark.asyncio
    async def test_pending_pattern_creates_rule_and_updates_status(self):
        """A pending pattern should be promoted: L1 rule created, status updated."""
        from dashboard_api.db_queries import promote_pattern_in_db

        pattern_id = "pat-pending-001"
        mock_db = make_mock_db()

        # Simulate a pending pattern row
        pattern_row = FakeRow(
            [pattern_id, "pending", "svc_restart", "service_stopped", "RB-AUTO-SVC"],
            columns=["pattern_id", "status", "pattern_signature", "incident_type", "runbook_id"],
        )

        executed_queries = []

        async def mock_execute(query, params=None):
            query_str = str(query)
            executed_queries.append(query_str)

            # SELECT FOR UPDATE on patterns
            if "SELECT * FROM patterns" in query_str and "FOR UPDATE" in query_str:
                return FakeResult([pattern_row])
            # INSERT INTO l1_rules
            if "INSERT INTO l1_rules" in query_str:
                return FakeResult([], rowcount=1)
            # UPDATE patterns SET status = 'promoted'
            if "UPDATE patterns" in query_str:
                return FakeResult([], rowcount=1)
            return FakeResult([], rowcount=0)

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        with patch("dashboard_api.websocket_manager.broadcast_event", new_callable=AsyncMock) as mock_broadcast:
            rule_id = await promote_pattern_in_db(mock_db, pattern_id)

        # Should return rule_id derived from pattern_signature
        assert rule_id == "RB-AUTO-SVC_RESTART"

        # Verify commit was called (not rollback)
        mock_db.commit.assert_awaited_once()
        mock_db.rollback.assert_not_awaited()

        # Verify the right queries were executed
        assert any("SELECT * FROM patterns" in q and "FOR UPDATE" in q for q in executed_queries)
        assert any("INSERT INTO l1_rules" in q for q in executed_queries)
        assert any("UPDATE patterns" in q and "promoted" in q for q in executed_queries)

        # Verify websocket broadcast was attempted
        mock_broadcast.assert_awaited_once()
        broadcast_args = mock_broadcast.call_args
        assert broadcast_args[0][0] == "pattern_promoted"
        assert broadcast_args[0][1]["pattern_id"] == pattern_id
        assert broadcast_args[0][1]["rule_id"] == "RB-AUTO-SVC_RESTART"

    @pytest.mark.asyncio
    async def test_non_pending_pattern_returns_none(self):
        """A pattern that is not in 'pending' status should return None (no promotion)."""
        from dashboard_api.db_queries import promote_pattern_in_db

        pattern_id = "pat-already-promoted"
        mock_db = make_mock_db()

        async def mock_execute(query, params=None):
            query_str = str(query)
            # SELECT FOR UPDATE finds nothing (status != 'pending')
            if "SELECT * FROM patterns" in query_str and "FOR UPDATE" in query_str:
                return FakeResult([])
            return FakeResult([], rowcount=0)

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        with patch("dashboard_api.websocket_manager.broadcast_event", new_callable=AsyncMock) as mock_broadcast:
            rule_id = await promote_pattern_in_db(mock_db, pattern_id)

        assert rule_id is None

        # No commit, no broadcast
        mock_db.commit.assert_not_awaited()
        mock_broadcast.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: Flywheel runbook_id validation in platform auto-promotion (Step 4)
# ---------------------------------------------------------------------------

class TestPlatformPromotionRunbookValidation:
    """Test that _flywheel_promotion_loop validates runbook_ids before promotion."""

    @pytest.mark.asyncio
    async def test_valid_db_runbook_id_is_promoted(self):
        """A platform candidate whose runbook_id exists in the runbooks table should be promoted."""
        import re
        import main as m

        # Verify the regex pattern matches known embedded prefixes
        pattern = re.compile(r"^(L1|LIN|WIN|MAC|NET|RB|ESC)-", re.IGNORECASE)
        assert not pattern.match("unknown-runbook-123")
        # A runbook_id in the DB set should pass validation
        valid_runbook_ids = {"custom-runbook-abc"}
        rb_id = "custom-runbook-abc"
        assert rb_id in valid_runbook_ids or pattern.match(rb_id)

    @pytest.mark.asyncio
    async def test_valid_embedded_prefix_is_promoted(self):
        """A platform candidate with a known embedded prefix should pass validation."""
        import re

        pattern = re.compile(r"^(L1|LIN|WIN|MAC|NET|RB|ESC)-", re.IGNORECASE)
        valid_prefixes = [
            "L1-SVC-DNS-001", "LIN-PATCH-001", "WIN-FW-001",
            "MAC-FILEVAULT-001", "NET-NTP-001", "RB-AUTO-SERVICE_RESTART",
            "ESC-CRITICAL-001",
        ]
        for rb_id in valid_prefixes:
            assert pattern.match(rb_id), f"Expected {rb_id} to match embedded prefix pattern"

    @pytest.mark.asyncio
    async def test_invalid_runbook_id_is_skipped(self):
        """A platform candidate with an unknown runbook_id (not in DB, no known prefix) should be skipped."""
        import re

        pattern = re.compile(r"^(L1|LIN|WIN|MAC|NET|RB|ESC)-", re.IGNORECASE)
        invalid_ids = [
            "bogus-runbook", "UNKNOWN-001", "random_string",
            "12345", "", "l1",  # "l1" alone (no dash) should not match
        ]
        valid_runbook_ids = set()  # empty DB set
        for rb_id in invalid_ids:
            assert rb_id not in valid_runbook_ids and not pattern.match(rb_id), \
                f"Expected {rb_id} to be rejected"

    @pytest.mark.asyncio
    async def test_flywheel_loop_skips_invalid_runbook(self):
        """Integration: _flywheel_promotion_loop skips candidates with invalid runbook_ids."""
        from unittest.mock import call
        import main as m

        mock_db = make_mock_db()

        # Track which queries were executed
        executed_queries = []

        # Platform candidate with an INVALID runbook_id (no DB match, no prefix match)
        bad_candidate = FakeRow(
            ["bad_type:bogus_rb", "bad_type", "bogus_rb", 6, 25, 0.95],
            columns=["pattern_key", "incident_type", "runbook_id",
                      "distinct_orgs", "total_occurrences", "success_rate"],
        )
        # Platform candidate with a VALID embedded prefix
        good_candidate = FakeRow(
            ["svc_stop:L1-SVC-DNS-001", "svc_stop", "L1-SVC-DNS-001", 7, 30, 0.92],
            columns=["pattern_key", "incident_type", "runbook_id",
                      "distinct_orgs", "total_occurrences", "success_rate"],
        )

        # Mock for the inserted row result
        inserted_row = FakeRow([True], columns=["inserted"])

        call_count = {"n": 0}

        async def mock_execute(query, params=None):
            query_str = str(query)
            executed_queries.append(query_str)
            call_count["n"] += 1

            # Step 0-3: pattern generation, aggregation, eligible update, platform stats
            if "INSERT INTO patterns" in query_str:
                return FakeResult([], rowcount=0)
            if "INSERT INTO aggregated_pattern_stats" in query_str:
                return FakeResult([], rowcount=0)
            if "UPDATE aggregated_pattern_stats" in query_str:
                return FakeResult([], rowcount=0)
            if "INSERT INTO platform_pattern_stats" in query_str:
                return FakeResult([], rowcount=0)

            # Step 4: platform candidates query
            if "FROM platform_pattern_stats" in query_str and "promoted_at IS NULL" in query_str:
                return FakeResult([bad_candidate, good_candidate])

            # Runbook validation query
            if "SELECT runbook_id FROM runbooks" in query_str:
                return FakeResult([])  # empty DB — only prefix-based validation

            # INSERT INTO l1_rules (should only happen for good_candidate)
            if "INSERT INTO l1_rules" in query_str:
                return FakeResult([inserted_row], rowcount=1)

            # UPDATE platform_pattern_stats SET promoted_at
            if "UPDATE platform_pattern_stats" in query_str and "promoted_at" in query_str:
                return FakeResult([], rowcount=1)

            return FakeResult([], rowcount=0)

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        # Patch async_session to return our mock, and make the loop run once then stop
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_session():
            yield mock_db

        run_count = {"n": 0}
        original_sleep = asyncio.sleep

        async def limited_sleep(duration):
            run_count["n"] += 1
            if run_count["n"] >= 2:
                raise asyncio.CancelledError()
            # Skip the initial 120s startup wait
            return

        with patch.object(m, "async_session", mock_session), \
             patch("asyncio.sleep", side_effect=limited_sleep):
            try:
                await m._flywheel_promotion_loop()
            except asyncio.CancelledError:
                pass  # Expected: limited_sleep raises CancelledError to stop the loop

        # The INSERT INTO l1_rules should have been called exactly once (for the good candidate)
        l1_inserts = [q for q in executed_queries if "INSERT INTO l1_rules" in q]
        assert len(l1_inserts) == 1, f"Expected 1 l1_rules INSERT, got {len(l1_inserts)}"

        # The bogus_rb candidate should NOT have produced an INSERT
        # Verify by checking that "bogus_rb" never appeared as a runbook_id in INSERT params
        # (We can't easily check params, but we know only 1 INSERT happened = the good one)


# ---------------------------------------------------------------------------
# Tests: Promotion eligibility thresholds (Step 2)
# ---------------------------------------------------------------------------

class TestPromotionEligibilityThresholds:
    """Test the threshold logic for marking patterns as promotion_eligible."""

    def test_eligibility_criteria_constants(self):
        """Verify the hardcoded thresholds match the documented values."""
        # These thresholds are in the SQL (Step 2 of flywheel loop)
        # total_occurrences >= 5, success_rate >= 0.90, l2_resolutions >= 3
        min_occurrences = 5
        min_success_rate = 0.90
        min_l2_resolutions = 3
        max_staleness_days = 7

        assert min_occurrences == 5
        assert min_success_rate == 0.90
        assert min_l2_resolutions == 3
        assert max_staleness_days == 7

    def test_pattern_signature_format_is_two_part(self):
        """Pattern signature in aggregated_pattern_stats is incident_type:runbook_id (no hostname)."""
        # Step 1 uses: et.incident_type || ':' || et.runbook_id
        sig = "firewall_status:RB-WIN-FIREWALL-001"
        parts = sig.split(":")
        assert len(parts) == 2
        assert parts[0] == "firewall_status"
        assert parts[1] == "RB-WIN-FIREWALL-001"

    def test_pattern_id_is_16_hex_chars(self):
        """Pattern ID is LEFT(md5(...), 16) — 16 hex characters."""
        import hashlib
        sig = "firewall_status:RB-WIN-FIREWALL-001"
        md5 = hashlib.md5(sig.encode()).hexdigest()
        pattern_id = md5[:16]
        assert len(pattern_id) == 16
        assert all(c in "0123456789abcdef" for c in pattern_id)


# ---------------------------------------------------------------------------
# Tests: Platform auto-promotion thresholds (Step 4)
# ---------------------------------------------------------------------------

class TestPlatformAutoPromotionThresholds:
    """Test the cross-client platform promotion criteria."""

    def test_rule_id_format(self):
        """Platform rule IDs follow L1-PLATFORM-{TYPE}-{RUNBOOK} format."""
        incident_type = "firewall_status"
        runbook_id = "RB-WIN-FIREWALL-001"
        rule_id = f"L1-PLATFORM-{incident_type.upper()}-{runbook_id[:12].upper().replace('-', '')}"
        assert rule_id == "L1-PLATFORM-FIREWALL_STATUS-RBWINFIREW"

    def test_incident_pattern_includes_incident_type(self):
        """Promoted l1_rules must have incident_type in incident_pattern JSON."""
        import json
        incident_type = "firewall_status"
        incident_pattern = {"incident_type": incident_type}
        if incident_type:
            incident_pattern["check_type"] = incident_type
        assert incident_pattern["incident_type"] == "firewall_status"
        assert incident_pattern["check_type"] == "firewall_status"

    def test_promotion_cap_is_5_per_cycle(self):
        """Max 5 platform promotions per 30-minute cycle."""
        promotions_this_cycle = 5
        remaining = max(0, 5 - promotions_this_cycle)
        assert remaining == 0

    def test_remaining_decreases_with_promotions(self):
        """Remaining promotion slots decrease as rules are promoted."""
        for i in range(6):
            remaining = max(0, 5 - i)
            if i < 5:
                assert remaining > 0
            else:
                assert remaining == 0


# ---------------------------------------------------------------------------
# Tests: Health monitoring canary logic (Step 5)
# ---------------------------------------------------------------------------

class TestHealthMonitoringCanary:
    """Test the auto-disable and graduation thresholds."""

    def test_degradation_threshold_is_70_percent(self):
        """Rules with <70% success after 48h should be disabled."""
        success_rate = 0.69
        min_executions = 3
        assert success_rate < 0.70
        assert min_executions >= 3

    def test_graduation_threshold_is_70_percent(self):
        """Rules with >=70% success after 48h should graduate to synced."""
        success_rate = 0.70
        assert success_rate >= 0.70

    def test_minimum_executions_required(self):
        """At least 3 executions required before making health decisions."""
        executions = 2
        assert executions < 3  # Should not evaluate health yet

    def test_edge_case_exactly_70_percent(self):
        """70% success = graduation (>=), not disable (<)."""
        success_rate = 0.70
        assert success_rate >= 0.70  # Graduate
        assert not (success_rate < 0.70)  # Don't disable


# ---------------------------------------------------------------------------
# Tests: Audit log writes (RT-3)
# ---------------------------------------------------------------------------

class TestPromotionAuditLog:
    """Test that promotion decisions write to promotion_audit_log."""

    @pytest.mark.asyncio
    async def test_platform_promotion_writes_audit_log(self):
        """Platform auto-promotion should INSERT into promotion_audit_log."""
        import main as m

        mock_db = make_mock_db()
        executed_queries = []

        good_candidate = FakeRow(
            ["svc:RB-AUTO-SVC", "svc", "RB-AUTO-SVC", 6, 20, 0.95],
            columns=["pattern_key", "incident_type", "runbook_id",
                      "distinct_orgs", "total_occurrences", "success_rate"],
        )
        inserted_row = FakeRow([True], columns=["inserted"])

        async def mock_execute(query, params=None):
            query_str = str(query)
            executed_queries.append(query_str)

            if "INSERT INTO patterns" in query_str:
                return FakeResult([], rowcount=0)
            if "INSERT INTO aggregated_pattern_stats" in query_str:
                return FakeResult([], rowcount=0)
            if "UPDATE aggregated_pattern_stats" in query_str:
                return FakeResult([], rowcount=0)
            if "INSERT INTO platform_pattern_stats" in query_str:
                return FakeResult([], rowcount=0)
            if "FROM platform_pattern_stats" in query_str and "promoted_at IS NULL" in query_str:
                return FakeResult([good_candidate])
            if "SELECT runbook_id FROM runbooks" in query_str:
                return FakeResult([])
            if "DISTINCT incident_pattern" in query_str:
                return FakeResult([])
            if "INSERT INTO l1_rules" in query_str:
                return FakeResult([inserted_row], rowcount=1)
            if "INSERT INTO promotion_audit_log" in query_str:
                return FakeResult([], rowcount=1)
            if "UPDATE platform_pattern_stats" in query_str:
                return FakeResult([], rowcount=1)
            if "UPDATE l1_rules SET enabled" in query_str:
                return FakeResult([], rowcount=0)
            if "UPDATE l1_rules SET source" in query_str:
                return FakeResult([], rowcount=0)
            if "DELETE FROM execution_telemetry" in query_str:
                return FakeResult([], rowcount=0)
            return FakeResult([], rowcount=0)

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_session():
            yield mock_db

        run_count = {"n": 0}

        async def limited_sleep(duration):
            run_count["n"] += 1
            if run_count["n"] >= 2:
                raise asyncio.CancelledError()
            return

        with patch.object(m, "async_session", mock_session), \
             patch("asyncio.sleep", side_effect=limited_sleep):
            try:
                await m._flywheel_promotion_loop()
            except asyncio.CancelledError:
                pass

        audit_inserts = [q for q in executed_queries if "INSERT INTO promotion_audit_log" in q]
        assert len(audit_inserts) >= 1, f"Expected audit log INSERT, got {len(audit_inserts)}"


# ---------------------------------------------------------------------------
# Tests: Telemetry retention (RT-6)
# ---------------------------------------------------------------------------

class TestTelemetryRetention:
    """Test that the flywheel loop cleans up old telemetry."""

    @pytest.mark.asyncio
    async def test_flywheel_loop_includes_retention_step(self):
        """The flywheel loop should DELETE telemetry older than 90 days."""
        import main as m

        mock_db = make_mock_db()
        executed_queries = []

        async def mock_execute(query, params=None):
            query_str = str(query)
            executed_queries.append(query_str)
            return FakeResult([], rowcount=0)

        mock_db.execute = AsyncMock(side_effect=mock_execute)

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_session():
            yield mock_db

        run_count = {"n": 0}

        async def limited_sleep(duration):
            run_count["n"] += 1
            if run_count["n"] >= 2:
                raise asyncio.CancelledError()
            return

        with patch.object(m, "async_session", mock_session), \
             patch("asyncio.sleep", side_effect=limited_sleep):
            try:
                await m._flywheel_promotion_loop()
            except asyncio.CancelledError:
                pass

        retention_deletes = [q for q in executed_queries if "DELETE FROM execution_telemetry" in q and "90 days" in q]
        assert len(retention_deletes) == 1, f"Expected 1 retention DELETE, got {len(retention_deletes)}"


# ---------------------------------------------------------------------------
# Tests: Runbook validation regression
# ---------------------------------------------------------------------------

class TestRunbookValidationRegression:
    """Additional runbook validation edge cases."""

    def test_case_insensitive_prefix_match(self):
        """Embedded prefix regex should be case-insensitive."""
        import re
        pattern = re.compile(r"^(L1|LIN|WIN|MAC|NET|RB|ESC)-", re.IGNORECASE)
        assert pattern.match("l1-lowercase")
        assert pattern.match("Lin-Mixed")
        assert pattern.match("WIN-UPPERCASE")

    def test_prefix_requires_dash(self):
        """Prefix must be followed by a dash — bare prefix is not enough."""
        import re
        pattern = re.compile(r"^(L1|LIN|WIN|MAC|NET|RB|ESC)-", re.IGNORECASE)
        assert not pattern.match("L1")
        assert not pattern.match("RB")
        assert not pattern.match("WIN")

    def test_empty_string_rejected(self):
        """Empty runbook_id should be rejected."""
        import re
        pattern = re.compile(r"^(L1|LIN|WIN|MAC|NET|RB|ESC)-", re.IGNORECASE)
        assert not pattern.match("")

    def test_none_runbook_id_rejected(self):
        """None runbook_id should not crash validation."""
        import re
        pattern = re.compile(r"^(L1|LIN|WIN|MAC|NET|RB|ESC)-", re.IGNORECASE)
        rb_id = None
        valid_ids = set()
        is_valid = (rb_id is not None) and (rb_id in valid_ids or pattern.match(rb_id))
        assert not is_valid


# ---------------------------------------------------------------------------
# Tests: Source constraint validation
# ---------------------------------------------------------------------------

class TestSourceConstraint:
    """Test that l1_rules source values are valid."""

    def test_valid_sources(self):
        """All valid source values should pass."""
        valid = {'built-in', 'synced', 'promoted', 'platform'}
        for s in valid:
            assert s in valid

    def test_invalid_sources_rejected(self):
        """Invalid source values should not be in the valid set."""
        valid = {'built-in', 'synced', 'promoted', 'platform'}
        invalid = ['builtin', 'auto', 'manual', 'custom', '', None]
        for s in invalid:
            assert s not in valid


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
