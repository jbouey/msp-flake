"""
Consistent hash ring for server-side target assignment.

CRITICAL: This implementation MUST produce identical assignments to the Go
daemon's HashRing in appliance/internal/daemon/mesh.go for the hash-ring
path. Both use:
- SHA256 of "{MAC}:{i}" for i in 0..63
- First 4 bytes as big-endian uint32
- MAC normalized to uppercase, no separators
- Clockwise nearest-node assignment (binary search)

When targets < 2x nodes, a deterministic round-robin fallback guarantees
every node gets work (the Go daemon uses server-authoritative assignments
so it doesn't need to match this path).
"""
import hashlib
import struct
from bisect import bisect_left
from typing import Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

REPLICAS = 64  # Must match mesh.go NewHashRing() replicas field


def normalize_mac(mac: str) -> str:
    """Normalize MAC to uppercase, no separators. Matches Go normalizeMACForRing().

    Returns empty string for invalid input rather than raising.
    """
    if not mac or not isinstance(mac, str):
        return ""
    return mac.upper().replace(":", "").replace("-", "").replace(".", "").strip()


def _hash_key(key: str) -> int:
    """SHA256 first 4 bytes as big-endian uint32. Matches Go hashKey()."""
    h = hashlib.sha256(key.encode()).digest()
    return struct.unpack(">I", h[:4])[0]


class HashRing:
    """Consistent hash ring matching Go daemon's implementation.

    Thread-safe after construction (immutable ring). Create a new instance
    when the node set changes.
    """

    def __init__(self, macs: List[str]):
        # Filter out empty/invalid MACs
        valid_macs = [normalize_mac(m) for m in (macs or [])]
        self._nodes = sorted(set(m for m in valid_macs if m))

        if not self._nodes:
            logger.warning("hash_ring_empty", input_macs=len(macs or []))
            self._ring: List[tuple] = []
            self._hashes: List[int] = []
            self._node_index: Dict[str, int] = {}
            return

        # Build ring: each node gets REPLICAS virtual positions
        self._ring = []
        for mac in self._nodes:
            for i in range(REPLICAS):
                h = _hash_key(f"{mac}:{i}")
                self._ring.append((h, mac))
        self._ring.sort(key=lambda x: x[0])
        self._hashes = [entry[0] for entry in self._ring]

        # Precompute node index for O(1) round-robin lookup
        self._node_index = {mac: idx for idx, mac in enumerate(self._nodes)}

        logger.info(
            "hash_ring_initialized",
            node_count=len(self._nodes),
            ring_entries=len(self._ring),
            nodes=self._nodes,
        )

    @property
    def node_count(self) -> int:
        """Number of unique nodes in the ring."""
        return len(self._nodes)

    @property
    def nodes(self) -> List[str]:
        """Sorted list of normalized MAC addresses in the ring."""
        return list(self._nodes)

    def owner(self, target_ip: str) -> str:
        """Return the MAC that owns the target IP via consistent hashing.

        Returns empty string if the ring is empty or target is invalid.
        """
        if not self._ring or not target_ip:
            return ""
        h = _hash_key(target_ip)
        idx = bisect_left(self._hashes, h)
        if idx >= len(self._ring):
            idx = 0
        return self._ring[idx][1]

    def targets_for_node(self, mac: str, target_ips: List[str]) -> List[str]:
        """Return the subset of target_ips assigned to this MAC.

        Strategy selection:
        - targets < 2x nodes: deterministic round-robin (guarantees every
          node gets work — consistent hashing degenerates with few items)
        - targets >= 2x nodes: consistent hash ring (standard path)

        Returns empty list for unknown MACs, empty inputs, or ring errors.
        """
        if not target_ips or not mac:
            return []

        norm = normalize_mac(mac)
        if not norm:
            logger.warning("hash_ring_invalid_mac", raw_mac=mac)
            return []

        # Unknown node — not in the ring
        if norm not in self._node_index:
            logger.debug("hash_ring_unknown_node", mac=norm, ring_nodes=self._nodes)
            return []

        # Deduplicate targets while preserving order
        seen = set()
        unique_targets = []
        for ip in target_ips:
            if ip and ip not in seen:
                seen.add(ip)
                unique_targets.append(ip)

        if not unique_targets:
            return []

        # Small target set: deterministic round-robin
        if len(unique_targets) < 2 * len(self._nodes) and len(self._nodes) > 1:
            sorted_targets = sorted(unique_targets)
            node_idx = self._node_index[norm]  # O(1) lookup
            assigned = [t for i, t in enumerate(sorted_targets) if i % len(self._nodes) == node_idx]
            logger.debug(
                "hash_ring_round_robin",
                mac=norm,
                assigned_count=len(assigned),
                total_targets=len(unique_targets),
                node_count=len(self._nodes),
            )
            return assigned

        # Standard consistent hash ring path
        assigned = [ip for ip in unique_targets if self.owner(ip) == norm]
        logger.debug(
            "hash_ring_consistent",
            mac=norm,
            assigned_count=len(assigned),
            total_targets=len(unique_targets),
            node_count=len(self._nodes),
        )
        return assigned

    def get_full_assignment(self, target_ips: List[str]) -> Dict[str, List[str]]:
        """Compute the full assignment map: {mac: [targets]} for all nodes.

        Useful for observability and debugging — shows the complete picture
        without requiring per-node calls.
        """
        result: Dict[str, List[str]] = {mac: [] for mac in self._nodes}
        if not target_ips or not self._nodes:
            return result

        # Use the same logic as targets_for_node for consistency
        for mac in self._nodes:
            result[mac] = self.targets_for_node(mac, target_ips)

        # Validate: every target should be assigned to exactly one node
        all_assigned = set()
        for targets in result.values():
            all_assigned.update(targets)
        unique_targets = set(ip for ip in target_ips if ip)
        unassigned = unique_targets - all_assigned
        if unassigned:
            logger.error(
                "hash_ring_unassigned_targets",
                unassigned=sorted(unassigned),
                node_count=len(self._nodes),
            )

        return result

    def validate(self) -> Optional[str]:
        """Check ring integrity. Returns error message or None if valid."""
        if not self._nodes:
            return "Ring has no nodes"
        if len(self._ring) != len(self._nodes) * REPLICAS:
            return f"Ring size mismatch: {len(self._ring)} != {len(self._nodes)} * {REPLICAS}"
        if self._hashes != sorted(self._hashes):
            return "Ring hashes not sorted"
        return None
