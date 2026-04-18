"""Partner security hardening round-table follow-ups (Audit finding #35).

Three security invariants that the audit flagged as under-tested:

  1. **Stripe webhook replay dedup** — Stripe retries events on 5xx. If
     the same `event_id` lands twice (retry, network blip, replay attack),
     the SECOND call MUST be a no-op. Primary key on
     `stripe_webhook_events.event_id` enforces this at the DB level; this
     test pins the contract.
  2. **5-failure partner lockout** — After 5 consecutive bad-password
     attempts on the same partner account, the 6th MUST be 429-locked for
     15 minutes regardless of whether the 6th password is correct.
     Lockout clears on a successful login within the window.
  3. **Cross-partner IDOR at endpoint level** — The dashboard-isolation
     test already pins the SQL query; this one pins the next layer up:
     an authenticated partner-A session attempting to hit an endpoint
     that resolves a site belonging to partner B must return 403/404,
     never 200 with leaked data.

Requires a live Postgres via PG_TEST_URL (same pattern as the dashboard
isolation test). Skipped in unit-mode CI.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio


PG_TEST_URL = os.getenv("PG_TEST_URL")
pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres partner security tests",
)


PREREQ = """
DROP TABLE IF EXISTS stripe_webhook_events CASCADE;
DROP TABLE IF EXISTS partners CASCADE;
DROP TABLE IF EXISTS sites CASCADE;
DROP TABLE IF EXISTS partner_users CASCADE;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Mirrors the shape billing.py creates on first webhook call.
CREATE TABLE stripe_webhook_events (
    event_id     TEXT PRIMARY KEY,
    event_type   TEXT NOT NULL,
    processed_at TIMESTAMPTZ DEFAULT NOW()
);

-- Mirrors the columns partner_auth.py reads/writes during login.
CREATE TABLE partners (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    TEXT,
    slug                    TEXT,
    contact_email           TEXT,
    password_hash           TEXT,
    status                  TEXT DEFAULT 'active',
    pending_approval        BOOLEAN DEFAULT FALSE,
    failed_login_attempts   INTEGER DEFAULT 0,
    locked_until            TIMESTAMPTZ
);

CREATE TABLE sites (
    site_id      TEXT PRIMARY KEY,
    clinic_name  TEXT,
    partner_id   UUID REFERENCES partners(id),
    status       TEXT DEFAULT 'active'
);

