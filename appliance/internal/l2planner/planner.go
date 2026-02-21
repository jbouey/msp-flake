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
	// API
	APIKey      string
	APIEndpoint string // Default: "https://api.anthropic.com"
	APIModel    string // Default: "claude-haiku-4-5-20251001"
	APITimeout  time.Duration
	MaxTokens   int

	// Budget
	Budget BudgetConfig

	// Guardrails
	AllowedActions []string // nil = use defaults

	// Telemetry
	TelemetryEndpoint string // Central Command API base URL
	TelemetryAPIKey   string
	SiteID            string
}

// DefaultPlannerConfig returns a config with sane defaults.
func DefaultPlannerConfig() PlannerConfig {
	return PlannerConfig{
		APIEndpoint: "https://api.anthropic.com",
		APIModel:    "claude-haiku-4-5-20251001",
		APITimeout:  30 * time.Second,
		MaxTokens:   1024,
		Budget:      DefaultBudgetConfig(),
	}
}

// Planner is the native Go L2 LLM planner.
// It has the same method signatures as l2bridge.Client for easy daemon swap.
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
		cfg.APIEndpoint = "https://api.anthropic.com"
	}
	if cfg.APIModel == "" {
		cfg.APIModel = "claude-haiku-4-5-20251001"
	}
	if cfg.APITimeout == 0 {
		cfg.APITimeout = 30 * time.Second
	}
	if cfg.MaxTokens == 0 {
		cfg.MaxTokens = 1024
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

	if cfg.TelemetryEndpoint != "" && cfg.TelemetryAPIKey != "" {
		p.telemetry = NewTelemetryReporter(cfg.TelemetryEndpoint, cfg.TelemetryAPIKey, cfg.SiteID)
	}

	return p
}

// IsConnected returns true if the planner has an API key configured.
// (Unlike the l2bridge.Client which checks socket connection, we just check config.)
func (p *Planner) IsConnected() bool {
	return p.config.APIKey != ""
}

// Plan sends an incident to the Anthropic API and returns the LLM decision.
// Flow: budget check → PHI scrub → build prompt → API call → parse → guardrails → return
func (p *Planner) Plan(incident *l2bridge.Incident) (*l2bridge.LLMDecision, error) {
	// 1. Budget check
	if err := p.budget.CheckBudget(); err != nil {
		return nil, fmt.Errorf("L2 budget: %w", err)
	}

	// 2. Acquire concurrency slot
	release, ok := p.budget.TryAcquire()
	if !ok {
		return nil, fmt.Errorf("L2 concurrency limit reached")
	}
	defer release()

	// 3. PHI scrub the raw data
	scrubbedIncident := *incident // shallow copy
	if incident.RawData != nil {
		scrubbedIncident.RawData = p.scrubber.ScrubMap(incident.RawData)
	}

	// Log PHI categories found
	if incident.RawData != nil {
		for k, v := range incident.RawData {
			if str, ok := v.(string); ok {
				if cats := p.scrubber.ScrubReport(str); len(cats) > 0 {
					log.Printf("[l2planner] PHI scrubbed from %s: %v", k, cats)
				}
			}
		}
	}

	// 4. Build API request
	apiReq := BuildRequest(p.config.APIModel, p.config.MaxTokens, &scrubbedIncident)

	// 5. Call Anthropic API
	start := time.Now()
	apiResp, err := p.callAPI(apiReq)
	elapsed := time.Since(start)

	if err != nil {
		return nil, fmt.Errorf("L2 API call (%v): %w", elapsed.Round(time.Millisecond), err)
	}

	log.Printf("[l2planner] API response in %v (input=%d, output=%d tokens)",
		elapsed.Round(time.Millisecond), apiResp.Usage.InputTokens, apiResp.Usage.OutputTokens)

	// 6. Record cost
	cost := p.budget.RecordCost(apiResp.Usage.InputTokens, apiResp.Usage.OutputTokens)
	log.Printf("[l2planner] Cost: $%.6f (budget remaining: $%.4f)",
		cost, p.budget.Stats().DailyRemaining)

	// 7. Parse response into LLMDecision
	decision, err := ParseResponse(apiResp, incident.ID)
	if err != nil {
		return nil, fmt.Errorf("L2 parse response: %w", err)
	}

	// 8. Apply guardrails
	script, _ := decision.ActionParams["script"].(string)
	check := p.guardrail.Check(decision.RecommendedAction, script, decision.Confidence)
	if !check.Allowed {
		log.Printf("[l2planner] Guardrails blocked: %s (category=%s)", check.Reason, check.Category)
		decision.EscalateToL3 = true
		decision.Reasoning = fmt.Sprintf("Guardrails: %s. Original: %s", check.Reason, decision.Reasoning)
	}

	// Store token counts for telemetry
	decision.ContextUsed = map[string]interface{}{
		"input_tokens":  apiResp.Usage.InputTokens,
		"output_tokens": apiResp.Usage.OutputTokens,
		"cost_usd":      cost,
		"latency_ms":    elapsed.Milliseconds(),
		"model":         p.config.APIModel,
	}

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

// callAPI sends a request to the Anthropic Messages API.
func (p *Planner) callAPI(req AnthropicRequest) (*AnthropicResponse, error) {
	body, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	url := p.config.APIEndpoint + "/v1/messages"

	httpReq, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}

	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("x-api-key", p.config.APIKey)
	httpReq.Header.Set("anthropic-version", "2023-06-01")

	resp, err := p.client.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("API request: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("API returned %d: %s", resp.StatusCode, truncate(string(respBody), 300))
	}

	var apiResp AnthropicResponse
	if err := json.Unmarshal(respBody, &apiResp); err != nil {
		return nil, fmt.Errorf("parse API response: %w", err)
	}

	return &apiResp, nil
}
