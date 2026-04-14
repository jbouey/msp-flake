// Package crypto provides Ed25519 signature verification for order integrity.
//
// Central Command signs all orders with its Ed25519 private key.
// The appliance daemon verifies signatures before executing any order,
// preventing a compromised Central Command or MITM from injecting
// malicious orders into the fleet.
package crypto

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"sort"
	"sync"
)

// OrderVerifier verifies Ed25519 signatures on orders from Central Command.
// Supports key rotation: holds both current and previous public keys so that
// orders signed with either key are accepted during a rotation window.
type OrderVerifier struct {
	mu          sync.RWMutex
	publicKey   ed25519.PublicKey
	keyHex      string
	previousKey ed25519.PublicKey // Previous key for rotation support
	prevKeyHex  string
}

// NewOrderVerifier creates a verifier. If publicKeyHex is empty, verification
// is deferred until SetPublicKey is called (first checkin provides the key).
func NewOrderVerifier(publicKeyHex string) *OrderVerifier {
	v := &OrderVerifier{}
	if publicKeyHex != "" {
		if err := v.SetPublicKey(publicKeyHex); err != nil {
			log.Printf("[crypto] Failed to set initial public key: %v", err)
		}
	}
	return v
}

// SetPublicKey sets or updates the server's Ed25519 public key.
// Called when the checkin response provides server_public_key.
func (v *OrderVerifier) SetPublicKey(hexKey string) error {
	pubBytes, err := hex.DecodeString(hexKey)
	if err != nil {
		return fmt.Errorf("decode public key hex: %w", err)
	}
	if len(pubBytes) != ed25519.PublicKeySize {
		return fmt.Errorf("invalid public key size: got %d, want %d", len(pubBytes), ed25519.PublicKeySize)
	}

	v.mu.Lock()
	defer v.mu.Unlock()
	v.publicKey = ed25519.PublicKey(pubBytes)
	v.keyHex = hexKey
	return nil
}

// SetPublicKeys sets the current public key and optionally previous keys for rotation support.
// The first element of previousHexes (if any, and different from currentHex) is stored as the
// previous key. Orders signed with either key will be accepted.
func (v *OrderVerifier) SetPublicKeys(currentHex string, previousHexes []string) error {
	// Set current key via existing method
	if err := v.SetPublicKey(currentHex); err != nil {
		return err
	}

	// Set previous key if provided and different from current
	v.mu.Lock()
	defer v.mu.Unlock()
	v.previousKey = nil
	v.prevKeyHex = ""
	if len(previousHexes) > 0 && previousHexes[0] != currentHex {
		prevBytes, err := hex.DecodeString(previousHexes[0])
		if err == nil && len(prevBytes) == ed25519.PublicKeySize {
			v.previousKey = ed25519.PublicKey(prevBytes)
			v.prevKeyHex = previousHexes[0]
			log.Printf("[crypto] Previous key set for rotation support: %s...", previousHexes[0][:16])
		}
	}
	return nil
}

// HasKey returns true if a public key has been set.
func (v *OrderVerifier) HasKey() bool {
	v.mu.RLock()
	defer v.mu.RUnlock()
	return v.publicKey != nil
}

// PublicKeyHex returns the current public key as hex string.
func (v *OrderVerifier) PublicKeyHex() string {
	v.mu.RLock()
	defer v.mu.RUnlock()
	return v.keyHex
}

// VerifyOrder verifies the Ed25519 signature on a signed order payload.
// signedPayload is the canonical JSON string that was signed.
// signatureHex is the hex-encoded 64-byte Ed25519 signature.
//
// Phase 13 H1 (Session 205): on failure, the error carries the cached
// key fingerprint(s), signed_payload length, and a SHA-256 prefix of the
// payload bytes. Enables server-side triage without physical access.
// None of these values are secrets — the pubkey is public, the hash is
// one-way, the length is meta.
func (v *OrderVerifier) VerifyOrder(signedPayload, signatureHex string) error {
	v.mu.RLock()
	pk := v.publicKey
	prevPK := v.previousKey
	keyHex := v.keyHex
	prevKeyHex := v.prevKeyHex
	v.mu.RUnlock()

	if pk == nil {
		return fmt.Errorf("no server public key configured")
	}

	sig, err := hex.DecodeString(signatureHex)
	if err != nil {
		// Detect base64-encoded signatures and give a clear diagnostic
		if decoded, b64Err := base64.StdEncoding.DecodeString(signatureHex); b64Err == nil && len(decoded) == ed25519.SignatureSize {
			return fmt.Errorf("signature is base64-encoded but must be hex-encoded (128 hex chars). "+
				"Use signature.hex() in Python, not base64.b64encode(). Got %d chars, expected 128",
				len(signatureHex))
		}
		return fmt.Errorf("decode signature: expected 128-char hex string, got %d chars: %w",
			len(signatureHex), err)
	}
	if len(sig) != ed25519.SignatureSize {
		return fmt.Errorf("invalid signature size: got %d bytes (from %d hex chars), want %d bytes (128 hex chars)",
			len(sig), len(signatureHex), ed25519.SignatureSize)
	}

	// Try current key first
	if ed25519.Verify(pk, []byte(signedPayload), sig) {
		return nil
	}

	// Try previous key (rotation window)
	if prevPK != nil && ed25519.Verify(prevPK, []byte(signedPayload), sig) {
		log.Printf("[crypto] Order verified with PREVIOUS key (rotation in progress)")
		return nil
	}

	// Phase 13 H1: enrich the failure with diagnostic metadata.
	spSum := sha256.Sum256([]byte(signedPayload))
	spSumHex := hex.EncodeToString(spSum[:])
	curFp := safeFingerprint(keyHex)
	prevFp := safeFingerprint(prevKeyHex)
	keyCount := 1
	if prevPK != nil {
		keyCount = 2
	}
	return fmt.Errorf(
		"Ed25519 signature verification failed "+
			"(tried %d keys; cur_fp=%s prev_fp=%s sp_len=%d sp_sha256_16=%s)",
		keyCount, curFp, prevFp, len(signedPayload), spSumHex[:16],
	)
}

