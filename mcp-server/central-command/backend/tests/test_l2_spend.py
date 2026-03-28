"""Tests for L2 spend controls: monitoring-only guard and decision cache.

Verifies:
1. Monitoring-only check types are blocked from L2 without calling LLM
2. L2 decision cache returns cached results for same pattern_signature
"""

import os
import sys
import types
import uuid
from datetime import datetime, timezone, timedelta
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

import main as _main_module  # noqa: E402, F401

# Import functions and constants after stub cleanup, at module level.
# We do NOT import Pydantic models here because other test files may install
# stubs that break BaseModel. Instead we use SimpleNamespace for request objects.
from dashboard_api.agent_api import agent_l2_plan, MONITORING_ONLY_CHECKS  # noqa: E402
from dashboard_api.l2_planner import L2Decision, lookup_cached_l2_decision  # noqa: E402


def _make_l2_request(**kwargs):
    """Create a request-like object for agent_l2_plan without Pydantic."""
    defaults = {
        "incident_id": str(uuid.uuid4()),
        "site_id": "site-1",
        "host_id": "host-1",
        "incident_type": "service_down",
        "severity": "medium",
        "raw_data": {},
        "pattern_signature": "",
        "created_at": "",
    }
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeRow:
    def __init__(self, values):
        self._values = values

    def __getitem__(self, idx):
        return self._values[idx]


class FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def scalar(self):
        row = self.fetchone()
        return row[0] if row else None


# ---------------------------------------------------------------------------
# Task 1: MONITORING_ONLY_CHECKS membership
# ---------------------------------------------------------------------------

class TestMonitoringOnlyChecks:
    """Verify monitoring-only check types are in the set."""

    def test_device_unreachable_in_monitoring_only(self):
        assert "device_unreachable" in MONITORING_ONLY_CHECKS

    def test_screen_lock_in_monitoring_only(self):
        assert "screen_lock" in MONITORING_ONLY_CHECKS

    def test_screen_lock_policy_in_monitoring_only(self):
        assert "screen_lock_policy" in MONITORING_ONLY_CHECKS

    def test_bitlocker_in_monitoring_only(self):
        assert "bitlocker" in MONITORING_ONLY_CHECKS

    def test_bitlocker_status_in_monitoring_only(self):
        assert "bitlocker_status" in MONITORING_ONLY_CHECKS

    def test_net_host_reachability_in_monitoring_only(self):
        assert "net_host_reachability" in MONITORING_ONLY_CHECKS


# ---------------------------------------------------------------------------
# Task 1: L2 endpoint rejects monitoring-only checks
# ---------------------------------------------------------------------------

class TestL2MonitoringOnlyGuard:
    """L2 endpoint returns escalate for monitoring-only types without calling LLM."""

    @pytest.mark.asyncio
    async def test_monitoring_only_incident_type_skips_llm(self):
        """When incident_type is monitoring-only, L2 returns escalate without LLM."""
        request = _make_l2_request(
            incident_id=str(uuid.uuid4()),
            site_id="site-1",
            host_id="host-1",
            incident_type="device_unreachable",
            severity="medium",
            raw_data={"check_type": "device_unreachable"},
        )

        mock_db = AsyncMock()
        with patch("dashboard_api.l2_planner.is_l2_available", return_value=True), \
             patch("dashboard_api.l2_planner.analyze_incident") as mock_llm:

            result = await agent_l2_plan(request, db=mock_db, auth_site_id="site-1")

            # LLM should NOT have been called
            mock_llm.assert_not_called()

            assert result["recommended_action"] == "escalate"
            assert result["escalate_to_l3"] is True
            assert result["confidence"] == 0.0
            assert result["context_used"]["skipped_reason"] == "monitoring_only"

    @pytest.mark.asyncio
    async def test_monitoring_only_check_type_skips_llm(self):
        """When raw_data.check_type is monitoring-only, L2 returns escalate."""
        request = _make_l2_request(
            incident_id=str(uuid.uuid4()),
            site_id="site-1",
            host_id="host-1",
            incident_type="drift_detected",
            severity="medium",
            raw_data={"check_type": "screen_lock_policy"},
        )

        mock_db = AsyncMock()
        with patch("dashboard_api.l2_planner.is_l2_available", return_value=True), \
             patch("dashboard_api.l2_planner.analyze_incident") as mock_llm:

            result = await agent_l2_plan(request, db=mock_db, auth_site_id="site-1")

            mock_llm.assert_not_called()
            assert result["recommended_action"] == "escalate"
            assert result["context_used"]["skipped_reason"] == "monitoring_only"

    @pytest.mark.asyncio
    async def test_non_monitoring_type_proceeds_to_llm(self):
        """Non-monitoring incident types should proceed to LLM normally."""
        from dashboard_api.agent_api import agent_l2_plan, L2PlanRequest
        from dashboard_api.l2_planner import L2Decision

        request = _make_l2_request(
            incident_id=str(uuid.uuid4()),
            site_id="site-1",
            host_id="host-1",
            incident_type="service_down",
            severity="high",
            raw_data={"check_type": "service_down"},
        )

        mock_decision = L2Decision(
            runbook_id="RB-SVC-001",
            reasoning="Service needs restart",
            confidence=0.8,
            alternative_runbooks=[],
            requires_human_review=False,
            pattern_signature="abc123",
            llm_model="claude-sonnet-4-20250514",
            llm_latency_ms=1200,
        )

        mock_db = AsyncMock()
        with patch("dashboard_api.l2_planner.is_l2_available", return_value=True), \
             patch("dashboard_api.l2_planner.analyze_incident", new_callable=AsyncMock, return_value=mock_decision), \
             patch("dashboard_api.l2_planner.record_l2_decision", new_callable=AsyncMock):

            result = await agent_l2_plan(request, db=mock_db, auth_site_id="site-1")

            assert result["recommended_action"] == "execute_runbook"
            assert result["runbook_id"] == "RB-SVC-001"


