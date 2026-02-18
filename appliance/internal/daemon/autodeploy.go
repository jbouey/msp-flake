package daemon

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/osiriscare/appliance/internal/discovery"
	"github.com/osiriscare/appliance/internal/winrm"
)

const (
	// Agent paths
	agentBinaryName = "osiris-agent.exe"
	agentInstallDir = `C:\OsirisCare`
	agentServiceName = "OsirisCareAgent"

	// Timing
	autoDeployInterval  = 1 * time.Hour  // Re-check for new workstations
	deployCheckInterval = 5 * time.Minute // Minimum between status checks on same host
)

// autoDeployer manages automatic Go agent deployment to discovered workstations.
type autoDeployer struct {
	daemon *Daemon

	mu           sync.Mutex
	deployed     map[string]time.Time // hostname → last successful deploy time
	lastCheck    map[string]time.Time // hostname → last status check time
	lastEnumTime time.Time            // last AD enumeration
	agentB64     string               // cached base64 of agent binary
	agentLoaded  bool
}

func newAutoDeployer(d *Daemon) *autoDeployer {
	return &autoDeployer{
		daemon:    d,
		deployed:  make(map[string]time.Time),
		lastCheck: make(map[string]time.Time),
	}
}

// runAutoDeployIfNeeded is called each daemon cycle. It checks if it's time
// to enumerate and deploy, and runs if so.
func (ad *autoDeployer) runAutoDeployIfNeeded(ctx context.Context) {
	cfg := ad.daemon.config

	// Need DC credentials for AD enumeration + deployment
	if cfg.DomainController == nil || *cfg.DomainController == "" {
		return
	}
	if cfg.DCUsername == nil || cfg.DCPassword == nil {
		return
	}

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

// runAutoDeployOnce performs one full cycle: enumerate → check → deploy.
func (ad *autoDeployer) runAutoDeployOnce(ctx context.Context) {
	cfg := ad.daemon.config
	dc := *cfg.DomainController
	username := *cfg.DCUsername
	password := *cfg.DCPassword

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

	log.Printf("[autodeploy] %d/%d workstations reachable via WinRM", len(reachable), len(workstations))

	if len(reachable) == 0 {
		return
	}

	// Deploy to each reachable workstation
	deployed := 0
	skipped := 0
	failed := 0

	for _, ws := range reachable {
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
		ad.mu.Unlock()

		// Check if agent is already running on this workstation
		target := ad.buildTarget(ws)
		if target == nil {
			continue
		}

		installed, running := ad.checkAgentStatus(ctx, target)
		if installed && running {
			ad.mu.Lock()
			ad.deployed[hostname] = time.Now()
			ad.mu.Unlock()
			skipped++
			continue
		}

		// Deploy the agent
		if err := ad.deployAgent(ctx, target, ws); err != nil {
			log.Printf("[autodeploy] Deploy to %s failed: %v", hostname, err)
			failed++
		} else {
			log.Printf("[autodeploy] Successfully deployed agent to %s", hostname)
			ad.mu.Lock()
			ad.deployed[hostname] = time.Now()
			ad.mu.Unlock()
			deployed++
		}
	}

	log.Printf("[autodeploy] Complete: deployed=%d, skipped=%d, failed=%d",
		deployed, skipped, failed)
}

// buildTarget creates a WinRM target for a workstation using DC admin credentials.
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
		Hostname: hostname,
		Port:     5985,
		Username: *cfg.DCUsername,
		Password: *cfg.DCPassword,
		UseSSL:   false,
	}
}

