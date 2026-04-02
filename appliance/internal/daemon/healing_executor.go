package daemon

// healing_executor.go — Wires L1 healing rules to WinRM/SSH executors.
//
// The L1 engine matches drift events to rules and produces an action+params.
// This file provides the ActionExecutor callback that dispatches those actions
// to the appropriate executor (WinRM for Windows, SSH for Linux).
//
// Runbook scripts are loaded from runbooks.json (embedded at compile time
// via go:embed in runbooks_embed.go). This file contains 92 runbooks exported from
// the Python agent's runbook library.

import (
	"context"
	"fmt"
	"log"
	"net"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/osiriscare/appliance/internal/healing"
	"github.com/osiriscare/appliance/internal/maputil"
	"github.com/osiriscare/appliance/internal/sshexec"
	"github.com/osiriscare/appliance/internal/winrm"
)

// classifyHealError returns a structured error category from a raw error string.
// Dashboard uses these to show "30% auth failures, 20% timeouts" etc.
func classifyHealError(errMsg string) string {
	if errMsg == "" {
		return ""
	}
	lower := strings.ToLower(errMsg)
	switch {
	case strings.Contains(lower, "401") ||
		strings.Contains(lower, "unauthorized") ||
		strings.Contains(lower, "access denied") ||
		strings.Contains(lower, "permission denied") ||
		strings.Contains(lower, "invalid content type"): // WinRM NTLM auth mismatch
		return "auth_failure"
	case strings.Contains(lower, "timeout") ||
		strings.Contains(lower, "deadline exceeded") ||
		strings.Contains(lower, "timed out"):
		return "timeout"
	case strings.Contains(lower, "connection refused") ||
		strings.Contains(lower, "no route") ||
		strings.Contains(lower, "unreachable") ||
		strings.Contains(lower, "i/o timeout"):
		return "network_error"
	case strings.Contains(lower, "exit code") ||
		strings.Contains(lower, "non-zero") ||
		strings.Contains(lower, "failed:"):
		return "script_error"
	case strings.Contains(lower, "not found") ||
		strings.Contains(lower, "no such") ||
		strings.Contains(lower, "missing"):
		return "not_found"
	default:
		return "unknown"
	}
}

// runbookEntry is a single runbook loaded from the embedded JSON.
type runbookEntry struct {
	ID               string   `json:"id"`
	Name             string   `json:"name"`
	Platform         string   `json:"platform"`
	DetectScript     string   `json:"detect_script"`
	RemediateScript  string   `json:"remediate_script"`
	VerifyScript     string   `json:"verify_script"`
	HIPAAControls    []string `json:"hipaa_controls"`
	Severity         string   `json:"severity"`
	TimeoutSeconds   int      `json:"timeout_seconds"`
}

