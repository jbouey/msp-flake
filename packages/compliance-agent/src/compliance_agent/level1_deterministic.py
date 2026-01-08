"""
Level 1: Deterministic Rules Engine.

Handles 70-80% of incidents with:
- Sub-100ms response time
- Zero LLM cost
- Predictable, auditable behavior
- YAML-based rule definitions

Rules are loaded from:
1. Built-in default rules
2. Custom rules directory
3. Promoted rules from Level 2 learning
"""

import re
import yaml
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum

from .incident_db import IncidentDatabase, ResolutionLevel, IncidentOutcome


logger = logging.getLogger(__name__)


class MatchOperator(str, Enum):
    """Operators for rule matching."""
    EQUALS = "eq"
    NOT_EQUALS = "ne"
    CONTAINS = "contains"
    REGEX = "regex"
    GREATER_THAN = "gt"
    LESS_THAN = "lt"
    IN = "in"
    NOT_IN = "not_in"


@dataclass
class RuleCondition:
    """A single condition in a rule."""
    field: str
    operator: MatchOperator
    value: Any

    def matches(self, data: Dict[str, Any]) -> bool:
        """Check if this condition matches the data."""
        # Navigate nested fields with dot notation
        actual_value = self._get_field_value(data, self.field)

        if actual_value is None:
            return False

        if self.operator == MatchOperator.EQUALS:
            return actual_value == self.value
        elif self.operator == MatchOperator.NOT_EQUALS:
            return actual_value != self.value
        elif self.operator == MatchOperator.CONTAINS:
            return self.value in str(actual_value)
        elif self.operator == MatchOperator.REGEX:
            return bool(re.search(self.value, str(actual_value)))
        elif self.operator == MatchOperator.GREATER_THAN:
            return float(actual_value) > float(self.value)
        elif self.operator == MatchOperator.LESS_THAN:
            return float(actual_value) < float(self.value)
        elif self.operator == MatchOperator.IN:
            return actual_value in self.value
        elif self.operator == MatchOperator.NOT_IN:
            return actual_value not in self.value

        return False

    def _get_field_value(self, data: Dict[str, Any], field: str) -> Any:
        """Get nested field value using dot notation."""
        parts = field.split(".")
        value = data

        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None

        return value


