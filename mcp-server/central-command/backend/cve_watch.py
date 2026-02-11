"""CVE Watch API — Progressive Vulnerability Coverage Tracking.

Syncs CVE data from NVD API v2.0, matches vulnerabilities to fleet
appliances via CPE, and tracks remediation/mitigation status.
Supports HIPAA 164.308(a)(1) risk analysis requirements.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field

from .fleet import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cve-watch", tags=["cve-watch"])

NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"

# Severity ordering for consistent sorting
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}


# =============================================================================
# Pydantic Models
# =============================================================================

class CVEStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(open|mitigated|accepted_risk|not_affected)$")
    notes: Optional[str] = None


class CVEWatchConfigUpdate(BaseModel):
    watched_cpes: Optional[List[str]] = None
    sync_interval_hours: Optional[int] = Field(None, ge=1, le=168)
    min_severity: Optional[str] = Field(None, pattern="^(critical|high|medium|low)$")
    enabled: Optional[bool] = None
    nvd_api_key: Optional[str] = None


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/summary")
async def get_cve_summary():
    """Global CVE summary with counts by severity and coverage percentage."""
    pool = await get_pool()

    # Severity counts
    severity_rows = await pool.fetch("""
        SELECT severity, COUNT(*) as cnt
        FROM cve_entries
        GROUP BY severity
    """)
    by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    total_cves = 0
    for row in severity_rows:
        sev = row["severity"].lower()
        if sev in by_severity:
            by_severity[sev] = row["cnt"]
        total_cves += row["cnt"]

    # Status counts
    status_rows = await pool.fetch("""
        SELECT status, COUNT(*) as cnt
        FROM cve_fleet_matches
        GROUP BY status
    """)
    by_status = {"open": 0, "mitigated": 0, "accepted_risk": 0, "not_affected": 0}
    for row in status_rows:
        st = row["status"]
        if st in by_status:
            by_status[st] = row["cnt"]

    total_matches = sum(by_status.values())
    covered = by_status["mitigated"] + by_status["not_affected"]
    coverage_pct = round((covered / total_matches * 100) if total_matches > 0 else 0, 1)

    # Config info
    config = await pool.fetchrow("SELECT last_sync_at, watched_cpes FROM cve_watch_config LIMIT 1")

    watched = config["watched_cpes"] if config else []
    if isinstance(watched, str):
        watched = json.loads(watched)

    return {
        "total_cves": total_cves,
        "by_severity": by_severity,
        "by_status": by_status,
        "coverage_pct": coverage_pct,
        "last_sync": config["last_sync_at"].isoformat() if config and config["last_sync_at"] else None,
        "watched_cpes": watched,
    }


@router.get("/cves")
async def list_cves(
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List CVEs with optional filters."""
    pool = await get_pool()

    conditions = []
    params = []
    param_idx = 1

    if severity:
        conditions.append(f"c.severity = ${param_idx}")
        params.append(severity.lower())
        param_idx += 1

    if search:
        conditions.append(f"(c.cve_id ILIKE ${param_idx} OR c.description ILIKE ${param_idx})")
        params.append(f"%{search}%")
        param_idx += 1

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Status filter on fleet matches (subquery)
    having_clause = ""
    if status:
        having_clause = f"HAVING bool_or(fm.status = ${param_idx})"
        params.append(status)
        param_idx += 1

    query = f"""
        SELECT c.id, c.cve_id, c.severity, c.cvss_score, c.published_date,
               c.description, c.nvd_status,
               COUNT(fm.id) as affected_count,
               COALESCE(
                   (SELECT fm2.status FROM cve_fleet_matches fm2
                    WHERE fm2.cve_id = c.id
                    ORDER BY CASE fm2.status
                        WHEN 'open' THEN 0
                        WHEN 'mitigated' THEN 1
                        WHEN 'accepted_risk' THEN 2
                        WHEN 'not_affected' THEN 3
                    END
                    LIMIT 1),
                   'no_match'
               ) as aggregate_status
        FROM cve_entries c
        LEFT JOIN cve_fleet_matches fm ON fm.cve_id = c.id
        {where_clause}
        GROUP BY c.id
        {having_clause}
        ORDER BY
            CASE c.severity
                WHEN 'critical' THEN 0
                WHEN 'high' THEN 1
                WHEN 'medium' THEN 2
                WHEN 'low' THEN 3
                ELSE 4
            END,
            c.published_date DESC
        LIMIT ${param_idx} OFFSET ${param_idx + 1}
    """
    params.extend([limit, offset])

    rows = await pool.fetch(query, *params)

    return [
        {
            "id": str(row["id"]),
            "cve_id": row["cve_id"],
            "severity": row["severity"],
            "cvss_score": float(row["cvss_score"]) if row["cvss_score"] else None,
            "published_date": row["published_date"].isoformat() if row["published_date"] else None,
            "description": (row["description"] or "")[:300],
            "affected_count": row["affected_count"],
            "status": row["aggregate_status"],
        }
        for row in rows
    ]


