"""Device inventory sync from appliance network scanners.

Receives device inventory reports from appliance local-portals and stores
them in Central Command for fleet-wide visibility.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, Field
import asyncpg
from fastapi import APIRouter, HTTPException, Query

from .fleet import get_pool
from .tenant_middleware import admin_connection
from .oui_lookup import get_manufacturer_hint
from .credential_crypto import decrypt_credential

logger = logging.getLogger(__name__)


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================


class ComplianceCheckDetail(BaseModel):
    """A single compliance check result from the appliance."""
    check_type: str
    hipaa_control: Optional[str] = None
    status: str  # pass, warn, fail
    details: Optional[str] = None  # JSON string or None
    checked_at: datetime


class DeviceSyncEntry(BaseModel):
    """A single device from the appliance inventory."""
    device_id: str = Field(..., description="Local device ID from appliance")
    hostname: Optional[str] = None
    ip_address: str
    mac_address: Optional[str] = None
    device_type: str = "unknown"
    os_name: Optional[str] = None
    os_version: Optional[str] = None

    # Medical device handling
    medical_device: bool = False
    scan_policy: str = "standard"  # standard, limited, excluded
    manually_opted_in: bool = False

    # Compliance
    compliance_status: str = "unknown"  # compliant, drifted, unknown, excluded
    open_ports: List[int] = Field(default_factory=list)
    compliance_details: List[ComplianceCheckDetail] = Field(default_factory=list)

    # Discovery metadata
    discovery_source: str = "nmap"
    first_seen_at: datetime
    last_seen_at: datetime
    last_scan_at: Optional[datetime] = None

    # Probe fields (populated by Go daemon network probe)
    os_fingerprint: Optional[str] = None
    distro: Optional[str] = None
    probe_ssh: Optional[bool] = None
    probe_winrm: Optional[bool] = None
    probe_snmp: Optional[bool] = None
    ad_joined: Optional[bool] = None


class DeviceSyncReport(BaseModel):
    """Batch device sync report from an appliance."""
    appliance_id: str = Field(..., description="Appliance identifier")
    site_id: str = Field(..., description="Site/client identifier")
    scan_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Device inventory
    devices: List[DeviceSyncEntry]

    # Summary stats
    total_devices: int
    monitored_devices: int
    excluded_devices: int
    medical_devices: int
    compliance_rate: float  # 0-100 percentage


class DeviceSyncResponse(BaseModel):
    """Response to device sync request."""
    status: str  # success, partial, error
    devices_received: int
    devices_updated: int
    devices_created: int
    message: str


# =============================================================================
# DATABASE OPERATIONS
# =============================================================================


async def sync_devices(report: DeviceSyncReport) -> DeviceSyncResponse:
    """
    Sync device inventory from an appliance to Central Command.

    Creates or updates devices in the central database.
    """
    pool = await get_pool()

    devices_updated = 0
    devices_created = 0
    errors = []

    async with admin_connection(pool) as conn:
        # Look up appliance by site_id (unique constraint)
        appliance_row = await conn.fetchrow(
            "SELECT id FROM appliances WHERE site_id = $1",
            report.site_id,
        )

        if not appliance_row:
            return DeviceSyncResponse(
                status="error",
                devices_received=len(report.devices),
                devices_updated=0,
                devices_created=0,
                message=f"Unknown site_id: {report.site_id}. Appliance must checkin first.",
            )

        appliance_db_id = appliance_row["id"]

        # Detect bridge MACs: same MAC on 3+ devices means WiFi bridge/gateway.
        # Clear the MAC on those devices to prevent phantom duplicates.
        from collections import Counter
        mac_counts = Counter(
            d.mac_address for d in report.devices if d.mac_address
        )
        bridge_macs = {m for m, c in mac_counts.items() if c >= 3}
        if bridge_macs:
            logger.info(
                "Bridge MAC(s) detected on %d+ devices, clearing: %s",
                3, bridge_macs,
            )
            for device in report.devices:
                if device.mac_address in bridge_macs:
                    device.mac_address = None

        # Process each device
        for device in report.devices:
            try:
                # Check if device exists (by appliance + local device ID)
                existing = await conn.fetchrow(
                    """
                    SELECT id FROM discovered_devices
                    WHERE appliance_id = $1 AND local_device_id = $2
                    """,
                    appliance_db_id,
                    device.device_id,
                )

                if existing:
                    device_db_id = existing["id"]
                    # Determine last_probe_at: set to NOW() if any probe field is present
                    has_probe_data = any(
                        v is not None for v in [
                            device.probe_ssh, device.probe_winrm,
                            device.probe_snmp, device.ad_joined,
                        ]
                    )
                    # Update existing device
                    await conn.execute(
                        """
                        UPDATE discovered_devices SET
                            hostname = $3,
                            ip_address = $4,
                            mac_address = $5,
                            device_type = $6,
                            os_name = $7,
                            os_version = $8,
                            medical_device = $9,
                            scan_policy = $10,
                            manually_opted_in = $11,
                            compliance_status = $12,
                            open_ports = $13,
                            discovery_source = $14,
                            last_seen_at = $15,
                            last_scan_at = $16,
                            os_fingerprint = COALESCE($17, os_fingerprint),
                            distro = COALESCE($18, distro),
                            probe_ssh = COALESCE($19, probe_ssh),
                            probe_winrm = COALESCE($20, probe_winrm),
                            ad_joined = COALESCE($21, ad_joined),
                            last_probe_at = CASE WHEN $22 THEN NOW() ELSE last_probe_at END,
                            sync_updated_at = NOW()
                        WHERE appliance_id = $1 AND local_device_id = $2
                        """,
                        appliance_db_id,
                        device.device_id,
                        device.hostname,
                        device.ip_address,
                        device.mac_address,
                        device.device_type,
                        device.os_name,
                        device.os_version,
                        device.medical_device,
                        device.scan_policy,
                        device.manually_opted_in,
                        device.compliance_status,
                        device.open_ports,
                        device.discovery_source,
                        device.last_seen_at,
                        device.last_scan_at,
                        device.os_fingerprint,
                        device.distro,
                        device.probe_ssh,
                        device.probe_winrm,
                        device.ad_joined,
                        has_probe_data,
                    )
                    devices_updated += 1
                else:
                    # Insert new device
                    has_probe_data = any(
                        v is not None for v in [
                            device.probe_ssh, device.probe_winrm,
                            device.probe_snmp, device.ad_joined,
                        ]
                    )
                    device_db_id = await conn.fetchval(
                        """
                        INSERT INTO discovered_devices (
                            appliance_id, local_device_id, hostname, ip_address,
                            mac_address, device_type, os_name, os_version,
                            medical_device, scan_policy, manually_opted_in,
                            compliance_status, open_ports, discovery_source,
                            first_seen_at, last_seen_at, last_scan_at,
                            os_fingerprint, distro, probe_ssh, probe_winrm, ad_joined,
                            last_probe_at,
                            sync_created_at, sync_updated_at
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                            $12, $13, $14, $15, $16, $17,
                            $18, $19, $20, $21, $22,
                            CASE WHEN $23 THEN NOW() ELSE NULL END,
                            NOW(), NOW()
                        )
                        RETURNING id
                        """,
                        appliance_db_id,
                        device.device_id,
                        device.hostname,
                        device.ip_address,
                        device.mac_address,
                        device.device_type,
                        device.os_name,
                        device.os_version,
                        device.medical_device,
                        device.scan_policy,
                        device.manually_opted_in,
                        device.compliance_status,
                        device.open_ports,
                        device.discovery_source,
                        device.first_seen_at,
                        device.last_seen_at,
                        device.last_scan_at,
                        device.os_fingerprint,
                        device.distro,
                        device.probe_ssh,
                        device.probe_winrm,
                        device.ad_joined,
                        has_probe_data,
                    )
                    devices_created += 1

                # Auto-classify device_status based on probe results.
                # Only update if current status is 'discovered' or null
                # (never overwrite managed states).
                MANAGED_STATES = ('agent_active', 'deploying', 'pending_deploy', 'ignored')
                current_status_row = await conn.fetchrow(
                    "SELECT device_status FROM discovered_devices WHERE id = $1",
                    device_db_id,
                )
                current_status = current_status_row["device_status"] if current_status_row else None
                if current_status not in MANAGED_STATES:
                    new_device_status: Optional[str] = None
                    if device.ad_joined:
                        # Check for active go_agent coverage
                        agent_active = await conn.fetchval(
                            """
                            SELECT COUNT(*) FROM go_agents
                            WHERE site_id = $1
                            AND (hostname = $2 OR ip_address::text = $3)
                            AND status IN ('active', 'connected')
                            """,
                            report.site_id,
                            device.hostname or "",
                            device.ip_address,
                        )
                        if not agent_active:
                            new_device_status = "ad_managed"
                    elif device.probe_ssh or device.probe_winrm:
                        new_device_status = "take_over_available"

                    if new_device_status is not None:
                        await conn.execute(
                            """
                            UPDATE discovered_devices
                            SET device_status = $2, sync_updated_at = NOW()
                            WHERE id = $1
                            """,
                            device_db_id,
                            new_device_status,
                        )

                # Auto-classify device_type from probe data.
                # Only classify if current type is 'unknown' or empty.
                current_type = device.device_type or 'unknown'
                if current_type == 'unknown':
                    probe_ssh = device.probe_ssh or False
                    probe_winrm = device.probe_winrm or False
                    ad_joined = device.ad_joined or False
                    os_fp = (device.os_fingerprint or '').lower()
                    ports = device.open_ports or []

                    new_type = 'unknown'
                    if ad_joined and probe_winrm:
                        # AD-joined Windows machine — check for DC/server ports
                        if 'server' in os_fp or any(p in ports for p in [53, 88, 389, 636, 3268]):
                            new_type = 'server'
                        else:
                            new_type = 'workstation'
                    elif probe_winrm:
                        new_type = 'workstation'
                    elif probe_ssh:
                        if any(kw in os_fp for kw in ('linux', 'ubuntu', 'nixos', 'debian', 'centos', 'rhel')):
                            if any(p in ports for p in [80, 443, 3306, 5432]):
                                new_type = 'server'
                            else:
                                new_type = 'workstation'
                        elif any(kw in os_fp for kw in ('darwin', 'mac', 'apple')):
                            new_type = 'workstation'
                        else:
                            new_type = 'workstation'
                    elif 80 in ports or 443 in ports:
                        if 161 in ports:
                            new_type = 'network'
                        else:
                            new_type = 'server'
                    elif any(p in ports for p in [9100, 515, 631]):
                        new_type = 'printer'
                    elif 161 in ports:
                        new_type = 'network'

                    if new_type != 'unknown':
                        await conn.execute(
                            "UPDATE discovered_devices SET device_type = $1, sync_updated_at = NOW() WHERE id = $2",
                            new_type, device_db_id,
                        )
                        logger.info(
                            "Auto-classified device %s (%s) as %s (ssh=%s winrm=%s ad=%s)",
                            device.ip_address, device.hostname or "?",
                            new_type, probe_ssh, probe_winrm, ad_joined,
                        )

                # Auto-populate os_name from OS fingerprint if currently empty.
                if device.os_fingerprint:
                    current_os_row = await conn.fetchrow(
                        "SELECT os_name FROM discovered_devices WHERE id = $1",
                        device_db_id,
                    )
                    current_os = current_os_row["os_name"] if current_os_row else None
                    if not current_os:
                        os_name = device.os_fingerprint.split('/')[0].strip()
                        if os_name:
                            await conn.execute(
                                "UPDATE discovered_devices SET os_name = $1, sync_updated_at = NOW() WHERE id = $2",
                                os_name, device_db_id,
                            )

                # Upsert compliance check details
                for check in device.compliance_details:
                    details_json = check.details
                    # Parse JSON string to dict if needed for JSONB
                    if isinstance(details_json, str):
                        try:
                            details_json = json.loads(details_json)
                        except (json.JSONDecodeError, TypeError):
                            details_json = None

                    await conn.execute(
                        """
                        INSERT INTO device_compliance_details
                            (discovered_device_id, check_type, hipaa_control, status, details, checked_at)
                        VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                        ON CONFLICT (discovered_device_id, check_type) DO UPDATE SET
                            hipaa_control = EXCLUDED.hipaa_control,
                            status = EXCLUDED.status,
                            details = EXCLUDED.details,
                            checked_at = EXCLUDED.checked_at,
                            synced_at = NOW()
                        """,
                        device_db_id,
                        check.check_type,
                        check.hipaa_control,
                        check.status,
                        json.dumps(details_json) if details_json else None,
                        check.checked_at,
                    )

            except Exception as e:
                errors.append(f"Device {device.device_id}: {str(e)}")

        # Update appliance last check-in timestamp
        await conn.execute(
            """
            UPDATE appliances SET
                last_checkin = NOW()
            WHERE id = $1
            """,
            appliance_db_id,
        )

        # Also update site_appliances so dashboard status stays accurate
        # even when the dedicated checkin endpoint fails
        await conn.execute(
            """
            UPDATE site_appliances SET
                last_checkin = NOW(),
                status = 'online',
                offline_since = NULL,
                offline_notified = false
            WHERE site_id = $1
            """,
            report.site_id,
        )

    # Auto-populate workstations table from discovered workstation/server devices
    try:
        async with admin_connection(pool) as link_conn:
            await _link_devices_to_workstations(link_conn, report.site_id)
    except Exception as e:
        logger.warning(f"Device→workstation linkage failed for {report.site_id}: {e}")

    # Archive devices not seen in 30 days
    try:
        async with admin_connection(pool) as archive_conn:
            await archive_conn.execute("""
                UPDATE discovered_devices
                SET device_status = 'archived'
                WHERE last_seen_at < NOW() - INTERVAL '30 days'
                    AND device_status NOT IN ('ignored', 'archived')
            """)
    except Exception as e:
        logger.warning(f"Auto-archive sweep failed for {report.site_id}: {e}")

    # Auto-update credential IPs when a device's MAC is seen at a new address.
    # Zero-friction: the device identity is the MAC, not the IP. DHCP changes
    # shouldn't break scanning.
    try:
        async with admin_connection(pool) as cred_conn:
            creds = await cred_conn.fetch("""
                SELECT id, convert_from(encrypted_data, 'UTF8') as cred_json
                FROM site_credentials
                WHERE site_id = $1
                  AND credential_type IN ('winrm', 'domain_admin', 'local_admin')
            """, report.site_id)

            for cred in creds:
                try:
                    cred_data = json.loads(cred["cred_json"])
                    cred_host = cred_data.get("host") or cred_data.get("target_host")
                    if not cred_host:
                        continue

                    # Find a discovered device whose old IP matches the credential host
                    # but has moved to a new IP (MAC-verified)
                    for device in report.devices:
                        if not device.mac_address or not device.ip_address:
                            continue
                        # Check if this device was previously at the credential's IP
                        # by looking at the DB record
                        prev = await cred_conn.fetchrow("""
                            SELECT ip_address FROM discovered_devices
                            WHERE site_id = $1 AND mac_address = $2
                              AND ip_address != $3
                            ORDER BY last_seen_at DESC LIMIT 1
                        """, report.site_id, device.mac_address, device.ip_address)

                        if prev and prev["ip_address"] == cred_host and device.ip_address != cred_host:
                            # MAC was at cred_host, now at device.ip_address — update credential
                            cred_data["host"] = device.ip_address
                            new_json = json.dumps(cred_data)
                            await cred_conn.execute("""
                                UPDATE site_credentials
                                SET encrypted_data = convert_to($1, 'UTF8'),
                                    updated_at = NOW()
                                WHERE id = $2
                            """, new_json, cred["id"])
                            logger.info(
                                f"Auto-updated credential {cred['id']} IP: {cred_host} → {device.ip_address} "
                                f"(MAC {device.mac_address}, site {report.site_id})"
                            )
                except Exception as e:
                    logger.debug(f"Credential IP check failed for {cred['id']}: {e}")
    except Exception as e:
        logger.warning(f"Credential IP auto-update failed for {report.site_id}: {e}")

    status = "success"
    message = f"Synced {devices_created} new, {devices_updated} updated"
    if errors:
        status = "partial"
        message += f", {len(errors)} errors"

    return DeviceSyncResponse(
        status=status,
        devices_received=len(report.devices),
        devices_updated=devices_updated,
        devices_created=devices_created,
        message=message,
    )


async def get_site_devices(
    site_id: str,
    device_type: Optional[str] = None,
    compliance_status: Optional[str] = None,
    include_medical: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> List[dict]:
    """
    Get all devices for a site across all appliances.
    """
    pool = await get_pool()

    query = """
        SELECT
            d.*,
            a.host_id as appliance_hostname,
            a.site_id
        FROM discovered_devices d
        JOIN appliances a ON d.appliance_id = a.id
        WHERE a.site_id = $1
    """
    params = [site_id]
    param_idx = 2

    if device_type:
        query += f" AND d.device_type = ${param_idx}"
        params.append(device_type)
        param_idx += 1

    if compliance_status:
        query += f" AND d.compliance_status = ${param_idx}"
        params.append(compliance_status)
        param_idx += 1

    if not include_medical:
        query += " AND d.medical_device = false"

    query += f" ORDER BY d.last_seen_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
    params.extend([limit, offset])

    async with admin_connection(pool) as conn:
        rows = await conn.fetch(query, *params)

        # Fetch agent coverage data for this site
        agents = await conn.fetch(
            "SELECT hostname, ip_address, status, last_heartbeat, agent_version FROM go_agents WHERE site_id = $1",
            site_id,
        )
        agent_by_host = {}
        agent_by_ip = {}
        for a in agents:
            if a["hostname"]:
                agent_by_host[a["hostname"].upper()] = dict(a)
            if a["ip_address"]:
                agent_by_ip[str(a["ip_address"])] = dict(a)

        # Fetch credential coverage (what protocols we can reach)
        creds = await conn.fetch(
            "SELECT credential_name, credential_type, encrypted_data FROM site_credentials WHERE site_id = $1",
            site_id,
        )
        cred_hosts: dict = {}  # ip/host -> list of credential types
        for c in creds:
            raw = c["encrypted_data"]
            if raw:
                try:
                    cd = json.loads(decrypt_credential(raw))
                    host = cd.get("host") or cd.get("target_host")
                    if host:
                        cred_hosts.setdefault(host, []).append(c["credential_type"])
                except (json.JSONDecodeError, TypeError):
                    pass

        # Determine managed subnets from appliance IPs (/24)
        appliance_subnets = set()
        appliance_rows = await conn.fetch(
            "SELECT ip_address FROM appliances WHERE site_id = $1", site_id
        )
        for ar in appliance_rows:
            aip = str(ar["ip_address"] or "")
            if aip and "." in aip:
                # Extract /24 subnet (e.g. "192.168.88" from "192.168.88.241")
                appliance_subnets.add(".".join(aip.split(".")[:3]))

        devices = []
        for row in rows:
            d = dict(row)
            mac = d.get("mac_address")
            d["manufacturer_hint"] = get_manufacturer_hint(mac) if mac else {"manufacturer": None, "device_class": None, "confidence": None}

            # Tag devices as managed/unmanaged based on subnet
            ip = str(d.get("ip_address") or "")
            device_subnet = ".".join(ip.split(".")[:3]) if "." in ip else ""
            d["managed_network"] = device_subnet in appliance_subnets if device_subnet else False

            # Determine agent coverage
            hostname = (d.get("hostname") or "").upper()
            agent = agent_by_host.get(hostname) or agent_by_ip.get(ip)
            cred_types = cred_hosts.get(ip, [])

            coverage = {"level": "none", "methods": [], "agent_version": None, "agent_status": None}
            if agent:
                coverage["level"] = "agent"
                coverage["methods"].append("agent")
                coverage["agent_version"] = agent.get("agent_version")
                coverage["agent_status"] = agent.get("status")
            if any(ct in ("winrm", "domain_admin", "domain_member", "local_admin") for ct in cred_types):
                coverage["methods"].append("winrm")
                if coverage["level"] == "none":
                    coverage["level"] = "remote"
            if any(ct in ("ssh_key", "ssh_password") for ct in cred_types):
                coverage["methods"].append("ssh")
                if coverage["level"] == "none":
                    coverage["level"] = "remote"

            d["agent_coverage"] = coverage
            devices.append(d)
        return devices


async def get_site_device_counts(site_id: str) -> dict:
    """
    Get device count summary for a site.
    """
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Get managed subnets for filtering
        appliance_rows = await conn.fetch(
            "SELECT ip_address FROM appliances WHERE site_id = $1", site_id
        )
        managed_prefixes = []
        for ar in appliance_rows:
            aip = str(ar["ip_address"] or "")
            if aip and "." in aip:
                managed_prefixes.append(".".join(aip.split(".")[:3]) + ".")

        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE d.compliance_status = 'compliant') as compliant,
                COUNT(*) FILTER (WHERE d.compliance_status = 'drifted') as drifted,
                COUNT(*) FILTER (WHERE d.compliance_status = 'unknown') as unknown,
                COUNT(*) FILTER (WHERE d.medical_device = true) as medical,
                COUNT(*) FILTER (WHERE d.device_type = 'workstation') as workstations,
                COUNT(*) FILTER (WHERE d.device_type = 'server') as servers,
                COUNT(*) FILTER (WHERE d.device_type = 'network') as network_devices,
                COUNT(*) FILTER (WHERE d.device_type = 'printer') as printers
            FROM discovered_devices d
            JOIN appliances a ON d.appliance_id = a.id
            WHERE a.site_id = $1
            """,
            site_id,
        )
        return dict(row) if row else {
            "total": 0, "compliant": 0, "drifted": 0, "unknown": 0,
            "medical": 0, "workstations": 0, "servers": 0,
            "network_devices": 0, "printers": 0,
        }


