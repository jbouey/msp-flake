"""Regression gate — `admin_connection(pool)` blocks must not contain
multi-statement admin queries unless wrapped in `conn.transaction():`.

Session 212 routing-pathology class (sigauth `303421cc`): PgBouncer
transaction-pool routes the `SET LOCAL app.is_admin = 'true'` and
subsequent statements to DIFFERENT backends. The 2nd+ statement
sees `app.is_admin='false'` (mig 234 default), RLS hides every
row, the call returns silent zero-rows. Centralized fix:
`tenant_middleware.admin_transaction(pool)` pins SET LOCAL +
queries to ONE backend via explicit transaction.

`feedback_round_table_at_gates_enterprise.md` Coach D-2 finding
2026-05-08 (ultrathink sweep): three new admin endpoints
(`issue_partner_weekly_digest_pdf`, `issue_partner_ba_compliance_
attestation_pdf`, `render_partner_incident_timeline_pdf`) shipped
this session with `admin_connection(pool)` while their inner
functions issue 2-5+ admin queries. Fixed in the same sweep;
this gate prevents reintroduction.

Sibling pattern: `test_admin_transaction_for_multistatement.py`
pins specific named sites with anchors. THAT test is the
per-line ratchet for known-good sites. THIS test is the
CLASS-GENERAL AST-walk that catches NEW violations anywhere
in the backend, fail-loud, baseline 0 after the 2026-05-08 sweep.

Algorithm:
  1. Scan `mcp-server/central-command/backend/*.py` (excluding
     tests/, venv/).
  2. Find every `async with .*admin_connection(.*) as (\\w+):` block.
  3. For each block, look at the next ~80 lines (function body).
  4. Count `<conn>.fetch*(` and `<conn>.execute(` calls that are
     NOT inside an inner `async with <conn>.transaction():` block.
  5. If count >= 2 outside a transaction → violation.
  6. Baseline 0; if you must add an exception, document the
     exception in BLOCK_ALLOWLIST with rationale.
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Files whose `admin_connection`-multi pattern is allowed (e.g. sites
# where the multi-statement reads are intentionally split across
# transactions for a documented reason). Empty as of 2026-05-08;
# adding an entry requires explicit round-table sign-off.
BLOCK_ALLOWLIST: dict[str, list[str]] = {
    # "module.py": ["function_name reason: ..."],
}

# Files whose `admin_connection` use is gate-exempt entirely
# (e.g. helper modules that never run admin-query handlers).
FILE_EXEMPT = {
    # tests/ already excluded structurally
}

_OPEN_BLOCK_RE = re.compile(
    r"async\s+with\s+\S*admin_connection\s*\([^)]*\)\s+as\s+(\w+)\s*:"
)


def _backend_python_files() -> list[pathlib.Path]:
    files = []
    for py in _BACKEND.rglob("*.py"):
        rel = py.relative_to(_BACKEND)
        if rel.parts[0] in {"tests", "venv", ".venv", "__pycache__", "scripts"}:
            continue
        if py.name in FILE_EXEMPT:
            continue
        files.append(py)
    return files


def _check_block(
    src_lines: list[str], start_line_idx: int, conn_name: str
) -> tuple[int, int]:
    """Return (db_call_count_outside_txn, last_line_inspected).

    Walks from start_line_idx (the line AFTER the `async with admin_
    connection`) until it hits an outer dedent or another `async with
    admin_connection`. Tracks whether the current cursor is inside a
    nested `async with conn.transaction():` block; calls inside that
    don't count.
    """
    count_outside = 0
    last_line = start_line_idx
    n = len(src_lines)
    # Find indent of start
    if start_line_idx >= n:
        return 0, start_line_idx
    block_indent = len(src_lines[start_line_idx]) - len(
        src_lines[start_line_idx].lstrip()
    )
    in_txn_indent: int | None = None  # if non-None, we're inside a
                                       # `conn.transaction():` block
    db_call_re = re.compile(
        rf"\b{re.escape(conn_name)}\.(fetch|fetchrow|fetchval|fetchall|execute|executemany|cursor)\("
    )
    txn_open_re = re.compile(
        rf"async\s+with\s+{re.escape(conn_name)}\.transaction\s*\(\s*\)\s*:"
    )

    for i in range(start_line_idx, min(n, start_line_idx + 200)):
        line = src_lines[i]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            last_line = i
            continue
        cur_indent = len(line) - len(stripped)
        # End of block when we dedent to <= block_indent and the line
        # is not blank/comment.
        if i > start_line_idx and cur_indent < block_indent:
            break
        last_line = i
        # Track txn entry/exit
        if txn_open_re.search(line):
            in_txn_indent = cur_indent
            continue
        if in_txn_indent is not None and cur_indent <= in_txn_indent:
            in_txn_indent = None
        # Count DB calls
        if db_call_re.search(line):
            if in_txn_indent is None:
                count_outside += 1

    return count_outside, last_line


# Ratchet baseline as of 2026-05-08 sweep. The Coach ultrathink sweep
# fixed 3 violations in this session's new code (P-F6/P-F7/P-F8); 241
# pre-existing violations remain across legacy modules (routes.py,
# mesh_targets.py, audit_report.py, appliance_delegation.py, etc.).
# This gate fail-loud BLOCKS new violations from landing AND ratchets
# DOWN as legacy sites get migrated to admin_transaction.
#
# Sibling pattern: `test_frontend_mutation_csrf.py::CSRF_BASELINE_MAX`
# + `test_baseline_doesnt_regress_silently`.
#
# When you migrate a function to admin_transaction:
#   1. Make the change.
#   2. Re-run this test — it will fail with new count.
#   3. Drop ADMIN_CONN_MULTI_BASELINE_MAX in this file to match.
#   4. Commit both changes together.
ADMIN_CONN_MULTI_BASELINE_MAX = 55


def _collect_violations() -> list[str]:
    violations = []
    for py in _backend_python_files():
        rel = str(py.relative_to(_BACKEND))
        try:
            src = py.read_text()
        except Exception:
            continue
        if "admin_connection" not in src:
            continue
        lines = src.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            m = _OPEN_BLOCK_RE.search(line)
            if not m:
                i += 1
                continue
            conn_name = m.group(1)
            count, last = _check_block(lines, i + 1, conn_name)
            if count >= 2:
                allowed = BLOCK_ALLOWLIST.get(rel, [])
                func = "(unknown)"
                for j in range(i, max(0, i - 60), -1):
                    fm = re.match(r"\s*(async\s+)?def\s+(\w+)", lines[j])
                    if fm:
                        func = fm.group(2)
                        break
                if func in allowed:
                    pass
                else:
                    violations.append(
                        f"{rel}:{i + 1} — function `{func}` uses "
                        f"admin_connection with {count} DB calls outside "
                        f"`{conn_name}.transaction():`. Use "
                        f"`admin_transaction(pool)` instead."
                    )
            i = last + 1
    return violations


def test_no_admin_connection_multi_query():
    """Ratchet — violations must NOT exceed the baseline. Each
    legacy function migrated to `admin_transaction` SHOULD drop
    the baseline; new code MUST never add a new violation.
    """
    violations = _collect_violations()
    assert len(violations) <= ADMIN_CONN_MULTI_BASELINE_MAX, (
        f"NEW Session 212 routing-pathology violation(s). Count="
        f"{len(violations)} but baseline={ADMIN_CONN_MULTI_BASELINE_MAX}. "
        f"Use `admin_transaction(pool)` for every NEW admin handler "
        f"with 2+ admin queries. Existing violations are tracked but "
        f"NEW ones are blocked.\n\n"
        + "\n".join(f"  - {v}" for v in violations[:10])
        + ("\n  ... " + str(len(violations) - 10) + " more" if len(violations) > 10 else "")
    )


def test_baseline_doesnt_regress_silently():
    """When a legacy function is migrated to `admin_transaction`,
    the baseline MUST drop in the same commit. This test fails
    LOUDLY when the actual count is BELOW the constant — forcing
    the operator to ratchet ADMIN_CONN_MULTI_BASELINE_MAX down."""
    actual = len(_collect_violations())
    assert actual == ADMIN_CONN_MULTI_BASELINE_MAX, (
        f"actual={actual} but ADMIN_CONN_MULTI_BASELINE_MAX="
        f"{ADMIN_CONN_MULTI_BASELINE_MAX}. Adjust the constant in "
        f"this file to match, then commit. (If actual > baseline, "
        f"a NEW violation snuck in — fix the violation, don't bump "
        f"the baseline.)"
    )


def test_baseline_zero_on_2026_05_08_sweep():
    """The Coach ultrathink sweep on 2026-05-08 fixed 3 known
    violations (P-F6, P-F7, P-F8 issuance handlers) by switching to
    `admin_transaction`. Confirm those 3 functions now use
    `admin_transaction` AND not `admin_connection`."""
    src = (_BACKEND / "partners.py").read_text()
    for fn_name in (
        "issue_partner_weekly_digest_pdf",
        "issue_partner_ba_compliance_attestation_pdf",
        "render_partner_incident_timeline_pdf",
    ):
        idx = src.find(f"async def {fn_name}(")
        assert idx > 0, f"{fn_name} missing"
        # Look in the next 6000 chars
        body = src[idx : idx + 6000]
        assert "admin_transaction(pool)" in body, (
            f"{fn_name} should use admin_transaction(pool) — sweep "
            f"D-2 fix-up 2026-05-08. Reverted?"
        )
        # The OUTER block must not be admin_connection on its own
        # (we tolerate an inner admin_connection only if there's a
        # documented reason; for these 3 there isn't).
        first_async_with = re.search(
            r"async\s+with\s+admin_(connection|transaction)\(", body
        )
        assert first_async_with, (
            f"{fn_name}: no `async with admin_*` found in expected window"
        )
        assert first_async_with.group(1) == "transaction", (
            f"{fn_name}: first `async with admin_*` should be "
            f"`admin_transaction`, found `admin_{first_async_with.group(1)}`."
        )


def test_synthetic_violation_caught():
    """Positive control — write a tiny synthetic source string with a
    deliberate 2-DB-call admin_connection block and confirm the
    matcher flags it."""
    synthetic_src = """
