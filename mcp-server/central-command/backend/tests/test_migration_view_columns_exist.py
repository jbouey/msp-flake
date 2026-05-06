"""CI gate: every column referenced in a migration's CREATE VIEW / CREATE
FUNCTION exists in the schema fixture.

Closes the gap that produced the 2026-05-06 deploy failure: migration
285 introduced `v_l2_outcomes` view referencing `i.host_id`, but the
`incidents` table has no `host_id` column. The migration applied
locally (psql isn't strict about referenced-but-not-yet-evaluated
columns in view DDL planning) but failed at deploy. Round-table audit
+ Maya 2nd-eye both operated against migration source code, not
against the schema fixture, so the bug slipped past two adversarial
gates.

This test scans every migration's CREATE VIEW + CREATE FUNCTION
blocks for qualified column references (`<alias>.<column>`), builds
an alias → table map from the FROM/JOIN clauses, and cross-checks
each (table, column) pair against
`tests/fixtures/schema/prod_columns.json` — the same fixture that
test_sql_columns_match_schema uses as the production schema oracle.

Per-block escape hatch: a `-- noqa: schema-fixture-cross-check`
comment on the same line as `CREATE VIEW` / `CREATE OR REPLACE
FUNCTION` skips that block.

Why blocks and not whole files: a single migration can contain
multiple view/function definitions, some legitimately doing dynamic
DDL or referencing columns added in the same migration. Block-level
scoping keeps false positives bounded.
"""
from __future__ import annotations

import json
import pathlib
import re
import textwrap

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_MIGRATIONS = _BACKEND / "migrations"
_SCHEMA_FIXTURE = _BACKEND / "tests" / "fixtures" / "schema" / "prod_columns.json"

# Cutoff: this gate scans migrations >= this number. Older migrations
# may use patterns that don't fit the regex (older SQL styles,
# dynamic DDL via plpgsql), and they've already been deploy-tested
# in production. Future migrations get the gate.
_MIGRATION_FLOOR = 280

# Skip-block sentinel users can put on the same line as the CREATE.
_NOQA_PATTERN = re.compile(
    r"--\s*noqa:\s*schema-fixture-cross-check", re.IGNORECASE
)