# =============================================================================
# SQL MIGRATION
# =============================================================================

DEVICE_SYNC_MIGRATION = """
-- Add device sync support to Central Command

-- Discovered devices table
CREATE TABLE IF NOT EXISTS discovered_devices (
    id SERIAL PRIMARY KEY,
    appliance_id INTEGER NOT NULL REFERENCES appliances(id) ON DELETE CASCADE,
    local_device_id TEXT NOT NULL,

    -- Device info
    hostname TEXT,
    ip_address TEXT NOT NULL,
    mac_address TEXT,
    device_type TEXT DEFAULT 'unknown',
    os_name TEXT,
    os_version TEXT,

    -- Medical device handling
    medical_device BOOLEAN DEFAULT FALSE,
    scan_policy TEXT DEFAULT 'standard',
    manually_opted_in BOOLEAN DEFAULT FALSE,

    -- Compliance
    compliance_status TEXT DEFAULT 'unknown',
    open_ports INTEGER[] DEFAULT '{}',

    -- Discovery
    discovery_source TEXT DEFAULT 'nmap',
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    last_scan_at TIMESTAMPTZ,

    -- Sync metadata
    sync_created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sync_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Unique constraint: one device per appliance
    UNIQUE(appliance_id, local_device_id)
);

-- Index for common queries
CREATE INDEX IF NOT EXISTS idx_discovered_devices_appliance
    ON discovered_devices(appliance_id);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_compliance
    ON discovered_devices(compliance_status);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_type
    ON discovered_devices(device_type);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_medical
    ON discovered_devices(medical_device) WHERE medical_device = TRUE;

-- Add device sync columns to appliances
ALTER TABLE appliances ADD COLUMN IF NOT EXISTS last_device_sync TIMESTAMPTZ;
ALTER TABLE appliances ADD COLUMN IF NOT EXISTS device_count INTEGER DEFAULT 0;
ALTER TABLE appliances ADD COLUMN IF NOT EXISTS medical_device_count INTEGER DEFAULT 0;
"""


