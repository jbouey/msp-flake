"""
Multi-Framework Compliance API Endpoints

Provides endpoints for:
- Appliance framework configuration (get/set enabled frameworks)
- Compliance scores per framework
- Framework metadata and industry recommendations
- Control-level compliance status
"""

import json
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Import framework service (will be available after package install)
try:
    from compliance_agent.frameworks import (
        ComplianceFramework,
        FrameworkService,
        get_recommended_frameworks,
    )
    FRAMEWORK_SERVICE_AVAILABLE = True
except ImportError:
    FRAMEWORK_SERVICE_AVAILABLE = False
    logger.warning("Framework service not available - using stub implementation")


router = APIRouter(prefix="/api/frameworks", tags=["frameworks"])


# =============================================================================
# Request/Response Models
# =============================================================================

class FrameworkConfigRequest(BaseModel):
    """Request to update appliance framework configuration"""
    enabled_frameworks: List[str] = Field(
        default=["hipaa"],
        description="List of enabled frameworks: hipaa, soc2, pci_dss, nist_csf, cis"
    )
    primary_framework: str = Field(
        default="hipaa",
        description="Primary framework for dashboard display"
    )
    industry: str = Field(
        default="healthcare",
        description="Industry for recommendations"
    )
    framework_metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Framework-specific metadata"
    )


class FrameworkConfigResponse(BaseModel):
    """Response with appliance framework configuration"""
    appliance_id: str
    site_id: str
    enabled_frameworks: List[str]
    primary_framework: str
    industry: str
    framework_metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ComplianceScoreResponse(BaseModel):
    """Compliance score for a single framework"""
    framework: str
    framework_name: str
    total_controls: int
    passing_controls: int
    failing_controls: int
    unknown_controls: int
    score_percentage: float
    is_compliant: bool
    at_risk: bool
    calculated_at: datetime


class ControlStatusResponse(BaseModel):
    """Status of a single control"""
    control_id: str
    control_name: str
    category: str
    status: str  # pass, fail, unknown
    last_checked: Optional[datetime]
    evidence_bundle_id: Optional[str]


class IndustryRecommendation(BaseModel):
    """Framework recommendations for an industry"""
    industry: str
    primary: str
    recommended: List[str]
    description: str


# =============================================================================
# Database Helper Functions
# =============================================================================

async def get_db():
    """Dependency to get database session"""
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


