// Package grpcserver implements the gRPC server for Go agent communication.
package grpcserver

import (
	"encoding/json"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
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
	DriftCount    atomic.Int64
	ChecksPassed  atomic.Int64
	ChecksTotal   atomic.Int64
	AgentVersion  string
	OSVersion     string // e.g. "Windows 10", "Darwin 21.6.0", "Linux 5.15"
	IPAddress     string // peer address from gRPC connection
	RMMAgents     []*pb.RMMAgent
	pendingCmds   []*pb.HealCommand
}

// OSType derives the OS type ("windows", "macos", "linux") from the os_version string.
func (a *AgentState) OSType() string {
	v := strings.ToLower(a.OSVersion)
	switch {
	case strings.Contains(v, "windows"):
		return "windows"
	case strings.Contains(v, "darwin"), strings.Contains(v, "macos"):
		return "macos"
	case strings.Contains(v, "linux"):
		return "linux"
	default:
		return ""
	}
}

// AgentRegistry tracks connected Go agents with command queue support.
// Agent IDs and hostnames are persisted to disk so they survive daemon restarts.
type AgentRegistry struct {
	mu            sync.RWMutex
	agents        map[string]*AgentState    // agent_id -> state
	hostnameIndex map[string]string         // hostname_lower -> agent_id
	configVersion int
	persistPath   string // Path to JSON file for agent ID persistence
}

// persistedAgent is the on-disk representation of a known agent.
type persistedAgent struct {
	AgentID  string `json:"agent_id"`
	Hostname string `json:"hostname"`
}

// NewAgentRegistry creates a new registry. If stateDir is non-empty,
// known agent IDs are loaded from disk and persisted on changes.
func NewAgentRegistry() *AgentRegistry {
	return &AgentRegistry{
		agents:        make(map[string]*AgentState),
		hostnameIndex: make(map[string]string),
		configVersion: 1,
	}
}

// NewAgentRegistryPersistent creates a registry that persists known agents to stateDir.
func NewAgentRegistryPersistent(stateDir string) *AgentRegistry {
	r := &AgentRegistry{
		agents:        make(map[string]*AgentState),
		hostnameIndex: make(map[string]string),
		configVersion: 1,
		persistPath:   filepath.Join(stateDir, "agent_registry.json"),
	}
	r.loadFromDisk()
	return r
}

// loadFromDisk loads known agent IDs from the persist file.
// Agents are marked as disconnected (zero LastHeartbeat) until they reconnect.
func (r *AgentRegistry) loadFromDisk() {
	if r.persistPath == "" {
		return
	}
	data, err := os.ReadFile(r.persistPath)
	if err != nil {
		return // File doesn't exist yet — first boot
	}
	var agents []persistedAgent
	if err := json.Unmarshal(data, &agents); err != nil {
		log.Printf("[registry] Failed to parse %s: %v", r.persistPath, err)
		return
	}
	for _, a := range agents {
		state := &AgentState{
			AgentID:       a.AgentID,
			Hostname:      a.Hostname,
			hostnameLower: toLower(a.Hostname),
		}
		r.agents[a.AgentID] = state
		r.hostnameIndex[state.hostnameLower] = a.AgentID
	}
	log.Printf("[registry] Loaded %d known agents from disk", len(agents))
}

// saveToDisk persists current agent IDs to disk. Called under write lock.
func (r *AgentRegistry) saveToDisk() {
	if r.persistPath == "" {
		return
	}
	agents := make([]persistedAgent, 0, len(r.agents))
	for _, a := range r.agents {
		agents = append(agents, persistedAgent{
			AgentID:  a.AgentID,
			Hostname: a.Hostname,
		})
	}
	data, err := json.MarshalIndent(agents, "", "  ")
	if err != nil {
		log.Printf("[registry] Failed to marshal agents: %v", err)
		return
	}
	if err := os.WriteFile(r.persistPath, data, 0600); err != nil {
		log.Printf("[registry] Failed to write %s: %v", r.persistPath, err)
	}
}

// Register adds or updates an agent in the registry and persists to disk.
func (r *AgentRegistry) Register(state *AgentState) {
	r.mu.Lock()
	defer r.mu.Unlock()

	r.agents[state.AgentID] = state
	r.hostnameIndex[state.hostnameLower] = state.AgentID
	r.saveToDisk()
}

// Unregister removes an agent from the registry and persists to disk.
func (r *AgentRegistry) Unregister(agentID string) {
	r.mu.Lock()
	defer r.mu.Unlock()

	if agent, ok := r.agents[agentID]; ok {
		delete(r.hostnameIndex, agent.hostnameLower)
		delete(r.agents, agentID)
		r.saveToDisk()
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
