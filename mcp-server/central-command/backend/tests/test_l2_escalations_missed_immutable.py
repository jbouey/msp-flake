"""CI gate — `l2_escalations_missed` is INSERT-ONLY.

Maya P0-C verdict 2026-05-12
(`audit/maya-p0c-backfill-decision-2026-05-12.md`) mandates the
disclosure table is INSERT-only. Mutating a disclosed row would itself
be a chain-manipulation event — the exact forgery pattern Session 218
round-table 2026-05-08 RT-1.2 rejected.

This gate is a STATIC scan against the migration file (NOT a live DB
check) — sibling pattern to `tests/test_no_silent_db_write_swallow.py`.
It verifies that the migration introducing the table:

  1. Defines a `BEFORE UPDATE` trigger that raises on UPDATE.
  2. Defines a `BEFORE DELETE` trigger that raises on DELETE.
  3. Both trigger functions reference the table name.
  4. The UPDATE-rejector function body contains `RAISE EXCEPTION`.
  5. The DELETE-rejector function body contains `RAISE EXCEPTION`.

Drift this test catches:
  - Someone removes a trigger but leaves the function.
  - Someone removes a function but leaves the trigger.
  - Someone silently softens the trigger (e.g., raises a NOTICE
    instead of EXCEPTION).
  - Someone changes the trigger to BEFORE INSERT (which would be a
    DoS — the backfill itself couldn't run).
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_MIGRATIONS = _BACKEND / "migrations"

# Migration filename — coordinated with the actual file shipped. Brief
# specified 307 but 307 was already taken by 307_ots_proofs_status_check.sql
# so the disclosure table landed at 308. The gate searches for the file
# by name pattern so a future renumber is caught explicitly.
_MIGRATION_GLOB = "*_l2_escalations_missed.sql"
_TABLE_NAME = "l2_escalations_missed"


def _migration_path() -> pathlib.Path:
    matches = sorted(_MIGRATIONS.glob(_MIGRATION_GLOB))
    assert matches, (
        f"Expected a migration file matching {_MIGRATION_GLOB!r} under "
        f"{_MIGRATIONS}. The l2_escalations_missed disclosure table "
        f"(Maya P0-C verdict 2026-05-12) was not found. If the migration "
        f"was renumbered, update this gate's glob pattern."
    )
    assert len(matches) == 1, (
        f"Multiple migrations match {_MIGRATION_GLOB!r} — only one "
        f"INSERT-only disclosure-table migration should exist:\n"
        + "\n".join(f"  - {p}" for p in matches)
    )
    return matches[0]


def _read_migration() -> str:
    return _migration_path().read_text()


def test_migration_file_exists():
    """Sanity: the migration is on disk and parseable."""
    sql = _read_migration()
    assert _TABLE_NAME in sql, (
        f"Migration body does not reference table {_TABLE_NAME!r}"
    )


def test_table_create_statement_present():
    """The migration must CREATE the table itself (otherwise it's not
    the right migration)."""
    sql = _read_migration()
    pat = re.compile(
        r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?l2_escalations_missed",
        re.IGNORECASE,
    )
    assert pat.search(sql), (
        "Migration must contain CREATE TABLE l2_escalations_missed"
    )


def test_update_trigger_present():
    """A BEFORE UPDATE trigger on the table must exist."""
    sql = _read_migration()
    # CREATE TRIGGER <name> ... BEFORE UPDATE ON l2_escalations_missed
    pat = re.compile(
        r"CREATE\s+TRIGGER\s+\S+\s+BEFORE\s+UPDATE\s+ON\s+l2_escalations_missed",
        re.IGNORECASE | re.DOTALL,
    )
    assert pat.search(sql), (
        "BEFORE UPDATE trigger on l2_escalations_missed is required. "
        "Maya P0-C: rows are INSERT-only; mutating a disclosed row is "
        "itself a chain-manipulation event."
    )


def test_delete_trigger_present():
    """A BEFORE DELETE trigger on the table must exist."""
    sql = _read_migration()
    pat = re.compile(
        r"CREATE\s+TRIGGER\s+\S+\s+BEFORE\s+DELETE\s+ON\s+l2_escalations_missed",
        re.IGNORECASE | re.DOTALL,
    )
    assert pat.search(sql), (
        "BEFORE DELETE trigger on l2_escalations_missed is required. "
        "Audit-trail immutability."
    )


def test_update_function_raises():
    """The function called by the UPDATE trigger must contain
    `RAISE EXCEPTION` (not RAISE NOTICE / WARNING — those allow the
    operation through)."""
    sql = _read_migration()
    # Find function body that mentions UPDATE-denied semantics. The
    # canonical function name in our migration is
    # `l2_escalations_missed_reject_update`. Be flexible to renames as
    # long as the body raises EXCEPTION.
    func_pat = re.compile(
        r"CREATE\s+(OR\s+REPLACE\s+)?FUNCTION\s+(\S+)\s*\(\s*\).*?"
        r"LANGUAGE\s+plpgsql\s+AS\s+\$\$(.*?)\$\$",
        re.IGNORECASE | re.DOTALL,
    )
    funcs = {m.group(2): m.group(3) for m in func_pat.finditer(sql)}
    update_funcs = {
        name: body
        for name, body in funcs.items()
        if "update" in name.lower() and "l2_esc" in name.lower()
    }
    assert update_funcs, (
        "No UPDATE-rejector function found. Expected a plpgsql function "
        "named with `update` + `l2_esc` (e.g., "
        "l2_escalations_missed_reject_update)."
    )
    for name, body in update_funcs.items():
        assert re.search(r"\bRAISE\s+EXCEPTION\b", body, re.IGNORECASE), (
            f"Function {name} must RAISE EXCEPTION on the UPDATE path "
            f"(NOT RAISE NOTICE/WARNING — those allow the row mutation). "
            f"Body:\n{body[:400]}"
        )


def test_delete_function_raises():
    """The function called by the DELETE trigger must RAISE EXCEPTION."""
    sql = _read_migration()
    func_pat = re.compile(
        r"CREATE\s+(OR\s+REPLACE\s+)?FUNCTION\s+(\S+)\s*\(\s*\).*?"
        r"LANGUAGE\s+plpgsql\s+AS\s+\$\$(.*?)\$\$",
        re.IGNORECASE | re.DOTALL,
    )
    funcs = {m.group(2): m.group(3) for m in func_pat.finditer(sql)}
    delete_funcs = {
        name: body
        for name, body in funcs.items()
        if "delete" in name.lower() and "l2_esc" in name.lower()
    }
    assert delete_funcs, (
        "No DELETE-rejector function found. Expected a plpgsql function "
        "named with `delete` + `l2_esc` (e.g., "
        "l2_escalations_missed_reject_delete)."
    )
    for name, body in delete_funcs.items():
        assert re.search(r"\bRAISE\s+EXCEPTION\b", body, re.IGNORECASE), (
            f"Function {name} must RAISE EXCEPTION on the DELETE path. "
            f"Body:\n{body[:400]}"
        )


def test_no_insert_trigger_softening():
    """Defensive — the table must NOT have a BEFORE INSERT trigger that
    raises (would block the backfill itself, defeating the migration)."""
    sql = _read_migration()
    pat = re.compile(
        r"CREATE\s+TRIGGER\s+\S+\s+BEFORE\s+INSERT\s+ON\s+l2_escalations_missed",
        re.IGNORECASE | re.DOTALL,
    )
    assert not pat.search(sql), (
        "BEFORE INSERT trigger on l2_escalations_missed is forbidden — "
        "the table is INSERT-ONLY for legitimate disclosure writes. "
        "Blocking inserts would defeat the purpose."
    )


def test_backfill_insert_present():
    """Sanity — the migration must INSERT historical rows (otherwise
    it's just a schema-create, not the disclosure-recording artifact)."""
    sql = _read_migration()
    pat = re.compile(
        r"INSERT\s+INTO\s+l2_escalations_missed\b",
        re.IGNORECASE,
    )
    assert pat.search(sql), (
        "Migration must INSERT INTO l2_escalations_missed (the backfill "
        "from incident_recurrence_velocity). Otherwise the disclosure "
        "table is empty and the auditor-kit JSON section is empty too."
    )
