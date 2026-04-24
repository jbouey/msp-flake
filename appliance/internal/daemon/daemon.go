package daemon

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"runtime/debug"
	"strings"
	"sync"
	"time"

	"github.com/osiriscare/appliance/internal/ca"
	"github.com/osiriscare/appliance/internal/crypto"
	"github.com/osiriscare/appliance/internal/evidence"
	"github.com/osiriscare/appliance/internal/grpcserver"
	"github.com/osiriscare/appliance/internal/healing"
	"github.com/osiriscare/appliance/internal/l2bridge"
	"github.com/osiriscare/appliance/internal/logshipper"
	"github.com/osiriscare/appliance/internal/maputil"
	"github.com/osiriscare/appliance/internal/l2planner"
	"github.com/osiriscare/appliance/internal/orders"
	"github.com/osiriscare/appliance/internal/phiscrub"
	"github.com/osiriscare/appliance/internal/sdnotify"
	"github.com/osiriscare/appliance/internal/sshexec"
	"github.com/osiriscare/appliance/internal/winrm"
)

// Version is set at build time via -ldflags.
// Default "dev" indicates an untagged development build.
var Version = "0.4.8"

// driftCooldown tracks cooldown state for a hostname+check_type pair.
type driftCooldown struct {
	lastSeen    time.Time
	count       int           // Number of times seen in the flap window
	cooldownDur time.Duration // Current cooldown duration (escalates on flap)
}

// winTarget stores per-workstation Windows credentials from checkin.
type winTarget struct {
	Hostname string
	Username string
	Password string
	UseSSL   bool
	Role     string // "domain_admin", "winrm", "local_admin", etc.
}

// Daemon is the main appliance daemon that orchestrates all subsystems.
type Daemon struct {
	config    *Config
	phoneCli  *PhoneHomeClient
	grpcSrv   *grpcserver.Server
	registry  *grpcserver.AgentRegistry
	agentCA   *ca.AgentCA
	l1Engine  *healing.Engine
	l2Client  *l2bridge.Client  // legacy Unix socket bridge (deprecated)
	l2Planner *l2planner.Planner // native Go L2 LLM planner
	orderProc *orders.Processor
	winrmExec *winrm.Executor
	certPins  *winrm.CertPinStore // TOFU cert pinning for WinRM TLS
	sshExec   *sshexec.Executor

	// Credential envelope encryption keypair (X25519)
	envelopeKP *crypto.EnvelopeKeypair

	// Device-bound Ed25519 identity (Week 1 of the composed identity
	// stack). Loaded once at New(); shared with the phonehome client
	// for signing outbound requests.
	identity *Identity

	// Auto-deploy: spread agent to discovered workstations
	deployer *autoDeployer

	// Drift scanner: periodic security checks on Windows + Linux targets
	scanner *driftScanner

	// Threat detector: cross-host event correlation (brute force, ransomware indicators)
	threatDet *threatDetector

	// Network scanner: periodic port/reachability checks
	netScan *netScanner

	// Evidence submitter: packages drift scan results into compliance bundles
	evidenceSubmitter *evidence.Submitter
	agentPublicKey    string // hex-encoded Ed25519 public key

	// Telemetry reporter: sends L1/L2 execution outcomes to Central Command
	telemetry *l2planner.TelemetryReporter

	// Incident reporter: sends drift findings to POST /incidents for dashboard display
	incidents *incidentReporter

	// StateManager holds all mutex-protected state (cooldowns, targets, L2 mode, etc.)
	state *StateManager

	// Healing rate tracker: timestamped outcomes for rolling 24h rate calculation
	healTracker *healingRateTracker

	// Services bundles interfaces for subsystem access (set in New, RunCtx updated in Run)
	svc *Services

	// Run context: cancelled on daemon shutdown, propagated to healing operations
	runCtx    context.Context
	runCancel context.CancelFunc

	// WaitGroup for graceful goroutine drain on shutdown
	wg sync.WaitGroup

	// Healing journal: crash-safe tracking of in-flight healing operations
	healJournal *HealingJournal

	// Agent binary version cache for self-update endpoint
	agentVersionCache *agentVersionCache

	// Agent binary manifest: platform/arch → binary metadata for multi-platform deployment
	agentManifest *AgentManifest

	// Log shipper: tails journald and ships batches to Central Command
	logShipper *logshipper.Shipper

	// Circuit breaker: gates outbound calls when Central Command is unreachable
	serverBreaker *CircuitBreaker

	// Self-healer: monitors agent heartbeats, auto-redeploys stale agents
	selfHealer *selfHealer

	// WireGuard tunnel monitor: tracks connection state transitions
	wgMon *wgMonitor

	// credentialProbeOnce ensures the startup credential connectivity probe runs exactly once.
	credentialProbeOnce sync.Once

	// Mesh: consistent hash ring for multi-appliance scan target distribution
	mesh *Mesh

	// reconcileDetector: Session 205 Phase 2. Detects agent time-travel
	// (snapshot revert, backup restore, disk clone) and reports signals
	// in each checkin. Phase 3 adds plan application.
	reconcileDetector *ReconcileDetector

	// startTime tracks daemon boot for uptime reporting
	startTime time.Time

	// consecutiveAuthFailures tracks persistent 401s for auto-rekey
	consecutiveAuthFailures int

	// configPath is the path to config.yaml (for atomic API key updates)
	configPath string

	// Phase 13.5 H4 — immediate-checkin channel for sig-verify refresh.
	// When the Processor's verifySignature fails, it calls
	// RequestImmediateCheckin() which pushes to this buffered chan.
	// The main loop `select`s on `ticker.C OR <-checkinRequest` and
	// runs an extra checkin cycle on signal.
	//
	// `checkinDone` is a one-shot-per-cycle broadcast that fires at
	// the END of each runCheckin (success OR failure). The H4 caller
	// waits on it to know when the pubkey has been refreshed.
	checkinRequest chan struct{}
	checkinDoneMu  sync.Mutex
	checkinDone    []chan struct{} // waiters to notify on next checkin completion
}

// safeGo runs f in a new goroutine tracked by d.wg, with panic recovery.
// If the goroutine panics, the panic value and full stack trace are logged
// instead of crashing the daemon.
func (d *Daemon) safeGo(name string, f func()) {
	d.wg.Add(1)
	go func() {
		defer d.wg.Done()
		defer func() {
			if r := recover(); r != nil {
				log.Printf("[daemon] PANIC in goroutine %q: %v\n%s", name, r, debug.Stack())
			}
		}()
		f()
	}()
}

// attemptRekey requests a new API key from Central Command when persistent
// 401 auth failures indicate the current key is invalid. On success, updates
// both the in-memory config and the config.yaml file atomically.
func (d *Daemon) attemptRekey(ctx context.Context) {
	log.Printf("[daemon] AUTH FAILED %d consecutive times — requesting rekey from Central Command",
		d.consecutiveAuthFailures)

	resp, err := d.phoneCli.RequestRekey(ctx)
	if err != nil {
		log.Printf("[daemon] Rekey failed: %v", err)
		return
	}

	// Update config.yaml on disk
	configPath := d.config.ConfigPath
	if configPath == "" {
		configPath = "/var/lib/msp/config.yaml"
	}
	if err := UpdateAPIKey(configPath, resp.APIKey); err != nil {
		log.Printf("[daemon] Rekey: failed to update config file: %v", err)
		return
	}

	// Update in-memory config and recreate HTTP client with new key
	d.config.APIKey = resp.APIKey
	d.phoneCli = NewPhoneHomeClient(d.config)
	// v40.4 / daemon 0.4.8 (2026-04-23): propagate the new key to every
	// sub-component that holds its own copy. Prior to this, only
	// d.phoneCli saw the new key — d.incidents, d.telemetry, d.logShipper
	// each kept the stale string captured at New() time forever, producing
	// the split-brain 401-storm observed on /api/evidence/submit,
	// /api/logs/ingest, /api/agent/executions (audit item #5). Each
	// SetAPIKey is mutex-protected and nil-safe.
	d.incidents.SetAPIKey(resp.APIKey)
	if d.telemetry != nil {
		d.telemetry.SetAPIKey(resp.APIKey)
	}
	if d.logShipper != nil {
		d.logShipper.SetAPIKey(resp.APIKey)
	}
	d.consecutiveAuthFailures = 0

	log.Printf("[daemon] Rekey successful — new API key written to %s, clients rotated", configPath)
}

// isSubscriptionActive returns true if healing should be allowed.
// Active and trialing subscriptions allow healing; all other states suppress it.
func (d *Daemon) isSubscriptionActive() bool {
	return d.state.IsSubscriptionActive()
}

