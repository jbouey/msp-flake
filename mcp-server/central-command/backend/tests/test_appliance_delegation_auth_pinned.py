"""Pin gate — every router in `appliance_delegation.py` MUST require
`require_appliance_bearer` + call `_enforce_site_id` against the request
body's site_id (or per-entry site_id for batch endpoints).

Session 219 (2026-05-11) — weekly audit found 3 endpoints unauthenticated:
  - POST /api/appliances/{appliance_id}/delegate-key (privileged-chain class)
  - POST /api/appliances/{appliance_id}/audit-trail
  - POST /api/appliances/{appliance_id}/urgent-escalations

Pre-fix the audit found exactly 1 row in delegated_keys (synthetic test
data, already expired). Post-fix this gate prevents any new
@router.post handler in appliance_delegation.py from shipping without
the bearer + spoof check.

Algorithm (static AST + source walk):
  1. Find every @router.post/.put/.patch/.delete decorator.
  2. Confirm the handler signature includes a parameter typed
     `Depends(require_appliance_bearer)`.
  3. Confirm the function body calls `_enforce_site_id(...)` against
     a request site_id field within the first 20 lines.

Allowed exceptions via `# l1-auth-allowed: <reason>` opt-out comment
on the @router decorator line.

Sibling pattern:
  - `test_l2_resolution_requires_decision_record.py`
  - `test_l1_resolution_requires_remediation_step.py`
  - `test_privileged_chain_allowed_events_lockstep.py`
"""
from __future__ import annotations

import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO = _BACKEND.parent.parent.parent

_TARGET = _BACKEND / "appliance_delegation.py"


