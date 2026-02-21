package l2planner

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/osiriscare/appliance/internal/l2bridge"
)

// mockAnthropicServer creates a test server that mimics the Anthropic Messages API.
func mockAnthropicServer(t *testing.T, response LLMResponsePayload) *httptest.Server {
	t.Helper()

	respJSON, _ := json.Marshal(response)

	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Verify request format
		if r.URL.Path != "/v1/messages" {
			t.Errorf("Wrong path: %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Errorf("Wrong method: %s", r.Method)
		}
		if r.Header.Get("x-api-key") == "" {
			t.Error("Missing x-api-key header")
		}
		if r.Header.Get("anthropic-version") != "2023-06-01" {
			t.Errorf("Wrong anthropic-version: %s", r.Header.Get("anthropic-version"))
		}

		// Verify request body is valid
		var apiReq AnthropicRequest
		if err := json.NewDecoder(r.Body).Decode(&apiReq); err != nil {
			t.Errorf("Invalid request body: %v", err)
		}

		// Verify PHI was scrubbed from the request
		userMsg := apiReq.Messages[0].Content
		if strings.Contains(userMsg, "123-45-6789") {
			t.Error("SSN leaked through to API request!")
		}
		if strings.Contains(userMsg, "patient@hospital.com") {
			t.Error("Email leaked through to API request!")
		}

		// Return mock response
		apiResp := AnthropicResponse{
			ID:   "msg_test_123",
			Type: "message",
			Role: "assistant",
			Content: []struct {
				Type string `json:"type"`
				Text string `json:"text"`
			}{
				{Type: "text", Text: string(respJSON)},
			},
			Model:      "claude-haiku-4-5-20251001",
			StopReason: "end_turn",
		}
		apiResp.Usage.InputTokens = 500
		apiResp.Usage.OutputTokens = 200

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(apiResp)
	}))
}

func TestPlannerEndToEnd(t *testing.T) {
	server := mockAnthropicServer(t, LLMResponsePayload{
		RecommendedAction: "configure_firewall",
		ActionParams:      map[string]interface{}{"script": "Set-NetFirewallProfile -Profile Domain -Enabled True"},
		Confidence:        0.9,
		Reasoning:         "Firewall is disabled, needs re-enabling",
		RequiresApproval:  false,
		EscalateToL3:      false,
		RunbookID:         "L2-AUTO-firewall_status",
	})
	defer server.Close()

	planner := NewPlanner(PlannerConfig{
		APIKey:      "test-key",
		APIEndpoint: server.URL,
		APIModel:    "claude-haiku-4-5-20251001",
		MaxTokens:   1024,
		Budget:      DefaultBudgetConfig(),
		SiteID:      "test-site",
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
	if stats.DailySpendUSD <= 0 {
		t.Error("Budget should have recorded spend")
	}
	if stats.HourlyCalls != 1 {
		t.Errorf("Expected 1 hourly call, got %d", stats.HourlyCalls)
	}

	// Verify context metadata
	if decision.ContextUsed == nil {
		t.Error("Missing context_used metadata")
	}
}

func TestPlannerGuardrailBlocks(t *testing.T) {
	server := mockAnthropicServer(t, LLMResponsePayload{
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

func TestPlannerAPIError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusTooManyRequests)
		w.Write([]byte(`{"error": {"type": "rate_limit_error", "message": "Too many requests"}}`))
	}))
	defer server.Close()

	planner := NewPlanner(PlannerConfig{
		APIKey:      "test-key",
		APIEndpoint: server.URL,
		Budget:      DefaultBudgetConfig(),
	})

	incident := &l2bridge.Incident{
		ID:           "test-1",
		IncidentType: "test",
		RawData:      map[string]interface{}{},
	}

	_, err := planner.Plan(incident)
	if err == nil {
		t.Error("Should fail on API error")
	}
	if !strings.Contains(err.Error(), "429") {
		t.Errorf("Error should mention status code: %v", err)
	}
}

func TestPlannerIsConnected(t *testing.T) {
	p1 := NewPlanner(PlannerConfig{APIKey: "has-key"})
	if !p1.IsConnected() {
		t.Error("Should be connected with API key")
	}

	p2 := NewPlanner(PlannerConfig{APIKey: ""})
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
			w.Write([]byte(`{"error": "temporary"}`))
			return
		}

		resp := LLMResponsePayload{
			RecommendedAction: "restart_service",
			ActionParams:      map[string]interface{}{"script": "systemctl restart sshd"},
			Confidence:        0.8,
			Reasoning:         "SSH needs restart",
		}
		respJSON, _ := json.Marshal(resp)

		apiResp := AnthropicResponse{
			Content: []struct {
				Type string `json:"type"`
				Text string `json:"text"`
			}{
				{Type: "text", Text: string(respJSON)},
			},
		}
		apiResp.Usage.InputTokens = 100
		apiResp.Usage.OutputTokens = 50

		json.NewEncoder(w).Encode(apiResp)
	}))
	defer server.Close()

	planner := NewPlanner(PlannerConfig{
		APIKey:      "test-key",
		APIEndpoint: server.URL,
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
	var receivedBody AnthropicRequest

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&receivedBody)

		resp := LLMResponsePayload{
			RecommendedAction: "escalate",
			Confidence:        0.3,
			Reasoning:         "Unknown issue",
			EscalateToL3:      true,
			ActionParams:      map[string]interface{}{},
		}
		respJSON, _ := json.Marshal(resp)

		apiResp := AnthropicResponse{
			Content: []struct {
				Type string `json:"type"`
				Text string `json:"text"`
			}{
				{Type: "text", Text: string(respJSON)},
			},
		}
		apiResp.Usage.InputTokens = 100
		apiResp.Usage.OutputTokens = 50

		json.NewEncoder(w).Encode(apiResp)
	}))
	defer server.Close()

	planner := NewPlanner(PlannerConfig{
		APIKey:      "test-key",
		APIEndpoint: server.URL,
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

	// Check what was sent to the API
	userMsg := receivedBody.Messages[0].Content

	// PHI should be scrubbed
	if strings.Contains(userMsg, "999-88-7777") {
		t.Error("SSN leaked to API")
	}
	if strings.Contains(userMsg, "admin@hospital.com") {
		t.Error("Email leaked to API")
	}
	if strings.Contains(userMsg, "(555) 123-4567") {
		t.Error("Phone leaked to API")
	}

	// IPs should be preserved
	if !strings.Contains(userMsg, "192.168.88.100") {
		t.Error("IP address was incorrectly scrubbed")
	}

	// Infra data should be preserved
	if !strings.Contains(userMsg, "firewall_status drift") {
		t.Error("Infra data was incorrectly scrubbed")
	}

	// Redaction tags should be present
	if !strings.Contains(userMsg, "[SSN-REDACTED-") {
		t.Error("Missing SSN redaction tag")
	}
	if !strings.Contains(userMsg, "[EMAIL-REDACTED-") {
		t.Error("Missing email redaction tag")
	}
}

func TestPlannerClose(t *testing.T) {
	p := NewPlanner(DefaultPlannerConfig())
	// Should not panic
	p.Close()
}