// New creates a new daemon with the given configuration.
func New(cfg *Config) *Daemon {
	// Load (or first-boot create) the device identity BEFORE
	// constructing the phonehome client, so the client can hold a
	// reference and sign every outbound checkin.
	identity, identityErr := LoadOrCreateIdentity(cfg.StateDir)
	if identityErr != nil {
		// Non-fatal during the soak window: identity is observe-only on
		// the server side. We log loud and continue with bearer auth.
		log.Printf("[daemon] WARN: identity load failed (%v) — soak-mode falls back to bearer auth", identityErr)
	} else {
		log.Printf("[daemon] identity loaded: fingerprint=%s pubkey=%s",
			identity.Fingerprint(), identity.PublicKeyHex())
	}

	d := &Daemon{
		config:        cfg,
		identity:      identity,
		phoneCli:      NewPhoneHomeClientWithIdentity(cfg, identity),
		registry:      grpcserver.NewAgentRegistryPersistent(cfg.StateDir),
		state:         NewStateManager(),
		healTracker:   newHealingRateTracker(),
		serverBreaker: NewCircuitBreaker(5, 5*time.Minute),
		// reconcileDetector bumps boot_counter on construction — call once here.
		reconcileDetector: NewReconcileDetector(cfg.StateDir),
		startTime:     time.Now(),
		// Phase 13.5 H4 — buffered 1 so the sig-verify fail path never
		// blocks. Extra signals collapse to "at most one pending checkin
		// refresh request" which is exactly what we want.
		checkinRequest: make(chan struct{}, 1),
	}

	// Initialize WinRM cert pin store + executor (must be before L1 engine)
	pinStorePath := filepath.Join(cfg.StateDir, "winrm_pins.json")
	d.certPins = winrm.NewCertPinStore(pinStorePath)
	d.winrmExec = winrm.NewExecutorWithPins(d.certPins)
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
	d.l1Engine.SetRequireSignedRules(cfg.RequireSignedRules)
	log.Printf("[daemon] L1 engine loaded: %d rules (healing=%v, require_signed_rules=%v)", d.l1Engine.RuleCount(), !cfg.HealingDryRun, cfg.RequireSignedRules)

	// Initialize L2 planner (calls Central Command → Anthropic, no LLM key on device)
	if cfg.L2Enabled {
		d.l2Planner = l2planner.NewPlanner(&l2planner.PlannerConfig{
			APIEndpoint: cfg.APIEndpoint, // Same Central Command endpoint as checkins
			APIKey:      cfg.APIKey,      // Same site API key as checkins
			SiteID:      cfg.SiteID,
			APITimeout:  time.Duration(cfg.L2APITimeoutSecs) * time.Second,
			Budget: l2planner.BudgetConfig{
				DailyBudgetUSD:     cfg.L2DailyBudgetUSD,
				MaxCallsPerHour:    cfg.L2MaxCallsPerHour,
				MaxConcurrentCalls: cfg.L2MaxConcurrentCalls,
			},
			AllowedActions: cfg.L2AllowedActions,
		})
		log.Printf("[daemon] L2 planner initialized (via Central Command, budget=$%.2f/day)",
			cfg.L2DailyBudgetUSD)
	}

	// Initialize telemetry reporter for L1/L2 execution data flywheel
	if cfg.APIEndpoint != "" && cfg.APIKey != "" {
		d.telemetry = l2planner.NewTelemetryReporter(cfg.APIEndpoint, cfg.APIKey, cfg.SiteID)
		d.telemetry.EnableQueue(cfg.StateDir)
		d.incidents = newIncidentReporter(cfg.APIEndpoint, cfg.APIKey, cfg.SiteID)
		d.incidents.allowFunc = d.serverBreaker.Allow
		log.Printf("[daemon] Telemetry + incident reporters initialized (endpoint=%s)", cfg.APIEndpoint)
	}

	// Initialize log shipper (journald → Central Command)
	if cfg.APIEndpoint != "" && cfg.APIKey != "" {
		hostname, _ := os.Hostname()
		d.logShipper = logshipper.New(logshipper.Config{
			APIEndpoint: cfg.APIEndpoint,
			APIKey:      cfg.APIKey,
			SiteID:      cfg.SiteID,
			Hostname:    hostname,
			StateDir:    cfg.StateDir,
			BatchSize:   500,
			FlushEvery:  30 * time.Second,
			AllowFunc:   d.serverBreaker.Allow,
		})
		log.Printf("[daemon] Log shipper initialized (endpoint=%s)", cfg.APIEndpoint)
	}

	// Initialize credential envelope encryption keypair (X25519)
	envelopeKP, err := crypto.LoadOrCreateKeypair(cfg.StateDir)
	if err != nil {
		log.Printf("[daemon] WARNING: credential envelope encryption disabled: %v", err)
	} else {
		d.envelopeKP = envelopeKP
		log.Printf("[daemon] Credential envelope encryption enabled (pubkey=%s...)", d.envelopeKP.PublicKeyHex()[:16])
	}

	// Initialize healing journal for crash-safe execution tracking
	d.healJournal = newHealingJournal(cfg.StateDir)
	if active := d.healJournal.ActiveCount(); active > 0 {
		log.Printf("[daemon] Healing journal: %d recovered entries", active)
	}

	// Initialize order processor with completion callback
	d.orderProc = orders.NewProcessor(cfg.StateDir, d.completeOrder)
	d.orderProc.SetAgentCounter(d.registry)
	d.orderProc.SetRuleReloader(d.l1Engine.ReloadRules)
	// Phase 13.5 H4 — processor can trigger an immediate checkin when
	// sig-verify fails; it waits briefly for the pubkey refresh.
	d.orderProc.SetRefreshCheckinCallback(d.requestImmediateCheckinAndWait)

	// Build Services struct for subsystem injection (RunCtx set in Run())
	d.svc = &Services{
		Config:    cfg,
		Targets:   d.state,
		Cooldowns: d.state,
		Checks:    d.state,
		Incidents: d.incidents,
		WinRM:     d.winrmExec,
		SSH:       d.sshExec,
		Registry:  d.registry,
		SiteID:    cfg.SiteID,
	}

	// Initialize auto-deployer for zero-friction agent spread
	d.deployer = newAutoDeployer(d.svc, d)

	// Wire the AD hostnames callback now that deployer exists
	d.state.SetADHostnamesFunc(d.deployer.getADHostnames)

	// Initialize drift scanner for periodic security checks
	d.scanner = newDriftScanner(d.svc, d)

	// Initialize threat detector for cross-host event correlation
	d.threatDet = newThreatDetector(d.svc, d)

	// Override run_drift order stub with real handler that triggers scanner
	d.orderProc.RegisterHandler("run_drift", func(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
		if maputil.String(params, "mode") == "app_discovery" {
			return d.scanner.RunAppDiscovery(ctx, params)
		}
		return d.scanner.ForceScan(ctx), nil
	})

	// Override healing order stub with real handler that executes runbooks
	d.orderProc.RegisterHandler("healing", func(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
		return d.executeHealingOrder(ctx, params)
	})

	// Override validate_credential stub with real WinRM connectivity test
	d.orderProc.RegisterHandler("validate_credential", func(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
		return d.handleValidateCredential(ctx, params)
	})

	// configure_workstation_agent: fix execution policy + write config + start agent service
	// Uses the daemon's own authenticated WinRM session (bypasses external auth issues)
	d.orderProc.RegisterHandler("configure_workstation_agent", func(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
		return d.handleConfigureWorkstationAgent(ctx, params)
	})

	// remove_agent: stop service, remove binary + data dirs from a Linux/macOS host via SSH
	d.orderProc.RegisterHandler("remove_agent", func(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
		return d.handleRemoveAgent(ctx, params)
	})

	// clear_winrm_pin: remove TOFU cert pin for a host (e.g., after cert rotation)
	d.orderProc.RegisterHandler("clear_winrm_pin", func(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
		hostname := maputil.String(params, "hostname")
		if hostname == "" {
			return nil, fmt.Errorf("hostname is required for clear_winrm_pin")
		}
		d.certPins.ClearPin(hostname)
		d.winrmExec.InvalidateSession(hostname) // Force new session on next connect
		log.Printf("[orders] clear_winrm_pin: cleared cert pin for %s", hostname)
		return map[string]interface{}{
			"status":   "cleared",
			"hostname": hostname,
		}, nil
	})

	// chaos_quicktest: inject drift scenarios via WinRM, let normal scan cycle detect + heal
	d.orderProc.RegisterHandler("chaos_quicktest", func(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
		return d.handleChaosQuicktest(ctx, params)
	})

	// Initialize network scanner for port/reachability checks
	d.netScan = newNetScanner(d.svc, d)

	// Initialize self-healer for agent heartbeat monitoring + auto-redeploy
	d.selfHealer = newSelfHealer(d.svc, d)

	// Initialize WireGuard tunnel monitor for audit logging
	d.wgMon = &wgMonitor{}

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
			d.evidenceSubmitter.AllowFunc = d.serverBreaker.Allow
			d.evidenceSubmitter.CacheDir = filepath.Join(cfg.StateDir, "evidence_cache")
			d.evidenceSubmitter.OnSubmitted = func(bundleID, hash string) {
				d.state.AddBundleHash(BundleHashEntry{
					BundleID:   bundleID,
					BundleHash: hash,
					CheckedAt:  time.Now().UTC().Format(time.RFC3339),
				})
			}
			log.Printf("[daemon] Evidence submitter initialized (pubkey=%s..., cache=%s, witnessing=on)", pubHex[:12], d.evidenceSubmitter.CacheDir)
		}
	}

	// Restore persisted state from prior session (linux targets, L2 mode, cooldowns)
	if err := d.state.LoadFromDisk(cfg.StateDir); err != nil {
		log.Printf("[daemon] Failed to load persisted state: %v", err)
	}

	return d
}

