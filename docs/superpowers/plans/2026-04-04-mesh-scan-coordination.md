# Mesh Scan Coordination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Appliances on the same subnet autonomously divide scan targets using consistent hashing — no backend coordination, no duplicate scans.

**Architecture:** Appliances discover peers via ARP table + gRPC port probe. All peers form a consistent hash ring keyed by MAC. Each target IP is hashed onto the ring to determine its owner. 10-minute grace period before redistributing a lost peer's targets. Single appliance = ring of 1 = scans everything (backward compatible).

**Tech Stack:** Go, crypto/sha256, sort, net (TCP probe), existing netscan ARP discovery

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `appliance/internal/daemon/mesh.go` | Peer discovery, hash ring, OwnsTarget() |
| Create | `appliance/internal/daemon/mesh_test.go` | Unit tests for ring, ownership, grace period |
| Modify | `appliance/internal/daemon/driftscan.go` | Filter targets through mesh before scanning |
| Modify | `appliance/internal/daemon/netscan.go` | Filter device probes through mesh |
| Modify | `appliance/internal/daemon/daemon.go` | Initialize mesh, wire into scan loops |

---

### Task 1: Consistent hash ring + OwnsTarget

**Files:**
- Create: `appliance/internal/daemon/mesh.go`
- Create: `appliance/internal/daemon/mesh_test.go`

- [ ] **Step 1: Write mesh_test.go — ring basics**

```go
package daemon

import (
	"testing"
)

func TestHashRing_SingleNode_OwnsEverything(t *testing.T) {
	r := NewHashRing()
	r.AddNode("AA:BB:CC:DD:EE:01")

	// Single node must own every target
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

	// Both should own some targets (not 0 and not 256)
	if owned1 == 0 || owned2 == 0 {
		t.Errorf("unbalanced: node1=%d, node2=%d", owned1, owned2)
	}
	// Every target must be owned by exactly one node
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

	// Each node should own roughly 1/3 (allow 15-50% range for hash distribution)
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

	// Record ownership with 2 nodes
	ownerBefore := r.TargetOwner("192.168.88.50")

	// Remove one node
	r.RemoveNode("AA:BB:CC:DD:EE:02")

	// Node 1 should now own everything
	if !r.OwnsTarget("AA:BB:CC:DD:EE:01", "192.168.88.50") {
		t.Error("remaining node should own all targets after peer removal")
	}

	// Re-add node 2 — original owner should reclaim
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
```

- [ ] **Step 2: Write mesh.go — hash ring implementation**

