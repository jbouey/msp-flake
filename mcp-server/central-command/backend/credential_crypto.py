"""At-rest encryption for credentials using MultiFernet (AES-128-CBC + HMAC-SHA256).

This module is the single source of truth for all credential-at-rest encryption
across the platform: site_credentials, org_credentials, client_org_sso,
integrations, oauth_config, and partners.oauth_*_encrypted all flow through
`encrypt_credential()` / `decrypt_credential()`.

## Key rotation — multi-key support

The module supports key versioning via `cryptography.fernet.MultiFernet`, which
accepts a list of keys. The FIRST key in the list is always used for encryption;
decryption tries every key in order until one succeeds (or all fail). This is
the standard rotation pattern: when rotating, prepend the new key to the list,
re-encrypt all ciphertexts to the new key, then remove the old key.

## Key sources (in priority order)

1. `CREDENTIAL_ENCRYPTION_KEYS` env var (preferred) — comma-separated list of
   Fernet keys, newest first. Example: `KEY_2,KEY_1,KEY_0`
2. `CREDENTIAL_ENCRYPTION_KEY` env var (legacy single-key, still supported)
3. `/app/secrets/credential_encryption.key` file (bootstrap fallback)

No implicit key generation — if no key is available the module raises
`RuntimeError` on first use. We never want to silently start with a fresh key
and irreversibly orphan every existing ciphertext.

## Ciphertext format

All new ciphertexts start with the `b"FERNET:"` prefix. Legacy plaintext rows
(created before Fernet was introduced) are returned unchanged by
`decrypt_credential()` for back-compat, but `encrypt_credential()` always emits
the prefixed form. Fernet itself embeds a timestamp, so callers can trace
`fernet_age()` without touching the key material.
"""
import hashlib
import logging
import os
from typing import List

from cryptography.fernet import Fernet, MultiFernet, InvalidToken

logger = logging.getLogger(__name__)

_multi: MultiFernet | None = None
_key_fingerprints: List[str] = []


def _load_keys() -> List[str]:
    """Return the ordered list of Fernet keys, newest first.

    Priority: CREDENTIAL_ENCRYPTION_KEYS (multi) > CREDENTIAL_ENCRYPTION_KEY
    (single) > /app/secrets/credential_encryption.key file.

    Raises:
        RuntimeError: if no key material is available from any source.
    """
    # 1. Multi-key env var (preferred)
    multi_raw = os.environ.get("CREDENTIAL_ENCRYPTION_KEYS")
    if multi_raw:
        keys = [k.strip() for k in multi_raw.split(",") if k.strip()]
        if not keys:
            raise RuntimeError("CREDENTIAL_ENCRYPTION_KEYS is set but empty")
        return keys

    # 2. Legacy single-key env var
    single = os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
    if single:
        return [single.strip()]

    # 3. Bootstrap key file
    key_path = "/app/secrets/credential_encryption.key"
    if os.path.exists(key_path):
        with open(key_path) as f:
            key = f.read().strip()
        if key:
            return [key]

    logger.error(
        "CRITICAL: No credential encryption key found. Set "
        "CREDENTIAL_ENCRYPTION_KEYS (preferred), CREDENTIAL_ENCRYPTION_KEY, "
        "or mount a key at %s. Credentials cannot be decrypted without this key.",
        key_path,
    )
    raise RuntimeError(
        "No credential encryption key available — set CREDENTIAL_ENCRYPTION_KEYS "
        f"or CREDENTIAL_ENCRYPTION_KEY env var, or mount a key at {key_path}"
    )


def _fingerprint(key: str) -> str:
    """Short stable fingerprint for audit logging. First 12 hex chars of
    SHA-256 over the key material. Never use this for crypto — it is
    intentionally truncated so an auditor can spot which key was used
    without exposing the key itself.
    """
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _get_multi() -> MultiFernet:
    """Lazy-load the MultiFernet instance. Cached per-process.

    The cache key is the process id; tests can call `reset_cache()` to force
    reload after changing env vars.
    """
    global _multi, _key_fingerprints
    if _multi is not None:
        return _multi

    keys = _load_keys()
    fernets = [Fernet(k.encode() if isinstance(k, str) else k) for k in keys]
    _multi = MultiFernet(fernets)
    _key_fingerprints = [_fingerprint(k) for k in keys]
    logger.info(
        "Credential encryption initialised: %d key(s), primary fingerprint=%s",
        len(keys),
        _key_fingerprints[0] if _key_fingerprints else "?",
    )
    return _multi


