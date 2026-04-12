// Tests for the time-travel reconciliation detector (Session 205 Phase 2).
//
// Philosophy: the detector is pure-ish (reads disk, /proc/uptime, wall
// clock). We test via a tmp state dir; we do not mock /proc/uptime
// because getUptimeSeconds is a stdlib read and stubbing it would
// require refactoring for this test only. Instead, we rely on the
// relative comparison (last_reported_uptime vs current) and construct
// state that triggers each signal deterministically.
package daemon

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestNewReconcileDetector_CreatesStateDir(t *testing.T) {
	tmp := t.TempDir()
	sub := filepath.Join(tmp, "missing", "nested")
	d := NewReconcileDetector(sub)
	if d == nil {
		t.Fatal("NewReconcileDetector returned nil")
	}
	if _, err := os.Stat(sub); err != nil {
		t.Fatalf("state dir not created: %v", err)
	}
}

func TestBootCounterIncrementsOnStart(t *testing.T) {
	tmp := t.TempDir()
	d := NewReconcileDetector(tmp)
	first := d.readBootCounter()
	if first != 1 {
		t.Fatalf("expected boot_counter=1 after first init, got %d", first)
	}

	// Second detector on same dir = second "boot" = counter should advance
	d2 := NewReconcileDetector(tmp)
	second := d2.readBootCounter()
	if second != 2 {
		t.Fatalf("expected boot_counter=2 after second init, got %d", second)
	}
}

func TestDetect_NoSignalsOnFreshBoot(t *testing.T) {
	tmp := t.TempDir()
	d := NewReconcileDetector(tmp)
	res := d.Detect()
	if res.ReconcileNeeded {
		t.Fatalf("fresh boot must not trigger reconcile, got signals=%v", res.Signals)
	}
	if res.BootCounter != 1 {
		t.Fatalf("expected BootCounter=1, got %d", res.BootCounter)
	}
	if res.GenerationUUID != "" {
		t.Fatalf("expected empty GenerationUUID on fresh boot, got %q", res.GenerationUUID)
	}
}

