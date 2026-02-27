package daemon

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/osiriscare/appliance/internal/discovery"
	"github.com/osiriscare/appliance/internal/grpcserver"
	"github.com/osiriscare/appliance/internal/winrm"
)

// escapePSString escapes a string for safe interpolation inside a PowerShell
// double-quoted string. Prevents injection if passwords contain " or $.
func escapePSString(s string) string {
	s = strings.ReplaceAll(s, "`", "``")
	s = strings.ReplaceAll(s, "\"", "`\"")
	s = strings.ReplaceAll(s, "$", "`$")
	return s
}

const (
	// Agent paths
	agentBinaryName  = "osiris-agent.exe"
	agentInstallDir  = `C:\OsirisCare`
	agentServiceName = "OsirisCareAgent"

	// Timing
	autoDeployInterval = 1 * time.Hour // Re-check for new workstations

	// DC proxy temp path for staging binaries
	dcTempDir = `C:\Windows\Temp`
)

// autoDeployer manages automatic Go agent deployment to discovered workstations.
// Deployment fallback chain:
//  1. Direct WinRM (NTLM) — fast, works when workstation allows NTLM
//  2. DC Proxy (Kerberos via Invoke-Command) — always works in domain
//  3. DC Proxy: CIM scheduled task to enable PSRemoting, then retry
//  4. DC Proxy: WMI process creation to enable PSRemoting, then retry
//  5. Escalate to operator after maxConsecutiveFailures (default 3)
type autoDeployer struct {
	daemon *Daemon

	mu           sync.Mutex
	deployed     map[string]time.Time // hostname → last successful deploy time
	lastCheck    map[string]time.Time // hostname → last status check time
	lastEnumTime time.Time            // last AD enumeration
	agentB64     string               // cached base64 of agent binary
	agentLoaded  bool
	running      int32 // atomic guard: 1 = cycle in progress

	// Track which hosts need DC proxy (avoid retrying direct NTLM every cycle)
	needsProxy sync.Map // hostname → true

	// Track consecutive deployment failures per host for escalation
	failures   map[string]int       // hostname → consecutive failure count
	escalated  map[string]time.Time // hostname → time of last escalation
}

const maxConsecutiveFailures = 3 // Escalate after this many failures

func newAutoDeployer(d *Daemon) *autoDeployer {
	return &autoDeployer{
		daemon:    d,
		deployed:  make(map[string]time.Time),
		lastCheck: make(map[string]time.Time),
		failures:  make(map[string]int),
		escalated: make(map[string]time.Time),
	}
}

// runAutoDeployIfNeeded is called each daemon cycle. It checks if it's time
// to enumerate and deploy, and runs if so. Uses atomic guard to prevent
// overlapping runs when the main loop ticks faster than AD enumeration.
func (ad *autoDeployer) runAutoDeployIfNeeded(ctx context.Context) {
	cfg := ad.daemon.config

	// Need DC credentials for AD enumeration + deployment
	if cfg.DomainController == nil || *cfg.DomainController == "" {
		return
	}
	if cfg.DCUsername == nil || cfg.DCPassword == nil {
		return
	}

	// Atomic guard: only one cycle at a time
	if !atomic.CompareAndSwapInt32(&ad.running, 0, 1) {
		return
	}
	defer atomic.StoreInt32(&ad.running, 0)

	// Check timing
	ad.mu.Lock()
	sinceLastEnum := time.Since(ad.lastEnumTime)
	firstRun := ad.lastEnumTime.IsZero()
	ad.mu.Unlock()

	if !firstRun && sinceLastEnum < autoDeployInterval {
		return
	}

	log.Printf("[autodeploy] Starting workstation discovery and agent deployment")
	ad.runAutoDeployOnce(ctx)
}

// ensureWinRMViaGPO configures WinRM on all domain computers via Group Policy.
// Two-pronged approach:
//  1. Registry-based GPO settings: auto-start WinRM, firewall rules, auth config
//  2. PowerShell startup script: runs Enable-PSRemoting -Force on every boot
// The startup script is the primary mechanism — it creates the WS-Management
// listener which registry settings alone don't do. Registry settings serve as
// belt-and-suspenders for service config and firewall rules.
// Only runs once per daemon lifetime.
var winrmGPODone sync.Map

