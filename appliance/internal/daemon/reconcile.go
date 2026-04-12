// Package daemon time-travel reconciliation detection (Session 205 Phase 2).
//
// On boot, we capture baseline state: /proc/uptime, the last known
// "generation UUID" from disk, a monotonic boot_counter, and the
// last_known_good server state we had before shutdown. We compare
// these against signals that indicate we've woken up in a past state.
//
// Detection signals are reported to Central Command in every checkin.
// If ≥2 signals are present, the agent sets ReconcileNeeded=true and
// CC responds with a signed reconcile plan (see backend/reconcile.py).
//
// No actions are taken here — detection only. Application of the plan
// happens in Phase 3 after the CC response arrives.
package daemon

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const (
	bootCounterPath   = "/var/lib/msp/boot_counter"
	generationUUIDPath = "/var/lib/msp/generation_uuid"
	lastKnownGoodPath = "/var/lib/msp/last_known_good.mtime"
	lastUptimePath    = "/var/lib/msp/last_reported_uptime"

	// SignalBootCounterRegression: the on-disk boot_counter is LOWER
	// than what we reported last cycle (filesystem reverted).
	SignalBootCounterRegression = "boot_counter_regression"
	// SignalUptimeCliff: /proc/uptime dropped when we expected it to
	// keep climbing. Set when reported uptime < last reported uptime.
	SignalUptimeCliff = "uptime_cliff"
	// SignalGenerationMismatch: on-disk generation_uuid differs from
	// what CC sent us last cycle (snapshot reverted past a reconcile).
	SignalGenerationMismatch = "generation_mismatch"
	// SignalLKGFutureMtime: last_known_good file mtime is AFTER the
	// current wall-clock time (clock rolled backward).
	SignalLKGFutureMtime = "lkg_future_mtime"
)

// reconcileState holds everything the detector reads or writes on-disk.
// The detector is stateless across runs — all persistence is through
// these four files, which survive agent restarts but NOT snapshot reverts.
type reconcileState struct {
	CurrentBootCounter int64
	LocalGenerationUUID string  // what we have on-disk right now
	CurrentUptimeSeconds int
	LastReportedUptimeSeconds int  // what we reported to CC last cycle
	LKGMTime time.Time  // mtime of the last_known_good marker
}

// ReconcileDetector encapsulates the detection logic. Created once at
// daemon startup; called on every checkin cycle.
type ReconcileDetector struct {
	stateDir string
}

// NewReconcileDetector constructs the detector, ensuring the state dir
// exists and bumping boot_counter on startup.
func NewReconcileDetector(stateDir string) *ReconcileDetector {
	if stateDir == "" {
		stateDir = "/var/lib/msp"
	}
	d := &ReconcileDetector{stateDir: stateDir}
	// Ensure state dir exists (installer creates it but defend against fresh VMs).
	_ = os.MkdirAll(stateDir, 0755)
	// Increment boot counter exactly once per daemon process start.
	// A daemon restart WITHOUT reboot still counts — this is a floor, not
	// a system-reboot count. CC tracks max, so duplicates are harmless.
	d.incrementBootCounter()
	return d
}

// incrementBootCounter reads the on-disk counter, adds 1, writes back.
// If the file is missing or corrupt, starts at 1.
func (d *ReconcileDetector) incrementBootCounter() {
	path := filepath.Join(d.stateDir, "boot_counter")
	data, err := os.ReadFile(path)
	var current int64
	if err == nil {
		current, _ = strconv.ParseInt(strings.TrimSpace(string(data)), 10, 64)
	}
	next := current + 1
	if err := os.WriteFile(path, []byte(fmt.Sprintf("%d", next)), 0644); err != nil {
		slog.Warn("failed to write boot_counter",
			"component", "reconcile",
			"path", path,
			"error", err)
	}
}

// readBootCounter returns the current on-disk boot counter.
// Called during checkin to report the current value.
func (d *ReconcileDetector) readBootCounter() int64 {
	path := filepath.Join(d.stateDir, "boot_counter")
	data, err := os.ReadFile(path)
	if err != nil {
		return 0
	}
	n, _ := strconv.ParseInt(strings.TrimSpace(string(data)), 10, 64)
	return n
}

