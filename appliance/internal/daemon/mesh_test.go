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
