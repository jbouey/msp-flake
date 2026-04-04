package daemon

import (
	"fmt"
	"testing"
	"time"
)

func TestHashRing_SingleNode_OwnsEverything(t *testing.T) {
	r := NewHashRing()
	r.AddNode("AA:BB:CC:DD:EE:01")

	for _, ip := range []string{"192.168.88.1", "192.168.88.100", "10.0.0.1", "172.16.0.50"} {
		if !r.OwnsTarget("AA:BB:CC:DD:EE:01", ip) {
			t.Errorf("single node should own %s", ip)
		}
	}
}

func TestHashRing_TwoNodes_SplitTargets(t *testing.T) {
	r := NewHashRing()
	r.AddNode("AA:BB:CC:DD:EE:01")
	r.AddNode("AA:BB:CC:DD:EE:02")

	owned1, owned2 := 0, 0
	for i := 0; i < 256; i++ {
		ip := fmt.Sprintf("192.168.88.%d", i)
		if r.OwnsTarget("AA:BB:CC:DD:EE:01", ip) {
			owned1++
		}
		if r.OwnsTarget("AA:BB:CC:DD:EE:02", ip) {
			owned2++
		}
	}

	if owned1 == 0 || owned2 == 0 {
		t.Errorf("unbalanced: node1=%d, node2=%d", owned1, owned2)
	}
	if owned1+owned2 != 256 {
		t.Errorf("coverage gap: node1=%d + node2=%d != 256", owned1, owned2)
	}
}

func TestHashRing_ThreeNodes_ReasonablyBalanced(t *testing.T) {
	r := NewHashRing()
	r.AddNode("AA:BB:CC:DD:EE:01")
	r.AddNode("AA:BB:CC:DD:EE:02")
	r.AddNode("AA:BB:CC:DD:EE:03")

	counts := map[string]int{}
	total := 1000
	for i := 0; i < total; i++ {
		ip := fmt.Sprintf("10.0.%d.%d", i/256, i%256)
		for _, mac := range []string{"AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03"} {
			if r.OwnsTarget(mac, ip) {
				counts[mac]++
			}
		}
	}

	for mac, count := range counts {
		ratio := float64(count) / float64(total)
		if ratio < 0.15 || ratio > 0.50 {
			t.Errorf("node %s has %d/%d (%.1f%%) — too unbalanced", mac, count, total, ratio*100)
		}
	}
}

func TestHashRing_RemoveNode_RedistributesTargets(t *testing.T) {
	r := NewHashRing()
	r.AddNode("AA:BB:CC:DD:EE:01")
	r.AddNode("AA:BB:CC:DD:EE:02")

	ownerBefore := r.TargetOwner("192.168.88.50")

	r.RemoveNode("AA:BB:CC:DD:EE:02")

	if !r.OwnsTarget("AA:BB:CC:DD:EE:01", "192.168.88.50") {
		t.Error("remaining node should own all targets after peer removal")
	}

	r.AddNode("AA:BB:CC:DD:EE:02")
	ownerAfter := r.TargetOwner("192.168.88.50")

	if ownerBefore != ownerAfter {
		t.Errorf("owner changed after re-add: %s → %s (should be stable)", ownerBefore, ownerAfter)
	}
}

func TestHashRing_EmptyRing_OwnsNothing(t *testing.T) {
	r := NewHashRing()
	if r.OwnsTarget("AA:BB:CC:DD:EE:01", "192.168.88.1") {
		t.Error("empty ring should own nothing")
	}
}

func TestHashRing_NodeCount(t *testing.T) {
	r := NewHashRing()
	if r.NodeCount() != 0 {
		t.Errorf("expected 0, got %d", r.NodeCount())
	}
	r.AddNode("AA:BB:CC:DD:EE:01")
	if r.NodeCount() != 1 {
		t.Errorf("expected 1, got %d", r.NodeCount())
	}
	r.AddNode("AA:BB:CC:DD:EE:01") // duplicate
	if r.NodeCount() != 1 {
		t.Errorf("expected 1 after duplicate, got %d", r.NodeCount())
	}
}

func TestMesh_SingleAppliance_OwnsEverything(t *testing.T) {
	m := NewMesh("AA:BB:CC:DD:EE:01", "test-site", 50051)
	for _, ip := range []string{"192.168.88.1", "10.0.0.1", "172.16.0.50"} {
		if !m.OwnsTarget(ip) {
			t.Errorf("single appliance should own %s", ip)
		}
	}
	if m.PeerCount() != 0 {
		t.Errorf("expected 0 peers, got %d", m.PeerCount())
	}
}

