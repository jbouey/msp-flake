"""
Infrastructure endpoints extracted from main.py.

Server stats, runbook listing, backup status, and snapshot management.
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_auth
from .shared import RUNBOOKS, async_session, get_db

logger = structlog.get_logger()

router = APIRouter(tags=["infra"])


BACKUP_STATUS_FILE = Path("/opt/backups/status/latest.json")
SNAPSHOT_STATUS_FILE = Path("/opt/backups/status/hetzner-snapshot.json")


@router.get("/runbooks")
async def list_runbooks(user: dict = Depends(require_auth)):
    """List available runbooks."""
    runbooks = []
    for rb_id, rb in RUNBOOKS.items():
        runbooks.append({
            "id": rb_id,
            "name": rb.get("name"),
            "description": rb.get("description"),
            "category": rb.get("category"),
            "severity": rb.get("severity"),
            "hipaa_controls": rb.get("hipaa_controls", [])
        })

    async with async_session() as session:
        result = await session.execute(
            text("SELECT runbook_id, name, description, category, severity, hipaa_controls FROM runbooks WHERE enabled = true")
        )
        for row in result.fetchall():
            if row[0] not in [r["id"] for r in runbooks]:
                runbooks.append({
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "category": row[3],
                    "severity": row[4],
                    "hipaa_controls": row[5]
                })

    return {"runbooks": runbooks, "count": len(runbooks)}


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db), user: dict = Depends(require_auth)):
    """Get server statistics."""
    stats = {}

    result = await db.execute(text("SELECT COUNT(*) FROM appliances WHERE status = 'active'"))
    stats["active_appliances"] = result.scalar()

    result = await db.execute(text("""
        SELECT status, COUNT(*) FROM orders
        WHERE issued_at > NOW() - INTERVAL '24 hours'
        GROUP BY status
    """))
    stats["orders_24h"] = {row[0]: row[1] for row in result.fetchall()}

    result = await db.execute(text("""
        SELECT status, COUNT(*) FROM incidents
        WHERE reported_at > NOW() - INTERVAL '24 hours'
        GROUP BY status
    """))
    stats["incidents_24h"] = {row[0]: row[1] for row in result.fetchall()}

    result = await db.execute(text("""
        SELECT outcome, COUNT(*) FROM evidence_bundles
        WHERE timestamp_start > NOW() - INTERVAL '24 hours'
        GROUP BY outcome
    """))
    stats["evidence_24h"] = {row[0]: row[1] for row in result.fetchall()}

    result = await db.execute(text("""
        SELECT resolution_tier, COUNT(*) FROM incidents
        WHERE reported_at > NOW() - INTERVAL '7 days'
        AND resolution_tier IS NOT NULL
        GROUP BY resolution_tier
    """))
    stats["resolution_tiers_7d"] = {row[0]: row[1] for row in result.fetchall()}

    stats["timestamp"] = datetime.now(timezone.utc).isoformat()

    return stats


@router.get("/api/backup/status")
async def get_backup_status(user: dict = Depends(require_auth)):
    """Get current backup status for dashboard."""
    if not BACKUP_STATUS_FILE.exists():
        return {
            "status": "unknown",
            "message": "No backup status available",
            "last_backup": None,
            "storage_used_mb": 0,
            "total_snapshots": 0,
        }

    try:
        with open(BACKUP_STATUS_FILE, 'r') as f:
            status_data = json.load(f)

        return {
            "status": status_data.get("status", "unknown"),
            "last_backup": status_data.get("timestamp"),
            "last_backup_duration_seconds": status_data.get("duration_seconds"),
            "storage_used_mb": round(status_data.get("storage_used_bytes", 0) / (1024*1024), 2),
            "total_snapshots": status_data.get("total_snapshots", 0),
            "repository": status_data.get("repository"),
            "retention": status_data.get("retention"),
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to read backup status: {str(e)}",
            "last_backup": None,
        }


@router.get("/api/snapshot/status")
async def get_snapshot_status(user: dict = Depends(require_auth)):
    """Get Hetzner Cloud snapshot status."""
    if not SNAPSHOT_STATUS_FILE.exists():
        return {
            "status": "unknown",
            "message": "No snapshot has been taken yet. First snapshot runs Sunday 4 AM UTC.",
        }

    try:
        with open(SNAPSHOT_STATUS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        return {"status": "error", "message": f"Failed to read status: {e}"}


@router.get("/api/snapshot/list")
async def list_snapshots(user: dict = Depends(require_auth)):
    """List all Hetzner Cloud snapshots."""
    token_file = Path("/root/.hcloud-token")
    if not token_file.exists():
        return {"status": "error", "message": "Hetzner token not configured"}

    try:
        token = token_file.read_text().strip()

        result = subprocess.run(
            ["/root/.nix-profile/bin/hcloud", "image", "list", "--type", "snapshot", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "HCLOUD_TOKEN": token, "HOME": "/root"}
        )

        if result.returncode == 0:
            images = json.loads(result.stdout) if result.stdout else []
            our_snapshots = [
                {
                    "id": img.get("id"),
                    "description": img.get("description"),
                    "created": img.get("created"),
                    "size_gb": img.get("image_size"),
                }
                for img in images
                if img.get("description", "").startswith("osiriscare-weekly")
            ]
            return {
                "status": "success",
                "count": len(our_snapshots),
                "snapshots": sorted(our_snapshots, key=lambda x: x.get("created", ""), reverse=True)
            }
        else:
            return {"status": "error", "message": result.stderr}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Command timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