func TestDetect_UptimeCliffSignal(t *testing.T) {
	tmp := t.TempDir()
	d := NewReconcileDetector(tmp)

	// Seed last_reported_uptime to a value 10 hours in the future vs
	// current /proc/uptime. This triggers uptime_cliff (current+60 < last).
	current := getUptimeSeconds()
	fake := current + 36000 // 10 hours higher than reality
	if err := d.WriteLastReportedUptime(fake); err != nil {
		t.Fatalf("seed failed: %v", err)
	}

	res := d.Detect()
	found := false
	for _, s := range res.Signals {
		if s == SignalUptimeCliff {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected SignalUptimeCliff in signals, got %v", res.Signals)
	}
}

func TestDetect_LKGFutureMtimeSignal(t *testing.T) {
	tmp := t.TempDir()
	d := NewReconcileDetector(tmp)

	// Create LKG marker with mtime 1 hour in the future
	path := filepath.Join(tmp, "last_known_good.mtime")
	if err := os.WriteFile(path, []byte{}, 0644); err != nil {
		t.Fatalf("create lkg: %v", err)
	}
	future := time.Now().Add(1 * time.Hour)
	if err := os.Chtimes(path, future, future); err != nil {
		t.Fatalf("chtimes: %v", err)
	}

	res := d.Detect()
	found := false
	for _, s := range res.Signals {
		if s == SignalLKGFutureMtime {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected SignalLKGFutureMtime, got %v", res.Signals)
	}
}

func TestDetect_MinTwoSignalsRequiredForReconcile(t *testing.T) {
	// One signal alone must NOT flip ReconcileNeeded. Otherwise a
	// transient NTP hiccup would trigger a full reconcile (the exact
	// false-positive the backend's MIN_SIGNALS_REQUIRED=2 guards against).
	tmp := t.TempDir()
	d := NewReconcileDetector(tmp)

	// Only seed uptime_cliff
	current := getUptimeSeconds()
	if err := d.WriteLastReportedUptime(current + 36000); err != nil {
		t.Fatalf("seed failed: %v", err)
	}

	res := d.Detect()
	if len(res.Signals) != 1 {
		t.Fatalf("expected exactly 1 signal, got %d: %v", len(res.Signals), res.Signals)
	}
	if res.ReconcileNeeded {
		t.Fatal("ReconcileNeeded must be false with only 1 signal (backend requires ≥2)")
	}
}

func TestDetect_TwoSignalsFlipsReconcileNeeded(t *testing.T) {
	tmp := t.TempDir()
	d := NewReconcileDetector(tmp)

	// Seed both uptime_cliff + lkg_future_mtime
	current := getUptimeSeconds()
	if err := d.WriteLastReportedUptime(current + 36000); err != nil {
		t.Fatalf("seed uptime: %v", err)
	}
	lkgPath := filepath.Join(tmp, "last_known_good.mtime")
	if err := os.WriteFile(lkgPath, []byte{}, 0644); err != nil {
		t.Fatalf("seed lkg: %v", err)
	}
	future := time.Now().Add(1 * time.Hour)
	if err := os.Chtimes(lkgPath, future, future); err != nil {
		t.Fatalf("chtimes: %v", err)
	}

	res := d.Detect()
	if len(res.Signals) < 2 {
		t.Fatalf("expected ≥2 signals, got %d: %v", len(res.Signals), res.Signals)
	}
	if !res.ReconcileNeeded {
		t.Fatal("ReconcileNeeded must be true with ≥2 signals")
	}
}

func TestWriteGenerationUUID_RoundTrips(t *testing.T) {
	tmp := t.TempDir()
	d := NewReconcileDetector(tmp)

	uuid := "12345678-1234-4321-8abc-123456789abc"
	if err := d.WriteGenerationUUID(uuid); err != nil {
		t.Fatalf("write failed: %v", err)
	}
	if got := d.readGenerationUUID(); got != uuid {
		t.Fatalf("read returned %q, want %q", got, uuid)
	}
}

func TestTouchLKG_CreatesFileAndUpdatesMtime(t *testing.T) {
	tmp := t.TempDir()
	d := NewReconcileDetector(tmp)

	if err := d.TouchLKG(); err != nil {
		t.Fatalf("touch failed: %v", err)
	}
	path := filepath.Join(tmp, "last_known_good.mtime")
	info1, err := os.Stat(path)
	if err != nil {
		t.Fatalf("lkg not created: %v", err)
	}

	// Rewind mtime to past, touch again, verify it advanced
	past := time.Now().Add(-24 * time.Hour)
	if err := os.Chtimes(path, past, past); err != nil {
		t.Fatalf("chtimes: %v", err)
	}
	if err := d.TouchLKG(); err != nil {
		t.Fatalf("re-touch failed: %v", err)
	}
	info2, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat after re-touch: %v", err)
	}
	if !info2.ModTime().After(info1.ModTime().Add(-24*time.Hour).Add(1*time.Second)) {
		t.Fatalf("mtime didn't advance: before=%v after=%v", info1.ModTime(), info2.ModTime())
	}
}

func TestNewGenerationUUID_ValidFormat(t *testing.T) {
	// RFC 4122 v4: 8-4-4-4-12 hex digits, version nibble=4, variant nibble=8..b
	for i := 0; i < 50; i++ {
		uuid := NewGenerationUUID()
		if len(uuid) != 36 {
			t.Fatalf("length=%d for uuid=%q", len(uuid), uuid)
		}
		if uuid[8] != '-' || uuid[13] != '-' || uuid[18] != '-' || uuid[23] != '-' {
			t.Fatalf("wrong separator layout: %q", uuid)
		}
		// version nibble at position 14
		if uuid[14] != '4' {
			t.Fatalf("version nibble != 4: %q", uuid)
		}
		// variant nibble at position 19 (one of 8, 9, a, b)
		v := uuid[19]
		if v != '8' && v != '9' && v != 'a' && v != 'b' {
			t.Fatalf("variant nibble out of range (8..b): %q", uuid)
		}
	}
}

func TestWriteLastReportedUptime_RoundTrips(t *testing.T) {
	tmp := t.TempDir()
	d := NewReconcileDetector(tmp)
	if err := d.WriteLastReportedUptime(12345); err != nil {
		t.Fatalf("write: %v", err)
	}
	if got := d.readLastReportedUptime(); got != 12345 {
		t.Fatalf("read=%d want=12345", got)
	}
}

// Ensures the signal string constants used by detector match the ones
// reconcile.py expects — these are the wire protocol between agent + CC.
// If either side drifts, backend rejects the reconcile request.
func TestSignalConstants_MatchBackendWireProtocol(t *testing.T) {
	// See mcp-server/central-command/backend/reconcile.py:
	//   SIGNAL_BOOT_COUNTER_REGRESSION = "boot_counter_regression"
	//   SIGNAL_UPTIME_CLIFF = "uptime_cliff"
	//   SIGNAL_GENERATION_MISMATCH = "generation_mismatch"
	//   SIGNAL_LKG_FUTURE_MTIME = "lkg_future_mtime"
	expected := map[string]string{
		"boot_counter_regression": SignalBootCounterRegression,
		"uptime_cliff":            SignalUptimeCliff,
		"generation_mismatch":     SignalGenerationMismatch,
		"lkg_future_mtime":        SignalLKGFutureMtime,
	}
	for wire, got := range expected {
		if got != wire {
			t.Fatalf("signal %q != wire string %q — agent/backend drift!",
				got, wire)
		}
	}
}

// Regression: boot counter must never go BACKWARDS across process
// starts on the same state dir. Even if the counter file is deleted
// between starts, the new detector should start at 1 not negative.
func TestBootCounter_NeverNegative(t *testing.T) {
	tmp := t.TempDir()
	_ = NewReconcileDetector(tmp) // counter=1
	_ = NewReconcileDetector(tmp) // counter=2

	// Corrupt the file
	path := filepath.Join(tmp, "boot_counter")
	if err := os.WriteFile(path, []byte("garbage-not-a-number"), 0644); err != nil {
		t.Fatalf("corrupt: %v", err)
	}

	d := NewReconcileDetector(tmp)
	got := d.readBootCounter()
	if got < 1 {
		t.Fatalf("counter %d went negative after corruption", got)
	}
}

// Regression guard: the JSON keys shipped by sites.py inline
// reconcile_plan_payload MUST deserialize cleanly into the Go
// ReconcilePlan struct in phonehome.go. A missing key (like
// runbook_ids becoming plan_runbook_ids) silently drops data
// because all struct fields use `omitempty`. This test pins the
// wire contract.
//
// If this test breaks, check sites.py:~4270 payload keys AND
// phonehome.go ReconcilePlan tags — both must match.
func TestReconcilePlanJSON_WireParity(t *testing.T) {
	// Canonical CC response for a checkin that triggered reconcile.
	// Keys here mirror sites.py reconcile_plan_payload (Session 205
	// Phase 2). Any divergence between backend keys and Go struct tags
	// is the bug this test catches.
	sig := make([]byte, 128)
	for i := range sig {
		sig[i] = 'a'
	}
	canonical := `{"reconcile_plan":{` +
		`"plan_id":"12345678-1234-4321-8abc-123456789abc",` +
		`"new_generation_uuid":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",` +
		`"nonce_epoch_hex":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",` +
		`"runbook_ids":["RB-WIN-001","RB-LIN-002","RB-MAC-003"],` +
		`"issued_at":"2026-04-12T00:00:00+00:00",` +
		`"appliance_id":"test-site-AA:BB:CC:DD:EE:FF",` +
		`"signature_hex":"` + string(sig) + `"` +
		`}}`

	var resp struct {
		ReconcilePlan *ReconcilePlan `json:"reconcile_plan"`
	}
	if err := json.Unmarshal([]byte(canonical), &resp); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}
	if resp.ReconcilePlan == nil {
		t.Fatal("ReconcilePlan nil after unmarshal — JSON key mismatch")
	}
	p := resp.ReconcilePlan

	// Every field must populate — empty means a key mismatch between
	// backend payload shape and Go struct tag.
	checks := []struct {
		name, got string
	}{
		{"PlanID", p.PlanID},
		{"NewGenerationUUID", p.NewGenerationUUID},
		{"NonceEpochHex", p.NonceEpochHex},
		{"IssuedAt", p.IssuedAt},
		{"ApplianceID", p.ApplianceID},
		{"SignatureHex", p.SignatureHex},
	}
	for _, c := range checks {
		if c.got == "" {
			t.Errorf("field %s unmarshaled empty — JSON key mismatch between "+
				"sites.py payload and phonehome.go ReconcilePlan struct tag",
				c.name)
		}
	}
	if len(p.RunbookIDs) != 3 {
		t.Errorf("RunbookIDs unmarshaled %d entries, want 3: %v",
			len(p.RunbookIDs), p.RunbookIDs)
	}
	if len(p.SignatureHex) != 128 {
		t.Errorf("SignatureHex length=%d, want 128 (Ed25519 hex)", len(p.SignatureHex))
	}
}

