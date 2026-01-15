"""Tests for gRPC server integration with Go agents."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from compliance_agent.grpc_server import (
    AgentRegistry,
    AgentState,
    get_grpc_stats,
    GRPC_AVAILABLE,
)


class TestAgentRegistry:
    """Test agent registry functionality."""

    def test_register_agent(self):
        """Test registering a Go agent."""
        registry = AgentRegistry()
        state = AgentState(
            agent_id="go-test-agent-1",
            hostname="WORKSTATION01",
            tier=0,  # MONITOR_ONLY
        )

        registry.register(state)

        assert registry.get_connected_count() == 1
        assert "go-test-agent-1" in registry.agents
        assert registry.get_agent("go-test-agent-1") == state

    def test_unregister_agent(self):
        """Test unregistering a Go agent."""
        registry = AgentRegistry()
        state = AgentState(
            agent_id="go-test-agent-1",
            hostname="WORKSTATION01",
            tier=0,
        )

        registry.register(state)
        registry.unregister("go-test-agent-1")

        assert registry.get_connected_count() == 0
        assert registry.get_agent("go-test-agent-1") is None

    def test_config_version_tracking(self):
        """Test config version change detection."""
        registry = AgentRegistry()

        # Should return False for unknown agent
        assert not registry.config_version_changed("unknown-agent")

    def test_get_all_agents(self):
        """Test getting all registered agents."""
        registry = AgentRegistry()

        state1 = AgentState("go-agent-1", "WS01", 0)
        state2 = AgentState("go-agent-2", "WS02", 0)

        registry.register(state1)
        registry.register(state2)

        agents = registry.get_all_agents()
        assert len(agents) == 2


class TestAgentState:
    """Test agent state tracking."""

    def test_initial_state(self):
        """Test initial agent state values."""
        state = AgentState(
            agent_id="go-test-1",
            hostname="WORKSTATION01",
            tier=0,
        )

        assert state.agent_id == "go-test-1"
        assert state.hostname == "WORKSTATION01"
        assert state.tier == 0
        assert state.drift_count == 0
        assert state.rmm_agents == []
        assert isinstance(state.connected_at, datetime)
        assert isinstance(state.last_heartbeat, datetime)

    def test_update_drift_count(self):
        """Test incrementing drift count."""
        state = AgentState("go-test-1", "WS01", 0)

        state.drift_count += 1
        assert state.drift_count == 1

        state.drift_count += 5
        assert state.drift_count == 6


class TestGRPCStats:
    """Test gRPC statistics reporting."""

    def test_get_stats_empty(self):
        """Test stats with no agents."""
        registry = AgentRegistry()
        stats = get_grpc_stats(registry)

        assert stats["connected_agents"] == 0
        assert stats["agents"] == []
        assert "grpc_available" in stats

    def test_get_stats_with_agents(self):
        """Test stats with registered agents."""
        registry = AgentRegistry()

        state = AgentState("go-agent-1", "WORKSTATION01", 0)
        state.drift_count = 5
        registry.register(state)

        stats = get_grpc_stats(registry)

        assert stats["connected_agents"] == 1
        assert len(stats["agents"]) == 1
        assert stats["agents"][0]["hostname"] == "WORKSTATION01"
        assert stats["agents"][0]["drift_count"] == 5


@pytest.mark.skipif(not GRPC_AVAILABLE, reason="grpcio not installed")
class TestComplianceAgentServicer:
    """Test gRPC servicer functionality."""

    @pytest.mark.asyncio
    async def test_register_creates_agent_id(self):
        """Test that registration creates a unique agent ID."""
        from compliance_agent.grpc_server import ComplianceAgentServicer

        registry = AgentRegistry()
        servicer = ComplianceAgentServicer(registry)

        # Mock request
        request = MagicMock()
        request.hostname = "WORKSTATION01"
        request.os_version = "Windows 10"
        request.agent_version = "0.1.0"
        request.machine_guid = "test-guid"
        request.installed_software = []

        context = MagicMock()

        response = await servicer.Register(request, context)

        assert "agent_id" in response
        assert response["agent_id"].startswith("go-WORKSTATION01-")
        assert response["check_interval_seconds"] == 300
        assert "bitlocker" in response["enabled_checks"]

    @pytest.mark.asyncio
    async def test_heartbeat_updates_timestamp(self):
        """Test that heartbeat updates last seen time."""
        from compliance_agent.grpc_server import ComplianceAgentServicer

        registry = AgentRegistry()
        state = AgentState("go-test-1", "WS01", 0)
        registry.register(state)

        servicer = ComplianceAgentServicer(registry)

        # Mock request
        request = MagicMock()
        request.agent_id = "go-test-1"
        request.timestamp = 1234567890

        context = MagicMock()

        old_heartbeat = state.last_heartbeat
        response = await servicer.SendHeartbeat(request, context)

        assert response["acknowledged"] is True
        assert state.last_heartbeat >= old_heartbeat


class TestDriftRouting:
    """Test drift event routing to healing pipeline."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not GRPC_AVAILABLE, reason="grpcio not installed")
    async def test_drift_without_healing_engine(self):
        """Test drift handling when healing engine not configured."""
        from compliance_agent.grpc_server import ComplianceAgentServicer

        registry = AgentRegistry()
        servicer = ComplianceAgentServicer(
            registry,
            healing_engine=None,  # No healing engine
        )

        # Should not raise, just log warning
        event = MagicMock()
        event.hostname = "WS01"
        event.check_type = "bitlocker"
        event.passed = False
        event.hipaa_control = "164.312(a)(2)(iv)"
        event.expected = "ProtectionStatus=1"
        event.actual = "ProtectionStatus=0"
        event.metadata = {}

        await servicer._route_drift_to_healing(event)
        # Should complete without error

    @pytest.mark.asyncio
    @pytest.mark.skipif(not GRPC_AVAILABLE, reason="grpcio not installed")
    async def test_drift_with_healing_engine(self):
        """Test drift routing through healing engine."""
        from compliance_agent.grpc_server import ComplianceAgentServicer

        registry = AgentRegistry()

        # Mock healing engine
        healing_engine = AsyncMock()
        healing_result = MagicMock()
        healing_result.success = True
        healing_result.runbook_id = "RB-WIN-BITLOCKER"
        healing_engine.heal.return_value = healing_result

        # Mock config
        config = MagicMock()
        config.site_id = "test-site"

        servicer = ComplianceAgentServicer(
            registry,
            healing_engine=healing_engine,
            config=config,
        )

        event = MagicMock()
        event.hostname = "WS01"
        event.check_type = "bitlocker"
        event.passed = False
        event.hipaa_control = "164.312(a)(2)(iv)"
        event.expected = "ProtectionStatus=1"
        event.actual = "ProtectionStatus=0"
        event.metadata = {}

        await servicer._route_drift_to_healing(event)

        # Verify healing engine was called
        healing_engine.heal.assert_called_once()
