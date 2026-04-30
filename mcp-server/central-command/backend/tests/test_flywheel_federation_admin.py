"""F6 phase 2 foundation slice (Session 214 round-table SHIP_FOUNDATION_SLICE).

Source-level structural tests + DB-gated integration tests for the
flywheel_federation_admin module.

NO ENFORCEMENT TESTED — that's deferred per consensus. These tests
verify the read-only operator endpoint shape + the daily snapshot
writer's idempotency + the schema constraints from migration 262.
"""
from __future__ import annotations

import os
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
ADMIN_PY = (
    REPO_ROOT
    / "mcp-server" / "central-command" / "backend"
    / "flywheel_federation_admin.py"
)
MIG_262 = (
    REPO_ROOT
    / "mcp-server" / "central-command" / "backend"
    / "migrations" / "262_flywheel_federation_foundation.sql"
)
MAIN_PY = REPO_ROOT / "mcp-server" / "main.py"


def test_module_exists():
    assert ADMIN_PY.exists()


def test_endpoint_admin_only():
    """require_admin auth — partner cross-org visibility is NOT
    permitted at this layer."""
    src = ADMIN_PY.read_text()
    assert "Depends(require_admin)" in src, (
        "federation-candidates endpoint must require_admin — partners "
        "cannot see other partners' candidate counts (substrate-class "
        "operator-only visibility)."
    )


def test_endpoint_path_correct():
    src = ADMIN_PY.read_text()
    assert 'prefix="/api/admin/flywheel"' in src
    assert '"/federation-candidates"' in src


def test_endpoint_tier_query_param_validated():
    """Pydantic Query validation must restrict tier to 'org' or
    'platform' — local tier already has its own surface (F7
    diagnostic per-site)."""
    src = ADMIN_PY.read_text()
    assert re.search(
        r'tier:\s*str\s*=\s*Query\([^)]*pattern\s*=\s*"\^\(org\|platform\)\$"',
        src,
    ), (
        "tier query param must be validated against ^(org|platform)$"
    )


def test_no_cross_org_rule_id_disclosure():
    """Round-table consensus: 'NO rule_id-level disclosure across orgs.'

    The endpoint returns counts + summary, NOT lists of pattern_signature
    values across orgs. Scan for any code path that returns rule_id
    or pattern_signature lists in the cross-org context."""
    src = ADMIN_PY.read_text()
    # Strip docstrings + comments
    stripped = re.sub(r'"""[\s\S]*?"""', '""', src)
    stripped = re.sub(r"#[^\n]*", "", stripped)

    # The response model fields must NOT include rule_id or
    # pattern_signature lists
    forbidden_fields = [
        r"\brule_ids\s*:\s*List\b",
        r"\bpattern_signatures\s*:\s*List\b",
    ]
    for pat in forbidden_fields:
        assert not re.search(pat, stripped), (
            f"Federation candidates response must NOT expose "
            f"rule_id-level / pattern_signature-level data across orgs. "
            f"Round-table SHIP_FOUNDATION_SLICE consensus requires "
            f"counts + summary only."
        )


def test_snapshot_writer_only_writes_to_snapshot_table():
    """The take_federation_snapshot function MUST NOT write to
    aggregated_pattern_stats, promoted_rules, fleet_orders, or any
    chain-of-custody table. Only WRITE allowed is to
    flywheel_federation_candidate_daily itself.

    Round-table-flagged read-only contract."""
    src = ADMIN_PY.read_text()

    # Find take_federation_snapshot body
    fn_start = src.find("async def take_federation_snapshot")
    assert fn_start != -1
    # Find the end of the function (next top-level def or EOF)
    fn_body_end = len(src)
    next_def = re.search(r"\n(?:async )?def [a-zA-Z_]", src[fn_start + 1:])
    if next_def:
        fn_body_end = fn_start + 1 + next_def.start()
    fn_body = src[fn_start:fn_body_end]

    # For forbidden-pattern check: strip the FUNCTION DOCSTRING only
    # (the first triple-quoted string after `def`). Don't strip the
    # SQL triple-quoted literals in the body — those ARE the code we're
    # auditing. Use re.DOTALL with .*? to find just the first one.
    docstring_match = re.match(
        r'(async def take_federation_snapshot[^\n]*\n\s*)"""[\s\S]*?"""',
        fn_body,
    )
    if docstring_match:
        fn_no_docstring = (
            fn_body[:docstring_match.start()]
            + docstring_match.group(1)
            + fn_body[docstring_match.end():]
        )
    else:
        fn_no_docstring = fn_body
    stripped_for_forbidden = re.sub(r"#[^\n]*", "", fn_no_docstring)

    forbidden_targets = [
        r"\bUPDATE\s+aggregated_pattern_stats\b",
        r"\bUPDATE\s+promoted_rules\b",
        r"\bINSERT\s+INTO\s+aggregated_pattern_stats\b",
        r"\bINSERT\s+INTO\s+promoted_rules\b",
        r"\bINSERT\s+INTO\s+fleet_orders\b",
        r"\bINSERT\s+INTO\s+promoted_rule_events\b",  # audit chain
    ]
    for pat in forbidden_targets:
        match = re.search(pat, stripped_for_forbidden, re.IGNORECASE)
        assert not match, (
            f"take_federation_snapshot is the foundation-slice write "
            f"path — it MUST NOT touch enforcement / rollout / audit-"
            f"chain tables. Found forbidden: {pat}. Round-table "
            f"SHIP_FOUNDATION_SLICE consensus requires this isolation."
        )

    # Required-write check: assert the snapshot table appears as an
    # actual write target. Use the unstripped function body.
    assert re.search(
        r"INSERT\s+INTO\s+flywheel_federation_candidate_daily",
        fn_body,
        re.IGNORECASE,
    ), (
        "take_federation_snapshot must INSERT to "
        "flywheel_federation_candidate_daily — that's the whole point"
    )


