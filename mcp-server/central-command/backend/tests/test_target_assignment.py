"""Tests for server-side target assignment in checkin response.

Validates the consistent hash ring properties that the checkin handler
relies on: full coverage, no double-assignment, and graceful redistribution
when the appliance fleet changes size.
"""
import pytest

try:
    from hash_ring import HashRing, normalize_mac_for_ring
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
                f"{t} assigned to both {assigned[t]} and {normalize_mac_for_ring(mac)}"
            )
            assigned[t] = normalize_mac_for_ring(mac)
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
    surviving = {normalize_mac_for_ring(m) for m in macs_2}
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


# === Round-robin fallback tests ===

def test_round_robin_few_targets_all_nodes_get_work():
    """With targets < 2x nodes, round-robin guarantees every node gets at least 1."""
    macs = ["7C:D3:0A:7C:55:18", "84:3A:5B:91:B6:61", "84:3A:5B:1F:FF:E4"]
    ring = HashRing(macs)
    targets = ["192.168.88.233", "192.168.88.250", "192.168.88.251", "192.168.88.50"]
    # 4 targets < 2*3=6 → round-robin
    assignment = ring.get_full_assignment(targets)
    for mac, assigned in assignment.items():
        assert len(assigned) >= 1, f"{mac} got 0 targets in round-robin mode"
    # Total coverage
    all_assigned = set()
    for assigned in assignment.values():
        all_assigned.update(assigned)
    assert all_assigned == set(targets)


def test_round_robin_no_overlap():
    """Round-robin path assigns each target to exactly one node."""
    macs = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03"]
    ring = HashRing(macs)
    targets = ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.5"]
    assignment = ring.get_full_assignment(targets)
    all_assigned = []
    for assigned in assignment.values():
        all_assigned.extend(assigned)
    assert len(all_assigned) == len(set(all_assigned)), "Duplicate target assignment"


def test_round_robin_deterministic():
    """Round-robin produces same result across instances."""
    macs = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"]
    targets = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    ring_a = HashRing(macs)
    ring_b = HashRing(macs)
    for mac in macs:
        assert ring_a.targets_for_node(mac, targets) == ring_b.targets_for_node(mac, targets)


def test_switches_to_hash_ring_above_threshold():
    """With enough targets, uses hash ring instead of round-robin."""
    macs = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"]
    ring = HashRing(macs)
    # 4 targets >= 2*2=4 → hash ring path
    targets = [f"10.0.0.{i}" for i in range(1, 5)]
    # Verify full coverage (hash ring should still work)
    all_assigned = set()
    for mac in macs:
        all_assigned.update(ring.targets_for_node(mac, targets))
    assert all_assigned == set(targets)


# === Error handling tests ===

def test_none_mac_returns_empty():
    """None MAC doesn't crash."""
    ring = HashRing(["AA:BB:CC:DD:EE:01"])
    assert ring.targets_for_node(None, ["10.0.0.1"]) == []


def test_empty_string_mac_returns_empty():
    """Empty string MAC doesn't crash."""
    ring = HashRing(["AA:BB:CC:DD:EE:01"])
    assert ring.targets_for_node("", ["10.0.0.1"]) == []


def test_none_macs_in_constructor():
    """None values in MAC list are filtered out."""
    ring = HashRing([None, "", "AA:BB:CC:DD:EE:01", None])
    assert ring.node_count == 1
    assert ring.targets_for_node("AA:BB:CC:DD:EE:01", ["10.0.0.1"]) == ["10.0.0.1"]


def test_empty_ring():
    """Empty ring returns empty for everything."""
    ring = HashRing([])
    assert ring.node_count == 0
    assert ring.targets_for_node("AA:BB:CC:DD:EE:01", ["10.0.0.1"]) == []
    assert ring.owner("10.0.0.1") == ""


def test_duplicate_targets_deduplicated():
    """Duplicate target IPs are handled without double assignment."""
    ring = HashRing(["AA:BB:CC:DD:EE:01"])
    targets = ["10.0.0.1", "10.0.0.1", "10.0.0.2", "10.0.0.2"]
    result = ring.targets_for_node("AA:BB:CC:DD:EE:01", targets)
    assert sorted(result) == ["10.0.0.1", "10.0.0.2"]


def test_validate_healthy_ring():
    """Validate returns None for a correctly built ring."""
    ring = HashRing(["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"])
    assert ring.validate() is None


def test_validate_empty_ring():
    """Validate reports error for empty ring."""
    ring = HashRing([])
    assert ring.validate() is not None


def test_get_full_assignment_complete_coverage():
    """get_full_assignment covers all targets with no gaps."""
    macs = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03"]
    ring = HashRing(macs)
    targets = [f"10.0.0.{i}" for i in range(1, 21)]
    assignment = ring.get_full_assignment(targets)
    all_assigned = set()
    for assigned in assignment.values():
        all_assigned.update(assigned)
    assert all_assigned == set(targets), f"Missing: {set(targets) - all_assigned}"


def test_normalize_mac_for_ring_formats():
    """Various MAC formats normalize to the same value."""
    assert normalize_mac_for_ring("7C:D3:0A:7C:55:18") == "7CD30A7C5518"
    assert normalize_mac_for_ring("7c-d3-0a-7c-55-18") == "7CD30A7C5518"
    assert normalize_mac_for_ring("7cd30a7c5518") == "7CD30A7C5518"
    assert normalize_mac_for_ring("7C.D3.0A.7C.55.18") == "7CD30A7C5518"
    assert normalize_mac_for_ring("") == ""
    assert normalize_mac_for_ring(None) == ""
