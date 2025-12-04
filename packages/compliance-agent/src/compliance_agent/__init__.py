"""MSP Compliance Agent - Self-Healing NixOS Agent"""

__version__ = "0.2.0"

# Three-Tier Auto-Healing Architecture
from .incident_db import IncidentDatabase, Incident, ResolutionLevel, IncidentOutcome
from .level1_deterministic import DeterministicEngine, Rule, RuleMatch
from .level2_llm import Level2Planner, LLMConfig, LLMMode, LLMDecision
from .level3_escalation import EscalationHandler, EscalationConfig, EscalationTicket
from .learning_loop import SelfLearningSystem, PromotionConfig, PromotionCandidate
from .auto_healer import AutoHealer, AutoHealerConfig, HealingResult, create_auto_healer
from .backup_restore_test import (
    BackupRestoreTester,
    RestoreTestConfig,
    RestoreTestResult,
    run_backup_restore_test
)

__all__ = [
    # Version
    "__version__",

    # Incident Database
    "IncidentDatabase",
    "Incident",
    "ResolutionLevel",
    "IncidentOutcome",

    # Level 1 - Deterministic
    "DeterministicEngine",
    "Rule",
    "RuleMatch",

    # Level 2 - LLM
    "Level2Planner",
    "LLMConfig",
    "LLMMode",
    "LLMDecision",

    # Level 3 - Escalation
    "EscalationHandler",
    "EscalationConfig",
    "EscalationTicket",

    # Learning Loop
    "SelfLearningSystem",
    "PromotionConfig",
    "PromotionCandidate",

    # Auto-Healer Orchestrator
    "AutoHealer",
    "AutoHealerConfig",
    "HealingResult",
    "create_auto_healer",

    # Backup Restore Testing
    "BackupRestoreTester",
    "RestoreTestConfig",
    "RestoreTestResult",
    "run_backup_restore_test",
]
