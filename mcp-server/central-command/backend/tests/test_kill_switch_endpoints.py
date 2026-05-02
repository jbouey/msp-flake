"""CI gate: fleet-wide healing kill-switch endpoint surface.

#64 P0 ADVERSARIAL closure 2026-05-02. The kill-switch is the
emergency-stop primitive — operator MUST be able to halt fleet-wide
L2 healing in seconds when a bad rule starts mass-poisoning data.

This source-level gate enforces the contract:
  - All 3 endpoints exist (state / pause / resume)
  - Both mutating endpoints require confirm_phrase to prevent accidents
  - Both mutating endpoints write to admin_audit_log
  - agent_l2_plan checks the kill-switch flag at handler entry

Pre-fix: no kill switch existed. Operator forced to SSH+psql for
manual SQL ALTER. Incident response in MINUTES not SECONDS.
"""
from __future__ import annotations

import pathlib

import pytest


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent.parent.parent
_MAIN_PY = _REPO_ROOT / "mcp-server" / "main.py"
_AGENT_API = _BACKEND / "agent_api.py"


def _read(p: pathlib.Path) -> str:
    return p.read_text()


def test_global_state_endpoint_exists():
    src = _read(_MAIN_PY)
    assert '/api/admin/healing/global-state' in src, (
        "GET /api/admin/healing/global-state endpoint missing — UI "
        "needs this to render the kill-switch button + banner."
    )


def test_global_pause_endpoint_exists():
    src = _read(_MAIN_PY)
    assert '/api/admin/healing/global-pause' in src, (
        "POST /api/admin/healing/global-pause endpoint missing — this "
        "IS the emergency-stop primitive. Operator has no path to halt "
        "fleet-wide L2 healing without it."
    )


def test_global_resume_endpoint_exists():
    src = _read(_MAIN_PY)
    assert '/api/admin/healing/global-resume' in src, (
        "POST /api/admin/healing/global-resume endpoint missing — "
        "operator must be able to UN-pause as easily as pause; "
        "asymmetry creates incident-recovery friction."
    )


def test_pause_requires_confirm_phrase():
    """Anti-accident: pause MUST require typed-literal confirm_phrase."""
    src = _read(_MAIN_PY)
    assert "DISABLE-FLEET-HEALING" in src, (
        "Pause endpoint missing confirm_phrase requirement. Without "
        "literal-string check, accidental click = fleet outage."
    )


def test_resume_requires_different_confirm_phrase():
    """Anti-typo: resume requires a DIFFERENT phrase from pause so a
    half-typed 'DISABLE-' on resume doesn't accidentally pause."""
    src = _read(_MAIN_PY)
    assert "ENABLE-FLEET-HEALING" in src, (
        "Resume endpoint missing confirm_phrase requirement. Resume "
        "phrase MUST differ from pause to prevent typo-accidents."
    )


def test_pause_writes_admin_audit_log():
    src = _read(_MAIN_PY)
    assert "INSERT INTO admin_audit_log" in src and "healing.global_pause" in src, (
        "Pause endpoint missing admin_audit_log INSERT. Privileged "
        "actions MUST audit-log per CLAUDE.md INVIOLABLE chain-of-custody."
    )


def test_resume_writes_admin_audit_log():
    src = _read(_MAIN_PY)
    assert "healing.global_resume" in src, (
        "Resume endpoint missing admin_audit_log INSERT."
    )


def test_pause_requires_reason_min_length():
    src = _read(_MAIN_PY)
    assert "min_length=20" in src and "HealingPauseRequest" in src, (
        "Pause request schema must enforce reason ≥20 chars (privileged-"
        "access-chain rule from CLAUDE.md)."
    )


def test_l2_endpoint_checks_kill_switch():
    """agent_l2_plan MUST check the kill-switch flag BEFORE invoking
    the LLM. Otherwise the kill-switch is cosmetic — flag is set but
    healing keeps running."""
    src = _read(_AGENT_API)
    assert "fleet_healing_disabled" in src, (
        "agent_l2_plan in agent_api.py does not check the kill-switch "
        "flag. Without this check, setting the flag has NO RUNTIME "
        "EFFECT — healing keeps running. Kill-switch is cosmetic."
    )
    assert "fleet_healing_globally_disabled" in src, (
        "agent_l2_plan must return structured 503 with "
        "degraded_reason='fleet_healing_globally_disabled' so daemons "
        "know to fall through to L3 escalation."
    )


def test_kill_switch_uses_singleton_system_settings():
    """No new table; uses singleton system_settings.id=1 JSONB. If a
    future engineer adds a NEW table for the flag, the lockstep across
    pause/resume/state/agent_l2_plan breaks (4 readers/writers must
    point at the same source of truth)."""
    src = _read(_MAIN_PY)
    assert "system_settings WHERE id = 1" in src, (
        "Kill-switch must use the singleton system_settings table — "
        "consistency with the existing config primitive. Don't add a "
        "new table."
    )


# ─── #74 Ed25519 attestation chain wire ───────────────────────────────


def test_kill_switch_events_in_allowed_events():
    """ALLOWED_EVENTS in privileged_access_attestation.py must contain
    fleet_healing_global_pause + fleet_healing_global_resume so the
    kill-switch attestations can be written. Without this the attestation
    raises and falls through to admin_audit_log only — losing the
    crypto-grade evidence Camila required."""
    pa_path = _BACKEND / "privileged_access_attestation.py"
    src = pa_path.read_text()
    for event in ("fleet_healing_global_pause", "fleet_healing_global_resume"):
        assert f'"{event}"' in src, (
            f"ALLOWED_EVENTS missing {event!r}. Kill-switch attestation "
            f"will raise PrivilegedAccessAttestationError and fall back "
            f"to admin_audit_log only. Camila adversarial-round required "
            f"Ed25519-grade evidence for fleet-wide healing pause/resume."
        )


def test_kill_switch_endpoints_call_per_site_attestation():
    """Both pause + resume MUST call the per-site attestation helper.
    Otherwise the ALLOWED_EVENTS entry is dead weight + the kill-
    switch silently lacks crypto evidence."""
    src = _read(_MAIN_PY)
    assert "_kill_switch_per_site_attestation" in src, (
        "Kill-switch endpoints don't invoke per-site attestation. The "
        "ALLOWED_EVENTS entries are dead weight without callers."
    )
    # Verify both event types appear in the call sites
    for event in ("fleet_healing_global_pause", "fleet_healing_global_resume"):
        assert f'event_type="{event}"' in src, (
            f"Kill-switch endpoint not invoking attestation with "
            f"event_type={event!r}."
        )


def test_kill_switch_actor_must_be_email():
    """create_privileged_access_attestation rejects actors without '@'.
    Both endpoints MUST validate actor is email format BEFORE invoking
    attestation, else operator gets 500 instead of 403 with clear
    'use-email-actor' message."""
    src = _read(_MAIN_PY)
    # Both pause + resume should have an "@" check before the attestation
    # call. Look for the explicit email-check pattern.
    assert src.count('"@" not in actor') >= 2, (
        "Kill-switch endpoints don't validate email format on actor. "
        "Without this check, attestation raises PrivilegedAccessAttestation"
        "Error with confusing 'no @' message instead of clean 403."
    )
