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
from datetime import datetime, timedelta, timezone
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
            days_since = (datetime.now(timezone.utc) - last_seen).days
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
        """
        Extract common action parameters from successful incidents.

        Analyzes incident raw_data to find parameters that were consistently
        used in successful resolutions, enabling promoted rules to have
        properly configured action parameters.
        """
        if not incidents:
            return {}

        params = {}

        # Collect all raw_data from incidents
        raw_data_list = []
        for incident in incidents:
            raw_data_value = incident.get("raw_data", "{}")
            if isinstance(raw_data_value, dict):
                raw_data_list.append(raw_data_value)
            else:
                try:
                    raw_data_list.append(json.loads(raw_data_value))
                except (json.JSONDecodeError, TypeError):
                    continue

        if not raw_data_list:
            return {}

        # Parameter extraction based on action type
        action_param_keys = {
            "update_to_baseline_generation": [
                "target_generation", "baseline_hash", "flake_url"
            ],
            "restart_av_service": [
                "service_name", "av_product", "expected_hash"
            ],
            "run_backup_job": [
                "backup_repo", "backup_paths", "restic_repo", "retention_days"
            ],
            "restart_logging_services": [
                "logging_services", "log_destination", "service_name"
            ],
            "restore_firewall_baseline": [
                "ruleset_path", "baseline_rules", "allowed_ports"
            ],
            "enable_volume_encryption": [
                "volume_path", "encryption_type", "key_file"
            ]
        }

        # Get keys to look for based on action
        keys_to_extract = action_param_keys.get(action_name, [])

        # Also extract common keys that appear across all action types
        common_keys = [
            "service_name", "target_path", "timeout", "host_id",
            "check_type", "severity"
        ]
        keys_to_extract.extend(common_keys)

        # Count occurrences of each parameter value
        param_counts: Dict[str, Dict[Any, int]] = {}

        for raw_data in raw_data_list:
            for key in keys_to_extract:
                if key in raw_data:
                    value = raw_data[key]
                    # Convert lists to tuples for hashability
                    if isinstance(value, list):
                        value = tuple(value)

                    if key not in param_counts:
                        param_counts[key] = {}

                    # Convert value to string for counting (handles unhashable types)
                    value_key = str(value) if not isinstance(value, (str, int, float, bool, tuple)) else value
                    param_counts[key][value_key] = param_counts[key].get(value_key, 0) + 1

        # Select the most common value for each parameter
        min_occurrences = max(len(raw_data_list) // 2, 1)  # Must appear in at least half

        for key, counts in param_counts.items():
            if counts:
                # Find the most common value
                most_common_value = max(counts.items(), key=lambda x: x[1])
                value, count = most_common_value

                # Only include if it appears in enough incidents
                if count >= min_occurrences:
                    # Convert tuples back to lists for JSON serialization
                    if isinstance(value, tuple):
                        value = list(value)
                    params[key] = value

        logger.debug(
            f"Extracted {len(params)} parameters for action {action_name}: "
            f"{list(params.keys())}"
        )

        return params

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
            "promoted_at": datetime.now(timezone.utc).isoformat(),
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
        self.promoted_patterns[candidate.pattern_signature] = datetime.now(timezone.utc)

        logger.info(
            f"Promoted pattern {candidate.pattern_signature} to rule {rule.id}"
        )

        return rule

    def get_promotion_report(self) -> Dict[str, Any]:
        """Generate a report of promotable patterns for review."""
        candidates = self.find_promotion_candidates()

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
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

    # =========================================================================
    # Post-Promotion Monitoring & Rollback
    # =========================================================================

    def monitor_promoted_rules(self) -> Dict[str, Any]:
        """
        Monitor promoted rules for effectiveness and trigger rollback if needed.

        Checks each promoted rule's post-promotion performance against the
        rollback threshold. Rules exceeding the failure rate will be disabled.

        Returns:
            Monitoring report with rule status and any rollback actions taken
        """
        report = {
            "monitored_at": datetime.now(timezone.utc).isoformat(),
            "rules_monitored": 0,
            "rules_healthy": 0,
            "rules_degraded": 0,
            "rollbacks_triggered": [],
            "rule_details": []
        }

        # Get all promoted rules
        if not self.config.promotion_output_dir.exists():
            return report

        rule_files = list(self.config.promotion_output_dir.glob("*.yaml"))
        report["rules_monitored"] = len(rule_files)

        for rule_file in rule_files:
            try:
                with open(rule_file, 'r') as f:
                    rule_data = yaml.safe_load(f)

                if not rule_data:
                    continue

                rule_id = rule_data.get("id", rule_file.stem)
                promotion_meta = rule_data.get("_promotion_metadata", {})
                promoted_at = promotion_meta.get("promoted_at")

                if not promoted_at:
                    continue

                # Get post-promotion incident stats for this rule
                stats = self._get_post_promotion_stats(rule_id, promoted_at)

                rule_detail = {
                    "rule_id": rule_id,
                    "promoted_at": promoted_at,
                    "post_promotion_incidents": stats["total"],
                    "success_rate": stats["success_rate"],
                    "failure_rate": stats["failure_rate"],
                    "status": "healthy"
                }

                # Check if failure rate exceeds threshold
                if stats["total"] >= 3:  # Need minimum sample size
                    if stats["failure_rate"] > self.config.rollback_on_failure_rate:
                        rule_detail["status"] = "degraded"
                        report["rules_degraded"] += 1

                        # Trigger rollback if auto-rollback is enabled
                        if self.config.track_promotion_effectiveness:
                            rollback_result = self._rollback_rule(rule_id, rule_file, stats)
                            report["rollbacks_triggered"].append(rollback_result)
                            rule_detail["rollback"] = rollback_result
                    else:
                        report["rules_healthy"] += 1
                else:
                    # Not enough data yet
                    rule_detail["status"] = "monitoring"
                    report["rules_healthy"] += 1

                report["rule_details"].append(rule_detail)

            except Exception as e:
                logger.warning(f"Error monitoring rule {rule_file}: {e}")
                report["rule_details"].append({
                    "rule_id": rule_file.stem,
                    "status": "error",
                    "error": str(e)
                })

        return report

    def _get_post_promotion_stats(
        self,
        rule_id: str,
        promoted_at: str
    ) -> Dict[str, Any]:
        """
        Get incident statistics for a rule since its promotion.

        Args:
            rule_id: The promoted rule ID
            promoted_at: ISO timestamp of when rule was promoted

        Returns:
            Stats dict with total, successes, failures, and rates
        """
        try:
            promotion_time = datetime.fromisoformat(promoted_at)
        except (ValueError, TypeError):
            return {"total": 0, "successes": 0, "failures": 0,
                    "success_rate": 1.0, "failure_rate": 0.0}

        # Query incidents resolved by this rule since promotion
        # Note: This requires the incident_db to track which rule resolved each incident
        conn = self.incident_db._get_connection()
        cursor = conn.execute('''
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN resolution_status = 'success' THEN 1 ELSE 0 END) as successes,
                SUM(CASE WHEN resolution_status = 'failed' THEN 1 ELSE 0 END) as failures
            FROM incidents
            WHERE resolution_level = 'L1'
            AND resolution_action LIKE ?
            AND resolved_at >= ?
        ''', (f'%{rule_id}%', promoted_at))

        row = cursor.fetchone()
        conn.close()

        if not row or row[0] == 0:
            return {"total": 0, "successes": 0, "failures": 0,
                    "success_rate": 1.0, "failure_rate": 0.0}

        total = row[0]
        successes = row[1] or 0
        failures = row[2] or 0

        return {
            "total": total,
            "successes": successes,
            "failures": failures,
            "success_rate": successes / total if total > 0 else 1.0,
            "failure_rate": failures / total if total > 0 else 0.0
        }

    def _rollback_rule(
        self,
        rule_id: str,
        rule_file: Path,
        stats: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Rollback a promoted rule by disabling it.

        Args:
            rule_id: The rule ID to rollback
            rule_file: Path to the rule YAML file
            stats: The statistics that triggered rollback

        Returns:
            Rollback result dict
        """
        rollback_result = {
            "rule_id": rule_id,
            "rolled_back_at": datetime.now(timezone.utc).isoformat(),
            "reason": f"Failure rate {stats['failure_rate']:.1%} exceeds threshold {self.config.rollback_on_failure_rate:.1%}",
            "stats_at_rollback": stats,
            "success": False
        }

        try:
            # Load the rule
            with open(rule_file, 'r') as f:
                rule_data = yaml.safe_load(f)

            # Disable the rule
            rule_data["enabled"] = False
            rule_data["_rollback_metadata"] = {
                "rolled_back_at": datetime.now(timezone.utc).isoformat(),
                "reason": rollback_result["reason"],
                "stats_at_rollback": stats
            }

            # Move to rolled-back directory
            rollback_dir = self.config.promotion_output_dir / "rolled_back"
            rollback_dir.mkdir(parents=True, exist_ok=True)

            # Save to rollback location
            rollback_file = rollback_dir / rule_file.name
            with open(rollback_file, 'w') as f:
                yaml.dump(rule_data, f, default_flow_style=False)

            # Remove from active rules
            rule_file.unlink()

            # Remove from tracked patterns
            if rule_id in self.promoted_patterns:
                del self.promoted_patterns[rule_id]

            rollback_result["success"] = True
            rollback_result["rollback_file"] = str(rollback_file)

            logger.warning(
                f"Rolled back rule {rule_id}: {rollback_result['reason']}"
            )

        except Exception as e:
            rollback_result["error"] = str(e)
            logger.error(f"Failed to rollback rule {rule_id}: {e}")

        return rollback_result

    def get_rollback_history(self) -> List[Dict[str, Any]]:
        """
        Get history of rolled-back rules.

        Returns:
            List of rolled-back rule metadata
        """
        history = []

        rollback_dir = self.config.promotion_output_dir / "rolled_back"
        if not rollback_dir.exists():
            return history

        for rule_file in rollback_dir.glob("*.yaml"):
            try:
                with open(rule_file, 'r') as f:
                    rule_data = yaml.safe_load(f)

                if rule_data:
                    history.append({
                        "rule_id": rule_data.get("id", rule_file.stem),
                        "name": rule_data.get("name", "Unknown"),
                        "rollback_metadata": rule_data.get("_rollback_metadata", {}),
                        "promotion_metadata": rule_data.get("_promotion_metadata", {})
                    })
            except Exception as e:
                logger.warning(f"Error reading rollback history for {rule_file}: {e}")

        return history