// Regression guard: absent reconcile_plan MUST unmarshal to nil (the
// common case — 99%+ of checkins don't reconcile). If this breaks,
// existing daemons will crash on every checkin.
func TestReconcilePlanJSON_AbsentIsNil(t *testing.T) {
	rawJSON := `{"status":"ok","appliance_id":"x"}`
	var resp struct {
		Status        string         `json:"status"`
		ReconcilePlan *ReconcilePlan `json:"reconcile_plan,omitempty"`
	}
	if err := json.Unmarshal([]byte(rawJSON), &resp); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}
	if resp.ReconcilePlan != nil {
		t.Fatalf("ReconcilePlan should be nil when absent, got %+v",
			resp.ReconcilePlan)
	}
}

// Ensure rapid re-Detect() is stateless: calling Detect() many times
// without any checkin in between should produce the same signals
// (pure read-only function).
func TestDetect_IdempotentReads(t *testing.T) {
	tmp := t.TempDir()
	d := NewReconcileDetector(tmp)
	if err := d.WriteGenerationUUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"); err != nil {
		t.Fatalf("seed: %v", err)
	}

	first := d.Detect()
	for i := 0; i < 5; i++ {
		got := d.Detect()
		if got.BootCounter != first.BootCounter {
			t.Fatalf("boot_counter drift on read %d: %d vs %d",
				i, got.BootCounter, first.BootCounter)
		}
		if got.GenerationUUID != first.GenerationUUID {
			t.Fatalf("generation drift on read %d", i)
		}
		if fmt.Sprintf("%v", got.Signals) != fmt.Sprintf("%v", first.Signals) {
			t.Fatalf("signal drift on read %d: %v vs %v",
				i, got.Signals, first.Signals)
		}
	}
}
