//go:build windows

package healing

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	pb "github.com/osiriscare/agent/proto"
)

// bitlockerKeyDir is the secure local directory for recovery keys.
// Overridable in tests.
var bitlockerKeyDir = filepath.Join(os.Getenv("PROGRAMDATA"), "OsirisCare", "recovery-keys")

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
	case "winrm":
		res = healWinRM(execCtx, cmd)
	case "dns_service":
		res = healDNS(execCtx, cmd)
	case "windows_update":
		res = healWindowsUpdate(execCtx, cmd)
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

func healWinRM(ctx context.Context, cmd *pb.HealCommand) *Result {
	// Enable-PSRemoting sets WinRM to Auto, starts it, and configures listeners.
	// We also explicitly set the service to Auto and start it as a fallback.
	// Additionally restore Basic auth via GPO policy registry keys — the appliance
	// daemon uses Basic auth over HTTP, and GPO can override local winrm set commands.
	script := `
Set-Service WinRM -StartupType Automatic -ErrorAction SilentlyContinue
Start-Service WinRM -ErrorAction SilentlyContinue
Enable-PSRemoting -Force -SkipNetworkProfileCheck

# Ensure Basic auth is enabled at the GPO policy level (local winrm set is overridden by GPO)
$authPath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WinRM\Service\Auth'
$svcPath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WinRM\Service'
if (!(Test-Path $authPath)) { New-Item -Path $authPath -Force | Out-Null }
Set-ItemProperty -Path $authPath -Name 'AllowBasic' -Value 1 -Type DWord -Force
if (!(Test-Path $svcPath)) { New-Item -Path $svcPath -Force | Out-Null }
Set-ItemProperty -Path $svcPath -Name 'AllowUnencryptedTraffic' -Value 1 -Type DWord -Force

Restart-Service WinRM -Force -ErrorAction SilentlyContinue

$svc = Get-Service WinRM
$basicAuth = (Get-ItemProperty -Path $authPath -Name 'AllowBasic' -ErrorAction SilentlyContinue).AllowBasic
Write-Output "STATE=$($svc.Status)"
Write-Output "STARTTYPE=$($svc.StartType)"
Write-Output "BASIC_AUTH=$basicAuth"
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

	artifacts := make(map[string]string)
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "STATE=") {
			artifacts["service_state"] = strings.TrimPrefix(line, "STATE=")
		}
		if strings.HasPrefix(line, "STARTTYPE=") {
			artifacts["start_type"] = strings.TrimPrefix(line, "STARTTYPE=")
		}
		if strings.HasPrefix(line, "BASIC_AUTH=") {
			artifacts["basic_auth"] = strings.TrimPrefix(line, "BASIC_AUTH=")
		}
	}

	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
		Artifacts: artifacts,
	}
}

func healDNS(ctx context.Context, cmd *pb.HealCommand) *Result {
	// Restart DNS Client. On DCs, also restart DNS Server if it exists.
	script := `
$results = @()
# DNS Client
Set-Service Dnscache -StartupType Automatic -ErrorAction SilentlyContinue
Restart-Service Dnscache -Force -ErrorAction SilentlyContinue
$results += "DNSCACHE=$((Get-Service Dnscache).Status)"

# DNS Server (only on DCs)
$dnsSvc = Get-Service DNS -ErrorAction SilentlyContinue
if ($dnsSvc) {
    Set-Service DNS -StartupType Automatic -ErrorAction SilentlyContinue
    Start-Service DNS -ErrorAction SilentlyContinue
    $results += "DNSSERVER=$((Get-Service DNS).Status)"
}
$results | ForEach-Object { Write-Output $_ }
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

	artifacts := make(map[string]string)
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "DNSCACHE=") {
			artifacts["dnscache_state"] = strings.TrimPrefix(line, "DNSCACHE=")
		}
		if strings.HasPrefix(line, "DNSSERVER=") {
			artifacts["dns_server_state"] = strings.TrimPrefix(line, "DNSSERVER=")
		}
	}

	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
		Artifacts: artifacts,
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

	var recoveryKey, protectorID string
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "RECOVERY_KEY=") {
			recoveryKey = strings.TrimPrefix(line, "RECOVERY_KEY=")
		}
		if strings.HasPrefix(line, "PROTECTOR_ID=") {
			protectorID = strings.TrimPrefix(line, "PROTECTOR_ID=")
		}
	}

	artifacts := make(map[string]string)
	artifacts["protector_id"] = protectorID

	// Never send the recovery key over gRPC — store it locally with
	// restrictive permissions and record only a redacted placeholder
	// in the artifacts that get transmitted to the appliance.
	if recoveryKey != "" {
		if err := saveBitLockerKey(protectorID, recoveryKey); err != nil {
			log.Printf("[heal] WARNING: failed to save BitLocker recovery key locally: %v", err)
			artifacts["recovery_key"] = "[REDACTED — local save failed]"
		} else {
			log.Printf("[heal] BitLocker recovery key generated and saved locally (protector %s)", protectorID)
			artifacts["recovery_key"] = "[REDACTED — stored locally on endpoint]"
		}
	}

	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
		Artifacts: artifacts,
	}
}

