package l2planner

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/osiriscare/appliance/internal/l2bridge"
)

// mockCentralCommand creates a test server that mimics Central Command's /api/agent/l2/plan.
func mockCentralCommand(t *testing.T, response l2PlanResponse) *httptest.Server {
	t.Helper()

	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify request format
		if r.URL.Path != "/api/agent/l2/plan" {
			t.Errorf("Wrong path: %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Errorf("Wrong method: %s", r.Method)
		}
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
			t.Error("Missing Bearer auth header")
		}

		// Verify request body is valid and PHI was scrubbed
		var planReq l2PlanRequest
		if err := json.NewDecoder(r.Body).Decode(&planReq); err != nil {
			t.Errorf("Invalid request body: %v", err)
		}

		// Verify PHI was scrubbed before reaching Central Command
		for _, v := range planReq.RawData {
			if str, ok := v.(string); ok {
				if strings.Contains(str, "123-45-6789") {
					t.Error("SSN leaked to Central Command!")
				}
				if strings.Contains(str, "patient@hospital.com") {
					t.Error("Email leaked to Central Command!")
				}
			}
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(response)
	}))
}

func TestPlannerEndToEnd(t *testing.T) {
	server := mockCentralCommand(t, l2PlanResponse{
		IncidentID:        "drift-DC01-firewall-123",
		RecommendedAction: "configure_firewall",
		ActionParams:      map[string]interface{}{"runbook_id": "RB-FIREWALL-001"},
		Confidence:        0.9,
		Reasoning:         "Firewall is disabled, selecting firewall compliance runbook",
		RunbookID:         "RB-FIREWALL-001",
		RequiresApproval:  false,
		EscalateToL3:      false,
		ContextUsed: map[string]interface{}{
			"llm_model":      "claude-sonnet-4-20250514",
			"llm_latency_ms": float64(1500),
		},
	})
	defer server.Close()

	planner := NewPlanner(PlannerConfig{
		APIKey:      "test-site-api-key",
		APIEndpoint: server.URL,
		SiteID:      "test-site",
		Budget:      DefaultBudgetConfig(),
	})

	incident := &l2bridge.Incident{
		ID:           "drift-DC01-firewall-123",
		SiteID:       "test-site",
		HostID:       "DC01",
		IncidentType: "firewall_status",
		Severity:     "high",
		CreatedAt:    "2026-02-21T10:00:00Z",
		RawData: map[string]interface{}{
			"check_type":     "firewall_status",
			"drift_detected": true,
			"hostname":       "DC01",
			"patient_note":   "Patient SSN 123-45-6789, email patient@hospital.com",
			"ip_address":     "192.168.88.100",
		},
	}

	decision, err := planner.Plan(incident)
	if err != nil {
		t.Fatalf("Plan error: %v", err)
	}

	if decision.RecommendedAction != "configure_firewall" {
		t.Errorf("Wrong action: %s", decision.RecommendedAction)
	}
	if decision.Confidence != 0.9 {
		t.Errorf("Wrong confidence: %f", decision.Confidence)
	}
	if !decision.ShouldExecute() {
		t.Error("Should be auto-executable")
	}
	if decision.EscalateToL3 {
		t.Error("Should not escalate")
	}

	// Verify budget was tracked
	stats := planner.Stats()
	if stats.HourlyCalls != 1 {
		t.Errorf("Expected 1 hourly call, got %d", stats.HourlyCalls)
	}
}

func TestPlannerGuardrailBlocks(t *testing.T) {
	server := mockCentralCommand(t, l2PlanResponse{
		RecommendedAction: "format_disk", // NOT in allowlist
		ActionParams:      map[string]interface{}{"script": "mkfs.ext4 /dev/sda"},
		Confidence:        0.95,
		Reasoning:         "Disk needs formatting",
		EscalateToL3:      false,
	})
	defer server.Close()

	planner := NewPlanner(PlannerConfig{
		APIKey:      "test-key",
		APIEndpoint: server.URL,
		SiteID:      "test-site",
		Budget:      DefaultBudgetConfig(),
	})

	incident := &l2bridge.Incident{
		ID:           "test-1",
		SiteID:       "test-site",
		HostID:       "host-1",
		IncidentType: "disk_issue",
		Severity:     "high",
		RawData:      map[string]interface{}{},
	}

	decision, err := planner.Plan(incident)
	if err != nil {
		t.Fatalf("Plan error: %v", err)
	}

	// Guardrails should have forced escalation
	if !decision.EscalateToL3 {
		t.Error("Guardrails should have forced escalation for unknown action")
	}
	if decision.ShouldExecute() {
		t.Error("Should not auto-execute when guardrails block")
	}
}

func TestPlannerBudgetExhausted(t *testing.T) {
	planner := NewPlanner(PlannerConfig{
		APIKey:      "test-key",
		APIEndpoint: "http://unused",
		SiteID:      "test-site",
		Budget: BudgetConfig{
			DailyBudgetUSD:     0.0001, // tiny budget
			MaxCallsPerHour:    1000,
			MaxConcurrentCalls: 3,
		},
	})

	// Exhaust the budget
	planner.budget.RecordCost(1_000_000, 1_000_000)

	incident := &l2bridge.Incident{
		ID:           "test-1",
		IncidentType: "test",
		RawData:      map[string]interface{}{},
	}

	_, err := planner.Plan(incident)
	if err == nil {
		t.Error("Should fail when budget exhausted")
	}
	if !strings.Contains(err.Error(), "budget") {
		t.Errorf("Error should mention budget: %v", err)
	}
}

