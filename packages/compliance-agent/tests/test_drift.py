"""
Comprehensive tests for drift detection module.

Tests all 6 drift detection checks with mocked system state.
"""

import pytest
import json
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from typing import Any, Dict

from compliance_agent.drift import DriftDetector
from compliance_agent.models import DriftResult
from compliance_agent.config import AgentConfig


@pytest.fixture
def mock_config(tmp_path):
    """Create mock configuration."""
    baseline_path = tmp_path / "baseline.yaml"
    baseline_path.write_text("""
patching:
  expected_generation: 123
  max_generation_age_days: 30

av_edr:
  service_name: clamav
  binary_path: /usr/bin/clamscan
  binary_hash: abc123def456

backup:
  max_age_hours: 24
  status_file: /var/lib/compliance-agent/backup-status.json

logging:
  services:
    - rsyslog
    - systemd-journald

firewall:
  service: nftables
  ruleset_hash: firewall123hash

encryption:
  luks_volumes:
    - cryptroot
    - crypthome
""")

    # Create minimal config
    config = MagicMock(spec=AgentConfig)
    config.baseline_path = str(baseline_path)
    config.site_id = "test-site"
    config.host_id = "test-host"
    
    return config


@pytest.fixture
def detector(mock_config):
    """Create DriftDetector instance."""
    return DriftDetector(mock_config)


# =============================================================================
# Test: Initialization
# =============================================================================


@pytest.mark.asyncio
async def test_detector_initialization(detector):
    """Test DriftDetector initialization."""
    assert detector.config is not None
    assert detector.baseline_path is not None
    assert detector._baseline_cache is None


@pytest.mark.asyncio
async def test_load_baseline(detector):
    """Test baseline loading and caching."""
    baseline = await detector._load_baseline()
    
    assert isinstance(baseline, dict)
    assert 'patching' in baseline
    assert 'av_edr' in baseline
    assert 'backup' in baseline
    
    # Test caching
    baseline2 = await detector._load_baseline()
    assert baseline2 is baseline  # Same object


# =============================================================================
# Test: Patching Check
# =============================================================================


@pytest.mark.asyncio
async def test_patching_no_drift(detector):
    """Test patching check with no drift."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        # Mock current system
        mock_run.side_effect = [
            AsyncMock(stdout="/nix/store/hash-nixos-system"),
            AsyncMock(stdout="123   2025-01-15 10:23:45"),
            AsyncMock(stdout="123   2025-01-15 10:23:45")
        ]
        
        result = await detector.check_patching()
        
        assert isinstance(result, DriftResult)
        assert result.check == "patching"
        assert result.drifted is False
        assert result.severity == "low"
        assert result.recommended_action is None
        assert "164.308(a)(5)(ii)(B)" in result.hipaa_controls


@pytest.mark.asyncio
async def test_patching_generation_drift(detector):
    """Test patching check with generation mismatch."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.side_effect = [
            AsyncMock(stdout="/nix/store/hash-nixos-system"),
            AsyncMock(stdout="999   2025-01-15 10:23:45"),  # Different generation
            AsyncMock(stdout="999   2025-01-15 10:23:45")
        ]
        
        result = await detector.check_patching()
        
        assert result.drifted is True
        assert result.severity == "medium"
        assert result.recommended_action == "update_to_baseline_generation"
        assert result.pre_state["current_generation"] == 999
        assert result.pre_state["baseline_generation"] == 123


@pytest.mark.asyncio
async def test_patching_age_drift(detector):
    """Test patching check with old generation."""
    old_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
    
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.side_effect = [
            AsyncMock(stdout="/nix/store/hash-nixos-system"),
            AsyncMock(stdout="123   2025-01-15 10:23:45"),
            AsyncMock(stdout=f"123   {old_date}")
        ]
        
        result = await detector.check_patching()
        
        assert result.drifted is True
        assert result.severity == "high"
        assert result.recommended_action == "apply_system_updates"
        assert result.pre_state["generation_age_days"] > 30


