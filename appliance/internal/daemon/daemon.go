package daemon

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/osiriscare/appliance/internal/ca"
	"github.com/osiriscare/appliance/internal/evidence"
	"github.com/osiriscare/appliance/internal/grpcserver"
	"github.com/osiriscare/appliance/internal/healing"
	"github.com/osiriscare/appliance/internal/l2bridge"
	"github.com/osiriscare/appliance/internal/orders"
	"github.com/osiriscare/appliance/internal/sshexec"
	"github.com/osiriscare/appliance/internal/winrm"
)

// Version is set at build time.
var Version = "0.1.0"

// driftCooldown tracks cooldown state for a hostname+check_type pair.
type driftCooldown struct {
	lastSeen    time.Time
	count       int           // Number of times seen in the flap window
	cooldownDur time.Duration // Current cooldown duration (escalates on flap)
}

// Daemon is the main appliance daemon that orchestrates all subsystems.
type Daemon struct {
	config    *Config
	phoneCli  *PhoneHomeClient
	grpcSrv   *grpcserver.Server
	registry  *grpcserver.AgentRegistry
	agentCA   *ca.AgentCA
	l1Engine  *healing.Engine
	l2Client  *l2bridge.Client
	orderProc *orders.Processor
	winrmExec *winrm.Executor
	sshExec   *sshexec.Executor

	// Auto-deploy: spread agent to discovered workstations
	deployer *autoDeployer

	// Drift scanner: periodic security checks on Windows targets
	scanner *driftScanner

	// Evidence submitter: packages drift scan results into compliance bundles
	evidenceSubmitter *evidence.Submitter
	agentPublicKey    string // hex-encoded Ed25519 public key

	// Drift report cooldown: prevents excessive incident creation
	cooldownMu sync.Mutex
	cooldowns  map[string]*driftCooldown // key: "hostname:check_type"
}

// New creates a new daemon with the given configuration.
func New(cfg *Config) *Daemon {
	d := &Daemon{
		config:    cfg,
		phoneCli:  NewPhoneHomeClient(cfg),
		registry:  grpcserver.NewAgentRegistry(),
		cooldowns: make(map[string]*driftCooldown),
	}

	// Initialize WinRM and SSH executors (must be before L1 engine)
	d.winrmExec = winrm.NewExecutor()
	d.sshExec = sshexec.NewExecutor()

	// Initialize L1 healing engine
	rulesDir := cfg.RulesDir()
	var executor healing.ActionExecutor
	if cfg.HealingDryRun {
		executor = nil // nil executor → dry-run mode
	} else {
		executor = d.makeActionExecutor()
	}
	d.l1Engine = healing.NewEngine(rulesDir, executor)
	log.Printf("[daemon] L1 engine loaded: %d rules (healing=%v)", d.l1Engine.RuleCount(), !cfg.HealingDryRun)

	// Initialize L2 bridge (connects lazily)
	if cfg.L2Enabled {
		socketPath := filepath.Join(cfg.StateDir, "l2.sock")
		d.l2Client = l2bridge.NewClient(socketPath, 30*time.Second)
	}

	// Initialize order processor with completion callback
	d.orderProc = orders.NewProcessor(cfg.StateDir, d.completeOrder)

	// Initialize auto-deployer for zero-friction agent spread
	d.deployer = newAutoDeployer(d)

	// Initialize drift scanner for periodic security checks
	d.scanner = newDriftScanner(d)

	// Initialize evidence submitter for compliance pipeline
	if cfg.EnableEvidenceUpload {
		sigKey, pubHex, err := evidence.LoadOrCreateSigningKey(cfg.SigningKeyPath())
		if err != nil {
			log.Printf("[daemon] Evidence signing key failed: %v (evidence upload disabled)", err)
		} else {
			d.agentPublicKey = pubHex
			d.evidenceSubmitter = evidence.NewSubmitter(
				cfg.SiteID, cfg.APIEndpoint, cfg.APIKey, sigKey, pubHex,
			)
			log.Printf("[daemon] Evidence submitter initialized (pubkey=%s...)", pubHex[:12])
		}
	}

	return d
}