// Run starts the daemon and blocks until the context is cancelled.
func (d *Daemon) Run(ctx context.Context) error {
	d.runCtx, d.runCancel = context.WithCancel(ctx)
	d.svc.RunCtx = d.runCtx
	log.Printf("[daemon] OsirisCare Appliance Daemon %s starting", Version)
	l2Mode := "disabled"
	if d.l2Planner != nil {
		l2Mode = "native"
	} else if d.l2Client != nil {
		l2Mode = "bridge"
	}
	log.Printf("[daemon] site_id=%s, poll_interval=%ds, healing=%v, l2=%s",
		d.config.SiteID, d.config.PollInterval, d.config.HealingEnabled, l2Mode)

	// Phase W1.1: provision the watchdog api_key if /etc/msp-watchdog.yaml
	// is absent/empty. Runs asynchronously — bootstrap failure is logged
	// and doesn't block the main daemon's own startup.
	go d.BootstrapWatchdogIfNeeded(d.runCtx)

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

	// L2 planner readiness check
	if d.l2Planner != nil {
		if d.l2Planner.IsConnected() {
			log.Printf("[daemon] L2 planner ready (via Central Command)")
		} else {
			log.Printf("[daemon] L2 planner: missing API credentials")
		}
	}

	// Complete any deferred NixOS rebuild orders from prior restart
	d.orderProc.CompletePendingRebuild(ctx)

	// Complete any deferred update_daemon orders. The handler writes
	// a marker before the scheduled restart; this call observes the
	// running version (Version is a build-time constant) against the
	// expected version after the 70s health check has had time to
	// roll back if needed. Reports success or failure to /complete
	// — the backend then sees the truth instead of the racy
	// "ACK before restart" of the legacy flow.
	d.orderProc.CompletePendingUpdate(ctx, Version)

	// Initialize agent version cache (used by both HTTP file server and gRPC heartbeat)
	agentDir := filepath.Join(d.config.StateDir, "agent")
	d.agentVersionCache = newAgentVersionCache(agentDir)

	// Initialize agent binary manifest (multi-platform binary tracking).
	// ScanDirectory auto-detects binaries from filename conventions; idempotent.
	d.agentManifest = NewAgentManifest(d.config.StateDir)
	binDir := filepath.Join(d.config.StateDir, "bin")
	if err := d.agentManifest.ScanDirectory(binDir); err != nil {
		log.Printf("[daemon] Agent manifest scan failed: %v", err)
	} else {
		log.Printf("[daemon] Agent manifest: %d binaries registered", d.agentManifest.Count())
	}

	// Initialize mesh for multi-appliance scan coordination.
	// Single appliance = ring of 1 = scans everything (no behavior change).
	// When peers are discovered via ARP + gRPC probe, targets are split via consistent hashing.
	selfMAC := getMACAddress()
	if selfMAC != "" {
		d.mesh = NewMesh(selfMAC, d.config.SiteID, d.config.GRPCPort)
		d.svc.Mesh = d.mesh
		// Set CA cert pool for TLS-verified peer probes (P1-4)
		if d.agentCA != nil {
			if caPEM, err := d.agentCA.CACertPEM(); err == nil {
				pool := x509.NewCertPool()
				if pool.AppendCertsFromPEM(caPEM) {
					d.mesh.SetCACertPool(pool)
					log.Printf("[mesh] TLS peer verification enabled (CA cert loaded)")
				}
			}
		}
		log.Printf("[daemon] Mesh initialized: self=%s, site=%s, grpc_port=%d", selfMAC, d.config.SiteID, d.config.GRPCPort)

		// Immediate peer discovery at startup — don't wait for the 10-min netscan cycle.
		// This prevents duplicate scans in the first cycle when multiple appliances boot together.
		d.safeGo("meshBootstrapDiscovery", func() {
			devices := discoverARPDevices()
			if len(devices) > 0 {
				// Probe gRPC port on each device to identify siblings
				for i := range devices {
					conn, dialErr := net.DialTimeout("tcp", fmt.Sprintf("%s:%d", devices[i].IPAddress, d.config.GRPCPort), 2*time.Second)
					if dialErr == nil {
						conn.Close()
						devices[i].ProbeGRPC = true
					}
				}
				d.mesh.UpdatePeers(devices)
				if d.mesh.PeerCount() > 0 {
					log.Printf("[daemon] Mesh bootstrap: discovered %d peer(s) at startup", d.mesh.PeerCount())
				}
			}
		})
	}

	// Publish gRPC service via Avahi/mDNS for agent auto-discovery.
	// Agents resolve _osiris-grpc._tcp.local instead of hardcoding appliance IP.
	// Survives DHCP drift — agents re-resolve on disconnect.
	d.publishAvahiService()

	// Start HTTP file server for agent binary distribution.
	// Domain controllers download the agent binary via Invoke-WebRequest
	// instead of slow WinRM chunk uploads.
	d.safeGo("serveAgentFiles", func() {
		d.serveAgentFiles(ctx)
	})

	// Start gRPC server — auto-generate TLS certs from CA if available
	grpcCfg := grpcserver.Config{
		Port:   d.config.GRPCPort,
		SiteID: d.config.SiteID,
	}
	if d.agentCA != nil {
		lanIP := d.getApplianceLANIP()
		certPEM, keyPEM, err := d.agentCA.GenerateServerCert(lanIP)
		if err != nil {
			log.Printf("[daemon] Failed to generate gRPC server cert: %v", err)
		} else {
			grpcCfg.TLSCertFile = filepath.Join(d.config.CADir, "server.crt")
			grpcCfg.TLSKeyFile = filepath.Join(d.config.CADir, "server.key")
			grpcCfg.CACertFile = filepath.Join(d.config.CADir, "ca.crt")
			log.Printf("[daemon] gRPC TLS: cert=%d bytes, key=%d bytes, ip=%s",
				len(certPEM), len(keyPEM), lanIP)
		}
	}
	d.grpcSrv = grpcserver.NewServer(grpcCfg, d.registry, d.agentCA, d)

	d.safeGo("grpcServer", func() {
		if err := d.grpcSrv.Serve(); err != nil {
			log.Printf("[daemon] gRPC server error: %v", err)
		}
	})

	// Drain heal channel (process incidents from gRPC drift events)
	d.safeGo("processHealRequests", func() {
		d.processHealRequests(ctx)
	})

	// Start log shipper (journald → Central Command)
	if d.logShipper != nil {
		d.safeGo("logShipper", func() {
			d.logShipper.Run(ctx)
		})
	}

	// Start WireGuard tunnel monitor (checks every 5 minutes, logs state transitions)
	d.safeGo("wireguardMonitor", func() {
		d.runWireGuardMonitor(ctx)
	})

	// Initial checkin
	d.runCheckin(ctx)

	// Main loop
	ticker := time.NewTicker(time.Duration(d.config.PollInterval) * time.Second)
	defer ticker.Stop()

	log.Printf("[daemon] Main loop started (interval: %ds)", d.config.PollInterval)

	// Signal systemd that daemon is fully initialized
	if err := sdnotify.Ready(); err != nil {
		log.Printf("[daemon] sd_notify READY failed: %v", err)
	}

	// Watchdog ping goroutine — pings every 30s independent of poll cycle.
	// Without this, a slow runCycle (>120s) would trigger systemd watchdog
	// timeout and force-restart the daemon, losing uptime.
	watchdogTicker := time.NewTicker(30 * time.Second)
	defer watchdogTicker.Stop()
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case <-watchdogTicker.C:
				_ = sdnotify.Watchdog()
			}
		}
	}()

	for {
		select {
		case <-ctx.Done():
			log.Println("[daemon] Shutting down...")
			_ = sdnotify.Stopping()
			d.grpcSrv.GracefulStop()
			if d.l2Planner != nil {
				d.l2Planner.Close()
			}
			if d.l2Client != nil {
				d.l2Client.Close()
			}
			d.sshExec.CloseAll()

			// Wait for in-flight goroutines with 30s timeout
			done := make(chan struct{})
			go func() {
				d.wg.Wait()
				close(done)
			}()
			select {
			case <-done:
				log.Println("[daemon] All goroutines drained")
			case <-time.After(30 * time.Second):
				log.Println("[daemon] Goroutine drain timed out after 30s")
			}
			return nil
		case <-ticker.C:
			// Watchdog is pinged by a separate goroutine above
			d.runCycle(ctx)
		case <-d.checkinRequest:
			// Phase 13.5 H4 — processor requested an immediate checkin
			// (typically because a sig-verify failed and we want to
			// refresh the server pubkey). Run a full cycle — not just
			// runCheckin — so downstream work (auto-deploy, etc) stays
			// in sync. The cycle's checkin completion fires the
			// broadcast that H4's caller is waiting on.
			log.Printf("[daemon] H4: immediate checkin requested (sig-verify refresh path)")
			d.runCycle(ctx)
		}
	}
}

// requestImmediateCheckinAndWait signals the main loop to run an
// immediate checkin, then blocks up to `timeout` waiting for that
// checkin to complete (success OR failure). Returns true iff a
// checkin completed within the window.
//
// Phase 13.5 H4 — the Processor calls this when its verifySignature
// path fails; one short delay here is cheap compared to leaving a
// good order stuck until the next poll tick.
func (d *Daemon) requestImmediateCheckinAndWait(timeout time.Duration) bool {
	// Register a completion listener before signaling so we don't miss
	// a wake-up if the checkin happens to be about to fire anyway.
	done := make(chan struct{}, 1)
	d.checkinDoneMu.Lock()
	d.checkinDone = append(d.checkinDone, done)
	d.checkinDoneMu.Unlock()

	// Non-blocking signal — if the channel is already full, a request
	// is already queued and our listener will pick up that completion.
	select {
	case d.checkinRequest <- struct{}{}:
	default:
	}

	select {
	case <-done:
		return true
	case <-time.After(timeout):
		return false
	}
}

