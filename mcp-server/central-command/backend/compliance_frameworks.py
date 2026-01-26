"""Compliance Frameworks Management.

Central Command source of truth for compliance frameworks.
Appliances sync framework definitions from here.

Supported Frameworks:
- HIPAA (Healthcare)
- SOC 2 (Service Organizations)
- PCI-DSS (Payment Card Industry)
- NIST CSF (Cybersecurity Framework)
- NIST 800-171 (Defense Contractors)
- SOX (Financial Reporting)
- GDPR (EU Data Protection)
- CMMC (DoD Cybersecurity)
- ISO 27001 (Information Security)
- CIS Controls (Best Practices)
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from .fleet import get_pool
from .partners import require_partner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/frameworks", tags=["compliance-frameworks"])
partner_router = APIRouter(prefix="/api/partners/me", tags=["partner-compliance"])


# =============================================================================
# MODELS
# =============================================================================

class ComplianceControl(BaseModel):
    """A single compliance control/requirement."""
    control_id: str
    framework: str
    title: str
    description: str
    category: str
    check_type: str = "automated"  # automated, manual, hybrid
    severity: str = "medium"  # critical, high, medium, low
    check_script: Optional[str] = None
    remediation_runbook: Optional[str] = None
    evidence_requirements: List[str] = Field(default_factory=list)


class FrameworkDefinition(BaseModel):
    """Complete framework definition."""
    framework_id: str
    name: str
    full_name: str
    version: str
    last_updated: str
    description: str
    industries: List[str]
    controls: List[ComplianceControl] = Field(default_factory=list)
    total_controls: int = 0
    automated_controls: int = 0


class SiteComplianceConfig(BaseModel):
    """Site-level compliance configuration."""
    site_id: str
    site_name: str
    enabled_frameworks: List[str]
    coverage_tier: str = "standard"  # basic, standard, full
    industry: str = "healthcare"
    runbook_overrides: Dict[str, Any] = Field(default_factory=dict)
    check_schedule: Dict[str, str] = Field(default_factory=dict)
    alert_config: Dict[str, Any] = Field(default_factory=dict)


class UpdateSiteFrameworks(BaseModel):
    """Request to update site's enabled frameworks."""
    enabled_frameworks: List[str]
    industry: Optional[str] = None
    coverage_tier: Optional[str] = None


class PartnerComplianceDefaults(BaseModel):
    """Partner-level default compliance settings."""
    default_frameworks: List[str] = Field(default_factory=list)
    default_industry: str = "healthcare"
    default_coverage_tier: str = "standard"
    industry_presets: Dict[str, List[str]] = Field(default_factory=dict)


class UpdatePartnerDefaults(BaseModel):
    """Request to update partner's default compliance settings."""
    default_frameworks: Optional[List[str]] = None
    default_industry: Optional[str] = None
    default_coverage_tier: Optional[str] = None
    industry_presets: Optional[Dict[str, List[str]]] = None


# Industry presets - recommended frameworks by industry
INDUSTRY_PRESETS = {
    "healthcare": ["hipaa", "nist_csf"],
    "finance": ["sox", "pci_dss", "nist_csf"],
    "technology": ["soc2", "nist_csf"],
    "retail": ["pci_dss", "nist_csf"],
    "government": ["nist_800_171", "nist_csf"],
    "defense": ["cmmc", "nist_800_171"],
    "legal": ["nist_csf", "gdpr"],
    "education": ["nist_csf"],
    "manufacturing": ["nist_csf", "iso_27001"],
    "general": ["nist_csf", "cis"],
}


# =============================================================================
# FRAMEWORK DEFINITIONS
# =============================================================================

