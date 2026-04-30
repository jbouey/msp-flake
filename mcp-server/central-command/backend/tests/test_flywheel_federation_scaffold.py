"""F6 MVP slice (Session 214) — schema + feature flag scaffolding.

Tests the structural contract: the table exists, the seed rows are
present and ALL DISABLED, the feature flag defaults to off, the
read-path code is gated correctly. Threshold values are calibration-
pending and intentionally NOT pinned to specific numbers in this test
(calibration migration will adjust them).

DB-gated tests skip cleanly without TEST_DATABASE_URL.
"""
from __future__ import annotations

import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
MIG_261 = (
    REPO_ROOT
    / "mcp-server"
    / "central-command"
    / "backend"
    / "migrations"
    / "261_flywheel_eligibility_tiers.sql"
)
MAIN_PY = REPO_ROOT / "mcp-server" / "main.py"


def test_migration_file_exists():
    assert MIG_261.exists(), "Migration 261 missing"


def test_migration_creates_table_with_required_columns():
    src = MIG_261.read_text()
    required_columns = [
        "tier_name",
        "tier_level",
        "min_total_occurrences",
        "min_success_rate",
        "min_l2_resolutions",
        "max_age_days",
        "min_distinct_orgs",
        "min_distinct_sites",
        "enabled",
        "calibrated_at",
    ]
    for col in required_columns:
        assert col in src, f"Mig 261 missing column {col!r}"


def test_migration_seeds_three_tiers_all_disabled():
    """Seed rows MUST ship with enabled=FALSE. Calibration migration
    flips this AFTER round-table approval."""
    src = MIG_261.read_text()
    assert "'local'" in src
    assert "'org'" in src
    assert "'platform'" in src
    # Every seed VALUES tuple must end with FALSE for enabled column
    # AND NULL for calibrated_at — structural enforcement against
    # accidental enable-on-ship.
    # Find the INSERT block and verify each seed row line has FALSE.
    insert_start = src.find(
        "INSERT INTO flywheel_eligibility_tiers"
    )
    assert insert_start != -1
    insert_block = src[insert_start:src.find("ON CONFLICT", insert_start)]
    # Each tier row should reference enabled=FALSE explicitly
    for tier_name in ["'local'", "'org'", "'platform'"]:
        # Extract the row containing this tier_name; verify FALSE in it
        row_start = insert_block.find(tier_name)
        # The row may span multiple lines — slice to next ',' or ')'
        row_end = insert_block.find("),", row_start)
        if row_end == -1:
            row_end = insert_block.rfind(")", row_start)
        row_chunk = insert_block[row_start:row_end + 1]
        assert "FALSE" in row_chunk, (
            f"Seed row for {tier_name} must have enabled=FALSE — "
            f"calibration approval is required to enable any tier"
        )


def test_check_constraints_present():
    """tier_level must be 0..2; min_success_rate must be 0..1.
    Round-table P1+P2: also enforce calibration discipline (distinct
    columns required when calibrated_at is set) + cross-org isolation
    non-optional at tier_level >= 1."""
    src = MIG_261.read_text()
    assert "flywheel_tier_level_range" in src
    assert "flywheel_tier_success_rate_range" in src
    assert "flywheel_tier_org_isolation_required" in src, (
        "Round-table P2: schema must enforce org_isolation_required=TRUE "
        "for tier_level >= 1 (HIPAA boundary)"
    )
    assert "flywheel_tier_distinct_orgs_required_when_calibrated" in src, (
        "Round-table P1: calibrated platform tier must have explicit "
        "min_distinct_orgs (no placeholder values masquerading as "
        "calibrated decisions)"
    )
    assert "flywheel_tier_distinct_sites_required_when_calibrated" in src


def test_org_and_platform_seeds_have_null_distinct_thresholds():
    """Round-table P1: seeds for tier_level >= 1 ship with
    distinct_orgs/sites = NULL so the calibration migration MUST set
    them explicitly (forced by CHECK constraint when calibrated_at
    is populated)."""
    src = MIG_261.read_text()
    insert_start = src.find("INSERT INTO flywheel_eligibility_tiers")
    assert insert_start != -1
    insert_block = src[insert_start:src.find("ON CONFLICT", insert_start)]

    # Extract org row — should have NULL, NULL for distinct_orgs/sites
    org_idx = insert_block.find("'org', 1,")
    assert org_idx != -1, "org seed row not found"
    org_chunk = insert_block[org_idx:insert_block.find("),", org_idx)]
    # Both distinct columns must be NULL in the org row
    assert org_chunk.count("NULL") >= 2, (
        f"org seed row must have NULL for both min_distinct_orgs and "
        f"min_distinct_sites — got chunk: {org_chunk[:300]}"
    )

    # Extract platform row — same constraint
    plat_idx = insert_block.find("'platform', 2,")
    assert plat_idx != -1, "platform seed row not found"
    plat_chunk = insert_block[plat_idx:insert_block.find("),", plat_idx)]
    assert plat_chunk.count("NULL") >= 2, (
        f"platform seed row must have NULL for both min_distinct_orgs "
        f"and min_distinct_sites — got chunk: {plat_chunk[:300]}"
    )


def test_higher_tier_seeds_set_org_isolation_required():
    """Round-table P2: org and platform seeds must explicitly set
    org_isolation_required=TRUE — schema CHECK enforces this for
    tier_level >= 1, so the seed must satisfy it on INSERT."""
    src = MIG_261.read_text()
    insert_start = src.find("INSERT INTO flywheel_eligibility_tiers")
    insert_block = src[insert_start:src.find("ON CONFLICT", insert_start)]

    # Both higher-tier rows should have TRUE in their VALUES tuple
    for tier in ["'org', 1,", "'platform', 2,"]:
        tier_idx = insert_block.find(tier)
        assert tier_idx != -1
        tier_chunk = insert_block[tier_idx:insert_block.find("),", tier_idx)]
        assert "TRUE" in tier_chunk, (
            f"Seed row for {tier} must set org_isolation_required=TRUE "
            f"(HIPAA boundary)"
        )


