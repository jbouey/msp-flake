"""Regression gate — substrate `assertions.py` invariant CALLSITES must
construct `Violation` correctly + must not call raw `SAVEPOINT` SQL.

Round-2 audit (2026-05-09 `audit/coach-15-commit-adversarial-audit-
round2-2026-05-09.md` P0-RT2-A) caught an EXACT-class regression of
round-1's prometheus_metrics savepoint finding: 5 invariant callsites
shipped with `Violation(detail=...)` (singular `str`) when the
dataclass only accepts `details: Dict`. Plus one raw `SAVEPOINT`
SQL outside `conn.transaction()`. Both classes raised at every 60s
substrate tick — ~105 TypeError + ~102 NoActiveSQLTransactionError
per hour in prod logs, silent operator visibility loss.

This gate enforces both classes structurally via AST scan:

  (1) Every `Violation(...)` call MUST use `details=` (plural, dict)
      and NOT `detail=` (singular).

  (2) Every raw `SAVEPOINT` SQL string in `assertions.py` must be
      preceded (within the same function body) by `async with
      conn.transaction()`. Or — better — replaced with the context-
      manager form entirely.

The inline `prometheus_metrics_uses_savepoints` gate (round-1) only
covers `prometheus_metrics.py`. This gate covers `assertions.py`
specifically. The round-2 round-table queue item #2 calls for
`run_assertions_once()` against a real pool — that's a sprint
integration test; THIS gate is the static-source ratchet that
catches the same class without needing a Postgres backend.
"""
from __future__ import annotations

import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_TARGET = _BACKEND / "assertions.py"


def _broken_violation_callsites() -> list[str]:
    """Return file:line list of `Violation(...)` calls with `detail=`
    (singular) instead of `details=` (plural)."""
    src = _TARGET.read_text()
    tree = ast.parse(src)
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        # Match `Violation(...)` calls
        is_violation = False
        if isinstance(f, ast.Name) and f.id == "Violation":
            is_violation = True
        if not is_violation:
            continue
        for kw in node.keywords:
            if kw.arg == "detail":
                out.append(
                    f"assertions.py:{node.lineno} — "
                    f"Violation(detail=...) is wrong; the dataclass "
                    f"accepts `details: Dict`, not `detail: str`. "
                    f"Replace with details={{'interpretation': '...'}}"
                )
                break
    return out


def _raw_savepoint_callsites() -> list[str]:
    """Return file:line list of raw `SAVEPOINT` SQL string literals
    in assertions.py (excluding ones inside docstrings or comments).

    asyncpg requires SAVEPOINT to be inside an explicit transaction;
    invoking raw SQL `SAVEPOINT name` against `admin_connection` (which
    does NOT begin a top-level transaction unless wrapped in
    `admin_transaction`) raises NoActiveSQLTransactionError every tick.

    The correct shape is `async with conn.transaction():` — the asyncpg
    context-manager form which auto-detects savepoint vs top-level.
    """
    src = _TARGET.read_text()
    tree = ast.parse(src)
    out: list[str] = []
    # Walk every string constant; flag uppercase 'SAVEPOINT' + a name
    # token. Excluded: docstrings (handled by ast.get_docstring on
    # the function), comments (not in AST).
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        if not isinstance(node.value, str):
            continue
        s = node.value.strip().upper()
        if s.startswith("SAVEPOINT ") or s.startswith("ROLLBACK TO SAVEPOINT"):
            # Skip if this string is itself a docstring (parent is
            # an Expr that's the first stmt of a function/class).
            # Easiest heuristic: lineno > 50 AND not surrounded
            # by triple quotes (single-quoted SQL string indicator).
            # Using the AST col_offset is brittle; just allow-list
            # docstring text by checking the surrounding source line.
            line = src.splitlines()[node.lineno - 1] if node.lineno <= len(src.splitlines()) else ""
            if line.lstrip().startswith('"""') or line.lstrip().startswith("'''"):
                # Probably part of a docstring
                continue
            out.append(
                f"assertions.py:{node.lineno} — raw `SAVEPOINT` "
                f"SQL string: {node.value!r}. Replace with "
                f"`async with conn.transaction():` block. asyncpg "
                f"raises NoActiveSQLTransactionError when raw "
                f"SAVEPOINT runs against admin_connection without "
                f"an outer transaction."
            )
    return out


def test_no_broken_violation_callsites():
    """Round-2 P0-RT2-A: no Violation(detail=...) callsites. Baseline 0."""
    violations = _broken_violation_callsites()
    assert not violations, (
        "Substrate engine has Violation(detail=...) callsite(s). "
        "The dataclass accepts `details: Dict`, NOT `detail: str`. "
        "Each call raises TypeError at every 60s substrate tick.\n\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_no_raw_savepoint_sql():
    """Round-2 P0-RT2-A second class: no raw SAVEPOINT SQL strings.
    Use `async with conn.transaction():` instead. Baseline 0."""
    violations = _raw_savepoint_callsites()
    assert not violations, (
        "Substrate engine has raw SAVEPOINT SQL string literal(s). "
        "asyncpg requires SAVEPOINT inside an explicit transaction; "
        "use `async with conn.transaction():` instead.\n\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_synthetic_broken_violation_caught():
    """Positive control — synthetic Violation(detail=...) MUST be caught."""
    src = '''
@dataclass
class Violation:
    site_id: str
    details: Dict

def f():
    return Violation(site_id='x', detail='wrong shape')
'''
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "Violation"):
            continue
        for kw in node.keywords:
            if kw.arg == "detail":
                found = True
                break
    assert found


def test_synthetic_correct_violation_passes():
    """Negative control — Violation(details={...}) MUST NOT be caught."""
    src = '''
def f():
    return Violation(site_id='x', details={"interpretation": "ok"})
'''
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "Violation"):
            continue
        for kw in node.keywords:
            assert kw.arg != "detail", "negative control failed"
