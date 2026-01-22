"""
Cryptographic operations for compliance agent.

Handles Ed25519 signing and verification for:
- Evidence bundles (local signing)
- MCP orders (remote verification)
"""

import json
from pathlib import Path
from typing import Union, Dict, Any
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature
import hashlib


class KeyIntegrityError(Exception):
    """Raised when private key integrity check fails (possible tampering)."""
    pass


class Ed25519Signer:
    """
    Ed25519 signing operations.

    Used for signing evidence bundles locally.
    Includes integrity verification to detect key tampering.
    """

    def __init__(self, private_key_path: Path, verify_integrity: bool = True):
        """
        Initialize signer with private key.

        Args:
            private_key_path: Path to Ed25519 private key (PEM format)
            verify_integrity: If True, verify key hasn't been tampered with

        Raises:
            ValueError: If key file cannot be loaded
            KeyIntegrityError: If key integrity check fails (tampering detected)
        """
        self.private_key_path = Path(private_key_path)
        self._integrity_hash_path = self.private_key_path.with_suffix('.hash')
        self._private_key = self._load_private_key(verify_integrity)

    def _compute_key_hash(self, key_data: bytes) -> str:
        """Compute SHA256 hash of key data for integrity verification."""
        return hashlib.sha256(key_data).hexdigest()

    def _load_private_key(self, verify_integrity: bool = True) -> Ed25519PrivateKey:
        """
        Load Ed25519 private key from file with integrity verification.

        On first load, stores a hash of the key for future verification.
        On subsequent loads, verifies the key hasn't been tampered with.
        """
        try:
            with open(self.private_key_path, 'rb') as f:
                key_data = f.read()

            # Compute hash of raw key data
            current_hash = self._compute_key_hash(key_data)

            # Check integrity if hash file exists
            if verify_integrity and self._integrity_hash_path.exists():
                with open(self._integrity_hash_path, 'r') as f:
                    stored_hash = f.read().strip()

                if stored_hash != current_hash:
                    raise KeyIntegrityError(
                        f"Private key integrity check FAILED for {self.private_key_path}. "
                        "The key file may have been tampered with. "
                        "If this is expected (key rotation), delete the .hash file and restart."
                    )

            # Store hash on first load (if file doesn't exist)
            if not self._integrity_hash_path.exists():
                with open(self._integrity_hash_path, 'w') as f:
                    f.write(current_hash)
                # Set restrictive permissions on hash file
                self._integrity_hash_path.chmod(0o600)

            # Try PEM format first
            try:
                private_key = serialization.load_pem_private_key(
                    key_data,
                    password=None
                )
            except Exception:
                # Try raw 32-byte format
                if len(key_data) == 32:
                    private_key = Ed25519PrivateKey.from_private_bytes(key_data)
                else:
                    raise ValueError("Invalid key format")

            if not isinstance(private_key, Ed25519PrivateKey):
                raise ValueError("Not an Ed25519 private key")

            return private_key

        except KeyIntegrityError:
            # Re-raise integrity errors as-is
            raise
        except Exception as e:
            raise ValueError(f"Failed to load private key from {self.private_key_path}: {e}")

    def sign(self, data: Union[bytes, str, Dict[Any, Any]]) -> bytes:
        """
        Sign data with Ed25519 private key.

        Args:
            data: Data to sign (bytes, string, or JSON-serializable dict)

        Returns:
            64-byte Ed25519 signature
        """
        # Convert to bytes if needed
        if isinstance(data, dict):
            data = json.dumps(data, sort_keys=True).encode('utf-8')
        elif isinstance(data, str):
            data = data.encode('utf-8')

        # Sign
        signature = self._private_key.sign(data)

        return signature

    def sign_file(self, file_path: Path) -> bytes:
        """
        Sign a file.

        Args:
            file_path: Path to file to sign

        Returns:
            64-byte Ed25519 signature
        """
        with open(file_path, 'rb') as f:
            data = f.read()

        return self.sign(data)

    def get_public_key_bytes(self) -> bytes:
        """
        Get public key bytes (32 bytes).

        Returns:
            Raw Ed25519 public key bytes
        """
        public_key = self._private_key.public_key()
        return public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )

    def get_public_key_pem(self) -> str:
        """
        Get public key in PEM format.

        Returns:
            PEM-encoded public key string
        """
        public_key = self._private_key.public_key()
        pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return pem.decode('utf-8')


