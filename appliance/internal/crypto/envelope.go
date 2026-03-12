package crypto

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"

	"golang.org/x/crypto/nacl/box"
)

// EnvelopeKeypair holds a persistent X25519 keypair for credential encryption.
type EnvelopeKeypair struct {
	PublicKey  [32]byte
	PrivateKey [32]byte
}

// persistedKeypair is the on-disk format.
type persistedKeypair struct {
	PublicKey  string `json:"public_key"`  // hex
	PrivateKey string `json:"private_key"` // hex
}

// LoadOrCreateKeypair loads from disk or generates a new X25519 keypair.
func LoadOrCreateKeypair(stateDir string) (*EnvelopeKeypair, error) {
	path := filepath.Join(stateDir, "encryption_keypair.json")

	data, err := os.ReadFile(path)
	if err == nil {
		var p persistedKeypair
		if err := json.Unmarshal(data, &p); err != nil {
			return nil, fmt.Errorf("parse keypair: %w", err)
		}
		kp := &EnvelopeKeypair{}
		pubBytes, _ := hex.DecodeString(p.PublicKey)
		privBytes, _ := hex.DecodeString(p.PrivateKey)
		if len(pubBytes) == 32 && len(privBytes) == 32 {
			copy(kp.PublicKey[:], pubBytes)
			copy(kp.PrivateKey[:], privBytes)
			return kp, nil
		}
	}

	// Generate new keypair
	pub, priv, err := box.GenerateKey(rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("generate X25519 keypair: %w", err)
	}

	kp := &EnvelopeKeypair{PublicKey: *pub, PrivateKey: *priv}

	// Persist to disk
	p := persistedKeypair{
		PublicKey:  hex.EncodeToString(pub[:]),
		PrivateKey: hex.EncodeToString(priv[:]),
	}
	out, _ := json.MarshalIndent(p, "", "  ")
	if err := os.WriteFile(path, out, 0600); err != nil {
		log.Printf("[crypto] WARNING: Failed to persist encryption keypair: %v", err)
	}

	log.Printf("[crypto] Generated new X25519 encryption keypair")
	return kp, nil
}

// PublicKeyHex returns the hex-encoded public key for sending in checkin.
func (kp *EnvelopeKeypair) PublicKeyHex() string {
	return hex.EncodeToString(kp.PublicKey[:])
}

// EncryptedCredentials is the wire format for encrypted credential payloads.
type EncryptedCredentials struct {
	EphemeralPublicKey string `json:"ephemeral_public_key"` // hex
	Nonce              string `json:"nonce"`                // hex, 24 bytes
	Ciphertext         string `json:"ciphertext"`           // hex
}

// DecryptCredentials decrypts an encrypted credential payload.
// Returns the plaintext JSON containing windows_targets and linux_targets.
func (kp *EnvelopeKeypair) DecryptCredentials(enc *EncryptedCredentials) ([]byte, error) {
	ephPubBytes, err := hex.DecodeString(enc.EphemeralPublicKey)
	if err != nil || len(ephPubBytes) != 32 {
		return nil, fmt.Errorf("invalid ephemeral public key")
	}
	nonceBytes, err := hex.DecodeString(enc.Nonce)
	if err != nil || len(nonceBytes) != 24 {
		return nil, fmt.Errorf("invalid nonce")
	}
	ciphertext, err := hex.DecodeString(enc.Ciphertext)
	if err != nil {
		return nil, fmt.Errorf("invalid ciphertext: %w", err)
	}

	var ephPub [32]byte
	var nonce [24]byte
	copy(ephPub[:], ephPubBytes)
	copy(nonce[:], nonceBytes)

	plaintext, ok := box.Open(nil, ciphertext, &nonce, &ephPub, &kp.PrivateKey)
	if !ok {
		return nil, fmt.Errorf("NaCl box decryption failed (key mismatch or tampered)")
	}

	return plaintext, nil
}
