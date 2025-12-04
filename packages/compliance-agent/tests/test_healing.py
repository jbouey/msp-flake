"""
Tests for healing.py - Self-healing remediation engine.

Test Coverage:
- HealingEngine initialization
- All 6 remediation actions (success scenarios)
- All 6 remediation actions (failure scenarios)
- Maintenance window enforcement
- Health check verification
- Rollback scenarios
- Integration with drift detection
"""

import pytest
from datetime import time, timezone
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from compliance_agent.healing import HealingEngine
from compliance_agent.config import AgentConfig
from compliance_agent.models import (
    DriftResult,
    RemediationResult
)
from compliance_agent.utils import AsyncCommandError


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def test_config(tmp_path):
    """Create test configuration."""
    # Create required files
    baseline = tmp_path / "baseline.yaml"
    baseline.write_text("baseline_generation: 1000\n")

    cert_file = tmp_path / "client.crt"
    cert_file.write_text("CERT")

    key_file = tmp_path / "client.key"
    key_file.write_text("KEY")

    signing_key = tmp_path / "signing.key"
    signing_key.write_text("SIGNINGKEY")

    return AgentConfig(
        site_id="test-site",
        host_id="test-host",
        deployment_mode="direct",
        baseline_path=str(baseline),
        client_cert_file=str(cert_file),
        client_key_file=str(key_file),
        signing_key_file=str(signing_key),
        maintenance_window="02:00-04:00"
    )


@pytest.fixture
def healing_engine(test_config):
    """Create HealingEngine instance."""
    return HealingEngine(test_config)


@pytest.fixture
def patching_drift():
    """Create patching drift result."""
    return DriftResult(
        check="patching",
        drifted=True,
        pre_state={
            "current_generation": 999,
            "baseline_generation": 1000,
            "generation_age_days": 15
        },
        severity="medium",
        recommended_action="update_to_baseline_generation",
        hipaa_controls=["164.308(a)(5)(ii)(B)"]
    )


@pytest.fixture
def av_drift():
    """Create AV/EDR health drift result."""
    return DriftResult(
        check="av_edr_health",
        drifted=True,
        pre_state={
            "av_service": "clamav-daemon",
            "service_active": False,
            "av_binary_path": "/usr/sbin/clamd"
        },
        severity="high",
        recommended_action="restart_av_service",
        hipaa_controls=["164.308(a)(5)(ii)(B)"]
    )


