// OsirisCare Go Agent - Workstation compliance monitoring
//
// This agent runs on Windows workstations and reports drift events
// to the NixOS compliance appliance via gRPC.
//
// Features:
// - 6 compliance checks: BitLocker, Defender, Patches, Firewall, ScreenLock, RMM
// - Real-time Windows Event Log monitoring (<1s detection vs 5min polling)
// - gRPC bidirectional streaming for drift events and heal commands
// - SQLite offline queue for network resilience
// - mTLS with certificate auto-enrollment
// - Windows Service Control Manager (SCM) integration
// - DNS SRV auto-discovery of appliance
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"syscall"
	"time"

	"github.com/osiriscare/agent/internal/checks"
	"github.com/osiriscare/agent/internal/config"
	"github.com/osiriscare/agent/internal/discovery"
	"github.com/osiriscare/agent/internal/eventlog"
	"github.com/osiriscare/agent/internal/healing"
	"github.com/osiriscare/agent/internal/service"
	"github.com/osiriscare/agent/internal/transport"
	"github.com/osiriscare/agent/internal/updater"
	pb "github.com/osiriscare/agent/proto"
)

var (
	Version   = "dev"
	BuildTime = "unknown"
	GitCommit = "unknown"
)

// Command-line flags (parsed before SCM detection)
var (
	flagAppliance = flag.String("appliance", "", "Appliance gRPC address (host:port)")
	flagConfig    = flag.String("config", "", "Config file path (optional)")
	flagVersion   = flag.Bool("version", false, "Print version and exit")
	flagDryRun    = flag.Bool("dry-run", false, "Run checks once and exit (don't connect)")
)

func main() {
	flag.Parse()

	if *flagVersion {
		fmt.Printf("osiris-agent %s (%s, built %s)\n", Version, GitCommit, BuildTime)
		os.Exit(0)
	}

	// Set up file logging — Windows services have no console for stderr
	logPath := filepath.Join(filepath.Dir(os.Args[0]), "agent.log")
	if logPath == filepath.Join(".", "agent.log") {
		switch runtime.GOOS {
		case "darwin":
			logPath = "/Library/OsirisCare/agent.log"
		default:
			logPath = `C:\OsirisCare\agent.log`
		}
	}
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err == nil {
		// Write to file first (always works), then stderr (for interactive debugging).
		// File must be first: io.MultiWriter stops at first write error, and
		// os.Stderr has no valid handle when running as a Windows service.
		multiWriter := io.MultiWriter(logFile, os.Stderr)
		log.SetOutput(multiWriter)
	}

	log.SetFlags(log.LstdFlags | log.Lshortfile)
	log.Printf("OsirisCare Agent v%s (%s) starting...", Version, GitCommit)

	// Dry run: always interactive
	if *flagDryRun {
		runDryRun()
		return
	}

	// Windows Service Control Manager detection
	if service.IsWindowsService() {
		log.Println("Running as Windows service")
		svc := &service.AgentService{RunFunc: runAgent}
		if err := service.Run(svc); err != nil {
			log.Fatalf("Service failed: %v", err)
		}
		return
	}

	// Interactive mode (console)
	log.Println("Running in interactive mode")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		sig := <-sigChan
		log.Printf("Shutdown signal received: %v", sig)
		cancel()
	}()

	if err := runAgent(ctx); err != nil {
		log.Fatalf("Agent failed: %v", err)
	}
}

