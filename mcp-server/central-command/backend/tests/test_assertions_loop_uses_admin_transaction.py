"""Pin gate — Substrate Integrity Engine `assertions_loop` MUST use
`admin_transaction(pool)` for per-assertion isolation AND for the
independent `_ttl_sweep` path.

Gate A P0-4 + P1-4 (audit/coach-substrate-per-assertion-refactor-
gate-a-2026-05-11.md): the prior design held one outer
`admin_connection` for the whole tick, so a single asyncpg
`InterfaceError` from one of 60+ assertions poisoned the conn
and (worse) the `if errors == 0` short-circuit silently dropped
the TTL sweep on any tick that hit a transient error — letting
`sigauth_observations` grow unboundedly.

What this gate pins (static AST + source walk):
  1. `assertions_loop` imports `admin_transaction` (not legacy
     single-conn `admin_connection`).
  2. `_ttl_sweep` is invoked inside its OWN `admin_transaction(pool)`
     block — NOT nested inside the per-assertion path.
  3. The `if errors == 0` short-circuit gating `_ttl_sweep` is GONE.
  4. `run_assertions_once` accepts a pool (not a Connection), proving
     per-assertion isolation downstream.
  5. The `conn_dead` band-aid from commit b55846cb is GONE (per
     Gate A P0-5; was masking the cascade-fail class defensively).

Sibling pattern: `test_minio_worm_bucket_validation_pinned.py` (pin
on operator-discipline rule); `test_email_opacity_harmonized.py`
(pin on banned-shape rule).
"""
from __future__ import annotations

import ast
import pathlib

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent
_ASSERTIONS = _REPO / "mcp-server" / "central-command" / "backend" / "assertions.py"


def _load_module() -> ast.Module:
    return ast.parse(_ASSERTIONS.read_text())


def _find_func(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function {name} missing from {_ASSERTIONS}")


def test_assertions_loop_imports_admin_transaction():
    """`assertions_loop` MUST import `admin_transaction` (the
    per-statement-pinning helper). The legacy single-conn
    `admin_connection` import is forbidden here — it would silently
    revert to the cascade-fail design where one bad assertion
    poisons every subsequent assertion in the tick."""
    tree = _load_module()
    loop = _find_func(tree, "assertions_loop")
    src = ast.unparse(loop)
    assert "admin_transaction" in src, (
        "assertions_loop MUST import `admin_transaction` from "
        "tenant_middleware — proves per-assertion isolation design. "
        "Without it, one asyncpg InterfaceError cascades across the "
        "remaining 60+ assertions in the tick."
    )


def test_ttl_sweep_runs_in_its_own_admin_transaction():
    """`_ttl_sweep` MUST run inside its OWN `admin_transaction(pool)`
    block, NOT inside the per-assertion path's conn. This way a
    poisoned per-assertion conn does not suppress the sigauth
    reclaim. Independent fault domain."""
    tree = _load_module()
    loop = _find_func(tree, "assertions_loop")
    src = ast.unparse(loop)
    # The sweep MUST be wrapped in a transaction context that opens
    # a NEW conn — pattern is `async with admin_transaction(pool) as
    # <something>:` followed by `_ttl_sweep(<something>)`.
    assert "_ttl_sweep" in src, "assertions_loop must still call _ttl_sweep"
    # Look for the sweep invocation occurring after an
    # `admin_transaction(pool)` context-manager open.
    lines = src.splitlines()
    sweep_lines = [i for i, ln in enumerate(lines) if "_ttl_sweep(" in ln]
    assert sweep_lines, "no _ttl_sweep call found"
    # Within 5 preceding lines, an `admin_transaction(pool)` opener
    # MUST appear — otherwise the sweep is sharing the assertion-path
    # conn.
    for sweep_idx in sweep_lines:
        window = "\n".join(lines[max(0, sweep_idx - 5):sweep_idx + 1])
        assert "admin_transaction(pool)" in window, (
            f"_ttl_sweep at relative line {sweep_idx} is NOT inside an "
            f"independent admin_transaction(pool) block. Gate A P0-4 "
            f"requires the sweep to run in its OWN fault domain so a "
            f"poisoned per-assertion conn does not suppress sigauth "
            f"reclaim."
        )


def test_ttl_sweep_not_gated_on_errors_zero():
    """The `if errors == 0` short-circuit gating `_ttl_sweep` is
    GONE. Under per-assertion isolation, errors on one assertion's
    conn no longer affect the sweep's fault domain — the gate was
    silently dropping the sweep on EVERY tick with even one transient
    error, letting sigauth_observations grow unboundedly."""
    tree = _load_module()
    loop = _find_func(tree, "assertions_loop")
    src = ast.unparse(loop)
    # Heuristic: look for a conditional `if counters.get('errors'`
    # immediately followed by `_ttl_sweep`. That's the banned shape.
    forbidden_pairs = [
        ("if counters.get('errors', 0) == 0", "_ttl_sweep"),
        ('if counters.get("errors", 0) == 0', "_ttl_sweep"),
        ("if counters['errors'] == 0", "_ttl_sweep"),
        ('if counters["errors"] == 0', "_ttl_sweep"),
    ]
    for guard, sweep in forbidden_pairs:
        if guard in src:
            # Confirm the sweep is what's being guarded — look at the
            # next 3 lines after the guard.
            idx = src.find(guard)
            window = src[idx:idx + 200]
            assert sweep not in window, (
                f"`_ttl_sweep` is still gated on `errors == 0` "
                f"({guard!r}). Gate A P0-4 explicitly removes this "
                f"short-circuit — sweep runs in its OWN fault domain "
                f"and is independent of per-assertion errors."
            )


def test_run_assertions_once_accepts_pool():
    """`run_assertions_once` MUST accept a pool (named arg `pool`),
    NOT a Connection. The signature itself is load-bearing — proves
    that per-assertion `admin_transaction(pool)` is the call shape
    downstream, not a single outer-conn design."""
    tree = _load_module()
    func = _find_func(tree, "run_assertions_once")
    arg_names = [a.arg for a in func.args.args]
    assert arg_names == ["pool"], (
        f"run_assertions_once signature is {arg_names!r}, expected "
        f"['pool']. Gate A P0-2 requires the function to OWN the "
        f"per-assertion admin_transaction wrap — that means it takes "
        f"a pool, not a pre-acquired Connection."
    )


def test_conn_dead_band_aid_removed():
    """The `conn_dead` defensive band-aid from commit b55846cb is
    GONE (Gate A P0-5). Under per-assertion isolation, the flag
    would skip valid work for no reason — and its presence would
    signal that the cascade-fail class is being mitigated defensively
    rather than fixed architecturally."""
    src = _ASSERTIONS.read_text()
    # Tolerate the word in comments/docstrings that EXPLAIN the removal
    # (the run_assertions_once docstring references it historically).
    # Forbid actual code use: a `conn_dead =` assignment or a
    # `conn_dead` read in an `if`/`while`/`and`/`or` expression.
    forbidden = [
        "conn_dead = True",
        "conn_dead = False",
        "if conn_dead",
        "while conn_dead",
        "and conn_dead",
        "or conn_dead",
    ]
    for pat in forbidden:
        assert pat not in src, (
            f"`{pat}` is still present in assertions.py. Gate A P0-5 "
            f"requires the conn_dead band-aid to be deleted — "
            f"per-assertion isolation makes it both unnecessary and "
            f"harmful (it would skip valid work)."
        )
