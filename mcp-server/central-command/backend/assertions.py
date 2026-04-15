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
    Assertion(
        name="signature_verification_failures",
        severity="sev1",
        description="When a daemon presents a signature it must verify; sustained > 5% fail rate per site signals crypto drift, key compromise, or build-pipeline regression (Week 4)",
        check=lambda c: _check_signature_verification_failures(c),
    ),
    Assertion(
        name="claim_cert_expired_in_use",
        severity="sev1",
        description="Claims must only be accepted from CAs in their validity window; an expired/revoked CA being used means a code-path bypassed _validate_claim_cert (Week 4)",
        check=lambda c: _check_claim_cert_expired_in_use(c),
    ),
    Assertion(
        name="mac_rekeyed_recently",
        severity="sev2",
        description="A MAC with >= 2 claim events in 24h is either thrashing reinstall or impersonation; flag for operator review (Week 4)",
        check=lambda c: _check_mac_rekeyed_recently(c),
    ),
    Assertion(
        name="legacy_bearer_only_checkin",
        severity="sev3",
        description="Adoption tracker: online appliance has not produced a sigauth observation in 24h — daemon is bearer-only, blocks api_key retirement (Week 6)",
        check=lambda c: _check_legacy_bearer_only_checkin(c),
    ),
    Assertion(
        name="mesh_ring_deficit",
        severity="sev2",
        description="A multi-node site has an online appliance with 0 assigned_targets while siblings hold non-zero — ring didn't redistribute (Mesh Hardening Phase 3)",
        check=lambda c: _check_mesh_ring_deficit(c),
    ),
    Assertion(
        name="display_name_collision",
        severity="sev2",
        description="Two appliances at the same site share a display_name — operators cannot tell which physical box is which in the dashboard",
        check=lambda c: _check_display_name_collision(c),
    ),
    Assertion(
        name="winrm_circuit_open",
        severity="sev2",
        description="An appliance's per-site WinRM circuit is open (≥3 recent failures, zero successes in 30min). Remediation gated locally; other customers keep remediating. Replaces migration 164 global kill-switch.",
        check=lambda c: _check_winrm_circuit_open(c),
    ),
    Assertion(
        name="ghost_checkin_redirect",
        severity="sev2",
        description="An online appliance's last_checkin lags 15+ min behind its site's latest — its checkins are likely being rewritten into another row by false-positive ghost detection (Session 207 link-local fix).",
        check=lambda c: _check_ghost_checkin_redirect(c),
    ),
    Assertion(
        name="installed_but_silent",
        severity="sev1",
        description="An appliance completed the live-USB install phase but the installed system has never phoned home. Check the local status beacon at :8443 or the /boot/msp-boot-diag.json dump for per-boot diagnostics.",
        check=lambda c: _check_installed_but_silent(c),
    ),
    Assertion(
        name="watchdog_silent",
        severity="sev1",
        description="Watchdog service on a previously-reporting appliance has stopped checking in for >10 min. SSH-strip precondition regression — investigate immediately.",
        check=lambda c: _check_watchdog_silent(c),
    ),
    Assertion(
        name="watchdog_reports_daemon_down",
        severity="sev2",
        description="Watchdog is alive but reports the main daemon is not active. Issue watchdog_restart_daemon fleet order to remediate.",
        check=lambda c: _check_watchdog_reports_daemon_down(c),
    ),
    Assertion(
        name="winrm_pin_mismatch",
        severity="sev2",
        description="≥2 TLS pin check failures for the same Windows target in the last hour. Either the target's cert legitimately rotated (issue watchdog_reset_pin_store) or it's an in-progress MITM (investigate first).",
        check=lambda c: _check_winrm_pin_mismatch(c),
    ),
    Assertion(
        name="journal_upload_stale",
        severity="sev2",
        description="A previously-uploading appliance has not shipped a journal batch in >90 min (3x the 15-min cadence). Timer broken, egress blocked, or box offline.",
        check=lambda c: _check_journal_upload_stale(c),
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


async def _check_legacy_bearer_only_checkin(conn: asyncpg.Connection) -> List[Violation]:
    """Adoption tracker for the Week-6 api_key retirement.

    Online appliances that haven't produced any sigauth observations
    in 24h are running daemons too old to sign (pre-0.4.4) — they
    block flipping the platform to signature-only auth. Sev-3
    informational so it appears on the dashboard without paging
    anyone, and operators can plan upgrades for the long tail."""
    rows = await conn.fetch(
        """
        SELECT sa.site_id, sa.mac_address, sa.hostname, sa.agent_version
          FROM site_appliances sa
         WHERE sa.deleted_at IS NULL
           AND sa.status = 'online'
           AND NOT EXISTS (
                 SELECT 1 FROM sigauth_observations o
                  WHERE o.site_id = sa.site_id
                    AND UPPER(o.mac_address) = UPPER(sa.mac_address)
                    AND o.observed_at > NOW() - INTERVAL '24 hours'
           )
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "mac_address": r["mac_address"],
                "hostname": r["hostname"],
                "agent_version": r["agent_version"],
                "remediation": "Push daemon update to 0.4.4+ (signature-capable). Once all appliances clear this invariant, api_key retirement is safe.",
            },
        )
        for r in rows
    ]


async def _check_signature_verification_failures(conn: asyncpg.Connection) -> List[Violation]:
    """Per-site fail rate over the last hour of observed signatures.

    Floor of 5 samples to avoid false-flagging on a fresh site that
    happens to have its first checkin fail. Threshold of 5% — well
    above expected noise (sigs should have a ~0% fail rate when
    the daemon and server are in lockstep)."""
    rows = await conn.fetch(
        """
        SELECT site_id,
               COUNT(*)                                  AS total,
               COUNT(*) FILTER (WHERE valid = false)     AS failures,
               array_agg(DISTINCT reason) FILTER (WHERE valid = false) AS reasons
          FROM sigauth_observations
         WHERE observed_at > NOW() - INTERVAL '1 hour'
      GROUP BY site_id
        HAVING COUNT(*) >= 5
           AND COUNT(*) FILTER (WHERE valid = false) * 100.0 / COUNT(*) > 5
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "total_samples": r["total"],
                "failures": r["failures"],
                "fail_rate_pct": round(r["failures"] * 100.0 / r["total"], 1),
                "reasons": list(r["reasons"] or []),
            },
        )
        for r in rows
    ]


