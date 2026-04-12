// Tests for Session 205 Phase 3 reconcile plan application.
//
// The daemon verifies signature against the server-provided
// ReconcilePlan.SignedPayload string (byte-exact), not a reconstruction.
// Tests use pythonCompatCanonical to mimic the Python side's
// json.dumps(..., sort_keys=True) format — including ", " separators
// inside arrays, which Go's json.Marshal does not emit.
package daemon

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/osiriscare/appliance/internal/crypto"
	"github.com/osiriscare/appliance/internal/orders"
)

// pythonCompatCanonical produces the canonical JSON string that Python's
// json.dumps(sort_keys=True) emits for a reconcile plan. We implement
// this in the test so we can sign payloads that the daemon will then
// verify — this is the exact string the backend sends in SignedPayload.
//
// Python emits a single space after colons AND after commas (both inside
// objects and inside arrays), whereas Go's encoding/json uses no
// separators. We build the string literally to match.
func pythonCompatCanonical(plan *ReconcilePlan) string {
	// Keys are sorted alphabetically.
	rbs := plan.RunbookIDs
	if rbs == nil {
		rbs = []string{}
	}
	rbJSON := "["
	for i, rb := range rbs {
		if i > 0 {
			rbJSON += ", "
		}
		rbJSON += `"` + rb + `"`
	}
	rbJSON += "]"
	return `{"appliance_id": "` + plan.ApplianceID +
		`", "event_id": "` + plan.PlanID +
		`", "generation_uuid": "` + plan.NewGenerationUUID +
		`", "issued_at": "` + plan.IssuedAt +
		`", "nonce_epoch_hex": "` + plan.NonceEpochHex +
		`", "runbook_ids": ` + rbJSON + `}`
}

// Sign a plan: compute canonical, ed25519.Sign, populate SignedPayload + SignatureHex.
func signReconcilePlan(t *testing.T, plan *ReconcilePlan, priv ed25519.PrivateKey) {
	t.Helper()
	canonical := pythonCompatCanonical(plan)
	sig := ed25519.Sign(priv, []byte(canonical))
	plan.SignedPayload = canonical
	plan.SignatureHex = hex.EncodeToString(sig)
}

func setupDaemonWithKey(t *testing.T) (*Daemon, ed25519.PrivateKey, string) {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatalf("keygen: %v", err)
	}
	pubHex := hex.EncodeToString(pub)

	stateDir := t.TempDir()
	d := &Daemon{
		reconcileDetector: NewReconcileDetector(stateDir),
		orderProc:         orders.NewProcessor(stateDir, nil),
	}
	if err := d.orderProc.SetServerPublicKey(pubHex); err != nil {
		t.Fatalf("SetServerPublicKey: %v", err)
	}
	d.orderProc.SetApplianceID("test-appliance-001")
	d.phoneCli = newTestPhoneCli(t)
	return d, priv, stateDir
}

func newValidPlan(applianceID string) *ReconcilePlan {
	return &ReconcilePlan{
		PlanID:            "test-plan-001",
		NewGenerationUUID: "11111111-2222-4333-8444-555555555555",
		NonceEpochHex:     strings.Repeat("a", 64),
		RunbookIDs:        []string{"RB-WIN-001"},
		IssuedAt:          time.Now().UTC().Format(time.RFC3339),
		ApplianceID:       applianceID,
	}
}

// No panic, no state change — silent rejection.
func TestApplyReconcilePlan_BadSignatureDropped(t *testing.T) {
	d, priv, _ := setupDaemonWithKey(t)
	plan := newValidPlan("test-appliance-001")
	signReconcilePlan(t, plan, priv)
	plan.SignatureHex = strings.Repeat("b", 128) // swap in garbage sig

	original := "99999999-8888-4777-9666-555555555555"
	if err := d.reconcileDetector.WriteGenerationUUID(original); err != nil {
		t.Fatalf("seed: %v", err)
	}

	d.applyReconcilePlan(nil, plan)

	got := d.reconcileDetector.readGenerationUUID()
	if got != original {
		t.Fatalf("bad-sig plan mutated generation_uuid: got %q, want %q", got, original)
	}
}

func TestApplyReconcilePlan_WrongApplianceIDDropped(t *testing.T) {
	d, priv, _ := setupDaemonWithKey(t)
	plan := newValidPlan("some-OTHER-appliance")
	signReconcilePlan(t, plan, priv) // valid sig, wrong target

	original := "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
	if err := d.reconcileDetector.WriteGenerationUUID(original); err != nil {
		t.Fatalf("seed: %v", err)
	}

	d.applyReconcilePlan(nil, plan)

	got := d.reconcileDetector.readGenerationUUID()
	if got != original {
		t.Fatalf("wrong-appliance plan mutated generation_uuid: got %q, want %q", got, original)
	}
}

