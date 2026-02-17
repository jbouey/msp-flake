package ca

import (
	"crypto/x509"
	"encoding/pem"
	"os"
	"path/filepath"
	"testing"
)

func TestEnsureCACreateNew(t *testing.T) {
	dir := t.TempDir()
	c := New(dir)

	if err := c.EnsureCA(); err != nil {
		t.Fatalf("EnsureCA: %v", err)
	}

	// Verify files exist
	if _, err := os.Stat(filepath.Join(dir, "ca.crt")); err != nil {
		t.Fatalf("ca.crt not created: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, "ca.key")); err != nil {
		t.Fatalf("ca.key not created: %v", err)
	}

	// Verify key permissions
	info, _ := os.Stat(filepath.Join(dir, "ca.key"))
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("ca.key permissions: got %o, want 0600", info.Mode().Perm())
	}

	// Verify cert is valid
	if c.caCert == nil {
		t.Fatal("caCert should not be nil")
	}
	if !c.caCert.IsCA {
		t.Fatal("cert should be a CA")
	}
	if c.caCert.Subject.CommonName != "OsirisCare Appliance CA" {
		t.Fatalf("unexpected CN: %s", c.caCert.Subject.CommonName)
	}
}

func TestEnsureCALoadExisting(t *testing.T) {
	dir := t.TempDir()
	c := New(dir)

	// Create CA
	if err := c.EnsureCA(); err != nil {
		t.Fatalf("EnsureCA create: %v", err)
	}
	serial1 := c.caCert.SerialNumber

	// Load existing
	c2 := New(dir)
	if err := c2.EnsureCA(); err != nil {
		t.Fatalf("EnsureCA load: %v", err)
	}

	// Should be the same cert
	if c2.caCert.SerialNumber.Cmp(serial1) != 0 {
		t.Fatal("loaded cert should have same serial as created cert")
	}
}

func TestIssueAgentCert(t *testing.T) {
	dir := t.TempDir()
	c := New(dir)
	if err := c.EnsureCA(); err != nil {
		t.Fatalf("EnsureCA: %v", err)
	}

	certPEM, keyPEM, caPEM, err := c.IssueAgentCert("NVWS01", "go-NVWS01-abc")
	if err != nil {
		t.Fatalf("IssueAgentCert: %v", err)
	}

	if len(certPEM) == 0 || len(keyPEM) == 0 || len(caPEM) == 0 {
		t.Fatal("cert, key, and CA PEM should not be empty")
	}

	// Parse and verify cert
	block, _ := pem.Decode(certPEM)
	if block == nil {
		t.Fatal("failed to decode cert PEM")
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		t.Fatalf("parse cert: %v", err)
	}

	if cert.Subject.CommonName != "agent-NVWS01" {
		t.Fatalf("unexpected CN: %s", cert.Subject.CommonName)
	}
	if len(cert.DNSNames) != 1 || cert.DNSNames[0] != "NVWS01" {
		t.Fatalf("unexpected SAN: %v", cert.DNSNames)
	}
	if len(cert.ExtKeyUsage) != 1 || cert.ExtKeyUsage[0] != x509.ExtKeyUsageClientAuth {
		t.Fatal("cert should have ClientAuth EKU")
	}

	// Verify cert is signed by CA
	roots := x509.NewCertPool()
	roots.AddCert(c.caCert)
	opts := x509.VerifyOptions{
		Roots:     roots,
		KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}
	if _, err := cert.Verify(opts); err != nil {
		t.Fatalf("cert verification failed: %v", err)
	}
}

func TestIssueAgentCertUninitializedCA(t *testing.T) {
	c := New(t.TempDir())
	// Don't call EnsureCA

	_, _, _, err := c.IssueAgentCert("WS01", "go-WS01-abc")
	if err == nil {
		t.Fatal("expected error for uninitialized CA")
	}
}

func TestGenerateServerCert(t *testing.T) {
	dir := t.TempDir()
	c := New(dir)
	if err := c.EnsureCA(); err != nil {
		t.Fatalf("EnsureCA: %v", err)
	}

	certPEM, keyPEM, err := c.GenerateServerCert("192.168.88.241")
	if err != nil {
		t.Fatalf("GenerateServerCert: %v", err)
	}

	if len(certPEM) == 0 || len(keyPEM) == 0 {
		t.Fatal("cert and key PEM should not be empty")
	}

	// Parse and verify
	block, _ := pem.Decode(certPEM)
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		t.Fatalf("parse cert: %v", err)
	}

	if len(cert.IPAddresses) != 1 || cert.IPAddresses[0].String() != "192.168.88.241" {
		t.Fatalf("unexpected IP SAN: %v", cert.IPAddresses)
	}
	if len(cert.ExtKeyUsage) != 1 || cert.ExtKeyUsage[0] != x509.ExtKeyUsageServerAuth {
		t.Fatal("cert should have ServerAuth EKU")
	}

	// Should cache to disk
	if _, err := os.Stat(filepath.Join(dir, "server.crt")); err != nil {
		t.Fatal("server.crt not cached to disk")
	}

	// Second call should return cached
	certPEM2, _, err := c.GenerateServerCert("192.168.88.241")
	if err != nil {
		t.Fatalf("GenerateServerCert cached: %v", err)
	}
	if string(certPEM2) != string(certPEM) {
		t.Fatal("second call should return cached cert")
	}
}

func TestCACertPEM(t *testing.T) {
	dir := t.TempDir()
	c := New(dir)
	if err := c.EnsureCA(); err != nil {
		t.Fatalf("EnsureCA: %v", err)
	}

	pem, err := c.CACertPEM()
	if err != nil {
		t.Fatalf("CACertPEM: %v", err)
	}
	if len(pem) == 0 {
		t.Fatal("CA cert PEM should not be empty")
	}
}

func TestCACertPEMUninitialized(t *testing.T) {
	c := New(t.TempDir())
	_, err := c.CACertPEM()
	if err == nil {
		t.Fatal("expected error for uninitialized CA")
	}
}
