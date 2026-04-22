"""Session 209 Alertmanager webhook receiver tests.

The webhook is the only non-SMTP-native path for our AM → email
bridge. Breaking the contract silently drops paging — the whole
point of wiring Prom was to stop flying blind. These tests lock:

  1. Missing env token → 503 (AM retries)
  2. Bad/missing header token → 401 (no send, logged)
  3. Well-formed payload + valid token → delegates to
     send_alertmanager_digest with the recipient list
  4. Severity + status parsing shape the subject correctly
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")

_backend = pathlib.Path(__file__).resolve().parent.parent
_mcp_server = _backend.parent.parent
for _p in (str(_backend), str(_mcp_server)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_webhook():
    try:
        from dashboard_api import alertmanager_webhook as _w
    except Exception:
        import alertmanager_webhook as _w  # type: ignore
    return _w


def _load_email_alerts():
    try:
        from dashboard_api import email_alerts as _e
    except Exception:
        import email_alerts as _e  # type: ignore
    return _e


BACKEND = _backend
WEBHOOK = BACKEND / "alertmanager_webhook.py"
EMAIL_ALERTS = BACKEND / "email_alerts.py"


# -----------------------------------------------------------------------------
# Source-level guardrails
# -----------------------------------------------------------------------------


def test_webhook_module_exists():
    assert WEBHOOK.exists(), "alertmanager_webhook.py must exist in backend/"


def test_webhook_uses_post():
    assert '@router.post("/alertmanager-webhook")' in WEBHOOK.read_text()


def test_webhook_router_prefix():
    assert 'APIRouter(prefix="/api/admin"' in WEBHOOK.read_text(), (
        "Path prefix /api/admin is part of the Alertmanager config "
        "contract. Changing it orphans every AM pointing at the old URL."
    )


def test_webhook_uses_constant_time_compare():
    assert "hmac.compare_digest" in WEBHOOK.read_text()


def test_webhook_registered_in_main():
    main_py = (BACKEND.parent.parent / "main.py").read_text()
    assert "alertmanager_webhook_router" in main_py, (
        "Webhook router must be included in main.py, otherwise "
        "POST /api/admin/alertmanager-webhook returns 404."
    )
    assert "from dashboard_api.alertmanager_webhook import router" in main_py


def test_digest_helper_exists():
    assert "def send_alertmanager_digest(" in EMAIL_ALERTS.read_text(), (
        "email_alerts.py must expose send_alertmanager_digest — the "
        "webhook delegates to it and the existing SMTP retry path."
    )


# -----------------------------------------------------------------------------
# Webhook runtime behavior
# -----------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, headers: dict, body: dict | str | None):
        self.headers = {k.lower(): v for k, v in headers.items()}
        self._body = body

        class _Client:
            host = "10.100.0.3"
        self.client = _Client()

    async def json(self):
        if isinstance(self._body, str):
            return json.loads(self._body)
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _payload(n: int = 1, severity: str = "sev1", status: str = "firing") -> dict:
    return {
        "version": "4",
        "groupKey": "test",
        "status": status,
        "receiver": "osiriscare-email",
        "commonLabels": {"alertname": "EvidenceChainStalled", "severity": severity},
        "alerts": [
            {
                "status": status,
                "labels": {
                    "alertname": "EvidenceChainStalled",
                    "severity": severity,
                    "invariant_name": "evidence_chain_stalled",
                    "site_id": f"site-{i}",
                },
                "annotations": {
                    "summary": "evidence insert stalled",
                    "runbook_url": "https://www.osiriscare.net/admin/substrate-health",
                },
                "startsAt": "2026-04-18T19:00:00Z",
            }
            for i in range(n)
        ],
    }


def test_missing_env_token_returns_503():
    wh = _load_webhook()
    from fastapi import HTTPException

    env = {k: v for k, v in os.environ.items() if k != "ALERTMANAGER_WEBHOOK_TOKEN"}
    with patch.dict(os.environ, env, clear=True):
        req = _FakeRequest({"x-alertmanager-token": "anything"}, _payload())
        with pytest.raises(HTTPException) as exc:
            asyncio.run(wh.alertmanager_webhook(req))
    assert exc.value.status_code == 503


def test_missing_header_token_returns_401():
    wh = _load_webhook()
    from fastapi import HTTPException

    with patch.dict(os.environ, {"ALERTMANAGER_WEBHOOK_TOKEN": "valid"}):
        req = _FakeRequest({}, _payload())
        with pytest.raises(HTTPException) as exc:
            asyncio.run(wh.alertmanager_webhook(req))
    assert exc.value.status_code == 401


def test_bad_header_token_returns_401():
    wh = _load_webhook()
    from fastapi import HTTPException

    with patch.dict(os.environ, {"ALERTMANAGER_WEBHOOK_TOKEN": "valid"}):
        req = _FakeRequest({"x-alertmanager-token": "wrong"}, _payload())
        with pytest.raises(HTTPException) as exc:
            asyncio.run(wh.alertmanager_webhook(req))
    assert exc.value.status_code == 401


def test_valid_request_delegates_to_digest():
    wh = _load_webhook()
    stub = MagicMock(return_value=True)
    original = wh.send_alertmanager_digest
    try:
        wh.send_alertmanager_digest = stub
        with patch.dict(
            os.environ,
            {
                "ALERTMANAGER_WEBHOOK_TOKEN": "valid",
                "ALERTMANAGER_RECIPIENTS": "jbouey@osiriscare.net",
            },
        ):
            req = _FakeRequest(
                {"x-alertmanager-token": "valid"},
                _payload(n=3),
            )
            result = asyncio.run(wh.alertmanager_webhook(req))
    finally:
        wh.send_alertmanager_digest = original
    assert result == {"accepted": 3, "sent": True}
    stub.assert_called_once()
    sent_payload, sent_recipients = stub.call_args[0]
    assert sent_recipients == ["jbouey@osiriscare.net"]
    assert len(sent_payload["alerts"]) == 3


def test_valid_request_authorization_bearer():
    """Alertmanager's native http_config.authorization form.

    AM sends `Authorization: Bearer <token>`. The webhook must accept
    this as equivalent to X-Alertmanager-Token so we don't need a
    custom headers fork.
    """
    wh = _load_webhook()
    stub = MagicMock(return_value=True)
    original = wh.send_alertmanager_digest
    try:
        wh.send_alertmanager_digest = stub
        with patch.dict(
            os.environ,
            {
                "ALERTMANAGER_WEBHOOK_TOKEN": "am-token",
                "ALERTMANAGER_RECIPIENTS": "jbouey@osiriscare.net",
            },
        ):
            req = _FakeRequest(
                {"authorization": "Bearer am-token"},
                _payload(n=1),
            )
            result = asyncio.run(wh.alertmanager_webhook(req))
    finally:
        wh.send_alertmanager_digest = original
    assert result == {"accepted": 1, "sent": True}


def test_no_recipients_returns_sent_false():
    wh = _load_webhook()
    stub = MagicMock(return_value=True)
    original = wh.send_alertmanager_digest
    env = {k: v for k, v in os.environ.items() if not k.startswith("ALERT")}
    env["ALERTMANAGER_WEBHOOK_TOKEN"] = "valid"
    try:
        wh.send_alertmanager_digest = stub
        with patch.dict(os.environ, env, clear=True):
            req = _FakeRequest({"x-alertmanager-token": "valid"}, _payload())
            result = asyncio.run(wh.alertmanager_webhook(req))
    finally:
        wh.send_alertmanager_digest = original
    assert result["sent"] is False
    assert result["reason"] == "no_recipients"
    stub.assert_not_called()


def test_recipients_are_comma_parsed():
    wh = _load_webhook()
    stub = MagicMock(return_value=True)
    original = wh.send_alertmanager_digest
    try:
        wh.send_alertmanager_digest = stub
        with patch.dict(
            os.environ,
            {
                "ALERTMANAGER_WEBHOOK_TOKEN": "valid",
                "ALERTMANAGER_RECIPIENTS": "a@b.com, c@d.com ,,e@f.com",
            },
        ):
            req = _FakeRequest({"x-alertmanager-token": "valid"}, _payload())
            asyncio.run(wh.alertmanager_webhook(req))
    finally:
        wh.send_alertmanager_digest = original
    _, sent_recipients = stub.call_args[0]
    assert sent_recipients == ["a@b.com", "c@d.com", "e@f.com"]


# -----------------------------------------------------------------------------
# Digest helper behavior (no SMTP — mock _send_smtp_with_retry)
# -----------------------------------------------------------------------------


def test_digest_subject_includes_severity_and_status():
    em = _load_email_alerts()
    sent = []
    orig_configured = em.is_email_configured
    orig_send = em._send_smtp_with_retry
    try:
        em.is_email_configured = lambda: True
        em._send_smtp_with_retry = lambda msg, r, label=None, **_: (sent.append(msg), True)[1]
        ok = em.send_alertmanager_digest(
            _payload(n=2, severity="sev1", status="firing"),
            ["jbouey@osiriscare.net"],
        )
    finally:
        em.is_email_configured = orig_configured
        em._send_smtp_with_retry = orig_send
    assert ok is True
    assert len(sent) == 1
    subject = sent[0]["Subject"]
    # Subject leads with severity + status + a human-meaningful identifier.
    # After the 2026-04-22 readability redesign the identifier is the
    # invariant_name from the alert labels (what operators say out loud),
    # not the generic alertname bucket like "SubstrateInvariantSev2".
    assert "SEV1" in subject and "FIRING" in subject
    # Two alerts on two sites: "2 invariants across 2 sites"
    assert "2 invariants" in subject
    assert "across 2 sites" in subject


def test_digest_subject_single_alert_names_invariant_and_site():
    """Most common real-world pattern: one invariant failing on one site.
    Subject MUST name both — a subject like 'SubstrateInvariantSev2 — 1
    alert(s)' forces the operator to open the email to know what's
    broken. That regression is what the 2026-04-22 redesign closed."""
    em = _load_email_alerts()
    sent = []
    orig_configured = em.is_email_configured
    orig_send = em._send_smtp_with_retry
    try:
        em.is_email_configured = lambda: True
        em._send_smtp_with_retry = lambda msg, r, label=None, **_: (sent.append(msg), True)[1]
        em.send_alertmanager_digest(
            _payload(n=1, severity="sev2", status="firing"),
            ["jbouey@osiriscare.net"],
        )
    finally:
        em.is_email_configured = orig_configured
        em._send_smtp_with_retry = orig_send
    subject = sent[0]["Subject"]
    assert "SEV2" in subject and "FIRING" in subject
    assert "evidence_chain_stalled" in subject  # the invariant_name label
    assert "site-0" in subject                   # the site_id label


def test_digest_resolved_status():
    em = _load_email_alerts()
    sent = []
    orig_configured = em.is_email_configured
    orig_send = em._send_smtp_with_retry
    try:
        em.is_email_configured = lambda: True
        em._send_smtp_with_retry = lambda msg, r, label=None, **_: (sent.append(msg), True)[1]
        ok = em.send_alertmanager_digest(
            _payload(n=1, severity="sev2", status="resolved"),
            ["jbouey@osiriscare.net"],
        )
    finally:
        em.is_email_configured = orig_configured
        em._send_smtp_with_retry = orig_send
    assert ok is True
    assert "RESOLVED" in sent[0]["Subject"]


def test_digest_body_leads_with_human_summary_and_triage_link():
    """Body MUST be scannable in the first 5 seconds:
      - 'FIRING' or 'RESOLVED' headline before any AM metadata
      - single 'Triage:' link so the operator has one place to click
      - invariant-level one-liners before the raw payload dump
    Regression sentinel for the 2026-04-22 redesign.
    """
    em = _load_email_alerts()
    sent = []
    orig_configured = em.is_email_configured
    orig_send = em._send_smtp_with_retry
    try:
        em.is_email_configured = lambda: True
        em._send_smtp_with_retry = lambda msg, r, label=None, **_: (sent.append(msg), True)[1]
        em.send_alertmanager_digest(
            _payload(n=2, severity="sev2", status="firing"),
            ["jbouey@osiriscare.net"],
        )
    finally:
        em.is_email_configured = orig_configured
        em._send_smtp_with_retry = orig_send

    msg = sent[0]
    text_part = next(
        p for p in msg.walk() if p.get_content_type() == "text/plain"
    ).get_payload(decode=True).decode("utf-8")
    html_part = next(
        p for p in msg.walk() if p.get_content_type() == "text/html"
    ).get_payload(decode=True).decode("utf-8")

    # Plaintext: headline is the FIRST non-empty line (not "Receiver:", not
    # "Group key:"). The original renderer led with AM metadata.
    first_line = text_part.strip().split("\n", 1)[0]
    assert first_line.startswith("FIRING"), (
        f"Plaintext must lead with FIRING/RESOLVED headline, "
        f"got first line: {first_line!r}"
    )
    assert "Triage:" in text_part
    assert "/admin/substrate-health" in text_part
    # Invariants appear BEFORE the raw payload section.
    inv_idx = text_part.find("Invariants:")
    raw_idx = text_part.find("Raw Alertmanager payload")
    assert 0 <= inv_idx < raw_idx, (
        "Invariants list must come before the raw forensics block."
    )
    # The actual invariant names are visible.
    assert "evidence_chain_stalled" in text_part

    # HTML: has the Triage CTA button + the raw payload in <details>.
    assert "Triage in Substrate Health" in html_part
    assert "<details" in html_part and "Raw Alertmanager payload" in html_part
    # Invariants table is present.
    assert "Invariant" in html_part and "Site" in html_part


def test_digest_skips_when_email_not_configured():
    em = _load_email_alerts()
    orig = em.is_email_configured
    try:
        em.is_email_configured = lambda: False
        ok = em.send_alertmanager_digest(_payload(), ["x@y.com"])
    finally:
        em.is_email_configured = orig
    assert ok is False


def test_digest_skips_when_no_recipients():
    em = _load_email_alerts()
    orig = em.is_email_configured
    try:
        em.is_email_configured = lambda: True
        ok = em.send_alertmanager_digest(_payload(), [])
    finally:
        em.is_email_configured = orig
    assert ok is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
