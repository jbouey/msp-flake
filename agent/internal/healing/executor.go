//go:build windows

package healing

import (
	"context"
	"fmt"
	"log"
	"os/exec"
	"strings"
	"time"

	pb "github.com/osiriscare/agent/proto"
)

// Execute runs a HealCommand and returns the result.
func Execute(ctx context.Context, cmd *pb.HealCommand) *Result {
	ts := cmd.TimeoutSeconds
	if ts <= 0 || ts > 600 {
		ts = 60
	}
	timeout := time.Duration(ts) * time.Second

	execCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	log.Printf("[heal] Executing: %s/%s (id=%s, timeout=%v)",
		cmd.CheckType, cmd.Action, cmd.CommandId, timeout)

	var res *Result
	switch cmd.CheckType {
	case "firewall":
		res = healFirewall(execCtx, cmd)
	case "defender":
		res = healDefender(execCtx, cmd)
	case "screenlock":
		res = healScreenLock(execCtx, cmd)
	case "bitlocker":
		res = healBitLocker(execCtx, cmd)
	default:
		res = &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("unsupported check_type: %s", cmd.CheckType),
		}
	}

	if res.Success {
		log.Printf("[heal] SUCCESS: %s/%s (id=%s)", cmd.CheckType, cmd.Action, cmd.CommandId)
	} else {
		log.Printf("[heal] FAILED: %s/%s (id=%s): %s", cmd.CheckType, cmd.Action, cmd.CommandId, res.Error)
	}
	return res
}

// runPS executes a PowerShell command and returns stdout+stderr.
// Uses -ErrorAction Stop so PowerShell script errors propagate as non-zero exit.
func runPS(ctx context.Context, script string) (string, error) {
	wrappedScript := "$ErrorActionPreference='Stop'; " + script
	cmd := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", wrappedScript)
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

func healFirewall(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True`
	out, err := runPS(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("powershell error: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
	}
}

func healDefender(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `Set-MpPreference -DisableRealtimeMonitoring $false`
	out, err := runPS(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("powershell error: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
	}
}

func healScreenLock(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `
$path = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
if (!(Test-Path $path)) { New-Item -Path $path -Force | Out-Null }
Set-ItemProperty -Path $path -Name 'InactivityTimeoutSecs' -Value 900 -Type DWord
`
	out, err := runPS(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("powershell error: %v — %s", err, out),
		}
	}
	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
	}
}

func healBitLocker(ctx context.Context, cmd *pb.HealCommand) *Result {
	enableScript := `
$vol = Get-BitLockerVolume -MountPoint 'C:' -ErrorAction SilentlyContinue
if ($null -eq $vol) {
    Enable-BitLocker -MountPoint 'C:' -EncryptionMethod XtsAes256 -TpmProtector -SkipHardwareTest
}
$vol = Get-BitLockerVolume -MountPoint 'C:'
$rp = $vol.KeyProtector | Where-Object { $_.KeyProtectorType -eq 'RecoveryPassword' }
if ($null -eq $rp) {
    $rp = Add-BitLockerKeyProtector -MountPoint 'C:' -RecoveryPasswordProtector
    $rp = (Get-BitLockerVolume -MountPoint 'C:').KeyProtector | Where-Object { $_.KeyProtectorType -eq 'RecoveryPassword' }
}
Write-Output "RECOVERY_KEY=$($rp.RecoveryPassword)"
Write-Output "PROTECTOR_ID=$($rp.KeyProtectorId)"
`
	out, err := runPS(ctx, enableScript)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("powershell error: %v — %s", err, out),
		}
	}

	artifacts := make(map[string]string)
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "RECOVERY_KEY=") {
			artifacts["recovery_key"] = strings.TrimPrefix(line, "RECOVERY_KEY=")
		}
		if strings.HasPrefix(line, "PROTECTOR_ID=") {
			artifacts["protector_id"] = strings.TrimPrefix(line, "PROTECTOR_ID=")
		}
	}

	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
		Artifacts: artifacts,
	}
}
