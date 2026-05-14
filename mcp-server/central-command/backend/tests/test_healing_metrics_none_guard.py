"""Regression test for healing_metrics None-guard (Production fix 2026-05-13).

PRE-FIX: db_queries.py:1942 had `round(healing_rate, 1)` where
healing_rate is None for any site with total_incidents=0 (line 1935
sets the None). round(None, 1) raises TypeError → entire
/api/dashboard/fleet endpoint 500'd → frontend showed "0 sites
connected" sidebar widget even though sites existed in DB.

Every healthy site with zero incidents triggered the failure. Net
effect: 100% outage of the fleet-overview surface for any account
with no active incidents (i.e., the steady-state happy path).

POST-FIX: `round(healing_rate, 1) if healing_rate is not None else 0.0`
matches the sibling `order_rate` guard one line below.

This test pins the None-handling so a future PR can't regress.
"""
from __future__ import annotations

import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent


def test_get_all_healing_metrics_guards_none_healing_rate():
    """The `healing_success_rate` and `order_execution_rate` fields in
    `get_all_healing_metrics()` MUST guard against None. Without the
    guard, every fleet-overview request 500s for any site that has
    zero incidents — a 100% steady-state outage class.
    """
    src = (_BACKEND / "db_queries.py").read_text()
    # The literal post-fix shape:
    assert (
        "round(healing_rate, 1) if healing_rate is not None else 0.0"
        in src
    ), (
        "db_queries.py get_all_healing_metrics MUST guard "
        "round(healing_rate, 1) against None. Sibling order_rate "
        "guard sets the precedent. Pre-fix this caused 100% outage "
        "of /api/dashboard/fleet for steady-state (zero-incident) "
        "deployments."
    )
    # Companion sibling — verify the order_rate guard stayed intact:
    assert (
        "round(order_rate, 1) if order_rate is not None else 0.0"
        in src
    ), "order_execution_rate None-guard regressed."


def _nullable_names_in_function(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Walk a function's body for assignments of shape `NAME = (... if ... else None)`.

    Returns the set of locally-inferred-nullable variable names. Per-function
    scope is load-bearing per Task #71 Gate A v1 P0 #1 — module-wide inference
    produced false positives at routes.py:4875 where `compliance_score` was
    reassigned via `... else 0` (not None) within the same function.
    """
    nullable: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.IfExp):
            continue
        # Check the orelse is the literal `None`
        orelse = node.value.orelse
        if not (isinstance(orelse, ast.Constant) and orelse.value is None):
            continue
        # Collect target names (handle simple Name targets; skip
        # tuple-unpacking edge cases — could land in nullable bucket
        # if needed but not common in db_queries.py)
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                nullable.add(tgt.id)
    return nullable


def test_no_bare_round_on_potentially_none_metric_in_db_queries():
    """AST walk: any `round(NAME, ...)` in db_queries.py where NAME was
    locally inferred as nullable (assigned via `(... if ... else None)`)
    MUST be guarded by `is not None`. Per-function scope.

    Per Task #71 Gate A v1 APPROVE-WITH-FIXES (audit/coach-ast-gate-broaden
    -gate-a-2026-05-13.md):
      - Per-function inference (NOT module-wide — was causing false
        positives at routes.py:4875 in early fork)
      - Scoped to db_queries.py only initially (where the 2026-05-13
        prod outage class hit); expand on driving incident
      - Existing literal-string pin (test above) kept as defense-in-depth

    The inference replaces the prior hardcoded NULLABLE_RATES = {
    "healing_rate", "order_rate"} set. New nullable-rate variables in
    future db_queries.py functions are auto-detected.
    """
    src = (_BACKEND / "db_queries.py").read_text()
    tree = ast.parse(src)
    src_lines = src.splitlines()
    bare_rounds: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nullable = _nullable_names_in_function(fn)
        if not nullable:
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "round"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Name):
                continue
            rate_name = node.args[0].id
            if rate_name not in nullable:
                continue
            # Is this round() on a line containing `is not None`?
            line = src_lines[node.lineno - 1]
            if "is not None" not in line:
                bare_rounds.append(
                    f"db_queries.py:{node.lineno} in fn `{fn.name}` — "
                    f"round({rate_name}, ...) without `is not None` guard: "
                    f"{line.strip()}"
                )
    assert not bare_rounds, (
        "Unguarded round() on locally-nullable variable in db_queries.py. "
        "Add `if NAME is not None else 0.0` to prevent the "
        "production-fleet-500 class.\n"
        + "\n".join(bare_rounds)
    )
