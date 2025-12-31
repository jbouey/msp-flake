"""Pydantic models for Central Command Dashboard API.

All data models for the dashboard API including health metrics,
fleet overview, incidents, runbooks, learning loop, and onboarding.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# =============================================================================
# ENUMS
# =============================================================================

class HealthStatus(str, Enum):
    """Health status categories."""
    CRITICAL = "critical"
    WARNING = "warning"
    HEALTHY = "healthy"


class ResolutionLevel(str, Enum):
    """Auto-healing resolution levels."""
    L1 = "L1"  # Deterministic
    L2 = "L2"  # LLM-assisted
    L3 = "L3"  # Human escalation


class Severity(str, Enum):
    """Incident severity levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CheckType(str, Enum):
    """Compliance check types."""
    PATCHING = "patching"
    ANTIVIRUS = "antivirus"
    BACKUP = "backup"
    LOGGING = "logging"
    FIREWALL = "firewall"
    ENCRYPTION = "encryption"


class OnboardingStage(str, Enum):
    """Onboarding pipeline stages (two-phase)."""
    # Phase 1: Acquisition (B+D)
    LEAD = "lead"
    DISCOVERY = "discovery"
    PROPOSAL = "proposal"
    CONTRACT = "contract"
    INTAKE = "intake"
    CREDS = "creds"
    SHIPPED = "shipped"
    # Phase 2: Activation
    RECEIVED = "received"
    CONNECTIVITY = "connectivity"
    SCANNING = "scanning"
    BASELINE = "baseline"
    COMPLIANT = "compliant"
    ACTIVE = "active"


class CheckinStatus(str, Enum):
    """Appliance check-in status."""
    PENDING = "pending"
    CONNECTED = "connected"
    FAILED = "failed"


# =============================================================================
# HEALTH METRICS
# =============================================================================

class ConnectivityMetrics(BaseModel):
    """Connectivity health metrics (40% of overall)."""
    checkin_freshness: int = Field(ge=0, le=100, description="Score based on last check-in age")
    healing_success_rate: float = Field(ge=0, le=100, description="Successful heals / total incidents")
    order_execution_rate: float = Field(ge=0, le=100, description="Executed orders / total orders")
    score: float = Field(ge=0, le=100, description="Average of above three")


class ComplianceMetrics(BaseModel):
    """Compliance health metrics (60% of overall)."""
    patching: int = Field(ge=0, le=100, description="Patch compliance (0 or 100)")
    antivirus: int = Field(ge=0, le=100, description="AV compliance (0 or 100)")
    backup: int = Field(ge=0, le=100, description="Backup compliance (0 or 100)")
    logging: int = Field(ge=0, le=100, description="Logging compliance (0 or 100)")
    firewall: int = Field(ge=0, le=100, description="Firewall compliance (0 or 100)")
    encryption: int = Field(ge=0, le=100, description="Encryption compliance (0 or 100)")
    score: float = Field(ge=0, le=100, description="Average of above six")


class HealthMetrics(BaseModel):
    """Complete health metrics for an appliance or client."""
    connectivity: ConnectivityMetrics
    compliance: ComplianceMetrics
    overall: float = Field(ge=0, le=100, description="Weighted: connectivity*0.4 + compliance*0.6")
    status: HealthStatus


# =============================================================================
# FLEET MODELS
# =============================================================================

class Appliance(BaseModel):
    """Individual appliance in the fleet."""
    id: int
    site_id: str
    hostname: str
    ip_address: Optional[str] = None
    agent_version: Optional[str] = None
    tier: str = "standard"
    is_online: bool = False
    last_checkin: Optional[datetime] = None
    health: Optional[HealthMetrics] = None
    created_at: datetime


class ClientOverview(BaseModel):
    """Client summary for fleet overview."""
    site_id: str
    name: str
    appliance_count: int
    online_count: int
    health: HealthMetrics
    last_incident: Optional[datetime] = None
    incidents_24h: int = 0


class ClientDetail(BaseModel):
    """Detailed client view."""
    site_id: str
    name: str
    tier: str = "standard"
    appliances: List[Appliance]
    health: HealthMetrics
    recent_incidents: List["Incident"] = []
    compliance_breakdown: ComplianceMetrics


# =============================================================================
# INCIDENT MODELS
# =============================================================================

