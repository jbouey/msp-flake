"""Tests for security-critical modules: totp, csrf, credential_crypto.

These modules had zero test coverage before Session 202.
"""

import json
import os
import sys
import types
import secrets
import pytest


# ============================================================================
# TOTP Tests
# ============================================================================

# pyotp and bcrypt are available on the system
import pyotp
import bcrypt

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from totp import (
    generate_totp_secret,
    get_totp_uri,
    verify_totp,
    generate_backup_codes,
    hash_backup_code,
    verify_backup_code,
)


class TestTOTPGeneration:
    def test_secret_is_base32(self):
        secret = generate_totp_secret()
        assert len(secret) >= 16
        # Base32 chars only
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567=" for c in secret)

    def test_secrets_are_unique(self):
        s1 = generate_totp_secret()
        s2 = generate_totp_secret()
        assert s1 != s2

    def test_uri_format(self):
        secret = generate_totp_secret()
        uri = get_totp_uri(secret, "user@example.com")
        assert uri.startswith("otpauth://totp/")
        assert "OsirisCare" in uri
        assert "user%40example.com" in uri or "user@example.com" in uri

    def test_uri_custom_issuer(self):
        secret = generate_totp_secret()
        uri = get_totp_uri(secret, "test@test.com", issuer="TestApp")
        assert "TestApp" in uri


class TestTOTPVerification:
    def test_valid_code(self):
        secret = generate_totp_secret()
        totp = pyotp.TOTP(secret)
        code = totp.now()
        assert verify_totp(secret, code) is True

    def test_wrong_code(self):
        secret = generate_totp_secret()
        assert verify_totp(secret, "000000") is False

    def test_empty_secret(self):
        assert verify_totp("", "123456") is False
        assert verify_totp(None, "123456") is False

    def test_empty_code(self):
        secret = generate_totp_secret()
        assert verify_totp(secret, "") is False
        assert verify_totp(secret, None) is False


class TestBackupCodes:
    def test_default_count(self):
        codes = generate_backup_codes()
        assert len(codes) == 8

    def test_custom_count(self):
        codes = generate_backup_codes(count=4)
        assert len(codes) == 4

    def test_code_length(self):
        codes = generate_backup_codes()
        for code in codes:
            assert len(code) == 8

    def test_codes_are_unique(self):
        codes = generate_backup_codes()
        assert len(set(codes)) == len(codes)

    def test_codes_alphanumeric(self):
        codes = generate_backup_codes()
        import string
        valid = set(string.ascii_lowercase + string.digits)
        for code in codes:
            assert all(c in valid for c in code)

    def test_hash_and_verify_roundtrip(self):
        codes = generate_backup_codes(count=3)
        hashes = [hash_backup_code(c) for c in codes]
        hashes_json = json.dumps(hashes)

        # First code should verify
        valid, remaining_json = verify_backup_code(codes[0], hashes_json)
        assert valid is True
        remaining = json.loads(remaining_json)
        assert len(remaining) == 2  # One removed

    def test_verify_removes_used_code(self):
        codes = generate_backup_codes(count=2)
        hashes = [hash_backup_code(c) for c in codes]
        hashes_json = json.dumps(hashes)

        # Use first code
        valid, remaining_json = verify_backup_code(codes[0], hashes_json)
        assert valid is True

        # Try same code again — should fail
        valid2, _ = verify_backup_code(codes[0], remaining_json)
        assert valid2 is False

        # Second code still works
        valid3, final_json = verify_backup_code(codes[1], remaining_json)
        assert valid3 is True
        assert json.loads(final_json) == []

    def test_verify_wrong_code(self):
        codes = generate_backup_codes(count=2)
        hashes = [hash_backup_code(c) for c in codes]
        hashes_json = json.dumps(hashes)

        valid, returned_json = verify_backup_code("wrongcode", hashes_json)
        assert valid is False
        assert returned_json == hashes_json  # Unchanged

    def test_verify_empty_inputs(self):
        valid, result = verify_backup_code("", "[]")
        assert valid is False
        assert result == "[]"

        valid, result = verify_backup_code(None, None)
        assert valid is False
        assert result == "[]"

    def test_verify_invalid_json(self):
        valid, result = verify_backup_code("test", "not json{{{")
        assert valid is False
        assert result == "[]"


