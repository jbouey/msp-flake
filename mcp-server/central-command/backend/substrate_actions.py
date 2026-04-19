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

# Permissive by design — accepts 12-char unseparated MACs (from the Go daemon's
# normalize_mac_for_ring() output) and 17-char colon/dash separated. Format
# precision is NOT the purpose here; the authoritative check is the DB lookup
# (TargetNotFound if no row matches). Do not tighten without verifying every
# caller still passes.
MAC_PATTERN = re.compile(r"^[0-9A-Fa-f:\-]{12,17}$")


class SubstrateActionError(Exception):
    """Base class for substrate action errors."""


class TargetRefInvalid(SubstrateActionError):
    """target_ref missing required keys or values fail validation."""


class TargetNotFound(SubstrateActionError):
    """target_ref was valid but the referenced row does not exist."""


# Consumed by unlock_platform_account (Task 4) and reconcile_fleet_order (Task 5).
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

    if stage:
        status = await conn.execute(
            "DELETE FROM install_sessions WHERE mac_address = $1 AND install_stage = $2",
            mac, stage,
        )
    else:
        status = await conn.execute(
            "DELETE FROM install_sessions WHERE mac_address = $1",
            mac,
        )

    if status == "DELETE 0":
        raise TargetNotFound(
            f"install_sessions row for mac={mac!r} was deleted by a concurrent request"
        )

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


ALLOWED_UNLOCK_TABLES = {"partners", "client_users"}


async def _handle_unlock_platform_account(
    conn: Connection, target_ref: dict, reason: str
) -> dict:
    """Reset failed_login_attempts and locked_until for a locked platform account.

    target_ref keys:
      table  — required; one of {"partners", "client_users"}
      email  — required; must contain '@'

    Raises:
      TargetRefInvalid    — table not in allowlist, email missing/malformed, or
                            email matched multiple rows (partners.contact_email is
                            not unique — ambiguous match refused).
      TargetNotFound      — no row matches the given email in the named table.
      TargetNotActionable — row exists but is not currently locked.
    """
    table = target_ref.get("table")
    email = target_ref.get("email")

    if table not in ALLOWED_UNLOCK_TABLES:
        raise TargetRefInvalid(f"table must be one of {sorted(ALLOWED_UNLOCK_TABLES)}")
    if not email or not isinstance(email, str) or "@" not in email:
        raise TargetRefInvalid("email required and must contain '@'")

    # Per-table static SQL — table already whitelisted above.
    if table == "partners":
        rows = await conn.fetch(
            "SELECT id, COALESCE(contact_email, oauth_email) AS email, "
            "failed_login_attempts, locked_until "
            "FROM partners WHERE contact_email = $1 OR oauth_email = $1",
            email,
        )
    else:  # client_users
        rows = await conn.fetch(
            "SELECT id, email, failed_login_attempts, locked_until "
            "FROM client_users WHERE email = $1",
            email,
        )

    if not rows:
        raise TargetNotFound(f"no {table} row for email={email!r}")
    if len(rows) > 1:
        # partners.contact_email is not unique; a multi-row match is ambiguous.
        raise TargetRefInvalid(
            f"email={email!r} matched {len(rows)} rows in {table} — refusing to "
            "unlock without a disambiguator"
        )

    row = rows[0]
    is_locked = (
        (row["failed_login_attempts"] or 0) >= 5
        or row["locked_until"] is not None
    )
    if not is_locked:
        raise TargetNotActionable(
            f"{table} row for email={email!r} is not currently locked"
        )

    if table == "partners":
        status = await conn.execute(
            "UPDATE partners SET failed_login_attempts = 0, locked_until = NULL "
            "WHERE id = $1 AND (failed_login_attempts >= 5 OR locked_until IS NOT NULL)",
            row["id"],
        )
    else:
        status = await conn.execute(
            "UPDATE client_users SET failed_login_attempts = 0, locked_until = NULL "
            "WHERE id = $1 AND (failed_login_attempts >= 5 OR locked_until IS NOT NULL)",
            row["id"],
        )

    if status == "UPDATE 0":
        raise TargetNotActionable(
            f"{table} row for email={email!r} was unlocked by a concurrent request"
        )

    logger.info(
        "substrate.unlock_platform_account",
        extra={
            "table": table,
            "account_id": str(row["id"]),
            "previous_failed_count": row["failed_login_attempts"] or 0,
        },
    )

    return {
        "table": table,
        "email": row["email"],
        "previous_failed_count": row["failed_login_attempts"] or 0,
        "previous_locked_until": (
            row["locked_until"].isoformat() if row["locked_until"] else None
        ),
    }


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
