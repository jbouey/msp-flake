"""CI gate: every cryptographically-attested admin/client event has
its operator-visibility email hook present.

Pre-2026-05-04: kill-switch, admin destructive billing, break-glass,
privileged-access request creation, client-user mutations, site
mutations, and org deprovision/reprovision all wrote Ed25519 +
admin_audit_log but emitted ZERO operator email. Operators only
learned about these events via dashboard scan or 24h-stuck-escalation.

This source-level gate enforces the contract per-callsite. Adding a
new privileged event class requires (a) Ed25519 attestation insert,
(b) admin_audit_log insert, (c) send_operator_alert hook here.
Three-list lockstep style — drift = silent regression.

P2-1 (QA 2026-05-04): the original substring search would pass on
commented-out hooks (e.g. `# event_type="..."`) or hooks where the
caller string survived but the actual `send_operator_alert(...)` call
was deleted. Strengthened to AST-walk: parse the file, find
`Call(func.id == 'send_operator_alert')` nodes, extract the
`event_type` keyword, assert the expected value appears as an actual
runtime call argument — not as a string literal in dead code.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent.parent.parent
_MAIN_PY = _REPO_ROOT / "mcp-server" / "main.py"


def _read(p: pathlib.Path) -> str:
    return p.read_text()


def _extract_send_operator_alert_event_types(path: pathlib.Path) -> set[str]:
    """Walk the file's AST, return the set of event_type kwarg values
    passed to live `send_operator_alert(...)` calls.

    Recognizes both `send_operator_alert(event_type="...", ...)` and
    `email_alerts.send_operator_alert(event_type="...", ...)` forms.
    Comments + dead-code string literals are NOT matched (they don't
    appear as Call nodes at module-walk time).
    """
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError as e:
        pytest.fail(f"{path} failed to parse: {e}")

    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match `send_operator_alert(...)` (direct) or
        # `<anything>.send_operator_alert(...)` (attribute access)
        is_target = False
        func = node.func
        if isinstance(func, ast.Name) and func.id == "send_operator_alert":
            is_target = True
        elif isinstance(func, ast.Attribute) and func.attr == "send_operator_alert":
            is_target = True
        if not is_target:
            continue
        for kw in node.keywords:
            if kw.arg == "event_type" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    found.add(kw.value.value)
    return found


# Each tuple is (file_path, event_type_string). The event_type appears
# in the send_operator_alert(event_type="...") call at the hook site.
EXPECTED_HOOKS = [
    (_MAIN_PY, "fleet_healing_global_pause"),
    (_MAIN_PY, "fleet_healing_global_resume"),
    (_MAIN_PY, "billing_subscription_cancel"),
    (_MAIN_PY, "billing_charge_refund"),
    (_BACKEND / "breakglass_api.py", "break_glass_passphrase_retrieval"),
    (_BACKEND / "privileged_access_api.py", "privileged_access_request_created"),
    (_BACKEND / "client_portal.py", "client_user_invited"),
    (_BACKEND / "client_portal.py", "client_user_removed"),
    (_BACKEND / "client_portal.py", "client_user_role_changed"),
    (_BACKEND / "sites.py", "site_updated"),
    (_BACKEND / "sites.py", "appliance_relocated"),
    (_BACKEND / "org_management.py", "org_deprovisioned"),
    (_BACKEND / "org_management.py", "org_reprovisioned"),
]


@pytest.mark.parametrize("path,event_type", EXPECTED_HOOKS,
                         ids=[f"{p.name}::{e}" for p, e in EXPECTED_HOOKS])
def test_hook_callsite_present(path, event_type):
    """AST-strength check: the event_type must appear as an actual
    runtime kwarg to a Call node, NOT as a substring in a comment,
    docstring, or dead-code string literal. Caught by P2-1 in the
    QA round-table on f1d3e9f0."""
    found = _extract_send_operator_alert_event_types(path)
    assert event_type in found, (
        f"send_operator_alert hook missing for event_type={event_type!r} "
        f"in {path.name}. The cryptographic attestation chain captures "
        f"this event but operators won't see it in real time. Add a "
        f"send_operator_alert(event_type={event_type!r}, ...) call "
        f"after the existing audit_log insert + Ed25519 attestation "
        f"succeed. (AST-only — substring-in-comment will not satisfy.)"
    )


def test_helper_signature_stable():
    """The helper signature is part of the contract. If you rename or
    reshape it, every hook callsite needs to update in lockstep."""
    src = _read(_BACKEND / "email_alerts.py")
    assert "def send_operator_alert(" in src, (
        "send_operator_alert helper missing from email_alerts.py — "
        "every operator-visibility hook depends on it."
    )
    for kw in ["event_type:", "severity:", "summary:",
               "details:", "site_id:", "actor_email:"]:
        assert kw in src, (
            f"send_operator_alert signature missing parameter `{kw}`. "
            f"Callsites pass this kwarg explicitly."
        )


def test_ast_gate_rejects_commented_out_hook(tmp_path):
    """P2-1 (QA 2026-05-04): the gate must NOT accept a commented-out
    hook as satisfying the contract. Construct a synthetic source file
    with the event_type substring present only in comments and
    docstrings; assert the AST extractor finds nothing."""
    spoof = tmp_path / "spoof.py"
    spoof.write_text(
        '"""docstring mentioning event_type=\\"fake_event\\" — should not count."""\n'
        '# old: send_operator_alert(event_type="commented_out_event", ...)\n'
        'def f():\n'
        '    """body docstring with event_type=\\"another_fake\\"."""\n'
        '    s = "event_type=\\"string_literal_only\\""\n'
        '    return s\n'
    )
    found = _extract_send_operator_alert_event_types(spoof)
    assert found == set(), (
        f"AST gate spuriously matched dead-code event_types: {found}. "
        f"The P2-1 fix is broken — substring-bypass is back."
    )


def test_ast_gate_accepts_real_call(tmp_path):
    """Companion to the rejection test: confirm the AST extractor
    DOES find a real, live call site."""
    real = tmp_path / "real.py"
    real.write_text(
        'def f():\n'
        '    send_operator_alert(\n'
        '        event_type="real_event",\n'
        '        severity="P0",\n'
        '        summary="x",\n'
        '    )\n'
        '    email_alerts.send_operator_alert(\n'
        '        event_type="real_event_2",\n'
        '        severity="P1",\n'
        '        summary="y",\n'
        '    )\n'
    )
    found = _extract_send_operator_alert_event_types(real)
    assert found == {"real_event", "real_event_2"}, (
        f"AST gate failed to extract live calls: {found}"
    )


def test_helper_routes_to_alert_email():
    """The helper MUST send to ALERT_EMAIL (operator inbox). If a
    refactor accidentally routed it to client_orgs.alert_email or
    a partner address, every operator-visibility event would land
    in customer inboxes — a privacy + ops incident."""
    src = _read(_BACKEND / "email_alerts.py")
    helper_start = src.find("def send_operator_alert(")
    assert helper_start >= 0
    helper_body = src[helper_start:helper_start + 6000]
    assert "ALERT_EMAIL" in helper_body, (
        "send_operator_alert no longer references ALERT_EMAIL. The "
        "helper MUST route to the operator inbox, NOT to client/partner "
        "addresses. Recipient resolution drift was caught here."
    )
