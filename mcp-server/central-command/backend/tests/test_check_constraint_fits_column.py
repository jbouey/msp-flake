"""CI gate: every value listed in a migration's CHECK IN-list must
fit the column's max_length.

D6 disposition round-table 2026-05-01: the auto_recovered/VARCHAR(10)
outage was a 35-min prod fire because mig 264 added 'auto_recovered'
(14 chars) to a CHECK list against a VARCHAR(10) column. Postgres
accepts CHECK literals longer than the column type at constraint
creation; the failure only surfaces at INSERT runtime — and got
caught by an outer try/except style in the call path that masked
the StringDataRightTruncationError, leading to 35 min of evidence-
chain stall before substrate fired the meta-invariant.

This gate parses ALL migration files for `ADD CONSTRAINT … CHECK
… IN (...)` patterns, looks up the column's max_length in the
prod-schema fixture, and asserts the max literal length is ≤ the
column max_length. Fails CI immediately on any new mig that would
write a too-long literal.

Limitations (documented):
- Only catches `IN (...)` style enums. Doesn't catch CHECK with
  arithmetic / regex / arbitrary expressions on the column.
- Uses prod-schema fixture (`prod_column_widths.json`) which lags
  reality if a same-PR migration extends the column width before
  changing the CHECK list. In that case, refresh the fixture in
  the same PR.
"""
from __future__ import annotations

import json
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
MIGRATIONS_DIR = (
    REPO_ROOT / "mcp-server" / "central-command" / "backend" / "migrations"
)
SCHEMA_FIXTURE = (
    REPO_ROOT
    / "mcp-server"
    / "central-command"
    / "backend"
    / "tests"
    / "fixtures"
    / "schema"
    / "prod_column_widths.json"
)


# Match: ALTER TABLE <table> ... ADD CONSTRAINT <name> CHECK ( <body> )
# Multi-line tolerant. Captures table + check body.
_ADD_CHECK_PATTERN = re.compile(
    r"ALTER\s+TABLE\s+(?:public\.)?(\w+)\s+"
    r"(?:[^;]*?)\s*"
    r"ADD\s+CONSTRAINT\s+\w+\s+CHECK\s*\(\s*(.+?)\s*\)\s*;",
    re.IGNORECASE | re.DOTALL,
)

# Inside a CHECK body, find: <column> IN (<literal_list>) where each
# literal is a quoted string. Tolerates `column::text IN (...)` cast.
_IN_CLAUSE_PATTERN = re.compile(
    r"(\w+)(?:::\w+)?\s+IN\s*\(\s*((?:'[^']*'\s*,?\s*)+)\s*\)",
    re.IGNORECASE,
)


def _parse_string_literals(literal_blob: str) -> list[str]:
    """Extract `'value'` literals from `'a', 'b', 'c'` style blob."""
    return [m.group(1) for m in re.finditer(r"'([^']*)'", literal_blob)]


def test_check_constraint_values_fit_column_widths():
    """Walk every migration in numeric order. Track per (table, column)
    the LATEST declared CHECK literals — later ADD CONSTRAINT supersedes
    earlier (e.g. mig 264 added 'auto_recovered'/VARCHAR(10) bug;
    mig 267 dropped+re-added the CHECK with 'recovered' — only the
    267-state lives on prod).

    Validate only the LATEST per pair against the prod-fixture column
    width. This avoids false-positives on historical-drift values that
    have been superseded by later migrations.
    """

    assert SCHEMA_FIXTURE.exists(), (
        f"Schema fixture missing: {SCHEMA_FIXTURE}. "
        "Refresh from prod (information_schema.columns)."
    )
    schema = json.loads(SCHEMA_FIXTURE.read_text())

    # (table, column) -> (mig_filename, literals_list)
    latest_check: dict[tuple[str, str], tuple[str, list[str]]] = {}

    for mig_path in sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda p: p.name):
        src = mig_path.read_text()
        for m in _ADD_CHECK_PATTERN.finditer(src):
            table_name = m.group(1).lower()
            check_body = m.group(2)
            for in_match in _IN_CLAUSE_PATTERN.finditer(check_body):
                column_name = in_match.group(1).lower()
                literals_blob = in_match.group(2)
                literals = _parse_string_literals(literals_blob)
                if not literals:
                    continue
                # Later migration wins (numeric filename ordering).
                latest_check[(table_name, column_name)] = (
                    mig_path.name, literals,
                )

    failures: list[str] = []
    for (table_name, column_name), (mig_filename, literals) in latest_check.items():
        table_cols = schema.get(table_name)
        if table_cols is None:
            continue
        max_len = table_cols.get(column_name)
        if max_len is None:
            continue
        for lit in literals:
            if len(lit) > max_len:
                failures.append(
                    f"{mig_filename}: ALTER TABLE {table_name} "
                    f"ADD CONSTRAINT … CHECK ({column_name} IN "
                    f"…'{lit}'…) — literal '{lit}' is "
                    f"{len(lit)} chars but column "
                    f"{table_name}.{column_name} is "
                    f"VARCHAR({max_len}). CHECK accepts the "
                    f"value but INSERT raises "
                    f"StringDataRightTruncationError. Either "
                    f"shorten the literal OR ALTER COLUMN TYPE "
                    f"BEFORE adding the CHECK in the same migration. "
                    f"(This is the LATEST declared CHECK for this "
                    f"column; earlier superseded versions are not "
                    f"validated.)"
                )

    if failures:
        joined = "\n  - ".join(failures)
        raise AssertionError(
            f"CHECK literal vs column width drift in "
            f"{len(failures)} location(s) (D6 round-table 2026-05-01):"
            f"\n  - {joined}"
        )


def test_schema_fixture_loaded_with_widths():
    """Sanity: fixture has ≥100 tables + the canonical sample width."""
    assert SCHEMA_FIXTURE.exists()
    schema = json.loads(SCHEMA_FIXTURE.read_text())
    assert len(schema) >= 100, f"Fixture only has {len(schema)} tables"
    # incidents.resolution_tier is the canonical D6 audit sample
    # (auto_recovered/VARCHAR(10) outage).
    assert schema.get("incidents", {}).get("resolution_tier") == 10, (
        "Sanity check failed: incidents.resolution_tier should be VARCHAR(10) "
        "in the prod fixture (the canonical D6 audit sample)."
    )
