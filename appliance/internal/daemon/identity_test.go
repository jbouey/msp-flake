package daemon

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func TestLoadOrCreateIdentity_FirstBootCreatesKeypair(t *testing.T) {
	dir := t.TempDir()

	id, err := LoadOrCreateIdentity(dir)
	if err != nil {
		t.Fatalf("LoadOrCreateIdentity: %v", err)
	}

	if got := len(id.PublicKey()); got != ed25519.PublicKeySize {
		t.Fatalf("PublicKey length: want %d got %d", ed25519.PublicKeySize, got)
	}
	if id.Fingerprint() == "" || len(id.Fingerprint()) != 16 {
		t.Fatalf("Fingerprint should be 16 hex chars, got %q", id.Fingerprint())
	}

	// On-disk artifacts present + correct.
	for _, name := range []string{identityKeyFile, identityPubFile, identityFingerprintFile} {
		if _, err := os.Stat(filepath.Join(dir, name)); err != nil {
			t.Errorf("expected %s on disk: %v", name, err)
		}
	}

	// Private key file is mode 0600.
	info, err := os.Stat(filepath.Join(dir, identityKeyFile))
	if err != nil {
		t.Fatalf("stat agent.key: %v", err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Errorf("agent.key perm: want 0600 got %o", info.Mode().Perm())
	}
}

func TestLoadOrCreateIdentity_PersistenceAcrossReloads(t *testing.T) {
	dir := t.TempDir()

	id1, err := LoadOrCreateIdentity(dir)
	if err != nil {
		t.Fatalf("first call: %v", err)
	}
	pub1 := id1.PublicKeyHex()
	fp1 := id1.Fingerprint()

	// Reload.
	id2, err := LoadOrCreateIdentity(dir)
	if err != nil {
		t.Fatalf("second call: %v", err)
	}
	pub2 := id2.PublicKeyHex()
	fp2 := id2.Fingerprint()

	if pub1 != pub2 {
		t.Errorf("public key drifted across reloads: %q vs %q", pub1, pub2)
	}
	if fp1 != fp2 {
		t.Errorf("fingerprint drifted: %q vs %q", fp1, fp2)
	}
}

func TestLoadOrCreateIdentity_RejectsCorruptKeyFile(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, identityKeyFile), []byte("not-hex\n"), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}
	if _, err := LoadOrCreateIdentity(dir); err == nil {
		t.Error("expected error for non-hex agent.key")
	}
}

func TestLoadOrCreateIdentity_RejectsWrongLengthSeed(t *testing.T) {
	dir := t.TempDir()
	// Hex-encoded 16 bytes — not 32. Should fail length check.
	if err := os.WriteFile(filepath.Join(dir, identityKeyFile),
		[]byte(hex.EncodeToString(bytes.Repeat([]byte{0xAB}, 16))+"\n"), 0o600); err != nil {
		t.Fatalf("setup: %v", err)
	}
	_, err := LoadOrCreateIdentity(dir)
	if err == nil || !strings.Contains(err.Error(), "32 bytes") {
		t.Errorf("want length error, got %v", err)
	}
}

func TestLoadOrCreateIdentity_RejectsMissingDir(t *testing.T) {
	_, err := LoadOrCreateIdentity("/nonexistent/path/that/should/not/exist")
	if err == nil {
		t.Error("expected error for missing stateDir")
	}
}

func TestLoadOrCreateIdentity_RejectsEmptyDir(t *testing.T) {
	_, err := LoadOrCreateIdentity("")
	if err == nil {
		t.Error("expected error for empty stateDir")
	}
}

func TestSign_RoundTripVerifies(t *testing.T) {
	dir := t.TempDir()
	id, err := LoadOrCreateIdentity(dir)
	if err != nil {
		t.Fatalf("LoadOrCreateIdentity: %v", err)
	}
	msg := []byte("canonical input — POST\n/api/x\nbody-hash\n2026-04-15T03:45:23Z\nnonce")
	sig := id.Sign(msg)
	if len(sig) != ed25519.SignatureSize {
		t.Fatalf("sig length: want %d got %d", ed25519.SignatureSize, len(sig))
	}
	if !ed25519.Verify(id.PublicKey(), msg, sig) {
		t.Error("verify failed for matching pubkey")
	}
	// Tampered payload should fail verify.
	if ed25519.Verify(id.PublicKey(), append([]byte("X"), msg...), sig) {
		t.Error("verify accepted tampered message")
	}
}