# Match the START of a CREATE VIEW or CREATE FUNCTION block.
# We split the migration on these starts, then extract each statement
# up to its terminating `;` (handling plpgsql `$$…$$` quoted bodies
# specially since they contain semicolons).
_BLOCK_START = re.compile(
    r"""
    CREATE\s+(?:OR\s+REPLACE\s+)?(?P<kind>VIEW|FUNCTION)
    \s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Match `FROM <table> [AS] <alias>` and `JOIN <table> [AS] <alias>`.
# The optional AS keyword. Aliases are single-word identifiers;
# `pg_class c` is the typical shape.
_ALIAS_PATTERN = re.compile(
    r"""
    \b(?:FROM|JOIN)\s+
    (?P<table>[A-Za-z_][A-Za-z0-9_]*)
    (?:\s+AS)?
    \s+(?P<alias>[a-z_][a-z0-9_]*)
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Match `<alias>.<column>` references where alias is a single lowercase
# letter or short identifier (typical SQL aliasing convention).
# Excludes things like `pg_catalog.pg_class` which use schema prefixes.
_COL_REF_PATTERN = re.compile(
    r"""
    \b(?P<alias>[a-z_][a-z0-9_]*)
    \.(?P<column>[a-z_][a-z0-9_]*)
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)


# Reserved alias-like prefixes that aren't real table aliases.
_RESERVED_ALIASES = {
    "pg_catalog", "pg_class", "pg_namespace", "information_schema",
    "public", "current_user", "session_user", "current_setting",
    "now",  # NOW() function call
    "extract",  # EXTRACT() function call
    "jsonb_build_object",  # JSONB function
    "make_interval",  # interval-builder
}

# Column names that appear in pseudo-column form in PG (e.g.,
# `OLD.column` and `NEW.column` in trigger functions). These aren't
# JOINed tables; we skip them.
_TRIGGER_PSEUDO_ALIASES = {"OLD", "NEW", "old", "new"}


def _migration_files() -> list[pathlib.Path]:
    """Return migration files >= floor, sorted."""
    out = []
    for p in sorted(_MIGRATIONS.glob("*.sql")):
        m = re.match(r"(\d+)_", p.name)
        if not m:
            continue
        if int(m.group(1)) < _MIGRATION_FLOOR:
            continue
        out.append(p)
    return out


def _strip_sql_comments(src: str) -> str:
    """Remove SQL line comments (`-- ...`) but PRESERVE the noqa
    sentinel — the block-scope check needs to see it. We swap the
    sentinel for a placeholder, strip comments, then restore."""
    placeholder = "<<__noqa_block__>>"
    src = _NOQA_PATTERN.sub(placeholder, src)
    src = re.sub(r"--[^\n]*", "", src)
    src = src.replace(placeholder, "-- noqa: schema-fixture-cross-check")
    return src


def _extract_blocks(src: str) -> list[tuple[str, str, str]]:
    """Return [(kind, name, body), …] for each CREATE VIEW /
    CREATE FUNCTION statement.

    Walks the source forward: at each match of the START pattern,
    the statement extends to the next `;` at top level — but plpgsql
    function bodies wrap a `$$ … $$` block that itself contains
    semicolons. We detect the `$$` open/close and treat the matched
    region as one logical token.
    """
    out: list[tuple[str, str, str]] = []
    starts = list(_BLOCK_START.finditer(src))
    for i, m in enumerate(starts):
        kind = m.group("kind")
        name = m.group("name")
        # The header is the line containing the CREATE keyword.
        line_start = src.rfind("\n", 0, m.start()) + 1
        line_end = src.find("\n", m.end())
        header = src[line_start: line_end if line_end != -1 else len(src)]
        if _NOQA_PATTERN.search(header):
            continue
        # Walk forward from the start, tracking $$ depth + paren depth.
        # Statement ends at the first `;` outside any $$ block.
        idx = m.end()
        in_dollar = False
        while idx < len(src):
            if src.startswith("$$", idx):
                in_dollar = not in_dollar
                idx += 2
                continue
            ch = src[idx]
            if ch == ";" and not in_dollar:
                break
            idx += 1
        body = src[m.start(): idx]
        out.append((kind, name, body))
    return out


def _build_alias_map(body: str) -> dict[str, str]:
    """alias → table from FROM / JOIN clauses in the block body."""
    aliases: dict[str, str] = {}
    for m in _ALIAS_PATTERN.finditer(body):
        alias = m.group("alias")
        table = m.group("table")
        # Skip uppercase reserved keywords that the regex picked up
        # (LATERAL, etc.). Aliases are typically single letters.
        if alias.upper() in {"LATERAL", "EXTRACT", "ON", "USING", "ARRAY"}:
            continue
        aliases[alias] = table
    return aliases


def _column_refs(body: str) -> list[tuple[str, str, int]]:
    """Return [(alias, column, char_offset), …] from the body."""
    out = []
    for m in _COL_REF_PATTERN.finditer(body):
        out.append((m.group("alias"), m.group("column"), m.start()))
    return out


def _load_fixture() -> dict[str, set[str]]:
    data = json.loads(_SCHEMA_FIXTURE.read_text())
    return {tbl: set(cols) for tbl, cols in data.items()}


def test_migration_view_function_columns_exist():
    """The actual gate."""
    fixture = _load_fixture()
    failures: list[str] = []

    for mig in _migration_files():
        src = mig.read_text()
        # Strip comments but preserve the noqa sentinel so block-scope
        # opt-out still works.
        src_clean = _strip_sql_comments(src)
        blocks = _extract_blocks(src_clean)
        for kind, name, body in blocks:
            aliases = _build_alias_map(body)
            for alias, column, _offset in _column_refs(body):
                # Skip things that aren't real table aliases.
                if alias.lower() in _RESERVED_ALIASES:
                    continue
                if alias in _TRIGGER_PSEUDO_ALIASES:
                    continue
                # Unknown alias means we couldn't match it to a FROM/JOIN
                # — could be a CTE alias or function-local variable.
                # Skip rather than false-positive.
                if alias not in aliases:
                    continue
                table = aliases[alias]
                if table not in fixture:
                    # Table not in fixture (could be a CTE name or
                    # a freshly-created table not yet in fixture).
                    # Skip — schema_fixture_drift catches this class.
                    continue
                if column not in fixture[table]:
                    failures.append(
                        f"{mig.name} {kind} {name}: "
                        f"{alias}.{column} → references nonexistent "
                        f"column `{column}` on table `{table}` "
                        f"(per prod_columns.json fixture; the column "
                        f"is missing from {table}'s schema)"
                    )

    if failures:
        msg = textwrap.dedent("""
            Migration view/function references column(s) not in the
            production schema fixture. The migration would deploy locally
            (psql doesn't validate view body columns until first SELECT)
            but FAIL at deploy when the view is exercised. This was the
            class of bug that broke deploy 25432114856 on 2026-05-06.

            Fix: either (a) drop the bad column reference, (b) add a
            schema-side migration to introduce the column, or (c) add
            `-- noqa: schema-fixture-cross-check` on the same line as
            `CREATE VIEW` / `CREATE OR REPLACE FUNCTION` if you have a
            justified exception (e.g. dynamic DDL via plpgsql that the
            fixture can't capture).

            Findings:
        """).strip()
        raise AssertionError(msg + "\n\n" + "\n".join(f"  - {f}" for f in failures))


def test_gate_would_have_caught_the_2026_05_06_host_id_bug():
    """Self-test: prove the parser+matcher logic catches the exact
    bug class that broke deploy 25432114856. Constructs a synthetic
    migration string with the same shape (CREATE VIEW joining
    incidents with `host_id` in projection) and asserts the gate
    flags it."""
    fixture = _load_fixture()
    # 'host_id' is not in incidents per the fixture; this is the
    # truth that the gate exercises.
    assert "host_id" not in fixture.get("incidents", set()), (
        "Self-test prerequisite: incidents.host_id must NOT be in "
        "the fixture for this test to be meaningful. If host_id is "
        "now a real column on incidents, update the fixture and "
        "remove this self-test."
    )

    bad_migration = """
        CREATE OR REPLACE VIEW v_test_bug_repro AS
        SELECT
            i.id,
            i.host_id,         -- this should fail the gate
            i.appliance_id,
            i.incident_type
          FROM incidents i;
    """
    src_clean = _strip_sql_comments(bad_migration)
    blocks = _extract_blocks(src_clean)
    assert len(blocks) == 1, (
        f"Self-test parser found {len(blocks)} blocks; expected 1. "
        f"Block-extraction regex may be stale."
    )
    kind, name, body = blocks[0]
    aliases = _build_alias_map(body)
    assert aliases.get("i") == "incidents", (
        f"Self-test alias map: expected 'i'→'incidents'; got {aliases!r}"
    )
    refs = _column_refs(body)
    bad_refs = [
        (a, c) for a, c, _ in refs
        if aliases.get(a) == "incidents" and c not in fixture.get("incidents", set())
    ]
    assert ("i", "host_id") in bad_refs, (
        "Self-test FAILED: the gate's column-extraction did NOT flag "
        "i.host_id against the fixture's incidents schema. The exact "
        "bug class that broke deploy 2026-05-06 would slip past the "
        "gate. Investigate the regex or alias-map logic."
    )


def test_gate_finds_known_good_views():
    """Sanity: the gate at least PARSES the migrations it should
    scan. If this regresses, the test above could trivially pass
    by parsing nothing."""
    files = _migration_files()
    assert len(files) >= 3, (
        f"Expected ≥3 migrations >= mig {_MIGRATION_FLOOR}; "
        f"found {len(files)}. Did the floor or the migrations "
        f"directory change?"
    )
    # And at least one of the recent migrations DOES contain a
    # CREATE VIEW or CREATE FUNCTION block — proves the parser
    # is finding them.
    found_blocks = 0
    for f in files:
        src = _strip_sql_comments(f.read_text())
        found_blocks += len(_extract_blocks(src))
    assert found_blocks >= 1, (
        "Parser found ZERO view/function blocks across all in-scope "
        "migrations. Either the migrations changed shape (regex "
        "stale) OR no recent migrations create views/functions "
        "(unexpected — mig 285 + 286 do)."
    )