@pytest.mark.asyncio
async def test_patching_command_failure(detector):
    """Test patching check with command failure."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.side_effect = Exception("Command failed")
        
        result = await detector.check_patching()
        
        assert isinstance(result, DriftResult)
        assert result.check == "patching"
        assert result.severity == "critical"
        assert "error" in result.pre_state


# =============================================================================
# Test: AV/EDR Health Check
# =============================================================================


@pytest.mark.asyncio
async def test_av_edr_no_drift(detector):
    """Test AV/EDR check with no drift."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.return_value = AsyncMock(stdout="active")
        
        with patch('builtins.open', create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = b"test"
            
            with patch('pathlib.Path.exists', return_value=True):
                with patch('hashlib.sha256') as mock_hash:
                    mock_hash.return_value.hexdigest.return_value = "abc123def456"
                    
                    result = await detector.check_av_edr_health()
                    
                    assert result.drifted is False
                    assert result.severity == "low"
                    assert result.pre_state["service_active"] is True
                    assert result.pre_state["hash_matches"] is True


@pytest.mark.asyncio
async def test_av_edr_service_inactive(detector):
    """Test AV/EDR check with inactive service."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.return_value = AsyncMock(stdout="inactive")
        
        with patch('pathlib.Path.exists', return_value=True):
            result = await detector.check_av_edr_health()
            
            assert result.drifted is True
            assert result.severity == "critical"
            assert result.recommended_action == "restart_av_service"
            assert result.pre_state["service_active"] is False


@pytest.mark.asyncio
async def test_av_edr_hash_mismatch(detector):
    """Test AV/EDR check with binary hash mismatch."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.return_value = AsyncMock(stdout="active")
        
        with patch('builtins.open', create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = b"test"
            
            with patch('pathlib.Path.exists', return_value=True):
                with patch('hashlib.sha256') as mock_hash:
                    mock_hash.return_value.hexdigest.return_value = "different_hash"
                    
                    result = await detector.check_av_edr_health()
                    
                    assert result.drifted is True
                    assert result.severity == "high"
                    assert result.recommended_action == "verify_av_binary_integrity"
                    assert result.pre_state["hash_matches"] is False


# =============================================================================
# Test: Backup Verification Check
# =============================================================================


@pytest.mark.asyncio
async def test_backup_no_drift(detector, tmp_path):
    """Test backup check with no drift."""
    # Create backup status file
    status_file = tmp_path / "backup-status.json"
    status_data = {
        "last_backup": (datetime.utcnow() - timedelta(hours=1)).isoformat(),
        "last_restore_test": (datetime.utcnow() - timedelta(days=7)).isoformat(),
        "checksum": "backup123hash"
    }
    status_file.write_text(json.dumps(status_data))
    
    # Update baseline to use test status file
    detector._baseline_cache = {
        'backup': {
            'max_age_hours': 24,
            'status_file': str(status_file)
        }
    }
    
    result = await detector.check_backup_verification()
    
    assert result.drifted is False
    assert result.severity == "low"
    assert result.pre_state["backup_age_hours"] < 24


@pytest.mark.asyncio
async def test_backup_age_drift(detector, tmp_path):
    """Test backup check with old backup."""
    status_file = tmp_path / "backup-status.json"
    status_data = {
        "last_backup": (datetime.utcnow() - timedelta(hours=48)).isoformat(),
        "last_restore_test": (datetime.utcnow() - timedelta(days=7)).isoformat()
    }
    status_file.write_text(json.dumps(status_data))
    
    detector._baseline_cache = {
        'backup': {
            'max_age_hours': 24,
            'status_file': str(status_file)
        }
    }
    
    result = await detector.check_backup_verification()
    
    assert result.drifted is True
    assert result.severity in ["high", "critical"]
    assert result.recommended_action == "run_backup_job"
    assert result.pre_state["backup_age_hours"] > 24


@pytest.mark.asyncio
async def test_backup_restore_test_drift(detector, tmp_path):
    """Test backup check with old restore test."""
    status_file = tmp_path / "backup-status.json"
    status_data = {
        "last_backup": datetime.utcnow().isoformat(),
        "last_restore_test": (datetime.utcnow() - timedelta(days=45)).isoformat()
    }
    status_file.write_text(json.dumps(status_data))
    
    detector._baseline_cache = {
        'backup': {
            'max_age_hours': 24,
            'status_file': str(status_file)
        }
    }
    
    result = await detector.check_backup_verification()
    
    assert result.drifted is True
    assert result.recommended_action == "run_restore_test"
    assert result.pre_state["restore_test_age_days"] > 30


# =============================================================================
# Test: Logging Continuity Check
# =============================================================================


@pytest.mark.asyncio
async def test_logging_no_drift(detector):
    """Test logging check with no drift."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.side_effect = [
            AsyncMock(stdout="active"),  # rsyslog
            AsyncMock(stdout="active"),  # systemd-journald
            AsyncMock(stdout="CANARY: test")  # canary check
        ]
        
        result = await detector.check_logging_continuity()
        
        assert result.drifted is False
        assert result.severity == "low"
        assert result.pre_state["services_status"]["rsyslog"] is True
        assert result.pre_state["services_status"]["systemd-journald"] is True


@pytest.mark.asyncio
async def test_logging_service_inactive(detector):
    """Test logging check with inactive service."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.side_effect = [
            AsyncMock(stdout="inactive"),  # rsyslog
            AsyncMock(stdout="active"),    # systemd-journald
            AsyncMock(stdout="")           # canary check
        ]
        
        result = await detector.check_logging_continuity()
        
        assert result.drifted is True
        assert result.severity == "high"
        assert result.recommended_action == "restart_logging_services"
        assert result.pre_state["services_status"]["rsyslog"] is False


@pytest.mark.asyncio
async def test_logging_canary_missing(detector):
    """Test logging check with missing canary."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.side_effect = [
            AsyncMock(stdout="active"),
            AsyncMock(stdout="active"),
            AsyncMock(stdout="")  # No canary found
        ]
        
        result = await detector.check_logging_continuity()
        
        assert result.drifted is True
        assert result.severity in ["medium", "high"]
        assert result.recommended_action in ["investigate_log_delivery", "restart_logging_services"]


# =============================================================================
# Test: Firewall Baseline Check
# =============================================================================


@pytest.mark.asyncio
async def test_firewall_no_drift(detector):
    """Test firewall check with no drift."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.side_effect = [
            AsyncMock(stdout="active"),
            AsyncMock(stdout="table inet filter { chain input { ... } }")
        ]
        
        with patch('hashlib.sha256') as mock_hash:
            mock_hash.return_value.hexdigest.return_value = "firewall123hash"
            
            result = await detector.check_firewall_baseline()
            
            assert result.drifted is False
            assert result.severity == "low"
            assert result.pre_state["service_active"] is True