// saveBitLockerKey writes a recovery key to a secure local file (0600).
func saveBitLockerKey(protectorID, key string) error {
	if err := os.MkdirAll(bitlockerKeyDir, 0700); err != nil {
		return fmt.Errorf("create key dir: %w", err)
	}
	// Sanitise protector ID for filename (strip braces)
	safe := strings.NewReplacer("{", "", "}", "", "/", "_", "\\", "_").Replace(protectorID)
	if safe == "" {
		safe = "unknown"
	}
	path := filepath.Join(bitlockerKeyDir, safe+".key")
	content := fmt.Sprintf("Protector: %s\nRecoveryKey: %s\n", protectorID, key)
	return os.WriteFile(path, []byte(content), 0600)
}

// healWindowsUpdate installs pending critical/security updates via Windows Update.
// Uses the COM-based Windows Update Agent API (no external modules required).
// Idempotent: if no updates are pending, reports success with no changes.
func healWindowsUpdate(ctx context.Context, cmd *pb.HealCommand) *Result {
	script := `
try {
    $Session = New-Object -ComObject Microsoft.Update.Session
    $Searcher = $Session.CreateUpdateSearcher()
    $SearchResult = $Searcher.Search("IsInstalled=0 and Type='Software' and IsHidden=0")

    $Critical = @($SearchResult.Updates | Where-Object {
        $_.MsrcSeverity -eq 'Critical' -or $_.MsrcSeverity -eq 'Important'
    })

    if ($Critical.Count -eq 0) {
        Write-Output "NO_UPDATES_PENDING"
        exit 0
    }

    $ToInstall = New-Object -ComObject Microsoft.Update.UpdateColl
    foreach ($u in $Critical) { $ToInstall.Add($u) | Out-Null }

    $Downloader = $Session.CreateUpdateDownloader()
    $Downloader.Updates = $ToInstall
    $Downloader.Download() | Out-Null

    $Installer = $Session.CreateUpdateInstaller()
    $Installer.Updates = $ToInstall
    $Result = $Installer.Install()
    Write-Output "INSTALLED:$($Critical.Count) REBOOT:$($Result.RebootRequired)"
} catch {
    Write-Output "ERROR:$($_.Exception.Message)"
    exit 1
}
`
	out, err := runPS(ctx, script)
	if err != nil {
		return &Result{
			CommandID: cmd.CommandId,
			CheckType: cmd.CheckType,
			Success:   false,
			Error:     fmt.Sprintf("windows update failed: %v — %s", err, out),
		}
	}

	artifacts := map[string]string{"output": out}
	if strings.Contains(out, "REBOOT:True") {
		artifacts["reboot_required"] = "true"
	}

	return &Result{
		CommandID: cmd.CommandId,
		CheckType: cmd.CheckType,
		Success:   true,
		Artifacts: artifacts,
	}
}
