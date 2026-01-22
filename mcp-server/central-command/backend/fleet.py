"""Fleet management queries for Central Command Dashboard.

Provides database queries for multi-tenant fleet aggregation,
client overview, appliance details, and health metrics.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
import asyncpg

from .models import (
    Appliance,
    ClientOverview,
    ClientDetail,
    Incident,
    HealthMetrics,
    ComplianceMetrics,
    CheckType,
    Severity,
    ResolutionLevel,
)
from .metrics import (
    calculate_connectivity_score,
    calculate_compliance_score,
    calculate_overall_health,
    aggregate_health_scores,
)


# =============================================================================
# DATABASE CONNECTION
# =============================================================================

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Get or create the database connection pool."""
    global _pool
    if _pool is None:
        import os
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL environment variable must be set. "
                "Example: postgresql://user:pass@host:5432/dbname"
            )
        # Strip SQLAlchemy async driver suffix for raw asyncpg
        if "+asyncpg" in database_url:
            database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
    return _pool


async def close_pool():
    """Close the database connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _row_to_appliance(row: asyncpg.Record) -> Appliance:
    """Convert a database row to an Appliance model."""
    return Appliance(
        id=row["id"],
        site_id=row["site_id"],
        hostname=row["hostname"],
        ip_address=str(row["ip_address"]) if row["ip_address"] else None,
        agent_version=row.get("agent_version"),
        tier=row.get("tier", "standard"),
        is_online=row.get("is_online", False),
        last_checkin=row.get("last_checkin"),
        created_at=row["created_at"],
    )


def _row_to_incident(row: asyncpg.Record, site_id: str, hostname: str) -> Incident:
    """Convert a database row to an Incident model."""
    return Incident(
        id=row["id"],
        site_id=site_id,
        hostname=hostname,
        check_type=CheckType(row["check_type"]) if row["check_type"] else CheckType.PATCHING,
        severity=Severity(row["severity"]) if row["severity"] else Severity.MEDIUM,
        resolution_level=ResolutionLevel(row["resolution_level"]) if row.get("resolution_level") else None,
        resolved=row.get("resolved_at") is not None,
        resolved_at=row.get("resolved_at"),
        hipaa_controls=row.get("hipaa_controls", []) or [],
        created_at=row["created_at"],
    )


async def _get_appliance_health(
    pool: asyncpg.Pool,
    appliance_id: int,
    last_checkin: Optional[datetime],
) -> HealthMetrics:
    """Calculate health metrics for a single appliance."""
    async with pool.acquire() as conn:
        # Get incident stats
        incident_stats = await conn.fetchrow("""
            SELECT
                COUNT(*) as total_incidents,
                COUNT(*) FILTER (WHERE resolved_at IS NOT NULL) as resolved_incidents
            FROM incidents
            WHERE appliance_id = $1
            AND created_at > NOW() - INTERVAL '30 days'
        """, appliance_id)

        # Get order stats
        order_stats = await conn.fetchrow("""
            SELECT
                COUNT(*) as total_orders,
                COUNT(*) FILTER (WHERE status = 'executed') as executed_orders
            FROM orders
            WHERE appliance_id = $1
            AND created_at > NOW() - INTERVAL '30 days'
        """, appliance_id)

        # Get latest compliance data from drift_data
        compliance_row = await conn.fetchrow("""
            SELECT drift_data
            FROM incidents
            WHERE appliance_id = $1
            ORDER BY created_at DESC
            LIMIT 1
        """, appliance_id)

    # Calculate connectivity
    connectivity = calculate_connectivity_score(
        last_checkin=last_checkin,
        successful_heals=incident_stats["resolved_incidents"] if incident_stats else 0,
        total_incidents=incident_stats["total_incidents"] if incident_stats else 0,
        executed_orders=order_stats["executed_orders"] if order_stats else 0,
        total_orders=order_stats["total_orders"] if order_stats else 0,
    )

    # Calculate compliance (parse from drift_data or default)
    compliance_data = {}
    if compliance_row and compliance_row["drift_data"]:
        drift = compliance_row["drift_data"]
        # Map drift data to compliance checks
        compliance_data = {
            "patching": drift.get("patching_compliant", False),
            "antivirus": drift.get("av_compliant", False),
            "backup": drift.get("backup_compliant", False),
            "logging": drift.get("logging_compliant", False),
            "firewall": drift.get("firewall_compliant", False),
            "encryption": drift.get("encryption_compliant", False),
        }

    compliance = calculate_compliance_score(**compliance_data)

    return calculate_overall_health(connectivity, compliance)


# =============================================================================
# FLEET OVERVIEW
# =============================================================================

async def get_fleet_overview() -> List[ClientOverview]:
    """Get all clients with aggregated health scores.

    Returns:
        List of ClientOverview with health metrics for each site.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Get all unique sites with appliance counts
        sites = await conn.fetch("""
            SELECT
                site_id,
                COUNT(*) as appliance_count,
                COUNT(*) FILTER (WHERE is_online = true) as online_count,
                MAX(last_checkin) as last_checkin
            FROM appliances
            GROUP BY site_id
            ORDER BY site_id
        """)

        if not sites:
            return []

        result = []
        for site in sites:
            site_id = site["site_id"]

            # Get appliances for this site
            appliances = await conn.fetch("""
                SELECT id, last_checkin
                FROM appliances
                WHERE site_id = $1
            """, site_id)

            # Calculate health for each appliance
            health_list = []
            for app in appliances:
                health = await _get_appliance_health(pool, app["id"], app["last_checkin"])
                health_list.append(health)

            # Aggregate health scores
            aggregated_health = aggregate_health_scores(health_list)

            # Get incident count for last 24 hours
            incident_count = await conn.fetchval("""
                SELECT COUNT(*)
                FROM incidents i
                JOIN appliances a ON i.appliance_id = a.id
                WHERE a.site_id = $1
                AND i.created_at > NOW() - INTERVAL '24 hours'
            """, site_id)

            # Get last incident time
            last_incident = await conn.fetchval("""
                SELECT MAX(i.created_at)
                FROM incidents i
                JOIN appliances a ON i.appliance_id = a.id
                WHERE a.site_id = $1
            """, site_id)

            # Create human-readable name from site_id
            name = site_id.replace("-", " ").title()

            result.append(ClientOverview(
                site_id=site_id,
                name=name,
                appliance_count=site["appliance_count"],
                online_count=site["online_count"],
                health=aggregated_health,
                last_incident=last_incident,
                incidents_24h=incident_count or 0,
            ))

    return result


