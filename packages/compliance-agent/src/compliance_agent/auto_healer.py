"""
Three-Tier Incident Resolution Orchestrator.

Implements tiered remediation with human escalation:
- Level 1: Deterministic Rules Engine (70-80% of incidents, operator-configured)
- Level 2: LLM Context-Aware Planner (15-20%, generates remediation plans)
- Level 3: Human Escalation (5-10%, requires operator decision)

With pattern learning loop for L2-to-L1 rule promotion.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Callable, TYPE_CHECKING
from dataclasses import dataclass, field
from pathlib import Path

from .incident_db import IncidentDatabase, Incident, ResolutionLevel, IncidentOutcome

if TYPE_CHECKING:
    from .learning_sync import LearningSyncService
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
    log_level: str = "INFO"

    # Circuit breaker for loop detection
    max_heal_attempts_per_incident: int = 5  # Max attempts before cooldown
    cooldown_period_minutes: int = 30  # Cooldown after max attempts


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
        action_executor: Optional[Callable] = None,
        learning_sync: Optional["LearningSyncService"] = None,
    ):
        self.config = config
        self.action_executor = action_executor
        self.learning_sync = learning_sync

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

        # Circuit breaker: track heal attempts to prevent runaway loops
        # Key: (site_id, host_id, incident_type) -> (attempt_count, first_attempt_time)
        self._heal_attempts: Dict[tuple, tuple] = {}
        self._cooldowns: Dict[tuple, datetime] = {}

        # Flap detector: track resolve→recur cycles to catch heal loops where
        # remediation "succeeds" but drift recurs immediately (e.g., GPO override,
        # false positive detection). Escalates to L3 after max_flap_count cycles.
        # Key: circuit_key -> (flap_count, first_flap_time)
        self._flap_tracker: Dict[tuple, tuple] = {}
        self._max_flap_count = 3  # resolve→recur cycles before escalating
        self._flap_window_minutes = 120  # time window to count flaps (must exceed drift_report_cooldown * max_flap_count)

        logger.info(
            f"AutoHealer initialized: L1={config.enable_level1}, "
            f"L2={config.enable_level2}, L3={config.enable_level3}, "
            f"Learning={config.enable_learning}, "
            f"CircuitBreaker=max {config.max_heal_attempts_per_incident} attempts/{config.cooldown_period_minutes}min cooldown, "
            f"FlapDetector=max {self._max_flap_count} recurrences/{self._flap_window_minutes}min"
        )

    def _is_flapping(self, circuit_key: tuple) -> bool:
        """Check if an incident type is flapping (resolve→recur loop)."""
        if circuit_key not in self._flap_tracker:
            return False
        count, first_time = self._flap_tracker[circuit_key]
        age_minutes = (datetime.now(timezone.utc) - first_time).total_seconds() / 60
        if age_minutes > self._flap_window_minutes:
            # Window expired, reset
            del self._flap_tracker[circuit_key]
            return False
        return count >= self._max_flap_count

    def _track_flap(self, circuit_key: tuple) -> None:
        """Track a resolve→recur cycle. Called when an incident recurs after healing."""
        now = datetime.now(timezone.utc)
        if circuit_key in self._flap_tracker:
            count, first_time = self._flap_tracker[circuit_key]
            age_minutes = (now - first_time).total_seconds() / 60
            if age_minutes > self._flap_window_minutes:
                self._flap_tracker[circuit_key] = (1, now)
            else:
                new_count = count + 1
                self._flap_tracker[circuit_key] = (new_count, first_time)
                if new_count >= self._max_flap_count:
                    logger.warning(
                        f"FLAP DETECTED: {circuit_key[2]} on {circuit_key[1]} "
                        f"resolved then recurred {new_count} times in {age_minutes:.0f} min. "
                        f"Escalating to L3 - likely false positive or external override."
                    )
        else:
            self._flap_tracker[circuit_key] = (1, now)

    def _is_in_cooldown(self, circuit_key: tuple) -> bool:
        """Check if a circuit is in cooldown due to too many failures."""
        if circuit_key not in self._cooldowns:
            return False
        cooldown_until = self._cooldowns[circuit_key]
        if datetime.now(timezone.utc) >= cooldown_until:
            # Cooldown expired, reset tracking
            del self._cooldowns[circuit_key]
            if circuit_key in self._heal_attempts:
                del self._heal_attempts[circuit_key]
            return False
        return True

    def _track_heal_attempt(self, circuit_key: tuple, attempt_time: datetime) -> None:
        """Track a heal attempt and trigger cooldown if threshold exceeded."""
        window_minutes = 10  # Count attempts within a 10-minute window

        if circuit_key in self._heal_attempts:
            count, first_time = self._heal_attempts[circuit_key]
            age_minutes = (attempt_time - first_time).total_seconds() / 60

            if age_minutes > window_minutes:
                # Reset counter if window expired
                self._heal_attempts[circuit_key] = (1, attempt_time)
            else:
                # Increment counter
                new_count = count + 1
                self._heal_attempts[circuit_key] = (new_count, first_time)

                if new_count >= self.config.max_heal_attempts_per_incident:
                    # Trigger cooldown
                    cooldown_until = attempt_time + timedelta(minutes=self.config.cooldown_period_minutes)
                    self._cooldowns[circuit_key] = cooldown_until
                    logger.error(
                        f"CIRCUIT BREAKER TRIGGERED: {circuit_key[2]} on {circuit_key[1]} "
                        f"had {new_count} heal attempts in {age_minutes:.1f} minutes. "
                        f"Entering {self.config.cooldown_period_minutes} minute cooldown."
                    )
        else:
            self._heal_attempts[circuit_key] = (1, attempt_time)

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
        start_time = datetime.now(timezone.utc)

        # Build granular flap key: include runbook_id when available
        # so different runbooks within the same check_type don't cross-trigger.
        # E.g., SSH-001..SSH-004 all share check_type="ssh_config" but each
        # gets its own flap counter: "ssh_config:LIN-SSH-001", etc.
        flap_type = incident_type
        if raw_data and raw_data.get("runbook_id"):
            flap_type = f"{incident_type}:{raw_data['runbook_id']}"
        circuit_key = (site_id, host_id, flap_type)

        # Persistent flap suppression: check SQLite before anything else.
        # Once a check flaps and gets escalated to L3, healing stays off
        # until a human explicitly clears it (survives agent restarts).
        if self.incident_db.is_flap_suppressed(site_id, host_id, flap_type):
            logger.info(
                f"FLAP SUPPRESSED: {flap_type} on {host_id} — "
                f"healing disabled until operator clears suppression"
            )
            return HealingResult(
                incident_id=f"SUPPRESSED-{uuid.uuid4().hex[:8]}",
                success=False,
                escalated=True,
                resolution_level=ResolutionLevel.LEVEL3_HUMAN,
                action_taken="flap_suppressed_awaiting_human",
                resolution_time_ms=0,
                error=f"Persistent flap suppression active for {flap_type} — awaiting operator clearance"
            )

        # Circuit breaker: check for runaway healing loops
        if self._is_in_cooldown(circuit_key):
            cooldown_until = self._cooldowns[circuit_key]
            remaining = (cooldown_until - start_time).total_seconds() / 60
            logger.warning(
                f"CIRCUIT BREAKER: {incident_type} on {host_id} is in cooldown. "
                f"Skipping heal for {remaining:.1f} more minutes. "
                f"Too many failed attempts detected - possible loop."
            )
            return HealingResult(
                incident_id=f"SKIPPED-{uuid.uuid4().hex[:8]}",
                success=False,
                resolution_level=ResolutionLevel.LEVEL3_HUMAN,
                action_taken="circuit_breaker_cooldown",
                resolution_time_ms=0,
                error=f"Circuit breaker active: {remaining:.1f} min cooldown remaining"
            )

        # Flap detector: check for resolve→recur loops.
        # Only check existing state here — _track_flap() is called AFTER
        # successful healing so we only count real resolve→recur cycles,
        # not repeated detection of unhealed drift.
        if self._is_flapping(circuit_key):
            reason = (
                f"{flap_type} resolved then recurred {self._max_flap_count}+ times "
                f"within {self._flap_window_minutes} min — likely external override (e.g., GPO)"
            )
            logger.warning(
                f"FLAP DETECTOR: {flap_type} on {host_id} keeps recurring after healing. "
                f"Escalating to L3 and recording persistent suppression."
            )
            # Persist suppression so it survives agent restarts and window expiry
            self.incident_db.record_flap_suppression(
                site_id=site_id,
                host_id=host_id,
                incident_type=flap_type,
                reason=reason,
            )
            return HealingResult(
                incident_id=f"FLAP-{uuid.uuid4().hex[:8]}",
                success=False,
                escalated=True,
                resolution_level=ResolutionLevel.LEVEL3_HUMAN,
                action_taken="flap_detected_escalation",
                resolution_time_ms=int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000),
                error=f"Flap detected: {flap_type} resolved then recurred {self._max_flap_count}+ times. Healing suppressed until operator clears."
            )

        # Track this heal attempt
        self._track_heal_attempt(circuit_key, start_time)

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
                # Only track flap on successful healing (real resolve→recur cycle)
                if result.success:
                    self._track_flap(circuit_key)
                return result

        # Try Level 2 - LLM Planner
        if self.level2:
            result = await self._try_level2(incident, site_id, host_id)
            if result and not result.escalated:
                # Only track flap on successful healing
                if result.success:
                    self._track_flap(circuit_key)
                return result

        # Level 3 - Escalation
        if self.level3:
            result = await self._escalate(incident, site_id, host_id)
            return result

        # Fallback: no levels enabled
        end_time = datetime.now(timezone.utc)
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
        start_time = datetime.now(timezone.utc)

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

        # Merge raw_data fields into action_params so handlers get
        # context like runbook_id, distro, host from the drift result
        if raw_data and match.action_params is not None:
            for key in ("runbook_id", "distro", "host"):
                if key not in match.action_params and key in raw_data:
                    match.action_params[key] = raw_data[key]

        # Capture state before healing for telemetry
        state_before = self._capture_system_state(incident, host_id)

        # Execute the action
        result = await self.level1.execute(match, site_id, host_id)

        # Capture state after healing
        state_after = self._capture_system_state(incident, host_id)

        end_time = datetime.now(timezone.utc)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Resolution already recorded inside level1.execute() — don't double-count
        success = result.get("success", False)

        healing_result = HealingResult(
            incident_id=incident.id,
            success=success,
            resolution_level=ResolutionLevel.LEVEL1_DETERMINISTIC,
            action_taken=match.action,
            resolution_time_ms=duration_ms,
            output=str(result.get("output")),
            error=result.get("error")
        )

        # Report execution telemetry to learning sync
        await self._report_execution_telemetry(
            incident=incident,
            result=healing_result,
            state_before=state_before,
            state_after=state_after,
            action=match.action,
            runbook_id=match.rule.id,
        )

        return healing_result

    async def _try_level2(
        self,
        incident: Incident,
        site_id: str,
        host_id: str
    ) -> Optional[HealingResult]:
        """Try to resolve incident with Level 2 LLM planner."""
        start_time = datetime.now(timezone.utc)

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
                resolution_time_ms=int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000),
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
                resolution_time_ms=int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000),
                escalated=True,
                error="Requires human approval"
            )

        # Capture state before healing for telemetry
        state_before = self._capture_system_state(incident, host_id)

        # Execute the action
        result = await self.level2.execute(decision, site_id, host_id)

        # Capture state after healing
        state_after = self._capture_system_state(incident, host_id)

        end_time = datetime.now(timezone.utc)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Resolution already recorded inside level2.execute() — don't double-count
        success = result.get("success", False)

        healing_result = HealingResult(
            incident_id=incident.id,
            success=success,
            resolution_level=ResolutionLevel.LEVEL2_LLM,
            action_taken=decision.recommended_action,
            resolution_time_ms=duration_ms,
            output=str(result.get("output")),
            error=result.get("error")
        )

        # Report execution telemetry to learning sync
        await self._report_execution_telemetry(
            incident=incident,
            result=healing_result,
            state_before=state_before,
            state_after=state_after,
            action=decision.recommended_action,
            runbook_id=f"L2-{decision.recommended_action}",
        )

        return healing_result

    async def _escalate(
        self,
        incident: Incident,
        site_id: str,
        host_id: str
    ) -> HealingResult:
        """Escalate incident to Level 3 human handling."""
        start_time = datetime.now(timezone.utc)

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

        end_time = datetime.now(timezone.utc)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Escalation already recorded inside level3.escalate() — don't double-count

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

    def _capture_system_state(
        self,
        incident: Incident,
        host_id: str
    ) -> Dict[str, Any]:
        """
        Capture system state relevant to the incident for telemetry.

        This captures a snapshot of the system state that can be used
        for learning engine analysis (comparing before/after healing).
        """
        state = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "host_id": host_id,
            "incident_type": incident.incident_type,
        }

        # Add incident-specific state based on type
        try:
            if "service" in incident.incident_type.lower():
                # Service-related: capture service status
                state["services"] = self._get_relevant_services(incident.raw_data)
            elif "firewall" in incident.incident_type.lower():
                # Firewall: capture firewall rules state
                state["firewall_enabled"] = self._check_firewall_state()
            elif "bitlocker" in incident.incident_type.lower() or "encryption" in incident.incident_type.lower():
                # Encryption: capture drive encryption status
                state["encryption_status"] = self._check_encryption_state()
            elif "antivirus" in incident.incident_type.lower() or "av_" in incident.incident_type.lower():
                # AV/EDR: capture protection status
                state["av_enabled"] = self._check_av_state()
            elif "audit" in incident.incident_type.lower():
                # Audit logging: capture audit config
                state["audit_configured"] = self._check_audit_state()

            # Always include raw data summary
            if incident.raw_data:
                state["raw_data_keys"] = list(incident.raw_data.keys())
                # Include small values directly
                for key, value in incident.raw_data.items():
                    if isinstance(value, (bool, int, float, str)) and len(str(value)) < 100:
                        state[f"raw_{key}"] = value

        except Exception as e:
            logger.warning(f"Error capturing system state: {e}")
            state["capture_error"] = str(e)

        return state

    def _get_relevant_services(self, raw_data: Dict[str, Any]) -> Dict[str, str]:
        """Get status of services mentioned in incident data."""
        services = {}
        # Extract service names from raw data
        for key in ["service_name", "services", "check_type"]:
            if key in raw_data:
                val = raw_data[key]
                if isinstance(val, str):
                    services[val] = "unknown"
                elif isinstance(val, list):
                    for s in val:
                        services[str(s)] = "unknown"
        return services

    def _check_firewall_state(self) -> bool:
        """Check if Windows Firewall is enabled."""
        # This is a placeholder - actual implementation would query the system
        return True

    def _check_encryption_state(self) -> Dict[str, Any]:
        """Check drive encryption status."""
        return {"system_drive": "unknown"}

    def _check_av_state(self) -> bool:
        """Check if AV/EDR is enabled."""
        return True

    def _check_audit_state(self) -> bool:
        """Check if audit logging is configured."""
        return True

    # Check types that indicate Windows targets
    _WINDOWS_CHECK_TYPES = frozenset({
        "windows_defender", "workstation", "bitlocker",
        "defender", "patches", "screen_lock",
    })

    def _detect_platform(self, incident: Incident) -> str:
        """Detect platform from incident type (check_type)."""
        if incident.incident_type in self._WINDOWS_CHECK_TYPES:
            return "windows"
        return "linux"

    def _compute_state_diff(
        self,
        before: Dict[str, Any],
        after: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Compute difference between before and after states.

        Used by learning engine to understand what changed during healing.
        """
        diff = {
            "changed_keys": [],
            "added_keys": [],
            "removed_keys": [],
            "changes": {}
        }

        before_keys = set(before.keys())
        after_keys = set(after.keys())

        # Find added keys
        diff["added_keys"] = list(after_keys - before_keys)

        # Find removed keys
        diff["removed_keys"] = list(before_keys - after_keys)

        # Find changed values
        common_keys = before_keys & after_keys
        for key in common_keys:
            if before[key] != after[key]:
                diff["changed_keys"].append(key)
                diff["changes"][key] = {
                    "before": before[key],
                    "after": after[key]
                }

        return diff

    async def _report_execution_telemetry(
        self,
        incident: Incident,
        result: "HealingResult",
        state_before: Dict[str, Any],
        state_after: Dict[str, Any],
        action: str,
        runbook_id: str = None,
    ):
        """
        Report execution telemetry to learning sync service.

        Sends rich telemetry data for learning engine analysis.
        """
        if not self.learning_sync:
            return

        try:
            execution_result = {
                "execution_id": str(uuid.uuid4()),
                "incident_id": incident.id,
                "runbook_id": runbook_id or action,
                "hostname": incident.host_id,
                "platform": self._detect_platform(incident),
                "incident_type": incident.incident_type,
                "started_at": state_before.get("captured_at"),
                "completed_at": state_after.get("captured_at"),
                "duration_seconds": result.resolution_time_ms / 1000.0,
                "success": result.success,
                "status": "success" if result.success else "failure",
                "verification_passed": result.success and not result.error,
                "confidence": 1.0 if result.resolution_level == ResolutionLevel.LEVEL1_DETERMINISTIC else 0.8,
                "resolution_level": result.resolution_level.value if hasattr(result.resolution_level, 'value') else str(result.resolution_level),
                "state_before": state_before,
                "state_after": state_after,
                "state_diff": self._compute_state_diff(state_before, state_after),
                "executed_steps": [{"action": action, "success": result.success}],
                "error_message": result.error,
            }

            success = await self.learning_sync.report_execution(execution_result)
            if success:
                logger.debug(f"Reported execution telemetry for {incident.id}")
            else:
                logger.debug(f"Queued execution telemetry for {incident.id}")

        except Exception as e:
            logger.warning(f"Failed to report execution telemetry: {e}")

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
        max_heal_attempts_per_incident=config_dict.get("max_heal_attempts", 5),
        cooldown_period_minutes=config_dict.get("cooldown_minutes", 30)
    )

    return AutoHealer(config=config, action_executor=action_executor)
