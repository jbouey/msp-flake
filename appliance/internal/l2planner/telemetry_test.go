package l2planner

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/osiriscare/appliance/internal/l2bridge"
)

func TestTelemetryReport(t *testing.T) {
	var receivedPayload telemetryPayload
	var receivedAuth string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/agent/executions" {
			t.Errorf("Wrong path: %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Errorf("Wrong method: %s", r.Method)
		}

		receivedAuth = r.Header.Get("Authorization")

		err := json.NewDecoder(r.Body).Decode(&receivedPayload)
		if err != nil {
			t.Errorf("Decode error: %v", err)
		}

		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	reporter := NewTelemetryReporter(server.URL, "test-api-key", "test-site")
	reporter.SetApplianceID("test-appliance-001")

	incident := &l2bridge.Incident{
		ID:           "drift-DC01-firewall-123",
		SiteID:       "test-site",
		HostID:       "DC01",
		IncidentType: "firewall_status",
		Severity:     "high",
	}

	decision := &l2bridge.LLMDecision{
		IncidentID:        "drift-DC01-firewall-123",
		RecommendedAction: "configure_firewall",
		ActionParams:      map[string]interface{}{"script": "Set-NetFirewallProfile -Enabled True"},
		Confidence:        0.9,
		Reasoning:         "Firewall disabled, re-enabling",
		RunbookID:         "L2-AUTO-firewall_status",
	}

	reporter.ReportExecution(incident, decision, true, "", 1500, 2000, 500)

	// Verify wrapper structure
	if receivedPayload.SiteID != "test-site" {
		t.Errorf("Wrong site_id: %s", receivedPayload.SiteID)
	}
	if receivedPayload.ReportedAt == "" {
		t.Error("reported_at should not be empty")
	}

	exec := receivedPayload.Execution
	if exec.IncidentID != "drift-DC01-firewall-123" {
		t.Errorf("Wrong incident_id: %s", exec.IncidentID)
	}
	if exec.ApplianceID != "test-appliance-001" {
		t.Errorf("Wrong appliance_id: %s", exec.ApplianceID)
	}
	if !exec.Success {
		t.Error("Should be success")
	}
	if exec.ResolutionLevel != "L2" {
		t.Errorf("Wrong resolution_level: %s", exec.ResolutionLevel)
	}
	if exec.DurationSeconds != 1.5 {
		t.Errorf("Wrong duration: %f", exec.DurationSeconds)
	}
	if exec.InputTokens != 2000 {
		t.Errorf("Wrong input tokens: %d", exec.InputTokens)
	}
	if exec.OutputTokens != 500 {
		t.Errorf("Wrong output tokens: %d", exec.OutputTokens)
	}
	if exec.CostUSD <= 0 {
		t.Error("Cost should be > 0")
	}
	if exec.Confidence != 0.9 {
		t.Errorf("Wrong confidence: %f", exec.Confidence)
	}
	if exec.Reasoning != "Firewall disabled, re-enabling" {
		t.Errorf("Wrong reasoning: %s", exec.Reasoning)
	}
	if exec.PatternSignature != "firewall_status:L2-AUTO-firewall_status:DC01" {
		t.Errorf("Wrong pattern_signature: %s", exec.PatternSignature)
	}
	if exec.ExecutionID == "" {
		t.Error("execution_id should not be empty")
	}
	if receivedAuth != "Bearer test-api-key" {
		t.Errorf("Wrong auth header: %s", receivedAuth)
	}
}

func TestTelemetryReportFailure(t *testing.T) {
	var receivedPayload telemetryPayload

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&receivedPayload)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	reporter := NewTelemetryReporter(server.URL, "key", "site")

	incident := &l2bridge.Incident{
		ID:           "test-1",
		IncidentType: "linux_ssh_config",
		HostID:       "linux-1",
	}

	decision := &l2bridge.LLMDecision{
		RecommendedAction: "apply_ssh_hardening",
		ActionParams:      map[string]interface{}{"script": "fix-ssh.sh"},
		Confidence:        0.7,
		Reasoning:         "SSH config drift",
	}

	reporter.ReportExecution(incident, decision, false, "SSH connection refused", 500, 1000, 300)

	exec := receivedPayload.Execution
	if exec.Success {
		t.Error("Should report failure")
	}
	if exec.Status != "failure" {
		t.Errorf("Wrong status: %s", exec.Status)
	}
	if exec.ErrorMessage != "SSH connection refused" {
		t.Errorf("Wrong error: %s", exec.ErrorMessage)
	}
	if exec.PatternSignature != "linux_ssh_config::linux-1" {
		t.Errorf("Wrong pattern_signature: %s", exec.PatternSignature)
	}
}

