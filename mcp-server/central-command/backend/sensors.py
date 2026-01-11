"""
Sensor Management API for Central Command.

Provides endpoints to manage Windows and Linux sensors in the dual-mode architecture.

Dual-Mode Architecture:
- Sensors: Lightweight scripts (PowerShell/Bash) running on targets
- Detection: Read-only, push-based (no credentials stored on targets)
- Remediation: WinRM (Windows) or SSH (Linux), credential-pull from Central Command
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from pydantic import BaseModel

from fastapi import APIRouter, HTTPException, Depends
import asyncpg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sensors", tags=["sensors"])

# Sensor timeout in seconds (2 minutes)
SENSOR_TIMEOUT = 120


# =============================================================================
# Models
# =============================================================================

class SensorInfo(BaseModel):
    """Sensor information returned from registry."""
    hostname: str
    domain: Optional[str] = None
    sensor_version: Optional[str] = None
    first_seen: Optional[str] = None
    last_heartbeat: Optional[str] = None
    last_drift_count: int = 0
    last_compliant: bool = True
    is_active: bool = True
    age_seconds: Optional[int] = None
    mode: str = "sensor"
    platform: str = "windows"  # windows or linux
    sensor_id: Optional[str] = None  # For Linux sensors


class SensorCommand(BaseModel):
    """Sensor deployment command."""
    command_type: str  # deploy_sensor, remove_sensor, deploy_linux_sensor, remove_linux_sensor
    hostname: str
    platform: str = "windows"  # windows or linux


class SensorHeartbeatFromAppliance(BaseModel):
    """Sensor heartbeat forwarded from appliance."""
    site_id: str
    appliance_id: str
    hostname: str
    domain: Optional[str] = None
    sensor_version: str
    drift_count: int
    compliant: bool


# =============================================================================
# Database helpers
# =============================================================================

async def get_db_pool():
    """Get database connection pool."""
    # This should be configured at app startup
    from .main import get_pool
    return await get_pool()


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/sites/{site_id}")
async def get_site_sensors(site_id: str):
    """Get all sensors for a site."""
    pool = await get_db_pool()
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        sensors = await conn.fetch("""
            SELECT
                hostname,
                domain,
                sensor_version,
                first_seen,
                last_heartbeat,
                last_drift_count,
                last_compliant,
                is_active,
                appliance_id,
                EXTRACT(EPOCH FROM ($2 - COALESCE(last_heartbeat, first_seen))) as age_seconds
            FROM sensor_registry
            WHERE site_id = $1
            ORDER BY hostname
        """, site_id, now)

        result = []
        active_count = 0
        for s in sensors:
            age = int(s['age_seconds']) if s['age_seconds'] else 9999
            is_active = age < SENSOR_TIMEOUT and s['is_active']
            if is_active:
                active_count += 1

            result.append({
                "hostname": s['hostname'],
                "domain": s['domain'],
                "sensor_version": s['sensor_version'],
                "first_seen": s['first_seen'].isoformat() if s['first_seen'] else None,
                "last_heartbeat": s['last_heartbeat'].isoformat() if s['last_heartbeat'] else None,
                "last_drift_count": s['last_drift_count'],
                "last_compliant": s['last_compliant'],
                "is_active": is_active,
                "age_seconds": age,
                "appliance_id": s['appliance_id'],
                "mode": "sensor"
            })

        return {
            "sensors": result,
            "total": len(result),
            "active": active_count,
            "sensor_timeout_seconds": SENSOR_TIMEOUT
        }


@router.post("/sites/{site_id}/hosts/{hostname}/deploy")
async def deploy_sensor_to_host(site_id: str, hostname: str):
    """
    Queue sensor deployment to a specific host.
    The appliance will execute on next check-in.
    """
    pool = await get_db_pool()

    async with pool.acquire() as conn:
        # Get appliance for this site
        appliance = await conn.fetchrow("""
            SELECT appliance_id FROM appliances
            WHERE site_id = $1 AND status = 'online'
            LIMIT 1
        """, site_id)

        if not appliance:
            raise HTTPException(status_code=404, detail="No online appliance for site")

        # Queue the command
        await conn.execute("""
            INSERT INTO sensor_commands (site_id, appliance_id, command_type, hostname, status)
            VALUES ($1, $2, 'deploy_sensor', $3, 'pending')
        """, site_id, appliance['appliance_id'], hostname)

        logger.info(f"Queued sensor deploy: {hostname} on {site_id}")

        return {
            "status": "queued",
            "command_type": "deploy_sensor",
            "hostname": hostname,
            "appliance_id": appliance['appliance_id']
        }


@router.delete("/sites/{site_id}/hosts/{hostname}")
async def remove_sensor_from_host(site_id: str, hostname: str):
    """
    Queue sensor removal from a host.
    The appliance will execute on next check-in.
    """
    pool = await get_db_pool()

    async with pool.acquire() as conn:
        # Get appliance for this site
        appliance = await conn.fetchrow("""
            SELECT appliance_id FROM appliances
            WHERE site_id = $1 AND status = 'online'
            LIMIT 1
        """, site_id)

        if not appliance:
            raise HTTPException(status_code=404, detail="No online appliance for site")

        # Queue the command
        await conn.execute("""
            INSERT INTO sensor_commands (site_id, appliance_id, command_type, hostname, status)
            VALUES ($1, $2, 'remove_sensor', $3, 'pending')
        """, site_id, appliance['appliance_id'], hostname)

        # Mark sensor as inactive in registry
        await conn.execute("""
            UPDATE sensor_registry
            SET is_active = false
            WHERE site_id = $1 AND hostname = $2
        """, site_id, hostname)

        logger.info(f"Queued sensor removal: {hostname} on {site_id}")

        return {
            "status": "queued",
            "command_type": "remove_sensor",
            "hostname": hostname
        }


@router.get("/sites/{site_id}/commands/pending")
async def get_pending_sensor_commands(site_id: str, appliance_id: Optional[str] = None):
    """
    Get pending sensor commands for a site/appliance.
    Called by appliance during check-in.
    """
    pool = await get_db_pool()

    async with pool.acquire() as conn:
        if appliance_id:
            commands = await conn.fetch("""
                SELECT id, command_type, hostname, status, created_at
                FROM sensor_commands
                WHERE site_id = $1 AND appliance_id = $2 AND status = 'pending'
                ORDER BY created_at
                LIMIT 10
            """, site_id, appliance_id)
        else:
            commands = await conn.fetch("""
                SELECT id, command_type, hostname, status, created_at
                FROM sensor_commands
                WHERE site_id = $1 AND status = 'pending'
                ORDER BY created_at
                LIMIT 10
            """, site_id)

        # Mark as sent
        command_ids = [c['id'] for c in commands]
        if command_ids:
            await conn.execute("""
                UPDATE sensor_commands
                SET status = 'sent', sent_at = NOW()
                WHERE id = ANY($1)
            """, command_ids)

        return {
            "commands": [
                {
                    "id": c['id'],
                    "command_type": c['command_type'],
                    "hostname": c['hostname'],
                    "created_at": c['created_at'].isoformat()
                }
                for c in commands
            ]
        }


@router.post("/commands/{command_id}/complete")
async def complete_sensor_command(
    command_id: int,
    success: bool = True,
    error: Optional[str] = None
):
    """Mark a sensor command as completed."""
    pool = await get_db_pool()

    async with pool.acquire() as conn:
        result = {"success": success}
        if error:
            result["error"] = error

        await conn.execute("""
            UPDATE sensor_commands
            SET status = $2, completed_at = NOW(), result = $3
            WHERE id = $1
        """, command_id, 'completed' if success else 'failed', json.dumps(result))

        return {"status": "ok"}


@router.post("/heartbeat")
async def record_sensor_heartbeat(heartbeat: SensorHeartbeatFromAppliance):
    """
    Record sensor heartbeat forwarded from appliance.
    Updates or creates sensor registry entry.
    """
    pool = await get_db_pool()
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        # Upsert sensor registry
        await conn.execute("""
            INSERT INTO sensor_registry (
                site_id, hostname, domain, sensor_version,
                last_heartbeat, last_drift_count, last_compliant,
                is_active, appliance_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, true, $8)
            ON CONFLICT (site_id, hostname) DO UPDATE SET
                domain = EXCLUDED.domain,
                sensor_version = EXCLUDED.sensor_version,
                last_heartbeat = EXCLUDED.last_heartbeat,
                last_drift_count = EXCLUDED.last_drift_count,
                last_compliant = EXCLUDED.last_compliant,
                is_active = true,
                appliance_id = EXCLUDED.appliance_id
        """,
            heartbeat.site_id,
            heartbeat.hostname,
            heartbeat.domain,
            heartbeat.sensor_version,
            now,
            heartbeat.drift_count,
            heartbeat.compliant,
            heartbeat.appliance_id
        )

        return {"status": "ok"}


@router.get("/stats")
async def get_global_sensor_stats():
    """Get global sensor statistics across all sites."""
    pool = await get_db_pool()
    now = datetime.now(timezone.utc)
    timeout_threshold = now - timedelta(seconds=SENSOR_TIMEOUT)

    async with pool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT
                COUNT(*) as total_sensors,
                COUNT(*) FILTER (WHERE last_heartbeat > $1 AND is_active) as active_sensors,
                COUNT(DISTINCT site_id) as sites_with_sensors,
                SUM(last_drift_count) FILTER (WHERE last_heartbeat > $1) as total_drifts,
                COUNT(*) FILTER (WHERE NOT last_compliant AND last_heartbeat > $1) as non_compliant_hosts
            FROM sensor_registry
        """, timeout_threshold)

        return {
            "total_sensors": stats['total_sensors'] or 0,
            "active_sensors": stats['active_sensors'] or 0,
            "sites_with_sensors": stats['sites_with_sensors'] or 0,
            "total_active_drifts": stats['total_drifts'] or 0,
            "non_compliant_hosts": stats['non_compliant_hosts'] or 0,
            "sensor_timeout_seconds": SENSOR_TIMEOUT
        }


