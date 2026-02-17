"""
Agent Certificate Authority.

Generates a self-signed CA certificate on first run, then issues
per-agent client certificates during registration.

Certificate lifecycle:
1. Appliance boots -> CA cert generated (or loaded from disk)
2. Go agent registers (insecure first time) -> receives CA cert + signed client cert
3. Agent reconnects with mTLS -> all subsequent communication encrypted

HIPAA: 164.312(e)(1) - Transmission security
"""

import ipaddress
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

logger = logging.getLogger(__name__)

DEFAULT_CA_DIR = Path("/var/lib/msp/ca")


class AgentCA:
    """Manages CA keypair and issues per-agent TLS certificates."""

    def __init__(self, ca_dir: Path = DEFAULT_CA_DIR):
        self.ca_dir = ca_dir
        self.ca_cert = None
        self.ca_key = None

    @property
    def ca_cert_path(self) -> Path:
        return self.ca_dir / "ca.crt"

    @property
    def ca_key_path(self) -> Path:
        return self.ca_dir / "ca.key"

    @property
    def server_cert_path(self) -> Path:
        return self.ca_dir / "server.crt"

    @property
    def server_key_path(self) -> Path:
        return self.ca_dir / "server.key"

    def ensure_ca(self) -> None:
        """Generate CA cert/key if not present, or load existing."""
        self.ca_dir.mkdir(parents=True, exist_ok=True)

        if self.ca_cert_path.exists() and self.ca_key_path.exists():
            self.ca_key = serialization.load_pem_private_key(
                self.ca_key_path.read_bytes(), password=None
            )
            self.ca_cert = x509.load_pem_x509_certificate(
                self.ca_cert_path.read_bytes()
            )
            logger.info("Loaded existing CA certificate (expires %s)", self.ca_cert.not_valid_after_utc)
            return

        logger.info("Generating new CA certificate...")
        self.ca_key = ec.generate_private_key(ec.SECP256R1())

        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "OsirisCare"),
            x509.NameAttribute(NameOID.COMMON_NAME, "OsirisCare Appliance CA"),
        ])

        self.ca_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(self.ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3650))
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=0), critical=True
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_cert_sign=True,
                    crl_sign=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(self.ca_key, hashes.SHA256())
        )

        self.ca_key_path.write_bytes(
            self.ca_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        self.ca_key_path.chmod(0o600)

        self.ca_cert_path.write_bytes(
            self.ca_cert.public_bytes(serialization.Encoding.PEM)
        )

        logger.info("Generated new CA certificate (10 year validity)")

    def issue_agent_cert(
        self, hostname: str, agent_id: str
    ) -> tuple[bytes, bytes, bytes]:
        """
        Issue a client certificate for a Go agent.

        Returns: (cert_pem, key_pem, ca_cert_pem)
        """
        if self.ca_cert is None or self.ca_key is None:
            raise RuntimeError("CA not initialized — call ensure_ca() first")

        agent_key = ec.generate_private_key(ec.SECP256R1())

        subject = x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "OsirisCare"),
            x509.NameAttribute(NameOID.COMMON_NAME, f"agent-{hostname}"),
        ])

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self.ca_cert.subject)
            .public_key(agent_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
                critical=False,
            )
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(hostname)]),
                critical=False,
            )
            .sign(self.ca_key, hashes.SHA256())
        )

        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = agent_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        ca_pem = self.ca_cert.public_bytes(serialization.Encoding.PEM)

        logger.info(
            "Issued agent certificate for %s (id=%s, expires %s)",
            hostname,
            agent_id,
            cert.not_valid_after_utc,
        )

        return cert_pem, key_pem, ca_pem

    def generate_server_cert(self, appliance_ip: str) -> tuple[bytes, bytes]:
        """
        Generate server certificate for the gRPC server (appliance side).

        Returns: (cert_pem, key_pem)
        """
        if self.ca_cert is None or self.ca_key is None:
            raise RuntimeError("CA not initialized — call ensure_ca() first")

        # Check if server cert already exists and is still valid
        if self.server_cert_path.exists() and self.server_key_path.exists():
            existing = x509.load_pem_x509_certificate(
                self.server_cert_path.read_bytes()
            )
            remaining = existing.not_valid_after_utc - datetime.now(timezone.utc)
            if remaining.days > 30:
                logger.info("Existing server certificate valid for %d more days", remaining.days)
                return (
                    self.server_cert_path.read_bytes(),
                    self.server_key_path.read_bytes(),
                )

        server_key = ec.generate_private_key(ec.SECP256R1())

        san_list = [x509.IPAddress(ipaddress.ip_address(appliance_ip))]

        subject = x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "OsirisCare"),
            x509.NameAttribute(NameOID.COMMON_NAME, "OsirisCare Appliance"),
        ])

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self.ca_cert.subject)
            .public_key(server_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .add_extension(
                x509.SubjectAlternativeName(san_list), critical=False
            )
            .sign(self.ca_key, hashes.SHA256())
        )

        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

        # Cache to disk
        self.server_cert_path.write_bytes(cert_pem)
        self.server_key_path.write_bytes(key_pem)
        self.server_key_path.chmod(0o600)

        logger.info("Generated server certificate for %s", appliance_ip)

        return cert_pem, key_pem

    @property
    def ca_cert_pem(self) -> bytes:
        """Return CA certificate as PEM bytes."""
        if self.ca_cert is None:
            raise RuntimeError("CA not initialized")
        return self.ca_cert.public_bytes(serialization.Encoding.PEM)
