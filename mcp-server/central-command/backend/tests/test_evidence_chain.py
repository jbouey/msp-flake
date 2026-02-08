"""Tests for evidence_chain pure functions.

Tests Ed25519 signature verification, OTS file construction/parsing,
timestamp operation replay, and calendar URL extraction.
"""

import hashlib
import sys
import types
import os
import pytest

# Stub out heavy dependencies that evidence_chain.py imports at module level
# but that these pure-function tests don't need
for mod_name in (
    "fastapi", "pydantic", "sqlalchemy", "sqlalchemy.ext",
    "sqlalchemy.ext.asyncio", "aiohttp",
):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

# Provide names evidence_chain.py imports from fastapi/pydantic/sqlalchemy
_fastapi = sys.modules["fastapi"]
_fastapi.APIRouter = lambda **kw: type("FakeRouter", (), {"post": lambda *a, **k: lambda f: f, "get": lambda *a, **k: lambda f: f})()
_fastapi.HTTPException = Exception
_fastapi.Depends = lambda x: x
_fastapi.BackgroundTasks = object

_pydantic = sys.modules.setdefault("pydantic", types.ModuleType("pydantic"))
_pydantic.BaseModel = object
_pydantic.Field = lambda *a, **kw: None

_sa = sys.modules["sqlalchemy"]
_sa.text = lambda x: x
_sa_async = sys.modules.setdefault("sqlalchemy.ext.asyncio", types.ModuleType("sqlalchemy.ext.asyncio"))
_sa_async.AsyncSession = object

_aiohttp = sys.modules["aiohttp"]
_aiohttp.ClientTimeout = lambda **kw: None
_aiohttp.ClientSession = object
_aiohttp.ClientError = Exception

from evidence_chain import (
    verify_ed25519_signature,
    construct_ots_file,
    parse_ots_file,
    extract_calendar_url_from_proof,
    replay_timestamp_operations,
    read_bitcoin_varint,
    BTC_ATTESTATION_TAG,
)

# Import Ed25519 for generating test keys/signatures
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)


# =============================================================================
# Helpers
# =============================================================================

