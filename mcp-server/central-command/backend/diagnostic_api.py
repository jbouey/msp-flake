"""Diagnostic probe admin API (Phase 12.2).

Exposes the bounded, whitelisted diagnostic probe catalog so admins
can trigger remote probes on appliances without a new network
channel. Output flows back via the existing signed fleet-order
completion path, stored in fleet_order_completions.output (new column
from Phase 12.1).

All endpoints require admin auth. Every probe invocation is written
to admin_audit_log by run_diagnostic_probe().
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_auth
from .shared import get_db
from .diagnostic_probes import list_probes, run_diagnostic_probe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/diagnostics", tags=["admin"])


class ProbeRequest(BaseModel):
    site_id: str = Field(..., description="Target site_id")
    probe: str = Field(..., description="Probe name from the catalog")
    wait_seconds: int = Field(
        60, ge=10, le=180,
        description="How long to wait for daemon to complete (10..180s)",
    )


@router.get("/probes")
async def list_available_probes(
    request: Request,
    user: dict = Depends(require_auth),
) -> List[Dict[str, Any]]:
    """Catalog of diagnostic probes an admin can run."""
    return list_probes()


@router.post("/run")
async def run_probe(
    req: ProbeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Execute a single probe on the target site's primary appliance.

    Blocks for up to `wait_seconds` waiting on fleet_order_completions.
    Returns immediately with status='pending' if the daemon hasn't
    acked yet; caller can poll /completions?fleet_order_id=... to
    retrieve output later.
    """
    actor = user.get("email") or user.get("username") or "admin"
    try:
        return await run_diagnostic_probe(
            db=db,
            site_id=req.site_id,
            probe=req.probe,
            actor=actor,
            wait_seconds=req.wait_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Diagnostic probe {req.probe!r} on {req.site_id!r} failed")
        raise HTTPException(status_code=500, detail=f"Probe failed: {e}")


@router.get("/completions/{fleet_order_id}")
async def get_completion(
    fleet_order_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Fetch the output/error from a previously-issued probe by its
    fleet_order_id. Useful when /run returned status='pending'."""
    row = (await db.execute(text("""
        SELECT fleet_order_id::text AS fleet_order_id,
               appliance_id, status, output, error_message,
               duration_ms, completed_at, updated_at
        FROM fleet_order_completions
        WHERE fleet_order_id = :fid
        ORDER BY completed_at DESC NULLS LAST
        LIMIT 1
    """), {"fid": fleet_order_id})).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No completion recorded yet")
    return {
        "fleet_order_id": row.fleet_order_id,
        "appliance_id": row.appliance_id,
        "status": row.status,
        "output": row.output,
        "error_message": row.error_message,
        "duration_ms": row.duration_ms,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/recent-failures")
async def recent_failures(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
    hours: int = 24,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Recent fleet-order failures across the fleet — primary triage view.

    Phase 12.1 capture makes this actually useful; before the migration
    the error_message/output columns didn't exist."""
    rows = (await db.execute(text("""
        SELECT foc.fleet_order_id::text AS fleet_order_id,
               foc.appliance_id,
               fo.order_type,
               foc.error_message,
               foc.output,
               foc.duration_ms,
               foc.completed_at
        FROM fleet_order_completions foc
        JOIN fleet_orders fo ON fo.id = foc.fleet_order_id
        WHERE foc.status = 'failed'
          AND foc.completed_at > NOW() - make_interval(hours => :h)
        ORDER BY foc.completed_at DESC
        LIMIT :lim
    """), {"h": hours, "lim": limit})).fetchall()
    return [
        {
            "fleet_order_id": r.fleet_order_id,
            "appliance_id": r.appliance_id,
            "order_type": r.order_type,
            "error_message": r.error_message,
            "output_preview": _preview(r.output),
            "duration_ms": r.duration_ms,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in rows
    ]


@router.get("/pubkey-divergence")
async def pubkey_divergence(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Phase 13 H2/H5: report appliances whose most-recently-delivered
    server pubkey fingerprint diverges from the current signing key.
    Diverged appliances will reject signed fleet orders until they
    check in again and cache the current key.
    """
    import os as _os
    import pathlib as _p
    try:
        from nacl.signing import SigningKey
        from nacl.encoding import HexEncoder
        key_hex = _p.Path(
            _os.getenv("SIGNING_KEY_FILE", "/app/secrets/signing.key")
        ).read_bytes().strip()
        sk = SigningKey(key_hex, encoder=HexEncoder)
        current_fp = sk.verify_key.encode(encoder=HexEncoder).decode()[:16]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"server pubkey unavailable: {e}")

    rows = (await db.execute(text("""
        SELECT site_id, hostname, mac_address,
               server_pubkey_fingerprint_seen,
               server_pubkey_fingerprint_seen_at,
               last_checkin,
               agent_version
        FROM site_appliances
        WHERE deleted_at IS NULL
        ORDER BY last_checkin DESC NULLS LAST
    """))).fetchall()

    divergent: List[Dict[str, Any]] = []
    matched: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []
    for r in rows:
        entry = {
            "site_id": r.site_id,
            "hostname": r.hostname,
            "mac_address": r.mac_address,
            "agent_version": r.agent_version,
            "fingerprint_seen": r.server_pubkey_fingerprint_seen,
            "fingerprint_seen_at": r.server_pubkey_fingerprint_seen_at.isoformat()
                if r.server_pubkey_fingerprint_seen_at else None,
            "last_checkin": r.last_checkin.isoformat() if r.last_checkin else None,
        }
        if r.server_pubkey_fingerprint_seen is None:
            unknown.append(entry)
        elif r.server_pubkey_fingerprint_seen == current_fp:
            matched.append(entry)
        else:
            divergent.append(entry)

    return {
        "current_server_fingerprint": current_fp,
        "divergent": divergent,
        "matched": matched,
        "unknown": unknown,
        "summary": {
            "divergent_count": len(divergent),
            "matched_count": len(matched),
            "unknown_count": len(unknown),
        },
    }


def _preview(output: Any) -> Optional[str]:
    if output is None:
        return None
    try:
        import json as _json
        s = _json.dumps(output) if not isinstance(output, str) else output
        return s[:400] + ("..." if len(s) > 400 else "")
    except Exception:
        return str(output)[:400]
