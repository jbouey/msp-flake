"""CI gate: every partner-side mutation MUST use require_partner_role(...)
not bare Depends(require_partner).

Round-table 31 (2026-05-05) closure of audit P1 finding: 7 site-mutating
endpoints in partners.py shipped with `Depends(require_partner)` (any
role: admin/tech/billing). Billing-role partner_user could rotate site
credentials, delete creds, set/cancel maintenance, trigger checkin,
mutate assets — confidentiality + integrity break.

This gate AST-walks partners.py and asserts every @router.{post,put,
patch,delete} on a `/me/...` path uses require_partner_role(...) NOT
bare Depends(require_partner).

GET endpoints are exempt — they may legitimately be readable by all
roles. Per-user actions like "mark my notification read" are also
allowlisted because they affect only the caller's own data.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_PARTNERS_PY = _BACKEND / "partners.py"

# Endpoints whose mutation is per-user (touches only the caller's own
# row) and is legitimately reachable by any authenticated partner role.
# Add with explicit justification.
PER_USER_MUTATION_ALLOWLIST = {
    # PUT /me/notifications/{id}/read — partner_user marks their own
    # notification as read; doesn't touch site state or other users.
    "mark_partner_notification_read",
}


def _is_router_mutation(decorator: ast.Call) -> tuple[bool, str, str]:
    """Returns (is_mutation, http_method, path) for @router.{post|put|patch|delete}.
    is_mutation=False on GET or non-router decorators."""
    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return False, "", ""
    if not (isinstance(func.value, ast.Name) and func.value.id == "router"):
        return False, "", ""
    method = func.attr
    if method not in ("post", "put", "patch", "delete"):
        return False, "", ""
    path = ""
    if decorator.args and isinstance(decorator.args[0], ast.Constant):
        path = str(decorator.args[0].value)
    return True, method.upper(), path


def _find_partner_dependency(node: ast.AsyncFunctionDef) -> tuple[bool, bool]:
    """Returns (uses_require_partner_bare, uses_require_partner_role)."""
    bare = False
    role = False
    # Check defaults attached to args + kwonlyargs
    all_defaults = list(node.args.defaults) + list(node.args.kw_defaults or [])
    for default in all_defaults:
        if default is None:
            continue
        if isinstance(default, ast.Call):
            fn = default.func
            # Pattern: Depends(require_partner)
            if isinstance(fn, ast.Name) and fn.id == "Depends":
                if (default.args
                        and isinstance(default.args[0], ast.Name)
                        and default.args[0].id == "require_partner"):
                    bare = True
            # Pattern: require_partner_role("admin", ...)
            elif isinstance(fn, ast.Name) and fn.id == "require_partner_role":
                role = True
    return bare, role


def test_no_partner_mutation_uses_bare_require_partner():
    """Every @router.{post,put,patch,delete} on a /me/... endpoint
    must use require_partner_role(...) not bare Depends(require_partner).

    Pre-fix: 7 site-mutating endpoints leaked admin-class actions to
    billing/tech roles. CI gate ensures the regression class is closed.
    """
    src = _PARTNERS_PY.read_text()
    tree = ast.parse(src)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        # Skip if not on a /me/ path mutation
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            is_mut, method, path = _is_router_mutation(dec)
            if not is_mut:
                continue
            if not path.startswith("/me/") and not path.startswith("/me"):
                # Operator-class endpoints (e.g. POST /{partner_id}/users)
                # follow a different gating posture (require_admin) and
                # are out of scope for this gate.
                continue
            if node.name in PER_USER_MUTATION_ALLOWLIST:
                continue
            bare, role = _find_partner_dependency(node)
            if bare and not role:
                violations.append(
                    f"partners.py:{node.lineno} {method} {path} → "
                    f"{node.name}: uses Depends(require_partner) (any role) "
                    f"instead of require_partner_role(...)"
                )

    assert not violations, (
        "Partner-side mutations must gate by role. Pre-round-table-31, "
        "billing-role partner_user could rotate site credentials and "
        "trigger maintenance because endpoints used bare "
        "Depends(require_partner). Switch to require_partner_role(\"admin\", "
        "\"tech\") for site-state mutations, require_partner_role(\"admin\") "
        "for partner-org-state mutations. Add to PER_USER_MUTATION_ALLOWLIST "
        "with justification only if the endpoint affects ONLY the caller's "
        "own row.\n\n" + "\n".join(f"  - {v}" for v in violations)
    )


def test_no_wrong_column_in_session_delete():
    """Round-table 31 C1 (2026-05-05): client_sessions has column
    `user_id`, not `client_user_id`. A pre-fix DELETE in
    org_management.py used the wrong name and would have crashed on
    first call. Pin via grep across the whole backend.
    """
    bad_pattern = "client_sessions.*client_user_id|client_user_id.*client_sessions"
    import re
    pat = re.compile(
        r"DELETE\s+FROM\s+client_sessions[^;]*?\bclient_user_id\b",
        re.IGNORECASE | re.DOTALL,
    )
    pat2 = re.compile(
        r"\bclient_user_id\b\s+IN\s+\(\s*SELECT\s+id\s+FROM\s+client_users",
        re.IGNORECASE,
    )
    bad_files: list[str] = []
    for py in _BACKEND.rglob("*.py"):
        if py.name.startswith("test_"):
            continue
        if "/tests/" in str(py):
            continue
        try:
            txt = py.read_text()
        except Exception:
            continue
        # Only look at code that mentions client_sessions
        if "client_sessions" not in txt:
            continue
        for line_num, line in enumerate(txt.splitlines(), start=1):
            if pat.search(line) or pat2.search(line):
                bad_files.append(f"{py.relative_to(_BACKEND)}:{line_num} — {line.strip()}")
    assert not bad_files, (
        "client_sessions schema column is `user_id`, not `client_user_id`. "
        "Found references to the wrong column name in DELETE/UPDATE "
        "queries — would crash at runtime with UndefinedColumnError.\n\n"
        + "\n".join(f"  - {b}" for b in bad_files)
    )
