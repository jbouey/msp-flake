"""FastAPI routes for Central Command Dashboard.

All endpoints for the dashboard including fleet, incidents, runbooks,
learning loop, onboarding, stats, and command interface.

This module uses the central database when available, falling back
to mock data for demo/development purposes.
"""

import json
import logging
import secrets
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
    CoverageGap,
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
    StatsDeltas,
    ClientStats,
    CommandRequest,
    CommandResponse,
    Severity,
    ResolutionLevel,
    HealthStatus,
    CheckinStatus,
    ComplianceChecks,
    L2TestRequest,
    L2DecisionResponse,
    L2ConfigResponse,
)
from .fleet import get_fleet_overview as _get_real_fleet_overview, get_client_detail as _get_real_client_detail
from .tenant_middleware import admin_connection
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
from .auth import check_site_access_sa, check_site_access_pool


# ---- Safe enum converters (prevent crashes on unknown DB values) ----


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
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Get all clients with aggregated health scores."""
    # Get site data with org info
    org_scope = user.get("org_scope")
    if org_scope:
        where_clause = "WHERE s.status != 'inactive' AND s.client_org_id = ANY(:org_scope_ids)"
    else:
        where_clause = "WHERE s.status != 'inactive'"
    query_str = f"""
        SELECT
            s.site_id,
            s.clinic_name as name,
            s.status,
            s.client_org_id,
            co.name as org_name,
            COUNT(sa.id) as appliance_count,
            COUNT(sa.id) FILTER (WHERE sa.status = 'online') as online_count,
            MAX(sa.last_checkin) as last_checkin
        FROM sites s
        LEFT JOIN site_appliances sa ON sa.site_id = s.site_id
        LEFT JOIN client_orgs co ON co.id = s.client_org_id
        {where_clause}
        GROUP BY s.site_id, s.clinic_name, s.status, s.client_org_id, co.name
        ORDER BY MAX(sa.last_checkin) DESC NULLS LAST
        LIMIT :limit OFFSET :offset
    """
    params = {"limit": limit, "offset": offset}
    if org_scope:
        params["org_scope_ids"] = org_scope

    result = await db.execute(text(query_str), params)
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
            status=row.status or "online",
            client_org_id=str(row.client_org_id) if row.client_org_id else None,
            org_name=row.org_name,
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
async def get_client_detail(site_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
    """Get detailed view of a single client."""
    await check_site_access_sa(db, user, site_id)
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
async def get_client_appliances(site_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
    """Get all appliances for a client."""
    await check_site_access_sa(db, user, site_id)
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
    limit: int = Query(default=50, ge=1, le=200),
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
    incidents = await get_incidents_from_db(db, site_id=site_id, limit=limit, offset=offset, resolved=resolved, level=level)

    return [
        Incident(
            id=str(i["id"]),
            site_id=i["site_id"],
            hostname=i.get("hostname", ""),
            check_type=i.get("check_type") or i.get("incident_type") or "unknown",
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
        check_type=row.check_type or getattr(row, 'incident_type', None) or "unknown",
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
# INCIDENT ACTION ENDPOINTS (Resolve / Escalate / Suppress)
# =============================================================================


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident(
    incident_id: str,
    user: dict = Depends(auth_module.require_auth),
):
    """Manually resolve an incident.

    Sets status to 'resolved', resolution_tier to 'manual', and records
    who resolved it and when.
    """
    from .fleet import get_pool

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            "SELECT id, status FROM incidents WHERE id = $1",
            incident_id,
        )
        if not row:
            raise HTTPException(404, "Incident not found")
        if row["status"] == "resolved":
            raise HTTPException(400, "Incident already resolved")

        await conn.execute("""
            UPDATE incidents
            SET status = 'resolved',
                resolved = true,
                resolved_at = NOW(),
                resolution_tier = 'manual',
                updated_at = NOW()
            WHERE id = $1
        """, incident_id)

    logger.info(f"Incident {incident_id} manually resolved by admin")
    await broadcast_event("incident_resolved", {"incident_id": incident_id})
    return {"status": "resolved", "incident_id": incident_id}


class EscalateRequest(BaseModel):
    notes: Optional[str] = None


@router.post("/incidents/{incident_id}/escalate")
async def escalate_incident(
    incident_id: str,
    body: EscalateRequest = EscalateRequest(),
    user: dict = Depends(auth_module.require_auth),
):
    """Escalate an incident to L3 by creating an escalation ticket.

    Creates a new escalation ticket linked to this incident and marks
    the incident resolution_tier as L3.
    """
    from .fleet import get_pool

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow("""
            SELECT i.id, i.status, i.site_id, i.check_type, i.hostname,
                   i.severity, i.appliance_id
            FROM incidents i
            WHERE i.id = $1
        """, incident_id)

        if not row:
            raise HTTPException(404, "Incident not found")
        if row["status"] == "resolved":
            raise HTTPException(400, "Cannot escalate a resolved incident")

        ticket_id = str(secrets.token_hex(8))
        await conn.execute("""
            INSERT INTO escalation_tickets (
                id, incident_id, site_id, check_type, hostname,
                severity, status, escalation_reason, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, 'open', $7, NOW(), NOW()
            )
        """,
            ticket_id,
            incident_id,
            row["site_id"],
            row.get("check_type") or "unknown",
            row.get("hostname") or "",
            row["severity"],
            body.notes or "Manual escalation from admin dashboard",
        )

        await conn.execute("""
            UPDATE incidents
            SET resolution_tier = 'L3', updated_at = NOW()
            WHERE id = $1
        """, incident_id)

    logger.info(f"Incident {incident_id} escalated to L3, ticket {ticket_id}")
    await broadcast_event("incident_escalated", {
        "incident_id": incident_id,
        "ticket_id": ticket_id,
    })
    return {"status": "escalated", "incident_id": incident_id, "ticket_id": ticket_id}


@router.post("/incidents/{incident_id}/suppress")
async def suppress_incident(
    incident_id: str,
    user: dict = Depends(auth_module.require_auth),
):
    """Suppress future alerts for this check_type + hostname for 24 hours.

    Creates a compliance exception with a 1-day duration scoped to the
    check type and hostname of the incident.
    """
    from .fleet import get_pool

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow("""
            SELECT i.id, i.site_id, i.check_type, i.hostname
            FROM incidents i
            WHERE i.id = $1
        """, incident_id)

        if not row:
            raise HTTPException(404, "Incident not found")

        check_type = row.get("check_type") or "unknown"
        hostname = row.get("hostname") or ""

        exception_id = str(secrets.token_hex(8))
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=24)

        await conn.execute("""
            INSERT INTO compliance_exceptions (
                id, site_id, scope_type, item_id, device_filter,
                requested_by, approved_by, approval_date, approval_tier,
                start_date, expiration_date, requires_renewal,
                reason, risk_accepted_by, action,
                created_at, updated_at
            ) VALUES (
                $1, $2, 'check', $3, $4,
                'admin', 'admin', $5, 'admin',
                $5, $6, false,
                $7, 'admin', 'suppress_alert',
                $5, $5
            )
        """,
            exception_id,
            row["site_id"],
            check_type,
            f"hostname:{hostname}" if hostname else None,
            now,
            expires,
            f"24h suppression for {check_type} on {hostname or 'all hosts'} from incident {incident_id}",
        )

    logger.info(f"Incident {incident_id} suppressed 24h: {check_type} on {hostname}")
    await broadcast_event("incident_suppressed", {
        "incident_id": incident_id,
        "check_type": check_type,
        "hostname": hostname,
        "expires_at": expires.isoformat(),
    })
    return {
        "status": "suppressed",
        "incident_id": incident_id,
        "check_type": check_type,
        "hostname": hostname,
        "expires_at": expires.isoformat(),
    }


# =============================================================================
# EVENTS ENDPOINTS (Compliance Bundles)
# =============================================================================

@router.get("/events")
async def get_events(
    site_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
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
    limit: int = Query(default=20, ge=1, le=100),
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
            site_id=c.get("site_id"),
            site_name=c.get("site_name"),
            description=c.get("description") or "",
            occurrences=c["occurrences"],
            success_rate=c["success_rate"],
            avg_resolution_time_ms=c.get("avg_resolution_time_ms", 0),
            proposed_rule=c.get("proposed_rule"),
            first_seen=c.get("first_seen"),
            last_seen=c.get("last_seen"),
            impact_count_7d=c.get("impact_count_7d", 0),
        )
        for c in candidates
    ]


@router.get("/learning/coverage-gaps", response_model=List[CoverageGap])
async def get_coverage_gaps(db: AsyncSession = Depends(get_db)):
    """Get check_types seen in telemetry that lack L1 rules."""
    from .db_queries import get_coverage_gaps_from_db
    gaps = await get_coverage_gaps_from_db(db)
    return [CoverageGap(**g) for g in gaps]


@router.get("/learning/history", response_model=List[PromotionHistory])
async def get_promotion_history(limit: int = Query(default=20, ge=1, le=100), db: AsyncSession = Depends(get_db)):
    """Get recently promoted L2->L1 patterns from learning_promotion_candidates."""
    result = await db.execute(text("""
        SELECT
            lpc.id,
            lpc.pattern_signature,
            COALESCE(lpc.custom_rule_name, lpc.recommended_action,
                     'L1-' || LEFT(lpc.id::text, 8)) as rule_id,
            lpc.approved_at as promoted_at,
            COALESCE(exec_stats.total, 0) as executions_since,
            COALESCE(exec_stats.success_pct, 0) as success_rate
        FROM learning_promotion_candidates lpc
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE et.success) * 100.0 / NULLIF(COUNT(*), 0) as success_pct
            FROM execution_telemetry et
            WHERE et.incident_type = split_part(lpc.pattern_signature, ':', 1)
            AND et.created_at > lpc.approved_at
        ) exec_stats ON true
        WHERE lpc.approval_status = 'approved'
        AND lpc.approved_at IS NOT NULL
        ORDER BY lpc.approved_at DESC
        LIMIT :limit
    """), {"limit": limit})

    return [
        PromotionHistory(
            id=str(row.id),
            pattern_signature=row.pattern_signature,
            rule_id=row.rule_id,
            promoted_at=row.promoted_at,
            post_promotion_success_rate=float(row.success_rate or 0),
            executions_since_promotion=int(row.executions_since or 0),
        )
        for row in result.fetchall()
    ]


@router.post("/learning/promote/{pattern_id}")
async def promote_pattern(
    pattern_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Manually trigger promotion of a pattern to L1.

    Supports both legacy patterns table and aggregated_pattern_stats IDs.
    """
    # First try legacy patterns table
    from .db_queries import promote_pattern_in_db
    rule_id = await promote_pattern_in_db(db, pattern_id)
    if rule_id:
        return {"status": "promoted", "pattern_id": pattern_id, "new_rule_id": rule_id}

    # Try aggregated_pattern_stats (new path)
    result = await db.execute(text("""
        SELECT id, pattern_signature, site_id, recommended_action
        FROM aggregated_pattern_stats
        WHERE id::text = :pid AND promotion_eligible = true
    """), {"pid": pattern_id})
    aps_row = result.fetchone()

    if not aps_row:
        raise HTTPException(status_code=404, detail=f"Pattern {pattern_id} not found")

    # IDOR check: ensure org-scoped user can access this pattern's site
    await check_site_access_sa(db, user, aps_row.site_id)

    # Parse check_type from pattern_signature (format: "check_type:runbook_id")
    parts = aps_row.pattern_signature.split(":")
    incident_type = parts[0] if parts else aps_row.pattern_signature
    runbook_id = parts[1] if len(parts) > 1 else aps_row.recommended_action or ""

    rule_id = f"L1-AUTO-{incident_type.upper().replace('_', '-')[:20]}"

    try:
        # Create L1 rule
        await db.execute(text("""
            INSERT INTO l1_rules (rule_id, incident_pattern, runbook_id, confidence, promoted_from_l2, enabled)
            VALUES (:rule_id, :pattern, :runbook_id, 0.95, true, true)
            ON CONFLICT (rule_id) DO NOTHING
        """), {
            "rule_id": rule_id,
            "pattern": json.dumps({"incident_type": incident_type}),
            "runbook_id": runbook_id,
        })

        # Mark as no longer eligible
        await db.execute(text("""
            UPDATE aggregated_pattern_stats SET promotion_eligible = false WHERE id = :pid
        """), {"pid": int(pattern_id)})

        # Record in learning_promotion_candidates
        await db.execute(text("""
            INSERT INTO learning_promotion_candidates (
                site_id, pattern_signature, approval_status,
                approved_at, custom_rule_name
            ) VALUES (:site_id, :sig, 'approved', NOW(), :rule_id)
            ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
                approval_status = 'approved', approved_at = NOW(),
                custom_rule_name = :rule_id
        """), {
            "site_id": aps_row.site_id,
            "sig": aps_row.pattern_signature,
            "rule_id": rule_id,
        })

        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return {"status": "promoted", "pattern_id": pattern_id, "new_rule_id": rule_id}


