#!/usr/bin/env python3
"""
Network Discovery Scanner
Multi-method device discovery for MSP automation platform
"""

import asyncio
import json
import logging
import ipaddress
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict

try:
    import nmap3
    NMAP_AVAILABLE = True
except ImportError:
    NMAP_AVAILABLE = False
    print("Warning: python-nmap3 not installed. Active scanning disabled.")

try:
    from scapy.all import sniff, ARP, IP
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False
    print("Warning: scapy not installed. Passive discovery disabled.")

try:
    from pysnmp.hlapi import *
    SNMP_AVAILABLE = True
except ImportError:
    SNMP_AVAILABLE = False
    print("Warning: pysnmp not installed. SNMP discovery disabled.")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class DiscoveredDevice:
    """Represents a discovered network device"""
    ip: str
    client_id: str
    discovery_method: str
    timestamp: str
    hostname: Optional[str] = None
    mac: Optional[str] = None
    os: Optional[str] = None
    os_accuracy: Optional[int] = None
    device_type: Optional[str] = None
    services: List[Dict] = None
    tier: Optional[int] = None
    monitored: bool = False
    enrollment_status: str = "discovered"
    metadata: Dict = None

    def __post_init__(self):
        if self.services is None:
            self.services = []
        if self.metadata is None:
            self.metadata = {}

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)


