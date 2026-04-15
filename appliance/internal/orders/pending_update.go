// update_completion.go
//
// Deferred completion for update_daemon fleet orders.
//
// Background: the synchronous handler (orders.handleUpdateDaemon) used
// to return success immediately after scheduling the restart + health
// check. The processor would then POST /complete with success=true
// BEFORE the restart had even happened, let alone the 70s health check.
// If the health check later rolled back the binary, the backend still
// believed the upgrade had succeeded. site_appliances.agent_version
// would lag the fleet_order's declared version indefinitely and there
// was no way for the backend to observe the rollback.
//
// This file implements a post-restart confirmation gate:
//
//   1. handleUpdateDaemon writes a PendingUpdate marker to stateDir
//      and returns status="update_pending". The processor's main
//      loop recognizes that sentinel and skips its auto-complete call.
//
//   2. On next daemon startup (after the scheduled restart), LoadPending
//      reads the marker. The daemon then launches a goroutine that
//      polls its own Version string every 10s:
//
//      - Version matches ExpectedVersion → POST /complete success=true,
//        clear the marker. The "truth" is now visible to Central Command.
//      - Version still mismatches 10 minutes after restart → POST
//        /complete success=false with an explanatory error, clear the
//        marker. This handles the case where the restart succeeded but
//        the new binary crash-looped and the health check rolled back
//        (so the *new* daemon that's polling is actually the OLD
//        version).
//      - Daemon crashes mid-poll → marker persists → next startup
//        picks up where we left off. The timeout is absolute
//        (TimeoutAt is a wall-clock time), so long crashes still
//        bound the grace period.
//
// The marker file lives at <stateDir>/pending-update.json. It's
// written atomically (tmp + rename) and contains only non-secret
// fields.

package orders

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// pendingUpdateFile is the on-disk marker relative to stateDir.
const pendingUpdateFile = "pending-update.json"

// PendingUpdate is the on-disk record of a deferred update_daemon
// completion. ExpectedVersion is the version string the new binary
// should report after restart. TimeoutAt bounds how long we wait
// before giving up and reporting a failure.
type PendingUpdate struct {
	OrderID         string    `json:"order_id"`
	ExpectedVersion string    `json:"expected_version"`
	ScheduledAt     time.Time `json:"scheduled_at"`
	TimeoutAt       time.Time `json:"timeout_at"`
}

// WritePendingUpdate atomically writes a PendingUpdate to stateDir.
// Safe to call from the order handler. stateDir must already exist.
func WritePendingUpdate(stateDir string, p PendingUpdate) error {
	if p.OrderID == "" || p.ExpectedVersion == "" {
		return fmt.Errorf("pending update requires OrderID and ExpectedVersion")
	}
	if p.ScheduledAt.IsZero() {
		p.ScheduledAt = time.Now().UTC()
	}
	if p.TimeoutAt.IsZero() {
		p.TimeoutAt = p.ScheduledAt.Add(10 * time.Minute)
	}

	data, err := json.MarshalIndent(p, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal pending update: %w", err)
	}

	dest := filepath.Join(stateDir, pendingUpdateFile)
	tmp := dest + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return fmt.Errorf("write pending tmp: %w", err)
	}
	if err := os.Rename(tmp, dest); err != nil {
		os.Remove(tmp)
		return fmt.Errorf("rename pending: %w", err)
	}
	return nil
}

// LoadPendingUpdate reads the PendingUpdate marker from stateDir, or
// returns nil if no marker exists. A malformed marker is logged and
// treated as absent — better to lose a single completion than crash
// the daemon at startup.
func LoadPendingUpdate(stateDir string) *PendingUpdate {
	data, err := os.ReadFile(filepath.Join(stateDir, pendingUpdateFile))
	if err != nil {
		return nil
	}
	var p PendingUpdate
	if err := json.Unmarshal(data, &p); err != nil {
		return nil
	}
	if p.OrderID == "" || p.ExpectedVersion == "" {
		return nil
	}
	return &p
}

// ClearPendingUpdate removes the marker. No-op if the file is absent.
func ClearPendingUpdate(stateDir string) error {
	path := filepath.Join(stateDir, pendingUpdateFile)
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}
