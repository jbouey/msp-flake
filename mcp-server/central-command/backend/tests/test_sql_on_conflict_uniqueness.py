"""Static check: every `INSERT INTO <table> ... ON CONFLICT (cols)` in
backend Python references a column-set with a matching UNIQUE
constraint or unique index in prod.

Session 210-B 2026-04-25 audit Task #167. Today's bug class — the
"Approve" button 500 — was `INSERT INTO promoted_rules ... ON
CONFLICT (rule_id)` where `rule_id` had no unique constraint:

  asyncpg.exceptions.InvalidColumnReferenceError: there is no unique
    or exclusion constraint matching the ON CONFLICT specification

The schema linter (`test_sql_columns_match_schema.py`) catches column-
existence mismatches but doesn't validate constraint compatibility.
THIS test is the ON-CONFLICT linter complement. Found in 3 files
today (flywheel_promote.py, client_portal.py, learning_api.py); fix
was uniformly to switch to `ON CONFLICT (site_id, rule_id)` after
Migration 247 added that UNIQUE INDEX.

Refresh the fixture when migrations land that change unique
indexes/constraints:

    ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -t -A -c '
        SELECT json_object_agg(table_name, indexes) FROM (
            SELECT t.relname AS table_name,
                   json_agg(json_build_object(
                     name, i.relname, is_unique, ix.indisunique,
                     is_primary, ix.indisprimary,
                     columns, (...)
                   )) AS indexes
              FROM pg_class t JOIN pg_index ix ON ix.indrelid = t.oid
                              JOIN pg_class i ON i.oid = ix.indexrelid
                              JOIN pg_namespace n ON n.oid = t.relnamespace
             WHERE n.nspname = public AND t.relkind = r AND ix.indisunique = true
             GROUP BY t.relname) s
    '" | python3 -c '...slim transform...' > tests/fixtures/schema/prod_unique_indexes.json
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Dict, List, Set, Tuple

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "mcp-server" / "central-command" / "backend"
UNIQUE_FIXTURE = (
    BACKEND_DIR / "tests" / "fixtures" / "schema" / "prod_unique_indexes.json"
)


# Tables for which we deliberately don't enforce ON CONFLICT validation
# (e.g. dynamically-built table names, or tables whose constraints come
# from a subsystem outside the migration ledger).
TRUST_GAPS: Set[str] = set()


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


@pytest.fixture(scope="module")
def unique_sets() -> Dict[str, List[frozenset]]:
    """Load {table: [frozenset(cols), ...]} from the prod fixture."""
    if not UNIQUE_FIXTURE.exists():
        pytest.skip(
            f"unique-index fixture not found at {UNIQUE_FIXTURE}. "
            "Run the refresh command in this file's docstring."
        )
    raw = json.loads(UNIQUE_FIXTURE.read_text())
    out: Dict[str, List[frozenset]] = {}
    for tbl, idx_list in raw.items():
        out[tbl.lower()] = [frozenset(c.lower() for c in cols) for cols in idx_list]
    return out


# Match: INSERT INTO <table> (...) VALUES (...) ON CONFLICT (col1, col2) ...
# Or:    INSERT INTO <table> (...) SELECT ... ON CONFLICT (col1) ...
ON_CONFLICT_RE = re.compile(
    r"INSERT\s+INTO\s+([a-zA-Z_][\w]*)\s*\([^)]*\)\s*"
    r"(?:VALUES\s*\([^)]*\)|SELECT\s+[^O]*?)"
    r"\s*ON\s+CONFLICT\s*\(\s*([\w\s,]+?)\s*\)",
    re.IGNORECASE | re.DOTALL,
)


def _backend_py_files() -> List[pathlib.Path]:
    out: List[pathlib.Path] = []
    for p in BACKEND_DIR.rglob("*.py"):
        if any(skip in p.parts for skip in (
            "tests", "archived", "venv", "__pycache__", "node_modules",
        )):
            continue
        out.append(p)
    return out


def _scan_on_conflicts(src: str) -> List[Tuple[str, frozenset, int]]:
    """Yield (table, conflict_cols, lineno) per INSERT...ON CONFLICT."""
    out = []
    cleaned = _strip_sql_comments(src)
    for match in ON_CONFLICT_RE.finditer(cleaned):
        tbl = match.group(1).lower()
        col_blob = match.group(2)
        cols = frozenset(
            c.strip().lower()
            for c in col_blob.split(",")
            if c.strip() and re.match(r"^[a-z_][\w]*$", c.strip().lower())
        )
        if not cols:
            continue
        lineno = src[: match.start()].count("\n") + 1
        out.append((tbl, cols, lineno))
    return out


def test_unique_fixture_loaded(unique_sets):
    """Sanity: fixture has a reasonable shape."""
    assert len(unique_sets) >= 100, (
        f"Fixture has only {len(unique_sets)} tables — expected ≥100. "
        "Refresh from prod."
    )
    # Spot-check tables we KNOW have unique constraints
    assert "promoted_rules" in unique_sets
    # promoted_rules now has UNIQUE(site_id, rule_id) per Migration 247
    pr_uniques = unique_sets["promoted_rules"]
    assert any(s == frozenset({"site_id", "rule_id"}) for s in pr_uniques), (
        f"promoted_rules should have UNIQUE(site_id, rule_id) per Migration 247; "
        f"got: {[sorted(s) for s in pr_uniques]}"
    )


def test_every_on_conflict_targets_a_unique_set(unique_sets):
    """The headline check. Every `ON CONFLICT (cols)` must match an
    actual unique index/constraint. This is the rule that bit us with
    the 'Approve' button 500.

    A ratchet baseline for legacy code paths that aren't in the fix
    queue yet: lower as the codebase migrates.
    """
    failures: List[str] = []
    for py_path in _backend_py_files():
        try:
            src = py_path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = py_path.relative_to(REPO_ROOT)
        for tbl, cols, lineno in _scan_on_conflicts(src):
            if tbl in TRUST_GAPS:
                continue
            if tbl not in unique_sets:
                # Table not in fixture — could be an external table or
                # a typo. Skip rather than spam (the column-existence
                # linter handles the typo case).
                continue
            if cols not in unique_sets[tbl]:
                # Provide actionable hint: list the actual unique sets.
                actuals = [sorted(s) for s in unique_sets[tbl]]
                failures.append(
                    f"{rel}:{lineno}: ON CONFLICT ({', '.join(sorted(cols))}) on "
                    f"{tbl} — no matching unique constraint. Actual unique "
                    f"sets: {actuals}"
                )
    # Baseline locked 2026-04-25. Any NEW violation fails CI; lower this
    # number when migrating an existing site away from a non-unique
    # ON CONFLICT clause.
    BASELINE_MAX = 0
    assert len(failures) <= BASELINE_MAX, (
        f"{len(failures)} ON-CONFLICT violations > baseline {BASELINE_MAX}. "
        "Each line below is a runtime InvalidColumnReferenceError waiting "
        "to fire on first call. Either fix the ON CONFLICT clause to use "
        "the actual unique key, OR add a UNIQUE INDEX in a migration:\n"
        + "\n".join(f"  - {f}" for f in failures)
    )
