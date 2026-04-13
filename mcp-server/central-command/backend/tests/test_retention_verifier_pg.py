"""Integration test for retention_verifier (Phase 15 compliance ask).

Exercises verify_site_retention end-to-end against real Postgres.
Seeds bundles at staggered ages covering year-1 through year-7,
sanity-checks the sampling + verification logic, and proves that
a corrupted stored chain_hash is caught by the retention pass.

Skipped when PG_TEST_URL is unset.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
import asyncpg


PG_TEST_URL = os.getenv("PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres retention test",
)


PREREQ_SCHEMA = """
DROP TABLE IF EXISTS compliance_bundles CASCADE;

CREATE TABLE compliance_bundles (
    bundle_id       TEXT PRIMARY KEY,
    site_id         TEXT NOT NULL,
    bundle_hash     TEXT NOT NULL,
    prev_hash       TEXT NOT NULL,
    chain_position  INTEGER NOT NULL,
    chain_hash      TEXT NOT NULL,
    signature_valid BOOLEAN DEFAULT TRUE,
    checked_at      TIMESTAMPTZ NOT NULL,
    ots_status      TEXT DEFAULT 'anchored'
);
CREATE INDEX idx_cb_retention ON compliance_bundles (site_id, checked_at);
"""


def _chain_hash(bundle_hash: str, prev_hash: str, position: int) -> str:
    return hashlib.sha256(
        f"{bundle_hash}:{prev_hash}:{position}".encode()
    ).hexdigest()


@pytest_asyncio.fixture
async def conn():
    c = await asyncpg.connect(PG_TEST_URL)
    try:
        await c.execute(PREREQ_SCHEMA)
        yield c
    finally:
        await c.execute("DROP TABLE IF EXISTS compliance_bundles CASCADE")
        await c.close()


async def _seed_bundles_at_age(c, site_id: str, year_ago: int, n: int) -> None:
    """Insert n bundles with checked_at ~ year_ago years before now."""
    base_time = datetime.now(timezone.utc) - timedelta(days=year_ago * 365)
    prev = "0" * 64
    for i in range(n):
        pos = year_ago * 100 + i
        bh = hashlib.sha256(
            f"{site_id}-{year_ago}-{i}-{uuid.uuid4().hex[:8]}".encode()
        ).hexdigest()
        ch = _chain_hash(bh, prev, pos)
        await c.execute(
            "INSERT INTO compliance_bundles "
            "(bundle_id, site_id, bundle_hash, prev_hash, chain_position, "
            " chain_hash, signature_valid, checked_at, ots_status) "
            "VALUES ($1, $2, $3, $4, $5, $6, TRUE, $7, 'anchored')",
            f"bundle-{site_id}-y{year_ago}-{i}",
            site_id, bh, prev, pos, ch,
            base_time + timedelta(days=i),
        )
        prev = bh


@pytest.mark.asyncio
async def test_all_years_clean_passes(conn):
    from retention_verifier import verify_site_retention
    for y in [1, 2, 3, 4, 5, 6, 7]:
        await _seed_bundles_at_age(conn, "site-retained", y, 5)

    summary = await verify_site_retention(conn, "site-retained", per_bucket=3)
    assert summary["total_failed"] == 0
    assert summary["total_sampled"] > 0
    # Each year present in the by_year bucket
    for y in [1, 2, 3, 4, 5, 6, 7]:
        assert summary["by_year"][y]["sampled"] >= 1


@pytest.mark.asyncio
async def test_corrupted_chain_hash_caught(conn):
    from retention_verifier import verify_site_retention
    await _seed_bundles_at_age(conn, "site-corrupt", 3, 5)
    # Corrupt one bundle's chain_hash
    await conn.execute(
        "UPDATE compliance_bundles SET chain_hash = $1 "
        "WHERE site_id = 'site-corrupt' LIMIT 1",
        # asyncpg/postgres doesn't support UPDATE ... LIMIT; need WHERE
        "deadbeef" * 8,
    ) if False else None
    # UPDATE ... LIMIT isn't SQL standard — mutate the first row via CTE
    await conn.execute(
        """
        UPDATE compliance_bundles SET chain_hash = 'deadbeef' || repeat('0', 56)
        WHERE bundle_id = (
            SELECT bundle_id FROM compliance_bundles
            WHERE site_id = 'site-corrupt'
            LIMIT 1
        )
        """
    )

    # Sample enough that we're guaranteed to touch the corrupted row
    summary = await verify_site_retention(conn, "site-corrupt", per_bucket=5)
    # We only seeded 5 bundles at year-3, sample is 5 per year-bucket.
    # Random sample should include the corrupted one with high probability.
    # This test occasionally passes when sampling misses — run 3 passes.
    # For determinism: set per_bucket high so ALL rows are sampled.
    if summary["total_failed"] == 0:
        # Increase sample to all 5 — the corrupted one must be in it
        summary = await verify_site_retention(conn, "site-corrupt", per_bucket=100)
    assert summary["total_failed"] >= 1
    assert any(i["kind"] == "chain_hash_mismatch" for i in summary["issues"])


@pytest.mark.asyncio
async def test_signature_invalid_caught(conn):
    from retention_verifier import verify_site_retention
    await _seed_bundles_at_age(conn, "site-sig", 2, 5)
    await conn.execute(
        "UPDATE compliance_bundles SET signature_valid = FALSE "
        "WHERE site_id = 'site-sig' AND bundle_id = ("
        "  SELECT bundle_id FROM compliance_bundles "
        "  WHERE site_id = 'site-sig' LIMIT 1"
        ")"
    )

    summary = await verify_site_retention(conn, "site-sig", per_bucket=100)
    assert summary["total_failed"] >= 1
    assert any(i["kind"] == "signature_invalid" for i in summary["issues"])


@pytest.mark.asyncio
async def test_empty_site_reports_zero(conn):
    from retention_verifier import verify_site_retention
    summary = await verify_site_retention(conn, "nonexistent-site")
    assert summary["total_sampled"] == 0
    assert summary["total_failed"] == 0
    # Each year bucket present but empty
    for y in [1, 2, 3, 4, 5, 6, 7]:
        assert summary["by_year"][y]["sampled"] == 0


@pytest.mark.asyncio
async def test_missing_year_bucket_does_not_fail_pass(conn):
    """If a site has no bundles at year-5 (e.g., site is only 2 years
    old), the pass must not report it as a failure — just as an
    empty bucket."""
    from retention_verifier import verify_site_retention
    # Only seed years 1-2
    await _seed_bundles_at_age(conn, "young-site", 1, 3)
    await _seed_bundles_at_age(conn, "young-site", 2, 3)

    summary = await verify_site_retention(conn, "young-site", per_bucket=3)
    assert summary["total_failed"] == 0
    # Years 3..7 should be empty but not failed
    for y in [3, 4, 5, 6, 7]:
        assert summary["by_year"][y]["sampled"] == 0
        assert summary["by_year"][y]["failed"] == 0


@pytest.mark.asyncio
async def test_verify_bundle_helper_direct():
    from retention_verifier import _verify_bundle, _verify_chain_hash
    bh = "a" * 64
    ph = "b" * 64
    pos = 42
    ch = _verify_chain_hash  # just the function
    from retention_verifier import _verify_chain_hash
    import hashlib as _h
    expected = _h.sha256(f"{bh}:{ph}:{pos}".encode()).hexdigest()
    good = {
        "bundle_hash": bh, "prev_hash": ph, "chain_position": pos,
        "chain_hash": expected, "signature_valid": True,
    }
    ok, reason = _verify_bundle(good)
    assert ok is True
    assert reason == "ok"

    bad_hash = dict(good, chain_hash="deadbeef" * 8)
    ok, reason = _verify_bundle(bad_hash)
    assert ok is False
    assert reason == "chain_hash_mismatch"

    bad_sig = dict(good, signature_valid=False)
    ok, reason = _verify_bundle(bad_sig)
    assert ok is False
    assert reason == "signature_invalid"

    no_hash = dict(good, chain_hash=None)
    ok, reason = _verify_bundle(no_hash)
    assert ok is False
    assert reason == "chain_hash_missing"
