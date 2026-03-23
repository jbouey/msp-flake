package daemon

import (
	"context"
	"fmt"
	"log"
	"strconv"
	"strings"

	"github.com/osiriscare/appliance/internal/sshexec"
)

// PreFlightResult contains the results of pre-deployment checks
type PreFlightResult struct {
	Passed   bool
	Warnings []string
	Blockers []string
}

// runPreFlightChecks validates a target host is ready for agent deployment
func runPreFlightChecks(ctx context.Context, d *Daemon, deploy PendingDeploy) PreFlightResult {
	result := PreFlightResult{Passed: true}

	// Check 1: Reachability
	probe := probeHost(ctx, deploy.IPAddress)
	if deploy.DeployMethod == "ssh" && !probe.SSHOpen {
		result.Passed = false
		result.Blockers = append(result.Blockers, "SSH port 22 not reachable")
		return result // no point checking further
	}
	if deploy.DeployMethod == "winrm" && !probe.WinRMOpen {
		result.Passed = false
		result.Blockers = append(result.Blockers, "WinRM port 5985 not reachable")
		return result
	}

	// For SSH targets, run remote checks
	if deploy.DeployMethod == "ssh" && probe.SSHOpen {
		checkDiskSpace(ctx, d, deploy, &result)
		checkOSVersion(ctx, d, deploy, probe, &result)
		checkExistingSoftware(ctx, d, deploy, &result)
	}

	return result
}

// checkDiskSpace verifies at least 50MB free on the target
func checkDiskSpace(ctx context.Context, d *Daemon, deploy PendingDeploy, result *PreFlightResult) {
	target := buildSSHTarget(deploy)
	// df outputs in 1K blocks — check /opt (linux) or / (macos)
	mountPoint := "/opt"
	if deploy.OSType == "macos" {
		mountPoint = "/"
	}
	cmd := fmt.Sprintf("df -k %s | tail -1 | awk '{print $4}'", mountPoint)
	res := d.sshExec.Execute(ctx, target, cmd, "preflight", "disk-check", 15, 0, 0, false, nil)
	if !res.Success {
		log.Printf("[preflight] Disk check failed for %s: %s", deploy.Hostname, res.Error)
		return // non-fatal, skip this check
	}

	stdout, _ := res.Output["stdout"].(string)
	freeKB, err := strconv.ParseInt(strings.TrimSpace(stdout), 10, 64)
	if err != nil {
		return
	}

	freeMB := freeKB / 1024
	if freeMB < 50 {
		result.Passed = false
		result.Blockers = append(result.Blockers, fmt.Sprintf("Insufficient disk space: %dMB free (need 50MB)", freeMB))
	}
}

// checkOSVersion verifies the OS meets minimum requirements
func checkOSVersion(ctx context.Context, d *Daemon, deploy PendingDeploy, probe ProbeResult, result *PreFlightResult) {
	target := buildSSHTarget(deploy)

	switch deploy.OSType {
	case "macos":
		// macOS 10.15+ required for Go 1.22
		res := d.sshExec.Execute(ctx, target, "sw_vers -productVersion", "preflight", "os-version", 15, 0, 0, false, nil)
		if !res.Success {
			return
		}
		stdout, _ := res.Output["stdout"].(string)
		version := strings.TrimSpace(stdout)
		parts := strings.Split(version, ".")
		if len(parts) >= 2 {
			major, _ := strconv.Atoi(parts[0])
			minor, _ := strconv.Atoi(parts[1])
			if major < 10 || (major == 10 && minor < 15) {
				result.Passed = false
				result.Blockers = append(result.Blockers, fmt.Sprintf("macOS %s too old (need 10.15+)", version))
			}
		}
	case "linux":
		// Ubuntu 18.04+ / similar vintage required
		// Check kernel version as a proxy (4.15+ = Ubuntu 18.04 era)
		res := d.sshExec.Execute(ctx, target, "uname -r", "preflight", "os-version", 15, 0, 0, false, nil)
		if !res.Success {
			return
		}
		stdout, _ := res.Output["stdout"].(string)
		kernel := strings.TrimSpace(stdout)
		parts := strings.Split(kernel, ".")
		if len(parts) >= 2 {
			major, _ := strconv.Atoi(parts[0])
			if major < 4 {
				result.Passed = false
				result.Blockers = append(result.Blockers, fmt.Sprintf("Kernel %s too old (need 4.x+)", kernel))
			}
		}
	}
}

// checkExistingSoftware detects security software and RMM agents
func checkExistingSoftware(ctx context.Context, d *Daemon, deploy PendingDeploy, result *PreFlightResult) {
	target := buildSSHTarget(deploy)

	// Check for existing security software (warning, not blocker)
	securityProcs := []string{"falcon-sensor", "SentinelAgent", "ds_agent", "cbagentd"}
	for _, proc := range securityProcs {
		cmd := fmt.Sprintf("pgrep -x %s 2>/dev/null && echo FOUND || echo NOTFOUND", proc)
		res := d.sshExec.Execute(ctx, target, cmd, "preflight", "security-check", 10, 0, 0, false, nil)
		if !res.Success {
			continue
		}
		stdout, _ := res.Output["stdout"].(string)
		if strings.Contains(stdout, "FOUND") {
			result.Warnings = append(result.Warnings, fmt.Sprintf("Security software detected: %s (may interfere with install)", proc))
		}
	}

	// Check for existing RMM agents (warning, not blocker)
	rmmProcs := []string{"connectwise", "datto", "ninjarmm", "automate"}
	for _, proc := range rmmProcs {
		cmd := fmt.Sprintf("pgrep -f %s 2>/dev/null && echo FOUND || echo NOTFOUND", proc)
		res := d.sshExec.Execute(ctx, target, cmd, "preflight", "rmm-check", 10, 0, 0, false, nil)
		if !res.Success {
			continue
		}
		stdout, _ := res.Output["stdout"].(string)
		if strings.Contains(stdout, "FOUND") {
			result.Warnings = append(result.Warnings, fmt.Sprintf("Existing RMM agent detected: %s", proc))
		}
	}
}

// buildSSHTarget creates an sshexec.Target from a PendingDeploy
func buildSSHTarget(deploy PendingDeploy) *sshexec.Target {
	target := &sshexec.Target{
		Hostname:       deploy.IPAddress,
		Port:           22,
		Username:       deploy.Username,
		ConnectTimeout: 15,
		CommandTimeout: 30,
	}
	if deploy.Password != "" {
		password := deploy.Password
		target.Password = &password
	}
	if deploy.SSHKey != "" {
		key := deploy.SSHKey
		target.PrivateKey = &key
	}
	return target
}