async def _check_claim_cert_expired_in_use(conn: asyncpg.Connection) -> List[Violation]:
    """Claims that arrived using a CA past its validity window or
    revoked. Should be impossible — iso_ca._validate_claim_cert
    rejects those — so a hit means a code path bypassed validation
    (or the CA was revoked AFTER a legitimate claim landed; the
    24h window catches the bypass case but tolerates the latter)."""
    rows = await conn.fetch(
        """
        SELECT pce.site_id, pce.mac_address, pce.iso_build_sha,
               irc.valid_until, irc.revoked_at, pce.claimed_at
          FROM provisioning_claim_events pce
          JOIN iso_release_ca_pubkeys irc
            ON irc.iso_release_sha = pce.iso_build_sha
         WHERE pce.claimed_at > NOW() - INTERVAL '1 hour'
           AND (
                  irc.valid_until < pce.claimed_at
               OR (irc.revoked_at IS NOT NULL AND irc.revoked_at < pce.claimed_at)
           )
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "mac_address": r["mac_address"],
                "iso_build_sha": r["iso_build_sha"],
                "ca_valid_until": r["valid_until"].isoformat() if r["valid_until"] else None,
                "ca_revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None,
                "claimed_at": r["claimed_at"].isoformat(),
            },
        )
        for r in rows
    ]


async def _check_mac_rekeyed_recently(conn: asyncpg.Connection) -> List[Violation]:
    """MAC that has produced multiple claim events in 24h. Likely a
    legitimate reinstall but worth flagging — clusters point to a
    flapping appliance OR an impersonation attempt against a real
    fleet member."""
    rows = await conn.fetch(
        """
        SELECT site_id, mac_address,
               COUNT(*) AS claim_count,
               MAX(claimed_at) AS latest,
               array_agg(DISTINCT source) AS sources
          FROM provisioning_claim_events
         WHERE claimed_at > NOW() - INTERVAL '24 hours'
      GROUP BY site_id, mac_address
        HAVING COUNT(*) >= 2
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "mac_address": r["mac_address"],
                "claim_count_24h": r["claim_count"],
                "latest_claimed_at": r["latest"].isoformat(),
                "sources": list(r["sources"] or []),
                "interpretation": "reinstall churn OR impersonation — verify with operator",
            },
        )
        for r in rows
    ]


