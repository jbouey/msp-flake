// identity.go
//
// Device-bound Ed25519 identity for the appliance.
//
// On first boot the daemon generates a keypair and persists the
// private seed to <stateDir>/agent.key (mode 0600). The public key
// + 16-hex-char fingerprint are written to two sibling files so
// operators + provisioning flows can read them without touching the
// private seed. A human-readable manifest lands at
// /etc/osiriscare-identity.json for debugging.
//
// The private key NEVER leaves this file's filesystem. No exports,
// no network sends, no copies into memory dumps that we control.
// The daemon only ever holds it to call Sign().
//
// Atomic write semantics: every write goes to a .tmp sibling first
// and then os.Rename — which is atomic on POSIX within the same
// filesystem. A partial write during a crash leaves the previous
// file intact.
//
// Threading: a single Identity is created at startup and shared.
// Sign() is safe to call concurrently because ed25519.PrivateKey.Sign
// is re-entrant (it hashes internally per-call and has no mutable
// state).

package daemon

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// Filenames inside stateDir. Deliberately short, human-readable.
const (
	identityKeyFile         = "agent.key"         // 64 hex chars of Ed25519 seed, mode 0600
	identityPubFile         = "agent.pub"         // 64 hex chars of public key
	identityFingerprintFile = "agent.fingerprint" // 16 hex chars
	identityManifestFile    = "/etc/osiriscare-identity.json"
)

// Manifest is the operator-readable identity document written to
// /etc/osiriscare-identity.json. It carries no secrets. Used by the
// recovery script + dashboards that want to show a fingerprint.
type Manifest struct {
	Version     int       `json:"version"`
	Algorithm   string    `json:"algorithm"`
	Fingerprint string    `json:"fingerprint"`
	PubkeyHex   string    `json:"pubkey_hex"`
	CreatedAt   time.Time `json:"created_at"`
}

// Identity holds an appliance's device-bound Ed25519 keypair and the
// derived fingerprint. Construct via LoadOrCreateIdentity.
type Identity struct {
	priv        ed25519.PrivateKey // 64 bytes: seed (32) + public (32)
	pub         ed25519.PublicKey
	fingerprint string // 16 lowercase hex chars
	stateDir    string
	createdAt   time.Time
	mu          sync.RWMutex // guards future rotations
}

// LoadOrCreateIdentity reads an existing keypair from stateDir or
// creates a new one if the key file is absent.
//
// stateDir must exist and be writable. The function is idempotent
// after first call: subsequent invocations load the existing seed
// bit-for-bit.
func LoadOrCreateIdentity(stateDir string) (*Identity, error) {
	if stateDir == "" {
		return nil, errors.New("identity: stateDir must not be empty")
	}
	if info, err := os.Stat(stateDir); err != nil {
		return nil, fmt.Errorf("identity: stat stateDir: %w", err)
	} else if !info.IsDir() {
		return nil, fmt.Errorf("identity: stateDir %q is not a directory", stateDir)
	}

	keyPath := filepath.Join(stateDir, identityKeyFile)

	// Fast path: existing seed on disk, load it.
	if seedHex, err := os.ReadFile(keyPath); err == nil {
		return loadFromSeed(stateDir, strings.TrimSpace(string(seedHex)))
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("identity: read agent.key: %w", err)
	}

	// First-boot path: generate keypair.
	return generateAndPersist(stateDir)
}

func loadFromSeed(stateDir, seedHex string) (*Identity, error) {
	seed, err := hex.DecodeString(seedHex)
	if err != nil {
		return nil, fmt.Errorf("identity: agent.key is not hex: %w", err)
	}
	if len(seed) != ed25519.SeedSize {
		return nil, fmt.Errorf(
			"identity: agent.key seed must be %d bytes (got %d)",
			ed25519.SeedSize, len(seed),
		)
	}
	priv := ed25519.NewKeyFromSeed(seed)
	pub, ok := priv.Public().(ed25519.PublicKey)
	if !ok {
		return nil, errors.New("identity: failed to derive public key from seed")
	}
	fp := fingerprintOf(pub)

	// If the pub / fingerprint files were deleted or drifted, rewrite
	// them so sibling tools stay in sync. Best-effort; log and
	// continue rather than fail.
	ensureSidecars(stateDir, pub, fp)

	// Load createdAt from the key file's mtime as the best available
	// "first boot" timestamp. If the file is freshly touched it
	// won't be perfect; the manifest file also carries this.
	createdAt := loadCreatedAt(stateDir)

	return &Identity{
		priv:        priv,
		pub:         pub,
		fingerprint: fp,
		stateDir:    stateDir,
		createdAt:   createdAt,
	}, nil
}

