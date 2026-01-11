"""
Linux Sensor API - Endpoints for receiving Linux sensor events.

Implements the dual-mode architecture where bash sensors push drift events
and the appliance uses SSH for remediation.

Architecture:
- Sensors: Lightweight bash scripts (~400 lines) running every 10s
- Detection: Read-only, push-based (no credentials on target)
- Remediation: SSH-based, credential-pull from Central Command
"""

import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from pydantic import BaseModel
from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)

# In-memory registry of active Linux sensors
linux_sensor_registry: Dict[str, "LinuxSensorStatus"] = {}

# Timeout for considering sensor "dead" (seconds)
LINUX_SENSOR_TIMEOUT = 120


# =============================================================================
# Pydantic Models
# =============================================================================

class LinuxSensorHeartbeat(BaseModel):
    """Heartbeat from Linux sensor."""
    sensor_id: str
    hostname: str
    version: str
    uptime: int  # seconds
    timestamp: str


class LinuxSensorEvent(BaseModel):
    """Drift event pushed by Linux sensor."""
    sensor_id: str
    hostname: str
    check_type: str
    severity: str  # critical, high, medium, low
    title: str
    details: str
    current_value: Optional[str] = None
    expected_value: Optional[str] = None
    timestamp: str


class LinuxSensorRegistration(BaseModel):
    """Registration request from new Linux sensor."""
    hostname: str
    os_version: Optional[str] = None
    kernel: Optional[str] = None


class LinuxSensorConfig(BaseModel):
    """Configuration sent to sensor on registration."""
    sensor_id: str
    api_key: str
    check_interval: int = 10
    heartbeat_interval: int = 60


@dataclass
class LinuxSensorStatus:
    """Internal tracking of Linux sensor state."""
    sensor_id: str
    hostname: str
    last_heartbeat: datetime
    version: str = "unknown"
    uptime: int = 0
    event_count: int = 0
    last_event_type: Optional[str] = None


# =============================================================================
# Sensor Registry Functions
# =============================================================================

def has_active_linux_sensor(hostname: str) -> bool:
    """Check if hostname has an active Linux sensor within timeout."""
    for sensor_id, status in linux_sensor_registry.items():
        if status.hostname.lower() == hostname.lower():
            age = (datetime.now(timezone.utc) - status.last_heartbeat).total_seconds()
            if age < LINUX_SENSOR_TIMEOUT:
                return True
    return False


def get_linux_sensor_hosts() -> List[str]:
    """Return list of hostnames with active Linux sensors."""
    now = datetime.now(timezone.utc)
    return [
        status.hostname
        for status in linux_sensor_registry.values()
        if (now - status.last_heartbeat).total_seconds() < LINUX_SENSOR_TIMEOUT
    ]


def get_linux_polling_hosts(all_targets: List[str]) -> List[str]:
    """Return Linux hosts that need SSH polling (no active sensor)."""
    return [h for h in all_targets if not has_active_linux_sensor(h)]


def update_linux_sensor_registry(sensor_id: str, heartbeat: LinuxSensorHeartbeat) -> None:
    """Update sensor registry from heartbeat."""
    linux_sensor_registry[sensor_id] = LinuxSensorStatus(
        sensor_id=sensor_id,
        hostname=heartbeat.hostname,
        last_heartbeat=datetime.now(timezone.utc),
        version=heartbeat.version,
        uptime=heartbeat.uptime
    )


def touch_linux_sensor(sensor_id: str) -> None:
    """Update last-seen time for sensor (called when any event received)."""
    if sensor_id in linux_sensor_registry:
        linux_sensor_registry[sensor_id].last_heartbeat = datetime.now(timezone.utc)


def generate_sensor_credentials() -> tuple[str, str]:
    """Generate sensor ID and API key for new registration."""
    sensor_id = f"lsens-{secrets.token_hex(8)}"
    api_key = secrets.token_urlsafe(32)
    return sensor_id, api_key


# =============================================================================
# API Router
# =============================================================================

router = APIRouter(prefix="/sensor", tags=["linux-sensor"])

# Sensor script files (loaded from disk)
_sensor_scripts_dir: Optional[Path] = None


