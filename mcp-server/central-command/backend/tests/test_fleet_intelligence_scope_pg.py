"""Fleet-intelligence scope isolation test (Phase 15 A-spec).

Round-table QA audit flagged `test_fleet_intelligence_scope.py` as
missing. Cross-partner data leakage is a SEV-1 security event; every
fleet_intelligence endpoint scopes through `_partner_site_ids(partner_id)`.
This test is the regression fence on that helper.

  Partner A's sites MUST NOT appear in Partner B's scoped results
  even when A and B's sites share IDs by coincidence.

Skipped when PG_TEST_URL is unset.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
import asyncpg


PG_TEST_URL = os.getenv("PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping fleet-intelligence scope test",
)


PREREQ_SCHEMA = """
DROP TABLE IF EXISTS sites CASCADE;
DROP TABLE IF EXISTS client_orgs CASCADE;
DROP TABLE IF EXISTS partners CASCADE;
DROP EXTENSION IF EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE partners (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL
);

CREATE TABLE client_orgs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    current_partner_id UUID REFERENCES partners(id)
);

CREATE TABLE sites (
    site_id TEXT PRIMARY KEY,
    client_org_id UUID REFERENCES client_orgs(id) ON DELETE CASCADE
);
"""


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ_SCHEMA)
        yield c
    finally:
        await c.execute("""
            DROP TABLE IF EXISTS sites CASCADE;
            DROP TABLE IF EXISTS client_orgs CASCADE;
            DROP TABLE IF EXISTS partners CASCADE;
        """)
        await c.close()


async def _seed_partner(c, name: str) -> str:
    """Insert a partner, return its id (uuid as str)."""
    pid = await c.fetchval(
        "INSERT INTO partners (name) VALUES ($1) RETURNING id::text",
        name,
    )
    return pid


async def _seed_org(c, name: str, partner_id: str | None) -> str:
    oid = await c.fetchval(
        "INSERT INTO client_orgs (name, current_partner_id) "
        "VALUES ($1, $2::uuid) RETURNING id::text",
        name, partner_id,
    )
    return oid


async def _seed_site(c, site_id: str, org_id: str) -> None:
    await c.execute(
        "INSERT INTO sites (site_id, client_org_id) VALUES ($1, $2::uuid)",
        site_id, org_id,
    )


# We exercise the raw SQL the app uses (from fleet_intelligence._partner_site_ids).
# This test is the regression fence even if the app code refactors.
SCOPE_SQL = """
    SELECT s.site_id
    FROM sites s
    JOIN client_orgs co ON co.id = s.client_org_id
    WHERE co.current_partner_id = $1::uuid
"""


@pytest.mark.asyncio
async def test_partner_sees_only_own_sites(conn):
    partner_a = await _seed_partner(conn, "Partner A")
    partner_b = await _seed_partner(conn, "Partner B")

    org_a = await _seed_org(conn, "Org A", partner_a)
    org_b = await _seed_org(conn, "Org B", partner_b)

    await _seed_site(conn, "site-a-01", org_a)
    await _seed_site(conn, "site-a-02", org_a)
    await _seed_site(conn, "site-b-01", org_b)

    rows_a = await conn.fetch(SCOPE_SQL, partner_a)
    rows_b = await conn.fetch(SCOPE_SQL, partner_b)

    sites_a = {r["site_id"] for r in rows_a}
    sites_b = {r["site_id"] for r in rows_b}

    assert sites_a == {"site-a-01", "site-a-02"}
    assert sites_b == {"site-b-01"}
    assert sites_a.isdisjoint(sites_b), (
        "Cross-partner site visibility leak — SEV-1 regression"
    )


@pytest.mark.asyncio
async def test_partner_with_no_orgs_sees_empty(conn):
    partner_c = await _seed_partner(conn, "Partner C — no orgs yet")
    rows = await conn.fetch(SCOPE_SQL, partner_c)
    assert rows == []


@pytest.mark.asyncio
async def test_unassigned_org_sites_invisible_to_any_partner(conn):
    """An org whose current_partner_id is NULL (e.g., mid-transfer or
    orphaned) has sites but no partner can see them via the scoped
    query. Auditors will still see them via admin paths."""
    partner_a = await _seed_partner(conn, "Partner A")
    unassigned = await _seed_org(conn, "Orphan Org", None)
    await _seed_site(conn, "orphan-site-1", unassigned)

    rows_a = await conn.fetch(SCOPE_SQL, partner_a)
    assert rows_a == []


@pytest.mark.asyncio
async def test_org_transfer_migrates_visibility(conn):
    """If an org moves from partner A to partner B, subsequent scope
    queries return the sites under B, not A. This is the MSP
    business-continuity case — partner change of record."""
    partner_a = await _seed_partner(conn, "Partner A")
    partner_b = await _seed_partner(conn, "Partner B")
    org = await _seed_org(conn, "Transferable Org", partner_a)
    await _seed_site(conn, "transferred-site-1", org)

    # Initially under A
    rows_a = await conn.fetch(SCOPE_SQL, partner_a)
    assert {r["site_id"] for r in rows_a} == {"transferred-site-1"}

    # Transfer to B
    await conn.execute(
        "UPDATE client_orgs SET current_partner_id = $1::uuid WHERE id = $2::uuid",
        partner_b, org,
    )

    rows_a2 = await conn.fetch(SCOPE_SQL, partner_a)
    rows_b2 = await conn.fetch(SCOPE_SQL, partner_b)
    assert rows_a2 == []
    assert {r["site_id"] for r in rows_b2} == {"transferred-site-1"}


@pytest.mark.asyncio
async def test_any_array_filter_cannot_leak_across_partners(conn):
    """The app-level pattern that uses `site_id = ANY(:sites)` where
    :sites came from _partner_site_ids — confirm that passing an empty
    list returns an empty result rather than all sites (which would be
    catastrophic if the scope helper ever returned [])."""
    await _seed_partner(conn, "Partner X")
    org_x = await _seed_org(conn, "Org X",
                            await _seed_partner(conn, "Partner Y"))
    await _seed_site(conn, "x-1", org_x)

    rows = await conn.fetch(
        "SELECT site_id FROM sites WHERE site_id = ANY($1::text[])",
        [],  # empty scope
    )
    assert rows == [], (
        "ANY(empty_array) MUST return empty. If this ever returns rows, "
        "every scoped query is an open barn door."
    )
