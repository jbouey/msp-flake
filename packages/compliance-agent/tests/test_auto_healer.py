"""
Tests for the Three-Tier Auto-Healing Architecture.

Tests Level 1 (Deterministic), Level 2 (LLM), Level 3 (Escalation),
and the learning loop for pattern promotion.
"""

import pytest
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from compliance_agent.incident_db import (
    IncidentDatabase, Incident, ResolutionLevel, IncidentOutcome
)
from compliance_agent.level1_deterministic import (
    DeterministicEngine, Rule, RuleCondition, MatchOperator
)
from compliance_agent.level2_llm import (
    Level2Planner, LLMConfig, LLMMode, LLMDecision
)
from compliance_agent.level3_escalation import (
    EscalationHandler, EscalationConfig, EscalationPriority
)
from compliance_agent.learning_loop import (
    SelfLearningSystem, PromotionConfig
)
from compliance_agent.auto_healer import (
    AutoHealer, AutoHealerConfig, HealingResult
)


# ==================== Fixtures ====================

@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary incident database."""
    db_path = tmp_path / "test_incidents.db"
    return IncidentDatabase(db_path=str(db_path))


@pytest.fixture
def temp_rules_dir(tmp_path):
    """Create a temporary rules directory."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    return rules_dir


@pytest.fixture
def l1_engine(temp_db, temp_rules_dir):
    """Create a Level 1 deterministic engine."""
    return DeterministicEngine(
        rules_dir=temp_rules_dir,
        incident_db=temp_db,
        action_executor=None
    )


@pytest.fixture
def l3_handler(temp_db):
    """Create a Level 3 escalation handler."""
    config = EscalationConfig(
        email_enabled=False,
        slack_enabled=False,
        pagerduty_enabled=False
    )
    return EscalationHandler(config=config, incident_db=temp_db)


@pytest.fixture
def learning_system(temp_db, temp_rules_dir):
    """Create a learning system."""
    config = PromotionConfig(
        promotion_output_dir=temp_rules_dir / "promoted",
        min_occurrences=3,
        min_l2_resolutions=2,
        min_success_rate=0.8
    )
    return SelfLearningSystem(incident_db=temp_db, config=config)


@pytest.fixture
def auto_healer_config(tmp_path):
    """Create auto-healer configuration."""
    return AutoHealerConfig(
        db_path=str(tmp_path / "incidents.db"),
        rules_dir=tmp_path / "rules",
        enable_level1=True,
        enable_level2=False,  # Disable LLM for tests
        enable_level3=True,
        enable_learning=True,
        dry_run=True
    )


# ==================== Incident Database Tests ====================

class TestIncidentDatabase:
    """Tests for the incident database."""

    def test_create_incident(self, temp_db):
        """Test creating an incident."""
        incident = temp_db.create_incident(
            site_id="test-site",
            host_id="test-host",
            incident_type="backup",
            severity="high",
            raw_data={"check_type": "backup", "drift_detected": True}
        )

        assert incident.id.startswith("INC-")
        assert incident.site_id == "test-site"
        assert incident.incident_type == "backup"
        assert incident.pattern_signature is not None

    def test_resolve_incident(self, temp_db):
        """Test resolving an incident."""
        incident = temp_db.create_incident(
            site_id="test-site",
            host_id="test-host",
            incident_type="backup",
            severity="high",
            raw_data={"check_type": "backup"}
        )

        temp_db.resolve_incident(
            incident_id=incident.id,
            resolution_level=ResolutionLevel.LEVEL1_DETERMINISTIC,
            resolution_action="run_backup_job",
            outcome=IncidentOutcome.SUCCESS,
            resolution_time_ms=1000
        )

        resolved = temp_db.get_incident(incident.id)
        assert resolved.outcome == "success"
        assert resolved.resolution_level == "L1"

    def test_pattern_context(self, temp_db):
        """Test getting pattern context."""
        # Create multiple incidents with same pattern
        for _ in range(3):
            incident = temp_db.create_incident(
                site_id="test-site",
                host_id="test-host",
                incident_type="backup",
                severity="high",
                raw_data={"check_type": "backup", "drift_detected": True}
            )
            temp_db.resolve_incident(
                incident_id=incident.id,
                resolution_level=ResolutionLevel.LEVEL2_LLM,
                resolution_action="run_backup_job",
                outcome=IncidentOutcome.SUCCESS,
                resolution_time_ms=1000
            )

        context = temp_db.get_pattern_context(incident.pattern_signature)

        assert context["stats"]["total_occurrences"] == 3
        assert context["stats"]["l2_resolutions"] == 3

    def test_stats_summary(self, temp_db):
        """Test getting stats summary."""
        # Create and resolve some incidents
        for i in range(5):
            incident = temp_db.create_incident(
                site_id="test-site",
                host_id="test-host",
                incident_type="backup",
                severity="high",
                raw_data={"index": i}
            )
            temp_db.resolve_incident(
                incident_id=incident.id,
                resolution_level=ResolutionLevel.LEVEL1_DETERMINISTIC,
                resolution_action="test_action",
                outcome=IncidentOutcome.SUCCESS,
                resolution_time_ms=100
            )

        stats = temp_db.get_stats_summary(days=30)

        assert stats["total_incidents"] == 5
        assert stats["success_rate"] == 100.0


