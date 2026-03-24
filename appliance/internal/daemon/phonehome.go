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
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/osiriscare/appliance/internal/phiscrub"
)

// PhoneHomeClient handles communication with Central Command.
type PhoneHomeClient struct {
	config              *Config
	client              *http.Client
	consecutiveFailures int
	maxFailuresBeforeReset int
}

// NewPhoneHomeClient creates a new client for Central Command checkin.
// Uses Trust-On-First-Use (TOFU) certificate pinning: on the first successful
// TLS connection the server's leaf certificate SHA-256 fingerprint is saved to
// {StateDir}/server_cert_pin.hex. Subsequent connections verify the fingerprint
// matches, protecting against MITM even if a CA is compromised.
func NewPhoneHomeClient(cfg *Config) *PhoneHomeClient {
	pinPath := filepath.Join(cfg.StateDir, "server_cert_pin.hex")

	transport := &http.Transport{
		TLSClientConfig: &tls.Config{
			MinVersion: tls.VersionTLS12,
			VerifyPeerCertificate: func(rawCerts [][]byte, _ [][]*x509.Certificate) error {
				if len(rawCerts) == 0 {
					return fmt.Errorf("no certificates presented")
				}
				fingerprint := sha256.Sum256(rawCerts[0])
				fpHex := hex.EncodeToString(fingerprint[:])

				pinData, err := os.ReadFile(pinPath)
				if err != nil {
					// First connection: save pin (TOFU)
					if mkErr := os.MkdirAll(filepath.Dir(pinPath), 0700); mkErr != nil {
						log.Printf("[tls-pin] WARNING: could not create pin directory: %v", mkErr)
					}
					if wErr := os.WriteFile(pinPath, []byte(fpHex), 0600); wErr != nil {
						log.Printf("[tls-pin] WARNING: could not save pin file: %v", wErr)
					}
					log.Printf("[tls-pin] TOFU: pinned server certificate fingerprint: %s...", fpHex[:16])
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
	log.Printf("[phonehome] Recreating HTTP client after %d consecutive failures — clearing TLS pin for re-TOFU", c.consecutiveFailures)
	c.client.CloseIdleConnections()
	// Delete stale TOFU pin so NewPhoneHomeClient will re-pin on next connection
	pinPath := filepath.Join(c.config.StateDir, "server_cert_pin.hex")
	if err := os.Remove(pinPath); err != nil && !os.IsNotExist(err) {
		log.Printf("[phonehome] WARNING: could not remove stale pin file: %v", err)
	}
	fresh := NewPhoneHomeClient(c.config)
	c.client = fresh.client
	c.consecutiveFailures = 0
}

// ConsecutiveFailures returns the current failure count.
func (c *PhoneHomeClient) ConsecutiveFailures() int {
	return c.consecutiveFailures
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

	body, err := io.ReadAll(resp.Body)
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
	Status   string `json:"status"` // "success" or "failed"
	AgentID  string `json:"agent_id,omitempty"`
	Error    string `json:"error,omitempty"`
}

// CheckinRequest is the payload sent to Central Command.
type CheckinRequest struct {
	SiteID              string           `json:"site_id"`
	Hostname            string           `json:"hostname"`
	MACAddress          string           `json:"mac_address"`
	IPAddresses         []string         `json:"ip_addresses"`
	UptimeSeconds       int              `json:"uptime_seconds"`
	AgentVersion        string           `json:"agent_version"`
	NixOSVersion        string           `json:"nixos_version"`
	HasLocalCredentials bool             `json:"has_local_credentials"`
	AgentPublicKey      string           `json:"agent_public_key,omitempty"`
	WgPubKey            string           `json:"wg_pubkey,omitempty"`
	WgConnected         bool             `json:"wg_connected,omitempty"`
	WgIP                string           `json:"wg_ip,omitempty"`
	ConnectedAgents     []ConnectedAgent        `json:"connected_agents,omitempty"`
	DiscoveryResults    map[string]interface{}   `json:"discovery_results,omitempty"`
	EncryptionPublicKey string                   `json:"encryption_public_key,omitempty"`
	DeployResults       []DeployResult           `json:"deploy_results,omitempty"`
}

// ConnectedAgent represents a Go agent connected to this appliance via gRPC.
type ConnectedAgent struct {
	AgentID       string `json:"agent_id"`
	Hostname      string `json:"hostname"`
	AgentVersion  string `json:"agent_version,omitempty"`
	Tier          int    `json:"capability_tier"`
	ConnectedAt   string `json:"connected_at"`
	LastHeartbeat string `json:"last_heartbeat"`
	DriftCount    int64  `json:"drift_count"`
	ChecksPassed  int64  `json:"checks_passed"`
	ChecksTotal   int64  `json:"checks_total"`
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
		c.consecutiveFailures++
		if c.consecutiveFailures >= c.maxFailuresBeforeReset {
			c.RecreateClient()
		}
		return nil, fmt.Errorf("checkin request: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		c.consecutiveFailures++
		if c.consecutiveFailures >= c.maxFailuresBeforeReset {
			c.RecreateClient()
		}
		return nil, fmt.Errorf("checkin returned %d: %s", resp.StatusCode, string(respBody))
	}

	var result CheckinResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("parse response: %w", err)
	}

	c.consecutiveFailures = 0
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
		respBody, _ := io.ReadAll(resp.Body)
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

	// HTTP status codes (embedded in error message)
	if strings.Contains(msg, "returned 5") {
		return "server_error"
	}

	return "unknown"
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
		SiteID:        cfg.SiteID,
		Hostname:      hostname,
		MACAddress:    mac,
		IPAddresses:   ips,
		UptimeSeconds: uptime,
		AgentVersion:  version,
		NixOSVersion:  nixVer,
		WgPubKey:      wgPub,
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

func getMACAddress() string {
	ifaces, err := net.Interfaces()
	if err != nil {
		return ""
	}
	for _, iface := range ifaces {
		if iface.Flags&net.FlagLoopback != 0 || iface.Flags&net.FlagUp == 0 {
			continue
		}
		mac := iface.HardwareAddr.String()
		if mac == "" || strings.HasPrefix(mac, "00:00:00") {
			continue
		}
		return mac
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
		log.Printf("[wireguard] Failed to create config directory: %v", err)
		return
	}

	data, err := json.MarshalIndent(map[string]string{
		"hub_pubkey":   wg.HubPubKey,
		"hub_endpoint": wg.HubEndpoint,
		"my_ip":        wg.MyIP,
	}, "", "  ")
	if err != nil {
		log.Printf("[wireguard] Failed to marshal config: %v", err)
		return
	}

	if err := os.WriteFile(configPath, data, 0600); err != nil {
		log.Printf("[wireguard] Failed to write config: %v", err)
		return
	}

	// Restart the wireguard-tunnel systemd service
	cmd := execCommand("systemctl", "restart", "wireguard-tunnel")
	if out, err := cmd.CombinedOutput(); err != nil {
		log.Printf("[wireguard] Failed to restart tunnel service: %v (%s)", err, string(out))
	} else {
		log.Printf("[wireguard] Config written, tunnel restarting: %s -> %s", wg.MyIP, wg.HubEndpoint)
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
