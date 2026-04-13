#!/usr/bin/env python3
"""Reconcile promoted_rules → fleet_orders (Phase 15 closing pass).

Round-table audit found 43 promoted_rules in production with
deployment_count=0. Two of the three promotion paths bypassed
issue_sync_promoted_rule_orders, so historical promotions never had
the rollout order issued. Migration 163 trigger therefore never fired.

This one-shot reconciliation:

  1. Find every promoted_rules row that's status='active' AND has its
     l1_rules row enabled=true AND has NEVER been the target of a
     sync_promoted_rule fleet_order.
  2. Skip rows whose site is decommissioned.
  3. Issue one sync_promoted_rule order per remaining row, scoped to
     the original site_id (NOT fleet-wide — that would broadcast a
     site-scoped rule to the whole fleet).
  4. Log every action so the audit log captures what we did.

Default is DRY-RUN. Pass --apply to actually issue orders.

Run on the VPS where the order_signing key + DB are reachable:

    docker exec mcp-server python3 /app/scripts/reconcile_promoted_rules_orders.py
    docker exec mcp-server python3 /app/scripts/reconcile_promoted_rules_orders.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import List, Tuple

import asyncpg


# Paths inside the container — script is invoked from `docker exec`.
sys.path.insert(0, "/app")
sys.path.insert(0, "/app/dashboard_api")


async def find_orphan_promotions(conn: asyncpg.Connection) -> List[Tuple[str, str, str, str]]:
    """Return (rule_id, site_id, runbook_id, rule_yaml) for every
    promoted_rules row that needs a sync_promoted_rule order issued.

    Filters:
      - promoted_rules.status = 'active'
      - linked l1_rules.enabled = true (don't re-deploy disabled rules)
      - site is not decommissioned
      - site has at least one appliance that checked in within the
        last 7 days (skip zombie/pilot/test sites — their orders would
        sit unacked forever)
      - no sync_promoted_rule fleet_order with this rule_id has ever been created
    """
    rows = await conn.fetch("""
        SELECT pr.rule_id,
               pr.site_id,
               COALESCE(l.runbook_id, 'general') AS runbook_id,
               COALESCE(pr.rule_yaml, 'id: ' || pr.rule_id) AS rule_yaml
        FROM promoted_rules pr
        LEFT JOIN l1_rules l ON l.rule_id = pr.rule_id
        LEFT JOIN sites s ON s.site_id = pr.site_id
        WHERE pr.status = 'active'
          AND COALESCE(l.enabled, true) = true
          AND COALESCE(s.status, 'active') != 'decommissioned'
          AND EXISTS (
              SELECT 1 FROM site_appliances sa
              WHERE sa.site_id = pr.site_id
                AND sa.last_checkin > NOW() - INTERVAL '7 days'
          )
          AND NOT EXISTS (
              SELECT 1 FROM fleet_orders fo
              WHERE fo.order_type = 'sync_promoted_rule'
                AND fo.parameters->>'rule_id' = pr.rule_id
                AND fo.parameters->>'site_id' = pr.site_id
          )
        ORDER BY pr.promoted_at ASC
    """)
    return [(r["rule_id"], r["site_id"], r["runbook_id"], r["rule_yaml"]) for r in rows]


async def issue_one(conn: asyncpg.Connection, rule_id: str, site_id: str,
                    runbook_id: str, rule_yaml: str, apply: bool) -> bool:
    """Issue (or pretend to issue) a single rollout order. Returns
    True on success."""
    if not apply:
        print(f"  [DRY] would issue: rule_id={rule_id} site_id={site_id} runbook={runbook_id}")
        return True
    try:
        from dashboard_api.flywheel_promote import issue_sync_promoted_rule_orders
    except ImportError:
        from flywheel_promote import issue_sync_promoted_rule_orders
    try:
        n = await issue_sync_promoted_rule_orders(
            conn,
            rule_id=rule_id,
            runbook_id=runbook_id,
            rule_yaml=rule_yaml,
            site_id=site_id,
            scope="site",
        )
        print(f"  [APPLIED] rule_id={rule_id} site_id={site_id} orders_created={n}")
        return n > 0
    except Exception as e:
        print(f"  [ERROR] rule_id={rule_id} site_id={site_id}: {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually issue orders. Default is dry-run.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Cap orders issued in one run (safety net for first apply).",
    )
    args = parser.parse_args()

    raw_url = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    url = os.getenv("MIGRATION_DATABASE_URL", raw_url)
    if not url:
        print("ERROR: DATABASE_URL or MIGRATION_DATABASE_URL must be set", file=sys.stderr)
        sys.exit(2)

    conn = await asyncpg.connect(url)
    try:
        orphans = await find_orphan_promotions(conn)
        print(f"Found {len(orphans)} orphan promotions (no sync_promoted_rule order ever issued).")
        if not orphans:
            print("Nothing to do.")
            return

        if args.limit:
            orphans = orphans[: args.limit]
            print(f"--limit {args.limit} → processing first {len(orphans)} only.")

        if not args.apply:
            print()
            print("DRY RUN. Pass --apply to actually issue orders.")
            print()

        ok = 0
        fail = 0
        for rule_id, site_id, runbook_id, rule_yaml in orphans:
            success = await issue_one(conn, rule_id, site_id, runbook_id, rule_yaml, args.apply)
            if success:
                ok += 1
            else:
                fail += 1

        print()
        print(f"Summary: ok={ok}  fail={fail}  apply={args.apply}")
        if not args.apply:
            print(f"Would have issued {ok} orders. Re-run with --apply to do it.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
