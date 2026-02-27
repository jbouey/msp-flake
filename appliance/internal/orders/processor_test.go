package orders

import (
	"context"
	"crypto/ed25519"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNewProcessor(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)
	if p == nil {
		t.Fatal("expected non-nil processor")
	}
	if p.HandlerCount() != 17 {
		t.Fatalf("expected 17 handlers, got %d", p.HandlerCount())
	}
}

func TestProcessUnknownType(t *testing.T) {
	var completedID string
	var completedSuccess bool

	p := NewProcessor("/tmp/test", func(_ context.Context, orderID string, success bool, _ map[string]interface{}, _ string) error {
		completedID = orderID
		completedSuccess = success
		return nil
	})

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-001",
		OrderType: "nonexistent_type",
	})

	if result == nil {
		t.Fatal("expected result")
	}
	if result.Success {
		t.Fatal("expected failure for unknown type")
	}
	if completedID != "ord-001" {
		t.Fatalf("expected completion for ord-001, got %s", completedID)
	}
	if completedSuccess {
		t.Fatal("expected completion with success=false")
	}
}

func TestProcessMissingID(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)

	result := p.Process(context.Background(), &Order{
		OrderType: "force_checkin",
	})

	if result != nil {
		t.Fatal("expected nil result for missing order_id")
	}
}

func TestProcessMissingType(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)

	result := p.Process(context.Background(), &Order{
		OrderID: "ord-002",
	})

	if result != nil {
		t.Fatal("expected nil result for missing order_type")
	}
}

func TestProcessForceCheckin(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-003",
		OrderType: "force_checkin",
	})

	if !result.Success {
		t.Fatalf("expected success, got error: %s", result.Error)
	}
	if result.Result["status"] != "checkin_triggered" {
		t.Fatalf("unexpected status: %v", result.Result["status"])
	}
}

func TestProcessRunDrift(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-004",
		OrderType: "run_drift",
	})

	if !result.Success {
		t.Fatalf("expected success, got error: %s", result.Error)
	}
}

func TestProcessSyncRules(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-005",
		OrderType: "sync_rules",
	})

	if !result.Success {
		t.Fatalf("expected success, got error: %s", result.Error)
	}
}

// validPromotedRuleYAML is a well-formed L1 rule for testing.
const validPromotedRuleYAML = `id: L1-PROMOTED-ABC123
name: Test Rule
description: A test promoted rule
action: escalate
conditions:
  - field: incident_type
    operator: eq
    value: test_drift
severity_filter:
  - critical
cooldown_seconds: 300
`

func TestProcessSyncPromotedRule(t *testing.T) {
	dir := t.TempDir()
	p := NewProcessor(dir, nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-006",
		OrderType: "sync_promoted_rule",
		Parameters: map[string]interface{}{
			"rule_id":   "L1-PROMOTED-ABC123",
			"rule_yaml": validPromotedRuleYAML,
		},
	})

	if !result.Success {
		t.Fatalf("expected success, got error: %s", result.Error)
	}
	if result.Result["status"] != "deployed" {
		t.Fatalf("expected deployed, got %v", result.Result["status"])
	}

	// Verify file was written
	rulePath := filepath.Join(dir, "rules", "promoted", "L1-PROMOTED-ABC123.yaml")
	if _, err := os.Stat(rulePath); err != nil {
		t.Fatalf("rule file not created: %v", err)
	}
}

func TestProcessSyncPromotedRuleDuplicate(t *testing.T) {
	dir := t.TempDir()
	p := NewProcessor(dir, nil)

	dupYAML := `id: L1-PROMOTED-DUP
name: Duplicate Rule
action: escalate
conditions:
  - field: incident_type
    operator: eq
    value: test
`

	params := map[string]interface{}{
		"rule_id":   "L1-PROMOTED-DUP",
		"rule_yaml": dupYAML,
	}

	// First deploy
	p.Process(context.Background(), &Order{
		OrderID: "ord-007", OrderType: "sync_promoted_rule", Parameters: params,
	})

	// Second deploy should report already_exists
	result := p.Process(context.Background(), &Order{
		OrderID: "ord-008", OrderType: "sync_promoted_rule", Parameters: params,
	})

	if !result.Success {
		t.Fatalf("expected success, got error: %s", result.Error)
	}
	if result.Result["status"] != "already_exists" {
		t.Fatalf("expected already_exists, got %v", result.Result["status"])
	}
}