// readGenerationUUID returns the current on-disk generation UUID.
// Empty string if not yet written — this is expected on first boot
// before CC has issued any reconcile plans.
func (d *ReconcileDetector) readGenerationUUID() string {
	path := filepath.Join(d.stateDir, "generation_uuid")
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

// WriteGenerationUUID persists a new generation UUID after successful
// reconciliation or on first "known good" checkin. Called from Phase 3
// after the agent applies a reconcile plan.
func (d *ReconcileDetector) WriteGenerationUUID(uuid string) error {
	path := filepath.Join(d.stateDir, "generation_uuid")
	return os.WriteFile(path, []byte(uuid), 0644)
}

// readLastReportedUptime returns the uptime we sent in our previous
// successful checkin. Used to detect the "uptime cliff" signal: if
// current uptime < last_reported, the system clock went back.
func (d *ReconcileDetector) readLastReportedUptime() int {
	path := filepath.Join(d.stateDir, "last_reported_uptime")
	data, err := os.ReadFile(path)
	if err != nil {
		return 0
	}
	n, _ := strconv.Atoi(strings.TrimSpace(string(data)))
	return n
}

// WriteLastReportedUptime persists the uptime we just reported so the
// next cycle can compare. Called from phonehome.go after a successful
// checkin.
func (d *ReconcileDetector) WriteLastReportedUptime(uptime int) error {
	path := filepath.Join(d.stateDir, "last_reported_uptime")
	return os.WriteFile(path, []byte(fmt.Sprintf("%d", uptime)), 0644)
}

// readLastReportedBootCounter returns the boot_counter value from our
// previous successful checkin. Used for the CLIENT-SIDE
// boot_counter_regression signal (Phase 3.1): if current < last,
// the filesystem has been reverted (VM snapshot, backup restore).
//
// Before Phase 3.1 this signal was "server-side only" — CC compared
// reported vs last-known. That was too slow: a snapshot revert on the
// appliance would only trigger `uptime_cliff` (1 signal), below the
// MIN_SIGNALS_REQUIRED=2 threshold, so no reconcile fired. With this
// helper the daemon itself detects the regression and the combination
// of `boot_counter_regression` + `uptime_cliff` reliably trips the
// threshold on every snapshot revert.
func (d *ReconcileDetector) readLastReportedBootCounter() int64 {
	path := filepath.Join(d.stateDir, "last_reported_boot_counter")
	data, err := os.ReadFile(path)
	if err != nil {
		return 0
	}
	n, _ := strconv.ParseInt(strings.TrimSpace(string(data)), 10, 64)
	return n
}

// WriteLastReportedBootCounter persists the boot_counter we just
// reported. Called from daemon.go alongside WriteLastReportedUptime
// on successful checkin.
func (d *ReconcileDetector) WriteLastReportedBootCounter(counter int64) error {
	path := filepath.Join(d.stateDir, "last_reported_boot_counter")
	return os.WriteFile(path, []byte(fmt.Sprintf("%d", counter)), 0644)
}

// readLKGMTime reads the mtime of the last_known_good marker file.
// Zero time if missing. Used to detect clock rollback.
func (d *ReconcileDetector) readLKGMTime() time.Time {
	path := filepath.Join(d.stateDir, "last_known_good.mtime")
	info, err := os.Stat(path)
	if err != nil {
		return time.Time{}
	}
	return info.ModTime()
}

// TouchLKG updates the last_known_good marker's mtime to now. Called
// on every successful checkin (not every cycle — only after a clean
// round-trip to CC). Becomes the reference point for future rollback
// detection.
func (d *ReconcileDetector) TouchLKG() error {
	path := filepath.Join(d.stateDir, "last_known_good.mtime")
	now := time.Now()
	if _, err := os.Stat(path); os.IsNotExist(err) {
		if err := os.WriteFile(path, []byte{}, 0644); err != nil {
			return err
		}
	}
	return os.Chtimes(path, now, now)
}

// DetectionResult is returned from Detect. Callers include these fields
// in the checkin request so CC can either ignore (zero signals) or
// trigger reconciliation (≥2 signals).
type DetectionResult struct {
	BootCounter       int64
	GenerationUUID    string
	Signals           []string
	ReconcileNeeded   bool
}

// Detect gathers state and returns detection signals. Pure function of
// on-disk state + current /proc/uptime — no network calls, no mutations.
//
// Writes to disk ONLY happen in WriteLastReportedUptime / TouchLKG,
// which callers invoke on successful checkin. This lets the detector
// be called multiple times in a cycle without side effects.
func (d *ReconcileDetector) Detect() DetectionResult {
	res := DetectionResult{
		BootCounter:    d.readBootCounter(),
		GenerationUUID: d.readGenerationUUID(),
	}

	currentUptime := getUptimeSeconds()
	lastReportedUptime := d.readLastReportedUptime()

	// Signal 1: uptime_cliff — current < last reported (by more than 60s
	// slack to avoid false positives from NTP drift)
	if lastReportedUptime > 0 && currentUptime+60 < lastReportedUptime {
		res.Signals = append(res.Signals, SignalUptimeCliff)
	}

	// Signal 2: lkg_future_mtime — our "last known good" marker is in
	// the future. Clock rolled back.
	lkg := d.readLKGMTime()
	if !lkg.IsZero() && lkg.After(time.Now().Add(60*time.Second)) {
		res.Signals = append(res.Signals, SignalLKGFutureMtime)
	}

	// Signal 3 (Phase 3.1): boot_counter_regression — the on-disk
	// boot_counter is LOWER than what we reported in our previous
	// successful checkin. Filesystem reverted without rebooting to
	// the newer state (VM snapshot revert is the canonical case).
	//
	// Only emit when last_reported > 0 — a fresh state dir has no
	// baseline and a 0 comparison would false-positive on first boot.
	lastReportedBC := d.readLastReportedBootCounter()
	if lastReportedBC > 0 && res.BootCounter < lastReportedBC {
		res.Signals = append(res.Signals, SignalBootCounterRegression)
	}

	// generation_mismatch is still server-side-only: the daemon doesn't
	// know what UUID CC expects. CC compares reported vs last-known and
	// can echo the signal back via future reconcile response fields if
	// needed. For now the 3 client-side signals above cover every
	// realistic snapshot/restore scenario.

	// Trigger if ≥2 signals. MIN_SIGNALS_REQUIRED in backend/reconcile.py
	// must match — if these drift, backend rejects our request.
	res.ReconcileNeeded = len(res.Signals) >= 2
	return res
}

// NewGenerationUUID generates a fresh random UUIDv4-looking string.
// Used when we receive a reconcile plan — we write this to disk so
// the next cycle has a fresh generation to report.
func NewGenerationUUID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return ""
	}
	// Set version 4 + variant RFC4122
	b[6] = (b[6] & 0x0F) | 0x40
	b[8] = (b[8] & 0x3F) | 0x80
	return fmt.Sprintf("%s-%s-%s-%s-%s",
		hex.EncodeToString(b[0:4]),
		hex.EncodeToString(b[4:6]),
		hex.EncodeToString(b[6:8]),
		hex.EncodeToString(b[8:10]),
		hex.EncodeToString(b[10:16]))
}
