"""Pure-source guard: every name `lifespan` imports from dashboard_api
modules must actually be defined in those modules.

This catches the 2026-04-24 crashloop class:

  - 82a1f5d2 deleted `l2_auto_candidate_loop` from `background_tasks.py`
    along with the dead duplicate `flywheel_promotion_loop`.
  - main.py's `lifespan()` still imported `l2_auto_candidate_loop` from
    that module — but the import was inside the function body, so a
    bare `python -c "import main"` smoke-check did NOT trigger it.
  - In prod, FastAPI ran `lifespan()`, the deferred import raised
    ImportError, FastAPI exited the lifespan, the ASGI server crashed,
    Docker restarted — every 6-10 seconds for 2 minutes. The deploy
    /health verify timed out after 120s and the workflow rolled back.

Why source-level and not real-import: `dashboard_api.background_tasks`
requires asyncpg + structlog + sqlalchemy at import time. The pre-push
hook runs on a dev Python where those may not be installed. AST parse
catches the same class of bug without dragging in the runtime stack.

If someone adds a new module to the lifespan deferred imports, the test
self-discovers it via AST walk — no manual list to maintain.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
MAIN_PY = REPO_ROOT / "mcp-server" / "main.py"
BACKEND_DIR = REPO_ROOT / "mcp-server" / "central-command" / "backend"


def _module_top_level_names(path: pathlib.Path) -> set[str]:
    """Collect every top-level public binding in a .py module via AST.

    Includes `def`, `async def`, `class`, top-level assignments, and
    re-exports through `from X import Y` / `import X as Y` at module
    top-level. This is what `from <mod> import <name>` will resolve.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _lifespan_deferred_imports() -> list[tuple[str, str, int]]:
    """Walk main.py's `lifespan` async function body and collect every
    `from dashboard_api.X import Y` it does. Returns list of
    (target_module, imported_name, lineno).

    Only resolves imports that target `dashboard_api.*` — third-party
    or stdlib imports are out of scope (let runtime catch those).
    """
    src = MAIN_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: list[tuple[str, str, int]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef) or fn.name != "lifespan":
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.ImportFrom):
                continue
            if not (node.module or "").startswith("dashboard_api"):
                continue
            for alias in node.names:
                # Skip `from X import *`
                if alias.name == "*":
                    continue
                out.append((node.module, alias.name, node.lineno))
    return out


def _module_path(module: str) -> pathlib.Path | None:
    """Resolve `dashboard_api.foo.bar` to a file under backend/."""
    if module == "dashboard_api":
        return BACKEND_DIR / "__init__.py"
    rel = module.removeprefix("dashboard_api.").replace(".", "/")
    p = BACKEND_DIR / f"{rel}.py"
    if p.exists():
        return p
    p = BACKEND_DIR / rel / "__init__.py"
    if p.exists():
        return p
    return None


@pytest.fixture(scope="module")
def deferred_imports() -> list[tuple[str, str, int]]:
    return _lifespan_deferred_imports()


def test_main_py_has_lifespan_function():
    """Sanity: main.py defines async def lifespan."""
    src = MAIN_PY.read_text(encoding="utf-8")
    assert "async def lifespan" in src, "main.py missing lifespan() — refactor moved it?"


def test_lifespan_imports_at_least_one_dashboard_api_module(deferred_imports):
    """Sanity: walker found something. If this fails, lifespan was rewritten
    and this test needs review."""
    assert deferred_imports, (
        "AST walk found zero `from dashboard_api.X import Y` calls inside "
        "main.py's lifespan(). Either lifespan was refactored (review this "
        "test) or the AST walker is broken."
    )


def test_every_lifespan_deferred_import_resolves(deferred_imports):
    """The crashloop guard. Every deferred name must exist in its target.

    A failure here means: main.py imports a function that doesn't exist
    in the module it's importing from. That's the exact bug that took
    prod down on 2026-04-24 (l2_auto_candidate_loop deleted but still
    imported). Pre-push fails locally — push never reaches CI.
    """
    failures: list[str] = []
    # Cache parsed module names so we read each file once even if
    # lifespan imports many names from the same module.
    cache: dict[str, set[str]] = {}
    for module, name, lineno in deferred_imports:
        path = _module_path(module)
        if path is None:
            failures.append(
                f"main.py:{lineno} imports `{name}` from `{module}` — "
                f"that module path does not exist under backend/."
            )
            continue
        if module not in cache:
            cache[module] = _module_top_level_names(path)
        if name not in cache[module]:
            failures.append(
                f"main.py:{lineno} imports `{name}` from `{module}` — "
                f"name not defined at top level of {path.relative_to(REPO_ROOT)}."
            )
    assert not failures, (
        "Lifespan deferred-import resolution FAILED. Each missing name "
        "would cause a prod crashloop on the next deploy (FastAPI lifespan "
        "raises ImportError → container exits → Docker restarts → repeat). "
        "Either restore the missing function or remove its import.\n\n"
        + "\n".join(f"  - {f}" for f in failures)
    )
