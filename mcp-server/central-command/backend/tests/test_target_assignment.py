"""Tests for server-side target assignment in checkin response.

Validates the consistent hash ring properties that the checkin handler
relies on: full coverage, no double-assignment, and graceful redistribution
when the appliance fleet changes size.
"""
import pytest

try:
    from hash_ring import HashRing, normalize_mac
except ImportError:
    pytest.skip("hash_ring not yet available", allow_module_level=True)


def test_three_appliances_six_targets_full_coverage():
    """Every target is assigned to exactly one appliance — no gaps, no overlap."""
    macs = ["7C:D3:0A:7C:55:18", "84:3A:5B:91:B6:61", "84:3A:5B:1F:FF:E4"]
    ring = HashRing(macs)
    all_ips = [
        "192.168.88.250", "192.168.88.251", "192.168.88.232",
        "192.168.0.11", "192.168.88.50", "192.168.88.233",
    ]
    assigned: dict = {}
    for mac in macs:
        targets = ring.targets_for_node(mac, all_ips)
        for t in targets:
            assert t not in assigned, (
                f"{t} assigned to both {assigned[t]} and {normalize_mac(mac)}"
            )
            assigned[t] = normalize_mac(mac)
    assert set(assigned.keys()) == set(all_ips), (
        f"Missing: {set(all_ips) - set(assigned.keys())}"
    )


def test_single_appliance_gets_all_targets():
    """A lone appliance receives the complete target list."""
    ring = HashRing(["AA:BB:CC:DD:EE:01"])
    all_ips = ["192.168.88.1", "192.168.88.2", "10.0.0.1"]
    targets = ring.targets_for_node("AA:BB:CC:DD:EE:01", all_ips)
    assert set(targets) == set(all_ips)


def test_node_removal_redistributes():
    """When a node leaves, its targets move to surviving nodes with full coverage."""
    macs_3 = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03"]
    macs_2 = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"]
    ring_3 = HashRing(macs_3)
    ring_2 = HashRing(macs_2)
    all_ips = [f"192.168.88.{i}" for i in range(1, 21)]

    # Targets previously owned by node 3 are now owned by surviving nodes
    targets_3_had = ring_3.targets_for_node("AA:BB:CC:DD:EE:03", all_ips)
    surviving = {normalize_mac(m) for m in macs_2}
    for ip in targets_3_had:
        owner_after = ring_2.owner(ip)
        assert owner_after in surviving, (
            f"{ip} ended up at unexpected owner {owner_after}"
        )

    # All targets are still covered after the node leaves
    all_assigned = set()
    for mac in macs_2:
        all_assigned.update(ring_2.targets_for_node(mac, all_ips))
    assert all_assigned == set(all_ips)


def test_empty_targets_returns_empty():
    """No targets in → empty list out (no crash)."""
    ring = HashRing(["AA:BB:CC:DD:EE:01"])
    assert ring.targets_for_node("AA:BB:CC:DD:EE:01", []) == []


def test_unknown_mac_returns_empty():
    """A MAC not in the ring gets no targets (checkin safety guard)."""
    ring = HashRing(["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"])
    all_ips = ["192.168.88.1", "192.168.88.2"]
    targets = ring.targets_for_node("FF:FF:FF:FF:FF:FF", all_ips)
    assert targets == []


def test_assignment_is_deterministic():
    """Same ring + same targets always yields the same assignment."""
    macs = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03"]
    all_ips = [f"10.0.0.{i}" for i in range(1, 16)]
    ring_a = HashRing(macs)
    ring_b = HashRing(macs)
    for mac in macs:
        assert ring_a.targets_for_node(mac, all_ips) == ring_b.targets_for_node(mac, all_ips)


def test_two_appliances_no_overlap():
    """With two appliances, no target should appear in both assignment sets."""
    macs = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"]
    ring = HashRing(macs)
    all_ips = [f"192.168.1.{i}" for i in range(1, 31)]
    set1 = set(ring.targets_for_node(macs[0], all_ips))
    set2 = set(ring.targets_for_node(macs[1], all_ips))
    overlap = set1 & set2
    assert not overlap, f"Targets in both assignments: {overlap}"
    assert set1 | set2 == set(all_ips), "Union should equal all targets"
