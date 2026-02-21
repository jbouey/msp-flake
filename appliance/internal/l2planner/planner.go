package l2planner

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"time"

	"github.com/osiriscare/appliance/internal/l2bridge"
)

// PlannerConfig holds configuration for the L2 planner.
type PlannerConfig struct {
	// Central Command connection (the appliance's existing API endpoint + key)
	APIEndpoint string // Central Command base URL (e.g. "https://api.osiriscare.net")
	APIKey      string // Site API key for auth (same key used for checkins)
	SiteID      string
	APITimeout  time.Duration

	// Budget (local rate limiting on the appliance)
	Budget BudgetConfig

	// Guardrails (local safety checks on the appliance)
	AllowedActions []string // nil = use defaults
}

// DefaultPlannerConfig returns a config with sane defaults.
func DefaultPlannerConfig() PlannerConfig {
	return PlannerConfig{
		APIEndpoint: "https://api.osiriscare.net",
		APITimeout:  30 * time.Second,
		Budget:      DefaultBudgetConfig(),
	}
}

// Planner is the native Go L2 LLM planner.
// It calls Central Command's /api/agent/l2/plan endpoint, which holds the
// Anthropic API key and calls the LLM. The appliance never needs the LLM key.
//
// PHI scrubbing runs locally BEFORE data leaves the device (defense in depth).
// Guardrails run locally AFTER receiving the decision.
type Planner struct {
	config    PlannerConfig
	client    *http.Client
	scrubber  *PHIScrubber
	guardrail *Guardrails
	budget    *BudgetTracker
	telemetry *TelemetryReporter
}

// NewPlanner creates a new L2 planner.
func NewPlanner(cfg PlannerConfig) *Planner {
	if cfg.APIEndpoint == "" {
		cfg.APIEndpoint = "https://api.osiriscare.net"
	}
	if cfg.APITimeout == 0 {
		cfg.APITimeout = 30 * time.Second
	}

	p := &Planner{
		config: cfg,
		client: &http.Client{
			Timeout: cfg.APITimeout,
		},
		scrubber:  NewPHIScrubber(),
		guardrail: NewGuardrails(cfg.AllowedActions),
		budget:    NewBudgetTracker(cfg.Budget),
	}

	// Telemetry uses the same Central Command endpoint + key
	if cfg.APIEndpoint != "" && cfg.APIKey != "" {
		p.telemetry = NewTelemetryReporter(cfg.APIEndpoint, cfg.APIKey, cfg.SiteID)
	}

	return p
}

// IsConnected returns true if the planner has Central Command credentials.
func (p *Planner) IsConnected() bool {
	return p.config.APIKey != "" && p.config.APIEndpoint != ""
}

// l2PlanRequest is the JSON body sent to Central Command's /api/agent/l2/plan.
type l2PlanRequest struct {
	IncidentID       string                 `json:"incident_id"`
	SiteID           string                 `json:"site_id"`
	HostID           string                 `json:"host_id"`
	IncidentType     string                 `json:"incident_type"`
	Severity         string                 `json:"severity"`
	RawData          map[string]interface{} `json:"raw_data"`
	PatternSignature string                 `json:"pattern_signature"`
	CreatedAt        string                 `json:"created_at"`
}

// l2PlanResponse is the JSON body returned by Central Command's /api/agent/l2/plan.
// Maps directly to l2bridge.LLMDecision.
type l2PlanResponse struct {
	IncidentID        string                 `json:"incident_id"`
	RecommendedAction string                 `json:"recommended_action"`
	ActionParams      map[string]interface{} `json:"action_params"`
	Confidence        float64                `json:"confidence"`
	Reasoning         string                 `json:"reasoning"`
	RunbookID         string                 `json:"runbook_id"`
	RequiresApproval  bool                   `json:"requires_approval"`
	EscalateToL3      bool                   `json:"escalate_to_l3"`
	ContextUsed       map[string]interface{} `json:"context_used"`
}

