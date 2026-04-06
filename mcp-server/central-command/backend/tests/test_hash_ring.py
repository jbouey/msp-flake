"""Tests for Python consistent hash ring — must match Go daemon's mesh.go exactly."""
import json
import os
import pytest
from hash_ring import HashRing, normalize_mac


def test_single_node_owns_everything():
    ring = HashRing(["AA:BB:CC:DD:EE:01"])
    # AA:BB:CC:DD:EE:01 normalizes to AABBCCDDEE01
    for ip in ["192.168.88.1", "192.168.88.100", "10.0.0.1", "172.16.0.50"]:
        assert ring.owner(ip) == "AABBCCDDEE01", f"single node should own {ip}"


def test_two_nodes_split_targets():
    ring = HashRing(["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"])
    owned1 = sum(1 for i in range(256) if ring.owner(f"192.168.88.{i}") == "AABBCCDDEE01")
    owned2 = sum(1 for i in range(256) if ring.owner(f"192.168.88.{i}") == "AABBCCDDEE02")
    assert owned1 > 0 and owned2 > 0, f"unbalanced: {owned1} vs {owned2}"
    assert owned1 + owned2 == 256, f"coverage gap: {owned1} + {owned2} != 256"


def test_three_nodes_reasonably_balanced():
    macs = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03"]
    ring = HashRing(macs)
    counts = {normalize_mac(m): 0 for m in macs}
    for i in range(1000):
        ip = f"10.0.{i // 256}.{i % 256}"
        counts[ring.owner(ip)] += 1
    for mac, count in counts.items():
        assert 200 < count < 500, f"node {mac} got {count}/1000 — too skewed"


def test_deterministic():
    ring = HashRing(["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"])
    owner1 = ring.owner("192.168.88.42")
    owner2 = ring.owner("192.168.88.42")
    assert owner1 == owner2


def test_normalize_mac():
    assert normalize_mac("aa:bb:cc:dd:ee:ff") == "AABBCCDDEEFF"
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "AABBCCDDEEFF"
    assert normalize_mac("AABBCCDDEEFF") == "AABBCCDDEEFF"


def test_targets_for_node():
    macs = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03"]
    ring = HashRing(macs)
    all_ips = [f"192.168.88.{i}" for i in range(1, 11)]
    targets = ring.targets_for_node("AA:BB:CC:DD:EE:01", all_ips)
    assert len(targets) > 0
    assert len(targets) < 11
    all_assigned = set()
    for mac in macs:
        all_assigned.update(ring.targets_for_node(mac, all_ips))
    assert all_assigned == set(all_ips)


def test_empty_targets_returns_empty():
    ring = HashRing(["AA:BB:CC:DD:EE:01"])
    assert ring.targets_for_node("AA:BB:CC:DD:EE:01", []) == []


def test_empty_ring_returns_empty_string():
    ring = HashRing([])
    assert ring.owner("192.168.88.1") == ""


def test_generate_cross_language_vectors():
    """Generate test vectors for Go to validate against."""
    macs = ["AABBCCDDEEFF", "112233445566", "7CD30A7C5518"]
    ring = HashRing(macs)
    targets = {}
    for ip in [
        "192.168.88.250", "192.168.88.251", "192.168.88.232",
        "192.168.0.11", "10.0.0.1", "172.16.0.50",
        "192.168.88.1", "192.168.88.100", "192.168.88.200", "192.168.88.50",
    ]:
        targets[ip] = ring.owner(ip)
    vectors = {"nodes": macs, "replicas": 64, "targets": targets}
    vector_path = os.path.join(
        os.path.dirname(__file__),
        "../../../../appliance/internal/daemon/testdata/hash_ring_vectors.json",
    )
    os.makedirs(os.path.dirname(vector_path), exist_ok=True)
    with open(vector_path, "w") as f:
        json.dump(vectors, f, indent=2, sort_keys=True)
    with open(vector_path) as f:
        loaded = json.load(f)
    assert loaded["nodes"] == macs
    assert len(loaded["targets"]) == 10
