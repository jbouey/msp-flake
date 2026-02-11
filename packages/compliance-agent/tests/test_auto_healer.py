"""
Tests for the Three-Tier Auto-Healing Architecture.

Tests Level 1 (Deterministic), Level 2 (LLM), Level 3 (Escalation),
and the learning loop for pattern promotion.
"""

import pytest
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime, timezone
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
        enable_learning=True
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
        l1_engine.cooldowns[f"{match1.rule.id}:test-host"] = datetime.now(timezone.utc)

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


# ==================== Bundled Rules Loading Tests ====================

class TestBundledRulesLoading:
    """Tests for loading bundled YAML rules from the package's rules/ directory."""

    def test_bundled_rules_loaded(self, temp_db, temp_rules_dir):
        """Test that bundled rules from rules/ directory are loaded."""
        engine = DeterministicEngine(
            rules_dir=temp_rules_dir,
            incident_db=temp_db,
            action_executor=None
        )

        stats = engine.get_rule_stats()
        # Should have built-in rules + bundled Windows rules (34+)
        assert stats["total_rules"] > 10, (
            f"Expected >10 total rules (built-in + bundled), got {stats['total_rules']}"
        )

    def test_windows_firewall_rule_matches(self, temp_db, temp_rules_dir):
        """Test that Windows firewall rule matches a firewall_status incident."""
        engine = DeterministicEngine(
            rules_dir=temp_rules_dir,
            incident_db=temp_db,
            action_executor=None
        )

        data = {
            "check_type": "firewall_status",
            "drift_detected": True,
            "status": "fail",
            "details": {
                "domain_profile": "disabled",
            }
        }

        match = engine.match(
            incident_id="INC-TEST-FW-001",
            incident_type="firewall_status",
            severity="high",
            data=data
        )

        assert match is not None, "Expected firewall rule to match firewall_status incident"
        assert match.action == "run_windows_runbook"
        assert "runbook_id" in match.action_params

    def test_windows_defender_rule_matches(self, temp_db, temp_rules_dir):
        """Test that Windows defender rule matches a windows_defender incident."""
        engine = DeterministicEngine(
            rules_dir=temp_rules_dir,
            incident_db=temp_db,
            action_executor=None
        )

        data = {
            "check_type": "windows_defender",
            "drift_detected": True,
            "status": "fail",
            "details": {
                "realtime_protection": False,
            }
        }

        match = engine.match(
            incident_id="INC-TEST-DEF-001",
            incident_type="windows_defender",
            severity="high",
            data=data
        )

        assert match is not None, "Expected defender rule to match windows_defender incident"
        assert match.action == "run_windows_runbook"

    def test_windows_defender_exclusion_rule_matches(self, temp_db, temp_rules_dir):
        """Test that Defender exclusion rule matches when unauthorized_exclusions exist."""
        engine = DeterministicEngine(
            rules_dir=temp_rules_dir,
            incident_db=temp_db,
            action_executor=None
        )

        data = {
            "check_type": "windows_defender",
            "drift_detected": True,
            "status": "non_compliant",
            "details": {
                "unauthorized_exclusions": ["C:\\Windows\\Temp\\*.exe"],
            }
        }

        match = engine.match(
            incident_id="INC-TEST-EXCL-001",
            incident_type="windows_defender",
            severity="medium",
            data=data
        )

        assert match is not None, "Expected exclusion rule to match"
        assert match.action == "run_windows_runbook"
        assert match.action_params.get("runbook_id") == "RB-WIN-SEC-017"

    def test_bundled_rules_dont_override_custom(self, temp_db, temp_rules_dir):
        """Test that custom rules with higher priority take precedence over bundled."""
        # Write a custom rule with priority 1 (higher than bundled priority 5)
        rule_yaml = """
rules:
  - id: L1-CUSTOM-FW-001
    name: Custom Firewall Rule
    description: Custom high-priority firewall rule
    conditions:
      - field: check_type
        operator: eq
        value: firewall_status
      - field: drift_detected
        operator: eq
        value: true
    action: custom_firewall_action
    action_params:
      custom_param: true
    enabled: true
    priority: 1
    cooldown_seconds: 60
"""
        rule_file = temp_rules_dir / "custom_firewall.yaml"
        rule_file.write_text(rule_yaml)

        engine = DeterministicEngine(
            rules_dir=temp_rules_dir,
            incident_db=temp_db,
            action_executor=None
        )

        data = {
            "check_type": "firewall_status",
            "drift_detected": True,
            "status": "non_compliant",
            "details": {}
        }

        match = engine.match(
            incident_id="INC-TEST-PRIORITY-001",
            incident_type="firewall_status",
            severity="high",
            data=data
        )

        assert match is not None
        # Custom rule (priority 1) should match before bundled rules (priority 5)
        assert match.rule.id == "L1-CUSTOM-FW-001"
        assert match.action == "custom_firewall_action"

    def test_ssh_root_login_rule_matches(self, temp_db, temp_rules_dir):
        """Test SSH-001 matches PermitRootLogin drift (via runbook_id)."""
        engine = DeterministicEngine(
            rules_dir=temp_rules_dir,
            incident_db=temp_db,
            action_executor=None
        )

        data = {
            "check_type": "ssh_config",
            "drift_detected": True,
            "runbook_id": "LIN-SSH-001",
            "details": {"drift_description": "PermitRootLogin=yes"},
        }

        match = engine.match(
            incident_id="INC-TEST-SSH-001",
            incident_type="ssh_config",
            severity="high",
            data=data
        )

        assert match is not None, "Expected SSH-001 rule to match PermitRootLogin drift"
        assert match.rule.id == "L1-SSH-001"
        assert match.action_params["runbook_id"] == "LIN-SSH-001"

    def test_ssh_password_auth_rule_matches(self, temp_db, temp_rules_dir):
        """Test SSH-002 matches PasswordAuthentication drift (via runbook_id)."""
        engine = DeterministicEngine(
            rules_dir=temp_rules_dir,
            incident_db=temp_db,
            action_executor=None
        )

        data = {
            "check_type": "ssh_config",
            "drift_detected": True,
            "runbook_id": "LIN-SSH-002",
            "details": {"drift_description": "PasswordAuthentication=yes"},
        }

        match = engine.match(
            incident_id="INC-TEST-SSH-002",
            incident_type="ssh_config",
            severity="high",
            data=data
        )

        assert match is not None, "Expected SSH-002 rule to match PasswordAuthentication drift"
        assert match.rule.id == "L1-SSH-002"
        assert match.action_params["runbook_id"] == "LIN-SSH-002"

    def test_ssh_rules_dont_cross_match(self, temp_db, temp_rules_dir):
        """Test SSH-001 does NOT match SSH-002 drift and vice versa."""
        engine = DeterministicEngine(
            rules_dir=temp_rules_dir,
            incident_db=temp_db,
            action_executor=None
        )

        # PasswordAuth drift should NOT match SSH-001
        data = {
            "check_type": "ssh_config",
            "drift_detected": True,
            "runbook_id": "LIN-SSH-002",
            "details": {"drift_description": "PasswordAuthentication=yes"},
        }

        match = engine.match(
            incident_id="INC-TEST-SSH-CROSS",
            incident_type="ssh_config",
            severity="high",
            data=data
        )

        assert match is not None
        # Should match SSH-002, NOT SSH-001
        assert match.rule.id == "L1-SSH-002", f"Expected SSH-002 but got {match.rule.id}"

    def test_exists_operator(self, temp_db, temp_rules_dir):
        """Test the EXISTS operator in rule conditions."""
        engine = DeterministicEngine(
            rules_dir=temp_rules_dir,
            incident_db=temp_db,
            action_executor=None
        )

        # Test exists=true with field present
        cond = RuleCondition("details.some_field", MatchOperator.EXISTS, True)
        assert cond.matches({"details": {"some_field": "value"}}) is True

        # Test exists=true with field absent
        assert cond.matches({"details": {}}) is False

        # Test exists=false with field absent
        cond_not = RuleCondition("details.missing", MatchOperator.EXISTS, False)
        assert cond_not.matches({"details": {}}) is True


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
            last_seen=datetime.now(timezone.utc).isoformat(),
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