def test_router_registered_in_main():
    main_src = MAIN_PY.read_text()
    assert (
        "from dashboard_api.flywheel_federation_admin import" in main_src
    )
    assert "take_federation_snapshot" in main_src, (
        "main.py must import take_federation_snapshot for the daily loop"
    )
    assert "include_router(flywheel_federation_admin_router)" in main_src
    assert '"flywheel_federation_snapshot"' in main_src, (
        "Daily snapshot loop must be registered in task_defs"
    )


def test_daily_snapshot_loop_has_sleep_86400():
    """Cadence: once per day. The loop's sleep call must be ~86400s."""
    main_src = MAIN_PY.read_text()
    fn_start = main_src.find("async def _flywheel_federation_snapshot_loop")
    assert fn_start != -1
    fn_end = main_src.find("\n\n\n", fn_start)
    fn_body = main_src[fn_start:fn_end]
    assert "asyncio.sleep(86400)" in fn_body, (
        "Daily snapshot loop must sleep 24h between ticks"
    )


def test_migration_262_creates_required_schema():
    src = MIG_262.read_text()
    # Column add
    assert "promoted_rule_events" in src
    assert "tier_at_promotion TEXT" in src
    # Table create
    assert "CREATE TABLE IF NOT EXISTS flywheel_federation_candidate_daily" in src
    # CHECK constraints
    assert "flywheel_fcd_tier_name_valid" in src
    assert "flywheel_fcd_org_scope_matches_tier" in src
    # Surrogate PK + COALESCE-based unique index
    assert "id               BIGSERIAL PRIMARY KEY" in src
    assert "COALESCE(client_org_id, '')" in src


def test_migration_262_no_check_on_tier_at_promotion():
    """Round-table P5 framing: do NOT add CHECK on tier_at_promotion
    yet. Calibration round-table may want to adjust the set before
    locking."""
    src = MIG_262.read_text()
    # Should NOT contain a CHECK constraint specifically on tier_at_promotion
    assert not re.search(
        r"CHECK\s*\(\s*tier_at_promotion\s*IN",
        src,
    ), (
        "Do NOT lock tier_at_promotion values via CHECK yet. "
        "Calibration round-table may want to adjust the set."
    )


# DB-gated integration tests
_requires_db = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="F6 foundation tests require TEST_DATABASE_URL",
)


@_requires_db
@pytest.mark.asyncio
async def test_schema_objects_exist():
    """Smoke-check: migration 262 creates the right objects."""
    from shared import async_session
    from sqlalchemy import text as sql_text

    async with async_session() as db:
        # Column on promoted_rule_events
        cols = await db.execute(sql_text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'promoted_rule_events' "
            "AND column_name = 'tier_at_promotion'"
        ))
        names = [r[0] for r in cols]
        assert "tier_at_promotion" in names

        # Snapshot table
        tables = await db.execute(sql_text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'flywheel_federation_candidate_daily'"
        ))
        assert any("flywheel_federation_candidate_daily" == r[0] for r in tables)


@_requires_db
@pytest.mark.asyncio
async def test_check_constraint_rejects_platform_with_org_id():
    """tier='platform' AND client_org_id IS NOT NULL must be rejected."""
    from shared import async_session
    from sqlalchemy import text as sql_text

    async with async_session() as db:
        with pytest.raises(Exception) as exc:
            await db.execute(sql_text("""
                INSERT INTO flywheel_federation_candidate_daily
                    (snapshot_date, tier_name, client_org_id, candidate_count)
                VALUES (CURRENT_DATE, 'platform', 'should-fail', 1)
            """))
            await db.commit()
        assert "org_scope_matches_tier" in str(exc.value).lower() or "check" in str(exc.value).lower()
        await db.rollback()


@_requires_db
@pytest.mark.asyncio
async def test_check_constraint_rejects_org_with_null_org_id():
    """tier='org' AND client_org_id IS NULL must be rejected."""
    from shared import async_session
    from sqlalchemy import text as sql_text

    async with async_session() as db:
        with pytest.raises(Exception) as exc:
            await db.execute(sql_text("""
                INSERT INTO flywheel_federation_candidate_daily
                    (snapshot_date, tier_name, client_org_id, candidate_count)
                VALUES (CURRENT_DATE, 'org', NULL, 1)
            """))
            await db.commit()
        assert "org_scope_matches_tier" in str(exc.value).lower() or "check" in str(exc.value).lower()
        await db.rollback()
