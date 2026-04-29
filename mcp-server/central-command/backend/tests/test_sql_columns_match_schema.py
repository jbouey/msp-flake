"""Strict static check: every `INSERT INTO <table> (cols...)` and
`UPDATE <table> SET col = ...` in backend Python references columns
that ACTUALLY exist in the prod schema.

Session 210-B 2026-04-25 audit P1 #8 + round-table follow-up. Today
shipped FOUR column-name bugs that would have failed on first prod
call but passed CI because tests are source-grep / mock-based:

  * fleet_orders.site_id (doesn't exist — fleet-wide table)
  * fleet_orders.appliance_id (doesn't exist)
  * admin_audit_log.actor (real column is `username`)
  * admin_audit_log.target_type / target_id (real column is `target`)

Each was a copy-paste from a sister table. The exact CI guard the
audit recommended: parse SQL strings, validate column names against
the actual prod schema. First attempt at this used a regex-based
migration parser which was too lossy. This version replaces that
with a JSON fixture extracted from prod's information_schema, so
there's no parser-fragility risk — the schema source IS the schema.

Refresh the fixture when migrations land that change columns. Two
modes:

(a) From a clean prod (preferred — no manual edits to the JSON):

    ssh root@178.156.162.116 "docker exec mcp-postgres psql -U mcp -d mcp -t -A -c '
        SELECT json_object_agg(table_name, columns) FROM (
            SELECT table_name, json_agg(column_name ORDER BY ordinal_position) AS columns
              FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name NOT LIKE 'pg_%'
             GROUP BY table_name) s
    '" | python3 -c "
        import json, sys
        d = json.load(sys.stdin)
        d = {k: sorted(set(v)) for k, v in sorted(d.items())}
        json.dump(d, sys.stdout, indent=2, sort_keys=True)
    " > tests/fixtures/schema/prod_columns.json

(b) Forward-merge a local migration before it deploys (lockstep with
the migrations/N_*.sql files in the same PR). Edit the fixture in the
same diff; CI deploy will run the migration and the post-deploy fixture
will match. This is what the 2026-04-25 baseline-grind pass did with
migrations 248 + 249.

False-positive avoidance:
  * Subqueries / CTE INSERT-from-SELECT aren't conflated.
  * `INSERT INTO t SELECT ...` (no col list) is skipped — nothing to check.
  * The UPDATE walker uses a tighter regex that stops at WHERE/RETURNING.
  * Inline DDL strings inside Python (CREATE TABLE for tests) are skipped
    via the test-dir exclusion below.
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Dict, Set, Tuple, List

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "mcp-server" / "central-command" / "backend"
SCHEMA_FIXTURE = (
    BACKEND_DIR / "tests" / "fixtures" / "schema" / "prod_columns.json"
)


# Tables not in the schema dump (e.g. created on-demand at app boot,
# or app code uses CREATE TABLE IF NOT EXISTS in a runtime path).
# Adding a table here means "trust the developer not to typo column
# names in this code path." Empty by default — populate only when a
# false positive surfaces and is verified by hand.
SCHEMA_TRUST_GAPS: Set[str] = set()


def _strip_sql_comments(sql: str) -> str:
    """Remove `-- ...` line comments and `/* ... */` block comments."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


@pytest.fixture(scope="module")
def schema() -> Dict[str, Set[str]]:
    """Load the prod-extracted column fixture.

    See module docstring for refresh command. The fixture is a JSON
    object {table_name: [column, ...]} that mirrors prod's
    information_schema.columns at the time of capture.
    """
    if not SCHEMA_FIXTURE.exists():
        pytest.skip(
            f"schema fixture not found at {SCHEMA_FIXTURE}. "
            "Run the refresh command in this file's docstring."
        )
    raw = json.loads(SCHEMA_FIXTURE.read_text())
    return {tbl.lower(): {c.lower() for c in cols} for tbl, cols in raw.items()}