async def _check_journal_upload_stale(conn: asyncpg.Connection) -> List[Violation]:
    """An appliance that previously uploaded a journal batch has gone
    silent for >90 min (3x the 15-min cadence). Either the journal-
    upload timer is broken on-box, outbound HTTPS to /api/journal/
    upload is blocked by the egress allowlist (Phase H1 regression),
    or the box is genuinely offline.

    Does NOT fire for appliances that have never uploaded — same
    pattern as `watchdog_silent`, avoids spurious violations during
    the v30 rollout window.
    """
    rows = await conn.fetch(
        """
        WITH latest AS (
            SELECT DISTINCT ON (appliance_id)
                   site_id, appliance_id, received_at
              FROM journal_upload_events
          ORDER BY appliance_id, received_at DESC
        )
        SELECT site_id, appliance_id, received_at,
               EXTRACT(EPOCH FROM (NOW() - received_at))/60 AS minutes_stale
          FROM latest
         WHERE received_at < NOW() - INTERVAL '90 minutes'
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "appliance_id": r["appliance_id"],
                "last_upload_at": r["received_at"].isoformat(),
                "minutes_stale": float(r["minutes_stale"] or 0),
                "remediation": (
                    "Check appliance's msp-journal-upload.timer status. "
                    "Common causes: daemon crash-loop (see watchdog_reports"
                    "_daemon_down), egress allowlist blocking /api/journal/"
                    "upload (see Phase H1 config), or full LAN outage."
                ),
            },
        )
        for r in rows
    ]


async def _check_winrm_pin_mismatch(conn: asyncpg.Connection) -> List[Violation]:
    """A Windows target's WinRM TLS certificate fingerprint differs from
    the pin the appliance stored on first TOFU. Common causes:
    (a) DC cert was renewed/rotated, (b) the target VM was rebuilt with
    a fresh cert, (c) a box on the target's IP is impersonating it
    (MITM / DNS hijack). First two are legitimate — fix by issuing a
    host-scoped `watchdog_reset_pin_store` fleet order (handler shipped
    in appliance-watchdog v0.1.0). The third is a genuine attack and
    the pin mismatch IS the expected detection.

    Fires when ≥2 TLS-pin failures for the same (appliance_id, hostname)
    pair in the last hour — single transient hiccups don't trigger,
    sustained mismatch does. Auto-resolves as soon as WinRM succeeds
    again (post-reset-or-attacker-vanished).
    """
    rows = await conn.fetch(
        """
        SELECT site_id, appliance_id, hostname,
               COUNT(*) AS recent_pin_fails,
               MAX(created_at) AS latest_fail_at,
               array_agg(DISTINCT runbook_id) AS runbooks_affected
          FROM execution_telemetry
         WHERE created_at > NOW() - INTERVAL '1 hour'
           AND NOT success
           AND (error_message ILIKE '%TLS pin%'
                OR error_message ILIKE '%pin check%')
         GROUP BY site_id, appliance_id, hostname
        HAVING COUNT(*) >= 2
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "appliance_id": r["appliance_id"],
                "target_hostname": r["hostname"],
                "recent_pin_fails": r["recent_pin_fails"],
                "latest_fail_at": r["latest_fail_at"].isoformat(),
                "runbooks_affected": list(r["runbooks_affected"] or []),
                "remediation": (
                    "Verify the target's cert fingerprint is the one you "
                    "expect (DC renewed? VM rebuilt? Or a real MITM?). "
                    "If legitimate, issue a host-scoped fleet order: "
                    "fleet_cli create watchdog_reset_pin_store "
                    "--param site_id=<site> --param appliance_id=<aid>-watchdog "
                    "--param host=<target_hostname> "
                    "--actor-email <you> --reason 'DC cert renewed <date>'. "
                    "If attack, DO NOT reset — investigate DNS / ARP / "
                    "route tables on the appliance first."
                ),
            },
        )
        for r in rows
    ]


