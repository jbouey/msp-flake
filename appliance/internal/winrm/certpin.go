// Package winrm — certpin.go implements TOFU (Trust On First Use) certificate
// fingerprint pinning for WinRM TLS connections. On the first successful
// connection to a host, the server's leaf certificate SHA-256 fingerprint is
// stored. On subsequent connections the presented cert must match the stored
// fingerprint or the connection is rejected (possible MITM). Pins are persisted
// to disk at /var/lib/msp/winrm_pins.json.
package winrm

import (
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"sync"
	"time"
)

// DefaultPinStorePath is the default filesystem path for persisted WinRM cert pins.
const DefaultPinStorePath = "/var/lib/msp/winrm_pins.json"

// CertPin stores a single host's certificate fingerprint with metadata.
type CertPin struct {
	Fingerprint string `json:"fingerprint"` // SHA-256 hex of leaf cert DER
	FirstSeen   string `json:"first_seen"`  // RFC3339 timestamp of TOFU
	LastSeen    string `json:"last_seen"`    // RFC3339 timestamp of last successful verify
}

// CertPinStore implements TOFU certificate pinning. It is safe for concurrent
// use from multiple goroutines.
type CertPinStore struct {
	mu   sync.RWMutex
	pins map[string]CertPin // hostname/IP -> pin
	path string
}

// NewCertPinStore creates a new CertPinStore that persists to the given path.
// If the file exists, previously stored pins are loaded.
func NewCertPinStore(path string) *CertPinStore {
	store := &CertPinStore{
		pins: make(map[string]CertPin),
		path: path,
	}
	store.load()
	return store
}

// load reads persisted pins from disk. Errors are silently ignored (first run
// or corrupt file results in empty store — TOFU will re-pin on next connect).
func (s *CertPinStore) load() {
	data, err := os.ReadFile(s.path)
	if err != nil {
		return
	}
	var pins map[string]CertPin
	if err := json.Unmarshal(data, &pins); err != nil {
		log.Printf("[winrm-tls] WARNING: failed to parse pin store %s: %v (starting fresh)", s.path, err)
		return
	}
	s.pins = pins
	log.Printf("[winrm-tls] Loaded %d certificate pins from %s", len(pins), s.path)
}

// save writes the current pin set to disk atomically (write tmp + rename).
func (s *CertPinStore) save() {
	s.mu.RLock()
	data, err := json.MarshalIndent(s.pins, "", "  ")
	s.mu.RUnlock()
	if err != nil {
		log.Printf("[winrm-tls] WARNING: failed to marshal pins: %v", err)
		return
	}

	tmp := s.path + ".tmp"
	if err := os.WriteFile(tmp, data, 0600); err != nil {
		log.Printf("[winrm-tls] WARNING: failed to write pin store %s: %v", tmp, err)
		return
	}
	if err := os.Rename(tmp, s.path); err != nil {
		log.Printf("[winrm-tls] WARNING: failed to rename pin store %s -> %s: %v", tmp, s.path, err)
	}
}

// GetPin returns the stored fingerprint for a host, or false if none exists.
func (s *CertPinStore) GetPin(host string) (CertPin, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	pin, ok := s.pins[host]
	return pin, ok
}

// SetPin stores (or updates) the fingerprint for a host and persists to disk.
func (s *CertPinStore) SetPin(host, fingerprint string) {
	now := time.Now().UTC().Format(time.RFC3339)
	s.mu.Lock()
	s.pins[host] = CertPin{
		Fingerprint: fingerprint,
		FirstSeen:   now,
		LastSeen:    now,
	}
	s.mu.Unlock()
	s.save()
}

// TouchPin updates the LastSeen timestamp for an existing pin.
func (s *CertPinStore) TouchPin(host string) {
	now := time.Now().UTC().Format(time.RFC3339)
	s.mu.Lock()
	if pin, ok := s.pins[host]; ok {
		pin.LastSeen = now
		s.pins[host] = pin
	}
	s.mu.Unlock()
	// Persist periodically via save() is optional here; we skip it to avoid
	// disk I/O on every single connection. The next SetPin or ClearPin will
	// persist the updated timestamp.
}

// ClearPin removes the stored pin for a host (e.g., after legitimate cert
// rotation). The next connection will re-pin via TOFU.
func (s *CertPinStore) ClearPin(host string) {
	s.mu.Lock()
	delete(s.pins, host)
	s.mu.Unlock()
	s.save()
	log.Printf("[winrm-tls] Cleared certificate pin for %s (next connect will re-pin)", host)
}

// PinCount returns the number of stored pins.
func (s *CertPinStore) PinCount() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.pins)
}

// TLSConfigForHost returns a *tls.Config that implements TOFU cert pinning for
// a specific host. InsecureSkipVerify is set to true because the gowinrm library
// does not support custom CA verification — instead we use VerifyPeerCertificate
// to enforce our own fingerprint-based trust model:
//
//   - First connection (no stored pin): accept the cert, store its SHA-256
//     fingerprint. This is the "Trust On First Use" step.
//   - Subsequent connections: compare the presented cert's fingerprint against
//     the stored pin. Mismatch -> reject with error (possible MITM attack).
func (s *CertPinStore) TLSConfigForHost(host string) *tls.Config {
	return &tls.Config{
		InsecureSkipVerify: true, //nolint:gosec // We do our own verification via VerifyPeerCertificate
		VerifyPeerCertificate: func(rawCerts [][]byte, _ [][]*x509.Certificate) error {
			if len(rawCerts) == 0 {
				return fmt.Errorf("winrm-tls: no certificate presented by %s", host)
			}

			// Compute SHA-256 fingerprint of the leaf certificate
			fingerprint := SHA256Hex(rawCerts[0])

			existingPin, hasPin := s.GetPin(host)
			if !hasPin {
				// TOFU: first connection — trust and store
				log.Printf("[winrm-tls] TOFU: pinning cert for %s (fingerprint: %s...)", host, fingerprint[:16])
				s.SetPin(host, fingerprint)
				return nil
			}

			// Verify against stored pin
			if fingerprint != existingPin.Fingerprint {
				log.Printf("[winrm-tls] SECURITY: cert fingerprint mismatch for %s! stored=%s... presented=%s...",
					host, existingPin.Fingerprint[:16], fingerprint[:16])
				return fmt.Errorf("winrm-tls: certificate fingerprint mismatch for %s (possible MITM) — stored %s, got %s",
					host, existingPin.Fingerprint[:16], fingerprint[:16])
			}

			// Pin matches — update last-seen timestamp
			s.TouchPin(host)
			return nil
		},
	}
}

// SHA256Hex computes the SHA-256 hash of data and returns it as a lowercase hex string.
func SHA256Hex(data []byte) string {
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:])
}
