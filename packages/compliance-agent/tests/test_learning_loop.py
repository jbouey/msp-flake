"""
Tests for the Self-Learning System (Data Flywheel).

Tests cover:
- Action parameter extraction from incidents
- Promotion candidate identification
- Rule generation from patterns
- Confidence score calculation
- Rollback tracking
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, MagicMock, patch

from compliance_agent.learning_loop import (
    SelfLearningSystem,
    PromotionCandidate,
    PromotionConfig
)
from compliance_agent.incident_db import PatternStats


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_promotion_dir(tmp_path):
    """Create temporary promotion output directory."""
    promotion_dir = tmp_path / "promoted"
    promotion_dir.mkdir()
    return promotion_dir


@pytest.fixture
def mock_incident_db():
    """Create a mock incident database."""
    db = Mock()
    db.get_promotion_candidates.return_value = []
    db.get_pattern_context.return_value = {}
    db.get_stats_summary.return_value = {
        "total_incidents": 0,
        "l1_percentage": 0,
        "l2_percentage": 0,
        "l3_percentage": 0,
        "success_rate": 0,
        "avg_resolution_time_ms": 0
    }
    db.mark_promoted.return_value = None
    return db


@pytest.fixture
def promotion_config(temp_promotion_dir):
    """Create promotion configuration."""
    return PromotionConfig(
        min_occurrences=3,
        min_l2_resolutions=2,
        min_success_rate=0.8,
        max_avg_resolution_time_ms=30000,
        promotion_output_dir=temp_promotion_dir,
        auto_promote=False
    )


@pytest.fixture
def learning_system(mock_incident_db, promotion_config):
    """Create SelfLearningSystem instance."""
    return SelfLearningSystem(
        incident_db=mock_incident_db,
        config=promotion_config
    )


# ============================================================================
# Action Parameter Extraction Tests
# ============================================================================

class TestExtractActionParams:
    """Tests for _extract_action_params method."""

    def test_extract_params_empty_incidents(self, learning_system):
        """Test with empty incidents list."""
        params = learning_system._extract_action_params([], "restart_av_service")
        assert params == {}

    def test_extract_params_no_raw_data(self, learning_system):
        """Test with incidents missing raw_data."""
        incidents = [
            {"id": "inc-001", "incident_type": "av_edr"},
            {"id": "inc-002", "incident_type": "av_edr"}
        ]
        params = learning_system._extract_action_params(incidents, "restart_av_service")
        assert params == {}

    def test_extract_params_invalid_raw_data(self, learning_system):
        """Test with invalid raw_data (not JSON)."""
        incidents = [
            {"id": "inc-001", "raw_data": "not valid json"},
            {"id": "inc-002", "raw_data": "also not json"}
        ]
        params = learning_system._extract_action_params(incidents, "restart_av_service")
        assert params == {}

    def test_extract_params_single_incident(self, learning_system):
        """Test parameter extraction from single incident."""
        incidents = [
            {
                "id": "inc-001",
                "raw_data": json.dumps({
                    "service_name": "defender",
                    "av_product": "Windows Defender",
                    "check_type": "av_edr"
                })
            }
        ]
        params = learning_system._extract_action_params(incidents, "restart_av_service")

        # Should extract common parameters
        assert "service_name" in params or "av_product" in params or "check_type" in params

    def test_extract_params_consistent_values(self, learning_system):
        """Test extraction when all incidents have same values."""
        incidents = [
            {
                "id": "inc-001",
                "raw_data": json.dumps({
                    "service_name": "nginx",
                    "timeout": 30
                })
            },
            {
                "id": "inc-002",
                "raw_data": json.dumps({
                    "service_name": "nginx",
                    "timeout": 30
                })
            },
            {
                "id": "inc-003",
                "raw_data": json.dumps({
                    "service_name": "nginx",
                    "timeout": 30
                })
            }
        ]
        params = learning_system._extract_action_params(incidents, "restart_logging_services")

        assert params.get("service_name") == "nginx"
        assert params.get("timeout") == 30

    def test_extract_params_majority_wins(self, learning_system):
        """Test that most common value is selected."""
        incidents = [
            {"id": "inc-001", "raw_data": json.dumps({"service_name": "nginx"})},
            {"id": "inc-002", "raw_data": json.dumps({"service_name": "nginx"})},
            {"id": "inc-003", "raw_data": json.dumps({"service_name": "apache"})},
            {"id": "inc-004", "raw_data": json.dumps({"service_name": "nginx"})},
        ]
        params = learning_system._extract_action_params(incidents, "restart_logging_services")

        # nginx appears 3 times, apache 1 time
        assert params.get("service_name") == "nginx"

    def test_extract_params_action_specific_keys(self, learning_system):
        """Test extraction of action-specific parameter keys."""
        incidents = [
            {
                "id": "inc-001",
                "raw_data": json.dumps({
                    "backup_repo": "/mnt/backup",
                    "backup_paths": ["/data", "/config"],
                    "retention_days": 30,
                    "unrelated_field": "ignored"
                })
            },
            {
                "id": "inc-002",
                "raw_data": json.dumps({
                    "backup_repo": "/mnt/backup",
                    "backup_paths": ["/data", "/config"],
                    "retention_days": 30
                })
            }
        ]
        params = learning_system._extract_action_params(incidents, "run_backup_job")

        # Should extract backup-specific params
        assert "backup_repo" in params
        assert params["backup_repo"] == "/mnt/backup"
        # unrelated_field should not be extracted
        assert "unrelated_field" not in params

    def test_extract_params_with_dict_raw_data(self, learning_system):
        """Test extraction when raw_data is already a dict (not JSON string)."""
        incidents = [
            {
                "id": "inc-001",
                "raw_data": {
                    "service_name": "rsyslog",
                    "log_destination": "/var/log"
                }
            },
            {
                "id": "inc-002",
                "raw_data": {
                    "service_name": "rsyslog",
                    "log_destination": "/var/log"
                }
            }
        ]
        params = learning_system._extract_action_params(incidents, "restart_logging_services")

        assert params.get("service_name") == "rsyslog"

    def test_extract_params_list_values(self, learning_system):
        """Test extraction of list values."""
        incidents = [
            {
                "id": "inc-001",
                "raw_data": json.dumps({
                    "logging_services": ["rsyslog", "journald"],
                    "service_name": "rsyslog"
                })
            },
            {
                "id": "inc-002",
                "raw_data": json.dumps({
                    "logging_services": ["rsyslog", "journald"],
                    "service_name": "rsyslog"
                })
            }
        ]
        params = learning_system._extract_action_params(incidents, "restart_logging_services")

        # List should be preserved
        if "logging_services" in params:
            assert params["logging_services"] == ["rsyslog", "journald"]

    def test_extract_params_encryption_action(self, learning_system):
        """Test parameter extraction for encryption action."""
        incidents = [
            {
                "id": "inc-001",
                "raw_data": json.dumps({
                    "volume_path": "/dev/sda1",
                    "encryption_type": "luks",
                    "key_file": "/etc/luks/key"
                })
            },
            {
                "id": "inc-002",
                "raw_data": json.dumps({
                    "volume_path": "/dev/sda1",
                    "encryption_type": "luks",
                    "key_file": "/etc/luks/key"
                })
            }
        ]
        params = learning_system._extract_action_params(incidents, "enable_volume_encryption")

        assert "volume_path" in params or "encryption_type" in params

    def test_extract_params_patching_action(self, learning_system):
        """Test parameter extraction for patching action."""
        incidents = [
            {
                "id": "inc-001",
                "raw_data": json.dumps({
                    "target_generation": 42,
                    "baseline_hash": "abc123",
                    "flake_url": "github:myorg/nixos-config"
                })
            },
            {
                "id": "inc-002",
                "raw_data": json.dumps({
                    "target_generation": 42,
                    "baseline_hash": "abc123",
                    "flake_url": "github:myorg/nixos-config"
                })
            }
        ]
        params = learning_system._extract_action_params(incidents, "update_to_baseline_generation")

        assert "target_generation" in params or "baseline_hash" in params

    def test_extract_params_firewall_action(self, learning_system):
        """Test parameter extraction for firewall action."""
        incidents = [
            {
                "id": "inc-001",
                "raw_data": json.dumps({
                    "ruleset_path": "/etc/nftables.conf",
                    "allowed_ports": [22, 80, 443]
                })
            },
            {
                "id": "inc-002",
                "raw_data": json.dumps({
                    "ruleset_path": "/etc/nftables.conf",
                    "allowed_ports": [22, 80, 443]
                })
            }
        ]
        params = learning_system._extract_action_params(incidents, "restore_firewall_baseline")

        assert "ruleset_path" in params or "allowed_ports" in params


# ============================================================================
# Promotion Criteria Tests
# ============================================================================

class TestPromotionCriteria:
    """Tests for _meets_promotion_criteria method."""

    def test_meets_criteria_success(self, learning_system):
        """Test pattern that meets all criteria."""
        stats = PatternStats(
            pattern_signature="test-pattern-001",
            total_occurrences=10,
            l1_resolutions=0,
            l2_resolutions=5,
            l3_resolutions=0,
            success_rate=0.95,
            avg_resolution_time_ms=5000,
            last_seen="2025-12-03T00:00:00Z"
        )

        assert learning_system._meets_promotion_criteria(stats) is True

    def test_fails_min_occurrences(self, learning_system):
        """Test pattern with too few occurrences."""
        stats = PatternStats(
            pattern_signature="test-pattern-002",
            total_occurrences=1,  # Below min_occurrences=3
            l1_resolutions=0,
            l2_resolutions=1,
            l3_resolutions=0,
            success_rate=1.0,
            avg_resolution_time_ms=1000,
            last_seen="2025-12-03T00:00:00Z"
        )

        assert learning_system._meets_promotion_criteria(stats) is False

    def test_fails_min_l2_resolutions(self, learning_system):
        """Test pattern with too few L2 resolutions."""
        stats = PatternStats(
            pattern_signature="test-pattern-003",
            total_occurrences=10,
            l1_resolutions=9,
            l2_resolutions=1,  # Below min_l2_resolutions=2
            l3_resolutions=0,
            success_rate=1.0,
            avg_resolution_time_ms=1000,
            last_seen="2025-12-03T00:00:00Z"
        )

        assert learning_system._meets_promotion_criteria(stats) is False

    def test_fails_success_rate(self, learning_system):
        """Test pattern with low success rate."""
        stats = PatternStats(
            pattern_signature="test-pattern-004",
            total_occurrences=10,
            l1_resolutions=0,
            l2_resolutions=5,
            l3_resolutions=0,
            success_rate=0.5,  # Below min_success_rate=0.8
            avg_resolution_time_ms=1000,
            last_seen="2025-12-03T00:00:00Z"
        )

        assert learning_system._meets_promotion_criteria(stats) is False

    def test_fails_resolution_time(self, learning_system):
        """Test pattern with too long resolution time."""
        stats = PatternStats(
            pattern_signature="test-pattern-005",
            total_occurrences=10,
            l1_resolutions=0,
            l2_resolutions=5,
            l3_resolutions=0,
            success_rate=1.0,
            avg_resolution_time_ms=60000,  # Above max_avg_resolution_time_ms=30000
            last_seen="2025-12-03T00:00:00Z"
        )

        assert learning_system._meets_promotion_criteria(stats) is False


# ============================================================================
# Confidence Calculation Tests
# ============================================================================

class TestConfidenceCalculation:
    """Tests for _calculate_confidence method."""

    def test_high_confidence(self, learning_system):
        """Test confidence for excellent pattern."""
        stats = PatternStats(
            pattern_signature="test-pattern",
            total_occurrences=50,
            l1_resolutions=0,
            l2_resolutions=50,
            l3_resolutions=0,
            success_rate=0.98,
            avg_resolution_time_ms=1000,
            last_seen=datetime.now(timezone.utc).isoformat()
        )
        successful_actions = [
            {"resolution_action": "restart_service", "count": 48},
            {"resolution_action": "other_action", "count": 2}
        ]

        confidence = learning_system._calculate_confidence(stats, successful_actions)

        # Should be high confidence
        assert confidence >= 0.9

    def test_low_confidence_due_to_success_rate(self, learning_system):
        """Test confidence for pattern with lower success rate."""
        stats = PatternStats(
            pattern_signature="test-pattern",
            total_occurrences=10,
            l1_resolutions=0,
            l2_resolutions=10,
            l3_resolutions=0,
            success_rate=0.7,  # Lower success rate
            avg_resolution_time_ms=1000,
            last_seen=datetime.now(timezone.utc).isoformat()
        )
        successful_actions = [
            {"resolution_action": "restart_service", "count": 7}
        ]

        confidence = learning_system._calculate_confidence(stats, successful_actions)

        # Should be moderate confidence
        assert 0.6 <= confidence < 0.9

    def test_confidence_bounded(self, learning_system):
        """Test that confidence is always between 0 and 1."""
        stats = PatternStats(
            pattern_signature="test-pattern",
            total_occurrences=1000,  # Very high
            l1_resolutions=0,
            l2_resolutions=1000,
            l3_resolutions=0,
            success_rate=1.0,
            avg_resolution_time_ms=100,
            last_seen=datetime.now(timezone.utc).isoformat()
        )
        successful_actions = [
            {"resolution_action": "restart_service", "count": 1000}
        ]

        confidence = learning_system._calculate_confidence(stats, successful_actions)

        assert 0.0 <= confidence <= 1.0


# ============================================================================
# Rule Generation Tests
# ============================================================================

class TestRuleGeneration:
    """Tests for generate_rule method."""

    def test_generate_rule_basic(self, learning_system):
        """Test basic rule generation from candidate."""
        stats = PatternStats(
            pattern_signature="patching-drift-001",
            total_occurrences=10,
            l1_resolutions=0,
            l2_resolutions=10,
            l3_resolutions=0,
            success_rate=0.95,
            avg_resolution_time_ms=5000,
            last_seen="2025-12-03T00:00:00Z"
        )

        candidate = PromotionCandidate(
            pattern_signature="patching-drift-001",
            stats=stats,
            sample_incidents=[
                {
                    "id": "inc-001",
                    "incident_type": "patching",
                    "raw_data": json.dumps({"check_type": "patching", "drift_detected": True})
                }
            ],
            recommended_action="update_to_baseline_generation",
            action_params={"target_generation": 42},
            confidence_score=0.92,
            promotion_reason="High confidence pattern"
        )

        rule = learning_system.generate_rule(candidate)

        assert rule is not None
        assert rule.id.startswith("L1-PROMOTED-")
        assert rule.action == "update_to_baseline_generation"
        assert rule.action_params.get("target_generation") == 42
        assert rule.enabled is True
        assert rule.source == "promoted"


# ============================================================================
# Flywheel Health Tests
# ============================================================================

class TestFlywheelHealth:
    """Tests for _assess_flywheel_health method."""

    def test_excellent_health(self, learning_system):
        """Test excellent flywheel health assessment."""
        stats = {"l1_percentage": 75, "success_rate": 96}
        health = learning_system._assess_flywheel_health(stats)
        assert health == "excellent"

    def test_good_health(self, learning_system):
        """Test good flywheel health assessment."""
        stats = {"l1_percentage": 55, "success_rate": 88}
        health = learning_system._assess_flywheel_health(stats)
        assert health == "good"

    def test_developing_health(self, learning_system):
        """Test developing flywheel health assessment."""
        stats = {"l1_percentage": 35, "success_rate": 75}
        health = learning_system._assess_flywheel_health(stats)
        assert health == "developing"

    def test_needs_attention(self, learning_system):
        """Test needs_attention flywheel health assessment."""
        stats = {"l1_percentage": 20, "success_rate": 50}
        health = learning_system._assess_flywheel_health(stats)
        assert health == "needs_attention"


# ============================================================================
# HIPAA Control Extraction Tests
# ============================================================================

class TestHIPAAControlExtraction:
    """Tests for _extract_hipaa_controls method."""

    def test_patching_controls(self, learning_system):
        """Test HIPAA controls for patching incidents."""
        incidents = [{"incident_type": "patching"}]
        controls = learning_system._extract_hipaa_controls(incidents)
        assert "164.308(a)(5)(ii)(B)" in controls

    def test_backup_controls(self, learning_system):
        """Test HIPAA controls for backup incidents."""
        incidents = [{"incident_type": "backup"}]
        controls = learning_system._extract_hipaa_controls(incidents)
        assert "164.308(a)(7)(ii)(A)" in controls

    def test_logging_controls(self, learning_system):
        """Test HIPAA controls for logging incidents."""
        incidents = [{"incident_type": "logging"}]
        controls = learning_system._extract_hipaa_controls(incidents)
        assert "164.312(b)" in controls

    def test_encryption_controls(self, learning_system):
        """Test HIPAA controls for encryption incidents."""
        incidents = [{"incident_type": "encryption"}]
        controls = learning_system._extract_hipaa_controls(incidents)
        assert "164.312(a)(2)(iv)" in controls

    def test_empty_incidents(self, learning_system):
        """Test HIPAA controls for empty incidents."""
        controls = learning_system._extract_hipaa_controls([])
        assert controls == []


# ============================================================================
# Promotion Report Tests
# ============================================================================

class TestPromotionReport:
    """Tests for get_promotion_report method."""

    def test_empty_report(self, learning_system, mock_incident_db):
        """Test promotion report with no candidates."""
        mock_incident_db.get_promotion_candidates.return_value = []

        report = learning_system.get_promotion_report()

        assert report["total_candidates"] == 0
        assert report["candidates"] == []
        assert "promotion_criteria" in report

    def test_report_includes_criteria(self, learning_system):
        """Test that report includes promotion criteria."""
        report = learning_system.get_promotion_report()

        criteria = report["promotion_criteria"]
        assert "min_occurrences" in criteria
        assert "min_l2_resolutions" in criteria
        assert "min_success_rate" in criteria


# ============================================================================
# Learning Metrics Tests
# ============================================================================

class TestLearningMetrics:
    """Tests for get_learning_metrics method."""

    def test_basic_metrics(self, learning_system, mock_incident_db):
        """Test basic learning metrics retrieval."""
        mock_incident_db.get_stats_summary.return_value = {
            "total_incidents": 100,
            "l1_percentage": 60,
            "l2_percentage": 30,
            "l3_percentage": 10,
            "success_rate": 85,
            "avg_resolution_time_ms": 5000
        }

        metrics = learning_system.get_learning_metrics(days=30)

        assert metrics["period_days"] == 30
        assert metrics["total_incidents"] == 100
        assert "resolution_breakdown" in metrics
        assert "flywheel_status" in metrics


# ============================================================================
# Rollback Tracking Tests
# ============================================================================

class TestRollbackTracking:
    """Tests for rollback tracking functionality."""

    def test_get_rollback_history_empty(self, learning_system):
        """Test rollback history with no rollbacks."""
        history = learning_system.get_rollback_history()

        assert isinstance(history, list)
        assert len(history) == 0

    def test_monitor_promoted_rules_no_rules(self, learning_system):
        """Test monitoring with no promoted rules."""
        report = learning_system.monitor_promoted_rules()

        assert report["rules_monitored"] == 0
        assert report["rules_healthy"] == 0
        assert report["rules_degraded"] == 0
        assert report["rollbacks_triggered"] == []
        assert report["rule_details"] == []

    def test_monitor_promoted_rules_structure(self, learning_system):
        """Test that monitoring report has correct structure."""
        report = learning_system.monitor_promoted_rules()

        assert "monitored_at" in report
        assert "rules_monitored" in report
        assert "rules_healthy" in report
        assert "rules_degraded" in report
        assert "rollbacks_triggered" in report
        assert "rule_details" in report

    def test_rollback_on_failure_rate_config(self, learning_system):
        """Test rollback configuration is accessible."""
        config = learning_system.config

        assert hasattr(config, 'rollback_on_failure_rate')
        assert 0.0 <= config.rollback_on_failure_rate <= 1.0

    def test_get_post_promotion_stats_no_data(self, temp_promotion_dir):
        """Test post-promotion stats with no matching data using real DB."""
        import tempfile
        from compliance_agent.incident_db import IncidentDatabase

        # Create a real (but empty) incident database
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test_incidents.db"
            real_db = IncidentDatabase(db_path=str(db_path))

            config = PromotionConfig(
                promotion_output_dir=temp_promotion_dir
            )

            learning_system = SelfLearningSystem(
                incident_db=real_db,
                config=config
            )

            stats = learning_system._get_post_promotion_stats(
                rule_id="test-rule-001",
                promoted_at="2025-01-01T00:00:00Z"
            )

            assert stats["total"] == 0
            assert stats["successes"] == 0
            assert stats["failures"] == 0
            assert stats["success_rate"] == 1.0
            assert stats["failure_rate"] == 0.0

    def test_get_post_promotion_stats_invalid_date(self, learning_system):
        """Test post-promotion stats with invalid date."""
        stats = learning_system._get_post_promotion_stats(
            rule_id="test-rule-001",
            promoted_at="invalid-date"
        )

        assert stats["total"] == 0
        assert stats["success_rate"] == 1.0

    def test_rollback_rule_method_exists(self, learning_system):
        """Test that _rollback_rule method exists."""
        assert hasattr(learning_system, '_rollback_rule')
        assert callable(learning_system._rollback_rule)
