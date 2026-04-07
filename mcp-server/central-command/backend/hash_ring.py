"""
Consistent hash ring for server-side target assignment.

CRITICAL: This implementation MUST produce identical assignments to the Go
daemon's HashRing in appliance/internal/daemon/mesh.go. Both use:
- SHA256 of "{MAC}:{i}" for i in 0..63
- First 4 bytes as big-endian uint32
- MAC normalized to uppercase, no separators
- Clockwise nearest-node assignment (binary search)
"""
import hashlib
import struct
from bisect import bisect_left
from typing import List

import structlog

logger = structlog.get_logger(__name__)

REPLICAS = 64  # Must match mesh.go NewHashRing() replicas field


def normalize_mac(mac: str) -> str:
    """Normalize MAC to uppercase, no separators. Matches Go normalizeMACForRing()."""
    return mac.upper().replace(":", "").replace("-", "")


def _hash_key(key: str) -> int:
    """SHA256 first 4 bytes as big-endian uint32. Matches Go hashKey()."""
    h = hashlib.sha256(key.encode()).digest()
    return struct.unpack(">I", h[:4])[0]


class HashRing:
    """Consistent hash ring matching Go daemon's implementation exactly."""

    def __init__(self, macs: List[str]):
        self._nodes = sorted(set(normalize_mac(m) for m in macs))
        self._ring: List[tuple] = []
        for mac in self._nodes:
            for i in range(REPLICAS):
                h = _hash_key(f"{mac}:{i}")
                self._ring.append((h, mac))
        self._ring.sort(key=lambda x: x[0])
        self._hashes = [entry[0] for entry in self._ring]
        logger.debug(
            "hash_ring_initialized",
            node_count=len(self._nodes),
            ring_entries=len(self._ring),
        )

    def owner(self, target_ip: str) -> str:
        """Return the MAC that owns the target IP. Matches Go HashRing.owner()."""
        if not self._ring:
            return ""
        h = _hash_key(target_ip)
        idx = bisect_left(self._hashes, h)
        if idx >= len(self._ring):
            idx = 0
        return self._ring[idx][1]

    def targets_for_node(self, mac: str, target_ips: List[str]) -> List[str]:
        """Return the subset of target_ips assigned to this MAC.

        When targets are scarce (< 2x nodes), consistent hashing can degenerate
        and assign all targets to one node. Fall back to deterministic round-robin
        in that case to guarantee every node gets work.
        """
        norm = normalize_mac(mac)

        # Small target set: round-robin for guaranteed distribution
        if len(target_ips) < 2 * len(self._nodes) and len(self._nodes) > 1 and norm in self._nodes:
            sorted_targets = sorted(target_ips)
            node_idx = sorted(self._nodes).index(norm)
            return [t for i, t in enumerate(sorted_targets) if i % len(self._nodes) == node_idx]

        return [ip for ip in target_ips if self.owner(ip) == norm]
