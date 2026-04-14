"""
Appliance trace endpoint (#155).

One-stop diagnostic: given an IP or MAC, return the full provenance —
LAN scan, pre-provisioning, site_appliances row, install_sessions,
heartbeat count, recent logs.

Session 206 lesson: most of the session's wrong-turns came from me
INFERRING a MAC-to-IP mapping instead of querying the ground truth.
This endpoint makes ground truth one call away.

Route: GET /api/admin/appliance-trace/{target}
  target = IP (192.168.88.228) OR MAC (84:3A:5B:1F:FF:E4, any case, any sep)

Auth: require_admin. Returns a dict with every section populated (empty
list/null when no match).
"""

from __future__ import annotations
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .fleet import get_pool
from .tenant_middleware import admin_connection
from .auth import require_admin

logger = logging.getLogger(__name__)

appliance_trace_router = APIRouter(prefix="/api/admin", tags=["admin-trace"])


_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:\-]?[0-9A-Fa-f]{2}){5}$")
_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _normalize_mac(raw: str) -> Optional[str]:
    """Canonicalize MAC to uppercase colon-separated, or None if not a MAC."""
    if not _MAC_RE.match(raw):
        return None
    clean = raw.upper().replace(":", "").replace("-", "")
    return ":".join(clean[i:i + 2] for i in range(0, len(clean), 2))


def _looks_like_ip(raw: str) -> bool:
    if not _IP_RE.match(raw):
        return False
    return all(0 <= int(o) <= 255 for o in raw.split("."))


