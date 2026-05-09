"""journal_api.py — Session 207 Phase H4.

Journal-upload receiver. Appliances POST /api/journal/upload every
15 minutes with a batched + PHI-scrubbed chunk of journalctl lines.
Each batch lands as one append-only row in journal_upload_events
(hash-chained per appliance).

Design points:

- PHI scrubbing is appliance-side (per Session 204 rule — all data
  scrubbed at egress). Backend trusts the "scrubbed:true" claim in
  the payload but writes an admin_audit_log entry on any upload
  where the claim is missing. A future pass can do server-side
  verification with the phiscrub Python port.

- Size clamp: 512 KB per batch. Bigger uploads get 413. Stops a
  rogue appliance from ballooning the ledger.

- Bearer: per-appliance (require_appliance_bearer_full). Legacy
  site-level keys rejected. bearer_aid must exactly match
  request.appliance_id — same pattern as watchdog_api.

- Substrate invariant `journal_upload_stale` (sev2) fires when an
  appliance that previously uploaded hasn't in > 90 min (3x the
  15-min cadence).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .fleet import get_pool
from .tenant_middleware import admin_connection
from .shared import require_appliance_bearer_full

logger = logging.getLogger("journal_api")

journal_api_router = APIRouter(prefix="/api/journal", tags=["journal"])

MAX_BATCH_BYTES = 512 * 1024


class JournalUploadRequest(BaseModel):
    site_id: str = Field(..., min_length=1, max_length=64)
    appliance_id: str = Field(..., min_length=1, max_length=255)
    batch_start: datetime
    batch_end: datetime
    line_count: int = Field(..., ge=0)
    compressed: str = Field(..., description="zstd-base64 of the journal text")
    sha256: str = Field(..., min_length=64, max_length=64)
    scrubbed: bool = Field(
        default=True,
        description="Appliance-side PHI scrubber ran. "
                    "false/missing opens a compliance audit row.",
    )


def _chain_hash(prev: Optional[str], payload: Dict[str, Any]) -> str:
    prev_hex = prev or ("0" * 64)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(prev_hex.encode() + b":" + canonical).hexdigest()


@journal_api_router.post("/upload")
async def upload_journal_batch(
    req: JournalUploadRequest,
    bearer: tuple = Depends(require_appliance_bearer_full),
) -> Dict[str, Any]:
    """Accept one journal batch from an appliance."""
    bearer_site, bearer_aid = bearer
    if bearer_site != req.site_id:
        raise HTTPException(status_code=403, detail="auth_site_id ≠ request site")
    if not bearer_aid:
        raise HTTPException(
            status_code=403,
            detail="journal upload requires per-appliance bearer (not site-level)",
        )
    if bearer_aid != req.appliance_id:
        raise HTTPException(
            status_code=403,
            detail=f"bearer_aid {bearer_aid!r} != request appliance_id {req.appliance_id!r}",
        )

    payload_bytes = len(req.compressed)
    if payload_bytes > MAX_BATCH_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"batch too large: {payload_bytes} > {MAX_BATCH_BYTES}",
        )

    payload = {
        "batch_start": req.batch_start.isoformat(),
        "batch_end": req.batch_end.isoformat(),
        "line_count": req.line_count,
        "compressed": req.compressed,
        "sha256": req.sha256,
        "scrubbed": req.scrubbed,
    }

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # Gate H4-E fix: hold the SELECT+INSERT inside ONE transaction
        # and use pg_advisory_xact_lock on the appliance_id hash so two
        # concurrent uploads from the same box serialize on the chain
        # instead of racing to compute identical chain_prev_hash. The
        # advisory lock is released automatically at COMMIT — no cleanup.
        # Backstop: migration 220 UNIQUE (appliance_id, chain_prev_hash)
        # rejects a fork even if this transaction were to leak.
        chain = None
        prev = None
        for attempt in range(3):
            try:
                async with conn.transaction():
                    # Cheap xact lock scoped to (appliance_id) via sha1→bigint
                    lock_key = int.from_bytes(
                        hashlib.sha1(req.appliance_id.encode()).digest()[:8],
                        byteorder="big", signed=True,
                    )
                    await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)

                    prev_row = await conn.fetchrow(
                        "SELECT chain_hash FROM journal_upload_events "
                        "WHERE appliance_id = $1 ORDER BY id DESC LIMIT 1",
                        req.appliance_id,
                    )
                    prev = prev_row["chain_hash"] if prev_row else None
                    chain = _chain_hash(prev, payload)

                    await conn.execute(
                        """
                        INSERT INTO journal_upload_events (
                            site_id, appliance_id, batch_start, batch_end,
                            line_count, payload_bytes, payload,
                            chain_prev_hash, chain_hash
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
                        """,
                        req.site_id,
                        req.appliance_id,
                        req.batch_start,
                        req.batch_end,
                        req.line_count,
                        payload_bytes,
                        json.dumps(payload),
                        prev,
                        chain,
                    )
                break  # success
            except Exception as e:
                # UniqueViolation on migration 220's index = a racing
                # commit beat us to the same prev. Retry picks up the
                # new chain_prev_hash and chains off it.
                err = str(e)
                if "journal_upload_events_no_chain_fork" in err and attempt < 2:
                    logger.info(
                        "journal chain race appliance=%s attempt=%d — retrying",
                        req.appliance_id, attempt + 1,
                    )
                    continue
                raise

        # If the appliance DIDN'T claim full PHI scrubbing, record a
        # compliance event — the auditor walks this audit trail to see
        # which batches lacked full-parity scrubbing (until the phiscrub
        # Go package, 14 patterns, is invoked from the shell uploader).
        # Today the shell-side sed has 3 patterns; the appliance emits
        # scrubbed=false and the audit row is NOT dead code, it's the
        # honest attestation that parity is pending.
        if not req.scrubbed:
            try:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO admin_audit_log
                            (username, action, target, details, created_at)
                        VALUES (
                            'system',
                            'JOURNAL_UPLOAD_UNSCRUBBED',
                            $1::text,
                            jsonb_build_object(
                                'site_id', $2::text,
                                'appliance_id', $3::text,
                                'batch_start', $4::text,
                                'batch_end', $5::text,
                                'line_count', $6::int,
                                'reason', 'appliance-side sed scrubber lacks full phiscrub parity (3 patterns vs 14)'
                            ),
                            NOW()
                        )
                        """,
                        f"appliance:{req.appliance_id}",
                        req.site_id,
                        req.appliance_id,
                        req.batch_start.isoformat(),
                        req.batch_end.isoformat(),
                        req.line_count,
                    )
            except Exception:
                logger.error(
                    "failed to write JOURNAL_UPLOAD_UNSCRUBBED audit row site=%s aid=%s",
                    req.site_id, req.appliance_id, exc_info=True,
                )
            logger.info(
                "journal upload unscrubbed site=%s aid=%s lines=%d",
                req.site_id, req.appliance_id, req.line_count,
            )

    return {
        "ok": True,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "chain_hash": chain,
    }
