"""
Tests for OpenTimestamps blockchain anchoring module.

Tests the OTS client functionality including:
- Hash submission to calendar servers
- Proof serialization/deserialization
- Proof upgrade handling
- Configuration validation
"""

import pytest
import asyncio
import json
import hashlib
import base64
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Import the module under test
from compliance_agent.opentimestamps import (
    OTSClient,
    OTSConfig,
    OTSProof,
    compute_bundle_hash,
    timestamp_evidence_hash,
)


class TestOTSProof:
    """Tests for OTSProof dataclass."""

    def test_create_proof(self):
        """Test creating an OTS proof."""
        proof = OTSProof(
            bundle_hash="a" * 64,
            bundle_id="test-bundle-123",
            proof_data="dGVzdCBwcm9vZg==",  # base64 of "test proof"
            calendar_url="https://a.pool.opentimestamps.org",
            submitted_at=datetime.now(timezone.utc),
        )

        assert proof.bundle_hash == "a" * 64
        assert proof.bundle_id == "test-bundle-123"
        assert proof.status == "pending"
        assert proof.bitcoin_txid is None
        assert proof.bitcoin_block is None

    def test_proof_to_dict(self):
        """Test converting proof to dictionary."""
        now = datetime.now(timezone.utc)
        proof = OTSProof(
            bundle_hash="a" * 64,
            bundle_id="test-bundle-123",
            proof_data="dGVzdCBwcm9vZg==",
            calendar_url="https://a.pool.opentimestamps.org",
            submitted_at=now,
            status="anchored",
            bitcoin_block=800000,
        )

        d = proof.to_dict()

        assert d["bundle_hash"] == "a" * 64
        assert d["bundle_id"] == "test-bundle-123"
        assert d["status"] == "anchored"
        assert d["bitcoin_block"] == 800000
        assert d["submitted_at"] == now.isoformat()

    def test_proof_from_dict(self):
        """Test creating proof from dictionary."""
        now = datetime.now(timezone.utc)
        data = {
            "bundle_hash": "b" * 64,
            "bundle_id": "test-bundle-456",
            "proof_data": "dGVzdA==",
            "calendar_url": "https://b.pool.opentimestamps.org",
            "submitted_at": now.isoformat(),
            "status": "pending",
            "bitcoin_txid": None,
            "bitcoin_block": None,
        }

        proof = OTSProof.from_dict(data)

        assert proof.bundle_hash == "b" * 64
        assert proof.bundle_id == "test-bundle-456"
        assert proof.status == "pending"

    def test_proof_roundtrip(self):
        """Test serialization/deserialization roundtrip."""
        now = datetime.now(timezone.utc)
        original = OTSProof(
            bundle_hash="c" * 64,
            bundle_id="test-bundle-789",
            proof_data="cm91bmR0cmlw",
            calendar_url="https://alice.btc.calendar.opentimestamps.org",
            submitted_at=now,
            status="anchored",
            bitcoin_block=800001,
            anchored_at=now,
        )

        d = original.to_dict()
        restored = OTSProof.from_dict(d)

        assert restored.bundle_hash == original.bundle_hash
        assert restored.bundle_id == original.bundle_id
        assert restored.status == original.status
        assert restored.bitcoin_block == original.bitcoin_block