func TestProcessSyncPromotedRuleMissingFields(t *testing.T) {
	p := NewProcessor(t.TempDir(), nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-009",
		OrderType: "sync_promoted_rule",
		Parameters: map[string]interface{}{
			"rule_id": "L1-PROMOTED-X",
			// missing rule_yaml
		},
	})

	if result.Success {
		t.Fatal("expected failure for missing rule_yaml")
	}
}

func TestProcessHealing(t *testing.T) {
	// Without daemon registration, the stub returns an error to signal
	// that the real handler (executeHealingOrder) was not wired up.
	p := NewProcessor("/tmp/test", nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-010",
		OrderType: "healing",
		Parameters: map[string]interface{}{
			"runbook_id": "RB-WIN-SEC-001",
		},
	})

	if result.Success {
		t.Fatal("expected failure from unregistered healing stub")
	}

	// Verify that RegisterHandler overrides the stub
	p.RegisterHandler("healing", func(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
		return map[string]interface{}{"status": "healed", "runbook_id": params["runbook_id"]}, nil
	})

	result = p.Process(context.Background(), &Order{
		OrderID:   "ord-010b",
		OrderType: "healing",
		Parameters: map[string]interface{}{
			"runbook_id": "RB-WIN-SEC-001",
		},
	})

	if !result.Success {
		t.Fatalf("expected success after RegisterHandler, got error: %s", result.Error)
	}
	if result.Result["runbook_id"] != "RB-WIN-SEC-001" {
		t.Fatalf("expected RB-WIN-SEC-001, got %v", result.Result["runbook_id"])
	}
}

func TestProcessHealingMissingRunbook(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-011",
		OrderType: "healing",
	})

	if result.Success {
		t.Fatal("expected failure for missing runbook_id")
	}
}

func TestProcessDeploySensor(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-012",
		OrderType: "deploy_sensor",
		Parameters: map[string]interface{}{
			"hostname": "ws01.example.com",
		},
	})

	if !result.Success {
		t.Fatalf("expected success, got error: %s", result.Error)
	}
}

func TestProcessDeploySensorMissingHostname(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-013",
		OrderType: "deploy_sensor",
	})

	if result.Success {
		t.Fatal("expected failure for missing hostname")
	}
}

func TestProcessUpdateAgentMissingURL(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-014",
		OrderType: "update_agent",
	})

	if result.Success {
		t.Fatal("expected failure for missing package_url")
	}
}

func TestProcessDiagnosticWhitelist(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)

	// Disallowed command
	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-015",
		OrderType: "diagnostic",
		Parameters: map[string]interface{}{
			"command": "rm_everything",
		},
	})

	if result.Success {
		t.Fatal("expected failure for non-whitelisted command")
	}
}

func TestProcessAll(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)

	orders := []Order{
		{OrderID: "batch-1", OrderType: "force_checkin"},
		{OrderID: "batch-2", OrderType: "run_drift"},
		{OrderID: "batch-3", OrderType: "sync_rules"},
	}

	results := p.ProcessAll(context.Background(), orders)
	if len(results) != 3 {
		t.Fatalf("expected 3 results, got %d", len(results))
	}
	for _, r := range results {
		if !r.Success {
			t.Fatalf("order %s failed: %s", r.OrderID, r.Error)
		}
	}
}

func TestProcessAllCancellation(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // Cancel immediately

	orders := []Order{
		{OrderID: "cancel-1", OrderType: "force_checkin"},
		{OrderID: "cancel-2", OrderType: "run_drift"},
	}

	results := p.ProcessAll(ctx, orders)
	// Should process 0 orders due to immediate cancellation
	if len(results) > 1 {
		t.Fatalf("expected at most 1 result with cancelled context, got %d", len(results))
	}
}

func TestRegisterHandler(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)
	initial := p.HandlerCount()

	p.RegisterHandler("custom_order", func(_ context.Context, _ map[string]interface{}) (map[string]interface{}, error) {
		return map[string]interface{}{"custom": true}, nil
	})

	if p.HandlerCount() != initial+1 {
		t.Fatalf("expected %d handlers after registration, got %d", initial+1, p.HandlerCount())
	}

	result := p.Process(context.Background(), &Order{
		OrderID: "custom-1", OrderType: "custom_order",
	})

	if !result.Success {
		t.Fatalf("custom handler failed: %s", result.Error)
	}
}

