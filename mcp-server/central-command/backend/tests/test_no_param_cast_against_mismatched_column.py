"""CI gate: ban `col = $N::TYPE` casts where TYPE is a different type
family than the column's actual prod-schema type (Task #77 Phase B-lite,
2026-05-14).

Background — the 2026-05-13 4h+ dashboard outage (commit 3ec431c8) was
`signature_auth.py:618 WHERE appliance_id = $1::uuid` against a
`character varying` column. asyncpg threw `UndefinedFunctionError:
operator does not exist: character varying = uuid`.

Phase A (`test_no_uuid_cast_on_text_column.py`) is the high-confidence
stopgap: a hardcoded regex over 6 known-TEXT columns getting `::uuid`
casts. It stays as the never-regress floor — those 6 columns include
`appliance_id`/`site_id` which are MULTI-CLASS in the full schema
(`{uuid, text}`) and therefore CANNOT be resolved by this gate's
fixture-driven approach. Phase A's hard pin covers them.

Phase B-lite (this file) is the broad fixture-driven net: for every
`col = $N::TYPE` cast it resolves `col` against the typed prod-schema
fixture (`prod_column_types.json`) and flags any cast whose type family
differs from the column's stored type family.

Approach (no sqlparse — see audit/coach-cast-gate-phase-b-gate-a-2026-
05-14.md for why the full AST walker was deferred):
  - Regex-match `(?:qualifier.)?column = $N::type`.
  - Resolve `column` via a reverse map: column_name -> {type families
    across every table that has that column}.
  - If the column name resolves to EXACTLY ONE type family across the
    whole schema, and the cast type family differs -> violation.
  - If the column name maps to MULTIPLE conflicting families (e.g.
    `id` is uuid on some tables, integer on others) -> AMBIGUOUS, skip.
    This is the documented, honest scope reduction: without parsing the
    FROM clause we cannot know which table a bare column belongs to.
    Per Gate A sampling, multi-JOIN cast callsites are ~0% in this
    codebase, so the skip class is small.

Ratchet: BASELINE_MAX = 0. The codebase currently has ZERO mismatched
casts (verified at gate creation). Any NEW mismatched cast fails CI
immediately — this is a true zero-baseline ratchet, not a grind-down.
"""
from __future__ import annotations

import json
import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_TYPED_FIXTURE = (
    _BACKEND / "tests" / "fixtures" / "schema" / "prod_column_types.json"
)

# The codebase currently has zero mismatched casts. This is a ratchet
# floor — it must never go up without a fix. If a legitimate new cast
# trips a false positive, prefer adding a targeted skip below over
# raising this number.
BASELINE_MAX = 0

# Prod `information_schema.data_type` string  ->  type family.
_COLUMN_TYPE_FAMILY = {
    "uuid": "UUID",
    "text": "TEXT",
    "character varying": "TEXT",
    "character": "TEXT",
    "integer": "INT",
    "bigint": "INT",
    "smallint": "INT",
    "jsonb": "JSONB",
    "json": "JSONB",
    "timestamp with time zone": "TEMPORAL",
    "timestamp without time zone": "TEMPORAL",
    "date": "TEMPORAL",
    "inet": "INET",
    "bytea": "BYTEA",
    "boolean": "BOOL",
    "double precision": "FLOAT",
    "real": "FLOAT",
    "numeric": "FLOAT",
    # ARRAY and anything unmapped -> not classified -> skipped.
}

# Cast type token as written in SQL (`$N::<token>`)  ->  type family.
_CAST_TYPE_FAMILY = {
    "uuid": "UUID",
    "text": "TEXT",
    "varchar": "TEXT",
    "char": "TEXT",
    "bpchar": "TEXT",
    "int": "INT",
    "integer": "INT",
    "int4": "INT",
    "int2": "INT",
    "int8": "INT",
    "bigint": "INT",
    "smallint": "INT",
    "jsonb": "JSONB",
    "json": "JSONB",
    "timestamptz": "TEMPORAL",
    "timestamp": "TEMPORAL",
    "date": "TEMPORAL",
    "inet": "INET",
    "bytea": "BYTEA",
    "bool": "BOOL",
    "boolean": "BOOL",
    "float": "FLOAT",
    "float8": "FLOAT",
    "real": "FLOAT",
    "numeric": "FLOAT",
    "decimal": "FLOAT",
}