@pytest.fixture
def backup_drift():
    """Create backup verification drift result."""
    return DriftResult(
        check="backup_verification",
        drifted=True,
        pre_state={
            "backup_service": "restic-backup",
            "last_backup_age_hours": 48,
            "backup_repo": "/backup/repo"
        },
        severity="high",
        recommended_action="run_backup_job",
        hipaa_controls=["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
    )


@pytest.fixture
def logging_drift():
    """Create logging continuity drift result."""
    return DriftResult(
        check="logging_continuity",
        drifted=True,
        pre_state={
            "logging_services": ["rsyslog", "systemd-journald"],
            "rsyslog_active": False
        },
        severity="medium",
        recommended_action="restart_logging_services",
        hipaa_controls=["164.312(b)"]
    )


@pytest.fixture
def firewall_drift():
    """Create firewall baseline drift result."""
    return DriftResult(
        check="firewall_baseline",
        drifted=True,
        pre_state={
            "baseline_rules_path": "/etc/firewall/baseline.rules",
            "baseline_hash": "abc123",
            "current_hash": "def456"
        },
        severity="critical",
        recommended_action="restore_firewall_baseline",
        hipaa_controls=["164.312(a)(1)"]
    )


@pytest.fixture
def encryption_drift():
    """Create encryption drift result."""
    return DriftResult(
        check="encryption",
        drifted=True,
        pre_state={
            "unencrypted_volumes": ["/dev/sdb1", "/dev/sdc1"]
        },
        severity="critical",
        recommended_action="enable_volume_encryption",
        hipaa_controls=["164.312(a)(2)(iv)"]
    )


# ============================================================================
# Initialization Tests
# ============================================================================


def test_healing_engine_init(healing_engine, test_config):
    """Test HealingEngine initialization."""
    assert healing_engine.config == test_config
    assert healing_engine.deployment_mode == "direct"
    assert healing_engine.maintenance_window_start == time(2, 0)
    assert healing_engine.maintenance_window_end == time(4, 0)


@pytest.mark.asyncio
async def test_remediate_unknown_check(healing_engine):
    """Test remediate with unknown check type."""
    drift = DriftResult(
        check="unknown_check",
        drifted=True,
        pre_state={},
        severity="low"
    )
    
    result = await healing_engine.remediate(drift)
    
    assert result.outcome == "failed"
    assert "No remediation handler" in result.error


# ============================================================================
# Remediation Action 1: Update to Baseline Generation
# ============================================================================


@pytest.mark.asyncio
async def test_update_to_baseline_generation_success(healing_engine, patching_drift):
    """Test successful generation update."""
    with patch('compliance_agent.healing.run_command') as mock_run, \
         patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw:
        
        mock_mw.return_value = True
        
        # Mock commands: get current gen, switch gen, verify gen
        mock_run.side_effect = [
            AsyncMock(stdout="999   2025-01-15 10:23:45"),  # Current gen
            AsyncMock(stdout="Switching to generation 1000"),  # Switch
            AsyncMock(stdout="1000")  # Verify
        ]
        
        result = await healing_engine.update_to_baseline_generation(patching_drift)
        
        assert result.outcome == "success"
        assert result.check == "patching"
        assert result.rollback_available is True
        assert result.rollback_generation == 999
        assert len(result.actions) == 3


@pytest.mark.asyncio
async def test_update_to_baseline_generation_outside_window(healing_engine, patching_drift):
    """Test generation update deferred outside maintenance window."""
    with patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw:
        mock_mw.return_value = False
        
        result = await healing_engine.update_to_baseline_generation(patching_drift)
        
        assert result.outcome == "deferred"
        assert "maintenance window" in result.error.lower()


@pytest.mark.asyncio
async def test_update_to_baseline_generation_switch_fails(healing_engine, patching_drift):
    """Test generation update with switch failure."""
    with patch('compliance_agent.healing.run_command') as mock_run, \
         patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw:
        
        mock_mw.return_value = True
        
        # Mock: get current gen succeeds, switch fails
        mock_run.side_effect = [
            AsyncMock(stdout="999   2025-01-15 10:23:45"),
            AsyncCommandError("nixos-rebuild", 1, "", "Switch failed")
        ]
        
        result = await healing_engine.update_to_baseline_generation(patching_drift)
        
        assert result.outcome == "failed"
        assert "Switch failed" in result.error
        assert result.rollback_available is True
        assert result.rollback_generation == 999


@pytest.mark.asyncio
async def test_update_to_baseline_generation_verify_fails(healing_engine, patching_drift):
    """Test generation update with verification failure."""
    with patch('compliance_agent.healing.run_command') as mock_run, \
         patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw:
        
        mock_mw.return_value = True
        
        # Mock: get current gen, switch succeeds, verify shows wrong gen, rollback
        mock_run.side_effect = [
            AsyncMock(stdout="999   2025-01-15 10:23:45"),  # Current gen
            AsyncMock(stdout="Switching to generation 1000"),  # Switch
            AsyncMock(stdout="999"),  # Verify (wrong gen!)
            AsyncMock(stdout="Rolled back")  # Rollback
        ]
        
        result = await healing_engine.update_to_baseline_generation(patching_drift)
        
        assert result.outcome == "reverted"
        assert "verification failed" in result.error.lower()


# ============================================================================
# Remediation Action 2: Restart AV Service
# ============================================================================


@pytest.mark.asyncio
async def test_restart_av_service_success(healing_engine, av_drift):
    """Test successful AV service restart."""
    with patch('compliance_agent.healing.run_command') as mock_run:
        # Mock: restart service, check active, verify hash
        mock_run.side_effect = [
            AsyncMock(stdout=""),  # Restart
            AsyncMock(stdout="active"),  # Is active
            AsyncMock(stdout="abc123def456")  # Hash
        ]
        
        result = await healing_engine.restart_av_service(av_drift)
        
        assert result.outcome == "success"
        assert result.check == "av_edr_health"
        assert result.post_state["service_active"] is True
        assert len(result.actions) == 2


@pytest.mark.asyncio
async def test_restart_av_service_restart_fails(healing_engine, av_drift):
    """Test AV service restart failure."""
    with patch('compliance_agent.healing.run_command') as mock_run:
        mock_run.side_effect = [
            AsyncCommandError("systemctl", 1, "", "Failed to restart")
        ]
        
        result = await healing_engine.restart_av_service(av_drift)
        
        assert result.outcome == "failed"
        assert "Failed to restart" in result.error


@pytest.mark.asyncio
async def test_restart_av_service_not_active_after(healing_engine, av_drift):
    """Test AV service not active after restart."""
    with patch('compliance_agent.healing.run_command') as mock_run:
        # Mock: restart succeeds, but service still inactive
        # Note: need 3 mocks because av_drift has av_binary_path which triggers hash check
        mock_run.side_effect = [
            AsyncMock(stdout=""),  # Restart
            AsyncMock(stdout="inactive"),  # Still inactive
            AsyncMock(stdout="abc123def456")  # Hash (still runs even if inactive)
        ]

        result = await healing_engine.restart_av_service(av_drift)

        assert result.outcome == "failed"
        assert "not active" in result.error.lower()


# ============================================================================
# Remediation Action 3: Run Backup Job
# ============================================================================


@pytest.mark.asyncio
async def test_run_backup_job_success(healing_engine, backup_drift):
    """Test successful backup job."""
    with patch('compliance_agent.healing.run_command') as mock_run, \
         patch('compliance_agent.healing.asyncio.sleep'):
        
        # Mock: start backup, check status, get snapshot
        mock_run.side_effect = [
            AsyncMock(stdout=""),  # Start
            AsyncMock(stdout="Status: succeeded"),  # Status
            AsyncMock(stdout='[{"id": "snap123"}]')  # Snapshot
        ]
        
        result = await healing_engine.run_backup_job(backup_drift)
        
        assert result.outcome == "success"
        assert result.check == "backup_verification"
        assert result.post_state["backup_success"] is True
        assert result.post_state["backup_checksum"] == "snap123"


@pytest.mark.asyncio
async def test_run_backup_job_fails(healing_engine, backup_drift):
    """Test backup job failure."""
    with patch('compliance_agent.healing.run_command') as mock_run:
        mock_run.side_effect = [
            AsyncCommandError("systemctl", 1, "", "Backup failed")
        ]
        
        result = await healing_engine.run_backup_job(backup_drift)
        
        assert result.outcome == "failed"
        assert "Backup failed" in result.error


@pytest.mark.asyncio
async def test_run_backup_job_not_successful(healing_engine, backup_drift):
    """Test backup job didn't complete successfully."""
    with patch('compliance_agent.healing.run_command') as mock_run, \
         patch('compliance_agent.healing.asyncio.sleep'):
        
        # Mock: start succeeds, but status shows failure
        mock_run.side_effect = [
            AsyncMock(stdout=""),  # Start
            AsyncMock(stdout="Status: failed")  # Failed status
        ]
        
        result = await healing_engine.run_backup_job(backup_drift)
        
        assert result.outcome == "failed"
        assert "did not complete successfully" in result.error.lower()


# ============================================================================
# Remediation Action 4: Restart Logging Services
# ============================================================================


@pytest.mark.asyncio
async def test_restart_logging_services_success(healing_engine, logging_drift):
    """Test successful logging services restart."""
    from datetime import datetime as real_datetime, timezone

    # Fixed timestamp for deterministic canary message (must be timezone-aware)
    fixed_time = real_datetime(2025, 11, 7, 14, 30, 0, tzinfo=timezone.utc)
    expected_canary = f"MSP Compliance Agent - Logging Health Check - {fixed_time.isoformat()}"

    with patch('compliance_agent.healing.run_command') as mock_run, \
         patch('compliance_agent.healing.asyncio.sleep'), \
         patch('compliance_agent.healing.datetime') as mock_dt:
        # Mock datetime.now(timezone.utc) to return fixed time
        mock_dt.now.return_value = fixed_time

        # Mock: restart rsyslog, restart journald, check active x2, logger, journalctl
        mock_run.side_effect = [
            AsyncMock(stdout=""),  # Restart rsyslog
            AsyncMock(stdout=""),  # Restart journald
            AsyncMock(stdout="active"),  # Check rsyslog
            AsyncMock(stdout="active"),  # Check journald
            AsyncMock(stdout=""),  # Logger
            AsyncMock(stdout=expected_canary)  # Journalctl with matching canary
        ]

        result = await healing_engine.restart_logging_services(logging_drift)

        assert result.outcome == "success"
        assert result.check == "logging_continuity"
        assert result.post_state["canary_verified"] is True


@pytest.mark.asyncio
async def test_restart_logging_services_restart_fails(healing_engine, logging_drift):
    """Test logging service restart failure."""
    with patch('compliance_agent.healing.run_command') as mock_run:
        mock_run.side_effect = [
            AsyncCommandError("systemctl", 1, "", "Failed to restart rsyslog")
        ]
        
        result = await healing_engine.restart_logging_services(logging_drift)
        
        assert result.outcome == "failed"
        assert "Failed to restart rsyslog" in result.error


@pytest.mark.asyncio
async def test_restart_logging_services_canary_not_found(healing_engine, logging_drift):
    """Test logging services with canary not found."""
    with patch('compliance_agent.healing.run_command') as mock_run, \
         patch('compliance_agent.healing.asyncio.sleep'):
        
        # Mock: restarts succeed, active checks succeed, canary not found
        mock_run.side_effect = [
            AsyncMock(stdout=""),  # Restart rsyslog
            AsyncMock(stdout=""),  # Restart journald
            AsyncMock(stdout="active"),  # Check rsyslog
            AsyncMock(stdout="active"),  # Check journald
            AsyncMock(stdout=""),  # Logger
            AsyncMock(stdout="")  # Journalctl (canary not found)
        ]
        
        result = await healing_engine.restart_logging_services(logging_drift)
        
        assert result.outcome == "failed"
        assert "not fully operational" in result.error.lower()


# ============================================================================
# Remediation Action 5: Restore Firewall Baseline
# ============================================================================


@pytest.mark.asyncio
async def test_restore_firewall_baseline_success(healing_engine, firewall_drift, tmp_path):
    """Test successful firewall baseline restore."""
    with patch('compliance_agent.healing.run_command') as mock_run, \
         patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw, \
         patch('compliance_agent.healing.Path') as mock_path:
        
        mock_mw.return_value = True
        
        # Mock Path.exists() to return True
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.write_text = MagicMock()
        mock_path.return_value = mock_path_instance
        
        # Mock: save rules, restore rules, verify hash
        mock_run.side_effect = [
            AsyncMock(stdout="*filter\n:INPUT ACCEPT"),  # iptables-save
            AsyncMock(stdout=""),  # iptables-restore
            AsyncMock(stdout="abc123")  # Hash matches baseline
        ]
        
        result = await healing_engine.restore_firewall_baseline(firewall_drift)
        
        assert result.outcome == "success"
        assert result.check == "firewall_baseline"
        assert result.rollback_available is True


@pytest.mark.asyncio
async def test_restore_firewall_baseline_outside_window(healing_engine, firewall_drift):
    """Test firewall restore deferred outside maintenance window."""
    with patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw:
        mock_mw.return_value = False
        
        result = await healing_engine.restore_firewall_baseline(firewall_drift)
        
        assert result.outcome == "deferred"
        assert "maintenance window" in result.error.lower()


@pytest.mark.asyncio
async def test_restore_firewall_baseline_apply_fails(healing_engine, firewall_drift, tmp_path):
    """Test firewall baseline apply failure with rollback."""
    with patch('compliance_agent.healing.run_command') as mock_run, \
         patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw, \
         patch('compliance_agent.healing.Path') as mock_path:
        
        mock_mw.return_value = True
        
        # Mock Path
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.write_text = MagicMock()
        mock_path.return_value = mock_path_instance
        
        # Mock: save rules, restore fails, rollback
        mock_run.side_effect = [
            AsyncMock(stdout="*filter\n:INPUT ACCEPT"),  # iptables-save
            AsyncCommandError("iptables-restore", 1, "", "Restore failed"),  # Restore fails
            AsyncMock(stdout="")  # Rollback
        ]
        
        result = await healing_engine.restore_firewall_baseline(firewall_drift)
        
        assert result.outcome == "reverted"
        assert "Restore failed" in result.error


@pytest.mark.asyncio
async def test_restore_firewall_baseline_hash_mismatch(healing_engine, firewall_drift, tmp_path):
    """Test firewall restore with hash mismatch."""
    with patch('compliance_agent.healing.run_command') as mock_run, \
         patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw, \
         patch('compliance_agent.healing.Path') as mock_path:
        
        mock_mw.return_value = True
        
        # Mock Path
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.write_text = MagicMock()
        mock_path.return_value = mock_path_instance
        
        # Mock: save rules, restore succeeds, hash mismatch, rollback
        mock_run.side_effect = [
            AsyncMock(stdout="*filter\n:INPUT ACCEPT"),  # iptables-save
            AsyncMock(stdout=""),  # iptables-restore
            AsyncMock(stdout="wronghash"),  # Hash doesn't match
            AsyncMock(stdout="")  # Rollback
        ]
        
        result = await healing_engine.restore_firewall_baseline(firewall_drift)
        
        assert result.outcome == "reverted"
        assert "hash mismatch" in result.error.lower()


# ============================================================================
# Remediation Action 6: Enable Volume Encryption (Alert Only)
# ============================================================================


@pytest.mark.asyncio
async def test_enable_volume_encryption_alert(healing_engine, encryption_drift):
    """Test encryption alert generation."""
    with patch('compliance_agent.healing.run_command') as mock_run:
        mock_run.return_value = AsyncMock(stdout="")
        
        result = await healing_engine.enable_volume_encryption(encryption_drift)
        
        assert result.outcome == "alert"
        assert result.check == "encryption"
        assert "MANUAL INTERVENTION REQUIRED" in result.error
        assert "/dev/sdb1" in result.error
        assert "/dev/sdc1" in result.error


# ============================================================================
# Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_remediate_dispatcher(healing_engine, patching_drift):
    """Test remediate dispatcher routes to correct handler."""
    with patch('compliance_agent.healing.run_command') as mock_run, \
         patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw:
        
        mock_mw.return_value = True
        mock_run.side_effect = [
            AsyncMock(stdout="999   2025-01-15 10:23:45"),
            AsyncMock(stdout="Switching"),
            AsyncMock(stdout="1000")
        ]
        
        result = await healing_engine.remediate(patching_drift)
        
        assert result.check == "patching"
        assert result.outcome == "success"


@pytest.mark.asyncio
async def test_remediate_exception_handling(healing_engine, patching_drift):
    """Test remediate handles exceptions gracefully."""
    with patch.object(healing_engine, 'update_to_baseline_generation') as mock_handler:
        mock_handler.side_effect = Exception("Unexpected error")

        result = await healing_engine.remediate(patching_drift)

        assert result.outcome == "failed"
        assert "Unexpected error" in result.error


# ============================================================================
# Approval Integration Tests
# ============================================================================


@pytest.fixture
def approval_db(tmp_path):
    """Create temporary approval database."""
    from compliance_agent.approval import ApprovalManager
    db_path = tmp_path / "approvals.db"
    return ApprovalManager(db_path)


@pytest.fixture
def healing_engine_with_approval(test_config, approval_db):
    """Create HealingEngine with approval manager."""
    return HealingEngine(test_config, approval_manager=approval_db)


@pytest.mark.asyncio
async def test_remediate_no_approval_manager(healing_engine, patching_drift):
    """Test remediate proceeds without approval manager."""
    # No approval manager = all actions allowed
    with patch('compliance_agent.healing.run_command') as mock_run, \
         patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw:

        mock_mw.return_value = True
        mock_run.side_effect = [
            AsyncMock(stdout="999   2025-01-15 10:23:45"),
            AsyncMock(stdout="Switching"),
            AsyncMock(stdout="1000")
        ]

        result = await healing_engine.remediate(patching_drift)

        # Should proceed without approval check
        assert result.outcome in ("success", "failed", "deferred")
        assert result.outcome != "pending_approval"


@pytest.mark.asyncio
async def test_remediate_non_disruptive_no_approval(healing_engine_with_approval):
    """Test non-disruptive actions don't require approval."""
    av_drift = DriftResult(
        check="av_edr_health",
        drifted=True,
        pre_state={"av_service": "clamav-daemon"},
        severity="medium"
    )

    with patch('compliance_agent.healing.run_command') as mock_run:
        mock_run.side_effect = [
            AsyncMock(stdout=""),  # restart
            AsyncMock(stdout="active")  # is-active
        ]

        result = await healing_engine_with_approval.remediate(av_drift)

        # Non-disruptive action should not need approval
        assert result.outcome != "pending_approval"


@pytest.mark.asyncio
async def test_remediate_disruptive_requires_approval(healing_engine_with_approval, patching_drift):
    """Test disruptive actions require approval."""
    with patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw:
        mock_mw.return_value = False  # Not in maintenance window

        result = await healing_engine_with_approval.remediate(patching_drift)

        assert result.outcome == "pending_approval"
        assert "Approval required" in result.error
        assert result.error.startswith("Approval required: APR-")


@pytest.mark.asyncio
async def test_remediate_with_pre_approved_request(
    test_config, approval_db, patching_drift
):
    """Test remediate proceeds with pre-approved request."""
    # Create and approve a request
    request = approval_db.create_request(
        action_name="update_to_baseline_generation",
        drift_check="patching",
        site_id="test-site",
        host_id="test-host",
        pre_state={}
    )
    approval_db.approve(request.request_id, approved_by="admin")

    engine = HealingEngine(test_config, approval_manager=approval_db)

    with patch('compliance_agent.healing.run_command') as mock_run, \
         patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw:

        mock_mw.return_value = True
        mock_run.side_effect = [
            AsyncMock(stdout="999   2025-01-15 10:23:45"),
            AsyncMock(stdout="Switching"),
            AsyncMock(stdout="1000")
        ]

        result = await engine.remediate(
            patching_drift,
            approval_request_id=request.request_id
        )

        # Should proceed with approval
        assert result.outcome != "pending_approval"


@pytest.mark.asyncio
async def test_remediate_auto_approve_in_maintenance(
    healing_engine_with_approval, patching_drift
):
    """Test disruptive actions auto-approve in maintenance window."""
    with patch('compliance_agent.healing.run_command') as mock_run, \
         patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw:

        mock_mw.return_value = True  # In maintenance window
        mock_run.side_effect = [
            AsyncMock(stdout="999   2025-01-15 10:23:45"),
            AsyncMock(stdout="Switching"),
            AsyncMock(stdout="1000")
        ]

        result = await healing_engine_with_approval.remediate(patching_drift)

        # Should auto-approve in maintenance window
        assert result.outcome != "pending_approval"


@pytest.mark.asyncio
async def test_remediate_encryption_never_auto_approves(healing_engine_with_approval):
    """Test encryption actions never auto-approve even in maintenance."""
    encryption_drift = DriftResult(
        check="encryption",
        drifted=True,
        pre_state={"unencrypted_volumes": ["/dev/sda1"]},
        severity="critical"
    )

    with patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw:
        mock_mw.return_value = True  # Even in maintenance window

        result = await healing_engine_with_approval.remediate(encryption_drift)

        # Encryption should still require approval
        assert result.outcome == "pending_approval"


@pytest.mark.asyncio
async def test_get_pending_approvals(healing_engine_with_approval, patching_drift):
    """Test tracking pending approval requests."""
    with patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw:
        mock_mw.return_value = False

        # First call creates approval request
        await healing_engine_with_approval.remediate(patching_drift)

        pending = healing_engine_with_approval.get_pending_approvals()

        assert "patching" in pending
        assert pending["patching"].startswith("APR-")


@pytest.mark.asyncio
async def test_remediate_reuses_pending_request(healing_engine_with_approval, patching_drift):
    """Test that repeated calls reuse pending approval request."""
    with patch('compliance_agent.healing.is_within_maintenance_window') as mock_mw:
        mock_mw.return_value = False

        # First call
        result1 = await healing_engine_with_approval.remediate(patching_drift)
        request_id1 = result1.error.split(": ")[-1]

        # Second call should reuse same request
        result2 = await healing_engine_with_approval.remediate(patching_drift)
        request_id2 = result2.error.split(": ")[-1]

        assert request_id1 == request_id2
