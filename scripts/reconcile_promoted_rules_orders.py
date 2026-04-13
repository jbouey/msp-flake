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
import json
import os
import sys
from typing import List, Tuple

import asyncpg


# Paths inside the container — script is invoked from `docker exec`.
sys.path.insert(0, "/app")
sys.path.insert(0, "/app/dashboard_api")


def _ensure_signing_key_loaded() -> None:
    """The FastAPI lifespan is what normally populates main.signing_key.
    One-off scripts don't run the lifespan, so signing_key is None and
    sign_data() raises (previously: silently returned a bogus placeholder
    that broke Ed25519 verification on the appliance). Load it manually."""
    import main  # noqa: E402
    if getattr(main, "signing_key", None) is not None:
        return
    main.load_or_create_signing_key()
    if main.signing_key is None:
        raise RuntimeError(
            "signing_key still None after load_or_create_signing_key(); "
            "check SIGNING_KEY_FILE env var and file permissions"
        )
    print(f"Loaded signing_key, pubkey[:16]={main.get_public_key_hex()[:16]}")


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


# Hard ceiling — refuses to issue more than this many orders in a
# single run regardless of --limit. Protects against a runaway scan
# (eg. mass MAC re-provisioning event surfacing dormant rules) from
# overloading the appliance check-in delivery pipeline.
MAX_ORDERS_PER_RUN = 25


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually issue orders. Default is dry-run.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help=f"Cap orders issued in one run. Hard ceiling: {MAX_ORDERS_PER_RUN}.",
    )
    parser.add_argument(
        "--actor-email", type=str, default=None,
        help="Required with --apply: human email accountable for this run "
             "(written to admin_audit_log for chain-of-custody).",
    )
    parser.add_argument(
        "--reason", type=str, default=None,
        help="Required with --apply: free-text reason ≥ 20 chars "
             "describing why this backfill is being run.",
    )
    args = parser.parse_args()

    raw_url = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    url = os.getenv("MIGRATION_DATABASE_URL", raw_url)
    if not url:
        print("ERROR: DATABASE_URL or MIGRATION_DATABASE_URL must be set", file=sys.stderr)
        sys.exit(2)

    if args.apply:
        # P0 audit gate: every --apply run is bound to a named human +
        # a reason, written to admin_audit_log (immutable, append-only).
        actor = (args.actor_email or "").strip()
        reason = (args.reason or "").strip()
        if not actor or "@" not in actor:
            sys.exit(
                "--apply requires --actor-email <you@yourdomain.com> "
                "(named human, no service accounts)"
            )
        if len(reason) < 20:
            sys.exit(
                "--apply requires --reason '<≥20 chars describing why>' "
                "for audit/HIPAA accountability"
            )
        _ensure_signing_key_loaded()

    conn = await asyncpg.connect(url)
    try:
        orphans = await find_orphan_promotions(conn)
        print(f"Found {len(orphans)} orphan promotions (no sync_promoted_rule order ever issued).")
        if not orphans:
            print("Nothing to do.")
            return

        # Apply --limit, then enforce hard ceiling.
        original_count = len(orphans)
        effective_limit = args.limit if args.limit else MAX_ORDERS_PER_RUN
        effective_limit = min(effective_limit, MAX_ORDERS_PER_RUN)
        if len(orphans) > effective_limit:
            orphans = orphans[:effective_limit]
            print(
                f"--limit {effective_limit} (hard ceiling {MAX_ORDERS_PER_RUN}) → "
                f"processing first {len(orphans)} of {original_count}. Re-run "
                f"after this batch settles to continue."
            )

        if not args.apply:
            print()
            print("DRY RUN. Pass --apply --actor-email <e> --reason '<r>' to issue orders.")
            print()

        # P0 audit: write an admin_audit_log entry BEFORE we issue anything
        # so the audit trail exists even if the script crashes mid-run.
        audit_entry_id = None
        if args.apply:
            try:
                audit_entry_id = await conn.fetchval(
                    """
                    INSERT INTO admin_audit_log (username, action, target, details)
                    VALUES ($1, 'reconcile_promoted_rules_orders.start',
                            'fleet', $2::jsonb)
                    RETURNING id
                    """,
                    actor,
                    json.dumps({
                        "actor_email": actor,
                        "reason": reason,
                        "orphans_total": original_count,
                        "orphans_to_process": len(orphans),
                        "limit": args.limit,
                        "hard_ceiling": MAX_ORDERS_PER_RUN,
                    }),
                )
                print(f"[AUDIT] Logged start as admin_audit_log id={audit_entry_id}")
            except Exception as e:
                # Audit log write must succeed — refuse to proceed without it.
                print(f"ERROR: could not write admin_audit_log entry: {e}", file=sys.stderr)
                sys.exit(3)

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

        if args.apply:
            try:
                await conn.execute(
                    """
                    INSERT INTO admin_audit_log (username, action, target, details)
                    VALUES ($1, 'reconcile_promoted_rules_orders.complete',
                            'fleet', $2::jsonb)
                    """,
                    actor,
                    json.dumps({
                        "actor_email": actor,
                        "reason": reason,
                        "ok": ok, "fail": fail,
                        "start_audit_id": audit_entry_id,
                    }),
                )
            except Exception as e:
                # Don't fail the run for the close-bracket audit write,
                # but flag loudly.
                print(f"WARNING: could not write completion audit row: {e}", file=sys.stderr)

        if not args.apply:
            print(f"Would have issued {ok} orders. Re-run with --apply to do it.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
