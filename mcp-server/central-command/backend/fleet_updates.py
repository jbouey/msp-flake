"""Fleet Updates API - Zero-Touch Update System.

Phase 13: Manages ISO releases, rollouts, and appliance updates.
Supports staged rollouts with automatic pause on failure threshold.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field

from .fleet import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fleet", tags=["fleet-updates"])


# =============================================================================
# MODELS
# =============================================================================

class RolloutStage(BaseModel):
    """A single stage in a staged rollout."""
    percent: int = Field(..., ge=1, le=100)
    delay_hours: int = Field(default=24, ge=0)


class MaintenanceWindow(BaseModel):
    """Maintenance window configuration."""
    start: str = "02:00"
    end: str = "05:00"
    timezone: str = "America/New_York"
    days: List[str] = ["sunday", "monday", "tuesday", "wednesday", "thursday"]


class ReleaseCreate(BaseModel):
    """Create a new update release."""
    version: str
    iso_url: str
    sha256: str
    size_bytes: Optional[int] = None
    release_notes: Optional[str] = None
    agent_version: Optional[str] = None
    is_latest: bool = False


class ReleaseResponse(BaseModel):
    """Response model for releases."""
    id: str
    version: str
    iso_url: str
    sha256: str
    size_bytes: Optional[int]
    release_notes: Optional[str]
    agent_version: Optional[str]
    created_at: datetime
    is_active: bool
    is_latest: bool


class RolloutCreate(BaseModel):
    """Create a new rollout."""
    release_id: str
    name: Optional[str] = None
    strategy: str = "staged"  # immediate, staged, canary, manual
    stages: List[RolloutStage] = [
        RolloutStage(percent=5, delay_hours=24),
        RolloutStage(percent=25, delay_hours=24),
        RolloutStage(percent=100, delay_hours=0),
    ]
    maintenance_window: MaintenanceWindow = MaintenanceWindow()
    target_filter: Optional[Dict[str, Any]] = None
    failure_threshold_percent: int = 10
    auto_rollback: bool = True


class RolloutResponse(BaseModel):
    """Response model for rollouts."""
    id: str
    release_id: str
    version: str
    name: Optional[str]
    strategy: str
    current_stage: int
    stages: List[dict]
    maintenance_window: dict
    status: str
    started_at: Optional[datetime]
    paused_at: Optional[datetime]
    completed_at: Optional[datetime]
    failure_threshold_percent: int
    auto_rollback: bool
    progress: Optional[dict] = None


class ApplianceUpdateStatus(BaseModel):
    """Status update from an appliance."""
    status: str
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    boot_attempts: Optional[int] = None
    health_check_result: Optional[dict] = None


class ApplianceUpdateResponse(BaseModel):
    """Response for appliance update status."""
    id: str
    appliance_id: str
    appliance_name: Optional[str]
    site_id: Optional[str]
    rollout_id: str
    stage_assigned: int
    status: str
    previous_version: Optional[str]
    new_version: Optional[str]
    download_started_at: Optional[datetime]
    download_completed_at: Optional[datetime]
    reboot_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_message: Optional[str]
    boot_attempts: int
    created_at: datetime
    updated_at: datetime


# =============================================================================
# RELEASES ENDPOINTS
# =============================================================================

@router.get("/releases", response_model=List[ReleaseResponse])
async def list_releases(
    active_only: bool = Query(True, description="Only return active releases"),
    limit: int = Query(20, ge=1, le=100),
):
    """List all update releases."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = """
            SELECT id, version, iso_url, sha256, size_bytes, release_notes,
                   agent_version, created_at, is_active, is_latest
            FROM update_releases
            WHERE ($1 = false OR is_active = true)
            ORDER BY created_at DESC
            LIMIT $2
        """
        rows = await conn.fetch(query, active_only, limit)

        return [
            ReleaseResponse(
                id=str(row["id"]),
                version=row["version"],
                iso_url=row["iso_url"],
                sha256=row["sha256"],
                size_bytes=row["size_bytes"],
                release_notes=row["release_notes"],
                agent_version=row["agent_version"],
                created_at=row["created_at"],
                is_active=row["is_active"],
                is_latest=row["is_latest"],
            )
            for row in rows
        ]


@router.post("/releases", response_model=ReleaseResponse)
async def create_release(release: ReleaseCreate):
    """Create a new update release."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Check for duplicate version
        existing = await conn.fetchrow(
            "SELECT id FROM update_releases WHERE version = $1",
            release.version
        )
        if existing:
            raise HTTPException(status_code=409, detail=f"Release {release.version} already exists")

        row = await conn.fetchrow(
            """
            INSERT INTO update_releases (version, iso_url, sha256, size_bytes, release_notes, agent_version, is_latest)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, version, iso_url, sha256, size_bytes, release_notes, agent_version, created_at, is_active, is_latest
            """,
            release.version,
            release.iso_url,
            release.sha256,
            release.size_bytes,
            release.release_notes,
            release.agent_version,
            release.is_latest,
        )

        # Audit log
        await conn.execute(
            """
            INSERT INTO update_audit_log (event_type, release_id, details)
            VALUES ('release_created', $1, $2)
            """,
            row["id"],
            json.dumps({"version": release.version}),
        )

        return ReleaseResponse(
            id=str(row["id"]),
            version=row["version"],
            iso_url=row["iso_url"],
            sha256=row["sha256"],
            size_bytes=row["size_bytes"],
            release_notes=row["release_notes"],
            agent_version=row["agent_version"],
            created_at=row["created_at"],
            is_active=row["is_active"],
            is_latest=row["is_latest"],
        )


