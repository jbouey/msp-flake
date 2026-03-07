"""Shared TOTP 2FA module for all portals.

Provides TOTP generation, verification, and backup code management
for admin, partner, and client portal authentication.

Uses pyotp for RFC 6238 TOTP and bcrypt for backup code hashing.
"""

import json
import secrets
import string
import logging
from typing import Tuple, Optional, List

import pyotp
import bcrypt

logger = logging.getLogger(__name__)

# TOTP configuration
TOTP_WINDOW = 1  # Allow 30s clock skew (one period before/after)
BACKUP_CODE_LENGTH = 8
DEFAULT_BACKUP_CODE_COUNT = 8


def generate_totp_secret() -> str:
    """Generate a new TOTP secret for a user.

    Returns a base32-encoded secret suitable for authenticator apps.
    """
    return pyotp.random_base32()


def get_totp_uri(secret: str, email: str, issuer: str = "OsirisCare") -> str:
    """Generate an otpauth:// URI for QR code generation.

    Args:
        secret: Base32-encoded TOTP secret
        email: User's email address (used as account name)
        issuer: Application name shown in authenticator app

    Returns:
        otpauth:// URI string
    """
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=issuer)


def verify_totp(secret: str, code: str) -> bool:
    """Verify a TOTP code against a secret.

    Args:
        secret: Base32-encoded TOTP secret
        code: 6-digit TOTP code from authenticator app

    Returns:
        True if code is valid within the configured window
    """
    if not secret or not code:
        return False
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=TOTP_WINDOW)


def generate_backup_codes(count: int = DEFAULT_BACKUP_CODE_COUNT) -> List[str]:
    """Generate a set of random backup codes.

    Each code is an 8-character alphanumeric string.
    These are shown to the user once and never retrievable after.

    Args:
        count: Number of backup codes to generate

    Returns:
        List of plaintext backup codes
    """
    alphabet = string.ascii_lowercase + string.digits
    codes = []
    for _ in range(count):
        code = ''.join(secrets.choice(alphabet) for _ in range(BACKUP_CODE_LENGTH))
        codes.append(code)
    return codes


def hash_backup_code(code: str) -> str:
    """Hash a single backup code using bcrypt.

    Args:
        code: Plaintext backup code

    Returns:
        bcrypt hash string
    """
    return bcrypt.hashpw(code.encode(), bcrypt.gensalt()).decode()


def verify_backup_code(code: str, hashed_codes_json: str) -> Tuple[bool, str]:
    """Verify a backup code against stored hashed codes.

    If valid, removes the used code from the list (single-use).

    Args:
        code: Plaintext backup code to verify
        hashed_codes_json: JSON string containing array of bcrypt hashes

    Returns:
        Tuple of (is_valid, updated_hashed_codes_json)
        The updated JSON has the used code removed if valid.
    """
    if not code or not hashed_codes_json:
        return False, hashed_codes_json or "[]"

    try:
        hashed_codes = json.loads(hashed_codes_json)
    except (json.JSONDecodeError, TypeError):
        return False, "[]"

    code_bytes = code.encode()
    for i, hashed in enumerate(hashed_codes):
        try:
            if bcrypt.checkpw(code_bytes, hashed.encode()):
                # Remove used code (single-use)
                remaining = hashed_codes[:i] + hashed_codes[i + 1:]
                return True, json.dumps(remaining)
        except (ValueError, TypeError):
            continue

    return False, hashed_codes_json