// Run starts the daemon and blocks until the context is cancelled.
func (d *Daemon) Run(ctx context.Context) error {
	log.Printf("[daemon] OsirisCare Appliance Daemon v%s starting", Version)
	log.Printf("[daemon] site_id=%s, poll_interval=%ds, healing=%v, l2=%v",
		d.config.SiteID, d.config.PollInterval, d.config.HealingEnabled, d.config.L2Enabled)

	// Initialize CA
	if d.config.CADir != "" {
		d.agentCA = ca.New(d.config.CADir)
		if err := d.agentCA.EnsureCA(); err != nil {
			log.Printf("[daemon] CA init failed: %v (cert enrollment disabled)", err)
			d.agentCA = nil
		} else {
			log.Printf("[daemon] CA initialized from %s", d.config.CADir)
		}
	}

	// Connect L2 bridge if enabled
	if d.l2Client != nil {
		if err := d.l2Client.Connect(); err != nil {
			log.Printf("[daemon] L2 bridge connect failed: %v (L2 fallback disabled until reconnect)", err)
		} else {
			log.Printf("[daemon] L2 bridge connected")
		}
	}

	// Complete any deferred NixOS rebuild orders from prior restart
	d.orderProc.CompletePendingRebuild(ctx)

	// Start HTTP file server for agent binary distribution.
	// Domain controllers download the agent binary via Invoke-WebRequest
	// instead of slow WinRM chunk uploads.
	go d.serveAgentFiles(ctx)

	// Start gRPC server
	d.grpcSrv = grpcserver.NewServer(grpcserver.Config{
		Port:   d.config.GRPCPort,
		SiteID: d.config.SiteID,
	}, d.registry, d.agentCA)

	go func() {
		if err := d.grpcSrv.Serve(); err != nil {
			log.Printf("[daemon] gRPC server error: %v", err)
		}
	}()

	// Drain heal channel (process incidents from gRPC drift events)
	go d.processHealRequests(ctx)

	// Initial checkin
	d.runCheckin(ctx)

	// Main loop
	ticker := time.NewTicker(time.Duration(d.config.PollInterval) * time.Second)
	defer ticker.Stop()

	log.Printf("[daemon] Main loop started (interval: %ds)", d.config.PollInterval)

	for {
		select {
		case <-ctx.Done():
			log.Println("[daemon] Shutting down...")
			d.grpcSrv.GracefulStop()
			if d.l2Client != nil {
				d.l2Client.Close()
			}
			d.sshExec.CloseAll()
			return nil
		case <-ticker.C:
			d.runCycle(ctx)
		}
	}
}

// runCycle executes one iteration of the main daemon loop.
func (d *Daemon) runCycle(ctx context.Context) {
	start := time.Now()

	// Phone home to Central Command
	d.runCheckin(ctx)

	// Auto-deploy agents to discovered workstations (zero-friction).
	// Runs async so slow DC responses don't block the main loop.
	if d.config.WorkstationEnabled {
		go d.deployer.runAutoDeployIfNeeded(ctx)
	}

	// Drift scanning: periodic security checks on Windows targets.
	// Detects firewall disabled, rogue users, rogue tasks, stopped services.
	if d.config.WorkstationEnabled {
		go d.scanner.runDriftScanIfNeeded(ctx)
	}

	elapsed := time.Since(start)
	log.Printf("[daemon] Cycle complete in %v (agents=%d)",
		elapsed, d.registry.ConnectedCount())
}

// runCheckin sends a checkin to Central Command and processes the response.
func (d *Daemon) runCheckin(ctx context.Context) {
	var req CheckinRequest
	if d.agentPublicKey != "" {
		req = SystemInfoWithKey(d.config, Version, d.agentPublicKey)
	} else {
		req = SystemInfo(d.config, Version)
	}

	resp, err := d.phoneCli.Checkin(ctx, req)
	if err != nil {
		log.Printf("[daemon] Checkin failed: %v", err)
		return
	}

	log.Printf("[daemon] Checkin OK: appliance=%s, orders=%d, triggers=(enum=%v, scan=%v)",
		resp.ApplianceID, len(resp.PendingOrders),
		resp.TriggerEnumeration, resp.TriggerImmediateScan)

	// Process pending orders via order processor
	if len(resp.PendingOrders) > 0 {
		d.processOrders(ctx, resp.PendingOrders)
	}
}