# Core framework metadata (controls loaded from database/files)
FRAMEWORK_METADATA = {
    "hipaa": {
        "name": "HIPAA",
        "full_name": "Health Insurance Portability and Accountability Act",
        "version": "2024.1",
        "description": "US healthcare data protection standard for PHI",
        "industries": ["healthcare", "health_insurance", "medical_devices", "pharmacies"],
    },
    "soc2": {
        "name": "SOC 2",
        "full_name": "Service Organization Control 2",
        "version": "2024.1",
        "description": "Trust service criteria for service organizations",
        "industries": ["technology", "saas", "cloud_services", "data_centers"],
    },
    "pci_dss": {
        "name": "PCI-DSS",
        "full_name": "Payment Card Industry Data Security Standard",
        "version": "4.0",
        "description": "Payment card data security requirements",
        "industries": ["finance", "retail", "payment_processing", "e_commerce"],
    },
    "nist_csf": {
        "name": "NIST CSF",
        "full_name": "NIST Cybersecurity Framework",
        "version": "2.0",
        "description": "General cybersecurity best practices framework",
        "industries": ["any"],
    },
    "nist_800_171": {
        "name": "NIST 800-171",
        "full_name": "NIST Special Publication 800-171",
        "version": "r3",
        "description": "Protecting controlled unclassified information (CUI)",
        "industries": ["defense", "government_contractors", "aerospace"],
    },
    "sox": {
        "name": "SOX",
        "full_name": "Sarbanes-Oxley Act",
        "version": "2024",
        "description": "Financial reporting and internal controls",
        "industries": ["finance", "public_companies", "accounting", "banking"],
    },
    "gdpr": {
        "name": "GDPR",
        "full_name": "General Data Protection Regulation",
        "version": "2024",
        "description": "EU personal data protection regulation",
        "industries": ["any_eu_data"],
    },
    "cmmc": {
        "name": "CMMC",
        "full_name": "Cybersecurity Maturity Model Certification",
        "version": "2.0",
        "description": "DoD contractor cybersecurity requirements",
        "industries": ["defense", "dod_contractors"],
    },
    "iso_27001": {
        "name": "ISO 27001",
        "full_name": "ISO/IEC 27001",
        "version": "2022",
        "description": "International information security management standard",
        "industries": ["any"],
    },
    "cis": {
        "name": "CIS Controls",
        "full_name": "Center for Internet Security Controls",
        "version": "8.0",
        "description": "Prioritized cybersecurity best practices",
        "industries": ["any"],
    },
}


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("")
async def list_frameworks(
    industry: Optional[str] = Query(None, description="Filter by industry")
) -> Dict[str, Any]:
    """List all available compliance frameworks."""
    frameworks = []

    for fw_id, meta in FRAMEWORK_METADATA.items():
        # Filter by industry if specified
        if industry and industry not in meta["industries"] and "any" not in meta["industries"]:
            continue

        frameworks.append({
            "framework_id": fw_id,
            "name": meta["name"],
            "full_name": meta["full_name"],
            "version": meta["version"],
            "description": meta["description"],
            "industries": meta["industries"],
        })

    return {
        "frameworks": frameworks,
        "count": len(frameworks),
    }


