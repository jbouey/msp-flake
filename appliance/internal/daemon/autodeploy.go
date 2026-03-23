package daemon

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/osiriscare/appliance/internal/discovery"
	"github.com/osiriscare/appliance/internal/grpcserver"
	"github.com/osiriscare/appliance/internal/maputil"
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

	// DC WinRM SSL probe result (cached once per daemon lifetime)
	dcSSLProbed bool
	dcUseSSL    bool
	dcPort      int

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

// processPendingDeploys handles "Take Over" deploys from Central Command.
// Called after each successful checkin when the response includes pending_deploys.
func (a *autoDeployer) processPendingDeploys(ctx context.Context, deploys []PendingDeploy, siteID string) []DeployResult {
	var results []DeployResult
	for _, deploy := range deploys {
		result := DeployResult{DeviceID: deploy.DeviceID}

		var err error
		switch deploy.DeployMethod {
		case "ssh":
			err = a.daemon.deployViaSSH(ctx, deploy, siteID)
		case "winrm":
			// Reuse existing WinRM deploy infrastructure
			err = fmt.Errorf("winrm auto-deploy not yet implemented for standalone devices")
		default:
			err = fmt.Errorf("unknown deploy method: %s", deploy.DeployMethod)
		}

		if err != nil {
			result.Status = "failed"
			result.Error = err.Error()
			log.Printf("[autodeploy] deploy failed: device=%s hostname=%s error=%v", deploy.DeviceID, deploy.Hostname, err)
		} else {
			result.Status = "success"
			log.Printf("[autodeploy] deploy succeeded: device=%s hostname=%s os=%s", deploy.DeviceID, deploy.Hostname, deploy.OSType)
		}
		results = append(results, result)
	}
	return results
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
    # Discover GPO: prefer "Default Domain Policy", fall back to first linked GPO
    $GPOName = "Default Domain Policy"
    try {
        $null = Get-GPO -Name $GPOName -ErrorAction Stop
    } catch {
        # Default Domain Policy not found by name — try GUID, then first linked GPO
        try {
            $GPOName = (Get-GPO -Guid "31B2F340-016D-11D2-945F-00C04FB984F9" -ErrorAction Stop).DisplayName
        } catch {
            $domain = [System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain().Name
            $linked = (Get-GPInheritance -Target "DC=$($domain.Replace('.',',DC='))").GpoLinks
            if ($linked.Count -gt 0) { $GPOName = $linked[0].DisplayName }
            else { throw "No GPOs found linked to domain root" }
        }
    }
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

    # Write startup script (version-stamped, updated if version changes)
    $scriptPath = "$dir\Setup-WinRM.ps1"
    $scriptVersion = "v2"  # Bump this to force re-deploy
    $needsWrite = $true
    if (Test-Path $scriptPath) {
        $existing = Get-Content $scriptPath -Raw -EA SilentlyContinue
        if ($existing -match "# ScriptVersion: $scriptVersion") { $needsWrite = $false }
    }
    if ($needsWrite) {
        $script = @'
# ScriptVersion: v2
$ErrorActionPreference = 'SilentlyContinue'
$logFile = "C:\Windows\Temp\osiris-setup.log"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content $logFile "$ts - Setup-WinRM.ps1 v2 starting"

# Part 1: Enable WinRM and PSRemoting
Enable-PSRemoting -Force -SkipNetworkProfileCheck
Set-Item WSMan:\localhost\Service\Auth\Basic $true
Set-Item WSMan:\localhost\Service\Auth\Negotiate $true
Set-Item WSMan:\localhost\Service\Auth\Kerberos $true
Set-Item WSMan:\localhost\Service\AllowUnencrypted $true
Set-Item WSMan:\localhost\Client\TrustedHosts '*' -Force
netsh advfirewall firewall add rule name="WinRM HTTP" dir=in action=allow protocol=tcp localport=5985
netsh advfirewall firewall add rule name="DCOM RPC" dir=in action=allow protocol=tcp localport=135
netsh advfirewall firewall add rule name="SMB" dir=in action=allow protocol=tcp localport=445
Restart-Service WinRM -Force
Add-Content $logFile "$ts - WinRM configured"

# Part 1.5: Configure Windows Defender exclusions for OsirisCare
try {
    Add-MpPreference -ExclusionPath "C:\OsirisCare" -Force -EA SilentlyContinue
    Add-MpPreference -ExclusionProcess "osiris-agent.exe" -Force -EA SilentlyContinue
    Add-MpPreference -ExclusionProcess "OsirisCareAgent" -Force -EA SilentlyContinue
    Add-Content $logFile "$ts - Defender exclusions configured"
} catch {
    Add-Content $logFile "$ts - Defender exclusion failed: $_"
}

# Part 2: Deploy OsirisCare Agent from NETLOGON
$agentDir = "C:\OsirisCare"
$agentExe = "$agentDir\osiris-agent.exe"
$configFile = "$agentDir\config.json"
$svcName = "OsirisCareAgent"

$svc = Get-Service -Name $svcName -EA SilentlyContinue
if ($svc -and $svc.Status -eq 'Running') {
    Add-Content $logFile "$ts - Agent already running"
} else {
    $netlogonBin = "\\$env:USERDNSDOMAIN\NETLOGON\osiris-agent.exe"
    $netlogonCfg = "\\$env:USERDNSDOMAIN\NETLOGON\osiris-config.json"
    if (Test-Path $netlogonBin) {
        Add-Content $logFile "$ts - Deploying agent from NETLOGON"
        if (-not (Test-Path $agentDir)) { New-Item -ItemType Directory -Force -Path $agentDir | Out-Null }
        Copy-Item -Path $netlogonBin -Destination $agentExe -Force
        if (Test-Path $netlogonCfg) { Copy-Item -Path $netlogonCfg -Destination $configFile -Force }
        if (-not $svc) {
            $binPath = ('"' + $agentExe + '" --config "' + $configFile + '"')
            New-Service -Name $svcName -BinaryPathName $binPath -DisplayName "OsirisCare Agent" -StartupType Automatic -Description "OsirisCare compliance agent" -EA SilentlyContinue
        }
        Start-Service -Name $svcName -EA SilentlyContinue
        Add-Content $logFile "$ts - Agent deployed and started"
    } else {
        Add-Content $logFile "$ts - No agent on NETLOGON ($netlogonBin)"
    }
}
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

    # Also write scripts.ini (legacy CMD startup) — some systems only run this
    $cmdIniPath = "$base\scripts.ini"
    $cmdWrapperPath = "$dir\Setup-OsirisCare.cmd"
    if (-not (Test-Path $cmdIniPath) -or -not (Test-Path $cmdWrapperPath)) {
        # Create CMD wrapper that invokes the PowerShell script
        $crlf = [char]13 + [char]10
        $cmdScript = "@echo off" + $crlf + "powershell.exe -ExecutionPolicy Bypass -NoProfile -File " + [char]34 + "%~dp0Setup-WinRM.ps1" + [char]34
        Set-Content -Path $cmdWrapperPath -Value $cmdScript -Force
        $cmdIni = "[Startup]" + $crlf + "0CmdLine=Setup-OsirisCare.cmd" + $crlf + "0Parameters="
        Set-Content -Path $cmdIniPath -Value $cmdIni -Encoding ASCII -Force
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
		stdout := maputil.String(result.Output, "std_out")
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
		daemon:    ad.daemon,
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
	allTargets := make([]discovery.ADComputer, 0, len(reachable)+len(unreachableDirect))
	allTargets = append(allTargets, reachable...)
	allTargets = append(allTargets, unreachableDirect...)

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

		// Skip if a Go agent is already connected for this host (active agent = no deploy needed)
		if ad.daemon.registry != nil && ad.daemon.registry.HasAgentForHost(hostname) {
			skipped++
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
		err := ad.deployWithFallback(ctx, &ws)
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
			// Deploy succeeded — internal verification confirmed service is running.
			// Now verify the agent actually connects to our gRPC server.
			log.Printf("[autodeploy] Successfully deployed agent to %s — waiting for gRPC registration", hostname)
			ad.mu.Lock()
			ad.deployed[hostname] = time.Now()
			ad.failures[hostname] = 0
			ad.mu.Unlock()
			deployed++

			// Post-deploy: verify gRPC connection within 60 seconds
			go ad.verifyAgentConnection(ctx, hostname)
		}
	}

	log.Printf("[autodeploy] Complete: deployed=%d, skipped=%d, failed=%d",
		deployed, skipped, failed)
}

// verifyAgentConnection waits up to 60 seconds for a deployed agent to register
// via gRPC. If it doesn't connect, runs a diagnostic WinRM check to get the
// agent log and connection status, then logs a warning.
func (ad *autoDeployer) verifyAgentConnection(ctx context.Context, hostname string) {
	registry := ad.daemon.registry
	if registry == nil {
		return
	}

	// Poll for gRPC registration (check every 5 seconds for 60 seconds)
	deadline := time.After(60 * time.Second)
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-deadline:
			// Agent didn't connect — run diagnostic
			log.Printf("[autodeploy] WARNING: %s deployed but did not register via gRPC within 60s", hostname)
			ad.runPostDeployDiagnostic(hostname)
			return
		case <-ticker.C:
			if registry.HasAgentForHost(hostname) {
				log.Printf("[autodeploy] %s confirmed: gRPC registration successful", hostname)
				return
			}
		}
	}
}