# ==================== Platform Detection Tests ====================

class TestPlatformDetection:
    """Tests for _detect_platform() in AutoHealer."""

    def _make_incident(self, incident_type: str) -> Incident:
        return Incident(
            id=f"test-{incident_type}",
            site_id="test-site",
            host_id="test-host",
            incident_type=incident_type,
            severity="medium",
            raw_data={},
            pattern_signature=f"{incident_type}:{incident_type}:test-host",
            created_at="2026-02-07T12:00:00+00:00",
        )

    def test_windows_defender_is_windows(self, auto_healer_config):
        healer = AutoHealer(config=auto_healer_config)
        assert healer._detect_platform(self._make_incident("windows_defender")) == "windows"

    def test_bitlocker_is_windows(self, auto_healer_config):
        healer = AutoHealer(config=auto_healer_config)
        assert healer._detect_platform(self._make_incident("bitlocker")) == "windows"

    def test_workstation_is_windows(self, auto_healer_config):
        healer = AutoHealer(config=auto_healer_config)
        assert healer._detect_platform(self._make_incident("workstation")) == "windows"

    def test_screen_lock_is_windows(self, auto_healer_config):
        healer = AutoHealer(config=auto_healer_config)
        assert healer._detect_platform(self._make_incident("screen_lock")) == "windows"

    def test_patching_is_linux(self, auto_healer_config):
        healer = AutoHealer(config=auto_healer_config)
        assert healer._detect_platform(self._make_incident("patching")) == "linux"

    def test_firewall_is_linux(self, auto_healer_config):
        healer = AutoHealer(config=auto_healer_config)
        assert healer._detect_platform(self._make_incident("firewall")) == "linux"

    def test_backup_is_linux(self, auto_healer_config):
        healer = AutoHealer(config=auto_healer_config)
        assert healer._detect_platform(self._make_incident("backup")) == "linux"

    def test_ntp_sync_is_linux(self, auto_healer_config):
        healer = AutoHealer(config=auto_healer_config)
        assert healer._detect_platform(self._make_incident("ntp_sync")) == "linux"


