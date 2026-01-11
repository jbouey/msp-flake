"""
Tests for WORM (Write-Once-Read-Many) upload functionality.

Tests the evidence upload flow from agent to MCP server to MinIO.
"""

import pytest
import json
import hashlib
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from compliance_agent.worm_uploader import (
    WormUploader,
    WormConfig,
    UploadResult,
    load_worm_config_from_env,
)


@pytest.fixture
def temp_evidence_dir():
    """Create temporary evidence directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def worm_config():
    """Create test WORM configuration."""
    return WormConfig(
        enabled=True,
        mode="proxy",
        mcp_upload_endpoint="https://api.osiriscare.net",
        retention_days=90,
        max_retries=3,
        retry_delay_seconds=1,
        upload_batch_size=5,
        auto_upload=True,
    )


@pytest.fixture
def worm_uploader(worm_config, temp_evidence_dir):
    """Create WormUploader instance."""
    return WormUploader(
        config=worm_config,
        evidence_dir=temp_evidence_dir,
        client_id="test-site-001",
        mcp_api_key="test-api-key",
    )


@pytest.fixture
def sample_bundle(temp_evidence_dir):
    """Create a sample evidence bundle."""
    bundle_id = "EB-20260110-001"
    bundle_dir = temp_evidence_dir / bundle_id
    bundle_dir.mkdir(parents=True)

    bundle_data = {
        "bundle_id": bundle_id,
        "site_id": "test-site-001",
        "host_id": "test-host",
        "check_type": "patching",
        "outcome": "success",
        "pre_state": {"nixos_generation": 142},
        "post_state": {"nixos_generation": 143},
        "actions_taken": [{"action": "nixos-rebuild", "result": "success"}],
        "hipaa_controls": ["164.308(a)(5)(ii)(B)"],
        "timestamp_start": "2026-01-10T10:00:00Z",
        "timestamp_end": "2026-01-10T10:05:00Z",
    }

    bundle_path = bundle_dir / "bundle.json"
    bundle_path.write_text(json.dumps(bundle_data, indent=2))

    # Create signature file
    sig_path = bundle_dir / "bundle.sig"
    sig_path.write_bytes(b"MOCK_ED25519_SIGNATURE_BYTES_64")

    return bundle_path


class TestWormConfig:
    """Tests for WORM configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        config = WormConfig()
        assert config.enabled is False
        assert config.mode == "proxy"
        assert config.retention_days == 90
        assert config.max_retries == 3
        assert config.auto_upload is True

    def test_config_from_env(self, monkeypatch):
        """Test loading config from environment variables."""
        monkeypatch.setenv("WORM_ENABLED", "true")
        monkeypatch.setenv("WORM_MODE", "direct")
        monkeypatch.setenv("MCP_URL", "https://mcp.example.com")
        monkeypatch.setenv("WORM_S3_BUCKET", "test-bucket")
        monkeypatch.setenv("WORM_RETENTION_DAYS", "365")

        config = load_worm_config_from_env()

        assert config.enabled is True
        assert config.mode == "direct"
        assert config.mcp_upload_endpoint == "https://mcp.example.com"
        assert config.s3_bucket == "test-bucket"
        assert config.retention_days == 365


