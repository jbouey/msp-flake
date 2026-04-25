"""Source-level guard: every backend INSERT INTO admin_audit_log uses
the canonical column set + emits a well-formed `target` string.

Session 210-B 2026-04-25 audit. Today shipped 5 endpoints/jobs with
INSERTs against admin_audit_log that referenced columns that don't
exist in the schema (`actor`, `target_type`, `target_id`):

  * sites.py:relocate — `actor` (real column: `username`)
  * provisioning.py:admin_restore — `actor`
  * startup_invariants.py:334 — `target_type` + `target_id` (real: `target`)
  * chain_tamper_detector.py:215 — `target_type` + `target_id`
  * retention_verifier.py:241 — `target_type` + `target_id`

The cumulative-static-linter (`test_sql_columns_match_schema.py`) is
the durable catch for this class. THIS test is a tighter belt-and-
suspenders that also enforces the agreed `target` string format
(`<type>:<id>`) so the audit trail stays parseable downstream.

Adding a NEW INSERT INTO admin_audit_log without the canonical column
set fails this test. Adding a `target` value that doesn't follow
`<type>:<id>` is flagged as a soft warning.
"""
from __future__ import annotations

import pathlib
import re
from typing import List, Tuple

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "mcp-server" / "central-command" / "backend"


# Allowed column names for admin_audit_log INSERTs. Matches prod schema:
#   id (auto), user_id, username, action, target, details, ip_address, created_at
ALLOWED_COLUMNS = {
    "user_id", "username", "action", "target", "details",
    "ip_address", "created_at",
}

# Banned column names that have already bitten us. Adding any of these
# back is the regression we're guarding against.
BANNED_COLUMNS = {"actor", "target_type", "target_id"}


def _backend_py_files() -> List[pathlib.Path]:
    out: List[pathlib.Path] = []
    for p in BACKEND_DIR.rglob("*.py"):
        if any(skip in p.parts for skip in (
            "tests", "archived", "venv", "__pycache__", "node_modules",
        )):
            continue
        out.append(p)
    return out


_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+admin_audit_log\s*\(\s*([\w\s,\n]+?)\s*\)",
    re.IGNORECASE | re.DOTALL,
)


def _scan_audit_log_inserts(src: str) -> List[Tuple[set, int]]:
    out = []
    for match in _INSERT_RE.finditer(src):
        col_blob = match.group(1)
        cols = {c.strip().lower() for c in col_blob.split(",") if c.strip()}
        cols = {c for c in cols if re.match(r"^[a-z_][\w]*$", c)}
        lineno = src[: match.start()].count("\n") + 1
        out.append((cols, lineno))
    return out


def test_no_banned_columns_in_admin_audit_log_inserts():
    """No INSERT INTO admin_audit_log may reference `actor`,
    `target_type`, or `target_id` — those columns DON'T EXIST and
    every such INSERT fails on first call. This is the regression
    guard for the 5 bugs Session 210-B audit caught."""
    failures: List[str] = []
    for py_path in _backend_py_files():
        try:
            src = py_path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = py_path.relative_to(REPO_ROOT)
        for cols, lineno in _scan_audit_log_inserts(src):
            banned = cols & BANNED_COLUMNS
            if banned:
                failures.append(
                    f"{rel}:{lineno}: INSERT INTO admin_audit_log uses "
                    f"banned column(s) {sorted(banned)} — these columns "
                    "DON'T EXIST in the schema. Use `username` instead "
                    "of `actor`; collapse `target_type`+`target_id` into "
                    "a single `target` string with `<type>:<id>` shape."
                )
    assert not failures, "\n".join(f"  - {f}" for f in failures)


def test_admin_audit_log_inserts_use_only_real_columns():
    """Every column name in an INSERT INTO admin_audit_log must be in
    ALLOWED_COLUMNS. Catches new typos (e.g. `usernme`) before they
    reach prod."""
    failures: List[str] = []
    for py_path in _backend_py_files():
        try:
            src = py_path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = py_path.relative_to(REPO_ROOT)
        for cols, lineno in _scan_audit_log_inserts(src):
            unknown = cols - ALLOWED_COLUMNS
            if unknown:
                failures.append(
                    f"{rel}:{lineno}: INSERT INTO admin_audit_log uses "
                    f"unknown column(s) {sorted(unknown)} — schema only "
                    f"has {sorted(ALLOWED_COLUMNS)}"
                )
    assert not failures, "\n".join(f"  - {f}" for f in failures)


def test_target_string_uses_typed_prefix_pattern():
    """The agreed `target` format is `<type>:<id>` (e.g.
    `appliance:84:3A:5B:91:B6:61`, `site:north-valley-branch-2`,
    `invariant:relocation_stalled`). Bare strings without a typed
    prefix are noisy in the audit trail.

    This is a soft warning — bare `target=$1` (where $1 is a
    parameter) is fine because the value is computed at runtime. We
    only flag the case where the LITERAL passed to `target` is a
    bare value with no colon.
    """
    # Pattern: $N param at target position is fine. We're looking for
    # f-string or literal patterns where the target value lacks a
    # colon. Heuristic — won't be perfect but catches the obvious.
    suspicious: List[str] = []
    target_arg_re = re.compile(
        r'target\s*=\s*(?:f")?([^"\n]+)"',
        re.IGNORECASE,
    )
    for py_path in _backend_py_files():
        try:
            src = py_path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = py_path.relative_to(REPO_ROOT)
        # Only check files that have an admin_audit_log INSERT.
        if "INSERT INTO admin_audit_log" not in src:
            continue
        for match in target_arg_re.finditer(src):
            val = match.group(1).strip()
            # Skip param placeholders, dict accesses, function calls
            if val.startswith("$") or val.startswith("{") or "(" in val:
                continue
            # Skip if it has a colon (looks like <type>:<id>)
            if ":" in val:
                continue
            # Skip empty
            if not val:
                continue
            lineno = src[: match.start()].count("\n") + 1
            suspicious.append(f"{rel}:{lineno}: target={val!r} (no <type>:<id>)")

    # Soft warning, not a hard fail — preserve operator latitude. If
    # the count grows materially, flip to assert.
    if suspicious:
        # Don't fail; just print so the audit is visible.
        print("\nadmin_audit_log target-string format warnings:")
        for line in suspicious:
            print(f"  - {line}")