func TestApplyReconcilePlan_StalePlanDropped(t *testing.T) {
	d, priv, _ := setupDaemonWithKey(t)
	plan := newValidPlan("test-appliance-001")
	plan.IssuedAt = time.Now().UTC().Add(-30 * time.Minute).Format(time.RFC3339)
	signReconcilePlan(t, plan, priv)

	original := "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
	_ = d.reconcileDetector.WriteGenerationUUID(original)

	d.applyReconcilePlan(nil, plan)

	got := d.reconcileDetector.readGenerationUUID()
	if got != original {
		t.Fatalf("stale plan mutated generation_uuid: got %q", got)
	}
}

func TestApplyReconcilePlan_FuturePlanDropped(t *testing.T) {
	d, priv, _ := setupDaemonWithKey(t)
	plan := newValidPlan("test-appliance-001")
	plan.IssuedAt = time.Now().UTC().Add(30 * time.Minute).Format(time.RFC3339)
	signReconcilePlan(t, plan, priv)

	original := "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
	_ = d.reconcileDetector.WriteGenerationUUID(original)

	d.applyReconcilePlan(nil, plan)

	got := d.reconcileDetector.readGenerationUUID()
	if got != original {
		t.Fatalf("future plan mutated generation_uuid: got %q", got)
	}
}

func TestApplyReconcilePlan_MissingFieldsRejected(t *testing.T) {
	d, priv, _ := setupDaemonWithKey(t)

	cases := []struct {
		name   string
		mutate func(*ReconcilePlan)
	}{
		{"empty_signature", func(p *ReconcilePlan) { p.SignatureHex = "" }},
		{"empty_generation_uuid", func(p *ReconcilePlan) { p.NewGenerationUUID = "" }},
		{"empty_nonce_epoch", func(p *ReconcilePlan) { p.NonceEpochHex = "" }},
		{"empty_appliance_id", func(p *ReconcilePlan) { p.ApplianceID = "" }},
		{"empty_signed_payload", func(p *ReconcilePlan) { p.SignedPayload = "" }},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			plan := newValidPlan("test-appliance-001")
			signReconcilePlan(t, plan, priv)
			tc.mutate(plan)

			original := "before-" + tc.name
			_ = d.reconcileDetector.WriteGenerationUUID(original)

			d.applyReconcilePlan(nil, plan)

			got := d.reconcileDetector.readGenerationUUID()
			if got != original {
				t.Fatalf("malformed plan (%s) mutated generation_uuid: got %q want %q",
					tc.name, got, original)
			}
		})
	}
}

// Critical: the signed_payload must contain the appliance_id. A server
// that ships signature matching a *different* appliance's payload but
// claims a target appliance_id in the envelope MUST be rejected.
func TestApplyReconcilePlan_EnvelopeMismatchRejected(t *testing.T) {
	d, priv, _ := setupDaemonWithKey(t)

	// Sign a canonical where the appliance_id is "OTHER".
	inner := newValidPlan("OTHER-appliance")
	signReconcilePlan(t, inner, priv)

	// Build an envelope claiming "test-appliance-001" but with the
	// signed_payload (and valid signature) from the OTHER plan. A naive
	// verifier that only checks signature would accept this — we must
	// catch the envelope mismatch.
	evil := &ReconcilePlan{
		PlanID:            inner.PlanID,
		NewGenerationUUID: inner.NewGenerationUUID,
		NonceEpochHex:     inner.NonceEpochHex,
		RunbookIDs:        inner.RunbookIDs,
		IssuedAt:          inner.IssuedAt,
		ApplianceID:       "test-appliance-001", // lies
		SignatureHex:      inner.SignatureHex,
		SignedPayload:     inner.SignedPayload, // references OTHER-appliance
	}

	original := "before-envelope-attack"
	_ = d.reconcileDetector.WriteGenerationUUID(original)

	d.applyReconcilePlan(nil, evil)

	got := d.reconcileDetector.readGenerationUUID()
	if got != original {
		t.Fatalf("envelope-mismatch attack succeeded! generation_uuid now %q (was %q)",
			got, original)
	}
}

// Positive test: a well-formed, freshly-signed, correctly-targeted plan
// MUST mutate state.
func TestApplyReconcilePlan_ValidPlanAppliesState(t *testing.T) {
	d, priv, stateDir := setupDaemonWithKey(t)
	plan := newValidPlan("test-appliance-001")
	signReconcilePlan(t, plan, priv)

	d.applyReconcilePlan(nil, plan)

	got := d.reconcileDetector.readGenerationUUID()
	if got != plan.NewGenerationUUID {
		t.Fatalf("generation_uuid not written: got %q want %q",
			got, plan.NewGenerationUUID)
	}

	lkgPath := filepath.Join(stateDir, "last_known_good.mtime")
	if _, err := os.Stat(lkgPath); err != nil {
		t.Fatalf("LKG not touched: %v", err)
	}
}

