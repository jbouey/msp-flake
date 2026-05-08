"""Round-table RT-1.3 (Session 219, 2026-05-08): every `await conn.<method>(...)`
inside the `prometheus_metrics()` body MUST be enclosed in an
`async with conn.transaction():` ancestor (a savepoint).

Why this gate exists
====================
The /metrics endpoint issues 48 sequential reads inside a single
`admin_transaction(pool)` block. asyncpg + Postgres semantics:
when ONE query in an open transaction errors, the WHOLE
transaction is poisoned — every subsequent fetch returns
`InFailedSQLTransactionError` and the metric reports 0 silently.

The audit (`audit/coach-e2e-attestation-audit-2026-05-08.md`
F-P0-3) found 171 `InFailedSQLTransactionError` lines in 5,000
production log lines, all from `prometheus_metrics.py`.

Per-query savepoints (`async with conn.transaction():` inside the
outer admin_transaction) isolate failures: one section's error no
longer poisons the rest of the scrape. This is the same pattern
applied across `sites.py` checkin handler in Session 200.

Hard rule
---------
- Outer `admin_transaction(pool) as conn` STAYS — that pins SET
  LOCAL + reads to one PgBouncer backend.
- INSIDE that block, every `await conn.<fetch|fetchval|fetchrow|execute>(...)`
  call site must have an `async with conn.transaction():` ancestor.

Multiple awaits MAY share one savepoint (logical unit) — this test
just requires SOME savepoint ancestor exists; it doesn't enforce
"one per await".
"""
from __future__ import annotations

import ast
import pathlib

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_TARGET = _BACKEND / "prometheus_metrics.py"

# asyncpg connection methods we care about. `conn.transaction()` itself is
# the wrapper, not a query, so it's excluded.
_QUERY_METHODS = {"fetch", "fetchval", "fetchrow", "execute", "executemany"}


def _is_conn_query_call(call: ast.Call) -> bool:
    """True if call is `conn.<query_method>(...)`."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in _QUERY_METHODS:
        return False
    # Receiver must be a Name == "conn" (we don't care about other names).
    return isinstance(func.value, ast.Name) and func.value.id == "conn"


def _is_conn_transaction_with(node: ast.AsyncWith) -> bool:
    """True if the AsyncWith opens `conn.transaction()` (the savepoint)."""
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


def _collect_violations(tree: ast.Module) -> list[tuple[int, str]]:
    """Walk the AST and return (lineno, snippet) for every `await conn.<query>(...)`
    that does NOT have a `conn.transaction()` AsyncWith ancestor.

    The walk is parent-aware: we descend the tree recording the stack of
    enclosing nodes; whenever we hit an Await whose value is a Call that
    matches `_is_conn_query_call`, we check if any AsyncWith ancestor in
    the stack is a `conn.transaction()` block.
    """
    violations: list[tuple[int, str]] = []

    def visit(node: ast.AST, savepoint_depth: int) -> None:
        # If this node IS a conn.transaction() AsyncWith, bump the depth
        # for everything inside its body.
        is_savepoint = (
            isinstance(node, ast.AsyncWith) and _is_conn_transaction_with(node)
        )

        # Check the violation at this node BEFORE recursing.
        if isinstance(node, ast.Await):
            value = node.value
            if isinstance(value, ast.Call) and _is_conn_query_call(value):
                if savepoint_depth == 0:
                    method = value.func.attr  # type: ignore[attr-defined]
                    violations.append(
                        (node.lineno, f"await conn.{method}(...)")
                    )

        # Recurse into children with the (possibly bumped) depth.
        new_depth = savepoint_depth + (1 if is_savepoint else 0)
        for child in ast.iter_child_nodes(node):
            visit(child, new_depth)

    visit(tree, 0)
    return violations


def _load_metrics_function() -> ast.AsyncFunctionDef:
    """Return the AST node for the `prometheus_metrics` async function."""
    src = _TARGET.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(_TARGET))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "prometheus_metrics":
            return node
    raise AssertionError(
        "prometheus_metrics async function not found in prometheus_metrics.py"
    )


def test_every_conn_query_is_wrapped_in_savepoint() -> None:
    """Every await conn.<query_method>(...) inside prometheus_metrics() must
    have an `async with conn.transaction():` ancestor.

    Baseline: 0 violations after RT-1.3 sweep (Session 219).
    """
    fn = _load_metrics_function()
    violations = _collect_violations(fn)
    assert violations == [], (
        "prometheus_metrics.py has unsavepointed conn queries "
        "(RT-1.3 violation — first error poisons the rest of the scrape):\n"
        + "\n".join(f"  line {ln}: {snippet}" for ln, snippet in violations)
    )


def test_savepoint_count_meets_threshold() -> None:
    """Sanity check that the function actually contains savepoints.

    A change that accidentally drops all `async with conn.transaction()`
    blocks would trivially pass `test_every_conn_query_is_wrapped_in_savepoint`
    (0 violations because all bare awaits would still be 0). This guards
    that pathological case.
    """
    fn = _load_metrics_function()
    count = 0
    for node in ast.walk(fn):
        if isinstance(node, ast.AsyncWith) and _is_conn_transaction_with(node):
            count += 1
    assert count >= 20, (
        f"prometheus_metrics() should contain >=20 `async with conn.transaction()` "
        f"savepoints (RT-1.3 expected ~30); found {count}. "
        f"Did someone drop the savepoint sweep?"
    )


def test_outer_admin_transaction_preserved() -> None:
    """The outer `admin_transaction(pool) as conn` MUST stay — that's the
    PgBouncer-pinning helper. Per-query savepoints are NESTED inside it,
    not a replacement.
    """
    fn = _load_metrics_function()
    found_admin_transaction = False
    for node in ast.walk(fn):
        if not isinstance(node, ast.AsyncWith):
            continue
        for item in node.items:
            ctx = item.context_expr
            if isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Name):
                if ctx.func.id == "admin_transaction":
                    found_admin_transaction = True
                    break
    assert found_admin_transaction, (
        "Outer `admin_transaction(pool) as conn` is missing from "
        "prometheus_metrics(). RT-1.3 must NOT remove it — savepoints "
        "are nested inside, not a replacement."
    )


if __name__ == "__main__":  # pragma: no cover - manual-run convenience
    pytest.main([__file__, "-v"])
