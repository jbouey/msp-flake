package daemon

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

func makeCAKeypair(t *testing.T) (ed25519.PrivateKey, ed25519.PublicKey, string) {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatalf("ed25519: %v", err)
	}
	return priv, pub, hex.EncodeToString(pub)
}

func writeCertPair(t *testing.T, dir string, payload ClaimCertPayload, sig string, caPubHex string) (certPath, caPubPath string) {
	t.Helper()
	doc := ClaimCert{Payload: payload, SignatureB64: sig, Algorithm: "ed25519"}
	certBytes, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		t.Fatalf("marshal cert: %v", err)
	}
	certPath = filepath.Join(dir, "claim.cert")
	caPubPath = filepath.Join(dir, "claim-ca.pub")
	if err := os.WriteFile(certPath, certBytes, 0o644); err != nil {
		t.Fatalf("write cert: %v", err)
	}
	if err := os.WriteFile(caPubPath, []byte(caPubHex+"\n"), 0o644); err != nil {
		t.Fatalf("write capub: %v", err)
	}
	return
}

func signCertPayload(t *testing.T, priv ed25519.PrivateKey, p ClaimCertPayload) string {
	t.Helper()
	canonical := canonicalCertJSON(p)
	sig := ed25519.Sign(priv, canonical)
	return base64.RawURLEncoding.EncodeToString(sig)
}

func validPayload(caPubHex string) ClaimCertPayload {
	now := time.Now().UTC().Truncate(time.Second)
	return ClaimCertPayload{
		IsoReleaseSHA: "deadbeef" + "00000000000000000000000000000000",
		CAPubkeyHex:   caPubHex,
		IssuedAt:      now.Format(time.RFC3339),
		ValidUntil:    now.Add(90 * 24 * time.Hour).Format(time.RFC3339),
		Version:       1,
	}
}

// ---------------------------------------------------------------------------
// LoadClaimCertFrom
// ---------------------------------------------------------------------------

func TestLoadClaimCert_AbsentFilesReturnsNilNil(t *testing.T) {
	dir := t.TempDir()
	cert, err := LoadClaimCertFrom(filepath.Join(dir, "missing.cert"), filepath.Join(dir, "missing.pub"))
	if cert != nil {
		t.Errorf("expected nil cert when file missing, got %+v", cert)
	}
	if err != nil {
		t.Errorf("expected nil err, got %v", err)
	}
}

func TestLoadClaimCert_PresentCertMissingCAFails(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "claim.cert"), []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}
	_, err := LoadClaimCertFrom(
		filepath.Join(dir, "claim.cert"),
		filepath.Join(dir, "missing.pub"),
	)
	if err == nil {
		t.Error("expected error when CA pubkey missing")
	}
}

func TestLoadClaimCert_HappyPath(t *testing.T) {
	priv, _, pubHex := makeCAKeypair(t)
	dir := t.TempDir()
	payload := validPayload(pubHex)
	sig := signCertPayload(t, priv, payload)
	certPath, caPubPath := writeCertPair(t, dir, payload, sig, pubHex)

	cert, err := LoadClaimCertFrom(certPath, caPubPath)
	if err != nil {
		t.Fatalf("LoadClaimCert: %v", err)
	}
	if cert == nil {
		t.Fatal("expected non-nil cert")
	}
	if cert.Payload.CAPubkeyHex != pubHex {
		t.Errorf("CA pubkey mismatch")
	}
}

func TestLoadClaimCert_RejectsTamperedSignature(t *testing.T) {
	priv, _, pubHex := makeCAKeypair(t)
	dir := t.TempDir()
	payload := validPayload(pubHex)
	sig := signCertPayload(t, priv, payload)

	// Flip a bit in the signature.
	sigBytes, _ := base64.RawURLEncoding.DecodeString(sig)
	sigBytes[0] ^= 0xFF
	tampered := base64.RawURLEncoding.EncodeToString(sigBytes)

	certPath, caPubPath := writeCertPair(t, dir, payload, tampered, pubHex)
	_, err := LoadClaimCertFrom(certPath, caPubPath)
	if err == nil {
		t.Error("expected sig verification failure")
	}
}

func TestLoadClaimCert_RejectsExpired(t *testing.T) {
	priv, _, pubHex := makeCAKeypair(t)
	dir := t.TempDir()
	payload := validPayload(pubHex)
	payload.ValidUntil = time.Now().UTC().Add(-1 * time.Hour).Format(time.RFC3339)
	sig := signCertPayload(t, priv, payload)
	certPath, caPubPath := writeCertPair(t, dir, payload, sig, pubHex)

	_, err := LoadClaimCertFrom(certPath, caPubPath)
	if err == nil {
		t.Error("expected expiry error")
	}
}

func TestLoadClaimCert_RejectsCAPubkeyDrift(t *testing.T) {
	priv1, _, pub1Hex := makeCAKeypair(t)
	_, _, pub2Hex := makeCAKeypair(t)
	dir := t.TempDir()
	payload := validPayload(pub1Hex)
	sig := signCertPayload(t, priv1, payload)
	// Cert claims pub1; we ship CA pubkey file with pub2 — drift!
	certPath, caPubPath := writeCertPair(t, dir, payload, sig, pub2Hex)

	_, err := LoadClaimCertFrom(certPath, caPubPath)
	if err == nil {
		t.Error("expected CA pubkey drift detection")
	}
}

// ---------------------------------------------------------------------------
// BuildCSR
// ---------------------------------------------------------------------------