func (ad *autoDeployer) ensureWinRMViaGPO(ctx context.Context) {
	cfg := ad.daemon.config
	dc := *cfg.DomainController

	if _, done := winrmGPODone.LoadOrStore(dc, true); done {
		return
	}

	log.Printf("[autodeploy] Configuring WinRM on domain workstations via GPO on %s", dc)

	target := ad.dcTarget()

	gpoScript := `
$ErrorActionPreference = 'Stop'
$Result = @{ Changed = $false; Computers = @(); StartupScript = $false }

try {
    Import-Module GroupPolicy -ErrorAction Stop
    Import-Module ActiveDirectory -ErrorAction SilentlyContinue
    $GPOName = "Default Domain Policy"
    $BasePath = "HKLM\SOFTWARE\Policies\Microsoft\Windows\WinRM\Service"

    # --- Part 1: Registry-based GPO settings ---

    # Enable WinRM service auto-start
    try {
        $val = Get-GPRegistryValue -Name $GPOName -Key $BasePath -ValueName "AllowAutoConfig" -ErrorAction Stop
        if ($val.Value -ne 1) {
            Set-GPRegistryValue -Name $GPOName -Key $BasePath -ValueName "AllowAutoConfig" -Type DWord -Value 1
            $Result.Changed = $true
        }
    } catch {
        Set-GPRegistryValue -Name $GPOName -Key $BasePath -ValueName "AllowAutoConfig" -Type DWord -Value 1
        $Result.Changed = $true
    }

    # Allow all IPs for WinRM (IPv4 filter)
    try {
        $val = Get-GPRegistryValue -Name $GPOName -Key $BasePath -ValueName "IPv4Filter" -ErrorAction Stop
    } catch {
        Set-GPRegistryValue -Name $GPOName -Key $BasePath -ValueName "IPv4Filter" -Type String -Value "*"
        $Result.Changed = $true
    }

    # Set WinRM service startup to Automatic
    $svcPath = "HKLM\SYSTEM\CurrentControlSet\Services\WinRM"
    try {
        $val = Get-GPRegistryValue -Name $GPOName -Key $svcPath -ValueName "Start" -ErrorAction Stop
    } catch {
        Set-GPRegistryValue -Name $GPOName -Key $svcPath -ValueName "Start" -Type DWord -Value 2
        $Result.Changed = $true
    }

    # Enable WinRM firewall rule
    $fwPath = "HKLM\SOFTWARE\Policies\Microsoft\WindowsFirewall\FirewallRules"
    try {
        $val = Get-GPRegistryValue -Name $GPOName -Key $fwPath -ValueName "WinRM-HTTP-In-TCP" -ErrorAction Stop
    } catch {
        Set-GPRegistryValue -Name $GPOName -Key $fwPath -ValueName "WinRM-HTTP-In-TCP" -Type String -Value "v2.31|Action=Allow|Active=TRUE|Dir=In|Protocol=6|LPort=5985|App=System|Name=WinRM HTTP|"
        $Result.Changed = $true
    }

    # Allow unencrypted traffic for HTTP WinRM (enables NTLM over HTTP)
    $clientPath = "HKLM\SOFTWARE\Policies\Microsoft\Windows\WinRM\Client"
    try {
        $val = Get-GPRegistryValue -Name $GPOName -Key $clientPath -ValueName "AllowUnencryptedTraffic" -ErrorAction Stop
    } catch {
        Set-GPRegistryValue -Name $GPOName -Key $clientPath -ValueName "AllowUnencryptedTraffic" -Type DWord -Value 1
        Set-GPRegistryValue -Name $GPOName -Key $BasePath -ValueName "AllowUnencryptedTraffic" -Type DWord -Value 1
        $Result.Changed = $true
    }

    # --- Part 2: GPO Startup Script (runs Enable-PSRemoting -Force on boot) ---
    # This is critical because registry settings alone don't create the WS-Management
    # HTTP listener. Enable-PSRemoting does everything: listener, auth, firewall.

    $gpoId = (Get-GPO -Name $GPOName).Id
    $base = "\\$env:USERDNSDOMAIN\SysVol\$env:USERDNSDOMAIN\Policies\{$gpoId}\Machine\Scripts"
    $dir = "$base\Startup"

    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }

    # Only write if script doesn't exist (idempotent)
    $scriptPath = "$dir\Setup-WinRM.ps1"
    if (-not (Test-Path $scriptPath)) {
        $script = @'
$ErrorActionPreference = 'SilentlyContinue'
Enable-PSRemoting -Force -SkipNetworkProfileCheck
Set-Item WSMan:\localhost\Service\Auth\Basic $true
Set-Item WSMan:\localhost\Service\Auth\Negotiate $true
Set-Item WSMan:\localhost\Service\Auth\Kerberos $true
Set-Item WSMan:\localhost\Service\AllowUnencrypted $true
Set-Item WSMan:\localhost\Client\TrustedHosts '*' -Force
netsh advfirewall firewall add rule name="WinRM HTTP" dir=in action=allow protocol=tcp localport=5985
netsh advfirewall firewall add rule name="DCOM RPC" dir=in action=allow protocol=tcp localport=135
Restart-Service WinRM -Force
'@
        Set-Content -Path $scriptPath -Value $script -Force
        $Result.StartupScript = $true
        $Result.Changed = $true
    }

    # Ensure psscripts.ini references the startup script (Group Policy reads this)
    $iniPath = "$base\psscripts.ini"
    if (-not (Test-Path $iniPath)) {
        $crlf = [char]13 + [char]10
        $ini = "[Startup]" + $crlf + "0CmdLine=Setup-WinRM.ps1" + $crlf + "0Parameters="
        Set-Content -Path $iniPath -Value $ini -Encoding Unicode -Force
        $Result.Changed = $true
    }

    # Force gpupdate on workstations via DCOM (doesn't need WinRM)
    if ($Result.Changed) {
        # Bump GPO version to force clients to re-download
        $dn = (Get-ADDomain).DistinguishedName
        $gpo = [ADSI]"LDAP://CN={$gpoId},CN=Policies,CN=System,$dn"
        $ver = [int]$gpo.Properties["versionNumber"].Value
        $newVer = $ver + 65536
        $gpo.Properties["versionNumber"].Value = $newVer
        $gpo.CommitChanges()

        # Update GPT.INI version on SYSVOL
        $gptIni = "\\$env:USERDNSDOMAIN\SysVol\$env:USERDNSDOMAIN\Policies\{$gpoId}\GPT.INI"
        $content = Get-Content $gptIni -Raw
        if ($content -match 'Version=(\d+)') {
            $content = $content -replace "Version=$([int]$Matches[1])", "Version=$newVer"
            Set-Content -Path $gptIni -Value $content -Force
        }

        try {
            $Computers = Get-ADComputer -Filter {OperatingSystem -like "*Windows*" -and OperatingSystem -notlike "*Server*"} -Properties Name |
                Select-Object -ExpandProperty Name
            foreach ($Computer in $Computers) {
                try {
                    Invoke-GPUpdate -Computer $Computer -Force -RandomDelayInMinutes 0 -ErrorAction SilentlyContinue
                    $Result.Computers += $Computer
                } catch { }
            }
        } catch { }
    }

    $Result.Success = $true
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Success = $false
}

$Result | ConvertTo-Json -Compress
`

	result := ad.daemon.winrmExec.Execute(target, gpoScript, "WINRM-GPO", "autodeploy", 120, 1, 15.0, nil)
	if result.Success {
		stdout, _ := result.Output["std_out"].(string)
		log.Printf("[autodeploy] WinRM GPO configured on %s: %s", dc, stdout)
	} else {
		log.Printf("[autodeploy] WinRM GPO config failed on %s: %s (will use DC proxy)", dc, result.Error)
		winrmGPODone.Delete(dc) // allow retry
	}
}

