"""FastAPI routes for Central Command Dashboard.

All endpoints for the dashboard including fleet, incidents, runbooks,
learning loop, onboarding, stats, and command interface.

This module uses the central database when available, falling back
to mock data for demo/development purposes.
"""

import json
import logging
from enum import Enum
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, Query, HTTPException, Request, Depends, Response
from pydantic import BaseModel
from .websocket_manager import broadcast_event

logger = logging.getLogger(__name__)
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
    PatternReport,
    PatternReportResponse,
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
    get_events_from_db,
    get_learning_status_from_db,
    get_promotion_candidates_from_db,
    get_global_stats_from_db,
    get_compliance_scores_for_site,
    get_all_compliance_scores,
    get_runbooks_from_db,
    get_runbook_detail_from_db,
    get_runbook_executions_from_db,
    get_healing_metrics_for_site,
    get_all_healing_metrics,
    get_global_healing_metrics,
)
from .email_alerts import send_critical_alert
from . import auth as auth_module


# ---- Safe enum converters (prevent crashes on unknown DB values) ----

def _safe_check_type(ct: str) -> CheckType:
    """Safely convert check type, defaulting to BACKUP for unknown types."""
    try:
        return CheckType(ct)
    except (ValueError, KeyError):
        return CheckType.BACKUP


def _safe_severity(sev) -> Severity:
    """Safely convert severity, defaulting to MEDIUM for unknown/null values."""
    if not sev:
        return Severity.MEDIUM
    try:
        return Severity(sev)
    except (ValueError, KeyError):
        return Severity.MEDIUM


def _safe_resolution_level(rl) -> Optional[ResolutionLevel]:
    """Safely convert resolution level, returning None for unknown values."""
    if not rl:
        return None
    try:
        return ResolutionLevel(rl)
    except (ValueError, KeyError):
        return None


# Dashboard router - requires authentication for all routes
router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(auth_module.require_auth)],
)

# Database session dependency
async def get_db():
    # Try multiple import paths for flexibility
    try:
        from main import async_session
    except ImportError:
        import sys
        if 'server' in sys.modules and hasattr(sys.modules['server'], 'async_session'):
            async_session = sys.modules['server'].async_session
        else:
            raise RuntimeError("Database session not configured")

    async with async_session() as session:
        yield session



# =============================================================================
# FLEET ENDPOINTS
# =============================================================================

