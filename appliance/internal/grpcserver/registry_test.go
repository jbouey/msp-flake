package grpcserver

import (
	"testing"
	"time"

	pb "github.com/osiriscare/appliance/proto"
)

func newTestState(id, hostname string) *AgentState {
	now := time.Now().UTC()
	return &AgentState{
		AgentID:       id,
		Hostname:      hostname,
		hostnameLower: toLower(hostname),
		Tier:          pb.CapabilityTier_MONITOR_ONLY,
		ConnectedAt:   now,
		LastHeartbeat: now,
	}
}

func TestRegisterAndLookup(t *testing.T) {
	r := NewAgentRegistry()

	state := newTestState("go-WS01-abc", "WS01")
	r.Register(state)

	if r.ConnectedCount() != 1 {
		t.Fatalf("expected 1 agent, got %d", r.ConnectedCount())
	}

	got := r.GetAgent("go-WS01-abc")
	if got == nil {
		t.Fatal("GetAgent returned nil")
	}
	if got.Hostname != "WS01" {
		t.Fatalf("expected hostname WS01, got %s", got.Hostname)
	}
}

func TestHostnameLookupCaseInsensitive(t *testing.T) {
	r := NewAgentRegistry()
	r.Register(newTestState("go-WS01-abc", "NVWS01"))

	tests := []struct {
		hostname string
		want     bool
	}{
		{"NVWS01", true},
		{"nvws01", true},
		{"NvWs01", true},
		{"NVWS02", false},
	}

	for _, tt := range tests {
		got := r.HasAgentForHost(tt.hostname)
		if got != tt.want {
			t.Errorf("HasAgentForHost(%q) = %v, want %v", tt.hostname, got, tt.want)
		}

		agent := r.GetAgentByHostname(tt.hostname)
		if tt.want && agent == nil {
			t.Errorf("GetAgentByHostname(%q) returned nil, expected agent", tt.hostname)
		}
		if !tt.want && agent != nil {
			t.Errorf("GetAgentByHostname(%q) returned agent, expected nil", tt.hostname)
		}
	}
}

func TestUnregister(t *testing.T) {
	r := NewAgentRegistry()
	r.Register(newTestState("go-WS01-abc", "WS01"))
	r.Register(newTestState("go-WS02-def", "WS02"))

	if r.ConnectedCount() != 2 {
		t.Fatalf("expected 2 agents, got %d", r.ConnectedCount())
	}

	r.Unregister("go-WS01-abc")

	if r.ConnectedCount() != 1 {
		t.Fatalf("expected 1 agent after unregister, got %d", r.ConnectedCount())
	}

	if r.GetAgent("go-WS01-abc") != nil {
		t.Fatal("agent should be nil after unregister")
	}
	if !r.HasAgentForHost("WS02") {
		t.Fatal("WS02 should still be registered")
	}
	if r.HasAgentForHost("WS01") {
		t.Fatal("WS01 hostname should be removed from index")
	}
}

func TestQueueHealCommand(t *testing.T) {
	r := NewAgentRegistry()
	r.Register(newTestState("go-WS01-abc", "WS01"))

	cmd := &pb.HealCommand{
		CommandId: "heal-001",
		CheckType: "firewall",
		Action:    "enable",
	}

	if !r.QueueHealCommand("WS01", cmd) {
		t.Fatal("QueueHealCommand returned false for registered agent")
	}

	if r.QueueHealCommand("WS99", cmd) {
		t.Fatal("QueueHealCommand returned true for unknown agent")
	}

	// Pop pending
	cmds := r.PopPendingCommands("go-WS01-abc")
	if len(cmds) != 1 {
		t.Fatalf("expected 1 pending command, got %d", len(cmds))
	}
	if cmds[0].CommandId != "heal-001" {
		t.Fatalf("expected command heal-001, got %s", cmds[0].CommandId)
	}

	// Second pop should be empty
	cmds = r.PopPendingCommands("go-WS01-abc")
	if len(cmds) != 0 {
		t.Fatalf("expected 0 pending commands after pop, got %d", len(cmds))
	}
}

func TestAllAgents(t *testing.T) {
	r := NewAgentRegistry()
	r.Register(newTestState("go-WS01-abc", "WS01"))
	r.Register(newTestState("go-WS02-def", "WS02"))
	r.Register(newTestState("go-WS03-ghi", "WS03"))

	all := r.AllAgents()
	if len(all) != 3 {
		t.Fatalf("expected 3 agents, got %d", len(all))
	}
}

func TestHasActiveAgentForHost(t *testing.T) {
	r := NewAgentRegistry()

	// Active agent — heartbeat 1 minute ago
	active := newTestState("go-WS01-abc", "NVWS01")
	active.LastHeartbeat = time.Now().Add(-1 * time.Minute)
	r.Register(active)

	// Stale agent — heartbeat 20 minutes ago
	stale := newTestState("go-WS02-def", "NVWS02")
	stale.LastHeartbeat = time.Now().Add(-20 * time.Minute)
	r.Register(stale)

	// Disk-loaded agent — never heartbeated (zero time)
	diskOnly := newTestState("go-DC01-ghi", "NVDC01")
	diskOnly.LastHeartbeat = time.Time{}
	r.Register(diskOnly)

	maxStale := 10 * time.Minute

	tests := []struct {
		hostname string
		wantReg  bool // HasAgentForHost
		wantAct  bool // HasActiveAgentForHost
	}{
		{"NVWS01", true, true},   // active heartbeat
		{"nvws01", true, true},   // case insensitive
		{"NVWS02", true, false},  // stale heartbeat
		{"NVDC01", true, false},  // never heartbeated
		{"NVWS99", false, false}, // not registered
	}

	for _, tt := range tests {
		gotReg := r.HasAgentForHost(tt.hostname)
		if gotReg != tt.wantReg {
			t.Errorf("HasAgentForHost(%q) = %v, want %v", tt.hostname, gotReg, tt.wantReg)
		}
		gotAct := r.HasActiveAgentForHost(tt.hostname, maxStale)
		if gotAct != tt.wantAct {
			t.Errorf("HasActiveAgentForHost(%q) = %v, want %v", tt.hostname, gotAct, tt.wantAct)
		}
	}
}

func TestToLower(t *testing.T) {
	tests := []struct {
		in, want string
	}{
		{"HELLO", "hello"},
		{"Hello", "hello"},
		{"hello", "hello"},
		{"NVWS01", "nvws01"},
		{"", ""},
		{"123ABC", "123abc"},
	}
	for _, tt := range tests {
		got := toLower(tt.in)
		if got != tt.want {
			t.Errorf("toLower(%q) = %q, want %q", tt.in, got, tt.want)
		}
	}
}
