"""CI gate — ban the `admin_connection(pool) as conn, conn.transaction()`
anti-pattern across backend Python.

#138 sweep closure. Per tenant_middleware.py:147-157 routing-risk
caveat: PgBouncer transaction-pool mode can route the SET LOCAL
app.is_admin='true' and a subsequent fetch to DIFFERENT backends.
The fetch then runs without app.is_admin='true' (mig 234's role
default is false) and RLS hides every row. Symptom: silent
zero-row results in production, unreproducible in dev.

Multi-statement admin paths MUST use `admin_transaction(pool)` —
pins SET LOCAL + the full critical section to ONE PgBouncer
backend in ONE explicit txn.

`admin_connection` is still valid for SINGLE-statement reads
(where SET LOCAL + the read share one pgbouncer transaction).
The anti-pattern is specifically the inline `, conn.transaction()`
addition on the same `async with` line.

Ratchet baseline = 0 (true after #137 + #138 sweep). Any new
regression fails CI at PR-build time.
"""
from __future__ import annotations

import pathlib
import re


_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Anti-pattern shape: `async with admin_connection(...) as <var>, <var>.transaction():`
# Match flexibly — variable name + indent may vary.
_ANTI_PATTERN = re.compile(
    r"async\s+with\s+admin_connection\s*\([^)]*\)\s+as\s+\w+\s*,\s*\w+\.transaction\(\)",
)


def _scan_py_files() -> list[pathlib.Path]:
    """All backend .py files (excluding venvs, __pycache__, migrations,
    tests — tests may construct fixtures with the pattern legitimately)."""
    files = []
    for p in _BACKEND.rglob("*.py"):
        s = str(p)
        if "/venv/" in s or "/__pycache__/" in s or "/migrations/" in s:
            continue
        if "/tests/" in s:
            # Tests scan/grep the pattern (this file itself contains
            # the literal in its docstring + regex). Skip the tests
            # subtree.
            continue
        files.append(p)
    return files


def test_no_admin_connection_with_inline_transaction():
    """Ratchet baseline = 0. The anti-pattern is documented at
    tenant_middleware.py:147-157. Use admin_transaction(pool)
    instead for any multi-statement admin path."""
    offenders: list[tuple[str, int, str]] = []
    for path in _scan_py_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in _ANTI_PATTERN.finditer(text):
            # Find line number for the match start
            line_no = text[: m.start()].count("\n") + 1
            rel = path.relative_to(_BACKEND)
            offenders.append((str(rel), line_no, m.group(0)[:80]))
    assert not offenders, (
        f"Found {len(offenders)} usages of the routing-risk anti-"
        f"pattern `admin_connection(pool) as conn, conn.transaction()`. "
        f"Per tenant_middleware.py:147-157: PgBouncer transaction-"
        f"pool mode can route SET LOCAL + subsequent statements to "
        f"different backends. Use `admin_transaction(pool)` for any "
        f"multi-statement admin path. `admin_connection` alone is "
        f"fine for SINGLE-statement reads.\n\n"
        f"Offenders:\n"
        + "\n".join(
            f"  {f}:{line}: {pattern}"
            for f, line, pattern in offenders
        )
    )


def test_admin_transaction_helper_still_exists():
    """Sanity: the canonical helper this gate steers callers towards
    must remain importable + exported."""
    tm = (_BACKEND / "tenant_middleware.py").read_text()
    assert "async def admin_transaction(" in tm or \
           "def admin_transaction(" in tm, (
        "admin_transaction helper missing from tenant_middleware.py — "
        "this CI gate's recommendation can't be followed without it. "
        "Restore the helper OR redesign the gate."
    )


def test_pattern_warning_documented_in_tenant_middleware():
    """The anti-pattern's rationale lives at tenant_middleware.py:
    147-157. If that comment block is removed, future readers won't
    know WHY this gate exists."""
    tm = (_BACKEND / "tenant_middleware.py").read_text()
    assert "ROUTING-RISK CAVEAT" in tm, (
        "tenant_middleware.py must keep the ROUTING-RISK CAVEAT "
        "comment block explaining why admin_transaction is required "
        "for multi-statement paths."
    )
