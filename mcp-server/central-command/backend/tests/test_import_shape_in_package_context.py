"""CI gate: ban function-scope bare imports of local backend modules
(Task #72).

Production runs `dashboard_api` as a Python package (cwd=/app, NOT
/app/dashboard_api). At module-import time, top-level `from dashboard_api.X import Y`
statements resolve correctly. But at FUNCTION CALL time, a function-
scope `from <local_module> import` re-resolves against sys.path — and
sys.path[0] is the working directory, NOT /app/dashboard_api. So
function-scope `from signature_auth import` raises ModuleNotFoundError
in production while passing local tests (cwd=backend/ during pytest).

This is the 2026-05-13 4h+ dashboard outage class (sites.py:4231 fixed
in commit adb7671a + sites.py savepoint fix in 3ec431c8). Task #72
closes it structurally.

Gate A v1 (audit/coach-import-shape-gate-gate-a-2026-05-13.md) +
v2 (audit/coach-import-shape-gate-v2-gate-a-2026-05-13.md) APPROVE
with empty ALLOWLIST + hard-fail-on-NEW + audit_report.py:213 fixed
in same commit.

Classifier shape:
  - Walk every ast.ImportFrom node at function-scope (or deeper)
  - For each, classify the module:
      LOCAL_TOP (in backend/ dir) → fail unless guarded
      `dashboard_api.X` (package-prefixed sibling) → accept as relative-equivalent
      `.X` or `..X` (true relative) → accept
      STDLIB / THIRDPARTY → accept
  - If LOCAL_TOP bare: walk parent chain for try-except-ImportError
    wrapper (the relative-then-absolute fallback) — accept if found

Empty allowlist contract: every truly-bare local import MUST be
wrapped in the fallback. No exceptions.
"""
from __future__ import annotations

import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Mechanical enumeration of backend top-level module names. Re-derived
# from os.listdir on every test run so adding a new file
# (`backend/new_module.py`) automatically extends LOCAL_TOPS without
# manifest maintenance.
LOCAL_TOPS = frozenset({
    p.stem for p in _BACKEND.glob("*.py")
    if not p.stem.startswith("_") and p.stem != "__init__"
})

# Empty allowlist contract per Gate A v1/v2.
ALLOWLIST = frozenset()


def _is_local_top(module: str) -> bool:
    """Module name is a backend sibling top-level module."""
    if not module:
        return False
    top = module.split(".", 1)[0]
    return top in LOCAL_TOPS


def _is_package_prefixed_sibling(module: str) -> bool:
    """`dashboard_api.X` shape where X is a backend sibling — this is
    the production package-import path, equivalent to relative."""
    if not module.startswith("dashboard_api."):
        return False
    tail = module[len("dashboard_api."):]
    return tail.split(".", 1)[0] in LOCAL_TOPS


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """Map id(node) -> parent node for ancestor walks."""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _is_inside_function(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    """True if node has a FunctionDef / AsyncFunctionDef ancestor."""
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return True
        cur = parents.get(id(cur))
    return False


def _is_guarded(node: ast.ImportFrom, parents: dict[int, ast.AST]) -> bool:
    """Walk ancestors for a Try whose handler catches ImportError. The
    sibling-import-in-except pattern is the canonical guard.

    Handles NESTED Try chains (assertions.py:404-410 pattern: 3-level
    nested try-except-try-except fallback). Per Gate A v1 P1.
    """
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.Try):
            # Check handlers for ImportError
            for handler in cur.handlers:
                if handler.type is None:
                    continue  # bare except — accept defensively
                if isinstance(handler.type, ast.Name) and handler.type.id == "ImportError":
                    # Also check the handler body for a sibling import —
                    # the relative-then-absolute fallback pattern.
                    if _try_body_has_sibling_import(cur):
                        return True
                elif isinstance(handler.type, ast.Tuple):
                    for elt in handler.type.elts:
                        if isinstance(elt, ast.Name) and elt.id == "ImportError":
                            if _try_body_has_sibling_import(cur):
                                return True
                # Also: if the import we're checking is INSIDE a handler
                # of a Try whose body also has an import → accept (the
                # "absolute fallback in except" shape).
            # Also check if THIS node is in the handler body of a Try
            # whose body has the relative version — the second half of
            # the canonical pattern.
            for handler in cur.handlers:
                for stmt in handler.body:
                    if any(n is node for n in ast.walk(stmt)):
                        # node is inside an ImportError handler — the
                        # body of the same Try should have the relative
                        # version (sibling).
                        if _try_body_has_sibling_import(cur):
                            return True
        cur = parents.get(id(cur))
    return False


