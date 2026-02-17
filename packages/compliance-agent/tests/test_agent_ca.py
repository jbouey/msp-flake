"""Tests for Agent Certificate Authority (agent_ca.py).

Tests CA generation, loading, agent cert issuance, and server cert generation
using real cryptography (no mocking needed — uses tmp_path).
"""

import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

from compliance_agent.agent_ca import AgentCA


class TestAgentCAGeneration:
    """Test CA certificate generation and loading."""

    def test_ensure_ca_creates_new_ca(self, tmp_path):
        """First call to ensure_ca should generate CA cert + key."""
        ca = AgentCA(ca_dir=tmp_path)
        ca.ensure_ca()

        assert ca.ca_cert is not None
        assert ca.ca_key is not None
        assert (tmp_path / "ca.crt").exists()
        assert (tmp_path / "ca.key").exists()

        # Verify CA properties
        assert ca.ca_cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "OsirisCare Appliance CA"
        # CA should be valid for 10 years
        remaining = ca.ca_cert.not_valid_after_utc - datetime.now(timezone.utc)
        assert remaining.days > 3640

        # BasicConstraints should mark as CA
        bc = ca.ca_cert.extensions.get_extension_for_class(x509.BasicConstraints)
        assert bc.value.ca is True
        assert bc.critical is True

    def test_ensure_ca_loads_existing(self, tmp_path):
        """Second call to ensure_ca should load from disk, not regenerate."""
        ca1 = AgentCA(ca_dir=tmp_path)
        ca1.ensure_ca()
        serial1 = ca1.ca_cert.serial_number

        # Create new instance, load from same directory
        ca2 = AgentCA(ca_dir=tmp_path)
        ca2.ensure_ca()

        assert ca2.ca_cert.serial_number == serial1

    def test_ensure_ca_key_permissions(self, tmp_path):
        """CA key file should be mode 0600."""
        ca = AgentCA(ca_dir=tmp_path)
        ca.ensure_ca()

        key_stat = (tmp_path / "ca.key").stat()
        assert oct(key_stat.st_mode & 0o777) == "0o600"

    def test_ca_cert_pem_property(self, tmp_path):
        """ca_cert_pem property should return PEM bytes."""
        ca = AgentCA(ca_dir=tmp_path)
        ca.ensure_ca()

        pem = ca.ca_cert_pem
        assert pem.startswith(b"-----BEGIN CERTIFICATE-----")
        # Should round-trip
        loaded = x509.load_pem_x509_certificate(pem)
        assert loaded.serial_number == ca.ca_cert.serial_number

    def test_ca_cert_pem_raises_uninitialized(self, tmp_path):
        """ca_cert_pem should raise if CA not initialized."""
        ca = AgentCA(ca_dir=tmp_path)
        with pytest.raises(RuntimeError, match="CA not initialized"):
            _ = ca.ca_cert_pem


class TestAgentCertIssuance:
    """Test per-agent client certificate issuance."""

    def test_issue_agent_cert(self, tmp_path):
        """Issue a client cert for a Go agent."""
        ca = AgentCA(ca_dir=tmp_path)
        ca.ensure_ca()

        cert_pem, key_pem, ca_pem = ca.issue_agent_cert("WORKSTATION01", "agent-abc123")

        # Verify all are PEM
        assert cert_pem.startswith(b"-----BEGIN CERTIFICATE-----")
        assert key_pem.startswith(b"-----BEGIN PRIVATE KEY-----")
        assert ca_pem.startswith(b"-----BEGIN CERTIFICATE-----")

        # Parse and verify cert properties
        cert = x509.load_pem_x509_certificate(cert_pem)
        assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "agent-WORKSTATION01"

        # Should be signed by our CA
        assert cert.issuer == ca.ca_cert.subject

        # Should have clientAuth EKU
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        assert ExtendedKeyUsageOID.CLIENT_AUTH in eku.value

        # Should have SAN with hostname
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        assert "WORKSTATION01" in san.value.get_values_for_type(x509.DNSName)

        # Validity: ~1 year
        remaining = cert.not_valid_after_utc - datetime.now(timezone.utc)
        assert 360 < remaining.days <= 366

    def test_issue_agent_cert_uninitialized(self, tmp_path):
        """Should raise RuntimeError if CA not initialized."""
        ca = AgentCA(ca_dir=tmp_path)
        with pytest.raises(RuntimeError, match="CA not initialized"):
            ca.issue_agent_cert("HOST", "agent-1")

    def test_issue_multiple_certs_unique(self, tmp_path):
        """Each issued cert should have a unique serial number."""
        ca = AgentCA(ca_dir=tmp_path)
        ca.ensure_ca()

        cert1_pem, _, _ = ca.issue_agent_cert("HOST1", "agent-1")
        cert2_pem, _, _ = ca.issue_agent_cert("HOST2", "agent-2")

        cert1 = x509.load_pem_x509_certificate(cert1_pem)
        cert2 = x509.load_pem_x509_certificate(cert2_pem)

        assert cert1.serial_number != cert2.serial_number


class TestServerCert:
    """Test gRPC server certificate generation."""

    def test_generate_server_cert(self, tmp_path):
        """Generate server cert with IP SAN."""
        ca = AgentCA(ca_dir=tmp_path)
        ca.ensure_ca()

        cert_pem, key_pem = ca.generate_server_cert("192.168.88.241")

        cert = x509.load_pem_x509_certificate(cert_pem)
        assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "OsirisCare Appliance"

        # Should have serverAuth EKU
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        assert ExtendedKeyUsageOID.SERVER_AUTH in eku.value

        # Should be cached to disk
        assert (tmp_path / "server.crt").exists()
        assert (tmp_path / "server.key").exists()

    def test_server_cert_caching(self, tmp_path):
        """Second call should return cached cert if still valid."""
        ca = AgentCA(ca_dir=tmp_path)
        ca.ensure_ca()

        cert_pem1, _ = ca.generate_server_cert("192.168.88.241")
        cert_pem2, _ = ca.generate_server_cert("192.168.88.241")

        cert1 = x509.load_pem_x509_certificate(cert_pem1)
        cert2 = x509.load_pem_x509_certificate(cert_pem2)
        # Same cert (cached)
        assert cert1.serial_number == cert2.serial_number

    def test_generate_server_cert_uninitialized(self, tmp_path):
        """Should raise RuntimeError if CA not initialized."""
        ca = AgentCA(ca_dir=tmp_path)
        with pytest.raises(RuntimeError, match="CA not initialized"):
            ca.generate_server_cert("10.0.0.1")
