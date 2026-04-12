"""Agent time-travel reconciliation (Session 205).

When an agent wakes in a past state (VM snapshot revert, disk image clone,
backup restore, power-loss journal rollback, hardware replacement), its
local state is inconsistent with Central Command's view. This module
handles the detection → plan → apply cycle.

Phase 1 scope (this module):
  - Detection signal aggregation
  - Ed25519-signed reconcile plan generation
  - Nonce epoch advancement
  - Append-only audit to reconcile_events
  - NTP sync precondition check (server-side time is assumed authoritative)

Phase 2 adds the daemon-side detection + reporting.
Phase 3 adds idempotent runbook application.
"""
from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_appliance_bearer
from .shared import execute_with_retry, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/appliances", tags=["reconcile"])


# Detection signals — codes the agent reports in its reconcile request.
# Names are stable strings; we store them in detection_signals JSONB.
SIGNAL_BOOT_COUNTER_REGRESSION = "boot_counter_regression"
SIGNAL_UPTIME_CLIFF = "uptime_cliff"
SIGNAL_GENERATION_MISMATCH = "generation_mismatch"
SIGNAL_LKG_FUTURE_MTIME = "lkg_future_mtime"
SIGNAL_MISSING_RECENT_ORDERS = "missing_recent_orders"

# Minimum signal strength required to trigger reconciliation.
# Round Table: ≥2 independent signals. Prevents false positives from
# clock-only issues or a single noisy signal.
MIN_SIGNALS_REQUIRED = 2

# Max clock skew before we refuse to reconcile (force NTP first).
# Clock skew > this breaks TLS + signature validation anyway, so retry
# after sync rather than proceed with broken crypto.
MAX_CLOCK_SKEW_SECONDS = 300  # 5 minutes


# --- Pydantic models -------------------------------------------------------


class ReconcileRequest(BaseModel):
    """Request body the agent sends when it detects time-travel."""

    appliance_id: str = Field(..., description="Unique appliance identifier")
    site_id: str
    reported_boot_counter: int = Field(
        ..., description="Local /var/lib/msp/boot_counter value"
    )
    reported_generation_uuid: Optional[str] = Field(
        None, description="Local /var/lib/msp/generation UUID if present"
    )
    reported_uptime_seconds: int = Field(
        ..., description="Agent-side /proc/uptime reading"
    )
    clock_skew_seconds: int = Field(
        0,
        description=(
            "Agent's clock offset from Central Command (positive = agent "
            "ahead, negative = agent behind). Agent must NTP-sync before "
            "sending this request; reported value is post-sync."
        ),
    )
    detection_signals: List[str] = Field(
        ..., description="Signal codes the agent observed"
    )


class ReconcilePlan(BaseModel):
    """Plan response — everything the agent needs to return to baseline."""

    event_id: str = Field(..., description="reconcile_events.id for audit correlation")
    nonce_epoch_hex: str = Field(
        ..., description="New 32-byte nonce epoch (hex) to replace agent's"
    )
    runbook_ids: List[str] = Field(
        ...,
        description=(
            "Idempotent runbooks to re-apply. Listed in execution order. "
            "Agent must verify each runbook exists in its embedded "
            "runbooks.json and skip unknown IDs rather than error."
        ),
    )
    generation_uuid: str = Field(
        ..., description="New generation UUID for agent to write to disk"
    )
    plan_signature_hex: str = Field(
        ..., description="Ed25519 signature (hex) over canonical plan JSON"
    )
    signed_payload: str = Field(
        ...,
        description=(
            "Canonical JSON of the plan minus the signature. Agent reconstructs "
            "this from the plan fields in sorted order and verifies against "
            "signature + its appliance_public_key."
        ),
    )
    issued_at: str = Field(..., description="ISO8601 UTC timestamp")


# --- Detection validation --------------------------------------------------


def _validate_detection(
    req: ReconcileRequest, last_known: Optional[Dict[str, Any]]
) -> tuple[bool, str]:
    """Confirm the agent's detection signals are consistent with server state.

    Returns (accepted, reason). Rejecting the reconcile request with a
    clear reason protects against:
      - Rogue agent spamming reconciles to reset nonces
      - False positives from transient issues
      - Insufficient detection strength
    """
    if len(req.detection_signals) < MIN_SIGNALS_REQUIRED:
        return (
            False,
            f"Need ≥{MIN_SIGNALS_REQUIRED} detection signals, got {len(req.detection_signals)}",
        )

    if abs(req.clock_skew_seconds) > MAX_CLOCK_SKEW_SECONDS:
        return (
            False,
            f"Clock skew {req.clock_skew_seconds}s exceeds {MAX_CLOCK_SKEW_SECONDS}s — "
            f"NTP sync before retry",
        )

    if not last_known:
        # First-ever report from this appliance — nothing to compare against.
        # Accept the reconcile as "initial baseline" case.
        return True, "initial_baseline"

    # Cross-check reported signals against known state
    if SIGNAL_BOOT_COUNTER_REGRESSION in req.detection_signals:
        lkg_counter = last_known.get("boot_counter", 0) or 0
        if req.reported_boot_counter > lkg_counter:
            return (
                False,
                f"Claimed boot_counter_regression but reported={req.reported_boot_counter} "
                f"> last_known={lkg_counter}. Inconsistent.",
            )

    if SIGNAL_GENERATION_MISMATCH in req.detection_signals:
        lkg_gen = last_known.get("generation_uuid")
        if lkg_gen and req.reported_generation_uuid == str(lkg_gen):
            return (
                False,
                "Claimed generation_mismatch but reported UUID matches last_known",
            )

    return True, "valid"


