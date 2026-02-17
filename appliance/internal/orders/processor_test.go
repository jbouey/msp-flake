package orders

import (
	"context"
	"os"
	"path/filepath"
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

func TestProcessSyncPromotedRule(t *testing.T) {
	dir := t.TempDir()
	p := NewProcessor(dir, nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-006",
		OrderType: "sync_promoted_rule",
		Parameters: map[string]interface{}{
			"rule_id":   "L1-PROMOTED-ABC123",
			"rule_yaml": "id: L1-PROMOTED-ABC123\nname: Test\n",
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

	params := map[string]interface{}{
		"rule_id":   "L1-PROMOTED-DUP",
		"rule_yaml": "id: L1-PROMOTED-DUP\nname: Test\n",
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
	p := NewProcessor("/tmp/test", nil)

	result := p.Process(context.Background(), &Order{
		OrderID:   "ord-010",
		OrderType: "healing",
		Parameters: map[string]interface{}{
			"runbook_id": "RB-WIN-SEC-001",
		},
	})

	if !result.Success {
		t.Fatalf("expected success, got error: %s", result.Error)
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

	// Files should be cleaned up
	if _, err := os.Stat(pendingPath); err == nil {
		t.Fatal("pending file should be removed")
	}
	if _, err := os.Stat(markerPath); err == nil {
		t.Fatal("marker file should be removed")
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
