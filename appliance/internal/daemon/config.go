// Package daemon implements the main appliance daemon loop.
package daemon

import (
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

// Config holds appliance daemon configuration.
type Config struct {
	// Required
	SiteID string `yaml:"site_id"`
	APIKey string `yaml:"api_key"`

	// API connection
	APIEndpoint string `yaml:"api_endpoint"`

	// ConfigPath is the file path this config was loaded from (set by LoadConfig, not YAML).
	ConfigPath string `yaml:"-"`

	// Timing
	PollInterval int `yaml:"poll_interval"` // seconds

	// Features
	EnableDriftDetection bool `yaml:"enable_drift_detection"`
	EnableEvidenceUpload bool `yaml:"enable_evidence_upload"`
	EnableL1Sync         bool `yaml:"enable_l1_sync"`

	// Healing
	HealingEnabled bool `yaml:"healing_enabled"`
	HealingDryRun  bool `yaml:"healing_dry_run"`

	// L2 LLM Planner (calls Central Command, which holds the LLM API key)
	L2Enabled            bool     `yaml:"l2_enabled"`
	L2APITimeoutSecs     int      `yaml:"l2_api_timeout"`
	L2DailyBudgetUSD     float64  `yaml:"l2_daily_budget_usd"`
	L2MaxCallsPerHour    int      `yaml:"l2_max_calls_per_hour"`
	L2MaxConcurrentCalls int      `yaml:"l2_max_concurrent_calls"`
	L2AllowedActions     []string `yaml:"l2_allowed_actions"`

	// Paths
	StateDir string `yaml:"state_dir"`

	// Logging
	LogLevel string `yaml:"log_level"`

	// AD/Workstation
	WorkstationEnabled bool    `yaml:"workstation_enabled"`
	DomainController   *string `yaml:"domain_controller,omitempty"`
	DCUsername         *string `yaml:"dc_username,omitempty"`
	DCPassword         *string `yaml:"dc_password,omitempty"`

	// WORM
	WORMEnabled       bool   `yaml:"worm_enabled"`
	WORMMode          string `yaml:"worm_mode"`
	WORMRetentionDays int    `yaml:"worm_retention_days"`
	WORMAutoUpload    bool   `yaml:"worm_auto_upload"`

	// gRPC server
	GRPCPort int    `yaml:"grpc_port"`
	CADir    string `yaml:"ca_dir"`

	// Feature flag for Go daemon rollout
	UseGoDaemon bool `yaml:"use_go_daemon"`

	// DNS: AD domain controller for hostname resolution fallback.
	// When set, hostname lookups that fail against the system resolver
	// are retried against this server (e.g., for NVDC01 → 192.168.88.250).
	ADDNSServer string `yaml:"ad_dns_server"`

	// Security: reject unsigned L1 rules when a server public key is configured
	RequireSignedRules bool `yaml:"require_signed_rules"`

	// TLS certificate pinning: SHA-256 hash of Central Command's SPKI (Subject Public Key Info).
	// If set, the daemon verifies the server's public key matches this hash on every TLS connection,
	// protecting against MITM even if a CA is compromised. If empty, falls back to TOFU pinning.
	// Generate with: openssl x509 -in cert.pem -pubkey -noout | openssl pkey -pubin -outform DER | openssl dgst -sha256 -hex
	TLSPinHash string `yaml:"tls_pin_hash"`
}

// DefaultConfig returns a config with sane defaults.
func DefaultConfig() Config {
	return Config{
		APIEndpoint:          "https://api.osiriscare.net",
		PollInterval:         60,
		EnableDriftDetection: true,
		EnableEvidenceUpload: true,
		EnableL1Sync:         true,
		HealingEnabled:       true,
		HealingDryRun:        false,
		L2Enabled:            true,
		L2APITimeoutSecs:     30,
		L2DailyBudgetUSD:     10.00,
		L2MaxCallsPerHour:    60,
		L2MaxConcurrentCalls: 3,
		StateDir:             "/var/lib/msp",
		LogLevel:             "INFO",
		WorkstationEnabled:   true,
		WORMEnabled:          false,
		WORMMode:             "proxy",
		WORMRetentionDays:    90,
		WORMAutoUpload:       true,
		GRPCPort:             50051,
		CADir:                "/var/lib/msp/ca",
		UseGoDaemon:          false,
	}
}

// LoadConfig loads configuration from a YAML file with env overrides.
func LoadConfig(path string) (*Config, error) {
	cfg := DefaultConfig()

	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config: %w", err)
	}

	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}

	// Environment variable overrides
	if v := os.Getenv("HEALING_DRY_RUN"); v != "" {
		cfg.HealingDryRun = !isFalsy(v)
	}
	if v := os.Getenv("STATE_DIR"); v != "" {
		cfg.StateDir = v
	}
	if v := os.Getenv("LOG_LEVEL"); v != "" {
		cfg.LogLevel = strings.ToUpper(v)
	}
	if v := os.Getenv("L2_ENABLED"); v != "" {
		cfg.L2Enabled = !isFalsy(v)
	}

	// Validate required fields
	if cfg.SiteID == "" {
		return nil, fmt.Errorf("site_id is required")
	}
	if cfg.APIKey == "" {
		return nil, fmt.Errorf("api_key is required")
	}
	if cfg.PollInterval < 10 {
		cfg.PollInterval = 10
	}
	if cfg.PollInterval > 3600 {
		cfg.PollInterval = 3600
	}

	cfg.ConfigPath = path
	return &cfg, nil
}

