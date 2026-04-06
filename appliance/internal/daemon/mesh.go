package daemon

import (
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
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
	caCertPool  *x509.CertPool       // for TLS-verified peer probes (nil = TCP fallback)

	// Server-authoritative target assignment (Hybrid C+)
	serverTargets        []string  // IPs this appliance should scan
	serverEpoch          int64     // assignment epoch from backend
	serverAssignmentTime time.Time // when assignment was received
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
// Prefers server-authoritative assignment if recent (< 15 min).
// Falls back to local hash ring if no server assignment or stale.
func (m *Mesh) OwnsTarget(targetIP string) bool {
	m.mu.RLock()
	defer m.mu.RUnlock()

	// Server-authoritative: use if we have a recent assignment
	if m.serverTargets != nil && time.Since(m.serverAssignmentTime) < 15*time.Minute {
		for _, ip := range m.serverTargets {
			if ip == targetIP {
				return true
			}
		}
		return false
	}

	// Fallback: local ring (no server assignment or stale)
	return m.ring.OwnsTarget(m.selfMAC, targetIP)
}

// ApplyTargetAssignment stores server-authoritative target list from checkin response.
// ringMembers updates the local ring for failover consistency.
func (m *Mesh) ApplyTargetAssignment(targets []string, ringMembers []string, epoch int64) {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.serverTargets = targets
	m.serverEpoch = epoch
	m.serverAssignmentTime = time.Now()

	// Sync ring to match server's view for failover consistency
	for _, mac := range ringMembers {
		m.ring.AddNode(mac)
	}

	log.Printf("[mesh] Server assignment applied: %d targets, epoch=%d, ring=%d nodes",
		len(targets), epoch, m.ring.NodeCount())
}

// PeerCount returns the number of peers (excluding self).
func (m *Mesh) PeerCount() int {
	n := m.ring.NodeCount() - 1
	if n < 0 {
		return 0
	}
	return n
}

// SelfMAC returns this appliance's MAC on the ring.
func (m *Mesh) SelfMAC() string {
	return m.selfMAC
}

// MeshStats holds an atomic snapshot of mesh state for telemetry.
type MeshStats struct {
	PeerCount int
	RingSize  int
	PeerMACs  []string
}

// Stats returns an atomic snapshot of mesh state under a single lock acquisition.
// Prevents inconsistency between peer count and MAC list.
func (m *Mesh) Stats() MeshStats {
	m.mu.RLock()
	defer m.mu.RUnlock()

	nodes := m.ring.Nodes()
	var peerMACs []string
	for _, mac := range nodes {
		if mac != m.selfMAC {
			peerMACs = append(peerMACs, mac)
		}
	}
	ringSize := len(nodes)
	peerCount := ringSize - 1
	if peerCount < 0 {
		peerCount = 0
	}
	return MeshStats{
		PeerCount: peerCount,
		RingSize:  ringSize,
		PeerMACs:  peerMACs,
	}
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

// SetCACertPool sets the CA certificate pool for TLS-verified peer probes.
// Called once at startup after CA initialization.
func (m *Mesh) SetCACertPool(pool *x509.CertPool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.caCertPool = pool
}

// peerProbeResult holds the result of probing a single peer (used for parallel probing).
type peerProbeResult struct {
	MAC string
	IP  string // empty if unreachable
}

// UpdateBackendPeers integrates sibling appliance info delivered by Central Command.
// This enables mesh target splitting across subnets where ARP discovery can't reach.
// Each peer's gRPC port is probed to confirm liveness before adding to the ring.
// Peers NOT in the current delivery AND past grace period are expired (prevents black holes).
// Probes run concurrently (one goroutine per peer) to cap latency at max(IPs)*2s.
// The probeFunc parameter allows test injection; pass nil for production (TLS with CA fallback to TCP).
func (m *Mesh) UpdateBackendPeers(peers []MeshPeerInfo, probeFunc func(ip string, port int) bool) {
	probe := probeFunc
	if probe == nil {
		probe = m.defaultProbe()
	}

	// Probe all peers concurrently (I/O bound — network dials)
	results := make([]peerProbeResult, len(peers))
	var wg sync.WaitGroup
	for i, p := range peers {
		mac := normalizeMACForRing(p.MAC)
		if mac == "" || mac == m.selfMAC {
			continue
		}
		wg.Add(1)
		go func(idx int, mac string, ips []string) {
			defer wg.Done()
			for _, ip := range ips {
				if probe(ip, m.grpcPort) {
					results[idx] = peerProbeResult{MAC: mac, IP: ip}
					return
				}
			}
			results[idx] = peerProbeResult{MAC: mac} // unreachable
		}(i, mac, p.IPs)
	}
	wg.Wait()

	m.mu.Lock()
	defer m.mu.Unlock()

	now := time.Now()
	delivered := map[string]bool{m.selfMAC: true}

	for _, r := range results {
		if r.MAC == "" {
			continue
		}
		delivered[r.MAC] = true

		if r.IP == "" {
			// Unreachable — don't add, but mark as delivered so expiry logic
			// knows the backend still considers this peer active.
			continue
		}

		peer, exists := m.peers[r.MAC]
		if !exists {
			peer = &meshPeer{MAC: r.MAC, IP: r.IP}
			m.peers[r.MAC] = peer
			m.ring.AddNode(r.MAC)
			log.Printf("[mesh] Backend peer discovered: %s at %s (cross-subnet, ring size: %d)", r.MAC, r.IP, m.ring.NodeCount())
		}
		peer.LastSeen = now
		peer.IP = r.IP
		peer.Online = true
	}

	// Expire peers NOT in ARP seen set AND NOT in backend delivery AND past grace.
	// This prevents permanent black holes when a cross-subnet peer is decommissioned.
	for mac, peer := range m.peers {
		if delivered[mac] {
			continue
		}
		if peer.Online && now.Sub(peer.LastSeen) > m.gracePeriod {
			peer.Online = false
			m.ring.RemoveNode(mac)
			log.Printf("[mesh] Backend peer expired (not in delivery, grace elapsed): %s (ring size: %d)", mac, m.ring.NodeCount())
		}
	}
}

// defaultProbe returns the production probe function.
// Uses TLS with CA verification if available, falls back to TCP.
func (m *Mesh) defaultProbe() func(ip string, port int) bool {
	m.mu.RLock()
	caPool := m.caCertPool
	m.mu.RUnlock()

	return func(ip string, port int) bool {
		addr := fmt.Sprintf("%s:%d", ip, port)

		// Prefer TLS with CA verification (confirms peer is a sibling appliance)
		if caPool != nil {
			conn, err := tls.DialWithDialer(
				&net.Dialer{Timeout: 2 * time.Second},
				"tcp", addr,
				&tls.Config{
					RootCAs:            caPool,
					InsecureSkipVerify: false,
					ServerName:         ip, // gRPC server cert has IP SAN
				},
			)
			if err == nil {
				conn.Close()
				return true
			}
			// TLS failed — could be cert mismatch (different CA) or network error.
			// Fall through to TCP for backward compat with pre-TLS peers.
		}

		// TCP fallback (for appliances running older firmware without TLS on gRPC)
		conn, err := net.DialTimeout("tcp", addr, 2*time.Second)
		if err != nil {
			return false
		}
		conn.Close()
		return true
	}
}
