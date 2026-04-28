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
# appliance_id format: `{site_id}-{MAC with colons}` → need to allow ':'
_SAFE_APPLIANCE_ID = re.compile(r"^[a-zA-Z0-9.:_-]{1,160}$")
_SAFE_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _validated_site_id(site_id: str) -> str:
    """Validate site_id is safe for SET LOCAL interpolation."""
    if not _SAFE_SITE_ID.match(site_id):
        raise ValueError(f"Invalid site_id for RLS context: {site_id!r}")
    return site_id


def _validated_appliance_id(aid: str) -> str:
    """Validate appliance_id is safe for SET LOCAL interpolation.
    Appliance IDs contain colons (MAC addresses) so need a wider charset
    than _validated_site_id."""
    if not _SAFE_APPLIANCE_ID.match(aid):
        raise ValueError(f"Invalid appliance_id for RLS context: {aid!r}")
    return aid


@asynccontextmanager
async def tenant_connection(
    pool: asyncpg.Pool,
    site_id: Optional[str] = None,
    is_admin: bool = False,
    actor_appliance_id: Optional[str] = None,
):
    """Acquire a connection with tenant context set for RLS.

    Usage:
        async with tenant_connection(pool, site_id="site-abc123") as conn:
            rows = await conn.fetch("SELECT * FROM incidents")
            # RLS automatically filters to site-abc123

        async with tenant_connection(pool, is_admin=True) as conn:
            rows = await conn.fetch("SELECT * FROM incidents")
            # Admin bypass — sees all rows

        # Session 206 D2: tag the actor appliance so cross-appliance writes
        # can be audit-logged + eventually rejected.
        async with tenant_connection(
            pool, site_id=sid, actor_appliance_id=aid
        ) as conn:
            ...

    Args:
        pool: asyncpg connection pool
        site_id: The tenant's site_id. Required for non-admin contexts.
        is_admin: If True, sets admin bypass (sees all tenants).
        actor_appliance_id: The authenticated appliance for this request —
            used by Migration 197/199's cross-appliance UPDATE trigger to
            detect (and eventually reject) one appliance modifying another's
            row. Callers with access to a request-authenticated appliance
            identity SHOULD pass this.
    """
    async with pool.acquire() as conn:
        if is_admin:
            # Migration 234 flipped the mcp_app role default to 'false',
            # so admin context must be set explicitly. Session-level SET
            # (not SET LOCAL) because we intentionally do NOT wrap admin
            # connections in a transaction — avoids poisoning on long
            # multi-query endpoints (checkins, fleet overview, etc).
            # PgBouncer's server_reset_query = DISCARD ALL clears this
            # before the next borrower acquires the connection.
            await conn.execute("SET app.is_admin TO 'true'")
            try:
                yield conn
            finally:
                try:
                    await conn.execute("RESET app.is_admin")
                except Exception:
                    logger.warning("tenant_connection(is_admin=True) RESET failed")
        elif site_id:
            # Tenant-scoped: wrap in transaction for SET LOCAL scoping
            async with conn.transaction():
                safe_id = _validated_site_id(site_id)
                await conn.execute(
                    f"SET LOCAL app.current_tenant = '{safe_id}'"
                )
                await conn.execute("SET LOCAL app.is_admin = 'false'")
                if actor_appliance_id:
                    safe_aid = _validated_appliance_id(actor_appliance_id)
                    await conn.execute(
                        f"SET LOCAL app.actor_appliance_id = '{safe_aid}'"
                    )
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

    Migration 234 flipped the mcp_app ROLE default of app.is_admin to
    'false' so any path that forgets to set tenant/admin context gets
    zero rows instead of every tenant's rows. That flip means this
    helper can no longer rely on a DB-level default — it must SET the
    parameter itself.

    We use session-level `SET` (not `SET LOCAL`) deliberately:
      - SET LOCAL is transaction-scoped, and wrapping long multi-query
        admin endpoints (fleet dashboards, checkin handlers) in a single
        transaction causes transaction poisoning when one statement fails.
      - Session-level SET persists across transactions on the same
        backend connection — but PgBouncer transaction-pooling mode runs
        `DISCARD ALL` (its default `server_reset_query`) before returning
        a connection to the pool, so the SET does NOT leak to the next
        borrower.
      - Because this path relies on PgBouncer's reset hook, pgbouncer.ini
        MUST keep `server_reset_query = DISCARD ALL` (or a superset).
        Session-pool mode would break the invariant — revisit if pooling
        mode ever changes.

    *** ROUTING-RISK CAVEAT (Session 212 round-table) ***
    PgBouncer transaction-pool mode can route the SET above and a
    SUBSEQUENT autocommit fetch from this conn to DIFFERENT backends.
    The fetch then runs without `app.is_admin='true'` (Migration 234's
    role default is false) and RLS hides every row. Symptom: the call
    returns "no rows" intermittently in production, near-impossible to
    reproduce in dev. This is the bug class that motivated the sigauth
    verify wrap (commit 303421cc) and the new `admin_transaction()`
    helper below. Use `admin_connection` for SINGLE-statement reads
    (where the SET and the read share one pgbouncer transaction);
    use `admin_transaction` for any multi-statement work.

    Usage:
        async with admin_connection(pool) as conn:
            rows = await conn.fetch("SELECT * FROM incidents")  # all tenants
    """
    async with pool.acquire() as conn:
        # Explicit admin opt-in. No transaction wrapper — intentional.
        await conn.execute("SET app.is_admin TO 'true'")
        try:
            yield conn
        finally:
            # Belt-and-suspenders reset. PgBouncer's DISCARD ALL will also
            # clear this, but resetting here protects direct-to-Postgres
            # deployments (tests, local dev without PgBouncer) from
            # leaking admin context to the next borrower of the connection.
            try:
                await conn.execute("RESET app.is_admin")
            except Exception:
                # If reset fails the connection is already being recycled
                # or torn down; no action available here.
                logger.warning("admin_connection RESET failed — connection will be recycled")


@asynccontextmanager
async def admin_transaction(pool: asyncpg.Pool):
    """Transactional admin context — pins a single PgBouncer backend.

    Round-table 2026-04-28 angle 1 F1 P1: every multi-statement read
    via `admin_connection` carries the same routing risk as the
    sigauth verify path that motivated commit 303421cc. PgBouncer in
    transaction-pool mode can route the outer SET and a subsequent
    autocommit fetch to DIFFERENT backends; the fetch then runs
    without `app.is_admin='true'` (Migration 234's role default is
    false) and RLS hides every row. Symptom: the call returns
    "no rows" intermittently in production, near-impossible to
    reproduce in dev. The verify-path 303421cc fix used the pattern
    explicitly inline; this helper centralizes it.

    Use this — NOT `admin_connection` — when you need to issue 2+
    queries against admin context within one logical operation. The
    `SET LOCAL` is txn-scoped and pins to the backend pgbouncer
    assigns to this transaction. Failures inside roll back per the
    Session 205 asyncpg savepoint invariant; if you need partial
    tolerance, nest `async with conn.transaction():` savepoints.

    Single-statement reads (e.g. one `await conn.fetch(...)` and
    nothing else) can stay on `admin_connection` — pgbouncer's
    transaction is the same as the SET's transaction in that case.
    Two+ statements = use `admin_transaction`.

    Usage:
        async with admin_transaction(pool) as conn:
            rows = await conn.fetch("SELECT * FROM incidents")
            counts = await conn.fetch(
                "SELECT site_id, COUNT(*) FROM events GROUP BY 1"
            )
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.is_admin TO 'true'")
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
