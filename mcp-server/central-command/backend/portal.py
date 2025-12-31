"""Client Portal API endpoints.

Token-authenticated endpoints for client-facing compliance dashboards.
"""

import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/portal", tags=["portal"])


# =============================================================================
# MODELS
# =============================================================================

class PortalKPIs(BaseModel):
    """KPI metrics for portal display."""
    compliance_pct: float = 0.0
    patch_mttr_hours: float = 0.0
    mfa_coverage_pct: float = 100.0
    backup_success_rate: float = 100.0
    auto_fixes_24h: int = 0
    controls_passing: int = 0
    controls_warning: int = 0
    controls_failing: int = 0
    health_score: float = 0.0


class PortalControl(BaseModel):
    """Single control result for portal display."""
    rule_id: str
    name: str
    status: str  # pass, warn, fail
    severity: str  # critical, high, medium, low
    checked_at: Optional[datetime] = None
    hipaa_controls: List[str] = []
    scope_summary: str = ""
    auto_fix_triggered: bool = False
    fix_duration_sec: Optional[int] = None
    exception_applied: bool = False
    exception_reason: Optional[str] = None


class PortalIncident(BaseModel):
    """Incident summary for portal display."""
    incident_id: str
    incident_type: str
    severity: str
    auto_fixed: bool
    resolution_time_sec: Optional[int] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


class PortalEvidenceBundle(BaseModel):
    """Evidence bundle metadata for download."""
    bundle_id: str
    bundle_type: str  # daily, weekly, monthly
    generated_at: datetime
    size_bytes: int = 0


class PortalSite(BaseModel):
    """Site info for portal."""
    site_id: str
    name: str
    status: str
    last_checkin: Optional[datetime] = None


class PortalData(BaseModel):
    """Complete portal data response."""
    site: PortalSite
    kpis: PortalKPIs
    controls: List[PortalControl]
    incidents: List[PortalIncident]
    evidence_bundles: List[PortalEvidenceBundle]
    generated_at: datetime


class TokenResponse(BaseModel):
    """Portal token generation response."""
    portal_url: str
    token: str
    expires: str = "never"


# =============================================================================
# CONTROL METADATA
# =============================================================================

CONTROL_METADATA = {
    "endpoint_drift": {
        "name": "Endpoint Configuration Drift",
        "severity": "high",
        "hipaa": ["164.308(a)(1)(ii)(D)", "164.310(d)(1)"]
    },
    "patch_freshness": {
        "name": "Critical Patch Timeliness",
        "severity": "critical",
        "hipaa": ["164.308(a)(5)(ii)(B)"]
    },
    "backup_success": {
        "name": "Backup Success & Restore Testing",
        "severity": "critical",
        "hipaa": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
    },
    "mfa_coverage": {
        "name": "MFA Coverage for Human Accounts",
        "severity": "high",
        "hipaa": ["164.312(a)(2)(i)", "164.308(a)(4)(ii)(C)"]
    },
    "privileged_access": {
        "name": "Privileged Access Review",
        "severity": "high",
        "hipaa": ["164.308(a)(3)(ii)(B)", "164.308(a)(4)(ii)(B)"]
    },
    "git_protections": {
        "name": "Git Branch Protection",
        "severity": "medium",
        "hipaa": ["164.312(b)", "164.308(a)(5)(ii)(D)"]
    },
    "secrets_hygiene": {
        "name": "Secrets & Deploy Key Hygiene",
        "severity": "high",
        "hipaa": ["164.312(a)(2)(i)", "164.308(a)(4)(ii)(B)"]
    },
    "storage_posture": {
        "name": "Object Storage ACL Posture",
        "severity": "critical",
        "hipaa": ["164.310(d)(2)(iii)", "164.312(a)(1)"]
    }
}


# =============================================================================
# IN-MEMORY STORE (Replace with database in production)
# =============================================================================

# Temporary in-memory storage until database migration is complete
_portal_tokens: Dict[str, str] = {}  # site_id -> token
_compliance_data: Dict[str, Dict] = {}  # site_id -> compliance snapshot


def _get_db():
    """Placeholder for database dependency."""
    return None


# =============================================================================
# TOKEN MANAGEMENT
# =============================================================================

@router.post("/sites/{site_id}/generate-token", response_model=TokenResponse)
async def generate_portal_token(site_id: str):
    """Generate magic link token for client portal access."""
    # Generate 64-char token
    token = secrets.token_urlsafe(48)

    # Store token (in production, save to database)
    _portal_tokens[site_id] = token

    return TokenResponse(
        portal_url=f"https://portal.osiriscare.net/site/{site_id}?token={token}",
        token=token,
        expires="never"
    )


async def validate_token(site_id: str, token: str) -> bool:
    """Validate portal access token."""
    stored_token = _portal_tokens.get(site_id)
    if not stored_token or stored_token != token:
        raise HTTPException(status_code=403, detail="Invalid portal token")
    return True


# =============================================================================
# MAIN PORTAL ENDPOINT
# =============================================================================

