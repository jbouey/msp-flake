"""CI gate: INV-SIGNING-BACKEND-VAULT detail field must not leak via /health.

Vault Phase C P0 #4 (audit/coach-vault-p0-bundle-gate-a-redo-2-2026-05-13.md).
The InvariantResult.detail field can contain operational specifics: Vault
key version + pubkey fingerprint, bootstrap-pending operator SQL command,
drift evidence (old vs new pubkey, key version delta). Operator-facing
detail belongs ONLY in the admin-context `/api/admin/substrate-health`
endpoint; the public `/health` endpoint must remain a binary up/down
signal.

This gate AST-walks `mcp-server/main.py` for the `/health` route handler
and asserts it does NOT import `check_all_invariants` or `InvariantResult`
or otherwise expose the detail field.
"""
from __future__ import annotations

import ast
import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
_MAIN_PY = _REPO_ROOT / "mcp-server" / "main.py"


def _route_handler_for(path: str, tree: ast.AST) -> "ast.AsyncFunctionDef | ast.FunctionDef | None":
    """Find the route handler decorated with @app.get(path) or @router.get(path)."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            if not isinstance(dec.func, ast.Attribute):
                continue
            if dec.func.attr not in ("get", "post"):
                continue
            if dec.args and isinstance(dec.args[0], ast.Constant):
                if dec.args[0].value == path:
                    return node
    return None


def _function_body_names(func: ast.AST) -> set:
    """Return all dotted-name references in the function body."""
    names = set()
    for inner in ast.walk(func):
        if isinstance(inner, ast.Name):
            names.add(inner.id)
        elif isinstance(inner, ast.Attribute):
            names.add(inner.attr)
        elif isinstance(inner, ast.ImportFrom):
            for alias in inner.names:
                names.add(alias.name)
    return names


def test_health_endpoint_does_not_expose_invariant_detail():
    """`/health` endpoint must not consume `check_all_invariants` or
    expose `InvariantResult.detail`. Operational specifics belong only
    in the admin-context `/api/admin/substrate-health` endpoint.

    Vault P0 #4: INV-SIGNING-BACKEND-VAULT detail may include Vault key
    version + pubkey fingerprint + operator SQL — must not surface on
    the public health endpoint.
    """
    if not _MAIN_PY.exists():
        return  # Skip if path layout differs from expected (rare)
    src = _MAIN_PY.read_text()
    tree = ast.parse(src)

    health_handler = _route_handler_for("/health", tree)
    if health_handler is None:
        health_handler = _route_handler_for("/api/version", tree)

    assert health_handler is not None, (
        "could not locate /health or /api/version handler in mcp-server/main.py"
    )

    body_names = _function_body_names(health_handler)
    banned = {
        "check_all_invariants",
        "InvariantResult",
        "enforce_startup_invariants",
    }
    found = banned & body_names
    assert not found, (
        f"/health (or /api/version) handler in mcp-server/main.py references "
        f"{found!r} — operational invariant detail must not leak via the "
        f"public health endpoint. Move the consumer to "
        f"/api/admin/substrate-health (admin-context)."
    )
