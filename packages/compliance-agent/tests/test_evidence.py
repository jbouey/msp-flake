"""
Tests for evidence generation and storage.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta, timezone
import json

from compliance_agent.models import EvidenceBundle, ActionTaken
from compliance_agent.evidence import EvidenceGenerator
from compliance_agent.crypto import Ed25519Signer, generate_keypair
from compliance_agent.config import AgentConfig


@pytest.fixture
def temp_evidence_dir():
    """Create temporary evidence directory."""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def test_signer(tmp_path):
    """Create test Ed25519 signer."""
    private_key, _ = generate_keypair()
    key_path = tmp_path / "test-key"
    key_path.write_bytes(private_key)
    return Ed25519Signer(key_path)


@pytest.fixture
def test_config(temp_evidence_dir, tmp_path):
    """Create test configuration."""
    # Create mock baseline file
    baseline_path = tmp_path / "baseline.nix"
    baseline_path.write_text("{ }")

    # Create mock secret files
    cert_file = tmp_path / "cert.pem"
    cert_file.write_text("MOCK_CERT")
    key_file = tmp_path / "key.pem"
    key_file.write_text("MOCK_KEY")
    signing_key = tmp_path / "signing.key"
    signing_key.write_bytes(generate_keypair()[0])

    # Create state directory structure
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    evidence_dir = state_dir / "evidence"
    evidence_dir.mkdir()

    import os
    os.environ.update({
        'SITE_ID': 'test-site-001',
        'HOST_ID': 'test-host',
        'DEPLOYMENT_MODE': 'direct',  # Use direct mode for tests
        'STATE_DIR': str(state_dir),  # Use tmp state dir
        'BASELINE_PATH': str(baseline_path),
        'CLIENT_CERT_FILE': str(cert_file),
        'CLIENT_KEY_FILE': str(key_file),
        'SIGNING_KEY_FILE': str(signing_key),
    })

    from compliance_agent.config import load_config
    config = load_config()

    # Verify evidence_dir is set correctly
    assert config.evidence_dir == evidence_dir

    return config


@pytest.mark.asyncio
async def test_create_evidence_basic(test_config, test_signer):
    """Test creating basic evidence bundle."""
    generator = EvidenceGenerator(test_config, test_signer)

    bundle = await generator.create_evidence(
        check="patching",
        outcome="success",
        pre_state={"nixos_generation": 142},
        post_state={"nixos_generation": 143},
        hipaa_controls=["164.308(a)(5)(ii)(B)"]
    )

    assert bundle.bundle_id is not None
    assert bundle.site_id == "test-site-001"
    assert bundle.host_id == "test-host"
    assert bundle.check == "patching"
    assert bundle.outcome == "success"
    assert bundle.pre_state == {"nixos_generation": 142}
    assert bundle.post_state == {"nixos_generation": 143}
    assert bundle.hipaa_controls == ["164.308(a)(5)(ii)(B)"]


@pytest.mark.asyncio
async def test_create_evidence_with_actions(test_config, test_signer):
    """Test creating evidence with action details."""
    generator = EvidenceGenerator(test_config, test_signer)

    actions = [
        ActionTaken(
            step=1,
            action="nixos-rebuild",
            command="nixos-rebuild switch",
            exit_code=0,
            duration_sec=42.5
        ),
        ActionTaken(
            step=2,
            action="health_check",
            command="systemctl is-system-running",
            exit_code=0,
            duration_sec=1.2,
            result="running"
        )
    ]

    bundle = await generator.create_evidence(
        check="patching",
        outcome="success",
        pre_state={"nixos_generation": 142},
        post_state={"nixos_generation": 143},
        actions=actions
    )

    assert len(bundle.action_taken) == 2
    assert bundle.action_taken[0].step == 1
    assert bundle.action_taken[0].action == "nixos-rebuild"
    assert bundle.action_taken[1].result == "running"


@pytest.mark.asyncio
async def test_store_evidence(test_config, test_signer):
    """Test storing evidence to disk."""
    generator = EvidenceGenerator(test_config, test_signer)

    bundle = await generator.create_evidence(
        check="backup",
        outcome="success",
        pre_state={"last_backup": "2025-11-05"},
        post_state={"last_backup": "2025-11-06"}
    )

    bundle_path, sig_path, worm_uri, ots_status = await generator.store_evidence(bundle, sign=True)

    # Check files exist
    assert bundle_path.exists()
    assert sig_path.exists()

    # WORM is disabled by default, so URI should be None
    assert worm_uri is None
    # OTS is disabled by default
    assert ots_status is None

    # Check directory structure
    date = bundle.timestamp_start
    expected_dir = (
        test_config.evidence_dir /
        f"{date.year:04d}" /
        f"{date.month:02d}" /
        f"{date.day:02d}" /
        bundle.bundle_id
    )
    assert bundle_path.parent == expected_dir

    # Check contents
    with open(bundle_path) as f:
        data = json.load(f)
    assert data["bundle_id"] == bundle.bundle_id
    assert data["check"] == "backup"


@pytest.mark.asyncio
async def test_store_and_verify_evidence(test_config, test_signer):
    """Test evidence signature verification."""
    generator = EvidenceGenerator(test_config, test_signer)

    bundle = await generator.create_evidence(
        check="firewall",
        outcome="success",
        pre_state={"ruleset_hash": "abc123"},
        post_state={"ruleset_hash": "def456"}
    )

    bundle_path, sig_path, _, _ = await generator.store_evidence(bundle, sign=True)

    # Verify signature
    is_valid = await generator.verify_evidence(bundle_path, sig_path)
    assert is_valid is True

    # Tamper with bundle
    with open(bundle_path, 'r') as f:
        data = json.load(f)
    data["outcome"] = "failed"  # Tamper
    with open(bundle_path, 'w') as f:
        json.dump(data, f)

    # Verification should fail
    is_valid = await generator.verify_evidence(bundle_path, sig_path)
    assert is_valid is False


@pytest.mark.asyncio
async def test_load_evidence(test_config, test_signer):
    """Test loading evidence from disk."""
    generator = EvidenceGenerator(test_config, test_signer)

    # Create and store bundle
    original_bundle = await generator.create_evidence(
        check="logging",
        outcome="success",
        pre_state={"journald_active": False},
        post_state={"journald_active": True}
    )

    await generator.store_evidence(original_bundle)

    # Load bundle
    loaded_bundle = await generator.load_evidence(original_bundle.bundle_id)

    assert loaded_bundle is not None
    assert loaded_bundle.bundle_id == original_bundle.bundle_id
    assert loaded_bundle.check == "logging"
    assert loaded_bundle.outcome == "success"


@pytest.mark.asyncio
async def test_list_evidence(test_config, test_signer):
    """Test listing evidence bundles."""
    generator = EvidenceGenerator(test_config, test_signer)

    # Create multiple bundles
    for i in range(5):
        bundle = await generator.create_evidence(
            check=["patching", "backup", "firewall"][i % 3],
            outcome=["success", "failed"][i % 2],
            pre_state={},
            post_state={}
        )
        await generator.store_evidence(bundle)

    # List all
    all_bundles = await generator.list_evidence()
    assert len(all_bundles) == 5

    # Filter by check
    patching_bundles = await generator.list_evidence(check_type="patching")
    assert len(patching_bundles) == 2

    # Filter by outcome
    success_bundles = await generator.list_evidence(outcome="success")
    assert len(success_bundles) == 3

    # Limit results
    limited = await generator.list_evidence(limit=2)
    assert len(limited) == 2


@pytest.mark.asyncio
async def test_prune_old_evidence(test_config, test_signer):
    """Test pruning old evidence bundles."""
    generator = EvidenceGenerator(test_config, test_signer)

    # Create bundles with different timestamps
    now = datetime.now(timezone.utc)

    for i in range(10):
        timestamp = now - timedelta(days=i * 10)
        bundle = await generator.create_evidence(
            check="patching",
            outcome="success",
            pre_state={},
            post_state={},
            timestamp_start=timestamp,
            timestamp_end=timestamp
        )
        await generator.store_evidence(bundle)

    # Prune: keep last 5, never delete < 30 days old
    deleted = await generator.prune_old_evidence(
        retention_count=5,
        retention_days=30
    )

    # Should delete some but not all
    remaining = await generator.list_evidence()
    assert len(remaining) >= 5
    assert len(remaining) < 10


@pytest.mark.asyncio
async def test_evidence_stats(test_config, test_signer):
    """Test evidence statistics."""
    generator = EvidenceGenerator(test_config, test_signer)

    # Create bundles
    for check, outcome in [
        ("patching", "success"),
        ("backup", "success"),
        ("patching", "failed"),
    ]:
        bundle = await generator.create_evidence(
            check=check,
            outcome=outcome,
            pre_state={},
            post_state={}
        )
        await generator.store_evidence(bundle)

    stats = await generator.get_evidence_stats()

    assert stats["total_count"] == 3
    assert stats["by_outcome"]["success"] == 2
    assert stats["by_outcome"]["failed"] == 1
    assert stats["by_check"]["patching"] == 2
    assert stats["by_check"]["backup"] == 1
    assert stats["total_size_bytes"] > 0


@pytest.mark.asyncio
async def test_evidence_with_rollback(test_config, test_signer):
    """Test evidence bundle with rollback information."""
    generator = EvidenceGenerator(test_config, test_signer)

    bundle = await generator.create_evidence(
        check="patching",
        outcome="reverted",
        pre_state={"nixos_generation": 142},
        post_state={"nixos_generation": 142},
        rollback_available=True,
        rollback_generation=142,
        error="Health check failed after rebuild"
    )

    assert bundle.outcome == "reverted"
    assert bundle.rollback_available is True
    assert bundle.rollback_generation == 142
    assert bundle.error == "Health check failed after rebuild"


@pytest.mark.asyncio
async def test_evidence_deferred_outside_window(test_config, test_signer):
    """Test evidence for action deferred outside maintenance window."""
    generator = EvidenceGenerator(test_config, test_signer)

    bundle = await generator.create_evidence(
        check="patching",
        outcome="deferred",
        pre_state={"nixos_generation": 142},
        post_state={"nixos_generation": 142},
        error="Outside maintenance window"
    )

    assert bundle.outcome == "deferred"
    assert "maintenance window" in bundle.error.lower()


def test_evidence_bundle_validation():
    """Test evidence bundle model validation."""
    # Valid bundle
    bundle = EvidenceBundle(
        site_id="test-site",
        host_id="test-host",
        deployment_mode="direct",
        timestamp_start=datetime.now(timezone.utc),
        timestamp_end=datetime.now(timezone.utc) + timedelta(seconds=10),
        policy_version="1.0",
        check="patching",
        outcome="success"
    )
    assert bundle.site_id == "test-site"

    # Invalid: end before start
    with pytest.raises(ValueError, match="timestamp_end must be after timestamp_start"):
        EvidenceBundle(
            site_id="test-site",
            host_id="test-host",
            deployment_mode="direct",
            timestamp_start=datetime.now(timezone.utc),
            timestamp_end=datetime.now(timezone.utc) - timedelta(seconds=10),
            policy_version="1.0",
            check="patching",
            outcome="success"
        )

    # Invalid: reseller mode without reseller_id
    with pytest.raises(ValueError, match="reseller_id required"):
        EvidenceBundle(
            site_id="test-site",
            host_id="test-host",
            deployment_mode="reseller",
            reseller_id=None,
            timestamp_start=datetime.now(timezone.utc),
            timestamp_end=datetime.now(timezone.utc),
            policy_version="1.0",
            check="patching",
            outcome="success"
        )


# =============================================================================
# WORM Uploader Tests
# =============================================================================

from compliance_agent.worm_uploader import WormUploader, WormConfig, UploadResult


@pytest.fixture
def worm_config():
    """Create test WORM config (disabled by default)."""
    return WormConfig(
        enabled=False,
        mode="proxy",
        retention_days=90
    )


@pytest.fixture
def worm_config_proxy(tmp_path):
    """Create test WORM config for proxy mode."""
    return WormConfig(
        enabled=True,
        mode="proxy",
        mcp_upload_endpoint="http://localhost:8080",
        retention_days=90,
        max_retries=1,
        retry_delay_seconds=0
    )


@pytest.fixture
def worm_uploader(worm_config, temp_evidence_dir):
    """Create test WORM uploader (disabled)."""
    return WormUploader(
        config=worm_config,
        evidence_dir=temp_evidence_dir,
        client_id="test-client-001"
    )


def test_worm_config_defaults():
    """Test WORM config default values."""
    config = WormConfig()
    assert config.enabled is False
    assert config.mode == "proxy"
    assert config.retention_days == 90
    assert config.max_retries == 3
    assert config.auto_upload is True


def test_worm_uploader_init(temp_evidence_dir):
    """Test WORM uploader initialization."""
    config = WormConfig(enabled=True, mode="proxy")
    uploader = WormUploader(
        config=config,
        evidence_dir=temp_evidence_dir,
        client_id="test-client"
    )

    assert uploader.client_id == "test-client"
    assert uploader.evidence_dir == temp_evidence_dir
    assert uploader.config.enabled is True


@pytest.mark.asyncio
async def test_worm_upload_disabled(worm_uploader, temp_evidence_dir):
    """Test that uploads are skipped when disabled."""
    # Create a fake bundle
    bundle_path = temp_evidence_dir / "test-bundle.json"
    bundle_path.write_text('{"bundle_id": "EB-test-0001"}')

    result = await worm_uploader.upload_bundle(bundle_path)

    assert result.success is False
    assert "disabled" in result.error.lower()


@pytest.mark.asyncio
async def test_worm_upload_proxy_no_endpoint(temp_evidence_dir):
    """Test proxy mode fails without endpoint."""
    config = WormConfig(
        enabled=True,
        mode="proxy",
        mcp_upload_endpoint=None
    )
    uploader = WormUploader(
        config=config,
        evidence_dir=temp_evidence_dir,
        client_id="test-client"
    )

    bundle_path = temp_evidence_dir / "test-bundle.json"
    bundle_path.write_text('{"bundle_id": "EB-test-0001"}')

    result = await uploader.upload_bundle(bundle_path)

    assert result.success is False
    assert "endpoint" in result.error.lower()


@pytest.mark.asyncio
async def test_worm_upload_direct_no_bucket(temp_evidence_dir):
    """Test direct mode fails without bucket."""
    config = WormConfig(
        enabled=True,
        mode="direct",
        s3_bucket=None
    )
    uploader = WormUploader(
        config=config,
        evidence_dir=temp_evidence_dir,
        client_id="test-client"
    )

    bundle_path = temp_evidence_dir / "test-bundle.json"
    bundle_path.write_text('{"bundle_id": "EB-test-0001"}')

    result = await uploader.upload_bundle(bundle_path)

    assert result.success is False
    assert "bucket" in result.error.lower()


def test_worm_uploader_stats(worm_uploader):
    """Test WORM uploader stats."""
    stats = worm_uploader.get_stats()

    assert stats["enabled"] is False
    assert stats["mode"] == "proxy"
    assert stats["total_uploaded"] == 0
    assert stats["pending_count"] == 0


def test_worm_upload_registry(temp_evidence_dir):
    """Test upload registry persistence."""
    config = WormConfig(enabled=False)
    uploader = WormUploader(
        config=config,
        evidence_dir=temp_evidence_dir,
        client_id="test-client"
    )

    # Manually add to registry
    uploader._upload_registry["EB-test-0001"] = {
        "success": True,
        "s3_uri": "s3://bucket/key",
        "upload_timestamp": "2025-11-06T00:00:00"
    }
    uploader._save_registry()

    # Create new uploader and verify registry loaded
    uploader2 = WormUploader(
        config=config,
        evidence_dir=temp_evidence_dir,
        client_id="test-client"
    )

    assert "EB-test-0001" in uploader2._upload_registry
    assert uploader2._upload_registry["EB-test-0001"]["success"] is True


@pytest.mark.asyncio
async def test_worm_sync_pending_disabled(worm_uploader):
    """Test sync returns empty when disabled."""
    results = await worm_uploader.sync_pending()
    assert results == []


def test_worm_uploader_extract_bundle_id(worm_uploader, temp_evidence_dir):
    """Test bundle ID extraction."""
    # Test from parent directory name
    bundle_dir = temp_evidence_dir / "2025" / "11" / "06" / "EB-20251106-0001"
    bundle_dir.mkdir(parents=True)
    bundle_path = bundle_dir / "bundle.json"
    bundle_path.write_text('{"bundle_id": "EB-20251106-0001"}')

    bundle_id = worm_uploader._extract_bundle_id(bundle_path)
    assert bundle_id == "EB-20251106-0001"


@pytest.mark.asyncio
async def test_evidence_worm_stats(test_config, test_signer):
    """Test evidence generator WORM stats."""
    generator = EvidenceGenerator(test_config, test_signer)

    stats = generator.get_worm_stats()

    # WORM is disabled in test config
    assert stats["enabled"] is False
    assert stats["total_uploaded"] == 0


@pytest.mark.asyncio
async def test_evidence_sync_to_worm_disabled(test_config, test_signer):
    """Test WORM sync returns error when disabled."""
    generator = EvidenceGenerator(test_config, test_signer)

    result = await generator.sync_to_worm()

    assert result["enabled"] is False
    assert "not initialized" in result["error"]


# =============================================================================
# Batch Processing Tests
# =============================================================================


@pytest.mark.asyncio
async def test_store_evidence_batch_empty(test_config, test_signer):
    """Test batch storage with empty list."""
    generator = EvidenceGenerator(test_config, test_signer)

    results = await generator.store_evidence_batch([])

    assert results == []


@pytest.mark.asyncio
async def test_store_evidence_batch_single(test_config, test_signer):
    """Test batch storage with single bundle."""
    generator = EvidenceGenerator(test_config, test_signer)

    bundle = await generator.create_evidence(
        check="patching",
        outcome="success",
        pre_state={"gen": 142},
        post_state={"gen": 143}
    )

    results = await generator.store_evidence_batch([bundle])

    assert len(results) == 1
    assert results[0][0] is not None  # bundle_path
    assert results[0][1] is not None  # sig_path
    assert results[0][0].exists()


@pytest.mark.asyncio
async def test_store_evidence_batch_multiple(test_config, test_signer):
    """Test batch storage with multiple bundles."""
    generator = EvidenceGenerator(test_config, test_signer)

    # Create 5 bundles
    bundles = []
    for i in range(5):
        bundle = await generator.create_evidence(
            check=["patching", "backup", "firewall"][i % 3],
            outcome="success",
            pre_state={"index": i},
            post_state={"index": i + 1}
        )
        bundles.append(bundle)

    results = await generator.store_evidence_batch(bundles, max_concurrency=3)

    assert len(results) == 5
    for result in results:
        assert result[0] is not None
        assert result[0].exists()


@pytest.mark.asyncio
async def test_store_evidence_batch_concurrency_limit(test_config, test_signer):
    """Test batch storage respects concurrency limit."""
    generator = EvidenceGenerator(test_config, test_signer)

    # Create 10 bundles
    bundles = []
    for i in range(10):
        bundle = await generator.create_evidence(
            check="patching",
            outcome="success",
            pre_state={"gen": i},
            post_state={"gen": i + 1}
        )
        bundles.append(bundle)

    # Store with max_concurrency=2 (should process in batches)
    results = await generator.store_evidence_batch(bundles, max_concurrency=2)

    assert len(results) == 10
    stored_count = sum(1 for r in results if r[0] is not None)
    assert stored_count == 10


@pytest.mark.asyncio
async def test_store_evidence_batch_without_sign(test_config, test_signer):
    """Test batch storage without signing."""
    generator = EvidenceGenerator(test_config, test_signer)

    bundles = []
    for i in range(3):
        bundle = await generator.create_evidence(
            check="backup",
            outcome="success",
            pre_state={},
            post_state={}
        )
        bundles.append(bundle)

    results = await generator.store_evidence_batch(bundles, sign=False)

    assert len(results) == 3
    for result in results:
        bundle_path, sig_path, _, _ = result
        assert bundle_path is not None
        assert bundle_path.exists()
        # sig_path should be None when sign=False
        assert sig_path is None


@pytest.mark.asyncio
async def test_sync_to_worm_parallel_disabled(test_config, test_signer):
    """Test parallel WORM sync returns error when disabled."""
    generator = EvidenceGenerator(test_config, test_signer)

    result = await generator.sync_to_worm_parallel(max_workers=5)

    assert result["enabled"] is False
    assert "not initialized" in result["error"]


@pytest.mark.asyncio
async def test_sync_to_worm_with_batch_size(test_config, test_signer):
    """Test WORM sync with custom batch size."""
    generator = EvidenceGenerator(test_config, test_signer)

    # This will fail because WORM is disabled, but tests the method exists
    result = await generator.sync_to_worm(batch_size=20)

    assert result["enabled"] is False


@pytest.mark.asyncio
async def test_worm_sync_pending_batch(temp_evidence_dir):
    """Test WORM uploader batch sync."""
    config = WormConfig(
        enabled=True,
        mode="proxy",
        mcp_upload_endpoint="http://localhost:8080",
        max_retries=1,
        retry_delay_seconds=0,
        upload_batch_size=2
    )
    uploader = WormUploader(
        config=config,
        evidence_dir=temp_evidence_dir,
        client_id="test-client"
    )

    # Create fake bundles
    for i in range(5):
        bundle_dir = temp_evidence_dir / "2025" / "11" / "06" / f"EB-test-{i:04d}"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = bundle_dir / "bundle.json"
        bundle_path.write_text(f'{{"bundle_id": "EB-test-{i:04d}"}}')

    # Find pending should find all 5
    pending = uploader._find_pending_bundles()
    assert len(pending) == 5

    # Verify batch size is set
    assert uploader.config.upload_batch_size == 2