@router.get("/fleet")
async def get_fleet_overview(
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
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
        LIMIT :limit OFFSET :offset
    """)

    result = await db.execute(query, {"limit": limit, "offset": offset})
    rows = result.fetchall()

    # Get compliance scores for all sites
    all_compliance = await get_all_compliance_scores(db)

    # Batch-fetch healing metrics for all sites in 2 queries (replaces N+1)
    healing_metrics_cache = await get_all_healing_metrics(db)

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

        # Get healing metrics for this site
        site_healing = healing_metrics_cache.get(row.site_id, {})

        clients.append(ClientOverview(
            site_id=row.site_id,
            name=row.name or row.site_id,
            appliance_count=row.appliance_count or 0,
            online_count=row.online_count or 0,
            health=HealthMetrics(
                connectivity=ConnectivityMetrics(
                    checkin_freshness=checkin_freshness,
                    healing_success_rate=site_healing.get("healing_success_rate", 100.0),
                    order_execution_rate=site_healing.get("order_execution_rate", 100.0),
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
            last_incident=site_healing.get("last_incident"),
            incidents_24h=site_healing.get("incidents_24h", 0)
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

    # Sequential - AsyncSession doesn't support concurrent ops on same session
    site_compliance = await get_compliance_scores_for_site(db, site_id)
    site_healing = await get_healing_metrics_for_site(db, site_id)

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
                ips = json.loads(a.ip_addresses) if isinstance(a.ip_addresses, str) else a.ip_addresses
                ip_addr = ips[0] if ips else None
            except (json.JSONDecodeError, TypeError, IndexError) as e:
                logger.debug(f"Failed to parse IP addresses for appliance: {e}")

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
                    healing_success_rate=site_healing.get("healing_success_rate", 100.0),
                    order_execution_rate=site_healing.get("order_execution_rate", 100.0),
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
                healing_success_rate=site_healing.get("healing_success_rate", 100.0),
                order_execution_rate=site_healing.get("order_execution_rate", 100.0),
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
    # Sequential - AsyncSession doesn't support concurrent ops on same session
    site_compliance = await get_compliance_scores_for_site(db, site_id)
    site_healing = await get_healing_metrics_for_site(db, site_id)

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
                ips = json.loads(a.ip_addresses) if isinstance(a.ip_addresses, str) else a.ip_addresses
                ip_addr = ips[0] if ips else None
            except (json.JSONDecodeError, TypeError, IndexError) as e:
                logger.debug(f"Failed to parse IP addresses for appliance: {e}")

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
                    healing_success_rate=site_healing.get("healing_success_rate", 100.0),
                    order_execution_rate=site_healing.get("order_execution_rate", 100.0),
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
    offset: int = Query(default=0, ge=0),
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
    incidents = await get_incidents_from_db(db, site_id=site_id, limit=limit, offset=offset, resolved=resolved)

    return [
        Incident(
            id=str(i["id"]),
            site_id=i["site_id"],
            hostname=i.get("hostname", ""),
            check_type=_safe_check_type(i["check_type"]) if i.get("check_type") else CheckType.BACKUP,
            severity=_safe_severity(i["severity"]),
            resolution_level=_safe_resolution_level(i.get("resolution_level")),
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
        check_type=_safe_check_type(row.check_type) if row.check_type else CheckType.BACKUP,
        severity=_safe_severity(row.severity),
        drift_data=row.details or {},
        resolution_level=_safe_resolution_level(row.resolution_tier),
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
# EVENTS ENDPOINTS (Compliance Bundles)
# =============================================================================

@router.get("/events")
async def get_events(
    site_id: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Get recent events from compliance bundles.

    This shows drift detections and compliance checks from appliances.
    Unlike incidents which require explicit reporting, events are automatically
    created for every compliance check run by the appliance.

    Args:
        site_id: Filter by client site
        limit: Maximum number of results

    Returns:
        List of recent events with check results.
    """
    events = await get_events_from_db(db, site_id=site_id, limit=limit, offset=offset)
    return events


# =============================================================================
# RUNBOOK ENDPOINTS
# =============================================================================

@router.get("/runbooks", response_model=List[Runbook])
async def get_runbooks(db: AsyncSession = Depends(get_db)):
    """Get all runbooks in the library.

    Returns:
        List of runbooks with HIPAA mappings, execution stats from database.
    """
    runbooks = await get_runbooks_from_db(db)

    return [
        Runbook(
            id=rb["id"],
            name=rb["name"],
            description=rb["description"],
            level=ResolutionLevel.L1,
            hipaa_controls=rb["hipaa_controls"],
            is_disruptive=rb["is_disruptive"],
            execution_count=rb["execution_count"],
            success_rate=rb["success_rate"],
            avg_execution_time_ms=rb["avg_execution_time_ms"],
        )
        for rb in runbooks
    ]


@router.get("/runbooks/{runbook_id}", response_model=RunbookDetail)
async def get_runbook_detail(runbook_id: str, db: AsyncSession = Depends(get_db)):
    """Get runbook detail including steps, params, execution history."""
    rb = await get_runbook_detail_from_db(db, runbook_id)

    if not rb:
        raise HTTPException(status_code=404, detail=f"Runbook {runbook_id} not found")

    return RunbookDetail(
        id=rb["id"],
        name=rb["name"],
        description=rb["description"],
        level=ResolutionLevel.L1,
        hipaa_controls=rb["hipaa_controls"],
        is_disruptive=rb["is_disruptive"],
        steps=rb["steps"],
        parameters=rb["parameters"],
        execution_count=rb["execution_count"],
        success_rate=rb["success_rate"],
        avg_execution_time_ms=rb["avg_execution_time_ms"],
        created_at=rb["created_at"],
        updated_at=rb["updated_at"],
    )


@router.get("/runbooks/{runbook_id}/executions", response_model=List[RunbookExecution])
async def get_runbook_executions(
    runbook_id: str,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Get recent executions of a specific runbook from orders table."""
    executions = await get_runbook_executions_from_db(db, runbook_id, limit, offset=offset)

    return [
        RunbookExecution(
            id=ex["id"],
            runbook_id=ex["runbook_id"],
            site_id=ex["site_id"],
            hostname=ex["hostname"],
            incident_id=ex["incident_id"],
            success=ex["success"],
            execution_time_ms=ex["execution_time_ms"],
            output=ex["output"],
            executed_at=ex["executed_at"],
        )
        for ex in executions
    ]


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
            description=c.get("description") or "",
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
    # Match execution telemetry by incident_type + target (hostname or site_id)
    # from pattern_signature format "check_type:check_type:target".
    # Old approach matched on promoted_to_rule_id but agent records executions
    # with different runbook IDs (e.g. L1-SVC-DNS-001 vs RB-AUTO-SERVICE_).
    result = await db.execute(text("""
        SELECT
            p.pattern_id,
            p.pattern_signature,
            p.promoted_to_rule_id,
            p.promoted_at,
            COALESCE(exec_stats.total, 0) as executions_since,
            COALESCE(exec_stats.success_pct, 0) as success_rate
        FROM patterns p
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE et.success) * 100.0 / NULLIF(COUNT(*), 0) as success_pct
            FROM execution_telemetry et
            WHERE et.incident_type = p.incident_type
            AND et.created_at > p.promoted_at
            AND (
                et.hostname = split_part(p.pattern_signature, ':', 3)
                OR et.site_id = split_part(p.pattern_signature, ':', 3)
            )
        ) exec_stats ON true
        WHERE p.status = 'promoted'
        ORDER BY p.promoted_at DESC
        LIMIT :limit
    """), {"limit": limit})

    return [
        PromotionHistory(
            id=row.pattern_id,
            pattern_signature=row.pattern_signature,
            rule_id=row.promoted_to_rule_id or f"L1-{row.pattern_id[:8].upper()}",
            promoted_at=row.promoted_at,
            post_promotion_success_rate=float(row.success_rate or 0),
            executions_since_promotion=int(row.executions_since or 0),
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


@router.post("/learning/reject/{pattern_id}")
async def reject_pattern(pattern_id: str, db: AsyncSession = Depends(get_db)):
    """Reject a promotion candidate, marking it as dismissed."""
    result = await db.execute(
        text("SELECT pattern_id, status FROM patterns WHERE pattern_id = :pid"),
        {"pid": pattern_id}
    )
    pattern = result.fetchone()
    if not pattern:
        raise HTTPException(status_code=404, detail=f"Pattern {pattern_id} not found")

    await db.execute(text("""
        UPDATE patterns SET status = 'rejected' WHERE pattern_id = :pid
    """), {"pid": pattern_id})
    await db.commit()

    return {"status": "rejected", "pattern_id": pattern_id}


@router.post("/patterns", response_model=PatternReportResponse)
async def report_pattern(report: PatternReport, db: AsyncSession = Depends(get_db)):
    """Receive pattern report from agent after successful healing.

    This endpoint is called by appliances after L1/L2 healing succeeds.
    Patterns are aggregated and tracked for potential L1 promotion.

    Args:
        report: Pattern report containing healing details

    Returns:
        Pattern status including occurrences and success rate
    """
    import hashlib
    from datetime import datetime, timezone

    # Generate pattern ID from signature
    pattern_signature = f"{report.check_type}:{report.issue_signature}"
    pattern_id = hashlib.sha256(pattern_signature.encode()).hexdigest()[:16]

    # Check if pattern exists
    result = await db.execute(
        text("SELECT pattern_id, occurrences, success_count, failure_count FROM patterns WHERE pattern_id = :pid"),
        {"pid": pattern_id}
    )
    existing = result.fetchone()

    if existing:
        # Update existing pattern
        occurrences = existing.occurrences + 1
        success_count = existing.success_count + (1 if report.success else 0)
        failure_count = existing.failure_count + (0 if report.success else 1)
        success_rate = (success_count / occurrences) * 100 if occurrences > 0 else 0.0

        await db.execute(text("""
            UPDATE patterns
            SET occurrences = :occ,
                success_count = :sc,
                failure_count = :fc,
                success_rate = :rate,
                last_seen = NOW()
            WHERE pattern_id = :pid
        """), {
            "pid": pattern_id,
            "occ": occurrences,
            "sc": success_count,
            "fc": failure_count,
            "rate": success_rate,
        })
        await db.commit()

        return PatternReportResponse(
            pattern_id=pattern_id,
            status="updated",
            occurrences=occurrences,
            success_rate=success_rate,
        )
    else:
        # Create new pattern
        occurrences = 1
        success_count = 1 if report.success else 0
        failure_count = 0 if report.success else 1
        success_rate = 100.0 if report.success else 0.0

        await db.execute(text("""
            INSERT INTO patterns (
                pattern_id, pattern_signature, description, incident_type, runbook_id,
                occurrences, success_count, failure_count, success_rate,
                avg_resolution_time_ms, total_resolution_time_ms,
                status, first_seen, last_seen, created_at
            ) VALUES (
                :pid, :sig, :desc, :itype, :rid,
                :occ, :sc, :fc, :rate,
                :avg_time, :total_time,
                'pending', NOW(), NOW(), NOW()
            )
        """), {
            "pid": pattern_id,
            "sig": pattern_signature,
            "desc": f"Auto-detected pattern from {report.site_id}",
            "itype": report.check_type,
            "rid": report.runbook_id,
            "occ": occurrences,
            "sc": success_count,
            "fc": failure_count,
            "rate": success_rate,
            "avg_time": report.execution_time_ms,
            "total_time": report.execution_time_ms,
        })
        await db.commit()

        return PatternReportResponse(
            pattern_id=pattern_id,
            status="created",
            occurrences=occurrences,
            success_rate=success_rate,
        )


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
                blockers = json.loads(row.blockers) if isinstance(row.blockers, str) else row.blockers
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug(f"Failed to parse blockers JSON: {e}")

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

    # Calculate real metrics from sites table
    ship_result = await db.execute(text("""
        SELECT AVG(EXTRACT(EPOCH FROM (shipped_at - lead_at)) / 86400.0)
        FROM sites WHERE shipped_at IS NOT NULL AND lead_at IS NOT NULL
    """))
    avg_ship = round(ship_result.scalar() or 0.0, 1)

    active_result = await db.execute(text("""
        SELECT AVG(EXTRACT(EPOCH FROM (active_at - lead_at)) / 86400.0)
        FROM sites WHERE active_at IS NOT NULL AND lead_at IS NOT NULL
    """))
    avg_active = round(active_result.scalar() or 0.0, 1)

    stalled_result = await db.execute(text("""
        SELECT COUNT(*) FROM sites
        WHERE onboarding_stage NOT IN ('active', 'compliant')
        AND created_at < NOW() - INTERVAL '14 days'
    """))
    stalled = stalled_result.scalar() or 0

    conn_result = await db.execute(text("""
        SELECT COUNT(*) FROM sites
        WHERE onboarding_stage = 'connectivity'
        AND connectivity_at IS NOT NULL
        AND connectivity_at < NOW() - INTERVAL '3 days'
    """))
    conn_issues = conn_result.scalar() or 0

    return OnboardingMetrics(
        total_prospects=total,
        acquisition=acquisition,
        activation=activation,
        avg_days_to_ship=avg_ship,
        avg_days_to_active=avg_active,
        stalled_count=stalled,
        at_risk_count=stalled,
        connectivity_issues=conn_issues,
    )


@router.get("/onboarding/{client_id}", response_model=OnboardingClient)
async def get_onboarding_detail(client_id: int, db: AsyncSession = Depends(get_db)):
    """Get detailed onboarding status for a single client."""
    clients = await get_onboarding_pipeline(db)
    for client in clients:
        if client.id == str(client_id):
            return client
    raise HTTPException(status_code=404, detail=f"Client {client_id} not found")


@router.post("/onboarding", response_model=OnboardingClient)
async def create_prospect(prospect: ProspectCreate, db: AsyncSession = Depends(get_db)):
    """Create new prospect (Lead stage)."""
    now = datetime.now(timezone.utc)

    # Insert into DB
    result = await db.execute(text("""
        INSERT INTO sites (site_id, clinic_name, contact_name, contact_email, contact_phone,
                           onboarding_stage, notes, lead_at, created_at)
        VALUES (:site_id, :name, :contact_name, :contact_email, :contact_phone,
                'lead', :notes, :now, :now)
        RETURNING site_id
    """), {
        "site_id": prospect.name.lower().replace(" ", "-").replace("'", ""),
        "name": prospect.name,
        "contact_name": prospect.contact_name,
        "contact_email": prospect.contact_email,
        "contact_phone": prospect.contact_phone,
        "notes": prospect.notes,
        "now": now,
    })
    await db.commit()
    row = result.fetchone()

    return OnboardingClient(
        id=row.site_id if row else prospect.name.lower().replace(" ", "-"),
        name=prospect.name,
        contact_name=prospect.contact_name,
        contact_email=prospect.contact_email,
        contact_phone=prospect.contact_phone,
        stage=OnboardingStage.LEAD,
        stage_entered_at=now,
        days_in_stage=0,
        notes=prospect.notes,
        lead_at=now,
        progress_percent=10,
        created_at=now,
    )


@router.patch("/onboarding/{client_id}/stage")
async def advance_stage(client_id: str, request: StageAdvance, db: AsyncSession = Depends(get_db)):
    """Move client to next stage."""
    now = datetime.now(timezone.utc)
    stage_val = request.new_stage.value

    await db.execute(text("""
        UPDATE sites
        SET onboarding_stage = :new_stage
        WHERE site_id = :client_id
    """), {"new_stage": stage_val, "client_id": client_id})

    # Set stage timestamp column if it exists
    stage_col_map = {
        'lead': 'lead_at', 'discovery': 'discovery_at', 'proposal': 'proposal_at',
        'contract': 'contract_at', 'intake': 'intake_at', 'creds': 'creds_at',
        'shipped': 'shipped_at', 'received': 'received_at', 'connectivity': 'connectivity_at',
        'scanning': 'scanning_at', 'baseline': 'baseline_at', 'active': 'active_at',
    }
    ts_col = stage_col_map.get(stage_val)
    if ts_col:
        try:
            await db.execute(
                text(f"UPDATE sites SET {ts_col} = :now WHERE site_id = :client_id"),
                {"now": now, "client_id": client_id},
            )
        except Exception:
            pass  # Column may not exist

    if request.notes:
        await db.execute(text("""
            UPDATE sites SET notes = COALESCE(notes || E'\\n', '') || :note
            WHERE site_id = :client_id
        """), {"note": request.notes, "client_id": client_id})

    await db.commit()
    return {"status": "advanced", "client_id": client_id, "new_stage": stage_val}


@router.patch("/onboarding/{client_id}/blockers")
async def update_blockers(client_id: str, request: BlockerUpdate, db: AsyncSession = Depends(get_db)):
    """Update blockers for a client."""
    await db.execute(text("""
        UPDATE sites SET blockers = :blockers WHERE site_id = :client_id
    """), {"blockers": json.dumps(request.blockers), "client_id": client_id})
    await db.commit()
    return {"status": "updated", "client_id": client_id, "blockers": request.blockers}


@router.post("/onboarding/{client_id}/note")
async def add_note(client_id: str, request: NoteAdd, db: AsyncSession = Depends(get_db)):
    """Add a note to client's onboarding record."""
    await db.execute(text("""
        UPDATE sites SET notes = COALESCE(notes || E'\\n', '') || :note
        WHERE site_id = :client_id
    """), {"note": request.note, "client_id": client_id})
    await db.commit()
    return {"status": "added", "client_id": client_id, "note": request.note}


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
        computed_at=datetime.now(timezone.utc).isoformat(),
    )


