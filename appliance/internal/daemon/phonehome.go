package daemon

import (
	"bytes"
	"context"
	cryptorand "crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
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
	// Week 1 of the composed identity stack: when set, the client
	// signs every outbound checkin with the device-bound Ed25519
	// keypair. nil during the soak only when LoadOrCreateIdentity
	// failed at startup (logged loudly; bearer auth still works).
	identity *Identity
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

// NewPhoneHomeClientWithIdentity is the Week-1+ constructor used by
// daemon.New(). It wraps NewPhoneHomeClient and attaches an Identity
// so Checkin can sign its outbound payload. Passing a nil identity
// is allowed — the client falls back to bearer-only behavior.
func NewPhoneHomeClientWithIdentity(cfg *Config, id *Identity) *PhoneHomeClient {
	c := NewPhoneHomeClient(cfg)
	c.identity = id
	return c
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
	// HeartbeatSignature — Ed25519 hex over SHA-256 of
	//   {site_id}|{mac_address}|{checkin_timestamp_unix}|{agent_version}
	// Populated if the daemon has a signing key available (post-D1). Server
	// records it in appliance_heartbeats.agent_signature; verification is
	// enabled server-side once every appliance in the fleet ships it.
	HeartbeatSignature  string           `json:"heartbeat_signature,omitempty"`
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
	// Time-travel state (Session 205 Phase 2). Agent reports each cycle so
	// Central Command can detect snapshot reverts / backup restores and
	// trigger a signed reconciliation. See internal/daemon/reconcile.go.
	BootCounter         int64            `json:"boot_counter,omitempty"`
	GenerationUUID      string           `json:"generation_uuid,omitempty"`
	ReconcileNeeded     bool             `json:"reconcile_needed,omitempty"`
	ReconcileSignals    []string         `json:"reconcile_signals,omitempty"`
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
	// Boot/clock diagnostics — catches HP T-series TSC stuck-clock issues.
	// ProcUptimeRaw is the raw /proc/uptime content so we can see what the
	// kernel is reporting vs what we parse. If ProcUptimeRaw is "107.00 62.33"
	// every checkin, the kernel clocksource is frozen and we need
	// clocksource=hpet kernel param.
	BootSource      string   `json:"boot_source,omitempty"`   // live_usb, installed_disk
	ProcUptimeRaw   string   `json:"proc_uptime_raw,omitempty"`
	Clocksource     string   `json:"clocksource,omitempty"`   // from /sys/devices/system/clocksource/
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
		BootSource:    detectBootSource(),
		ProcUptimeRaw: getProcUptimeRaw(),
		Clocksource:   getCurrentClocksource(),
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
	// Time-travel reconciliation plan (Session 205). Delivered inline when
	// the agent reported ≥2 detection signals and CC validated the request.
	// Nil when no reconcile is needed. Agent MUST Ed25519-verify against
	// ServerPublicKey(s) before executing.
	ReconcilePlan *ReconcilePlan `json:"reconcile_plan,omitempty"`
}

// ReconcilePlan is the server-authoritative recovery plan for an agent
// that reported time-travel signals. All fields are signed together; the
// signature_hex is the Ed25519 signature over the canonical SignedPayload
// string (sort_keys=True JSON of plan_id + new_generation_uuid +
// nonce_epoch_hex + runbook_ids + issued_at + appliance_id) as produced
// by the backend.
//
// The agent verifies against SignedPayload DIRECTLY (not a reconstruction)
// to avoid cross-language JSON serialization ambiguity — Python's
// json.dumps and Go's json.Marshal differ on separator whitespace. Using
// the server-produced string makes the wire contract byte-exact.
type ReconcilePlan struct {
	// PlanID: server-generated UUID for this plan (correlation in audit).
	PlanID string `json:"plan_id"`
	// NewGenerationUUID: the agent MUST write this to disk after apply.
	// Future checkins include this; CC rotates it to detect future reverts.
	NewGenerationUUID string `json:"new_generation_uuid"`
	// NonceEpochHex: 64-char hex (32 random bytes). Agent purges any cached
	// orders/nonces below this epoch — invalidates captured-order replays
	// that a reverted snapshot might re-accept.
	NonceEpochHex string `json:"nonce_epoch_hex"`
	// RunbookIDs: ordered list to execute idempotently. May be empty if
	// CC decided no remediation is needed (rare — usually we re-run the
	// last known-good checks).
	RunbookIDs []string `json:"runbook_ids"`
	// IssuedAt: RFC3339 timestamp CC signed at. Agent rejects plans older
	// than 10 minutes to prevent replay of captured plans.
	IssuedAt string `json:"issued_at"`
	// ApplianceID: must match this appliance — rejects cross-site plans.
	ApplianceID string `json:"appliance_id"`
	// SignatureHex: Ed25519 signature, 128-char lowercase hex.
	SignatureHex string `json:"signature_hex"`
	// SignedPayload: the EXACT canonical JSON string that was signed.
	// Agent verifies SignatureHex against this string directly. Must NOT
	// be reconstructed client-side — the server is the source of truth
	// for the byte sequence that produced the signature.
	SignedPayload string `json:"signed_payload"`
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

	// Week 1 of the composed identity stack: when an Identity is
	// attached, sign the request and add the three X-Appliance-*
	// headers the server uses for observe-only verification. The
	// canonical input format is FROZEN here and at signature_auth.py
	// — same byte layout, same separators (no trailing newline).
	if c.identity != nil {
		signRequest(httpReq, body, c.identity)
	}

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

// PostReconcileAck POSTs a reconcile ACK to Central Command after the
// daemon applies (or fails to apply) a signed reconcile plan. The body
// is the JSON-serialized reconcileAckRequest.
//
// Uses the same Bearer auth as checkin. Non-200 responses are surfaced
// as errors so the caller can log — retries are NOT attempted here
// (reconcile_apply.go treats ACK as best-effort).
func (c *PhoneHomeClient) PostReconcileAck(ctx context.Context, body []byte) error {
	url := strings.TrimRight(c.config.APIEndpoint, "/") + "/api/appliances/reconcile/ack"
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create reconcile ack request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+c.config.APIKey)
	httpReq.Header.Set("User-Agent", "OsirisCare-Appliance/Go")

	resp, err := c.client.Do(httpReq)
	if err != nil {
		return fmt.Errorf("reconcile ack request: %w", err)
	}
	defer resp.Body.Close()

	// Drain body even on success — keeps TLS connection reusable.
	respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<10))
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("reconcile ack returned %d: %s",
			resp.StatusCode, string(respBody))
	}
	return nil
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

