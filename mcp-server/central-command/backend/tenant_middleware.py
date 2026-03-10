"""Tenant isolation middleware for Row-Level Security (RLS).

Sets PostgreSQL session variables per-transaction so RLS policies
can enforce multi-tenant data isolation at the database layer.

Architecture:
    Request → Auth → Middleware extracts site_id → SET LOCAL app.current_tenant
    Admin requests → SET LOCAL app.is_admin = true (bypasses tenant filter)
    Agent requests → site_id from appliance lookup

Works with both asyncpg raw pool and SQLAlchemy AsyncSession.
SET LOCAL is transaction-scoped — automatically cleared when connection
returns to pool. Safe with PgBouncer transaction pooling mode.
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)


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
        async with conn.transaction():
            if is_admin:
                await conn.execute("SET LOCAL app.is_admin = 'true'")
                if site_id:
                    await conn.execute(
                        "SET LOCAL app.current_tenant = $1", site_id
                    )
            elif site_id:
                await conn.execute(
                    "SET LOCAL app.current_tenant = $1", site_id
                )
                await conn.execute("SET LOCAL app.is_admin = 'false'")
            else:
                # No tenant context — RLS will return empty results
                # This is intentionally restrictive (fail-closed)
                logger.warning("tenant_connection called without site_id or is_admin")
                await conn.execute("SET LOCAL app.is_admin = 'false'")
                await conn.execute("SET LOCAL app.current_tenant = ''")

            yield conn


@asynccontextmanager
async def admin_connection(pool: asyncpg.Pool):
    """Shortcut for admin-level connections that bypass RLS.

    Usage:
        async with admin_connection(pool) as conn:
            rows = await conn.fetch("SELECT * FROM incidents")  # all tenants
    """
    async with tenant_connection(pool, is_admin=True) as conn:
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
        await conn.execute("SET LOCAL app.current_tenant = $1", site_id)
    else:
        await conn.execute("SET LOCAL app.current_tenant = ''")