# ============================================================================
# CSRF Tests
# ============================================================================

# Set env before import
os.environ.setdefault("CSRF_SECRET", "test-csrf-secret-for-unit-tests")
os.environ.setdefault("SESSION_TOKEN_SECRET", "test-session-secret")

# Stub starlette before importing csrf
for mod_name in ("starlette", "starlette.middleware", "starlette.middleware.base", "starlette.responses"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)
sys.modules["starlette.middleware.base"].BaseHTTPMiddleware = type("BHM", (), {})
sys.modules["starlette.responses"].Response = object

# Stub fastapi
for mod_name in ("fastapi",):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)
sys.modules["fastapi"].Request = object
sys.modules["fastapi"].HTTPException = type("HE", (Exception,), {"__init__": lambda self, **kw: None})

from csrf import generate_csrf_token, validate_csrf_token


class TestCSRFTokenGeneration:
    def test_token_has_signature(self):
        token = generate_csrf_token()
        assert "." in token
        parts = token.rsplit(".", 1)
        assert len(parts) == 2
        assert len(parts[1]) == 16  # HMAC truncated to 16 hex

    def test_tokens_are_unique(self):
        t1 = generate_csrf_token()
        t2 = generate_csrf_token()
        assert t1 != t2


class TestCSRFValidation:
    def test_matching_tokens_pass(self):
        token = generate_csrf_token()
        assert validate_csrf_token(token, token) is True

    def test_mismatched_tokens_fail(self):
        t1 = generate_csrf_token()
        t2 = generate_csrf_token()
        assert validate_csrf_token(t1, t2) is False

    def test_empty_cookie_fails(self):
        token = generate_csrf_token()
        assert validate_csrf_token("", token) is False
        assert validate_csrf_token(None, token) is False

    def test_empty_header_fails(self):
        token = generate_csrf_token()
        assert validate_csrf_token(token, "") is False
        assert validate_csrf_token(token, None) is False

    def test_tampered_signature_fails(self):
        token = generate_csrf_token()
        # Flip last char of signature
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
        assert validate_csrf_token(tampered, tampered) is False


# ============================================================================
# Credential Crypto Tests
# ============================================================================

# Set a valid Fernet key for testing
from cryptography.fernet import Fernet
test_key = Fernet.generate_key().decode()
os.environ["CREDENTIAL_ENCRYPTION_KEY"] = test_key

# Reset the cached fernet instance
import credential_crypto
credential_crypto._fernet = None

from credential_crypto import encrypt_credential, decrypt_credential


class TestCredentialEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        plaintext = '{"username": "admin", "password": "secret123"}'
        encrypted = encrypt_credential(plaintext)
        decrypted = decrypt_credential(encrypted)
        assert decrypted == plaintext

    def test_encrypted_has_fernet_prefix(self):
        encrypted = encrypt_credential("test")
        assert encrypted.startswith(b"FERNET:")

    def test_decrypt_legacy_plaintext(self):
        """Non-FERNET data returned as-is (legacy compat)."""
        legacy = b'{"username": "old", "password": "legacy"}'
        result = decrypt_credential(legacy)
        assert result == legacy.decode()

    def test_decrypt_string_passthrough(self):
        """String input returned as-is."""
        result = decrypt_credential("plain string")
        assert result == "plain string"

    def test_decrypt_memoryview(self):
        """memoryview input handled correctly."""
        plaintext = '{"key": "value"}'
        encrypted = encrypt_credential(plaintext)
        mv = memoryview(encrypted)
        decrypted = decrypt_credential(mv)
        assert decrypted == plaintext

    def test_different_encryptions_differ(self):
        """Same plaintext produces different ciphertexts (Fernet uses random IV)."""
        e1 = encrypt_credential("same input")
        e2 = encrypt_credential("same input")
        assert e1 != e2

    def test_missing_key_raises(self):
        """RuntimeError when no key is configured."""
        import credential_crypto as cc
        old_fernet = cc._fernet
        cc._fernet = None
        old_env = os.environ.pop("CREDENTIAL_ENCRYPTION_KEY", None)
        try:
            with pytest.raises(RuntimeError):
                cc._get_fernet()
        finally:
            if old_env:
                os.environ["CREDENTIAL_ENCRYPTION_KEY"] = old_env
            cc._fernet = old_fernet
