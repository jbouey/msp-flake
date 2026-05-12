"""Pin gate — every state-changing endpoint with
`Depends(require_appliance_bearer)` must have its path covered by
`csrf.py:EXEMPT_PATHS` or `csrf.py:EXEMPT_PREFIXES`.

Session 220 task #122 (2026-05-11). Real bug class — Session 210-B
2026-04-24: `/api/journal/upload` shipped with `require_appliance_bearer`
but was not in csrf.py exempt lists. Every POST from
`msp-journal-upload.timer` got a silent CSRF 403. `journal_upload_events`
sat at 0 rows for days; the `journal_upload_never_received` substrate
invariant fired on the customer site before the gap was diagnosed.

Same class found again in Session 220 zero-auth audit: 7 sites.py POSTs
under `/api/sites/{site_id}/...` (NOT in EXEMPT_PREFIXES) returned silent
403 to every appliance request. This gate prevents the next instance.

SCOPE: any handler decorated with `@<x>.post|put|patch|delete(...)` AND
having `Depends(require_appliance_bearer)` in its parameter defaults.
GET/HEAD/OPTIONS are skipped per `CSRFMiddleware.SAFE_METHODS`.

PATH RESOLUTION (pure AST, no imports):
  full_path = include_router_prefix + APIRouter_prefix + decorator_path

  - `APIRouter(prefix="/api/x")` parsed from module-level router assigns
  - `app.include_router(router_var, prefix="/api/y")` parsed from main.py
  - Per Gate A P0-2, BOTH overlays must be applied.

EXEMPT MEMBERSHIP CHECK (per Gate A P0-1):
  full_path in EXEMPT_PATHS (set) OR
  any(full_path.startswith(p) for p in EXEMPT_PREFIXES) (tuple)

FAILURE MESSAGE: per Gate A P2, names file:line + handler + exact line to
paste into csrf.py — sibling shape from
`test_no_middleware_dispatch_raises_httpexception.py`.

INVERSE DIRECTION (every exempt has an appliance endpoint) is INTENTIONALLY
NOT IMPLEMENTED in v1 per Gate A P1 — false-positives ~15 legitimate
pre-login / OAuth / webhook entries. Defer to task #123 with explicit
`# csrf-allowlist:` comment scheme.
"""
from __future__ import annotations

import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent.parent.parent
_MCP_SERVER = _REPO / "mcp-server"
_MAIN_PY = _MCP_SERVER / "main.py"
_CSRF_PY = _BACKEND / "csrf.py"

_STATE_CHANGING_METHODS = {"post", "put", "patch", "delete"}

# Both appliance-bearer FastAPI dependencies. The `_full` variant
# (shared.py:571) returns a (site_id, appliance_id) tuple and is a
# strict superset of `require_appliance_bearer`. Gate B verdict
# 2026-05-11 caught the hardcoded-name miss: 6 callsites including
# journal_api.py:74 `/api/journal/upload` (the literal Session 210-B
# regression file) used `_full` and were invisible to the v1 gate.
# Closes the diff-only-review-missed-what-was-MISSING antipattern.
_APPLIANCE_BEARER_DEP_NAMES = {
    "require_appliance_bearer",
    "require_appliance_bearer_full",
}

# Known CSRF-blocked dead routes flagged by the Session 220 zero-auth audit
# (task #113). Each entry is a (method, full_path) the gate would otherwise
# fail on. These ARE bugs — silent CSRF-403 to any appliance that calls them
# — but they're already tracked under task #120 (Disposition triage for
# 7 CSRF-blocked "zero-auth" endpoints). The gate's role here is to PREVENT
# the set from growing; reducing it is the triage task's job.
#
# Each entry MUST cite the source-of-record below. To remove: either delete
# the endpoint OR add the path to csrf.py EXEMPT_PATHS / EXEMPT_PREFIXES
# under task #120, with explicit Gate A approval of the dispatched route.
#
# Source: audit/csrf-blocks-zero-auth-endpoints-finding-2026-05-11.md
_KNOWN_BLOCKED_DEAD_ROUTES: set[tuple[str, str]] = {
    # Top-level paths under /orders, /drift, /evidence/upload (agent_api.py
    # twins in main.py — registered, but unreached by appliance bearer in
    # 12h prod logs; CSRF middleware silently rejects).
    ("post", "/orders/acknowledge"),
    ("post", "/drift"),
    ("post", "/evidence/upload"),
    # /api/learning/promotion-report — L2 flywheel promotion intake.
    # CSRF-blocked, 0 calls in 12h prod logs.
    ("post", "/api/learning/promotion-report"),
    # /api/discovery/report — discovery batch report. CSRF-blocked.
    ("post", "/api/discovery/report"),
    # /api/alerts/email — appliance email alerts. CSRF-blocked.
    ("post", "/api/alerts/email"),
}


