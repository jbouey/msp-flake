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
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/osiriscare/agent/internal/checks"
	"github.com/osiriscare/agent/internal/config"
	"github.com/osiriscare/agent/internal/discovery"
	"github.com/osiriscare/agent/internal/eventlog"
	"github.com/osiriscare/agent/internal/healing"
	"github.com/osiriscare/agent/internal/service"
	"github.com/osiriscare/agent/internal/transport"
	pb "github.com/osiriscare/agent/proto"
)

var (
	Version   = "0.3.0"
	BuildTime = "unknown"
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
		fmt.Printf("osiris-agent %s (built %s)\n", Version, BuildTime)
		os.Exit(0)
	}

	log.SetFlags(log.LstdFlags | log.Lshortfile)
	log.Printf("OsirisCare Agent v%s starting...", Version)

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

	// DNS SRV discovery if no appliance address configured
	if cfg.ApplianceAddr == "" {
		log.Println("[discovery] No appliance address configured, attempting DNS SRV discovery...")
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
				log.Printf("[discovery] Discovered appliance: %s", addr)
				if saveErr := cfg.Save(); saveErr != nil {
					log.Printf("[discovery] Failed to cache config: %v", saveErr)
				}
			}
		} else {
			log.Println("[discovery] Could not detect AD domain — agent will operate offline")
		}
	}

	// Initialize gRPC transport
	var grpcClient *transport.GRPCClient
	if cfg.ApplianceAddr != "" {
		grpcClient, err = transport.NewGRPCClient(ctx, cfg)
		if err != nil {
			log.Printf("Failed to connect to appliance: %v (will retry)", err)
		}
	}
	if grpcClient != nil {
		defer grpcClient.Close()
	}

	// Register with appliance
	var regResp *pb.RegisterResponse
	if grpcClient != nil && grpcClient.IsConnected() {
		regResp, err = grpcClient.Register(ctx)
		if err != nil {
			log.Printf("Failed to register: %v", err)
		} else {
			log.Printf("Registered as %s, tier=%d, interval=%ds",
				regResp.AgentId, regResp.CapabilityTier, regResp.CheckIntervalSeconds)
		}
	}

	// Check interval and enabled checks
	checkInterval := 300
	enabledChecks := []string{"bitlocker", "defender", "patches", "firewall", "screenlock", "rmm_detection"}

	if regResp != nil {
		if regResp.CheckIntervalSeconds > 0 {
			checkInterval = int(regResp.CheckIntervalSeconds)
		}
		if len(regResp.EnabledChecks) > 0 {
			enabledChecks = regResp.EnabledChecks
		}
	}

	checkRegistry := checks.NewRegistry(enabledChecks)

	// Offline queue
	offlineQueue, err := transport.NewOfflineQueue(cfg.DataDir)
	if err != nil {
		log.Printf("Failed to initialize offline queue: %v", err)
	}
	if offlineQueue != nil {
		defer offlineQueue.Close()
	}

	// Start persistent drift stream
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

	// Heartbeat loop
	if grpcClient != nil && grpcClient.IsConnected() {
		go runHeartbeatLoop(ctx, grpcClient)
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

	runChecks(ctx, checkRegistry, grpcClient, offlineQueue, regResp)

	for {
		select {
		case <-ctx.Done():
			log.Println("Shutting down gracefully")
			return nil
		case <-ticker.C:
			runChecks(ctx, checkRegistry, grpcClient, offlineQueue, regResp)
		}
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
func runHeartbeatLoop(ctx context.Context, client *transport.GRPCClient) {
	ticker := time.NewTicker(60 * time.Second)
	defer ticker.Stop()

	log.Println("[heartbeat] Heartbeat loop started (60s interval)")
	for {
		select {
		case <-ctx.Done():
			log.Println("[heartbeat] Heartbeat loop stopped")
			return
		case <-ticker.C:
			resp, err := client.SendHeartbeat(ctx)
			if err != nil {
				log.Printf("[heartbeat] Failed: %v", err)
				continue
			}
			if resp.ConfigChanged {
				log.Println("[heartbeat] Config changed — re-registration needed")
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
) {
	log.Println("Running compliance checks...")
	start := time.Now()

	results := registry.RunAll(ctx)

	passCount := 0
	failCount := 0

	for _, result := range results {
		if result.Error != nil {
			log.Printf("  [ERROR] %s: %v", result.CheckType, result.Error)
			continue
		}

		if result.Passed {
			log.Printf("  [PASS] %s", result.CheckType)
			passCount++
		} else {
			log.Printf("  [FAIL] %s: expected=%q, actual=%q", result.CheckType, result.Expected, result.Actual)
			failCount++

			agentID := ""
			if reg != nil {
				agentID = reg.AgentId
			}

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

			if client != nil && client.IsConnected() {
				if err := client.SendDrift(ctx, event); err != nil {
					log.Printf("Failed to send drift event, queueing: %v", err)
					if queue != nil {
						queue.Enqueue(event)
					}
				}
			} else if queue != nil {
				queue.Enqueue(event)
			}
		}
	}

	elapsed := time.Since(start)
	log.Printf("Checks complete in %v: %d passed, %d failed", elapsed, passCount, failCount)

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

	registry := checks.NewRegistry([]string{
		"bitlocker",
		"defender",
		"patches",
		"firewall",
		"screenlock",
		"rmm_detection",
	})

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
