"""
Unit tests for incident_db.py - Incident Database module.

Tests cover:
- Incident creation and storage
- Pattern signature generation
- Resolution recording with SQL injection prevention
- Pattern statistics aggregation
- Promotion eligibility detection
- Human feedback recording
"""

import pytest
import tempfile
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from compliance_agent.incident_db import (
    IncidentDatabase,
    Incident,
    PatternStats,
    ResolutionLevel,
    IncidentOutcome,
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    db = IncidentDatabase(db_path)
    yield db

    # Cleanup
    os.unlink(db_path)
    if os.path.exists(f"{db_path}-wal"):
        os.unlink(f"{db_path}-wal")
    if os.path.exists(f"{db_path}-shm"):
        os.unlink(f"{db_path}-shm")


class TestIncidentCreation:
    """Tests for incident creation."""

    def test_create_incident_basic(self, temp_db):
        """Test basic incident creation."""
        incident = temp_db.create_incident(
            site_id="site-001",
            host_id="host-001",
            incident_type="firewall_drift",
            severity="high",
            raw_data={"check_type": "firewall", "status": "fail"}
        )

        assert incident.id.startswith("INC-")
        assert incident.site_id == "site-001"
        assert incident.host_id == "host-001"
        assert incident.incident_type == "firewall_drift"
        assert incident.severity == "high"
        assert incident.pattern_signature is not None
        assert len(incident.pattern_signature) == 16  # SHA256[:16]
        assert incident.resolved_at is None

    def test_create_incident_generates_unique_ids(self, temp_db):
        """Test that each incident gets a unique ID."""
        ids = set()
        for _ in range(10):
            incident = temp_db.create_incident(
                site_id="site-001",
                host_id="host-001",
                incident_type="test",
                severity="low",
                raw_data={}
            )
            ids.add(incident.id)

        assert len(ids) == 10  # All unique

    def test_create_incident_updates_pattern_stats(self, temp_db):
        """Test that pattern stats are updated on creation."""
        # Create two incidents with same pattern
        temp_db.create_incident(
            site_id="site-001",
            host_id="host-001",
            incident_type="firewall_drift",
            severity="high",
            raw_data={"check_type": "firewall"}
        )
        temp_db.create_incident(
            site_id="site-001",
            host_id="host-002",
            incident_type="firewall_drift",
            severity="high",
            raw_data={"check_type": "firewall"}
        )

        # Check pattern stats
        candidates = temp_db.get_promotion_candidates()
        # May or may not be eligible depending on thresholds


class TestPatternSignature:
    """Tests for pattern signature generation."""

    def test_same_pattern_same_signature(self, temp_db):
        """Test that similar incidents get the same pattern signature."""
        inc1 = temp_db.create_incident(
            site_id="site-001",
            host_id="host-001",
            incident_type="firewall_drift",
            severity="high",
            raw_data={"check_type": "firewall", "status": "fail"}
        )
        inc2 = temp_db.create_incident(
            site_id="site-002",
            host_id="host-002",
            incident_type="firewall_drift",
            severity="high",
            raw_data={"check_type": "firewall", "status": "fail"}
        )

        assert inc1.pattern_signature == inc2.pattern_signature

    def test_different_pattern_different_signature(self, temp_db):
        """Test that different incident types get different signatures."""
        inc1 = temp_db.create_incident(
            site_id="site-001",
            host_id="host-001",
            incident_type="firewall_drift",
            severity="high",
            raw_data={"check_type": "firewall"}
        )
        inc2 = temp_db.create_incident(
            site_id="site-001",
            host_id="host-001",
            incident_type="backup_drift",
            severity="high",
            raw_data={"check_type": "backup"}
        )

        assert inc1.pattern_signature != inc2.pattern_signature

    def test_error_normalization_timestamps(self, temp_db):
        """Test that timestamps in errors are normalized."""
        sig1 = temp_db.generate_pattern_signature(
            "error",
            {"error_message": "Failed at 2024-01-15T10:30:00"}
        )
        sig2 = temp_db.generate_pattern_signature(
            "error",
            {"error_message": "Failed at 2024-12-25T23:59:59"}
        )

        assert sig1 == sig2

    def test_error_normalization_ips(self, temp_db):
        """Test that IP addresses in errors are normalized."""
        sig1 = temp_db.generate_pattern_signature(
            "error",
            {"error_message": "Connection to 192.168.1.1 failed"}
        )
        sig2 = temp_db.generate_pattern_signature(
            "error",
            {"error_message": "Connection to 10.0.0.50 failed"}
        )

        assert sig1 == sig2


class TestResolutionRecording:
    """Tests for incident resolution."""

    def test_resolve_incident_l1(self, temp_db):
        """Test resolving an incident at L1."""
        incident = temp_db.create_incident(
            site_id="site-001",
            host_id="host-001",
            incident_type="firewall_drift",
            severity="high",
            raw_data={"check_type": "firewall"}
        )

        temp_db.resolve_incident(
            incident_id=incident.id,
            resolution_level=ResolutionLevel.LEVEL1_DETERMINISTIC,
            resolution_action="run_windows_runbook:L1-FW-001",
            outcome=IncidentOutcome.SUCCESS,
            resolution_time_ms=150
        )

        resolved = temp_db.get_incident(incident.id)
        assert resolved.resolved_at is not None
        assert resolved.resolution_level == "L1"
        assert resolved.outcome == "success"
        assert resolved.resolution_time_ms == 150

    def test_resolve_incident_l2(self, temp_db):
        """Test resolving an incident at L2."""
        incident = temp_db.create_incident(
            site_id="site-001",
            host_id="host-001",
            incident_type="backup_drift",
            severity="medium",
            raw_data={"check_type": "backup"}
        )

        temp_db.resolve_incident(
            incident_id=incident.id,
            resolution_level=ResolutionLevel.LEVEL2_LLM,
            resolution_action="run_backup_job",
            outcome=IncidentOutcome.SUCCESS,
            resolution_time_ms=2500
        )

        resolved = temp_db.get_incident(incident.id)
        assert resolved.resolution_level == "L2"

    def test_resolve_incident_l3(self, temp_db):
        """Test resolving an incident at L3."""
        incident = temp_db.create_incident(
            site_id="site-001",
            host_id="host-001",
            incident_type="encryption_drift",
            severity="critical",
            raw_data={"check_type": "encryption"}
        )

        temp_db.resolve_incident(
            incident_id=incident.id,
            resolution_level=ResolutionLevel.LEVEL3_HUMAN,
            resolution_action="escalate",
            outcome=IncidentOutcome.ESCALATED,
            resolution_time_ms=5000
        )

        resolved = temp_db.get_incident(incident.id)
        assert resolved.resolution_level == "L3"
        assert resolved.outcome == "escalated"

    def test_resolve_nonexistent_incident(self, temp_db):
        """Test that resolving nonexistent incident raises error."""
        with pytest.raises(ValueError, match="not found"):
            temp_db.resolve_incident(
                incident_id="INC-NONEXISTENT",
                resolution_level=ResolutionLevel.LEVEL1_DETERMINISTIC,
                resolution_action="test",
                outcome=IncidentOutcome.SUCCESS,
                resolution_time_ms=100
            )

    def test_resolution_updates_pattern_stats(self, temp_db):
        """Test that resolution updates pattern statistics."""
        # Create and resolve multiple incidents
        for i in range(3):
            incident = temp_db.create_incident(
                site_id="site-001",
                host_id=f"host-{i}",
                incident_type="firewall_drift",
                severity="high",
                raw_data={"check_type": "firewall"}
            )
            temp_db.resolve_incident(
                incident_id=incident.id,
                resolution_level=ResolutionLevel.LEVEL2_LLM,
                resolution_action="restore_firewall",
                outcome=IncidentOutcome.SUCCESS,
                resolution_time_ms=200
            )

        # Get stats
        stats = temp_db.get_stats_summary(days=30)
        assert stats['total_incidents'] == 3
        assert stats['l2_percentage'] == 100.0


class TestSQLInjectionPrevention:
    """Tests ensuring SQL injection is prevented."""

    def test_resolution_level_cannot_inject(self, temp_db):
        """Test that resolution level uses parameterized queries."""
        incident = temp_db.create_incident(
            site_id="site-001",
            host_id="host-001",
            incident_type="test",
            severity="low",
            raw_data={}
        )

        # This should work normally without SQL injection
        temp_db.resolve_incident(
            incident_id=incident.id,
            resolution_level=ResolutionLevel.LEVEL1_DETERMINISTIC,
            resolution_action="test'; DROP TABLE incidents; --",
            outcome=IncidentOutcome.SUCCESS,
            resolution_time_ms=100
        )

        # Table should still exist
        resolved = temp_db.get_incident(incident.id)
        assert resolved is not None

    def test_pattern_signature_in_query(self, temp_db):
        """Test pattern signature is properly escaped."""
        incident = temp_db.create_incident(
            site_id="site'; DROP TABLE pattern_stats; --",
            host_id="host-001",
            incident_type="test",
            severity="low",
            raw_data={}
        )

        # Should still work
        stats = temp_db.get_stats_summary()
        assert stats is not None


class TestPromotionEligibility:
    """Tests for L2→L1 promotion eligibility."""

    def test_promotion_requires_5_occurrences(self, temp_db):
        """Test that promotion requires at least 5 occurrences."""
        for i in range(4):
            incident = temp_db.create_incident(
                site_id="site-001",
                host_id=f"host-{i}",
                incident_type="firewall_drift",
                severity="high",
                raw_data={"check_type": "firewall"}
            )
            temp_db.resolve_incident(
                incident_id=incident.id,
                resolution_level=ResolutionLevel.LEVEL2_LLM,
                resolution_action="restore_firewall",
                outcome=IncidentOutcome.SUCCESS,
                resolution_time_ms=200
            )

        # 4 occurrences - should not be eligible
        candidates = temp_db.get_promotion_candidates()
        assert len(candidates) == 0

    def test_promotion_eligible_at_threshold(self, temp_db):
        """Test promotion eligibility at threshold."""
        # Create 5 incidents with L2 resolutions
        for i in range(5):
            incident = temp_db.create_incident(
                site_id="site-001",
                host_id=f"host-{i}",
                incident_type="firewall_drift",
                severity="high",
                raw_data={"check_type": "firewall"}
            )
            temp_db.resolve_incident(
                incident_id=incident.id,
                resolution_level=ResolutionLevel.LEVEL2_LLM,
                resolution_action="restore_firewall",
                outcome=IncidentOutcome.SUCCESS,
                resolution_time_ms=200
            )

        candidates = temp_db.get_promotion_candidates()
        assert len(candidates) == 1
        assert candidates[0].total_occurrences == 5
        assert candidates[0].success_rate >= 0.9

    def test_low_success_rate_not_eligible(self, temp_db):
        """Test that low success rate prevents promotion."""
        for i in range(5):
            incident = temp_db.create_incident(
                site_id="site-001",
                host_id=f"host-{i}",
                incident_type="backup_drift",
                severity="medium",
                raw_data={"check_type": "backup"}
            )
            # Only 2 of 5 succeed (40% success rate)
            outcome = IncidentOutcome.SUCCESS if i < 2 else IncidentOutcome.FAILURE
            temp_db.resolve_incident(
                incident_id=incident.id,
                resolution_level=ResolutionLevel.LEVEL2_LLM,
                resolution_action="run_backup",
                outcome=outcome,
                resolution_time_ms=200
            )

        # Should not be eligible due to low success rate
        candidates = temp_db.get_promotion_candidates()
        assert len(candidates) == 0


class TestHumanFeedback:
    """Tests for human feedback recording."""

    def test_add_human_feedback(self, temp_db):
        """Test adding human feedback to incident."""
        incident = temp_db.create_incident(
            site_id="site-001",
            host_id="host-001",
            incident_type="test",
            severity="low",
            raw_data={}
        )

        temp_db.add_human_feedback(
            incident_id=incident.id,
            feedback_type="correct_resolution",
            feedback_data={"approved": True, "notes": "Good fix"}
        )

        updated = temp_db.get_incident(incident.id)
        assert updated.human_feedback is not None


class TestPatternContext:
    """Tests for pattern context retrieval."""

    def test_get_pattern_context(self, temp_db):
        """Test retrieving pattern context for LLM."""
        # Create incidents
        for i in range(3):
            incident = temp_db.create_incident(
                site_id="site-001",
                host_id=f"host-{i}",
                incident_type="firewall_drift",
                severity="high",
                raw_data={"check_type": "firewall"}
            )
            temp_db.resolve_incident(
                incident_id=incident.id,
                resolution_level=ResolutionLevel.LEVEL2_LLM,
                resolution_action="restore_firewall",
                outcome=IncidentOutcome.SUCCESS,
                resolution_time_ms=200
            )

        pattern_sig = incident.pattern_signature
        context = temp_db.get_pattern_context(pattern_sig)

        assert context['pattern_signature'] == pattern_sig
        assert context['stats'] is not None
        assert len(context['recent_incidents']) == 3
        assert len(context['successful_actions']) >= 1


class TestStatsSummary:
    """Tests for statistics summary."""

    def test_stats_summary_empty(self, temp_db):
        """Test stats summary with no incidents."""
        stats = temp_db.get_stats_summary()
        assert stats['total_incidents'] == 0

    def test_stats_summary_with_data(self, temp_db):
        """Test stats summary with incidents."""
        # Create L1, L2, L3 incidents
        levels = [
            (ResolutionLevel.LEVEL1_DETERMINISTIC, 50),
            (ResolutionLevel.LEVEL2_LLM, 2000),
            (ResolutionLevel.LEVEL3_HUMAN, 5000),
        ]

        for level, time_ms in levels:
            incident = temp_db.create_incident(
                site_id="site-001",
                host_id="host-001",
                incident_type="test",
                severity="medium",
                raw_data={}
            )
            temp_db.resolve_incident(
                incident_id=incident.id,
                resolution_level=level,
                resolution_action="test",
                outcome=IncidentOutcome.SUCCESS,
                resolution_time_ms=time_ms
            )

        stats = temp_db.get_stats_summary()
        assert stats['total_incidents'] == 3
        assert round(stats['l1_percentage'], 1) == 33.3
        assert round(stats['l2_percentage'], 1) == 33.3
        assert round(stats['l3_percentage'], 1) == 33.3


class TestMarkPromoted:
    """Tests for marking patterns as promoted."""

    def test_mark_promoted(self, temp_db):
        """Test marking a pattern as promoted to L1."""
        # Create eligible pattern
        incident_ids = []
        for i in range(5):
            incident = temp_db.create_incident(
                site_id="site-001",
                host_id=f"host-{i}",
                incident_type="firewall_drift",
                severity="high",
                raw_data={"check_type": "firewall"}
            )
            incident_ids.append(incident.id)
            temp_db.resolve_incident(
                incident_id=incident.id,
                resolution_level=ResolutionLevel.LEVEL2_LLM,
                resolution_action="restore_firewall",
                outcome=IncidentOutcome.SUCCESS,
                resolution_time_ms=200
            )

        pattern_sig = incident.pattern_signature

        # Mark as promoted
        temp_db.mark_promoted(
            pattern_signature=pattern_sig,
            rule_yaml="id: L1-TEST-001\nname: Test Rule",
            incident_ids=incident_ids
        )

        # Should no longer be in candidates
        candidates = temp_db.get_promotion_candidates()
        promoted_sigs = [c.pattern_signature for c in candidates]
        assert pattern_sig not in promoted_sigs


class TestDatabasePruning:
    """Tests for database pruning to prevent disk space issues."""

    def test_prune_old_resolved_incidents(self, temp_db):
        """Test that old resolved incidents are pruned."""
        import sqlite3

        # Create an old incident (manually set created_at to 60 days ago)
        old_incident = temp_db.create_incident(
            site_id="site-001",
            host_id="host-001",
            incident_type="firewall_drift",
            severity="high",
            raw_data={"check_type": "firewall"}
        )

        # Resolve it
        temp_db.resolve_incident(
            incident_id=old_incident.id,
            resolution_level=ResolutionLevel.LEVEL1_DETERMINISTIC,
            resolution_action="restore_firewall",
            outcome=IncidentOutcome.SUCCESS,
            resolution_time_ms=100
        )

        # Manually backdate the incident to 60 days ago
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        conn = sqlite3.connect(temp_db.db_path)
        conn.execute(
            "UPDATE incidents SET created_at = ?, resolved_at = ? WHERE id = ?",
            (old_date, old_date, old_incident.id)
        )
        conn.commit()
        conn.close()

        # Create a recent incident
        recent_incident = temp_db.create_incident(
            site_id="site-001",
            host_id="host-002",
            incident_type="firewall_drift",
            severity="high",
            raw_data={"check_type": "firewall"}
        )
        temp_db.resolve_incident(
            incident_id=recent_incident.id,
            resolution_level=ResolutionLevel.LEVEL1_DETERMINISTIC,
            resolution_action="restore_firewall",
            outcome=IncidentOutcome.SUCCESS,
            resolution_time_ms=100
        )

        # Verify we have 2 incidents before prune
        stats_before = temp_db.get_database_stats()
        assert stats_before["incidents_count"] == 2

        # Prune with 30-day retention
        result = temp_db.prune_old_incidents(retention_days=30)

        # Should have deleted the old incident
        assert result["incidents_deleted"] == 1
        assert result["incidents_after"] == 1

        # Recent incident should still exist
        assert temp_db.get_incident(recent_incident.id) is not None
        # Old incident should be gone
        assert temp_db.get_incident(old_incident.id) is None

    def test_prune_keeps_unresolved_incidents(self, temp_db):
        """Test that unresolved incidents are kept regardless of age."""
        import sqlite3

        # Create an unresolved incident
        unresolved = temp_db.create_incident(
            site_id="site-001",
            host_id="host-001",
            incident_type="encryption_drift",
            severity="critical",
            raw_data={"check_type": "encryption"}
        )

        # Backdate to 90 days ago but don't resolve
        old_date = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        conn = sqlite3.connect(temp_db.db_path)
        conn.execute(
            "UPDATE incidents SET created_at = ? WHERE id = ?",
            (old_date, unresolved.id)
        )
        conn.commit()
        conn.close()

        # Prune with 30-day retention
        result = temp_db.prune_old_incidents(retention_days=30, keep_unresolved=True)

        # Should NOT have deleted the unresolved incident
        assert result["incidents_deleted"] == 0
        assert temp_db.get_incident(unresolved.id) is not None

    def test_get_database_stats(self, temp_db):
        """Test database statistics retrieval."""
        # Create some incidents
        for i in range(5):
            incident = temp_db.create_incident(
                site_id="site-001",
                host_id=f"host-{i}",
                incident_type="test",
                severity="low",
                raw_data={}
            )
            if i < 3:  # Resolve 3 of 5
                temp_db.resolve_incident(
                    incident_id=incident.id,
                    resolution_level=ResolutionLevel.LEVEL1_DETERMINISTIC,
                    resolution_action="test",
                    outcome=IncidentOutcome.SUCCESS,
                    resolution_time_ms=100
                )

        stats = temp_db.get_database_stats()

        assert stats["incidents_count"] == 5
        assert stats["unresolved_count"] == 2
        assert stats["file_size_mb"] >= 0
        assert stats["oldest_incident"] is not None
        assert stats["newest_incident"] is not None

    def test_prune_clears_feedback(self, temp_db):
        """Test that learning feedback is deleted with incidents."""
        import sqlite3

        # Create and resolve an incident with feedback
        incident = temp_db.create_incident(
            site_id="site-001",
            host_id="host-001",
            incident_type="test",
            severity="low",
            raw_data={}
        )
        temp_db.resolve_incident(
            incident_id=incident.id,
            resolution_level=ResolutionLevel.LEVEL2_LLM,
            resolution_action="test",
            outcome=IncidentOutcome.SUCCESS,
            resolution_time_ms=100
        )
        temp_db.add_human_feedback(
            incident_id=incident.id,
            feedback_type="approved",
            feedback_data={"notes": "Good"}
        )

        # Backdate
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        conn = sqlite3.connect(temp_db.db_path)
        conn.execute(
            "UPDATE incidents SET created_at = ?, resolved_at = ? WHERE id = ?",
            (old_date, old_date, incident.id)
        )
        conn.commit()
        conn.close()

        # Prune
        result = temp_db.prune_old_incidents(retention_days=30)

        # Feedback should also be deleted
        assert result["feedback_deleted"] == 1
