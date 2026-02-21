package l2planner

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"

	"github.com/osiriscare/appliance/internal/l2bridge"
)

// TelemetryReporter sends L2 execution outcomes to Central Command.
// This feeds the data flywheel: L2 decisions are recorded, patterns accumulate,
// and successful patterns get promoted to L1 rules.
type TelemetryReporter struct {
	endpoint string // Base API endpoint (e.g. "https://api.osiriscare.net")
	apiKey   string
	siteID   string
	client   *http.Client
}

// NewTelemetryReporter creates a new telemetry reporter.
func NewTelemetryReporter(endpoint, apiKey, siteID string) *TelemetryReporter {
	return &TelemetryReporter{
		endpoint: endpoint,
		apiKey:   apiKey,
		siteID:   siteID,
		client: &http.Client{
			Timeout: 10 * time.Second,
		},
	}
}

// ExecutionReport is the payload sent to /api/agent/executions.
type ExecutionReport struct {
	SiteID       string  `json:"site_id"`
	IncidentID   string  `json:"incident_id"`
	IncidentType string  `json:"incident_type"`
	Hostname     string  `json:"hostname"`
	RunbookID    string  `json:"runbook_id"`
	Action       string  `json:"action"`
	Script       string  `json:"script"`
	Confidence   float64 `json:"confidence"`
	Reasoning    string  `json:"reasoning"`
	Success      bool    `json:"success"`
	Error        string  `json:"error,omitempty"`
	DurationMs   int64   `json:"duration_ms"`
	Level        string  `json:"level"` // always "L2"
	InputTokens  int     `json:"input_tokens,omitempty"`
	OutputTokens int     `json:"output_tokens,omitempty"`
	CostUSD      float64 `json:"cost_usd,omitempty"`
}

// ReportExecution sends an execution outcome to Central Command.
// Designed to be called as `go reporter.ReportExecution(...)` — fire and forget.
func (r *TelemetryReporter) ReportExecution(
	incident *l2bridge.Incident,
	decision *l2bridge.LLMDecision,
	success bool,
	execErr string,
	durationMs int64,
	inputTokens, outputTokens int,
) {
	script, _ := decision.ActionParams["script"].(string)

	report := ExecutionReport{
		SiteID:       r.siteID,
		IncidentID:   incident.ID,
		IncidentType: incident.IncidentType,
		Hostname:     incident.HostID,
		RunbookID:    decision.RunbookID,
		Action:       decision.RecommendedAction,
		Script:       script,
		Confidence:   decision.Confidence,
		Reasoning:    decision.Reasoning,
		Success:      success,
		Error:        execErr,
		DurationMs:   durationMs,
		Level:        "L2",
		InputTokens:  inputTokens,
		OutputTokens: outputTokens,
		CostUSD:      CalculateCost(inputTokens, outputTokens),
	}

	body, err := json.Marshal(report)
	if err != nil {
		log.Printf("[l2planner] Telemetry marshal error: %v", err)
		return
	}

	url := fmt.Sprintf("%s/api/agent/executions", r.endpoint)
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		log.Printf("[l2planner] Telemetry request error: %v", err)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+r.apiKey)

	resp, err := r.client.Do(req)
	if err != nil {
		log.Printf("[l2planner] Telemetry POST failed: %v", err)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		log.Printf("[l2planner] Telemetry POST returned %d", resp.StatusCode)
		return
	}

	log.Printf("[l2planner] Telemetry reported: incident=%s action=%s success=%v",
		incident.ID, decision.RecommendedAction, success)
}