class Ed25519Verifier:
    """
    Ed25519 signature verification.

    Used for verifying MCP order signatures.
    """

    def __init__(self, public_key: Union[bytes, str, Ed25519PublicKey]):
        """
        Initialize verifier with public key.

        Args:
            public_key: Ed25519 public key (32 bytes, PEM string, or key object)

        Raises:
            ValueError: If public key invalid
        """
        self._public_key = self._load_public_key(public_key)

    def _load_public_key(self, public_key: Union[bytes, str, Ed25519PublicKey]) -> Ed25519PublicKey:
        """Load Ed25519 public key from various formats."""
        if isinstance(public_key, Ed25519PublicKey):
            return public_key

        if isinstance(public_key, str):
            # PEM format
            public_key = public_key.encode('utf-8')

        try:
            # Try PEM format
            if b'-----BEGIN PUBLIC KEY-----' in public_key:
                key = serialization.load_pem_public_key(public_key)
            # Try raw 32-byte format
            elif len(public_key) == 32:
                key = Ed25519PublicKey.from_public_bytes(public_key)
            else:
                raise ValueError("Invalid public key format")

            if not isinstance(key, Ed25519PublicKey):
                raise ValueError("Not an Ed25519 public key")

            return key

        except Exception as e:
            raise ValueError(f"Failed to load public key: {e}")

    def verify(self, data: Union[bytes, str, Dict[Any, Any]], signature: bytes) -> bool:
        """
        Verify Ed25519 signature.

        Args:
            data: Data that was signed
            signature: 64-byte Ed25519 signature

        Returns:
            True if signature valid, False otherwise
        """
        # Convert to bytes if needed
        if isinstance(data, dict):
            data = json.dumps(data, sort_keys=True).encode('utf-8')
        elif isinstance(data, str):
            data = data.encode('utf-8')

        try:
            self._public_key.verify(signature, data)
            return True
        except InvalidSignature:
            return False

    def verify_file(self, file_path: Path, signature: bytes) -> bool:
        """
        Verify file signature.

        Args:
            file_path: Path to file that was signed
            signature: 64-byte Ed25519 signature

        Returns:
            True if signature valid, False otherwise
        """
        with open(file_path, 'rb') as f:
            data = f.read()

        return self.verify(data, signature)


def generate_keypair() -> tuple[bytes, bytes]:
    """
    Generate a new Ed25519 keypair.

    Returns:
        Tuple of (private_key_bytes, public_key_bytes) - each 32 bytes

    Note:
        This is primarily for testing. Production keys should be
        generated securely and managed via SOPS.
    """
    private_key = Ed25519PrivateKey.generate()

    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()
    )

    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )

    return private_bytes, public_bytes


def sha256_hash(data: Union[bytes, str, Path]) -> str:
    """
    Compute SHA256 hash.

    Args:
        data: Data to hash (bytes, string, or file path)

    Returns:
        Hex-encoded SHA256 hash
    """
    if isinstance(data, Path):
        with open(data, 'rb') as f:
            data = f.read()
    elif isinstance(data, str):
        data = data.encode('utf-8')

    return hashlib.sha256(data).hexdigest()


def verify_hash(data: Union[bytes, str, Path], expected_hash: str) -> bool:
    """
    Verify SHA256 hash.

    Args:
        data: Data to hash
        expected_hash: Expected hex-encoded SHA256 hash

    Returns:
        True if hash matches, False otherwise
    """
    actual_hash = sha256_hash(data)
    return actual_hash == expected_hash


def ensure_signing_key(key_path: Path) -> tuple[bool, str]:
    """
    Ensure signing key exists, generating one if needed.

    On first run, generates a new Ed25519 keypair and saves:
    - Private key: key_path (raw 32 bytes)
    - Public key: key_path.with_suffix('.pub') (raw 32 bytes)
    - Integrity hash: key_path.with_suffix('.hash') (for tampering detection)

    Args:
        key_path: Path where private key should be stored

    Returns:
        Tuple of (was_generated, public_key_hex)
        - was_generated: True if key was just created, False if already existed
        - public_key_hex: Hex-encoded public key for verification

    Raises:
        PermissionError: If directory not writable
        OSError: If key generation fails
        KeyIntegrityError: If existing key fails integrity check
    """
    import logging
    logger = logging.getLogger(__name__)

    key_path = Path(key_path)
    pub_path = key_path.with_suffix('.pub')
    hash_path = key_path.with_suffix('.hash')

    # Check if key already exists
    if key_path.exists():
        # Load existing key with integrity verification
        signer = Ed25519Signer(key_path, verify_integrity=True)
        public_key_hex = signer.get_public_key_bytes().hex()
        logger.debug(f"Using existing signing key: {key_path}")
        return False, public_key_hex

    # Generate new keypair
    logger.info(f"Generating new Ed25519 signing key at {key_path}")

    private_bytes, public_bytes = generate_keypair()

    # Ensure directory exists
    key_path.parent.mkdir(parents=True, exist_ok=True)

    # Write private key with secure permissions
    key_path.write_bytes(private_bytes)
    key_path.chmod(0o600)  # Owner read/write only

    # Write public key
    pub_path.write_bytes(public_bytes)
    pub_path.chmod(0o644)  # World readable

    # Write integrity hash (for tampering detection)
    key_hash = hashlib.sha256(private_bytes).hexdigest()
    hash_path.write_text(key_hash)
    hash_path.chmod(0o600)  # Owner read/write only

    logger.info(f"Generated signing keypair: private={key_path}, public={pub_path}")
    logger.info(f"Created integrity hash: {hash_path}")
    logger.info(f"Public key (hex): {public_bytes.hex()}")

    return True, public_bytes.hex()
