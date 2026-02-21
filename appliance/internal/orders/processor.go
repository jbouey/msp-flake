// Package orders processes pending orders from Central Command.
//
// Order flow:
//  1. Fetch pending orders from checkin response
//  2. Acknowledge each order (marks as "executing")
//  3. Dispatch to handler by order_type
//  4. Complete order with result (success/failure)
//
// 17 order types are handled, from simple checkins to NixOS rebuilds.
package orders

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// Order represents a pending order from Central Command.
type Order struct {
	OrderID    string                 `json:"order_id"`
	OrderType  string                 `json:"order_type"`
	Parameters map[string]interface{} `json:"parameters"`
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
	handlers   map[string]HandlerFunc
	onComplete CompletionCallback
	stateDir   string
}

// NewProcessor creates a new order processor.
func NewProcessor(stateDir string, onComplete CompletionCallback) *Processor {
	p := &Processor{
		handlers:   make(map[string]HandlerFunc),
		onComplete: onComplete,
		stateDir:   stateDir,
	}

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

	return p
}

// RegisterHandler adds or replaces a handler for an order type.
// This allows subsystems (healing engine, drift checker, etc.) to inject their handlers.
func (p *Processor) RegisterHandler(orderType string, handler HandlerFunc) {
	p.handlers[orderType] = handler
}

// Process handles a single order: dispatch to handler, report completion.
func (p *Processor) Process(ctx context.Context, order *Order) *OrderResult {
	if order.OrderID == "" || order.OrderType == "" {
		log.Printf("[orders] Skipping order with missing id or type")
		return nil
	}

	log.Printf("[orders] Processing order %s: %s", order.OrderID, order.OrderType)

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
		p.complete(ctx, order.OrderID, false, nil, err.Error())
		return &OrderResult{OrderID: order.OrderID, Success: false, Error: err.Error()}
	}

	log.Printf("[orders] Order %s completed successfully", order.OrderID)
	p.complete(ctx, order.OrderID, true, result, "")
	return &OrderResult{OrderID: order.OrderID, Success: true, Result: result}
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

	go func() {
		time.Sleep(5 * time.Second)
		cmd := exec.Command("systemctl", "restart", "appliance-daemon")
		if err := cmd.Run(); err != nil {
			log.Printf("[orders] Restart failed: %v", err)
		}
	}()

	return map[string]interface{}{"status": "restart_scheduled"}, nil
}

func (p *Processor) handleNixOSRebuild(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	flakeRef, _ := params["flake_ref"].(string)
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
	go func() {
		time.Sleep(10 * time.Second)
		exec.Command("systemctl", "restart", "appliance-daemon").Run()
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

	if packageURL == "" {
		return nil, fmt.Errorf("package_url is required")
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

	if isoURL == "" || version == "" {
		return nil, fmt.Errorf("version and iso_url are required")
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

	cmd := exec.Command("journalctl", "-u", "appliance-daemon", "--no-pager", "-n", fmt.Sprintf("%d", lines))
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

	cmd := exec.Command(args[0], args[1:]...)
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

	if err := os.WriteFile(rulePath, []byte(ruleYAML), 0o644); err != nil {
		return nil, fmt.Errorf("write promoted rule: %w", err)
	}

	return map[string]interface{}{
		"status":  "deployed",
		"rule_id": ruleID,
	}, nil
}

func (p *Processor) handleHealing(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	runbookID, _ := params["runbook_id"].(string)
	if runbookID == "" {
		return nil, fmt.Errorf("runbook_id is required")
	}

	// Healing is routed through the L1 engine or WinRM/SSH executors
	return map[string]interface{}{
		"status":     "healing_triggered",
		"runbook_id": runbookID,
	}, nil
}

func (p *Processor) handleUpdateCredentials(_ context.Context, _ map[string]interface{}) (map[string]interface{}, error) {
	// Credential refresh is handled by the daemon's phone-home client
	return map[string]interface{}{"status": "credential_refresh_triggered"}, nil
}
