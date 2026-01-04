"""FastAPI routes for Central Command Dashboard.

All endpoints for the dashboard including fleet, incidents, runbooks,
learning loop, onboarding, stats, and command interface.

This module uses the central database when available, falling back
to mock data for demo/development purposes.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, Query, HTTPException, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

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
from .db_queries import (
    get_incidents_from_db,
    get_learning_status_from_db,
    get_promotion_candidates_from_db,
    get_global_stats_from_db,
    get_compliance_scores_for_site,
    get_all_compliance_scores,
)


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# Database session dependency
async def get_db():
    from main import async_session
    async with async_session() as session:
        yield session



# =============================================================================
# FLEET ENDPOINTS
# =============================================================================

@router.get("/fleet")
async def get_fleet_overview(db: AsyncSession = Depends(get_db)):
    """Get all clients with aggregated health scores."""
    # Get site data
    query = text("""
        SELECT
            s.site_id,
            s.clinic_name as name,
            s.status,
            COUNT(sa.id) as appliance_count,
            COUNT(sa.id) FILTER (WHERE sa.status = 'online') as online_count,
            MAX(sa.last_checkin) as last_checkin
        FROM sites s
        LEFT JOIN site_appliances sa ON sa.site_id = s.site_id
        GROUP BY s.site_id, s.clinic_name, s.status
        ORDER BY MAX(sa.last_checkin) DESC NULLS LAST
    """)

    result = await db.execute(query)
    rows = result.fetchall()

    # Get compliance scores for all sites
    all_compliance = await get_all_compliance_scores(db)

    clients = []
    for row in rows:
        # Calculate connectivity score based on last checkin
        checkin_freshness = 0
        if row.last_checkin:
            time_since = datetime.now(timezone.utc) - row.last_checkin
            if time_since < timedelta(minutes=5):
                checkin_freshness = 100
            elif time_since < timedelta(minutes=15):
                checkin_freshness = 80
            elif time_since < timedelta(hours=1):
                checkin_freshness = 50
            else:
                checkin_freshness = 20

        connectivity_score = float(checkin_freshness)

        # Get real compliance scores for this site
        site_compliance = all_compliance.get(row.site_id, {})
        if site_compliance.get("has_data"):
            patching = site_compliance.get("patching") or 0
            antivirus = site_compliance.get("antivirus") or 0
            backup = site_compliance.get("backup") or 0
            logging = site_compliance.get("logging") or 0
            firewall = site_compliance.get("firewall") or 0
            encryption = site_compliance.get("encryption") or 0
            compliance_score = site_compliance.get("score") or 0.0
        else:
            # No compliance data yet - show as unknown/pending
            patching = 0
            antivirus = 0
            backup = 0
            logging = 0
            firewall = 0
            encryption = 0
            compliance_score = 0.0

        overall = connectivity_score * 0.4 + compliance_score * 0.6

        # Determine status based on connectivity and compliance
        if checkin_freshness >= 80 and compliance_score >= 70:
            status = "healthy"
        elif checkin_freshness >= 50 or compliance_score >= 50:
            status = "warning"
        else:
            status = "critical"

        clients.append(ClientOverview(
            site_id=row.site_id,
            name=row.name or row.site_id,
            appliance_count=row.appliance_count or 0,
            online_count=row.online_count or 0,
            health=HealthMetrics(
                connectivity=ConnectivityMetrics(
                    checkin_freshness=checkin_freshness,
                    healing_success_rate=95.0,
                    order_execution_rate=98.0,
                    score=connectivity_score
                ),
                compliance=ComplianceMetrics(
                    patching=patching,
                    antivirus=antivirus,
                    backup=backup,
                    logging=logging,
                    firewall=firewall,
                    encryption=encryption,
                    score=compliance_score
                ),
                overall=overall,
                status=HealthStatus(status)
            ),
            last_incident=None,
            incidents_24h=0
        ))

    return clients


@router.get("/fleet/{site_id}", response_model=ClientDetail)
async def get_client_detail(site_id: str, db: AsyncSession = Depends(get_db)):
    """Get detailed view of a single client."""
    # Get site info
    site_result = await db.execute(
        text("SELECT site_id, clinic_name, tier FROM sites WHERE site_id = :site_id"),
        {"site_id": site_id}
    )
    site = site_result.fetchone()

    if not site:
        raise HTTPException(status_code=404, detail=f"Client {site_id} not found")

    # Get real compliance scores for this site
    site_compliance = await get_compliance_scores_for_site(db, site_id)
    if site_compliance.get("has_data"):
        patching = site_compliance.get("patching") or 0
        antivirus = site_compliance.get("antivirus") or 0
        backup = site_compliance.get("backup") or 0
        logging_score = site_compliance.get("logging") or 0
        firewall = site_compliance.get("firewall") or 0
        encryption = site_compliance.get("encryption") or 0
        compliance_score = site_compliance.get("score") or 0.0
    else:
        patching = 0
        antivirus = 0
        backup = 0
        logging_score = 0
        firewall = 0
        encryption = 0
        compliance_score = 0.0

    # Get appliances
    appliances_result = await db.execute(
        text("""
            SELECT id, appliance_id, hostname, ip_addresses, agent_version,
                   status, last_checkin, first_checkin
            FROM site_appliances
            WHERE site_id = :site_id
        """),
        {"site_id": site_id}
    )
    appliance_rows = appliances_result.fetchall()

    appliances = []
    for i, a in enumerate(appliance_rows):
        is_online = a.status == 'online'
        checkin_freshness = 0
        if a.last_checkin:
            time_since = datetime.now(timezone.utc) - a.last_checkin
            if time_since < timedelta(minutes=5):
                checkin_freshness = 100
            elif time_since < timedelta(minutes=15):
                checkin_freshness = 80
            elif time_since < timedelta(hours=1):
                checkin_freshness = 50

        ip_addr = None
        if a.ip_addresses:
            try:
                import json
                ips = json.loads(a.ip_addresses) if isinstance(a.ip_addresses, str) else a.ip_addresses
                ip_addr = ips[0] if ips else None
            except:
                pass

        appliance_overall = float(checkin_freshness) * 0.4 + compliance_score * 0.6

        appliances.append(Appliance(
            id=str(a.appliance_id or i + 1),
            site_id=site_id,
            hostname=a.hostname or "unknown",
            ip_address=ip_addr,
            agent_version=a.agent_version,
            tier=site.tier or "mid",
            is_online=is_online,
            last_checkin=a.last_checkin,
            health=HealthMetrics(
                connectivity=ConnectivityMetrics(
                    checkin_freshness=checkin_freshness,
                    healing_success_rate=95.0,
                    order_execution_rate=98.0,
                    score=float(checkin_freshness)
                ),
                compliance=ComplianceMetrics(
                    patching=patching, antivirus=antivirus, backup=backup,
                    logging=logging_score, firewall=firewall, encryption=encryption,
                    score=compliance_score
                ),
                overall=appliance_overall,
                status=HealthStatus.HEALTHY if is_online and compliance_score >= 70 else HealthStatus.WARNING
            ),
            created_at=a.first_checkin or datetime.now(timezone.utc)
        ))

    # Calculate overall health
    online_count = sum(1 for a in appliances if a.is_online)
    connectivity_score = 100.0 if online_count else 0.0
    overall = connectivity_score * 0.4 + compliance_score * 0.6

    # Determine status
    if online_count and compliance_score >= 70:
        health_status = HealthStatus.HEALTHY
    elif online_count or compliance_score >= 50:
        health_status = HealthStatus.WARNING
    else:
        health_status = HealthStatus.CRITICAL

    return ClientDetail(
        site_id=site.site_id,
        name=site.clinic_name or site_id,
        tier=site.tier or "mid",
        appliances=appliances,
        health=HealthMetrics(
            connectivity=ConnectivityMetrics(
                checkin_freshness=100 if online_count else 0,
                healing_success_rate=95.0,
                order_execution_rate=98.0,
                score=connectivity_score
            ),
            compliance=ComplianceMetrics(
                patching=patching, antivirus=antivirus, backup=backup,
                logging=logging_score, firewall=firewall, encryption=encryption,
                score=compliance_score
            ),
            overall=overall,
            status=health_status
        ),
        recent_incidents=[],
        compliance_breakdown=ComplianceMetrics(
            patching=patching, antivirus=antivirus, backup=backup,
            logging=logging_score, firewall=firewall, encryption=encryption,
            score=compliance_score
        )
    )


@router.get("/fleet/{site_id}/appliances", response_model=List[Appliance])
async def get_client_appliances(site_id: str, db: AsyncSession = Depends(get_db)):
    """Get all appliances for a client."""
    # Get real compliance scores for this site
    site_compliance = await get_compliance_scores_for_site(db, site_id)
    if site_compliance.get("has_data"):
        patching = site_compliance.get("patching") or 0
        antivirus = site_compliance.get("antivirus") or 0
        backup = site_compliance.get("backup") or 0
        logging_score = site_compliance.get("logging") or 0
        firewall = site_compliance.get("firewall") or 0
        encryption = site_compliance.get("encryption") or 0
        compliance_score = site_compliance.get("score") or 0.0
    else:
        patching = 0
        antivirus = 0
        backup = 0
        logging_score = 0
        firewall = 0
        encryption = 0
        compliance_score = 0.0

    result = await db.execute(
        text("""
            SELECT id, appliance_id, hostname, ip_addresses, agent_version,
                   status, last_checkin, first_checkin
            FROM site_appliances WHERE site_id = :site_id
        """),
        {"site_id": site_id}
    )
    rows = result.fetchall()

    appliances = []
    for i, a in enumerate(rows):
        is_online = a.status == 'online'
        checkin_freshness = 100 if is_online else 0

        ip_addr = None
        if a.ip_addresses:
            try:
                import json
                ips = json.loads(a.ip_addresses) if isinstance(a.ip_addresses, str) else a.ip_addresses
                ip_addr = ips[0] if ips else None
            except: pass

        appliance_overall = float(checkin_freshness) * 0.4 + compliance_score * 0.6

        appliances.append(Appliance(
            id=str(a.appliance_id or i + 1),
            site_id=site_id,
            hostname=a.hostname or "unknown",
            ip_address=ip_addr,
            agent_version=a.agent_version,
            tier="mid",
            is_online=is_online,
            last_checkin=a.last_checkin,
            health=HealthMetrics(
                connectivity=ConnectivityMetrics(
                    checkin_freshness=checkin_freshness,
                    healing_success_rate=95.0,
                    order_execution_rate=98.0,
                    score=float(checkin_freshness)
                ),
                compliance=ComplianceMetrics(
                    patching=patching, antivirus=antivirus, backup=backup,
                    logging=logging_score, firewall=firewall, encryption=encryption,
                    score=compliance_score
                ),
                overall=appliance_overall,
                status=HealthStatus.HEALTHY if is_online and compliance_score >= 70 else HealthStatus.WARNING
            ),
            created_at=a.first_checkin or datetime.now(timezone.utc)
        ))

    return appliances


# =============================================================================
# INCIDENT ENDPOINTS
# =============================================================================

@router.get("/incidents", response_model=List[Incident])
async def get_incidents(
    site_id: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    level: Optional[str] = None,
    resolved: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
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
    incidents = await get_incidents_from_db(db, site_id=site_id, limit=limit, resolved=resolved)
    return [
        Incident(
            id=str(i["id"]),
            site_id=i["site_id"],
            hostname=i.get("hostname", ""),
            check_type=CheckType(i["check_type"]) if i.get("check_type") else CheckType.BACKUP,
            severity=Severity(i["severity"]),
            resolution_level=ResolutionLevel(i["resolution_level"]) if i.get("resolution_level") else None,
            resolved=i["resolved"],
            resolved_at=i.get("resolved_at"),
            hipaa_controls=i.get("hipaa_controls", []),
            created_at=i["created_at"],
        )
        for i in incidents
    ]


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
async def get_incident_detail(incident_id: str, db: AsyncSession = Depends(get_db)):
    """Get full incident detail including evidence bundle."""
    result = await db.execute(
        text("""
            SELECT i.*, a.site_id, a.host_id as hostname
            FROM incidents i
            JOIN appliances a ON a.id = i.appliance_id
            WHERE i.id = :incident_id
        """),
        {"incident_id": incident_id}
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

    return IncidentDetail(
        id=str(row.id),
        site_id=row.site_id,
        appliance_id=str(row.appliance_id),
        hostname=row.hostname or "",
        check_type=CheckType(row.check_type) if row.check_type else CheckType.BACKUP,
        severity=Severity(row.severity),
        drift_data=row.details or {},
        resolution_level=ResolutionLevel(row.resolution_tier) if row.resolution_tier else None,
        resolved=row.status == "resolved",
        resolved_at=row.resolved_at,
        hipaa_controls=row.hipaa_controls or [],
        evidence_bundle_id=None,
        evidence_hash=None,
        runbook_executed=None,
        execution_log=None,
        created_at=row.created_at,
    )


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
async def get_learning_status(db: AsyncSession = Depends(get_db)):
    """Get current state of the L2->L1 learning loop.

    Returns:
        Total L1 rules, L2 decisions, patterns awaiting promotion, etc.
    """
    status = await get_learning_status_from_db(db)
    return LearningStatus(
        total_l1_rules=status.get("total_l1_rules", 0),
        total_l2_decisions_30d=status.get("total_l2_decisions_30d", 0),
        patterns_awaiting_promotion=status.get("patterns_awaiting_promotion", 0),
        recently_promoted_count=status.get("recently_promoted_count", 0),
        promotion_success_rate=status.get("promotion_success_rate", 0.0),
        l1_resolution_rate=status.get("l1_resolution_rate", 0.0),
        l2_resolution_rate=status.get("l2_resolution_rate", 0.0),
    )


@router.get("/learning/candidates", response_model=List[PromotionCandidate])
async def get_promotion_candidates(db: AsyncSession = Depends(get_db)):
    """Get patterns that are candidates for L1 promotion.

    Criteria: 5+ occurrences, 90%+ success rate.
    """
    candidates = await get_promotion_candidates_from_db(db)
    return [
        PromotionCandidate(
            id=c["id"],
            pattern_signature=c["pattern_signature"],
            description=c.get("description", ""),
            occurrences=c["occurrences"],
            success_rate=c["success_rate"],
            avg_resolution_time_ms=c.get("avg_resolution_time_ms", 0),
            proposed_rule=c.get("proposed_rule"),
            first_seen=c.get("first_seen"),
            last_seen=c.get("last_seen"),
        )
        for c in candidates
    ]


@router.get("/learning/history", response_model=List[PromotionHistory])
async def get_promotion_history(limit: int = Query(default=20, le=100), db: AsyncSession = Depends(get_db)):
    """Get recently promoted L2->L1 rules."""
    result = await db.execute(text("""
        SELECT pattern_id, pattern_signature, description, promoted_to_rule_id, promoted_at
        FROM patterns
        WHERE status = 'promoted'
        ORDER BY promoted_at DESC
        LIMIT :limit
    """), {"limit": limit})

    return [
        PromotionHistory(
            id=row.pattern_id,
            pattern_signature=row.pattern_signature,
            description=row.description or "",
            promoted_to_rule_id=row.promoted_to_rule_id,
            promoted_at=row.promoted_at,
        )
        for row in result.fetchall()
    ]


@router.post("/learning/promote/{pattern_id}")
async def promote_pattern(pattern_id: str, db: AsyncSession = Depends(get_db)):
    """Manually trigger promotion of a pattern to L1.

    Requires: Human review confirmation.
    """
    from .db_queries import promote_pattern_in_db

    rule_id = await promote_pattern_in_db(db, pattern_id)
    if rule_id:
        return {"status": "promoted", "pattern_id": pattern_id, "new_rule_id": rule_id}

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
async def get_onboarding_pipeline(db: AsyncSession = Depends(get_db)):
    """Get all prospects in the onboarding pipeline."""
    query = text("""
        SELECT
            ROW_NUMBER() OVER (ORDER BY created_at) as row_id,
            site_id, clinic_name, contact_name, contact_email, contact_phone,
            onboarding_stage, notes, blockers,
            lead_at, discovery_at, proposal_at, contract_at, intake_at,
            creds_at, shipped_at, received_at, connectivity_at,
            scanning_at, baseline_at, active_at, created_at
        FROM sites
        WHERE onboarding_stage NOT IN ('active', 'compliant')
        ORDER BY created_at DESC
    """)

    result = await db.execute(query)
    rows = result.fetchall()

    
    stage_progress = {
        'lead': 10, 'discovery': 20, 'proposal': 30, 'contract': 40,
        'intake': 50, 'creds': 60, 'shipped': 70, 'received': 80,
        'connectivity': 85, 'scanning': 90, 'baseline': 95
    }

    prospects = []
    for row in rows:
        stage_val = row.onboarding_stage or 'lead'
        stage_map = {
            'pending': 'lead', 'intake_received': 'intake', 'credentials': 'creds'
        }
        stage_val = stage_map.get(stage_val, stage_val)

        # Determine stage_entered_at
        ts_map = {
            'lead': row.lead_at, 'discovery': row.discovery_at,
            'proposal': row.proposal_at, 'contract': row.contract_at,
            'intake': row.intake_at, 'creds': row.creds_at,
            'shipped': row.shipped_at, 'received': row.received_at,
            'connectivity': row.connectivity_at, 'scanning': row.scanning_at,
            'baseline': row.baseline_at
        }
        stage_entered = ts_map.get(stage_val) or row.created_at

        days_in_stage = (datetime.now(timezone.utc) - stage_entered).days if stage_entered else 0

        blockers = []
        if row.blockers:
            try:
                import json
                blockers = json.loads(row.blockers) if isinstance(row.blockers, str) else row.blockers
            except: pass

        prospects.append(OnboardingClient(
            id=int(row.row_id),
            name=row.clinic_name or row.site_id,
            contact_name=row.contact_name,
            contact_email=row.contact_email,
            contact_phone=row.contact_phone,
            stage=OnboardingStage(stage_val) if stage_val in [s.value for s in OnboardingStage] else OnboardingStage.LEAD,
            stage_entered_at=stage_entered,
            days_in_stage=days_in_stage,
            blockers=blockers if isinstance(blockers, list) else [],
            notes=row.notes,
            lead_at=row.lead_at,
            discovery_at=row.discovery_at,
            proposal_at=row.proposal_at,
            contract_at=row.contract_at,
            intake_at=row.intake_at,
            creds_at=row.creds_at,
            shipped_at=row.shipped_at,
            received_at=row.received_at,
            connectivity_at=row.connectivity_at,
            scanning_at=row.scanning_at,
            baseline_at=row.baseline_at,
            active_at=row.active_at,
            created_at=row.created_at,
            progress_percent=stage_progress.get(stage_val, 50),
        ))

    return prospects


@router.get("/onboarding/metrics", response_model=OnboardingMetrics)
async def get_onboarding_metrics(db: AsyncSession = Depends(get_db)):
    """Get aggregate pipeline metrics.

    Returns:
        Counts by stage, avg time to deploy, at-risk clients.
    """
    # Count sites by onboarding stage
    result = await db.execute(text("""
        SELECT onboarding_stage, COUNT(*) as count FROM sites GROUP BY onboarding_stage
    """))
    stage_counts = {row.onboarding_stage: row.count for row in result.fetchall()}

    # Map stages to acquisition/activation
    acquisition = {
        "lead": stage_counts.get("lead", 0),
        "discovery": stage_counts.get("discovery", 0),
        "proposal": stage_counts.get("proposal", 0),
        "contract": stage_counts.get("contract", 0),
        "intake": stage_counts.get("intake", 0) + stage_counts.get("intake_received", 0),
        "creds": stage_counts.get("creds", 0) + stage_counts.get("credentials", 0),
        "shipped": stage_counts.get("shipped", 0),
    }
    activation = {
        "received": stage_counts.get("received", 0),
        "connectivity": stage_counts.get("connectivity", 0),
        "scanning": stage_counts.get("scanning", 0),
        "baseline": stage_counts.get("baseline", 0),
        "compliant": stage_counts.get("compliant", 0),
        "active": stage_counts.get("active", 0),
    }

    total = sum(stage_counts.values())

    return OnboardingMetrics(
        total_prospects=total,
        acquisition=acquisition,
        activation=activation,
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
        progress_percent=stage_progress.get(stage_val, 50),
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
async def get_global_stats(db: AsyncSession = Depends(get_db)):
    """Get aggregate statistics across all clients."""
    stats = await get_global_stats_from_db(db)
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


@router.get("/stats/{site_id}", response_model=ClientStats)
async def get_client_stats(site_id: str, db: AsyncSession = Depends(get_db)):
    """Get statistics for a specific client."""
    # Check site exists
    site_result = await db.execute(
        text("SELECT site_id FROM sites WHERE site_id = :site_id"),
        {"site_id": site_id}
    )
    if not site_result.fetchone():
        raise HTTPException(status_code=404, detail=f"Client {site_id} not found")

    # Get appliance counts
    appliance_result = await db.execute(text("""
        SELECT COUNT(*) as total,
               COUNT(*) FILTER (WHERE status = 'online') as online
        FROM site_appliances WHERE site_id = :site_id
    """), {"site_id": site_id})
    app_row = appliance_result.fetchone()

    # Get incident stats
    incident_result = await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE i.reported_at > NOW() - INTERVAL '24 hours') as day,
            COUNT(*) FILTER (WHERE i.reported_at > NOW() - INTERVAL '7 days') as week,
            COUNT(*) FILTER (WHERE i.reported_at > NOW() - INTERVAL '30 days') as month,
            COUNT(*) FILTER (WHERE i.resolution_tier = 'L1') as l1,
            COUNT(*) FILTER (WHERE i.resolution_tier = 'L2') as l2,
            COUNT(*) FILTER (WHERE i.resolution_tier = 'L3') as l3
        FROM incidents i
        JOIN appliances a ON a.id = i.appliance_id
        WHERE a.site_id = :site_id
    """), {"site_id": site_id})
    inc_row = incident_result.fetchone()

    return ClientStats(
        site_id=site_id,
        appliance_count=app_row.total or 0,
        online_count=app_row.online or 0,
        compliance_score=85.0,  # TODO: calculate from evidence
        connectivity_score=100.0 if app_row.online else 0.0,
        incidents_24h=inc_row.day or 0,
        incidents_7d=inc_row.week or 0,
        incidents_30d=inc_row.month or 0,
        l1_resolution_count=inc_row.l1 or 0,
        l2_resolution_count=inc_row.l2 or 0,
        l3_escalation_count=inc_row.l3 or 0,
    )


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
