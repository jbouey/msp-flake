// Package ca manages a certificate authority for agent mTLS enrollment.
//
// Certificate lifecycle:
// 1. Appliance boots -> CA cert generated (or loaded from disk)
// 2. Go agent registers (insecure first time) -> receives CA cert + signed client cert
// 3. Agent reconnects with mTLS -> all subsequent communication encrypted
//
// HIPAA: 164.312(e)(1) - Transmission security
package ca

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"time"
)

// AgentCA manages a CA keypair and issues per-agent TLS certificates.
type AgentCA struct {
	Dir     string
	caCert  *x509.Certificate
	caKey   *ecdsa.PrivateKey
}

// New creates an AgentCA with the given directory.
func New(dir string) *AgentCA {
	return &AgentCA{Dir: dir}
}

func (ca *AgentCA) caCertPath() string  { return filepath.Join(ca.Dir, "ca.crt") }
func (ca *AgentCA) caKeyPath() string   { return filepath.Join(ca.Dir, "ca.key") }
func (ca *AgentCA) serverCertPath() string { return filepath.Join(ca.Dir, "server.crt") }
func (ca *AgentCA) serverKeyPath() string  { return filepath.Join(ca.Dir, "server.key") }

// EnsureCA generates a CA cert/key if not present, or loads existing.
func (ca *AgentCA) EnsureCA() error {
	if err := os.MkdirAll(ca.Dir, 0o755); err != nil {
		return fmt.Errorf("create CA dir: %w", err)
	}

	// Try loading existing
	if ca.loadExisting() == nil {
		return nil
	}

	// Generate new CA
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return fmt.Errorf("generate CA key: %w", err)
	}

	serial, err := randomSerial()
	if err != nil {
		return err
	}

	now := time.Now().UTC()
	template := &x509.Certificate{
		SerialNumber: serial,
		Subject: pkix.Name{
			Organization: []string{"OsirisCare"},
			CommonName:   "OsirisCare Appliance CA",
		},
		NotBefore:             now,
		NotAfter:              now.Add(10 * 365 * 24 * time.Hour), // 10 years
		IsCA:                  true,
		MaxPathLen:            0,
		MaxPathLenZero:        true,
		BasicConstraintsValid: true,
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
	}

	certDER, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		return fmt.Errorf("create CA cert: %w", err)
	}

	cert, err := x509.ParseCertificate(certDER)
	if err != nil {
		return fmt.Errorf("parse CA cert: %w", err)
	}

	// Write key
	keyBytes, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		return fmt.Errorf("marshal CA key: %w", err)
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyBytes})
	if err := os.WriteFile(ca.caKeyPath(), keyPEM, 0o600); err != nil {
		return fmt.Errorf("write CA key: %w", err)
	}

	// Write cert
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certDER})
	if err := os.WriteFile(ca.caCertPath(), certPEM, 0o644); err != nil {
		return fmt.Errorf("write CA cert: %w", err)
	}

	ca.caCert = cert
	ca.caKey = key
	return nil
}

// loadExisting loads CA cert and key from disk.
func (ca *AgentCA) loadExisting() error {
	certPEM, err := os.ReadFile(ca.caCertPath())
	if err != nil {
		return err
	}
	keyPEM, err := os.ReadFile(ca.caKeyPath())
	if err != nil {
		return err
	}

	certBlock, _ := pem.Decode(certPEM)
	if certBlock == nil {
		return fmt.Errorf("no PEM block in CA cert")
	}
	cert, err := x509.ParseCertificate(certBlock.Bytes)
	if err != nil {
		return fmt.Errorf("parse CA cert: %w", err)
	}

	keyBlock, _ := pem.Decode(keyPEM)
	if keyBlock == nil {
		return fmt.Errorf("no PEM block in CA key")
	}
	key, err := x509.ParseECPrivateKey(keyBlock.Bytes)
	if err != nil {
		return fmt.Errorf("parse CA key: %w", err)
	}

	ca.caCert = cert
	ca.caKey = key
	return nil
}