@router.post("/learning/reject/{pattern_id}")
async def reject_pattern(
    pattern_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Reject a promotion candidate, marking it as dismissed."""
    # Try legacy patterns table first
    result = await db.execute(
        text("SELECT pattern_id, status FROM patterns WHERE pattern_id = :pid"),
        {"pid": pattern_id}
    )
    pattern = result.fetchone()
    if pattern:
        await db.execute(text("""
            UPDATE patterns SET status = 'rejected' WHERE pattern_id = :pid
        """), {"pid": pattern_id})
        await db.commit()
        return {"status": "rejected", "pattern_id": pattern_id}

    # Try aggregated_pattern_stats
    result = await db.execute(text("""
        SELECT id, pattern_signature, site_id
        FROM aggregated_pattern_stats
        WHERE id::text = :pid AND promotion_eligible = true
    """), {"pid": pattern_id})
    aps_row = result.fetchone()

    if not aps_row:
        raise HTTPException(status_code=404, detail=f"Pattern {pattern_id} not found")

    # IDOR check: ensure org-scoped user can access this pattern's site
    await check_site_access_sa(db, user, aps_row.site_id)

    # Mark as no longer eligible
    await db.execute(text("""
        UPDATE aggregated_pattern_stats SET promotion_eligible = false WHERE id = :pid
    """), {"pid": int(pattern_id)})

    # Record rejection
    await db.execute(text("""
        INSERT INTO learning_promotion_candidates (
            site_id, pattern_signature, approval_status, rejection_reason
        ) VALUES (:site_id, :sig, 'rejected', 'Manually rejected by admin')
        ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
            approval_status = 'rejected', rejection_reason = 'Manually rejected by admin'
    """), {"site_id": aps_row.site_id, "sig": aps_row.pattern_signature})

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
async def advance_stage(
    client_id: str,
    request: StageAdvance,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Move client to next stage with transition validation."""
    await check_site_access_sa(db, user, client_id)
    now = datetime.now(timezone.utc)
    stage_val = request.new_stage.value

    # Allowed forward transitions (each stage can advance to the next, or skip max 1)
    STAGE_ORDER = [
        'lead', 'discovery', 'proposal', 'contract', 'intake', 'creds',
        'shipped', 'received', 'connectivity', 'scanning', 'baseline',
        'compliant', 'active',
    ]

    # Get current stage
    current = await db.execute(
        text("SELECT onboarding_stage FROM sites WHERE site_id = :client_id"),
        {"client_id": client_id}
    )
    current_row = current.fetchone()
    if not current_row:
        raise HTTPException(status_code=404, detail=f"Site {client_id} not found")

    current_stage = current_row.onboarding_stage or 'lead'

    # Validate transition: allow forward movement (skip up to 2 stages) or backward by 1
    if current_stage in STAGE_ORDER and stage_val in STAGE_ORDER:
        current_idx = STAGE_ORDER.index(current_stage)
        new_idx = STAGE_ORDER.index(stage_val)
        if new_idx > current_idx + 3:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot skip from '{current_stage}' to '{stage_val}'. Max 3 stages forward."
            )
        if new_idx < current_idx - 1:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot go back from '{current_stage}' to '{stage_val}'. Max 1 stage backward."
            )

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
async def update_blockers(
    client_id: str,
    request: BlockerUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Update blockers for a client."""
    await check_site_access_sa(db, user, client_id)
    await db.execute(text("""
        UPDATE sites SET blockers = :blockers WHERE site_id = :client_id
    """), {"blockers": json.dumps(request.blockers), "client_id": client_id})
    await db.commit()
    return {"status": "updated", "client_id": client_id, "blockers": request.blockers}


@router.post("/onboarding/{client_id}/note")
async def add_note(
    client_id: str,
    request: NoteAdd,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Add a note to client's onboarding record."""
    await check_site_access_sa(db, user, client_id)
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

    # Get Go agent stats and drift check count from asyncpg pool
    total_go_agents = 0
    active_drift_checks = 47  # default: all known check types
    try:
        from .fleet import get_pool
        from .tenant_middleware import admin_connection
        pool = await get_pool()
        async with admin_connection(pool) as conn:
            agent_row = await conn.fetchrow(
                "SELECT COUNT(*) as cnt FROM go_agents WHERE status = 'connected'"
            )
            total_go_agents = agent_row["cnt"] if agent_row else 0

            # Count enabled drift checks across all active sites
            drift_row = await conn.fetchrow("""
                SELECT COUNT(DISTINCT check_type) as cnt
                FROM site_drift_config WHERE enabled = true
            """)
            if drift_row and drift_row["cnt"] > 0:
                active_drift_checks = drift_row["cnt"]
    except Exception:
        pass

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
        active_drift_checks=active_drift_checks,
        total_go_agents=total_go_agents,
        computed_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/stats/deltas", response_model=StatsDeltas)
async def get_stats_deltas(db: AsyncSession = Depends(get_db)):
    """Week-over-week delta indicators for dashboard KPI cards.

    Compares current metrics against 7-day-ago values:
    - compliance_delta: current avg compliance score minus 7d-ago score
    - incidents_24h_delta: current 24h count minus 7d-ago 24h count
    - l1_rate_delta: current L1 resolution % minus 7d-ago %
    - clients_delta: current site count minus 7d-ago count
    """
    try:
        # --- Current compliance score (same logic as get_global_stats) ---
        comp_now = await db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant')) as passed,
                COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant', 'fail', 'non_compliant', 'warning')) as total
            FROM compliance_bundles cb,
                 jsonb_array_elements(cb.checks) as c
            WHERE cb.created_at > NOW() - INTERVAL '24 hours'
              AND jsonb_array_length(cb.checks) > 0
        """))
        cn = comp_now.fetchone()
        compliance_now = round((cn.passed or 0) / max(cn.total or 1, 1) * 100, 1)

        # --- 7-day-ago compliance score (24h window starting 7 days ago) ---
        comp_prev = await db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant')) as passed,
                COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant', 'fail', 'non_compliant', 'warning')) as total
            FROM compliance_bundles cb,
                 jsonb_array_elements(cb.checks) as c
            WHERE cb.created_at BETWEEN NOW() - INTERVAL '8 days' AND NOW() - INTERVAL '7 days'
              AND jsonb_array_length(cb.checks) > 0
        """))
        cp = comp_prev.fetchone()
        compliance_prev = round((cp.passed or 0) / max(cp.total or 1, 1) * 100, 1)

        compliance_delta = round(compliance_now - compliance_prev, 1)

        # --- Incident counts: 24h now vs 24h window 7 days ago ---
        inc_row = await db.execute(text("""
            SELECT
                COUNT(*) FILTER (
                    WHERE reported_at > NOW() - INTERVAL '24 hours'
                ) as now_24h,
                COUNT(*) FILTER (
                    WHERE reported_at BETWEEN NOW() - INTERVAL '8 days' AND NOW() - INTERVAL '7 days'
                ) as prev_24h,
                COUNT(*) FILTER (
                    WHERE resolution_tier = 'L1' AND status = 'resolved'
                      AND reported_at > NOW() - INTERVAL '30 days'
                ) as l1_now,
                COUNT(*) FILTER (
                    WHERE status = 'resolved'
                      AND reported_at > NOW() - INTERVAL '30 days'
                ) as resolved_now
            FROM incidents
        """))
        ir = inc_row.fetchone()
        incidents_24h_delta = (ir.now_24h or 0) - (ir.prev_24h or 0)

        # --- L1 resolution rate: current vs 7d-ago window ---
        l1_rate_now = round((ir.l1_now or 0) / max(ir.resolved_now or 1, 1) * 100, 1)

        l1_prev_row = await db.execute(text("""
            SELECT
                COUNT(*) FILTER (
                    WHERE resolution_tier = 'L1' AND status = 'resolved'
                ) as l1_prev,
                COUNT(*) FILTER (
                    WHERE status = 'resolved'
                ) as resolved_prev
            FROM incidents
            WHERE reported_at BETWEEN NOW() - INTERVAL '37 days' AND NOW() - INTERVAL '7 days'
        """))
        lpr = l1_prev_row.fetchone()
        l1_rate_prev = round((lpr.l1_prev or 0) / max(lpr.resolved_prev or 1, 1) * 100, 1)
        l1_rate_delta = round(l1_rate_now - l1_rate_prev, 1)

        # --- Client count delta ---
        sites_now = await db.execute(text("SELECT COUNT(*) as cnt FROM sites"))
        sn = sites_now.fetchone()

        # Sites that existed 7 days ago (created_at <= 7 days ago)
        sites_prev = await db.execute(text("""
            SELECT COUNT(*) as cnt FROM sites
            WHERE created_at <= NOW() - INTERVAL '7 days'
        """))
        sp = sites_prev.fetchone()
        clients_delta = (sn.cnt or 0) - (sp.cnt or 0)

        return StatsDeltas(
            compliance_delta=compliance_delta,
            incidents_24h_delta=incidents_24h_delta,
            l1_rate_delta=l1_rate_delta,
            clients_delta=clients_delta,
        )
    except Exception as e:
        logger.warning(f"Stats deltas query failed: {e}")
        return StatsDeltas()


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
                        WHERE sa.last_checkin > NOW() - INTERVAL '15 minutes'
                    ) as online_count,
                    MAX(sa.last_checkin) as last_checkin
                FROM sites s
                LEFT JOIN site_appliances sa ON sa.site_id = s.site_id
                GROUP BY s.site_id, s.clinic_name
            ),
            site_incidents AS (
                SELECT
                    a.site_id,
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
                JOIN appliances a ON a.id = i.appliance_id
                WHERE i.reported_at > NOW() - INTERVAL '30 days'
                GROUP BY a.site_id
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

        site_join = "JOIN appliances a ON a.id = i.appliance_id" if site_id else ""
        site_filter = "AND a.site_id = :site_id" if site_id else ""
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
            {site_join}
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

        site_join = "JOIN appliances a ON a.id = i.appliance_id" if site_id else ""
        site_filter = "AND a.site_id = :site_id" if site_id else ""
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
            {site_join}
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
            {site_join}
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
            {site_join}
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
                i.id, a.site_id, i.incident_type, i.check_type, i.severity,
                i.reported_at, s.clinic_name
            FROM incidents i
            JOIN appliances a ON a.id = i.appliance_id
            LEFT JOIN sites s ON s.site_id = a.site_id
            WHERE i.resolution_tier = 'L3'
            AND i.status != 'resolved'
            ORDER BY i.reported_at DESC
            LIMIT 20
        """))
        l3_rows = l3_result.fetchall()

        # Repeat offenders: same check_type, same site, 3+ incidents in 24h (healing not sticking)
        repeat_result = await db.execute(text("""
            SELECT
                a.site_id, i.check_type, COUNT(*) as occurrences,
                MAX(i.reported_at) as latest,
                s.clinic_name
            FROM incidents i
            JOIN appliances a ON a.id = i.appliance_id
            LEFT JOIN sites s ON s.site_id = a.site_id
            WHERE i.reported_at > NOW() - INTERVAL '24 hours'
            AND i.resolution_tier = 'L1'
            GROUP BY a.site_id, i.check_type, s.clinic_name
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
async def get_client_stats(site_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
    """Get statistics for a specific client."""
    await check_site_access_sa(db, user, site_id)
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
        fleet = await _get_real_fleet_overview()
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
        detail = await _get_real_client_detail(site_id)
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
    limit: int = Query(50, ge=1, le=200),
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
        # Check if MFA is required (password was correct but 2FA needed)
        if isinstance(result, dict) and result.get("status") == "mfa_required":
            return LoginResponse(
                success=False,
                error="mfa_required",
                user={"mfa_token": result["mfa_token"]},
            )
        return LoginResponse(success=False, error=result.get("error", "Authentication failed"))


class VerifyTOTPRequest(BaseModel):
    mfa_token: str
    totp_code: str


@auth_router.post("/verify-totp")
async def verify_totp_login(
    body: VerifyTOTPRequest,
    http_request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    """Complete login after TOTP verification.

    Accepts the short-lived MFA pending token + TOTP code,
    verifies both, then creates the full session.
    """
    ip_address = http_request.client.host if http_request.client else None
    user_agent = http_request.headers.get("user-agent")

    success, token, result = await auth_module.complete_mfa_login(
        db,
        body.mfa_token,
        body.totp_code,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    if success:
        response.set_cookie(
            key="session_token",
            value=token,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=86400,
            path="/",
        )
        return LoginResponse(success=True, user=result)
    else:
        return LoginResponse(success=False, error=result.get("error", "TOTP verification failed"))


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
    limit: int = Query(100, ge=1, le=500),
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


@router.post("/admin/rules/{rule_id}/disable")
async def disable_promoted_rule(rule_id: str, db: AsyncSession = Depends(get_db)):
    """Disable a promoted L1 rule (rollback). Logs to audit trail."""
    result = await db.execute(
        text("SELECT rule_id, runbook_id, source, enabled FROM l1_rules WHERE rule_id = :rid"),
        {"rid": rule_id}
    )
    rule = result.fetchone()
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}")

    if not rule.enabled:
        return {"status": "already_disabled", "rule_id": rule_id}

    await db.execute(
        text("UPDATE l1_rules SET enabled = false WHERE rule_id = :rid"),
        {"rid": rule_id}
    )
    # Audit log
    await db.execute(text("""
        INSERT INTO audit_log (event_type, actor, resource_type, resource_id, details)
        VALUES ('rule.disabled', 'admin', 'l1_rule', :rid,
                :details::jsonb)
    """), {
        "rid": rule_id,
        "details": json.dumps({
            "runbook_id": rule.runbook_id,
            "source": rule.source,
            "action": "manual_rollback",
        }),
    })
    await db.commit()

    return {"status": "disabled", "rule_id": rule_id, "runbook_id": rule.runbook_id}


@router.post("/admin/rules/{rule_id}/enable")
async def enable_rule(rule_id: str, db: AsyncSession = Depends(get_db)):
    """Re-enable a disabled L1 rule."""
    result = await db.execute(
        text("UPDATE l1_rules SET enabled = true WHERE rule_id = :rid RETURNING rule_id"),
        {"rid": rule_id}
    )
    row = result.fetchone()
    await db.commit()
    if not row:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}")
    return {"status": "enabled", "rule_id": rule_id}


# =============================================================================
# ORGANIZATION ENDPOINTS
# =============================================================================

@router.get("/organizations")
async def list_organizations(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: Optional[str] = Query(None, alias="status"),
):
    """List all organizations with site counts and aggregate health."""
    org_scope = user.get("org_scope")
    where_clause = "WHERE co.id = ANY(:org_scope_ids)" if org_scope else ""
    params = {}
    if org_scope:
        params["org_scope_ids"] = org_scope

    if status_filter:
        if where_clause:
            where_clause += " AND co.status = :status_filter"
        else:
            where_clause = "WHERE co.status = :status_filter"
        params["status_filter"] = status_filter

    count_result = await db.execute(text(f"""
        SELECT COUNT(*) FROM client_orgs co {where_clause}
    """), params)
    total = count_result.scalar()

    params["limit"] = limit
    params["offset"] = offset

    result = await db.execute(text(f"""
        SELECT
            co.id,
            co.name,
            co.primary_email,
            co.practice_type,
            co.provider_count,
            co.status,
            co.created_at,
            COUNT(DISTINCT s.site_id) as site_count,
            COUNT(DISTINCT sa.appliance_id) as appliance_count,
            MAX(sa.last_checkin) as last_checkin
        FROM client_orgs co
        LEFT JOIN sites s ON s.client_org_id = co.id
        LEFT JOIN site_appliances sa ON sa.site_id = s.site_id
        {where_clause}
        GROUP BY co.id, co.name, co.primary_email, co.practice_type,
                 co.provider_count, co.status, co.created_at
        ORDER BY co.name
        LIMIT :limit OFFSET :offset
    """), params)
    rows = result.fetchall()

    # Build site→org mapping from a single query (eliminates N+1)
    site_org_result = await db.execute(text(
        "SELECT site_id, client_org_id FROM sites WHERE client_org_id IS NOT NULL"
    ))
    site_org_map = {}
    for sr in site_org_result.fetchall():
        site_org_map.setdefault(str(sr.client_org_id), []).append(sr.site_id)

    all_compliance = await get_all_compliance_scores(db)

    orgs = []
    for row in rows:
        org_id_str = str(row.id)
        org_site_ids = site_org_map.get(org_id_str, [])
        org_scores = []
        for sid in org_site_ids:
            sc = all_compliance.get(sid, {})
            if sc.get("has_data"):
                org_scores.append(sc.get("score", 0))

        avg_compliance = (
            sum(org_scores) / len(org_scores) if org_scores else 0
        )

        orgs.append({
            "id": org_id_str,
            "name": row.name,
            "primary_email": row.primary_email,
            "practice_type": row.practice_type,
            "provider_count": row.provider_count,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "site_count": row.site_count,
            "appliance_count": row.appliance_count,
            "last_checkin": row.last_checkin.isoformat() if row.last_checkin else None,
            "avg_compliance": round(avg_compliance, 1),
        })

    return {"organizations": orgs, "count": len(orgs), "total": total, "limit": limit, "offset": offset}


@router.post("/organizations")
async def create_organization(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Create a new client organization."""
    body = await request.json()
    name = body.get("name", "").strip()
    email = body.get("primary_email", "").strip()
    if not name or not email:
        raise HTTPException(status_code=400, detail="Name and primary_email are required")

    result = await db.execute(text("""
        INSERT INTO client_orgs (name, primary_email, primary_phone, practice_type, provider_count, status)
        VALUES (:name, :email, :phone, :practice_type, :provider_count, 'active')
        RETURNING id, name, primary_email, created_at
    """), {
        "name": name,
        "email": email,
        "phone": body.get("primary_phone", ""),
        "practice_type": body.get("practice_type", ""),
        "provider_count": body.get("provider_count", 1),
    })
    row = result.fetchone()
    await db.commit()
    return {
        "status": "created",
        "id": str(row.id),
        "name": row.name,
        "primary_email": row.primary_email,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/organizations/{org_id}")
async def get_organization_detail(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Get organization detail with nested site list."""
    auth_module._check_org_access(user, org_id)
    # Get org info
    result = await db.execute(text("""
        SELECT id, name, primary_email, primary_phone, address_line1,
               city, state, postal_code, npi_number, practice_type,
               provider_count, status, created_at
        FROM client_orgs
        WHERE id = :org_id
    """), {"org_id": org_id})
    org = result.fetchone()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Get org's sites with appliance data
    sites_result = await db.execute(text("""
        SELECT
            s.site_id,
            s.clinic_name,
            s.tier,
            s.onboarding_stage,
            COUNT(sa.id) as appliance_count,
            COUNT(sa.id) FILTER (WHERE sa.status = 'online') as online_count,
            MAX(sa.last_checkin) as last_checkin
        FROM sites s
        LEFT JOIN site_appliances sa ON sa.site_id = s.site_id
        WHERE s.client_org_id = :org_id
        GROUP BY s.site_id, s.clinic_name, s.tier, s.onboarding_stage
        ORDER BY s.clinic_name
    """), {"org_id": org_id})
    site_rows = sites_result.fetchall()

    all_compliance = await get_all_compliance_scores(db)
    healing_cache = await get_all_healing_metrics(db)

    sites = []
    for row in site_rows:
        sc = all_compliance.get(row.site_id, {})
        sh = healing_cache.get(row.site_id, {})
        compliance_score = sc.get("score", 0) if sc.get("has_data") else 0

        # Live status
        live_status = "pending"
        if row.last_checkin:
            age = datetime.now(timezone.utc) - row.last_checkin
            if age < timedelta(minutes=15):
                live_status = "online"
            elif age < timedelta(hours=1):
                live_status = "stale"
            else:
                live_status = "offline"

        sites.append({
            "site_id": row.site_id,
            "clinic_name": row.clinic_name or row.site_id.replace('-', ' ').title(),
            "tier": row.tier or "standard",
            "onboarding_stage": row.onboarding_stage or "active",
            "appliance_count": row.appliance_count,
            "online_count": row.online_count,
            "live_status": live_status,
            "last_checkin": row.last_checkin.isoformat() if row.last_checkin else None,
            "compliance_score": round(compliance_score, 1),
            "healing_success_rate": sh.get("healing_success_rate", 0),
            "incidents_24h": sh.get("incidents_24h", 0),
        })

    return {
        "id": str(org.id),
        "name": org.name,
        "primary_email": org.primary_email,
        "primary_phone": org.primary_phone,
        "address": ", ".join(filter(None, [
            org.address_line1, org.city, org.state, org.postal_code
        ])) or None,
        "npi_number": org.npi_number,
        "practice_type": org.practice_type,
        "provider_count": org.provider_count,
        "status": org.status,
        "created_at": org.created_at.isoformat() if org.created_at else None,
        "site_count": len(sites),
        "sites": sites,
    }


@router.get("/organizations/{org_id}/available-sites")
async def get_available_sites(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Get sites not yet assigned to this org (or any org)."""
    result = await db.execute(text("""
        SELECT s.site_id, s.clinic_name, s.tier,
               sa.last_checkin, sa.status
        FROM sites s
        LEFT JOIN site_appliances sa ON sa.site_id = s.site_id
        WHERE s.client_org_id IS NULL OR s.client_org_id != :org_id
        ORDER BY s.clinic_name
    """), {"org_id": org_id})
    rows = result.fetchall()
    return {"sites": [
        {
            "site_id": r.site_id,
            "clinic_name": r.clinic_name or r.site_id,
            "tier": r.tier,
            "last_checkin": r.last_checkin.isoformat() if r.last_checkin else None,
            "status": r.status,
        }
        for r in rows
    ]}


@router.post("/organizations/{org_id}/sites")
async def assign_site_to_org(
    org_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Assign an existing site to an organization."""
    body = await request.json()
    site_id = body.get("site_id", "").strip()
    if not site_id:
        raise HTTPException(status_code=400, detail="site_id is required")
    await check_site_access_sa(db, user, site_id)

    # Verify org exists
    org = await db.execute(text("SELECT id FROM client_orgs WHERE id = :org_id"), {"org_id": org_id})
    if not org.fetchone():
        raise HTTPException(status_code=404, detail="Organization not found")

    # Verify site exists
    site = await db.execute(text("SELECT site_id FROM sites WHERE site_id = :site_id"), {"site_id": site_id})
    if not site.fetchone():
        raise HTTPException(status_code=404, detail="Site not found")

    await db.execute(text("""
        UPDATE sites SET client_org_id = :org_id WHERE site_id = :site_id
    """), {"org_id": org_id, "site_id": site_id})
    await db.commit()
    return {"status": "assigned", "site_id": site_id, "org_id": org_id}


@router.delete("/organizations/{org_id}/sites/{site_id}")
async def unassign_site_from_org(
    org_id: str,
    site_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Remove a site from an organization."""
    await check_site_access_sa(db, user, site_id)
    await db.execute(text("""
        UPDATE sites SET client_org_id = NULL
        WHERE site_id = :site_id AND client_org_id = :org_id
    """), {"org_id": org_id, "site_id": site_id})
    await db.commit()
    return {"status": "unassigned", "site_id": site_id}


@router.get("/organizations/{org_id}/health")
async def get_organization_health(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Get consolidated health metrics across all sites in an organization."""
    auth_module._check_org_access(user, org_id)

    # Verify org exists
    org_check = await db.execute(
        text("SELECT id FROM client_orgs WHERE id = :org_id"),
        {"org_id": org_id}
    )
    if not org_check.fetchone():
        raise HTTPException(status_code=404, detail="Organization not found")

    # Get org's site_ids
    sites_result = await db.execute(
        text("SELECT site_id FROM sites WHERE client_org_id = :org_id"),
        {"org_id": org_id}
    )
    site_ids = [r.site_id for r in sites_result.fetchall()]

    if not site_ids:
        return {
            "org_id": org_id,
            "compliance": {"score": 0, "has_data": False, "site_scores": {}, "bundle_score": 0},
            "go_agents": {"total_agents": 0, "active_agents": 0, "compliance_rate": None},
            "incidents": {"total_24h": 0, "total_7d": 0, "total_30d": 0, "by_severity": {}},
            "healing": {"success_rate": 0, "order_execution_rate": 0, "total_incidents": 0, "resolved_incidents": 0, "total_orders": 0, "completed_orders": 0},
            "fleet": {"total": 0, "online": 0, "stale": 0, "offline": 0},
            "categories": {},
        }

    # Compliance scores
    all_compliance = await get_all_compliance_scores(db)
    site_scores = {}
    scores_list = []
    for sid in site_ids:
        sc = all_compliance.get(sid, {})
        if sc.get("has_data"):
            site_scores[sid] = round(sc["score"], 1)
            scores_list.append(sc["score"])
    avg_score = round(sum(scores_list) / len(scores_list), 1) if scores_list else 0

    # Incident counts (24h, 7d, 30d)
    incident_result = await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as total_24h,
            COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '7 days') as total_7d,
            COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '30 days') as total_30d,
            COUNT(*) FILTER (WHERE severity = 'critical' AND created_at > NOW() - INTERVAL '7 days') as critical_7d,
            COUNT(*) FILTER (WHERE severity = 'high' AND created_at > NOW() - INTERVAL '7 days') as high_7d,
            COUNT(*) FILTER (WHERE severity = 'medium' AND created_at > NOW() - INTERVAL '7 days') as medium_7d,
            COUNT(*) FILTER (WHERE severity = 'low' AND created_at > NOW() - INTERVAL '7 days') as low_7d
        FROM incidents
        WHERE site_id = ANY(:site_ids)
    """), {"site_ids": site_ids})
    inc = incident_result.fetchone()

    # Healing metrics (mirrors db_queries.py get_all_healing_metrics pattern)
    healing_inc_result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE i.status = 'resolved') as resolved
        FROM incidents i
        WHERE i.site_id = ANY(:site_ids)
    """), {"site_ids": site_ids})
    heal_inc = healing_inc_result.fetchone()

    healing_ord_result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status = 'completed') as completed
        FROM admin_orders
        WHERE site_id = ANY(:site_ids)
    """), {"site_ids": site_ids})
    heal_ord = healing_ord_result.fetchone()

    healing_rate = round(
        (heal_inc.resolved / heal_inc.total * 100) if heal_inc.total > 0 else 100.0, 1
    )
    order_rate = round(
        (heal_ord.completed / heal_ord.total * 100) if heal_ord.total > 0 else 100.0, 1
    )

    # Fleet status
    fleet_result = await db.execute(text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE last_checkin > NOW() - INTERVAL '15 minutes') as online,
            COUNT(*) FILTER (
                WHERE last_checkin <= NOW() - INTERVAL '15 minutes'
                  AND last_checkin > NOW() - INTERVAL '1 hour'
            ) as stale,
            COUNT(*) FILTER (
                WHERE last_checkin IS NULL OR last_checkin <= NOW() - INTERVAL '1 hour'
            ) as offline
        FROM site_appliances
        WHERE site_id = ANY(:site_ids)
    """), {"site_ids": site_ids})
    fleet = fleet_result.fetchone()

    # Per-category compliance breakdown
    cat_result = await db.execute(text("""
        SELECT
            cb.check_type,
            COUNT(*) FILTER (WHERE cb.check_result = 'pass') as passes,
            COUNT(*) FILTER (WHERE cb.check_result = 'fail') as fails,
            COUNT(*) as total
        FROM compliance_bundles cb
        WHERE cb.site_id = ANY(:site_ids)
          AND cb.checked_at = (
              SELECT MAX(cb2.checked_at) FROM compliance_bundles cb2
              WHERE cb2.site_id = cb.site_id AND cb2.check_type = cb.check_type
          )
        GROUP BY cb.check_type
        ORDER BY cb.check_type
    """), {"site_ids": site_ids})
    categories = {}
    for row in cat_result.fetchall():
        categories[row.check_type] = {
            "passes": row.passes,
            "fails": row.fails,
            "total": row.total,
            "score": round(row.passes / row.total * 100, 1) if row.total > 0 else 0,
        }

    # Go agent compliance data across org sites
    go_agent_result = await db.execute(text("""
        SELECT sg.site_id,
               COALESCE(sg.total_agents, 0) as total_agents,
               COALESCE(sg.active_agents, 0) as active_agents,
               COALESCE(sg.overall_compliance_rate, 0) as compliance_rate
        FROM site_go_agent_summaries sg
        WHERE sg.site_id = ANY(:site_ids)
    """), {"site_ids": site_ids})
    go_agent_rows = go_agent_result.fetchall()

    org_agent_count = 0
    org_active_agents = 0
    go_scores_weighted_sum = 0.0
    go_total_agents_for_weight = 0
    for ga in go_agent_rows:
        org_agent_count += ga.total_agents
        org_active_agents += ga.active_agents
        if ga.active_agents > 0:
            go_scores_weighted_sum += float(ga.compliance_rate) * ga.active_agents
            go_total_agents_for_weight += ga.active_agents

    org_agent_compliance = round(go_scores_weighted_sum / go_total_agents_for_weight, 1) if go_total_agents_for_weight > 0 else None

    # Blend bundle compliance score with Go agent compliance
    if org_agent_compliance is not None and avg_score > 0:
        # Weight by data density: bundle sites vs active agent sites
        bundle_weight = len(scores_list)
        agent_weight = sum(1 for ga in go_agent_rows if ga.active_agents > 0)
        total_weight = bundle_weight + agent_weight
        blended_score = round((avg_score * bundle_weight + org_agent_compliance * agent_weight) / total_weight, 1) if total_weight > 0 else avg_score
    elif org_agent_compliance is not None:
        blended_score = org_agent_compliance
    else:
        blended_score = avg_score

    return {
        "org_id": org_id,
        "compliance": {
            "score": blended_score,
            "has_data": len(scores_list) > 0 or org_agent_compliance is not None,
            "site_scores": site_scores,
            "bundle_score": avg_score,
        },
        "go_agents": {
            "total_agents": org_agent_count,
            "active_agents": org_active_agents,
            "compliance_rate": org_agent_compliance,
        },
        "incidents": {
            "total_24h": inc.total_24h,
            "total_7d": inc.total_7d,
            "total_30d": inc.total_30d,
            "by_severity": {
                "critical": inc.critical_7d,
                "high": inc.high_7d,
                "medium": inc.medium_7d,
                "low": inc.low_7d,
            },
        },
        "healing": {
            "success_rate": healing_rate,
            "order_execution_rate": order_rate,
            "total_incidents": heal_inc.total,
            "resolved_incidents": heal_inc.resolved,
            "total_orders": heal_ord.total,
            "completed_orders": heal_ord.completed,
        },
        "fleet": {
            "total": fleet.total,
            "online": fleet.online,
            "stale": fleet.stale,
            "offline": fleet.offline,
        },
        "categories": categories,
    }