// runAutoDeployOnce performs one full cycle: enumerate → check → deploy.
func (ad *autoDeployer) runAutoDeployOnce(ctx context.Context) {
	cfg := ad.daemon.config
	dc := *cfg.DomainController
	username := *cfg.DCUsername
	password := *cfg.DCPassword

	// Ensure WinRM is enabled on domain workstations via GPO (best-effort)
	ad.ensureWinRMViaGPO(ctx)

	// Build AD enumerator using the daemon's WinRM executor
	executor := &adScriptExec{
		winrmExec: ad.daemon.winrmExec,
		username:  username,
		password:  password,
	}
	enumerator := discovery.NewADEnumerator(dc, username, password, "", executor)

	// Enumerate workstations from AD
	_, workstations, err := enumerator.EnumerateAll(ctx)
	if err != nil {
		log.Printf("[autodeploy] AD enumeration failed: %v", err)
		return
	}

	ad.mu.Lock()
	ad.lastEnumTime = time.Now()
	ad.mu.Unlock()

	if len(workstations) == 0 {
		log.Printf("[autodeploy] No workstations found in AD")
		return
	}

	log.Printf("[autodeploy] Found %d workstations in AD", len(workstations))

	// Resolve missing IPs
	enumerator.ResolveMissingIPs(ctx, workstations)

	// Filter to reachable workstations (WinRM port 5985)
	var reachable []discovery.ADComputer
	for i := range workstations {
		if discovery.TestConnectivity(ctx, &workstations[i], 5985) {
			reachable = append(reachable, workstations[i])
		}
	}

	// Workstations with WinRM port closed need DC proxy via Invoke-Command.
	// The DC can reach them even if we can't directly.
	var unreachableDirect []discovery.ADComputer
	for i := range workstations {
		found := false
		for _, r := range reachable {
			if r.Hostname == workstations[i].Hostname {
				found = true
				break
			}
		}
		if !found {
			unreachableDirect = append(unreachableDirect, workstations[i])
		}
	}

	log.Printf("[autodeploy] %d/%d reachable directly, %d need DC proxy",
		len(reachable), len(workstations), len(unreachableDirect))

	// Deploy to each workstation with fallback chain
	deployed := 0
	skipped := 0
	failed := 0

	// Combine all workstations — reachable first, then unreachable (DC proxy only)
	allTargets := append(reachable, unreachableDirect...)

	for _, ws := range allTargets {
		// Bail out if daemon is shutting down
		select {
		case <-ctx.Done():
			log.Printf("[autodeploy] Context cancelled, aborting deploy loop")
			return
		default:
		}

		hostname := ws.Hostname
		if hostname == "" {
			continue
		}

		// Skip if already deployed recently
		ad.mu.Lock()
		if t, ok := ad.deployed[hostname]; ok && time.Since(t) < 24*time.Hour {
			ad.mu.Unlock()
			skipped++
			continue
		}
		// Skip if escalated recently (back off for 4 hours after escalation)
		if t, ok := ad.escalated[hostname]; ok && time.Since(t) < 4*time.Hour {
			ad.mu.Unlock()
			log.Printf("[autodeploy] Skipping %s — escalated %s ago, backing off", hostname, time.Since(t).Round(time.Minute))
			skipped++
			continue
		}
		ad.mu.Unlock()

		// Deploy with fallback chain
		err := ad.deployWithFallback(ctx, ws)
		if err != nil {
			log.Printf("[autodeploy] Deploy to %s failed (all methods): %v", hostname, err)
			ad.mu.Lock()
			ad.failures[hostname]++
			failCount := ad.failures[hostname]
			ad.mu.Unlock()

			if failCount >= maxConsecutiveFailures {
				ad.escalateDeployFailure(hostname, failCount, err)
			}
			failed++
		} else {
			// Post-deploy verification: confirm agent is actually running.
			// Wait briefly for service to start, then check status.
			time.Sleep(5 * time.Second)
			installed, running := ad.verifyAgentPostDeploy(ctx, ws)
			if running {
				log.Printf("[autodeploy] Successfully deployed agent to %s (verified running)", hostname)
				ad.mu.Lock()
				ad.deployed[hostname] = time.Now()
				ad.failures[hostname] = 0 // Reset failure counter on success
				ad.mu.Unlock()
				deployed++
			} else if installed {
				log.Printf("[autodeploy] Agent installed on %s but NOT running — marking as failed", hostname)
				ad.mu.Lock()
				ad.failures[hostname]++
				ad.mu.Unlock()
				failed++
			} else {
				log.Printf("[autodeploy] Post-deploy verification failed for %s — agent not found", hostname)
				ad.mu.Lock()
				ad.failures[hostname]++
				ad.mu.Unlock()
				failed++
			}
		}
	}

	log.Printf("[autodeploy] Complete: deployed=%d, skipped=%d, failed=%d",
		deployed, skipped, failed)
}

// deployWithFallback tries to deploy to a workstation using the fallback chain:
// 1. Direct WinRM (if not known to need proxy) — single probe, no retries
// 2. DC Proxy via Invoke-Command (Kerberos/Negotiate)
//
// IMPORTANT: The direct WinRM probe uses exactly 1 attempt (no retries) to
// avoid triggering domain account lockout (default threshold: 5 attempts).
// Each 401 failure counts against the lockout counter.
func (ad *autoDeployer) deployWithFallback(ctx context.Context, ws discovery.ADComputer) error {
	hostname := ws.Hostname

	// Check if we already know this host needs DC proxy
	_, knownNeedsProxy := ad.needsProxy.Load(hostname)

	// Fallback 1: Try direct WinRM — single probe with 0 retries
	// Skip status check (it would be another NTLM attempt). The probe
	// itself tells us if direct WinRM works. We avoid wasting lockout attempts.
	if !knownNeedsProxy {
		target := ad.buildTarget(ws)
		if target != nil {
			// Single probe: try a lightweight command with 0 retries
			probeResult := ad.daemon.winrmExec.Execute(target,
				fmt.Sprintf(`$svc = Get-Service -Name "%s" -EA SilentlyContinue; if ($svc -and $svc.Status -eq "Running") { "RUNNING" } else { "NOT_RUNNING" }`, agentServiceName),
				"AGENT-PROBE", "autodeploy", 15, 0, 10.0, nil)

			if probeResult.Success {
				stdout, _ := probeResult.Output["std_out"].(string)
				if strings.TrimSpace(stdout) == "RUNNING" {
					return nil // Already deployed and running
				}
				// Direct WinRM works — proceed with full deploy
				err := ad.deployAgentDirect(ctx, target, ws)
				if err == nil {
					return nil
				}
				log.Printf("[autodeploy] [%s] Direct deploy failed: %v — trying DC proxy", hostname, err)
			} else if strings.Contains(probeResult.Error, "401") {
				log.Printf("[autodeploy] [%s] Direct WinRM auth failed (401) — switching to DC proxy", hostname)
				ad.needsProxy.Store(hostname, true)
			} else {
				log.Printf("[autodeploy] [%s] Direct WinRM failed: %s — trying DC proxy", hostname, probeResult.Error)
			}
		}
	}

	// Fallback 2: DC Proxy via Invoke-Command (Kerberos)
	log.Printf("[autodeploy] [%s] Deploying via DC proxy (Kerberos)", hostname)

	// First check if already installed via DC proxy
	installed, running := ad.checkAgentStatusViaDC(ctx, hostname)
	if installed && running {
		return nil
	}

	return ad.deployAgentViaDC(ctx, ws)
}