@router.get("/sites/{site_id}/dual-mode-status")
async def get_site_dual_mode_status(site_id: str):
    """
    Get dual-mode status for a site.
    Shows which hosts have sensors vs need polling.
    """
    pool = await get_db_pool()
    now = datetime.now(timezone.utc)
    timeout_threshold = now - timedelta(seconds=SENSOR_TIMEOUT)

    async with pool.acquire() as conn:
        # Get all Windows targets for site
        credentials = await conn.fetch("""
            SELECT hostname, credential_type
            FROM site_credentials
            WHERE site_id = $1
            AND credential_type IN ('winrm', 'domain_admin', 'local_admin')
        """, site_id)

        # Get active sensors
        sensors = await conn.fetch("""
            SELECT hostname
            FROM sensor_registry
            WHERE site_id = $1 AND is_active = true AND last_heartbeat > $2
        """, site_id, timeout_threshold)

        sensor_hosts = {s['hostname'].lower() for s in sensors}
        all_hosts = {c['hostname'].lower() for c in credentials if c['hostname']}

        sensor_mode_hosts = list(sensor_hosts & all_hosts)
        polling_mode_hosts = list(all_hosts - sensor_hosts)

        # Get all Linux targets for site
        linux_credentials = await conn.fetch("""
            SELECT hostname, credential_type
            FROM site_credentials
            WHERE site_id = $1
            AND credential_type IN ('ssh', 'ssh_key')
        """, site_id)

        # Get active Linux sensors
        linux_sensors = await conn.fetch("""
            SELECT hostname
            FROM sensor_registry
            WHERE site_id = $1 AND is_active = true AND last_heartbeat > $2
            AND platform = 'linux'
        """, site_id, timeout_threshold)

        linux_sensor_hosts = {s['hostname'].lower() for s in linux_sensors}
        all_linux_hosts = {c['hostname'].lower() for c in linux_credentials if c['hostname']}

        linux_sensor_mode_hosts = list(linux_sensor_hosts & all_linux_hosts)
        linux_polling_mode_hosts = list(all_linux_hosts - linux_sensor_hosts)

        total_hosts = len(all_hosts) + len(all_linux_hosts)
        total_sensor_hosts = len(sensor_mode_hosts) + len(linux_sensor_mode_hosts)

        return {
            "windows": {
                "total_targets": len(all_hosts),
                "sensor_mode_hosts": sensor_mode_hosts,
                "sensor_mode_count": len(sensor_mode_hosts),
                "polling_mode_hosts": polling_mode_hosts,
                "polling_mode_count": len(polling_mode_hosts),
                "efficiency_percent": round(len(sensor_mode_hosts) / max(len(all_hosts), 1) * 100, 1)
            },
            "linux": {
                "total_targets": len(all_linux_hosts),
                "sensor_mode_hosts": linux_sensor_mode_hosts,
                "sensor_mode_count": len(linux_sensor_mode_hosts),
                "polling_mode_hosts": linux_polling_mode_hosts,
                "polling_mode_count": len(linux_polling_mode_hosts),
                "efficiency_percent": round(len(linux_sensor_mode_hosts) / max(len(all_linux_hosts), 1) * 100, 1)
            },
            "combined": {
                "total_targets": total_hosts,
                "total_sensor_hosts": total_sensor_hosts,
                "efficiency_percent": round(total_sensor_hosts / max(total_hosts, 1) * 100, 1)
            }
        }


