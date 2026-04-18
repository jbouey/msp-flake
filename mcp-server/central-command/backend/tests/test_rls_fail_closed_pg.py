"""RLS fail-closed invariant (Migration 234).

This test pins the contract that gives migration 234 its value: a
connection that runs under the mcp_app role WITHOUT any explicit context
setup must see ZERO rows on RLS-protected tables.

The old posture (migration 082): ALTER DATABASE mcp SET app.is_admin='true'.
That meant any SQLAlchemy endpoint through `get_db()` saw every tenant's
rows by default. One missed `WHERE site_id = ...` shipped as a data leak.

The new posture (migration 234): ALTER ROLE mcp_app SET app.is_admin='false'.
Code that forgets the tenant context gets empty results — loud, obvious,
and non-leaky. Admin code must opt in explicitly (admin_connection,
tenant_connection(is_admin=True), or the SQLAlchemy engine `connect`
event listener in shared.py).

Requires PG_TEST_URL pointing at a Postgres with the mcp_app role and an
RLS-protected table. Skipped in unit-mode CI.
"""
from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio


PG_TEST_URL = os.getenv("PG_TEST_URL")
pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres RLS fail-closed test",
)


PREREQ = """
DROP TABLE IF EXISTS t_rls_probe CASCADE;

CREATE TABLE t_rls_probe (
    site_id TEXT,
    payload TEXT
);
ALTER TABLE t_rls_probe ENABLE ROW LEVEL SECURITY;

-- Admin-only policy identical in shape to the ones in migration 078.
CREATE POLICY t_rls_probe_admin_bypass ON t_rls_probe
    FOR ALL
    USING (current_setting('app.is_admin', true)::boolean = true);

INSERT INTO t_rls_probe (site_id, payload) VALUES
    ('site-a', 'data-a'),
    ('site-b', 'data-b'),
    ('site-c', 'data-c');
"""


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ)
        yield c
    finally:
        await c.execute("DROP TABLE IF EXISTS t_rls_probe CASCADE")
        await c.close()


@pytest.mark.asyncio
async def test_admin_role_sees_everything_by_default(conn):
    """The setup/migrator role `mcp` keeps app.is_admin = 'true' — sees rows."""
    rows = await conn.fetch("SELECT site_id FROM t_rls_probe")
    assert len(rows) == 3, (
        "The migrator connection must keep admin context — any loss here "
        "means migrations and admin SQL would go blind"
    )


@pytest.mark.asyncio
async def test_explicit_fail_closed_sees_nothing(conn):
    """Flipping the GUC to 'false' on the current session returns zero rows.

    This is what `ALTER ROLE mcp_app SET app.is_admin='false'` makes the
    default for any connection under that role. We simulate it here by
    flipping the session parameter to match the role-default behavior.
    """
    async with conn.transaction():
        await conn.execute("SET LOCAL app.is_admin = 'false'")
        rows = await conn.fetch("SELECT site_id FROM t_rls_probe")
        assert rows == [], (
            "With app.is_admin = 'false' and no tenant context, an "
            "RLS-protected table MUST return zero rows. Any leak here "
            "means the admin-bypass policy is malformed."
        )


@pytest.mark.asyncio
async def test_opt_in_admin_recovers_visibility(conn):
    """Explicit opt-in `SET app.is_admin = 'true'` sees everything — mirrors
    what admin_connection() does in tenant_middleware.py after migration 234.
    """
    # Simulate the fail-closed baseline.
    await conn.execute("SET app.is_admin TO 'false'")
    rows_closed = await conn.fetch("SELECT site_id FROM t_rls_probe")
    assert rows_closed == [], "baseline must be empty"

    # Explicit admin opt-in (mirrors admin_connection's new behavior).
    await conn.execute("SET app.is_admin TO 'true'")
    rows_open = await conn.fetch("SELECT site_id FROM t_rls_probe")
    assert len(rows_open) == 3, (
        "explicit SET app.is_admin = 'true' MUST restore visibility — "
        "this is the path admin_connection() takes"
    )

    # Reset hygiene (mirrors admin_connection's RESET on exit).
    await conn.execute("RESET app.is_admin")


@pytest.mark.asyncio
async def test_set_local_scope_does_not_leak(conn):
    """SET LOCAL app.is_admin = 'true' inside a transaction does NOT persist
    past COMMIT — this is what tenant_connection(is_admin=True) relies on
    for isolation between requests sharing a pooled connection.
    """
    await conn.execute("SET app.is_admin TO 'false'")

    async with conn.transaction():
        await conn.execute("SET LOCAL app.is_admin = 'true'")
        inside = await conn.fetch("SELECT site_id FROM t_rls_probe")
        assert len(inside) == 3, "inside SET LOCAL block admin must be on"

    # After the transaction, the session-level setting ('false') is back.
    outside = await conn.fetch("SELECT site_id FROM t_rls_probe")
    assert outside == [], (
        "SET LOCAL must not leak past COMMIT — this is the fail-closed "
        "guarantee for tenant_connection"
    )