# ----------------------------------------------------------------------
# csrf.py — extract EXEMPT_PATHS (Set) AND EXEMPT_PREFIXES (Tuple)
# ----------------------------------------------------------------------

def _extract_csrf_exemptions() -> tuple[set[str], tuple[str, ...]]:
    """AST-parse csrf.py to extract the two exemption structures.
    Gate A P0-1: must read BOTH, not just EXEMPT_PATHS."""
    tree = ast.parse(_CSRF_PY.read_text())
    paths: set[str] = set()
    prefixes: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == "EXEMPT_PATHS" and isinstance(node.value, ast.Set):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        paths.add(elt.value)
            elif target.id == "EXEMPT_PREFIXES" and isinstance(node.value, ast.Tuple):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        prefixes.append(elt.value)
    return paths, tuple(prefixes)


def _is_exempt(path: str, paths: set[str], prefixes: tuple[str, ...]) -> bool:
    if path in paths:
        return True
    return any(path.startswith(p) for p in prefixes)


# ----------------------------------------------------------------------
# main.py — extract include_router overlay prefixes AND module-import
# resolution. A router is "registered" only if main.py both imports it
# (with or without alias) AND calls app.include_router on the alias.
# Gate A P0-2 + agent_api.py false-positive lesson (2026-05-11):
# scanning router files alone yields dead-code violations for modules
# whose router var isn't imported into main.py. Cross-file import
# tracking is required.
# ----------------------------------------------------------------------

def _extract_registry() -> dict[tuple[str, str], str]:
    """Returns `{(module_basename, original_router_var): include_prefix}`.

    Example: `from dashboard_api.discovery import router as discovery_router`
    + `app.include_router(discovery_router)` →
    {("discovery", "router"): ""}.

    Routers with no matching import-and-include pair are NOT in the registry
    and their handlers are skipped (dead code per main.py's perspective).
    """
    tree = ast.parse(_MAIN_PY.read_text())

    # Pass 1: import statements. Build alias→(module_basename, orig_name).
    alias_to_origin: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module is None:
            continue
        module_basename = node.module.rsplit(".", 1)[-1]
        for alias in node.names:
            local_name = alias.asname or alias.name
            alias_to_origin[local_name] = (module_basename, alias.name)

    # Pass 2: include_router calls. For each `app.include_router(X, prefix=Y)`,
    # if X is an aliased import, record (module, orig_name) → prefix.
    registry: dict[tuple[str, str], str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "include_router"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name):
            local_name = first.id
        elif isinstance(first, ast.Attribute):
            local_name = first.attr
        else:
            continue
        prefix = ""
        for kw in node.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                prefix = kw.value.value
                break
        # Look up the import that bound this local name.
        if local_name in alias_to_origin:
            registry[alias_to_origin[local_name]] = prefix
    return registry


# ----------------------------------------------------------------------
# module — extract APIRouter assignments → router_var → prefix
# ----------------------------------------------------------------------

def _extract_router_prefixes(tree: ast.AST) -> dict[str, str]:
    """For a single file's AST, find `<name> = APIRouter(prefix="...")`
    assignments. Returns {var_name: prefix}."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        # Match APIRouter(...) — Name "APIRouter" or Attribute ending in APIRouter
        is_router = False
        if isinstance(func, ast.Name) and func.id == "APIRouter":
            is_router = True
        elif isinstance(func, ast.Attribute) and func.attr == "APIRouter":
            is_router = True
        if not is_router:
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                prefix = kw.value.value
                break
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = prefix
    return out


# ----------------------------------------------------------------------
# handler detection — Depends(require_appliance_bearer) + decorator
# ----------------------------------------------------------------------

def _is_appliance_bearer_depends_call(node: ast.AST) -> bool:
    """True if `node` is a `Depends(require_appliance_bearer[_full])` Call."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if not ((isinstance(f, ast.Name) and f.id == "Depends") or
            (isinstance(f, ast.Attribute) and f.attr == "Depends")):
        return False
    for a in node.args:
        if isinstance(a, ast.Name) and a.id in _APPLIANCE_BEARER_DEP_NAMES:
            return True
        if isinstance(a, ast.Attribute) and a.attr in _APPLIANCE_BEARER_DEP_NAMES:
            return True
    return False