// makeActionExecutor returns a healing.ActionExecutor that dispatches actions
// to the daemon's WinRM and SSH executors. The returned function closes over
// the daemon's executor instances and config.
func (d *Daemon) makeActionExecutor() healing.ActionExecutor {
	return func(action string, params map[string]interface{}, siteID, hostID string) (map[string]interface{}, error) {
		switch action {
		case "run_windows_runbook":
			return d.executeRunbook(params, hostID, "windows")
		case "run_linux_runbook":
			return d.executeRunbook(params, hostID, "linux")
		case "escalate":
			reason := maputil.String(params, "reason")
			if reason == "" {
				reason = "Rule action is escalate"
			}
			log.Printf("[l1-exec] Escalating to L3: host=%s reason=%s", hostID, reason)
			return map[string]interface{}{"escalated": true, "reason": reason}, nil
		case "restore_firewall_baseline":
			return d.executeRunbook(map[string]interface{}{
				"runbook_id": "RB-WIN-SEC-001",
				"phases":     []interface{}{"remediate", "verify"},
			}, hostID, "windows")
		case "restart_av_service":
			return d.executeRunbook(map[string]interface{}{
				"runbook_id": "RB-WIN-SEC-006",
				"phases":     []interface{}{"remediate", "verify"},
			}, hostID, "windows")
		case "update_to_baseline_generation":
			// Trigger GPO baseline update on the DC via WinRM
			script := `gpupdate /force; (Get-GPO -All | Where-Object { $_.ModificationTime -gt (Get-Date).AddDays(-1) }).DisplayName`
			return d.executeInlineScript(script, hostID, "windows", params)
		case "run_backup_job":
			// Trigger restic backup on the Linux appliance
			jobName := maputil.StringDefault(params, "job_name", "restic-backup")
			script := fmt.Sprintf("systemctl start %s.service && systemctl is-active %s.service", jobName, jobName)
			return d.executeInlineScript(script, hostID, "linux", params)
		case "restart_logging_services":
			// Restart logging services (journald on NixOS, rsyslog on others)
			script := "systemctl restart systemd-journald && journalctl --verify --quiet 2>&1 | tail -5"
			return d.executeInlineScript(script, hostID, "linux", params)
		case "renew_certificate":
			// Attempt ACME certificate renewal
			script := "if command -v certbot >/dev/null 2>&1; then certbot renew --non-interactive; elif command -v acme.sh >/dev/null 2>&1; then acme.sh --renew-all; else echo 'No ACME client found'; exit 1; fi"
			return d.executeInlineScript(script, hostID, "linux", params)
		case "cleanup_disk_space":
			// Clean up temp files, old logs, journal vacuum (keep 7d of nix generations for rollback)
			script := "journalctl --vacuum-size=100M 2>/dev/null; find /tmp -type f -atime +7 -delete 2>/dev/null; find /var/log -name '*.gz' -mtime +30 -delete 2>/dev/null; nix-collect-garbage --delete-older-than 7d 2>/dev/null; df -h / | tail -1"
			return d.executeInlineScript(script, hostID, "linux", params)
		default:
			return nil, fmt.Errorf("unknown action: %s", action)
		}
	}
}

const (
	l1HealingTimeout    = 5 * time.Minute  // Max time for L1 deterministic healing
	orderHealingTimeout = 10 * time.Minute // Max time for healing orders from Central Command
)

// executeRunbook runs a remediation runbook via WinRM (windows) or SSH (linux).
// Uses the daemon's run context with an L1 timeout so healing operations
// cancel on shutdown or if they exceed the time boundary.
func (d *Daemon) executeRunbook(params map[string]interface{}, hostID, platform string) (map[string]interface{}, error) {
	ctx, cancel := context.WithTimeout(d.runCtx, l1HealingTimeout)
	defer cancel()
	return d.executeRunbookCtx(ctx, params, hostID, platform)
}

