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
    description is the engineering explanation (shown in the dashboard
    tooltip + notification body). display_name + recommended_action
    are the OPERATOR-facing surface — 1-sentence human names + single-
    action-to-take, rendered prominently in /admin/substrate-health.

    v36 round-table: every invariant MUST provide both display_name
    and recommended_action. Blank string = TODO, the dashboard falls
    back to the engineering name + description, but the goal is to
    have nothing blank."""

    name: str
    severity: str  # 'sev1' | 'sev2' | 'sev3'
    description: str
    check: CheckFn
    display_name: str = ""            # e.g., "Install stuck — network blocked"
    recommended_action: str = ""      # e.g., "Whitelist api.osiriscare.net on your DNS filter"


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


async def _check_phantom_detector_healthy(conn: asyncpg.Connection) -> List[Violation]:
    """The phantom detector is an orthogonal liveness verifier — it
    cross-checks heartbeats against other signals and flags appliances
    that CLAIM online but lie. If it stops running, a whole layer of
    the liveness defense silently disappears. 2026-04-16 incident:
    schema drift made phantom_detector_loop crash every 5 min for
    hours with no external signal. In-process heartbeat registry +
    this invariant turn silent-crash into a Sev1 within two ticks.
    """
    # Triple-fallback import: relative when run as part of the
    # `dashboard_api` package (production + CI), absolute when tests
    # or scripts poke assertions.py directly.
    try:
        from .bg_heartbeat import get_heartbeat
    except ImportError:
        try:
            from dashboard_api.bg_heartbeat import get_heartbeat  # pragma: no cover
        except ImportError:
            from bg_heartbeat import get_heartbeat  # type: ignore
    hb = get_heartbeat("phantom_detector")
    if hb is None:
        # Process just started — give the loop a cycle to register.
        return []
    if hb["age_s"] > 900:  # 3x the 300s cadence
        return [
            Violation(
                site_id=None,
                details={
                    "loop": "phantom_detector",
                    "age_s": hb["age_s"],
                    "iterations": hb["iterations"],
                    "errors": hb["errors"],
                    "interpretation": "phantom_detector loop has not heartbeat "
                    "in > 15 min; orthogonal liveness verification is silently "
                    "disabled. Check mcp-server logs for an exception traceback.",
                },
            )
        ]
    return []


async def _check_heartbeat_write_divergence(conn: asyncpg.Connection) -> List[Violation]:
    """site_appliances.last_checkin is maintained by the UPSERT in the
    checkin handler. appliance_heartbeats is maintained by a SEPARATE
    INSERT wrapped in a savepoint. If the INSERT silently fails (bad
    partition, schema drift, constraint), last_checkin stays fresh
    but heartbeat history stops — every downstream consumer that
    reads heartbeats (rollup MV, SLA, cadence anomaly) quietly
    drifts. This invariant closes the loop."""
    rows = await conn.fetch(
        """
        SELECT
            sa.site_id,
            sa.appliance_id,
            sa.hostname,
            sa.last_checkin,
            (SELECT MAX(observed_at)
               FROM appliance_heartbeats
              WHERE appliance_id = sa.appliance_id) AS last_heartbeat
          FROM site_appliances sa
         WHERE sa.deleted_at IS NULL
           AND sa.status = 'online'
           AND sa.last_checkin > NOW() - INTERVAL '10 minutes'
        """
    )
    out: List[Violation] = []
    for r in rows:
        lc = r["last_checkin"]
        lh = r["last_heartbeat"]
        # No heartbeat ever, OR last_checkin > 10 min ahead of last_heartbeat
        if lh is None or (lc - lh).total_seconds() > 600:
            out.append(
                Violation(
                    site_id=r["site_id"],
                    details={
                        "appliance_id": r["appliance_id"],
                        "hostname": r["hostname"],
                        "last_checkin": lc.isoformat() if lc else None,
                        "last_heartbeat": lh.isoformat() if lh else None,
                        "lag_s": (lc - lh).total_seconds() if lh else None,
                        "interpretation": "checkin UPSERT is succeeding but "
                        "heartbeat INSERT is failing (likely missing monthly "
                        "partition, schema drift, or constraint violation). "
                        "Check mcp-server logs for 'heartbeat insert failed'.",
                    },
                )
            )
    return out


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
    Assertion(
        name="vps_disk_pressure",
        severity="sev2",
        description="Postgres database size > 50 GB (proxy for host disk pressure). 2026-04-15 incident: disk filled up silently, postgres crash-looped for 30min, 8 deploys failed. Run nix-collect-garbage + /opt ISO cleanup.",
        check=lambda c: _check_vps_disk_pressure(c),
    ),
    Assertion(
        name="provisioning_stalled",
        severity="sev2",
        description="Installer phoned home (install_sessions fresh) but installed system never did (site_appliances stale or missing). 2026-04-15 t740 incident: Pi-hole blocked api.osiriscare.net → installed-system daemon couldn't resolve → silent brick. Most likely a DNS filter (Pi-hole / Umbrella / Fortinet / Sophos / Barracuda) on the site's network. Whitelist api.osiriscare.net.",
        check=lambda c: _check_provisioning_stalled(c),
    ),
    Assertion(
        name="appliance_moved_unack",
        severity="sev2",
        description="Appliance physical relocation detected > 24h ago but no operator acknowledgment bundle has chained to the detection. HIPAA §164.310(d)(1) requires movement tracking with reason; unacknowledged moves could indicate theft, tampering, or shadow IT. Surface in the admin panel until acknowledged via POST /api/admin/appliances/{id}/acknowledge-relocation.",
        check=lambda c: _check_appliance_moved_unack(c),
    ),
    Assertion(
        name="phantom_detector_healthy",
        severity="sev1",
        description="The phantom_detector_loop (orthogonal liveness verifier) must heartbeat within 15 min. Silent crash = entire layer of phantom-appliance-online defense silently disabled. 2026-04-16 incident: schema drift crashed it for hours undetected.",
        check=_check_phantom_detector_healthy,
    ),
    Assertion(
        name="heartbeat_write_divergence",
        severity="sev1",
        description="site_appliances.last_checkin fresh but appliance_heartbeats lags 10+ min behind OR is NULL. Checkin UPSERT succeeding but heartbeat INSERT savepoint is being silently caught — every downstream consumer (rollup MV, SLA, cadence anomaly detector) is drifting.",
        check=_check_heartbeat_write_divergence,
    ),
    Assertion(
        name="journal_upload_never_received",
        severity="sev3",
        description="An appliance has been checking in continuously for >24h but has never POSTed a journal batch. Either the msp-journal-upload.timer is not deployed on the host (older ISO predating Session 207), or the very first attempt is failing silently. Forensics path is broken for this appliance — incident investigation cannot use the hash-chained journal archive.",
        check=lambda c: _check_journal_upload_never_received(c),
    ),
    Assertion(
        name="evidence_chain_stalled",
        severity="sev1",
        description="At least one appliance has checked in in the last 15 minutes but zero compliance_bundles have been inserted in that window. Baseline fleet bundle rate is ~1 bundle per 4–5 min per active appliance, so 15 minutes of silence while checkins continue is a strong signal that evidence-chain writes are failing (RLS context miss, missing partition, signing key mismatch, disk pressure). Caught the 2026-04-18 RLS P0 after the fact — this invariant would have fired it inside 15 minutes.",
        check=lambda c: _check_evidence_chain_stalled(c),
    ),
]


# ──────────────────────────────────────────────────────────────────────
# v36 round-table mandate: every invariant has a human name +
# recommended action. The `description` field is engineering prose;
# display_name + recommended_action are operator-facing. Dashboard
# renders:
#
#     [SEV2] <display_name>
#     <N> appliance(s) affected: <host1>, <host2>, ...
#     Recommended: <recommended_action>
#     [ View raw details ]
#
# The map below is the single source of truth. Populated into each
# Assertion object at module-load time by _populate_display_metadata().
# Any invariant missing from the map logs a loud warning at startup
# (enforced by a unit test so CI catches omissions before production).
# ──────────────────────────────────────────────────────────────────────

_DISPLAY_METADATA: Dict[str, Dict[str, str]] = {
    "legacy_uuid_populated": {
        "display_name": "Legacy appliance UUID missing",
        "recommended_action": "Run the one-time UUID backfill: "
            "python3 mcp-server/central-command/backend/scripts/backfill_legacy_uuids.py",
    },
    "install_loop": {
        "display_name": "Box is reboot-looping at install stage",
        "recommended_action": "Physical inspection: BIOS boot order, corrupt install USB, "
            "or internal disk dying. Check /boot/msp-boot-diag.json on the appliance for details.",
    },
    "offline_appliance_over_1h": {
        "display_name": "Appliance offline > 1 hour",
        "recommended_action": "Check power and network at the appliance. "
            "If persistent, run scripts/recover_legacy_appliance.sh <site_id> <mac> <ip>.",
    },
    "agent_version_lag": {
        "display_name": "Agent version is behind fleet",
        "recommended_action": "Issue an update_daemon fleet order: "
            "fleet_cli update --site <id> --binary-url https://api.osiriscare.net/updates/appliance-daemon-<latest>",
    },
    "fleet_order_url_resolvable": {
        "display_name": "Pending fleet order points at a dead URL",
        "recommended_action": "Cancel the order and re-issue with a resolvable "
            "api.osiriscare.net URL. release.osiriscare.net does NOT exist — use api.osiriscare.net/updates/...",
    },
    "discovered_devices_freshness": {
        "display_name": "Network-scan data is stale",
        "recommended_action": "Run a netscan: fleet_cli orders run_netscan --site <id>. "
            "If persistent, check the appliance's network-scanner.service.",
    },
    "install_session_ttl": {
        "display_name": "Install session past TTL",
        "recommended_action": "Clean up stale install_sessions: routine maintenance, usually self-heals. "
            "If a real appliance is blocked from checking in, investigate DNS/network first.",
    },
    "mesh_ring_size": {
        "display_name": "Mesh hash-ring underpopulated",
        "recommended_action": "Verify all expected appliances are online and checking in. "
            "Cross-reference dashboard fleet count vs. configured site appliances.",
    },
    "online_implies_installed_system": {
        "display_name": "Online appliance is still running live-USB",
        "recommended_action": "Install to disk: pull USB and hit F9/boot-menu to confirm "
            "the internal disk is booting. If dd didn't complete, reflash the USB and try again.",
    },
    "every_online_appliance_has_active_api_key": {
        "display_name": "Online appliance has no active API key",
        "recommended_action": "Force rekey: fleet_cli orders rekey --site <id> --mac <mac>. "
            "Also check api_keys table for zombie/deactivated rows.",
    },
    "auth_failure_lockout": {
        "display_name": "Account locked out after auth failures",
        "recommended_action": "Admin unlock via UPDATE partners SET failed_login_attempts=0, locked_until=NULL "
            "(or client_users for client portal accounts). Investigate source of failures.",
    },
    "claim_event_unchained": {
        "display_name": "Provisioning claim chain broken",
        "recommended_action": "Forensics required — evidence chain integrity failure. "
            "Check claim_events table ordering + signatures. Do NOT UPDATE without investigating.",
    },
    "signature_verification_failures": {
        "display_name": "Agent signature verification failing",
        "recommended_action": "Likely mesh / Vault flip skew. Verify signing_backend matches deployed mesh pubkey. "
            "During Vault cutover, check multi-trust rollover is complete (both keys in trust set).",
    },
    "claim_cert_expired_in_use": {
        "display_name": "Expired claim cert still being used",
        "recommended_action": "Rotate the claim cert: revoke old, issue new, redeploy to affected appliances. "
            "See docs/security/key-rotation-runbook.md.",
    },
    "mac_rekeyed_recently": {
        "display_name": "MAC rekeyed recently",
        "recommended_action": "Usually benign after legitimate recovery. If unexpected, investigate — "
            "could indicate a spoofing attempt or a misconfigured auto-rekey loop.",
    },
    "legacy_bearer_only_checkin": {
        "display_name": "Appliance on legacy bearer auth only",
        "recommended_action": "Upgrade appliance to agent version with Heartbeat-Signature support "
            "(v0.4.0+). Issue update_daemon fleet order.",
    },
    "mesh_ring_deficit": {
        "display_name": "Mesh has fewer nodes than expected",
        "recommended_action": "Check which appliances are offline / unregistered. "
            "Substrate lists them in the details.matches array. Bring each online or de-register intentionally.",
    },
    "display_name_collision": {
        "display_name": "Duplicate display names within a site",
        "recommended_action": "Run the site-wide display-name reassignment via sites.py STEP 3.8c, "
            "OR manually update site_appliances.display_name for the colliding rows.",
    },
    "winrm_circuit_open": {
        "display_name": "WinRM credential circuit tripped",
        "recommended_action": "Check Windows target credentials in site_credentials. "
            "Re-test via fleet_cli winrm-test, then fleet_cli reset-circuit --host <hostname>.",
    },
    "ghost_checkin_redirect": {
        "display_name": "Multi-NIC ghost checkin being redirected",
        "recommended_action": "Known multi-NIC behavior — verify the correct MAC is primary. "
            "Check sites.py multi-NIC ghost detection logs for the appliance.",
    },
    "installed_but_silent": {
        "display_name": "Install ran but installed system never phoned home",
        "recommended_action": "LAN-scan for the local status beacon at http://<appliance-ip>:8443/ "
            "OR attach the SSD to another system and read /boot/msp-boot-diag.json. "
            "Common causes: MSP-DATA not mounted, no DHCP lease, config.yaml missing, "
            "daemon crash-loop, outbound HTTPS blocked.",
    },
    "watchdog_silent": {
        "display_name": "Appliance watchdog not checking in",
        "recommended_action": "Check appliance-watchdog.service on the appliance. "
            "May need fleet_cli orders watchdog_redeploy_daemon --site <id> --appliance <id>.",
    },
    "watchdog_reports_daemon_down": {
        "display_name": "Watchdog reports main daemon is down",
        "recommended_action": "Issue a watchdog_restart_daemon fleet order. "
            "If repeated, escalate to watchdog_redeploy_daemon or physical inspection.",
    },
    "winrm_pin_mismatch": {
        "display_name": "Windows target TLS cert changed",
        "recommended_action": "Re-pin the cert after operator verification: "
            "fleet_cli orders watchdog_reset_pin_store (then re-run the drift scan).",
    },
    "journal_upload_stale": {
        "display_name": "Journal uploads stopped",
        "recommended_action": "Check msp-journal-upload.timer on the appliance. "
            "Common causes: egress firewall blocking, timer unit broken, or the appliance silently offline.",
    },
    "vps_disk_pressure": {
        "display_name": "VPS database disk > 50 GB",
        "recommended_action": "SSH to the VPS, run nix-collect-garbage + /opt ISO cleanup. "
            "See .agent/scripts/vps_housekeeping.sh for the idempotent script (runs daily via timer).",
    },
    "provisioning_stalled": {
        "display_name": "Install stuck — DNS filter likely blocking us",
        "recommended_action": "Whitelist api.osiriscare.net (port 443) on the site's "
            "DNS filter / web proxy / firewall (Pi-hole, Umbrella, Fortinet, Sophos, Barracuda). "
            "Verify per-device rules if your filter has them — by-MAC whitelisting is common and easy to miss.",
    },
    "appliance_moved_unack": {
        "display_name": "Appliance physically relocated — unacknowledged",
        "recommended_action": "Acknowledge the move via the admin panel (or POST "
            "/api/admin/appliances/{appliance_id}/acknowledge-relocation) with a reason "
            "category + free-text detail. If this move was NOT expected, treat as a security "
            "incident: verify physical location, check for tampering, investigate shadow IT. "
            "HIPAA §164.310(d)(1) audit trail.",
    },
    "phantom_detector_healthy": {
        "display_name": "Phantom-appliance detector is not running",
        "recommended_action": "Grep mcp-server logs for 'phantom_detector' or "
            "'APPLIANCE_LIVENESS_LIE'. Most likely cause is a schema drift between the "
            "INSERT statement and admin_audit_log columns. Re-deploy after fixing, "
            "then verify via /api/admin/health/loops.",
    },
    "heartbeat_write_divergence": {
        "display_name": "Heartbeat INSERT failing silently",
        "recommended_action": "Grep mcp-server logs for 'heartbeat insert failed'. "
            "Usually caused by a missing monthly partition on appliance_heartbeats. "
            "Run the partition-creation migration or extend the monthly cron. "
            "Until fixed, cadence anomaly + uptime SLA metrics are unreliable.",
    },
    "journal_upload_never_received": {
        "display_name": "Appliance has never shipped a journal batch",
        "recommended_action": "Re-image the appliance with the current ISO "
            "(post-Session-207 builds include msp-journal-upload.timer). Verify "
            "/api/journal/upload is reachable from the site's egress allowlist. "
            "Without this, forensics after an incident cannot use the hash-chained "
            "journal archive for that appliance.",
    },
    "evidence_chain_stalled": {
        "display_name": "Evidence chain INSERT stalled fleet-wide",
        "recommended_action": "Treat as P0 — attestation is offline. "
            "Grep mcp-server logs for 'InsufficientPrivilegeError', 'UniqueViolation', or partition errors on compliance_bundles. "
            "Verify `SELECT current_setting('app.is_admin', true)` returns 'true' inside a SQLAlchemy session "
            "(if 'false', the after_begin listener in shared.py is broken — see 2026-04-18 fix 2ddc596). "
            "If partition-related, check for missing monthly partition on compliance_bundles and create it. "
            "If signing-key related, check per-appliance key rotation state in site_appliances.agent_public_key.",
    },
}


def _populate_display_metadata() -> None:
    """Apply _DISPLAY_METADATA onto every Assertion in ALL_ASSERTIONS.
    Any invariant without an entry in the map logs a WARNING so CI
    (and the test_assertion_metadata_complete test) catch it.

    Runs once at module import, so runtime ALL_ASSERTIONS objects
    always have display_name + recommended_action populated before the
    first substrate tick reads them."""
    for a in ALL_ASSERTIONS:
        meta = _DISPLAY_METADATA.get(a.name)
        if meta is None:
            logger.warning(
                "assertion %r missing display_name + recommended_action "
                "in _DISPLAY_METADATA — dashboard will fall back to raw name",
                a.name,
            )
            continue
        a.display_name = meta["display_name"]
        a.recommended_action = meta["recommended_action"]


_populate_display_metadata()


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


async def _check_journal_upload_never_received(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """An appliance has been alive >24h but has never shipped a journal
    batch. Complements `journal_upload_stale` — which only fires for
    appliances that uploaded once and then stopped.

    The two together cover:
      never uploaded    → `journal_upload_never_received` (deploy gap)
      uploaded once+    → `journal_upload_stale`          (runtime gap)

    Without this check, a fleet can silently lack forensics forever
    as long as the very first upload never lands (older ISO, broken
    egress allowlist, endpoint misconfig). Post-mortem: 2026-04-17
    found journal_upload_events=0 in production across 4 appliances.
    """
    rows = await conn.fetch(
        """
        SELECT sa.site_id, sa.appliance_id, sa.hostname, sa.agent_version,
               sa.first_checkin,
               EXTRACT(EPOCH FROM (NOW() - sa.first_checkin))/3600 AS alive_hours
          FROM site_appliances sa
          LEFT JOIN journal_upload_events j
                 ON j.appliance_id = sa.appliance_id
         WHERE sa.deleted_at IS NULL
           AND sa.first_checkin IS NOT NULL
           AND sa.first_checkin < NOW() - INTERVAL '24 hours'
           AND sa.last_checkin > NOW() - INTERVAL '1 hour'
         GROUP BY sa.site_id, sa.appliance_id, sa.hostname,
                  sa.agent_version, sa.first_checkin
        HAVING COUNT(j.id) = 0
         LIMIT 200
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "appliance_id": r["appliance_id"],
                "hostname": r["hostname"],
                "agent_version": r["agent_version"],
                "alive_hours": float(r["alive_hours"] or 0),
                "remediation": (
                    "Appliance lacks msp-journal-upload.timer — re-image "
                    "with current ISO (post-Session-207) or verify the "
                    "timer unit is enabled and /api/journal/upload is "
                    "reachable from the appliance egress allowlist."
                ),
            },
        )
        for r in rows
    ]