// checkAgentStatus checks if the OsirisCare agent service is installed and running.
func (ad *autoDeployer) checkAgentStatus(_ context.Context, target *winrm.Target) (installed bool, running bool) {
	script := fmt.Sprintf(`
$svc = Get-Service -Name "%s" -ErrorAction SilentlyContinue
if ($svc) {
    @{ installed = $true; running = ($svc.Status -eq "Running") } | ConvertTo-Json -Compress
} else {
    @{ installed = $false; running = $false } | ConvertTo-Json -Compress
}
`, agentServiceName)

	result := ad.daemon.winrmExec.Execute(target, script, "AGENT-CHECK", "autodeploy", 15, 1, 10.0, nil)
	if !result.Success {
		return false, false
	}

	// stdout is in Output["std_out"]
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

// loadAgentBinary reads and base64-encodes the agent binary (cached).
func (ad *autoDeployer) loadAgentBinary() (string, error) {
	ad.mu.Lock()
	defer ad.mu.Unlock()

	if ad.agentLoaded {
		return ad.agentB64, nil
	}

	// Look for agent binary in standard locations
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

// deployAgent deploys the Go agent to a single workstation via WinRM.
// 5-step pipeline: mkdir → write binary → write config → install service → verify.
func (ad *autoDeployer) deployAgent(ctx context.Context, target *winrm.Target, ws discovery.ADComputer) error {
	agentB64, err := ad.loadAgentBinary()
	if err != nil {
		return err
	}

	// Get the appliance's gRPC listen address for the agent config
	grpcAddr := ad.daemon.config.GRPCListenAddr()

	hostname := ws.Hostname

	// Step 1: Create install directory
	log.Printf("[autodeploy] [%s] Step 1/5: Creating directory", hostname)
	mkdirResult := ad.daemon.winrmExec.Execute(target,
		fmt.Sprintf(`New-Item -ItemType Directory -Force -Path "%s" | Out-Null; "OK"`, agentInstallDir),
		"AGENT-DEPLOY-MKDIR", "autodeploy", 30, 1, 10.0, nil)
	if !mkdirResult.Success {
		return fmt.Errorf("mkdir failed: %s", mkdirResult.Error)
	}

	// Step 2: Write agent binary (base64 decode on target)
	log.Printf("[autodeploy] [%s] Step 2/5: Writing agent binary (%d bytes encoded)", hostname, len(agentB64))

	// Write base64 in chunks to avoid WinRM command size limits
	chunkSize := 400000 // ~400KB chunks (safe for WinRM)
	tempB64File := agentInstallDir + `\agent.b64`

	// Clear any previous temp file
	ad.daemon.winrmExec.Execute(target,
		fmt.Sprintf(`Remove-Item -Path "%s" -Force -ErrorAction SilentlyContinue`, tempB64File),
		"AGENT-DEPLOY-CLEAN", "autodeploy", 10, 1, 5.0, nil)

	for i := 0; i < len(agentB64); i += chunkSize {
		end := i + chunkSize
		if end > len(agentB64) {
			end = len(agentB64)
		}
		chunk := agentB64[i:end]

		appendScript := fmt.Sprintf(`Add-Content -Path "%s" -Value "%s" -NoNewline`, tempB64File, chunk)
		chunkResult := ad.daemon.winrmExec.Execute(target,
			appendScript, "AGENT-DEPLOY-CHUNK", "autodeploy", 60, 1, 30.0, nil)
		if !chunkResult.Success {
			return fmt.Errorf("write chunk %d failed: %s", i/chunkSize, chunkResult.Error)
		}
	}

	// Decode base64 to exe
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

	// Step 3: Write config file
	log.Printf("[autodeploy] [%s] Step 3/5: Writing config", hostname)
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

	// Step 4: Install as Windows service
	log.Printf("[autodeploy] [%s] Step 4/5: Installing service", hostname)
	serviceScript := fmt.Sprintf(`
$ErrorActionPreference = 'Stop'
$serviceName = "%s"
$exePath = "%s\%s"
$configPath = "%s\config.json"

# Remove existing service if present
$existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    sc.exe delete $serviceName | Out-Null
    Start-Sleep -Seconds 2
}

# Create new service
New-Service -Name $serviceName `+
		"`"+`-BinaryPathName "`+"$exePath --config `\"$configPath`\"\""+` `+
		"`"+`-DisplayName "OsirisCare Compliance Agent" `+
		"`"+`-Description "HIPAA compliance monitoring agent" `+
		"`"+`-StartupType Automatic `+
		"`"+`-ErrorAction Stop

# Start service
Start-Service -Name $serviceName -ErrorAction Stop

# Set recovery: restart on failure
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

	// Step 5: Verify
	log.Printf("[autodeploy] [%s] Step 5/5: Verifying", hostname)
	installed, running := ad.checkAgentStatus(ctx, target)
	if !installed || !running {
		return fmt.Errorf("verification failed: installed=%v running=%v", installed, running)
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
		Hostname: hostname,
		Port:     5985,
		Username: username,
		Password: password,
		UseSSL:   false,
	}

	result := e.winrmExec.Execute(target, script, "AD-ENUM", "discovery", timeout, 1, float64(timeout), nil)
	if !result.Success {
		return "", fmt.Errorf("%s", result.Error)
	}
	stdout, _ := result.Output["std_out"].(string)
	return stdout, nil
}