async def _check_watchdog_silent(conn: asyncpg.Connection) -> List[Violation]:
    """The appliance-watchdog service checks in every 2 min. If an
    appliance has ever produced a watchdog_events.checkin AND the
    latest such row is > 10 min stale, either (a) the watchdog itself
    is down or (b) the box has lost network. Either way an operator
    needs to know because the watchdog is the SSH-strip precondition;
    if it's broken, we've regressed to needing SSH for recovery.

    Doesn't fire for appliances that never registered a watchdog
    (pre-v30 fleet) — only for boxes that USED to report and stopped.
    That distinction matters during the rollout window.
    """
    rows = await conn.fetch(
        """
        WITH latest AS (
            SELECT DISTINCT ON (appliance_id)
                   site_id, appliance_id, created_at
              FROM watchdog_events
             WHERE event_type = 'checkin'
          ORDER BY appliance_id, created_at DESC
        )
        SELECT site_id, appliance_id, created_at,
               EXTRACT(EPOCH FROM (NOW() - created_at))/60 AS minutes_silent
          FROM latest
         WHERE created_at < NOW() - INTERVAL '10 minutes'
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "watchdog_appliance_id": r["appliance_id"],
                "last_checkin": r["created_at"].isoformat(),
                "minutes_silent": float(r["minutes_silent"] or 0),
                "remediation": (
                    "Watchdog service on this appliance has stopped reporting. "
                    "Either the watchdog binary crashed, the -watchdog bearer "
                    "is invalid, or the box is fully offline. Check "
                    "site_appliances.last_checkin for the main daemon too — if "
                    "both are silent, physical/console access is now required. "
                    "If only the watchdog is silent, the watchdog is the bug."
                ),
            },
        )
        for r in rows
    ]


async def _check_watchdog_reports_daemon_down(conn: asyncpg.Connection) -> List[Violation]:
    """Watchdog is alive (recent checkin) AND its payload reports that
    the main daemon is NOT active. The watchdog did its job — surfaced
    the problem — now operator action is needed.
    """
    rows = await conn.fetch(
        """
        WITH latest AS (
            SELECT DISTINCT ON (appliance_id)
                   site_id, appliance_id, created_at, payload
              FROM watchdog_events
             WHERE event_type = 'checkin'
          ORDER BY appliance_id, created_at DESC
        )
        SELECT site_id, appliance_id, created_at, payload
          FROM latest
         WHERE created_at > NOW() - INTERVAL '10 minutes'
           AND payload->>'main_daemon_status' IS DISTINCT FROM 'active'
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "watchdog_appliance_id": r["appliance_id"],
                "watchdog_last_checkin": r["created_at"].isoformat(),
                "main_daemon_status": r["payload"].get("main_daemon_status"),
                "main_daemon_substate": r["payload"].get("main_daemon_substate"),
                "remediation": (
                    "Watchdog reports the main daemon is down. Issue a "
                    "watchdog_restart_daemon fleet order via "
                    "fleet_cli --actor-email / --reason. If that fails, "
                    "escalate to watchdog_redeploy_daemon."
                ),
            },
        )
        for r in rows
    ]