class TestOTSConfig:
    """Tests for OTSConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = OTSConfig()

        assert config.enabled is True
        assert len(config.calendars) == 4
        assert config.timeout_seconds == 30
        assert config.auto_upgrade is True

    def test_custom_config(self):
        """Test custom configuration."""
        config = OTSConfig(
            enabled=False,
            calendars=["https://custom.calendar.org"],
            timeout_seconds=60,
        )

        assert config.enabled is False
        assert len(config.calendars) == 1
        assert config.timeout_seconds == 60


class TestComputeBundleHash:
    """Tests for compute_bundle_hash function."""

    def test_compute_hash(self):
        """Test computing SHA256 hash of bundle JSON."""
        bundle_json = '{"bundle_id": "test", "check": "patching"}'
        hash_result = compute_bundle_hash(bundle_json)

        # Verify it's a valid SHA256 hex string
        assert len(hash_result) == 64
        assert all(c in '0123456789abcdef' for c in hash_result)

        # Verify deterministic
        assert compute_bundle_hash(bundle_json) == hash_result

    def test_different_content_different_hash(self):
        """Test that different content produces different hashes."""
        hash1 = compute_bundle_hash('{"a": 1}')
        hash2 = compute_bundle_hash('{"a": 2}')

        assert hash1 != hash2


class TestOTSClient:
    """Tests for OTSClient class."""

    @pytest.fixture
    def config(self, tmp_path):
        """Create test config with temp directory."""
        return OTSConfig(
            enabled=True,
            calendars=["https://test.calendar.org"],
            timeout_seconds=5,
            proof_dir=tmp_path / "proofs",
        )

    @pytest.fixture
    def client(self, config):
        """Create test client."""
        return OTSClient(config)

    def test_client_init(self, client):
        """Test client initialization."""
        assert client.config.enabled is True
        assert client._pending_proofs == {}

    def test_client_init_creates_proof_dir(self, config):
        """Test client creates proof directory."""
        client = OTSClient(config)
        assert config.proof_dir.exists()

    @pytest.mark.asyncio
    async def test_submit_hash_invalid_length(self, client):
        """Test submit_hash rejects invalid hash length."""
        result = await client.submit_hash("abc", "test-bundle")
        assert result is None

    @pytest.mark.asyncio
    async def test_submit_hash_invalid_hex(self, client):
        """Test submit_hash rejects invalid hex."""
        result = await client.submit_hash("z" * 64, "test-bundle")
        assert result is None

    @pytest.mark.asyncio
    async def test_submit_hash_disabled(self):
        """Test submit_hash returns None when disabled."""
        config = OTSConfig(enabled=False)
        client = OTSClient(config)

        result = await client.submit_hash("a" * 64, "test-bundle")
        assert result is None

    @pytest.mark.asyncio
    async def test_submit_hash_mocked_success(self, client):
        """Test successful hash submission with mocked HTTP."""
        mock_proof_data = b'\x00\x01\x02'  # Fake OTS proof bytes

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.read = AsyncMock(return_value=mock_proof_data)

            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_context.__aexit__ = AsyncMock()

            mock_session = AsyncMock()
            mock_session.post = MagicMock(return_value=mock_context)
            mock_session.closed = False

            client._session = mock_session

            result = await client.submit_hash("a" * 64, "test-bundle")

            assert result is not None
            assert result.bundle_id == "test-bundle"
            assert result.status == "pending"
            assert result.proof_data == base64.b64encode(mock_proof_data).decode('ascii')

    @pytest.mark.asyncio
    async def test_verify_pending_proof(self, client):
        """Test verifying a pending proof."""
        proof = OTSProof(
            bundle_hash="a" * 64,
            bundle_id="test-bundle",
            proof_data="dGVzdA==",
            calendar_url="https://test.calendar.org",
            submitted_at=datetime.now(timezone.utc),
            status="pending",
        )

        is_valid, message = await client.verify_proof(proof)

        assert is_valid is True
        assert "pending" in message.lower()

    @pytest.mark.asyncio
    async def test_verify_anchored_proof(self, client):
        """Test verifying an anchored proof."""
        proof = OTSProof(
            bundle_hash="a" * 64,
            bundle_id="test-bundle",
            proof_data="dGVzdA==",
            calendar_url="https://test.calendar.org",
            submitted_at=datetime.now(timezone.utc),
            status="anchored",
            bitcoin_block=800000,
        )

        is_valid, message = await client.verify_proof(proof)

        assert is_valid is True
        assert "800000" in message

    @pytest.mark.asyncio
    async def test_verify_failed_proof(self, client):
        """Test verifying a failed proof."""
        proof = OTSProof(
            bundle_hash="a" * 64,
            bundle_id="test-bundle",
            proof_data="dGVzdA==",
            calendar_url="https://test.calendar.org",
            submitted_at=datetime.now(timezone.utc),
            status="failed",
            error="Connection timeout",
        )

        is_valid, message = await client.verify_proof(proof)

        assert is_valid is False
        assert "failed" in message.lower()

    def test_has_bitcoin_attestation(self, client):
        """Test Bitcoin attestation detection."""
        # OTS Bitcoin marker: 0x0588960d73d71901
        marker = bytes.fromhex("0588960d73d71901")

        # Proof without marker
        proof_without = b'\x00\x01\x02\x03'
        assert client._has_bitcoin_attestation(proof_without) is False

        # Proof with marker
        proof_with = b'\x00\x01' + marker + b'\x00\x00\x00\x00\x00\x00\x00\x00'
        assert client._has_bitcoin_attestation(proof_with) is True

    def test_get_pending_count(self, client):
        """Test getting pending proof count."""
        assert client.get_pending_count() == 0

        # Add a pending proof
        client._pending_proofs["test-1"] = OTSProof(
            bundle_hash="a" * 64,
            bundle_id="test-1",
            proof_data="test",
            calendar_url="https://test.org",
            submitted_at=datetime.now(timezone.utc),
        )

        assert client.get_pending_count() == 1

    def test_get_proof(self, client):
        """Test getting a cached proof."""
        proof = OTSProof(
            bundle_hash="a" * 64,
            bundle_id="test-1",
            proof_data="test",
            calendar_url="https://test.org",
            submitted_at=datetime.now(timezone.utc),
        )
        client._pending_proofs["test-1"] = proof

        result = client.get_proof("test-1")
        assert result is proof

        result = client.get_proof("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_close(self, client):
        """Test closing the client."""
        # Create a mock session
        mock_session = AsyncMock()
        mock_session.closed = False
        client._session = mock_session

        await client.close()

        mock_session.close.assert_called_once()


class TestTimestampEvidenceHash:
    """Tests for convenience function timestamp_evidence_hash."""

    @pytest.mark.asyncio
    async def test_timestamp_hash_disabled(self):
        """Test timestamping when OTS is disabled."""
        config = OTSConfig(enabled=False)
        result = await timestamp_evidence_hash("a" * 64, "test-bundle", config)
        assert result is None


class TestOTSIntegration:
    """Integration tests for OTS with real (mocked) calendar responses."""

    @pytest.mark.asyncio
    async def test_proof_save_and_load(self, tmp_path):
        """Test saving and loading proofs from disk."""
        config = OTSConfig(
            enabled=True,
            proof_dir=tmp_path / "proofs",
        )
        client = OTSClient(config)

        # Create and save a proof
        proof = OTSProof(
            bundle_hash="a" * 64,
            bundle_id="test-bundle",
            proof_data="dGVzdCBwcm9vZg==",
            calendar_url="https://test.calendar.org",
            submitted_at=datetime.now(timezone.utc),
        )

        await client._save_proof(proof)

        # Verify file exists
        proof_file = config.proof_dir / "test-bundle.ots.json"
        assert proof_file.exists()

        # Load and verify content
        with open(proof_file) as f:
            loaded_data = json.load(f)

        assert loaded_data["bundle_id"] == "test-bundle"
        assert loaded_data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_load_pending_proofs(self, tmp_path):
        """Test loading pending proofs from disk."""
        config = OTSConfig(
            enabled=True,
            proof_dir=tmp_path / "proofs",
        )
        config.proof_dir.mkdir(parents=True)

        # Create a proof file
        proof_data = {
            "bundle_hash": "a" * 64,
            "bundle_id": "saved-bundle",
            "proof_data": "dGVzdA==",
            "calendar_url": "https://test.org",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }

        with open(config.proof_dir / "saved-bundle.ots.json", "w") as f:
            json.dump(proof_data, f)

        # Load proofs
        client = OTSClient(config)
        proofs = await client.load_pending_proofs()

        assert len(proofs) == 1
        assert proofs[0].bundle_id == "saved-bundle"
        assert "saved-bundle" in client._pending_proofs