// dcTarget returns a WinRM target for the domain controller.
func (ad *autoDeployer) dcTarget() *winrm.Target {
	cfg := ad.daemon.config
	return &winrm.Target{
		Hostname:  *cfg.DomainController,
		Port:      5986,
		Username:  *cfg.DCUsername,
		Password:  *cfg.DCPassword,
		UseSSL:    true,
		VerifySSL: false,
	}
}

// buildTarget creates a WinRM target for direct workstation connection.
func (ad *autoDeployer) buildTarget(ws discovery.ADComputer) *winrm.Target {
	cfg := ad.daemon.config
	if cfg.DCUsername == nil || cfg.DCPassword == nil {
		return nil
	}

	hostname := ""
	if ws.IPAddress != nil && *ws.IPAddress != "" {
		hostname = *ws.IPAddress
	} else if ws.FQDN != "" {
		hostname = ws.FQDN
	} else {
		hostname = ws.Hostname
	}

	return &winrm.Target{
		Hostname:  hostname,
		Port:      5986,
		Username:  *cfg.DCUsername,
		Password:  *cfg.DCPassword,
		UseSSL:    true,
		VerifySSL: false,
	}
}

// checkAgentStatus checks agent status via direct WinRM.
func (ad *autoDeployer) checkAgentStatus(_ context.Context, target *winrm.Target) (installed bool, running bool) {
	script := fmt.Sprintf(`
$svc = Get-Service -Name "%s" -ErrorAction SilentlyContinue
if ($svc) {
    @{ installed = $true; running = ($svc.Status -eq "Running") } | ConvertTo-Json -Compress
} else {
    @{ installed = $false; running = $false } | ConvertTo-Json -Compress
}
`, agentServiceName)

	result := ad.daemon.winrmExec.Execute(target, script, "AGENT-CHECK", "autodeploy", 15, 0, 10.0, nil)
	if !result.Success {
		return false, false
	}

	stdout, _ := result.Output["std_out"].(string)
	if stdout == "" {
		return false, false
	}

	var status struct {
		Installed bool `json:"installed"`
		Running   bool `json:"running"`
	}
	if err := json.Unmarshal([]byte(stdout), &status); err != nil {
		return false, false
	}

	return status.Installed, status.Running
}

// checkAgentStatusViaDC checks agent status via DC proxy (Invoke-Command).
// Uses the same 3-tier auth fallback as deployAgentViaDC: Kerberos → Negotiate → IP+Negotiate.
func (ad *autoDeployer) checkAgentStatusViaDC(_ context.Context, hostname string) (installed bool, running bool) {
	cfg := ad.daemon.config

	script := fmt.Sprintf(`
$ErrorActionPreference = 'Stop'
try {
    $Computer = "%s"
    $secPass = ConvertTo-SecureString "%s" -AsPlainText -Force
    $cred = New-Object PSCredential("%s", $secPass)

    # 3-tier session fallback: Kerberos → Negotiate → IP+Negotiate
    $session = $null
    try {
        $session = New-PSSession -ComputerName $Computer -Credential $cred -ErrorAction Stop
    } catch {
        try {
            $session = New-PSSession -ComputerName $Computer -Credential $cred -Authentication Negotiate -ErrorAction Stop
        } catch {
            try {
                $ip = [string]((Resolve-DnsName $Computer -Type A -EA Stop | Select-Object -First 1).IPAddress)
                $currentTH = (Get-Item WSMan:\localhost\Client\TrustedHosts -EA SilentlyContinue).Value
                if ($currentTH -notlike "*$ip*") {
                    if ($currentTH -and $currentTH -ne "") {
                        Set-Item WSMan:\localhost\Client\TrustedHosts -Value "$currentTH,$ip" -Force
                    } else {
                        Set-Item WSMan:\localhost\Client\TrustedHosts -Value $ip -Force
                    }
                }
                $session = New-PSSession -ComputerName $ip -Credential $cred -Authentication Negotiate -ErrorAction Stop
            } catch {
                throw "All session methods failed: $_"
            }
        }
    }

    $result = Invoke-Command -Session $session -ScriptBlock {
        param($svcName)
        $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
        if ($svc) {
            @{ installed = $true; running = ($svc.Status -eq "Running") }
        } else {
            @{ installed = $false; running = $false }
        }
    } -ArgumentList "%s" -ErrorAction Stop
    Remove-PSSession $session -EA SilentlyContinue
    $result | ConvertTo-Json -Compress
} catch {
    @{ installed = $false; running = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`, escapePSString(hostname), escapePSString(*cfg.DCPassword), escapePSString(*cfg.DCUsername), agentServiceName)

	result := ad.daemon.winrmExec.Execute(ad.dcTarget(), script, "AGENT-CHECK-PROXY", "autodeploy", 30, 0, 15.0, nil)
	if !result.Success {
		return false, false
	}

	stdout, _ := result.Output["std_out"].(string)
	if stdout == "" {
		return false, false
	}

	var status struct {
		Installed bool   `json:"installed"`
		Running   bool   `json:"running"`
		Error     string `json:"error,omitempty"`
	}
	if err := json.Unmarshal([]byte(stdout), &status); err != nil {
		return false, false
	}

	if status.Error != "" {
		log.Printf("[autodeploy] [%s] DC proxy status check error: %s", hostname, status.Error)
	}

	return status.Installed, status.Running
}

// verifyAgentPostDeploy confirms the agent is actually installed and running
// after a deploy attempt. Uses direct WinRM first, falls back to DC proxy.
func (ad *autoDeployer) verifyAgentPostDeploy(ctx context.Context, ws discovery.ADComputer) (installed, running bool) {
	hostname := ws.Hostname

	// Try direct WinRM first
	target := ad.buildTarget(ws)
	if target != nil {
		probeResult := ad.daemon.winrmExec.Execute(target,
			fmt.Sprintf(`$svc = Get-Service -Name "%s" -EA SilentlyContinue; if ($svc) { @{installed=$true;running=($svc.Status -eq "Running")} } else { @{installed=$false;running=$false} } | ConvertTo-Json -Compress`, agentServiceName),
			"AGENT-VERIFY", "autodeploy", 15, 0, 10.0, nil)

		if probeResult.Success {
			stdout, _ := probeResult.Output["std_out"].(string)
			var status struct {
				Installed bool `json:"installed"`
				Running   bool `json:"running"`
			}
			if err := json.Unmarshal([]byte(stdout), &status); err == nil {
				log.Printf("[autodeploy] [%s] Post-deploy verify (direct): installed=%v running=%v",
					hostname, status.Installed, status.Running)
				return status.Installed, status.Running
			}
		}
	}

	// Fall back to DC proxy
	installed, running = ad.checkAgentStatusViaDC(ctx, hostname)
	log.Printf("[autodeploy] [%s] Post-deploy verify (DC proxy): installed=%v running=%v",
		hostname, installed, running)
	return installed, running
}

