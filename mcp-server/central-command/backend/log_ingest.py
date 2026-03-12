"""Log ingestion and search endpoints.

Receives batched log entries from appliance daemons and provides
search/export for the admin dashboard and portals.
"""

import gzip
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field

from .fleet import get_pool
from .auth import require_auth
from .tenant_middleware import tenant_connection, admin_connection
from .sites import require_appliance_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/logs", tags=["logs"])

# Syslog priority labels
PRIORITY_LABELS = {
    0: "emerg", 1: "alert", 2: "crit", 3: "err",
    4: "warning", 5: "notice", 6: "info", 7: "debug",
}

MAX_BATCH_SIZE = 1000
MAX_MESSAGE_LEN = 8192


# =============================================================================
# MODELS
# =============================================================================

class LogEntry(BaseModel):
    ts: str  # ISO timestamp
    unit: str
    pri: int = Field(ge=0, le=7, default=6)
    msg: str
    boot: Optional[str] = None


class LogBatch(BaseModel):
    site_id: str
    hostname: str
    batch: List[LogEntry]


# =============================================================================
# INGEST ENDPOINT (appliance → Central Command)
# =============================================================================

@router.post("/ingest")
async def ingest_logs(request: Request):
    """Receive a batch of log entries from an appliance daemon.

    Accepts JSON or gzip-compressed JSON. Authenticated via Bearer token.
    """
    # Auth
    site_id = await require_appliance_auth(request)

    # Parse body (handle gzip)
    content_encoding = request.headers.get("content-encoding", "")
    raw_body = await request.body()

    if content_encoding == "gzip":
        try:
            raw_body = gzip.decompress(raw_body)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid gzip payload")

    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    hostname = payload.get("hostname", "unknown")
    batch = payload.get("batch", [])

    if not batch:
        return {"accepted": 0, "dropped": 0}

    if len(batch) > MAX_BATCH_SIZE:
        batch = batch[:MAX_BATCH_SIZE]

    # Validate and prepare records
    records = []
    dropped = 0
    for entry in batch:
        try:
            ts = entry.get("ts", "")
            unit = entry.get("unit", "unknown")[:128]
            pri = max(0, min(7, int(entry.get("pri", 6))))
            msg = entry.get("msg", "")[:MAX_MESSAGE_LEN]
            boot_id = entry.get("boot", None)

            if not ts or not msg:
                dropped += 1
                continue

            # Parse timestamp
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            parsed_ts = datetime.fromisoformat(ts)

            records.append((
                site_id, hostname, unit, pri, parsed_ts, msg,
                boot_id[:64] if boot_id else None
            ))
        except Exception:
            dropped += 1

    if not records:
        return {"accepted": 0, "dropped": dropped}

    # Bulk insert
    pool = await get_pool()
    async with tenant_connection(pool, site_id=site_id) as conn:
        await conn.executemany("""
            INSERT INTO log_entries (site_id, hostname, unit, priority, timestamp, message, boot_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, records)

    logger.info(f"Ingested {len(records)} log entries for site={site_id} host={hostname}")
    return {"accepted": len(records), "dropped": dropped}


# =============================================================================
# SEARCH ENDPOINT (dashboard)
# =============================================================================

@router.get("/search")
async def search_logs(
    request: Request,
    site_id: str = Query(...),
    start: Optional[str] = Query(None, description="ISO timestamp start"),
    end: Optional[str] = Query(None, description="ISO timestamp end"),
    unit: Optional[str] = Query(None),
    priority: Optional[int] = Query(None, ge=0, le=7),
    q: Optional[str] = Query(None, description="Full-text search"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: dict = None,
):
    """Search log entries with filters. Admin dashboard endpoint."""
    from .auth import require_auth
    user = await require_auth(request)

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # Build query dynamically
        conditions = ["site_id = $1"]
        params = [site_id]
        idx = 2

        if start:
            conditions.append(f"timestamp >= ${idx}")
            ts = start.replace("Z", "+00:00") if start.endswith("Z") else start
            params.append(datetime.fromisoformat(ts))
            idx += 1

        if end:
            conditions.append(f"timestamp <= ${idx}")
            ts = end.replace("Z", "+00:00") if end.endswith("Z") else end
            params.append(datetime.fromisoformat(ts))
            idx += 1

        if unit:
            conditions.append(f"unit = ${idx}")
            params.append(unit)
            idx += 1

        if priority is not None:
            conditions.append(f"priority <= ${idx}")
            params.append(priority)
            idx += 1

        if q:
            conditions.append(f"to_tsvector('english', message) @@ plainto_tsquery('english', ${idx})")
            params.append(q)
            idx += 1

        where = " AND ".join(conditions)

        # Count total
        count_row = await conn.fetchval(
            f"SELECT COUNT(*) FROM log_entries WHERE {where}",
            *params
        )

        # Fetch page
        params.append(limit)
        params.append(offset)
        rows = await conn.fetch(
            f"""SELECT id, site_id, hostname, unit, priority, timestamp, message, boot_id
                FROM log_entries
                WHERE {where}
                ORDER BY timestamp DESC
                LIMIT ${idx} OFFSET ${idx + 1}""",
            *params
        )

        logs = [
            {
                "id": row["id"],
                "site_id": row["site_id"],
                "hostname": row["hostname"],
                "unit": row["unit"],
                "priority": row["priority"],
                "priority_label": PRIORITY_LABELS.get(row["priority"], "unknown"),
                "timestamp": row["timestamp"].isoformat(),
                "message": row["message"],
                "boot_id": row["boot_id"],
            }
            for row in rows
        ]

        return {
            "logs": logs,
            "total": count_row,
            "has_more": (offset + limit) < count_row,
        }


# =============================================================================
# UNITS ENDPOINT (for filter dropdown)
# =============================================================================

@router.get("/units")
async def get_log_units(
    request: Request,
    site_id: str = Query(...),
):
    """Return distinct log unit names for a site."""
    from .auth import require_auth
    await require_auth(request)

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT unit FROM log_entries WHERE site_id = $1 ORDER BY unit",
            site_id
        )
        return [row["unit"] for row in rows]


# =============================================================================
# EXPORT ENDPOINT
# =============================================================================

@router.get("/export")
async def export_logs(
    request: Request,
    site_id: str = Query(...),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    unit: Optional[str] = Query(None),
    priority: Optional[int] = Query(None, ge=0, le=7),
    q: Optional[str] = Query(None),
    format: str = Query("csv", regex="^(csv|json)$"),
):
    """Export logs as CSV or NDJSON. Streams response."""
    from .auth import require_auth
    from starlette.responses import StreamingResponse
    await require_auth(request)

    pool = await get_pool()

    async def generate():
        async with admin_connection(pool) as conn:
            conditions = ["site_id = $1"]
            params = [site_id]
            idx = 2

            if start:
                conditions.append(f"timestamp >= ${idx}")
                ts = start.replace("Z", "+00:00") if start.endswith("Z") else start
                params.append(datetime.fromisoformat(ts))
                idx += 1
            if end:
                conditions.append(f"timestamp <= ${idx}")
                ts = end.replace("Z", "+00:00") if end.endswith("Z") else end
                params.append(datetime.fromisoformat(ts))
                idx += 1
            if unit:
                conditions.append(f"unit = ${idx}")
                params.append(unit)
                idx += 1
            if priority is not None:
                conditions.append(f"priority <= ${idx}")
                params.append(priority)
                idx += 1
            if q:
                conditions.append(f"to_tsvector('english', message) @@ plainto_tsquery('english', ${idx})")
                params.append(q)
                idx += 1

            where = " AND ".join(conditions)

            if format == "csv":
                yield "timestamp,hostname,unit,priority,message\n"

            rows = await conn.fetch(
                f"""SELECT hostname, unit, priority, timestamp, message
                    FROM log_entries WHERE {where}
                    ORDER BY timestamp DESC LIMIT 10000""",
                *params
            )
            for row in rows:
                if format == "csv":
                    msg = row["message"].replace('"', '""')
                    yield f'{row["timestamp"].isoformat()},{row["hostname"]},{row["unit"]},{row["priority"]},"{msg}"\n'
                else:
                    yield json.dumps({
                        "timestamp": row["timestamp"].isoformat(),
                        "hostname": row["hostname"],
                        "unit": row["unit"],
                        "priority": row["priority"],
                        "message": row["message"],
                    }) + "\n"

    media = "text/csv" if format == "csv" else "application/x-ndjson"
    filename = f"logs-{site_id}.{format}"
    return StreamingResponse(
        generate(),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# =============================================================================
# PARTITION MAINTENANCE (called from background task)
# =============================================================================

async def maintain_log_partitions(pool):
    """Create future partitions and drop old ones (>90 days)."""
    try:
        async with admin_connection(pool) as conn:
            # Create partitions for next 2 months
            for i in range(3):
                await conn.execute(f"""
                    DO $$
                    DECLARE
                        start_date DATE := date_trunc('month', CURRENT_DATE + interval '{i} months')::date;
                        end_date DATE := (start_date + interval '1 month')::date;
                        part_name TEXT := 'log_entries_' || to_char(start_date, 'YYYY_MM');
                    BEGIN
                        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = part_name) THEN
                            EXECUTE format(
                                'CREATE TABLE %I PARTITION OF log_entries FOR VALUES FROM (%L) TO (%L)',
                                part_name, start_date, end_date
                            );
                            RAISE NOTICE 'Created partition %', part_name;
                        END IF;
                    END $$;
                """)

            # Drop partitions older than 90 days
            cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y_%m")
            parts = await conn.fetch("""
                SELECT relname FROM pg_class
                WHERE relname LIKE 'log_entries_%' AND relkind = 'r'
                ORDER BY relname
            """)
            for row in parts:
                name = row["relname"]
                # Extract YYYY_MM from partition name
                suffix = name.replace("log_entries_", "")
                if suffix < cutoff:
                    await conn.execute(f"DROP TABLE IF EXISTS {name}")
                    logger.info(f"Dropped expired log partition: {name}")

    except Exception as e:
        logger.warning(f"Log partition maintenance error: {e}")
