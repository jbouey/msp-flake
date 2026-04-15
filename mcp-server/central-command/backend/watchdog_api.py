"""watchdog_api.py — Session 207 Phase W0.

Backend surface for the appliance-watchdog service. The watchdog is a
second systemd unit on each appliance with its own Ed25519 identity,
its own 2-minute checkin loop, and a tight whitelist of 6 fleet-order
types that can recover a wedged main daemon without requiring SSH:

    watchdog_restart_daemon      `systemctl restart appliance-daemon`
    watchdog_refetch_config      re-download /var/lib/msp/config.yaml
    watchdog_reset_pin_store     delete /var/lib/msp/winrm_pins.json
    watchdog_reset_api_key       trigger the provisioning rekey flow
    watchdog_redeploy_daemon     re-download + install daemon binary
    watchdog_collect_diagnostics bundle journal + state, POST back

The watchdog authenticates via `require_appliance_bearer` using its
own `<appliance_id>-watchdog` bearer — the one-active-key trigger on
api_keys treats this as a distinct bucket from the main daemon's key.

Two endpoints:

    POST /api/watchdog/checkin
        Reports watchdog liveness + main_daemon_status every 2 min.
        Writes an append-only row into watchdog_events. Returns the
        list of pending watchdog_* orders (filtered by appliance_id).

    POST /api/watchdog/diagnostics
        Uploads a diagnostic bundle produced by
        watchdog_collect_diagnostics (or manually). Stored under
        watchdog_events.event_type='diagnostics_uploaded' with the
        payload inline so the operator can see it on the dashboard.

The watchdog does NOT share fleet-order consumption with the main
daemon — the watchdog only reads orders whose
`parameters->>'appliance_id'` matches its `<aid>-watchdog` suffix, and
the main daemon only reads orders matching its own `<aid>` (no
suffix). Independent surfaces, single fleet_orders table.

Pairs with:
    - Migration 217 (watchdog_events ledger)
    - Migration 218 (v_privileged_types extension for watchdog_* types)
    - assertions.py invariants:
        watchdog_silent            sev1 — no checkin in 10 min
        watchdog_reports_daemon_down  sev2 — watchdog alive, daemon not
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .fleet import get_pool
from .tenant_middleware import admin_connection
from .shared import require_appliance_bearer_full

logger = logging.getLogger("watchdog_api")

watchdog_api_router = APIRouter(prefix="/api/watchdog", tags=["watchdog"])


# ─── Pydantic models ──────────────────────────────────────────────────


class WatchdogCheckinRequest(BaseModel):
    site_id: str = Field(..., min_length=1, max_length=64)
    appliance_id: str = Field(..., min_length=1, max_length=255)
    watchdog_version: str = Field(default="0.1.0")
    main_daemon_status: str = Field(..., description="active|inactive|failed|unknown")
    main_daemon_substate: Optional[str] = None
    boot_time: Optional[datetime] = None
    uptime_seconds: Optional[int] = None
    wall_time: Optional[datetime] = None
    last_main_checkin_attempt: Optional[datetime] = None


class WatchdogDiagnosticsRequest(BaseModel):
    site_id: str = Field(..., min_length=1, max_length=64)
    appliance_id: str = Field(..., min_length=1, max_length=255)
    order_id: Optional[str] = Field(default=None, description="UUID if diag was triggered by an order")
    bundle: Dict[str, Any] = Field(default_factory=dict)


class WatchdogOrderComplete(BaseModel):
    order_id: str = Field(..., min_length=36, max_length=36)
    order_type: str = Field(..., min_length=1, max_length=60)
    status: str = Field(..., pattern="^(success|failure)$")
    output: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


class WatchdogBootstrapRequest(BaseModel):
    """Main daemon calls this on startup when its /etc/msp-watchdog.yaml
    is absent/empty. No body fields required — the bearer's site_id AND
    appliance_id are authoritative (bearer_aid is the main daemon's
    canonical id; backend derives watchdog_aid as '<bearer_aid>-watchdog').
    Returns plaintext key so the main daemon can write the config file."""
    pass


# ─── Helpers ──────────────────────────────────────────────────────────


def _enforce_watchdog_id(
    bearer_site: str, bearer_aid: Optional[str],
    request_site: str, request_aid: str,
) -> None:
    """Per-appliance bearer guard. All four conditions must hold:

      1. bearer's site_id matches the request body's site_id
      2. request body's appliance_id ends in '-watchdog'
         (otherwise the main daemon's bearer is being reused for the
         watchdog surface — wrong whitelist)
      3. bearer_aid is NOT None (legacy site-level keys cannot post
         to /api/watchdog/*; only per-appliance keys issued to the
         watchdog itself)
      4. bearer_aid == request.appliance_id
         (you cannot claim to be an appliance other than the one your
         bearer is bound to — closes the Phase-W-gate security bug
         where a compromised main-daemon bearer could poison
         watchdog_events for ghost appliances)
    """
    if bearer_site != request_site:
        raise HTTPException(status_code=403, detail="auth_site_id mismatch")
    if not request_aid.endswith("-watchdog"):
        raise HTTPException(
            status_code=403,
            detail="appliance_id must end in '-watchdog' (watchdog-only surface)",
        )
    if not bearer_aid:
        raise HTTPException(
            status_code=403,
            detail="bearer is site-level; watchdog surface requires per-appliance bearer",
        )
    if bearer_aid != request_aid:
        raise HTTPException(
            status_code=403,
            detail=f"bearer_aid {bearer_aid!r} != request appliance_id {request_aid!r}",
        )


def _chain_hash(prev: Optional[str], payload: Dict[str, Any]) -> str:
    """SHA-256 over `prev_hash:canonical_json` — same convention as
    identity_chain.chain_hash. Used per (appliance_id) so each watchdog's
    event log is a self-contained chain."""
    prev_hex = prev or ("0" * 64)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(prev_hex.encode() + b":" + canonical).hexdigest()


async def _fetch_prev_hash(conn, appliance_id: str) -> Optional[str]:
    row = await conn.fetchrow(
        "SELECT chain_hash FROM watchdog_events "
        "WHERE appliance_id = $1 AND chain_hash IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        appliance_id,
    )
    return row["chain_hash"] if row else None


async def _append_event(
    conn,
    site_id: str,
    appliance_id: str,
    event_type: str,
    payload: Dict[str, Any],
    order_id: Optional[str] = None,
    watchdog_order_type: Optional[str] = None,
) -> str:
    """Append an event to the per-appliance hash chain. Each sibling
    transaction (savepoint) so a chain-hash collision doesn't poison the
    outer request."""
    async with conn.transaction():
        prev = await _fetch_prev_hash(conn, appliance_id)
        h = _chain_hash(prev, payload)
        await conn.execute(
            """
            INSERT INTO watchdog_events (
                site_id, appliance_id, event_type, order_id,
                watchdog_order_type, payload, chain_prev_hash, chain_hash
            ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
            """,
            site_id,
            appliance_id,
            event_type,
            order_id,
            watchdog_order_type,
            json.dumps(payload),
            prev,
            h,
        )
    return h


async def _pending_watchdog_orders(conn, site_id: str, watchdog_aid: str) -> List[Dict[str, Any]]:
    """Orders targeting this watchdog that are still active + not yet
    acked by this appliance. Mirror of /api/orders/pending but scoped to
    watchdog_* order_types AND the -watchdog appliance_id."""
    rows = await conn.fetch(
        """
        SELECT fo.id::text AS order_id,
               fo.order_type,
               fo.parameters,
               fo.created_at
          FROM fleet_orders fo
         WHERE fo.status = 'active'
           AND fo.order_type LIKE 'watchdog\\_%'
           AND fo.parameters->>'site_id' = $1
           AND COALESCE(fo.parameters->>'appliance_id', '') = $2
           AND NOT EXISTS (
               SELECT 1 FROM fleet_order_completions foc
                WHERE foc.fleet_order_id = fo.id
                  AND foc.appliance_id = $2
           )
         ORDER BY fo.created_at ASC
         LIMIT 20
        """,
        site_id,
        watchdog_aid,
    )
    return [
        {
            "order_id": r["order_id"],
            "order_type": r["order_type"],
            "parameters": r["parameters"] if isinstance(r["parameters"], dict) else json.loads(r["parameters"] or "{}"),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


# ─── Endpoints ────────────────────────────────────────────────────────


@watchdog_api_router.post("/checkin")
async def watchdog_checkin(
    req: WatchdogCheckinRequest,
    request: Request,
    bearer: tuple = Depends(require_appliance_bearer_full),
) -> Dict[str, Any]:
    """2-minute heartbeat from the appliance-watchdog service. Records a
    `checkin` event in watchdog_events (hash-chained), returns the list
    of pending watchdog_* orders."""
    bearer_site, bearer_aid = bearer
    _enforce_watchdog_id(bearer_site, bearer_aid, req.site_id, req.appliance_id)

    client_ip = request.client.host if request.client else None
    payload = {
        "watchdog_version": req.watchdog_version,
        "main_daemon_status": req.main_daemon_status,
        "main_daemon_substate": req.main_daemon_substate,
        "uptime_seconds": req.uptime_seconds,
        "last_main_checkin_attempt": (
            req.last_main_checkin_attempt.isoformat() if req.last_main_checkin_attempt else None
        ),
        "client_ip": client_ip,
    }

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        await _append_event(conn, req.site_id, req.appliance_id, "checkin", payload)
        pending = await _pending_watchdog_orders(conn, req.site_id, req.appliance_id)

    return {
        "ok": True,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "pending_orders": pending,
    }


@watchdog_api_router.post("/diagnostics")
async def watchdog_diagnostics(
    req: WatchdogDiagnosticsRequest,
    bearer: tuple = Depends(require_appliance_bearer_full),
) -> Dict[str, Any]:
    """Diagnostic bundle upload. Either triggered by a
    `watchdog_collect_diagnostics` order (with order_id) or posted ad-hoc
    on startup / error. Payload is stored inline on watchdog_events so
    the operator can read it from the dashboard without pulling logs."""
    bearer_site, bearer_aid = bearer
    _enforce_watchdog_id(bearer_site, bearer_aid, req.site_id, req.appliance_id)

    payload_shape = {"order_id": req.order_id, "bundle_keys": sorted(list((req.bundle or {}).keys()))}
    logger.info("watchdog diagnostics uploaded site=%s aid=%s keys=%s",
                req.site_id, req.appliance_id, payload_shape["bundle_keys"])

    # Truncate oversized bundles before we persist — a rogue watchdog
    # shouldn't be able to grow watchdog_events unboundedly.
    truncated_bundle = _truncate(req.bundle, max_bytes=256 * 1024)

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        chain = await _append_event(
            conn,
            req.site_id,
            req.appliance_id,
            "diagnostics_uploaded",
            {"bundle": truncated_bundle},
            order_id=req.order_id,
        )
    return {"ok": True, "chain_hash": chain}


@watchdog_api_router.post("/orders/{order_id}/complete")
async def watchdog_order_complete(
    order_id: str,
    req: WatchdogOrderComplete,
    bearer: tuple = Depends(require_appliance_bearer_full),
) -> Dict[str, Any]:
    bearer_site, bearer_aid = bearer
    """Watchdog acknowledges an order's outcome. Writes the completion
    row (reuses existing fleet_order_completions) AND records an event
    in watchdog_events so the operator sees the watchdog-specific chain
    alongside the regular fleet-order completion history.
    """
    if req.order_id != order_id:
        raise HTTPException(status_code=400, detail="path order_id ≠ body order_id")

    if not req.order_type.startswith("watchdog_"):
        raise HTTPException(
            status_code=400,
            detail=f"order_type {req.order_type!r} is not a watchdog_* type",
        )

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        order_row = await conn.fetchrow(
            "SELECT parameters FROM fleet_orders WHERE id = $1",
            order_id,
        )
        if not order_row:
            raise HTTPException(status_code=404, detail="order_id not found")
        params = order_row["parameters"] if isinstance(order_row["parameters"], dict) else json.loads(order_row["parameters"] or "{}")
        aid = params.get("appliance_id", "")
        site_id = params.get("site_id", "")
        if bearer_site != site_id:
            raise HTTPException(status_code=403, detail="auth_site_id ≠ order site")
        if not aid.endswith("-watchdog"):
            raise HTTPException(
                status_code=403,
                detail="order not targeted at a -watchdog appliance_id",
            )
        if not bearer_aid or bearer_aid != aid:
            raise HTTPException(
                status_code=403,
                detail=f"bearer_aid {bearer_aid!r} != order target {aid!r}",
            )

        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO fleet_order_completions
                    (fleet_order_id, appliance_id, status, output, error_message)
                VALUES ($1, $2, $3, $4::jsonb, $5)
                ON CONFLICT (fleet_order_id, appliance_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    output = EXCLUDED.output,
                    error_message = EXCLUDED.error_message,
                    updated_at = NOW()
                """,
                order_id,
                aid,
                req.status,
                json.dumps(req.output or {}),
                req.error_message,
            )

            event_type = "order_executed" if req.status == "success" else "order_failed"
            await _append_event(
                conn,
                site_id,
                aid,
                event_type,
                {"status": req.status, "output": req.output, "error_message": req.error_message},
                order_id=order_id,
                watchdog_order_type=req.order_type,
            )

    return {"ok": True}


@watchdog_api_router.post("/bootstrap")
async def watchdog_bootstrap(
    req: WatchdogBootstrapRequest,
    bearer: tuple = Depends(require_appliance_bearer_full),
) -> Dict[str, Any]:
    """Phase W1.1 — main daemon provisions a watchdog api_key for itself.

    Auth: MAIN daemon's bearer. Bearer_aid is authoritative — the
    caller's claim is ignored. Backend derives watchdog_aid =
    '<bearer_aid>-watchdog' so cross-appliance bootstrap is
    impossible by construction. Mints a fresh 32-byte urlsafe secret,
    hashes it, INSERTs with active=true. Migration 209 trigger
    auto-deactivates any prior active watchdog key in the same bucket
    and writes the structured api_key audit row.

    The plaintext key is never readable after this response — lose it
    and you re-bootstrap (safe because the re-bootstrap auto-deactivates
    the lost one and the operator sees the rotation in admin_audit_log).
    """
    bearer_site, bearer_aid = bearer
    if not bearer_aid:
        raise HTTPException(
            status_code=403,
            detail="bootstrap requires per-appliance bearer (not site-level)",
        )
    if bearer_aid.endswith("-watchdog"):
        raise HTTPException(
            status_code=400,
            detail="bootstrap must be called by the MAIN daemon bearer, not the watchdog's",
        )

    watchdog_aid = f"{bearer_aid}-watchdog"
    new_key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(new_key.encode()).hexdigest()
    key_prefix = new_key[:8]

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # Migration 209 trigger auto-deactivates any prior active row
        # for this (site_id, appliance_id) bucket + writes audit entry.
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO api_keys
                    (site_id, key_hash, key_prefix, description,
                     active, appliance_id)
                VALUES ($1, $2, $3, $4, true, $5)
                """,
                bearer_site,
                key_hash,
                key_prefix,
                f"watchdog bearer for {bearer_aid}",
                watchdog_aid,
            )

    logger.info(
        "watchdog bootstrap minted key site=%s main_aid=%s watchdog_aid=%s prefix=%s",
        bearer_site, bearer_aid, watchdog_aid, key_prefix,
    )

    return {
        "site_id": bearer_site,
        "appliance_id": watchdog_aid,
        "api_key": new_key,
        "api_endpoint": "https://api.osiriscare.net",
    }


def _truncate(obj: Any, max_bytes: int) -> Any:
    """Best-effort size clamp so a watchdog can't balloon watchdog_events.
    Serialize, check length; if oversized, replace with a summary."""
    try:
        blob = json.dumps(obj, default=str)
    except Exception:
        return {"truncated": True, "reason": "unserializable"}
    if len(blob) <= max_bytes:
        return obj
    return {
        "truncated": True,
        "original_bytes": len(blob),
        "max_bytes": max_bytes,
        "preview": blob[:4096],
    }
