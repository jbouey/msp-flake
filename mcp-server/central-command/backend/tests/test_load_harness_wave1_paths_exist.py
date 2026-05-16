"""CI gate (Task #62 v2.1 Commit 1, Gate A P0-1): assert every load-
harness Wave-1 endpoint path declared in the v2.1 spec resolves to a
real `@router.<method>` decorator in the backend tree.

Gate A finding (audit/coach-62-load-harness-v1-gate-a-2026-05-16.md
§P0-1): the v1 design listed two endpoints whose paths didn't match
the real route table — `/api/appliances/order` and `/api/evidence/
sites/{id}/submit`. A k6 script written against v1 would 404 on 2 of
5 scenarios.

v2.1 fix: pin Wave 1 to 4 endpoints (P0-2 dropped /evidence/upload)
and CI-gate that each path's real `@router` decorator exists. Prevents
silent drift if a backend route is renamed but the spec isn't
updated.

This is a SOURCE-SHAPE gate (no DB, no runtime). Wave 1 list is
embedded here as the canonical truth — when the spec doc bumps to
v2.2 (P1-1 endpoint expansion), THIS file bumps in lockstep.
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent


# Wave 1 endpoints per v2.1 spec
# (.agent/plans/40-load-testing-harness-design-v2.1-2026-05-16.md).
# Each entry: (method, path, expected source file glob, expected auth
# dep).
_WAVE1_ENDPOINTS = [
    # method, path, expected-source-file (relative to backend), required-auth-dep
    ("POST", "/api/appliances/checkin", "agent_api.py", "require_appliance_bearer"),
    ("GET", "/api/appliances/orders/", "agent_api.py", "require_appliance_bearer"),  # path has {site_id} suffix; prefix-match
    ("POST", "/api/journal/upload", "journal_api.py", "require_appliance_bearer"),
    ("GET", "/health", "main.py", None),  # no auth, baseline
]


def _scan_router_decorators(src: str) -> list[tuple[str, str, str]]:
    """Extract (method, path, router_var) tuples from `@router.<method>
    ("<path>")` decorator lines."""
    pat = re.compile(
        r"@([a-zA-Z_]+_router|router|app|auth_router)\.(get|post|put|patch|delete)\(\s*[\"']([^\"']+)[\"']",
        re.IGNORECASE,
    )
    return [
        (m.group(2).upper(), m.group(3), m.group(1))
        for m in pat.finditer(src)
    ]


def _scan_router_prefix(src: str, router_var: str) -> str:
    """Extract the `prefix=` from `<router_var> = APIRouter(prefix=...)`
    if present. Returns '' if no prefix declared."""
    pat = re.compile(
        rf"\b{re.escape(router_var)}\s*=\s*APIRouter\([^)]*prefix\s*=\s*[\"']([^\"']+)[\"']",
        re.IGNORECASE | re.DOTALL,
    )
    m = pat.search(src)
    return m.group(1) if m else ""


def _all_backend_routes() -> list[tuple[str, str, str]]:
    """Return (method, full_path, file_relpath) tuples for every backend
    .py file with @router decorators. full_path combines the
    APIRouter(prefix=...) + the decorator path literal."""
    result: list[tuple[str, str, str]] = []
    search_paths = [_BACKEND] + [
        p.parent for p in _BACKEND.rglob("main.py") if "tests" not in p.parts and "venv" not in p.parts
    ]
    # Also include the parent mcp-server dir (where the FastAPI app lives)
    mcp_server = _BACKEND.parent  # central-command/
    if (mcp_server.parent / "main.py").exists():
        # Special case: mcp-server/main.py at the root of mcp-server/
        result.extend(_scan_file(mcp_server.parent / "main.py"))
    for py in _BACKEND.glob("*.py"):
        if py.name.startswith("test_"):
            continue
        result.extend(_scan_file(py))
    return result


def _scan_file(py: pathlib.Path) -> list[tuple[str, str, str]]:
    try:
        src = py.read_text(encoding="utf-8")
    except OSError:
        return []
    routes = _scan_router_decorators(src)
    out: list[tuple[str, str, str]] = []
    for method, path, router_var in routes:
        if router_var == "app":
            # @app.get("/health") — full path as declared
            full = path
        else:
            prefix = _scan_router_prefix(src, router_var)
            full = prefix + path
        out.append((method, full, py.name))
    return out


def _find_path_in_routes(
    target_method: str, target_path: str, expected_file: str,
    routes: list[tuple[str, str, str]],
) -> tuple[bool, str]:
    """Look for the target endpoint. Path matching:
      - exact match
      - target is a prefix of the declared path
      - declared-path tail-segment matches target tail-segment
        (FastAPI APIRouter prefix is composed at
        `app.include_router(prefix=...)` time OR the
        `APIRouter(prefix=...)` line in the router module; the bare
        `@router.<method>(...)` decorator may only carry the tail
        segment(s) like `/orders/{site_id}` or `/upload`)
    """
    target_tail = target_path.rstrip("/")
    # Extract the last segment(s) of the target — for matching against
    # decorators that only carry the tail (e.g., target
    # '/api/appliances/orders/' → tail-segment '/orders')
    target_last_segs = "/" + target_tail.split("/")[-1] if target_tail.count("/") > 1 else target_tail
    for method, full_path, file_name in routes:
        if method != target_method:
            continue
        # Strategy 1 — full-path exact / prefix
        if full_path == target_path or full_path.startswith(target_path):
            return True, f"{file_name}: @{method.lower()}({full_path!r})"
        # Strategy 2 — declared path starts with the target tail segment
        # AND the file_name matches the expected_file hint (the spec's
        # expected_file column anchors this to prevent false-positives
        # across unrelated routers)
        if file_name == expected_file and full_path.startswith(target_last_segs):
            return True, f"{file_name}: @{method.lower()}({full_path!r}) [tail-segment match]"
    return False, ""


def test_every_wave1_endpoint_resolves_to_real_router_decorator():
    """Every endpoint in `_WAVE1_ENDPOINTS` must have a real
    `@router.<method>("<path>")` decorator somewhere in the backend
    tree. Catches the v1 Gate A P0-1 class of drift."""
    routes = _all_backend_routes()
    missing: list[str] = []

    for method, path, expected_file, auth_dep in _WAVE1_ENDPOINTS:
        found, where = _find_path_in_routes(method, path, expected_file, routes)
        if not found:
            # Show 3 nearest matches to aid debugging
            method_matches = [
                f"{m} {p} ({f})" for m, p, f in routes if m == method
                and (path.split('/')[-1].split('{')[0] in p or p.endswith(path.rsplit('/', 1)[-1]))
            ][:3]
            hint = (
                "\n      Near-matches: " + "; ".join(method_matches)
                if method_matches else ""
            )
            missing.append(
                f"  - {method} {path}: NO matching @router decorator found "
                f"in backend tree (expected near {expected_file}){hint}"
            )

    assert not missing, (
        "Load harness v2.1 Wave-1 endpoints don't resolve to real routes — "
        "either the route was renamed (update the spec + this gate's "
        "_WAVE1_ENDPOINTS table) OR the endpoint never existed (Gate A "
        "P0-1 class — update v2.1 spec doc + this gate together):\n"
        + "\n".join(missing)
    )


def test_wave1_endpoint_count_is_pinned():
    """v2.1 spec pins Wave 1 to exactly 4 endpoints (P0-2 dropped
    /evidence/upload; P1-1 expansion is followup task #105). Bumping
    this requires bumping the spec doc + this gate together — prevents
    silent scope creep."""
    assert len(_WAVE1_ENDPOINTS) == 4, (
        f"v2.1 Wave-1 endpoint count = {len(_WAVE1_ENDPOINTS)}, "
        f"expected 4. If P1-1 endpoint expansion (followup #105) is "
        f"shipping, bump v2.1 → v2.2 doc + this gate's count in "
        f"lockstep."
    )