# =============================================================================
# COMMAND CENTER ENDPOINTS
# =============================================================================

@router.get("/fleet-posture")
async def get_fleet_posture(db: AsyncSession = Depends(get_db)):
    """Fleet-wide health matrix: per-site health, incidents, trend, sorted by needs-attention."""
    try:
        result = await db.execute(text("""
            WITH site_health AS (
                SELECT
                    s.site_id,
                    s.clinic_name,
                    COUNT(DISTINCT sa.id) as appliance_count,
                    COUNT(DISTINCT sa.id) FILTER (
                        WHERE sa.last_checkin > NOW() - INTERVAL '5 minutes'
                    ) as online_count,
                    MAX(sa.last_checkin) as last_checkin
                FROM sites s
                LEFT JOIN site_appliances sa ON sa.site_id = s.site_id
                GROUP BY s.site_id, s.clinic_name
            ),
            site_incidents AS (
                SELECT
                    i.site_id,
                    COUNT(*) FILTER (
                        WHERE i.reported_at > NOW() - INTERVAL '24 hours'
                    ) as incidents_24h,
                    COUNT(*) FILTER (
                        WHERE i.status != 'resolved'
                    ) as unresolved,
                    COUNT(*) FILTER (
                        WHERE i.resolution_tier = 'L3' AND i.status != 'resolved'
                    ) as l3_unresolved,
                    COUNT(*) FILTER (
                        WHERE i.resolution_tier = 'L1' AND i.reported_at > NOW() - INTERVAL '24 hours'
                    ) as l1_24h,
                    COUNT(*) FILTER (
                        WHERE i.resolution_tier = 'L2' AND i.reported_at > NOW() - INTERVAL '24 hours'
                    ) as l2_24h,
                    COUNT(*) FILTER (
                        WHERE i.resolution_tier = 'L3' AND i.reported_at > NOW() - INTERVAL '24 hours'
                    ) as l3_24h
                FROM incidents i
                WHERE i.reported_at > NOW() - INTERVAL '30 days'
                GROUP BY i.site_id
            ),
            site_compliance AS (
                SELECT
                    cb.site_id,
                    ROUND(
                        COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant'))::numeric /
                        NULLIF(COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant', 'fail', 'non_compliant', 'warning'))::numeric, 0) * 100, 1
                    ) as compliance_score
                FROM compliance_bundles cb,
                     jsonb_array_elements(cb.checks) as c
                WHERE cb.created_at > NOW() - INTERVAL '24 hours'
                  AND jsonb_array_length(cb.checks) > 0
                GROUP BY cb.site_id
            ),
            site_compliance_prev AS (
                SELECT
                    cb.site_id,
                    ROUND(
                        COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant'))::numeric /
                        NULLIF(COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant', 'fail', 'non_compliant', 'warning'))::numeric, 0) * 100, 1
                    ) as prev_score
                FROM compliance_bundles cb,
                     jsonb_array_elements(cb.checks) as c
                WHERE cb.created_at BETWEEN NOW() - INTERVAL '48 hours' AND NOW() - INTERVAL '24 hours'
                  AND jsonb_array_length(cb.checks) > 0
                GROUP BY cb.site_id
            )
            SELECT
                sh.site_id,
                sh.clinic_name,
                sh.appliance_count,
                sh.online_count,
                sh.last_checkin,
                COALESCE(si.incidents_24h, 0) as incidents_24h,
                COALESCE(si.unresolved, 0) as unresolved,
                COALESCE(si.l3_unresolved, 0) as l3_unresolved,
                COALESCE(si.l1_24h, 0) as l1_24h,
                COALESCE(si.l2_24h, 0) as l2_24h,
                COALESCE(si.l3_24h, 0) as l3_24h,
                COALESCE(sc.compliance_score, 0) as compliance_score,
                CASE
                    WHEN sc.compliance_score > scp.prev_score + 2 THEN 'improving'
                    WHEN sc.compliance_score < scp.prev_score - 2 THEN 'declining'
                    ELSE 'stable'
                END as trend
            FROM site_health sh
            LEFT JOIN site_incidents si ON si.site_id = sh.site_id
            LEFT JOIN site_compliance sc ON sc.site_id = sh.site_id
            LEFT JOIN site_compliance_prev scp ON scp.site_id = sh.site_id
            ORDER BY
                COALESCE(si.l3_unresolved, 0) DESC,
                COALESCE(si.unresolved, 0) DESC,
                COALESCE(sc.compliance_score, 100) ASC,
                sh.clinic_name ASC
        """))
        rows = result.fetchall()
        return [{
            "site_id": r.site_id,
            "clinic_name": r.clinic_name,
            "appliance_count": r.appliance_count,
            "online_count": r.online_count,
            "last_checkin": r.last_checkin.isoformat() if r.last_checkin else None,
            "incidents_24h": r.incidents_24h,
            "unresolved": r.unresolved,
            "l3_unresolved": r.l3_unresolved,
            "l1_24h": r.l1_24h,
            "l2_24h": r.l2_24h,
            "l3_24h": r.l3_24h,
            "compliance_score": float(r.compliance_score),
            "trend": r.trend or "stable",
        } for r in rows]
    except Exception as e:
        logger.warning(f"Fleet posture query failed: {e}")
        return []