// runAgent is the core agent logic. It receives a context that is cancelled
// on shutdown (either from SCM or SIGINT/SIGTERM).
func runAgent(ctx context.Context) error {
	cfg, err := config.Load(*flagConfig, *flagAppliance)
	if err != nil {
		return fmt.Errorf("failed to load config: %w", err)
	}

	// Self-update: check if previous update needs confirmation or rollback
	installDir := filepath.Dir(os.Args[0])
	if installDir == "." || installDir == "" {
		switch runtime.GOOS {
		case "darwin":
			installDir = "/Library/OsirisCare"
		default:
			installDir = `C:\OsirisCare`
		}
	}
	upd := updater.New(cfg.DataDir, installDir, Version, "OsirisCareAgent")
	upd.CheckRollbackNeeded()

	// Auto-discovery if no appliance address configured.
	// Priority: mDNS (local network, survives DHCP drift) → DNS SRV (AD domain)
	if cfg.ApplianceAddr == "" {
		log.Println("[discovery] No appliance address configured, attempting mDNS discovery...")
		addr, err := discovery.DiscoverApplianceMDNSWithRetry(ctx, 3)
		if err == nil {
			cfg.ApplianceAddr = addr
			log.Printf("[discovery] mDNS discovered appliance: %s", addr)
			if saveErr := cfg.Save(); saveErr != nil {
				log.Printf("[discovery] Failed to cache config: %v", saveErr)
			}
		} else {
			log.Printf("[discovery] mDNS failed: %v — falling back to DNS SRV", err)
			domain := cfg.Domain
			if domain == "" {
				domain = discovery.DiscoverDomain()
				if domain != "" {
					log.Printf("[discovery] Detected domain: %s", domain)
					cfg.Domain = domain
				}
			}
			if domain != "" {
				addr, err := discovery.DiscoverApplianceWithRetry(domain, discovery.MaxRetries)
				if err != nil {
					log.Printf("[discovery] SRV discovery failed: %v (will start without appliance connection)", err)
				} else {
					cfg.ApplianceAddr = addr
					log.Printf("[discovery] SRV discovered appliance: %s", addr)
					if saveErr := cfg.Save(); saveErr != nil {
						log.Printf("[discovery] Failed to cache config: %v", saveErr)
					}
				}
			} else {
				log.Println("[discovery] Could not detect AD domain — agent will operate offline")
			}
		}
	}

	// Initialize gRPC transport
	var grpcClient *transport.GRPCClient
	if cfg.ApplianceAddr != "" {
		grpcClient, err = transport.NewGRPCClient(ctx, cfg, Version)
		if err != nil {
			log.Printf("Failed to connect to appliance: %v (will retry in background)", err)
		}
	}
	if grpcClient != nil {
		defer grpcClient.Close()
	}

	// Register with appliance (+ reconnect loop if initial connect fails)
	var regResp *pb.RegisterResponse
	if grpcClient != nil && grpcClient.IsConnected() {
		regResp = tryRegisterAndSetup(ctx, grpcClient, upd)
	}

	// Background reconnect loop — retries connection if initial connect failed or drops
	if grpcClient != nil && !grpcClient.IsConnected() {
		go reconnectLoop(ctx, cfg, grpcClient, upd)
	}

	// Check interval and enabled checks
	checkInterval := 300
	enabledChecks := checks.DefaultEnabledChecks()

	if regResp != nil {
		if regResp.CheckIntervalSeconds > 0 {
			checkInterval = int(regResp.CheckIntervalSeconds)
		}
		if len(regResp.EnabledChecks) > 0 {
			enabledChecks = regResp.EnabledChecks
		}
	}

	checkRegistry := checks.NewRegistry(enabledChecks)
	flapDetector := checks.NewFlapDetector()

	// Offline queue
	offlineQueue, err := transport.NewOfflineQueue(cfg.DataDir)
	if err != nil {
		log.Printf("Failed to initialize offline queue: %v", err)
	}
	if offlineQueue != nil {
		defer offlineQueue.Close()
	}

	// Start persistent drift stream (if already connected)
	if grpcClient != nil && grpcClient.IsConnected() {
		if err := grpcClient.StartDriftStream(ctx); err != nil {
			log.Printf("Failed to start drift stream: %v (will use one-shot mode)", err)
		} else {
			log.Println("Bidirectional drift stream established")
		}
	}

	// Heal command executor
	if grpcClient != nil {
		go runHealExecutor(ctx, grpcClient)
	}

	// Heartbeat loop (also handles self-update signals)
	if grpcClient != nil && grpcClient.IsConnected() {
		go runHeartbeatLoop(ctx, grpcClient, upd)
	}

	// Real-time Windows Event Log monitoring
	hostname := checks.GetHostname()
	eventWatcher := eventlog.NewWatcher(hostname, func(event *eventlog.ComplianceEvent) {
		log.Printf("[REALTIME] Compliance event detected: %s (channel: %s)", event.CheckType, event.Channel)

		agentID := ""
		if regResp != nil {
			agentID = regResp.AgentId
		}
		driftEvent := event.ConvertToDriftEvent(agentID, hostname)

		// Flap detection for real-time events
		if !flapDetector.ShouldSend(event.CheckType, event.Passed) {
			log.Printf("[REALTIME] Suppressed flapping event: %s (%s)", event.CheckType, flapDetector.Status(event.CheckType))
			return
		}

		if grpcClient != nil && grpcClient.IsConnected() {
			if err := grpcClient.SendDrift(ctx, driftEvent); err != nil {
				log.Printf("[REALTIME] Failed to send event, queueing: %v", err)
				if offlineQueue != nil {
					offlineQueue.Enqueue(driftEvent)
				}
			} else {
				log.Printf("[REALTIME] Drift event sent: %s", event.CheckType)
			}
		} else if offlineQueue != nil {
			offlineQueue.Enqueue(driftEvent)
		}
	})

	if err := eventWatcher.Start(); err != nil {
		log.Printf("Failed to start event log watcher: %v (polling will still work)", err)
	} else {
		defer eventWatcher.Stop()
	}

	// Main compliance check loop
	ticker := time.NewTicker(time.Duration(checkInterval) * time.Second)
	defer ticker.Stop()

	log.Printf("Starting compliance check loop (interval: %ds, checks: %v)", checkInterval, enabledChecks)

	runChecks(ctx, checkRegistry, grpcClient, offlineQueue, regResp, flapDetector)

	for {
		select {
		case <-ctx.Done():
			log.Println("Shutting down gracefully")
			return nil
		case <-ticker.C:
			runChecks(ctx, checkRegistry, grpcClient, offlineQueue, regResp, flapDetector)
		}
	}
}

