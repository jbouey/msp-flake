// Package orders processes pending orders from Central Command.
//
// Order flow:
//  1. Fetch pending orders from checkin response
//  2. Acknowledge each order (marks as "executing")
//  3. Dispatch to handler by order_type
//  4. Complete order with result (success/failure)
//
// 19 order types are handled, from simple checkins to NixOS rebuilds.
package orders

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/osiriscare/appliance/internal/crypto"
	"gopkg.in/yaml.v3"
)

// --- Order execution timeouts ---
// Long-running orders (NixOS rebuild, binary downloads) get generous timeouts.
// Quick handlers (checkin, status, sync) get short timeouts to prevent hangs.
var orderTimeouts = map[string]time.Duration{
	"nixos_rebuild":       30 * time.Minute,
	"update_agent":        10 * time.Minute,
	"update_daemon":       10 * time.Minute,
	"update_iso":          15 * time.Minute,
	"healing":             5 * time.Minute,
	"deploy_sensor":       5 * time.Minute,
	"deploy_linux_sensor": 5 * time.Minute,
	"diagnostic":          2 * time.Minute,
	"chaos_quicktest":     5 * time.Minute,
	"view_logs":           30 * time.Second,
	"validate_credential": 30 * time.Second,
}

// defaultOrderTimeout is used for order types not in orderTimeouts.
const defaultOrderTimeout = 2 * time.Minute

// orderTimeout returns the timeout for a given order type.
func orderTimeout(orderType string) time.Duration {
	if t, ok := orderTimeouts[orderType]; ok {
		return t
	}
	return defaultOrderTimeout
}

// --- Parameter allowlists for dangerous order types ---

// allowedFlakeRefPattern matches only our official flake refs.
// Format: github:jbouey/msp-flake#<output-name>
var allowedFlakeRefPattern = regexp.MustCompile(`^github:jbouey/msp-flake#[a-zA-Z0-9_-]+$`)

// allowedDownloadDomains are the only domains from which we accept package/ISO URLs.
// NOTE: No raw IPs — use DNS names only. Pinning to IPs leaks infrastructure details
// in the binary and breaks if the VPS migrates.
var allowedDownloadDomains = map[string]bool{
	"api.osiriscare.net":     true,
	"release.osiriscare.net": true,
	// SECURITY: github.com removed — too broad. Attacker could host malicious binaries
	// on any GitHub release. Use api.osiriscare.net/updates/ for all binary distribution.
}

// validateFlakeRef ensures flake_ref points to the official repo.
func validateFlakeRef(flakeRef string) error {
	if flakeRef == "" {
		return nil // Empty uses hardcoded default, which is safe
	}
	if !allowedFlakeRefPattern.MatchString(flakeRef) {
		return fmt.Errorf("flake_ref %q does not match allowed pattern (must be github:jbouey/msp-flake#<output>)", flakeRef)
	}
	return nil
}

// allowedRuleActions are the only valid L1 rule actions.
var allowedRuleActions = map[string]bool{
	"update_to_baseline_generation": true,
	"restart_av_service":            true,
	"run_backup_job":                true,
	"restart_logging_services":      true,
	"restore_firewall_baseline":     true,
	"run_windows_runbook":           true,
	"run_linux_runbook":             true,
	"escalate":                      true,
	"renew_certificate":             true,
	"cleanup_disk_space":            true,
}

