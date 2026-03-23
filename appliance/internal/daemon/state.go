package daemon

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

const stateFileName = "daemon_state.json"

// persistedCooldown is the on-disk representation of a driftCooldown entry.
type persistedCooldown struct {
	LastSeen    time.Time     `json:"last_seen"`
	Count       int           `json:"count"`
	CooldownDur time.Duration `json:"cooldown_dur"` // nanoseconds
}

// PersistedState holds daemon state that survives restarts.
type PersistedState struct {
	LinuxTargets       []linuxTarget                `json:"linux_targets,omitempty"`
	L2Mode             string                       `json:"l2_mode,omitempty"`
	SubscriptionStatus string                       `json:"subscription_status,omitempty"`
	Cooldowns          map[string]persistedCooldown `json:"cooldowns,omitempty"`
	SavedAt            time.Time                    `json:"saved_at"`
}

// saveState delegates to StateManager.SaveToDisk.
func (d *Daemon) saveState() {
	d.state.SaveToDisk(d.config.StateDir)
}

// loadState restores persisted state from disk.
// Returns nil if no state file exists (first boot).
func loadState(stateDir string) (*PersistedState, error) {
	path := filepath.Join(stateDir, stateFileName)

	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil // First boot — no state to restore
		}
		return nil, fmt.Errorf("read state file: %w", err)
	}

	var state PersistedState
	if err := json.Unmarshal(data, &state); err != nil {
		return nil, fmt.Errorf("parse state file: %w", err)
	}

	return &state, nil
}
