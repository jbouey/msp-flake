"""CI gate: `current_signing_method` must be imported at module level,
not inside function bodies.

Vault Phase C P0 #6 (audit/coach-vault-p0-bundle-gate-a-redo-2-2026-05-13.md).
The 2026-05-12 revert chain's iter-1 root cause was a function-body
`from .signing_backend import current_signing_method` wrapped in an outer
try/except that swallowed the ImportError when tests imported the module
without package context. Result: INSERT calls silently skipped + pg-tests
red with `expected 1 order created, got 0`.

Module-level imports fail fast at module-load time, before any test
fixture runs. Tests that can't import the module get a clear error;
tests that can import work correctly.

Module-level try/except for dual import-context support IS allowed (e.g.
flywheel_promote.py module-level `try: from .signing_backend ... except
ImportError: from signing_backend ...`). What's banned is the SAME shape
inside a FunctionDef / AsyncFunctionDef body.

The class generalizes: any helper that's invoked at INSERT time should
be imported at module level. Add additional banned names below.
"""
from __future__ import annotations

import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Names that must NOT be imported inside a function body.
BANNED_FUNCTION_BODY_NAMES = {
    "current_signing_method",
}


def _function_body_imports(tree: ast.AST) -> list[tuple[int, str]]:
    """Yield (lineno, alias_name) for each ImportFrom node nested
    inside a FunctionDef / AsyncFunctionDef body.
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if inner is node:
                continue
            if not isinstance(inner, ast.ImportFrom):
                continue
            if not inner.module or "signing_backend" not in inner.module:
                continue
            for alias in inner.names:
                if alias.name in BANNED_FUNCTION_BODY_NAMES:
                    out.append((inner.lineno, alias.name))
    return out


def test_no_function_body_import_of_current_signing_method():
    """`current_signing_method` imported inside a function body hides
    ImportError silently → iter-1 revert root cause.

    To fix a violation:
      1. Move the import to module level (top of file, near other imports).
      2. If the module is import-context-flexible (no `.` package), use
         module-level try/except like flywheel_promote.py — NEVER move
         the try/except INTO the function.
    """
    violations: list[str] = []
    for py_path in sorted(_BACKEND.rglob("*.py")):
        if any(seg in py_path.parts for seg in (
            "tests", "migrations", "substrate_runbooks", "templates",
            "__pycache__", "scripts", "venv",
        )):
            continue
        try:
            tree = ast.parse(py_path.read_text())
        except (OSError, SyntaxError):
            continue
        for lineno, name in _function_body_imports(tree):
            rel = py_path.relative_to(_BACKEND)
            violations.append(
                f"{rel}:{lineno} — function-body import of {name!r} "
                f"from signing_backend. Move to module level (see "
                f"fleet_updates.py:19 gold pattern, or flywheel_promote.py "
                f"module-level try/except for import-context-flexible modules)."
            )
    assert not violations, (
        f"{len(violations)} function-body import(s) of banned name(s):\n  "
        + "\n  ".join(violations)
    )
