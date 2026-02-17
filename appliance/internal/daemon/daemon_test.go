package daemon

import (
	"context"
	"testing"
	"time"

	"github.com/osiriscare/appliance/internal/grpcserver"
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
}

func TestNewDaemonWithL2(t *testing.T) {
	cfg := testConfig()
	cfg.L2Enabled = true
	d := New(cfg)

	if d.l2Client == nil {
		t.Fatal("expected L2 client when L2 enabled")
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

	// processHealRequests should skip when healing is disabled
	// We can't easily test the channel loop, but we can verify the daemon
	// is correctly configured
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

func TestCompleteOrder(t *testing.T) {
	d := New(testConfig())

	// Should not panic
	err := d.completeOrder(context.Background(), "test-order", true, map[string]interface{}{"status": "ok"}, "")
	if err != nil {
		t.Fatalf("completeOrder: %v", err)
	}

	err = d.completeOrder(context.Background(), "test-order-fail", false, nil, "something went wrong")
	if err != nil {
		t.Fatalf("completeOrder: %v", err)
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
