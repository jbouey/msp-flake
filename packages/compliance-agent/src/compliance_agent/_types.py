"""
Single source of truth for all shared types in compliance-agent.

IMPORTANT: Import types from this module, not from individual files.
This ensures consistency across the codebase and enables proper type checking.

Usage:
    from compliance_agent._types import (
        Incident, EvidenceBundle, ComplianceCheck,
        CheckStatus, Severity, CheckType,
        now_utc  # Use instead of datetime.utcnow()
    )
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
from dataclasses import dataclass, field
from pydantic import BaseModel, Field
import uuid


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def now_utc() -> datetime:
    """
    Get current UTC time with timezone info.

    Use this instead of datetime.utcnow() which is deprecated.
    """
    return datetime.now(timezone.utc)


def generate_id() -> str:
    """Generate a unique ID string (UUID4)."""
    return str(uuid.uuid4())


# =============================================================================
# ENUMS
# =============================================================================


class Severity(str, Enum):
    """Incident severity levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CheckStatus(str, Enum):
    """Status of a compliance check."""
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


class CheckType(str, Enum):
    """
    All compliance check types.

    Core HIPAA checks:
    - patching, antivirus, backup, logging, firewall, encryption, network

    Extended monitoring:
    - ntp_sync, certificate_expiry, database_corruption, memory_pressure
    - windows_defender, disk_space, service_health, prohibited_port

    Workstation checks (Go agent):
    - workstation, bitlocker, defender, patches, screen_lock
    """
    # Core compliance checks
    PATCHING = "patching"
    ANTIVIRUS = "antivirus"
    BACKUP = "backup"
    LOGGING = "logging"
    FIREWALL = "firewall"
    ENCRYPTION = "encryption"
    NETWORK = "network"

    # Extended monitoring
    NTP_SYNC = "ntp_sync"
    CERTIFICATE_EXPIRY = "certificate_expiry"
    DATABASE_CORRUPTION = "database_corruption"
    MEMORY_PRESSURE = "memory_pressure"
    WINDOWS_DEFENDER = "windows_defender"
    DISK_SPACE = "disk_space"
    SERVICE_HEALTH = "service_health"
    PROHIBITED_PORT = "prohibited_port"

    # Linux checks (from runbooks)
    SSH_CONFIG = "ssh_config"
    KERNEL = "kernel"
    CRON = "cron"
    PERMISSIONS = "permissions"
    ACCOUNTS = "accounts"

    # Workstation checks (from Go agent)
    WORKSTATION = "workstation"
    BITLOCKER = "bitlocker"
    DEFENDER = "defender"
    PATCHES = "patches"
    SCREEN_LOCK = "screen_lock"


class ResolutionLevel(str, Enum):
    """Auto-healing resolution levels."""
    L1 = "L1"  # Deterministic rules
    L2 = "L2"  # LLM-assisted
    L3 = "L3"  # Human escalation


class HealthStatus(str, Enum):
    """Health status categories."""
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"


class PatternStatus(str, Enum):
    """Learning loop pattern status."""
    PENDING = "pending"
    APPROVED = "approved"
    PROMOTED = "promoted"
    REJECTED = "rejected"


class CapabilityTier(str, Enum):
    """Go agent capability tiers (mirrors proto enum)."""
    MONITOR_ONLY = "monitor_only"
    SELF_HEAL = "self_heal"
    FULL_REMEDIATION = "full_remediation"


# Outcome types for evidence bundles
OutcomeType = Literal[
    "success", "failed", "reverted", "deferred",
    "alert", "rejected", "expired", "pending_approval"
]

# Deployment mode types
DeploymentMode = Literal["reseller", "direct"]


# =============================================================================
# DATACLASSES (for internal use, no validation)
# =============================================================================


@dataclass
class Incident:
    """
    Incident record for compliance violations.

    This is the core type for tracking compliance issues.
    """
    incident_id: str = field(default_factory=generate_id)
    site_id: str = ""
    hostname: str = ""
    check_type: str = ""  # CheckType value
    severity: str = "medium"  # Severity value
    status: str = "open"  # open, resolved, escalated
    source: str = "drift_detection"
    details: Dict[str, Any] = field(default_factory=dict)
    hipaa_controls: List[str] = field(default_factory=list)
    resolution_level: Optional[str] = None  # ResolutionLevel value
    resolved_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=now_utc)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "incident_id": self.incident_id,
            "site_id": self.site_id,
            "hostname": self.hostname,
            "check_type": self.check_type,
            "severity": self.severity,
            "status": self.status,
            "source": self.source,
            "details": self.details,
            "hipaa_controls": self.hipaa_controls,
            "resolution_level": self.resolution_level,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class ComplianceCheck:
    """Result of a single compliance check."""
    check_type: str  # CheckType value
    status: str = "unknown"  # CheckStatus value
    passed: bool = False
    expected: str = ""
    actual: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    hipaa_controls: List[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=now_utc)