@router.get("/{framework_id}")
async def get_framework(framework_id: str) -> FrameworkDefinition:
    """Get complete framework definition with all controls.

    This endpoint is called by appliances during sync to get
    the full framework including all control definitions.
    """
    if framework_id not in FRAMEWORK_METADATA:
        raise HTTPException(status_code=404, detail=f"Framework '{framework_id}' not found")

    meta = FRAMEWORK_METADATA[framework_id]

    # Load controls from database
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                control_id, title, description, category,
                check_type, severity, check_script,
                remediation_runbook, evidence_requirements
            FROM compliance_controls
            WHERE framework_id = $1
            ORDER BY control_id
        """, framework_id)

        controls = [
            ComplianceControl(
                control_id=row["control_id"],
                framework=framework_id,
                title=row["title"],
                description=row["description"],
                category=row["category"],
                check_type=row["check_type"] or "automated",
                severity=row["severity"] or "medium",
                check_script=row["check_script"],
                remediation_runbook=row["remediation_runbook"],
                evidence_requirements=row["evidence_requirements"] or [],
            )
            for row in rows
        ]

    automated_count = sum(1 for c in controls if c.check_type == "automated")

    return FrameworkDefinition(
        framework_id=framework_id,
        name=meta["name"],
        full_name=meta["full_name"],
        version=meta["version"],
        last_updated=datetime.now(timezone.utc).isoformat(),
        description=meta["description"],
        industries=meta["industries"],
        controls=controls,
        total_controls=len(controls),
        automated_controls=automated_count,
    )


@router.get("/{framework_id}/controls")
async def list_framework_controls(
    framework_id: str,
    category: Optional[str] = None,
    check_type: Optional[str] = None,
    severity: Optional[str] = None,
) -> Dict[str, Any]:
    """List controls for a framework with optional filters."""
    if framework_id not in FRAMEWORK_METADATA:
        raise HTTPException(status_code=404, detail=f"Framework '{framework_id}' not found")

    pool = await get_pool()

    query = """
        SELECT
            control_id, title, description, category,
            check_type, severity, remediation_runbook
        FROM compliance_controls
        WHERE framework_id = $1
    """
    params = [framework_id]
    param_idx = 2

    if category:
        query += f" AND category = ${param_idx}"
        params.append(category)
        param_idx += 1

    if check_type:
        query += f" AND check_type = ${param_idx}"
        params.append(check_type)
        param_idx += 1

    if severity:
        query += f" AND severity = ${param_idx}"
        params.append(severity)
        param_idx += 1

    query += " ORDER BY control_id"

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    controls = [dict(row) for row in rows]

    return {
        "framework_id": framework_id,
        "controls": controls,
        "count": len(controls),
        "filters": {
            "category": category,
            "check_type": check_type,
            "severity": severity,
        },
    }


# =============================================================================
# SITE COMPLIANCE CONFIG
# =============================================================================

@router.get("/sites/{site_id}/compliance-config")
async def get_site_compliance_config(site_id: str) -> SiteComplianceConfig:
    """Get compliance configuration for a site.

    Called by appliances during sync to determine:
    - Which frameworks to enforce
    - Coverage tier (determines runbooks)
    - Custom schedules and overrides
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        site = await conn.fetchrow("""
            SELECT
                s.site_id, s.clinic_name, s.tier, s.industry,
                s.enabled_frameworks, s.runbook_overrides,
                s.check_schedule, s.alert_config
            FROM sites s
            WHERE s.site_id = $1
        """, site_id)

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

    # Default to HIPAA for healthcare if not specified
    enabled_frameworks = site["enabled_frameworks"]
    if not enabled_frameworks:
        industry = site["industry"] or "healthcare"
        if industry == "healthcare":
            enabled_frameworks = ["hipaa"]
        elif industry == "finance":
            enabled_frameworks = ["sox", "pci_dss"]
        else:
            enabled_frameworks = ["nist_csf"]

    return SiteComplianceConfig(
        site_id=site["site_id"],
        site_name=site["clinic_name"],
        enabled_frameworks=enabled_frameworks,
        coverage_tier=site["tier"] or "standard",
        industry=site["industry"] or "healthcare",
        runbook_overrides=site["runbook_overrides"] or {},
        check_schedule=site["check_schedule"] or {},
        alert_config=site["alert_config"] or {},
    )


@router.put("/sites/{site_id}/compliance-config")
async def update_site_compliance_config(
    site_id: str,
    update: UpdateSiteFrameworks,
) -> Dict[str, Any]:
    """Update compliance configuration for a site.

    Allows configuring which frameworks apply to a site.
    """
    # Validate frameworks
    invalid = [f for f in update.enabled_frameworks if f not in FRAMEWORK_METADATA]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid frameworks: {invalid}. Valid: {list(FRAMEWORK_METADATA.keys())}"
        )

    pool = await get_pool()

    async with pool.acquire() as conn:
        # Verify site exists
        exists = await conn.fetchval(
            "SELECT 1 FROM sites WHERE site_id = $1",
            site_id
        )
        if not exists:
            raise HTTPException(status_code=404, detail="Site not found")

        # Update configuration
        await conn.execute("""
            UPDATE sites SET
                enabled_frameworks = $2,
                industry = COALESCE($3, industry),
                tier = COALESCE($4, tier),
                updated_at = NOW()
            WHERE site_id = $1
        """,
            site_id,
            update.enabled_frameworks,
            update.industry,
            update.coverage_tier,
        )

    logger.info(f"Updated site {site_id} frameworks: {update.enabled_frameworks}")

    return {
        "status": "updated",
        "site_id": site_id,
        "enabled_frameworks": update.enabled_frameworks,
    }


