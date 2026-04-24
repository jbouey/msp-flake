package daemon

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"sync"
	"time"

	"github.com/osiriscare/appliance/internal/phiscrub"
)

// incidentReporter sends drift findings to Central Command's POST /incidents
// endpoint so they appear in the dashboard's incidents table.
// This runs alongside the telemetry reporter (which feeds the learning loop).
//
// v40.4 / daemon 0.4.8 (2026-04-23): apiKey is mutex-protected so the
// main daemon can rotate it after an auto-rekey without racing
// concurrent HTTP goroutines. Prior to this, newIncidentReporter
// captured the API key by value — a rekey updated d.config.APIKey
// but this struct kept the stale string forever, causing the
// split-brain 401-storm on /incidents endpoints while /checkin
// returned 200 with the fresh key. See audit item #5.
type incidentReporter struct {
	endpoint  string // Base API endpoint
	mu        sync.RWMutex
	apiKey    string
	siteID    string
	client    *http.Client
	allowFunc func() bool // optional: skip reporting when server is unreachable
}

func newIncidentReporter(endpoint, apiKey, siteID string) *incidentReporter {
	return &incidentReporter{
		endpoint: endpoint,
		apiKey:   apiKey,
		siteID:   siteID,
		client: &http.Client{
			Timeout: 10 * time.Second,
		},
	}
}

// SetAPIKey rotates the bearer token used for all future /incidents POSTs.
// Safe to call concurrently with in-flight request goroutines; the RWMutex
// guarantees the Bearer header built inside ReportDriftIncident / ReportHealed
// sees either the old or new key atomically, never a torn read.
func (r *incidentReporter) SetAPIKey(key string) {
	if r == nil {
		return
	}
	r.mu.Lock()
	r.apiKey = key
	r.mu.Unlock()
}

// currentAPIKey returns the key under a read lock — cheap, reentrant-safe,
// guarantees no torn string read while SetAPIKey runs on another goroutine.
func (r *incidentReporter) currentAPIKey() string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.apiKey
}

// incidentPayload matches the backend IncidentReport model (main.py:254).
type incidentPayload struct {
	SiteID        string                 `json:"site_id"`
	HostID        string                 `json:"host_id"`
	IncidentType  string                 `json:"incident_type"`
	Severity      string                 `json:"severity"`
	CheckType     string                 `json:"check_type,omitempty"`
	Details       map[string]interface{} `json:"details"`
	PreState      map[string]interface{} `json:"pre_state"`
	HIPAAControls []string               `json:"hipaa_controls,omitempty"`
}

// ReportDriftIncident sends a drift finding to the backend incidents table.
// Designed to be called as `go reporter.ReportDriftIncident(...)` — fire and forget.
// All text fields are PHI-scrubbed before transmission per HIPAA §164.312(e)(1).
func (r *incidentReporter) ReportDriftIncident(
	hostname, checkType, expected, actual, hipaaControl, severity, platform string,
) {
	if r == nil {
		return
	}
	if r.allowFunc != nil && !r.allowFunc() {
		return // circuit breaker open
	}

	// Scrub all text fields before sending to Central Command.
	// Do NOT scrub siteID (infrastructure), checkType (enum), severity (enum),
	// hipaaControl (standard reference), or platform (enum).
	hostname = phiscrub.Scrub(hostname)
	expected = phiscrub.Scrub(expected)
	actual = phiscrub.Scrub(actual)

	hipaaControls := []string{}
	if hipaaControl != "" {
		hipaaControls = []string{hipaaControl}
	}

	payload := incidentPayload{
		SiteID:       r.siteID,
		HostID:       hostname,
		IncidentType: checkType,
		Severity:     severity,
		CheckType:    checkType,
		Details: map[string]interface{}{
			"drift_detected": true,
			"expected":       expected,
			"actual":         actual,
			"platform":       platform,
			"source":         "go-daemon",
			"message":        "Drift detected: " + checkType,
		},
		PreState: map[string]interface{}{
			"expected": expected,
			"actual":   actual,
		},
		HIPAAControls: hipaaControls,
	}

	body, err := json.Marshal(payload)
	if err != nil {
		slog.Error("marshal error", "component", "incidents", "error", err)
		return
	}

	url := r.endpoint + "/incidents"
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		slog.Error("request error", "component", "incidents", "error", err)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+r.currentAPIKey())

	resp, err := r.client.Do(req)
	if err != nil {
		slog.Error("POST failed", "component", "incidents", "error", err)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		slog.Warn("POST returned unexpected status", "component", "incidents", "status_code", resp.StatusCode, "hostname", hostname, "check_type", checkType)
		return
	}
}

// ReportHealed notifies the backend that an incident was resolved.
// Called after successful L1/L2 healing.
// Hostname is PHI-scrubbed before transmission.
func (r *incidentReporter) ReportHealed(
	hostname, checkType, resolutionTier, ruleID string,
) {
	if r == nil {
		return
	}
	if r.allowFunc != nil && !r.allowFunc() {
		return // circuit breaker open
	}

	// Scrub hostname (may contain patient identifiers).
	// checkType, resolutionTier, ruleID are infrastructure enums — not scrubbed.
	hostname = phiscrub.Scrub(hostname)

	payload := map[string]interface{}{
		"site_id":         r.siteID,
		"host_id":         hostname,
		"check_type":      checkType,
		"resolution_tier": resolutionTier,
		"runbook_id":      ruleID,
		"status":          "resolved",
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return
	}

	url := r.endpoint + "/incidents/resolve"
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+r.currentAPIKey())

	resp, err := r.client.Do(req)
	if err != nil {
		return
	}
	resp.Body.Close()
}
