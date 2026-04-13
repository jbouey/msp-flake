#!/usr/bin/env python3
"""Backfill promoted_rules.rule_yaml with daemon-valid YAML (round-table P1 #6).

Round-table audit Session 206: all 43 promoted_rules rows in prod had
stub YAML like:
    id: L1-AUTO-RANSOMWARE-INDICATOR
    name: ransomware_indicator
    action: execute_runbook
    runbook_id: RB-WIN-STG-002

That's rejected by the Go daemon on two counts (action whitelist +
missing conditions). The `issue_sync_promoted_rule_orders` path now
SYNTHESIZES a valid body at issue-time, so new orders work. But the
DB still holds the stub — future debugging / audit / replay against
promoted_rules.rule_yaml will be misleading.

This one-shot UPDATE rewrites rule_yaml in-place using the same
build_daemon_valid_rule_yaml synthesizer the order-issuer uses. The
change is purely cosmetic at the daemon level (the daemon never
reads promoted_rules directly — it only ever sees fleet_order
parameters), so there's no runtime risk.

Default is DRY-RUN. Pass --apply to actually write.

Audit: every --apply run writes admin_audit_log start+complete rows,
bound to --actor-email + --reason ≥ 20 chars.

Usage inside mcp-server container:
    docker exec mcp-server python3 /app/dashboard_api/backfill_promoted_rules_yaml.py
    docker exec mcp-server python3 /app/dashboard_api/backfill_promoted_rules_yaml.py \\
        --apply --actor-email you@your.org --reason "Backfill per round-table P1 #6"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import List, Tuple

import asyncpg


sys.path.insert(0, "/app")
sys.path.insert(0, "/app/dashboard_api")


async def find_backfill_candidates(conn: asyncpg.Connection) -> List[Tuple[str, str, str]]:
    """(rule_id, runbook_id, incident_type) for every promoted_rules
    row whose rule_yaml is missing a conditions: block OR has the
    legacy execute_runbook action."""
    rows = await conn.fetch("""
        SELECT pr.rule_id, l.runbook_id,
               l.incident_pattern->>'incident_type' AS incident_type,
               COALESCE(pr.rule_yaml, '') AS rule_yaml
        FROM promoted_rules pr
        JOIN l1_rules l ON l.rule_id = pr.rule_id
        WHERE (pr.rule_yaml IS NULL
               OR pr.rule_yaml NOT LIKE '%conditions:%'
               OR pr.rule_yaml LIKE '%execute_runbook%')
          AND l.incident_pattern->>'incident_type' IS NOT NULL
        ORDER BY pr.promoted_at ASC
    """)
    out = []
    for r in rows:
        if not r["incident_type"]:
            continue
        out.append((r["rule_id"], r["runbook_id"], r["incident_type"]))
    return out


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor-email", type=str, default=None)
    parser.add_argument("--reason", type=str, default=None)
    args = parser.parse_args()

    if args.apply:
        actor = (args.actor_email or "").strip()
        reason = (args.reason or "").strip()
        if not actor or "@" not in actor:
            sys.exit("--apply requires --actor-email <you@yourdomain.com>")
        if len(reason) < 20:
            sys.exit("--apply requires --reason '<≥20 chars>'")

    # Lazy import so dry-run doesn't pull in the signing stack
    try:
        from dashboard_api.flywheel_math import build_daemon_valid_rule_yaml
    except ImportError:
        from flywheel_math import build_daemon_valid_rule_yaml

    raw_url = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    url = os.getenv("MIGRATION_DATABASE_URL", raw_url)
    if not url:
        sys.exit("ERROR: DATABASE_URL or MIGRATION_DATABASE_URL must be set")

    conn = await asyncpg.connect(url)
    try:
        candidates = await find_backfill_candidates(conn)
        print(f"Found {len(candidates)} promoted_rules rows needing rule_yaml backfill.")
        if not candidates:
            print("Nothing to do.")
            return

        audit_id = None
        if args.apply:
            audit_id = await conn.fetchval(
                """
                INSERT INTO admin_audit_log (username, action, target, details)
                VALUES ($1, 'backfill_promoted_rules_yaml.start', 'fleet', $2::jsonb)
                RETURNING id
                """,
                actor,
                json.dumps({
                    "actor_email": actor,
                    "reason": reason,
                    "candidates": len(candidates),
                }),
            )
            print(f"[AUDIT] Logged start as admin_audit_log id={audit_id}")

        ok = 0
        fail = 0
        for rule_id, runbook_id, incident_type in candidates:
            try:
                new_yaml = build_daemon_valid_rule_yaml(
                    rule_id=rule_id,
                    runbook_id=runbook_id,
                    incident_type=incident_type,
                )
            except Exception as e:
                print(f"  [SKIP] rule_id={rule_id}: classifier failed — {e}")
                fail += 1
                continue

            if args.apply:
                try:
                    await conn.execute(
                        "UPDATE promoted_rules SET rule_yaml = $1 WHERE rule_id = $2",
                        new_yaml, rule_id,
                    )
                    print(f"  [APPLIED] rule_id={rule_id}")
                    ok += 1
                except Exception as e:
                    print(f"  [ERROR] rule_id={rule_id}: {e}")
                    fail += 1
            else:
                print(f"  [DRY] rule_id={rule_id} runbook={runbook_id} "
                      f"incident_type={incident_type}")
                ok += 1

        print()
        print(f"Summary: ok={ok}  skip/fail={fail}  apply={args.apply}")

        if args.apply:
            await conn.execute(
                """
                INSERT INTO admin_audit_log (username, action, target, details)
                VALUES ($1, 'backfill_promoted_rules_yaml.complete', 'fleet', $2::jsonb)
                """,
                actor,
                json.dumps({"ok": ok, "fail": fail, "start_audit_id": audit_id}),
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
