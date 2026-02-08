"""Tests for authentication module.

Tests password hashing, validation, session tokens, and lockout logic.
"""

import os
import sys
import types
import pytest

# Set SESSION_TOKEN_SECRET before importing auth
os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret-for-auth-tests-only")

# Stub out heavy dependencies so auth.py can import without them installed
for mod_name in ("fastapi", "sqlalchemy", "sqlalchemy.ext", "sqlalchemy.ext.asyncio", "sqlalchemy.ext.asyncio"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

# Provide the names auth.py imports from fastapi / sqlalchemy
sys.modules["fastapi"].Request = object
sys.modules["fastapi"].HTTPException = Exception
sys.modules["fastapi"].Depends = lambda x: x
_sa_mod = sys.modules.setdefault("sqlalchemy", types.ModuleType("sqlalchemy"))
_sa_mod.text = lambda x: x
_sa_async = sys.modules.setdefault("sqlalchemy.ext.asyncio", types.ModuleType("sqlalchemy.ext.asyncio"))
_sa_async.AsyncSession = object

from auth import (
    validate_password_complexity,
    hash_password,
    verify_password,
    generate_session_token,
    hash_token,
)


class TestPasswordComplexity:
    """Test validate_password_complexity()."""

    def test_valid_password(self):
        valid, msg = validate_password_complexity("MySecure@Pass1")
        assert valid is True
        assert msg is None

    def test_too_short(self):
        valid, msg = validate_password_complexity("Short@1a")
        assert valid is False
        assert "12 characters" in msg

    def test_no_uppercase(self):
        valid, msg = validate_password_complexity("mysecure@pass1")
        assert valid is False
        assert "uppercase" in msg

    def test_no_lowercase(self):
        valid, msg = validate_password_complexity("MYSECURE@PASS1")
        assert valid is False
        assert "lowercase" in msg

    def test_no_digit(self):
        valid, msg = validate_password_complexity("MySecure@Passw")
        assert valid is False
        assert "digit" in msg

    def test_no_special_char(self):
        valid, msg = validate_password_complexity("MySecurePass12")
        assert valid is False
        assert "special character" in msg

    def test_common_password(self):
        valid, msg = validate_password_complexity("Password123!")
        assert valid is False
        assert "common" in msg.lower() or "breached" in msg.lower()

    def test_repeating_chars(self):
        valid, msg = validate_password_complexity("Myaaaa@Pass12")
        assert valid is False
        assert "repeating" in msg

    def test_sequential_chars(self):
        valid, msg = validate_password_complexity("My1234@Passwrd")
        assert valid is False
        assert "sequential" in msg

    def test_exactly_12_chars(self):
        valid, msg = validate_password_complexity("Axq@9w2Km8rZ")
        assert valid is True

    def test_empty_password(self):
        valid, msg = validate_password_complexity("")
        assert valid is False


class TestPasswordHashing:
    """Test hash_password() and verify_password()."""

    def test_hash_produces_bcrypt(self):
        hashed = hash_password("TestPass@1234")
        assert hashed.startswith("$2")

    def test_verify_correct_password(self):
        hashed = hash_password("TestPass@1234")
        assert verify_password("TestPass@1234", hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("TestPass@1234")
        assert verify_password("WrongPass@1234", hashed) is False

    def test_verify_legacy_sha256(self):
        """Legacy SHA-256 hashes should still verify."""
        import hashlib
        salt = "testsalt"
        password = "OldPassword@1234"
        stored_hash = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        legacy_hash = f"sha256${salt}${stored_hash}"
        assert verify_password(password, legacy_hash) is True
        assert verify_password("WrongPassword@1", legacy_hash) is False

    def test_verify_invalid_hash_format(self):
        assert verify_password("anything", "invalid_hash") is False

    def test_verify_malformed_sha256(self):
        assert verify_password("anything", "sha256$only_two_parts") is False

    def test_different_passwords_different_hashes(self):
        h1 = hash_password("TestPass@1234")
        h2 = hash_password("TestPass@1234")
        # bcrypt uses random salt, so same password produces different hashes
        assert h1 != h2

    def test_hash_not_plaintext(self):
        hashed = hash_password("TestPass@1234")
        assert "TestPass@1234" not in hashed


class TestSessionTokens:
    """Test generate_session_token() and hash_token()."""

    def test_token_is_string(self):
        token = generate_session_token()
        assert isinstance(token, str)
        assert len(token) > 20

    def test_tokens_are_unique(self):
        tokens = [generate_session_token() for _ in range(100)]
        assert len(set(tokens)) == 100

    def test_hash_token_deterministic(self):
        token = "test-token-abc123"
        h1 = hash_token(token)
        h2 = hash_token(token)
        assert h1 == h2

    def test_hash_token_different_inputs(self):
        h1 = hash_token("token-a")
        h2 = hash_token("token-b")
        assert h1 != h2

    def test_hash_token_is_hex(self):
        h = hash_token("test-token")
        assert all(c in "0123456789abcdef" for c in h)
        assert len(h) == 64  # SHA-256 hex digest

    def test_hash_token_requires_secret(self):
        old_secret = os.environ.get("SESSION_TOKEN_SECRET")
        try:
            del os.environ["SESSION_TOKEN_SECRET"]
            with pytest.raises(RuntimeError, match="SESSION_TOKEN_SECRET"):
                hash_token("test")
        finally:
            if old_secret:
                os.environ["SESSION_TOKEN_SECRET"] = old_secret
