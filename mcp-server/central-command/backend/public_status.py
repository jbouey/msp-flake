"""
Public status page endpoints (#144 P2).

Unauthenticated route at /api/public/status/{slug} returns a green/amber/red
snapshot of a site's appliances from the appliance_status_rollup MV. Slug
is opt-in per site (sites.public_status_slug) so customers can share or
keep private.

Builds external verification pressure — if the public page says "online"
while the appliance is actually down, the customer catches us. Conversely,
if we say "offline" honestly, the customer trusts the page.

Privacy: no MAC addresses, no IPs, no hostnames. Only a display_name and
a status color. Hostnames could leak PHI-adjacent info (e.g.
PATIENT-ROOM-201-PC); strip to a generic label.
"""

from __future__ import annotations
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .fleet import get_pool
from .tenant_middleware import admin_connection
from .auth import require_admin

logger = logging.getLogger(__name__)

public_status_router = APIRouter(tags=["public-status"])
admin_status_router = APIRouter(prefix="/api/admin/sites", tags=["admin-status"])


def _sanitize_label(display_name: Optional[str], hostname: Optional[str],
                    appliance_id: str, idx: int) -> str:
    """Customer-facing label for an appliance on the public page. Intentionally
    generic to avoid leaking PHI-adjacent info like patient-named hostnames."""
    if display_name:
        safe = display_name.strip()
        if safe and len(safe) <= 40:
            return safe
    # Fall back to a numeric label. Keep it boring on purpose.
    return f"Appliance {idx + 1}"


@public_status_router.get("/api/public/status/{slug}")
async def public_status(slug: str, request: Request) -> Dict[str, Any]:
    """Unauthenticated site status page. Returns sanitized appliance
    health for a single site matched by an unguessable slug."""
    if not slug or len(slug) < 16:
        raise HTTPException(status_code=404, detail="Not found")

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        site_row = await conn.fetchrow(
            """
            SELECT site_id, clinic_name
            FROM sites
            WHERE public_status_slug = $1
            """,
            slug,
        )
        if not site_row:
            # Deliberately 404 with no detail — do not leak whether the
            # slug looks valid, to prevent enumeration.
            raise HTTPException(status_code=404, detail="Not found")

        site_id = site_row["site_id"]
        rows = await conn.fetch(
            """
            SELECT appliance_id, hostname, display_name, live_status,
                   last_heartbeat_at, stale_seconds, uptime_ratio_24h,
                   checkin_count_24h
            FROM appliance_status_rollup
            WHERE site_id = $1
            ORDER BY appliance_id
            """,
            site_id,
        )

    appliances: List[Dict[str, Any]] = []
    totals = {"online": 0, "stale": 0, "offline": 0}
    for i, r in enumerate(rows):
        status = r["live_status"]
        totals[status] = totals.get(status, 0) + 1
        appliances.append(
            {
                "label": _sanitize_label(
                    r["display_name"], r["hostname"], r["appliance_id"], i
                ),
                "status": status,
                "last_seen_iso": (
                    r["last_heartbeat_at"].isoformat()
                    if r["last_heartbeat_at"]
                    else None
                ),
                "stale_seconds": r["stale_seconds"],
                "uptime_24h_pct": (
                    round(r["uptime_ratio_24h"] * 100, 1)
                    if r["checkin_count_24h"] > 0
                    else None
                ),
            }
        )

    overall = "online"
    if totals["offline"] > 0:
        overall = "offline"
    elif totals["stale"] > 0:
        overall = "stale"

    return {
        "organization": site_row["clinic_name"] or "OsirisCare customer",
        "status": overall,
        "totals": totals,
        "appliances": appliances,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verification_note": (
            "Status derived from signed appliance heartbeats. "
            "Hostnames and network details are intentionally hidden. "
            "To independently verify, request the signed auditor kit from your MSP."
        ),
    }


# =============================================================================
# Admin: generate / rotate / revoke a site's public slug
# =============================================================================

@admin_status_router.post("/{site_id}/status-slug")
async def enable_or_rotate_public_status(
    site_id: str,
    admin: dict = Depends(require_admin),
):
    """Generate (or rotate) a public status slug for a site. Returns the
    full public URL the customer can share."""
    new_slug = secrets.token_urlsafe(24)  # 32+ char unguessable

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        row = await conn.fetchrow(
            """
            UPDATE sites
            SET public_status_slug = $1
            WHERE site_id = $2
            RETURNING site_id, clinic_name, public_status_slug
            """,
            new_slug,
            site_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail=f"site_id={site_id} not found")
    return {
        "site_id": row["site_id"],
        "public_status_url": f"/status/{row['public_status_slug']}",
        "slug": row["public_status_slug"],
        "message": "Public status page enabled. Share the URL with the customer.",
    }


@admin_status_router.delete("/{site_id}/status-slug")
async def revoke_public_status(
    site_id: str,
    admin: dict = Depends(require_admin),
):
    """Revoke the public slug — the page at /status/{slug} starts 404ing."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        result = await conn.execute(
            """
            UPDATE sites
            SET public_status_slug = NULL
            WHERE site_id = $1
            """,
            site_id,
        )
    return {"site_id": site_id, "revoked": True}
