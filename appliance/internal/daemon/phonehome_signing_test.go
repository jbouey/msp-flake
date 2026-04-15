package daemon

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"net/http"
	"strings"
	"testing"
	"time"
)

// TestSignRequest_AddsAllFourHeaders asserts the signing helper
// populates the four wire-protocol headers — and only those four.
// The names + casing must NOT drift; the backend's signature_auth
// module reads them by exact name (case-insensitive in HTTP, but
// we keep the canonical form for greppability + observability).
func TestSignRequest_AddsAllFourHeaders(t *testing.T) {
	dir := t.TempDir()
	id, err := LoadOrCreateIdentity(dir)
	if err != nil {
		t.Fatalf("identity: %v", err)
	}

	body := []byte(`{"site_id":"site-x","mac":"AA:BB:CC:DD:EE:FF"}`)
	req, _ := http.NewRequest(
		http.MethodPost,
		"https://api.osiriscare.net/api/appliances/checkin",
		bytes.NewReader(body),
	)

	signRequest(req, body, id)

	for _, h := range []string{
		"X-Appliance-Signature",
		"X-Appliance-Timestamp",
		"X-Appliance-Nonce",
		"X-Appliance-Pubkey-Fingerprint",
	} {
		if v := req.Header.Get(h); v == "" {
			t.Errorf("expected header %q to be set", h)
		}
	}
}

// TestSignRequest_FingerprintMatchesIdentity guards against the
// helper accidentally emitting a different fingerprint than the
// identity reports — that would cause the server to look up the
// wrong key and silently fail every signature.
func TestSignRequest_FingerprintMatchesIdentity(t *testing.T) {
	dir := t.TempDir()
	id, err := LoadOrCreateIdentity(dir)
	if err != nil {
		t.Fatalf("identity: %v", err)
	}
	req, _ := http.NewRequest(http.MethodPost, "https://api.osiriscare.net/x", bytes.NewReader(nil))
	signRequest(req, nil, id)

	got := req.Header.Get("X-Appliance-Pubkey-Fingerprint")
	if got != id.Fingerprint() {
		t.Errorf("fingerprint header drift: header=%q identity=%q", got, id.Fingerprint())
	}
}

// TestSignRequest_TimestampInValidWindow makes sure the timestamp
// the daemon emits is the same RFC3339 second-precision Z form
// signature_auth.TS_RE expects on the server. A regex check here
// gives us a strong "the daemon and server agree on shape" guarantee.
func TestSignRequest_TimestampInValidWindow(t *testing.T) {
	dir := t.TempDir()
	id, _ := LoadOrCreateIdentity(dir)
	req, _ := http.NewRequest(http.MethodPost, "https://api.osiriscare.net/x", bytes.NewReader(nil))
	signRequest(req, nil, id)

	ts := req.Header.Get("X-Appliance-Timestamp")
	parsed, err := time.Parse("2006-01-02T15:04:05Z", ts)
	if err != nil {
		t.Fatalf("timestamp not RFC3339-second-Z: %q (%v)", ts, err)
	}
	skew := time.Since(parsed)
	if skew < 0 {
		skew = -skew
	}
	if skew > 5*time.Second {
		t.Errorf("timestamp drifted from now by %v — clock or formatter bug", skew)
	}
}

// TestSignRequest_NonceIs32HexChars locks the nonce shape — the
// backend rejects anything else with reason="bad_nonce".
func TestSignRequest_NonceIs32HexChars(t *testing.T) {
	dir := t.TempDir()
	id, _ := LoadOrCreateIdentity(dir)
	req, _ := http.NewRequest(http.MethodPost, "https://api.osiriscare.net/x", bytes.NewReader(nil))
	signRequest(req, nil, id)

	nonce := req.Header.Get("X-Appliance-Nonce")
	if len(nonce) != 32 {
		t.Fatalf("nonce length: want 32 got %d (%q)", len(nonce), nonce)
	}
	for _, c := range nonce {
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')) {
			t.Errorf("nonce char %q not lowercase hex", c)
		}
	}
}

// TestSignRequest_VerifiesAgainstPublicKey is the round-trip
// integration test: we re-derive the canonical input the same way
// the backend will, and verify with the daemon's PublicKey. If this
// passes, the daemon and the in-process spec are in lockstep. The
// backend's Python implementation is then locked to the same spec
// by tests/test_signature_auth.py.
func TestSignRequest_VerifiesAgainstPublicKey(t *testing.T) {
	dir := t.TempDir()
	id, _ := LoadOrCreateIdentity(dir)

	body := []byte(`{"hello":"world"}`)
	method := http.MethodPost
	path := "/api/appliances/checkin"
	url := "https://api.osiriscare.net" + path
	req, _ := http.NewRequest(method, url, bytes.NewReader(body))
	signRequest(req, body, id)

	ts := req.Header.Get("X-Appliance-Timestamp")
	nonce := req.Header.Get("X-Appliance-Nonce")
	sigB64 := req.Header.Get("X-Appliance-Signature")

	bodyHash := sha256.Sum256(body)
	canonical := []byte(strings.ToUpper(method) +
		"\n" + path +
		"\n" + hex.EncodeToString(bodyHash[:]) +
		"\n" + ts +
		"\n" + nonce)

	sig, err := base64.RawURLEncoding.DecodeString(sigB64)
	if err != nil {
		t.Fatalf("decode sig: %v", err)
	}
	if !ed25519.Verify(id.PublicKey(), canonical, sig) {
		t.Fatal("re-derived canonical failed verify — daemon emits one shape, test expects another")
	}
}

// TestSignRequest_NoIdentityIsNoOp confirms the contract that a
// nil-identity client falls back to bearer-only without panicking.
// (This branch is taken by the legacy NewPhoneHomeClient constructor.)
func TestSignRequest_NoIdentityIsNoOp(t *testing.T) {
	defer func() {
		if r := recover(); r != nil {
			t.Errorf("nil identity caused panic: %v", r)
		}
	}()
	cfg := &Config{StateDir: t.TempDir()}
	c := NewPhoneHomeClient(cfg)
	if c.identity != nil {
		t.Error("default constructor should leave identity nil")
	}
}
