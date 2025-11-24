"""
Auto-Healer: Three-Tier Incident Resolution Orchestrator.

Implements the LLM-Era Auto-Healing architecture:
- Level 1: Deterministic Rules Engine (70-80% of incidents)
- Level 2: LLM Context-Aware Planner (15-20%)
- Level 3: Human Escalation (5-10%)

With self-learning loop for continuous improvement.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass, field
from pathlib import Path

from .incident_db import IncidentDatabase, Incident, ResolutionLevel
from .level1_deterministic import DeterministicEngine, RuleMatch
from .level2_llm import Level2Planner, LLMConfig, LLMMode, LLMDecision
from .level3_escalation import EscalationHandler, EscalationConfig
from .learning_loop import SelfLearningSystem, PromotionConfig
from .models import DriftResult


logger = logging.getLogger(__name__)


@dataclass
class AutoHealerConfig:
    """Configuration for the auto-healer."""
    # Database
    db_path: str = "/var/lib/msp-compliance-agent/incidents.db"

    # Level 1 config
    rules_dir: Path = Path("/etc/msp/rules")
    enable_level1: bool = True

    # Level 2 config
    enable_level2: bool = True
    llm_mode: LLMMode = LLMMode.HYBRID
    local_model: str = "llama3.1:8b"
    local_endpoint: str = "http://localhost:11434"
    api_provider: str = "openai"
    api_model: str = "gpt-4o-mini"
    api_key: Optional[str] = None

    # Level 3 config
    enable_level3: bool = True
    slack_webhook: Optional[str] = None
    pagerduty_key: Optional[str] = None
    email_recipients: list = field(default_factory=list)

    # Learning loop
    enable_learning: bool = True
    auto_promote_rules: bool = False

    # General
    dry_run: bool = False
    log_level: str = "INFO"


@dataclass
class HealingResult:
    """Result of auto-healing attempt."""
    incident_id: str
    success: bool
    resolution_level: ResolutionLevel
    action_taken: Optional[str]
    resolution_time_ms: int
    output: Optional[str] = None
    error: Optional[str] = None
    escalated: bool = False
    ticket_id: Optional[str] = None


class AutoHealer:
    """
    Three-Tier Auto-Healing Orchestrator.

    Processes incidents through:
    1. Level 1: Fast deterministic rules
    2. Level 2: LLM-based planning (if L1 doesn't match)
    3. Level 3: Human escalation (if L2 can't resolve)

    With continuous learning to promote L2 patterns to L1.
    """

    def __init__(
        self,
        config: AutoHealerConfig,
        action_executor: Optional[Callable] = None
    ):
        self.config = config
        self.action_executor = action_executor

        # Initialize incident database
        self.incident_db = IncidentDatabase(db_path=config.db_path)

        # Initialize Level 1 - Deterministic Engine
        self.level1: Optional[DeterministicEngine] = None
        if config.enable_level1:
            self.level1 = DeterministicEngine(
                rules_dir=config.rules_dir,
                incident_db=self.incident_db,
                action_executor=action_executor
            )

        # Initialize Level 2 - LLM Planner
        self.level2: Optional[Level2Planner] = None
        if config.enable_level2:
            llm_config = LLMConfig(
                mode=config.llm_mode,
                local_model=config.local_model,
                local_endpoint=config.local_endpoint,
                api_provider=config.api_provider,
                api_model=config.api_model,
                api_key=config.api_key
            )
            self.level2 = Level2Planner(
                config=llm_config,
                incident_db=self.incident_db,
                action_executor=action_executor
            )

        # Initialize Level 3 - Escalation Handler
        self.level3: Optional[EscalationHandler] = None
        if config.enable_level3:
            esc_config = EscalationConfig(
                slack_enabled=bool(config.slack_webhook),
                slack_webhook_url=config.slack_webhook,
                pagerduty_enabled=bool(config.pagerduty_key),
                pagerduty_routing_key=config.pagerduty_key,
                email_enabled=bool(config.email_recipients),
                email_recipients=config.email_recipients
            )
            self.level3 = EscalationHandler(
                config=esc_config,
                incident_db=self.incident_db
            )

        # Initialize Learning Loop
        self.learning: Optional[SelfLearningSystem] = None
        if config.enable_learning:
            promo_config = PromotionConfig(
                auto_promote=config.auto_promote_rules,
                promotion_output_dir=config.rules_dir / "promoted"
            )
            self.learning = SelfLearningSystem(
                incident_db=self.incident_db,
                config=promo_config
            )

        logger.info(
            f"AutoHealer initialized: L1={config.enable_level1}, "
            f"L2={config.enable_level2}, L3={config.enable_level3}, "
            f"Learning={config.enable_learning}"
        )

    async def heal(
        self,
        site_id: str,
        host_id: str,
        incident_type: str,
        severity: str,
        raw_data: Dict[str, Any]
    ) -> HealingResult:
        """
        Main entry point: Process an incident through the three-tier system.

        Returns the result of the healing attempt.
        """
        start_time = datetime.utcnow()

        # Create incident record
        incident = self.incident_db.create_incident(
            site_id=site_id,
            host_id=host_id,
            incident_type=incident_type,
            severity=severity,
            raw_data=raw_data
        )

        logger.info(f"Processing incident {incident.id} ({incident_type}/{severity})")

        # Try Level 1 - Deterministic Rules
        if self.level1:
            result = await self._try_level1(incident, site_id, host_id, raw_data)
            if result:
                return result

        # Try Level 2 - LLM Planner
        if self.level2:
            result = await self._try_level2(incident, site_id, host_id)
            if result and not result.escalated:
                return result

        # Level 3 - Escalation
        if self.level3:
            result = await self._escalate(incident, site_id, host_id)
            return result

        # Fallback: no levels enabled
        end_time = datetime.utcnow()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        return HealingResult(
            incident_id=incident.id,
            success=False,
            resolution_level=ResolutionLevel.UNRESOLVED,
            action_taken=None,
            resolution_time_ms=duration_ms,
            error="No healing levels enabled"
        )

    async def _try_level1(
        self,
        incident: Incident,
        site_id: str,
        host_id: str,
        raw_data: Dict[str, Any]
    ) -> Optional[HealingResult]:
        """Try to resolve incident with Level 1 deterministic rules."""
        start_time = datetime.utcnow()

        # Match against rules
        match = self.level1.match(
            incident_id=incident.id,
            incident_type=incident.incident_type,
            severity=incident.severity,
            data=raw_data
        )

        if not match:
            logger.debug(f"No L1 rule matched for incident {incident.id}")
            return None

        logger.info(f"L1 rule matched: {match.rule.id} -> {match.action}")

        # Check for escalation action
        if match.action == "escalate":
            logger.info(f"L1 rule {match.rule.id} triggers escalation")
            return None  # Let L3 handle it

        # Execute the action
        if self.config.dry_run:
            logger.info(f"[DRY RUN] Would execute: {match.action}")
            result = {"success": True, "output": "DRY_RUN"}
        else:
            result = await self.level1.execute(match, site_id, host_id)

        end_time = datetime.utcnow()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        return HealingResult(
            incident_id=incident.id,
            success=result.get("success", False),
            resolution_level=ResolutionLevel.LEVEL1_DETERMINISTIC,
            action_taken=match.action,
            resolution_time_ms=duration_ms,
            output=str(result.get("output")),
            error=result.get("error")
        )

    async def _try_level2(
        self,
        incident: Incident,
        site_id: str,
        host_id: str
    ) -> Optional[HealingResult]:
        """Try to resolve incident with Level 2 LLM planner."""
        start_time = datetime.utcnow()

        # Check if LLM is available
        if not await self.level2.is_available():
            logger.warning("L2 LLM not available, escalating")
            return HealingResult(
                incident_id=incident.id,
                success=False,
                resolution_level=ResolutionLevel.LEVEL2_LLM,
                action_taken=None,
                resolution_time_ms=0,
                escalated=True,
                error="LLM not available"
            )

        # Get LLM decision
        decision = await self.level2.plan(incident)

        logger.info(
            f"L2 decision: {decision.recommended_action} "
            f"(confidence: {decision.confidence:.2f})"
        )

        # Check for escalation
        if decision.escalate_to_l3:
            return HealingResult(
                incident_id=incident.id,
                success=False,
                resolution_level=ResolutionLevel.LEVEL2_LLM,
                action_taken=None,
                resolution_time_ms=int((datetime.utcnow() - start_time).total_seconds() * 1000),
                escalated=True,
                error=decision.reasoning
            )

        # Check for approval requirement
        if decision.requires_approval:
            logger.info(f"L2 action requires approval, escalating")
            return HealingResult(
                incident_id=incident.id,
                success=False,
                resolution_level=ResolutionLevel.LEVEL2_LLM,
                action_taken=decision.recommended_action,
                resolution_time_ms=int((datetime.utcnow() - start_time).total_seconds() * 1000),
                escalated=True,
                error="Requires human approval"
            )

        # Execute the action
        if self.config.dry_run:
            logger.info(f"[DRY RUN] Would execute: {decision.recommended_action}")
            result = {"success": True, "output": "DRY_RUN"}
        else:
            result = await self.level2.execute(decision, site_id, host_id)

        end_time = datetime.utcnow()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        return HealingResult(
            incident_id=incident.id,
            success=result.get("success", False),
            resolution_level=ResolutionLevel.LEVEL2_LLM,
            action_taken=decision.recommended_action,
            resolution_time_ms=duration_ms,
            output=str(result.get("output")),
            error=result.get("error")
        )

    async def _escalate(
        self,
        incident: Incident,
        site_id: str,
        host_id: str
    ) -> HealingResult:
        """Escalate incident to Level 3 human handling."""
        start_time = datetime.utcnow()

        # Build context for the ticket
        context = {}
        if self.level2:
            context = self.level2.build_context(incident)

        # Create escalation ticket
        ticket = await self.level3.escalate(
            incident=incident,
            reason="Could not resolve automatically",
            context=context
        )

        end_time = datetime.utcnow()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        return HealingResult(
            incident_id=incident.id,
            success=False,  # Not resolved yet
            resolution_level=ResolutionLevel.LEVEL3_HUMAN,
            action_taken="escalated",
            resolution_time_ms=duration_ms,
            escalated=True,
            ticket_id=ticket.id
        )

    async def heal_drift(self, drift: DriftResult, site_id: str, host_id: str) -> HealingResult:
        """
        Convenience method to heal a drift detection result.

        Converts DriftResult to the format expected by heal().
        """
        raw_data = {
            "check_type": drift.check_type,
            "drift_detected": drift.drifted,
            "details": drift.details,
            "baseline_expected": drift.baseline_expected,
            "current_value": drift.current_value,
            "hipaa_control": drift.hipaa_control
        }

        severity = "high" if drift.drifted else "info"

        return await self.heal(
            site_id=site_id,
            host_id=host_id,
            incident_type=drift.check_type,
            severity=severity,
            raw_data=raw_data
        )

    def get_stats(self, days: int = 30) -> Dict[str, Any]:
        """Get auto-healer statistics."""
        db_stats = self.incident_db.get_stats_summary(days=days)

        stats = {
            "period_days": days,
            "incidents": db_stats,
            "levels": {
                "l1_enabled": self.config.enable_level1,
                "l2_enabled": self.config.enable_level2,
                "l3_enabled": self.config.enable_level3
            }
        }

        if self.level1:
            stats["l1_rules"] = self.level1.get_rule_stats()

        if self.learning:
            stats["learning"] = self.learning.get_learning_metrics(days=days)

        if self.level3:
            stats["open_tickets"] = len(self.level3.get_open_tickets())

        return stats

    def get_promotion_candidates(self) -> list:
        """Get patterns eligible for L1 promotion."""
        if not self.learning:
            return []

        return self.learning.find_promotion_candidates()

    async def promote_pattern(
        self,
        pattern_signature: str,
        approved_by: str
    ) -> bool:
        """Manually promote a pattern to L1."""
        if not self.learning:
            logger.error("Learning loop not enabled")
            return False

        candidates = self.learning.find_promotion_candidates()

        for candidate in candidates:
            if candidate.pattern_signature == pattern_signature:
                rule = self.learning.promote_pattern(candidate, approved_by)

                # Reload L1 rules to include the new promoted rule
                if self.level1:
                    self.level1.reload_rules()

                logger.info(f"Promoted pattern {pattern_signature} to rule {rule.id}")
                return True

        logger.warning(f"Pattern {pattern_signature} not found in promotion candidates")
        return False

    def reload_rules(self):
        """Reload Level 1 rules from disk."""
        if self.level1:
            self.level1.reload_rules()
            logger.info("L1 rules reloaded")


# Convenience function to create auto-healer from config dict
def create_auto_healer(
    config_dict: Dict[str, Any],
    action_executor: Optional[Callable] = None
) -> AutoHealer:
    """Create AutoHealer from configuration dictionary."""
    config = AutoHealerConfig(
        db_path=config_dict.get("db_path", "/var/lib/msp-compliance-agent/incidents.db"),
        rules_dir=Path(config_dict.get("rules_dir", "/etc/msp/rules")),
        enable_level1=config_dict.get("enable_level1", True),
        enable_level2=config_dict.get("enable_level2", True),
        llm_mode=LLMMode(config_dict.get("llm_mode", "hybrid")),
        local_model=config_dict.get("local_model", "llama3.1:8b"),
        local_endpoint=config_dict.get("local_endpoint", "http://localhost:11434"),
        api_provider=config_dict.get("api_provider", "openai"),
        api_model=config_dict.get("api_model", "gpt-4o-mini"),
        api_key=config_dict.get("api_key"),
        enable_level3=config_dict.get("enable_level3", True),
        slack_webhook=config_dict.get("slack_webhook"),
        pagerduty_key=config_dict.get("pagerduty_key"),
        email_recipients=config_dict.get("email_recipients", []),
        enable_learning=config_dict.get("enable_learning", True),
        auto_promote_rules=config_dict.get("auto_promote_rules", False),
        dry_run=config_dict.get("dry_run", False)
    )

    return AutoHealer(config=config, action_executor=action_executor)