# =============================================================================
# FASTAPI ROUTER
# =============================================================================

device_sync_router = APIRouter(prefix="/api/devices", tags=["devices"])


@device_sync_router.post("/sync", response_model=DeviceSyncResponse)
async def receive_device_sync(report: DeviceSyncReport) -> DeviceSyncResponse:
    """
    Receive device inventory sync from an appliance.

    Called by local-portal on appliances to push discovered devices
    to Central Command for fleet-wide visibility.
    """
    try:
        return await sync_devices(report)
    except Exception as e:
        logger.error(f"Device sync failed: {e}")
        raise HTTPException(status_code=500, detail="Device sync failed. Please try again.")


@device_sync_router.get("/sites/{site_id}")
async def list_site_devices(
    site_id: str,
    device_type: Optional[str] = Query(None, description="Filter by device type"),
    compliance_status: Optional[str] = Query(None, description="Filter by compliance status"),
    include_medical: bool = Query(True, description="Include medical devices"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """
    Get all discovered devices for a site.

    Aggregates devices across all appliances at the site.
    """
    devices = await get_site_devices(
        site_id=site_id,
        device_type=device_type,
        compliance_status=compliance_status,
        include_medical=include_medical,
        limit=limit,
        offset=offset,
    )

    counts = await get_site_device_counts(site_id)

    return {
        "site_id": site_id,
        "devices": devices,
        "counts": counts,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": counts["total"],
        },
    }


@device_sync_router.get("/sites/{site_id}/summary")
async def get_site_device_summary(site_id: str) -> dict:
    """
    Get device inventory summary for a site.

    Returns counts by type, compliance status, and medical device stats.
    """
    pool = await get_pool()
    counts = await get_site_device_counts(site_id)

    total = counts["total"]
    compliant = counts["compliant"]
    drifted = counts["drifted"]
    scanned = compliant + drifted  # Only devices that have been checked

    # Coverage stats
    async with admin_connection(pool) as conn:
        agent_count = await conn.fetchval(
            "SELECT COUNT(DISTINCT hostname) FROM go_agents WHERE site_id = $1",
            site_id,
        ) or 0
        cred_count = await conn.fetchval(
            "SELECT COUNT(*) FROM site_credentials WHERE site_id = $1",
            site_id,
        ) or 0

        # Stale credentials: created more than 90 days ago
        stale_creds_count = await conn.fetchval("""
            SELECT COUNT(*)
            FROM site_credentials
            WHERE site_id = $1
            AND created_at < NOW() - INTERVAL '90 days'
        """, site_id) or 0

        # Network coverage score: devices with agent_active / total non-ignored devices
        coverage_row = await conn.fetchrow("""
            SELECT
                COUNT(*) FILTER (WHERE device_status = 'agent_active') as agent_active,
                COUNT(*) FILTER (WHERE device_status NOT IN ('ignored', 'archived')) as total_non_ignored
            FROM discovered_devices
            WHERE site_id = $1
        """, site_id)

    agent_active = coverage_row["agent_active"] or 0
    total_non_ignored = coverage_row["total_non_ignored"] or 0
    network_coverage_pct = round((agent_active / total_non_ignored * 100), 1) if total_non_ignored > 0 else 0.0
    unmanaged_count = total_non_ignored - agent_active

    return {
        "site_id": site_id,
        "total_devices": total,
        "compliance_rate": round(compliant / scanned * 100, 1) if scanned > 0 else 0.0,
        "by_compliance": {
            "compliant": compliant,
            "drifted": counts["drifted"],
            "unknown": counts["unknown"],
        },
        "by_type": {
            "workstations": counts["workstations"],
            "servers": counts["servers"],
            "network": counts["network_devices"],
            "printers": counts["printers"],
        },
        "medical_devices": {
            "total": counts["medical"],
            "excluded_by_default": True,
        },
        "coverage": {
            "agents_enrolled": agent_count,
            "credentials_configured": cred_count,
        },
        "network_coverage_pct": network_coverage_pct,
        "unmanaged_device_count": unmanaged_count,
        "stale_credentials_count": stale_creds_count,
    }


@device_sync_router.get("/sites/{site_id}/medical")
async def list_medical_devices(
    site_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """
    Get all medical devices for a site.

    Medical devices are excluded from compliance scanning by default
    and require manual opt-in.
    """
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT
                d.*,
                a.host_id as appliance_hostname
            FROM discovered_devices d
            JOIN appliances a ON d.appliance_id = a.id
            WHERE a.site_id = $1 AND d.medical_device = true
            ORDER BY d.last_seen_at DESC
            LIMIT $2 OFFSET $3
            """,
            site_id,
            limit,
            offset,
        )

        total = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM discovered_devices d
            JOIN appliances a ON d.appliance_id = a.id
            WHERE a.site_id = $1 AND d.medical_device = true
            """,
            site_id,
        )

    devices = [dict(row) for row in rows]

    return {
        "site_id": site_id,
        "medical_devices": devices,
        "total": total,
        "note": "Medical devices are excluded from compliance scanning by default for patient safety",
    }


@device_sync_router.get("/sites/{site_id}/device/{device_id}/compliance")
async def get_device_compliance_details(site_id: str, device_id: int) -> dict:
    """
    Get compliance check details for a specific device.

    Returns individual check results with HIPAA control mappings.
    """
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        # Verify device belongs to site
        device = await conn.fetchrow(
            """
            SELECT d.id, d.hostname, d.ip_address, d.compliance_status
            FROM discovered_devices d
            JOIN appliances a ON d.appliance_id = a.id
            WHERE d.id = $1 AND a.site_id = $2
            """,
            device_id,
            site_id,
        )

        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        checks = await conn.fetch(
            """
            SELECT check_type, hipaa_control, status, details, checked_at, synced_at
            FROM device_compliance_details
            WHERE discovered_device_id = $1
            ORDER BY checked_at DESC
            """,
            device_id,
        )

    return {
        "device_id": device_id,
        "hostname": device["hostname"],
        "ip_address": device["ip_address"],
        "compliance_status": device["compliance_status"],
        "checks": [dict(row) for row in checks],
        "total_checks": len(checks),
        "passed": sum(1 for c in checks if c["status"] == "pass"),
        "warned": sum(1 for c in checks if c["status"] == "warn"),
        "failed": sum(1 for c in checks if c["status"] == "fail"),
    }


# =============================================================================
# DEVICE → WORKSTATION LINKAGE
# =============================================================================

async def _link_devices_to_workstations(conn: asyncpg.Connection, site_id: str):
    """Auto-populate the workstations table from discovered_devices.

    When the network scanner discovers devices typed as 'workstation' or 'server',
    upsert them into the workstations table so the Workstations tab shows data.
    Compliance status is derived from recent incidents targeting each device.
    """
    devices = await conn.fetch("""
        SELECT d.id, d.hostname, d.ip_address, d.mac_address,
               d.os_name, d.os_version, d.compliance_status,
               d.last_seen_at, d.device_type
        FROM discovered_devices d
        JOIN appliances a ON d.appliance_id = a.id
        WHERE a.site_id = $1
        AND d.device_type IN ('workstation', 'server')
        AND d.ip_address IS NOT NULL
    """, site_id)

    if not devices:
        return

    # Derive compliance status from incidents: query recent incidents that have
    # hostname in their details JSON (set by Go daemon's incident reporter).
    # Also check incidents keyed by appliance_id for the local NixOS appliance.
    incident_status = await conn.fetch("""
        SELECT
            details->>'hostname' as hostname,
            COUNT(*) FILTER (WHERE status IN ('open', 'resolving')) as open_count,
            COUNT(*) FILTER (WHERE status = 'resolved') as resolved_count,
            MAX(created_at) as last_incident_at
        FROM incidents
        WHERE site_id = $1
        AND created_at > NOW() - INTERVAL '7 days'
        AND details->>'hostname' IS NOT NULL
        GROUP BY details->>'hostname'
    """, site_id)

    # Build lookup: hostname/IP → compliance status + last check time
    compliance_map = {}
    for row in incident_status:
        h = row['hostname']
        if row['open_count'] > 0:
            compliance_map[h] = ('drifted', row['last_incident_at'])
        elif row['resolved_count'] > 0:
            compliance_map[h] = ('compliant', row['last_incident_at'])

    # Site-level fallback: derive compliance per platform from incidents without
    # per-host hostname (pre-backfill data). This gives a reasonable status
    # for devices where we have incident data but no per-host linkage yet.
    platform_status = await conn.fetch("""
        SELECT
            COALESCE(details->>'platform', 'unknown') as platform,
            COUNT(*) FILTER (WHERE status IN ('open', 'resolving')) as open_count,
            COUNT(*) FILTER (WHERE status = 'resolved') as resolved_count,
            MAX(created_at) as last_incident_at
        FROM incidents
        WHERE site_id = $1
        AND created_at > NOW() - INTERVAL '7 days'
        GROUP BY COALESCE(details->>'platform', 'unknown')
    """, site_id)

    platform_map = {}
    for row in platform_status:
        p = row['platform']
        if row['open_count'] > 0:
            platform_map[p] = ('drifted', row['last_incident_at'])
        elif row['resolved_count'] > 0:
            platform_map[p] = ('compliant', row['last_incident_at'])

    upserted = 0
    for dev in devices:
        hostname = dev['hostname'] or dev['ip_address']
        if not hostname:
            continue

        # Check incident-derived status: per-host first, then platform fallback
        ip = dev['ip_address']
        incident_data = compliance_map.get(ip) or compliance_map.get(hostname)
        if not incident_data:
            # Platform fallback: match OS to incident platform
            os_lower = (dev['os_name'] or '').lower()
            if 'windows' in os_lower:
                incident_data = platform_map.get('windows')
            elif 'linux' in os_lower or 'nixos' in os_lower:
                incident_data = platform_map.get('linux')
            elif 'darwin' in os_lower or 'macos' in os_lower or 'apple' in os_lower:
                incident_data = platform_map.get('linux')  # macOS scanned as linux targets

        if incident_data:
            status, last_check = incident_data
        else:
            status = dev['compliance_status'] or 'unknown'
            last_check = None

        # last_seen within 30 min = online
        online = False
        if dev['last_seen_at']:
            ts = dev['last_seen_at']
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            online = age < 1800

        await conn.execute("""
            INSERT INTO workstations (
                site_id, hostname, ip_address, mac_address,
                os_name, os_version, online, last_seen,
                compliance_status, last_compliance_check, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
            ON CONFLICT (site_id, hostname) DO UPDATE SET
                ip_address = COALESCE(EXCLUDED.ip_address, workstations.ip_address),
                mac_address = COALESCE(EXCLUDED.mac_address, workstations.mac_address),
                os_name = COALESCE(EXCLUDED.os_name, workstations.os_name),
                os_version = COALESCE(EXCLUDED.os_version, workstations.os_version),
                online = EXCLUDED.online,
                last_seen = COALESCE(EXCLUDED.last_seen, workstations.last_seen),
                compliance_status = EXCLUDED.compliance_status,
                last_compliance_check = COALESCE(EXCLUDED.last_compliance_check, workstations.last_compliance_check),
                updated_at = NOW()
        """,
            site_id,
            hostname,
            dev['ip_address'],
            dev['mac_address'],
            dev['os_name'],
            dev['os_version'],
            online,
            dev['last_seen_at'],
            status,
            last_check,
        )
        upserted += 1

    if upserted > 0:
        await _update_workstation_summary(conn, site_id)

    logger.info(f"Linked {upserted} discovered devices → workstations for site {site_id}")


async def _update_workstation_summary(conn: asyncpg.Connection, site_id: str):
    """Update the site_workstation_summaries table from current workstation data."""
    import hashlib
    import uuid as _uuid

    stats = await conn.fetchrow("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE online = true) as online,
            COUNT(*) FILTER (WHERE compliance_status = 'compliant') as compliant,
            COUNT(*) FILTER (WHERE compliance_status = 'drifted') as drifted,
            COUNT(*) FILTER (WHERE compliance_status = 'error') as error,
            COUNT(*) FILTER (WHERE compliance_status = 'unknown') as unknown
        FROM workstations WHERE site_id = $1
    """, site_id)

    if not stats or stats['total'] == 0:
        return

    total = stats['total']
    compliance_rate = (stats['compliant'] / total * 100) if total > 0 else 0

    # Generate a deterministic bundle_id and evidence_hash for the summary
    bundle_id = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"ws-summary-{site_id}"))
    evidence_hash = hashlib.sha256(
        f"{site_id}:{total}:{stats['compliant']}:{stats['drifted']}".encode()
    ).hexdigest()

    await conn.execute("""
        INSERT INTO site_workstation_summaries (
            site_id, bundle_id, total_workstations, online_workstations,
            compliant_workstations, drifted_workstations,
            error_workstations, unknown_workstations,
            overall_compliance_rate, check_compliance, evidence_hash, last_scan
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, '{}'::jsonb, $10, NOW())
        ON CONFLICT (site_id) DO UPDATE SET
            total_workstations = EXCLUDED.total_workstations,
            online_workstations = EXCLUDED.online_workstations,
            compliant_workstations = EXCLUDED.compliant_workstations,
            drifted_workstations = EXCLUDED.drifted_workstations,
            error_workstations = EXCLUDED.error_workstations,
            unknown_workstations = EXCLUDED.unknown_workstations,
            overall_compliance_rate = EXCLUDED.overall_compliance_rate,
            evidence_hash = EXCLUDED.evidence_hash,
            last_scan = EXCLUDED.last_scan,
            updated_at = NOW()
    """,
        site_id,
        bundle_id,
        stats['total'],
        stats['online'],
        stats['compliant'],
        stats['drifted'],
        stats['error'],
        stats['unknown'],
        compliance_rate,
        evidence_hash,
    )
