package daemon

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"path/filepath"
	"time"

	"github.com/osiriscare/appliance/internal/ca"
	"github.com/osiriscare/appliance/internal/grpcserver"
	"github.com/osiriscare/appliance/internal/healing"
	"github.com/osiriscare/appliance/internal/l2bridge"
	"github.com/osiriscare/appliance/internal/orders"
)

// Version is set at build time.
var Version = "0.1.0"

// Daemon is the main appliance daemon that orchestrates all subsystems.
type Daemon struct {
	config   *Config
	phoneCli *PhoneHomeClient
	grpcSrv  *grpcserver.Server
	registry *grpcserver.AgentRegistry
	agentCA  *ca.AgentCA
	l1Engine *healing.Engine
	l2Client *l2bridge.Client
	orderProc *orders.Processor
}

// New creates a new daemon with the given configuration.
func New(cfg *Config) *Daemon {
	d := &Daemon{
		config:   cfg,
		phoneCli: NewPhoneHomeClient(cfg),
		registry: grpcserver.NewAgentRegistry(),
	}

	// Initialize L1 healing engine
	rulesDir := cfg.RulesDir()
	var executor healing.ActionExecutor
	if cfg.HealingDryRun {
		executor = nil // nil executor → dry-run mode
	}
	d.l1Engine = healing.NewEngine(rulesDir, executor)
	log.Printf("[daemon] L1 engine loaded: %d rules (dry_run=%v)", d.l1Engine.RuleCount(), cfg.HealingDryRun)

	// Initialize L2 bridge (connects lazily)
	if cfg.L2Enabled {
		socketPath := filepath.Join(cfg.StateDir, "l2.sock")
		d.l2Client = l2bridge.NewClient(socketPath, 30*time.Second)
	}

	// Initialize order processor with completion callback
	d.orderProc = orders.NewProcessor(cfg.StateDir, d.completeOrder)

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

	elapsed := time.Since(start)
	log.Printf("[daemon] Cycle complete in %v (agents=%d)",
		elapsed, d.registry.ConnectedCount())
}

// runCheckin sends a checkin to Central Command and processes the response.
func (d *Daemon) runCheckin(ctx context.Context) {
	req := SystemInfo(d.config, Version)

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

// completeOrder reports order completion back to Central Command via phone-home.
func (d *Daemon) completeOrder(ctx context.Context, orderID string, success bool, result map[string]interface{}, errMsg string) error {
	log.Printf("[daemon] Order %s completion: success=%v", orderID, success)

	// Build completion payload for next checkin
	// In production, this would POST directly to /api/appliances/orders/<id>/complete
	// For now, log the completion (the Python daemon does this via the checkin response)
	if result != nil {
		data, _ := json.Marshal(result)
		log.Printf("[daemon] Order %s result: %s", orderID, string(data))
	}
	if errMsg != "" {
		log.Printf("[daemon] Order %s error: %s", orderID, errMsg)
	}

	return nil
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
func (d *Daemon) healIncident(_ context.Context, req grpcserver.HealRequest) {
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
			// L2 auto-execution would use the WinRM/SSH executors here
			// For now, log the decision (wiring executors is a follow-up task)
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

// escalateToL3 logs an incident that requires human intervention.
func (d *Daemon) escalateToL3(incidentID string, req grpcserver.HealRequest, reason string) {
	log.Printf("[daemon] L3 ESCALATION: incident=%s host=%s check=%s hipaa=%s reason=%s",
		incidentID, req.Hostname, req.CheckType, req.HIPAAControl, reason)
	// In production, this would create an escalation record in Central Command
	// and potentially send notifications (email, Slack, etc.)
}
