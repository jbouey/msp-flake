"""Tests for network scanner type definitions."""

import pytest
from datetime import datetime, timezone

from network_scanner._types import (
    Device,
    DeviceType,
    DevicePort,
    ScanPolicy,
    DeviceStatus,
    ComplianceStatus,
    DiscoverySource,
    MEDICAL_DEVICE_PORTS,
    MEDICAL_HOSTNAME_PATTERNS,
)


class TestDevice:
    """Tests for Device dataclass."""

    def test_device_defaults(self):
        """Device should have sensible defaults."""
        device = Device(ip_address="192.168.1.100")

        assert device.ip_address == "192.168.1.100"
        assert device.device_type == DeviceType.UNKNOWN
        assert device.scan_policy == ScanPolicy.STANDARD
        assert device.medical_device is False
        assert device.manually_opted_in is False
        assert device.status == DeviceStatus.DISCOVERED
        assert device.can_be_scanned is True

    def test_mark_as_medical(self):
        """Marking device as medical should exclude it from scanning."""
        device = Device(ip_address="192.168.1.100")
        device.mark_as_medical()

        assert device.medical_device is True
        assert device.device_type == DeviceType.MEDICAL
        assert device.scan_policy == ScanPolicy.EXCLUDED
        assert device.status == DeviceStatus.EXCLUDED
        assert device.compliance_status == ComplianceStatus.EXCLUDED
        assert device.can_be_scanned is False

    def test_medical_device_cannot_be_scanned_without_opt_in(self):
        """Medical devices should require explicit opt-in."""
        device = Device(ip_address="192.168.1.100")
        device.mark_as_medical()

        assert device.can_be_scanned is False

    def test_medical_device_opt_in(self):
        """Medical devices can be opted in for limited scanning."""
        device = Device(ip_address="192.168.1.100")
        device.mark_as_medical()

        result = device.opt_in_medical_device(reason="Testing")

        assert result is True
        assert device.manually_opted_in is True
        assert device.scan_policy == ScanPolicy.LIMITED
        assert device.status == DeviceStatus.MONITORED
        assert device.can_be_scanned is True

    def test_non_medical_device_opt_in_fails(self):
        """Opt-in should fail for non-medical devices."""
        device = Device(ip_address="192.168.1.100")

        result = device.opt_in_medical_device()

        assert result is False
        assert device.manually_opted_in is False

    def test_excluded_device_cannot_be_scanned(self):
        """Excluded devices cannot be scanned."""
        device = Device(
            ip_address="192.168.1.100",
            scan_policy=ScanPolicy.EXCLUDED,
        )

        assert device.can_be_scanned is False


class TestDevicePort:
    """Tests for DevicePort dataclass."""

    def test_port_defaults(self):
        """DevicePort should have sensible defaults."""
        port = DevicePort(device_id="test-id", port=443)

        assert port.port == 443
        assert port.protocol == "tcp"
        assert port.state == "open"
        assert port.service_name is None


class TestMedicalDeviceDetection:
    """Tests for medical device detection constants."""

    def test_dicom_ports_are_medical(self):
        """DICOM ports should be in medical device ports."""
        assert 104 in MEDICAL_DEVICE_PORTS  # DICOM
        assert 11112 in MEDICAL_DEVICE_PORTS  # DICOM default

    def test_hl7_ports_are_medical(self):
        """HL7 ports should be in medical device ports."""
        assert 2575 in MEDICAL_DEVICE_PORTS

    def test_hostname_patterns_include_common_medical(self):
        """Hostname patterns should include common medical devices."""
        assert "pacs" in MEDICAL_HOSTNAME_PATTERNS
        assert "dicom" in MEDICAL_HOSTNAME_PATTERNS
        assert "xray" in MEDICAL_HOSTNAME_PATTERNS
        assert "mri-" in MEDICAL_HOSTNAME_PATTERNS


class TestEnums:
    """Tests for enum types."""

    def test_device_type_values(self):
        """DeviceType should have expected values."""
        assert DeviceType.WORKSTATION.value == "workstation"
        assert DeviceType.SERVER.value == "server"
        assert DeviceType.NETWORK.value == "network"
        assert DeviceType.PRINTER.value == "printer"
        assert DeviceType.MEDICAL.value == "medical"
        assert DeviceType.UNKNOWN.value == "unknown"

    def test_scan_policy_values(self):
        """ScanPolicy should have expected values."""
        assert ScanPolicy.STANDARD.value == "standard"
        assert ScanPolicy.LIMITED.value == "limited"
        assert ScanPolicy.EXCLUDED.value == "excluded"

    def test_discovery_source_values(self):
        """DiscoverySource should have expected values."""
        assert DiscoverySource.AD.value == "ad"
        assert DiscoverySource.ARP.value == "arp"
        assert DiscoverySource.NMAP.value == "nmap"
        assert DiscoverySource.GO_AGENT.value == "go_agent"
        assert DiscoverySource.MANUAL.value == "manual"
