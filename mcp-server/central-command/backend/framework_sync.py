"""Framework Sync Engine — Live compliance framework control catalog.

Syncs control definitions from official sources (NIST OSCAL, etc.),
seeds manual frameworks from control_mappings.yaml, calculates coverage
gaps, and provides API endpoints for the Compliance Library dashboard.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

import httpx
import yaml
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from .fleet import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/framework-sync", tags=["framework-sync"])

# OSCAL catalog URLs (NIST-hosted, machine-readable JSON)
OSCAL_CATALOG_URLS = {
    "nist_800_53": "https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json",
}

# Path to control_mappings.yaml for seeding manual frameworks
YAML_PATH = Path(__file__).parent / ".." / ".." / ".." / "packages" / "compliance-agent" / "src" / "compliance_agent" / "frameworks" / "mappings" / "control_mappings.yaml"


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/status")
async def get_sync_status():
    """All frameworks with sync status and coverage stats."""
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT framework, display_name, current_version, source_type, source_url,
               last_sync_at, last_sync_status, total_controls, our_coverage, coverage_pct,
               enabled
        FROM framework_versions
        ORDER BY display_name
    """)
    return [
        {
            "framework": r["framework"],
            "display_name": r["display_name"],
            "version": r["current_version"],
            "source_type": r["source_type"],
            "source_url": r["source_url"],
            "last_sync": r["last_sync_at"].isoformat() if r["last_sync_at"] else None,
            "sync_status": r["last_sync_status"],
            "total_controls": r["total_controls"],
            "our_coverage": r["our_coverage"],
            "coverage_pct": float(r["coverage_pct"]) if r["coverage_pct"] else 0,
            "enabled": r["enabled"],
        }
        for r in rows
    ]


