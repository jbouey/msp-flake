"""Unit tests for email_alerts.send_operator_alert.

Session pickup 2026-05-04 (post Session 215). The helper is the
operator-visibility echo of cryptographically attested events
(kill-switch, billing-destructive, break-glass, privileged-access
request creation, client-org user mutations, site mutations,
org deprovision/reprovision). The Ed25519 attestation chain stays
the source of truth; this email path is best-effort.

Three contracts pinned:
  1. Returns False (does not raise) when SMTP not configured.
  2. Calls _send_smtp_with_retry with [ALERT_EMAIL] as recipient list.
  3. Subject line carries the [OsirisCare <SEV>] prefix and event_type.
  4. Body never raises on dict/list values in details (json-serialized).
"""
from __future__ import annotations

import os
import sys
import pathlib
from unittest.mock import patch

# Allow `from email_alerts import ...` when run from repo root.
_BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))


def test_returns_false_when_smtp_not_configured(monkeypatch):
    monkeypatch.setattr("email_alerts.SMTP_USER", "")
    monkeypatch.setattr("email_alerts.SMTP_PASSWORD", "")
    from email_alerts import send_operator_alert
    assert send_operator_alert(
        event_type="test_event",
        severity="P0",
        summary="should be skipped",
    ) is False


def test_passes_alert_email_to_smtp(monkeypatch):
    monkeypatch.setattr("email_alerts.SMTP_USER", "user")
    monkeypatch.setattr("email_alerts.SMTP_PASSWORD", "pw")
    monkeypatch.setattr("email_alerts.ALERT_EMAIL", "ops@example.test")

    captured = {}

    def fake_send(msg, recipients, label, max_retries=3, partner_branding=None):
        captured["recipients"] = recipients
        captured["subject"] = msg.get("Subject")
        captured["label"] = label
        return True

    monkeypatch.setattr("email_alerts._send_smtp_with_retry", fake_send)
    from email_alerts import send_operator_alert
    ok = send_operator_alert(
        event_type="break_glass_passphrase_retrieval",
        severity="P0",
        summary="retrieved by ops@example.test",
        site_id="site-x",
        actor_email="ops@example.test",
    )
    assert ok is True
    assert captured["recipients"] == ["ops@example.test"]
    assert "[OsirisCare P0]" in captured["subject"]
    assert "break_glass_passphrase_retrieval" in captured["subject"]
    assert "site=site-x" in captured["subject"]
    assert "operator alert: break_glass_passphrase_retrieval" in captured["label"]


def test_handles_dict_details_without_raising(monkeypatch):
    monkeypatch.setattr("email_alerts.SMTP_USER", "user")
    monkeypatch.setattr("email_alerts.SMTP_PASSWORD", "pw")
    monkeypatch.setattr("email_alerts._send_smtp_with_retry",
                        lambda *a, **k: True)
    from email_alerts import send_operator_alert
    # Nested dict + list mix — must serialize without raising
    ok = send_operator_alert(
        event_type="t",
        severity="P2",
        summary="s",
        details={
            "changes": {"contact_email": {"from": "a@x", "to": "b@x"}},
            "list_field": [1, 2, "three"],
            "scalar": 42,
        },
    )
    assert ok is True


def test_returns_false_on_smtp_failure(monkeypatch):
    monkeypatch.setattr("email_alerts.SMTP_USER", "user")
    monkeypatch.setattr("email_alerts.SMTP_PASSWORD", "pw")
    monkeypatch.setattr("email_alerts._send_smtp_with_retry",
                        lambda *a, **k: False)
    from email_alerts import send_operator_alert
    ok = send_operator_alert(
        event_type="t",
        severity="P1",
        summary="s",
    )
    assert ok is False


def test_subject_truncates_when_too_long(monkeypatch):
    monkeypatch.setattr("email_alerts.SMTP_USER", "user")
    monkeypatch.setattr("email_alerts.SMTP_PASSWORD", "pw")
    captured = {}

    def fake_send(msg, recipients, label, max_retries=3, partner_branding=None):
        captured["subject"] = msg.get("Subject")
        return True

    monkeypatch.setattr("email_alerts._send_smtp_with_retry", fake_send)
    from email_alerts import send_operator_alert
    send_operator_alert(
        event_type="t",
        severity="P0",
        summary="x" * 500,
    )
    assert len(captured["subject"]) <= 160
    assert captured["subject"].endswith("...")


def test_never_raises_on_unexpected_internal_error(monkeypatch):
    """Even if msg construction blows up, the helper must NOT raise.
    The cryptographic attestation already succeeded — email failure
    must not propagate and break the calling handler's response."""
    monkeypatch.setattr("email_alerts.SMTP_USER", "user")
    monkeypatch.setattr("email_alerts.SMTP_PASSWORD", "pw")

    def boom(*a, **k):
        raise RuntimeError("unexpected SMTP failure path")

    monkeypatch.setattr("email_alerts._send_smtp_with_retry", boom)
    from email_alerts import send_operator_alert
    # Must NOT raise
    ok = send_operator_alert(
        event_type="t",
        severity="P0",
        summary="s",
    )
    assert ok is False
