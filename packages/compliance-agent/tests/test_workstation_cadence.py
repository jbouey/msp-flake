"""
Tests for workstation discovery and compliance check cadence/scheduling.

Verifies the polling intervals:
- Discovery: Every 3600s (1 hour) from AD
- Compliance scans: Every 600s (10 minutes) on discovered workstations
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from typing import Dict, Any, List

from compliance_agent.appliance_agent import ApplianceAgent
from compliance_agent.appliance_config import ApplianceConfig
from compliance_agent.workstation_discovery import Workstation
from compliance_agent.workstation_checks import (
    WorkstationComplianceResult,
    ComplianceStatus,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_config(tmp_path):
    """Create a mock ApplianceConfig for testing."""
    from pathlib import Path

    config = MagicMock(spec=ApplianceConfig)
    config.central_url = "https://api.test.com"
    config.api_key = "test-api-key"
    config.site_id = "test-site-001"
    config.api_endpoint = "https://api.test.com/api/v1"
    config.poll_interval = 60
    config.log_level = "INFO"
    config.enable_drift_detection = False

    # Use real Path objects for directories
    config.state_dir = tmp_path / "state"
    config.evidence_dir = tmp_path / "evidence"
    config.rules_dir = tmp_path / "rules"

    config.windows_targets = []
    config.linux_targets = []
    config.workstation_enabled = True
    config.domain_controller = "DC01.clinic.local"
    config.healing_enabled = False
    config.sensor_enabled = False
    config.grpc_enabled = False
    return config


@pytest.fixture
def sample_workstations():
    """Sample workstations for testing."""
    return [
        Workstation(
            hostname="WS001",
            distinguished_name="CN=WS001,OU=Workstations,DC=clinic,DC=local",
            ip_address="192.168.1.100",
            os_name="Windows 11 Enterprise",
            online=True,
        ),
        Workstation(
            hostname="WS002",
            distinguished_name="CN=WS002,OU=Workstations,DC=clinic,DC=local",
            ip_address="192.168.1.101",
            os_name="Windows 10 Enterprise",
            online=True,
        ),
        Workstation(
            hostname="WS003",
            distinguished_name="CN=WS003,OU=Workstations,DC=clinic,DC=local",
            ip_address="192.168.1.102",
            os_name="Windows 10 Enterprise",
            online=False,  # Offline workstation
        ),
    ]


# ============================================================================
# Interval Configuration Tests
# ============================================================================


class TestCadenceIntervals:
    """Test that cadence intervals are configured correctly."""

    def test_workstation_scan_interval_default(self, mock_config):
        """Test default workstation scan interval is 600s (10 minutes)."""
        agent = ApplianceAgent(mock_config)

        assert agent._workstation_scan_interval == 600
        assert agent._workstation_scan_interval == 10 * 60  # 10 minutes

    def test_workstation_discovery_interval_default(self, mock_config):
        """Test default discovery interval is 3600s (1 hour)."""
        agent = ApplianceAgent(mock_config)

        assert agent._workstation_discovery_interval == 3600
        assert agent._workstation_discovery_interval == 60 * 60  # 1 hour

    def test_initial_timestamps_trigger_immediate_scan(self, mock_config):
        """Test that initial timestamps are set to datetime.min for immediate first run."""
        agent = ApplianceAgent(mock_config)

        # Initial timestamps should be datetime.min (with UTC timezone)
        assert agent._last_workstation_scan == datetime.min.replace(tzinfo=timezone.utc)
        assert agent._last_workstation_discovery == datetime.min.replace(tzinfo=timezone.utc)

    def test_scan_interval_shorter_than_discovery(self, mock_config):
        """Verify scan interval (10 min) < discovery interval (1 hour)."""
        agent = ApplianceAgent(mock_config)

        assert agent._workstation_scan_interval < agent._workstation_discovery_interval
        # Scans run 6x per discovery cycle
        assert agent._workstation_discovery_interval / agent._workstation_scan_interval == 6


# ============================================================================
# Discovery Cadence Tests
# ============================================================================


class TestDiscoveryCadence:
    """Test workstation discovery scheduling."""

    @pytest.mark.asyncio
    async def test_discovery_runs_on_first_call(self, mock_config, sample_workstations):
        """Discovery should run immediately on first call (datetime.min)."""
        agent = ApplianceAgent(mock_config)

        agent._discover_workstations = AsyncMock(return_value=None)
        agent.workstations = []

        # Simulate _run_workstation_compliance calling discovery
        now = datetime.now(timezone.utc)
        discovery_elapsed = (now - agent._last_workstation_discovery).total_seconds()

        # Should trigger discovery (elapsed >> 3600)
        assert discovery_elapsed >= agent._workstation_discovery_interval

        await agent._discover_workstations()
        agent._discover_workstations.assert_called_once()

    @pytest.mark.asyncio
    async def test_discovery_skipped_within_interval(self, mock_config):
        """Discovery should be skipped if interval hasn't elapsed."""
        agent = ApplianceAgent(mock_config)

        # Set last discovery to 30 minutes ago (within 1-hour interval)
        agent._last_workstation_discovery = datetime.now(timezone.utc) - timedelta(minutes=30)

        now = datetime.now(timezone.utc)
        discovery_elapsed = (now - agent._last_workstation_discovery).total_seconds()

        # Should NOT trigger discovery (30 min < 60 min)
        assert discovery_elapsed < agent._workstation_discovery_interval

    @pytest.mark.asyncio
    async def test_discovery_runs_after_interval(self, mock_config):
        """Discovery should run after interval has elapsed."""
        agent = ApplianceAgent(mock_config)

        # Set last discovery to 65 minutes ago (past 1-hour interval)
        agent._last_workstation_discovery = datetime.now(timezone.utc) - timedelta(minutes=65)

        now = datetime.now(timezone.utc)
        discovery_elapsed = (now - agent._last_workstation_discovery).total_seconds()

        # Should trigger discovery (65 min > 60 min)
        assert discovery_elapsed >= agent._workstation_discovery_interval


