"""Migration 257 — rename_site() function.

DB-gated tests: require TEST_DATABASE_URL with migration 257 applied.

F4 P1 from 2026-04-29 round-table — pins the centralized site-rename
SQL function so future contributors can't bypass the lockstep.

Behavioral contract (asserted here):
  * Validates inputs: actor must be email, reason ≥20 chars, from≠to.
  * Writes site_canonical_mapping row first.
  * Auto-discovers tables with site_id and physically moves rows.
  * Skips immutable tables (compliance_bundles + audit-class).
  * Audit-logs to admin_audit_log with structured details.
  * Returns SETOF (touched_table TEXT, rows_affected BIGINT).
  * pg_advisory_xact_lock serializes concurrent renames (smoke test only).
"""
from __future__ import annotations

import os
import secrets

import pytest
from sqlalchemy import text

from shared import async_session

_requires_db = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="migration_257 tests require TEST_DATABASE_URL",
)


def _unique(prefix: str) -> str:
    """Per-run unique site_id so re-runs don't hit ON CONFLICT no-op
    paths (site_canonical_mapping is append-only — DELETE blocked —
    so test data lives forever in the test DB; uniqueness avoids
    masked failures on re-run)."""
    return f"{prefix}-{secrets.token_hex(4)}"


async def _ensure_site(db, site_id: str) -> None:
    """rename_site() now refuses if from_site_id doesn't exist
    (F4 round-table P1-2). Tests must seed a real sites row first."""
    await db.execute(text("""
        INSERT INTO sites (site_id, clinic_name, client_org_id)
        VALUES (:sid, 'test-fixture-clinic', NULL)
        ON CONFLICT (site_id) DO NOTHING
    """), {"sid": site_id})


@_requires_db
@pytest.mark.asyncio
async def test_rename_site_rejects_non_email_actor():
    async with async_session() as db:
        with pytest.raises(Exception) as exc:
            await db.execute(text("""
                SELECT * FROM rename_site(
                    'test-rename-actor-bad-from',
                    'test-rename-actor-bad-to',
                    'system',
                    'should reject system actor per CLAUDE.md privileged-access'
                )
            """))
            await db.commit()
        assert "actor" in str(exc.value).lower()
        await db.rollback()


@_requires_db
@pytest.mark.asyncio
async def test_rename_site_rejects_short_reason():
    async with async_session() as db:
        with pytest.raises(Exception) as exc:
            await db.execute(text("""
                SELECT * FROM rename_site(
                    'test-rename-reason-bad-from',
                    'test-rename-reason-bad-to',
                    'human@example.com',
                    'too short'
                )
            """))
            await db.commit()
        assert "20" in str(exc.value) or "reason" in str(exc.value).lower()
        await db.rollback()


@_requires_db
@pytest.mark.asyncio
async def test_rename_site_rejects_self_rename():
    async with async_session() as db:
        with pytest.raises(Exception) as exc:
            await db.execute(text("""
                SELECT * FROM rename_site(
                    'test-rename-self',
                    'test-rename-self',
                    'human@example.com',
                    'Self-rename must be rejected per validation'
                )
            """))
            await db.commit()
        assert "differ" in str(exc.value).lower() or "from" in str(exc.value).lower()
        await db.rollback()


@_requires_db
@pytest.mark.asyncio
async def test_rename_site_writes_canonical_mapping():
    """Canonical mapping is the FIRST thing the function writes."""
    src = _unique("test-rename-mapping-source")
    dst = _unique("test-rename-mapping-target")
    async with async_session() as db:
        await _ensure_site(db, src)
        await db.commit()

        await db.execute(text("""
            SELECT * FROM rename_site(:src, :dst, 'human@example.com',
                'Test that canonical mapping row gets written first')
        """), {"src": src, "dst": dst})
        await db.commit()

        result = await db.execute(text("""
            SELECT to_site_id, actor
              FROM site_canonical_mapping
             WHERE from_site_id = :src
        """), {"src": src})
        row = result.fetchone()
        assert row is not None
        assert row[0] == dst
        assert row[1] == 'human@example.com'


@_requires_db
@pytest.mark.asyncio
async def test_rename_site_skips_immutable_tables():
    """compliance_bundles + audit-class tables MUST NOT have their site_id touched."""
    async with async_session() as db:
        result = await db.execute(text(
            "SELECT table_name FROM _rename_site_immutable_tables()"
        ))
        immutable = {r[0] for r in result}

    # Cryptographic + audit-class + parent identity (F4 round-table) +
    # mig 259 drift-close additions (F4-followup invariant surfaced 7
    # tables with site_id + DELETE-blocking trigger that weren't in
    # the immutable list — all 7 confirmed intentionally append-only).
    required_immutable = {
        # F4 round-table P0-2: parent identity row
        'sites',
        # Cryptographic / evidence
        'compliance_bundles',
        'compliance_packets',
        'evidence_bundles',
        'audit_packages',
        'ots_proofs',
        'baa_signatures',
        # Audit-class
        'admin_audit_log',
        'client_audit_log',
        'partner_activity_log',
        'portal_access_log',
        'incident_remediation_steps',
        'fleet_order_completions',
        'sigauth_observations',
        'promoted_rule_events',
        'reconcile_events',
        # Mig 259: F4-followup drift-close (Session 213)
        'appliance_heartbeats',
        'consent_request_tokens',
        'integration_audit_log',
        'liveness_claims',
        'promotion_audit_log_recovery',
        'provisioning_claim_events',
        'watchdog_events',
        # Self-referential
        'site_canonical_mapping',
    }
    missing = required_immutable - immutable
    assert not missing, (
        f"_rename_site_immutable_tables() missing required tables: {missing}. "
        f"Adding compliance/audit tables to the immutable list is a privileged "
        f"decision — review with the round-table before changing."
    )