@dataclass
class Rule:
    """A deterministic rule for incident handling."""
    id: str
    name: str
    description: str
    conditions: List[RuleCondition]
    action: str
    action_params: Dict[str, Any] = field(default_factory=dict)
    hipaa_controls: List[str] = field(default_factory=list)
    severity_filter: Optional[List[str]] = None
    enabled: bool = True
    priority: int = 100  # Lower = higher priority
    cooldown_seconds: int = 300
    max_retries: int = 1
    source: str = "builtin"  # builtin, custom, promoted

    def matches(self, incident_type: str, severity: str, data: Dict[str, Any]) -> bool:
        """Check if this rule matches an incident."""
        if not self.enabled:
            return False

        # Check severity filter
        if self.severity_filter and severity not in self.severity_filter:
            return False

        # Check all conditions (AND logic)
        for condition in self.conditions:
            if not condition.matches(data):
                return False

        return True

    @classmethod
    def from_yaml(cls, yaml_data: Dict[str, Any], source: str = "custom") -> 'Rule':
        """Create a Rule from YAML data."""
        conditions = []
        for cond in yaml_data.get("conditions", []):
            conditions.append(RuleCondition(
                field=cond["field"],
                operator=MatchOperator(cond["operator"]),
                value=cond["value"]
            ))

        return cls(
            id=yaml_data["id"],
            name=yaml_data["name"],
            description=yaml_data.get("description", ""),
            conditions=conditions,
            action=yaml_data["action"],
            action_params=yaml_data.get("action_params", {}),
            hipaa_controls=yaml_data.get("hipaa_controls", []),
            severity_filter=yaml_data.get("severity_filter"),
            enabled=yaml_data.get("enabled", True),
            priority=yaml_data.get("priority", 100),
            cooldown_seconds=yaml_data.get("cooldown_seconds", 300),
            max_retries=yaml_data.get("max_retries", 1),
            source=source
        )

    def to_yaml(self) -> Dict[str, Any]:
        """Convert rule to YAML-serializable dict."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "conditions": [
                {
                    "field": c.field,
                    "operator": c.operator.value,
                    "value": c.value
                }
                for c in self.conditions
            ],
            "action": self.action,
            "action_params": self.action_params,
            "hipaa_controls": self.hipaa_controls,
            "severity_filter": self.severity_filter,
            "enabled": self.enabled,
            "priority": self.priority,
            "cooldown_seconds": self.cooldown_seconds,
            "max_retries": self.max_retries,
            "source": self.source
        }


@dataclass
class RuleMatch:
    """Result of a rule match."""
    rule: Rule
    incident_id: str
    matched_at: str
    action: str
    action_params: Dict[str, Any]


class DeterministicEngine:
    """
    Level 1 Deterministic Rules Engine.

    Provides fast, predictable incident resolution using
    pattern-matched rules. No LLM involvement.
    """

    def __init__(
        self,
        rules_dir: Optional[Path] = None,
        incident_db: Optional[IncidentDatabase] = None,
        action_executor: Optional[Callable] = None
    ):
        self.rules_dir = rules_dir or Path("/etc/msp/rules")
        self.incident_db = incident_db
        self.action_executor = action_executor
        self.rules: List[Rule] = []
        self.cooldowns: Dict[str, datetime] = {}  # rule_id:host_id -> last_execution

        self._load_rules()

    def _load_rules(self):
        """Load all rules from built-in defaults and custom directory."""
        self.rules = []

        # Load built-in rules
        self._load_builtin_rules()

        # Load custom rules from directory
        if self.rules_dir.exists():
            for rule_file in self.rules_dir.glob("*.yaml"):
                try:
                    self._load_rule_file(rule_file)
                except Exception as e:
                    logger.error(f"Failed to load rule file {rule_file}: {e}")

            for rule_file in self.rules_dir.glob("*.yml"):
                try:
                    self._load_rule_file(rule_file)
                except Exception as e:
                    logger.error(f"Failed to load rule file {rule_file}: {e}")

        # Sort by priority (lower = higher priority)
        self.rules.sort(key=lambda r: r.priority)

        logger.info(f"Loaded {len(self.rules)} rules")

    def _load_rule_file(self, path: Path):
        """Load rules from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        if isinstance(data, dict) and "rules" in data:
            # Multiple rules in one file
            for rule_data in data["rules"]:
                self.rules.append(Rule.from_yaml(rule_data, source="custom"))
        elif isinstance(data, dict):
            # Single rule
            self.rules.append(Rule.from_yaml(data, source="custom"))

    def _load_builtin_rules(self):
        """Load built-in default rules."""
        builtin_rules = [
            # Patching drift - update to baseline
            Rule(
                id="L1-PATCH-001",
                name="Patching Generation Drift",
                description="NixOS generation behind baseline, trigger update",
                conditions=[
                    RuleCondition("check_type", MatchOperator.EQUALS, "patching"),
                    RuleCondition("drift_detected", MatchOperator.EQUALS, True),
                    RuleCondition("details.generation_drift", MatchOperator.EQUALS, True),
                ],
                action="update_to_baseline_generation",
                action_params={"verify_after": True},
                hipaa_controls=["164.308(a)(5)(ii)(B)"],
                priority=10,
                source="builtin"
            ),

            # AV/EDR service not running
            Rule(
                id="L1-AV-001",
                name="AV/EDR Service Down",
                description="Antivirus or EDR service not running",
                conditions=[
                    RuleCondition("check_type", MatchOperator.EQUALS, "av_edr"),
                    RuleCondition("drift_detected", MatchOperator.EQUALS, True),
                    RuleCondition("details.service_running", MatchOperator.EQUALS, False),
                ],
                action="restart_av_service",
                action_params={"service_name": "clamav-daemon"},
                hipaa_controls=["164.308(a)(5)(ii)(B)"],
                priority=5,  # High priority - security critical
                source="builtin"
            ),

            # Backup failure
            Rule(
                id="L1-BACKUP-001",
                name="Backup Job Failure",
                description="Backup job failed or missing",
                conditions=[
                    RuleCondition("check_type", MatchOperator.EQUALS, "backup"),
                    RuleCondition("drift_detected", MatchOperator.EQUALS, True),
                    RuleCondition("details.last_backup_success", MatchOperator.EQUALS, False),
                ],
                action="run_backup_job",
                action_params={"job_name": "restic-backup"},
                hipaa_controls=["164.308(a)(7)(ii)(A)"],
                priority=15,
                source="builtin"
            ),

            # Backup age drift
            Rule(
                id="L1-BACKUP-002",
                name="Backup Age Exceeded",
                description="Last successful backup too old",
                conditions=[
                    RuleCondition("check_type", MatchOperator.EQUALS, "backup"),
                    RuleCondition("drift_detected", MatchOperator.EQUALS, True),
                    RuleCondition("details.age_hours", MatchOperator.GREATER_THAN, 24),
                ],
                action="run_backup_job",
                action_params={"job_name": "restic-backup", "force": True},
                hipaa_controls=["164.308(a)(7)(ii)(A)"],
                priority=20,
                source="builtin"
            ),

            # Logging service down
            Rule(
                id="L1-LOG-001",
                name="Logging Service Down",
                description="Audit logging service not running",
                conditions=[
                    RuleCondition("check_type", MatchOperator.EQUALS, "logging"),
                    RuleCondition("drift_detected", MatchOperator.EQUALS, True),
                    RuleCondition("details.services_running", MatchOperator.EQUALS, False),
                ],
                action="restart_logging_services",
                action_params={},
                hipaa_controls=["164.312(b)"],
                priority=5,  # High priority - audit critical
                source="builtin"
            ),

            # Firewall rules changed
            Rule(
                id="L1-FW-001",
                name="Firewall Configuration Drift",
                description="Firewall rules deviated from baseline",
                conditions=[
                    RuleCondition("check_type", MatchOperator.EQUALS, "firewall"),
                    RuleCondition("drift_detected", MatchOperator.EQUALS, True),
                ],
                action="restore_firewall_baseline",
                action_params={},
                hipaa_controls=["164.312(e)(1)"],
                priority=10,
                source="builtin"
            ),

            # Encryption volume issue - escalate (no auto-fix)
            Rule(
                id="L1-ENCRYPT-001",
                name="Encryption Status Alert",
                description="Encryption issue detected - escalate to human",
                conditions=[
                    RuleCondition("check_type", MatchOperator.EQUALS, "encryption"),
                    RuleCondition("drift_detected", MatchOperator.EQUALS, True),
                ],
                action="escalate",
                action_params={
                    "reason": "Encryption changes require human verification",
                    "urgency": "high"
                },
                hipaa_controls=["164.312(a)(2)(iv)"],
                priority=1,  # Highest priority - immediately escalate
                source="builtin"
            ),

            # Certificate expiring soon
            Rule(
                id="L1-CERT-001",
                name="Certificate Expiring",
                description="TLS certificate expiring within 30 days",
                conditions=[
                    RuleCondition("incident_type", MatchOperator.EQUALS, "cert_expiry"),
                    RuleCondition("details.days_remaining", MatchOperator.LESS_THAN, 30),
                ],
                action="renew_certificate",
                action_params={},
                hipaa_controls=["164.312(e)(1)"],
                priority=25,
                source="builtin"
            ),

            # Disk space critical
            Rule(
                id="L1-DISK-001",
                name="Disk Space Critical",
                description="Disk usage above 90%",
                conditions=[
                    RuleCondition("incident_type", MatchOperator.EQUALS, "disk_space"),
                    RuleCondition("details.usage_percent", MatchOperator.GREATER_THAN, 90),
                ],
                action="cleanup_disk_space",
                action_params={"targets": ["/var/log", "/tmp", "/var/cache"]},
                hipaa_controls=[],
                priority=15,
                source="builtin"
            ),

            # Service crash loop
            Rule(
                id="L1-SERVICE-001",
                name="Service Crash Loop",
                description="Service restarting repeatedly",
                conditions=[
                    RuleCondition("incident_type", MatchOperator.EQUALS, "service_crash"),
                    RuleCondition("details.restart_count", MatchOperator.GREATER_THAN, 3),
                ],
                action="escalate",
                action_params={
                    "reason": "Service in crash loop - requires investigation",
                    "include_logs": True
                },
                hipaa_controls=[],
                priority=10,
                source="builtin"
            ),
        ]

        self.rules.extend(builtin_rules)

    def reload_rules(self):
        """Reload rules from disk."""
        self._load_rules()

    def add_promoted_rule(self, rule: Rule):
        """Add a rule promoted from Level 2."""
        rule.source = "promoted"
        self.rules.append(rule)
        self.rules.sort(key=lambda r: r.priority)

        # Save to promoted rules directory
        promoted_dir = self.rules_dir / "promoted"
        promoted_dir.mkdir(parents=True, exist_ok=True)

        rule_file = promoted_dir / f"{rule.id}.yaml"
        with open(rule_file, 'w') as f:
            yaml.dump(rule.to_yaml(), f, default_flow_style=False)

        logger.info(f"Added promoted rule: {rule.id}")

    def match(
        self,
        incident_id: str,
        incident_type: str,
        severity: str,
        data: Dict[str, Any]
    ) -> Optional[RuleMatch]:
        """
        Find the first matching rule for an incident.

        Returns RuleMatch if found, None if no rule matches (escalate to L2).
        """
        for rule in self.rules:
            if rule.matches(incident_type, severity, data):
                # Check cooldown
                cooldown_key = f"{rule.id}:{data.get('host_id', 'unknown')}"

                if cooldown_key in self.cooldowns:
                    last_exec = self.cooldowns[cooldown_key]
                    elapsed = (datetime.now(timezone.utc) - last_exec).total_seconds()

                    if elapsed < rule.cooldown_seconds:
                        logger.debug(
                            f"Rule {rule.id} in cooldown ({elapsed:.0f}s < {rule.cooldown_seconds}s)"
                        )
                        continue

                return RuleMatch(
                    rule=rule,
                    incident_id=incident_id,
                    matched_at=datetime.now(timezone.utc).isoformat(),
                    action=rule.action,
                    action_params=rule.action_params
                )

        return None

    async def execute(
        self,
        match: RuleMatch,
        site_id: str,
        host_id: str
    ) -> Dict[str, Any]:
        """
        Execute a matched rule's action.

        Returns execution result with success/failure status.
        """
        start_time = datetime.now(timezone.utc)
        result = {
            "rule_id": match.rule.id,
            "incident_id": match.incident_id,
            "action": match.action,
            "started_at": start_time.isoformat(),
            "success": False,
            "output": None,
            "error": None
        }

        try:
            # Update cooldown
            cooldown_key = f"{match.rule.id}:{host_id}"
            self.cooldowns[cooldown_key] = start_time

            # Execute action
            if self.action_executor:
                output = await self.action_executor(
                    action=match.action,
                    params=match.action_params,
                    site_id=site_id,
                    host_id=host_id
                )
                result["output"] = output
                # Check if action_executor returned success status
                if isinstance(output, dict):
                    result["success"] = output.get("success", False)
                    result["error"] = output.get("error")
                else:
                    result["success"] = True  # Legacy: assume success if no dict returned
            else:
                # No executor configured - dry run
                logger.warning(f"No action executor configured, dry run: {match.action}")
                result["output"] = "DRY_RUN"
                result["success"] = True

            end_time = datetime.now(timezone.utc)
            result["completed_at"] = end_time.isoformat()
            result["duration_ms"] = int((end_time - start_time).total_seconds() * 1000)

            # Record in incident database
            if self.incident_db:
                outcome = IncidentOutcome.SUCCESS if result["success"] else IncidentOutcome.FAILURE
                self.incident_db.resolve_incident(
                    incident_id=match.incident_id,
                    resolution_level=ResolutionLevel.LEVEL1_DETERMINISTIC,
                    resolution_action=match.action,
                    outcome=outcome,
                    resolution_time_ms=result["duration_ms"]
                )

        except Exception as e:
            logger.error(f"Rule execution failed: {e}")
            result["error"] = str(e)
            result["completed_at"] = datetime.now(timezone.utc).isoformat()

            if self.incident_db:
                self.incident_db.resolve_incident(
                    incident_id=match.incident_id,
                    resolution_level=ResolutionLevel.LEVEL1_DETERMINISTIC,
                    resolution_action=match.action,
                    outcome=IncidentOutcome.FAILURE,
                    resolution_time_ms=int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
                )

        return result

    def get_rule_stats(self) -> Dict[str, Any]:
        """Get statistics about loaded rules."""
        by_source = {"builtin": 0, "custom": 0, "promoted": 0}
        by_action = {}

        for rule in self.rules:
            by_source[rule.source] = by_source.get(rule.source, 0) + 1
            by_action[rule.action] = by_action.get(rule.action, 0) + 1

        return {
            "total_rules": len(self.rules),
            "enabled_rules": len([r for r in self.rules if r.enabled]),
            "by_source": by_source,
            "by_action": by_action,
            "active_cooldowns": len(self.cooldowns)
        }

    def list_rules(self) -> List[Dict[str, Any]]:
        """List all rules with their details."""
        return [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "action": r.action,
                "priority": r.priority,
                "enabled": r.enabled,
                "source": r.source,
                "hipaa_controls": r.hipaa_controls
            }
            for r in self.rules
        ]