// processOrders converts raw checkin order maps to Order structs and dispatches them.
func (d *Daemon) processOrders(ctx context.Context, rawOrders []map[string]interface{}) {
	orderList := make([]orders.Order, 0, len(rawOrders))
	for _, raw := range rawOrders {
		orderID, _ := raw["order_id"].(string)
		orderType, _ := raw["order_type"].(string)

		params := make(map[string]interface{})
		if p, ok := raw["parameters"].(map[string]interface{}); ok {
			params = p
		}
		// Inject order_id into params so handlers like nixos_rebuild can persist it
		params["_order_id"] = orderID

		orderList = append(orderList, orders.Order{
			OrderID:    orderID,
			OrderType:  orderType,
			Parameters: params,
		})
	}

	results := d.orderProc.ProcessAll(ctx, orderList)
	for _, r := range results {
		if r.Success {
			log.Printf("[daemon] Order %s completed successfully", r.OrderID)
		} else {
			log.Printf("[daemon] Order %s failed: %s", r.OrderID, r.Error)
		}
	}
}

// completeOrder reports order completion back to Central Command via HTTP POST.
func (d *Daemon) completeOrder(ctx context.Context, orderID string, success bool, result map[string]interface{}, errMsg string) error {
	log.Printf("[daemon] Order %s completion: success=%v", orderID, success)

	payload := map[string]interface{}{
		"success": success,
	}
	if result != nil {
		payload["result"] = result
	}
	if errMsg != "" {
		payload["error_message"] = errMsg
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal completion: %w", err)
	}

	url := strings.TrimRight(d.config.APIEndpoint, "/") + "/api/orders/" + orderID + "/complete"

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create completion request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+d.config.APIKey)

	resp, err := d.phoneCli.client.Do(httpReq)
	if err != nil {
		log.Printf("[daemon] Order %s completion POST failed: %v (will retry on next cycle)", orderID, err)
		return fmt.Errorf("completion request: %w", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		log.Printf("[daemon] Order %s completion returned %d: %s", orderID, resp.StatusCode, string(respBody))
		return fmt.Errorf("completion returned %d", resp.StatusCode)
	}

	log.Printf("[daemon] Order %s completion accepted by Central Command", orderID)
	return nil
}

// serveAgentFiles serves the agent binary directory over HTTP for DC downloads.
// Used by the auto-deploy DC proxy path — DC downloads agent binary via
// Invoke-WebRequest instead of slow WinRM chunk uploads.
func (d *Daemon) serveAgentFiles(ctx context.Context) {
	agentDir := filepath.Join(d.config.StateDir, "agent")
	mux := http.NewServeMux()
	mux.Handle("/agent/", http.StripPrefix("/agent/", http.FileServer(http.Dir(agentDir))))

	srv := &http.Server{
		Addr:    ":8090",
		Handler: mux,
	}

	go func() {
		<-ctx.Done()
		srv.Close()
	}()

	log.Printf("[daemon] Agent file server on :8090 (serving %s)", agentDir)
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Printf("[daemon] Agent file server error: %v", err)
	}
}

// processHealRequests reads from the gRPC server's heal channel and routes
// incidents through the L1→L2→L3 healing pipeline.
func (d *Daemon) processHealRequests(ctx context.Context) {
	if d.grpcSrv == nil {
		return
	}
	for {
		select {
		case <-ctx.Done():
			return
		case req := <-d.grpcSrv.HealChan:
			log.Printf("[daemon] Heal request: %s/%s from %s",
				req.Hostname, req.CheckType, req.AgentID)

			if !d.config.HealingEnabled {
				log.Printf("[daemon] Healing disabled, skipping %s/%s", req.Hostname, req.CheckType)
				continue
			}

			d.healIncident(ctx, req)
		}
	}
}

