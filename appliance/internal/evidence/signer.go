// Package evidence implements evidence bundle signing and submission
// for the compliance pipeline.
package evidence

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
)

// LoadOrCreateSigningKey loads an Ed25519 private key from path,
// or generates a new one if the file doesn't exist.
// Returns the private key and the hex-encoded public key.
func LoadOrCreateSigningKey(path string) (ed25519.PrivateKey, string, error) {
	data, err := os.ReadFile(path)
	if err == nil && len(data) == ed25519.SeedSize {
		// Reconstruct from seed (32 bytes)
		priv := ed25519.NewKeyFromSeed(data)
		pub := hex.EncodeToString(priv.Public().(ed25519.PublicKey))
		return priv, pub, nil
	}

	// Generate new keypair
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return nil, "", fmt.Errorf("generate key: %w", err)
	}

	// Persist the seed (first 32 bytes of the 64-byte private key)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return nil, "", fmt.Errorf("create key dir: %w", err)
	}
	if err := os.WriteFile(path, priv.Seed(), 0600); err != nil {
		return nil, "", fmt.Errorf("write key: %w", err)
	}

	return priv, hex.EncodeToString(pub), nil
}

// Sign returns the hex-encoded Ed25519 signature of data.
func Sign(key ed25519.PrivateKey, data []byte) string {
	sig := ed25519.Sign(key, data)
	return hex.EncodeToString(sig)
}