// tryRegisterAndSetup attempts registration and returns the response.
func tryRegisterAndSetup(ctx context.Context, client *transport.GRPCClient, upd *updater.Updater) *pb.RegisterResponse {
	regResp, err := client.Register(ctx)
	if err != nil {
		log.Printf("Failed to register: %v", err)
		return nil
	}
	log.Printf("Registered as %s, tier=%d, interval=%ds",
		regResp.AgentId, regResp.CapabilityTier, regResp.CheckIntervalSeconds)
	return regResp
}

// reconnectLoop watches the gRPC connection and retries with exponential backoff.
// It runs for the lifetime of the agent — if the connection drops after success,
// it re-enters the retry cycle.
func reconnectLoop(ctx context.Context, cfg *config.Config, client *transport.GRPCClient, upd *updater.Updater) {
	backoff := 30 * time.Second
	maxBackoff := 5 * time.Minute
	monitorInterval := 30 * time.Second

	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}

		if client.IsConnected() {
			// Connection is healthy — monitor for drops
			backoff = 30 * time.Second // reset backoff
			select {
			case <-ctx.Done():
				return
			case <-time.After(monitorInterval):
				continue // re-check IsConnected on next iteration
			}
		}

		// After 3+ consecutive failures, try mDNS re-resolution — appliance may
		// have changed IP via DHCP. This is the key fix for DHCP drift resilience.
		if client.ConsecutiveFailures() >= 3 {
			if newAddr, err := discovery.DiscoverApplianceMDNS(ctx, 3*time.Second); err == nil && newAddr != cfg.ApplianceAddr {
				log.Printf("[reconnect] mDNS re-resolved appliance: %s → %s", cfg.ApplianceAddr, newAddr)
				cfg.ApplianceAddr = newAddr
				client.UpdateAddress(newAddr)
				_ = cfg.Save()
			}
		}

		log.Printf("[reconnect] Attempting gRPC reconnect to %s...", cfg.ApplianceAddr)
		if err := client.Reconnect(ctx); err != nil {
			client.RecordFailure()
			errClass := transport.ClassifyConnectionError(err)
			failures := client.ConsecutiveFailures()

			// Detect TOFU pin mismatch — appliance may have regenerated its
			// self-signed cert on restart. After threshold consecutive failures,
			// clear the stale pin so the next attempt re-enrolls via TOFU.
			if errors.Is(err, transport.ErrPinMismatch) {
				n := client.PinMismatchCount()
				if n >= 3 {
					log.Printf("[reconnect] TOFU pin mismatch detected %d times — clearing stale pin for re-enrollment", n)
					if clearErr := client.ClearPinFile(); clearErr != nil {
						log.Printf("[reconnect] WARNING: failed to clear pin file: %v", clearErr)
					}
					// Use short backoff so re-enrollment happens quickly
					backoff = 5 * time.Second
					continue
				}
			}

			// Detect cert expiry/rejection — after repeated auth failures,
			// force cert re-enrollment (agent equivalent of appliance auto-rekey).
			if errClass == "auth_rejected" || errClass == "tls_error" {
				if client.NeedsCertReEnrollment() {
					log.Printf("[reconnect] Auth rejected %d times — forcing cert re-enrollment", failures)
					if reErr := client.ForceReEnrollment(); reErr != nil {
						log.Printf("[reconnect] WARNING: cert re-enrollment prep failed: %v", reErr)
					}
					backoff = 5 * time.Second
					continue
				}
			}

			log.Printf("[reconnect] Failed (%s, consecutive=%d): %v (retry in %s)",
				errClass, failures, err, backoff)
			backoff = backoff * 2
			if backoff > maxBackoff {
				backoff = maxBackoff
			}
			continue
		}

		client.RecordSuccess()

		// Connected — register and set up streams
		regResp := tryRegisterAndSetup(ctx, client, upd)
		if regResp == nil {
			log.Printf("[reconnect] Connected but registration failed, will retry")
			continue
		}

		// Start drift stream
		if err := client.StartDriftStream(ctx); err != nil {
			log.Printf("[reconnect] Drift stream failed: %v", err)
		} else {
			log.Println("[reconnect] Drift stream established")
		}

		// Start heartbeat loop
		go runHeartbeatLoop(ctx, client, upd)

		log.Println("[reconnect] Successfully reconnected and registered")
		backoff = 30 * time.Second // reset for next disconnect
	}
}

