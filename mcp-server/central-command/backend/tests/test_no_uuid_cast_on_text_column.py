"""CI gate: ban `$N::uuid` casts on TEXT-typed columns (Task #77 Phase A).

Today's 4h+ dashboard outage (commit 3ec431c8) was caused by
signature_auth.py:618 doing `WHERE appliance_id = $1::uuid` against
`site_appliances.appliance_id` which is `character varying` (TEXT),
not UUID. asyncpg threw `UndefinedFunctionError: operator does not
exist: character varying = uuid`.

Consistency-coach round-table (audit/coach-dashboard-sync-inconsistency-
2026-05-13.md) flagged: 126 `$N::uuid` casts in backend code. Only 1
was wrong, 125 verified correct by inspection — but unverified
structurally. Coach also recommended a phased gate:

  Phase A (this file) — regex stopgap over high-confidence wrong-cast
    patterns: `(appliance_id|site_id|host_id|hostname|ip_address|
    mac_address) = $N::uuid`. These 6 columns are TEXT/VARCHAR in
    100% of their schema occurrences (verified via mig 049/191/195
    + samples). Any `::uuid` cast against them is wrong-by-construction.

  Phase B (Task #77 Phase B, future) — full sqlparse AST walker over
    every `$N::TYPE` cast vs augmented prod_columns.json types. Covers
    the remaining 120 casts at full structural confidence.

The stopgap deliberately doesn't try to catch every case — its job is
to close the exact bug class that caused today's outage + the nearest
siblings.
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Columns that are 100% TEXT/VARCHAR in the prod schema (verified via
# information_schema.columns + mig 049/191/195 + samples). Any
# `::uuid` cast against these is wrong-by-construction.
_TEXT_COLUMNS = frozenset({
    "appliance_id",
    "site_id",
    "host_id",
    "hostname",
    "ip_address",
    "mac_address",
})

# Regex anchors on the load-bearing pattern: `WHERE col = $N::uuid`
# (case-insensitive) where col is one of the known TEXT columns.
# Handles table-qualified references (`d.appliance_id`, `sa.site_id`).
# Doesn't try to handle multi-line WHERE clauses — those are caught by
# Phase B's sqlparse walker.
_WRONG_CAST_RE = re.compile(
    r"\b(?:[a-z_][a-z_0-9]*\.)?(" + "|".join(_TEXT_COLUMNS) + r")\s*=\s*\$\d+::uuid",
    re.IGNORECASE,
)

# Files exempt from the gate — typically test fixtures or legacy
# migrations that mention the pattern as documentation.
_EXEMPT_FILES = frozenset({
    "test_no_uuid_cast_on_text_column.py",  # this file mentions the pattern
})


def _file_violations(path: pathlib.Path) -> list[tuple[int, str]]:
    """Return list of (line_no, matched_text) for every wrong-cast hit."""
    if path.name in _EXEMPT_FILES:
        return []
    try:
        text = path.read_text()
    except OSError:
        return []
    out: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        # Skip pure-comment lines
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        for match in _WRONG_CAST_RE.finditer(line):
            out.append((line_no, match.group(0)))
    return out


def test_text_columns_constant_is_locked():
    """Adding entries to _TEXT_COLUMNS requires a separate Gate A —
    we need to verify the new column is TEXT-typed in 100% of its
    schema occurrences. Pin at 6 today.
    """
    assert len(_TEXT_COLUMNS) == 6, (
        f"_TEXT_COLUMNS has {len(_TEXT_COLUMNS)} entries; expected 6. "
        f"Adding entries requires Gate A approval + schema verification. "
        f"Removing entries (e.g., schema migration changes a column to "
        f"UUID) requires updating this lock down."
    )


def test_no_uuid_cast_on_known_text_column():
    """Scan every backend `.py` for `$N::uuid` casts against known
    TEXT columns. Hard-fail any hit — this is the bug class that
    caused the 2026-05-13 4h+ dashboard outage (commit 3ec431c8).

    The stopgap pattern catches:
      - `WHERE appliance_id = $1::uuid` (the original bug)
      - `WHERE d.site_id = $2::uuid` (table-qualified)
      - Multi-arg WHERE clauses
      - Both DELETE and SELECT contexts

    It does NOT catch:
      - Multi-line WHERE col\\n = $N::uuid (rare; Phase B handles)
      - Compound expressions like `COALESCE(col, '') = $N::uuid`
      - Casts via CAST(col AS UUID) (different syntax; rare in practice)

    Phase B (Task #77 Phase B) will close the structural gap. Phase A
    is the high-confidence stopgap that would have caught today's bug
    at commit time.
    """
    all_violations: list[str] = []
    for py in _BACKEND.glob("*.py"):
        violations = _file_violations(py)
        for line_no, text in violations:
            all_violations.append(
                f"  {py.name}:{line_no}: `{text}` — "
                f"`::uuid` cast against TEXT column. Today's outage class. "
                f"Drop the cast (column is TEXT, value is passed as TEXT)."
            )
    assert not all_violations, (
        "Wrong-cast violations (Task #77 Phase A bug class):\n"
        + "\n".join(all_violations)
    )
