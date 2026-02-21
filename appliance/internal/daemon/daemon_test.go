package daemon

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/osiriscare/appliance/internal/grpcserver"
	"github.com/osiriscare/appliance/internal/l2bridge"
)

func testConfig() *Config {
	cfg := DefaultConfig()
	cfg.SiteID = "test-site"
	cfg.APIKey = "test-key"
	cfg.StateDir = "/tmp/daemon-test"
	cfg.CADir = ""
	cfg.HealingEnabled = true
	cfg.HealingDryRun = true
	cfg.L2Enabled = false
	return &cfg
}

func TestNewDaemon(t *testing.T) {
	d := New(testConfig())
	if d == nil {
		t.Fatal("expected non-nil daemon")
	}
	if d.l1Engine == nil {
		t.Fatal("expected L1 engine to be initialized")
	}
	if d.orderProc == nil {
		t.Fatal("expected order processor to be initialized")
	}
	if d.l2Client != nil {
		t.Fatal("expected L2 client to be nil when L2 disabled")
	}
	if d.winrmExec == nil {
		t.Fatal("expected WinRM executor to be initialized")
	}
	if d.sshExec == nil {
		t.Fatal("expected SSH executor to be initialized")
	}
}

func TestNewDaemonWithL2(t *testing.T) {
	cfg := testConfig()
	cfg.L2Enabled = true
	d := New(cfg)

	if d.l2Planner == nil {
		t.Fatal("expected L2 planner when L2 enabled")
	}
}

func TestNewDaemonDryRun(t *testing.T) {
	cfg := testConfig()
	cfg.HealingDryRun = true
	d := New(cfg)

	// Dry run should result in nil executor on L1 engine (dry-run mode)
	if d.l1Engine == nil {
		t.Fatal("expected L1 engine")
	}
	if d.l1Engine.RuleCount() == 0 {
		t.Fatal("expected builtin rules to be loaded")
	}
}

func TestHealIncidentL1Match(t *testing.T) {
	d := New(testConfig())

	// Create a heal request that matches builtin rule L1-FW-001
	// L1-FW-001 conditions: check_type=="firewall", drift_detected==true, platform!="nixos"
	// healIncident() sets check_type=CheckType, drift_detected=true, platform="windows"
	req := grpcserver.HealRequest{
		AgentID:      "agent-1",
		Hostname:     "ws01.test.local",
		CheckType:    "firewall",
		HIPAAControl: "164.312(e)(1)",
		Expected:     "enabled",
		Actual:       "disabled",
	}

	// Should match L1-FW-001 and execute (dry-run since no executor configured)
	d.healIncident(context.Background(), req)
}

func TestHealIncidentNoMatch(t *testing.T) {
	d := New(testConfig())

	// Create a heal request that doesn't match any rule
	req := grpcserver.HealRequest{
		AgentID:   "agent-1",
		Hostname:  "ws01.test.local",
		CheckType: "unknown_check_type_xyz",
		Expected:  "something",
		Actual:    "other",
	}

	// Should not panic, should escalate to L3
	d.healIncident(context.Background(), req)
}

func TestHealIncidentHealingDisabled(t *testing.T) {
	cfg := testConfig()
	cfg.HealingEnabled = false
	d := New(cfg)

	if d.config.HealingEnabled {
		t.Fatal("healing should be disabled")
	}
}

func TestProcessOrders(t *testing.T) {
	d := New(testConfig())

	rawOrders := []map[string]interface{}{
		{
			"order_id":   "ord-001",
			"order_type": "force_checkin",
		},
		{
			"order_id":   "ord-002",
			"order_type": "run_drift",
		},
	}

	// Should not panic
	d.processOrders(context.Background(), rawOrders)
}

func TestProcessOrdersWithParams(t *testing.T) {
	d := New(testConfig())

	rawOrders := []map[string]interface{}{
		{
			"order_id":   "ord-003",
			"order_type": "healing",
			"parameters": map[string]interface{}{
				"runbook_id": "RB-WIN-SEC-001",
			},
		},
	}

	d.processOrders(context.Background(), rawOrders)
}

func TestProcessOrdersUnknownType(t *testing.T) {
	d := New(testConfig())

	rawOrders := []map[string]interface{}{
		{
			"order_id":   "ord-004",
			"order_type": "nonexistent_order_type",
		},
	}

	// Should handle gracefully
	d.processOrders(context.Background(), rawOrders)
}

func TestCompleteOrderHTTP(t *testing.T) {
	// Set up a mock HTTP server that accepts order completions
	var receivedBody map[string]interface{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		if !contains(r.URL.Path, "/api/orders/") || !contains(r.URL.Path, "/complete") {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer test-key" {
			t.Errorf("expected Bearer test-key, got %s", r.Header.Get("Authorization"))
		}

		json.NewDecoder(r.Body).Decode(&receivedBody)
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status":"ok"}`))
	}))
	defer server.Close()

	cfg := testConfig()
	cfg.APIEndpoint = server.URL
	d := New(cfg)

	err := d.completeOrder(context.Background(), "test-order-123", true, map[string]interface{}{"healed": true}, "")
	if err != nil {
		t.Fatalf("completeOrder: %v", err)
	}

	if receivedBody["success"] != true {
		t.Fatalf("expected success=true, got %v", receivedBody["success"])
	}
	result, ok := receivedBody["result"].(map[string]interface{})
	if !ok {
		t.Fatal("expected result map in body")
	}
	if result["healed"] != true {
		t.Fatalf("expected healed=true, got %v", result["healed"])
	}
}