func TestCompletePendingRebuild(t *testing.T) {
	dir := t.TempDir()

	// Write a pending rebuild order
	pendingPath := filepath.Join(dir, ".pending-rebuild-order")
	os.WriteFile(pendingPath, []byte("rebuild-ord-001"), 0o644)

	markerPath := filepath.Join(dir, ".rebuild-in-progress")
	os.WriteFile(markerPath, []byte(`{"timestamp":"2026-02-17T00:00:00Z"}`), 0o644)

	var completedID string
	p := NewProcessor(dir, func(_ context.Context, orderID string, success bool, _ map[string]interface{}, _ string) error {
		completedID = orderID
		return nil
	})

	p.CompletePendingRebuild(context.Background())

	if completedID != "rebuild-ord-001" {
		t.Fatalf("expected rebuild-ord-001, got %s", completedID)
	}

	// Rebuild state files should be cleaned up
	if _, err := os.Stat(pendingPath); err == nil {
		t.Fatal("pending file should be removed")
	}
	if _, err := os.Stat(markerPath); err == nil {
		t.Fatal("marker file should be removed")
	}

	// .rebuild-verified should exist (watchdog marker)
	verifiedPath := filepath.Join(dir, ".rebuild-verified")
	if _, err := os.Stat(verifiedPath); err != nil {
		t.Fatalf(".rebuild-verified should exist: %v", err)
	}
}

func TestCompletePendingRebuildNoPending(t *testing.T) {
	dir := t.TempDir()

	called := false
	p := NewProcessor(dir, func(_ context.Context, _ string, _ bool, _ map[string]interface{}, _ string) error {
		called = true
		return nil
	})

	p.CompletePendingRebuild(context.Background())

	if called {
		t.Fatal("completion callback should not be called when no pending rebuild")
	}
}

func TestProcessUpdateCredentials(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-016",
		OrderType: "update_credentials",
	})

	if !result.Success {
		t.Fatalf("expected success, got error: %s", result.Error)
	}
}

func TestProcessSensorStatus(t *testing.T) {
	p := NewProcessor("/tmp/test", nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-017",
		OrderType: "sensor_status",
	})

	if !result.Success {
		t.Fatalf("expected success, got error: %s", result.Error)
	}
}

// --- Host scoping tests ---

// signPayload is a test helper that signs a JSON payload with the given private key.
func signPayload(t *testing.T, payload map[string]interface{}, privKey ed25519.PrivateKey) (string, string) {
	t.Helper()
	payloadJSON, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("marshal payload: %v", err)
	}
	sig := ed25519.Sign(privKey, payloadJSON)
	return string(payloadJSON), hex.EncodeToString(sig)
}

func TestHostScopeMatchingAppliance(t *testing.T) {
	_, privKey, _ := ed25519.GenerateKey(nil)
	pubKeyHex := hex.EncodeToString(privKey.Public().(ed25519.PublicKey))

	p := NewProcessor("/tmp/test", nil)
	p.SetServerPublicKey(pubKeyHex)
	p.SetApplianceID("site-AA:BB:CC:DD:EE:FF")

	payload := map[string]interface{}{
		"order_id":             "host-001",
		"order_type":           "force_checkin",
		"parameters":           map[string]interface{}{},
		"nonce":                "abc123",
		"created_at":           "2026-02-24T00:00:00+00:00",
		"expires_at":           "2026-02-24T01:00:00+00:00",
		"target_appliance_id":  "site-AA:BB:CC:DD:EE:FF",
	}
	signedPayload, signature := signPayload(t, payload, privKey)

	result := p.Process(context.Background(), &Order{
		OrderID:       "host-001",
		OrderType:     "force_checkin",
		SignedPayload: signedPayload,
		Signature:     signature,
	})

	if !result.Success {
		t.Fatalf("expected success for matching appliance, got: %s", result.Error)
	}
}

func TestHostScopeMismatchedAppliance(t *testing.T) {
	_, privKey, _ := ed25519.GenerateKey(nil)
	pubKeyHex := hex.EncodeToString(privKey.Public().(ed25519.PublicKey))

	p := NewProcessor("/tmp/test", nil)
	p.SetServerPublicKey(pubKeyHex)
	p.SetApplianceID("site-AA:BB:CC:DD:EE:FF")

	// Order is signed for a DIFFERENT appliance
	payload := map[string]interface{}{
		"order_id":             "host-002",
		"order_type":           "nixos_rebuild",
		"parameters":           map[string]interface{}{},
		"nonce":                "def456",
		"created_at":           "2026-02-24T00:00:00+00:00",
		"expires_at":           "2026-02-24T01:00:00+00:00",
		"target_appliance_id":  "site-11:22:33:44:55:66",
	}
	signedPayload, signature := signPayload(t, payload, privKey)

	result := p.Process(context.Background(), &Order{
		OrderID:       "host-002",
		OrderType:     "nixos_rebuild",
		SignedPayload: signedPayload,
		Signature:     signature,
	})

	if result.Success {
		t.Fatal("expected failure for mismatched appliance ID")
	}
	if result.Error == "" {
		t.Fatal("expected error message")
	}
}

