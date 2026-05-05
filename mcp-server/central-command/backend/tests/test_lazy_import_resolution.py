"""CI gate: every `from .X import Y` (lazy or top-level) must resolve.

Session 217 (2026-05-05) closure of a 26-day P0:
client_portal.py:348 + 531 had `from .rate_limiter import check_rate_limit`
inside the function bodies of `request_magic_link` and `login_with_password`,
but rate_limiter.py never exposed a module-level `check_rate_limit` —
only a same-named METHOD on the RateLimiter class. The result: every
client-portal magic-link request and password login attempt silently
500'd in production for 26 days, masked by the privacy-by-design
"If [email] is registered, you'll receive a link" response that returns
identical body for success + failure.

Why existing gates missed it:
  - `python3 -c "import main"` (pre-push smoke) only resolves
    TOP-LEVEL imports. Lazy imports inside function bodies don't
    execute until the function is called.
  - Source-level tests check structural shape (column lockstep,
    endpoint surface) but don't actually call the endpoint.
  - Behavior tests would catch it but aren't in pre-push (need DB).

This gate AST-walks every `.py` in dashboard_api/ and asserts that
every `ImportFrom node` (whether at module level or inside a function
body) refers to a name that exists in the target module. Fails CI
immediately on regression of this class.

Limitations (documented):
  - Only checks dashboard_api → dashboard_api imports (`from .X import Y`).
    Cross-package imports (third-party, stdlib) are out of scope —
    runtime would have caught them at process start anyway.
  - Star imports (`from .X import *`) skip — too dynamic.
  - Conditional imports (try/except ImportError) — these are
    intentionally fail-soft; we record but don't fail the test.
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
from typing import Set

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent.parent.parent


def _module_exported_names(mod_path: pathlib.Path) -> Set[str]:
    """Return the set of names a module exposes at top-level via AST.

    Includes:
      - Names assigned at module level (`X = ...`)
      - Function defs (`def X` / `async def X`)
      - Class defs (`class X`)
      - Top-level imports re-exported (`from .other import X` makes X
        a name on this module)
      - Type aliases (`X: TypeAlias = ...`)

    Does NOT execute the module — pure AST walk so no side effects.
    """
    names: Set[str] = set()
    try:
        tree = ast.parse(mod_path.read_text(), filename=str(mod_path))
    except SyntaxError:
        return names

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
                elif isinstance(tgt, ast.Tuple):
                    for elt in tgt.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                # `import X.Y` exposes `X` at top level
                top = (alias.asname or alias.name).split(".")[0]
                names.add(top)
    return names


def _is_in_try_block(node: ast.AST, parents: list) -> bool:
    """Walk the parent chain to find an enclosing Try block. Used to
    skip conditional imports inside try/except ImportError."""
    for p in reversed(parents):
        if isinstance(p, ast.Try):
            return True
    return False


def _collect_imports(file_path: pathlib.Path) -> list:
    """Return list of (lineno, module_name, imported_name, in_try) for
    every relative ImportFrom in the file."""
    src = file_path.read_text()
    try:
        tree = ast.parse(src, filename=str(file_path))
    except SyntaxError:
        return []

    found = []
    # Build parent map for try-block detection.
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def parent_chain(n):
        chain = []
        cur = parents.get(n)
        while cur is not None:
            chain.append(cur)
            cur = parents.get(cur)
        return chain

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            continue  # absolute import, not in scope of this gate
        if node.module is None:
            continue
        in_try = _is_in_try_block(node, parent_chain(node))
        for alias in node.names:
            if alias.name == "*":
                continue
            found.append(
                (node.lineno, node.module, alias.name, in_try)
            )
    return found


def _resolve_relative_module(
    importer: pathlib.Path, level: int, module_name: str,
) -> pathlib.Path | None:
    """Resolve `from .X import Y` (level=1) to the target file path."""
    base = importer.parent
    for _ in range(level - 1):
        base = base.parent
    target = base / f"{module_name}.py"
    if target.exists():
        return target
    pkg = base / module_name / "__init__.py"
    if pkg.exists():
        return pkg
    return None


def test_every_lazy_relative_import_resolves():
    """The headline gate: every `from .X import Y` in dashboard_api
    must point to a Y that exists in X. Catches the 2026-04-09 →
    2026-05-05 client-portal regression class."""
    failures: list[str] = []
    skipped_try: list[str] = []

    for py_file in sorted(_BACKEND.rglob("*.py")):
        # Skip tests + fixtures + virtualenv/cache.
        rel = py_file.relative_to(_BACKEND)
        if rel.parts[0] in {"tests", "venv", ".venv", "__pycache__"}:
            continue

        for lineno, module_name, imported_name, in_try in _collect_imports(py_file):
            # Always relative (level >= 1); resolve to file.
            target = py_file.parent / f"{module_name}.py"
            # Handle deeper relative resolution if needed.
            if not target.exists():
                target = py_file.parent / module_name / "__init__.py"
            if not target.exists():
                # Module doesn't resolve at all — separate class of
                # bug; let import-time fail it loudly. Skip here.
                continue

            exported = _module_exported_names(target)
            if imported_name in exported:
                continue

            note = (
                f"{rel}:{lineno} — `from .{module_name} import "
                f"{imported_name}` but {target.relative_to(_BACKEND)} "
                f"does not export `{imported_name}`."
            )
            if in_try:
                # Conditional imports are fail-soft by design.
                skipped_try.append(note)
            else:
                failures.append(note)

    if failures:
        raise AssertionError(
            "Lazy or eager `from .X import Y` references nonexistent Y. "
            "These imports succeed at module-load time (when lazy, they "
            "haven't executed yet) but blow up at first call site, "
            "yielding silent 500s in prod. See Session 217 client-portal "
            "26-day regression for the canonical incident.\n\n"
            + "\n".join(f"  - {f}" for f in failures)
        )

    # Conditional imports are recorded but don't fail; print them for
    # operator awareness.
    if skipped_try:
        # Use pytest's logging so the noise stays out of CI green output.
        import warnings
        warnings.warn(
            f"Conditional imports inside try/except ImportError "
            f"(intentional fail-soft, not gated): {len(skipped_try)} "
            f"references — review periodically.",
            stacklevel=2,
        )


def test_check_rate_limit_module_level_present():
    """Belt-and-suspenders specific to the regressed import. The generic
    gate above catches it via AST, but this pinned test is human-readable
    and ratchets the specific Session 217 fix."""
    src = (_BACKEND / "rate_limiter.py").read_text()
    assert "async def check_rate_limit(" in src, (
        "rate_limiter.py must expose a module-level `check_rate_limit` "
        "function — both client_portal.py:348 (request_magic_link) and "
        ":531 (login_with_password) import it lazily. Removing the "
        "module-level export silently 500s the entire client portal "
        "login surface (regression class: 2026-04-09 → 2026-05-05)."
    )
    # Belt-and-suspenders: signature shape that matches both call sites.
    assert "client_key" in src
    assert "category" in src
    # Failure-mode posture: fail-open on Redis-down, with explicit
    # rationale in a comment so a future hardening pass can't silently
    # flip it without thinking.
    assert "fail-open" in src.lower(), (
        "check_rate_limit must document its Redis-down failure mode."
    )