// Lock the wire format: a plan signed with pythonCompatCanonical MUST
// verify via crypto.OrderVerifier (the same mechanism the daemon uses).
// If this breaks, the production verifier cannot accept backend plans.
func TestCanonicalPayload_VerifiesWithOrderVerifier(t *testing.T) {
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatalf("keygen: %v", err)
	}

	plan := &ReconcilePlan{
		PlanID:            "round-trip-test",
		NewGenerationUUID: "11111111-2222-4333-8444-555555555555",
		NonceEpochHex:     strings.Repeat("a", 64),
		RunbookIDs:        []string{"RB-A", "RB-B"},
		IssuedAt:          time.Now().UTC().Format(time.RFC3339),
		ApplianceID:       "test",
	}

	canonical := pythonCompatCanonical(plan)
	sig := ed25519.Sign(priv, []byte(canonical))

	v := crypto.NewOrderVerifier("")
	if err := v.SetPublicKey(hex.EncodeToString(pub)); err != nil {
		t.Fatalf("set key: %v", err)
	}
	if err := v.VerifyOrder(canonical, hex.EncodeToString(sig)); err != nil {
		t.Fatalf("round-trip verify failed: %v", err)
	}
}

// Phase 3 round-table finding I1 — regression guard.
//
// Replay attack: attacker captures a valid plan signed 30 minutes ago,
// mutates ONLY the envelope IssuedAt to "now", and re-delivers. The
// signature remains valid (SignedPayload unchanged) and all structural
// checks pass — but freshness must fail because the daemon extracts
// issued_at from the SIGNED payload, not the envelope.
func TestApplyReconcilePlan_EnvelopeIssuedAtReplayRejected(t *testing.T) {
	d, priv, _ := setupDaemonWithKey(t)
	plan := newValidPlan("test-appliance-001")

	// Freshly sign a plan dated 30 minutes ago (inside the signed
	// payload). This is what the attacker "captures".
	plan.IssuedAt = time.Now().UTC().Add(-30 * time.Minute).Format(time.RFC3339)
	signReconcilePlan(t, plan, priv)
	// Now the attacker rewrites JUST the envelope IssuedAt to lie about
	// freshness. SignedPayload still says 30 minutes ago.
	plan.IssuedAt = time.Now().UTC().Format(time.RFC3339)

	original := "before-replay-attack"
	_ = d.reconcileDetector.WriteGenerationUUID(original)

	d.applyReconcilePlan(nil, plan)

	got := d.reconcileDetector.readGenerationUUID()
	if got != original {
		t.Fatalf("envelope-IssuedAt replay attack succeeded! generation_uuid now %q (was %q)",
			got, original)
	}
}

// Extractor should return empty string on malformed inputs — never panic.
func TestExtractFieldFromSignedPayload_MalformedReturnsEmpty(t *testing.T) {
	cases := []struct {
		name    string
		payload string
	}{
		{"empty", ""},
		{"not_json", "hello"},
		{"missing_field", `{"other": "value"}`},
		{"not_string_value", `{"issued_at": 42}`},
		{"truncated", `{"issued_at": "2026-`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := extractFieldFromSignedPayload(tc.payload, "issued_at")
			if got != "" {
				t.Fatalf("expected empty on malformed %q, got %q", tc.name, got)
			}
		})
	}
}

// Tampering with signed_payload AFTER signing must break verification.
func TestCanonicalPayload_TamperDetectedOnSignedPayload(t *testing.T) {
	d, priv, _ := setupDaemonWithKey(t)
	plan := newValidPlan("test-appliance-001")
	signReconcilePlan(t, plan, priv)

	// Tamper — swap a character
	orig := plan.SignedPayload
	plan.SignedPayload = strings.Replace(orig, "test-appliance-001", "test-appliance-999", 1)

	original := "before-tamper"
	_ = d.reconcileDetector.WriteGenerationUUID(original)

	d.applyReconcilePlan(nil, plan)

	got := d.reconcileDetector.readGenerationUUID()
	if got != original {
		t.Fatalf("tamper went undetected! generation_uuid now %q", got)
	}
}

// newTestPhoneCli returns a PhoneHomeClient that POSTs to a
// non-routable address. ACK calls log an error and return early —
// exactly the behavior applyReconcilePlan is designed to tolerate.
func newTestPhoneCli(t *testing.T) *PhoneHomeClient {
	t.Helper()
	cfg := &Config{APIEndpoint: "http://127.0.0.1:1", APIKey: "test"}
	return NewPhoneHomeClient(cfg)
}

// Sanity: our pythonCompatCanonical output matches the exact format
// Python's json.dumps(..., sort_keys=True) produces.
func TestPythonCompatCanonical_MatchesExpectedFormat(t *testing.T) {
	plan := &ReconcilePlan{
		PlanID:            "e1",
		NewGenerationUUID: "g1",
		NonceEpochHex:     "deadbeef",
		RunbookIDs:        []string{"RB-1", "RB-2"},
		IssuedAt:          "2026-04-12T00:00:00+00:00",
		ApplianceID:       "a1",
	}
	got := pythonCompatCanonical(plan)
	want := `{"appliance_id": "a1", "event_id": "e1", "generation_uuid": "g1", "issued_at": "2026-04-12T00:00:00+00:00", "nonce_epoch_hex": "deadbeef", "runbook_ids": ["RB-1", "RB-2"]}`
	if got != want {
		t.Fatalf("python-compat mismatch\nwant: %s\n got: %s", want, got)
	}
}