# =============================================================================
# Linux Sensor Endpoints
# =============================================================================

class LinuxSensorHeartbeatFromAppliance(BaseModel):
    """Linux sensor heartbeat forwarded from appliance."""
    site_id: str
    appliance_id: str
    sensor_id: str
    hostname: str
    version: str
    uptime: int
    event_count: int = 0


@router.post("/sites/{site_id}/linux/{hostname}/deploy")
async def deploy_linux_sensor_to_host(site_id: str, hostname: str):
    """
    Queue Linux sensor deployment to a specific host.
    The appliance will execute via SSH on next check-in.
    """
    pool = await get_db_pool()

    async with pool.acquire() as conn:
        # Get appliance for this site
        appliance = await conn.fetchrow("""
            SELECT appliance_id FROM appliances
            WHERE site_id = $1 AND status = 'online'
            LIMIT 1
        """, site_id)

        if not appliance:
            raise HTTPException(status_code=404, detail="No online appliance for site")

        # Queue the command
        await conn.execute("""
            INSERT INTO sensor_commands (site_id, appliance_id, command_type, hostname, platform, status)
            VALUES ($1, $2, 'deploy_linux_sensor', $3, 'linux', 'pending')
        """, site_id, appliance['appliance_id'], hostname)

        logger.info(f"Queued Linux sensor deploy: {hostname} on {site_id}")

        return {
            "status": "queued",
            "command_type": "deploy_linux_sensor",
            "platform": "linux",
            "hostname": hostname,
            "appliance_id": appliance['appliance_id']
        }


