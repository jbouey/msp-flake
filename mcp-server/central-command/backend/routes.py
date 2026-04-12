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
from typing import Optional, List, Dict, Any
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
from .shared import execute_with_retry


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

    result = await execute_with_retry(db,text(query_str), params)
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
    site_result = await execute_with_retry(db,
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
    appliances_result = await execute_with_retry(db,
        text("""
            SELECT id, appliance_id, hostname, ip_addresses, agent_version,
                   status, last_checkin, first_checkin, display_name,
                   COALESCE(jsonb_array_length(assigned_targets), 0) as assigned_target_count,
                   daemon_health->>'boot_source' as boot_source
            FROM site_appliances
            WHERE site_id = :site_id AND deleted_at IS NULL
        """),
        {"site_id": site_id}
    )
    appliance_rows = appliances_result.fetchall()

    appliances = []
    for i, a in enumerate(appliance_rows):
        is_online = a.status == 'online'
        boot_source = getattr(a, 'boot_source', None) or 'unknown'
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
            hostname=a.display_name or a.hostname or "unknown",
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
            created_at=a.first_checkin or datetime.now(timezone.utc),
            display_name=a.display_name,
            assigned_target_count=getattr(a, 'assigned_target_count', 0) or 0,
            boot_source=boot_source if boot_source != 'unknown' else None,
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

    result = await execute_with_retry(db,
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
    user: dict = Depends(auth_module.require_auth),
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
    incidents = await get_incidents_from_db(
        db, site_id=site_id, limit=limit, offset=offset,
        resolved=resolved, level=level, org_scope=user.get("org_scope"),
    )

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
            remediation_attempts=i.get("remediation_attempts", 0),
            remediation_exhausted=i.get("remediation_exhausted", False),
            created_at=i["created_at"],
        )
        for i in incidents
    ]


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
async def get_incident_detail(incident_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
    """Get full incident detail including evidence bundle."""
    result = await execute_with_retry(db,
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

    # Org-scope IDOR prevention: verify this incident's site is accessible
    await check_site_access_sa(db, user, row.site_id)

    # Fetch remediation steps from relational table (migration 137)
    remediation_history = []
    try:
        steps_result = await execute_with_retry(db,
            text("""
                SELECT tier, runbook_id, result, confidence, created_at
                FROM incident_remediation_steps
                WHERE incident_id = :incident_id
                ORDER BY step_idx
            """),
            {"incident_id": str(row.id)}
        )
        remediation_history = [
            {
                "tier": s.tier,
                "runbook_id": s.runbook_id,
                "result": s.result,
                "confidence": s.confidence,
                "timestamp": s.created_at.isoformat() if s.created_at else None,
            }
            for s in steps_result.fetchall()
        ]
    except Exception:
        # Fallback to JSONB column if migration 137 hasn't run
        remediation_history = getattr(row, 'remediation_history', None) or []

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
        remediation_attempts=getattr(row, 'remediation_attempts', None) or 0,
        remediation_exhausted=getattr(row, 'remediation_exhausted', None) or False,
        remediation_history=remediation_history,
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
            SELECT i.id, i.status, i.site_id, i.check_type,
                   i.details->>'hostname' AS hostname,
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
            SELECT i.id, i.site_id, i.check_type,
                   i.details->>'hostname' AS hostname
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
    user: dict = Depends(auth_module.require_auth),
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
    events = await get_events_from_db(db, site_id=site_id, limit=limit, offset=offset, org_scope=user.get("org_scope"))
    return events


# =============================================================================
# RUNBOOK ENDPOINTS
# =============================================================================

@router.get("/runbooks", response_model=List[Runbook])
async def get_runbooks(db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
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
async def get_runbook_detail(runbook_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
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
    user: dict = Depends(auth_module.require_auth),
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

@router.get("/admin/flywheel/health")
async def get_flywheel_health(
    user: dict = Depends(auth_module.require_auth),
):
    """Comprehensive flywheel pipeline health for admin dashboard panel.

    Returns:
    - Pipeline stages (pending/approved/promoted/stuck counts)
    - Recent promotions (last 10)
    - Stuck candidates list (approved but no promoted_rules)
    - Eligible but unapproved patterns
    - Auto-disabled rules (bad promotions)
    - Pipeline health score
    """
    from .fleet import get_pool
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Candidate stages
        cand_stats = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE approval_status = 'pending') as pending,
                COUNT(*) FILTER (WHERE approval_status = 'approved') as approved,
                COUNT(*) FILTER (WHERE approval_status = 'rejected') as rejected
            FROM learning_promotion_candidates
        """)

        # Promoted rules
        pr_stats = await conn.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'active') as active,
                COUNT(*) FILTER (WHERE status = 'disabled') as disabled,
                COUNT(*) FILTER (WHERE promoted_at > NOW() - INTERVAL '24 hours') as last_24h,
                COUNT(*) FILTER (WHERE promoted_at > NOW() - INTERVAL '7 days') as last_7d
            FROM promoted_rules
        """)

        # Stuck candidates (CRITICAL metric)
        stuck_count = await conn.fetchval("""
            SELECT COUNT(*) FROM learning_promotion_candidates lpc
            LEFT JOIN promoted_rules pr
                ON pr.pattern_signature = lpc.pattern_signature
                AND pr.site_id = lpc.site_id
            WHERE lpc.approval_status = 'approved' AND pr.rule_id IS NULL
        """)

        # Eligible waiting (for manual approval or auto-promotion)
        eligible_count = await conn.fetchval("""
            SELECT COUNT(*) FROM aggregated_pattern_stats
            WHERE promotion_eligible = true
        """)

        # Recent promotions
        recent = await conn.fetch("""
            SELECT pr.rule_id, pr.pattern_signature, pr.site_id,
                   pr.status, pr.promoted_at,
                   s.clinic_name
            FROM promoted_rules pr
            LEFT JOIN sites s ON s.site_id = pr.site_id
            ORDER BY pr.promoted_at DESC
            LIMIT 10
        """)

        # Auto-disabled rules (rolled back due to poor performance)
        disabled = await conn.fetch("""
            SELECT rule_id, created_at
            FROM l1_rules
            WHERE source = 'promoted' AND enabled = false
              AND created_at > NOW() - INTERVAL '30 days'
            ORDER BY created_at DESC
            LIMIT 10
        """)

        # Stuck candidates detail
        stuck_list = await conn.fetch("""
            SELECT lpc.id, lpc.site_id, lpc.pattern_signature, lpc.approved_at
            FROM learning_promotion_candidates lpc
            LEFT JOIN promoted_rules pr
                ON pr.pattern_signature = lpc.pattern_signature
                AND pr.site_id = lpc.site_id
            WHERE lpc.approval_status = 'approved' AND pr.rule_id IS NULL
            ORDER BY lpc.approved_at DESC
            LIMIT 20
        """)

        # Pipeline health score
        health_issues = []
        if stuck_count > 0:
            health_issues.append(f"{stuck_count} approved candidates not promoted")
        if pr_stats["last_7d"] == 0 and eligible_count > 0:
            health_issues.append(f"{eligible_count} eligible patterns but no promotions in 7 days")
        if pr_stats["disabled"] > 5:
            health_issues.append(f"{pr_stats['disabled']} rules auto-disabled (high failure rate)")

        health_status = "healthy"
        if stuck_count > 0 or (pr_stats["last_7d"] == 0 and eligible_count > 5):
            health_status = "degraded"
        if stuck_count > 10 or len(health_issues) >= 3:
            health_status = "critical"

        return {
            "health_status": health_status,
            "health_issues": health_issues,
            "pipeline": {
                "pending_candidates": cand_stats["pending"] or 0,
                "approved_candidates": cand_stats["approved"] or 0,
                "rejected_candidates": cand_stats["rejected"] or 0,
                "promoted_rules_total": pr_stats["total"] or 0,
                "promoted_rules_active": pr_stats["active"] or 0,
                "promoted_rules_disabled": pr_stats["disabled"] or 0,
                "promotions_24h": pr_stats["last_24h"] or 0,
                "promotions_7d": pr_stats["last_7d"] or 0,
                "stuck_candidates": stuck_count or 0,
                "eligible_waiting": eligible_count or 0,
            },
            "recent_promotions": [
                {
                    "rule_id": r["rule_id"],
                    "pattern_signature": r["pattern_signature"],
                    "site_id": r["site_id"],
                    "clinic_name": r["clinic_name"],
                    "status": r["status"],
                    "promoted_at": r["promoted_at"].isoformat() if r["promoted_at"] else None,
                }
                for r in recent
            ],
            "auto_disabled": [
                {
                    "rule_id": d["rule_id"],
                    "disabled_at": d["created_at"].isoformat() if d["created_at"] else None,
                }
                for d in disabled
            ],
            "stuck_candidates_list": [
                {
                    "id": str(s["id"]),
                    "site_id": s["site_id"],
                    "pattern_signature": s["pattern_signature"],
                    "approved_at": s["approved_at"].isoformat() if s["approved_at"] else None,
                }
                for s in stuck_list
            ],
        }


@router.get("/learning/status", response_model=LearningStatus)
async def get_learning_status(db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
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
        last_promotion_at=status.get("last_promotion_at"),
    )


@router.get("/learning/candidates", response_model=List[PromotionCandidate])
async def get_promotion_candidates(db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
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
async def get_coverage_gaps(db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
    """Get check_types seen in telemetry that lack L1 rules."""
    from .db_queries import get_coverage_gaps_from_db
    gaps = await get_coverage_gaps_from_db(db)
    return [CoverageGap(**g) for g in gaps]


@router.get("/learning/history", response_model=List[PromotionHistory])
async def get_promotion_history(limit: int = Query(default=20, ge=1, le=100), db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
    """Get recently promoted L2->L1 patterns from learning_promotion_candidates."""
    result = await execute_with_retry(db,text("""
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
    result = await execute_with_retry(db,text("""
        SELECT id, pattern_signature, site_id, recommended_action
        FROM aggregated_pattern_stats
        WHERE id::text = :pid AND promotion_eligible = true
    """), {"pid": pattern_id})
    aps_row = result.fetchone()

    if not aps_row:
        raise HTTPException(status_code=404, detail=f"Pattern {pattern_id} not found")

    # IDOR check: ensure org-scoped user can access this pattern's site
    await check_site_access_sa(db, user, aps_row.site_id)

    # Admin promote uses the same shared promotion logic as partner path.
    # We must first create or upsert the candidate, then call promote_candidate.
    # Use an asyncpg connection since flywheel_promote expects asyncpg.
    from .fleet import get_pool
    from .tenant_middleware import admin_connection
    from .flywheel_promote import promote_candidate

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        async with conn.transaction():
            # Upsert candidate from aggregated_pattern_stats
            candidate_row = await conn.fetchrow("""
                INSERT INTO learning_promotion_candidates (
                    site_id, pattern_signature, approval_status
                ) VALUES ($1, $2, 'pending')
                ON CONFLICT (site_id, pattern_signature) DO UPDATE SET
                    approval_status = learning_promotion_candidates.approval_status
                RETURNING id, site_id, pattern_signature
            """, aps_row.site_id, aps_row.pattern_signature)

            # Fetch full candidate with metrics from aggregated_pattern_stats
            full = await conn.fetchrow("""
                SELECT lpc.id, lpc.site_id, lpc.pattern_signature,
                       aps.success_rate, aps.total_occurrences, aps.l2_resolutions,
                       aps.recommended_action
                FROM learning_promotion_candidates lpc
                JOIN aggregated_pattern_stats aps
                    ON aps.site_id = lpc.site_id
                    AND aps.pattern_signature = lpc.pattern_signature
                WHERE lpc.id = $1
            """, candidate_row["id"])

            result = await promote_candidate(
                conn=conn,
                candidate=dict(full) if full else dict(candidate_row),
                actor=user.get("username", "admin"),
                actor_type="admin",
            )

            # Mark as no longer eligible in aggregated_pattern_stats
            await conn.execute("""
                UPDATE aggregated_pattern_stats
                SET promotion_eligible = false
                WHERE id = $1
            """, int(pattern_id))

    return {"status": "promoted", "pattern_id": pattern_id, **result}


@router.post("/learning/reject/{pattern_id}")
async def reject_pattern(
    pattern_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Reject a promotion candidate, marking it as dismissed."""
    # Try legacy patterns table first
    result = await execute_with_retry(db,
        text("SELECT pattern_id, status FROM patterns WHERE pattern_id = :pid"),
        {"pid": pattern_id}
    )
    pattern = result.fetchone()
    if pattern:
        await execute_with_retry(db,text("""
            UPDATE patterns SET status = 'rejected' WHERE pattern_id = :pid
        """), {"pid": pattern_id})
        await db.commit()
        return {"status": "rejected", "pattern_id": pattern_id}

    # Try aggregated_pattern_stats
    result = await execute_with_retry(db,text("""
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
    await execute_with_retry(db,text("""
        UPDATE aggregated_pattern_stats SET promotion_eligible = false WHERE id = :pid
    """), {"pid": int(pattern_id)})

    # Record rejection
    await execute_with_retry(db,text("""
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
    result = await execute_with_retry(db,
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

        await execute_with_retry(db,text("""
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

        await execute_with_retry(db,text("""
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
async def get_onboarding_pipeline(db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
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

    result = await execute_with_retry(db,query)
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
async def get_onboarding_metrics(db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
    """Get aggregate pipeline metrics.

    Returns:
        Counts by stage, avg time to deploy, at-risk clients.
    """
    # Count sites by onboarding stage
    result = await execute_with_retry(db,text("""
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
    ship_result = await execute_with_retry(db,text("""
        SELECT AVG(EXTRACT(EPOCH FROM (shipped_at - lead_at)) / 86400.0)
        FROM sites WHERE shipped_at IS NOT NULL AND lead_at IS NOT NULL
    """))
    avg_ship = round(ship_result.scalar() or 0.0, 1)

    active_result = await execute_with_retry(db,text("""
        SELECT AVG(EXTRACT(EPOCH FROM (active_at - lead_at)) / 86400.0)
        FROM sites WHERE active_at IS NOT NULL AND lead_at IS NOT NULL
    """))
    avg_active = round(active_result.scalar() or 0.0, 1)

    stalled_result = await execute_with_retry(db,text("""
        SELECT COUNT(*) FROM sites
        WHERE onboarding_stage NOT IN ('active', 'compliant')
        AND created_at < NOW() - INTERVAL '14 days'
    """))
    stalled = stalled_result.scalar() or 0

    conn_result = await execute_with_retry(db,text("""
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
async def get_onboarding_detail(client_id: int, db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
    """Get detailed onboarding status for a single client."""
    clients = await get_onboarding_pipeline(db)
    for client in clients:
        if client.id == str(client_id):
            return client
    raise HTTPException(status_code=404, detail=f"Client {client_id} not found")


@router.post("/sites")
async def create_site(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Create a new site from the admin dashboard."""
    body = await request.json()
    clinic_name = body.get("clinic_name", "").strip()
    if not clinic_name:
        raise HTTPException(status_code=400, detail="clinic_name is required")

    import re
    site_id = body.get("site_id") or re.sub(r'[^a-z0-9-]', '', clinic_name.lower().replace(" ", "-"))
    contact_name = body.get("contact_name", "")
    contact_email = body.get("contact_email", "")
    tier = body.get("tier", "mid")
    now = datetime.now(timezone.utc)

    try:
        await execute_with_retry(db,text("""
            INSERT INTO sites (site_id, clinic_name, contact_name, contact_email,
                               tier, status, onboarding_stage, lead_at, created_at)
            VALUES (:site_id, :clinic_name, :contact_name, :contact_email,
                    :tier, 'active', 'lead', :now, :now)
        """), {
            "site_id": site_id,
            "clinic_name": clinic_name,
            "contact_name": contact_name,
            "contact_email": contact_email,
            "tier": tier,
            "now": now,
        })
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail=f"Site '{site_id}' already exists")
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "created", "site_id": site_id, "clinic_name": clinic_name}


@router.get("/appliances/unclaimed")
async def list_unclaimed_appliances(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """List appliances that have called home but aren't assigned to a site."""
    result = await execute_with_retry(db,text("""
        SELECT id, mac_address, notes, registered_at, provisioned_at
        FROM appliance_provisioning
        WHERE site_id IS NULL
        ORDER BY registered_at DESC
    """))
    rows = result.fetchall()
    return {
        "unclaimed": [
            {
                "id": row.id,
                "mac_address": row.mac_address,
                "notes": row.notes,
                "registered_at": row.registered_at.isoformat() if row.registered_at else None,
            }
            for row in rows
        ],
        "count": len(rows),
    }


@router.post("/appliances/claim")
async def claim_appliance_to_site(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Assign an unclaimed appliance to a site."""
    body = await request.json()
    mac_address = body.get("mac_address", "").strip().upper()
    site_id = body.get("site_id", "").strip()
    if not mac_address or not site_id:
        raise HTTPException(status_code=400, detail="mac_address and site_id are required")

    # Verify site exists
    site = await execute_with_retry(db,text("SELECT site_id FROM sites WHERE site_id = :s"), {"s": site_id})
    if not site.fetchone():
        raise HTTPException(status_code=404, detail="Site not found")

    # Generate API key for the appliance
    import secrets
    import hashlib
    api_key = secrets.token_urlsafe(32)

    result = await execute_with_retry(db,text("""
        UPDATE appliance_provisioning
        SET site_id = :site_id, api_key = :api_key, provisioned_at = NOW(),
            notes = COALESCE(notes, '') || ' | Claimed via dashboard'
        WHERE UPPER(mac_address) = :mac
        RETURNING id, mac_address, site_id
    """), {"site_id": site_id, "api_key": api_key, "mac": mac_address})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Appliance not found in provisioning table")

    # Register the API key in api_keys table so checkin auth (require_appliance_bearer) works.
    # Without this, the appliance gets a 401 on every checkin because the key_hash doesn't exist.
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    await execute_with_retry(db,text("""
        INSERT INTO api_keys (site_id, key_hash, key_prefix, description, active, created_at)
        VALUES (:site_id, :key_hash, :key_prefix, 'Auto-generated during drop-ship claim', true, NOW())
        ON CONFLICT DO NOTHING
    """), {
        "site_id": site_id,
        "key_hash": key_hash,
        "key_prefix": api_key[:8],
    })
    await db.commit()

    return {
        "status": "claimed",
        "mac_address": row.mac_address,
        "site_id": row.site_id,
        "message": "Appliance will receive config on next check-in.",
    }


@router.post("/appliances/transfer")
async def transfer_appliance(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Transfer an appliance from one site to another by MAC address.

    Updates appliance_provisioning, appliances, and site_appliances tables
    to reflect the new site assignment. The appliance picks up its new
    config on the next check-in.
    """
    body = await request.json()
    mac_address = body.get("mac_address", "").strip().upper()
    from_site_id = body.get("from_site_id", "").strip()
    to_site_id = body.get("to_site_id", "").strip()

    if not mac_address or not from_site_id or not to_site_id:
        raise HTTPException(status_code=400, detail="mac_address, from_site_id, and to_site_id are required")

    if from_site_id == to_site_id:
        raise HTTPException(status_code=400, detail="Source and destination sites must be different")

    # Verify both sites exist
    from_site = await execute_with_retry(db,
        text("SELECT site_id, clinic_name FROM sites WHERE site_id = :s"), {"s": from_site_id}
    )
    from_row = from_site.fetchone()
    if not from_row:
        raise HTTPException(status_code=404, detail=f"Source site '{from_site_id}' not found")

    to_site = await execute_with_retry(db,
        text("SELECT site_id, clinic_name FROM sites WHERE site_id = :s"), {"s": to_site_id}
    )
    to_row = to_site.fetchone()
    if not to_row:
        raise HTTPException(status_code=404, detail=f"Destination site '{to_site_id}' not found")

    # 1. Update appliance_provisioning
    prov_result = await execute_with_retry(db,text("""
        UPDATE appliance_provisioning
        SET site_id = :to_site_id,
            notes = COALESCE(notes, '') || :transfer_note
        WHERE UPPER(mac_address) = :mac AND site_id = :from_site_id
        RETURNING id, mac_address
    """), {
        "to_site_id": to_site_id,
        "mac": mac_address,
        "from_site_id": from_site_id,
        "transfer_note": f" | Transferred {from_site_id} -> {to_site_id} via dashboard",
    })
    prov_row = prov_result.fetchone()

    # 2. Update appliances table (match by mac_address and from_site_id)
    await execute_with_retry(db,text("""
        UPDATE appliances
        SET site_id = :to_site_id
        WHERE UPPER(mac_address) = :mac AND site_id = :from_site_id
    """), {"to_site_id": to_site_id, "mac": mac_address, "from_site_id": from_site_id})

    # 3. Update site_appliances (match by mac in appliance_id or by site)
    await execute_with_retry(db,text("""
        UPDATE site_appliances
        SET site_id = :to_site_id
        WHERE site_id = :from_site_id
          AND appliance_id IN (
              SELECT appliance_id FROM appliances WHERE UPPER(mac_address) = :mac
          )
    """), {"to_site_id": to_site_id, "mac": mac_address, "from_site_id": from_site_id})

    await db.commit()

    if not prov_row:
        logger.warning(f"Appliance transfer: no provisioning row for MAC {mac_address} at site {from_site_id}")

    logger.info(f"Appliance {mac_address} transferred from {from_site_id} to {to_site_id} by {user.get('username', 'unknown')}")

    return {
        "status": "transferred",
        "mac_address": mac_address,
        "from_site_id": from_site_id,
        "from_site_name": from_row.clinic_name,
        "to_site_id": to_site_id,
        "to_site_name": to_row.clinic_name,
        "message": "Appliance will receive new site config on next check-in.",
    }


@router.post("/onboarding", response_model=OnboardingClient)
async def create_prospect(prospect: ProspectCreate, db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
    """Create new prospect (Lead stage)."""
    now = datetime.now(timezone.utc)

    # Insert into DB
    result = await execute_with_retry(db,text("""
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
    current = await execute_with_retry(db,
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

    await execute_with_retry(db,text("""
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
            await execute_with_retry(db,
                text(f"UPDATE sites SET {ts_col} = :now WHERE site_id = :client_id"),
                {"now": now, "client_id": client_id},
            )
        except Exception:
            pass  # Column may not exist

    if request.notes:
        await execute_with_retry(db,text("""
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
    await execute_with_retry(db,text("""
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
    await execute_with_retry(db,text("""
        UPDATE sites SET notes = COALESCE(notes || E'\\n', '') || :note
        WHERE site_id = :client_id
    """), {"note": request.note, "client_id": client_id})
    await db.commit()
    return {"status": "added", "client_id": client_id, "note": request.note}


# =============================================================================
# STATS ENDPOINTS
# =============================================================================

@router.get("/kpi-trends")
async def get_kpi_trends(
    days: int = 14,
    user: dict = Depends(auth_module.require_auth),
):
    """Per-day KPI trend data for the Dashboard sparklines.

    Returns an aligned 14-day (default) series for each of the three
    secondary KPIs so the frontend can render tiny inline trend charts
    without spending a separate query per card.

    Response shape:
      {
        "days": 14,
        "series": {
          "incidents_24h": [12, 8, 15, ...],
          "l1_rate":       [82, 85, 79, ...],
          "clients":       [2, 2, 2, ...],
        },
        "computed_at": "2026-04-09T12:00:00Z"
      }

    Each list has exactly `days` entries, oldest first. Missing days are
    filled with 0 for incidents/clients and null-coerced-to-0 for the L1
    rate so the sparkline never breaks.
    """
    # Clamp the range — no one needs more than 90 days on a dashboard
    # sparkline and larger queries get expensive.
    if days < 2:
        days = 2
    if days > 90:
        days = 90

    from .fleet import get_pool
    from .tenant_middleware import admin_connection

    pool = await get_pool()

    incidents_per_day: list[int] = [0] * days
    l1_per_day: list[float] = [0.0] * days
    clients_per_day: list[int] = [0] * days

    async with admin_connection(pool) as conn:
        # 1) Incidents/day — group by created_at::date, last N days.
        try:
            rows = await conn.fetch(
                """
                SELECT
                    (NOW() - created_at)::interval AS age,
                    COUNT(*) AS cnt
                FROM incidents
                WHERE created_at > NOW() - ($1 || ' days')::interval
                GROUP BY date_trunc('day', created_at)
                ORDER BY date_trunc('day', created_at) ASC
                """,
                str(days),
            )
            # Rough bucket: day index = days - 1 - floor(age_days)
            for row in rows:
                age_days = row["age"].days if row["age"] else 0
                idx = days - 1 - age_days
                if 0 <= idx < days:
                    incidents_per_day[idx] = int(row["cnt"])
        except Exception:
            logger.exception("kpi-trends: incidents query failed")

        # 2) L1 success rate per day — from execution_telemetry.
        try:
            rows = await conn.fetch(
                """
                SELECT
                    date_trunc('day', created_at) AS day,
                    COUNT(*) FILTER (WHERE status = 'success') AS success_count,
                    COUNT(*) AS total_count
                FROM execution_telemetry
                WHERE created_at > NOW() - ($1 || ' days')::interval
                GROUP BY date_trunc('day', created_at)
                ORDER BY day ASC
                """,
                str(days),
            )
            for row in rows:
                age_days = (datetime.now(timezone.utc).date() - row["day"].date()).days
                idx = days - 1 - age_days
                if 0 <= idx < days and row["total_count"] > 0:
                    l1_per_day[idx] = round(
                        float(row["success_count"]) / float(row["total_count"]) * 100, 1
                    )
        except Exception:
            logger.exception("kpi-trends: L1 rate query failed")

        # 3) Clients over time — count of active client_orgs per day.
        # Client count is slow-moving; cheap COUNT(*) at each day boundary.
        try:
            rows = await conn.fetch(
                """
                SELECT
                    generate_series(
                        date_trunc('day', NOW() - ($1 || ' days')::interval),
                        date_trunc('day', NOW()),
                        '1 day'::interval
                    ) AS day
                """,
                str(days),
            )
            # Snapshot the current count into each historical slot. This is
            # intentionally a flat line for now — per-day historical snapshots
            # would require a time-series table we don't have yet. The frontend
            # still renders it as "Clients", which is an accurate current value.
            current_count_row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM client_orgs WHERE status = 'active'"
            )
            current_count = int(current_count_row["cnt"]) if current_count_row else 0
            for i in range(days):
                clients_per_day[i] = current_count
        except Exception:
            logger.exception("kpi-trends: clients query failed")

    return {
        "days": days,
        "series": {
            "incidents_24h": incidents_per_day,
            "l1_rate": l1_per_day,
            "clients": clients_per_day,
        },
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/sla-strip")
async def get_dashboard_sla_strip(user: dict = Depends(auth_module.require_auth)):
    """Platform-wide SLA posture — feeds the DashboardSLAStrip component.

    Returns load-bearing metrics so customer success + compliance can see
    at a glance whether the platform is meeting contractual targets:

      - `healing_rate_24h`: L1+L2 auto-heal success rate over the last 24h
      - `ots_anchor_age_minutes`: age of the oldest pending OTS proof
        (HIPAA evidence-integrity signal)
      - `online_appliances_pct`: fraction of appliances reporting in the
        last 5 minutes (fleet availability)
      - `mfa_coverage_pct`: percentage of admin users with MFA enrolled
        (security posture)

    Null values indicate "no data" and render as "—" on the frontend;
    they never render as a breached SLA. Targets are static here (baked
    into the response) so the frontend never has to hard-code a threshold
    — the backend is the single source of truth.
    """
    from .fleet import get_pool
    from .tenant_middleware import admin_connection

    pool = await get_pool()
    healing_rate_24h: float | None = None
    ots_anchor_age_minutes: float | None = None
    online_appliances_pct: float | None = None
    mfa_coverage_pct: float | None = None

    async with admin_connection(pool) as conn:
        # 1) Healing rate: successful L1/L2 executions over the last 24h.
        try:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE status = 'success') AS success_count,
                    COUNT(*) AS total_count
                FROM execution_telemetry
                WHERE created_at > NOW() - INTERVAL '24 hours'
            """)
            if row and row["total_count"] and row["total_count"] > 0:
                healing_rate_24h = float(row["success_count"]) / float(row["total_count"]) * 100
        except Exception:
            logger.exception("sla-strip: healing rate query failed")

        # 2) OTS anchor age: oldest pending proof, in minutes.
        try:
            row = await conn.fetchrow("""
                SELECT EXTRACT(EPOCH FROM (NOW() - MIN(submitted_at))) AS oldest_pending_seconds
                FROM ots_proofs
                WHERE status = 'pending'
            """)
            if row and row["oldest_pending_seconds"] is not None:
                ots_anchor_age_minutes = float(row["oldest_pending_seconds"]) / 60.0
            else:
                # No pending proofs — SLA is trivially met. Report 0 min.
                ots_anchor_age_minutes = 0.0
        except Exception:
            logger.exception("sla-strip: OTS anchor query failed")

        # 3) Fleet availability: online appliances / total appliances.
        try:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE last_checkin > NOW() - INTERVAL '5 minutes') AS online,
                    COUNT(*) AS total
                FROM site_appliances
            """)
            if row and row["total"] and row["total"] > 0:
                online_appliances_pct = float(row["online"]) / float(row["total"]) * 100
        except Exception:
            logger.exception("sla-strip: fleet availability query failed")

        # 4) MFA coverage: fraction of admin users with MFA enrolled.
        # Compliance control — HIPAA §164.312(d) person/entity auth.
        try:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE mfa_enabled = true) AS enrolled,
                    COUNT(*) AS total
                FROM admin_users
                WHERE is_active = true
            """)
            if row and row["total"] and row["total"] > 0:
                mfa_coverage_pct = float(row["enrolled"]) / float(row["total"]) * 100
        except Exception:
            logger.exception("sla-strip: MFA coverage query failed")

    return {
        "healing_rate_24h": healing_rate_24h,
        "healing_target": 85.0,
        "ots_anchor_age_minutes": ots_anchor_age_minutes,
        "ots_target_minutes": 120.0,
        "online_appliances_pct": online_appliances_pct,
        "fleet_target": 95.0,
        "mfa_coverage_pct": mfa_coverage_pct,
        "mfa_target": 100.0,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/flywheel-intelligence")
async def get_flywheel_intelligence(db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
    """Flywheel intelligence dashboard: recurrence velocity, auto-promotions,
    cross-incident correlations.

    Round Table: "Show the flywheel learning — recurrence rate trending down,
    promotions increasing, correlations discovered."
    """
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # Recurrence velocity — chronic patterns
        try:
            chronic = await conn.fetch("""
                SELECT site_id, incident_type, resolved_4h, resolved_7d,
                       velocity_per_hour, last_l1_runbook,
                       recurrence_broken_at, recurrence_broken_by_runbook
                FROM incident_recurrence_velocity
                WHERE is_chronic = true
                ORDER BY velocity_per_hour DESC
                LIMIT 20
            """)
        except Exception:
            chronic = []

        # Global recurrence rate: % of incidents that recur within 4h
        try:
            recurrence_row = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE resolved_4h >= 3) as chronic_types,
                    COUNT(*) as total_types,
                    COALESCE(SUM(resolved_4h), 0) as total_recurrences_4h,
                    COALESCE(SUM(resolved_7d), 0) as total_resolved_7d
                FROM incident_recurrence_velocity
            """)
        except Exception:
            recurrence_row = None

        # Recent auto-promotions
        try:
            promotions = await conn.fetch("""
                SELECT rule_id, incident_pattern->>'incident_type' as incident_type,
                       runbook_id, confidence, description, created_at
                FROM l1_rules
                WHERE source = 'flywheel_recurrence'
                ORDER BY created_at DESC
                LIMIT 10
            """)
        except Exception:
            promotions = []

        # Cross-incident correlations
        try:
            correlations = await conn.fetch("""
                SELECT site_id, incident_type_a, incident_type_b,
                       co_occurrence_count, avg_gap_seconds, confidence
                FROM incident_correlation_pairs
                WHERE confidence >= 0.3
                ORDER BY confidence DESC
                LIMIT 20
            """)
        except Exception:
            correlations = []

        # L2 recurrence decisions
        try:
            l2_recurrence = await conn.fetchrow("""
                SELECT
                    COUNT(*) as total_recurrence_decisions,
                    COUNT(*) FILTER (WHERE runbook_id IS NOT NULL AND confidence >= 0.6) as actionable
                FROM l2_decisions
                WHERE escalation_reason = 'recurrence'
                  AND created_at > NOW() - INTERVAL '7 days'
            """)
        except Exception:
            l2_recurrence = None

    recurrence_rate = 0.0
    if recurrence_row and recurrence_row["total_resolved_7d"] and recurrence_row["total_resolved_7d"] > 0:
        recurrence_rate = round(
            recurrence_row["total_recurrences_4h"] / recurrence_row["total_resolved_7d"] * 100, 1
        )

    return {
        "recurrence_rate_pct": recurrence_rate,
        "chronic_patterns": [dict(r) for r in chronic],
        "chronic_count": recurrence_row["chronic_types"] if recurrence_row else 0,
        "total_types_tracked": recurrence_row["total_types"] if recurrence_row else 0,
        "auto_promotions": [
            {**dict(r), "created_at": r["created_at"].isoformat() if r["created_at"] else None}
            for r in promotions
        ],
        "correlations": [dict(r) for r in correlations],
        "l2_recurrence_decisions": {
            "total": l2_recurrence["total_recurrence_decisions"] if l2_recurrence else 0,
            "actionable": l2_recurrence["actionable"] if l2_recurrence else 0,
        },
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/l2-budget/{site_id}")
async def get_l2_budget(
    site_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """L2 spend transparency: show this site's daily budget + current usage.

    Exposes the contextual budget algorithm state so the admin UI can
    display "L2 budget: $0.50/day. Used: $0.18. Remaining: $0.32." and
    surface when a site is approaching its cap.
    """
    from dashboard_api.l2_planner import compute_l2_budget_context

    # Pattern name is optional here — we use a sentinel to get site-wide state
    # without incrementing any per-pattern counters.
    ctx = await compute_l2_budget_context(site_id, "__budget_query__")

    # Fleet-wide patterns summary: list all patterns that called L2 today
    from .fleet import get_pool
    from .tenant_middleware import admin_connection
    pool = await get_pool()
    patterns: list[dict] = []
    async with admin_connection(pool) as conn:
        try:
            rows = await conn.fetch(
                """
                SELECT incident_type, call_count, total_cost_usd,
                       last_runbook_id, last_confidence, last_call_at
                FROM l2_rate_limits
                WHERE site_id = $1 AND day = CURRENT_DATE
                ORDER BY total_cost_usd DESC
                """,
                site_id,
            )
            for r in rows:
                d = dict(r)
                d["total_cost_usd"] = float(d["total_cost_usd"] or 0)
                d["last_confidence"] = float(d["last_confidence"]) if d["last_confidence"] is not None else None
                d["last_call_at"] = d["last_call_at"].isoformat() if d["last_call_at"] else None
                patterns.append(d)
        except Exception:
            pass

    spent = ctx.get("spent_today_usd", 0.0)
    budget = ctx.get("daily_budget_usd", 0.0)
    remaining = max(0.0, budget - spent)
    pct_used = round((spent / budget) * 100, 1) if budget > 0 else 0.0

    return {
        "site_id": site_id,
        "tier": ctx.get("tier"),
        "device_count": ctx.get("device_count", 0),
        "daily_budget_usd": budget,
        "spent_today_usd": spent,
        "remaining_usd": round(remaining, 4),
        "percent_used": pct_used,
        "distinct_patterns_today": ctx.get("distinct_patterns_today", 0),
        "patterns": patterns,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/stats", response_model=GlobalStats)
async def get_global_stats(db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
    """Get aggregate statistics across all clients."""
    stats = await get_global_stats_from_db(db)

    # Get Go agent stats and drift check count from asyncpg pool
    total_go_agents = 0
    active_drift_checks = 47  # default: all known check types
    active_threats = 0
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

            # Count active threats (ransomware/brute force, critical, unresolved)
            threat_row = await conn.fetchrow("""
                SELECT COUNT(*) as cnt FROM incidents
                WHERE check_type IN ('ransomware_indicator', 'brute_force_detected')
                  AND severity = 'critical'
                  AND status != 'resolved'
            """)
            active_threats = threat_row["cnt"] if threat_row else 0
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
        active_threats=active_threats,
        computed_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/stats/deltas", response_model=StatsDeltas)
async def get_stats_deltas(db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
    """Week-over-week delta indicators for dashboard KPI cards.

    Compares current metrics against 7-day-ago values:
    - compliance_delta: current avg compliance score minus 7d-ago score
    - incidents_24h_delta: current 24h count minus 7d-ago 24h count
    - l1_rate_delta: current L1 resolution % minus 7d-ago %
    - clients_delta: current site count minus 7d-ago count
    """
    try:
        # --- Current compliance score (same logic as get_global_stats) ---
        comp_now = await execute_with_retry(db,text("""
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
        comp_prev = await execute_with_retry(db,text("""
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
        inc_row = await execute_with_retry(db,text("""
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

        l1_prev_row = await execute_with_retry(db,text("""
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
        sites_now = await execute_with_retry(db,text("SELECT COUNT(*) as cnt FROM sites"))
        sn = sites_now.fetchone()

        # Sites that existed 7 days ago (created_at <= 7 days ago)
        sites_prev = await execute_with_retry(db,text("""
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
async def get_fleet_posture(db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
    """Fleet-wide health matrix: per-site health, incidents, trend, sorted by needs-attention."""
    try:
        result = await execute_with_retry(db,text("""
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
            interval_hours = 24
            trunc = "date_trunc('hour', i.reported_at)"
        elif window == "7d":
            bucket = "day"
            interval_hours = 168  # 7 * 24
            trunc = "date_trunc('day', i.reported_at)"
        else:
            bucket = "day"
            interval_hours = 720  # 30 * 24
            trunc = "date_trunc('day', i.reported_at)"

        site_join = "JOIN appliances a ON a.id = i.appliance_id" if site_id else ""
        site_filter = "AND a.site_id = :site_id" if site_id else ""
        params = {"interval_hours": interval_hours}
        if site_id:
            params["site_id"] = site_id

        result = await execute_with_retry(db,text(f"""
            SELECT
                {trunc} as bucket,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L1') as l1,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L2') as l2,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L3') as l3,
                COUNT(*) FILTER (WHERE i.status != 'resolved') as unresolved,
                COUNT(*) as total
            FROM incidents i
            {site_join}
            WHERE i.reported_at > NOW() - :interval_hours * INTERVAL '1 hour'
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
            interval_hours = 24
        elif window == "7d":
            interval_hours = 168  # 7 * 24
        else:
            interval_hours = 720  # 30 * 24

        site_join = "JOIN appliances a ON a.id = i.appliance_id" if site_id else ""
        site_filter = "AND a.site_id = :site_id" if site_id else ""
        params = {"interval_hours": interval_hours}
        if site_id:
            params["site_id"] = site_id

        # Tier counts
        tier_result = await execute_with_retry(db,text(f"""
            SELECT
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L1') as l1,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L2') as l2,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L3') as l3,
                COUNT(*) FILTER (WHERE i.resolution_tier IS NULL) as unclassified,
                COUNT(*) FILTER (WHERE i.status != 'resolved') as unresolved,
                COUNT(*) as total
            FROM incidents i
            {site_join}
            WHERE i.reported_at > NOW() - :interval_hours * INTERVAL '1 hour'
            {site_filter}
        """), params)
        tier = tier_result.fetchone()

        # Top incident types with tier breakdown
        types_result = await execute_with_retry(db,text(f"""
            SELECT
                COALESCE(i.incident_type, i.check_type, 'unknown') as incident_type,
                COUNT(*) as count,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L1') as l1,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L2') as l2,
                COUNT(*) FILTER (WHERE i.resolution_tier = 'L3') as l3
            FROM incidents i
            {site_join}
            WHERE i.reported_at > NOW() - :interval_hours * INTERVAL '1 hour'
            {site_filter}
            GROUP BY COALESCE(i.incident_type, i.check_type, 'unknown')
            ORDER BY count DESC
            LIMIT 8
        """), params)
        types_rows = types_result.fetchall()

        # MTTR by tier (average minutes from reported to resolved)
        mttr_result = await execute_with_retry(db,text(f"""
            SELECT
                i.resolution_tier,
                ROUND(AVG(EXTRACT(EPOCH FROM (i.resolved_at - i.reported_at)) / 60)::numeric, 1) as avg_minutes,
                COUNT(*) as resolved_count
            FROM incidents i
            {site_join}
            WHERE i.reported_at > NOW() - :interval_hours * INTERVAL '1 hour'
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
async def get_attention_required(db: AsyncSession = Depends(get_db), user: dict = Depends(auth_module.require_auth)):
    """Items that need human attention: L3 escalations, failed healings, offline appliances."""
    org_scope = user.get("org_scope")
    org_filter = "AND s.client_org_id = ANY(:org_ids)" if org_scope else ""
    org_params = {"org_ids": org_scope} if org_scope else {}
    try:
        # L3 escalations (unresolved)
        l3_result = await execute_with_retry(db,text(f"""
            SELECT
                i.id, a.site_id, i.incident_type, i.check_type, i.severity,
                i.reported_at, s.clinic_name
            FROM incidents i
            JOIN appliances a ON a.id = i.appliance_id
            LEFT JOIN sites s ON s.site_id = a.site_id
            WHERE i.resolution_tier = 'L3'
            AND i.status != 'resolved'
            {org_filter}
            ORDER BY i.reported_at DESC
            LIMIT 20
        """), org_params)
        l3_rows = l3_result.fetchall()

        # Repeat offenders: same check_type, same site, 3+ incidents in 24h (healing not sticking)
        repeat_result = await execute_with_retry(db,text(f"""
            SELECT
                a.site_id, i.check_type, COUNT(*) as occurrences,
                MAX(i.reported_at) as latest,
                s.clinic_name
            FROM incidents i
            JOIN appliances a ON a.id = i.appliance_id
            LEFT JOIN sites s ON s.site_id = a.site_id
            WHERE i.reported_at > NOW() - INTERVAL '24 hours'
            AND i.resolution_tier = 'L1'
            {org_filter}
            GROUP BY a.site_id, i.check_type, s.clinic_name
            HAVING COUNT(*) >= 3
            ORDER BY COUNT(*) DESC
            LIMIT 20
        """), org_params)
        repeat_rows = repeat_result.fetchall()

        # Offline appliances (no checkin > 30 min)
        offline_result = await execute_with_retry(db,text(f"""
            SELECT
                sa.site_id, sa.hostname, sa.last_checkin, sa.agent_version,
                s.clinic_name
            FROM site_appliances sa
            LEFT JOIN sites s ON s.site_id = sa.site_id
            WHERE (sa.last_checkin < NOW() - INTERVAL '30 minutes'
            OR sa.last_checkin IS NULL)
            {"AND s.client_org_id = ANY(:org_ids)" if org_scope else ""}
            ORDER BY sa.last_checkin ASC NULLS FIRST
            LIMIT 20
        """), org_params)
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
                "title": f"Recurring: {r.check_type} ({r.occurrences}x in 24h)",
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
    site_result = await execute_with_retry(db,
        text("SELECT site_id FROM sites WHERE site_id = :site_id"),
        {"site_id": site_id}
    )
    if not site_result.fetchone():
        raise HTTPException(status_code=404, detail=f"Client {site_id} not found")

    # Get appliance counts
    appliance_result = await execute_with_retry(db,text("""
        SELECT COUNT(*) as total,
               COUNT(*) FILTER (WHERE status = 'online') as online
        FROM site_appliances WHERE site_id = :site_id
    """), {"site_id": site_id})
    app_row = appliance_result.fetchone()

    # Get incident stats
    incident_result = await execute_with_retry(db,text("""
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
    compliance_result = await execute_with_retry(db,text("""
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

        result = await execute_with_retry(db,text(query), params)
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
        result = await execute_with_retry(db,text("""
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
    await execute_with_retry(db,text("""
        UPDATE notifications
        SET is_read = TRUE, read_at = NOW()
        WHERE id = :id
    """), {"id": notification_id})
    await db.commit()
    return {"status": "ok", "notification_id": notification_id}


@router.post("/notifications/read-all")
async def mark_all_notifications_read(db: AsyncSession = Depends(get_db)):
    """Mark all notifications as read."""
    result = await execute_with_retry(db,text("""
        UPDATE notifications
        SET is_read = TRUE, read_at = NOW()
        WHERE is_read = FALSE
    """))
    await db.commit()
    return {"status": "ok", "marked_count": result.rowcount}


@router.post("/notifications/{notification_id}/dismiss")
async def dismiss_notification(notification_id: str, db: AsyncSession = Depends(get_db)):
    """Dismiss a notification (hide it)."""
    await execute_with_retry(db,text("""
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
    result = await execute_with_retry(db,text("""
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
    await execute_with_retry(db,text("""
        CREATE TABLE IF NOT EXISTS system_settings (
            id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            settings JSONB NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by VARCHAR(255)
        )
    """))
    await execute_with_retry(db,text("""
        INSERT INTO system_settings (id, settings)
        VALUES (1, '{}')
        ON CONFLICT (id) DO NOTHING
    """))
    await db.commit()


@router.get("/admin/settings", response_model=SystemSettingsModel)
async def get_system_settings(db: AsyncSession = Depends(get_db)):
    """Get current system settings."""
    await ensure_settings_table(db)

    result = await execute_with_retry(db,text(
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
    await execute_with_retry(db,
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
    result = await execute_with_retry(db,
        text("DELETE FROM execution_telemetry WHERE created_at < NOW() - INTERVAL '1 day' * :days"),
        {"days": retention_days}
    )
    await db.commit()

    return {"deleted": result.rowcount, "retention_days": retention_days}


@router.post("/admin/settings/reset-learning")
async def reset_learning_data(db: AsyncSession = Depends(get_db)):
    """Reset all learning data (patterns and L1 rules)."""
    patterns_result = await execute_with_retry(db,text("DELETE FROM patterns"))
    rules_result = await execute_with_retry(db,text(
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
    result = await execute_with_retry(db,
        text("SELECT rule_id, runbook_id, source, enabled FROM l1_rules WHERE rule_id = :rid"),
        {"rid": rule_id}
    )
    rule = result.fetchone()
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}")

    if not rule.enabled:
        return {"status": "already_disabled", "rule_id": rule_id}

    await execute_with_retry(db,
        text("UPDATE l1_rules SET enabled = false WHERE rule_id = :rid"),
        {"rid": rule_id}
    )
    # Audit log
    await execute_with_retry(db,text("""
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
    result = await execute_with_retry(db,
        text("UPDATE l1_rules SET enabled = true WHERE rule_id = :rid RETURNING rule_id"),
        {"rid": rule_id}
    )
    row = result.fetchone()
    await db.commit()
    if not row:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}")
    return {"status": "enabled", "rule_id": rule_id}


# =============================================================================
# L1 RULE BUILDER ENDPOINTS
# =============================================================================


@router.get("/admin/rules")
async def list_l1_rules(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """List all L1 rules with runbook names."""
    from .fleet import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.rule_id, r.incident_pattern, r.runbook_id, r.confidence,
                   r.enabled, r.source, r.match_count, r.success_count, r.success_rate,
                   r.created_at, rb.name as runbook_name
            FROM l1_rules r
            LEFT JOIN runbooks rb ON rb.runbook_id = r.runbook_id
            ORDER BY r.enabled DESC, r.created_at DESC
        """)
        return [dict(r) for r in rows]


@router.post("/admin/rules")
async def create_l1_rule(
    request: Request,
    user: dict = Depends(auth_module.require_auth),
):
    """Create a new manual L1 rule."""
    body = await request.json()
    incident_type = body.get("incident_type")
    runbook_id = body.get("runbook_id")
    confidence = body.get("confidence", 0.90)

    if not incident_type or not runbook_id:
        raise HTTPException(status_code=400, detail="incident_type and runbook_id are required")

    rule_id = f"L1-MANUAL-{incident_type.upper().replace(' ', '-')[:30]}"
    incident_pattern = json.dumps({"incident_type": incident_type})

    from .fleet import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO l1_rules (rule_id, incident_pattern, runbook_id, confidence, enabled, source)
            VALUES ($1, $2::jsonb, $3, $4, true, 'manual')
            ON CONFLICT (rule_id) DO UPDATE SET
                runbook_id = EXCLUDED.runbook_id,
                confidence = EXCLUDED.confidence,
                enabled = true
        """, rule_id, incident_pattern, runbook_id, confidence)

    # Audit log
    from .fleet import get_pool as _gp
    pool2 = await _gp()
    async with pool2.acquire() as conn:
        await conn.execute("""
            INSERT INTO audit_log (event_type, actor, resource_type, resource_id, details)
            VALUES ('rule.created', $1, 'l1_rule', $2, $3::jsonb)
        """, user.get("username", "admin"), rule_id, json.dumps({
            "incident_type": incident_type,
            "runbook_id": runbook_id,
            "confidence": confidence,
            "source": "manual",
        }))

    return {"rule_id": rule_id, "status": "created"}


@router.delete("/admin/rules/{rule_id}")
async def delete_l1_rule(
    rule_id: str,
    request: Request,
    user: dict = Depends(auth_module.require_auth),
):
    """Delete an L1 rule."""
    from .fleet import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM l1_rules WHERE rule_id = $1", rule_id
        )

    # Audit log
    pool2 = await get_pool()
    async with pool2.acquire() as conn:
        await conn.execute("""
            INSERT INTO audit_log (event_type, actor, resource_type, resource_id, details)
            VALUES ('rule.deleted', $1, 'l1_rule', $2, $3::jsonb)
        """, user.get("username", "admin"), rule_id, json.dumps({
            "action": "manual_delete",
        }))

    return {"status": "deleted", "rule_id": rule_id}


@router.get("/admin/rules/incident-types")
async def list_incident_types(
    request: Request,
    user: dict = Depends(auth_module.require_auth),
):
    """List known incident types from recent incidents for dropdown selection."""
    from .fleet import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT incident_type, COUNT(*) as occurrences
            FROM incidents
            WHERE created_at > NOW() - INTERVAL '30 days'
              AND incident_type IS NOT NULL
            GROUP BY incident_type
            ORDER BY occurrences DESC
        """)
    return [{"type": r["incident_type"], "count": r["occurrences"]} for r in rows]


@router.post("/admin/rules/test")
async def test_l1_rule(
    request: Request,
    user: dict = Depends(auth_module.require_auth),
):
    """Dry-run a rule against recent incidents to preview matches."""
    body = await request.json()
    incident_type = body.get("incident_type")
    if not incident_type:
        raise HTTPException(status_code=400, detail="incident_type is required")

    from .fleet import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        matches = await conn.fetch("""
            SELECT id, incident_type, check_type, details->>'hostname' as hostname,
                   severity, created_at
            FROM incidents
            WHERE incident_type = $1
            AND created_at > NOW() - INTERVAL '7 days'
            ORDER BY created_at DESC
            LIMIT 20
        """, incident_type)
    return {"matches": [dict(m) for m in matches], "count": len(matches)}


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

    count_result = await execute_with_retry(db,text(f"""
        SELECT COUNT(*) FROM client_orgs co {where_clause}
    """), params)
    total = count_result.scalar()

    params["limit"] = limit
    params["offset"] = offset

    result = await execute_with_retry(db,text(f"""
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
    site_org_result = await execute_with_retry(db,text(
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

    result = await execute_with_retry(db,text("""
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


@router.put("/organizations/{org_id}")
async def update_organization(
    org_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Update an organization's details."""
    body = await request.json()
    fields = []
    params: dict = {"org_id": org_id}
    for key in ("name", "primary_email", "primary_phone", "practice_type", "provider_count", "status"):
        if key in body:
            fields.append(f"{key} = :{key}")
            params[key] = body[key]
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    sql = f"UPDATE client_orgs SET {', '.join(fields)}, updated_at = NOW() WHERE id = :org_id RETURNING id, name"
    result = await execute_with_retry(db,text(sql), params)
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")
    await db.commit()
    return {"status": "updated", "id": str(row.id), "name": row.name}


@router.delete("/organizations/{org_id}")
async def delete_organization(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Delete an organization. Fails if it has sites assigned."""
    # Check for assigned sites
    sites = await execute_with_retry(db,text(
        "SELECT COUNT(*) FROM sites WHERE client_org_id = :org_id"
    ), {"org_id": org_id})
    count = sites.scalar()
    if count and count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete organization with {count} assigned site(s). Unassign sites first."
        )
    result = await execute_with_retry(db,text(
        "DELETE FROM client_orgs WHERE id = :org_id RETURNING id, name"
    ), {"org_id": org_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")
    await db.commit()
    return {"status": "deleted", "id": str(row.id), "name": row.name}


@router.get("/organizations/{org_id}")
async def get_organization_detail(
    org_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(auth_module.require_auth),
):
    """Get organization detail with nested site list."""
    auth_module._check_org_access(user, org_id)
    # Get org info
    result = await execute_with_retry(db,text("""
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
    sites_result = await execute_with_retry(db,text("""
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
    result = await execute_with_retry(db,text("""
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
    org = await execute_with_retry(db,text("SELECT id FROM client_orgs WHERE id = :org_id"), {"org_id": org_id})
    if not org.fetchone():
        raise HTTPException(status_code=404, detail="Organization not found")

    # Verify site exists
    site = await execute_with_retry(db,text("SELECT site_id FROM sites WHERE site_id = :site_id"), {"site_id": site_id})
    if not site.fetchone():
        raise HTTPException(status_code=404, detail="Site not found")

    await execute_with_retry(db,text("""
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
    await execute_with_retry(db,text("""
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
    org_check = await execute_with_retry(db,
        text("SELECT id FROM client_orgs WHERE id = :org_id"),
        {"org_id": org_id}
    )
    if not org_check.fetchone():
        raise HTTPException(status_code=404, detail="Organization not found")

    # Get org's site_ids
    sites_result = await execute_with_retry(db,
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
    incident_result = await execute_with_retry(db,text("""
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
    healing_inc_result = await execute_with_retry(db,text("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE i.status = 'resolved') as resolved
        FROM incidents i
        WHERE i.site_id = ANY(:site_ids)
    """), {"site_ids": site_ids})
    heal_inc = healing_inc_result.fetchone()

    healing_ord_result = await execute_with_retry(db,text("""
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
    fleet_result = await execute_with_retry(db,text("""
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

    # Per-category compliance breakdown — uses DISTINCT ON to get the latest
    # bundle per (site_id, check_type) instead of a correlated subquery that
    # times out on 200K+ rows.
    cat_result = await execute_with_retry(db,text("""
        SELECT check_type,
            COUNT(*) FILTER (WHERE check_result = 'pass') as passes,
            COUNT(*) FILTER (WHERE check_result = 'fail') as fails,
            COUNT(*) as total
        FROM (
            SELECT DISTINCT ON (site_id, check_type)
                site_id, check_type, check_result
            FROM compliance_bundles
            WHERE site_id = ANY(:site_ids)
            ORDER BY site_id, check_type, checked_at DESC
        ) latest
        GROUP BY check_type
        ORDER BY check_type
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
    go_agent_result = await execute_with_retry(db,text("""
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
        "evidence_witnesses": await _get_org_witness_stats(db, site_ids),
    }


async def _get_org_witness_stats(db, site_ids: list) -> dict:
    """Witness attestation stats for an org's sites."""
    try:
        result = await execute_with_retry(db,text("""
            SELECT
                count(DISTINCT wa.bundle_id) as witnessed_bundles,
                count(*) as total_attestations,
                count(*) FILTER (WHERE wa.created_at > NOW() - interval '24h') as attestations_24h
            FROM witness_attestations wa
            WHERE wa.bundle_id IN (
                SELECT bundle_id FROM compliance_bundles WHERE site_id = ANY(:site_ids)
            )
        """), {"site_ids": site_ids})
        row = result.fetchone()
        total_bundles_result = await execute_with_retry(db,text("""
            SELECT count(DISTINCT bundle_id) FROM compliance_bundles
            WHERE site_id = ANY(:site_ids) AND checked_at > NOW() - interval '24h'
        """), {"site_ids": site_ids})
        total_24h = total_bundles_result.scalar() or 0
        witnessed_24h = row.attestations_24h if row else 0
        return {
            "total_attestations": row.total_attestations if row else 0,
            "witnessed_bundles": row.witnessed_bundles if row else 0,
            "attestations_24h": witnessed_24h,
            "coverage_pct": round(witnessed_24h / total_24h * 100, 1) if total_24h > 0 else 0,
        }
    except Exception:
        return {"total_attestations": 0, "witnessed_bundles": 0, "attestations_24h": 0, "coverage_pct": 0}


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

    result = await execute_with_retry(db,text(f"""
        SELECT i.id, i.site_id, s.clinic_name, i.incident_type, i.severity,
               i.status, i.created_at, i.resolved_at
        FROM incidents i
        JOIN sites s ON s.site_id = i.site_id
        WHERE {where}
        ORDER BY i.created_at DESC
        LIMIT :limit OFFSET :offset
    """), {**params, "limit": limit, "offset": offset})
    rows = result.fetchall()

    count_result = await execute_with_retry(db,text(f"""
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
# ORG-LEVEL INVENTORY (multi-appliance aggregation)
# =============================================================================

@router.get("/organizations/{org_id}/devices")
async def get_organization_devices(
    org_id: str,
    user: dict = Depends(auth_module.require_auth),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Aggregate device inventory across all sites in an organization."""
    auth_module._check_org_access(user, org_id)
    from .fleet import get_pool

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        site_ids = [r['site_id'] for r in await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1", org_id
        )]
        if not site_ids:
            return {"devices": [], "summary": {}, "total": 0}

        devices = await conn.fetch("""
            SELECT d.id, d.site_id, s.clinic_name, d.hostname, d.ip_address,
                   d.mac_address, d.device_type, d.os_name, d.compliance_status,
                   d.device_status, d.last_seen_at, d.owner_appliance_id,
                   a_owner.site_id as owner_site_id
            FROM discovered_devices d
            JOIN sites s ON d.site_id = s.site_id
            LEFT JOIN appliances a_owner ON d.owner_appliance_id = a_owner.id
            WHERE d.site_id = ANY($1)
            ORDER BY d.ip_address
            LIMIT $2 OFFSET $3
        """, site_ids, limit, offset)

        total = await conn.fetchval(
            "SELECT count(*) FROM discovered_devices WHERE site_id = ANY($1)", site_ids
        )

        summary = await conn.fetchrow("""
            SELECT count(*) as total,
                count(*) FILTER (WHERE compliance_status = 'compliant') as compliant,
                count(*) FILTER (WHERE compliance_status = 'drifted') as drifted,
                count(*) FILTER (WHERE compliance_status = 'unknown' OR compliance_status IS NULL) as unknown,
                count(DISTINCT site_id) as site_count
            FROM discovered_devices WHERE site_id = ANY($1)
        """, site_ids)

    return {
        "devices": [
            {
                "id": str(d['id']),
                "site_id": d['site_id'],
                "clinic_name": d['clinic_name'],
                "hostname": d['hostname'],
                "ip_address": d['ip_address'],
                "mac_address": d['mac_address'],
                "device_type": d['device_type'],
                "os_name": d['os_name'],
                "compliance_status": d['compliance_status'] or 'unknown',
                "device_status": d['device_status'],
                "last_seen": d['last_seen_at'].isoformat() if d['last_seen_at'] else None,
                "owner_site_id": d['owner_site_id'],
            }
            for d in devices
        ],
        "summary": {
            "total": summary['total'],
            "compliant": summary['compliant'],
            "drifted": summary['drifted'],
            "unknown": summary['unknown'],
            "site_count": summary['site_count'],
            "compliance_rate": round(summary['compliant'] / summary['total'] * 100, 1) if summary['total'] > 0 else 0,
        },
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/organizations/{org_id}/workstations")
async def get_organization_workstations(
    org_id: str,
    user: dict = Depends(auth_module.require_auth),
):
    """Aggregate workstation compliance across all sites in an organization."""
    auth_module._check_org_access(user, org_id)
    from .fleet import get_pool

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        site_ids = [r['site_id'] for r in await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1", org_id
        )]
        if not site_ids:
            return {"workstations": [], "summary": None}

        ws_rows = await conn.fetch("""
            SELECT w.id, w.site_id, s.clinic_name, w.hostname, w.ip_address,
                   w.os_name, w.online, w.compliance_status, w.last_compliance_check,
                   w.compliance_percentage, w.last_seen
            FROM workstations w
            JOIN sites s ON w.site_id = s.site_id
            WHERE w.site_id = ANY($1)
            ORDER BY w.hostname
        """, site_ids)

        summary_row = await conn.fetchrow("""
            SELECT count(*) as total,
                count(*) FILTER (WHERE online) as online,
                count(*) FILTER (WHERE compliance_status = 'compliant') as compliant,
                count(*) FILTER (WHERE compliance_status = 'drifted') as drifted,
                count(*) FILTER (WHERE compliance_status = 'error') as error,
                count(*) FILTER (WHERE compliance_status IS NULL OR compliance_status = 'unknown') as unknown
            FROM workstations WHERE site_id = ANY($1)
        """, site_ids)

        total = summary_row['total'] or 0
        compliant = summary_row['compliant'] or 0

    return {
        "workstations": [
            {
                "id": str(ws['id']),
                "site_id": ws['site_id'],
                "clinic_name": ws['clinic_name'],
                "hostname": ws['hostname'],
                "ip_address": ws['ip_address'],
                "os_name": ws['os_name'],
                "online": ws['online'],
                "compliance_status": ws['compliance_status'] or 'unknown',
                "last_compliance_check": ws['last_compliance_check'].isoformat() if ws['last_compliance_check'] else None,
                "compliance_percentage": float(ws['compliance_percentage'] or 0),
                "last_seen": ws['last_seen'].isoformat() if ws['last_seen'] else None,
            }
            for ws in ws_rows
        ],
        "summary": {
            "total_workstations": total,
            "online_workstations": summary_row['online'] or 0,
            "compliant_workstations": compliant,
            "drifted_workstations": summary_row['drifted'] or 0,
            "error_workstations": summary_row['error'] or 0,
            "unknown_workstations": summary_row['unknown'] or 0,
            "overall_compliance_rate": round(compliant / total * 100, 1) if total > 0 else 0,
        },
    }


@router.get("/organizations/{org_id}/agents")
async def get_organization_agents(
    org_id: str,
    user: dict = Depends(auth_module.require_auth),
):
    """Aggregate Go agent status across all sites in an organization."""
    auth_module._check_org_access(user, org_id)
    from .fleet import get_pool

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        site_ids = [r['site_id'] for r in await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1", org_id
        )]
        if not site_ids:
            return {"agents": [], "summary": {"total": 0, "active": 0, "offline": 0}}

        now = datetime.now(timezone.utc)
        rows = await conn.fetch("""
            SELECT g.agent_id, g.hostname, g.ip_address, g.site_id,
                   s.clinic_name,
                   COALESCE(NULLIF(g.os_version, ''), g.os_name) AS os_version,
                   g.agent_version, g.status, g.last_heartbeat,
                   g.checks_passed, g.checks_total, g.compliance_percentage
            FROM go_agents g
            JOIN sites s ON g.site_id = s.site_id
            WHERE g.site_id = ANY($1)
            ORDER BY g.last_heartbeat DESC NULLS LAST
        """, site_ids)

    agents = []
    summary = {"active": 0, "stale": 0, "offline": 0, "never": 0}
    for r in rows:
        hb = r['last_heartbeat']
        if hb is None:
            derived = "never"
        else:
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            age = now - hb
            if age < timedelta(minutes=5):
                derived = "active"
            elif age < timedelta(hours=1):
                derived = "stale"
            else:
                derived = "offline"
        summary[derived] += 1
        agents.append({
            "agent_id": r['agent_id'],
            "hostname": r['hostname'],
            "ip_address": r['ip_address'],
            "site_id": r['site_id'],
            "clinic_name": r['clinic_name'],
            "os_version": r['os_version'],
            "agent_version": r['agent_version'],
            "derived_status": derived,
            "last_heartbeat": r['last_heartbeat'].isoformat() if r['last_heartbeat'] else None,
            "compliance_percentage": float(r['compliance_percentage'] or 0),
        })

    return {
        "agents": agents,
        "summary": {**summary, "total": len(agents)},
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

        # Get disabled checks (includes both disabled and not_applicable)
        disabled = await conn.fetch(
            "SELECT check_type FROM site_drift_config WHERE site_id = $1 AND (enabled = false OR status = 'not_applicable')", site_id
        )
        disabled_set = {r["check_type"] for r in disabled}
        if not disabled:
            defaults = await conn.fetch(
                "SELECT check_type FROM site_drift_config WHERE site_id = '__defaults__' AND (enabled = false OR status = 'not_applicable')"
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
                        "linux_firewall", "network_profile"],
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
                        "winrm", "dns_config"],
        }
        reverse_map = {}
        for cat, types in categories.items():
            for ct in types:
                reverse_map[ct] = cat

        # --- Source 1: Compliance bundles (drift scans) ---
        # Deduplicate: latest result per (hostname, check_type) only.
        # Old query grabbed latest 50 rows which could all be the same
        # check looping every 3 minutes, inflating pass/fail counts.
        bundles = await conn.fetch("""
            SELECT DISTINCT ON (hostname, check_type) checks, check_type
            FROM (
                SELECT checks,
                       check_type,
                       COALESCE(
                           checks->0->>'hostname',
                           check_type
                       ) as hostname,
                       checked_at
                FROM compliance_bundles
                WHERE site_id = $1
                  AND checked_at > NOW() - INTERVAL '24 hours'
                  AND check_type NOT LIKE 'net_%'
                ORDER BY checked_at DESC
            ) sub
            ORDER BY hostname, check_type, checked_at DESC
        """, site_id)

        # Fallback: if no data in last 24h, use latest per check_type
        if not bundles:
            bundles = await conn.fetch("""
                SELECT DISTINCT ON (check_type) checks, check_type
                FROM compliance_bundles
                WHERE site_id = $1
                  AND check_type NOT LIKE 'net_%'
                ORDER BY check_type, checked_at DESC
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
                # Skip network monitoring checks — not compliance attestation
                if ct.startswith("net_"):
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

        # --- Source 2: Active incidents (informational only, NOT added to scores) ---
        # Incidents reflect the same failures already captured in compliance bundles.
        # Adding them to cat_fail would double-count, inflating failure rates.
        # We query them here for the incident_count metric, not for scoring.
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
            incident_fails += row["devices_affected"]

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

        # --- Compute per-category scores with HIPAA weighting ---
        from dashboard_api.db_queries import HIPAA_CATEGORY_WEIGHTS

        breakdown = {}
        weighted_sum = 0.0
        weight_sum = 0.0
        for cat in categories:
            total = cat_pass[cat] + cat_fail[cat] + cat_warn[cat]
            if total > 0:
                # Score = (passes + 0.5*warnings) / total * 100
                score = round(((cat_pass[cat] + 0.5 * cat_warn[cat]) / total) * 100)
                breakdown[cat] = score
                w = HIPAA_CATEGORY_WEIGHTS.get(cat, 0.06)
                weighted_sum += score * w
                weight_sum += w
            else:
                breakdown[cat] = None

        bundle_overall = round(weighted_sum / weight_sum, 1) if weight_sum > 0 else None

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

        # Trend: exclude network scan check types (net_*) — they're monitoring,
        # not compliance. Including them tanks the score because netscan produces
        # hundreds of "fail" bundles per day for unreachable hosts / unexpected ports.
        trend_rows = await conn.fetch("""
            SELECT
                DATE(checked_at) as date,
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE check_result IN ('pass', 'compliant')) as passed
            FROM compliance_bundles
            WHERE site_id = $1
              AND checked_at > NOW() - INTERVAL '30 days'
              AND check_result IS NOT NULL
              AND check_type NOT LIKE 'net_%'
            GROUP BY DATE(checked_at)
            ORDER BY date ASC
        """, site_id)

        trend = [
            {
                "date": r["date"].isoformat(),
                "score": round((r["passed"] / r["total"]) * 100, 1) if r["total"] > 0 else None
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
            SELECT check_type, enabled, notes,
                   COALESCE(status, CASE WHEN enabled THEN 'enabled' ELSE 'disabled' END) as status,
                   exception_reason
            FROM site_drift_config
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
                "status": override["status"] or ("enabled" if override["enabled"] else "disabled"),
                "exception_reason": override["exception_reason"] or "",
            })
        else:
            default_enabled = ct not in DEFAULT_DISABLED
            checks.append({
                "check_type": ct,
                "enabled": default_enabled,
                "platform": _check_platform(ct),
                "notes": "",
                "status": "enabled" if default_enabled else "disabled",
                "exception_reason": "",
            })

    # Include any site-specific checks not in the canonical list (custom checks)
    for ct, r in overrides.items():
        if ct not in {c["check_type"] for c in checks}:
            checks.append({
                "check_type": ct,
                "enabled": r["enabled"],
                "platform": _check_platform(ct),
                "notes": r["notes"] or "",
                "status": r["status"] or ("enabled" if r["enabled"] else "disabled"),
                "exception_reason": r["exception_reason"] or "",
            })

    return {"site_id": site_id, "checks": checks}


# Critical checks that cannot be disabled — disabling these breaks compliance monitoring
CRITICAL_DRIFT_CHECKS = {"firewall_status", "windows_defender", "audit_logging", "bitlocker_status"}


def _validate_drift_config_checks(checks):
    """Validate drift config safety bounds.

    Prevents partners/clients from:
    1. Disabling ALL checks (at least 1 must remain enabled or marked N/A)
    2. Disabling critical compliance checks (firewall, defender, audit, bitlocker)
       — marking critical checks as N/A IS allowed (requires documented reason)
    """
    # Works with both DriftCheckItem objects and plain dicts
    enabled_count = 0
    blocked_critical = []
    for item in checks:
        check_type = item.check_type if hasattr(item, "check_type") else item.get("check_type", "")
        enabled = item.enabled if hasattr(item, "enabled") else item.get("enabled", True)
        status = (item.status if hasattr(item, "status") else item.get("status")) or ("enabled" if enabled else "disabled")

        if enabled or status == "not_applicable":
            enabled_count += 1
        elif check_type in CRITICAL_DRIFT_CHECKS:
            # Critical checks can be N/A (with reason) but cannot be simply disabled
            blocked_critical.append(check_type)

    if blocked_critical:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot disable critical compliance checks: {', '.join(sorted(blocked_critical))}. "
            f"These checks are required for HIPAA compliance monitoring. "
            f"Use 'Not Applicable' with a documented reason if this check does not apply.",
        )

    if enabled_count < 1:
        raise HTTPException(
            status_code=400,
            detail="At least 1 drift check must remain enabled. Disabling all checks removes compliance monitoring.",
        )


class DriftCheckItem(BaseModel):
    check_type: str
    enabled: bool
    status: Optional[str] = None  # 'enabled', 'disabled', 'not_applicable'
    exception_reason: Optional[str] = None  # reason for N/A status


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

    # Safety bounds: prevent partners/clients from breaking compliance monitoring
    _validate_drift_config_checks(body.checks)

    from .fleet import get_pool
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        async with conn.transaction():
            for item in body.checks:
                # Derive status from fields: explicit status takes priority,
                # otherwise infer from enabled boolean
                status = item.status or ("enabled" if item.enabled else "disabled")
                if status not in ("enabled", "disabled", "not_applicable"):
                    raise HTTPException(status_code=400, detail=f"Invalid status '{status}' for check {item.check_type}")
                if status == "not_applicable" and not (item.exception_reason or "").strip():
                    raise HTTPException(
                        status_code=400,
                        detail=f"Check {item.check_type}: exception_reason is required when status is not_applicable",
                    )
                # N/A checks are stored as enabled=false (so legacy queries exclude them)
                # but status='not_applicable' distinguishes them from simple disables
                effective_enabled = item.enabled if status != "not_applicable" else False
                exception_reason = (item.exception_reason or "").strip() if status == "not_applicable" else None
                await conn.execute("""
                    INSERT INTO site_drift_config (site_id, check_type, enabled, status, exception_reason, modified_by, modified_at)
                    VALUES ($1, $2, $3, $5, $6, $4, NOW())
                    ON CONFLICT (site_id, check_type)
                    DO UPDATE SET enabled = $3, status = $5, exception_reason = $6, modified_by = $4, modified_at = NOW()
                """, site_id, item.check_type, effective_enabled, user.get("username", "admin"), status, exception_reason)
    return {"status": "ok", "site_id": site_id, "updated": len(body.checks)}


# =============================================================================
# MAINTENANCE MODE
# =============================================================================

class MaintenanceRequest(BaseModel):
    duration_hours: float
    reason: str


@router.put("/sites/{site_id}/maintenance")
async def set_maintenance(
    site_id: str,
    body: MaintenanceRequest,
    user: dict = Depends(auth_module.require_auth),
):
    """Set a maintenance window for a site. Suppresses incident creation until expiry."""
    await check_site_access_pool(user, site_id)

    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=422, detail="reason is required")
    if body.duration_hours < 0.5 or body.duration_hours > 48:
        raise HTTPException(status_code=422, detail="duration_hours must be between 0.5 and 48")

    from .fleet import get_pool
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        await conn.execute("""
            UPDATE sites
            SET maintenance_until = NOW() + ($1 || ' hours')::INTERVAL,
                maintenance_reason = $2,
                maintenance_set_by = $3
            WHERE site_id = $4
        """, str(body.duration_hours), body.reason.strip(), user.get("username", "admin"), site_id)

    logger.info("Maintenance window set",
                site_id=site_id,
                duration_hours=body.duration_hours,
                set_by=user.get("username", "admin"))

    return {"status": "ok", "site_id": site_id, "duration_hours": body.duration_hours}


@router.delete("/sites/{site_id}/maintenance")
async def cancel_maintenance(
    site_id: str,
    user: dict = Depends(auth_module.require_auth),
):
    """Cancel an active maintenance window for a site."""
    await check_site_access_pool(user, site_id)

    from .fleet import get_pool
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        await conn.execute("""
            UPDATE sites
            SET maintenance_until = NULL,
                maintenance_reason = NULL,
                maintenance_set_by = NULL
            WHERE site_id = $1
        """, site_id)

    logger.info("Maintenance window cancelled", site_id=site_id, cancelled_by=user.get("username", "admin"))

    return {"status": "ok", "site_id": site_id, "maintenance_until": None}


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
            SELECT check_type, enabled, notes,
                   COALESCE(status, CASE WHEN enabled THEN 'enabled' ELSE 'disabled' END) as status,
                   exception_reason
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


# =============================================================================
# CREDENTIAL HEALTH
# =============================================================================

@router.get("/sites/{site_id}/credential-health")
async def get_credential_health(
    site_id: str,
    user: dict = Depends(auth_module.require_auth),
):
    """Get credential health for a site based on execution telemetry.

    For each hostname the appliance has targeted, returns:
    - recent_failures: failures in the last 24h
    - consecutive_failures: consecutive failures (unbroken by a success)
    - last_success: timestamp of last successful execution
    - last_failure: timestamp of last failed execution
    - status: 'healthy', 'degraded' (1-2 consecutive fails), or 'stale' (3+)
    """
    await check_site_access_pool(user, site_id)
    from .fleet import get_pool
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Per-hostname telemetry health for this site (last 7 days of activity)
        rows = await conn.fetch("""
            SELECT
                hostname,
                COUNT(*) FILTER (
                    WHERE success = false AND created_at > NOW() - INTERVAL '24 hours'
                ) AS recent_failures_24h,
                COUNT(*) FILTER (
                    WHERE success = true AND created_at > NOW() - INTERVAL '24 hours'
                ) AS recent_successes_24h,
                MAX(created_at) FILTER (WHERE success = true) AS last_success,
                MAX(created_at) FILTER (WHERE success = false) AS last_failure,
                COUNT(*) AS total_executions
            FROM execution_telemetry
            WHERE site_id = $1
              AND hostname IS NOT NULL
              AND hostname != ''
              AND created_at > NOW() - INTERVAL '7 days'
            GROUP BY hostname
            ORDER BY hostname
        """, site_id)

        # For each host, compute consecutive failures (most recent unbroken streak)
        hosts = []
        for row in rows:
            hostname = row["hostname"]
            # Get the last N executions in order to count consecutive failures
            recent = await conn.fetch("""
                SELECT success, created_at
                FROM execution_telemetry
                WHERE site_id = $1 AND hostname = $2
                ORDER BY created_at DESC
                LIMIT 20
            """, site_id, hostname)

            consecutive_failures = 0
            for r in recent:
                if not r["success"]:
                    consecutive_failures += 1
                else:
                    break

            if consecutive_failures >= 3:
                status = "stale"
            elif consecutive_failures >= 1:
                status = "degraded"
            else:
                status = "healthy"

            hosts.append({
                "hostname": hostname,
                "recent_failures_24h": row["recent_failures_24h"],
                "recent_successes_24h": row["recent_successes_24h"],
                "last_success": row["last_success"].isoformat() if row["last_success"] else None,
                "last_failure": row["last_failure"].isoformat() if row["last_failure"] else None,
                "consecutive_failures": consecutive_failures,
                "total_executions_7d": row["total_executions"],
                "status": status,
            })

    # Summary counts
    stale_count = sum(1 for h in hosts if h["status"] == "stale")
    degraded_count = sum(1 for h in hosts if h["status"] == "degraded")
    healthy_count = sum(1 for h in hosts if h["status"] == "healthy")

    return {
        "site_id": site_id,
        "hosts": hosts,
        "summary": {
            "total_hosts": len(hosts),
            "healthy": healthy_count,
            "degraded": degraded_count,
            "stale": stale_count,
        },
    }


# =============================================================================
# TARGET HEALTH — connectivity probe status per target
# =============================================================================

@router.get("/sites/{site_id}/target-health")
async def get_target_health(
    site_id: str,
    user: dict = Depends(auth_module.require_auth),
):
    """Get target connectivity health for a site.

    Returns per-target probe results reported by the appliance daemon's
    probeTargetConnectivity function. Each target has status per protocol
    (SSH, WinRM, SNMP) with error details and latency.
    """
    await check_site_access_pool(user, site_id)
    from .fleet import get_pool
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        rows = await conn.fetch("""
            SELECT hostname, protocol, port, status, error,
                   latency_ms, reported_by, last_reported_at
            FROM target_health
            WHERE site_id = $1
            ORDER BY hostname, protocol
        """, site_id)

        # Group by hostname for a cleaner response
        hosts: dict = {}
        for row in rows:
            hostname = row["hostname"]
            if hostname not in hosts:
                hosts[hostname] = {
                    "hostname": hostname,
                    "protocols": [],
                    "overall_status": "ok",
                }
            proto_entry = {
                "protocol": row["protocol"],
                "port": row["port"],
                "status": row["status"],
                "error": row["error"],
                "latency_ms": row["latency_ms"],
                "reported_by": row["reported_by"],
                "last_reported_at": row["last_reported_at"].isoformat() if row["last_reported_at"] else None,
            }
            hosts[hostname]["protocols"].append(proto_entry)

            # Worst-status wins for overall
            if row["status"] != "ok":
                hosts[hostname]["overall_status"] = "unhealthy"

        host_list = list(hosts.values())

        # Summary
        ok_count = sum(1 for h in host_list if h["overall_status"] == "ok")
        unhealthy_count = len(host_list) - ok_count

    return {
        "site_id": site_id,
        "targets": host_list,
        "summary": {
            "total_targets": len(host_list),
            "healthy": ok_count,
            "unhealthy": unhealthy_count,
        },
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
            "SELECT site_id, clinic_name, status, wg_ip FROM sites WHERE site_id = $1",
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

        # 5b. Remove WireGuard peer config if this site had a VPN IP
        wg_ip = site.get("wg_ip") if site else None
        if wg_ip:
            try:
                import os
                from .provisioning import WG_PEER_DIR
                peer_file = os.path.join(WG_PEER_DIR, f"{site_id}.conf")
                if os.path.exists(peer_file):
                    os.remove(peer_file)
                    # Touch reload flag for systemd path unit to trigger wg syncconf
                    import time as _time
                    reload_flag = os.path.join(WG_PEER_DIR, ".reload")
                    with open(reload_flag, 'w') as f:
                        f.write(str(_time.time()))
                    actions_taken.append("WireGuard peer config removed")
                    logger.info(f"WireGuard peer removed for decommissioned site {site_id}")
            except Exception as e:
                logger.warning(f"Failed to remove WireGuard peer for {site_id}: {e}")

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


# =============================================================================
# =============================================================================
# SITE PROVISIONING (Admin)
# =============================================================================


class ProvisionRequest(BaseModel):
    mac_address: Optional[str] = None  # If provided, pre-register this MAC for this site
    client_email: Optional[str] = None  # Client contact email for alerts + onboarding


@router.post("/sites/{site_id}/provision")
async def create_site_provision(
    site_id: str,
    body: ProvisionRequest = ProvisionRequest(),
    user: dict = Depends(auth_module.require_operator),
    db: AsyncSession = Depends(get_db),
):
    """Register a MAC address for auto-provisioning to this site.

    When the appliance boots and calls /api/provision/{MAC}, the server
    returns the site config + API key. The appliance auto-configures and
    begins checking in. Zero manual config.yaml needed.

    If mac_address is provided: pre-register that specific MAC.
    If omitted: return info for manual registration.
    """
    import hashlib

    # Verify site exists
    site = await execute_with_retry(db, text(
        "SELECT site_id, clinic_name, partner_id FROM sites WHERE site_id = :sid"
    ), {"sid": site_id})
    site_row = site.fetchone()
    if not site_row:
        raise HTTPException(status_code=404, detail="Site not found")

    # Store client email on site if provided
    if body.client_email:
        await execute_with_retry(db, text(
            "UPDATE sites SET client_contact_email = :email WHERE site_id = :sid"
        ), {"email": body.client_email.strip(), "sid": site_id})

    # Generate API key for the new appliance
    raw_key = secrets.token_urlsafe(32)

    if body.mac_address:
        mac = body.mac_address.upper().strip()
        # Insert into appliance_provisioning — the MAC lookup endpoint reads this
        await execute_with_retry(db, text("""
            INSERT INTO appliance_provisioning (mac_address, site_id, api_key, notes, registered_at)
            VALUES (:mac, :sid, :key, :notes, NOW())
            ON CONFLICT (mac_address) DO UPDATE SET
                site_id = EXCLUDED.site_id,
                api_key = EXCLUDED.api_key,
                notes = EXCLUDED.notes,
                registered_at = NOW()
        """), {
            "mac": mac,
            "sid": site_id,
            "key": raw_key,
            "notes": f"Admin-provisioned by {user.get('username', 'unknown')}",
        })
        # Audit log
        await execute_with_retry(db, text("""
            INSERT INTO admin_audit_log (username, action, target, details, created_at)
            VALUES (:user, 'APPLIANCE_PROVISIONED', :mac, :details::jsonb, NOW())
        """), {
            "user": user.get("username", "admin"),
            "mac": mac,
            "details": json.dumps({"site_id": site_id, "clinic_name": site_row.clinic_name}),
        })

        await db.commit()

        return {
            "status": "mac_registered",
            "site_id": site_id,
            "mac_address": mac,
            "message": f"MAC {mac} registered for {site_row.clinic_name or site_id}. Boot the appliance — it will auto-configure.",
        }

    # No MAC provided — return instructions
    return {
        "status": "ready",
        "site_id": site_id,
        "message": "Enter the appliance MAC address to pre-register it for this site. The MAC is printed on the device label or shown on the BIOS POST screen.",
    }


@router.get("/provisions")
async def list_all_provisions(
    status_filter: Optional[str] = None,
    site_id: Optional[str] = None,
    limit: int = 100,
    user: dict = Depends(auth_module.require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Admin: list ALL appliance provisions across all sites with filters.

    Powers the admin Provisions page. Every row can be edited (email/notes),
    toggled active/inactive (via status), or deleted. Status values:
      - pending: MAC registered, appliance has NOT called home yet
      - claimed: MAC registered, appliance has provisioned (provisioned_at set)
      - stale:   pending but registered_at > 30 days ago, no claim
    """
    limit = max(1, min(500, int(limit)))

    where = []
    params: Dict[str, Any] = {"lim": limit}
    if site_id:
        where.append("ap.site_id = :sid")
        params["sid"] = site_id

    sql = """
        SELECT
            ap.mac_address,
            ap.site_id,
            s.clinic_name,
            s.client_contact_email,
            ap.registered_at,
            ap.provisioned_at,
            ap.notes,
            CASE
              WHEN ap.provisioned_at IS NOT NULL THEN 'claimed'
              WHEN ap.registered_at < NOW() - INTERVAL '30 days' THEN 'stale'
              ELSE 'pending'
            END AS status
        FROM appliance_provisioning ap
        LEFT JOIN sites s ON s.site_id = ap.site_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ap.registered_at DESC LIMIT :lim"

    result = await execute_with_retry(db, text(sql), params)
    rows = [dict(r._mapping) for r in result.fetchall()]

    # Apply status filter in Python (CASE expression in WHERE is awkward)
    if status_filter:
        rows = [r for r in rows if r["status"] == status_filter]

    # Format for JSON
    for r in rows:
        for k in ("registered_at", "provisioned_at"):
            if r.get(k):
                r[k] = r[k].isoformat()

    # Summary counts
    counts_result = await execute_with_retry(db, text("""
        SELECT
          COUNT(*) FILTER (WHERE provisioned_at IS NOT NULL) as claimed,
          COUNT(*) FILTER (WHERE provisioned_at IS NULL AND registered_at >= NOW() - INTERVAL '30 days') as pending,
          COUNT(*) FILTER (WHERE provisioned_at IS NULL AND registered_at < NOW() - INTERVAL '30 days') as stale,
          COUNT(*) as total
        FROM appliance_provisioning
    """))
    summary = dict(counts_result.fetchone()._mapping)

    return {
        "provisions": rows,
        "summary": summary,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


class ProvisionUpdate(BaseModel):
    notes: Optional[str] = None
    site_id: Optional[str] = None  # move to a different site
    client_email: Optional[str] = None  # updates sites.client_contact_email


@router.patch("/provisions/{mac_address}")
async def update_provision(
    mac_address: str,
    body: ProvisionUpdate,
    user: dict = Depends(auth_module.require_operator),
    db: AsyncSession = Depends(get_db),
):
    """Edit a provision: update notes, move to different site, change client email.

    Common use case: typo in MAC when creating (delete + recreate), or updating
    the contact email after client assigns a different IT person.
    """
    mac_norm = mac_address.upper().strip()

    existing = await execute_with_retry(
        db, text("SELECT site_id FROM appliance_provisioning WHERE mac_address = :mac"),
        {"mac": mac_norm},
    )
    row = existing.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Provision not found")

    updates: list[str] = []
    params: Dict[str, Any] = {"mac": mac_norm}

    if body.notes is not None:
        updates.append("notes = :notes")
        params["notes"] = body.notes
    if body.site_id is not None:
        # Verify target site exists
        site_check = await execute_with_retry(
            db, text("SELECT 1 FROM sites WHERE site_id = :sid"), {"sid": body.site_id},
        )
        if not site_check.fetchone():
            raise HTTPException(status_code=400, detail=f"Target site {body.site_id} does not exist")
        updates.append("site_id = :new_sid")
        params["new_sid"] = body.site_id

    if updates:
        sql = f"UPDATE appliance_provisioning SET {', '.join(updates)} WHERE mac_address = :mac"
        await execute_with_retry(db, text(sql), params)

    if body.client_email is not None:
        # client_email lives on sites, not on appliance_provisioning
        target_site = body.site_id or row.site_id
        if target_site:
            await execute_with_retry(db, text(
                "UPDATE sites SET client_contact_email = :email WHERE site_id = :sid"
            ), {"email": body.client_email.strip(), "sid": target_site})

    # Audit log
    await execute_with_retry(db, text("""
        INSERT INTO admin_audit_log (username, action, target, details, created_at)
        VALUES (:user, 'PROVISION_UPDATED', :mac, :details::jsonb, NOW())
    """), {
        "user": user.get("username", "admin"),
        "mac": mac_norm,
        "details": json.dumps({
            "notes": body.notes,
            "new_site_id": body.site_id,
            "client_email_updated": body.client_email is not None,
        }),
    })
    await db.commit()
    return {"status": "updated", "mac_address": mac_norm}


@router.delete("/provisions/{mac_address}")
async def delete_provision(
    mac_address: str,
    user: dict = Depends(auth_module.require_operator),
    db: AsyncSession = Depends(get_db),
):
    """Delete a provision. If the appliance has already claimed (provisioned_at set),
    this only removes the pre-registration row — the running appliance continues.
    Use when cleaning up typos or stale pre-registrations.
    """
    mac_norm = mac_address.upper().strip()

    existing = await execute_with_retry(
        db, text("SELECT site_id, provisioned_at FROM appliance_provisioning WHERE mac_address = :mac"),
        {"mac": mac_norm},
    )
    row = existing.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Provision not found")

    await execute_with_retry(db, text(
        "DELETE FROM appliance_provisioning WHERE mac_address = :mac"
    ), {"mac": mac_norm})

    # Audit log
    await execute_with_retry(db, text("""
        INSERT INTO admin_audit_log (username, action, target, details, created_at)
        VALUES (:user, 'PROVISION_DELETED', :mac, :details::jsonb, NOW())
    """), {
        "user": user.get("username", "admin"),
        "mac": mac_norm,
        "details": json.dumps({
            "site_id": row.site_id,
            "was_claimed": row.provisioned_at is not None,
        }),
    })
    await db.commit()
    return {"status": "deleted", "mac_address": mac_norm}


@router.get("/sites/{site_id}/provisions")
async def list_site_provisions(
    site_id: str,
    user: dict = Depends(auth_module.require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List all provisioned/pending appliances for a site."""
    result = await execute_with_retry(db, text("""
        SELECT mac_address, site_id, registered_at, provisioned_at, notes
        FROM appliance_provisioning
        WHERE site_id = :sid
        ORDER BY registered_at DESC
    """), {"sid": site_id})
    rows = result.fetchall()

    return {
        "provisions": [
            {
                "mac_address": r.mac_address,
                "registered_at": r.registered_at.isoformat() if r.registered_at else None,
                "provisioned_at": r.provisioned_at.isoformat() if r.provisioned_at else None,
                "status": "provisioned" if r.provisioned_at else "waiting",
                "notes": r.notes,
            }
            for r in rows
        ],
        "count": len(rows),
    }


@router.get("/provisions/unclaimed")
async def list_unclaimed_appliances(
    user: dict = Depends(auth_module.require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List all unclaimed appliances that have called home but aren't assigned to a site.

    Self-registration flow: appliances boot, call /api/provision/{MAC},
    get auto-registered as unclaimed. Admin sees them here and claims.
    """
    result = await execute_with_retry(db, text("""
        SELECT mac_address, registered_at, notes
        FROM appliance_provisioning
        WHERE site_id IS NULL
        ORDER BY registered_at DESC
    """))
    rows = result.fetchall()

    return {
        "unclaimed": [
            {
                "mac_address": r.mac_address,
                "registered_at": r.registered_at.isoformat() if r.registered_at else None,
                "notes": r.notes,
            }
            for r in rows
        ],
        "count": len(rows),
    }


class ClaimApplianceRequest(BaseModel):
    mac_address: str
    site_id: str


@router.post("/provisions/claim")
async def claim_unclaimed_appliance(
    body: ClaimApplianceRequest,
    user: dict = Depends(auth_module.require_operator),
    db: AsyncSession = Depends(get_db),
):
    """Claim an unclaimed appliance and assign it to a site.

    The appliance already called home and is polling for config.
    Once claimed, the next poll returns the full config and the
    appliance auto-provisions.
    """
    import hashlib

    mac = body.mac_address.upper().strip()

    # Verify the MAC exists and is unclaimed
    existing = await execute_with_retry(db, text(
        "SELECT id, site_id FROM appliance_provisioning WHERE UPPER(mac_address) = :mac"
    ), {"mac": mac})
    row = existing.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="MAC not found in unclaimed list")
    if row.site_id:
        raise HTTPException(status_code=400, detail=f"MAC already claimed by site {row.site_id}")

    # Verify site exists
    site = await execute_with_retry(db, text(
        "SELECT site_id, clinic_name FROM sites WHERE site_id = :sid"
    ), {"sid": body.site_id})
    site_row = site.fetchone()
    if not site_row:
        raise HTTPException(status_code=404, detail="Site not found")

    # Generate API key and claim
    raw_key = secrets.token_urlsafe(32)
    await execute_with_retry(db, text("""
        UPDATE appliance_provisioning
        SET site_id = :sid, api_key = :key,
            notes = COALESCE(notes, '') || ' | Claimed by ' || :user || ' at ' || NOW()::text
        WHERE UPPER(mac_address) = :mac
    """), {"sid": body.site_id, "key": raw_key, "mac": mac, "user": user.get("username", "admin")})

    # Audit log
    await execute_with_retry(db, text("""
        INSERT INTO admin_audit_log (username, action, target, details, created_at)
        VALUES (:user, 'APPLIANCE_CLAIMED', :mac, :details::jsonb, NOW())
    """), {
        "user": user.get("username", "admin"),
        "mac": mac,
        "details": json.dumps({"site_id": body.site_id, "clinic_name": site_row.clinic_name}),
    })

    await db.commit()

    return {
        "status": "claimed",
        "mac_address": mac,
        "site_id": body.site_id,
        "clinic_name": site_row.clinic_name,
        "message": f"Appliance {mac} assigned to {site_row.clinic_name}. It will auto-configure on next poll.",
    }


@router.post("/sites/{site_id}/deployment-pack")
async def generate_deployment_pack(
    site_id: str,
    user: dict = Depends(auth_module.require_operator),
    db: AsyncSession = Depends(get_db),
):
    """Generate a config.yaml for offline USB provisioning.

    Download this file, put it on the installer USB at /osiriscare/config.yaml.
    The appliance reads it during install — no internet needed for provisioning.
    Includes site_id, API key, and admin SSH key.
    """
    import hashlib
    import yaml

    # Verify site exists
    site = await execute_with_retry(db, text(
        "SELECT site_id, clinic_name, partner_id FROM sites WHERE site_id = :sid"
    ), {"sid": site_id})
    site_row = site.fetchone()
    if not site_row:
        raise HTTPException(status_code=404, detail="Site not found")

    # Generate API key
    raw_key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    await execute_with_retry(db, text("""
        INSERT INTO api_keys (key_hash, site_id, active, created_at, description)
        VALUES (:hash, :sid, true, NOW(), 'Deployment pack key')
        ON CONFLICT (key_hash) DO NOTHING
    """), {"hash": key_hash, "sid": site_id})

    # Get admin SSH keys for the site
    ssh_keys = []
    try:
        ssh_result = await execute_with_retry(db, text(
            "SELECT ssh_authorized_keys FROM sites WHERE site_id = :sid"
        ), {"sid": site_id})
        ssh_row = ssh_result.fetchone()
        if ssh_row and ssh_row.ssh_authorized_keys:
            ssh_keys = list(ssh_row.ssh_authorized_keys)
    except Exception:
        pass

    config = {
        "site_id": site_row.site_id,
        "api_key": raw_key,
        "api_endpoint": "https://api.osiriscare.net",
        "ssh_authorized_keys": ssh_keys,
    }

    # Audit log
    await execute_with_retry(db, text("""
        INSERT INTO admin_audit_log (username, action, target, details, created_at)
        VALUES (:user, 'DEPLOYMENT_PACK_GENERATED', :sid, :details::jsonb, NOW())
    """), {
        "user": user.get("username", "admin"),
        "sid": site_id,
        "details": json.dumps({"clinic_name": site_row.clinic_name}),
    })

    await db.commit()

    # Return as YAML (the appliance reads config.yaml)
    try:
        import yaml
        config_yaml = yaml.dump(config, default_flow_style=False)
    except ImportError:
        config_yaml = json.dumps(config, indent=2)

    return {
        "config_yaml": config_yaml,
        "config": config,
        "instructions": (
            "Save as 'config.yaml' on the installer USB at one of these paths:\n"
            "  /config.yaml\n"
            "  /msp/config.yaml\n"
            "  /osiriscare/config.yaml\n\n"
            "The appliance reads this during boot — no internet needed for provisioning."
        ),
    }


# VPN MANAGEMENT ENDPOINTS
# =============================================================================


async def _get_hub_peer_status() -> dict:
    """Query WireGuard hub for real-time peer status.

    Reads a JSON status file written by a cron job on the VPS host.
    The mcp-server container has /opt/mcp-server/wireguard mounted.
    """
    import os
    peers = {}

    try:
        status_file = "/opt/mcp-server/wireguard/status.json"
        if os.path.exists(status_file):
            with open(status_file) as f:
                data = json.load(f)
            for peer in data.get("peers", []):
                ip = peer.get("allowed_ips", "").replace("/32", "")
                hs = peer.get("last_handshake")
                peers[ip] = {
                    "last_handshake": datetime.fromisoformat(hs) if hs else None,
                    "rx": peer.get("rx", 0),
                    "tx": peer.get("tx", 0),
                    "endpoint": peer.get("endpoint"),
                }
    except Exception as e:
        logger.warning(f"Failed to read WireGuard hub status: {e}")

    return peers


@router.get("/vpn/status")
async def get_vpn_status(user: dict = Depends(auth_module.require_auth)):
    """Get WireGuard VPN fleet status with real-time handshake data."""
    from .fleet import get_pool

    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Get all sites with WireGuard configured.
        # Use DISTINCT ON to collapse multiple appliances per site into one row
        # (picking the most-recently-checked-in appliance for version/checkin data),
        # and include an appliance_count for display.
        sites = await conn.fetch("""
            SELECT DISTINCT ON (s.site_id)
                   s.site_id, s.clinic_name, s.status, s.wg_ip, s.wg_pubkey,
                   s.wg_connected_at, sa.last_checkin, sa.agent_version,
                   (SELECT COUNT(*) FROM site_appliances
                    WHERE site_id = s.site_id) AS appliance_count
            FROM sites s
            LEFT JOIN site_appliances sa ON sa.site_id = s.site_id
            WHERE s.wg_ip IS NOT NULL
            ORDER BY s.site_id, sa.last_checkin DESC NULLS LAST
        """)

        # Get real-time handshake data from hub
        wg_peers = await _get_hub_peer_status()

        result = []
        for site in sites:
            wg_ip = site["wg_ip"]
            peer_data = wg_peers.get(wg_ip, {})

            # Determine connection status from latest handshake
            last_handshake = peer_data.get("last_handshake")
            connected = False
            if last_handshake:
                connected = (datetime.now(timezone.utc) - last_handshake).total_seconds() < 300

            result.append({
                "site_id": site["site_id"],
                "clinic_name": site["clinic_name"],
                "site_status": site["status"],
                "wg_ip": wg_ip,
                "wg_pubkey": site["wg_pubkey"][:12] + "..." if site["wg_pubkey"] else None,
                "connected": connected,
                "last_handshake": last_handshake.isoformat() if last_handshake else None,
                "bytes_received": peer_data.get("rx", 0),
                "bytes_sent": peer_data.get("tx", 0),
                "endpoint": peer_data.get("endpoint"),
                "last_checkin": site["last_checkin"].isoformat() if site["last_checkin"] else None,
                "agent_version": site["agent_version"],
                "appliance_count": site["appliance_count"],
            })

        # Sort by clinic name for consistent display order
        result.sort(key=lambda r: r["clinic_name"])

        # Count stats
        total = len(result)
        connected_count = sum(1 for r in result if r["connected"])

        return {
            "peers": result,
            "total": total,
            "connected": connected_count,
            "disconnected": total - connected_count,
        }


@router.post("/vpn/{site_id}/rotate-key")
async def rotate_vpn_key(site_id: str, user: dict = Depends(auth_module.require_auth)):
    """Create a fleet order to rotate the WireGuard key on an appliance."""
    import uuid
    from .fleet import get_pool

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        site = await conn.fetchrow(
            "SELECT wg_ip FROM sites WHERE site_id = $1", site_id
        )
        if not site or not site["wg_ip"]:
            raise HTTPException(status_code=400, detail="Site has no WireGuard configuration")

        order_id = str(uuid.uuid4())
        await conn.execute("""
            INSERT INTO admin_orders (order_id, order_type, parameters, status, site_id, expires_at)
            VALUES ($1, $2, '{}'::jsonb, $3, $4, NOW() + INTERVAL '24 hours')
        """, order_id, "rotate_wg_key", "active", site_id)

        # Audit trail
        await conn.execute("""
            INSERT INTO audit_log (event_type, actor, resource_type, resource_id, details)
            VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
            "vpn.key_rotation_ordered",
            user.get("username", "unknown"),
            "site",
            site_id,
            json.dumps({"order_id": order_id}),
        )

    return {"status": "ordered", "order_id": order_id, "site_id": site_id}


# =============================================================================
# SOC 2 / MULTI-FRAMEWORK COMPLIANCE REPORT EXPORT
# =============================================================================


@router.get("/sites/{site_id}/compliance-report")
async def get_compliance_report(
    site_id: str,
    framework: str = Query(default="hipaa", description="Framework to report on"),
    user: dict = Depends(auth_module.require_auth),
):
    """Generate a structured compliance report for a given framework.

    Fetches all framework_controls for the requested framework, maps each
    control to the most recent compliance_bundles via check_control_mappings,
    counts evidence_bundles, and returns an auditor-ready JSON report.
    """
    await check_site_access_pool(user, site_id)
    from .fleet import get_pool
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Verify site exists
        site = await conn.fetchrow(
            "SELECT site_id, clinic_name FROM sites WHERE site_id = $1", site_id
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # 1. Get all controls for this framework with their mapped check_ids
        control_rows = await conn.fetch("""
            SELECT fc.control_id, fc.control_name, fc.description, fc.category,
                   ARRAY_AGG(ccm.check_id) FILTER (WHERE ccm.check_id IS NOT NULL) AS check_ids
            FROM framework_controls fc
            LEFT JOIN check_control_mappings ccm
                ON ccm.framework = fc.framework AND ccm.control_id = fc.control_id
            WHERE fc.framework = $1
            GROUP BY fc.control_id, fc.control_name, fc.description, fc.category
            ORDER BY fc.category, fc.control_id
        """, framework)

        if not control_rows:
            raise HTTPException(
                status_code=404,
                detail=f"No controls found for framework '{framework}'",
            )

        # 2. Get the latest compliance bundle result per check_type for this site
        bundle_rows = await conn.fetch("""
            SELECT DISTINCT ON (check_type)
                   check_type, check_result, checked_at
            FROM compliance_bundles
            WHERE site_id = $1
              AND checked_at > NOW() - INTERVAL '30 days'
            ORDER BY check_type, checked_at DESC
        """, site_id)
        bundle_map = {
            r["check_type"]: {
                "result": r["check_result"],
                "checked_at": r["checked_at"],
            }
            for r in bundle_rows
        }

        # 3. Count evidence bundles for this site
        evidence_count_row = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM evidence_bundles WHERE site_id = $1", site_id
        )
        total_evidence = evidence_count_row["cnt"] if evidence_count_row else 0

        # 4. Build per-control report
        controls = []
        passing = 0
        failing = 0
        not_tested = 0

        for row in control_rows:
            check_ids = row["check_ids"] or []
            check_results = []
            control_status = "not_tested"
            latest_check = None
            evidence_for_control = 0

            for check_id in check_ids:
                bundle = bundle_map.get(check_id)
                if bundle:
                    result = bundle["result"] or "unknown"
                    checked_at = bundle["checked_at"]
                    check_results.append({
                        "check_type": check_id,
                        "result": result,
                        "checked_at": checked_at.isoformat() if checked_at else None,
                    })
                    if latest_check is None or (checked_at and checked_at > latest_check):
                        latest_check = checked_at

            # Determine control status from its check results
            if check_results:
                has_fail = any(r["result"] in ("fail", "critical") for r in check_results)
                has_pass = any(r["result"] in ("pass", "compliant") for r in check_results)
                if has_fail:
                    control_status = "fail"
                elif has_pass:
                    control_status = "pass"

            # Count evidence bundles that match any of this control's check_ids
            if check_ids:
                ev_row = await conn.fetchrow("""
                    SELECT COUNT(*) AS cnt FROM evidence_bundles
                    WHERE site_id = $1 AND manifest::text LIKE ANY($2)
                """, site_id, [f"%{cid}%" for cid in check_ids])
                evidence_for_control = ev_row["cnt"] if ev_row else 0

            if control_status == "pass":
                passing += 1
            elif control_status == "fail":
                failing += 1
            else:
                not_tested += 1

            controls.append({
                "control_id": row["control_id"],
                "description": (row["control_name"] or row["description"] or ""),
                "category": row["category"] or "",
                "status": control_status,
                "evidence_count": evidence_for_control,
                "latest_check": latest_check.isoformat() if latest_check else None,
                "check_results": check_results,
            })

        total_controls = len(controls)
        overall_score = round(
            (passing + 0.5 * not_tested) / total_controls * 100, 1
        ) if total_controls > 0 else 0.0

    return {
        "framework": framework,
        "site_id": site_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_score": overall_score,
        "controls": controls,
        "summary": {
            "total_controls": total_controls,
            "passing": passing,
            "failing": failing,
            "not_tested": not_tested,
        },
    }


# =============================================================================
# INDUSTRY PRESET CONFIGURATION
# =============================================================================

INDUSTRY_PRESETS = {
    "healthcare": {"primary": "hipaa", "additional": ["nist_csf", "cis"]},
    "financial": {"primary": "soc2", "additional": ["pci_dss", "glba", "nist_csf"]},
    "legal": {"primary": "soc2", "additional": ["gdpr", "nist_csf"]},
    "government": {"primary": "cmmc", "additional": ["nist_800_53", "nist_800_171"]},
    "general": {"primary": "nist_csf", "additional": ["cis"]},
}


class IndustryPresetRequest(BaseModel):
    industry: str


@router.post("/sites/{site_id}/apply-industry-preset")
async def apply_industry_preset(
    site_id: str,
    body: IndustryPresetRequest,
    user: dict = Depends(auth_module.require_auth),
):
    """Apply an industry preset to configure compliance frameworks for a site.

    Looks up the preset for the given industry, then upserts
    appliance_framework_configs for every appliance at the site so that
    the correct frameworks are enabled and the primary framework is set.
    """
    await check_site_access_pool(user, site_id)

    preset = INDUSTRY_PRESETS.get(body.industry)
    if not preset:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown industry '{body.industry}'. "
                   f"Valid options: {', '.join(sorted(INDUSTRY_PRESETS.keys()))}",
        )

    from .fleet import get_pool
    pool = await get_pool()

    primary = preset["primary"]
    enabled = [primary] + preset["additional"]

    async with admin_connection(pool) as conn:
        # Verify site exists
        site = await conn.fetchrow(
            "SELECT site_id FROM sites WHERE site_id = $1", site_id
        )
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get all appliances for this site
        appliances = await conn.fetch(
            "SELECT appliance_id FROM site_appliances WHERE site_id = $1", site_id
        )

        updated_count = 0
        for appliance in appliances:
            await conn.execute("""
                INSERT INTO appliance_framework_configs (
                    appliance_id, site_id, enabled_frameworks,
                    primary_framework, industry, framework_metadata,
                    created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, '{}'::jsonb, NOW(), NOW())
                ON CONFLICT (appliance_id) DO UPDATE SET
                    enabled_frameworks = EXCLUDED.enabled_frameworks,
                    primary_framework = EXCLUDED.primary_framework,
                    industry = EXCLUDED.industry,
                    updated_at = NOW()
            """, appliance["appliance_id"], site_id, enabled, primary, body.industry)
            updated_count += 1

        # If no appliances exist yet, create a site-level config placeholder
        if updated_count == 0:
            await conn.execute("""
                INSERT INTO appliance_framework_configs (
                    appliance_id, site_id, enabled_frameworks,
                    primary_framework, industry, framework_metadata,
                    created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, '{}'::jsonb, NOW(), NOW())
                ON CONFLICT (appliance_id) DO UPDATE SET
                    enabled_frameworks = EXCLUDED.enabled_frameworks,
                    primary_framework = EXCLUDED.primary_framework,
                    industry = EXCLUDED.industry,
                    updated_at = NOW()
            """, f"__site__{site_id}", site_id, enabled, primary, body.industry)
            updated_count = 1

        # Audit trail
        await conn.execute("""
            INSERT INTO audit_log (event_type, actor, resource_type, resource_id, details)
            VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
            "industry_preset.applied",
            user.get("username", "unknown"),
            "site",
            site_id,
            json.dumps({
                "industry": body.industry,
                "primary_framework": primary,
                "enabled_frameworks": enabled,
                "appliances_updated": updated_count,
            }),
        )

    return {
        "status": "applied",
        "site_id": site_id,
        "industry": body.industry,
        "primary_framework": primary,
        "enabled_frameworks": enabled,
        "appliances_updated": updated_count,
    }


# =============================================================================
# ADMIN REPORTS
# =============================================================================

@router.get("/admin/reports/generate")
async def generate_admin_report(
    request: Request,
    site_id: str = Query(...),
    month: str = Query(None),
    format: str = Query("json"),
    user: dict = Depends(auth_module.require_auth),
):
    """Generate a compliance report for a site."""
    from .fleet import get_pool

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # Get site info
        site = await conn.fetchrow(
            "SELECT site_id, clinic_name FROM sites WHERE site_id = $1", site_id
        )
        if not site:
            raise HTTPException(404, "Site not found")

        # Determine target month
        target_month = month or datetime.now(timezone.utc).strftime("%Y-%m")
        parts = target_month.split("-")
        if len(parts) != 2:
            raise HTTPException(400, "month must be YYYY-MM format")
        from datetime import date as _date
        start = _date(int(parts[0]), int(parts[1]), 1)

        # Bundle stats
        bundle_stats = await conn.fetchrow("""
            SELECT COUNT(*) as total_bundles,
                   COUNT(CASE WHEN signature IS NOT NULL THEN 1 END) as signed_bundles,
                   COUNT(CASE WHEN ots_status = 'anchored' THEN 1 END) as anchored_bundles,
                   AVG(jsonb_array_length(checks)) as avg_checks
            FROM compliance_bundles
            WHERE site_id = $1 AND created_at >= $2::date
              AND created_at < ($2::date + interval '1 month')
        """, site_id, start)

        # Incident stats
        incident_stats = await conn.fetchrow("""
            SELECT COUNT(*) as total,
                   COUNT(CASE WHEN resolution_tier = 'L1' THEN 1 END) as l1_resolved,
                   COUNT(CASE WHEN resolution_tier = 'L2' THEN 1 END) as l2_resolved,
                   COUNT(CASE WHEN resolution_tier = 'L3' THEN 1 END) as l3_escalated,
                   COUNT(CASE WHEN status = 'resolved' THEN 1 END) as resolved
            FROM incidents
            WHERE site_id = $1 AND created_at >= $2::date
              AND created_at < ($2::date + interval '1 month')
        """, site_id, start)

        # Compliance score — computed from bundle checks
        score_row = await conn.fetchrow("""
            SELECT
                ROUND(
                    COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant'))::numeric /
                    NULLIF(COUNT(*) FILTER (WHERE c->>'status' IN ('pass', 'compliant', 'fail', 'non_compliant', 'warning'))::numeric, 0) * 100, 1
                ) as compliance_score
            FROM compliance_bundles cb,
                 jsonb_array_elements(cb.checks) as c
            WHERE cb.site_id = $1 AND cb.created_at >= $2::date
              AND cb.created_at < ($2::date + interval '1 month')
        """, site_id, start)

        # Category breakdown from recent bundles
        categories = await conn.fetch("""
            SELECT x.check_type,
                   COUNT(*) as checks,
                   SUM(CASE WHEN x.status IN ('pass', 'compliant') THEN 1 ELSE 0 END) as passed,
                   SUM(CASE WHEN x.status IN ('fail', 'non_compliant') THEN 1 ELSE 0 END) as failed
            FROM compliance_bundles cb,
                 jsonb_array_elements(cb.checks) AS c,
                 jsonb_to_record(c) AS x(check_type text, status text)
            WHERE cb.site_id = $1 AND cb.created_at >= $2::date
              AND cb.created_at < ($2::date + interval '1 month')
            GROUP BY x.check_type
            ORDER BY failed DESC
            LIMIT 20
        """, site_id, start)

        total_incidents = incident_stats["total"] or 0
        resolved_incidents = incident_stats["resolved"] or 0

        report = {
            "site_id": site_id,
            "clinic_name": site["clinic_name"],
            "period": target_month,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "compliance_score": score_row["compliance_score"] if score_row else None,
            "evidence": {
                "total_bundles": bundle_stats["total_bundles"],
                "signed": bundle_stats["signed_bundles"],
                "blockchain_anchored": bundle_stats["anchored_bundles"],
                "avg_checks_per_bundle": round(float(bundle_stats["avg_checks"] or 0), 1),
            },
            "incidents": {
                "total": total_incidents,
                "l1_auto_resolved": incident_stats["l1_resolved"],
                "l2_llm_resolved": incident_stats["l2_resolved"],
                "l3_escalated": incident_stats["l3_escalated"],
                "resolution_rate": round(
                    100 * resolved_incidents / max(total_incidents, 1), 1
                ),
            },
            "categories": [dict(c) for c in categories],
        }

        return report


# =============================================================================
# SYSTEM HEALTH
# =============================================================================

@router.get("/admin/system-health")
async def get_system_health(
    request: Request,
    user: dict = Depends(auth_module.require_auth),
):
    """Composite system health endpoint for admin dashboard."""
    from .fleet import get_pool

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # Database stats
        db_size = await conn.fetchval("SELECT pg_size_pretty(pg_database_size(current_database()))")
        conn_stats = await conn.fetch(
            "SELECT state, COUNT(*) as count FROM pg_stat_activity GROUP BY state"
        )
        table_sizes = await conn.fetch("""
            SELECT relname as table_name,
                   n_live_tup as row_count,
                   pg_size_pretty(pg_total_relation_size(relid)) as total_size
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            LIMIT 10
        """)

        # L2 API usage
        l2_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM l2_decisions WHERE created_at > NOW() - INTERVAL '24 hours'"
        )

        # Error count
        errors_1h = await conn.fetchval("""
            SELECT COUNT(*) FROM incidents
            WHERE created_at > NOW() - INTERVAL '1 hour' AND severity = 'critical'
        """)

        # Fleet status
        fleet = await conn.fetch("""
            SELECT sa.site_id, sa.agent_version,
                   s.clinic_name,
                   CASE WHEN sa.last_checkin > NOW() - INTERVAL '5 minutes' THEN 'online'
                        WHEN sa.last_checkin > NOW() - INTERVAL '15 minutes' THEN 'stale'
                        ELSE 'offline' END as status,
                   sa.last_checkin
            FROM site_appliances sa
            LEFT JOIN sites s ON s.site_id = sa.site_id
        """)

    # Background tasks
    bg_tasks = {}
    try:
        import main as _main_mod
        for name, task in getattr(_main_mod.app.state, 'bg_tasks', {}).items():
            if task.done():
                exc = task.exception() if not task.cancelled() else None
                bg_tasks[name] = {"status": f"crashed: {exc}" if exc else "completed"}
            else:
                bg_tasks[name] = {"status": "running"}
    except Exception:
        bg_tasks = {"note": {"status": "unable to inspect background tasks"}}

    overall = "healthy" if (errors_1h or 0) == 0 else "degraded"

    return {
        "status": overall,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "database": {
            "size": db_size,
            "connections": {r["state"] or "unknown": r["count"] for r in conn_stats},
            "top_tables": [dict(t) for t in table_sizes],
        },
        "l2_api": {"calls_24h": l2_24h or 0},
        "errors": {"critical_1h": errors_1h or 0},
        "fleet": [
            {
                "site_id": f["site_id"],
                "clinic_name": f["clinic_name"],
                "agent_version": f["agent_version"],
                "status": f["status"],
                "last_checkin": f["last_checkin"].isoformat() if f["last_checkin"] else None,
            }
            for f in fleet
        ],
        "background_tasks": bg_tasks,
    }


# =============================================================================
# AGENT HEALTH (fleet-wide Go agent status)
# =============================================================================

@router.get("/admin/agent-health")
async def get_agent_health(
    user: dict = Depends(auth_module.require_auth),
):
    """Fleet-wide Go agent health overview.

    Returns every registered Go agent with a derived status based on
    heartbeat freshness:
      - active:  heartbeat < 5 min ago
      - stale:   5-60 min
      - offline: > 60 min
      - never:   null heartbeat

    Also returns summary counts.
    """
    from .fleet import get_pool

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        rows = await conn.fetch("""
            SELECT g.agent_id, g.hostname, g.ip_address,
                   COALESCE(NULLIF(g.os_version, ''), g.os_name) AS os_version,
                   g.agent_version, g.status,
                   GREATEST(g.last_heartbeat, g.updated_at) AS last_heartbeat,
                   g.checks_passed, g.checks_total,
                   g.compliance_percentage, g.site_id,
                   s.clinic_name
            FROM go_agents g
            LEFT JOIN sites s ON s.site_id = g.site_id
            ORDER BY GREATEST(g.last_heartbeat, g.updated_at) DESC NULLS LAST
        """)

    now = datetime.now(timezone.utc)
    agents = []
    summary = {"active": 0, "stale": 0, "offline": 0, "never": 0}

    for row in rows:
        hb = row["last_heartbeat"]
        if hb is None:
            derived = "never"
        else:
            # Ensure timezone-aware comparison
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            age = now - hb
            if age < timedelta(minutes=5):
                derived = "active"
            elif age < timedelta(hours=1):
                derived = "stale"
            else:
                derived = "offline"

        summary[derived] += 1
        agents.append({
            "agent_id": row["agent_id"],
            "hostname": row["hostname"],
            "ip_address": row["ip_address"],
            "os_version": row["os_version"],
            "agent_version": row["agent_version"],
            "db_status": row["status"],
            "derived_status": derived,
            "last_heartbeat": row["last_heartbeat"].isoformat() if row["last_heartbeat"] else None,
            "checks_passed": row["checks_passed"] or 0,
            "checks_total": row["checks_total"] or 0,
            "compliance_percentage": float(row["compliance_percentage"] or 0),
            "site_id": row["site_id"],
            "clinic_name": row["clinic_name"],
        })

    return {
        "checked_at": now.isoformat(),
        "total_agents": len(agents),
        "summary": summary,
        "agents": agents,
    }


# =============================================================================
# EVIDENCE WITNESS STATUS
# =============================================================================

@router.get("/admin/evidence-witness")
async def get_evidence_witness_status(
    user: dict = Depends(auth_module.require_auth),
):
    """Evidence witness attestation stats for the admin dashboard."""
    from .fleet import get_pool

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # Total attestations
        total = await conn.fetchval("SELECT count(*) FROM witness_attestations")

        # Attestations by witness appliance
        by_witness = await conn.fetch("""
            SELECT witness_appliance, count(*) as cnt, max(created_at) as latest
            FROM witness_attestations
            GROUP BY witness_appliance
            ORDER BY cnt DESC
        """)

        # Recent attestation rate (last 24h)
        recent = await conn.fetchval(
            "SELECT count(*) FROM witness_attestations WHERE created_at > NOW() - interval '24h'"
        )

        # Bundles with vs without witnesses (last 24h)
        coverage = await conn.fetchrow("""
            SELECT
                count(DISTINCT cb.bundle_id) as total_bundles,
                count(DISTINCT wa.bundle_id) as witnessed_bundles
            FROM compliance_bundles cb
            LEFT JOIN witness_attestations wa ON wa.bundle_id = cb.bundle_id
            WHERE cb.checked_at > NOW() - interval '24h'
        """)

    total_b = coverage['total_bundles'] or 0
    witnessed_b = coverage['witnessed_bundles'] or 0

    return {
        "total_attestations": total,
        "attestations_24h": recent,
        "witness_coverage_24h": {
            "total_bundles": total_b,
            "witnessed_bundles": witnessed_b,
            "coverage_pct": round(witnessed_b / total_b * 100, 1) if total_b > 0 else 0,
        },
        "by_witness": [
            {
                "witness_appliance": r['witness_appliance'],
                "attestation_count": r['cnt'],
                "latest": r['latest'].isoformat() if r['latest'] else None,
            }
            for r in by_witness
        ],
    }


# =============================================================================
# HEALING TELEMETRY (execution_telemetry breakdown)
# =============================================================================

@router.get("/admin/healing-telemetry")
async def get_healing_telemetry(
    hours: int = Query(default=24, ge=1, le=168, description="Lookback window in hours"),
    user: dict = Depends(auth_module.require_auth),
):
    """Healing execution telemetry grouped by incident_type and outcome.

    Returns aggregated counts from execution_telemetry for the given
    lookback window (default 24h, max 7d). Powers error-breakdown and
    success-rate charts on the admin dashboard.
    """
    from .fleet import get_pool

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        rows = await conn.fetch("""
            SELECT incident_type, runbook_id, success,
                   COUNT(*) AS attempts,
                   MAX(created_at) AS latest
            FROM execution_telemetry
            WHERE created_at > NOW() - make_interval(hours => $1)
            GROUP BY incident_type, runbook_id, success
            ORDER BY attempts DESC
        """, hours)

        totals = await conn.fetchrow("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE success = true) AS succeeded,
                   COUNT(*) FILTER (WHERE success = false) AS failed
            FROM execution_telemetry
            WHERE created_at > NOW() - make_interval(hours => $1)
        """, hours)

        error_breakdown = await conn.fetch("""
            SELECT COALESCE(failure_type, 'unknown') AS failure_type,
                   COUNT(*) AS count
            FROM execution_telemetry
            WHERE created_at > NOW() - make_interval(hours => $1)
              AND success = false
            GROUP BY failure_type
            ORDER BY count DESC
        """, hours)

    entries = []
    for row in rows:
        entries.append({
            "incident_type": row["incident_type"],
            "runbook_id": row["runbook_id"],
            "success": row["success"],
            "attempts": row["attempts"],
            "latest": row["latest"].isoformat() if row["latest"] else None,
        })

    total = totals["total"] or 0
    succeeded = totals["succeeded"] or 0
    failed = totals["failed"] or 0

    return {
        "hours": hours,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "success_rate": round(100 * succeeded / max(total, 1), 1),
        },
        "error_breakdown": [
            {"failure_type": r["failure_type"], "count": r["count"]}
            for r in error_breakdown
        ],
        "entries": entries,
    }


# =============================================================================
# HEALING SLA OVERVIEW
# =============================================================================

@router.get("/admin/sla-overview")
async def get_sla_overview_endpoint(
    user: dict = Depends(auth_module.require_auth),
):
    """Per-site healing rate SLA overview with 7-period trend.

    Returns for each active site: current healing rate, SLA target,
    whether the SLA is met, and the last 7 hourly periods for trending.
    """
    from .fleet import get_pool
    from .healing_sla import get_sla_overview

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        overview = await get_sla_overview(conn)

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "sites": overview,
    }


# =============================================================================
# COMPLIANCE EVIDENCE PACKET GENERATION
# =============================================================================

@router.get("/admin/sites/{site_id}/compliance-packet")
async def generate_site_compliance_packet(
    site_id: str,
    user: dict = Depends(auth_module.require_auth),
):
    """Generate a JSON compliance evidence packet for a site.

    Aggregates site info, compliance scores, healing rate, evidence
    bundle hashes, incident summary, and HIPAA control mapping into a
    single downloadable JSON structure.

    Partners can format this however they need for auditors or clients.
    """
    from .fleet import get_pool

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # Verify site exists
        site = await conn.fetchrow("""
            SELECT s.site_id, s.clinic_name, s.status, s.created_at,
                   s.client_org_id
            FROM sites s WHERE s.site_id = $1
        """, site_id)
        if not site:
            raise HTTPException(404, "Site not found")

        await check_site_access_pool(conn, user, site_id)

        # --- Site info ---
        devices = await conn.fetch("""
            SELECT hostname, os_type, compliance_status, last_seen
            FROM discovered_devices
            WHERE site_id = $1
            ORDER BY last_seen DESC NULLS LAST
        """, site_id)

        agents = await conn.fetch("""
            SELECT appliance_id, hostname, agent_version, status, last_checkin
            FROM site_appliances
            WHERE site_id = $1
            ORDER BY last_checkin DESC NULLS LAST
        """, site_id)

        # --- Compliance score + category breakdown ---
        score_row = await conn.fetchrow("""
            WITH expanded AS (
                SELECT
                    c->>'check' AS check_type,
                    c->>'status' AS check_status
                FROM compliance_bundles cb,
                     jsonb_array_elements(cb.checks) AS c
                WHERE cb.site_id = $1
                  AND cb.checked_at > NOW() - INTERVAL '30 days'
                  AND jsonb_array_length(cb.checks) > 0
            )
            SELECT
                COUNT(*) FILTER (WHERE check_status IN ('pass', 'compliant')) AS passes,
                COUNT(*) FILTER (WHERE check_status = 'warning') AS warnings,
                COUNT(*) FILTER (WHERE check_status IN ('fail', 'non_compliant')) AS fails,
                COUNT(*) AS total
            FROM expanded
            WHERE check_type IS NOT NULL
        """, site_id)

        total_checks = score_row["total"] or 0
        passes = score_row["passes"] or 0
        warnings = score_row["warnings"] or 0
        fails = score_row["fails"] or 0
        distinct_fails = fails  # Each check result counted once
        denom = passes + warnings + distinct_fails
        compliance_score = round((passes + 0.5 * warnings) / denom * 100, 1) if denom > 0 else None

        # Category breakdown (group by check_type prefix)
        categories = await conn.fetch("""
            WITH expanded AS (
                SELECT
                    c->>'check' AS check_type,
                    c->>'status' AS check_status
                FROM compliance_bundles cb,
                     jsonb_array_elements(cb.checks) AS c
                WHERE cb.site_id = $1
                  AND cb.checked_at > NOW() - INTERVAL '30 days'
                  AND jsonb_array_length(cb.checks) > 0
            )
            SELECT
                check_type,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE check_status IN ('pass', 'compliant')) AS pass_count,
                COUNT(*) FILTER (WHERE check_status IN ('fail', 'non_compliant')) AS fail_count
            FROM expanded
            WHERE check_type IS NOT NULL
            GROUP BY check_type
            ORDER BY fail_count DESC, total DESC
        """, site_id)

        # --- Healing rate for last 30 days ---
        healing = await conn.fetchrow("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE success = true) AS succeeded
            FROM execution_telemetry
            WHERE site_id = $1
              AND created_at > NOW() - INTERVAL '30 days'
        """, site_id)

        healing_total = healing["total"] or 0
        healing_succeeded = healing["succeeded"] or 0
        healing_rate = round(healing_succeeded / healing_total * 100, 1) if healing_total > 0 else None

        # SLA status (latest period)
        sla = await conn.fetchrow("""
            SELECT healing_rate, sla_target, sla_met, period_start, period_end
            FROM site_healing_sla
            WHERE site_id = $1
            ORDER BY period_start DESC
            LIMIT 1
        """, site_id)

        # --- Evidence bundle hashes (last 30 days, most recent 50) ---
        evidence_hashes = await conn.fetch("""
            SELECT bundle_id, bundle_hash, chain_position, check_type,
                   check_result, checked_at
            FROM compliance_bundles
            WHERE site_id = $1
              AND checked_at > NOW() - INTERVAL '30 days'
            ORDER BY chain_position DESC
            LIMIT 50
        """, site_id)

        # --- Incident summary ---
        incident_summary = await conn.fetch("""
            SELECT
                COALESCE(i.check_type, i.incident_type, 'unknown') AS category,
                COUNT(*) FILTER (WHERE i.status != 'resolved') AS open_count,
                COUNT(*) FILTER (WHERE i.status = 'resolved') AS resolved_count,
                COUNT(*) AS total
            FROM incidents i
            JOIN appliances a ON a.id = i.appliance_id
            WHERE a.site_id = $1
              AND i.reported_at > NOW() - INTERVAL '30 days'
            GROUP BY category
            ORDER BY total DESC
        """, site_id)

        incident_totals = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE i.status != 'resolved') AS total_open,
                COUNT(*) FILTER (WHERE i.status = 'resolved') AS total_resolved,
                COUNT(*) AS total
            FROM incidents i
            JOIN appliances a ON a.id = i.appliance_id
            WHERE a.site_id = $1
              AND i.reported_at > NOW() - INTERVAL '30 days'
        """, site_id)

        # --- HIPAA control mapping status ---
        control_mappings = await conn.fetch("""
            SELECT
                crm.framework, crm.control_id, crm.control_title,
                crm.runbook_id, crm.automated
            FROM control_runbook_mapping crm
            ORDER BY crm.framework, crm.control_id
        """)

    # Build the packet
    now = datetime.now(timezone.utc)
    packet = {
        "packet_type": "compliance_evidence",
        "generated_at": now.isoformat(),
        "period": "last_30_days",

        "site": {
            "site_id": site["site_id"],
            "clinic_name": site["clinic_name"],
            "status": site["status"],
            "created_at": site["created_at"].isoformat() if site["created_at"] else None,
            "device_count": len(devices),
            "agent_count": len(agents),
            "devices": [
                {
                    "hostname": d["hostname"],
                    "os_type": d["os_type"],
                    "compliance_status": d["compliance_status"],
                    "last_seen": d["last_seen"].isoformat() if d["last_seen"] else None,
                }
                for d in devices
            ],
            "agents": [
                {
                    "appliance_id": a["appliance_id"],
                    "hostname": a["hostname"],
                    "agent_version": a["agent_version"],
                    "status": a["status"],
                    "last_checkin": a["last_checkin"].isoformat() if a["last_checkin"] else None,
                }
                for a in agents
            ],
        },

        "compliance": {
            "score": compliance_score,
            "total_checks": total_checks,
            "passes": passes,
            "warnings": warnings,
            "fails": fails,
            "categories": [
                {
                    "check_type": c["check_type"],
                    "total": c["total"],
                    "pass_count": c["pass_count"],
                    "fail_count": c["fail_count"],
                    "pass_rate": round(c["pass_count"] / c["total"] * 100, 1) if c["total"] > 0 else 0,
                }
                for c in categories
            ],
        },

        "healing": {
            "total_attempts_30d": healing_total,
            "successful_heals_30d": healing_succeeded,
            "healing_rate_30d": healing_rate,
            "sla": {
                "current_rate": float(sla["healing_rate"]) if sla else None,
                "target": float(sla["sla_target"]) if sla else None,
                "met": sla["sla_met"] if sla else None,
                "period_start": sla["period_start"].isoformat() if sla else None,
                "period_end": sla["period_end"].isoformat() if sla else None,
            } if sla else None,
        },

        "evidence_chain": {
            "bundle_count": len(evidence_hashes),
            "bundles": [
                {
                    "bundle_id": str(e["bundle_id"]),
                    "bundle_hash": e["bundle_hash"],
                    "chain_position": e["chain_position"],
                    "check_type": e["check_type"],
                    "check_result": e["check_result"],
                    "checked_at": e["checked_at"].isoformat() if e["checked_at"] else None,
                }
                for e in evidence_hashes
            ],
        },

        "incidents": {
            "total_open": incident_totals["total_open"] if incident_totals else 0,
            "total_resolved": incident_totals["total_resolved"] if incident_totals else 0,
            "total": incident_totals["total"] if incident_totals else 0,
            "by_category": [
                {
                    "category": row["category"],
                    "open": row["open_count"],
                    "resolved": row["resolved_count"],
                    "total": row["total"],
                }
                for row in incident_summary
            ],
        },

        "hipaa_controls": {
            "total_mappings": len(control_mappings),
            "mappings": [
                {
                    "framework": m["framework"],
                    "control_id": m["control_id"],
                    "control_title": m["control_title"],
                    "runbook_id": m["runbook_id"],
                    "automated": m["automated"],
                }
                for m in control_mappings
            ],
        },
    }

    return Response(
        content=json.dumps(packet, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="compliance-packet-{site_id}-{now.strftime("%Y%m%d")}.json"',
        },
    )


@router.get("/organizations/{org_id}/compliance-packet")
async def generate_org_compliance_packet(
    org_id: str,
    user: dict = Depends(auth_module.require_auth),
):
    """Generate an org-level compliance evidence packet with witness attestations.

    Aggregates compliance, healing, evidence, incidents, and witness
    attestations across all sites in the org. Audit-supportive JSON.
    """
    auth_module._check_org_access(user, org_id)
    from .fleet import get_pool

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        org = await conn.fetchrow("SELECT id, name, practice_type FROM client_orgs WHERE id = $1", org_id)
        if not org:
            raise HTTPException(404, "Organization not found")

        site_ids = [r['site_id'] for r in await conn.fetch(
            "SELECT site_id FROM sites WHERE client_org_id = $1", org_id
        )]
        if not site_ids:
            raise HTTPException(404, "No sites in organization")

        sites = await conn.fetch("""
            SELECT s.site_id, s.clinic_name, sa.agent_version, sa.last_checkin
            FROM sites s LEFT JOIN site_appliances sa ON s.site_id = sa.site_id
            WHERE s.client_org_id = $1 ORDER BY s.clinic_name
        """, org_id)

        # Compliance (DISTINCT ON for latest per site+type)
        compliance = await conn.fetchrow("""
            SELECT count(*) FILTER (WHERE check_result = 'pass') as passes,
                   count(*) FILTER (WHERE check_result = 'fail') as fails,
                   count(*) as total
            FROM (
                SELECT DISTINCT ON (site_id, check_type) check_result
                FROM compliance_bundles WHERE site_id = ANY($1)
                ORDER BY site_id, check_type, checked_at DESC
            ) latest
        """, site_ids)

        # Healing
        healing = await conn.fetchrow("""
            SELECT count(*) FILTER (WHERE success) as succeeded,
                   count(*) as total
            FROM execution_telemetry
            WHERE site_id = ANY($1) AND created_at > NOW() - interval '30 days'
        """, site_ids)

        # Evidence chain summary
        evidence = await conn.fetchrow("""
            SELECT count(*) as total_bundles,
                   count(*) FILTER (WHERE agent_signature IS NOT NULL) as signed,
                   count(*) FILTER (WHERE signature_valid) as verified
            FROM compliance_bundles
            WHERE site_id = ANY($1) AND checked_at > NOW() - interval '30 days'
        """, site_ids)

        # Witness attestations
        witnesses = await conn.fetch("""
            SELECT wa.bundle_id, wa.witness_appliance, wa.witness_signature,
                   wa.witness_public_key, wa.created_at
            FROM witness_attestations wa
            WHERE wa.bundle_id IN (
                SELECT bundle_id FROM compliance_bundles WHERE site_id = ANY($1)
            )
            ORDER BY wa.created_at DESC
            LIMIT 100
        """, site_ids)

        witness_summary = await conn.fetchrow("""
            SELECT count(*) as total,
                   count(DISTINCT bundle_id) as witnessed_bundles,
                   count(DISTINCT witness_appliance) as witness_count
            FROM witness_attestations
            WHERE bundle_id IN (
                SELECT bundle_id FROM compliance_bundles WHERE site_id = ANY($1)
            )
        """, site_ids)

        # Incidents
        incidents = await conn.fetchrow("""
            SELECT count(*) FILTER (WHERE status != 'resolved') as open,
                   count(*) FILTER (WHERE status = 'resolved') as resolved,
                   count(*) as total
            FROM incidents WHERE site_id = ANY($1)
            AND created_at > NOW() - interval '30 days'
        """, site_ids)

    now = datetime.now(timezone.utc)
    total_c = compliance['total'] or 0
    passes = compliance['passes'] or 0
    h_total = healing['total'] or 0
    h_ok = healing['succeeded'] or 0

    packet = {
        "packet_type": "org_compliance_evidence",
        "generated_at": now.isoformat(),
        "period": "last_30_days",

        "organization": {
            "id": str(org['id']),
            "name": org['name'],
            "practice_type": org['practice_type'],
            "site_count": len(site_ids),
            "sites": [
                {
                    "site_id": s['site_id'],
                    "clinic_name": s['clinic_name'],
                    "agent_version": s['agent_version'],
                    "last_checkin": s['last_checkin'].isoformat() if s['last_checkin'] else None,
                }
                for s in sites
            ],
        },

        "compliance": {
            "score": round(passes / total_c * 100, 1) if total_c > 0 else 0,
            "total_checks": total_c,
            "passes": passes,
            "fails": compliance['fails'] or 0,
        },

        "healing": {
            "total_attempts_30d": h_total,
            "successful_heals_30d": h_ok,
            "healing_rate_30d": round(h_ok / h_total * 100, 1) if h_total > 0 else 0,
        },

        "evidence_chain": {
            "total_bundles": evidence['total_bundles'] or 0,
            "signed": evidence['signed'] or 0,
            "verified": evidence['verified'] or 0,
            "signing_coverage_pct": round((evidence['signed'] or 0) / (evidence['total_bundles'] or 1) * 100, 1),
        },

        "witness_attestations": {
            "total_attestations": witness_summary['total'] or 0,
            "witnessed_bundles": witness_summary['witnessed_bundles'] or 0,
            "witness_appliance_count": witness_summary['witness_count'] or 0,
            "recent_attestations": [
                {
                    "bundle_id": w['bundle_id'],
                    "witness_appliance": w['witness_appliance'],
                    "witness_signature": w['witness_signature'][:32] + "...",
                    "created_at": w['created_at'].isoformat() if w['created_at'] else None,
                }
                for w in witnesses[:20]
            ],
        },

        "incidents": {
            "open": incidents['open'] or 0,
            "resolved": incidents['resolved'] or 0,
            "total": incidents['total'] or 0,
        },
    }

    return Response(
        content=json.dumps(packet, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="compliance-packet-org-{org_id[:8]}-{now.strftime("%Y%m%d")}.json"',
        },
    )
