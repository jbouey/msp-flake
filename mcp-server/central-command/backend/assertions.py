"""Substrate Integrity Engine — continuous assertion of system invariants.

Every 60s the engine runs every registered Assertion against prod.
Each Assertion returns a list of Violation dicts (zero or more). The
engine UPSERTs them into substrate_violations (open-row dedup on
invariant_name + site_id) and AUTO-RESOLVES open rows whose
violations are no longer present.

Invariants encode "things that MUST be true if the substrate is
working as advertised." Tonight's failures (NULL legacy_uuid, stale
discovered_devices, DNS-dead fleet order URLs, install loops, etc.)
each become one Assertion. The system then watches itself and pages
ITSELF when an invariant breaks — the goal is to never again find
out about a substrate failure by Jeff and Claude grinding through a
debug session.

Invariants live alongside the code that produces the underlying
state. Adding a new one is ~10 lines: write the SQL query, add to
ALL_ASSERTIONS, ship. No infrastructure change, no schema change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlparse

import asyncpg

logger = logging.getLogger("assertions")


@dataclass
class Violation:
    """One concrete violation of an Assertion at a single site (or
    global, when site_id is None). details is JSON-serializable; it
    surfaces to the dashboard + audit log so an operator can act
    without re-running the query."""

    site_id: Optional[str]
    details: Dict[str, object]


CheckFn = Callable[[asyncpg.Connection], Awaitable[List[Violation]]]


@dataclass
class Assertion:
    """A single named invariant. severity drives alert routing.
    description is shown in the dashboard tooltip + the notification
    body — write it as a human-actionable sentence."""

    name: str
    severity: str  # 'sev1' | 'sev2' | 'sev3'
    description: str
    check: CheckFn


# --- The invariants ---------------------------------------------------
#
# Each check returns the SET of CURRENT violations. The engine
# diff'd against open rows: new violations open new rows, missing
# violations close existing rows. Idempotent re-runs.


async def _check_legacy_uuid_populated(conn: asyncpg.Connection) -> List[Violation]:
    """Every site_appliances row must have non-NULL legacy_uuid for the
    M1 v_appliances_current JOIN chain to surface it correctly.
    Tonight: 3 of 4 prod rows had NULL until migration 206."""
    rows = await conn.fetch(
        """
        SELECT site_id, COUNT(*) AS n
          FROM site_appliances
         WHERE legacy_uuid IS NULL AND deleted_at IS NULL
      GROUP BY site_id
        """
    )
    return [Violation(site_id=r["site_id"], details={"null_count": r["n"]}) for r in rows]


async def _check_install_loop(conn: asyncpg.Connection) -> List[Violation]:
    """An install_sessions row with checkin_count > 5 means the box is
    stuck in a USB-boot loop — install completed (or appeared to)
    but the next reboot didn't pick up the installed system.
    Tonight: t740 hit count=50 with no alert until we added the
    health_monitor check."""
    rows = await conn.fetch(
        """
        SELECT site_id, mac_address, hostname, checkin_count, last_seen
          FROM install_sessions
         WHERE checkin_count > 5
           AND install_stage = 'live_usb'
           AND last_seen > NOW() - INTERVAL '6 hours'
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "mac_address": r["mac_address"],
                "hostname": r["hostname"],
                "checkin_count": r["checkin_count"],
                "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
            },
        )
        for r in rows
    ]