func TestPlannerCentralCommandError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
		w.Write([]byte(`{"detail": "L2 LLM not configured (no API key)"}`))
	}))
	defer server.Close()

	planner := NewPlanner(PlannerConfig{
		APIKey:      "test-key",
		APIEndpoint: server.URL,
		SiteID:      "test-site",
		Budget:      DefaultBudgetConfig(),
	})

	incident := &l2bridge.Incident{
		ID:           "test-1",
		IncidentType: "test",
		RawData:      map[string]interface{}{},
	}

	_, err := planner.Plan(incident)
	if err == nil {
		t.Error("Should fail on Central Command error")
	}
	if !strings.Contains(err.Error(), "503") {
		t.Errorf("Error should mention status code: %v", err)
	}
}

func TestPlannerIsConnected(t *testing.T) {
	p1 := NewPlanner(PlannerConfig{APIKey: "has-key", APIEndpoint: "https://api.example.com"})
	if !p1.IsConnected() {
		t.Error("Should be connected with API key + endpoint")
	}

	p2 := NewPlanner(PlannerConfig{APIKey: "", APIEndpoint: "https://api.example.com"})
	if p2.IsConnected() {
		t.Error("Should not be connected without API key")
	}
}

func TestPlanWithRetry(t *testing.T) {
	attempts := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		attempts++
		if attempts < 2 {
			w.WriteHeader(http.StatusInternalServerError)
			w.Write([]byte(`{"detail": "temporary error"}`))
			return
		}

		json.NewEncoder(w).Encode(l2PlanResponse{
			RecommendedAction: "restart_service",
			ActionParams:      map[string]interface{}{},
			Confidence:        0.8,
			Reasoning:         "SSH needs restart",
			RunbookID:         "RB-SERVICE-001",
		})
	}))
	defer server.Close()

	planner := NewPlanner(PlannerConfig{
		APIKey:      "test-key",
		APIEndpoint: server.URL,
		SiteID:      "test-site",
		Budget:      DefaultBudgetConfig(),
	})

	incident := &l2bridge.Incident{
		ID:           "test-retry",
		IncidentType: "linux_ssh_config",
		RawData:      map[string]interface{}{},
	}

	decision, err := planner.PlanWithRetry(incident, 2)
	if err != nil {
		t.Fatalf("PlanWithRetry should succeed after retry: %v", err)
	}
	if decision.RecommendedAction != "restart_service" {
		t.Errorf("Wrong action: %s", decision.RecommendedAction)
	}
	if attempts != 2 {
		t.Errorf("Expected 2 attempts, got %d", attempts)
	}
}

func TestPlannerPHIScrubbing(t *testing.T) {
	var receivedReq l2PlanRequest

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&receivedReq)

		json.NewEncoder(w).Encode(l2PlanResponse{
			RecommendedAction: "escalate",
			Confidence:        0.3,
			Reasoning:         "Unknown issue",
			EscalateToL3:      true,
			ActionParams:      map[string]interface{}{},
		})
	}))
	defer server.Close()

	planner := NewPlanner(PlannerConfig{
		APIKey:      "test-key",
		APIEndpoint: server.URL,
		SiteID:      "test-site",
		Budget:      DefaultBudgetConfig(),
	})

	incident := &l2bridge.Incident{
		ID:           "phi-test",
		IncidentType: "firewall_status",
		HostID:       "DC01",
		SiteID:       "test",
		RawData: map[string]interface{}{
			"ssn_field":    "SSN is 999-88-7777",
			"email_field":  "Contact admin@hospital.com",
			"ip_field":     "Server 192.168.88.100",
			"phone_field":  "Call (555) 123-4567",
			"normal_field": "firewall_status drift",
		},
	}

	planner.Plan(incident)

	// PHI should be scrubbed in what reached Central Command
	for k, v := range receivedReq.RawData {
		str, ok := v.(string)
		if !ok {
			continue
		}
		if strings.Contains(str, "999-88-7777") {
			t.Errorf("SSN leaked to Central Command in %s", k)
		}
		if strings.Contains(str, "admin@hospital.com") {
			t.Errorf("Email leaked to Central Command in %s", k)
		}
		if strings.Contains(str, "(555) 123-4567") {
			t.Errorf("Phone leaked to Central Command in %s", k)
		}
	}

	// IPs should be preserved
	ipField, _ := receivedReq.RawData["ip_field"].(string)
	if !strings.Contains(ipField, "192.168.88.100") {
		t.Error("IP address was incorrectly scrubbed")
	}

	// Infra data should be preserved
	normalField, _ := receivedReq.RawData["normal_field"].(string)
	if normalField != "firewall_status drift" {
		t.Error("Infra data was incorrectly scrubbed")
	}
}

func TestPlannerClose(t *testing.T) {
	p := NewPlanner(DefaultPlannerConfig())
	// Should not panic
	p.Close()
}