# ---------------------------------------------------------------------------
# Task 2: L2 decision cache
# ---------------------------------------------------------------------------

class TestL2DecisionCache:
    """Test L2 decision caching via lookup_cached_l2_decision."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_decision(self):
        """A recent cached decision should be returned."""
        mock_row = FakeRow([
            "RB-SVC-001",       # runbook_id
            "Restart service",  # reasoning
            0.85,               # confidence
            "sig123",           # pattern_signature
            "claude-sonnet-4",  # llm_model
            1200,               # llm_latency_ms
            False,              # requires_human_review
        ])

        mock_db = AsyncMock()
        mock_db.execute.return_value = FakeResult(rows=[mock_row])

        result = await lookup_cached_l2_decision(mock_db, "sig123")

        assert result is not None
        assert result.runbook_id == "RB-SVC-001"
        assert result.confidence == 0.85
        assert result.reasoning == "Restart service"

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        """No cached decision returns None."""
        mock_db = AsyncMock()
        mock_db.execute.return_value = FakeResult(rows=[])

        result = await lookup_cached_l2_decision(mock_db, "sig_no_match")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_pattern_returns_none(self):
        """Empty pattern signature should return None without querying."""
        mock_db = AsyncMock()
        result = await lookup_cached_l2_decision(mock_db, "")

        assert result is None
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_hit_skips_llm_in_endpoint(self):
        """When cache hits in the endpoint, LLM is not called."""
        cached_decision = L2Decision(
            runbook_id="RB-SVC-002",
            reasoning="Cached: restart service",
            confidence=0.9,
            alternative_runbooks=[],
            requires_human_review=False,
            pattern_signature="cached_sig",
            llm_model="claude-sonnet-4",
            llm_latency_ms=1000,
        )

        request = _make_l2_request(
            incident_id=str(uuid.uuid4()),
            site_id="site-1",
            host_id="host-1",
            incident_type="service_down",
            severity="high",
            raw_data={"check_type": "service_down"},
            pattern_signature="cached_sig",
        )

        mock_db = AsyncMock()
        with patch("dashboard_api.l2_planner.is_l2_available", return_value=True), \
             patch("dashboard_api.l2_planner.lookup_cached_l2_decision", new_callable=AsyncMock, return_value=cached_decision), \
             patch("dashboard_api.l2_planner.analyze_incident") as mock_llm:

            result = await agent_l2_plan(request, db=mock_db, auth_site_id="site-1")

            mock_llm.assert_not_called()
            assert result["recommended_action"] == "execute_runbook"
            assert result["runbook_id"] == "RB-SVC-002"
            assert result["context_used"]["cache_status"] == "cached_24h"

    @pytest.mark.asyncio
    async def test_cache_miss_calls_llm(self):
        """When cache misses, LLM is called normally."""
        from dashboard_api.agent_api import agent_l2_plan, L2PlanRequest
        mock_decision = L2Decision(
            runbook_id="RB-SVC-003",
            reasoning="LLM analysis",
            confidence=0.7,
            alternative_runbooks=[],
            requires_human_review=False,
            pattern_signature="new_sig",
            llm_model="claude-sonnet-4",
            llm_latency_ms=2000,
        )

        request = _make_l2_request(
            incident_id=str(uuid.uuid4()),
            site_id="site-1",
            host_id="host-1",
            incident_type="service_down",
            severity="high",
            raw_data={"check_type": "service_down"},
            pattern_signature="new_sig",
        )

        mock_db = AsyncMock()
        with patch("dashboard_api.l2_planner.is_l2_available", return_value=True), \
             patch("dashboard_api.l2_planner.lookup_cached_l2_decision", new_callable=AsyncMock, return_value=None), \
             patch("dashboard_api.l2_planner.analyze_incident", new_callable=AsyncMock, return_value=mock_decision), \
             patch("dashboard_api.l2_planner.record_l2_decision", new_callable=AsyncMock):

            result = await agent_l2_plan(request, db=mock_db, auth_site_id="site-1")

            assert result["recommended_action"] == "execute_runbook"
            assert result["runbook_id"] == "RB-SVC-003"
            assert "cache_status" not in result.get("context_used", {})