// SystemInfoSigned returns a checkin request with the agent public key AND
// a D1 heartbeat signature. The signature is Ed25519 over SHA-256 of the
// canonical heartbeat payload: site_id|mac|timestamp_unix|agent_version.
// Server records it in appliance_heartbeats.agent_signature so auditors can
// verify liveness claims were made by the legitimate appliance key.
//
// Pass a SignFunc (typically daemon.SignBytes or similar). If signFn is nil
// or returns an error, HeartbeatSignature is omitted (server treats as NULL).
func SystemInfoSigned(
	cfg *Config,
	version, pubKeyHex string,
	signFn func([]byte) ([]byte, error),
) CheckinRequest {
	req := SystemInfoWithKey(cfg, version, pubKeyHex)
	if signFn == nil {
		return req
	}
	// Canonical form — must match server's expectation.
	ts := fmt.Sprintf("%d", time.Now().UTC().Unix())
	payload := strings.Join([]string{
		req.SiteID,
		strings.ToUpper(req.MACAddress),
		ts,
		req.AgentVersion,
	}, "|")
	h := sha256.Sum256([]byte(payload))
	sig, err := signFn(h[:])
	if err != nil {
		slog.Warn("heartbeat signing failed — sending unsigned",
			"component", "phonehome", "error", err)
		return req
	}
	req.HeartbeatSignature = hex.EncodeToString(sig)
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
// Detection order (most reliable first):
//  1. /proc/mounts: if / is on a real disk (ext4/btrfs/xfs), it's installed.
//     If / is tmpfs/ramfs/squashfs, it's a live ISO.
//  2. MSP-DATA partition label exists → installed (installer creates this).
//  3. /proc/cmdline: fallback — only trust if mounts unavailable. NixOS installed
//     systems may have "squashfs" in initrd paths, so we can't use cmdline alone.
func detectBootSource() string {
	// PRIMARY: Check root filesystem type — most reliable signal.
	// Installed: ext4, btrfs, xfs, zfs. Live: tmpfs, ramfs, squashfs, overlay.
	mounts, err := os.ReadFile("/proc/mounts")
	if err == nil {
		for _, line := range strings.Split(string(mounts), "\n") {
			fields := strings.Fields(line)
			if len(fields) >= 3 && fields[1] == "/" {
				fstype := fields[2]
				switch fstype {
				case "tmpfs", "ramfs", "squashfs", "overlay":
					return "live_usb"
				case "ext4", "ext3", "ext2", "btrfs", "xfs", "zfs", "f2fs":
					return "installed_disk"
				}
				// Unknown fstype — fall through to secondary checks
			}
		}
	}

	// SECONDARY: Check for the MSP-DATA partition label (created by installer).
	// If present, the system has been installed regardless of cmdline.
	if _, err := os.Stat("/dev/disk/by-label/MSP-DATA"); err == nil {
		return "installed_disk"
	}

	// TERTIARY: /proc/cmdline heuristic — only when mounts unavailable.
	// Note: NixOS installed systems CAN have "squashfs" in cmdline (initrd paths),
	// so this is not reliable on its own. We only reach here if mounts check failed.
	cmdline, err := os.ReadFile("/proc/cmdline")
	if err == nil {
		cmd := string(cmdline)
		if strings.Contains(cmd, "boot.shell_on_fail") ||
			strings.Contains(cmd, "copytoram") {
			return "live_usb"
		}
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

// lastUptimeReading caches the last /proc/uptime read for diagnostic purposes.
// If the kernel clocksource is broken (HP T-series TSC bug), we can see the
// raw value the kernel returned and know to pin clocksource=hpet.
var (
	lastUptimeRaw     string
	lastUptimeReadAt  time.Time
	lastUptimeSeconds int
)

func getUptimeSeconds() int {
	data, err := os.ReadFile("/proc/uptime")
	if err != nil {
		slog.Warn("/proc/uptime read failed", "component", "daemon", "error", err)
		return 0
	}
	raw := strings.TrimSpace(string(data))
	parts := strings.Fields(raw)
	if len(parts) == 0 {
		slog.Warn("/proc/uptime empty", "component", "daemon", "raw", raw)
		return 0
	}
	var seconds float64
	n, err := fmt.Sscanf(parts[0], "%f", &seconds)
	if err != nil || n != 1 {
		slog.Warn("/proc/uptime parse failed", "component", "daemon", "value", parts[0], "error", err)
		return 0
	}
	result := int(seconds)

	// Diagnostic: if uptime appears frozen (identical reads), log it.
	// This catches the HP T-series TSC-stuck bug and daemon cache issues.
	if !lastUptimeReadAt.IsZero() && lastUptimeSeconds == result &&
		time.Since(lastUptimeReadAt) > 60*time.Second {
		slog.Warn("/proc/uptime reads frozen — possible kernel clocksource issue",
			"component", "daemon",
			"frozen_at_seconds", result,
			"frozen_duration", time.Since(lastUptimeReadAt).Round(time.Second).String(),
			"raw", raw,
			"recommendation", "kernel clocksource=hpet")
	}
	lastUptimeRaw = raw
	lastUptimeReadAt = time.Now()
	lastUptimeSeconds = result
	return result
}

// GetUptimeDiagnostics returns the raw uptime reading for daemon_health telemetry.
// Exposed so the checkin can include raw /proc/uptime bytes when diagnostic mode
// is enabled, helping root-cause clock issues on problem hardware.
func GetUptimeDiagnostics() (raw string, readAt time.Time, parsed int) {
	return lastUptimeRaw, lastUptimeReadAt, lastUptimeSeconds
}

// getProcUptimeRaw returns the raw /proc/uptime content.
// Used in daemon_health telemetry to diagnose clock freezes (e.g. HP T-series
// TSC stuck-clock bug). The field is small (~20 bytes) and harmless to ship.
func getProcUptimeRaw() string {
	data, err := os.ReadFile("/proc/uptime")
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

// getCurrentClocksource reads the active kernel clocksource.
// On healthy systems: "tsc", "hpet", "acpi_pm", "kvm-clock", etc.
// If the kernel keeps marking TSC as unstable, the active source may
// flap — we report whatever is active at checkin time.
func getCurrentClocksource() string {
	data, err := os.ReadFile("/sys/devices/system/clocksource/clocksource0/current_clocksource")
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
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

// signRequest attaches the Week-1 device-identity signature headers
// to an outbound HTTP request. The canonical signing input is FROZEN
// at this layout — the backend's signature_auth.verify_appliance_signature
// rebuilds the same bytes and verifies. Any drift in either side
// breaks every signature.
//
// canonical = METHOD\nPATH\nSHA256_HEX_LOWER(body)\nRFC3339_UTC_Z\nNONCE_HEX32
//
// Headers added:
//
//	X-Appliance-Signature: base64url(ed25519_sig)   // no padding
//	X-Appliance-Timestamp: 2026-04-15T03:45:23Z
//	X-Appliance-Nonce:     <32 lowercase hex chars>
//	X-Appliance-Pubkey-Fingerprint: <16 hex chars>  // observability hint
//
// Failure to sign is logged but never blocks the request — bearer
// auth still works in soak mode.
func signRequest(req *http.Request, body []byte, id *Identity) {
	bodyHash := sha256.Sum256(body)
	bodyHashHex := hex.EncodeToString(bodyHash[:])

	ts := time.Now().UTC().Format("2006-01-02T15:04:05Z")

	nonceBytes := make([]byte, 16)
	if _, err := cryptorand.Read(nonceBytes); err != nil {
		slog.Warn("sigauth nonce gen failed — skipping signature", "component", "sigauth", "error", err)
		return
	}
	nonceHex := hex.EncodeToString(nonceBytes)

	canonical := []byte(strings.ToUpper(req.Method) +
		"\n" + req.URL.Path +
		"\n" + bodyHashHex +
		"\n" + ts +
		"\n" + nonceHex)

	sig := id.Sign(canonical)
	sigB64 := base64.RawURLEncoding.EncodeToString(sig)

	req.Header.Set("X-Appliance-Signature", sigB64)
	req.Header.Set("X-Appliance-Timestamp", ts)
	req.Header.Set("X-Appliance-Nonce", nonceHex)
	req.Header.Set("X-Appliance-Pubkey-Fingerprint", id.Fingerprint())
}