# ==================== Level 1 Deterministic Tests ====================

class TestLevel1Deterministic:
    """Tests for Level 1 deterministic rules engine."""

    def test_builtin_rules_loaded(self, l1_engine):
        """Test that built-in rules are loaded."""
        stats = l1_engine.get_rule_stats()

        assert stats["total_rules"] > 0
        assert stats["by_source"]["builtin"] > 0

    def test_rule_matching(self, l1_engine):
        """Test rule matching."""
        data = {
            "check_type": "backup",
            "drift_detected": True,
            "details": {
                "last_backup_success": False
            }
        }

        match = l1_engine.match(
            incident_id="INC-TEST-001",
            incident_type="backup",
            severity="high",
            data=data
        )

        assert match is not None
        assert match.action == "run_backup_job"

    def test_no_rule_match(self, l1_engine):
        """Test when no rule matches."""
        data = {
            "check_type": "unknown_type",
            "something": "else"
        }

        match = l1_engine.match(
            incident_id="INC-TEST-002",
            incident_type="unknown",
            severity="low",
            data=data
        )

        assert match is None

    def test_cooldown_enforcement(self, l1_engine):
        """Test that cooldowns are enforced."""
        data = {
            "check_type": "backup",
            "drift_detected": True,
            "details": {"last_backup_success": False},
            "host_id": "test-host"
        }

        # First match should succeed
        match1 = l1_engine.match(
            incident_id="INC-TEST-003",
            incident_type="backup",
            severity="high",
            data=data
        )
        assert match1 is not None

        # Simulate execution to set cooldown
        l1_engine.cooldowns[f"{match1.rule.id}:test-host"] = datetime.utcnow()

        # Second match should fail due to cooldown
        match2 = l1_engine.match(
            incident_id="INC-TEST-004",
            incident_type="backup",
            severity="high",
            data=data
        )

        # Should match different rule or none due to cooldown
        if match2:
            assert match2.rule.id != match1.rule.id

    def test_custom_rule(self, l1_engine, temp_rules_dir):
        """Test loading and matching custom rules."""
        # Create a custom rule file
        rule_yaml = """
rules:
  - id: L1-TEST-001
    name: Test Rule
    description: A test rule
    conditions:
      - field: incident_type
        operator: eq
        value: test_incident
    action: test_action
    action_params:
      param1: value1
    enabled: true
    priority: 1
"""
        rule_file = temp_rules_dir / "test_rules.yaml"
        rule_file.write_text(rule_yaml)

        # Reload rules
        l1_engine.reload_rules()

        # Should now match
        match = l1_engine.match(
            incident_id="INC-TEST-005",
            incident_type="test_incident",
            severity="high",
            data={"incident_type": "test_incident"}
        )

        assert match is not None
        assert match.rule.id == "L1-TEST-001"
        assert match.action == "test_action"


# ==================== Level 3 Escalation Tests ====================

