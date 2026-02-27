package daemon

// healing_executor.go — Wires L1 healing rules to WinRM/SSH executors.
//
// The L1 engine matches drift events to rules and produces an action+params.
// This file provides the ActionExecutor callback that dispatches those actions
// to the appropriate executor (WinRM for Windows, SSH for Linux).
//
// Runbook scripts are loaded from runbooks.json (embedded at compile time via
// go:embed in runbooks_embed.go). This file contains 92 runbooks exported from
// the Python agent's runbook library.

import (
	"context"
	"fmt"
	"log"
	"os/exec"
	"time"

	"github.com/osiriscare/appliance/internal/healing"
	"github.com/osiriscare/appliance/internal/sshexec"
	"github.com/osiriscare/appliance/internal/winrm"
)

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
			reason, _ := params["reason"].(string)
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
		default:
			return nil, fmt.Errorf("unknown action: %s", action)
		}
	}
}

// executeRunbook runs a remediation runbook via WinRM (windows) or SSH (linux).
func (d *Daemon) executeRunbook(params map[string]interface{}, hostID, platform string) (map[string]interface{}, error) {
	runbookID, _ := params["runbook_id"].(string)
	if runbookID == "" {
		return nil, fmt.Errorf("runbook_id required")
	}

	phases, _ := params["phases"].([]interface{})
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

	for _, phase := range phases {
		phaseStr, _ := phase.(string)
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
			result := d.winrmExec.Execute(target, script, runbookID, phaseStr, timeout, 1, 15.0, rb.HIPAAControls)
			if !result.Success {
				return map[string]interface{}{
					"success": false, "phase": phaseStr, "error": result.Error,
				}, fmt.Errorf("%s phase %s failed: %s", runbookID, phaseStr, result.Error)
			}
			results[phaseStr] = result.Output

		case "linux":
			// For self-healing on this appliance, execute locally instead of SSH
			if d.isSelfHost(hostID) {
				result := d.executeLocal(script, runbookID, phaseStr, timeout)
				if !result.Success {
					return map[string]interface{}{
						"success": false, "phase": phaseStr, "error": result.Error,
					}, fmt.Errorf("%s phase %s failed: %s", runbookID, phaseStr, result.Error)
				}
				results[phaseStr] = result.Output
			} else {
				target := d.buildHealingSSHTarget(hostID)
				if target == nil {
					return nil, fmt.Errorf("no SSH credentials for host %s", hostID)
				}
				result := d.sshExec.Execute(context.Background(), target, script, runbookID, phaseStr, timeout, 1, 5.0, true, rb.HIPAAControls)
				if !result.Success {
					return map[string]interface{}{
						"success": false, "phase": phaseStr, "error": result.Error,
					}, fmt.Errorf("%s phase %s failed: %s", runbookID, phaseStr, result.Error)
				}
				results[phaseStr] = result.Output
			}

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
	return &winrm.Target{
		Hostname: hostID,
		Port:     5985,
		Username: *d.config.DCUsername,
		Password: *d.config.DCPassword,
		UseSSL:   false,
	}
}

// buildHealingSSHTarget creates an SSH target for Linux healing.
func (d *Daemon) buildHealingSSHTarget(hostID string) *sshexec.Target {
	return &sshexec.Target{
		Hostname: hostID,
		Port:     22,
		Username: "root",
	}
}

// isSelfHost returns true if the hostID refers to this appliance.
func (d *Daemon) isSelfHost(hostID string) bool {
	applianceHostname := d.config.SiteID + "-appliance"
	return hostID == applianceHostname || hostID == "localhost" || hostID == "127.0.0.1"
}

// localExecResult mirrors the SSH executor result for local execution.
type localExecResult struct {
	Success bool
	Output  string
	Error   string
}

// executeLocal runs a remediation script locally via bash instead of SSH.
// Used for self-healing on the appliance itself.
func (d *Daemon) executeLocal(script, runbookID, phase string, timeout int) localExecResult {
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
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

// executeHealingOrder processes a healing order from Central Command.
// Unlike drift-triggered healing (which goes through the L1 engine), healing
// orders arrive pre-matched with a runbook_id. We look up the runbook,
// determine the platform, and dispatch to the appropriate executor.
func (d *Daemon) executeHealingOrder(_ context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	runbookID, _ := params["runbook_id"].(string)
	if runbookID == "" {
		return nil, fmt.Errorf("runbook_id is required")
	}

	hostname, _ := params["hostname"].(string)
	checkType, _ := params["check_type"].(string)

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

	log.Printf("[healing-order] Executing %s on %s (platform=%s check_type=%s)", runbookID, hostname, platform, checkType)

	rbParams := map[string]interface{}{
		"runbook_id": runbookID,
		"phases":     []interface{}{"remediate", "verify"},
	}

	result, err := d.executeRunbook(rbParams, hostname, platform)
	if err != nil {
		log.Printf("[healing-order] %s on %s FAILED: %v", runbookID, hostname, err)
		return map[string]interface{}{
			"status":     "failed",
			"runbook_id": runbookID,
			"hostname":   hostname,
			"error":      err.Error(),
		}, err
	}

	log.Printf("[healing-order] %s on %s SUCCEEDED", runbookID, hostname)
	result["status"] = "healed"
	result["runbook_id"] = runbookID
	result["hostname"] = hostname
	return result, nil
}
