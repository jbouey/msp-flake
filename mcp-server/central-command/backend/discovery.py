"""Network discovery module.

Provides functions for network discovery and asset classification.
Called by the appliance agent to discover and classify network assets.

Discovery flow:
1. Partner triggers discovery via /api/partners/me/sites/{site_id}/discovery/trigger
2. Discovery scan record created with status='running'
3. Appliance picks up the order on next sync
4. Appliance runs network discovery locally
5. Appliance reports results back via /api/discovery/report
6. Results stored in discovered_assets table
"""

import ipaddress
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from fastapi import Request  # noqa: F401 — used by appliance-auth handlers below

from .fleet import get_pool
from .tenant_middleware import admin_connection, admin_transaction
from .shared import require_appliance_bearer, _enforce_site_id


router = APIRouter(prefix="/api/discovery", tags=["discovery"])


# =============================================================================
# MODELS
# =============================================================================

class DiscoveredAsset(BaseModel):
    """A discovered network asset."""
    ip_address: str
    hostname: Optional[str] = None
    mac_address: Optional[str] = None
    asset_type: Optional[str] = None  # domain_controller, sql_server, file_server, etc.
    os_info: Optional[str] = None
    confidence: float = 0.5
    discovery_method: str = "port_scan"  # port_scan, ad_query, dns_srv, netbios, manual
    open_ports: List[int] = []
    detected_services: Dict[str, str] = {}  # {port: service_name}
    ad_info: Optional[Dict[str, Any]] = None


# Session 220 task #120 PR-A (2026-05-12): DiscoveryReport + ScanStatus
# models and their report_discovery_results + update_scan_status
# handlers were deleted. Both endpoints were silently CSRF-403'd
# for their entire lifetime (no daemon caller — verified
# `grep -rn discovery/report appliance/` empty); zero real traffic
# in 24h prod logs (only loopback verification probes). Discovery
# results never flowed through these handlers in production.


# =============================================================================
# SERVICE CLASSIFICATION
# =============================================================================

# Port -> Service mapping
PORT_SERVICES = {
    21: "ftp",
    22: "ssh",
    23: "telnet",
    25: "smtp",
    53: "dns",
    80: "http",
    88: "kerberos",
    110: "pop3",
    135: "rpc",
    137: "netbios-ns",
    138: "netbios-dgm",
    139: "netbios-ssn",
    143: "imap",
    389: "ldap",
    443: "https",
    445: "smb",
    464: "kpasswd",
    465: "smtps",
    514: "syslog",
    587: "submission",
    636: "ldaps",
    993: "imaps",
    995: "pop3s",
    1433: "mssql",
    1434: "mssql-browser",
    3268: "gc",  # Global Catalog
    3269: "gcs",  # Global Catalog SSL
    3306: "mysql",
    3389: "rdp",
    5432: "postgresql",
    5985: "winrm",
    5986: "winrm-ssl",
    8080: "http-alt",
    8443: "https-alt",
}


def classify_asset(
    open_ports: List[int],
    hostname: Optional[str] = None,
    ad_info: Optional[Dict[str, Any]] = None
) -> tuple[str, float]:
    """Classify an asset based on its ports and hostname.

    Returns:
        (asset_type, confidence)
    """
    port_set = set(open_ports)

    # Domain Controller: LDAP + Kerberos + DNS + Global Catalog
    dc_ports = {88, 389, 636, 3268}
    if len(dc_ports & port_set) >= 3:
        return ("domain_controller", 0.95)

    # Also check hostname patterns for DC
    if hostname:
        hostname_lower = hostname.lower()
        if any(x in hostname_lower for x in ['dc', 'domain', 'ad-']):
            if 389 in port_set or 88 in port_set:
                return ("domain_controller", 0.85)

    # SQL Server
    if 1433 in port_set:
        confidence = 0.9 if 1434 in port_set else 0.85
        return ("sql_server", confidence)

    # Backup Server (common ports)
    backup_indicators = {
        9392,   # Veeam
        10000,  # Acronis
        5671,   # Datto
        6106,   # BackupExec
    }
    if backup_indicators & port_set:
        return ("backup_server", 0.85)

    # File Server: SMB + possibly RPC
    if 445 in port_set:
        if hostname:
            hostname_lower = hostname.lower()
            if any(x in hostname_lower for x in ['file', 'share', 'nas', 'stor']):
                return ("file_server", 0.85)
        # High SMB traffic is file server indicator
        return ("file_server", 0.6)

    # Exchange Server
    if 25 in port_set and 443 in port_set and 587 in port_set:
        return ("exchange_server", 0.8)

    # Print Server
    if 631 in port_set or 9100 in port_set:
        return ("print_server", 0.8)

    # Web Server
    if 80 in port_set or 443 in port_set:
        if 3389 not in port_set:  # Not RDP = probably server
            return ("web_server", 0.6)

    # Workstation: RDP but few server ports
    if 3389 in port_set:
        server_ports = {25, 53, 80, 88, 389, 443, 445, 1433}
        if len(server_ports & port_set) <= 2:
            return ("workstation", 0.7)

    # Unknown
    return ("unknown", 0.3)