func TestMesh_UpdatePeers_AddsPeer(t *testing.T) {
	m := NewMesh("AA:BB:CC:DD:EE:01", "test-site", 50051)

	// Simulate discovering a peer with open gRPC port
	devices := []discoveredDevice{
		{IPAddress: "192.168.88.100", MACAddress: "AA:BB:CC:DD:EE:02", ProbeGRPC: true},
	}
	m.UpdatePeers(devices)

	if m.PeerCount() != 1 {
		t.Errorf("expected 1 peer, got %d", m.PeerCount())
	}

	// Self should no longer own everything
	ownCount := 0
	for i := 0; i < 256; i++ {
		if m.OwnsTarget(fmt.Sprintf("192.168.88.%d", i)) {
			ownCount++
		}
	}
	if ownCount == 256 {
		t.Error("with a peer, self should not own all 256 targets")
	}
	if ownCount == 0 {
		t.Error("self should still own some targets")
	}
}

func TestMesh_UpdatePeers_IgnoresNonGRPC(t *testing.T) {
	m := NewMesh("AA:BB:CC:DD:EE:01", "test-site", 50051)

	devices := []discoveredDevice{
		{IPAddress: "192.168.88.100", MACAddress: "AA:BB:CC:DD:EE:02", ProbeGRPC: false},
	}
	m.UpdatePeers(devices)

	if m.PeerCount() != 0 {
		t.Errorf("non-gRPC device should not be added as peer, got %d peers", m.PeerCount())
	}
}

func TestMesh_UpdateBackendPeers_CrossSubnet(t *testing.T) {
	m := NewMesh("AA:BB:CC:DD:EE:01", "test-site", 50051)

	// Simulate backend delivering a peer on a different subnet
	alwaysReachable := func(ip string, port int) bool { return true }
	m.UpdateBackendPeers([]MeshPeerInfo{
		{MAC: "AA:BB:CC:DD:EE:02", IPs: []string{"192.168.0.11"}},
	}, alwaysReachable)

	if m.PeerCount() != 1 {
		t.Errorf("expected 1 peer from backend, got %d", m.PeerCount())
	}

	// Targets should now be split
	ownCount := 0
	for i := 0; i < 256; i++ {
		if m.OwnsTarget(fmt.Sprintf("192.168.88.%d", i)) {
			ownCount++
		}
	}
	if ownCount == 256 {
		t.Error("with a backend peer, self should not own all targets")
	}
	if ownCount == 0 {
		t.Error("self should still own some targets")
	}
}

func TestMesh_UpdateBackendPeers_SkipsSelf(t *testing.T) {
	m := NewMesh("AA:BB:CC:DD:EE:01", "test-site", 50051)

	alwaysReachable := func(ip string, port int) bool { return true }
	m.UpdateBackendPeers([]MeshPeerInfo{
		{MAC: "AA:BB:CC:DD:EE:01", IPs: []string{"192.168.88.241"}}, // self
	}, alwaysReachable)

	if m.PeerCount() != 0 {
		t.Errorf("should not add self as peer, got %d", m.PeerCount())
	}
}

func TestMesh_UpdateBackendPeers_UnreachableSkipped(t *testing.T) {
	m := NewMesh("AA:BB:CC:DD:EE:01", "test-site", 50051)

	neverReachable := func(ip string, port int) bool { return false }
	m.UpdateBackendPeers([]MeshPeerInfo{
		{MAC: "AA:BB:CC:DD:EE:02", IPs: []string{"10.0.0.99"}},
	}, neverReachable)

	if m.PeerCount() != 0 {
		t.Errorf("unreachable peer should not be added, got %d", m.PeerCount())
	}
}

func TestMesh_UpdateBackendPeers_TriesMultipleIPs(t *testing.T) {
	m := NewMesh("AA:BB:CC:DD:EE:01", "test-site", 50051)

	// First IP unreachable, second reachable
	probed := []string{}
	selectiveProbe := func(ip string, port int) bool {
		probed = append(probed, ip)
		return ip == "10.100.0.2" // only WireGuard IP works
	}
	m.UpdateBackendPeers([]MeshPeerInfo{
		{MAC: "AA:BB:CC:DD:EE:02", IPs: []string{"192.168.0.11", "10.100.0.2"}},
	}, selectiveProbe)

	if m.PeerCount() != 1 {
		t.Errorf("should find peer via second IP, got %d peers", m.PeerCount())
	}
	if len(probed) != 2 {
		t.Errorf("should have probed both IPs, probed %d", len(probed))
	}
}

