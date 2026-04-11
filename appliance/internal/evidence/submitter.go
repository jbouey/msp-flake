package evidence

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/osiriscare/appliance/internal/phiscrub"
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

// Submitter builds and submits evidence bundles to Central Command.
// When Central Command is unreachable (circuit breaker open or HTTP failure),
// evidence bundles are cached locally and drained on reconnection.
type Submitter struct {
	siteID       string
	apiEndpoint  string
	apiKey       string
	signingKey   ed25519.PrivateKey
	publicKeyHex string
	client       *http.Client
	AllowFunc    func() bool              // optional: skip submission when server is unreachable (circuit breaker)
	CacheDir     string                  // local evidence cache directory (set by daemon)
	OnSubmitted  func(bundleID, hash string) // callback: record bundle hash for peer witnessing
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
	BundleHash     string                 `json:"bundle_hash"`
	AgentSignature string                 `json:"agent_signature"`
	AgentPublicKey string                 `json:"agent_public_key"`
	SignedData     string                 `json:"signed_data"`
}

// SigningKey returns the Ed25519 private key for peer witness counter-signing.
func (s *Submitter) SigningKey() ed25519.PrivateKey { return s.signingKey }

// buildAndSubmitForTypes is the shared implementation for building evidence bundles.
func (s *Submitter) buildAndSubmitForTypes(ctx context.Context, findings []DriftFinding, scannedHosts []string, checkTypes []string) error {
	if len(scannedHosts) == 0 {
		return nil
	}
	if s.AllowFunc != nil && !s.AllowFunc() {
		log.Printf("[evidence] Circuit breaker open — caching evidence locally")
		// Don't return nil — build the bundle and cache it
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
		// Scrub hostname before it enters the evidence bundle
		scrubbedHost := phiscrub.Scrub(host)
		for _, ct := range checkTypes {
			key := host + ":" + ct
			check := map[string]any{
				"check":    ct,
				"hostname": scrubbedHost,
			}

			if f, found := driftMap[key]; found {
				check["status"] = "fail"
				// Scrub expected/actual values — may contain raw command output with PHI
				check["expected"] = phiscrub.Scrub(f.Expected)
				check["actual"] = phiscrub.Scrub(f.Actual)
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

// BuildAndSubmitNetwork packages network drift findings into a compliance evidence bundle.
func (s *Submitter) BuildAndSubmitNetwork(ctx context.Context, findings []DriftFinding, scannedHosts []string) error {
	return s.buildAndSubmitForTypes(ctx, findings, scannedHosts, networkCheckTypes)
}

// submitBundle signs and submits an evidence bundle to Central Command.
func (s *Submitter) submitBundle(ctx context.Context, checks []map[string]any, compliant, nonCompliant int, scannedHosts []string, now time.Time) error {

	summary := map[string]any{
		"total_checks":  compliant + nonCompliant,
		"compliant":     compliant,
		"non_compliant": nonCompliant,
		"scanned_hosts": len(scannedHosts),
		"scan_method":   "deterministic", // L1 drift scan — not LLM-assessed
		"confidence":    1.0,             // Deterministic scans have full confidence
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

	// Client-side bundle hash — computed from signedBytes (canonical JSON).
	// This commits the hash at the source, preventing server-side tampering.
	bundleDigest := sha256.Sum256(signedBytes)
	bundleHash := hex.EncodeToString(bundleDigest[:])

	payload := bundlePayload{
		SiteID:         s.siteID,
		CheckedAt:      now.Format(time.RFC3339),
		Checks:         checks,
		Summary:        summary,
		BundleHash:     bundleHash,
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

	// If circuit breaker is open, cache locally instead of attempting HTTP
	if s.AllowFunc != nil && !s.AllowFunc() {
		return s.cacheBundle(body)
	}

	resp, err := s.client.Do(req)
	if err != nil {
		// Submission failed — cache locally for retry
		log.Printf("[evidence] Submit failed, caching locally: %v", err)
		_ = s.cacheBundle(body)
		return fmt.Errorf("submit evidence: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read evidence response: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		// Server rejected — cache for retry (may be transient)
		if resp.StatusCode >= 500 {
			log.Printf("[evidence] Server error %d, caching locally", resp.StatusCode)
			_ = s.cacheBundle(body)
		}
		return fmt.Errorf("evidence submit returned %d: %s", resp.StatusCode, string(respBody))
	}

	var result struct {
		BundleID      string `json:"bundle_id"`
		BundleHash    string `json:"bundle_hash"`
		ChainPosition int    `json:"chain_position"`
	}
	if err := json.Unmarshal(respBody, &result); err == nil {
		log.Printf("[evidence] Submitted: bundle=%s chain_pos=%d checks=%d compliant=%d/%d",
			result.BundleID, result.ChainPosition, compliant+nonCompliant, compliant, compliant+nonCompliant)
		// Record bundle hash for peer witnessing
		if s.OnSubmitted != nil && result.BundleID != "" && result.BundleHash != "" {
			s.OnSubmitted(result.BundleID, result.BundleHash)
		}
	}

	return nil
}

// cacheBundle writes an evidence bundle to local disk for later submission.
// Files are named by timestamp for FIFO ordering. The CacheDir is created
// on first use. Max 1000 cached bundles (oldest dropped) to prevent disk fill.
func (s *Submitter) cacheBundle(body []byte) error {
	if s.CacheDir == "" {
		return nil // no cache dir configured
	}
	if err := os.MkdirAll(s.CacheDir, 0o700); err != nil {
		return fmt.Errorf("create cache dir: %w", err)
	}

	// Cap at 1000 cached bundles
	entries, _ := os.ReadDir(s.CacheDir)
	if len(entries) >= 1000 {
		// Drop oldest
		sort.Slice(entries, func(i, j int) bool { return entries[i].Name() < entries[j].Name() })
		_ = os.Remove(filepath.Join(s.CacheDir, entries[0].Name()))
	}

	name := fmt.Sprintf("bundle-%s.json", time.Now().UTC().Format("20060102-150405.000"))
	path := filepath.Join(s.CacheDir, name)
	if err := os.WriteFile(path, body, 0o600); err != nil {
		return fmt.Errorf("write cache file: %w", err)
	}
	log.Printf("[evidence] Cached locally: %s (%d bytes)", name, len(body))
	return nil
}

// DrainCache submits any locally cached evidence bundles to Central Command.
// Called after each successful checkin when the circuit breaker is closed.
// Idempotent: the backend deduplicates by bundle_id (ON CONFLICT DO UPDATE).
func (s *Submitter) DrainCache(ctx context.Context) int {
	if s.CacheDir == "" {
		return 0
	}
	entries, err := os.ReadDir(s.CacheDir)
	if err != nil || len(entries) == 0 {
		return 0
	}

	url := s.apiEndpoint + "/api/evidence/sites/" + s.siteID + "/submit"
	submitted := 0

	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		path := filepath.Join(s.CacheDir, entry.Name())
		body, err := os.ReadFile(path)
		if err != nil {
			log.Printf("[evidence] Failed to read cache file %s: %v", entry.Name(), err)
			continue
		}

		req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
		if err != nil {
			continue
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+s.apiKey)

		resp, err := s.client.Do(req)
		if err != nil {
			log.Printf("[evidence] Cache drain failed (stopping): %v", err)
			break // Server unreachable — stop draining, try next cycle
		}
		resp.Body.Close()

		if resp.StatusCode == http.StatusOK || resp.StatusCode == http.StatusConflict {
			_ = os.Remove(path)
			submitted++
		} else if resp.StatusCode >= 500 {
			log.Printf("[evidence] Cache drain got %d (stopping)", resp.StatusCode)
			break // Server error — stop, try next cycle
		} else {
			// 4xx — bad request, remove to avoid infinite retry
			log.Printf("[evidence] Cache drain got %d for %s (removing)", resp.StatusCode, entry.Name())
			_ = os.Remove(path)
		}
	}

	if submitted > 0 {
		log.Printf("[evidence] Drained %d cached bundles", submitted)
	}
	return submitted
}
