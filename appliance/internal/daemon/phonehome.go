package daemon

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"strings"
	"time"
)

// PhoneHomeClient handles communication with Central Command.
type PhoneHomeClient struct {
	config *Config
	client *http.Client
}

// NewPhoneHomeClient creates a new client for Central Command checkin.
func NewPhoneHomeClient(cfg *Config) *PhoneHomeClient {
	return &PhoneHomeClient{
		config: cfg,
		client: &http.Client{
			Timeout: 30 * time.Second,
			Transport: &http.Transport{
				TLSClientConfig:     &tls.Config{MinVersion: tls.VersionTLS12},
				MaxIdleConns:        5,
				IdleConnTimeout:     90 * time.Second,
				TLSHandshakeTimeout: 10 * time.Second,
			},
		},
	}
}

// CheckinRequest is the payload sent to Central Command.
type CheckinRequest struct {
	SiteID              string   `json:"site_id"`
	Hostname            string   `json:"hostname"`
	MACAddress          string   `json:"mac_address"`
	IPAddresses         []string `json:"ip_addresses"`
	UptimeSeconds       int      `json:"uptime_seconds"`
	AgentVersion        string   `json:"agent_version"`
	NixOSVersion        string   `json:"nixos_version"`
	HasLocalCredentials bool     `json:"has_local_credentials"`
	AgentPublicKey      string   `json:"agent_public_key,omitempty"`
}

// CheckinResponse is what Central Command returns.
type CheckinResponse struct {
	Status               string                   `json:"status"`
	ApplianceID          string                   `json:"appliance_id"`
	ServerTime           string                   `json:"server_time"`
	ServerPublicKey      string                   `json:"server_public_key"`
	MergedDuplicates     int                      `json:"merged_duplicates"`
	PendingOrders        []map[string]interface{}  `json:"pending_orders"`
	WindowsTargets       []map[string]interface{}  `json:"windows_targets"`
	LinuxTargets         []map[string]interface{}  `json:"linux_targets"`
	EnabledRunbooks      []string                 `json:"enabled_runbooks"`
	TriggerEnumeration   bool                     `json:"trigger_enumeration"`
	TriggerImmediateScan bool                     `json:"trigger_immediate_scan"`
	L2Mode               string                   `json:"l2_mode"`
	SubscriptionStatus   string                   `json:"subscription_status"`
}

// Checkin sends a phone-home checkin to Central Command.
func (c *PhoneHomeClient) Checkin(ctx context.Context, req CheckinRequest) (*CheckinResponse, error) {
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
		return nil, fmt.Errorf("checkin request: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("checkin returned %d: %s", resp.StatusCode, string(respBody))
	}

	var result CheckinResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("parse response: %w", err)
	}

	return &result, nil
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
func SystemInfo(cfg *Config, version string) CheckinRequest {
	hostname := getHostname()
	mac := getMACAddress()
	ips := getIPAddresses()
	uptime := getUptimeSeconds()
	nixVer := getNixOSVersion()

	return CheckinRequest{
		SiteID:        cfg.SiteID,
		Hostname:      hostname,
		MACAddress:    mac,
		IPAddresses:   ips,
		UptimeSeconds: uptime,
		AgentVersion:  version,
		NixOSVersion:  nixVer,
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

func init() {
	// Suppress unused import warning
	_ = log.Println
}