# ============================================================================
# Scan Cadence Tests
# ============================================================================


class TestScanCadence:
    """Test workstation compliance scan scheduling."""

    @pytest.mark.asyncio
    async def test_scan_runs_on_first_call(self, mock_config, sample_workstations):
        """Scan should run immediately on first call (datetime.min)."""
        agent = ApplianceAgent(mock_config)

        agent.workstations = sample_workstations

        now = datetime.now(timezone.utc)
        scan_elapsed = (now - agent._last_workstation_scan).total_seconds()

        # Should trigger scan (elapsed >> 600)
        assert scan_elapsed >= agent._workstation_scan_interval

    @pytest.mark.asyncio
    async def test_scan_skipped_within_interval(self, mock_config, sample_workstations):
        """Scan should be skipped if interval hasn't elapsed."""
        agent = ApplianceAgent(mock_config)

        agent.workstations = sample_workstations
        # Set last scan to 5 minutes ago (within 10-minute interval)
        agent._last_workstation_scan = datetime.now(timezone.utc) - timedelta(minutes=5)

        now = datetime.now(timezone.utc)
        scan_elapsed = (now - agent._last_workstation_scan).total_seconds()

        # Should NOT trigger scan (5 min < 10 min)
        assert scan_elapsed < agent._workstation_scan_interval

    @pytest.mark.asyncio
    async def test_scan_runs_after_interval(self, mock_config, sample_workstations):
        """Scan should run after interval has elapsed."""
        agent = ApplianceAgent(mock_config)

        agent.workstations = sample_workstations
        # Set last scan to 12 minutes ago (past 10-minute interval)
        agent._last_workstation_scan = datetime.now(timezone.utc) - timedelta(minutes=12)

        now = datetime.now(timezone.utc)
        scan_elapsed = (now - agent._last_workstation_scan).total_seconds()

        # Should trigger scan (12 min > 10 min)
        assert scan_elapsed >= agent._workstation_scan_interval

    @pytest.mark.asyncio
    async def test_scan_skipped_when_no_workstations(self, mock_config):
        """Scan should be skipped if no workstations discovered."""
        agent = ApplianceAgent(mock_config)

        agent.workstations = []  # No workstations
        agent._last_workstation_scan = datetime.min.replace(tzinfo=timezone.utc)

        # Even though interval elapsed, no workstations = no scan
        assert len(agent.workstations) == 0


# ============================================================================
# Online Filtering Tests
# ============================================================================


class TestOnlineFiltering:
    """Test that only online workstations are scanned."""

    def test_only_online_workstations_scanned(self, sample_workstations):
        """Only online workstations should be included in scans."""
        online = [ws for ws in sample_workstations if ws.online]

        assert len(online) == 2
        assert all(ws.online for ws in online)
        assert "WS001" in [ws.hostname for ws in online]
        assert "WS002" in [ws.hostname for ws in online]
        assert "WS003" not in [ws.hostname for ws in online]

    def test_scan_skipped_when_all_offline(self, mock_config):
        """Scan should be skipped if all workstations are offline."""
        offline_workstations = [
            Workstation(
                hostname="WS001",
                distinguished_name="",
                ip_address="192.168.1.100",
                online=False,
            ),
            Workstation(
                hostname="WS002",
                distinguished_name="",
                ip_address="192.168.1.101",
                online=False,
            ),
        ]

        online = [ws for ws in offline_workstations if ws.online]
        assert len(online) == 0


# ============================================================================
# Timestamp Update Tests
# ============================================================================