// IssueAgentCert issues a client certificate for a Go agent.
// Returns (cert_pem, key_pem, ca_cert_pem).
func (ca *AgentCA) IssueAgentCert(hostname, agentID string) (certPEM, keyPEM, caPEM []byte, err error) {
	if ca.caCert == nil || ca.caKey == nil {
		return nil, nil, nil, fmt.Errorf("CA not initialized — call EnsureCA() first")
	}

	agentKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("generate agent key: %w", err)
	}

	serial, err := randomSerial()
	if err != nil {
		return nil, nil, nil, err
	}

	now := time.Now().UTC()
	template := &x509.Certificate{
		SerialNumber: serial,
		Subject: pkix.Name{
			Organization: []string{"OsirisCare"},
			CommonName:   fmt.Sprintf("agent-%s", hostname),
		},
		NotBefore:   now,
		NotAfter:    now.Add(365 * 24 * time.Hour), // 1 year
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
		DNSNames:    []string{hostname},
	}

	certDER, err := x509.CreateCertificate(rand.Reader, template, ca.caCert, &agentKey.PublicKey, ca.caKey)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("sign agent cert: %w", err)
	}

	certPEM = pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certDER})
	keyBytes, err := x509.MarshalECPrivateKey(agentKey)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("marshal agent key: %w", err)
	}
	keyPEM = pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyBytes})
	caPEM = pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: ca.caCert.Raw})

	return certPEM, keyPEM, caPEM, nil
}

// GenerateServerCert generates a server certificate for the gRPC server.
// If an existing cert is valid for >30 days, returns it instead.
// Returns (cert_pem, key_pem).
func (ca *AgentCA) GenerateServerCert(applianceIP string) (certPEM, keyPEM []byte, err error) {
	if ca.caCert == nil || ca.caKey == nil {
		return nil, nil, fmt.Errorf("CA not initialized — call EnsureCA() first")
	}

	// Check existing server cert
	if existingCert, existingKey, ok := ca.loadExistingServerCert(); ok {
		return existingCert, existingKey, nil
	}

	serverKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, nil, fmt.Errorf("generate server key: %w", err)
	}

	serial, err := randomSerial()
	if err != nil {
		return nil, nil, err
	}

	ip := net.ParseIP(applianceIP)
	now := time.Now().UTC()
	template := &x509.Certificate{
		SerialNumber: serial,
		Subject: pkix.Name{
			Organization: []string{"OsirisCare"},
			CommonName:   "OsirisCare Appliance",
		},
		NotBefore:   now,
		NotAfter:    now.Add(365 * 24 * time.Hour),
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		IPAddresses: []net.IP{ip},
	}

	certDER, err := x509.CreateCertificate(rand.Reader, template, ca.caCert, &serverKey.PublicKey, ca.caKey)
	if err != nil {
		return nil, nil, fmt.Errorf("sign server cert: %w", err)
	}

	certPEM = pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certDER})
	keyBytes, err := x509.MarshalECPrivateKey(serverKey)
	if err != nil {
		return nil, nil, fmt.Errorf("marshal server key: %w", err)
	}
	keyPEM = pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyBytes})

	// Cache to disk
	_ = os.WriteFile(ca.serverCertPath(), certPEM, 0o644)
	_ = os.WriteFile(ca.serverKeyPath(), keyPEM, 0o600)

	return certPEM, keyPEM, nil
}

// CACertPEM returns the CA certificate as PEM bytes.
func (ca *AgentCA) CACertPEM() ([]byte, error) {
	if ca.caCert == nil {
		return nil, fmt.Errorf("CA not initialized")
	}
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: ca.caCert.Raw}), nil
}

func (ca *AgentCA) loadExistingServerCert() (certPEM, keyPEM []byte, ok bool) {
	certData, err := os.ReadFile(ca.serverCertPath())
	if err != nil {
		return nil, nil, false
	}
	keyData, err := os.ReadFile(ca.serverKeyPath())
	if err != nil {
		return nil, nil, false
	}

	block, _ := pem.Decode(certData)
	if block == nil {
		return nil, nil, false
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return nil, nil, false
	}

	remaining := time.Until(cert.NotAfter)
	if remaining > 30*24*time.Hour {
		return certData, keyData, true
	}
	return nil, nil, false
}

func randomSerial() (*big.Int, error) {
	serialLimit := new(big.Int).Lsh(big.NewInt(1), 128)
	serial, err := rand.Int(rand.Reader, serialLimit)
	if err != nil {
		return nil, fmt.Errorf("generate serial: %w", err)
	}
	return serial, nil
}
