"""One-shot: backfill ledger events for promoted_rules that have ZERO
rows in promoted_rule_events (the Session 206 spine).

The 2026-04-18 audit found 17/43 promoted_rules with no ledger history.
Root cause: three-list lockstep drift (Python EVENT_TYPES did not match
the DB CHECK), so every safe_rollout_promoted_rule advance_lifecycle
call raised CheckViolationError, which was downgraded to a logger.warning
by the silent-swallow in that function. The fleet_order landed on the
appliance but the ledger was silent.

Migration 236 + the three-list lockstep CI test fix that going forward.
This script cleans up the back-inventory by synthesizing retroactive
ledger events from the fields we can still trust (promoted_at,
deployment_count, last_deployed_at).

Usage (from VPS):
    docker exec -w /app/dashboard_api mcp-server \\
        python3 -m scripts.backfill_flywheel_orphan_ledger

Idempotent: skips rules that already have ledger rows.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys


logger = logging.getLogger(__name__)


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Prefer the direct pool — no RLS tenant scoping needed for this
    # one-shot, and we want admin privilege to bypass any defensive
    # triggers that may be on the path.
    from fleet import get_pool
    from tenant_middleware import admin_connection
    from flywheel_state import backfill_lifecycle_events

    pool = await get_pool()
    async with admin_connection(pool) as conn:
        # Pre-count so the operator can sanity-check the expected work
        pre = await conn.fetchval(
            """
            SELECT COUNT(*)
              FROM promoted_rules pr
             WHERE NOT EXISTS (
                   SELECT 1 FROM promoted_rule_events e
                    WHERE e.rule_id = pr.rule_id
               )
            """
        )
        logger.info(
            "pre_backfill",
            extra={"orphan_promoted_rules": int(pre or 0)},
        )
        if pre == 0:
            logger.info("no orphans — nothing to do")
            return 0

        written = await backfill_lifecycle_events(conn)
        logger.info(
            "backfill_complete",
            extra={"rules_written": written},
        )

        # Post-verify: every promoted_rule should now have at least one
        # ledger event. Any remainder = backfill_lifecycle_events raised
        # for that row and logged at ERROR.
        post = await conn.fetchval(
            """
            SELECT COUNT(*)
              FROM promoted_rules pr
             WHERE NOT EXISTS (
                   SELECT 1 FROM promoted_rule_events e
                    WHERE e.rule_id = pr.rule_id
               )
            """
        )
        logger.info(
            "post_backfill",
            extra={"remaining_orphans": int(post or 0)},
        )
        return int(post or 0)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
