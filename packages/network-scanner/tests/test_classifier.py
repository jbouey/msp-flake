"""Tests for device classification."""

import pytest

from network_scanner.classifier import (
    classify_device,
    discovered_to_device,
    ClassificationResult,
)
from network_scanner._types import (
    Device,
    DeviceType,
    DeviceStatus,
    ScanPolicy,
    ComplianceStatus,
    MEDICAL_DEVICE_PORTS,
)
from network_scanner.discovery import DiscoveredDevice


class TestMedicalDeviceDetection:
    """
    Tests for medical device detection.

    CRITICAL: Medical devices MUST be excluded by default.
    """

    def test_dicom_port_detection(self):
        """DICOM ports should be classified as medical."""
        result = classify_device(
            open_ports=[104],  # DICOM
            hostname="modality-1",
            os_info=None,
        )

        assert result.device_type == DeviceType.MEDICAL
        assert result.is_medical is True
        assert result.confidence >= 0.9
        assert "104" in result.reason

    def test_hl7_port_detection(self):
        """HL7 ports should be classified as medical."""
        result = classify_device(
            open_ports=[2575],  # HL7
            hostname="hl7-interface",
            os_info=None,
        )

        assert result.device_type == DeviceType.MEDICAL
        assert result.is_medical is True

    def test_multiple_medical_ports(self):
        """Multiple medical ports should still classify as medical."""
        result = classify_device(
            open_ports=[104, 2575, 11112],  # DICOM + HL7 + DICOM default
            hostname="imaging-server",
            os_info="Linux",
        )

        assert result.device_type == DeviceType.MEDICAL
        assert result.is_medical is True

    def test_medical_service_detection(self):
        """Medical services in port_services should trigger detection."""
        result = classify_device(
            open_ports=[8042],
            hostname="unknown",
            os_info=None,
            port_services={8042: "DICOM Web Server"},
        )

        assert result.device_type == DeviceType.MEDICAL
        assert result.is_medical is True

    def test_pacs_hostname_detection(self):
        """PACS hostname should trigger medical classification."""
        result = classify_device(
            open_ports=[80, 443],  # Normal web ports
            hostname="pacs-server",
            os_info="Linux",
        )

        assert result.device_type == DeviceType.MEDICAL
        assert result.is_medical is True
        assert "pacs" in result.reason.lower()

    def test_xray_hostname_detection(self):
        """X-ray device hostname should trigger medical classification."""
        result = classify_device(
            open_ports=[22, 80],
            hostname="xray-room1",
            os_info=None,
        )

        assert result.device_type == DeviceType.MEDICAL
        assert result.is_medical is True

    def test_mri_hostname_detection(self):
        """MRI device hostname should trigger medical classification."""
        result = classify_device(
            open_ports=[22],
            hostname="mri-scanner-2",
            os_info=None,
        )

        assert result.device_type == DeviceType.MEDICAL
        assert result.is_medical is True

    @pytest.mark.parametrize("port", list(MEDICAL_DEVICE_PORTS)[:5])
    def test_all_medical_ports_detected(self, port):
        """All defined medical ports should trigger detection."""
        result = classify_device(
            open_ports=[port],
            hostname="device",
            os_info=None,
        )

        assert result.device_type == DeviceType.MEDICAL, f"Port {port} should be medical"
        assert result.is_medical is True


class TestMedicalDeviceExclusion:
    """Tests ensuring medical devices are properly excluded."""

    def test_medical_device_excluded_by_default(self):
        """Medical devices should have excluded scan policy."""
        discovered = DiscoveredDevice(
            ip_address="10.0.1.50",
            hostname="dicom-server",
            open_ports=[104, 11112],
        )

        device = discovered_to_device(discovered)

        assert device.medical_device is True
        assert device.scan_policy == ScanPolicy.EXCLUDED
        assert device.status == DeviceStatus.EXCLUDED
        assert device.compliance_status == ComplianceStatus.EXCLUDED
        assert device.can_be_scanned is False

    def test_medical_device_requires_opt_in(self):
        """Medical devices should require manual opt-in."""
        discovered = DiscoveredDevice(
            ip_address="10.0.1.50",
            hostname="ultrasound-1",
            open_ports=[104],
        )

        device = discovered_to_device(discovered)

        # Should be excluded
        assert device.can_be_scanned is False
        assert device.manually_opted_in is False

        # After opt-in
        device.opt_in_medical_device()
        assert device.can_be_scanned is True
        assert device.manually_opted_in is True
        assert device.scan_policy == ScanPolicy.LIMITED


class TestServerDetection:
    """Tests for server classification."""

    def test_domain_controller_detection(self):
        """Domain controllers should be classified as servers."""
        result = classify_device(
            open_ports=[88, 389, 636, 3268],  # Kerberos, LDAP, LDAPS, GC
            hostname="NVDC01",
            os_info="Windows Server 2022",
        )

        assert result.device_type == DeviceType.SERVER
        assert result.confidence >= 0.9
        assert result.is_medical is False

    def test_web_server_detection(self):
        """Web servers should be classified correctly."""
        result = classify_device(
            open_ports=[22, 80, 443, 8080],
            hostname="web-srv-01",
            os_info="Ubuntu 22.04",
        )

        assert result.device_type == DeviceType.SERVER
        assert result.is_medical is False

    def test_database_server_detection(self):
        """Database servers should be classified correctly."""
        result = classify_device(
            open_ports=[22, 1433, 5432],  # SSH, MSSQL, PostgreSQL
            hostname="db-server",
            os_info="Linux",
        )

        assert result.device_type == DeviceType.SERVER
        assert result.is_medical is False

    def test_windows_server_os_detection(self):
        """Windows Server OS should be detected."""
        result = classify_device(
            open_ports=[3389, 5985],
            hostname="srv-01",
            os_info="Windows Server 2019",
        )

        assert result.device_type == DeviceType.SERVER
        assert result.confidence >= 0.9


