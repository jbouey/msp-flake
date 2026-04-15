"""sigauth_enforcement.py — Week 5 of the composed identity stack.

Two functional surfaces:

1. /api/admin/sigauth/{promote,demote}/{appliance_id}
   Manual operator control over per-appliance signature enforcement.
   Promote: observe → enforce.
   Demote:  enforce → observe (instant rollback).
   Both write the actor + reason into site_appliances metadata
   columns AND admin_audit_log.

2. sigauth_auto_promotion_loop()
   Background task. Every 5 minutes scans 'observe'-mode appliances
   and promotes any that have ≥ MIN_SAMPLES sigauth observations
   in the last AUTO_PROMOTE_WINDOW with ZERO failures and ZERO
   present=False rows. Auto-promotion is the safe-by-construction
   path; manual is for special cases.

Migration 192 row-guard is satisfied because every UPDATE here
filters by appliance_id (one row at a time). No SET LOCAL needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import auth as auth_module
from .fleet import get_pool
from .tenant_middleware import admin_connection

logger = logging.getLogger("sigauth_enforcement")

router = APIRouter(prefix="/api/admin/sigauth", tags=["sigauth-enforcement"])


# ---------------------------------------------------------------------------
# Auto-promotion thresholds
# ---------------------------------------------------------------------------
#
# Conservative defaults. Tunable via env without redeploy.
# MIN_SAMPLES = number of valid sig observations before auto-promote
# AUTO_PROMOTE_WINDOW_HOURS = lookback for the sample count
# AUTO_PROMOTE_INTERVAL = how often the worker runs
MIN_SAMPLES = int(os.environ.get("SIGAUTH_AUTO_PROMOTE_MIN_SAMPLES", "60"))
AUTO_PROMOTE_WINDOW_HOURS = int(os.environ.get("SIGAUTH_AUTO_PROMOTE_WINDOW_HOURS", "6"))
AUTO_PROMOTE_INTERVAL_SECONDS = int(os.environ.get("SIGAUTH_AUTO_PROMOTE_INTERVAL_SECONDS", "300"))


class EnforcementChangeRequest(BaseModel):
    reason: str = Field(..., min_length=10, max_length=500)


class EnforcementChangeResponse(BaseModel):
    appliance_id: str
    site_id: str
    previous: str
    new: str
    actor: str
    reason: str


async def _set_enforcement(
    conn, appliance_id: str, target: str, actor: str, reason: str
) -> EnforcementChangeResponse:
    """Per-row UPDATE — Migration 192 row-guard satisfied. Writes
    metadata columns + admin_audit_log row in one transaction."""
    row = await conn.fetchrow(
        "SELECT site_id, signature_enforcement FROM site_appliances "
        "WHERE appliance_id = $1 AND deleted_at IS NULL",
        appliance_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="appliance_id not found")
    previous = row["signature_enforcement"]
    if previous == target:
        # Idempotent — operator might double-click. Don't audit a no-op.
        return EnforcementChangeResponse(
            appliance_id=appliance_id, site_id=row["site_id"],
            previous=previous, new=target, actor=actor, reason=reason,
        )

    async with conn.transaction():
        await conn.execute(
            """
            UPDATE site_appliances
               SET signature_enforcement = $1,
                   signature_enforcement_changed_at = NOW(),
                   signature_enforcement_changed_by = $2,
                   signature_enforcement_reason = $3
             WHERE appliance_id = $4
            """,
            target, actor, reason, appliance_id,
        )
        await conn.execute(
            """
            INSERT INTO admin_audit_log (action, target, username, details, created_at)
            VALUES ('sigauth.' || $1, 'appliance:' || $2, $3, $4::jsonb, NOW())
            """,
            target,  # 'sigauth.enforce' or 'sigauth.observe'
            appliance_id,
            actor,
            json.dumps({
                "site_id": row["site_id"],
                "previous": previous,
                "new": target,
                "reason": reason,
            }),
        )

    logger.info(
        "sigauth_enforcement changed",
        appliance_id=appliance_id,
        site_id=row["site_id"],
        previous=previous,
        new=target,
        actor=actor,
        reason=reason,
    )

    return EnforcementChangeResponse(
        appliance_id=appliance_id, site_id=row["site_id"],
        previous=previous, new=target, actor=actor, reason=reason,
    )


@router.post("/promote/{appliance_id}", response_model=EnforcementChangeResponse)
async def promote(
    appliance_id: str,
    req: EnforcementChangeRequest,
    user: dict = Depends(auth_module.require_auth),
) -> EnforcementChangeResponse:
    """Manually move appliance observe → enforce. Use for testing
    or when auto-promotion criteria haven't been met but the
    operator has out-of-band proof the daemon is healthy."""
    actor = user.get("username") or user.get("email") or "unknown-admin"
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        return await _set_enforcement(conn, appliance_id, "enforce", actor, req.reason)


@router.post("/demote/{appliance_id}", response_model=EnforcementChangeResponse)
async def demote(
    appliance_id: str,
    req: EnforcementChangeRequest,
    user: dict = Depends(auth_module.require_auth),
) -> EnforcementChangeResponse:
    """Instant rollback enforce → observe. Use the moment a
    daemon you just promoted starts auth-failing. Idempotent."""
    actor = user.get("username") or user.get("email") or "unknown-admin"
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        return await _set_enforcement(conn, appliance_id, "observe", actor, req.reason)


# ---------------------------------------------------------------------------
# Auto-promotion worker
# ---------------------------------------------------------------------------


async def _auto_promotion_tick(conn) -> dict:
    """Single sweep — find observe-mode appliances with sustained
    valid signatures and promote them. Returns counters for
    observability."""
    candidates = await conn.fetch(
        """
        WITH window_obs AS (
            SELECT site_id, mac_address,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE valid = false) AS fails
              FROM sigauth_observations
             WHERE observed_at > NOW() - $1::interval
          GROUP BY site_id, mac_address
        )
        SELECT sa.appliance_id, sa.site_id, sa.mac_address,
               wo.total, wo.fails
          FROM site_appliances sa
          JOIN window_obs wo ON wo.site_id = sa.site_id
                            AND UPPER(wo.mac_address) = UPPER(sa.mac_address)
         WHERE sa.deleted_at IS NULL
           AND sa.signature_enforcement = 'observe'
           AND wo.total >= $2
           AND wo.fails = 0
        """,
        f"{AUTO_PROMOTE_WINDOW_HOURS} hours",
        MIN_SAMPLES,
    )

    promoted = 0
    for row in candidates:
        try:
            await _set_enforcement(
                conn,
                row["appliance_id"],
                "enforce",
                actor="auto-promotion",
                reason=f"sustained valid signatures: {row['total']} samples in {AUTO_PROMOTE_WINDOW_HOURS}h, 0 failures",
            )
            promoted += 1
        except Exception:
            logger.error(
                "sigauth auto-promotion failed",
                appliance_id=row["appliance_id"],
                exc_info=True,
            )

    return {"candidates": len(candidates), "promoted": promoted}


async def sigauth_auto_promotion_loop():
    """Background task — runs every AUTO_PROMOTE_INTERVAL_SECONDS.
    Wired into main.py task supervisor as 'sigauth_auto_promotion'."""
    await asyncio.sleep(180)  # Settle period after cold start.
    logger.info(
        "sigauth_auto_promotion loop started",
        interval_s=AUTO_PROMOTE_INTERVAL_SECONDS,
        min_samples=MIN_SAMPLES,
        window_h=AUTO_PROMOTE_WINDOW_HOURS,
    )
    while True:
        try:
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                result = await _auto_promotion_tick(conn)
            if result["promoted"]:
                logger.info(
                    "sigauth auto-promotion tick",
                    candidates=result["candidates"],
                    promoted=result["promoted"],
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("sigauth_auto_promotion tick failed", exc_info=True)

        await asyncio.sleep(AUTO_PROMOTE_INTERVAL_SECONDS)