@router.get("/organizations/{org_id}/incidents")
async def get_organization_incidents(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
    site_id: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None, alias="incident_status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List incidents across all sites in an organization."""
    auth_module._check_org_access(user, org_id)

    # Build dynamic query
    conditions = ["s.client_org_id = :org_id"]
    params = {"org_id": org_id}

    if site_id:
        conditions.append("i.site_id = :site_id")
        params["site_id"] = site_id
    if severity:
        conditions.append("i.severity = :severity")
        params["severity"] = severity
    if status:
        conditions.append("i.status = :inc_status")
        params["inc_status"] = status

    where = " AND ".join(conditions)

    result = await db.execute(text(f"""
        SELECT i.id, i.site_id, s.clinic_name, i.incident_type, i.severity,
               i.status, i.created_at, i.resolved_at
        FROM incidents i
        JOIN sites s ON s.site_id = i.site_id
        WHERE {where}
        ORDER BY i.created_at DESC
        LIMIT :limit OFFSET :offset
    """), {**params, "limit": limit, "offset": offset})
    rows = result.fetchall()

    count_result = await db.execute(text(f"""
        SELECT COUNT(*) FROM incidents i
        JOIN sites s ON s.site_id = i.site_id
        WHERE {where}
    """), params)
    total = count_result.scalar()

    return {
        "incidents": [
            {
                "id": str(r.id),
                "site_id": r.site_id,
                "clinic_name": r.clinic_name or r.site_id,
                "incident_type": r.incident_type,
                "severity": r.severity,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# =============================================================================
# DRIFT CONFIG ENDPOINTS
# =============================================================================

def _check_platform(check_type: str) -> str:
    """Derive platform from check_type prefix."""
    if check_type.startswith("macos_"):
        return "macos"
    elif check_type.startswith("linux_"):
        return "linux"
    return "windows"


@router.get("/sites/{site_id}/compliance-health")
async def get_admin_compliance_health(
    site_id: str,
    user: dict = Depends(auth_module.require_auth),
):
    """Get compliance health breakdown for admin site detail view."""
    await check_site_access_pool(user, site_id)
    from .fleet import get_pool
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        site = await conn.fetchrow(
            "SELECT site_id, clinic_name FROM sites WHERE site_id = $1", site_id
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get disabled checks
        disabled = await conn.fetch(
            "SELECT check_type FROM site_drift_config WHERE site_id = $1 AND enabled = false", site_id
        )
        disabled_set = {r["check_type"] for r in disabled}
        if not disabled:
            defaults = await conn.fetch(
                "SELECT check_type FROM site_drift_config WHERE site_id = '__defaults__' AND enabled = false"
            )
            disabled_set = {r["check_type"] for r in defaults}

        # Expanded category map covering Windows bundles + Linux/NixOS incidents
        categories = {
            "patching": ["nixos_generation", "windows_update", "linux_patching",
                        "linux_unattended_upgrades", "linux_kernel_params"],
            "antivirus": ["windows_defender", "windows_defender_exclusions",
                         "defender_exclusions"],
            "backup": ["backup_status", "windows_backup_status"],
            "logging": ["audit_logging", "windows_audit_policy", "linux_audit",
                       "linux_logging", "security_audit", "audit_policy",
                       "linux_log_forwarding"],
            "firewall": ["firewall", "windows_firewall_status", "firewall_status",
                        "linux_firewall", "network_profile", "net_unexpected_ports"],
            "encryption": ["bitlocker", "windows_bitlocker_status", "linux_crypto",
                          "windows_smb_signing", "bitlocker_status", "smb_signing",
                          "smb1_protocol"],
            "access_control": ["rogue_admin_users", "linux_accounts", "windows_password_policy",
                              "linux_permissions", "linux_ssh_config", "windows_screen_lock_policy",
                              "screen_lock", "screen_lock_policy", "password_policy",
                              "guest_account", "rdp_nla", "rogue_scheduled_tasks"],
            "services": ["critical_services", "linux_services", "windows_service_dns",
                        "windows_service_netlogon", "windows_service_spooler",
                        "windows_service_w32time", "windows_service_wuauserv", "agent_status",
                        "service_dns", "service_netlogon", "service_status",
                        "spooler_service", "linux_failed_services", "ntp_sync",
                        "winrm", "dns_config", "net_dns_resolution",
                        "net_expected_service", "net_host_reachability"],
        }
        reverse_map = {}
        for cat, types in categories.items():
            for ct in types:
                reverse_map[ct] = cat

        # --- Source 1: Compliance bundles (Windows drift scans) ---
        bundles = await conn.fetch("""
            SELECT checks FROM compliance_bundles
            WHERE site_id = $1
            ORDER BY checked_at DESC LIMIT 50
        """, site_id)

        cat_pass: dict = {cat: 0 for cat in categories}
        cat_fail: dict = {cat: 0 for cat in categories}
        cat_warn: dict = {cat: 0 for cat in categories}
        total_passed = 0
        total_failed = 0
        total_warnings = 0

        for bundle in bundles:
            checks = bundle["checks"] or []
            if isinstance(checks, str):
                import json as _json
                try:
                    checks = _json.loads(checks)
                except Exception:
                    continue
            for check in checks:
                if not isinstance(check, dict):
                    continue
                ct = check.get("check", "")
                if ct in disabled_set:
                    continue
                status = (check.get("status") or "").lower()
                cat = reverse_map.get(ct)
                if status in ("compliant", "pass"):
                    total_passed += 1
                    if cat:
                        cat_pass[cat] += 1
                elif status == "warning":
                    total_warnings += 1
                    if cat:
                        cat_warn[cat] += 1
                elif status in ("non_compliant", "fail"):
                    total_failed += 1
                    if cat:
                        cat_fail[cat] += 1

        # --- Source 2: Active incidents (ALL platforms: Linux, NixOS, Windows) ---
        # Count distinct compliance issues (unique check_type per device), not raw alerts
        incident_rows = await conn.fetch("""
            SELECT i.check_type, count(DISTINCT i.appliance_id) as devices_affected
            FROM incidents i
            JOIN appliances a ON a.id = i.appliance_id
            WHERE a.site_id = $1 AND i.resolved_at IS NULL
            GROUP BY i.check_type
        """, site_id)

        incident_fails = 0
        for row in incident_rows:
            ct = row["check_type"]
            if ct in disabled_set:
                continue
            cnt = row["devices_affected"]  # 1 fail per device with this issue
            cat = reverse_map.get(ct)
            if cat:
                cat_fail[cat] += cnt
                total_failed += cnt
                incident_fails += cnt
                incident_fails += cnt

        # --- Source 3: Go agent check results (workstation-level compliance) ---
        go_agent_rows = await conn.fetch("""
            SELECT checks_passed, checks_total, compliance_percentage
            FROM go_agents
            WHERE site_id = $1 AND status IN ('connected', 'active')
              AND checks_total > 0
        """, site_id)

        go_agent_checks_passed = 0
        go_agent_checks_total = 0
        for ga_row in go_agent_rows:
            go_agent_checks_passed += ga_row["checks_passed"] or 0
            go_agent_checks_total += ga_row["checks_total"] or 0

        # --- Compute per-category scores from total basket ---
        breakdown = {}
        overall_sum = 0
        cats_with_data = 0
        for cat in categories:
            total = cat_pass[cat] + cat_fail[cat] + cat_warn[cat]
            if total > 0:
                # Score = (passes + 0.5*warnings) / total * 100
                score = round(((cat_pass[cat] + 0.5 * cat_warn[cat]) / total) * 100)
                breakdown[cat] = score
                overall_sum += score
                cats_with_data += 1
            else:
                breakdown[cat] = None

        bundle_overall = round(overall_sum / cats_with_data, 1) if cats_with_data > 0 else None

        # Blend bundle compliance with Go agent compliance, weighted by check count
        if go_agent_checks_total > 0:
            go_agent_score = round((go_agent_checks_passed / go_agent_checks_total) * 100, 1)
            bundle_check_count = total_passed + total_failed + total_warnings
            if bundle_overall is not None and bundle_check_count > 0:
                # Weighted average: each source weighted by its check count
                total_weight = bundle_check_count + go_agent_checks_total
                overall = round(
                    (bundle_overall * bundle_check_count + go_agent_score * go_agent_checks_total) / total_weight,
                    1,
                )
            else:
                # Only Go agent data available
                overall = go_agent_score
        else:
            overall = bundle_overall

        trend_rows = await conn.fetch("""
            SELECT
                DATE(cb.checked_at) as date,
                COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant', 'fail', 'non_compliant', 'warning')) as total,
                COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant')) as passed
            FROM compliance_bundles cb,
                 jsonb_array_elements(cb.checks) as c
            WHERE cb.site_id = $1
              AND cb.checked_at > NOW() - INTERVAL '30 days'
              AND jsonb_array_length(cb.checks) > 0
            GROUP BY DATE(cb.checked_at)
            ORDER BY date ASC
        """, site_id)

        trend = [
            {
                "date": r["date"].isoformat(),
                "score": round((r["passed"] / r["total"]) * 100, 1) if r["total"] > 0 else 100.0
            }
            for r in trend_rows
        ]

        healing = await conn.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE success = true AND resolution_level IN ('L1', 'L2')) as auto_healed,
                COUNT(*) FILTER (WHERE resolution_level = 'L3' OR success = false) as pending
            FROM execution_telemetry
            WHERE site_id = $1
              AND created_at > NOW() - INTERVAL '30 days'
        """, site_id)

        # Network coverage score
        coverage_row = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE device_status = 'agent_active') as agent_active,
                COUNT(*) FILTER (WHERE device_status NOT IN ('ignored', 'archived')) as total_non_ignored
            FROM discovered_devices
            WHERE site_id = $1
        """, site_id)

        agent_active = coverage_row["agent_active"] or 0
        total_non_ignored = coverage_row["total_non_ignored"] or 0
        network_coverage_pct = round((agent_active / total_non_ignored * 100), 1) if total_non_ignored > 0 else 0.0
        unmanaged_count = total_non_ignored - agent_active

        return {
            "site_id": site_id,
            "clinic_name": site["clinic_name"],
            "overall_score": overall,
            "breakdown": breakdown,
            "counts": {
                "passed": total_passed,
                "failed": total_failed,
                "warnings": total_warnings,
                "total": total_passed + total_failed + total_warnings,
            },
            "go_agents": {
                "agent_count": len(go_agent_rows),
                "checks_passed": go_agent_checks_passed,
                "checks_total": go_agent_checks_total,
                "compliance_score": round((go_agent_checks_passed / go_agent_checks_total) * 100, 1) if go_agent_checks_total > 0 else None,
            },
            "trend": trend,
            "healing": {
                "total": healing["total"] if healing else 0,
                "auto_healed": healing["auto_healed"] if healing else 0,
                "pending": healing["pending"] if healing else 0,
            },
            "network_coverage_pct": network_coverage_pct,
            "unmanaged_device_count": unmanaged_count,
        }


@router.get("/sites/{site_id}/devices-at-risk")
async def get_devices_at_risk(
    site_id: str,
    user: dict = Depends(auth_module.require_auth),
):
    """Get per-device drift/compliance breakdown for a site.

    Returns devices sorted by risk (most issues first), with per-category
    incident counts so admins and clients can identify culprit devices at a glance.
    """
    await check_site_access_pool(user, site_id)
    from .fleet import get_pool
    pool = await get_pool()

    categories = {
        "patching": ["nixos_generation", "windows_update", "linux_patching"],
        "antivirus": ["windows_defender", "windows_defender_exclusions"],
        "backup": ["backup_status", "windows_backup_status"],
        "logging": ["audit_logging", "windows_audit_policy", "linux_audit", "linux_logging"],
        "firewall": ["firewall", "windows_firewall_status", "firewall_status", "linux_firewall"],
        "encryption": ["bitlocker", "windows_bitlocker_status", "linux_crypto", "windows_smb_signing"],
        "access_control": ["rogue_admin_users", "linux_accounts", "windows_password_policy",
                          "linux_permissions", "linux_ssh_config", "windows_screen_lock_policy"],
        "services": ["critical_services", "linux_services", "windows_service_dns",
                    "windows_service_netlogon", "windows_service_spooler",
                    "windows_service_w32time", "windows_service_wuauserv", "agent_status"],
    }
    reverse_map = {}
    for cat, types in categories.items():
        for ct in types:
            reverse_map[ct] = cat

    async with admin_connection(pool) as conn:
        # Verify site exists
        site = await conn.fetchrow(
            "SELECT site_id FROM sites WHERE site_id = $1", site_id
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get all active (unresolved) incidents for this site, grouped by hostname
        rows = await conn.fetch("""
            SELECT sa.hostname, i.check_type, i.severity, i.created_at, i.id,
                   i.resolution_tier as resolution_level
            FROM incidents i
            JOIN site_appliances sa ON sa.appliance_id = i.appliance_id::text
            WHERE sa.site_id = $1
              AND i.status != 'resolved'
            ORDER BY sa.hostname, i.created_at DESC
        """, site_id)

        # Get device info from discovered_devices for enrichment (hostname, ip, device_type)
        device_info = {}
        try:
            devices = await conn.fetch("""
                SELECT d.hostname, d.ip_address, d.device_type, d.os_name, d.compliance_status
                FROM discovered_devices d
                WHERE d.site_id = $1
            """, site_id)
            for d in devices:
                hn = (d["hostname"] or "").lower()
                if hn:
                    device_info[hn] = {
                        "ip_address": d["ip_address"],
                        "device_type": d["device_type"],
                        "os_name": d["os_name"],
                        "compliance_status": d["compliance_status"],
                    }
        except Exception as e:
            logger.warning(f"Device enrichment failed (non-fatal): {e}")

        # Build per-device breakdown
        device_map: dict = {}
        for row in rows:
            hostname = row["hostname"] or "unknown"
            if hostname not in device_map:
                hn_lower = hostname.lower()
                info = device_info.get(hn_lower, {})
                device_map[hostname] = {
                    "hostname": hostname,
                    "ip_address": info.get("ip_address"),
                    "device_type": info.get("device_type"),
                    "os_name": info.get("os_name"),
                    "active_incidents": 0,
                    "critical_count": 0,
                    "high_count": 0,
                    "medium_count": 0,
                    "low_count": 0,
                    "categories": {cat: 0 for cat in categories},
                    "worst_severity": "low",
                    "incidents": [],
                }

            dev = device_map[hostname]
            dev["active_incidents"] += 1
            sev = (row["severity"] or "medium").lower()
            if sev == "critical":
                dev["critical_count"] += 1
            elif sev == "high":
                dev["high_count"] += 1
            elif sev == "medium":
                dev["medium_count"] += 1
            else:
                dev["low_count"] += 1

            # Update worst severity
            sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
            if sev_rank.get(sev, 0) > sev_rank.get(dev["worst_severity"], 0):
                dev["worst_severity"] = sev

            # Category mapping
            cat = reverse_map.get(row["check_type"])
            if cat:
                dev["categories"][cat] += 1

            # Include incident summary (max 5 per device)
            if len(dev["incidents"]) < 5:
                dev["incidents"].append({
                    "id": row["id"],
                    "check_type": row["check_type"],
                    "severity": row["severity"],
                    "resolution_level": row["resolution_level"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                })

        # Sort by risk: critical first, then high, then total count
        devices_list = sorted(
            device_map.values(),
            key=lambda d: (d["critical_count"], d["high_count"], d["active_incidents"]),
            reverse=True,
        )

        # Also compute a simple health score per device
        # Based on active incident severity weights
        for dev in devices_list:
            penalty = (dev["critical_count"] * 25 + dev["high_count"] * 15 +
                       dev["medium_count"] * 8 + dev["low_count"] * 3)
            dev["health_score"] = max(0, 100 - penalty)

        return {
            "site_id": site_id,
            "total_devices_at_risk": len(devices_list),
            "devices": devices_list,
        }


@router.get("/sites/{site_id}/drift-config")
async def get_drift_config(
    site_id: str,
    user: dict = Depends(auth_module.require_auth),
):
    """Get drift scan configuration for a site.

    Returns ALL known check types with enabled=true as default,
    overlaid by any site-specific overrides from the database.
    """
    await check_site_access_pool(user, site_id)
    from .fleet import get_pool
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        rows = await conn.fetch("""
            SELECT check_type, enabled, notes FROM site_drift_config
            WHERE site_id = $1
            ORDER BY check_type
        """, site_id)

    # Build override map from DB
    overrides = {r["check_type"]: r for r in rows}

    # Canonical list of all check types (must match daemon scan capabilities)
    ALL_CHECK_TYPES = [
        # Windows
        "firewall_status", "windows_defender", "windows_update", "audit_logging",
        "rogue_admin_users", "rogue_scheduled_tasks", "agent_status",
        "bitlocker_status", "smb_signing", "smb1_protocol", "screen_lock_policy",
        "defender_exclusions", "dns_config", "network_profile", "password_policy",
        "rdp_nla", "guest_account", "service_dns", "service_netlogon",
        "wmi_event_persistence", "registry_run_persistence", "audit_policy",
        "defender_cloud_protection", "spooler_service",
        # Linux
        "linux_firewall", "linux_ssh_root", "linux_ssh_password",
        "linux_failed_services", "linux_disk_space", "linux_suid",
        "linux_unattended_upgrades", "linux_audit", "linux_ntp", "linux_cert_expiry",
        # macOS
        "macos_filevault", "macos_gatekeeper", "macos_sip", "macos_firewall",
        "macos_auto_update", "macos_screen_lock", "macos_remote_login",
        "macos_file_sharing", "macos_time_machine", "macos_ntp_sync",
        "macos_admin_users", "macos_disk_space", "macos_cert_expiry",
    ]

    # Default disabled checks (SSH is the management channel for macOS)
    DEFAULT_DISABLED = {"macos_remote_login"}

    checks = []
    for ct in ALL_CHECK_TYPES:
        override = overrides.get(ct)
        if override:
            checks.append({
                "check_type": ct,
                "enabled": override["enabled"],
                "platform": _check_platform(ct),
                "notes": override["notes"] or "",
            })
        else:
            checks.append({
                "check_type": ct,
                "enabled": ct not in DEFAULT_DISABLED,
                "platform": _check_platform(ct),
                "notes": "",
            })

    # Include any site-specific checks not in the canonical list (custom checks)
    for ct, r in overrides.items():
        if ct not in {c["check_type"] for c in checks}:
            checks.append({
                "check_type": ct,
                "enabled": r["enabled"],
                "platform": _check_platform(ct),
                "notes": r["notes"] or "",
            })

    return {"site_id": site_id, "checks": checks}


class DriftCheckItem(BaseModel):
    check_type: str
    enabled: bool


class DriftConfigUpdate(BaseModel):
    checks: List[DriftCheckItem]


@router.put("/sites/{site_id}/drift-config")
async def update_drift_config(
    site_id: str,
    body: DriftConfigUpdate,
    user: dict = Depends(auth_module.require_auth),
):
    """Upsert drift scan configuration for a site."""
    await check_site_access_pool(user, site_id)
    from .fleet import get_pool
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        async with conn.transaction():
            for item in body.checks:
                await conn.execute("""
                    INSERT INTO site_drift_config (site_id, check_type, enabled, modified_by, modified_at)
                    VALUES ($1, $2, $3, $4, NOW())
                    ON CONFLICT (site_id, check_type)
                    DO UPDATE SET enabled = $3, modified_by = $4, modified_at = NOW()
                """, site_id, item.check_type, item.enabled, user.get("username", "admin"))
    return {"status": "ok", "site_id": site_id, "updated": len(body.checks)}


# =============================================================================
# L4 ESCALATION QUEUE (ADMIN / CENTRAL COMMAND)
# =============================================================================

@router.get("/l4-queue")
async def get_l4_queue(
    status: Optional[str] = Query(None, description="Filter: open, resolved"),
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(auth_module.require_auth),
):
    """Get L4 escalation queue — tickets that partners escalated to Central Command."""
    from .fleet import get_pool
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        status_clause = ""
        params: list = [limit]
        if status == "resolved":
            status_clause = "AND et.l4_resolved_at IS NOT NULL"
        elif status == "open" or status is None:
            status_clause = "AND et.l4_resolved_at IS NULL"

        tickets = await conn.fetch(f"""
            SELECT et.id, et.partner_id, et.site_id, et.incident_id, et.incident_type,
                   et.severity, et.priority, et.title, et.summary,
                   et.hipaa_controls, et.attempted_actions, et.recommended_action,
                   et.status, et.recurrence_count, et.previous_ticket_id,
                   et.l4_escalated_at, et.l4_escalated_by, et.l4_notes,
                   et.l4_resolved_at, et.l4_resolved_by, et.l4_resolution_notes,
                   et.created_at, et.sla_breached,
                   s.clinic_name as site_name,
                   p.name as partner_name
            FROM escalation_tickets et
            LEFT JOIN sites s ON s.site_id = et.site_id::text
            LEFT JOIN partners p ON p.id = et.partner_id
            WHERE et.escalated_to_l4 = true {status_clause}
            ORDER BY et.l4_escalated_at DESC
            LIMIT $1
        """, *params)

        return {
            "tickets": [dict(t) for t in tickets],
            "count": len(tickets),
        }


class L4ResolveRequest(BaseModel):
    resolved_by: str
    resolution_notes: str


@router.post("/l4-queue/{ticket_id}/resolve")
async def resolve_l4_ticket(
    ticket_id: str,
    body: L4ResolveRequest,
    user: dict = Depends(auth_module.require_auth),
):
    """Admin resolves an L4 escalation ticket."""
    from .fleet import get_pool
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        ticket = await conn.fetchrow("""
            SELECT id, escalated_to_l4, l4_resolved_at FROM escalation_tickets
            WHERE id = $1 AND escalated_to_l4 = true
        """, ticket_id)

        if not ticket:
            raise HTTPException(404, "L4 ticket not found")

        if ticket['l4_resolved_at']:
            raise HTTPException(400, "Already resolved")

        await conn.execute("""
            UPDATE escalation_tickets
            SET l4_resolved_at = NOW(),
                l4_resolved_by = $2,
                l4_resolution_notes = $3,
                status = 'resolved',
                updated_at = NOW()
            WHERE id = $1
        """, ticket_id, body.resolved_by, body.resolution_notes)

    return {"status": "l4_resolved", "ticket_id": ticket_id}


# =============================================================================
# SITE DECOMMISSION ENDPOINTS
# =============================================================================


@router.get("/sites/{site_id}/export")
async def export_site_data(
    site_id: str,
    user: dict = Depends(auth_module.require_operator),
):
    """Export all site data as JSON for archival before decommission.

    HIPAA requires 6-year data retention — this endpoint produces
    a comprehensive export that can be stored offline.
    """
    await check_site_access_pool(user, site_id)
    from .fleet import get_pool
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Verify site exists
        site = await conn.fetchrow(
            "SELECT * FROM sites WHERE site_id = $1", site_id
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Site info
        site_info = dict(site)
        # Convert non-serializable types
        for k, v in site_info.items():
            if isinstance(v, datetime):
                site_info[k] = v.isoformat()

        # Incidents (last 1000)
        incident_rows = await conn.fetch("""
            SELECT id, incident_type, hostname, severity, status,
                   resolution_level, details, created_at, resolved_at, site_id
            FROM incidents
            WHERE site_id = $1
            ORDER BY created_at DESC
            LIMIT 1000
        """, site_id)
        incidents = []
        for row in incident_rows:
            d = dict(row)
            for k, v in d.items():
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
            incidents.append(d)

        # Evidence bundles (last 500)
        evidence_rows = await conn.fetch("""
            SELECT id, site_id, bundle_type, checks, checked_at, created_at
            FROM compliance_bundles
            WHERE site_id = $1
            ORDER BY created_at DESC
            LIMIT 500
        """, site_id)
        evidence = []
        for row in evidence_rows:
            d = dict(row)
            for k, v in d.items():
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
            evidence.append(d)

        # Compliance results (latest per check type)
        compliance_rows = await conn.fetch("""
            SELECT DISTINCT ON (check_type)
                id, site_id, check_type, status, details, checked_at
            FROM compliance_results
            WHERE site_id = $1
            ORDER BY check_type, checked_at DESC
        """, site_id)
        compliance = []
        for row in compliance_rows:
            d = dict(row)
            for k, v in d.items():
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
            compliance.append(d)

        # Workstations
        workstation_rows = await conn.fetch("""
            SELECT id, site_id, hostname, os_type, compliance_status,
                   last_compliance_check, details, created_at
            FROM workstations
            WHERE site_id = $1
            ORDER BY hostname
        """, site_id)
        workstations = []
        for row in workstation_rows:
            d = dict(row)
            for k, v in d.items():
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
            workstations.append(d)

        # Discovered devices
        device_rows = await conn.fetch("""
            SELECT id, site_id, mac_address, ip_address, hostname,
                   device_type, vendor, compliance_status, first_seen, last_seen
            FROM discovered_devices
            WHERE site_id = $1
            ORDER BY last_seen DESC NULLS LAST
        """, site_id)
        devices = []
        for row in device_rows:
            d = dict(row)
            for k, v in d.items():
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
            devices.append(d)

        # Credentials (names only, NOT secrets — HIPAA safe)
        cred_rows = await conn.fetch("""
            SELECT id, site_id, credential_type, credential_name, created_at
            FROM site_credentials
            WHERE site_id = $1
            ORDER BY credential_name
        """, site_id)
        credentials = []
        for row in cred_rows:
            d = dict(row)
            for k, v in d.items():
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
            credentials.append(d)

        # Go agents
        go_agent_rows = await conn.fetch("""
            SELECT id, site_id, agent_id, hostname, capability_tier,
                   status, version, last_checkin, created_at
            FROM go_agents
            WHERE site_id = $1
            ORDER BY hostname
        """, site_id)
        go_agents = []
        for row in go_agent_rows:
            d = dict(row)
            for k, v in d.items():
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
            go_agents.append(d)

        # Drift config
        drift_rows = await conn.fetch("""
            SELECT check_type, enabled, notes
            FROM site_drift_config
            WHERE site_id = $1
            ORDER BY check_type
        """, site_id)
        drift_config = [dict(r) for r in drift_rows]

        # Orders (last 200)
        order_rows = await conn.fetch("""
            SELECT order_id, appliance_id, site_id, order_type,
                   parameters, status, created_at, expires_at,
                   acknowledged_at, completed_at
            FROM admin_orders
            WHERE site_id = $1
            ORDER BY created_at DESC
            LIMIT 200
        """, site_id)
        orders = []
        for row in order_rows:
            d = dict(row)
            for k, v in d.items():
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
            orders.append(d)

        # Appliances
        appliance_rows = await conn.fetch("""
            SELECT appliance_id, hostname, mac_address, ip_addresses,
                   agent_version, status, last_checkin, first_checkin
            FROM site_appliances
            WHERE site_id = $1
        """, site_id)
        appliances = []
        for row in appliance_rows:
            d = dict(row)
            for k, v in d.items():
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
            appliances.append(d)

    return {
        "export_version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "site": site_info,
        "appliances": appliances,
        "incidents": incidents,
        "incidents_count": len(incidents),
        "evidence_bundles": evidence,
        "evidence_count": len(evidence),
        "compliance_results": compliance,
        "workstations": workstations,
        "devices": devices,
        "credentials": credentials,
        "go_agents": go_agents,
        "drift_config": drift_config,
        "orders": orders,
        "orders_count": len(orders),
    }


@router.post("/sites/{site_id}/decommission")
async def decommission_site(
    site_id: str,
    user: dict = Depends(auth_module.require_operator),
):
    """Decommission a site — revoke access, stop appliances, mark inactive.

    This is a destructive operation:
    1. Validates site exists and is not already inactive
    2. Revokes all API keys for the site
    3. Invalidates portal access tokens
    4. Creates fleet order to stop appliances (force_checkin with stop flag)
    5. Updates site status to 'inactive'
    6. Logs to audit trail

    HIPAA note: Data is NOT deleted. Site data remains for 6-year retention.
    Use the export endpoint first to create an offline archive.
    """
    await check_site_access_pool(user, site_id)
    from .fleet import get_pool
    from .order_signing import sign_admin_order
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    actions_taken = []

    async with admin_connection(pool) as conn:
        # 1. Validate site exists and is not already inactive
        site = await conn.fetchrow(
            "SELECT site_id, clinic_name, status FROM sites WHERE site_id = $1",
            site_id,
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        if site["status"] == "inactive":
            raise HTTPException(
                status_code=400,
                detail="Site is already decommissioned (status: inactive)",
            )

        clinic_name = site["clinic_name"] or site_id

        # 2. Revoke all API keys for the site
        api_key_result = await conn.execute(
            "UPDATE api_keys SET active = false WHERE site_id = $1 AND active = true",
            site_id,
        )
        revoked_keys = int(api_key_result.split()[-1]) if api_key_result else 0
        if revoked_keys > 0:
            actions_taken.append(f"Revoked {revoked_keys} API key(s)")

        # 3. Invalidate portal access tokens
        token_result = await conn.execute("""
            DELETE FROM portal_access_tokens WHERE site_id = $1
        """, site_id)
        revoked_tokens = int(token_result.split()[-1]) if token_result else 0
        if revoked_tokens > 0:
            actions_taken.append(f"Invalidated {revoked_tokens} portal token(s)")

        # 4. Create fleet orders to stop appliances
        appliances = await conn.fetch(
            "SELECT appliance_id FROM site_appliances WHERE site_id = $1",
            site_id,
        )
        stop_orders_created = 0
        expires_at = now + timedelta(hours=1)
        for row in appliances:
            order_id = f"ORD-DECOM-{now.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"
            parameters = {"reason": "site_decommissioned", "stop": True}
            nonce, signature, signed_payload = sign_admin_order(
                order_id,
                "force_checkin",
                parameters,
                now,
                expires_at,
                target_appliance_id=row["appliance_id"],
            )
            await conn.execute("""
                INSERT INTO admin_orders (
                    order_id, appliance_id, site_id, order_type,
                    parameters, priority, status, created_at, expires_at,
                    nonce, signature, signed_payload
                ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11, $12)
            """,
                order_id,
                row["appliance_id"],
                site_id,
                "force_checkin",
                json.dumps(parameters),
                0,
                "pending",
                now,
                expires_at,
                nonce,
                signature,
                signed_payload,
            )
            stop_orders_created += 1

        if stop_orders_created > 0:
            actions_taken.append(
                f"Sent stop order to {stop_orders_created} appliance(s)"
            )

        # 5. Update site status to 'inactive'
        await conn.execute(
            "UPDATE sites SET status = 'inactive', updated_at = $2 WHERE site_id = $1",
            site_id,
            now,
        )
        actions_taken.append("Site status set to inactive")

        # 6. Audit trail
        await conn.execute("""
            INSERT INTO audit_log (event_type, actor, resource_type, resource_id, details)
            VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
            "site.decommissioned",
            user.get("username", "unknown"),
            "site",
            site_id,
            json.dumps({
                "clinic_name": clinic_name,
                "previous_status": site["status"],
                "revoked_api_keys": revoked_keys,
                "revoked_portal_tokens": revoked_tokens,
                "stop_orders_created": stop_orders_created,
                "actions": actions_taken,
            }),
        )
        actions_taken.append("Audit trail entry created")

    # Broadcast site status change via websocket
    try:
        await broadcast_event("site_decommissioned", {
            "site_id": site_id,
            "clinic_name": clinic_name,
        })
    except Exception as e:
        logger.warning(f"Failed to broadcast decommission event: {e}")

    return {
        "status": "decommissioned",
        "site_id": site_id,
        "clinic_name": clinic_name,
        "actions": actions_taken,
        "decommissioned_at": now.isoformat(),
    }
