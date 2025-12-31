"""FastAPI routes for Central Command Dashboard.

All endpoints for the dashboard including fleet, incidents, runbooks,
learning loop, onboarding, stats, and command interface.

This module uses the central database when available, falling back
to mock data for demo/development purposes.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, Query, HTTPException, Request

from .models import (
    ClientOverview,
    ClientDetail,
    Appliance,
    Incident,
    IncidentDetail,
    Runbook,
    RunbookDetail,
    RunbookExecution,
    LearningStatus,
    PromotionCandidate,
    PromotionHistory,
    OnboardingClient,
    OnboardingMetrics,
    OnboardingStage,
    ProspectCreate,
    StageAdvance,
    BlockerUpdate,
    NoteAdd,
    GlobalStats,
    ClientStats,
    CommandRequest,
    CommandResponse,
    CheckType,
    Severity,
    ResolutionLevel,
    HealthStatus,
    CheckinStatus,
    ComplianceChecks,
    L2TestRequest,
    L2DecisionResponse,
    L2ConfigResponse,
)
from .fleet import get_mock_fleet_overview, get_mock_client_detail
from .metrics import calculate_health_from_raw

# Try to import the database store
try:
    import sys
    import os
    # Add parent directory to path for database import
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from database import get_store
    HAS_DATABASE = True
except ImportError:
    HAS_DATABASE = False
    get_store = None


def _get_db():
    """Get database store if available."""
    if HAS_DATABASE and get_store:
        try:
            return get_store()
        except Exception:
            return None
    return None


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# =============================================================================
# FLEET ENDPOINTS
# =============================================================================

@router.get("/fleet", response_model=List[ClientOverview])
async def get_fleet_overview():
    """Get all clients with aggregated health scores.

    Returns:
        List of clients with health metrics.
    """
    store = _get_db()
    if store:
        clients = store.get_all_clients()
        if clients:
            return [
                ClientOverview(
                    site_id=c.site_id,
                    name=c.name,
                    appliance_count=len(store.get_client_appliances(c.site_id)),
                    overall_health=c.overall_health,
                    connectivity=c.connectivity_score,
                    compliance=c.compliance_score,
                    status=HealthStatus(c.health_status) if c.health_status else HealthStatus.HEALTHY,
                    last_seen=c.last_seen,
                )
                for c in clients
            ]

    # No clients in database - return empty list (production mode)
    return []


@router.get("/fleet/{site_id}", response_model=ClientDetail)
async def get_client_detail(site_id: str):
    """Get detailed view of a single client.

    Returns:
        Client info, appliances, recent incidents, compliance breakdown.
    """
    store = _get_db()
    if store:
        client = store.get_client(site_id)
        if client:
            appliances = store.get_client_appliances(site_id)
            incidents = store.get_incidents(site_id=site_id, limit=10)

            return ClientDetail(
                site_id=client.site_id,
                name=client.name,
                appliance_count=len(appliances),
                overall_health=client.overall_health,
                connectivity=client.connectivity_score,
                compliance=client.compliance_score,
                status=HealthStatus(client.health_status) if client.health_status else HealthStatus.HEALTHY,
                last_seen=client.last_seen,
                appliances=[
                    Appliance(
                        id=a.id,
                        hostname=a.hostname,
                        ip_address=a.ip_address or "",
                        is_online=a.is_online,
                        overall_health=a.overall_health,
                        checkin_rate=a.checkin_rate,
                        healing_rate=a.healing_rate,
                        order_execution_rate=a.order_execution_rate,
                        last_checkin=a.last_checkin,
                        version=a.version or "1.0.0",
                    )
                    for a in appliances
                ],
                compliance_checks=ComplianceChecks(
                    backup=True,
                    patching=True,
                    antivirus=True,
                    firewall=True,
                    encryption=True,
                    logging=True,
                ),
                incidents=[
                    Incident(
                        id=i.id,
                        site_id=i.site_id,
                        hostname=i.hostname,
                        check_type=CheckType(i.check_type) if i.check_type else CheckType.BACKUP,
                        severity=Severity(i.severity),
                        resolution_level=ResolutionLevel(i.resolution_level) if i.resolution_level else None,
                        resolved=i.resolved,
                        resolved_at=i.resolved_at,
                        hipaa_controls=i.hipaa_controls or [],
                        created_at=i.created_at,
                    )
                    for i in incidents
                ],
            )

    # Client not found (production mode)
    raise HTTPException(status_code=404, detail=f"Client {site_id} not found")


@router.get("/fleet/{site_id}/appliances", response_model=List[Appliance])
async def get_client_appliances(site_id: str):
    """Get all appliances for a client.

    Returns:
        List of appliances with individual health scores.
    """
    store = _get_db()
    if store:
        appliances = store.get_client_appliances(site_id)
        if appliances:
            return [
                Appliance(
                    id=a.id,
                    hostname=a.hostname,
                    ip_address=a.ip_address or "",
                    is_online=a.is_online,
                    overall_health=a.overall_health,
                    checkin_rate=a.checkin_rate,
                    healing_rate=a.healing_rate,
                    order_execution_rate=a.order_execution_rate,
                    last_checkin=a.last_checkin,
                    version=a.version or "1.0.0",
                )
                for a in appliances
            ]

    # No appliances found (production mode)
    return []


# =============================================================================
# INCIDENT ENDPOINTS
# =============================================================================

@router.get("/incidents", response_model=List[Incident])
async def get_incidents(
    site_id: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    level: Optional[str] = None,
    resolved: Optional[bool] = None,
):
    """Get recent incidents, optionally filtered.

    Args:
        site_id: Filter by client site
        limit: Maximum number of results
        level: Filter by resolution level (L1, L2, L3)
        resolved: Filter by resolution status

    Returns:
        List of incidents with resolution details.
    """
    store = _get_db()
    if store:
        incidents = store.get_incidents(
            site_id=site_id,
            limit=limit,
            level=level,
            resolved=resolved,
        )
        if incidents:
            return [
                Incident(
                    id=i.id,
                    site_id=i.site_id,
                    hostname=i.hostname,
                    check_type=CheckType(i.check_type) if i.check_type else CheckType.BACKUP,
                    severity=Severity(i.severity),
                    resolution_level=ResolutionLevel(i.resolution_level) if i.resolution_level else None,
                    resolved=i.resolved,
                    resolved_at=i.resolved_at,
                    hipaa_controls=i.hipaa_controls or [],
                    created_at=i.created_at,
                )
                for i in incidents
            ]

    # No incidents - return empty list (production mode)
    return []


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
async def get_incident_detail(incident_id: int):
    """Get full incident detail including evidence bundle."""
    store = _get_db()
    if store:
        incident = store.get_incident(incident_id)
        if incident:
            return IncidentDetail(
                id=incident.id,
                site_id=incident.site_id,
                appliance_id=incident.appliance_id,
                hostname=incident.hostname,
                check_type=CheckType(incident.check_type) if incident.check_type else CheckType.BACKUP,
                severity=Severity(incident.severity),
                drift_data=incident.drift_data or {},
                resolution_level=ResolutionLevel(incident.resolution_level) if incident.resolution_level else None,
                resolved=incident.resolved,
                resolved_at=incident.resolved_at,
                hipaa_controls=incident.hipaa_controls or [],
                evidence_bundle_id=incident.evidence_bundle_id,
                evidence_hash=incident.evidence_hash,
                runbook_executed=incident.runbook_executed,
                execution_log=incident.execution_log,
                created_at=incident.created_at,
            )

    raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")


# =============================================================================
# RUNBOOK ENDPOINTS
# =============================================================================

@router.get("/runbooks", response_model=List[Runbook])
async def get_runbooks():
    """Get all runbooks in the library.

    Returns:
        List of runbooks with HIPAA mappings, execution stats.
    """
    return [
        Runbook(
            id="RB-WIN-PATCH-001",
            name="Windows Patch Compliance",
            description="Verify and apply Windows security patches",
            level=ResolutionLevel.L1,
            hipaa_controls=["164.308(a)(5)(ii)(B)"],
            is_disruptive=True,
            execution_count=234,
            success_rate=98.2,
            avg_execution_time_ms=45000,
        ),
        Runbook(
            id="RB-WIN-AV-001",
            name="Windows Defender Health",
            description="Verify Defender status and update signatures",
            level=ResolutionLevel.L1,
            hipaa_controls=["164.308(a)(5)(ii)(B)", "164.312(b)"],
            is_disruptive=False,
            execution_count=156,
            success_rate=99.4,
            avg_execution_time_ms=8000,
        ),
        Runbook(
            id="RB-WIN-BACKUP-001",
            name="Backup Status Verification",
            description="Verify backup job completion and restore capability",
            level=ResolutionLevel.L1,
            hipaa_controls=["164.308(a)(7)", "164.308(a)(7)(ii)(A)"],
            is_disruptive=False,
            execution_count=312,
            success_rate=95.8,
            avg_execution_time_ms=12000,
        ),
        Runbook(
            id="RB-WIN-LOGGING-001",
            name="Audit Logging Compliance",
            description="Verify Windows audit policy and log collection",
            level=ResolutionLevel.L1,
            hipaa_controls=["164.312(b)"],
            is_disruptive=False,
            execution_count=189,
            success_rate=97.3,
            avg_execution_time_ms=5000,
        ),
        Runbook(
            id="RB-WIN-FIREWALL-001",
            name="Windows Firewall Compliance",
            description="Verify firewall status and rule compliance",
            level=ResolutionLevel.L1,
            hipaa_controls=["164.312(e)(1)"],
            is_disruptive=False,
            execution_count=145,
            success_rate=99.1,
            avg_execution_time_ms=3000,
        ),
        Runbook(
            id="RB-WIN-ENCRYPTION-001",
            name="BitLocker Encryption",
            description="Verify BitLocker status and recovery key backup",
            level=ResolutionLevel.L1,
            hipaa_controls=["164.312(a)(2)(iv)", "164.312(e)(2)(ii)"],
            is_disruptive=True,
            execution_count=87,
            success_rate=96.5,
            avg_execution_time_ms=120000,
        ),
        Runbook(
            id="RB-WIN-AD-001",
            name="Active Directory Health",
            description="Verify AD health, password policies, and account security",
            level=ResolutionLevel.L1,
            hipaa_controls=["164.312(a)(1)", "164.312(d)"],
            is_disruptive=False,
            execution_count=201,
            success_rate=98.0,
            avg_execution_time_ms=15000,
        ),
    ]


@router.get("/runbooks/{runbook_id}", response_model=RunbookDetail)
async def get_runbook_detail(runbook_id: str):
    """Get runbook detail including steps, params, execution history."""
    now = datetime.now(timezone.utc)

    if runbook_id == "RB-WIN-PATCH-001":
        return RunbookDetail(
            id="RB-WIN-PATCH-001",
            name="Windows Patch Compliance",
            description="Verify and apply Windows security patches",
            level=ResolutionLevel.L1,
            hipaa_controls=["164.308(a)(5)(ii)(B)"],
            is_disruptive=True,
            steps=[
                {"order": 1, "action": "check_pending_patches", "timeout_seconds": 60},
                {"order": 2, "action": "download_patches", "timeout_seconds": 300},
                {"order": 3, "action": "install_patches", "timeout_seconds": 600},
                {"order": 4, "action": "verify_installation", "timeout_seconds": 60},
            ],
            parameters={
                "reboot_if_required": True,
                "maintenance_window": "02:00-06:00",
                "critical_only": False,
            },
            execution_count=234,
            success_rate=98.2,
            avg_execution_time_ms=45000,
            created_at=now - timedelta(days=180),
            updated_at=now - timedelta(days=7),
        )

    raise HTTPException(status_code=404, detail=f"Runbook {runbook_id} not found")


@router.get("/runbooks/{runbook_id}/executions", response_model=List[RunbookExecution])
async def get_runbook_executions(
    runbook_id: str,
    limit: int = Query(default=20, le=100),
):
    """Get recent executions of a specific runbook."""
    now = datetime.now(timezone.utc)

    if runbook_id == "RB-WIN-PATCH-001":
        return [
            RunbookExecution(
                id=5001,
                runbook_id=runbook_id,
                site_id="north-valley-family-practice",
                hostname="NVFP-DC01",
                incident_id=1001,
                success=True,
                execution_time_ms=42000,
                output="Installed 3 patches successfully",
                executed_at=now - timedelta(hours=4),
            ),
            RunbookExecution(
                id=5002,
                runbook_id=runbook_id,
                site_id="cedar-medical-group",
                hostname="CMG-DC01",
                incident_id=1002,
                success=True,
                execution_time_ms=48000,
                output="Installed 5 patches successfully",
                executed_at=now - timedelta(hours=12),
            ),
        ][:limit]

    return []


# =============================================================================
# LEARNING LOOP ENDPOINTS
# =============================================================================

@router.get("/learning/status", response_model=LearningStatus)
async def get_learning_status():
    """Get current state of the L2->L1 learning loop.

    Returns:
        Total L1 rules, L2 decisions, patterns awaiting promotion, etc.
    """
    store = _get_db()
    if store:
        status = store.get_learning_status()
        stats = store.get_global_stats()
        return LearningStatus(
            total_l1_rules=status.get("total_l1_rules", 0),
            total_l2_decisions_30d=status.get("total_l2_decisions_30d", 0),
            patterns_awaiting_promotion=status.get("patterns_awaiting_promotion", 0),
            recently_promoted_count=status.get("recently_promoted_count", 0),
            promotion_success_rate=status.get("promotion_success_rate", 0.0),
            l1_resolution_rate=stats.get("l1_resolution_rate", 0.0),
            l2_resolution_rate=stats.get("l2_resolution_rate", 0.0),
        )

    # Fallback to mock data
    return LearningStatus(
        total_l1_rules=47,
        total_l2_decisions_30d=234,
        patterns_awaiting_promotion=3,
        recently_promoted_count=5,
        promotion_success_rate=94.0,
        l1_resolution_rate=78.0,
        l2_resolution_rate=18.0,
    )


@router.get("/learning/candidates", response_model=List[PromotionCandidate])
async def get_promotion_candidates():
    """Get patterns that are candidates for L1 promotion.

    Criteria: 5+ occurrences, 90%+ success rate.
    """
    store = _get_db()
    if store:
        candidates = store.get_promotion_candidates()
        if candidates:
            return candidates

    # No candidates - return empty list (production mode)
    return []


@router.get("/learning/history", response_model=List[PromotionHistory])
async def get_promotion_history(limit: int = Query(default=20, le=100)):
    """Get recently promoted L2->L1 rules."""
    store = _get_db()
    if store:
        history = store.get_promotion_history(limit=limit)
        if history:
            return history

    # No history - return empty list (production mode)
    return []


@router.post("/learning/promote/{pattern_id}")
async def promote_pattern(pattern_id: str):
    """Manually trigger promotion of a pattern to L1.

    Requires: Human review confirmation.
    """
    store = _get_db()
    if store:
        result = store.promote_pattern(pattern_id)
        if result:
            return result

    raise HTTPException(status_code=404, detail=f"Pattern {pattern_id} not found")


# =============================================================================
# L2 LLM PLANNER ENDPOINTS
# =============================================================================

# Try to import the L2 planner
try:
    from .l2_planner import (
        analyze_incident,
        get_l2_config,
        is_l2_available,
        AVAILABLE_RUNBOOKS,
    )
    HAS_L2_PLANNER = True
except ImportError:
    HAS_L2_PLANNER = False
    analyze_incident = None
    get_l2_config = None
    is_l2_available = None
    AVAILABLE_RUNBOOKS = {}


@router.get("/l2/config", response_model=L2ConfigResponse)
async def get_l2_configuration():
    """Get L2 LLM planner configuration and status.

    Returns:
        Current L2 configuration including enabled status, provider, model.
    """
    if not HAS_L2_PLANNER or not get_l2_config:
        return L2ConfigResponse(
            enabled=False,
            provider=None,
            model="not configured",
            timeout_seconds=30,
            max_tokens=1024,
            temperature=0.1,
            runbooks_available=0,
        )

    config = get_l2_config()
    return L2ConfigResponse(**config)


@router.get("/l2/runbooks")
async def get_l2_runbooks():
    """Get available runbooks for L2 selection.

    Returns:
        Dict of runbook_id -> runbook details.
    """
    if not HAS_L2_PLANNER:
        return {"runbooks": {}, "count": 0}

    return {
        "runbooks": AVAILABLE_RUNBOOKS,
        "count": len(AVAILABLE_RUNBOOKS),
    }


@router.post("/l2/test", response_model=L2DecisionResponse)
async def test_l2_planner(request: L2TestRequest):
    """Test the L2 LLM planner with a sample incident.

    This endpoint allows testing the LLM connection and runbook selection
    without creating a real incident.

    Args:
        request: Incident details to analyze

    Returns:
        L2 decision including runbook recommendation, reasoning, confidence.
    """
    if not HAS_L2_PLANNER or not analyze_incident:
        return L2DecisionResponse(
            runbook_id=None,
            reasoning="L2 planner not available - module not loaded",
            confidence=0.0,
            alternative_runbooks=[],
            requires_human_review=True,
            pattern_signature="",
            llm_model="none",
            llm_latency_ms=0,
            error="L2 planner not configured",
        )

    if not is_l2_available():
        return L2DecisionResponse(
            runbook_id=None,
            reasoning="No LLM API key configured (OPENAI_API_KEY or ANTHROPIC_API_KEY)",
            confidence=0.0,
            alternative_runbooks=[],
            requires_human_review=True,
            pattern_signature="",
            llm_model="none",
            llm_latency_ms=0,
            error="No LLM API key configured",
        )

    # Call the L2 planner
    decision = await analyze_incident(
        incident_type=request.incident_type,
        severity=request.severity,
        check_type=request.check_type,
        details=request.details or {},
        pre_state={},
        hipaa_controls=None,
    )

    return L2DecisionResponse(
        runbook_id=decision.runbook_id,
        reasoning=decision.reasoning,
        confidence=decision.confidence,
        alternative_runbooks=decision.alternative_runbooks,
        requires_human_review=decision.requires_human_review,
        pattern_signature=decision.pattern_signature,
        llm_model=decision.llm_model,
        llm_latency_ms=decision.llm_latency_ms,
        error=decision.error,
    )


# =============================================================================
# ONBOARDING PIPELINE ENDPOINTS
# =============================================================================

@router.get("/onboarding", response_model=List[OnboardingClient])
async def get_onboarding_pipeline():
    """Get all prospects in the onboarding pipeline.

    Returns:
        List of clients with stage, progress, blockers.
    """
    store = _get_db()
    if store:
        prospects = store.get_onboarding_prospects()
        if prospects:
            return prospects

    # No prospects in database - return empty list (production mode)
    return []


@router.get("/onboarding/metrics", response_model=OnboardingMetrics)
async def get_onboarding_metrics():
    """Get aggregate pipeline metrics.

    Returns:
        Counts by stage, avg time to deploy, at-risk clients.
    """
    store = _get_db()
    if store:
        metrics = store.get_onboarding_metrics()
        if metrics:
            return OnboardingMetrics(**metrics)

    # No data - return empty metrics (production mode)
    return OnboardingMetrics(
        total_prospects=0,
        acquisition={
            "lead": 0,
            "discovery": 0,
            "proposal": 0,
            "contract": 0,
            "intake": 0,
            "creds": 0,
            "shipped": 0,
        },
        activation={
            "received": 0,
            "connectivity": 0,
            "scanning": 0,
            "baseline": 0,
            "compliant": 0,
            "active": 0,
        },
        avg_days_to_ship=0.0,
        avg_days_to_active=0.0,
        stalled_count=0,
        at_risk_count=0,
        connectivity_issues=0,
    )


@router.get("/onboarding/{client_id}", response_model=OnboardingClient)
async def get_onboarding_detail(client_id: int):
    """Get detailed onboarding status for a single client."""
    clients = await get_onboarding_pipeline()
    for client in clients:
        if client.id == client_id:
            return client
    raise HTTPException(status_code=404, detail=f"Client {client_id} not found")


@router.post("/onboarding", response_model=OnboardingClient)
async def create_prospect(prospect: ProspectCreate):
    """Create new prospect (Lead stage)."""
    now = datetime.now(timezone.utc)

    return OnboardingClient(
        id=100,  # Would be from DB
        name=prospect.name,
        contact_name=prospect.contact_name,
        contact_email=prospect.contact_email,
        contact_phone=prospect.contact_phone,
        stage=OnboardingStage.LEAD,
        stage_entered_at=now,
        days_in_stage=0,
        notes=prospect.notes,
        lead_at=now,
        progress_percent=8,
        phase=1,
        phase_progress=14,
        created_at=now,
    )


@router.patch("/onboarding/{client_id}/stage")
async def advance_stage(client_id: int, request: StageAdvance):
    """Move client to next stage."""
    return {
        "status": "advanced",
        "client_id": client_id,
        "new_stage": request.new_stage,
        "notes": request.notes,
    }


@router.patch("/onboarding/{client_id}/blockers")
async def update_blockers(client_id: int, request: BlockerUpdate):
    """Update blockers for a client."""
    return {
        "status": "updated",
        "client_id": client_id,
        "blockers": request.blockers,
    }


@router.post("/onboarding/{client_id}/note")
async def add_note(client_id: int, request: NoteAdd):
    """Add a note to client's onboarding record."""
    return {
        "status": "added",
        "client_id": client_id,
        "note": request.note,
    }