// runPostDeployDiagnostic checks the agent's status on a workstation via WinRM
// when it fails to connect to gRPC. Collects service status, agent log tail,
// and network connectivity to help diagnose the issue.
func (ad *autoDeployer) runPostDeployDiagnostic(hostname string) {
	cfg := ad.daemon.config
	if cfg.DomainController == nil || cfg.DCUsername == nil || cfg.DCPassword == nil {
		return
	}

	grpcAddr := cfg.GRPCListenAddr()
	grpcIP := grpcAddr
	if idx := strings.LastIndex(grpcIP, ":"); idx >= 0 {
		grpcIP = grpcIP[:idx]
	}

	diagScript := fmt.Sprintf(""+
		"$ErrorActionPreference = 'SilentlyContinue'\n"+
		"$diag = @{}\n"+
		"$svc = Get-Service -Name \"%s\" -EA SilentlyContinue\n"+
		"$diag.service_status = if ($svc) { $svc.Status.ToString() } else { 'NOT_FOUND' }\n"+
		"$diag.service_start = if ($svc) { $svc.StartType.ToString() } else { 'N/A' }\n"+
		"$proc = Get-Process -Name 'osiris-agent' -EA SilentlyContinue\n"+
		"$diag.process_running = [bool]$proc\n"+
		"$diag.process_id = if ($proc) { $proc.Id } else { $null }\n"+
		"$logPaths = @(\"%s\\agent.log\", \"C:\\ProgramData\\OsirisCare\\agent.log\")\n"+
		"foreach ($lp in $logPaths) {\n"+
		"  if (Test-Path $lp) {\n"+
		"    $diag.log_tail = (Get-Content $lp -Tail 15) -join [char]10\n"+
		"    $diag.log_path = $lp\n"+
		"    break\n"+
		"  }\n"+
		"}\n"+
		"if (-not $diag.log_tail) { $diag.log_tail = 'NO_LOG_FILE' }\n"+
		"try { $diag.config = Get-Content \"%s\\config.json\" -Raw } catch { $diag.config = 'NOT_FOUND' }\n"+
		"try {\n"+
		"  $tcp = New-Object System.Net.Sockets.TcpClient\n"+
		"  $tcp.Connect('%s', 50051)\n"+
		"  $diag.grpc_reachable = $true\n"+
		"  $tcp.Close()\n"+
		"} catch {\n"+
		"  $diag.grpc_reachable = $false\n"+
		"  $diag.grpc_error = $_.Exception.Message\n"+
		"}\n"+
		"try {\n"+
		"  $fw = Get-NetFirewallProfile -Profile Domain,Private | Select-Object Name,Enabled\n"+
		"  $diag.firewall = ($fw | ForEach-Object { $_.Name + '=' + $_.Enabled }) -join ','\n"+
		"} catch { $diag.firewall = 'QUERY_FAILED' }\n"+
		"$diag | ConvertTo-Json -Depth 2 -Compress\n",
		agentServiceName, agentInstallDir, agentInstallDir, grpcIP)

	// Try direct WinRM first, then DC proxy
	target := ad.buildTargetByHostname(hostname)
	if target != nil {
		res := ad.daemon.winrmExec.Execute(target, diagScript, "AGENT-DIAG", "autodeploy", 30, 0, 10.0, nil)
		if res.Success {
			stdout := maputil.String(res.Output, "std_out")
			log.Printf("[autodeploy] [%s] Post-deploy diagnostic: %s", hostname, strings.TrimSpace(stdout))
			return
		}
	}

	// Fallback: run via DC proxy
	dcTarget := ad.dcTarget()
	proxyScript := fmt.Sprintf(""+
		"$Computer = \"%s\"\n"+
		"$secPass = ConvertTo-SecureString \"%s\" -AsPlainText -Force\n"+
		"$cred = New-Object PSCredential(\"%s\", $secPass)\n"+
		"try {\n"+
		"  $session = New-PSSession -ComputerName $Computer -Credential $cred -Authentication Negotiate -ErrorAction Stop\n"+
		"  $result = Invoke-Command -Session $session -ScriptBlock { %s } -ErrorAction Stop\n"+
		"  Remove-PSSession $session -EA SilentlyContinue\n"+
		"  $result\n"+
		"} catch {\n"+
		"  @{ error = $_.Exception.Message } | ConvertTo-Json -Compress\n"+
		"}\n",
		hostname, escapePSString(*cfg.DCPassword), *cfg.DCUsername, diagScript)

	dcRes := ad.daemon.winrmExec.Execute(dcTarget, proxyScript, "AGENT-DIAG-DC", "autodeploy", 45, 0, 10.0, nil)
	if dcRes.Success {
		stdout := maputil.String(dcRes.Output, "std_out")
		log.Printf("[autodeploy] [%s] Post-deploy diagnostic (via DC): %s", hostname, strings.TrimSpace(stdout))
	} else {
		log.Printf("[autodeploy] [%s] Post-deploy diagnostic failed: %s", hostname, dcRes.Error)
	}
}