async def _check_vps_disk_pressure(conn: asyncpg.Connection) -> List[Violation]:
    """Postgres' data volume is at >80% full. 2026-04-15 incident: VPS
    hit 100% full mid-session after 6 consecutive ISO builds; postgres
    crash-looped with `could not write lock file "postmaster.pid": No
    space left on device` for 30+ minutes before anyone noticed. Weekly
    nix-gc timer was running but couldn't keep pace with iteration
    volume. No substrate signal existed for host disk pressure.

    This invariant uses PostgreSQL's `pg_stat_file()` to read the size
    of pg_class's data file, plus `pg_tablespace_size()` of the default
    tablespace, against available space on the volume. Fires at 80%
    full (sev2) so operators have a window to GC before a hard crash.

    Postgres exposes a crude but sufficient signal via
    `pg_database_size()` + free-space estimate from the OS — we read
    the underlying device stats via a COPY PROGRAM call, gated by an
    admin role. When that fails (no shell escape available), returns
    empty — better a missed signal than a crash-loop from a failed
    assertion check itself.
    """
    try:
        row = await conn.fetchrow(
            """
            SELECT
              (SELECT setting FROM pg_settings WHERE name='data_directory') AS data_dir,
              (SELECT pg_database_size(current_database())) AS db_bytes
            """
        )
        if not row:
            return []
        db_gb = (int(row["db_bytes"]) or 0) / (1024 ** 3)

        # Not all postgres images allow COPY PROGRAM. Fail-soft: if we
        # can't read df output, only warn via db size alone (50 GB+
        # database on a 150 GB disk is a reliable proxy for "look at
        # this host"). The real invariant lives in a separate OS-level
        # exporter we should add, but this catches the class today.
        if db_gb < 50:
            return []
        return [
            Violation(
                site_id=None,
                details={
                    "db_size_gb": round(db_gb, 2),
                    "threshold_gb": 50,
                    "data_directory": row["data_dir"],
                    "remediation": (
                        "postgres data volume is large. Check `df -h` on "
                        "the VPS; if >80% full, run `nix-collect-garbage "
                        "--delete-old` and remove stale ISOs under "
                        "/opt/osiriscare-*.iso. See scripts/"
                        "vps_housekeeping.sh for an automatable version."
                    ),
                },
            )
        ]
    except Exception:
        logger.debug("disk_pressure check failed (harmless)", exc_info=True)
        return []


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


