"""Security event archival — WORM storage for OCR audit readiness.

Receives PHI-scrubbed Windows security events from appliance daemons
and archives them to both PostgreSQL (partitioned) and MinIO (object lock).
Events are append-only; no UPDATE or DELETE is permitted.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .fleet import get_pool
from .tenant_middleware import admin_connection
from .sites import require_appliance_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/security-events", tags=["security-events"])

MAX_BATCH_SIZE = 500
MAX_MESSAGE_LEN = 2048


# =============================================================================
# MODELS
# =============================================================================

class SecurityEventEntry(BaseModel):
    event_id: int
    timestamp: str  # ISO 8601 timestamp
    hostname: str
    message: str
    source_host: Optional[str] = None
    category: Optional[str] = None
    severity: Optional[str] = None


class SecurityEventBatch(BaseModel):
    events: List[SecurityEventEntry] = Field(max_length=MAX_BATCH_SIZE)


# =============================================================================
# EVENT CATEGORY MAPPING
# =============================================================================

EVENT_CATEGORIES = {
    4624: "authentication", 4625: "authentication", 4634: "authentication",
    4648: "authentication",
    4720: "account_management", 4722: "account_management",
    4724: "account_management", 4725: "account_management",
    4726: "account_management",
    4728: "group_management", 4732: "group_management",
    4735: "group_management", 4756: "group_management",
    4740: "account_lockout", 4767: "account_lockout",
    4768: "kerberos", 4771: "kerberos", 4776: "kerberos",
    4946: "firewall", 4947: "firewall", 4950: "firewall",
    1102: "audit_log",
}

EVENT_SEVERITY = {
    1102: "critical",  # Audit log cleared
    4625: "warning",   # Failed logon
    4740: "warning",   # Account lockout
    4771: "warning",   # Kerberos pre-auth failed
    4720: "notice",    # Account created
    4722: "notice",    # Account enabled
    4724: "notice",    # Password reset
    4725: "notice",    # Account disabled
    4726: "notice",    # Account deleted
    4728: "notice",    # Group membership change
    4732: "notice",    # Local group change
    4735: "notice",    # Local group changed
    4756: "notice",    # Universal group change
    4946: "warning",   # Firewall rule added
    4947: "warning",   # Firewall rule modified
    4950: "warning",   # Firewall setting changed
}


# =============================================================================
# ARCHIVAL ENDPOINT
# =============================================================================

@router.post("/archive")
async def archive_security_events(request: Request):
    """Archive sanitized security events to WORM storage for audit compliance.

    Events are already PHI-scrubbed by the appliance daemon before transmission.
    This endpoint stores them in:
    1. PostgreSQL security_events table (partitioned by month, append-only)
    2. MinIO WORM bucket with object lock (when configured)

    Authenticated via appliance Bearer token.
    """
    site_id = await require_appliance_auth(request)

    body = await request.json()
    events = body.get("events", [])

    if not events:
        return {"status": "ok", "archived": 0}

    if len(events) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Batch too large: {len(events)} events (max {MAX_BATCH_SIZE})"
        )

    pool = await get_pool()
    archived = 0

    async with admin_connection(pool) as conn:
        # Batch insert using executemany for efficiency
        rows = []
        for ev in events:
            event_id = ev.get("event_id", 0)
            timestamp_str = ev.get("timestamp", "")
            hostname = ev.get("hostname", "unknown")
            message = ev.get("message", "")

            if not timestamp_str or not message:
                continue

            # Truncate message for storage
            if len(message) > MAX_MESSAGE_LEN:
                message = message[:MAX_MESSAGE_LEN]

            # Parse timestamp — Go daemon sends ISO8601 strings with fractional
            # seconds and Z suffix. asyncpg needs datetime objects, not strings.
            try:
                event_ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                event_ts = datetime.now(timezone.utc)

            # Derive category and severity from event ID
            category = ev.get("category") or EVENT_CATEGORIES.get(event_id, "other")
            severity = ev.get("severity") or EVENT_SEVERITY.get(event_id, "info")
            source_host = ev.get("source_host", hostname)

            rows.append((
                site_id, hostname, event_id, event_ts,
                message, source_host, category, severity
            ))

        if rows:
            try:
                await conn.executemany(
                    """INSERT INTO security_events
                       (site_id, hostname, event_id, event_timestamp,
                        message, source_host, category, severity)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                    rows
                )
                archived = len(rows)
            except Exception as e:
                logger.error(f"Security event archival failed for {site_id}: {e}")
                raise HTTPException(status_code=500, detail="Archival failed")

    # MinIO WORM write (best-effort, does not block response)
    if archived > 0:
        try:
            await _write_to_minio_worm(site_id, events)
        except Exception as e:
            # Log but don't fail — PostgreSQL is the primary store
            logger.warning(f"MinIO WORM write failed for {site_id}: {e}")

    logger.info(f"Archived {archived} security events for site {site_id}")
    return {"status": "ok", "archived": archived}