// runHealExecutor consumes HealCommands from the gRPC client channel,
// executes them locally, and reports results back to the appliance.
func runHealExecutor(ctx context.Context, client *transport.GRPCClient) {
	log.Println("[heal] Heal command executor started")
	for {
		select {
		case <-ctx.Done():
			log.Println("[heal] Heal command executor stopped")
			return
		case cmd := <-client.HealCmds:
			result := healing.Execute(ctx, cmd)

			healResult := &pb.HealingResult{
				CheckType:    result.CheckType,
				Success:      result.Success,
				ErrorMessage: result.Error,
				CommandId:    result.CommandID,
				Artifacts:    result.Artifacts,
			}

			if err := client.SendHealingResult(ctx, healResult); err != nil {
				log.Printf("[heal] Failed to report result for %s: %v", cmd.CommandId, err)
			}
		}
	}
}

// runHeartbeatLoop sends periodic heartbeats to the appliance.
// Also attempts to re-establish the drift stream if it has disconnected,
// and triggers self-updates when the appliance signals a new version.
func runHeartbeatLoop(ctx context.Context, client *transport.GRPCClient, upd *updater.Updater) {
	ticker := time.NewTicker(60 * time.Second)
	defer ticker.Stop()

	streamRetryCount := 0
	heartbeatFailures := 0

	log.Println("[heartbeat] Heartbeat loop started (60s interval)")
	for {
		select {
		case <-ctx.Done():
			log.Println("[heartbeat] Heartbeat loop stopped")
			return
		case <-ticker.C:
			resp, err := client.SendHeartbeat(ctx)
			if err != nil {
				heartbeatFailures++
				log.Printf("[heartbeat] Failed (consecutive=%d): %v", heartbeatFailures, err)
				// After 3 consecutive failures, mark disconnected so the reconnect
				// loop triggers a full reconnect instead of endlessly failing here.
				if heartbeatFailures >= 3 {
					log.Printf("[heartbeat] %d consecutive failures — marking disconnected for reconnect", heartbeatFailures)
					client.MarkDisconnected()
					return // exit heartbeat loop; reconnect loop will restart it
				}
				continue
			}
			heartbeatFailures = 0 // reset on success
			if resp.ConfigChanged {
				log.Println("[heartbeat] Config changed — re-registration needed")
			}

			// Self-update check
			if resp.UpdateAvailable && upd != nil {
				log.Printf("[heartbeat] Update available: v%s", resp.UpdateVersion)
				go func() {
					if err := upd.CheckAndUpdate(ctx, resp.UpdateVersion, resp.UpdateUrl, resp.UpdateSha256); err != nil {
						log.Printf("[heartbeat] Update failed: %v", err)
					}
				}()
			}

			// Re-establish drift stream if it has disconnected
			if !client.StreamActive() && client.IsConnected() {
				streamRetryCount++
				// Retry every 5th heartbeat (~5 min) to avoid hammering
				if streamRetryCount%5 == 0 {
					log.Println("[heartbeat] Drift stream inactive, attempting to re-establish...")
					if err := client.StartDriftStream(ctx); err != nil {
						log.Printf("[heartbeat] Failed to re-establish drift stream: %v", err)
					} else {
						log.Println("[heartbeat] Drift stream re-established")
						streamRetryCount = 0
					}
				}
			} else {
				streamRetryCount = 0
			}

			// Certificate renewal check (every ~1 hour, piggyback on heartbeat)
			if streamRetryCount == 0 && client.CertNeedsRenewal() {
				log.Println("[heartbeat] Certificate expiring soon, triggering renewal")
				go func() {
					if err := client.RenewCerts(ctx); err != nil {
						log.Printf("[heartbeat] Cert renewal failed: %v", err)
					}
				}()
			}
		}
	}
}