# `(?:qualifier.)?column = $N::type`
_CAST_RE = re.compile(
    r"\b(?:[a-z_][a-z_0-9]*\.)?([a-z_][a-z_0-9]+)\s*=\s*\$\d+::(\w+)",
    re.IGNORECASE,
)

# Files exempt from the gate (this file names the pattern as docs).
_EXEMPT_FILES = frozenset({
    "test_no_param_cast_against_mismatched_column.py",
    "test_no_uuid_cast_on_text_column.py",  # Phase A — same pattern in docs
})


def _column_family_map() -> dict[str, set[str]]:
    """column_name -> {type families across every table that has it}."""
    typed = json.loads(_TYPED_FIXTURE.read_text())
    rev: dict[str, set[str]] = {}
    for cols in typed.values():
        for col, dtype in cols.items():
            fam = _COLUMN_TYPE_FAMILY.get(dtype)
            if fam is None:
                # ARRAY / unmapped — record a sentinel so the column is
                # treated as multi-class (ambiguous) and skipped, rather
                # than silently resolving to whatever else it maps to.
                fam = "OTHER"
            rev.setdefault(col.lower(), set()).add(fam)
    return rev


def _file_violations(
    path: pathlib.Path, fam_map: dict[str, set[str]]
) -> list[tuple[int, str, str]]:
    """Return (line_no, matched_text, reason) for every mismatched cast."""
    if path.name in _EXEMPT_FILES:
        return []
    try:
        text = path.read_text()
    except OSError:
        return []
    out: list[tuple[int, str, str]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for match in _CAST_RE.finditer(line):
            col = match.group(1).lower()
            cast_token = match.group(2).lower()
            cast_fam = _CAST_TYPE_FAMILY.get(cast_token)
            if cast_fam is None:
                continue  # unknown cast token — don't guess
            col_families = fam_map.get(col)
            if not col_families:
                continue  # not a known prod column — nothing to check
            if len(col_families) != 1:
                continue  # ambiguous (multi-table conflict) — documented skip
            col_fam = next(iter(col_families))
            if col_fam == "OTHER":
                continue  # unclassified column type — skip
            if col_fam != cast_fam:
                out.append((
                    line_no,
                    match.group(0).strip(),
                    f"column `{col}` is {col_fam} in prod schema but "
                    f"cast as ::{cast_token} ({cast_fam})",
                ))
    return out


def test_typed_fixture_present():
    assert _TYPED_FIXTURE.is_file(), (
        f"missing typed schema fixture {_TYPED_FIXTURE} — regenerate via "
        f"the combined command in test_sql_columns_match_schema.py"
    )


def test_no_param_cast_against_mismatched_column():
    """Scan every backend `.py` for `col = $N::TYPE` casts whose type
    family differs from the column's prod-schema type family.

    True zero-baseline ratchet — the codebase has no mismatched casts
    today. A new one is the 2026-05-13 outage class and fails CI.
    """
    fam_map = _column_family_map()
    all_violations: list[str] = []
    for py in sorted(_BACKEND.glob("*.py")):
        for line_no, matched, reason in _file_violations(py, fam_map):
            all_violations.append(
                f"  {py.name}:{line_no}: `{matched}` — {reason}. "
                f"Drop or correct the cast (the 2026-05-13 dashboard "
                f"outage class)."
            )
    assert len(all_violations) <= BASELINE_MAX, (
        f"{len(all_violations)} mismatched-cast violation(s) > baseline "
        f"{BASELINE_MAX} (Task #77 Phase B-lite bug class):\n"
        + "\n".join(all_violations)
    )


def test_baseline_is_zero():
    """Pin the ratchet at 0. If this ever needs to go up, a real cast
    bug or a fixture-drift false positive has appeared — investigate,
    don't just bump."""
    assert BASELINE_MAX == 0, (
        "BASELINE_MAX must stay 0 — this gate is a true zero-baseline "
        "ratchet. Raising it means a mismatched cast was accepted."
    )