class TestWorkstationDetection:
    """Tests for workstation classification."""

    def test_rdp_workstation_detection(self):
        """RDP-only hosts should be workstations."""
        result = classify_device(
            open_ports=[3389],
            hostname="PC-RECEPTION",
            os_info="Windows 10",
        )

        assert result.device_type == DeviceType.WORKSTATION
        assert result.is_medical is False

    def test_windows_10_detection(self):
        """Windows 10 should be classified as workstation."""
        result = classify_device(
            open_ports=[135, 445, 3389],
            hostname="DESKTOP-ABC123",
            os_info="Windows 10 Pro",
        )

        assert result.device_type == DeviceType.WORKSTATION

    def test_windows_11_detection(self):
        """Windows 11 should be classified as workstation."""
        result = classify_device(
            open_ports=[3389],
            hostname="laptop-user1",
            os_info="Windows 11 Enterprise",
        )

        assert result.device_type == DeviceType.WORKSTATION

    def test_macos_detection(self):
        """macOS should be classified as workstation."""
        result = classify_device(
            open_ports=[22, 5900],  # SSH, VNC
            hostname="macbook-pro",
            os_info="macOS Ventura",
        )

        assert result.device_type == DeviceType.WORKSTATION


class TestNetworkDeviceDetection:
    """Tests for network device classification."""

    def test_snmp_device_detection(self):
        """SNMP devices should be classified as network."""
        result = classify_device(
            open_ports=[22, 23, 161],  # SSH, Telnet, SNMP
            hostname="switch-core-01",
            os_info=None,
        )

        assert result.device_type == DeviceType.NETWORK

    def test_cisco_hostname_detection(self):
        """Cisco hostname should be classified as network."""
        result = classify_device(
            open_ports=[22, 161],
            hostname="cisco-2960-sw1",
            os_info=None,
        )

        assert result.device_type == DeviceType.NETWORK

    def test_firewall_detection(self):
        """Firewall devices should be classified as network."""
        result = classify_device(
            open_ports=[22, 443],
            hostname="fw-edge-01",
            os_info=None,
        )

        assert result.device_type == DeviceType.NETWORK


class TestPrinterDetection:
    """Tests for printer classification."""

    def test_jetdirect_port_detection(self):
        """JetDirect port should classify as printer."""
        result = classify_device(
            open_ports=[9100],
            hostname="HP-LaserJet",
            os_info=None,
        )

        assert result.device_type == DeviceType.PRINTER

    def test_ipp_port_detection(self):
        """IPP port should classify as printer."""
        result = classify_device(
            open_ports=[631],
            hostname="printer-001",
            os_info=None,
        )

        assert result.device_type == DeviceType.PRINTER

    def test_printer_hostname_detection(self):
        """Printer hostname patterns should be detected."""
        result = classify_device(
            open_ports=[80, 443],
            hostname="xerox-mfp-reception",
            os_info=None,
        )

        assert result.device_type == DeviceType.PRINTER


class TestUnknownDevices:
    """Tests for unknown device classification."""

    def test_minimal_info_unknown(self):
        """Devices with minimal info should be unknown."""
        result = classify_device(
            open_ports=[],
            hostname=None,
            os_info=None,
        )

        assert result.device_type == DeviceType.UNKNOWN
        assert result.confidence < 0.5

    def test_generic_ports_unknown(self):
        """Generic ports without clear indicators should be unknown."""
        result = classify_device(
            open_ports=[8000, 8888],
            hostname="device-1234",
            os_info=None,
        )

        assert result.device_type == DeviceType.UNKNOWN


class TestDiscoveredToDevice:
    """Tests for discovered_to_device conversion."""

    def test_non_medical_device_conversion(self):
        """Non-medical devices should be converted correctly."""
        discovered = DiscoveredDevice(
            ip_address="192.168.1.100",
            hostname="workstation-01",
            mac_address="aa:bb:cc:dd:ee:ff",
            os_name="Windows 10",
            open_ports=[3389],
        )

        device = discovered_to_device(discovered)

        assert device.ip_address == "192.168.1.100"
        assert device.hostname == "workstation-01"
        assert device.device_type == DeviceType.WORKSTATION
        assert device.medical_device is False
        assert device.scan_policy == ScanPolicy.STANDARD
        assert device.can_be_scanned is True

    def test_medical_device_conversion(self):
        """Medical devices should be properly excluded."""
        discovered = DiscoveredDevice(
            ip_address="10.0.1.50",
            hostname="ct-scanner",
            open_ports=[104],
        )

        device = discovered_to_device(discovered)

        assert device.device_type == DeviceType.MEDICAL
        assert device.medical_device is True
        assert device.scan_policy == ScanPolicy.EXCLUDED
        assert device.status == DeviceStatus.EXCLUDED
        assert device.can_be_scanned is False


class TestClassificationPriority:
    """Tests ensuring medical detection takes priority."""

    def test_medical_over_server(self):
        """Medical classification should take priority over server."""
        # Device with both server ports and medical ports
        result = classify_device(
            open_ports=[22, 80, 443, 104],  # Server ports + DICOM
            hostname="imaging-server",
            os_info="Linux",
        )

        # Should be classified as medical, not server
        assert result.device_type == DeviceType.MEDICAL
        assert result.is_medical is True

    def test_medical_over_workstation(self):
        """Medical classification should take priority over workstation."""
        result = classify_device(
            open_ports=[3389, 104],  # RDP + DICOM
            hostname="portable-imaging",
            os_info="Windows 10",
        )

        assert result.device_type == DeviceType.MEDICAL
        assert result.is_medical is True