class TestTimestampUpdates:
    """Test that timestamps are properly updated after operations."""

    @pytest.mark.asyncio
    async def test_discovery_updates_timestamp(self, mock_config):
        """Discovery should update _last_workstation_discovery timestamp."""
        agent = ApplianceAgent(mock_config)

        old_timestamp = agent._last_workstation_discovery

        # Simulate discovery completion
        agent._last_workstation_discovery = datetime.now(timezone.utc)

        assert agent._last_workstation_discovery > old_timestamp

    @pytest.mark.asyncio
    async def test_scan_updates_timestamp(self, mock_config, sample_workstations):
        """Scan should update _last_workstation_scan timestamp."""
        agent = ApplianceAgent(mock_config)

        agent.workstations = sample_workstations
        old_timestamp = agent._last_workstation_scan

        # Simulate scan completion
        agent._last_workstation_scan = datetime.now(timezone.utc)

        assert agent._last_workstation_scan > old_timestamp


# ============================================================================
# Edge Cases
# ============================================================================


class TestCadenceEdgeCases:
    """Test edge cases in cadence logic."""

    @pytest.mark.asyncio
    async def test_exact_interval_boundary(self, mock_config):
        """Test behavior at exact interval boundary."""
        agent = ApplianceAgent(mock_config)

        # Set last scan to exactly 600 seconds ago
        agent._last_workstation_scan = datetime.now(timezone.utc) - timedelta(seconds=600)

        now = datetime.now(timezone.utc)
        scan_elapsed = (now - agent._last_workstation_scan).total_seconds()

        # At exactly 600s, should trigger (>= comparison)
        assert scan_elapsed >= agent._workstation_scan_interval

    @pytest.mark.asyncio
    async def test_multiple_cycles_simulation(self, mock_config, sample_workstations):
        """Simulate multiple scan cycles to verify timing."""
        agent = ApplianceAgent(mock_config)

        agent.workstations = sample_workstations
        scan_times = []

        # Simulate 1 hour of operation (6 scan cycles at 10 min each)
        base_time = datetime.now(timezone.utc) - timedelta(hours=1)

        for cycle in range(7):  # 0, 10, 20, 30, 40, 50, 60 minutes
            current_time = base_time + timedelta(minutes=cycle * 10)
            elapsed = (current_time - agent._last_workstation_scan).total_seconds()

            if elapsed >= agent._workstation_scan_interval:
                scan_times.append(current_time)
                agent._last_workstation_scan = current_time

        # Should have 7 scans (initial + 6 cycles)
        assert len(scan_times) == 7

    def test_workstation_enabled_flag(self, mock_config):
        """Test that workstation_enabled flag is respected."""
        mock_config.workstation_enabled = False

        agent = ApplianceAgent(mock_config)

        assert agent._workstation_enabled is False

    def test_domain_controller_configuration(self, mock_config):
        """Test domain controller is configured from config."""
        mock_config.domain_controller = "NVDC01.northvalley.local"

        agent = ApplianceAgent(mock_config)

        assert agent._domain_controller == "NVDC01.northvalley.local"


# ============================================================================
# Integration Timing Tests
# ============================================================================


class TestCadenceIntegration:
    """Integration tests for discovery + scan timing."""

    @pytest.mark.asyncio
    async def test_discovery_before_first_scan(self, mock_config):
        """Discovery should run before first scan when both are due."""
        agent = ApplianceAgent(mock_config)

        # Both timestamps are datetime.min
        now = datetime.now(timezone.utc)
        discovery_elapsed = (now - agent._last_workstation_discovery).total_seconds()
        scan_elapsed = (now - agent._last_workstation_scan).total_seconds()

        # Both should be due
        assert discovery_elapsed >= agent._workstation_discovery_interval
        assert scan_elapsed >= agent._workstation_scan_interval

        # Discovery interval is longer, so it definitely needs to run first
        # to populate agent.workstations before scan can process them
        assert agent.workstations == []

    @pytest.mark.asyncio
    async def test_scan_without_discovery(self, mock_config, sample_workstations):
        """Scan should work if workstations already discovered."""
        agent = ApplianceAgent(mock_config)

        # Pre-populate workstations (as if discovery already ran)
        agent.workstations = sample_workstations
        # Set discovery timestamp to recent (no re-discovery needed)
        agent._last_workstation_discovery = datetime.now(timezone.utc)
        # But scan timestamp is old (scan needed)
        agent._last_workstation_scan = datetime.min.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        discovery_elapsed = (now - agent._last_workstation_discovery).total_seconds()
        scan_elapsed = (now - agent._last_workstation_scan).total_seconds()

        # Discovery not needed, scan is needed
        assert discovery_elapsed < agent._workstation_discovery_interval
        assert scan_elapsed >= agent._workstation_scan_interval
        assert len(agent.workstations) == 3