func TestHostScopeFleetOrder(t *testing.T) {
	_, privKey, _ := ed25519.GenerateKey(nil)
	pubKeyHex := hex.EncodeToString(privKey.Public().(ed25519.PublicKey))

	p := NewProcessor("/tmp/test", nil)
	p.SetServerPublicKey(pubKeyHex)
	p.SetApplianceID("site-AA:BB:CC:DD:EE:FF")

	// Fleet order — no target_appliance_id, should be allowed
	payload := map[string]interface{}{
		"order_id":   "fleet-001",
		"order_type": "force_checkin",
		"parameters": map[string]interface{}{},
		"nonce":      "ghi789",
		"created_at": "2026-02-24T00:00:00+00:00",
		"expires_at": "2026-02-24T01:00:00+00:00",
	}
	signedPayload, signature := signPayload(t, payload, privKey)

	result := p.Process(context.Background(), &Order{
		OrderID:       "fleet-001",
		OrderType:     "force_checkin",
		SignedPayload: signedPayload,
		Signature:     signature,
	})

	if !result.Success {
		t.Fatalf("expected success for fleet-wide order, got: %s", result.Error)
	}
}

// --- Parameter allowlist tests ---

func TestValidateFlakeRef(t *testing.T) {
	tests := []struct {
		name    string
		ref     string
		wantErr bool
	}{
		{"empty_uses_default", "", false},
		{"valid_official", "github:jbouey/msp-flake#osiriscare-appliance-disk", false},
		{"valid_different_output", "github:jbouey/msp-flake#some-other-output", false},
		{"malicious_repo", "github:attacker/evil-flake#exploit", true},
		{"path_injection", "github:jbouey/msp-flake/../evil#output", true},
		{"non_github", "git+https://evil.com/repo#output", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validateFlakeRef(tt.ref)
			if (err != nil) != tt.wantErr {
				t.Errorf("validateFlakeRef(%q) error=%v, wantErr=%v", tt.ref, err, tt.wantErr)
			}
		})
	}
}

func TestValidateDownloadURL(t *testing.T) {
	tests := []struct {
		name    string
		url     string
		wantErr bool
	}{
		{"valid_github", "https://github.com/jbouey/msp-flake/releases/download/v1.0/agent.tar.gz", false},
		{"valid_vps", "https://178.156.162.116/packages/agent-v2.tar.gz", false},
		{"valid_gh_objects", "https://objects.githubusercontent.com/release/agent.tar.gz", false},
		{"http_not_https", "http://github.com/jbouey/msp-flake/releases/download/v1.0/agent.tar.gz", true},
		{"evil_domain", "https://evil.com/agent.tar.gz", true},
		{"empty_url", "", true},
		{"relative_path", "/tmp/exploit.sh", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validateDownloadURL(tt.url, "test_url")
			if (err != nil) != tt.wantErr {
				t.Errorf("validateDownloadURL(%q) error=%v, wantErr=%v", tt.url, err, tt.wantErr)
			}
		})
	}
}

func TestNixOSRebuildRejectsEvilFlake(t *testing.T) {
	p := NewProcessor(t.TempDir(), nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "evil-rebuild",
		OrderType: "nixos_rebuild",
		Parameters: map[string]interface{}{
			"flake_ref": "github:attacker/rootkit#pwn",
		},
	})

	if result.Success {
		t.Fatal("expected failure for malicious flake_ref")
	}
	if !strings.Contains(result.Error, "SECURITY") {
		t.Fatalf("expected SECURITY in error, got: %s", result.Error)
	}
}

func TestUpdateAgentRejectsEvilURL(t *testing.T) {
	p := NewProcessor(t.TempDir(), nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "evil-update",
		OrderType: "update_agent",
		Parameters: map[string]interface{}{
			"package_url": "https://evil.com/backdoor.tar.gz",
			"version":     "0.0.1",
		},
	})

	if result.Success {
		t.Fatal("expected failure for evil package_url")
	}
	if !strings.Contains(result.Error, "SECURITY") {
		t.Fatalf("expected SECURITY in error, got: %s", result.Error)
	}
}

