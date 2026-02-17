// Package grpcserver implements the gRPC server for Go agent communication.
package grpcserver

import (
	"sync"
	"time"

	pb "github.com/osiriscare/appliance/proto"
)

// AgentState tracks the state of a connected Go agent.
type AgentState struct {
	AgentID       string
	Hostname      string
	hostnameLower string
	Tier          pb.CapabilityTier
	ConnectedAt   time.Time
	LastHeartbeat time.Time
	DriftCount    int64
	RMMAgents     []*pb.RMMAgent
	pendingCmds   []*pb.HealCommand
}

// AgentRegistry tracks connected Go agents with command queue support.
type AgentRegistry struct {
	mu            sync.RWMutex
	agents        map[string]*AgentState    // agent_id -> state
	hostnameIndex map[string]string         // hostname_lower -> agent_id
	configVersion int
}

// NewAgentRegistry creates a new registry.
func NewAgentRegistry() *AgentRegistry {
	return &AgentRegistry{
		agents:        make(map[string]*AgentState),
		hostnameIndex: make(map[string]string),
		configVersion: 1,
	}
}

// Register adds or updates an agent in the registry.
func (r *AgentRegistry) Register(state *AgentState) {
	r.mu.Lock()
	defer r.mu.Unlock()

	r.agents[state.AgentID] = state
	r.hostnameIndex[state.hostnameLower] = state.AgentID
}

// Unregister removes an agent from the registry.
func (r *AgentRegistry) Unregister(agentID string) {
	r.mu.Lock()
	defer r.mu.Unlock()

	if agent, ok := r.agents[agentID]; ok {
		delete(r.hostnameIndex, agent.hostnameLower)
		delete(r.agents, agentID)
	}
}

// GetAgent returns agent state by ID, or nil if not found.
func (r *AgentRegistry) GetAgent(agentID string) *AgentState {
	r.mu.RLock()
	defer r.mu.RUnlock()

	return r.agents[agentID]
}

// GetAgentByHostname returns agent state by hostname (case-insensitive).
func (r *AgentRegistry) GetAgentByHostname(hostname string) *AgentState {
	r.mu.RLock()
	defer r.mu.RUnlock()

	agentID, ok := r.hostnameIndex[toLower(hostname)]
	if !ok {
		return nil
	}
	return r.agents[agentID]
}

// HasAgentForHost checks if a Go agent is connected for the given hostname.
func (r *AgentRegistry) HasAgentForHost(hostname string) bool {
	r.mu.RLock()
	defer r.mu.RUnlock()

	_, ok := r.hostnameIndex[toLower(hostname)]
	return ok
}

// ConnectedCount returns the number of connected agents.
func (r *AgentRegistry) ConnectedCount() int {
	r.mu.RLock()
	defer r.mu.RUnlock()

	return len(r.agents)
}

// QueueHealCommand queues a heal command for an agent by hostname.
// Returns true if the agent was found and the command was queued.
func (r *AgentRegistry) QueueHealCommand(hostname string, cmd *pb.HealCommand) bool {
	r.mu.Lock()
	defer r.mu.Unlock()

	agentID, ok := r.hostnameIndex[toLower(hostname)]
	if !ok {
		return false
	}
	agent, ok := r.agents[agentID]
	if !ok {
		return false
	}
	agent.pendingCmds = append(agent.pendingCmds, cmd)
	return true
}

// PopPendingCommands returns and clears pending commands for an agent.
func (r *AgentRegistry) PopPendingCommands(agentID string) []*pb.HealCommand {
	r.mu.Lock()
	defer r.mu.Unlock()

	agent, ok := r.agents[agentID]
	if !ok || len(agent.pendingCmds) == 0 {
		return nil
	}
	cmds := agent.pendingCmds
	agent.pendingCmds = nil
	return cmds
}

// AllAgents returns a snapshot of all agent states.
func (r *AgentRegistry) AllAgents() []*AgentState {
	r.mu.RLock()
	defer r.mu.RUnlock()

	agents := make([]*AgentState, 0, len(r.agents))
	for _, agent := range r.agents {
		agents = append(agents, agent)
	}
	return agents
}

// toLower is a simple lowercase helper to avoid importing strings for one call.
func toLower(s string) string {
	b := make([]byte, len(s))
	for i := range s {
		c := s[i]
		if c >= 'A' && c <= 'Z' {
			c += 'a' - 'A'
		}
		b[i] = c
	}
	return string(b)
}
