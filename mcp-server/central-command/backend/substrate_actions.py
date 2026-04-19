"""Handler registry for POST /api/admin/substrate/action.

Each entry is a single-row, internal-substrate-only action. Nothing in this
module enqueues fleet orders or touches customer infrastructure.
Non-operator posture audit: docs/superpowers/specs/2026-04-19-substrate-operator-controls-design.md Section 12.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict

from asyncpg import Connection

logger = logging.getLogger(__name__)

HandlerFn = Callable[[Connection, dict, str], Awaitable[dict]]

MAC_PATTERN = re.compile(r"^[0-9A-Fa-f:\-]{12,17}$")


class SubstrateActionError(Exception):
    """Base class for substrate action errors."""


class TargetRefInvalid(SubstrateActionError):
    """target_ref missing required keys or values fail validation."""


class TargetNotFound(SubstrateActionError):
    """target_ref was valid but the referenced row does not exist."""


class TargetNotActionable(SubstrateActionError):
    """Target exists but is not in a state where this action applies."""


@dataclass(frozen=True)
class SubstrateAction:
    """Metadata for a substrate-only operator action.

    Fields:
      handler: async function that executes the action.
      required_reason_chars: minimum reason length (0 = reason optional).
      audit_action: admin_audit_log.action string (format: "substrate.<key>").
    """

    handler: HandlerFn
    required_reason_chars: int
    audit_action: str


async def _handle_cleanup_install_session(
    conn: Connection, target_ref: dict, reason: str
) -> dict:
    """Delete one stale install_sessions row keyed by MAC address.

    target_ref keys (API-level):
      mac   — required; normalized MAC string, 12–17 hex chars + separators
      stage — optional; if provided, also filters by install_stage
    """
    mac = target_ref.get("mac")
    stage = target_ref.get("stage")

    if not mac or not MAC_PATTERN.match(mac):
        raise TargetRefInvalid("mac required and must match pattern [0-9A-Fa-f:\\-]{12,17}")

    if stage:
        row = await conn.fetchrow(
            "SELECT mac_address, install_stage, checkin_count, first_seen "
            "FROM install_sessions WHERE mac_address = $1 AND install_stage = $2",
            mac, stage,
        )
    else:
        row = await conn.fetchrow(
            "SELECT mac_address, install_stage, checkin_count, first_seen "
            "FROM install_sessions WHERE mac_address = $1",
            mac,
        )

    if row is None:
        raise TargetNotFound(f"no install_sessions row for mac={mac!r}")

    await conn.execute("DELETE FROM install_sessions WHERE mac_address = $1", mac)

    logger.info(
        "substrate.cleanup_install_session",
        extra={
            "mac": mac,
            "stage": row["install_stage"],
            "checkin_count": row["checkin_count"],
        },
    )

    return {
        "deleted": 1,
        "mac": mac,
        "stage": row["install_stage"],
        "checkin_count": row["checkin_count"],
        "first_seen": row["first_seen"].isoformat(),
    }


async def _handle_unlock_platform_account(
    conn: Connection, target_ref: dict, reason: str
) -> dict:
    raise NotImplementedError("_handle_unlock_platform_account: wired in Task 4")


async def _handle_reconcile_fleet_order(
    conn: Connection, target_ref: dict, reason: str
) -> dict:
    raise NotImplementedError("_handle_reconcile_fleet_order: wired in Task 5")


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
