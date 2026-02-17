package l2bridge

import (
	"bufio"
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// startMockSidecar creates a mock L2 sidecar that responds to JSON-RPC requests.
func startMockSidecar(t *testing.T, socketPath string, handler func(req jsonRPCRequest) interface{}) net.Listener {
	t.Helper()

	ln, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatalf("listen: %v", err)
	}

	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return // listener closed
			}
			go func(c net.Conn) {
				defer c.Close()
				reader := bufio.NewReader(c)
				for {
					line, err := reader.ReadBytes('\n')
					if err != nil {
						return
					}

					var req jsonRPCRequest
					if err := json.Unmarshal(line, &req); err != nil {
						return
					}

					result := handler(req)

					resp := jsonRPCResponse{
						JSONRPC: "2.0",
						ID:      req.ID,
					}

					if errVal, ok := result.(*jsonRPCError); ok {
						resp.Error = errVal
					} else {
						data, _ := json.Marshal(result)
						resp.Result = data
					}

					data, _ := json.Marshal(resp)
					data = append(data, '\n')
					c.Write(data)
				}
			}(conn)
		}
	}()

	return ln
}

func TestPlan(t *testing.T) {
	dir := t.TempDir()
	sock := filepath.Join(dir, "l2.sock")

	ln := startMockSidecar(t, sock, func(req jsonRPCRequest) interface{} {
		if req.Method != "plan" {
			return &jsonRPCError{Code: -32601, Message: "method not found"}
		}

		return LLMDecision{
			IncidentID:        "inc-001",
			RecommendedAction: "restart_service",
			ActionParams:      map[string]interface{}{"service": "dns"},
			Confidence:        0.85,
			Reasoning:         "DNS service down, restart is safe",
			RequiresApproval:  false,
			EscalateToL3:      false,
		}
	})
	defer ln.Close()

	client := NewClient(sock, 5*time.Second)
	if err := client.Connect(); err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer client.Close()

	incident := &Incident{
		ID:           "inc-001",
		SiteID:       "site-01",
		HostID:       "ws01",
		IncidentType: "service_dns",
		Severity:     "high",
		RawData: map[string]interface{}{
			"check_type":     "service_dns",
			"drift_detected": true,
		},
		CreatedAt: time.Now().UTC().Format(time.RFC3339),
	}

	decision, err := client.Plan(incident)
	if err != nil {
		t.Fatalf("plan: %v", err)
	}

	if decision.IncidentID != "inc-001" {
		t.Fatalf("expected inc-001, got %s", decision.IncidentID)
	}
	if decision.RecommendedAction != "restart_service" {
		t.Fatalf("expected restart_service, got %s", decision.RecommendedAction)
	}
	if decision.Confidence != 0.85 {
		t.Fatalf("expected confidence 0.85, got %f", decision.Confidence)
	}
	if !decision.ShouldExecute() {
		t.Fatal("expected ShouldExecute=true")
	}
}

func TestHealth(t *testing.T) {
	dir := t.TempDir()
	sock := filepath.Join(dir, "l2.sock")

	ln := startMockSidecar(t, sock, func(req jsonRPCRequest) interface{} {
		if req.Method == "health" {
			return map[string]interface{}{"status": "ok", "uptime": 123}
		}
		return &jsonRPCError{Code: -32601, Message: "method not found"}
	})
	defer ln.Close()

	client := NewClient(sock, 5*time.Second)
	if err := client.Connect(); err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer client.Close()

	if err := client.Health(); err != nil {
		t.Fatalf("health check failed: %v", err)
	}
}

func TestPlanEscalateToL3(t *testing.T) {
	dir := t.TempDir()
	sock := filepath.Join(dir, "l2.sock")

	ln := startMockSidecar(t, sock, func(req jsonRPCRequest) interface{} {
		return LLMDecision{
			IncidentID:        "inc-002",
			RecommendedAction: "escalate",
			Confidence:        0.3,
			Reasoning:         "Low confidence, needs human review",
			RequiresApproval:  true,
			EscalateToL3:      true,
		}
	})
	defer ln.Close()

	client := NewClient(sock, 5*time.Second)
	client.Connect()
	defer client.Close()

	decision, err := client.Plan(&Incident{ID: "inc-002", IncidentType: "unknown"})
	if err != nil {
		t.Fatalf("plan: %v", err)
	}

	if decision.ShouldExecute() {
		t.Fatal("expected ShouldExecute=false for escalation")
	}
	if !decision.EscalateToL3 {
		t.Fatal("expected EscalateToL3=true")
	}
}

func TestLowConfidenceRequiresApproval(t *testing.T) {
	decision := &LLMDecision{
		Confidence:       0.4,
		RequiresApproval: true,
		EscalateToL3:     false,
	}

	if decision.ShouldExecute() {
		t.Fatal("expected ShouldExecute=false for low confidence with approval required")
	}
}