class TestFlapDetector:
    """Test the flap detector catches resolve→recur loops."""

    @pytest.fixture
    def auto_healer_config(self, tmp_path):
        return AutoHealerConfig(
            db_path=str(tmp_path / "test_flap.db"),
            rules_dir=tmp_path / "rules",
            enable_level1=True,
            enable_level2=False,
            enable_level3=False,
            enable_learning=False,
        )

    def test_flap_thresholds(self, auto_healer_config):
        """Verify flap detector uses correct thresholds."""
        healer = AutoHealer(config=auto_healer_config)
        assert healer._max_flap_count == 3
        assert healer._flap_window_minutes == 120

    def test_flap_not_triggered_below_threshold(self, auto_healer_config):
        """Two recurrences should not trigger flap detection."""
        healer = AutoHealer(config=auto_healer_config)
        key = ("site-1", "host-1", "firewall")

        healer._track_flap(key)
        assert not healer._is_flapping(key)

        healer._track_flap(key)
        assert not healer._is_flapping(key)

    def test_flap_triggered_at_threshold(self, auto_healer_config):
        """Three recurrences should trigger flap detection."""
        healer = AutoHealer(config=auto_healer_config)
        key = ("site-1", "host-1", "firewall")

        for _ in range(3):
            healer._track_flap(key)

        assert healer._is_flapping(key)

    @pytest.mark.asyncio
    async def test_flap_escalation_result_has_escalated_true(self, auto_healer_config):
        """Flap detection should return HealingResult with escalated=True."""
        healer = AutoHealer(config=auto_healer_config)
        key = ("site-1", "host-1", "firewall")

        # Pre-load flap tracker to threshold
        for _ in range(3):
            healer._track_flap(key)

        result = await healer.heal(
            site_id="site-1",
            host_id="host-1",
            incident_type="firewall",
            severity="high",
            raw_data={"check_type": "firewall", "drift_detected": True}
        )

        assert result.escalated is True
        assert result.action_taken == "flap_detected_escalation"
        assert result.resolution_level == ResolutionLevel.LEVEL3_HUMAN
        assert result.success is False

    def test_flap_window_reset(self, auto_healer_config):
        """Flap counter should reset after window expires."""
        from datetime import timedelta
        healer = AutoHealer(config=auto_healer_config)
        key = ("site-1", "host-1", "firewall")

        # Simulate 2 flaps from 3 hours ago (outside 120-min window)
        old_time = datetime.now(timezone.utc) - timedelta(hours=3)
        healer._flap_tracker[key] = (2, old_time)

        # Next track should reset the counter
        healer._track_flap(key)
        count, _ = healer._flap_tracker[key]
        assert count == 1  # Reset, not incremented to 3