# =============================================================================
# STATS ENDPOINTS
# =============================================================================

@router.get("/stats", response_model=GlobalStats)
async def get_global_stats():
    """Get aggregate statistics across all clients."""
    store = _get_db()
    if store:
        stats = store.get_global_stats()
        return GlobalStats(
            total_clients=stats.get("total_clients", 0),
            total_appliances=stats.get("total_appliances", 0),
            online_appliances=stats.get("online_appliances", 0),
            avg_compliance_score=stats.get("avg_compliance_score", 0.0),
            avg_connectivity_score=stats.get("avg_connectivity_score", 0.0),
            incidents_24h=stats.get("incidents_24h", 0),
            incidents_7d=stats.get("incidents_7d", 0),
            incidents_30d=stats.get("incidents_30d", 0),
            l1_resolution_rate=stats.get("l1_resolution_rate", 0.0),
            l2_resolution_rate=stats.get("l2_resolution_rate", 0.0),
            l3_escalation_rate=stats.get("l3_escalation_rate", 0.0),
        )

    # Fallback to mock data
    return GlobalStats(
        total_clients=3,
        total_appliances=6,
        online_appliances=5,
        avg_compliance_score=82.0,
        avg_connectivity_score=95.0,
        incidents_24h=12,
        incidents_7d=45,
        incidents_30d=156,
        l1_resolution_rate=78.0,
        l2_resolution_rate=18.0,
        l3_escalation_rate=4.0,
    )