func TestConnectFailure(t *testing.T) {
	client := NewClient("/nonexistent/l2.sock", 2*time.Second)
	err := client.Connect()
	if err == nil {
		t.Fatal("expected connection error for nonexistent socket")
	}
}

func TestPlanNotConnected(t *testing.T) {
	client := NewClient("/tmp/noexist.sock", 2*time.Second)
	_, err := client.Plan(&Incident{ID: "inc-003"})
	if err == nil {
		t.Fatal("expected error when not connected")
	}
}

func TestPlanWithRetryReconnects(t *testing.T) {
	dir := t.TempDir()
	sock := filepath.Join(dir, "l2.sock")

	ln := startMockSidecar(t, sock, func(req jsonRPCRequest) interface{} {
		return LLMDecision{
			IncidentID:        "inc-004",
			RecommendedAction: "restart_service",
			Confidence:        0.9,
		}
	})
	defer ln.Close()

	client := NewClient(sock, 5*time.Second)
	// Start connected
	client.Connect()

	// Simulate connection drop
	client.mu.Lock()
	client.conn.Close()
	client.conn = nil
	client.reader = nil
	client.mu.Unlock()

	// PlanWithRetry should reconnect
	decision, err := client.PlanWithRetry(&Incident{ID: "inc-004"}, 2)
	if err != nil {
		t.Fatalf("plan with retry: %v", err)
	}
	if decision.IncidentID != "inc-004" {
		t.Fatalf("expected inc-004, got %s", decision.IncidentID)
	}
}

func TestRPCError(t *testing.T) {
	dir := t.TempDir()
	sock := filepath.Join(dir, "l2.sock")

	ln := startMockSidecar(t, sock, func(req jsonRPCRequest) interface{} {
		return &jsonRPCError{Code: -32000, Message: "LLM API rate limited"}
	})
	defer ln.Close()

	client := NewClient(sock, 5*time.Second)
	client.Connect()
	defer client.Close()

	_, err := client.Plan(&Incident{ID: "inc-005"})
	if err == nil {
		t.Fatal("expected RPC error")
	}
	if err.Error() != "L2 plan: RPC error -32000: LLM API rate limited" {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestIsConnected(t *testing.T) {
	dir := t.TempDir()
	sock := filepath.Join(dir, "l2.sock")

	ln := startMockSidecar(t, sock, func(req jsonRPCRequest) interface{} {
		return map[string]interface{}{"status": "ok"}
	})
	defer ln.Close()

	client := NewClient(sock, 5*time.Second)

	if client.IsConnected() {
		t.Fatal("expected not connected before Connect()")
	}

	client.Connect()
	if !client.IsConnected() {
		t.Fatal("expected connected after Connect()")
	}

	client.Close()
	if client.IsConnected() {
		t.Fatal("expected not connected after Close()")
	}
}

func TestMultipleRequests(t *testing.T) {
	dir := t.TempDir()
	sock := filepath.Join(dir, "l2.sock")

	callCount := 0
	ln := startMockSidecar(t, sock, func(req jsonRPCRequest) interface{} {
		callCount++
		return LLMDecision{
			IncidentID:        "inc",
			RecommendedAction: "action_" + string(rune('A'+callCount-1)),
			Confidence:        0.9,
		}
	})
	defer ln.Close()

	client := NewClient(sock, 5*time.Second)
	client.Connect()
	defer client.Close()

	for i := 0; i < 5; i++ {
		_, err := client.Plan(&Incident{ID: "inc"})
		if err != nil {
			t.Fatalf("plan %d: %v", i, err)
		}
	}

	if callCount != 5 {
		t.Fatalf("expected 5 calls, got %d", callCount)
	}
}

func TestShouldExecuteDecisions(t *testing.T) {
	tests := []struct {
		name     string
		decision LLMDecision
		want     bool
	}{
		{
			name:     "high confidence, no escalation",
			decision: LLMDecision{Confidence: 0.9, EscalateToL3: false, RequiresApproval: false},
			want:     true,
		},
		{
			name:     "exact threshold",
			decision: LLMDecision{Confidence: 0.6, EscalateToL3: false, RequiresApproval: false},
			want:     true,
		},
		{
			name:     "below threshold",
			decision: LLMDecision{Confidence: 0.59, EscalateToL3: false, RequiresApproval: false},
			want:     false,
		},
		{
			name:     "escalate to L3",
			decision: LLMDecision{Confidence: 0.9, EscalateToL3: true, RequiresApproval: false},
			want:     false,
		},
		{
			name:     "requires approval",
			decision: LLMDecision{Confidence: 0.9, EscalateToL3: false, RequiresApproval: true},
			want:     false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := tt.decision.ShouldExecute(); got != tt.want {
				t.Errorf("ShouldExecute() = %v, want %v", got, tt.want)
			}
		})
	}
}

// Suppress unused os import warning
var _ = os.TempDir