async def _check_installed_but_silent(conn: asyncpg.Connection) -> List[Violation]:
    """An appliance made it through the live-USB phase (install_sessions
    checkin_count ≥ 5) but the installed system has never phoned home.
    This is the "install appeared to complete, box reboots, daemon
    never talks" class of failure — observed on the t740 where the
    install completed cleanly from the operator's perspective but
    site_appliances.last_checkin stayed frozen at the live-USB-era
    timestamp. Previously invisible; the operator had no signal.

    Semantics:
      - install_sessions had enough boots to show install ran (≥5
        checkins from the live USB)
      - install_sessions.last_seen is now stale > 20 min (the box is
        no longer on the live USB — it's either installed, bricked,
        or dd'd by a fresh USB)
      - site_appliances.last_checkin is older than the appliance's
        install_sessions.first_seen + 15 min (the installed system
        has never produced a fresh heartbeat after the install
        window)

    Paired with the local status beacon on :8443 and the
    /boot/msp-boot-diag.json dump, an operator with LAN access can
    diagnose the specific failure in 60 seconds.
    """
    rows = await conn.fetch(
        """
        WITH install_peak AS (
            SELECT mac_address, site_id,
                   MAX(checkin_count) AS peak_count,
                   MAX(last_seen) AS last_live_usb_checkin,
                   MIN(first_seen) AS first_live_usb_checkin
              FROM install_sessions
             GROUP BY mac_address, site_id
        )
        SELECT sa.site_id, sa.mac_address, sa.hostname, sa.appliance_id,
               sa.agent_version, sa.last_checkin,
               ip.peak_count,
               ip.last_live_usb_checkin,
               ip.first_live_usb_checkin,
               EXTRACT(EPOCH FROM (NOW() - ip.last_live_usb_checkin))/60 AS minutes_since_usb
          FROM site_appliances sa
          JOIN install_peak ip
            ON UPPER(ip.mac_address) = UPPER(sa.mac_address)
           AND ip.site_id = sa.site_id
         WHERE sa.deleted_at IS NULL
           AND ip.peak_count >= 5
           AND ip.last_live_usb_checkin < NOW() - INTERVAL '20 minutes'
           AND (
                 sa.last_checkin IS NULL
              OR sa.last_checkin < ip.first_live_usb_checkin + INTERVAL '15 minutes'
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
                "agent_version": r["agent_version"],
                "peak_install_checkins": r["peak_count"],
                "minutes_since_last_usb_checkin": float(r["minutes_since_usb"] or 0),
                "last_site_appliances_checkin": (
                    r["last_checkin"].isoformat() if r["last_checkin"] else None
                ),
                "remediation": (
                    "Install completed but installed-system daemon has never "
                    "phoned home. LAN-scan for the local status beacon at "
                    "http://<appliance-ip>:8443/ for per-boot diagnostics, "
                    "OR attach the SSD to another system and read "
                    "/boot/msp-boot-diag.json. Common causes: MSP-DATA not "
                    "mounted, no DHCP lease, config.yaml missing, daemon "
                    "crash-loop, outbound HTTPS blocked."
                ),
            },
        )
        for r in rows
    ]


async def _check_ghost_checkin_redirect(conn: asyncpg.Connection) -> List[Violation]:
    """Multi-NIC ghost detection is rewriting one appliance's checkins into
    another appliance's row. This is correct when two MACs live on the same
    box (dual-NIC daemon). It is INCORRECT when the trigger was IP overlap
    on a non-unique range (link-local, WG, loopback) — observed in Session
    207 when 169.254.88.1 on every appliance's second interface turned two
    distinct boxes into each other's ghosts and misrouted a canary fleet
    order.

    Signal: two online site_appliances rows at the same site have NOT
    updated last_checkin in more than 15 min while the site as a whole is
    heartbeating. That gap usually means their checkins are being
    redirected to a third row. Sev2; operator should inspect sites.py
    Method-2 ghost-detection logs.
    """
    rows = await conn.fetch(
        """
        WITH site_heartbeat AS (
            SELECT site_id, MAX(last_checkin) AS site_latest
              FROM site_appliances
             WHERE deleted_at IS NULL
          GROUP BY site_id
        )
        SELECT sa.site_id, sa.mac_address, sa.hostname, sa.appliance_id,
               sa.last_checkin,
               EXTRACT(EPOCH FROM (sh.site_latest - sa.last_checkin))/60 AS lag_minutes,
               sh.site_latest
          FROM site_appliances sa
          JOIN site_heartbeat sh ON sh.site_id = sa.site_id
         WHERE sa.deleted_at IS NULL
           AND sa.status = 'online'
           AND sh.site_latest > NOW() - INTERVAL '5 minutes'
           AND sa.last_checkin < sh.site_latest - INTERVAL '15 minutes'
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "mac_address": r["mac_address"],
                "hostname": r["hostname"],
                "appliance_id": r["appliance_id"],
                "lag_minutes": float(r["lag_minutes"] or 0),
                "site_latest_checkin": r["site_latest"].isoformat() if r["site_latest"] else None,
                "this_row_last_checkin": r["last_checkin"].isoformat() if r["last_checkin"] else None,
                "remediation": "Check mcp-server logs for 'Multi-NIC ghost detected' entries mentioning this MAC. If the detection is false-positive (shared link-local / WG / loopback IP), review sites.py STEP 0.9 Method 2 exclusion list.",
            },
        )
        for r in rows
    ]