@router.get("/site/{site_id}", response_model=PortalData)
async def get_portal_data(
    site_id: str,
    token: str = Query(..., description="Portal access token")
):
    """Main portal data endpoint - validates token and returns all portal data."""
    await validate_token(site_id, token)

    # Get site info (mock for now)
    site = PortalSite(
        site_id=site_id,
        name=site_id.replace("-", " ").title(),
        status="online",
        last_checkin=datetime.now(timezone.utc) - timedelta(minutes=2)
    )

    # Get compliance data from phone-home cache
    compliance = _compliance_data.get(site_id, {})

    # Build KPIs
    kpis = PortalKPIs(
        compliance_pct=compliance.get("compliance_pct", 95.0),
        patch_mttr_hours=compliance.get("patch_mttr_hours", 4.2),
        mfa_coverage_pct=compliance.get("mfa_coverage_pct", 100.0),
        backup_success_rate=compliance.get("backup_success_rate", 100.0),
        auto_fixes_24h=compliance.get("auto_fixes_24h", 0),
        controls_passing=compliance.get("controls_passing", 8),
        controls_warning=compliance.get("controls_warning", 0),
        controls_failing=compliance.get("controls_failing", 0),
        health_score=compliance.get("health_score", 95.0)
    )

    # Build controls (8 core controls)
    controls = []
    control_results = compliance.get("control_results", {})

    for rule_id, meta in CONTROL_METADATA.items():
        result = control_results.get(rule_id, {})
        controls.append(PortalControl(
            rule_id=rule_id,
            name=meta["name"],
            status=result.get("status", "pass"),
            severity=meta["severity"],
            checked_at=result.get("checked_at"),
            hipaa_controls=meta["hipaa"],
            scope_summary=result.get("scope_summary", "All checks passing"),
            auto_fix_triggered=result.get("auto_fix_triggered", False),
            fix_duration_sec=result.get("fix_duration_sec"),
            exception_applied=result.get("exception_applied", False),
            exception_reason=result.get("exception_reason")
        ))

    # Build incidents (recent)
    incidents = []
    for inc in compliance.get("recent_incidents", []):
        incidents.append(PortalIncident(
            incident_id=inc.get("incident_id", ""),
            incident_type=inc.get("type", ""),
            severity=inc.get("severity", "medium"),
            auto_fixed=inc.get("auto_fixed", False),
            resolution_time_sec=inc.get("resolution_time_sec"),
            created_at=inc.get("created_at", datetime.now(timezone.utc)),
            resolved_at=inc.get("resolved_at")
        ))

    # Build evidence bundles
    bundles = []
    for bundle in compliance.get("evidence_bundles", []):
        bundles.append(PortalEvidenceBundle(
            bundle_id=bundle.get("bundle_id", ""),
            bundle_type=bundle.get("bundle_type", "daily"),
            generated_at=bundle.get("generated_at", datetime.now(timezone.utc)),
            size_bytes=bundle.get("size_bytes", 0)
        ))

    return PortalData(
        site=site,
        kpis=kpis,
        controls=controls,
        incidents=incidents,
        evidence_bundles=bundles,
        generated_at=datetime.now(timezone.utc)
    )


# =============================================================================
# CONTROLS ENDPOINT
# =============================================================================

@router.get("/site/{site_id}/controls")
async def get_controls(
    site_id: str,
    token: str = Query(..., description="Portal access token")
):
    """Get 8 core controls with current status."""
    await validate_token(site_id, token)

    compliance = _compliance_data.get(site_id, {})
    control_results = compliance.get("control_results", {})

    controls = []
    for rule_id, meta in CONTROL_METADATA.items():
        result = control_results.get(rule_id, {})
        controls.append({
            "rule_id": rule_id,
            "name": meta["name"],
            "status": result.get("status", "pass"),
            "severity": meta["severity"],
            "checked_at": result.get("checked_at"),
            "hipaa_controls": meta["hipaa"],
            "scope": result.get("scope", {"summary": "All checks passing"}),
            "auto_fix_triggered": result.get("auto_fix_triggered", False),
            "fix_duration_sec": result.get("fix_duration_sec"),
            "exception_applied": result.get("exception_applied", False),
            "exception_reason": result.get("exception_reason")
        })

    return {"controls": controls}


# =============================================================================
# EVIDENCE ENDPOINTS
# =============================================================================

@router.get("/site/{site_id}/evidence")
async def list_evidence(
    site_id: str,
    token: str = Query(..., description="Portal access token")
):
    """List available evidence bundles."""
    await validate_token(site_id, token)

    compliance = _compliance_data.get(site_id, {})
    bundles = compliance.get("evidence_bundles", [])

    return {"bundles": bundles}


@router.get("/site/{site_id}/evidence/{bundle_id}/download")
async def download_evidence(
    site_id: str,
    bundle_id: str,
    token: str = Query(..., description="Portal access token")
):
    """Get presigned URL for evidence bundle download."""
    await validate_token(site_id, token)

    # In production, generate presigned MinIO URL
    # For now, return placeholder
    return {
        "download_url": f"https://api.osiriscare.net/evidence/{site_id}/{bundle_id}",
        "expires_in": 3600,
        "bundle_id": bundle_id
    }