def reset_cache() -> None:
    """Clear the cached MultiFernet. Test-only helper — production code
    should never call this because it means concurrent requests could be
    using different key versions for a brief window."""
    global _multi, _key_fingerprints
    _multi = None
    _key_fingerprints = []


def get_key_fingerprints() -> List[str]:
    """Return the fingerprints of all currently-loaded keys (newest first).

    Used by the rotation admin endpoint to prove which keys are active
    without leaking the key material itself.
    """
    _get_multi()  # ensure cache warm
    return list(_key_fingerprints)


def primary_key_fingerprint() -> str:
    """Return the fingerprint of the key that will encrypt new ciphertexts."""
    fps = get_key_fingerprints()
    return fps[0] if fps else ""


def encrypt_credential(plaintext_json: str) -> bytes:
    """Encrypt credential JSON string. Returns encrypted bytes for DB storage.

    New ciphertexts always carry the `b"FERNET:"` prefix so callers can
    distinguish encrypted vs legacy-plaintext rows.
    """
    mf = _get_multi()
    return b"FERNET:" + mf.encrypt(plaintext_json.encode())


def decrypt_credential(encrypted_data: bytes) -> str:
    """Decrypt credential data from DB. Returns JSON string.

    Handles three formats:
      1. FERNET-prefixed ciphertext (the current format, verified by
         trying every known key in the keyring via MultiFernet)
      2. Legacy plaintext bytes (created before Fernet was introduced)
      3. Already-decoded strings (defensive)

    Raises:
        InvalidToken: if the ciphertext cannot be decrypted by ANY key in
            the keyring. Callers should surface this as a 500 and log the
            fingerprint of the keys that were tried.
    """
    if isinstance(encrypted_data, memoryview):
        encrypted_data = bytes(encrypted_data)

    if isinstance(encrypted_data, (bytes, bytearray)) and encrypted_data.startswith(b"FERNET:"):
        mf = _get_multi()
        try:
            return mf.decrypt(encrypted_data[7:]).decode()
        except InvalidToken:
            logger.error(
                "Credential decrypt failed: no key in keyring matches ciphertext. "
                "Active fingerprints: %s",
                get_key_fingerprints(),
            )
            raise

    # Legacy plaintext — return as-is
    if isinstance(encrypted_data, (bytes, bytearray)):
        return encrypted_data.decode()
    return encrypted_data


def rotate_ciphertext(encrypted_data: bytes) -> bytes:
    """Re-encrypt a ciphertext using the current primary key, preserving
    the plaintext.

    This is the key primitive for the background re-encryption migration:
    the row is read, decrypted by whichever key in the keyring matches, then
    immediately re-encrypted by the newest key. Callers should run this inside
    the same transaction as the UPDATE so a failure leaves the row untouched.

    Handles legacy plaintext rows by encrypting them for the first time.

    Returns:
        New ciphertext bytes (always FERNET-prefixed). If the input is
        already encrypted by the newest key, the output is NOT guaranteed
        to be byte-identical (Fernet embeds a fresh IV + timestamp) but it
        will decrypt to the same plaintext.

    Raises:
        InvalidToken: if the ciphertext is FERNET-prefixed but cannot be
            decrypted by any key in the keyring.
    """
    plaintext = decrypt_credential(encrypted_data)
    return encrypt_credential(plaintext)


def generate_new_key() -> str:
    """Generate a fresh Fernet key and return it as a base64 string.

    This does NOT install the key into the keyring — the caller is
    responsible for adding it to CREDENTIAL_ENCRYPTION_KEYS (newest first),
    restarting the process, and running the rotation re-encrypt loop.
    """
    return Fernet.generate_key().decode()
