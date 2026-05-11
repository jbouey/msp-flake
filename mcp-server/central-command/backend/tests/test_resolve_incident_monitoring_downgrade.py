"""Pin gate — `main.py:/incidents/resolve` MUST downgrade `L1` to
`'monitoring'` for monitoring-only check_types AND enforce site_id.

Session 219 Phase 3 PR-3b (2026-05-11) Layer 2 defensive gate. The
appliance daemon hardcodes `"L1"` in `ReportHealed` even for
`Action: "escalate"` rules — 1,137 historical orphans on chaos-lab.
Layer 1 daemon fix lands in PR-3a after this commit; Layer 2 ships
FIRST as the safety net for the daemon-rollout window (hours/days).

Gate A v3 P0 + P1 fixes pinned by this test:
- P0-1: patch ONLY main.py (agent_api.py:1613 is dead)
- P0-2: use cached MONITORING_ONLY_CHECKS module-global, NOT
  `load_monitoring_only_from_registry` per-request
- P1-1: `_enforce_site_id` called (closes pre-existing C1 gap)

Algorithm (static AST + source walk):
  1. Find `resolve_incident_by_type` in main.py.
  2. Confirm body contains `_enforce_site_id(auth_site_id, site_id,`
  3. Confirm body contains `MONITORING_ONLY_CHECKS` reference (cached
     module-global, not the loader function).
  4. Confirm body contains `resolution_tier = "monitoring"` downgrade.
  5. Confirm body does NOT call `load_monitoring_only_from_registry`
     (per-request reload storm forbidden by P0-2).
"""
from __future__ import annotations

import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
# main.py lives at mcp-server/main.py (parent of central-command/), not repo root.
_MCP_SERVER = _BACKEND.parent.parent
_MAIN = _MCP_SERVER / "main.py"


def _find_func(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    return None


def test_resolve_incident_by_type_calls_enforce_site_id():
    """P1-1 (Gate A v3): close the C1 cross-site spoof gap. The dead
    agent_api.py twin enforced site_id; the live main.py handler did
    not. Phase 3 PR-3b adds the gate."""
    tree = ast.parse(_MAIN.read_text())
    func = _find_func(tree, "resolve_incident_by_type")
    assert func is not None, "resolve_incident_by_type missing from main.py"
    src = ast.unparse(func)
    assert "_enforce_site_id(auth_site_id, site_id," in src, (
        "resolve_incident_by_type MUST call `_enforce_site_id(auth_site_id, "
        "site_id, ...)` to close the C1 cross-site spoof gap. "
        "Gate A v3 P1-1 (Session 219 Phase 3)."
    )


def test_resolve_incident_by_type_downgrades_monitoring_only_l1():
    """Layer 2 defensive gate (Session 219 Phase 3 PR-3b). The handler
    MUST downgrade `resolution_tier='L1'` to `'monitoring'` when the
    check_type is in MONITORING_ONLY_CHECKS — catches the daemon's
    hardcoded-L1-on-escalate-action class structurally on the
    backend, ahead of the async daemon-rollout window."""
    tree = ast.parse(_MAIN.read_text())
    func = _find_func(tree, "resolve_incident_by_type")
    assert func is not None
    src = ast.unparse(func)
    # Must reference the cached module-global.
    assert "MONITORING_ONLY_CHECKS" in src, (
        "Handler must reference cached MONITORING_ONLY_CHECKS module-"
        "global for the L1→monitoring downgrade gate."
    )
    # Must do the downgrade.
    assert (
        "resolution_tier = 'monitoring'" in src
        or 'resolution_tier = "monitoring"' in src
    ), (
        "Handler must downgrade `resolution_tier` to 'monitoring' when "
        "check_type is in MONITORING_ONLY_CHECKS. Session 219 Phase 3 "
        "Layer 2 defensive gate."
    )


def test_resolve_incident_does_not_call_registry_loader_per_request():
    """P0-2 (Gate A v3): the registry loader REWRITES the global set
    + DB roundtrip — calling it per-request creates a reload storm
    + race conditions on the module-global under concurrent requests.
    The cached MONITORING_ONLY_CHECKS set (populated at lifespan
    startup) is the correct hot-path source."""
    tree = ast.parse(_MAIN.read_text())
    func = _find_func(tree, "resolve_incident_by_type")
    assert func is not None
    src = ast.unparse(func)
    assert "load_monitoring_only_from_registry" not in src, (
        "resolve_incident_by_type MUST NOT call "
        "load_monitoring_only_from_registry on the hot path — it "
        "rewrites the module-global + DB roundtrip. Use the cached "
        "MONITORING_ONLY_CHECKS set directly. Gate A v3 P0-2."
    )


def test_resolve_incident_handler_uses_require_appliance_bearer():
    """Sanity: the endpoint is authenticated. Pre-existing requirement
    but worth pinning so a future refactor doesn't accidentally drop
    the bearer dependency."""
    tree = ast.parse(_MAIN.read_text())
    func = _find_func(tree, "resolve_incident_by_type")
    assert func is not None
    src = ast.unparse(func)
    assert "Depends(require_appliance_bearer)" in src, (
        "resolve_incident_by_type must use Depends(require_appliance_bearer)."
    )
