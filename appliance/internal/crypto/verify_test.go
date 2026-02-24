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
