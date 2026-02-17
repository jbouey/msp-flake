// Package checkin implements the appliance checkin HTTP receiver.
//
// This replaces the /api/appliances/checkin FastAPI endpoint in sites.py.
// It handles the fan-in pattern where all appliances periodically check in,
// receive pending orders, credentials, and configuration.
package checkin

import (
	"strings"
	"time"
)

// CheckinRequest matches the ApplianceCheckin Pydantic model.
type CheckinRequest struct {
	SiteID              string   `json:"site_id"`
	Hostname            string   `json:"hostname"`
	MACAddress          string   `json:"mac_address"`
	IPAddresses         []string `json:"ip_addresses"`
	UptimeSeconds       *int     `json:"uptime_seconds,omitempty"`
	AgentVersion        *string  `json:"agent_version,omitempty"`
	NixOSVersion        *string  `json:"nixos_version,omitempty"`
	HasLocalCredentials bool     `json:"has_local_credentials"`
	AgentPublicKey      *string  `json:"agent_public_key,omitempty"`
}

// CheckinResponse is the JSON response sent back to the appliance.
type CheckinResponse struct {
	Status               string          `json:"status"`
	ApplianceID          string          `json:"appliance_id"`
	ServerTime           string          `json:"server_time"`
	MergedDuplicates     int             `json:"merged_duplicates"`
	PendingOrders        []PendingOrder  `json:"pending_orders"`
	WindowsTargets       []WindowsTarget `json:"windows_targets"`
	LinuxTargets         []LinuxTarget   `json:"linux_targets"`
	EnabledRunbooks      []string        `json:"enabled_runbooks"`
	TriggerEnumeration   bool            `json:"trigger_enumeration"`
	TriggerImmediateScan bool            `json:"trigger_immediate_scan"`
}

// PendingOrder represents an admin order or healing order.
type PendingOrder struct {
	OrderID    string                 `json:"order_id"`
	OrderType  string                 `json:"order_type"`
	Parameters map[string]interface{} `json:"parameters"`
	Priority   int                    `json:"priority"`
	CreatedAt  *string                `json:"created_at"`
	ExpiresAt  *string                `json:"expires_at"`
	RunbookID  string                 `json:"runbook_id,omitempty"`
}

// WindowsTarget is a WinRM credential set for a Windows machine.
type WindowsTarget struct {
	Hostname string `json:"hostname"`
	Username string `json:"username"`
	Password string `json:"password"`
	UseSSL   bool   `json:"use_ssl"`
}

// LinuxTarget is an SSH credential set for a Linux machine.
type LinuxTarget struct {
	Hostname   string  `json:"hostname"`
	Port       int     `json:"port"`
	Username   string  `json:"username"`
	Password   *string `json:"password,omitempty"`
	PrivateKey *string `json:"private_key,omitempty"`
	Distro     *string `json:"distro,omitempty"`
}

// NormalizeMAC normalizes a MAC address to uppercase colon-separated format.
// "84:3a:5b:91:b6:61" -> "84:3A:5B:91:B6:61"
// "84-3A-5B-91-B6-61" -> "84:3A:5B:91:B6:61"
// "843a5b91b661"      -> "84:3A:5B:91:B6:61"
func NormalizeMAC(mac string) string {
	clean := strings.ToUpper(
		strings.NewReplacer(":", "", "-", "", ".", "").Replace(mac),
	)
	if len(clean) != 12 {
		return mac // Return as-is if not a valid MAC
	}
	var parts []string
	for i := 0; i < 12; i += 2 {
		parts = append(parts, clean[i:i+2])
	}
	return strings.Join(parts, ":")
}

// CleanMAC strips separators and uppercases for comparison.
func CleanMAC(mac string) string {
	return strings.ToUpper(
		strings.NewReplacer(":", "", "-", "", ".", "").Replace(mac),
	)
}

// CanonicalApplianceID generates the deterministic appliance ID.
func CanonicalApplianceID(siteID, mac string) string {
	return siteID + "-" + NormalizeMAC(mac)
}

// isoTime formats a time as ISO 8601.
func isoTime(t time.Time) string {
	return t.UTC().Format(time.RFC3339)
}

// isoTimePtr formats a *time.Time as *string.
func isoTimePtr(t *time.Time) *string {
	if t == nil {
		return nil
	}
	s := t.UTC().Format(time.RFC3339)
	return &s
}
