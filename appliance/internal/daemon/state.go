package daemon

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"time"
)

const stateFileName = "daemon_state.json"

// PersistedState holds daemon state that survives restarts.
type PersistedState struct {
	LinuxTargets       []linuxTarget `json:"linux_targets,omitempty"`
	L2Mode             string        `json:"l2_mode,omitempty"`
	SubscriptionStatus string        `json:"subscription_status,omitempty"`
	SavedAt            time.Time     `json:"saved_at"`
}

// statePath returns the full path to the state file.
func (d *Daemon) statePath() string {
	return filepath.Join(d.config.StateDir, stateFileName)
}

// saveState persists critical in-memory state to disk.
// Uses atomic write (tmp + rename) for crash safety.
func (d *Daemon) saveState() {
	d.linuxTargetsMu.RLock()
	targets := make([]linuxTarget, len(d.linuxTargets))
	copy(targets, d.linuxTargets)
	d.linuxTargetsMu.RUnlock()

	d.l2ModeMu.RLock()
	l2 := d.l2Mode
	d.l2ModeMu.RUnlock()

	d.subscriptionMu.RLock()
	sub := d.subscriptionStatus
	d.subscriptionMu.RUnlock()

	state := PersistedState{
		LinuxTargets:       targets,
		L2Mode:             l2,
		SubscriptionStatus: sub,
		SavedAt:            time.Now(),
	}

	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		log.Printf("[daemon] Failed to marshal state: %v", err)
		return
	}

	path := d.statePath()
	tmpPath := path + ".tmp"
	if err := os.WriteFile(tmpPath, data, 0600); err != nil {
		log.Printf("[daemon] Failed to write state file: %v", err)
		return
	}
	if err := os.Rename(tmpPath, path); err != nil {
		log.Printf("[daemon] Failed to rename state file: %v", err)
	}
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