@router.get("/cves/{cve_id}")
async def get_cve_detail(cve_id: str):
    """Get full CVE detail including affected appliances."""
    pool = await get_pool()

    cve = await pool.fetchrow(
        "SELECT * FROM cve_entries WHERE cve_id = $1", cve_id
    )
    if not cve:
        raise HTTPException(status_code=404, detail=f"CVE {cve_id} not found")

    matches = await pool.fetch("""
        SELECT fm.appliance_id, fm.site_id, fm.status, fm.notes,
               fm.mitigated_at, fm.mitigated_by
        FROM cve_fleet_matches fm
        WHERE fm.cve_id = $1
        ORDER BY fm.status, fm.appliance_id
    """, cve["id"])

    return {
        "id": str(cve["id"]),
        "cve_id": cve["cve_id"],
        "severity": cve["severity"],
        "cvss_score": float(cve["cvss_score"]) if cve["cvss_score"] else None,
        "published_date": cve["published_date"].isoformat() if cve["published_date"] else None,
        "last_modified": cve["last_modified"].isoformat() if cve["last_modified"] else None,
        "description": cve["description"],
        "references": cve["refs"] or [],
        "cwe_ids": cve["cwe_ids"] or [],
        "nvd_status": cve["nvd_status"],
        "affected_count": len(matches),
        "status": matches[0]["status"] if matches else "no_match",
        "affected_appliances": [
            {
                "appliance_id": m["appliance_id"],
                "site_id": m["site_id"],
                "status": m["status"],
                "notes": m["notes"],
                "mitigated_at": m["mitigated_at"].isoformat() if m["mitigated_at"] else None,
                "mitigated_by": m["mitigated_by"],
            }
            for m in matches
        ],
    }


@router.put("/cves/{cve_id}/status")
async def update_cve_status(cve_id: str, body: CVEStatusUpdate):
    """Update status for all fleet matches of a CVE."""
    pool = await get_pool()

    cve = await pool.fetchrow(
        "SELECT id FROM cve_entries WHERE cve_id = $1", cve_id
    )
    if not cve:
        raise HTTPException(status_code=404, detail=f"CVE {cve_id} not found")

    mitigated_at = datetime.now(timezone.utc) if body.status == "mitigated" else None

    updated = await pool.execute("""
        UPDATE cve_fleet_matches
        SET status = $1, notes = COALESCE($2, notes),
            mitigated_at = COALESCE($3, mitigated_at),
            updated_at = NOW()
        WHERE cve_id = $4
    """, body.status, body.notes, mitigated_at, cve["id"])

    return {"status": "updated", "cve_id": cve_id, "new_status": body.status}


@router.post("/sync")
async def trigger_sync(background_tasks: BackgroundTasks):
    """Trigger manual NVD CVE sync."""
    background_tasks.add_task(_run_sync)
    return {"status": "sync_started"}