// Plan sends a PHI-scrubbed incident to Central Command and returns the LLM decision.
// Flow: budget check → PHI scrub → POST to Central Command → guardrails → return
func (p *Planner) Plan(incident *l2bridge.Incident) (*l2bridge.LLMDecision, error) {
	// 1. Budget check (local rate limiting)
	if err := p.budget.CheckBudget(); err != nil {
		return nil, fmt.Errorf("L2 budget: %w", err)
	}

	// 2. Acquire concurrency slot
	release, ok := p.budget.TryAcquire()
	if !ok {
		return nil, fmt.Errorf("L2 concurrency limit reached")
	}
	defer release()

	// 3. PHI scrub the raw data BEFORE it leaves the device
	scrubbedData := incident.RawData
	if incident.RawData != nil {
		// Log what's being scrubbed
		for k, v := range incident.RawData {
			if str, ok := v.(string); ok {
				if cats := p.scrubber.ScrubReport(str); len(cats) > 0 {
					log.Printf("[l2planner] PHI scrubbed from %s: %v", k, cats)
				}
			}
		}
		scrubbedData = p.scrubber.ScrubMap(incident.RawData)
	}

	// 4. Build request for Central Command
	planReq := l2PlanRequest{
		IncidentID:       incident.ID,
		SiteID:           p.config.SiteID,
		HostID:           incident.HostID,
		IncidentType:     incident.IncidentType,
		Severity:         incident.Severity,
		RawData:          scrubbedData,
		PatternSignature: incident.PatternSignature,
		CreatedAt:        incident.CreatedAt,
	}

	// 5. Call Central Command /api/agent/l2/plan
	start := time.Now()
	planResp, err := p.callCentralCommand(planReq)
	elapsed := time.Since(start)

	if err != nil {
		return nil, fmt.Errorf("L2 plan (%v): %w", elapsed.Round(time.Millisecond), err)
	}

	log.Printf("[l2planner] Central Command response in %v: action=%s confidence=%.2f",
		elapsed.Round(time.Millisecond), planResp.RecommendedAction, planResp.Confidence)

	// 6. Record the call in budget tracker
	p.budget.RecordCost(0, 0) // Cost is tracked server-side; this just increments the hourly counter

	// 7. Convert response to LLMDecision
	decision := &l2bridge.LLMDecision{
		IncidentID:        planResp.IncidentID,
		RecommendedAction: planResp.RecommendedAction,
		ActionParams:      planResp.ActionParams,
		Confidence:        planResp.Confidence,
		Reasoning:         planResp.Reasoning,
		RunbookID:         planResp.RunbookID,
		RequiresApproval:  planResp.RequiresApproval,
		EscalateToL3:      planResp.EscalateToL3,
		ContextUsed:       planResp.ContextUsed,
	}

	// 8. Apply local guardrails (defense in depth)
	script, _ := decision.ActionParams["script"].(string)
	check := p.guardrail.Check(decision.RecommendedAction, script, decision.Confidence)
	if !check.Allowed {
		log.Printf("[l2planner] Guardrails blocked: %s (category=%s)", check.Reason, check.Category)
		decision.EscalateToL3 = true
		decision.Reasoning = fmt.Sprintf("Guardrails: %s. Original: %s", check.Reason, decision.Reasoning)
	}

	// Add latency to context
	if decision.ContextUsed == nil {
		decision.ContextUsed = make(map[string]interface{})
	}
	decision.ContextUsed["appliance_latency_ms"] = elapsed.Milliseconds()

	return decision, nil
}

// PlanWithRetry attempts to plan with retries on transient failures.
func (p *Planner) PlanWithRetry(incident *l2bridge.Incident, maxRetries int) (*l2bridge.LLMDecision, error) {
	var lastErr error
	for attempt := 0; attempt <= maxRetries; attempt++ {
		if attempt > 0 {
			log.Printf("[l2planner] Retry %d/%d after error: %v", attempt, maxRetries, lastErr)
			time.Sleep(time.Duration(attempt) * time.Second)
		}

		decision, err := p.Plan(incident)
		if err == nil {
			return decision, nil
		}
		lastErr = err
	}
	return nil, fmt.Errorf("L2 plan failed after %d retries: %w", maxRetries, lastErr)
}

// ReportExecution sends an execution outcome to Central Command for the data flywheel.
func (p *Planner) ReportExecution(
	incident *l2bridge.Incident,
	decision *l2bridge.LLMDecision,
	success bool,
	execErr string,
	durationMs int64,
) {
	if p.telemetry == nil {
		return
	}

	inputTokens, _ := decision.ContextUsed["input_tokens"].(int)
	outputTokens, _ := decision.ContextUsed["output_tokens"].(int)

	p.telemetry.ReportExecution(incident, decision, success, execErr, durationMs, inputTokens, outputTokens)
}

// Stats returns current budget statistics.
func (p *Planner) Stats() BudgetStats {
	return p.budget.Stats()
}

// Close is a no-op for the native planner (no persistent connection).
func (p *Planner) Close() {
	// No-op: HTTP client doesn't need cleanup
}

// callCentralCommand sends a plan request to Central Command's L2 endpoint.
func (p *Planner) callCentralCommand(req l2PlanRequest) (*l2PlanResponse, error) {
	body, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	url := p.config.APIEndpoint + "/api/agent/l2/plan"

	httpReq, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}

	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+p.config.APIKey)

	resp, err := p.client.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("Central Command request: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("Central Command returned %d: %s", resp.StatusCode, truncate(string(respBody), 300))
	}

	var planResp l2PlanResponse
	if err := json.Unmarshal(respBody, &planResp); err != nil {
		return nil, fmt.Errorf("parse response: %w", err)
	}

	return &planResp, nil
}
