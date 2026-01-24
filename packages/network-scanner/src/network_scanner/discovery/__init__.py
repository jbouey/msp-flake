"""
Discovery methods for network scanning.

Each discovery method implements the same interface:
- async discover() -> list[DiscoveredDevice]

Methods:
- AD Discovery: Query Active Directory for domain-joined computers
- ARP Discovery: Read ARP table for recently-seen hosts
- Nmap Discovery: Port scan for active hosts and services
- Go Agent: Listen for Go agent check-ins
"""

from .base import DiscoveredDevice, DiscoveryMethod
from .ad_discovery import ADDiscovery, ADDiscoveryConfig
from .arp_discovery import ARPDiscovery, ARPScanDiscovery
from .nmap_discovery import NmapDiscovery, NmapPingSweep
from .go_agent import GoAgentListener, GoAgentDiscovery, GoAgentRegistry, AgentInfo

__all__ = [
    "DiscoveredDevice",
    "DiscoveryMethod",
    "ADDiscovery",
    "ADDiscoveryConfig",
    "ARPDiscovery",
    "ARPScanDiscovery",
    "NmapDiscovery",
    "NmapPingSweep",
    "GoAgentListener",
    "GoAgentDiscovery",
    "GoAgentRegistry",
    "AgentInfo",
]
