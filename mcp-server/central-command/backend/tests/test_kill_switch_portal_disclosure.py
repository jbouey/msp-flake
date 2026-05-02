"""CI gate: client portal /home payload exposes fleet_healing_state.

#73 closure 2026-05-02 (Camila adversarial-round sub-followup of #64).

Pre-fix: kill-switch state was admin-internal only. A clinic auditor
visiting the portal during a paused window had no way to know
auto-remediation was off — HIPAA §164.316(b)(2)(i) chain-of-custody
gap if a fail-to-pass transition was missed during the pause.

Post-fix: portal /api/portal/site/{id}/home payload includes
fleet_healing_state. Client UI surfaces a banner above the home card
when paused. Actor email is intentionally NOT exposed (admin-internal);
reason IS exposed (operator-written, public-safe).

This source-level gate enforces the contract.
"""
from __future__ import annotations

import pathlib

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_PORTAL = _BACKEND / "portal.py"
_PRACTICE_HOME = _BACKEND.parent / "frontend" / "src" / "portal" / "PracticeHomeCard.tsx"


def test_portal_home_returns_fleet_healing_state():
    src = _PORTAL.read_text()
    assert '"fleet_healing_state"' in src or "'fleet_healing_state'" in src, (
        "portal.py::get_portal_home does NOT include fleet_healing_state "
        "in the response. Camila adversarial-round of #64 required this "
        "for HIPAA chain-of-custody when a paused window may have missed "
        "auto-remediation transitions."
    )


def test_portal_home_does_not_expose_actor_email_to_client():
    """Admin actor's email is intentionally NOT in the client portal
    payload. Reason IS in (operator-written, public-safe). Defense
    against accidental PII leak."""
    src = _PORTAL.read_text()
    # Find the fleet_healing_state block; verify no `actor` key inside
    import re
    # Look for the block construction; should NOT have actor=...
    m = re.search(r"fleet_healing_state[^=]*?=[^}]*?\}", src, re.DOTALL)
    if m:
        block = m.group(0)
        assert "actor" not in block.lower(), (
            "fleet_healing_state block in portal.py exposes actor field. "
            "Admin email is intentionally admin-internal — clinic auditors "
            "see the reason but not who flipped the switch."
        )


def test_practice_home_card_renders_pause_banner():
    src = _PRACTICE_HOME.read_text()
    assert "fleet_healing_state" in src, (
        "PracticeHomeCard.tsx is missing fleet_healing_state UI. The "
        "backend now ships the field; the client must surface it as a "
        "banner."
    )
    assert "paused" in src.lower(), (
        "Banner copy missing 'paused' wording — operator/auditor needs "
        "explicit terminology, not just an icon."
    )


def test_practice_home_card_interface_includes_field():
    """The HomeData TS interface must declare fleet_healing_state so
    consumers don't trip over `undefined` reads."""
    src = _PRACTICE_HOME.read_text()
    assert "fleet_healing_state?:" in src, (
        "HomeData interface in PracticeHomeCard.tsx does not declare "
        "fleet_healing_state. Add as optional field with disabled +"
        "paused_since + paused_reason sub-fields."
    )