// allowedRuleIDPattern matches valid promoted rule IDs (alphanumeric + hyphens + underscores).
var allowedRuleIDPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{3,64}$`)

// promotedRuleSchema is used to validate the YAML structure of promoted rules.
type promotedRuleSchema struct {
	ID              string                   `yaml:"id"`
	Name            string                   `yaml:"name"`
	Description     string                   `yaml:"description"`
	Conditions      []promotedConditionSchema `yaml:"conditions"`
	Action          string                   `yaml:"action"`
	ActionParams    map[string]interface{}   `yaml:"action_params"`
	HIPAAControls   []string                 `yaml:"hipaa_controls"`
	SeverityFilter  []string                 `yaml:"severity_filter"`
	Enabled         *bool                    `yaml:"enabled"`
	Priority        int                      `yaml:"priority"`
	CooldownSeconds int                      `yaml:"cooldown_seconds"`
	MaxRetries      int                      `yaml:"max_retries"`
	GPOManaged      bool                     `yaml:"gpo_managed"`
}

type promotedConditionSchema struct {
	Field    string      `yaml:"field"`
	Operator string      `yaml:"operator"`
	Value    interface{} `yaml:"value"`
}

// validatePromotedRule parses and validates a promoted rule YAML.
func validatePromotedRule(ruleID, ruleYAML string) error {
	if !allowedRuleIDPattern.MatchString(ruleID) {
		return fmt.Errorf("rule_id %q contains invalid characters", ruleID)
	}

	// Size limit: rules should be small (< 8KB)
	if len(ruleYAML) > 8192 {
		return fmt.Errorf("rule_yaml exceeds 8KB limit (%d bytes)", len(ruleYAML))
	}

	var rule promotedRuleSchema
	if err := yaml.Unmarshal([]byte(ruleYAML), &rule); err != nil {
		return fmt.Errorf("invalid YAML: %w", err)
	}

	// Rule ID in YAML must match the provided rule_id
	if rule.ID != ruleID {
		return fmt.Errorf("YAML id %q does not match rule_id %q", rule.ID, ruleID)
	}

	if rule.Name == "" {
		return fmt.Errorf("rule name is required")
	}

	if rule.Action == "" {
		return fmt.Errorf("rule action is required")
	}

	if !allowedRuleActions[rule.Action] {
		return fmt.Errorf("action %q not in allowed actions", rule.Action)
	}

	if len(rule.Conditions) == 0 {
		return fmt.Errorf("rule must have at least one condition")
	}

	// Validate each condition has required fields
	for i, cond := range rule.Conditions {
		if cond.Field == "" {
			return fmt.Errorf("condition[%d]: field is required", i)
		}
		if cond.Operator == "" {
			return fmt.Errorf("condition[%d]: operator is required", i)
		}
	}

	return nil
}

// validateDownloadURL ensures a package/ISO URL points to an allowed domain.
func validateDownloadURL(rawURL, fieldName string) error {
	if rawURL == "" {
		return fmt.Errorf("%s is required", fieldName)
	}
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return fmt.Errorf("invalid %s URL: %w", fieldName, err)
	}
	if parsed.Scheme != "https" {
		return fmt.Errorf("%s must use HTTPS (got %s)", fieldName, parsed.Scheme)
	}
	host := parsed.Hostname()
	if !allowedDownloadDomains[host] {
		return fmt.Errorf("%s domain %q not in allowlist", fieldName, host)
	}
	return nil
}

// Order represents a pending order from Central Command.
type Order struct {
	OrderID       string                 `json:"order_id"`
	OrderType     string                 `json:"order_type"`
	Parameters    map[string]interface{} `json:"parameters"`
	Nonce         string                 `json:"nonce,omitempty"`
	Signature     string                 `json:"signature,omitempty"`
	SignedPayload string                 `json:"signed_payload,omitempty"`
}

// OrderResult is the result of processing an order.
type OrderResult struct {
	OrderID string                 `json:"order_id"`
	Success bool                   `json:"success"`
	Result  map[string]interface{} `json:"result,omitempty"`
	Error   string                 `json:"error,omitempty"`
}

// HandlerFunc is the signature for order handlers.
type HandlerFunc func(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error)

// CompletionCallback is called after an order is processed to report results.
type CompletionCallback func(ctx context.Context, orderID string, success bool, result map[string]interface{}, errMsg string) error

// Processor dispatches and executes orders.
// AgentCounter provides a read-only view of connected agent count.
// Implemented by grpcserver.AgentRegistry; optional dependency for sensor_status handler.
type AgentCounter interface {
	ConnectedCount() int
}

// RuleReloadFunc is called after a promoted rule is deployed to trigger an L1 engine reload.
type RuleReloadFunc func()

type Processor struct {
	handlers     map[string]HandlerFunc
	onComplete   CompletionCallback
	onRuleReload RuleReloadFunc
	stateDir     string
	verifier     *crypto.OrderVerifier
	applianceID  string // This appliance's ID (from checkin response)
	agentCounter AgentCounter // Optional: set via SetAgentCounter for real sensor counts

	// Nonce replay protection: tracks used nonces to prevent replay attacks
	nonceMu    sync.Mutex
	usedNonces map[string]time.Time // nonce → first-seen timestamp

	// Order idempotency: tracks recently executed order IDs to skip duplicates
	executedMu     sync.Mutex
	executedOrders map[string]time.Time // order_id → execution timestamp

	// Phase 13.5 — signature-verification hardening
	pubkeyMu               sync.RWMutex
	lastDeliveredPubkeyHex string                          // Pubkey server delivered on most recent checkin — H6 trust source
	refreshCheckin         func(timeout time.Duration) bool // Trigger an immediate checkin + wait — H4 auto-refresh
	verifyRetryMu          sync.Mutex
	verifyRetried          map[string]time.Time // order_id → time retry was attempted (prevents loop)
}

// SetRuleReloader registers a callback to reload L1 rules after promoted rule deployment.
func (p *Processor) SetRuleReloader(fn RuleReloadFunc) {
	p.onRuleReload = fn
}

// NewProcessor creates a new order processor.
func NewProcessor(stateDir string, onComplete CompletionCallback) *Processor {
	p := &Processor{
		handlers:       make(map[string]HandlerFunc),
		onComplete:     onComplete,
		stateDir:       stateDir,
		verifier:       crypto.NewOrderVerifier(""),
		usedNonces:     make(map[string]time.Time),
		executedOrders: make(map[string]time.Time),
		verifyRetried:  make(map[string]time.Time),
	}

	// Load persisted nonces from previous sessions
	p.loadNonces()

	// Register built-in handlers
	p.handlers["force_checkin"] = p.handleForceCheckin
	p.handlers["run_drift"] = p.handleRunDrift
	p.handlers["sync_rules"] = p.handleSyncRules
	p.handlers["restart_agent"] = p.handleRestartAgent
	p.handlers["nixos_rebuild"] = p.handleNixOSRebuild
	p.handlers["update_agent"] = p.handleUpdateAgent
	p.handlers["update_iso"] = p.handleUpdateISO
	p.handlers["view_logs"] = p.handleViewLogs
	p.handlers["diagnostic"] = p.handleDiagnostic
	p.handlers["deploy_sensor"] = p.handleDeploySensor
	p.handlers["remove_sensor"] = p.handleRemoveSensor
	p.handlers["deploy_linux_sensor"] = p.handleDeployLinuxSensor
	p.handlers["remove_linux_sensor"] = p.handleRemoveLinuxSensor
	p.handlers["sensor_status"] = p.handleSensorStatus
	p.handlers["sync_promoted_rule"] = p.handleSyncPromotedRule
	p.handlers["healing"] = p.handleHealing
	p.handlers["update_credentials"] = p.handleUpdateCredentials
	p.handlers["update_daemon"] = p.handleUpdateDaemon
	p.handlers["validate_credential"] = p.handleValidateCredential
	p.handlers["disable_healing"] = p.handleDisableHealing
	p.handlers["enable_healing"] = p.handleEnableHealing
	p.handlers["rotate_wg_key"] = p.handleRotateWgKey
	p.handlers["isolate_host"] = p.handleIsolateHost
	p.handlers["chaos_quicktest"] = p.handleChaosQuicktest
	p.handlers["enable_emergency_access"] = p.handleEnableEmergencyAccess
	p.handlers["disable_emergency_access"] = p.handleDisableEmergencyAccess
	p.handlers["configure_dns"] = p.handleConfigureDNS

	return p
}

// RegisterHandler adds or replaces a handler for an order type.
// This allows subsystems (healing engine, drift checker, etc.) to inject their handlers.
func (p *Processor) RegisterHandler(orderType string, handler HandlerFunc) {
	p.handlers[orderType] = handler
}

// SetAgentCounter sets the agent counter used by handleSensorStatus to report
// real connected agent counts. If not set, sensor_status returns "registry_unavailable".
func (p *Processor) SetAgentCounter(ac AgentCounter) {
	p.agentCounter = ac
}

// SetServerPublicKey sets the Ed25519 public key used to verify order signatures.
// Called when the checkin response provides server_public_key.
func (p *Processor) SetServerPublicKey(hexKey string) error {
	return p.verifier.SetPublicKey(hexKey)
}

// SetPublicKeys sets current and previous public keys for key rotation support.
// Orders signed with either key will be accepted during the rotation window.
func (p *Processor) SetPublicKeys(currentHex string, previousHexes []string) error {
	return p.verifier.SetPublicKeys(currentHex, previousHexes)
}

// SetApplianceID sets this appliance's identity for host-scoped order verification.
// Orders signed with a target_appliance_id that doesn't match will be rejected.
func (p *Processor) SetApplianceID(id string) {
	p.applianceID = id
}

// SetLastDeliveredPubkey records the pubkey the server sent in the most
// recent successful checkin response. This is the H6 trust source —
// verifySignature will fall back to verifying against an envelope-
// advertised pubkey ONLY when it matches this value byte-for-byte.
//
// Called from the daemon's checkin handler on every checkin that
// returns server_public_key. Thread-safe via `pubkeyMu`.
func (p *Processor) SetLastDeliveredPubkey(hexKey string) {
	p.pubkeyMu.Lock()
	p.lastDeliveredPubkeyHex = hexKey
	p.pubkeyMu.Unlock()
}

// SetRefreshCheckinCallback registers a function the Processor calls
// when signature verification fails — to pull a fresh pubkey via an
// immediate checkin (H4). The callback blocks up to `timeout` waiting
// for the checkin to finish, returning true if it completed in time.
func (p *Processor) SetRefreshCheckinCallback(fn func(timeout time.Duration) bool) {
	p.refreshCheckin = fn
}

// VerifySignedPayload verifies an Ed25519 signature over an arbitrary canonical
// JSON payload signed by Central Command. Used by subsystems outside the order
// flow (e.g. reconcile plans in Session 205 Phase 3) that share the same
// signing keys. Returns nil on valid signature, error otherwise.
//
// This reuses the same current+previous key pair used for orders, so a
// reconcile plan issued during a key rotation window still verifies.
func (p *Processor) VerifySignedPayload(canonicalPayload, signatureHex string) error {
	return p.verifier.VerifyOrder(canonicalPayload, signatureHex)
}

// HasServerKey returns true if a server public key has been configured
// (i.e. at least one successful checkin has completed).
func (p *Processor) HasServerKey() bool {
	return p.verifier.HasKey()
}

// PurgeAllNonces drops every cached nonce in-memory. Called by the
// reconcile flow when the server advances nonce_epoch — any captured
// orders from before the epoch become unreplayable because the signed
// payload's epoch won't match the server's current one, but we ALSO
// clear the local cache so memory doesn't grow unbounded across
// reconciles. Also persists the empty state to disk so a subsequent
// restart doesn't resurrect the cleared nonces.
//
// Safe to call multiple times; a no-op on empty cache.
func (p *Processor) PurgeAllNonces() {
	p.nonceMu.Lock()
	defer p.nonceMu.Unlock()
	p.usedNonces = make(map[string]time.Time)
	p.persistNoncesLocked()
}

// ApplianceID returns the current appliance identity (empty if not yet set from checkin).
func (p *Processor) ApplianceID() string {
	return p.applianceID
}

// Process handles a single order: verify signature, dispatch to handler, report completion.
func (p *Processor) Process(ctx context.Context, order *Order) *OrderResult {
	if order.OrderID == "" || order.OrderType == "" {
		log.Printf("[orders] Skipping order with missing id or type")
		return nil
	}

	log.Printf("[orders] Processing order %s: %s", order.OrderID, order.OrderType)

	// Idempotency check: skip orders already executed in the last hour
	if p.alreadyExecuted(order.OrderID) {
		log.Printf("[orders] Skipping duplicate order %s (already executed recently)", order.OrderID)
		return &OrderResult{OrderID: order.OrderID, Success: true, Result: map[string]interface{}{"status": "already_executed"}}
	}

	// Verify Ed25519 signature before executing any order.
	// Orders without a signature are rejected when we have a server public key.
	if err := p.verifySignature(order); err != nil {
		errMsg := fmt.Sprintf("signature verification failed: %v", err)
		log.Printf("[orders] SECURITY: %s for order %s (type=%s)", errMsg, order.OrderID, order.OrderType)
		p.complete(ctx, order.OrderID, false, nil, errMsg)
		return &OrderResult{OrderID: order.OrderID, Success: false, Error: errMsg}
	}

	// Nonce replay protection: reject orders with previously-used nonces
	if order.Nonce != "" {
		if err := p.checkAndRecordNonce(order.Nonce); err != nil {
			errMsg := fmt.Sprintf("nonce replay detected: %v", err)
			log.Printf("[orders] SECURITY: %s for order %s (type=%s)", errMsg, order.OrderID, order.OrderType)
			p.complete(ctx, order.OrderID, false, nil, errMsg)
			return &OrderResult{OrderID: order.OrderID, Success: false, Error: errMsg}
		}
	}

	// Expiry validation: reject orders past their expires_at timestamp.
	// The expires_at is embedded in the signed payload (tamper-proof after signature verification).
	if order.SignedPayload != "" {
		var payload map[string]interface{}
		if err := json.Unmarshal([]byte(order.SignedPayload), &payload); err == nil {
			if expiresStr, ok := payload["expires_at"].(string); ok {
				expiresAt, err := time.Parse(time.RFC3339, expiresStr)
				if err == nil && time.Now().After(expiresAt) {
					errMsg := fmt.Sprintf("order expired at %s (current: %s)", expiresAt.Format(time.RFC3339), time.Now().UTC().Format(time.RFC3339))
					log.Printf("[orders] SECURITY: %s for order %s (type=%s)", errMsg, order.OrderID, order.OrderType)
					p.complete(ctx, order.OrderID, false, nil, errMsg)
					return &OrderResult{OrderID: order.OrderID, Success: false, Error: errMsg}
				}
			}
		}
	}

	handler, ok := p.handlers[order.OrderType]
	if !ok {
		errMsg := fmt.Sprintf("unknown order type: %s", order.OrderType)
		log.Printf("[orders] %s for order %s", errMsg, order.OrderID)
		p.complete(ctx, order.OrderID, false, nil, errMsg)
		return &OrderResult{OrderID: order.OrderID, Success: false, Error: errMsg}
	}

	params := order.Parameters
	if params == nil {
		params = map[string]interface{}{}
	}

	// Enforce per-order-type timeout to prevent handlers from blocking forever.
	timeout := orderTimeout(order.OrderType)
	handlerCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	// Pass order_id through ctx so handlers like handleUpdateDaemon can
	// write a pending-completion marker keyed by order_id without an
	// API change to every handler signature.
	handlerCtx = context.WithValue(handlerCtx, ctxKeyOrderID{}, order.OrderID)

	result, err := handler(handlerCtx, params)
	if err != nil {
		errMsg := err.Error()
		if handlerCtx.Err() == context.DeadlineExceeded {
			errMsg = fmt.Sprintf("order timed out after %s: %v", timeout, err)
		}
		log.Printf("[orders] Order %s failed: %s", order.OrderID, errMsg)
		// Clear nonce on execution failure so the order can be retried
		// after the backend auto-expires the failed completion (1 hour).
		// Security failures (bad signature, bad nonce) are caught above
		// and keep the nonce cached to prevent replay attacks.
		p.removeNonce(order.Nonce)
		p.complete(ctx, order.OrderID, false, nil, errMsg)
		return &OrderResult{OrderID: order.OrderID, Success: false, Error: errMsg}
	}

	log.Printf("[orders] Order %s completed successfully", order.OrderID)
	p.recordExecuted(order.OrderID)

	// Deferred-completion sentinel: handlers may return
	// status="update_pending" to defer the /complete POST until a
	// later condition is met (e.g. update_daemon waits for the new
	// binary to actually run + report its version after restart).
	// The poller in daemon.go is responsible for the eventual POST.
	if status, ok := result["status"].(string); ok && status == "update_pending" {
		log.Printf("[orders] Order %s deferred — completion will be posted after post-restart version verification", order.OrderID)
		return &OrderResult{OrderID: order.OrderID, Success: true, Result: result}
	}

	p.complete(ctx, order.OrderID, true, result, "")
	return &OrderResult{OrderID: order.OrderID, Success: true, Result: result}
}

// ctxKeyOrderID is the unexported context key that carries the
// current order_id from Process() into handlers without requiring
// every handler signature to change.
type ctxKeyOrderID struct{}

// orderIDFromContext returns the order_id propagated into the
// handler context by Process(), or "" if not present.
func orderIDFromContext(ctx context.Context) string {
	v, _ := ctx.Value(ctxKeyOrderID{}).(string)
	return v
}

// verifySignature checks the Ed25519 signature on an order, then verifies
// host scoping (target_appliance_id in the signed payload must match this appliance).
// Returns nil if the signature is valid or if verification is not yet configured
// (graceful degradation during rollout — logs a warning for unsigned orders).
// dangerousOrderTypes are order types that must NEVER execute without signature verification.
// These can modify binaries, execute commands, or alter security configuration.
var dangerousOrderTypes = map[string]bool{
	"update_daemon":               true,
	"nixos_rebuild":               true,
	"healing":                     true,
	"diagnostic":                  true,
	"sync_promoted_rule":          true,
	"configure_workstation_agent": true,
	"update_agent":                true,
	"enable_emergency_access":     true,
	"disable_emergency_access":    true,
}

func (p *Processor) verifySignature(order *Order) error {
	if !p.verifier.HasKey() {
		// No server public key yet (first checkin hasn't completed).
		// SECURITY: block dangerous order types until key is available.
		if dangerousOrderTypes[order.OrderType] {
			log.Printf("[orders] SECURITY: rejected %s order %s — no server public key yet (pre-checkin)",
				order.OrderType, order.OrderID)
			return fmt.Errorf("dangerous order %s rejected: server public key not yet received", order.OrderType)
		}
		// Allow safe order types (force_checkin, run_drift, restart_agent) through
		log.Printf("[orders] WARNING: allowing safe order %s (type=%s) without signature (pre-checkin)",
			order.OrderID, order.OrderType)
		return nil
	}

	if order.Signature == "" || order.SignedPayload == "" {
		log.Printf("[orders] SECURITY: rejected unsigned order %s (type=%s) — server must sign all orders",
			order.OrderID, order.OrderType)
		return fmt.Errorf("unsigned order rejected: order %s has no signature", order.OrderID)
	}

	// Step 1: Verify Ed25519 cryptographic signature.
	//
	// Phase 13.5 — hardened verification path:
	//   H6: if cache fails, try envelope's signing_pubkey_hex IF and only
	//       if it matches the pubkey the server most-recently delivered
	//       via checkin. Bounds trust.
	//   H4: if H6 also fails (or envelope carries no pubkey), fire an
	//       immediate checkin, wait up to 5s, retry the verify ONCE.
	//       The per-order retry map prevents retry loops.
	envelopeKeyHex := extractEnvelopePubkey(order.SignedPayload)
	p.pubkeyMu.RLock()
	trustedEnvelopeKey := p.lastDeliveredPubkeyHex
	p.pubkeyMu.RUnlock()

	verifyErr := p.verifier.VerifyOrderWithEnvelopeKey(
		order.SignedPayload, order.Signature, envelopeKeyHex, trustedEnvelopeKey,
	)

	if verifyErr != nil && p.refreshCheckin != nil && !p.alreadyRetriedVerify(order.OrderID) {
		// H4 — one-shot refresh retry.
		p.markVerifyRetried(order.OrderID)
		log.Printf("[orders] H4: verify failed for order %s; requesting immediate checkin + retry",
			order.OrderID)
		if p.refreshCheckin(5 * time.Second) {
			// Pubkey may have been refreshed — pull it again + retry.
			p.pubkeyMu.RLock()
			trustedEnvelopeKey = p.lastDeliveredPubkeyHex
			p.pubkeyMu.RUnlock()
			verifyErr = p.verifier.VerifyOrderWithEnvelopeKey(
				order.SignedPayload, order.Signature, envelopeKeyHex, trustedEnvelopeKey,
			)
			if verifyErr == nil {
				log.Printf("[orders] H4: verify PASSED after refresh for order %s", order.OrderID)
			} else {
				// Structured failure token — makes fleet-wide grep trivial.
				log.Printf("[orders] sig_verify_pubkey_stale_after_refresh order_id=%s type=%s err=%v",
					order.OrderID, order.OrderType, verifyErr)
			}
		} else {
			log.Printf("[orders] H4: refresh checkin did NOT complete within 5s for order %s",
				order.OrderID)
		}
	}
	if verifyErr != nil {
		return verifyErr
	}

	// Step 2: Verify host scoping — reject orders targeted at a different appliance.
	// The target_appliance_id is embedded in the signed payload (tamper-proof).
	if err := p.verifyHostScope(order); err != nil {
		return err
	}

	return nil
}

// extractEnvelopePubkey pulls the H6 advertised signing pubkey out of the
// signed payload JSON. Returns "" if the envelope has no such field or
// the payload is malformed — callers treat "" as "no envelope key, use
// only the cache path."
func extractEnvelopePubkey(signedPayload string) string {
	if signedPayload == "" {
		return ""
	}
	var m map[string]interface{}
	if err := json.Unmarshal([]byte(signedPayload), &m); err != nil {
		return ""
	}
	if v, ok := m["signing_pubkey_hex"].(string); ok {
		return v
	}
	return ""
}

// alreadyRetriedVerify returns true iff we've already done the H4
// refresh-retry dance for this order_id within the last 30 minutes.
// Prevents a persistently-bad-pubkey scenario from spamming checkins
// every poll cycle.
func (p *Processor) alreadyRetriedVerify(orderID string) bool {
	p.verifyRetryMu.Lock()
	defer p.verifyRetryMu.Unlock()
	// GC stale entries so the map doesn't grow unbounded
	cutoff := time.Now().Add(-30 * time.Minute)
	for id, t := range p.verifyRetried {
		if t.Before(cutoff) {
			delete(p.verifyRetried, id)
		}
	}
	_, ok := p.verifyRetried[orderID]
	return ok
}

func (p *Processor) markVerifyRetried(orderID string) {
	p.verifyRetryMu.Lock()
	p.verifyRetried[orderID] = time.Now()
	p.verifyRetryMu.Unlock()
}

// verifyHostScope checks that the signed payload's target_appliance_id matches
// this appliance's ID. Fleet-wide orders (no target_appliance_id) are allowed.
func (p *Processor) verifyHostScope(order *Order) error {
	if p.applianceID == "" {
		// Appliance ID not yet known (pre-first-checkin). Allow.
		return nil
	}

	// Parse the signed payload to extract target_appliance_id
	var payload map[string]interface{}
	if err := json.Unmarshal([]byte(order.SignedPayload), &payload); err != nil {
		return fmt.Errorf("parse signed payload for host scope check: %w", err)
	}

	target, ok := payload["target_appliance_id"]
	if !ok || target == nil {
		// No target_appliance_id in signed payload — fleet-wide order, allow.
		return nil
	}

	targetStr, ok := target.(string)
	if !ok || targetStr == "" {
		return nil
	}

	if targetStr != p.applianceID {
		return fmt.Errorf("host scope mismatch: order targets %q but this appliance is %q", targetStr, p.applianceID)
	}

	return nil
}

// ProcessAll handles a batch of orders sequentially.
func (p *Processor) ProcessAll(ctx context.Context, orders []Order) []*OrderResult {
	var results []*OrderResult
	for i := range orders {
		select {
		case <-ctx.Done():
			return results
		default:
		}
		if r := p.Process(ctx, &orders[i]); r != nil {
			results = append(results, r)
		}
	}
	return results
}

// CompletePendingRebuild checks for deferred rebuild completion on startup.
func (p *Processor) CompletePendingRebuild(ctx context.Context) {
	pendingFile := filepath.Join(p.stateDir, ".pending-rebuild-order")
	data, err := os.ReadFile(pendingFile)
	if err != nil {
		return // No pending rebuild
	}

	orderID := strings.TrimSpace(string(data))
	if orderID == "" {
		return
	}

	log.Printf("[orders] Completing deferred rebuild order %s", orderID)

	// System came up and is checking in — rebuild was successful
	result := map[string]interface{}{
		"status":              "rebuild_complete",
		"completed_after_restart": true,
		"message":             "System successfully restarted after rebuild",
	}

	p.complete(ctx, orderID, true, result, "")

	// Write .rebuild-verified marker — the NixOS watchdog timer reads this
	// to know it's safe to persist the rebuild with `nixos-rebuild switch`.
	// If this file doesn't appear within 10 minutes, the watchdog rolls back.
	verifiedPath := filepath.Join(p.stateDir, ".rebuild-verified")
	os.WriteFile(verifiedPath, []byte(time.Now().UTC().Format(time.RFC3339)), 0o644)
	log.Printf("[orders] Wrote %s — watchdog will persist rebuild", verifiedPath)

	// Cleanup rebuild state files
	os.Remove(pendingFile)
	os.Remove(filepath.Join(p.stateDir, ".rebuild-in-progress"))
}

// HandlerCount returns the number of registered handlers.
func (p *Processor) HandlerCount() int {
	return len(p.handlers)
}

// CompletePendingUpdate handles deferred update_daemon completion at
// startup. Reads the pending-update marker (if any) and posts the
// /complete result based on whether the running version matches the
// expected version.
//
// Timing: the original handler scheduled a 10s restart timer + a 70s
// health check timer. The health check rolls back the binary if the
// new daemon is unhealthy. So the window from "new daemon first
// starts" to "health check has decided" is roughly 60s. We wait until
// at least 90s after the marker was written before making the
// decision, ensuring the health check has had time to run + roll back
// if needed. If the rollback fired, the daemon currently running is
// the OLD binary (Version != ExpectedVersion) and we report failure.
//
// If we crashed/restarted multiple times mid-poll, the marker
// persists and we pick up where we left off. The TimeoutAt field is
// an absolute wall-clock deadline; once exceeded we always report
// failure rather than wait indefinitely.
//
// Runs as a background goroutine so daemon startup isn't blocked.
func (p *Processor) CompletePendingUpdate(ctx context.Context, currentVersion string) {
	pending := LoadPendingUpdate(p.stateDir)
	if pending == nil {
		return
	}

	go func() {
		log.Printf("[orders] Found pending-update marker: order=%s expected=%s current=%s scheduled=%s",
			pending.OrderID, pending.ExpectedVersion, currentVersion, pending.ScheduledAt.Format(time.RFC3339))

		// Sleep until the health-check window (90s after scheduled
		// restart) has elapsed. If we're already past that window,
		// the sleep is a no-op.
		decisionTime := pending.ScheduledAt.Add(90 * time.Second)
		if wait := time.Until(decisionTime); wait > 0 {
			log.Printf("[orders] Pending update: waiting %s for health-check window", wait.Round(time.Second))
			select {
			case <-time.After(wait):
			case <-ctx.Done():
				log.Printf("[orders] Pending update: ctx cancelled before decision window")
				return
			}
		}

		// Check absolute timeout — even if version still doesn't
		// match, give up and report failure rather than wait forever.
		if time.Now().After(pending.TimeoutAt) && currentVersion != pending.ExpectedVersion {
			errMsg := fmt.Sprintf("post-restart version verification timed out (expected=%s, current=%s, deadline=%s)",
				pending.ExpectedVersion, currentVersion, pending.TimeoutAt.Format(time.RFC3339))
			log.Printf("[orders] Pending update %s TIMEOUT — reporting failure: %s", pending.OrderID, errMsg)
			p.complete(ctx, pending.OrderID, false, nil, errMsg)
			_ = ClearPendingUpdate(p.stateDir)
			return
		}

		if currentVersion == pending.ExpectedVersion {
			log.Printf("[orders] Pending update %s CONFIRMED — version=%s", pending.OrderID, currentVersion)
			result := map[string]interface{}{
				"status":           "update_confirmed",
				"version":          currentVersion,
				"expected_version": pending.ExpectedVersion,
				"verified_after":   time.Since(pending.ScheduledAt).Round(time.Second).String(),
			}
			p.complete(ctx, pending.OrderID, true, result, "")
			_ = ClearPendingUpdate(p.stateDir)
			return
		}

		// Version mismatch and we're inside the timeout window —
		// the most likely explanation is that the 70s health check
		// rolled back the binary. Report failure with that context.
		errMsg := fmt.Sprintf("post-restart version mismatch — expected=%s, running=%s — health check likely rolled back",
			pending.ExpectedVersion, currentVersion)
		log.Printf("[orders] Pending update %s ROLLED BACK — reporting failure: %s", pending.OrderID, errMsg)
		p.complete(ctx, pending.OrderID, false, nil, errMsg)
		_ = ClearPendingUpdate(p.stateDir)
	}()
}

// --- Completion helper ---

func (p *Processor) complete(ctx context.Context, orderID string, success bool, result map[string]interface{}, errMsg string) {
	if p.onComplete == nil {
		return
	}
	if err := p.onComplete(ctx, orderID, success, result, errMsg); err != nil {
		log.Printf("[orders] Failed to report completion for %s: %v", orderID, err)
	}
}

// --- Built-in handlers ---

func (p *Processor) handleForceCheckin(_ context.Context, _ map[string]interface{}) (map[string]interface{}, error) {
	// Actual checkin is handled by the daemon's phone-home client
	return map[string]interface{}{"status": "checkin_triggered"}, nil
}

func (p *Processor) handleRunDrift(_ context.Context, _ map[string]interface{}) (map[string]interface{}, error) {
	// Actual drift detection is handled by the daemon's drift checker
	return map[string]interface{}{"status": "drift_scan_triggered"}, nil
}

func (p *Processor) handleSyncRules(_ context.Context, _ map[string]interface{}) (map[string]interface{}, error) {
	// Rules sync is handled by the daemon's rules syncer
	return map[string]interface{}{"status": "sync_triggered"}, nil
}

func (p *Processor) handleRestartAgent(_ context.Context, _ map[string]interface{}) (map[string]interface{}, error) {
	log.Printf("[orders] Scheduling agent restart in 5 seconds")

	// Use systemd-run to escape ProtectSystem=strict sandbox (NixOS PATH issue).
	go func() {
		time.Sleep(5 * time.Second)
		cmd := exec.CommandContext(context.Background(), "systemd-run",
			"--unit=msp-daemon-restart", "--collect",
			"--property=TimeoutStartSec=30",
			"--setenv=PATH=/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin",
			"/run/current-system/sw/bin/bash", "-c",
			"systemctl restart appliance-daemon")
		if out, err := cmd.CombinedOutput(); err != nil {
			log.Printf("[orders] Restart failed: %v\n%s", err, string(out))
		}
	}()

	return map[string]interface{}{"status": "restart_scheduled"}, nil
}

func (p *Processor) handleNixOSRebuild(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	flakeRef, _ := params["flake_ref"].(string)

	// Validate flake ref against allowlist before proceeding
	if err := validateFlakeRef(flakeRef); err != nil {
		return nil, fmt.Errorf("SECURITY: %w", err)
	}

	if flakeRef == "" {
		flakeRef = "github:jbouey/msp-flake#osiriscare-appliance-disk"
	}

	// Read current system for rollback
	currentSystem, _ := os.Readlink("/run/current-system")

	// Write marker file
	markerData := map[string]interface{}{
		"timestamp":       time.Now().UTC().Format(time.RFC3339),
		"previous_system": currentSystem,
		"flake_ref":       flakeRef,
	}
	markerJSON, _ := json.Marshal(markerData)
	markerPath := filepath.Join(p.stateDir, ".rebuild-in-progress")
	if err := os.WriteFile(markerPath, markerJSON, 0o644); err != nil {
		return nil, fmt.Errorf("write rebuild marker: %w", err)
	}

	// Persist order_id for post-restart completion
	if orderID, _ := params["_order_id"].(string); orderID != "" {
		pendingPath := filepath.Join(p.stateDir, ".pending-rebuild-order")
		os.WriteFile(pendingPath, []byte(orderID), 0o644)
	}

	// Remove stale systemd override that may point to an old bind-mounted binary.
	// Previous hot-deploys create /run/systemd/system/appliance-daemon.service.d/override.conf
	// which overrides ExecStart to /var/lib/msp/appliance-daemon. After nixos-rebuild,
	// the nix store binary is correct but the override makes the service use the old one.
	overridePath := "/run/systemd/system/appliance-daemon.service.d/override.conf"
	if _, err := os.Stat(overridePath); err == nil {
		os.Remove(overridePath)
		log.Printf("[orders] Removed stale systemd override at %s", overridePath)
	}

	log.Printf("[orders] Two-phase rebuild: nixos-rebuild test --flake %s --refresh", flakeRef)

	// Run nixos-rebuild test via systemd-run to escape ProtectSystem=strict sandbox.
	// --unit=msp-rebuild: predictable unit name for tracking/cancellation
	// --pipe: forward stdout/stderr through to CombinedOutput
	// --collect: clean up unit after completion
	// --wait: block until rebuild finishes
	cmd := exec.CommandContext(ctx, "systemd-run",
		"--unit=msp-rebuild", "--wait", "--pipe", "--collect",
		"--property=TimeoutStartSec=600",
		"/run/current-system/sw/bin/nixos-rebuild", "test", "--flake", flakeRef, "--refresh")

	output, err := cmd.CombinedOutput()
	if err != nil {
		os.Remove(markerPath)
		// Truncate output for error reporting
		outStr := string(output)
		if len(outStr) > 500 {
			outStr = outStr[len(outStr)-500:]
		}
		log.Printf("[orders] nixos-rebuild test failed (exit %v)", err)
		return nil, fmt.Errorf("nixos-rebuild test failed: %v\n%s", err, outStr)
	}

	log.Printf("[orders] nixos-rebuild test succeeded, scheduling daemon restart in 10s")

	// Schedule restart — the daemon will come back up and call CompletePendingRebuild()
	// Use systemd-run to escape ProtectSystem=strict sandbox (NixOS PATH issue).
	go func() {
		time.Sleep(10 * time.Second)
		cmd := exec.CommandContext(context.Background(), "systemd-run",
			"--unit=msp-daemon-restart", "--collect",
			"--property=TimeoutStartSec=30",
			"--setenv=PATH=/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin",
			"/run/current-system/sw/bin/bash", "-c",
			"systemctl restart appliance-daemon")
		if out, err := cmd.CombinedOutput(); err != nil {
			log.Printf("[orders] Daemon restart failed: %v\n%s", err, string(out))
		}
	}()

	return map[string]interface{}{
		"status":          "test_activated",
		"previous_system": currentSystem,
		"message":         "NixOS rebuild test activated. Watchdog will persist after successful checkin or rollback after 10min.",
	}, nil
}

func (p *Processor) handleUpdateAgent(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	packageURL, _ := params["package_url"].(string)
	version, _ := params["version"].(string)
	expectedSHA, _ := params["binary_sha256"].(string)

	// Validate package URL against domain allowlist
	if err := validateDownloadURL(packageURL, "package_url"); err != nil {
		return nil, fmt.Errorf("SECURITY: %w", err)
	}
	if version == "" {
		return nil, fmt.Errorf("version is required")
	}

	// Download agent binary to temp file
	log.Printf("[orders] Downloading agent binary v%s from %s", version, packageURL)
	client := &http.Client{Timeout: 5 * time.Minute}
	resp, err := client.Get(packageURL)
	if err != nil {
		return nil, fmt.Errorf("download failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("download returned HTTP %d", resp.StatusCode)
	}

	tmpFile, err := os.CreateTemp("", "osiris-agent-*.exe")
	if err != nil {
		return nil, fmt.Errorf("create temp file: %w", err)
	}
	tmpPath := tmpFile.Name()
	defer os.Remove(tmpPath)

	hasher := sha256.New()
	written, err := io.Copy(io.MultiWriter(tmpFile, hasher), resp.Body)
	tmpFile.Close()
	if err != nil {
		return nil, fmt.Errorf("download write failed: %w", err)
	}

	actualSHA := hex.EncodeToString(hasher.Sum(nil))
	if expectedSHA != "" && actualSHA != expectedSHA {
		return nil, fmt.Errorf("SHA256 mismatch: expected %s, got %s", expectedSHA, actualSHA)
	}

	// Install: move to agent directory
	agentDir := filepath.Join(p.stateDir, "agent")
	if err := os.MkdirAll(agentDir, 0755); err != nil {
		return nil, fmt.Errorf("create agent dir: %w", err)
	}

	destPath := filepath.Join(agentDir, "osiris-agent.exe")
	versionPath := filepath.Join(agentDir, "VERSION")

	// Atomic replace: copy to temp in target dir, then rename
	destTmp := destPath + ".tmp"
	src, err := os.Open(tmpPath)
	if err != nil {
		return nil, fmt.Errorf("open temp: %w", err)
	}
	dst, err := os.OpenFile(destTmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0755)
	if err != nil {
		src.Close()
		return nil, fmt.Errorf("create dest: %w", err)
	}
	_, err = io.Copy(dst, src)
	src.Close()
	dst.Close()
	if err != nil {
		os.Remove(destTmp)
		return nil, fmt.Errorf("copy failed: %w", err)
	}

	if err := os.Rename(destTmp, destPath); err != nil {
		os.Remove(destTmp)
		return nil, fmt.Errorf("rename failed: %w", err)
	}

	// Write VERSION file
	if err := os.WriteFile(versionPath, []byte(version), 0644); err != nil {
		log.Printf("[orders] WARNING: failed to write VERSION file: %v", err)
	}

	log.Printf("[orders] Agent binary updated to v%s (%d bytes, sha256=%s)", version, written, actualSHA)

	return map[string]interface{}{
		"status":  "updated",
		"version": version,
		"size":    written,
		"sha256":  actualSHA,
		"message": "Agent binary updated — autodeploy will distribute to workstations",
	}, nil
}

func (p *Processor) handleUpdateISO(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	version, _ := params["version"].(string)
	isoURL, _ := params["iso_url"].(string)

	if version == "" {
		return nil, fmt.Errorf("version is required")
	}
	// Validate ISO URL against domain allowlist
	if err := validateDownloadURL(isoURL, "iso_url"); err != nil {
		return nil, fmt.Errorf("SECURITY: %w", err)
	}

	return map[string]interface{}{
		"status":  "update_received",
		"version": version,
		"message": "ISO update will be applied during maintenance window",
	}, nil
}

func (p *Processor) handleViewLogs(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	lines := 50
	if l, ok := params["lines"].(float64); ok && l > 0 {
		lines = int(l)
		if lines > 500 {
			lines = 500
		}
	}

	cmd := exec.CommandContext(context.Background(), "journalctl", "-u", "appliance-daemon", "--no-pager", "-n", fmt.Sprintf("%d", lines))
	output, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("journalctl: %w", err)
	}

	return map[string]interface{}{
		"logs":  string(output),
		"lines": lines,
	}, nil
}

// allowedDiagnostics defines whitelisted diagnostic commands.
var allowedDiagnostics = map[string][]string{
	"agent_status":    {"systemctl", "status", "appliance-daemon"},
	"agent_logs":      {"journalctl", "-u", "appliance-daemon", "--no-pager", "-n", "100"},
	"system_logs":     {"journalctl", "--no-pager", "-n", "100"},
	"disk_usage":      {"df", "-h"},
	"memory":          {"free", "-h"},
	"uptime":          {"uptime"},
	"network":         {"ip", "addr", "show"},
	"dns":             {"cat", "/etc/resolv.conf"},
	"time_sync":       {"timedatectl", "status"},
	"nix_generations":  {"nix-env", "--list-generations", "-p", "/nix/var/nix/profiles/system"},
	"current_system":  {"readlink", "/run/current-system"},
	"services":        {"systemctl", "list-units", "--type=service", "--state=running", "--no-pager"},
	"firewall":        {"nft", "list", "ruleset"},
	"evidence_queue":  {"ls", "-la", "/var/lib/msp/evidence/"},
	"rebuild_status":  {"cat", "/var/lib/msp/.rebuild-in-progress"},
	"nic_list":        {"ip", "link", "show"},
	"boot_source":     {"cat", "/proc/cmdline"},
	"mount_root":      {"findmnt", "-n", "-o", "SOURCE,FSTYPE,TARGET", "/"},
	"wireguard":       {"wg", "show"},
	"order_nonces":    {"cat", "/var/lib/msp/used_nonces.json"},
}

func (p *Processor) handleDiagnostic(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	command, _ := params["command"].(string)
	if command == "" {
		return nil, fmt.Errorf("command is required")
	}

	args, ok := allowedDiagnostics[command]
	if !ok {
		return nil, fmt.Errorf("command %q not in whitelist", command)
	}

	cmd := exec.CommandContext(context.Background(), args[0], args[1:]...)
	output, err := cmd.CombinedOutput()

	exitCode := 0
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else {
			exitCode = -1
		}
	}

	// Truncate output to 2000 chars
	outStr := string(output)
	if len(outStr) > 2000 {
		outStr = outStr[:2000] + "\n... (truncated)"
	}

	return map[string]interface{}{
		"command":   command,
		"exit_code": exitCode,
		"output":    outStr,
	}, nil
}

// handleConfigureDNS writes extra_hosts entries to config.yaml and restarts
// the msp-dns-hosts service. This allows Central Command to push DNS entries
// (like AD domain controller hostnames) to appliances without SSH access.
//
// Parameters:
//
//	extra_hosts: map[string]string  e.g. {"NVDC01": "192.168.88.250"}
func (p *Processor) handleConfigureDNS(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	hostsRaw, ok := params["extra_hosts"]
	if !ok {
		return nil, fmt.Errorf("extra_hosts parameter required")
	}

	hostsMap, ok := hostsRaw.(map[string]interface{})
	if !ok {
		return nil, fmt.Errorf("extra_hosts must be a map of hostname→IP")
	}

	// Validate entries
	entries := make(map[string]string)
	for hostname, ipRaw := range hostsMap {
		ip, ok := ipRaw.(string)
		if !ok {
			return nil, fmt.Errorf("invalid IP for host %s", hostname)
		}
		// Basic validation: hostname must be alphanumeric+dash, IP must have dots
		if len(hostname) == 0 || len(hostname) > 63 {
			return nil, fmt.Errorf("invalid hostname: %s", hostname)
		}
		if len(ip) < 7 || len(ip) > 45 {
			return nil, fmt.Errorf("invalid IP: %s", ip)
		}
		entries[hostname] = ip
	}

	configPath := "/var/lib/msp/config.yaml"
	data, err := os.ReadFile(configPath)
	if err != nil {
		return nil, fmt.Errorf("failed to read config.yaml: %w", err)
	}

	// Parse YAML
	var config map[string]interface{}
	if err := yaml.Unmarshal(data, &config); err != nil {
		return nil, fmt.Errorf("failed to parse config.yaml: %w", err)
	}

	// Merge extra_hosts (don't replace — add/update entries)
	existing, _ := config["extra_hosts"].(map[string]interface{})
	if existing == nil {
		existing = make(map[string]interface{})
	}
	for hostname, ip := range entries {
		existing[hostname] = ip
	}
	config["extra_hosts"] = existing

	// Write back
	out, err := yaml.Marshal(config)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal config.yaml: %w", err)
	}
	if err := os.WriteFile(configPath, out, 0644); err != nil {
		return nil, fmt.Errorf("failed to write config.yaml: %w", err)
	}

	// Restart the DNS hosts service to apply
	cmd := exec.Command("systemctl", "restart", "msp-dns-hosts")
	restartOut, restartErr := cmd.CombinedOutput()

	log.Printf("[orders] DNS configured via fleet order: %d entries, restart=%s", len(entries), string(restartOut))

	result := map[string]interface{}{
		"entries_written": len(entries),
		"hosts":           entries,
	}
	if restartErr != nil {
		result["restart_error"] = restartErr.Error()
	}
	return result, nil
}

func (p *Processor) handleDeploySensor(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	hostname, _ := params["hostname"].(string)
	if hostname == "" {
		return nil, fmt.Errorf("hostname is required")
	}

	return map[string]interface{}{
		"status":   "not_implemented",
		"hostname": hostname,
		"error":    "sensor deployment via fleet orders is not yet implemented",
	}, nil
}

func (p *Processor) handleRemoveSensor(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	hostname, _ := params["hostname"].(string)
	if hostname == "" {
		return nil, fmt.Errorf("hostname is required")
	}

	return map[string]interface{}{
		"status":   "not_implemented",
		"hostname": hostname,
		"error":    "sensor removal via fleet orders is not yet implemented",
	}, nil
}

func (p *Processor) handleDeployLinuxSensor(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	hostname, _ := params["hostname"].(string)
	if hostname == "" {
		return nil, fmt.Errorf("hostname is required")
	}

	return map[string]interface{}{
		"status":   "not_implemented",
		"hostname": hostname,
		"error":    "sensor deployment via fleet orders is not yet implemented",
	}, nil
}

func (p *Processor) handleRemoveLinuxSensor(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	hostname, _ := params["hostname"].(string)
	if hostname == "" {
		return nil, fmt.Errorf("hostname is required")
	}

	return map[string]interface{}{
		"status":   "not_implemented",
		"hostname": hostname,
		"error":    "sensor removal via fleet orders is not yet implemented",
	}, nil
}

func (p *Processor) handleSensorStatus(_ context.Context, _ map[string]interface{}) (map[string]interface{}, error) {
	if p.agentCounter == nil {
		return map[string]interface{}{
			"status": "registry_unavailable",
			"error":  "agent registry is not configured",
		}, nil
	}

	count := p.agentCounter.ConnectedCount()
	return map[string]interface{}{
		"status":               "collected",
		"total_active_sensors": count,
	}, nil
}

func (p *Processor) handleSyncPromotedRule(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	ruleID, _ := params["rule_id"].(string)
	ruleYAML, _ := params["rule_yaml"].(string)

	if ruleID == "" || ruleYAML == "" {
		return nil, fmt.Errorf("rule_id and rule_yaml are required")
	}

	// Validate rule YAML against schema before writing to disk
	if err := validatePromotedRule(ruleID, ruleYAML); err != nil {
		return nil, fmt.Errorf("SECURITY: promoted rule validation failed: %w", err)
	}

	promotedDir := filepath.Join(p.stateDir, "rules", "promoted")
	if err := os.MkdirAll(promotedDir, 0o755); err != nil {
		return nil, fmt.Errorf("create promoted rules dir: %w", err)
	}

	rulePath := filepath.Join(promotedDir, ruleID+".yaml")
	// SECURITY: prevent path traversal — ensure rulePath stays within promotedDir
	if !strings.HasPrefix(filepath.Clean(rulePath), filepath.Clean(promotedDir)) {
		return nil, fmt.Errorf("SECURITY: rule_id %q escapes promoted directory", ruleID)
	}
	if _, err := os.Stat(rulePath); err == nil {
		return map[string]interface{}{
			"status":  "already_exists",
			"rule_id": ruleID,
		}, nil
	}

	if err := os.WriteFile(rulePath, []byte(ruleYAML), 0o600); err != nil {
		return nil, fmt.Errorf("write promoted rule: %w", err)
	}

	// Trigger L1 engine reload so the new rule is active immediately
	if p.onRuleReload != nil {
		log.Printf("[orders] Reloading L1 rules after deploying promoted rule %s", ruleID)
		p.onRuleReload()
	}

	return map[string]interface{}{
		"status":  "deployed",
		"rule_id": ruleID,
	}, nil
}

func (p *Processor) handleHealing(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	// Fallback stub — the daemon overrides this via RegisterHandler("healing", ...)
	// with executeHealingOrder() which runs runbooks via WinRM/SSH/bash.
	// If this code runs, the daemon failed to register the real handler.
	runbookID, _ := params["runbook_id"].(string)
	log.Printf("[orders] WARNING: healing stub invoked for %s — real handler not registered", runbookID)
	return nil, fmt.Errorf("healing handler not initialized — daemon must register executeHealingOrder")
}

func (p *Processor) handleChaosQuicktest(_ context.Context, _ map[string]interface{}) (map[string]interface{}, error) {
	// Fallback stub — the daemon overrides this via RegisterHandler("chaos_quicktest", ...)
	// with handleChaosQuicktest() which injects drift via WinRM.
	// If this code runs, the daemon failed to register the real handler.
	log.Printf("[orders] WARNING: chaos_quicktest stub invoked — real handler not registered")
	return nil, fmt.Errorf("chaos_quicktest handler not initialized — daemon must register real handler")
}

func (p *Processor) handleUpdateCredentials(_ context.Context, _ map[string]interface{}) (map[string]interface{}, error) {
	// Credential refresh is handled by the daemon's phone-home client
	return map[string]interface{}{"status": "credential_refresh_triggered"}, nil
}

// handleValidateCredential tests WinRM connectivity with a credential.
// The daemon calls back to Central Command with the result via checkin telemetry.
func (p *Processor) handleValidateCredential(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	credID, _ := params["credential_id"].(string)
	hostname, _ := params["hostname"].(string)
	credType, _ := params["credential_type"].(string)

	if credID == "" {
		return nil, fmt.Errorf("credential_id is required")
	}

	result := map[string]interface{}{
		"credential_id":   credID,
		"hostname":        hostname,
		"credential_type": credType,
		"can_connect":     false,
		"can_read_ad":     false,
		"is_domain_admin": false,
	}

	// The actual WinRM test is performed by the daemon via RegisterHandler override.
	// This stub returns the result shape; the daemon injects the real handler
	// that has access to winTargets and the WinRM executor.
	log.Printf("[orders] validate_credential stub for %s (%s) — daemon must register real handler", hostname, credType)
	result["status"] = "stub_only"
	result["error"] = "validate_credential handler not initialized — daemon must register real handler"
	return result, nil
}

// handleUpdateDaemon downloads a new daemon binary, verifies its SHA256 hash,
// writes it to /var/lib/msp/appliance-daemon, creates a systemd override to use
// the new binary, and schedules a daemon restart.
func (p *Processor) handleUpdateDaemon(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	binaryURL, _ := params["binary_url"].(string)
	expectedHash, _ := params["binary_sha256"].(string)
	version, _ := params["version"].(string)

	if err := validateDownloadURL(binaryURL, "binary_url"); err != nil {
		return nil, fmt.Errorf("SECURITY: %w", err)
	}
	if expectedHash == "" {
		return nil, fmt.Errorf("binary_sha256 is required for integrity verification")
	}
	if len(expectedHash) != 64 {
		return nil, fmt.Errorf("binary_sha256 must be 64 hex chars, got %d", len(expectedHash))
	}

	// Download binary
	log.Printf("[orders] Downloading daemon binary from %s", binaryURL)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, binaryURL, nil)
	if err != nil {
		return nil, fmt.Errorf("create download request: %w", err)
	}

	client := &http.Client{Timeout: 5 * time.Minute}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("download daemon binary: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("download failed: HTTP %d", resp.StatusCode)
	}

	// Read with size limit (100MB max)
	const maxSize = 100 * 1024 * 1024
	limitReader := io.LimitReader(resp.Body, maxSize+1)
	data, err := io.ReadAll(limitReader)
	if err != nil {
		return nil, fmt.Errorf("read download: %w", err)
	}
	if len(data) > maxSize {
		return nil, fmt.Errorf("binary exceeds 100MB size limit")
	}

	// Verify SHA256
	actualHash := sha256.Sum256(data)
	actualHex := hex.EncodeToString(actualHash[:])
	if actualHex != expectedHash {
		return nil, fmt.Errorf("SHA256 mismatch: expected %s, got %s", expectedHash, actualHex)
	}
	log.Printf("[orders] Binary SHA256 verified: %s (%d bytes)", actualHex, len(data))

	// Write to state dir — save last-known-good before replacing
	binaryPath := filepath.Join(p.stateDir, "appliance-daemon")
	backupPath := binaryPath + ".last-good"
	if _, err := os.Stat(binaryPath); err == nil {
		// Backup current binary as rollback target
		if copyErr := copyFile(binaryPath, backupPath); copyErr != nil {
			log.Printf("[orders] Warning: could not backup current binary: %v", copyErr)
		} else {
			log.Printf("[orders] Backed up current binary to %s", backupPath)
		}
	}

	tmpPath := binaryPath + ".tmp"
	if err := os.WriteFile(tmpPath, data, 0o755); err != nil {
		return nil, fmt.Errorf("write binary: %w", err)
	}
	if err := os.Rename(tmpPath, binaryPath); err != nil {
		os.Remove(tmpPath)
		return nil, fmt.Errorf("install binary: %w", err)
	}
	log.Printf("[orders] Daemon binary installed to %s", binaryPath)

	// Create systemd override to use the new binary.
	// The daemon runs under ProtectSystem=strict, so /etc and /run/systemd are
	// read-only from inside the sandbox. Write the override content to a temp file
	// in the state dir (writable), then use systemd-run to install it outside the
	// sandbox — same pattern as handleNixOSRebuild.
	overrideContent := fmt.Sprintf("[Service]\nExecStart=\nExecStart=%s\n", binaryPath)
	overrideTmp := filepath.Join(p.stateDir, ".override.conf.tmp")
	if err := os.WriteFile(overrideTmp, []byte(overrideContent), 0o644); err != nil {
		return nil, fmt.Errorf("write temp override: %w", err)
	}
	defer os.Remove(overrideTmp)

	overrideDir := "/run/systemd/system/appliance-daemon.service.d"
	installScript := fmt.Sprintf(
		"mkdir -p %s && cp %s %s/override.conf && systemctl daemon-reload",
		overrideDir, overrideTmp, overrideDir)

	// NixOS: /bin/bash doesn't exist, and systemd-run transient units have minimal PATH.
	// Use /run/current-system/sw/bin/bash and set PATH to include coreutils + systemd.
	// Use unique unit names (timestamp suffix) to avoid collisions with previous runs —
	// systemd-run refuses to create a unit if a dead transient with the same name exists.
	unitSuffix := fmt.Sprintf("%d", time.Now().UnixMilli())
	installCmd := exec.CommandContext(ctx, "systemd-run",
		"--unit=msp-daemon-update-"+unitSuffix, "--wait", "--pipe", "--collect",
		"--property=TimeoutStartSec=30",
		"--setenv=PATH=/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin",
		"/run/current-system/sw/bin/bash", "-c", installScript)
	installOut, err := installCmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("install systemd override via systemd-run: %v\n%s", err, string(installOut))
	}
	log.Printf("[orders] Systemd override installed to %s via systemd-run", overrideDir)

	// Schedule restart + health check via systemd transient units.
	// CRITICAL: The old approach used an in-process goroutine that died when
	// `systemctl restart` killed the daemon. The health check never ran.
	// Fix: use systemd-run timers that survive the process restart.
	log.Printf("[orders] Scheduling daemon restart in 10 seconds (version=%s) with external health check", version)

	bashPath := "/run/current-system/sw/bin/bash"
	envPath := "PATH=/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin"

	// Step 1: Schedule the restart as a systemd transient timer (fires in 10s)
	restartScript := "systemctl restart appliance-daemon"
	restartCmd := exec.CommandContext(ctx, "systemd-run",
		"--unit=msp-daemon-restart-"+unitSuffix,
		"--timer-property=AccuracySec=1s",
		"--on-active=10s",
		"--collect",
		"--setenv="+envPath,
		bashPath, "-c", restartScript)
	if out, err := restartCmd.CombinedOutput(); err != nil {
		log.Printf("[orders] Failed to schedule restart timer: %v\n%s", err, string(out))
		return nil, fmt.Errorf("failed to schedule restart: %v", err)
	}

	// Step 2: Schedule the health check as a SEPARATE transient timer (fires in 70s).
	// This timer is managed by systemd, NOT by the daemon process.
	// It survives the daemon restart and fires independently.
	// Health check: verify the daemon is running AND reports the expected version.
	// We check version output instead of /proc/PID/exe path because NixOS
	// resolves symlinks through /nix/store, making path comparison unreliable.
	healthScript := fmt.Sprintf(`
		if systemctl is-active appliance-daemon >/dev/null 2>&1; then
			RUNNING_VER=$(%s --version 2>&1 | tail -1 | awk '{print $NF}')
			if [ "$RUNNING_VER" = "%s" ]; then
				echo "Health check passed — daemon running version %s"
			else
				echo "HEALTH CHECK FAILED — expected %s, got $RUNNING_VER — rolling back"
				cp '%s' '%s' 2>/dev/null && systemctl restart appliance-daemon
			fi
		else
			echo "HEALTH CHECK FAILED — daemon not active — rolling back"
			cp '%s' '%s' 2>/dev/null && systemctl restart appliance-daemon
		fi
	`, binaryPath, version, version, version, backupPath, binaryPath, backupPath, binaryPath)

	healthCmd := exec.CommandContext(ctx, "systemd-run",
		"--unit=msp-daemon-healthcheck-"+unitSuffix,
		"--timer-property=AccuracySec=1s",
		"--on-active=70s",
		"--collect",
		"--setenv="+envPath,
		bashPath, "-c", healthScript)
	if out, err := healthCmd.CombinedOutput(); err != nil {
		log.Printf("[orders] Failed to schedule health check timer: %v\n%s", err, string(out))
		// Don't fail the order — restart is already scheduled. Health check is best-effort.
	}

	// Write the deferred-completion marker BEFORE returning. The
	// processor will see status="update_pending" and skip its
	// auto-complete call. After the scheduled restart, the new
	// daemon's startup loads this marker and posts /complete only
	// when its actual --version output matches the expected version
	// (or after a 10-min wall-clock timeout, whichever comes first).
	// Without this, the order ACK would race ahead of the restart
	// and a rolled-back upgrade would leave the backend believing
	// the upgrade succeeded — site_appliances.agent_version would
	// silently lag forever.
	orderID := orderIDFromContext(ctx)
	if orderID != "" {
		now := time.Now().UTC()
		pending := PendingUpdate{
			OrderID:         orderID,
			ExpectedVersion: version,
			ScheduledAt:     now,
			TimeoutAt:       now.Add(10 * time.Minute),
		}
		if writeErr := WritePendingUpdate(p.stateDir, pending); writeErr != nil {
			// If we can't persist the marker, fall back to the
			// legacy behavior (immediate ACK) rather than failing
			// the install — the binary is already on disk and the
			// restart is scheduled.
			log.Printf("[orders] WARN: could not write pending-update marker for order %s: %v — falling back to immediate ACK", orderID, writeErr)
			return map[string]interface{}{
				"status":      "update_installed",
				"version":     version,
				"binary_path": binaryPath,
				"sha256":      actualHex,
				"message":     "Daemon binary installed, restart scheduled in 10s (no deferred completion)",
			}, nil
		}
		log.Printf("[orders] Wrote pending-update marker for order %s (expected_version=%s)", orderID, version)
	} else {
		log.Printf("[orders] WARN: no order_id in context — cannot defer completion for update_daemon, falling back to immediate ACK")
	}

	return map[string]interface{}{
		"status":      "update_pending",
		"version":     version,
		"binary_path": binaryPath,
		"sha256":      actualHex,
		"message":     "Daemon binary installed, restart scheduled in 10s, completion deferred until post-restart version verification",
	}, nil
}

// --- Emergency WireGuard Access (customer-approved, time-bounded) ---

const maxEmergencyMinutes = 480 // 8 hours absolute max

func (p *Processor) handleEnableEmergencyAccess(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	durationMin := 120 // default 2 hours
	if d, ok := params["max_duration_minutes"].(float64); ok && d > 0 {
		durationMin = int(d)
	}
	if durationMin > maxEmergencyMinutes {
		durationMin = maxEmergencyMinutes
	}

	approvedBy, _ := params["approved_by"].(string)
	if approvedBy == "" {
		return nil, fmt.Errorf("approved_by is required (client admin who authorized access)")
	}

	log.Printf("[orders] EMERGENCY ACCESS: enabling WireGuard for %d minutes (approved by: %s)", durationMin, approvedBy)

	// Start WireGuard keygen + tunnel
	bashPath := "/run/current-system/sw/bin/bash"
	envPath := "PATH=/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin"

	startCmd := exec.CommandContext(ctx, "systemd-run",
		"--unit=msp-emergency-wg-start", "--wait", "--pipe", "--collect",
		"--property=TimeoutStartSec=30",
		"--setenv="+envPath,
		bashPath, "-c",
		"systemctl start wireguard-keygen && systemctl start wireguard-tunnel")
	if out, err := startCmd.CombinedOutput(); err != nil {
		return nil, fmt.Errorf("failed to start WireGuard: %v\n%s", err, string(out))
	}

	// Set auto-disable timer — THIS IS THE TECHNICAL ENFORCEMENT.
	// The timer runs independently of the daemon and WILL fire even if
	// the daemon crashes, is restarted, or the operator forgets to disable.
	timerDuration := fmt.Sprintf("%dm", durationMin)
	timerCmd := exec.CommandContext(ctx, "systemd-run",
		"--unit=msp-emergency-wg-expire", "--timer-property=AccuracySec=1s",
		fmt.Sprintf("--on-active=%s", timerDuration),
		"--collect",
		"--setenv="+envPath,
		bashPath, "-c",
		"systemctl stop wireguard-tunnel; ip link del wg0 2>/dev/null; echo 'Emergency access expired'")
	if out, err := timerCmd.CombinedOutput(); err != nil {
		// If timer fails to set, IMMEDIATELY disable — fail-secure
		log.Printf("[orders] SECURITY: failed to set auto-disable timer — disabling WireGuard immediately: %v", err)
		exec.CommandContext(ctx, "systemctl", "stop", "wireguard-tunnel").Run()
		return nil, fmt.Errorf("failed to set auto-disable timer (fail-secure: tunnel stopped): %v\n%s", err, string(out))
	}

	log.Printf("[orders] EMERGENCY ACCESS ACTIVE: WireGuard up, auto-expires in %d minutes", durationMin)

	return map[string]interface{}{
		"status":              "emergency_access_enabled",
		"duration_minutes":    durationMin,
		"approved_by":         approvedBy,
		"auto_expire":         true,
		"message":             fmt.Sprintf("WireGuard tunnel active. Auto-disables in %d minutes.", durationMin),
	}, nil
}

func (p *Processor) handleDisableEmergencyAccess(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	log.Printf("[orders] EMERGENCY ACCESS: disabling WireGuard tunnel")

	bashPath := "/run/current-system/sw/bin/bash"
	envPath := "PATH=/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin"

	stopCmd := exec.CommandContext(ctx, "systemd-run",
		"--unit=msp-emergency-wg-stop", "--wait", "--pipe", "--collect",
		"--property=TimeoutStartSec=15",
		"--setenv="+envPath,
		bashPath, "-c",
		"systemctl stop wireguard-tunnel; ip link del wg0 2>/dev/null; systemctl stop msp-emergency-wg-expire.timer 2>/dev/null")
	out, err := stopCmd.CombinedOutput()
	if err != nil {
		log.Printf("[orders] WireGuard stop warning: %v\n%s", err, string(out))
	}

	log.Printf("[orders] EMERGENCY ACCESS DISABLED: WireGuard tunnel down")

	return map[string]interface{}{
		"status":  "emergency_access_disabled",
		"message": "WireGuard tunnel stopped and auto-expire timer cancelled.",
	}, nil
}

// --- Order idempotency ---

const executedOrderMaxAge = 1 * time.Hour

// alreadyExecuted checks if an order was executed within the last hour.
func (p *Processor) alreadyExecuted(orderID string) bool {
	p.executedMu.Lock()
	defer p.executedMu.Unlock()

	ts, exists := p.executedOrders[orderID]
	if !exists {
		return false
	}
	return time.Since(ts) < executedOrderMaxAge
}

// recordExecuted marks an order as executed and cleans up stale entries.
func (p *Processor) recordExecuted(orderID string) {
	p.executedMu.Lock()
	defer p.executedMu.Unlock()

	p.executedOrders[orderID] = time.Now()

	// Evict entries older than 1 hour
	cutoff := time.Now().Add(-executedOrderMaxAge)
	for id, ts := range p.executedOrders {
		if ts.Before(cutoff) {
			delete(p.executedOrders, id)
		}
	}
}

// --- Nonce replay protection ---

const nonceMaxAge = 2 * time.Hour // SECURITY: reduced from 24h to shrink replay attack window

// nonceStore is the on-disk format for persisted nonces.
type nonceStore struct {
	Nonces map[string]time.Time `json:"nonces"`
}

// checkAndRecordNonce rejects replayed nonces and records new ones.
func (p *Processor) checkAndRecordNonce(nonce string) error {
	p.nonceMu.Lock()
	defer p.nonceMu.Unlock()

	if _, exists := p.usedNonces[nonce]; exists {
		return fmt.Errorf("nonce %q already used", nonce)
	}

	p.usedNonces[nonce] = time.Now()

	// Evict expired nonces periodically (every time we record)
	p.evictExpiredNoncesLocked()

	// Persist to disk
	p.persistNoncesLocked()

	return nil
}

// removeNonce removes a nonce from the cache, allowing the order to be retried.
// Called when an order fails due to download/execution errors (not security issues).
func (p *Processor) removeNonce(nonce string) {
	if nonce == "" {
		return
	}
	p.nonceMu.Lock()
	defer p.nonceMu.Unlock()
	delete(p.usedNonces, nonce)
	p.persistNoncesLocked()
}

// evictExpiredNoncesLocked removes nonces older than 24h. Must hold nonceMu.
func (p *Processor) evictExpiredNoncesLocked() {
	cutoff := time.Now().Add(-nonceMaxAge)
	for nonce, ts := range p.usedNonces {
		if ts.Before(cutoff) {
			delete(p.usedNonces, nonce)
		}
	}
}

// persistNoncesLocked writes the nonce map to disk. Must hold nonceMu.
func (p *Processor) persistNoncesLocked() {
	path := filepath.Join(p.stateDir, "used_nonces.json")
	store := nonceStore{Nonces: p.usedNonces}
	data, err := json.Marshal(store)
	if err != nil {
		log.Printf("[orders] Failed to marshal nonces: %v", err)
		return
	}
	if err := os.WriteFile(path, data, 0o600); err != nil {
		log.Printf("[orders] Failed to persist nonces to %s: %v", path, err)
	}
}

// loadNonces reads persisted nonces from disk on startup.
func (p *Processor) loadNonces() {
	path := filepath.Join(p.stateDir, "used_nonces.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return // File doesn't exist yet — first boot
	}

	var store nonceStore
	if err := json.Unmarshal(data, &store); err != nil {
		log.Printf("[orders] Failed to parse nonces from %s: %v", path, err)
		return
	}

	// Load and evict stale entries
	cutoff := time.Now().Add(-nonceMaxAge)
	loaded := 0
	for nonce, ts := range store.Nonces {
		if ts.After(cutoff) {
			p.usedNonces[nonce] = ts
			loaded++
		}
	}
	if loaded > 0 {
		log.Printf("[orders] Loaded %d nonces from disk (evicted %d expired)", loaded, len(store.Nonces)-loaded)
	}
}

// healingFlagPath returns the path to the persistent healing-enabled flag file.
func (p *Processor) healingFlagPath() string {
	return filepath.Join(p.stateDir, "healing_enabled")
}

// IsHealingEnabled checks the persistent flag. Defaults to true if file doesn't exist.
func (p *Processor) IsHealingEnabled() bool {
	data, err := os.ReadFile(p.healingFlagPath())
	if err != nil {
		return true // Default: healing enabled
	}
	return strings.TrimSpace(string(data)) != "false"
}

// handleDisableHealing persists healing=disabled and logs it.
func (p *Processor) handleDisableHealing(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	if err := os.WriteFile(p.healingFlagPath(), []byte("false"), 0o644); err != nil {
		return nil, fmt.Errorf("failed to write healing flag: %w", err)
	}
	reason, _ := params["reason"].(string)
	log.Printf("[orders] Healing DISABLED by Central Command (reason: %s)", reason)
	return map[string]interface{}{
		"healing_enabled": false,
		"reason":          reason,
	}, nil
}

// handleEnableHealing persists healing=enabled and logs it.
func (p *Processor) handleEnableHealing(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	if err := os.WriteFile(p.healingFlagPath(), []byte("true"), 0o644); err != nil {
		return nil, fmt.Errorf("failed to write healing flag: %w", err)
	}
	reason, _ := params["reason"].(string)
	log.Printf("[orders] Healing ENABLED by Central Command (reason: %s)", reason)
	return map[string]interface{}{
		"healing_enabled": true,
		"reason":          reason,
	}, nil
}

// handleRotateWgKey generates a new WireGuard keypair, replaces the existing
// key files, and restarts the WireGuard tunnel. The new public key will be
// sent to Central Command on the next checkin.
func (p *Processor) handleRotateWgKey(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	wgDir := filepath.Join(p.stateDir, "wireguard")

	// Read old public key for the result (best-effort)
	oldPub, _ := os.ReadFile(filepath.Join(wgDir, "public.key"))

	// Generate new private key
	genKey := exec.CommandContext(ctx, "wg", "genkey")
	privKey, err := genKey.Output()
	if err != nil {
		return nil, fmt.Errorf("generate WireGuard key: %w", err)
	}
	privKey = bytes.TrimSpace(privKey)

	// Write new private key (0600 permissions — root only)
	if err := os.WriteFile(filepath.Join(wgDir, "private.key"), privKey, 0o600); err != nil {
		return nil, fmt.Errorf("write WireGuard private key: %w", err)
	}

	// Derive public key from the new private key
	pubCmd := exec.CommandContext(ctx, "wg", "pubkey")
	pubCmd.Stdin = bytes.NewReader(privKey)
	newPub, err := pubCmd.Output()
	if err != nil {
		return nil, fmt.Errorf("derive WireGuard public key: %w", err)
	}
	newPub = bytes.TrimSpace(newPub)

	if err := os.WriteFile(filepath.Join(wgDir, "public.key"), newPub, 0o600); err != nil {
		return nil, fmt.Errorf("write WireGuard public key: %w", err)
	}

	// Restart the WireGuard tunnel to pick up the new key
	restartCmd := exec.CommandContext(ctx, "systemctl", "restart", "wireguard-tunnel")
	if restartErr := restartCmd.Run(); restartErr != nil {
		log.Printf("[orders] WireGuard tunnel restart failed (key was rotated): %v", restartErr)
	}

	log.Printf("[orders] WireGuard key rotated successfully")
	return map[string]interface{}{
		"status":     "rotated",
		"old_pubkey": strings.TrimSpace(string(oldPub)),
		"new_pubkey": strings.TrimSpace(string(newPub)),
	}, nil
}

// handleIsolateHost implements ransomware containment via fleet order.
// This is a DANGEROUS operation — it disables network adapters on the target
// host (except WireGuard management tunnel) to prevent lateral movement.
//
// Steps:
//  1. Create VSS snapshot for forensic preservation
//  2. Disable all network adapters except WireGuard tunnel
//  3. Log all actions for incident report
//
// This handler requires explicit human approval via fleet order dispatch.
// It is NEVER triggered automatically — the threat detector creates incidents
// but isolation requires operator authorization.
func (p *Processor) handleIsolateHost(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	hostname, _ := params["hostname"].(string)
	if hostname == "" {
		return nil, fmt.Errorf("isolate_host requires 'hostname' parameter")
	}

	reason, _ := params["reason"].(string)
	if reason == "" {
		reason = "active_threat_containment"
	}

	log.Printf("[orders] ISOLATE_HOST initiated for %s (reason: %s) — THIS IS A CONTAINMENT ACTION", hostname, reason)

	results := map[string]interface{}{
		"hostname": hostname,
		"reason":   reason,
		"actions":  []map[string]interface{}{},
	}
	actions := []map[string]interface{}{}

	// Step 1: Create VSS snapshot for forensic preservation
	vssScript := `