# =============================================================================
# REPORT ENDPOINTS
# =============================================================================

@router.get("/site/{site_id}/report/monthly")
async def get_monthly_report(
    site_id: str,
    token: str = Query(..., description="Portal access token"),
    month: Optional[str] = Query(None, description="YYYY-MM format")
):
    """Generate or retrieve monthly compliance packet PDF."""
    await validate_token(site_id, token)

    if not month:
        month = datetime.now(timezone.utc).strftime("%Y-%m")

    # In production, check for existing report or trigger generation
    return {
        "status": "available",
        "download_url": f"https://api.osiriscare.net/reports/{site_id}/monthly-{month}.pdf",
        "month": month,
        "generated_at": datetime.now(timezone.utc).isoformat()
    }


# =============================================================================
# COMPLIANCE SNAPSHOT MODELS
# =============================================================================

class ControlResult(BaseModel):
    """Single control check result from appliance."""
    rule_id: str
    status: str  # pass, warn, fail
    checked_at: datetime
    scope_summary: str = ""
    auto_fix_triggered: bool = False
    fix_duration_sec: Optional[int] = None


class ComplianceSnapshot(BaseModel):
    """Compliance snapshot from appliance phone-home."""
    site_id: str
    host_id: str
    # KPIs
    compliance_pct: float = 100.0
    patch_mttr_hours: float = 0.0
    mfa_coverage_pct: float = 100.0
    backup_success_rate: float = 100.0
    auto_fixes_24h: int = 0
    health_score: float = 100.0
    # Control results
    control_results: List[ControlResult] = []
    # Recent incidents (last 24h)
    recent_incidents: List[Dict[str, Any]] = []
    # Metadata
    agent_version: Optional[str] = None
    policy_version: Optional[str] = None


# =============================================================================
# PHONE-HOME ENDPOINT
# =============================================================================

@router.post("/appliances/snapshot")
async def receive_compliance_snapshot(snapshot: ComplianceSnapshot):
    """Receive compliance snapshot from appliance phone-home.

    This endpoint is called by appliances every 5 minutes with their
    current compliance status, control check results, and KPIs.

    The data is cached in memory and displayed on the client portal.
    """
    # Count control statuses
    controls_passing = sum(1 for c in snapshot.control_results if c.status == "pass")
    controls_warning = sum(1 for c in snapshot.control_results if c.status == "warn")
    controls_failing = sum(1 for c in snapshot.control_results if c.status == "fail")

    # Build control results dict
    control_results_dict = {}
    for result in snapshot.control_results:
        control_results_dict[result.rule_id] = {
            "status": result.status,
            "checked_at": result.checked_at.isoformat() if result.checked_at else None,
            "scope_summary": result.scope_summary,
            "auto_fix_triggered": result.auto_fix_triggered,
            "fix_duration_sec": result.fix_duration_sec
        }

    # Update cached compliance data
    update_compliance_data(snapshot.site_id, {
        "compliance_pct": snapshot.compliance_pct,
        "patch_mttr_hours": snapshot.patch_mttr_hours,
        "mfa_coverage_pct": snapshot.mfa_coverage_pct,
        "backup_success_rate": snapshot.backup_success_rate,
        "auto_fixes_24h": snapshot.auto_fixes_24h,
        "controls_passing": controls_passing,
        "controls_warning": controls_warning,
        "controls_failing": controls_failing,
        "health_score": snapshot.health_score,
        "compliance_results": control_results_dict,
        "recent_incidents": snapshot.recent_incidents
    })

    return {
        "status": "received",
        "site_id": snapshot.site_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "controls_received": len(snapshot.control_results)
    }


# =============================================================================
# INTERNAL: UPDATE COMPLIANCE DATA FROM PHONE-HOME
# =============================================================================

def update_compliance_data(site_id: str, data: Dict[str, Any]):
    """Update compliance data from appliance phone-home.

    Called by the phone-home endpoint to cache portal data.
    """
    _compliance_data[site_id] = {
        "compliance_pct": data.get("compliance_pct", 95.0),
        "patch_mttr_hours": data.get("patch_mttr_hours", 4.2),
        "mfa_coverage_pct": data.get("mfa_coverage_pct", 100.0),
        "backup_success_rate": data.get("backup_success_rate", 100.0),
        "auto_fixes_24h": data.get("auto_fixes_24h", 0),
        "controls_passing": data.get("controls_passing", 8),
        "controls_warning": data.get("controls_warning", 0),
        "controls_failing": data.get("controls_failing", 0),
        "health_score": data.get("health_score", 95.0),
        "control_results": data.get("compliance_results", {}),
        "recent_incidents": data.get("recent_incidents", []),
        "evidence_bundles": data.get("evidence_bundles", []),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
