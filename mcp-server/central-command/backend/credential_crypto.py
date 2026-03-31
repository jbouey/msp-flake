"""At-rest encryption for site_credentials using Fernet symmetric encryption.

Uses a server-side key (not the appliance's X25519 key, which is for transit).
Key is loaded from CREDENTIAL_ENCRYPTION_KEY env var or generated and stored.
"""
import os
from cryptography.fernet import Fernet

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
    if not key:
        key_path = "/app/secrets/credential_encryption.key"
        if os.path.exists(key_path):
            with open(key_path) as f:
                key = f.read().strip()
        else:
            import logging
            logging.getLogger(__name__).error(
                "CRITICAL: No credential encryption key found. "
                "Set CREDENTIAL_ENCRYPTION_KEY env var or mount key at %s. "
                "Credentials cannot be encrypted/decrypted without this key.",
                key_path,
            )
            raise RuntimeError(
                f"Credential encryption key not found at {key_path} "
                "and CREDENTIAL_ENCRYPTION_KEY env var not set"
            )

    _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt_credential(plaintext_json: str) -> bytes:
    """Encrypt credential JSON string. Returns encrypted bytes for DB storage."""
    f = _get_fernet()
    return b"FERNET:" + f.encrypt(plaintext_json.encode())


def decrypt_credential(encrypted_data: bytes) -> str:
    """Decrypt credential data from DB. Returns JSON string.

    Handles both encrypted (FERNET: prefix) and legacy plaintext data.
    """
    if isinstance(encrypted_data, memoryview):
        encrypted_data = bytes(encrypted_data)

    if isinstance(encrypted_data, (bytes, bytearray)) and encrypted_data.startswith(b"FERNET:"):
        f = _get_fernet()
        return f.decrypt(encrypted_data[7:]).decode()

    # Legacy plaintext — return as-is
    if isinstance(encrypted_data, (bytes, bytearray)):
        return encrypted_data.decode()
    return encrypted_data
