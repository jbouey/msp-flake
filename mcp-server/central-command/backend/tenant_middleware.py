"""Tenant isolation middleware for Row-Level Security (RLS).

Sets PostgreSQL session variables per-transaction so RLS policies
can enforce multi-tenant data isolation at the database layer.

Architecture:
    admin_connection: No transaction wrapper — relies on database default
        app.is_admin = 'true'. Used for admin dashboard, internal operations.
    tenant_connection: Wraps in transaction with SET LOCAL to enforce
        tenant isolation (is_admin='false', current_tenant=site_id).

Works with PgBouncer transaction pooling mode.
"""

import logging
import re
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

# SET LOCAL doesn't support $1 parameterized queries in PostgreSQL.
# Validate site_id to prevent SQL injection before interpolating.
_SAFE_SITE_ID = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")
_SAFE_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _validated_site_id(site_id: str) -> str:
    """Validate site_id is safe for SET LOCAL interpolation."""
    if not _SAFE_SITE_ID.match(site_id):
        raise ValueError(f"Invalid site_id for RLS context: {site_id!r}")
    return site_id


@asynccontextmanager
async def tenant_connection(
    pool: asyncpg.Pool,
    site_id: Optional[str] = None,
    is_admin: bool = False,
):
    """Acquire a connection with tenant context set for RLS.

    Usage:
        async with tenant_connection(pool, site_id="site-abc123") as conn:
            rows = await conn.fetch("SELECT * FROM incidents")
            # RLS automatically filters to site-abc123

        async with tenant_connection(pool, is_admin=True) as conn:
            rows = await conn.fetch("SELECT * FROM incidents")
            # Admin bypass — sees all rows

    Args:
        pool: asyncpg connection pool
        site_id: The tenant's site_id. Required for non-admin contexts.
        is_admin: If True, sets admin bypass (sees all tenants).
    """
    async with pool.acquire() as conn:
        if is_admin:
            # Database default is app.is_admin='true', so no SET needed.
            # No transaction wrapper — avoids transaction poisoning for
            # long multi-query endpoints (checkins, fleet overview, etc).
            yield conn
        elif site_id:
            # Tenant-scoped: wrap in transaction for SET LOCAL scoping
            async with conn.transaction():
                safe_id = _validated_site_id(site_id)
                await conn.execute(
                    f"SET LOCAL app.current_tenant = '{safe_id}'"
                )
                await conn.execute("SET LOCAL app.is_admin = 'false'")
                yield conn
        else:
            # No tenant context — RLS will return empty results
            async with conn.transaction():
                logger.warning("tenant_connection called without site_id or is_admin")
                await conn.execute("SET LOCAL app.is_admin = 'false'")
                await conn.execute("SET LOCAL app.current_tenant = ''")
                yield conn


@asynccontextmanager
async def admin_connection(pool: asyncpg.Pool):
    """Shortcut for admin-level connections that bypass RLS.

    No transaction wrapper — relies on database default app.is_admin='true'.
    This avoids transaction poisoning for multi-query admin endpoints.

    Usage:
        async with admin_connection(pool) as conn:
            rows = await conn.fetch("SELECT * FROM incidents")  # all tenants
    """
    async with pool.acquire() as conn:
        yield conn


def _validated_org_id(org_id: str) -> str:
    """Validate org_id (UUID) is safe for SET LOCAL interpolation."""
    org_str = str(org_id)
    if not _SAFE_UUID.match(org_str):
        raise ValueError(f"Invalid org_id for RLS context: {org_str!r}")
    return org_str


@asynccontextmanager
async def org_connection(
    pool: asyncpg.Pool,
    org_id: str,
):
    """Acquire a connection with org-level tenant context for RLS.

    Sets app.is_admin='false' and app.current_org=org_id.
    RLS policies on site-level tables use:
        site_id IN (SELECT site_id FROM sites WHERE client_org_id = current_org)
    Tables with direct org_id columns (e.g. client_escalation_preferences)
    check client_org_id = current_org.

    Usage:
        async with org_connection(pool, org_id=user["org_id"]) as conn:
            rows = await conn.fetch("SELECT * FROM incidents")
            # RLS filters to sites owned by this org
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            safe_org = _validated_org_id(org_id)
            await conn.execute(f"SET LOCAL app.current_org = '{safe_org}'")
            await conn.execute("SET LOCAL app.is_admin = 'false'")
            await conn.execute("SET LOCAL app.current_tenant = ''")
            yield conn


async def set_tenant_context(
    conn: asyncpg.Connection,
    site_id: Optional[str] = None,
    is_admin: bool = False,
):
    """Set tenant context on an existing connection (within a transaction).

    Use this when you already have a connection and transaction open,
    e.g., inside a savepoint or nested transaction block.

    IMPORTANT: Must be called within an active transaction for SET LOCAL to work.
    """
    if is_admin:
        await conn.execute("SET LOCAL app.is_admin = 'true'")
    else:
        await conn.execute("SET LOCAL app.is_admin = 'false'")

    if site_id:
        safe_id = _validated_site_id(site_id)
        await conn.execute(f"SET LOCAL app.current_tenant = '{safe_id}'")
    else:
        await conn.execute("SET LOCAL app.current_tenant = ''")
