package l2planner

import (
	"strings"
	"testing"

	"github.com/osiriscare/appliance/internal/l2bridge"
)

func TestBuildUserPrompt(t *testing.T) {
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
			"expected":       "enabled",
			"actual":         "disabled",
		},
	}

	prompt := BuildUserPrompt(incident)

	required := []string{
		"drift-DC01-firewall-123",
		"test-site",
		"DC01",
		"firewall_status",
		"high",
		"INCIDENT DETAILS",
		"CONTEXT DATA",
	}

	for _, r := range required {
		if !strings.Contains(prompt, r) {
			t.Errorf("Prompt missing %q:\n%s", r, prompt)
		}
	}
}

func TestBuildRequest(t *testing.T) {
	incident := &l2bridge.Incident{
		ID:           "test-1",
		SiteID:       "site-1",
		HostID:       "host-1",
		IncidentType: "linux_ssh_config",
		Severity:     "medium",
		CreatedAt:    "2026-02-21T10:00:00Z",
	}

	req := BuildRequest("claude-haiku-4-5-20251001", 1024, incident)

	if req.Model != "claude-haiku-4-5-20251001" {
		t.Errorf("Wrong model: %s", req.Model)
	}
	if req.MaxTokens != 1024 {
		t.Errorf("Wrong max_tokens: %d", req.MaxTokens)
	}
	if req.System == "" {
		t.Error("Missing system prompt")
	}
	if len(req.Messages) != 1 {
		t.Errorf("Expected 1 message, got %d", len(req.Messages))
	}
	if req.Messages[0].Role != "user" {
		t.Errorf("Wrong role: %s", req.Messages[0].Role)
	}
}

func TestParseResponse(t *testing.T) {
	resp := &AnthropicResponse{
		Content: []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		}{
			{
				Type: "text",
				Text: `{
					"recommended_action": "restart_service",
					"action_params": {"script": "systemctl restart sshd"},
					"confidence": 0.85,
					"reasoning": "SSH config drift detected, restarting service to apply changes",
					"requires_approval": false,
					"escalate_to_l3": false,
					"runbook_id": "L2-AUTO-linux_ssh_config"
				}`,
			},
		},
	}

	decision, err := ParseResponse(resp, "incident-123")
	if err != nil {
		t.Fatalf("ParseResponse error: %v", err)
	}

	if decision.IncidentID != "incident-123" {
		t.Errorf("Wrong incident_id: %s", decision.IncidentID)
	}
	if decision.RecommendedAction != "restart_service" {
		t.Errorf("Wrong action: %s", decision.RecommendedAction)
	}
	if decision.Confidence != 0.85 {
		t.Errorf("Wrong confidence: %f", decision.Confidence)
	}
	if decision.EscalateToL3 {
		t.Error("Should not escalate")
	}
	if !decision.ShouldExecute() {
		t.Error("Should be auto-executable")
	}

	script, ok := decision.ActionParams["script"].(string)
	if !ok || script != "systemctl restart sshd" {
		t.Errorf("Wrong script: %v", decision.ActionParams["script"])
	}
}

func TestParseResponseWithCodeFence(t *testing.T) {
	resp := &AnthropicResponse{
		Content: []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		}{
			{
				Type: "text",
				Text: "```json\n{\"recommended_action\": \"escalate\", \"action_params\": {}, \"confidence\": 0.3, \"reasoning\": \"Unknown issue\", \"requires_approval\": false, \"escalate_to_l3\": true}\n```",
			},
		},
	}

	decision, err := ParseResponse(resp, "test-1")
	if err != nil {
		t.Fatalf("ParseResponse with code fence error: %v", err)
	}

	if !decision.EscalateToL3 {
		t.Error("Should escalate")
	}
	if decision.ShouldExecute() {
		t.Error("Should not auto-execute when escalating")
	}
}

func TestParseResponseEmpty(t *testing.T) {
	resp := &AnthropicResponse{}

	_, err := ParseResponse(resp, "test-1")
	if err == nil {
		t.Error("Should error on empty response")
	}
}

func TestParseResponseInvalidJSON(t *testing.T) {
	resp := &AnthropicResponse{
		Content: []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		}{
			{Type: "text", Text: "This is not JSON at all"},
		},
	}

	_, err := ParseResponse(resp, "test-1")
	if err == nil {
		t.Error("Should error on invalid JSON")
	}
}

func TestSystemPromptContainsAllowedActions(t *testing.T) {
	for _, action := range DefaultAllowedActions {
		if !strings.Contains(systemPrompt, action) {
			t.Errorf("System prompt missing allowed action: %s", action)
		}
	}
}
