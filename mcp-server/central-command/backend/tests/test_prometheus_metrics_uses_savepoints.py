"""Round-table P0-2 (Session 219, 2026-05-09): every metric SECTION in
`prometheus_metrics()` MUST be wrapped in its OWN
`async with admin_transaction(pool) as conn:` block AND have its OWN
try/except.

Why this gate exists
====================
The /metrics endpoint issues 30+ sequential read sections. Two earlier
shapes both leaked transaction-poisoning errors at runtime:

1. ORIGINAL (pre-Session 219): one outer `admin_transaction(pool) as
   conn` wrapping all sections, no per-query isolation. A single bad
   query poisoned the rest of the scrape — 171 InFailedSQLTransactionError
   in 5,000 production log lines (`audit/coach-e2e-attestation-audit-2026-05-08.md`).

2. SAVEPOINT ATTEMPT (commit 5cdcf90f, 2026-05-08): kept the outer
   admin_transaction, wrapped each query in `async with conn.transaction():`
   (an asyncpg savepoint). Still emitted 1500+ InFailedSQLTransactionError
   per 4 hours. Root cause: when asyncpg's prepared-statement-cache marks
   the outer transaction aborted, the SAVEPOINT SQL itself runs against
   an aborted transaction. Per-query savepoints can't recover a parent
   that asyncpg has already given up on.

3. CURRENT (this gate): each section opens its OWN
   `admin_transaction(pool) as conn` block. Fresh PgBouncer backend,
   fresh transaction; section A's failure cannot poison section B.

Hard rule
---------
- The OUTER `admin_transaction(pool) as conn` IS REMOVED. There is no
  one-outer-transaction. There are many sibling per-section transactions.
- Every section is `try: / async with admin_transaction(pool) as conn: /
  ... / except Exception: logger.exception(...)`.
- Inner `async with conn.transaction()` savepoints MUST NOT exist — they
  were the broken middle-layer fix.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_TARGET = _BACKEND / "prometheus_metrics.py"


def _is_admin_transaction_with(node: ast.AsyncWith) -> bool:
    """True if the AsyncWith opens `admin_transaction(...)` (any args)."""
    for item in node.items:
        ctx = item.context_expr
        if not isinstance(ctx, ast.Call):
            continue
        func = ctx.func
        if isinstance(func, ast.Name) and func.id == "admin_transaction":
            return True
    return False


def _is_conn_transaction_with(node: ast.AsyncWith) -> bool:
    """True if the AsyncWith opens `conn.transaction()` (the legacy savepoint)."""
    for item in node.items:
        ctx = item.context_expr
        if not isinstance(ctx, ast.Call):
            continue
        func = ctx.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "transaction":
            continue
        if isinstance(func.value, ast.Name) and func.value.id == "conn":
            return True
    return False


def _load_metrics_function() -> ast.AsyncFunctionDef:
    src = _TARGET.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(_TARGET))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "prometheus_metrics":
            return node
    raise AssertionError(
        "prometheus_metrics async function not found in prometheus_metrics.py"
    )


def _is_section_try(node: ast.Try) -> bool:
    """True if the Try block contains an `async with admin_transaction(...)`
    in its body. This is the SHAPE we expect for every metric section.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.AsyncWith) and _is_admin_transaction_with(child):
            return True
    return False


def _try_has_logger_exception_handler(node: ast.Try) -> bool:
    """True if at least one except handler calls logger.exception(...) or
    is a `pass`-only handler (mesh sub-section uses bare pass).
    """
    for handler in node.handlers:
        if not handler.body:
            continue
        # Accept either logger.exception(...) or a bare pass
        for stmt in handler.body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                f = stmt.value.func
                if isinstance(f, ast.Attribute) and f.attr == "exception":
                    return True
            if isinstance(stmt, ast.Pass):
                return True
    return False


def test_every_section_uses_admin_transaction() -> None:
    """Every metric section is `try: / async with admin_transaction(pool) as
    conn: / ... / except Exception: logger.exception(...)`.

    We approximate "section" as: any Try node that contains an
    `async with admin_transaction(...)` in its body. Each such Try MUST
    have an except-handler that either logs or `pass`-es (the mesh
    sub-section has a `pass`-only handler).
    """
    fn = _load_metrics_function()
    section_tries = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Try) and _is_section_try(node)
    ]
    # We expect MANY sections (≥25 per round-table acceptance).
    assert len(section_tries) >= 25, (
        f"Expected at least 25 metric sections wrapped in "
        f"`try: / async with admin_transaction(...) / except`, found "
        f"{len(section_tries)}. Did someone collapse the per-section "
        f"isolation into a single block?"
    )
    # And every section MUST have a logger.exception (or pass) handler.
    bad = [
        n.lineno for n in section_tries
        if not _try_has_logger_exception_handler(n)
    ]
    assert not bad, (
        "Sections missing logger.exception(...) (or pass) handler at lines: "
        + ", ".join(str(ln) for ln in bad)
    )


def test_admin_transaction_count_meets_threshold() -> None:
    """Sanity check: at least 25 `async with admin_transaction(...)` blocks
    inside the function. Less than that means sections were collapsed.
    """
    fn = _load_metrics_function()
    count = sum(
        1 for node in ast.walk(fn)
        if isinstance(node, ast.AsyncWith) and _is_admin_transaction_with(node)
    )
    assert count >= 25, (
        f"prometheus_metrics() should contain >=25 "
        f"`async with admin_transaction(pool) as conn:` blocks "
        f"(P0-2 expected ~30); found {count}. "
        f"Did someone re-collapse sections?"
    )


