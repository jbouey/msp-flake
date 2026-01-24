"""
MSP Network Scanner - Device discovery and inventory for compliance appliances.

This service discovers and classifies network devices, maintaining a local
inventory database for compliance monitoring. Medical devices are EXCLUDED
by default and require explicit manual opt-in.

Architecture:
    network-scanner.service (EYES) - discovers and assesses network devices
    Separate from compliance-agent.service (HANDS) - healing and remediation

Sovereignty:
    - All data stored locally in /var/lib/msp/devices.db
    - Works fully offline without Central Command
    - Syncs to Central Command when connected (optional)
"""

__version__ = "1.0.0"

from ._types import (
    Device,
    DeviceType,
    DevicePort,
    ScanPolicy,
    DeviceStatus,
    ComplianceStatus,
    DiscoverySource,
    ScanResult,
    ScanHistory,
    DeviceComplianceCheck,
)

__all__ = [
    "__version__",
    "Device",
    "DeviceType",
    "DevicePort",
    "ScanPolicy",
    "DeviceStatus",
    "ComplianceStatus",
    "DiscoverySource",
    "ScanResult",
    "ScanHistory",
    "DeviceComplianceCheck",
]
