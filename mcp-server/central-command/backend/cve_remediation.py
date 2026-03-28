"""CVE-to-Runbook auto-remediation engine.

Generates preventative runbooks from CVE fleet matches and optionally
triggers L1 auto-remediation for full-coverage service tier clients.

Architecture:
  - process_new_cve_matches() is the entry point, called periodically
    by the background task loop (every 30 min) or after a CVE sync.
  - generate_cve_runbook() creates a runbook for a CVE + fleet match.
  - auto_remediate_cve() creates an L1 rule for full-coverage sites.
  - All operations are idempotent (safe to re-run).

Follows the same patterns as learning_api.py and protection_profiles.py
for runbook/L1 rule creation.
"""

import json
from datetime import datetime, timezone
from typing import Optional, Dict

import structlog

from .fleet import get_pool

logger = structlog.get_logger(__name__)


# =============================================================================
# Severity-to-check-type mapping for CVE runbook categorization
# =============================================================================

_CVE_CATEGORY_MAP = {
    "critical": "security",
    "high": "security",
    "medium": "patching",
    "low": "patching",
    "unknown": "patching",
}

_SEVERITY_HIPAA_MAP = {
    "critical": ["164.308(a)(1)(ii)(A)", "164.308(a)(5)(ii)(B)", "164.312(a)(1)"],
    "high": ["164.308(a)(1)(ii)(A)", "164.308(a)(5)(ii)(B)"],
    "medium": ["164.308(a)(5)(ii)(B)"],
    "low": ["164.308(a)(5)(ii)(B)"],
    "unknown": [],
}


def _make_runbook_id(cve_id: str) -> str:
    """Derive a deterministic runbook ID from a CVE ID.

    Example: CVE-2024-12345 -> RB-CVE-2024-12345
    """
    return f"RB-CVE-{cve_id.replace('CVE-', '')}"


def _make_l1_rule_id(cve_id: str) -> str:
    """Derive a deterministic L1 rule ID from a CVE ID.

    Example: CVE-2024-12345 -> L1-CVE-2024-12345
    """
    return f"L1-CVE-{cve_id.replace('CVE-', '')}"


def _build_runbook_steps(cve_description: str, severity: str) -> list:
    """Build generic remediation steps based on CVE description heuristics.

    For real-world use, this would integrate with vendor advisory APIs.
    Currently generates sensible defaults based on keyword analysis.
    """
    desc_lower = (cve_description or "").lower()
    steps = []

    # Detect remediation type from description keywords
    if any(kw in desc_lower for kw in ("update", "upgrade", "patch", "version")):
        steps.append({
            "order": 1,
            "action": "detect",
            "description": "Check installed software version against CVE-affected range",
        })
        steps.append({
            "order": 2,
            "action": "remediate",
            "description": "Apply vendor security update or patch to fixed version",
        })
        steps.append({
            "order": 3,
            "action": "verify",
            "description": "Confirm updated version is no longer in CVE-affected range",
        })
    elif any(kw in desc_lower for kw in ("config", "setting", "misconfigur", "default")):
        steps.append({
            "order": 1,
            "action": "detect",
            "description": "Check current configuration against CVE mitigation requirements",
        })
        steps.append({
            "order": 2,
            "action": "remediate",
            "description": "Apply recommended configuration change per vendor advisory",
        })
        steps.append({
            "order": 3,
            "action": "verify",
            "description": "Validate configuration change resolves the vulnerability",
        })
    elif any(kw in desc_lower for kw in ("service", "daemon", "process", "restart")):
        steps.append({
            "order": 1,
            "action": "detect",
            "description": "Check if affected service is running a vulnerable version",
        })
        steps.append({
            "order": 2,
            "action": "remediate",
            "description": "Update and restart affected service with patched version",
        })
        steps.append({
            "order": 3,
            "action": "verify",
            "description": "Confirm service is running on non-vulnerable version",
        })
    else:
        # Generic fallback
        steps.append({
            "order": 1,
            "action": "detect",
            "description": "Identify affected software components on this system",
        })
        steps.append({
            "order": 2,
            "action": "remediate",
            "description": "Apply vendor-recommended remediation for this CVE",
        })
        steps.append({
            "order": 3,
            "action": "verify",
            "description": "Verify remediation and confirm system is no longer vulnerable",
        })

    return steps


