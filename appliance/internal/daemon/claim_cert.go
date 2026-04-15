// claim_cert.go
//
// Week 2 of the composed identity stack — daemon side.
//
// Two responsibilities:
//
//   1. Read /etc/installer/claim.cert + verify it against the
//      embedded CA pubkey at /etc/installer/claim-ca.pub. Both files
//      ship in the ISO's initrd and never leave it; verification is
//      stateless and offline.
//
//   2. Build a CSR (Certificate Signing Request — really a JSON
//      payload) signed by the daemon's device identity. The CSR
//      embeds the verified claim cert so the backend can validate
//      both halves in one round trip.
//
// Canonical bytes for the CSR signature MUST match
// iso_ca_helpers.canonical_csr on the backend exactly. This file
// freezes that layout on the daemon side; the Python helper freezes
// it on the server side; iso_ca_helpers tests prove they agree.

package daemon

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"
)

// Default on-disk paths inside the running ISO. Both files ship in
// the initrd; the daemon never writes to /etc/installer/.
const (
	defaultClaimCertPath   = "/etc/installer/claim.cert"
	defaultClaimCAPubPath  = "/etc/installer/claim-ca.pub"
)

// ClaimCertPayload mirrors the JSON shape the mint script emits.
// It must serialize byte-identically (sort_keys, no spaces) to the
// Python json.dumps(..., sort_keys=True, separators=(',',':'))
// output — that's the canonical input both the cert sig and the
// daemon's CSR sig hash over.
type ClaimCertPayload struct {
	IsoReleaseSHA string `json:"iso_release_sha"`
	CAPubkeyHex   string `json:"ca_pubkey_hex"`
	IssuedAt      string `json:"issued_at"`
	ValidUntil    string `json:"valid_until"`
	Version       int    `json:"version"`
}

// ClaimCert is the JSON-doc loaded from /etc/installer/claim.cert.
type ClaimCert struct {
	Payload      ClaimCertPayload `json:"payload"`
	SignatureB64 string           `json:"signature_b64"`
	Algorithm    string           `json:"algorithm"`
}

// LoadClaimCert reads + verifies the claim cert against the bundled
// CA pubkey. Returns nil + nil when the cert files are absent (i.e.
// running on a non-claim-aware ISO or in dev) — callers treat that
// as "no claim flow available, fall back to legacy provisioning."
//
// Returns nil + error only on a present-but-broken cert (forged,
// expired, signature mismatch). Those failures are LOUD and refuse
// to attempt provisioning under bad credentials.
func LoadClaimCert() (*ClaimCert, error) {
	return LoadClaimCertFrom(defaultClaimCertPath, defaultClaimCAPubPath)
}

// LoadClaimCertFrom is the test-friendly variant — caller supplies
// explicit paths. Production callers should prefer LoadClaimCert.
func LoadClaimCertFrom(certPath, caPubPath string) (*ClaimCert, error) {
	certBytes, err := os.ReadFile(certPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil // legacy path / non-claim-aware ISO
		}
		return nil, fmt.Errorf("read claim cert: %w", err)
	}
	caPubBytes, err := os.ReadFile(caPubPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, errors.New("claim cert present but CA pubkey missing — corrupt ISO")
		}
		return nil, fmt.Errorf("read claim CA pubkey: %w", err)
	}

	var cert ClaimCert
	if err := json.Unmarshal(certBytes, &cert); err != nil {
		return nil, fmt.Errorf("parse claim cert json: %w", err)
	}

	// Sanity: cert.payload.ca_pubkey_hex must match the bundled
	// pubkey. This catches drift between the cert and the CA the
	// ISO was built with.
	expectedCAPub := strings.TrimSpace(string(caPubBytes))
	if cert.Payload.CAPubkeyHex != expectedCAPub {
		return nil, fmt.Errorf("claim cert ca_pubkey_hex %s != bundled CA pubkey %s",
			cert.Payload.CAPubkeyHex[:16]+"...", expectedCAPub[:16]+"...")
	}

	// Validity window check — cert may be expired.
	now := time.Now().UTC()
	validUntil, err := time.Parse(time.RFC3339, cert.Payload.ValidUntil)
	if err != nil {
		return nil, fmt.Errorf("parse claim cert valid_until: %w", err)
	}
	if now.After(validUntil) {
		return nil, fmt.Errorf("claim cert expired at %s (now %s)",
			validUntil.Format(time.RFC3339), now.Format(time.RFC3339))
	}

	// Verify the signature against the bundled CA pubkey.
	caPub, err := hex.DecodeString(expectedCAPub)
	if err != nil {
		return nil, fmt.Errorf("decode bundled CA pubkey: %w", err)
	}
	if len(caPub) != ed25519.PublicKeySize {
		return nil, fmt.Errorf("bundled CA pubkey size %d != %d",
			len(caPub), ed25519.PublicKeySize)
	}

	canonical := canonicalCertJSON(cert.Payload)
	sig, err := decodeB64URLPadless(cert.SignatureB64)
	if err != nil {
		return nil, fmt.Errorf("decode cert sig: %w", err)
	}
	if !ed25519.Verify(caPub, canonical, sig) {
		return nil, errors.New("claim cert signature verification failed")
	}

	return &cert, nil
}

