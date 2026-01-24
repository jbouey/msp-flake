"""
Base classes for discovery methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .._types import DiscoverySource, now_utc


@dataclass
class DiscoveredDevice:
    """
    A device discovered by a discovery method.

    This is a lightweight representation before classification.
    The scanner service will convert this to a full Device object.
    """
    ip_address: str
    hostname: Optional[str] = None
    mac_address: Optional[str] = None

    # OS info (if available)
    os_name: Optional[str] = None
    os_version: Optional[str] = None

    # Manufacturer info (from MAC OUI or other sources)
    manufacturer: Optional[str] = None
    model: Optional[str] = None

    # Open ports (if port scanning was done)
    open_ports: list[int] = field(default_factory=list)
    port_services: dict[int, str] = field(default_factory=dict)

    # Discovery metadata
    discovery_source: DiscoverySource = DiscoverySource.NMAP
    discovered_at: datetime = field(default_factory=now_utc)

    # Additional data from AD, etc.
    distinguished_name: Optional[str] = None  # AD DN
    domain: Optional[str] = None
    is_domain_joined: bool = False


class DiscoveryMethod(ABC):
    """Base class for discovery methods."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of this discovery method."""
        pass

    @abstractmethod
    async def discover(self) -> list[DiscoveredDevice]:
        """
        Discover devices using this method.

        Returns list of discovered devices.
        """
        pass

    async def is_available(self) -> bool:
        """Check if this discovery method is available."""
        return True