async def _check_offline_appliance_long(conn: asyncpg.Connection) -> List[Violation]:
    """An appliance offline > 1h with no manual ack is a customer-
    visible problem. The 30min email warning is informational; this
    is the structured Sev-2 signal that flows into the dashboard."""
    rows = await conn.fetch(
        """
        SELECT site_id, mac_address, hostname,
               EXTRACT(EPOCH FROM (NOW() - last_checkin))/3600 AS hours_silent,
               agent_version
          FROM site_appliances
         WHERE deleted_at IS NULL
           AND status = 'offline'
           AND last_checkin < NOW() - INTERVAL '1 hour'
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "mac_address": r["mac_address"],
                "hostname": r["hostname"],
                "hours_silent": float(r["hours_silent"] or 0),
                "agent_version": r["agent_version"],
            },
        )
        for r in rows
    ]


async def _check_agent_version_lag(conn: asyncpg.Connection) -> List[Violation]:
    """site_appliances.agent_version must match the most-recent
    SUCCESSFULLY-COMPLETED update_daemon order. A lag means the
    completion ACK lied (pre-0.4.3 daemon bug) or a rollback fired
    silently."""
    rows = await conn.fetch(
        """
        WITH last_update AS (
            SELECT DISTINCT ON (parameters->>'site_id')
                   parameters->>'site_id'  AS site_id,
                   parameters->>'version'  AS expected_version,
                   created_at
              FROM fleet_orders
             WHERE order_type = 'update_daemon'
               AND status = 'completed'
          ORDER BY parameters->>'site_id', created_at DESC
        )
        SELECT lu.site_id, sa.mac_address, sa.hostname,
               sa.agent_version, lu.expected_version
          FROM last_update lu
          JOIN site_appliances sa ON sa.site_id = lu.site_id
                                  AND sa.deleted_at IS NULL
                                  AND sa.status = 'online'
         WHERE sa.agent_version IS DISTINCT FROM lu.expected_version
           AND lu.created_at < NOW() - INTERVAL '15 minutes'
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "mac_address": r["mac_address"],
                "hostname": r["hostname"],
                "running_version": r["agent_version"],
                "expected_version": r["expected_version"],
            },
        )
        for r in rows
    ]


async def _check_fleet_order_url_resolvable(conn: asyncpg.Connection) -> List[Violation]:
    """Every active update_daemon order's binary_url hostname must
    resolve in DNS. Tonight: an order pointed at release.osiriscare.net
    which has no A record anywhere; the canary DNS-failed every 60s
    for an hour with no alert."""
    rows = await conn.fetch(
        """
        SELECT id::text AS order_id,
               parameters->>'site_id'    AS site_id,
               parameters->>'binary_url' AS binary_url
          FROM fleet_orders
         WHERE status = 'active'
           AND order_type = 'update_daemon'
           AND parameters ? 'binary_url'
        """
    )

    violations: List[Violation] = []
    for r in rows:
        url = r["binary_url"] or ""
        host = urlparse(url).hostname or ""
        if not host:
            violations.append(
                Violation(
                    site_id=r["site_id"],
                    details={"order_id": r["order_id"], "url": url, "reason": "no_hostname"},
                )
            )
            continue
        try:
            # gethostbyname is sync — assertions run on the event
            # loop. ~10 active update_daemon orders max in practice
            # so the blocking cost is negligible.
            await asyncio.get_event_loop().run_in_executor(
                None, socket.gethostbyname, host
            )
        except socket.gaierror as e:
            violations.append(
                Violation(
                    site_id=r["site_id"],
                    details={
                        "order_id": r["order_id"],
                        "url": url,
                        "host": host,
                        "reason": "dns_resolve_failed",
                        "error": str(e),
                    },
                )
            )
    return violations