// escalateDeployFailure reports persistent deployment failures as incidents
// for operator investigation. After maxConsecutiveFailures, we stop hammering
// the workstation and back off for 4 hours. This handles the real-world case
// where PSRemoting/DCOM are both blocked and require manual intervention
// (e.g., local GPO, physical access, or VPN-isolated workstation).
func (ad *autoDeployer) escalateDeployFailure(hostname string, failCount int, lastErr error) {
	ad.mu.Lock()
	ad.escalated[hostname] = time.Now()
	ad.mu.Unlock()

	errMsg := fmt.Sprintf("Agent deployment to %s has failed %d consecutive times. "+
		"Last error: %v. The workstation may need manual WinRM/PSRemoting enablement. "+
		"Backing off for 4 hours. Remediation: run 'Enable-PSRemoting -Force' locally, "+
		"or verify GPO startup script is applied and reboot.",
		hostname, failCount, lastErr)

	log.Printf("[autodeploy] ESCALATION: %s", errMsg)

	// Report as incident to Central Command via the daemon's healing pipeline
	req := grpcserver.HealRequest{
		AgentID:   "appliance-daemon",
		Hostname:  hostname,
		CheckType: "WIN-DEPLOY-UNREACHABLE",
		Expected:  "agent_deployed",
		Actual:    fmt.Sprintf("deploy_failed_%d_attempts", failCount),
		Metadata: map[string]string{
			"error":       lastErr.Error(),
			"remediation": "Enable-PSRemoting -Force on workstation, or reboot for GPO startup script",
		},
	}
	ad.daemon.healIncident(context.Background(), req)
}

// loadAgentBinary reads and base64-encodes the agent binary (cached).
func (ad *autoDeployer) loadAgentBinary() (string, error) {
	ad.mu.Lock()
	defer ad.mu.Unlock()

	if ad.agentLoaded {
		return ad.agentB64, nil
	}

	paths := []string{
		filepath.Join(ad.daemon.config.StateDir, "agent", agentBinaryName),
		"/var/lib/msp/agent/" + agentBinaryName,
	}

	var data []byte
	var loadPath string
	for _, p := range paths {
		var err error
		data, err = os.ReadFile(p)
		if err == nil {
			loadPath = p
			break
		}
	}

	if data == nil {
		return "", fmt.Errorf("agent binary not found at %v", paths)
	}

	log.Printf("[autodeploy] Loaded agent binary from %s (%d bytes)", loadPath, len(data))
	ad.agentB64 = base64.StdEncoding.EncodeToString(data)
	ad.agentLoaded = true
	return ad.agentB64, nil
}

// deployAgentDirect deploys the agent via direct WinRM to the workstation.
// This is the fast path — works when the workstation accepts NTLM auth.
func (ad *autoDeployer) deployAgentDirect(ctx context.Context, target *winrm.Target, ws discovery.ADComputer) error {
	agentB64, err := ad.loadAgentBinary()
	if err != nil {
		return err
	}

	grpcAddr := ad.daemon.config.GRPCListenAddr()
	hostname := ws.Hostname

	// Step 1: Create install directory (0 retries — probe already confirmed WinRM works)
	log.Printf("[autodeploy] [%s] Direct: Step 1/5 Creating directory", hostname)
	mkdirResult := ad.daemon.winrmExec.Execute(target,
		fmt.Sprintf(`New-Item -ItemType Directory -Force -Path "%s" | Out-Null; "OK"`, agentInstallDir),
		"AGENT-DEPLOY-MKDIR", "autodeploy", 30, 0, 10.0, nil)
	if !mkdirResult.Success {
		return fmt.Errorf("mkdir failed: %s", mkdirResult.Error)
	}

	// Step 2: Write agent binary (chunked base64)
	log.Printf("[autodeploy] [%s] Direct: Step 2/5 Writing binary (%d bytes encoded)", hostname, len(agentB64))
	if err := ad.writeB64ChunksToTarget(target, agentB64, hostname); err != nil {
		return err
	}

	// Step 3: Write config
	log.Printf("[autodeploy] [%s] Direct: Step 3/5 Writing config", hostname)
	if err := ad.writeConfigToTarget(target, grpcAddr); err != nil {
		return err
	}

	// Step 4: Install service
	log.Printf("[autodeploy] [%s] Direct: Step 4/5 Installing service", hostname)
	if err := ad.installServiceOnTarget(target); err != nil {
		return err
	}

	// Step 5: Verify
	log.Printf("[autodeploy] [%s] Direct: Step 5/5 Verifying", hostname)
	installed, running := ad.checkAgentStatus(ctx, target)
	if !installed || !running {
		return fmt.Errorf("verification failed: installed=%v running=%v", installed, running)
	}

	return nil
}

// stageAgentToNETLOGON downloads the agent binary from the appliance's HTTP file
// server to the DC's NETLOGON share. NETLOGON is readable by all domain computers,
// making it an efficient distribution point. Only stages once per daemon lifetime.
//
// Uses Invoke-WebRequest (native HTTP) instead of WinRM chunk upload — transfers
// 16MB in seconds instead of hours.
var netlogonStaged sync.Map