class Incident(BaseModel):
    """Incident summary for lists."""
    id: int
    site_id: str
    hostname: str
    check_type: CheckType
    severity: Severity
    resolution_level: Optional[ResolutionLevel] = None
    resolved: bool = False
    resolved_at: Optional[datetime] = None
    hipaa_controls: List[str] = []
    created_at: datetime


class IncidentDetail(BaseModel):
    """Full incident detail including evidence."""
    id: int
    site_id: str
    appliance_id: int
    hostname: str
    check_type: CheckType
    severity: Severity
    drift_data: Dict[str, Any] = {}
    resolution_level: Optional[ResolutionLevel] = None
    resolved: bool = False
    resolved_at: Optional[datetime] = None
    hipaa_controls: List[str] = []
    evidence_bundle_id: Optional[int] = None
    evidence_hash: Optional[str] = None
    runbook_executed: Optional[str] = None
    execution_log: Optional[str] = None
    created_at: datetime


# =============================================================================
# RUNBOOK MODELS
# =============================================================================

class Runbook(BaseModel):
    """Runbook summary for library listing."""
    id: str
    name: str
    description: str
    level: ResolutionLevel
    hipaa_controls: List[str] = []
    is_disruptive: bool = False
    execution_count: int = 0
    success_rate: float = 0.0
    avg_execution_time_ms: int = 0


class RunbookDetail(BaseModel):
    """Full runbook detail with steps."""
    id: str
    name: str
    description: str
    level: ResolutionLevel
    hipaa_controls: List[str] = []
    is_disruptive: bool = False
    steps: List[Dict[str, Any]] = []
    parameters: Dict[str, Any] = {}
    execution_count: int = 0
    success_rate: float = 0.0
    avg_execution_time_ms: int = 0
    created_at: datetime
    updated_at: datetime


class RunbookExecution(BaseModel):
    """Single runbook execution record."""
    id: int
    runbook_id: str
    site_id: str
    hostname: str
    incident_id: Optional[int] = None
    success: bool
    execution_time_ms: int
    output: Optional[str] = None
    error: Optional[str] = None
    executed_at: datetime


# =============================================================================
# LEARNING LOOP MODELS
# =============================================================================

class LearningStatus(BaseModel):
    """Overall learning loop status."""
    total_l1_rules: int
    total_l2_decisions_30d: int
    patterns_awaiting_promotion: int
    recently_promoted_count: int
    promotion_success_rate: float
    l1_resolution_rate: float
    l2_resolution_rate: float


class PromotionCandidate(BaseModel):
    """Pattern that is a candidate for L1 promotion."""
    id: str
    pattern_signature: str
    description: str
    occurrences: int
    success_rate: float
    avg_resolution_time_ms: int
    proposed_rule: str
    first_seen: datetime
    last_seen: datetime


class PromotionHistory(BaseModel):
    """Recently promoted L2→L1 rule."""
    id: int
    pattern_signature: str
    rule_id: str
    promoted_at: datetime
    post_promotion_success_rate: float
    executions_since_promotion: int


# =============================================================================
# ONBOARDING MODELS
# =============================================================================

class ComplianceChecks(BaseModel):
    """Compliance check results for onboarding."""
    patching: Optional[bool] = None
    antivirus: Optional[bool] = None
    backup: Optional[bool] = None
    logging: Optional[bool] = None
    firewall: Optional[bool] = None
    encryption: Optional[bool] = None


class OnboardingClient(BaseModel):
    """Client in the onboarding pipeline."""
    id: int
    name: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    stage: OnboardingStage
    stage_entered_at: datetime
    days_in_stage: int = 0
    blockers: List[str] = []
    notes: Optional[str] = None

    # Phase 1: Acquisition timestamps
    lead_at: Optional[datetime] = None
    discovery_at: Optional[datetime] = None
    proposal_at: Optional[datetime] = None
    contract_at: Optional[datetime] = None
    intake_at: Optional[datetime] = None
    creds_at: Optional[datetime] = None
    shipped_at: Optional[datetime] = None

    # Phase 2: Activation timestamps
    received_at: Optional[datetime] = None
    connectivity_at: Optional[datetime] = None
    scanning_at: Optional[datetime] = None
    baseline_at: Optional[datetime] = None
    compliant_at: Optional[datetime] = None
    active_at: Optional[datetime] = None

    # Tracking info
    tracking_number: Optional[str] = None
    tracking_carrier: Optional[str] = None
    appliance_serial: Optional[str] = None
    site_id: Optional[str] = None

    # Activation metrics
    checkin_status: Optional[CheckinStatus] = None
    last_checkin: Optional[datetime] = None
    assets_discovered: Optional[int] = None
    compliance_checks: Optional[ComplianceChecks] = None
    compliance_score: Optional[int] = None

    # Progress
    progress_percent: int = 0
    phase: int = 1
    phase_progress: int = 0

    created_at: datetime


