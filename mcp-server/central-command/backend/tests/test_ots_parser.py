"""Tests for OpenTimestamps parser and OTS file construction.

These functions were previously untested — a parser bug would silently
invalidate every proof. Table-driven tests cover the OTS operation set.
"""

import hashlib
import os
import sys
import types
import pytest

# Stub heavy deps
for mod_name in (
    "fastapi", "pydantic", "sqlalchemy", "sqlalchemy.ext",
    "sqlalchemy.ext.asyncio", "aiohttp",
):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

_fastapi = sys.modules["fastapi"]
_fastapi.APIRouter = lambda **kw: type("R", (), {"post": lambda *a, **k: lambda f: f, "get": lambda *a, **k: lambda f: f})()
_fastapi.HTTPException = Exception
_fastapi.Depends = lambda x: x
_fastapi.BackgroundTasks = object
_fastapi.Request = object
_fastapi.Cookie = lambda default=None, **kw: default
_fastapi.Query = lambda default=None, **kw: default
_pydantic = sys.modules.setdefault("pydantic", types.ModuleType("pydantic"))
_pydantic.BaseModel = object
_pydantic.Field = lambda *a, **kw: None
_sa = sys.modules["sqlalchemy"]
_sa.text = lambda x: x
_sa_async = sys.modules.setdefault("sqlalchemy.ext.asyncio", types.ModuleType("sqlalchemy.ext.asyncio"))
_sa_async.create_async_engine = lambda *a, **kw: None
_sa_async.AsyncSession = object
_sa_async.async_sessionmaker = lambda *a, **kw: None

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from evidence_chain import construct_ots_file, replay_timestamp_operations, extract_calendar_url_from_proof


class TestConstructOTSFile:
    """Verify OTS file format is spec-compliant."""

    def test_magic_header_present(self):
        """OTS file must start with the magic header."""
        hash_bytes = b"\x00" * 32
        calendar_response = b"\x08\x01\x02"
        result = construct_ots_file(hash_bytes, calendar_response)
        expected_magic = b'\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94'
        assert result.startswith(expected_magic), "OTS file must start with magic header"

    def test_version_byte_after_magic(self):
        """Version byte 0x01 follows magic header."""
        hash_bytes = b"\xab" * 32
        result = construct_ots_file(hash_bytes, b"\x08")
        # Magic is 31 bytes, then version
        assert result[31:32] == b'\x01'

    def test_sha256_hash_algorithm_marker(self):
        """Hash algorithm marker is 0x08 (SHA256)."""
        hash_bytes = b"\xcd" * 32
        result = construct_ots_file(hash_bytes, b"\x08")
        # Magic (31) + version (1) = 32, then hash algo
        assert result[32:33] == b'\x08'

    def test_hash_bytes_embedded(self):
        """The 32-byte hash is embedded after the algorithm marker."""
        hash_bytes = bytes.fromhex("a" * 64)
        calendar_response = b"\x08"
        result = construct_ots_file(hash_bytes, calendar_response)
        # Magic (31) + version (1) + algo (1) = 33, then 32 bytes of hash
        assert result[33:65] == hash_bytes

    def test_calendar_response_appended(self):
        """Calendar response is appended after the hash."""
        hash_bytes = b"\x00" * 32
        calendar_response = b"\xde\xad\xbe\xef"
        result = construct_ots_file(hash_bytes, calendar_response)
        # Magic (31) + version (1) + algo (1) + hash (32) = 65, then calendar
        assert result[65:] == calendar_response

    def test_total_length_deterministic(self):
        """Output length = 65 + calendar_response length."""
        hash_bytes = b"\x00" * 32
        for cr_len in (0, 1, 100, 1000):
            cr = b"x" * cr_len
            result = construct_ots_file(hash_bytes, cr)
            assert len(result) == 65 + cr_len