@dataclass
class DriftEvent:
    """
    Drift event from Go agent (mirrors proto DriftEvent).
    """
    agent_id: str = ""
    hostname: str = ""
    check_type: str = ""
    passed: bool = True
    expected: str = ""
    actual: str = ""
    hipaa_control: str = ""
    timestamp: int = 0  # Unix timestamp
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class HealCommand:
    """
    Command to execute healing on Go agent (mirrors proto HealCommand).
    """
    command_id: str = field(default_factory=generate_id)
    check_type: str = ""
    action: str = ""
    params: Dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 60


@dataclass
class HealingResult:
    """
    Result of a healing attempt (mirrors proto HealingResult).
    """
    agent_id: str = ""
    hostname: str = ""
    check_type: str = ""
    success: bool = False
    error_message: str = ""
    timestamp: int = 0
    artifacts: Dict[str, str] = field(default_factory=dict)
    command_id: str = ""


@dataclass
class PatternReport:
    """Pattern report for learning loop."""
    site_id: str = ""
    check_type: str = ""
    issue_signature: str = ""
    resolution_steps: List[str] = field(default_factory=list)
    success: bool = False
    execution_time_ms: int = 0
    runbook_id: Optional[str] = None
    reported_at: datetime = field(default_factory=now_utc)


# =============================================================================
# PYDANTIC MODELS (for API/validation)
# =============================================================================


class IncidentModel(BaseModel):
    """Pydantic model for Incident API serialization."""
    incident_id: str = Field(default_factory=generate_id)
    site_id: str
    hostname: str
    check_type: str
    severity: Severity = Severity.MEDIUM
    status: str = "open"
    source: str = "drift_detection"
    details: Dict[str, Any] = Field(default_factory=dict)
    hipaa_controls: List[str] = Field(default_factory=list)
    resolution_level: Optional[ResolutionLevel] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=now_utc)


class ComplianceCheckModel(BaseModel):
    """Pydantic model for compliance check results."""
    check_type: CheckType
    status: CheckStatus = CheckStatus.UNKNOWN
    passed: bool = False
    expected: str = ""
    actual: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)
    hipaa_controls: List[str] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=now_utc)


class HealthMetrics(BaseModel):
    """Health metrics for appliance or client."""
    overall: float = Field(ge=0, le=100, default=100.0)
    connectivity_score: float = Field(ge=0, le=100, default=100.0)
    compliance_score: float = Field(ge=0, le=100, default=100.0)
    status: HealthStatus = HealthStatus.HEALTHY

    @property
    def is_healthy(self) -> bool:
        return self.status == HealthStatus.HEALTHY


# =============================================================================
# TYPE ALIASES
# =============================================================================

# JSON-compatible types
JSONValue = Union[str, int, float, bool, None, Dict[str, Any], List[Any]]
JSONDict = Dict[str, JSONValue]

# Common parameter types
SiteId = str
HostId = str
IncidentId = str
RunbookId = str
PatternId = str
BundleId = str


# =============================================================================
# HIPAA CONTROL MAPPINGS
# =============================================================================

HIPAA_CONTROLS: Dict[str, List[str]] = {
    "patching": ["164.308(a)(1)(ii)(B)", "164.308(a)(5)(ii)(B)"],
    "antivirus": ["164.308(a)(5)(ii)(B)"],
    "backup": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"],
    "logging": ["164.312(b)", "164.308(a)(1)(ii)(D)"],
    "firewall": ["164.312(a)(1)", "164.312(e)(1)"],
    "encryption": ["164.312(a)(2)(iv)", "164.312(e)(2)(ii)"],
    "network": ["164.312(e)(1)"],
    "bitlocker": ["164.312(a)(2)(iv)"],
    "defender": ["164.308(a)(5)(ii)(B)"],
    "screen_lock": ["164.312(a)(2)(iii)"],
    "ntp_sync": ["164.312(b)"],
}


def get_hipaa_controls(check_type: str) -> List[str]:
    """Get HIPAA control citations for a check type."""
    return HIPAA_CONTROLS.get(check_type.lower(), [])


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Functions
    "now_utc",
    "generate_id",
    "get_hipaa_controls",

    # Enums
    "Severity",
    "CheckStatus",
    "CheckType",
    "ResolutionLevel",
    "HealthStatus",
    "PatternStatus",
    "CapabilityTier",

    # Type aliases
    "OutcomeType",
    "DeploymentMode",
    "JSONValue",
    "JSONDict",
    "SiteId",
    "HostId",
    "IncidentId",
    "RunbookId",
    "PatternId",
    "BundleId",

    # Dataclasses
    "Incident",
    "ComplianceCheck",
    "DriftEvent",
    "HealCommand",
    "HealingResult",
    "PatternReport",

    # Pydantic models
    "IncidentModel",
    "ComplianceCheckModel",
    "HealthMetrics",

    # Constants
    "HIPAA_CONTROLS",
]