async def _check_winrm_circuit_open(conn: asyncpg.Connection) -> List[Violation]:
    """Per-appliance WinRM circuit breaker is open. Introduced alongside
    the rollback of migration 164's global kill-switch — replaces it with
    a per-(site,appliance) dynamic gate so only the actively-failing
    appliance falls back to monitoring mode while siblings + other
    customers keep remediating.

    Fires when any appliance has ≥3 WinRM failures with zero successes
    in the last 30 min (view `v_appliance_winrm_circuit`, migration 215).
    Auto-resolves as soon as one successful Windows runbook execution
    lands, which closes the circuit.
    """
    rows = await conn.fetch(
        """
        SELECT site_id, appliance_id,
               recent_winrm_fails, recent_successes,
               last_success_at, last_fail_at
          FROM v_appliance_winrm_circuit
         WHERE circuit_state = 'open'
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "appliance_id": r["appliance_id"],
                "recent_winrm_fails": r["recent_winrm_fails"],
                "recent_successes": r["recent_successes"],
                "last_success_at": (
                    r["last_success_at"].isoformat() if r["last_success_at"] else None
                ),
                "last_fail_at": (
                    r["last_fail_at"].isoformat() if r["last_fail_at"] else None
                ),
                "remediation": (
                    "Investigate WinRM credentials / TLS pins on this appliance. "
                    "Common causes: stale NTLM password after AD rotation, "
                    "stale TLS pin after DC cert renewal, WinRM service crash "
                    "on the target Windows host. Circuit auto-closes on first "
                    "successful Windows runbook execution."
                ),
            },
        )
        for r in rows
    ]


async def _check_display_name_collision(conn: asyncpg.Connection) -> List[Violation]:
    """Two rows with the same (site_id, display_name) leave the dashboard
    ambiguous — operators cannot tell which physical box is flopping
    online/offline because they share a label. Observed on
    `north-valley-branch-2` 2026-04-15 where the t740 install-loop box
    and the real `.227` appliance both held `osiriscare-3`. The Step 3.3
    generator in sites.py was hardened to enforce per-site uniqueness;
    this invariant catches any regression OR pre-existing collisions the
    fix didn't backfill.
    """
    rows = await conn.fetch(
        """
        SELECT site_id, display_name,
               COUNT(*) AS n,
               array_agg(mac_address ORDER BY first_checkin) AS macs,
               array_agg(hostname ORDER BY first_checkin) AS hostnames
          FROM site_appliances
         WHERE deleted_at IS NULL
           AND display_name IS NOT NULL
         GROUP BY site_id, display_name
        HAVING COUNT(*) > 1
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "display_name": r["display_name"],
                "colliding_count": r["n"],
                "mac_addresses": list(r["macs"] or []),
                "hostnames": list(r["hostnames"] or []),
                "remediation": "UPDATE site_appliances SET display_name = <unique> WHERE mac_address = <losing-mac>. Step 3.3 generator in sites.py enforces uniqueness going forward.",
            },
        )
        for r in rows
    ]


