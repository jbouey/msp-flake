package grpcserver

import (
	"context"
	"io"
	"net"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/test/bufconn"

	"github.com/osiriscare/appliance/internal/ca"
	pb "github.com/osiriscare/appliance/proto"
)

const bufSize = 1024 * 1024

func setupTestServer(t *testing.T, agentCA *ca.AgentCA) (pb.ComplianceAgentClient, *AgentRegistry, chan HealRequest, func()) {
	t.Helper()

	registry := NewAgentRegistry()
	healChan := make(chan HealRequest, 64)

	lis := bufconn.Listen(bufSize)
	srv := grpc.NewServer()
	pb.RegisterComplianceAgentServer(srv, &servicer{
		registry: registry,
		agentCA:  agentCA,
		healChan: healChan,
		siteID:   "test-site",
	})

	go func() {
		if err := srv.Serve(lis); err != nil {
			// Server stopped — expected on cleanup
		}
	}()

	dialer := func(context.Context, string) (net.Conn, error) {
		return lis.Dial()
	}

	conn, err := grpc.NewClient("passthrough:///bufnet",
		grpc.WithContextDialer(dialer),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatalf("grpc.NewClient: %v", err)
	}

	client := pb.NewComplianceAgentClient(conn)

	cleanup := func() {
		conn.Close()
		srv.GracefulStop()
		lis.Close()
	}

	return client, registry, healChan, cleanup
}

func TestRegisterRPC(t *testing.T) {
	client, registry, _, cleanup := setupTestServer(t, nil)
	defer cleanup()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.Register(ctx, &pb.RegisterRequest{
		Hostname:          "NVWS01",
		OsVersion:         "Windows 10",
		AgentVersion:      "0.3.0",
		InstalledSoftware: []string{"ConnectWise", "Office365"},
		MacAddress:        "aa:bb:cc:dd:ee:ff",
		NeedsCertificates: false,
	})
	if err != nil {
		t.Fatalf("Register: %v", err)
	}

	if resp.AgentId == "" {
		t.Fatal("AgentId should not be empty")
	}
	if resp.CheckIntervalSeconds != 300 {
		t.Fatalf("expected interval 300, got %d", resp.CheckIntervalSeconds)
	}
	if len(resp.EnabledChecks) != 6 {
		t.Fatalf("expected 6 enabled checks, got %d", len(resp.EnabledChecks))
	}
	if resp.CapabilityTier != pb.CapabilityTier_MONITOR_ONLY {
		t.Fatalf("expected MONITOR_ONLY tier, got %v", resp.CapabilityTier)
	}

	// Verify registry
	if registry.ConnectedCount() != 1 {
		t.Fatalf("expected 1 registered agent, got %d", registry.ConnectedCount())
	}
	if !registry.HasAgentForHost("NVWS01") {
		t.Fatal("NVWS01 should be in registry")
	}
}

func TestRegisterWithCertEnrollment(t *testing.T) {
	// Create a temp CA
	caDir := t.TempDir()
	agentCA := ca.New(caDir)
	if err := agentCA.EnsureCA(); err != nil {
		t.Fatalf("EnsureCA: %v", err)
	}

	client, _, _, cleanup := setupTestServer(t, agentCA)
	defer cleanup()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.Register(ctx, &pb.RegisterRequest{
		Hostname:          "NVWS01",
		NeedsCertificates: true,
	})
	if err != nil {
		t.Fatalf("Register: %v", err)
	}

	if len(resp.CaCertPem) == 0 {
		t.Fatal("CaCertPem should not be empty when NeedsCertificates=true")
	}
	if len(resp.AgentCertPem) == 0 {
		t.Fatal("AgentCertPem should not be empty")
	}
	if len(resp.AgentKeyPem) == 0 {
		t.Fatal("AgentKeyPem should not be empty")
	}
}

func TestRegisterWithoutCA(t *testing.T) {
	client, _, _, cleanup := setupTestServer(t, nil)
	defer cleanup()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp, err := client.Register(ctx, &pb.RegisterRequest{
		Hostname:          "NVWS01",
		NeedsCertificates: true,
	})
	if err != nil {
		t.Fatalf("Register: %v", err)
	}

	// Should succeed but without certs
	if len(resp.CaCertPem) != 0 {
		t.Fatal("CaCertPem should be empty when CA not configured")
	}
}

