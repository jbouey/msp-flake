package l2planner

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/osiriscare/appliance/internal/l2bridge"
)

func TestTelemetryReport(t *testing.T) {
	var receivedReport ExecutionReport
	var receivedAuth string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/agent/executions" {
			t.Errorf("Wrong path: %s", r.URL.Path)
		}
		if r.Method != http.MethodPost {
			t.Errorf("Wrong method: %s", r.Method)
		}

		receivedAuth = r.Header.Get("Authorization")

		err := json.NewDecoder(r.Body).Decode(&receivedReport)
		if err != nil {
			t.Errorf("Decode error: %v", err)
		}

		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	reporter := NewTelemetryReporter(server.URL, "test-api-key", "test-site")

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

	// Verify the report
	if receivedReport.SiteID != "test-site" {
		t.Errorf("Wrong site_id: %s", receivedReport.SiteID)
	}
	if receivedReport.IncidentID != "drift-DC01-firewall-123" {
		t.Errorf("Wrong incident_id: %s", receivedReport.IncidentID)
	}
	if receivedReport.Action != "configure_firewall" {
		t.Errorf("Wrong action: %s", receivedReport.Action)
	}
	if !receivedReport.Success {
		t.Error("Should be success")
	}
	if receivedReport.Level != "L2" {
		t.Errorf("Wrong level: %s", receivedReport.Level)
	}
	if receivedReport.DurationMs != 1500 {
		t.Errorf("Wrong duration: %d", receivedReport.DurationMs)
	}
	if receivedReport.InputTokens != 2000 {
		t.Errorf("Wrong input tokens: %d", receivedReport.InputTokens)
	}
	if receivedReport.CostUSD <= 0 {
		t.Error("Cost should be > 0")
	}
	if receivedAuth != "Bearer test-api-key" {
		t.Errorf("Wrong auth header: %s", receivedAuth)
	}
}

func TestTelemetryReportFailure(t *testing.T) {
	var receivedReport ExecutionReport

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&receivedReport)
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

	if receivedReport.Success {
		t.Error("Should report failure")
	}
	if receivedReport.Error != "SSH connection refused" {
		t.Errorf("Wrong error: %s", receivedReport.Error)
	}
}

func TestTelemetryServerDown(t *testing.T) {
	// Should not panic when server is unreachable
	reporter := NewTelemetryReporter("http://localhost:1", "key", "site")

	incident := &l2bridge.Incident{ID: "test-1", IncidentType: "test"}
	decision := &l2bridge.LLMDecision{ActionParams: map[string]interface{}{}}

	// This should log an error but not panic
	reporter.ReportExecution(incident, decision, false, "test", 0, 0, 0)
}