func TestCompleteOrderHTTPFailure(t *testing.T) {
	cfg := testConfig()
	cfg.APIEndpoint = "http://127.0.0.1:1" // unreachable
	d := New(cfg)

	err := d.completeOrder(context.Background(), "test-order-fail", false, nil, "something went wrong")
	if err == nil {
		t.Fatal("expected error for unreachable server")
	}
}

func TestEscalateToL3(t *testing.T) {
	d := New(testConfig())

	req := grpcserver.HealRequest{
		AgentID:      "agent-1",
		Hostname:     "ws01",
		CheckType:    "unknown",
		HIPAAControl: "164.312(a)(1)",
	}

	// Should not panic
	d.escalateToL3("incident-123", req, "test escalation")
}

func TestBuildWinRMTarget(t *testing.T) {
	d := New(testConfig())

	// No credentials → nil
	req := grpcserver.HealRequest{
		Hostname: "ws01.test.local",
		Metadata: map[string]string{},
	}
	if d.buildWinRMTarget(req) != nil {
		t.Fatal("expected nil target without credentials")
	}

	// With credentials
	req.Metadata = map[string]string{
		"winrm_username": "DOMAIN\\admin",
		"winrm_password": "secret",
		"ip_address":     "192.168.1.10",
	}
	target := d.buildWinRMTarget(req)
	if target == nil {
		t.Fatal("expected non-nil target")
	}
	if target.Hostname != "192.168.1.10" {
		t.Fatalf("expected IP 192.168.1.10, got %s", target.Hostname)
	}
	if target.Username != "DOMAIN\\admin" {
		t.Fatalf("expected DOMAIN\\admin, got %s", target.Username)
	}
	if target.Port != 5985 {
		t.Fatalf("expected port 5985, got %d", target.Port)
	}
}

func TestBuildSSHTarget(t *testing.T) {
	d := New(testConfig())

	// No credentials → nil
	req := grpcserver.HealRequest{
		Hostname: "linux01.test.local",
		Metadata: map[string]string{},
	}
	if d.buildSSHTarget(req) != nil {
		t.Fatal("expected nil target without credentials")
	}

	// With password
	req.Metadata = map[string]string{
		"ssh_username": "admin",
		"ssh_password": "secret",
	}
	target := d.buildSSHTarget(req)
	if target == nil {
		t.Fatal("expected non-nil target")
	}
	if target.Username != "admin" {
		t.Fatalf("expected admin, got %s", target.Username)
	}
	if target.Password == nil || *target.Password != "secret" {
		t.Fatal("expected password=secret")
	}

	// With key
	req.Metadata = map[string]string{
		"ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----",
		"ip_address":      "10.0.0.5",
	}
	target = d.buildSSHTarget(req)
	if target == nil {
		t.Fatal("expected non-nil target")
	}
	if target.Username != "root" { // default when not specified
		t.Fatalf("expected root, got %s", target.Username)
	}
	if target.Hostname != "10.0.0.5" {
		t.Fatalf("expected 10.0.0.5, got %s", target.Hostname)
	}
}

func TestExecuteL2ActionNoCredentials(t *testing.T) {
	d := New(testConfig())

	decision := &l2bridge.LLMDecision{
		RecommendedAction: "Restart-Service -Name 'wuauserv'",
		Confidence:        0.85,
		RunbookID:         "L2-test",
	}

	req := grpcserver.HealRequest{
		AgentID:   "agent-1",
		Hostname:  "ws01.test.local",
		CheckType: "service_wuauserv",
		Metadata:  map[string]string{},
	}

	// Should escalate to L3 (no credentials) without panicking
	d.executeL2Action(context.Background(), decision, req, "incident-test")
}

func TestExecuteL2ActionLinuxPlatform(t *testing.T) {
	d := New(testConfig())

	decision := &l2bridge.LLMDecision{
		RecommendedAction: "systemctl restart sshd",
		Confidence:        0.90,
	}

	req := grpcserver.HealRequest{
		AgentID:   "agent-1",
		Hostname:  "linux01.test.local",
		CheckType: "ssh_config",
		Metadata: map[string]string{
			"platform":     "linux",
			"ssh_username": "root",
			"ssh_password": "password",
		},
	}

	// Will fail (can't connect) but should not panic
	d.executeL2Action(context.Background(), decision, req, "incident-linux-test")
}

func TestDaemonShutdown(t *testing.T) {
	d := New(testConfig())

	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	// Run will fail on checkin (no server) but should shutdown cleanly on context cancel
	err := d.Run(ctx)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(s) > 0 && containsAt(s, substr))
}

func containsAt(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
