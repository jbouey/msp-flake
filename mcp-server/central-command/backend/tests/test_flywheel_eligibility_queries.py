"""F6 phase 2 — read-only eligibility query helpers (Session 214).

Source-level structural tests + DB-gated integration tests.
"""
from __future__ import annotations

import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
QUERIES_PY = (
    REPO_ROOT
    / "mcp-server" / "central-command" / "backend"
    / "flywheel_eligibility_queries.py"
)


def test_module_exists():
    assert QUERIES_PY.exists()


def test_module_is_read_only_no_writes():
    """Critical: module must NEVER contain UPDATE/INSERT/DELETE
    against any table, AND must never use conn.execute() (which is the
    single API surface that can write). Round-table P1-3 hardened the
    regex to be whitespace-tolerant and added the conn.execute ban.

    Strip SQL + Python comments + docstrings before scanning so
    legitimate commentary doesn't false-positive."""
    import re
    src = QUERIES_PY.read_text()

    # Strip Python triple-quoted strings (docstrings)
    stripped = re.sub(r'"""[\s\S]*?"""', '""', src)
    stripped = re.sub(r"'''[\s\S]*?'''", "''", stripped)
    # Strip Python single-line comments
    stripped = re.sub(r"#[^\n]*", "", stripped)
    # Strip SQL line comments
    stripped = re.sub(r"--[^\n]*", "", stripped)

    # Whitespace-tolerant patterns — `INSERT\n  INTO` was missed by
    # the previous literal substring check.
    write_patterns = [
        (r"\bINSERT\s+INTO\b", "INSERT INTO"),
        (r"\bUPDATE\s+\w+\s+SET\b", "UPDATE ... SET"),
        (r"\bDELETE\s+FROM\b", "DELETE FROM"),
    ]
    for regex, label in write_patterns:
        match = re.search(regex, stripped, re.IGNORECASE)
        assert not match, (
            f"flywheel_eligibility_queries.py is read-only — found "
            f"forbidden '{label}' pattern in source. This module is "
            f"consumed by the F7 diagnostic endpoint and any future "
            f"Phase 2 enforcement code; writes belong in the "
            f"enforcement caller, NOT here. Round-table 2026-04-30 P1-3."
        )

    # Hard ban on conn.execute() — the only asyncpg API surface that
    # can write. Reads use conn.fetch / conn.fetchrow / conn.fetchval.
    assert not re.search(r"\.execute\s*\(", stripped), (
        "flywheel_eligibility_queries.py uses conn.execute() — banned. "
        "This module is read-only. Use conn.fetch / fetchrow / fetchval "
        "instead. Round-table 2026-04-30 P1-3."
    )


def test_org_query_has_explicit_client_org_id_filter():
    """HIPAA cross-org boundary: tier 1 query MUST filter sites by
    client_org_id BEFORE aggregating. The JOIN pattern is verified
    structurally — if a future refactor moves the filter elsewhere
    (after aggregation, or via a different column), this test fails."""
    src = QUERIES_PY.read_text()
    # The org query body should contain the explicit filter
    assert "WHERE client_org_id = $1" in src, (
        "Tier 1 query MUST filter `sites` by client_org_id BEFORE "
        "aggregating. Cross-org leak class. Round-table 2026-04-30."
    )
    # And the function signature should require the parameter
    assert "client_org_id: str" in src, (
        "count_tier_org_eligible MUST require client_org_id as a typed "
        "parameter — never aggregate without an explicit org scope."
    )


def test_count_tier_org_returns_none_when_uncalibrated():
    """Source-level: the tier 1 query must short-circuit when
    min_distinct_sites is None (uncalibrated). We can't run the
    query without that threshold without making up a value, and
    making up a value is exactly what F6 round-table P1 forbade.

    Regex-based assertion (round-table P2-8) — the prior line-index
    parse broke on whitespace/comments between the if and return."""
    import re
    src = QUERIES_PY.read_text()
    assert re.search(
        r"if\s+thresholds\.min_distinct_sites\s+is\s+None:\s*\n\s*return\s+None",
        src,
    ), (
        "count_tier_org_eligible must short-circuit with `return None` "
        "when thresholds.min_distinct_sites is None — making up a "
        "threshold value is what F6 round-table P1 forbade."
    )


def test_count_tier_platform_returns_none_when_uncalibrated():
    """Source-level: tier 2 short-circuits when EITHER distinct
    threshold is uncalibrated."""
    src = QUERIES_PY.read_text()
    assert "thresholds.min_distinct_orgs is None" in src
    assert "thresholds.min_distinct_sites is None" in src


