"""Admin API — acknowledge appliance relocation.

Pairs with appliance_relocation.detect_and_record_relocation (the
automatic detection half). When an admin acknowledges the move via
this endpoint, a privileged_access_attestation bundle is written with
event_type='appliance_relocation_acknowledged', linking naturally to
the detection bundle via the site's hash chain.

HIPAA §164.310(d)(1) audit trail is now complete:
  detection (auto)  +  acknowledgment (human, signed, reason, actor)
  = full chain of custody on physical moves.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .fleet import get_pool
from .tenant_middleware import admin_transaction
from .auth import require_admin
from .privileged_access_attestation import (
    create_privileged_access_attestation,
    PrivilegedAccessAttestationError,
)

logger = logging.getLogger("appliance_relocation_api")

router = APIRouter(prefix="/api/admin", tags=["appliance-relocation"])


_ALLOWED_REASONS = {
    "office_relocation",
    "equipment_swap",
    "staging_to_production",
    "preventive_maintenance",
    "theft_response",
    "tampering_incident",
    "shadow_it_discovery",
    "other",
}


class RelocationAck(BaseModel):
    detection_bundle_id: str = Field(..., min_length=8, max_length=64)
    reason_category: str = Field(..., description="One of: office_relocation, equipment_swap, staging_to_production, preventive_maintenance, theft_response, tampering_incident, shadow_it_discovery, other")
    reason_detail: str = Field(..., min_length=20, max_length=500,
                               description="Free-text detail (≥20 chars). Appears on the customer's H6 feed + auditor kit.")


@router.post("/appliances/{appliance_id}/acknowledge-relocation")
async def acknowledge_relocation(
    appliance_id: str,
    req: RelocationAck,
    request: Request,
    admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """Acknowledge that an appliance's physical move was expected.

    Writes a privileged_access_attestation with event_type =
    'appliance_relocation_acknowledged'. Includes actor (admin email),
    reason_category + reason_detail, IP of admin. Chains into the site's
    evidence chain on top of the detection bundle.

    If the detection bundle doesn't exist or doesn't belong to this
    site/appliance, rejects with 404 — prevents an admin from
    acknowledging a move on the wrong box by accident.
    """
    if req.reason_category not in _ALLOWED_REASONS:
        raise HTTPException(
            status_code=400,
            detail=f"reason_category must be one of {sorted(_ALLOWED_REASONS)}",
        )

    actor = admin.get("email") or admin.get("username")
    if not actor:
        raise HTTPException(status_code=403, detail="admin identity missing")

    pool = await get_pool()
    # #137 Sub-B Gate B precedent fix (audit/coach-116-sub-b-gate-b-
    # 2026-05-17.md): admin_connection + conn.transaction() is the
    # anti-pattern flagged in tenant_middleware.py:147-157 — under
    # PgBouncer transaction-pool mode the SET LOCAL app.is_admin
    # and subsequent statements can route to DIFFERENT backends
    # (Session 212 routing-risk caveat). admin_transaction pins
    # SET LOCAL + all multi-statement work to ONE backend in ONE
    # explicit txn. Mirrors the swap shipped in vault_key_approval_
    # api.py via #116 Sub-B P1 fix.
    async with admin_transaction(pool) as conn:
        # Verify the detection bundle exists + belongs to the right
        # appliance (by appliance_id → site lookup).
        sa = await conn.fetchrow(
            "SELECT site_id, mac_address FROM site_appliances "
            "WHERE appliance_id = $1 AND deleted_at IS NULL",
            appliance_id,
        )
        if not sa:
            raise HTTPException(status_code=404, detail=f"appliance {appliance_id} not found")
        site_id = sa["site_id"]

        det = await conn.fetchrow(
            """
            SELECT bundle_id, bundle_hash, chain_position, checks, summary
              FROM compliance_bundles
             WHERE bundle_id = $1
               AND site_id = $2
               AND check_type = 'appliance_relocation'
            """,
            req.detection_bundle_id, site_id,
        )
        if not det:
            raise HTTPException(
                status_code=404,
                detail=f"relocation detection bundle {req.detection_bundle_id} not found for site {site_id}",
            )

        # Compose a reason string that includes the category + detail,
        # so the attestation row captures both in the single `reason`
        # field required by privileged_access_attestation.
        reason = f"[{req.reason_category}] {req.reason_detail.strip()}"

        try:
            attestation = await create_privileged_access_attestation(
                conn,
                site_id=site_id,
                event_type="appliance_relocation_acknowledged",
                actor_email=actor,
                reason=reason,
                origin_ip=request.client.host if request.client else None,
                approvals=[
                    {
                        "detection_bundle_id": req.detection_bundle_id,
                        "detection_bundle_hash": det["bundle_hash"],
                        "appliance_id": appliance_id,
                        "mac_address": sa["mac_address"],
                        "reason_category": req.reason_category,
                    }
                ],
            )
        except PrivilegedAccessAttestationError as e:
            logger.error(
                "relocation ack attestation failed site=%s aid=%s err=%s",
                site_id, appliance_id, e, exc_info=True,
            )
            raise HTTPException(status_code=502, detail=f"attestation failed: {e}")

    logger.warning(
        "relocation_acknowledged site=%s aid=%s actor=%s category=%s det_bundle=%s ack_bundle=%s",
        site_id, appliance_id, actor, req.reason_category,
        req.detection_bundle_id, attestation["bundle_id"],
    )
    return {
        "ok": True,
        "acknowledged_at": attestation.get("timestamp") or "now",
        "detection_bundle_id": req.detection_bundle_id,
        "acknowledgment_bundle_id": attestation["bundle_id"],
        "chain_position": attestation["chain_position"],
        "actor": actor,
    }


@router.get("/appliances/{appliance_id}/relocations")
async def list_relocations(
    appliance_id: str,
    admin: dict = Depends(require_admin),
) -> Dict[str, Any]:
    """List relocation detections for this appliance + their ack status.

    Used by the admin panel to surface "this box moved and no one has
    said why" — the exact UI card that supersedes manual dashboard
    spelunking after a physical move.
    """
    pool = await get_pool()
    # admin_transaction (wave-21): list_relocations issues 3 admin
    # reads (appliance lookup, relocations history, current site).
    async with admin_transaction(pool) as conn:
        sa = await conn.fetchrow(
            "SELECT site_id FROM site_appliances WHERE appliance_id = $1 AND deleted_at IS NULL",
            appliance_id,
        )
        if not sa:
            raise HTTPException(status_code=404, detail=f"appliance {appliance_id} not found")
        site_id = sa["site_id"]

        # All detection bundles for this appliance, newest first.
        detections = await conn.fetch(
            """
            SELECT bundle_id, bundle_hash, chain_position, checked_at,
                   checks, summary
              FROM compliance_bundles
             WHERE site_id = $1
               AND check_type = 'appliance_relocation'
               AND checks::jsonb @> jsonb_build_array(jsonb_build_object('appliance_id', $2::text))
          ORDER BY checked_at DESC
             LIMIT 50
            """,
            site_id, appliance_id,
        )

        # Ack bundles for this site (any appliance — we'll filter to the
        # detections by matching the detection_bundle_id in approvals).
        acks = await conn.fetch(
            """
            SELECT bundle_id, chain_position, checked_at, checks
              FROM compliance_bundles
             WHERE site_id = $1
               AND check_type = 'privileged_access'
               AND checks::jsonb @> jsonb_build_array(jsonb_build_object('event_type', 'appliance_relocation_acknowledged'))
          ORDER BY checked_at DESC
             LIMIT 200
            """,
            site_id,
        )

    # Map each detection to its ack (if any). Matching is done by
    # looking inside the ack's checks[0].approvals[0].detection_bundle_id.
    import json as _json
    ack_by_detection: Dict[str, Any] = {}
    for a in acks:
        checks = a["checks"]
        if isinstance(checks, str):
            checks = _json.loads(checks)
        if not checks:
            continue
        approvals = (checks[0] or {}).get("approvals") or []
        for approval in approvals:
            det_id = (approval or {}).get("detection_bundle_id")
            if det_id and det_id not in ack_by_detection:
                ack_by_detection[det_id] = {
                    "bundle_id": a["bundle_id"],
                    "chain_position": a["chain_position"],
                    "checked_at": a["checked_at"].isoformat(),
                    "actor": (checks[0] or {}).get("actor_email"),
                    "reason": (checks[0] or {}).get("reason"),
                }

    return {
        "appliance_id": appliance_id,
        "site_id": site_id,
        "relocations": [
            {
                "detection": {
                    "bundle_id": d["bundle_id"],
                    "bundle_hash": d["bundle_hash"],
                    "chain_position": d["chain_position"],
                    "checked_at": d["checked_at"].isoformat(),
                    "from_subnet": (d["summary"] or {}).get("from_subnet") if isinstance(d["summary"], dict)
                                    else (_json.loads(d["summary"]) or {}).get("from_subnet") if d["summary"] else None,
                    "to_subnet": (d["summary"] or {}).get("to_subnet") if isinstance(d["summary"], dict)
                                  else (_json.loads(d["summary"]) or {}).get("to_subnet") if d["summary"] else None,
                },
                "acknowledgment": ack_by_detection.get(d["bundle_id"]),
            }
            for d in detections
        ],
    }
