"""
Self-Learning System for Pattern Promotion.

Implements the "data flywheel" concept:
1. Track L2 LLM decisions and their outcomes
2. Identify patterns with consistent successful resolutions
3. Automatically promote patterns to L1 deterministic rules
4. Continuously improve resolution speed and reduce costs
"""

import json
import yaml
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from .incident_db import IncidentDatabase, PatternStats
from .level1_deterministic import Rule, RuleCondition, MatchOperator


logger = logging.getLogger(__name__)


@dataclass
class PromotionCandidate:
    """A pattern eligible for L1 promotion."""
    pattern_signature: str
    stats: PatternStats
    sample_incidents: List[Dict[str, Any]]
    recommended_action: str
    action_params: Dict[str, Any]
    confidence_score: float
    promotion_reason: str


@dataclass
class PromotionConfig:
    """Configuration for the learning loop."""
    # Promotion thresholds
    min_occurrences: int = 5
    min_l2_resolutions: int = 3
    min_success_rate: float = 0.9
    max_avg_resolution_time_ms: int = 30000  # 30 seconds

    # Promotion schedule
    check_interval_hours: int = 24
    auto_promote: bool = False  # Require human approval by default
    promotion_output_dir: Path = Path("/etc/msp/rules/promoted")

    # Learning metrics
    track_promotion_effectiveness: bool = True
    rollback_on_failure_rate: float = 0.2  # Rollback if >20% failure after promotion


