#!/usr/bin/env python3
"""
Device Classification and Tier Assignment
Categorizes discovered devices for appropriate monitoring strategies
"""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DeviceType(Enum):
    """Standard device type classifications"""
    LINUX_SERVER = "linux_server"
    WINDOWS_SERVER = "windows_server"
    MACOS_SERVER = "macos_server"
    NETWORK_INFRASTRUCTURE = "network_infrastructure"
    FIREWALL = "firewall"
    VPN_GATEWAY = "vpn_gateway"
    LOAD_BALANCER = "load_balancer"
    DATABASE_SERVER = "database_server"
    APPLICATION_SERVER = "application_server"
    WEB_SERVER = "web_server"
    WINDOWS_WORKSTATION = "windows_workstation"
    MACOS_WORKSTATION = "macos_workstation"
    LINUX_WORKSTATION = "linux_workstation"
    PRINTER = "printer"
    SCANNER = "scanner"
    MEDICAL_DEVICE = "medical_device"
    IOT_DEVICE = "iot_device"
    UNKNOWN = "unknown"


class MonitoringTier(Enum):
    """Monitoring tier classifications"""
    TIER_1 = 1  # Infrastructure - Easy to monitor
    TIER_2 = 2  # Applications - Moderate difficulty
    TIER_3 = 3  # Business processes - Complex


@dataclass
class MonitoringStrategy:
    """Defines monitoring approach for a device"""
    tier: int
    method: str  # 'agent', 'agentless', 'snmp', 'syslog'
    auto_enroll: bool
    reason: str
    required_capabilities: List[str]
    hipaa_controls: List[str]