# --- Plan builder ----------------------------------------------------------


def _build_plan_payload(
    event_id: str,
    appliance_id: str,
    nonce_epoch_hex: str,
    generation_uuid: str,
    runbook_ids: List[str],
    issued_at: datetime,
) -> str:
    """Canonical JSON of the plan (excluding signature).

    Agent reconstructs this from the plan fields and verifies the
    signature against its appliance_public_key. sort_keys=True is
    critical — any ordering difference breaks verification.
    """
    return json.dumps(
        {
            "event_id": event_id,
            "appliance_id": appliance_id,
            "nonce_epoch_hex": nonce_epoch_hex,
            "generation_uuid": generation_uuid,
            "runbook_ids": runbook_ids,
            "issued_at": issued_at.isoformat(),
        },
        sort_keys=True,
    )


async def _fetch_baseline_runbooks(
    db: AsyncSession, site_id: str
) -> List[str]:
    """Fetch runbook IDs the agent should re-apply after reconcile.

    Currently returns the L1 rules currently active for this site.
    When Phase 3 lands, this will include runbooks that any "chronic"
    incident type needs to bring that type back to compliant state.
    """
    # L1 rules active for this site's incident types
    result = await execute_with_retry(db, text("""
        SELECT DISTINCT runbook_id
        FROM l1_rules
        WHERE enabled = true
          AND runbook_id IS NOT NULL
          AND NOT runbook_id LIKE 'ESC-%'
        ORDER BY runbook_id
        LIMIT 20
    """))
    rows = result.fetchall()
    return [r.runbook_id for r in rows]


# --- Endpoints -------------------------------------------------------------


