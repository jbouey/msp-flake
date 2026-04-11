package daemon

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync/atomic"
	"time"

	"github.com/osiriscare/appliance/internal/phiscrub"
)

// ErrAuthFailed is returned when the server responds with 401 (API key mismatch).
// The daemon uses this to trigger the auto-rekey flow.
var ErrAuthFailed = errors.New("authentication failed")

// PhoneHomeClient handles communication with Central Command.
type PhoneHomeClient struct {
	config                 *Config
	client                 *http.Client
	consecutiveFailures    atomic.Int32
	maxFailuresBeforeReset int
}

// NewPhoneHomeClient creates a new client for Central Command checkin.
//
// TLS pinning modes (in priority order):
//  1. SPKI pin (config): If TLSPinHash is set, the SHA-256 of the server's Subject Public
//     Key Info (SPKI) is verified on every connection. This survives certificate renewals
//     (the public key stays the same) and protects against CA compromise.
//  2. TOFU pin (fallback): On the first successful TLS connection the server's leaf
//     certificate SHA-256 fingerprint is saved to {StateDir}/server_cert_pin.hex.
//     Subsequent connections verify the fingerprint matches.
func NewPhoneHomeClient(cfg *Config) *PhoneHomeClient {
	pinPath := filepath.Join(cfg.StateDir, "server_cert_pin.hex")
	spkiPinHash := cfg.TLSPinHash // empty string = not configured

	transport := &http.Transport{
		TLSClientConfig: &tls.Config{
			MinVersion: tls.VersionTLS12,
			VerifyPeerCertificate: func(rawCerts [][]byte, verifiedChains [][]*x509.Certificate) error {
				if len(rawCerts) == 0 {
					return fmt.Errorf("no certificates presented")
				}

				// Mode 1: SPKI public key pinning (build-time/config-time hash)
				if spkiPinHash != "" {
					for _, chain := range verifiedChains {
						for _, cert := range chain {
							pubKeyBytes, err := x509.MarshalPKIXPublicKey(cert.PublicKey)
							if err != nil {
								continue
							}
							hash := sha256.Sum256(pubKeyBytes)
							if hex.EncodeToString(hash[:]) == spkiPinHash {
								return nil // SPKI pin matches
							}
						}
					}
					return fmt.Errorf("TLS public key does not match pinned SPKI hash %s... — possible MITM attack", spkiPinHash[:min(16, len(spkiPinHash))])
				}

				// Mode 2: TOFU certificate fingerprint pinning (fallback)
				fingerprint := sha256.Sum256(rawCerts[0])
				fpHex := hex.EncodeToString(fingerprint[:])

				pinData, err := os.ReadFile(pinPath)
				if err != nil {
					// First connection: save pin (TOFU)
					if mkErr := os.MkdirAll(filepath.Dir(pinPath), 0700); mkErr != nil {
						slog.Warn("could not create pin directory", "component", "tls-pin", "error", mkErr)
					}
					if wErr := os.WriteFile(pinPath, []byte(fpHex), 0600); wErr != nil {
						slog.Warn("could not save pin file", "component", "tls-pin", "error", wErr)
					}
					slog.Info("TOFU: pinned server certificate fingerprint", "component", "tls-pin", "fingerprint", fpHex[:16])
					return nil
				}

				savedFP := strings.TrimSpace(string(pinData))
				if savedFP != fpHex {
					return fmt.Errorf("TLS certificate fingerprint mismatch! Expected %s..., got %s... — possible MITM attack",
						savedFP[:16], fpHex[:16])
				}
				return nil
			},
		},
		MaxIdleConns:        5,
		IdleConnTimeout:     90 * time.Second,
		TLSHandshakeTimeout: 10 * time.Second,
	}

	if spkiPinHash != "" {
		slog.Info("SPKI public key pinning enabled", "component", "tls-pin", "hash_prefix", spkiPinHash[:min(16, len(spkiPinHash))])
	}

	return &PhoneHomeClient{
		config: cfg,
		client: &http.Client{
			Timeout:   30 * time.Second,
			Transport: transport,
		},
		maxFailuresBeforeReset: 3,
	}
}