func TestBuildCSR_NilIdentity(t *testing.T) {
	priv, _, pubHex := makeCAKeypair(t)
	payload := validPayload(pubHex)
	sig := signCertPayload(t, priv, payload)
	cert := &ClaimCert{Payload: payload, SignatureB64: sig}
	if _, err := BuildCSR(nil, cert, "site-x", "AA:BB:CC:DD:EE:FF", "HW"); err == nil {
		t.Error("expected error for nil identity")
	}
}

func TestBuildCSR_NilCert(t *testing.T) {
	dir := t.TempDir()
	id, _ := LoadOrCreateIdentity(dir)
	if _, err := BuildCSR(id, nil, "site-x", "AA:BB:CC:DD:EE:FF", "HW"); err == nil {
		t.Error("expected error for nil cert")
	}
}

func TestBuildCSR_PopulatesAllFields(t *testing.T) {
	priv, _, pubHex := makeCAKeypair(t)
	payload := validPayload(pubHex)
	sig := signCertPayload(t, priv, payload)
	cert := &ClaimCert{Payload: payload, SignatureB64: sig}

	dir := t.TempDir()
	id, err := LoadOrCreateIdentity(dir)
	if err != nil {
		t.Fatalf("identity: %v", err)
	}
	csr, err := BuildCSR(id, cert, "site-x", "aa:bb:cc:dd:ee:ff", "HW-123")
	if err != nil {
		t.Fatalf("BuildCSR: %v", err)
	}
	if csr.SiteID != "site-x" {
		t.Errorf("SiteID drift")
	}
	if csr.MACAddress != "AA:BB:CC:DD:EE:FF" {
		t.Errorf("MAC must be uppercased: got %q", csr.MACAddress)
	}
	if csr.AgentPubkeyHex != id.PublicKeyHex() {
		t.Errorf("AgentPubkeyHex mismatch")
	}
	if len(csr.Nonce) != 32 {
		t.Errorf("nonce wrong length: %d", len(csr.Nonce))
	}
	if csr.HardwareID != "HW-123" {
		t.Errorf("HardwareID drift")
	}
	if csr.CSRSignatureB64 == "" {
		t.Errorf("CSR signature missing")
	}
}

// TestBuildCSR_VerifiesAgainstAgentPublicKey is the round-trip check
// — re-derive the canonical bytes the way the BACKEND will, and
// verify the signature with the daemon's identity public key. If
// this passes, daemon and backend canonical layouts are byte-locked.
func TestBuildCSR_VerifiesAgainstAgentPublicKey(t *testing.T) {
	priv, _, pubHex := makeCAKeypair(t)
	payload := validPayload(pubHex)
	sig := signCertPayload(t, priv, payload)
	cert := &ClaimCert{Payload: payload, SignatureB64: sig}

	dir := t.TempDir()
	id, _ := LoadOrCreateIdentity(dir)
	csr, _ := BuildCSR(id, cert, "site-x", "AA:BB:CC:DD:EE:FF", "HW")

	// Re-derive backend canonical.
	canonical := canonicalCSRBytes(
		csr.SiteID, csr.MACAddress, csr.AgentPubkeyHex,
		csr.HardwareID, csr.Nonce, csr.Timestamp, csr.ClaimCert.Payload,
	)
	csrSig, err := base64.RawURLEncoding.DecodeString(csr.CSRSignatureB64)
	if err != nil {
		t.Fatalf("decode csr sig: %v", err)
	}
	if !ed25519.Verify(id.PublicKey(), canonical, csrSig) {
		t.Fatal("CSR sig does not verify with daemon identity pubkey")
	}
}

func TestBuildCSR_NonceIsRandomPerCall(t *testing.T) {
	priv, _, pubHex := makeCAKeypair(t)
	payload := validPayload(pubHex)
	sig := signCertPayload(t, priv, payload)
	cert := &ClaimCert{Payload: payload, SignatureB64: sig}
	dir := t.TempDir()
	id, _ := LoadOrCreateIdentity(dir)

	csr1, _ := BuildCSR(id, cert, "s", "AA:BB:CC:DD:EE:FF", "HW")
	csr2, _ := BuildCSR(id, cert, "s", "AA:BB:CC:DD:EE:FF", "HW")
	if csr1.Nonce == csr2.Nonce {
		t.Error("nonce reused — collision means replay isn't blocked")
	}
}

// TestCanonicalCertJSON_DeterministicOutput pins the exact bytes the
// canonical helper produces. Drift here means every cert ever issued
// is invalidated overnight — test failure should make us think VERY
// hard before changing.
func TestCanonicalCertJSON_DeterministicOutput(t *testing.T) {
	p := ClaimCertPayload{
		IsoReleaseSHA: "deadbeef",
		CAPubkeyHex:   "abcd",
		IssuedAt:      "2026-04-15T03:45:23Z",
		ValidUntil:    "2026-07-14T03:45:23Z",
		Version:       1,
	}
	got := string(canonicalCertJSON(p))
	// Strict alphabetical key order: ca_pubkey_hex, iso_release_sha,
	// issued_at, valid_until, version.
	// "iso_release_sha" < "issued_at" because 'o' < 's' at position 2.
	want := `{"ca_pubkey_hex":"abcd","iso_release_sha":"deadbeef","issued_at":"2026-04-15T03:45:23Z","valid_until":"2026-07-14T03:45:23Z","version":1}`
	if got != want {
		t.Errorf("canonical drift!\nwant: %s\n got: %s", want, got)
	}
}