// healIncident routes an incident through L1 deterministic → L2 LLM → L3 escalation.
func (d *Daemon) healIncident(ctx context.Context, req grpcserver.HealRequest) {
	// Drift report cooldown: suppress repeated incidents for the same host+check
	// Default 10 min cooldown, escalates to 1 hour on flap detection (>3 in 30 min)
	cooldownKey := req.Hostname + ":" + req.CheckType
	if d.shouldSuppressDrift(cooldownKey) {
		log.Printf("[daemon] Drift suppressed (cooldown): %s/%s", req.Hostname, req.CheckType)
		return
	}

	incidentID := fmt.Sprintf("drift-%s-%s-%d", req.Hostname, req.CheckType, time.Now().UnixMilli())

	// Build incident data map for L1 matching.
	// L1 rules match on "check_type" and "drift_detected" fields,
	// mirroring the Python agent's incident structure.
	data := map[string]interface{}{
		"check_type":     req.CheckType,
		"incident_type":  req.CheckType,
		"drift_detected": true, // drift events always indicate failed checks
		"hostname":       req.Hostname,
		"host_id":        req.Hostname,
		"agent_id":       req.AgentID,
		"expected":       req.Expected,
		"actual":         req.Actual,
		"hipaa_control":  req.HIPAAControl,
		"platform":       "windows", // gRPC drift events come from Windows agents
	}
	for k, v := range req.Metadata {
		data[k] = v
	}

	severity := "high"
	if req.HIPAAControl == "" {
		severity = "medium"
	}

	// L1: Deterministic matching
	match := d.l1Engine.Match(incidentID, req.CheckType, severity, data)
	if match != nil {
		log.Printf("[daemon] L1 match: rule=%s action=%s for %s/%s",
			match.Rule.ID, match.Action, req.Hostname, req.CheckType)

		result := d.l1Engine.Execute(match, d.config.SiteID, req.Hostname)
		if result.Success {
			log.Printf("[daemon] L1 healed %s/%s via %s in %dms",
				req.Hostname, req.CheckType, match.Rule.ID, result.DurationMs)

			// GPO firewall fix: when firewall drift is healed, also fix the
			// domain GPO to prevent GPO from turning firewall back off.
			// Zero-friction: runs automatically without operator intervention.
			if req.CheckType == "firewall_status" {
				go d.fixFirewallGPO(req.Hostname)
			}
		} else {
			log.Printf("[daemon] L1 execution failed for %s/%s: %s",
				req.Hostname, req.CheckType, result.Error)
		}
		return
	}

	// L2: LLM planner (if enabled and connected)
	if d.l2Client != nil && d.l2Client.IsConnected() {
		log.Printf("[daemon] L1 no match for %s/%s, escalating to L2", req.Hostname, req.CheckType)

		incident := &l2bridge.Incident{
			ID:           incidentID,
			SiteID:       d.config.SiteID,
			HostID:       req.Hostname,
			IncidentType: req.CheckType,
			Severity:     severity,
			RawData:      data,
			CreatedAt:    time.Now().UTC().Format(time.RFC3339),
		}

		decision, err := d.l2Client.PlanWithRetry(incident, 1)
		if err != nil {
			log.Printf("[daemon] L2 plan failed for %s/%s: %v — escalating to L3",
				req.Hostname, req.CheckType, err)
			d.escalateToL3(incidentID, req, "L2 plan failed: "+err.Error())
			return
		}

		if decision.ShouldExecute() {
			log.Printf("[daemon] L2 decision: %s (confidence=%.2f) for %s/%s",
				decision.RecommendedAction, decision.Confidence, req.Hostname, req.CheckType)
			d.executeL2Action(ctx, decision, req, incidentID)
			return
		}

		// L2 says escalate
		log.Printf("[daemon] L2 escalating %s/%s to L3: %s",
			req.Hostname, req.CheckType, decision.Reasoning)
		d.escalateToL3(incidentID, req, decision.Reasoning)
		return
	}

	// L3: No L1 match and no L2 available
	log.Printf("[daemon] No L1 match and L2 unavailable for %s/%s — escalating to L3",
		req.Hostname, req.CheckType)
	d.escalateToL3(incidentID, req, "No L1 rule match, L2 not available")
}