# =============================================================================
# PARTNER COMPLIANCE MANAGEMENT
# =============================================================================

@partner_router.get("/compliance/defaults")
async def get_partner_compliance_defaults(
    partner=Depends(require_partner)
) -> Dict[str, Any]:
    """Get partner's default compliance settings.

    Partners can set defaults that apply to all new sites.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                default_frameworks, default_industry,
                default_coverage_tier, industry_presets
            FROM partners
            WHERE id = $1
        """, partner['id'])

    defaults = PartnerComplianceDefaults(
        default_frameworks=row["default_frameworks"] or ["hipaa"],
        default_industry=row["default_industry"] or "healthcare",
        default_coverage_tier=row["default_coverage_tier"] or "standard",
        industry_presets=row["industry_presets"] or INDUSTRY_PRESETS,
    )

    return {
        "defaults": defaults.dict(),
        "available_frameworks": [
            {
                "id": fw_id,
                "name": meta["name"],
                "description": meta["description"],
                "industries": meta["industries"],
            }
            for fw_id, meta in FRAMEWORK_METADATA.items()
        ],
        "industry_presets": INDUSTRY_PRESETS,
    }


@partner_router.put("/compliance/defaults")
async def update_partner_compliance_defaults(
    update: UpdatePartnerDefaults,
    partner=Depends(require_partner)
) -> Dict[str, Any]:
    """Update partner's default compliance settings."""
    pool = await get_pool()

    # Validate frameworks if provided
    if update.default_frameworks:
        invalid = [f for f in update.default_frameworks if f not in FRAMEWORK_METADATA]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid frameworks: {invalid}"
            )

    # Validate industry presets if provided
    if update.industry_presets:
        for industry, frameworks in update.industry_presets.items():
            invalid = [f for f in frameworks if f not in FRAMEWORK_METADATA]
            if invalid:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid frameworks in preset '{industry}': {invalid}"
                )

    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE partners SET
                default_frameworks = COALESCE($2, default_frameworks),
                default_industry = COALESCE($3, default_industry),
                default_coverage_tier = COALESCE($4, default_coverage_tier),
                industry_presets = COALESCE($5, industry_presets),
                updated_at = NOW()
            WHERE id = $1
        """,
            partner['id'],
            update.default_frameworks,
            update.default_industry,
            update.default_coverage_tier,
            json.dumps(update.industry_presets) if update.industry_presets else None,
        )

    logger.info(f"Partner {partner['id']} updated compliance defaults")

    return {
        "status": "updated",
        "message": "Default compliance settings updated",
    }


@partner_router.get("/sites/{site_id}/compliance")
async def get_partner_site_compliance(
    site_id: str,
    partner=Depends(require_partner)
) -> Dict[str, Any]:
    """Get compliance configuration for a partner's site."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        site = await conn.fetchrow("""
            SELECT
                s.site_id, s.clinic_name, s.tier, s.industry,
                s.enabled_frameworks, s.runbook_overrides,
                s.check_schedule, s.alert_config, s.status
            FROM sites s
            WHERE s.site_id = $1 AND s.partner_id = $2
        """, site_id, partner['id'])

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Get partner defaults for comparison
        partner_row = await conn.fetchrow("""
            SELECT default_frameworks, default_industry, industry_presets
            FROM partners WHERE id = $1
        """, partner['id'])

    # Determine effective frameworks
    enabled = site["enabled_frameworks"]
    using_defaults = False
    if not enabled:
        # Use industry preset or partner default
        industry = site["industry"] or partner_row["default_industry"] or "healthcare"
        presets = partner_row["industry_presets"] or INDUSTRY_PRESETS
        enabled = presets.get(industry, INDUSTRY_PRESETS.get(industry, ["nist_csf"]))
        using_defaults = True

    return {
        "site_id": site["site_id"],
        "site_name": site["clinic_name"],
        "status": site["status"],
        "compliance": {
            "enabled_frameworks": enabled,
            "using_defaults": using_defaults,
            "industry": site["industry"],
            "coverage_tier": site["tier"] or "standard",
            "runbook_overrides": site["runbook_overrides"] or {},
            "check_schedule": site["check_schedule"] or {},
        },
        "available_frameworks": [
            {
                "id": fw_id,
                "name": meta["name"],
                "description": meta["description"],
                "enabled": fw_id in enabled,
            }
            for fw_id, meta in FRAMEWORK_METADATA.items()
        ],
    }


@partner_router.put("/sites/{site_id}/compliance")
async def update_partner_site_compliance(
    site_id: str,
    update: UpdateSiteFrameworks,
    partner=Depends(require_partner)
) -> Dict[str, Any]:
    """Update compliance configuration for a partner's site.

    Partners can enable/disable frameworks for their sites.
    """
    # Validate frameworks
    invalid = [f for f in update.enabled_frameworks if f not in FRAMEWORK_METADATA]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid frameworks: {invalid}. Valid: {list(FRAMEWORK_METADATA.keys())}"
        )

    pool = await get_pool()

    async with pool.acquire() as conn:
        # Verify site belongs to partner
        site = await conn.fetchrow("""
            SELECT site_id, clinic_name FROM sites
            WHERE site_id = $1 AND partner_id = $2
        """, site_id, partner['id'])

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Update configuration
        await conn.execute("""
            UPDATE sites SET
                enabled_frameworks = $2,
                industry = COALESCE($3, industry),
                tier = COALESCE($4, tier),
                updated_at = NOW()
            WHERE site_id = $1
        """,
            site_id,
            update.enabled_frameworks,
            update.industry,
            update.coverage_tier,
        )

    logger.info(f"Partner {partner['id']} updated site {site_id} frameworks: {update.enabled_frameworks}")

    return {
        "status": "updated",
        "site_id": site_id,
        "site_name": site["clinic_name"],
        "enabled_frameworks": update.enabled_frameworks,
        "industry": update.industry,
        "coverage_tier": update.coverage_tier,
    }


@partner_router.get("/sites/compliance/summary")
async def get_partner_sites_compliance_summary(
    partner=Depends(require_partner)
) -> Dict[str, Any]:
    """Get compliance summary for all partner sites.

    Shows which frameworks are enabled across the partner's portfolio.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Get all sites with their frameworks
        sites = await conn.fetch("""
            SELECT
                s.site_id, s.clinic_name, s.industry, s.tier,
                s.enabled_frameworks, s.status
            FROM sites s
            WHERE s.partner_id = $1
            ORDER BY s.clinic_name
        """, partner['id'])

        # Get partner defaults
        partner_row = await conn.fetchrow("""
            SELECT default_frameworks, default_industry, industry_presets
            FROM partners WHERE id = $1
        """, partner['id'])

    # Calculate summary
    framework_counts = {}
    industry_counts = {}
    sites_data = []

    presets = partner_row["industry_presets"] or INDUSTRY_PRESETS

    for site in sites:
        industry = site["industry"] or partner_row["default_industry"] or "healthcare"
        frameworks = site["enabled_frameworks"]

        if not frameworks:
            frameworks = presets.get(industry, INDUSTRY_PRESETS.get(industry, ["nist_csf"]))

        # Count frameworks
        for fw in frameworks:
            framework_counts[fw] = framework_counts.get(fw, 0) + 1

        # Count industries
        industry_counts[industry] = industry_counts.get(industry, 0) + 1

        sites_data.append({
            "site_id": site["site_id"],
            "site_name": site["clinic_name"],
            "industry": industry,
            "tier": site["tier"] or "standard",
            "frameworks": frameworks,
            "status": site["status"],
        })

    return {
        "total_sites": len(sites),
        "framework_distribution": framework_counts,
        "industry_distribution": industry_counts,
        "sites": sites_data,
    }