@pytest.mark.asyncio
async def test_firewall_service_inactive(detector):
    """Test firewall check with inactive service."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.side_effect = [
            AsyncMock(stdout="inactive"),
            AsyncMock(stdout="")
        ]
        
        result = await detector.check_firewall_baseline()
        
        assert result.drifted is True
        assert result.severity == "critical"
        assert result.recommended_action == "start_firewall_service"


@pytest.mark.asyncio
async def test_firewall_ruleset_drift(detector):
    """Test firewall check with ruleset hash mismatch."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.side_effect = [
            AsyncMock(stdout="active"),
            AsyncMock(stdout="table inet filter { chain input { ... } }")
        ]
        
        with patch('hashlib.sha256') as mock_hash:
            mock_hash.return_value.hexdigest.return_value = "different_hash"
            
            result = await detector.check_firewall_baseline()
            
            assert result.drifted is True
            assert result.severity == "high"
            assert result.recommended_action == "restore_firewall_baseline"


# =============================================================================
# Test: Encryption Check
# =============================================================================


@pytest.mark.asyncio
async def test_encryption_no_drift(detector):
    """Test encryption check with all volumes encrypted."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.side_effect = [
            AsyncMock(stdout="type: LUKS2"),  # cryptroot
            AsyncMock(stdout="type: LUKS2")   # crypthome
        ]
        
        result = await detector.check_encryption()
        
        assert result.drifted is False
        assert result.severity == "low"
        assert result.pre_state["luks_status"]["cryptroot"] is True
        assert result.pre_state["luks_status"]["crypthome"] is True


@pytest.mark.asyncio
async def test_encryption_volume_unencrypted(detector):
    """Test encryption check with unencrypted volume."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.side_effect = [
            AsyncMock(stdout="type: LUKS2"),  # cryptroot
            AsyncMock(stdout="")              # crypthome not LUKS
        ]
        
        result = await detector.check_encryption()
        
        assert result.drifted is True
        assert result.severity == "critical"
        assert result.recommended_action == "enable_volume_encryption"
        assert result.pre_state["luks_status"]["crypthome"] is False