# =============================================================================
# CLIENT DETAIL
# =============================================================================

async def get_client_detail(site_id: str) -> Optional[ClientDetail]:
    """Get detailed view of a single client.

    Args:
        site_id: The site identifier

    Returns:
        ClientDetail with appliances, health, and recent incidents
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Get all appliances for this site
        appliance_rows = await conn.fetch("""
            SELECT *
            FROM appliances
            WHERE site_id = $1
            ORDER BY hostname
        """, site_id)

        if not appliance_rows:
            return None

        # Build appliance list with health
        appliances = []
        health_list = []
        for row in appliance_rows:
            appliance = _row_to_appliance(row)
            health = await _get_appliance_health(pool, row["id"], row["last_checkin"])
            appliance.health = health
            appliances.append(appliance)
            health_list.append(health)

        # Aggregate health
        aggregated_health = aggregate_health_scores(health_list)

        # Get tier (from first appliance)
        tier = appliance_rows[0].get("tier", "standard")

        # Get recent incidents
        incident_rows = await conn.fetch("""
            SELECT i.*, a.hostname
            FROM incidents i
            JOIN appliances a ON i.appliance_id = a.id
            WHERE a.site_id = $1
            ORDER BY i.created_at DESC
            LIMIT 20
        """, site_id)

        recent_incidents = [
            _row_to_incident(row, site_id, row["hostname"])
            for row in incident_rows
        ]

        # Create human-readable name
        name = site_id.replace("-", " ").title()

        return ClientDetail(
            site_id=site_id,
            name=name,
            tier=tier,
            appliances=appliances,
            health=aggregated_health,
            recent_incidents=recent_incidents,
            compliance_breakdown=aggregated_health.compliance,
        )


# =============================================================================
# CLIENT APPLIANCES
# =============================================================================

async def get_client_appliances(site_id: str) -> List[Appliance]:
    """Get all appliances for a client with individual health scores.

    Args:
        site_id: The site identifier

    Returns:
        List of Appliance with health metrics
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        appliance_rows = await conn.fetch("""
            SELECT *
            FROM appliances
            WHERE site_id = $1
            ORDER BY hostname
        """, site_id)

        appliances = []
        for row in appliance_rows:
            appliance = _row_to_appliance(row)
            health = await _get_appliance_health(pool, row["id"], row["last_checkin"])
            appliance.health = health
            appliances.append(appliance)

        return appliances