@router.get("/incident-trends")
async def get_incident_trends(
    window: str = Query("24h", regex="^(24h|7d|30d)$"),
    site_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Time-series incident data bucketed by hour or day, grouped by resolution tier."""
    try:
        if window == "24h":
            bucket = "hour"
            interval = "24 hours"
            trunc = "date_trunc('hour', i.reported_at)"
        elif window == "7d":
            bucket = "day"
            interval = "7 days"
            trunc = "date_trunc('day', i.reported_at)"
        else:
            bucket = "day"
            interval = "30 days"
            trunc = "date_trunc('day', i.reported_at)"

        site_filter = "AND i.site_id = :site_id" if site_id else ""
        params = {"site_id": site_id} if site_id else {}

        result = await db.execute(text(f"""
            SELECT
                {trunc} as bucket,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L1') as l1,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L2') as l2,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L3') as l3,
                COUNT(*) FILTER (WHERE i.status != 'resolved') as unresolved,
                COUNT(*) as total
            FROM incidents i
            WHERE i.reported_at > NOW() - INTERVAL '{interval}'
            {site_filter}
            GROUP BY bucket
            ORDER BY bucket ASC
        """), params)
        rows = result.fetchall()

        return {
            "window": window,
            "bucket_type": bucket,
            "data": [{
                "time": r.bucket.isoformat() if r.bucket else None,
                "l1": r.l1,
                "l2": r.l2,
                "l3": r.l3,
                "unresolved": r.unresolved,
                "total": r.total,
            } for r in rows]
        }
    except Exception as e:
        logger.warning(f"Incident trends query failed: {e}")
        return {"window": window, "bucket_type": "hour", "data": []}


@router.get("/incident-breakdown")
async def get_incident_breakdown(
    window: str = Query("24h", regex="^(24h|7d|30d)$"),
    site_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Aggregate incident breakdown: tier counts, top types, MTTR by tier."""
    try:
        if window == "24h":
            interval = "24 hours"
        elif window == "7d":
            interval = "7 days"
        else:
            interval = "30 days"

        site_filter = "AND i.site_id = :site_id" if site_id else ""
        params = {"site_id": site_id} if site_id else {}

        # Tier counts
        tier_result = await db.execute(text(f"""
            SELECT
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L1') as l1,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L2') as l2,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L3') as l3,
                COUNT(*) FILTER (WHERE i.resolution_tier IS NULL) as unclassified,
                COUNT(*) FILTER (WHERE i.status != 'resolved') as unresolved,
                COUNT(*) as total
            FROM incidents i
            WHERE i.reported_at > NOW() - INTERVAL '{interval}'
            {site_filter}
        """), params)
        tier = tier_result.fetchone()

        # Top incident types with tier breakdown
        types_result = await db.execute(text(f"""
            SELECT
                COALESCE(i.incident_type, i.check_type, 'unknown') as incident_type,
                COUNT(*) as count,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L1') as l1,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L2') as l2,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L3') as l3
            FROM incidents i
            WHERE i.reported_at > NOW() - INTERVAL '{interval}'
            {site_filter}
            GROUP BY COALESCE(i.incident_type, i.check_type, 'unknown')
            ORDER BY count DESC
            LIMIT 8
        """), params)
        types_rows = types_result.fetchall()

        # MTTR by tier (average minutes from reported to resolved)
        mttr_result = await db.execute(text(f"""
            SELECT
                i.resolution_tier,
                ROUND(AVG(EXTRACT(EPOCH FROM (i.resolved_at - i.reported_at)) / 60)::numeric, 1) as avg_minutes,
                COUNT(*) as resolved_count
            FROM incidents i
            WHERE i.reported_at > NOW() - INTERVAL '{interval}'
            AND i.status = 'resolved'
            AND i.resolved_at IS NOT NULL
            {site_filter}
            GROUP BY i.resolution_tier
        """), params)
        mttr_rows = mttr_result.fetchall()
        mttr_map = {r.resolution_tier: {"avg_minutes": float(r.avg_minutes) if r.avg_minutes else 0, "resolved_count": r.resolved_count} for r in mttr_rows}

        return {
            "window": window,
            "tier_counts": {
                "l1": tier.l1 if tier else 0,
                "l2": tier.l2 if tier else 0,
                "l3": tier.l3 if tier else 0,
                "unclassified": tier.unclassified if tier else 0,
                "unresolved": tier.unresolved if tier else 0,
                "total": tier.total if tier else 0,
            },
            "top_types": [{
                "incident_type": r.incident_type,
                "count": r.count,
                "l1": r.l1,
                "l2": r.l2,
                "l3": r.l3,
            } for r in types_rows],
            "mttr": {
                "l1": mttr_map.get("L1", {"avg_minutes": 0, "resolved_count": 0}),
                "l2": mttr_map.get("L2", {"avg_minutes": 0, "resolved_count": 0}),
                "l3": mttr_map.get("L3", {"avg_minutes": 0, "resolved_count": 0}),
            },
        }
    except Exception as e:
        logger.warning(f"Incident breakdown query failed: {e}")
        return {"window": window, "tier_counts": {}, "top_types": [], "mttr": {}}


