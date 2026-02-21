package evidence

import (
	"crypto/ed25519"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"
)

func TestLoadOrCreateSigningKey_New(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "keys", "signing.key")

	priv, pubHex, err := LoadOrCreateSigningKey(path)
	if err != nil {
		t.Fatalf("LoadOrCreateSigningKey: %v", err)
	}
	if priv == nil {
		t.Fatal("private key is nil")
	}
	if len(pubHex) != 64 {
		t.Fatalf("expected 64 hex chars for public key, got %d", len(pubHex))
	}

	// Verify file was created
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("key file not created: %v", err)
	}
	if len(data) != ed25519.SeedSize {
		t.Fatalf("key file should be %d bytes (seed), got %d", ed25519.SeedSize, len(data))
	}
}

func TestLoadOrCreateSigningKey_Reload(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "keys", "signing.key")

	// Create
	_, pub1, err := LoadOrCreateSigningKey(path)
	if err != nil {
		t.Fatalf("first call: %v", err)
	}

	// Reload
	_, pub2, err := LoadOrCreateSigningKey(path)
	if err != nil {
		t.Fatalf("second call: %v", err)
	}

	if pub1 != pub2 {
		t.Fatalf("reloaded key has different public key: %s vs %s", pub1, pub2)
	}
}

func TestSign_Verify(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "signing.key")

	priv, pubHex, err := LoadOrCreateSigningKey(path)
	if err != nil {
		t.Fatalf("LoadOrCreateSigningKey: %v", err)
	}

	data := []byte(`{"site_id":"test","checks":[]}`)
	sigHex := Sign(priv, data)

	// Verify with stdlib
	pubBytes, err := hex.DecodeString(pubHex)
	if err != nil {
		t.Fatalf("decode public key: %v", err)
	}
	sigBytes, err := hex.DecodeString(sigHex)
	if err != nil {
		t.Fatalf("decode signature: %v", err)
	}

	if !ed25519.Verify(ed25519.PublicKey(pubBytes), data, sigBytes) {
		t.Fatal("signature verification failed")
	}
}