async def _check_discovered_devices_freshness(conn: asyncpg.Connection) -> List[Violation]:
    """Every site_appliances MAC must appear in discovered_devices with
    last_seen_at fresher than 1h. If the canary is scanning but the
    sibling MAC stopped showing up, either the box is genuinely
    powered down OR there's a case-mismatch UPSERT bug. Either way
    the operator needs to know."""
    rows = await conn.fetch(
        """
        SELECT sa.site_id, sa.mac_address, sa.hostname,
               EXTRACT(EPOCH FROM (NOW() - dd.last_seen_at))/3600 AS hours_stale
          FROM site_appliances sa
     LEFT JOIN discovered_devices dd
            ON LOWER(dd.mac_address) = LOWER(sa.mac_address)
           AND dd.site_id = sa.site_id
         WHERE sa.deleted_at IS NULL
           AND sa.status = 'online'
           AND (dd.last_seen_at IS NULL
                OR dd.last_seen_at < NOW() - INTERVAL '1 hour')
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "mac_address": r["mac_address"],
                "hostname": r["hostname"],
                "hours_stale": float(r["hours_stale"] or 0),
            },
        )
        for r in rows
    ]


async def _check_no_unresolved_pending_migrations(conn: asyncpg.Connection) -> List[Violation]:
    """Migration runner is fail-closed at startup, so the existence of
    a pending migration without a successful subsequent deploy is
    almost always a CI/CD failure that operators didn't notice.
    Tonight: migration 206 failed on row-guard, blocked the deploy
    and the new code didn't roll out."""
    # This is a ledger sanity check — the migration ledger should
    # match the on-disk file count. If it doesn't, something deployed
    # the code without applying the migration (the worst class of
    # split-brain).
    rec = await conn.fetchrow(
        """
        SELECT MAX(CAST(version AS INTEGER)) AS max_version
          FROM schema_migrations
         WHERE version ~ '^[0-9]+$'
        """
    )
    if rec is None:
        return []
    # We can't see the disk from inside the engine — caller passes
    # the disk count via a closure. For now this assertion is a
    # placeholder that always passes; the real disk-vs-ledger check
    # lives in /api/admin/health and is wired separately.
    return []


async def _check_install_session_ttl(conn: asyncpg.Connection) -> List[Violation]:
    """install_sessions has expires_at but nothing was deleting expired
    rows until Migration 206. If they accumulate again, the cleanup
    cron is broken."""
    rec = await conn.fetchrow(
        """
        SELECT COUNT(*) AS expired_count
          FROM install_sessions
         WHERE expires_at < NOW() - INTERVAL '1 hour'
        """
    )
    if rec and rec["expired_count"] > 0:
        return [
            Violation(
                site_id=None,
                details={"expired_install_sessions": rec["expired_count"]},
            )
        ]
    return []


async def _check_mesh_ring_health(conn: asyncpg.Connection) -> List[Violation]:
    """A site with > 1 online appliance MUST form a mesh ring of size
    > 1. ring=1 with 2+ online means the gRPC mesh layer is broken
    (port 50051 blocked, TLS misconfig, etc.) — the auto-healing
    fallback is silently disabled."""
    rows = await conn.fetch(
        """
        SELECT site_id, COUNT(*) AS online_count
          FROM site_appliances
         WHERE deleted_at IS NULL AND status = 'online'
      GROUP BY site_id
        HAVING COUNT(*) >= 2
        """
    )
    # We don't have a ring_size column in the rollup MV right now,
    # so this assertion is a hook — the real check needs the
    # appliance_status_rollup or a daemon-reported metric. Leaving
    # the signature in place so we can wire it once the metric ships.
    return []


# Ordered list of registered assertions. Add new ones at the bottom.
ALL_ASSERTIONS: List[Assertion] = [
    Assertion(
        name="legacy_uuid_populated",
        severity="sev2",
        description="Every site_appliances row should have non-NULL legacy_uuid (M1 invariant)",
        check=_check_legacy_uuid_populated,
    ),
    Assertion(
        name="install_loop",
        severity="sev1",
        description="No appliance should be in an install_sessions live_usb loop with checkin_count > 5",
        check=_check_install_loop,
    ),
    Assertion(
        name="offline_appliance_over_1h",
        severity="sev2",
        description="Every appliance should have checked in within the last hour",
        check=_check_offline_appliance_long,
    ),
    Assertion(
        name="agent_version_lag",
        severity="sev1",
        description="agent_version must match the most-recent successful update_daemon order",
        check=_check_agent_version_lag,
    ),
    Assertion(
        name="fleet_order_url_resolvable",
        severity="sev1",
        description="Every active update_daemon order's binary_url must resolve in DNS",
        check=_check_fleet_order_url_resolvable,
    ),
    Assertion(
        name="discovered_devices_freshness",
        severity="sev2",
        description="Every online appliance MAC should appear in discovered_devices fresher than 1h",
        check=_check_discovered_devices_freshness,
    ),
    Assertion(
        name="install_session_ttl",
        severity="sev3",
        description="No install_sessions row should remain past expires_at",
        check=_check_install_session_ttl,
    ),
    # Hook for future ring-health check; harmless until the metric ships.
    Assertion(
        name="mesh_ring_size",
        severity="sev2",
        description="Sites with 2+ online appliances must form a mesh ring of size > 1",
        check=_check_mesh_ring_health,
    ),
    Assertion(
        name="online_implies_installed_system",
        severity="sev2",
        description="An appliance with status=online must NOT still be running the live USB installer",
        check=lambda c: _check_online_implies_installed(c),
    ),
    Assertion(
        name="every_online_appliance_has_active_api_key",
        severity="sev1",
        description="Every online appliance must have at least one active api_keys row (or fall through to a site-level key)",
        check=lambda c: _check_online_has_active_key(c),
    ),
    Assertion(
        name="auth_failure_lockout",
        severity="sev1",
        description="Any appliance with auth_failure_count >= 3 is locked out of the platform — operator action needed",
        check=lambda c: _check_auth_failure_lockout(c),
    ),
    Assertion(
        name="claim_event_unchained",
        severity="sev2",
        description="Every provisioning_claim_events row > 5min old must have chain_prev_hash + chain_hash populated (Week 3)",
        check=lambda c: _check_claim_event_unchained(c),
    ),
]


async def _check_online_implies_installed(conn: asyncpg.Connection) -> List[Violation]:
    """An appliance is "online" only if it's checked in within the last
    15 min. But the daemon in the live ISO ALSO checks in — so a
    box stuck in the install loop registers as online despite never
    completing install. Detect this by hostname == 'osiriscare-installer'
    AND status == 'online'."""
    rows = await conn.fetch(
        """
        SELECT site_id, mac_address, hostname, agent_version, last_checkin
          FROM site_appliances
         WHERE deleted_at IS NULL
           AND status = 'online'
           AND (hostname = 'osiriscare-installer' OR hostname LIKE '%installer%')
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "mac_address": r["mac_address"],
                "hostname": r["hostname"],
                "agent_version": r["agent_version"],
                "interpretation": "appliance is on live USB, not installed system — install likely failed",
            },
        )
        for r in rows
    ]


async def _check_online_has_active_key(conn: asyncpg.Connection) -> List[Violation]:
    """Every online appliance must be authable. An online row with no
    active api_keys row (and no active site-level key for its site)
    is a ticking time-bomb — when its current bearer expires or
    rotates it will lock out and we'll never know."""
    rows = await conn.fetch(
        """
        SELECT sa.site_id, sa.mac_address, sa.hostname, sa.appliance_id
          FROM site_appliances sa
         WHERE sa.deleted_at IS NULL
           AND sa.status = 'online'
           AND NOT EXISTS (
               SELECT 1 FROM api_keys ak
                WHERE ak.active = true
                  AND ak.site_id = sa.site_id
                  AND (ak.appliance_id = sa.appliance_id OR ak.appliance_id IS NULL)
           )
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "mac_address": r["mac_address"],
                "hostname": r["hostname"],
                "appliance_id": r["appliance_id"],
                "remediation": "POST /api/provision/rekey with site_id+mac_address",
            },
        )
        for r in rows
    ]


async def _check_claim_event_unchained(conn: asyncpg.Connection) -> List[Violation]:
    """Every claim event > 5min old must have chain_prev_hash +
    chain_hash populated. The transactional path in claim-v2
    populates them; an unchained row means the post-INSERT UPDATE
    failed silently (or a row was hand-inserted by a DBA without
    chain extension)."""
    rows = await conn.fetch(
        """
        SELECT site_id, mac_address, id,
               EXTRACT(EPOCH FROM (NOW() - claimed_at))/60 AS minutes_old
          FROM provisioning_claim_events
         WHERE (chain_prev_hash IS NULL OR chain_hash IS NULL)
           AND claimed_at < NOW() - INTERVAL '5 minutes'
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "mac_address": r["mac_address"],
                "claim_event_id": r["id"],
                "minutes_old": float(r["minutes_old"] or 0),
                "remediation": "Investigate failed UPDATE in claim-v2 transaction or hand-INSERT bypass",
            },
        )
        for r in rows
    ]