async def get_appliance_framework_config(
    db: AsyncSession,
    appliance_id: str
) -> Optional[Dict[str, Any]]:
    """Get framework configuration for an appliance"""
    query = text("""
        SELECT
            appliance_id, site_id, enabled_frameworks, primary_framework,
            industry, framework_metadata, created_at, updated_at
        FROM appliance_framework_configs
        WHERE appliance_id = :appliance_id
    """)
    result = await db.execute(query, {"appliance_id": appliance_id})
    row = result.fetchone()

    if not row:
        return None

    return {
        "appliance_id": row.appliance_id,
        "site_id": row.site_id,
        "enabled_frameworks": row.enabled_frameworks or ["hipaa"],
        "primary_framework": row.primary_framework or "hipaa",
        "industry": row.industry or "healthcare",
        "framework_metadata": row.framework_metadata or {},
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def upsert_appliance_framework_config(
    db: AsyncSession,
    appliance_id: str,
    site_id: str,
    enabled_frameworks: List[str],
    primary_framework: str,
    industry: str,
    framework_metadata: Dict[str, Any]
) -> Dict[str, Any]:
    """Create or update framework configuration for an appliance"""
    query = text("""
        INSERT INTO appliance_framework_configs (
            appliance_id, site_id, enabled_frameworks, primary_framework,
            industry, framework_metadata, created_at, updated_at
        ) VALUES (
            :appliance_id, :site_id, :enabled_frameworks, :primary_framework,
            :industry, CAST(:framework_metadata AS jsonb), NOW(), NOW()
        )
        ON CONFLICT (appliance_id) DO UPDATE SET
            enabled_frameworks = EXCLUDED.enabled_frameworks,
            primary_framework = EXCLUDED.primary_framework,
            industry = EXCLUDED.industry,
            framework_metadata = EXCLUDED.framework_metadata,
            updated_at = NOW()
        RETURNING appliance_id, site_id, enabled_frameworks, primary_framework,
                  industry, framework_metadata, created_at, updated_at
    """)

    result = await db.execute(query, {
        "appliance_id": appliance_id,
        "site_id": site_id,
        "enabled_frameworks": enabled_frameworks,
        "primary_framework": primary_framework,
        "industry": industry,
        "framework_metadata": json.dumps(framework_metadata),
    })
    await db.commit()
    row = result.fetchone()

    return {
        "appliance_id": row.appliance_id,
        "site_id": row.site_id,
        "enabled_frameworks": row.enabled_frameworks,
        "primary_framework": row.primary_framework,
        "industry": row.industry,
        "framework_metadata": row.framework_metadata,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def get_compliance_scores(
    db: AsyncSession,
    appliance_id: str
) -> List[Dict[str, Any]]:
    """Get compliance scores for all enabled frameworks"""
    query = text("""
        SELECT
            cs.framework,
            cs.total_controls,
            cs.passing_controls,
            cs.failing_controls,
            cs.unknown_controls,
            cs.score_percentage,
            cs.is_compliant,
            cs.at_risk,
            cs.calculated_at
        FROM compliance_scores cs
        JOIN appliance_framework_configs afc ON cs.appliance_id = afc.appliance_id
        WHERE cs.appliance_id = :appliance_id
          AND cs.framework = ANY(afc.enabled_frameworks)
        ORDER BY cs.framework
    """)

    result = await db.execute(query, {"appliance_id": appliance_id})
    rows = result.fetchall()

    return [
        {
            "framework": row.framework,
            "total_controls": row.total_controls,
            "passing_controls": row.passing_controls,
            "failing_controls": row.failing_controls,
            "unknown_controls": row.unknown_controls,
            "score_percentage": float(row.score_percentage or 0),
            "is_compliant": row.is_compliant,
            "at_risk": row.at_risk,
            "calculated_at": row.calculated_at,
        }
        for row in rows
    ]


async def get_control_status(
    db: AsyncSession,
    appliance_id: str,
    framework: str
) -> List[Dict[str, Any]]:
    """Get control-level status for a framework"""
    query = text("""
        SELECT
            control_id,
            outcome as status,
            last_checked
        FROM v_control_status
        WHERE appliance_id = :appliance_id
          AND framework = :framework
        ORDER BY control_id
    """)

    result = await db.execute(query, {
        "appliance_id": appliance_id,
        "framework": framework,
    })
    rows = result.fetchall()

    return [
        {
            "control_id": row.control_id,
            "status": row.status,
            "last_checked": row.last_checked,
        }
        for row in rows
    ]


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/appliances/{appliance_id}/config")
async def get_appliance_frameworks(
    appliance_id: str,
    db: AsyncSession = Depends(get_db)
) -> FrameworkConfigResponse:
    """
    Get current framework configuration for an appliance.

    Returns enabled frameworks, primary framework, and industry settings.
    """
    config = await get_appliance_framework_config(db, appliance_id)

    if not config:
        raise HTTPException(
            status_code=404,
            detail=f"Appliance {appliance_id} not found or not configured"
        )

    return FrameworkConfigResponse(**config)


@router.put("/appliances/{appliance_id}/config")
async def update_appliance_frameworks(
    appliance_id: str,
    request: FrameworkConfigRequest,
    db: AsyncSession = Depends(get_db)
) -> FrameworkConfigResponse:
    """
    Update framework configuration for an appliance.

    This changes which compliance frameworks the appliance reports against.
    Evidence bundles will be tagged for all enabled frameworks.
    """
    # Validate frameworks
    valid_frameworks = {"hipaa", "soc2", "pci_dss", "nist_csf", "cis"}
    for fw in request.enabled_frameworks:
        if fw not in valid_frameworks:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid framework: {fw}. Valid: {valid_frameworks}"
            )

    # Validate primary is in enabled list
    if request.primary_framework not in request.enabled_frameworks:
        raise HTTPException(
            status_code=400,
            detail="Primary framework must be in enabled frameworks list"
        )

    # Get site_id from appliance (appliance_id is actually site_id in our schema)
    site_query = text("SELECT site_id FROM appliances WHERE site_id = :appliance_id")
    result = await db.execute(site_query, {"appliance_id": appliance_id})
    row = result.fetchone()

    if not row:
        # Appliance not registered yet - use appliance_id as site_id
        site_id = appliance_id
    else:
        site_id = row.site_id

    # Upsert config
    config = await upsert_appliance_framework_config(
        db,
        appliance_id=appliance_id,
        site_id=site_id,
        enabled_frameworks=request.enabled_frameworks,
        primary_framework=request.primary_framework,
        industry=request.industry,
        framework_metadata=request.framework_metadata,
    )

    return FrameworkConfigResponse(**config)


@router.get("/appliances/{appliance_id}/scores")
async def get_appliance_compliance_scores(
    appliance_id: str,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get compliance scores for all enabled frameworks.

    Returns a score breakdown for each framework the appliance is configured for.
    """
    # Get config
    config = await get_appliance_framework_config(db, appliance_id)
    if not config:
        raise HTTPException(status_code=404, detail="Appliance not found")

    # Get scores
    scores = await get_compliance_scores(db, appliance_id)

    # Get framework names
    framework_names = {
        "hipaa": "HIPAA Security Rule",
        "soc2": "SOC 2 Type II",
        "pci_dss": "PCI DSS 4.0",
        "nist_csf": "NIST CSF 2.0",
        "cis": "CIS Controls v8",
    }

    return {
        "appliance_id": appliance_id,
        "site_id": config["site_id"],
        "primary_framework": config["primary_framework"],
        "scores": [
            {
                **score,
                "framework_name": framework_names.get(score["framework"], score["framework"]),
            }
            for score in scores
        ],
        "generated_at": datetime.utcnow().isoformat(),
    }


@router.get("/appliances/{appliance_id}/controls/{framework}")
async def get_appliance_control_status(
    appliance_id: str,
    framework: str,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get detailed control-by-control status for a specific framework.

    This is the audit-ready view showing every control and its evidence status.
    """
    # Validate framework
    valid_frameworks = {"hipaa", "soc2", "pci_dss", "nist_csf", "cis"}
    if framework not in valid_frameworks:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid framework: {framework}"
        )

    # Get control status from view
    controls = await get_control_status(db, appliance_id, framework)

    # Enhance with control names if framework service available
    if FRAMEWORK_SERVICE_AVAILABLE:
        service = FrameworkService()
        for ctrl in controls:
            details = service.get_control_details(
                ComplianceFramework(framework),
                ctrl["control_id"]
            )
            if details:
                ctrl["control_name"] = details.control_name
                ctrl["category"] = details.category
            else:
                ctrl["control_name"] = ctrl["control_id"]
                ctrl["category"] = "Unknown"
    else:
        for ctrl in controls:
            ctrl["control_name"] = ctrl["control_id"]
            ctrl["category"] = "Unknown"

    return {
        "appliance_id": appliance_id,
        "framework": framework,
        "controls": controls,
        "total_controls": len(controls),
        "generated_at": datetime.utcnow().isoformat(),
    }


@router.post("/appliances/{appliance_id}/scores/refresh")
async def refresh_compliance_scores(
    appliance_id: str,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Refresh compliance scores for an appliance.

    Re-calculates scores based on current evidence. Usually called automatically
    when new evidence is submitted.
    """
    # Get config
    config = await get_appliance_framework_config(db, appliance_id)
    if not config:
        raise HTTPException(status_code=404, detail="Appliance not found")

    # Refresh scores for each enabled framework
    for framework in config["enabled_frameworks"]:
        query = text("SELECT refresh_compliance_score(:appliance_id, :framework)")
        await db.execute(query, {
            "appliance_id": appliance_id,
            "framework": framework,
        })

    await db.commit()

    return {
        "appliance_id": appliance_id,
        "frameworks_refreshed": config["enabled_frameworks"],
        "refreshed_at": datetime.utcnow().isoformat(),
    }


# =============================================================================
# Framework Metadata Endpoints
# =============================================================================

@router.get("/metadata")
async def get_all_frameworks() -> Dict[str, Any]:
    """
    Get metadata for all supported frameworks.

    Returns framework names, versions, categories, and regulatory bodies.
    """
    frameworks = {
        "hipaa": {
            "name": "HIPAA Security Rule",
            "version": "2013 (with 2025 NPRM)",
            "description": "Health Insurance Portability and Accountability Act",
            "regulatory_body": "HHS/OCR",
            "industry": "healthcare",
            "categories": [
                "Administrative Safeguards",
                "Physical Safeguards",
                "Technical Safeguards",
                "Organizational Requirements",
            ],
        },
        "soc2": {
            "name": "SOC 2 Type II",
            "version": "2017 (Trust Services Criteria)",
            "description": "Service Organization Control 2",
            "regulatory_body": "AICPA",
            "industry": "technology",
            "categories": [
                "Security (CC)",
                "Availability (A)",
                "Processing Integrity (PI)",
                "Confidentiality (C)",
                "Privacy (P)",
            ],
        },
        "pci_dss": {
            "name": "PCI DSS",
            "version": "4.0.1",
            "description": "Payment Card Industry Data Security Standard",
            "regulatory_body": "PCI SSC",
            "industry": "retail",
            "categories": [
                "Build and Maintain Secure Network",
                "Protect Cardholder Data",
                "Maintain Vulnerability Management",
                "Implement Strong Access Control",
                "Monitor and Test Networks",
                "Maintain Security Policy",
            ],
        },
        "nist_csf": {
            "name": "NIST Cybersecurity Framework",
            "version": "2.0",
            "description": "National Institute of Standards and Technology CSF",
            "regulatory_body": "NIST",
            "industry": "general",
            "categories": [
                "Govern (GV)",
                "Identify (ID)",
                "Protect (PR)",
                "Detect (DE)",
                "Respond (RS)",
                "Recover (RC)",
            ],
        },
        "cis": {
            "name": "CIS Critical Security Controls",
            "version": "8.0",
            "description": "Center for Internet Security Controls",
            "regulatory_body": "CIS",
            "industry": "general",
            "categories": [
                "Basic Controls (IG1)",
                "Foundational Controls (IG2)",
                "Organizational Controls (IG3)",
            ],
        },
    }

    return {
        "frameworks": frameworks,
        "supported_count": len(frameworks),
    }


@router.get("/industries")
async def get_industry_recommendations() -> Dict[str, Any]:
    """
    Get recommended frameworks for each industry.

    Helps clients choose appropriate frameworks based on their business.
    """
    industries = {
        "healthcare": {
            "primary": "hipaa",
            "recommended": ["hipaa", "nist_csf"],
            "description": "Medical clinics, hospitals, health tech, covered entities",
        },
        "technology": {
            "primary": "soc2",
            "recommended": ["soc2", "nist_csf"],
            "description": "SaaS, software companies, IT service providers",
        },
        "retail": {
            "primary": "pci_dss",
            "recommended": ["pci_dss", "soc2"],
            "description": "Stores, e-commerce, payment processing",
        },
        "finance": {
            "primary": "soc2",
            "recommended": ["soc2", "pci_dss", "nist_csf"],
            "description": "RIAs, financial services, banking, fintech",
        },
        "government": {
            "primary": "nist_csf",
            "recommended": ["nist_csf", "cis"],
            "description": "Federal, state, local government agencies",
        },
        "general": {
            "primary": "nist_csf",
            "recommended": ["nist_csf", "cis"],
            "description": "General business cybersecurity baseline",
        },
    }

    return {
        "industries": industries,
    }


@router.get("/checks")
async def get_infrastructure_checks() -> Dict[str, Any]:
    """
    Get all infrastructure checks and their framework mappings.

    Shows which controls each check satisfies across all frameworks.
    """
    if FRAMEWORK_SERVICE_AVAILABLE:
        service = FrameworkService()
        checks = []

        for check in service.get_all_checks():
            mappings = {}
            for fw, controls in check.framework_controls.items():
                mappings[fw.value] = controls

            checks.append({
                "check_id": check.check_id,
                "check_name": check.check_name,
                "description": check.description,
                "check_type": check.check_type,
                "runbook_id": check.runbook_id,
                "framework_mappings": mappings,
            })

        return {
            "checks": checks,
            "total_checks": len(checks),
        }
    else:
        # Stub response when service not available
        return {
            "checks": [],
            "total_checks": 0,
            "warning": "Framework service not available",
        }


# =============================================================================
# Dashboard Endpoints
# =============================================================================

@router.get("/dashboard/overview")
async def get_compliance_dashboard(
    site_id: Optional[str] = None,
    framework: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get dashboard overview with compliance scores.

    Shows aggregated compliance status across all appliances.
    """
    # Build query
    query_parts = ["""
        SELECT
            afc.site_id,
            afc.appliance_id,
            a.hostname,
            afc.primary_framework,
            afc.enabled_frameworks,
            cs.framework,
            cs.score_percentage,
            cs.passing_controls,
            cs.total_controls,
            cs.is_compliant,
            cs.at_risk,
            cs.calculated_at
        FROM appliance_framework_configs afc
        JOIN appliances a ON afc.appliance_id = a.id
        LEFT JOIN compliance_scores cs
            ON afc.appliance_id = cs.appliance_id
            AND (cs.framework = afc.primary_framework OR cs.framework = :framework)
        WHERE 1=1
    """]
    params = {"framework": framework}

    if site_id:
        query_parts.append("AND afc.site_id = :site_id")
        params["site_id"] = site_id

    query_parts.append("ORDER BY afc.site_id, afc.appliance_id")

    query = text(" ".join(query_parts))
    result = await db.execute(query, params)
    rows = result.fetchall()

    # Aggregate stats
    total_appliances = len(set(row.appliance_id for row in rows))
    compliant = sum(1 for row in rows if row.is_compliant)
    at_risk = sum(1 for row in rows if row.at_risk)
    frameworks_in_use = set()

    appliance_scores = []
    for row in rows:
        if row.enabled_frameworks:
            frameworks_in_use.update(row.enabled_frameworks)

        appliance_scores.append({
            "appliance_id": row.appliance_id,
            "site_id": row.site_id,
            "hostname": row.hostname,
            "framework": row.framework or row.primary_framework,
            "score": float(row.score_percentage or 0),
            "passing": row.passing_controls or 0,
            "total": row.total_controls or 0,
            "is_compliant": row.is_compliant or False,
            "at_risk": row.at_risk or False,
        })

    return {
        "total_appliances": total_appliances,
        "compliant_appliances": compliant,
        "at_risk_appliances": at_risk,
        "frameworks_in_use": list(frameworks_in_use),
        "appliance_scores": appliance_scores,
        "generated_at": datetime.utcnow().isoformat(),
    }