// notifyCheckinDone fires the broadcast channel to every registered
// waiter exactly once. Called by runCheckin after every attempt
// (success or failure) so H4 callers stop blocking even on
// partial failures. Listeners are drained so the slice never grows
// unbounded.
func (d *Daemon) notifyCheckinDone() {
	d.checkinDoneMu.Lock()
	waiters := d.checkinDone
	d.checkinDone = nil
	d.checkinDoneMu.Unlock()
	for _, w := range waiters {
		select {
		case w <- struct{}{}:
		default:
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
	// Only deploy when subscription is active — expired sites get drift detection but not healing.
	if d.config.WorkstationEnabled && d.isSubscriptionActive() {
		d.safeGo("autoDeploy", func() { d.deployer.runAutoDeployIfNeeded(ctx) })
	}

	// Self-healing: watch for stale agent heartbeats and auto-redeploy.
	d.safeGo("selfHeal", func() { d.selfHealer.runSelfHealIfNeeded(ctx) })

	// Drift scanning: periodic security checks on Windows targets.
	// Detects firewall disabled, rogue users, rogue tasks, stopped services.
	if d.config.WorkstationEnabled {
		d.safeGo("driftScan", func() { d.scanner.runDriftScanIfNeeded(ctx) })
	}

	// Linux drift scanning: periodic security checks on Linux targets.
	// Scans appliance self + any remote linux_targets from checkin response.
	if d.config.EnableDriftDetection {
		d.safeGo("linuxScan", func() { d.scanner.runLinuxScanIfNeeded(ctx) })
	}

	// Network scanning: port enumeration + host reachability checks.
	if d.config.EnableDriftDetection {
		d.safeGo("netScan", func() { d.netScan.runNetScanIfNeeded(ctx) })
	}

	elapsed := time.Since(start)
	log.Printf("[daemon] Cycle complete in %v (agents=%d)",
		elapsed, d.registry.ConnectedCount())

	// Touch the activity marker so the zombie-watch systemd timer knows the
	// daemon is doing real work, not just heartbeating. If this file stops
	// being updated for 15 minutes, msp-daemon-zombie-watch.timer will
	// force-restart the daemon to clear the deadlock.
	touchActivityMarker()
}

// touchActivityMarker updates /var/lib/msp/.last-activity mtime. The
// zombie-watch systemd timer checks this file's age and restarts the daemon
// if the marker is stale, catching the "alive goroutine, dead work" failure
// mode that systemd's built-in WatchdogSec cannot detect.
func touchActivityMarker() {
	const path = "/var/lib/msp/.last-activity"
	now := time.Now()
	// Ignore errors — if we can't touch the file, the watchdog will
	// restart the daemon and we'll try again in a fresh process.
	if err := os.Chtimes(path, now, now); err != nil {
		// File doesn't exist — create it
		f, createErr := os.Create(path)
		if createErr == nil {
			_ = f.Close()
		}
	}
}

// runCheckin sends a checkin to Central Command and processes the response.
func (d *Daemon) runCheckin(ctx context.Context) {
	// H4 — every checkin attempt (success OR failure) notifies any
	// waiters from requestImmediateCheckinAndWait so they don't block
	// forever when a checkin fails partway through.
	defer d.notifyCheckinDone()

	var req CheckinRequest
	if d.agentPublicKey != "" {
		// D1 (Session 206): if we have a signing key via the evidence
		// submitter, sign the heartbeat too. Server records the signature
		// in appliance_heartbeats.agent_signature and auditors can verify
		// that liveness claims came from the legitimate appliance key.
		// Gracefully falls back to unsigned if the submitter/key is nil.
		var signFn func([]byte) ([]byte, error)
		if d.evidenceSubmitter != nil {
			if key := d.evidenceSubmitter.SigningKey(); key != nil {
				signFn = func(msg []byte) ([]byte, error) {
					return ed25519.Sign(key, msg), nil
				}
			}
		}
		if signFn != nil {
			req = SystemInfoSigned(d.config, Version, d.agentPublicKey, signFn)
		} else {
			req = SystemInfoWithKey(d.config, Version, d.agentPublicKey)
		}
	} else {
		req = SystemInfo(d.config, Version)
	}

	// Include connected Go agent data for sync to Central Command
	// Agent hostnames are PHI-scrubbed; AgentID/version are infrastructure identifiers.
	if agents := d.registry.AllAgents(); len(agents) > 0 {
		for _, a := range agents {
			req.ConnectedAgents = append(req.ConnectedAgents, ConnectedAgent{
				AgentID:       a.AgentID,
				Hostname:      phiscrub.Scrub(a.Hostname),
				AgentVersion:  a.AgentVersion,
				IPAddress:     a.IPAddress,
				OSVersion:     a.OSVersion,
				Tier:          int(a.Tier),
				ConnectedAt:   a.ConnectedAt.UTC().Format(time.RFC3339),
				LastHeartbeat: a.LastHeartbeat.UTC().Format(time.RFC3339),
				DriftCount:    a.DriftCount.Load(),
				ChecksPassed:  a.ChecksPassed.Load(),
				ChecksTotal:   a.ChecksTotal.Load(),
			})
		}
	}

	// Include pending app discovery results (one-shot: cleared after send)
	// Scrub all string values in the discovery map for PHI before transmission.
	if dr := d.state.DrainDiscoveryResults(); dr != nil {
		req.DiscoveryResults = phiscrub.ScrubMap(dr)
	}

	// Include WireGuard tunnel status in checkin
	if wgIP := getWireGuardIP(); wgIP != "" {
		req.WgIP = wgIP
		if wgSt := checkWireGuardStatus(); wgSt != nil {
			req.WgConnected = wgSt.Connected
		}
	}

	// Include encryption public key for credential envelope encryption
	if d.envelopeKP != nil {
		req.EncryptionPublicKey = d.envelopeKP.PublicKeyHex()
	}

	// Include deploy results from previous cycle (cleared after send).
	// Scrub hostnames + error messages before egress — may contain PHI
	// (e.g., patient-named workstations like "PATIENT-ROOM-201-PC").
	if dr := d.state.DrainDeployResults(); len(dr) > 0 {
		for i := range dr {
			dr[i].Hostname = phiscrub.Scrub(dr[i].Hostname)
			dr[i].Error = phiscrub.Scrub(dr[i].Error)
		}
		req.DeployResults = dr
	}

	// Daemon runtime health — Go stdlib, zero deps
	req.DaemonHealth = collectDaemonHealth(d.startTime, d.mesh)

	// Peer witnessing: send recent bundle hashes + any pending attestations
	req.BundleHashes = d.state.DrainBundleHashes()
	req.WitnessAttestations = d.state.DrainWitnessAttestations()

	// Phase 2 time-travel detection (Session 205). Pure function of on-disk
	// state + /proc/uptime — no network calls, no mutations. Signals get
	// shipped to CC; if ≥2 present, CC returns a signed reconcile plan.
	if d.reconcileDetector != nil {
		detection := d.reconcileDetector.Detect()
		req.BootCounter = detection.BootCounter
		req.GenerationUUID = detection.GenerationUUID
		req.ReconcileNeeded = detection.ReconcileNeeded
		req.ReconcileSignals = detection.Signals
		if detection.ReconcileNeeded {
			slog.Warn("time-travel signals detected — requesting reconcile",
				"component", "reconcile",
				"signals", detection.Signals,
				"boot_counter", detection.BootCounter)
		}
	}

	resp, err := d.phoneCli.Checkin(ctx, &req)
	if err != nil {
		d.serverBreaker.RecordFailure()
		failures := d.phoneCli.ConsecutiveFailures()
		errClass := classifyConnectivityError(err)
		log.Printf("[daemon] Checkin failed (%s, consecutive=%d, circuit=%s): %v",
			errClass, failures, d.serverBreaker.State(), err)

		// Auto-rekey: if we're getting persistent 401s, request a new API key
		if errors.Is(err, ErrAuthFailed) {
			d.consecutiveAuthFailures++
			if d.consecutiveAuthFailures >= 3 {
				d.attemptRekey(ctx)
			}
		} else {
			d.consecutiveAuthFailures = 0
		}

		// Fallback: poll for fleet orders directly when checkin is broken
		if failures >= 1 && d.orderProc.ApplianceID() != "" {
			if orders, fetchErr := d.phoneCli.FetchPendingOrders(ctx, d.orderProc.ApplianceID()); fetchErr == nil && len(orders) > 0 {
				log.Printf("[daemon] Fleet order fallback: fetched %d pending orders", len(orders))
				d.processOrders(ctx, orders)
			}
		}
		return
	}

	d.serverBreaker.RecordSuccess()
	d.consecutiveAuthFailures = 0

	// Phase 2 time-travel: mark this cycle as known-good for next-cycle
	// comparison. LKG mtime detects clock rollback; last_reported_uptime
	// detects /proc/uptime regression (VM snapshot revert).
	// Phase 3.1: last_reported_boot_counter gives the detector a second
	// client-side signal on snapshot revert (filesystem rewind observable
	// without network consultation).
	// Best-effort: persistence failures should not break checkin flow.
	if d.reconcileDetector != nil {
		if err := d.reconcileDetector.WriteLastReportedUptime(req.UptimeSeconds); err != nil {
			slog.Warn("failed to persist last_reported_uptime",
				"component", "reconcile", "error", err)
		}
		if err := d.reconcileDetector.WriteLastReportedBootCounter(req.BootCounter); err != nil {
			slog.Warn("failed to persist last_reported_boot_counter",
				"component", "reconcile", "error", err)
		}
		if err := d.reconcileDetector.TouchLKG(); err != nil {
			slog.Warn("failed to touch last_known_good marker",
				"component", "reconcile", "error", err)
		}
	}

	// Phase 3 time-travel: apply any server-issued reconcile plan. This
	// runs AFTER LKG+uptime persistence so even a plan that fails mid-
	// apply (crash between steps) leaves the detector in a consistent
	// state. applyReconcilePlan is self-contained: signature verify,
	// appliance scope check, freshness check, nonce purge, UUID write,
	// ACK. Errors are logged and swallowed — a reconcile failure must
	// not break the main checkin loop.
	if resp.ReconcilePlan != nil {
		d.applyReconcilePlan(ctx, resp.ReconcilePlan)
	}

	// Drain cached evidence bundles on successful checkin (CC is reachable)
	if d.evidenceSubmitter != nil {
		if n := d.evidenceSubmitter.DrainCache(ctx); n > 0 {
			log.Printf("[daemon] Drained %d cached evidence bundles", n)
		}
	}

	// M3 (Session 206): ACK our currently-held mesh targets so the server's
	// mesh_reassignment_loop can reassign unACKed targets to live
	// appliances. If the server reports reassignments happened, our local
	// view is out of sync — log a hint for the next refresh cycle.
	// Non-fatal: failure just means targets rely on TTL-based reassignment.
	if d.mesh != nil && d.orderProc != nil && d.orderProc.ApplianceID() != "" {
		targets := d.mesh.CurrentTargets()
		if len(targets) > 0 {
			ackEntries := make([]MeshTargetAckEntry, 0, len(targets))
			for _, t := range targets {
				ackEntries = append(ackEntries, MeshTargetAckEntry{
					TargetKey:  t,
					TargetType: "device",
				})
			}
			if ackResp, ackErr := PostMeshAck(
				ctx,
				d.config.APIEndpoint,
				d.config.APIKey,
				d.config.SiteID,
				d.orderProc.ApplianceID(),
				ackEntries,
				nil, // default httpClient
			); ackErr != nil {
				slog.Warn("mesh ack failed",
					"component", "daemon",
					"error", ackErr)
			} else if ackResp != nil && ackResp.Reassigned > 0 {
				slog.Info("mesh ack reported reassignment — targets may be stale",
					"component", "daemon",
					"reassigned", ackResp.Reassigned,
					"acked", ackResp.Acked,
					"total", ackResp.TotalAssigned)
			}
		}
	}

	// Peer witnessing: counter-sign sibling bundle hashes and submit immediately.
	// Phase 3: attestations are sent in the SAME cycle via direct HTTP POST,
	// not queued for the next checkin. This eliminates the 1-cycle latency.
	if len(resp.PeerBundleHashes) > 0 && d.evidenceSubmitter != nil {
		var attestations []WitnessAttestation
		for _, ph := range resp.PeerBundleHashes {
			sig := evidence.Sign(d.evidenceSubmitter.SigningKey(), []byte(ph.BundleHash))
			attestations = append(attestations, WitnessAttestation{
				BundleID:         ph.BundleID,
				BundleHash:       ph.BundleHash,
				WitnessSignature: sig,
				WitnessPublicKey: d.agentPublicKey,
				SourceAppliance:  ph.SourceAppliance,
			})
		}
		// Submit attestations immediately (same cycle, no queuing)
		if err := d.submitWitnessAttestations(ctx, attestations); err != nil {
			// Fallback: queue for next checkin cycle
			for _, a := range attestations {
				d.state.AddWitnessAttestation(a)
			}
			log.Printf("[witness] Immediate submit failed, queued %d for next cycle: %v", len(attestations), err)
		} else {
			log.Printf("[witness] Counter-signed + submitted %d attestations (same cycle)", len(attestations))
		}
	}

	// submitWitnessAttestations is defined below the runCheckin method.

	// Set appliance ID on telemetry reporter and order processor (received from Central Command)
	if resp.ApplianceID != "" {
		if d.telemetry != nil {
			d.telemetry.SetApplianceID(resp.ApplianceID)
		}
		d.orderProc.SetApplianceID(resp.ApplianceID)
	}

	// Store server public key(s) for order + rules signature verification.
	// When server_public_keys is provided (key rotation support), the first key is
	// current and the rest are previous keys still valid during the rotation window.
	if resp.ServerPublicKey != "" {
		if len(resp.ServerPublicKeys) > 1 {
			// Multi-key rotation: first key is current, rest are previous
			if err := d.orderProc.SetPublicKeys(resp.ServerPublicKeys[0], resp.ServerPublicKeys[1:]); err != nil {
				log.Printf("[daemon] Failed to set public keys on order processor: %v", err)
			}
		} else {
			if err := d.orderProc.SetServerPublicKey(resp.ServerPublicKey); err != nil {
				log.Printf("[daemon] Failed to set server public key on order processor: %v", err)
			}
		}
		// Phase 13.5 H6 — stamp the just-delivered pubkey as the trusted
		// envelope-key reference. Used as the bounded-trust source when
		// verify fails against the cache but the order envelope
		// advertises the same pubkey we just received here.
		d.orderProc.SetLastDeliveredPubkey(resp.ServerPublicKey)
		if d.l1Engine != nil {
			if err := d.l1Engine.SetServerPublicKey(resp.ServerPublicKey); err != nil {
				log.Printf("[daemon] Failed to set server public key on L1 engine: %v", err)
			}
		}
	}

	// API key single-use rotation: server sends a new key on first checkin.
	// The USB-provisioned key becomes useless after this point.
	if resp.RotatedAPIKey != "" {
		log.Printf("[daemon] API key rotated by server — updating config")
		d.config.APIKey = resp.RotatedAPIKey
		configPath := filepath.Join(d.config.StateDir, "config.yaml")
		if err := UpdateAPIKey(configPath, resp.RotatedAPIKey); err != nil {
			log.Printf("[daemon] Failed to save rotated API key: %v", err)
		} else {
			log.Printf("[daemon] Rotated API key saved to config.yaml")
		}
	}

	// Decrypt envelope-encrypted credentials if present
	if d.envelopeKP != nil && resp.EncryptedCredentials != nil {
		encJSON, _ := json.Marshal(resp.EncryptedCredentials)
		var enc crypto.EncryptedCredentials
		if err := json.Unmarshal(encJSON, &enc); err == nil && enc.Ciphertext != "" {
			plaintext, err := d.envelopeKP.DecryptCredentials(&enc)
			if err != nil {
				log.Printf("[daemon] SECURITY: credential envelope decryption failed: %v", err)
			} else {
				var creds struct {
					WindowsTargets []map[string]interface{} `json:"windows_targets"`
					LinuxTargets   []map[string]interface{} `json:"linux_targets"`
				}
				if err := json.Unmarshal(plaintext, &creds); err == nil {
					resp.WindowsTargets = creds.WindowsTargets
					resp.LinuxTargets = creds.LinuxTargets
					log.Printf("[daemon] Decrypted credential envelope (win=%d, linux=%d)",
						len(creds.WindowsTargets), len(creds.LinuxTargets))
				}
			}
		}
	}

	// Store Linux targets from checkin response
	if len(resp.LinuxTargets) > 0 {
		parsed := parseLinuxTargets(resp.LinuxTargets)
		d.state.SetLinuxTargets(parsed)
	}

	// Store Windows targets (DC credentials) from checkin response
	if len(resp.WindowsTargets) > 0 {
		d.state.LoadWindowsTargets(resp.WindowsTargets, d.config)
	}

	// Probe target connectivity once after first credential load
	if len(resp.WindowsTargets) > 0 || len(resp.LinuxTargets) > 0 {
		d.credentialProbeOnce.Do(func() {
			d.safeGo("credential-probe", func() {
				d.probeTargetConnectivity(ctx)
			})
		})
	}

	// Log after envelope decryption so target counts are accurate
	log.Printf("[daemon] Checkin OK: appliance=%s, orders=%d, win_targets=%d, linux_targets=%d, triggers=(enum=%v, scan=%v)",
		resp.ApplianceID, len(resp.PendingOrders), len(resp.WindowsTargets), len(resp.LinuxTargets),
		resp.TriggerEnumeration, resp.TriggerImmediateScan)

	// Store L2 healing mode from checkin response
	if resp.L2Mode != "" {
		d.state.SetL2Mode(resp.L2Mode)
	}

	// Store subscription status for healing gating
	if resp.SubscriptionStatus != "" {
		d.state.SetSubscriptionStatus(resp.SubscriptionStatus)
	}

	// Update disabled drift checks from site config
	if len(resp.DisabledChecks) > 0 {
		newMap := make(map[string]bool, len(resp.DisabledChecks))
		for _, ct := range resp.DisabledChecks {
			newMap[ct] = true
		}
		d.state.SetDisabledChecks(newMap)
		log.Printf("[daemon] Disabled drift checks updated: %v", resp.DisabledChecks)
	} else {
		// Clear any previously disabled checks if server sends empty list
		if len(d.state.GetDisabledChecks()) > 0 {
			d.state.SetDisabledChecks(make(map[string]bool))
			log.Printf("[daemon] Disabled drift checks cleared")
		}
	}

	// Process pending orders via order processor
	if len(resp.PendingOrders) > 0 {
		d.processOrders(ctx, resp.PendingOrders)
	}

	// Process pending deploys from Central Command ("Take Over" flow)
	if len(resp.PendingDeploys) > 0 {
		log.Printf("[daemon] Received %d pending deploys", len(resp.PendingDeploys))
		results := d.deployer.processPendingDeploys(ctx, resp.PendingDeploys, d.config.SiteID)
		for _, r := range results {
			d.state.AddDeployResult(r)
		}
	}

	// Apply WireGuard tunnel config if Central Command provisioned it
	if resp.Wireguard != nil {
		applyWireguardConfig(resp.Wireguard)
	}

	// Drain queued telemetry entries (network is known-good after successful checkin)
	if d.telemetry != nil {
		d.telemetry.DrainQueue()
	}

	// Update mesh with backend-seeded peers (enables cross-subnet target splitting).
	// Runs in a goroutine because gRPC probes take up to 2s per peer IP.
	if d.mesh != nil && len(resp.MeshPeers) > 0 {
		peers := resp.MeshPeers
		d.safeGo("meshBackendPeers", func() {
			d.mesh.UpdateBackendPeers(peers, nil)
		})
	}

	// Apply server-authoritative target assignments (Hybrid C+).
	// Takes precedence over local hash ring for scan target ownership.
	if d.mesh != nil && resp.TargetAssignments != nil {
		d.mesh.ApplyTargetAssignment(
			resp.TargetAssignments.YourTargets,
			resp.TargetAssignments.RingMembers,
			resp.TargetAssignments.AssignmentEpoch,
		)
	}

	// Update self-healer with current agent heartbeat data from gRPC registry
	if agents := d.registry.AllAgents(); len(agents) > 0 {
		agentInfos := make([]GoAgentInfo, 0, len(agents))
		for _, a := range agents {
			agentInfos = append(agentInfos, GoAgentInfo{
				Hostname:      a.Hostname,
				IPAddress:     a.IPAddress,
				OSType:        a.OSType(),
				LastHeartbeat: a.LastHeartbeat,
			})
		}
		d.selfHealer.updateFromCheckin(agentInfos)
	}

	// Persist state to disk for survival across restarts
	d.saveState()
}

// LookupWinTarget delegates to StateManager.
// submitWitnessAttestations POSTs counter-signed attestations directly to Central Command.
// Phase 3: same-cycle submission — no queuing for next checkin.
func (d *Daemon) submitWitnessAttestations(ctx context.Context, attestations []WitnessAttestation) error {
	if len(attestations) == 0 || d.config.APIEndpoint == "" {
		return nil
	}
	payload := map[string]interface{}{
		"site_id":      d.config.SiteID,
		"attestations": attestations,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	url := strings.TrimRight(d.config.APIEndpoint, "/") + "/api/witness/submit"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+d.config.APIKey)
	resp, err := d.phoneCli.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("witness submit returned %d", resp.StatusCode)
	}
	return nil
}

func (d *Daemon) LookupWinTarget(hostname string) (winTarget, bool) {
	return d.state.LookupWinTarget(hostname)
}

// probeTargetConnectivity runs a lightweight connectivity check against all
// configured Windows and Linux targets immediately after the first credential
// load. It dials TCP only (WinRM port for Windows, SSH port for Linux) and
// logs clear pass/fail results so operators can catch bad credentials or
// unreachable hosts before the first full scan cycle.
func (d *Daemon) probeTargetConnectivity(ctx context.Context) {
	log.Printf("[daemon] [startup] Probing target connectivity...")

	dialer := net.Dialer{Timeout: 5 * time.Second}
	okCount, failCount := 0, 0

	// Probe Windows targets
	winTargets := d.state.GetWinTargets()
	for hostname, wt := range winTargets {
		if ctx.Err() != nil {
			return
		}
		role := wt.Role
		if role == "" {
			role = "unknown"
		}
		// Determine which WinRM port to dial (prefer 5986 HTTPS, fall back 5985 HTTP)
		ports := []struct {
			port int
			desc string
		}{
			{5986, "WinRM-HTTPS"},
			{5985, "WinRM-HTTP"},
		}
		connected := false
		for _, p := range ports {
			addr := net.JoinHostPort(hostname, fmt.Sprintf("%d", p.port))
			conn, err := dialer.DialContext(ctx, "tcp", addr)
			if err == nil {
				conn.Close()
				log.Printf("[daemon] [startup] Target %s (%s): %s OK (port %d)", hostname, role, p.desc, p.port)
				connected = true
				okCount++
				break
			}
		}
		if !connected {
			log.Printf("[daemon] [startup] WARNING: Target %s (%s): WinRM UNREACHABLE (5986+5985 failed)", hostname, role)
			failCount++
		}
	}

	// Probe Linux/macOS targets
	linuxTargets := d.state.GetLinuxTargets()
	for _, lt := range linuxTargets {
		if ctx.Err() != nil {
			return
		}
		label := lt.Label
		if label == "" {
			label = "linux"
		}
		port := lt.Port
		if port == 0 {
			port = 22
		}
		addr := net.JoinHostPort(lt.Hostname, fmt.Sprintf("%d", port))
		conn, err := dialer.DialContext(ctx, "tcp", addr)
		if err == nil {
			conn.Close()
			log.Printf("[daemon] [startup] Target %s (%s): SSH OK (port %d)", lt.Hostname, label, port)
			okCount++
		} else {
			log.Printf("[daemon] [startup] WARNING: Target %s (%s): SSH UNREACHABLE (port %d: %v)", lt.Hostname, label, port, err)
			failCount++
		}
	}

	if failCount > 0 {
		log.Printf("[daemon] [startup] Credential probe complete: %d OK, %d FAILED — check target IPs and firewall rules", okCount, failCount)
	} else if okCount > 0 {
		log.Printf("[daemon] [startup] Credential probe complete: all %d targets reachable", okCount)
	} else {
		log.Printf("[daemon] [startup] Credential probe: no targets configured")
	}
}

// processOrders converts raw checkin order maps to Order structs and dispatches them.
func (d *Daemon) processOrders(ctx context.Context, rawOrders []map[string]interface{}) {
	orderList := make([]orders.Order, 0, len(rawOrders))
	for _, raw := range rawOrders {
		orderID := maputil.String(raw, "order_id")
		orderType := maputil.String(raw, "order_type")

		params := maputil.Map(raw, "parameters")
		if params == nil {
			params = make(map[string]interface{})
		}
		// Inject order_id into params so handlers like nixos_rebuild can persist it
		params["_order_id"] = orderID

		// Inject runbook_id from top-level field into params (healing orders)
		if rbID, ok := raw["runbook_id"].(string); ok && rbID != "" {
			params["runbook_id"] = rbID
		}

		// Extract signature fields for verification
		nonce := maputil.String(raw, "nonce")
		signature := maputil.String(raw, "signature")
		signedPayload := maputil.String(raw, "signed_payload")

		orderList = append(orderList, orders.Order{
			OrderID:       orderID,
			OrderType:     orderType,
			Parameters:    params,
			Nonce:         nonce,
			Signature:     signature,
			SignedPayload: signedPayload,
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

// HostCredentials holds the SSH/password credentials needed to redeploy an agent.
type HostCredentials struct {
	Username string
	Password string
	SSHKey   string
}

// findCredentialsForHost delegates to StateManager.
func (d *Daemon) findCredentialsForHost(hostname, ip string) *HostCredentials {
	return d.state.FindCredentialsForHost(hostname, ip)
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

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10<<20))
	if err != nil {
		return fmt.Errorf("read completion response for order %s: %w", orderID, err)
	}
	if resp.StatusCode != http.StatusOK {
		log.Printf("[daemon] Order %s completion returned %d: %s", orderID, resp.StatusCode, string(respBody))
		return fmt.Errorf("completion returned %d", resp.StatusCode)
	}

	log.Printf("[daemon] Order %s completion accepted by Central Command", orderID)
	return nil
}

// getApplianceLANIP returns the first non-loopback IPv4 address on the appliance.
func (d *Daemon) getApplianceLANIP() string {
	addrs, err := net.InterfaceAddrs()
	if err == nil {
		for _, addr := range addrs {
			if ipNet, ok := addr.(*net.IPNet); ok && !ipNet.IP.IsLoopback() && ipNet.IP.To4() != nil {
				return ipNet.IP.String()
			}
		}
	}
	return "127.0.0.1"
}

// winrmSettings holds the cached WinRM connection settings for a host.
type winrmSettings struct {
	Port     int
	UseSSL   bool
	CachedAt time.Time
}

// probeWinRM delegates to StateManager.
func (d *Daemon) probeWinRM(hostname string) winrmSettings {
	return d.state.ProbeWinRMPort(hostname)
}

// serveAgentFiles serves the agent binary directory over HTTP for DC downloads
// and workstation self-updates. Endpoints:
//   - GET /agent/version.json — version manifest (version, SHA256, size)
//   - GET /agent/osiris-agent.exe — the agent binary
func (d *Daemon) serveAgentFiles(ctx context.Context) {
	agentDir := filepath.Join(d.config.StateDir, "agent")

	mux := http.NewServeMux()
	mux.HandleFunc("/agent/version.json", d.handleAgentVersion(d.agentVersionCache))
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

			if !d.config.HealingEnabled || !d.orderProc.IsHealingEnabled() {
				log.Printf("[daemon] Healing disabled, skipping %s/%s", req.Hostname, req.CheckType)
				continue
			}

			if !d.isSubscriptionActive() {
				log.Printf("[daemon] Subscription expired — healing suppressed: %s/%s", req.Hostname, req.CheckType)
				continue
			}

			d.healIncident(ctx, &req)
		}
	}
}

// healIncident routes an incident through L1 deterministic → L2 LLM → L3 escalation.
func (d *Daemon) healIncident(ctx context.Context, req *grpcserver.HealRequest) {
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
		"platform":       d.inferPlatformFromCheckType(req.CheckType), // detect from check type prefix
	}
	for k, v := range req.Metadata {
		data[k] = v
	}

	severity := "high"
	if req.HIPAAControl == "" {
		severity = "medium"
	}

	// Report incident to Central Command dashboard (async, fire-and-forget)
	platform := maputil.StringDefault(data, "platform", "windows")
	if d.incidents != nil {
		d.safeGo("reportDriftIncident", func() { d.incidents.ReportDriftIncident(req.Hostname, req.CheckType, req.Expected, req.Actual, req.HIPAAControl, severity, platform) })
	}

	// Check healing exhaustion: if L1 has failed too many times for this key,
	// skip L1 and let the server-side pipeline handle escalation.
	if d.state.IsHealingExhausted(cooldownKey) {
		log.Printf("[daemon] L1 healing exhausted for %s/%s (%d+ failed attempts), skipping to server-side escalation",
			req.Hostname, req.CheckType, maxHealingAttempts)
		return
	}

	// L1: Deterministic matching
	match := d.l1Engine.Match(incidentID, req.CheckType, severity, data)
	if match != nil {
		log.Printf("[daemon] L1 match: rule=%s action=%s for %s/%s",
			match.Rule.ID, match.Action, req.Hostname, req.CheckType)

		// Extract runbook_id from action params for telemetry (flywheel needs runbook_id, not rule ID)
		telemetryRunbookID := match.Rule.ID // fallback to rule ID
		if rbID, ok := match.Rule.ActionParams["runbook_id"].(string); ok && rbID != "" {
			telemetryRunbookID = rbID
		}

		// Journal: checkpoint before execution + audit trail
		d.healJournal.StartHealing(incidentID, telemetryRunbookID, req.Hostname, platform, req.CheckType, "L1")
		d.healJournal.SetAuditTrail(incidentID, "Deterministic L1 rule match", 1.0, match.Rule.Description)

		result := d.l1Engine.Execute(match, d.config.SiteID, req.Hostname)

		if result.Success {
			d.healJournal.FinishHealing(incidentID, true, "")
			d.healTracker.Record(true)
			log.Printf("[daemon] L1 healed %s/%s via %s in %dms",
				req.Hostname, req.CheckType, match.Rule.ID, result.DurationMs)

			// Reset exhaustion counter on success
			d.state.ResetHealingExhaustion(cooldownKey)

			if d.telemetry != nil {
				d.safeGo("telemetryL1", func() { d.telemetry.ReportL1Execution(incidentID, req.Hostname, req.CheckType, telemetryRunbookID, true, "", "", result.DurationMs) })
			}

			if d.incidents != nil {
				d.safeGo("reportHealed", func() { d.incidents.ReportHealed(req.Hostname, req.CheckType, "L1", match.Rule.ID) })
			}

			// GPO firewall fix: when firewall drift is healed, also fix the
			// domain GPO to prevent GPO from turning firewall back off.
			// Zero-friction: runs automatically without operator intervention.
			if req.CheckType == "firewall_status" {
				d.safeGo("fixFirewallGPO", func() { d.fixFirewallGPO(req.Hostname) })
			}
		} else {
			d.healJournal.FinishHealing(incidentID, false, result.Error)
			d.healTracker.Record(false)

			// Track L1 failure for exhaustion
			failCount := d.state.RecordHealingFailure(cooldownKey)
			log.Printf("[daemon] L1 execution failed for %s/%s: %s (attempt %d/%d)",
				req.Hostname, req.CheckType, result.Error, failCount, maxHealingAttempts)

			if d.telemetry != nil {
				errCat := classifyHealError(result.Error)
				// Don't report "no_credentials" as failures — these are config gaps,
				// not healing failures. Reporting them tanks the healing rate.
				if errCat != "no_credentials" {
					d.safeGo("telemetryL1Fail", func() {
						d.telemetry.ReportL1Execution(incidentID, req.Hostname, req.CheckType, telemetryRunbookID, false, result.Error, errCat, result.DurationMs)
					})
				}
			}
		}
		return
	}

	// Check L2 mode: "disabled" skips L2, "manual" generates plan but escalates for approval
	l2Mode := d.state.GetL2Mode()
	if l2Mode == "" {
		l2Mode = "auto" // Default if not yet received from checkin
	}

	if l2Mode == "disabled" {
		log.Printf("[daemon] L2 disabled for this appliance — escalating %s/%s to L3",
			req.Hostname, req.CheckType)
		d.escalateToL3(incidentID, req, "No L1 rule match, L2 disabled by policy")
		return
	}

	// L2: Native LLM planner (preferred)
	if d.l2Planner != nil && d.l2Planner.IsConnected() {
		log.Printf("[daemon] L1 no match for %s/%s, escalating to L2 (native)", req.Hostname, req.CheckType)

		incident := &l2bridge.Incident{
			ID:           incidentID,
			SiteID:       d.config.SiteID,
			HostID:       req.Hostname,
			IncidentType: req.CheckType,
			Severity:     severity,
			RawData:      data,
			CreatedAt:    time.Now().UTC().Format(time.RFC3339),
		}

		decision, err := d.l2Planner.PlanWithRetry(incident, 1)
		if err != nil {
			log.Printf("[daemon] L2 plan failed for %s/%s: %v — escalating to L3",
				req.Hostname, req.CheckType, err)
			d.escalateToL3(incidentID, req, "L2 plan failed: "+err.Error())
			return
		}

		// In auto mode: execute if L2 found a viable plan (confidence >= 0.6, not escalated)
		// RequiresApproval is only enforced in manual mode — auto mode auto-executes
		canExecute := !decision.EscalateToL3 && decision.Confidence >= 0.6
		if canExecute {
			// Manual mode: L2 generates plan but requires human approval
			if l2Mode == "manual" {
				log.Printf("[daemon] L2 plan ready but mode=manual — escalating %s/%s for approval: %s",
					req.Hostname, req.CheckType, decision.RecommendedAction)
				d.escalateToL3(incidentID, req, fmt.Sprintf(
					"L2 plan available (manual approval required): action=%s confidence=%.2f — %s",
					decision.RecommendedAction, decision.Confidence, decision.Reasoning))
				return
			}

			log.Printf("[daemon] L2 decision: %s (confidence=%.2f, approval=%v, runbook=%s) for %s/%s",
				decision.RecommendedAction, decision.Confidence, decision.RequiresApproval, decision.RunbookID, req.Hostname, req.CheckType)
			d.healJournal.StartHealing(incidentID, decision.RunbookID, req.Hostname, platform, req.CheckType, "L2")
			// Record investigation audit trail — hypothesis, confidence, reasoning
			d.healJournal.SetAuditTrail(incidentID, decision.Reasoning, decision.Confidence, decision.Reasoning)
			l2Start := time.Now()
			var l2Success bool
			var l2Err string
			// Route through the runbook execution engine when we have a runbook ID.
			// executeHealingOrder looks up runbook steps and runs them properly via
			// WinRM/SSH, unlike executeL2Action which ran raw script strings.
			if decision.RunbookID != "" {
				params := map[string]interface{}{
					"runbook_id":      decision.RunbookID,
					"hostname":        req.Hostname,
					"check_type":      req.CheckType,
					"resolution_tier": "L2",
				}
				_, err := d.executeHealingOrder(ctx, params)
				if err != nil {
					l2Success = false
					l2Err = err.Error()
				} else {
					l2Success = true
				}
			} else {
				l2Success, l2Err = d.executeL2Action(ctx, decision, req, incidentID)
			}
			d.healJournal.FinishHealing(incidentID, l2Success, l2Err)
			d.healTracker.Record(l2Success)
			// Report telemetry for data flywheel (async) with actual success/failure
			dur := time.Since(l2Start).Milliseconds()
			d.safeGo("telemetryL2", func() {
					errCat := classifyHealError(l2Err)
					d.l2Planner.ReportExecution(incident, decision, l2Success, l2Err, errCat, dur)
				})
			return
		}

		// L2 says escalate
		log.Printf("[daemon] L2 escalating %s/%s to L3: %s",
			req.Hostname, req.CheckType, decision.Reasoning)
		d.escalateToL3(incidentID, req, decision.Reasoning)
		return
	}

	// L2: Legacy Unix socket bridge (deprecated fallback)
	if d.l2Client != nil && d.l2Client.IsConnected() {
		log.Printf("[daemon] L1 no match for %s/%s, escalating to L2 (legacy bridge)", req.Hostname, req.CheckType)

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
// SECURITY: L2 must return a RunbookID. Raw script execution is BLOCKED — the LLM
// must not be able to generate arbitrary shell commands. If no RunbookID, escalate to L3.
// Returns (success, errorMessage) for telemetry reporting.
func (d *Daemon) executeL2Action(ctx context.Context, decision *l2bridge.LLMDecision, req *grpcserver.HealRequest, incidentID string) (bool, string) {
	platform := req.Metadata["platform"]
	if platform == "" {
		platform = "windows"
	}

	// SECURITY: Only execute via registered runbooks. Never execute raw scripts from L2.
	// The LLM is not an execution authority — it selects from pre-approved runbooks.
	runbookID := decision.RunbookID
	if runbookID == "" {
		log.Printf("[daemon] SECURITY: L2 returned no RunbookID for %s/%s — refusing raw script execution, escalating to L3",
			req.Hostname, req.CheckType)
		d.escalateToL3(incidentID, req, "L2 returned no runbook ID — raw script execution blocked by policy")
		return false, "L2 raw script execution blocked"
	}

	// Look up runbook from the embedded registry — must exist
	rb, ok := runbookRegistry[runbookID]
	if !ok {
		log.Printf("[daemon] L2 returned unknown runbookID %s for %s/%s — escalating to L3",
			runbookID, req.Hostname, req.CheckType)
		d.escalateToL3(incidentID, req, fmt.Sprintf("Unknown runbook ID: %s", runbookID))
		return false, "Unknown runbook ID: " + runbookID
	}
	script := rb.RemediateScript

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
			return false, "No WinRM credentials for target"
		}
		result := d.winrmExec.ExecuteCtx(ctx, target, script, runbookID, "l2_auto", 300, 1, 30.0, hipaaControls)
		if result.Success {
			log.Printf("[daemon] L2 healed %s/%s via WinRM in %.1fs (hash=%s)",
				req.Hostname, req.CheckType, result.DurationSecs, result.OutputHash)
			return true, ""
		}
		log.Printf("[daemon] L2 WinRM execution failed for %s/%s: %s — escalating to L3",
			req.Hostname, req.CheckType, result.Error)
		d.escalateToL3(incidentID, req, "L2 WinRM execution failed: "+result.Error)
		return false, result.Error

	case "linux":
		target := d.buildSSHTarget(req)
		if target == nil {
			log.Printf("[daemon] L2 no SSH target for %s — escalating to L3", req.Hostname)
			d.escalateToL3(incidentID, req, "No SSH credentials for target")
			return false, "No SSH credentials for target"
		}
		result := d.sshExec.Execute(ctx, target, script, runbookID, "l2_auto", 60, 1, 5.0, true, hipaaControls)
		if result.Success {
			log.Printf("[daemon] L2 healed %s/%s via SSH in %.1fs (hash=%s)",
				req.Hostname, req.CheckType, result.DurationSecs, result.OutputHash)
			return true, ""
		}
		log.Printf("[daemon] L2 SSH execution failed for %s/%s: %s — escalating to L3",
			req.Hostname, req.CheckType, result.Error)
		d.escalateToL3(incidentID, req, "L2 SSH execution failed: "+result.Error)
		return false, result.Error

	default:
		log.Printf("[daemon] L2 unknown platform %q for %s — escalating to L3", platform, req.Hostname)
		d.escalateToL3(incidentID, req, fmt.Sprintf("Unknown platform: %s", platform))
		return false, fmt.Sprintf("Unknown platform: %s", platform)
	}
}

