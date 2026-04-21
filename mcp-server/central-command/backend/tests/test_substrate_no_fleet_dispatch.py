"""Non-operator posture guardrail: no substrate handler writes to fleet_orders.

If this test fails, the substrate panel has crossed a BAA line — the action
is no longer a single-row, internal-substrate-only change, it's a fleet
dispatch that touches customer infrastructure. Treat a failure here as a
P0 and revert the offending handler.

See: docs/superpowers/specs/2026-04-19-substrate-operator-controls-design.md
Section 2 (non-operator posture) and Section 12 (posture audit table).

Gated by TEST_DATABASE_URL because handlers run real DB queries against
seeded rows. The guardrail is parametrized across every action_key in the
registry so adding a new handler automatically extends coverage.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Add backend directory to sys.path so backend modules are importable.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from substrate_actions import SUBSTRATE_ACTIONS

_TEST_DB_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DB_URL,
    reason="non-operator posture guardrail tests require TEST_DATABASE_URL",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("action_key", sorted(SUBSTRATE_ACTIONS.keys()))
async def test_handler_does_not_insert_fleet_order(
    pool, action_key, happy_path_target_ref_for,
):
    """Every registered substrate handler MUST leave fleet_orders row-count unchanged.

    The handler is invoked against a seeded happy-path target_ref. Handler
    exceptions are swallowed here (separate tests cover error paths) — this
    test's sole job is asserting posture, not success.
    """
    from tenant_middleware import admin_connection

    target_ref = happy_path_target_ref_for(action_key)
    reason = "Posture guardrail test — confirming no fleet_orders INSERT"

    async with admin_connection(pool) as conn:
        before = await conn.fetchval("SELECT COUNT(*) FROM fleet_orders")
        async with conn.transaction():
            action = SUBSTRATE_ACTIONS[action_key]
            try:
                await action.handler(conn, target_ref, reason)
            except Exception:
                # Handler exceptions are fine — we only care about
                # fleet_orders row-count invariance. Error-path tests
                # live in test_substrate_actions_*.py.
                pass
        after = await conn.fetchval("SELECT COUNT(*) FROM fleet_orders")

    assert after == before, (
        f"action {action_key!r} inserted into fleet_orders "
        f"({before} → {after}) — this violates non-operator posture. "
        "See docs/superpowers/specs/2026-04-19-substrate-operator-controls-design.md Section 2."
    )