@router.get("/releases/{version}", response_model=ReleaseResponse)
async def get_release(version: str):
    """Get a specific release by version."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, version, iso_url, sha256, size_bytes, release_notes,
                   agent_version, created_at, is_active, is_latest
            FROM update_releases WHERE version = $1
            """,
            version
        )
        if not row:
            raise HTTPException(status_code=404, detail=f"Release {version} not found")

        return ReleaseResponse(
            id=str(row["id"]),
            version=row["version"],
            iso_url=row["iso_url"],
            sha256=row["sha256"],
            size_bytes=row["size_bytes"],
            release_notes=row["release_notes"],
            agent_version=row["agent_version"],
            created_at=row["created_at"],
            is_active=row["is_active"],
            is_latest=row["is_latest"],
        )


@router.put("/releases/{version}/latest")
async def set_latest_release(version: str):
    """Mark a release as the latest."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE update_releases SET is_latest = true WHERE version = $1",
            version
        )
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail=f"Release {version} not found")

        return {"status": "ok", "message": f"Release {version} marked as latest"}


@router.delete("/releases/{version}")
async def deactivate_release(version: str):
    """Deactivate a release (soft delete)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE update_releases SET is_active = false, is_latest = false WHERE version = $1",
            version
        )
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail=f"Release {version} not found")

        return {"status": "ok", "message": f"Release {version} deactivated"}


# =============================================================================
# ROLLOUTS ENDPOINTS
# =============================================================================

@router.get("/rollouts", response_model=List[RolloutResponse])
async def list_rollouts(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(20, ge=1, le=100),
):
    """List all rollouts."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = """
            SELECT r.id, r.release_id, rel.version, r.name, r.strategy, r.current_stage,
                   r.stages, r.maintenance_window, r.status, r.started_at, r.paused_at,
                   r.completed_at, r.failure_threshold_percent, r.auto_rollback
            FROM update_rollouts r
            JOIN update_releases rel ON r.release_id = rel.id
            WHERE ($1::text IS NULL OR r.status = $1)
            ORDER BY r.started_at DESC
            LIMIT $2
        """
        rows = await conn.fetch(query, status, limit)

        results = []
        for row in rows:
            # Get progress stats
            progress = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'succeeded') as succeeded,
                    COUNT(*) FILTER (WHERE status = 'failed') as failed,
                    COUNT(*) FILTER (WHERE status = 'rolled_back') as rolled_back,
                    COUNT(*) FILTER (WHERE status IN ('pending', 'notified', 'downloading', 'ready')) as pending,
                    COUNT(*) FILTER (WHERE status IN ('rebooting', 'verifying')) as in_progress
                FROM appliance_updates WHERE rollout_id = $1
                """,
                row["id"]
            )

            results.append(RolloutResponse(
                id=str(row["id"]),
                release_id=str(row["release_id"]),
                version=row["version"],
                name=row["name"],
                strategy=row["strategy"],
                current_stage=row["current_stage"],
                stages=row["stages"] if isinstance(row["stages"], list) else json.loads(row["stages"] or "[]"),
                maintenance_window=row["maintenance_window"] if isinstance(row["maintenance_window"], dict) else json.loads(row["maintenance_window"] or "{}"),
                status=row["status"],
                started_at=row["started_at"],
                paused_at=row["paused_at"],
                completed_at=row["completed_at"],
                failure_threshold_percent=row["failure_threshold_percent"],
                auto_rollback=row["auto_rollback"],
                progress={
                    "total": progress["total"],
                    "succeeded": progress["succeeded"],
                    "failed": progress["failed"],
                    "rolled_back": progress["rolled_back"],
                    "pending": progress["pending"],
                    "in_progress": progress["in_progress"],
                    "success_rate": round(100 * progress["succeeded"] / progress["total"], 1) if progress["total"] > 0 else 0,
                } if progress else None,
            ))

        return results


