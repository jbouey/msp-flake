"""CI gate: every `*_pg.py` test fixture's `CREATE TABLE fleet_orders` block
must include the `signing_method` column.

Vault Phase C P0 cross-cutting (audit/coach-vault-p0-bundle-gate-a-redo-2-
2026-05-13.md). The 2026-05-12 revert chain's iter-2 root cause was schema
drift between prod migration 177 (which added `fleet_orders.signing_method`)
and these test fixtures (which build their own stripped-down CREATE TABLE
blocks for *_pg.py docker-postgres tests).

The drift was silent until an INSERT path actually wrote the column, then
every test importing the fixture broke at `column "signing_method" of
relation "fleet_orders" does not exist`.

This gate pins the lockstep — every fleet_orders fixture must mirror prod's
mig 177 column. When a future migration adds another column to fleet_orders,
extend the EXPECTED_COLUMNS set + this docstring.

Class also applies to other prod tables whose INSERT paths are exercised by
*_pg.py fixtures — generalize per-table if drift appears elsewhere.
"""
from __future__ import annotations

import pathlib
import re

_TESTS = pathlib.Path(__file__).resolve().parent

# Columns that must appear in every `CREATE TABLE fleet_orders` fixture
# block. Extend this set when prod adds new columns whose INSERT paths
# code-exercises in *_pg.py tests.
EXPECTED_COLUMNS = {
    "signing_method",  # mig 177 — Vault Phase C P0 #3
}


def _create_table_blocks(src: str) -> list[str]:
    """Return all `CREATE TABLE fleet_orders ... );` blocks."""
    return re.findall(
        r"CREATE TABLE fleet_orders\b[^;]+;",
        src,
        re.DOTALL,
    )


def test_every_pg_fixture_fleet_orders_has_signing_method():
    """Every `*_pg.py` file with a `CREATE TABLE fleet_orders` must
    include each EXPECTED_COLUMN in the column list.
    """
    violations: list[str] = []
    fixture_files = sorted(_TESTS.glob("*_pg.py"))
    assert fixture_files, "expected at least one *_pg.py fixture file"
    for pg in fixture_files:
        if pg.name == pathlib.Path(__file__).name:
            continue
        try:
            src = pg.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for block in _create_table_blocks(src):
            for col in EXPECTED_COLUMNS:
                if col not in block:
                    violations.append(
                        f"{pg.name}: CREATE TABLE fleet_orders block missing "
                        f"required column {col!r}. Add "
                        f"`signing_method TEXT NOT NULL DEFAULT 'file'` to "
                        f"the column list. Mig 177 added this on prod; "
                        f"fixtures must mirror or the INSERT path explodes."
                    )
    assert not violations, "\n".join(violations)
