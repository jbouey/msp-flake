"""
Local encrypted credential storage for appliance mode.

Eliminates the need to send credentials in every checkin response.
Credentials are stored encrypted at rest using Fernet symmetric encryption
with a key derived from the appliance API key + machine identity.

HIPAA Controls:
- §164.312(a)(2)(iv): Encryption and decryption
- §164.312(e)(1): Transmission security (reduces credential exposure)
"""

import json
import hashlib
import hmac
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Fernet requires cryptography package
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    import base64
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    logger.warning("cryptography package not available - credential store will use fallback encoding")


def _get_machine_id() -> str:
    """Get a stable machine identifier for key derivation.

    Tries /etc/machine-id (NixOS/systemd) first, falls back to MAC address.
    """
    try:
        machine_id_path = Path("/etc/machine-id")
        if machine_id_path.exists():
            return machine_id_path.read_text().strip()
    except Exception:
        pass

    # Fallback to MAC address
    try:
        for iface in Path("/sys/class/net").iterdir():
            if iface.name in ("lo", "docker0"):
                continue
            addr_file = iface / "address"
            if addr_file.exists():
                mac = addr_file.read_text().strip()
                if mac and mac != "00:00:00:00:00:00":
                    return mac
    except Exception:
        pass

    return "fallback-machine-id"


def _derive_key(api_key: str, machine_id: str) -> bytes:
    """Derive a Fernet-compatible encryption key from API key + machine ID.

    Uses HKDF (HMAC-based Key Derivation Function) with SHA256.
    Falls back to SHA256 hash if cryptography package not available.
    """
    key_material = f"{api_key}:{machine_id}".encode()

    if CRYPTO_AVAILABLE:
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"osiriscare-credential-store-v1",
            info=b"appliance-credential-encryption",
        )
        derived = hkdf.derive(key_material)
        return base64.urlsafe_b64encode(derived)
    else:
        # Fallback: SHA256 hash (less secure but functional)
        h = hashlib.sha256(key_material)
        return base64.urlsafe_b64encode(h.digest())


