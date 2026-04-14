"""
Session 206 invariant: no site-wide UPDATE on site_appliances / appliances.

Background: three independent SQL bugs propagated one appliance's state onto
every row at a site, because UPDATEs joined by site_id only (no appliance_id
filter). The dashboard showed 2 phantom appliances as online for 3+ days
before heartbeats exposed the lie.

This test statically greps the backend for the UPDATE pattern that caused
the bug. Defense in depth:

  * DB trigger (Migration 192) — runtime enforcement
  * This test                  — build-time enforcement
  * Per-host UPDATEs (e51884f, 6930292) — the actual fix

If you need a legitimate site-wide UPDATE (e.g. site transfer), explicitly
declare bulk intent by:
  1. Calling  await conn.execute("SET LOCAL app.allow_multi_row='true'")
     inside the same transaction, AND
  2. Adding the file path to ALLOWED_SITE_WIDE_PATHS below with a reason.

Do not add test bypasses to escape this test. The DB trigger will reject
the UPDATE at runtime anyway.
"""

from __future__ import annotations
import os
import re
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent.parent.parent  # mcp-server/

# Files that legitimately perform site-wide UPDATEs. Each entry must include
# the specific reason and the safety bypass mechanism. Adding an entry here
# requires the DB-level bypass to be set inside the transaction.
ALLOWED_SITE_WIDE_PATHS: dict[str, str] = {
    # routes.py site-transfer operation: intentionally moves all appliances
    # at a site to another site. Bulk by design. Must set LOCAL
    # app.allow_multi_row='true' inside the transaction.
    str(BACKEND_ROOT / "routes.py"): (
        "site-transfer — moves appliances from one site_id to another. "
        "Requires SET LOCAL app.allow_multi_row='true' inside the txn."
    ),
}

# Pattern: UPDATE (site_appliances|appliances) SET ... WHERE contains site_id
# but does NOT contain any of: appliance_id, host_id, mac_address, id =.
# Works across line breaks inside a multi-line SQL string literal.
UPDATE_STATEMENT_RE = re.compile(
    r"UPDATE\s+(site_appliances|appliances)\b\s+SET\b"
    r"(?P<body>[^;\"'`]*?)"
    r"WHERE\s+(?P<where>[^;\"'`]*?)"
    r"(?=(?:RETURNING|;|\"\"\"|'''|`{3}|\)\s*,\s*\{))",
    re.IGNORECASE | re.DOTALL,
)

# Per-row filters that are considered sufficient to scope an UPDATE to a
# single appliance. If the WHERE clause contains any of these, the statement
# passes the test.
PER_ROW_FILTERS = (
    "appliance_id",
    "host_id",
    "mac_address",
    "\nid =",
    " id =",
    "= :id",
    "= $1 AND",  # loose — many per-row UPDATEs use site_id=$1 AND id=$2
)


# Declaring bulk intent: caller must call `SET LOCAL app.allow_multi_row`
# inside the same transaction. If the test sees that string within this many
# lines BEFORE the UPDATE, the UPDATE is considered intentional bulk.
BULK_DECLARATION_LOOKBACK_LINES = 10
BULK_DECLARATION_MARKER = "app.allow_multi_row"


def _has_bulk_declaration_nearby(text: str, update_start_idx: int) -> bool:
    """Check if `app.allow_multi_row` appears in the ~N lines before the
    UPDATE statement. The DB trigger (Migration 192) requires the SET LOCAL
    flag to bypass the single-row guard; the test mirrors that requirement."""
    prefix = text[:update_start_idx]
    lines_before = "\n".join(
        prefix.splitlines()[-BULK_DECLARATION_LOOKBACK_LINES:]
    )
    return BULK_DECLARATION_MARKER in lines_before


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return (line_no, table, where_clause) tuples for suspicious UPDATEs."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[tuple[int, str, str]] = []
    for match in UPDATE_STATEMENT_RE.finditer(text):
        table = match.group(1).lower()
        where = match.group("where").lower()

        # Must reference site_id in the WHERE clause — otherwise it's
        # targeting something else (e.g., by primary key id alone).
        if "site_id" not in where:
            continue

        # If any per-row filter is present, it's scoped correctly.
        if any(f in where for f in PER_ROW_FILTERS):
            continue

        # Caller declared bulk intent via SET LOCAL app.allow_multi_row?
        if _has_bulk_declaration_nearby(text, match.start()):
            continue

        # Line number where the UPDATE starts
        line_no = text[: match.start()].count("\n") + 1
        findings.append((line_no, table, where.strip()[:120]))
    return findings


def _iter_py_files() -> list[Path]:
    """Walk the backend + main.py, skipping tests and caches."""
    files: list[Path] = []
    for root, dirs, fnames in os.walk(BACKEND_ROOT):
        # Skip test/cache/migration dirs
        dirs[:] = [
            d for d in dirs
            if d not in ("__pycache__", "tests", "migrations", "venv")
        ]
        for fn in fnames:
            if fn.endswith(".py"):
                files.append(Path(root) / fn)
    # Include main.py (lives at mcp-server/main.py)
    main_py = REPO_ROOT / "main.py"
    if main_py.exists():
        files.append(main_py)
    return files


def test_no_site_wide_update_without_per_row_filter():
    """Static grep: UPDATE site_appliances/appliances WHERE site_id=... must
    also filter by appliance_id / host_id / mac_address / id.

    Session 206 invariant. Three bugs of this shape shipped to production
    and made 2 phantom appliances look online for 3 days. Don't let a 4th
    slip through.
    """
    offenders: list[str] = []
    for path in _iter_py_files():
        path_str = str(path)
        findings = _scan_file(path)
        for line_no, table, where in findings:
            # Allow paths that are explicitly whitelisted (e.g., site transfer).
            if path_str in ALLOWED_SITE_WIDE_PATHS:
                continue
            offenders.append(
                f"{path_str}:{line_no} UPDATE {table} "
                f"WHERE {where!r} — no per-row filter"
            )

    if offenders:
        msg_lines = [
            "Found site-wide UPDATE statements that could propagate one "
            "appliance's state onto every row at the site:",
            "",
            *offenders,
            "",
            "Session 206 invariant. Add appliance_id/host_id/mac_address to",
            "the WHERE clause, OR add the file to ALLOWED_SITE_WIDE_PATHS if",
            "the bulk behavior is intentional AND the code sets",
            "LOCAL app.allow_multi_row='true' inside the transaction.",
        ]
        pytest.fail("\n".join(msg_lines))


def test_allowed_paths_exist():
    """Guard the whitelist itself: if an allowed path no longer exists
    (file renamed or deleted), drop it from the list."""
    for path_str in ALLOWED_SITE_WIDE_PATHS:
        p = Path(path_str)
        if not p.exists():
            pytest.fail(
                f"ALLOWED_SITE_WIDE_PATHS references missing file: {path_str}. "
                f"Remove it from the allowlist."
            )