```go
package daemon

import (
	"crypto/sha256"
	"encoding/binary"
	"fmt"
	"log"
	"net"
	"sort"
	"strings"
	"sync"
	"time"
)

// HashRing implements consistent hashing for target assignment across appliances.
// Each node is identified by its MAC address. Targets (IPs) are assigned to
// the nearest clockwise node on the ring.
type HashRing struct {
	mu       sync.RWMutex
	nodes    map[string]bool  // MAC → present
	ring     []ringEntry      // sorted by hash
	replicas int              // virtual nodes per physical node
}

type ringEntry struct {
	hash uint32
	mac  string
}

// NewHashRing creates a hash ring with 64 virtual nodes per physical node
// for reasonable distribution across 1-10 appliances.
func NewHashRing() *HashRing {
	return &HashRing{
		nodes:    make(map[string]bool),
		replicas: 64,
	}
}

// AddNode adds an appliance (by MAC) to the ring. Idempotent.
func (r *HashRing) AddNode(mac string) {
	mac = normalizeMACForRing(mac)
	r.mu.Lock()
	defer r.mu.Unlock()

	if r.nodes[mac] {
		return
	}
	r.nodes[mac] = true
	r.rebuild()
}

// RemoveNode removes an appliance from the ring. Its targets redistribute.
func (r *HashRing) RemoveNode(mac string) {
	mac = normalizeMACForRing(mac)
	r.mu.Lock()
	defer r.mu.Unlock()

	if !r.nodes[mac] {
		return
	}
	delete(r.nodes, mac)
	r.rebuild()
}

// OwnsTarget returns true if the given MAC is the owner of the target IP.
// Returns false if the ring is empty or the MAC is not in the ring.
func (r *HashRing) OwnsTarget(mac, targetIP string) bool {
	mac = normalizeMACForRing(mac)
	r.mu.RLock()
	defer r.mu.RUnlock()

	if len(r.ring) == 0 || !r.nodes[mac] {
		return false
	}

	return r.owner(targetIP) == mac
}

// TargetOwner returns the MAC of the node that owns the target IP.
// Returns empty string if the ring is empty.
func (r *HashRing) TargetOwner(targetIP string) string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.owner(targetIP)
}

// NodeCount returns the number of nodes in the ring.
func (r *HashRing) NodeCount() int {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return len(r.nodes)
}

// Nodes returns a copy of all node MACs in the ring.
func (r *HashRing) Nodes() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]string, 0, len(r.nodes))
	for mac := range r.nodes {
		out = append(out, mac)
	}
	return out
}

func (r *HashRing) owner(targetIP string) string {
	if len(r.ring) == 0 {
		return ""
	}
	h := hashKey(targetIP)
	idx := sort.Search(len(r.ring), func(i int) bool {
		return r.ring[i].hash >= h
	})
	if idx >= len(r.ring) {
		idx = 0 // wrap around
	}
	return r.ring[idx].mac
}

func (r *HashRing) rebuild() {
	r.ring = nil
	for mac := range r.nodes {
		for i := 0; i < r.replicas; i++ {
			key := fmt.Sprintf("%s:%d", mac, i)
			r.ring = append(r.ring, ringEntry{hash: hashKey(key), mac: mac})
		}
	}
	sort.Slice(r.ring, func(i, j int) bool {
		return r.ring[i].hash < r.ring[j].hash
	})
}

func hashKey(key string) uint32 {
	h := sha256.Sum256([]byte(key))
	return binary.BigEndian.Uint32(h[:4])
}

func normalizeMACForRing(mac string) string {
	return strings.ToUpper(strings.ReplaceAll(strings.ReplaceAll(mac, ":", ""), "-", ""))
}

// --- Mesh: peer discovery + ring management ---

// Mesh manages peer discovery and the consistent hash ring.
// It discovers sibling appliances on the LAN via ARP + gRPC port probe
// and maintains a ring for scan target assignment.
type Mesh struct {
	mu          sync.RWMutex
	selfMAC     string
	siteID      string
	grpcPort    int
	ring        *HashRing
	peers       map[string]*meshPeer // MAC → peer
	gracePeriod time.Duration
}

type meshPeer struct {
	MAC      string
	IP       string
	LastSeen time.Time
	Online   bool
}

// NewMesh creates a mesh with the local appliance as the initial (and possibly only) node.
func NewMesh(selfMAC, siteID string, grpcPort int) *Mesh {
	m := &Mesh{
		selfMAC:     normalizeMACForRing(selfMAC),
		siteID:      siteID,
		grpcPort:    grpcPort,
		ring:        NewHashRing(),
		peers:       make(map[string]*meshPeer),
		gracePeriod: 10 * time.Minute,
	}
	m.ring.AddNode(selfMAC)
	return m
}

// OwnsTarget returns true if this appliance should scan the given target IP.
// With a single appliance (no peers), always returns true.
func (m *Mesh) OwnsTarget(targetIP string) bool {
	return m.ring.OwnsTarget(m.selfMAC, targetIP)
}

// PeerCount returns the number of peers (excluding self).
func (m *Mesh) PeerCount() int {
	return m.ring.NodeCount() - 1
}

// UpdatePeers scans the ARP table for sibling appliances and updates the ring.
// Called from netscan cycle. Probes gRPC port to confirm peer identity.
func (m *Mesh) UpdatePeers(arpDevices []discoveredDevice) {
	m.mu.Lock()
	defer m.mu.Unlock()

	now := time.Now()
	seen := map[string]bool{m.selfMAC: true}

	for _, dev := range arpDevices {
		mac := normalizeMACForRing(dev.MAC)
		if mac == m.selfMAC {
			continue
		}

		// Probe gRPC port to confirm this is a sibling appliance
		if !dev.ProbeGRPC && !probePort(dev.IP, m.grpcPort, 2*time.Second) {
			continue
		}

		seen[mac] = true
		peer, exists := m.peers[mac]
		if !exists {
			peer = &meshPeer{MAC: mac, IP: dev.IP}
			m.peers[mac] = peer
			m.ring.AddNode(mac)
			log.Printf("[mesh] Peer discovered: %s at %s (ring size: %d)", mac, dev.IP, m.ring.NodeCount())
		}
		peer.LastSeen = now
		peer.IP = dev.IP
		peer.Online = true
	}

	// Expire peers not seen recently
	for mac, peer := range m.peers {
		if seen[mac] {
			continue
		}
		if peer.Online && now.Sub(peer.LastSeen) > m.gracePeriod {
			peer.Online = false
			m.ring.RemoveNode(mac)
			log.Printf("[mesh] Peer lost (grace expired): %s (ring size: %d)", mac, m.ring.NodeCount())
		}
	}
}

// probePort attempts a TCP connection to ip:port with a timeout.
func probePort(ip string, port int, timeout time.Duration) bool {
	addr := fmt.Sprintf("%s:%d", ip, port)
	conn, err := net.DialTimeout("tcp", addr, timeout)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}
```

- [ ] **Step 3: Add import for fmt in test file and run tests**