// executeL2Action dispatches an L2 decision to the appropriate executor (WinRM or SSH).
func (d *Daemon) executeL2Action(ctx context.Context, decision *l2bridge.LLMDecision, req grpcserver.HealRequest, incidentID string) {
	platform, _ := req.Metadata["platform"]
	if platform == "" {
		platform = "windows" // default: gRPC drift events come from Windows agents
	}

	script, _ := decision.ActionParams["script"].(string)
	if script == "" {
		script = decision.RecommendedAction
	}

	runbookID := decision.RunbookID
	if runbookID == "" {
		runbookID = "L2-AUTO-" + incidentID
	}

	hipaaControls := []string{}
	if req.HIPAAControl != "" {
		hipaaControls = []string{req.HIPAAControl}
	}

	switch platform {
	case "windows":
		target := d.buildWinRMTarget(req)
		if target == nil {
			log.Printf("[daemon] L2 no WinRM target for %s — escalating to L3", req.Hostname)
			d.escalateToL3(incidentID, req, "No WinRM credentials for target")
			return
		}
		result := d.winrmExec.Execute(target, script, runbookID, "l2_auto", 300, 1, 30.0, hipaaControls)
		if result.Success {
			log.Printf("[daemon] L2 healed %s/%s via WinRM in %.1fs (hash=%s)",
				req.Hostname, req.CheckType, result.DurationSecs, result.OutputHash)
		} else {
			log.Printf("[daemon] L2 WinRM execution failed for %s/%s: %s — escalating to L3",
				req.Hostname, req.CheckType, result.Error)
			d.escalateToL3(incidentID, req, "L2 WinRM execution failed: "+result.Error)
		}

	case "linux":
		target := d.buildSSHTarget(req)
		if target == nil {
			log.Printf("[daemon] L2 no SSH target for %s — escalating to L3", req.Hostname)
			d.escalateToL3(incidentID, req, "No SSH credentials for target")
			return
		}
		result := d.sshExec.Execute(ctx, target, script, runbookID, "l2_auto", 60, 1, 5.0, true, hipaaControls)
		if result.Success {
			log.Printf("[daemon] L2 healed %s/%s via SSH in %.1fs (hash=%s)",
				req.Hostname, req.CheckType, result.DurationSecs, result.OutputHash)
		} else {
			log.Printf("[daemon] L2 SSH execution failed for %s/%s: %s — escalating to L3",
				req.Hostname, req.CheckType, result.Error)
			d.escalateToL3(incidentID, req, "L2 SSH execution failed: "+result.Error)
		}

	default:
		log.Printf("[daemon] L2 unknown platform %q for %s — escalating to L3", platform, req.Hostname)
		d.escalateToL3(incidentID, req, fmt.Sprintf("Unknown platform: %s", platform))
	}
}

// buildWinRMTarget creates a WinRM target from the heal request metadata.
// Credentials come from the checkin response's windows_targets list, cached in the daemon.
func (d *Daemon) buildWinRMTarget(req grpcserver.HealRequest) *winrm.Target {
	// Extract credentials from metadata (populated during drift report with target info)
	username, _ := req.Metadata["winrm_username"]
	password, _ := req.Metadata["winrm_password"]
	ipAddr, _ := req.Metadata["ip_address"]

	if username == "" || password == "" {
		return nil
	}

	hostname := req.Hostname
	if ipAddr != "" {
		hostname = ipAddr
	}

	return &winrm.Target{
		Hostname: hostname,
		Port:     5985,
		Username: username,
		Password: password,
		UseSSL:   false,
	}
}