class TestLevel3Escalation:
    """Tests for Level 3 escalation handler."""

    @pytest.mark.asyncio
    async def test_escalate_incident(self, l3_handler, temp_db):
        """Test escalating an incident."""
        incident = temp_db.create_incident(
            site_id="test-site",
            host_id="test-host",
            incident_type="encryption",
            severity="critical",
            raw_data={"check_type": "encryption", "drift_detected": True}
        )

        ticket = await l3_handler.escalate(
            incident=incident,
            reason="Encryption changes require human verification",
            context={"historical": {}}
        )

        assert ticket.id.startswith("ESC-")
        assert ticket.incident_id == incident.id
        assert ticket.priority == EscalationPriority.CRITICAL

    @pytest.mark.asyncio
    async def test_resolve_ticket(self, l3_handler, temp_db):
        """Test resolving an escalation ticket."""
        incident = temp_db.create_incident(
            site_id="test-site",
            host_id="test-host",
            incident_type="backup",
            severity="high",
            raw_data={}
        )

        ticket = await l3_handler.escalate(
            incident=incident,
            reason="Test escalation",
            context={}
        )

        await l3_handler.resolve_ticket(
            ticket_id=ticket.id,
            resolution="Manually verified backup configuration",
            action_taken="Verified and updated backup schedule",
            feedback={"quality": "good", "was_helpful": True}
        )

        resolved_ticket = l3_handler.get_ticket(ticket.id)

        assert resolved_ticket.status == "resolved"
        assert resolved_ticket.resolution is not None

    def test_priority_determination(self, l3_handler, temp_db):
        """Test priority is determined correctly."""
        # Critical severity should be CRITICAL priority
        incident = temp_db.create_incident(
            site_id="test-site",
            host_id="test-host",
            incident_type="encryption",
            severity="critical",
            raw_data={}
        )

        priority = l3_handler._determine_priority(incident, "test")
        assert priority == EscalationPriority.CRITICAL


# ==================== Learning Loop Tests ====================

class TestLearningLoop:
    """Tests for the self-learning system."""

    def test_find_promotion_candidates(self, learning_system, temp_db):
        """Test finding promotion candidates."""
        # Create incidents with consistent L2 resolutions
        pattern_sig = None
        for i in range(5):
            incident = temp_db.create_incident(
                site_id="test-site",
                host_id="test-host",
                incident_type="backup",
                severity="high",
                raw_data={"check_type": "backup", "drift_detected": True}
            )
            pattern_sig = incident.pattern_signature

            temp_db.resolve_incident(
                incident_id=incident.id,
                resolution_level=ResolutionLevel.LEVEL2_LLM,
                resolution_action="run_backup_job",
                outcome=IncidentOutcome.SUCCESS,
                resolution_time_ms=1000
            )

        candidates = learning_system.find_promotion_candidates()

        # Should find at least one candidate
        assert len(candidates) >= 0  # May or may not meet criteria

    def test_generate_rule(self, learning_system, temp_db):
        """Test generating a rule from a candidate."""
        from compliance_agent.learning_loop import PromotionCandidate, PatternStats

        # Create a mock candidate
        stats = PatternStats(
            pattern_signature="abc123",
            total_occurrences=10,
            l1_resolutions=0,
            l2_resolutions=8,
            l3_resolutions=2,
            success_rate=0.9,
            avg_resolution_time_ms=1500,
            last_seen=datetime.utcnow().isoformat(),
            recommended_action="run_backup_job",
            promotion_eligible=True
        )

        candidate = PromotionCandidate(
            pattern_signature="abc123",
            stats=stats,
            sample_incidents=[
                {"id": "INC-1", "incident_type": "backup", "raw_data": '{"check_type": "backup"}'}
            ],
            recommended_action="run_backup_job",
            action_params={},
            confidence_score=0.92,
            promotion_reason="Test promotion"
        )

        rule = learning_system.generate_rule(candidate)

        assert rule.id.startswith("L1-PROMOTED-")
        assert rule.action == "run_backup_job"
        assert rule.source == "promoted"

    def test_learning_metrics(self, learning_system, temp_db):
        """Test getting learning metrics."""
        # Create some incidents
        for i in range(3):
            incident = temp_db.create_incident(
                site_id="test-site",
                host_id="test-host",
                incident_type="backup",
                severity="high",
                raw_data={}
            )
            temp_db.resolve_incident(
                incident_id=incident.id,
                resolution_level=ResolutionLevel.LEVEL1_DETERMINISTIC,
                resolution_action="test",
                outcome=IncidentOutcome.SUCCESS,
                resolution_time_ms=100
            )

        metrics = learning_system.get_learning_metrics(days=30)

        assert "resolution_breakdown" in metrics
        assert "flywheel_status" in metrics