@router.get("/stats/{site_id}", response_model=ClientStats)
async def get_client_stats(site_id: str):
    """Get statistics for a specific client."""
    store = _get_db()
    if store:
        client = store.get_client(site_id)
        if client:
            appliances = store.get_client_appliances(site_id)
            incidents = store.get_incidents(site_id=site_id)

            # Count by resolution level
            l1_count = sum(1 for i in incidents if i.resolution_level == "L1")
            l2_count = sum(1 for i in incidents if i.resolution_level == "L2")
            l3_count = sum(1 for i in incidents if i.resolution_level == "L3")

            from datetime import timedelta
            now = datetime.now(timezone.utc)
            incidents_24h = sum(1 for i in incidents if i.created_at and i.created_at >= now - timedelta(hours=24))
            incidents_7d = sum(1 for i in incidents if i.created_at and i.created_at >= now - timedelta(days=7))

            return ClientStats(
                site_id=site_id,
                appliance_count=len(appliances),
                online_count=sum(1 for a in appliances if a.is_online),
                compliance_score=client.compliance_score,
                connectivity_score=client.connectivity_score,
                incidents_24h=incidents_24h,
                incidents_7d=incidents_7d,
                incidents_30d=len(incidents),
                l1_resolution_count=l1_count,
                l2_resolution_count=l2_count,
                l3_escalation_count=l3_count,
            )

    # Fallback to mock data for known clients
    if site_id == "north-valley-family-practice":
        return ClientStats(
            site_id=site_id,
            appliance_count=2,
            online_count=2,
            compliance_score=92.0,
            connectivity_score=98.0,
            incidents_24h=3,
            incidents_7d=12,
            incidents_30d=45,
            l1_resolution_count=35,
            l2_resolution_count=8,
            l3_escalation_count=2,
        )

    raise HTTPException(status_code=404, detail=f"Client {site_id} not found")


