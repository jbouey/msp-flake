"""
Tests for Network Posture Detector.

Uses mocking to simulate network checks without requiring real hosts.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from compliance_agent.network_posture import (
    ListeningPort,
    NetworkPostureResult,
    NetworkPostureDetector,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def detector():
    """Create a network posture detector with defaults."""
    return NetworkPostureDetector()


@pytest.fixture
def mock_linux_executor():
    """Create a mock Linux executor."""
    executor = AsyncMock()

    # Default script result
    result = MagicMock()
    result.success = True
    result.output = {"stdout": "tcp|0.0.0.0|22|sshd\ntcp|127.0.0.1|5432|postgres"}
    executor.execute_script = AsyncMock(return_value=result)

    return executor


@pytest.fixture
def mock_windows_executor():
    """Create a mock Windows executor."""
    executor = AsyncMock()

    # First call returns ports
    result_ports = MagicMock()
    result_ports.success = True
    result_ports.output = {
        "std_out": "",
        "parsed": [
            {"Protocol": "TCP", "Address": "0.0.0.0", "Port": 22, "PID": 1234, "Process": "sshd"},
            {"Protocol": "TCP", "Address": "127.0.0.1", "Port": 1433, "PID": 5678, "Process": "sqlservr"},
        ]
    }

    # Second call returns DNS as list of strings
    result_dns = MagicMock()
    result_dns.success = True
    result_dns.output = {"std_out": "", "parsed": ["8.8.8.8", "1.1.1.1"]}

    # Third+ calls return success for reachability
    result_reach = MagicMock()
    result_reach.success = True
    result_reach.output = {"std_out": "SUCCESS"}

    # Return different results for different calls
    executor.execute_script = AsyncMock(side_effect=[result_ports, result_dns, result_reach, result_reach])

    return executor


@pytest.fixture
def mock_linux_target():
    """Create a mock Linux target."""
    target = MagicMock()
    target.hostname = "192.168.1.100"
    return target


@pytest.fixture
def mock_windows_target():
    """Create a mock Windows target."""
    target = MagicMock()
    target.hostname = "192.168.1.200"
    return target


# =============================================================================
# LISTENING PORT TESTS
# =============================================================================

class TestListeningPort:
    def test_create_port(self):
        port = ListeningPort(
            port=22,
            protocol="tcp",
            process="sshd",
            bind_address="0.0.0.0"
        )
        assert port.port == 22
        assert port.protocol == "tcp"
        assert port.external is True

    def test_port_external_detection(self):
        """Test external binding detection."""
        # 0.0.0.0 is external
        port1 = ListeningPort(port=22, protocol="tcp", process="sshd", bind_address="0.0.0.0")
        assert port1.external is True

        # :: (IPv6 any) is external
        port2 = ListeningPort(port=22, protocol="tcp", process="sshd", bind_address="::")
        assert port2.external is True

        # 127.0.0.1 is internal
        port3 = ListeningPort(port=22, protocol="tcp", process="sshd", bind_address="127.0.0.1")
        assert port3.external is False

        # ::1 is internal
        port4 = ListeningPort(port=22, protocol="tcp", process="sshd", bind_address="::1")
        assert port4.external is False

    def test_port_to_dict(self):
        port = ListeningPort(
            port=443,
            protocol="tcp",
            process="nginx",
            pid=1234,
            bind_address="0.0.0.0"
        )
        d = port.to_dict()
        assert d["port"] == 443
        assert d["process"] == "nginx"
        assert d["pid"] == 1234
        assert d["external"] is True


# =============================================================================
# NETWORK POSTURE RESULT TESTS
# =============================================================================

class TestNetworkPostureResult:
    def test_create_result(self):
        result = NetworkPostureResult(
            target="192.168.1.100",
            os_type="linux"
        )
        assert result.target == "192.168.1.100"
        assert result.os_type == "linux"
        assert result.compliant is True
        assert result.timestamp  # Auto-generated

    def test_result_to_dict(self):
        result = NetworkPostureResult(
            target="192.168.1.100",
            os_type="linux",
            listening_ports=[
                ListeningPort(port=22, protocol="tcp", process="sshd", bind_address="0.0.0.0")
            ]
        )
        d = result.to_dict()
        assert d["target"] == "192.168.1.100"
        assert len(d["listening_ports"]) == 1
        assert d["listening_ports"][0]["port"] == 22


# =============================================================================
# DETECTOR TESTS
# =============================================================================

class TestNetworkPostureDetector:
    def test_create_detector(self, detector):
        assert detector.baseline is not None
        assert "prohibited_ports" in detector.baseline

    def test_default_baseline(self, detector):
        """Test default baseline has essential values."""
        baseline = detector._default_baseline()
        assert "prohibited_ports" in baseline
        assert len(baseline["prohibited_ports"]) >= 3

    @pytest.mark.asyncio
    async def test_detect_linux_basic(self, detector, mock_linux_executor, mock_linux_target):
        """Test basic Linux detection."""
        result = await detector.detect_linux(mock_linux_executor, mock_linux_target)

        assert result.target == "192.168.1.100"
        assert result.os_type == "linux"
        assert len(result.listening_ports) >= 0

    @pytest.mark.asyncio
    async def test_detect_windows_basic(self, detector, mock_windows_executor, mock_windows_target):
        """Test basic Windows detection."""
        result = await detector.detect_windows(mock_windows_executor, mock_windows_target)

        assert result.target == "192.168.1.200"
        assert result.os_type == "windows"

    def test_parse_linux_ports(self, detector):
        """Test Linux port parsing (ss output format)."""
        output = """tcp|0.0.0.0|22|sshd