// UpdateAPIKey atomically updates the top-level api_key in the config YAML
// file. Writes to a temp file then renames to avoid partial writes.
//
// Mesh Hardening Phase 2 (Daemon 0.4.5): switched from line-prefix regex to
// yaml.Node round-trip. The prior implementation matched `api_key:` with
// strings.HasPrefix on a trimmed line, which corrupted YAML containing
// nested keys like `config.api_key:` — observed during the .226 rekey
// attempt that produced invalid YAML until a manual sed patch. The Node-
// based implementation only touches the top-level mapping, preserves
// surrounding comments and ordering, and post-parses the result to verify
// the new key landed where we expected.
func UpdateAPIKey(path string, newKey string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read config for rekey: %w", err)
	}

	var root yaml.Node
	if err := yaml.Unmarshal(data, &root); err != nil {
		return fmt.Errorf("parse config for rekey: %w", err)
	}

	if root.Kind != yaml.DocumentNode || len(root.Content) == 0 {
		return fmt.Errorf("config YAML is not a document node")
	}
	top := root.Content[0]
	if top.Kind != yaml.MappingNode {
		return fmt.Errorf("config YAML root is not a mapping (kind=%d)", top.Kind)
	}

	// Top-level mapping: content is [key, value, key, value, ...]. Walk in
	// pairs and replace only the TOP-level api_key scalar — nested keys
	// (e.g. config.api_key under a sub-map) are never touched.
	found := false
	for i := 0; i+1 < len(top.Content); i += 2 {
		k := top.Content[i]
		if k.Kind != yaml.ScalarNode || k.Value != "api_key" {
			continue
		}
		v := top.Content[i+1]
		v.Kind = yaml.ScalarNode
		v.Tag = "!!str"
		v.Value = newKey
		// Preserve the original scalar style when possible; fall back to
		// double-quoted for safety since the key may contain URL-unsafe bytes.
		if v.Style == 0 {
			v.Style = yaml.DoubleQuotedStyle
		}
		found = true
		break
	}
	if !found {
		top.Content = append(top.Content,
			&yaml.Node{Kind: yaml.ScalarNode, Tag: "!!str", Value: "api_key"},
			&yaml.Node{Kind: yaml.ScalarNode, Tag: "!!str", Value: newKey, Style: yaml.DoubleQuotedStyle},
		)
	}

	out, err := yaml.Marshal(&root)
	if err != nil {
		return fmt.Errorf("marshal config after rekey: %w", err)
	}

	// Post-verify: parse the output as a plain map and confirm the top-level
	// api_key matches what we intended to write. Catches any silent Marshal
	// regression before we rename the file into place.
	var verify map[string]interface{}
	if err := yaml.Unmarshal(out, &verify); err != nil {
		return fmt.Errorf("post-verify parse failed: %w", err)
	}
	if got, _ := verify["api_key"].(string); got != newKey {
		return fmt.Errorf("post-verify mismatch: expected api_key to be updated but got %q", got)
	}

	tmpPath := path + ".tmp"
	if err := os.WriteFile(tmpPath, out, 0600); err != nil {
		return fmt.Errorf("write temp config: %w", err)
	}
	if err := os.Rename(tmpPath, path); err != nil {
		os.Remove(tmpPath)
		return fmt.Errorf("rename config: %w", err)
	}
	return nil
}

// EvidenceDir returns the evidence storage directory.
func (c *Config) EvidenceDir() string {
	return filepath.Join(c.StateDir, "evidence")
}

// QueueDBPath returns the SQLite queue database path.
func (c *Config) QueueDBPath() string {
	return filepath.Join(c.StateDir, "queue.db")
}

// RulesDir returns the L1 rules directory.
func (c *Config) RulesDir() string {
	return filepath.Join(c.StateDir, "rules")
}

// SigningKeyPath returns the path to the Ed25519 signing key.
func (c *Config) SigningKeyPath() string {
	return filepath.Join(c.StateDir, "keys", "signing.key")
}

// GRPCListenAddr returns the address agents should connect to.
// Uses the appliance's LAN IP and the configured gRPC port.
func (c *Config) GRPCListenAddr() string {
	// Try to find the LAN IP
	addrs, err := net.InterfaceAddrs()
	if err == nil {
		for _, addr := range addrs {
			if ipNet, ok := addr.(*net.IPNet); ok && !ipNet.IP.IsLoopback() && ipNet.IP.To4() != nil {
				return fmt.Sprintf("%s:%d", ipNet.IP.String(), c.GRPCPort)
			}
		}
	}
	// Fallback
	return fmt.Sprintf("0.0.0.0:%d", c.GRPCPort)
}

func isFalsy(v string) bool {
	v = strings.ToLower(strings.TrimSpace(v))
	return v == "false" || v == "0" || v == "no"
}