def _state_changing_post_handlers(tree: ast.Module) -> list[tuple[ast.AsyncFunctionDef, int]]:
    """Return (handler, decorator_line) for every @router.post/put/patch/delete handler."""
    out: list[tuple[ast.AsyncFunctionDef, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for dec in node.decorator_list:
            # @router.post("...") / @router.put / .patch / .delete
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not isinstance(func, ast.Attribute):
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != "router":
                continue
            if func.attr not in {"post", "put", "patch", "delete"}:
                continue
            out.append((node, dec.lineno))
    return out


def _handler_has_appliance_bearer_dep(handler: ast.AsyncFunctionDef) -> bool:
    """Return True iff the handler's signature includes a
    `Depends(require_appliance_bearer)` or `Depends(require_admin)`
    parameter. require_admin is acceptable for admin-only management
    routes (revoke-key, list-keys etc.) — appliance-callable routes
    MUST use require_appliance_bearer."""
    for arg in handler.args.args + handler.args.kwonlyargs:
        if arg.annotation is None:
            continue
        ann = ast.unparse(arg.annotation) if hasattr(ast, "unparse") else ""
        if "require_appliance_bearer" in ann or "require_admin" in ann:
            return True
    # Also check defaults — `auth_site_id: str = Depends(require_appliance_bearer)`
    src = ast.unparse(handler)
    if "Depends(require_appliance_bearer)" in src or "Depends(require_admin)" in src:
        return True
    return False


def _handler_enforces_site_id(handler: ast.AsyncFunctionDef) -> bool:
    """Return True iff the handler body contains an `_enforce_site_id(`
    call. (Body may have many lines — checked across the full unparsed
    source, NOT a line-limited window, because batch endpoints loop
    through entries before calling _enforce_site_id.)"""
    src = ast.unparse(handler) if hasattr(ast, "unparse") else ""
    return "_enforce_site_id(" in src


def _is_admin_only_route(handler: ast.AsyncFunctionDef) -> bool:
    """require_admin routes are operator-management endpoints (not
    appliance-callable) and don't need _enforce_site_id — the admin
    bearer ALREADY authorizes cross-site action by design."""
    src = ast.unparse(handler) if hasattr(ast, "unparse") else ""
    return "Depends(require_admin)" in src


def test_every_post_endpoint_requires_appliance_bearer():
    """Every state-changing endpoint in appliance_delegation.py MUST
    either declare `Depends(require_appliance_bearer)` (appliance-callable)
    or `Depends(require_admin)` (operator-management)."""
    tree = ast.parse(_TARGET.read_text())
    handlers = _state_changing_post_handlers(tree)
    assert handlers, "no @router.post handlers found — gate is broken"
    missing: list[str] = []
    for handler, dec_line in handlers:
        if not _handler_has_appliance_bearer_dep(handler):
            missing.append(f"appliance_delegation.py:{dec_line} {handler.name}")
    assert not missing, (
        "state-changing endpoints lacking auth dependency:\n"
        + "\n".join(f"  - {m}" for m in missing)
        + "\n\nAdd `auth_site_id: str = Depends(require_appliance_bearer)` "
        "(appliance-callable) or `_: dict = Depends(require_admin)` "
        "(operator-management) to the handler signature. Session 219 "
        "weekly audit 2026-05-11 P0."
    )


def test_appliance_bearer_endpoints_enforce_site_id():
    """Every endpoint using `Depends(require_appliance_bearer)` MUST
    call `_enforce_site_id(...)` somewhere in the body. Catches the
    'forgot to cross-check' regression — without the enforce call, the
    bearer authorizes site-A but the request body could still target
    site-B."""
    tree = ast.parse(_TARGET.read_text())
    handlers = _state_changing_post_handlers(tree)
    missing: list[str] = []
    for handler, dec_line in handlers:
        if _is_admin_only_route(handler):
            continue  # admin bypass is intentional
        if not _handler_has_appliance_bearer_dep(handler):
            continue  # caught by test #1
        if not _handler_enforces_site_id(handler):
            missing.append(f"appliance_delegation.py:{dec_line} {handler.name}")
    assert not missing, (
        "appliance-bearer endpoints lacking `_enforce_site_id(...)` call:\n"
        + "\n".join(f"  - {m}" for m in missing)
        + "\n\nAdd `await _enforce_site_id(auth_site_id, <request.site_id>, "
        '"<endpoint_name>")` early in the handler body (or per-entry for '
        "batch endpoints)."
    )


def test_delegate_signing_key_uses_auth_site_id_for_inserts():
    """The signed `delegation_data` dict + the `INSERT INTO delegated_keys`
    statement MUST both bind to `auth_site_id` (bearer-authenticated),
    NOT `request.site_id` (caller-supplied). Load-bearing per Gate A
    P0-4 — using the caller-supplied value makes the auth gate a no-op."""
    src = _TARGET.read_text()
    # Find the delegate_signing_key function body bounds.
    tree = ast.parse(src)
    func = None
    for node in ast.walk(tree):
        if (isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
                and node.name == "delegate_signing_key"):
            func = node
            break
    assert func is not None, "delegate_signing_key not found"
    body_src = ast.unparse(func)
    # Both the delegation_data dict literal AND the INSERT statement
    # must reference auth_site_id, NOT request.site_id.
    assert '"site_id": auth_site_id' in body_src or "'site_id': auth_site_id" in body_src, (
        "delegation_data dict must bind site_id to auth_site_id, not "
        "request.site_id (Gate A P0-4)."
    )
    # The INSERT statement uses positional args; check the args list
    # references auth_site_id.
    insert_idx = body_src.find("INSERT INTO delegated_keys")
    assert insert_idx >= 0, "INSERT INTO delegated_keys statement missing"
    # Find the args block after the INSERT — it should contain auth_site_id.
    args_block = body_src[insert_idx:insert_idx + 2000]
    assert "auth_site_id" in args_block, (
        "INSERT INTO delegated_keys arg list must reference auth_site_id, "
        "not request.site_id (Gate A P0-4)."
    )


def test_delegate_signing_key_in_privileged_order_types():
    """`delegate_signing_key` must be in the privileged-chain three-list.
    Verified by importing fleet_cli + privileged_access_attestation.
    Migration 305 is the third list (cannot static-import; the
    lockstep checker script handles the SQL side)."""
    import sys
    sys.path.insert(0, str(_BACKEND))
    # Read source directly to avoid import-chain issues.
    fleet_cli_src = (_BACKEND / "fleet_cli.py").read_text()
    attestation_src = (_BACKEND / "privileged_access_attestation.py").read_text()
    assert '"delegate_signing_key"' in fleet_cli_src, (
        "delegate_signing_key MUST be in fleet_cli.PRIVILEGED_ORDER_TYPES "
        "(Gate A P0-1 — privileged-chain class registration)."
    )
    assert '"delegate_signing_key"' in attestation_src, (
        "delegate_signing_key MUST be in "
        "privileged_access_attestation.ALLOWED_EVENTS (Gate A P0-1)."
    )
    mig_path = _BACKEND / "migrations" / "305_delegate_signing_key_privileged.sql"
    assert mig_path.exists(), (
        "Migration 305_delegate_signing_key_privileged.sql MUST exist "
        "(Gate A P0-1 — third list of the lockstep trio)."
    )
    assert "'delegate_signing_key'" in mig_path.read_text(), (
        "Migration 305 must include delegate_signing_key in v_privileged_types."
    )