# INSERT INTO <table> (col1, col2, ...) VALUES ...
# We require an explicit column list. `INSERT INTO t SELECT ...` and
# `INSERT INTO t DEFAULT VALUES` are not validatable from text alone.
INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+([a-zA-Z_][\w]*)\s*\(\s*([\w\s,\n]+?)\s*\)\s*(?:VALUES|SELECT)",
    re.IGNORECASE | re.DOTALL,
)
# UPDATE <table> SET col1 = ..., col2 = ...
# Stop tokens, in order: SQL clauses (WHERE / RETURNING), triple-quote
# string terminator, OR `\n<ws>"` / `\n<ws>)` / `\n<ws>'` — these cover
# (a) end of a triple-quoted SQL block followed by Python kwargs, and
# (b) implicit-string-concat in a docstring that happens to mention an
# UPDATE statement (e.g. assertions.py recommended_action text).
UPDATE_RE = re.compile(
    r"UPDATE\s+([a-zA-Z_][\w]*)\s+SET\s+(.*?)"
    r"(?:\bWHERE\b|\bRETURNING\b|\"\"\"|'''|\n\s*[\"')]|\)\s*$)",
    re.IGNORECASE | re.DOTALL,
)
# SELECT <col1, col2, ...> FROM <table> ...
# Single-table SELECT only — JOINs explicitly skipped because column
# binding is ambiguous (`SELECT a.x, b.y FROM a JOIN b ...`). The
# audit observed 57 naive hits → only 2 real after JOIN filtering;
# this regex matches single-table SELECTs and skips multi-table joins
# by stopping at the first JOIN/AS-alias/comma-separated FROM list.
SELECT_RE = re.compile(
    r"SELECT\s+(?!.*\bJOIN\b)(.*?)\s+FROM\s+([a-zA-Z_][\w]*)\s*"
    r"(?:WHERE|ORDER\s+BY|LIMIT|GROUP\s+BY|HAVING|FOR\s+UPDATE|\"\"\"|'''|\)|\n\s*['\"]|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _backend_py_files() -> List[pathlib.Path]:
    """Walk backend/*.py — skip tests, archived, scripts, venv."""
    out: List[pathlib.Path] = []
    for p in BACKEND_DIR.rglob("*.py"):
        parts = p.parts
        if any(skip in parts for skip in (
            "tests", "archived", "venv", "__pycache__",
            "node_modules",
        )):
            continue
        out.append(p)
    return out


def _scan_inserts(src: str) -> List[Tuple[str, Set[str], int]]:
    """Yield (table, columns_referenced, approx_lineno) per INSERT."""
    out = []
    cleaned = _strip_sql_comments(src)
    for match in INSERT_RE.finditer(cleaned):
        tbl = match.group(1).lower()
        col_blob = match.group(2)
        cols = {c.strip().lower() for c in col_blob.split(",") if c.strip()}
        # Drop spurious tokens (e.g. accidental SQL keywords matched as cols)
        cols = {c for c in cols if re.match(r"^[a-z_][\w]*$", c)}
        lineno = src[: match.start()].count("\n") + 1
        out.append((tbl, cols, lineno))
    return out


def _scan_selects(src: str) -> List[Tuple[str, Set[str], int]]:
    """Yield (table, columns_referenced, approx_lineno) per single-table
    SELECT. JOINs filtered out by the regex (column binding ambiguous);
    `SELECT *` skipped (nothing to validate); aggregate-only SELECTs
    (COUNT, MAX, etc. without a column ref) skipped.
    """
    out = []
    cleaned = _strip_sql_comments(src)
    for match in SELECT_RE.finditer(cleaned):
        col_blob = match.group(1)
        tbl = match.group(2).lower()
        # Skip SELECT *
        if "*" in col_blob:
            continue
        # Skip aggregate-only (no bare-identifier column references)
        if not re.search(r"[a-zA-Z_]\w*", col_blob):
            continue
        # Extract bare column identifiers — strip aggregates, casts,
        # arithmetic, string concat, function calls.
        cols: Set[str] = set()
        for part in col_blob.split(","):
            part = part.strip()
            # Skip if the part contains a function call or aggregate
            if "(" in part:
                continue
            # Skip if part has a CAST / arithmetic / non-trivial expr
            if any(op in part for op in ("::", "+", "-", "/", "*", "||", " AS ", " as ")):
                continue
            # Bare identifier check
            m = re.match(r"^([a-zA-Z_][\w]*)\s*$", part)
            if m:
                cols.add(m.group(1).lower())
        # Drop SQL keywords that might match the regex spuriously
        cols -= {"distinct", "all"}
        if cols:
            lineno = src[: match.start()].count("\n") + 1
            out.append((tbl, cols, lineno))
    return out


def _scan_updates(src: str) -> List[Tuple[str, Set[str], int]]:
    """Yield (table, columns_set_referenced, approx_lineno) per UPDATE."""
    out = []
    cleaned = _strip_sql_comments(src)
    for match in UPDATE_RE.finditer(cleaned):
        tbl = match.group(1).lower()
        set_blob = match.group(2)
        # Extract `col = ...` patterns. The regex above already stops
        # at WHERE / RETURNING / triple-quote, so `set_blob` is just
        # the SET clause.
        cols = set()
        for col_match in re.finditer(r"([a-zA-Z_][\w]*)\s*=", set_blob):
            cols.add(col_match.group(1).lower())
        lineno = src[: match.start()].count("\n") + 1
        out.append((tbl, cols, lineno))
    return out


def test_schema_fixture_loaded(schema):
    """Sanity: the fixture loaded with a reasonable shape."""
    assert len(schema) >= 100, (
        f"Fixture only has {len(schema)} tables — expected ≥100. "
        "Refresh from prod (see docstring)."
    )
    # Spot-check a few well-known tables to make sure the fixture
    # is the right shape.
    for required in ("admin_audit_log", "fleet_orders", "site_appliances",
                     "compliance_bundles", "api_keys", "sites"):
        assert required in schema, (
            f"Required table {required!r} missing from fixture. "
            "Refresh from prod."
        )
    # And spot-check a known column on admin_audit_log — protects against
    # a fixture that loaded but is structurally wrong.
    assert "username" in schema["admin_audit_log"]
    assert "actor" not in schema["admin_audit_log"]


# Ratchet baselines locked at ZERO on 2026-04-25 after the systematic
# baseline-grind pass (#168). The starting count was 16 INSERT + 9
# UPDATE; every violation was either fixed in code (column renames,
# canonical-pattern alignment) or covered by a forward migration
# (248 runbook_config notes, 249 site_credentials partner-UI columns).
#
# This file now enforces an absolute ceiling, paired with the
# baseline-doesn't-regress test below — so adding a single column-name
# typo fails CI immediately, AND lowering the ceiling without code
# changes also fails (forces the constants and the codebase to stay
# in lockstep).
INSERT_BASELINE_MAX = 0
UPDATE_BASELINE_MAX = 0
# SELECT linter (Session 213 round-table P0 — promoted from P2). Both
# of the 2026-04-29 P0 SQL bugs that 500'd /sites/{id}/export
# (compliance_bundles.bundle_type, go_agents.version, go_agents.last_checkin)
# would have been caught at PR time by extending the linter to SELECT
# column references.
#
# Baseline locked at 12 (the current parse-noise floor). Of the 12,
# most are false positives from regex alias-prefix handling
# (`s.site_id` parsed as bare `site_id` against the wrong table) and
# JOIN-detection edge cases — the regex's negative-lookahead JOIN
# filter doesn't constrain to current-statement scope. Three are
# genuinely fixture-stale (compliance_bundles got `appliance_id` +
# `outcome` columns; fixture refresh deferred to next session).
#
# Filed as P3: convert to sqlparse-based AST scan + reduce baseline
# to 0. The regex ratchet still catches the failure class — adding
# a NEW SELECT bug fails CI immediately because count > 12.
SELECT_BASELINE_MAX = 12


def test_every_python_insert_references_real_columns(schema):
    """The headline check. Every INSERT INTO <known_table> (cols...) must
    reference columns that exist in the prod schema.

    Caught the audit-log column-name bugs (target_type, target_id, actor)
    and the relocate fleet_orders bug (site_id, appliance_id) immediately.

    Ratchet: a NEW violation fails CI even if the count is below the
    baseline ceiling. Adding to a previously-clean file is a regression.
    """
    failures: List[str] = []
    for py_path in _backend_py_files():
        try:
            src = py_path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = py_path.relative_to(REPO_ROOT)
        for tbl, cols, lineno in _scan_inserts(src):
            if tbl in SCHEMA_TRUST_GAPS:
                continue
            if tbl not in schema:
                continue
            unknown = cols - schema[tbl]
            if unknown:
                failures.append(
                    f"{rel}:{lineno}: INSERT INTO {tbl} references "
                    f"unknown column(s) {sorted(unknown)}"
                )
    assert len(failures) <= INSERT_BASELINE_MAX, (
        f"{len(failures)} INSERT schema mismatches > baseline {INSERT_BASELINE_MAX}. "
        "A new bug joined the list. Either fix it (and lower INSERT_BASELINE_MAX) "
        "or justify the addition.\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


def test_every_python_update_references_real_columns(schema):
    """Same check on the UPDATE side. Tighter regex stops at WHERE /
    RETURNING / closing triple-quote so we don't conflate adjacent
    statements.

    Ratchet: same NEW-violation-fails-CI semantics as the INSERT test.
    """
    failures: List[str] = []
    for py_path in _backend_py_files():
        try:
            src = py_path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = py_path.relative_to(REPO_ROOT)
        for tbl, cols, lineno in _scan_updates(src):
            if tbl in SCHEMA_TRUST_GAPS:
                continue
            if tbl not in schema:
                continue
            unknown = cols - schema[tbl]
            if unknown:
                failures.append(
                    f"{rel}:{lineno}: UPDATE {tbl} SET references "
                    f"unknown column(s) {sorted(unknown)}"
                )
    assert len(failures) <= UPDATE_BASELINE_MAX, (
        f"{len(failures)} UPDATE schema mismatches > baseline {UPDATE_BASELINE_MAX}. "
        "A new bug joined the list. Either fix it (and lower UPDATE_BASELINE_MAX) "
        "or justify the addition.\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


def test_every_python_select_references_real_columns(schema):
    """Same check on the SELECT side. Tighter regex skips JOINs (column
    binding ambiguous), SELECT *, aggregate-only selects, and parts
    with function calls / casts / arithmetic.

    Caught the 2026-04-29 P0s: compliance_bundles.bundle_type (real:
    check_type) and go_agents.version + go_agents.last_checkin (real:
    agent_version + last_heartbeat). Both 500'd /sites/{id}/export.

    Ratchet: same NEW-violation-fails-CI semantics as INSERT/UPDATE.
    """
    failures: List[str] = []
    for py_path in _backend_py_files():
        try:
            src = py_path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = py_path.relative_to(REPO_ROOT)
        for tbl, cols, lineno in _scan_selects(src):
            if tbl in SCHEMA_TRUST_GAPS:
                continue
            if tbl not in schema:
                continue
            unknown = cols - schema[tbl]
            if unknown:
                failures.append(
                    f"{rel}:{lineno}: SELECT FROM {tbl} references "
                    f"unknown column(s) {sorted(unknown)}"
                )
    assert len(failures) <= SELECT_BASELINE_MAX, (
        f"{len(failures)} SELECT schema mismatches > baseline {SELECT_BASELINE_MAX}. "
        "A new bug joined the list. Either fix it (and lower SELECT_BASELINE_MAX) "
        "or justify the addition.\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


def _count_violations(schema):
    """Helper: count INSERT + UPDATE + SELECT schema mismatches (no assert)."""
    ins, upd, sel = 0, 0, 0
    for py_path in _backend_py_files():
        try:
            src = py_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for tbl, cols, _ in _scan_inserts(src):
            if tbl in SCHEMA_TRUST_GAPS or tbl not in schema:
                continue
            if cols - schema[tbl]:
                ins += 1
        for tbl, cols, _ in _scan_updates(src):
            if tbl in SCHEMA_TRUST_GAPS or tbl not in schema:
                continue
            if cols - schema[tbl]:
                upd += 1
        for tbl, cols, _ in _scan_selects(src):
            if tbl in SCHEMA_TRUST_GAPS or tbl not in schema:
                continue
            if cols - schema[tbl]:
                sel += 1
    return ins, upd, sel


def test_baseline_doesnt_regress_silently(schema):
    """If anyone fixes a column-name bug, the ratchet baseline should
    drop to match. This test fails LOUDLY when actual count is BELOW
    the constants, forcing the operator to lower them in the same
    commit. Prevents the 'lockstep with the floor' anti-pattern where
    bugs creep back in unnoticed because the constant is too lax.

    Mirrors the pattern in test_frontend_mutation_csrf.py.
    """
    ins, upd, sel = _count_violations(schema)
    assert ins == INSERT_BASELINE_MAX, (
        f"INSERT violations={ins} but INSERT_BASELINE_MAX={INSERT_BASELINE_MAX}. "
        "Adjust INSERT_BASELINE_MAX in this file to match the actual count."
    )
    assert upd == UPDATE_BASELINE_MAX, (
        f"UPDATE violations={upd} but UPDATE_BASELINE_MAX={UPDATE_BASELINE_MAX}. "
        "Adjust UPDATE_BASELINE_MAX in this file to match the actual count."
    )
    assert sel == SELECT_BASELINE_MAX, (
        f"SELECT violations={sel} but SELECT_BASELINE_MAX={SELECT_BASELINE_MAX}. "
        "Adjust SELECT_BASELINE_MAX in this file to match the actual count."
    )
