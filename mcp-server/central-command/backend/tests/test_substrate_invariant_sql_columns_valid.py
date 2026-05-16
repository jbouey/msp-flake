"""CI gate (Gate B C5a-rev1, 2026-05-16): every substrate invariant's
SQL must reference real columns per the prod schema fixture.

This gate exists because two `_check_*` functions shipped to prod in
27c8fdc1 referencing `compliance_bundles.details` (column doesn't
exist) and `l2_decisions.details` / `evidence_bundles.details` /
`aggregated_pattern_stats.details` (none have a `details` column).
Both invariants raised `UndefinedColumnError` on every 60s tick and
NEVER FIRED — the sev1 chain-integrity backstop was structurally
broken. Per Gate B verdict
`audit/coach-c5a-pha-94-closure-gate-b-2026-05-16.md` §P0-1+P0-2.

Scan logic:
  1. Walk every `_check_*` function in `assertions.py`.
  2. Extract triple-quoted SQL strings inside `conn.fetch(...)` /
     `conn.fetchrow(...)` / `conn.execute(...)`.
  3. Find `<table>.<column>` patterns (`tbl.col` form). For each,
     check that `col` exists on `tbl` per `prod_columns.json`.
  4. Also find `<alias>.<column>` patterns where `alias` is bound
     via `FROM <table> <alias>` / `JOIN <table> <alias>` — resolve
     the alias to the real table + validate.

Known-OK shapes (excluded from scan):
  - bare `column` (no qualifier) — too noisy without a SQL parser
  - `details->>'key'` JSONB expressions — `details` is the only thing
    that needs to be a real column; the key is just a JSONB lookup
  - PG built-in columns: `oid`, `tableoid`, `xmin`, `xmax`, etc.
  - Standalone PG functions: `now()`, `current_date`, etc.

This is a SOURCE-SHAPE gate. The runtime check would catch it on
first tick BUT the assertion engine catches UndefinedColumnError
and logs at WARNING — silent failure class. This gate is the
structural prevention.
"""
from __future__ import annotations

import json
import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_ASSERTIONS = _BACKEND / "assertions.py"
_SCHEMA_FIXTURE = _BACKEND / "tests" / "fixtures" / "schema" / "prod_columns.json"


# Tables that aren't in the prod fixture but are PG built-ins or
# substrate engine internals — skip column validation.
_SCHEMA_GAPS = {
    "pg_settings",
    "pg_class",
    "pg_indexes",
    "pg_stat_activity",
    "pg_stat_user_tables",
    "information_schema.tables",
    "information_schema.columns",
}


def _load_schema() -> dict:
    return json.loads(_SCHEMA_FIXTURE.read_text())


def _extract_check_function_sql() -> list[tuple[str, str]]:
    """Return [(function_name, sql_string)] for every `_check_*` function
    in assertions.py. Each function may contribute multiple SQL strings
    (one per conn.fetch/.fetchrow/.execute call)."""
    src = _ASSERTIONS.read_text()
    # Find every `async def _check_NAME(`, capture body up to next `async def`
    # or end of file.
    fn_pat = re.compile(
        r"async\s+def\s+(_check_\w+)\s*\(.*?(?=\nasync\s+def\s+|\nALL_ASSERTIONS|\Z)",
        re.DOTALL,
    )
    sql_pat = re.compile(
        r"""conn\.(?:fetch|fetchrow|fetchval|execute)\s*\(\s*[fr]?["']{3}(.*?)["']{3}""",
        re.DOTALL,
    )
    result: list[tuple[str, str]] = []
    for m in fn_pat.finditer(src):
        name = m.group(1)
        body = m.group(0)
        for s in sql_pat.finditer(body):
            result.append((name, s.group(1)))
    return result


# Match `alias.column` qualified refs. Excludes JSONB `details->>'foo'`
# (the `->>` operator is not `.` so JSONB lookups don't false-match
# columns). Also excludes 2-char (table alias) cases like `s.id`
# carefully — we treat the LHS as an alias to resolve.
_QUALIFIED_REF_PAT = re.compile(
    r"(?<![\w'])([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)(?!['\w])",
    re.IGNORECASE,
)


