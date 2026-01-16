// OsirisCare Go Agent - Workstation compliance monitoring
//
// This agent runs on Windows workstations and reports drift events
// to the NixOS compliance appliance via gRPC or HTTP fallback.
//
// Features:
// - 6 compliance checks: BitLocker, Defender, Patches, Firewall, ScreenLock, RMM
// - gRPC streaming for efficient communication
// - SQLite offline queue for network resilience
// - mTLS for secure appliance communication
// - Windows service integration
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
	"github.com/osiriscare/agent/internal/transport"
	pb "github.com/osiriscare/agent/proto"
)

var (
	// Build-time variables
	Version   = "0.1.0"
	BuildTime = "unknown"
)

func main() {
	// Parse flags
	applianceAddr := flag.String("appliance", "", "Appliance gRPC address (host:port)")
	configFile := flag.String("config", "", "Config file path (optional)")
	version := flag.Bool("version", false, "Print version and exit")
	dryRun := flag.Bool("dry-run", false, "Run checks once and exit (don't connect)")
	flag.Parse()

	if *version {
		fmt.Printf("osiris-agent %s (built %s)\n", Version, BuildTime)
		os.Exit(0)
	}

	// Setup logging
	log.SetFlags(log.LstdFlags | log.Lshortfile)
	log.Printf("OsirisCare Agent v%s starting...", Version)

	// Load configuration
	cfg, err := config.Load(*configFile, *applianceAddr)
	if err != nil {
		log.Fatalf("Failed to load config: %v", err)
	}

	// Dry run mode - just run checks and exit
	if *dryRun {
		runDryRun()
		return
	}

	// Create context with cancellation
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Handle shutdown signals
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		sig := <-sigChan
		log.Printf("Shutdown signal received: %v", sig)
		cancel()
	}()

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

	// Set default check interval if not registered
	checkInterval := 300 // 5 minutes default
	enabledChecks := []string{"bitlocker", "defender", "patches", "firewall", "screenlock", "rmm_detection"}

	if regResp != nil {
		checkInterval = int(regResp.CheckIntervalSeconds)
		if len(regResp.EnabledChecks) > 0 {
			enabledChecks = regResp.EnabledChecks
		}
	}

	// Initialize check registry with enabled checks
	checkRegistry := checks.NewRegistry(enabledChecks)

	// Initialize offline queue for network failures
	offlineQueue, err := transport.NewOfflineQueue(cfg.DataDir)
	if err != nil {
		log.Printf("Failed to initialize offline queue: %v", err)
	}
	if offlineQueue != nil {
		defer offlineQueue.Close()
	}

	// Main loop
	ticker := time.NewTicker(time.Duration(checkInterval) * time.Second)
	defer ticker.Stop()

	log.Printf("Starting compliance check loop (interval: %ds, checks: %v)", checkInterval, enabledChecks)

	// Initial check run
	runChecks(ctx, checkRegistry, grpcClient, offlineQueue, regResp)

	for {
		select {
		case <-ctx.Done():
			log.Println("Shutting down gracefully")
			return
		case <-ticker.C:
			runChecks(ctx, checkRegistry, grpcClient, offlineQueue, regResp)
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

			// Create drift event
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

			// Try to send via gRPC
			if client != nil && client.IsConnected() {
				if err := client.SendDrift(ctx, event); err != nil {
					log.Printf("Failed to send drift event, queueing: %v", err)
					if queue != nil {
						queue.Enqueue(event)
					}
				}
			} else if queue != nil {
				// Queue if not connected
				queue.Enqueue(event)
			}
		}
	}

	elapsed := time.Since(start)
	log.Printf("Checks complete in %v: %d passed, %d failed", elapsed, passCount, failCount)

	// Drain offline queue if connected
	if client != nil && client.IsConnected() && queue != nil {
		drainQueue(ctx, client, queue)
	}

	// Log queue status
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
			// Re-queue on failure
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

	// Run all checks
	registry := checks.NewRegistry([]string{
		"bitlocker",
		"defender",
		"patches",
		"firewall",
		"screenlock",
		"rmm_detection",
	})

	results := registry.RunAll(ctx)

	fmt.Println("\n=== Compliance Check Results ===\n")

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
			fmt.Printf("  HIPAA Control: §%s\n", result.HIPAAControl)
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