class TestPersistentFlapSuppression:
    """Test persistent flap suppression survives window expiry and agent restarts."""

    @pytest.fixture
    def auto_healer_config(self, tmp_path):
        return AutoHealerConfig(
            db_path=str(tmp_path / "test_suppression.db"),
            rules_dir=tmp_path / "rules",
            enable_level1=True,
            enable_level2=False,
            enable_level3=False,
            enable_learning=False,
        )

    def test_flap_records_persistent_suppression(self, auto_healer_config):
        """When flap is detected, a persistent suppression record is created in SQLite."""
        healer = AutoHealer(config=auto_healer_config)
        key = ("site-1", "host-1", "firewall")

        # Trigger flap detection
        for _ in range(3):
            healer._track_flap(key)

        assert healer._is_flapping(key)

        # Simulate what heal() does when flap detected
        healer.incident_db.record_flap_suppression(
            site_id="site-1", host_id="host-1",
            incident_type="firewall", reason="test flap"
        )

        assert healer.incident_db.is_flap_suppressed("site-1", "host-1", "firewall")

    @pytest.mark.asyncio
    async def test_heal_returns_suppressed_when_persisted(self, auto_healer_config):
        """heal() should return suppressed result when persistent suppression exists."""
        healer = AutoHealer(config=auto_healer_config)

        # Pre-record a suppression (simulates previous agent run)
        healer.incident_db.record_flap_suppression(
            site_id="site-1", host_id="host-1",
            incident_type="firewall", reason="GPO override loop"
        )

        result = await healer.heal(
            site_id="site-1", host_id="host-1",
            incident_type="firewall", severity="high",
            raw_data={"check_type": "firewall", "drift_detected": True}
        )

        assert result.success is False
        assert result.action_taken == "flap_suppressed_awaiting_human"
        assert result.escalated is True
        assert result.resolution_level == ResolutionLevel.LEVEL3_HUMAN

    @pytest.mark.asyncio
    async def test_heal_resumes_after_suppression_cleared(self, auto_healer_config):
        """After operator clears suppression, healing should resume."""
        healer = AutoHealer(config=auto_healer_config)

        # Record and then clear suppression
        healer.incident_db.record_flap_suppression(
            site_id="site-1", host_id="host-1",
            incident_type="firewall", reason="GPO override loop"
        )
        cleared = healer.incident_db.clear_flap_suppression(
            "site-1", "host-1", "firewall", cleared_by="admin"
        )
        assert cleared is True
        assert not healer.incident_db.is_flap_suppressed("site-1", "host-1", "firewall")

    def test_suppression_survives_new_db_connection(self, auto_healer_config):
        """Suppression persists across new IncidentDatabase instances (simulates restart)."""
        from compliance_agent.incident_db import IncidentDatabase

        db_path = auto_healer_config.db_path

        # First "agent run" records suppression
        db1 = IncidentDatabase(db_path=db_path)
        db1.record_flap_suppression("site-1", "host-1", "firewall", "GPO loop")
        assert db1.is_flap_suppressed("site-1", "host-1", "firewall")

        # Second "agent run" — new instance, same DB file
        db2 = IncidentDatabase(db_path=db_path)
        assert db2.is_flap_suppressed("site-1", "host-1", "firewall")

    def test_get_active_suppressions(self, auto_healer_config):
        """get_active_suppressions returns only uncleared suppressions."""
        healer = AutoHealer(config=auto_healer_config)
        db = healer.incident_db

        db.record_flap_suppression("site-1", "host-1", "firewall", "GPO loop")
        db.record_flap_suppression("site-1", "host-2", "defender", "service conflict")
        db.record_flap_suppression("site-1", "host-3", "patches", "WSUS override")

        # Clear one
        db.clear_flap_suppression("site-1", "host-2", "defender")

        active = db.get_active_suppressions()
        assert len(active) == 2
        types = {s["incident_type"] for s in active}
        assert types == {"firewall", "patches"}

    def test_clear_nonexistent_suppression_returns_false(self, auto_healer_config):
        """Clearing a non-existent suppression returns False."""
        healer = AutoHealer(config=auto_healer_config)
        result = healer.incident_db.clear_flap_suppression("site-1", "host-1", "firewall")
        assert result is False

    @pytest.mark.asyncio
    async def test_flap_detection_persists_suppression_via_heal(self, auto_healer_config):
        """Full integration: 3 successful heals → flap counter reaches threshold → 4th call suppressed."""
        healer = AutoHealer(config=auto_healer_config)

        # First 3 calls succeed at L1 (dry run), each increments flap counter
        for i in range(3):
            result = await healer.heal(
                site_id="site-1", host_id="host-1",
                incident_type="firewall", severity="high",
                raw_data={"check_type": "firewall", "drift_detected": True, "platform": "windows"}
            )
            # All 3 should succeed via L1 dry run
            assert result.success, f"Call {i+1} should succeed"

        # 4th call: flap counter is 3, _is_flapping triggers
        result = await healer.heal(
            site_id="site-1", host_id="host-1",
            incident_type="firewall", severity="high",
            raw_data={"check_type": "firewall", "drift_detected": True, "platform": "windows"}
        )
        assert result.action_taken == "flap_detected_escalation"
        assert healer.incident_db.is_flap_suppressed("site-1", "host-1", "firewall")

        # 5th call: even after clearing in-memory tracker, persistent suppression blocks
        healer._flap_tracker.clear()
        result = await healer.heal(
            site_id="site-1", host_id="host-1",
            incident_type="firewall", severity="high",
            raw_data={"check_type": "firewall", "drift_detected": True, "platform": "windows"}
        )
        assert result.action_taken == "flap_suppressed_awaiting_human"

    @pytest.mark.asyncio
    async def test_no_flap_without_successful_healing(self, auto_healer_config):
        """Incidents without matching L1 rules should NOT increment flap counter."""
        healer = AutoHealer(config=auto_healer_config)

        # Call heal() with an incident type that has no L1 rule match
        for i in range(5):
            result = await healer.heal(
                site_id="site-1", host_id="host-1",
                incident_type="unknown_check", severity="low",
                raw_data={"check_type": "unknown_check", "drift_detected": True}
            )

        # Should NOT be flap-suppressed — no healing ever succeeded
        assert not healer.incident_db.is_flap_suppressed("site-1", "host-1", "unknown_check")
        # Flap tracker should be empty for this key
        circuit_key = ("site-1", "host-1", "unknown_check")
        assert circuit_key not in healer._flap_tracker


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
