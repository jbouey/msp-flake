"""
Nmap-based network discovery.

Uses nmap for port scanning and service detection.
Provides the most detailed device information including:
- Open ports and services
- OS detection
- Device classification hints
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

try:
    import nmap
    NMAP_AVAILABLE = True
except ImportError:
    NMAP_AVAILABLE = False
    nmap = None

from .._types import DiscoverySource, MEDICAL_DEVICE_PORTS
from .base import DiscoveredDevice, DiscoveryMethod

logger = logging.getLogger(__name__)


class NmapDiscovery(DiscoveryMethod):
    """
    Discover devices using nmap port scanning.

    Provides detailed port and service information for device classification.
    Medical device ports (DICOM, HL7) are detected and flagged.
    """

    def __init__(
        self,
        network_ranges: list[str],
        scan_arguments: str = "-sS -sV --top-ports 1000",
        host_timeout: int = 60,
        max_concurrent: int = 10,
    ):
        """
        Initialize nmap discovery.

        Args:
            network_ranges: List of CIDR ranges to scan
            scan_arguments: nmap command arguments
            host_timeout: Timeout per host in seconds
            max_concurrent: Maximum concurrent host scans
        """
        self.network_ranges = network_ranges
        self.scan_arguments = scan_arguments
        self.host_timeout = host_timeout
        self.max_concurrent = max_concurrent
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent)

    @property
    def name(self) -> str:
        return "nmap"

    async def is_available(self) -> bool:
        """Check if nmap is available."""
        if not NMAP_AVAILABLE:
            logger.warning("python-nmap library not installed")
            return False

        try:
            result = await asyncio.create_subprocess_exec(
                "which", "nmap",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await result.wait()
            return result.returncode == 0
        except Exception:
            return False

    async def discover(self) -> list[DiscoveredDevice]:
        """
        Discover devices using nmap.

        Returns list of discovered devices with port information.
        """
        if not NMAP_AVAILABLE:
            logger.error("python-nmap not available")
            return []

        devices = []
        loop = asyncio.get_event_loop()

        for network_range in self.network_ranges:
            try:
                logger.info(f"Scanning network range: {network_range}")

                # Run nmap scan in thread pool (it's synchronous)
                range_devices = await loop.run_in_executor(
                    self._executor,
                    self._scan_range,
                    network_range,
                )
                devices.extend(range_devices)

            except Exception as e:
                logger.error(f"Error scanning {network_range}: {e}")

        logger.info(f"Nmap discovery found {len(devices)} hosts")
        return devices

    def _scan_range(self, network_range: str) -> list[DiscoveredDevice]:
        """
        Scan a single network range (runs in thread pool).

        This is a blocking operation that should not be called from async code.
        """
        devices = []

        try:
            scanner = nmap.PortScanner()

            # Add host timeout to arguments
            args = f"{self.scan_arguments} --host-timeout {self.host_timeout}s"

            logger.debug(f"Running nmap: {network_range} {args}")
            scanner.scan(hosts=network_range, arguments=args)

            for host in scanner.all_hosts():
                try:
                    device = self._parse_host(scanner, host)
                    if device:
                        devices.append(device)
                except Exception as e:
                    logger.warning(f"Error parsing host {host}: {e}")

        except Exception as e:
            logger.error(f"Nmap scan error: {e}")

        return devices

    def _parse_host(self, scanner, host: str) -> Optional[DiscoveredDevice]:
        """Parse nmap results for a single host."""
        host_info = scanner[host]

        # Check host state
        if host_info.state() != "up":
            return None

        # Get hostname
        hostname = None
        if "hostnames" in host_info:
            for hn in host_info["hostnames"]:
                if hn.get("name"):
                    hostname = hn["name"]
                    break

        # Get MAC address
        mac_address = None
        manufacturer = None
        if "addresses" in host_info:
            mac_address = host_info["addresses"].get("mac")
        if "vendor" in host_info and mac_address:
            manufacturer = host_info["vendor"].get(mac_address)

        # Get OS info
        os_name = None
        os_version = None
        if "osmatch" in host_info and host_info["osmatch"]:
            best_match = host_info["osmatch"][0]
            os_name = best_match.get("name")
            if "osclass" in best_match and best_match["osclass"]:
                os_class = best_match["osclass"][0]
                os_version = os_class.get("osgen")

        # Get open ports
        open_ports = []
        port_services = {}
        has_medical_ports = False

        for proto in ["tcp", "udp"]:
            if proto in host_info:
                for port, port_info in host_info[proto].items():
                    if port_info.get("state") == "open":
                        open_ports.append(port)
                        service = port_info.get("name", "")
                        version = port_info.get("version", "")
                        if version:
                            port_services[port] = f"{service} {version}"
                        else:
                            port_services[port] = service

                        # Check for medical device ports
                        if port in MEDICAL_DEVICE_PORTS:
                            has_medical_ports = True
                            logger.info(f"Medical port {port} detected on {host}")

        device = DiscoveredDevice(
            ip_address=host,
            hostname=hostname,
            mac_address=mac_address,
            os_name=os_name,
            os_version=os_version,
            manufacturer=manufacturer,
            open_ports=open_ports,
            port_services=port_services,
            discovery_source=DiscoverySource.NMAP,
        )

        # Flag potential medical devices
        if has_medical_ports:
            logger.warning(
                f"Potential medical device detected at {host} "
                f"(ports: {[p for p in open_ports if p in MEDICAL_DEVICE_PORTS]})"
            )

        return device


class NmapPingSweep(DiscoveryMethod):
    """
    Quick ping sweep using nmap.

    Faster than full port scan, good for initial discovery.
    """

    def __init__(
        self,
        network_ranges: list[str],
        timeout: int = 30,
    ):
        """
        Initialize ping sweep.

        Args:
            network_ranges: List of CIDR ranges to scan
            timeout: Total scan timeout in seconds
        """
        self.network_ranges = network_ranges
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "nmap-ping"

    async def is_available(self) -> bool:
        """Check if nmap is available."""
        if not NMAP_AVAILABLE:
            return False
        try:
            result = await asyncio.create_subprocess_exec(
                "which", "nmap",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await result.wait()
            return result.returncode == 0
        except Exception:
            return False

    async def discover(self) -> list[DiscoveredDevice]:
        """
        Run ping sweep to find live hosts.

        Returns list of discovered devices (IP only).
        """
        if not NMAP_AVAILABLE:
            return []

        devices = []
        loop = asyncio.get_event_loop()

        for network_range in self.network_ranges:
            try:
                range_devices = await loop.run_in_executor(
                    None,
                    self._ping_sweep,
                    network_range,
                )
                devices.extend(range_devices)
            except Exception as e:
                logger.error(f"Ping sweep error for {network_range}: {e}")

        logger.info(f"Ping sweep found {len(devices)} hosts")
        return devices

    def _ping_sweep(self, network_range: str) -> list[DiscoveredDevice]:
        """Run ping sweep (blocking)."""
        devices = []

        try:
            scanner = nmap.PortScanner()
            scanner.scan(
                hosts=network_range,
                arguments=f"-sn --host-timeout {self.timeout}s",
            )

            for host in scanner.all_hosts():
                if scanner[host].state() == "up":
                    hostname = None
                    if "hostnames" in scanner[host]:
                        for hn in scanner[host]["hostnames"]:
                            if hn.get("name"):
                                hostname = hn["name"]
                                break

                    devices.append(DiscoveredDevice(
                        ip_address=host,
                        hostname=hostname,
                        discovery_source=DiscoverySource.NMAP,
                    ))

        except Exception as e:
            logger.error(f"Ping sweep error: {e}")

        return devices