func TestTelemetryReportWithPatternSignature(t *testing.T) {
	var receivedPayload telemetryPayload

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&receivedPayload)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	reporter := NewTelemetryReporter(server.URL, "key", "site")

	incident := &l2bridge.Incident{
		ID:               "test-1",
		IncidentType:     "firewall",
		HostID:           "host-1",
		PatternSignature: "custom-pattern-sig",
	}

	decision := &l2bridge.LLMDecision{
		ActionParams: map[string]interface{}{},
		Confidence:   0.8,
	}

	reporter.ReportExecution(incident, decision, true, "", 100, 0, 0)

	// Should use incident's pattern signature when available
	if receivedPayload.Execution.PatternSignature != "custom-pattern-sig" {
		t.Errorf("Should use incident pattern_signature, got: %s", receivedPayload.Execution.PatternSignature)
	}
}

func TestTelemetryServerDown(t *testing.T) {
	// Should not panic when server is unreachable
	reporter := NewTelemetryReporter("http://localhost:1", "key", "site")

	incident := &l2bridge.Incident{ID: "test-1", IncidentType: "test", HostID: "host"}
	decision := &l2bridge.LLMDecision{ActionParams: map[string]interface{}{}}

	// This should log an error but not panic
	reporter.ReportExecution(incident, decision, false, "test", 0, 0, 0)
}

func TestL1TelemetryReport(t *testing.T) {
	var receivedPayload telemetryPayload
	var receivedAuth string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/agent/executions" {
			t.Errorf("Wrong path: %s", r.URL.Path)
		}
		receivedAuth = r.Header.Get("Authorization")
		json.NewDecoder(r.Body).Decode(&receivedPayload)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	reporter := NewTelemetryReporter(server.URL, "test-key", "test-site")
	reporter.SetApplianceID("appliance-001")

	reporter.ReportL1Execution("drift-DC01-firewall-123", "DC01", "firewall_status", "RB-WIN-SEC-001", true, "", 250)

	// Verify wrapper
	if receivedPayload.SiteID != "test-site" {
		t.Errorf("Wrong site_id: %s", receivedPayload.SiteID)
	}

	exec := receivedPayload.Execution
	if exec.ResolutionLevel != "L1" {
		t.Errorf("Wrong resolution_level: %s", exec.ResolutionLevel)
	}
	if exec.IncidentType != "firewall_status" {
		t.Errorf("Wrong incident_type: %s", exec.IncidentType)
	}
	if exec.Hostname != "DC01" {
		t.Errorf("Wrong hostname: %s", exec.Hostname)
	}
	if exec.RunbookID != "RB-WIN-SEC-001" {
		t.Errorf("Wrong runbook_id: %s", exec.RunbookID)
	}
	if !exec.Success {
		t.Error("Should be success")
	}
	if exec.Status != "success" {
		t.Errorf("Wrong status: %s", exec.Status)
	}
	if exec.Confidence != 1.0 {
		t.Errorf("Wrong confidence: %f", exec.Confidence)
	}
	if exec.DurationSeconds != 0.25 {
		t.Errorf("Wrong duration: %f", exec.DurationSeconds)
	}
	if exec.CostUSD != 0 {
		t.Errorf("L1 should have zero cost, got: %f", exec.CostUSD)
	}
	if exec.PatternSignature != "firewall_status:RB-WIN-SEC-001:DC01" {
		t.Errorf("Wrong pattern_signature: %s", exec.PatternSignature)
	}
	if exec.ApplianceID != "appliance-001" {
		t.Errorf("Wrong appliance_id: %s", exec.ApplianceID)
	}
	if receivedAuth != "Bearer test-key" {
		t.Errorf("Wrong auth header: %s", receivedAuth)
	}
}

func TestL1TelemetryReportFailure(t *testing.T) {
	var receivedPayload telemetryPayload

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&receivedPayload)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	reporter := NewTelemetryReporter(server.URL, "key", "site")
	reporter.ReportL1Execution("test-1", "SRV01", "services", "RB-WIN-SVC-001", false, "service restart failed", 500)

	exec := receivedPayload.Execution
	if exec.Success {
		t.Error("Should report failure")
	}
	if exec.Status != "failure" {
		t.Errorf("Wrong status: %s", exec.Status)
	}
	if exec.ErrorMessage != "service restart failed" {
		t.Errorf("Wrong error: %s", exec.ErrorMessage)
	}
	if exec.ResolutionLevel != "L1" {
		t.Errorf("Should be L1, got: %s", exec.ResolutionLevel)
	}
}