@router.post("/reconcile", response_model=ReconcilePlan)
async def request_reconcile(
    req: ReconcileRequest,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
) -> ReconcilePlan:
    """Agent reports time-travel — server returns a signed plan.

    Flow:
      1. Enforce auth_site_id matches req.site_id (same pattern as checkin)
      2. Load last_known state for this appliance
      3. Validate detection signals are consistent
      4. Generate new nonce_epoch + generation_uuid
      5. Sign the plan with the server's signing key (NOT the per-appliance key)
      6. Append to reconcile_events audit
      7. Return signed plan

    The agent validates the signature using the server public key embedded
    at build time (not a rotatable secret — this is the same key that
    signs all fleet orders today).
    """
    if req.site_id != auth_site_id:
        raise HTTPException(
            status_code=403,
            detail="Site ID mismatch: token does not authorize this site",
        )

    # 1. Fetch last-known state
    result = await execute_with_retry(db, text("""
        SELECT boot_counter, generation_uuid, nonce_epoch,
               last_reconcile_at, reconcile_count
        FROM site_appliances
        WHERE appliance_id = :aid AND site_id = :sid AND deleted_at IS NULL
    """), {"aid": req.appliance_id, "sid": req.site_id})
    last_known_row = result.fetchone()
    last_known = dict(last_known_row._mapping) if last_known_row else None

    if last_known is None:
        raise HTTPException(
            status_code=404,
            detail=f"Appliance {req.appliance_id} not registered — cannot reconcile",
        )

    # 2. Validate detection
    ok, reason = _validate_detection(req, last_known)
    if not ok:
        # Record the rejection for forensics but don't return a plan
        await execute_with_retry(db, text("""
            INSERT INTO reconcile_events (
                appliance_id, site_id, detected_at, detection_signals,
                reported_boot_counter, last_known_boot_counter,
                reported_generation_uuid, last_known_generation_uuid,
                reported_uptime_seconds, clock_skew_seconds,
                plan_status, error_message
            ) VALUES (
                :aid, :sid, NOW(), CAST(:signals AS jsonb),
                :rbc, :lkbc, CAST(:rgu AS UUID), :lkgu,
                :rus, :cs,
                'rejected', :err
            )
        """), {
            "aid": req.appliance_id,
            "sid": req.site_id,
            "signals": json.dumps(req.detection_signals),
            "rbc": req.reported_boot_counter,
            "lkbc": last_known.get("boot_counter"),
            "rgu": req.reported_generation_uuid,
            "lkgu": last_known.get("generation_uuid"),
            "rus": req.reported_uptime_seconds,
            "cs": req.clock_skew_seconds,
            "err": reason,
        })
        await db.commit()
        raise HTTPException(status_code=400, detail=f"Reconcile rejected: {reason}")

    # 3. Generate fresh epoch + generation
    new_epoch = secrets.token_bytes(32)
    new_epoch_hex = new_epoch.hex()
    new_generation = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    issued_at = datetime.now(timezone.utc)

    # 4. Fetch baseline runbooks
    runbook_ids = await _fetch_baseline_runbooks(db, req.site_id)

    # 5. Sign the plan
    from main import sign_data

    signed_payload = _build_plan_payload(
        event_id, req.appliance_id, new_epoch_hex, new_generation,
        runbook_ids, issued_at,
    )
    signature_hex = sign_data(signed_payload)
    if len(signature_hex) != 128:
        raise HTTPException(
            status_code=500,
            detail=f"Signing returned {len(signature_hex)} chars, expected 128",
        )

    # 6. Persist audit + update appliance state
    await execute_with_retry(db, text("""
        INSERT INTO reconcile_events (
            id, appliance_id, site_id, detected_at, detection_signals,
            reported_boot_counter, last_known_boot_counter,
            reported_generation_uuid, last_known_generation_uuid,
            reported_uptime_seconds, clock_skew_seconds,
            plan_generated_at, plan_runbook_ids, plan_signature_hex,
            plan_nonce_epoch_hex, plan_status
        ) VALUES (
            CAST(:eid AS UUID), :aid, :sid, NOW(), CAST(:signals AS jsonb),
            :rbc, :lkbc, CAST(:rgu AS UUID), :lkgu,
            :rus, :cs,
            NOW(), CAST(:rbks AS TEXT[]), :sig,
            :epoch, 'pending'
        )
    """), {
        "eid": event_id,
        "aid": req.appliance_id,
        "sid": req.site_id,
        "signals": json.dumps(req.detection_signals),
        "rbc": req.reported_boot_counter,
        "lkbc": last_known.get("boot_counter"),
        "rgu": req.reported_generation_uuid,
        "lkgu": last_known.get("generation_uuid"),
        "rus": req.reported_uptime_seconds,
        "cs": req.clock_skew_seconds,
        "rbks": runbook_ids,
        "sig": signature_hex,
        "epoch": new_epoch_hex,
    })

    # Update appliance state — treat this as the new "known good"
    await execute_with_retry(db, text("""
        UPDATE site_appliances
        SET boot_counter = GREATEST(boot_counter, :rbc),
            generation_uuid = CAST(:new_gen AS UUID),
            nonce_epoch = :new_epoch,
            last_reconcile_at = NOW(),
            reconcile_count = reconcile_count + 1
        WHERE appliance_id = :aid AND site_id = :sid
    """), {
        "rbc": req.reported_boot_counter,
        "new_gen": new_generation,
        "new_epoch": new_epoch,
        "aid": req.appliance_id,
        "sid": req.site_id,
    })

    await db.commit()

    logger.info(
        "Reconcile plan issued",
        extra={
            "event_id": event_id,
            "appliance_id": req.appliance_id,
            "site_id": req.site_id,
            "signals": req.detection_signals,
            "runbook_count": len(runbook_ids),
        },
    )

    return ReconcilePlan(
        event_id=event_id,
        nonce_epoch_hex=new_epoch_hex,
        runbook_ids=runbook_ids,
        generation_uuid=new_generation,
        plan_signature_hex=signature_hex,
        signed_payload=signed_payload,
        issued_at=issued_at.isoformat(),
    )


class ReconcileAckRequest(BaseModel):
    event_id: str
    success: bool
    error_message: Optional[str] = None
    post_boot_counter: Optional[int] = None
    post_generation_uuid: Optional[str] = None


@router.post("/reconcile/ack")
async def ack_reconcile(
    ack: ReconcileAckRequest,
    db: AsyncSession = Depends(get_db),
    auth_site_id: str = Depends(require_appliance_bearer),
) -> Dict[str, Any]:
    """Agent reports the outcome of applying a reconcile plan."""
    status = "applied" if ack.success else "failed"
    await execute_with_retry(db, text("""
        UPDATE reconcile_events
        SET plan_applied_at = NOW(),
            plan_status = :status,
            error_message = :err,
            post_boot_counter = :pbc,
            post_generation_uuid = CAST(:pgu AS UUID)
        WHERE id = CAST(:eid AS UUID)
          AND site_id = :sid
    """), {
        "eid": ack.event_id,
        "sid": auth_site_id,
        "status": status,
        "err": ack.error_message,
        "pbc": ack.post_boot_counter,
        "pgu": ack.post_generation_uuid,
    })
    await db.commit()

    logger.info(
        "Reconcile plan acknowledged",
        extra={
            "event_id": ack.event_id,
            "success": ack.success,
            "site_id": auth_site_id,
        },
    )
    return {"status": "recorded"}
