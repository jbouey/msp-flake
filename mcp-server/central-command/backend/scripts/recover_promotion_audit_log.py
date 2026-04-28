#!/usr/bin/env python3
"""Recovery script for promotion_audit_log_recovery dead-letter queue.

Migration 253 + Session 212 round-table P0. When the savepoint around
the `promotion_audit_log` INSERT in flywheel_promote.promote_candidate
fires (partition missing, CHECK violation, etc.), the audit payload
is dead-lettered into `promotion_audit_log_recovery`. This script
retries each unrecovered row by INSERTing into `promotion_audit_log`,
then atomically flips the recovery row's `recovered=true`.

Idempotent. Re-running is safe — already-recovered rows are skipped
by the `WHERE recovered = FALSE` filter.

Usage:
    python3 scripts/recover_promotion_audit_log.py --dry-run
    python3 scripts/recover_promotion_audit_log.py --apply

The substrate sev1 invariant `promotion_audit_log_recovery_pending`
clears within 60s of a successful run.

DO NOT manually flip `recovered=true` without a successful INSERT —
that creates a phantom recovery and breaks HIPAA §164.312(b) chain
of custody.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List

import asyncpg


def _get_db_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("MIGRATION_DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL or MIGRATION_DATABASE_URL must be set")
    # asyncpg wants the postgres:// scheme, not postgresql+asyncpg://
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


async def _fetch_unrecovered(conn: asyncpg.Connection) -> List[asyncpg.Record]:
    """Pull every queued row that hasn't been recovered yet, oldest first.
    Excludes rows where the recovery has already been attempted and
    flagged with `recovered_audit_log_id` (defensive — the WHERE clause
    is the primary gate)."""
    return await conn.fetch(
        """
        SELECT id, queued_at, event_type, rule_id, pattern_signature,
               check_type, site_id, confidence_score, success_rate,
               l2_resolutions, total_occurrences, source, actor, metadata,
               failure_reason, failure_class
          FROM promotion_audit_log_recovery
         WHERE recovered = FALSE
         ORDER BY queued_at
        """
    )


async def _retry_row(
    conn: asyncpg.Connection,
    row: asyncpg.Record,
    actor: str,
    *,
    apply: bool,
) -> Dict[str, Any]:
    """Attempt to INSERT this row into `promotion_audit_log` then flip
    the recovery flag. Both operations live in ONE transaction so a
    partial-success state is impossible.

    Returns a dict describing the outcome — caller logs / counts."""
    if not apply:
        return {
            "id": row["id"],
            "rule_id": row["rule_id"],
            "queued_at": row["queued_at"].isoformat(),
            "would_retry": True,
            "failure_class": row["failure_class"],
        }

    metadata_json = (
        json.dumps(row["metadata"])
        if isinstance(row["metadata"], dict)
        else row["metadata"]
    )

    try:
        async with conn.transaction():
            audit_id = await conn.fetchval(
                """
                INSERT INTO promotion_audit_log (
                    event_type, rule_id, pattern_signature, check_type,
                    site_id, confidence_score, success_rate,
                    l2_resolutions, total_occurrences, source, actor, metadata
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb
                )
                RETURNING id
                """,
                row["event_type"],
                row["rule_id"],
                row["pattern_signature"],
                row["check_type"],
                row["site_id"],
                row["confidence_score"],
                row["success_rate"],
                row["l2_resolutions"],
                row["total_occurrences"],
                row["source"],
                row["actor"],
                metadata_json,
            )
            await conn.execute(
                """
                UPDATE promotion_audit_log_recovery
                   SET recovered = TRUE,
                       recovered_at = NOW(),
                       recovered_by = $1,
                       recovery_audit_log_id = $2
                 WHERE id = $3
                """,
                actor,
                audit_id,
                row["id"],
            )
        return {
            "id": row["id"],
            "rule_id": row["rule_id"],
            "audit_log_id": audit_id,
            "recovered": True,
        }
    except Exception as e:
        return {
            "id": row["id"],
            "rule_id": row["rule_id"],
            "recovered": False,
            "error_class": type(e).__name__,
            "error": str(e)[:500],
        }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually attempt the recovery INSERTs. Without this, "
             "lists what WOULD be retried (dry-run is the default).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Explicit dry-run flag (default behavior; included for clarity).",
    )
    parser.add_argument(
        "--actor", default=os.environ.get("USER") or "recovery-script",
        help="Recorded in recovered_by; defaults to $USER.",
    )
    args = parser.parse_args()

    if args.apply and args.dry_run:
        sys.exit("Pick one: --apply OR --dry-run.")
    apply = args.apply  # default = dry-run

    conn = await asyncpg.connect(_get_db_url())
    try:
        await conn.execute("SET app.is_admin TO 'true'")
        rows = await _fetch_unrecovered(conn)
        if not rows:
            print("No unrecovered rows. Substrate invariant should be clear.")
            return 0

        print(f"{'DRY-RUN' if not apply else 'APPLY'}: {len(rows)} unrecovered rows")
        successes = 0
        failures: List[Dict[str, Any]] = []
        for row in rows:
            result = await _retry_row(conn, row, args.actor, apply=apply)
            if apply and result.get("recovered"):
                successes += 1
                print(f"  ✓ id={result['id']} rule_id={result['rule_id']} "
                      f"audit_log_id={result['audit_log_id']}")
            elif apply:
                failures.append(result)
                print(f"  ✗ id={result['id']} rule_id={result['rule_id']} "
                      f"error={result['error_class']}: {result['error']}")
            else:
                print(f"  · id={result['id']} rule_id={result['rule_id']} "
                      f"queued_at={result['queued_at']} "
                      f"failure_class={result['failure_class']}")

        if apply:
            print(f"\n{successes}/{len(rows)} recovered; {len(failures)} still failing.")
            return 0 if not failures else 1
        else:
            print(f"\n{len(rows)} would-be retried. Re-run with --apply.")
            return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