Run: `cd /Users/dad/Documents/Msp_Flakes/appliance && go test ./internal/daemon/ -run TestHashRing -v -count=1`
Expected: All 6 tests PASS

- [ ] **Step 4: Commit**

```bash
git add appliance/internal/daemon/mesh.go appliance/internal/daemon/mesh_test.go
git commit -m "feat: consistent hash ring + mesh peer discovery for multi-appliance scan coordination"
```

---

### Task 2: Wire mesh into daemon startup

**Files:**
- Modify: `appliance/internal/daemon/daemon.go`

- [ ] **Step 1: Add mesh field to Daemon struct and initialize in startup**

In the Daemon struct, add:
```go
mesh *Mesh
```

In the daemon startup (after config is loaded, MAC is known), initialize:
```go
if d.config.MACAddress != "" {
    d.mesh = NewMesh(d.config.MACAddress, d.config.SiteID, d.config.GRPCPort)
    log.Printf("[daemon] Mesh initialized: self=%s, site=%s", d.config.MACAddress, d.config.SiteID)
}
```

- [ ] **Step 2: Commit**

```bash
git add appliance/internal/daemon/daemon.go
git commit -m "feat: initialize mesh on daemon startup"
```

---

### Task 3: Feed ARP discoveries into mesh

**Files:**
- Modify: `appliance/internal/daemon/netscan.go`

- [ ] **Step 1: Add ProbeGRPC field to discoveredDevice**

In the `discoveredDevice` struct (netscan.go), add:
```go
ProbeGRPC bool
```

In the device probe loop (where ProbeSSH and ProbeWinRM are set), add gRPC probe:
```go
dev.ProbeGRPC = probePort(dev.IP, ns.svc.Config.GRPCPort, 2*time.Second)
```

- [ ] **Step 2: Call mesh.UpdatePeers after device discovery**

After `discoverARPDevices()` returns and probing is complete, add:
```go
if ns.svc.mesh != nil {
    ns.svc.mesh.UpdatePeers(devices)
}
```

- [ ] **Step 3: Commit**

```bash
git add appliance/internal/daemon/netscan.go
git commit -m "feat: feed ARP discoveries into mesh for peer detection"
```

---

### Task 4: Filter drift scan targets through mesh

**Files:**
- Modify: `appliance/internal/daemon/driftscan.go`

- [ ] **Step 1: Add mesh filter to Windows target loop**

In `scanWindowsTargets()`, after the target list is built but before scanning, filter:

```go
// Mesh filter: only scan targets this appliance owns
if ds.daemon.mesh != nil && ds.daemon.mesh.PeerCount() > 0 {
    var owned []scanTarget
    for _, t := range targets {
        ip := t.target.Hostname
        if ds.daemon.mesh.OwnsTarget(ip) {
            owned = append(owned, t)
        }
    }
    if len(owned) < len(targets) {
        log.Printf("[driftscan] Mesh filter: %d/%d Windows targets owned by this appliance", len(owned), len(targets))
    }
    targets = owned
}
```

- [ ] **Step 2: Add same filter to Linux target loop**

In `scanLinuxTargets()`, same pattern:

```go
// Mesh filter: only scan targets this appliance owns
if ds.daemon.mesh != nil && ds.daemon.mesh.PeerCount() > 0 {
    var owned []scanTarget
    for _, t := range targets {
        if ds.daemon.mesh.OwnsTarget(t.hostname) {
            owned = append(owned, t)
        }
    }
    if len(owned) < len(targets) {
        log.Printf("[linuxscan] Mesh filter: %d/%d Linux targets owned by this appliance", len(owned), len(targets))
    }
    targets = owned
}
```

- [ ] **Step 3: Run all tests**

Run: `cd /Users/dad/Documents/Msp_Flakes/appliance && go test ./... -count=1`
Expected: All 18 packages PASS

- [ ] **Step 4: Commit**

```bash
git add appliance/internal/daemon/driftscan.go
git commit -m "feat: drift scan filters targets through mesh — no duplicate scans across appliances"
```

---

### Task 5: Build, deploy, verify

- [ ] **Step 1: Build v0.3.78**

```bash
cd /Users/dad/Documents/Msp_Flakes/appliance && make build-linux VERSION=0.3.78
```

- [ ] **Step 2: Deploy via fleet order**

Upload binary to VPS with unique name, create fleet order for both appliances.

- [ ] **Step 3: Verify mesh discovery in logs**

After both appliances update to v0.3.78, check logs for:
- `[mesh] Peer discovered: <MAC> at <IP> (ring size: 2)`
- `[driftscan] Mesh filter: N/M Windows targets owned by this appliance`

- [ ] **Step 4: Verify no duplicate incidents**

Check that each target generates incidents from only one appliance, not both.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "verified: mesh scan coordination — 2 appliances, split targets, no duplicates"
```