// buildTargetByHostname creates a WinRM target for a hostname using credential lookup.
// Tries hostname, then resolved IP for credential lookup.
func (ad *autoDeployer) buildTargetByHostname(hostname string) *winrm.Target {
	cfg := ad.daemon.config

	username := ""
	password := ""
	connectHost := hostname

	if wt, ok := ad.daemon.LookupWinTarget(hostname); ok && wt.Role != "domain_admin" {
		username = wt.Username
		password = wt.Password
		connectHost = wt.Hostname
	} else {
		// Try resolving hostname to IP for credential lookup
		if addrs, err := net.DefaultResolver.LookupHost(context.Background(), hostname); err == nil {
			for _, addr := range addrs {
				if wt2, ok2 := ad.daemon.LookupWinTarget(addr); ok2 && wt2.Role != "domain_admin" {
					username = wt2.Username
					password = wt2.Password
					connectHost = addr
					break
				}
			}
		}
		// Final fallback: DC credentials
		if username == "" {
			if cfg.DCUsername != nil && cfg.DCPassword != nil {
				username = *cfg.DCUsername
				password = *cfg.DCPassword
			} else {
				return nil
			}
		}
	}

	ws := ad.daemon.probeWinRM(connectHost)
	return &winrm.Target{
		Hostname:  connectHost,
		Port:      ws.Port,
		Username:  username,
		Password:  password,
		UseSSL:    ws.UseSSL,
		VerifySSL: false,
	}
}

