package daemon

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/osiriscare/appliance/internal/evidence"
	"github.com/osiriscare/appliance/internal/grpcserver"
	"github.com/osiriscare/appliance/internal/winrm"
)

// flexStringSlice handles PowerShell JSON that serializes single-element arrays
// as bare strings. Accepts both "value" and ["value"] in JSON.
type flexStringSlice []string

func (f *flexStringSlice) UnmarshalJSON(data []byte) error {
	// Try as array first (common case)
	var arr []string
	if err := json.Unmarshal(data, &arr); err == nil {
		*f = arr
		return nil
	}
	// Fall back to bare string
	var s string
	if err := json.Unmarshal(data, &s); err == nil {
		if s != "" {
			*f = []string{s}
		} else {
			*f = nil
		}
		return nil
	}
	// Accept null gracefully
	*f = nil
	return nil
}

const (
	driftScanInterval = 15 * time.Minute // How often to scan all targets

	// Known safe scheduled tasks — everything else is suspicious
	// These are default Windows tasks that should not be flagged.
	safeTaskPrefix = `\Microsoft\Windows\`
)

// driftScanner periodically checks Windows and Linux targets for security drift.
// Windows: firewall disabled, Defender stopped, rogue users, rogue scheduled tasks,
// critical services stopped, BitLocker, SMB signing, etc.
// Linux: firewall rules, SSH hardening, failed services, disk space, SUID, etc.
type driftScanner struct {
	daemon *Daemon

	mu           sync.Mutex
	lastScanTime time.Time
	running      int32 // atomic guard

	linuxMu           sync.Mutex
	lastLinuxScanTime time.Time
	linuxRunning      int32 // atomic guard
}

func newDriftScanner(d *Daemon) *driftScanner {
	return &driftScanner{daemon: d}
}

// ForceScan runs both Windows and Linux drift scans immediately,
// bypassing the interval check. Called from run_drift fleet order handler.
func (ds *driftScanner) ForceScan(ctx context.Context) map[string]interface{} {
	log.Printf("[driftscan] Force scan triggered by fleet order")

	windowsDone := false
	linuxDone := false

	// Run Windows scan if configured
	cfg := ds.daemon.config
	if cfg.WorkstationEnabled && cfg.DomainController != nil && *cfg.DomainController != "" {
		if atomic.CompareAndSwapInt32(&ds.running, 0, 1) {
			ds.mu.Lock()
			ds.lastScanTime = time.Now()
			ds.mu.Unlock()
			ds.scanWindowsTargets(ctx)
			atomic.StoreInt32(&ds.running, 0)
			windowsDone = true
		}
	}

	// Run Linux scan
	if cfg.EnableDriftDetection {
		if atomic.CompareAndSwapInt32(&ds.linuxRunning, 0, 1) {
			ds.linuxMu.Lock()
			ds.lastLinuxScanTime = time.Now()
			ds.linuxMu.Unlock()
			ds.scanLinuxTargets(ctx)
			atomic.StoreInt32(&ds.linuxRunning, 0)
			linuxDone = true
		}
	}

	return map[string]interface{}{
		"status":       "scan_completed",
		"windows_scan": windowsDone,
		"linux_scan":   linuxDone,
	}
}

// runDriftScanIfNeeded runs a full scan if the interval has elapsed.
// Called from the main daemon loop (runCycle).
func (ds *driftScanner) runDriftScanIfNeeded(ctx context.Context) {
	if !atomic.CompareAndSwapInt32(&ds.running, 0, 1) {
		return // Already running
	}
	defer atomic.StoreInt32(&ds.running, 0)

	ds.mu.Lock()
	since := time.Since(ds.lastScanTime)
	first := ds.lastScanTime.IsZero()
	ds.mu.Unlock()

	if !first && since < driftScanInterval {
		return
	}

	log.Printf("[driftscan] Starting drift scan cycle")
	ds.mu.Lock()
	ds.lastScanTime = time.Now()
	ds.mu.Unlock()

	ds.scanWindowsTargets(ctx)
}

// scanWindowsTargets enumerates known Windows targets and checks each for drift.
func (ds *driftScanner) scanWindowsTargets(ctx context.Context) {
	cfg := ds.daemon.config
	if cfg.DomainController == nil || *cfg.DomainController == "" {
		return
	}
	if cfg.DCUsername == nil || cfg.DCPassword == nil {
		return
	}

	// Build target list: DC + any deployed workstations
	targets := []scanTarget{
		{
			hostname: *cfg.DomainController,
			label:    "DC",
			target: &winrm.Target{
				Hostname: *cfg.DomainController,
				Port:     5985,
				Username: *cfg.DCUsername,
				Password: *cfg.DCPassword,
				UseSSL:   false,
			},
		},
	}

	// Add deployed workstations from the autodeploy tracker
	if ds.daemon.deployer != nil {
		ds.daemon.deployer.mu.Lock()
		for hostname := range ds.daemon.deployer.deployed {
			targets = append(targets, scanTarget{
				hostname: hostname,
				label:    "WS",
				target: &winrm.Target{
					Hostname: hostname,
					Port:     5985,
					Username: *cfg.DCUsername,
					Password: *cfg.DCPassword,
					UseSSL:   false,
				},
			})
		}
		ds.daemon.deployer.mu.Unlock()
	}

	var allFindings []driftFinding
	var scannedHosts []string

	for _, t := range targets {
		select {
		case <-ctx.Done():
			return
		default:
		}

		scannedHosts = append(scannedHosts, t.hostname)
		drifts := ds.checkTarget(ctx, t)
		allFindings = append(allFindings, drifts...)
		for _, d := range drifts {
			ds.reportDrift(d)
		}
	}

	log.Printf("[driftscan] Scan complete: targets=%d, drifts_found=%d",
		len(targets), len(allFindings))

	// Submit evidence bundle to Central Command
	if ds.daemon.evidenceSubmitter != nil && len(scannedHosts) > 0 {
		// Convert to evidence package types
		evFindings := make([]evidence.DriftFinding, len(allFindings))
		for i, f := range allFindings {
			evFindings[i] = evidence.DriftFinding{
				Hostname:     f.Hostname,
				CheckType:    f.CheckType,
				Expected:     f.Expected,
				Actual:       f.Actual,
				HIPAAControl: f.HIPAAControl,
				Severity:     f.Severity,
			}
		}
		if err := ds.daemon.evidenceSubmitter.BuildAndSubmit(ctx, evFindings, scannedHosts); err != nil {
			log.Printf("[driftscan] Evidence submission failed: %v", err)
		}
	}
}

type scanTarget struct {
	hostname string
	label    string
	target   *winrm.Target
}

// driftFinding represents a single drift condition found on a target.
type driftFinding struct {
	Hostname     string
	CheckType    string
	Expected     string
	Actual       string
	HIPAAControl string
	Severity     string
	Details      map[string]string
}

// checkTarget runs all drift checks against a single Windows target.
func (ds *driftScanner) checkTarget(ctx context.Context, t scanTarget) []driftFinding {
	// Single comprehensive PowerShell script that checks everything in one WinRM call.
	// This minimizes network round-trips and authentication overhead.
	script := `
$ErrorActionPreference = 'SilentlyContinue'
$result = @{}

# 1. Firewall profiles
$fw = @{}
Get-NetFirewallProfile | ForEach-Object {
    $fw[$_.Name] = $_.Enabled.ToString()
}
$result.Firewall = $fw

# 2. Windows Defender
$wd = Get-Service WinDefend -EA SilentlyContinue
$result.Defender = if ($wd) { $wd.Status.ToString() } else { "NotFound" }

# 3. Windows Update service
$wu = Get-Service wuauserv -EA SilentlyContinue
$result.WindowsUpdate = if ($wu) { $wu.Status.ToString() } else { "NotFound" }

# 4. Windows Event Log service
$el = Get-Service EventLog -EA SilentlyContinue
$result.EventLog = if ($el) { $el.Status.ToString() } else { "NotFound" }

# 5. Rogue local administrators (non-default)
$defaultAdmins = @("Administrator", "Domain Admins", "Enterprise Admins")
$rogueAdmins = @()
try {
    $members = Get-LocalGroupMember -Group "Administrators" -EA Stop
    foreach ($m in $members) {
        $name = $m.Name.Split('\')[-1]
        if ($name -notin $defaultAdmins -and $m.ObjectClass -eq 'User') {
            $rogueAdmins += $name
        }
    }
} catch {}
$result.RogueAdmins = @($rogueAdmins)

# 6. Rogue scheduled tasks (not in Microsoft\Windows\ path)
$rogueTasks = @()
try {
    Get-ScheduledTask -EA Stop | Where-Object {
        $_.TaskPath -notlike '\Microsoft\Windows\*' -and
        $_.TaskPath -ne '\' -or
        ($_.TaskPath -eq '\' -and $_.TaskName -notmatch '^(MicrosoftEdge|GoogleUpdate|OneDrive|User_Feed)')
    } | ForEach-Object {
        if ($_.TaskName -ne 'OsirisCareAgent' -and
            $_.TaskName -notmatch '^(CreateExplorerShellUnelevatedTask|klnagent)') {
            $rogueTasks += @{
                Name = $_.TaskName
                Path = $_.TaskPath
                State = $_.State.ToString()
            }
        }
    }
} catch {}
$result.RogueTasks = $rogueTasks

# 7. OsirisCare agent status (on workstations)
$agent = Get-Service OsirisCareAgent -EA SilentlyContinue
$result.AgentStatus = if ($agent) { $agent.Status.ToString() } else { "NotInstalled" }

# 8. BitLocker status (system drive)
$result.BitLocker = "NotAvailable"
try {
    $bl = Get-BitLockerVolume -MountPoint "C:" -EA Stop
    $result.BitLocker = $bl.ProtectionStatus.ToString()
} catch {}

# 9. SMB signing
$result.SMBSigning = "Unknown"
try {
    $smb = Get-SmbServerConfiguration -EA Stop
    $result.SMBSigning = if ($smb.RequireSecuritySignature) { "Required" } else { "NotRequired" }
} catch {}

# 10. SMB1 protocol
$result.SMB1 = "Unknown"
try {
    $smb1 = Get-SmbServerConfiguration -EA Stop
    $result.SMB1 = if ($smb1.EnableSMB1Protocol) { "Enabled" } else { "Disabled" }
} catch {}

# 11. Screen lock / inactivity timeout (via registry)
$result.ScreenLock = "Unknown"
try {
    $sl = Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -Name "InactivityTimeoutSecs" -EA Stop
    $result.ScreenLock = $sl.InactivityTimeoutSecs.ToString()
} catch {
    $result.ScreenLock = "NotConfigured"
}

# 12. Defender exclusions
$defExclusions = @()
try {
    $prefs = Get-MpPreference -EA Stop
    if ($prefs.ExclusionPath) { $defExclusions += $prefs.ExclusionPath }
    if ($prefs.ExclusionProcess) { $defExclusions += $prefs.ExclusionProcess }
    if ($prefs.ExclusionExtension) { $defExclusions += $prefs.ExclusionExtension }
} catch {}
$result.DefenderExclusions = @($defExclusions)

# 13. DNS configuration (check for hijacking)
$dnsServers = @()
try {
    Get-DnsClientServerAddress -AddressFamily IPv4 -EA Stop | Where-Object { $_.ServerAddresses.Count -gt 0 } | ForEach-Object {
        $dnsServers += $_.ServerAddresses
    }
    $dnsServers = @($dnsServers | Select-Object -Unique)
} catch {}
$result.DNSServers = @($dnsServers)

# 14. Network profile (domain vs public/private)
$netProfiles = @{}
try {
    Get-NetConnectionProfile -EA Stop | ForEach-Object {
        $netProfiles[$_.InterfaceAlias] = $_.NetworkCategory.ToString()
    }
} catch {}
$result.NetworkProfiles = $netProfiles

# 15. Password policy (domain)
$result.PasswordPolicy = @{}
try {
    $pp = net accounts 2>$null
    if ($pp) {
        $minLen = ($pp | Select-String "Minimum password length" | ForEach-Object { ($_ -split ":\s*")[1] }) -replace '\D'
        $maxAge = ($pp | Select-String "Maximum password age" | ForEach-Object { ($_ -split ":\s*")[1] }) -replace '\D'
        $lockout = ($pp | Select-String "Lockout threshold" | ForEach-Object { ($_ -split ":\s*")[1] }) -replace '\D'
        $result.PasswordPolicy = @{
            MinLength = if ($minLen) { [int]$minLen } else { 0 }
            MaxAgeDays = if ($maxAge) { [int]$maxAge } else { 0 }
            LockoutThreshold = if ($lockout) { [int]$lockout } else { 0 }
        }
    }
} catch {}

# 16. RDP Network Level Authentication
$result.RDPNLA = "Unknown"
try {
    $rdp = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" -Name "UserAuthentication" -EA Stop
    $result.RDPNLA = if ($rdp.UserAuthentication -eq 1) { "Enabled" } else { "Disabled" }
} catch {
    $result.RDPNLA = "NotConfigured"
}

# 17. Guest account
$result.GuestAccount = "Unknown"
try {
    $guest = Get-LocalUser -Name "Guest" -EA Stop
    $result.GuestAccount = if ($guest.Enabled) { "Enabled" } else { "Disabled" }
} catch {
    $result.GuestAccount = "NotFound"
}

# 18. Critical AD services (DC only)
$adServices = @{}
foreach ($svc in @("DNS","Netlogon","NTDS")) {
    $s = Get-Service $svc -EA SilentlyContinue
    if ($s) { $adServices[$svc] = $s.Status.ToString() }
}
$result.ADServices = $adServices

$result | ConvertTo-Json -Depth 3 -Compress
`

	scanResult := ds.daemon.winrmExec.Execute(t.target, script, "DRIFT-SCAN", "driftscan", 30, 0, 15.0, nil)
	if !scanResult.Success {
		// Try via DC proxy for workstations
		if t.label == "WS" {
			return ds.checkTargetViaDCProxy(ctx, t)
		}
		log.Printf("[driftscan] Scan failed for %s (%s): %s", t.hostname, t.label, scanResult.Error)
		return nil
	}

	stdout, _ := scanResult.Output["std_out"].(string)
	if stdout == "" {
		return nil
	}

	var state windowsScanState
	if err := json.Unmarshal([]byte(stdout), &state); err != nil {
		log.Printf("[driftscan] Parse error for %s: %v", t.hostname, err)
		return nil
	}

	return ds.evaluateWindowsFindings(state, t)
}

// windowsScanState is the parsed output of the Windows drift scan PowerShell script.
type windowsScanState struct {
	Firewall      map[string]string `json:"Firewall"`
	Defender      string            `json:"Defender"`
	WindowsUpdate string            `json:"WindowsUpdate"`
	EventLog      string            `json:"EventLog"`
	RogueAdmins   flexStringSlice   `json:"RogueAdmins"`
	RogueTasks    []struct {
		Name  string `json:"Name"`
		Path  string `json:"Path"`
		State string `json:"State"`
	} `json:"RogueTasks"`
	AgentStatus        string            `json:"AgentStatus"`
	BitLocker          string            `json:"BitLocker"`
	SMBSigning         string            `json:"SMBSigning"`
	SMB1               string            `json:"SMB1"`
	ScreenLock         string            `json:"ScreenLock"`
	DefenderExclusions flexStringSlice   `json:"DefenderExclusions"`
	DNSServers         flexStringSlice   `json:"DNSServers"`
	NetworkProfiles    map[string]string `json:"NetworkProfiles"`
	PasswordPolicy     struct {
		MinLength        int `json:"MinLength"`
		MaxAgeDays       int `json:"MaxAgeDays"`
		LockoutThreshold int `json:"LockoutThreshold"`
	} `json:"PasswordPolicy"`
	RDPNLA       string            `json:"RDPNLA"`
	GuestAccount string            `json:"GuestAccount"`
	ADServices   map[string]string `json:"ADServices"`
}

// evaluateWindowsFindings converts a parsed Windows scan state into drift findings.
func (ds *driftScanner) evaluateWindowsFindings(state windowsScanState, t scanTarget) []driftFinding {
	var findings []driftFinding

	// 1. Firewall
	for profile, enabled := range state.Firewall {
		if strings.ToLower(enabled) == "false" {
			findings = append(findings, driftFinding{
				Hostname:     t.hostname,
				CheckType:    "firewall_status",
				Expected:     "True",
				Actual:       "False",
				HIPAAControl: "164.312(a)(1)",
				Severity:     "high",
				Details:      map[string]string{"profile": profile},
			})
		}
	}

	// 2. Windows Defender
	if state.Defender != "" && state.Defender != "Running" && state.Defender != "NotFound" {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "windows_defender",
			Expected:     "Running",
			Actual:       state.Defender,
			HIPAAControl: "164.308(a)(5)(ii)(B)",
			Severity:     "high",
		})
	}

	// 3. Windows Update
	if state.WindowsUpdate == "Stopped" {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "windows_update",
			Expected:     "Running",
			Actual:       "Stopped",
			HIPAAControl: "164.308(a)(5)(ii)(A)",
			Severity:     "medium",
		})
	}

	// 4. Audit logging (Event Log service)
	if state.EventLog == "Stopped" {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "audit_logging",
			Expected:     "Running",
			Actual:       "Stopped",
			HIPAAControl: "164.312(b)",
			Severity:     "critical",
		})
	}

	// 5. Rogue admins
	if len(state.RogueAdmins) > 0 {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "rogue_admin_users",
			Expected:     "none",
			Actual:       strings.Join(state.RogueAdmins, ", "),
			HIPAAControl: "164.312(a)(1)",
			Severity:     "critical",
			Details:      map[string]string{"users": strings.Join(state.RogueAdmins, ",")},
		})
	}

	// 6. Rogue scheduled tasks
	if len(state.RogueTasks) > 0 {
		taskNames := make([]string, 0, len(state.RogueTasks))
		for _, rt := range state.RogueTasks {
			taskNames = append(taskNames, rt.Name)
		}
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "rogue_scheduled_tasks",
			Expected:     "none",
			Actual:       strings.Join(taskNames, ", "),
			HIPAAControl: "164.308(a)(1)(ii)(D)",
			Severity:     "high",
			Details:      map[string]string{"tasks": strings.Join(taskNames, ",")},
		})
	}

	// 7. Agent not running on workstation
	if t.label == "WS" && state.AgentStatus != "Running" {
		findings = append(findings, driftFinding{
			Hostname:  t.hostname,
			CheckType: "agent_status",
			Expected:  "Running",
			Actual:    state.AgentStatus,
			Severity:  "medium",
		})
	}

	// 8. BitLocker — must be "On" for HIPAA encryption at rest
	if state.BitLocker != "NotAvailable" && state.BitLocker != "On" && state.BitLocker != "1" {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "bitlocker_status",
			Expected:     "On",
			Actual:       state.BitLocker,
			HIPAAControl: "164.312(a)(2)(iv)",
			Severity:     "critical",
		})
	}

	// 9. SMB signing — must be required
	if state.SMBSigning == "NotRequired" {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "smb_signing",
			Expected:     "Required",
			Actual:       "NotRequired",
			HIPAAControl: "164.312(e)(2)(ii)",
			Severity:     "high",
		})
	}

	// 10. SMB1 — must be disabled (legacy, insecure)
	if state.SMB1 == "Enabled" {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "smb1_protocol",
			Expected:     "Disabled",
			Actual:       "Enabled",
			HIPAAControl: "164.312(e)(1)",
			Severity:     "high",
		})
	}

	// 11. Screen lock — must be configured (inactivity timeout)
	if state.ScreenLock == "NotConfigured" {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "screen_lock_policy",
			Expected:     "Configured",
			Actual:       "NotConfigured",
			HIPAAControl: "164.312(a)(2)(iii)",
			Severity:     "medium",
		})
	}

	// 12. Defender exclusions — flag if any exist (review needed)
	if len(state.DefenderExclusions) > 0 {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "defender_exclusions",
			Expected:     "none",
			Actual:       fmt.Sprintf("%d exclusions", len(state.DefenderExclusions)),
			HIPAAControl: "164.308(a)(5)(ii)(B)",
			Severity:     "medium",
			Details:      map[string]string{"exclusions": strings.Join(state.DefenderExclusions, ",")},
		})
	}

	// 13. DNS configuration — flag suspicious DNS servers
	if len(state.DNSServers) > 0 {
		knownDNS := map[string]bool{
			"127.0.0.1": true, "8.8.8.8": true, "8.8.4.4": true,
			"1.1.1.1": true, "1.0.0.1": true, "9.9.9.9": true,
		}
		// Also allow the DC IP as DNS
		cfg := ds.daemon.config
		if cfg.DomainController != nil {
			knownDNS[*cfg.DomainController] = true
		}
		suspiciousDNS := []string{}
		for _, dns := range state.DNSServers {
			if !knownDNS[dns] && !strings.HasPrefix(dns, "192.168.") && !strings.HasPrefix(dns, "10.") && !strings.HasPrefix(dns, "172.") {
				suspiciousDNS = append(suspiciousDNS, dns)
			}
		}
		if len(suspiciousDNS) > 0 {
			findings = append(findings, driftFinding{
				Hostname:     t.hostname,
				CheckType:    "dns_config",
				Expected:     "known_dns_only",
				Actual:       strings.Join(suspiciousDNS, ", "),
				HIPAAControl: "164.312(e)(1)",
				Severity:     "critical",
				Details:      map[string]string{"suspicious": strings.Join(suspiciousDNS, ",")},
			})
		}
	}

	// 14. Network profile — workstations on domain should show "DomainAuthenticated"
	if t.label == "WS" {
		for iface, profile := range state.NetworkProfiles {
			if profile == "Public" {
				findings = append(findings, driftFinding{
					Hostname:     t.hostname,
					CheckType:    "network_profile",
					Expected:     "DomainAuthenticated",
					Actual:       "Public",
					HIPAAControl: "164.312(e)(1)",
					Severity:     "medium",
					Details:      map[string]string{"interface": iface},
				})
				break // one finding per host is enough
			}
		}
	}

	// 15. Password policy — minimum requirements
	if state.PasswordPolicy.MinLength > 0 && state.PasswordPolicy.MinLength < 8 {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "password_policy",
			Expected:     "min_length>=8",
			Actual:       fmt.Sprintf("min_length=%d", state.PasswordPolicy.MinLength),
			HIPAAControl: "164.312(d)",
			Severity:     "high",
		})
	}
	if state.PasswordPolicy.LockoutThreshold == 0 {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "password_policy",
			Expected:     "lockout_threshold>0",
			Actual:       "lockout_threshold=0 (no lockout)",
			HIPAAControl: "164.312(d)",
			Severity:     "high",
		})
	}

	// 16. RDP NLA
	if state.RDPNLA == "Disabled" {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "rdp_nla",
			Expected:     "Enabled",
			Actual:       "Disabled",
			HIPAAControl: "164.312(d)",
			Severity:     "high",
		})
	}

	// 17. Guest account
	if state.GuestAccount == "Enabled" {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "guest_account",
			Expected:     "Disabled",
			Actual:       "Enabled",
			HIPAAControl: "164.312(a)(1)",
			Severity:     "high",
		})
	}

	// 18. AD Services (DC only)
	if t.label == "DC" {
		for svc, status := range state.ADServices {
			if status != "Running" {
				checkType := "service_dns"
				if svc == "Netlogon" {
					checkType = "service_netlogon"
				}
				findings = append(findings, driftFinding{
					Hostname:     t.hostname,
					CheckType:    checkType,
					Expected:     "Running",
					Actual:       status,
					HIPAAControl: "164.312(a)(1)",
					Severity:     "critical",
					Details:      map[string]string{"service": svc},
				})
			}
		}
	}

	if len(findings) > 0 {
		log.Printf("[driftscan] %s (%s): %d drift findings", t.hostname, t.label, len(findings))
	}

	return findings
}

// checkTargetViaDCProxy runs the drift scan on a workstation via the DC using Invoke-Command.
func (ds *driftScanner) checkTargetViaDCProxy(ctx context.Context, t scanTarget) []driftFinding {
	cfg := ds.daemon.config
	if cfg.DomainController == nil || *cfg.DomainController == "" {
		return nil
	}

	dcTarget := &winrm.Target{
		Hostname: *cfg.DomainController,
		Port:     5985,
		Username: *cfg.DCUsername,
		Password: *cfg.DCPassword,
		UseSSL:   false,
	}

	// Run the same scan via Invoke-Command on the DC
	proxyScript := fmt.Sprintf(`
$secPass = ConvertTo-SecureString "%s" -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential("%s", $secPass)

$sessions = @()
$sessions += New-PSSession -ComputerName "%s" -Credential $cred -ErrorAction SilentlyContinue
if (-not $sessions -or $sessions.Count -eq 0) {
    $sessions += New-PSSession -ComputerName "%s" -ErrorAction SilentlyContinue
}
if (-not $sessions -or $sessions.Count -eq 0) {
    @{ Error = "Cannot establish session" } | ConvertTo-Json -Compress
    return
}

$session = $sessions[0]

$result = Invoke-Command -Session $session -ScriptBlock {
    $ErrorActionPreference = 'SilentlyContinue'
    $r = @{}
    $fw = @{}
    Get-NetFirewallProfile | ForEach-Object { $fw[$_.Name] = $_.Enabled.ToString() }
    $r.Firewall = $fw
    $wd = Get-Service WinDefend -EA SilentlyContinue
    $r.Defender = if ($wd) { $wd.Status.ToString() } else { "NotFound" }
    $wu = Get-Service wuauserv -EA SilentlyContinue
    $r.WindowsUpdate = if ($wu) { $wu.Status.ToString() } else { "NotFound" }
    $agent = Get-Service OsirisCareAgent -EA SilentlyContinue
    $r.AgentStatus = if ($agent) { $agent.Status.ToString() } else { "NotInstalled" }
    $rogueAdmins = @()
    try {
        $members = Get-LocalGroupMember -Group "Administrators" -EA Stop
        foreach ($m in $members) {
            $name = $m.Name.Split('\')[-1]
            if ($name -notin @("Administrator","Domain Admins","Enterprise Admins") -and $m.ObjectClass -eq 'User') {
                $rogueAdmins += $name
            }
        }
    } catch {}
    $r.RogueAdmins = @($rogueAdmins)
    $r.RogueTasks = @()
    try {
        Get-ScheduledTask -EA Stop | Where-Object {
            $_.TaskPath -notlike '\Microsoft\Windows\*' -and $_.TaskName -ne 'OsirisCareAgent'
        } | ForEach-Object { $r.RogueTasks += @{Name=$_.TaskName;Path=$_.TaskPath;State=$_.State.ToString()} }
    } catch {}
    $r
} -ErrorAction Stop

Remove-PSSession $session -EA SilentlyContinue
$result | ConvertTo-Json -Depth 3 -Compress
`,
		escapePSString(*cfg.DCPassword),
		escapePSString(*cfg.DCUsername),
		escapePSString(t.hostname),
		escapePSString(t.hostname),
	)

	scanResult := ds.daemon.winrmExec.Execute(dcTarget, proxyScript, "DRIFT-SCAN-PROXY", "driftscan", 60, 0, 30.0, nil)
	if !scanResult.Success {
		log.Printf("[driftscan] DC proxy scan failed for %s: %s", t.hostname, scanResult.Error)
		return nil
	}

	stdout, _ := scanResult.Output["std_out"].(string)
	if stdout == "" {
		return nil
	}

	// Parse using same struct as direct scan (proxy returns subset of fields)
	var proxyState struct {
		windowsScanState
		Error string `json:"Error"`
	}
	if err := json.Unmarshal([]byte(stdout), &proxyState); err != nil {
		log.Printf("[driftscan] DC proxy parse error for %s: %v (raw: %.200s)", t.hostname, err, stdout)
		return nil
	}
	if proxyState.Error != "" {
		log.Printf("[driftscan] DC proxy session error for %s: %s", t.hostname, proxyState.Error)
		return nil
	}

	return ds.evaluateWindowsFindings(proxyState.windowsScanState, t)
}

// runLinuxScanIfNeeded runs a Linux scan if the interval has elapsed.
func (ds *driftScanner) runLinuxScanIfNeeded(ctx context.Context) {
	if !atomic.CompareAndSwapInt32(&ds.linuxRunning, 0, 1) {
		return
	}
	defer atomic.StoreInt32(&ds.linuxRunning, 0)

	ds.linuxMu.Lock()
	since := time.Since(ds.lastLinuxScanTime)
	first := ds.lastLinuxScanTime.IsZero()
	ds.linuxMu.Unlock()

	if !first && since < driftScanInterval {
		return
	}

	log.Printf("[linuxscan] Starting Linux drift scan cycle")
	ds.linuxMu.Lock()
	ds.lastLinuxScanTime = time.Now()
	ds.linuxMu.Unlock()

	ds.scanLinuxTargets(ctx)
}

// reportDrift sends a drift finding through the L1→L2→L3 healing pipeline.
func (ds *driftScanner) reportDrift(f driftFinding) {
	metadata := map[string]string{
		"platform": "windows",
		"source":   "driftscan",
	}
	for k, v := range f.Details {
		metadata[k] = v
	}

	// If the daemon has DC credentials, include them for healing
	if ds.daemon.config.DCUsername != nil {
		metadata["winrm_username"] = *ds.daemon.config.DCUsername
	}
	if ds.daemon.config.DCPassword != nil {
		metadata["winrm_password"] = *ds.daemon.config.DCPassword
	}

	req := grpcserver.HealRequest{
		Hostname:     f.Hostname,
		CheckType:    f.CheckType,
		Expected:     f.Expected,
		Actual:       f.Actual,
		HIPAAControl: f.HIPAAControl,
		AgentID:      "driftscan",
		Metadata:     metadata,
	}

	log.Printf("[driftscan] DRIFT: %s/%s expected=%s actual=%s hipaa=%s",
		f.Hostname, f.CheckType, f.Expected, f.Actual, f.HIPAAControl)

	// Route through the healing pipeline
	ds.daemon.healIncident(context.Background(), req)
}