@router.get("/config")
async def get_config():
    """Get CVE Watch configuration."""
    pool = await get_pool()
    config = await pool.fetchrow("SELECT * FROM cve_watch_config LIMIT 1")

    if not config:
        return {
            "watched_cpes": [],
            "sync_interval_hours": 6,
            "min_severity": "medium",
            "enabled": False,
            "has_api_key": False,
            "last_sync_at": None,
        }

    cpes = config["watched_cpes"] or []
    if isinstance(cpes, str):
        cpes = json.loads(cpes)

    return {
        "watched_cpes": cpes,
        "sync_interval_hours": config["sync_interval_hours"],
        "min_severity": config["min_severity"],
        "enabled": config["enabled"],
        "has_api_key": bool(config["nvd_api_key"]),
        "last_sync_at": config["last_sync_at"].isoformat() if config["last_sync_at"] else None,
    }


@router.put("/config")
async def update_config(body: CVEWatchConfigUpdate):
    """Update CVE Watch configuration."""
    pool = await get_pool()

    config = await pool.fetchrow("SELECT id FROM cve_watch_config LIMIT 1")
    if not config:
        raise HTTPException(status_code=404, detail="CVE Watch config not initialized")

    updates = []
    params = []
    param_idx = 1

    if body.watched_cpes is not None:
        updates.append(f"watched_cpes = ${param_idx}::jsonb")
        params.append(json.dumps(body.watched_cpes))
        param_idx += 1

    if body.sync_interval_hours is not None:
        updates.append(f"sync_interval_hours = ${param_idx}")
        params.append(body.sync_interval_hours)
        param_idx += 1

    if body.min_severity is not None:
        updates.append(f"min_severity = ${param_idx}")
        params.append(body.min_severity)
        param_idx += 1

    if body.enabled is not None:
        updates.append(f"enabled = ${param_idx}")
        params.append(body.enabled)
        param_idx += 1

    if body.nvd_api_key is not None:
        updates.append(f"nvd_api_key = ${param_idx}")
        params.append(body.nvd_api_key)
        param_idx += 1

    if not updates:
        return {"status": "no_changes"}

    updates.append("updated_at = NOW()")
    params.append(config["id"])

    await pool.execute(
        f"UPDATE cve_watch_config SET {', '.join(updates)} WHERE id = ${param_idx}",
        *params
    )

    return {"status": "updated"}


# =============================================================================
# NVD Sync Logic
# =============================================================================

async def _run_sync():
    """Wrapper for background task sync."""
    try:
        pool = await get_pool()
        await _sync_nvd_cves(pool)
    except Exception as e:
        logger.error(f"CVE sync failed: {e}")


async def _sync_nvd_cves(pool):
    """Fetch new/modified CVEs from NVD API v2.0."""
    config = await pool.fetchrow("SELECT * FROM cve_watch_config LIMIT 1")
    if not config or not config["enabled"]:
        logger.info("CVE Watch disabled or not configured")
        return

    watched_cpes = config["watched_cpes"] or []
    # Handle JSONB returned as string (double-encoded) vs native list
    if isinstance(watched_cpes, str):
        watched_cpes = json.loads(watched_cpes)
    api_key = config.get("nvd_api_key")
    last_sync = config["last_sync_at"]
    min_severity = config["min_severity"] or "medium"

    if not watched_cpes:
        logger.info("No CPEs configured for CVE Watch")
        return

    headers = {}
    if api_key:
        headers["apiKey"] = api_key

    # Rate limit delay
    delay = 0.6 if api_key else 6.0

    total_synced = 0
    logger.info(f"Starting CVE sync for {len(watched_cpes)} CPEs (delay={delay}s)...")

    for i, cpe in enumerate(watched_cpes):
        try:
            if i > 0:
                await asyncio.sleep(delay)  # Rate limit between CPEs
            count = await _sync_cpe(pool, cpe, headers, last_sync, min_severity, delay)
            total_synced += count
            logger.info(f"  CPE {cpe}: {count} CVEs synced")
        except Exception as e:
            logger.error(f"  CPE {cpe} sync failed: {e}")

    # Match CVEs to fleet after sync
    matched = await _match_cves_to_fleet(pool)

    # Update sync timestamp
    await pool.execute("""
        UPDATE cve_watch_config
        SET last_sync_at = NOW(), last_sync_cve_count = $1, updated_at = NOW()
    """, total_synced)

    logger.info(f"CVE sync complete: {total_synced} CVEs synced, {matched} fleet matches")