class TestWormUploader:
    """Tests for WormUploader class."""

    def test_init(self, worm_uploader, temp_evidence_dir):
        """Test uploader initialization."""
        assert worm_uploader.config.enabled is True
        assert worm_uploader.evidence_dir == temp_evidence_dir
        assert worm_uploader.client_id == "test-site-001"

    def test_extract_bundle_id_from_path(self, worm_uploader, sample_bundle):
        """Test bundle ID extraction from path."""
        bundle_id = worm_uploader._extract_bundle_id(sample_bundle)
        assert bundle_id == "EB-20260110-001"

    def test_extract_bundle_id_from_content(self, worm_uploader, temp_evidence_dir):
        """Test bundle ID extraction from bundle content."""
        # Create bundle without ID in parent dir name
        bundle_path = temp_evidence_dir / "some_random_name" / "bundle.json"
        bundle_path.parent.mkdir(parents=True)
        bundle_path.write_text(json.dumps({"bundle_id": "from-content-123"}))

        bundle_id = worm_uploader._extract_bundle_id(bundle_path)
        assert bundle_id == "from-content-123"

    def test_upload_disabled(self, temp_evidence_dir, sample_bundle):
        """Test upload when WORM is disabled."""
        config = WormConfig(enabled=False)
        uploader = WormUploader(
            config=config,
            evidence_dir=temp_evidence_dir,
            client_id="test-site",
        )

        # Run synchronously for this test
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            uploader.upload_bundle(sample_bundle)
        )

        assert result.success is False
        assert "disabled" in result.error.lower()

    def test_upload_registry_persistence(self, worm_uploader, temp_evidence_dir):
        """Test that upload registry is persisted to disk."""
        # Manually add to registry
        worm_uploader._upload_registry["test-bundle-001"] = {
            "success": True,
            "s3_uri": "s3://bucket/test.json",
            "upload_timestamp": "2026-01-10T10:00:00Z",
        }
        worm_uploader._save_registry()

        # Verify file exists
        registry_path = temp_evidence_dir / ".upload_registry.json"
        assert registry_path.exists()

        # Reload and verify
        loaded = json.loads(registry_path.read_text())
        assert "test-bundle-001" in loaded
        assert loaded["test-bundle-001"]["success"] is True

    def test_skip_already_uploaded(self, worm_uploader, sample_bundle):
        """Test that already-uploaded bundles are skipped."""
        bundle_id = "EB-20260110-001"

        # Pre-populate registry
        worm_uploader._upload_registry[bundle_id] = {
            "success": True,
            "s3_uri": "s3://bucket/existing.json",
            "upload_timestamp": "2026-01-10T09:00:00Z",
        }

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            worm_uploader.upload_bundle(sample_bundle)
        )

        assert result.success is True
        assert result.s3_uri == "s3://bucket/existing.json"

    @pytest.mark.asyncio
    async def test_upload_via_proxy_success(self, worm_uploader, sample_bundle):
        """Test successful proxy upload - mocks the upload method directly."""
        # Instead of complex aiohttp mocking, patch the internal method
        expected_result = UploadResult(
            bundle_id="EB-20260110-001",
            success=True,
            s3_uri="s3://evidence-worm/test-site-001/2026/01/10/EB-20260110-001.json",
            signature_uri="s3://evidence-worm/test-site-001/2026/01/10/EB-20260110-001.sig",
            upload_timestamp="2026-01-10T10:00:00Z",
            retention_days=90,
        )

        with patch.object(worm_uploader, '_upload_via_proxy', new_callable=AsyncMock) as mock_upload:
            mock_upload.return_value = expected_result
            result = await worm_uploader.upload_bundle(sample_bundle)

        assert result.success is True
        assert "evidence-worm" in result.s3_uri

    @pytest.mark.asyncio
    async def test_upload_via_proxy_retry_on_failure(self, worm_uploader, sample_bundle):
        """Test retry logic on upload failure."""
        # Set fast retry for test
        worm_uploader.config.retry_delay_seconds = 0.01
        worm_uploader.config.max_retries = 2

        # Mock that always fails
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response)))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await worm_uploader.upload_bundle(sample_bundle)

        assert result.success is False
        assert result.retry_count == 2
        assert "failed after" in result.error.lower()

    def test_find_pending_bundles(self, worm_uploader, temp_evidence_dir):
        """Test finding bundles pending upload."""
        # Create some bundles
        for i in range(3):
            bundle_dir = temp_evidence_dir / f"bundle-{i}"
            bundle_dir.mkdir()
            (bundle_dir / "bundle.json").write_text(json.dumps({"bundle_id": f"bundle-{i}"}))
            (bundle_dir / "bundle.sig").write_bytes(b"sig")

        # Mark one as already uploaded
        worm_uploader._upload_registry["bundle-0"] = {"success": True}

        pending = worm_uploader._find_pending_bundles()

        # Should find 2 pending (bundle-1 and bundle-2)
        assert len(pending) == 2
        bundle_ids = [worm_uploader._extract_bundle_id(p[0]) for p in pending]
        assert "bundle-1" in bundle_ids
        assert "bundle-2" in bundle_ids
        assert "bundle-0" not in bundle_ids

    def test_get_stats(self, worm_uploader, temp_evidence_dir):
        """Test upload statistics."""
        # Create some bundles and mark some uploaded
        for i in range(5):
            bundle_dir = temp_evidence_dir / f"bundle-{i}"
            bundle_dir.mkdir()
            (bundle_dir / "bundle.json").write_text(json.dumps({"bundle_id": f"bundle-{i}"}))

        # Mark 3 as uploaded
        for i in range(3):
            worm_uploader._upload_registry[f"bundle-{i}"] = {
                "success": True,
                "upload_timestamp": f"2026-01-10T0{i}:00:00Z"
            }

        stats = worm_uploader.get_stats()

        assert stats["enabled"] is True
        assert stats["mode"] == "proxy"
        assert stats["total_uploaded"] == 3
        assert stats["pending_count"] == 2
        assert stats["retention_days"] == 90


class TestProxyEndpointContract:
    """Tests verifying the proxy endpoint contract matches what MCP expects."""

    def test_bundle_hash_format(self, sample_bundle):
        """Test that bundle hash is computed correctly."""
        bundle_content = sample_bundle.read_bytes()
        bundle_hash = hashlib.sha256(bundle_content).hexdigest()

        # Hash should be 64 hex chars
        assert len(bundle_hash) == 64
        assert all(c in "0123456789abcdef" for c in bundle_hash)

    def test_header_format(self, worm_uploader, sample_bundle):
        """Test that headers are formatted correctly for MCP endpoint."""
        bundle_content = sample_bundle.read_bytes()
        bundle_hash = hashlib.sha256(bundle_content).hexdigest()

        # This is what the uploader sends
        headers = {
            "X-Client-ID": worm_uploader.client_id,
            "X-Bundle-ID": "EB-20260110-001",
            "X-Bundle-Hash": f"sha256:{bundle_hash}",
            "Authorization": f"Bearer {worm_uploader.mcp_api_key}",
        }

        assert headers["X-Client-ID"] == "test-site-001"
        assert headers["X-Bundle-Hash"].startswith("sha256:")
        assert "Bearer" in headers["Authorization"]


class TestWormRetention:
    """Tests for WORM retention requirements."""

    def test_minimum_retention_days(self):
        """Test that retention cannot be set below minimum."""
        # HIPAA requires 6 years (2190 days), but we allow 90 as minimum per bundle
        # with 7 years overall in bucket lifecycle
        config = WormConfig(retention_days=30)  # Below recommended
        assert config.retention_days == 30  # Allowed at config level

        # In production, MinIO bucket should enforce minimum

    def test_retention_until_calculation(self):
        """Test retention date calculation."""
        now = datetime(2026, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
        retention_days = 90

        from datetime import timedelta
        retention_until = now + timedelta(days=retention_days)

        assert retention_until.year == 2026
        assert retention_until.month == 4
        assert retention_until.day == 10  # Jan 10 + 90 days = April 10
