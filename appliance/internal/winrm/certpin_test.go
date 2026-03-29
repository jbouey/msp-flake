package winrm

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"
)

func TestCertPinStore_NewEmpty(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pins.json")

	store := NewCertPinStore(path)
	if store.PinCount() != 0 {
		t.Fatalf("expected 0 pins, got %d", store.PinCount())
	}
}

func TestCertPinStore_SetAndGet(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pins.json")

	store := NewCertPinStore(path)
	store.SetPin("192.168.88.250", "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890")

	pin, ok := store.GetPin("192.168.88.250")
	if !ok {
		t.Fatal("expected pin to exist")
	}
	if pin.Fingerprint != "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890" {
		t.Fatalf("unexpected fingerprint: %s", pin.Fingerprint)
	}
	if pin.FirstSeen == "" {
		t.Fatal("FirstSeen should be set")
	}
	if pin.LastSeen == "" {
		t.Fatal("LastSeen should be set")
	}
}

func TestCertPinStore_Persistence(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pins.json")

	// Create and populate
	store1 := NewCertPinStore(path)
	store1.SetPin("host1", "fingerprint1")
	store1.SetPin("host2", "fingerprint2")

	// Reload from disk
	store2 := NewCertPinStore(path)
	if store2.PinCount() != 2 {
		t.Fatalf("expected 2 pins after reload, got %d", store2.PinCount())
	}
	pin, ok := store2.GetPin("host1")
	if !ok || pin.Fingerprint != "fingerprint1" {
		t.Fatalf("host1 pin mismatch after reload: ok=%v fingerprint=%s", ok, pin.Fingerprint)
	}
	pin, ok = store2.GetPin("host2")
	if !ok || pin.Fingerprint != "fingerprint2" {
		t.Fatalf("host2 pin mismatch after reload: ok=%v fingerprint=%s", ok, pin.Fingerprint)
	}
}

func TestCertPinStore_ClearPin(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pins.json")

	store := NewCertPinStore(path)
	store.SetPin("host1", "fp1")
	store.SetPin("host2", "fp2")

	store.ClearPin("host1")

	if store.PinCount() != 1 {
		t.Fatalf("expected 1 pin after clear, got %d", store.PinCount())
	}
	_, ok := store.GetPin("host1")
	if ok {
		t.Fatal("host1 should be gone after ClearPin")
	}
	_, ok = store.GetPin("host2")
	if !ok {
		t.Fatal("host2 should still exist")
	}

	// Verify persistence
	store2 := NewCertPinStore(path)
	if store2.PinCount() != 1 {
		t.Fatalf("expected 1 pin after reload, got %d", store2.PinCount())
	}
}

func TestCertPinStore_ClearNonexistent(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pins.json")

	store := NewCertPinStore(path)
	// Should not panic
	store.ClearPin("nonexistent")
	if store.PinCount() != 0 {
		t.Fatalf("expected 0 pins, got %d", store.PinCount())
	}
}

func TestCertPinStore_TOFUFirstConnect(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pins.json")
	store := NewCertPinStore(path)

	// Simulate a leaf certificate
	fakeCertDER := []byte("this is a fake certificate DER blob for testing")
	expectedFP := SHA256Hex(fakeCertDER)

	tlsCfg := store.TLSConfigForHost("10.0.0.1")

	// First connect: TOFU should accept and pin
	err := tlsCfg.VerifyPeerCertificate([][]byte{fakeCertDER}, nil)
	if err != nil {
		t.Fatalf("TOFU should accept first cert, got: %v", err)
	}

	// Verify pin was stored
	pin, ok := store.GetPin("10.0.0.1")
	if !ok {
		t.Fatal("pin should exist after TOFU")
	}
	if pin.Fingerprint != expectedFP {
		t.Fatalf("stored fingerprint mismatch: got %s, want %s", pin.Fingerprint, expectedFP)
	}
}

func TestCertPinStore_MatchingCert(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pins.json")
	store := NewCertPinStore(path)

	fakeCertDER := []byte("consistent certificate bytes")

	// First connect: TOFU pin
	tlsCfg := store.TLSConfigForHost("10.0.0.2")
	err := tlsCfg.VerifyPeerCertificate([][]byte{fakeCertDER}, nil)
	if err != nil {
		t.Fatalf("TOFU failed: %v", err)
	}

	// Second connect: same cert, should pass
	err = tlsCfg.VerifyPeerCertificate([][]byte{fakeCertDER}, nil)
	if err != nil {
		t.Fatalf("matching cert should pass, got: %v", err)
	}
}

