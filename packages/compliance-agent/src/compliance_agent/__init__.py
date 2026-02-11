"""MSP Compliance Agent - Evidence Capture & Remediation for NixOS"""

__version__ = "1.0.68"

# Three-Tier Remediation Architecture
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
from .portal_controls import PortalControlChecker, ControlResult
from .phone_home import PhoneHome
from .provisioning import (
    needs_provisioning,
    run_provisioning_cli,
    run_provisioning_auto,
    claim_provision_code,
)
from .ntp_verify import (
    NTPVerifier,
    NTPVerificationResult,
    NTPServerResult,
    verify_time_for_evidence,
    get_verified_timestamp,
)

# Linux Drift Detection
from .linux_drift import (
    LinuxDriftDetector,
    DriftResult,
    RemediationResult,
)
from .runbooks.linux.executor import (
    LinuxTarget,
    LinuxExecutor,
    LinuxExecutionResult,
)
from .runbooks.linux.runbooks import (
    LinuxRunbook,
    RUNBOOKS as LINUX_RUNBOOKS,
)

# Network Posture Detection
from .network_posture import (
    NetworkPostureDetector,
    NetworkPostureResult,
    ListeningPort,
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

    # Portal Controls
    "PortalControlChecker",
    "ControlResult",

    # Phone Home
    "PhoneHome",

    # Provisioning
    "needs_provisioning",
    "run_provisioning_cli",
    "run_provisioning_auto",
    "claim_provision_code",

    # NTP Verification
    "NTPVerifier",
    "NTPVerificationResult",
    "NTPServerResult",
    "verify_time_for_evidence",
    "get_verified_timestamp",

    # Linux Drift Detection
    "LinuxDriftDetector",
    "DriftResult",
    "RemediationResult",
    "LinuxTarget",
    "LinuxExecutor",
    "LinuxExecutionResult",
    "LinuxRunbook",
    "LINUX_RUNBOOKS",

    # Network Posture Detection
    "NetworkPostureDetector",
    "NetworkPostureResult",
    "ListeningPort",
]
