"""Runbook Configuration API for Central Command Dashboard.

Endpoints for managing runbook enable/disable settings at site and appliance levels.
Partners can customize which runbooks are active for their managed sites.
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Query, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from pydantic import BaseModel

try:
    from auth import require_auth
except ImportError:
    from .auth import require_auth


router = APIRouter(prefix="/api/runbooks", tags=["runbooks"])


# =============================================================================
# Pydantic Models
# =============================================================================

class RunbookInfo(BaseModel):
    """Runbook catalog entry."""
    id: str
    name: str
    description: Optional[str] = None
    category: str
    check_type: str
    severity: str = "medium"
    level: str = "L1"  # Resolution level for frontend compatibility
    is_disruptive: bool = False
    requires_maintenance_window: bool = False
    hipaa_controls: List[str] = []
    version: str = "1.0"
    execution_count: int = 0
    success_rate: float = 0.0
    avg_execution_time_ms: int = 0


class RunbookConfigStatus(BaseModel):
    """Runbook enable/disable status for a site or appliance."""
    runbook_id: str
    enabled: bool
    modified_by: Optional[str] = None
    modified_at: Optional[datetime] = None
    notes: Optional[str] = None


class SiteRunbookConfigItem(BaseModel):
    """Runbook config with full details for frontend display."""
    runbook_id: str
    name: str
    description: Optional[str] = None
    category: str
    severity: str = "medium"
    is_disruptive: bool = False
    enabled: bool
    modified_by: Optional[str] = None
    modified_at: Optional[datetime] = None


class RunbookConfigUpdate(BaseModel):
    """Request to update runbook configuration."""
    enabled: bool
    notes: Optional[str] = None


class SiteRunbookConfig(BaseModel):
    """Complete runbook configuration for a site."""
    site_id: str
    runbooks: List[RunbookConfigStatus]


class EnabledRunbooksResponse(BaseModel):
    """List of enabled runbook IDs for an appliance."""
    appliance_id: str
    enabled_runbooks: List[str]
    source: str = "site"  # site, appliance, or default


# =============================================================================
# Database Dependency
# =============================================================================

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
# Runbook Catalog Endpoints
# =============================================================================

@router.get("", response_model=List[RunbookInfo])
async def list_all_runbooks(
    category: Optional[str] = Query(None, description="Filter by category"),
    db: AsyncSession = Depends(get_db)
):
    """List all available runbooks from the catalog with execution statistics."""
    # Build base query with execution stats from telemetry via mapping table
    base_query = """
        WITH telemetry_stats AS (
            SELECT
                COALESCE(m.runbook_id, et.runbook_id) as runbook_id,
                COUNT(*) as exec_count,
                COUNT(*) FILTER (WHERE et.success = true) as success_count,
                AVG(et.duration_seconds * 1000) FILTER (WHERE et.duration_seconds IS NOT NULL) as avg_time_ms
            FROM execution_telemetry et
            LEFT JOIN runbook_id_mapping m ON m.l1_rule_id = et.runbook_id
            GROUP BY COALESCE(m.runbook_id, et.runbook_id)
        )
        SELECT
            r.runbook_id, r.name, r.description, r.category, r.check_type, r.severity,
            r.is_disruptive, r.requires_maintenance_window, r.hipaa_controls, r.version,
            COALESCE(ts.exec_count, 0) as execution_count,
            COALESCE(ts.success_count, 0) as success_count,
            COALESCE(ts.avg_time_ms, 0) as avg_time_ms
        FROM runbooks r
        LEFT JOIN telemetry_stats ts ON ts.runbook_id = r.runbook_id
    """

    if category:
        query = text(base_query + " WHERE r.category = :category ORDER BY r.runbook_id")
        result = await db.execute(query, {"category": category})
    else:
        query = text(base_query + " ORDER BY r.category, r.runbook_id")
        result = await db.execute(query)

    rows = result.fetchall()
    return [
        RunbookInfo(
            id=row.runbook_id,
            name=row.name,
            description=row.description,
            category=row.category,
            check_type=row.check_type,
            severity=row.severity,
            is_disruptive=row.is_disruptive,
            requires_maintenance_window=row.requires_maintenance_window or False,
            hipaa_controls=row.hipaa_controls or [],
            version=row.version or "1.0",
            execution_count=row.execution_count or 0,
            success_rate=round((row.success_count / row.execution_count * 100), 1) if row.execution_count > 0 else 0.0,
            avg_execution_time_ms=int(row.avg_time_ms or 0)
        )
        for row in rows
    ]


@router.get("/categories")
async def list_runbook_categories(db: AsyncSession = Depends(get_db)):
    """List runbook categories with counts."""
    query = text("""
        SELECT category, COUNT(*) as count
        FROM runbooks
        GROUP BY category
        ORDER BY category
    """)
    result = await db.execute(query)
    rows = result.fetchall()

    return [{"category": row.category, "count": row.count} for row in rows]


@router.get("/{runbook_id}", response_model=RunbookInfo)
async def get_runbook_detail(runbook_id: str, db: AsyncSession = Depends(get_db)):
    """Get details for a specific runbook."""
    query = text("""
        SELECT runbook_id, name, description, category, check_type, severity,
               is_disruptive, requires_maintenance_window, hipaa_controls, version
        FROM runbooks
        WHERE runbook_id = :runbook_id
    """)
    result = await db.execute(query, {"runbook_id": runbook_id})
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Runbook {runbook_id} not found")

    return RunbookInfo(
        id=row.runbook_id,
        name=row.name,
        description=row.description,
        category=row.category,
        check_type=row.check_type,
        severity=row.severity,
        is_disruptive=row.is_disruptive,
        requires_maintenance_window=row.requires_maintenance_window or False,
        hipaa_controls=row.hipaa_controls or [],
        version=row.version or "1.0"
    )


# =============================================================================
# Site-Level Configuration Endpoints
# =============================================================================

@router.get("/sites/{site_id}", response_model=List[SiteRunbookConfigItem])
async def get_site_runbook_config(site_id: str, db: AsyncSession = Depends(get_db)):
    """Get runbook configuration for a site.

    Returns all runbooks with their enabled/disabled status and full details.
    Runbooks not in site_runbook_config are enabled by default.
    """
    # Get all runbooks with site-specific overrides and full details
    query = text("""
        SELECT
            r.runbook_id,
            r.name,
            r.description,
            r.category,
            r.severity,
            r.is_disruptive,
            COALESCE(src.enabled, true) as enabled,
            src.modified_by,
            src.modified_at
        FROM runbooks r
        LEFT JOIN site_runbook_config src ON src.runbook_id = r.runbook_id AND src.site_id = :site_id
        ORDER BY r.category, r.runbook_id
    """)
    result = await db.execute(query, {"site_id": site_id})
    rows = result.fetchall()

    return [
        SiteRunbookConfigItem(
            runbook_id=row.runbook_id,
            name=row.name,
            description=row.description,
            category=row.category,
            severity=row.severity or "medium",
            is_disruptive=row.is_disruptive or False,
            enabled=row.enabled,
            modified_by=row.modified_by,
            modified_at=row.modified_at
        )
        for row in rows
    ]


@router.put("/sites/{site_id}/{runbook_id}")
async def update_site_runbook_config(
    site_id: str,
    runbook_id: str,
    config: RunbookConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: Dict[str, Any] = Depends(require_auth)
):
    """Enable or disable a runbook for a site."""
    # Verify runbook exists
    check_query = text("SELECT runbook_id FROM runbooks WHERE runbook_id = :runbook_id")
    result = await db.execute(check_query, {"runbook_id": runbook_id})
    if not result.fetchone():
        raise HTTPException(status_code=404, detail=f"Runbook {runbook_id} not found")

    # Upsert configuration
    query = text("""
        INSERT INTO site_runbook_config (site_id, runbook_id, enabled, modified_by, modified_at, notes)
        VALUES (:site_id, :runbook_id, :enabled, :modified_by, NOW(), :notes)
        ON CONFLICT (site_id, runbook_id) DO UPDATE SET
            enabled = EXCLUDED.enabled,
            modified_by = EXCLUDED.modified_by,
            modified_at = NOW(),
            notes = COALESCE(EXCLUDED.notes, site_runbook_config.notes)
        RETURNING enabled, modified_at
    """)

    result = await db.execute(query, {
        "site_id": site_id,
        "runbook_id": runbook_id,
        "enabled": config.enabled,
        "modified_by": user.get("username", "api"),
        "notes": config.notes
    })
    await db.commit()
    row = result.fetchone()

    return {
        "site_id": site_id,
        "runbook_id": runbook_id,
        "enabled": row.enabled,
        "modified_at": row.modified_at.isoformat() if row.modified_at else None
    }


@router.post("/sites/{site_id}/bulk")
async def bulk_update_site_runbooks(
    site_id: str,
    updates: dict,  # {"runbook_id": enabled_bool, ...}
    db: AsyncSession = Depends(get_db),
    user: Dict[str, Any] = Depends(require_auth)
):
    """Bulk enable/disable runbooks for a site.

    Body: {"RB-WIN-SVC-001": true, "RB-WIN-SEC-002": false, ...}
    """
    results = []
    modified_by = user.get("username", "api")

    for runbook_id, enabled in updates.items():
        query = text("""
            INSERT INTO site_runbook_config (site_id, runbook_id, enabled, modified_by, modified_at)
            VALUES (:site_id, :runbook_id, :enabled, :modified_by, NOW())
            ON CONFLICT (site_id, runbook_id) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                modified_by = :modified_by,
                modified_at = NOW()
        """)
        await db.execute(query, {
            "site_id": site_id,
            "runbook_id": runbook_id,
            "enabled": enabled,
            "modified_by": modified_by
        })
        results.append({"runbook_id": runbook_id, "enabled": enabled})

    await db.commit()
    return {"site_id": site_id, "updated": len(results), "results": results}


@router.post("/sites/{site_id}/category/{category}")
async def toggle_category_for_site(
    site_id: str,
    category: str,
    enabled: bool = Query(..., description="Enable or disable all runbooks in category"),
    db: AsyncSession = Depends(get_db),
    user: Dict[str, Any] = Depends(require_auth)
):
    """Enable or disable all runbooks in a category for a site."""
    # Get runbooks in category
    query = text("SELECT runbook_id FROM runbooks WHERE category = :category")
    result = await db.execute(query, {"category": category})
    runbook_ids = [row.runbook_id for row in result.fetchall()]

    if not runbook_ids:
        raise HTTPException(status_code=404, detail=f"No runbooks in category '{category}'")

    # Update all
    modified_by = user.get("username", "api")
    for runbook_id in runbook_ids:
        query = text("""
            INSERT INTO site_runbook_config (site_id, runbook_id, enabled, modified_by, modified_at)
            VALUES (:site_id, :runbook_id, :enabled, :modified_by, NOW())
            ON CONFLICT (site_id, runbook_id) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                modified_by = :modified_by,
                modified_at = NOW()
        """)
        await db.execute(query, {
            "site_id": site_id,
            "runbook_id": runbook_id,
            "enabled": enabled,
            "modified_by": modified_by
        })

    await db.commit()
    return {
        "site_id": site_id,
        "category": category,
        "enabled": enabled,
        "runbooks_updated": len(runbook_ids)
    }


# =============================================================================
# Appliance-Level Configuration Endpoints
# =============================================================================

@router.get("/appliances/{appliance_id}", response_model=List[RunbookConfigStatus])
async def get_appliance_runbook_overrides(
    appliance_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get appliance-level runbook overrides.

    Returns only overrides; runbooks not listed use site-level config.
    """
    query = text("""
        SELECT runbook_id, enabled, modified_by, modified_at, notes
        FROM appliance_runbook_config
        WHERE appliance_id = :appliance_id
        ORDER BY runbook_id
    """)
    result = await db.execute(query, {"appliance_id": appliance_id})
    rows = result.fetchall()

    return [
        RunbookConfigStatus(
            runbook_id=row.runbook_id,
            enabled=row.enabled,
            modified_by=row.modified_by,
            modified_at=row.modified_at,
            notes=row.notes
        )
        for row in rows
    ]