func (ad *autoDeployer) stageAgentToNETLOGON(ctx context.Context) error {
	cfg := ad.daemon.config
	dc := *cfg.DomainController

	if _, done := netlogonStaged.Load(dc); done {
		return nil
	}

	// Make sure the binary exists locally
	if _, err := ad.loadAgentBinary(); err != nil {
		return err
	}

	dcTarget := ad.dcTarget()

	// Get the appliance's LAN IP for the download URL
	applianceIP := ad.daemon.config.GRPCListenAddr()
	// GRPCListenAddr returns "ip:port", extract just the IP
	if idx := strings.LastIndex(applianceIP, ":"); idx >= 0 {
		applianceIP = applianceIP[:idx]
	}

	downloadURL := fmt.Sprintf("http://%s:8090/agent/%s", applianceIP, agentBinaryName)

	log.Printf("[autodeploy] Staging agent binary to NETLOGON via HTTP download from %s", downloadURL)

	// Single WinRM command: download from appliance HTTP server → save to NETLOGON
	stageScript := fmt.Sprintf(`
$ErrorActionPreference = 'Stop'
try {
    # Download agent binary from appliance HTTP file server
    $tempPath = "%s\%s"
    Invoke-WebRequest -Uri "%s" -OutFile $tempPath -UseBasicParsing -ErrorAction Stop

    # Copy to NETLOGON share (readable by all domain PCs)
    $netlogon = (Get-SmbShare -Name NETLOGON -ErrorAction Stop).Path
    $dest = Join-Path $netlogon "%s"
    Copy-Item -Path $tempPath -Destination $dest -Force
    Remove-Item -Path $tempPath -Force -EA SilentlyContinue

    @{ Success = $true; Path = $dest; Size = (Get-Item $dest).Length } | ConvertTo-Json -Compress
} catch {
    @{ Success = $false; Error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`, dcTempDir, agentBinaryName, downloadURL, agentBinaryName)

	result := ad.daemon.winrmExec.Execute(dcTarget, stageScript, "NETLOGON-STAGE", "autodeploy", 120, 1, 30.0, nil)
	if !result.Success {
		return fmt.Errorf("NETLOGON stage failed: %s", result.Error)
	}

	stdout, _ := result.Output["std_out"].(string)
	log.Printf("[autodeploy] Agent staged to NETLOGON: %s", stdout)

	// Check if the script reported success
	if strings.Contains(stdout, `"Success":false`) {
		return fmt.Errorf("NETLOGON stage script error: %s", stdout)
	}

	netlogonStaged.Store(dc, true)
	return nil
}