def _try_body_has_sibling_import(try_node: ast.Try) -> bool:
    """Check the Try's body AND its except-handler bodies for a relative
    import (`from .X import`) or a `dashboard_api.X` sibling.

    Either order of try-except is acceptable:
      try: from .X import ... / except: from X import ...
      try: from X import ... / except: from .X import ...
    The key is that ANY branch of the try/except has a working
    import shape.
    """
    def _scan(stmts):
        for stmt in stmts:
            for n in ast.walk(stmt):
                if isinstance(n, ast.ImportFrom):
                    if (n.level or 0) >= 1:
                        return True
                    if n.module and _is_package_prefixed_sibling(n.module):
                        return True
        return False

    if _scan(try_node.body):
        return True
    for handler in try_node.handlers:
        if _scan(handler.body):
            return True
    return False


def _file_violations(path: pathlib.Path) -> list[tuple[int, str, str]]:
    """Return list of (line_no, module, why) violations.

    Exempts:
      - This gate file (mentions the pattern as documentation)
      - Test files (don't run in production package context; cwd is
        backend/ during pytest, so bare imports resolve fine; module
        re-import shape rules apply to PRODUCTION code only)
    """
    if path.name == "test_import_shape_in_package_context.py":
        return []
    # Test files run from backend/ cwd (pytest discovery); bare local
    # imports work in test context. Production-only class.
    if path.name.startswith("test_"):
        return []
    try:
        src = path.read_text()
        tree = ast.parse(src)
    except (SyntaxError, OSError):
        return []
    parents = _build_parent_map(tree)
    out: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        # Module-level imports are fine (re-resolution happens once at
        # process boot, where sys.path has the right shape).
        if not _is_inside_function(node, parents):
            continue
        # Relative imports (`from .X import`) are always fine
        if (node.level or 0) >= 1:
            continue
        module = node.module or ""
        # `dashboard_api.X` shape — accept (production package-import)
        if _is_package_prefixed_sibling(module):
            continue
        # Stdlib / third-party — accept (presence in LOCAL_TOPS is the gate)
        if not _is_local_top(module):
            continue
        # Bare local import inside a function — must be guarded.
        if _is_guarded(node, parents):
            continue
        key = f"{path.name}:{module}"
        if key in ALLOWLIST:
            continue
        out.append((
            node.lineno, module,
            f"function-scope `from {module} import ...` is bare local. "
            f"Wrap in try/except ImportError fallback: "
            f"`try: from .{module} import X / except ImportError: "
            f"from {module} import X`. Pre-fix the 2026-05-13 dashboard "
            f"outage (sites.py:4231) had this exact shape — production "
            f"package context = ModuleNotFoundError silently masked.",
        ))
    return out


def test_allowlist_lock():
    """Empty allowlist contract — no exceptions to the fallback rule.
    Per Gate A v1 + v2: any new bare local import must EITHER be wrapped
    OR explicitly added to ALLOWLIST with Gate A approval.
    """
    assert len(ALLOWLIST) == 0, (
        f"ALLOWLIST length is {len(ALLOWLIST)} — expected exactly 0 "
        f"(empty allowlist contract). Adding entries requires Gate A "
        f"approval + a memorialized reason."
    )


def test_local_tops_includes_known_modules():
    """Sanity check: LOCAL_TOPS includes expected backend module names."""
    must_include = {
        "signature_auth", "audit_report", "baa_status",
        "compliance_score", "client_portal", "agent_api", "sites",
        "routes", "assertions", "background_tasks", "device_sync",
    }
    missing = must_include - LOCAL_TOPS
    assert not missing, (
        f"LOCAL_TOPS missing expected backend modules: {missing}. "
        f"Has the backend dir layout changed?"
    )


def test_no_bare_local_imports_in_functions():
    """The load-bearing gate: no function-scope bare local imports."""
    all_violations: list[str] = []
    for py in _BACKEND.glob("*.py"):
        for line_no, module, why in _file_violations(py):
            all_violations.append(
                f"  {py.name}:{line_no} `from {module} import ...` — {why}"
            )
    assert not all_violations, (
        "Function-scope bare local imports detected. These FAIL in "
        "production package context (cwd=/app, NOT /app/dashboard_api). "
        "Same class as the 2026-05-13 4h dashboard outage (commit "
        "adb7671a). Wrap each in the relative-then-absolute fallback:\n"
        + "\n".join(all_violations)
    )