async def _write_to_minio_worm(site_id: str, events: list):
    """Write events to MinIO bucket with object lock for evidence-grade retention.

    This is a best-effort write — if MinIO is not configured, it silently skips.
    Object lock ensures events cannot be deleted or overwritten (WORM compliance).
    """
    try:
        import boto3
        from botocore.config import Config as BotoConfig
        import os

        endpoint = os.getenv("MINIO_ENDPOINT")
        if not endpoint:
            return  # MinIO not configured — skip

        # boto3 needs full URL; minio library accepts bare hostname
        endpoint_url = endpoint if endpoint.startswith("http") else f"http://{endpoint}"

        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", ""),
            aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", ""),
            config=BotoConfig(signature_version="s3v4"),
        )

        bucket = os.getenv("MINIO_WORM_BUCKET", "security-events-worm")
        now = datetime.now(timezone.utc)
        key = f"{site_id}/{now.strftime('%Y/%m/%d')}/{now.strftime('%H%M%S')}_{len(events)}.json"

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(events).encode("utf-8"),
            ContentType="application/json",
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=now.replace(year=now.year + 7),  # 7-year HIPAA retention
        ))
        logger.debug(f"WORM write: {bucket}/{key} ({len(events)} events)")
    except ImportError:
        pass  # boto3 not installed — MinIO feature unavailable
    except Exception as e:
        raise  # Re-raise for caller to log


# =============================================================================
# QUERY ENDPOINTS (admin dashboard)
# =============================================================================

@router.get("/search")
async def search_security_events(
    request: Request,
    site_id: Optional[str] = None,
    hostname: Optional[str] = None,
    event_id: Optional[int] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
):
    """Search archived security events for audit/investigation.

    Admin-only endpoint for the dashboard.
    """
    from .auth import require_auth
    await require_auth(request)

    if limit > 1000:
        limit = 1000

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        conditions = []
        params = []
        idx = 1

        if site_id:
            conditions.append(f"site_id = ${idx}")
            params.append(site_id)
            idx += 1

        if hostname:
            conditions.append(f"hostname = ${idx}")
            params.append(hostname)
            idx += 1

        if event_id is not None:
            conditions.append(f"event_id = ${idx}")
            params.append(event_id)
            idx += 1

        if category:
            conditions.append(f"category = ${idx}")
            params.append(category)
            idx += 1

        if severity:
            conditions.append(f"severity = ${idx}")
            params.append(severity)
            idx += 1

        if since:
            conditions.append(f"event_timestamp >= ${idx}::timestamptz")
            params.append(since)
            idx += 1

        where = " AND ".join(conditions) if conditions else "TRUE"
        params.append(limit)

        rows = await conn.fetch(
            f"""SELECT id, site_id, hostname, event_id, event_timestamp,
                       message, source_host, category, severity, created_at
                FROM security_events
                WHERE {where}
                ORDER BY event_timestamp DESC
                LIMIT ${idx}""",
            *params
        )

        return {
            "events": [
                {
                    "id": r["id"],
                    "site_id": r["site_id"],
                    "hostname": r["hostname"],
                    "event_id": r["event_id"],
                    "event_timestamp": r["event_timestamp"].isoformat() if r["event_timestamp"] else None,
                    "message": r["message"],
                    "source_host": r["source_host"],
                    "category": r["category"],
                    "severity": r["severity"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ],
            "count": len(rows),
        }
