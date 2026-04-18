"""Three-list lockstep CI gate.

The flywheel spine has THREE places that encode the same event-type
vocabulary and three places that encode the same state vocabulary.
Drift between them is a P0 bug — today's audit found that Python
EVENT_TYPES (16) had silently diverged from the DB CHECK (22) and every
advance_lifecycle() call emitting a Python-only name tripped the CHECK
and rolled back, silently, for months.

This test fails CI the instant the three lists disagree.

THREE LISTS — must stay in sync:
  1. flywheel_state.EVENT_TYPES        (Python validator)
  2. promoted_rule_events.event_type   (DB CHECK)
  3. promoted_rule_lifecycle_transitions (DB state edges)

THREE STATE LISTS:
  1. flywheel_state.LIFECYCLE_STATES
  2. promoted_rules.lifecycle_state CHECK
  3. Referenced to_state/from_state in transitions matrix

Skipped when PG_TEST_URL is unset.
"""
from __future__ import annotations

import os
import pathlib
import re

import asyncpg
import pytest
import pytest_asyncio


PG_TEST_URL = os.getenv("PG_TEST_URL")
pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping lockstep test",
)

MIGRATIONS_DIR = pathlib.Path(__file__).parent.parent / "migrations"


PREREQ_SCHEMA = """
DROP TABLE IF EXISTS promoted_rule_events CASCADE;
DROP TABLE IF EXISTS promoted_rule_lifecycle_transitions CASCADE;
DROP TABLE IF EXISTS promoted_rules CASCADE;
DROP FUNCTION IF EXISTS advance_lifecycle(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT, TEXT) CASCADE;
DROP FUNCTION IF EXISTS enforce_lifecycle_via_advance() CASCADE;
DROP FUNCTION IF EXISTS prule_events_append_only_guard() CASCADE;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE promoted_rules (
    rule_id TEXT PRIMARY KEY,
    site_id TEXT,
    status TEXT DEFAULT 'active',
    promoted_at TIMESTAMPTZ DEFAULT NOW()
);
"""


SPINE_MIGRATIONS = [
    "181_flywheel_spine.sql",
    "184_runbook_attestation.sql",
    "188_extend_event_type_check.sql",
    "236_flywheel_spine_repair.sql",
]


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ_SCHEMA)
        for name in SPINE_MIGRATIONS:
            path = MIGRATIONS_DIR / name
            if not path.exists():
                pytest.skip(f"migration {name} not present")
            sql = path.read_text()
            # 184 references runbook tables; we only need its event types.
            # Skip 184 if it touches runbook_* that we haven't stubbed.
            try:
                await c.execute(sql)
            except asyncpg.exceptions.UndefinedTableError:
                # Partial apply — the CHECK part still matters; skip gracefully
                pass
        yield c
    finally:
        await c.execute("""
            DROP TABLE IF EXISTS promoted_rule_events CASCADE;
            DROP TABLE IF EXISTS promoted_rule_lifecycle_transitions CASCADE;
            DROP TABLE IF EXISTS promoted_rules CASCADE;
            DROP FUNCTION IF EXISTS advance_lifecycle CASCADE;
            DROP FUNCTION IF EXISTS enforce_lifecycle_via_advance CASCADE;
            DROP FUNCTION IF EXISTS prule_events_append_only_guard CASCADE;
        """)
        await c.close()


def _parse_check_values(check_def: str) -> set[str]:
    """Pull the single-quoted names out of an IN (...) CHECK clause."""
    # e.g. "CHECK ((event_type = ANY (ARRAY['a'::text, 'b'::text])))"
    return set(re.findall(r"'([^']+)'", check_def))


async def _fetch_check(conn: asyncpg.Connection, constraint_name: str) -> set[str]:
    row = await conn.fetchrow(
        """
        SELECT pg_get_constraintdef(c.oid) AS def
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
         WHERE c.conname = $1
        """,
        constraint_name,
    )
    assert row is not None, f"constraint {constraint_name} not found"
    return _parse_check_values(row["def"])


# ─── Event-type lockstep ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_python_event_types_superset_of_db_check(conn):
    """Every DB-allowed event type must be declared in Python EVENT_TYPES.

    Direction matters: if the DB allows a type Python doesn't know about,
    some other code path can INSERT it directly and Python's validator
    would reject a later replay. If Python declares a type the DB doesn't
    allow, advance_lifecycle() emits a row that trips the CHECK and the
    whole transaction rolls back.

    So we assert SET-EQUALITY, not superset.
    """
    from flywheel_state import EVENT_TYPES
    db_types = await _fetch_check(conn, "promoted_rule_events_event_type_check")

    python_only = EVENT_TYPES - db_types
    db_only = db_types - EVENT_TYPES

    assert not python_only, (
        f"Python EVENT_TYPES declares {python_only!r} but the DB CHECK "
        f"does not allow them. Add them to a new migration."
    )
    assert not db_only, (
        f"DB CHECK allows {db_only!r} but Python EVENT_TYPES does not "
        f"list them. Add them to flywheel_state.EVENT_TYPES."
    )


@pytest.mark.asyncio
async def test_state_transition_matrix_covers_all_referenced_states(conn):
    """Every state Python can transition TO must have at least one row
    in the transition matrix. Catches typos ('retried' vs 'retired')."""
    from flywheel_state import LIFECYCLE_STATES

    rows = await conn.fetch(
        "SELECT DISTINCT from_state, to_state "
        "FROM promoted_rule_lifecycle_transitions"
    )
    matrix_states = {r["from_state"] for r in rows} | {r["to_state"] for r in rows}

    python_only_states = LIFECYCLE_STATES - matrix_states
    assert not python_only_states, (
        f"Python LIFECYCLE_STATES declares {python_only_states!r} but "
        f"no row in the transition matrix references them. Dead code "
        f"or missing migration."
    )


@pytest.mark.asyncio
async def test_proposed_to_rolling_out_transition_present(conn):
    """Regression for migration 236 — auto-promotions need the direct edge.

    Before 236, promoted_rules defaulted to 'proposed' and safe_rollout
    tried to advance to 'rolling_out'. The matrix only had proposed→approved
    and approved→rolling_out as separate hops, so every auto-promotion
    since Session 206 cutover silently failed its transition check.
    """
    row = await conn.fetchrow(
        "SELECT 1 FROM promoted_rule_lifecycle_transitions "
        "WHERE from_state = 'proposed' AND to_state = 'rolling_out'"
    )
    assert row is not None, (
        "Migration 236 should have added proposed→rolling_out. "
        "Without it, auto-promotions cannot land a rollout."
    )


@pytest.mark.asyncio
async def test_every_python_event_is_emittable(conn):
    """For every name in Python EVENT_TYPES, INSERT a row into
    promoted_rule_events and confirm the CHECK accepts it. If the
    DB dropped or renamed a type, this catches it.
    """
    from flywheel_state import EVENT_TYPES
    for et in EVENT_TYPES:
        try:
            await conn.execute(
                """
                INSERT INTO promoted_rule_events (
                    rule_id, event_type, stage, outcome, actor
                ) VALUES ($1, $2, 'monitoring', 'success', 'system:test')
                """,
                f"lockstep-{et}", et,
            )
        except asyncpg.exceptions.CheckViolationError:
            pytest.fail(
                f"DB rejected event_type={et!r} even though Python declares "
                f"it in EVENT_TYPES. Migrations and code have drifted."
            )
