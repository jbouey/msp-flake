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
"""
from __future__ import annotations

import pathlib

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent.parent.parent
_MAIN_PY = _REPO_ROOT / "mcp-server" / "main.py"


def _read(p: pathlib.Path) -> str:
    return p.read_text()


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
    src = _read(path)
    needle = f'event_type="{event_type}"'
    assert needle in src, (
        f"send_operator_alert hook missing for event_type={event_type} "
        f"in {path.name}. The cryptographic attestation chain captures "
        f"this event but operators won't see it in real time. Add a "
        f"send_operator_alert(...) call after the existing audit_log "
        f"insert + Ed25519 attestation succeed."
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
