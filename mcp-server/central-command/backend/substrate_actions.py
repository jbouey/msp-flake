"""Handler registry for POST /api/admin/substrate/action.

Each entry is a single-row, internal-substrate-only action. Nothing in this
module enqueues fleet orders or touches customer infrastructure.
Non-operator posture audit: docs/superpowers/specs/2026-04-19-substrate-operator-controls-design.md Section 12.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict

from asyncpg import Connection

logger = logging.getLogger(__name__)

HandlerFn = Callable[[Connection, dict, str], Awaitable[dict]]


@dataclass(frozen=True)
class SubstrateAction:
    """Metadata for a substrate-only operator action."""

    handler: HandlerFn
    required_reason_chars: int
    audit_action: str


async def _handle_cleanup_install_session(
    conn: Connection, target_ref: dict, reason: str
) -> dict:
    raise NotImplementedError("wired in Task 3")


async def _handle_unlock_platform_account(
    conn: Connection, target_ref: dict, reason: str
) -> dict:
    raise NotImplementedError("wired in Task 4")


async def _handle_reconcile_fleet_order(
    conn: Connection, target_ref: dict, reason: str
) -> dict:
    raise NotImplementedError("wired in Task 5")


SUBSTRATE_ACTIONS: Dict[str, SubstrateAction] = {
    "cleanup_install_session": SubstrateAction(
        handler=_handle_cleanup_install_session,
        required_reason_chars=0,
        audit_action="substrate.cleanup_install_session",
    ),
    "unlock_platform_account": SubstrateAction(
        handler=_handle_unlock_platform_account,
        required_reason_chars=20,
        audit_action="substrate.unlock_platform_account",
    ),
    "reconcile_fleet_order": SubstrateAction(
        handler=_handle_reconcile_fleet_order,
        required_reason_chars=20,
        audit_action="substrate.reconcile_fleet_order",
    ),
}
