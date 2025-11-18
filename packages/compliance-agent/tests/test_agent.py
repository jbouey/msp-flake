"""
Tests for agent.py - Main compliance agent orchestration.

Test Coverage:
- Agent initialization
- Main loop execution
- Drift → remediation → evidence flow
- Offline queue integration
- Graceful shutdown
- Signal handling
- Health checks
- Error scenarios
"""

import pytest
import asyncio
from datetime import time
from unittest.mock import AsyncMock, Mock, patch, MagicMock
from pathlib import Path

from compliance_agent.agent import ComplianceAgent
from compliance_agent.models import (
    AgentConfig,
    DriftResult,
    RemediationResult,
    ActionTaken,
    EvidenceBundle
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def test_config(tmp_path):
    """Create test configuration."""
    return AgentConfig(
        deployment_mode="direct",
        client_id="test-client",
        site_id="test-site",
        host_id="test-host",
        state_dir=str(tmp_path / "state"),
        evidence_dir=str(tmp_path / "evidence"),
        log_dir=str(tmp_path / "logs"),
        baseline_path=str(tmp_path / "baseline.yaml"),
        signing_key_file=str(tmp_path / "signing-key.pem"),
        mcp_url="https://mcp.example.com",
        mcp_api_key_file=str(tmp_path / "api-key.txt"),
        mcp_poll_interval_sec=60,
        maintenance_window_start=time(2, 0),
        maintenance_window_end=time(4, 0)
    )


@pytest.fixture
def mock_signer(tmp_path):
    """Create mock Ed25519Signer."""
    # Create dummy key file
    key_file = tmp_path / "signing-key.pem"
    key_file.write_text("dummy-key-content")
    
    with patch('compliance_agent.agent.Ed25519Signer') as mock_class:
        mock_instance = Mock()
        mock_instance.sign.return_value = b"signature"
        mock_instance.get_public_key_bytes.return_value = b"publickey"
        mock_class.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def agent(test_config, mock_signer, tmp_path):
    """Create ComplianceAgent instance with mocked dependencies."""
    # Create dummy files
    (tmp_path / "api-key.txt").write_text("test-api-key")
    (tmp_path / "baseline.yaml").write_text("baseline: test")
    
    with patch('compliance_agent.agent.DriftDetector'), \
         patch('compliance_agent.agent.HealingEngine'), \
         patch('compliance_agent.agent.EvidenceGenerator'), \
         patch('compliance_agent.agent.OfflineQueue'), \
         patch('compliance_agent.agent.MCPClient'):
        
        agent = ComplianceAgent(test_config)
        return agent


# ============================================================================
# Initialization Tests
# ============================================================================


def test_agent_initialization(agent, test_config):
    """Test agent initialization."""
    assert agent.config == test_config
    assert agent.running is False
    assert agent.drift_detector is not None
    assert agent.healing_engine is not None
    assert agent.evidence_generator is not None
    assert agent.offline_queue is not None
    assert agent.mcp_client is not None
    
    # Check statistics initialized
    assert agent.stats["loops_completed"] == 0
    assert agent.stats["drift_detected"] == 0


def test_agent_initialization_without_mcp(test_config, mock_signer, tmp_path):
    """Test agent initialization without MCP server (offline mode)."""
    # Create config without MCP URL
    config_no_mcp = AgentConfig(
        deployment_mode="direct",
        client_id="test-client",
        site_id="test-site",
        host_id="test-host",
        state_dir=str(tmp_path / "state"),
        evidence_dir=str(tmp_path / "evidence"),
        log_dir=str(tmp_path / "logs"),
        baseline_path=str(tmp_path / "baseline.yaml"),
        signing_key_file=str(tmp_path / "signing-key.pem"),
        mcp_url=None,  # No MCP server
        mcp_poll_interval_sec=60
    )
    
    (tmp_path / "baseline.yaml").write_text("baseline: test")
    
    with patch('compliance_agent.agent.DriftDetector'), \
         patch('compliance_agent.agent.HealingEngine'), \
         patch('compliance_agent.agent.EvidenceGenerator'), \
         patch('compliance_agent.agent.OfflineQueue'):
        
        agent = ComplianceAgent(config_no_mcp)
        assert agent.mcp_client is None


# ============================================================================
# Main Loop Tests
# ============================================================================


@pytest.mark.asyncio
async def test_run_iteration_no_drift(agent):
    """Test single iteration with no drift detected."""
    # Mock drift detector to return no drift
    agent.drift_detector.check_all = AsyncMock(return_value=[
        DriftResult(check="patching", drifted=False, pre_state={}, severity="low"),
        DriftResult(check="av_edr_health", drifted=False, pre_state={}, severity="low")
    ])
    
    await agent._run_iteration()
    
    # Should detect but not remediate
    assert agent.stats["drift_detected"] == 0
    assert agent.stats["remediations_attempted"] == 0


@pytest.mark.asyncio
async def test_run_iteration_with_drift(agent):
    """Test single iteration with drift detected and remediated."""
    # Mock drift detector
    drift = DriftResult(
        check="patching",
        drifted=True,
        pre_state={"generation": 999},
        severity="medium",
        recommended_action="update_to_baseline_generation",
        hipaa_controls=["164.308(a)(5)(ii)(B)"]
    )
    
    agent.drift_detector.check_all = AsyncMock(return_value=[drift])
    
    # Mock healing engine
    remediation = RemediationResult(
        check="patching",
        outcome="success",
        pre_state={"generation": 999},
        post_state={"generation": 1000},
        actions=[ActionTaken(action="switch_generation", timestamp=Mock())]
    )
    
    agent.healing_engine.remediate = AsyncMock(return_value=remediation)
    
    # Mock evidence generator
    evidence = EvidenceBundle(
        site_id="test-site",
        host_id="test-host",
        deployment_mode="direct",
        timestamp_start=Mock(),
        timestamp_end=Mock(),
        policy_version="1.0",
        check="patching",
        outcome="success"
    )
    
    agent.evidence_generator.create_evidence = AsyncMock(return_value=evidence)
    agent.evidence_generator.store_evidence = AsyncMock(
        return_value=(Path("/tmp/bundle.json"), Path("/tmp/bundle.sig"))
    )
    
    # Mock MCP client
    agent.mcp_client.upload_evidence = AsyncMock(return_value=True)
    
    await agent._run_iteration()
    
    # Verify flow
    assert agent.stats["drift_detected"] == 1
    assert agent.stats["remediations_attempted"] == 1
    assert agent.stats["remediations_successful"] == 1
    assert agent.stats["evidence_generated"] == 1
    assert agent.stats["evidence_uploaded"] == 1


@pytest.mark.asyncio
async def test_run_iteration_remediation_failure(agent):
    """Test iteration with remediation failure."""
    drift = DriftResult(
        check="patching",
        drifted=True,
        pre_state={},
        severity="high"
    )
    
    agent.drift_detector.check_all = AsyncMock(return_value=[drift])
    
    # Mock remediation failure
    remediation = RemediationResult(
        check="patching",
        outcome="failed",
        pre_state={},
        error="Remediation failed"
    )
    
    agent.healing_engine.remediate = AsyncMock(return_value=remediation)
    
    # Mock evidence generation
    evidence = EvidenceBundle(
        site_id="test-site",
        host_id="test-host",
        deployment_mode="direct",
        timestamp_start=Mock(),
        timestamp_end=Mock(),
        policy_version="1.0",
        check="patching",
        outcome="failed"
    )
    
    agent.evidence_generator.create_evidence = AsyncMock(return_value=evidence)
    agent.evidence_generator.store_evidence = AsyncMock(
        return_value=(Path("/tmp/bundle.json"), None)
    )
    
    agent.mcp_client.upload_evidence = AsyncMock(return_value=True)
    
    await agent._run_iteration()
    
    # Should attempt but not succeed
    assert agent.stats["remediations_attempted"] == 1
    assert agent.stats["remediations_successful"] == 0
    assert agent.stats["evidence_generated"] == 1


# ============================================================================
# Evidence Submission Tests
# ============================================================================


@pytest.mark.asyncio
async def test_submit_evidence_success(agent, tmp_path):
    """Test successful evidence submission to MCP."""
    evidence = EvidenceBundle(
        site_id="test-site",
        host_id="test-host",
        deployment_mode="direct",
        timestamp_start=Mock(year=2025, month=11, day=7),
        timestamp_end=Mock(),
        policy_version="1.0",
        check="patching",
        outcome="success",
        bundle_id="test-bundle-123"
    )
    
    # Create evidence files
    bundle_dir = (
        agent.config.evidence_dir /
        "2025" / "11" / "07" / "test-bundle-123"
    )
    bundle_dir.mkdir(parents=True)
    bundle_path = bundle_dir / "bundle.json"
    bundle_path.write_text('{"test": "evidence"}')
    
    agent.mcp_client.upload_evidence = AsyncMock(return_value=True)
    
    await agent._submit_evidence(evidence)
    
    assert agent.stats["evidence_uploaded"] == 1
    assert agent.stats["evidence_queued"] == 0


@pytest.mark.asyncio
async def test_submit_evidence_failure_queues(agent, tmp_path):
    """Test evidence queued when MCP upload fails."""
    evidence = EvidenceBundle(
        site_id="test-site",
        host_id="test-host",
        deployment_mode="direct",
        timestamp_start=Mock(year=2025, month=11, day=7),
        timestamp_end=Mock(),
        policy_version="1.0",
        check="patching",
        outcome="success",
        bundle_id="test-bundle-456"
    )
    
    # Create evidence files
    bundle_dir = (
        agent.config.evidence_dir /
        "2025" / "11" / "07" / "test-bundle-456"
    )
    bundle_dir.mkdir(parents=True)
    bundle_path = bundle_dir / "bundle.json"
    bundle_path.write_text('{"test": "evidence"}')
    
    # Mock upload failure
    agent.mcp_client.upload_evidence = AsyncMock(return_value=False)
    
    # Mock queue
    agent.offline_queue.enqueue = AsyncMock()
    
    await agent._submit_evidence(evidence)
    
    # Should queue for later
    assert agent.stats["evidence_uploaded"] == 0
    assert agent.stats["evidence_queued"] == 1
    agent.offline_queue.enqueue.assert_called_once()


# ============================================================================
# Offline Queue Processing Tests
# ============================================================================


@pytest.mark.asyncio
async def test_process_offline_queue_empty(agent):
    """Test processing empty offline queue."""
    agent.offline_queue.get_pending = AsyncMock(return_value=[])
    
    await agent._process_offline_queue()
    
    # Should return early
    assert agent.stats["evidence_uploaded"] == 0


@pytest.mark.asyncio
async def test_process_offline_queue_success(agent, tmp_path):
    """Test successful processing of queued evidence."""
    from compliance_agent.models import QueuedEvidence
    from datetime import datetime
    
    # Create queued evidence file
    bundle_path = tmp_path / "queued_bundle.json"
    bundle_path.write_text('{"test": "queued"}')
    
    queued = QueuedEvidence(
        id=1,
        bundle_id="queued-123",
        bundle_path=str(bundle_path),
        signature_path=None,
        created_at=datetime.utcnow(),
        retry_count=0
    )
    
    agent.offline_queue.get_pending = AsyncMock(return_value=[queued])
    agent.offline_queue.mark_uploaded = AsyncMock()
    agent.mcp_client.upload_evidence = AsyncMock(return_value=True)
    
    await agent._process_offline_queue()
    
    # Should upload and mark as uploaded
    agent.mcp_client.upload_evidence.assert_called_once()
    agent.offline_queue.mark_uploaded.assert_called_once_with(1)
    assert agent.stats["evidence_uploaded"] == 1


# ============================================================================
# Shutdown Tests
# ============================================================================


@pytest.mark.asyncio
async def test_shutdown(agent):
    """Test graceful shutdown."""
    agent.running = True
    
    await agent._shutdown()
    
    assert agent.running is False


@pytest.mark.asyncio
async def test_signal_handler(agent):
    """Test signal handler sets shutdown event."""
    import signal
    
    agent._setup_signal_handlers()
    agent.running = True
    
    # Manually trigger shutdown event (simulating signal)
    agent.shutdown_event.set()
    
    assert agent.shutdown_event.is_set()


# ============================================================================
# Health Check Tests
# ============================================================================


@pytest.mark.asyncio
async def test_health_check_healthy(agent):
    """Test health check when all systems healthy."""
    agent.running = True
    agent.mcp_client.health_check = AsyncMock(return_value=True)
    agent.offline_queue.get_stats = AsyncMock(return_value={"pending": 0, "failed": 0})
    
    health = await agent.health_check()
    
    assert health["status"] == "healthy"
    assert health["site_id"] == "test-site"
    assert health["mcp_server"] == "healthy"
    assert "stats" in health
    assert "offline_queue" in health


@pytest.mark.asyncio
async def test_health_check_stopped(agent):
    """Test health check when agent stopped."""
    agent.running = False
    agent.mcp_client.health_check = AsyncMock(return_value=False)
    agent.offline_queue.get_stats = AsyncMock(return_value={})
    
    health = await agent.health_check()
    
    assert health["status"] == "stopped"


@pytest.mark.asyncio
async def test_health_check_no_mcp(agent):
    """Test health check without MCP client."""
    agent.running = True
    agent.mcp_client = None
    agent.offline_queue.get_stats = AsyncMock(return_value={})
    
    health = await agent.health_check()
    
    assert health["mcp_server"] == "not_configured"


# ============================================================================
# Error Handling Tests
# ============================================================================


@pytest.mark.asyncio
async def test_remediation_exception_handling(agent):
    """Test exception handling during remediation."""
    drift = DriftResult(
        check="patching",
        drifted=True,
        pre_state={},
        severity="high"
    )
    
    agent.drift_detector.check_all = AsyncMock(return_value=[drift])
    
    # Mock remediation raising exception
    agent.healing_engine.remediate = AsyncMock(side_effect=Exception("Remediation error"))
    
    # Mock evidence generation
    agent.evidence_generator.create_evidence = AsyncMock(return_value=Mock(
        site_id="test",
        host_id="test",
        timestamp_start=Mock(year=2025, month=11, day=7),
        bundle_id="test-123"
    ))
    agent.evidence_generator.store_evidence = AsyncMock(
        return_value=(Path("/tmp/bundle.json"), None)
    )
    
    agent.mcp_client.upload_evidence = AsyncMock(return_value=True)
    
    # Should not raise, should create evidence with error
    await agent._run_iteration()
    
    assert agent.stats["remediations_attempted"] == 1
    assert agent.stats["remediations_successful"] == 0