class TestReplayTimestampOperations:
    """Verify OTS operation replay matches spec."""

    def test_sha256_operation(self):
        """0x08 = SHA256 of current state."""
        initial = b"hello"
        ops = b"\x08\x00"  # SHA256, then attestation marker
        result = replay_timestamp_operations(initial, ops)
        assert result == hashlib.sha256(b"hello").digest()

    def test_prepend_operation(self):
        """0xf0 LEN DATA = prepend DATA to current state."""
        initial = b"world"
        # 0xf0 (prepend), 0x05 (length), "hello" (5 bytes), 0x00 (attestation)
        ops = b"\xf0\x05hello\x00"
        result = replay_timestamp_operations(initial, ops)
        assert result == b"helloworld"

    def test_append_operation(self):
        """0xf1 LEN DATA = append DATA to current state."""
        initial = b"hello"
        # 0xf1 (append), 0x05 (length), "world" (5 bytes), 0x00
        ops = b"\xf1\x05world\x00"
        result = replay_timestamp_operations(initial, ops)
        assert result == b"helloworld"

    def test_sha1_operation(self):
        """0x67 = SHA1 of current state."""
        initial = b"test"
        ops = b"\x67\x00"
        result = replay_timestamp_operations(initial, ops)
        assert result == hashlib.sha1(b"test").digest()

    def test_ripemd160_operation(self):
        """0x20 = RIPEMD160 of current state (Bitcoin uses this)."""
        initial = b"test"
        ops = b"\x20\x00"
        result = replay_timestamp_operations(initial, ops)
        h = hashlib.new('ripemd160')
        h.update(b"test")
        assert result == h.digest()

    def test_composite_operations(self):
        """Chain multiple operations: append then SHA256."""
        initial = b"data"
        # append " extra" (6 bytes), then SHA256
        ops = b"\xf1\x06 extra\x08\x00"
        result = replay_timestamp_operations(initial, ops)
        expected = hashlib.sha256(b"data extra").digest()
        assert result == expected

    def test_prepend_then_sha256(self):
        """Real-world pattern: prepend calendar nonce, then hash."""
        initial = b"\x00" * 32
        # prepend 4-byte nonce, then SHA256
        ops = b"\xf0\x04\x01\x02\x03\x04\x08\x00"
        result = replay_timestamp_operations(initial, ops)
        expected = hashlib.sha256(b"\x01\x02\x03\x04" + b"\x00" * 32).digest()
        assert result == expected

    def test_attestation_marker_stops_replay(self):
        """0x00 attestation marker stops replay and returns current state."""
        initial = b"before"
        # SHA256, attestation, then garbage that should be ignored
        ops = b"\x08\x00\xff\xff\xff\xff"
        result = replay_timestamp_operations(initial, ops)
        assert result == hashlib.sha256(b"before").digest()

    def test_empty_ops_returns_initial(self):
        """No operations returns initial hash unchanged (up to 64 bytes)."""
        initial = b"\x42" * 32
        result = replay_timestamp_operations(initial, b"")
        assert result == initial

    def test_malformed_prepend_returns_partial(self):
        """Truncated prepend should not crash."""
        initial = b"test"
        # 0xf0 (prepend), 0x05 (length), but only 2 bytes follow
        ops = b"\xf0\x05ab"
        result = replay_timestamp_operations(initial, ops)
        # Should return current_hash (unchanged) without crashing
        assert result is not None

    def test_invalid_opcode_logs_and_returns(self):
        """Unknown opcode doesn't crash."""
        initial = b"test"
        ops = b"\xa5"  # Invalid opcode
        result = replay_timestamp_operations(initial, ops)
        # Should not crash, returns current state
        assert result is not None


class TestExtractCalendarURL:
    """Verify calendar URL extraction from proof bytes."""

    def test_extract_opentimestamps_url(self):
        """URL containing 'opentimestamps.org' is extracted."""
        # Build proof bytes with embedded URL
        proof = b"\x00\x08\x01" + b"https://alice.btc.calendar.opentimestamps.org\x00" + b"\xff\xff"
        result = extract_calendar_url_from_proof(proof)
        assert result is not None
        assert "opentimestamps.org" in result
        assert "alice" in result

    def test_no_url_returns_none(self):
        """Proof without URL returns None."""
        proof = b"\x00\x08\x01\xab\xcd\xef"
        result = extract_calendar_url_from_proof(proof)
        assert result is None

    def test_non_opentimestamps_url_returns_none(self):
        """URLs that aren't opentimestamps.org are rejected."""
        proof = b"https://evil.com/fake\x00"
        result = extract_calendar_url_from_proof(proof)
        assert result is None

    def test_url_terminated_by_null(self):
        """URL extraction stops at null byte."""
        proof = b"https://alice.btc.calendar.opentimestamps.org\x00garbage"
        result = extract_calendar_url_from_proof(proof)
        assert result is not None
        assert "garbage" not in result