@router.put("/appliances/{appliance_id}/{runbook_id}")
async def update_appliance_runbook_config(
    appliance_id: str,
    runbook_id: str,
    config: RunbookConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: Dict[str, Any] = Depends(require_auth)
):
    """Set appliance-level override for a runbook."""
    # Verify runbook exists
    check_query = text("SELECT runbook_id FROM runbooks WHERE runbook_id = :runbook_id")
    result = await db.execute(check_query, {"runbook_id": runbook_id})
    if not result.fetchone():
        raise HTTPException(status_code=404, detail=f"Runbook {runbook_id} not found")

    # Upsert configuration
    query = text("""
        INSERT INTO appliance_runbook_config (appliance_id, runbook_id, enabled, modified_by, modified_at, notes)
        VALUES (:appliance_id, :runbook_id, :enabled, :modified_by, NOW(), :notes)
        ON CONFLICT (appliance_id, runbook_id) DO UPDATE SET
            enabled = EXCLUDED.enabled,
            modified_by = EXCLUDED.modified_by,
            modified_at = NOW(),
            notes = COALESCE(EXCLUDED.notes, appliance_runbook_config.notes)
        RETURNING enabled, modified_at
    """)

    result = await db.execute(query, {
        "appliance_id": appliance_id,
        "runbook_id": runbook_id,
        "enabled": config.enabled,
        "modified_by": user.get("username", "api"),
        "notes": config.notes
    })
    await db.commit()
    row = result.fetchone()

    return {
        "appliance_id": appliance_id,
        "runbook_id": runbook_id,
        "enabled": row.enabled,
        "modified_at": row.modified_at.isoformat() if row.modified_at else None
    }