@_requires_db
@pytest.mark.asyncio
async def test_rename_site_audit_logs():
    """rename_site() writes one structured admin_audit_log row."""
    src = _unique("test-rename-audit-from")
    dst = _unique("test-rename-audit-to")
    async with async_session() as db:
        await _ensure_site(db, src)
        await db.commit()

        await db.execute(text("""
            SELECT * FROM rename_site(:src, :dst, 'auditor@example.com',
                'Test that admin_audit_log row gets written by rename_site')
        """), {"src": src, "dst": dst})
        await db.commit()

        result = await db.execute(text("""
            SELECT username, details
              FROM admin_audit_log
             WHERE action = 'site.rename'
               AND target = :target
             ORDER BY created_at DESC
             LIMIT 1
        """), {"target": f"site:{src}"})
        row = result.fetchone()
        assert row is not None
        assert row[0] == 'auditor@example.com'
        details = row[1]
        assert details['from_site_id'] == src
        assert details['to_site_id'] == dst
        assert details['function_version'] == 'rename_site_v1'
        assert 'tables_intentionally_skipped' in details
        # F4 round-table P0-2: `sites` itself is in the immutable list
        assert 'sites' in details['tables_intentionally_skipped']


@_requires_db
@pytest.mark.asyncio
async def test_rename_site_returns_per_table_counts():
    """rename_site() returns SETOF (touched_table, rows_affected)."""
    src = _unique("test-rename-counts-from")
    dst = _unique("test-rename-counts-to")
    async with async_session() as db:
        await _ensure_site(db, src)
        await db.commit()

        result = await db.execute(text("""
            SELECT touched_table, rows_affected
              FROM rename_site(:src, :dst, 'human@example.com',
                  'Test that the function returns per-table row counts')
        """), {"src": src, "dst": dst})
        rows = list(result)
        await db.commit()

        # site_canonical_mapping is the first row by contract
        table_names = [r[0] for r in rows]
        assert 'site_canonical_mapping' in table_names
        # Every count is a non-negative integer
        for row in rows:
            assert row[1] >= 0


@_requires_db
@pytest.mark.asyncio
async def test_rename_site_rejects_nonexistent_from_site():
    """F4 round-table P1-2: refuse silent no-op on typo'd from_site_id."""
    nonexistent = _unique("test-rename-does-not-exist")
    dst = _unique("test-rename-target")
    async with async_session() as db:
        with pytest.raises(Exception) as exc:
            await db.execute(text("""
                SELECT * FROM rename_site(:src, :dst, 'human@example.com',
                    'Should reject because from_site_id does not exist')
            """), {"src": nonexistent, "dst": dst})
            await db.commit()
        msg = str(exc.value).lower()
        assert "does not exist" in msg or "from_site_id" in msg
        await db.rollback()


@_requires_db
@pytest.mark.asyncio
async def test_rename_site_skips_sites_table_itself():
    """F4 round-table P0-2: `sites` is in the immutable list. The PK
    update class is intractable across the FK graph; mapping carries
    the alias instead."""
    async with async_session() as db:
        result = await db.execute(text(
            "SELECT table_name FROM _rename_site_immutable_tables() "
            "WHERE table_name = 'sites'"
        ))
        assert result.scalar() == 'sites', (
            "F4 P0-2 regression: `sites` must be in immutable list. "
            "PK update would cascade unpredictably; site_canonical_mapping "
            "carries the alias."
        )


@_requires_db
@pytest.mark.asyncio
async def test_rename_site_function_volatility():
    """rename_site is VOLATILE (writes data); helper is IMMUTABLE."""
    async with async_session() as db:
        result = await db.execute(text(
            "SELECT provolatile FROM pg_proc WHERE proname='rename_site'"
        ))
        # 'v' = VOLATILE (correct — it INSERTs/UPDATEs)
        assert result.scalar() == 'v'

        result = await db.execute(text(
            "SELECT provolatile FROM pg_proc "
            "WHERE proname='_rename_site_immutable_tables'"
        ))
        # 'i' = IMMUTABLE (helper returns a fixed VALUES list)
        assert result.scalar() == 'i'