@router.post("/rollouts", response_model=RolloutResponse)
async def create_rollout(rollout: RolloutCreate, background_tasks: BackgroundTasks):
    """Create and start a new rollout."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Verify release exists
        release = await conn.fetchrow(
            "SELECT id, version FROM update_releases WHERE id = $1 AND is_active = true",
            UUID(rollout.release_id)
        )
        if not release:
            raise HTTPException(status_code=404, detail="Release not found or inactive")

        # Create rollout
        stages_json = json.dumps([s.model_dump() for s in rollout.stages])
        maint_json = json.dumps(rollout.maintenance_window.model_dump())
        filter_json = json.dumps(rollout.target_filter) if rollout.target_filter else None

        row = await conn.fetchrow(
            """
            INSERT INTO update_rollouts
            (release_id, name, strategy, stages, maintenance_window, target_filter,
             failure_threshold_percent, auto_rollback, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'in_progress')
            RETURNING id, release_id, name, strategy, current_stage, stages, maintenance_window,
                      status, started_at, paused_at, completed_at, failure_threshold_percent, auto_rollback
            """,
            UUID(rollout.release_id),
            rollout.name or f"Rollout {release['version']}",
            rollout.strategy,
            stages_json,
            maint_json,
            filter_json,
            rollout.failure_threshold_percent,
            rollout.auto_rollback,
        )

        # Get all appliances to include in rollout
        appliance_query = """
            SELECT id FROM appliances WHERE status = 'active'
        """
        if rollout.target_filter:
            if "site_ids" in rollout.target_filter:
                appliance_query += f" AND site_id = ANY($1)"
                appliances = await conn.fetch(appliance_query, rollout.target_filter["site_ids"])
            else:
                appliances = await conn.fetch(appliance_query)
        else:
            appliances = await conn.fetch(appliance_query)

        # Assign appliances to stages
        import random
        appliance_ids = [a["id"] for a in appliances]
        random.shuffle(appliance_ids)  # Randomize order for fair distribution

        stages = rollout.stages
        total = len(appliance_ids)
        assigned = 0

        for stage_idx, stage in enumerate(stages):
            if stage_idx == len(stages) - 1:
                # Last stage gets all remaining
                stage_appliances = appliance_ids[assigned:]
            else:
                count = int(total * stage.percent / 100)
                stage_appliances = appliance_ids[assigned:assigned + count]
                assigned += count

            for app_id in stage_appliances:
                await conn.execute(
                    """
                    INSERT INTO appliance_updates (appliance_id, rollout_id, stage_assigned, new_version)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (appliance_id, rollout_id) DO NOTHING
                    """,
                    app_id,
                    row["id"],
                    stage_idx,
                    release["version"],
                )

        # Audit log
        await conn.execute(
            """
            INSERT INTO update_audit_log (event_type, release_id, rollout_id, details)
            VALUES ('rollout_started', $1, $2, $3)
            """,
            UUID(rollout.release_id),
            row["id"],
            json.dumps({"strategy": rollout.strategy, "appliances": len(appliance_ids)}),
        )

        return RolloutResponse(
            id=str(row["id"]),
            release_id=str(row["release_id"]),
            version=release["version"],
            name=row["name"],
            strategy=row["strategy"],
            current_stage=row["current_stage"],
            stages=json.loads(stages_json),
            maintenance_window=json.loads(maint_json),
            status=row["status"],
            started_at=row["started_at"],
            paused_at=row["paused_at"],
            completed_at=row["completed_at"],
            failure_threshold_percent=row["failure_threshold_percent"],
            auto_rollback=row["auto_rollback"],
            progress={"total": len(appliance_ids), "succeeded": 0, "failed": 0, "pending": len(appliance_ids), "in_progress": 0},
        )


@router.post("/rollouts/{rollout_id}/pause")
async def pause_rollout(rollout_id: str):
    """Pause a rollout."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE update_rollouts
            SET status = 'paused', paused_at = NOW()
            WHERE id = $1 AND status = 'in_progress'
            """,
            UUID(rollout_id)
        )
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Rollout not found or not in progress")

        await conn.execute(
            "INSERT INTO update_audit_log (event_type, rollout_id) VALUES ('rollout_paused', $1)",
            UUID(rollout_id)
        )

        return {"status": "ok", "message": "Rollout paused"}


@router.post("/rollouts/{rollout_id}/resume")
async def resume_rollout(rollout_id: str):
    """Resume a paused rollout."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE update_rollouts
            SET status = 'in_progress', paused_at = NULL
            WHERE id = $1 AND status = 'paused'
            """,
            UUID(rollout_id)
        )
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Rollout not found or not paused")

        await conn.execute(
            "INSERT INTO update_audit_log (event_type, rollout_id) VALUES ('rollout_resumed', $1)",
            UUID(rollout_id)
        )

        return {"status": "ok", "message": "Rollout resumed"}


@router.post("/rollouts/{rollout_id}/cancel")
async def cancel_rollout(rollout_id: str):
    """Cancel a rollout."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE update_rollouts
            SET status = 'cancelled', completed_at = NOW()
            WHERE id = $1 AND status IN ('pending', 'in_progress', 'paused')
            """,
            UUID(rollout_id)
        )
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Rollout not found or already completed")

        await conn.execute(
            "INSERT INTO update_audit_log (event_type, rollout_id) VALUES ('rollout_cancelled', $1)",
            UUID(rollout_id)
        )

        return {"status": "ok", "message": "Rollout cancelled"}


@router.post("/rollouts/{rollout_id}/advance")
async def advance_rollout_stage(rollout_id: str):
    """Manually advance to the next rollout stage."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rollout = await conn.fetchrow(
            "SELECT current_stage, stages FROM update_rollouts WHERE id = $1",
            UUID(rollout_id)
        )
        if not rollout:
            raise HTTPException(status_code=404, detail="Rollout not found")

        stages = json.loads(rollout["stages"]) if isinstance(rollout["stages"], str) else rollout["stages"]
        if rollout["current_stage"] >= len(stages) - 1:
            raise HTTPException(status_code=400, detail="Already at final stage")

        new_stage = rollout["current_stage"] + 1
        await conn.execute(
            "UPDATE update_rollouts SET current_stage = $1 WHERE id = $2",
            new_stage,
            UUID(rollout_id)
        )

        # Notify appliances in new stage
        await conn.execute(
            """
            UPDATE appliance_updates
            SET status = 'notified'
            WHERE rollout_id = $1 AND stage_assigned = $2 AND status = 'pending'
            """,
            UUID(rollout_id),
            new_stage
        )

        await conn.execute(
            """
            INSERT INTO update_audit_log (event_type, rollout_id, details)
            VALUES ('rollout_stage_advanced', $1, $2)
            """,
            UUID(rollout_id),
            json.dumps({"new_stage": new_stage}),
        )

        return {"status": "ok", "message": f"Advanced to stage {new_stage}"}