class DeviceClassifier:
    """
    Classifies devices and assigns monitoring strategies
    Based on service fingerprints, OS detection, and hostname patterns
    """

    # Service port mappings
    SERVICE_SIGNATURES = {
        'ssh': [22],
        'telnet': [23],
        'smtp': [25, 587],
        'dns': [53],
        'http': [80, 8080, 8000],
        'https': [443, 8443],
        'snmp': [161, 162],
        'ldap': [389, 636],
        'smb': [139, 445],
        'mysql': [3306],
        'postgresql': [5432],
        'mssql': [1433],
        'oracle': [1521],
        'mongodb': [27017],
        'redis': [6379],
        'elasticsearch': [9200, 9300],
        'rabbitmq': [5672, 15672],
        'docker': [2375, 2376],
        'kubernetes': [6443, 10250],
        'printer_lpd': [515],
        'printer_ipp': [631],
        'printer_raw': [9100],
        'dicom': [104, 2761, 2762, 11112],
        'hl7': [2575],
        'pacs': [104],
        'rdp': [3389],
        'vnc': [5900, 5901],
    }

    # OS classification patterns
    OS_PATTERNS = {
        'linux': ['linux', 'ubuntu', 'debian', 'centos', 'rhel', 'fedora', 'suse'],
        'windows_server': ['windows server', 'win server'],
        'windows_workstation': ['windows 10', 'windows 11', 'windows 7', 'windows 8'],
        'macos': ['mac os', 'macos', 'darwin', 'os x'],
        'network_device': ['cisco', 'juniper', 'arista', 'hp switch', 'dell switch'],
        'printer': ['printer', 'hp laserjet', 'canon', 'xerox', 'brother'],
    }

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.auto_enroll_tiers = self.config.get('auto_enroll_tiers', [1, 2])
        self.excluded_types = self.config.get('excluded_types', [
            'windows_workstation',
            'macos_workstation',
            'printer'
        ])

    def classify_device(self, device: Dict) -> Dict:
        """
        Main classification method
        Returns enhanced device dict with classification and monitoring strategy
        """
        logger.debug(f"Classifying device: {device.get('ip')}")

        # Extract features
        services = device.get('services', [])
        os_info = (device.get('os') or '').lower()
        hostname = (device.get('hostname') or '').lower()

        # Classify device type
        device_type = self._determine_device_type(services, os_info, hostname)
        device['device_type'] = device_type.value

        # Assign monitoring tier
        tier = self._assign_tier(device_type, services, os_info)
        device['tier'] = tier.value

        # Determine monitoring strategy
        strategy = self._determine_monitoring_strategy(
            device_type, tier, services, os_info
        )

        device['monitoring_strategy'] = {
            'tier': strategy.tier,
            'method': strategy.method,
            'auto_enroll': strategy.auto_enroll,
            'reason': strategy.reason,
            'required_capabilities': strategy.required_capabilities,
            'hipaa_controls': strategy.hipaa_controls
        }

        # Determine if should be monitored
        device['should_monitor'] = self._should_monitor(device_type)

        # Add classification metadata
        device['classification_metadata'] = {
            'service_count': len(services),
            'open_ports': [s.get('port') for s in services if s.get('state') == 'open'],
            'os_confidence': device.get('os_accuracy', 0),
            'classification_timestamp': device.get('timestamp')
        }

        return device

    def _determine_device_type(
        self,
        services: List[Dict],
        os_info: str,
        hostname: str
    ) -> DeviceType:
        """
        Determine device type from available information
        Priority: Services > OS > Hostname
        """
        # Extract open ports
        open_ports = [
            s.get('port') for s in services
            if s.get('state') == 'open' and s.get('port')
        ]

        # Check for medical devices (highest priority for HIPAA compliance)
        if self._has_service_signature(open_ports, 'dicom') or \
           self._has_service_signature(open_ports, 'hl7') or \
           'pacs' in hostname or 'modality' in hostname:
            return DeviceType.MEDICAL_DEVICE

        # Check for network infrastructure
        if self._has_service_signature(open_ports, 'snmp') and \
           not self._has_service_signature(open_ports, 'http'):
            return DeviceType.NETWORK_INFRASTRUCTURE

        # Check for printers
        if self._has_service_signature(open_ports, 'printer_lpd') or \
           self._has_service_signature(open_ports, 'printer_ipp') or \
           self._has_service_signature(open_ports, 'printer_raw'):
            return DeviceType.PRINTER

        # Check OS patterns
        for os_type, patterns in self.OS_PATTERNS.items():
            if any(pattern in os_info for pattern in patterns):
                if os_type == 'linux':
                    # Differentiate between server and workstation
                    if self._has_server_services(open_ports):
                        return DeviceType.LINUX_SERVER
                    else:
                        return DeviceType.LINUX_WORKSTATION

                elif os_type == 'windows_server':
                    return DeviceType.WINDOWS_SERVER

                elif os_type == 'windows_workstation':
                    return DeviceType.WINDOWS_WORKSTATION

                elif os_type == 'macos':
                    if self._has_server_services(open_ports):
                        return DeviceType.MACOS_SERVER
                    else:
                        return DeviceType.MACOS_WORKSTATION

                elif os_type == 'network_device':
                    return DeviceType.NETWORK_INFRASTRUCTURE

        # Check for specific server roles
        if self._has_service_signature(open_ports, 'mysql') or \
           self._has_service_signature(open_ports, 'postgresql') or \
           self._has_service_signature(open_ports, 'mssql') or \
           self._has_service_signature(open_ports, 'mongodb'):
            return DeviceType.DATABASE_SERVER

        if self._has_service_signature(open_ports, 'http') or \
           self._has_service_signature(open_ports, 'https'):
            return DeviceType.WEB_SERVER

        # Generic server if has SSH and other services
        if self._has_service_signature(open_ports, 'ssh') and len(open_ports) > 2:
            return DeviceType.LINUX_SERVER

        # Windows server if has RDP and other services
        if self._has_service_signature(open_ports, 'rdp') and len(open_ports) > 2:
            return DeviceType.WINDOWS_SERVER

        return DeviceType.UNKNOWN

    def _assign_tier(
        self,
        device_type: DeviceType,
        services: List[Dict],
        os_info: str
    ) -> MonitoringTier:
        """
        Assign monitoring tier based on device type and complexity
        """
        tier_1_types = [
            DeviceType.LINUX_SERVER,
            DeviceType.WINDOWS_SERVER,
            DeviceType.NETWORK_INFRASTRUCTURE,
            DeviceType.FIREWALL,
            DeviceType.VPN_GATEWAY,
            DeviceType.WEB_SERVER,
        ]

        tier_2_types = [
            DeviceType.DATABASE_SERVER,
            DeviceType.APPLICATION_SERVER,
            DeviceType.WINDOWS_WORKSTATION,
            DeviceType.MACOS_WORKSTATION,
        ]

        tier_3_types = [
            DeviceType.MEDICAL_DEVICE,
            DeviceType.IOT_DEVICE,
        ]

        if device_type in tier_1_types:
            return MonitoringTier.TIER_1
        elif device_type in tier_2_types:
            return MonitoringTier.TIER_2
        elif device_type in tier_3_types:
            return MonitoringTier.TIER_3
        else:
            return MonitoringTier.TIER_1  # Default

    def _determine_monitoring_strategy(
        self,
        device_type: DeviceType,
        tier: MonitoringTier,
        services: List[Dict],
        os_info: str
    ) -> MonitoringStrategy:
        """
        Determine the appropriate monitoring strategy for a device
        """
        # Extract open ports
        open_ports = [
            s.get('port') for s in services
            if s.get('state') == 'open' and s.get('port')
        ]

        # Linux/Windows servers - Agent-based monitoring
        if device_type in [DeviceType.LINUX_SERVER, DeviceType.WINDOWS_SERVER]:
            return MonitoringStrategy(
                tier=tier.value,
                method='agent',
                auto_enroll=tier.value in self.auto_enroll_tiers,
                reason='Server with SSH/RDP access - agent deployment recommended',
                required_capabilities=['ssh_access', 'sudo_privileges'],
                hipaa_controls=[
                    '164.312(b)',  # Audit controls
                    '164.308(a)(1)(ii)(D)',  # Information system activity review
                ]
            )

        # Network infrastructure - SNMP monitoring
        elif device_type in [DeviceType.NETWORK_INFRASTRUCTURE, DeviceType.FIREWALL]:
            return MonitoringStrategy(
                tier=tier.value,
                method='snmp',
                auto_enroll=True,
                reason='Network device - SNMP monitoring',
                required_capabilities=['snmp_read_access'],
                hipaa_controls=[
                    '164.312(b)',  # Audit controls
                    '164.310(d)(1)',  # Device and media controls
                ]
            )

        # Database servers - Agent with database module
        elif device_type == DeviceType.DATABASE_SERVER:
            return MonitoringStrategy(
                tier=tier.value,
                method='agent',
                auto_enroll=tier.value in self.auto_enroll_tiers,
                reason='Database server - requires specialized monitoring',
                required_capabilities=['ssh_access', 'database_read_access'],
                hipaa_controls=[
                    '164.312(b)',  # Audit controls
                    '164.308(a)(1)(ii)(D)',  # Information system activity review
                    '164.312(a)(1)',  # Access control
                ]
            )

        # Web servers - Agent + WAF logs
        elif device_type == DeviceType.WEB_SERVER:
            return MonitoringStrategy(
                tier=tier.value,
                method='agent',
                auto_enroll=True,
                reason='Web server - agent with HTTP access logs',
                required_capabilities=['ssh_access', 'log_access'],
                hipaa_controls=[
                    '164.312(b)',  # Audit controls
                    '164.312(e)(1)',  # Transmission security
                ]
            )

        # Medical devices - Agentless (DICOM logs, HL7 feeds)
        elif device_type == DeviceType.MEDICAL_DEVICE:
            return MonitoringStrategy(
                tier=tier.value,
                method='agentless',
                auto_enroll=False,  # Requires manual approval
                reason='Medical device - agentless monitoring only (vendor restrictions)',
                required_capabilities=['syslog_receiver', 'network_monitoring'],
                hipaa_controls=[
                    '164.312(b)',  # Audit controls
                    '164.310(d)(1)',  # Device and media controls
                    '164.308(a)(1)(ii)(D)',  # Information system activity review
                ]
            )

        # Workstations - Excluded by default (infra-only scope)
        elif device_type in [DeviceType.WINDOWS_WORKSTATION, DeviceType.MACOS_WORKSTATION]:
            return MonitoringStrategy(
                tier=tier.value,
                method='excluded',
                auto_enroll=False,
                reason='Endpoint device - outside infrastructure monitoring scope',
                required_capabilities=[],
                hipaa_controls=[]
            )

        # Printers - Minimal monitoring
        elif device_type == DeviceType.PRINTER:
            return MonitoringStrategy(
                tier=tier.value,
                method='snmp',
                auto_enroll=False,
                reason='Printer - minimal SNMP monitoring if needed',
                required_capabilities=['snmp_read_access'],
                hipaa_controls=[]
            )

        # Unknown devices - Manual review required
        else:
            return MonitoringStrategy(
                tier=tier.value,
                method='manual_review',
                auto_enroll=False,
                reason='Unknown device type - requires manual classification',
                required_capabilities=[],
                hipaa_controls=[]
            )

    def _should_monitor(self, device_type: DeviceType) -> bool:
        """
        Determine if device should be monitored based on infra-only scope
        """
        # Excluded device types (infra-only service catalog)
        if device_type.value in self.excluded_types:
            return False

        # Include all infrastructure and server types
        monitored_types = [
            DeviceType.LINUX_SERVER,
            DeviceType.WINDOWS_SERVER,
            DeviceType.MACOS_SERVER,
            DeviceType.NETWORK_INFRASTRUCTURE,
            DeviceType.FIREWALL,
            DeviceType.VPN_GATEWAY,
            DeviceType.LOAD_BALANCER,
            DeviceType.DATABASE_SERVER,
            DeviceType.APPLICATION_SERVER,
            DeviceType.WEB_SERVER,
        ]

        return device_type in monitored_types

    def _has_service_signature(self, open_ports: List[int], service_name: str) -> bool:
        """Check if device has a specific service signature"""
        signature_ports = self.SERVICE_SIGNATURES.get(service_name, [])
        return any(port in open_ports for port in signature_ports)

    def _has_server_services(self, open_ports: List[int]) -> bool:
        """Determine if device is running server services"""
        server_services = [
            'ssh', 'http', 'https', 'mysql', 'postgresql', 'mssql',
            'ldap', 'smb', 'docker', 'kubernetes'
        ]

        return any(
            self._has_service_signature(open_ports, service)
            for service in server_services
        )

    def generate_classification_report(self, devices: List[Dict]) -> Dict:
        """
        Generate summary report of device classifications
        """
        report = {
            'total_devices': len(devices),
            'by_type': {},
            'by_tier': {1: 0, 2: 0, 3: 0},
            'by_monitoring_method': {},
            'auto_enroll_count': 0,
            'manual_review_count': 0,
            'excluded_count': 0,
        }

        for device in devices:
            # Count by type
            device_type = device.get('device_type', 'unknown')
            report['by_type'][device_type] = report['by_type'].get(device_type, 0) + 1

            # Count by tier
            tier = device.get('tier', 1)
            report['by_tier'][tier] = report['by_tier'].get(tier, 0) + 1

            # Count by monitoring method
            strategy = device.get('monitoring_strategy', {})
            method = strategy.get('method', 'unknown')
            report['by_monitoring_method'][method] = \
                report['by_monitoring_method'].get(method, 0) + 1

            # Count enrollment status
            if strategy.get('auto_enroll'):
                report['auto_enroll_count'] += 1
            elif method == 'excluded':
                report['excluded_count'] += 1
            else:
                report['manual_review_count'] += 1

        return report