func TestReportDriftStream(t *testing.T) {
	client, registry, healChan, cleanup := setupTestServer(t, nil)
	defer cleanup()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	// Register agent first
	regResp, err := client.Register(ctx, &pb.RegisterRequest{Hostname: "NVWS01"})
	if err != nil {
		t.Fatalf("Register: %v", err)
	}

	// Open drift stream
	stream, err := client.ReportDrift(ctx)
	if err != nil {
		t.Fatalf("ReportDrift: %v", err)
	}

	// Send passing check — should NOT trigger heal
	err = stream.Send(&pb.DriftEvent{
		AgentId:   regResp.AgentId,
		Hostname:  "NVWS01",
		CheckType: "bitlocker",
		Passed:    true,
		Timestamp: time.Now().Unix(),
	})
	if err != nil {
		t.Fatalf("Send passing drift: %v", err)
	}

	ack, err := stream.Recv()
	if err != nil {
		t.Fatalf("Recv ack: %v", err)
	}
	if !ack.Received {
		t.Fatal("expected ack.Received = true")
	}
	if ack.HealCommand != nil {
		t.Fatal("passing check should not have heal command")
	}

	// Send failing firewall check — SHOULD trigger heal
	err = stream.Send(&pb.DriftEvent{
		AgentId:      regResp.AgentId,
		Hostname:     "NVWS01",
		CheckType:    "firewall",
		Passed:       false,
		Expected:     "enabled",
		Actual:       "disabled",
		HipaaControl: "164.312(a)(1)",
		Timestamp:    time.Now().Unix(),
	})
	if err != nil {
		t.Fatalf("Send failing drift: %v", err)
	}

	ack, err = stream.Recv()
	if err != nil {
		t.Fatalf("Recv ack: %v", err)
	}
	if ack.HealCommand == nil {
		t.Fatal("failing firewall check should have heal command")
	}
	if ack.HealCommand.Action != "enable" {
		t.Fatalf("expected heal action 'enable', got %q", ack.HealCommand.Action)
	}
	if ack.HealCommand.CheckType != "firewall" {
		t.Fatalf("expected check type 'firewall', got %q", ack.HealCommand.CheckType)
	}

	// Verify drift was routed to heal channel
	select {
	case req := <-healChan:
		if req.CheckType != "firewall_status" { // mapped from "firewall"
			t.Fatalf("expected mapped check type 'firewall_status', got %q", req.CheckType)
		}
		if req.Hostname != "NVWS01" {
			t.Fatalf("expected hostname NVWS01, got %s", req.Hostname)
		}
	case <-time.After(time.Second):
		t.Fatal("expected heal request on channel")
	}

	// Verify drift count
	agent := registry.GetAgent(regResp.AgentId)
	if agent == nil {
		t.Fatal("agent should be in registry")
	}
	if agent.DriftCount != 2 { // 1 pass + 1 fail
		t.Fatalf("expected drift count 2, got %d", agent.DriftCount)
	}

	stream.CloseSend()
}

func TestReportHealing(t *testing.T) {
	client, _, _, cleanup := setupTestServer(t, nil)
	defer cleanup()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	ack, err := client.ReportHealing(ctx, &pb.HealingResult{
		AgentId:   "go-WS01-abc",
		Hostname:  "NVWS01",
		CheckType: "firewall",
		Success:   true,
		Timestamp: time.Now().Unix(),
		Artifacts: map[string]string{"recovery_key": "123456-789012"},
	})
	if err != nil {
		t.Fatalf("ReportHealing: %v", err)
	}
	if !ack.Received {
		t.Fatal("expected ack.Received = true")
	}
}

func TestHeartbeat(t *testing.T) {
	client, registry, _, cleanup := setupTestServer(t, nil)
	defer cleanup()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	// Register agent
	regResp, err := client.Register(ctx, &pb.RegisterRequest{Hostname: "NVWS01"})
	if err != nil {
		t.Fatalf("Register: %v", err)
	}

	// Queue a heal command
	registry.QueueHealCommand("NVWS01", &pb.HealCommand{
		CommandId: "queued-001",
		CheckType: "defender",
		Action:    "start",
	})

	// Send heartbeat
	resp, err := client.Heartbeat(ctx, &pb.HeartbeatRequest{
		AgentId:   regResp.AgentId,
		Timestamp: time.Now().Unix(),
	})
	if err != nil {
		t.Fatalf("Heartbeat: %v", err)
	}
	if !resp.Acknowledged {
		t.Fatal("expected acknowledged")
	}
	if len(resp.PendingCommands) != 1 {
		t.Fatalf("expected 1 pending command, got %d", len(resp.PendingCommands))
	}
	if resp.PendingCommands[0].CommandId != "queued-001" {
		t.Fatalf("expected queued-001, got %s", resp.PendingCommands[0].CommandId)
	}

	// Second heartbeat should have no pending
	resp, err = client.Heartbeat(ctx, &pb.HeartbeatRequest{
		AgentId:   regResp.AgentId,
		Timestamp: time.Now().Unix(),
	})
	if err != nil {
		t.Fatalf("Heartbeat 2: %v", err)
	}
	if len(resp.PendingCommands) != 0 {
		t.Fatalf("expected 0 pending commands, got %d", len(resp.PendingCommands))
	}
}

func TestReportRMMStatus(t *testing.T) {
	client, registry, _, cleanup := setupTestServer(t, nil)
	defer cleanup()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	// Register agent
	regResp, err := client.Register(ctx, &pb.RegisterRequest{Hostname: "NVWS01"})
	if err != nil {
		t.Fatalf("Register: %v", err)
	}

	ack, err := client.ReportRMMStatus(ctx, &pb.RMMStatusReport{
		AgentId:  regResp.AgentId,
		Hostname: "NVWS01",
		DetectedAgents: []*pb.RMMAgent{
			{Name: "ConnectWise", Version: "23.1", Running: true, ServiceName: "ScreenConnect"},
			{Name: "Datto", Version: "1.5", Running: false, ServiceName: "DattoAgent"},
		},
		Timestamp: time.Now().Unix(),
	})
	if err != nil {
		t.Fatalf("ReportRMMStatus: %v", err)
	}
	if !ack.Received {
		t.Fatal("expected ack.Received = true")
	}

	// Verify RMM agents stored
	agent := registry.GetAgent(regResp.AgentId)
	if len(agent.RMMAgents) != 2 {
		t.Fatalf("expected 2 RMM agents, got %d", len(agent.RMMAgents))
	}
}

func TestDriftStreamEOF(t *testing.T) {
	client, _, _, cleanup := setupTestServer(t, nil)
	defer cleanup()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	stream, err := client.ReportDrift(ctx)
	if err != nil {
		t.Fatalf("ReportDrift: %v", err)
	}

	// Close without sending — server should handle gracefully
	stream.CloseSend()

	// Try to recv — should get EOF
	_, err = stream.Recv()
	if err != io.EOF {
		t.Fatalf("expected EOF, got %v", err)
	}
}