@router.get("/controls/{framework}")
async def get_framework_controls(
    framework: str,
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """List controls for a framework with optional filters."""
    pool = await get_pool()

    conditions = ["fc.framework = $1"]
    params: list = [framework]
    idx = 2

    if category:
        conditions.append(f"fc.category = ${idx}")
        params.append(category)
        idx += 1

    if search:
        conditions.append(f"(fc.control_id ILIKE ${idx} OR fc.control_name ILIKE ${idx})")
        params.append(f"%{search}%")
        idx += 1

    where = " AND ".join(conditions)

    rows = await pool.fetch(f"""
        SELECT fc.control_id, fc.control_name, fc.description, fc.category,
               fc.subcategory, fc.parent_control_id, fc.severity, fc.required,
               ccm.check_id, ccm.mapping_source
        FROM framework_controls fc
        LEFT JOIN check_control_mappings ccm
            ON ccm.framework = fc.framework AND ccm.control_id = fc.control_id
        WHERE {where}
        ORDER BY fc.category, fc.control_id
        LIMIT ${idx} OFFSET ${idx + 1}
    """, *params, limit, offset)

    return [
        {
            "control_id": r["control_id"],
            "control_name": r["control_name"],
            "description": (r["description"] or "")[:500],
            "category": r["category"],
            "subcategory": r["subcategory"],
            "parent_control_id": r["parent_control_id"],
            "severity": r["severity"],
            "required": r["required"],
            "mapped_check": r["check_id"],
            "mapping_source": r["mapping_source"],
        }
        for r in rows
    ]


@router.get("/crosswalks/{framework}")
async def get_crosswalks(framework: str):
    """Cross-mappings from one framework to others."""
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT source_control_id, target_framework, target_control_id,
               mapping_type, source_reference
        FROM framework_crosswalks
        WHERE source_framework = $1
        ORDER BY source_control_id, target_framework
    """, framework)

    return [
        {
            "source_control_id": r["source_control_id"],
            "target_framework": r["target_framework"],
            "target_control_id": r["target_control_id"],
            "mapping_type": r["mapping_type"],
            "source_reference": r["source_reference"],
        }
        for r in rows
    ]


@router.get("/coverage")
async def get_coverage_analysis():
    """Coverage analysis — which controls have checks, which don't."""
    pool = await get_pool()

    # Framework summaries
    frameworks = await pool.fetch("""
        SELECT framework, display_name, total_controls, our_coverage, coverage_pct
        FROM framework_versions
        WHERE enabled = true
        ORDER BY display_name
    """)

    # Check-to-framework matrix
    mappings = await pool.fetch("""
        SELECT check_id, framework, control_id
        FROM check_control_mappings
        ORDER BY check_id, framework
    """)

    # Build matrix
    matrix: Dict[str, Dict[str, List[str]]] = {}
    for m in mappings:
        if m["check_id"] not in matrix:
            matrix[m["check_id"]] = {}
        if m["framework"] not in matrix[m["check_id"]]:
            matrix[m["check_id"]][m["framework"]] = []
        matrix[m["check_id"]][m["framework"]].append(m["control_id"])

    # Gap analysis per framework
    gaps = {}
    for fw in frameworks:
        unmapped = await pool.fetchval("""
            SELECT COUNT(*) FROM framework_controls fc
            WHERE fc.framework = $1
            AND NOT EXISTS (
                SELECT 1 FROM check_control_mappings ccm
                WHERE ccm.framework = fc.framework AND ccm.control_id = fc.control_id
            )
        """, fw["framework"])
        gaps[fw["framework"]] = unmapped

    return {
        "frameworks": [
            {
                "framework": f["framework"],
                "display_name": f["display_name"],
                "total_controls": f["total_controls"],
                "our_coverage": f["our_coverage"],
                "coverage_pct": float(f["coverage_pct"]) if f["coverage_pct"] else 0,
                "unmapped_controls": gaps.get(f["framework"], 0),
            }
            for f in frameworks
        ],
        "check_matrix": matrix,
    }


@router.post("/sync")
async def trigger_sync_all(background_tasks: BackgroundTasks):
    """Trigger sync for all enabled OSCAL frameworks."""
    background_tasks.add_task(_run_full_sync)
    return {"status": "sync_started", "frameworks": list(OSCAL_CATALOG_URLS.keys())}


@router.post("/sync/{framework}")
async def trigger_sync_one(framework: str, background_tasks: BackgroundTasks):
    """Trigger sync for a single framework."""
    if framework not in OSCAL_CATALOG_URLS:
        # For manual frameworks, re-seed from YAML
        background_tasks.add_task(_seed_framework_from_yaml, framework)
        return {"status": "seed_started", "framework": framework, "source": "yaml"}

    background_tasks.add_task(_run_framework_sync, framework)
    return {"status": "sync_started", "framework": framework, "source": "oscal"}


@router.get("/categories/{framework}")
async def get_categories(framework: str):
    """Get distinct categories for a framework."""
    pool = await get_pool()
    rows = await pool.fetch("""
        SELECT DISTINCT category, COUNT(*) as control_count
        FROM framework_controls
        WHERE framework = $1
        GROUP BY category
        ORDER BY category
    """, framework)
    return [{"category": r["category"], "count": r["control_count"]} for r in rows]


# =============================================================================
# Sync Logic
# =============================================================================

async def _run_full_sync():
    """Run sync for all OSCAL frameworks + seed manual ones + calculate coverage."""
    try:
        pool = await get_pool()

        # Seed from YAML first (populates manual frameworks + check mappings)
        await _seed_from_yaml(pool)

        # Sync OSCAL frameworks
        for fw, url in OSCAL_CATALOG_URLS.items():
            try:
                count = await _sync_oscal_framework(pool, fw, url)
                logger.info(f"OSCAL sync {fw}: {count} controls")
            except Exception as e:
                logger.error(f"OSCAL sync {fw} failed: {e}")
                await pool.execute("""
                    UPDATE framework_versions
                    SET last_sync_status = 'failed', updated_at = NOW()
                    WHERE framework = $1
                """, fw)

        # Calculate coverage
        await _calculate_coverage(pool)
        logger.info("Framework sync complete")
    except Exception as e:
        logger.error(f"Framework sync failed: {e}")


async def _run_framework_sync(framework: str):
    """Sync a single OSCAL framework."""
    try:
        pool = await get_pool()
        url = OSCAL_CATALOG_URLS.get(framework)
        if url:
            count = await _sync_oscal_framework(pool, framework, url)
            logger.info(f"OSCAL sync {framework}: {count} controls")
        await _calculate_coverage(pool)
    except Exception as e:
        logger.error(f"Framework sync {framework} failed: {e}")


async def _sync_oscal_framework(pool, framework: str, url: str) -> int:
    """Sync framework controls from NIST OSCAL JSON catalog."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        catalog = resp.json()

    controls = []
    _parse_oscal_groups(catalog.get("catalog", {}), controls)

    for ctrl in controls:
        await pool.execute("""
            INSERT INTO framework_controls (
                framework, control_id, control_name, description, category,
                subcategory, parent_control_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (framework, control_id) DO UPDATE SET
                control_name = EXCLUDED.control_name,
                description = EXCLUDED.description,
                category = EXCLUDED.category,
                updated_at = NOW()
        """, framework, ctrl["control_id"], ctrl["control_name"],
             ctrl["description"], ctrl["category"], ctrl.get("subcategory"),
             ctrl.get("parent_control_id"))

    await pool.execute("""
        UPDATE framework_versions
        SET last_sync_at = NOW(), last_sync_status = 'success',
            total_controls = $1, updated_at = NOW()
        WHERE framework = $2
    """, len(controls), framework)

    return len(controls)


def _parse_oscal_groups(catalog: dict, controls: list, parent_category: str = ""):
    """Recursively parse OSCAL catalog groups and controls."""
    for group in catalog.get("groups", []):
        category = group.get("title", parent_category)

        for control in group.get("controls", []):
            ctrl_id = control.get("id", "").upper()
            controls.append({
                "control_id": ctrl_id,
                "control_name": control.get("title", ""),
                "description": _extract_oscal_prose(control),
                "category": category,
                "parent_control_id": None,
            })

            # Sub-controls (enhancements)
            for sub in control.get("controls", []):
                sub_id = sub.get("id", "").upper()
                controls.append({
                    "control_id": sub_id,
                    "control_name": sub.get("title", ""),
                    "description": _extract_oscal_prose(sub),
                    "category": category,
                    "parent_control_id": ctrl_id,
                })

        # Nested groups
        _parse_oscal_groups(group, controls, category)


def _extract_oscal_prose(control: dict) -> str:
    """Extract prose text from OSCAL control parts."""
    parts = []
    for part in control.get("parts", []):
        if part.get("name") == "statement":
            prose = part.get("prose", "")
            if prose:
                parts.append(prose)
            # Nested parts
            for sub_part in part.get("parts", []):
                sub_prose = sub_part.get("prose", "")
                if sub_prose:
                    parts.append(sub_prose)
    return " ".join(parts)[:2000] if parts else ""


# =============================================================================
# YAML Seed Logic
# =============================================================================

async def _seed_from_yaml(pool):
    """Seed framework_controls and check_control_mappings from control_mappings.yaml."""
    # Check multiple possible YAML locations
    yaml_path = _find_yaml_path()
    if not yaml_path:
        logger.warning("control_mappings.yaml not found, skipping YAML seed")
        return

    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    checks = data.get("checks", {})
    frameworks_meta = data.get("frameworks", {})

    # Seed framework controls from the YAML mappings
    for check_id, check_data in checks.items():
        mappings = check_data.get("framework_mappings", {})

        for fw_key, controls in mappings.items():
            for ctrl in controls:
                ctrl_id = ctrl.get("control_id", "")
                if not ctrl_id:
                    continue

                # Insert/update the control definition
                await pool.execute("""
                    INSERT INTO framework_controls (
                        framework, control_id, control_name, category, subcategory, required
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (framework, control_id) DO UPDATE SET
                        control_name = COALESCE(EXCLUDED.control_name, framework_controls.control_name),
                        category = COALESCE(EXCLUDED.category, framework_controls.category),
                        updated_at = NOW()
                """,
                    fw_key,
                    ctrl_id,
                    ctrl.get("control_name", ""),
                    ctrl.get("category", ""),
                    ctrl.get("subcategory"),
                    ctrl.get("required", True),
                )

                # Insert check-to-control mapping
                await pool.execute("""
                    INSERT INTO check_control_mappings (check_id, framework, control_id, mapping_source)
                    VALUES ($1, $2, $3, 'yaml')
                    ON CONFLICT (check_id, framework, control_id) DO NOTHING
                """, check_id, fw_key, ctrl_id)

    # Update total_controls for manually-seeded frameworks
    for fw_key in set(
        fw for check_data in checks.values()
        for fw in check_data.get("framework_mappings", {})
    ):
        total = await pool.fetchval(
            "SELECT COUNT(*) FROM framework_controls WHERE framework = $1", fw_key
        )
        await pool.execute("""
            UPDATE framework_versions
            SET total_controls = $1, last_sync_at = NOW(), last_sync_status = 'seeded', updated_at = NOW()
            WHERE framework = $2
        """, total, fw_key)

    logger.info(f"Seeded {len(checks)} checks across {len(set(fw for c in checks.values() for fw in c.get('framework_mappings', {})))} frameworks from YAML")


async def _seed_framework_from_yaml(framework: str):
    """Re-seed a single framework from YAML."""
    try:
        pool = await get_pool()
        await _seed_from_yaml(pool)
        await _calculate_coverage(pool)
    except Exception as e:
        logger.error(f"YAML seed for {framework} failed: {e}")


def _find_yaml_path() -> Optional[Path]:
    """Find control_mappings.yaml in various locations."""
    candidates = [
        YAML_PATH,
        Path("/opt/mcp-server/packages/compliance-agent/src/compliance_agent/frameworks/mappings/control_mappings.yaml"),
        Path(__file__).parent / "control_mappings.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


# =============================================================================
# Coverage Analysis
# =============================================================================

async def _calculate_coverage(pool):
    """Calculate coverage stats for each framework."""
    frameworks = await pool.fetch("SELECT framework, total_controls FROM framework_versions")

    for fw in frameworks:
        mapped = await pool.fetchval("""
            SELECT COUNT(DISTINCT control_id) FROM check_control_mappings
            WHERE framework = $1
        """, fw["framework"])

        total = fw["total_controls"] or 0
        pct = round((mapped / total * 100), 2) if total > 0 else 0

        await pool.execute("""
            UPDATE framework_versions
            SET our_coverage = $1, coverage_pct = $2, updated_at = NOW()
            WHERE framework = $3
        """, mapped, pct, fw["framework"])


# =============================================================================
# Background Sync Loop
# =============================================================================

async def framework_sync_loop():
    """Weekly framework sync background task."""
    # Initial sync on startup (after 5 min to let DB warm up)
    await asyncio.sleep(300)

    while True:
        try:
            await _run_full_sync()
        except Exception as e:
            logger.error(f"Framework sync loop error: {e}")

        # Weekly re-sync
        await asyncio.sleep(7 * 24 * 3600)
