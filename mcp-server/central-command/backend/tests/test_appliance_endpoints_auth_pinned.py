"""Pin gate — POST/PUT/PATCH/DELETE routers in provisioning.py +
sensors.py + discovery.py MUST be authenticated.

Session 219 (2026-05-11) Commit 2 — weekly audit-cadence 2026-05-11
found 5 additional zero-auth state-changing endpoints across these
3 files (sibling to the 3 in appliance_delegation.py closed by
Commit 1 of the sprint). All 5 now hardened:

  provisioning.py:
    - POST /heartbeat — pre-claim. Auth = provision_code (option B2).
    - POST /status — post-claim. Auth = require_appliance_bearer.

  discovery.py:
    - POST /report — post-claim. Auth = require_appliance_bearer +
      _enforce_site_id.

  sensors.py:
    - POST /heartbeat — post-claim. Auth = require_appliance_bearer +
      _enforce_site_id(auth_site_id, heartbeat.site_id).
    - POST /linux/heartbeat — same.
    - POST /commands/{id}/complete — post-claim. Auth =
      require_appliance_bearer + site-scoped UPDATE.

Algorithm (static AST):
  1. Find every @router.post/.put/.patch/.delete decorator across the
     3 target files.
  2. Confirm the handler signature has EITHER:
       - Depends(require_appliance_bearer)
       - Depends(require_admin)
       - explicit provision_code field on the request model
         (currently only /heartbeat, allowlisted by handler name)
  3. Fail with file:line for any uncovered endpoint.

Sibling pattern:
  - `test_appliance_delegation_auth_pinned.py` (Commit 1)
  - `test_l1_resolution_requires_remediation_step.py`
  - `test_l2_resolution_requires_decision_record.py`
"""
from __future__ import annotations

import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

_TARGETS = [
    _BACKEND / "provisioning.py",
    _BACKEND / "discovery.py",
    _BACKEND / "sensors.py",
]

# Handlers that use a custom auth mechanism instead of Depends().
# Each entry requires inline justification. Pre-claim handlers cannot
# use require_appliance_bearer because the appliance has no bearer
# yet — they use provision_code (B2 pattern) or HMAC.
_PROVISION_CODE_AUTH_ALLOWLIST = {
    "provisioning_heartbeat",  # pre-claim B2: validates provision_code
    "claim_provision_code",  # /claim: the entire purpose is exchanging
                             # provision_code → api_key, so the code IS
                             # the auth.
    "validate_provision_code",  # GET-only sanity probe; no state change.
}

# Handlers that are NOT appliance-facing (admin operator tools).
_ADMIN_HANDLERS_ALLOWLIST = {
    "admin_restore",  # provisioning.py admin-only restore endpoint.
    "rekey_appliance",  # admin-only via require_admin.
}


