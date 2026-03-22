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

// --- Parameter allowlists for dangerous order types ---

// allowedFlakeRefPattern matches only our official flake refs.
// Format: github:jbouey/msp-flake#<output-name>
var allowedFlakeRefPattern = regexp.MustCompile(`^github:jbouey/msp-flake#[a-zA-Z0-9_-]+$`)

// allowedDownloadDomains are the only domains from which we accept package/ISO URLs.
var allowedDownloadDomains = map[string]bool{
	"github.com":                    true,
	"objects.githubusercontent.com": true,
	"178.156.162.116":               true, // VPS IP
	"api.osiriscare.net":            true, // VPS domain
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
type Processor struct {
	handlers    map[string]HandlerFunc
	onComplete  CompletionCallback
	stateDir    string
	verifier    *crypto.OrderVerifier
	applianceID string // This appliance's ID (from checkin response)

	// Nonce replay protection: tracks used nonces to prevent replay attacks
	nonceMu    sync.Mutex
	usedNonces map[string]time.Time // nonce → first-seen timestamp

	// Order idempotency: tracks recently executed order IDs to skip duplicates
	executedMu     sync.Mutex
	executedOrders map[string]time.Time // order_id → execution timestamp
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

	return p
}

// RegisterHandler adds or replaces a handler for an order type.
// This allows subsystems (healing engine, drift checker, etc.) to inject their handlers.
func (p *Processor) RegisterHandler(orderType string, handler HandlerFunc) {
	p.handlers[orderType] = handler
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

	result, err := handler(ctx, params)
	if err != nil {
		log.Printf("[orders] Order %s failed: %v", order.OrderID, err)
		// Clear nonce on execution failure so the order can be retried
		// after the backend auto-expires the failed completion (1 hour).
		// Security failures (bad signature, bad nonce) are caught above
		// and keep the nonce cached to prevent replay attacks.
		p.removeNonce(order.Nonce)
		p.complete(ctx, order.OrderID, false, nil, err.Error())
		return &OrderResult{OrderID: order.OrderID, Success: false, Error: err.Error()}
	}

	log.Printf("[orders] Order %s completed successfully", order.OrderID)
	p.recordExecuted(order.OrderID)
	p.complete(ctx, order.OrderID, true, result, "")
	return &OrderResult{OrderID: order.OrderID, Success: true, Result: result}
}

// verifySignature checks the Ed25519 signature on an order, then verifies
// host scoping (target_appliance_id in the signed payload must match this appliance).
// Returns nil if the signature is valid or if verification is not yet configured
// (graceful degradation during rollout — logs a warning for unsigned orders).
func (p *Processor) verifySignature(order *Order) error {
	if !p.verifier.HasKey() {
		// No server public key yet (first checkin hasn't completed).
		// Allow orders through but log a warning.
		if order.Signature != "" {
			log.Printf("[orders] WARNING: order %s has signature but no server public key to verify", order.OrderID)
		}
		return nil
	}

	if order.Signature == "" || order.SignedPayload == "" {
		log.Printf("[orders] SECURITY: rejected unsigned order %s (type=%s) — server must sign all orders",
			order.OrderID, order.OrderType)
		return fmt.Errorf("unsigned order rejected: order %s has no signature", order.OrderID)
	}

	// Step 1: Verify Ed25519 cryptographic signature
	if err := p.verifier.VerifyOrder(order.SignedPayload, order.Signature); err != nil {
		return err
	}

	// Step 2: Verify host scoping — reject orders targeted at a different appliance.
	// The target_appliance_id is embedded in the signed payload (tamper-proof).
	if err := p.verifyHostScope(order); err != nil {
		return err
	}

	return nil
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

	// Validate package URL against domain allowlist
	if err := validateDownloadURL(packageURL, "package_url"); err != nil {
		return nil, fmt.Errorf("SECURITY: %w", err)
	}
	if version == "" {
		version = "unknown"
	}

	return map[string]interface{}{
		"status":  "update_received",
		"version": version,
		"message": "Agent update will be applied",
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

func (p *Processor) handleDeploySensor(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	hostname, _ := params["hostname"].(string)
	if hostname == "" {
		return nil, fmt.Errorf("hostname is required")
	}

	// Sensor deployment is handled by the WinRM executor with deploy script
	return map[string]interface{}{
		"status":   "deploy_triggered",
		"hostname": hostname,
	}, nil
}

func (p *Processor) handleRemoveSensor(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	hostname, _ := params["hostname"].(string)
	if hostname == "" {
		return nil, fmt.Errorf("hostname is required")
	}

	return map[string]interface{}{
		"status":   "remove_triggered",
		"hostname": hostname,
	}, nil
}

func (p *Processor) handleDeployLinuxSensor(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	hostname, _ := params["hostname"].(string)
	if hostname == "" {
		return nil, fmt.Errorf("hostname is required")
	}

	return map[string]interface{}{
		"status":   "deploy_triggered",
		"hostname": hostname,
	}, nil
}

func (p *Processor) handleRemoveLinuxSensor(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	hostname, _ := params["hostname"].(string)
	if hostname == "" {
		return nil, fmt.Errorf("hostname is required")
	}

	return map[string]interface{}{
		"status":   "remove_triggered",
		"hostname": hostname,
	}, nil
}

func (p *Processor) handleSensorStatus(_ context.Context, _ map[string]interface{}) (map[string]interface{}, error) {
	// Sensor status is gathered from the registry
	return map[string]interface{}{
		"status":              "collected",
		"total_active_sensors": 0,
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
	if _, err := os.Stat(rulePath); err == nil {
		return map[string]interface{}{
			"status":  "already_exists",
			"rule_id": ruleID,
		}, nil
	}

	if err := os.WriteFile(rulePath, []byte(ruleYAML), 0o600); err != nil {
		return nil, fmt.Errorf("write promoted rule: %w", err)
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

	// Write to state dir
	binaryPath := filepath.Join(p.stateDir, "appliance-daemon")
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

	log.Printf("[orders] Scheduling daemon restart in 10 seconds (version=%s)", version)
	go func() {
		time.Sleep(10 * time.Second)
		// Use systemd-run to escape the ProtectSystem=strict sandbox.
		// NixOS: systemctl may not be in the daemon's PATH; systemd-run
		// sets an explicit PATH so the restart command can find it.
		cmd := exec.CommandContext(context.Background(), "systemd-run",
			"--unit=msp-daemon-restart-"+unitSuffix, "--collect",
			"--property=TimeoutStartSec=30",
			"--setenv=PATH=/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin",
			"/run/current-system/sw/bin/bash", "-c",
			"systemctl restart appliance-daemon")
		if out, err := cmd.CombinedOutput(); err != nil {
			log.Printf("[orders] Daemon restart failed: %v\n%s", err, string(out))
		}
	}()

	return map[string]interface{}{
		"status":      "update_installed",
		"version":     version,
		"binary_path": binaryPath,
		"sha256":      actualHex,
		"message":     "Daemon binary installed, restart scheduled in 10s",
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

const nonceMaxAge = 24 * time.Hour

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