def set_sensor_scripts_dir(path: Path) -> None:
    """Set the directory containing sensor scripts."""
    global _sensor_scripts_dir
    _sensor_scripts_dir = path


def _get_script_content(script_name: str) -> Optional[str]:
    """Get content of a sensor script file."""
    if _sensor_scripts_dir is None:
        return None

    script_path = _sensor_scripts_dir / script_name
    if script_path.exists():
        return script_path.read_text()
    return None


# =============================================================================
# Script Download Endpoints
# =============================================================================

@router.get("/install.sh", response_class=PlainTextResponse)
async def get_install_script():
    """Download the sensor installation script."""
    content = _get_script_content("install.sh")
    if content is None:
        raise HTTPException(status_code=404, detail="Install script not found")
    return Response(content=content, media_type="text/x-sh")


@router.get("/uninstall.sh", response_class=PlainTextResponse)
async def get_uninstall_script():
    """Download the sensor uninstallation script."""
    content = _get_script_content("uninstall.sh")
    if content is None:
        raise HTTPException(status_code=404, detail="Uninstall script not found")
    return Response(content=content, media_type="text/x-sh")


@router.get("/osiriscare-sensor.sh", response_class=PlainTextResponse)
async def get_sensor_script():
    """Download the main sensor script."""
    content = _get_script_content("osiriscare-sensor.sh")
    if content is None:
        raise HTTPException(status_code=404, detail="Sensor script not found")
    return Response(content=content, media_type="text/x-sh")


# =============================================================================
# Registration & Heartbeat Endpoints
# =============================================================================

@router.post("/register")
async def register_sensor(registration: LinuxSensorRegistration):
    """
    Register a new Linux sensor.
    Returns credentials for the sensor to use.
    """
    sensor_id, api_key = generate_sensor_credentials()

    # Store initial status
    linux_sensor_registry[sensor_id] = LinuxSensorStatus(
        sensor_id=sensor_id,
        hostname=registration.hostname,
        last_heartbeat=datetime.now(timezone.utc),
        version="pending"
    )

    logger.info(f"Registered new Linux sensor: {sensor_id} for {registration.hostname}")

    return LinuxSensorConfig(
        sensor_id=sensor_id,
        api_key=api_key,
        check_interval=10,
        heartbeat_interval=60
    )


@router.post("/heartbeat")
async def sensor_heartbeat_endpoint(heartbeat: LinuxSensorHeartbeat):
    """
    Receive heartbeat from Linux sensor.
    Updates sensor registry and confirms sensor is alive.
    """
    now = datetime.now(timezone.utc)

    update_linux_sensor_registry(heartbeat.sensor_id, heartbeat)

    logger.debug(
        f"Linux sensor heartbeat: {heartbeat.hostname} "
        f"(sensor: {heartbeat.sensor_id}, uptime: {heartbeat.uptime}s)"
    )

    return {
        "status": "ok",
        "mode": "sensor",
        "server_time": now.isoformat(),
        "next_heartbeat_in": 60
    }


# =============================================================================
# Event Endpoints
# =============================================================================

@router.post("/event")
async def sensor_event_endpoint(
    event: LinuxSensorEvent,
    background_tasks: BackgroundTasks
):
    """
    Receive drift event from Linux sensor.
    Queues drift for healing via L1/L2/L3 pipeline.
    """
    logger.info(
        f"Linux sensor event: {event.hostname} - {event.check_type} ({event.severity})"
    )

    # Update sensor last-seen time
    touch_linux_sensor(event.sensor_id)

    # Update event count
    if event.sensor_id in linux_sensor_registry:
        linux_sensor_registry[event.sensor_id].event_count += 1
        linux_sensor_registry[event.sensor_id].last_event_type = event.check_type

    # Queue for healing in background
    background_tasks.add_task(handle_linux_sensor_event, event)

    event_id = f"{event.sensor_id}-{event.check_type}-{event.timestamp[:19]}"

    return {
        "status": "received",
        "healing_queued": True,
        "event_id": event_id
    }


# =============================================================================
# Status Endpoints
# =============================================================================