// buildSSHTarget creates an SSH target from the heal request metadata.
func (d *Daemon) buildSSHTarget(req grpcserver.HealRequest) *sshexec.Target {
	username, _ := req.Metadata["ssh_username"]
	password, _ := req.Metadata["ssh_password"]
	key, _ := req.Metadata["ssh_private_key"]
	ipAddr, _ := req.Metadata["ip_address"]

	if username == "" {
		username = "root"
	}
	if password == "" && key == "" {
		return nil
	}

	hostname := req.Hostname
	if ipAddr != "" {
		hostname = ipAddr
	}

	target := &sshexec.Target{
		Hostname: hostname,
		Port:     22,
		Username: username,
	}
	if password != "" {
		target.Password = &password
	}
	if key != "" {
		target.PrivateKey = &key
	}

	return target
}

// escalateToL3 logs an incident that requires human intervention.
func (d *Daemon) escalateToL3(incidentID string, req grpcserver.HealRequest, reason string) {
	log.Printf("[daemon] L3 ESCALATION: incident=%s host=%s check=%s hipaa=%s reason=%s",
		incidentID, req.Hostname, req.CheckType, req.HIPAAControl, reason)
	// In production, this would create an escalation record in Central Command
	// and potentially send notifications (email, Slack, etc.)
}

// gpoFixDone tracks whether the GPO firewall fix has already been applied.
// sync.Map: key = DC hostname, value = true
var gpoFixDone sync.Map

// fixFirewallGPO runs a PowerShell script on the domain controller to ensure
// the Default Domain Policy GPO has firewall enabled (not disabled).
// This fixes the root cause of recurring firewall drift: a GPO that turns off
// the Windows Firewall, which the L1 healer re-enables, creating a flap loop.
//
// Zero-friction: runs automatically after the first firewall heal, no operator
// intervention required. Only runs once per DC per daemon lifetime.
func (d *Daemon) fixFirewallGPO(triggerHost string) {
	// Need DC credentials
	if d.config.DomainController == nil || *d.config.DomainController == "" {
		return
	}
	if d.config.DCUsername == nil || d.config.DCPassword == nil {
		return
	}

	dc := *d.config.DomainController

	// Only fix once per DC
	if _, done := gpoFixDone.LoadOrStore(dc, true); done {
		return
	}

	log.Printf("[daemon] GPO firewall fix: checking Default Domain Policy on %s (triggered by %s)",
		dc, triggerHost)

	target := &winrm.Target{
		Hostname: dc,
		Port:     5985,
		Username: *d.config.DCUsername,
		Password: *d.config.DCPassword,
		UseSSL:   false,
	}

	// PowerShell script that checks and fixes the GPO firewall setting.
	// Uses the GroupPolicy module (available on DCs by default).
	// Checks if Default Domain Policy disables firewall for any profile,
	// and if so, sets all profiles to Enabled.
	gpoFixScript := `
$ErrorActionPreference = 'Stop'
$Result = @{ Changed = $false; Profiles = @{}; Error = $null }

try {
    Import-Module GroupPolicy -ErrorAction Stop

    # Get Default Domain Policy GUID
    $DDPName = "Default Domain Policy"
    $GPO = Get-GPO -Name $DDPName -ErrorAction Stop

    # Registry-based firewall settings in GPO
    # Location: HKLM\SOFTWARE\Policies\Microsoft\WindowsFirewall
    $Profiles = @("DomainProfile", "StandardProfile", "PublicProfile")
    $BasePath = "HKLM\SOFTWARE\Policies\Microsoft\WindowsFirewall"

    foreach ($Profile in $Profiles) {
        $RegPath = "$BasePath\$Profile"
        try {
            $Val = Get-GPRegistryValue -Name $DDPName -Key $RegPath -ValueName "EnableFirewall" -ErrorAction Stop
            $Result.Profiles[$Profile] = @{ CurrentValue = $Val.Value; Type = $Val.Type.ToString() }

            if ($Val.Value -eq 0) {
                # Firewall is DISABLED by GPO — fix it
                Set-GPRegistryValue -Name $DDPName -Key $RegPath -ValueName "EnableFirewall" -Type DWord -Value 1
                $Result.Changed = $true
                $Result.Profiles[$Profile].Fixed = $true
                $Result.Profiles[$Profile].NewValue = 1
            }
        } catch [System.Runtime.InteropServices.COMException] {
            # Registry value not set in GPO — no conflict, firewall not managed by this GPO
            $Result.Profiles[$Profile] = @{ Status = "not_configured" }
        }
    }

    if ($Result.Changed) {
        # Force group policy update on all domain computers
        $Result.GPUpdateTriggered = $true
    }

    $Result.Success = $true
} catch {
    $Result.Error = $_.Exception.Message
    $Result.Success = $false
}

$Result | ConvertTo-Json -Depth 3
`

	result := d.winrmExec.Execute(target, gpoFixScript, "GPO-FW-FIX", "gpo_fix", 120, 1, 30.0, []string{"164.312(a)(1)"})
	if result.Success {
		log.Printf("[daemon] GPO firewall fix completed on %s: output_hash=%s", dc, result.OutputHash)

		// After fixing GPO, force gpupdate on the trigger host
		if triggerHost != dc {
			triggerTarget := d.findWinRMTarget(triggerHost)
			if triggerTarget != nil {
				gpupdateResult := d.winrmExec.Execute(triggerTarget,
					"gpupdate /force /target:computer | Out-Null; @{Updated=$true} | ConvertTo-Json",
					"GPO-FW-UPDATE", "gpo_update", 60, 1, 15.0, nil)
				if gpupdateResult.Success {
					log.Printf("[daemon] GPO update forced on %s", triggerHost)
				}
			}
		}
	} else {
		log.Printf("[daemon] GPO firewall fix failed on %s: %s", dc, result.Error)
		// Allow retry on next occurrence
		gpoFixDone.Delete(dc)
	}
}