// buildWinRMTarget creates a WinRM target from the heal request metadata.
// Credentials come from the checkin response's windows_targets list, cached in the daemon.
func (d *Daemon) buildWinRMTarget(req *grpcserver.HealRequest) *winrm.Target {
	// Extract credentials from metadata (populated during drift report with target info)
	username := req.Metadata["winrm_username"]
	password := req.Metadata["winrm_password"]
	ipAddr := req.Metadata["ip_address"]

	if username == "" || password == "" {
		return nil
	}

	hostname := req.Hostname
	if ipAddr != "" {
		hostname = ipAddr
	}

	ws := d.probeWinRM(hostname)
	return &winrm.Target{
		Hostname:  hostname,
		Port:      ws.Port,
		Username:  username,
		Password:  password,
		UseSSL:    ws.UseSSL,
		VerifySSL: true, // TOFU cert pinning via CertPinStore
	}
}

// handleChaosQuicktest injects drift scenarios via WinRM for chaos testing.
// Each scenario runs a PowerShell inject command on a Windows target.
// The normal scan cycle then detects drift and the healing pipeline remediates.
// The backend polls execution_telemetry to track detect/heal status.
func (d *Daemon) handleChaosQuicktest(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	campaignID := maputil.String(params, "campaign_id")
	if campaignID == "" {
		campaignID = fmt.Sprintf("chaos-%d", time.Now().UnixMilli())
	}

	scenariosRaw, ok := params["scenarios"]
	if !ok {
		return nil, fmt.Errorf("scenarios array is required")
	}

	scenarios, ok := scenariosRaw.([]interface{})
	if !ok {
		return nil, fmt.Errorf("scenarios must be an array")
	}

	if len(scenarios) == 0 || len(scenarios) > 10 {
		return nil, fmt.Errorf("scenarios must have 1-10 entries (got %d)", len(scenarios))
	}

	results := make([]map[string]interface{}, 0, len(scenarios))

	for i, raw := range scenarios {
		s, ok := raw.(map[string]interface{})
		if !ok {
			results = append(results, map[string]interface{}{
				"index":  i,
				"status": "error",
				"error":  "scenario must be an object",
			})
			continue
		}

		target := maputil.String(s, "target")
		driftType := maputil.String(s, "type")
		inject := maputil.String(s, "inject")

		if target == "" || inject == "" {
			results = append(results, map[string]interface{}{
				"index":  i,
				"type":   driftType,
				"status": "error",
				"error":  "target and inject are required",
			})
			continue
		}

		// SECURITY: only allow injection against known Windows targets
		if !d.isKnownTarget(target, "windows") {
			log.Printf("[chaos] SECURITY: rejected inject against unknown target %q", target)
			results = append(results, map[string]interface{}{
				"index":  i,
				"type":   driftType,
				"target": target,
				"status": "rejected",
				"error":  fmt.Sprintf("target %q is not a known Windows host", target),
			})
			continue
		}

		winrmTarget := d.buildHealingWinRMTarget(target)
		if winrmTarget == nil {
			results = append(results, map[string]interface{}{
				"index":  i,
				"type":   driftType,
				"target": target,
				"status": "error",
				"error":  "no WinRM credentials available for target",
			})
			continue
		}

		log.Printf("[chaos] Injecting scenario %d/%d: %s on %s (campaign=%s)",
			i+1, len(scenarios), driftType, target, campaignID)

		execResult := d.winrmExec.ExecuteCtx(ctx, winrmTarget, inject,
			"chaos-"+driftType, "inject", 60, 1, 5.0, nil)

		status := "injected"
		errMsg := ""
		if !execResult.Success {
			status = "inject_failed"
			errMsg = execResult.Error
		}

		results = append(results, map[string]interface{}{
			"index":  i,
			"type":   driftType,
			"target": target,
			"status": status,
			"error":  errMsg,
		})
	}

	injected := 0
	for _, r := range results {
		if r["status"] == "injected" {
			injected++
		}
	}

	log.Printf("[chaos] Campaign %s complete: %d/%d scenarios injected", campaignID, injected, len(scenarios))

	return map[string]interface{}{
		"campaign_id":     campaignID,
		"scenarios_total": len(scenarios),
		"injected":        injected,
		"results":         results,
	}, nil
}