func TestSyncPromotedRuleRejectsInvalidAction(t *testing.T) {
	p := NewProcessor(t.TempDir(), nil)

	badYAML := `id: L1-BAD-ACTION
name: Evil Rule
action: exec_arbitrary_command
conditions:
  - field: incident_type
    operator: eq
    value: test
`
	result := p.Process(context.Background(), &Order{
		OrderID:   "evil-rule-1",
		OrderType: "sync_promoted_rule",
		Parameters: map[string]interface{}{
			"rule_id":   "L1-BAD-ACTION",
			"rule_yaml": badYAML,
		},
	})

	if result.Success {
		t.Fatal("expected failure for invalid action")
	}
	if !strings.Contains(result.Error, "SECURITY") {
		t.Fatalf("expected SECURITY in error, got: %s", result.Error)
	}
}

func TestSyncPromotedRuleRejectsIDMismatch(t *testing.T) {
	p := NewProcessor(t.TempDir(), nil)

	badYAML := `id: DIFFERENT-ID
name: Mismatched Rule
action: escalate
conditions:
  - field: incident_type
    operator: eq
    value: test
`
	result := p.Process(context.Background(), &Order{
		OrderID:   "evil-rule-2",
		OrderType: "sync_promoted_rule",
		Parameters: map[string]interface{}{
			"rule_id":   "L1-EXPECTED-ID",
			"rule_yaml": badYAML,
		},
	})

	if result.Success {
		t.Fatal("expected failure for ID mismatch")
	}
}

func TestSyncPromotedRuleRejectsNoConditions(t *testing.T) {
	p := NewProcessor(t.TempDir(), nil)

	badYAML := `id: L1-NO-COND
name: No conditions rule
action: escalate
`
	result := p.Process(context.Background(), &Order{
		OrderID:   "evil-rule-3",
		OrderType: "sync_promoted_rule",
		Parameters: map[string]interface{}{
			"rule_id":   "L1-NO-COND",
			"rule_yaml": badYAML,
		},
	})

	if result.Success {
		t.Fatal("expected failure for missing conditions")
	}
}

func TestSyncPromotedRuleRejectsInvalidYAML(t *testing.T) {
	p := NewProcessor(t.TempDir(), nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "evil-rule-4",
		OrderType: "sync_promoted_rule",
		Parameters: map[string]interface{}{
			"rule_id":   "L1-BAD-YAML",
			"rule_yaml": "{{{{not valid yaml!@#$",
		},
	})

	if result.Success {
		t.Fatal("expected failure for invalid YAML")
	}
}

func TestSyncPromotedRuleRejectsOversized(t *testing.T) {
	p := NewProcessor(t.TempDir(), nil)

	// Create a YAML string > 8KB
	bigYAML := "id: L1-BIG-RULE\nname: Big\naction: escalate\nconditions:\n  - field: x\n    operator: eq\n    value: " + strings.Repeat("x", 9000) + "\n"

	result := p.Process(context.Background(), &Order{
		OrderID:   "evil-rule-5",
		OrderType: "sync_promoted_rule",
		Parameters: map[string]interface{}{
			"rule_id":   "L1-BIG-RULE",
			"rule_yaml": bigYAML,
		},
	})

	if result.Success {
		t.Fatal("expected failure for oversized rule YAML")
	}
}

func TestUpdateISORejectsHTTP(t *testing.T) {
	p := NewProcessor(t.TempDir(), nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "evil-iso",
		OrderType: "update_iso",
		Parameters: map[string]interface{}{
			"iso_url": "http://github.com/jbouey/msp-flake/iso.img",
			"version": "1.0",
		},
	})

	if result.Success {
		t.Fatal("expected failure for HTTP (non-HTTPS) iso_url")
	}
}

func TestHostScopeNoApplianceIDYet(t *testing.T) {
	_, privKey, _ := ed25519.GenerateKey(nil)
	pubKeyHex := hex.EncodeToString(privKey.Public().(ed25519.PublicKey))

	p := NewProcessor("/tmp/test", nil)
	p.SetServerPublicKey(pubKeyHex)
	// Do NOT set appliance ID — simulates pre-first-checkin

	payload := map[string]interface{}{
		"order_id":             "host-003",
		"order_type":           "force_checkin",
		"parameters":           map[string]interface{}{},
		"nonce":                "jkl012",
		"created_at":           "2026-02-24T00:00:00+00:00",
		"expires_at":           "2026-02-24T01:00:00+00:00",
		"target_appliance_id":  "site-11:22:33:44:55:66",
	}
	signedPayload, signature := signPayload(t, payload, privKey)

	result := p.Process(context.Background(), &Order{
		OrderID:       "host-003",
		OrderType:     "force_checkin",
		SignedPayload: signedPayload,
		Signature:     signature,
	})

	// Should allow — we don't know our ID yet, can't enforce scoping
	if !result.Success {
		t.Fatalf("expected success when appliance ID not yet known, got: %s", result.Error)
	}
}