@partner_router.post("/sites/{site_id}/compliance/apply-preset")
async def apply_industry_preset(
    site_id: str,
    industry: str,
    partner=Depends(require_partner)
) -> Dict[str, Any]:
    """Apply an industry preset to a site.

    Quick way to configure a site with recommended frameworks for an industry.
    """
    pool = await get_pool()

    # Get partner's presets (or use defaults)
    async with pool.acquire() as conn:
        partner_row = await conn.fetchrow("""
            SELECT industry_presets FROM partners WHERE id = $1
        """, partner['id'])

        presets = partner_row["industry_presets"] or INDUSTRY_PRESETS

        if industry not in presets and industry not in INDUSTRY_PRESETS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown industry: {industry}. Available: {list(presets.keys())}"
            )

        frameworks = presets.get(industry, INDUSTRY_PRESETS.get(industry))

        # Verify site belongs to partner
        site = await conn.fetchrow("""
            SELECT site_id, clinic_name FROM sites
            WHERE site_id = $1 AND partner_id = $2
        """, site_id, partner['id'])

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Apply preset
        await conn.execute("""
            UPDATE sites SET
                enabled_frameworks = $2,
                industry = $3,
                updated_at = NOW()
            WHERE site_id = $1
        """, site_id, frameworks, industry)

    logger.info(f"Applied {industry} preset to site {site_id}: {frameworks}")

    return {
        "status": "applied",
        "site_id": site_id,
        "site_name": site["clinic_name"],
        "industry": industry,
        "enabled_frameworks": frameworks,
    }