async def _check_mesh_ring_deficit(conn: asyncpg.Connection) -> List[Violation]:
    """An online appliance at a multi-node site must be holding some
    fraction of that site's targets. If a sibling has non-zero targets
    but this node has 0, the consistent-hash ring didn't redistribute
    when membership changed — Phase 3 of Mesh Hardening is supposed
    to prevent this by reassigning siblings in-line with any checkin,
    so a violation here means either STEP 3.8c errored (caught by the
    outer try/except in sites.py) OR a stale assignment never got
    refreshed. 3-minute grace to avoid flagging the sub-second gap
    between STEP 3 (last_checkin update) and STEP 3.8c (assignment
    write) during a brand-new checkin.
    """
    rows = await conn.fetch(
        """
        WITH site_totals AS (
            SELECT site_id,
                   COUNT(*) AS online_count,
                   MAX(COALESCE(jsonb_array_length(assigned_targets), 0)) AS max_tgts
              FROM site_appliances
             WHERE deleted_at IS NULL AND status = 'online'
          GROUP BY site_id
            HAVING COUNT(*) >= 2
        )
        SELECT sa.site_id, sa.mac_address, sa.hostname, sa.appliance_id,
               sa.agent_version, sa.assignment_epoch,
               COALESCE(jsonb_array_length(sa.assigned_targets), 0) AS my_target_count,
               st.max_tgts, st.online_count
          FROM site_appliances sa
          JOIN site_totals st ON st.site_id = sa.site_id
         WHERE sa.deleted_at IS NULL
           AND sa.status = 'online'
           AND COALESCE(jsonb_array_length(sa.assigned_targets), 0) = 0
           AND st.max_tgts > 0
           AND (sa.assignment_epoch IS NULL
                OR EXTRACT(EPOCH FROM NOW())::bigint - sa.assignment_epoch > 180)
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "mac_address": r["mac_address"],
                "hostname": r["hostname"],
                "appliance_id": r["appliance_id"],
                "agent_version": r["agent_version"],
                "my_target_count": r["my_target_count"],
                "peer_max_target_count": r["max_tgts"],
                "online_count_at_site": r["online_count"],
                "assignment_epoch": r["assignment_epoch"],
                "remediation": "Force a checkin on any sibling (systemctl restart appliance-daemon) — STEP 3.8c reassigns all online siblings in-line. If it re-opens, check mcp-server logs for target_assignment_failed or sibling_reassignment_failed.",
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

        # Phase T-B gate fix: collapse multi-Violation groups into
        # ONE row per (invariant, site). If an invariant returns several
        # Violations for the same site (e.g. winrm_pin_mismatch with two
        # target hosts), merge their details into a single row with a
        # `matches` array — not N rows that race the partial UNIQUE
        # index and crash the engine mid-tick.
        collapsed: Dict[str, Violation] = {}
        for v in current:
            site_key = v.site_id or ""
            if site_key in collapsed:
                existing = collapsed[site_key].details
                if "matches" not in existing:
                    # Lift the first violation into a matches[] entry
                    collapsed[site_key] = Violation(
                        site_id=v.site_id,
                        details={"matches": [existing]},
                    )
                collapsed[site_key].details.setdefault("matches", []).append(v.details)
                collapsed[site_key].details["match_count"] = len(
                    collapsed[site_key].details["matches"]
                )
            else:
                collapsed[site_key] = v

        current_keys = set(collapsed.keys())

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

        # Phase T-B gate fix: every mutation below runs in its own
        # savepoint so a single UniqueViolation (or any other error)
        # doesn't abort the outer transaction + blind the remaining
        # invariants for the tick. Each site's UPDATE/INSERT/resolve
        # is atomic and independently retryable; one bad row touches
        # only its own counter.
        for site_key, v in collapsed.items():
            if site_key in open_by_site:
                try:
                    async with conn.transaction():
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
                except Exception:
                    logger.error(
                        "substrate refresh failed: invariant=%s site=%s",
                        a.name, site_key, exc_info=True,
                    )
                    counters["errors"] += 1
            else:
                try:
                    async with conn.transaction():
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
                except Exception:
                    # UniqueViolation here = race between two tick passes
                    # (or a previous tick partially committed). Resolve
                    # by treating this as a refresh on the existing row.
                    logger.warning(
                        "substrate INSERT raced: invariant=%s site=%s — falling back to refresh",
                        a.name, site_key, exc_info=True,
                    )
                    try:
                        async with conn.transaction():
                            await conn.execute(
                                """
                                UPDATE substrate_violations
                                   SET last_seen_at = NOW(),
                                       details      = $1::jsonb
                                 WHERE invariant_name = $2
                                   AND COALESCE(site_id, '') = $3
                                   AND resolved_at IS NULL
                                """,
                                json.dumps(v.details), a.name, site_key,
                            )
                        counters["refreshed"] += 1
                    except Exception:
                        counters["errors"] += 1

        for site_key, row_id in open_by_site.items():
            if site_key not in current_keys:
                try:
                    async with conn.transaction():
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
                except Exception:
                    logger.error(
                        "substrate resolve failed: invariant=%s site=%s id=%s",
                        a.name, site_key, row_id, exc_info=True,
                    )
                    counters["errors"] += 1

    return counters


async def _ttl_sweep(conn: asyncpg.Connection) -> int:
    """Reclaim sigauth_observations older than 24h. Bounded-volume
    table — sweeping every 60s tick is cheap and keeps the working
    set in cache. Returns the number of rows deleted."""
    result = await conn.execute(
        "DELETE FROM sigauth_observations WHERE observed_at < NOW() - INTERVAL '24 hours'"
    )
    # asyncpg returns 'DELETE <n>'; parse out n for observability.
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0


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
                # TTL sweep in same conn — cheap, atomic per tick.
                deleted = await _ttl_sweep(conn)
            if counters["opened"] or counters["resolved"] or deleted:
                logger.info(
                    "assertions tick: opened=%d refreshed=%d resolved=%d errors=%d sigauth_swept=%d",
                    counters["opened"], counters["refreshed"],
                    counters["resolved"], counters["errors"], deleted,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("assertions_loop tick failed", exc_info=True)

        await asyncio.sleep(60)