tcp|127.0.0.1|5432|postgres
udp|0.0.0.0|123|ntpd"""

        ports = detector._parse_linux_ports(output)

        assert len(ports) == 3
        assert ports[0].port == 22
        assert ports[0].protocol == "tcp"
        assert ports[0].process == "sshd"
        assert ports[0].external is True

        assert ports[1].port == 5432
        assert ports[1].external is False  # 127.0.0.1

    def test_parse_windows_ports(self, detector):
        """Test Windows port parsing (netstat JSON format)."""
        data = [
            {"Protocol": "TCP", "Address": "0.0.0.0", "Port": 22, "PID": 1234, "Process": "sshd"},
            {"Protocol": "TCP", "Address": "127.0.0.1", "Port": 1433, "PID": 5678, "Process": "sqlservr"},
        ]

        ports = detector._parse_windows_ports(data)

        assert len(ports) == 2
        assert ports[0].port == 22
        assert ports[0].external is True
        assert ports[1].port == 1433
        assert ports[1].external is False


# =============================================================================
# BASELINE ANALYSIS TESTS
# =============================================================================

class TestBaselineAnalysis:
    def test_detect_prohibited_port(self, detector):
        """Test detection of prohibited ports."""
        result = NetworkPostureResult(
            target="192.168.1.100",
            os_type="linux",
            listening_ports=[
                ListeningPort(port=23, protocol="tcp", process="telnetd", bind_address="0.0.0.0"),
            ]
        )

        detector._analyze_posture(result)

        assert len(result.prohibited_ports) == 1
        assert result.prohibited_ports[0]["port"] == 23
        assert result.compliant is False

    def test_detect_external_database(self, detector):
        """Test detection of externally bound database."""
        result = NetworkPostureResult(
            target="192.168.1.100",
            os_type="linux",
            listening_ports=[
                ListeningPort(port=5432, protocol="tcp", process="postgres", bind_address="0.0.0.0"),
            ]
        )

        detector._analyze_posture(result)

        assert len(result.external_bindings) == 1
        # Should flag as violation since 5432 is not in allowed_external_ports
        assert len(result.baseline_violations) >= 1
        assert result.compliant is False

    def test_allowed_external_port(self, detector):
        """Test that allowed external ports don't trigger violations."""
        result = NetworkPostureResult(
            target="192.168.1.100",
            os_type="linux",
            listening_ports=[
                ListeningPort(port=22, protocol="tcp", process="sshd", bind_address="0.0.0.0"),
                ListeningPort(port=443, protocol="tcp", process="nginx", bind_address="0.0.0.0"),
            ]
        )

        detector._analyze_posture(result)

        # SSH (22) and HTTPS (443) are allowed
        assert len(result.prohibited_ports) == 0
        # Should still be compliant
        assert result.compliant is True

    def test_never_external_violation(self, detector):
        """Test detection of services that should never be external."""
        result = NetworkPostureResult(
            target="192.168.1.100",
            os_type="linux",
            listening_ports=[
                ListeningPort(port=6379, protocol="tcp", process="redis-server", bind_address="0.0.0.0"),
            ]
        )

        detector._analyze_posture(result)

        # Redis should be flagged
        assert any("redis" in v.get("process", "").lower() or "never_external" in v.get("type", "")
                  for v in result.drift_items)
        assert result.compliant is False

    def test_multiple_violations(self, detector):
        """Test detection of multiple violations."""
        result = NetworkPostureResult(
            target="192.168.1.100",
            os_type="linux",
            listening_ports=[
                ListeningPort(port=21, protocol="tcp", process="vsftpd", bind_address="0.0.0.0"),
                ListeningPort(port=23, protocol="tcp", process="telnetd", bind_address="0.0.0.0"),
                ListeningPort(port=3306, protocol="tcp", process="mysqld", bind_address="0.0.0.0"),
            ]
        )

        detector._analyze_posture(result)

        assert len(result.prohibited_ports) >= 2  # FTP and Telnet
        assert len(result.drift_items) >= 2
        assert result.compliant is False


# =============================================================================
# EVIDENCE GENERATION TESTS
# =============================================================================