func TestCertPinStore_MismatchRejected(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pins.json")
	store := NewCertPinStore(path)

	realCertDER := []byte("the real server certificate")
	fakeCertDER := []byte("an attacker's certificate")

	// First connect: TOFU pin the real cert
	tlsCfg := store.TLSConfigForHost("10.0.0.3")
	err := tlsCfg.VerifyPeerCertificate([][]byte{realCertDER}, nil)
	if err != nil {
		t.Fatalf("TOFU failed: %v", err)
	}

	// Second connect: different cert (MITM) — must be rejected
	err = tlsCfg.VerifyPeerCertificate([][]byte{fakeCertDER}, nil)
	if err == nil {
		t.Fatal("mismatched cert should be rejected, but was accepted")
	}
	if got := err.Error(); got == "" {
		t.Fatal("error message should not be empty")
	}
}

func TestCertPinStore_NoCertRejected(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pins.json")
	store := NewCertPinStore(path)

	tlsCfg := store.TLSConfigForHost("10.0.0.4")

	// No certs presented — must reject
	err := tlsCfg.VerifyPeerCertificate([][]byte{}, nil)
	if err == nil {
		t.Fatal("empty cert list should be rejected")
	}
}

func TestCertPinStore_ClearAndRepin(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pins.json")
	store := NewCertPinStore(path)

	oldCertDER := []byte("old server certificate")
	newCertDER := []byte("new server certificate after rotation")

	tlsCfg := store.TLSConfigForHost("10.0.0.5")

	// TOFU pin old cert
	err := tlsCfg.VerifyPeerCertificate([][]byte{oldCertDER}, nil)
	if err != nil {
		t.Fatalf("TOFU failed: %v", err)
	}

	// New cert would be rejected
	err = tlsCfg.VerifyPeerCertificate([][]byte{newCertDER}, nil)
	if err == nil {
		t.Fatal("new cert should be rejected before ClearPin")
	}

	// Operator clears pin (cert rotation)
	store.ClearPin("10.0.0.5")

	// Now new cert should be accepted (re-TOFU)
	err = tlsCfg.VerifyPeerCertificate([][]byte{newCertDER}, nil)
	if err != nil {
		t.Fatalf("new cert should be accepted after ClearPin, got: %v", err)
	}

	// Verify re-pinned to new cert
	pin, ok := store.GetPin("10.0.0.5")
	if !ok {
		t.Fatal("pin should exist after re-TOFU")
	}
	expectedFP := SHA256Hex(newCertDER)
	if pin.Fingerprint != expectedFP {
		t.Fatalf("re-pinned fingerprint mismatch: got %s, want %s", pin.Fingerprint, expectedFP)
	}
}

func TestCertPinStore_MultipleHosts(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pins.json")
	store := NewCertPinStore(path)

	cert1 := []byte("cert for host A")
	cert2 := []byte("cert for host B")

	cfg1 := store.TLSConfigForHost("hostA")
	cfg2 := store.TLSConfigForHost("hostB")

	// Pin both
	if err := cfg1.VerifyPeerCertificate([][]byte{cert1}, nil); err != nil {
		t.Fatalf("TOFU hostA: %v", err)
	}
	if err := cfg2.VerifyPeerCertificate([][]byte{cert2}, nil); err != nil {
		t.Fatalf("TOFU hostB: %v", err)
	}

	// Cross-check: hostA's cert should fail on hostB's config
	if err := cfg2.VerifyPeerCertificate([][]byte{cert1}, nil); err == nil {
		t.Fatal("hostA cert should not pass on hostB verifier")
	}

	if store.PinCount() != 2 {
		t.Fatalf("expected 2 pins, got %d", store.PinCount())
	}
}

func TestCertPinStore_CorruptFileStartsFresh(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pins.json")

	// Write corrupt data
	os.WriteFile(path, []byte("not valid json {{{"), 0600)

	store := NewCertPinStore(path)
	if store.PinCount() != 0 {
		t.Fatalf("corrupt file should start fresh, got %d pins", store.PinCount())
	}
}

func TestSHA256Hex(t *testing.T) {
	data := []byte("test data for hashing")
	got := SHA256Hex(data)

	expected := sha256.Sum256(data)
	want := hex.EncodeToString(expected[:])

	if got != want {
		t.Fatalf("SHA256Hex mismatch: got %s, want %s", got, want)
	}

	if len(got) != 64 {
		t.Fatalf("expected 64 hex chars, got %d", len(got))
	}
}

func TestCertPinStore_InsecureSkipVerifyTrue(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "pins.json")
	store := NewCertPinStore(path)

	// Verify that TLSConfigForHost sets InsecureSkipVerify (required because
	// the gowinrm library doesn't support custom CA — we do our own
	// verification via VerifyPeerCertificate)
	cfg := store.TLSConfigForHost("anyhost")
	if !cfg.InsecureSkipVerify {
		t.Fatal("InsecureSkipVerify must be true (we use VerifyPeerCertificate instead)")
	}
	if cfg.VerifyPeerCertificate == nil {
		t.Fatal("VerifyPeerCertificate must be set")
	}
}
