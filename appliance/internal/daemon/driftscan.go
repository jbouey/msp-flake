package daemon

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/osiriscare/appliance/internal/evidence"
	"github.com/osiriscare/appliance/internal/grpcserver"
	"github.com/osiriscare/appliance/internal/maputil"
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
	driftScanInterval      = 15 * time.Minute // How often to scan all targets
	driftRescanInterval    = 5 * time.Minute  // Faster rescan when drift was found on last cycle
)

// clampScanInterval enforces safety bounds on scan intervals.
// Prevents misconfiguration from setting intervals too low (DoS) or too high (stale data).
func clampScanInterval(d time.Duration) time.Duration {
	const minInterval = 5 * time.Minute  // 300 seconds
	const maxInterval = 1 * time.Hour    // 3600 seconds
	if d < minInterval {
		log.Printf("[driftscan] Scan interval %v clamped to minimum %v", d, minInterval)
		return minInterval
	}
	if d > maxInterval {
		log.Printf("[driftscan] Scan interval %v clamped to maximum %v", d, maxInterval)
		return maxInterval
	}
	return d
}

// driftScanner periodically checks Windows and Linux targets for security drift.
// Windows: firewall disabled, Defender stopped, rogue users, rogue scheduled tasks,
// critical services stopped, BitLocker, SMB signing, etc.
// Linux: firewall rules, SSH hardening, failed services, disk space, SUID, etc.
type driftScanner struct {
	svc    *Services // interfaces for decoupled access
	daemon *Daemon   // for healing pipeline, evidence, deployer, etc.

	mu           sync.Mutex
	lastScanTime time.Time
	running      int32 // atomic guard

	linuxMu           sync.Mutex
	lastLinuxScanTime time.Time
	linuxRunning      int32 // atomic guard

	// credential_ip_mismatch cooldown: 1-hour per hostname to avoid spamming
	// the dashboard on every 15-min scan cycle while a credential is stale.
	ipMismatchMu       sync.Mutex
	ipMismatchCooldown map[string]time.Time // hostname -> last reported

	// Adaptive scan interval: hosts that had drift on the last scan cycle
	// trigger a faster rescan (5 min instead of 15) to verify healing worked.
	lastDriftHostsMu sync.Mutex
	lastDriftHosts   map[string]bool // hostname -> had drift on last scan
}

func newDriftScanner(svc *Services, d *Daemon) *driftScanner {
	return &driftScanner{
		svc:                svc,
		daemon:             d,
		ipMismatchCooldown: make(map[string]time.Time),
		lastDriftHosts:     make(map[string]bool),
	}
}

// effectiveInterval returns driftRescanInterval if any host had drift on the
// last scan, otherwise driftScanInterval. This lets the scanner verify healing
// worked sooner without per-host timers.
func (ds *driftScanner) effectiveInterval() time.Duration {
	ds.lastDriftHostsMu.Lock()
	defer ds.lastDriftHostsMu.Unlock()
	for _, hasDrift := range ds.lastDriftHosts {
		if hasDrift {
			return driftRescanInterval
		}
	}
	return driftScanInterval
}

// updateLastDriftHosts merges scan results into the drift host map.
// Hosts with findings are marked true; scanned hosts with no findings are cleared.
// This supports concurrent Windows + Linux scans updating the same map safely.
func (ds *driftScanner) updateLastDriftHosts(findings []driftFinding, scannedHosts []string) {
	driftSet := make(map[string]bool)
	for _, f := range findings {
		driftSet[f.Hostname] = true
	}

	ds.lastDriftHostsMu.Lock()
	// Clear hosts that were scanned clean this cycle
	for _, h := range scannedHosts {
		if !driftSet[h] {
			delete(ds.lastDriftHosts, h)
		}
	}
	// Mark hosts that had drift
	for h := range driftSet {
		ds.lastDriftHosts[h] = true
	}
	count := len(ds.lastDriftHosts)
	ds.lastDriftHostsMu.Unlock()

	if count > 0 {
		log.Printf("[driftscan] Adaptive interval: %d host(s) with drift, next scan in %v",
			count, driftRescanInterval)
	}
}

// isCheckDisabled returns true if the given check type has been disabled in site drift config.
func (ds *driftScanner) isCheckDisabled(checkType string) bool {
	return ds.svc.Checks.IsDisabled(checkType)
}

// shouldSuppressIPMismatch returns true if a credential_ip_mismatch drift report
// for the given hostname should be suppressed (still within the 1-hour cooldown).
func (ds *driftScanner) shouldSuppressIPMismatch(hostname string) bool {
	ds.ipMismatchMu.Lock()
	defer ds.ipMismatchMu.Unlock()

	last, exists := ds.ipMismatchCooldown[hostname]
	if exists && time.Since(last) < time.Hour {
		return true
	}
	ds.ipMismatchCooldown[hostname] = time.Now()
	return false
}

// ForceScan runs both Windows and Linux drift scans immediately,
// bypassing the interval check. Called from run_drift fleet order handler.
func (ds *driftScanner) ForceScan(ctx context.Context) map[string]interface{} {
	log.Printf("[driftscan] Force scan triggered by fleet order")

	windowsDone := false
	linuxDone := false

	// Run Windows scan if configured
	cfg := ds.svc.Config
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

	interval := clampScanInterval(ds.effectiveInterval())
	if !first && since < interval {
		return
	}

	log.Printf("[driftscan] Starting drift scan cycle (interval=%v)", interval)
	ds.mu.Lock()
	ds.lastScanTime = time.Now()
	ds.mu.Unlock()

	ds.scanWindowsTargets(ctx)
}