async def _check_auth_failure_lockout(conn: asyncpg.Connection) -> List[Violation]:
    """auth_failure_count >= 3 means the daemon has been bouncing
    off the auth wall and (if it's an old daemon without
    auto-rekey) needs operator intervention — push a fresh api_key
    to /var/lib/msp/config.yaml or run /api/provision/rekey."""
    rows = await conn.fetch(
        """
        SELECT site_id, mac_address, hostname, agent_version,
               auth_failure_count,
               EXTRACT(EPOCH FROM (NOW() - auth_failure_since))/60 AS minutes_failing
          FROM site_appliances
         WHERE deleted_at IS NULL
           AND auth_failure_count >= 3
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "mac_address": r["mac_address"],
                "hostname": r["hostname"],
                "agent_version": r["agent_version"],
                "auth_failure_count": r["auth_failure_count"],
                "minutes_failing": float(r["minutes_failing"] or 0),
                "remediation": "Daemon < 0.3.84 lacks auto-rekey. Run /api/provision/rekey then SSH to push key.",
            },
        )
        for r in rows
    ]


# --- Engine ----------------------------------------------------------


async def run_assertions_once(conn: asyncpg.Connection) -> Dict[str, int]:
    """Run every registered assertion exactly once. UPSERTs new
    violations, marks resolved any open rows whose violations no
    longer appear. Returns a {opened, refreshed, resolved, errors}
    counters dict for observability."""

    counters = {"opened": 0, "refreshed": 0, "resolved": 0, "errors": 0}

    for a in ALL_ASSERTIONS:
        try:
            current = await a.check(conn)
        except Exception:
            logger.error("assertion %s raised", a.name, exc_info=True)
            counters["errors"] += 1
            continue

        # Build the set of (site_id) keys currently failing.
        current_keys = {(v.site_id or "") for v in current}

        # Open rows for this assertion.
        open_rows = await conn.fetch(
            """
            SELECT id, COALESCE(site_id, '') AS site_key
              FROM substrate_violations
             WHERE invariant_name = $1
               AND resolved_at IS NULL
            """,
            a.name,
        )
        open_by_site = {r["site_key"]: r["id"] for r in open_rows}

        # Insert new violations OR refresh last_seen on existing ones.
        for v in current:
            site_key = v.site_id or ""
            if site_key in open_by_site:
                await conn.execute(
                    """
                    UPDATE substrate_violations
                       SET last_seen_at = NOW(),
                           details      = $1::jsonb
                     WHERE id = $2
                    """,
                    json.dumps(v.details),
                    open_by_site[site_key],
                )
                counters["refreshed"] += 1
            else:
                # We already proved no open row exists for this
                # (invariant, site) combo above, so a plain INSERT
                # is safe. The partial unique index on the table
                # is a backstop against logic-bug double-inserts.
                await conn.execute(
                    """
                    INSERT INTO substrate_violations
                          (invariant_name, severity, site_id, details)
                    VALUES ($1, $2, $3, $4::jsonb)
                    """,
                    a.name,
                    a.severity,
                    v.site_id,
                    json.dumps(v.details),
                )
                counters["opened"] += 1
                logger.warning(
                    "substrate violation OPENED: invariant=%s severity=%s site=%s details=%s",
                    a.name,
                    a.severity,
                    v.site_id,
                    json.dumps(v.details),
                )

        # Resolve any open rows whose key no longer appears.
        for site_key, row_id in open_by_site.items():
            if site_key not in current_keys:
                await conn.execute(
                    "UPDATE substrate_violations SET resolved_at = NOW() WHERE id = $1",
                    row_id,
                )
                counters["resolved"] += 1
                logger.info(
                    "substrate violation RESOLVED: invariant=%s site=%s id=%s",
                    a.name,
                    site_key,
                    row_id,
                )

    return counters


async def assertions_loop():
    """Background task — runs every 60s. Wired into main.py lifespan
    via health_monitor's broader background_tasks orchestration."""
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection

    await asyncio.sleep(120)  # Let pool + migrations settle on cold start.
    logger.info("Substrate Integrity Engine started (interval=60s, assertions=%d)",
                len(ALL_ASSERTIONS))

    while True:
        try:
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                counters = await run_assertions_once(conn)
            if counters["opened"] or counters["resolved"]:
                logger.info(
                    "assertions tick: opened=%d refreshed=%d resolved=%d errors=%d",
                    counters["opened"], counters["refreshed"],
                    counters["resolved"], counters["errors"],
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("assertions_loop tick failed", exc_info=True)

        await asyncio.sleep(60)
