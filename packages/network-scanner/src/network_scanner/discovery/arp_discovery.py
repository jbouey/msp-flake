"""
ARP table discovery.

Reads the local ARP cache to find recently-seen hosts on the network.
Fast but limited to hosts that have communicated recently.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from typing import Optional

from .._types import DiscoverySource
from .base import DiscoveredDevice, DiscoveryMethod

logger = logging.getLogger(__name__)


class ARPDiscovery(DiscoveryMethod):
    """
    Discover devices from the ARP cache.

    This is a fast, passive discovery method that finds hosts
    that have communicated with the appliance recently.
    """

    def __init__(self, interface: Optional[str] = None):
        """
        Initialize ARP discovery.

        Args:
            interface: Network interface to query (None for all)
        """
        self.interface = interface

    @property
    def name(self) -> str:
        return "arp"

    async def is_available(self) -> bool:
        """Check if ARP command is available."""
        try:
            result = await asyncio.create_subprocess_exec(
                "which", "arp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await result.wait()
            return result.returncode == 0
        except Exception:
            return False

    async def discover(self) -> list[DiscoveredDevice]:
        """
        Discover devices from ARP cache.

        Returns list of discovered devices with IP and MAC addresses.
        """
        devices = []

        try:
            # Build command
            cmd = ["arp", "-an"]
            if self.interface:
                cmd.extend(["-i", self.interface])

            # Run arp command
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await result.communicate()

            if result.returncode != 0:
                logger.error(f"ARP command failed: {stderr.decode()}")
                return devices

            # Parse output
            # Linux format: ? (192.168.1.1) at aa:bb:cc:dd:ee:ff [ether] on eth0
            # macOS format: ? (192.168.1.1) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]
            output = stdout.decode()

            for line in output.strip().split("\n"):
                if not line:
                    continue

                try:
                    device = self._parse_arp_line(line)
                    if device:
                        devices.append(device)
                except Exception as e:
                    logger.debug(f"Failed to parse ARP line '{line}': {e}")
                    continue

            logger.info(f"ARP discovery found {len(devices)} hosts")

        except Exception as e:
            logger.error(f"Error during ARP discovery: {e}")

        return devices

    def _parse_arp_line(self, line: str) -> Optional[DiscoveredDevice]:
        """Parse a single ARP output line."""
        # Pattern for both Linux and macOS
        # Matches: hostname (IP) at MAC ...
        pattern = r"(?:(\S+)\s+)?\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-fA-F:]+)"
        match = re.search(pattern, line)

        if not match:
            return None

        hostname = match.group(1) if match.group(1) != "?" else None
        ip_address = match.group(2)
        mac_address = match.group(3).lower()

        # Skip incomplete entries (no MAC)
        if mac_address in ("(incomplete)", "ff:ff:ff:ff:ff:ff"):
            return None

        # Get manufacturer from MAC OUI (first 3 octets)
        manufacturer = self._lookup_oui(mac_address)

        return DiscoveredDevice(
            ip_address=ip_address,
            hostname=hostname,
            mac_address=mac_address,
            manufacturer=manufacturer,
            discovery_source=DiscoverySource.ARP,
        )

    def _lookup_oui(self, mac_address: str) -> Optional[str]:
        """
        Look up manufacturer from MAC OUI.

        This is a simplified lookup with common OUIs.
        A full implementation would use a complete OUI database.
        """
        # Common OUI prefixes (first 6 hex chars)
        oui_map = {
            "00:50:56": "VMware",
            "00:0c:29": "VMware",
            "00:1c:42": "Parallels",
            "08:00:27": "VirtualBox",
            "52:54:00": "QEMU/KVM",
            "00:15:5d": "Microsoft Hyper-V",
            "ac:de:48": "Dell",
            "d4:be:d9": "Dell",
            "00:1e:67": "HP",
            "3c:d9:2b": "HP",
            "00:1a:a0": "Lenovo",
            "78:dd:12": "Lenovo",
            "f0:9f:c2": "Apple",
            "3c:22:fb": "Apple",
            "00:1b:63": "Cisco",
            "00:26:cb": "Cisco",
            "00:00:5e": "IANA (VRRP)",
        }

        # Normalize MAC format
        mac_prefix = mac_address.lower()[:8]
        return oui_map.get(mac_prefix)


class ARPScanDiscovery(DiscoveryMethod):
    """
    Active ARP scanning using arp-scan.

    More thorough than passive ARP cache but requires arp-scan installed.
    """

    def __init__(
        self,
        network_range: str,
        interface: Optional[str] = None,
    ):
        """
        Initialize ARP scan discovery.

        Args:
            network_range: CIDR range to scan (e.g., "192.168.1.0/24")
            interface: Network interface to use
        """
        self.network_range = network_range
        self.interface = interface

    @property
    def name(self) -> str:
        return "arp-scan"

    async def is_available(self) -> bool:
        """Check if arp-scan is available."""
        try:
            result = await asyncio.create_subprocess_exec(
                "which", "arp-scan",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await result.wait()
            return result.returncode == 0
        except Exception:
            return False

    async def discover(self) -> list[DiscoveredDevice]:
        """
        Discover devices using active ARP scanning.

        Returns list of discovered devices.
        """
        devices = []

        try:
            # Build command
            cmd = ["arp-scan", "--localnet"]
            if self.interface:
                cmd.extend(["--interface", self.interface])
            if self.network_range:
                cmd = ["arp-scan", self.network_range]
                if self.interface:
                    cmd.extend(["--interface", self.interface])

            # Run arp-scan (requires root)
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await result.communicate()

            if result.returncode != 0:
                logger.error(f"arp-scan failed: {stderr.decode()}")
                return devices

            # Parse output
            # Format: 192.168.1.1    aa:bb:cc:dd:ee:ff    Manufacturer
            output = stdout.decode()

            for line in output.strip().split("\n"):
                if not line or line.startswith("Interface:") or line.startswith("Starting"):
                    continue

                parts = line.split("\t")
                if len(parts) >= 2:
                    ip_address = parts[0].strip()
                    mac_address = parts[1].strip().lower()
                    manufacturer = parts[2].strip() if len(parts) > 2 else None

                    # Validate IP format
                    if not re.match(r"\d+\.\d+\.\d+\.\d+", ip_address):
                        continue

                    devices.append(DiscoveredDevice(
                        ip_address=ip_address,
                        mac_address=mac_address,
                        manufacturer=manufacturer,
                        discovery_source=DiscoverySource.ARP,
                    ))

            logger.info(f"ARP scan discovered {len(devices)} hosts")

        except Exception as e:
            logger.error(f"Error during ARP scan: {e}")

        return devices
