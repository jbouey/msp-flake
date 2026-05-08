"""Regression gate — supervised background loops in `mcp-server/main.py`
MUST acquire admin context before issuing queries against RLS-protected
tables.

The audit on 2026-05-08 (`audit/coach-e2e-attestation-audit-2026-05-08.md`
F-P0-1) caught a production rupture: 3 background loops in main.py used
the bare `pool.acquire()` path instead of `admin_transaction(pool)`.
The asyncpg pool connects through PgBouncer; PgBouncer-routed backends
inherit `app.is_admin = 'false'` (mig 234 tenant-safety default). RLS
silently filtered every query to zero rows.

Loops affected (all fixed in commit `7db2faab`):
  - `_merkle_batch_loop`         — 18 days of unanchored evidence
  - `_evidence_chain_check_loop` — substrate blind to chain corruption
  - `expire_fleet_orders_loop`   — UPDATE no-op'd silently

This gate enforces the rule structurally: any function whose name ends
with `_loop` AND that takes admin-required action (query or mutate
RLS-protected tables) MUST use `admin_transaction(pool)` or
`admin_connection(pool)`.

The check is conservative — it BANS bare `pool.acquire()` in any
function named `*_loop` regardless of which tables it touches. If a
loop legitimately needs the bare `pool.acquire()` path (e.g. it only
writes to RLS-free tables), add the function name to LOOP_ALLOWLIST
with a justification.
"""
from __future__ import annotations

import ast
import pathlib

_REPO = pathlib.Path(__file__).resolve().parents[4]
_MAIN = _REPO / "mcp-server" / "main.py"


# Functions that legitimately use bare `pool.acquire()` in a *_loop
# context. Each entry must include a why-justified comment.
LOOP_ALLOWLIST: dict[str, str] = {
    # Verified 2026-05-08 via `SELECT FROM pg_policies WHERE tablename
    # IN ('compliance_packets', 'sites', 'admin_audit_log')` returning
    # 0 rows — these three tables have NO RLS policies. The two loops
    # below only read/write those tables and are safe with bare
    # `pool.acquire()`.
    "_compliance_packet_loop": "reads sites + compliance_packets — neither has RLS policies (verified 2026-05-08)",
    "_audit_log_retention_loop": "DELETE on admin_audit_log — table has no RLS policies (verified 2026-05-08)",
    # If RLS is ever added to any of these tables, REMOVE the
    # allowlist entry and migrate to admin_transaction. The substrate
    # invariant `bg_loop_rls_admin_context_required` (queued) will
    # detect the divergence at runtime.
}


def _bg_loop_violations() -> list[str]:
    """Walk main.py and return every (function_name, line_no) where a
    `*_loop` function uses `pool.acquire()` without first having
    imported or referenced `admin_transaction` or `admin_connection`
    in its enclosing scope.
    """
    src = _MAIN.read_text()
    tree = ast.parse(src)
    violations: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef):
            self._check(node)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
            self._check(node)
            self.generic_visit(node)

        def _check(self, node: ast.AST):
            name = getattr(node, "name", "")
            if not name.endswith("_loop"):
                return
            if name in LOOP_ALLOWLIST:
                return
            # Walk this function's body: does it call admin_transaction
            # or admin_connection somewhere?
            uses_admin = False
            uses_pool_acquire = False
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute):
                    if sub.attr in ("admin_transaction", "admin_connection"):
                        uses_admin = True
                    if sub.attr == "acquire":
                        # Heuristic: pool.acquire() shape — Attribute named "acquire"
                        # with the value being a Name like "pool" / "ks_pool" / "_pool".
                        val = sub.value
                        if isinstance(val, ast.Name) and val.id.endswith("pool"):
                            uses_pool_acquire = True
                if isinstance(sub, ast.Name) and sub.id in (
                    "admin_transaction",
                    "admin_connection",
                ):
                    uses_admin = True
            if uses_pool_acquire and not uses_admin:
                violations.append(
                    f"main.py:{node.lineno} — `{name}` uses bare "
                    f"`pool.acquire()` with no `admin_transaction`/"
                    f"`admin_connection` reference in scope. PgBouncer-"
                    f"routed default is app.is_admin='false' — every "
                    f"RLS-protected query silently returns 0 rows. "
                    f"See audit F-P0-1 (2026-05-08)."
                )

    Visitor().visit(tree)
    return violations


def test_no_bare_pool_acquire_in_supervised_loops():
    """Baseline 0 — every `*_loop` in main.py uses admin_transaction
    or admin_connection. New violations BLOCKED.

    Rationale: see module docstring + audit/coach-e2e-attestation-
    audit-2026-05-08.md F-P0-1.
    """
    violations = _bg_loop_violations()
    assert not violations, (
        "Background loop in main.py uses bare pool.acquire() — "
        "PgBouncer-routed backend default is app.is_admin='false', "
        "RLS will silently filter every query to zero rows.\n\n"
        + "\n".join(f"  - {v}" for v in violations)
        + "\n\nFix: import admin_transaction from "
        "dashboard_api.tenant_middleware and replace the "
        "`async with pool.acquire() as conn:` block. Or add the "
        "function name to LOOP_ALLOWLIST with a why-justified comment "
        "if the loop genuinely doesn't need admin context."
    )


def test_known_post_fix_loops_use_admin_transaction():
    """Pin the 3 loops fixed in commit 7db2faab. If any of them
    regresses to `pool.acquire()`, fail loudly with a specific
    message naming the loop.
    """
    src = _MAIN.read_text()
    tree = ast.parse(src)

    expected = {
        "_merkle_batch_loop": "audit F-P0-1: 18d Merkle stall pre-fix",
        "_evidence_chain_check_loop": "audit F-P0-1 sibling: chain integrity check was RLS-blind",
        "expire_fleet_orders_loop": "audit F-P0-1 sibling: fleet-order expiry was no-op'd",
    }

    found_admin: dict[str, bool] = {n: False for n in expected}
    found_pool_acquire: dict[str, bool] = {n: False for n in expected}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if node.name not in expected:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute):
                if sub.attr in ("admin_transaction", "admin_connection"):
                    found_admin[node.name] = True
                if sub.attr == "acquire" and isinstance(sub.value, ast.Name) \
                        and sub.value.id.endswith("pool"):
                    found_pool_acquire[node.name] = True
            if isinstance(sub, ast.Name) and sub.id in (
                "admin_transaction",
                "admin_connection",
            ):
                found_admin[node.name] = True

    for fn, why in expected.items():
        assert found_admin[fn], (
            f"Loop `{fn}` should use admin_transaction/admin_connection "
            f"({why}) but no such reference found. Pre-fix this loop was "
            f"silently RLS-filtered to zero rows. Did the fix regress?"
        )
        assert not found_pool_acquire[fn], (
            f"Loop `{fn}` still contains a bare `pool.acquire()` call "
            f"— remove it. {why}."
        )
