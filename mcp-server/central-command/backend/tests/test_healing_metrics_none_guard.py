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


def test_no_bare_round_on_potentially_none_metric_in_db_queries():
    """AST walk: any `round(NAME, ...)` in db_queries.py where NAME
    is one of the known-nullable rate variables MUST be guarded by a
    conditional. Catches the class structurally.
    """
    src = (_BACKEND / "db_queries.py").read_text()
    tree = ast.parse(src)
    NULLABLE_RATES = {"healing_rate", "order_rate"}
    bare_rounds: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "round"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Name):
            continue
        rate_name = node.args[0].id
        if rate_name not in NULLABLE_RATES:
            continue
        # Is this round() inside an IfExp guard?
        # ast.walk yields the call regardless of context — re-check
        # via parent traversal would require a parent map. Cheaper:
        # check the source line contains `is not None`.
        line_no = node.lineno
        line = src.splitlines()[line_no - 1]
        if "is not None" not in line:
            bare_rounds.append(
                f"db_queries.py:{line_no} — round({rate_name}, ...) "
                f"without `is not None` guard: {line.strip()}"
            )
    assert not bare_rounds, (
        "Unguarded round() on nullable rate variable. Add `if NAME is "
        "not None else 0.0` to prevent the production-fleet-500 class.\n"
        + "\n".join(bare_rounds)
    )
