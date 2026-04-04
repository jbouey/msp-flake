package daemon

import (
	"crypto/sha256"
	"encoding/binary"
	"fmt"
	"log"
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
	nodes    map[string]bool // MAC → present
	ring     []ringEntry     // sorted by hash
	replicas int             // virtual nodes per physical node
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

// SelfMAC returns this appliance's MAC on the ring.
func (m *Mesh) SelfMAC() string {
	return m.selfMAC
}

// UpdatePeers scans the ARP table for sibling appliances and updates the ring.
// Called from netscan cycle. Probes gRPC port to confirm peer identity.
func (m *Mesh) UpdatePeers(arpDevices []discoveredDevice) {
	m.mu.Lock()
	defer m.mu.Unlock()

	now := time.Now()
	seen := map[string]bool{m.selfMAC: true}

	for _, dev := range arpDevices {
		mac := normalizeMACForRing(dev.MACAddress)
		if mac == m.selfMAC {
			continue
		}

		// Only consider devices with open gRPC port as potential peers
		if !dev.ProbeGRPC {
			continue
		}

		seen[mac] = true
		peer, exists := m.peers[mac]
		if !exists {
			peer = &meshPeer{MAC: mac, IP: dev.IPAddress}
			m.peers[mac] = peer
			m.ring.AddNode(mac)
			log.Printf("[mesh] Peer discovered: %s at %s (ring size: %d)", mac, dev.IPAddress, m.ring.NodeCount())
		}
		peer.LastSeen = now
		peer.IP = dev.IPAddress
		peer.Online = true
	}

	// Expire peers not seen recently (grace period prevents thrashing)
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
