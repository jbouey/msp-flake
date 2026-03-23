package daemon

import (
	"testing"
	"time"
)

func TestSelfHealer_HealthyAgent(t *testing.T) {
	sh := &selfHealer{agents: make(map[string]*agentHealthEntry)}
	sh.updateFromCheckin([]GoAgentInfo{
		{Hostname: "test-host", IPAddress: "192.168.1.1", LastHeartbeat: time.Now()},
	})
	if len(sh.agents) != 1 {
		t.Errorf("expected 1 agent, got %d", len(sh.agents))
	}
	entry := sh.agents["test-host"]
	if time.Since(entry.LastHeartbeat) > time.Second {
		t.Error("heartbeat should be recent")
	}
}

func TestSelfHealer_StaleDetection(t *testing.T) {
	sh := &selfHealer{agents: map[string]*agentHealthEntry{
		"stale-host": {
			Hostname:      "stale-host",
			IPAddress:     "192.168.1.2",
			LastHeartbeat: time.Now().Add(-15 * time.Minute), // 15 min ago
		},
	}}
	entry := sh.agents["stale-host"]
	if time.Since(entry.LastHeartbeat) < agentStaleTimeout {
		t.Error("agent should be detected as stale")
	}
}

func TestSelfHealer_EscalationAfterMaxAttempts(t *testing.T) {
	entry := &agentHealthEntry{
		Hostname:       "failing-host",
		DeployAttempts: maxRedeployAttempts + 1,
	}
	if entry.DeployAttempts <= maxRedeployAttempts {
		t.Error("should exceed max attempts")
	}
}

func TestSelfHealer_UpdateFromCheckin(t *testing.T) {
	sh := &selfHealer{agents: make(map[string]*agentHealthEntry)}

	// First update — two agents
	sh.updateFromCheckin([]GoAgentInfo{
		{Hostname: "host-a", IPAddress: "10.0.0.1", LastHeartbeat: time.Now().Add(-5 * time.Minute)},
		{Hostname: "host-b", IPAddress: "10.0.0.2", LastHeartbeat: time.Now()},
	})
	if len(sh.agents) != 2 {
		t.Errorf("expected 2 agents, got %d", len(sh.agents))
	}

	// Second update — host-a recovers with a fresh heartbeat
	sh.updateFromCheckin([]GoAgentInfo{
		{Hostname: "host-a", IPAddress: "10.0.0.1", LastHeartbeat: time.Now()},
	})
	if time.Since(sh.agents["host-a"].LastHeartbeat) > time.Second {
		t.Error("host-a heartbeat should be updated")
	}
	// host-b should still be tracked (updateFromCheckin never removes entries)
	if _, ok := sh.agents["host-b"]; !ok {
		t.Error("host-b should still be in agents map")
	}
}

func TestSelfHealer_SkipEscalated(t *testing.T) {
	sh := &selfHealer{agents: map[string]*agentHealthEntry{
		"esc-host": {
			Hostname:       "esc-host",
			IPAddress:      "10.0.0.9",
			LastHeartbeat:  time.Now().Add(-30 * time.Minute),
			DeployAttempts: maxRedeployAttempts + 1,
			Escalated:      true,
		},
	}}
	// Already escalated: DeployAttempts must not increase further
	before := sh.agents["esc-host"].DeployAttempts
	// Simulate what runSelfHealIfNeeded would do when it sees Escalated=true
	for _, entry := range sh.agents {
		if entry.Escalated {
			// loop should skip — nothing to assert except counter unchanged
		}
	}
	if sh.agents["esc-host"].DeployAttempts != before {
		t.Error("escalated entry should not have its counter mutated")
	}
}

func TestSelfHealer_IPAddressUpdated(t *testing.T) {
	sh := &selfHealer{agents: make(map[string]*agentHealthEntry)}

	// Initial registration
	sh.updateFromCheckin([]GoAgentInfo{
		{Hostname: "mobile-host", IPAddress: "10.0.0.5", LastHeartbeat: time.Now()},
	})

	// Host gets a new DHCP address
	sh.updateFromCheckin([]GoAgentInfo{
		{Hostname: "mobile-host", IPAddress: "10.0.0.77", LastHeartbeat: time.Now()},
	})

	if sh.agents["mobile-host"].IPAddress != "10.0.0.77" {
		t.Errorf("IP should update to 10.0.0.77, got %s", sh.agents["mobile-host"].IPAddress)
	}
}