// scanWindowsTargets enumerates known Windows targets and checks each for drift.
func (ds *driftScanner) scanWindowsTargets(ctx context.Context) {
	cfg := ds.svc.Config
	if cfg.DomainController == nil || *cfg.DomainController == "" {
		return
	}
	if cfg.DCUsername == nil || cfg.DCPassword == nil {
		return
	}

	// Build target list: DC + any deployed workstations
	dcWS := ds.svc.Targets.ProbeWinRMPort(*cfg.DomainController)
	targets := []scanTarget{
		{
			hostname: *cfg.DomainController,
			label:    "DC",
			target: &winrm.Target{
				Hostname:  *cfg.DomainController,
				Port:      dcWS.Port,
				Username:  *cfg.DCUsername,
				Password:  *cfg.DCPassword,
				UseSSL:    dcWS.UseSSL,
				VerifySSL: true, // TOFU cert pinning via CertPinStore
			},
		},
	}

	// Build WinRM scan targets from two sources:
	// 1. Deployed hosts from autodeploy tracker (workstations + servers)
	// 2. Credential-delivered Windows targets (fallback for non-deployed hosts)
	// Push-first: skip any host with a connected Go agent.
	scanned := map[string]bool{*cfg.DomainController: true} // DC already added above

	if ds.daemon.deployer != nil {
		ds.daemon.deployer.mu.Lock()
		for hostname := range ds.daemon.deployer.deployed {
			if scanned[hostname] {
				continue
			}
			// If a Go agent is actively heartbeating for this host, skip WinRM — push covers it.
			// Stale/dead agents (no heartbeat in 10min) fall back to WinRM pull scan.
			if ds.svc.Registry != nil && ds.svc.Registry.HasActiveAgentForHost(hostname, agentStaleTimeout) {
				log.Printf("[driftscan] Skipping WinRM for %s (covered by active Go agent)", hostname)
				scanned[hostname] = true
				continue
			}
			if ds.svc.Registry != nil && ds.svc.Registry.HasAgentForHost(hostname) {
				log.Printf("[driftscan] Agent %s registered but stale — falling back to WinRM pull scan", hostname)
			}
			ws := ds.svc.Targets.ProbeWinRMPort(hostname)
			// Use per-workstation credentials if available.
			// Try hostname first, then resolved IP (credentials may be stored under IP).
			wsUser := *cfg.DCUsername
			wsPass := *cfg.DCPassword
			connectHost := hostname
			if wt, ok := ds.svc.Targets.LookupWinTarget(hostname); ok && wt.Role != "domain_admin" {
				wsUser = wt.Username
				wsPass = wt.Password
				connectHost = wt.Hostname // use the IP from credential if available
			} else {
				// Resolve hostname to IP and retry lookup.
				// Uses AD DNS fallback when system resolver can't find AD hostnames.
				if resolved, err := resolveHostnameWithFallback(ctx, hostname, cfg.ADDNSServer); err == nil {
					if wt2, ok2 := ds.svc.Targets.LookupWinTarget(resolved); ok2 && wt2.Role != "domain_admin" {
						wsUser = wt2.Username
						wsPass = wt2.Password
						connectHost = resolved
					} else {
						connectHost = resolved
					}
				}
			}
			targets = append(targets, scanTarget{
				hostname: hostname,
				label:    "WS",
				target: &winrm.Target{
					Hostname:  connectHost,
					Port:      ws.Port,
					Username:  wsUser,
					Password:  wsPass,
					UseSSL:    ws.UseSSL,
					VerifySSL: true, // TOFU cert pinning via CertPinStore
				},
			})
			scanned[hostname] = true
		}
		ds.daemon.deployer.mu.Unlock()
	}

	// Add credential-delivered Windows targets not yet covered (servers, non-deployed hosts).
	// These are hosts with WinRM credentials but no Go agent — WinRM pull scan is the only path.
	for hostname, wt := range ds.svc.Targets.GetWinTargets() {
		if scanned[hostname] || wt.Role == "domain_admin" {
			continue
		}
		if ds.svc.Registry != nil && ds.svc.Registry.HasActiveAgentForHost(hostname, agentStaleTimeout) {
			continue
		}
		if ds.svc.Registry != nil && ds.svc.Registry.HasAgentForHost(hostname) {
			log.Printf("[driftscan] Credential target %s has stale agent — WinRM fallback scan", hostname)
		}
		connectHost := hostname
		// DNS re-resolution: if the credential is stored by hostname and the
		// connect address looks like a stale IP, check if DNS has a newer address.
		// We log the mismatch but don't auto-update (security decision for partner).
		if newIP := dnsReResolve(ctx, hostname, wt.Hostname); newIP != "" {
			log.Printf("[driftscan] Credential %q stored IP %s differs from DNS %s — using stored IP (partner must update credentials to switch)",
				hostname, wt.Hostname, newIP)
		}
		if wt.Hostname != "" {
			connectHost = wt.Hostname
		}
		// Netscan IP cross-reference: check if netscan discovered this host at a
		// different IP than the credential stores. If so, report the mismatch as a
		// drift finding and use the discovered IP for the scan attempt (the stale
		// credential IP is likely unreachable after a DHCP change).
		if discoveredIP, found := ds.lookupDeviceIP(hostname); found && discoveredIP != connectHost {
			log.Printf("[driftscan] Credential for %s points to %s but device discovered at %s",
				hostname, connectHost, discoveredIP)
			// Report credential_ip_mismatch drift finding with 1-hour cooldown
			if !ds.isCheckDisabled("credential_ip_mismatch") && !ds.shouldSuppressIPMismatch(hostname) {
				mismatchFinding := driftFinding{
					Hostname:     hostname,
					CheckType:    "credential_ip_mismatch",
					Expected:     fmt.Sprintf("Credential IP %s matches device", connectHost),
					Actual:       fmt.Sprintf("Device discovered at %s via ARP/netscan", discoveredIP),
					HIPAAControl: "164.312(a)(1)",
					Severity:     "medium",
					Details: map[string]string{
						"hostname":      hostname,
						"credential_ip": connectHost,
						"discovered_ip": discoveredIP,
						"platform":      "windows",
						"source":        "netscan_cross_ref",
					},
				}
				ds.reportDrift(&mismatchFinding)
			}
			// Use the discovered IP for the scan attempt
			connectHost = discoveredIP
		}
		ws := ds.svc.Targets.ProbeWinRMPort(connectHost)
		targets = append(targets, scanTarget{
			hostname: hostname,
			label:    "SRV",
			target: &winrm.Target{
				Hostname:  connectHost,
				Port:      ws.Port,
				Username:  wt.Username,
				Password:  wt.Password,
				UseSSL:    ws.UseSSL,
				VerifySSL: true, // TOFU cert pinning via CertPinStore
			},
		})
	}

	// Mesh filter: only scan targets this appliance owns on the hash ring.
	// Single appliance (no peers) owns everything — no filtering applied.
	// Key is canonicalized to IP to prevent hostname/IP hash divergence between appliances.
	if ds.svc.Mesh != nil && ds.svc.Mesh.PeerCount() > 0 {
		var owned []scanTarget
		for _, t := range targets {
			key := t.hostname
			if t.target != nil {
				key = t.target.Hostname
			}
			// Canonicalize: if not already an IP, resolve to one
			if net.ParseIP(key) == nil {
				if addrs, err := net.LookupHost(key); err == nil && len(addrs) > 0 {
					key = addrs[0]
				}
			}
			if ds.svc.Mesh.OwnsTarget(key) {
				owned = append(owned, t)
			}
		}
		if len(owned) < len(targets) {
			log.Printf("[driftscan] Mesh filter: %d/%d Windows targets owned by this appliance", len(owned), len(targets))
		}
		targets = owned
	}

	var allFindings []driftFinding
	var scannedHosts []string
	unreachableCount := 0

	for _, t := range targets {
		select {
		case <-ctx.Done():
			return
		default:
		}

		scannedHosts = append(scannedHosts, t.hostname)
		drifts := ds.checkTarget(ctx, t)
		allFindings = append(allFindings, drifts...)

		// Track successful scans for credential staleness detection.
		// A scan is successful if it didn't produce a device_unreachable finding.
		wasUnreachable := false
		for i := range drifts {
			if drifts[i].CheckType == "device_unreachable" {
				wasUnreachable = true
				unreachableCount++
				// Feature 2: If unreachable host is domain-joined, log GPO hint.
				// The next autodeploy cycle's ensureWinRMViaGPO() will handle enablement.
				if ds.daemon.deployer != nil {
					adHosts := ds.svc.Targets.GetADHostnames()
					if adHosts[t.hostname] || adHosts[drifts[i].Hostname] {
						log.Printf("[driftscan] Unreachable host %s is domain-joined — GPO should enable WinRM on next boot", t.hostname)
					}
				}
			}
		}

		// Subnet-dark detection: if ≥80% of targets are unreachable (and ≥3 total),
		// suppress individual device_unreachable incidents to prevent alert storms.
		// Report a single "subnet_dark" finding instead.
		subnetDark := len(targets) >= 3 && unreachableCount*100/len(targets) >= 80

		for i := range drifts {
			if drifts[i].CheckType == "device_unreachable" && subnetDark {
				// Suppress individual unreachable — will be replaced by aggregate below
				continue
			}
			ds.reportDrift(&drifts[i])
		}
		if !wasUnreachable {
			ds.daemon.state.RecordSuccessfulScan(t.hostname)
		}
	}

	// Report a single aggregate finding when subnet appears dark
	if len(targets) >= 3 && unreachableCount*100/len(targets) >= 80 {
		log.Printf("[driftscan] SUBNET DARK: %d/%d targets unreachable — suppressing individual incidents",
			unreachableCount, len(targets))
		ds.reportDrift(&driftFinding{
			Hostname:     "network",
			CheckType:    "subnet_dark",
			Expected:     fmt.Sprintf("%d targets responding", len(targets)),
			Actual:       fmt.Sprintf("%d/%d targets unreachable — possible network infrastructure issue", unreachableCount, len(targets)),
			HIPAAControl: "164.312(a)(1)",
		})
	}

	// Feature 1: Check for stale credentials — hosts that haven't had a
	// successful scan in 7+ days (or never scanned at all).
	ds.checkStaleCredentials(targets)

	log.Printf("[driftscan] Scan complete: targets=%d, drifts_found=%d",
		len(targets), len(allFindings))

	// Update adaptive scan interval: track which hosts had drift
	ds.updateLastDriftHosts(allFindings, scannedHosts)

	// Log healing rate after each scan cycle
	ds.daemon.logHealingRate()

	// Collect and analyze compliance-relevant Windows Event Logs (on-prem only).
	// Device logs are also fed into the threat detector for cross-host correlation
	// (brute force detection, ransomware indicators).
	go func() {
		logEntries := ds.collectAndAnalyzeDeviceLogs(ctx, targets)
		if ds.daemon.threatDet != nil && len(logEntries) > 0 {
			ds.daemon.threatDet.analyze(ctx, logEntries)
		}
		// Run VSS shadow copy check for ransomware detection
		if ds.daemon.threatDet != nil {
			ds.daemon.threatDet.analyzeVSS(ctx, targets)
		}
	}()

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
        if ($_.TaskName -notmatch '^(OsirisCareAgent|OsirisCare|ForceTimeSync|CreateExplorerShellUnelevatedTask|klnagent)') {
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

# 8. BitLocker status (all volumes)
$result.BitLocker = "NotAvailable"
$result.BitLockerVolumes = @()
try {
    $allVols = Get-BitLockerVolume -EA Stop
    if ($allVols) {
        $volResults = @()
        foreach ($vol in $allVols) {
            $volResults += @{
                MountPoint = $vol.MountPoint
                ProtectionStatus = $vol.ProtectionStatus.ToString()
                EncryptionPercentage = $vol.EncryptionPercentage
            }
        }
        $result.BitLockerVolumes = $volResults
        # Legacy single-value: "On" if ALL volumes protected, else first unprotected status
        $unprotected = $allVols | Where-Object { $_.ProtectionStatus -ne 'On' -and $_.ProtectionStatus -ne 1 }
        if ($unprotected) {
            $result.BitLocker = $unprotected[0].ProtectionStatus.ToString()
        } else {
            $result.BitLocker = "On"
        }
    }
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

# 19. WMI event subscriptions (persistence mechanism)
$wmiSubs = @()
try {
    Get-WmiObject -Namespace root\subscription -Class __EventFilter -EA Stop | ForEach-Object {
        if ($_.Name -notmatch '^(BVTFilter|SCM Event Log Filter)$') {
            $wmiSubs += @{ Name=$_.Name; Query=$_.Query }
        }
    }
} catch {}
$result.WMIPersistence = $wmiSubs

# 20. Registry Run key entries (persistence mechanism)
$runKeys = @()
$runPaths = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce'
)
foreach ($p in $runPaths) {
    $props = Get-ItemProperty -Path $p -EA SilentlyContinue
    if ($props) {
        $props.PSObject.Properties | Where-Object {
            $_.Name -notmatch '^(PS|SecurityHealth|Windows Defender|VMware|VBox|OsirisCare)'
        } | ForEach-Object {
            $val = $_.Value.ToString()
            $runKeys += @{ Name=$_.Name; Value=$val.Substring(0, [Math]::Min(200, $val.Length)); Path=$p }
        }
    }
}
$result.RegistryRunKeys = $runKeys

# 21. Audit policy subcategories (critical for HIPAA)
$auditPolicy = @{}
try {
    $ap = auditpol /get /category:* /r 2>$null | ConvertFrom-Csv
    $critical = @('Logon','Account Lockout','Process Creation','Security Group Management','User Account Management','Audit Policy Change','File System','Registry','Handle Manipulation','Detailed File Share','Process Termination','DPAPI Activity')
    foreach ($entry in $ap) {
        if ($critical -contains $entry.'Subcategory') {
            $auditPolicy[$entry.'Subcategory'] = $entry.'Inclusion Setting'
        }
    }
} catch {}
$result.AuditPolicy = $auditPolicy

# 22. Defender advanced protection settings
$defAdv = @{}
try {
    $status = Get-MpComputerStatus -EA Stop
    $defAdv.RealTimeProtection = $status.RealTimeProtectionEnabled.ToString()
    $defAdv.AntivirusEnabled = $status.AntivirusEnabled.ToString()
    $prefs = Get-MpPreference -EA Stop
    $defAdv.MAPSReporting = $prefs.MAPSReporting.ToString()
    $defAdv.CloudBlockLevel = $prefs.CloudBlockLevel.ToString()
    $defAdv.SubmitSamplesConsent = $prefs.SubmitSamplesConsent.ToString()
} catch {}
$result.DefenderAdvanced = $defAdv

# 23. Print Spooler service (attack surface)
$sp = Get-Service Spooler -EA SilentlyContinue
$result.SpoolerService = if ($sp) { $sp.Status.ToString() } else { "NotFound" }

# 24. Dangerous inbound firewall rules (name patterns + risky ports)
$dangerousRules = @()
$dangerousPatterns = @('Allow All*', 'Allow All Inbound*', 'Allow All RDP*', '*Allow Telnet*', 'RemoteAccess*', '*Open All*', '*Permit Any*')
$safeNamePrefixes = @('Core Networking', 'File and Printer', 'Remote Desktop', 'Windows Remote', 'DFS', 'AllJoyn', 'Cast to', 'Delivery', 'mDNS', 'Hyper-V', 'Network Discovery', 'Performance', 'Remote Event', 'OsirisCare', 'Wi-Fi Direct', 'BranchCache', 'Windows Defender')
try {
    Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True -EA Stop | ForEach-Object {
        $r = $_
        $isSafe = $false
        foreach ($sp in $safeNamePrefixes) { if ($r.DisplayName -like "$sp*") { $isSafe = $true; break } }
        if ($isSafe) { return }
        $matchedPattern = $false
        foreach ($p in $dangerousPatterns) { if ($r.DisplayName -like $p) { $matchedPattern = $true; break } }
        if ($matchedPattern) {
            $port = (Get-NetFirewallPortFilter -AssociatedNetFirewallRule $r -EA SilentlyContinue)
            $dangerousRules += @{ Name=$r.DisplayName; Port=$port.LocalPort; Protocol=$port.Protocol.ToString() }
            return
        }
        $port = (Get-NetFirewallPortFilter -AssociatedNetFirewallRule $r -EA SilentlyContinue)
        $isRisky = $false
        if ($port.LocalPort -eq 'Any' -and $port.Protocol -ne 'ICMPv4') { $isRisky = $true }
        if ($port.LocalPort -match '(21|23|69|445|3389|4444|5985|5986)') {
            if ($r.DisplayGroup -notmatch '(Remote Desktop|Windows Remote Management|File and Printer|Core Networking)') { $isRisky = $true }
        }
        if ($isRisky) {
            $dangerousRules += @{ Name=$r.DisplayName; Port=$port.LocalPort; Protocol=$port.Protocol.ToString() }
        }
    }
} catch {}
$result.DangerousInboundRules = $dangerousRules

# 25. Backup verification — VSS snapshots + Windows Server Backup + System Restore
$backup = @{
    backup_tool = "none"
    last_backup = ""
    backup_age_days = -1
    backup_status = "missing"
    restore_test = "not_tested"
    details = ""
}
try {
    # Check VSS shadow copies
    $vssOutput = vssadmin list shadows 2>$null
    $vssCount = ($vssOutput | Select-String 'Shadow Copy ID').Count
    if ($vssCount -gt 0) {
        $backup.backup_tool = "vss"
        $backup.backup_status = "current"
        $backup.details = "$vssCount shadow copies"
        # Get most recent shadow copy date
        $dates = $vssOutput | Select-String 'creation time:\s*(.+)' | ForEach-Object { $_.Matches[0].Groups[1].Value.Trim() }
        if ($dates) {
            $latest = $dates | Sort-Object -Descending | Select-Object -First 1
            try {
                $latestDate = [DateTime]::Parse($latest)
                $backup.last_backup = $latestDate.ToUniversalTime().ToString('o')
                $ageDays = [math]::Round(((Get-Date) - $latestDate).TotalDays, 1)
                $backup.backup_age_days = $ageDays
                if ($ageDays -gt 7) { $backup.backup_status = "stale" }
            } catch {}
        }
    }
} catch {}

# Windows Server Backup (if available)
try {
    $wbSummary = Get-WBSummary -EA Stop
    if ($wbSummary) {
        $backup.backup_tool = "wbadmin"
        $lastSuccess = $wbSummary.LastSuccessfulBackupTime
        if ($lastSuccess -and $lastSuccess -ne [DateTime]::MinValue) {
            $backup.last_backup = $lastSuccess.ToUniversalTime().ToString('o')
            $ageDays = [math]::Round(((Get-Date) - $lastSuccess).TotalDays, 1)
            $backup.backup_age_days = $ageDays
            $backup.backup_status = if ($ageDays -le 7) { "current" } else { "stale" }
            $backup.details = "Last WSB: $lastSuccess"
        }
    }
} catch {}

# System Restore points (fallback)
if ($backup.backup_tool -eq "none") {
    try {
        $rp = Get-ComputerRestorePoint -EA Stop | Sort-Object SequenceNumber -Descending | Select-Object -First 1
        if ($rp) {
            $backup.backup_tool = "system_restore"
            $rpDate = [Management.ManagementDateTimeConverter]::ToDateTime($rp.CreationTime)
            $backup.last_backup = $rpDate.ToUniversalTime().ToString('o')
            $ageDays = [math]::Round(((Get-Date) - $rpDate).TotalDays, 1)
            $backup.backup_age_days = $ageDays
            $backup.backup_status = if ($ageDays -le 7) { "current" } else { "stale" }
            $backup.details = "Restore point: $($rp.Description)"
        }
    } catch {}
}
$result.BackupVerification = $backup

$result | ConvertTo-Json -Depth 3 -Compress
`

	scanResult := ds.svc.WinRM.Execute(t.target, script, "DRIFT-SCAN", "driftscan", 30, 0, 15.0, nil)
	if !scanResult.Success {
		// Try via DC proxy for workstations
		if t.label == "WS" {
			return ds.checkTargetViaDCProxy(ctx, t)
		}
		errMsg := scanResult.Error
		log.Printf("[driftscan] Scan failed for %s (%s): %s", t.hostname, t.label, errMsg)

		// Distinguish auth failures from connectivity failures — auth errors
		// mean credentials are wrong, not that the host is unreachable.
		if isAuthError(errMsg) {
			log.Printf("[driftscan] ERROR: Credential auth failure for %s — check credential_name/password in site_credentials", t.hostname)
			return ds.credentialFailureFinding(t, errMsg)
		}
		return ds.unreachableFinding(t, "windows", errMsg)
	}

	stdout := maputil.String(scanResult.Output, "std_out")
	if stdout == "" {
		return nil
	}

	var state windowsScanState
	if err := json.Unmarshal([]byte(stdout), &state); err != nil {
		log.Printf("[driftscan] Parse error for %s: %v", t.hostname, err)
		return nil
	}

	return ds.evaluateWindowsFindings(&state, t)
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
	AgentStatus    string `json:"AgentStatus"`
	BitLocker      string `json:"BitLocker"`
	BitLockerVolumes []struct {
		MountPoint           string `json:"MountPoint"`
		ProtectionStatus     string `json:"ProtectionStatus"`
		EncryptionPercentage int    `json:"EncryptionPercentage"`
	} `json:"BitLockerVolumes"`
	SMBSigning string `json:"SMBSigning"`
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
	// New checks for chaos lab coverage
	WMIPersistence []struct {
		Name  string `json:"Name"`
		Query string `json:"Query"`
	} `json:"WMIPersistence"`
	RegistryRunKeys []struct {
		Name  string `json:"Name"`
		Value string `json:"Value"`
		Path  string `json:"Path"`
	} `json:"RegistryRunKeys"`
	AuditPolicy      map[string]string `json:"AuditPolicy"`
	DefenderAdvanced    map[string]string `json:"DefenderAdvanced"`
	SpoolerService      string            `json:"SpoolerService"`
	DangerousInboundRules []struct {
		Name     string `json:"Name"`
		Port     string `json:"Port"`
		Protocol string `json:"Protocol"`
	} `json:"DangerousInboundRules"`
	BackupVerification struct {
		BackupTool    string  `json:"backup_tool"`
		LastBackup    string  `json:"last_backup"`
		BackupAgeDays float64 `json:"backup_age_days"`
		BackupStatus  string  `json:"backup_status"`
		RestoreTest   string  `json:"restore_test"`
		Details       string  `json:"details"`
	} `json:"BackupVerification"`
}

// evaluateWindowsFindings converts a parsed Windows scan state into drift findings.
func (ds *driftScanner) evaluateWindowsFindings(state *windowsScanState, t scanTarget) []driftFinding {
	var findings []driftFinding

	// 1. Firewall
	for profile, enabled := range state.Firewall {
		if strings.EqualFold(enabled, "false") {
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

	// 8. BitLocker — ALL mounted volumes must be encrypted for HIPAA encryption at rest
	if len(state.BitLockerVolumes) > 0 {
		unprotected := []string{}
		for _, vol := range state.BitLockerVolumes {
			if vol.ProtectionStatus != "On" && vol.ProtectionStatus != "1" {
				unprotected = append(unprotected, fmt.Sprintf("%s(%s)", vol.MountPoint, vol.ProtectionStatus))
			}
		}
		if len(unprotected) > 0 {
			findings = append(findings, driftFinding{
				Hostname:     t.hostname,
				CheckType:    "bitlocker_status",
				Expected:     "On (all volumes)",
				Actual:       fmt.Sprintf("%d/%d unprotected: %s", len(unprotected), len(state.BitLockerVolumes), strings.Join(unprotected, ", ")),
				HIPAAControl: "164.312(a)(2)(iv)",
				Severity:     "critical",
				Details:      map[string]string{"unprotected_volumes": strings.Join(unprotected, ","), "total_volumes": fmt.Sprintf("%d", len(state.BitLockerVolumes))},
			})
		}
	} else if state.BitLocker != "NotAvailable" && state.BitLocker != "On" && state.BitLocker != "1" {
		// Fallback for older PowerShell without Get-BitLockerVolume array support
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

	// 11. Screen lock — must be configured with inactivity timeout <= 900s (15 min)
	if state.ScreenLock == "NotConfigured" {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "screen_lock_policy",
			Expected:     "<= 900",
			Actual:       "NotConfigured",
			HIPAAControl: "164.312(a)(2)(iii)",
			Severity:     "medium",
		})
	} else if state.ScreenLock != "Unknown" {
		// Check if timeout exceeds HIPAA 15-minute limit
		if timeout, err := strconv.Atoi(state.ScreenLock); err == nil && (timeout > 900 || timeout == 0) {
			findings = append(findings, driftFinding{
				Hostname:     t.hostname,
				CheckType:    "screen_lock_policy",
				Expected:     "<= 900",
				Actual:       state.ScreenLock,
				HIPAAControl: "164.312(a)(2)(iii)",
				Severity:     "medium",
				Details:      map[string]string{"inactivity_timeout_secs": state.ScreenLock},
			})
		}
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
		cfg := ds.svc.Config
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

	// 19. WMI event subscription persistence
	if len(state.WMIPersistence) > 0 {
		wmiNames := make([]string, 0, len(state.WMIPersistence))
		for _, sub := range state.WMIPersistence {
			wmiNames = append(wmiNames, sub.Name)
		}
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "wmi_event_persistence",
			Expected:     "none",
			Actual:       strings.Join(wmiNames, ", "),
			HIPAAControl: "164.308(a)(5)(ii)(C)",
			Severity:     "critical",
			Details:      map[string]string{"subscriptions": strings.Join(wmiNames, ",")},
		})
	}

	// 20. Registry Run key persistence
	if len(state.RegistryRunKeys) > 0 {
		keyNames := make([]string, 0, len(state.RegistryRunKeys))
		for _, rk := range state.RegistryRunKeys {
			keyNames = append(keyNames, rk.Name)
		}
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "registry_run_persistence",
			Expected:     "none",
			Actual:       strings.Join(keyNames, ", "),
			HIPAAControl: "164.308(a)(5)(ii)(C)",
			Severity:     "high",
			Details:      map[string]string{"keys": strings.Join(keyNames, ",")},
		})
	}

	// 21. Audit policy — flag if any critical subcategory is "No Auditing"
	if len(state.AuditPolicy) > 0 {
		nonCompliant := []string{}
		for subcategory, setting := range state.AuditPolicy {
			if setting == "No Auditing" {
				nonCompliant = append(nonCompliant, subcategory)
			}
		}
		if len(nonCompliant) > 0 {
			findings = append(findings, driftFinding{
				Hostname:     t.hostname,
				CheckType:    "audit_policy",
				Expected:     "Success and Failure",
				Actual:       "No Auditing: " + strings.Join(nonCompliant, ", "),
				HIPAAControl: "164.312(b)",
				Severity:     "critical",
				Details:      map[string]string{"subcategories": strings.Join(nonCompliant, ",")},
			})
		}
	}

	// 22. Defender advanced — cloud protection / MAPS disabled
	if len(state.DefenderAdvanced) > 0 {
		problems := []string{}
		if v, ok := state.DefenderAdvanced["RealTimeProtection"]; ok && v == "False" {
			problems = append(problems, "RealTimeProtection=off")
		}
		if v, ok := state.DefenderAdvanced["MAPSReporting"]; ok && v == "0" {
			problems = append(problems, "CloudProtection=off")
		}
		if v, ok := state.DefenderAdvanced["SubmitSamplesConsent"]; ok && v == "0" {
			problems = append(problems, "SampleSubmission=off")
		}
		if len(problems) > 0 {
			findings = append(findings, driftFinding{
				Hostname:     t.hostname,
				CheckType:    "defender_cloud_protection",
				Expected:     "all_enabled",
				Actual:       strings.Join(problems, ", "),
				HIPAAControl: "164.308(a)(5)(ii)(B)",
				Severity:     "high",
				Details:      map[string]string{"issues": strings.Join(problems, ",")},
			})
		}
	}

	// 23. Spooler service — should be disabled on DCs and servers
	if state.SpoolerService == "Running" && t.label == "DC" {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "spooler_service",
			Expected:     "Stopped",
			Actual:       "Running",
			HIPAAControl: "164.312(e)(1)",
			Severity:     "medium",
			Details:      map[string]string{"note": "PrintNightmare attack surface"},
		})
	}

	// 24. Dangerous inbound firewall rules
	if len(state.DangerousInboundRules) > 0 {
		ruleNames := make([]string, 0, len(state.DangerousInboundRules))
		for _, r := range state.DangerousInboundRules {
			ruleNames = append(ruleNames, r.Name)
		}
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "firewall_dangerous_rules",
			Expected:     "none",
			Actual:       strings.Join(ruleNames, ", "),
			HIPAAControl: "164.312(e)(1)",
			Severity:     "high",
			Details:      map[string]string{"rules": strings.Join(ruleNames, ",")},
		})
	}

	// 25. Backup verification — must have recent backup (within 7 days)
	// "missing"/"none" = backup not configured (setup requirement, not drift).
	// Report as a low-severity setup item, not a high-severity drift finding.
	// Only "stale" (configured but outdated) is real drift.
	bv := state.BackupVerification
	if bv.BackupStatus == "missing" || bv.BackupTool == "none" {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "backup_not_configured",
			Expected:     "backup software installed and configured",
			Actual:       "no backup tool detected — requires client/partner setup",
			HIPAAControl: "164.308(a)(7)(ii)(A)",
			Severity:     "low",
			Details: map[string]string{
				"backup_tool":   bv.BackupTool,
				"backup_status": bv.BackupStatus,
				"category":      "setup_requirement",
			},
		})
	} else if bv.BackupStatus == "stale" {
		findings = append(findings, driftFinding{
			Hostname:     t.hostname,
			CheckType:    "backup_verification",
			Expected:     "backup_within_7d",
			Actual:       fmt.Sprintf("last backup %.1f days ago (%s)", bv.BackupAgeDays, bv.BackupTool),
			HIPAAControl: "164.308(a)(7)(ii)(A)",
			Severity:     "medium",
			Details: map[string]string{
				"backup_tool":     bv.BackupTool,
				"backup_status":   bv.BackupStatus,
				"backup_age_days": fmt.Sprintf("%.1f", bv.BackupAgeDays),
				"last_backup":     bv.LastBackup,
				"restore_test":    bv.RestoreTest,
			},
		})
	}

	// Filter out disabled checks
	if len(findings) > 0 {
		filtered := findings[:0]
		for _, f := range findings {
			if !ds.isCheckDisabled(f.CheckType) {
				filtered = append(filtered, f)
			}
		}
		findings = filtered
	}

	if len(findings) > 0 {
		log.Printf("[driftscan] %s (%s): %d drift findings", t.hostname, t.label, len(findings))
	}

	return findings
}

// checkTargetViaDCProxy runs the drift scan on a workstation via the DC using Invoke-Command.
func (ds *driftScanner) checkTargetViaDCProxy(ctx context.Context, t scanTarget) []driftFinding {
	cfg := ds.svc.Config
	if cfg.DomainController == nil || *cfg.DomainController == "" {
		return nil
	}

	dcWS := ds.svc.Targets.ProbeWinRMPort(*cfg.DomainController)
	dcTarget := &winrm.Target{
		Hostname:  *cfg.DomainController,
		Port:      dcWS.Port,
		Username:  *cfg.DCUsername,
		Password:  *cfg.DCPassword,
		UseSSL:    dcWS.UseSSL,
		VerifySSL: true, // TOFU cert pinning via CertPinStore
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

	scanResult := ds.svc.WinRM.Execute(dcTarget, proxyScript, "DRIFT-SCAN-PROXY", "driftscan", 60, 0, 30.0, nil)
	if !scanResult.Success {
		log.Printf("[driftscan] DC proxy scan failed for %s: %s", t.hostname, scanResult.Error)
		// Direct WinRM and DC proxy both failed — try direct fallback with per-host creds
		return ds.checkTargetDirectFallback(ctx, t)
	}

	stdout := maputil.String(scanResult.Output, "std_out")
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
		log.Printf("[driftscan] DC proxy session error for %s: %s — trying direct WinRM fallback", t.hostname, proxyState.Error)
		// Fallback: try direct WinRM with workstation-specific credentials.
		// The initial direct attempt (in checkTarget) used DC admin creds which
		// may not have local admin rights on the workstation. Here we try with
		// per-workstation credentials from the credential store.
		return ds.checkTargetDirectFallback(ctx, t)
	}

	return ds.evaluateWindowsFindings(&proxyState.windowsScanState, t)
}

// checkTargetDirectFallback tries a direct WinRM scan using per-workstation
// credentials when both the initial direct attempt and DC proxy fail.
// This handles the common case where:
// - DC admin creds don't have local admin on the workstation
// - DC proxy (Invoke-Command) fails due to delegation/CredSSP config
// - But the workstation has its own local admin credentials in the cred store
func (ds *driftScanner) checkTargetDirectFallback(ctx context.Context, t scanTarget) []driftFinding {
	// Look up per-workstation credentials
	wt, ok := ds.svc.Targets.LookupWinTarget(t.hostname)
	if !ok {
		// Try by IP from the original target
		wt, ok = ds.svc.Targets.LookupWinTarget(t.target.Hostname)
	}
	if !ok || wt.Username == "" {
		log.Printf("[driftscan] No workstation-specific credentials for %s, cannot fallback", t.hostname)
		// All three scan paths failed (direct, DC proxy, direct fallback).
		// Report unreachable so it appears as an incident in the dashboard.
		return ds.unreachableFinding(t, "windows", "all scan methods failed (direct WinRM, DC proxy, direct fallback — no per-host credentials)")
	}

	// Build a new target with workstation-specific credentials
	ws := ds.svc.Targets.ProbeWinRMPort(wt.Hostname)
	directTarget := scanTarget{
		hostname: t.hostname,
		label:    "WS-DIRECT",
		target: &winrm.Target{
			Hostname:  wt.Hostname,
			Port:      ws.Port,
			Username:  wt.Username,
			Password:  wt.Password,
			UseSSL:    ws.UseSSL,
			VerifySSL: true, // TOFU cert pinning via CertPinStore
		},
	}

	log.Printf("[driftscan] Direct fallback scan for %s using %s@%s",
		t.hostname, wt.Username, wt.Hostname)

	return ds.checkTarget(ctx, directTarget)
}

// runLinuxScanIfNeeded runs a Linux scan if the interval has elapsed.
func (ds *driftScanner) runLinuxScanIfNeeded(ctx context.Context) {
	if !atomic.CompareAndSwapInt32(&ds.linuxRunning, 0, 1) {
		return
	}
	defer atomic.StoreInt32(&ds.linuxRunning, 0)

	// Check if we have targets — don't burn the interval timer on an empty scan
	hasTargets := len(ds.svc.Targets.GetLinuxTargets()) > 0

	ds.linuxMu.Lock()
	since := time.Since(ds.lastLinuxScanTime)
	first := ds.lastLinuxScanTime.IsZero()
	ds.linuxMu.Unlock()

	interval := clampScanInterval(ds.effectiveInterval())
	if !first && since < interval {
		return
	}

	// If no targets yet (credentials haven't arrived from checkin), skip
	// without updating lastLinuxScanTime so we retry next cycle
	if !hasTargets {
		return
	}

	log.Printf("[linuxscan] Starting Linux drift scan cycle (interval=%v)", interval)
	ds.linuxMu.Lock()
	ds.lastLinuxScanTime = time.Now()
	ds.linuxMu.Unlock()

	ds.scanLinuxTargets(ctx)
}

// staleCredentialThreshold is how long a credential can go without a
// successful scan before it is flagged as stale.
const staleCredentialThreshold = 7 * 24 * time.Hour // 7 days

// checkStaleCredentials iterates over all scanned targets and reports a
// credential_stale finding for any host whose last successful scan is
// older than 7 days (or has never succeeded).
func (ds *driftScanner) checkStaleCredentials(targets []scanTarget) {
	if ds.isCheckDisabled("credential_stale") {
		return
	}

	now := time.Now()

	for _, t := range targets {
		lastScan, ever := ds.daemon.state.GetLastSuccessfulScan(t.hostname)

		var daysSince int
		var lastScanStr string
		if !ever {
			daysSince = -1 // never scanned
			lastScanStr = "never"
		} else {
			daysSince = int(now.Sub(lastScan).Hours() / 24)
			lastScanStr = lastScan.Format(time.RFC3339)
		}

		if ever && now.Sub(lastScan) < staleCredentialThreshold {
			continue // scanned recently enough
		}

		// 24h dedup: only fire once per day per host
		if !ds.daemon.state.ShouldSendStaleAlert(t.hostname) {
			continue
		}

		ds.reportDrift(&driftFinding{
			Hostname:     t.hostname,
			CheckType:    "credential_stale",
			Expected:     "Successful scan within 7 days",
			Actual:       fmt.Sprintf("Last successful scan: %s", lastScanStr),
			Severity:     "medium",
			HIPAAControl: "164.308(a)(8)",
			Details: map[string]string{
				"hostname":        t.hostname,
				"days_since_scan": fmt.Sprintf("%d", daysSince),
				"credential_name": t.hostname,
			},
		})
	}
}

// reportDrift sends a drift finding through the L1→L2→L3 healing pipeline.
func (ds *driftScanner) reportDrift(f *driftFinding) {
	metadata := map[string]string{
		"platform": "windows",
		"source":   "driftscan",
	}
	for k, v := range f.Details {
		metadata[k] = v
	}

	// If the daemon has DC credentials, include them for healing
	if ds.svc.Config.DCUsername != nil {
		metadata["winrm_username"] = *ds.svc.Config.DCUsername
	}
	if ds.svc.Config.DCPassword != nil {
		metadata["winrm_password"] = *ds.svc.Config.DCPassword
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
	ds.daemon.healIncident(context.Background(), &req)
}

// unreachableFinding creates a device_unreachable drift finding for a target
// that failed all scan methods. Returns a single-element slice so it can be
// returned directly from checkTarget / checkTargetDirectFallback.
// Cooldown deduplication is handled downstream in healIncident.
func (ds *driftScanner) unreachableFinding(t scanTarget, platform, errorMsg string) []driftFinding {
	if ds.isCheckDisabled("device_unreachable") {
		return nil
	}
	return []driftFinding{{
		Hostname:     t.hostname,
		CheckType:    "device_unreachable",
		Expected:     "Device responding to management protocol",
		Actual:       fmt.Sprintf("Connection failed: %s", errorMsg),
		HIPAAControl: "164.312(a)(1)",
		Severity:     "medium",
		Details: map[string]string{
			"hostname":   t.hostname,
			"ip_address": t.target.Hostname,
			"port":       fmt.Sprintf("%d", t.target.Port),
			"error":      errorMsg,
			"platform":   platform,
			"label":      t.label,
		},
	}}
}

// isAuthError returns true if the error indicates an authentication failure.
// Delegates to classifyHealError (healing_executor.go) to avoid duplicating match logic.
func isAuthError(errMsg string) bool {
	return classifyHealError(errMsg) == "auth_failure"
}

// credentialFailureFinding creates a credential_stale drift finding when
// WinRM authentication fails. This is distinct from device_unreachable —
// the host is reachable but credentials are wrong.
func (ds *driftScanner) credentialFailureFinding(t scanTarget, errorMsg string) []driftFinding {
	if ds.isCheckDisabled("credential_stale") {
		return nil
	}
	return []driftFinding{{
		Hostname:     t.hostname,
		CheckType:    "credential_stale",
		Expected:     "WinRM authentication successful",
		Actual:       fmt.Sprintf("Authentication failed: %s", errorMsg),
		HIPAAControl: "164.312(d)",
		Severity:     "high",
		Details: map[string]string{
			"hostname":   t.hostname,
			"ip_address": t.target.Hostname,
			"port":       fmt.Sprintf("%d", t.target.Port),
			"error":      errorMsg,
			"label":      t.label,
			"username":   t.target.Username,
		},
	}}
}

// lookupDeviceIP searches the netscan's discovered devices for one matching
// the given hostname (case-insensitive). Returns the discovered IP and true
// if found. This enables cross-referencing credential targets against ARP data
// to detect DHCP IP changes.
func (ds *driftScanner) lookupDeviceIP(hostname string) (string, bool) {
	if ds.daemon.netScan == nil {
		return "", false
	}
	return ds.daemon.netScan.LookupDeviceByHostname(hostname)
}

// dnsReResolve checks whether a credential-delivered target's stored IP still
// matches DNS. If DNS resolves to a different IP, it logs a diagnostic warning.
// It does NOT auto-update credentials (that is a security decision for the partner).
// Returns the DNS-resolved IP if different from stored, or empty string if same/unresolvable.
func dnsReResolve(ctx context.Context, credName, storedIP string) string {
	// Only meaningful when the stored address looks like an IP
	if net.ParseIP(storedIP) == nil {
		return "" // stored as hostname, DNS handled naturally
	}

	addrs, err := net.DefaultResolver.LookupHost(ctx, credName)
	if err != nil || len(addrs) == 0 {
		return "" // credential name doesn't resolve via DNS — normal for IP-only entries
	}

	for _, addr := range addrs {
		if addr == storedIP {
			return "" // stored IP matches DNS — all good
		}
	}

	log.Printf("[driftscan] DNS mismatch for credential %q: stored=%s, DNS=%s (possible DHCP change)",
		credName, storedIP, addrs[0])
	return addrs[0]
}

// resolveHostnameWithFallback resolves a hostname using the system resolver first,
// then falls back to the AD DNS server if configured and the system resolver fails.
// This handles the common case where the MikroTik router doesn't know AD hostnames.
func resolveHostnameWithFallback(ctx context.Context, hostname, adDNSServer string) (string, error) {
	// Try system resolver first
	addrs, err := net.DefaultResolver.LookupHost(ctx, hostname)
	if err == nil && len(addrs) > 0 {
		return addrs[0], nil
	}

	// Fall back to AD DNS server if configured
	if adDNSServer == "" {
		return "", fmt.Errorf("resolve %s: %w (no AD DNS fallback configured)", hostname, err)
	}

	adResolver := &net.Resolver{
		PreferGo: true,
		Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
			d := net.Dialer{Timeout: 5 * time.Second}
			return d.DialContext(ctx, "udp", adDNSServer+":53")
		},
	}
	addrs, err2 := adResolver.LookupHost(ctx, hostname)
	if err2 != nil || len(addrs) == 0 {
		return "", fmt.Errorf("resolve %s: system=%v, ad_dns=%v", hostname, err, err2)
	}

	log.Printf("[driftscan] Resolved %s via AD DNS (%s) → %s", hostname, adDNSServer, addrs[0])
	return addrs[0], nil
}