@router.delete("/sites/{site_id}/linux/{hostname}")
async def remove_linux_sensor_from_host(site_id: str, hostname: str):
    """
    Queue Linux sensor removal from a host.
    The appliance will execute via SSH on next check-in.
    """
    pool = await get_db_pool()

    async with pool.acquire() as conn:
        # Get appliance for this site
        appliance = await conn.fetchrow("""
            SELECT appliance_id FROM appliances
            WHERE site_id = $1 AND status = 'online'
            LIMIT 1
        """, site_id)

        if not appliance:
            raise HTTPException(status_code=404, detail="No online appliance for site")

        # Queue the command
        await conn.execute("""
            INSERT INTO sensor_commands (site_id, appliance_id, command_type, hostname, platform, status)
            VALUES ($1, $2, 'remove_linux_sensor', $3, 'linux', 'pending')
        """, site_id, appliance['appliance_id'], hostname)

        # Mark sensor as inactive in registry
        await conn.execute("""
            UPDATE sensor_registry
            SET is_active = false
            WHERE site_id = $1 AND hostname = $2 AND platform = 'linux'
        """, site_id, hostname)

        logger.info(f"Queued Linux sensor removal: {hostname} on {site_id}")

        return {
            "status": "queued",
            "command_type": "remove_linux_sensor",
            "platform": "linux",
            "hostname": hostname
        }


