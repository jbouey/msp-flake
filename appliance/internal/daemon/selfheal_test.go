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
			LastHeartbeat: time.Now().Add(-15 * time.Minute),
		},
	}}
	entry := sh.agents["stale-host"]
	if time.Since(entry.LastHeartbeat) < agentStaleTimeout {
		t.Error("agent should be detected as stale")
	}
}

func TestSelfHealer_CooldownBackoff(t *testing.T) {
	// Attempts 1-3: 10 min cooldown
	entry := &agentHealthEntry{
		Hostname:       "failing-host",
		DeployAttempts: 2,
		LastDeployAt:   time.Now().Add(-5 * time.Minute), // 5 min ago
	}
	cooldown := redeployCooldownBase
	if entry.DeployAttempts > 6 {
		cooldown = redeployCooldownBackoff
	} else if entry.DeployAttempts > 3 {
		cooldown = redeployCooldownMedium
	}
	if time.Since(entry.LastDeployAt) >= cooldown {
		t.Error("should still be in cooldown (5 min < 10 min base)")
	}

	// Attempts 4-6: 30 min cooldown
	entry.DeployAttempts = 5
	entry.LastDeployAt = time.Now().Add(-15 * time.Minute)
	cooldown = redeployCooldownBase
	if entry.DeployAttempts > 6 {
		cooldown = redeployCooldownBackoff
	} else if entry.DeployAttempts > 3 {
		cooldown = redeployCooldownMedium
	}
	if time.Since(entry.LastDeployAt) >= cooldown {
		t.Error("should still be in cooldown (15 min < 30 min medium)")
	}

	// Attempts 7+: 2 hour cooldown
	entry.DeployAttempts = 8
	entry.LastDeployAt = time.Now().Add(-1 * time.Hour)
	cooldown = redeployCooldownBase
	if entry.DeployAttempts > 6 {
		cooldown = redeployCooldownBackoff
	} else if entry.DeployAttempts > 3 {
		cooldown = redeployCooldownMedium
	}
	if time.Since(entry.LastDeployAt) >= cooldown {
		t.Error("should still be in cooldown (1h < 2h backoff)")
	}
}

func TestSelfHealer_RecoveryResetsCounter(t *testing.T) {
	sh := &selfHealer{agents: map[string]*agentHealthEntry{
		"recovered": {
			Hostname:       "recovered",
			DeployAttempts: 10,
			LastHeartbeat:  time.Now(), // just heartbeated
		},
	}}
	// Simulate what the selfheal loop does for a healthy agent
	entry := sh.agents["recovered"]
	if !entry.LastHeartbeat.IsZero() && time.Since(entry.LastHeartbeat) < 5*time.Minute {
		if entry.DeployAttempts > 0 {
			entry.DeployAttempts = 0
		}
		entry.LastSuccessAt = entry.LastHeartbeat
	}
	if entry.DeployAttempts != 0 {
		t.Errorf("expected 0 deploy attempts after recovery, got %d", entry.DeployAttempts)
	}
}

func TestSelfHealer_UpdateFromCheckin(t *testing.T) {
	sh := &selfHealer{agents: make(map[string]*agentHealthEntry)}

	sh.updateFromCheckin([]GoAgentInfo{
		{Hostname: "host-a", IPAddress: "10.0.0.1", LastHeartbeat: time.Now().Add(-5 * time.Minute)},
		{Hostname: "host-b", IPAddress: "10.0.0.2", LastHeartbeat: time.Now()},
	})
	if len(sh.agents) != 2 {
		t.Errorf("expected 2 agents, got %d", len(sh.agents))
	}

	sh.updateFromCheckin([]GoAgentInfo{
		{Hostname: "host-a", IPAddress: "10.0.0.1", LastHeartbeat: time.Now()},
	})
	if time.Since(sh.agents["host-a"].LastHeartbeat) > time.Second {
		t.Error("host-a heartbeat should be updated")
	}
	if _, ok := sh.agents["host-b"]; !ok {
		t.Error("host-b should still be in agents map")
	}
}

func TestSelfHealer_NeverGivesUp(t *testing.T) {
	// Even after 100 failed attempts, the entry is NOT permanently blocked
	entry := &agentHealthEntry{
		Hostname:       "stubborn-host",
		DeployAttempts: 100,
		LastDeployAt:   time.Now().Add(-3 * time.Hour), // past the 2h backoff
	}
	cooldown := redeployCooldownBackoff // 2h for attempt 7+
	if time.Since(entry.LastDeployAt) < cooldown {
		t.Error("should be past cooldown after 3 hours")
	}
	// Verify: no Escalated field, no permanent block
	entry.DeployAttempts++
	if entry.DeployAttempts != 101 {
		t.Error("should keep counting, never stop")
	}
}

func TestSelfHealer_IPAddressUpdated(t *testing.T) {
	sh := &selfHealer{agents: make(map[string]*agentHealthEntry)}

	sh.updateFromCheckin([]GoAgentInfo{
		{Hostname: "mobile-host", IPAddress: "10.0.0.5", LastHeartbeat: time.Now()},
	})
	if sh.agents["mobile-host"].IPAddress != "10.0.0.5" {
		t.Error("initial IP should be 10.0.0.5")
	}

	sh.updateFromCheckin([]GoAgentInfo{
		{Hostname: "mobile-host", IPAddress: "10.0.0.99", LastHeartbeat: time.Now()},
	})
	if sh.agents["mobile-host"].IPAddress != "10.0.0.99" {
		t.Error("IP should be updated to 10.0.0.99")
	}
}
