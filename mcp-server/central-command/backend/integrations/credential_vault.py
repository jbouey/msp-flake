"""
Per-integration credential encryption using HKDF-derived keys.

SECURITY REQUIREMENT: NEVER use shared encryption keys across integrations.

Each integration has a unique encryption key derived via HKDF from:
- Master key (from KMS or local file)
- Integration ID (unique salt per integration)
- Purpose string (encrypt, sign, etc.)

This ensures that compromise of one integration's key doesn't expose others.

Production mode: Master key from AWS KMS
Development mode: Master key from local file

Usage:
    vault = CredentialVault(mode="local")  # or "kms"

    # Encrypt credentials for storage
    encrypted = vault.encrypt_credentials(integration_id, {
        "access_token": "sk-xxx",
        "refresh_token": "rt-xxx"
    })

    # Decrypt credentials
    creds = vault.decrypt_credentials(integration_id, encrypted)
    print(creds.get("access_token"))
"""

import os
import json
import base64
import logging
import hashlib
import secrets
from pathlib import Path
from typing import Dict, Any, Optional, Union
from datetime import datetime

from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.fernet import Fernet, InvalidToken

from .secure_credentials import SecureCredentials

logger = logging.getLogger(__name__)


# Configuration
MASTER_KEY_PATH = os.getenv("LOCAL_MASTER_KEY_PATH", "/var/lib/msp/integration-master.key")
KMS_KEY_ID = os.getenv("KMS_KEY_ID")
KEY_LENGTH = 32  # 256 bits for Fernet


class CredentialVaultError(Exception):
    """Base exception for credential vault errors."""
    pass


class KeyDerivationError(CredentialVaultError):
    """Error during key derivation."""
    pass


class EncryptionError(CredentialVaultError):
    """Error during encryption."""
    pass


class DecryptionError(CredentialVaultError):
    """Error during decryption."""
    pass


