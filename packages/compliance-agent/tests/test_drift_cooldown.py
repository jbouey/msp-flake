"""
Tests for drift report cooldown behavior.

Covers the flapping scenario: heal → pass → GPO revert → fail → heal loop.
The cooldown should NOT be cleared on pass, preventing incident spam.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def agent_with_healer():
    """Create ApplianceAgent with mocked auto_healer for cooldown tests."""
    from compliance_agent.appliance_agent import ApplianceAgent

    agent = ApplianceAgent.__new__(ApplianceAgent)
    agent._drift_report_times = {}
    agent._drift_report_cooldown = 600  # 10 min

    # Mock auto_healer
    agent.auto_healer = AsyncMock()
    agent.auto_healer.heal = AsyncMock(return_value=MagicMock(
        success=True,
        resolution_level="L1",
        action_taken="restore_firewall_baseline",
        incident_id="inc-001",
        resolution_time_ms=50,
        error=None,
    ))

    # Mock client (for report_incident, resolve_incident, report_pattern)
    agent.client = AsyncMock()
    agent.client.report_incident = AsyncMock(return_value={"incident_id": "inc-001"})
    agent.client.resolve_incident = AsyncMock()
    agent.client.report_pattern = AsyncMock()

    # Mock config
    agent.config = MagicMock()
    agent.config.site_id = "test-site"

    return agent


class TestDriftCooldown:
    """Test drift report cooldown prevents flapping loops."""

    @pytest.mark.asyncio
    async def test_first_failure_reports_incident(self, agent_with_healer):
        """First failure should always report and heal."""
        result = await agent_with_healer._handle_drift_healing(
            "firewall",
            {"status": "fail", "details": {"rules_missing": ["input-drop"]}}
        )

        assert result is not None
        assert result["success"] is True
        assert "firewall" in agent_with_healer._drift_report_times

    @pytest.mark.asyncio
    async def test_pass_does_not_clear_cooldown(self, agent_with_healer):
        """A passing check should NOT clear the cooldown timestamp."""
        # Simulate a prior failure setting cooldown
        agent_with_healer._drift_report_times["firewall"] = datetime.now(timezone.utc)

        result = await agent_with_healer._handle_drift_healing(
            "firewall",
            {"status": "pass", "details": {}}
        )

        assert result is None
        # Cooldown must still be present
        assert "firewall" in agent_with_healer._drift_report_times

    @pytest.mark.asyncio
    async def test_second_failure_within_cooldown_suppressed(self, agent_with_healer):
        """Failure within 600s of last report should be suppressed."""
        # Set cooldown 30 seconds ago
        agent_with_healer._drift_report_times["firewall"] = (
            datetime.now(timezone.utc) - timedelta(seconds=30)
        )

        result = await agent_with_healer._handle_drift_healing(
            "firewall",
            {"status": "fail", "details": {"rules_missing": ["input-drop"]}}
        )

        assert result is None
        # heal() should NOT have been called
        agent_with_healer.auto_healer.heal.assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_after_cooldown_expires_reports(self, agent_with_healer):
        """Failure after 600s cooldown should report normally."""
        # Set cooldown 601 seconds ago (expired)
        agent_with_healer._drift_report_times["firewall"] = (
            datetime.now(timezone.utc) - timedelta(seconds=601)
        )

        result = await agent_with_healer._handle_drift_healing(
            "firewall",
            {"status": "fail", "details": {"rules_missing": ["input-drop"]}}
        )

        assert result is not None
        assert result["success"] is True
        agent_with_healer.auto_healer.heal.assert_called_once()

    @pytest.mark.asyncio
    async def test_flapping_scenario_suppressed(self, agent_with_healer):
        """Simulate the full flapping loop: fail→heal→pass→fail should NOT re-report."""
        # Step 1: Initial failure — reports and heals
        result1 = await agent_with_healer._handle_drift_healing(
            "firewall",
            {"status": "fail", "details": {"rules_missing": ["input-drop"]}}
        )
        assert result1 is not None
        assert agent_with_healer.auto_healer.heal.call_count == 1

        # Step 2: Pass (heal worked) — should NOT clear cooldown
        result2 = await agent_with_healer._handle_drift_healing(
            "firewall",
            {"status": "pass", "details": {}}
        )
        assert result2 is None
        assert "firewall" in agent_with_healer._drift_report_times

        # Step 3: Fail again (GPO reverted) — should be SUPPRESSED by cooldown
        result3 = await agent_with_healer._handle_drift_healing(
            "firewall",
            {"status": "fail", "details": {"rules_missing": ["input-drop"]}}
        )
        assert result3 is None
        # heal() should still only have been called once (from step 1)
        assert agent_with_healer.auto_healer.heal.call_count == 1

    @pytest.mark.asyncio
    async def test_different_checks_have_independent_cooldowns(self, agent_with_healer):
        """Each check_name should have its own cooldown timer."""
        # Fail firewall
        await agent_with_healer._handle_drift_healing(
            "firewall",
            {"status": "fail", "details": {}}
        )
        assert agent_with_healer.auto_healer.heal.call_count == 1

        # Fail backup — should report independently
        await agent_with_healer._handle_drift_healing(
            "backup",
            {"status": "fail", "details": {}}
        )
        assert agent_with_healer.auto_healer.heal.call_count == 2

        # Fail firewall again — should be suppressed
        result = await agent_with_healer._handle_drift_healing(
            "firewall",
            {"status": "fail", "details": {}}
        )
        assert result is None
        assert agent_with_healer.auto_healer.heal.call_count == 2

    @pytest.mark.asyncio
    async def test_no_auto_healer_returns_none(self, agent_with_healer):
        """Without auto_healer, should return None immediately."""
        agent_with_healer.auto_healer = None

        result = await agent_with_healer._handle_drift_healing(
            "firewall",
            {"status": "fail", "details": {}}
        )
        assert result is None