# =============================================================================
# COMMAND INTERFACE
# =============================================================================

@router.post("/command", response_model=CommandResponse)
async def execute_command(request: CommandRequest):
    """Process natural language or structured command.

    Supported commands:
      - "status all" -> Fleet overview
      - "status <site_id>" -> Client detail
      - "incidents <site_id>" -> Recent incidents for client
      - "compliance <site_id>" -> Compliance breakdown
      - "runbook <runbook_id>" -> Runbook detail
      - "learning status" -> Learning loop status

    Returns:
        Structured response based on command type.
    """
    command = request.command.strip().lower()

    # Parse command
    if command == "status all":
        fleet = get_mock_fleet_overview()
        return CommandResponse(
            command=request.command,
            command_type="fleet_overview",
            success=True,
            data={
                "clients": [c.model_dump() for c in fleet],
                "total_clients": len(fleet),
            },
        )

    if command.startswith("status "):
        site_id = command.replace("status ", "").strip()
        detail = get_mock_client_detail(site_id)
        if detail:
            return CommandResponse(
                command=request.command,
                command_type="client_detail",
                success=True,
                data=detail.model_dump(),
            )
        return CommandResponse(
            command=request.command,
            command_type="client_detail",
            success=False,
            error=f"Client {site_id} not found",
        )

    if command == "learning status":
        status = await get_learning_status()
        return CommandResponse(
            command=request.command,
            command_type="learning_status",
            success=True,
            data=status.model_dump(),
        )

    if command.startswith("incidents "):
        site_id = command.replace("incidents ", "").strip()
        incidents = await get_incidents(site_id=site_id, limit=10)
        return CommandResponse(
            command=request.command,
            command_type="incidents",
            success=True,
            data={
                "site_id": site_id,
                "incidents": [i.model_dump() for i in incidents],
            },
        )

    if command.startswith("runbook "):
        runbook_id = command.replace("runbook ", "").strip().upper()
        try:
            runbook = await get_runbook_detail(runbook_id)
            return CommandResponse(
                command=request.command,
                command_type="runbook_detail",
                success=True,
                data=runbook.model_dump(),
            )
        except HTTPException:
            return CommandResponse(
                command=request.command,
                command_type="runbook_detail",
                success=False,
                error=f"Runbook {runbook_id} not found",
            )

    # Unknown command
    return CommandResponse(
        command=request.command,
        command_type="unknown",
        success=False,
        error="Unknown command. Try: status all, status <site_id>, incidents <site_id>, runbook <id>, learning status",
    )