async def _sync_cpe(pool, cpe: str, headers: dict, last_sync, min_severity: str, delay: float) -> int:
    """Sync CVEs for a single CPE string."""
    params = {
        "cpeName": cpe,
        "resultsPerPage": 200,
    }

    # Incremental sync: only fetch modified since last sync
    # NVD API v2.0 requires ISO 8601 with timezone offset
    if last_sync:
        params["lastModStartDate"] = last_sync.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
        params["lastModEndDate"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")

    # Severity filtering done post-fetch (cvssV3Severity param deprecated in NVD API v2.0)
    severity_min_rank = SEVERITY_ORDER.get(min_severity, 2)  # Default: medium

    count = 0
    start_index = 0
    max_retries = 3

    while True:
        params["startIndex"] = start_index

        for attempt in range(max_retries):
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(NVD_API_URL, params=params, headers=headers)
                if resp.status_code == 403 or resp.status_code == 429:
                    wait = 30 * (attempt + 1)
                    logger.warning(f"NVD API rate limited ({resp.status_code}), waiting {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                break
        else:
            logger.error(f"NVD API rate limit exceeded after {max_retries} retries")
            return count

        data = resp.json()

        for vuln in data.get("vulnerabilities", []):
            cve_data = vuln["cve"]
            # Filter by minimum severity
            sev = _extract_severity(cve_data)
            if SEVERITY_ORDER.get(sev, 4) <= severity_min_rank:
                await _upsert_cve(pool, cve_data)
                count += 1

        total_results = data.get("totalResults", 0)
        if start_index + 200 >= total_results:
            break
        start_index += 200

        await asyncio.sleep(delay)

    return count


def _extract_severity(cve_data: dict) -> str:
    """Extract severity from CVE metrics without full parsing."""
    metrics = cve_data.get("metrics", {})
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        metric_list = metrics.get(key, [])
        if metric_list:
            return metric_list[0].get("cvssData", {}).get("baseSeverity", "unknown").lower()
    return "unknown"


async def _upsert_cve(pool, cve_data: dict):
    """Insert or update a CVE entry from NVD API response."""
    cve_id = cve_data.get("id", "")

    # Extract CVSS v3.1 score and severity
    cvss_score = None
    severity = "unknown"
    metrics = cve_data.get("metrics", {})

    for metric_key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        metric_list = metrics.get(metric_key, [])
        if metric_list:
            cvss_data = metric_list[0].get("cvssData", {})
            cvss_score = cvss_data.get("baseScore")
            severity = cvss_data.get("baseSeverity", "unknown").lower()
            break

    # Extract description (English)
    description = ""
    for desc in cve_data.get("descriptions", []):
        if desc.get("lang") == "en":
            description = desc.get("value", "")
            break

    # Extract affected CPE configurations
    affected_cpes = []
    for config in cve_data.get("configurations", []):
        for node in config.get("nodes", []):
            for match in node.get("cpeMatch", []):
                if match.get("vulnerable"):
                    affected_cpes.append({
                        "criteria": match.get("criteria", ""),
                        "versionStartIncluding": match.get("versionStartIncluding"),
                        "versionEndExcluding": match.get("versionEndExcluding"),
                        "versionEndIncluding": match.get("versionEndIncluding"),
                    })

    # Extract references
    refs = [
        {"url": ref.get("url", ""), "source": ref.get("source", "")}
        for ref in cve_data.get("references", [])
    ]

    # Extract CWE IDs
    cwe_ids = []
    for weakness in cve_data.get("weaknesses", []):
        for desc in weakness.get("description", []):
            cwe_id = desc.get("value", "")
            if cwe_id.startswith("CWE-"):
                cwe_ids.append(cwe_id)

    published = cve_data.get("published")
    last_modified = cve_data.get("lastModified")
    nvd_status = cve_data.get("vulnStatus")

    await pool.execute("""
        INSERT INTO cve_entries (
            cve_id, severity, cvss_score, published_date, last_modified,
            description, affected_cpes, refs, cwe_ids, nvd_status, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10, NOW())
        ON CONFLICT (cve_id) DO UPDATE SET
            severity = EXCLUDED.severity,
            cvss_score = EXCLUDED.cvss_score,
            last_modified = EXCLUDED.last_modified,
            description = EXCLUDED.description,
            affected_cpes = EXCLUDED.affected_cpes,
            refs = EXCLUDED.refs,
            cwe_ids = EXCLUDED.cwe_ids,
            nvd_status = EXCLUDED.nvd_status,
            updated_at = NOW()
    """,
        cve_id,
        severity,
        cvss_score,
        _parse_nvd_date(published),
        _parse_nvd_date(last_modified),
        description,
        json.dumps(affected_cpes),
        json.dumps(refs),
        cwe_ids,
        nvd_status,
    )


def _parse_nvd_date(date_str) -> Optional[datetime]:
    """Parse NVD date string to datetime."""
    if not date_str:
        return None
    try:
        # NVD format: 2024-01-15T10:30:00.000
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# =============================================================================
# Fleet Matching
# =============================================================================

async def _match_cves_to_fleet(pool) -> int:
    """Match CVEs to fleet appliances based on CPE criteria."""
    # Get all online appliances
    appliances = await pool.fetch("""
        SELECT appliance_id, site_id, agent_version, nixos_version
        FROM site_appliances
        WHERE status = 'online'
    """)

    if not appliances:
        return 0

    # Get CVEs that don't have matches yet (or re-match all for new appliances)
    cves = await pool.fetch("""
        SELECT id, cve_id, affected_cpes FROM cve_entries
    """)

    matched = 0
    for cve in cves:
        affected_cpes = cve["affected_cpes"] or []
        for appliance in appliances:
            if _cpe_matches_appliance(affected_cpes, appliance):
                try:
                    await pool.execute("""
                        INSERT INTO cve_fleet_matches (cve_id, appliance_id, site_id, match_reason)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (cve_id, appliance_id) DO NOTHING
                    """,
                        cve["id"],
                        appliance["appliance_id"],
                        appliance["site_id"],
                        "CPE version match",
                    )
                    matched += 1
                except Exception:
                    pass  # Duplicate or constraint violation

    return matched


def _cpe_matches_appliance(affected_cpes: list, appliance: dict) -> bool:
    """Check if any affected CPE matches an appliance's software profile.

    Simple keyword matching — maps CPE vendor:product to appliance metadata.
    A more sophisticated implementation would parse CPE version ranges.
    """
    for cpe_match in affected_cpes:
        criteria = cpe_match.get("criteria", "").lower()

        # Windows Server
        if "microsoft:windows_server" in criteria:
            return True  # All Windows targets are potentially affected

        # Windows 10/11 workstations
        if "microsoft:windows_10" in criteria or "microsoft:windows_11" in criteria:
            return True

        # Ubuntu
        if "canonical:ubuntu" in criteria:
            return True

        # OpenSSH (affects all Linux targets)
        if "openssh" in criteria:
            return True

        # Python
        if "python:python" in criteria:
            agent_version = appliance.get("agent_version", "")
            if agent_version:
                return True

        # NixOS
        if "nix:nixos" in criteria or "nixos" in criteria:
            if appliance.get("nixos_version"):
                return True

    return False


# =============================================================================
# Background Sync Loop (called from main.py startup)
# =============================================================================

async def cve_sync_loop():
    """Periodic CVE sync background task. Call via asyncio.create_task() on startup."""
    await asyncio.sleep(120)  # Wait 2 min after startup
    while True:
        try:
            pool = await get_pool()
            config = await pool.fetchrow(
                "SELECT sync_interval_hours, enabled FROM cve_watch_config LIMIT 1"
            )
            if config and config["enabled"]:
                await _sync_nvd_cves(pool)
                interval = config["sync_interval_hours"] * 3600
            else:
                interval = 3600  # Check again in 1 hour if disabled
        except Exception as e:
            logger.error(f"CVE sync loop error: {e}")
            interval = 3600

        await asyncio.sleep(interval)