# =============================================================================
# MOCK DATA (for development/testing)
# =============================================================================

def get_mock_fleet_overview() -> List[ClientOverview]:
    """Return mock fleet data for development."""
    from .metrics import calculate_health_from_raw

    now = datetime.now(timezone.utc)

    return [
        ClientOverview(
            site_id="north-valley-family-practice",
            name="North Valley Family Practice",
            appliance_count=2,
            online_count=2,
            health=calculate_health_from_raw(
                last_checkin=now - timedelta(minutes=2),
                successful_heals=45,
                total_incidents=50,
                executed_orders=48,
                total_orders=50,
                patching=True,
                antivirus=True,
                backup=True,
                logging=True,
                firewall=True,
                encryption=True,
            ),
            last_incident=now - timedelta(hours=4),
            incidents_24h=3,
        ),
        ClientOverview(
            site_id="cedar-medical-group",
            name="Cedar Medical Group",
            appliance_count=3,
            online_count=2,
            health=calculate_health_from_raw(
                last_checkin=now - timedelta(minutes=45),
                successful_heals=20,
                total_incidents=30,
                executed_orders=25,
                total_orders=30,
                patching=True,
                antivirus=True,
                backup=False,  # Backup issue
                logging=True,
                firewall=False,  # Firewall issue
                encryption=True,
            ),
            last_incident=now - timedelta(hours=1),
            incidents_24h=8,
        ),
        ClientOverview(
            site_id="lakeside-pediatrics",
            name="Lakeside Pediatrics",
            appliance_count=1,
            online_count=1,
            health=calculate_health_from_raw(
                last_checkin=now - timedelta(minutes=5),
                successful_heals=30,
                total_incidents=32,
                executed_orders=30,
                total_orders=32,
                patching=True,
                antivirus=True,
                backup=True,
                logging=True,
                firewall=True,
                encryption=True,
            ),
            last_incident=now - timedelta(days=2),
            incidents_24h=0,
        ),
    ]


def get_mock_client_detail(site_id: str) -> Optional[ClientDetail]:
    """Return mock client detail for development."""
    from .metrics import calculate_health_from_raw

    now = datetime.now(timezone.utc)

    if site_id == "north-valley-family-practice":
        health = calculate_health_from_raw(
            last_checkin=now - timedelta(minutes=2),
            successful_heals=45,
            total_incidents=50,
            patching=True,
            antivirus=True,
            backup=True,
            logging=True,
            firewall=True,
            encryption=True,
        )
        return ClientDetail(
            site_id=site_id,
            name="North Valley Family Practice",
            tier="professional",
            appliances=[
                Appliance(
                    id=1,
                    site_id=site_id,
                    hostname="NVFP-DC01",
                    ip_address="192.168.1.10",
                    agent_version="1.2.0",
                    tier="professional",
                    is_online=True,
                    last_checkin=now - timedelta(minutes=2),
                    health=health,
                    created_at=now - timedelta(days=90),
                ),
                Appliance(
                    id=2,
                    site_id=site_id,
                    hostname="NVFP-FS01",
                    ip_address="192.168.1.11",
                    agent_version="1.2.0",
                    tier="professional",
                    is_online=True,
                    last_checkin=now - timedelta(minutes=5),
                    health=health,
                    created_at=now - timedelta(days=90),
                ),
            ],
            health=health,
            recent_incidents=[
                Incident(
                    id=101,
                    site_id=site_id,
                    hostname="NVFP-DC01",
                    check_type=CheckType.BACKUP,
                    severity=Severity.MEDIUM,
                    resolution_level=ResolutionLevel.L1,
                    resolved=True,
                    resolved_at=now - timedelta(hours=4),
                    hipaa_controls=["164.308(a)(7)"],
                    created_at=now - timedelta(hours=4, minutes=5),
                ),
            ],
            compliance_breakdown=health.compliance,
        )

    return None