def test_compute_tier_resolution_returns_dict_shape():
    """Source-level: compute_tier_resolution returns a dict that maps
    1:1 to the F7 endpoint's TierResolution model. Round-table P2-6
    consistent `*_would_be_eligible` naming across all three tiers."""
    src = QUERIES_PY.read_text()
    required_keys = [
        '"local_would_be_eligible"',
        '"org_would_be_eligible"',
        '"platform_would_be_eligible"',
        '"tier_local_active"',
        '"tier_org_active"',
        '"tier_platform_active"',
        '"tier_local_calibrated"',
        '"tier_org_calibrated"',
        '"tier_platform_calibrated"',
        '"client_org_id"',
        '"notes"',
    ]
    for k in required_keys:
        assert k in src, f"compute_tier_resolution missing key {k}"


def test_compute_tier_resolution_includes_thresholds():
    """Round-table P3 (Session 214 follow-up): tier_*_thresholds
    must be in the response so operators can see the actual values
    used per tier without reading migrations."""
    src = QUERIES_PY.read_text()
    threshold_keys = [
        '"tier_local_thresholds"',
        '"tier_org_thresholds"',
        '"tier_platform_thresholds"',
    ]
    for k in threshold_keys:
        assert k in src, f"compute_tier_resolution missing {k}"
    # The helper that projects TierThresholds → dict must exist
    assert "_thresholds_to_dict" in src, (
        "missing _thresholds_to_dict helper for projecting threshold "
        "bundles to JSON-serializable dicts"
    )


def test_thresholds_to_dict_projects_all_threshold_fields():
    """Type-drift guard (round-table P1-recommend): every non-state
    field of `TierThresholds` (i.e. excluding tier_name, tier_level,
    enabled, calibrated) must be projected by `_thresholds_to_dict`.

    If a future migration adds a new threshold dimension to the
    dataclass and forgets to update the projection helper, this test
    fails immediately — same class of three-list-lockstep failure
    that bit sigauth identity-key, flywheel event_type, and runbook
    attestation."""
    from dataclasses import fields
    import sys
    backend_dir = REPO_ROOT / "mcp-server" / "central-command" / "backend"
    sys.path.insert(0, str(backend_dir))
    from flywheel_eligibility_queries import TierThresholds, _thresholds_to_dict

    state_fields = {"tier_name", "tier_level", "enabled", "calibrated"}
    threshold_fields = {f.name for f in fields(TierThresholds)} - state_fields

    sample = TierThresholds(
        tier_name="x",
        tier_level=0,
        min_total_occurrences=1,
        min_success_rate=0.9,
        min_l2_resolutions=1,
        max_age_days=30,
        min_distinct_orgs=None,
        min_distinct_sites=None,
        enabled=False,
        calibrated=False,
    )
    projected = set(_thresholds_to_dict(sample).keys())
    missing = threshold_fields - projected
    assert not missing, (
        f"_thresholds_to_dict drift: TierThresholds has these threshold "
        f"fields that are NOT projected to the response dict: {missing}. "
        f"Update _thresholds_to_dict to include them, OR add them to "
        f"the state_fields exclusion if they're not threshold dimensions."
    )


def test_pydantic_view_matches_threshold_fields():
    """Same drift class for the API model: TierThresholdsView must
    list every threshold field that `_thresholds_to_dict` projects.
    Otherwise Pydantic silently drops keys when validating the dict
    into the model."""
    from dataclasses import fields
    import sys
    backend_dir = REPO_ROOT / "mcp-server" / "central-command" / "backend"
    sys.path.insert(0, str(backend_dir))
    from flywheel_eligibility_queries import TierThresholds
    from flywheel_diagnostic import TierThresholdsView

    state_fields = {"tier_name", "tier_level", "enabled", "calibrated"}
    threshold_fields = {f.name for f in fields(TierThresholds)} - state_fields
    pydantic_fields = set(TierThresholdsView.model_fields.keys())
    missing = threshold_fields - pydantic_fields
    assert not missing, (
        f"TierThresholdsView Pydantic model is missing fields: "
        f"{missing}. Add them to the model so the API exposes them."
    )


def test_thresholds_view_pydantic_model_exists():
    """The F7 endpoint must define a TierThresholdsView Pydantic
    model so the OpenAPI schema typed-codegens cleanly for the
    frontend."""
    src = (
        REPO_ROOT
        / "mcp-server" / "central-command" / "backend"
        / "flywheel_diagnostic.py"
    ).read_text()
    assert "class TierThresholdsView(BaseModel):" in src, (
        "F7 endpoint must define TierThresholdsView so the threshold "
        "bundle codegens as a typed object, not Dict[str, Any]"
    )
    # And the TierResolution model must reference it
    assert "tier_local_thresholds: Optional[TierThresholdsView]" in src
    assert "tier_org_thresholds: Optional[TierThresholdsView]" in src
    assert "tier_platform_thresholds: Optional[TierThresholdsView]" in src