// safeFingerprint returns the first 16 hex chars of a pubkey or "-" when empty.
func safeFingerprint(hexKey string) string {
	if hexKey == "" {
		return "-"
	}
	if len(hexKey) > 16 {
		return hexKey[:16]
	}
	return hexKey
}

// VerifyRulesBundle verifies the signature on a rules sync response.
// rulesJSON is the canonical JSON string of the rules array.
// signatureHex is the hex-encoded Ed25519 signature.
func (v *OrderVerifier) VerifyRulesBundle(rulesJSON, signatureHex string) error {
	return v.VerifyOrder(rulesJSON, signatureHex)
}

// VerifyOrderWithEnvelopeKey is Phase 13.5 H6 — a bounded-trust fallback
// verifier. Tries current + previous keys first; on failure, if the
// envelope carries `signing_pubkey_hex` AND it matches the pubkey the
// server most-recently delivered via checkin (which the caller passes
// as `trustedEnvelopeKeyHex`), retry the verify against that envelope
// key.
//
// Why "bounded": the daemon does NOT accept arbitrary envelope keys.
// The envelope's pubkey is only honored when an independent channel
// (the most-recent checkin) already acknowledged the same key — so the
// envelope adds a second verification path without widening trust.
//
// Closes the "verifier cache is stale but the checkin already delivered
// the new pubkey" race window, at zero cost to security posture.
//
// Returns nil on any successful verification. Returns the original
// current-keys error (H1 enriched) on any kind of failure — callers
// don't get a different error shape for the envelope-key path.
func (v *OrderVerifier) VerifyOrderWithEnvelopeKey(
	signedPayload, signatureHex, envelopeKeyHex, trustedEnvelopeKeyHex string,
) error {
	// Fast path — current + previous key already verifies.
	if err := v.VerifyOrder(signedPayload, signatureHex); err == nil {
		return nil
	} else {
		// Only attempt H6 path when we have BOTH an envelope key (the
		// payload's advertised pubkey) AND a trusted reference (the
		// pubkey most-recently delivered via checkin). Both must be
		// present and byte-equal for the envelope key to be honored.
		if envelopeKeyHex == "" || trustedEnvelopeKeyHex == "" {
			return err
		}
		if envelopeKeyHex != trustedEnvelopeKeyHex {
			// Envelope is advertising a pubkey the server has NOT
			// delivered to us. Reject — this is the attack vector the
			// bounded-trust design rejects.
			return err
		}
	}

	// Envelope pubkey matches last-delivered. Try it.
	pkBytes, decodeErr := hex.DecodeString(envelopeKeyHex)
	if decodeErr != nil || len(pkBytes) != ed25519.PublicKeySize {
		return fmt.Errorf("envelope pubkey hex invalid: decode err=%v len=%d", decodeErr, len(pkBytes))
	}
	sig, sigErr := hex.DecodeString(signatureHex)
	if sigErr != nil || len(sig) != ed25519.SignatureSize {
		return fmt.Errorf("signature decode: err=%v len=%d", sigErr, len(sig))
	}

	pk := ed25519.PublicKey(pkBytes)
	if ed25519.Verify(pk, []byte(signedPayload), sig) {
		log.Printf("[crypto] H6 verify PASSED via envelope pubkey %s (matches last-checkin-delivered)",
			safeFingerprint(envelopeKeyHex))
		return nil
	}

	// Same enriched error as VerifyOrder for consistency
	spSum := sha256.Sum256([]byte(signedPayload))
	spSumHex := hex.EncodeToString(spSum[:])
	return fmt.Errorf(
		"Ed25519 signature verification failed (H6 envelope path also fails; "+
			"envelope_fp=%s sp_len=%d sp_sha256_16=%s)",
		safeFingerprint(envelopeKeyHex), len(signedPayload), spSumHex[:16],
	)
}

// BuildSignedPayload reconstructs the canonical signed payload from order fields.
// This must match the Python side's json.dumps(dict, sort_keys=True) format.
func BuildSignedPayload(fields map[string]interface{}) (string, error) {
	// Sort keys for deterministic JSON (matches Python's sort_keys=True)
	keys := make([]string, 0, len(fields))
	for k := range fields {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	ordered := make([]byte, 0, 256)
	ordered = append(ordered, '{')
	for i, k := range keys {
		if i > 0 {
			ordered = append(ordered, ',', ' ')
		}
		keyJSON, _ := json.Marshal(k)
		ordered = append(ordered, keyJSON...)
		ordered = append(ordered, ':', ' ')
		valJSON, err := json.Marshal(fields[k])
		if err != nil {
			return "", fmt.Errorf("marshal field %q: %w", k, err)
		}
		ordered = append(ordered, valJSON...)
	}
	ordered = append(ordered, '}')

	return string(ordered), nil
}