@router.get("/attention-required")
async def get_attention_required(db: AsyncSession = Depends(get_db)):
    """Items that need human attention: L3 escalations, failed healings, offline appliances."""
    try:
        # L3 escalations (unresolved)
        l3_result = await db.execute(text("""
            SELECT
                i.id, i.site_id, i.incident_type, i.check_type, i.severity,
                i.reported_at, s.clinic_name
            FROM incidents i
            LEFT JOIN sites s ON s.site_id = i.site_id
            WHERE i.resolution_tier = 'L3'
            AND i.status != 'resolved'
            ORDER BY i.reported_at DESC
            LIMIT 20
        """))
        l3_rows = l3_result.fetchall()

        # Repeat offenders: same check_type, same site, 3+ incidents in 24h (healing not sticking)
        repeat_result = await db.execute(text("""
            SELECT
                i.site_id, i.check_type, COUNT(*) as occurrences,
                MAX(i.reported_at) as latest,
                s.clinic_name
            FROM incidents i
            LEFT JOIN sites s ON s.site_id = i.site_id
            WHERE i.reported_at > NOW() - INTERVAL '24 hours'
            AND i.resolution_tier = 'L1'
            GROUP BY i.site_id, i.check_type, s.clinic_name
            HAVING COUNT(*) >= 3
            ORDER BY COUNT(*) DESC
            LIMIT 20
        """))
        repeat_rows = repeat_result.fetchall()

        # Offline appliances (no checkin > 30 min)
        offline_result = await db.execute(text("""
            SELECT
                sa.site_id, sa.hostname, sa.last_checkin, sa.agent_version,
                s.clinic_name
            FROM site_appliances sa
            LEFT JOIN sites s ON s.site_id = sa.site_id
            WHERE sa.last_checkin < NOW() - INTERVAL '30 minutes'
            OR sa.last_checkin IS NULL
            ORDER BY sa.last_checkin ASC NULLS FIRST
            LIMIT 20
        """))
        offline_rows = offline_result.fetchall()

        items = []

        for r in l3_rows:
            items.append({
                "type": "l3_escalation",
                "severity": "critical",
                "title": f"L3 Escalation: {r.check_type or r.incident_type}",
                "site_id": r.site_id,
                "clinic_name": r.clinic_name,
                "detail": f"Unresolved since {r.reported_at.strftime('%b %d %H:%M') if r.reported_at else 'unknown'}",
                "timestamp": r.reported_at.isoformat() if r.reported_at else None,
                "incident_id": r.id,
            })

        for r in repeat_rows:
            items.append({
                "type": "repeat_failure",
                "severity": "warning",
                "title": f"Repeat drift: {r.check_type} ({r.occurrences}x in 24h)",
                "site_id": r.site_id,
                "clinic_name": r.clinic_name,
                "detail": f"Auto-healing not sticking — {r.occurrences} recurrences",
                "timestamp": r.latest.isoformat() if r.latest else None,
                "incident_id": None,
            })

        for r in offline_rows:
            items.append({
                "type": "offline_appliance",
                "severity": "warning",
                "title": f"Appliance offline: {r.hostname or 'unknown'}",
                "site_id": r.site_id,
                "clinic_name": r.clinic_name,
                "detail": f"Last seen {r.last_checkin.strftime('%b %d %H:%M') if r.last_checkin else 'never'}",
                "timestamp": r.last_checkin.isoformat() if r.last_checkin else None,
                "incident_id": None,
            })

        # Sort: critical first, then by timestamp desc
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        items.sort(key=lambda x: (severity_order.get(x["severity"], 9), x.get("timestamp") or "", ))

        return {
            "count": len(items),
            "l3_count": len(l3_rows),
            "repeat_count": len(repeat_rows),
            "offline_count": len(offline_rows),
            "items": items,
        }
    except Exception as e:
        logger.warning(f"Attention required query failed: {e}")
        return {"count": 0, "l3_count": 0, "repeat_count": 0, "offline_count": 0, "items": []}


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

    # Calculate compliance score from individual checks within bundles
    compliance_result = await db.execute(text("""
        WITH expanded AS (
            SELECT c->>'status' as check_status
            FROM compliance_bundles cb,
                 jsonb_array_elements(cb.checks) as c
            WHERE cb.site_id = :site_id
              AND cb.created_at > NOW() - INTERVAL '24 hours'
              AND jsonb_array_length(cb.checks) > 0
        )
        SELECT
            COUNT(*) FILTER (WHERE check_status IN ('pass', 'compliant')) as passed,
            COUNT(*) FILTER (WHERE check_status IN ('pass', 'compliant', 'fail', 'non_compliant', 'warning')) as total
        FROM expanded
    """), {"site_id": site_id})
    comp_row = compliance_result.fetchone()
    compliance_score = round((comp_row.passed or 0) / max(comp_row.total or 1, 1) * 100, 1)

    return ClientStats(
        site_id=site_id,
        appliance_count=app_row.total or 0,
        online_count=app_row.online or 0,
        compliance_score=compliance_score,
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


# =============================================================================
# NOTIFICATIONS
# =============================================================================

class NotificationSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
    SUCCESS = "success"

class Notification(BaseModel):
    id: str
    site_id: Optional[str] = None
    appliance_id: Optional[str] = None
    severity: NotificationSeverity
    category: str
    title: str
    message: str
    metadata: dict = {}
    is_read: bool = False
    is_dismissed: bool = False
    created_at: datetime
    read_at: Optional[datetime] = None

class NotificationSummary(BaseModel):
    total: int
    unread: int
    critical: int
    warning: int
    info: int
    success: int


class NotificationCreate(BaseModel):
    """Request model for creating a notification."""
    severity: str
    category: str
    title: str
    message: str
    site_id: Optional[str] = None
    appliance_id: Optional[str] = None
    metadata: Optional[dict] = None


@router.get("/notifications", response_model=List[Notification])
async def get_notifications(
    site_id: Optional[str] = None,
    severity: Optional[str] = None,
    unread_only: bool = False,
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db)
):
    """Get notifications with optional filters."""
    try:
        query = """
            SELECT id, site_id, appliance_id, severity, category, title, message,
                   metadata, is_read, is_dismissed, created_at, read_at
            FROM notifications
            WHERE is_dismissed = FALSE
        """
        params = {}

        if site_id:
            query += " AND (site_id = :site_id OR site_id IS NULL)"
            params["site_id"] = site_id
        if severity:
            query += " AND severity = :severity"
            params["severity"] = severity
        if unread_only:
            query += " AND is_read = FALSE"

        query += " ORDER BY created_at DESC LIMIT :limit"
        params["limit"] = limit

        result = await db.execute(text(query), params)
        rows = result.fetchall()

        return [Notification(
            id=str(row.id),
            site_id=row.site_id,
            appliance_id=row.appliance_id,
            severity=row.severity,
            category=row.category,
            title=row.title,
            message=row.message,
            metadata=row.metadata or {},
            is_read=row.is_read,
            is_dismissed=row.is_dismissed,
            created_at=row.created_at,
            read_at=row.read_at,
        ) for row in rows]
    except Exception as e:
        logger.warning(f"Failed to fetch notifications: {e}")
        return []