@appliance_trace_router.get("/appliance-trace/{target}")
async def appliance_trace(
    target: str,
    limit_logs: int = Query(20, ge=0, le=200),
    admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Trace every system's knowledge of an appliance identified by IP or MAC."""
    mac = _normalize_mac(target)
    is_ip = _looks_like_ip(target)

    if not mac and not is_ip:
        raise HTTPException(
            status_code=400,
            detail=(
                f"target must be a valid IP (1.2.3.4) or MAC "
                f"(AA:BB:CC:DD:EE:FF) — got {target!r}"
            ),
        )

    pool = await get_pool()
    out: Dict[str, Any] = {
        "target_input": target,
        "resolved_mac": mac,
        "resolved_ip": target if is_ip else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lan_scan": [],
        "provisioning": None,
        "site_appliances": [],
        "install_sessions": [],
        "heartbeat_summary": None,
        "recent_heartbeats": [],
        "liveness_claims": [],
        "mesh_assignments": [],
    }

    async with admin_connection(pool) as conn:
        # 1) discovered_devices — LAN scan ground truth
        if is_ip:
            rows = await conn.fetch(
                """
                SELECT site_id, ip_address::text, mac_address, hostname,
                       device_type, last_seen_at
                FROM discovered_devices
                WHERE ip_address::text = $1
                ORDER BY last_seen_at DESC
                LIMIT 5
                """,
                target,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT site_id, ip_address::text, mac_address, hostname,
                       device_type, last_seen_at
                FROM discovered_devices
                WHERE UPPER(mac_address) = $1
                ORDER BY last_seen_at DESC
                LIMIT 5
                """,
                mac,
            )
        out["lan_scan"] = [_row_to_dict(r) for r in rows]

        # If we only had IP, promote the MAC we discovered
        if not mac and out["lan_scan"]:
            discovered_mac = out["lan_scan"][0].get("mac_address")
            if discovered_mac:
                mac = discovered_mac.upper()
                out["resolved_mac"] = mac

        # 2) appliance_provisioning
        if mac:
            prov = await conn.fetchrow(
                """
                SELECT mac_address, site_id, provisioned_at, registered_at,
                       api_key IS NOT NULL AS has_api_key
                FROM appliance_provisioning
                WHERE UPPER(mac_address) = $1
                """,
                mac,
            )
            if prov:
                out["provisioning"] = _row_to_dict(prov)

        # 3) site_appliances
        if mac:
            sa_rows = await conn.fetch(
                """
                SELECT appliance_id, site_id, hostname, display_name,
                       mac_address, ip_addresses, agent_version,
                       first_checkin, last_checkin, status,
                       daemon_health->>'boot_source' AS boot_source,
                       deleted_at, deleted_by,
                       EXTRACT(EPOCH FROM (NOW() - last_checkin))::int AS stale_sec
                FROM site_appliances
                WHERE UPPER(mac_address) = $1
                ORDER BY last_checkin DESC NULLS LAST
                """,
                mac,
            )
            out["site_appliances"] = [_row_to_dict(r) for r in sa_rows]

        # 4) install_sessions
        if mac:
            is_rows = await conn.fetch(
                """
                SELECT session_id, site_id, hostname, install_stage,
                       first_seen, last_seen, checkin_count, expires_at,
                       boot_source
                FROM install_sessions
                WHERE UPPER(mac_address) = $1
                ORDER BY last_seen DESC
                LIMIT 5
                """,
                mac,
            )
            out["install_sessions"] = [_row_to_dict(r) for r in is_rows]

        # 5) heartbeats — summary + recent
        if mac and out["site_appliances"]:
            appliance_id = out["site_appliances"][0]["appliance_id"]
            summary = await conn.fetchrow(
                """
                SELECT COUNT(*) AS total,
                       MAX(observed_at) AS last_observed_at,
                       COUNT(*) FILTER (WHERE agent_signature IS NOT NULL) AS signed_count,
                       EXTRACT(EPOCH FROM (NOW() - MAX(observed_at)))::int AS stale_sec
                FROM appliance_heartbeats
                WHERE appliance_id = $1
                  AND observed_at > NOW() - INTERVAL '7 days'
                """,
                appliance_id,
            )
            out["heartbeat_summary"] = _row_to_dict(summary)
            if limit_logs > 0:
                recent = await conn.fetch(
                    """
                    SELECT observed_at, status, agent_version, boot_source,
                           primary_subnet, has_anycast,
                           agent_signature IS NOT NULL AS signed
                    FROM appliance_heartbeats
                    WHERE appliance_id = $1
                    ORDER BY observed_at DESC
                    LIMIT 20
                    """,
                    appliance_id,
                )
                out["recent_heartbeats"] = [_row_to_dict(r) for r in recent]

            # 6) liveness claims (APPLIANCE_LIVENESS_LIE ledger)
            lc = await conn.fetch(
                """
                SELECT claim_id::text, claim_type, claimed_at,
                       cited_heartbeat_id, cited_heartbeat_hash,
                       details, published_to
                FROM liveness_claims
                WHERE appliance_id = $1
                ORDER BY claimed_at DESC
                LIMIT 10
                """,
                appliance_id,
            )
            out["liveness_claims"] = [_row_to_dict(r) for r in lc]

            # 7) mesh assignments
            ma = await conn.fetch(
                """
                SELECT assignment_id::text, target_key, target_type,
                       assigned_at, last_ack_at, ack_count, expires_at,
                       reassigned_from
                FROM mesh_target_assignments
                WHERE appliance_id = $1
                ORDER BY assigned_at DESC
                LIMIT 20
                """,
                appliance_id,
            )
            out["mesh_assignments"] = [_row_to_dict(r) for r in ma]

    # Diagnostic summary — a one-line verdict for tired operators.
    out["verdict"] = _verdict(out)
    return out


def _verdict(trace: Dict[str, Any]) -> str:
    """Single-line verdict that answers 'is this box healthy?'"""
    mac = trace.get("resolved_mac")
    hb = trace.get("heartbeat_summary")
    sa = trace.get("site_appliances") or []
    if not mac:
        return "Unknown MAC — target not seen by LAN scan or any other table"
    if not trace.get("provisioning"):
        return f"{mac}: not pre-provisioned — appliance cannot authenticate"
    if not sa:
        if trace.get("install_sessions"):
            return f"{mac}: in install_sessions (live USB), never completed install"
        return f"{mac}: pre-provisioned but never checked in — stuck in daemon provision loop?"
    sa0 = sa[0]
    if sa0.get("deleted_at"):
        return f"{mac}: site_appliances row is soft-deleted (by {sa0.get('deleted_by')!r})"
    if hb and hb.get("total", 0) == 0:
        return f"{mac}: site_appliances row exists but ZERO heartbeats in 7d — daemon not running"
    if hb:
        ss = hb.get("stale_sec")
        if ss is not None and ss < 90:
            return f"{mac}: healthy — last heartbeat {ss}s ago ({hb.get('total', 0)} in 7d)"
        if ss is not None and ss < 300:
            return f"{mac}: stale — last heartbeat {ss}s ago"
        return f"{mac}: offline — last heartbeat {ss}s ago"
    return f"{mac}: state unclear, inspect full trace"


def _row_to_dict(row) -> Dict[str, Any]:
    """asyncpg Record → JSON-safe dict. Timestamps isoformat, inet → str."""
    if row is None:
        return {}
    out = {}
    for k, v in dict(row).items():
        if v is None:
            out[k] = None
        elif isinstance(v, (datetime,)):
            out[k] = v.isoformat()
        elif hasattr(v, "isoformat"):  # date
            out[k] = v.isoformat()
        elif isinstance(v, (str, int, float, bool, list, dict)):
            out[k] = v
        else:
            out[k] = str(v)
    return out