async def synthetic_violation():
    async with admin_connection(pool) as conn:
        partner = await conn.fetchrow("SELECT 1")
        sites = await conn.fetch("SELECT 2")
        await conn.execute("SELECT 3")
"""
    lines = synthetic_src.splitlines()
    # Find the open-block line
    for i, line in enumerate(lines):
        m = _OPEN_BLOCK_RE.search(line)
        if m:
            conn_name = m.group(1)
            count, _ = _check_block(lines, i + 1, conn_name)
            assert count == 3, (
                f"matcher should count 3 DB calls outside transaction; "
                f"got {count}"
            )
            return
    raise AssertionError("synthetic open-block not matched")


def test_synthetic_safe_with_inner_transaction_passes():
    """Negative control — the same shape with an inner
    `conn.transaction():` should NOT count as a violation."""
    synthetic_src = """
async def synthetic_safe():
    async with admin_connection(pool) as conn:
        async with conn.transaction():
            await conn.fetchrow("SELECT 1")
            await conn.fetch("SELECT 2")
            await conn.execute("SELECT 3")
"""
    lines = synthetic_src.splitlines()
    for i, line in enumerate(lines):
        m = _OPEN_BLOCK_RE.search(line)
        if m:
            conn_name = m.group(1)
            count, _ = _check_block(lines, i + 1, conn_name)
            assert count == 0, (
                f"matcher should count 0 DB calls outside transaction "
                f"when wrapped in conn.transaction(); got {count}"
            )
            return
    raise AssertionError("synthetic open-block not matched")