func (d *Daemon) executeRunbookCtx(ctx context.Context, params map[string]interface{}, hostID, platform string) (map[string]interface{}, error) {
	runbookID := maputil.String(params, "runbook_id")
	if runbookID == "" {
		return nil, fmt.Errorf("runbook_id required")
	}

	phases := maputil.Slice(params, "phases")
	if len(phases) == 0 {
		phases = []interface{}{"remediate", "verify"}
	}

	// Look up the runbook from the embedded registry
	rb, ok := runbookRegistry[runbookID]
	if !ok {
		return nil, fmt.Errorf("unknown runbook: %s (registry has %d entries)", runbookID, len(runbookRegistry))
	}

	// Get the script for each phase
	phaseScripts := map[string]string{
		"detect":    rb.DetectScript,
		"remediate": rb.RemediateScript,
		"verify":    rb.VerifyScript,
	}

	timeout := rb.TimeoutSeconds
	if timeout <= 0 {
		timeout = 120
	}

	results := map[string]interface{}{}

	// Journal ID for phase tracking — use runbookID:hostID as composite key
	journalID := fmt.Sprintf("%s:%s", runbookID, hostID)

	for _, phase := range phases {
		phaseStr, ok := phase.(string)
		if !ok {
			log.Printf("[l1-exec] skipping non-string phase: %T", phase)
			continue
		}
		script := phaseScripts[phaseStr]
		if script == "" {
			continue
		}

		log.Printf("[l1-exec] %s %s phase=%s on %s", platform, runbookID, phaseStr, hostID)

		switch platform {
		case "windows":
			target := d.buildHealingWinRMTarget(hostID)
			if target == nil {
				return nil, fmt.Errorf("no WinRM credentials for host %s", hostID)
			}
			result := d.winrmExec.ExecuteCtx(ctx, target, script, runbookID, phaseStr, timeout, 1, 15.0, rb.HIPAAControls)
			if !result.Success {
				return map[string]interface{}{
					"success": false, "phase": phaseStr, "error": result.Error,
				}, fmt.Errorf("%s phase %s failed: %s", runbookID, phaseStr, result.Error)
			}
			d.healJournal.CompletePhase(journalID, phaseStr)
			results[phaseStr] = result.Output

		case "linux":
			// For self-healing on this appliance, execute locally instead of SSH
			if d.isSelfHost(hostID) {
				result := d.executeLocalCtx(ctx, script, runbookID, phaseStr, timeout)
				if !result.Success {
					return map[string]interface{}{
						"success": false, "phase": phaseStr, "error": result.Error,
					}, fmt.Errorf("%s phase %s failed: %s", runbookID, phaseStr, result.Error)
				}
				d.healJournal.CompletePhase(journalID, phaseStr)
				results[phaseStr] = result.Output
			} else {
				target := d.buildHealingSSHTarget(hostID)
				if target == nil {
					return nil, fmt.Errorf("no SSH credentials for host %s", hostID)
				}
				result := d.sshExec.Execute(ctx, target, script, runbookID, phaseStr, timeout, 1, 5.0, true, rb.HIPAAControls)
				if !result.Success {
					return map[string]interface{}{
						"success": false, "phase": phaseStr, "error": result.Error,
					}, fmt.Errorf("%s phase %s failed: %s", runbookID, phaseStr, result.Error)
				}
				d.healJournal.CompletePhase(journalID, phaseStr)
				results[phaseStr] = result.Output
			}

		case "macos":
			// macOS uses SSH execution (same as remote linux targets)
			target := d.buildHealingSSHTarget(hostID)
			if target == nil {
				return nil, fmt.Errorf("no SSH credentials for macOS host %s", hostID)
			}
			result := d.sshExec.Execute(ctx, target, script, runbookID, phaseStr, timeout, 1, 5.0, true, rb.HIPAAControls)
			if !result.Success {
				return map[string]interface{}{
					"success": false, "phase": phaseStr, "error": result.Error,
				}, fmt.Errorf("%s phase %s failed: %s", runbookID, phaseStr, result.Error)
			}
			d.healJournal.CompletePhase(journalID, phaseStr)
			results[phaseStr] = result.Output

		default:
			return nil, fmt.Errorf("unknown platform: %s", platform)
		}
	}

	results["success"] = true
	return results, nil
}

// buildHealingWinRMTarget creates a WinRM target using the daemon's DC credentials.
// For L1 healing, we use the domain admin credentials since all drift targets
// are Windows domain members scanned via the same DC creds.
func (d *Daemon) buildHealingWinRMTarget(hostID string) *winrm.Target {
	if d.config.DCUsername == nil || d.config.DCPassword == nil {
		return nil
	}
	ws := d.probeWinRM(hostID)
	return &winrm.Target{
		Hostname:  hostID,
		Port:      ws.Port,
		Username:  *d.config.DCUsername,
		Password:  *d.config.DCPassword,
		UseSSL:    ws.UseSSL,
		VerifySSL: true, // TOFU cert pinning via CertPinStore
	}
}