def test_misconfiguration_warning_is_logger_warning_not_info():
    """Round-table P2: flag-on-but-tier-inactive log must be at
    WARNING level so the log shipper alerts on it. INFO would sit in
    container logs and miss operator attention."""
    src = MAIN_PY.read_text()
    # The flag-on-but-inactive block uses logger.warning
    block_start = src.find("flywheel_federation_flag_on_but_local_tier_inactive")
    assert block_start != -1
    # 200 chars before the literal — must contain logger.warning
    leading = src[max(0, block_start - 200):block_start]
    assert "logger.warning" in leading, (
        "flywheel_federation misconfiguration must log at WARNING level "
        "(operator-actionable). Round-table 2026-04-30 P2."
    )


def test_main_py_reads_feature_flag():
    """main.py Step 2 must consult FLYWHEEL_FEDERATION_ENABLED env var."""
    src = MAIN_PY.read_text()
    assert "FLYWHEEL_FEDERATION_ENABLED" in src, (
        "F6 read path must check FLYWHEEL_FEDERATION_ENABLED env var"
    )


def test_main_py_default_off_when_flag_absent():
    """Default value MUST be 'false' so unconfigured prod doesn't
    accidentally activate the federation path."""
    src = MAIN_PY.read_text()
    # The os.environ.get call should default to 'false'
    assert '"FLYWHEEL_FEDERATION_ENABLED", "false"' in src, (
        "FLYWHEEL_FEDERATION_ENABLED env var must default to 'false'"
    )


def test_main_py_uses_lenient_env_parser():
    """Round-table 2026-04-30 P1: parser must accept "true"/"1"/
    "yes"/"on" — sibling subsystem assertions.py::L2_ENABLED uses
    the same convention. Strict-only ("== 'true'") would silently
    drop FLYWHEEL_FEDERATION_ENABLED=1, which is a real operator
    footgun (split convention in this repo)."""
    src = MAIN_PY.read_text()
    assert 'in ("true", "1", "yes", "on")' in src, (
        "Federation flag parser must be lenient (matches L2_ENABLED). "
        "Round-table P1 — strict parser is the documented sibling-"
        "subsystem mismatch."
    )


def test_main_py_falls_back_when_tier_not_calibrated():
    """If the flag is on but the tier is not enabled OR not calibrated,
    main.py MUST fall back to hardcoded defaults (defensive)."""
    src = MAIN_PY.read_text()
    assert "calibrated_at" in src, (
        "Read path must check tier.calibrated_at"
    )
    assert "flywheel_federation_flag_on_but_local_tier_inactive" in src, (
        "Defensive log when flag is on but tier is not enabled+calibrated"
    )


# DB-gated integration tests
_requires_db = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="F6 scaffold tests require TEST_DATABASE_URL",
)


@_requires_db
@pytest.mark.asyncio
async def test_table_exists_with_all_three_tiers():
    from shared import async_session
    from sqlalchemy import text as sql_text

    async with async_session() as db:
        result = await db.execute(sql_text(
            "SELECT tier_name, tier_level, enabled, calibrated_at, "
            "       org_isolation_required, min_distinct_orgs, "
            "       min_distinct_sites "
            "FROM flywheel_eligibility_tiers ORDER BY tier_level"
        ))
        rows = list(result)

    assert len(rows) == 3
    tier_names = [r[0] for r in rows]
    assert tier_names == ['local', 'org', 'platform']
    # All disabled, no calibration timestamp
    for r in rows:
        assert r[2] is False, f"Tier {r[0]!r} ships enabled — must be FALSE"
        assert r[3] is None, f"Tier {r[0]!r} has calibrated_at set — must be NULL"

    # Higher tiers (org=1, platform=2): org_isolation_required=TRUE,
    # distinct_orgs/sites NULL (calibration-pending).
    by_name = {r[0]: r for r in rows}
    assert by_name['org'][4] is True, "org tier must require cross-org isolation"
    assert by_name['platform'][4] is True, "platform tier must require cross-org isolation"
    assert by_name['org'][5] is None and by_name['org'][6] is None
    assert by_name['platform'][5] is None and by_name['platform'][6] is None
    # Local tier: org_isolation_required is NULL (not applicable),
    # min_distinct_sites=1 by definition.
    assert by_name['local'][6] == 1


@_requires_db
@pytest.mark.asyncio
async def test_check_constraints_reject_invalid():
    from shared import async_session
    from sqlalchemy import text as sql_text

    async with async_session() as db:
        # tier_level 99 — must reject
        with pytest.raises(Exception):
            await db.execute(sql_text("""
                INSERT INTO flywheel_eligibility_tiers
                    (tier_name, tier_level, min_total_occurrences,
                     min_success_rate, min_l2_resolutions, max_age_days,
                     description, enabled)
                VALUES ('invalid', 99, 1, 0.5, 1, 1, 'invalid level', FALSE)
            """))
            await db.commit()
        await db.rollback()

        # success_rate 2.0 — must reject
        with pytest.raises(Exception):
            await db.execute(sql_text("""
                INSERT INTO flywheel_eligibility_tiers
                    (tier_name, tier_level, min_total_occurrences,
                     min_success_rate, min_l2_resolutions, max_age_days,
                     description, enabled)
                VALUES ('invalid_rate', 0, 1, 2.0, 1, 1, 'invalid rate', FALSE)
            """))
            await db.commit()
        await db.rollback()