func TestSign_ConcurrencyIsSafe(t *testing.T) {
	dir := t.TempDir()
	id, err := LoadOrCreateIdentity(dir)
	if err != nil {
		t.Fatalf("LoadOrCreateIdentity: %v", err)
	}
	var wg sync.WaitGroup
	for i := 0; i < 64; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			payload := []byte{byte(i), byte(i >> 8)}
			sig := id.Sign(payload)
			if !ed25519.Verify(id.PublicKey(), payload, sig) {
				t.Errorf("verify failed for payload #%d", i)
			}
		}(i)
	}
	wg.Wait()
}

func TestFingerprint_MatchesBackendDerivation(t *testing.T) {
	// The Python backend computes:
	//   sha256(raw_pubkey_bytes).hexdigest()[:16]
	// Confirm Go derivation matches bit-for-bit so a daemon and
	// server compute the same fingerprint for the same key.
	dir := t.TempDir()
	id, err := LoadOrCreateIdentity(dir)
	if err != nil {
		t.Fatalf("LoadOrCreateIdentity: %v", err)
	}
	sum := sha256.Sum256(id.PublicKey())
	want := hex.EncodeToString(sum[:])[:16]
	if id.Fingerprint() != want {
		t.Errorf("fingerprint mismatch: helper=%q raw=%q", id.Fingerprint(), want)
	}
}

func TestEnsureSidecars_RewritesDriftedPub(t *testing.T) {
	dir := t.TempDir()
	id, err := LoadOrCreateIdentity(dir)
	if err != nil {
		t.Fatalf("LoadOrCreateIdentity: %v", err)
	}

	// Corrupt the pub sidecar.
	pubPath := filepath.Join(dir, identityPubFile)
	if err := os.WriteFile(pubPath, []byte("garbage\n"), 0o644); err != nil {
		t.Fatalf("corrupt pub: %v", err)
	}

	// Reload — ensureSidecars should rewrite to truth.
	if _, err := LoadOrCreateIdentity(dir); err != nil {
		t.Fatalf("reload: %v", err)
	}

	got, err := os.ReadFile(pubPath)
	if err != nil {
		t.Fatalf("read pub after reload: %v", err)
	}
	want := id.PublicKeyHex() + "\n"
	if string(got) != want {
		t.Errorf("pub sidecar not rewritten:\n want=%q\n  got=%q", want, string(got))
	}
}

func TestAtomicWrite_LeavesNoTmpOnSuccess(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "x.txt")
	if err := atomicWrite(target, []byte("hi"), 0o644); err != nil {
		t.Fatalf("atomicWrite: %v", err)
	}
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if strings.Contains(e.Name(), ".tmp") {
			t.Errorf("leftover tmp file: %s", e.Name())
		}
	}
}

func TestManifest_HasExpectedFields(t *testing.T) {
	dir := t.TempDir()
	id, err := LoadOrCreateIdentity(dir)
	if err != nil {
		t.Fatalf("LoadOrCreateIdentity: %v", err)
	}
	m := id.Manifest()
	if m.Version != 1 {
		t.Errorf("Version: want 1 got %d", m.Version)
	}
	if m.Algorithm != "ed25519" {
		t.Errorf("Algorithm: want ed25519 got %q", m.Algorithm)
	}
	if m.Fingerprint != id.Fingerprint() {
		t.Errorf("Fingerprint mismatch: %q vs %q", m.Fingerprint, id.Fingerprint())
	}
	if m.PubkeyHex != id.PublicKeyHex() {
		t.Errorf("PubkeyHex mismatch")
	}
	if m.CreatedAt.IsZero() {
		t.Error("CreatedAt should not be zero")
	}
}