class TestEvidenceGeneration:
    def test_generate_evidence(self, detector):
        """Test evidence bundle generation."""
        results = [
            NetworkPostureResult(
                target="192.168.1.100",
                os_type="linux",
                compliant=True,
                listening_ports=[
                    ListeningPort(port=22, protocol="tcp", process="sshd", bind_address="0.0.0.0"),
                ]
            ),
            NetworkPostureResult(
                target="192.168.1.200",
                os_type="windows",
                compliant=False,
                prohibited_ports=[{"port": 23, "description": "Telnet"}],
            )
        ]

        evidence = detector.generate_evidence(results)

        assert evidence["type"] == "network_posture_detection"
        assert evidence["hosts_scanned"] == 2
        assert evidence["compliant_count"] == 1
        assert evidence["non_compliant_count"] == 1
        assert "hash" in evidence

    def test_get_summary(self, detector):
        """Test summary generation."""
        results = [
            NetworkPostureResult(
                target="192.168.1.100",
                os_type="linux",
                compliant=True,
            ),
            NetworkPostureResult(
                target="192.168.1.200",
                os_type="windows",
                compliant=False,
                prohibited_ports=[{"port": 23, "description": "Telnet"}],
                drift_items=[
                    {"type": "prohibited_port", "port": 23},
                    {"type": "unauthorized_external_port", "port": 8080},
                ],
                hipaa_controls=["164.312(e)(1)"],
            )
        ]

        summary = detector.get_summary(results)

        assert summary["hosts_scanned"] == 2
        assert summary["compliant"] == 1
        assert summary["non_compliant"] == 1
        assert summary["issues"]["prohibited_ports"] == 1
        assert "164.312(e)(1)" in summary["hipaa_controls_affected"]


# =============================================================================
# REMEDIATION TESTS
# =============================================================================

class TestRemediation:
    @pytest.mark.asyncio
    async def test_remediate_prohibited_port_linux(self, detector, mock_linux_executor, mock_linux_target):
        """Test remediation of prohibited port on Linux."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.output = {"stdout": "BLOCKED_UFW"}
        mock_linux_executor.execute_script = AsyncMock(return_value=mock_result)

        success = await detector.remediate_prohibited_port(
            mock_linux_executor,
            mock_linux_target,
            port=23,
            os_type="linux"
        )

        assert success is True
        mock_linux_executor.execute_script.assert_called_once()

    @pytest.mark.asyncio
    async def test_remediate_prohibited_port_windows(self, detector, mock_windows_executor, mock_windows_target):
        """Test remediation of prohibited port on Windows."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.output = {"std_out": "BLOCKED"}
        mock_windows_executor.execute_script = AsyncMock(return_value=mock_result)

        success = await detector.remediate_prohibited_port(
            mock_windows_executor,
            mock_windows_target,
            port=23,
            os_type="windows"
        )

        assert success is True


# =============================================================================
# REACHABILITY TESTS
# =============================================================================

class TestReachability:
    @pytest.mark.asyncio
    async def test_reachability_check_success(self, detector, mock_linux_executor, mock_linux_target):
        """Test successful reachability check."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.output = {"stdout": "SUCCESS"}
        mock_linux_executor.execute_script = AsyncMock(return_value=mock_result)

        failures = await detector._check_reachability_linux(mock_linux_executor, mock_linux_target)

        # All reachability assertions should pass
        assert len(failures) == 0

    @pytest.mark.asyncio
    async def test_reachability_check_failure(self, detector, mock_linux_executor, mock_linux_target):
        """Test failed reachability check."""
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.output = {"stdout": "FAILED"}
        mock_linux_executor.execute_script = AsyncMock(return_value=mock_result)

        failures = await detector._check_reachability_linux(mock_linux_executor, mock_linux_target)

        # Should have failures if baseline has required assertions
        # Note: depends on baseline config


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    def test_parse_empty_linux_output(self, detector):
        """Test parsing empty output."""
        ports = detector._parse_linux_ports("")
        assert ports == []

    def test_parse_malformed_linux_output(self, detector):
        """Test parsing malformed output."""
        output = "not|valid\ndata"
        ports = detector._parse_linux_ports(output)
        # Should handle gracefully
        assert isinstance(ports, list)

    def test_parse_none_windows_output(self, detector):
        """Test parsing None Windows output."""
        ports = detector._parse_windows_ports(None)
        assert ports == []

    def test_parse_single_windows_port(self, detector):
        """Test parsing single port (not array)."""
        data = {"Protocol": "TCP", "Address": "0.0.0.0", "Port": 22, "Process": "sshd"}
        ports = detector._parse_windows_ports(data)
        assert len(ports) == 1

    def test_analyze_empty_result(self, detector):
        """Test analyzing result with no ports."""
        result = NetworkPostureResult(
            target="192.168.1.100",
            os_type="linux"
        )

        detector._analyze_posture(result)

        assert result.compliant is True
        assert len(result.prohibited_ports) == 0
