// Package daemon implements the main appliance daemon loop.
package daemon

import (
	"fmt"
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

	// Timing
	PollInterval int `yaml:"poll_interval"` // seconds

	// Features
	EnableDriftDetection bool `yaml:"enable_drift_detection"`
	EnableEvidenceUpload bool `yaml:"enable_evidence_upload"`
	EnableL1Sync         bool `yaml:"enable_l1_sync"`

	// Healing
	HealingEnabled bool `yaml:"healing_enabled"`
	HealingDryRun  bool `yaml:"healing_dry_run"`

	// L2 LLM Planner
	L2Enabled     bool   `yaml:"l2_enabled"`
	L2APIProvider string `yaml:"l2_api_provider"`
	L2APIKey      string `yaml:"l2_api_key"`
	L2APIModel    string `yaml:"l2_api_model"`

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
		L2Enabled:            false,
		L2APIProvider:        "anthropic",
		L2APIModel:           "claude-3-5-haiku-latest",
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

	return &cfg, nil
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

func isFalsy(v string) bool {
	v = strings.ToLower(strings.TrimSpace(v))
	return v == "false" || v == "0" || v == "no"
}