// canonicalCertJSON serializes the cert payload byte-identically to
// the Python json.dumps(sort_keys=True, separators=(',',':')) output.
// Go's encoding/json sorts map keys but does NOT sort struct fields
// in declaration order — so we hand-build the dict as a sorted-key
// map.
func canonicalCertJSON(p ClaimCertPayload) []byte {
	// Use a string→json.RawMessage map for deterministic ordering.
	m := map[string]any{
		"iso_release_sha": p.IsoReleaseSHA,
		"ca_pubkey_hex":   p.CAPubkeyHex,
		"issued_at":       p.IssuedAt,
		"valid_until":     p.ValidUntil,
		"version":         p.Version,
	}
	out, _ := json.Marshal(m) // map keys auto-sorted by encoding/json
	return out
}

// CSRRequest is the JSON body POSTed to /api/provision/claim-v2.
type CSRRequest struct {
	SiteID          string    `json:"site_id"`
	MACAddress      string    `json:"mac_address"`
	AgentPubkeyHex  string    `json:"agent_pubkey_hex"`
	HardwareID      string    `json:"hardware_id,omitempty"`
	Nonce           string    `json:"nonce"`
	Timestamp       string    `json:"timestamp"`
	ClaimCert       ClaimCert `json:"claim_cert"`
	CSRSignatureB64 string    `json:"csr_signature_b64"`
}

// BuildCSR constructs a CSRRequest signed by the daemon's identity.
// The signature binds the request to BOTH the agent pubkey AND the
// embedded claim cert — an attacker who steals one cannot replay it
// under a different pubkey or on a different cert.
func BuildCSR(
	id *Identity,
	cert *ClaimCert,
	siteID, macAddress, hardwareID string,
) (*CSRRequest, error) {
	if id == nil {
		return nil, errors.New("identity is nil")
	}
	if cert == nil {
		return nil, errors.New("claim cert is nil — no provisioning credentials")
	}
	nonceBytes := make([]byte, 16)
	if _, err := rand.Read(nonceBytes); err != nil {
		return nil, fmt.Errorf("nonce: %w", err)
	}
	nonceHex := hex.EncodeToString(nonceBytes)
	timestamp := time.Now().UTC().Format("2006-01-02T15:04:05Z")

	canonical := canonicalCSRBytes(
		siteID,
		strings.ToUpper(macAddress),
		strings.ToLower(id.PublicKeyHex()),
		hardwareID,
		nonceHex,
		timestamp,
		cert.Payload,
	)
	sig := id.Sign(canonical)

	return &CSRRequest{
		SiteID:          siteID,
		MACAddress:      strings.ToUpper(macAddress),
		AgentPubkeyHex:  strings.ToLower(id.PublicKeyHex()),
		HardwareID:      hardwareID,
		Nonce:           nonceHex,
		Timestamp:       timestamp,
		ClaimCert:       *cert,
		CSRSignatureB64: base64.RawURLEncoding.EncodeToString(sig),
	}, nil
}

// canonicalCSRBytes builds the canonical signing input for a CSR.
// This MUST match iso_ca_helpers.canonical_csr on the backend
// byte-for-byte. Tests in iso_ca and iso_ca_helpers prove the byte
// layout via the documented protocol.
func canonicalCSRBytes(
	siteID, mac, agentPubkeyHex, hardwareID, nonce, timestamp string,
	cert ClaimCertPayload,
) []byte {
	parts := []string{
		siteID,
		mac, // already uppercased by caller
		agentPubkeyHex,
		hardwareID,
		nonce,
		timestamp,
		string(canonicalCertJSON(cert)),
	}
	return []byte(strings.Join(parts, "\n"))
}

func decodeB64URLPadless(s string) ([]byte, error) {
	pad := (4 - len(s)%4) % 4
	return base64.URLEncoding.DecodeString(s + strings.Repeat("=", pad))
}