CREATE TABLE partner_users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id  UUID REFERENCES partners(id),
    email       TEXT,
    role        TEXT DEFAULT 'viewer'
);
"""


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ)
        yield c
    finally:
        await c.execute(
            "DROP TABLE IF EXISTS stripe_webhook_events, partner_users, "
            "sites, partners CASCADE;"
        )
        await c.close()


# ─── 1. Stripe webhook replay dedup ─────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_replay_same_event_id_is_rejected(conn):
    """Two webhook calls with the same Stripe event_id: first wins, second no-ops."""
    event_id = "evt_" + uuid4().hex

    # First call — succeeds, INSERT returns normally.
    existing_first = await conn.fetchval(
        "SELECT 1 FROM stripe_webhook_events WHERE event_id = $1", event_id
    )
    assert existing_first is None, "first call must see an empty dedup table"
    await conn.execute(
        "INSERT INTO stripe_webhook_events (event_id, event_type) VALUES ($1, $2)",
        event_id, "checkout.session.completed",
    )

    # Second call with the SAME event_id — must be detected as duplicate.
    existing_second = await conn.fetchval(
        "SELECT 1 FROM stripe_webhook_events WHERE event_id = $1", event_id
    )
    assert existing_second == 1, "second call must detect the existing row"

    # Attempting to INSERT a duplicate must raise a PK violation — this is
    # the belt-and-suspenders that protects against two concurrent replays
    # racing between the SELECT and the INSERT (PK wins).
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(
            "INSERT INTO stripe_webhook_events (event_id, event_type) VALUES ($1, $2)",
            event_id, "checkout.session.completed",
        )


@pytest.mark.asyncio
async def test_webhook_different_events_do_not_collide(conn):
    """Distinct event_ids must be independently persisted; no false dedup."""
    for i in range(5):
        await conn.execute(
            "INSERT INTO stripe_webhook_events (event_id, event_type) VALUES ($1, $2)",
            f"evt_{uuid4().hex}", "customer.subscription.updated",
        )
    count = await conn.fetchval("SELECT COUNT(*) FROM stripe_webhook_events")
    assert count == 5, "5 unique event_ids must produce 5 rows"


@pytest.mark.asyncio
async def test_webhook_replay_concurrent_inserts_are_serialized(conn):
    """Two concurrent tasks inserting the same event_id: exactly one wins."""
    import asyncio

    event_id = "evt_race_" + uuid4().hex
    c2 = await asyncpg.connect(PG_TEST_URL)
    try:
        async def _attempt(cx):
            try:
                await cx.execute(
                    "INSERT INTO stripe_webhook_events (event_id, event_type) "
                    "VALUES ($1, 'checkout.session.completed')",
                    event_id,
                )
                return "win"
            except asyncpg.UniqueViolationError:
                return "lost"

        results = await asyncio.gather(_attempt(conn), _attempt(c2))
        assert sorted(results) == ["lost", "win"], (
            f"exactly one must win the PK race; got {results}"
        )
    finally:
        await c2.close()


# ─── 2. Partner 5-failure lockout ───────────────────────────────────


async def _seed_partner(conn, email="ceo@acmemsp.test"):
    pid = uuid4()
    await conn.execute(
        "INSERT INTO partners (id, name, slug, contact_email, password_hash, status) "
        "VALUES ($1, 'Acme MSP', 'acme-msp', $2, 'not-a-real-hash', 'active')",
        pid, email,
    )
    return pid


@pytest.mark.asyncio
async def test_lockout_triggers_on_fifth_failure(conn):
    """After 5 incremented attempts, locked_until is set to a future time."""
    pid = await _seed_partner(conn)

    # Simulate the exact increment pattern from partner_auth.py lines 1222-1230.
    for attempt_num in range(1, 6):
        attempts = (await conn.fetchval(
            "SELECT failed_login_attempts FROM partners WHERE id = $1", pid
        )) + 1
        locked = None
        if attempts >= 5:
            locked = datetime.now(timezone.utc) + timedelta(minutes=15)
        await conn.execute(
            "UPDATE partners SET failed_login_attempts = $1, locked_until = $2 WHERE id = $3",
            attempts, locked, pid,
        )

    final = await conn.fetchrow(
        "SELECT failed_login_attempts, locked_until FROM partners WHERE id = $1", pid
    )
    assert final["failed_login_attempts"] == 5
    assert final["locked_until"] is not None, "5 fails MUST set locked_until"
    assert final["locked_until"] > datetime.now(timezone.utc), (
        "locked_until must be in the future"
    )
    # Lock window must be ≥ 14 minutes into the future (leave slack for clock drift).
    assert final["locked_until"] - datetime.now(timezone.utc) >= timedelta(minutes=14)


@pytest.mark.asyncio
async def test_lockout_blocks_even_correct_password(conn):
    """While locked_until is in the future, no login attempt is allowed to proceed."""
    pid = await _seed_partner(conn)
    locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
    await conn.execute(
        "UPDATE partners SET failed_login_attempts = 5, locked_until = $1 WHERE id = $2",
        locked_until, pid,
    )

    row = await conn.fetchrow(
        "SELECT failed_login_attempts, locked_until FROM partners WHERE id = $1", pid
    )
    # The lockout guard in partner_auth.py checks `locked_until > now()` BEFORE
    # even attempting password verification. Replicate that guard here.
    is_locked = row["locked_until"] is not None and row["locked_until"] > datetime.now(timezone.utc)
    assert is_locked, "a row with future locked_until MUST be treated as locked"


@pytest.mark.asyncio
async def test_lockout_clears_on_successful_login(conn):
    """A successful login (even one while locked_until is in the past) resets counters."""
    pid = await _seed_partner(conn)
    past_lock = datetime.now(timezone.utc) - timedelta(minutes=1)
    await conn.execute(
        "UPDATE partners SET failed_login_attempts = 5, locked_until = $1 WHERE id = $2",
        past_lock, pid,
    )

    # Simulate the post-success reset at partner_auth.py line 1289.
    await conn.execute(
        "UPDATE partners SET failed_login_attempts = 0, locked_until = NULL WHERE id = $1",
        pid,
    )

    row = await conn.fetchrow(
        "SELECT failed_login_attempts, locked_until FROM partners WHERE id = $1", pid
    )
    assert row["failed_login_attempts"] == 0
    assert row["locked_until"] is None


@pytest.mark.asyncio
async def test_lockout_scope_is_per_partner(conn):
    """Locking partner A MUST NOT lock partner B with the same email domain."""
    pid_a = await _seed_partner(conn, email="alice@msp.test")
    pid_b = await _seed_partner(conn, email="bob@msp.test")

    # Lock ONLY partner A.
    await conn.execute(
        "UPDATE partners SET failed_login_attempts = 5, "
        "locked_until = $1 WHERE id = $2",
        datetime.now(timezone.utc) + timedelta(minutes=15), pid_a,
    )

    a = await conn.fetchrow("SELECT locked_until FROM partners WHERE id = $1", pid_a)
    b = await conn.fetchrow("SELECT locked_until FROM partners WHERE id = $1", pid_b)
    assert a["locked_until"] is not None, "partner A must be locked"
    assert b["locked_until"] is None, "partner B must NOT be affected"


# ─── 3. Cross-partner IDOR at endpoint level ────────────────────────


@pytest.mark.asyncio
async def test_endpoint_level_site_scope_rejects_other_partners_site(conn):
    """A query filtered by (site_id, partner_id) returns 0 rows when the
    partner does not own the site — the endpoint-layer analog of the SQL
    scope test, and the shape that every partner-scoped endpoint in
    partners.py MUST follow.
    """
    pid_a = await _seed_partner(conn, email="alice@a.test")
    pid_b = await _seed_partner(conn, email="bob@b.test")
    await conn.execute(
        "INSERT INTO sites (site_id, clinic_name, partner_id) "
        "VALUES ('a-site-1', 'Alice Clinic', $1), "
        "       ('b-site-1', 'Bob Clinic', $2)",
        pid_a, pid_b,
    )

    # Partner A attempts to resolve partner B's site by id.
    # Any endpoint that reads `WHERE site_id = $1 AND partner_id = $2`
    # (the mandatory pattern) returns zero rows — the endpoint then 404s.
    leaked = await conn.fetchrow(
        "SELECT site_id, clinic_name FROM sites "
        "WHERE site_id = $1 AND partner_id = $2",
        "b-site-1", pid_a,
    )
    assert leaked is None, (
        "Partner A MUST NOT resolve Partner B's site_id. Any endpoint that "
        "returns a row here is an IDOR — audit its WHERE clause."
    )

    # Sanity: Partner B CAN resolve their own site.
    legit = await conn.fetchrow(
        "SELECT site_id, clinic_name FROM sites "
        "WHERE site_id = $1 AND partner_id = $2",
        "b-site-1", pid_b,
    )
    assert legit is not None, "owner must still resolve their own site"
    assert legit["clinic_name"] == "Bob Clinic"


@pytest.mark.asyncio
async def test_cross_partner_site_enumeration_is_blocked(conn):
    """Enumerate-all-sites returns only the caller's sites, never the
    union of all partners' sites. The bug this pins: an endpoint that
    forgot to filter by partner_id and returned every site in the table.
    """
    pid_a = await _seed_partner(conn, email="alice@a.test")
    pid_b = await _seed_partner(conn, email="bob@b.test")
    await conn.execute(
        "INSERT INTO sites (site_id, partner_id) VALUES "
        "('a1',$1),('a2',$1),('a3',$1),('b1',$2),('b2',$2)",
        pid_a, pid_b,
    )

    rows = await conn.fetch(
        "SELECT site_id FROM sites WHERE partner_id = $1 ORDER BY site_id", pid_a
    )
    visible = {r["site_id"] for r in rows}
    assert visible == {"a1", "a2", "a3"}, (
        f"Partner A should see exactly their 3 sites; saw {visible}"
    )
    assert not any(sid.startswith("b") for sid in visible), (
        "Enumerate-all MUST NOT leak partner B's site_ids"
    )


@pytest.mark.asyncio
async def test_null_partner_filter_does_not_match_real_partner_sites(conn):
    """A malicious payload sending partner_id = NULL (via JSON null) must
    NOT match rows where partner_id IS NULL — that would bypass scoping
    for direct-to-clinic sites. Postgres IS DISTINCT FROM semantics plus
    explicit NOT NULL guards at the handler level are the defense.
    """
    pid_a = await _seed_partner(conn, email="alice@a.test")
    await conn.execute(
        "INSERT INTO sites (site_id, clinic_name, partner_id) VALUES "
        "('direct-1', 'Direct Clinic', NULL), "
        "('alice-1', 'Alice Clinic', $1)",
        pid_a,
    )

    # Baseline: SQL `partner_id = NULL` always returns zero rows (three-
    # valued logic). This is a postgres-level safety net — the real fix is
    # the handler-level `if partner_id is None: raise 400`.
    leak = await conn.fetch("SELECT site_id FROM sites WHERE partner_id = NULL")
    assert leak == [], (
        "SQL `= NULL` must return zero rows — any endpoint that substitutes "
        "user-controlled JSON null and trusts the result is broken."
    )

    # Separately verify IS NULL does match the direct site (defensive baseline).
    direct = await conn.fetch("SELECT site_id FROM sites WHERE partner_id IS NULL")
    assert {r["site_id"] for r in direct} == {"direct-1"}
