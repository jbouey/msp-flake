package evidence

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"time"
)

// DriftFinding represents a single drift condition found during scanning.
type DriftFinding struct {
	Hostname     string
	CheckType    string
	Expected     string
	Actual       string
	HIPAAControl string
	Severity     string
}

// allCheckTypes are the 7 check types the drift scanner produces.
// These must match CATEGORY_CHECKS in the backend's db_queries.py.
var allCheckTypes = []string{
	"firewall_status",
	"windows_defender",
	"windows_update",
	"audit_logging",
	"rogue_admin_users",
	"rogue_scheduled_tasks",
	"agent_status",
}

// Submitter builds and submits evidence bundles to Central Command.
type Submitter struct {
	siteID      string
	apiEndpoint string
	apiKey      string
	signingKey  ed25519.PrivateKey
	publicKeyHex string
	client      *http.Client
}

// NewSubmitter creates a new evidence submitter.
func NewSubmitter(siteID, apiEndpoint, apiKey string, key ed25519.PrivateKey, pubHex string) *Submitter {
	return &Submitter{
		siteID:       siteID,
		apiEndpoint:  strings.TrimRight(apiEndpoint, "/"),
		apiKey:       apiKey,
		signingKey:   key,
		publicKeyHex: pubHex,
		client: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

// bundlePayload matches the EvidenceBundleSubmit Pydantic model on the backend.
type bundlePayload struct {
	SiteID         string                 `json:"site_id"`
	CheckedAt      string                 `json:"checked_at"`
	Checks         []map[string]any       `json:"checks"`
	Summary        map[string]any         `json:"summary"`
	AgentSignature string                 `json:"agent_signature"`
	AgentPublicKey string                 `json:"agent_public_key"`
	SignedData     string                 `json:"signed_data"`
}

// BuildAndSubmit packages drift findings into a compliance evidence bundle
// and submits it to Central Command.
//
// Logic: For each scanned host, we produce one check per check type.
// If a drift finding exists for that host+check, the check status is "fail".
// Otherwise, the check status is "pass" (no drift = compliant).
func (s *Submitter) BuildAndSubmit(ctx context.Context, findings []DriftFinding, scannedHosts []string) error {
	if len(scannedHosts) == 0 {
		return nil // nothing scanned, nothing to submit
	}

	now := time.Now().UTC()

	// Build a lookup: "hostname:check_type" -> finding
	driftMap := make(map[string]*DriftFinding, len(findings))
	for i := range findings {
		key := findings[i].Hostname + ":" + findings[i].CheckType
		driftMap[key] = &findings[i]
	}

	// Build individual check results
	var checks []map[string]any
	compliant := 0
	nonCompliant := 0

	for _, host := range scannedHosts {
		for _, ct := range allCheckTypes {
			key := host + ":" + ct
			check := map[string]any{
				"check":    ct,
				"hostname": host,
			}

			if f, found := driftMap[key]; found {
				check["status"] = "fail"
				check["expected"] = f.Expected
				check["actual"] = f.Actual
				if f.HIPAAControl != "" {
					check["hipaa_control"] = f.HIPAAControl
				}
				nonCompliant++
			} else {
				check["status"] = "pass"
				compliant++
			}

			checks = append(checks, check)
		}
	}

	summary := map[string]any{
		"total_checks":  compliant + nonCompliant,
		"compliant":     compliant,
		"non_compliant": nonCompliant,
		"scanned_hosts": len(scannedHosts),
	}

	// Build the signed_data string (must match backend verification)
	signedObj := map[string]any{
		"site_id":    s.siteID,
		"checked_at": now.Format(time.RFC3339),
		"checks":     checks,
		"summary":    summary,
	}
	signedBytes, err := json.Marshal(signedObj)
	if err != nil {
		return fmt.Errorf("marshal signed_data: %w", err)
	}
	signedData := string(signedBytes)

	// Sign
	signature := Sign(s.signingKey, signedBytes)

	payload := bundlePayload{
		SiteID:         s.siteID,
		CheckedAt:      now.Format(time.RFC3339),
		Checks:         checks,
		Summary:        summary,
		AgentSignature: signature,
		AgentPublicKey: s.publicKeyHex,
		SignedData:     signedData,
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal bundle: %w", err)
	}

	url := s.apiEndpoint + "/api/evidence/sites/" + s.siteID + "/submit"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+s.apiKey)

	resp, err := s.client.Do(req)
	if err != nil {
		return fmt.Errorf("submit evidence: %w", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("evidence submit returned %d: %s", resp.StatusCode, string(respBody))
	}

	var result struct {
		BundleID      string `json:"bundle_id"`
		ChainPosition int    `json:"chain_position"`
	}
	if err := json.Unmarshal(respBody, &result); err == nil {
		log.Printf("[evidence] Submitted: bundle=%s chain_pos=%d checks=%d compliant=%d/%d",
			result.BundleID, result.ChainPosition, compliant+nonCompliant, compliant, compliant+nonCompliant)
	}

	return nil
}