@router.get("/notifications/summary", response_model=NotificationSummary)
async def get_notification_summary(db: AsyncSession = Depends(get_db)):
    """Get notification counts by severity."""
    try:
        result = await db.execute(text("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE is_read = FALSE) as unread,
                COUNT(*) FILTER (WHERE severity = 'critical' AND is_read = FALSE) as critical,
                COUNT(*) FILTER (WHERE severity = 'warning' AND is_read = FALSE) as warning,
                COUNT(*) FILTER (WHERE severity = 'info' AND is_read = FALSE) as info,
                COUNT(*) FILTER (WHERE severity = 'success' AND is_read = FALSE) as success
            FROM notifications
            WHERE is_dismissed = FALSE
        """))
        row = result.fetchone()
        return NotificationSummary(
            total=row.total or 0,
            unread=row.unread or 0,
            critical=row.critical or 0,
            warning=row.warning or 0,
            info=row.info or 0,
            success=row.success or 0,
        )
    except Exception as e:
        logger.warning(f"Failed to fetch notification summary: {e}")
        return NotificationSummary(total=0, unread=0, critical=0, warning=0, info=0, success=0)


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, db: AsyncSession = Depends(get_db)):
    """Mark a notification as read."""
    await db.execute(text("""
        UPDATE notifications
        SET is_read = TRUE, read_at = NOW()
        WHERE id = :id
    """), {"id": notification_id})
    await db.commit()
    return {"status": "ok", "notification_id": notification_id}


@router.post("/notifications/read-all")
async def mark_all_notifications_read(db: AsyncSession = Depends(get_db)):
    """Mark all notifications as read."""
    result = await db.execute(text("""
        UPDATE notifications
        SET is_read = TRUE, read_at = NOW()
        WHERE is_read = FALSE
    """))
    await db.commit()
    return {"status": "ok", "marked_count": result.rowcount}


@router.post("/notifications/{notification_id}/dismiss")
async def dismiss_notification(notification_id: str, db: AsyncSession = Depends(get_db)):
    """Dismiss a notification (hide it)."""
    await db.execute(text("""
        UPDATE notifications
        SET is_dismissed = TRUE
        WHERE id = :id
    """), {"id": notification_id})
    await db.commit()
    return {"status": "ok", "notification_id": notification_id}


@router.post("/notifications", response_model=Notification)
async def create_notification(
    notification: NotificationCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new notification. Sends email for critical severity."""
    import json

    # Insert notification
    result = await db.execute(text("""
        INSERT INTO notifications (site_id, appliance_id, severity, category, title, message, metadata)
        VALUES (:site_id, :appliance_id, :severity, :category, :title, :message, :metadata)
        RETURNING id, site_id, appliance_id, severity, category, title, message, metadata, is_read, is_dismissed, created_at, read_at
    """), {
        "site_id": notification.site_id,
        "appliance_id": notification.appliance_id,
        "severity": notification.severity,
        "category": notification.category,
        "title": notification.title,
        "message": notification.message,
        "metadata": json.dumps(notification.metadata or {}),
    })
    await db.commit()

    row = result.fetchone()

    # Send email for critical alerts
    if notification.severity == "critical":
        send_critical_alert(
            title=notification.title,
            message=notification.message,
            site_id=notification.site_id,
            category=notification.category,
            metadata=notification.metadata
        )

    notif = Notification(
        id=str(row.id),
        site_id=row.site_id,
        appliance_id=row.appliance_id,
        severity=row.severity,
        category=row.category,
        title=row.title,
        message=row.message,
        metadata=json.loads(row.metadata) if row.metadata else {},
        is_read=row.is_read,
        is_dismissed=row.is_dismissed,
        created_at=row.created_at,
        read_at=row.read_at
    )

    # Broadcast to connected dashboard clients
    try:
        await broadcast_event("notification_created", {
            "id": notif.id,
            "site_id": notif.site_id,
            "appliance_id": notif.appliance_id,
            "severity": notif.severity,
            "category": notif.category,
            "title": notif.title,
            "message": notif.message,
            "metadata": notif.metadata,
            "is_read": notif.is_read,
            "is_dismissed": notif.is_dismissed,
            "created_at": notif.created_at.isoformat() if notif.created_at else None,
        })
    except Exception as e:
        logger.warning(f"Failed to broadcast notification event: {e}")

    return notif


# =============================================================================
# AUTHENTICATION ENDPOINTS
# =============================================================================

from . import auth as auth_module

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    success: bool
    user: Optional[dict] = None
    error: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    username: str
    displayName: str
    role: str


@auth_router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    """Authenticate user and return session token.

    Sets an HTTP-only cookie for secure token storage.
    Also returns token in body for backwards compatibility.
    """
    ip_address = http_request.client.host if http_request.client else None
    user_agent = http_request.headers.get("user-agent")

    success, token, result = await auth_module.authenticate_user(
        db,
        request.username,
        request.password,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    if success:
        # Set HTTP-only secure cookie for session
        response.set_cookie(
            key="session_token",
            value=token,
            httponly=True,
            secure=True,  # Only sent over HTTPS
            samesite="strict",  # Prevents CSRF
            max_age=86400,  # 24 hours
            path="/",
        )
        return LoginResponse(success=True, user=result)
    else:
        return LoginResponse(success=False, error=result.get("error", "Authentication failed"))


@auth_router.post("/logout")
async def logout(
    http_request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    """Invalidate session token and clear cookie."""
    # Try to get token from cookie first, then Authorization header
    token = http_request.cookies.get("session_token")
    if not token:
        auth_header = http_request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if token:
        ip_address = http_request.client.host if http_request.client else None
        success = await auth_module.logout(db, token, ip_address)
        # Clear the cookie
        response.delete_cookie(key="session_token", path="/")
        return {"success": success}

    raise HTTPException(status_code=401, detail="No token provided")


@auth_router.get("/me", response_model=Optional[UserResponse])
async def get_current_user(
    http_request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Validate session token and return current user.

    Accepts token from:
    1. HTTP-only cookie (preferred, more secure)
    2. Authorization header (backwards compatibility)
    """
    # Try cookie first (more secure), then Authorization header
    token = http_request.cookies.get("session_token")
    if not token:
        auth_header = http_request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(status_code=401, detail="No token provided")

    user = await auth_module.validate_session(db, token)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return UserResponse(
        id=user["id"],
        username=user["username"],
        displayName=user["displayName"],
        role=user["role"],
    )


@auth_router.get("/audit-logs")
async def get_audit_logs(
    limit: int = Query(100, le=500),
    _user: dict = Depends(auth_module.require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Get admin audit logs (requires admin role)."""
    logs = await auth_module.get_audit_logs(db, limit=limit)
    return {"logs": logs}


# =============================================================================
# ADMIN SETTINGS ENDPOINTS
# =============================================================================

class SystemSettingsModel(BaseModel):
    """System-wide settings."""
    # Display
    timezone: str = "America/New_York"
    date_format: str = "MM/DD/YYYY"
    # Session
    session_timeout_minutes: int = 60
    require_2fa: bool = False
    # Fleet
    auto_update_enabled: bool = True
    update_window_start: str = "02:00"
    update_window_end: str = "06:00"
    rollout_percentage: int = 5
    # Data Retention
    telemetry_retention_days: int = 90
    incident_retention_days: int = 365
    audit_log_retention_days: int = 730
    # Notifications
    email_notifications_enabled: bool = True
    slack_notifications_enabled: bool = False
    escalation_timeout_minutes: int = 60
    # API
    api_rate_limit: int = 100
    webhook_timeout_seconds: int = 30


async def ensure_settings_table(db: AsyncSession):
    """Ensure the system_settings table exists."""
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS system_settings (
            id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            settings JSONB NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by VARCHAR(255)
        )
    """))
    await db.execute(text("""
        INSERT INTO system_settings (id, settings)
        VALUES (1, '{}')
        ON CONFLICT (id) DO NOTHING
    """))
    await db.commit()


@router.get("/admin/settings", response_model=SystemSettingsModel)
async def get_system_settings(db: AsyncSession = Depends(get_db)):
    """Get current system settings."""
    await ensure_settings_table(db)

    result = await db.execute(text(
        "SELECT settings FROM system_settings WHERE id = 1"
    ))
    row = result.fetchone()

    if row and row.settings:
        defaults = SystemSettingsModel()
        stored = row.settings if isinstance(row.settings, dict) else {}
        return SystemSettingsModel(**{**defaults.model_dump(), **stored})

    return SystemSettingsModel()


@router.put("/admin/settings", response_model=SystemSettingsModel)
async def update_system_settings(
    settings: SystemSettingsModel,
    db: AsyncSession = Depends(get_db)
):
    """Update system settings."""
    await ensure_settings_table(db)

    import json
    await db.execute(
        text("""
            UPDATE system_settings
            SET settings = :settings::jsonb,
                updated_at = NOW()
            WHERE id = 1
        """),
        {"settings": json.dumps(settings.model_dump())}
    )
    await db.commit()

    return settings


@router.post("/admin/settings/purge-telemetry")
async def purge_old_telemetry(db: AsyncSession = Depends(get_db)):
    """Purge telemetry data older than retention period."""
    settings = await get_system_settings(db)
    retention_days = settings.telemetry_retention_days

    # SECURITY: Use parameterized query to prevent SQL injection
    result = await db.execute(
        text("DELETE FROM execution_telemetry WHERE created_at < NOW() - INTERVAL '1 day' * :days"),
        {"days": retention_days}
    )
    await db.commit()

    return {"deleted": result.rowcount, "retention_days": retention_days}


@router.post("/admin/settings/reset-learning")
async def reset_learning_data(db: AsyncSession = Depends(get_db)):
    """Reset all learning data (patterns and L1 rules)."""
    patterns_result = await db.execute(text("DELETE FROM patterns"))
    rules_result = await db.execute(text(
        "DELETE FROM l1_rules WHERE promoted_from_l2 = true"
    ))
    await db.commit()

    return {
        "patterns_deleted": patterns_result.rowcount,
        "rules_deleted": rules_result.rowcount
    }
