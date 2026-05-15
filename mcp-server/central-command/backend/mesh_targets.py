"""
Mesh target assignment + ACK API (#M3).

Background: hash_ring.py historically computed target assignments purely
from the appliances table. When the appliances table was lying about
liveness (the
Session 206 bug), targets got assigned to phantom appliances and
silently never executed.

This module makes assignments first-class rows in mesh_target_assignments
(Migration 195) with TTL + appliance ACK. Appliances re-ACK every checkin.
Unacked assignments expire and get reassigned on the next rebalance pass.

The reassignment loop uses the same hash ring logic as hash_ring.py but
only considers appliances that are currently ACKing targets (i.e., alive
per our newest signal).
"""

from __future__ import annotations
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .fleet import get_pool
from .tenant_middleware import admin_connection, admin_transaction
from .shared import require_appliance_bearer

logger = logging.getLogger(__name__)

mesh_targets_router = APIRouter(prefix="/api/appliances/mesh", tags=["mesh"])


class MeshTargetAck(BaseModel):
    site_id: str
    appliance_id: str
    targets: List[Dict[str, str]] = Field(
        ...,
        description="List of {target_key, target_type} dicts the appliance claims to own",
    )


class MeshTargetsAckResponse(BaseModel):
    acked: int
    unknown: int        # Target not assigned to this appliance
    reassigned: int     # Target was reassigned to someone else
    total_assigned: int # Total targets owned by this appliance


@mesh_targets_router.post("/ack", response_model=MeshTargetsAckResponse)
async def ack_mesh_targets(
    req: MeshTargetAck,
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Appliance-side ACK: 'I am monitoring these targets.'

    Extends TTL for each target we confirm. Silently drops targets that
    aren't assigned to the caller (they may have been reassigned while
    the appliance was computing). Returns counts so the daemon can
    reconcile its local view.
    """
    if req.site_id != auth_site_id:
        raise HTTPException(
            status_code=403,
            detail="site_id mismatch — bearer token doesn't match request body",
        )

    pool = await get_pool()
    acked = 0
    unknown = 0
    reassigned = 0
    # admin_transaction (wave-27): ack_mesh_targets issues 3+ admin
    # statements per target (lookup, UPDATE assignment, audit log).
    async with admin_transaction(pool) as conn:
        for t in req.targets:
            key = t.get("target_key")
            ttype = t.get("target_type")
            if not key or not ttype:
                unknown += 1
                continue
            ok = await conn.fetchval(
                "SELECT record_mesh_target_ack($1, $2, $3, $4)",
                req.site_id, req.appliance_id, key, ttype,
            )
            if ok:
                acked += 1
            else:
                # Check if this target was reassigned (still exists, different owner)
                existing_owner = await conn.fetchval(
                    """
                    SELECT appliance_id FROM mesh_target_assignments
                    WHERE site_id = $1 AND target_key = $2 AND target_type = $3
                    """,
                    req.site_id, key, ttype,
                )
                if existing_owner and existing_owner != req.appliance_id:
                    reassigned += 1
                else:
                    unknown += 1

        total = await conn.fetchval(
            """
            SELECT COUNT(*) FROM mesh_target_assignments
            WHERE site_id = $1 AND appliance_id = $2
            """,
            req.site_id, req.appliance_id,
        )

    return MeshTargetsAckResponse(
        acked=acked,
        unknown=unknown,
        reassigned=reassigned,
        total_assigned=total or 0,
    )


@mesh_targets_router.get("/assignments")
async def get_my_assignments(
    auth_site_id: str = Depends(require_appliance_bearer),
):
    """Appliance queries its current target list from the server. Only
    returns assignments whose TTL has not expired (so dead assignments
    don't waste the appliance's time)."""
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        rows = await conn.fetch(
            """
            SELECT target_key, target_type, last_ack_at, ack_count,
                   expires_at, reassigned_from
            FROM mesh_target_assignments
            WHERE site_id = $1
              AND expires_at > NOW()
            ORDER BY target_key
            """,
            auth_site_id,
        )
    return {
        "site_id": auth_site_id,
        "assignments": [
            {
                "target_key": r["target_key"],
                "target_type": r["target_type"],
                "last_ack_at": r["last_ack_at"].isoformat() if r["last_ack_at"] else None,
                "ack_count": r["ack_count"],
                "expires_at": r["expires_at"].isoformat(),
            }
            for r in rows
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# =============================================================================
# Server-side rebalancing: reassign expired targets to live appliances
# =============================================================================

def _consistent_hash(target_key: str, appliance_ids: List[str]) -> str:
    """Stable assignment: hash(target) % len(appliances). Same semantics
    as hash_ring.py but scoped to the live set."""
    h = hashlib.sha256(target_key.encode()).hexdigest()
    idx = int(h[:16], 16) % len(appliance_ids)
    return sorted(appliance_ids)[idx]


async def rebalance_expired_assignments(site_id: str) -> Dict[str, int]:
    """Find expired assignments at a site and reassign them to a live
    appliance. "Live" means the appliance has a fresh heartbeat in the
    last 5 minutes — orthogonal to last_checkin (Session 206 invariant).

    Returns counts {expired, reassigned, orphaned}.
    """
    pool = await get_pool()
    stats = {"expired": 0, "reassigned": 0, "orphaned": 0}
    # admin_transaction (wave-16): rebalance_expired_assignments issues
    # 4 admin statements (live lookup, expired select, UPDATE reassign,
    # UPDATE orphan). Pin SET LOCAL to one backend.
    async with admin_transaction(pool) as conn:
        # Live appliances = those with heartbeats in the last 5 min.
        live_rows = await conn.fetch(
            """
            SELECT DISTINCT sa.appliance_id
            FROM site_appliances sa
            JOIN appliance_heartbeats hb
              ON hb.appliance_id = sa.appliance_id
             AND hb.observed_at > NOW() - INTERVAL '5 minutes'
            WHERE sa.site_id = $1
              AND sa.deleted_at IS NULL
            """,
            site_id,
        )
        live_ids = [r["appliance_id"] for r in live_rows]

        # Expired assignments at this site
        expired = await conn.fetch(
            """
            SELECT assignment_id, appliance_id, target_key, target_type
            FROM mesh_target_assignments
            WHERE site_id = $1
              AND expires_at < NOW()
            """,
            site_id,
        )
        stats["expired"] = len(expired)

        if not live_ids:
            # No live appliance — everything orphaned. DON'T delete; audit
            # trail matters. Mark them for investigation.
            stats["orphaned"] = len(expired)
            return stats

        for row in expired:
            new_owner = _consistent_hash(row["target_key"], live_ids)
            if new_owner == row["appliance_id"]:
                # Same appliance won the hash — renew the assignment.
                await conn.execute(
                    """
                    UPDATE mesh_target_assignments
                    SET assigned_at = NOW(),
                        last_ack_at = NULL,
                        ack_count = 0
                    WHERE assignment_id = $1
                    """,
                    row["assignment_id"],
                )
            else:
                await conn.execute(
                    """
                    UPDATE mesh_target_assignments
                    SET appliance_id = $1,
                        reassigned_from = $2,
                        reassigned_at = NOW(),
                        assigned_at = NOW(),
                        last_ack_at = NULL,
                        ack_count = 0
                    WHERE assignment_id = $3
                    """,
                    new_owner,
                    row["appliance_id"],
                    row["assignment_id"],
                )
                stats["reassigned"] += 1

    return stats