# =============================================================================
# APPLIANCE UPDATE ENDPOINTS
# =============================================================================

@router.get("/rollouts/{rollout_id}/appliances", response_model=List[ApplianceUpdateResponse])
async def list_rollout_appliances(
    rollout_id: str,
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """List appliances in a rollout."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = """
            SELECT au.id, au.appliance_id, a.name as appliance_name, a.site_id,
                   au.rollout_id, au.stage_assigned, au.status, au.previous_version,
                   au.new_version, au.download_started_at, au.download_completed_at,
                   au.reboot_at, au.completed_at, au.error_message, au.boot_attempts,
                   au.created_at, au.updated_at
            FROM appliance_updates au
            JOIN appliances a ON au.appliance_id = a.id
            WHERE au.rollout_id = $1
            AND ($2::text IS NULL OR au.status = $2)
            ORDER BY au.stage_assigned, au.created_at
            LIMIT $3
        """
        rows = await conn.fetch(query, UUID(rollout_id), status, limit)

        return [
            ApplianceUpdateResponse(
                id=str(row["id"]),
                appliance_id=str(row["appliance_id"]),
                appliance_name=row["appliance_name"],
                site_id=str(row["site_id"]) if row["site_id"] else None,
                rollout_id=str(row["rollout_id"]),
                stage_assigned=row["stage_assigned"],
                status=row["status"],
                previous_version=row["previous_version"],
                new_version=row["new_version"],
                download_started_at=row["download_started_at"],
                download_completed_at=row["download_completed_at"],
                reboot_at=row["reboot_at"],
                completed_at=row["completed_at"],
                error_message=row["error_message"],
                boot_attempts=row["boot_attempts"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]


@router.get("/appliances/{appliance_id}/pending-update")
async def get_pending_update(appliance_id: str):
    """Check if an appliance has a pending update (called during check-in).

    appliance_id can be either:
    - UUID: Direct appliance ID lookup
    - site_id: String identifier like 'physical-appliance-pilot-1aea78'
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Try to parse as UUID first, otherwise look up by site_id
        try:
            app_uuid = UUID(appliance_id)
        except ValueError:
            # Look up by site_id
            app_row = await conn.fetchrow(
                "SELECT id FROM appliances WHERE site_id = $1",
                appliance_id
            )
            if not app_row:
                return {"update_available": False}
            app_uuid = app_row["id"]

        # Find active rollout with notified status for this appliance
        row = await conn.fetchrow(
            """
            SELECT au.id, au.rollout_id, au.new_version, au.status,
                   rel.iso_url, rel.sha256, rel.size_bytes,
                   r.maintenance_window
            FROM appliance_updates au
            JOIN update_rollouts r ON au.rollout_id = r.id
            JOIN update_releases rel ON r.release_id = rel.id
            WHERE au.appliance_id = $1
            AND r.status = 'in_progress'
            AND au.status IN ('notified', 'downloading', 'ready')
            AND au.stage_assigned <= r.current_stage
            LIMIT 1
            """,
            app_uuid
        )

        if not row:
            return {"update_available": False}

        maint = row["maintenance_window"]
        if isinstance(maint, str):
            maint = json.loads(maint)

        return {
            "update_available": True,
            "update": {
                "update_id": str(row["id"]),
                "rollout_id": str(row["rollout_id"]),
                "version": row["new_version"],
                "iso_url": row["iso_url"],
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
                "maintenance_window": maint,
                "current_status": row["status"],
            }
        }


@router.post("/appliances/{appliance_id}/update-status")
async def update_appliance_status(appliance_id: str, status_update: ApplianceUpdateStatus):
    """Update the status of an appliance's update (called by appliance).

    appliance_id can be either UUID or site_id.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Try to parse as UUID first, otherwise look up by site_id
        try:
            app_uuid = UUID(appliance_id)
        except ValueError:
            # Look up by site_id
            app_row = await conn.fetchrow(
                "SELECT id FROM appliances WHERE site_id = $1",
                appliance_id
            )
            if not app_row:
                raise HTTPException(status_code=404, detail="Appliance not found")
            app_uuid = app_row["id"]

        # Find the active update for this appliance
        update = await conn.fetchrow(
            """
            SELECT au.id, au.rollout_id, r.failure_threshold_percent, r.auto_rollback
            FROM appliance_updates au
            JOIN update_rollouts r ON au.rollout_id = r.id
            WHERE au.appliance_id = $1 AND r.status = 'in_progress'
            ORDER BY au.created_at DESC LIMIT 1
            """,
            app_uuid
        )

        if not update:
            raise HTTPException(status_code=404, detail="No active update for this appliance")

        # Update status
        update_fields = ["status = $2", "updated_at = NOW()"]
        params = [update["id"], status_update.status]
        param_idx = 3

        if status_update.error_message:
            update_fields.append(f"error_message = ${param_idx}")
            params.append(status_update.error_message)
            param_idx += 1

        if status_update.error_code:
            update_fields.append(f"error_code = ${param_idx}")
            params.append(status_update.error_code)
            param_idx += 1

        if status_update.boot_attempts is not None:
            update_fields.append(f"boot_attempts = ${param_idx}")
            params.append(status_update.boot_attempts)
            param_idx += 1

        if status_update.health_check_result:
            update_fields.append(f"health_checks = health_checks || ${param_idx}::jsonb")
            params.append(json.dumps([status_update.health_check_result]))
            param_idx += 1

        # Set timestamps based on status
        if status_update.status == "downloading":
            update_fields.append("download_started_at = NOW()")
        elif status_update.status == "ready":
            update_fields.append("download_completed_at = NOW()")
        elif status_update.status == "rebooting":
            update_fields.append("reboot_at = NOW()")
        elif status_update.status in ("succeeded", "failed", "rolled_back"):
            update_fields.append("completed_at = NOW()")

        await conn.execute(
            f"UPDATE appliance_updates SET {', '.join(update_fields)} WHERE id = $1",
            *params
        )

        # Check failure threshold if failed
        if status_update.status == "failed" and update["auto_rollback"]:
            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'failed') as failed,
                    COUNT(*) FILTER (WHERE status IN ('succeeded', 'failed')) as completed
                FROM appliance_updates WHERE rollout_id = $1
                """,
                update["rollout_id"]
            )

            if stats["completed"] > 0:
                failure_rate = 100 * stats["failed"] / stats["completed"]
                if failure_rate >= update["failure_threshold_percent"]:
                    # Pause rollout
                    await conn.execute(
                        """
                        UPDATE update_rollouts
                        SET status = 'paused', paused_at = NOW()
                        WHERE id = $1 AND status = 'in_progress'
                        """,
                        update["rollout_id"]
                    )
                    logger.warning(f"Rollout {update['rollout_id']} paused: failure rate {failure_rate:.1f}%")

        # Audit log
        await conn.execute(
            """
            INSERT INTO update_audit_log (event_type, rollout_id, appliance_id, details)
            VALUES ('appliance_status_update', $1, $2, $3)
            """,
            update["rollout_id"],
            app_uuid,
            json.dumps({"status": status_update.status, "error": status_update.error_message}),
        )

        return {"status": "ok"}


