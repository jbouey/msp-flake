"""
Tests for cryptographic operations.
"""

import pytest
from pathlib import Path
import tempfile
import json

from compliance_agent.crypto import (
    Ed25519Signer,
    Ed25519Verifier,
    generate_keypair,
    sha256_hash,
    verify_hash,
    ensure_signing_key
)


def test_generate_keypair():
    """Test Ed25519 keypair generation."""
    private_key, public_key = generate_keypair()

    assert len(private_key) == 32
    assert len(public_key) == 32
    assert private_key != public_key


def test_sign_and_verify_bytes():
    """Test signing and verifying byte data."""
    private_key, public_key = generate_keypair()

    # Create temp file for private key
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(private_key)
        key_path = Path(f.name)

    try:
        # Sign data
        signer = Ed25519Signer(key_path)
        data = b"Test data for signing"
        signature = signer.sign(data)

        assert len(signature) == 64

        # Verify signature
        verifier = Ed25519Verifier(public_key)
        assert verifier.verify(data, signature) is True

        # Verify wrong data fails
        wrong_data = b"Wrong data"
        assert verifier.verify(wrong_data, signature) is False

    finally:
        key_path.unlink()


def test_sign_and_verify_string():
    """Test signing and verifying string data."""
    private_key, public_key = generate_keypair()

    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(private_key)
        key_path = Path(f.name)

    try:
        signer = Ed25519Signer(key_path)
        data = "Test string for signing"
        signature = signer.sign(data)

        verifier = Ed25519Verifier(public_key)
        assert verifier.verify(data, signature) is True
        assert verifier.verify("Wrong string", signature) is False

    finally:
        key_path.unlink()


def test_sign_and_verify_json():
    """Test signing and verifying JSON data."""
    private_key, public_key = generate_keypair()

    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(private_key)
        key_path = Path(f.name)

    try:
        signer = Ed25519Signer(key_path)

        data = {
            "bundle_id": "test-001",
            "site_id": "clinic-001",
            "timestamp": "2025-11-06T12:00:00Z",
            "check": "patching"
        }

        signature = signer.sign(data)

        verifier = Ed25519Verifier(public_key)
        assert verifier.verify(data, signature) is True

        # Verify modified data fails
        modified_data = data.copy()
        modified_data["check"] = "backup"
        assert verifier.verify(modified_data, signature) is False

    finally:
        key_path.unlink()


def test_sign_file():
    """Test file signing."""
    private_key, public_key = generate_keypair()

    with tempfile.NamedTemporaryFile(delete=False) as key_file:
        key_file.write(private_key)
        key_path = Path(key_file.name)

    with tempfile.NamedTemporaryFile(delete=False, mode='w') as data_file:
        data_file.write("File content to sign")
        data_file.flush()
        data_path = Path(data_file.name)

    try:
        signer = Ed25519Signer(key_path)
        signature = signer.sign_file(data_path)

        verifier = Ed25519Verifier(public_key)
        assert verifier.verify_file(data_path, signature) is True

    finally:
        key_path.unlink()
        data_path.unlink()


def test_get_public_key():
    """Test extracting public key from signer."""
    private_key, expected_public_key = generate_keypair()

    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(private_key)
        key_path = Path(f.name)

    try:
        signer = Ed25519Signer(key_path)
        public_key_bytes = signer.get_public_key_bytes()

        assert public_key_bytes == expected_public_key

        # Test PEM format
        pem = signer.get_public_key_pem()
        assert '-----BEGIN PUBLIC KEY-----' in pem
        assert '-----END PUBLIC KEY-----' in pem

    finally:
        key_path.unlink()


def test_sha256_hash():
    """Test SHA256 hashing."""
    # Hash bytes
    data_bytes = b"Test data"
    hash1 = sha256_hash(data_bytes)
    assert len(hash1) == 64  # 32 bytes = 64 hex chars

    # Hash string
    data_str = "Test data"
    hash2 = sha256_hash(data_str)
    assert hash1 == hash2

    # Hash file
    with tempfile.NamedTemporaryFile(delete=False, mode='w') as f:
        f.write("Test data")
        f.flush()
        file_path = Path(f.name)

    try:
        hash3 = sha256_hash(file_path)
        assert hash1 == hash3
    finally:
        file_path.unlink()


def test_verify_hash():
    """Test hash verification."""
    data = b"Test data"
    correct_hash = sha256_hash(data)
    wrong_hash = sha256_hash(b"Wrong data")

    assert verify_hash(data, correct_hash) is True
    assert verify_hash(data, wrong_hash) is False


def test_invalid_private_key():
    """Test loading invalid private key fails."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"not a valid key")
        key_path = Path(f.name)

    try:
        with pytest.raises(ValueError, match="Failed to load private key"):
            Ed25519Signer(key_path)
    finally:
        key_path.unlink()


def test_invalid_public_key():
    """Test loading invalid public key fails."""
    with pytest.raises(ValueError, match="Failed to load public key"):
        Ed25519Verifier(b"not a valid key")


def test_ensure_signing_key_creates_new():
    """Test ensure_signing_key creates new key if missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = Path(tmpdir) / "signing.key"
        pub_path = key_path.with_suffix('.pub')

        # Key should not exist yet
        assert not key_path.exists()
        assert not pub_path.exists()

        # Ensure key (should create)
        was_generated, public_key_hex = ensure_signing_key(key_path)

        assert was_generated is True
        assert len(public_key_hex) == 64  # 32 bytes = 64 hex chars
        assert key_path.exists()
        assert pub_path.exists()

        # Check permissions
        assert (key_path.stat().st_mode & 0o777) == 0o600
        assert (pub_path.stat().st_mode & 0o777) == 0o644

        # Verify key works
        signer = Ed25519Signer(key_path)
        signature = signer.sign(b"test data")
        assert len(signature) == 64

        # Verify public key matches
        assert signer.get_public_key_bytes().hex() == public_key_hex


def test_ensure_signing_key_uses_existing():
    """Test ensure_signing_key uses existing key if present."""
    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = Path(tmpdir) / "signing.key"

        # Create key first time
        was_generated1, public_key_hex1 = ensure_signing_key(key_path)
        assert was_generated1 is True

        # Call again - should not regenerate
        was_generated2, public_key_hex2 = ensure_signing_key(key_path)
        assert was_generated2 is False
        assert public_key_hex2 == public_key_hex1  # Same key


def test_ensure_signing_key_nested_dir():
    """Test ensure_signing_key creates nested directories."""
    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = Path(tmpdir) / "nested" / "deep" / "signing.key"

        # Nested dirs should not exist yet
        assert not key_path.parent.exists()

        # Ensure key (should create dirs)
        was_generated, _ = ensure_signing_key(key_path)

        assert was_generated is True
        assert key_path.exists()
        assert key_path.parent.exists()