func generateAndPersist(stateDir string) (*Identity, error) {
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("identity: GenerateKey: %w", err)
	}
	seed := priv.Seed()
	fp := fingerprintOf(pub)
	createdAt := time.Now().UTC()

	// 1. agent.key — the secret.
	if err := atomicWrite(
		filepath.Join(stateDir, identityKeyFile),
		[]byte(hex.EncodeToString(seed)+"\n"),
		0o600,
	); err != nil {
		return nil, fmt.Errorf("identity: persist agent.key: %w", err)
	}

	// 2. agent.pub — public half.
	if err := atomicWrite(
		filepath.Join(stateDir, identityPubFile),
		[]byte(hex.EncodeToString(pub)+"\n"),
		0o644,
	); err != nil {
		return nil, fmt.Errorf("identity: persist agent.pub: %w", err)
	}

	// 3. agent.fingerprint — derived.
	if err := atomicWrite(
		filepath.Join(stateDir, identityFingerprintFile),
		[]byte(fp+"\n"),
		0o644,
	); err != nil {
		return nil, fmt.Errorf("identity: persist agent.fingerprint: %w", err)
	}

	// 4. /etc/osiriscare-identity.json — operator doc. Best-effort;
	// /etc is read-only under NixOS's store-backed /etc in some
	// contexts. We tolerate failure.
	writeManifest(pub, fp, createdAt)

	return &Identity{
		priv:        priv,
		pub:         pub,
		fingerprint: fp,
		stateDir:    stateDir,
		createdAt:   createdAt,
	}, nil
}

// Sign signs an arbitrary byte payload with the device private key.
// Returns a 64-byte ed25519 signature. Safe for concurrent use.
func (i *Identity) Sign(data []byte) []byte {
	i.mu.RLock()
	defer i.mu.RUnlock()
	return ed25519.Sign(i.priv, data)
}

// PublicKey returns the raw 32-byte public key. The value is safe to
// export over the wire.
func (i *Identity) PublicKey() ed25519.PublicKey {
	i.mu.RLock()
	defer i.mu.RUnlock()
	buf := make([]byte, len(i.pub))
	copy(buf, i.pub)
	return buf
}

// PublicKeyHex returns the lowercase hex encoding of the public key.
// Used as a transport representation in checkin payloads.
func (i *Identity) PublicKeyHex() string {
	return hex.EncodeToString(i.PublicKey())
}

// Fingerprint returns the stable 16-char identity fingerprint. Match
// value the dashboard shows and the backend indexes on.
func (i *Identity) Fingerprint() string {
	i.mu.RLock()
	defer i.mu.RUnlock()
	return i.fingerprint
}

// CreatedAt returns the best-effort first-boot timestamp.
func (i *Identity) CreatedAt() time.Time {
	i.mu.RLock()
	defer i.mu.RUnlock()
	return i.createdAt
}

// Manifest returns the public (non-secret) identity document.
func (i *Identity) Manifest() Manifest {
	return Manifest{
		Version:     1,
		Algorithm:   "ed25519",
		Fingerprint: i.Fingerprint(),
		PubkeyHex:   i.PublicKeyHex(),
		CreatedAt:   i.CreatedAt(),
	}
}

// ----- helpers -----

// fingerprintOf returns the canonical 16 lowercase hex char
// fingerprint for a public key. Matches signature_auth._fingerprint
// on the backend — keep them in sync.
func fingerprintOf(pub ed25519.PublicKey) string {
	sum := sha256.Sum256(pub)
	return hex.EncodeToString(sum[:])[:16]
}

// atomicWrite writes data to path via a .tmp sibling + rename. Fails
// if the destination FS doesn't support rename (shouldn't happen on
// anything we deploy on).
func atomicWrite(path string, data []byte, perm os.FileMode) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, filepath.Base(path)+".tmp-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	// Defer a cleanup that runs ONLY if we don't rename successfully.
	cleaned := false
	defer func() {
		if !cleaned {
			_ = os.Remove(tmpName)
		}
	}()

	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Chmod(tmpName, perm); err != nil {
		return err
	}
	if err := os.Rename(tmpName, path); err != nil {
		return err
	}
	cleaned = true
	return nil
}

func ensureSidecars(stateDir string, pub ed25519.PublicKey, fp string) {
	pubPath := filepath.Join(stateDir, identityPubFile)
	fpPath := filepath.Join(stateDir, identityFingerprintFile)
	if existing, err := os.ReadFile(pubPath); err != nil || strings.TrimSpace(string(existing)) != hex.EncodeToString(pub) {
		_ = atomicWrite(pubPath, []byte(hex.EncodeToString(pub)+"\n"), 0o644)
	}
	if existing, err := os.ReadFile(fpPath); err != nil || strings.TrimSpace(string(existing)) != fp {
		_ = atomicWrite(fpPath, []byte(fp+"\n"), 0o644)
	}
}

func loadCreatedAt(stateDir string) time.Time {
	info, err := os.Stat(filepath.Join(stateDir, identityKeyFile))
	if err != nil {
		return time.Now().UTC()
	}
	return info.ModTime().UTC()
}

// writeManifest is best-effort. /etc may be read-only under certain
// NixOS store configurations; we tolerate any write failure because
// the canonical identity material lives in stateDir.
func writeManifest(pub ed25519.PublicKey, fp string, createdAt time.Time) {
	m := Manifest{
		Version:     1,
		Algorithm:   "ed25519",
		Fingerprint: fp,
		PubkeyHex:   hex.EncodeToString(pub),
		CreatedAt:   createdAt,
	}
	data, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return
	}
	_ = atomicWrite(identityManifestFile, data, 0o644)
}

// writeAllFromReader is a small utility retained for future
// rotation paths: write everything from r to dest atomically.
//
// Unused today but referenced by the roadmap (Week 2+ rotation
// handler). Keeping the helper compiled-in guards against drift.
func writeAllFromReader(dest string, r io.Reader, perm os.FileMode) error {
	buf, err := io.ReadAll(r)
	if err != nil {
		return err
	}
	return atomicWrite(dest, buf, perm)
}