def _function_has_appliance_bearer_dep(func: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Scan a function's parameter defaults for `Depends(require_appliance_bearer[_full])`."""
    for default in list(func.args.defaults) + list(func.args.kw_defaults or []):
        if default is None:
            continue
        if _is_appliance_bearer_depends_call(default):
            return True
    return False


def _decorator_has_appliance_bearer_dep(deco: ast.Call) -> bool:
    """Scan a decorator's `dependencies=[Depends(...), ...]` kwarg.

    Closes Gate B v2 P1-A class — `@router.post(p, dependencies=[Depends(
    require_appliance_bearer)])` is a live FastAPI pattern (install_reports.py
    and install_telemetry.py use it with the install-token dep). A future
    appliance-bearer migration to this shape would escape the v1 gate."""
    for kw in deco.keywords:
        if kw.arg != "dependencies":
            continue
        if not isinstance(kw.value, ast.List):
            continue
        for elt in kw.value.elts:
            if _is_appliance_bearer_depends_call(elt):
                return True
    return False




def _extract_handlers(
    func: ast.AsyncFunctionDef | ast.FunctionDef,
    router_prefixes: dict[str, str],
    module_basename: str,
    registry: dict[tuple[str, str], str],
    is_main: bool,
) -> list[tuple[str, str, int, str]]:
    """For each state-changing decorator on `func`, return (method, full_path,
    lineno, deco_path). Skips GET/HEAD/OPTIONS. Resolves cross-file prefix.

    For non-main.py modules, ONLY emits handlers whose router-variable is
    registered in main.py via the (module_basename, var_name) registry.
    Closes the agent_api.py false-positive class (router exists but main.py
    never imports/includes it → handler unreachable → not a real silent-403)."""
    out: list[tuple[str, str, int, str]] = []
    func_level_dep = _function_has_appliance_bearer_dep(func)
    for deco in func.decorator_list:
        if not isinstance(deco, ast.Call):
            continue
        f = deco.func
        if not isinstance(f, ast.Attribute):
            continue
        method = f.attr.lower()
        if method not in _STATE_CHANGING_METHODS:
            continue
        owner = f.value
        if not isinstance(owner, ast.Name):
            continue
        owner_name = owner.id
        if not deco.args:
            continue
        first = deco.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        deco_path = first.value
        # Per-decorator dep check (Gate B v2 P1-A): the same function may
        # bear function-level Depends OR carry it via this decorator's
        # `dependencies=[Depends(...)]` kwarg.
        if not (func_level_dep or _decorator_has_appliance_bearer_dep(deco)):
            continue
        # Resolve full path + registration check:
        if owner_name == "app" and is_main:
            full = deco_path  # @app.post in main.py — absolute path
        else:
            # @<router>.post in a router module — must be registered in main.py
            key = (module_basename, owner_name)
            if key not in registry:
                # Dead code from main.py's perspective. Skip silently.
                continue
            include_prefix = registry[key]
            router_prefix = router_prefixes.get(owner_name, "")
            full = include_prefix + router_prefix + deco_path
        while "//" in full:
            full = full.replace("//", "/")
        out.append((method, full, deco.lineno, deco_path))
    return out


def _scan_file(path: pathlib.Path, registry: dict[tuple[str, str], str]):
    """Yield (path, method, full_path, lineno, handler_name, deco_path) tuples
    for every state-changing appliance-bearer handler in `path`."""
    try:
        src = path.read_text()
    except Exception:
        return
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    router_prefixes = _extract_router_prefixes(tree)
    module_basename = path.stem
    is_main = path.name == "main.py" and path.parent.name == "mcp-server"
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        # Per-decorator filter inside _extract_handlers picks up both
        # function-level and decorator-level appliance-bearer deps.
        for method, full_path, lineno, deco_path in _extract_handlers(
            node, router_prefixes, module_basename, registry, is_main,
        ):
            yield path, method, full_path, lineno, node.name, deco_path


# ----------------------------------------------------------------------
# THE GATE
# ----------------------------------------------------------------------

def test_csrf_exempt_paths_match_appliance_endpoints():
    """Every state-changing endpoint with `Depends(require_appliance_bearer)`
    MUST be covered by `csrf.py:EXEMPT_PATHS` or `EXEMPT_PREFIXES`.

    Closes the Session 210-B `/api/journal/upload` regression class +
    Session 220 zero-auth audit `/api/sites/{site_id}/...` 7-endpoint set.
    """
    paths, prefixes = _extract_csrf_exemptions()
    registry = _extract_registry()

    # Scan both the backend module dir and main.py (top-level mcp-server).
    scan_files: list[pathlib.Path] = list(_BACKEND.rglob("*.py"))
    scan_files.append(_MAIN_PY)

    violations: list[str] = []
    for path in scan_files:
        parts = set(path.parts)
        if parts & {"venv", ".venv", "node_modules", "__pycache__", "tests"}:
            continue
        for src_path, method, full_path, lineno, handler, deco_path in _scan_file(path, registry):
            if _is_exempt(full_path, paths, prefixes):
                continue
            if (method, full_path) in _KNOWN_BLOCKED_DEAD_ROUTES:
                # Tracked under task #120; gate role is to prevent growth.
                continue
            rel = src_path.relative_to(_REPO).as_posix()
            # Suggest the exact line to add to EXEMPT_PATHS.
            # If the path has a `{...}` template segment, suggest a prefix instead.
            if "{" in full_path:
                # Suggest EXEMPT_PREFIXES entry — strip everything from the first `{`.
                idx = full_path.index("{")
                suggested = full_path[:idx]
                hint = f'    Add to csrf.py EXEMPT_PREFIXES: "{suggested}",'
            else:
                hint = f'    Add to csrf.py EXEMPT_PATHS: "{full_path}",'
            violations.append(
                f"  {rel}:{lineno}  {method.upper()} {full_path}  "
                f"(handler: {handler}, decorator path: {deco_path!r})\n{hint}"
            )

    assert not violations, (
        "\n\nState-changing endpoints with `Depends(require_appliance_bearer)` "
        "MUST be in `csrf.py:EXEMPT_PATHS` or `EXEMPT_PREFIXES`. Otherwise "
        "every POST/PUT/PATCH/DELETE from the appliance gets a silent 403 "
        "from the CSRF middleware.\n\n"
        "Real-world bug class (Session 210-B + Session 220 zero-auth audit):\n"
        "  - /api/journal/upload — appliance journal batches got 403 for "
        "weeks; substrate invariant journal_upload_never_received fired.\n"
        "  - /api/sites/{site_id}/checkin etc — 7 endpoints in 2026-05-11 "
        "audit silently 403'd to appliance traffic.\n\n"
        "Missing exemptions:\n"
        + "\n".join(violations)
        + "\n\nSibling pattern: tests/test_no_middleware_dispatch_raises_"
        "httpexception.py (task #121, return-not-raise gate)."
    )


# ----------------------------------------------------------------------
# Positive control — synthetic missing exemption MUST be caught
# ----------------------------------------------------------------------

def test_extract_csrf_exemptions_parses_both_structures():
    """Sanity: csrf.py current EXEMPT_PATHS is non-empty and EXEMPT_PREFIXES
    tuple is non-empty. Prevents the gate from silently rotting if csrf.py
    is restructured and the AST extractor breaks."""
    paths, prefixes = _extract_csrf_exemptions()
    assert len(paths) >= 10, (
        f"EXEMPT_PATHS extraction broken — only {len(paths)} entries "
        f"parsed; expected >=10 (currently ~25 in csrf.py). Check AST shape."
    )
    assert len(prefixes) >= 10, (
        f"EXEMPT_PREFIXES extraction broken — only {len(prefixes)} entries "
        f"parsed; expected >=10 (currently ~35 in csrf.py). Check AST shape."
    )
    # Canonical known-good entries — guard against silent extractor drift.
    assert "/api/appliances/checkin" in paths, (
        "EXEMPT_PATHS missing canonical entry /api/appliances/checkin — "
        "extractor likely broken (Set vs Dict vs List literal mismatch)."
    )
    assert any(p == "/api/appliances/" for p in prefixes), (
        "EXEMPT_PREFIXES missing canonical prefix /api/appliances/ — "
        "extractor likely broken (Tuple vs List literal mismatch)."
    )


def test_is_exempt_membership_logic():
    """The membership check must be `path in PATHS or any(startswith(p))`.
    Closes Gate A P0-1: a v0 sketch that checked PATHS-only would
    false-positive every `/api/appliances/<sub>` endpoint."""
    paths = {"/api/exact"}
    prefixes = ("/api/prefix/",)
    assert _is_exempt("/api/exact", paths, prefixes)
    assert _is_exempt("/api/prefix/anything", paths, prefixes)
    assert _is_exempt("/api/prefix/deeply/nested", paths, prefixes)
    assert not _is_exempt("/api/other", paths, prefixes)
    assert not _is_exempt("/api/exact/sub", paths, prefixes), (
        "exact-match should NOT match sub-paths"
    )


def test_synthetic_handler_path_resolution():
    """End-to-end: synthetic AST with APIRouter + handler + Depends should
    resolve to the expected full path. Catches refactor-induced extractor
    regressions."""
    src = '''
from fastapi import APIRouter, Depends
router = APIRouter(prefix="/api/widgets")

@router.post("/{widget_id}/poke")
async def poke_widget(widget_id: str, _=Depends(require_appliance_bearer)):
    return {}

@router.get("/{widget_id}")
async def read_widget(widget_id: str, _=Depends(require_appliance_bearer)):
    return {}
'''
    tree = ast.parse(src)
    router_prefixes = _extract_router_prefixes(tree)
    assert router_prefixes == {"router": "/api/widgets"}, router_prefixes

    # Pretend main.py registered widgets-module's `router` with no prefix.
    fake_registry = {("widgets_module", "router"): ""}
    handlers: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            for method, full, _ln, _dp in _extract_handlers(
                node, router_prefixes, "widgets_module", fake_registry, is_main=False,
            ):
                handlers.append((method, full))

    # GET handler MUST be skipped (CSRF safe method).
    assert ("post", "/api/widgets/{widget_id}/poke") in handlers, handlers
    assert not any(m == "get" for m, _p in handlers), (
        f"GET handlers must be skipped; got {handlers!r}"
    )


def test_synthetic_require_appliance_bearer_full_variant_detected():
    """Gate B P0-A regression: the `_full` variant of the dep is a strict
    superset of `require_appliance_bearer` (shared.py:571). The gate v1
    hardcoded only the base name and missed 6 real callsites including
    journal_api.py:74 — the literal Session 210-B regression file. Both
    names MUST be detected."""
    src = '''
from fastapi import APIRouter, Depends
router = APIRouter(prefix="/api/journal")

@router.post("/upload")
async def upload_journal(_=Depends(require_appliance_bearer_full)):
    return {}
'''
    tree = ast.parse(src)
    router_prefixes = _extract_router_prefixes(tree)
    fake_registry = {("journal_module", "router"): ""}
    handlers: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            for method, full, _ln, _dp in _extract_handlers(
                node, router_prefixes, "journal_module", fake_registry, is_main=False,
            ):
                handlers.append((method, full))
    assert ("post", "/api/journal/upload") in handlers, (
        f"`require_appliance_bearer_full` dep must be detected; got {handlers!r}"
    )


def test_synthetic_dependencies_kwarg_shape_detected():
    """Gate B v2 P1-A regression (task #124): the `@router.post(p,
    dependencies=[Depends(require_appliance_bearer)])` decorator-kwarg
    shape is a live FastAPI pattern (install_reports.py + install_telemetry.py
    use it with install-token deps). The gate MUST detect it for appliance-
    bearer dep — otherwise a future appliance migration to this shape
    silently escapes the CSRF parity check."""
    src = '''
from fastapi import APIRouter, Depends
router = APIRouter(prefix="/api/widgets")

@router.post("/{widget_id}/poke", dependencies=[Depends(require_appliance_bearer)])
async def poke_widget(widget_id: str):
    return {}

@router.post("/{widget_id}/full-poke", dependencies=[Depends(require_appliance_bearer_full)])
async def full_poke_widget(widget_id: str):
    return {}

@router.post("/{widget_id}/auth-only", dependencies=[Depends(require_auth)])
async def auth_only_widget(widget_id: str):
    return {}
'''
    tree = ast.parse(src)
    router_prefixes = _extract_router_prefixes(tree)
    fake_registry = {("widgets_module", "router"): ""}
    handlers: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            for method, full, _ln, _dp in _extract_handlers(
                node, router_prefixes, "widgets_module", fake_registry, is_main=False,
            ):
                handlers.append((method, full))
    assert ("post", "/api/widgets/{widget_id}/poke") in handlers, (
        f"decorator-kwarg shape with require_appliance_bearer must be detected; "
        f"got {handlers!r}"
    )
    assert ("post", "/api/widgets/{widget_id}/full-poke") in handlers, (
        f"decorator-kwarg shape with _full variant must be detected; "
        f"got {handlers!r}"
    )
    assert ("post", "/api/widgets/{widget_id}/auth-only") not in handlers, (
        f"non-bearer dep (require_auth) MUST NOT be flagged; got {handlers!r}"
    )
