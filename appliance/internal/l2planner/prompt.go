package l2planner

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/osiriscare/appliance/internal/l2bridge"
)

// System prompt for the L2 LLM planner.
const systemPrompt = `You are an infrastructure remediation planner for HIPAA-compliant healthcare IT environments.

Given a compliance drift incident, you must:
1. Analyze the incident type, severity, and context data
2. Determine the most appropriate remediation action
3. Generate a safe, minimal remediation script
4. Assess your confidence level (0.0-1.0)

CONSTRAINTS:
- You may ONLY use pre-approved remediation actions: restart_service, enable_service, configure_firewall, apply_gpo, enable_bitlocker, fix_audit_policy, apply_ssh_hardening, fix_ntp, fix_permissions, enable_defender, fix_password_policy, escalate
- Scripts must be idempotent and safe to re-run
- Never modify data, only system configuration
- Never access or modify PHI/patient data
- If unsure, set escalate_to_l3: true
- For Windows targets, use PowerShell
- For Linux targets, use bash

Respond with ONLY a JSON object (no markdown, no explanation) with these fields:
{
  "recommended_action": "one of the pre-approved actions",
  "action_params": {"script": "the remediation script"},
  "confidence": 0.85,
  "reasoning": "brief explanation of your analysis",
  "requires_approval": false,
  "escalate_to_l3": false,
  "runbook_id": "L2-AUTO-<incident_type>"
}`

// BuildUserPrompt constructs the user message from a scrubbed incident.
func BuildUserPrompt(incident *l2bridge.Incident) string {
	var b strings.Builder

	b.WriteString("INCIDENT DETAILS:\n")
	fmt.Fprintf(&b, "- ID: %s\n", incident.ID)
	fmt.Fprintf(&b, "- Site: %s\n", incident.SiteID)
	fmt.Fprintf(&b, "- Host: %s\n", incident.HostID)
	fmt.Fprintf(&b, "- Type: %s\n", incident.IncidentType)
	fmt.Fprintf(&b, "- Severity: %s\n", incident.Severity)
	fmt.Fprintf(&b, "- Time: %s\n", incident.CreatedAt)

	if incident.PatternSignature != "" {
		fmt.Fprintf(&b, "- Pattern: %s\n", incident.PatternSignature)
	}

	if len(incident.RawData) > 0 {
		b.WriteString("\nCONTEXT DATA:\n")
		for k, v := range incident.RawData {
			fmt.Fprintf(&b, "- %s: %v\n", k, v)
		}
	}

	b.WriteString("\nProvide a JSON remediation plan.")
	return b.String()
}

// AnthropicRequest is the request body for the Anthropic Messages API.
type AnthropicRequest struct {
	Model     string             `json:"model"`
	MaxTokens int                `json:"max_tokens"`
	System    string             `json:"system,omitempty"`
	Messages  []AnthropicMessage `json:"messages"`
}

// AnthropicMessage is a message in the Anthropic API format.
type AnthropicMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// AnthropicResponse is the response from the Anthropic Messages API.
type AnthropicResponse struct {
	ID      string `json:"id"`
	Type    string `json:"type"`
	Role    string `json:"role"`
	Content []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	} `json:"content"`
	Model        string `json:"model"`
	StopReason   string `json:"stop_reason"`
	StopSequence string `json:"stop_sequence,omitempty"`
	Usage        struct {
		InputTokens  int `json:"input_tokens"`
		OutputTokens int `json:"output_tokens"`
	} `json:"usage"`
}

// BuildRequest constructs the full Anthropic API request.
func BuildRequest(model string, maxTokens int, incident *l2bridge.Incident) AnthropicRequest {
	return AnthropicRequest{
		Model:     model,
		MaxTokens: maxTokens,
		System:    systemPrompt,
		Messages: []AnthropicMessage{
			{
				Role:    "user",
				Content: BuildUserPrompt(incident),
			},
		},
	}
}

// LLMResponsePayload is the JSON structure we expect the LLM to return.
type LLMResponsePayload struct {
	RecommendedAction string                 `json:"recommended_action"`
	ActionParams      map[string]interface{} `json:"action_params"`
	Confidence        float64                `json:"confidence"`
	Reasoning         string                 `json:"reasoning"`
	RequiresApproval  bool                   `json:"requires_approval"`
	EscalateToL3      bool                   `json:"escalate_to_l3"`
	RunbookID         string                 `json:"runbook_id,omitempty"`
}

// ParseResponse extracts the LLM decision from an Anthropic API response.
func ParseResponse(resp *AnthropicResponse, incidentID string) (*l2bridge.LLMDecision, error) {
	if len(resp.Content) == 0 {
		return nil, fmt.Errorf("empty response content")
	}

	text := resp.Content[0].Text

	// Strip markdown code fences if present
	text = strings.TrimSpace(text)
	if strings.HasPrefix(text, "```") {
		lines := strings.Split(text, "\n")
		if len(lines) > 2 {
			text = strings.Join(lines[1:len(lines)-1], "\n")
		}
	}

	var payload LLMResponsePayload
	if err := json.Unmarshal([]byte(text), &payload); err != nil {
		return nil, fmt.Errorf("parse LLM JSON response: %w (raw: %s)", err, truncate(text, 200))
	}

	return &l2bridge.LLMDecision{
		IncidentID:        incidentID,
		RecommendedAction: payload.RecommendedAction,
		ActionParams:      payload.ActionParams,
		Confidence:        payload.Confidence,
		Reasoning:         payload.Reasoning,
		RequiresApproval:  payload.RequiresApproval,
		EscalateToL3:      payload.EscalateToL3,
		RunbookID:         payload.RunbookID,
	}, nil
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max] + "..."
}