def detect_services(open_ports: List[int]) -> Dict[str, str]:
    """Map open ports to service names."""
    return {
        str(port): PORT_SERVICES.get(port, f"unknown-{port}")
        for port in open_ports
    }


# =============================================================================
# API ENDPOINTS
# =============================================================================

# Session 220 task #120 PR-A (2026-05-12): two POST handlers deleted —
#   - report_discovery_results @router.post("/report")
#     (zero daemon callers in production; 24h log silence confirms)
#   - update_scan_status      @router.post("/status")
#     (zero daemon callers; CSRF-403'd for entire lifetime)
# Their Pydantic models (DiscoveryReport, ScanStatus) deleted above.
# The /api/discovery/* GET endpoints (pending, assets summary) remain.


@router.get("/pending/{site_id}")
async def get_pending_scans(
    site_id: str,
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Get pending discovery scans for a site. Appliance-only.

    Called by appliance during sync to check for triggered scans.

    Pre-fix (Session 220 RT-Auth-2026-05-12 zero-auth audit P2): no
    auth dependency. Anonymous callers could probe whether scans are
    pending for any caller-supplied site_id (tenant probe). The bearer
    site_id MUST match the path site_id to prevent cross-site probing.
    """
    await _enforce_site_id(auth_site_id, site_id, "discovery_pending")
    pool = await get_pool()

    async with admin_connection(pool) as conn:
        scans = await conn.fetch("""
            SELECT ds.id, ds.scan_type, ds.triggered_by, ds.started_at,
                   s.site_id as site_id_str
            FROM discovery_scans ds
            JOIN sites s ON s.id = ds.site_id
            WHERE s.site_id = $1 AND ds.status = 'running'
            ORDER BY ds.started_at DESC
        """, site_id)

        return {
            "pending_scans": [
                {
                    "scan_id": str(s['id']),
                    "scan_type": s['scan_type'],
                    "triggered_by": s['triggered_by'],
                    "started_at": s['started_at'].isoformat(),
                }
                for s in scans
            ],
            "count": len(scans),
        }


@router.get("/assets/{site_id}/summary")
async def get_asset_summary(
    site_id: str,
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Get asset summary for a site. Appliance-only.

    Returns counts by type and status for dashboard display.

    Pre-fix (Session 220 RT-Auth-2026-05-12 zero-auth audit P2): no
    auth dependency. Anonymous callers could enumerate per-site asset
    inventory aggregates by caller-supplied site_id.
    """
    await _enforce_site_id(auth_site_id, site_id, "discovery_assets_summary")
    pool = await get_pool()

    # wave-12: 4-DB-call read pinned to single PgBouncer backend (Session 212 routing-pathology rule).
    async with admin_transaction(pool) as conn:
        # Get site internal ID
        site = await conn.fetchrow("""
            SELECT id FROM sites WHERE site_id = $1
        """, site_id)

        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        # Count by type
        by_type = await conn.fetch("""
            SELECT asset_type, COUNT(*) as count
            FROM discovered_assets
            WHERE site_id = $1
            GROUP BY asset_type
        """, site['id'])

        # Count by status
        by_status = await conn.fetch("""
            SELECT monitoring_status, COUNT(*) as count
            FROM discovered_assets
            WHERE site_id = $1
            GROUP BY monitoring_status
        """, site['id'])

        # Total and critical assets
        totals = await conn.fetchrow("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE asset_type IN ('domain_controller', 'sql_server', 'backup_server')) as critical,
                COUNT(*) FILTER (WHERE monitoring_status = 'monitored') as monitored,
                COUNT(*) FILTER (WHERE monitoring_status = 'unreachable') as unreachable,
                MAX(last_seen_at) as last_scan
            FROM discovered_assets
            WHERE site_id = $1
        """, site['id'])

        return {
            "site_id": site_id,
            "total_assets": totals['total'],
            "critical_assets": totals['critical'],
            "monitored_assets": totals['monitored'],
            "unreachable_assets": totals['unreachable'],
            "last_scan": totals['last_scan'].isoformat() if totals['last_scan'] else None,
            "by_type": {r['asset_type'] or 'unknown': r['count'] for r in by_type},
            "by_status": {r['monitoring_status']: r['count'] for r in by_status},
        }
