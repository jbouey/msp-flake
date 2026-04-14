// Tests for D1 heartbeat signing helper (SystemInfoSigned).
// Enterprise-grade: validate canonical payload format, signature
// round-trip, graceful failure when SignFunc is nil or errors.

package daemon

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"
)

// Minimal Config stub — SystemInfoSigned only needs SiteID.
func testCfg() *Config {
	return &Config{SiteID: "test-site"}
}

func TestSystemInfoSigned_NilSignFunc_OmitsSignature(t *testing.T) {
	// If the caller has no signing key, heartbeat_signature must be empty
	// (omitempty → server records NULL). Must NOT panic.
	req := SystemInfoSigned(testCfg(), "0.4.1", "deadbeef", nil)
	if req.HeartbeatSignature != "" {
		t.Errorf("expected empty sig when SignFunc is nil, got %q", req.HeartbeatSignature)
	}
}

func TestSystemInfoSigned_SignFuncError_OmitsSignature(t *testing.T) {
	// SignFunc can error (HSM offline, key rotation mid-flight). Must
	// continue with unsigned heartbeat — never block the checkin.
	signFn := func([]byte) ([]byte, error) {
		return nil, errors.New("signing service unavailable")
	}
	req := SystemInfoSigned(testCfg(), "0.4.1", "deadbeef", signFn)
	if req.HeartbeatSignature != "" {
		t.Errorf("expected empty sig on SignFunc error, got %q", req.HeartbeatSignature)
	}
}

func TestSystemInfoSigned_ProducesValidEd25519Signature(t *testing.T) {
	// Generate a real Ed25519 key pair + verify the signature the helper
	// produces is valid against the canonical payload. This is the
	// contract the server depends on.
	pub, priv, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatalf("keygen: %v", err)
	}

	signFn := func(msg []byte) ([]byte, error) {
		return ed25519.Sign(priv, msg), nil
	}

	before := time.Now().UTC().Unix()
	req := SystemInfoSigned(testCfg(), "0.4.1", hex.EncodeToString(pub), signFn)
	after := time.Now().UTC().Unix()

	if req.HeartbeatSignature == "" {
		t.Fatal("expected non-empty signature")
	}

	sig, err := hex.DecodeString(req.HeartbeatSignature)
	if err != nil {
		t.Fatalf("decode sig: %v", err)
	}
	if len(sig) != ed25519.SignatureSize {
		t.Errorf("sig size: got %d want %d", len(sig), ed25519.SignatureSize)
	}

	// Canonical payload (must match what the helper signed):
	//   SiteID | MAC(upper) | unix_ts | AgentVersion
	// We don't know the exact ts the helper used, but it MUST be in
	// [before, after]. Try every ts in the window.
	mac := strings.ToUpper(req.MACAddress)
	verified := false
	for ts := before; ts <= after; ts++ {
		payload := fmt.Sprintf("%s|%s|%d|%s", req.SiteID, mac, ts, req.AgentVersion)
		h := sha256.Sum256([]byte(payload))
		if ed25519.Verify(pub, h[:], sig) {
			verified = true
			break
		}
	}
	if !verified {
		t.Errorf("signature did not verify against any ts in [%d, %d] — "+
			"canonical format may have drifted. sig=%s",
			before, after, req.HeartbeatSignature)
	}
}

func TestSystemInfoSigned_PreservesBaseFields(t *testing.T) {
	// Signing helper must NOT drop any fields that SystemInfoWithKey
	// populated. Otherwise the checkin loses data.
	pub, priv, _ := ed25519.GenerateKey(nil)
	signFn := func(msg []byte) ([]byte, error) { return ed25519.Sign(priv, msg), nil }

	req := SystemInfoSigned(testCfg(), "0.4.1", hex.EncodeToString(pub), signFn)

	if req.SiteID != "test-site" {
		t.Errorf("site_id dropped: %q", req.SiteID)
	}
	if req.AgentVersion != "0.4.1" {
		t.Errorf("agent_version dropped: %q", req.AgentVersion)
	}
	if req.AgentPublicKey != hex.EncodeToString(pub) {
		t.Errorf("agent_public_key dropped")
	}
}

func TestSystemInfoSigned_DifferentSigPerCheckin(t *testing.T) {
	// Two sequential calls should produce DIFFERENT signatures (ts differs).
	// If they match, the timestamp isn't in the payload and replay protection
	// is broken.
	pub, priv, _ := ed25519.GenerateKey(nil)
	_ = pub
	signFn := func(msg []byte) ([]byte, error) { return ed25519.Sign(priv, msg), nil }

	req1 := SystemInfoSigned(testCfg(), "0.4.1", "pubkey", signFn)
	time.Sleep(1100 * time.Millisecond)
	req2 := SystemInfoSigned(testCfg(), "0.4.1", "pubkey", signFn)

	if req1.HeartbeatSignature == req2.HeartbeatSignature {
		t.Error("signatures match across 1s window — ts is not in payload, replay protection broken")
	}
}