class SelfLearningSystem:
    """
    Self-Learning System for automatic rule promotion.

    Analyzes L2 LLM decisions to find patterns that can be
    promoted to L1 deterministic rules, reducing latency and cost.
    """

    def __init__(
        self,
        incident_db: IncidentDatabase,
        config: PromotionConfig = None
    ):
        self.incident_db = incident_db
        self.config = config or PromotionConfig()
        self.promoted_patterns: Dict[str, datetime] = {}

    def find_promotion_candidates(self) -> List[PromotionCandidate]:
        """
        Find patterns eligible for L1 promotion.

        Returns patterns that meet all criteria:
        - Sufficient occurrences
        - High success rate
        - Consistent resolution action
        """
        candidates = []

        # Get patterns marked as promotion-eligible by the incident DB
        eligible_patterns = self.incident_db.get_promotion_candidates()

        for stats in eligible_patterns:
            # Skip if already promoted
            if stats.pattern_signature in self.promoted_patterns:
                continue

            # Verify it meets our criteria
            if not self._meets_promotion_criteria(stats):
                continue

            # Get sample incidents for this pattern
            context = self.incident_db.get_pattern_context(
                stats.pattern_signature,
                limit=10
            )

            sample_incidents = context.get("recent_incidents", [])
            successful_actions = context.get("successful_actions", [])

            if not successful_actions:
                continue

            # Get the most common successful action
            top_action = successful_actions[0]
            action_name = top_action.get("resolution_action", "unknown")

            # Calculate confidence score
            confidence = self._calculate_confidence(stats, successful_actions)

            candidate = PromotionCandidate(
                pattern_signature=stats.pattern_signature,
                stats=stats,
                sample_incidents=sample_incidents,
                recommended_action=action_name,
                action_params=self._extract_action_params(sample_incidents, action_name),
                confidence_score=confidence,
                promotion_reason=self._generate_promotion_reason(stats, confidence)
            )

            candidates.append(candidate)

        # Sort by confidence score (highest first)
        candidates.sort(key=lambda c: c.confidence_score, reverse=True)

        return candidates

    def _meets_promotion_criteria(self, stats: PatternStats) -> bool:
        """Check if pattern meets all promotion criteria."""
        # Minimum occurrences
        if stats.total_occurrences < self.config.min_occurrences:
            return False

        # Minimum L2 resolutions (must have been handled by L2)
        if stats.l2_resolutions < self.config.min_l2_resolutions:
            return False

        # Minimum success rate
        if stats.success_rate < self.config.min_success_rate:
            return False

        # Maximum resolution time (patterns that take too long may not be suitable)
        if stats.avg_resolution_time_ms > self.config.max_avg_resolution_time_ms:
            return False

        return True

    def _calculate_confidence(
        self,
        stats: PatternStats,
        successful_actions: List[Dict[str, Any]]
    ) -> float:
        """
        Calculate confidence score for promotion.

        Considers:
        - Success rate
        - Number of occurrences
        - Consistency of resolution action
        - Recency of incidents
        """
        # Base confidence from success rate
        base_confidence = stats.success_rate

        # Bonus for more occurrences (up to 10% bonus)
        occurrence_bonus = min(stats.total_occurrences / 50, 0.1)

        # Bonus for action consistency
        if successful_actions:
            top_action_count = successful_actions[0].get("count", 0)
            total_successes = sum(a.get("count", 0) for a in successful_actions)
            consistency = top_action_count / max(total_successes, 1)
            consistency_bonus = consistency * 0.1
        else:
            consistency_bonus = 0

        # Penalty for old patterns (may be stale)
        try:
            last_seen = datetime.fromisoformat(stats.last_seen)
            days_since = (datetime.utcnow() - last_seen).days
            recency_penalty = min(days_since / 30, 0.2) * -1
        except (ValueError, TypeError):
            recency_penalty = 0

        confidence = base_confidence + occurrence_bonus + consistency_bonus + recency_penalty

        return min(max(confidence, 0.0), 1.0)

    def _extract_action_params(
        self,
        incidents: List[Dict[str, Any]],
        action_name: str
    ) -> Dict[str, Any]:
        """Extract common action parameters from successful incidents."""
        # In a full implementation, this would analyze the incident data
        # to extract parameters that were commonly used
        return {}

    def _generate_promotion_reason(
        self,
        stats: PatternStats,
        confidence: float
    ) -> str:
        """Generate human-readable promotion reason."""
        return (
            f"Pattern seen {stats.total_occurrences} times with "
            f"{stats.success_rate:.1%} success rate. "
            f"{stats.l2_resolutions} L2 resolutions with consistent action. "
            f"Confidence: {confidence:.2f}"
        )

    def generate_rule(self, candidate: PromotionCandidate) -> Rule:
        """
        Generate a Level 1 rule from a promotion candidate.

        Creates a deterministic rule that can handle this pattern
        without LLM involvement.
        """
        # Analyze sample incidents to build conditions
        conditions = self._build_conditions(candidate.sample_incidents)

        rule_id = f"L1-PROMOTED-{candidate.pattern_signature[:8].upper()}"

        rule = Rule(
            id=rule_id,
            name=f"Promoted: {candidate.recommended_action}",
            description=f"Auto-promoted from L2. {candidate.promotion_reason}",
            conditions=conditions,
            action=candidate.recommended_action,
            action_params=candidate.action_params,
            hipaa_controls=self._extract_hipaa_controls(candidate.sample_incidents),
            severity_filter=None,  # Match all severities
            enabled=True,
            priority=50,  # Medium priority (below built-in, above custom)
            cooldown_seconds=300,
            max_retries=1,
            source="promoted"
        )

        return rule

    def _build_conditions(
        self,
        sample_incidents: List[Dict[str, Any]]
    ) -> List[RuleCondition]:
        """Build rule conditions from sample incidents."""
        conditions = []

        if not sample_incidents:
            return conditions

        # Find common fields across all incidents
        first_incident = sample_incidents[0]
        raw_data_value = first_incident.get("raw_data", "{}")
        # Handle both dict (from test) and string (from database) formats
        if isinstance(raw_data_value, dict):
            raw_data = raw_data_value
        else:
            raw_data = json.loads(raw_data_value)

        # Always include incident type
        incident_type = first_incident.get("incident_type")
        if incident_type:
            conditions.append(RuleCondition(
                field="incident_type",
                operator=MatchOperator.EQUALS,
                value=incident_type
            ))

        # Check for common patterns in raw_data
        if "check_type" in raw_data:
            conditions.append(RuleCondition(
                field="check_type",
                operator=MatchOperator.EQUALS,
                value=raw_data["check_type"]
            ))

        if raw_data.get("drift_detected"):
            conditions.append(RuleCondition(
                field="drift_detected",
                operator=MatchOperator.EQUALS,
                value=True
            ))

        return conditions

    def _extract_hipaa_controls(
        self,
        sample_incidents: List[Dict[str, Any]]
    ) -> List[str]:
        """Extract HIPAA controls from incidents."""
        # Map incident types to HIPAA controls
        control_map = {
            "patching": ["164.308(a)(5)(ii)(B)"],
            "av_edr": ["164.308(a)(5)(ii)(B)"],
            "backup": ["164.308(a)(7)(ii)(A)"],
            "logging": ["164.312(b)"],
            "firewall": ["164.312(e)(1)"],
            "encryption": ["164.312(a)(2)(iv)"],
        }

        if sample_incidents:
            incident_type = sample_incidents[0].get("incident_type", "")
            return control_map.get(incident_type, [])

        return []

    def promote_pattern(
        self,
        candidate: PromotionCandidate,
        approved_by: Optional[str] = None
    ) -> Rule:
        """
        Promote a pattern to Level 1.

        Creates the rule, saves it to disk, and records the promotion.
        """
        # Generate rule
        rule = self.generate_rule(candidate)

        # Save rule to disk
        self.config.promotion_output_dir.mkdir(parents=True, exist_ok=True)
        rule_file = self.config.promotion_output_dir / f"{rule.id}.yaml"

        rule_yaml = rule.to_yaml()
        rule_yaml["_promotion_metadata"] = {
            "promoted_at": datetime.utcnow().isoformat(),
            "promoted_by": approved_by or "auto",
            "confidence_score": candidate.confidence_score,
            "promotion_reason": candidate.promotion_reason,
            "sample_incident_count": len(candidate.sample_incidents),
            "stats": {
                "total_occurrences": candidate.stats.total_occurrences,
                "success_rate": candidate.stats.success_rate,
                "l2_resolutions": candidate.stats.l2_resolutions
            }
        }

        with open(rule_file, 'w') as f:
            yaml.dump(rule_yaml, f, default_flow_style=False)

        # Record promotion in incident database
        incident_ids = [i.get("id") for i in candidate.sample_incidents if i.get("id")]
        self.incident_db.mark_promoted(
            pattern_signature=candidate.pattern_signature,
            rule_yaml=yaml.dump(rule_yaml),
            incident_ids=incident_ids
        )

        # Track promotion
        self.promoted_patterns[candidate.pattern_signature] = datetime.utcnow()

        logger.info(
            f"Promoted pattern {candidate.pattern_signature} to rule {rule.id}"
        )

        return rule

    def get_promotion_report(self) -> Dict[str, Any]:
        """Generate a report of promotable patterns for review."""
        candidates = self.find_promotion_candidates()

        return {
            "generated_at": datetime.utcnow().isoformat(),
            "total_candidates": len(candidates),
            "promotion_criteria": {
                "min_occurrences": self.config.min_occurrences,
                "min_l2_resolutions": self.config.min_l2_resolutions,
                "min_success_rate": self.config.min_success_rate,
                "max_avg_resolution_time_ms": self.config.max_avg_resolution_time_ms
            },
            "candidates": [
                {
                    "pattern_signature": c.pattern_signature,
                    "recommended_action": c.recommended_action,
                    "confidence_score": c.confidence_score,
                    "promotion_reason": c.promotion_reason,
                    "stats": {
                        "total_occurrences": c.stats.total_occurrences,
                        "success_rate": c.stats.success_rate,
                        "l1_resolutions": c.stats.l1_resolutions,
                        "l2_resolutions": c.stats.l2_resolutions,
                        "l3_resolutions": c.stats.l3_resolutions,
                        "avg_resolution_time_ms": c.stats.avg_resolution_time_ms
                    }
                }
                for c in candidates
            ]
        }

    def get_learning_metrics(self, days: int = 30) -> Dict[str, Any]:
        """
        Get learning metrics for the data flywheel.

        Shows how the system is improving over time.
        """
        # Get incident stats
        stats = self.incident_db.get_stats_summary(days=days)

        # Count promoted rules
        promoted_count = len(list(self.config.promotion_output_dir.glob("*.yaml"))) \
            if self.config.promotion_output_dir.exists() else 0

        return {
            "period_days": days,
            "total_incidents": stats["total_incidents"],
            "resolution_breakdown": {
                "l1_percentage": stats["l1_percentage"],
                "l2_percentage": stats["l2_percentage"],
                "l3_percentage": stats["l3_percentage"]
            },
            "success_rate": stats["success_rate"],
            "avg_resolution_time_ms": stats["avg_resolution_time_ms"],
            "promoted_rules_count": promoted_count,
            "promotion_candidates": len(self.find_promotion_candidates()),
            "flywheel_status": self._assess_flywheel_health(stats)
        }

    def _assess_flywheel_health(self, stats: Dict[str, Any]) -> str:
        """Assess health of the data flywheel."""
        l1_pct = stats["l1_percentage"]
        success = stats["success_rate"]

        if l1_pct >= 70 and success >= 95:
            return "excellent"
        elif l1_pct >= 50 and success >= 85:
            return "good"
        elif l1_pct >= 30 and success >= 70:
            return "developing"
        else:
            return "needs_attention"
