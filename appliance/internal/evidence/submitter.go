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

// windowsCheckTypes are the check types the Windows drift scanner produces.
var windowsCheckTypes = []string{
	"firewall_status",
	"windows_defender",
	"windows_update",
	"audit_logging",
	"rogue_admin_users",
	"rogue_scheduled_tasks",
	"agent_status",
	"bitlocker_status",
	"smb_signing",
	"smb1_protocol",
	"screen_lock_policy",
	"defender_exclusions",
	"dns_config",
	"network_profile",
	"password_policy",
	"rdp_nla",
	"guest_account",
	"service_dns",
	"service_netlogon",
}

// linuxCheckTypes are the check types the Linux drift scanner produces.
var linuxCheckTypes = []string{
	"linux_firewall",
	"linux_ssh_config",
	"linux_failed_services",
	"linux_disk_space",
	"linux_suid_binaries",
	"linux_audit_logging",
	"linux_ntp_sync",
	"linux_kernel_params",
	"linux_open_ports",
	"linux_user_accounts",
	"linux_file_permissions",
	"linux_unattended_upgrades",
	"linux_log_forwarding",
	"linux_cron_review",
	"linux_cert_expiry",
}

// networkCheckTypes are the check types the network scanner produces.
var networkCheckTypes = []string{
	"net_unexpected_ports",
	"net_expected_service",
	"net_host_reachability",
	"net_dns_resolution",
}

// allCheckTypes combines all platform check types.
var allCheckTypes = func() []string {
	all := make([]string, 0, len(windowsCheckTypes)+len(linuxCheckTypes)+len(networkCheckTypes))
	all = append(all, windowsCheckTypes...)
	all = append(all, linuxCheckTypes...)
	all = append(all, networkCheckTypes...)
	return all
}()

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

// buildAndSubmitForTypes is the shared implementation for building evidence bundles.
func (s *Submitter) buildAndSubmitForTypes(ctx context.Context, findings []DriftFinding, scannedHosts []string, checkTypes []string) error {
	if len(scannedHosts) == 0 {
		return nil
	}

	now := time.Now().UTC()

	driftMap := make(map[string]*DriftFinding, len(findings))
	for i := range findings {
		key := findings[i].Hostname + ":" + findings[i].CheckType
		driftMap[key] = &findings[i]
	}

	var checks []map[string]any
	compliant := 0
	nonCompliant := 0

	for _, host := range scannedHosts {
		for _, ct := range checkTypes {
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

	return s.submitBundle(ctx, checks, compliant, nonCompliant, scannedHosts, now)
}

// BuildAndSubmit packages Windows drift findings into a compliance evidence bundle.
func (s *Submitter) BuildAndSubmit(ctx context.Context, findings []DriftFinding, scannedHosts []string) error {
	return s.buildAndSubmitForTypes(ctx, findings, scannedHosts, windowsCheckTypes)
}

// BuildAndSubmitLinux packages Linux drift findings into a compliance evidence bundle.
func (s *Submitter) BuildAndSubmitLinux(ctx context.Context, findings []DriftFinding, scannedHosts []string) error {
	return s.buildAndSubmitForTypes(ctx, findings, scannedHosts, linuxCheckTypes)
}

// submitBundle signs and submits an evidence bundle to Central Command.
func (s *Submitter) submitBundle(ctx context.Context, checks []map[string]any, compliant, nonCompliant int, scannedHosts []string, now time.Time) error {

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