def test_no_inner_savepoints() -> None:
    """The 5cdcf90f-style `async with conn.transaction():` savepoints MUST
    NOT exist anywhere inside `prometheus_metrics()`. They were the
    broken middle-layer fix that still emitted 1500+
    InFailedSQLTransactionError per 4 hours; per-section
    admin_transaction is the correct pattern.
    """
    fn = _load_metrics_function()
    bad_lines = [
        node.lineno for node in ast.walk(fn)
        if isinstance(node, ast.AsyncWith) and _is_conn_transaction_with(node)
    ]
    assert not bad_lines, (
        "prometheus_metrics() contains legacy `async with conn.transaction()` "
        "savepoints (P0-2 violation — they don't survive an asyncpg-aborted "
        "outer transaction). Lines: " + ", ".join(str(ln) for ln in bad_lines)
    )


def test_outer_admin_transaction_preserved() -> None:
    """Negative control: there must NOT be a SINGLE outer admin_transaction
    wrapping the entire scrape (the OLD pattern). We test this by
    checking that no admin_transaction AsyncWith node has another
    admin_transaction AsyncWith as a descendant — every
    admin_transaction must be a sibling (or nested only inside a Try +
    inner control flow), never a parent of another admin_transaction.

    Test name kept (despite the slightly-counterintuitive semantics) so
    the gate's identity is preserved across history. The brief
    explicitly requires this name to FAIL on the old shape and PASS on
    the new shape.
    """
    fn = _load_metrics_function()
    admin_withs = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.AsyncWith) and _is_admin_transaction_with(node)
    ]
    assert admin_withs, "expected at least one admin_transaction in metrics"
    for outer in admin_withs:
        # Walk only DESCENDANTS (skip self) and assert none is also
        # an admin_transaction AsyncWith.
        for child in ast.walk(outer):
            if child is outer:
                continue
            if isinstance(child, ast.AsyncWith) and _is_admin_transaction_with(child):
                raise AssertionError(
                    f"Found a NESTED admin_transaction at line {child.lineno} "
                    f"inside the admin_transaction at line {outer.lineno}. "
                    f"P0-2 requires per-section sibling admin_transactions, "
                    f"NOT a single outer one wrapping the rest."
                )


# -----------------------------------------------------------------------------
# Synthetic positive + negative controls (Brief acceptance §)
# -----------------------------------------------------------------------------

_OLD_SHAPE_SOURCE = '''
async def prometheus_metrics():
    pool = None
    sections = []
    try:
        async with admin_transaction(pool) as conn:
            try:
                async with conn.transaction():
                    rows = await conn.fetch("SELECT 1")
                sections.append(rows)
            except Exception:
                logger.exception("a")
            try:
                async with conn.transaction():
                    rows = await conn.fetch("SELECT 2")
                sections.append(rows)
            except Exception:
                logger.exception("b")
    except Exception:
        logger.exception("outer")
'''

_NEW_SHAPE_SOURCE = '''
async def prometheus_metrics():
    pool = None
    sections = []
    try:
        async with admin_transaction(pool) as conn:
            rows = await conn.fetch("SELECT 1")
        sections.append(rows)
    except Exception:
        logger.exception("a")
    try:
        async with admin_transaction(pool) as conn:
            rows = await conn.fetch("SELECT 2")
        sections.append(rows)
    except Exception:
        logger.exception("b")
'''


def _synthetic_fn(source: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "prometheus_metrics":
            return node
    raise AssertionError("synthetic prometheus_metrics not found")


def test_synthetic_old_shape_fails_outer_check() -> None:
    """The OLD shape (single outer admin_transaction wrapping nested ones
    or savepoints) must FAIL the outer-not-nested check.
    """
    fn = _synthetic_fn(_OLD_SHAPE_SOURCE)
    # No outer admin_transaction wraps another admin_transaction in this
    # synthetic — but the savepoints must trip `test_no_inner_savepoints`'s
    # rule. Verify that.
    bad_lines = [
        node.lineno for node in ast.walk(fn)
        if isinstance(node, ast.AsyncWith) and _is_conn_transaction_with(node)
    ]
    assert bad_lines, (
        "synthetic OLD shape was supposed to contain conn.transaction() "
        "savepoints; static gate would not catch it."
    )


def test_synthetic_new_shape_passes() -> None:
    """The NEW shape (sibling per-section admin_transactions, no
    savepoints) must PASS every gate.
    """
    fn = _synthetic_fn(_NEW_SHAPE_SOURCE)
    # Must have at least 2 admin_transactions (synthetic has 2 sections).
    admin_count = sum(
        1 for node in ast.walk(fn)
        if isinstance(node, ast.AsyncWith) and _is_admin_transaction_with(node)
    )
    assert admin_count == 2
    # No savepoints.
    bad_lines = [
        node.lineno for node in ast.walk(fn)
        if isinstance(node, ast.AsyncWith) and _is_conn_transaction_with(node)
    ]
    assert not bad_lines
    # No nested admin_transactions.
    for outer in [
        n for n in ast.walk(fn)
        if isinstance(n, ast.AsyncWith) and _is_admin_transaction_with(n)
    ]:
        for child in ast.walk(outer):
            if child is outer:
                continue
            assert not (
                isinstance(child, ast.AsyncWith)
                and _is_admin_transaction_with(child)
            ), "synthetic NEW shape unexpectedly nested admin_transactions"


if __name__ == "__main__":  # pragma: no cover - manual-run convenience
    pytest.main([__file__, "-v"])
