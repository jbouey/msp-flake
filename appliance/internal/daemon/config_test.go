package daemon

import (
	"os"
	"path/filepath"
	"strings"
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

// TestUpdateAPIKey_TopLevelReplace confirms the happy path — the top-level
// api_key is replaced and the round-trip still parses cleanly.
func TestUpdateAPIKey_TopLevelReplace(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.yaml")
	original := `site_id: "abc"
api_key: "old-key"
poll_interval: 60
`
	if err := os.WriteFile(cfgPath, []byte(original), 0o644); err != nil {
		t.Fatal(err)
	}

	if err := UpdateAPIKey(cfgPath, "new-key-xyz"); err != nil {
		t.Fatalf("UpdateAPIKey: %v", err)
	}

	cfg, err := LoadConfig(cfgPath)
	if err != nil {
		t.Fatalf("reload after rekey: %v", err)
	}
	if cfg.APIKey != "new-key-xyz" {
		t.Fatalf("expected new-key-xyz, got %q", cfg.APIKey)
	}
	if cfg.SiteID != "abc" {
		t.Fatalf("site_id was clobbered: %q", cfg.SiteID)
	}
	if cfg.PollInterval != 60 {
		t.Fatalf("poll_interval was clobbered: %d", cfg.PollInterval)
	}
}

// TestUpdateAPIKey_NestedKeyNotTouched is the regression test for the .226
// incident: the old line-prefix matcher mutated nested `config.api_key:`
// subkeys. The yaml.Node-based implementation only touches the top level.
func TestUpdateAPIKey_NestedKeyNotTouched(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.yaml")
	// A nested block that has its own api_key field inside a sub-map. The
	// old line-prefix regex would have replaced this sub-key and corrupted
	// indentation. The yaml.Node walk ignores it.
	original := `site_id: "abc"
api_key: "outer-old"
downstream:
  api_key: "inner-must-survive"
  endpoint: "https://downstream.example"
poll_interval: 60
`
	if err := os.WriteFile(cfgPath, []byte(original), 0o644); err != nil {
		t.Fatal(err)
	}

	if err := UpdateAPIKey(cfgPath, "outer-new"); err != nil {
		t.Fatalf("UpdateAPIKey: %v", err)
	}

	// Re-read raw bytes and verify the nested key is untouched.
	got, err := os.ReadFile(cfgPath)
	if err != nil {
		t.Fatal(err)
	}
	s := string(got)
	if !strings.Contains(s, `inner-must-survive`) {
		t.Fatalf("nested api_key was corrupted by UpdateAPIKey!\nfile:\n%s", s)
	}
	if !strings.Contains(s, `endpoint: "https://downstream.example"`) &&
		!strings.Contains(s, `endpoint: https://downstream.example`) {
		t.Fatalf("sibling key in nested block was lost:\n%s", s)
	}

	cfg, err := LoadConfig(cfgPath)
	if err != nil {
		t.Fatalf("reload after rekey: %v", err)
	}
	if cfg.APIKey != "outer-new" {
		t.Fatalf("outer api_key not replaced: %q", cfg.APIKey)
	}
}

// TestUpdateAPIKey_AppendMissing confirms that a config without api_key gets
// one appended rather than silently no-op'd.
func TestUpdateAPIKey_AppendMissing(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.yaml")
	original := `site_id: "abc"
poll_interval: 60
`
	if err := os.WriteFile(cfgPath, []byte(original), 0o644); err != nil {
		t.Fatal(err)
	}

	if err := UpdateAPIKey(cfgPath, "minted-key"); err != nil {
		t.Fatalf("UpdateAPIKey: %v", err)
	}

	cfg, err := LoadConfig(cfgPath)
	if err != nil {
		t.Fatalf("reload after rekey: %v", err)
	}
	if cfg.APIKey != "minted-key" {
		t.Fatalf("expected minted-key, got %q", cfg.APIKey)
	}
}

// TestUpdateAPIKey_AtomicTempCleanup confirms that even when the rename step
// succeeds, we don't leave a .tmp file behind.
func TestUpdateAPIKey_AtomicTempCleanup(t *testing.T) {
	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.yaml")
	os.WriteFile(cfgPath, []byte("site_id: s\napi_key: k\n"), 0o644)

	if err := UpdateAPIKey(cfgPath, "k2"); err != nil {
		t.Fatalf("UpdateAPIKey: %v", err)
	}
	if _, err := os.Stat(cfgPath + ".tmp"); !os.IsNotExist(err) {
		t.Fatalf(".tmp file was left behind: err=%v", err)
	}
}

// TestParseOverrideExecStart_LastNonEmptyWins locks in the systemd override
// format semantics: the LAST non-empty ExecStart= line defines the effective
// command, which is the pattern update_daemon fleet orders write.
func TestParseOverrideExecStart_LastNonEmptyWins(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "override.conf")
	content := `[Service]
ExecStart=
ExecStart=/var/lib/msp/appliance-daemon --config /var/lib/msp/config.yaml
`
	os.WriteFile(p, []byte(content), 0o644)

	got := parseOverrideExecStart(p)
	want := "/var/lib/msp/appliance-daemon"
	if got != want {
		t.Fatalf("parseOverrideExecStart: got %q want %q", got, want)
	}
}

// TestParseOverrideExecStart_EmptyFile returns "" cleanly.
func TestParseOverrideExecStart_EmptyFile(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "override.conf")
	os.WriteFile(p, []byte(""), 0o644)

	if got := parseOverrideExecStart(p); got != "" {
		t.Fatalf("empty override.conf should return empty string, got %q", got)
	}
}

// TestParseOverrideExecStart_MissingFile returns "" without error.
func TestParseOverrideExecStart_MissingFile(t *testing.T) {
	got := parseOverrideExecStart("/nonexistent/path/override.conf")
	if got != "" {
		t.Fatalf("missing file should return empty string, got %q", got)
	}
}