def _make_keypair():
    """Generate an Ed25519 keypair for testing."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    pub_bytes = public_key.public_bytes_raw()
    return private_key, pub_bytes.hex()


def _sign(private_key, data: bytes) -> str:
    """Sign data and return hex-encoded signature."""
    return private_key.sign(data).hex()


# =============================================================================
# Ed25519 Signature Verification
# =============================================================================

class TestEd25519Verification:
    """Test verify_ed25519_signature()."""

    def test_valid_signature(self):
        priv, pub_hex = _make_keypair()
        data = b"evidence bundle payload"
        sig_hex = _sign(priv, data)
        assert verify_ed25519_signature(data, sig_hex, pub_hex) is True

    def test_wrong_data(self):
        priv, pub_hex = _make_keypair()
        sig_hex = _sign(priv, b"original data")
        assert verify_ed25519_signature(b"tampered data", sig_hex, pub_hex) is False

    def test_wrong_key(self):
        priv1, _ = _make_keypair()
        _, pub_hex2 = _make_keypair()
        data = b"test data"
        sig_hex = _sign(priv1, data)
        assert verify_ed25519_signature(data, sig_hex, pub_hex2) is False

    def test_invalid_signature_length(self):
        _, pub_hex = _make_keypair()
        short_sig = "ab" * 32  # 32 bytes instead of 64
        assert verify_ed25519_signature(b"data", short_sig, pub_hex) is False

    def test_invalid_public_key_length(self):
        priv, _ = _make_keypair()
        sig_hex = _sign(priv, b"data")
        short_key = "ab" * 16  # 16 bytes instead of 32
        assert verify_ed25519_signature(b"data", sig_hex, short_key) is False

    def test_invalid_hex_signature(self):
        _, pub_hex = _make_keypair()
        assert verify_ed25519_signature(b"data", "not-valid-hex", pub_hex) is False

    def test_invalid_hex_public_key(self):
        priv, _ = _make_keypair()
        sig_hex = _sign(priv, b"data")
        assert verify_ed25519_signature(b"data", sig_hex, "not-valid-hex") is False

    def test_empty_data(self):
        priv, pub_hex = _make_keypair()
        sig_hex = _sign(priv, b"")
        assert verify_ed25519_signature(b"", sig_hex, pub_hex) is True

    def test_large_data(self):
        priv, pub_hex = _make_keypair()
        data = b"x" * 100_000
        sig_hex = _sign(priv, data)
        assert verify_ed25519_signature(data, sig_hex, pub_hex) is True

    def test_json_evidence_bundle(self):
        """Simulate what the agent actually signs: sorted JSON."""
        import json
        priv, pub_hex = _make_keypair()
        bundle = {
            "site_id": "test-site-001",
            "checked_at": "2026-02-07T12:00:00+00:00",
            "checks": [{"check": "dns_resolution", "status": "pass"}],
            "summary": {"total": 1, "pass": 1, "fail": 0},
        }
        signed_data = json.dumps(bundle, sort_keys=True).encode("utf-8")
        sig_hex = _sign(priv, signed_data)
        assert verify_ed25519_signature(signed_data, sig_hex, pub_hex) is True


# =============================================================================
# OTS File Construction
# =============================================================================

OTS_MAGIC = b'\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94'


class TestOTSConstruction:
    """Test construct_ots_file()."""

    def test_basic_construction(self):
        hash_bytes = hashlib.sha256(b"test").digest()
        calendar_response = b"\xf1\x04test"  # fake calendar data
        result = construct_ots_file(hash_bytes, calendar_response)

        assert result.startswith(OTS_MAGIC)
        # Version byte
        assert result[len(OTS_MAGIC)] == 0x01
        # Hash algorithm (SHA256 = 0x08)
        assert result[len(OTS_MAGIC) + 1] == 0x08
        # Hash bytes
        offset = len(OTS_MAGIC) + 2
        assert result[offset:offset + 32] == hash_bytes
        # Calendar response appended
        assert result[offset + 32:] == calendar_response

    def test_construct_includes_version_byte(self):
        """construct_ots_file includes version byte 0x01 before hash algo."""
        hash_bytes = hashlib.sha256(b"version test").digest()
        ots_file = construct_ots_file(hash_bytes, b"\xf1\x04data")
        # parse_ots_file expects no version byte (v0 format), so construct
        # output (v1 with version byte) needs the upgrade_pending_proofs
        # v0→v1 fixup to be parsed. Verify the version byte is present.
        offset = len(OTS_MAGIC)
        assert ots_file[offset] == 0x01  # version
        assert ots_file[offset + 1] == 0x08  # SHA256

    def test_empty_calendar_response(self):
        hash_bytes = hashlib.sha256(b"empty").digest()
        result = construct_ots_file(hash_bytes, b"")
        assert len(result) == len(OTS_MAGIC) + 2 + 32


# =============================================================================
# OTS File Parsing
# =============================================================================

class TestOTSParsing:
    """Test parse_ots_file()."""

    def test_valid_ots_file(self):
        """parse_ots_file expects v0 format: MAGIC + 0x08 + hash (no version byte)."""
        hash_bytes = hashlib.sha256(b"parse test").digest()
        timestamp_data = b"\xf1\x08testdata" + b"https://bob.btc.calendar.opentimestamps.org/ts"
        ots_file = OTS_MAGIC + b'\x08' + hash_bytes + timestamp_data
        parsed = parse_ots_file(ots_file)

        assert parsed is not None
        assert parsed["hash_bytes"] == hash_bytes
        assert parsed["has_bitcoin"] is False

    def test_detects_bitcoin_attestation(self):
        hash_bytes = hashlib.sha256(b"btc test").digest()
        BTC_TAG = b'\x05\x88\x96\x0d\x73\xd7\x19\x01'
        timestamp_data = b"\xf1\x04test" + BTC_TAG + b'\x00\x00\x10\x00'
        ots_file = OTS_MAGIC + b'\x08' + hash_bytes + timestamp_data
        parsed = parse_ots_file(ots_file)

        assert parsed is not None
        assert parsed["has_bitcoin"] is True

    def test_rejects_non_ots_data(self):
        assert parse_ots_file(b"not an OTS file") is None

    def test_rejects_wrong_hash_algo(self):
        hash_bytes = b'\x00' * 32
        # Use 0x09 instead of 0x08 (SHA256)
        ots_file = OTS_MAGIC + b'\x09' + hash_bytes
        assert parse_ots_file(ots_file) is None

    def test_rejects_truncated(self):
        # OTS magic + version byte but no hash
        ots_file = OTS_MAGIC + b'\x08'
        assert parse_ots_file(ots_file) is None

    def test_empty_bytes(self):
        assert parse_ots_file(b"") is None


# =============================================================================
# Calendar URL Extraction
# =============================================================================

class TestCalendarURLExtraction:
    """Test extract_calendar_url_from_proof()."""

    def test_extracts_pool_url(self):
        data = b"\xf1\x04xxxx" + b"https://a.pool.opentimestamps.org/digest"
        url = extract_calendar_url_from_proof(data)
        assert url is not None
        assert "opentimestamps.org" in url

    def test_extracts_alice_calendar(self):
        data = b"prefix" + b"https://alice.btc.calendar.opentimestamps.org/timestamp/abc123"
        url = extract_calendar_url_from_proof(data)
        assert url is not None
        assert "alice.btc.calendar" in url

    def test_no_url_present(self):
        data = b"\xf1\x04\x00\x00just binary data"
        url = extract_calendar_url_from_proof(data)
        assert url is None

    def test_non_ots_url_ignored(self):
        data = b"https://example.com/not-ots"
        url = extract_calendar_url_from_proof(data)
        assert url is None

    def test_empty_input(self):
        assert extract_calendar_url_from_proof(b"") is None


# =============================================================================
# Timestamp Operation Replay
# =============================================================================

class TestTimestampReplay:
    """Test replay_timestamp_operations()."""

    def test_sha256_operation(self):
        """0x08 should SHA256-hash the current state."""
        initial = hashlib.sha256(b"test").digest()
        # Single SHA256 op followed by attestation marker
        ops = bytes([0x08, 0x00])
        result = replay_timestamp_operations(initial, ops)
        expected = hashlib.sha256(initial).digest()
        assert result == expected

    def test_append_operation(self):
        """0xf1 LEN DATA should append data."""
        initial = b"A" * 32
        append_data = b"BCDE"
        ops = bytes([0xf1, 4]) + append_data + bytes([0x00])
        result = replay_timestamp_operations(initial, ops)
        assert result == initial + append_data

    def test_prepend_operation(self):
        """0xf0 LEN DATA should prepend data."""
        initial = b"X" * 32
        prepend_data = b"YZ"
        ops = bytes([0xf0, 2]) + prepend_data + bytes([0x00])
        result = replay_timestamp_operations(initial, ops)
        assert result == prepend_data + initial

    def test_append_then_sha256(self):
        """Append + SHA256 chain."""
        initial = hashlib.sha256(b"chain").digest()
        suffix = b"\x01\x02\x03\x04"
        ops = bytes([0xf1, 4]) + suffix + bytes([0x08, 0x00])
        result = replay_timestamp_operations(initial, ops)
        expected = hashlib.sha256(initial + suffix).digest()
        assert result == expected

    def test_empty_operations(self):
        """Empty ops with no attestation returns None (falls through)."""
        initial = b"A" * 32
        result = replay_timestamp_operations(initial, b"")
        # Falls through without attestation marker - returns current state
        # but only if len <= 64
        assert result == initial

    def test_returns_none_for_oversized_state(self):
        """If state grows beyond 64 bytes without attestation, returns None."""
        initial = b"A" * 32
        # Append enough data to push past 64 bytes, no attestation
        big_append = b"B" * 40
        ops = bytes([0xf1, 40]) + big_append
        result = replay_timestamp_operations(initial, ops)
        assert result is None


# =============================================================================
# Bitcoin Varint Parsing
# =============================================================================

class TestBitcoinVarint:
    """Test read_bitcoin_varint()."""

    def test_single_byte_zero(self):
        assert read_bitcoin_varint(b"\x00", 0) == (0, 1)

    def test_single_byte_max(self):
        """Values 0-252 are encoded as a single byte."""
        assert read_bitcoin_varint(b"\xfc", 0) == (252, 1)

    def test_two_byte_encoding(self):
        """0xFD prefix = 2-byte little-endian value."""
        # 0xFD 0x00 0x01 = 256
        data = bytes([0xFD, 0x00, 0x01])
        assert read_bitcoin_varint(data, 0) == (256, 3)

    def test_two_byte_block_height(self):
        """Typical Bitcoin block height ~880,000 needs 4-byte encoding."""
        # 880,000 = 0x0D6F80 → 0xFE prefix + 4-byte LE
        height = 880000
        data = bytes([0xFE]) + height.to_bytes(4, 'little')
        value, consumed = read_bitcoin_varint(data, 0)
        assert value == 880000
        assert consumed == 5

    def test_four_byte_encoding(self):
        """0xFE prefix = 4-byte little-endian value."""
        # 0xFE + 4 bytes LE for 70000
        height = 70000
        data = bytes([0xFE]) + height.to_bytes(4, 'little')
        assert read_bitcoin_varint(data, 0) == (70000, 5)

    def test_eight_byte_encoding(self):
        """0xFF prefix = 8-byte little-endian value."""
        big_val = 2**40
        data = bytes([0xFF]) + big_val.to_bytes(8, 'little')
        assert read_bitcoin_varint(data, 0) == (big_val, 9)

    def test_offset_position(self):
        """Read varint at non-zero offset."""
        # Prefix garbage + varint at pos 5
        data = b"\x00\x00\x00\x00\x00\x42"
        assert read_bitcoin_varint(data, 5) == (0x42, 1)

    def test_after_btc_attestation_tag(self):
        """Parse block height after BTC attestation tag (real-world layout)."""
        height = 880123
        tag = BTC_ATTESTATION_TAG
        varint_data = bytes([0xFE]) + height.to_bytes(4, 'little')
        proof_data = b"\xf1\x04test" + tag + varint_data
        pos = proof_data.find(tag) + len(tag)
        value, consumed = read_bitcoin_varint(proof_data, pos)
        assert value == 880123
        assert consumed == 5

    def test_empty_data_raises(self):
        with pytest.raises(IndexError):
            read_bitcoin_varint(b"", 0)

    def test_truncated_two_byte_raises(self):
        with pytest.raises(IndexError):
            read_bitcoin_varint(bytes([0xFD, 0x01]), 0)  # needs 3 bytes

    def test_truncated_four_byte_raises(self):
        with pytest.raises(IndexError):
            read_bitcoin_varint(bytes([0xFE, 0x01, 0x02]), 0)  # needs 5 bytes

    def test_out_of_bounds_raises(self):
        with pytest.raises(IndexError):
            read_bitcoin_varint(b"\x00", 5)

    def test_current_block_height_range(self):
        """Current Bitcoin block heights (~880K) parse correctly."""
        for height in [879000, 880000, 881000, 900000]:
            data = bytes([0xFE]) + height.to_bytes(4, 'little')
            assert read_bitcoin_varint(data, 0)[0] == height
