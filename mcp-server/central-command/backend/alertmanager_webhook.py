"""Alertmanager webhook receiver.

POST /api/admin/alertmanager-webhook

Called by the Prometheus Alertmanager running on the Vault host
(10.100.0.3) when a substrate-invariant alert fires or resolves.

Auth: shared-secret via X-Alertmanager-Token header. Token lives in
env only (ALERTMANAGER_WEBHOOK_TOKEN) — not in DB, not in git. Compared
with hmac.compare_digest to avoid timing oracles.

Recipients: ALERTMANAGER_RECIPIENTS env (comma-separated), falling
back to ALERT_EMAIL. No recipient = 200 with sent=False + logged.

Payload: standard Alertmanager v4 webhook JSON. Forwarded to
email_alerts.send_alertmanager_digest which builds one email per POST
using the existing hardened SMTP retry path.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .email_alerts import send_alertmanager_digest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["alertmanager"])


def _expected_token() -> str:
    return os.getenv("ALERTMANAGER_WEBHOOK_TOKEN", "").strip()


def _recipients() -> list[str]:
    raw = os.getenv("ALERTMANAGER_RECIPIENTS") or os.getenv("ALERT_EMAIL") or ""
    return [r.strip() for r in raw.split(",") if r.strip()]


@router.post("/alertmanager-webhook")
async def alertmanager_webhook(request: Request) -> dict[str, Any]:
    """Receive an Alertmanager webhook POST and forward as email digest.

    Returns 503 if token env is unset (so AM retries instead of silently
    dropping alerts against a half-configured server), 401 on bad/missing
    token, 400 on malformed JSON, 200 on success.
    """
    expected = _expected_token()
    if not expected:
        raise HTTPException(503, detail="Alertmanager webhook not configured")

    # Accept either `Authorization: Bearer <token>` (Alertmanager's
    # native http_config.authorization) or `X-Alertmanager-Token`
    # (curl-friendly custom header for manual tests).
    submitted = ""
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        submitted = auth_header[7:].strip()
    if not submitted:
        submitted = request.headers.get("x-alertmanager-token", "").strip()

    if not submitted or not hmac.compare_digest(submitted, expected):
        logger.warning(
            "alertmanager_webhook_auth_failure",
            extra={"remote": request.client.host if request.client else "?"},
        )
        raise HTTPException(401, detail="Invalid token")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, detail="Invalid JSON payload")

    if not isinstance(payload, dict):
        raise HTTPException(400, detail="Payload must be a JSON object")

    alerts = payload.get("alerts")
    if not isinstance(alerts, list):
        raise HTTPException(400, detail="Payload.alerts must be a list")

    recipients = _recipients()
    if not recipients:
        logger.error("alertmanager_webhook_no_recipients", extra={"alerts": len(alerts)})
        return {"accepted": len(alerts), "sent": False, "reason": "no_recipients"}

    sent = send_alertmanager_digest(payload, recipients)
    if not sent:
        logger.error(
            "alertmanager_webhook_send_failed",
            extra={"alerts": len(alerts), "recipients_count": len(recipients)},
        )
    return {"accepted": len(alerts), "sent": sent}