func TestMesh_UpdateBackendPeers_MergesWithARPPeers(t *testing.T) {
	m := NewMesh("AA:BB:CC:DD:EE:01", "test-site", 50051)

	// First: ARP discovers a peer on same subnet
	m.UpdatePeers([]discoveredDevice{
		{IPAddress: "192.168.88.100", MACAddress: "AA:BB:CC:DD:EE:02", ProbeGRPC: true},
	})
	if m.PeerCount() != 1 {
		t.Fatal("ARP peer should be added")
	}

	// Then: backend delivers a different peer on another subnet
	alwaysReachable := func(ip string, port int) bool { return true }
	m.UpdateBackendPeers([]MeshPeerInfo{
		{MAC: "AA:BB:CC:DD:EE:03", IPs: []string{"192.168.0.11"}},
	}, alwaysReachable)

	if m.PeerCount() != 2 {
		t.Errorf("should have 2 peers (1 ARP + 1 backend), got %d", m.PeerCount())
	}
}

func TestMesh_UpdateBackendPeers_ExpiresStaleBackendPeers(t *testing.T) {
	m := NewMesh("AA:BB:CC:DD:EE:01", "test-site", 50051)
	m.gracePeriod = 100 * time.Millisecond

	alwaysReachable := func(ip string, port int) bool { return true }

	// Add peer via backend
	m.UpdateBackendPeers([]MeshPeerInfo{
		{MAC: "AA:BB:CC:DD:EE:02", IPs: []string{"192.168.0.11"}},
	}, alwaysReachable)
	if m.PeerCount() != 1 {
		t.Fatal("backend peer should be added")
	}

	// Wait for grace to expire
	time.Sleep(150 * time.Millisecond)

	// Backend no longer delivers this peer (decommissioned)
	m.UpdateBackendPeers([]MeshPeerInfo{}, alwaysReachable)

	if m.PeerCount() != 0 {
		t.Errorf("stale backend peer should be expired, got %d peers", m.PeerCount())
	}
}

func TestMesh_Stats_AtomicSnapshot(t *testing.T) {
	m := NewMesh("AA:BB:CC:DD:EE:01", "test-site", 50051)

	alwaysReachable := func(ip string, port int) bool { return true }
	m.UpdateBackendPeers([]MeshPeerInfo{
		{MAC: "AA:BB:CC:DD:EE:02", IPs: []string{"192.168.0.11"}},
		{MAC: "AA:BB:CC:DD:EE:03", IPs: []string{"192.168.0.12"}},
	}, alwaysReachable)

	stats := m.Stats()
	if stats.PeerCount != 2 {
		t.Errorf("expected 2 peers, got %d", stats.PeerCount)
	}
	if stats.RingSize != 3 {
		t.Errorf("expected ring size 3, got %d", stats.RingSize)
	}
	if len(stats.PeerMACs) != 2 {
		t.Errorf("expected 2 peer MACs, got %d", len(stats.PeerMACs))
	}
}

func TestMesh_UpdateBackendPeers_EmptySlice(t *testing.T) {
	m := NewMesh("AA:BB:CC:DD:EE:01", "test-site", 50051)
	// Should be a no-op, no panic
	m.UpdateBackendPeers(nil, nil)
	m.UpdateBackendPeers([]MeshPeerInfo{}, nil)
	if m.PeerCount() != 0 {
		t.Errorf("empty updates should not add peers, got %d", m.PeerCount())
	}
}

func TestMesh_GracePeriod_KeepsPeerInRing(t *testing.T) {
	m := NewMesh("AA:BB:CC:DD:EE:01", "test-site", 50051)
	m.gracePeriod = 100 * time.Millisecond // short for testing

	// Add peer
	m.UpdatePeers([]discoveredDevice{
		{IPAddress: "192.168.88.100", MACAddress: "AA:BB:CC:DD:EE:02", ProbeGRPC: true},
	})
	if m.PeerCount() != 1 {
		t.Fatal("peer should be added")
	}

	// Update without the peer (simulates it disappearing)
	m.UpdatePeers([]discoveredDevice{})

	// Should still be in the ring (within grace period)
	if m.PeerCount() != 1 {
		t.Error("peer should remain during grace period")
	}

	// Wait for grace to expire
	time.Sleep(150 * time.Millisecond)
	m.UpdatePeers([]discoveredDevice{})

	if m.PeerCount() != 0 {
		t.Error("peer should be removed after grace period")
	}
}