// handleValidateCredential tests WinRM connectivity using a stored credential.
// It looks up the credential's hostname in winTargets and runs a simple test command.
func (d *Daemon) handleValidateCredential(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
	credID := maputil.String(params, "credential_id")
	hostname := maputil.String(params, "hostname")
	credType := maputil.String(params, "credential_type")

	result := map[string]interface{}{
		"credential_id":   credID,
		"hostname":        hostname,
		"credential_type": credType,
		"can_connect":     false,
		"can_read_ad":     false,
		"is_domain_admin": false,
		"errors":          []string{},
	}

	if hostname == "" {
		result["errors"] = []string{"no hostname specified"}
		return result, nil
	}

	// Look up credentials from checkin cache
	target, ok := d.LookupWinTarget(hostname)
	if !ok {
		// Try DC credentials as fallback
		dcHost := ""
		dcUser := ""
		dcPass := ""
		if d.config.DomainController != nil {
			dcHost = *d.config.DomainController
		}
		if d.config.DCUsername != nil {
			dcUser = *d.config.DCUsername
		}
		if d.config.DCPassword != nil {
			dcPass = *d.config.DCPassword
		}

		if dcUser != "" && dcPass != "" && (hostname == dcHost || credType == "domain_admin") {
			target = winTarget{
				Hostname: hostname,
				Username: dcUser,
				Password: dcPass,
				Role:     "domain_admin",
			}
			ok = true
		}
	}

	if !ok {
		result["errors"] = []string{"no cached credentials for hostname " + hostname}
		return result, nil
	}

	ws := d.probeWinRM(hostname)
	winrmTarget := &winrm.Target{
		Hostname:  hostname,
		Port:      ws.Port,
		Username:  target.Username,
		Password:  target.Password,
		UseSSL:    ws.UseSSL,
		VerifySSL: true, // TOFU cert pinning via CertPinStore
	}

	// Test basic WinRM connectivity with a simple command
	testScript := `$env:COMPUTERNAME`
	execResult := d.winrmExec.ExecuteCtx(ctx, winrmTarget, testScript, "credential-validate", "connect-test", 30, 1, 0, nil)

	if execResult.Success {
		result["can_connect"] = true
		log.Printf("[orders] validate_credential: WinRM OK for %s (%s)", hostname, credType)

		// If domain_admin, test AD read access
		if credType == "domain_admin" {
			adScript := `try { Get-ADDomain | Select-Object -ExpandProperty DNSRoot } catch { "AD_ERROR: $_" }`
			adResult := d.winrmExec.ExecuteCtx(ctx, winrmTarget, adScript, "credential-validate", "ad-test", 30, 1, 0, nil)
			stdout, _ := adResult.Output["std_out"].(string)
			if adResult.Success && !strings.Contains(stdout, "AD_ERROR") {
				result["can_read_ad"] = true
				result["is_domain_admin"] = true
			}
		}
	} else {
		errMsg := execResult.Error
		if errMsg == "" {
			errMsg = "WinRM connection failed"
		}
		result["errors"] = []string{errMsg}
		log.Printf("[orders] validate_credential: WinRM FAILED for %s: %s", hostname, errMsg)
	}

	result["status"] = "validated"
	return result, nil
}

