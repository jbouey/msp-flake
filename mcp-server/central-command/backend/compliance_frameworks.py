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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/frameworks", tags=["compliance-frameworks"])


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
"""