// buildHealingSSHTarget creates an SSH target for Linux/macOS healing.
// Looks up credentials from linuxTargets first, falls back to root@22.
func (d *Daemon) buildHealingSSHTarget(hostID string) *sshexec.Target {
	targets := d.state.GetLinuxTargets()

	// Helper to build an SSH target from a linuxTarget credential entry.
	buildFromLT := func(lt linuxTarget) *sshexec.Target {
		port := lt.Port
		if port == 0 {
			port = 22
		}
		user := lt.Username
		if user == "" {
			user = "root"
		}
		t := &sshexec.Target{
			Hostname: lt.Hostname,
			Port:     port,
			Username: user,
		}
		if lt.Password != "" {
			pw := lt.Password
			t.Password = &pw
		}
		if lt.PrivateKey != "" {
			pk := lt.PrivateKey
			t.PrivateKey = &pk
		}
		return t
	}

	// 1. Direct hostname match (credentials keyed by this hostID)
	for _, lt := range targets {
		if lt.Hostname == hostID {
			return buildFromLT(lt)
		}
	}

	// 2. Label match — credentials are often keyed by IP, but the label
	// matches the hostname (e.g., hostID="MaCs-iMac.local", label="MaCs-iMac.local",
	// lt.Hostname="192.168.88.50")
	for _, lt := range targets {
		if strings.EqualFold(lt.Label, hostID) || strings.EqualFold(lt.Hostname, hostID) {
			return buildFromLT(lt)
		}
	}

	// 3. gRPC registry — resolve the hostname to an IP via a connected agent,
	// then look up credentials by that IP
	if d.registry != nil {
		if agent := d.registry.GetAgentByHostname(hostID); agent != nil && agent.IPAddress != "" {
			for _, lt := range targets {
				if lt.Hostname == agent.IPAddress {
					return buildFromLT(lt)
				}
			}
		}
	}

	// Fallback for unknown hosts
	return &sshexec.Target{
		Hostname: hostID,
		Port:     22,
		Username: "root",
	}
}

// isSelfHost returns true if the hostID refers to this appliance.
// Matches site-derived hostname, localhost, loopback, actual hostname,
// and any local IP address — the Linux scan target is typically an IP.
func (d *Daemon) isSelfHost(hostID string) bool {
	applianceHostname := d.config.SiteID + "-appliance"
	if hostID == applianceHostname || hostID == "localhost" || hostID == "127.0.0.1" {
		return true
	}
	if hn, err := os.Hostname(); err == nil && hostID == hn {
		return true
	}
	// Check against local network interfaces — Linux scan targets use IPs
	addrs, err := net.InterfaceAddrs()
	if err != nil {
		return false
	}
	for _, addr := range addrs {
		if ipnet, ok := addr.(*net.IPNet); ok && ipnet.IP.String() == hostID {
			return true
		}
	}
	return false
}

// localExecResult mirrors the SSH executor result for local execution.
type localExecResult struct {
	Success bool
	Output  string
	Error   string
}

// executeLocalCtx runs a remediation script locally via bash instead of SSH.
// Used for self-healing on the appliance itself.
func (d *Daemon) executeLocalCtx(parent context.Context, script, runbookID, phase string, timeout int) localExecResult {
	ctx, cancel := context.WithTimeout(parent, time.Duration(timeout)*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, "bash", "-c", script)
	output, err := cmd.CombinedOutput()
	outStr := string(output)
	if len(outStr) > 2000 {
		outStr = outStr[len(outStr)-2000:]
	}

	if err != nil {
		log.Printf("[l1-exec] Local %s phase=%s failed: %v", runbookID, phase, err)
		return localExecResult{Success: false, Output: outStr, Error: fmt.Sprintf("%v: %s", err, outStr)}
	}

	log.Printf("[l1-exec] Local %s phase=%s succeeded", runbookID, phase)
	return localExecResult{Success: true, Output: outStr}
}

