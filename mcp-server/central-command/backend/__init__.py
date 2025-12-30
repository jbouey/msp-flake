"""Central Command Dashboard Backend

Provides API endpoints for the Central Command dashboard including:
- Fleet overview and health metrics
- Incident tracking and resolution
- Runbook library management
- Learning loop (L2→L1 promotion) status
- Onboarding pipeline tracking
- Command interface
"""

from .models import (
    HealthMetrics,
    ConnectivityMetrics,
    ComplianceMetrics,
    ClientOverview,
    ClientDetail,
    Appliance,
    Incident,
    IncidentDetail,
    Runbook,
    RunbookExecution,
    LearningStatus,
    PromotionCandidate,
    PromotionHistory,
    OnboardingClient,
    OnboardingMetrics,
    GlobalStats,
    CommandResponse,
)

from .metrics import (
    calculate_checkin_freshness,
    calculate_connectivity_score,
    calculate_compliance_score,
    calculate_overall_health,
    get_health_status,
)

__all__ = [
    # Models
    "HealthMetrics",
    "ConnectivityMetrics",
    "ComplianceMetrics",
    "ClientOverview",
    "ClientDetail",
    "Appliance",
    "Incident",
    "IncidentDetail",
    "Runbook",
    "RunbookExecution",
    "LearningStatus",
    "PromotionCandidate",
    "PromotionHistory",
    "OnboardingClient",
    "OnboardingMetrics",
    "GlobalStats",
    "CommandResponse",
    # Metrics functions
    "calculate_checkin_freshness",
    "calculate_connectivity_score",
    "calculate_compliance_score",
    "calculate_overall_health",
    "get_health_status",
]
