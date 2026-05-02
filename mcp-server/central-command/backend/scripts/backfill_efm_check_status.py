#!/usr/bin/env python3
"""Backfill `evidence_framework_mappings.check_status` from existing
compliance_bundles JSONB.

Background (D1 fix 2026-05-02): migration 271 added the
`check_status` column to evidence_framework_mappings + rewrote
`calculate_compliance_score` to use per-control granularity. The
function ignores rows where `check_status IS NULL` (pre-backfill).
This script populates check_status for historical mappings (~117K rows)
so scores reflect the FULL 30-day window, not just bundles received
post-deploy.

Approach (per design doc + coach #6):
  - Iterate compliance_bundles in chunks of 1000 (created_at DESC —
    newest first so the 30-day window populates fastest)
  - For each bundle:
      a. JOIN existing evidence_framework_mappings WHERE bundle_id=$1
         to find pairs ALREADY mapped — DO NOT discover new pairs
         from current YAML (the YAML may have changed since ingest)
      b. Parse bundle.checks JSONB; group per-host status by check_type
      c. For each existing (framework, control_id), aggregate per-control
         status from CURRENT YAML's check_type → control mapping +
         the bundle's per-host statuses
      d. UPDATE evidence_framework_mappings SET check_status=? WHERE
         id=? AND check_status IS NULL  (idempotent — populated rows skipped)

YAML drift mitigation: log a structured warning when a check_type in
the bundle has no current YAML mapping for a (framework, control_id)
pair that historically existed. Operator can use this to spot
crosswalk drift.

Race with writer (post-deploy): writer's ON CONFLICT DO UPDATE always
writes a value; backfill's `WHERE check_status IS NULL` skips
already-populated rows. Atomic at the row level — no double-write.

Usage:
    docker exec mcp-server bash -c 'cd /app && python3 -m \\
      dashboard_api.scripts.backfill_efm_check_status \\
      --apply --chunk-size 1000 --max-bundles 200000'

Idempotent. DRY-RUN by default.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from typing import Any


logger = logging.getLogger("backfill_efm_check_status")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


# Per-control aggregation taxonomy — MUST match the writer's _agg() in
# evidence_chain.py::map_evidence_to_frameworks AND mig 271 CHECK domain.
# Lockstep enforced by tests/test_per_control_lockstep.py.
PASSING = {"pass", "compliant", "warning"}
FAILING = {"fail", "non_compliant"}


def _agg(statuses: list[str]) -> str:
    if any(s in FAILING for s in statuses):
        return "fail"
    if any(s in PASSING for s in statuses):
        return "pass"
    return "unknown"


def _get_db_url() -> str:
    """Prefer MIGRATION_DATABASE_URL (mcp superuser, RLS bypass)."""
    url = os.environ.get("MIGRATION_DATABASE_URL")
    if url:
        return url
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("MIGRATION_DATABASE_URL or DATABASE_URL must be set")
    print("WARNING — using DATABASE_URL; RLS may filter compliance_bundles.",
          file=sys.stderr)
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


async def _process_bundle(
    conn,
    bundle_row: dict,
    get_controls,
    apply: bool,
) -> tuple[int, int, list[str]]:
    """Returns (rows_updated, rows_skipped_already_populated, drift_warnings)."""
    bundle_id = bundle_row["bundle_id"]
    site_id = bundle_row["site_id"]
    checks_json = bundle_row.get("check_result") or {}
    if isinstance(checks_json, str):
        try:
            checks_json = json.loads(checks_json)
        except json.JSONDecodeError:
            return (0, 0, [f"bundle_id={bundle_id}: invalid checks JSON"])

    checks = (
        checks_json.get("checks")
        if isinstance(checks_json, dict)
        else None
    )
    if not isinstance(checks, list):
        return (0, 0, [])

    # Get appliance_framework_configs for this site to know enabled frameworks.
    enabled_row = await conn.fetchrow(
        "SELECT enabled_frameworks FROM appliance_framework_configs WHERE site_id = $1",
        site_id,
    )
    if not enabled_row:
        enabled_row = await conn.fetchrow(
            "SELECT enabled_frameworks FROM appliance_framework_configs WHERE appliance_id LIKE $1",
            f"{site_id}%",
        )
    if not enabled_row or not enabled_row["enabled_frameworks"]:
        enabled = ["hipaa"]
    else:
        enabled = list(enabled_row["enabled_frameworks"])

    # Build per-control statuses from bundle JSONB
    control_to_statuses: dict[tuple[str, str], list[str]] = {}
    for check in checks:
        if not isinstance(check, dict):
            continue
        check_type = check.get("check") or check.get("check_type")
        status = check.get("status")
        if not check_type or not status:
            continue
        controls = get_controls(check_type, enabled)
        for ctrl in controls:
            key = (ctrl["framework"], ctrl["control_id"])
            control_to_statuses.setdefault(key, []).append(status)

    # Find existing mapping rows for this bundle — coach #6: do NOT
    # expand the mapping set, only update existing rows
    existing_rows = await conn.fetch(
        """
        SELECT id, framework, control_id
        FROM evidence_framework_mappings
        WHERE bundle_id = $1 AND check_status IS NULL
        """,
        bundle_id,
    )
    if not existing_rows:
        return (0, 0, [])

    rows_updated = 0
    drift_warnings: list[str] = []
    for row in existing_rows:
        key = (row["framework"], row["control_id"])
        if key not in control_to_statuses:
            # YAML drift — historical mapping has no current crosswalk.
            # Log but do not invent a status.
            drift_warnings.append(
                f"bundle_id={bundle_id} framework={row['framework']} "
                f"control_id={row['control_id']}: no current YAML mapping"
            )
            continue
        agg = _agg(control_to_statuses[key])
        if apply:
            await conn.execute(
                "UPDATE evidence_framework_mappings SET check_status = $1 "
                "WHERE id = $2 AND check_status IS NULL",
                agg, row["id"],
            )
        rows_updated += 1
    return (rows_updated, 0, drift_warnings)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--max-bundles", type=int, default=200_000,
                        help="Safety cap on total bundles processed.")
    parser.add_argument("--sleep-ms", type=int, default=200,
                        help="Sleep between chunks to reduce DB load.")
    parser.add_argument("--apply", action="store_true",
                        help="Without this flag, runs in DRY-RUN mode.")
    args = parser.parse_args()

    import asyncpg

    sys.path.insert(0, "/app")
    from dashboard_api.framework_mapper import get_controls_for_check_with_hipaa_map

    conn = await asyncpg.connect(_get_db_url())
    await conn.execute("SET statement_timeout = 0")

    # Snapshot — how many rows even need backfill
    pending = await conn.fetchval(
        "SELECT COUNT(*) FROM evidence_framework_mappings WHERE check_status IS NULL"
    )
    logger.info(
        f"Backfill plan: {pending} mapping rows pending. "
        f"chunk_size={args.chunk_size} max_bundles={args.max_bundles} apply={args.apply}"
    )
    if pending == 0:
        await conn.close()
        logger.info("Nothing to backfill.")
        return 0

    last_ts = None
    total_processed_bundles = 0
    total_rows_updated = 0
    total_drift_warnings = 0

    started = time.time()
    while total_processed_bundles < args.max_bundles:
        if last_ts is None:
            chunk = await conn.fetch(
                "SELECT bundle_id, site_id, check_result, created_at "
                "FROM compliance_bundles "
                "WHERE bundle_id IN (SELECT DISTINCT bundle_id "
                "                    FROM evidence_framework_mappings "
                "                    WHERE check_status IS NULL) "
                "ORDER BY created_at DESC LIMIT $1",
                args.chunk_size,
            )
        else:
            chunk = await conn.fetch(
                "SELECT bundle_id, site_id, check_result, created_at "
                "FROM compliance_bundles "
                "WHERE created_at < $1 AND bundle_id IN ("
                "    SELECT DISTINCT bundle_id FROM evidence_framework_mappings "
                "    WHERE check_status IS NULL) "
                "ORDER BY created_at DESC LIMIT $2",
                last_ts, args.chunk_size,
            )
        if not chunk:
            break

        for bundle_row in chunk:
            updated, _skipped, drift = await _process_bundle(
                conn, dict(bundle_row), get_controls_for_check_with_hipaa_map,
                apply=args.apply,
            )
            total_rows_updated += updated
            total_drift_warnings += len(drift)
            for d in drift[:3]:
                logger.warning(d)
            total_processed_bundles += 1

        last_ts = chunk[-1]["created_at"]
        elapsed = time.time() - started
        logger.info(
            f"progress bundles={total_processed_bundles} "
            f"rows_updated={total_rows_updated} "
            f"drift_warnings={total_drift_warnings} elapsed_s={elapsed:.1f}"
        )
        await asyncio.sleep(args.sleep_ms / 1000.0)

    elapsed = time.time() - started
    logger.info(
        f"DONE bundles={total_processed_bundles} "
        f"rows_updated={total_rows_updated} "
        f"drift_warnings={total_drift_warnings} elapsed_s={elapsed:.1f} "
        f"apply={args.apply}"
    )
    await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
