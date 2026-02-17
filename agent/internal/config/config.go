// Package config handles agent configuration loading and defaults.
package config

import (
	"encoding/json"
	"log"
	"os"
	"path/filepath"
)

// Config holds all agent configuration
type Config struct {
	// Connection settings
	ApplianceAddr string `json:"appliance_addr"`
	DataDir       string `json:"data_dir"`
	Domain        string `json:"domain"` // AD domain for SRV discovery

	// TLS settings
	CertFile string `json:"cert_file"`
	KeyFile  string `json:"key_file"`
	CAFile   string `json:"ca_file"`

	// Fallback HTTP settings (for REST API compatibility)
	HTTPEndpoint string `json:"http_endpoint"`
	UseHTTP      bool   `json:"use_http"`

	// Embedded defaults (set at compile time from appliance)
	DefaultAppliance string `json:"-"`
}

// Load loads configuration from file and command line overrides
func Load(configFile, applianceAddr string) (*Config, error) {
	// Default data directory
	dataDir := os.Getenv("PROGRAMDATA")
	if dataDir == "" {
		dataDir = "C:\\ProgramData"
	}

	cfg := &Config{
		DataDir: filepath.Join(dataDir, "OsirisCare"),
	}

	// Try config file
	if configFile != "" {
		data, err := os.ReadFile(configFile)
		if err == nil {
			if jsonErr := json.Unmarshal(data, cfg); jsonErr != nil {
				log.Printf("[config] WARNING: failed to parse %s: %v", configFile, jsonErr)
			}
		}
	}

	// Also try default location
	if cfg.ApplianceAddr == "" {
		defaultConfig := filepath.Join(cfg.DataDir, "config.json")
		if data, err := os.ReadFile(defaultConfig); err == nil {
			if jsonErr := json.Unmarshal(data, cfg); jsonErr != nil {
				log.Printf("[config] WARNING: failed to parse %s: %v", defaultConfig, jsonErr)
			}
		}
	}

	// Command line overrides
	if applianceAddr != "" {
		cfg.ApplianceAddr = applianceAddr
	}

	// Ensure data directory exists
	if err := os.MkdirAll(cfg.DataDir, 0755); err != nil {
		return nil, err
	}

	// Set default TLS paths
	if cfg.CertFile == "" {
		cfg.CertFile = filepath.Join(cfg.DataDir, "agent.crt")
	}
	if cfg.KeyFile == "" {
		cfg.KeyFile = filepath.Join(cfg.DataDir, "agent.key")
	}
	if cfg.CAFile == "" {
		cfg.CAFile = filepath.Join(cfg.DataDir, "ca.crt")
	}

	return cfg, nil
}

// Save saves current configuration to file
func (c *Config) Save() error {
	configPath := filepath.Join(c.DataDir, "config.json")
	data, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(configPath, data, 0644)
}

// DatabasePath returns the path to the offline queue database
func (c *Config) DatabasePath() string {
	return filepath.Join(c.DataDir, "offline_queue.db")
}

// LogPath returns the path to the log file
func (c *Config) LogPath() string {
	return filepath.Join(c.DataDir, "agent.log")
}

// StatusPath returns the path to the status file
func (c *Config) StatusPath() string {
	return filepath.Join(c.DataDir, "status.json")
}
