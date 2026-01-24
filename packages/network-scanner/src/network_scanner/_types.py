"""
Type definitions for the network scanner.

These dataclasses define the core domain model for device discovery,
classification, and compliance tracking.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def now_utc() -> datetime:
    """Get current UTC timestamp (replaces deprecated datetime.utcnow())."""
    return datetime.now(timezone.utc)


class DeviceType(str, Enum):
    """Device classification types."""
    WORKSTATION = "workstation"
    SERVER = "server"
    NETWORK = "network"
    PRINTER = "printer"
    MEDICAL = "medical"  # EXCLUDED by default
    UNKNOWN = "unknown"


class ScanPolicy(str, Enum):
    """Scanning policy for a device."""
    STANDARD = "standard"  # Full compliance scanning
    LIMITED = "limited"    # Basic connectivity only
    EXCLUDED = "excluded"  # No scanning (medical devices default to this)


class DeviceStatus(str, Enum):
    """Device lifecycle status."""
    DISCOVERED = "discovered"  # Found but not yet categorized
    MONITORED = "monitored"    # Actively being scanned
    EXCLUDED = "excluded"      # Intentionally excluded (medical, etc.)
    OFFLINE = "offline"        # Not seen recently


class ComplianceStatus(str, Enum):
    """Device compliance status."""
    COMPLIANT = "compliant"
    DRIFTED = "drifted"
    UNKNOWN = "unknown"
    EXCLUDED = "excluded"


class DiscoverySource(str, Enum):
    """How the device was discovered."""
    AD = "ad"           # Active Directory query
    ARP = "arp"         # ARP table scan
    NMAP = "nmap"       # Port scan
    GO_AGENT = "go_agent"  # Go agent check-in
    MANUAL = "manual"   # Manually added


@dataclass
class DevicePort:
    """An open port discovered on a device."""
    device_id: str
    port: int
    protocol: str = "tcp"
    service_name: Optional[str] = None
    service_version: Optional[str] = None
    state: str = "open"
    last_seen_at: datetime = field(default_factory=now_utc)


@dataclass
class Device:
    """
    A discovered network device.

    Medical devices (detected by DICOM/HL7 ports) are EXCLUDED by default
    and require manual opt-in via manually_opted_in=True.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    hostname: Optional[str] = None
    ip_address: str = ""
    mac_address: Optional[str] = None

    # Classification
    device_type: DeviceType = DeviceType.UNKNOWN
    os_name: Optional[str] = None
    os_version: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None

    # Medical device handling - EXCLUDED BY DEFAULT
    medical_device: bool = False
    scan_policy: ScanPolicy = ScanPolicy.STANDARD
    manually_opted_in: bool = False  # Required for medical devices

    # PHI access (tracked for HIPAA compliance)
    phi_access_flag: bool = False

    # Discovery metadata
    discovery_source: DiscoverySource = DiscoverySource.NMAP
    first_seen_at: datetime = field(default_factory=now_utc)
    last_seen_at: datetime = field(default_factory=now_utc)
    last_scan_at: Optional[datetime] = None

    # Status
    status: DeviceStatus = DeviceStatus.DISCOVERED
    online: bool = False

    # Compliance
    compliance_status: ComplianceStatus = ComplianceStatus.UNKNOWN
    last_compliance_check: Optional[datetime] = None

    # Ports (populated after port scan)
    open_ports: list[DevicePort] = field(default_factory=list)

    # Sync tracking
    synced_to_central: bool = False
    sync_version: int = 0

    def mark_as_medical(self) -> None:
        """Mark device as medical - automatically excludes from scanning."""
        self.medical_device = True
        self.device_type = DeviceType.MEDICAL
        self.scan_policy = ScanPolicy.EXCLUDED
        self.status = DeviceStatus.EXCLUDED
        self.compliance_status = ComplianceStatus.EXCLUDED

    def opt_in_medical_device(self, reason: str = "") -> bool:
        """
        Manually opt-in a medical device for scanning.

        This is an explicit action required to scan any medical device.
        Returns True if opt-in was successful.
        """
        if not self.medical_device:
            return False

        self.manually_opted_in = True
        self.scan_policy = ScanPolicy.LIMITED  # Limited scanning for safety
        self.status = DeviceStatus.MONITORED
        self.compliance_status = ComplianceStatus.UNKNOWN
        return True

    @property
    def can_be_scanned(self) -> bool:
        """Check if device is eligible for compliance scanning."""
        if self.scan_policy == ScanPolicy.EXCLUDED:
            return False
        if self.medical_device and not self.manually_opted_in:
            return False
        return True


@dataclass
class ScanResult:
    """Result of a network scan operation."""
    scan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    scan_type: str = "full"  # full, quick, targeted
    started_at: datetime = field(default_factory=now_utc)
    completed_at: Optional[datetime] = None

    # Results
    devices_found: int = 0
    new_devices: int = 0
    changed_devices: int = 0
    medical_devices_excluded: int = 0

    # Methods used
    methods_used: list[str] = field(default_factory=list)
    network_ranges: list[str] = field(default_factory=list)

    # Status
    status: str = "running"  # running, completed, failed
    error_message: Optional[str] = None
    triggered_by: str = "schedule"  # schedule, manual, api


@dataclass
class ScanHistory:
    """Historical record of a scan."""
    id: str
    scan_type: str
    started_at: datetime
    completed_at: Optional[datetime]
    status: str
    devices_found: int
    new_devices: int
    changed_devices: int
    medical_devices_excluded: int
    methods_used: list[str]
    network_ranges: list[str]
    error_message: Optional[str]
    triggered_by: str


@dataclass
class DeviceComplianceCheck:
    """Result of a compliance check on a device."""
    id: Optional[int] = None
    device_id: str = ""
    check_type: str = ""  # firewall, antivirus, encryption, etc.
    hipaa_control: Optional[str] = None  # e.g., "164.312(a)(1)"
    status: str = "unknown"  # pass, warn, fail
    details: Optional[dict] = None
    checked_at: datetime = field(default_factory=now_utc)


# Medical device detection ports (DICOM, HL7, etc.)
MEDICAL_DEVICE_PORTS = frozenset({
    104,    # DICOM (primary)
    2575,   # HL7
    2761,   # DICOM TLS
    2762,   # DICOM ISCL
    11112,  # DICOM default
    4242,   # DICOM WADO
    8042,   # Orthanc DICOM
    8043,   # Orthanc DICOM TLS
    # Note: 8080 removed - too generic (used by Tomcat, proxies, etc.)
    11113,  # DICOM alternative
    11114,  # DICOM alternative
    11115,  # DICOM alternative
})

# Additional medical device indicators in hostnames
MEDICAL_HOSTNAME_PATTERNS = [
    "modality",
    "pacs",
    "dicom",
    "xray",
    "ct-",
    "mri-",
    "ultrasound",
    "ventilator",
    "ecg",
    "ekg",
    "infusion",
    "monitor-",
    "philips",
    "ge-healthcare",
    "siemens",
]
