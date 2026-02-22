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
	endpoint    string // Base API endpoint (e.g. "https://api.osiriscare.net")
	apiKey      string
	siteID      string
	applianceID string
	client      *http.Client
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

// SetApplianceID sets the appliance ID for telemetry reports.
func (r *TelemetryReporter) SetApplianceID(id string) {
	r.applianceID = id
}

// executionData is the inner execution payload matching what the backend extracts.
type executionData struct {
	ExecutionID     string  `json:"execution_id"`
	IncidentID      string  `json:"incident_id"`
	ApplianceID     string  `json:"appliance_id,omitempty"`
	RunbookID       string  `json:"runbook_id"`
	Hostname     string `json:"hostname"`
	IncidentType string `json:"incident_type"`
	DurationSeconds float64 `json:"duration_seconds"`
	Success         bool    `json:"success"`
	Status          string  `json:"status"`
	Confidence      float64 `json:"confidence"`
	ResolutionLevel string  `json:"resolution_level"`
	ErrorMessage    string  `json:"error_message,omitempty"`
	// Flywheel fields — cost, tokens, reasoning, pattern_signature
	CostUSD          float64 `json:"cost_usd,omitempty"`
	InputTokens      int     `json:"input_tokens,omitempty"`
	OutputTokens     int     `json:"output_tokens,omitempty"`
	Reasoning        string  `json:"reasoning,omitempty"`
	PatternSignature string  `json:"pattern_signature,omitempty"`
}

// telemetryPayload matches the backend's ExecutionTelemetryInput model.
type telemetryPayload struct {
	SiteID     string        `json:"site_id"`
	Execution  executionData `json:"execution"`
	ReportedAt string        `json:"reported_at"`
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
	now := time.Now().UTC()
	execID := fmt.Sprintf("l2-%s-%d", incident.ID, now.UnixMilli())
	costUSD := CalculateCost(inputTokens, outputTokens)

	// Use incident's pattern signature if available, otherwise build from type+host
	patternSig := incident.PatternSignature
	if patternSig == "" {
		patternSig = fmt.Sprintf("%s:%s:%s", incident.IncidentType, incident.IncidentType, incident.HostID)
	}

	status := "success"
	if !success {
		status = "failure"
	}

	payload := telemetryPayload{
		SiteID: r.siteID,
		Execution: executionData{
			ExecutionID:      execID,
			IncidentID:       incident.ID,
			ApplianceID:      r.applianceID,
			RunbookID:        decision.RunbookID,
			Hostname:         incident.HostID,
			IncidentType:     incident.IncidentType,
			DurationSeconds:  float64(durationMs) / 1000.0,
			Success:          success,
			Status:           status,
			Confidence:       decision.Confidence,
			ResolutionLevel:  "L2",
			ErrorMessage:     execErr,
			CostUSD:          costUSD,
			InputTokens:      inputTokens,
			OutputTokens:     outputTokens,
			Reasoning:        decision.Reasoning,
			PatternSignature: patternSig,
		},
		ReportedAt: now.Format(time.RFC3339),
	}

	body, err := json.Marshal(payload)
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

	log.Printf("[l2planner] Telemetry reported: incident=%s action=%s success=%v cost=$%.4f tokens=%d+%d",
		incident.ID, decision.RecommendedAction, success, costUSD, inputTokens, outputTokens)
}