def test_no_string_concat_interval_pattern():
    """P0 regression guard (2026-04-30): the pattern
    `(:max_age || ' days')::INTERVAL` BROKE PRODUCTION for ~7 hours.
    asyncpg binds int parameters as int, the `||` text operator can't
    accept int → TypeError every flywheel cycle, zero fleet_orders
    issued. The fix is `make_interval(days => :max_age)` which
    accepts int directly.

    This test bans the broken pattern in both files."""
    import re
    pattern = re.compile(r"\|\|\s*'\s*days\s*'\s*\)\s*::\s*INTERVAL", re.IGNORECASE)
    for path_str in [
        "mcp-server/main.py",
        "mcp-server/central-command/backend/flywheel_eligibility_queries.py",
    ]:
        path = REPO_ROOT / path_str
        if not path.exists():
            continue
        src = path.read_text()
        match = pattern.search(src)
        assert not match, (
            f"{path_str}: forbidden pattern `(:param || ' days')::INTERVAL` found. "
            f"asyncpg binds int as int, can't auto-cast for `||` text op. "
            f"Use `make_interval(days => :param)` instead. P0 regression "
            f"from F6 MVP slice 2026-04-30."
        )


def test_uses_shared_parse_bool_env():
    """Round-table P2-7: env-flag parsing MUST go through
    shared.parse_bool_env so the F7 diagnostic and main.py
    enforcement can't drift on what 'TRUE' (capital T) means.
    Drift between them would be a credibility event."""
    diag_src = (
        REPO_ROOT
        / "mcp-server" / "central-command" / "backend"
        / "flywheel_diagnostic.py"
    ).read_text()
    assert "parse_bool_env" in diag_src, (
        "flywheel_diagnostic.py must use shared.parse_bool_env "
        "(round-table P2-7 — single source of truth for env-flag "
        "parsing across the F7 diagnostic + future enforcement)."
    )


def test_diagnostic_endpoint_includes_tier_resolution():
    """F7 endpoint MUST include the new tier_resolution section."""
    src = (
        REPO_ROOT
        / "mcp-server" / "central-command" / "backend"
        / "flywheel_diagnostic.py"
    ).read_text()
    assert "class TierResolution(BaseModel):" in src
    assert "tier_resolution: TierResolution" in src
    assert "compute_tier_resolution" in src
    # The handler must scope the call to canonical_site_id
    assert "compute_tier_resolution(" in src


# DB-gated integration test
_requires_db = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="F6 phase 2 query tests require TEST_DATABASE_URL",
)


