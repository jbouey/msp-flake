"""
Sensor API - Endpoints for receiving Windows sensor events.

Implements the dual-mode architecture where sensors push drift events
and the appliance uses WinRM for remediation.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from pydantic import BaseModel
from fastapi import APIRouter, BackgroundTasks, HTTPException

logger = logging.getLogger(__name__)

# In-memory registry of active sensors
sensor_registry: Dict[str, "SensorStatus"] = {}

# Timeout for considering sensor "dead" (seconds)
SENSOR_TIMEOUT = 120


# =============================================================================
# Pydantic Models
# =============================================================================

class SensorHeartbeat(BaseModel):
    """Heartbeat from Windows sensor."""
    hostname: str
    domain: Optional[str] = None
    sensor_version: str
    timestamp: str
    drift_count: int
    has_critical: bool
    compliant: bool
    uptime_seconds: Optional[int] = None
    mode: str = "sensor"


class SensorDriftEvent(BaseModel):
    """Drift event pushed by Windows sensor."""
    hostname: str
    domain: Optional[str] = None
    drift_type: str
    severity: str  # critical, high, medium, low
    details: Dict[str, Any] = {}
    check_id: Optional[str] = None
    detected_at: str
    sensor_version: Optional[str] = None


class SensorResolution(BaseModel):
    """Resolution event from sensor (drift was fixed externally)."""
    hostname: str
    drift_type: str
    resolved_at: str
    resolved_by: Optional[str] = "external"


@dataclass
class SensorStatus:
    """Internal tracking of sensor state."""
    hostname: str
    last_heartbeat: datetime
    sensor_version: str
    drift_count: int = 0
    compliant: bool = True
    mode: str = "sensor"
    domain: Optional[str] = None


# =============================================================================
# Sensor Registry Functions
# =============================================================================

def has_active_sensor(hostname: str) -> bool:
    """Check if hostname has an active sensor within timeout."""
    if hostname not in sensor_registry:
        return False

    status = sensor_registry[hostname]
    age = (datetime.now(timezone.utc) - status.last_heartbeat).total_seconds()
    return age < SENSOR_TIMEOUT


def get_sensor_hosts() -> List[str]:
    """Return list of hostnames with active sensors."""
    return [h for h in sensor_registry.keys() if has_active_sensor(h)]


def get_polling_hosts(all_targets: List[str]) -> List[str]:
    """Return hosts that need WinRM polling (no active sensor)."""
    return [h for h in all_targets if not has_active_sensor(h)]


def update_sensor_registry(hostname: str, heartbeat: SensorHeartbeat) -> None:
    """Update sensor registry from heartbeat."""
    sensor_registry[hostname] = SensorStatus(
        hostname=hostname,
        last_heartbeat=datetime.now(timezone.utc),
        sensor_version=heartbeat.sensor_version,
        drift_count=heartbeat.drift_count,
        compliant=heartbeat.compliant,
        mode=heartbeat.mode,
        domain=heartbeat.domain
    )


def touch_sensor(hostname: str) -> None:
    """Update last-seen time for sensor (called when any event received)."""
    if hostname in sensor_registry:
        sensor_registry[hostname].last_heartbeat = datetime.now(timezone.utc)


# =============================================================================
# API Router
# =============================================================================

router = APIRouter(prefix="/api/sensor", tags=["sensor"])


@router.post("/heartbeat")
async def sensor_heartbeat_endpoint(heartbeat: SensorHeartbeat):
    """
    Receive heartbeat from Windows sensor.
    Updates sensor registry and confirms sensor is alive.
    """
    now = datetime.now(timezone.utc)

    update_sensor_registry(heartbeat.hostname, heartbeat)

    logger.debug(
        f"Sensor heartbeat: {heartbeat.hostname} "
        f"(drifts: {heartbeat.drift_count}, compliant: {heartbeat.compliant})"
    )

    return {
        "status": "ok",
        "mode": "sensor",
        "server_time": now.isoformat(),
        "next_heartbeat_in": 60
    }


@router.post("/drift")
async def sensor_drift_endpoint(
    event: SensorDriftEvent,
    background_tasks: BackgroundTasks
):
    """
    Receive drift event from Windows sensor.
    Queues drift for healing via L1/L2/L3 pipeline.
    """
    logger.info(
        f"Sensor drift: {event.hostname} - {event.drift_type} ({event.severity})"
    )

    # Update sensor last-seen time
    touch_sensor(event.hostname)

    # Queue for healing in background
    background_tasks.add_task(handle_sensor_drift, event)

    event_id = f"{event.hostname}-{event.drift_type}-{event.detected_at}"

    return {
        "status": "received",
        "healing_queued": True,
        "event_id": event_id
    }


@router.post("/resolved")
async def sensor_resolved_endpoint(event: SensorResolution):
    """
    Receive resolution event from sensor.
    Updates incident database to mark drift as resolved externally.
    """
    logger.info(f"Sensor resolved: {event.hostname} - {event.drift_type}")

    # Update sensor last-seen time
    touch_sensor(event.hostname)

    # TODO: Update incident database when integrated
    # await incident_db.mark_resolved(
    #     hostname=event.hostname,
    #     drift_type=event.drift_type,
    #     resolved_at=event.resolved_at,
    #     resolved_by=event.resolved_by
    # )

    return {"status": "acknowledged"}


@router.get("/status")
async def get_sensor_status():
    """
    Return status of all registered sensors.
    Used by dashboard to show sensor health.
    """
    now = datetime.now(timezone.utc)

    statuses = []
    for hostname, status in sensor_registry.items():
        age = (now - status.last_heartbeat).total_seconds()
        statuses.append({
            "hostname": hostname,
            "domain": status.domain,
            "sensor_version": status.sensor_version,
            "last_heartbeat": status.last_heartbeat.isoformat(),
            "age_seconds": int(age),
            "active": age < SENSOR_TIMEOUT,
            "compliant": status.compliant,
            "drift_count": status.drift_count,
            "mode": status.mode
        })

    return {
        "sensors": statuses,
        "total": len(statuses),
        "active": sum(1 for s in statuses if s["active"]),
        "sensor_timeout_seconds": SENSOR_TIMEOUT
    }


@router.get("/hosts/polling")
async def get_hosts_needing_polling(all_hosts: Optional[str] = None):
    """
    Return list of hosts that need WinRM polling.
    Used by main loop to determine polling targets.

    Query param all_hosts: comma-separated list of all target hostnames
    """
    if all_hosts:
        host_list = [h.strip() for h in all_hosts.split(",")]
    else:
        host_list = []

    sensor_hosts = get_sensor_hosts()
    polling_hosts = get_polling_hosts(host_list)

    return {
        "total_hosts": len(host_list),
        "sensor_hosts": len(sensor_hosts),
        "sensor_hostnames": sensor_hosts,
        "polling_hosts": polling_hosts,
        "polling_count": len(polling_hosts)
    }


# =============================================================================
# Drift Handling (Background Task)
# =============================================================================

# These will be set by the appliance agent during initialization
_auto_healer = None
_windows_targets = []
_incident_db = None
_l3_escalation = None
_evidence_generator = None
_config = None


def configure_healing(
    auto_healer=None,
    windows_targets=None,
    incident_db=None,
    l3_escalation=None,
    evidence_generator=None,
    config=None
):
    """Configure healing dependencies (called during agent init)."""
    global _auto_healer, _windows_targets, _incident_db
    global _l3_escalation, _evidence_generator, _config

    _auto_healer = auto_healer
    _windows_targets = windows_targets or []
    _incident_db = incident_db
    _l3_escalation = l3_escalation
    _evidence_generator = evidence_generator
    _config = config


async def handle_sensor_drift(event: SensorDriftEvent):
    """
    Process drift event from sensor through L1/L2/L3 healing.
    Uses WinRM to execute remediation.
    """
    try:
        # Check if healing is configured
        if not _auto_healer:
            logger.warning(
                f"Healing not configured - sensor drift from {event.hostname} "
                f"not processed"
            )
            return

        # Import here to avoid circular imports
        from .incident_db import Incident
        from datetime import datetime, timezone
        import uuid

        # Convert sensor event to Incident format
        incident = Incident(
            id=f"SENS-{uuid.uuid4().hex[:12]}",
            site_id=_config.site_id if _config else "unknown",
            host_id=event.hostname,
            incident_type=event.drift_type,
            severity=event.severity,
            raw_data={
                "check_type": event.drift_type,
                "drift_detected": True,
                "sensor_event": True,
                "check_id": event.check_id,
                **event.details
            },
            created_at=event.detected_at,
            pattern_signature=f"sensor:{event.drift_type}:{event.hostname}"
        )

        # Find matching Windows target for WinRM remediation
        target = None
        for t in _windows_targets:
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
                f"No WinRM credentials for sensor host: {event.hostname}"
            )
            if _l3_escalation:
                await _l3_escalation.create_ticket(
                    incident,
                    reason="Sensor detected drift but no WinRM credentials configured"
                )
            return

        # Record incident if DB available
        if _incident_db:
            _incident_db.record_incident(incident)

        # Route through healing engine (uses WinRM for remediation)
        result = await _auto_healer.heal(incident)

        if result and result.success:
            logger.info(
                f"Healed sensor drift: {event.hostname}/{event.drift_type} "
                f"via {getattr(result, 'runbook_id', 'unknown')}"
            )

            # Generate evidence bundle if available
            if _evidence_generator:
                await _evidence_generator.create_bundle(
                    runbook_id=getattr(result, 'runbook_id', event.check_id),
                    incident=incident,
                    healing_result=result,
                    trigger="sensor_event"
                )
        else:
            reason = getattr(result, 'reason', 'Unknown error') if result else 'No result'
            logger.warning(
                f"Healing failed for sensor drift: {event.hostname}/{event.drift_type} "
                f"- {reason}"
            )

    except Exception as e:
        logger.error(f"Error healing sensor drift: {e}", exc_info=True)
        if _l3_escalation and _config:
            try:
                from .incident_db import Incident
                incident = Incident(
                    id=f"SENS-ERR-{event.hostname[:8]}",
                    site_id=_config.site_id,
                    host_id=event.hostname,
                    incident_type=event.drift_type,
                    severity="high",
                    raw_data={"error": str(e)},
                    created_at=datetime.now(timezone.utc).isoformat(),
                    pattern_signature="sensor:error"
                )
                await _l3_escalation.create_ticket(incident, reason=str(e))
            except Exception:
                pass


# =============================================================================
# Utility Functions for Agent Integration
# =============================================================================

def get_dual_mode_stats() -> Dict[str, Any]:
    """Return statistics for dual-mode operation."""
    now = datetime.now(timezone.utc)
    active = [h for h, s in sensor_registry.items()
              if (now - s.last_heartbeat).total_seconds() < SENSOR_TIMEOUT]

    return {
        "total_sensors": len(sensor_registry),
        "active_sensors": len(active),
        "sensor_hostnames": active,
        "sensor_timeout_seconds": SENSOR_TIMEOUT
    }


def clear_stale_sensors(max_age_seconds: int = 3600) -> int:
    """Remove sensors that haven't checked in for a long time."""
    now = datetime.now(timezone.utc)
    stale = [
        h for h, s in sensor_registry.items()
        if (now - s.last_heartbeat).total_seconds() > max_age_seconds
    ]

    for hostname in stale:
        del sensor_registry[hostname]

    return len(stale)