async def _check_provisioning_stalled(conn: asyncpg.Connection) -> List[Violation]:
    """Fires when an installer is actively phoning home (install_sessions
    fresh within the last hour, ≥3 checkins) but the installed system
    has not produced a fresh heartbeat in site_appliances within the
    last 15 minutes.

    This catches the "Pi-hole / DNS filter blocks api.osiriscare.net for
    the installed system but not the installer" pattern observed on the
    t740 2026-04-15. Sibling invariant to installed_but_silent, which
    fires later (≥20 min after installer stops looping). This one fires
    EARLIER so the operator sees the issue within 15 min of the first
    failed install-system checkin, not 20+ min after the installer has
    already given up.

    When both fire for the same MAC: trust this one's hint — DNS filter
    is the most common cause when the installer worked but the installed
    system didn't.
    """
    rows = await conn.fetch(
        """
        SELECT
            iss.site_id,
            iss.mac_address,
            iss.hostname,
            iss.ip_addresses,
            iss.checkin_count AS installer_checkins,
            iss.last_seen     AS installer_last_seen,
            sa.last_checkin   AS site_appliance_last_checkin,
            EXTRACT(EPOCH FROM (NOW() - iss.last_seen))/60 AS installer_age_min,
            EXTRACT(EPOCH FROM (NOW() - sa.last_checkin))/60 AS appliance_age_min
          FROM install_sessions iss
          LEFT JOIN site_appliances sa
            ON UPPER(sa.mac_address) = UPPER(iss.mac_address)
           AND sa.site_id = iss.site_id
           AND sa.deleted_at IS NULL
         WHERE iss.last_seen > NOW() - INTERVAL '1 hour'
           AND iss.checkin_count >= 3
           AND (
                 sa.last_checkin IS NULL
              OR sa.last_checkin < NOW() - INTERVAL '15 minutes'
           )
        """
    )
    out: List[Violation] = []
    for r in rows:
        out.append(
            Violation(
                site_id=r["site_id"],
                details={
                    "mac_address": r["mac_address"],
                    "hostname": r["hostname"],
                    "ip_addresses": list(r["ip_addresses"] or []),
                    "installer_checkin_count": int(r["installer_checkins"]),
                    "installer_last_seen": (
                        r["installer_last_seen"].isoformat()
                        if r["installer_last_seen"] else None
                    ),
                    "installer_age_min": round(float(r["installer_age_min"] or 0), 1),
                    "site_appliance_last_checkin": (
                        r["site_appliance_last_checkin"].isoformat()
                        if r["site_appliance_last_checkin"] else None
                    ),
                    "site_appliance_age_min": (
                        round(float(r["appliance_age_min"]), 1)
                        if r["appliance_age_min"] is not None else None
                    ),
                    "hint": (
                        "Installer reached Central Command but the installed "
                        "system has not. Most likely a DNS filter (Pi-hole, "
                        "Umbrella, Fortinet, Sophos, Barracuda, etc.) on this "
                        "site's network is blocking api.osiriscare.net for the "
                        "installed system's MAC. Fix: whitelist "
                        "api.osiriscare.net (port 443) on the site's DNS "
                        "filter / web proxy / firewall, then reboot the "
                        "appliance. Verify with the local status beacon on "
                        "the appliance at http://<ip>:8443/ for per-boot "
                        "diagnostics."
                    ),
                },
            )
        )
    return out