# =============================================================================
# Core Functions
# =============================================================================

async def generate_cve_runbook(conn, cve_id: str, fleet_match_id: str) -> Optional[str]:
    """Generate a runbook for a CVE fleet match.

    Idempotent: if a runbook already exists for this CVE, returns the
    existing runbook_id without creating a duplicate.

    Args:
        conn: asyncpg connection (from pool.acquire())
        cve_id: The CVE identifier string (e.g., "CVE-2024-12345")
        fleet_match_id: UUID of the cve_fleet_matches row

    Returns:
        The runbook_id (e.g., "RB-CVE-2024-12345") or None on failure.
    """
    runbook_id = _make_runbook_id(cve_id)

    # Check if runbook already exists (idempotent)
    existing = await conn.fetchval(
        "SELECT runbook_id FROM runbooks WHERE runbook_id = $1", runbook_id
    )
    if existing:
        logger.info(f"CVE runbook already exists: {cve_id} -> {runbook_id}")
        return runbook_id

    # Look up CVE details
    cve = await conn.fetchrow(
        "SELECT cve_id, severity, cvss_score, description, affected_cpes FROM cve_entries WHERE cve_id = $1",
        cve_id,
    )
    if not cve:
        logger.warning(f"CVE not found in cve_entries: {cve_id}")
        return None

    severity = cve["severity"] or "unknown"
    description = cve["description"] or ""
    cvss_score = cve["cvss_score"]

    # Build runbook metadata
    category = _CVE_CATEGORY_MAP.get(severity, "patching")
    hipaa_controls = _SEVERITY_HIPAA_MAP.get(severity, [])
    steps = _build_runbook_steps(description, severity)

    name = f"CVE Remediation: {cve_id}"
    desc_text = description[:500] if description else f"Auto-generated remediation for {cve_id}"
    if cvss_score:
        desc_text = f"[CVSS {cvss_score}] {desc_text}"

    # Determine check_type from CVE category
    check_type = "patching" if category == "patching" else "security"

    # Determine is_disruptive based on severity
    is_disruptive = severity in ("critical", "high")

    try:
        await conn.execute("""
            INSERT INTO runbooks (
                runbook_id, name, description, category, check_type,
                severity, is_disruptive, hipaa_controls, steps
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            ON CONFLICT (runbook_id) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                updated_at = NOW()
        """,
            runbook_id,
            name,
            desc_text,
            category,
            check_type,
            severity,
            is_disruptive,
            hipaa_controls,
            json.dumps(steps),
        )

        logger.info(f"CVE runbook generated: {cve_id} -> {runbook_id} (severity={severity}, category={category})")
        return runbook_id

    except Exception as e:
        logger.error(f"Failed to generate CVE runbook {cve_id} ({runbook_id}): {e}")
        return None


async def auto_remediate_cve(conn, cve_id: str, site_id: str, runbook_id: str) -> Dict:
    """Create an L1 rule for auto-remediation of a CVE at a site.

    DISABLED: L1 rule creation is paused because the daemon reports drift-typed
    incidents (e.g. 'patching', 'backup_not_configured'), not CVE-typed incidents
    (e.g. 'cve_cve_2025_49708'). The generated rules never match any real incident.
    Runbook generation still works — only the L1 rule step is skipped.

    TODO: Wire CVE→L1 when the daemon maps CVE matches to drift check types
    with cve_id in the incident details JSON.
    """
    return {
        "action": "skipped",
        "rule_id": None,
        "reason": "L1 rule creation paused — daemon does not report CVE-typed incidents yet. Runbook generated successfully.",
    }