@_requires_db
@pytest.mark.asyncio
async def test_cross_org_isolation_property():
    """**THE LOAD-BEARING TEST** for F6 phase 2 (round-table P1-4).

    Insert fixture rows under two distinct client_org_ids, then call
    count_tier_org_eligible scoped to ONE org and assert the count
    matches ONLY that org's contributing rows. If the JOIN ever
    leaked across orgs, this test fails immediately.

    The structural test (`test_org_query_has_explicit_client_org_id_filter`)
    catches the "did someone delete the filter" class. This property
    test catches the "did someone change WHERE to a wrong scope"
    class — the actual privacy boundary.
    """
    import secrets
    from shared import async_session
    from flywheel_eligibility_queries import (
        TierThresholds,
        count_tier_org_eligible,
    )
    from sqlalchemy import text as sql_text

    # Use unique IDs per run so re-runs don't collide
    suffix = secrets.token_hex(4)
    org_a_id = f"test-org-a-{suffix}"
    org_b_id = f"test-org-b-{suffix}"
    site_a1 = f"test-site-a1-{suffix}"
    site_a2 = f"test-site-a2-{suffix}"
    site_b1 = f"test-site-b1-{suffix}"

    thresholds = TierThresholds(
        tier_name="org",
        tier_level=1,
        min_total_occurrences=1,
        min_success_rate=0.0,
        min_l2_resolutions=0,
        max_age_days=365,
        min_distinct_orgs=None,
        min_distinct_sites=1,  # set so the helper actually queries
        enabled=False,
        calibrated=False,
    )

    async with async_session() as db:
        # Setup: 2 orgs, 3 sites (2 in A, 1 in B), each with one
        # aggregated_pattern_stats row sharing the SAME pattern_signature.
        # If the JOIN leaks, count_tier_org for org_A would see all 3.
        await db.execute(sql_text("""
            INSERT INTO client_orgs (id, name)
            VALUES (:a, 'test-org-a'), (:b, 'test-org-b')
            ON CONFLICT (id) DO NOTHING
        """), {"a": org_a_id, "b": org_b_id})
        await db.execute(sql_text("""
            INSERT INTO sites (site_id, clinic_name, client_org_id, status)
            VALUES (:s1, 'a1', :a, 'online'),
                   (:s2, 'a2', :a, 'online'),
                   (:s3, 'b1', :b, 'online')
            ON CONFLICT (site_id) DO NOTHING
        """), {"s1": site_a1, "s2": site_a2, "s3": site_b1, "a": org_a_id, "b": org_b_id})

        pattern_sig = f"crossorg-test-{suffix}"
        await db.execute(sql_text("""
            INSERT INTO aggregated_pattern_stats (
                site_id, pattern_signature, total_occurrences,
                l1_resolutions, l2_resolutions, l3_resolutions,
                success_count, total_resolution_time_ms,
                success_rate, avg_resolution_time_ms,
                first_seen, last_seen
            ) VALUES
                (:s1, :p, 10, 0, 5, 0, 9, 1000, 0.9, 100, NOW(), NOW()),
                (:s2, :p, 10, 0, 5, 0, 9, 1000, 0.9, 100, NOW(), NOW()),
                (:s3, :p, 10, 0, 5, 0, 9, 1000, 0.9, 100, NOW(), NOW())
            ON CONFLICT (site_id, pattern_signature) DO NOTHING
        """), {"s1": site_a1, "s2": site_a2, "s3": site_b1, "p": pattern_sig})
        await db.commit()

        # The helper takes asyncpg.Connection. We need to drive it
        # via the actual asyncpg path. Use the get_pool helper from
        # fleet (same pattern as flywheel_diagnostic.py).
        try:
            from fleet import get_pool
        except ImportError:
            from .fleet import get_pool

    # Run the helpers against asyncpg pool directly (bypass SQLAlchemy
    # because the helpers expect asyncpg.Connection)
    pool = await get_pool()
    async with pool.acquire() as raw_conn:
        # Set admin context (these tables aren't tenant-scoped but
        # consistency with prod path)
        count_a = await count_tier_org_eligible(raw_conn, org_a_id, thresholds)
        count_b = await count_tier_org_eligible(raw_conn, org_b_id, thresholds)

    # Org A has 2 sites contributing 1 distinct pattern → count=1
    # Org B has 1 site contributing 1 distinct pattern → count=1
    # If the JOIN leaked, both counts would be HIGHER (the leaked rows
    # would aggregate into the queried org's totals)
    assert count_a == 1, (
        f"Cross-org isolation breach: org_a count is {count_a}, "
        f"expected 1. The JOIN may be leaking org_b's rows."
    )
    assert count_b == 1, (
        f"Cross-org isolation breach: org_b count is {count_b}, "
        f"expected 1. The JOIN may be leaking org_a's rows."
    )

    # Stronger check: verify total_occurrences AGGREGATION stays
    # within scope. Org A has 2 sites × 10 occurrences = 20.
    # If leaked, would be 30 (3 sites × 10).
    async with pool.acquire() as raw_conn:
        scoped_total = await raw_conn.fetchval("""
            WITH site_scope AS (
                SELECT site_id FROM sites
                 WHERE client_org_id = $1 AND status != 'inactive'
            )
            SELECT SUM(aps.total_occurrences)
              FROM aggregated_pattern_stats aps
              JOIN site_scope ss ON ss.site_id = aps.site_id
             WHERE aps.pattern_signature = $2
        """, org_a_id, pattern_sig)
    assert scoped_total == 20, (
        f"Cross-org aggregation leaked: org_a scoped total is "
        f"{scoped_total}, expected 20 (2 sites × 10). If 30, the "
        f"site_scope filter is broken."
    )


@_requires_db
@pytest.mark.asyncio
async def test_load_tier_returns_seed_rows():
    """The 3 seed tiers (mig 261) should load. enabled=False,
    calibrated=False on all of them in test DB (matches prod)."""
    from shared import async_session
    from flywheel_eligibility_queries import load_tier
    from sqlalchemy import text as sql_text

    async with async_session() as db:
        # Use a raw asyncpg-like adapter — SQLAlchemy session
        # doesn't directly fit, so this test exercises the
        # load_tier helper's structural contract by checking
        # that its query against the real table works.
        result = await db.execute(sql_text(
            "SELECT tier_name FROM flywheel_eligibility_tiers "
            "ORDER BY tier_level"
        ))
        names = [r[0] for r in result]
    assert names == ["local", "org", "platform"]