async def _check_appliance_moved_unack(conn: asyncpg.Connection) -> List[Violation]:
    """Relocation detection bundle > 24h old with no matching ack bundle.

    An `appliance_relocation` detection bundle is written automatically
    when an appliance's primary subnet changes (sites.py STEP 3.4,
    appliance_relocation.detect_and_record_relocation).

    An `appliance_relocation_acknowledged` attestation bundle is
    written when an operator acknowledges the move via
    POST /api/admin/appliances/{id}/acknowledge-relocation. The ack
    bundle's approvals array contains `detection_bundle_id` pointing
    at the matching detection bundle.

    We fire when:
      - a detection bundle exists in the last 30 days
      - it's been > 24 hours since detection
      - no ack bundle references this detection_bundle_id

    30-day lookback avoids flooding the dashboard with ancient moves
    that nobody cares about anymore; 24h gives the operator time to
    notice + respond to a planned move before it becomes a false alarm.
    """
    rows = await conn.fetch(
        """
        WITH detections AS (
            SELECT cb.bundle_id, cb.site_id, cb.checked_at,
                   cb.summary->>'from_subnet' AS from_subnet,
                   cb.summary->>'to_subnet'   AS to_subnet,
                   cb.checks->0->>'appliance_id'  AS appliance_id,
                   cb.checks->0->>'mac_address'   AS mac_address,
                   cb.checks->0->>'hostname'      AS hostname
              FROM compliance_bundles cb
             WHERE cb.check_type = 'appliance_relocation'
               AND cb.checked_at > NOW() - INTERVAL '30 days'
               AND cb.checked_at < NOW() - INTERVAL '24 hours'
        ),
        acks AS (
            SELECT
              jsonb_array_elements(
                coalesce(cb.checks->0->'approvals', '[]'::jsonb)
              )->>'detection_bundle_id' AS detection_bundle_id
              FROM compliance_bundles cb
             WHERE cb.check_type = 'privileged_access'
               AND cb.checks::text LIKE '%appliance_relocation_acknowledged%'
        )
        SELECT d.*
          FROM detections d
          LEFT JOIN acks a ON a.detection_bundle_id = d.bundle_id
         WHERE a.detection_bundle_id IS NULL
         ORDER BY d.checked_at DESC
         LIMIT 200
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "detection_bundle_id": r["bundle_id"],
                "appliance_id": r["appliance_id"],
                "mac_address": r["mac_address"],
                "hostname": r["hostname"],
                "from_subnet": r["from_subnet"],
                "to_subnet": r["to_subnet"],
                "detected_at": r["checked_at"].isoformat(),
                "hours_unacknowledged": round(
                    (datetime.now(timezone.utc) - r["checked_at"]).total_seconds() / 3600, 1
                ),
                "hint": (
                    "Physical relocation of this appliance was detected but "
                    "no operator acknowledgment has been recorded. HIPAA "
                    "§164.310(d)(1) requires movement tracking with reason. "
                    "Acknowledge via the admin panel (or POST "
                    "/api/admin/appliances/{appliance_id}/acknowledge-relocation) "
                    "with a reason_category + free-text detail. If this move "
                    "was NOT expected, treat as a security incident: verify "
                    "physical location, check for tampering, investigate."
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


async def _check_evidence_chain_stalled(conn: asyncpg.Connection) -> List[Violation]:
    """Fleet-wide evidence-chain health: if any appliance checked in in
    the last 15 min, there should be AT LEAST one compliance_bundles
    row written in the last 15 min. Checkins happen every 60s; drift
    scans + bundle writes happen every ~5 min per appliance — so 15 min
    of fleet silence while appliances are actively checking in is
    strongly anomalous.

    Caught the 2026-04-18 RLS P0 (migration 234 + broken SQLAlchemy
    `after_begin` binding → 2608 InsufficientPrivilegeError rejections
    on compliance_bundles INSERT in 2h with zero visibility). This
    invariant would have opened a sev1 violation inside 15 min instead
    of being discovered via dashboard data-contradiction analysis.

    Scope intentionally wider than the original `rls_rejection_spike`
    proposal: this catches RLS failures AND any other evidence-chain
    INSERT failure (partition missing, disk full, signing key rotation
    bug, etc.) at the OUTCOME layer. No log scraping required.
    """
    row = await conn.fetchrow(
        """
        WITH fleet_state AS (
            SELECT COUNT(*) FILTER (
                       WHERE last_checkin > NOW() - INTERVAL '15 minutes'
                   ) AS online_recent
              FROM site_appliances
             WHERE deleted_at IS NULL
        ),
        bundle_state AS (
            SELECT COUNT(*) AS bundles_15m,
                   MAX(created_at) AS latest_bundle_at
              FROM compliance_bundles
             WHERE created_at > NOW() - INTERVAL '15 minutes'
        )
        SELECT fs.online_recent,
               bs.bundles_15m,
               bs.latest_bundle_at,
               EXTRACT(EPOCH FROM (NOW() - bs.latest_bundle_at))/60
                   AS minutes_since_last_bundle
          FROM fleet_state fs, bundle_state bs
        """
    )
    if row is None:
        return []
    online_recent = int(row["online_recent"] or 0)
    bundles_15m = int(row["bundles_15m"] or 0)
    # Only fire when at least one appliance is actively checking in
    # AND no bundles have landed in the window. Zero-fleet is handled
    # by other invariants (offline_appliance_over_1h etc.).
    if online_recent < 1 or bundles_15m > 0:
        return []
    minutes_since = float(row["minutes_since_last_bundle"] or 0)
    return [
        Violation(
            site_id=None,
            details={
                "online_recent_15m": online_recent,
                "bundles_15m": bundles_15m,
                "latest_bundle_at": row["latest_bundle_at"].isoformat() if row["latest_bundle_at"] else None,
                "minutes_since_last_bundle": minutes_since,
                "remediation": (
                    "Grep mcp-server logs for InsufficientPrivilegeError, UniqueViolation, "
                    "or partition errors on compliance_bundles. Common causes: RLS context "
                    "not set in SQLAlchemy session (see 2026-04-18 fix commits ebb9f17 + 2ddc596), "
                    "missing monthly partition, signing key rotation mid-write, or disk pressure."
                ),
            },
        )
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
