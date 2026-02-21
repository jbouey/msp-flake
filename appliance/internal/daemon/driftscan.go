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

const (
	driftScanInterval = 15 * time.Minute // How often to scan all targets

	// Known safe scheduled tasks — everything else is suspicious
	// These are default Windows tasks that should not be flagged.
	safeTaskPrefix = `\Microsoft\Windows\`
)

// driftScanner periodically checks Windows targets for security drift.
// Detects: firewall disabled, Defender stopped, rogue users, rogue scheduled tasks,
// critical services stopped.
type driftScanner struct {
	daemon *Daemon

	mu           sync.Mutex
	lastScanTime time.Time
	running      int32 // atomic guard
}

func newDriftScanner(d *Daemon) *driftScanner {
	return &driftScanner{daemon: d}
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
$result.RogueAdmins = $rogueAdmins

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

	var state struct {
		Firewall      map[string]string `json:"Firewall"`
		Defender      string            `json:"Defender"`
		WindowsUpdate string            `json:"WindowsUpdate"`
		EventLog      string            `json:"EventLog"`
		RogueAdmins   []string          `json:"RogueAdmins"`
		RogueTasks    []struct {
			Name  string `json:"Name"`
			Path  string `json:"Path"`
			State string `json:"State"`
		} `json:"RogueTasks"`
		AgentStatus string `json:"AgentStatus"`
	}
	if err := json.Unmarshal([]byte(stdout), &state); err != nil {
		log.Printf("[driftscan] Parse error for %s: %v", t.hostname, err)
		return nil
	}

	var findings []driftFinding

	// Check firewall
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

	// Check Defender
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

	// Check Windows Update
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

	// Check Event Log
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

	// Rogue admins
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

	// Rogue scheduled tasks
	if len(state.RogueTasks) > 0 {
		taskNames := make([]string, 0, len(state.RogueTasks))
		for _, t := range state.RogueTasks {
			taskNames = append(taskNames, t.Name)
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

	// Agent not running on workstation
	if t.label == "WS" && state.AgentStatus != "Running" {
		findings = append(findings, driftFinding{
			Hostname:  t.hostname,
			CheckType: "agent_status",
			Expected:  "Running",
			Actual:    state.AgentStatus,
			Severity:  "medium",
		})
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
    $r.RogueAdmins = $rogueAdmins
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

	// Parse the same as direct scan
	var state struct {
		Firewall      map[string]string `json:"Firewall"`
		Defender      string            `json:"Defender"`
		WindowsUpdate string            `json:"WindowsUpdate"`
		AgentStatus   string            `json:"AgentStatus"`
		RogueAdmins   []string          `json:"RogueAdmins"`
		RogueTasks    []struct {
			Name  string `json:"Name"`
			Path  string `json:"Path"`
			State string `json:"State"`
		} `json:"RogueTasks"`
		Error string `json:"Error"`
	}
	if err := json.Unmarshal([]byte(stdout), &state); err != nil {
		log.Printf("[driftscan] DC proxy parse error for %s: %v (raw: %.200s)", t.hostname, err, stdout)
		return nil
	}
	if state.Error != "" {
		log.Printf("[driftscan] DC proxy session error for %s: %s", t.hostname, state.Error)
		return nil
	}

	var findings []driftFinding

	for profile, enabled := range state.Firewall {
		if strings.ToLower(enabled) == "false" {
			findings = append(findings, driftFinding{
				Hostname: t.hostname, CheckType: "firewall_status",
				Expected: "True", Actual: "False",
				HIPAAControl: "164.312(a)(1)", Severity: "high",
				Details: map[string]string{"profile": profile},
			})
		}
	}
	if state.Defender != "" && state.Defender != "Running" && state.Defender != "NotFound" {
		findings = append(findings, driftFinding{
			Hostname: t.hostname, CheckType: "windows_defender",
			Expected: "Running", Actual: state.Defender,
			HIPAAControl: "164.308(a)(5)(ii)(B)", Severity: "high",
		})
	}
	if state.WindowsUpdate == "Stopped" {
		findings = append(findings, driftFinding{
			Hostname: t.hostname, CheckType: "windows_update",
			Expected: "Running", Actual: "Stopped",
			HIPAAControl: "164.308(a)(5)(ii)(A)", Severity: "medium",
		})
	}
	if len(state.RogueAdmins) > 0 {
		findings = append(findings, driftFinding{
			Hostname: t.hostname, CheckType: "rogue_admin_users",
			Expected: "none", Actual: strings.Join(state.RogueAdmins, ", "),
			HIPAAControl: "164.312(a)(1)", Severity: "critical",
		})
	}
	if len(state.RogueTasks) > 0 {
		names := make([]string, 0, len(state.RogueTasks))
		for _, rt := range state.RogueTasks {
			names = append(names, rt.Name)
		}
		findings = append(findings, driftFinding{
			Hostname: t.hostname, CheckType: "rogue_scheduled_tasks",
			Expected: "none", Actual: strings.Join(names, ", "),
			HIPAAControl: "164.308(a)(1)(ii)(D)", Severity: "high",
		})
	}
	if state.AgentStatus != "Running" {
		findings = append(findings, driftFinding{
			Hostname: t.hostname, CheckType: "agent_status",
			Expected: "Running", Actual: state.AgentStatus,
			Severity: "medium",
		})
	}

	return findings
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
