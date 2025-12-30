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

    # Fallback to mock data
    return get_mock_fleet_overview()


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

    # Fallback to mock data
    detail = get_mock_client_detail(site_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Client {site_id} not found")
    return detail


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

    # Fallback to mock data
    detail = get_mock_client_detail(site_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Client {site_id} not found")
    return detail.appliances


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
    now = datetime.now(timezone.utc)

    # Mock incidents for Phase 1
    incidents = [
        Incident(
            id=1001,
            site_id="north-valley-family-practice",
            hostname="NVFP-DC01",
            check_type=CheckType.BACKUP,
            severity=Severity.MEDIUM,
            resolution_level=ResolutionLevel.L1,
            resolved=True,
            resolved_at=now - timedelta(hours=4),
            hipaa_controls=["164.308(a)(7)"],
            created_at=now - timedelta(hours=4, minutes=5),
        ),
        Incident(
            id=1002,
            site_id="cedar-medical-group",
            hostname="CMG-DC01",
            check_type=CheckType.PATCHING,
            severity=Severity.HIGH,
            resolution_level=ResolutionLevel.L2,
            resolved=True,
            resolved_at=now - timedelta(hours=2),
            hipaa_controls=["164.308(a)(5)(ii)(B)"],
            created_at=now - timedelta(hours=2, minutes=30),
        ),
        Incident(
            id=1003,
            site_id="lakeside-pediatrics",
            hostname="LP-FS01",
            check_type=CheckType.ANTIVIRUS,
            severity=Severity.HIGH,
            resolution_level=ResolutionLevel.L1,
            resolved=True,
            resolved_at=now - timedelta(hours=6),
            hipaa_controls=["164.308(a)(5)(ii)(B)"],
            created_at=now - timedelta(hours=6, minutes=2),
        ),
        Incident(
            id=1004,
            site_id="cedar-medical-group",
            hostname="CMG-DC01",
            check_type=CheckType.FIREWALL,
            severity=Severity.CRITICAL,
            resolution_level=ResolutionLevel.L3,
            resolved=False,
            hipaa_controls=["164.312(e)(1)"],
            created_at=now - timedelta(hours=1),
        ),
    ]

    # Apply filters
    if site_id:
        incidents = [i for i in incidents if i.site_id == site_id]
    if level:
        incidents = [i for i in incidents if i.resolution_level and i.resolution_level.value == level]
    if resolved is not None:
        incidents = [i for i in incidents if i.resolved == resolved]

    return incidents[:limit]


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
async def get_incident_detail(incident_id: int):
    """Get full incident detail including evidence bundle."""
    now = datetime.now(timezone.utc)

    # Mock incident detail for Phase 1
    if incident_id == 1001:
        return IncidentDetail(
            id=1001,
            site_id="north-valley-family-practice",
            appliance_id=1,
            hostname="NVFP-DC01",
            check_type=CheckType.BACKUP,
            severity=Severity.MEDIUM,
            drift_data={
                "last_backup": (now - timedelta(hours=26)).isoformat(),
                "backup_sla_hours": 24,
                "backup_tool": "Veeam",
            },
            resolution_level=ResolutionLevel.L1,
            resolved=True,
            resolved_at=now - timedelta(hours=4),
            hipaa_controls=["164.308(a)(7)"],
            evidence_bundle_id=5001,
            evidence_hash="a1b2c3d4e5f6...",
            runbook_executed="RB-WIN-BACKUP-001",
            execution_log="Backup service restarted successfully",
            created_at=now - timedelta(hours=4, minutes=5),
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
    now = datetime.now(timezone.utc)

    return [
        PromotionCandidate(
            id="pattern-001",
            pattern_signature="backup_vss_writer_failure",
            description="VSS Writer service failure during backup",
            occurrences=12,
            success_rate=100.0,
            avg_resolution_time_ms=45000,
            proposed_rule="Restart VSS Writer service when backup fails with VSS error",
            first_seen=now - timedelta(days=30),
            last_seen=now - timedelta(hours=6),
        ),
        PromotionCandidate(
            id="pattern-002",
            pattern_signature="defender_signature_stale",
            description="Windows Defender signatures older than 7 days",
            occurrences=8,
            success_rate=100.0,
            avg_resolution_time_ms=120000,
            proposed_rule="Force Defender signature update via Update-MpSignature",
            first_seen=now - timedelta(days=21),
            last_seen=now - timedelta(days=1),
        ),
        PromotionCandidate(
            id="pattern-003",
            pattern_signature="temp_disk_full",
            description="Temp folder consuming >90% of disk space",
            occurrences=6,
            success_rate=83.3,
            avg_resolution_time_ms=30000,
            proposed_rule="Clear temp files older than 7 days when disk >90%",
            first_seen=now - timedelta(days=14),
            last_seen=now - timedelta(days=2),
        ),
    ]


@router.get("/learning/history", response_model=List[PromotionHistory])
async def get_promotion_history(limit: int = Query(default=20, le=100)):
    """Get recently promoted L2->L1 rules."""
    now = datetime.now(timezone.utc)

    return [
        PromotionHistory(
            id=1,
            pattern_signature="disk_temp_cleanup",
            rule_id="RB-WIN-DISK-002",
            promoted_at=now - timedelta(days=2),
            post_promotion_success_rate=100.0,
            executions_since_promotion=8,
        ),
        PromotionHistory(
            id=2,
            pattern_signature="eventlog_full",
            rule_id="RB-WIN-LOGGING-002",
            promoted_at=now - timedelta(days=15),
            post_promotion_success_rate=98.0,
            executions_since_promotion=24,
        ),
        PromotionHistory(
            id=3,
            pattern_signature="cert_expiry_30d",
            rule_id="RB-WIN-CERT-001",
            promoted_at=now - timedelta(days=29),
            post_promotion_success_rate=100.0,
            executions_since_promotion=12,
        ),
    ][:limit]


@router.post("/learning/promote/{pattern_id}")
async def promote_pattern(pattern_id: str):
    """Manually trigger promotion of a pattern to L1.

    Requires: Human review confirmation.
    """
    if pattern_id not in ["pattern-001", "pattern-002", "pattern-003"]:
        raise HTTPException(status_code=404, detail=f"Pattern {pattern_id} not found")

    return {
        "status": "promoted",
        "pattern_id": pattern_id,
        "new_rule_id": f"RB-AUTO-{pattern_id.upper()}",
        "message": "Pattern promoted to L1 successfully",
    }


# =============================================================================
# ONBOARDING PIPELINE ENDPOINTS
# =============================================================================

@router.get("/onboarding", response_model=List[OnboardingClient])
async def get_onboarding_pipeline():
    """Get all prospects in the onboarding pipeline.

    Returns:
        List of clients with stage, progress, blockers.
    """
    now = datetime.now(timezone.utc)

    return [
        OnboardingClient(
            id=1,
            name="Riverside Family Care",
            contact_name="Dr. Michael Chen",
            contact_email="mchen@riversidefc.com",
            contact_phone="(570) 555-0101",
            stage=OnboardingStage.CONNECTIVITY,
            stage_entered_at=now - timedelta(days=2),
            days_in_stage=2,
            blockers=["WinRM not enabled on target servers"],
            lead_at=now - timedelta(days=15),
            discovery_at=now - timedelta(days=12),
            proposal_at=now - timedelta(days=10),
            contract_at=now - timedelta(days=8),
            intake_at=now - timedelta(days=6),
            creds_at=now - timedelta(days=5),
            shipped_at=now - timedelta(days=4),
            received_at=now - timedelta(days=2),
            connectivity_at=now - timedelta(days=2),
            tracking_number="1Z999AA10123456784",
            tracking_carrier="UPS",
            appliance_serial="T640-2024-0042",
            site_id="riverside-family-care",
            checkin_status=CheckinStatus.CONNECTED,
            last_checkin=now - timedelta(minutes=2),
            progress_percent=69,
            phase=2,
            phase_progress=33,
            created_at=now - timedelta(days=15),
        ),
        OnboardingClient(
            id=2,
            name="Valley Pediatrics",
            contact_name="Dr. Sarah Mitchell",
            contact_email="smitchell@valleypeds.com",
            contact_phone="(570) 555-0202",
            stage=OnboardingStage.RECEIVED,
            stage_entered_at=now - timedelta(days=1),
            days_in_stage=1,
            blockers=["Appliance not phoning home - possible firewall issue"],
            lead_at=now - timedelta(days=14),
            discovery_at=now - timedelta(days=12),
            proposal_at=now - timedelta(days=10),
            contract_at=now - timedelta(days=9),
            intake_at=now - timedelta(days=7),
            creds_at=now - timedelta(days=5),
            shipped_at=now - timedelta(days=3),
            received_at=now - timedelta(days=1),
            tracking_number="1Z999AA10123456785",
            tracking_carrier="UPS",
            appliance_serial="T640-2024-0043",
            checkin_status=CheckinStatus.PENDING,
            progress_percent=62,
            phase=2,
            phase_progress=17,
            created_at=now - timedelta(days=14),
        ),
        OnboardingClient(
            id=3,
            name="Mountain View Medical",
            contact_name="Jennifer Walsh",
            contact_email="jwalsh@mvmedical.com",
            contact_phone="(570) 555-0303",
            stage=OnboardingStage.INTAKE,
            stage_entered_at=now - timedelta(days=5),
            days_in_stage=5,
            blockers=["Awaiting IT contact for AD credentials"],
            lead_at=now - timedelta(days=18),
            discovery_at=now - timedelta(days=15),
            proposal_at=now - timedelta(days=12),
            contract_at=now - timedelta(days=10),
            intake_at=now - timedelta(days=5),
            progress_percent=38,
            phase=1,
            phase_progress=71,
            created_at=now - timedelta(days=18),
        ),
        OnboardingClient(
            id=4,
            name="Cedar Heights Clinic",
            contact_name="Dr. Robert Park",
            contact_email="rpark@cedarheights.com",
            contact_phone="(570) 555-0404",
            stage=OnboardingStage.DISCOVERY,
            stage_entered_at=now - timedelta(days=3),
            days_in_stage=3,
            notes="Call scheduled for Jan 2, 2:00 PM",
            lead_at=now - timedelta(days=5),
            discovery_at=now - timedelta(days=3),
            progress_percent=15,
            phase=1,
            phase_progress=29,
            created_at=now - timedelta(days=5),
        ),
    ]


@router.get("/onboarding/metrics", response_model=OnboardingMetrics)
async def get_onboarding_metrics():
    """Get aggregate pipeline metrics.

    Returns:
        Counts by stage, avg time to deploy, at-risk clients.
    """
    return OnboardingMetrics(
        total_prospects=11,
        acquisition={
            "lead": 2,
            "discovery": 1,
            "proposal": 1,
            "contract": 0,
            "intake": 1,
            "creds": 0,
            "shipped": 1,
        },
        activation={
            "received": 1,
            "connectivity": 1,
            "scanning": 0,
            "baseline": 0,
            "compliant": 0,
            "active": 3,
        },
        avg_days_to_ship=10.5,
        avg_days_to_active=18.0,
        stalled_count=2,
        at_risk_count=1,
        connectivity_issues=1,
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