async def process_new_cve_matches(conn) -> Dict:
    """Process unprocessed CVE fleet matches: generate runbooks and auto-remediate.

    Called periodically by the background task loop.

    Args:
        conn: asyncpg connection

    Returns:
        Dict with stats: processed, runbooks_generated, auto_remediated, skipped, errors
    """
    stats = {
        "processed": 0,
        "runbooks_generated": 0,
        "auto_remediated": 0,
        "skipped": 0,
        "errors": 0,
    }

    # Find unprocessed matches (remediation_status IS NULL)
    # Skip inactive/deprecated sites to avoid clogging the queue
    matches = await conn.fetch("""
        SELECT fm.id, fm.cve_id, fm.site_id, fm.appliance_id,
               ce.cve_id AS cve_id_str, ce.severity
        FROM cve_fleet_matches fm
        JOIN cve_entries ce ON ce.id = fm.cve_id
        JOIN sites s ON s.site_id = fm.site_id AND s.status != 'inactive'
        WHERE fm.remediation_status IS NULL
        ORDER BY
            CASE ce.severity
                WHEN 'critical' THEN 0
                WHEN 'high' THEN 1
                WHEN 'medium' THEN 2
                WHEN 'low' THEN 3
                ELSE 4
            END,
            fm.created_at ASC
        LIMIT 500
    """)

    if not matches:
        logger.debug("CVE remediation: no unprocessed matches")
        return stats

    logger.info("CVE remediation processing", batch_size=len(matches))

    for match in matches:
        match_id = match["id"]
        cve_id_str = match["cve_id_str"]
        site_id = match["site_id"]
        severity = match["severity"]
        stats["processed"] += 1

        try:
            # Step 1: Generate runbook
            runbook_id = await generate_cve_runbook(conn, cve_id_str, str(match_id))

            if not runbook_id:
                await conn.execute("""
                    UPDATE cve_fleet_matches
                    SET remediation_status = 'failed',
                        remediation_attempted_at = NOW(),
                        updated_at = NOW()
                    WHERE id = $1
                """, match_id)
                stats["errors"] += 1
                continue

            stats["runbooks_generated"] += 1

            # Step 2: Auto-remediate if eligible
            remediation_result = await auto_remediate_cve(
                conn, cve_id_str, site_id, runbook_id
            )

            if remediation_result["action"] == "created":
                remediation_status = "auto_remediated"
                stats["auto_remediated"] += 1
            elif remediation_result["action"] == "already_exists":
                remediation_status = "auto_remediated"
                # Don't double-count
            elif remediation_result["action"] == "skipped":
                remediation_status = "runbook_generated"
                stats["skipped"] += 1
            else:
                remediation_status = "failed"
                stats["errors"] += 1

            # Step 3: Update match record
            await conn.execute("""
                UPDATE cve_fleet_matches
                SET remediation_status = $1,
                    remediation_runbook_id = $2,
                    remediation_attempted_at = NOW(),
                    updated_at = NOW()
                WHERE id = $3
            """, remediation_status, runbook_id, match_id)

        except Exception as e:
            logger.error(f"Error processing CVE fleet match {match_id} ({cve_id_str}): {e}")
            stats["errors"] += 1
            try:
                await conn.execute("""
                    UPDATE cve_fleet_matches
                    SET remediation_status = 'failed',
                        remediation_attempted_at = NOW(),
                        updated_at = NOW()
                    WHERE id = $1
                """, match_id)
            except Exception:
                pass  # Best effort status update

    logger.info(
        f"CVE remediation processing complete: "
        f"processed={stats['processed']}, runbooks={stats['runbooks_generated']}, "
        f"auto_remediated={stats['auto_remediated']}, skipped={stats['skipped']}, "
        f"errors={stats['errors']}"
    )
    return stats


# =============================================================================
# Background Task Loop
# =============================================================================

async def cve_remediation_loop():
    """Periodic background task: process new CVE fleet matches.

    Runs every 30 minutes. Called via asyncio.create_task() from main.py
    lifespan startup.
    """
    import asyncio
    await asyncio.sleep(180)  # Wait 3 min after startup (after CVE sync settles)

    while True:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                stats = await process_new_cve_matches(conn)
                if stats["processed"] > 0:
                    logger.info("CVE remediation loop completed",
                                processed=stats["processed"],
                                runbooks=stats["runbooks_generated"],
                                auto_remediated=stats["auto_remediated"],
                                skipped=stats["skipped"],
                                errors=stats["errors"])
        except Exception as e:
            logger.error("CVE remediation loop error", error=str(e))

        # Drain backlog faster (5 min), then steady-state (30 min)
        interval = 300 if stats.get("processed", 0) > 0 else 1800
        await asyncio.sleep(interval)


# =============================================================================
# API Helpers (called from routes.py endpoints)
# =============================================================================