@router.get("/status")
async def get_linux_sensor_status():
    """
    Return status of all registered Linux sensors.
    Used by dashboard to show sensor health.
    """
    now = datetime.now(timezone.utc)

    statuses = []
    for sensor_id, status in linux_sensor_registry.items():
        age = (now - status.last_heartbeat).total_seconds()
        statuses.append({
            "sensor_id": sensor_id,
            "hostname": status.hostname,
            "version": status.version,
            "last_heartbeat": status.last_heartbeat.isoformat(),
            "age_seconds": int(age),
            "active": age < LINUX_SENSOR_TIMEOUT,
            "uptime": status.uptime,
            "event_count": status.event_count,
            "last_event_type": status.last_event_type
        })

    return {
        "sensors": statuses,
        "total": len(statuses),
        "active": sum(1 for s in statuses if s["active"]),
        "sensor_timeout_seconds": LINUX_SENSOR_TIMEOUT
    }


@router.get("/hosts/polling")
async def get_hosts_needing_ssh_polling(all_hosts: Optional[str] = None):
    """
    Return list of Linux hosts that need SSH polling.
    Used by main loop to determine polling targets.

    Query param all_hosts: comma-separated list of all target hostnames
    """
    if all_hosts:
        host_list = [h.strip() for h in all_hosts.split(",")]
    else:
        host_list = []

    sensor_hosts = get_linux_sensor_hosts()
    polling_hosts = get_linux_polling_hosts(host_list)

    return {
        "total_hosts": len(host_list),
        "sensor_hosts": len(sensor_hosts),
        "sensor_hostnames": sensor_hosts,
        "polling_hosts": polling_hosts,
        "polling_count": len(polling_hosts)
    }


# =============================================================================
# Event Handling (Background Task)
# =============================================================================

# These will be set by the appliance agent during initialization
_linux_healer = None
_linux_targets = []
_incident_db = None
_l3_escalation = None
_evidence_generator = None
_config = None


def configure_linux_healing(
    linux_healer=None,
    linux_targets=None,
    incident_db=None,
    l3_escalation=None,
    evidence_generator=None,
    config=None
):
    """Configure Linux healing dependencies (called during agent init)."""
    global _linux_healer, _linux_targets, _incident_db
    global _l3_escalation, _evidence_generator, _config

    _linux_healer = linux_healer
    _linux_targets = linux_targets or []
    _incident_db = incident_db
    _l3_escalation = l3_escalation
    _evidence_generator = evidence_generator
    _config = config