@router.post("/linux/heartbeat")
async def record_linux_sensor_heartbeat(heartbeat: LinuxSensorHeartbeatFromAppliance):
    """
    Record Linux sensor heartbeat forwarded from appliance.
    Updates or creates sensor registry entry.
    """
    pool = await get_db_pool()
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        # Upsert sensor registry
        await conn.execute("""
            INSERT INTO sensor_registry (
                site_id, hostname, sensor_version,
                last_heartbeat, last_drift_count, last_compliant,
                is_active, appliance_id, platform, sensor_id
            ) VALUES ($1, $2, $3, $4, $5, true, true, $6, 'linux', $7)
            ON CONFLICT (site_id, hostname) DO UPDATE SET
                sensor_version = EXCLUDED.sensor_version,
                last_heartbeat = EXCLUDED.last_heartbeat,
                last_drift_count = EXCLUDED.last_drift_count,
                is_active = true,
                appliance_id = EXCLUDED.appliance_id,
                platform = 'linux',
                sensor_id = EXCLUDED.sensor_id
        """,
            heartbeat.site_id,
            heartbeat.hostname,
            heartbeat.version,
            now,
            heartbeat.event_count,
            heartbeat.appliance_id,
            heartbeat.sensor_id
        )

        return {"status": "ok"}


@router.get("/sites/{site_id}/linux")
async def get_site_linux_sensors(site_id: str):
    """Get all Linux sensors for a site."""
    pool = await get_db_pool()
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        sensors = await conn.fetch("""
            SELECT
                hostname,
                sensor_id,
                sensor_version,
                first_seen,
                last_heartbeat,
                last_drift_count,
                last_compliant,
                is_active,
                appliance_id,
                EXTRACT(EPOCH FROM ($2 - COALESCE(last_heartbeat, first_seen))) as age_seconds
            FROM sensor_registry
            WHERE site_id = $1 AND platform = 'linux'
            ORDER BY hostname
        """, site_id, now)

        result = []
        active_count = 0
        for s in sensors:
            age = int(s['age_seconds']) if s['age_seconds'] else 9999
            is_active = age < SENSOR_TIMEOUT and s['is_active']
            if is_active:
                active_count += 1

            result.append({
                "hostname": s['hostname'],
                "sensor_id": s['sensor_id'],
                "sensor_version": s['sensor_version'],
                "first_seen": s['first_seen'].isoformat() if s['first_seen'] else None,
                "last_heartbeat": s['last_heartbeat'].isoformat() if s['last_heartbeat'] else None,
                "last_drift_count": s['last_drift_count'],
                "last_compliant": s['last_compliant'],
                "is_active": is_active,
                "age_seconds": age,
                "appliance_id": s['appliance_id'],
                "platform": "linux",
                "mode": "sensor"
            })

        return {
            "sensors": result,
            "total": len(result),
            "active": active_count,
            "platform": "linux",
            "sensor_timeout_seconds": SENSOR_TIMEOUT
        }