// deployAgentViaDC deploys the agent through the DC using Invoke-Command.
// Uses NETLOGON share for binary distribution (fast SMB) and registers SPNs +
// TrustedHosts to fix common Kerberos/NTLM issues.
//
// Fallback chain within DC proxy:
//   a) Kerberos PSSession (default, works when SPNs are correct)
//   b) Negotiate with TrustedHosts (works when Kerberos SPNs are broken)
//   c) Log error for manual investigation
func (ad *autoDeployer) deployAgentViaDC(ctx context.Context, ws discovery.ADComputer) error {
	cfg := ad.daemon.config
	grpcAddr := cfg.GRPCListenAddr()
	hostname := ws.Hostname
	dcTarget := ad.dcTarget()

	// Stage agent binary to NETLOGON (once per daemon lifetime)
	if err := ad.stageAgentToNETLOGON(ctx); err != nil {
		return fmt.Errorf("stage to NETLOGON: %w", err)
	}

	log.Printf("[autodeploy] [%s] DC proxy: Deploying via NETLOGON + Invoke-Command", hostname)

	configJSON := fmt.Sprintf(`{"appliance_addr":"%s","check_interval":300}`, grpcAddr)

	// Get domain name for NETLOGON UNC path
	// Single script on DC that:
	// 1. Registers HTTP SPN for workstation (fixes Kerberos)
	// 2. Adds workstation to TrustedHosts (enables Negotiate fallback)
	// 3. Creates PSSession with Negotiate auth
	// 4. Copies binary from NETLOGON via UNC path (fast SMB)
	// 5. Writes config + installs service
	deployScript := fmt.Sprintf(`
$ErrorActionPreference = 'Stop'
$Result = @{ Step = "init"; Method = "unknown" }

try {
    $Computer = "%s"
    $secPass = ConvertTo-SecureString "%s" -AsPlainText -Force
    $cred = New-Object PSCredential("%s", $secPass)
    $domain = (Get-ADDomain).DNSRoot

    # Fix 1: Register HTTP SPN for workstation (required for Kerberos WinRM)
    $Result.Step = "spn"
    try {
        setspn -S "HTTP/$Computer" "$Computer" 2>&1 | Out-Null
        setspn -S "HTTP/$Computer.$domain" "$Computer" 2>&1 | Out-Null
    } catch { }

    # Fix 2: Add workstation to TrustedHosts (allows Negotiate/NTLM fallback)
    $Result.Step = "trustedhosts"
    $current = (Get-Item WSMan:\localhost\Client\TrustedHosts -EA SilentlyContinue).Value
    if ($current -notlike "*$Computer*") {
        if ($current -and $current -ne "") {
            Set-Item WSMan:\localhost\Client\TrustedHosts -Value "$current,$Computer" -Force
        } else {
            Set-Item WSMan:\localhost\Client\TrustedHosts -Value $Computer -Force
        }
    }

    # Try creating PSSession — Kerberos first, then Negotiate, then Enable-PSRemoting via CIM
    $session = $null
    $Result.Step = "session"
    $lastSessionErr = $null

    # Attempt 1: Default (Kerberos)
    try {
        $session = New-PSSession -ComputerName $Computer -Credential $cred -ErrorAction Stop
        $Result.Method = "kerberos"
    } catch {
        $lastSessionErr = $_
        # Attempt 2: Negotiate (NTLM fallback)
        try {
            $session = New-PSSession -ComputerName $Computer -Credential $cred -Authentication Negotiate -ErrorAction Stop
            $Result.Method = "negotiate"
        } catch {
            $lastSessionErr = $_
            # Attempt 3: Use IP with TrustedHosts
            try {
                $ip = [string]((Resolve-DnsName $Computer -Type A -EA Stop | Select-Object -First 1).IPAddress)
                $currentTH = (Get-Item WSMan:\localhost\Client\TrustedHosts).Value
                if ($currentTH -notlike "*$ip*") {
                    Set-Item WSMan:\localhost\Client\TrustedHosts -Value "$currentTH,$ip" -Force
                }
                $session = New-PSSession -ComputerName $ip -Credential $cred -Authentication Negotiate -ErrorAction Stop
                $Result.Method = "negotiate_ip"
            } catch {
                $lastSessionErr = $_
            }
        }
    }

    # Attempt 4: Enable PSRemoting via CIM scheduled task (DCOM, bypasses WinRM)
    if (-not $session) {
        $Result.Step = "enable_psremoting_cim"
        $cimErr = $null
        try {
            $cs = New-CimSession -ComputerName $Computer -ErrorAction Stop
            $a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -Command Enable-PSRemoting -Force -SkipNetworkProfileCheck; Set-Item WSMan:\localhost\Service\Auth\Negotiate -Value true; Set-Item WSMan:\localhost\Service\Auth\Kerberos -Value true; Restart-Service WinRM -Force"
            Register-ScheduledTask -TaskName "OsirisEnablePSR" -Action $a -CimSession $cs -Force -User "SYSTEM" -RunLevel Highest -EA Stop
            Start-ScheduledTask -TaskName "OsirisEnablePSR" -CimSession $cs
            Start-Sleep -Seconds 20
            Unregister-ScheduledTask -TaskName "OsirisEnablePSR" -CimSession $cs -Confirm:$false -EA SilentlyContinue
            Remove-CimSession $cs -EA SilentlyContinue

            # Retry session after enabling PSRemoting
            $Result.Step = "session_retry_cim"
            try {
                $session = New-PSSession -ComputerName $Computer -Credential $cred -ErrorAction Stop
                $Result.Method = "kerberos_after_cim"
            } catch {
                try {
                    $session = New-PSSession -ComputerName $Computer -Credential $cred -Authentication Negotiate -ErrorAction Stop
                    $Result.Method = "negotiate_after_cim"
                } catch {
                    $cimErr = $_
                }
            }
        } catch {
            $cimErr = $_
        }
    }

    # Attempt 5: Enable PSRemoting via WMI process creation (older DCOM path, sometimes works when CIM doesn't)
    if (-not $session) {
        $Result.Step = "enable_psremoting_wmi"
        $wmiErr = $null
        try {
            $wmiCmd = 'powershell.exe -NoProfile -Command "Enable-PSRemoting -Force -SkipNetworkProfileCheck; Set-Item WSMan:\localhost\Service\Auth\Negotiate $true; Set-Item WSMan:\localhost\Service\Auth\Kerberos $true; Restart-Service WinRM -Force"'
            $wmiResult = Invoke-WmiMethod -Class Win32_Process -Name Create -ComputerName $Computer -Credential $cred -ArgumentList @($wmiCmd) -ErrorAction Stop
            if ($wmiResult.ReturnValue -eq 0) {
                Start-Sleep -Seconds 25

                # Retry session after WMI-based enablement
                $Result.Step = "session_retry_wmi"
                try {
                    $session = New-PSSession -ComputerName $Computer -Credential $cred -ErrorAction Stop
                    $Result.Method = "kerberos_after_wmi"
                } catch {
                    try {
                        $session = New-PSSession -ComputerName $Computer -Credential $cred -Authentication Negotiate -ErrorAction Stop
                        $Result.Method = "negotiate_after_wmi"
                    } catch {
                        $wmiErr = $_
                    }
                }
            } else {
                $wmiErr = "WMI Create process returned $($wmiResult.ReturnValue)"
            }
        } catch {
            $wmiErr = $_
        }

        if (-not $session) {
            $errParts = @()
            if ($lastSessionErr) { $errParts += "PSSession: $lastSessionErr" }
            if ($cimErr) { $errParts += "CIM: $cimErr" }
            if ($wmiErr) { $errParts += "WMI: $wmiErr" }
            throw ("All remote methods failed. " + ($errParts -join ". "))
        }
    }

    # Step 2: Create directory on workstation
    $Result.Step = "mkdir"
    Invoke-Command -Session $session -ScriptBlock {
        New-Item -ItemType Directory -Force -Path "%s" | Out-Null
    } -ErrorAction Stop

    # Step 3: Copy binary from NETLOGON (fast SMB, no WinRM transfer needed)
    $Result.Step = "copy"
    $netlogonPath = "\\$domain\NETLOGON\%s"
    Invoke-Command -Session $session -ScriptBlock {
        param($src, $dest)
        Copy-Item -Path $src -Destination $dest -Force -ErrorAction Stop
    } -ArgumentList $netlogonPath, "%s\%s" -ErrorAction Stop

    # Step 4: Write config
    $Result.Step = "config"
    $configContent = '%s'
    Invoke-Command -Session $session -ScriptBlock {
        param($cfg, $dir)
        Set-Content -Path "$dir\config.json" -Value $cfg -Encoding UTF8
    } -ArgumentList $configContent, "%s" -ErrorAction Stop

    # Step 5: Install and start service
    $Result.Step = "service"
    Invoke-Command -Session $session -ScriptBlock {
        param($svcName, $exePath, $configPath)
        $existing = Get-Service -Name $svcName -ErrorAction SilentlyContinue
        if ($existing) {
            Stop-Service -Name $svcName -Force -EA SilentlyContinue
            Start-Sleep -Seconds 2
            sc.exe delete $svcName | Out-Null
            Start-Sleep -Seconds 2
        }
        $binPath = """$exePath"" --config ""$configPath"""
        New-Service -Name $svcName -BinaryPathName $binPath -DisplayName "OsirisCare Compliance Agent" -Description "HIPAA compliance monitoring agent" -StartupType Automatic -ErrorAction Stop
        Start-Service -Name $svcName -ErrorAction Stop
        sc.exe failure $svcName reset= 86400 actions= restart/60000/restart/60000/restart/60000 | Out-Null
        Start-Sleep -Seconds 3
        $svc = Get-Service -Name $svcName
        if ($svc.Status -ne "Running") {
            throw "Service not running: $($svc.Status)"
        }
    } -ArgumentList "%s", "%s\%s", "%s\config.json" -ErrorAction Stop

    # Verify
    $Result.Step = "verify"
    $svcStatus = Invoke-Command -Session $session -ScriptBlock {
        param($svcName)
        $svc = Get-Service -Name $svcName -EA SilentlyContinue
        if ($svc) { @{ installed = $true; running = ($svc.Status -eq "Running") } }
        else { @{ installed = $false; running = $false } }
    } -ArgumentList "%s" -ErrorAction Stop

    Remove-PSSession $session -EA SilentlyContinue

    $Result.Success = $true
    $Result.Installed = $svcStatus.installed
    $Result.Running = $svcStatus.running
} catch {
    $Result.Success = $false
    $Result.Error = $_.Exception.Message
    if ($session) { Remove-PSSession $session -EA SilentlyContinue }
}

$Result | ConvertTo-Json -Compress
`,
		escapePSString(hostname), escapePSString(*cfg.DCPassword), escapePSString(*cfg.DCUsername),
		agentInstallDir,                           // mkdir
		agentBinaryName,                           // NETLOGON source filename
		agentInstallDir, agentBinaryName,          // copy destination
		configJSON, agentInstallDir,               // config
		agentServiceName, agentInstallDir, agentBinaryName, agentInstallDir, // service
		agentServiceName, // verify
	)

	deployResult := ad.daemon.winrmExec.Execute(dcTarget, deployScript, "AGENT-DEPLOY-PROXY", "autodeploy", 300, 1, 60.0, nil)

	stdout, _ := deployResult.Output["std_out"].(string)
	log.Printf("[autodeploy] [%s] DC proxy result: %s", hostname, stdout)

	if !deployResult.Success {
		return fmt.Errorf("DC proxy deploy failed: %s", deployResult.Error)
	}

	// Parse result
	var proxyResult struct {
		Success   bool   `json:"Success"`
		Step      string `json:"Step"`
		Method    string `json:"Method"`
		Error     string `json:"Error,omitempty"`
		Installed bool   `json:"Installed"`
		Running   bool   `json:"Running"`
	}
	if err := json.Unmarshal([]byte(stdout), &proxyResult); err != nil {
		// Empty or unparseable output is NOT a success — the deploy script
		// must return valid JSON with Success:true to confirm deployment.
		return fmt.Errorf("DC proxy parse error (deploy unconfirmed): %v (raw: %s)", err, stdout)
	}

	if !proxyResult.Success {
		return fmt.Errorf("DC proxy failed at step '%s' (method=%s): %s",
			proxyResult.Step, proxyResult.Method, proxyResult.Error)
	}

	if !proxyResult.Installed || !proxyResult.Running {
		return fmt.Errorf("DC proxy verification failed: installed=%v running=%v",
			proxyResult.Installed, proxyResult.Running)
	}

	log.Printf("[autodeploy] [%s] DC proxy: Deployed via %s — agent installed and running",
		hostname, proxyResult.Method)
	return nil
}