// executeInlineScript runs a script directly (not from runbook registry) via WinRM or SSH.
// Used for L1 actions that don't map to a specific runbook.
func (d *Daemon) executeInlineScript(script, hostID, platform string, params map[string]interface{}) (map[string]interface{}, error) {
	ctx, cancel := context.WithTimeout(d.runCtx, l1HealingTimeout)
	defer cancel()
	return d.executeInlineScriptCtx(ctx, script, hostID, platform, params)
}

func (d *Daemon) executeInlineScriptCtx(ctx context.Context, script, hostID, platform string, params map[string]interface{}) (map[string]interface{}, error) {
	actionName := "inline"
	if a, ok := params["_action"].(string); ok {
		actionName = a
	}

	switch platform {
	case "windows":
		target := d.buildHealingWinRMTarget(hostID)
		if target == nil {
			return nil, fmt.Errorf("no WinRM credentials for host %s", hostID)
		}
		result := d.winrmExec.ExecuteCtx(ctx, target, script, actionName, "remediate", 120, 1, 15.0, nil)
		if !result.Success {
			return map[string]interface{}{"success": false, "error": result.Error}, fmt.Errorf("%s failed: %s", actionName, result.Error)
		}
		return map[string]interface{}{"success": true, "output": result.Output}, nil

	case "linux":
		if d.isSelfHost(hostID) {
			result := d.executeLocalCtx(ctx, script, actionName, "remediate", 120)
			if !result.Success {
				return map[string]interface{}{"success": false, "error": result.Error}, fmt.Errorf("%s failed: %s", actionName, result.Error)
			}
			return map[string]interface{}{"success": true, "output": result.Output}, nil
		}
		target := d.buildHealingSSHTarget(hostID)
		if target == nil {
			return nil, fmt.Errorf("no SSH credentials for host %s", hostID)
		}
		result := d.sshExec.Execute(ctx, target, script, actionName, "remediate", 120, 1, 5.0, true, nil)
		if !result.Success {
			return map[string]interface{}{"success": false, "error": result.Error}, fmt.Errorf("%s failed: %s", actionName, result.Error)
		}
		return map[string]interface{}{"success": true, "output": result.Output}, nil

	default:
		return nil, fmt.Errorf("unknown platform: %s", platform)
	}
}

// isKnownTarget validates that a hostname is a recognized target for this appliance.
// For windows: checks the domain controller and deployed workstations.
// For linux: checks linux targets from checkin and allows localhost/self.
func (d *Daemon) isKnownTarget(hostname, platform string) bool {
	if hostname == "" {
		return false
	}

	// Self-referencing is always allowed for linux
	if d.isSelfHost(hostname) {
		return true
	}

	switch platform {
	case "windows":
		// Domain controller
		if d.config.DomainController != nil && *d.config.DomainController == hostname {
			return true
		}
		// Deployed workstations tracked by auto-deployer
		if d.deployer != nil {
			d.deployer.mu.Lock()
			_, deployed := d.deployer.deployed[hostname]
			d.deployer.mu.Unlock()
			if deployed {
				return true
			}
		}
		// Go agent registry — connected agents are known targets.
		// Critical for healing commands from gRPC push agents (hostname != credential IP).
		if d.registry != nil && d.registry.HasAgentForHost(hostname) {
			return true
		}
		return false

	case "linux", "macos":
		for _, lt := range d.state.GetLinuxTargets() {
			if lt.Hostname == hostname {
				return true
			}
		}
		// Also check gRPC agent registry — connected agents are known targets
		if d.registry != nil && d.registry.HasAgentForHost(hostname) {
			return true
		}
		return false

	default:
		return false
	}
}

// inferPlatformFromCheckType determines the platform from the drift check type name.
func (d *Daemon) inferPlatformFromCheckType(checkType string) string {
	if len(checkType) > 6 && checkType[:6] == "macos_" {
		return "macos"
	}
	if len(checkType) > 6 && checkType[:6] == "linux_" {
		return "linux"
	}
	return "windows"
}

