"""Tests for the A/B partition health gate module.

Phase 13: Tests for post-boot health verification and automatic rollback.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from compliance_agent.health_gate import (
    get_active_partition_from_cmdline,
    get_active_partition_from_state,
    get_active_partition,
    load_update_state,
    clear_update_state,
    get_boot_count,
    increment_boot_count,
    clear_boot_count,
    set_next_boot,
    mark_current_as_good,
    run_health_checks,
    run_health_gate,
    MAX_BOOT_ATTEMPTS,
)


class TestPartitionDetection:
    """Tests for partition detection from various sources."""

    def test_cmdline_partition_a(self, tmp_path):
        """Test detecting partition A from kernel cmdline."""
        proc_cmdline = tmp_path / "cmdline"
        proc_cmdline.write_text("BOOT_IMAGE=/boot/kernel ab.partition=A quiet")

        with patch("compliance_agent.health_gate.Path") as mock_path:
            mock_path.return_value.read_text.return_value = "ab.partition=A quiet"
            result = get_active_partition_from_cmdline()
            assert result == "A"

    def test_cmdline_partition_b(self, tmp_path):
        """Test detecting partition B from kernel cmdline."""
        with patch("compliance_agent.health_gate.Path") as mock_path:
            mock_path.return_value.read_text.return_value = "ab.partition=B quiet"
            result = get_active_partition_from_cmdline()
            assert result == "B"

    def test_cmdline_no_partition(self):
        """Test handling missing partition in cmdline."""
        with patch("compliance_agent.health_gate.Path") as mock_path:
            mock_path.return_value.read_text.return_value = "quiet loglevel=3"
            result = get_active_partition_from_cmdline()
            assert result is None

    def test_state_file_grub_format(self, tmp_path):
        """Test reading GRUB source format ab_state file."""
        ab_state = tmp_path / "ab_state"
        ab_state.write_text('set active_partition="A"\n')

        with patch("compliance_agent.health_gate.AB_STATE_FILE", ab_state):
            result = get_active_partition_from_state()
            assert result == "A"

    def test_state_file_simple_format(self, tmp_path):
        """Test reading simple format ab_state file."""
        ab_state = tmp_path / "ab_state"
        ab_state.write_text("B")

        with patch("compliance_agent.health_gate.AB_STATE_FILE", ab_state):
            result = get_active_partition_from_state()
            assert result == "B"

    def test_state_file_not_exists(self, tmp_path):
        """Test handling missing ab_state file."""
        ab_state = tmp_path / "ab_state_missing"

        with patch("compliance_agent.health_gate.AB_STATE_FILE", ab_state):
            result = get_active_partition_from_state()
            assert result is None


class TestBootState:
    """Tests for boot state management."""

    def test_load_update_state(self, tmp_path):
        """Test loading update state from JSON file."""
        state_file = tmp_path / "update_state.json"
        state = {
            "update_id": "test-123",
            "version": "1.0.42",
            "target_partition": "B",
        }
        state_file.write_text(json.dumps(state))

        with patch("compliance_agent.health_gate.UPDATE_STATE_FILE", state_file):
            result = load_update_state()
            assert result["update_id"] == "test-123"
            assert result["target_partition"] == "B"

    def test_load_update_state_missing(self, tmp_path):
        """Test handling missing update state file."""
        state_file = tmp_path / "update_state_missing.json"

        with patch("compliance_agent.health_gate.UPDATE_STATE_FILE", state_file):
            result = load_update_state()
            assert result is None

    def test_clear_update_state(self, tmp_path):
        """Test clearing update state file."""
        state_file = tmp_path / "update_state.json"
        state_file.write_text('{"test": true}')

        with patch("compliance_agent.health_gate.UPDATE_STATE_FILE", state_file):
            clear_update_state()
            assert not state_file.exists()

    def test_get_boot_count_exists(self, tmp_path):
        """Test reading existing boot count."""
        boot_count_file = tmp_path / "boot_count"
        boot_count_file.write_text("2")

        with patch("compliance_agent.health_gate.BOOT_COUNT_FILE", boot_count_file):
            result = get_boot_count()
            assert result == 2

    def test_get_boot_count_missing(self, tmp_path):
        """Test boot count defaults to 0 when file missing."""
        boot_count_file = tmp_path / "boot_count_missing"

        with patch("compliance_agent.health_gate.BOOT_COUNT_FILE", boot_count_file):
            result = get_boot_count()
            assert result == 0

    def test_increment_boot_count(self, tmp_path):
        """Test incrementing boot count."""
        boot_count_file = tmp_path / "boot_count"
        boot_count_file.write_text("1")
        state_dir = tmp_path

        with patch("compliance_agent.health_gate.BOOT_COUNT_FILE", boot_count_file):
            with patch("compliance_agent.health_gate.STATE_DIR", state_dir):
                result = increment_boot_count()
                assert result == 2
                assert boot_count_file.read_text() == "2"

    def test_clear_boot_count(self, tmp_path):
        """Test clearing boot count."""
        boot_count_file = tmp_path / "boot_count"
        boot_count_file.write_text("3")

        with patch("compliance_agent.health_gate.BOOT_COUNT_FILE", boot_count_file):
            clear_boot_count()
            assert boot_count_file.read_text() == "0"


class TestNextBoot:
    """Tests for set_next_boot functionality."""

    def test_set_next_boot_a(self, tmp_path):
        """Test setting next boot to partition A."""
        ab_state = tmp_path / "boot" / "ab_state"

        with patch("compliance_agent.health_gate.AB_STATE_FILE", ab_state):
            result = set_next_boot("A")
            assert result is True
            assert ab_state.exists()
            content = ab_state.read_text()
            assert 'active_partition="A"' in content

    def test_set_next_boot_b(self, tmp_path):
        """Test setting next boot to partition B."""
        ab_state = tmp_path / "boot" / "ab_state"

        with patch("compliance_agent.health_gate.AB_STATE_FILE", ab_state):
            result = set_next_boot("B")
            assert result is True
            content = ab_state.read_text()
            assert 'active_partition="B"' in content

    def test_set_next_boot_invalid(self, tmp_path):
        """Test rejecting invalid partition."""
        ab_state = tmp_path / "boot" / "ab_state"

        with patch("compliance_agent.health_gate.AB_STATE_FILE", ab_state):
            result = set_next_boot("C")
            assert result is False
            assert not ab_state.exists()


class TestHealthChecks:
    """Tests for health check functionality."""

    @pytest.mark.asyncio
    async def test_run_health_checks_all_pass(self):
        """Test health checks when all pass."""
        with patch("compliance_agent.health_gate.check_network") as mock_net:
            with patch("compliance_agent.health_gate.check_ntp_sync") as mock_ntp:
                with patch("compliance_agent.health_gate.check_disk_space") as mock_disk:
                    mock_net.return_value = {"passed": True}
                    mock_ntp.return_value = {"passed": True}
                    mock_disk.return_value = {"passed": True, "usage_percent": 45}

                    passed, results = await run_health_checks("https://api.test.com")

                    assert passed is True
                    assert results["network"]["passed"] is True
                    assert results["ntp"]["passed"] is True
                    assert results["disk"]["passed"] is True

    @pytest.mark.asyncio
    async def test_run_health_checks_network_fail(self):
        """Test health checks when network fails."""
        with patch("compliance_agent.health_gate.check_network") as mock_net:
            with patch("compliance_agent.health_gate.check_ntp_sync") as mock_ntp:
                with patch("compliance_agent.health_gate.check_disk_space") as mock_disk:
                    mock_net.return_value = {"passed": False, "error": "Connection refused"}
                    mock_ntp.return_value = {"passed": True}
                    mock_disk.return_value = {"passed": True}

                    passed, results = await run_health_checks("https://api.test.com")

                    assert passed is False
                    assert results["network"]["passed"] is False

    @pytest.mark.asyncio
    async def test_run_health_checks_disk_full(self):
        """Test health checks when disk is full."""
        with patch("compliance_agent.health_gate.check_network") as mock_net:
            with patch("compliance_agent.health_gate.check_ntp_sync") as mock_ntp:
                with patch("compliance_agent.health_gate.check_disk_space") as mock_disk:
                    mock_net.return_value = {"passed": True}
                    mock_ntp.return_value = {"passed": True}
                    mock_disk.return_value = {"passed": False, "usage_percent": 95}

                    passed, results = await run_health_checks("https://api.test.com")

                    assert passed is False
                    assert results["disk"]["passed"] is False


class TestHealthGate:
    """Tests for the main health gate logic."""

    @pytest.mark.asyncio
    async def test_no_update_state_skips_checks(self, tmp_path):
        """Test that health gate passes through when no update pending."""
        state_file = tmp_path / "update_state.json"

        with patch("compliance_agent.health_gate.UPDATE_STATE_FILE", state_file):
            result = await run_health_gate()
            assert result == 0  # Success, nothing to verify

    @pytest.mark.asyncio
    async def test_healthy_boot_clears_state(self, tmp_path):
        """Test that successful health check clears update state."""
        state_file = tmp_path / "update_state.json"
        state = {"update_id": "test", "version": "1.0.42", "target_partition": "A"}
        state_file.write_text(json.dumps(state))

        boot_count_file = tmp_path / "boot_count"
        boot_count_file.write_text("0")

        ab_state = tmp_path / "ab_state"
        ab_state.write_text('set active_partition="A"\n')

        config = {"api_base_url": "https://api.test.com", "api_key": "test", "appliance_id": "test"}

        with patch("compliance_agent.health_gate.UPDATE_STATE_FILE", state_file):
            with patch("compliance_agent.health_gate.BOOT_COUNT_FILE", boot_count_file):
                with patch("compliance_agent.health_gate.AB_STATE_FILE", ab_state):
                    with patch("compliance_agent.health_gate.STATE_DIR", tmp_path):
                        with patch("compliance_agent.health_gate.get_active_partition", return_value="A"):
                            with patch("compliance_agent.health_gate.load_config", return_value=config):
                                with patch("compliance_agent.health_gate.run_health_checks", return_value=(True, {})):
                                    with patch("compliance_agent.health_gate.report_status", return_value=True):
                                        result = await run_health_gate()

                                        assert result == 0  # Success
                                        assert not state_file.exists()  # State cleared

    @pytest.mark.asyncio
    async def test_unhealthy_increments_counter(self, tmp_path):
        """Test that failed health check increments boot counter."""
        state_file = tmp_path / "update_state.json"
        state = {"update_id": "test", "version": "1.0.42", "target_partition": "A"}
        state_file.write_text(json.dumps(state))

        boot_count_file = tmp_path / "boot_count"
        boot_count_file.write_text("0")

        ab_state = tmp_path / "ab_state"
        ab_state.write_text('set active_partition="A"\n')

        config = {"api_base_url": "https://api.test.com", "api_key": "test", "appliance_id": "test"}

        with patch("compliance_agent.health_gate.UPDATE_STATE_FILE", state_file):
            with patch("compliance_agent.health_gate.BOOT_COUNT_FILE", boot_count_file):
                with patch("compliance_agent.health_gate.AB_STATE_FILE", ab_state):
                    with patch("compliance_agent.health_gate.STATE_DIR", tmp_path):
                        with patch("compliance_agent.health_gate.get_active_partition", return_value="A"):
                            with patch("compliance_agent.health_gate.load_config", return_value=config):
                                with patch("compliance_agent.health_gate.run_health_checks", return_value=(False, {"network": {"passed": False}})):
                                    with patch("compliance_agent.health_gate.report_status", return_value=True):
                                        result = await run_health_gate()

                                        assert result == 1  # Unhealthy, will retry
                                        assert boot_count_file.read_text() == "1"

    @pytest.mark.asyncio
    async def test_max_attempts_triggers_rollback(self, tmp_path):
        """Test that max boot attempts triggers rollback."""
        state_file = tmp_path / "update_state.json"
        state = {"update_id": "test", "version": "1.0.42", "target_partition": "A"}
        state_file.write_text(json.dumps(state))

        # Already at max attempts - 1
        boot_count_file = tmp_path / "boot_count"
        boot_count_file.write_text(str(MAX_BOOT_ATTEMPTS - 1))

        ab_state = tmp_path / "ab_state"
        ab_state.write_text('set active_partition="A"\n')

        config = {"api_base_url": "https://api.test.com", "api_key": "test", "appliance_id": "test"}

        with patch("compliance_agent.health_gate.UPDATE_STATE_FILE", state_file):
            with patch("compliance_agent.health_gate.BOOT_COUNT_FILE", boot_count_file):
                with patch("compliance_agent.health_gate.AB_STATE_FILE", ab_state):
                    with patch("compliance_agent.health_gate.STATE_DIR", tmp_path):
                        with patch("compliance_agent.health_gate.get_active_partition", return_value="A"):
                            with patch("compliance_agent.health_gate.load_config", return_value=config):
                                with patch("compliance_agent.health_gate.run_health_checks", return_value=(False, {"network": {"passed": False}})):
                                    with patch("compliance_agent.health_gate.report_status", return_value=True):
                                        with patch("subprocess.run") as mock_reboot:
                                            result = await run_health_gate()

                                            assert result == 2  # Rollback triggered
                                            # Verify rollback to partition B
                                            content = ab_state.read_text()
                                            assert 'active_partition="B"' in content
                                            # Verify reboot called
                                            mock_reboot.assert_called()

    @pytest.mark.asyncio
    async def test_wrong_partition_clears_state(self, tmp_path):
        """Test that booting on wrong partition clears state."""
        state_file = tmp_path / "update_state.json"
        state = {"update_id": "test", "version": "1.0.42", "target_partition": "B"}
        state_file.write_text(json.dumps(state))

        with patch("compliance_agent.health_gate.UPDATE_STATE_FILE", state_file):
            with patch("compliance_agent.health_gate.get_active_partition", return_value="A"):
                # We're on A but target was B - rollback must have occurred
                result = await run_health_gate()

                assert result == 0
                assert not state_file.exists()


class TestMarkCurrentAsGood:
    """Tests for marking current partition as good."""

    def test_mark_current_as_good(self, tmp_path):
        """Test marking current partition as known-good."""
        ab_state = tmp_path / "ab_state"
        boot_count_file = tmp_path / "boot_count"
        boot_count_file.write_text("2")

        with patch("compliance_agent.health_gate.AB_STATE_FILE", ab_state):
            with patch("compliance_agent.health_gate.BOOT_COUNT_FILE", boot_count_file):
                with patch("compliance_agent.health_gate.get_active_partition", return_value="B"):
                    mark_current_as_good()

                    content = ab_state.read_text()
                    assert 'active_partition="B"' in content
                    assert boot_count_file.read_text() == "0"
