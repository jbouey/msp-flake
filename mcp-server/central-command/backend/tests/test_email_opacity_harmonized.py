"""CI gate: customer-facing email helpers across multiple modules use
opaque-mode subjects + bodies (task #42 harmonization, 2026-05-06;
sweep + AST refactor 2026-05-06).

Counsel approved opaque-mode for cross-org relocate emails (RT21
v2.3). Maya flagged the inconsistency: owner-transfer +
email-rename emails were still verbose. Once one class shipped
opaque, the asymmetry was attackable. Harmonized to opaque.

A subsequent sweep (2026-05-06) found two more customer-facing
helpers that interpolated org_name in subjects/bodies — the
client-portal invite email and the org-deprovision notice. Both
matched the RT21 threat model (recipient may be a mistyped /
forwarded / ex-member address; org identity should only appear
after they authenticate). Both refactored to opaque mode and
folded into the gate.

This gate is AST-based (Maya P2-2/P2-4 hardening): regex scans
miss multi-line subjects, kwarg-style send_email calls, and
parenthesized concatenations. Walking the AST and inspecting the
actual `Call.args[0]` (recipient), `args[1]` (subject), `args[2]`
(body) gives reliable extraction.

Modules in scope (customer-facing — must be opaque):
  - cross_org_site_relocate.py — RT21 (also pinned by
    test_cross_org_relocate_contract.py)
  - client_owner_transfer.py
  - client_user_email_rename.py
  - client_portal.py — invite email + magic-link
  - org_management.py — deprovision notice

Modules explicitly out of scope (operator-facing — verbose OK):
  - email_alerts.py (operator alerts)
  - privileged_access_notifier.py (operator chain notifications)
  - escalation_engine.py (operator escalation)
  - assertions.py (substrate invariant alerts)
  - notifications.py (settings/test routing)
  - mfa_admin.py — single static-subject helper, opaque-by-construction
  - partner_auth.py — partner approval, opaque-by-construction
"""
from __future__ import annotations

import ast
import pathlib
from typing import Iterable

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_OWNER_TRANSFER = _BACKEND / "client_owner_transfer.py"
_EMAIL_RENAME = _BACKEND / "client_user_email_rename.py"
_CLIENT_PORTAL = _BACKEND / "client_portal.py"
_ORG_MGMT = _BACKEND / "org_management.py"
_RELOCATE = _BACKEND / "cross_org_site_relocate.py"
# Maya 4th-pass P0: portal.py / sites.py / background_tasks.py were
# misclassified as operator-only — they call SMTP fallbacks
# (_send_smtp_with_retry / send_email) for client_contact_email
# recipients with org/clinic interpolation. Moved into opaque scope.
_PORTAL = _BACKEND / "portal.py"
_SITES = _BACKEND / "sites.py"
_BG_TASKS = _BACKEND / "background_tasks.py"
# Cold-onboarding 2026-05-09 P0 #1+#3+#4 closure: self-serve Stripe
# webhook now sends the onboarding email when a customer's first
# tenant is materialized. Customer-facing class — opaque required
# (recipient may be a freshly-created mailbox or a forwarded address;
# org identity should appear only inside the authenticated portal
# session).
_CLIENT_SIGNUP = _BACKEND / "client_signup.py"
# Task #53 v2 Phase 0 (2026-05-13): alert_router.py is fully
# customer-facing for the 4 subject paths (compliance digest,
# monitoring-active, compliance alert, partner non-engagement) — all
# 4 rewritten to class-hint plain literals in the same commit.
#
# email_alerts.py is DELIBERATELY EXCLUDED from this allowlist at
# Phase 0: it's mixed-recipient (operator-class subjects at lines
# 257/780/820/1584 + 1 latent send_companion_alert_email org_name
# leak to clinic-side at line 820). The SRA-reminder subject at
# line 947 IS customer-facing and was rewritten in the same commit
# to a class-hint plain literal — but the module as a whole cannot
# enter _OPAQUE_MODULES without structural recipient-split first.
# Phase 1 design (own Gate A) addresses email_alerts.py module-level
# gating + the line-820 latent leak fix.
_ALERT_ROUTER = _BACKEND / "alert_router.py"

_OPAQUE_MODULES = (
    _OWNER_TRANSFER,
    _EMAIL_RENAME,
    _CLIENT_PORTAL,
    _ORG_MGMT,
    _RELOCATE,  # also pinned by test_cross_org_relocate_contract.py
    _PORTAL,
    _SITES,
    _BG_TASKS,
    _CLIENT_SIGNUP,
    _ALERT_ROUTER,
)