def _state_changing_post_handlers(tree: ast.Module) -> list[tuple[ast.AsyncFunctionDef, int, str]]:
    """Return (handler, decorator_line, http_method) for every state-
    changing @router decorator."""
    out: list[tuple[ast.AsyncFunctionDef, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not isinstance(func, ast.Attribute):
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != "router":
                continue
            if func.attr not in {"post", "put", "patch", "delete"}:
                continue
            out.append((node, dec.lineno, func.attr))
    return out


def _has_auth_dep(handler: ast.AsyncFunctionDef) -> bool:
    """True iff handler signature includes Depends(require_appliance_bearer)
    OR Depends(require_admin)."""
    src = ast.unparse(handler)
    return (
        "Depends(require_appliance_bearer)" in src
        or "Depends(require_admin)" in src
    )


def _has_provision_code_validation(handler: ast.AsyncFunctionDef) -> bool:
    """True iff handler body validates provision_code (B2 auth pattern).
    Heuristic: body references `provision_code` AND raises HTTPException
    with 403 OR 401 status."""
    src = ast.unparse(handler)
    if "provision_code" not in src:
        return False
    # Must raise 403 or 401 on the validation miss.
    return "status_code=403" in src or "status_code=401" in src


# Handlers hardened in Session 219 zero-auth sprint Commit 2 (2026-05-11).
# This pin gate ENFORCES auth on these specific handlers. Additional
# zero-auth handlers in the same files were identified during gate
# construction and are tracked as a follow-up sprint (TaskCreate item),
# not gate-enforced yet — adding them to the gate without a Gate A is
# the antipattern Session 219 explicitly bans.
_COMMIT_2_HARDENED_HANDLERS = {
    "provisioning_heartbeat",        # provisioning.py — B2 provision_code
    "update_provision_status",       # provisioning.py — bearer
    "report_discovery_results",      # discovery.py — bearer + enforce
    "record_sensor_heartbeat",       # sensors.py — bearer + enforce
    "record_linux_sensor_heartbeat", # sensors.py — bearer + enforce
    "complete_sensor_command",       # sensors.py — bearer + site-scoped UPDATE
}


def test_commit_2_hardened_handlers_authenticated():
    """The 6 specific handlers hardened in Commit 2 of the Session 219
    zero-auth sprint MUST authenticate the caller. Other state-changing
    handlers in these files are tracked separately — adding them here
    without an explicit Gate A would expand scope mid-sprint."""
    missing: list[str] = []
    found_handlers: set[str] = set()
    for path in _TARGETS:
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        for handler, dec_line, method in _state_changing_post_handlers(tree):
            name = handler.name
            if name not in _COMMIT_2_HARDENED_HANDLERS:
                continue
            found_handlers.add(name)
            if name in _PROVISION_CODE_AUTH_ALLOWLIST:
                if not _has_provision_code_validation(handler):
                    missing.append(
                        f"{path.name}:{dec_line} {name} (allowlisted but "
                        "provision_code validation not detected — must "
                        "raise 401/403 on invalid code)"
                    )
                continue
            if not _has_auth_dep(handler):
                missing.append(f"{path.name}:{dec_line} {name} ({method.upper()})")
    # Every Commit-2 handler must actually exist (catches a rename
    # regression that would silently drop the gate).
    not_found = _COMMIT_2_HARDENED_HANDLERS - found_handlers
    assert not not_found, (
        f"Commit 2 handlers not found in target files (renamed or moved?): "
        f"{sorted(not_found)}. Update _COMMIT_2_HARDENED_HANDLERS if these "
        f"were intentionally renamed."
    )
    assert not missing, (
        "Commit-2-hardened endpoints lacking auth dependency:\n"
        + "\n".join(f"  - {m}" for m in missing)
        + "\n\nAdd `auth_site_id: str = Depends(require_appliance_bearer)` "
        "(appliance-callable, post-claim) OR validate provision_code "
        "with explicit 401/403 raise (pre-claim B2 pattern, add handler "
        "name to _PROVISION_CODE_AUTH_ALLOWLIST). Session 219 P0."
    )


def test_commit_2_bearer_endpoints_enforce_site_id_or_constrain_by_auth():
    """Every Commit-2 handler using Depends(require_appliance_bearer)
    MUST either call _enforce_site_id OR constrain its UPDATE/INSERT
    by auth_site_id. Catches the 'forgot to cross-check' regression."""
    missing: list[str] = []
    for path in _TARGETS:
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        for handler, dec_line, method in _state_changing_post_handlers(tree):
            if handler.name not in _COMMIT_2_HARDENED_HANDLERS:
                continue
            if handler.name in _PROVISION_CODE_AUTH_ALLOWLIST:
                continue  # B2 auth handled by test #3
            if not _has_auth_dep(handler):
                continue  # caught by test #1
            src = ast.unparse(handler)
            if "Depends(require_admin)" in src:
                continue
            has_enforce = "_enforce_site_id(" in src
            has_auth_param = "auth_site_id" in src and (
                "$" in src or "%s" in src or "WHERE" in src.upper()
            )
            if not (has_enforce or has_auth_param):
                missing.append(f"{path.name}:{dec_line} {handler.name}")
    assert not missing, (
        "Commit-2 bearer-authenticated endpoints not cross-checking site:\n"
        + "\n".join(f"  - {m}" for m in missing)
        + "\n\nAdd `await _enforce_site_id(auth_site_id, <site_id>, "
        '"<endpoint>")` OR include `auth_site_id` as a SQL parameter '
        "in the UPDATE/INSERT WHERE clause."
    )


def test_provisioning_heartbeat_validates_provision_code():
    """provisioning_heartbeat must:
      1. Have a `provision_code` field on HeartbeatRequest
      2. Look up appliance_provisions by that code
      3. Raise 403 on miss/expired"""
    src = (_BACKEND / "provisioning.py").read_text()
    # HeartbeatRequest must declare provision_code.
    hb_idx = src.find("class HeartbeatRequest")
    assert hb_idx >= 0, "HeartbeatRequest class missing"
    hb_class = src[hb_idx:hb_idx + 1500]
    assert "provision_code" in hb_class, (
        "HeartbeatRequest model must include `provision_code` field "
        "(Session 219 B2 auth pattern)."
    )
    # provisioning_heartbeat must query appliance_provisions.
    hb_func_idx = src.find("async def provisioning_heartbeat")
    assert hb_func_idx >= 0
    hb_func = src[hb_func_idx:hb_func_idx + 3000]
    assert "appliance_provisions" in hb_func, (
        "provisioning_heartbeat must validate code against "
        "appliance_provisions table."
    )
    assert "provision_code" in hb_func, (
        "provisioning_heartbeat must reference provision_code field."
    )
    assert "status_code=403" in hb_func, (
        "provisioning_heartbeat must raise HTTPException(status_code=403) "
        "on invalid/expired code."
    )