// writeB64ChunksToTarget writes base64-encoded data in chunks to a target via WinRM.
func (ad *autoDeployer) writeB64ChunksToTarget(target *winrm.Target, b64Data string, label string) error {
	tempB64File := agentInstallDir + `\agent.b64`

	// Clear any previous temp file
	ad.daemon.winrmExec.Execute(target,
		fmt.Sprintf(`New-Item -ItemType Directory -Force -Path "%s" | Out-Null; Remove-Item -Path "%s" -Force -ErrorAction SilentlyContinue`, agentInstallDir, tempB64File),
		"DEPLOY-PREP-"+label, "autodeploy", 10, 0, 5.0, nil)

	chunkSize := 400000
	for i := 0; i < len(b64Data); i += chunkSize {
		end := i + chunkSize
		if end > len(b64Data) {
			end = len(b64Data)
		}
		chunk := b64Data[i:end]

		appendScript := fmt.Sprintf(`Add-Content -Path "%s" -Value "%s" -NoNewline`, tempB64File, chunk)
		chunkResult := ad.daemon.winrmExec.Execute(target,
			appendScript, "DEPLOY-CHUNK-"+label, "autodeploy", 60, 1, 30.0, nil)
		if !chunkResult.Success {
			return fmt.Errorf("write chunk %d failed: %s", i/chunkSize, chunkResult.Error)
		}
	}

	return nil
}

// writeConfigToTarget writes the agent config to a workstation via direct WinRM.
func (ad *autoDeployer) writeConfigToTarget(target *winrm.Target, grpcAddr string) error {
	configJSON := fmt.Sprintf(`{
    "appliance_addr": "%s",
    "check_interval": 300
}`, grpcAddr)

	configScript := fmt.Sprintf(`
Set-Content -Path "%s\config.json" -Value @'
%s
'@ -Encoding UTF8
"OK"
`, agentInstallDir, configJSON)

	configResult := ad.daemon.winrmExec.Execute(target,
		configScript, "AGENT-DEPLOY-CONFIG", "autodeploy", 30, 1, 10.0, nil)
	if !configResult.Success {
		return fmt.Errorf("write config failed: %s", configResult.Error)
	}
	return nil
}

// installServiceOnTarget installs and starts the agent service via direct WinRM.
func (ad *autoDeployer) installServiceOnTarget(target *winrm.Target) error {
	// First decode the base64 to exe
	tempB64File := agentInstallDir + `\agent.b64`
	decodeScript := fmt.Sprintf(`
$b64 = Get-Content -Path "%s" -Raw
$bytes = [Convert]::FromBase64String($b64)
[IO.File]::WriteAllBytes("%s\%s", $bytes)
Remove-Item -Path "%s" -Force
"OK"
`, tempB64File, agentInstallDir, agentBinaryName, tempB64File)

	decodeResult := ad.daemon.winrmExec.Execute(target,
		decodeScript, "AGENT-DEPLOY-DECODE", "autodeploy", 120, 1, 60.0, nil)
	if !decodeResult.Success {
		return fmt.Errorf("decode binary failed: %s", decodeResult.Error)
	}

	// Install service
	serviceScript := fmt.Sprintf(`
$ErrorActionPreference = 'Stop'
$serviceName = "%s"
$exePath = "%s\%s"
$configPath = "%s\config.json"

$existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    sc.exe delete $serviceName | Out-Null
    Start-Sleep -Seconds 2
}

New-Service -Name $serviceName -BinaryPathName """$exePath"" --config ""$configPath""" -DisplayName "OsirisCare Compliance Agent" -Description "HIPAA compliance monitoring agent" -StartupType Automatic -ErrorAction Stop

Start-Service -Name $serviceName -ErrorAction Stop

sc.exe failure $serviceName reset= 86400 actions= restart/60000/restart/60000/restart/60000 | Out-Null

Start-Sleep -Seconds 3
$svc = Get-Service -Name $serviceName
if ($svc.Status -ne "Running") {
    throw "Service failed to start. Status: $($svc.Status)"
}
"SUCCESS"
`, agentServiceName, agentInstallDir, agentBinaryName, agentInstallDir)

	svcResult := ad.daemon.winrmExec.Execute(target,
		serviceScript, "AGENT-DEPLOY-SVC", "autodeploy", 90, 1, 30.0, nil)
	if !svcResult.Success {
		return fmt.Errorf("service install failed: %s", svcResult.Error)
	}
	return nil
}

// adScriptExec adapts the WinRM executor to the discovery.ScriptExecutor interface.
type adScriptExec struct {
	winrmExec *winrm.Executor
	username  string
	password  string
}

func (e *adScriptExec) RunScript(_ context.Context, hostname, script, username, password string, timeout int) (string, error) {
	if username == "" {
		username = e.username
	}
	if password == "" {
		password = e.password
	}

	target := &winrm.Target{
		Hostname:  hostname,
		Port:      5986,
		Username:  username,
		Password:  password,
		UseSSL:    true,
		VerifySSL: false,
	}

	result := e.winrmExec.Execute(target, script, "AD-ENUM", "discovery", timeout, 1, float64(timeout), nil)
	if !result.Success {
		return "", fmt.Errorf("%s", result.Error)
	}
	stdout, _ := result.Output["std_out"].(string)
	return stdout, nil
}
