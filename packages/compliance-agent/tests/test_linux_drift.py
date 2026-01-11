"""
Tests for Linux Drift Detector.

Uses mocking to simulate drift detection without requiring real Linux hosts.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from compliance_agent.linux_drift import (
    DriftResult,
    RemediationResult,
    LinuxDriftDetector,
)
from compliance_agent.runbooks.linux.executor import LinuxTarget, LinuxExecutor


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def linux_target():
    """Create a test Linux target."""
    return LinuxTarget(
        hostname="192.168.1.100",
        port=22,
        username="testuser",
        password="testpass",
    )


@pytest.fixture
def mock_executor():
    """Create a mock Linux executor."""
    executor = MagicMock(spec=LinuxExecutor)
    executor.targets = {}
    executor.add_target = MagicMock()
    executor.remove_target = MagicMock()
    executor.close_all = AsyncMock()
    executor._distro_cache = {}
    return executor


@pytest.fixture
def detector(linux_target, mock_executor):
    """Create a drift detector with mock executor."""
    detector = LinuxDriftDetector(
        targets=[linux_target],
        executor=mock_executor
    )
    return detector


# =============================================================================
# DRIFT RESULT TESTS
# =============================================================================

class TestDriftResult:
    def test_create_drift_result(self):
        result = DriftResult(
            target="192.168.1.100",
            runbook_id="LIN-SSH-001",
            check_type="ssh_config",
            severity="high",
            compliant=False,
            drift_description="PermitRootLogin=yes",
            raw_output="DRIFT:PermitRootLogin=yes",
            hipaa_controls=["164.312(a)(1)"],
        )
        assert result.target == "192.168.1.100"
        assert result.compliant is False
        assert result.timestamp  # Auto-generated

    def test_compliant_result(self):
        result = DriftResult(
            target="192.168.1.100",
            runbook_id="LIN-SSH-001",
            check_type="ssh_config",
            severity="high",
            compliant=True,
            drift_description="",
            raw_output="COMPLIANT",
            hipaa_controls=["164.312(a)(1)"],
        )
        assert result.compliant is True

    def test_drift_result_to_dict(self):
        result = DriftResult(
            target="192.168.1.100",
            runbook_id="LIN-SSH-001",
            check_type="ssh_config",
            severity="high",
            compliant=False,
            drift_description="PermitRootLogin=yes",
            raw_output="DRIFT:PermitRootLogin=yes",
            hipaa_controls=["164.312(a)(1)"],
            l1_eligible=True,
        )
        d = result.to_dict()
        assert d["target"] == "192.168.1.100"
        assert d["runbook_id"] == "LIN-SSH-001"
        assert d["l1_eligible"] is True


# =============================================================================
# REMEDIATION RESULT TESTS
# =============================================================================

class TestRemediationResult:
    def test_create_remediation_result(self):
        result = RemediationResult(
            target="192.168.1.100",
            runbook_id="LIN-SSH-001",
            success=True,
            phases_completed=["detect", "remediate", "verify"],
            duration_seconds=5.0,
        )
        assert result.success is True
        assert len(result.phases_completed) == 3

    def test_failed_remediation(self):
        result = RemediationResult(
            target="192.168.1.100",
            runbook_id="LIN-SSH-001",
            success=False,
            phases_completed=["detect"],
            error="Permission denied",
        )
        assert result.success is False
        assert result.error == "Permission denied"


# =============================================================================
# DETECTOR TESTS
# =============================================================================

class TestLinuxDriftDetector:
    def test_create_detector(self, detector):
        assert len(detector.targets) == 1
        assert detector.baseline is not None

    def test_add_target(self, detector):
        new_target = LinuxTarget(hostname="192.168.1.101", username="admin", password="pass")
        detector.add_target(new_target)
        assert len(detector.targets) == 2

    def test_remove_target(self, detector, linux_target):
        detector.remove_target(linux_target.hostname)
        assert len(detector.targets) == 0

    def test_default_baseline(self, detector):
        """Test default baseline has essential categories."""
        baseline = detector._default_baseline()
        assert "ssh_config" in baseline
        assert "firewall" in baseline

    def test_get_enabled_checks(self, detector):
        """Test getting enabled check types from baseline."""
        enabled = detector._get_enabled_checks()
        assert "ssh_config" in enabled
        assert "firewall" in enabled

    @pytest.mark.asyncio
    async def test_detect_host_success(self, detector, linux_target, mock_executor):
        """Test successful host detection."""
        # Setup mock
        mock_executor.detect_distro = AsyncMock(return_value="ubuntu")

        mock_exec_result = MagicMock()
        mock_exec_result.success = True
        mock_exec_result.output = {"stdout": "COMPLIANT:PermitRootLogin=no"}

        mock_executor.run_runbook = AsyncMock(return_value=[mock_exec_result])

        results = await detector.detect_host(linux_target)

        assert len(results) > 0
        mock_executor.detect_distro.assert_called_once()

    @pytest.mark.asyncio
    async def test_detect_host_drift(self, detector, linux_target, mock_executor):
        """Test detection of drift."""
        mock_executor.detect_distro = AsyncMock(return_value="ubuntu")

        mock_exec_result = MagicMock()
        mock_exec_result.success = False
        mock_exec_result.output = {"stdout": "DRIFT:PermitRootLogin=yes"}

        mock_executor.run_runbook = AsyncMock(return_value=[mock_exec_result])

        results = await detector.detect_host(linux_target)

        # Should have at least one drift result
        assert len(results) > 0
        # First result should show drift
        assert results[0].compliant is False

    @pytest.mark.asyncio
    async def test_detect_all(self, detector, linux_target, mock_executor):
        """Test detection across all targets."""
        mock_executor.detect_distro = AsyncMock(return_value="ubuntu")
        mock_exec_result = MagicMock()
        mock_exec_result.success = True
        mock_exec_result.output = {"stdout": "COMPLIANT"}
        mock_executor.run_runbook = AsyncMock(return_value=[mock_exec_result])

        results = await detector.detect_all()

        assert len(results) > 0
        # Results should be stored in history
        assert len(detector._detection_history) > 0

    @pytest.mark.asyncio
    async def test_detect_category(self, detector, linux_target, mock_executor):
        """Test detection for specific category."""
        mock_executor.detect_distro = AsyncMock(return_value="ubuntu")
        mock_exec_result = MagicMock()
        mock_exec_result.success = True
        mock_exec_result.output = {"stdout": "COMPLIANT"}
        mock_executor.run_runbook = AsyncMock(return_value=[mock_exec_result])

        results = await detector.detect_category(linux_target, "ssh_config")

        # Should only have ssh_config results
        for r in results:
            assert r.check_type == "ssh_config"


# =============================================================================
# REMEDIATION TESTS
# =============================================================================

class TestRemediation:
    @pytest.mark.asyncio
    async def test_remediate_drift(self, detector, linux_target, mock_executor):
        """Test remediation of a drift."""
        drift = DriftResult(
            target=linux_target.hostname,
            runbook_id="LIN-SSH-001",
            check_type="ssh_config",
            severity="high",
            compliant=False,
            drift_description="PermitRootLogin=yes",
            raw_output="DRIFT:PermitRootLogin=yes",
            hipaa_controls=["164.312(a)(1)"],
            l1_eligible=True,
        )

        # Mock successful remediation
        mock_results = [
            MagicMock(phase="detect", success=True),
            MagicMock(phase="remediate", success=True),
            MagicMock(phase="verify", success=True),
        ]
        mock_executor.run_runbook = AsyncMock(return_value=mock_results)

        result = await detector.remediate(drift)

        assert result.success is True
        assert "verify" in result.phases_completed

    @pytest.mark.asyncio
    async def test_remediate_target_not_found(self, detector):
        """Test remediation when target not found."""
        drift = DriftResult(
            target="nonexistent-host",
            runbook_id="LIN-SSH-001",
            check_type="ssh_config",
            severity="high",
            compliant=False,
            drift_description="test",
            raw_output="test",
            hipaa_controls=[],
        )

        result = await detector.remediate(drift)

        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_remediate_all_l1(self, detector, linux_target, mock_executor):
        """Test auto-remediation of L1 drifts."""
        # Add some L1-eligible drifts to history
        detector._detection_history = [
            DriftResult(
                target=linux_target.hostname,
                runbook_id="LIN-SSH-001",
                check_type="ssh_config",
                severity="high",
                compliant=False,
                drift_description="test",
                raw_output="test",
                hipaa_controls=[],
                l1_eligible=True,
            ),
            DriftResult(
                target=linux_target.hostname,
                runbook_id="LIN-SSH-002",
                check_type="ssh_config",
                severity="high",
                compliant=True,  # Compliant - should skip
                drift_description="",
                raw_output="COMPLIANT",
                hipaa_controls=[],
                l1_eligible=True,
            ),
        ]

        mock_results = [MagicMock(phase="verify", success=True)]
        mock_executor.run_runbook = AsyncMock(return_value=mock_results)

        results = await detector.remediate_all_l1()

        # Should only remediate the non-compliant L1 drift
        assert len(results) == 1


# =============================================================================
# EVIDENCE GENERATION TESTS
# =============================================================================

class TestEvidenceGeneration:
    @pytest.mark.asyncio
    async def test_generate_evidence(self, detector, linux_target):
        """Test evidence bundle generation."""
        results = [
            DriftResult(
                target=linux_target.hostname,
                runbook_id="LIN-SSH-001",
                check_type="ssh_config",
                severity="high",
                compliant=True,
                drift_description="",
                raw_output="COMPLIANT",
                hipaa_controls=["164.312(a)(1)"],
                distro="ubuntu",
            ),
            DriftResult(
                target=linux_target.hostname,
                runbook_id="LIN-SSH-002",
                check_type="ssh_config",
                severity="high",
                compliant=False,
                drift_description="PasswordAuthentication=yes",
                raw_output="DRIFT:PasswordAuthentication=yes",
                hipaa_controls=["164.312(a)(1)"],
                distro="ubuntu",
            ),
        ]

        evidence = await detector.generate_evidence(results)

        assert evidence["type"] == "linux_drift_detection"
        assert evidence["total_checks"] == 2
        assert evidence["compliant_count"] == 1
        assert evidence["drift_count"] == 1
        assert "hash" in evidence

    def test_get_drift_summary(self, detector, linux_target):
        """Test drift summary generation."""
        results = [
            DriftResult(
                target=linux_target.hostname,
                runbook_id="LIN-SSH-001",
                check_type="ssh_config",
                severity="high",
                compliant=False,
                drift_description="test",
                raw_output="test",
                hipaa_controls=[],
                l1_eligible=True,
            ),
            DriftResult(
                target=linux_target.hostname,
                runbook_id="LIN-PATCH-001",
                check_type="patching",
                severity="high",
                compliant=False,
                drift_description="test",
                raw_output="test",
                hipaa_controls=[],
                l1_eligible=False,
                l2_eligible=True,
            ),
        ]

        summary = detector.get_drift_summary(results)

        assert summary["total_checks"] == 2
        assert summary["drifted"] == 2
        assert summary["l1_actionable"] == 1
        assert summary["l2_actionable"] == 1
        assert summary["by_severity"]["high"] == 2


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_detect_host_error(self, detector, linux_target, mock_executor):
        """Test handling of detection errors."""
        # Patch the executor's detect_distro to raise an exception
        async def raise_error(target):
            raise Exception("Connection refused")

        mock_executor.detect_distro = raise_error

        results = await detector.detect_host(linux_target)

        # Should return error results
        assert len(results) >= 1
        # At least one should be non-compliant due to the error
        non_compliant = [r for r in results if not r.compliant]
        assert len(non_compliant) >= 1

    @pytest.mark.asyncio
    async def test_detect_all_with_failing_host(self, detector, mock_executor):
        """Test detect_all when one host fails."""
        # Add second target
        target2 = LinuxTarget(hostname="192.168.1.102", username="test", password="test")
        detector.targets.append(target2)

        call_count = 0

        async def mock_detect_distro(target):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Host unreachable")
            return "ubuntu"

        mock_executor.detect_distro = mock_detect_distro
        mock_exec_result = MagicMock()
        mock_exec_result.success = True
        mock_exec_result.output = {"stdout": "COMPLIANT"}
        mock_executor.run_runbook = AsyncMock(return_value=[mock_exec_result])

        results = await detector.detect_all()

        # Should have results from both hosts (one error, one success)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_close(self, detector, mock_executor):
        """Test closing the detector."""
        await detector.close()
        mock_executor.close_all.assert_called_once()


# =============================================================================
# BASELINE TESTS
# =============================================================================

class TestBaseline:
    def test_load_missing_baseline(self):
        """Test loading non-existent baseline file."""
        detector = LinuxDriftDetector(
            baseline_path="/nonexistent/path/baseline.yaml"
        )
        # Should fall back to defaults
        assert detector.baseline is not None
        assert "ssh_config" in detector.baseline

    def test_group_by_category(self, detector, linux_target):
        """Test grouping results by category."""
        results = [
            DriftResult(
                target=linux_target.hostname,
                runbook_id="LIN-SSH-001",
                check_type="ssh_config",
                severity="high",
                compliant=True,
                drift_description="",
                raw_output="COMPLIANT",
                hipaa_controls=[],
            ),
            DriftResult(
                target=linux_target.hostname,
                runbook_id="LIN-FW-001",
                check_type="firewall",
                severity="critical",
                compliant=False,
                drift_description="no firewall",
                raw_output="DRIFT",
                hipaa_controls=[],
            ),
        ]

        categories = detector._group_by_category(results)

        assert "ssh_config" in categories
        assert categories["ssh_config"]["compliant"] == 1
        assert "firewall" in categories
        assert categories["firewall"]["drifted"] == 1
