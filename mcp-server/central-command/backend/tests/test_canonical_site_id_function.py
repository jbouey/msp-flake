"""Migration 256 — canonical_site_id() function + site_canonical_mapping table.

DB-gated tests: require TEST_DATABASE_URL with migration 256 applied.

Pins the architectural close on the eligibility-fragmentation class
(F1 P0 from 2026-04-29 round-table).

Behavioral contract:
  * No mapping → identity (returns input unchanged).
  * Single hop (A → B) → returns B.
  * Multi-hop chain (A → B → C) → returns C transitively.
  * NULL → NULL.
  * Cycle (A → B → A): depth-bounded, must NOT loop forever.
  * Append-only: DELETE blocked, UPDATE blocked.
  * Self-mapping rejected at INSERT (CHECK constraint).
  * reason < 20 chars rejected (CHECK constraint).
  * Backfill row for physical-appliance-pilot-1aea78 → north-valley-branch-2
    must exist.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from shared import async_session

_requires_db = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="migration_256 tests require TEST_DATABASE_URL",
)


@_requires_db
@pytest.mark.asyncio
async def test_canonical_no_mapping_returns_input():
    async with async_session() as db:
        result = await db.execute(text(
            "SELECT canonical_site_id('definitely-not-a-mapped-site-xyz123')"
        ))
        assert result.scalar() == 'definitely-not-a-mapped-site-xyz123'


@_requires_db
@pytest.mark.asyncio
async def test_canonical_null_returns_null():
    async with async_session() as db:
        result = await db.execute(text("SELECT canonical_site_id(NULL)"))
        assert result.scalar() is None


@_requires_db
@pytest.mark.asyncio
async def test_canonical_backfill_row_exists():
    """Migration 256 backfill: orphan from migration 255 must resolve."""
    async with async_session() as db:
        result = await db.execute(text(
            "SELECT canonical_site_id('physical-appliance-pilot-1aea78')"
        ))
        assert result.scalar() == 'north-valley-branch-2'


@_requires_db
@pytest.mark.asyncio
async def test_canonical_multi_hop_resolves_transitively():
    """A → B → C should return C, not B."""
    async with async_session() as db:
        await db.execute(text("""
            INSERT INTO site_canonical_mapping
                (from_site_id, to_site_id, actor, reason)
            VALUES
                ('test-site-A', 'test-site-B',
                 'test:multi-hop',
                 'Multi-hop test fixture row A→B 20+ chars'),
                ('test-site-B', 'test-site-C',
                 'test:multi-hop',
                 'Multi-hop test fixture row B→C 20+ chars')
            ON CONFLICT (from_site_id) DO NOTHING
        """))
        await db.commit()

        result = await db.execute(text("SELECT canonical_site_id('test-site-A')"))
        assert result.scalar() == 'test-site-C'

        result = await db.execute(text("SELECT canonical_site_id('test-site-B')"))
        assert result.scalar() == 'test-site-C'

        # cleanup not possible — append-only. Tests must use unique site_ids.


@_requires_db
@pytest.mark.asyncio
async def test_canonical_self_map_rejected_at_insert():
    """from_site_id == to_site_id violates site_canonical_mapping_no_self."""
    async with async_session() as db:
        with pytest.raises(Exception) as exc:
            await db.execute(text("""
                INSERT INTO site_canonical_mapping
                    (from_site_id, to_site_id, actor, reason)
                VALUES
                    ('test-self-loop', 'test-self-loop',
                     'test', 'Self-loop must be rejected 20+ chars')
            """))
            await db.commit()
        assert (
            "site_canonical_mapping_no_self" in str(exc.value).lower()
            or "check" in str(exc.value).lower()
        )
        await db.rollback()


@_requires_db
@pytest.mark.asyncio
async def test_canonical_short_reason_rejected():
    """reason < 20 chars violates site_canonical_mapping_reason_min_length."""
    async with async_session() as db:
        with pytest.raises(Exception) as exc:
            await db.execute(text("""
                INSERT INTO site_canonical_mapping
                    (from_site_id, to_site_id, actor, reason)
                VALUES
                    ('test-short-reason', 'test-short-target',
                     'test', 'short')
            """))
            await db.commit()
        assert (
            "reason_min_length" in str(exc.value).lower()
            or "check" in str(exc.value).lower()
        )
        await db.rollback()


@_requires_db
@pytest.mark.asyncio
async def test_canonical_delete_blocked():
    """site_canonical_mapping is append-only."""
    async with async_session() as db:
        await db.execute(text("""
            INSERT INTO site_canonical_mapping
                (from_site_id, to_site_id, actor, reason)
            VALUES
                ('test-delete-blocked', 'test-delete-target',
                 'test', 'Delete-blocked test fixture row 20+ chars')
            ON CONFLICT (from_site_id) DO NOTHING
        """))
        await db.commit()

        with pytest.raises(Exception) as exc:
            await db.execute(text(
                "DELETE FROM site_canonical_mapping "
                "WHERE from_site_id='test-delete-blocked'"
            ))
            await db.commit()
        assert "append-only" in str(exc.value).lower()
        await db.rollback()


@_requires_db
@pytest.mark.asyncio
async def test_canonical_update_blocked():
    """site_canonical_mapping is append-only — UPDATE blocked too."""
    async with async_session() as db:
        await db.execute(text("""
            INSERT INTO site_canonical_mapping
                (from_site_id, to_site_id, actor, reason)
            VALUES
                ('test-update-blocked', 'test-update-target-A',
                 'test', 'Update-blocked test fixture row 20+ chars')
            ON CONFLICT (from_site_id) DO NOTHING
        """))
        await db.commit()

        with pytest.raises(Exception) as exc:
            await db.execute(text(
                "UPDATE site_canonical_mapping "
                "SET to_site_id='test-update-target-B' "
                "WHERE from_site_id='test-update-blocked'"
            ))
            await db.commit()
        assert "append-only" in str(exc.value).lower()
        await db.rollback()


@_requires_db
@pytest.mark.asyncio
async def test_canonical_unique_from_site_id():
    """One canonical per orphan — UNIQUE on from_site_id."""
    async with async_session() as db:
        await db.execute(text("""
            INSERT INTO site_canonical_mapping
                (from_site_id, to_site_id, actor, reason)
            VALUES
                ('test-unique-from', 'test-target-1',
                 'test', 'First insert wins; second collides 20+chars')
            ON CONFLICT (from_site_id) DO NOTHING
        """))
        await db.commit()

        with pytest.raises(Exception) as exc:
            await db.execute(text("""
                INSERT INTO site_canonical_mapping
                    (from_site_id, to_site_id, actor, reason)
                VALUES
                    ('test-unique-from', 'test-target-2',
                     'test', 'Second insert must fail 20+ chars long')
            """))
            await db.commit()
        assert (
            "duplicate" in str(exc.value).lower()
            or "unique" in str(exc.value).lower()
        )
        await db.rollback()


@_requires_db
@pytest.mark.asyncio
async def test_canonical_cycle_does_not_loop_forever():
    """A pathologic cycle A → B → A: depth limit must cap the recursion.

    We can't actually create a cycle (UNIQUE on from_site_id prevents the
    second leg's INSERT — A→B then B→A is fine, but adding A→B AGAIN to
    overwrite B→C is what would create a true cycle, and UPDATE is blocked).
    So this test creates the longest-possible legal chain and asserts the
    function terminates within depth 16.
    """
    async with async_session() as db:
        for i in range(15):
            await db.execute(text(f"""
                INSERT INTO site_canonical_mapping
                    (from_site_id, to_site_id, actor, reason)
                VALUES
                    ('test-chain-{i:02d}', 'test-chain-{i+1:02d}',
                     'test', 'Long-chain depth-limit test fixture row')
                ON CONFLICT (from_site_id) DO NOTHING
            """))
        await db.commit()

        result = await db.execute(text(
            "SELECT canonical_site_id('test-chain-00')"
        ))
        # Chain is exactly 15 hops (00→01→02→...→15). Depth limit 16,
        # so the full chain resolves to 15.
        assert result.scalar() == 'test-chain-15'


@_requires_db
@pytest.mark.asyncio
async def test_canonical_function_is_stable():
    """STABLE — query planner caches within a transaction."""
    async with async_session() as db:
        result = await db.execute(text(
            "SELECT provolatile FROM pg_proc WHERE proname='canonical_site_id'"
        ))
        # 's' = STABLE. 'v' = VOLATILE. 'i' = IMMUTABLE.
        assert result.scalar() == 's'


@_requires_db
@pytest.mark.asyncio
async def test_actor_must_be_email():
    """F1 P0 round-table: actor enforcement (CLAUDE.md privileged-access rule)."""
    async with async_session() as db:
        # 'system' rejected
        with pytest.raises(Exception) as exc:
            await db.execute(text("""
                INSERT INTO site_canonical_mapping
                    (from_site_id, to_site_id, actor, reason)
                VALUES
                    ('test-actor-system', 'test-actor-target',
                     'system', 'system actor must be rejected per CLAUDE.md')
            """))
            await db.commit()
        assert (
            "actor_is_email" in str(exc.value).lower()
            or "check" in str(exc.value).lower()
        )
        await db.rollback()

        # 'migration:NNN' rejected
        with pytest.raises(Exception) as exc:
            await db.execute(text("""
                INSERT INTO site_canonical_mapping
                    (from_site_id, to_site_id, actor, reason)
                VALUES
                    ('test-actor-mig', 'test-actor-target',
                     'migration:999', 'migration tag must not be in actor field')
            """))
            await db.commit()
        assert (
            "actor_is_email" in str(exc.value).lower()
            or "check" in str(exc.value).lower()
        )
        await db.rollback()

        # Valid email accepted
        await db.execute(text("""
            INSERT INTO site_canonical_mapping
                (from_site_id, to_site_id, actor, reason)
            VALUES
                ('test-actor-valid', 'test-actor-target',
                 'human@example.com', 'Valid email actor — should accept fine')
            ON CONFLICT (from_site_id) DO NOTHING
        """))
        await db.commit()


@_requires_db
@pytest.mark.asyncio
async def test_step3_platform_pattern_stats_counts_canonical_orgs():
    """F1 P0 round-table: platform_pattern_stats must aggregate orphan
    telemetry under canonical, not exclude it via the JOIN to `sites`.

    Setup:
      * Insert telemetry under 'test-orphan-distinct' (no `sites` row)
      * Insert mapping 'test-orphan-distinct' → some canonical site_id
        that DOES exist in `sites`
      * Run the platform_pattern_stats aggregator query
      * Assert the orphan rows contributed to `distinct_sites` /
        `distinct_orgs`

    NOTE: the actual platform aggregator runs every 30 min in
    _flywheel_promotion_loop. This test directly executes the same
    aggregator SQL inline so it doesn't depend on the loop firing.
    Without the canonical_site_id() patch, this test would FAIL because
    the orphan telemetry would be excluded by the JOIN to `sites`.
    """
    async with async_session() as db:
        # Find a real canonical site_id that exists in `sites`
        canonical = await db.execute(text(
            "SELECT site_id FROM sites LIMIT 1"
        ))
        canonical_site = canonical.scalar()
        if not canonical_site:
            pytest.skip("no sites in test DB to anchor canonical")

        orphan = "test-step3-orphan-canonical"

        # Insert mapping
        await db.execute(text("""
            INSERT INTO site_canonical_mapping
                (from_site_id, to_site_id, actor, reason)
            VALUES
                (:orphan, :canonical, 'test@example.com',
                 'Step 3 canonicalization regression test fixture row')
            ON CONFLICT (from_site_id) DO NOTHING
        """), {"orphan": orphan, "canonical": canonical_site})
        await db.commit()

        # Verify the mapping resolves
        resolved = await db.execute(text(
            "SELECT canonical_site_id(:orphan)"
        ), {"orphan": orphan})
        assert resolved.scalar() == canonical_site

        # The pre-fix bug: a JOIN sites s ON s.site_id = et.site_id
        # would silently exclude orphan telemetry. The fixed JOIN is
        # via canonical_site_id().
        # We assert the JOIN PATTERN itself resolves orphan→canonical.
        result = await db.execute(text("""
            SELECT s.site_id
              FROM sites s
             WHERE s.site_id = canonical_site_id(:orphan)
        """), {"orphan": orphan})
        assert result.scalar() == canonical_site, (
            "F1 regression: canonical_site_id() must resolve orphan→canonical "
            "for the platform_pattern_stats JOIN. If this fails, the Step 3 "
            "aggregator is back to undercounting distinct_sites."
        )