# Forbidden parameter names in opaque-mode helpers (drop verbose-mode
# context kwargs from helper signatures). Recipient-address params
# like `target_email` / `initiator_email` are NOT forbidden — they're
# the to-address, not a context leak.
FORBIDDEN_HELPER_PARAMS = (
    "org_name",
    "actor_kind",
    "reason",
    "clinic_name",
    "target_email_inline",
    "initiator_email_inline",
    "source_org_name",
    "target_org_name",
)

FORBIDDEN_CALL_KWARGS = (
    "org_name=",
    "actor_kind=",
    "reason=",
    "clinic_name=",
)

# Tokens forbidden in body f-string literals (interpolated context).
FORBIDDEN_BODY_TOKENS = (
    "{org_name}",
    "{clinic_name}",
    "{actor_kind}",
    "{site_name}",
    "{source_org_name}",
    "{target_org_name}",
    "{reason",  # catches {reason} and {reason[:200]}
    "{old_email}",  # NEW-recipient body must not name old address
    # {new_email} carve-out: allowed ONLY in send_email's first arg
    # (the recipient address). Enforced by the AST walker which
    # inspects only args[1] (subject) and args[2] (body), not args[0]
)

# Tokens forbidden in subject literals — strict superset of body.
FORBIDDEN_SUBJECT_TOKENS = FORBIDDEN_BODY_TOKENS + (
    "{new_email}",
    "{target_email}",
    "{initiator_email}",
)

# Helpers whose signatures must be opaque
OWNER_TRANSFER_HELPERS = (
    "_send_initiator_confirmation_email",
    "_send_target_accept_email",
)
EMAIL_RENAME_HELPERS = ("_send_dual_notification",)


# ---------------------------------------------------------------- AST