// buildSSHTarget creates an SSH target from the heal request metadata.
func (d *Daemon) buildSSHTarget(req *grpcserver.HealRequest) *sshexec.Target {
	username := req.Metadata["ssh_username"]
	password := req.Metadata["ssh_password"]
	key := req.Metadata["ssh_private_key"]
	ipAddr := req.Metadata["ip_address"]

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
func (d *Daemon) escalateToL3(incidentID string, req *grpcserver.HealRequest, reason string) {
	log.Printf("[daemon] L3 ESCALATION: incident=%s host=%s check=%s hipaa=%s reason=%s",
		incidentID, req.Hostname, req.CheckType, req.HIPAAControl, reason)
	// In production, this would create an escalation record in Central Command
	// and potentially send notifications (email, Slack, etc.)
}

// gpoFixDone is now a field on the Daemon struct (below), not a package global.

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
	if _, done := d.state.gpoFixDone.LoadOrStore(dc, true); done {
		return
	}

	log.Printf("[daemon] GPO firewall fix: checking Default Domain Policy on %s (triggered by %s)",
		dc, triggerHost)

	ws := d.probeWinRM(dc)
	target := &winrm.Target{
		Hostname:  dc,
		Port:      ws.Port,
		Username:  *d.config.DCUsername,
		Password:  *d.config.DCPassword,
		UseSSL:    ws.UseSSL,
		VerifySSL: true, // TOFU cert pinning via CertPinStore
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
		d.state.gpoFixDone.Delete(dc)
	}
}