async def get_cve_remediation_status(cve_id: str) -> Dict:
    """Get remediation status for a CVE across the fleet.

    Args:
        cve_id: The CVE identifier string

    Returns:
        Dict with status breakdown and runbook info.
    """
    pool = await get_pool()

    cve = await pool.fetchrow(
        "SELECT id, cve_id, severity, cvss_score FROM cve_entries WHERE cve_id = $1",
        cve_id,
    )
    if not cve:
        return None

    matches = await pool.fetch("""
        SELECT fm.id, fm.site_id, fm.appliance_id,
               fm.remediation_status, fm.remediation_runbook_id,
               fm.remediation_attempted_at, fm.status
        FROM cve_fleet_matches fm
        WHERE fm.cve_id = $1
        ORDER BY fm.remediation_status NULLS FIRST
    """, cve["id"])

    status_counts = {}
    for m in matches:
        rs = m["remediation_status"] or "pending"
        status_counts[rs] = status_counts.get(rs, 0) + 1

    runbook_id = _make_runbook_id(cve_id)
    runbook_exists = await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM runbooks WHERE runbook_id = $1)", runbook_id
    )

    return {
        "cve_id": cve_id,
        "severity": cve["severity"],
        "cvss_score": float(cve["cvss_score"]) if cve["cvss_score"] else None,
        "total_matches": len(matches),
        "remediation_status_counts": status_counts,
        "runbook_id": runbook_id if runbook_exists else None,
        "runbook_exists": runbook_exists,
        "matches": [
            {
                "id": str(m["id"]),
                "site_id": m["site_id"],
                "appliance_id": m["appliance_id"],
                "cve_status": m["status"],
                "remediation_status": m["remediation_status"] or "pending",
                "remediation_runbook_id": m["remediation_runbook_id"],
                "remediation_attempted_at": (
                    m["remediation_attempted_at"].isoformat()
                    if m["remediation_attempted_at"] else None
                ),
            }
            for m in matches
        ],
    }


async def trigger_cve_runbook_generation(cve_id: str) -> Dict:
    """Manually trigger runbook generation for a specific CVE.

    Called from the admin API endpoint.

    Args:
        cve_id: The CVE identifier string

    Returns:
        Dict with result info.
    """
    pool = await get_pool()

    cve = await pool.fetchrow(
        "SELECT id, cve_id, severity FROM cve_entries WHERE cve_id = $1",
        cve_id,
    )
    if not cve:
        return {"error": f"CVE {cve_id} not found", "status": "not_found"}

    # Get fleet matches for this CVE
    matches = await pool.fetch("""
        SELECT id, site_id, appliance_id, remediation_status
        FROM cve_fleet_matches
        WHERE cve_id = $1
    """, cve["id"])

    if not matches:
        return {
            "status": "no_matches",
            "cve_id": cve_id,
            "message": "No fleet matches found for this CVE",
        }

    async with pool.acquire() as conn:
        # Generate the runbook (idempotent)
        runbook_id = await generate_cve_runbook(
            conn, cve_id, str(matches[0]["id"])
        )

        if not runbook_id:
            return {
                "status": "failed",
                "cve_id": cve_id,
                "message": "Failed to generate runbook",
            }

        # Process auto-remediation for each match
        results = []
        for match in matches:
            result = await auto_remediate_cve(
                conn, cve_id, match["site_id"], runbook_id
            )
            results.append({
                "site_id": match["site_id"],
                "appliance_id": match["appliance_id"],
                **result,
            })

            # Update match status
            if result["action"] in ("created", "already_exists"):
                remediation_status = "auto_remediated"
            elif result["action"] == "skipped":
                remediation_status = "runbook_generated"
            else:
                remediation_status = "failed"

            await conn.execute("""
                UPDATE cve_fleet_matches
                SET remediation_status = $1,
                    remediation_runbook_id = $2,
                    remediation_attempted_at = NOW(),
                    updated_at = NOW()
                WHERE id = $3
            """, remediation_status, runbook_id, match["id"])

    auto_count = sum(1 for r in results if r["action"] in ("created", "already_exists"))
    skip_count = sum(1 for r in results if r["action"] == "skipped")

    return {
        "status": "success",
        "cve_id": cve_id,
        "runbook_id": runbook_id,
        "total_matches": len(matches),
        "auto_remediated": auto_count,
        "skipped": skip_count,
        "details": results,
    }
