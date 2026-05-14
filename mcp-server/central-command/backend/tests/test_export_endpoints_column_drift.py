"""CI gate: ban schema-vs-code drift in admin export endpoints (Task #76).

Pre-fix at routes.py:6442 + routes.py:8638, two admin endpoints
referenced columns that DO NOT EXIST in discovered_devices schema:
  - routes.py:6442 — `vendor` (no such column), `first_seen`/`last_seen`
    (columns are `first_seen_at`/`last_seen_at`)
  - routes.py:8638 — `os_type` (column is `os_name`), `last_seen`

Both endpoints (admin-only `/sites/{id}/export` + `/admin/sites/{id}/
compliance-packet`) would 500 on first call. Admin-only never exercised
so silent latent bug for months. Gate A v2 P0 (Maya): §164.524
timeliness risk on /compliance-packet.

Post-fix: SQL aliases (`first_seen_at AS first_seen`, `os_name AS
os_type`, etc.) preserve dict(row) serializer keys while reading the
correct columns.

This gate prevents regression: any backend `.py` file that SELECTs
known-wrong column names from `discovered_devices` fails CI.
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Columns that do NOT exist in discovered_devices schema today.
# Verified against migrations + production information_schema 2026-05-13.
# Any SELECT/ORDER BY referencing these is a latent bug.
_KNOWN_NONEXISTENT = frozenset({
    "vendor",       # was in routes.py:6442 pre-fix
    "first_seen",   # actual column: first_seen_at
    "last_seen",    # actual column: last_seen_at
    "os_type",      # actual column: os_name
})

# Pattern: column name preceded by SELECT/comma/whitespace and followed by
# `,` or whitespace-then-FROM. Excludes alias-target patterns (`AS X`).
_BARE_COL_RE = re.compile(
    r"(?<![A-Za-z_.])(" + "|".join(_KNOWN_NONEXISTENT) + r")(?:\s*,|\s+(?=FROM|ORDER|\Z|$))",
    re.IGNORECASE,
)

# Exempt: this file (mentions the names) + tests-fixture files.
_EXEMPT_FILES = frozenset({
    "test_export_endpoints_column_drift.py",
})


def _file_violations(path: pathlib.Path) -> list[tuple[int, str]]:
    if path.name in _EXEMPT_FILES:
        return []
    try:
        text = path.read_text()
    except OSError:
        return []
    out: list[tuple[int, str]] = []
    in_discovered_devices_query = False
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Track query context — only count violations within a query
        # that touches discovered_devices.
        if "discovered_devices" in line.lower():
            in_discovered_devices_query = True
        if not in_discovered_devices_query:
            continue
        # Look for bare wrong-column refs. Skip lines with `AS <col>`
        # (alias targets — that's the post-fix pattern).
        # Also skip lines containing `_at AS` (the post-fix alias).
        if " AS " in line.upper() and any(
            f"{wrong}" in line.lower() for wrong in ("as first_seen", "as last_seen", "as os_type")
        ):
            # The post-fix alias pattern — column AS alias-with-wrong-name
            # is fine because the SELECT pulls the real column.
            continue
        for match in _BARE_COL_RE.finditer(line):
            col = match.group(1)
            # Filter: only flag bare references; the alias target is OK
            # (e.g., "first_seen_at AS first_seen" is fine — the LHS is
            # the real column).
            before = line[:match.start()].rstrip().upper()
            if before.endswith("AS"):
                continue
            out.append((line_no, col))
        # End query when we hit a triple-quote-close OR an empty line
        # at module level.
        if '"""' in line or "'''" in line:
            in_discovered_devices_query = False
    return out


def test_no_nonexistent_columns_referenced_in_discovered_devices_queries():
    """Scan every backend `.py` for SELECT references to columns that
    don't exist in discovered_devices schema. Hard-fail any hit.

    Fix shape: alias real columns to the desired key in the result
    (e.g., `first_seen_at AS first_seen` reads the correct column +
    produces the expected key in dict(row)).
    """
    all_violations: list[str] = []
    for py in _BACKEND.glob("*.py"):
        for line_no, col in _file_violations(py):
            all_violations.append(
                f"  {py.name}:{line_no}: `{col}` — column does NOT exist "
                f"in discovered_devices schema. Either drop it or alias "
                f"the real column (`<real_col> AS {col}`)."
            )
    assert not all_violations, (
        "Schema-vs-code drift in discovered_devices queries:\n"
        + "\n".join(all_violations)
    )