def main():
    """Example usage"""
    from scanner import DiscoveredDevice
    import json

    # Example discovered devices
    devices = [
        {
            'ip': '192.168.1.10',
            'hostname': 'web-server-01',
            'os': 'Ubuntu Linux 22.04',
            'services': [
                {'port': 22, 'state': 'open', 'name': 'ssh'},
                {'port': 80, 'state': 'open', 'name': 'http'},
                {'port': 443, 'state': 'open', 'name': 'https'},
            ]
        },
        {
            'ip': '192.168.1.50',
            'hostname': 'cisco-sw-01',
            'os': 'Cisco IOS',
            'services': [
                {'port': 23, 'state': 'open', 'name': 'telnet'},
                {'port': 161, 'state': 'open', 'name': 'snmp'},
            ]
        },
        {
            'ip': '192.168.1.100',
            'hostname': 'hp-printer-01',
            'os': '',
            'services': [
                {'port': 631, 'state': 'open', 'name': 'ipp'},
                {'port': 9100, 'state': 'open', 'name': 'jetdirect'},
            ]
        },
    ]

    classifier = DeviceClassifier()

    print("=== Device Classification Results ===\n")

    classified_devices = []
    for device in devices:
        classified = classifier.classify_device(device)
        classified_devices.append(classified)

        print(f"IP: {classified['ip']}")
        print(f"  Type: {classified['device_type']}")
        print(f"  Tier: {classified['tier']}")
        print(f"  Should Monitor: {classified['should_monitor']}")
        print(f"  Strategy: {classified['monitoring_strategy']['method']}")
        print(f"  Auto-Enroll: {classified['monitoring_strategy']['auto_enroll']}")
        print(f"  Reason: {classified['monitoring_strategy']['reason']}")
        print()

    # Generate report
    report = classifier.generate_classification_report(classified_devices)
    print("=== Classification Report ===")
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