$ErrorActionPreference = 'SilentlyContinue'
try {
    $shadow = (vssadmin create shadow /for=C: 2>&1)
    $id = ($shadow | Select-String 'Shadow Copy ID: \{(.+)\}').Matches[0].Groups[1].Value
    @{ status = "created"; shadow_id = $id } | ConvertTo-Json -Compress
} catch {
    @{ status = "failed"; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`
	// Note: WinRM execution is handled by the daemon via RegisterHandler override.
	// This stub records the intended action. The real WinRM call happens when
	// the daemon replaces this handler with one that has WinRM access.
	actions = append(actions, map[string]interface{}{
		"step":   "vss_snapshot",
		"script": vssScript,
		"status": "pending",
	})

	// Step 2: Disable network adapters except WireGuard
	isolateScript := `
$ErrorActionPreference = 'SilentlyContinue'
$disabled = @()
$skipped = @()
Get-NetAdapter | ForEach-Object {
    if ($_.Name -like "*WireGuard*" -or $_.Name -like "*wg*") {
        $skipped += $_.Name
    } else {
        try {
            Disable-NetAdapter -Name $_.Name -Confirm:$false
            $disabled += $_.Name
        } catch {
            # Log but continue
        }
    }
}
@{ disabled = $disabled; skipped = $skipped; count = $disabled.Count } | ConvertTo-Json -Compress
`
	actions = append(actions, map[string]interface{}{
		"step":   "network_isolation",
		"script": isolateScript,
		"status": "pending",
		"note":   "Disables all adapters except WireGuard management tunnel",
	})

	// Step 3: Log the isolation event
	actions = append(actions, map[string]interface{}{
		"step":      "audit_log",
		"timestamp": time.Now().UTC().Format(time.RFC3339),
		"message":   fmt.Sprintf("Host %s isolated for containment (reason: %s)", hostname, reason),
		"status":    "recorded",
	})

	results["actions"] = actions
	results["status"] = "isolation_initiated"

	// Write isolation record to state dir for tracking
	isolationRecord := map[string]interface{}{
		"hostname":   hostname,
		"reason":     reason,
		"timestamp":  time.Now().UTC().Format(time.RFC3339),
		"actions":    actions,
	}
	recordBytes, _ := json.Marshal(isolationRecord)
	recordPath := filepath.Join(p.stateDir, "isolation_"+hostname+".json")
	if err := os.WriteFile(recordPath, recordBytes, 0o600); err != nil {
		log.Printf("[orders] Failed to write isolation record: %v", err)
	}

	log.Printf("[orders] ISOLATE_HOST for %s: %d actions queued", hostname, len(actions))
	return results, nil
}

// copyFile copies src to dst, preserving permissions.
func copyFile(src, dst string) error {
	data, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	info, err := os.Stat(src)
	if err != nil {
		return err
	}
	return os.WriteFile(dst, data, info.Mode())
}