@pytest.mark.asyncio
async def test_encryption_volume_check_failure(detector):
    """Test encryption check with command failure."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.side_effect = [
            Exception("cryptsetup failed"),
            Exception("cryptsetup failed")
        ]
        
        result = await detector.check_encryption()
        
        assert result.drifted is True
        assert result.severity == "critical"
        assert all(v is False for v in result.pre_state["luks_status"].values())


# =============================================================================
# Test: check_all Integration
# =============================================================================


@pytest.mark.asyncio
async def test_check_all_no_drift(detector):
    """Test running all checks with no drift."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        # Mock all commands to return success
        mock_run.return_value = AsyncMock(stdout="active")
        
        with patch('pathlib.Path.exists', return_value=False):
            results = await detector.check_all()
            
            assert isinstance(results, list)
            assert len(results) == 6  # 6 checks
            assert all(isinstance(r, DriftResult) for r in results)


@pytest.mark.asyncio
async def test_check_all_with_drift(detector, tmp_path):
    """Test running all checks with some drift."""
    # Create backup status with old backup
    status_file = tmp_path / "backup-status.json"
    status_data = {
        "last_backup": (datetime.utcnow() - timedelta(hours=48)).isoformat()
    }
    status_file.write_text(json.dumps(status_data))
    
    detector._baseline_cache = {
        'patching': {'expected_generation': 123, 'max_generation_age_days': 30},
        'av_edr': {'service_name': 'clamav'},
        'backup': {'max_age_hours': 24, 'status_file': str(status_file)},
        'logging': {'services': ['rsyslog']},
        'firewall': {'service': 'nftables'},
        'encryption': {'luks_volumes': ['cryptroot']}
    }
    
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.return_value = AsyncMock(stdout="active")
        
        with patch('pathlib.Path.exists', return_value=False):
            results = await detector.check_all()
            
            assert len(results) == 6
            drifted_count = sum(1 for r in results if r.drifted)
            assert drifted_count >= 1  # At least backup should be drifted


@pytest.mark.asyncio
async def test_check_all_with_exception(detector):
    """Test check_all handles exceptions gracefully."""
    with patch.object(detector, 'check_patching', side_effect=Exception("Test error")):
        with patch.object(detector, 'check_av_edr_health', return_value=DriftResult(
            check="av_edr_health", drifted=False, severity="low"
        )):
            results = await detector.check_all()
            
            # Should return results from successful checks only
            assert isinstance(results, list)
            # Will have fewer than 6 results due to exception


# =============================================================================
# Test: HIPAA Controls Mapping
# =============================================================================


@pytest.mark.asyncio
async def test_hipaa_controls_all_checks(detector):
    """Test that all checks have HIPAA control mappings."""
    with patch('compliance_agent.drift.run_command') as mock_run:
        mock_run.return_value = AsyncMock(stdout="")
        
        with patch('pathlib.Path.exists', return_value=False):
            results = await detector.check_all()
            
            for result in results:
                assert result.hipaa_controls is not None
                assert len(result.hipaa_controls) > 0
                assert all(isinstance(c, str) for c in result.hipaa_controls)
