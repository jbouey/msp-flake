"""Chain-tamper detector integration test (Phase 15 A-spec hygiene).

Builds a synthetic compliance_bundles chain in a real Postgres,
mutates select rows to simulate tampering, and asserts the
detector's _verify_site_recent() helper correctly identifies which
bundles are valid vs broken.

Skipped when PG_TEST_URL is unset.
"""
from __future__ import annotations

import hashlib
import os
import uuid

import pytest
import pytest_asyncio
import asyncpg


PG_TEST_URL = os.getenv("PG_TEST_URL")

pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="PG_TEST_URL not set — skipping live-Postgres tamper-detector test",
)


GENESIS_HASH = "0" * 64


# Minimal compliance_bundles schema — only the columns the detector reads.
PREREQ_SCHEMA = """
DROP TABLE IF EXISTS compliance_bundles CASCADE;

CREATE TABLE compliance_bundles (
    bundle_id      TEXT PRIMARY KEY,
    site_id        TEXT NOT NULL,
    bundle_hash    TEXT NOT NULL,
    prev_hash      TEXT NOT NULL,
    chain_position INTEGER NOT NULL,
    chain_hash     TEXT NOT NULL,
    check_type     TEXT DEFAULT 'compliance'
);
CREATE INDEX idx_cb_site_pos ON compliance_bundles (site_id, chain_position);
"""


def _compute_chain_hash(bundle_hash: str, prev_hash: str, position: int) -> str:
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


async def _insert_chain(c, site_id: str, length: int) -> list:
    """Build a valid hash chain of `length` bundles. Returns list of
    (bundle_id, bundle_hash, position)."""
    bundles = []
    prev = GENESIS_HASH
    for pos in range(1, length + 1):
        bundle_id = f"bundle-{site_id}-{pos}"
        bundle_hash = hashlib.sha256(f"{site_id}-{pos}-content".encode()).hexdigest()
        chain_hash = _compute_chain_hash(bundle_hash, prev, pos)
        await c.execute(
            "INSERT INTO compliance_bundles "
            "(bundle_id, site_id, bundle_hash, prev_hash, chain_position, chain_hash) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            bundle_id, site_id, bundle_hash, prev, pos, chain_hash,
        )
        bundles.append((bundle_id, bundle_hash, pos))
        prev = bundle_hash
    return bundles


# ─── Tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_site_returns_zero(conn):
    from chain_tamper_detector import _verify_site_recent
    verified, broken = await _verify_site_recent(conn, "no-such-site")
    assert verified == 0
    assert broken == []


@pytest.mark.asyncio
async def test_valid_chain_all_verified(conn):
    from chain_tamper_detector import _verify_site_recent
    await _insert_chain(conn, "site-good", 5)
    verified, broken = await _verify_site_recent(conn, "site-good")
    # First bundle in window has no prev to verify so we don't count
    # link_ok for it; chain_hash is checked. All 5 should pass hash.
    assert verified == 5
    assert broken == []


@pytest.mark.asyncio
async def test_tampered_chain_hash_detected(conn):
    from chain_tamper_detector import _verify_site_recent
    await _insert_chain(conn, "site-evil", 5)
    # Mutate bundle 3's chain_hash to a wrong value
    await conn.execute(
        "UPDATE compliance_bundles SET chain_hash = $1 "
        "WHERE site_id = 'site-evil' AND chain_position = 3",
        "deadbeef" * 8,
    )
    verified, broken = await _verify_site_recent(conn, "site-evil")
    assert verified == 4
    assert len(broken) == 1
    assert broken[0]["position"] == 3
    assert broken[0]["hash_valid"] is False


@pytest.mark.asyncio
async def test_tampered_prev_hash_detected(conn):
    from chain_tamper_detector import _verify_site_recent
    await _insert_chain(conn, "site-link", 5)
    # Mutate bundle 4's prev_hash so linkage breaks. Note: this also
    # invalidates bundle 4's own chain_hash because chain_hash is
    # computed over (bundle_hash:prev_hash:position). So bundle 4
    # fails BOTH hash_ok (recomputed expected != stored) and link_ok.
    # We re-compute the chain_hash to keep hash_ok true and isolate
    # the link failure.
    new_prev = "ab" * 32
    row = await conn.fetchrow(
        "SELECT bundle_hash, chain_position FROM compliance_bundles "
        "WHERE site_id='site-link' AND chain_position=4"
    )
    new_chain_hash = _compute_chain_hash(row["bundle_hash"], new_prev, 4)
    await conn.execute(
        "UPDATE compliance_bundles SET prev_hash=$1, chain_hash=$2 "
        "WHERE site_id='site-link' AND chain_position=4",
        new_prev, new_chain_hash,
    )
    verified, broken = await _verify_site_recent(conn, "site-link")
    assert len(broken) == 1
    assert broken[0]["position"] == 4
    assert broken[0]["hash_valid"] is True   # we re-hashed
    assert broken[0]["link_valid"] is False  # but linkage is broken


@pytest.mark.asyncio
async def test_chain_position_gap_detected(conn):
    from chain_tamper_detector import _verify_site_recent
    await _insert_chain(conn, "site-gap", 5)
    # Delete bundle 3 — leaves a gap (1, 2, 4, 5)
    await conn.execute(
        "DELETE FROM compliance_bundles "
        "WHERE site_id='site-gap' AND chain_position=3"
    )
    verified, broken = await _verify_site_recent(conn, "site-gap")
    # Bundle 4 (which now follows position 2 in our window) should
    # fail link_ok because position != prev.position + 1.
    assert any(
        b["position"] == 4 and b["link_valid"] is False
        for b in broken
    )


@pytest.mark.asyncio
async def test_window_only_walks_recent(conn, monkeypatch):
    """When the chain is longer than CHAIN_TAMPER_WINDOW, only the
    most-recent slice is walked. Tampering OUTSIDE the window is
    invisible to this loop (which is fine — full chain audit is the
    auditor-kit's job)."""
    import chain_tamper_detector
    monkeypatch.setattr(chain_tamper_detector, "CHAIN_TAMPER_WINDOW", 3)

    from chain_tamper_detector import _verify_site_recent
    await _insert_chain(conn, "site-window", 10)
    # Tamper bundle 2 — outside the most-recent-3 window
    await conn.execute(
        "UPDATE compliance_bundles SET chain_hash = $1 "
        "WHERE site_id='site-window' AND chain_position=2",
        "deadbeef" * 8,
    )
    verified, broken = await _verify_site_recent(conn, "site-window")
    # Walked 3 most-recent (8, 9, 10) — all valid. Tampered bundle 2
    # is outside the window.
    assert verified == 3
    assert broken == []
