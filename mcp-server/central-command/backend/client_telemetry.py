"""client_telemetry.py — ingest browser-side contract-drift events.

Session 210 (2026-04-24) Layer 3 of enterprise API reliability. The
frontend's `apiFieldGuard.ts` emits a `FIELD_UNDEFINED` event when code
reads an expected field that's undefined — i.e., the backend contract
has drifted relative to what the frontend expects.

Events aggregate here. The `frontend_field_undefined_spike` substrate
invariant (assertions.py) reads from `client_telemetry_events` and
fires sev2 when > FIELD_UNDEFINED_SPIKE_THRESHOLD events land in a
5-minute window. That's the operator's signal to investigate which
endpoint+field pair drifted.

Endpoint: POST /api/admin/telemetry/client-field-undefined
Auth:     session cookie (same origin). Request originates from our
          own dashboard, so plain require_auth is sufficient.
CSRF:     standard (via X-CSRF-Token header, checked by CSRFMiddleware).

Payload shape (from frontend):
    {
      "kind": "FIELD_UNDEFINED",
      "endpoint": "/api/portal/site/{id}",
      "field": "tier",
      "component": "PortalScorecard",   // optional
      "observed_type": "object",         // typeof obj on frontend
      "page": "/client/portal",
      "ts": "2026-04-24T16:30:00Z"
    }

Storage: `client_telemetry_events` (new table, migration ships with this
endpoint). Append-only, partitioned by day, retention-friendly (auto-prune
after 30 days).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from . import auth as auth_module
from .shared import execute_with_retry, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/telemetry", tags=["telemetry"])


class ClientFieldUndefinedEvent(BaseModel):
    """Payload from frontend's apiFieldGuard.requireField when a field
    is undefined on an API response."""

    kind: str = Field(pattern=r"^FIELD_UNDEFINED$")
    endpoint: str = Field(max_length=200)
    field: str = Field(max_length=100)
    component: Optional[str] = Field(default=None, max_length=100)
    observed_type: str = Field(max_length=30)
    page: str = Field(default="", max_length=200)
    ts: str = Field(max_length=40)  # ISO 8601 from client clock


@router.post("/client-field-undefined", status_code=204)
async def record_field_undefined(
    event: ClientFieldUndefinedEvent,
    request: Request,
    user: dict = Depends(auth_module.require_auth),
    db = Depends(get_db),
) -> None:
    """Record a single FIELD_UNDEFINED event. Returns 204 No Content.

    Never raises on bad input beyond what Pydantic catches — telemetry
    failure must not break the frontend. Worst-case: event is dropped,
    substrate invariant sees fewer events, spike doesn't fire, operator
    gets no alert. That's acceptable; the alternative (5xx from this
    endpoint breaking an already-broken page) is worse.
    """
    # Parse client-provided timestamp defensively. A bad clock on the
    # client is not our problem — fall back to server NOW().
    client_ts: Optional[datetime] = None
    try:
        client_ts = datetime.fromisoformat(event.ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass

    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")[:200]

    try:
        await execute_with_retry(
            db,
            text(
                """
                INSERT INTO client_telemetry_events
                    (event_kind, endpoint, field_name, component,
                     observed_type, page, client_ts, recorded_at,
                     user_id, ip_address, user_agent)
                VALUES
                    (:kind, :endpoint, :field, :component,
                     :observed_type, :page, :client_ts, NOW(),
                     :user_id, :ip, :ua)
                """
            ),
            {
                "kind": event.kind,
                "endpoint": event.endpoint,
                "field": event.field,
                "component": event.component,
                "observed_type": event.observed_type,
                "page": event.page,
                "client_ts": client_ts,
                "user_id": user.get("id") or user.get("user_id"),
                "ip": ip,
                "ua": ua,
            },
        )
        await db.commit()
    except Exception:
        # Telemetry ingest failure must not break the dashboard. Log
        # as ERROR (per CLAUDE.md "No silent write failures" rule)
        # but return 204 to the client anyway.
        logger.error(
            "client_telemetry_ingest_failed",
            exc_info=True,
            extra={
                "endpoint": event.endpoint,
                "field": event.field,
                "page": event.page,
            },
        )
        await db.rollback()
