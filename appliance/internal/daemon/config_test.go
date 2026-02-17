package daemon

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDefaultConfig(t *testing.T) {
	cfg := DefaultConfig()
	if cfg.APIEndpoint != "https://api.osiriscare.net" {
		t.Fatalf("unexpected api_endpoint: %s", cfg.APIEndpoint)
	}
	if cfg.PollInterval != 60 {
		t.Fatalf("unexpected poll_interval: %d", cfg.PollInterval)
	}
	if !cfg.HealingEnabled {
		t.Fatal("healing should be enabled by default")
	}
	if cfg.GRPCPort != 50051 {
		t.Fatalf("unexpected grpc_port: %d", cfg.GRPCPort)
	}
}

func TestLoadConfig(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.yaml")

	content := `
site_id: "north-valley-01"
api_key: "test-key-123"
api_endpoint: "https://test.osiriscare.net"
poll_interval: 30
healing_enabled: true
healing_dry_run: true
l2_enabled: false
grpc_port: 50052
`
	os.WriteFile(cfgPath, []byte(content), 0o644)

	cfg, err := LoadConfig(cfgPath)
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}

	if cfg.SiteID != "north-valley-01" {
		t.Fatalf("unexpected site_id: %s", cfg.SiteID)
	}
	if cfg.APIKey != "test-key-123" {
		t.Fatalf("unexpected api_key: %s", cfg.APIKey)
	}
	if cfg.APIEndpoint != "https://test.osiriscare.net" {
		t.Fatalf("unexpected api_endpoint: %s", cfg.APIEndpoint)
	}
	if cfg.PollInterval != 30 {
		t.Fatalf("unexpected poll_interval: %d", cfg.PollInterval)
	}
	if !cfg.HealingDryRun {
		t.Fatal("healing_dry_run should be true")
	}
	if cfg.GRPCPort != 50052 {
		t.Fatalf("unexpected grpc_port: %d", cfg.GRPCPort)
	}
}

func TestLoadConfigMissingSiteID(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.yaml")
	os.WriteFile(cfgPath, []byte(`api_key: "key"`), 0o644)

	_, err := LoadConfig(cfgPath)
	if err == nil {
		t.Fatal("expected error for missing site_id")
	}
}

func TestLoadConfigMissingAPIKey(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.yaml")
	os.WriteFile(cfgPath, []byte(`site_id: "site"`), 0o644)

	_, err := LoadConfig(cfgPath)
	if err == nil {
		t.Fatal("expected error for missing api_key")
	}
}

func TestLoadConfigPollIntervalClamping(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.yaml")

	// Too low
	os.WriteFile(cfgPath, []byte(`site_id: "s"
api_key: "k"
poll_interval: 1`), 0o644)

	cfg, _ := LoadConfig(cfgPath)
	if cfg.PollInterval != 10 {
		t.Fatalf("expected clamped to 10, got %d", cfg.PollInterval)
	}

	// Too high
	os.WriteFile(cfgPath, []byte(`site_id: "s"
api_key: "k"
poll_interval: 9999`), 0o644)

	cfg, _ = LoadConfig(cfgPath)
	if cfg.PollInterval != 3600 {
		t.Fatalf("expected clamped to 3600, got %d", cfg.PollInterval)
	}
}

func TestLoadConfigEnvOverrides(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.yaml")
	os.WriteFile(cfgPath, []byte(`site_id: "s"
api_key: "k"
healing_dry_run: false
log_level: INFO`), 0o644)

	t.Setenv("HEALING_DRY_RUN", "true")
	t.Setenv("LOG_LEVEL", "debug")

	cfg, err := LoadConfig(cfgPath)
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}

	if !cfg.HealingDryRun {
		t.Fatal("env override should set healing_dry_run=true")
	}
	if cfg.LogLevel != "DEBUG" {
		t.Fatalf("env override should set log_level=DEBUG, got %s", cfg.LogLevel)
	}
}

func TestConfigPaths(t *testing.T) {
	cfg := &Config{StateDir: "/var/lib/msp"}

	if cfg.EvidenceDir() != "/var/lib/msp/evidence" {
		t.Fatalf("unexpected evidence dir: %s", cfg.EvidenceDir())
	}
	if cfg.QueueDBPath() != "/var/lib/msp/queue.db" {
		t.Fatalf("unexpected queue db: %s", cfg.QueueDBPath())
	}
	if cfg.RulesDir() != "/var/lib/msp/rules" {
		t.Fatalf("unexpected rules dir: %s", cfg.RulesDir())
	}
}