// deployWithFallback tries to deploy to a workstation using the fallback chain:
// 1. Direct WinRM (if not known to need proxy) — single probe, no retries
// 2. DC Proxy via Invoke-Command (Kerberos/Negotiate)
//
// IMPORTANT: The direct WinRM probe uses exactly 1 attempt (no retries) to
// avoid triggering domain account lockout (default threshold: 5 attempts).
// Each 401 failure counts against the lockout counter.
func (ad *autoDeployer) deployWithFallback(ctx context.Context, ws *discovery.ADComputer) error {
	hostname := ws.Hostname

	// Check if we already know this host needs DC proxy
	_, knownNeedsProxy := ad.needsProxy.Load(hostname)

	// Fallback 1: Try direct WinRM — single probe with 0 retries
	// Skip status check (it would be another NTLM attempt). The probe
	// itself tells us if direct WinRM works. We avoid wasting lockout attempts.
	if !knownNeedsProxy {
		target := ad.buildTarget(ws)
		if target != nil {
			// Probe: check service status AND version + config
			grpcAddr := ad.daemon.config.GRPCListenAddr()
			expectedVersion := readVersionFile(filepath.Join(ad.daemon.config.StateDir, "agent"))
			probeScript := fmt.Sprintf(`
$svc = Get-Service -Name "%s" -EA SilentlyContinue
if (-not $svc -or $svc.Status -ne "Running") { "NOT_RUNNING"; exit }
$cfg = $null
try { $cfg = Get-Content "%s\config.json" -Raw -EA Stop | ConvertFrom-Json } catch {}
$ver = $null
try { $out = & "%s\%s" --version 2>&1; if ($out -match "(\d+\.\d+\.\d+)") { $ver = $matches[1] } } catch {}
if ($ver -eq "%s" -and $cfg.appliance_addr -eq "%s") { "OK" } else { "STALE|ver=$ver|addr=$($cfg.appliance_addr)" }
`, agentServiceName, agentInstallDir, agentInstallDir, agentBinaryName, expectedVersion, grpcAddr)
			probeResult := ad.daemon.winrmExec.Execute(target,
				probeScript,
				"AGENT-PROBE", "autodeploy", 20, 0, 10.0, nil)

			switch {
			case probeResult.Success:
				stdout := maputil.String(probeResult.Output, "std_out")
				trimmed := strings.TrimSpace(stdout)
				if trimmed == "OK" {
					return nil // Already deployed, correct version and config
				}
				if trimmed == "NOT_RUNNING" {
					log.Printf("[autodeploy] [%s] Agent not running, deploying", hostname)
				} else {
					log.Printf("[autodeploy] [%s] Agent needs update: %s", hostname, trimmed)
				}
				// Direct WinRM works — proceed with full deploy
				err := ad.deployAgentDirect(ctx, target, ws)
				if err == nil {
					return nil
				}
				log.Printf("[autodeploy] [%s] Direct deploy failed: %v — trying DC proxy", hostname, err)
			case strings.Contains(probeResult.Error, "401"):
				log.Printf("[autodeploy] [%s] Direct WinRM auth failed (401) — switching to DC proxy", hostname)
				ad.needsProxy.Store(hostname, true)
			default:
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

	// Probe DC WinRM port once: prefer 5986/SSL, fall back to 5985/HTTP
	if !ad.dcSSLProbed {
		ad.dcSSLProbed = true
		dc := *cfg.DomainController
		dialer := net.Dialer{Timeout: 3 * time.Second}
		conn, err := dialer.DialContext(context.Background(), "tcp", fmt.Sprintf("%s:%d", dc, 5986))
		if err == nil {
			conn.Close()
			ad.dcUseSSL = true
			ad.dcPort = 5986
			log.Printf("[autodeploy] DC %s: WinRM HTTPS (5986) available", dc)
		} else {
			conn2, err2 := dialer.DialContext(context.Background(), "tcp", fmt.Sprintf("%s:%d", dc, 5985))
			if err2 == nil {
				conn2.Close()
				ad.dcUseSSL = false
				ad.dcPort = 5985
				log.Printf("[autodeploy] DC %s: WinRM HTTPS unavailable, using HTTP (5985)", dc)
			} else {
				// Neither port open — default to SSL, will fail with useful error
				ad.dcUseSSL = true
				ad.dcPort = 5986
				log.Printf("[autodeploy] DC %s: neither WinRM port reachable, defaulting to 5986", dc)
			}
		}
	}

	return &winrm.Target{
		Hostname:  *cfg.DomainController,
		Port:      ad.dcPort,
		Username:  *cfg.DCUsername,
		Password:  *cfg.DCPassword,
		UseSSL:    ad.dcUseSSL,
		VerifySSL: false,
	}
}

// buildTarget creates a WinRM target for direct workstation connection.
// Probes 5986 (HTTPS) first, then falls back to 5985 (HTTP).
func (ad *autoDeployer) buildTarget(ws *discovery.ADComputer) *winrm.Target {
	cfg := ad.daemon.config

	var hostname string
	switch {
	case ws.IPAddress != nil && *ws.IPAddress != "":
		hostname = *ws.IPAddress
	case ws.FQDN != "":
		hostname = ws.FQDN
	default:
		hostname = ws.Hostname
	}

	// Look up per-workstation credentials first (e.g. local_admin, winrm type)
	username := ""
	password := ""
	if wt, ok := ad.daemon.LookupWinTarget(hostname); ok && wt.Role != "domain_admin" {
		username = wt.Username
		password = wt.Password
		log.Printf("[autodeploy] [%s] Using workstation-specific credentials (role=%s, user=%s)", ws.Hostname, wt.Role, username)
	} else if cfg.DCUsername != nil && cfg.DCPassword != nil {
		username = *cfg.DCUsername
		password = *cfg.DCPassword
	} else {
		return nil
	}

	// Probe workstation: prefer HTTPS, fall back to HTTP
	port := 5986
	useSSL := true
	dialer := net.Dialer{Timeout: 2 * time.Second}
	conn, err := dialer.DialContext(context.Background(), "tcp", fmt.Sprintf("%s:%d", hostname, 5986))
	if err == nil {
		conn.Close()
	} else {
		conn2, err2 := dialer.DialContext(context.Background(), "tcp", fmt.Sprintf("%s:%d", hostname, 5985))
		if err2 == nil {
			conn2.Close()
			port = 5985
			useSSL = false
		}
	}

	return &winrm.Target{
		Hostname:  hostname,
		Port:      port,
		Username:  username,
		Password:  password,
		UseSSL:    useSSL,
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

	stdout := maputil.String(result.Output, "std_out")
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

	stdout := maputil.String(result.Output, "std_out")
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
	ad.daemon.healIncident(context.Background(), &req)
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
// Uses HTTP download from appliance file server instead of chunked WinRM transfer.
func (ad *autoDeployer) deployAgentDirect(ctx context.Context, target *winrm.Target, ws *discovery.ADComputer) error {
	grpcAddr := ad.daemon.config.GRPCListenAddr()
	hostname := ws.Hostname

	// Determine appliance IP for download URL (from gRPC listen address)
	applianceIP := ad.daemon.config.GRPCListenAddr()
	if idx := strings.LastIndex(applianceIP, ":"); idx >= 0 {
		applianceIP = applianceIP[:idx]
	}
	if applianceIP == "" || applianceIP == "0.0.0.0" {
		return fmt.Errorf("appliance IP not configured")
	}
	downloadURL := fmt.Sprintf("http://%s:8090/agent/%s", applianceIP, agentBinaryName)

	// Step 1: Create install directory
	log.Printf("[autodeploy] [%s] Direct: Step 1/4 Creating directory", hostname)
	mkdirResult := ad.daemon.winrmExec.Execute(target,
		fmt.Sprintf(`New-Item -ItemType Directory -Force -Path "%s" | Out-Null; "OK"`, agentInstallDir),
		"AGENT-DEPLOY-MKDIR", "autodeploy", 30, 0, 10.0, nil)
	if !mkdirResult.Success {
		return fmt.Errorf("mkdir failed: %s", mkdirResult.Error)
	}

	// Step 2: Download binary via HTTP from appliance file server
	log.Printf("[autodeploy] [%s] Direct: Step 2/4 Downloading binary from %s", hostname, downloadURL)
	dlScript := fmt.Sprintf(`
$dest = "%s\%s"
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $wc = New-Object System.Net.WebClient
    $wc.DownloadFile("%s", $dest)
    $fi = Get-Item $dest
    @{ Success = $true; Size = $fi.Length; Path = $dest } | ConvertTo-Json -Compress
} catch {
    @{ Success = $false; Error = $_.Exception.Message } | ConvertTo-Json -Compress
}`, agentInstallDir, agentBinaryName, downloadURL)
	dlResult := ad.daemon.winrmExec.Execute(target,
		dlScript, "AGENT-DEPLOY-DL", "autodeploy", 120, 0, 10.0, nil)
	if !dlResult.Success {
		stderr := maputil.String(dlResult.Output, "std_err")
		return fmt.Errorf("download failed: err=%s stderr=%s", dlResult.Error, stderr)
	}
	stdout := maputil.String(dlResult.Output, "std_out")
	log.Printf("[autodeploy] [%s] Direct: Download result: %s", hostname, strings.TrimSpace(stdout))

	// Check if the PowerShell download script reported failure — try NETLOGON UNC fallback
	if strings.Contains(stdout, `"Success":false`) || strings.Contains(stdout, `"Success": false`) {
		log.Printf("[autodeploy] [%s] HTTP download failed, trying NETLOGON UNC copy", hostname)

		// Stage binary to NETLOGON first (idempotent, cached per version)
		if err := ad.stageAgentToNETLOGON(ctx); err != nil {
			return fmt.Errorf("HTTP download failed and NETLOGON stage failed: %w", err)
		}

		// Copy from NETLOGON share — domain-joined machines can always read this
		// Use DC IP directly to bypass DNS/DFS caching issues
		dcIP := *ad.daemon.config.DomainController
		uncScript := fmt.Sprintf(`
$dest = "%s\%s"
try {
    $src = "\\%s\NETLOGON\%s"
    # Stop service before overwriting binary (locked file)
    Stop-Service -Name "%s" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    # Clear SMB client cache for this path
    net use "\\%s\NETLOGON" /delete 2>$null
    Copy-Item -Path $src -Destination $dest -Force
    $fi = Get-Item $dest
    @{ Success = $true; Size = $fi.Length; Path = $dest; Method = "NETLOGON" } | ConvertTo-Json -Compress
} catch {
    @{ Success = $false; Error = $_.Exception.Message; Method = "NETLOGON" } | ConvertTo-Json -Compress
}`, agentInstallDir, agentBinaryName, dcIP, agentBinaryName, agentServiceName, dcIP)
		uncResult := ad.daemon.winrmExec.Execute(target,
			uncScript, "AGENT-DEPLOY-UNC", "autodeploy", 60, 0, 10.0, nil)
		if !uncResult.Success {
			return fmt.Errorf("both HTTP and NETLOGON download failed: %s", strings.TrimSpace(stdout))
		}
		uncStdout := maputil.String(uncResult.Output, "std_out")
		log.Printf("[autodeploy] [%s] Direct: NETLOGON copy result: %s", hostname, strings.TrimSpace(uncStdout))
		if strings.Contains(uncStdout, `"Success":false`) {
			log.Printf("[autodeploy] [%s] NETLOGON copy failed, falling back to WinRM base64 transfer", hostname)
			goto winrmB64Transfer
		}

		// Verify size matches expected (SYSVOL can serve stale cached files)
		agentData, loadErr := ad.loadAgentBinary()
		if loadErr == nil {
			rawBytes, _ := base64.StdEncoding.DecodeString(agentData)
			expectedSize := len(rawBytes)
			// Parse size from UNC result
			var uncParsed struct{ Size int }
			if json.Unmarshal([]byte(uncStdout), &uncParsed) == nil && uncParsed.Size != expectedSize {
				log.Printf("[autodeploy] [%s] NETLOGON copy size mismatch: got %d, expected %d — falling back to WinRM base64", hostname, uncParsed.Size, expectedSize)
				goto winrmB64Transfer
			}
		}
		goto downloadDone
	}

winrmB64Transfer:
	{
		// Last resort: push binary via WinRM base64 chunks
		log.Printf("[autodeploy] [%s] Direct: WinRM base64 transfer (slow but reliable)", hostname)
		b64Data, err := ad.loadAgentBinary()
		if err != nil {
			return fmt.Errorf("load agent binary for b64 transfer: %w", err)
		}

		// Stop service first to unlock the binary
		ad.daemon.winrmExec.Execute(target,
			fmt.Sprintf(`Stop-Service -Name "%s" -Force -EA SilentlyContinue; Start-Sleep -Seconds 1; "OK"`, agentServiceName),
			"AGENT-STOP", "autodeploy", 15, 0, 10.0, nil)

		// Write base64 in chunks via PowerShell file append.
		// WinRM command size limit is ~32KB so use 20KB chunks.
		chunkSize := 20000
		b64File := agentInstallDir + `\agent.b64`
		totalChunks := (len(b64Data) + chunkSize - 1) / chunkSize
		for i := 0; i < len(b64Data); i += chunkSize {
			end := i + chunkSize
			if end > len(b64Data) {
				end = len(b64Data)
			}
			chunk := b64Data[i:end]
			chunkNum := i/chunkSize + 1

			// Use single-quoted here-string to avoid PowerShell variable expansion
			var chunkScript string
			if i == 0 {
				chunkScript = fmt.Sprintf("[IO.File]::WriteAllText('%s', '%s'); 'OK'", b64File, chunk)
			} else {
				chunkScript = fmt.Sprintf("[IO.File]::AppendAllText('%s', '%s'); 'OK'", b64File, chunk)
			}
			res := ad.daemon.winrmExec.Execute(target,
				chunkScript, "AGENT-B64-CHUNK", "autodeploy", 30, 0, 10.0, nil)
			if !res.Success {
				stderr := maputil.String(res.Output, "std_err")
				return fmt.Errorf("b64 chunk %d/%d write failed at offset %d: err=%s stderr=%s", chunkNum, totalChunks, i, res.Error, stderr)
			}
			if chunkNum%50 == 0 || chunkNum == totalChunks {
				log.Printf("[autodeploy] [%s] Direct: Base64 chunk %d/%d", hostname, chunkNum, totalChunks)
			}
		}
		log.Printf("[autodeploy] [%s] Direct: Base64 transfer complete (%d chars in %d chunks)", hostname, len(b64Data), totalChunks)
	}

downloadDone:

	// Step 3: Write config + install service
	log.Printf("[autodeploy] [%s] Direct: Step 3/4 Writing config + installing service", hostname)
	if err := ad.writeConfigToTarget(target, grpcAddr); err != nil {
		return err
	}
	if err := ad.installServiceOnTarget(target); err != nil {
		return err
	}

	// Step 4: Verify
	log.Printf("[autodeploy] [%s] Direct: Step 4/4 Verifying", hostname)
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

	// Version-aware guard: re-stage when agent binary version changes
	currentVersion := readVersionFile(filepath.Join(ad.daemon.config.StateDir, "agent"))
	if staged, ok := netlogonStaged.Load(dc); ok {
		if s, ok := staged.(string); ok && s == currentVersion {
			return nil
		}
		log.Printf("[autodeploy] Agent version changed to %s, re-staging to NETLOGON", currentVersion)
		// Invalidate the binary cache so we re-read from disk
		ad.mu.Lock()
		ad.agentLoaded = false
		ad.agentB64 = ""
		ad.mu.Unlock()
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

	stdout := maputil.String(result.Output, "std_out")
	log.Printf("[autodeploy] Agent staged to NETLOGON: %s", stdout)

	// Check if the script reported success
	if strings.Contains(stdout, `"Success":false`) {
		return fmt.Errorf("NETLOGON stage script error: %s", stdout)
	}

	// Also stage config.json to NETLOGON so the GPO startup script can use it
	grpcAddr := ad.daemon.config.GRPCListenAddr()
	configJSON := fmt.Sprintf(`{"appliance_addr":"%s","check_interval":300,"data_dir":"C:\\ProgramData\\OsirisCare"}`, grpcAddr)
	configScript := fmt.Sprintf(`
$ErrorActionPreference = 'Stop'
try {
    $netlogon = (Get-SmbShare -Name NETLOGON -ErrorAction Stop).Path
    $dest = Join-Path $netlogon "osiris-config.json"
    Set-Content -Path $dest -Value '%s' -Force
    @{ Success = $true; Path = $dest } | ConvertTo-Json -Compress
} catch {
    @{ Success = $false; Error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`, strings.ReplaceAll(configJSON, "'", "''"))

	configResult := ad.daemon.winrmExec.Execute(dcTarget, configScript, "NETLOGON-CONFIG", "autodeploy", 30, 1, 15.0, nil)
	if configResult.Success {
		configStdout := maputil.String(configResult.Output, "std_out")
		log.Printf("[autodeploy] Config staged to NETLOGON: %s", configStdout)
	} else {
		log.Printf("[autodeploy] Config staging failed (non-fatal): %s", configResult.Error)
	}

	netlogonStaged.Store(dc, currentVersion)
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
func (ad *autoDeployer) deployAgentViaDC(ctx context.Context, ws *discovery.ADComputer) error {
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

    # Step 3: Copy binary from DC local NETLOGON to workstation via -ToSession (avoids Kerberos double-hop)
    $Result.Step = "copy"
    $netlogonLocal = (Get-SmbShare -Name NETLOGON -ErrorAction Stop).Path
    $localBinPath = Join-Path $netlogonLocal "%s"
    Copy-Item -Path $localBinPath -Destination "%s\%s" -ToSession $session -Force -ErrorAction Stop

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

	stdout := maputil.String(deployResult.Output, "std_out")
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
	daemon    *Daemon
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

	ws := e.daemon.probeWinRM(hostname)
	target := &winrm.Target{
		Hostname:  hostname,
		Port:      ws.Port,
		Username:  username,
		Password:  password,
		UseSSL:    ws.UseSSL,
		VerifySSL: false,
	}

	result := e.winrmExec.Execute(target, script, "AD-ENUM", "discovery", timeout, 1, float64(timeout), nil)
	if !result.Success {
		return "", fmt.Errorf("%s", result.Error)
	}
	stdout := maputil.String(result.Output, "std_out")
	return stdout, nil
}
