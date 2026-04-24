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

try:
    from . import auth as auth_module
    from .shared import check_rate_limit, execute_with_retry, get_db
except ImportError:  # direct-module import (test path — no package context)
    import auth as auth_module  # type: ignore[no-redef]
    from shared import check_rate_limit, execute_with_retry, get_db  # type: ignore[no-redef]

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


@router.post("/client-field-undefined", status_code=202)
async def record_field_undefined(
    event: ClientFieldUndefinedEvent,
    request: Request,
    user: dict = Depends(auth_module.require_auth),
    db = Depends(get_db),
) -> dict:
    """Record a single FIELD_UNDEFINED event. Returns 202 Accepted.

    202 matches the best-effort async-ingest semantic (telemetry may or
    may not persist on the path through the DB write). FastAPI disallows
    a return-body annotation on 204 No Content, so 202 is the cleaner
    status code here.

    Never raises on bad input beyond what Pydantic catches — telemetry
    failure must not break the frontend. Worst-case: event is dropped,
    substrate invariant sees fewer events, spike doesn't fire, operator
    gets no alert. That's acceptable; the alternative (5xx from this
    endpoint breaking an already-broken page) is worse.
    """
    # Rate limit — 100 events/minute per authenticated user. Session 210
    # round-table #4: session-auth'd endpoint but uncapped = an attacker
    # (or a badly-behaved frontend loop) could fill client_telemetry_events.
    # 100/min is generous given apiFieldGuard dedups at 60s per
    # (endpoint, field) per browser, so a legitimate single user emits
    # at most ~dozens per minute even during a chaotic contract drift.
    user_key = f"user:{user.get('id') or user.get('user_id') or 'anon'}"
    allowed, retry_after = await check_rate_limit(
        user_key,
        "client_field_undefined",
        window_seconds=60,
        max_requests=100,
    )
    if not allowed:
        # Telemetry endpoints shouldn't hard-reject — just log and drop.
        # Returning 429 would break the frontend's UI (apiFieldGuard
        # catches errors but still, less noise is better). Silently
        # discard by early return with the normal 202.
        logger.warning(
            "client_telemetry_rate_limited",
            extra={"user_key": user_key, "retry_after": retry_after},
        )
        return {"accepted": False, "reason": "rate_limited"}

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
        # but return 202 to the client anyway — the whole point is that
        # a failed telemetry write can't cascade into a broken UX.
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
    return {"accepted": True}
