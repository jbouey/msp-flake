package crypto

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"testing"

	"golang.org/x/crypto/nacl/box"
)

func TestLoadOrCreateKeypair(t *testing.T) {
	dir := t.TempDir()
	kp1, err := LoadOrCreateKeypair(dir)
	if err != nil {
		t.Fatalf("LoadOrCreateKeypair: %v", err)
	}

	// Should return same keypair on second load
	kp2, err := LoadOrCreateKeypair(dir)
	if err != nil {
		t.Fatalf("LoadOrCreateKeypair (reload): %v", err)
	}
	if kp1.PublicKeyHex() != kp2.PublicKeyHex() {
		t.Errorf("Public keys differ after reload: %s vs %s", kp1.PublicKeyHex(), kp2.PublicKeyHex())
	}
}

func TestDecryptCredentials_RoundTrip(t *testing.T) {
	dir := t.TempDir()
	kp, err := LoadOrCreateKeypair(dir)
	if err != nil {
		t.Fatalf("LoadOrCreateKeypair: %v", err)
	}

	// Simulate server-side: encrypt with ephemeral key for kp.PublicKey
	ephPub, ephPriv, err := box.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatalf("GenerateKey: %v", err)
	}

	payload := map[string]interface{}{
		"windows_targets": []map[string]string{{"hostname": "dc01", "username": "admin", "password": "s3cret"}},
		"linux_targets":   []interface{}{},
	}
	plaintext, _ := json.Marshal(payload)

	var nonce [24]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		t.Fatalf("rand nonce: %v", err)
	}

	ciphertext := box.Seal(nil, plaintext, &nonce, &kp.PublicKey, ephPriv)

	enc := &EncryptedCredentials{
		EphemeralPublicKey: hex.EncodeToString(ephPub[:]),
		Nonce:              hex.EncodeToString(nonce[:]),
		Ciphertext:         hex.EncodeToString(ciphertext),
	}

	decrypted, err := kp.DecryptCredentials(enc)
	if err != nil {
		t.Fatalf("DecryptCredentials: %v", err)
	}

	var result map[string]interface{}
	if err := json.Unmarshal(decrypted, &result); err != nil {
		t.Fatalf("Unmarshal decrypted: %v", err)
	}

	winTargets, ok := result["windows_targets"].([]interface{})
	if !ok || len(winTargets) != 1 {
		t.Fatalf("Expected 1 windows target, got: %v", result["windows_targets"])
	}
}

func TestDecryptCredentials_WrongKey(t *testing.T) {
	dir := t.TempDir()
	kp, _ := LoadOrCreateKeypair(dir)

	// Encrypt with a different recipient key
	wrongPub, wrongPriv, _ := box.GenerateKey(rand.Reader)
	ephPub, _, _ := box.GenerateKey(rand.Reader)

	var nonce [24]byte
	rand.Read(nonce[:])

	// Encrypt for wrongPub, not kp.PublicKey
	ciphertext := box.Seal(nil, []byte(`{"test": true}`), &nonce, wrongPub, wrongPriv)

	enc := &EncryptedCredentials{
		EphemeralPublicKey: hex.EncodeToString(ephPub[:]),
		Nonce:              hex.EncodeToString(nonce[:]),
		Ciphertext:         hex.EncodeToString(ciphertext),
	}

	_, err := kp.DecryptCredentials(enc)
	if err == nil {
		t.Fatal("Expected decryption to fail with wrong key")
	}
}