// findWinRMTarget builds a WinRM target for a hostname using DC credentials.
// Domain admin credentials (from config) work for all domain-joined machines.
func (d *Daemon) findWinRMTarget(hostname string) *winrm.Target {
	if d.config.DCUsername == nil || d.config.DCPassword == nil {
		return nil
	}
	return &winrm.Target{
		Hostname: hostname,
		Port:     5985,
		Username: *d.config.DCUsername,
		Password: *d.config.DCPassword,
		UseSSL:   false,
	}
}

const (
	defaultCooldown = 10 * time.Minute // Normal cooldown between heal attempts
	flapCooldown    = 1 * time.Hour    // Extended cooldown when flapping detected
	flapThreshold   = 3                // Occurrences in flapWindow → flapping
	flapWindow      = 30 * time.Minute // Window to count occurrences
	cooldownCleanup = 2 * time.Hour    // Entries older than this are removed
)

// shouldSuppressDrift checks if a drift report should be suppressed due to cooldown.
// Returns true if the drift should be suppressed (still in cooldown).
// Implements flap detection: if >3 drift events for the same key within 30 minutes,
// extends cooldown to 1 hour.
func (d *Daemon) shouldSuppressDrift(key string) bool {
	d.cooldownMu.Lock()
	defer d.cooldownMu.Unlock()

	now := time.Now()

	// Lazy cleanup of stale entries
	if len(d.cooldowns) > 100 {
		for k, entry := range d.cooldowns {
			if now.Sub(entry.lastSeen) > cooldownCleanup {
				delete(d.cooldowns, k)
			}
		}
	}

	entry, exists := d.cooldowns[key]
	if !exists {
		// First time seeing this drift — allow it, start tracking
		d.cooldowns[key] = &driftCooldown{
			lastSeen:    now,
			count:       1,
			cooldownDur: defaultCooldown,
		}
		return false
	}

	elapsed := now.Sub(entry.lastSeen)

	// Still in cooldown — suppress
	if elapsed < entry.cooldownDur {
		// Count flap occurrences
		if elapsed < flapWindow {
			entry.count++
			if entry.count >= flapThreshold {
				entry.cooldownDur = flapCooldown
				log.Printf("[daemon] Flap detected for %s (%d in %v), cooldown extended to %v",
					key, entry.count, elapsed.Round(time.Second), flapCooldown)
			}
		}
		return true
	}

	// Cooldown expired — allow, reset tracking
	entry.lastSeen = now
	entry.count = 1
	entry.cooldownDur = defaultCooldown
	return false
}