def _parse(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _iter_send_email_calls(tree: ast.Module) -> Iterable[ast.Call]:
    """Yield every Call node that looks like `send_email(...)` —
    bare-name OR attribute-access OR `_send_dual_notification(...)`.
    Walking the AST is robust against the formatting variations a
    regex scan misses (multi-line subjects, kwarg-style calls,
    parenthesized string concatenations)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in {
            "send_email",
            "_send_dual_notification",
            "_send_initiator_confirmation_email",
            "_send_target_accept_email",
        }:
            yield node


def _iter_mime_send_calls(tree: ast.Module) -> Iterable[ast.Call]:
    """Yield calls to MIME-style senders. These take a pre-built
    MIMEMessage as arg[0] — subject/body are set via
    msg['Subject'] = ... earlier in the function, so the
    arg-position checks don't apply. Subject opacity for these is
    enforced separately by `test_mime_subject_assignments_opaque`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in {"_send_smtp_with_retry", "_send_magic_link_smtp"}:
            yield node


def _arg(call: ast.Call, position: int, kw_aliases: tuple[str, ...]):
    """Resolve positional or keyword arg. send_email is called as
    send_email(recipient, subject, body) by convention; some
    helper-call sites use kwargs (old_email=, new_email=)."""
    if len(call.args) > position:
        return call.args[position]
    for kw in call.keywords:
        if kw.arg in kw_aliases:
            return kw.value
    return None


def _enclosing_function(tree: ast.Module, target: ast.AST) -> ast.AST | None:
    """Find the FunctionDef/AsyncFunctionDef that lexically encloses
    `target` (the Call we're inspecting). Returns None if `target` is
    at module scope. Pre-built parent map so we don't recompute."""
    parent: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node
    cur: ast.AST | None = target
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur
        cur = parent.get(id(cur))
    return None


def _resolve_name(
    tree: ast.Module, name: str, call: ast.AST | None = None
) -> ast.AST | None:
    """Scope-aware Name resolution (Steve P1 + Maya P1).

    Rules (fail closed on ambiguity):
      1. If `call` is provided, search Assigns inside the enclosing
         FunctionDef first. If exactly one assignment to `name`
         exists in that scope, return its RHS. If multiple distinct
         RHS expressions exist (conditional rebind), return None
         (unresolvable → fail closed).
      2. Fall back to module-level Assigns ONLY if no
         function-scoped assignment exists. Same multi-distinct-RHS
         rule applies.

    Module-walk taking "the latest assignment" (the prior
    implementation) was scope-blind: a benign module-level
    `body = "static"` could shadow a real function-local
    `body = f"{org_name}"` and silently pass the gate."""

    def _scan(scope: ast.AST) -> list[ast.AST]:
        rhs: list[ast.AST] = []
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == name:
                        rhs.append(node.value)
            elif isinstance(node, ast.AnnAssign):
                if (
                    isinstance(node.target, ast.Name)
                    and node.target.id == name
                    and node.value is not None
                ):
                    rhs.append(node.value)
        return rhs

    fn = _enclosing_function(tree, call) if call is not None else None
    if fn is not None:
        local = _scan(fn)
        if len(local) == 1:
            return local[0]
        if len(local) > 1:
            # Conditional rebind — multiple distinct RHS reach the
            # call point. Fail closed; the contributor must inline.
            distinct = {ast.dump(v) for v in local}
            if len(distinct) > 1:
                return None
            return local[0]
    module_lvl = _scan(tree)
    if len(module_lvl) == 1:
        return module_lvl[0]
    if len(module_lvl) > 1:
        distinct = {ast.dump(v) for v in module_lvl}
        if len(distinct) > 1:
            return None
        return module_lvl[0]
    return None


# Backwards-compat alias for any external callers
_resolve_name_in_module = _resolve_name


def _literal_string(
    node,
    tree: ast.Module | None = None,
    call: ast.AST | None = None,
    _depth: int = 0,
    _seen: set[int] | None = None,
) -> str | None:
    """Render a string-ish AST node into its source-equivalent
    string, including f-string template text. Returns None for
    non-string-shaped nodes (variables, calls, etc.)."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if _seen is None:
        _seen = set()
    if id(node) in _seen or _depth > 4:
        return None
    _seen = _seen | {id(node)}
    if isinstance(node, ast.JoinedStr):
        # f-string: concatenate literal text + {expr} placeholders.
        # If the expr is a Name and we have a tree, follow the Name
        # through the resolver so two-step indirection
        # (`f"{x}"` where `x = f"{org_name}"`) is caught (Maya P1).
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                out.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                if isinstance(v.value, ast.Name) and tree is not None:
                    nested = _literal_string(
                        v.value, tree, call, _depth + 1, _seen
                    )
                    if nested is not None:
                        out.append(nested)
                        continue
                try:
                    out.append("{" + ast.unparse(v.value) + "}")
                except Exception:
                    out.append("{?}")
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_string(node.left, tree, call, _depth, _seen)
        right = _literal_string(node.right, tree, call, _depth, _seen)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.IfExp):
        # Conditional: `f"..." if cond else ""`. Render BOTH branches
        # and concatenate so forbidden-token search hits a leak in
        # either branch. Common in opaque-mode bodies that
        # conditionally include a chain-anchor line.
        body_s = _literal_string(node.body, tree, call, _depth, _seen)
        else_s = _literal_string(node.orelse, tree, call, _depth, _seen)
        if body_s is not None and else_s is not None:
            return body_s + else_s
        if body_s is not None:
            return body_s
        if else_s is not None:
            return else_s
        return None
    if isinstance(node, ast.Name) and tree is not None and _depth < 3:
        target = _resolve_name(tree, node.id, call)
        if target is not None:
            return _literal_string(target, tree, call, _depth + 1, _seen)
        return None
    return None


# ---------------------------------------------------------------- helpers tests


def _helper_signature(src: str, helper_name: str) -> str:
    import re
    m = re.search(
        rf"async def {helper_name}\((.*?)\)",
        src,
        __import__("re").DOTALL,
    )
    assert m, f"{helper_name} not found in source"
    return m.group(1)


def test_owner_transfer_helpers_have_opaque_signatures():
    src = _OWNER_TRANSFER.read_text()
    for helper in OWNER_TRANSFER_HELPERS:
        sig = _helper_signature(src, helper)
        for forbidden in FORBIDDEN_HELPER_PARAMS:
            assert forbidden not in sig, (
                f"{helper} signature in client_owner_transfer.py "
                f"still accepts `{forbidden}` parameter — task #42 "
                f"harmonization made owner-transfer emails opaque."
            )


def test_email_rename_helper_has_opaque_signature():
    src = _EMAIL_RENAME.read_text()
    for helper in EMAIL_RENAME_HELPERS:
        sig = _helper_signature(src, helper)
        for forbidden in FORBIDDEN_HELPER_PARAMS:
            assert forbidden not in sig, (
                f"{helper} signature in client_user_email_rename.py "
                f"still accepts `{forbidden}` parameter — task #42 "
                f"harmonization made email-rename emails opaque."
            )


# ---------------------------------------------------------------- subject + body


def test_subjects_are_opaque_across_all_modules():
    """AST-walk every send_email() call in the opaque modules and
    check arg[1] (subject) for forbidden interpolations. Replaces
    the regex-based version (Maya P2-2)."""
    failures = []
    for path in _OPAQUE_MODULES:
        tree = _parse(path)
        for call in _iter_send_email_calls(tree):
            subject = _arg(call, 1, ("subject",))
            rendered = _literal_string(subject, tree, call)
            if rendered is None:
                # Non-string subject: it's a variable or call. Force
                # the literal-string convention so opacity is auditable.
                if subject is not None:
                    failures.append(
                        f"{path.name}:{call.lineno} subject is not a "
                        f"string literal ({type(subject).__name__}) — "
                        f"opaque-mode requires plain string subjects "
                        f"so the gate can audit them."
                    )
                continue
            for token in FORBIDDEN_SUBJECT_TOKENS:
                if token in rendered:
                    failures.append(
                        f"{path.name}:{call.lineno} subject "
                        f"interpolates `{token}` — opaque-mode "
                        f"forbids context names in subjects."
                    )
    assert not failures, "Opacity violations:\n" + "\n".join(
        f"  - {f}" for f in failures
    )


def test_bodies_are_opaque_across_all_modules():
    """AST-walk every send_email() call and check arg[2] (body) for
    forbidden interpolations. Recipient-address arg[0] is exempt.
    Fails closed if body cannot be resolved to a literal (Maya P1):
    the gate refuses to silently skip variable-bound bodies because
    that's exactly the regression vector — `body = f\"{org_name}\";
    send_email(to, subj, body)`. _literal_string follows Name
    references into module-level Assigns to handle the
    body_template pattern."""
    failures = []
    unresolvable: list[str] = []
    for path in _OPAQUE_MODULES:
        tree = _parse(path)
        for call in _iter_send_email_calls(tree):
            body = _arg(call, 2, ("body",))
            if body is None:
                continue
            rendered = _literal_string(body, tree, call)
            if rendered is None:
                unresolvable.append(
                    f"{path.name}:{call.lineno} body arg "
                    f"({type(body).__name__}) cannot be resolved to "
                    f"a string literal — the opacity gate fails "
                    f"closed. Either inline the body literal or "
                    f"assign body to a string-constant Name in the "
                    f"same module so the resolver can follow it."
                )
                continue
            for token in FORBIDDEN_BODY_TOKENS:
                if token in rendered:
                    failures.append(
                        f"{path.name}:{call.lineno} body "
                        f"interpolates `{token}` — opaque-mode "
                        f"forbids context names in bodies."
                    )
    assert not failures and not unresolvable, (
        "Opacity violations:\n"
        + "\n".join(f"  - {f}" for f in failures + unresolvable)
    )


def test_mime_subject_assignments_opaque():
    """For MIME-style senders (`_send_smtp_with_retry`,
    `_send_magic_link_smtp`), the subject is set on the message via
    `msg["Subject"] = ...` earlier in the function. Walk the AST
    for those Subscript-Subject assignments in opaque modules and
    check they don't interpolate context tokens."""
    failures: list[str] = []
    for path in _OPAQUE_MODULES:
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for tgt in node.targets:
                if not isinstance(tgt, ast.Subscript):
                    continue
                # msg["Subject"] = ...  — slice is a Constant("Subject")
                slc = tgt.slice
                slice_str = None
                if isinstance(slc, ast.Constant) and isinstance(slc.value, str):
                    slice_str = slc.value
                if slice_str != "Subject":
                    continue
                rendered = _literal_string(node.value, tree, node)
                if rendered is None:
                    failures.append(
                        f"{path.name}:{node.lineno} MIME Subject "
                        f"assignment is not a string literal — "
                        f"opaque-mode requires plain string subjects."
                    )
                    continue
                for token in FORBIDDEN_SUBJECT_TOKENS:
                    if token in rendered:
                        failures.append(
                            f"{path.name}:{node.lineno} MIME Subject "
                            f"interpolates `{token}` — opaque-mode "
                            f"forbids context names in subjects."
                        )
    assert not failures, "Opacity violations:\n" + "\n".join(
        f"  - {f}" for f in failures
    )


def test_call_sites_do_not_pass_forbidden_kwargs():
    """Helper call sites in the opaque modules must not pass the
    verbose-mode context kwargs."""
    failures = []
    for path in _OPAQUE_MODULES:
        tree = _parse(path)
        for call in _iter_send_email_calls(tree):
            for kw in call.keywords:
                if kw.arg in {
                    "org_name",
                    "actor_kind",
                    "reason",
                    "clinic_name",
                    "source_org_name",
                    "target_org_name",
                }:
                    failures.append(
                        f"{path.name}:{call.lineno} call passes "
                        f"forbidden kwarg `{kw.arg}=`."
                    )
    assert not failures, "Opacity violations:\n" + "\n".join(
        f"  - {f}" for f in failures
    )


def test_meta_every_send_email_caller_is_classified():
    """Maya pre-mortem (round-table 2026-05-06): manual
    `_OPAQUE_MODULES` is the most likely future-incident vector — a
    contributor adds a new customer-facing email helper, forgets to
    add it here, the gate silently passes, org names start leaking
    again. Auto-discover every `*.py` under `backend/` that calls
    `send_email(...)` or `_send_dual_notification(...)`. Each must
    be classified as opaque (in `_OPAQUE_MODULES`) or operator (in
    `OPERATOR_ALLOWLIST`) — no third state. Forces deliberate
    triage on every new caller. Replaces the prior regex-belt
    proximity test (Steve P3 — AST coverage now sufficient)."""
    OPERATOR_ALLOWLIST = {
        _BACKEND / "email_alerts.py",
        _BACKEND / "privileged_access_notifier.py",
        _BACKEND / "escalation_engine.py",
        _BACKEND / "assertions.py",
        _BACKEND / "notifications.py",
        _BACKEND / "mfa_admin.py",
        _BACKEND / "partner_auth.py",
        _BACKEND / "partner_admin_transfer.py",
        _BACKEND / "email_service.py",
        # NOTE: portal.py, background_tasks.py, sites.py moved to
        # _OPAQUE_MODULES (Maya 4th-pass P0). Do NOT add them here.
    }
    classified = set(_OPAQUE_MODULES) | OPERATOR_ALLOWLIST
    discovered: list[pathlib.Path] = []
    for py_path in _BACKEND.glob("*.py"):
        if py_path.name.startswith("test_"):
            continue
        try:
            src = py_path.read_text()
        except Exception:
            continue
        if (
            "send_email(" in src
            or "_send_dual_notification(" in src
            or "_send_smtp_with_retry(" in src
            or "_send_magic_link_smtp(" in src
        ):
            discovered.append(py_path)
    unclassified = [p for p in discovered if p not in classified]
    assert not unclassified, (
        "send_email(...) callers not classified as opaque or "
        "operator — pick one and add to either `_OPAQUE_MODULES` "
        "(customer-facing → opaque required) or to "
        "`OPERATOR_ALLOWLIST` in this test (internal → verbose "
        "OK):\n"
        + "\n".join(f"  - {p.name}" for p in unclassified)
    )


# ---------------------------------------------------------------- operator allowlist guard


def test_operator_modules_are_not_in_opaque_scope():
    """Sentinel: if someone moves `email_alerts.py` etc. into
    _OPAQUE_MODULES, this test fails — operator channels are
    explicitly verbose and should not be gated by opacity rules."""
    operator_modules = {
        _BACKEND / "email_alerts.py",
        _BACKEND / "privileged_access_notifier.py",
        _BACKEND / "escalation_engine.py",
        _BACKEND / "assertions.py",
        _BACKEND / "notifications.py",
    }
    # Maya P2 hardening: assert each operator module exists. If a
    # file moves (e.g. into an internal/ subdir) the sentinel must
    # FAIL rather than silently pass with an empty intersection.
    missing = [p for p in operator_modules if not p.exists()]
    assert not missing, (
        f"Operator modules listed in sentinel no longer exist on "
        f"disk: {[p.name for p in missing]}. Update the sentinel "
        f"path or restore the file."
    )
    overlap = operator_modules & set(_OPAQUE_MODULES)
    assert not overlap, (
        f"Operator-facing modules incorrectly placed in opaque "
        f"scope: {[p.name for p in overlap]}. Operator channels "
        f"are intentionally verbose."
    )