// RecreateClient rebuilds the HTTP client with a fresh transport to recover
// from stuck connection pools or stale TLS state. Clears the TOFU pin so the
// next connection re-pins (handles cert rotation from Let's Encrypt renewals).
func (c *PhoneHomeClient) RecreateClient() {
	slog.Warn("recreating HTTP client — clearing TLS pin for re-TOFU", "component", "phonehome", "consecutive_failures", c.consecutiveFailures.Load())
	c.client.CloseIdleConnections()
	// Delete stale TOFU pin so NewPhoneHomeClient will re-pin on next connection
	pinPath := filepath.Join(c.config.StateDir, "server_cert_pin.hex")
	if err := os.Remove(pinPath); err != nil && !os.IsNotExist(err) {
		slog.Warn("could not remove stale pin file", "component", "phonehome", "error", err)
	}
	fresh := NewPhoneHomeClient(c.config)
	c.client = fresh.client
	c.consecutiveFailures.Store(0)
}

// ConsecutiveFailures returns the current failure count.
func (c *PhoneHomeClient) ConsecutiveFailures() int {
	return int(c.consecutiveFailures.Load())
}

// FetchPendingOrders polls the fleet order fallback endpoint when checkin is broken.
func (c *PhoneHomeClient) FetchPendingOrders(ctx context.Context, applianceID string) ([]map[string]interface{}, error) {
	url := strings.TrimRight(c.config.APIEndpoint, "/") + "/api/fleet/orders/pending?appliance_id=" + applianceID

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}
	httpReq.Header.Set("Authorization", "Bearer "+c.config.APIKey)
	httpReq.Header.Set("User-Agent", "OsirisCare-Appliance/Go")

	resp, err := c.client.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("fetch orders: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(io.LimitReader(resp.Body, 10<<20))
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("server returned %d: %s", resp.StatusCode, string(body))
	}

	var result struct {
		Orders []map[string]interface{} `json:"orders"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("parse response: %w", err)
	}
	return result.Orders, nil
}

// PendingDeploy describes a device that needs agent deployment.
type PendingDeploy struct {
	DeviceID       string `json:"device_id"`
	IPAddress      string `json:"ip_address"`
	Hostname       string `json:"hostname"`
	OSType         string `json:"os_type"`
	DeployMethod   string `json:"deploy_method"` // "ssh" or "winrm"
	Username       string `json:"username"`
	Password       string `json:"password"`
	SSHKey         string `json:"ssh_key,omitempty"`
	AgentBinaryURL string `json:"agent_binary_url"`
}

// DeployResult reports the outcome of a deployment attempt.
type DeployResult struct {
	DeviceID string `json:"device_id"`
	Hostname string `json:"hostname,omitempty"`
	OSType   string `json:"os_type,omitempty"`
	Status   string `json:"status"` // "success" or "failed"
	AgentID  string `json:"agent_id,omitempty"`
	Error    string `json:"error,omitempty"`
}

// CheckinRequest is the payload sent to Central Command.
type CheckinRequest struct {
	SiteID              string           `json:"site_id"`
	Hostname            string           `json:"hostname"`
	MACAddress          string           `json:"mac_address"`
	AllMACAddresses     []string         `json:"all_mac_addresses,omitempty"`
	IPAddresses         []string         `json:"ip_addresses"`
	UptimeSeconds       int              `json:"uptime_seconds"`
	AgentVersion        string           `json:"agent_version"`
	NixOSVersion        string           `json:"nixos_version"`
	HasLocalCredentials bool             `json:"has_local_credentials"`
	AgentPublicKey      string           `json:"agent_public_key,omitempty"`
	BootSource          string           `json:"boot_source,omitempty"`
	WgPubKey            string           `json:"wg_pubkey,omitempty"`
	WgConnected         bool             `json:"wg_connected,omitempty"`
	WgIP                string           `json:"wg_ip,omitempty"`
	ConnectedAgents     []ConnectedAgent        `json:"connected_agents,omitempty"`
	DiscoveryResults    map[string]interface{}   `json:"discovery_results,omitempty"`
	EncryptionPublicKey string                   `json:"encryption_public_key,omitempty"`
	DeployResults       []DeployResult           `json:"deploy_results,omitempty"`
	DaemonHealth        *DaemonHealth            `json:"daemon_health,omitempty"`
	// Peer witnessing: recent bundle hashes for sibling appliances to counter-sign
	BundleHashes        []BundleHashEntry        `json:"bundle_hashes,omitempty"`
	// Witness attestations: counter-signatures of sibling bundle hashes from previous cycle
	WitnessAttestations []WitnessAttestation     `json:"witness_attestations,omitempty"`
}

// BundleHashEntry is a recent evidence bundle hash for peer witnessing.
type BundleHashEntry struct {
	BundleID   string `json:"bundle_id"`
	BundleHash string `json:"bundle_hash"`
	CheckedAt  string `json:"checked_at"`
}

// WitnessAttestation is this appliance's counter-signature of a sibling's bundle hash.
type WitnessAttestation struct {
	BundleID         string `json:"bundle_id"`
	BundleHash       string `json:"bundle_hash"`
	WitnessSignature string `json:"witness_signature"` // Ed25519 signature of the hash
	WitnessPublicKey string `json:"witness_public_key"`
	SourceAppliance  string `json:"source_appliance"` // who created the bundle
}

// DaemonHealth reports runtime stats from the Go daemon.
// Uses Go's stdlib runtime package — zero external dependencies.
type DaemonHealth struct {
	Goroutines    int     `json:"goroutines"`
	HeapAllocMB   float64 `json:"heap_alloc_mb"`
	HeapSysMB     float64 `json:"heap_sys_mb"`
	GCPauseMs     float64 `json:"gc_pause_ms"`       // last GC pause duration
	GCCycles      uint32  `json:"gc_cycles"`
	NumCPU        int     `json:"num_cpu"`
	UptimeSeconds int64   `json:"uptime_seconds_daemon"`
	// Mesh coordination stats
	MeshPeerCount int      `json:"mesh_peer_count"`
	MeshRingSize  int      `json:"mesh_ring_size"`  // total nodes including self
	MeshPeerMACs    []string `json:"mesh_peer_macs,omitempty"`
	// WireGuard access state — auditable proof of tunnel status
	WgAccessState   string   `json:"wg_access_state"`    // "off", "active", "bootstrap"
	WgAccessExpires string   `json:"wg_access_expires,omitempty"` // ISO8601 if active
}

// ConnectedAgent represents a Go agent connected to this appliance via gRPC.
type ConnectedAgent struct {
	AgentID       string `json:"agent_id"`
	Hostname      string `json:"hostname"`
	AgentVersion  string `json:"agent_version,omitempty"`
	IPAddress     string `json:"ip_address,omitempty"`
	OSVersion     string `json:"os_version,omitempty"`
	Tier          int    `json:"capability_tier"`
	ConnectedAt   string `json:"connected_at"`
	LastHeartbeat string `json:"last_heartbeat"`
	DriftCount    int64  `json:"drift_count"`
	ChecksPassed  int64  `json:"checks_passed"`
	ChecksTotal   int64  `json:"checks_total"`
}

// collectDaemonHealth reads Go runtime stats — zero external dependencies.
// mesh may be nil for single-appliance deployments.
func collectDaemonHealth(startTime time.Time, mesh *Mesh) *DaemonHealth {
	var m runtime.MemStats
	runtime.ReadMemStats(&m)
	var lastPauseMs float64
	if m.NumGC > 0 {
		lastPauseMs = float64(m.PauseNs[(m.NumGC+255)%256]) / 1e6
	}
	h := &DaemonHealth{
		Goroutines:    runtime.NumGoroutine(),
		HeapAllocMB:   float64(m.HeapAlloc) / (1024 * 1024),
		HeapSysMB:     float64(m.HeapSys) / (1024 * 1024),
		GCPauseMs:     lastPauseMs,
		GCCycles:      m.NumGC,
		NumCPU:        runtime.NumCPU(),
		UptimeSeconds: int64(time.Since(startTime).Seconds()),
	}
	if mesh != nil {
		stats := mesh.Stats()
		h.MeshPeerCount = stats.PeerCount
		h.MeshRingSize = stats.RingSize
		h.MeshPeerMACs = stats.PeerMACs
	}
	// WireGuard access state — determine by checking if wg0 interface exists
	if wgIP := getWireGuardIP(); wgIP != "" {
		h.WgAccessState = "active"
		// Check if there's a systemd timer that will auto-expire it
		if out, err := exec.Command("systemctl", "is-active", "msp-emergency-wg-expire.timer").Output(); err == nil && strings.TrimSpace(string(out)) == "active" {
			// Timer is running — get the time left
			if left, err := exec.Command("systemctl", "show", "-p", "NextElapseUSecRealtime", "--value", "msp-emergency-wg-expire.timer").Output(); err == nil {
				h.WgAccessExpires = strings.TrimSpace(string(left))
			}
		}
	} else {
		h.WgAccessState = "off"
	}
	return h
}

// WireguardConfig holds the hub-side WireGuard parameters delivered by Central Command.
type WireguardConfig struct {
	HubPubKey   string `json:"hub_pubkey"`
	HubEndpoint string `json:"hub_endpoint"`
	MyIP        string `json:"my_ip"`
}

// CheckinResponse is what Central Command returns.
type CheckinResponse struct {
	Status               string                   `json:"status"`
	ApplianceID          string                   `json:"appliance_id"`
	ServerTime           string                   `json:"server_time"`
	RotatedAPIKey        string                   `json:"rotated_api_key,omitempty"`
	ServerPublicKey      string                   `json:"server_public_key"`
	ServerPublicKeys     []string                 `json:"server_public_keys,omitempty"`
	MergedDuplicates     int                      `json:"merged_duplicates"`
	PendingOrders        []map[string]interface{}  `json:"pending_orders"`
	WindowsTargets       []map[string]interface{}  `json:"windows_targets"`
	LinuxTargets         []map[string]interface{}  `json:"linux_targets"`
	EnabledRunbooks      []string                 `json:"enabled_runbooks"`
	TriggerEnumeration   bool                     `json:"trigger_enumeration"`
	TriggerImmediateScan bool                     `json:"trigger_immediate_scan"`
	L2Mode               string                   `json:"l2_mode"`
	SubscriptionStatus   string                   `json:"subscription_status"`
	DisabledChecks       []string                 `json:"disabled_checks"`
	EncryptedCredentials map[string]interface{}   `json:"encrypted_credentials,omitempty"`
	PendingDeploys       []PendingDeploy          `json:"pending_deploys,omitempty"`
	Wireguard            *WireguardConfig         `json:"wireguard,omitempty"`
	// Peer witnessing: sibling appliance bundle hashes to counter-sign
	PeerBundleHashes     []PeerBundleHash         `json:"peer_bundle_hashes,omitempty"`
	// Mesh: sibling appliance IPs+MACs for cross-subnet peer discovery
	MeshPeers            []MeshPeerInfo           `json:"mesh_peers,omitempty"`
	// Server-authoritative target assignment (Hybrid C+)
	TargetAssignments *TargetAssignment `json:"target_assignments,omitempty"`
}

// MeshPeerInfo is a sibling appliance's identity delivered by Central Command
// for cross-subnet mesh peer discovery (ARP only works on same L2 segment).
type MeshPeerInfo struct {
	MAC string   `json:"mac"`
	IPs []string `json:"ips"`
}

// TargetAssignment is the server-authoritative scan target list.
type TargetAssignment struct {
	YourTargets     []string `json:"your_targets"`
	RingMembers     []string `json:"ring_members"`
	AssignmentEpoch int64    `json:"assignment_epoch"`
}

// PeerBundleHash is a sibling appliance's bundle hash delivered for witnessing.
type PeerBundleHash struct {
	BundleID        string `json:"bundle_id"`
	BundleHash      string `json:"bundle_hash"`
	SourceAppliance string `json:"source_appliance"` // appliance_id that created it
	SourcePublicKey string `json:"source_public_key"`
}

// Checkin sends a phone-home checkin to Central Command.
func (c *PhoneHomeClient) Checkin(ctx context.Context, req *CheckinRequest) (*CheckinResponse, error) {
	body, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal checkin: %w", err)
	}

	url := strings.TrimRight(c.config.APIEndpoint, "/") + "/api/appliances/checkin"

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}

	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+c.config.APIKey)
	httpReq.Header.Set("User-Agent", "OsirisCare-Appliance/Go")

	resp, err := c.client.Do(httpReq)
	if err != nil {
		if c.consecutiveFailures.Add(1) >= int32(c.maxFailuresBeforeReset) {
			c.RecreateClient()
		}
		return nil, fmt.Errorf("checkin request: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10<<20))
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		if c.consecutiveFailures.Add(1) >= int32(c.maxFailuresBeforeReset) {
			c.RecreateClient()
		}
		if resp.StatusCode == http.StatusUnauthorized {
			return nil, fmt.Errorf("%w: checkin returned %d: %s", ErrAuthFailed, resp.StatusCode, string(respBody))
		}
		return nil, fmt.Errorf("checkin returned %d: %s", resp.StatusCode, string(respBody))
	}

	var result CheckinResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("parse response: %w", err)
	}

	c.consecutiveFailures.Store(0)
	return &result, nil
}

// deviceSyncPayload is the JSON body sent to POST /api/devices/sync.
type deviceSyncPayload struct {
	ApplianceID      string                   `json:"appliance_id"`
	SiteID           string                   `json:"site_id"`
	ScanTimestamp    string                   `json:"scan_timestamp"`
	Devices          []deviceSyncEntry        `json:"devices"`
	TotalDevices     int                      `json:"total_devices"`
	MonitoredDevices int                      `json:"monitored_devices"`
	ExcludedDevices  int                      `json:"excluded_devices"`
	MedicalDevices   int                      `json:"medical_devices"`
	ComplianceRate   float64                  `json:"compliance_rate"`
	IPChanges        []ipChange               `json:"ip_changes,omitempty"`
}

// deviceSyncEntry is one device in the sync payload.
type deviceSyncEntry struct {
	DeviceID        string `json:"device_id"`
	Hostname        string `json:"hostname,omitempty"`
	IPAddress       string `json:"ip_address"`
	MACAddress      string `json:"mac_address,omitempty"`
	DeviceType      string `json:"device_type"`
	OSName          string `json:"os_name,omitempty"`
	ComplianceStatus string `json:"compliance_status"`
	DiscoverySource string `json:"discovery_source"`
	FirstSeenAt     string `json:"first_seen_at"`
	LastSeenAt      string `json:"last_seen_at"`
	OpenPorts       []int  `json:"open_ports"`

	// Probe fields
	OSFingerprint   string `json:"os_fingerprint,omitempty"`
	Distro          string `json:"distro,omitempty"`
	ProbeSSH        *bool  `json:"probe_ssh,omitempty"`
	ProbeWinRM      *bool  `json:"probe_winrm,omitempty"`
	ADJoined        *bool  `json:"ad_joined,omitempty"`
}

// SyncDevices sends discovered device inventory to Central Command.
func (c *PhoneHomeClient) SyncDevices(ctx context.Context, payload *deviceSyncPayload) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal device sync: %w", err)
	}

	url := strings.TrimRight(c.config.APIEndpoint, "/") + "/api/devices/sync"

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}

	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+c.config.APIKey)
	httpReq.Header.Set("User-Agent", "OsirisCare-Appliance/Go")

	resp, err := c.client.Do(httpReq)
	if err != nil {
		return fmt.Errorf("device sync request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 10<<20))
		return fmt.Errorf("device sync returned %d: %s", resp.StatusCode, string(respBody))
	}

	return nil
}

// classifyConnectivityError returns a human-readable classification of why a checkin failed.
func classifyConnectivityError(err error) string {
	if err == nil {
		return "ok"
	}
	msg := err.Error()

	// DNS resolution failure → likely no network or DNS misconfigured
	var dnsErr *net.DNSError
	if errors.As(err, &dnsErr) {
		if dnsErr.IsNotFound {
			return "dns_not_found"
		}
		return "dns_error"
	}

	// Connection refused → server down but network is up
	var opErr *net.OpError
	if errors.As(err, &opErr) {
		if opErr.Op == "dial" {
			if strings.Contains(msg, "connection refused") {
				return "server_down"
			}
			if strings.Contains(msg, "no route to host") || strings.Contains(msg, "network is unreachable") {
				return "network_down"
			}
		}
	}

	// Timeout
	if os.IsTimeout(err) || strings.Contains(msg, "deadline exceeded") || strings.Contains(msg, "context deadline") {
		return "timeout"
	}

	// TLS errors
	if strings.Contains(msg, "tls:") || strings.Contains(msg, "certificate") {
		return "tls_error"
	}

	// Auth failure (401)
	if errors.Is(err, ErrAuthFailed) || strings.Contains(msg, "returned 401") {
		return "auth_failed"
	}

	// HTTP status codes (embedded in error message)
	if strings.Contains(msg, "returned 5") {
		return "server_error"
	}

	return "unknown"
}

// rekeyRequest is the JSON body for POST /api/provision/rekey.
type rekeyRequest struct {
	SiteID     string `json:"site_id"`
	MACAddress string `json:"mac_address"`
	Hostname   string `json:"hostname,omitempty"`
	HardwareID string `json:"hardware_id,omitempty"`
}

// rekeyResponse is the JSON response from /api/provision/rekey.
type rekeyResponse struct {
	Status      string `json:"status"`
	APIKey      string `json:"api_key"`
	ApplianceID string `json:"appliance_id"`
}

// RequestRekey calls the rekey endpoint to get a new API key.
// This is used when the daemon detects persistent 401 auth failures.
func (c *PhoneHomeClient) RequestRekey(ctx context.Context) (*rekeyResponse, error) {
	hostname := getHostname()
	mac := getMACAddress()
	hwID := getHardwareID()

	reqBody := rekeyRequest{
		SiteID:     c.config.SiteID,
		MACAddress: mac,
		Hostname:   hostname,
		HardwareID: hwID,
	}

	body, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("marshal rekey request: %w", err)
	}

	url := strings.TrimRight(c.config.APIEndpoint, "/") + "/api/provision/rekey"
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("create rekey request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("User-Agent", "OsirisCare-Appliance/Go")

	resp, err := c.client.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("rekey request: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10<<20))
	if err != nil {
		return nil, fmt.Errorf("read rekey response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("rekey returned %d: %s", resp.StatusCode, string(respBody))
	}

	var result rekeyResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("parse rekey response: %w", err)
	}
	return &result, nil
}

// getHardwareID reads the SMBIOS/DMI product UUID.
func getHardwareID() string {
	data, err := os.ReadFile("/sys/class/dmi/id/product_uuid")
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

// SystemInfo gathers system information for the checkin request.
// Hostname is PHI-scrubbed to remove patient-identifying patterns.
// Infrastructure identifiers (site_id, IPs, MACs, version, WG key) are NOT scrubbed.
func SystemInfo(cfg *Config, version string) CheckinRequest {
	hostname := phiscrub.Scrub(getHostname())
	mac := getMACAddress()
	ips := getIPAddresses()
	uptime := getUptimeSeconds()
	nixVer := getNixOSVersion()
	wgPub := getWireGuardPubKey()

	return CheckinRequest{
		SiteID:          cfg.SiteID,
		Hostname:        hostname,
		MACAddress:      mac,
		AllMACAddresses: getAllPhysicalMACs(),
		BootSource:      detectBootSource(),
		IPAddresses:     ips,
		UptimeSeconds:   uptime,
		AgentVersion:    version,
		NixOSVersion:    nixVer,
		WgPubKey:        wgPub,
	}
}

// SystemInfoWithKey returns a checkin request that includes the agent public key.
func SystemInfoWithKey(cfg *Config, version, pubKeyHex string) CheckinRequest {
	req := SystemInfo(cfg, version)
	req.AgentPublicKey = pubKeyHex
	return req
}

func getHostname() string {
	h, err := os.Hostname()
	if err != nil {
		return "unknown"
	}
	return h
}

// detectBootSource determines if the system is running from a live USB installer
// or from an installed disk. This prevents the ghost-registration bug where an
// appliance registers from the live USB environment but the install never completes.
//
// Detection: NixOS live ISOs mount root as tmpfs with a squashfs nix store.
// Installed systems have a real disk partition (ext4/btrfs) at /.
func detectBootSource() string {
	// Check /proc/cmdline for live ISO indicators
	cmdline, err := os.ReadFile("/proc/cmdline")
	if err == nil {
		cmd := string(cmdline)
		if strings.Contains(cmd, "boot.shell_on_fail") ||
			strings.Contains(cmd, "copytoram") ||
			strings.Contains(cmd, "squashfs") {
			return "live_usb"
		}
	}

	// Check if root filesystem is tmpfs (live ISO) vs real disk
	mounts, err := os.ReadFile("/proc/mounts")
	if err == nil {
		for _, line := range strings.Split(string(mounts), "\n") {
			fields := strings.Fields(line)
			if len(fields) >= 3 && fields[1] == "/" {
				fstype := fields[2]
				if fstype == "tmpfs" || fstype == "ramfs" || fstype == "squashfs" {
					return "live_usb"
				}
				return "installed_disk"
			}
		}
	}

	// Check for the MSP-DATA partition label (created by installer)
	if _, err := os.Stat("/dev/disk/by-label/MSP-DATA"); err == nil {
		return "installed_disk"
	}

	return "unknown"
}

// getAllPhysicalMACs returns all non-loopback, non-virtual MAC addresses
// sorted alphabetically by interface name for deterministic ordering.
// This prevents the ghost-appliance bug where non-deterministic interface
// enumeration causes a multi-NIC machine to register as multiple appliances.
func getAllPhysicalMACs() []string {
	ifaces, err := net.Interfaces()
	if err != nil {
		return nil
	}
	// Sort by interface name for deterministic ordering across boots
	sort.Slice(ifaces, func(i, j int) bool {
		return ifaces[i].Name < ifaces[j].Name
	})
	var macs []string
	for _, iface := range ifaces {
		if iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		mac := strings.ToUpper(iface.HardwareAddr.String())
		if mac == "" || strings.HasPrefix(mac, "00:00:00") {
			continue
		}
		// Skip virtual/tunnel interfaces (WireGuard, docker, veth)
		name := iface.Name
		if strings.HasPrefix(name, "wg") || strings.HasPrefix(name, "docker") ||
			strings.HasPrefix(name, "veth") || strings.HasPrefix(name, "br-") {
			continue
		}
		macs = append(macs, mac)
	}
	return macs
}

// getMACAddress returns the primary MAC — first physical NIC sorted by name.
// Deterministic: always returns the same MAC regardless of interface bring-up order.
func getMACAddress() string {
	macs := getAllPhysicalMACs()
	if len(macs) > 0 {
		return macs[0]
	}
	return ""
}

func getIPAddresses() []string {
	addrs, err := net.InterfaceAddrs()
	if err != nil {
		return nil
	}
	var ips []string
	for _, addr := range addrs {
		if ipNet, ok := addr.(*net.IPNet); ok && !ipNet.IP.IsLoopback() && ipNet.IP.To4() != nil {
			ips = append(ips, ipNet.IP.String())
		}
	}
	return ips
}

func getUptimeSeconds() int {
	data, err := os.ReadFile("/proc/uptime")
	if err != nil {
		return 0
	}
	parts := strings.Fields(string(data))
	if len(parts) == 0 {
		return 0
	}
	var seconds float64
	fmt.Sscanf(parts[0], "%f", &seconds)
	return int(seconds)
}

func getNixOSVersion() string {
	data, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return "unknown"
	}
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, "VERSION_ID=") {
			return strings.Trim(strings.TrimPrefix(line, "VERSION_ID="), "\"")
		}
	}
	return "unknown"
}

// getWireGuardPubKey reads the appliance's WireGuard public key if available.
func getWireGuardPubKey() string {
	data, err := os.ReadFile("/var/lib/msp/wireguard/public.key")
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

// applyWireguardConfig writes the hub-side WireGuard parameters to disk and
// restarts the wireguard-tunnel systemd service so the tunnel comes up.
func applyWireguardConfig(wg *WireguardConfig) {
	if wg == nil || wg.HubPubKey == "" || wg.HubEndpoint == "" || wg.MyIP == "" {
		return
	}

	configDir := "/var/lib/msp/wireguard"
	configPath := filepath.Join(configDir, "config.json")

	// Read existing config to avoid unnecessary restarts
	if existing, err := os.ReadFile(configPath); err == nil {
		var old WireguardConfig
		if json.Unmarshal(existing, &old) == nil {
			if old.HubPubKey == wg.HubPubKey && old.HubEndpoint == wg.HubEndpoint && old.MyIP == wg.MyIP {
				return // config unchanged, skip restart
			}
		}
	}

	if err := os.MkdirAll(configDir, 0700); err != nil {
		slog.Error("failed to create config directory", "component", "wireguard", "error", err)
		return
	}

	data, err := json.MarshalIndent(map[string]string{
		"hub_pubkey":   wg.HubPubKey,
		"hub_endpoint": wg.HubEndpoint,
		"my_ip":        wg.MyIP,
	}, "", "  ")
	if err != nil {
		slog.Error("failed to marshal config", "component", "wireguard", "error", err)
		return
	}

	if err := os.WriteFile(configPath, data, 0600); err != nil {
		slog.Error("failed to write config", "component", "wireguard", "error", err)
		return
	}

	// Restart the wireguard-tunnel systemd service
	cmd := execCommand("systemctl", "restart", "wireguard-tunnel")
	if out, err := cmd.CombinedOutput(); err != nil {
		slog.Error("failed to restart tunnel service", "component", "wireguard", "error", err, "output", string(out))
	} else {
		slog.Info("config written, tunnel restarting", "component", "wireguard", "my_ip", wg.MyIP, "hub_endpoint", wg.HubEndpoint)
	}
}

// execCommand is a variable so tests can replace it.
var execCommand = execCommandFunc

func execCommandFunc(name string, args ...string) *execCmd {
	return &execCmd{cmd: exec.Command(name, args...)}
}

// execCmd wraps exec.Cmd to allow test substitution.
type execCmd struct {
	cmd *exec.Cmd
}

func (c *execCmd) CombinedOutput() ([]byte, error) {
	return c.cmd.CombinedOutput()
}

// Ensure x509 import is referenced (used by VerifyPeerCertificate callback signature).
var _ *x509.Certificate