class CredentialStore:
    """Fernet-encrypted local credential storage.

    Credentials are stored at {state_dir}/credentials.enc as encrypted JSON.
    The encryption key is derived from the appliance API key + machine identity,
    ensuring credentials can't be decrypted on a different machine.

    Usage:
        store = CredentialStore(state_dir=Path("/var/lib/msp-compliance-agent"), api_key="...")
        store.store_credentials("windows", [{"hostname": "dc1", "username": "admin", ...}])
        creds = store.load_credentials("windows")
    """

    # Maximum age before credentials should be refreshed from server
    DEFAULT_TTL_SECONDS = 86400  # 24 hours

    def __init__(self, state_dir: Path, api_key: str):
        self._state_dir = Path(state_dir)
        self._store_path = self._state_dir / "credentials.enc"
        self._meta_path = self._state_dir / "credentials.meta"
        self._machine_id = _get_machine_id()
        self._key = _derive_key(api_key, self._machine_id)

        if CRYPTO_AVAILABLE:
            self._fernet = Fernet(self._key)
        else:
            self._fernet = None

        # Ensure state directory exists
        self._state_dir.mkdir(parents=True, exist_ok=True)

    def _encrypt(self, data: bytes) -> bytes:
        """Encrypt data using Fernet."""
        if self._fernet:
            return self._fernet.encrypt(data)
        else:
            # Fallback: XOR with derived key (NOT secure, but functional)
            # This path should not be used in production
            logger.warning("Using fallback encryption - install cryptography package")
            key_bytes = self._key[:32]
            encrypted = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(data))
            return base64.urlsafe_b64encode(encrypted)

    def _decrypt(self, data: bytes) -> bytes:
        """Decrypt data using Fernet."""
        if self._fernet:
            return self._fernet.decrypt(data)
        else:
            decoded = base64.urlsafe_b64decode(data)
            key_bytes = self._key[:32]
            return bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(decoded))

    def _load_store(self) -> Dict[str, Any]:
        """Load and decrypt the credential store."""
        if not self._store_path.exists():
            return {}

        try:
            encrypted = self._store_path.read_bytes()
            decrypted = self._decrypt(encrypted)
            return json.loads(decrypted.decode('utf-8'))
        except Exception as e:
            logger.error(f"Failed to load credential store: {e}")
            return {}

    def _save_store(self, store: Dict[str, Any]) -> None:
        """Encrypt and save the credential store atomically."""
        try:
            data = json.dumps(store).encode('utf-8')
            encrypted = self._encrypt(data)

            # Atomic write: write to temp file, then rename
            tmp_path = self._store_path.with_suffix('.tmp')
            tmp_path.write_bytes(encrypted)
            tmp_path.rename(self._store_path)

            # Restrict permissions (owner read/write only)
            os.chmod(self._store_path, 0o600)
        except Exception as e:
            logger.error(f"Failed to save credential store: {e}")
            raise

    def _load_meta(self) -> Dict[str, Any]:
        """Load credential metadata (timestamps, versions)."""
        if not self._meta_path.exists():
            return {}
        try:
            return json.loads(self._meta_path.read_text())
        except Exception:
            return {}

    def _save_meta(self, meta: Dict[str, Any]) -> None:
        """Save credential metadata."""
        try:
            tmp_path = self._meta_path.with_suffix('.tmp')
            tmp_path.write_text(json.dumps(meta))
            tmp_path.rename(self._meta_path)
            os.chmod(self._meta_path, 0o600)
        except Exception as e:
            logger.error(f"Failed to save credential metadata: {e}")

    def store_credentials(self, cred_type: str, credentials: List[Dict]) -> None:
        """Store credentials of the given type.

        Args:
            cred_type: Credential type (e.g., "windows", "linux")
            credentials: List of credential dicts
        """
        store = self._load_store()
        store[cred_type] = credentials
        self._save_store(store)

        # Update metadata
        meta = self._load_meta()
        if cred_type not in meta:
            meta[cred_type] = {}
        meta[cred_type]["stored_at"] = datetime.now(timezone.utc).isoformat()
        meta[cred_type]["count"] = len(credentials)
        # Hash credentials to detect changes
        cred_hash = hashlib.sha256(json.dumps(credentials, sort_keys=True).encode()).hexdigest()[:16]
        meta[cred_type]["hash"] = cred_hash
        self._save_meta(meta)

        logger.info(f"Stored {len(credentials)} {cred_type} credentials locally")

    def load_credentials(self, cred_type: str) -> List[Dict]:
        """Load credentials of the given type.

        Args:
            cred_type: Credential type (e.g., "windows", "linux")

        Returns:
            List of credential dicts, or empty list if not found
        """
        store = self._load_store()
        creds = store.get(cred_type, [])
        if creds:
            logger.debug(f"Loaded {len(creds)} {cred_type} credentials from local store")
        return creds

    def has_credentials(self, cred_type: str) -> bool:
        """Check if credentials of the given type are stored locally."""
        store = self._load_store()
        creds = store.get(cred_type, [])
        return len(creds) > 0

    def clear_credentials(self, cred_type: str) -> None:
        """Remove credentials of the given type."""
        store = self._load_store()
        if cred_type in store:
            del store[cred_type]
            self._save_store(store)

        meta = self._load_meta()
        if cred_type in meta:
            del meta[cred_type]
            self._save_meta(meta)

        logger.info(f"Cleared {cred_type} credentials from local store")

    def credentials_age_seconds(self, cred_type: str) -> int:
        """Get the age of stored credentials in seconds.

        Returns:
            Age in seconds, or -1 if no credentials stored
        """
        meta = self._load_meta()
        type_meta = meta.get(cred_type, {})
        stored_at = type_meta.get("stored_at")

        if not stored_at:
            return -1

        try:
            stored_time = datetime.fromisoformat(stored_at)
            age = (datetime.now(timezone.utc) - stored_time).total_seconds()
            return int(age)
        except Exception:
            return -1

    def credentials_hash(self, cred_type: str) -> Optional[str]:
        """Get the hash of stored credentials for change detection.

        Returns:
            Hash string, or None if no credentials stored
        """
        meta = self._load_meta()
        return meta.get(cred_type, {}).get("hash")

    def needs_refresh(self, cred_type: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
        """Check if credentials need refreshing from server.

        Args:
            cred_type: Credential type
            ttl_seconds: Maximum age before refresh needed

        Returns:
            True if credentials should be refreshed
        """
        age = self.credentials_age_seconds(cred_type)
        if age < 0:
            return True  # No credentials stored
        return age >= ttl_seconds