# =============================================================================
# DATABASE MIGRATION
# =============================================================================

FRAMEWORKS_MIGRATION = """
-- Compliance controls table
CREATE TABLE IF NOT EXISTS compliance_controls (
    id SERIAL PRIMARY KEY,
    framework_id TEXT NOT NULL,
    control_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    category TEXT,
    check_type TEXT DEFAULT 'automated',
    severity TEXT DEFAULT 'medium',
    check_script TEXT,
    remediation_runbook TEXT,
    evidence_requirements TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(framework_id, control_id)
);

CREATE INDEX IF NOT EXISTS idx_controls_framework
    ON compliance_controls(framework_id);
CREATE INDEX IF NOT EXISTS idx_controls_category
    ON compliance_controls(framework_id, category);
CREATE INDEX IF NOT EXISTS idx_controls_severity
    ON compliance_controls(severity);

-- Add framework columns to sites
ALTER TABLE sites ADD COLUMN IF NOT EXISTS enabled_frameworks TEXT[];
ALTER TABLE sites ADD COLUMN IF NOT EXISTS industry TEXT;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS runbook_overrides JSONB DEFAULT '{}';
ALTER TABLE sites ADD COLUMN IF NOT EXISTS check_schedule JSONB DEFAULT '{}';
ALTER TABLE sites ADD COLUMN IF NOT EXISTS alert_config JSONB DEFAULT '{}';

-- Control-runbook mapping
CREATE TABLE IF NOT EXISTS control_runbook_mapping (
    id SERIAL PRIMARY KEY,
    framework_id TEXT NOT NULL,
    control_id TEXT NOT NULL,
    runbook_id TEXT NOT NULL,
    is_primary BOOLEAN DEFAULT TRUE,
    UNIQUE(framework_id, control_id, runbook_id)
);

-- Partner compliance defaults
ALTER TABLE partners ADD COLUMN IF NOT EXISTS default_frameworks TEXT[];
ALTER TABLE partners ADD COLUMN IF NOT EXISTS default_industry TEXT DEFAULT 'healthcare';
ALTER TABLE partners ADD COLUMN IF NOT EXISTS default_coverage_tier TEXT DEFAULT 'standard';
ALTER TABLE partners ADD COLUMN IF NOT EXISTS industry_presets JSONB DEFAULT '{}';
"""