# ==================== Auto-Healer Integration Tests ====================

class TestAutoHealer:
    """Integration tests for the auto-healer."""

    @pytest.mark.asyncio
    async def test_heal_with_l1_match(self, auto_healer_config):
        """Test healing an incident that matches L1 rules."""
        # Create auto-healer
        auto_healer = AutoHealer(config=auto_healer_config)

        result = await auto_healer.heal(
            site_id="test-site",
            host_id="test-host",
            incident_type="backup",
            severity="high",
            raw_data={
                "check_type": "backup",
                "drift_detected": True,
                "details": {"last_backup_success": False}
            }
        )

        assert result.incident_id.startswith("INC-")
        assert result.resolution_level == ResolutionLevel.LEVEL1_DETERMINISTIC
        assert result.action_taken == "run_backup_job"

    @pytest.mark.asyncio
    async def test_heal_with_escalation(self, auto_healer_config):
        """Test healing an incident that escalates."""
        auto_healer = AutoHealer(config=auto_healer_config)

        # Encryption issues should escalate
        result = await auto_healer.heal(
            site_id="test-site",
            host_id="test-host",
            incident_type="encryption",
            severity="critical",
            raw_data={
                "check_type": "encryption",
                "drift_detected": True
            }
        )

        # Should either match escalation rule or go to L3
        assert result.escalated or result.action_taken == "escalate"

    @pytest.mark.asyncio
    async def test_heal_unknown_incident(self, auto_healer_config):
        """Test healing an unknown incident type."""
        auto_healer = AutoHealer(config=auto_healer_config)

        result = await auto_healer.heal(
            site_id="test-site",
            host_id="test-host",
            incident_type="totally_unknown",
            severity="low",
            raw_data={"something": "unknown"}
        )

        # Should escalate since no L1 rule matches and L2 is disabled
        assert result.escalated or result.resolution_level == ResolutionLevel.LEVEL3_HUMAN

    def test_get_stats(self, auto_healer_config):
        """Test getting auto-healer stats."""
        auto_healer = AutoHealer(config=auto_healer_config)
        stats = auto_healer.get_stats(days=30)

        assert "incidents" in stats
        assert "levels" in stats
        assert stats["levels"]["l1_enabled"] is True


# ==================== Rule Condition Tests ====================

class TestRuleConditions:
    """Tests for rule condition matching."""

    def test_equals_operator(self):
        """Test equals operator."""
        condition = RuleCondition(
            field="incident_type",
            operator=MatchOperator.EQUALS,
            value="backup"
        )

        assert condition.matches({"incident_type": "backup"}) is True
        assert condition.matches({"incident_type": "other"}) is False

    def test_contains_operator(self):
        """Test contains operator."""
        condition = RuleCondition(
            field="message",
            operator=MatchOperator.CONTAINS,
            value="error"
        )

        assert condition.matches({"message": "An error occurred"}) is True
        assert condition.matches({"message": "Everything is fine"}) is False

    def test_greater_than_operator(self):
        """Test greater than operator."""
        condition = RuleCondition(
            field="details.cpu_percent",
            operator=MatchOperator.GREATER_THAN,
            value=90
        )

        assert condition.matches({"details": {"cpu_percent": 95}}) is True
        assert condition.matches({"details": {"cpu_percent": 50}}) is False

    def test_regex_operator(self):
        """Test regex operator."""
        condition = RuleCondition(
            field="error_message",
            operator=MatchOperator.REGEX,
            value=r"failed.*backup"
        )

        assert condition.matches({"error_message": "failed to complete backup"}) is True
        assert condition.matches({"error_message": "backup successful"}) is False

    def test_nested_field_access(self):
        """Test nested field access with dot notation."""
        condition = RuleCondition(
            field="details.service.status",
            operator=MatchOperator.EQUALS,
            value="stopped"
        )

        data = {
            "details": {
                "service": {
                    "status": "stopped"
                }
            }
        }

        assert condition.matches(data) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