async def handle_linux_sensor_event(event: LinuxSensorEvent):
    """
    Process drift event from Linux sensor through L1/L2/L3 healing.
    Uses SSH to execute remediation.
    """
    try:
        # Check if healing is configured
        if not _linux_healer:
            logger.warning(
                f"Linux healing not configured - sensor event from {event.hostname} "
                f"not processed"
            )
            return

        # Import here to avoid circular imports
        from .models import Incident
        import uuid

        # Map sensor check types to runbook types
        check_type_map = {
            "ssh_config": "sshd_password_auth",
            "firewall": "firewall_disabled",
            "failed_logins": "failed_logins",
            "disk_space": "disk_space",
            "memory": "memory_usage",
            "users": "unauthorized_user",
            "services": "service_stopped",
            "file_integrity": "file_integrity",
            "open_ports": "open_ports",
            "updates": "security_updates",
            "audit_logs": "audit_logs",
            "cron_jobs": "cron_modified"
        }

        incident_type = check_type_map.get(event.check_type, event.check_type)

        # Convert sensor event to Incident format
        incident = Incident(
            id=f"LSENS-{uuid.uuid4().hex[:12]}",
            site_id=_config.site_id if _config else "unknown",
            host_id=event.hostname,
            incident_type=incident_type,
            severity=event.severity,
            raw_data={
                "check_type": event.check_type,
                "title": event.title,
                "details": event.details,
                "drift_detected": True,
                "sensor_event": True,
                "sensor_id": event.sensor_id,
                "current_value": event.current_value,
                "expected_value": event.expected_value
            },
            created_at=event.timestamp,
            pattern_signature=f"linux_sensor:{event.check_type}:{event.hostname}"
        )

        # Find matching Linux target for SSH remediation
        target = None
        for t in _linux_targets:
            # Match by hostname (case-insensitive, partial match)
            t_hostname = getattr(t, 'hostname', '') or ''
            t_display = getattr(t, 'display_name', '') or ''

            if (t_hostname.lower() == event.hostname.lower() or
                event.hostname.lower() in t_hostname.lower() or
                t_display.lower() == event.hostname.lower()):
                target = t
                break

        if not target:
            logger.error(
                f"No SSH credentials for Linux sensor host: {event.hostname}"
            )
            if _l3_escalation:
                await _l3_escalation.create_ticket(
                    incident,
                    reason="Sensor detected drift but no SSH credentials configured"
                )
            return

        # Record incident if DB available
        if _incident_db:
            _incident_db.record_incident(incident)

        # Route through Linux healing engine (uses SSH for remediation)
        result = await _linux_healer.heal(incident, target)

        if result and result.success:
            logger.info(
                f"Healed Linux sensor drift: {event.hostname}/{event.check_type} "
                f"via {getattr(result, 'runbook_id', 'unknown')}"
            )

            # Generate evidence bundle if available
            if _evidence_generator:
                await _evidence_generator.create_bundle(
                    runbook_id=getattr(result, 'runbook_id', event.check_type),
                    incident=incident,
                    healing_result=result,
                    trigger="linux_sensor_event"
                )
        else:
            reason = getattr(result, 'reason', 'Unknown error') if result else 'No result'
            logger.warning(
                f"Linux healing failed for sensor drift: {event.hostname}/{event.check_type} "
                f"- {reason}"
            )

    except Exception as e:
        logger.error(f"Error healing Linux sensor drift: {e}", exc_info=True)
        if _l3_escalation and _config:
            try:
                from .models import Incident
                incident = Incident(
                    id=f"LSENS-ERR-{event.hostname[:8]}",
                    site_id=_config.site_id,
                    host_id=event.hostname,
                    incident_type=event.check_type,
                    severity="high",
                    raw_data={"error": str(e)},
                    created_at=datetime.now(timezone.utc).isoformat(),
                    pattern_signature="linux_sensor:error"
                )
                await _l3_escalation.create_ticket(incident, reason=str(e))
            except Exception:
                pass


# =============================================================================
# Utility Functions
# =============================================================================

def get_linux_dual_mode_stats() -> Dict[str, Any]:
    """Return statistics for Linux dual-mode operation."""
    now = datetime.now(timezone.utc)
    active = [
        status for status in linux_sensor_registry.values()
        if (now - status.last_heartbeat).total_seconds() < LINUX_SENSOR_TIMEOUT
    ]

    total_events = sum(s.event_count for s in linux_sensor_registry.values())

    return {
        "total_sensors": len(linux_sensor_registry),
        "active_sensors": len(active),
        "sensor_hostnames": [s.hostname for s in active],
        "total_events_received": total_events,
        "sensor_timeout_seconds": LINUX_SENSOR_TIMEOUT
    }


def clear_stale_linux_sensors(max_age_seconds: int = 3600) -> int:
    """Remove sensors that haven't checked in for a long time."""
    now = datetime.now(timezone.utc)
    stale = [
        sensor_id for sensor_id, status in linux_sensor_registry.items()
        if (now - status.last_heartbeat).total_seconds() > max_age_seconds
    ]

    for sensor_id in stale:
        del linux_sensor_registry[sensor_id]

    return len(stale)


def get_combined_sensor_stats() -> Dict[str, Any]:
    """Get combined stats for both Windows and Linux sensors."""
    from .sensor_api import get_dual_mode_stats as get_windows_stats

    windows_stats = get_windows_stats()
    linux_stats = get_linux_dual_mode_stats()

    return {
        "windows": windows_stats,
        "linux": linux_stats,
        "total_active_sensors": (
            windows_stats.get("active_sensors", 0) +
            linux_stats.get("active_sensors", 0)
        ),
        "all_sensor_hostnames": (
            windows_stats.get("sensor_hostnames", []) +
            linux_stats.get("sensor_hostnames", [])
        )
    }