class OnboardingMetrics(BaseModel):
    """Aggregate onboarding pipeline metrics."""
    total_prospects: int

    # Phase 1: Acquisition counts
    acquisition: Dict[str, int] = {
        "lead": 0,
        "discovery": 0,
        "proposal": 0,
        "contract": 0,
        "intake": 0,
        "creds": 0,
        "shipped": 0,
    }

    # Phase 2: Activation counts
    activation: Dict[str, int] = {
        "received": 0,
        "connectivity": 0,
        "scanning": 0,
        "baseline": 0,
        "compliant": 0,
        "active": 0,
    }

    avg_days_to_ship: float = 0.0
    avg_days_to_active: float = 0.0
    stalled_count: int = 0
    at_risk_count: int = 0
    connectivity_issues: int = 0


class ProspectCreate(BaseModel):
    """Create new prospect request."""
    name: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    notes: Optional[str] = None


class StageAdvance(BaseModel):
    """Advance onboarding stage request."""
    new_stage: OnboardingStage
    notes: Optional[str] = None


class BlockerUpdate(BaseModel):
    """Update blockers request."""
    blockers: List[str]


class NoteAdd(BaseModel):
    """Add note request."""
    note: str


# =============================================================================
# STATS MODELS
# =============================================================================

class GlobalStats(BaseModel):
    """Aggregate statistics across all clients."""
    total_clients: int
    total_appliances: int
    online_appliances: int
    avg_compliance_score: float
    avg_connectivity_score: float
    incidents_24h: int
    incidents_7d: int
    incidents_30d: int
    l1_resolution_rate: float
    l2_resolution_rate: float
    l3_escalation_rate: float


class ClientStats(BaseModel):
    """Statistics for a specific client."""
    site_id: str
    appliance_count: int
    online_count: int
    compliance_score: float
    connectivity_score: float
    incidents_24h: int
    incidents_7d: int
    incidents_30d: int
    l1_resolution_count: int
    l2_resolution_count: int
    l3_escalation_count: int


# =============================================================================
# COMMAND INTERFACE
# =============================================================================

class CommandRequest(BaseModel):
    """Command interface request."""
    command: str


class CommandResponse(BaseModel):
    """Command interface response."""
    command: str
    command_type: str
    success: bool
    data: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
    error: Optional[str] = None


# =============================================================================
# L2 LLM PLANNER MODELS
# =============================================================================

class L2TestRequest(BaseModel):
    """Request to test L2 LLM planner."""
    incident_type: str = Field(..., description="Type of incident (e.g., 'backup_failure')")
    severity: str = Field(default="medium", description="Incident severity")
    check_type: Optional[str] = Field(default=None, description="Check type (e.g., 'backup')")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Additional incident details")


class L2DecisionResponse(BaseModel):
    """Response from L2 LLM planner."""
    runbook_id: Optional[str] = Field(None, description="Recommended runbook ID")
    reasoning: str = Field(..., description="LLM reasoning for the decision")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0-1")
    alternative_runbooks: List[str] = Field(default_factory=list)
    requires_human_review: bool = Field(default=False)
    pattern_signature: str = Field(default="")
    llm_model: str = Field(..., description="Model used for analysis")
    llm_latency_ms: int = Field(..., description="LLM response latency")
    error: Optional[str] = Field(default=None, description="Error if any")


class L2ConfigResponse(BaseModel):
    """L2 planner configuration status."""
    enabled: bool
    provider: Optional[str]
    model: str
    timeout_seconds: int
    max_tokens: int
    temperature: float
    runbooks_available: int


# Forward reference resolution
ClientDetail.model_rebuild()
