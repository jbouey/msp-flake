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

_OPAQUE_MODULES = (_OWNER_TRANSFER, _EMAIL_RENAME, _CLIENT_PORTAL, _ORG_MGMT)

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


def _resolve_name_in_module(tree: ast.Module, name: str) -> ast.AST | None:
    """Find a same-module assignment `<name> = <expr>` and return the
    expression. Used to follow `body = <template>; send_email(..., body)`
    patterns so the gate doesn't fail open on Name args (Maya P1)."""
    best = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    best = node.value  # take the latest assignment
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name and node.value is not None:
                best = node.value
    return best


def _literal_string(node, tree: ast.Module | None = None, _depth: int = 0) -> str | None:
    """Render a string-ish AST node into its source-equivalent
    string, including f-string template text. Returns None for
    non-string-shaped nodes (variables, calls, etc.)."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        # f-string: concatenate literal text + {expr} placeholders
        # rendered as `{ast.unparse(value)}` so forbidden interpolations
        # are searchable as literal `{org_name}` etc.
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                out.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                try:
                    out.append("{" + ast.unparse(v.value) + "}")
                except Exception:
                    out.append("{?}")
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        # "a" + "b" or f"x" + f"y"
        left = _literal_string(node.left, tree, _depth)
        right = _literal_string(node.right, tree, _depth)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.Name) and tree is not None and _depth < 3:
        # Follow `body = <template>; send_email(..., body)` (Maya P1)
        target = _resolve_name_in_module(tree, node.id)
        if target is not None:
            return _literal_string(target, tree, _depth + 1)
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
            rendered = _literal_string(subject, tree)
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
            rendered = _literal_string(body, tree)
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


def test_no_fstring_only_subjects_with_context_interpolation():
    """Maya P2-2 belt: even if AST extraction fails on a weird
    construct, this catch-all string scan flags any send_email line
    where the subject literally contains `{org_name}` etc."""
    for path in _OPAQUE_MODULES:
        src = path.read_text()
        for token in (
            "{org_name}",
            "{source_org_name}",
            "{target_org_name}",
            "{clinic_name}",
            "{site_name}",
        ):
            # Find every occurrence of the token, then check if it
            # sits within ~200 chars after a `send_email(` paren.
            idx = 0
            while True:
                pos = src.find(token, idx)
                if pos < 0:
                    break
                idx = pos + 1
                window_start = max(0, pos - 400)
                window = src[window_start:pos]
                if "send_email(" in window and "_send_" not in window[
                    window.rfind("send_email(") :
                ][:30]:
                    # Crude proximity check: this token sits in the
                    # arg list of a send_email call. Real check is
                    # the AST tests above; this is a backup.
                    raise AssertionError(
                        f"{path.name}:{src[:pos].count(chr(10))+1} "
                        f"{token} appears within send_email(...) — "
                        f"opaque-mode forbids."
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