# =============================================================================
# STATS & MONITORING
# =============================================================================

@router.get("/stats")
async def get_fleet_update_stats():
    """Get overall fleet update statistics."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        releases = await conn.fetchrow(
            "SELECT COUNT(*) as total, COUNT(*) FILTER (WHERE is_active) as active FROM update_releases"
        )

        latest = await conn.fetchrow(
            "SELECT version FROM update_releases WHERE is_latest = true"
        )

        rollouts = await conn.fetchrow(
            """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'in_progress') as in_progress,
                COUNT(*) FILTER (WHERE status = 'paused') as paused,
                COUNT(*) FILTER (WHERE status = 'completed') as completed
            FROM update_rollouts
            """
        )

        appliance_updates = await conn.fetchrow(
            """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'succeeded') as succeeded,
                COUNT(*) FILTER (WHERE status = 'failed') as failed,
                COUNT(*) FILTER (WHERE status = 'rolled_back') as rolled_back
            FROM appliance_updates
            WHERE created_at > NOW() - INTERVAL '30 days'
            """
        )

        return {
            "releases": {
                "total": releases["total"],
                "active": releases["active"],
                "latest_version": latest["version"] if latest else None,
            },
            "rollouts": {
                "total": rollouts["total"],
                "in_progress": rollouts["in_progress"],
                "paused": rollouts["paused"],
                "completed": rollouts["completed"],
            },
            "appliance_updates_30d": {
                "total": appliance_updates["total"],
                "succeeded": appliance_updates["succeeded"],
                "failed": appliance_updates["failed"],
                "rolled_back": appliance_updates["rolled_back"],
                "success_rate": round(100 * appliance_updates["succeeded"] / appliance_updates["total"], 1) if appliance_updates["total"] > 0 else 0,
            },
        }