class CredentialVault:
    """
    Secure credential storage with per-integration key derivation.

    Uses HKDF to derive unique encryption keys for each integration,
    ensuring key isolation even if the master key is compromised for
    one integration.
    """

    def __init__(self, mode: str = "auto"):
        """
        Initialize the credential vault.

        Args:
            mode: "local", "kms", or "auto" (default)
                  - local: Use file-based master key
                  - kms: Use AWS KMS for master key
                  - auto: Use KMS if configured, else local
        """
        self.mode = self._determine_mode(mode)
        self._master_key: Optional[bytes] = None
        self._salt_cache: Dict[str, bytes] = {}

        logger.info(f"CredentialVault initialized in {self.mode} mode")

    def _determine_mode(self, mode: str) -> str:
        """Determine actual mode based on configuration."""
        if mode == "auto":
            if KMS_KEY_ID:
                return "kms"
            return "local"
        return mode

    def _get_master_key(self) -> bytes:
        """
        Get or load the master key.

        Returns:
            32-byte master key

        Raises:
            KeyDerivationError: If master key cannot be loaded
        """
        if self._master_key is not None:
            return self._master_key

        if self.mode == "kms":
            self._master_key = self._get_kms_key()
        else:
            self._master_key = self._get_local_key()

        return self._master_key

    def _get_local_key(self) -> bytes:
        """
        Get or create local file-based master key.

        Returns:
            32-byte master key
        """
        key_path = Path(MASTER_KEY_PATH)

        if key_path.exists():
            # Load existing key
            key_data = key_path.read_bytes()
            if len(key_data) != KEY_LENGTH:
                raise KeyDerivationError(f"Invalid master key length: {len(key_data)}")
            logger.debug("Loaded existing master key from file")
            return key_data

        # Create new key
        key_path.parent.mkdir(parents=True, exist_ok=True)
        new_key = secrets.token_bytes(KEY_LENGTH)

        # Write with restricted permissions
        key_path.write_bytes(new_key)
        os.chmod(key_path, 0o600)

        logger.info(f"Created new master key at {key_path}")
        return new_key

    def _get_kms_key(self) -> bytes:
        """
        Get master key from AWS KMS.

        Uses KMS to generate a data key, storing the encrypted version
        locally and keeping the plaintext in memory only.

        Returns:
            32-byte master key
        """
        try:
            import boto3
        except ImportError:
            raise KeyDerivationError("boto3 required for KMS mode")

        if not KMS_KEY_ID:
            raise KeyDerivationError("KMS_KEY_ID environment variable not set")

        # Check for cached encrypted key
        encrypted_key_path = Path(MASTER_KEY_PATH + ".encrypted")

        kms_client = boto3.client("kms")

        if encrypted_key_path.exists():
            # Decrypt existing key
            encrypted_key = encrypted_key_path.read_bytes()
            response = kms_client.decrypt(
                CiphertextBlob=encrypted_key,
                KeyId=KMS_KEY_ID
            )
            logger.debug("Decrypted master key from KMS")
            return response["Plaintext"]

        # Generate new data key
        response = kms_client.generate_data_key(
            KeyId=KMS_KEY_ID,
            KeySpec="AES_256"
        )

        # Store encrypted key
        encrypted_key_path.parent.mkdir(parents=True, exist_ok=True)
        encrypted_key_path.write_bytes(response["CiphertextBlob"])
        os.chmod(encrypted_key_path, 0o600)

        logger.info("Generated new KMS-encrypted master key")
        return response["Plaintext"]

    def _get_integration_salt(self, integration_id: str) -> bytes:
        """
        Get or create salt for an integration.

        Each integration has a unique salt stored in the database
        or derived deterministically from the integration ID.

        Args:
            integration_id: Unique integration identifier

        Returns:
            16-byte salt
        """
        if integration_id in self._salt_cache:
            return self._salt_cache[integration_id]

        # Derive salt deterministically from integration ID
        # This ensures the same salt is always used for the same integration
        salt = hashlib.sha256(
            f"osiriscare:integration:salt:{integration_id}".encode()
        ).digest()[:16]

        self._salt_cache[integration_id] = salt
        return salt

    def derive_key(
        self,
        integration_id: str,
        purpose: str = "encrypt"
    ) -> bytes:
        """
        Derive a unique key for an integration using HKDF.

        Args:
            integration_id: Unique integration identifier
            purpose: Key purpose (e.g., "encrypt", "sign")

        Returns:
            32-byte derived key suitable for Fernet
        """
        master_key = self._get_master_key()
        salt = self._get_integration_salt(integration_id)
        info = f"osiriscare:v1:{integration_id}:{purpose}".encode()

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=KEY_LENGTH,
            salt=salt,
            info=info,
            backend=default_backend()
        )

        derived_key = hkdf.derive(master_key)

        # Fernet requires base64-encoded 32-byte key
        return base64.urlsafe_b64encode(derived_key)

    def encrypt_credentials(
        self,
        integration_id: str,
        credentials: Union[Dict[str, Any], SecureCredentials]
    ) -> bytes:
        """
        Encrypt credentials with integration-specific key.

        Args:
            integration_id: Unique integration identifier
            credentials: Credentials to encrypt (dict or SecureCredentials)

        Returns:
            Encrypted credentials as bytes

        Raises:
            EncryptionError: If encryption fails
        """
        try:
            key = self.derive_key(integration_id, "encrypt")
            fernet = Fernet(key)

            # Convert to JSON
            if isinstance(credentials, SecureCredentials):
                json_data = credentials.to_json()
            else:
                json_data = json.dumps(credentials, sort_keys=True, default=str)

            # Encrypt
            encrypted = fernet.encrypt(json_data.encode("utf-8"))

            logger.debug(
                f"Encrypted credentials for integration {integration_id[:8]}..."
            )
            return encrypted

        except Exception as e:
            logger.error(f"Encryption failed: {type(e).__name__}")
            raise EncryptionError(f"Failed to encrypt credentials: {e}") from e

    def decrypt_credentials(
        self,
        integration_id: str,
        encrypted: bytes
    ) -> SecureCredentials:
        """
        Decrypt credentials with integration-specific key.

        Args:
            integration_id: Unique integration identifier
            encrypted: Encrypted credentials bytes

        Returns:
            Decrypted credentials as SecureCredentials

        Raises:
            DecryptionError: If decryption fails
        """
        try:
            key = self.derive_key(integration_id, "encrypt")
            fernet = Fernet(key)

            # Decrypt
            decrypted = fernet.decrypt(encrypted)
            data = json.loads(decrypted.decode("utf-8"))

            logger.debug(
                f"Decrypted credentials for integration {integration_id[:8]}..."
            )
            return SecureCredentials(**data)

        except InvalidToken:
            logger.error(
                f"Decryption failed for integration {integration_id[:8]}... "
                "(invalid token - key mismatch or corrupted data)"
            )
            raise DecryptionError("Invalid token - credentials may be corrupted")
        except Exception as e:
            logger.error(f"Decryption failed: {type(e).__name__}")
            raise DecryptionError(f"Failed to decrypt credentials: {e}") from e

    def rotate_integration_key(
        self,
        integration_id: str,
        old_encrypted: bytes
    ) -> bytes:
        """
        Re-encrypt credentials with a new derived key.

        This should be called after master key rotation to
        re-encrypt all integration credentials.

        Args:
            integration_id: Unique integration identifier
            old_encrypted: Currently encrypted credentials

        Returns:
            Re-encrypted credentials
        """
        # Decrypt with current key
        credentials = self.decrypt_credentials(integration_id, old_encrypted)

        # Clear salt cache to force new derivation
        self._salt_cache.pop(integration_id, None)

        # Re-encrypt with new key
        return self.encrypt_credentials(integration_id, credentials)

    def verify_key_derivation(self, integration_id: str) -> bool:
        """
        Verify that key derivation is working correctly.

        Useful for health checks and diagnostics.

        Args:
            integration_id: Integration ID to test

        Returns:
            True if key derivation succeeds
        """
        try:
            key = self.derive_key(integration_id, "verify")
            return len(base64.urlsafe_b64decode(key)) == KEY_LENGTH
        except Exception:
            return False

    def get_key_fingerprint(self, integration_id: str) -> str:
        """
        Get a safe fingerprint of the derived key.

        Useful for logging and debugging without exposing the key.

        Args:
            integration_id: Integration ID

        Returns:
            SHA256 fingerprint (first 16 chars)
        """
        key = self.derive_key(integration_id, "fingerprint")
        return hashlib.sha256(key).hexdigest()[:16]


# Global vault instance (lazy initialization)
_vault_instance: Optional[CredentialVault] = None


def get_vault() -> CredentialVault:
    """
    Get the global CredentialVault instance.

    Returns:
        CredentialVault singleton
    """
    global _vault_instance
    if _vault_instance is None:
        _vault_instance = CredentialVault()
    return _vault_instance