class NetworkDiscovery:
    """Multi-method network discovery system"""

    def __init__(self, client_id: str, config: Dict):
        self.client_id = client_id
        self.config = config
        self.discovered_devices = {}

    async def discover_devices(self, subnets: List[str]) -> List[DiscoveredDevice]:
        """
        Main discovery orchestrator
        Uses multiple methods in parallel for comprehensive coverage
        """
        logger.info(f"Starting device discovery for client {self.client_id}")
        logger.info(f"Subnets: {subnets}")

        devices = []
        methods = self.config.get('methods', ['active_nmap'])

        # Run discovery methods in parallel
        tasks = []

        if 'active_nmap' in methods and NMAP_AVAILABLE:
            for subnet in subnets:
                tasks.append(self._discover_active_nmap(subnet))

        if 'passive_arp' in methods and SCAPY_AVAILABLE:
            tasks.append(self._discover_passive_arp())

        if 'snmp_walk' in methods and SNMP_AVAILABLE:
            for subnet in subnets:
                tasks.append(self._discover_snmp(subnet))

        # Gather all results
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Flatten results and handle exceptions
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Discovery method failed: {result}")
            elif isinstance(result, list):
                devices.extend(result)
            elif result is not None:
                devices.append(result)

        # Deduplicate by IP address (keep most detailed record)
        unique_devices = {}
        for device in devices:
            if device.ip not in unique_devices:
                unique_devices[device.ip] = device
            else:
                # Merge information from multiple discovery methods
                existing = unique_devices[device.ip]
                if not existing.hostname and device.hostname:
                    existing.hostname = device.hostname
                if not existing.mac and device.mac:
                    existing.mac = device.mac
                if not existing.os and device.os:
                    existing.os = device.os
                existing.services.extend(device.services)
                existing.discovery_method += f",{device.discovery_method}"

        # Classify devices
        for device in unique_devices.values():
            device.device_type = self._classify_device(device)
            device.tier = self._assign_tier(device)

        logger.info(f"Discovery complete. Found {len(unique_devices)} unique devices")

        return list(unique_devices.values())

    async def _discover_active_nmap(self, subnet: str) -> List[DiscoveredDevice]:
        """
        Active network scanning using nmap
        Identifies live hosts, OS, services, and versions
        """
        if not NMAP_AVAILABLE:
            logger.warning("Nmap not available, skipping active scan")
            return []

        logger.info(f"Starting active nmap scan of {subnet}")

        devices = []
        nm = nmap3.Nmap()

        try:
            # Fast ping sweep first to find live hosts
            logger.debug(f"Ping sweep: {subnet}")
            ping_results = nm.nmap_ping_scan(subnet)

            live_hosts = []
            for ip, data in ping_results.items():
                if ip in ['stats', 'runtime']:
                    continue
                if isinstance(data, dict) and data.get('state', {}).get('state') == 'up':
                    live_hosts.append(ip)

            logger.info(f"Found {len(live_hosts)} live hosts")

            # Detailed scan of live hosts
            for host in live_hosts:
                try:
                    logger.debug(f"Scanning {host}")

                    # Service and version detection
                    scan_result = nm.nmap_version_detection(host)

                    if host not in scan_result:
                        continue

                    host_data = scan_result[host]

                    device = DiscoveredDevice(
                        ip=host,
                        client_id=self.client_id,
                        discovery_method='active_nmap',
                        timestamp=datetime.utcnow().isoformat()
                    )

                    # Extract hostname
                    if 'hostname' in host_data and host_data['hostname']:
                        if isinstance(host_data['hostname'], list):
                            device.hostname = host_data['hostname'][0].get('name')
                        else:
                            device.hostname = host_data['hostname']

                    # Extract MAC address
                    if 'macaddress' in host_data:
                        device.mac = host_data['macaddress'].get('addr')

                    # Extract OS information
                    if 'osmatch' in host_data and host_data['osmatch']:
                        best_match = host_data['osmatch'][0]
                        device.os = best_match.get('name')
                        device.os_accuracy = int(best_match.get('accuracy', 0))

                    # Extract services
                    if 'ports' in host_data:
                        for port_data in host_data['ports']:
                            if isinstance(port_data, dict):
                                service = {
                                    'port': port_data.get('portid'),
                                    'protocol': port_data.get('protocol'),
                                    'name': port_data.get('service', {}).get('name'),
                                    'product': port_data.get('service', {}).get('product', ''),
                                    'version': port_data.get('service', {}).get('version', ''),
                                    'state': port_data.get('state')
                                }
                                device.services.append(service)

                    devices.append(device)

                except Exception as e:
                    logger.error(f"Error scanning host {host}: {e}")

        except Exception as e:
            logger.error(f"Error in nmap scan of {subnet}: {e}")

        return devices

    async def _discover_passive_arp(self, duration: int = 60) -> List[DiscoveredDevice]:
        """
        Passive ARP monitoring
        Discovers devices from broadcast ARP traffic
        """
        if not SCAPY_AVAILABLE:
            logger.warning("Scapy not available, skipping passive discovery")
            return []

        logger.info(f"Starting passive ARP monitoring for {duration} seconds")

        devices_found = {}

        def packet_handler(packet):
            """Process captured ARP packets"""
            if ARP in packet:
                ip = packet[ARP].psrc
                mac = packet[ARP].hwsrc

                if ip not in devices_found:
                    device = DiscoveredDevice(
                        ip=ip,
                        mac=mac,
                        client_id=self.client_id,
                        discovery_method='passive_arp',
                        timestamp=datetime.utcnow().isoformat()
                    )
                    devices_found[ip] = device
                    logger.debug(f"Discovered device via ARP: {ip} ({mac})")

        try:
            # Sniff ARP packets for specified duration
            sniff(
                filter="arp",
                prn=packet_handler,
                timeout=duration,
                store=False
            )
        except Exception as e:
            logger.error(f"Error in passive ARP monitoring: {e}")

        logger.info(f"Passive ARP discovered {len(devices_found)} devices")
        return list(devices_found.values())

    async def _discover_snmp(self, subnet: str) -> List[DiscoveredDevice]:
        """
        SNMP-based discovery for managed network equipment
        Queries: sysDescr, sysName, sysLocation
        """
        if not SNMP_AVAILABLE:
            logger.warning("SNMP not available, skipping SNMP discovery")
            return []

        logger.info(f"Starting SNMP discovery of {subnet}")

        devices = []
        community = self.config.get('snmp_community', 'public')

        # Generate list of IPs to check
        try:
            network = ipaddress.ip_network(subnet, strict=False)
            hosts = list(network.hosts())
        except Exception as e:
            logger.error(f"Error parsing subnet {subnet}: {e}")
            return []

        # Limit to reasonable number for SNMP (avoid DoS)
        if len(hosts) > 254:
            logger.warning(f"Subnet {subnet} too large for SNMP walk, limiting to /24")
            hosts = hosts[:254]

        for host in hosts:
            host_ip = str(host)

            try:
                # Query SNMP system information
                iterator = getCmd(
                    SnmpEngine(),
                    CommunityData(community),
                    UdpTransportTarget((host_ip, 161), timeout=1, retries=0),
                    ContextData(),
                    ObjectType(ObjectIdentity('SNMPv2-MIB', 'sysDescr', 0)),
                    ObjectType(ObjectIdentity('SNMPv2-MIB', 'sysName', 0)),
                    ObjectType(ObjectIdentity('SNMPv2-MIB', 'sysLocation', 0))
                )

                errorIndication, errorStatus, errorIndex, varBinds = next(iterator)

                if not errorIndication and not errorStatus:
                    device = DiscoveredDevice(
                        ip=host_ip,
                        client_id=self.client_id,
                        discovery_method='snmp',
                        timestamp=datetime.utcnow().isoformat(),
                        device_type='network_infrastructure',
                        tier=1,
                        metadata={
                            'snmp_sys_descr': str(varBinds[0][1]),
                            'snmp_sys_location': str(varBinds[2][1])
                        }
                    )

                    # Extract hostname from sysName
                    device.hostname = str(varBinds[1][1])

                    devices.append(device)
                    logger.debug(f"SNMP discovered: {host_ip} ({device.hostname})")

            except Exception as e:
                # Silent fail - most hosts won't respond to SNMP
                pass

        logger.info(f"SNMP discovered {len(devices)} devices")
        return devices

    def _classify_device(self, device: DiscoveredDevice) -> str:
        """
        Classify device type based on services, OS, and other indicators
        Returns: Device type classification
        """
        services = device.services or []
        os = (device.os or '').lower()
        hostname = (device.hostname or '').lower()

        # Network infrastructure classification
        if device.discovery_method == 'snmp':
            return 'network_infrastructure'

        # Check common service ports for classification
        service_ports = [s.get('port') for s in services if s.get('port')]

        # Server classification
        server_ports = {22, 80, 443, 3306, 5432, 1433, 389, 636, 5672, 6379, 27017}
        if any(port in server_ports for port in service_ports):
            if 'linux' in os or 'unix' in os:
                return 'linux_server'
            elif 'windows server' in os:
                return 'windows_server'
            else:
                return 'server_unknown'

        # Network infrastructure (router/switch/firewall)
        network_ports = {23, 161, 162, 830}
        if any(port in network_ports for port in service_ports):
            return 'network_infrastructure'

        # Workstation classification
        if 'windows' in os and 'server' not in os:
            return 'windows_workstation'
        elif 'mac os' in os or 'darwin' in os:
            return 'macos_workstation'

        # Printer classification
        printer_ports = {515, 631, 9100}
        if any(port in printer_ports for port in service_ports):
            return 'printer'

        # Medical device indicators (DICOM ports)
        medical_ports = {104, 2761, 2762, 11112}
        if any(port in medical_ports for port in service_ports):
            return 'medical_device'

        # Database servers
        db_ports = {3306, 5432, 1433, 5984, 27017, 6379}
        if any(port in db_ports for port in service_ports):
            return 'database_server'

        # Web servers
        web_ports = {80, 443, 8080, 8443}
        if any(port in web_ports for port in service_ports):
            return 'web_server'

        return 'unknown'

    def _assign_tier(self, device: DiscoveredDevice) -> int:
        """
        Assign monitoring tier based on device type
        Tier 1: Infrastructure (easy to monitor)
        Tier 2: Applications (moderate difficulty)
        Tier 3: Business processes (complex)
        """
        device_type = device.device_type or 'unknown'

        tier_1_types = [
            'linux_server', 'windows_server', 'network_infrastructure',
            'firewall', 'vpn_gateway', 'web_server'
        ]

        tier_2_types = [
            'database_server', 'application_server',
            'windows_workstation', 'macos_workstation'
        ]

        tier_3_types = [
            'medical_device', 'ehr_server', 'pacs_server'
        ]

        if device_type in tier_1_types:
            return 1
        elif device_type in tier_2_types:
            return 2
        elif device_type in tier_3_types:
            return 3
        else:
            return 1  # Default to Tier 1


async def main():
    """Example usage"""
    config = {
        'methods': ['active_nmap'],  # Enable available methods
        'snmp_community': 'public'
    }

    discovery = NetworkDiscovery(
        client_id='clinic-001',
        config=config
    )

    # Example: scan local network
    subnets = ['192.168.1.0/24']

    devices = await discovery.discover_devices(subnets)

    # Print results
    print(f"\n=== Discovery Results ===")
    print(f"Total devices found: {len(devices)}\n")

    for device in devices:
        print(f"IP: {device.ip}")
        print(f"  Hostname: {device.hostname or 'N/A'}")
        print(f"  MAC: {device.mac or 'N/A'}")
        print(f"  OS: {device.os or 'N/A'}")
        print(f"  Type: {device.device_type}")
        print(f"  Tier: {device.tier}")
        print(f"  Services: {len(device.services)}")
        print(f"  Method: {device.discovery_method}")
        print()

    # Save to JSON
    output_file = f"discovery_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump([d.to_dict() for d in devices], f, indent=2)

    print(f"Results saved to {output_file}")


if __name__ == '__main__':
    asyncio.run(main())