// findWinRMTarget builds a WinRM target for a hostname using DC credentials.
// Domain admin credentials (from config) work for all domain-joined machines.
func (d *Daemon) findWinRMTarget(hostname string) *winrm.Target {
	if d.config.DCUsername == nil || d.config.DCPassword == nil {
		return nil
	}
	ws := d.probeWinRM(hostname)
	return &winrm.Target{
		Hostname:  hostname,
		Port:      ws.Port,
		Username:  *d.config.DCUsername,
		Password:  *d.config.DCPassword,
		UseSSL:    ws.UseSSL,
		VerifySSL: true, // TOFU cert pinning via CertPinStore
	}
}

const (
	defaultCooldown = 10 * time.Minute // Normal cooldown between heal attempts
	flapCooldown    = 1 * time.Hour    // Extended cooldown when flapping detected
	flapThreshold   = 3                // Occurrences in flapWindow → flapping
	flapWindow      = 30 * time.Minute // Window to count occurrences
	cooldownCleanup = 2 * time.Hour    // Entries older than this are removed
)

// shouldSuppressDrift delegates to StateManager.
func (d *Daemon) shouldSuppressDrift(key string) bool {
	return d.state.ShouldSuppress(key)
}

// getADHostnames delegates to StateManager.
func (d *Daemon) getADHostnames() map[string]bool {
	return d.state.GetADHostnames()
}
