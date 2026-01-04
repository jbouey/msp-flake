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
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .fleet import get_pool


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


class DiscoveryReport(BaseModel):
    """Report from appliance after running discovery."""
    scan_id: str
    site_id: str
    appliance_id: str
    network_range: str
    assets: List[DiscoveredAsset]
    duration_seconds: float
    errors: List[str] = []


class ScanStatus(BaseModel):
    """Update scan status."""
    scan_id: str
    status: str  # running, completed, failed
    error_message: Optional[str] = None
    assets_found: int = 0
    new_assets: int = 0


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

@router.post("/report")
async def report_discovery_results(report: DiscoveryReport):
    """Receive discovery results from an appliance.

    Called by the compliance agent after completing a network scan.
    Updates the discovered_assets table with findings.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Verify scan exists and get site internal ID
        scan = await conn.fetchrow("""
            SELECT ds.id, ds.site_id, s.site_id as site_id_str
            FROM discovery_scans ds
            JOIN sites s ON s.id = ds.site_id
            WHERE ds.id = $1
        """, report.scan_id)

        if not scan:
            raise HTTPException(status_code=404, detail="Scan not found")

        # Verify site_id matches
        if scan['site_id_str'] != report.site_id:
            raise HTTPException(status_code=400, detail="Site ID mismatch")

        site_internal_id = scan['site_id']
        new_count = 0
        updated_count = 0

        # Process each discovered asset
        for asset in report.assets:
            # Validate IP
            try:
                ip = ipaddress.ip_address(asset.ip_address)
            except ValueError:
                continue  # Skip invalid IPs

            # Auto-classify if not provided
            if not asset.asset_type:
                asset_type, confidence = classify_asset(
                    asset.open_ports,
                    asset.hostname,
                    asset.ad_info
                )
            else:
                asset_type = asset.asset_type
                confidence = asset.confidence

            # Detect services
            services = asset.detected_services or detect_services(asset.open_ports)

            # Upsert asset
            result = await conn.execute("""
                INSERT INTO discovered_assets (
                    site_id, ip_address, hostname, mac_address,
                    asset_type, os_info, confidence, discovery_method,
                    open_ports, detected_services, ad_info,
                    last_seen_at, monitoring_status
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NOW(), 'discovered')
                ON CONFLICT (site_id, ip_address) DO UPDATE SET
                    hostname = COALESCE(EXCLUDED.hostname, discovered_assets.hostname),
                    mac_address = COALESCE(EXCLUDED.mac_address, discovered_assets.mac_address),
                    asset_type = CASE
                        WHEN EXCLUDED.confidence > discovered_assets.confidence
                        THEN EXCLUDED.asset_type
                        ELSE discovered_assets.asset_type
                    END,
                    os_info = COALESCE(EXCLUDED.os_info, discovered_assets.os_info),
                    confidence = GREATEST(EXCLUDED.confidence, discovered_assets.confidence),
                    open_ports = EXCLUDED.open_ports,
                    detected_services = EXCLUDED.detected_services,
                    ad_info = COALESCE(EXCLUDED.ad_info, discovered_assets.ad_info),
                    last_seen_at = NOW(),
                    updated_at = NOW()
            """,
                site_internal_id,
                asset.ip_address,
                asset.hostname,
                asset.mac_address,
                asset_type,
                asset.os_info,
                confidence,
                asset.discovery_method,
                asset.open_ports,
                services,
                asset.ad_info
            )

            if "INSERT" in result:
                new_count += 1
            else:
                updated_count += 1

        # Update scan record
        error_msg = "; ".join(report.errors) if report.errors else None
        status = "failed" if report.errors and not report.assets else "completed"

        await conn.execute("""
            UPDATE discovery_scans
            SET status = $1,
                completed_at = NOW(),
                network_range_scanned = $2,
                assets_found = $3,
                new_assets = $4,
                changed_assets = $5,
                error_message = $6,
                scan_log = $7
            WHERE id = $8
        """,
            status,
            report.network_range,
            len(report.assets),
            new_count,
            updated_count,
            error_msg,
            {"duration_seconds": report.duration_seconds, "errors": report.errors},
            report.scan_id
        )

        # Mark any assets not seen in this scan as potentially missing
        # (only if this was a full scan)
        await conn.execute("""
            UPDATE discovered_assets
            SET monitoring_status = CASE
                WHEN monitoring_status = 'monitored' THEN 'unreachable'
                ELSE monitoring_status
            END
            WHERE site_id = $1
            AND last_seen_at < NOW() - INTERVAL '1 hour'
            AND monitoring_status NOT IN ('ignored', 'unreachable')
        """, site_internal_id)

    return {
        "status": "processed",
        "scan_id": report.scan_id,
        "assets_processed": len(report.assets),
        "new_assets": new_count,
        "updated_assets": updated_count,
    }


@router.post("/status")
async def update_scan_status(status: ScanStatus):
    """Update the status of a running discovery scan.

    Called by appliance to report scan progress or completion.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        if status.status == "completed":
            result = await conn.execute("""
                UPDATE discovery_scans
                SET status = $1,
                    completed_at = NOW(),
                    assets_found = $2,
                    new_assets = $3,
                    error_message = $4
                WHERE id = $5
            """,
                status.status,
                status.assets_found,
                status.new_assets,
                status.error_message,
                status.scan_id
            )
        else:
            result = await conn.execute("""
                UPDATE discovery_scans
                SET status = $1, error_message = $2
                WHERE id = $3
            """, status.status, status.error_message, status.scan_id)

        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="Scan not found")

    return {"status": "updated", "scan_id": status.scan_id}


@router.get("/pending/{site_id}")
async def get_pending_scans(site_id: str):
    """Get pending discovery scans for a site.

    Called by appliance during sync to check for triggered scans.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
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
async def get_asset_summary(site_id: str):
    """Get asset summary for a site.

    Returns counts by type and status for dashboard display.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
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
