"""
Device classification based on ports, hostname, and OS.

CRITICAL: Medical devices are EXCLUDED by default and require
manual opt-in before any scanning can occur.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from ._types import (
    Device,
    DeviceType,
    ScanPolicy,
    DeviceStatus,
    ComplianceStatus,
    MEDICAL_DEVICE_PORTS,
    MEDICAL_HOSTNAME_PATTERNS,
)
from .discovery import DiscoveredDevice

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    """Result of device classification."""
    device_type: DeviceType
    confidence: float  # 0.0 to 1.0
    reason: str
    is_medical: bool = False


def classify_device(
    open_ports: list[int],
    hostname: Optional[str],
    os_info: Optional[str],
    port_services: Optional[dict[int, str]] = None,
) -> ClassificationResult:
    """
    Classify a device based on network characteristics.

    IMPORTANT: Medical devices are detected and flagged for EXCLUSION.
    They require explicit manual opt-in before any scanning.

    Args:
        open_ports: List of open port numbers
        hostname: Device hostname (if known)
        os_info: OS name/version string
        port_services: Dict mapping port numbers to service names

    Returns:
        ClassificationResult with device type and confidence
    """
    port_set = set(open_ports)
    hostname_lower = (hostname or "").lower()
    os_lower = (os_info or "").lower()
    port_services = port_services or {}

    # =========================================================================
    # MEDICAL DEVICE DETECTION - HIGHEST PRIORITY
    # Medical devices are EXCLUDED by default for patient safety
    # =========================================================================

    medical_result = _detect_medical_device(
        port_set, hostname_lower, port_services
    )
    if medical_result:
        return medical_result

    # =========================================================================
    # Standard device classification (non-medical)
    # =========================================================================

    # Domain Controller detection
    dc_result = _detect_domain_controller(port_set, hostname_lower)
    if dc_result:
        return dc_result

    # Server detection
    server_result = _detect_server(port_set, hostname_lower, os_lower)
    if server_result:
        return server_result

    # Network device detection
    network_result = _detect_network_device(port_set, hostname_lower, port_services)
    if network_result:
        return network_result

    # Printer detection
    printer_result = _detect_printer(port_set, hostname_lower, port_services)
    if printer_result:
        return printer_result

    # Workstation detection
    workstation_result = _detect_workstation(port_set, hostname_lower, os_lower)
    if workstation_result:
        return workstation_result

    # Unknown
    return ClassificationResult(
        device_type=DeviceType.UNKNOWN,
        confidence=0.3,
        reason="No clear classification signals",
    )


def _detect_medical_device(
    port_set: set[int],
    hostname_lower: str,
    port_services: dict[int, str],
) -> Optional[ClassificationResult]:
    """
    Detect medical devices.

    CRITICAL: Medical devices are EXCLUDED from scanning by default.
    This is a safety measure for healthcare environments.
    """
    # Check for medical device ports
    medical_ports_found = port_set & MEDICAL_DEVICE_PORTS
    if medical_ports_found:
        port_list = sorted(medical_ports_found)
        logger.warning(
            f"MEDICAL DEVICE DETECTED via ports {port_list}. "
            "Device will be EXCLUDED from scanning."
        )
        return ClassificationResult(
            device_type=DeviceType.MEDICAL,
            confidence=0.95,
            reason=f"Medical protocol ports detected: {port_list}",
            is_medical=True,
        )

    # Check for medical service names
    for port, service in port_services.items():
        service_lower = service.lower()
        if any(med in service_lower for med in ["dicom", "hl7", "fhir", "pacs"]):
            logger.warning(
                f"MEDICAL DEVICE DETECTED via service '{service}' on port {port}. "
                "Device will be EXCLUDED from scanning."
            )
            return ClassificationResult(
                device_type=DeviceType.MEDICAL,
                confidence=0.90,
                reason=f"Medical service detected: {service} on port {port}",
                is_medical=True,
            )

    # Check hostname patterns
    for pattern in MEDICAL_HOSTNAME_PATTERNS:
        if pattern in hostname_lower:
            logger.warning(
                f"MEDICAL DEVICE DETECTED via hostname pattern '{pattern}'. "
                "Device will be EXCLUDED from scanning."
            )
            return ClassificationResult(
                device_type=DeviceType.MEDICAL,
                confidence=0.80,
                reason=f"Medical hostname pattern detected: {pattern}",
                is_medical=True,
            )

    return None


def _detect_domain_controller(
    port_set: set[int],
    hostname_lower: str,
) -> Optional[ClassificationResult]:
    """Detect Windows Domain Controllers."""
    # DC ports: Kerberos (88), LDAP (389, 636), Global Catalog (3268, 3269)
    dc_ports = {88, 389, 636, 3268, 3269}
    dc_count = len(port_set & dc_ports)

    if dc_count >= 3:
        return ClassificationResult(
            device_type=DeviceType.SERVER,
            confidence=0.95,
            reason=f"Domain Controller detected ({dc_count} DC ports)",
        )

    # Hostname hints
    if any(hint in hostname_lower for hint in ["dc", "pdc", "bdc", "domain"]):
        if dc_count >= 1:
            return ClassificationResult(
                device_type=DeviceType.SERVER,
                confidence=0.85,
                reason="Domain Controller (hostname + DC ports)",
            )

    return None


def _detect_server(
    port_set: set[int],
    hostname_lower: str,
    os_lower: str,
) -> Optional[ClassificationResult]:
    """Detect server systems."""
    # Common server ports
    server_ports = {
        22,    # SSH
        25,    # SMTP
        53,    # DNS
        80,    # HTTP
        110,   # POP3
        143,   # IMAP
        443,   # HTTPS
        445,   # SMB
        1433,  # MSSQL
        1521,  # Oracle
        3306,  # MySQL
        5432,  # PostgreSQL
        5985,  # WinRM HTTP
        5986,  # WinRM HTTPS
        8080,  # HTTP Alt
        8443,  # HTTPS Alt
    }

    server_count = len(port_set & server_ports)

    # Strong server indicators: multiple server ports
    if server_count >= 4:
        return ClassificationResult(
            device_type=DeviceType.SERVER,
            confidence=0.90,
            reason=f"Multiple server ports detected ({server_count})",
        )

    # OS detection
    if "server" in os_lower:
        return ClassificationResult(
            device_type=DeviceType.SERVER,
            confidence=0.95,
            reason="Server OS detected",
        )

    # Hostname hints
    server_hints = ["srv", "server", "app", "db", "web", "mail", "sql", "file"]
    if any(hint in hostname_lower for hint in server_hints):
        if server_count >= 2:
            return ClassificationResult(
                device_type=DeviceType.SERVER,
                confidence=0.80,
                reason="Server hostname with service ports",
            )

    return None


def _detect_network_device(
    port_set: set[int],
    hostname_lower: str,
    port_services: dict[int, str],
) -> Optional[ClassificationResult]:
    """Detect network devices (routers, switches, APs)."""
    # SNMP ports
    snmp_ports = {161, 162}
    has_snmp = bool(port_set & snmp_ports)

    # Network device ports
    network_ports = {23, 830}  # Telnet, NETCONF

    # Check service names
    network_services = ["snmp", "cisco", "juniper", "netconf", "ssh"]
    has_network_service = any(
        any(ns in svc.lower() for ns in network_services)
        for svc in port_services.values()
    )

    if has_snmp and (port_set & network_ports or has_network_service):
        return ClassificationResult(
            device_type=DeviceType.NETWORK,
            confidence=0.90,
            reason="SNMP with network management protocols",
        )

    # Hostname hints
    network_hints = [
        "router", "switch", "fw", "firewall", "ap-", "wap",
        "ubnt", "unifi", "cisco", "juniper", "meraki"
    ]
    if any(hint in hostname_lower for hint in network_hints):
        return ClassificationResult(
            device_type=DeviceType.NETWORK,
            confidence=0.85,
            reason="Network device hostname pattern",
        )

    if has_snmp and len(port_set) <= 5:
        return ClassificationResult(
            device_type=DeviceType.NETWORK,
            confidence=0.70,
            reason="SNMP with minimal services (likely network device)",
        )

    return None


def _detect_printer(
    port_set: set[int],
    hostname_lower: str,
    port_services: dict[int, str],
) -> Optional[ClassificationResult]:
    """Detect printers and MFPs."""
    # Printer ports
    printer_ports = {
        9100,  # RAW/JetDirect
        515,   # LPD
        631,   # IPP/CUPS
    }

    printer_count = len(port_set & printer_ports)

    if printer_count >= 1:
        return ClassificationResult(
            device_type=DeviceType.PRINTER,
            confidence=0.90,
            reason=f"Printer port detected ({port_set & printer_ports})",
        )

    # Hostname hints
    printer_hints = [
        "print", "prn", "mfp", "copier", "hp-", "xerox",
        "canon", "epson", "brother", "ricoh", "lexmark"
    ]
    if any(hint in hostname_lower for hint in printer_hints):
        return ClassificationResult(
            device_type=DeviceType.PRINTER,
            confidence=0.80,
            reason="Printer hostname pattern",
        )

    # Service name hints
    for svc in port_services.values():
        if any(p in svc.lower() for p in ["print", "jetdirect", "ipp"]):
            return ClassificationResult(
                device_type=DeviceType.PRINTER,
                confidence=0.85,
                reason=f"Printer service detected: {svc}",
            )

    return None


def _detect_workstation(
    port_set: set[int],
    hostname_lower: str,
    os_lower: str,
) -> Optional[ClassificationResult]:
    """Detect workstations and desktops."""
    # Workstation indicators: RDP without server ports
    has_rdp = 3389 in port_set

    # Count server-like ports
    server_ports = {22, 25, 53, 80, 443, 445, 1433, 3306}
    server_count = len(port_set & server_ports)

    # Workstation OS hints
    workstation_os = ["windows 10", "windows 11", "macos", "ubuntu desktop"]
    is_workstation_os = any(ws in os_lower for ws in workstation_os)

    if has_rdp and server_count <= 2:
        return ClassificationResult(
            device_type=DeviceType.WORKSTATION,
            confidence=0.85,
            reason="RDP with limited server ports",
        )

    if is_workstation_os:
        return ClassificationResult(
            device_type=DeviceType.WORKSTATION,
            confidence=0.90,
            reason="Workstation OS detected",
        )

    # Hostname patterns
    workstation_hints = ["pc", "desktop", "laptop", "ws-", "client"]
    if any(hint in hostname_lower for hint in workstation_hints):
        return ClassificationResult(
            device_type=DeviceType.WORKSTATION,
            confidence=0.75,
            reason="Workstation hostname pattern",
        )

    # Default: if has RDP, likely workstation
    if has_rdp:
        return ClassificationResult(
            device_type=DeviceType.WORKSTATION,
            confidence=0.70,
            reason="RDP enabled",
        )

    return None


def discovered_to_device(discovered: DiscoveredDevice) -> Device:
    """
    Convert a DiscoveredDevice to a full Device with classification.

    CRITICAL: Medical devices are automatically marked as EXCLUDED.
    """
    # Classify the device
    result = classify_device(
        open_ports=discovered.open_ports,
        hostname=discovered.hostname,
        os_info=discovered.os_name,
        port_services=discovered.port_services,
    )

    # Create device
    device = Device(
        ip_address=discovered.ip_address,
        hostname=discovered.hostname,
        mac_address=discovered.mac_address,
        device_type=result.device_type,
        os_name=discovered.os_name,
        os_version=discovered.os_version,
        manufacturer=discovered.manufacturer,
        model=discovered.model,
        discovery_source=discovered.discovery_source,
        first_seen_at=discovered.discovered_at,
        last_seen_at=discovered.discovered_at,
    )

    # CRITICAL: Handle medical devices
    if result.is_medical:
        device.mark_as_medical()
        logger.warning(
            f"Medical device at {device.ip_address} EXCLUDED from scanning. "
            f"Reason: {result.reason}"
        )
    else:
        # Non-medical devices start as discovered, then can be monitored
        device.status = DeviceStatus.DISCOVERED
        device.compliance_status = ComplianceStatus.UNKNOWN

    # Store classification confidence (could be useful for auditing)
    # This would go in device notes or metadata

    return device
