package crypto

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"testing"
)

func TestOrderVerifier_VerifyOrder(t *testing.T) {
	// Generate test keypair
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	pubHex := hex.EncodeToString(pub)

	// Sign a test payload (matches Python: json.dumps(dict, sort_keys=True))
	payload := `{"expires_at": "2026-01-01T00:00:00", "issued_at": "2025-12-31T00:00:00", "nonce": "abc123", "order_id": "test-001", "parameters": {}, "runbook_id": "RB-001"}`
	sig := ed25519.Sign(priv, []byte(payload))
	sigHex := hex.EncodeToString(sig)

	v := NewOrderVerifier(pubHex)

	// Valid signature
	if err := v.VerifyOrder(payload, sigHex); err != nil {
		t.Errorf("valid signature rejected: %v", err)
	}

	// Tampered payload
	if err := v.VerifyOrder(payload+"x", sigHex); err == nil {
		t.Error("tampered payload accepted")
	}

	// Wrong signature
	if err := v.VerifyOrder(payload, hex.EncodeToString(make([]byte, 64))); err == nil {
		t.Error("wrong signature accepted")
	}
}

func TestOrderVerifier_NoKey(t *testing.T) {
	v := NewOrderVerifier("")
	if v.HasKey() {
		t.Error("empty verifier should not have key")
	}
	if err := v.VerifyOrder("data", "aabb"); err == nil {
		t.Error("verification should fail without key")
	}
}

func TestOrderVerifier_SetPublicKey(t *testing.T) {
	pub, _, _ := ed25519.GenerateKey(rand.Reader)
	pubHex := hex.EncodeToString(pub)

	v := NewOrderVerifier("")
	if err := v.SetPublicKey(pubHex); err != nil {
		t.Errorf("SetPublicKey failed: %v", err)
	}
	if !v.HasKey() {
		t.Error("should have key after SetPublicKey")
	}

	// Invalid key
	if err := v.SetPublicKey("invalid"); err == nil {
		t.Error("should reject invalid hex")
	}
	if err := v.SetPublicKey("aabb"); err == nil {
		t.Error("should reject wrong-size key")
	}
}

func TestBuildSignedPayload(t *testing.T) {
	fields := map[string]interface{}{
		"order_id":   "test-001",
		"runbook_id": "RB-001",
		"parameters": map[string]interface{}{},
		"nonce":      "abc123",
	}

	result, err := BuildSignedPayload(fields)
	if err != nil {
		t.Fatal(err)
	}

	// Verify it's valid JSON
	var parsed map[string]interface{}
	if err := json.Unmarshal([]byte(result), &parsed); err != nil {
		t.Errorf("result is not valid JSON: %v", err)
	}

	// Verify keys are sorted
	if result[1] != '"' || result[2] != 'n' {
		// First key alphabetically should be "nonce"
		t.Errorf("keys not sorted: %s", result)
	}
}

// ═══════════════════════════════════════════════════════════════════
// Phase 13.5 H6 — envelope-key fallback verification
// ═══════════════════════════════════════════════════════════════════

func TestOrderVerifier_EnvelopeKeyH6_FallbackSucceeds(t *testing.T) {
	// Scenario: daemon's cached pubkey is STALE. The order is signed
	// with a different key (the current server key). The envelope
	// advertises the current key, and the daemon separately received
	// the same key via last checkin. H6 should verify.
	stalePub, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	currentPub, currentPriv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	stalePubHex := hex.EncodeToString(stalePub)
	currentPubHex := hex.EncodeToString(currentPub)

	payload := `{"order_id": "h6-test"}`
	sig := ed25519.Sign(currentPriv, []byte(payload))
	sigHex := hex.EncodeToString(sig)

	v := NewOrderVerifier(stalePubHex) // cache stale key
	// Envelope says "signed with currentPubHex", daemon's last-checkin
	// reference is also currentPubHex — bounded trust satisfied.
	if err := v.VerifyOrderWithEnvelopeKey(payload, sigHex, currentPubHex, currentPubHex); err != nil {
		t.Errorf("H6 envelope-key verify should succeed, got: %v", err)
	}
}

func TestOrderVerifier_EnvelopeKeyH6_RejectsArbitraryKey(t *testing.T) {
	// Scenario: attacker sends an order signed with their own key,
	// advertises their own key in the envelope. But the daemon's
	// last-checkin reference is the REAL server key. H6 must reject.
	_, _, _ = ed25519.GenerateKey(rand.Reader) // unused
	attackerPub, attackerPriv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	serverPub, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	serverPubHex := hex.EncodeToString(serverPub)
	attackerPubHex := hex.EncodeToString(attackerPub)

	payload := `{"order_id": "attack"}`
	sig := ed25519.Sign(attackerPriv, []byte(payload))
	sigHex := hex.EncodeToString(sig)

	v := NewOrderVerifier(serverPubHex)
	// Envelope says attackerPubHex but last-checkin reference is serverPubHex
	// — MUST reject (bounded trust violated).
	if err := v.VerifyOrderWithEnvelopeKey(payload, sigHex, attackerPubHex, serverPubHex); err == nil {
		t.Error("H6 must REJECT envelope-key that doesn't match last-delivered")
	}
}

func TestOrderVerifier_EnvelopeKeyH6_NoEnvelopeKey_FallsThrough(t *testing.T) {
	// Scenario: envelope carries no signing_pubkey_hex. Verify should
	// behave exactly like VerifyOrder — cache-only path.
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	pubHex := hex.EncodeToString(pub)

	payload := `{"order_id": "no-envelope"}`
	sig := ed25519.Sign(priv, []byte(payload))
	sigHex := hex.EncodeToString(sig)

	v := NewOrderVerifier(pubHex)
	// Valid sig + cache key matches → success, envelope key irrelevant
	if err := v.VerifyOrderWithEnvelopeKey(payload, sigHex, "", ""); err != nil {
		t.Errorf("cache-path verify should succeed even without envelope key: %v", err)
	}

	// Bad sig + no envelope → failure
	badSigHex := hex.EncodeToString(make([]byte, 64))
	if err := v.VerifyOrderWithEnvelopeKey(payload, badSigHex, "", ""); err == nil {
		t.Error("bad sig + no envelope must fail")
	}
}

func TestOrderVerifier_EnvelopeKeyH6_CachePathStillSucceedsFirst(t *testing.T) {
	// When cache key matches, the envelope path never runs (fast path).
	// Passing a bogus envelope key should NOT cause a failure as long as
	// the cache verify succeeds.
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	pubHex := hex.EncodeToString(pub)

	payload := `{"order_id": "cache-wins"}`
	sig := ed25519.Sign(priv, []byte(payload))
	sigHex := hex.EncodeToString(sig)

	v := NewOrderVerifier(pubHex)
	// Bogus envelope key with bogus trusted ref — BUT cache-path verify
	// succeeds so we short-circuit before hitting H6 logic.
	if err := v.VerifyOrderWithEnvelopeKey(payload, sigHex, "deadbeef", "cafebabe"); err != nil {
		t.Errorf("cache path must win over envelope path: %v", err)
	}
}
