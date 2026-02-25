package daemon

import (
	"bytes"
	"encoding/json"
	"log"
	"net/http"
	"time"
)

// incidentReporter sends drift findings to Central Command's POST /incidents
// endpoint so they appear in the dashboard's incidents table.
// This runs alongside the telemetry reporter (which feeds the learning loop).
type incidentReporter struct {
	endpoint string // Base API endpoint
	apiKey   string
	siteID   string
	client   *http.Client
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
func (r *incidentReporter) ReportDriftIncident(
	hostname, checkType, expected, actual, hipaaControl, severity, platform string,
) {
	if r == nil {
		return
	}

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
		log.Printf("[incidents] Marshal error: %v", err)
		return
	}

	url := r.endpoint + "/incidents"
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		log.Printf("[incidents] Request error: %v", err)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+r.apiKey)

	resp, err := r.client.Do(req)
	if err != nil {
		log.Printf("[incidents] POST failed: %v", err)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		log.Printf("[incidents] POST returned %d for %s/%s", resp.StatusCode, hostname, checkType)
		return
	}
}

// ReportHealed notifies the backend that an incident was resolved.
// Called after successful L1/L2 healing.
func (r *incidentReporter) ReportHealed(
	hostname, checkType, resolutionTier, ruleID string,
) {
	if r == nil {
		return
	}

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
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+r.apiKey)

	resp, err := r.client.Do(req)
	if err != nil {
		return
	}
	resp.Body.Close()
}