func runChecks(
	ctx context.Context,
	registry *checks.Registry,
	client *transport.GRPCClient,
	queue *transport.OfflineQueue,
	reg *pb.RegisterResponse,
	flap *checks.FlapDetector,
) {
	log.Println("Running compliance checks...")
	start := time.Now()

	results := registry.RunAll(ctx)

	passCount := 0
	failCount := 0
	suppressedCount := 0

	for _, result := range results {
		if result.Error != nil {
			log.Printf("  [ERROR] %s: %v", result.CheckType, result.Error)
			continue
		}

		// Record all results in flap detector (pass and fail)
		shouldSend := flap.ShouldSend(result.CheckType, result.Passed)

		agentID := ""
		if reg != nil {
			agentID = reg.AgentId
		}

		if result.Passed {
			log.Printf("  [PASS] %s", result.CheckType)
			passCount++
		} else {
			failCount++

			if !shouldSend {
				log.Printf("  [FAIL] %s: suppressed (flapping: %s)", result.CheckType, flap.Status(result.CheckType))
				suppressedCount++
				continue
			}

			log.Printf("  [FAIL] %s: expected=%q, actual=%q", result.CheckType, result.Expected, result.Actual)
		}

		// Send ALL check results (pass + fail) so the appliance has full compliance picture
		event := &pb.DriftEvent{
			AgentId:      agentID,
			Hostname:     checks.GetHostname(),
			CheckType:    result.CheckType,
			Passed:       result.Passed,
			Expected:     result.Expected,
			Actual:       result.Actual,
			HipaaControl: result.HIPAAControl,
			Timestamp:    checks.GetTimestamp(),
			Metadata:     result.Metadata,
		}
		// Tag flapping events so the backend knows
		if !result.Passed && flap.Status(result.CheckType) != "stable" {
			if event.Metadata == nil {
				event.Metadata = make(map[string]string)
			}
			event.Metadata["flapping"] = "true"
		}

		if client != nil && client.IsConnected() {
			if err := client.SendDrift(ctx, event); err != nil {
				log.Printf("Failed to send drift event, queueing: %v", err)
				if queue != nil && !result.Passed {
					queue.Enqueue(event)
				}
			}
		} else if queue != nil && !result.Passed {
			queue.Enqueue(event)
		}
	}

	elapsed := time.Since(start)
	if suppressedCount > 0 {
		log.Printf("Checks complete in %v: %d passed, %d failed (%d suppressed as flapping)", elapsed, passCount, failCount, suppressedCount)
	} else {
		log.Printf("Checks complete in %v: %d passed, %d failed", elapsed, passCount, failCount)
	}

	if client != nil && client.IsConnected() && queue != nil {
		drainQueue(ctx, client, queue)
	}

	if queue != nil {
		queueSize := queue.Count()
		if queueSize > 0 {
			log.Printf("Offline queue: %d events pending", queueSize)
		}
	}
}

func drainQueue(ctx context.Context, client *transport.GRPCClient, queue *transport.OfflineQueue) {
	events, err := queue.DequeueAll(100)
	if err != nil {
		log.Printf("Failed to dequeue events: %v", err)
		return
	}

	if len(events) == 0 {
		return
	}

	log.Printf("Draining offline queue: %d events", len(events))

	sent := 0
	for _, event := range events {
		if err := client.SendDrift(ctx, event); err != nil {
			queue.Enqueue(event)
			break
		}
		sent++
	}

	if sent > 0 {
		log.Printf("Sent %d queued events", sent)
	}
}

func runDryRun() {
	log.Println("Running in dry-run mode (single check cycle)")

	ctx := context.Background()

	registry := checks.NewRegistry(checks.DefaultEnabledChecks())

	results := registry.RunAll(ctx)

	fmt.Println("\n=== Compliance Check Results ===")

	for _, result := range results {
		status := "PASS"
		if !result.Passed {
			status = "FAIL"
		}
		if result.Error != nil {
			status = "ERROR"
		}

		fmt.Printf("[%s] %s\n", status, result.CheckType)

		if result.HIPAAControl != "" {
			fmt.Printf("  HIPAA Control: %s\n", result.HIPAAControl)
		}

		if result.Error != nil {
			fmt.Printf("  Error: %v\n", result.Error)
		} else {
			fmt.Printf("  Expected: %s\n", result.Expected)
			fmt.Printf("  Actual: %s\n", result.Actual)
		}

		if len(result.Metadata) > 0 {
			fmt.Println("  Metadata:")
			for k, v := range result.Metadata {
				fmt.Printf("    %s: %s\n", k, v)
			}
		}
		fmt.Println()
	}
}