@router.delete("/appliances/{appliance_id}/{runbook_id}")
async def remove_appliance_runbook_override(
    appliance_id: str,
    runbook_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Remove appliance-level override (revert to site config)."""
    query = text("""
        DELETE FROM appliance_runbook_config
        WHERE appliance_id = :appliance_id AND runbook_id = :runbook_id
    """)
    result = await db.execute(query, {
        "appliance_id": appliance_id,
        "runbook_id": runbook_id
    })
    await db.commit()

    return {"deleted": result.rowcount > 0}


# =============================================================================
# Effective Configuration Endpoints
# =============================================================================

@router.get("/appliances/{appliance_id}/effective", response_model=EnabledRunbooksResponse)
async def get_effective_runbooks_for_appliance(
    appliance_id: str,
    site_id: Optional[str] = Query(None, description="Site ID (if known)"),
    db: AsyncSession = Depends(get_db)
):
    """Get effective list of enabled runbooks for an appliance.

    Hierarchy: appliance override > site config > default (enabled)

    This is the endpoint the agent calls during check-in to get its runbook list.
    """
    # If site_id not provided, try to extract from appliance_id format
    # Format: {site_id}-{mac_address}
    if not site_id and "-" in appliance_id:
        # Try to find site from appliance_id pattern
        parts = appliance_id.rsplit("-", 1)
        if len(parts) == 2:
            possible_site_id = parts[0]
            # Verify it's a valid site
            check = text("SELECT site_id FROM sites WHERE site_id = :site_id")
            result = await db.execute(check, {"site_id": possible_site_id})
            if result.fetchone():
                site_id = possible_site_id

    # Get all runbooks with effective enabled status
    query = text("""
        SELECT
            r.id as runbook_id,
            COALESCE(
                arc.enabled,           -- Appliance override takes priority
                src.enabled,           -- Site config second
                true                   -- Default is enabled
            ) as enabled,
            CASE
                WHEN arc.enabled IS NOT NULL THEN 'appliance'
                WHEN src.enabled IS NOT NULL THEN 'site'
                ELSE 'default'
            END as source
        FROM runbooks r
        LEFT JOIN site_runbook_config src ON src.runbook_id = r.id AND src.site_id = :site_id
        LEFT JOIN appliance_runbook_config arc ON arc.runbook_id = r.id AND arc.appliance_id = :appliance_id
        ORDER BY r.id
    """)

    result = await db.execute(query, {
        "site_id": site_id or "",
        "appliance_id": appliance_id
    })
    rows = result.fetchall()

    enabled_runbooks = [row.runbook_id for row in rows if row.enabled]
    sources = {row.source for row in rows if row.enabled}
    primary_source = "appliance" if "appliance" in sources else ("site" if "site" in sources else "default")

    return EnabledRunbooksResponse(
        appliance_id=appliance_id,
        enabled_runbooks=enabled_runbooks,
        source=primary_source
    )


# =============================================================================
# Execution History Endpoints
# =============================================================================

@router.get("/executions")
async def list_runbook_executions(
    site_id: Optional[str] = Query(None),
    runbook_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db)
):
    """List runbook execution history with filters."""
    conditions = []
    params = {"limit": limit}

    if site_id:
        conditions.append("site_id = :site_id")
        params["site_id"] = site_id
    if runbook_id:
        conditions.append("runbook_id = :runbook_id")
        params["runbook_id"] = runbook_id
    if status:
        conditions.append("status = :status")
        params["status"] = status

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

    query = text(f"""
        SELECT
            id, runbook_id, site_id, appliance_id, target_hostname,
            triggered_by, incident_id, status,
            detect_result, remediate_result, verify_result,
            started_at, completed_at, execution_time_ms,
            error_message, retry_count, created_at
        FROM runbook_executions
        {where_clause}
        ORDER BY started_at DESC
        LIMIT :limit
    """)

    result = await db.execute(query, params)
    rows = result.fetchall()

    return [
        {
            "id": str(row.id),
            "runbook_id": row.runbook_id,
            "site_id": row.site_id,
            "appliance_id": row.appliance_id,
            "target_hostname": row.target_hostname,
            "triggered_by": row.triggered_by,
            "status": row.status,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "execution_time_ms": row.execution_time_ms,
            "error_message": row.error_message,
        }
        for row in rows
    ]


@router.get("/executions/stats")
async def get_runbook_execution_stats(
    days: int = Query(30, le=90),
    db: AsyncSession = Depends(get_db)
):
    """Get aggregate execution statistics for runbooks."""
    query = text("""
        SELECT
            runbook_id,
            COUNT(*) as total_executions,
            COUNT(*) FILTER (WHERE status = 'success') as successful,
            COUNT(*) FILTER (WHERE status = 'failed') as failed,
            AVG(execution_time_ms) FILTER (WHERE status = 'success') as avg_time_ms,
            MAX(started_at) as last_execution
        FROM runbook_executions
        WHERE started_at > NOW() - INTERVAL ':days days'
        GROUP BY runbook_id
        ORDER BY total_executions DESC
    """.replace(":days", str(days)))

    result = await db.execute(query)
    rows = result.fetchall()

    return [
        {
            "runbook_id": row.runbook_id,
            "total_executions": row.total_executions,
            "successful": row.successful,
            "failed": row.failed,
            "success_rate": round(row.successful / row.total_executions * 100, 1) if row.total_executions > 0 else 0,
            "avg_time_ms": int(row.avg_time_ms) if row.avg_time_ms else 0,
            "last_execution": row.last_execution.isoformat() if row.last_execution else None
        }
        for row in rows
    ]