// executeHealingOrder processes a healing order from Central Command.
// Unlike drift-triggered healing (which goes through the L1 engine), healing
// orders arrive pre-matched with a runbook_id. We look up the runbook,
// determine the platform, and dispatch to the appropriate executor.
func (d *Daemon) executeHealingOrder(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	runbookID := maputil.String(params, "runbook_id")
	if runbookID == "" {
		return nil, fmt.Errorf("runbook_id is required")
	}

	hostname := maputil.String(params, "hostname")
	checkType := maputil.String(params, "check_type")

	// Look up runbook to determine platform
	rb, ok := runbookRegistry[runbookID]
	if !ok {
		return nil, fmt.Errorf("unknown runbook %s (registry has %d entries)", runbookID, len(runbookRegistry))
	}

	platform := rb.Platform
	if platform == "" {
		// Infer from runbook ID prefix
		switch {
		case len(runbookID) > 4 && (runbookID[:4] == "RB-W" || runbookID[:4] == "WIN-"):
			platform = "windows"
		case len(runbookID) > 4 && (runbookID[:4] == "RB-L" || runbookID[:4] == "LIN-"):
			platform = "linux"
		case len(runbookID) > 4 && (runbookID[:4] == "MAC-" || runbookID[:8] == "ESC-MAC-"):
			platform = "macos"
		default:
			platform = "windows" // default for HIPAA compliance targets
		}
	}

	// Determine target hostname — fall back to DC for Windows, self for Linux
	if hostname == "" {
		switch platform {
		case "windows":
			if d.config.DomainController != nil {
				hostname = *d.config.DomainController
			} else {
				return nil, fmt.Errorf("no hostname in order and no DC configured")
			}
		case "linux":
			hostname = "localhost"
		}
	}

	// SECURITY: Validate hostname against known targets to prevent
	// execution against arbitrary hosts via crafted orders
	if !d.isKnownTarget(hostname, platform) {
		log.Printf("[healing-order] SECURITY: rejected order %s — hostname %q is not a known %s target",
			runbookID, hostname, platform)
		return nil, fmt.Errorf("SECURITY: hostname %q is not a known %s target for this appliance", hostname, platform)
	}

	log.Printf("[healing-order] Executing %s on %s (platform=%s check_type=%s)", runbookID, hostname, platform, checkType)

	// Journal: checkpoint before execution
	orderID := fmt.Sprintf("order-%s-%s-%d", runbookID, hostname, time.Now().UnixMilli())
	d.healJournal.StartHealing(orderID, runbookID, hostname, platform, checkType, "order")

	// Enforce per-order timeout boundary
	orderCtx, orderCancel := context.WithTimeout(ctx, orderHealingTimeout)
	defer orderCancel()

	rbParams := map[string]interface{}{
		"runbook_id": runbookID,
		"phases":     []interface{}{"remediate", "verify"},
	}

	result, err := d.executeRunbookCtx(orderCtx, rbParams, hostname, platform)
	if err != nil {
		d.healJournal.FinishHealing(orderID, false, err.Error())
		log.Printf("[healing-order] %s on %s FAILED: %v", runbookID, hostname, err)
		return map[string]interface{}{
			"status":     "failed",
			"runbook_id": runbookID,
			"hostname":   hostname,
			"error":      err.Error(),
		}, err
	}

	d.healJournal.FinishHealing(orderID, true, "")
	log.Printf("[healing-order] %s on %s SUCCEEDED", runbookID, hostname)

	// Resolve the incident on the backend (belt-and-suspenders with order completion hook)
	if d.incidents != nil && checkType != "" {
		tier := "L2" // healing orders are typically L2
		if _, ok := params["resolution_tier"]; ok {
			if t, ok2 := params["resolution_tier"].(string); ok2 && t != "" {
				tier = t
			}
		}
		d.safeGo("reportHealedOrder", func() { d.incidents.ReportHealed(hostname, checkType, tier, runbookID) })
	}

	result["status"] = "healed"
	result["runbook_id"] = runbookID
	result["hostname"] = hostname
	return result, nil
}