def _resolve_aliases(sql: str) -> dict[str, str]:
    """Find `FROM <table> [AS] <alias>` and `JOIN <table> [AS] <alias>`
    + return alias→table mapping. Bare `FROM <table>` (no alias) maps
    the table to itself."""
    aliases: dict[str, str] = {}
    # Pattern: FROM tbl [AS] alias  /  JOIN tbl [AS] alias
    for m in re.finditer(
        r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)(?:\s+(?:AS\s+)?([a-z_][a-z0-9_]*))?",
        sql, re.IGNORECASE,
    ):
        tbl = m.group(1)
        alias = m.group(2) or tbl
        # Skip keywords that aren't tables (LEFT JOIN appliances ON ...
        # would set alias='ON' if the regex grabs the next word; guard).
        if alias.upper() in ("ON", "WHERE", "AND", "OR", "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "LATERAL"):
            alias = tbl
        aliases[alias.lower()] = tbl.lower()
    return aliases


def _column_refs(sql: str) -> set[tuple[str, str]]:
    """Extract (alias_or_table, column) pairs from the SQL. Excludes
    JSONB sub-key lookups via `->>` (handled separately because
    `details->>'soak_test'` is fine if `details` is a real column —
    only the `details` part needs validation, which the qualified-ref
    walker catches naturally)."""
    refs: set[tuple[str, str]] = set()
    for m in _QUALIFIED_REF_PAT.finditer(sql):
        lhs, rhs = m.group(1).lower(), m.group(2).lower()
        refs.add((lhs, rhs))
    return refs


# PG built-in column names + system pseudo-columns + common alias-of-
# alias false-positives the regex generates. These are NEVER flagged.
_OK_COLUMNS = {
    "ctid", "oid", "tableoid", "xmin", "xmax", "cmin", "cmax",
    # CTE names + with-clause aliases — these are not "tables" but
    # appear in `FROM cte` shape and have their own column space.
    # The scan can't statically resolve CTE columns, so we soft-allow.
}


# CTE / sub-query aliases that appear as `FROM <alias>` after a `WITH
# <alias> AS (...)` clause. Treat as out-of-scope for column
# validation — the CTE defines its own column space.
def _cte_names(sql: str) -> set[str]:
    cte: set[str] = set()
    for m in re.finditer(r"\b(?:WITH|,)\s+([a-z_][a-z0-9_]*)\s+AS\s*\(", sql, re.IGNORECASE):
        cte.add(m.group(1).lower())
    return cte


def test_every_substrate_invariant_sql_references_real_columns():
    schema = _load_schema()
    schema_lc = {tbl.lower(): {c.lower() for c in cols} for tbl, cols in schema.items()}

    failures: list[str] = []
    fn_sql_pairs = _extract_check_function_sql()
    assert fn_sql_pairs, (
        "could not extract any `_check_*` function SQL — regex broken?"
    )

    for fn_name, sql in fn_sql_pairs:
        aliases = _resolve_aliases(sql)
        ctes = _cte_names(sql)
        for lhs, col in _column_refs(sql):
            # Skip if LHS is a CTE / sub-query alias
            if lhs in ctes:
                continue
            # Skip if column is a known PG/system column
            if col in _OK_COLUMNS:
                continue
            # Resolve alias → real table
            tbl = aliases.get(lhs, lhs)
            # Skip if table is a known gap (PG internals)
            if tbl in _SCHEMA_GAPS or any(tbl.startswith(p) for p in ("pg_", "information_schema")):
                continue
            # Resolve against schema fixture
            if tbl not in schema_lc:
                # LHS isn't a real table OR known alias — could be a
                # column-on-row reference like `r["col"]` parsed wrong.
                # Skip silently to avoid false-positives.
                continue
            if col not in schema_lc[tbl]:
                failures.append(
                    f"{fn_name}: SQL references {tbl}.{col} but column "
                    f"does not exist per prod_columns.json fixture"
                )

    assert not failures, (
        "Substrate invariant SQL references columns that don't exist "
        "in prod schema fixture. These invariants raise "
        "UndefinedColumnError every 60s tick + silently fail. Gate B "
        "C5a-rev1 (2026-05-16) found 2 P0s of this exact class — fix "
        "the SQL or update the fixture:\n  "
        + "\n  ".join(failures)
    )


def test_synthetic_marker_invariants_use_sites_synthetic():
    """Specifically pin that the 2 synthetic-leak invariants
    (load_test_marker_in_compliance_bundles +
    synthetic_traffic_marker_orphan) use `sites.synthetic` as the
    authority — NOT a per-row `details.synthetic` marker that
    doesn't exist on most target tables. Rev1 closure sentinel."""
    src = _ASSERTIONS.read_text()
    # Locate _check_load_test_marker_in_compliance_bundles
    m = re.search(
        r"async\s+def\s+_check_load_test_marker_in_compliance_bundles\s*\(.*?"
        r"return\s+\[",
        src, re.DOTALL,
    )
    assert m, "could not locate _check_load_test_marker_in_compliance_bundles"
    body = m.group(0)
    assert "sites WHERE synthetic = TRUE" in body or "synthetic = TRUE" in body, (
        "_check_load_test_marker_in_compliance_bundles must consult "
        "sites.synthetic — using a per-row marker on compliance_"
        "bundles is the broken shape that prompted Gate B C5a-rev1."
    )
    assert "details->>'synthetic'" not in body, (
        "_check_load_test_marker_in_compliance_bundles is reaching "
        "for `details->>'synthetic'` again — that column does not "
        "exist on compliance_bundles. Use sites.synthetic instead."
    )

    # Locate _check_synthetic_traffic_marker_orphan
    m = re.search(
        r"async\s+def\s+_check_synthetic_traffic_marker_orphan\s*\(.*?"
        r"return\s+violations",
        src, re.DOTALL,
    )
    assert m, "could not locate _check_synthetic_traffic_marker_orphan"
    body = m.group(0)
    assert "JOIN sites" in body and "s.synthetic = TRUE" in body, (
        "_check_synthetic_traffic_marker_orphan must JOIN sites + "
        "filter on s.synthetic=TRUE. Per-row details lookups on "
        "l2_decisions/evidence_bundles/aggregated_pattern_stats fail "
        "silently — those tables don't have a `details` column."
    )
