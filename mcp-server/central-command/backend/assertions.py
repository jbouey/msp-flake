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
import os
import logging
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Tuple
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


def _version_tuple(v: Optional[str]) -> Tuple[int, ...]:
    """Parse 'X.Y.Z' (optionally 'v'-prefixed, optionally with a '-rc1' suffix)
    into an int tuple for stable comparison. Non-numeric segments clamp to 0 so
    we can't crash on unexpected formats. Returns (0,) on empty/None so
    unparseable versions sort as lowest."""
    if not v:
        return (0,)
    clean = v.lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    parts: List[int] = []
    for p in clean.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts) if parts else (0,)


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
    """site_appliances.agent_version is BEHIND the most-recent successfully-
    completed update_daemon order for the site. "Behind" (running < expected)
    is the real lag — the completion ACK lied (pre-0.4.3 daemon bug) or a
    rollback fired silently. Running AHEAD of expected is NOT a lag: it means
    a newer update completed without the server recording `status=completed`,
    which is a fleet_orders reporting hygiene gap, not a fleet health SEV1.

    Bounded to the last 30d of completed orders so an ancient 0.3.91 record
    can't anchor "expected" forever on a fleet that's moved on to 0.4.x.
    """
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
               AND created_at > NOW() - INTERVAL '30 days'
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
    violations: List[Violation] = []
    for r in rows:
        running = _version_tuple(r["agent_version"])
        expected = _version_tuple(r["expected_version"])
        if running >= expected:
            continue
        violations.append(
            Violation(
                site_id=r["site_id"],
                details={
                    "mac_address": r["mac_address"],
                    "hostname": r["hostname"],
                    "running_version": r["agent_version"],
                    "expected_version": r["expected_version"],
                },
            )
        )
    return violations


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
    the operator needs to know.

    Session 210-B 2026-04-24: discovered_devices does NOT have a
    UNIQUE(mac_address, site_id). Multiple rows per pair are normal
    (each scan can insert a new row). The freshness check must look
    at MAX(last_seen_at) per (MAC, site_id) — joining the raw table
    multiplies violations 1-per-stale-row even when a fresh row
    exists for the same MAC.
    """
    rows = await conn.fetch(
        """
        WITH dd_freshest AS (
            SELECT LOWER(mac_address) AS mac, site_id,
                   MAX(last_seen_at) AS last_seen_at
              FROM discovered_devices
             GROUP BY LOWER(mac_address), site_id
        )
        SELECT sa.site_id, sa.mac_address, sa.hostname,
               EXTRACT(EPOCH FROM (NOW() - dd.last_seen_at))/3600 AS hours_stale
          FROM site_appliances sa
     LEFT JOIN dd_freshest dd
            ON dd.mac = LOWER(sa.mac_address)
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


async def _check_compliance_bundles_trigger_disabled(conn: asyncpg.Connection) -> List[Violation]:
    """Sev1 — `compliance_bundles_no_delete` trigger MUST be in
    `ENABLE ALWAYS` state (`tgenabled='A'`).

    Phase 1 multi-tenant audit F-P1-3 (2026-05-09): adversarial
    cleanup tests sometimes need to bypass the no-delete trigger
    via `ALTER TABLE ... DISABLE TRIGGER`. If a test crashes
    mid-execution OR an operator forgets to re-enable, the chain-
    of-custody integrity guard goes silent and bulk-DELETEs become
    possible without anyone noticing.

    This invariant fires sev1 if `compliance_bundles_no_delete`
    trigger is in any state other than 'A' (ALWAYS) on the parent
    partitioned table OR any of its partitions. 'D' (disabled) is
    explicitly NOT allowed — the only legitimate state is ALWAYS.

    The cleanup convention going forward (encoded in
    audit/multi-tenant-phase1-concurrent-write-stress-2026-05-09.md):
    after any synthetic-data injection that requires DISABLE,
    operators MUST `ALTER TABLE ... ENABLE ALWAYS TRIGGER` before
    test exit. This invariant is the runtime defense if that
    discipline ever slips.
    """
    rows = await conn.fetch(
        """
        SELECT n.nspname AS schema_name,
               c.relname AS table_name,
               t.tgname AS trigger_name,
               t.tgenabled::text AS state
          FROM pg_trigger t
          JOIN pg_class c ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE t.tgname = 'compliance_bundles_no_delete'
           AND NOT t.tgisinternal
           AND t.tgenabled <> 'A'
        """
    )
    return [
        Violation(
            site_id=None,
            details={
                "schema": r["schema_name"],
                "table": r["table_name"],
                "trigger": r["trigger_name"],
                "tgenabled_state": r["state"],
                "interpretation": (
                    f"`{r['schema_name']}.{r['table_name']}.{r['trigger_name']}` "
                    f"is in tgenabled='{r['state']}' (expected 'A' = ALWAYS). "
                    f"Chain-of-custody integrity guard is degraded. Run: "
                    f"ALTER TABLE {r['schema_name']}.{r['table_name']} "
                    f"ENABLE ALWAYS TRIGGER {r['trigger_name']};"
                ),
            },
        )
        for r in rows
    ]


async def _check_db_baseline_guc_drift(conn: asyncpg.Connection) -> List[Violation]:
    """Sev2 — load-bearing Postgres GUC defaults must match baseline.

    Phase 1 multi-tenant audit F-P1-4 (2026-05-09): the substrate
    relies on `app.is_admin` defaulting to 'false' (mig 234 tenant
    safety) and `app.current_tenant`/`app.current_org`/
    `app.current_partner_id` defaulting to '' (empty = no tenant).
    If any of these drift to 'true' / non-empty, RLS posture
    silently flips to permissive without anyone noticing.

    Watches the 4 load-bearing GUCs on the database role. Fires
    sev2 if any has drifted from the baseline.
    """
    BASELINE_GUCS = {
        "app.is_admin": "false",
        "app.current_tenant": "",
        "app.current_org": "",
        "app.current_partner_id": "",
    }
    out: List[Violation] = []
    # Round-2 audit P0-RT2-B fix (2026-05-09): the prior version
    # called `current_setting()` against the SESSION GUC. The
    # `admin_connection` caller already SET LOCAL app.is_admin='true'
    # to bypass RLS — so this invariant ALWAYS fired sev2 false-
    # positive against its own caller's setting. Switched to reading
    # the DATABASE-ROLE baseline via `pg_db_role_setting`, which is
    # the actual ALTER DATABASE / ALTER ROLE persistent setting.
    # If THAT drifts from baseline, RLS posture has actually flipped.
    role_settings = await conn.fetch(
        """
        SELECT unnest(setconfig) AS kv
          FROM pg_db_role_setting drs
          JOIN pg_database d ON d.oid = drs.setdatabase
         WHERE d.datname = current_database()
           AND drs.setrole = 0  -- 0 = database-wide setting
        """
    )
    db_role_gucs: Dict[str, str] = {}
    for row in role_settings:
        kv = row["kv"]
        if "=" in kv:
            key, _, val = kv.partition("=")
            db_role_gucs[key.strip()] = val.strip()

    for guc, expected in BASELINE_GUCS.items():
        # If the DB-role setting doesn't exist, the system default
        # applies (which IS the baseline by construction — mig 234).
        actual = db_role_gucs.get(guc)
        if actual is None:
            continue
        if actual != expected:
            out.append(
                Violation(
                    site_id=None,
                    details={
                        "guc": guc,
                        "expected": expected,
                        "actual": actual,
                        "interpretation": (
                            f"GUC `{guc}` is set to `{actual!r}` (expected "
                            f"`{expected!r}`). RLS posture has drifted from "
                            f"the tenant-safety baseline (mig 234). "
                            f"Investigate which migration / hotfix set this; "
                            f"if intentional, document + add to BASELINE_GUCS."
                        ),
                    },
                )
            )
    return out


async def _check_substrate_sla_breach(conn: asyncpg.Connection) -> List[Violation]:
    """Sev2 — META — a substrate invariant has been open beyond its SLA.

    The 2026-05-08 audit (audit/coach-e2e-attestation-audit-2026-05-08.md)
    found that 4 substrate violations had been open for a cumulative
    >32,500 minutes (>22 days). The engine was healthy; the response
    loop was not. CLAUDE.md feedback memory `feedback_substrate_*`
    captures the principle: the engine catches drift in <60s, but
    that signal is wasted if no one acts on it.

    Per-severity SLA (process-defined; tunable):
      sev1   ≤  4h  — alert if open longer
      sev2   ≤ 24h  — alert if open longer
      sev3   ≤ 30d  — alert if open longer (informational class
                       like pre_mig175_privileged_unattested is
                       intentionally long-open by design; sev3 SLA
                       is generous)

    This meta-invariant fires sev2 when ANY non-meta sev1/sev2 row
    has been open beyond its SLA. The intentionally-long-open
    informational invariants (`pre_mig175_privileged_unattested` —
    sev3 disclosure surface) are excluded by name; if more get
    added, they get added to the carve-out below.

    The meta-invariant DOES NOT fire on itself (would create a
    feedback loop with no termination).
    """
    # Carve-out: sev3 informational disclosure-surface invariants
    # that are intentionally long-open by design. Adding to this
    # list requires explicit round-table sign-off.
    LONG_OPEN_BY_DESIGN = (
        "pre_mig175_privileged_unattested",
        "l2_recurrence_partitioning_disclosed",  # sev3 disclosure surface (Session 220 RT-P1)
        "substrate_sla_breach",  # never alert on self
    )

    rows = await conn.fetch(
        """
        SELECT invariant_name, severity,
               EXTRACT(EPOCH FROM (NOW() - detected_at))/60 AS open_minutes,
               COUNT(*) OVER (PARTITION BY invariant_name) AS row_count
          FROM substrate_violations
         WHERE resolved_at IS NULL
           AND invariant_name <> ALL($1::text[])
         ORDER BY detected_at ASC
        """,
        list(LONG_OPEN_BY_DESIGN),
    )
    out: List[Violation] = []
    for r in rows:
        sev = r["severity"]
        open_min = float(r["open_minutes"])
        # Per-severity SLA in minutes
        sla_min = {"sev1": 240, "sev2": 1440, "sev3": 43200}.get(sev, 1440)
        if open_min <= sla_min:
            continue
        out.append(
            Violation(
                site_id=None,
                details={
                    "breached_invariant": r["invariant_name"],
                    "breached_severity": sev,
                    "open_minutes": round(open_min, 1),
                    "open_hours": round(open_min / 60, 1),
                    "sla_minutes": sla_min,
                    "interpretation": (
                        f"Substrate invariant `{r['invariant_name']}` "
                        f"({sev}) has been open for "
                        f"{round(open_min/60, 1)}h, exceeding its "
                        f"{sla_min}-minute SLA. The engine is firing "
                        f"correctly; the response loop is not. Read "
                        f"the runbook for the named invariant and "
                        f"act, OR if the invariant is intentionally "
                        f"long-open by design, add it to "
                        f"_check_substrate_sla_breach.LONG_OPEN_BY_DESIGN "
                        f"with round-table sign-off."
                    ),
                },
            )
        )
    return out


async def _check_pre_mig175_privileged_unattested(conn: asyncpg.Connection) -> List[Violation]:
    """Sev3 — INFORMATIONAL — pre-mig-175 privileged orders without attestation.

    The Privileged-Access Chain-of-Custody rule (CLAUDE.md INVIOLABLE)
    requires every privileged fleet_orders row to carry
    `parameters->>'attestation_bundle_id'` linking to a real
    compliance_bundles row with check_type='privileged_access'.
    Migration 175 added a pre-INSERT trigger
    `trg_enforce_privileged_chain` that REJECTS any new violation.

    Three rows on `north-valley-branch-2` pre-date the trigger
    (by 49h, 2h21m, and 2h21m respectively). The 2026-05-08
    round-table chose disclosure over backfill (forgery risk);
    a SECURITY_ADVISORY ships in `docs/security/`. This invariant
    keeps the gap visible on the substrate dashboard so future
    operators see it without archaeology.

    Sev3 because: (1) prevention is in place — zero new violations
    possible; (2) the affected orders never executed (terminal
    states `expired`/`cancelled`); (3) disclosure is the
    resolution, not auto-heal. The invariant exists for OPERATOR
    VISIBILITY, not for action.

    Resolves when: the rows are deleted (NEVER — fleet_orders is
    in the audit-class set) OR a future migration explicitly
    grandfathers them in (requires round-table approval).
    """
    rows = await conn.fetch(
        """
        SELECT id::text AS order_id,
               order_type,
               created_at,
               status
          FROM fleet_orders
         WHERE order_type IN (
                 'enable_emergency_access',
                 'disable_emergency_access',
                 'bulk_remediation',
                 'signing_key_rotation'
             )
           AND (parameters->>'attestation_bundle_id') IS NULL
         ORDER BY created_at ASC
         LIMIT 50
        """
    )
    return [
        Violation(
            site_id=None,  # Pre-mig-175 rows lack a site anchor; the
                            # advisory file names the affected sites.
            details={
                "order_id": r["order_id"],
                "order_type": r["order_type"],
                "created_at": r["created_at"].isoformat(),
                "status": r["status"],
                "advisory_ref": (
                    "docs/security/"
                    "SECURITY_ADVISORY_2026-04-13_PRIVILEGED_PRE_TRIGGER.md"
                ),
                "interpretation": (
                    f"Pre-mig-175 privileged fleet_orders row "
                    f"`{r['order_id']}` ({r['order_type']}, status="
                    f"{r['status']}, created {r['created_at'].isoformat()}) "
                    f"carries no attestation_bundle_id. This row "
                    f"pre-dates the chain-of-custody trigger and is "
                    f"covered by public security advisory "
                    f"OSIRIS-2026-04-13-PRIVILEGED-PRE-TRIGGER. INFORMATIONAL: "
                    f"new violations are blocked by trg_enforce_privileged_chain."
                ),
            },
        )
        for r in rows
    ]


async def _check_merkle_batch_stalled(conn: asyncpg.Connection) -> List[Violation]:
    """Sev1 — Merkle batch worker has not anchored evidence in >6h.

    Background: `compliance_bundles` rows transition `ots_status` like:

        'pending' (just inserted) → 'batching' (queued for next Merkle run)
                                  → 'pending' on the proof  (root submitted to OTS)
                                  → 'anchored' (Bitcoin block confirms)

    `_merkle_batch_loop` runs hourly and walks every site with rows
    in `ots_status='batching'` to build a Merkle tree + submit the
    root to OpenTimestamps.

    The 2026-05-08 audit (audit/coach-e2e-attestation-audit-2026-05-08.md
    F-P0-1) found 2,669 rows pinned in `ots_status='batching'` for 18
    days on the only paying customer site — the loop was RLS-blind
    (PgBouncer-routed asyncpg pool inherits app.is_admin='false', mig
    234 default) and silently iterated over zero sites. The structural
    fix shipped commit 7db2faab (admin_transaction). This invariant is
    the runtime-defense layer: if the structural fix ever regresses
    OR a different fault stalls the batcher, fire sev1 in 60s — not
    18 days.

    Threshold: oldest `ots_status='batching'` row older than 6h is
    sev1 (loop runs hourly; 6h = ~6 missed cycles). Per-site row so
    operators see WHICH site is stuck.
    """
    rows = await conn.fetch(
        """
        SELECT site_id,
               COUNT(*) AS stuck_count,
               MIN(created_at) AS oldest_stuck_at,
               EXTRACT(EPOCH FROM (NOW() - MIN(created_at))) / 3600 AS oldest_hours
          FROM compliance_bundles
         WHERE ots_status = 'batching'
           AND created_at < NOW() - INTERVAL '6 hours'
         GROUP BY site_id
         ORDER BY oldest_stuck_at ASC
         LIMIT 50
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "stuck_count": int(r["stuck_count"]),
                "oldest_stuck_at": r["oldest_stuck_at"].isoformat(),
                "oldest_hours": round(float(r["oldest_hours"]), 1),
                "interpretation": (
                    f"Site `{r['site_id']}` has {int(r['stuck_count'])} "
                    f"compliance_bundles rows pinned at "
                    f"ots_status='batching' for "
                    f"{round(float(r['oldest_hours']), 1)}+ hours. The "
                    f"hourly _merkle_batch_loop has not transitioned "
                    f"these rows toward Bitcoin OTS anchoring. §164.312"
                    f"(c)(1) integrity controls + the customer-facing "
                    f"tamper-evidence promise depend on this loop firing."
                ),
            },
        )
        for r in rows
    ]


async def _check_compliance_packets_stalled(conn: asyncpg.Connection) -> List[Violation]:
    """Sev1 — HIPAA monthly attestations missing (Block 4 P1).

    `compliance_packets` (mig 141) holds monthly compliance attestations
    auto-generated by `_compliance_packet_loop` for HIPAA §164.316(b)(2)(i)
    6-year retention. Per CLAUDE.md, missing months = silent compliance
    gap that auditors will catch.

    This invariant fires when:
    - A site emitted compliance_bundles in the prior completed month
      (proving it was operationally active), AND
    - No compliance_packets row exists for that site+month+year,
      framework='hipaa', AND
    - We are >24h past the start of the new month (auto-gen grace
      window: the loop runs hourly, 24h is generous).

    Sev1 because: missing months ARE the audit failure. Operator must
    investigate within the workday + manually backfill via the admin
    endpoint if needed.
    """
    # Phase 1 multi-tenant audit P2 fix (F-P1-2, 2026-05-09): the
    # prior `EXTRACT(YEAR/MONTH FROM cb.created_at)` per-row predicate
    # forces a sequential scan + can't use partition pruning on the
    # monthly compliance_bundles partitions. Profiled at 162ms with
    # 251K bundles, projects to ~5s at N=20. Range comparison
    # `created_at >= start AND created_at < end` IS sargable + lets
    # PG partition pruning eliminate every non-matching partition
    # entirely + uses the (site_id, created_at) index.
    rows = await conn.fetch(
        """
        WITH prior_month AS (
            SELECT date_trunc('month', NOW() - INTERVAL '1 month') AS prior_start,
                   date_trunc('month', NOW())                       AS curr_month_start,
                   EXTRACT(YEAR FROM (NOW() - INTERVAL '1 month'))::int AS y,
                   EXTRACT(MONTH FROM (NOW() - INTERVAL '1 month'))::int AS m
        ),
        active_sites AS (
            SELECT DISTINCT cb.site_id
              FROM compliance_bundles cb, prior_month pm
             WHERE cb.created_at >= pm.prior_start
               AND cb.created_at <  pm.curr_month_start
        )
        SELECT a.site_id, p.y AS year, p.m AS month
          FROM active_sites a, prior_month p
         WHERE NOT EXISTS (
             SELECT 1 FROM compliance_packets cp
              WHERE cp.site_id = a.site_id
                AND cp.year = p.y
                AND cp.month = p.m
                AND cp.framework = 'hipaa'
         )
           -- 24h grace window from start of current month
           AND NOW() > p.curr_month_start + INTERVAL '24 hours'
         ORDER BY a.site_id
         LIMIT 50
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "year": int(r["year"]),
                "month": int(r["month"]),
                "framework": "hipaa",
                "interpretation": (
                    f"HIPAA monthly compliance packet for site "
                    f"`{r['site_id']}` is missing for {r['year']}-"
                    f"{int(r['month']):02d}. The site emitted bundles "
                    f"that month so it was operationally active; the "
                    f"auto-gen path failed silently. §164.316(b)(2)(i) "
                    f"6-year retention class — auditor-visible gap."
                ),
            },
        )
        for r in rows
    ]


async def _check_email_dlq_growing(conn: asyncpg.Connection) -> List[Violation]:
    """Sev2 — outbound email infrastructure failing silently.

    Maya note 2026-05-04 from the partner-portal consistency audit.
    `email_send_failures` (mig 272) is the DLQ written by
    `_record_email_dlq_failure` when `_send_smtp_with_retry` exhausts
    its 3-retry budget. Pre-fix: final-failure email sends were only
    visible in stdout — operators learned about SMTP outages or auth
    breaks via "users complaining no email arrived".

    Threshold: > 5 unresolved rows in 24h fires sev2. Conservative
    initial calibration; tune after the table populates with real
    traffic patterns. Same shape as flywheel-related sev2 invariants
    (rolling-window count + GROUP BY label so the operator sees
    which send-class is failing).

    The invariant is label-grouped so a healthy alert-digest path
    doesn't get masked by a stuck operator-alert path; operator
    sees per-pipeline counts.
    """
    rows = await conn.fetch(
        """
        SELECT label, COUNT(*) AS n, MAX(failed_at) AS last_failed
          FROM email_send_failures
         WHERE resolved_at IS NULL
           AND failed_at > NOW() - INTERVAL '24 hours'
         GROUP BY label
        HAVING COUNT(*) > 5
         ORDER BY n DESC
         LIMIT 10
        """
    )
    return [
        Violation(
            site_id=None,
            details={
                "label": r["label"],
                "unresolved_count_24h": int(r["n"]),
                "last_failed_at": r["last_failed"].isoformat()
                                  if r["last_failed"] else None,
                "interpretation": (
                    f"{int(r['n'])} email-send failures in the last 24h "
                    f"on the `{r['label']}` pipeline have not been "
                    f"resolved. SMTP outage / auth break / recipient "
                    f"bounce class. Check SMTP_USER + SMTP_PASSWORD env, "
                    f"verify mail.privateemail.com TLS handshake, then "
                    f"manually resolve rows once root cause is fixed."
                ),
            },
        )
        for r in rows
    ]


async def _check_cross_org_relocate_chain_orphan(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Sev1 — a site shows prior_client_org_id set but no completed
    cross_org_site_relocate_requests row attests the move.

    RT21 (2026-05-05) Steve threat model item 4: the substrate must
    detect when a cross-org move bypassed the proper flow. The proper
    flow writes:
      - sites.prior_client_org_id (set via execute_relocate)
      - cross_org_site_relocate_requests row in status=completed
        (recording all 6 attestation_bundle_ids across the lifecycle)
    A direct UPDATE on sites.client_org_id (DBA shortcut, accidental
    backfill, etc.) sets prior but leaves no relocate row — chain-of-
    custody gap. Sev1 because §164.528 disclosure-accounting integrity
    is on the line.

    The check is read-only: for every site with prior_client_org_id
    set, look for a completed relocate row matching site_id +
    source_org_id (= prior) + target_org_id (= current). Any site
    failing the lookup is a violation.
    """
    rows = await conn.fetch(
        """
        SELECT s.site_id,
               s.client_org_id::text AS current_org_id,
               s.prior_client_org_id::text AS prior_org_id
          FROM sites s  -- # noqa: synthetic-allowlisted — substrate engine MUST tick on synthetic site (Task #66 B1); rows segregate via substrate_violations.synthetic at INSERT time
         WHERE s.prior_client_org_id IS NOT NULL
        """
    )
    violations: List[Violation] = []
    for row in rows:
        match = await conn.fetchval(
            """
            SELECT 1
              FROM cross_org_site_relocate_requests
             WHERE site_id = $1
               AND source_org_id::text = $2
               AND target_org_id::text = $3
               AND status = 'completed'
             LIMIT 1
            """,
            row["site_id"],
            row["prior_org_id"],
            row["current_org_id"],
        )
        if not match:
            violations.append(
                Violation(
                    site_id=row["site_id"],
                    details={
                        "prior_org_id": str(row["prior_org_id"]),
                        "current_org_id": str(row["current_org_id"]),
                        "interpretation": (
                            f"site has prior_client_org_id={row['prior_org_id']} "
                            f"and current client_org_id={row['current_org_id']} "
                            f"but no completed cross_org_site_relocate_requests "
                            f"row attests the move — chain-of-custody gap"
                        ),
                    },
                )
            )
    return violations


async def _check_fleet_order_fanout_partial_completion(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Sev2 — a fleet_cli --all-at-site fan-out (one
    privileged_access_attestation bundle covering N fleet_orders)
    has at least one target appliance unacked > 6h post-issuance.

    Task #128 closure (#118 Gate B P2-1, multi-device fan-out
    follow-up). Per #118 (multi-device P1-2): --all-at-site creates
    ONE bundle + N orders via the cross-link UPDATE pattern
    (admin_audit_log.details->>'fleet_order_ids' jsonb array). At
    fan-out scale (10-20 appliances), some target appliances may
    be offline → fleet_order never acked → silent partial
    completion. The operator sees the fan-out as 'issued' without
    seeing 'K-of-N never executed'.

    Sev2 per Gate A 2026-05-16 §P1-2 + Gate B 2026-05-16 P1-A: the
    cited sibling is `appliance_moved_unack` (sev2 at :2779) — the
    silent-fail-after-issuance class. Justification stands on first
    principles: chain-of-trust-affected privileged fan-out merits
    operator-attention tier; sev3 falls below panel surfacing.

    Window: 6h–168h (7d) per Gate B P1-B — original 6h–24h band
    silently dropped Friday-evening orphans that would surface Monday
    triage. PRIVILEGED_ACCESS_% audit rows are low-volume (operator-
    initiated fan-outs, ~few per day), so the action LIKE filter
    bounds the scan safely at 7d.

    Algorithm:
      - Scan admin_audit_log narrowed to action LIKE
        'PRIVILEGED_ACCESS_%' over last 24h (Gate A P1-1: action
        filter avoids the COUNT(*)-class timeout on the large
        audit table)
      - Filter rows with details ? 'fleet_order_ids' AND
        jsonb_array_length > 1 (fan-out shape only)
      - Filter rows older than NOW() - 6h (Gate A: matches daemon
        heartbeat cadence + mig 161 retry window)
      - jsonb_array_elements_text to unpack the fleet_order_ids
        array into per-order rows
      - LEFT JOIN fleet_order_completions ON fleet_order_id
        (composite PK is (fleet_order_id, appliance_id) — Gate A
        P0-1 fix: use `WHERE foc.fleet_order_id IS NULL` for
        unmatched, NOT `foc.id IS NULL` because there's no `id`
        column)
      - Gate A P0-3 fix: 'skipped' status (appliance at
        skip_version) is a successful completion — would
        false-positive on every update_daemon fan-out to
        already-updated boxes if omitted. NULL OR explicit-skip
        excluded.
      - LIMIT 100 to bound log spam under widespread offline
        appliances

    Cross-link gap caveat: fleet_cli.py:543 writes the
    fleet_order_ids array via a best-effort try/except. If that
    UPDATE fails, the invariant is blind to that fan-out entirely.
    Tracked as separate followup task.
    """
    rows = await conn.fetch(
        """
        WITH fan_out_orders AS (
            SELECT al.details->>'bundle_id'           AS bundle_id,
                   al.action                          AS audit_action,
                   al.created_at                      AS issued_at,
                   jsonb_array_elements_text(al.details->'fleet_order_ids') AS fleet_order_id_text,
                   jsonb_array_length(al.details->'fleet_order_ids') AS fan_out_size
              FROM admin_audit_log al
             WHERE al.action LIKE 'PRIVILEGED_ACCESS_%'
               AND al.created_at > NOW() - INTERVAL '168 hours'
               AND al.created_at < NOW() - INTERVAL '6 hours'
               AND al.details ? 'fleet_order_ids'
               AND jsonb_array_length(al.details->'fleet_order_ids') > 1
        )
        SELECT fob.bundle_id,
               fob.audit_action,
               fob.issued_at,
               fob.fan_out_size,
               fob.fleet_order_id_text,
               EXTRACT(EPOCH FROM (NOW() - fob.issued_at))/3600 AS hours_unacked
          FROM fan_out_orders fob
          LEFT JOIN fleet_order_completions foc
            ON foc.fleet_order_id::text = fob.fleet_order_id_text
           AND foc.status IN ('completed', 'acknowledged', 'skipped')
         WHERE foc.fleet_order_id IS NULL
         ORDER BY fob.issued_at DESC
         LIMIT 100
        """
    )
    return [
        Violation(
            site_id=None,
            details={
                "bundle_id": r["bundle_id"],
                "audit_action": r["audit_action"],
                "issued_at": r["issued_at"].isoformat() if r["issued_at"] else None,
                "fan_out_size": int(r["fan_out_size"]),
                "fleet_order_id": r["fleet_order_id_text"],
                "hours_unacked": float(r["hours_unacked"] or 0),
                "interpretation": (
                    f"fleet_order {r['fleet_order_id_text']!r} from "
                    f"fan-out bundle {r['bundle_id']!r} (size "
                    f"{int(r['fan_out_size'])}) issued "
                    f"{float(r['hours_unacked'] or 0):.1f}h ago has "
                    f"NO completion (completed/acknowledged/skipped) "
                    f"in fleet_order_completions. Likely target "
                    f"appliance offline > 6h, daemon not pulling "
                    f"orders, or fleet_order_completion writer broken."
                ),
            },
        )
        for r in rows
    ]


async def _check_bundle_chain_position_gap(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Sev1 — per-site evidence-chain integrity gate. Detects
    non-contiguous `chain_position` values within compliance_bundles
    for a single site_id (last 24h window).

    Task #117 prerequisite (multi-device P1-1, Gate A 2026-05-16):
    `evidence_chain.py::create_compliance_bundle` uses
    `pg_advisory_xact_lock(hashtext(site_id), hashtext('attest'))` to
    serialize chain writes per-site. The contention load test (Sub-
    commits B-D) exercises 20-way concurrent writers against a single
    test site — proving the lock works requires the substrate to
    catch any gap. Pre-#117 NO per-site chain-integrity invariant
    existed (only `cross_org_relocate_chain_orphan`, which is a
    completely different shape).

    Why sev1: chain-position gaps are the most direct form of
    chain-of-custody corruption. Two bundles with the same prev_hash
    (or any sequence break) means the chain walks differently across
    two consecutive auditor-kit downloads — kit hash flips between
    downloads, which is the visible tamper-evidence violation. Same
    severity class as `load_test_marker_in_compliance_bundles` +
    `cross_org_relocate_chain_orphan`.

    Algorithm:
      - PARTITION BY site_id (all 6 pg_advisory_xact_lock callsites
        lock per-site; per-check-type partitioning would mis-attribute)
      - WHERE created_at > NOW() - INTERVAL '24 hours' (partition
        pruning on the monthly-partitioned compliance_bundles table)
      - LAG(chain_position) over (PARTITION BY site_id ORDER BY
        chain_position) — gap exists when
        `chain_position - prev_chain_position > 1`
      - Genesis bundle (LAG NULL) is naturally excluded by the
        arithmetic predicate
      - LIMIT 100 to bound log spam under widespread corruption

    Carve-outs: NONE today. Historical mig 043 bundles predate the
    advisory-lock fix (Session 207) but are outside the 24h window.
    OTS retro-anchoring doesn't write new compliance_bundles rows
    (only stamps existing rows via UPDATE), so chain_position is
    untouched. cross_org_relocate copies bundles into the target
    site's chain via a separate flow that preserves chain_position
    contiguity (mig 280).
    """
    rows = await conn.fetch(
        """
        WITH ordered AS (
            SELECT site_id,
                   chain_position,
                   bundle_id,
                   created_at,
                   LAG(chain_position) OVER (
                       PARTITION BY site_id
                       ORDER BY chain_position
                   ) AS prev_chain_position
              FROM compliance_bundles
             WHERE created_at > NOW() - INTERVAL '24 hours'
        )
        SELECT site_id,
               chain_position,
               prev_chain_position,
               bundle_id,
               created_at,
               chain_position - prev_chain_position AS gap_size
          FROM ordered
         WHERE prev_chain_position IS NOT NULL
           AND chain_position - prev_chain_position > 1
         ORDER BY site_id, chain_position
         LIMIT 100
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "chain_position": int(r["chain_position"]),
                "prev_chain_position": int(r["prev_chain_position"]),
                "gap_size": int(r["gap_size"]),
                "bundle_id": r["bundle_id"],
                "created_at": (
                    r["created_at"].isoformat() if r["created_at"] else None
                ),
                "interpretation": (
                    f"chain_position gap of {int(r['gap_size'])} at "
                    f"site_id={r['site_id']!r} bundle_id="
                    f"{r['bundle_id']!r}. Expected next position "
                    f"{int(r['prev_chain_position']) + 1}, got "
                    f"{int(r['chain_position'])}. Indicates either "
                    f"(a) the per-site advisory lock failed to "
                    f"serialize a concurrent write, OR (b) a row was "
                    f"deleted/skipped post-INSERT. Walk back from the "
                    f"gap and quarantine the affected segment."
                ),
            },
        )
        for r in rows
    ]


async def _check_cross_org_relocate_baa_receipt_unauthorized(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Sev1 — counsel approval contingency #2 (2026-05-06): every
    completed relocate's target org MUST have had a non-NULL
    `baa_relocate_receipt_signature_id` (or addendum_signature_id)
    AT THE TIME of the execute. A row that completed without that
    column populated is a bypass that violates outside-counsel's
    approval condition.

    Engineering check at the endpoint layer (`_check_target_org_baa`)
    refuses to advance target-accept without the signature. This
    substrate invariant catches the case where:
      - the column was populated at target-accept time
      - then unset / NULLed by a subsequent admin action
      - the relocate completed
    OR a code-path bypass that skipped the receipt check.

    The signature must STAY on the org's row after relocate (so
    auditors walking back across the boundary can confirm the
    receipt-authorization was in force at execute time). If contracts
    later rolls back receipt-authorization for an org, that's a
    business decision but should NOT erase the historical signature_id
    on a completed relocate's target org.

    The check is read-only: for each completed relocate, look at
    the target org's current baa_relocate_receipt_signature_id +
    addendum_signature_id. If both NULL, that's a sev1 violation.
    """
    rows = await conn.fetch(
        """
        SELECT r.id AS relocate_id,
               r.site_id,
               r.target_org_id::text AS target_org_id,
               r.executed_at,
               co.baa_relocate_receipt_signature_id,
               co.baa_relocate_receipt_addendum_signature_id
          FROM cross_org_site_relocate_requests r
          JOIN client_orgs co ON co.id = r.target_org_id
         WHERE r.status = 'completed'
        """
    )
    violations: List[Violation] = []
    for row in rows:
        has_auth = (
            row["baa_relocate_receipt_signature_id"] is not None
            or row["baa_relocate_receipt_addendum_signature_id"] is not None
        )
        if not has_auth:
            violations.append(
                Violation(
                    site_id=row["site_id"],
                    details={
                        "relocate_id": str(row["relocate_id"]),
                        "executed_at": row["executed_at"].isoformat() if row["executed_at"] else None,
                        "target_org_id": str(row["target_org_id"]),
                        "interpretation": (
                            f"completed relocate {row['relocate_id']} "
                            f"executed_at={row['executed_at']} has target "
                            f"org {row['target_org_id']} with NULL "
                            f"baa_relocate_receipt_signature_id — counsel "
                            f"approval condition #2 violated; the move "
                            f"happened without recorded receipt-authorization"
                        ),
                    },
                )
            )
    return violations


async def _check_unbridged_telemetry_runbook_ids(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Sev2 — execution_telemetry rows whose runbook_id has no
    corresponding agent_runbook_id in the runbooks table.

    RT-DM Issue #1 (2026-05-06). Migration 284 backfilled the bridge
    for known L1-* IDs; this invariant catches new agent rules that
    ship without a corresponding runbooks row. Without it, the
    bridge silently drifts and per-runbook execution counts go to
    0 again.

    The check is read-only: distinct unbridged runbook_ids in
    execution_telemetry from the last 7 days. 7-day window keeps
    the violation list bounded for a long-running drift; once a
    new bridge row is added the violation clears.
    """
    rows = await conn.fetch(
        """
        SELECT DISTINCT et.runbook_id
          FROM execution_telemetry et
         WHERE et.runbook_id IS NOT NULL
           AND et.runbook_id <> ''
           AND et.created_at > NOW() - INTERVAL '7 days'
           AND NOT EXISTS (
               SELECT 1 FROM runbooks r
                WHERE r.agent_runbook_id = et.runbook_id
                   OR r.runbook_id = et.runbook_id
           )
         LIMIT 50
        """
    )
    violations: List[Violation] = []
    for row in rows:
        violations.append(
            Violation(
                site_id=None,
                details={
                    "runbook_id": row["runbook_id"],
                    "interpretation": (
                        f"execution_telemetry.runbook_id={row['runbook_id']!r} "
                        f"has no matching runbooks.agent_runbook_id (or "
                        f"runbook_id). Add a bridge row in a migration."
                    ),
                },
            )
        )
    return violations


async def _check_l2_resolution_without_decision_record(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Sev2 — incident with resolution_tier='L2' but no l2_decisions
    row referencing it.

    RT-DM Issue #2 non-consensus hardening (2026-05-06). Every L2
    resolution should be traceable to either a fresh LLM decision
    or a cache-hit reference; an L2-tier resolution with no
    corresponding l2_decisions row is an integrity gap that
    auditors would flag (decision-without-record OR record-without-
    decision both classes are bad).

    The check looks at incidents resolved in the last 7 days with
    tier=L2 — a manageable window for drift detection without
    growing the violation set unboundedly.
    """
    # Maya 2nd-eye fix (2026-05-06): incidents.incident_id is NOT a
    # column (verified — the only references in the codebase were in
    # the original draft of this query + mig 285's view). Single JOIN
    # on `i.id::text = ld.incident_id`.
    rows = await conn.fetch(
        """
        SELECT i.id::text AS incident_pk,
               i.site_id,
               i.resolved_at
          FROM incidents i
         WHERE i.resolution_tier = 'L2'
           AND i.status = 'resolved'
           AND i.resolved_at > NOW() - INTERVAL '7 days'
           AND NOT EXISTS (
               SELECT 1 FROM l2_decisions ld
                WHERE ld.incident_id = i.id::text
           )
         LIMIT 50
        """
    )
    violations: List[Violation] = []
    for row in rows:
        violations.append(
            Violation(
                site_id=row.get("site_id"),
                details={
                    "incident_pk": str(row["incident_pk"]),
                    "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
                    "interpretation": (
                        f"incident id={row['incident_pk']} "
                        f"resolved_at={row['resolved_at']} carries "
                        f"resolution_tier='L2' but no l2_decisions row "
                        f"references it — L2 resolution without LLM record"
                    ),
                },
            )
        )
    return violations


async def _check_l1_resolution_without_remediation_step(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Sev2 — incident with resolution_tier='L1' but no
    incident_remediation_steps row referencing it.

    Session 219 hardening (2026-05-11, sibling of L2-orphan invariant).
    `resolution_tier='L1'` is the customer-facing "auto-healed" label.
    An L1-resolved incident with no relational step is a false claim on
    the audit chain — every PDF + dashboard that counts L1 as "healed"
    is over-stating the platform's remediation rate.

    Prod sample 2026-05-11 (past 30 days): 1131 L1 orphans of 2327 (49%)
    on chaos-lab (north-valley-branch-2). Branch-1 (paying customer)
    has zero resolved incidents in the window — no customer exposure
    during the dark stretch.

    Ground truth: `LEFT JOIN incident_remediation_steps` (relational
    table per mig 137). The `incidents.remediation_history` JSONB column
    was NOT migrated — querying it would false-positive on every row
    since mig 137 ran.

    Dedup: `(site_id, COALESCE(dedup_key, id::text))` so flap-row sites
    don't starve the violation budget (single dedup_key flapping 50×
    surfaces ONCE, not 50×).

    Window: 24 hours. Window function (COUNT OVER PARTITION) runs
    BEFORE the LIMIT clause per Postgres planner — so site_total_orphans
    reflects the full partition count, not the limited surface.
    """
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (i.site_id, COALESCE(i.dedup_key, i.id::text))
               i.site_id,
               i.incident_type,
               i.id::text AS incident_pk,
               i.resolved_at,
               COUNT(*) OVER (PARTITION BY i.site_id) AS site_total_orphans
          FROM incidents i
          LEFT JOIN incident_remediation_steps irs
            ON irs.incident_id = i.id
         WHERE i.resolution_tier = 'L1'
           AND i.status = 'resolved'
           AND i.resolved_at > NOW() - INTERVAL '24 hours'
           AND irs.id IS NULL
         ORDER BY i.site_id, COALESCE(i.dedup_key, i.id::text), i.resolved_at DESC
         LIMIT 50
        """
    )

    total_count = await conn.fetchval(
        """
        SELECT COUNT(*)
          FROM incidents i
          LEFT JOIN incident_remediation_steps irs
            ON irs.incident_id = i.id
         WHERE i.resolution_tier = 'L1'
           AND i.status = 'resolved'
           AND i.resolved_at > NOW() - INTERVAL '24 hours'
           AND irs.id IS NULL
        """
    ) or 0

    violations: List[Violation] = []
    for row in rows:
        violations.append(
            Violation(
                site_id=row.get("site_id"),
                details={
                    "incident_pk": str(row["incident_pk"]),
                    "incident_type": row["incident_type"],
                    "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
                    "site_orphan_count_24h": int(row["site_total_orphans"]),
                    "fleet_orphan_count_24h": int(total_count),
                    "interpretation": (
                        f"incident id={row['incident_pk']} "
                        f"type={row['incident_type']} "
                        f"resolved_at={row['resolved_at']} carries "
                        f"resolution_tier='L1' but no incident_remediation_steps "
                        f"row references it — L1 resolution without runbook execution"
                    ),
                },
            )
        )
    return violations


async def _check_orders_stuck_acknowledged(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Sev2 — orders.status stuck in 'acknowledged' (>30 min) or
    'executing' (>1 hour) without ever reaching a terminal state.

    RT-DM Issue #3 non-consensus hardening (2026-05-06). The
    auto_complete_order_on_telemetry trigger (mig 286) handles the
    nominal happy path; this invariant catches the case where the
    agent's telemetry never reaches the backend (network gap,
    crash, missing order_id metadata) so the dashboard doesn't
    silently show orders as 'in flight' indefinitely.

    The sweep_stuck_orders() function in mig 286 is the auto-fix
    for these; this invariant pages when the sweeper itself is
    not running OR the volume is high enough to indicate a system-
    level issue (not just one-off appliance crashes).
    """
    rows = await conn.fetch(
        """
        SELECT order_id,
               status,
               acknowledged_at,
               appliance_id,
               site_id
          FROM orders
         WHERE (status = 'acknowledged' AND acknowledged_at < NOW() - INTERVAL '30 minutes')
            OR (status = 'executing' AND acknowledged_at < NOW() - INTERVAL '1 hour')
         LIMIT 50
        """
    )
    violations: List[Violation] = []
    for row in rows:
        violations.append(
            Violation(
                site_id=row.get("site_id"),
                details={
                    "order_id": str(row["order_id"]),
                    "status": row["status"],
                    "acknowledged_at": row["acknowledged_at"].isoformat() if row["acknowledged_at"] else None,
                    "interpretation": (
                        f"order order_id={row['order_id']} "
                        f"status={row['status']} "
                        f"acknowledged_at={row['acknowledged_at']} — "
                        f"appliance never reported completion telemetry"
                    ),
                },
            )
        )
    return violations


async def _check_client_portal_zero_evidence_with_data(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Sev2 — RLS misalignment between substrate-owned data and the
    client portal's read view.

    Round-table 2026-05-05 Stage 4
    (.agent/plans/25-client-portal-data-display-roundtable-2026-05-05.md).
    Stage 1 fixed the canonical bug: tenant_middleware.org_connection()
    sets `app.current_tenant=''` so site-RLS-only tables returned 0
    rows under client sessions. The bug was silent for ~months because:
      - Frontend masked 500s as "empty state" via privacy-by-design copy
      - No alarm fires when client portal returns suspiciously-low data

    This invariant catches the regression class: for each org that
    HAS compliance_bundles in the last 7 days, simulate the client-
    portal canonical query under that org's RLS context. If the row
    count drops to zero, fire sev2 — RLS or query change is hiding
    real evidence from the customer's view.

    Implementation note: substrate_assertions_loop runs as the admin
    role (is_admin='true'). To exercise the CLIENT path we need to
    SET LOCAL app.current_org=<id>, app.is_admin='false',
    app.current_tenant='' for the duration of the simulated query —
    matching what org_connection actually does. We restore admin
    after each org check so the rest of the loop's queries continue
    to bypass RLS.
    """
    # Take a small sample of recently-active orgs. Full-scan would be
    # expensive on 1000+-org deployments; sampling 10 most-recently-
    # active is enough to surface a class regression. If the bug is
    # back, we'll see it within minutes on the next loop iteration
    # because EVERY org would be hit.
    candidate_rows = await conn.fetch(
        """
        SELECT s.client_org_id::text AS org_id,
               COUNT(DISTINCT cb.id) AS bundle_count_7d
          FROM compliance_bundles cb
          JOIN sites s ON s.site_id = cb.site_id
         WHERE cb.checked_at > NOW() - INTERVAL '7 days'
           AND s.client_org_id IS NOT NULL
         GROUP BY s.client_org_id
        HAVING COUNT(DISTINCT cb.id) > 0
         ORDER BY MAX(cb.checked_at) DESC
         LIMIT 10
        """
    )

    violations: List[Violation] = []
    for row in candidate_rows:
        org_id = row["org_id"]
        bundle_count_7d = int(row["bundle_count_7d"])

        # Simulate the canonical query under this org's client RLS.
        # Post-Gate-A refactor (2026-05-11): every caller now wraps
        # this function in `admin_transaction(pool)`, so `conn` is
        # ALWAYS inside an outer transaction. `async with
        # conn.transaction()` therefore opens a true SAVEPOINT here —
        # one bad org row rolls back ONLY its SET LOCAL + visible-count
        # query; sibling orgs continue. `SET LOCAL` scopes to the
        # savepoint and is released on context exit (no explicit RESET
        # needed). Round-2 audit P0-RT2-A: prior raw `SAVEPOINT` SQL
        # was raising `NoActiveSQLTransactionError` ~102/hr in prod
        # before the per-assertion admin_transaction wrap was added.
        try:
            async with conn.transaction():
                await conn.execute(
                    f"SET LOCAL app.current_org = '{org_id}'",
                )
                await conn.execute(
                    "SET LOCAL app.is_admin = 'false'",
                )
                await conn.execute(
                    "SET LOCAL app.current_tenant = ''",
                )

                visible_count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM compliance_bundles
                     WHERE site_id IN (
                         SELECT site_id FROM sites
                          WHERE client_org_id::text = current_setting('app.current_org', true)
                     )
                       AND checked_at > NOW() - INTERVAL '7 days'
                    """
                )
                visible_count = int(visible_count or 0)
            # Savepoint committed on with-block exit; SET LOCAL released.

            if visible_count == 0:
                violations.append(
                    Violation(
                        site_id=None,
                        details={
                            "client_org_id": org_id,
                            "bundle_count_7d_admin_view": bundle_count_7d,
                            "bundle_count_7d_client_view": 0,
                            "interpretation": (
                                f"client_org {org_id} has {bundle_count_7d} "
                                f"compliance bundles in the last 7 days "
                                f"(admin view), but the client portal sees "
                                f"ZERO under that org's RLS context. RLS "
                                f"misalignment hiding real evidence. Same "
                                f"class as the 2026-05-05 P0 closed in "
                                f"mig 278 — check if a NEW site-RLS table "
                                f"was added without an org-scoped policy."
                            ),
                        },
                    )
                )
        except Exception:
            # Best-effort — don't fail the entire assertion run on a
            # single org's quirk. The `async with conn.transaction()`
            # context manager auto-rolls back the savepoint on
            # exception; SET LOCAL settings are released with it.
            # Just log and move to the next org.
            logger.error(
                "client_rls_sim_savepoint_recovery_failed",
                extra={"client_org_id": org_id},
                exc_info=True,
            )
            continue

    return violations


async def _check_partition_maintainer_dry(conn: asyncpg.Connection) -> List[Violation]:
    """Sev1 — next month's partition missing on a critical partitioned
    table (Block 4 P1).

    Partitioned tables: `compliance_bundles` (mig 138, evidence chain),
    `portal_access_log` (mig 138, audit), `appliance_heartbeats`
    (mig 121, liveness ledger), `promoted_rule_events` (mig 181,
    flywheel ledger), `canonical_metric_samples` (mig 314, Counsel
    Rule 1 runtime sampling — no `_default` partition, so a wedged
    `canonical_metric_samples_pruner_loop` makes next-month INSERTs
    fail outright).

    `partition_maintainer_loop` is supposed to keep ≥3 months of
    forward partitions. If it's wedged or dead, next-month INSERTs
    land in the `_default` partition (bloats it + degrades query
    plans) or fail outright if no default exists.

    Sev1 because: HIPAA evidence chain (compliance_bundles) hitting
    the default partition slows every auditor-kit query proportionally.

    Naming conventions (live in migrations; if these drift, this
    invariant breaks loud — desired):
    - compliance_bundles:       `_YYYY_MM`
    - portal_access_log:        `_YYYY_MM`
    - appliance_heartbeats:     `_yYYYYMM`
    - promoted_rule_events:     `_YYYYMM`
    - canonical_metric_samples: `_YYYY_MM`
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    if now.month == 12:
        next_y, next_m = now.year + 1, 1
    else:
        next_y, next_m = now.year, now.month + 1
    # Substring patterns that the next-month child name MUST contain.
    expected_patterns = [
        f"{next_y}_{next_m:02d}",   # YYYY_MM (compliance_bundles, portal_access_log)
        f"y{next_y}{next_m:02d}",    # yYYYYMM (appliance_heartbeats)
        f"{next_y}{next_m:02d}",     # YYYYMM (promoted_rule_events)
    ]
    rows = await conn.fetch(
        """
        SELECT parent.relname AS parent_table,
               array_agg(child.relname ORDER BY child.relname DESC) AS children
          FROM pg_inherits i
          JOIN pg_class parent ON parent.oid = i.inhparent
          JOIN pg_class child ON child.oid = i.inhrelid
         WHERE parent.relname IN ('compliance_bundles', 'portal_access_log',
                                   'appliance_heartbeats', 'promoted_rule_events',
                                   'canonical_metric_samples')
           AND child.relname NOT LIKE '%_default%'
         GROUP BY parent.relname
        """
    )
    out: List[Violation] = []
    for r in rows:
        parent = r["parent_table"]
        children = r["children"] or []
        # Has any child name containing one of the expected patterns?
        if any(p in c for c in children for p in expected_patterns):
            continue
        out.append(Violation(
            site_id=None,
            details={
                "parent_table": parent,
                "next_year": next_y,
                "next_month": next_m,
                "latest_existing": children[0] if children else None,
                "interpretation": (
                    f"`{parent}` has NO partition for {next_y}-"
                    f"{next_m:02d}. Next-month INSERTs will land in "
                    f"`{parent}_default` (bloat + degraded query plans). "
                    f"Check `partition_maintainer_loop` + `heartbeat_"
                    f"partition_maintainer_loop` heartbeats; expected "
                    f"cadence 86400s (daily)."
                ),
            },
        ))
    return out


_SCHEMA_FIXTURE_DRIFT_LIMIT = 50  # Cap per-tick violation rows so a
                                  # wholesale schema event doesn't flood
                                  # the dashboard. Operator sees first 50
                                  # drifts; remaining surface next tick.
_SCHEMA_FIXTURE_QUERY_TIMEOUT_S = 5.0  # information_schema query MUST NOT
                                       # block the substrate loop. Diana
                                       # adversarial-round catch.
_FIXTURE_CACHE: Optional[Dict[str, Any]] = None  # Module-level cache —
                                                 # fixture is immutable
                                                 # for the lifetime of a
                                                 # process (deploy = new
                                                 # process). Steve catch:
                                                 # path.read_text() per-
                                                 # tick is sync blocking.


def _load_fixture_once() -> Optional[Dict[str, Any]]:
    """Lazy-load + cache the schema fixture. Returns None if missing
    or malformed (skip-graceful for test/local env). Re-load on
    process restart only."""
    global _FIXTURE_CACHE
    if _FIXTURE_CACHE is not None:
        return _FIXTURE_CACHE
    import json
    import pathlib as _pl
    fixture_path = (
        _pl.Path(__file__).resolve().parent
        / "tests" / "fixtures" / "schema" / "prod_columns.json"
    )
    if not fixture_path.exists():
        return None
    try:
        data = json.loads(fixture_path.read_text())
    except Exception:
        return None
    if not isinstance(data, dict) or not data:
        return None
    _FIXTURE_CACHE = data
    return data


async def _check_schema_fixture_drift(conn: asyncpg.Connection) -> List[Violation]:
    """Sev3 — prod's information_schema differs from the deployed code's
    tests/fixtures/schema/prod_columns.json.

    Followup #49 (2026-05-02 Diana adversarial-audit recommendation).
    Catches the 'forgot to update fixture' deploy class that bit Session
    214 audit cycle TWICE (mig 271 forward-merge required manual edits).

    The `test_sql_columns_match_schema` CI gate prevents new deploys
    with drift, but doesn't catch the case where prod has drifted from
    the fixture in the CURRENTLY DEPLOYED code (manual SQL ALTER outside
    migration, partial migration, fixture commit reverted but mig left
    in place). This invariant fires when that gap exists.

    Sev3 because the deployed code is functioning; the gap is in
    future-CI signal accuracy. Surfaces on /admin/substrate-health.

    Defenses (added 2026-05-02 adversarial round-table):
    1. EXCLUDES partition children + default partition (pg_class.relispartition).
       Otherwise every monthly partition rollover fires false positives.
    2. Query timeout 5s — prevents blocking the substrate loop on a
       slow information_schema scan (Diana catch).
    3. Fixture cached at module level — no per-tick sync IO (Steve catch).
    4. LIMIT 50 violation rows per tick (named constant, not magic).
    """
    fixture = _load_fixture_once()
    if fixture is None:
        return []

    # Partition-child filter: information_schema.columns does NOT
    # distinguish parent from partition child. If we don't filter, every
    # monthly partition rollover (eg compliance_bundles_2026_06 created)
    # fires `prod_only` — alarm-fatigue class. Use pg_class.relispartition
    # to exclude. Brian + Diana adversarial catch.
    try:
        rows = await asyncio.wait_for(
            conn.fetch(
                """
                SELECT c.relname AS table_name,
                       a.attname AS column_name
                  FROM pg_class c
                  JOIN pg_namespace n ON c.relnamespace = n.oid
                  JOIN pg_attribute a ON a.attrelid = c.oid
                 WHERE n.nspname = 'public'
                   AND c.relkind IN ('r', 'p')      -- regular + partitioned parent
                   AND NOT c.relispartition          -- exclude partition children
                   AND a.attnum > 0                  -- exclude system columns
                   AND NOT a.attisdropped
                """
            ),
            timeout=_SCHEMA_FIXTURE_QUERY_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        # Skip this tick rather than block the substrate loop.
        return []

    prod_by_table: dict[str, set[str]] = {}
    for r in rows:
        prod_by_table.setdefault(r["table_name"], set()).add(r["column_name"])

    # Filter the fixture to match what the prod query selects:
    # exclude partition children + exclude views.
    #
    # Pre-fix (initial #49 ship): only filtered partition children.
    # Adversarial QA pass 2026-05-02 found 17 false-positive
    # `fixture_only` violations because the fixture EXTRACTION SQL
    # used `information_schema.columns` (which includes views) but
    # the invariant's prod query uses `pg_class.relkind IN ('r','p')`
    # (regular + partitioned tables only). Asymmetry → false positives.
    # Now: also exclude `v_*`-prefixed names + known view names from
    # the fixture-side comparison.
    #
    # Partition naming patterns (per CLAUDE.md partition_maintainer rules):
    #   compliance_bundles + portal_access_log:  _YYYY_MM
    #   appliance_heartbeats:                     _yYYYYMM
    #   promoted_rule_events:                     _YYYYMM
    #   default partition:                        _default suffix
    import re as _re
    _PARTITION_RE = _re.compile(
        r"_(?:y?\d{4}_?\d{2}|default)$"
    )

    # Query prod for the canonical view list (includes both 'v' and 'm'
    # — regular views and materialized views). Then drop those from the
    # fixture-side comparison. SAFE failure mode: query error returns
    # empty set; we then might still false-positive on views, but
    # operator can manually disregard.
    try:
        view_rows = await asyncio.wait_for(
            conn.fetch(
                "SELECT relname FROM pg_class c "
                "JOIN pg_namespace n ON c.relnamespace = n.oid "
                "WHERE n.nspname = 'public' AND c.relkind IN ('v', 'm')"
            ),
            timeout=2.0,
        )
        view_names = {r["relname"] for r in view_rows}
    except Exception:
        view_names = set()

    fixture_tables = {
        t: cols for t, cols in fixture.items()
        if not _PARTITION_RE.search(t) and t not in view_names
    }

    drifts: list[tuple[str, str, str]] = []  # (table, column, direction)
    for table_name, fixture_cols in fixture_tables.items():
        if table_name not in prod_by_table:
            drifts.append((table_name, "<entire table>", "fixture_only"))
            continue
        prod_cols = prod_by_table[table_name]
        fixture_set = set(fixture_cols) if isinstance(fixture_cols, list) else set()
        for col in fixture_set - prod_cols:
            drifts.append((table_name, col, "fixture_only"))
        for col in prod_cols - fixture_set:
            drifts.append((table_name, col, "prod_only"))

    out: list[Violation] = []
    for table, column, direction in drifts[:_SCHEMA_FIXTURE_DRIFT_LIMIT]:
        out.append(Violation(
            site_id=None,  # global
            details={
                "table": table,
                "column": column,
                "direction": direction,
                "interpretation": (
                    f"deployed fixture lists `{table}.{column}` but prod "
                    f"information_schema does not — fixture is stale "
                    f"(column dropped without removing from fixture?)."
                    if direction == "fixture_only" else
                    f"prod has `{table}.{column}` but the deployed "
                    f"fixture does not — fixture forward-merge missed "
                    f"the migration that added it."
                ),
                "fix": (
                    f"Regenerate prod_columns.json from current prod "
                    f"information_schema (see test_sql_columns_match_schema "
                    f"docstring for the SQL+jq pipeline). Ship as a "
                    f"fixture-only commit. If drift was caused by a "
                    f"manual SQL ALTER, audit who did it via admin_audit_log."
                ),
                "total_drifts": len(drifts),  # so operator knows if cap was hit
                "shown": min(len(drifts), _SCHEMA_FIXTURE_DRIFT_LIMIT),
            },
        ))
    return out


async def _check_substrate_assertions_meta_silent(conn: asyncpg.Connection) -> List[Violation]:
    """The substrate engine's META watcher (Session 214 Block 2 round-table).

    `assertions_loop` writes a heartbeat every 60s tick. If the loop
    itself hangs in a stuck await (deadlock, asyncpg connection-acquire
    wait, hung HTTP), the heartbeat stops but the supervisor cannot
    detect a stuck await as an exception — the task hangs forever
    silently. The dashboard then shows "all-clear" forever because
    no fresh tick = no new violations, but ALSO no resolution.

    Sev1 because: if substrate is silent, EVERY downstream signal the
    substrate would have surfaced is silent. This is the meta-failure
    that hides every other failure. Threshold 180s = 3x the 60s
    expected cadence, matching the phantom_detector pattern.
    """
    try:
        from .bg_heartbeat import get_heartbeat
    except ImportError:
        try:
            from dashboard_api.bg_heartbeat import get_heartbeat  # pragma: no cover
        except ImportError:
            from bg_heartbeat import get_heartbeat  # type: ignore
    hb = get_heartbeat("substrate_assertions")
    if hb is None:
        # Process just started — give the loop a cycle to register.
        return []
    if hb["age_s"] > 180:
        return [
            Violation(
                site_id=None,
                details={
                    "loop": "substrate_assertions",
                    "age_s": hb["age_s"],
                    "iterations": hb["iterations"],
                    "errors": hb["errors"],
                    "interpretation": (
                        "substrate_assertions loop has not heartbeat in "
                        "> 3 min (3x cadence). The dashboard 'all-clear' "
                        "state is meaningless until this resolves — the "
                        "watcher itself is silent. Check mcp-server logs "
                        "for asyncpg pool exhaustion, deadlock, or stuck "
                        "await. Restart the container if no obvious cause."
                    ),
                },
            )
        ]
    return []


async def _check_bg_loop_silent(conn: asyncpg.Connection) -> List[Violation]:
    """Generic background-loop staleness check (Session 214 Block 2).

    Walks every loop in the bg_heartbeat registry and emits one Violation
    per loop whose `age_s > 3 * EXPECTED_INTERVAL_S[name]`. Loops not
    in EXPECTED_INTERVAL_S return 'unknown' — those are skipped to avoid
    noise; backfilling EXPECTED_INTERVAL_S is the operator's path to
    full coverage.

    Sev2 because: a stuck loop typically degrades a single feature
    (flywheel promotions, partition maintenance, alert digests) rather
    than masking the entire substrate. The substrate-meta sev1 covers
    the catastrophic case.
    """
    try:
        from .bg_heartbeat import get_all_heartbeats, assess_staleness
    except ImportError:
        try:
            from dashboard_api.bg_heartbeat import (  # pragma: no cover
                get_all_heartbeats, assess_staleness,
            )
        except ImportError:
            from bg_heartbeat import get_all_heartbeats, assess_staleness  # type: ignore

    out: List[Violation] = []
    # substrate_assertions has its own dedicated sev1 invariant — don't
    # double-fire from the generic sev2 catch-all.
    EXCLUDED = {"substrate_assertions", "phantom_detector"}
    heartbeats = get_all_heartbeats()
    for name, entry in heartbeats.items():
        if name in EXCLUDED:
            continue
        if assess_staleness(entry) != "stale":
            continue
        out.append(Violation(
            site_id=None,
            details={
                "loop": name,
                "age_s": entry["age_s"],
                "iterations": entry["iterations"],
                "errors": entry["errors"],
                "interpretation": (
                    f"Loop '{name}' has not heartbeat in {entry['age_s']:.0f}s "
                    f"— more than 3x its expected cadence. Stuck await, "
                    f"deadlock, or upstream dependency outage. Check "
                    f"mcp-server logs filtered for `{name}`."
                ),
            },
        ))
    return out


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


async def _check_sensitive_workflow_advanced_without_baa(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Sev1 — a BAA-gated sensitive workflow ADVANCED in the last 30
    days for a client_org with no active formal BAA (Task #52,
    Counsel Rule 6; §164.504(e) — the BAA must be in place before the
    BA performs services involving PHI).

    List 3 of the BAA enforcement lockstep. The CI gate
    (test_baa_gated_workflows_lockstep.py) catches an un-gated
    endpoint at build time; THIS invariant is the runtime backstop —
    it catches a code path that bypassed require_active_baa /
    enforce_or_log_admin_bypass, OR an org whose BAA lapsed AFTER the
    action advanced.

    Scope: the two state-machine workflows with a durable row —
    `cross_org_relocate` (cross_org_site_relocate_requests) and
    `owner_transfer` (client_org_owner_transfer_requests) — PLUS
    `evidence_export` (Task #92, 2026-05-15), scanned via the
    `auditor_kit_download` rows in `admin_audit_log`. The audit row's
    `details` carries `site_id` + `client_org_id` denormalized at
    write time (commit 5ce77722), so this scan needs no JOIN to
    `sites`. Only the `client_portal` + `partner_portal` `auth_method`
    rows are scanned — admin + legacy `?token=` are carved out
    (Carol carve-outs #3 + #4).

    Admin carve-out for the state-machine workflows: an admin-context
    advance writes a `baa_enforcement_bypass` admin_audit_log row
    (enforce_or_log_admin_bypass). Rows with a matching bypass entry
    are excluded — legitimate operator actions, already audited.
    evidence_export does NOT use the bypass row (its inline gate
    raises 403 instead of logging a bypass), so the bypass-row
    exclusion is guarded `if workflow != 'evidence_export'` below.
    """
    try:
        import baa_status
    except ImportError:  # pragma: no cover — package-context fallback
        from . import baa_status  # type: ignore

    rows = await conn.fetch(
        """
        SELECT 'cross_org_relocate' AS workflow,
               r.source_org_id::text AS org_id,
               r.site_id AS site_id,
               r.id::text AS row_id,
               COALESCE(r.executed_at, r.source_release_at) AS advanced_at
          FROM cross_org_site_relocate_requests r
         WHERE COALESCE(r.executed_at, r.source_release_at)
               > NOW() - INTERVAL '30 days'
        UNION ALL
        SELECT 'owner_transfer' AS workflow,
               t.client_org_id::text AS org_id,
               NULL AS site_id,
               t.id::text AS row_id,
               COALESCE(t.completed_at, t.current_ack_at) AS advanced_at
          FROM client_org_owner_transfer_requests t
         WHERE COALESCE(t.completed_at, t.current_ack_at)
               > NOW() - INTERVAL '30 days'
        UNION ALL
        -- Task #92: evidence_export scan via auditor_kit_download
        -- audit rows. site_id + client_org_id are denormalized at
        -- write time (commit 5ce77722). Filter to the two gated
        -- branches; admin + legacy ?token= are excluded carve-outs.
        SELECT 'evidence_export' AS workflow,
               aal.details->>'client_org_id' AS org_id,
               aal.details->>'site_id' AS site_id,
               aal.id::text AS row_id,
               aal.created_at AS advanced_at
          FROM admin_audit_log aal
         WHERE aal.action = 'auditor_kit_download'
           AND aal.created_at > NOW() - INTERVAL '30 days'
           AND aal.details->>'auth_method' IN ('client_portal','partner_portal')
        UNION ALL
        -- Task #98: new_site_onboarding scan via the sites table itself.
        -- A site row created without a matching baa_enforcement_bypass
        -- audit entry for a non-BAA org = a code path bypassed
        -- enforce_or_log_admin_bypass entirely. Window matches the
        -- other branches (30 days).
        SELECT 'new_site_onboarding' AS workflow,
               s.client_org_id::text AS org_id,
               s.site_id AS site_id,
               s.site_id AS row_id,
               s.created_at AS advanced_at
          FROM sites s  -- # noqa: synthetic-allowlisted — substrate engine MUST tick on synthetic site (Task #66 B1); rows segregate via substrate_violations.synthetic at INSERT time
         WHERE s.created_at > NOW() - INTERVAL '30 days'
           AND s.client_org_id IS NOT NULL
        UNION ALL
        -- Task #98: new_credential_entry scan. site_credentials has
        -- no client_org_id, so JOIN sites for the ownership snapshot.
        SELECT 'new_credential_entry' AS workflow,
               s.client_org_id::text AS org_id,
               sc.site_id AS site_id,
               sc.id::text AS row_id,
               sc.created_at AS advanced_at
          FROM site_credentials sc
          JOIN sites s ON s.site_id = sc.site_id
         WHERE sc.created_at > NOW() - INTERVAL '30 days'
           AND s.client_org_id IS NOT NULL
        """
    )
    violations: List[Violation] = []
    # An org can appear on multiple rows — cache the predicate result.
    ok_cache: Dict[str, bool] = {}
    for row in rows:
        org_id = row["org_id"]
        if not org_id:
            continue
        if org_id not in ok_cache:
            ok_cache[org_id] = await baa_status.baa_enforcement_ok(conn, org_id)
        if ok_cache[org_id]:
            continue
        # No active BAA — was this a logged admin carve-out?
        # Only the state-machine workflows use enforce_or_log_admin_bypass
        # (which writes the baa_enforcement_bypass row). evidence_export
        # uses check_baa_for_evidence_export, which RAISES 403 instead of
        # logging a bypass — so an evidence_export violation here is
        # unambiguously a gate-bypass or post-action BAA lapse, with no
        # legitimate-operator escape hatch. Skip the bypass-row lookup
        # for it (Task #92 Coach guard).
        if row["workflow"] != "evidence_export":
            bypass = await conn.fetchval(
                """
                SELECT 1 FROM admin_audit_log
                 WHERE action = 'baa_enforcement_bypass'
                   AND details->>'workflow' = $1
                   AND details->>'client_org_id' = $2
                 LIMIT 1
                """,
                row["workflow"], org_id,
            )
            if bypass:
                continue  # legitimate operator carve-out, already audited
        violations.append(Violation(
            site_id=row["site_id"],
            details={
                "workflow": row["workflow"],
                "client_org_id": org_id,
                "row_id": row["row_id"],
                "advanced_at": (
                    row["advanced_at"].isoformat()
                    if row["advanced_at"] else None
                ),
                "interpretation": (
                    f"workflow '{row['workflow']}' advanced for "
                    f"client_org {org_id} which has NO active formal "
                    f"BAA (baa_enforcement_ok=FALSE) and NO logged "
                    f"admin carve-out — either a code path bypassed "
                    f"require_active_baa/enforce_or_log_admin_bypass, "
                    f"or the org's BAA lapsed after the action. "
                    f"§164.504(e)."
                ),
            },
        ))
    return violations


# Task #62 v2.1 Commit 5a (2026-05-16): load-harness substrate invariants.
# Promised in mig 316 comments + v2.1 spec §"Substrate invariant" notes.
# Ships standalone of Commit 4 ops (CX22 + Vault Transit) — invariants
# are code-only and can land + tick before any real load runs fire.

# Customer-facing aggregation tables that MUST NEVER reference a
# synthetic site_id. The compliance_bundles invariant above is sev1
# (chain corruption); these are sev2 (visibility leak). All have
# a `site_id` column — verified against prod_columns.json fixture.
#
# Per-table TIMESTAMP COLUMN — most have `created_at` but
# `aggregated_pattern_stats` is rollup-only + tracks `first_seen` /
# `last_seen` instead. Iter-4 Commit 2 prod runtime check (2026-05-16)
# found that hardcoding `t.created_at` raised UndefinedColumnError
# every 60s on aggregated_pattern_stats. Per-table override fixes.
#
# Adding a table here = declaring it customer-facing.
_LOAD_TEST_SYNTHETIC_FORBIDDEN_TABLES = (
    # (table_name, timestamp_column_for_first_seen, timestamp_column_for_last_seen)
    ("incidents", "created_at", "created_at"),
    ("l2_decisions", "created_at", "created_at"),
    ("evidence_bundles", "created_at", "created_at"),
    ("aggregated_pattern_stats", "first_seen", "last_seen"),
)


async def _check_signing_backend_drifted_from_vault(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """fleet_orders signed in the last hour with a signing_method that
    differs from the configured SIGNING_BACKEND_PRIMARY env. This is
    the "code path silently fell back" class — operator configured
    Vault as primary but some callsite ended up signing via file.

    Vault Phase C iter-4 Commit 2 (2026-05-16). Anchored to mig 311
    (vault_signing_key_versions) + INV-SIGNING-BACKEND-VAULT startup
    invariant; this is the runtime backstop for both.

    Shadow-mode carve-out: when SIGNING_BACKEND=shadow, the shadow
    wrapper delegates to a primary backend (file or vault), so
    comparing observed methods against 'shadow' literal would always
    be a false positive. Compare against SIGNING_BACKEND_PRIMARY
    instead.
    """
    import os as _os
    backend_mode = _os.getenv("SIGNING_BACKEND", "file").strip().lower()
    primary = _os.getenv("SIGNING_BACKEND_PRIMARY", "file").strip().lower()
    if backend_mode == "shadow":
        expected = primary
    else:
        expected = backend_mode

    rows = await conn.fetch(
        """
        SELECT signing_method, COUNT(*) AS n
          FROM fleet_orders
         WHERE created_at > NOW() - INTERVAL '1 hour'
         GROUP BY signing_method
        """
    )
    if not rows:
        return []  # No recent signing activity — no signal.

    unexpected = [r for r in rows if r["signing_method"] != expected]
    if not unexpected:
        return []

    return [Violation(
        site_id=None,
        details={
            "expected_signing_method": expected,
            "observed_methods": {r["signing_method"]: int(r["n"]) for r in rows},
            "unexpected_count": sum(int(r["n"]) for r in unexpected),
            "interpretation": (
                f"fleet_orders signed in the last hour include signing_"
                f"method values other than the configured "
                f"SIGNING_BACKEND_PRIMARY={expected!r}. Either (a) a "
                f"code path silently fell back to a different backend "
                f"(check signing_backend.current_signing_method silent "
                f"swallow class), OR (b) operator changed the env mid-"
                f"hour without coordinated restart. Investigate via "
                f"SELECT signing_method, COUNT(*) FROM fleet_orders "
                f"WHERE created_at > NOW() - INTERVAL '1 hour' GROUP BY 1."
            ),
            "remediation": (
                "Tail mcp-server logs for 'current_signing_method_"
                "fallback' errors. If found, the singleton-build "
                "failed + the helper fell back to env default."
            ),
        },
    )]


async def _check_load_test_run_stuck_active(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """A load_test_runs row in starting/running/aborting state with
    started_at older than 6h indicates a k6 process that crashed or
    a wrapper that never called /complete — the partial unique index
    will block any NEW run until this one is reaped."""
    rows = await conn.fetch(
        """
        SELECT run_id::text AS run_id,
               started_at,
               status,
               started_by,
               scenario_sha,
               EXTRACT(EPOCH FROM (now() - started_at))/3600 AS hours_open
          FROM load_test_runs
         WHERE status IN ('starting','running','aborting')
           AND started_at < now() - INTERVAL '6 hours'
        """
    )
    return [
        Violation(
            site_id=None,
            details={
                "run_id": r["run_id"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "status": r["status"],
                "started_by": r["started_by"],
                "scenario_sha": r["scenario_sha"],
                "hours_open": float(r["hours_open"] or 0),
                "interpretation": (
                    f"load-test run {r['run_id']} stuck in {r['status']!r} "
                    f"for {float(r['hours_open'] or 0):.1f}h — k6 likely "
                    f"died without calling /complete. Partial unique "
                    f"index blocks any NEW run; reap via POST /api/admin/"
                    f"load-test/{{run_id}}/complete with final_status="
                    f"'failed'."
                ),
            },
        )
        for r in rows
    ]


async def _check_load_test_run_aborted_no_completion(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """A load_test_runs row with abort_requested_at set but status
    still NOT terminal (aborted/completed/failed) and abort older
    than 30 min indicates the abort-bridge regressed — k6 should
    poll /status every iteration + exit within 30s of seeing
    'aborting'. 30 min without transition = abort propagation broken."""
    rows = await conn.fetch(
        """
        SELECT run_id::text AS run_id,
               status,
               abort_requested_at,
               abort_requested_by,
               abort_reason,
               EXTRACT(EPOCH FROM (now() - abort_requested_at))/60 AS minutes_since_abort
          FROM load_test_runs
         WHERE abort_requested_at IS NOT NULL
           AND status NOT IN ('aborted','completed','failed')
           AND abort_requested_at < now() - INTERVAL '30 minutes'
        """
    )
    return [
        Violation(
            site_id=None,
            details={
                "run_id": r["run_id"],
                "status": r["status"],
                "abort_requested_at": r["abort_requested_at"].isoformat() if r["abort_requested_at"] else None,
                "abort_requested_by": r["abort_requested_by"],
                "abort_reason": r["abort_reason"],
                "minutes_since_abort": float(r["minutes_since_abort"] or 0),
                "interpretation": (
                    f"abort requested {float(r['minutes_since_abort'] or 0):.0f} min "
                    f"ago but run still in {r['status']!r} — k6 abort-poll "
                    f"bridge regressed or k6 wrapper crashed. Reap via "
                    f"POST /api/admin/load-test/{{run_id}}/complete with "
                    f"final_status='failed'."
                ),
            },
        )
        for r in rows
    ]


async def _check_load_test_marker_in_compliance_bundles(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Defense-in-depth runtime backstop: compliance_bundles is Ed25519-
    chained + OTS-anchored — its hash pins the auditor-kit determinism
    contract. ANY bundle written for a synthetic site corrupts the
    chain + flips kit hash between consecutive downloads.

    Layered defenses (this is the THIRD layer):
      1. CI gate test_no_load_test_marker_in_compliance_bundles —
         scans Python source for 'load_test' literals near
         compliance_bundles write callsites (build-time prevention)
      2. DB CHECK constraint no_synthetic_bundles (mig 315) —
         REJECTS any compliance_bundles row with site_id LIKE
         'synthetic-%' at write time
      3. THIS invariant — runtime scan for the bypass class:
         sites.synthetic=TRUE that doesn't carry the 'synthetic-%'
         site_id prefix (CHECK constraint is name-based, not
         flag-based)

    Gate B C5a-rev1 (2026-05-16): rewritten after fork verdict
    audit/coach-c5a-pha-94-closure-gate-b-2026-05-16.md P0 found
    that compliance_bundles has NO `details` column — the prior
    query raised UndefinedColumnError every 60s tick + the sev1
    invariant silently never fired."""
    rows = await conn.fetch(
        """
        SELECT cb.bundle_id, cb.site_id, cb.check_type, cb.created_at
          FROM compliance_bundles cb
         WHERE (cb.site_id LIKE 'synthetic-%'
             OR cb.site_id IN (SELECT site_id FROM sites WHERE synthetic = TRUE))
           -- Task #117 Sub-commit B (mig 325) carve-out: the chain-
           -- contention load-test site INTENTIONALLY writes real
           -- compliance_bundles (the entire point is to exercise the
           -- per-site advisory lock under N-way contention). It uses
           -- sites.load_test_chain_contention=TRUE (NOT sites.synthetic)
           -- + non-'synthetic-' prefix to bypass the no_synthetic_bundles
           -- CHECK. This carve-out is the defensive layer in case a
           -- future migration accidentally flips synthetic=TRUE here.
           AND cb.site_id != 'load-test-chain-contention-site'
         LIMIT 100
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "bundle_id": r["bundle_id"],
                "check_type": r["check_type"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "interpretation": (
                    f"compliance_bundles bundle_id={r['bundle_id']} "
                    f"writes to synthetic site site_id={r['site_id']!r} — "
                    f"Ed25519 chain + auditor-kit determinism contract "
                    f"violated. Quarantine + investigate writer. Layer 2 "
                    f"(CHECK constraint no_synthetic_bundles) blocks "
                    f"'synthetic-%' prefix at INSERT; this fires when a "
                    f"non-prefixed site_id has sites.synthetic=TRUE."
                ),
            },
        )
        for r in rows
    ]


async def _check_load_test_chain_contention_site_orphan(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Sev2 — compliance_bundles row exists for site_id='load-test-
    chain-contention-site' OUTSIDE an active load_test_runs window.

    Task #117 Sub-commit B (mig 325) per Gate A Option C. The chain-
    contention test seeds 20 appliances + 20 bearers + 1 site row
    (sites.load_test_chain_contention=TRUE, NOT sites.synthetic=TRUE
    so no_synthetic_bundles CHECK passes + real bundles can be
    written). Bundles are EXPECTED on this site_id during k6 soak
    windows; they are an ORPHAN if no load_test_runs row covers the
    bundle's created_at.

    Counsel Rule 4 alignment: orphan-coverage detection on synthetic
    infrastructure. Without this gate a production writer could
    silently target the seed site (e.g., a test fixture leaks into
    prod, a typo'd site_id constant) and we'd never see it —
    bundles would just accumulate on a non-customer site.

    Trigger conditions:
      - bundle exists for site_id='load-test-chain-contention-site'
      - bundle's created_at is NOT within [started_at, COALESCE(
        completed_at, started_at + INTERVAL '4 hours')] of ANY
        load_test_runs row (4h max soak per #117 design)

    Auto-resolves when: stray bundle ages out of scope OR a
    load_test_runs row gets backfilled (admin op).

    Action narrowing: bound the scan to last 7d compliance_bundles
    on this single site — synthetic infrastructure should rarely
    accumulate bundles, so 7d is generous + the partial index on
    sites.load_test_chain_contention=TRUE makes the load_test_runs
    join cheap. LIMIT 50.

    Gate B 2026-05-16 P1-1 fix: COALESCE buffer extended 4h → 24h.
    Original 4h band false-positived on chaos tests, admin ops, or
    clock-stalled load_test_runs rows that legitimately extend
    beyond the #117 design max (30min). 24h is the worst-case bound
    — anything >24h orphaned really IS an orphan worth surfacing,
    and synthetic infra should never have a covering-row gap >1d.
    """
    rows = await conn.fetch(
        """
        SELECT cb.bundle_id, cb.site_id, cb.check_type, cb.created_at
          FROM compliance_bundles cb
         WHERE cb.site_id = 'load-test-chain-contention-site'
           AND cb.created_at > NOW() - INTERVAL '7 days'
           AND NOT EXISTS (
             SELECT 1
               FROM load_test_runs ltr
              WHERE cb.created_at >= ltr.started_at
                AND cb.created_at <= COALESCE(
                    ltr.completed_at,
                    ltr.started_at + INTERVAL '24 hours'
                )
           )
         ORDER BY cb.created_at DESC
         LIMIT 50
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "bundle_id": r["bundle_id"],
                "check_type": r["check_type"],
                "created_at": (
                    r["created_at"].isoformat() if r["created_at"] else None
                ),
                "interpretation": (
                    f"compliance_bundles bundle_id={r['bundle_id']!r} "
                    f"writes to load-test seed site "
                    f"site_id={r['site_id']!r} OUTSIDE any active "
                    f"load_test_runs window — a production writer is "
                    f"accidentally targeting the synthetic infrastructure. "
                    f"Investigate writer + quarantine bundle. See "
                    f"substrate_runbooks/load_test_chain_contention_"
                    f"site_orphan.md."
                ),
            },
        )
        for r in rows
    ]


async def _check_synthetic_traffic_marker_orphan(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Wider class than the compliance_bundles invariant: scans 4
    customer-facing aggregation tables (incidents, l2_decisions,
    evidence_bundles, aggregated_pattern_stats) for rows tied to
    synthetic sites. Authority is `sites.synthetic=TRUE` (mig 315),
    NOT a per-row details marker — most of these tables lack a
    `details` JSONB column entirely.

    Also catches the MTTR-soak shape on `incidents.details->>
    'soak_test'='true'` (real marker per mig 303 + indexed partial).
    Customer-facing aggregations should filter synthetic sites out
    via the canonical `WHERE site_id NOT IN (SELECT site_id FROM
    sites WHERE synthetic = TRUE)` predicate.

    Gate B C5a-rev1 (2026-05-16): rewritten — prior query referenced
    `details->>'synthetic'` which only exists on incidents (and the
    real MTTR marker is `details->>'soak_test'='true'`, not
    `synthetic='mttr_soak'`). 3 of 4 tables silently skipped pre-
    rev1 via `except asyncpg.PostgresError`. Per fork verdict
    audit/coach-c5a-pha-94-closure-gate-b-2026-05-16.md P0-2."""
    violations: List[Violation] = []
    for tbl, first_ts_col, last_ts_col in _LOAD_TEST_SYNTHETIC_FORBIDDEN_TABLES:
        # iter-4 prod runtime fix (2026-05-16): per-table timestamp
        # column override — aggregated_pattern_stats has
        # `first_seen`/`last_seen`, not `created_at`. Pre-fix the
        # hardcoded `t.created_at` raised UndefinedColumnError every
        # 60s tick on that table. The CI gate
        # test_substrate_invariant_sql_columns_valid failed open
        # because the alias resolver doesn't see f-string interpolated
        # table names; gate hardening shipped alongside.
        rows = await conn.fetch(
            f"""
            SELECT t.site_id, COUNT(*) AS hit_count,
                   MIN(t.{first_ts_col}) AS first_seen,
                   MAX(t.{last_ts_col}) AS last_seen
              FROM {tbl} t
              JOIN sites s ON s.site_id = t.site_id
             WHERE s.synthetic = TRUE
             GROUP BY t.site_id
             LIMIT 100
            """
        )
        for r in rows:
            violations.append(Violation(
                site_id=r["site_id"],
                details={
                    "table": tbl,
                    "hit_count": int(r["hit_count"] or 0),
                    "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
                    "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
                    "interpretation": (
                        f"{int(r['hit_count'] or 0)} rows in {tbl} "
                        f"for synthetic site site_id={r['site_id']!r} — "
                        f"load-harness or MTTR-soak traffic leaked into "
                        f"a customer-facing aggregation. Add the "
                        f"canonical filter "
                        f"`WHERE site_id NOT IN (SELECT site_id FROM "
                        f"sites WHERE synthetic = TRUE)` to the writer "
                        f"or reader path."
                    ),
                },
            ))

    # Additional MTTR-soak marker check on incidents (mig 303 indexed
    # partial). Catches the case where soak_test marker landed on a
    # non-synthetic site_id (writer-side bug) — that wouldn't show in
    # the sites-join above. Counts as a sibling but distinct row.
    soak_rows = await conn.fetch(
        """
        SELECT i.site_id, COUNT(*) AS hit_count,
               MIN(i.created_at) AS first_seen,
               MAX(i.created_at) AS last_seen
          FROM incidents i
          LEFT JOIN sites s ON s.site_id = i.site_id
         WHERE i.details->>'soak_test' = 'true'
           AND (s.synthetic IS NOT TRUE OR s.site_id IS NULL)
         GROUP BY i.site_id
         LIMIT 100
        """
    )
    for r in soak_rows:
        violations.append(Violation(
            site_id=r["site_id"],
            details={
                "table": "incidents",
                "marker": "details.soak_test='true'",
                "hit_count": int(r["hit_count"] or 0),
                "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
                "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
                "interpretation": (
                    f"{int(r['hit_count'] or 0)} incidents tagged "
                    f"soak_test='true' on NON-synthetic site_id={r['site_id']!r} — "
                    f"the MTTR-soak writer is mis-routing markers. "
                    f"Either the site_id is wrong or sites.synthetic "
                    f"hasn't been flipped TRUE. Per mig 303 + 315."
                ),
            },
        ))
    return violations


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
        name="sigauth_crypto_failures",
        severity="sev1",
        description="Subset of signature_verification_failures filtered to the adversarial-reason set (invalid_signature, bad_signature_format, nonce_replay). Distinct priority signal: crypto-level fail = real attack / drift; conjunction with the umbrella = security incident; umbrella alone = enrollment/clock debt (2026-04-25)",
        check=lambda c: _check_sigauth_crypto_failures(c),
    ),
    Assertion(
        name="sigauth_enforce_mode_rejections",
        severity="sev2",
        description="Tightened gate for enforce-mode appliances: ANY invalid sigauth observation in 6h fires this. The umbrella (1h/5-sample/5%) is structurally blind to low-rate jitter, but enforce mode by definition is a 0%-fail contract. One rejection means a key-state coherence smell that must be investigated before it becomes 100% (Session 211 Phase 2 QA, 2026-04-28).",
        check=lambda c: _check_sigauth_enforce_mode_rejections(c),
    ),
    # Removed 2026-05-05 17:23Z (Session 217 close of task #4): the
    # sigauth_post_fix_window_canary invariant covered the 7-day
    # acceptance window 2026-04-28 17:11Z → 2026-05-05 17:11Z that
    # bracketed the sigauth wrap-fix deploy. Verified silent (0
    # firings, 0 violations) across the entire window via fork
    # psql query before removal. The umbrella sigauth_enforce_mode_rejections
    # invariant remains as the steady-state detector.
    Assertion(
        name="flywheel_orphan_telemetry",
        severity="sev1",
        description="Detects execution_telemetry rows under site_ids that have no matching live site_appliances row — the upstream source class that defeated migrations 252 + 254 on 2026-04-29. The flywheel aggregator (main.py _flywheel_promotion_loop) GROUPs BY site_id and would silently re-create orphan aggregated_pattern_stats rows from such telemetry, leading to phantom-eligible candidates promoting into dead sites. Fires sev1 on any site_id with >10 orphan telemetry rows in the last 24h. Round-table 2026-04-29 P0 (F3).",
        check=lambda c: _check_flywheel_orphan_telemetry(c),
    ),
    Assertion(
        name="rename_site_immutable_list_drift",
        severity="sev2",
        description="Detects site_id-bearing tables protected by a DELETE-blocking trigger that are NOT in _rename_site_immutable_tables(). Such a table is operationally append-only (UPDATE/DELETE blocked is the standard audit-class signal) yet rename_site() would happily rewrite its site_id — a chain-of-custody violation waiting to happen. Fires sev2 to flag the immutable-list drift before the next rename. Session 213 F4-followup (mig 257 round-table). Resolution: add the table to _rename_site_immutable_tables() in a follow-on migration, OR confirm the table is genuinely operational and the DELETE-block is unintended.",
        check=lambda c: _check_rename_site_immutable_list_drift(c),
    ),
    Assertion(
        name="go_agent_heartbeat_stale",
        severity="sev2",
        description="Detects workstation Go agents whose last_heartbeat is older than 6 hours, EXCLUDING agents tagged agent_version='dev' (chaos-lab targets). Doctrine fix from Session 214 round-table 2026-04-30: empirically observed 4 chaos-lab agents showing status='connected' 7+ days after their host (iMac) was powered off. The state machine in main.py::_go_agent_status_decay_loop now flips status accordingly, but the invariant is the alarm tripwire — sev2 means 'operator action expected within the workday' (workstation has been silent for at least 6h, which is longer than legitimate after-hours / commute / lunch windows). The dashboard partner contact should follow up; substrate's job ends at the alarm.",
        check=lambda c: _check_go_agent_heartbeat_stale(c),
    ),
    Assertion(
        name="appliance_offline_extended",
        severity="sev2",
        description="Sibling to existing offline_appliance_over_1h (sev2, fires at 1h). This sev2 fires at 24h+. Different runbook implication: the 1h sibling means 'wait one cycle, probably transient'; this means 'phone the customer.' Appliances offline >24h are a customer-experience event, not a substrate-internal blip. Round-table 2026-04-30: shipping the longer-threshold sibling explicitly so operators have escalation graduation. Unlike go_agent_heartbeat_stale, this does NOT exclude dev-tagged appliances (there's no equivalent of agent_version='dev' on appliances).",
        check=lambda c: _check_appliance_offline_extended(c),
    ),
    Assertion(
        name="flywheel_federation_misconfigured",
        severity="sev3",
        description="Detects the FLYWHEEL_FEDERATION_ENABLED env flag being ON in production while no tier in flywheel_eligibility_tiers has both enabled=TRUE AND calibrated_at IS NOT NULL. In this state the federation read path falls back to hardcoded defaults (defensive — no behavior change) and emits a logger.warning per loop tick, but the operator-visibility gap is sev3. Resolution: either flip the env var off (true intent: federation OFF) or run the calibration migration that flips a tier to enabled+calibrated (true intent: federation ON). Round-table 2026-04-30 fast-follow from F6 MVP slice review. Session 214.",
        check=lambda c: _check_flywheel_federation_misconfigured(c),
    ),
    Assertion(
        name="promotion_audit_log_recovery_pending",
        severity="sev1",
        description="HIPAA §164.312(b) chain-of-custody durability gate. promotion_audit_log_recovery is the dead-letter queue for promotion_audit_log INSERTs that failed inside flywheel_promote.promote_candidate's Step 7 savepoint (Migration 253, Session 212 round-table P0). Any unrecovered row means an L1 rule promotion happened without its audit row landing in the WORM-style audit log — the chain of custody is broken until an operator runs the recovery script. Sev1 because the loss is HIPAA-relevant and grows monotonically until handled.",
        check=lambda c: _check_promotion_audit_log_recovery_pending(c),
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
        name="provisioning_network_fail",
        severity="sev2",
        description="A freshly-installed appliance is retrying provisioning (install_sessions.checkin_count ≥ 3 within the last hour) AND the installed system has never reported a successful 4-stage network gate (first_outbound_success_at IS NULL) AND site_appliances is either missing or stale >15min. Fires EARLIER than provisioning_stalled (within ~90s of first failed install-system checkin) so the customer sees the DNS/egress/TLS/health stage failure before 20 minutes of silence. Paired with v40 FIX-11 gate on :8443 beacon — `install_gate_status.last_stage_failed` names the exact broken stage. 2026-04-23 round-table — outcome-layer signal for the FIX-9/10 firewall-determinism fix.",
        check=lambda c: _check_provisioning_network_fail(c),
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
        name="installer_halted_early",
        severity="sev2",
        description="install_sessions row ≥20 min old, checkin_count < 5 (installer did not reach the older `installed_but_silent` threshold), AND site_appliances.last_checkin is NULL or predates the install_sessions row. Covers the v40.0-v40.2 bricking class where the installer posted /start once then completed and the installed system never checked in — zero other invariants fired for 4+ hours on 2026-04-23. Distinct from `installed_but_silent` (peak_count ≥ 5) because that one requires the installer to have looped multiple times; this one fires on the single-post-then-silent pattern.",
        check=lambda c: _check_installer_halted_early(c),
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
        name="relocation_stalled",
        severity="sev2",
        description=(
            "An admin-initiated appliance relocation has been pending > 30 min — "
            "the daemon never picked up its reprovision order or never produced "
            "a successful checkin under the target site. Same risk class as "
            "today's orphan: the source row is in 'relocating' state, the "
            "target row exists but never received a checkin. Either the "
            "daemon's offline, the reprovision order failed, or the daemon "
            "version is too old to handle reprovision (should have been gated "
            "by the relocate endpoint, but worth surfacing if the gate slipped). "
            "Round-table RT-4 (Session 210-B 2026-04-25)."
        ),
        check=lambda c: _check_relocation_stalled(c),
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
    Assertion(
        name="flywheel_ledger_stalled",
        severity="sev1",
        description="The flywheel orchestrator runs every 5 minutes and is the ONLY legal writer to promoted_rule_events (the append-only spine ledger). If there are promoted rules in transitional states (rolling_out with fleet-order completions, active with unacknowledged regime events) but zero ledger rows landed in the last 60 minutes, the orchestrator is either stuck or every transition is silently failing its advance_lifecycle() call. This is exactly the 2026-04-18 audit finding: Python EVENT_TYPES had drifted from the DB CHECK so every transition tripped a CheckViolationError that safe_rollout downgraded to a WARNING. Fleet Intelligence dashboard sat at all-zeros for months. At sev1 because the data flywheel — L2→L1 self-improvement — is silently offline.",
        check=lambda c: _check_flywheel_ledger_stalled(c),
    ),
    Assertion(
        name="nixos_rebuild_success_drought",
        severity="sev2",
        description="Operators have issued ≥1 nixos_rebuild admin_order in the last 7 days but NONE have succeeded. Signals a silent regression in the flake-eval / systemd-boot / lanzaboote path that prevents NixOS-level remediation from landing. Lost capability is invisible until the next live rebuild attempt surfaces it — this invariant exposes the gap on the dashboard between attempts. 2026-04-21 round-table: a 59-day success drought on fleet-wide rebuild capability went undetected until a live canary rebuild against the journal-timer feature confirmed it; pre-0.4.7 daemons truncated the nix `error:` banner, so every failure looked like an unexplained exit code.",
        check=lambda c: _check_nixos_rebuild_success_drought(c),
    ),
    Assertion(
        name="appliance_disk_pressure",
        severity="sev2",
        description="An appliance has surfaced a `No space left on device` error in a recent admin_order or fleet_order_completion within the last 24 hours. Disk pressure on /nix/store silently fails nixos_rebuild AND blocks evidence-bundle writes (the daemon cannot fsync the compliance bundle to disk). 2026-04-22 canary incident: 84:3A:5B:1D:0F:E5 rebuild failed with `writing to file: No space left on device` under a 5 GB /nix store — no substrate signal existed until the 0.4.7 diagnostic upgrade tail exposed the nix error. Mirror of vps_disk_pressure, scoped per-appliance. Auto-resolves when no matching error is observed in the 24h window.",
        check=lambda c: _check_appliance_disk_pressure(c),
    ),
    Assertion(
        name="l2_decisions_stalled",
        severity="sev2",
        description="L2_ENABLED=true but <5 L2 decisions in the last 48 hours while the fleet has active appliances. Signals the LLM pipeline is silently offline (API key, circuit breaker, budget cap, or zero-result circuit). Added 2026-04-24 (Session 210) when re-enabling L2 after the 2026-04-12 kill switch so the NEXT silent death pages inside 48h instead of taking 30 days to notice. Automatically quiet when L2_ENABLED=false, so intentional maintenance doesn't create noise.",
        check=lambda c: _check_l2_decisions_stalled(c),
    ),
    Assertion(
        name="unbridged_telemetry_runbook_ids",
        severity="sev2",
        description="Detects execution_telemetry rows whose runbook_id has no corresponding agent_runbook_id in the runbooks table — drift between agent rule shipments and the canonical runbook table. RT-DM Issue #1 (2026-05-06): the agent's L1-* IDs and the backend's LIN-* / RB-* IDs were unbridged for months, breaking per-runbook execution counts. Migration 284 added agent_runbook_id + backfill; this invariant catches new agent rules that ship without a corresponding runbooks row.",
        check=lambda c: _check_unbridged_telemetry_runbook_ids(c),
    ),
    Assertion(
        name="l2_resolution_without_decision_record",
        severity="sev2",
        description="An incident with resolution_tier='L2' has no corresponding row in l2_decisions — integrity gap between the resolution tier and the LLM decision record. RT-DM Issue #2 hardening (non-consensus): every L2 resolution should be traceable to either a fresh LLM decision OR a cache-hit reference. Surfaces incidents where the L2 path resolved but the audit trail is missing.",
        check=lambda c: _check_l2_resolution_without_decision_record(c),
    ),
    Assertion(
        name="l1_resolution_without_remediation_step",
        severity="sev2",
        description="An incident with resolution_tier='L1' has no corresponding row in incident_remediation_steps — auto-resolve path tagged 'L1' without recording the runbook execution. Session 219 (2026-05-11) prod sample found 1131 of 2327 L1 resolutions (49%) on chaos-lab lacking a relational step. Sibling of l2_resolution_without_decision_record. resolution_tier='L1' is the customer-facing 'auto-healed' label; a missing relational step is a false claim on the audit chain.",
        check=lambda c: _check_l1_resolution_without_remediation_step(c),
    ),
    Assertion(
        name="orders_stuck_acknowledged",
        severity="sev2",
        description="orders.status has been 'acknowledged' or 'executing' for >30 minutes (acknowledged) or >1 hour (executing) — agent ack'd the order but never reported execution telemetry. RT-DM Issue #3 hardening (non-consensus): without this, dashboards show stuck orders as 'in flight' indefinitely. The sweep_stuck_orders() function (mig 286) addresses these but should not be needed often; firing this invariant means the agent → telemetry path is broken or the order_id metadata isn't reaching telemetry.",
        check=lambda c: _check_orders_stuck_acknowledged(c),
    ),
    Assertion(
        name="frontend_field_undefined_spike",
        severity="sev2",
        description="Browser-side apiFieldGuard has seen >10 FIELD_UNDEFINED events from >=2 distinct sessions for the same (endpoint, field) pair in the last 5 min. Frontend code is reading a field the backend is no longer returning, and real users are hitting it. This invariant is Layer 3 of the Session 210 enterprise API reliability plan: even with Pydantic contract checks (Layer 6) and OpenAPI codegen (Layer 1) there's still a semantic-drift class (JSONB sub-fields, enum values, format changes) this catches at runtime. Auto-resolves when the spike passes (field populated again OR frontend updated to stop reading it).",
        check=lambda c: _check_frontend_field_undefined_spike(c),
    ),
    Assertion(
        name="synthetic_l2_pps_rows",
        severity="sev3",
        description="platform_pattern_stats should never contain rows with L2-prefixed runbook_id (they are synthetic planner-internal IDs, excluded from aggregation by background_tasks.py:1189 `NOT LIKE 'L2-%'`). 2026-04-18 migration 237 DELETEd 2 such rows; Session 210 found them back with January timestamps despite no identified INSERT path. Rows were re-deleted. This invariant catches the next reappearance loud instead of silent.",
        check=lambda c: _check_synthetic_l2_pps_rows(c),
    ),
    Assertion(
        name="substrate_assertions_meta_silent",
        severity="sev1",
        description="The substrate engine's own assertions_loop has not heartbeat in 3+ min (3x the 60s cadence). The dashboard's 'all-clear' state is meaningless while this fires — the watcher itself is silent. Catches the meta-failure class (stuck await / deadlock / asyncpg pool exhaustion) that hides every downstream invariant. Round-table 2026-05-01 Block 2 P0 closure.",
        check=lambda c: _check_substrate_assertions_meta_silent(c),
    ),
    Assertion(
        name="bg_loop_silent",
        severity="sev2",
        description="A registered background loop (with known expected cadence in bg_heartbeat.EXPECTED_INTERVAL_S) has not heartbeat in 3x its cadence. One violation row per stuck loop. Closes the silent-stuck-loop class — `_supervised` auto-restarts on EXCEPTIONS but a stuck await is not an exception, so a hung loop logs nothing. Round-table 2026-05-01 Block 2 P0 closure.",
        check=lambda c: _check_bg_loop_silent(c),
    ),
    Assertion(
        name="compliance_packets_stalled",
        severity="sev1",
        description="A site emitted compliance_bundles in the prior completed month but compliance_packets has no row for that site+month+framework='hipaa'. HIPAA §164.316(b)(2)(i) requires 6-year retention of monthly attestations — silent miss is auditor-visible. 24h grace window from start of new month before firing. Round-table 2026-05-01 Block 4 P1 closure.",
        check=lambda c: _check_compliance_packets_stalled(c),
    ),
    Assertion(
        name="compliance_bundles_trigger_disabled",
        severity="sev1",
        description="`compliance_bundles_no_delete` trigger is NOT in ENABLE ALWAYS state on the parent partitioned table or any partition. Chain-of-custody integrity guard is degraded — bulk-DELETEs become possible without alarm. Phase 1 multi-tenant audit F-P1-3 (2026-05-09) — adversarial cleanup tests can leave the trigger DISABLED if cleanup mid-aborts. Round-table 4-of-4 approved this runtime defense.",
        check=lambda c: _check_compliance_bundles_trigger_disabled(c),
    ),
    Assertion(
        name="db_baseline_guc_drift",
        severity="sev2",
        description="Load-bearing Postgres GUC has drifted from baseline. The substrate's tenant-safety posture depends on `app.is_admin` defaulting to 'false' (mig 234) and `app.current_tenant`/`app.current_org`/`app.current_partner_id` defaulting to ''. If any has drifted to a permissive value, RLS isolation silently flips. Phase 1 multi-tenant audit F-P1-4 (2026-05-09).",
        check=lambda c: _check_db_baseline_guc_drift(c),
    ),
    Assertion(
        name="substrate_sla_breach",
        severity="sev2",
        description="META — any non-meta sev1/sev2 substrate invariant has been open beyond its per-severity SLA (sev1 ≤4h, sev2 ≤24h, sev3 ≤30d). Closes the 'engine works, response loop doesn't' class caught by the 2026-05-08 E2E audit (4 sev2 invariants had been open for cumulative >22 days). Skips self + intentional long-open carve-outs (currently `pre_mig175_privileged_unattested` — sev3 disclosure surface). Operations PM track. Round-table 2026-05-08 RT-3.3 close.",
        check=lambda c: _check_substrate_sla_breach(c),
    ),
    Assertion(
        name="pre_mig175_privileged_unattested",
        severity="sev3",
        description="INFORMATIONAL — surfaces 3 pre-migration-175 privileged fleet_orders rows on north-valley-branch-2 that lack attestation_bundle_id. New violations are STRUCTURALLY blocked by trg_enforce_privileged_chain (mig 175). Disclosure path chosen over backfill per round-table 2026-05-08 RT-1.2 (4-of-4 Carol/Sarah/Steve/Maya); see docs/security/SECURITY_ADVISORY_2026-04-13_PRIVILEGED_PRE_TRIGGER.md. Sev3 — operator visibility, not action.",
        check=lambda c: _check_pre_mig175_privileged_unattested(c),
    ),
    Assertion(
        name="merkle_batch_stalled",
        severity="sev1",
        description="One or more compliance_bundles rows have been pinned at ots_status='batching' for >6 hours. The hourly _merkle_batch_loop has not transitioned them toward Bitcoin OTS anchoring. §164.312(c)(1) integrity controls + customer-facing tamper-evidence promise depend on this loop. Pre-fix on 2026-05-08 (commit 7db2faab) the loop was RLS-blind via bare pool.acquire() — 2,669 bundles stuck 18 days on the only paying site. Structural fix in place + CI gate (test_bg_loop_admin_context.py) prevents regression of the RLS class; THIS invariant is the runtime detector for any OTHER stall cause (calendar outage, asyncpg pool exhaustion, code-bug in process_merkle_batch). Round-table 2026-05-08 RT-1.1 (c) close.",
        check=lambda c: _check_merkle_batch_stalled(c),
    ),
    Assertion(
        name="partition_maintainer_dry",
        severity="sev1",
        description="A critical partitioned table (compliance_bundles, portal_access_log, appliance_heartbeats, promoted_rule_events, canonical_metric_samples) has NO partition for next month. INSERTs land in the _default partition (bloats it + degrades query plans) or fail if no default exists (canonical_metric_samples has NO default — wedge = INSERT failures). Indicates partition_maintainer_loop / heartbeat_partition_maintainer_loop / canonical_metric_samples_pruner_loop are wedged. Round-table 2026-05-01 Block 4 P1 closure; canonical_metric_samples added 2026-05-14 (Task #65a).",
        check=lambda c: _check_partition_maintainer_dry(c),
    ),
    Assertion(
        name="email_dlq_growing",
        severity="sev2",
        description="More than 5 unresolved rows in email_send_failures (mig 272 DLQ) within the last 24h on a single label. SMTP outage, auth break, or recipient-bounce class. Maya 2026-05-04 substrate observability follow-up — Email DLQ shipped in commit 3cd0a208 with no invariant attached because the table was empty; this fills the gap. Threshold conservative; tune after first month of real traffic.",
        check=lambda c: _check_email_dlq_growing(c),
    ),
    Assertion(
        name="cross_org_relocate_chain_orphan",
        severity="sev1",
        description="A site has sites.prior_client_org_id SET but no completed cross_org_site_relocate_requests row attesting the move. Indicates a code path bypassed the relocate flow (direct UPDATE on sites.client_org_id), creating a chain-of-custody gap that auditors would flag. RT21 (2026-05-05) Steve mit 4 substrate-layer guarantee: every cross-org move MUST flow through the attested state machine; the substrate engine catches any drift.",
        check=lambda c: _check_cross_org_relocate_chain_orphan(c),
    ),
    Assertion(
        name="bundle_chain_position_gap",
        severity="sev1",
        description="A site_id has a non-contiguous chain_position in compliance_bundles within the last 24h. The per-site pg_advisory_xact_lock in evidence_chain.py::create_compliance_bundle MUST serialize chain writes — a gap means either the lock failed to serialize a concurrent writer OR a row was deleted/skipped post-INSERT. Sev1 because chain corruption is the most direct form of chain-of-custody violation (kit hash flips between consecutive auditor-kit downloads, breaking the tamper-evidence promise). Task #117 prerequisite (multi-device P1-1) — load test would prove nothing without this gate. Runbook: substrate_runbooks/bundle_chain_position_gap.md.",
        check=_check_bundle_chain_position_gap,
    ),
    Assertion(
        name="fleet_order_fanout_partial_completion",
        severity="sev2",
        description="A fleet_cli --all-at-site fan-out (one privileged_access_attestation bundle covering N fleet_orders) has at least one target appliance unacked > 6h post-issuance. Per #118 (multi-device P1-2): fan-out creates ONE bundle + N orders via admin_audit_log.details->>'fleet_order_ids' jsonb array. At scale, some target appliances may be offline → silent partial completion. The operator sees the fan-out as 'issued' without seeing 'K-of-N never executed'. Sev2 per Gate A 2026-05-16 / Gate B P1-A — sibling parity with appliance_moved_unack (silent-fail-after-issuance class). 'skipped' status counts as ack (appliance at skip_version — Gate A P0-3 false-positive avoidance). Runbook: substrate_runbooks/fleet_order_fanout_partial_completion.md.",
        check=_check_fleet_order_fanout_partial_completion,
    ),
    Assertion(
        name="cross_org_relocate_baa_receipt_unauthorized",
        severity="sev1",
        description="A completed cross_org_site_relocate_requests row has a target_org_id whose baa_relocate_receipt_signature_id (and addendum_signature_id) are both NULL. Outside-counsel approval (2026-05-06) condition #2 requires that the receiving org's BAA or addendum expressly authorize receipt of transferred site compliance records, recorded as a signature_id on the org row. Endpoint check at target-accept refuses without it; this invariant catches post-execute drift (admin un-authorizing the org after relocate completed, or a code-path bypass that skipped the check). Mig 283.",
        check=lambda c: _check_cross_org_relocate_baa_receipt_unauthorized(c),
    ),
    Assertion(
        name="client_portal_zero_evidence_with_data",
        severity="sev2",
        description="An org with compliance_bundles in the last 7 days gets ZERO rows back when the canonical client-portal query is simulated under that org's RLS context. Catches the 2026-05-05 P0 regression class (mig 278) — RLS misalignment between substrate-owned data and the client portal's read view. Round-table 2026-05-05 Stage 4 closure.",
        check=lambda c: _check_client_portal_zero_evidence_with_data(c),
    ),
    Assertion(
        name="schema_fixture_drift",
        severity="sev3",
        description="The deployed code's tests/fixtures/schema/prod_columns.json differs from prod's information_schema. Catches the 'forgot to update fixture' deploy class — bit Session 214 audit cycle TWICE (mig 271 forward-merge required manual fixture edits). Sev3 because the test_sql_columns_match_schema CI gate already prevents new deploys with drift; this invariant catches the case where prod has drifted from the fixture in the CURRENTLY DEPLOYED code (rare, but happens via manual SQL or partial migration). Round-table 2026-05-02 Diana adversarial recommendation. Followup #49.",
        check=lambda c: _check_schema_fixture_drift(c),
    ),
    Assertion(
        name="chronic_without_l2_escalation",
        severity="sev2",
        description="An (site_id, incident_type) pair flagged is_chronic=TRUE in incident_recurrence_velocity has neither a matching l2_decisions.escalation_reason IN ('recurrence','recurrence_backfill') row in the last 24h nor an l2_escalations_missed disclosure row. Closes the Session 220 RT-P1 routing class — pre-fix the agent_api.py recurrence detector partitioned counts by appliance_id, so multi-daemon sites silently never tripped >=3-in-4h (320 missed L2 escalations / 7d at north-valley-branch-2). Forward fix: detector reads velocity table by (site_id, incident_type). This invariant catches regressions of the routing OR cases where the velocity loop stalled (cross-reference recurrence_velocity_stale).",
        check=lambda c: _check_chronic_without_l2_escalation(c),
    ),
    Assertion(
        name="l2_recurrence_partitioning_disclosed",
        severity="sev3",
        description="INFORMATIONAL — l2_escalations_missed carries rows disclosing historically-missed L2 escalations from the pre-2026-05-12 recurrence-detector partitioning bug. Round-table 2026-05-12 RT-P1 chose Option B (parallel disclosure table) over Option A (synthetic backfill into l2_decisions, rejected by Maya per Session 218 forgery precedent). This invariant keeps the disclosure surface visible on the substrate dashboard; auditor kit v2.2+ ships disclosures/missed_l2_escalations.json + SECURITY_ADVISORY_2026-05-12. Mirror of pre_mig175_privileged_unattested. Carved out of substrate_sla_breach.",
        check=lambda c: _check_l2_recurrence_partitioning_disclosed(c),
    ),
    Assertion(
        name="recurrence_velocity_stale",
        severity="sev3",
        description="One or more is_chronic=TRUE rows in incident_recurrence_velocity have computed_at older than 10 minutes — the freshness window the agent_api.py recurrence detector uses. Sev3 SPOF guard (Steve P0-B Gate A finding): bg_loop_silent (sev2) covers complete death of recurrence_velocity_loop; this catches the partial-degradation case where the loop runs slowly or specific chronic rows haven't been recomputed recently. Forward operation continues — new incidents still attempt the velocity read and log the stale signal — but chronic escalation may be delayed until the loop catches up.",
        check=lambda c: _check_recurrence_velocity_stale(c),
    ),
    # ── D1 heartbeat-signature invariants (Task #40, Counsel Rule 4) ──
    Assertion(
        name="daemon_heartbeat_unsigned",
        severity="sev2",
        description="An appliance whose site_appliances.agent_public_key IS SET has emitted ≥12 consecutive heartbeats in the last 60 minutes with NULL agent_signature (i.e. daemon is silently not signing heartbeats). Counsel Rule 4 orphan coverage at multi-device-enterprise fleet scale: a daemon that should be signing but isn't is potentially-compromised OR version-rolled-back OR daemon-bug. Threshold tuned per D1 RT 2026-05-13: 12-consecutive at ~5min cadence = ~1 hour of silent unsigned heartbeats before sev2 fires. Auto-resolves when the appliance emits a signed heartbeat. Runbook: substrate_runbooks/daemon_heartbeat_unsigned.md.",
        check=lambda c: _check_daemon_heartbeat_unsigned(c),
    ),
    Assertion(
        name="daemon_heartbeat_signature_invalid",
        severity="sev1",
        description="An appliance has emitted ≥3 heartbeats in the last 15 minutes with signature_valid=FALSE (i.e. signature is present but does NOT verify under any known pubkey, including previous_agent_public_key within the 15-minute rotation grace). Compromise-detection class — sev1 because either (a) the appliance's signing key has been replaced by an attacker, or (b) the canonical-payload format has drifted between daemon and backend lockstep. Runbook: substrate_runbooks/daemon_heartbeat_signature_invalid.md.",
        check=lambda c: _check_daemon_heartbeat_signature_invalid(c),
    ),
    Assertion(
        name="daemon_on_legacy_path_b",
        severity="sev3",
        description="INFORMATIONAL until 2026-08-13 deprecation deadline, then sev2. An appliance has emitted heartbeats with signature_canonical_format='v1b-reconstruct' (i.e. backend reconstructed the ±60s timestamp window because daemon did NOT supply heartbeat_timestamp — daemon version is pre-v0.5.0). After 2026-08-13 deprecation deadline, this becomes sev2: every appliance should have rolled forward to v0.5.0+ which supplies heartbeat_timestamp natively (path A). Allows fleet operators to track daemon-rollout progress. Runbook: substrate_runbooks/daemon_on_legacy_path_b.md.",
        check=lambda c: _check_daemon_on_legacy_path_b(c),
    ),
    Assertion(
        name="canonical_compliance_score_drift",
        severity="sev2",
        description="A customer-facing endpoint returned a compliance_score value that differs from the canonical helper output for the same inputs by more than 0.5. Counsel Rule 1 runtime half — pairs with the static AST gate (test_canonical_metrics_registry.py, Phase 0+1 shipped) which catches non-canonical-delegation drift. This invariant catches non-canonical-value drift (the endpoint went through a code path that produces a different value than the canonical helper would). Substrate samples 10% of customer-facing requests into canonical_metric_samples (Phase 2b decorator); this assertion verifies samples match canonical helper output. Runbook: substrate_runbooks/canonical_compliance_score_drift.md.",
        check=lambda c: _check_canonical_compliance_score_drift(c),
    ),
    Assertion(
        name="canonical_devices_freshness",
        severity="sev2",
        description="The 60s reconciliation loop that maintains canonical_devices (mig 319, Task #73) has not updated any row for an active site in >60min. Monthly compliance packet PDFs + device-inventory page may show stale counts. Counsel Rule 1 runtime parity — pairs with discovered_devices_freshness (existing). Runbook: substrate_runbooks/canonical_devices_freshness.md.",
        check=lambda c: _check_canonical_devices_freshness(c),
    ),
    Assertion(
        name="daemon_heartbeat_signature_unverified",
        severity="sev1",
        description="An appliance with a registered evidence-bundle public key has emitted ≥3 heartbeats in the last 15 minutes with `agent_signature IS NOT NULL` BUT `signature_valid IS NULL`. This is the verifier-crashed-silently class — backend tried to verify but hit an exception (e.g., ModuleNotFoundError on signature_auth import, missing pubkey row, decode failure) and stored NULL. Counsel Rule 4 PRIMARY (orphan-coverage — closes the gap that `daemon_heartbeat_unsigned` queries `agent_signature IS NULL` and `daemon_heartbeat_signature_invalid` queries `signature_valid IS NOT NULL`, so the NULL-despite-non-NULL-signature state silently slipped past both for ~13 days pre-fix 2026-05-13 adb7671a). Counsel Rule 3 SECONDARY — chain-of-custody for the signature chain. Sev1 because legitimate-but-unverified vs attacker-but-unverified are indistinguishable. Runbook: substrate_runbooks/daemon_heartbeat_signature_unverified.md.",
        check=lambda c: _check_daemon_heartbeat_signature_unverified(c),
    ),
    Assertion(
        name="sensitive_workflow_advanced_without_baa",
        severity="sev1",
        description="A BAA-gated sensitive workflow advanced in the last 30 days for a client_org with no active formal BAA (baa_status.baa_enforcement_ok=FALSE) and no legitimate carve-out. Tasks #52 + #92 + #98 (Counsel Rule 6 / §164.504(e)) — List 3 of the BAA-enforcement lockstep. 5 workflow scans: (1) cross_org_relocate via cross_org_site_relocate_requests, (2) owner_transfer via client_org_owner_transfer_requests, (3) evidence_export via admin_audit_log auditor_kit_download rows (denormalized site_id+client_org_id from commit 5ce77722, filtered to client_portal+partner_portal auth_methods), (4) new_site_onboarding via the sites table, (5) new_credential_entry via site_credentials JOIN sites. The CI gate test_baa_gated_workflows_lockstep.py catches an un-gated endpoint at build time; this invariant is the runtime backstop for a code path that bypassed require_active_baa / enforce_or_log_admin_bypass / check_baa_for_evidence_export OR an org whose BAA lapsed after the action. State-machine + admin-path advances write a baa_enforcement_bypass admin_audit_log row and are excluded; evidence_export uses raise-403 (no bypass row) and is method-aware (admin + legacy ?token= carved out). Runbook: substrate_runbooks/sensitive_workflow_advanced_without_baa.md.",
        check=lambda c: _check_sensitive_workflow_advanced_without_baa(c),
    ),
    Assertion(
        name="signing_backend_drifted_from_vault",
        severity="sev2",
        description="fleet_orders signed in the last hour with a signing_method that differs from the configured SIGNING_BACKEND_PRIMARY env. Task #45 / Vault P0 iter-4 Commit 2 (2026-05-16). Runtime backstop for the silent-fallback class — `signing_backend.current_signing_method()` had a silent except: return 'file' fallback that masked Vault errors; this invariant catches the resulting signing_method drift in fleet_orders. Shadow-mode carve-out: when SIGNING_BACKEND=shadow, compares against SIGNING_BACKEND_PRIMARY rather than the literal 'shadow'. Runbook: substrate_runbooks/signing_backend_drifted_from_vault.md.",
        check=_check_signing_backend_drifted_from_vault,
    ),
    Assertion(
        name="load_test_run_stuck_active",
        severity="sev2",
        description="A load_test_runs row in starting/running/aborting older than 6h indicates a k6 process that crashed or a wrapper that never called /complete. The partial unique index will block any NEW run until reaped. Task #62 v2.1 Commit 5a (2026-05-16). Runbook: substrate_runbooks/load_test_run_stuck_active.md.",
        check=_check_load_test_run_stuck_active,
    ),
    Assertion(
        name="load_test_run_aborted_no_completion",
        severity="sev3",
        description="A load_test_runs row with abort_requested_at set + status not terminal + abort > 30 min ago. The abort-bridge regressed — k6 should poll /status every iteration + exit within 30s. Task #62 v2.1 Commit 5a. Runbook: substrate_runbooks/load_test_run_aborted_no_completion.md.",
        check=_check_load_test_run_aborted_no_completion,
    ),
    Assertion(
        name="load_test_marker_in_compliance_bundles",
        severity="sev1",
        description="compliance_bundles row carries details->>'synthetic'='load_test'. Corrupts Ed25519 chain + flips auditor-kit determinism hash between consecutive downloads. CI gate test_no_load_test_marker_in_compliance_bundles covers structurally at build time; this is the runtime backstop. Task #62 v2.1 Commit 5a / Gate A P0-2 + P1-6. Runbook: substrate_runbooks/load_test_marker_in_compliance_bundles.md.",
        check=_check_load_test_marker_in_compliance_bundles,
    ),
    Assertion(
        name="synthetic_traffic_marker_orphan",
        severity="sev2",
        description="Synthetic-marked rows ('load_test' OR 'mttr_soak') in customer-facing aggregation tables (incidents / l2_decisions / evidence_bundles / aggregated_pattern_stats). Per v2.1 spec P0-3 marker unification — extends to plan-24's MTTR-soak marker. Task #62 v2.1 Commit 5a. Runbook: substrate_runbooks/synthetic_traffic_marker_orphan.md.",
        check=_check_synthetic_traffic_marker_orphan,
    ),
    Assertion(
        name="load_test_chain_contention_site_orphan",
        severity="sev2",
        description="compliance_bundles row exists for site_id='load-test-chain-contention-site' OUTSIDE an active load_test_runs window (no row covering the bundle's created_at). #117 Sub-commit B (mig 325). Fires when a production writer accidentally targets the load-test seed site — defends the Counsel Rule 4 'no silent orphan coverage' principle for synthetic infrastructure. The chain-contention test seeds 20 appliances + 20 bearers tied to this site; bundles are expected ONLY during k6 soak windows. Runbook: substrate_runbooks/load_test_chain_contention_site_orphan.md.",
        check=_check_load_test_chain_contention_site_orphan,
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
        "recommended_action": "Umbrella signal — fires on ANY sigauth fail "
            "(crypto + operational + enrollment). Check the conjunction with "
            "sigauth_crypto_failures: BOTH open = real crypto/attack signal "
            "(see that runbook); umbrella alone = enrollment debt or clock "
            "skew. Inspect details.reasons to classify. Common causes: "
            "appliance not yet enrolled (no provisioning_claim_events row), "
            "daemon clock skew > 5min, bad timestamp parsing.",
    },
    "sigauth_crypto_failures": {
        "display_name": "Agent signature CRYPTO-fails (priority signal)",
        "recommended_action": "Real crypto-level mismatch — wrong key, "
            "tampered body, replay, or canonical-input drift between "
            "deployed daemon and server. NOT enrollment debt. Steps: "
            "(1) verify the daemon's /var/lib/msp/agent.fingerprint "
            "matches v_current_appliance_identity.agent_pubkey_fingerprint "
            "for the SAME mac (NOT site_appliances.agent_public_key — "
            "that's the evidence-bundle key, a different key by design "
            "per Session 211 / #179); "
            "(2) check signature_auth.py canonical input vs "
            "phonehome.go::signRequest canonical (must be byte-identical); "
            "(3) if both check out, suspect active forgery or stolen key — "
            "rotate the appliance identity via fleet_cli rekey + revoke.",
    },
    "promotion_audit_log_recovery_pending": {
        "display_name": "Promotion audit log dead-letter queue has unrecovered rows",
        "recommended_action": "HIPAA §164.312(b) chain-of-custody is at risk. Each unrecovered row is "
            "an L1 rule promotion whose audit row did NOT land in promotion_audit_log. "
            "Run scripts/recover_promotion_audit_log.py to retry the INSERT (idempotent — "
            "safe to re-run). If the underlying failure persists (e.g. partition missing), "
            "fix the root cause first. Do NOT mark recovered=true manually without successfully "
            "INSERTing the audit row first — that creates a phantom recovery and breaks the chain.",
    },
    # Removed 2026-05-05 17:23Z (Session 217 close of task #4): the
    # sigauth_post_fix_window_canary acceptance window closed silent
    # at 2026-05-05 17:11Z. Display-metadata entry retired alongside
    # the Assertion + check function.
    "flywheel_orphan_telemetry": {
        "display_name": "Flywheel telemetry under a dead site_id",
        "recommended_action": "execution_telemetry has rows whose site_id has NO matching "
            "live site_appliances row. The flywheel aggregator will use those rows to "
            "manufacture phantom aggregated_pattern_stats entries which can then promote "
            "into a dead site (the candidate-253985 failure mode from 2026-04-29). "
            "Likely cause: a relocate/decommission cleanup that didn't cascade to "
            "execution_telemetry. Run the orphan_relocation migration pattern (252+254+255 "
            "as templates), confirm the live site_id is correct, and verify with "
            "scripts/db_delete_safety_check.py before the next deploy. Until cleared, "
            "the dashboard's promotion candidates may include phantom entries; do NOT "
            "approve any candidate whose site_id appears in this violation's details.",
    },
    "rename_site_immutable_list_drift": {
        "display_name": "Site-id table has DELETE-block trigger but isn't in immutable list",
        "recommended_action": "A site_id-bearing table is protected by a DELETE-blocking "
            "trigger (the standard audit-class / append-only signal) but is NOT in "
            "_rename_site_immutable_tables(). If rename_site() runs against a site_id "
            "currently in use, the function will rewrite this table's site_id "
            "transparently — a chain-of-custody violation if the table's append-only "
            "posture exists for HIPAA / cryptographic-binding reasons. Resolution: "
            "(a) ADD the table to _rename_site_immutable_tables() in a follow-on "
            "migration if it should be immutable (most likely), OR (b) DROP the "
            "DELETE-block trigger if the table is genuinely operational. Use the "
            "violation details to identify which side. Round-table review recommended "
            "before either path.",
    },
    "go_agent_heartbeat_stale": {
        "display_name": "Workstation agent silent > 6 hours",
        "recommended_action": "A workstation Go agent has not sent a "
            "heartbeat in 6+ hours. The state machine has already flipped "
            "the row's status to 'stale' / 'disconnected' / 'dead' depending "
            "on age. Operator action: (1) check whether the customer's "
            "workstation is genuinely offline (powered off, network issue, "
            "OS update reboot), (2) verify the Windows service "
            "'osiriscare-agent' is set to StartupType=Automatic on the host, "
            "(3) if persistent across multiple workstations from same site, "
            "investigate site-level network egress to api.osiriscare.net. "
            "Substrate's job ends at the alarm; partner contact (MSP) "
            "decides whether to notify the customer per their BAA. NEVER "
            "directly notify the clinic from substrate — that's BA territory. "
            "Excludes agent_version='dev' rows (chaos-lab targets are "
            "deliberately bouncy and shouldn't pollute the production-fleet "
            "dashboard).",
    },
    "appliance_offline_extended": {
        "display_name": "Appliance offline > 24 hours — phone the customer",
        "recommended_action": "An appliance has not checked in for 24+ hours. "
            "This is the escalation tier above offline_appliance_over_1h "
            "(sev2 fires at 1h). Where the 1-hour invariant means 'wait one "
            "cycle, probably transient,' this means 'something is genuinely "
            "wrong at the customer site.' Operator action: (1) check the "
            "customer's network status with the partner contact, (2) verify "
            "the appliance is powered and on a working LAN, (3) if multiple "
            "appliances from same site are over the 24h threshold, "
            "investigate site WAN. Note: this invariant does NOT auto-page "
            "the customer — the partner (MSP) holds the BAA and decides "
            "outreach. Substrate exposes the signal; the operator acts.",
    },
    "flywheel_federation_misconfigured": {
        "display_name": "Federation flag is ON but no tier is enabled+calibrated",
        "recommended_action": "FLYWHEEL_FEDERATION_ENABLED is set to a truthy value in "
            "the mcp-server environment, but no row in flywheel_eligibility_tiers has "
            "both enabled=TRUE AND calibrated_at IS NOT NULL. The flywheel read path "
            "is silently falling back to hardcoded defaults (5, 0.90, 3, 7) and "
            "logging a warning each loop tick. Resolution: (a) flip the env var off "
            "(if true intent is federation OFF — production is unchanged either way), "
            "OR (b) run the calibration migration that flips a tier to enabled=TRUE "
            "with a calibrated_at timestamp (true intent: federation ON with that "
            "tier's thresholds). The two-switch design is intentional defense-in-depth: "
            "both env AND tier must align before federation activates.",
    },
    "sigauth_enforce_mode_rejections": {
        "display_name": "Enforce-mode appliance had a sigauth rejection",
        "recommended_action": "Enforce mode is a 0%-fail contract; ANY "
            "rejection in 6h is a key-state coherence smell. Compare the "
            "daemon's identity fingerprint on disk "
            "(/var/lib/msp/agent.fingerprint AND /etc/osiriscare-identity.json — "
            "both should match) against site_appliances.agent_identity_public_key "
            "fingerprint server-side. If mismatch, daemon and server have drifted "
            "(stuck restart, dual-fingerprint-path bug, or partial rekey). "
            "Quick rollback: POST /api/admin/sigauth/demote/{appliance_id} "
            "with reason ≥10 chars to flip back to observe while you "
            "investigate. See substrate runbook for full diagnostic ladder.",
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
    "provisioning_network_fail": {
        "display_name": "Install stuck — installed system can't reach origin",
        "recommended_action": "LAN-scan http://<appliance-ip>:8443/ — "
            "install_gate_status.last_stage_failed names the exact broken stage. "
            "dns = whitelist api.osiriscare.net on the site's DNS filter. "
            "tcp_443 = allow outbound TCP/443 to 178.156.162.116. "
            "tls = exempt api.osiriscare.net from SSL-inspection. "
            "health = origin is down, contact OsirisCare on-call.",
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
    "installer_halted_early": {
        "display_name": "Installer posted /start once and then went silent",
        "recommended_action": "SSH in as msp with the ISO-embedded pubkey, then "
            "`sudo -i` via the Phase R break-glass passphrase (retrieve via "
            "/api/admin/appliance/{id}/break-glass, 5/hr rate-limited, audit-logged). "
            "Once root: `systemctl status msp-auto-provision.service` + "
            "`journalctl -u msp-auto-provision -n 100`. "
            "Typical cause as of v40.4: a DNS race before resolvconf has written "
            "/etc/resolv.conf. On v40.4+ msp-auto-provision has Restart=on-failure "
            "+ StartLimitBurst=10 so transient failures self-heal; if it's stuck at "
            "`failed` after the burst window the fix is a `systemctl restart` by "
            "operator. Also check port 8443 beacon — the beacon JSON names the broken "
            "stage. If msp-auto-provision is green but the daemon still isn't "
            "checking in, check for the daemon split-brain auth bug (sub-components "
            "holding stale api_key after auto-rekey — fixed in daemon 0.4.8, see "
            "audit item #5).",
    },
    "appliance_moved_unack": {
        "display_name": "Appliance physically relocated — unacknowledged",
        "recommended_action": "Acknowledge the move via the admin panel (or POST "
            "/api/admin/appliances/{appliance_id}/acknowledge-relocation) with a reason "
            "category + free-text detail. If this move was NOT expected, treat as a security "
            "incident: verify physical location, check for tampering, investigate shadow IT. "
            "HIPAA §164.310(d)(1) audit trail.",
    },
    "relocation_stalled": {
        "display_name": "Admin-initiated relocation stalled (>30 min pending)",
        "recommended_action": "Daemon never completed the move. Walk the diagnostic "
            "ladder: (1) is the appliance online; (2) does it report agent_version ≥ 0.4.11 "
            "(required for fleet_order path); (3) was a reprovision fleet_order issued "
            "(check details.fleet_order_id); (4) inspect the appliance's "
            "/var/lib/msp/appliance-daemon.log for the order ACK + any error. To recover: "
            "re-issue the relocate (same target_site_id) — endpoint is idempotent if the "
            "previous attempt has expired/failed. Or manually push via the ssh_snippet path "
            "(daemon < 0.4.11) using the new_api_key from the original response.",
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
    "fleet_order_fanout_partial_completion": {
        "display_name": "Fleet-order fan-out has K-of-N unacked at 6h+",
        "recommended_action": (
            "SEV2 — a privileged --all-at-site fan-out has one or "
            "more target appliances unacked > 6h. Investigate via: "
            "(1) `SELECT * FROM fleet_orders WHERE id = "
            "'<details.fleet_order_id>'` to see the target_appliance"
            "_id; (2) `SELECT * FROM site_appliances WHERE "  # noqa: site-appliances-deleted-include — operator-instruction example string in display_metadata, not an executable query
            "appliance_id = '<...>'` to check last_checkin + status; "
            "(3) if appliance is online but daemon isn't pulling, "
            "tail mcp-server logs for `appliance_id={...}` to find "
            "the auth-failure / fleet-order-pull errors. Common "
            "causes: appliance offline > 6h, daemon stuck on prior "
            "order, fleet_order_completion writer broken. If "
            "appliance is genuinely decommissioned, mark soft-"
            "delete + reissue fan-out with --target-appliance-id "
            "for the remaining live appliances. See substrate_"
            "runbooks/fleet_order_fanout_partial_completion.md."
        ),
    },
    "bundle_chain_position_gap": {
        "display_name": "Evidence chain has a position gap (chain-integrity violation)",
        "recommended_action": (
            "SEV1 — chain corruption. A site's compliance_bundles "
            "rows have a non-contiguous chain_position in the last "
            "24h. Auditor kit hash will flip between consecutive "
            "downloads for the affected site. "
            "Quarantine the affected bundle range "
            "(see details.chain_position + .prev_chain_position) — "
            "do NOT delete (HIPAA §164.316(b)(2)(i) 7-year retention "
            "+ mig 151 trg_prevent_audit_deletion would reject "
            "anyway). Find the writer that bypassed the per-site "
            "advisory lock: tail mcp-server logs for "
            "'evidence_chain' + 'create_compliance_bundle' around "
            "details.created_at. If the load test (Task #117) is "
            "running against this site, verify the synthetic-site "
            "carve-out matches the configured load-test site_id. "
            "See substrate_runbooks/bundle_chain_position_gap.md."
        ),
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
    "flywheel_ledger_stalled": {
        "display_name": "Flywheel spine ledger writes stalled",
        "recommended_action": "Grep mcp-server logs for "
            "'CheckViolationError' on promoted_rule_events or "
            "'safe_rollout_ledger_advance_failed'. Verify the three-list "
            "lockstep: Python flywheel_state.EVENT_TYPES vs the DB CHECK "
            "on promoted_rule_events.event_type vs the transition matrix. "
            "Run test_three_list_lockstep_pg.py — if it fails, land a "
            "migration that extends the DB CHECK to match the Python set. "
            "Cross-reference FLYWHEEL_ORCHESTRATOR_MODE (should be 'enforce' in prod).",
    },
    "nixos_rebuild_success_drought": {
        "display_name": "No nixos_rebuild has succeeded fleet-wide in 7d",
        "recommended_action": "Pull the latest failed order's output: "
            "`SELECT appliance_id, error_message, result FROM admin_orders "
            "WHERE order_type='nixos_rebuild' AND status='failed' "
            "ORDER BY completed_at DESC LIMIT 1;`. Pre-0.4.7 daemons truncate "
            "to 500 chars and hide the nix `error:` banner — upgrade to "
            "0.4.7+ then re-fire a canary on one appliance. Common causes: "
            "flake-eval regression from pinned-nixpkgs drift, lanzaboote hash "
            "mismatch, or a runtime-only option like boot.loader.systemd-boot "
            "that doesn't survive `nixos-rebuild test`. The 0.4.7 daemon "
            "writes the full log to /var/lib/msp/last-rebuild-error.log on "
            "the appliance — grab it via break-glass SSH if the error_message "
            "head+tail is still not enough.",
    },
    "appliance_disk_pressure": {
        "display_name": "Appliance /nix store out of space",
        "recommended_action": "Issue a nix_gc fleet order against the affected "
            "appliance: `fleet_cli.py create nix_gc --actor-email you@example.com "
            "--reason \"<20+ char reason>\" --param older_than_days=7 "
            "--param optimise=true`. The handler reclaims /nix/store generations "
            "older than N days and runs `nix-store --optimise` for hardlink "
            "dedup; returns before/after bytes in the completion payload. If "
            "disk stays tight after GC, the /nix partition itself is "
            "undersized — reprovision with a larger MSP-DATA partition (the "
            "disk image defaults to 20 GB for /nix, which is marginal once "
            "multiple generations accumulate). Cross-check "
            "nixos_rebuild_success_drought — disk pressure is the most common "
            "root cause of a silent rebuild failure.",
    },
    "frontend_field_undefined_spike": {
        "display_name": "Frontend reading fields the backend no longer returns",
        "recommended_action": "API contract drift has reached prod. Triage: "
            "(1) check the `details.endpoint` + `details.field_name` in the "
            "violation row; that's the broken contract. "
            "(2) grep the backend for the endpoint → find its Pydantic "
            "response model → is the field declared? "
            "(3) if the field was removed intentionally, update the frontend "
            "component that reads it, then regenerate api-generated.ts "
            "(`cd mcp-server/central-command/frontend && npm run generate-api`). "
            "(4) if the field was never there, the frontend has an outdated "
            "assumption — fix the component. "
            "(5) if the field SHOULD be there, check whether a recent backend "
            "deploy accidentally dropped it; roll back or hotfix. "
            "Auto-resolves when events stop arriving for that (endpoint, field) "
            "pair in a 5-minute window.",
    },
    "compliance_packets_stalled": {
        "display_name": "HIPAA monthly attestation packet missing",
        "recommended_action": "A site was operationally active in the prior "
            "month (emitted compliance_bundles) but compliance_packets has "
            "no row for that site+month+framework='hipaa'. HIPAA §164.316"
            "(b)(2)(i) requires 6-year retention. Steps: (1) check "
            "mcp-server logs for 'compliance_packet_autogen_failed' (Block "
            "3 added structured ERROR logging here). (2) Manually backfill "
            "via `POST /api/admin/compliance/packets/backfill` with site_id "
            "+ year + month. (3) If multiple sites fire simultaneously, "
            "_compliance_packet_loop is wedged — also expect bg_loop_silent "
            "to fire on it. Per non-operator partner posture: substrate "
            "exposes the gap; operator decides BAA-class disclosure.",
    },
    "partition_maintainer_dry": {
        "display_name": "Next-month partition missing on critical table",
        "recommended_action": "Partition coverage exhausted on a critical "
            "partitioned table (compliance_bundles / portal_access_log / "
            "appliance_heartbeats / promoted_rule_events / "
            "canonical_metric_samples). The "
            "partition_maintainer_loop is supposed to keep ≥3 months of "
            "forward partitions; if this fires, the loop is wedged or "
            "dead. Without next-month partitions, INSERTs land in the "
            "_default partition (bloats it; degrades query plans for "
            "auditor kits proportionally) or fail outright if no default "
            "exists (canonical_metric_samples has NO default partition — "
            "a wedged canonical_metric_samples_pruner_loop = INSERT "
            "failures). Steps: (1) check bg_loop_silent for "
            "'partition_maintainer' or 'heartbeat_partition_maintainer'. "
            "(2) Manually run `CREATE TABLE IF NOT EXISTS <parent>_<suffix>"
            " PARTITION OF <parent> FOR VALUES FROM (...) TO (...)` to "
            "unblock. (3) `docker compose restart mcp-server` to rearm "
            "the loop. Sev1 because the evidence chain depends on "
            "compliance_bundles partition health.",
    },
    "cross_org_relocate_chain_orphan": {
        "display_name": "Cross-org relocate without attestation — chain orphan",
        "recommended_action": (
            "A site has sites.prior_client_org_id set but no completed "
            "cross_org_site_relocate_requests row attests the move. "
            "Indicates the org change happened outside the attested "
            "flow (direct UPDATE, accidental backfill, etc.). "
            "Investigate which code path mutated the row, write a "
            "post-hoc attestation if the move was authorized, or "
            "reverse the change. RT21 (2026-05-05) Steve mit 4."
        ),
    },
    "cross_org_relocate_baa_receipt_unauthorized": {
        "display_name": "Cross-org relocate completed without BAA receipt-authorization",
        "recommended_action": (
            "A completed relocate's target org currently lacks both "
            "`baa_relocate_receipt_signature_id` and "
            "`baa_relocate_receipt_addendum_signature_id`. Outside-"
            "counsel approval (2026-05-06) condition #2 requires this "
            "signature exist at execute time and remain on the row. "
            "Investigate (a) whether contracts-team unset the column "
            "post-execute (business decision but should preserve "
            "history) OR (b) whether a code-path bypass skipped the "
            "endpoint receipt-auth check. If the move was authorized, "
            "re-record the signature_id from the original BAA review."
        ),
    },
    "client_portal_zero_evidence_with_data": {
        "display_name": "Client portal hiding evidence — RLS misalignment",
        "recommended_action": "An org with compliance_bundles in the "
            "last 7 days gets ZERO rows from the canonical client-"
            "portal query under its own RLS context. Same regression "
            "class as the 2026-05-05 P0 (mig 278). Steps: (1) Check "
            "what site-RLS table was added since mig 278 and confirm "
            "it has a tenant_org_isolation policy parallel to the "
            "ones in mig 278's DO-block. (2) Run "
            "tests/test_org_scoped_rls_policies.py locally — gate "
            "should fail loudly if a new in-scope table is missing "
            "the org policy. (3) Add the policy in a new migration "
            "modeled on mig 278. (4) Verify post-deploy by running "
            "the same simulation manually under fork psql: BEGIN; "
            "SET LOCAL app.current_org='<id>'; SET LOCAL "
            "app.is_admin='false'; SET LOCAL app.current_tenant=''; "
            "SELECT COUNT(*) FROM compliance_bundles WHERE site_id "
            "IN (SELECT site_id FROM sites WHERE client_org_id::text "
            "= current_setting('app.current_org')); ROLLBACK; — must "
            "match admin-side count. Sev2 because customers cannot see "
            "their own evidence chain → trust break on trust-bearing "
            "platform.",
    },
    "email_dlq_growing": {
        "display_name": "Email DLQ growing — outbound email pipeline failing",
        "recommended_action": "More than 5 unresolved rows in "
            "email_send_failures (mig 272 DLQ) within the last 24h on a "
            "single label. Class candidates: SMTP outage at "
            "mail.privateemail.com, SMTP_USER/SMTP_PASSWORD env break, "
            "DKIM/SPF DNS misalignment, recipient-side bounce. Steps: "
            "(1) `SELECT label, error_class, error_message, retry_count "
            "FROM email_send_failures WHERE resolved_at IS NULL ORDER BY "
            "failed_at DESC LIMIT 20` — distinguishes SMTP-class from "
            "auth-class from per-recipient bounces. (2) If SMTPException "
            "+ TimeoutError → check mail.privateemail.com from VPS via "
            "`telnet mail.privateemail.com 587`. (3) If "
            "SMTPAuthenticationError → rotate SMTP_PASSWORD. (4) Once "
            "root cause is fixed, mark resolved via `UPDATE "
            "email_send_failures SET resolved_at = NOW(), "
            "resolution_note = '...' WHERE id = ANY($1::bigint[])` for "
            "the affected rows. The cryptographic chain is unaffected "
            "by email failures (audit row + Ed25519 already committed); "
            "only the operator-visibility echo is delayed.",
    },
    "schema_fixture_drift": {
        "display_name": "Prod schema differs from deployed code's fixture",
        "recommended_action": "Prod's information_schema differs from "
            "tests/fixtures/schema/prod_columns.json in the deployed "
            "code. The test_sql_columns_match_schema CI gate prevents "
            "new deploys with drift, so this firing means EITHER (a) a "
            "manual SQL ALTER ran on prod outside the migration system "
            "(bypassed CI — investigate audit log), OR (b) a migration "
            "applied but the fixture wasn't forward-merged in the same "
            "PR (deploy slipped past the gate). Each violation row "
            "names one (table, column) drift. To fix: verify the "
            "drift is intentional, then update prod_columns.json to "
            "match prod (run the regen command in the test file's "
            "docstring) and ship as a fixture-only commit. Sev3 "
            "because the deployed code is functioning; the gap is in "
            "future-CI signal accuracy. Followup #49.",
    },
    "substrate_assertions_meta_silent": {
        "display_name": "Substrate watcher itself is silent (meta)",
        "recommended_action": "The substrate engine's own assertions_loop "
            "has not heartbeat in 3+ min — the dashboard's 'all-clear' state "
            "is MEANINGLESS until this resolves, because the watcher is the "
            "thing that produces violations. (1) Check mcp-server logs for "
            "'substrate_assertions' tick lines stopping abruptly. (2) Check "
            "for asyncpg pool exhaustion (`SELECT count(*) FROM pg_stat_activity "
            "WHERE datname='mcp'`). (3) Check for stuck await on a HTTP fetch "
            "(rare — substrate assertions are DB-only). (4) `docker compose "
            "restart mcp-server` if no obvious cause; container restart "
            "force-restarts the supervisor + the loop.",
    },
    "bg_loop_silent": {
        "display_name": "Background loop stuck (no heartbeat for 3x cadence)",
        "recommended_action": "A registered background loop has stopped "
            "writing heartbeats. _supervised auto-restarts on EXCEPTIONS but "
            "stuck awaits are not exceptions — the task hangs forever. "
            "details.loop names the offender; check mcp-server logs filtered "
            "for that loop's name. Common causes: asyncpg pool exhaustion, "
            "deadlock against another loop, hung HTTP fetch (LLM, OTS calendar, "
            "GitHub API). `docker compose restart mcp-server` is the blunt "
            "fix; root-cause via the log filter first.",
    },
    "synthetic_l2_pps_rows": {
        "display_name": "Synthetic L2-* runbook_id rows in platform_pattern_stats",
        "recommended_action": "Cleanup: `DELETE FROM platform_pattern_stats WHERE runbook_id LIKE 'L2-%'`. "
            "Then grep recent commits for platform_pattern_stats INSERT code and verify the "
            "`NOT LIKE 'L2-%'` filter at background_tasks.py:1189 is still present in the "
            "deployed container (`docker exec mcp-server grep -n \"NOT LIKE 'L2-%'\" "
            "/app/dashboard_api/background_tasks.py`). If the filter is intact, the "
            "resurrection mechanism is external (pg_restore or manual SQL) — check recent "
            "DBA activity. Rows are HARMLESS in the promotion path (distinct_orgs=1 < 5 "
            "default threshold) but indicate infrastructure drift worth investigating.",
    },
    "l2_decisions_stalled": {
        "display_name": "L2 LLM decisions silently stalled",
        "recommended_action": "L2 is enabled but producing <5 decisions per 48h "
            "with active appliances. Walk the cost-gate stack in order: "
            "(1) verify LLM API key is valid + has credit — `docker exec mcp-server env | "
            "grep -E 'ANTHROPIC|OPENAI|AZURE'`; "
            "(2) check the circuit breaker — `docker logs mcp-server | grep -E 'L2 circuit breaker'` — "
            "if OPENED, either wait for 15m cooldown or restart mcp-server to force reset; "
            "(3) check daily call cap — `docker logs mcp-server | grep 'daily_limit_reached'` — "
            "raise MAX_DAILY_L2_CALLS if legitimate load; "
            "(4) check zero-result circuit clamps — `SELECT site_id, pattern_signature, COUNT(*) "
            "FROM l2_decisions WHERE runbook_id IS NULL AND created_at > NOW() - INTERVAL '24 hours' "
            "GROUP BY 1,2 HAVING COUNT(*) >= 2` — these pairs are paused until next UTC midnight; "
            "(5) if L2 is meant to be disabled, set L2_ENABLED=false in docker-compose.yml and "
            "restart mcp-server — this invariant goes silent automatically.",
    },
    "unbridged_telemetry_runbook_ids": {
        "display_name": "Telemetry runbook_ids unbridged to runbooks table",
        "recommended_action": (
            "execution_telemetry has runbook_ids that don't match any "
            "runbooks.agent_runbook_id (or runbook_id). Pre-mig-284, the "
            "agent's L1-* IDs and the backend's RB-*/LIN-* IDs were "
            "unbridged for months. To clear: (a) for each unbridged ID "
            "shown, decide whether it corresponds to an existing runbook "
            "(then UPDATE runbooks SET agent_runbook_id = '<id>' WHERE "
            "runbook_id = '<canonical>') OR a new agent rule (then "
            "INSERT a new row with runbook_id='AGENT-<id>' and "
            "agent_runbook_id='<id>'). Ship the change as a numbered "
            "migration. RT-DM Issue #1 (2026-05-06)."
        ),
    },
    "l2_resolution_without_decision_record": {
        "display_name": "L2 resolution without LLM decision record",
        "recommended_action": (
            "An incident has resolution_tier='L2' but no l2_decisions "
            "row references it. Either (a) the agent_api code path "
            "that wrote tier='L2' did not call record_l2_decision() — "
            "regression — find the offending code path, OR (b) the "
            "incident was tier-set manually for ops reasons; document "
            "by inserting an l2_decisions row with reasoning='manual "
            "tier override per <ticket>'. RT-DM Issue #2 hardening "
            "(non-consensus, 2026-05-06). Pinned by "
            "tests/test_l2_canonical_view_used.py."
        ),
    },
    "l1_resolution_without_remediation_step": {
        "display_name": "L1 resolution without remediation step",
        "recommended_action": (
            "An incident has resolution_tier='L1' but no "
            "incident_remediation_steps row references it. Auto-resolve "
            "path tagged 'L1' without recording the runbook execution. "
            "Phase 2 root-cause investigation pending — likely an auto-"
            "resolve race in sites.py checkin handler beating daemon "
            "healing_executor's ReportHealed callback. Recommended: "
            "until Phase 2 names the offending callsite, treat trending-"
            "up violations as evidence the race is widening. Pinned by "
            "tests/test_l1_resolution_requires_remediation_step.py. "
            "Session 219 (2026-05-11)."
        ),
    },
    "compliance_bundles_trigger_disabled": {
        "display_name": "Chain-of-custody trigger DISABLED — integrity guard degraded",
        "recommended_action": (
            "compliance_bundles_no_delete trigger is in a non-ALWAYS state. "
            "Run: ALTER TABLE <schema>.<table> ENABLE ALWAYS TRIGGER "
            "compliance_bundles_no_delete; against the named table. The trigger "
            "is the last-line defense against bulk-DELETE on the chain-of-"
            "custody evidence table. If you DISABLED it for a one-shot "
            "cleanup, RE-ENABLE it immediately. Sev1 because every minute "
            "the trigger is off is a minute of customer-visible "
            "tamper-evidence integrity risk."
        ),
    },
    "db_baseline_guc_drift": {
        "display_name": "DB GUC drift — RLS posture compromised",
        "recommended_action": (
            "A load-bearing GUC has drifted from the tenant-safety baseline. "
            "Run psql `RESET <guc>` against the running database OR find the "
            "migration that mistakenly altered the default. "
            "Baseline: app.is_admin='false', app.current_tenant='', "
            "app.current_org='', app.current_partner_id=''. If a permissive "
            "value is intentional (rare!), document the rationale and add to "
            "_check_db_baseline_guc_drift.BASELINE_GUCS with round-table "
            "sign-off."
        ),
    },
    "substrate_sla_breach": {
        "display_name": "Substrate SLA breach — invariant open beyond response window",
        "recommended_action": (
            "META invariant. A non-meta sev1/sev2 invariant has been "
            "open beyond its per-severity SLA (sev1 ≤4h, sev2 ≤24h, "
            "sev3 ≤30d). The engine is firing correctly; the response "
            "loop is not. Resolution: (1) read the runbook for the "
            "breached invariant (named in details.breached_invariant); "
            "(2) execute the operator action it documents; (3) if the "
            "invariant is intentionally long-open by design, add it "
            "to `_check_substrate_sla_breach.LONG_OPEN_BY_DESIGN` "
            "with explicit round-table sign-off. NEVER add a carve-"
            "out to silence an alert that should drive action — that "
            "defeats the purpose of the SLA. Sev2 because: the engine "
            "is the customer-facing trust signal; sustained alert "
            "backlog erodes that trust."
        ),
    },
    "pre_mig175_privileged_unattested": {
        "display_name": "Pre-mig-175 privileged orders unattested (disclosed)",
        "recommended_action": (
            "INFORMATIONAL ONLY. Three privileged fleet_orders rows "
            "on north-valley-branch-2 pre-date migration 175's "
            "chain-of-custody trigger. New violations are "
            "structurally impossible. Disclosure path chosen over "
            "backfill (round-table 2026-05-08 RT-1.2). Auditors "
            "have public security advisory "
            "OSIRIS-2026-04-13-PRIVILEGED-PRE-TRIGGER on file in the "
            "auditor kit's disclosures/ folder. NO ACTION REQUIRED — "
            "this invariant exists for operator visibility so future "
            "operators see the disclosure without archaeology. The "
            "rows themselves are append-only; resolution is not "
            "deletion but documented disclosure."
        ),
    },
    "merkle_batch_stalled": {
        "display_name": "Merkle batch worker stalled — evidence not anchoring",
        "recommended_action": (
            "compliance_bundles rows pinned at ots_status='batching' "
            "for >6 hours indicate the hourly _merkle_batch_loop is "
            "not transitioning evidence toward OTS anchoring. "
            "Investigate: (1) `docker logs mcp-server | grep -E "
            "'Merkle batch|merkle_batch'` — is the loop firing? "
            "Look for `bg_task_started task=merkle_batch` AND a "
            "subsequent `Merkle batch created` line. (2) Check the "
            "OTS calendar: `curl -sS https://alice.btc.calendar.opentimestamps.org/`"
            " — if the calendar is down, OTS submissions silently "
            "fail (process_merkle_batch returns batched=0 with "
            "error=ots_submission_failed). (3) Verify admin context "
            "is reaching the loop: `docker exec mcp-server python -c "
            "'import asyncio; from dashboard_api.fleet import get_pool; "
            "from dashboard_api.tenant_middleware import admin_transaction; "
            "asyncio.run(...)'`. (4) Manual unstall — see runbook "
            "audit/round-table-closeout-2026-05-08.md §RT-1.1. "
            "Sev1 because a stalled batcher means evidence is not "
            "tamper-evidenced — auditors will catch the gap."
        ),
    },
    "orders_stuck_acknowledged": {
        "display_name": "Orders stuck in acknowledged/executing past timeout",
        "recommended_action": (
            "Order ack'd by appliance but no completion telemetry "
            "received within the timeout window (30 min for "
            "acknowledged, 1 hour for executing). Check: (a) is "
            "background_tasks.sweep_stuck_orders_loop running? "
            "`docker logs mcp-server | grep sweep_stuck_orders` — if "
            "absent, the sweeper is offline; (b) is the agent's "
            "telemetry path reaching the backend? "
            "execution_telemetry rows for the affected appliance in "
            "the last hour count > 0? (c) is the agent emitting "
            "order_id in telemetry metadata? `SELECT metadata->>'order_id' "
            "FROM execution_telemetry WHERE created_at > NOW() - "
            "INTERVAL '1 hour' AND metadata ? 'order_id'`. If volume is "
            "high, run `SELECT * FROM sweep_stuck_orders()` manually "
            "to clear the backlog. RT-DM Issue #3 (2026-05-06)."
        ),
    },
    "chronic_without_l2_escalation": {
        "display_name": "Chronic pattern without L2 root-cause analysis",
        "recommended_action": (
            "An (site, incident-type) pair is flagged chronic in the "
            "recurrence velocity table but has no matching L2 root-cause "
            "row in the last 24h and no disclosure entry in "
            "l2_escalations_missed. Order of investigation: (a) check "
            "recurrence_velocity_stale — if also firing, the velocity "
            "loop is behind; let it catch up. (b) check bg_loop_silent "
            "for recurrence_velocity_loop. (c) confirm the agent_api.py "
            "recurrence detector is still reading from "
            "incident_recurrence_velocity by (site_id, incident_type) — "
            "if the SELECT regressed to per-appliance, the multi-daemon "
            "partitioning bug is back. (d) if the row predates the "
            "2026-05-12 detector switch, INSERT a one-shot row into "
            "l2_escalations_missed disclosing it."
        ),
    },
    "l2_recurrence_partitioning_disclosed": {
        "display_name": "Historical recurrence misses disclosed",
        "recommended_action": (
            "INFORMATIONAL — l2_escalations_missed carries rows from the "
            "pre-2026-05-12 recurrence-detector partitioning bug. The "
            "forward fix shipped on 2026-05-12 (Session 220 RT-P1); the "
            "missed escalations are disclosed in auditor-kit v2.2+ "
            "(disclosures/missed_l2_escalations.json + "
            "SECURITY_ADVISORY_2026-05-12_RECURRENCE_DETECTOR_PARTITIONING.md). "
            "Mirror of pre_mig175_privileged_unattested. No auto-heal "
            "exists; backfill into l2_decisions would fabricate evidence "
            "of LLM calls that never ran (Session 218 forgery precedent). "
            "Resolves only when the disclosure surface is closed by a "
            "future round-table grandfathering decision."
        ),
    },
    "recurrence_velocity_stale": {
        "display_name": "Recurrence velocity data is stale",
        "recommended_action": (
            "One or more chronic-pattern rows in "
            "incident_recurrence_velocity have computed_at older than "
            "10 minutes — the freshness window the recurrence detector "
            "uses. Check bg_loop_silent for recurrence_velocity_loop "
            "first. If healthy, the row simply hasn't been touched by a "
            "recent recompute pass (no new incidents in the rolling "
            "windows) — non-actionable, will resolve. If bg_loop_silent "
            "is also firing, restart the background_tasks loop. Sev3 "
            "SPoF guard (Steve P0-B, Gate A 2026-05-12)."
        ),
    },
    "daemon_heartbeat_unsigned": {
        "display_name": "Daemon is silently NOT signing heartbeats",
        "recommended_action": (
            "Appliance has agent_public_key on file but emitted ≥12 "
            "heartbeats in the last 60 minutes with NULL agent_signature. "
            "Daemon should be signing every heartbeat per "
            "phonehome.go:827 SystemInfoSigned. Investigate (a) daemon "
            "version, (b) evidence-submitter signing-key state, (c) "
            "appliance-side signing-loop errors in daemon slog. If the "
            "appliance was recently re-flashed, verify site_appliances. "
            "agent_public_key matches the new daemon's key (15-min "
            "rotation grace via previous_agent_public_key). Counsel "
            "Rule 4 orphan-coverage. See "
            "substrate_runbooks/daemon_heartbeat_unsigned.md."
        ),
    },
    "daemon_heartbeat_signature_invalid": {
        "display_name": "Daemon signature does NOT verify — potential compromise OR canonical-format drift",
        "recommended_action": (
            "SEV1 — escalate to operator. ≥3 heartbeats in the last 15 "
            "minutes carry signature_valid=FALSE. Either (a) signing key "
            "compromised (rotate agent_public_key, isolate appliance, "
            "investigate), or (b) canonical-payload format drifted "
            "between daemon and backend (diff phonehome.go:837 vs "
            "signature_auth.py::_heartbeat_canonical_payload — all 4 "
            "lockstep surfaces must agree). Counsel Rule 4 compromise "
            "detection. See substrate_runbooks/daemon_heartbeat_"
            "signature_invalid.md."
        ),
    },
    "daemon_on_legacy_path_b": {
        "display_name": "Daemon using legacy path-B heartbeat verification (pre-v0.5.0)",
        "recommended_action": (
            "Informational until 2026-08-13 deprecation deadline, then "
            "auto-escalates to sev2. Appliance is on pre-v0.5.0 daemon "
            "that does not supply heartbeat_timestamp natively — backend "
            "verifies via path B (±60s reconstruction). Upgrade the "
            "daemon to v0.5.0+ before the deadline to switch to path A "
            "(daemon-supplied timestamp, deterministic, auditor-preferred). "
            "Tracks fleet-rollout progress. See "
            "substrate_runbooks/daemon_on_legacy_path_b.md."
        ),
    },
    "canonical_compliance_score_drift": {
        "display_name": "Customer-facing compliance score diverges from canonical helper",
        "recommended_action": (
            "A customer-facing endpoint returned a compliance_score value "
            "more than 0.5 different from what compute_compliance_score "
            "produces for the same inputs. Inspect details.endpoint_path "
            "+ details.delta to identify which surface drifted. Likely "
            "uses one of the allowlist 'migrate'-class entries in "
            "canonical_metrics.py — drive-down PR should migrate that "
            "endpoint to delegate to the canonical helper. See "
            "substrate_runbooks/canonical_compliance_score_drift.md."
        ),
    },
    "daemon_heartbeat_signature_unverified": {
        "display_name": "Daemon signature stored NULL — verifier crashed silently",
        "recommended_action": (
            "An appliance signed its heartbeat, but backend's verifier "
            "threw an exception while validating + stored NULL instead "
            "of TRUE/FALSE. This is the detection-gap class that masked "
            "D1 inert state for ~13 days pre-2026-05-13 (commit "
            "adb7671a). Investigate: (1) check mcp-server logs for "
            "ModuleNotFoundError or other exceptions in "
            "appliance_checkin's signature_auth import path; (2) "
            "verify site_appliances.agent_public_key for the affected "
            "appliance is non-NULL + correctly formatted; (3) verify "
            "the canonical-payload reconstruction in verify_heartbeat_"
            "signature doesn't have a code drift. See "
            "substrate_runbooks/daemon_heartbeat_signature_unverified.md."
        ),
    },
    "canonical_devices_freshness": {
        "display_name": "Canonical Devices Reconciliation Loop Stale",
        "recommended_action": (
            "The 60s background loop that maintains canonical_devices "
            "(mig 319) has not updated rows for an active site in "
            ">60min. Monthly compliance packet PDFs + the device-"
            "inventory page may show stale counts (the underlying "
            "discovered_devices data is still being collected — only "
            "the deduplicated view is stale). Check /admin/substrate-"
            "health for bg_loop_silent OR inspect ERROR logs for "
            "canonical_devices UPSERT failures. See "
            "substrate_runbooks/canonical_devices_freshness.md."
        ),
    },
    "sensitive_workflow_advanced_without_baa": {
        "display_name": "Sensitive workflow advanced without an active BAA",
        "recommended_action": (
            "A BAA-gated workflow (cross_org_relocate, owner_transfer, "
            "or evidence_export) advanced in the last 30 days for a "
            "client_org with no active formal BAA and no legitimate "
            "carve-out. Investigate: (1) confirm the org's BAA state "
            "via baa_status.baa_signature_status() — if the BAA "
            "genuinely lapsed AFTER the action, this is a real "
            "§164.504(e) gap; (2) if the org never had a BAA, a code "
            "path bypassed require_active_baa / enforce_or_log_admin_"
            "bypass / check_baa_for_evidence_export — find and gate "
            "it; (3) for cross_org_relocate or owner_transfer, if it "
            "WAS a legitimate admin action, the baa_enforcement_bypass "
            "audit row is missing — fix the admin path to log it. "
            "evidence_export does NOT use bypass rows (raises 403 "
            "instead), so a violation there is unambiguously a gate-"
            "bypass or post-action BAA lapse. See "
            "substrate_runbooks/sensitive_workflow_advanced_without_baa.md."
        ),
    },
    "signing_backend_drifted_from_vault": {
        "display_name": "Signing backend drifted from configured primary",
        "recommended_action": (
            "SEV2 — fleet_orders signed in the last hour include "
            "signing_method values other than the configured SIGNING_"
            "BACKEND_PRIMARY env. Either (a) a code path silently fell "
            "back to a different backend — tail mcp-server logs for "
            "'current_signing_method_fallback' errors, find the "
            "exception that triggered the fallback; OR (b) operator "
            "changed the env mid-hour without coordinated restart, "
            "in which case the invariant clears on next deploy. "
            "Investigate via SELECT signing_method, COUNT(*) FROM "
            "fleet_orders WHERE created_at > NOW() - INTERVAL '1 hour' "
            "GROUP BY 1. See substrate_runbooks/signing_backend_"
            "drifted_from_vault.md."
        ),
    },
    "load_test_run_stuck_active": {
        "display_name": "Load-test run stuck active beyond 6h",
        "recommended_action": (
            "k6 crashed or its wrapper never called /complete on a "
            "load-harness run. The partial unique index will block "
            "any NEW run until this one transitions to a terminal "
            "state. Reap via POST /api/admin/load-test/{run_id}/"
            "complete with final_status='failed'. See "
            "substrate_runbooks/load_test_run_stuck_active.md."
        ),
    },
    "load_test_run_aborted_no_completion": {
        "display_name": "Load-test abort not honored within 30 min",
        "recommended_action": (
            "Operator or AlertManager requested abort > 30 min ago "
            "but k6 has not transitioned to terminal. The abort-poll "
            "bridge regressed — k6 should poll /status every iteration "
            "+ exit within 30s. Force-terminate: POST /api/admin/load-"
            "test/{run_id}/complete with final_status='failed' + "
            "pkill -9 k6 on CX22. See substrate_runbooks/load_test_"
            "run_aborted_no_completion.md."
        ),
    },
    "load_test_marker_in_compliance_bundles": {
        "display_name": "Synthetic marker in evidence chain — CHAIN INTEGRITY",
        "recommended_action": (
            "SEV1 — chain-integrity event. A compliance_bundles row "
            "carries details->>'synthetic'='load_test'. Auditor-kit "
            "determinism hash will flip between consecutive downloads "
            "for the affected site. Page on-call security. Quarantine "
            "the row (do NOT delete), find the writer "
            "(git log -S 'load_test' --since=<days> + grep INSERT "
            "INTO compliance_bundles), re-run the chain verifier for "
            "blast-radius assessment. If a customer auditor has "
            "already downloaded a kit containing the row, loop in "
            "counsel. See substrate_runbooks/load_test_marker_in_"
            "compliance_bundles.md."
        ),
    },
    "synthetic_traffic_marker_orphan": {
        "display_name": "Synthetic marker in customer aggregation",
        "recommended_action": (
            "Load-harness or MTTR-soak traffic leaked into a customer-"
            "facing aggregation table (incidents / l2_decisions / "
            "evidence_bundles / aggregated_pattern_stats). Add the "
            "universal IS NOT TRUE filter pattern at the writer "
            "callsite + re-aggregate the affected customer metric. "
            "Sev2 (vs the sev1 sibling on compliance_bundles): no "
            "crypto chain corruption, just visibility leak. See "
            "substrate_runbooks/synthetic_traffic_marker_orphan.md."
        ),
    },
    "load_test_chain_contention_site_orphan": {
        "display_name": "Load-test site orphan bundle (synthetic infra)",
        "recommended_action": (
            "SEV2 — a compliance_bundles row exists for the load-"
            "test seed site OUTSIDE any active load_test_runs window. "
            "A production writer is accidentally targeting the "
            "synthetic infrastructure. Identify the writer via psql "
            "(SELECT bundle_id, check_type, created_at FROM compliance_"  # noqa: site-appliances-deleted-include
            "bundles WHERE site_id = 'load-test-chain-contention-site' "
            "ORDER BY created_at DESC LIMIT 20) + git log -S 'load-"
            "test-chain-contention-site' --since=<days>. Quarantine "
            "the row (do NOT delete — §164.316(b)(2)(i) 7y retention "
            "+ mig 151 trg_prevent_audit_deletion). Counsel Rule 4: "
            "synthetic infra orphan detection is sev2; if the bundle "
            "originated from a customer-facing endpoint, escalate to "
            "sev1 + page on-call. See substrate_runbooks/load_test_"
            "chain_contention_site_orphan.md."
        ),
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


# Adversarial-reason subset of sigauth fail reasons. These mean the
# signature itself is broken — wrong key, replay attack, malformed
# signature, or canonical-input drift between daemon and server.
# Distinct from the operational subset (unknown_pubkey, clock_skew,
# bad_timestamp, bad_nonce, no_headers) which signal enrollment debt
# or daemon misconfiguration, NOT crypto compromise.
#
# Note: `bad_body_hash` is a documented reason code in
# `signature_auth.py::SignatureVerifyResult` but is never emitted as a
# distinct reason — body-hash mismatches surface as `invalid_signature`
# because the body hash is folded into the canonical signed input and
# Ed25519 verify fails as a unit. So the canonical reason set is
# explicitly the four below.
CRYPTO_FAIL_REASONS = frozenset({
    "invalid_signature",
    "bad_signature_format",
    "nonce_replay",
})


async def _check_signature_verification_failures(conn: asyncpg.Connection) -> List[Violation]:
    """Per-site fail rate over the last hour of observed signatures.

    Umbrella signal: ANY sigauth fail counts (crypto + operational +
    enrollment). Floor of 5 samples to avoid false-flagging on a fresh
    site that happens to have its first checkin fail. Threshold of 5%.

    Priority sub-signal lives in `_check_sigauth_crypto_failures` and
    fires only on the adversarial-reason subset — see CRYPTO_FAIL_REASONS.
    Both invariants can fire simultaneously; the conjunction means a
    real attack/drift, the umbrella alone means enrollment debt."""
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


async def _check_sigauth_crypto_failures(conn: asyncpg.Connection) -> List[Violation]:
    """Per-site rate of CRYPTO-level sigauth fails — wrong key, tampered
    body, replay, canonical drift. Distinct from the umbrella
    `signature_verification_failures` invariant: this one filters to the
    adversarial-reason subset and fires at sev1 priority because a
    sustained crypto-fail rate is a security signal, not an operational
    one.

    When BOTH this and `signature_verification_failures` are open for
    the same site, treat as a security incident (real key compromise,
    canonical-input drift between deployed daemon and server, or active
    forgery). When only the umbrella fires (and this one doesn't),
    treat as enrollment / clock-skew debt — annoying but not a breach."""
    crypto_reasons = list(CRYPTO_FAIL_REASONS)
    rows = await conn.fetch(
        """
        SELECT site_id,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE valid = false AND reason = ANY($1::text[])) AS crypto_failures,
               array_agg(DISTINCT reason)
                  FILTER (WHERE valid = false AND reason = ANY($1::text[])) AS reasons
          FROM sigauth_observations
         WHERE observed_at > NOW() - INTERVAL '1 hour'
      GROUP BY site_id
        HAVING COUNT(*) >= 5
           AND COUNT(*) FILTER (WHERE valid = false AND reason = ANY($1::text[]))
                * 100.0 / COUNT(*) > 5
        """,
        crypto_reasons,
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "total_samples": r["total"],
                "crypto_failures": r["crypto_failures"],
                "fail_rate_pct": round(r["crypto_failures"] * 100.0 / r["total"], 1),
                "reasons": list(r["reasons"] or []),
                "classification": "adversarial",
            },
        )
        for r in rows
    ]


async def _check_promotion_audit_log_recovery_pending(conn: asyncpg.Connection) -> List[Violation]:
    """HIPAA §164.312(b) chain-of-custody durability check (Migration 253,
    Session 212 round-table P0). Fires sev1 when promotion_audit_log_recovery
    has any row with recovered=false: an L1 rule promotion happened but
    its audit row never made it to promotion_audit_log."""
    rows = await conn.fetch(
        """
        SELECT COUNT(*)                          AS total_pending,
               MIN(queued_at)                    AS oldest_queued_at,
               MAX(queued_at)                    AS newest_queued_at,
               array_agg(DISTINCT failure_class) AS failure_classes,
               array_agg(DISTINCT site_id)
                  FILTER (WHERE site_id IS NOT NULL) AS affected_sites
          FROM promotion_audit_log_recovery
         WHERE recovered = FALSE
        """
    )
    if not rows or not rows[0]["total_pending"]:
        return []
    r = rows[0]
    return [
        Violation(
            site_id=None,
            details={
                "total_pending": int(r["total_pending"]),
                "oldest_queued_at": r["oldest_queued_at"].isoformat() if r["oldest_queued_at"] else None,
                "newest_queued_at": r["newest_queued_at"].isoformat() if r["newest_queued_at"] else None,
                "failure_classes": list(r["failure_classes"] or []),
                "affected_sites": list(r["affected_sites"] or []),
                "remediation": "Run scripts/recover_promotion_audit_log.py — idempotent retry for each unrecovered row.",
            },
        )
    ]


async def _check_flywheel_orphan_telemetry(conn: asyncpg.Connection) -> List[Violation]:
    """Detect execution_telemetry rows under dead site_ids — the
    upstream class of bug that defeated migrations 252 + 254 on
    2026-04-29 by causing the flywheel aggregator to recreate orphan
    aggregated_pattern_stats rows. Round-table F3 P0 (2026-04-29).

    Fires sev1 per dead-site_id with >10 telemetry rows in the last
    24h. The 24h window + >10 floor avoids noise from ephemeral
    relocate windows where a brief overlap is expected; sustained
    orphan telemetry is the failure signal.

    Future architectural fix (F1 next session): replace this detector
    with a canonicalized aggregation view that joins through
    site_appliances, eliminating the orphan-recreation class
    structurally."""
    rows = await conn.fetch(
        """
        SELECT et.site_id,
               COUNT(*) AS orphan_rows_24h,
               MIN(et.created_at) AS oldest,
               MAX(et.created_at) AS newest
          FROM execution_telemetry et
         WHERE et.created_at > NOW() - INTERVAL '24 hours'
           AND et.site_id NOT IN (
               SELECT DISTINCT site_id FROM site_appliances
                WHERE deleted_at IS NULL
           )
      GROUP BY et.site_id
        HAVING COUNT(*) > 10
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "orphan_rows_24h": int(r["orphan_rows_24h"]),
                "oldest": r["oldest"].isoformat() if r["oldest"] else None,
                "newest": r["newest"].isoformat() if r["newest"] else None,
                "remediation": (
                    "site_id has no matching live site_appliances row. "
                    "Run the orphan_relocation cascade (migrations 252/254/255 as "
                    "templates) to migrate execution_telemetry + incidents + "
                    "l2_decisions to the canonical site_id BEFORE the next flywheel "
                    "tick (every 30 min) regenerates aggregated_pattern_stats."
                ),
            },
        )
        for r in rows
    ]


async def _check_rename_site_immutable_list_drift(conn: asyncpg.Connection) -> List[Violation]:
    """F4-followup substrate invariant (Session 213).

    Find tables that:
      (a) have a `site_id` column
      (b) are protected by a DELETE-blocking trigger (the standard
          audit-class / append-only signal — RAISE EXCEPTION inside a
          BEFORE DELETE trigger)
      (c) are NOT in `_rename_site_immutable_tables()`

    A table that matches (a)+(b)+(c) is operationally append-only yet
    `rename_site()` would happily rewrite its site_id — a chain-of-
    custody violation if the append-only posture exists for HIPAA or
    cryptographic-binding reasons.

    The check uses pg_trigger and pg_proc to find DELETE-blocking
    triggers by inspecting the trigger function source for
    `RAISE EXCEPTION` patterns. False positives are possible (a
    trigger that conditionally raises on DELETE only in some cases)
    but the runbook covers operator review.
    """
    # Two-pass detection (Session 214 P3 close on partition-aware
    # trigger detection):
    #
    # Round-table 2026-04-30 corrected the original narrative. The
    # motivating case is mig 191 (appliance_heartbeats partition), NOT
    # mig 121 (which is unrelated network_mode). Mig 191 attaches
    # `BEFORE DELETE FOR EACH ROW` to the partitioned PARENT — in
    # PG >= 13 this stores ONE non-internal pg_trigger row with
    # tgrelid = parent_oid and parent.relkind = 'p'. Children inherit
    # via auto-propagated rows with tgisinternal = true (which we
    # filter out anyway).
    #
    # Pass 1 catches THIS case via `c.relkind IN ('r', 'p')` — the
    # previous single-pass query only had `'r'` and missed
    # partitioned parents entirely. That alone closes the
    # appliance_heartbeats gap.
    #
    # Pass 2 catches a separate (legacy/manual) case: someone
    # manually attached a non-internal trigger to a specific child
    # partition without one on the parent. We surface the PARENT
    # name (via pg_inherits.inhparent) because rename_site() operates
    # on parent and the immutable list lives at the parent level.
    rows = await conn.fetch(
        """
        WITH partition_children AS (
            SELECT i.inhrelid AS child_oid, i.inhparent AS parent_oid
              FROM pg_inherits i
        ),
        trigger_carriers AS (
            -- Pass 1: trigger directly on the table
            SELECT DISTINCT c.relname AS table_name, c.oid AS table_oid
              FROM pg_trigger trg
              JOIN pg_class c ON c.oid = trg.tgrelid
              JOIN pg_proc p ON p.oid = trg.tgfoid
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public'
               AND c.relkind IN ('r', 'p')
               AND NOT trg.tgisinternal
               -- DELETE bit in tgtype: 1 << 3 = 8
               AND (trg.tgtype & 8) = 8
               AND p.prosrc ILIKE '%RAISE EXCEPTION%'
               -- Exclude partition CHILDREN — we'll project them up to
               -- their parent in Pass 2. A child in this CTE would
               -- surface the child name (e.g. appliance_heartbeats_y202604),
               -- not the parent (appliance_heartbeats).
               AND NOT EXISTS (
                   SELECT 1 FROM partition_children pc
                    WHERE pc.child_oid = c.oid
               )
            UNION
            -- Pass 2: trigger on a partition child → surface the parent
            SELECT DISTINCT parent.relname AS table_name, parent.oid AS table_oid
              FROM pg_trigger trg
              JOIN pg_class c ON c.oid = trg.tgrelid
              JOIN pg_proc p ON p.oid = trg.tgfoid
              JOIN pg_namespace n ON n.oid = c.relnamespace
              JOIN partition_children pc ON pc.child_oid = c.oid
              JOIN pg_class parent ON parent.oid = pc.parent_oid
             WHERE n.nspname = 'public'
               AND c.relkind = 'r'
               AND NOT trg.tgisinternal
               AND (trg.tgtype & 8) = 8
               AND p.prosrc ILIKE '%RAISE EXCEPTION%'
        ),
        site_id_tables AS (
            SELECT DISTINCT table_name
              FROM information_schema.columns
             WHERE column_name = 'site_id'
               AND table_schema = 'public'
               -- Skip date-suffixed backup snapshots (mig 257 pattern)
               AND table_name !~ '_backup_[0-9]{6,8}$'
        ),
        immutable AS (
            SELECT table_name FROM _rename_site_immutable_tables()
        )
        SELECT DISTINCT tc.table_name
          FROM trigger_carriers tc
          JOIN site_id_tables sit ON sit.table_name = tc.table_name
         WHERE tc.table_name NOT IN (SELECT table_name FROM immutable)
         ORDER BY tc.table_name
        """
    )
    if not rows:
        return []
    drift_tables = [r["table_name"] for r in rows]
    return [
        Violation(
            site_id=None,
            details={
                "drift_tables": drift_tables,
                "remediation": (
                    "Each listed table has a DELETE-blocking trigger (operationally "
                    "append-only) AND a site_id column AND is NOT in "
                    "_rename_site_immutable_tables(). rename_site() would rewrite "
                    "their site_id — a chain-of-custody risk. Either add the table to "
                    "_rename_site_immutable_tables() in a follow-on migration "
                    "(most likely if the DELETE-block is intentional for HIPAA / "
                    "cryptographic reasons), OR drop the DELETE-block trigger if the "
                    "table is genuinely operational. Round-table review before either."
                ),
            },
        )
    ]


async def _check_go_agent_heartbeat_stale(conn: asyncpg.Connection) -> List[Violation]:
    """Sev2 — workstation agents silent > 6 hours, excluding chaos-
    lab-tagged dev builds. Round-table 2026-04-30 fleet-edge liveness.

    D4 closure 2026-05-02: also excludes operator-marked terminal
    statuses ('decommissioned', 'archived'). When an MSP retires a
    workstation, marking the row terminal stops the alarm — the state
    machine in main.py::_go_agent_status_decay_loop also skips these
    rows so the operator's decision is durable. Lockstep enforced by
    tests/test_go_agent_terminal_status_lockstep.py.
    """
    rows = await conn.fetch(
        """
        SELECT agent_id,
               site_id,
               hostname,
               agent_version,
               status,
               last_heartbeat,
               EXTRACT(EPOCH FROM (NOW()::timestamp - last_heartbeat)) / 3600.0
                   AS hours_silent
          FROM go_agents
         WHERE last_heartbeat IS NOT NULL
           AND last_heartbeat < NOW()::timestamp - make_interval(hours => 6)
           AND (agent_version IS NULL OR agent_version != 'dev')
           AND status NOT IN ('decommissioned', 'archived')
         ORDER BY last_heartbeat ASC
         LIMIT 50
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "agent_id": r["agent_id"],
                "hostname": r["hostname"],
                "agent_version": r["agent_version"],
                "status": r["status"],
                "last_heartbeat": (
                    r["last_heartbeat"].isoformat() if r["last_heartbeat"] else None
                ),
                "hours_silent": round(float(r["hours_silent"]), 2),
                "remediation": (
                    "Agent has been silent for "
                    f"{round(float(r['hours_silent']), 1)}h. State machine "
                    "should already show status != 'connected'. Operator "
                    "checks: (a) workstation power state (b) "
                    "osiriscare-agent service Windows-side StartupType "
                    "(c) site-level network egress."
                ),
            },
        )
        for r in rows
    ]


async def _check_appliance_offline_extended(conn: asyncpg.Connection) -> List[Violation]:
    """Sev2 — appliance offline > 24h. Sibling to existing
    offline_appliance_over_1h sev2 (fires at 1h). This one's runbook
    implication is 'phone the customer'. Round-table 2026-04-30."""
    rows = await conn.fetch(
        """
        SELECT site_id,
               appliance_id::text AS appliance_id,
               hostname,
               agent_version,
               last_checkin,
               EXTRACT(EPOCH FROM (NOW() - last_checkin)) / 3600.0
                   AS hours_offline
          FROM site_appliances
         WHERE deleted_at IS NULL
           AND status NOT IN ('decommissioned', 'relocating', 'relocated')
           AND last_checkin IS NOT NULL
           AND last_checkin < NOW() - make_interval(hours => 24)
         ORDER BY last_checkin ASC
         LIMIT 50
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "appliance_id": r["appliance_id"],
                "hostname": r["hostname"],
                "agent_version": r["agent_version"],
                "last_checkin": (
                    r["last_checkin"].isoformat() if r["last_checkin"] else None
                ),
                "hours_offline": round(float(r["hours_offline"]), 2),
                "remediation": (
                    "Appliance offline for "
                    f"{round(float(r['hours_offline']), 1)}h — past the "
                    "24h escalation threshold. Partner contact should "
                    "phone the customer to confirm site status. Substrate "
                    "exposes the signal; operator decides outreach per BAA."
                ),
            },
        )
        for r in rows
    ]


async def _check_flywheel_federation_misconfigured(conn: asyncpg.Connection) -> List[Violation]:
    """F6 fast-follow substrate invariant (Session 214).

    Fires sev3 when the FLYWHEEL_FEDERATION_ENABLED env flag is
    truthy AND no tier in flywheel_eligibility_tiers has both
    enabled=TRUE AND calibrated_at IS NOT NULL. In this state the
    federation read path falls back to hardcoded defaults and emits
    logger.warning per loop tick — defensive behavior, but the
    misconfiguration deserves operator-visible signal on the
    substrate-health dashboard.

    Lenient env parser matches main.py + sibling subsystem
    (assertions.py::L2_ENABLED).
    """
    flag_raw = os.environ.get("FLYWHEEL_FEDERATION_ENABLED", "false").lower()
    if flag_raw not in ("true", "1", "yes", "on"):
        return []
    # Flag is truthy. Check whether any tier is genuinely active.
    row = await conn.fetchrow(
        """
        SELECT COUNT(*) AS active_count
          FROM flywheel_eligibility_tiers
         WHERE enabled = TRUE
           AND calibrated_at IS NOT NULL
        """
    )
    active_count = int(row["active_count"]) if row else 0
    if active_count > 0:
        return []
    # Misconfigured — surface the specific state for the runbook.
    tier_state_rows = await conn.fetch(
        """
        SELECT tier_name, enabled, calibrated_at IS NOT NULL AS is_calibrated
          FROM flywheel_eligibility_tiers
         ORDER BY tier_level
        """
    )
    return [
        Violation(
            site_id=None,
            details={
                "env_flag": flag_raw,
                "active_tier_count": active_count,
                "tier_state": [
                    {
                        "tier_name": r["tier_name"],
                        "enabled": bool(r["enabled"]),
                        "calibrated": bool(r["is_calibrated"]),
                    }
                    for r in tier_state_rows
                ],
                "remediation": (
                    "FLYWHEEL_FEDERATION_ENABLED is set in the mcp-server env "
                    "but no tier has enabled=TRUE AND calibrated_at IS NOT NULL. "
                    "The flywheel read path is silently using hardcoded defaults. "
                    "Either unset the env var (true intent: OFF) or run the "
                    "calibration migration to flip a tier to enabled+calibrated."
                ),
            },
        )
    ]


# Removed 2026-05-05 (Session 217, task #4 close): the function
# `_check_sigauth_post_fix_window_canary` covered the 7-day window
# 2026-04-28 17:11Z → 2026-05-05 17:11Z bracketing the sigauth
# wrap-fix deploy (commit 303421cc). Verified zero firings over the
# entire window via fork psql before removal. The steady-state
# detector `sigauth_enforce_mode_rejections` (sev2, rolling 6h)
# remains. Validation doc:
# docs/security/sigauth-wrap-validation-2026-04-28.md.


async def _check_sigauth_enforce_mode_rejections(conn: asyncpg.Connection) -> List[Violation]:
    """Enforce-mode appliances must have 0% sigauth fail rate. The
    umbrella (`signature_verification_failures`) only fires at >=5
    fails AND >=5% in 1h — structurally blind to low-rate jitter,
    which is exactly what we saw post-Session-211 enforce flip:
    4 unknown_pubkey rejections in 24h on north-valley-branch-2,
    0.09% rate, substrate stayed silent.

    This sibling invariant fires sev2 the moment ANY enforce-mode
    appliance has >=1 invalid sigauth observation in a rolling 6h
    window. Joins on (site_id, mac_address) so the violation is
    appliance-scoped (one row per offending appliance). Filed task
    #168 covers the underlying connection-coherence root-cause
    investigation. (Session 211 Phase 2 QA, 2026-04-28)"""
    rows = await conn.fetch(
        """
        SELECT sa.site_id,
               sa.mac_address,
               sa.appliance_id,
               COUNT(*) FILTER (WHERE NOT o.valid)            AS failures,
               COUNT(*)                                        AS total,
               array_agg(DISTINCT o.reason)
                 FILTER (WHERE NOT o.valid)                   AS reasons,
               MAX(o.observed_at) FILTER (WHERE NOT o.valid)  AS last_failure
          FROM site_appliances sa
          JOIN sigauth_observations o
            ON o.site_id = sa.site_id
           AND UPPER(o.mac_address) = UPPER(sa.mac_address)
         WHERE sa.deleted_at IS NULL
           AND sa.signature_enforcement = 'enforce'
           AND o.observed_at > NOW() - INTERVAL '6 hours'
      GROUP BY sa.site_id, sa.mac_address, sa.appliance_id
        HAVING COUNT(*) FILTER (WHERE NOT o.valid) >= 1
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "appliance_id": r["appliance_id"],
                "mac_address": r["mac_address"],
                "failures": r["failures"],
                "total_samples": r["total"],
                "fail_rate_pct": round(r["failures"] * 100.0 / r["total"], 3),
                "reasons": list(r["reasons"] or []),
                "last_failure_at": r["last_failure"].isoformat() if r["last_failure"] else None,
                "remediation": "POST /api/admin/sigauth/demote/{appliance_id} for instant rollback to observe.",
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

    JOINs site_appliances + filters deleted_at IS NULL so soft-deleted
    appliances (e.g. relocated rows from mig 245) don't keep firing
    after decommission. The reported site_id comes from site_appliances
    (the LIVE site_id), not from journal_upload_events (which records
    the site_id at upload time and is immutable post-relocation).
    """
    rows = await conn.fetch(
        """
        WITH latest AS (
            SELECT DISTINCT ON (appliance_id)
                   appliance_id, received_at
              FROM journal_upload_events
          ORDER BY appliance_id, received_at DESC
        )
        SELECT sa.site_id, sa.appliance_id, l.received_at,
               EXTRACT(EPOCH FROM (NOW() - l.received_at))/60 AS minutes_stale
          FROM latest l
          JOIN site_appliances sa ON sa.appliance_id = l.appliance_id
         WHERE l.received_at < NOW() - INTERVAL '90 minutes'
           AND sa.deleted_at IS NULL
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


async def _check_provisioning_network_fail(conn: asyncpg.Connection) -> List[Violation]:
    """v40 FIX-14 (Session 209, 2026-04-23 round-table cont.).

    Fires when a freshly-installed appliance has reached the installed
    system and is retrying provisioning, but the 4-stage network gate
    (DNS → TCP/443 → TLS → HTTP /health, FIX-11) has never passed.

    Predicates:
      - install_sessions row is fresh (last_seen in last hour,
        checkin_count >= 3 — enough attempts to be meaningful)
      - first_outbound_success_at IS NULL — the installed system has
        NEVER completed a successful 4-stage gate
      - Either site_appliances is missing for this MAC, OR its
        last_checkin is older than 15 minutes (so we're not firing
        on a working appliance that happened to have a slow first boot)

    Fires EARLIER than provisioning_stalled: that invariant waits for
    install_sessions to go stale (20+ min); this one fires within ~90s
    of the first failed installed-system checkin because
    first_outbound_success_at is populated eagerly on the FIRST pass.

    Distinct from installed_but_silent — that one fires when the
    installer itself has stopped and nothing else has started. This one
    fires while the installed system is ACTIVELY retrying but failing
    the gate.

    When fired, the beacon at http://<appliance-ip>:8443/ will have a
    populated `install_gate_status.last_stage_failed` naming the exact
    broken stage (dns/tcp_443/tls/health) — that's the actionable
    payload this invariant points operators at.
    """
    # We key on install_sessions because that's the only table that
    # has a row BEFORE the installed system ever checks in. The
    # first_outbound_success_at column lives on install_sessions too
    # (populated by /api/install/report/net-ready).
    #
    # Fallback: if the column doesn't yet exist (migration 239 not
    # applied), short-circuit to an empty list so we don't crash the
    # engine. The migration auto-apply at startup (Session 205) should
    # prevent this in practice but defense-in-depth costs nothing.
    col_exists = await conn.fetchval(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = 'install_sessions'
           AND column_name = 'first_outbound_success_at'
         LIMIT 1
        """
    )
    if not col_exists:
        return []

    rows = await conn.fetch(
        """
        SELECT
            iss.site_id,
            iss.mac_address,
            iss.hostname,
            iss.ip_addresses,
            iss.checkin_count AS installer_checkins,
            iss.last_seen     AS installer_last_seen,
            iss.last_error_code,
            iss.last_error_detail,
            iss.api_resolved_ip,
            sa.last_checkin   AS site_appliance_last_checkin,
            EXTRACT(EPOCH FROM (NOW() - iss.last_seen))/60 AS installer_age_min
          FROM install_sessions iss
          LEFT JOIN site_appliances sa
            ON UPPER(sa.mac_address) = UPPER(iss.mac_address)
           AND sa.site_id = iss.site_id
           AND sa.deleted_at IS NULL
         WHERE iss.last_seen > NOW() - INTERVAL '1 hour'
           AND iss.checkin_count >= 3
           AND iss.first_outbound_success_at IS NULL
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
                    "last_error_code": r["last_error_code"],
                    "last_error_detail": r["last_error_detail"],
                    "api_resolved_ip": r["api_resolved_ip"],
                    "site_appliance_last_checkin": (
                        r["site_appliance_last_checkin"].isoformat()
                        if r["site_appliance_last_checkin"] else None
                    ),
                    "hint": (
                        "Installed system has never reached the Central "
                        "Command origin. LAN-scan the appliance at "
                        "http://<appliance-ip>:8443/ — the "
                        "install_gate_status.last_stage_failed field names "
                        "the exact broken stage (dns, tcp_443, tls, or "
                        "health). Typical fix is to whitelist "
                        "api.osiriscare.net (origin 178.156.162.116) on "
                        "the site's DNS filter / web proxy / firewall."
                    ),
                },
            )
        )
    return out


async def _check_installer_halted_early(conn: asyncpg.Connection) -> List[Violation]:
    """v40.4 (2026-04-23) — the SIBLING that finally ships.

    `provisioning_stalled` and `provisioning_network_fail` both require
    `install_sessions.checkin_count >= 3` before they fire. That covers
    the "installer is actively retrying" case, but is BLIND to the
    exact failure mode that bricked 3/3 v40.0-v40.2 appliances for 4+
    hours on 2026-04-23: the installer posts `/api/install/report/start`
    exactly once (checkin_count=1), succeeds at writing `install_sessions`,
    and then the whole install completes and reboots into the installed
    system — which then FAILS to check in (DNS race in
    run_network_gate_check, missing `host` binary, etc.). Nothing in
    `install_sessions` ever advances past `first_seen`, and `site_appliances.
    last_checkin` never updates. For 4+ hours, every existing invariant
    stayed silent.

    This invariant covers that class: fires when
      * `install_sessions` has a row with `first_seen >= 20 min ago`
        (installer hit /report/start then stopped)
      * AND (`site_appliances` is missing OR `site_appliances.last_checkin
        IS NULL` OR `last_checkin < install_sessions.first_seen`)
        — the installed system NEVER successfully checked in
           after the install_sessions row was created.

    Severity is sev2 (not sev1) — the installer process COULD have
    been interrupted legitimately (operator pulled the USB mid-install,
    power cut). 20 min gives a real install enough time to finish + first
    boot + first checkin on normal hardware.
    """
    rows = await conn.fetch(
        """
        SELECT
            iss.site_id,
            iss.mac_address,
            iss.hostname,
            iss.ip_addresses,
            iss.first_seen,
            iss.last_seen,
            iss.checkin_count,
            iss.install_stage,
            sa.last_checkin,
            EXTRACT(EPOCH FROM (NOW() - iss.first_seen))/60 AS age_min
          FROM install_sessions iss
          LEFT JOIN site_appliances sa
            ON UPPER(sa.mac_address) = UPPER(iss.mac_address)
           AND sa.site_id = iss.site_id
           AND sa.deleted_at IS NULL
         WHERE iss.first_seen < NOW() - INTERVAL '20 minutes'
           AND iss.first_seen > NOW() - INTERVAL '24 hours'
           AND (
                 sa.last_checkin IS NULL
              OR sa.last_checkin < iss.first_seen
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
                    "install_stage": r["install_stage"],
                    "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
                    "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
                    "checkin_count": int(r["checkin_count"] or 0),
                    "age_min": round(float(r["age_min"]), 1),
                    "site_appliance_last_checkin": (
                        r["last_checkin"].isoformat()
                        if r["last_checkin"] else None
                    ),
                    "hint": (
                        "Installer posted /report/start and then stopped; "
                        "the installed system has not produced a checkin. "
                        "Most common cause as of v40.4: a DNS-race or "
                        "classpath bug in msp-auto-provision.service on "
                        "the installed system (see appliance-disk-image.nix "
                        "run_network_gate_check). SSH in as `msp` with the "
                        "ISO-embedded pubkey and `sudo systemctl status "
                        "msp-auto-provision.service` / `sudo journalctl -u "
                        "msp-auto-provision -n 100`. If it's failed, "
                        "`sudo systemctl restart msp-auto-provision.service` "
                        "(NOPASSWD). For a permanently-stuck box, check if "
                        "port 8443 beacon is responding — the beacon JSON's "
                        "`state` + `last_error` fields point at the exact "
                        "failure stage."
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


async def _check_relocation_stalled(conn: asyncpg.Connection) -> List[Violation]:
    """Round-table RT-4 (Session 210-B 2026-04-25). An admin-initiated
    relocation has been pending > 30 min. Either the daemon is offline,
    the reprovision order failed, or the daemon version is too old to
    handle reprovision (the relocate endpoint should have caught that
    via its version-gate, but worth surfacing if the gate ever slips).

    The finalize_pending_relocations() background sweep auto-flips
    stalled rows to 'expired' status after 30 min. This invariant
    fires on the SAME 30-min boundary so the dashboard alerts the
    operator at the moment of expiration; the row stays at 'pending'
    until the next sweep cycle, at which point it becomes 'expired'.
    Either state is a real signal — daemon failed to complete the
    move and operator must investigate.
    """
    rows = await conn.fetch(
        """
        SELECT r.id, r.source_site_id, r.target_site_id,
               r.source_appliance_id, r.target_appliance_id,
               r.mac_address, r.actor, r.reason, r.fleet_order_id,
               EXTRACT(EPOCH FROM (NOW() - r.initiated_at))/60 AS minutes_pending
          FROM relocations r
         WHERE r.status IN ('pending','expired')
           AND r.initiated_at < NOW() - INTERVAL '30 minutes'
           AND r.completed_at IS NULL
        """
    )
    return [
        Violation(
            site_id=r["source_site_id"],
            details={
                "relocation_id": r["id"],
                "mac_address": r["mac_address"],
                "source_site_id": r["source_site_id"],
                "target_site_id": r["target_site_id"],
                "source_appliance_id": r["source_appliance_id"],
                "target_appliance_id": r["target_appliance_id"],
                "actor": r["actor"],
                "reason": r["reason"],
                "fleet_order_id": r["fleet_order_id"],
                "minutes_pending": round(float(r["minutes_pending"]), 1),
                "remediation": (
                    "Daemon never completed the move. Investigate: "
                    "(1) is the appliance online (check site_appliances."
                    f"last_checkin for {r['source_appliance_id']!r}); "
                    "(2) was a reprovision fleet_order issued "
                    f"({'yes — id=' + r['fleet_order_id'] if r['fleet_order_id'] else 'NO — daemon < 0.4.11, ssh_snippet was returned to operator'}); "
                    "(3) is the daemon version current (must be ≥ 0.4.11 for "
                    "fleet_order path); (4) check the appliance's "
                    "/var/lib/msp/appliance-daemon.log for the order receipt "
                    "+ any error. To force completion: re-issue with the "
                    "ssh_snippet from the original relocate response, OR "
                    "manually finalize via UPDATE relocations SET status="
                    "'failed', completed_at=NOW() WHERE id=$RELOCATION_ID."
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


async def _check_flywheel_ledger_stalled(conn: asyncpg.Connection) -> List[Violation]:
    """Fire when the flywheel orchestrator has transitional work available
    but the ledger has been silent for a full hour.

    Signals that are TRUE only in the pathological case the 2026-04-18
    audit caught:
      * promoted_rules in state 'rolling_out' whose associated fleet_order
        already has a fleet_order_completion row ≥10 min old — the
        RolloutAckedTransition should have flipped them to 'active' on a
        prior 5-min tick, but didn't.
      * AND zero INSERTs into promoted_rule_events in the last 60 min,
        despite the orchestrator loop ticking every 5 min.

    Both conditions must hold. If no transitional work is available, a
    quiet ledger is correct. If transitions WOULD be possible but none
    happen, the orchestrator is broken.

    Deliberately does NOT dig into log messages — the invariant layer
    observes outcomes, not causes. The recommended_action points
    operators at the three-list lockstep CI test which localizes the
    most common cause in 30 seconds.
    """
    row = await conn.fetchrow(
        """
        WITH pending AS (
            SELECT COUNT(*) AS pending_transitions
              FROM promoted_rules pr
              JOIN fleet_orders fo
                ON fo.parameters->>'rule_id' = pr.rule_id
               AND fo.order_type = 'sync_promoted_rule'
              JOIN fleet_order_completions foc
                ON foc.fleet_order_id = fo.id
             WHERE pr.lifecycle_state = 'rolling_out'
               AND foc.status = 'completed'
               AND foc.completed_at < NOW() - INTERVAL '10 minutes'
               AND foc.completed_at > NOW() - INTERVAL '24 hours'
        ),
        ledger AS (
            SELECT COUNT(*) AS writes_60m,
                   MAX(created_at) AS latest_write_at
              FROM promoted_rule_events
             WHERE created_at > NOW() - INTERVAL '60 minutes'
        )
        SELECT p.pending_transitions,
               l.writes_60m,
               l.latest_write_at,
               EXTRACT(EPOCH FROM (NOW() - l.latest_write_at))/60
                   AS minutes_since_last_write
          FROM pending p, ledger l
        """
    )
    if row is None:
        return []
    pending = int(row["pending_transitions"] or 0)
    writes_60m = int(row["writes_60m"] or 0)
    # Only fire when transitional work EXISTS but ledger is silent
    if pending < 1 or writes_60m > 0:
        return []
    minutes_since = float(row["minutes_since_last_write"] or 0)
    return [
        Violation(
            site_id=None,
            details={
                "pending_transitions": pending,
                "writes_60m": writes_60m,
                "latest_write_at": (
                    row["latest_write_at"].isoformat()
                    if row["latest_write_at"] else None
                ),
                "minutes_since_last_write": minutes_since,
                "remediation": (
                    "Run test_three_list_lockstep_pg.py. If it fails, ship "
                    "a migration that extends promoted_rule_events.event_type "
                    "CHECK to match flywheel_state.EVENT_TYPES. If it passes, "
                    "check the orchestrator loop heartbeat "
                    "(/api/admin/health/loops) and grep mcp-server logs for "
                    "orchestrator_transition_failed or safe_rollout_ledger_advance_failed."
                ),
            },
        )
    ]


async def _check_frontend_field_undefined_spike(conn: asyncpg.Connection) -> List[Violation]:
    """Fire when browser-side apiFieldGuard has observed a spike of
    FIELD_UNDEFINED events — frontend code reading a field that the
    backend no longer provides (or never provided, per this endpoint).

    Session 210 (2026-04-24) Layer 3 of enterprise API reliability. The
    frontend emits these via /api/admin/telemetry/client-field-undefined;
    this invariant reads the aggregation and pages operators when drift
    reaches prod despite the Pydantic contract (Layer 6) and OpenAPI
    codegen (Layer 1) gates.

    Threshold: >10 events from distinct browser sessions in 5 min. Single
    user mashing F5 after a deploy shouldn't page — but the same
    (endpoint, field) drift hitting multiple users indicates real
    contract drift that shipped.
    """
    # If the migration hasn't landed yet (e.g., first deploy after this
    # commit but before Migration 242 applied), return no violations
    # rather than raising.
    # Two trigger paths:
    #   multi-user drift — 10+ events AND 2+ distinct sessions (catches real
    #     contract breaks affecting many customers)
    #   single-user high-volume — 30+ events even from 1 session (catches the
    #     single-tenant deployment case where a legit bug hits only 1 user
    #     hammering the page, which the multi-user path would miss)
    # Either path fires the invariant. Session 210 round-table #6 added the
    # single-user fallback — without it, small / single-tenant deployments
    # had a dead invariant they'd never trip.
    try:
        rows = await conn.fetch(
            """
            SELECT endpoint, field_name,
                   COUNT(*) AS event_count,
                   COUNT(DISTINCT ip_address) AS distinct_sessions,
                   MIN(recorded_at) AS first_seen,
                   MAX(recorded_at) AS last_seen
              FROM client_telemetry_events
             WHERE event_kind = 'FIELD_UNDEFINED'
               AND recorded_at > NOW() - INTERVAL '5 minutes'
             GROUP BY endpoint, field_name
             HAVING (COUNT(*) > 10 AND COUNT(DISTINCT ip_address) >= 2)
                 OR COUNT(*) > 30
             ORDER BY COUNT(*) DESC
             LIMIT 20
            """
        )
    except asyncpg.exceptions.UndefinedTableError:
        return []

    if not rows:
        return []
    return [
        Violation(
            site_id=None,
            details={
                "endpoint": r["endpoint"],
                "field_name": r["field_name"],
                "event_count_5m": int(r["event_count"]),
                "distinct_sessions": int(r["distinct_sessions"]),
                "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
                "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
                "remediation": (
                    f"Frontend is reading {r['field_name']!r} from {r['endpoint']} "
                    f"but the backend isn't returning it. Triage: "
                    f"(1) grep backend for the endpoint → inspect the Pydantic "
                    f"response model → is the field declared? "
                    f"(2) `python3 scripts/export_openapi.py` and diff against "
                    f"the committed openapi.json to see if the schema has drifted. "
                    f"(3) If the field was intentionally removed, update the "
                    f"frontend to stop reading it and regenerate "
                    f"frontend/src/api-generated.ts. "
                    f"(4) If the field was never there, the frontend has an "
                    f"outdated assumption; fix the React component."
                ),
            },
        )
        for r in rows
    ]


async def _check_synthetic_l2_pps_rows(conn: asyncpg.Connection) -> List[Violation]:
    """Fire if any `L2-%` prefixed runbook_id shows up in
    platform_pattern_stats.

    Context: migration 237 on 2026-04-18 DELETEd 2 L2- rows + the same
    commit added the `AND et.runbook_id NOT LIKE 'L2-%'` filter on the
    Step-3 aggregation INSERT (background_tasks.py:1189). Yet on
    2026-04-24 Session 210 found 2 L2- rows present again (ids 37921,
    37927, with January timestamps — the same rows migration 237
    deleted). The investigation couldn't identify the resurrection path
    (no parallel INSERT code path, no admin_audit_log trail). Most
    plausible: the code deploy + migration ran nearly atomic but the
    container-restart race meant Step 3 had one final tick with old
    code after migration DELETE ran. Any future reappearance is a
    regression — either the filter broke or a new INSERT path landed.

    Rows were DELETEd during Session 210. This invariant makes the
    next reappearance LOUD instead of silent.
    """
    rows = await conn.fetch(
        """
        SELECT pattern_key, runbook_id, incident_type,
               first_seen::timestamptz(0) AS first_seen,
               last_seen::timestamptz(0) AS last_seen
          FROM platform_pattern_stats
         WHERE runbook_id LIKE 'L2-%'
         ORDER BY id
         LIMIT 10
        """
    )
    if not rows:
        return []
    return [
        Violation(
            site_id=None,
            details={
                "affected_rows": len(rows),
                "sample_pattern_keys": [r["pattern_key"] for r in rows[:5]],
                "remediation": (
                    "L2-prefixed runbook_ids should never appear in "
                    "platform_pattern_stats — background_tasks.py:1189 filter "
                    "excludes them. If they've reappeared, either the filter "
                    "regressed or a new INSERT path landed. Immediate cleanup: "
                    "`DELETE FROM platform_pattern_stats WHERE runbook_id LIKE 'L2-%'`. "
                    "Then grep recent commits for 'platform_pattern_stats' INSERT "
                    "and audit any changes to the aggregation SQL. Cross-reference "
                    "migration 237 (the one-shot cleanup) — if the filter is fine, "
                    "the resurrection mechanism must be external (pg_restore, manual SQL)."
                ),
            },
        )
    ]


async def _check_l2_decisions_stalled(conn: asyncpg.Connection) -> List[Violation]:
    """Fire when L2_ENABLED=true but L2 decisions have fallen silent.

    Context: 2026-04-12 a kill switch (L2_ENABLED=false) disabled L2
    after Session 205 found 0.9% success rate + zero promoted_rule
    deployments + unbounded API spend. Session 210 (2026-04-24)
    re-enabled L2 after the Flywheel Spine + 3 cost gates (confidence
    floor 0.7, zero-result circuit, input gate) landed.

    THIS INVARIANT exists so the NEXT silent death doesn't hide for
    30 days. Without it, the previous outage was discovered only by
    an operator asking 'why no promotions?' during a manual audit.

    Fires when ALL of these hold:
      * L2_ENABLED=true in the running mcp-server process env
      * Fleet has at least one appliance that checked in in the last
        hour (no expectation of L2 activity when offline)
      * l2_decisions table has < 5 rows in the last 48 hours
      * AND there's evidence L2 SHOULD have fired but didn't — i.e.,
        ≥3 FAILED L1 attempts in 48h. A failed L1 step means the
        deterministic engine ran a runbook and reported a non-success
        result; that incident NATURALLY escalates to L2. If we see
        L1 failures but zero L2 decisions, L2 is silently broken.

    L1 succeeding on every step is NOT a stall signal — it means L1
    coverage is complete and L2 is correctly idle. The original
    `remediation_steps_48h >= 5` gate fired on healthy L1-saturated
    fleets and was wrong (Session 210-B 2026-04-25 audit found 85
    successful L1 steps + 0 L2 decisions on a healthy NVB2 fleet).

    48h is the noise-tolerant floor — a quiet weekend with healthy
    appliances can legitimately produce 0 incidents. Once the fleet
    has any drift → L1 failure → L2 flow, 5 in 48h is trivially easy
    to clear. Falling below signals either (a) LLM API broken, (b)
    circuit breaker stuck open, (c) daily budget cap tripped and not
    reset, (d) zero-result circuit clamped every pattern.

    Session 210-B 2026-04-24: pre-refinement, this fired even when L1
    correctly handled every incident (the L2 endpoint short-circuits
    in <40ms on L1 match without writing to l2_decisions). The
    `pipeline_signal` gate above prevents false positives during
    quiet-but-healthy periods where L1 carries the load.
    """
    import os as _os
    l2_enabled = _os.getenv("L2_ENABLED", "false").lower() in ("true", "1", "yes", "on")
    if not l2_enabled:
        return []

    row = await conn.fetchrow(
        """
        WITH state AS (
            SELECT COUNT(*) AS decisions_48h,
                   MAX(created_at) AS latest_decision_at
              FROM l2_decisions
             WHERE created_at > NOW() - INTERVAL '48 hours'
        ),
        fleet AS (
            SELECT COUNT(*) AS online
              FROM site_appliances
             WHERE deleted_at IS NULL
               AND last_checkin > NOW() - INTERVAL '1 hour'
        ),
        l1_failures AS (
            SELECT COUNT(*) AS failed_l1_steps_48h
              FROM incident_remediation_steps
             WHERE created_at > NOW() - INTERVAL '48 hours'
               AND tier = 'L1'
               AND result NOT IN ('order_created', 'success', 'completed', 'order_acked')
        )
        SELECT s.decisions_48h, s.latest_decision_at, f.online,
               l.failed_l1_steps_48h
          FROM state s, fleet f, l1_failures l
        """
    )
    if row is None:
        return []
    decisions_48h = int(row["decisions_48h"] or 0)
    online = int(row["online"] or 0)
    failed_l1_steps_48h = int(row["failed_l1_steps_48h"] or 0)
    if online < 1 or decisions_48h >= 5 or failed_l1_steps_48h < 3:
        return []
    latest = row["latest_decision_at"]
    hours_since = None
    if latest is not None:
        hours_since = (datetime.now(timezone.utc) - latest).total_seconds() / 3600.0
    return [
        Violation(
            site_id=None,
            details={
                "l2_decisions_48h": decisions_48h,
                "online_appliances_1h": online,
                "failed_l1_steps_48h": failed_l1_steps_48h,
                "latest_decision_at": latest.isoformat() if latest else None,
                "hours_since_last_decision": hours_since,
                "remediation": (
                    "L2 appears silently stalled. Walk the cost-gate stack: "
                    "(1) API key valid — `docker exec mcp-server env | grep -E 'ANTHROPIC|OPENAI|AZURE_OPENAI'`; "
                    "(2) Circuit breaker state — `docker logs mcp-server | grep 'L2 circuit breaker'` — if OPENED, wait for cooldown or restart mcp-server to reset; "
                    "(3) Daily call cap — `docker logs mcp-server | grep 'daily_limit_reached'`; raise MAX_DAILY_L2_CALLS if legitimate load; "
                    "(4) Zero-result circuit — `SELECT site_id, pattern_signature, COUNT(*) FROM l2_decisions WHERE runbook_id IS NULL AND created_at > NOW() - INTERVAL '24 hours' GROUP BY 1,2 HAVING COUNT(*) >= 2` — these pairs are gated; "
                    "(5) Intentional maintenance — if so, set L2_ENABLED=false in env and this invariant goes quiet."
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


async def _check_nixos_rebuild_success_drought(conn: asyncpg.Connection) -> List[Violation]:
    """Fire when operators have ATTEMPTED nixos_rebuild admin_orders
    in the last 7 days but none have succeeded.

    The healthy state is either (a) a steady cadence of completed
    rebuilds, or (b) no attempts at all. The pathological state is
    "we keep trying and every one fails" — which is invisible from
    the existing dashboards because nixos_rebuild is an admin-triggered
    action, not a background loop.

    Windows:
      * attempts counted within last 7 days (admin_orders.created_at)
      * successes counted within last 7 days (admin_orders.status='completed')
      * last-success anchor extended to 365 days so the violation
        details carry a human-meaningful "days since last success" number
        for the dashboard card.

    Does NOT fire when admin_orders has a completed row in the 7-day
    window, even if later failures followed — one success is enough to
    prove the path still works. Operator judgment takes over from there.
    """
    # Session 210-B fix: nixos_rebuild orders now route through BOTH
    # admin_orders (legacy) AND fleet_orders + fleet_order_completions
    # (modern via fleet_cli.py). Previously this invariant only queried
    # admin_orders so canary rebuilds issued via fleet_cli could succeed
    # and STILL leave the drought firing. Query both tables + unioned.
    row = await conn.fetchrow(
        """
        WITH admin AS (
            SELECT
                COUNT(*) FILTER (WHERE status = 'completed') AS successes_7d,
                COUNT(*) FILTER (WHERE status = 'failed')    AS failures_7d,
                COUNT(*) FILTER (WHERE status = 'expired')   AS expired_7d,
                MAX(completed_at) FILTER (WHERE status = 'completed')
                    AS last_success_7d
              FROM admin_orders
             WHERE order_type = 'nixos_rebuild'
               AND created_at > NOW() - INTERVAL '7 days'
        ),
        fleet AS (
            -- Count distinct completions per (order, appliance). One
            -- fleet_order can have N completions; each is a real attempt
            -- on a real appliance.
            SELECT
                COUNT(*) FILTER (WHERE foc.status = 'completed') AS successes_7d,
                COUNT(*) FILTER (WHERE foc.status = 'failed')    AS failures_7d,
                0 AS expired_7d,
                MAX(foc.completed_at) FILTER (WHERE foc.status = 'completed')
                    AS last_success_7d
              FROM fleet_order_completions foc
              JOIN fleet_orders fo ON fo.id = foc.fleet_order_id
             WHERE fo.order_type = 'nixos_rebuild'
               AND foc.completed_at > NOW() - INTERVAL '7 days'
        )
        SELECT
            (admin.successes_7d + fleet.successes_7d) AS successes_7d,
            (admin.failures_7d  + fleet.failures_7d)  AS failures_7d,
            (admin.expired_7d)                         AS expired_7d,
            GREATEST(
                COALESCE(admin.last_success_7d, TIMESTAMPTZ '-infinity'),
                COALESCE(fleet.last_success_7d, TIMESTAMPTZ '-infinity')
            ) AS last_success_7d
          FROM admin, fleet
        """
    )
    if row is None:
        return []
    successes = int(row["successes_7d"] or 0)
    failures = int(row["failures_7d"] or 0) + int(row["expired_7d"] or 0)
    if successes > 0 or failures < 1:
        return []

    # Anchor the "drought length" number — look back 365d across BOTH
    # tables so we can report "59 days since last success" cleanly.
    last_success_ever = await conn.fetchval(
        """
        SELECT MAX(ts) FROM (
            SELECT MAX(completed_at) AS ts
              FROM admin_orders
             WHERE order_type = 'nixos_rebuild' AND status = 'completed'
            UNION ALL
            SELECT MAX(foc.completed_at)
              FROM fleet_order_completions foc
              JOIN fleet_orders fo ON fo.id = foc.fleet_order_id
             WHERE fo.order_type = 'nixos_rebuild' AND foc.status = 'completed'
        ) s
        """
    )
    days_since_success: Optional[int] = None
    if last_success_ever is not None:
        delta = datetime.now(timezone.utc) - last_success_ever
        days_since_success = int(delta.total_seconds() // 86400)
    return [
        Violation(
            site_id=None,
            details={
                "successes_7d": successes,
                "failures_7d": int(row["failures_7d"] or 0),
                "expired_7d": int(row["expired_7d"] or 0),
                "last_success_at": (
                    last_success_ever.isoformat()
                    if last_success_ever else None
                ),
                "days_since_last_success": days_since_success,
                "remediation": (
                    "Pull the most recent failed order's payload: "
                    "SELECT appliance_id, error_message, result FROM admin_orders "
                    "WHERE order_type='nixos_rebuild' AND status='failed' "
                    "ORDER BY completed_at DESC LIMIT 1. 0.4.7+ daemons "
                    "persist the full log at /var/lib/msp/last-rebuild-error.log "
                    "on the appliance and return head+tail 4KB in error_message. "
                    "Pre-0.4.7 daemons tail-truncated to 500 chars and hid the "
                    "nix `error:` banner — upgrade first, then re-canary."
                ),
            },
        )
    ]


async def _check_appliance_disk_pressure(conn: asyncpg.Connection) -> List[Violation]:
    """Fire sev2 when a recent admin_order or fleet_order_completion from
    any appliance carries a `No space left on device` error within the
    last 24h. Emitted per-appliance so operators can remediate the
    specific box (via nix_gc fleet_order) without losing the fleet-wide
    context.

    The 2026-04-22 canary incident on 84:3A:5B:1D:0F:E5 surfaced this
    exactly: the /nix store was 99%+ full and every nixos_rebuild
    attempt failed with `writing to file: No space left on device`. No
    substrate signal existed — the drought invariant caught the outcome
    (0 successes in 7d) but not the root cause. This invariant catches
    the root cause within one 60s tick of the next failure.

    Matches the error banner across both sources of truth:
      * admin_orders.error_message (top-level column, populated since
        Session 209 commit 661c9ed1 — the COALESCE-from-JSONB fix).
      * admin_orders.result->>'error_message' (the JSONB payload that
        predates the top-level column fix; safe to union.)
      * fleet_order_completions.error_message (for fleet-wide rollouts).
      * fleet_order_completions.output->>'error_message' (JSONB mirror).

    Pattern-matches two deterministic surfaces of ENOSPC:
      * `%no space left%` — the kernel ENOSPC banner (direct writes).
      * `%database or disk is full%` — sqlite's translation of ENOSPC
        when committing to /nix/var/nix/db/db.sqlite or the eval-cache.

    Both phrases indicate the same structural condition (full partition),
    but they surface at different layers. The 2026-04-22 canary at
    7C:D3:0A:7C:55:18 hit ONLY the sqlite phrase — the top-level
    `No space left` banner never appeared — so the regex before FIX-7
    silently missed the violation. Both patterns must be checked; the
    eval-cache sqlite in particular can be full before the filesystem
    itself is 100%, because it lives under /root/.cache/nix on the
    same partition.
    """
    rows = await conn.fetch(
        """
        WITH evidence AS (
          -- admin_orders: top-level error_message column
          SELECT
              ao.appliance_id              AS appliance_id,
              ao.site_id                   AS site_id,
              ao.order_type                AS order_type,
              ao.order_id                  AS order_ref,
              ao.completed_at              AS observed_at,
              ao.error_message             AS error_text,
              'admin_order.error_message'  AS source
            FROM admin_orders ao
           WHERE ao.completed_at > NOW() - INTERVAL '24 hours'
             AND ao.status IN ('failed', 'completed')
             AND (ao.error_message ILIKE '%no space left%'
                  OR ao.error_message ILIKE '%database or disk is full%')

          UNION ALL

          -- admin_orders: JSONB result.error_message (catches legacy payloads)
          SELECT
              ao.appliance_id              AS appliance_id,
              ao.site_id                   AS site_id,
              ao.order_type                AS order_type,
              ao.order_id                  AS order_ref,
              ao.completed_at              AS observed_at,
              ao.result->>'error_message'  AS error_text,
              'admin_order.result'         AS source
            FROM admin_orders ao
           WHERE ao.completed_at > NOW() - INTERVAL '24 hours'
             AND ao.status IN ('failed', 'completed')
             AND (ao.result->>'error_message' ILIKE '%no space left%'
                  OR ao.result->>'error_message' ILIKE '%database or disk is full%')

          UNION ALL

          -- fleet_order_completions: top-level error_message column
          SELECT
              foc.appliance_id                   AS appliance_id,
              NULL                               AS site_id,
              fo.order_type                      AS order_type,
              foc.fleet_order_id::text           AS order_ref,
              foc.completed_at                   AS observed_at,
              foc.error_message                  AS error_text,
              'fleet_completion.error_message'   AS source
            FROM fleet_order_completions foc
            JOIN fleet_orders fo ON fo.id = foc.fleet_order_id
           WHERE foc.completed_at > NOW() - INTERVAL '24 hours'
             AND foc.status = 'failed'
             AND (foc.error_message ILIKE '%no space left%'
                  OR foc.error_message ILIKE '%database or disk is full%')

          UNION ALL

          -- fleet_order_completions: JSONB output.error_message
          SELECT
              foc.appliance_id                   AS appliance_id,
              NULL                               AS site_id,
              fo.order_type                      AS order_type,
              foc.fleet_order_id::text           AS order_ref,
              foc.completed_at                   AS observed_at,
              foc.output->>'error_message'       AS error_text,
              'fleet_completion.output'          AS source
            FROM fleet_order_completions foc
            JOIN fleet_orders fo ON fo.id = foc.fleet_order_id
           WHERE foc.completed_at > NOW() - INTERVAL '24 hours'
             AND foc.status = 'failed'
             AND (foc.output->>'error_message' ILIKE '%no space left%'
                  OR foc.output->>'error_message' ILIKE '%database or disk is full%')
        )
        SELECT
            appliance_id,
            MAX(site_id)                         AS site_id,
            COUNT(*)                             AS evidence_count,
            MAX(observed_at)                     AS latest_observed_at,
            -- Pick the freshest error message as the representative
            (array_agg(error_text ORDER BY observed_at DESC))[1]
                                                 AS latest_error,
            (array_agg(order_type ORDER BY observed_at DESC))[1]
                                                 AS latest_order_type,
            (array_agg(order_ref ORDER BY observed_at DESC))[1]
                                                 AS latest_order_ref,
            (array_agg(source ORDER BY observed_at DESC))[1]
                                                 AS latest_source
          FROM evidence
         WHERE appliance_id IS NOT NULL
         GROUP BY appliance_id
        """
    )
    violations: List[Violation] = []
    for row in rows:
        raw_error = row["latest_error"] or ""
        # Truncate the error to keep the details payload bounded — the
        # head+tail 4KB diagnostic from 0.4.7 is already large; we only
        # need enough of the banner to confirm the match in a glance.
        truncated_error = raw_error[:512] if raw_error else ""
        violations.append(
            Violation(
                site_id=row["site_id"],
                details={
                    "appliance_id": row["appliance_id"],
                    "evidence_count_24h": int(row["evidence_count"] or 0),
                    "latest_observed_at": (
                        row["latest_observed_at"].isoformat()
                        if row["latest_observed_at"] else None
                    ),
                    "latest_order_type": row["latest_order_type"],
                    "latest_order_ref": row["latest_order_ref"],
                    "latest_source": row["latest_source"],
                    "error_excerpt": truncated_error,
                    "remediation": (
                        "Issue a nix_gc fleet_order against this appliance: "
                        "`fleet_cli.py create nix_gc --actor-email "
                        "you@example.com --reason '<20+ char reason>' "
                        "--param older_than_days=7 --param optimise=true`. "
                        "The handler returns before/after bytes in the "
                        "completion payload — confirm bytes_freed > 0 and "
                        "re-canary the failing order."
                    ),
                },
            )
        )
    return violations


async def _check_chronic_without_l2_escalation(conn: asyncpg.Connection) -> List[Violation]:
    """Sev2 — Chronic (site_id, incident_type) pattern with no L2 audit row.

    Closes the Session 220 RT-P1 class. `incident_recurrence_velocity`
    flags `is_chronic=TRUE` for an `(site_id, incident_type)` pair but
    no matching `l2_decisions` row carries `escalation_reason IN
    ('recurrence', 'recurrence_backfill')` in the last 24h — meaning
    the dashboard says "chronic" but the flywheel never recorded a
    root-cause analysis.

    Pre-fix root cause: agent_api.py recurrence detector partitioned
    by `(appliance_id, incident_type)`. Multi-daemon sites (3 daemons
    at north-valley-branch-2) split the count across appliances and
    never tripped `>=3 in 4h`. 320 missed L2 escalations / 7d (verified
    2026-05-12). Detector switched to read from this same velocity
    table by (site_id, incident_type) — closes the routing gap.

    A row in `l2_escalations_missed` for the same (site, type) ALSO
    resolves the invariant: Maya P0-C disclosure path acknowledges
    the gap explicitly in the auditor kit. Per round-table 2026-05-12
    Option B, that parallel table is the §164.528 record, NOT a
    `recurrence_backfill` write into `l2_decisions` (which would
    fabricate evidence of LLM calls that never ran).

    Carol P0-D index `idx_l2_decisions_site_reason_created` (mig 308)
    is required for this NOT EXISTS to scale at 60s cadence on 232K+
    `l2_decisions` rows. `l2_decisions` has no `incident_type` column —
    join through `incidents` to filter by type.
    """
    rows = await conn.fetch(
        """
        SELECT v.site_id, v.incident_type, v.resolved_4h, v.resolved_7d,
               v.computed_at
          FROM incident_recurrence_velocity v
         WHERE v.is_chronic = TRUE
           AND v.computed_at > NOW() - INTERVAL '24 hours'
           AND NOT EXISTS (
                 SELECT 1
                   FROM l2_decisions ld
                   JOIN incidents i ON i.id = ld.incident_id
                  WHERE ld.site_id = v.site_id
                    AND i.incident_type = v.incident_type
                    AND ld.escalation_reason IN ('recurrence', 'recurrence_backfill')
                    AND ld.created_at > NOW() - INTERVAL '24 hours'
             )
           AND NOT EXISTS (
                 SELECT 1
                   FROM l2_escalations_missed lem
                  WHERE lem.site_id = v.site_id
                    AND lem.incident_type = v.incident_type
             )
         ORDER BY v.computed_at DESC
         LIMIT 50
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "incident_type": r["incident_type"],
                "resolved_4h": r["resolved_4h"],
                "resolved_7d": r["resolved_7d"],
                "velocity_computed_at": r["computed_at"].isoformat(),
                "interpretation": (
                    f"Chronic recurrence pattern flagged for "
                    f"({r['site_id']}, {r['incident_type']}) but no "
                    f"L2 root-cause analysis recorded in the last 24h "
                    f"and no entry in l2_escalations_missed disclosure "
                    f"table. Either the detector switch (Session 220 "
                    f"RT-P1) regressed, the velocity loop is stale "
                    f"(see recurrence_velocity_stale invariant), or "
                    f"the disclosure backfill missed this row."
                ),
                "remediation": (
                    "Check recurrence_velocity_stale first. If clean, "
                    "investigate agent_api.py recurrence detector — "
                    "the SELECT against incident_recurrence_velocity "
                    "at the reopen-branch and new-incident branch "
                    "should be reading this row's site_id+incident_type. "
                    "If the row predates 2026-05-12 (the detector "
                    "switch deploy), ship a one-shot INSERT into "
                    "l2_escalations_missed for it."
                ),
            },
        )
        for r in rows
    ]


async def _check_l2_recurrence_partitioning_disclosed(conn: asyncpg.Connection) -> List[Violation]:
    """Sev3 — INFORMATIONAL — recurrence-detector partitioning bug disclosure surface.

    The Session 220 RT-P1 round-table chose Option B (parallel
    l2_escalations_missed table + advisory disclosure) over Option A
    (synthetic backfill into l2_decisions). Per Maya P0-C, fabricating
    `recurrence_backfill` rows in l2_decisions would inject evidence
    of L2 LLM calls that never ran — the same forgery class Session 218
    rejected for pre-mig-175 privileged orders.

    Resolution path: this invariant is INFORMATIONAL. It never
    auto-resolves while `l2_escalations_missed` carries rows.
    Mirror of pre_mig175_privileged_unattested.

    Sev3 because: (1) forward fix is in place — `agent_api.py`
    recurrence detector now partitions correctly; (2) the missed
    escalations are disclosed in the auditor kit
    (`disclosures/missed_l2_escalations.json` + advisory MD); (3)
    no auto-heal is possible without forgery. The invariant exists
    for OPERATOR VISIBILITY, not for action.

    Resolves when: the rows are deleted (NEVER — l2_escalations_missed
    is INSERT-only by trigger) OR a future migration explicitly
    grandfathers them in (requires round-table approval).
    """
    rows = await conn.fetch(
        """
        SELECT site_id, incident_type, missed_count,
               first_observed_at, last_observed_at,
               disclosed_in_kit_version
          FROM l2_escalations_missed
         ORDER BY first_observed_at ASC
         LIMIT 50
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "incident_type": r["incident_type"],
                "missed_count": r["missed_count"],
                "first_observed_at": r["first_observed_at"].isoformat(),
                "last_observed_at": r["last_observed_at"].isoformat(),
                "disclosed_in_kit_version": r["disclosed_in_kit_version"],
                "advisory_ref": (
                    "disclosures/"
                    "SECURITY_ADVISORY_2026-05-12_RECURRENCE_DETECTOR_PARTITIONING.md"
                ),
                "interpretation": (
                    f"Recurrence-detector partitioning bug caused "
                    f"{r['missed_count']} L2 escalation(s) to be "
                    f"skipped for ({r['site_id']}, "
                    f"{r['incident_type']}) between "
                    f"{r['first_observed_at'].isoformat()} and "
                    f"{r['last_observed_at'].isoformat()}. The fix "
                    f"shipped 2026-05-12 (Session 220 RT-P1). Past "
                    f"misses are disclosed in auditor-kit "
                    f"v{r['disclosed_in_kit_version']}+. INFORMATIONAL: "
                    f"new misses are blocked by the detector switch."
                ),
            },
        )
        for r in rows
    ]


async def _check_recurrence_velocity_stale(conn: asyncpg.Connection) -> List[Violation]:
    """Sev3 — Chronic patterns whose velocity row is older than 10 minutes.

    Steve P0-B SPOF guard. `recurrence_velocity_loop` (background_tasks.py)
    recomputes `incident_recurrence_velocity` every 300s. The detector
    in agent_api.py reads from this table with a 10-min freshness guard
    — if the loop stalls, `is_chronic=TRUE` rows go stale and the
    detector misses the escalation.

    Fires sev3 when ≥1 row has `is_chronic=TRUE` AND `computed_at <
    NOW() - INTERVAL '10 minutes'`. Sev3 because:
      * `bg_loop_silent` (sev2) covers complete loop death.
      * This is the partial-degradation case: loop still runs but
        slowly, or the row hasn't been re-touched recently because
        the underlying incident dynamics have shifted.
      * Forward operation is unaffected — new incidents on chronic
        patterns will still attempt the recurrence query; they just
        log `recurrence_velocity_stale` and may miss the >=3 threshold
        until the loop catches up.

    Resolves when the loop catches up and every chronic row's
    computed_at advances inside the 10-min window.
    """
    rows = await conn.fetch(
        """
        SELECT site_id, incident_type, computed_at, resolved_4h,
               EXTRACT(EPOCH FROM (NOW() - computed_at)) / 60 AS age_minutes
          FROM incident_recurrence_velocity
         WHERE is_chronic = TRUE
           AND computed_at < NOW() - INTERVAL '10 minutes'
         ORDER BY computed_at ASC
         LIMIT 50
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "incident_type": r["incident_type"],
                "computed_at": r["computed_at"].isoformat(),
                "age_minutes": round(float(r["age_minutes"]), 1),
                "resolved_4h": r["resolved_4h"],
                "interpretation": (
                    f"Chronic-pattern row "
                    f"({r['site_id']}, {r['incident_type']}) has "
                    f"computed_at "
                    f"{round(float(r['age_minutes']), 1)} minutes "
                    f"behind the 10-min freshness window. The "
                    f"agent_api.py recurrence detector is reading "
                    f"stale velocity data on this pair — chronic "
                    f"escalation may be delayed."
                ),
                "remediation": (
                    "Check bg_loop_silent for recurrence_velocity_loop. "
                    "If healthy, the row simply hasn't been touched by "
                    "a recent recompute pass (no new incidents in the "
                    "rolling windows) — non-actionable, will resolve. "
                    "If bg_loop_silent is also firing, restart the "
                    "background_tasks loop."
                ),
            },
        )
        for r in rows
    ]


async def _check_daemon_heartbeat_unsigned(conn: asyncpg.Connection) -> List[Violation]:
    """Sev2 — Appliance has agent_public_key SET but recent heartbeats
    are NULL-signed.

    Counsel Rule 4 orphan coverage at multi-device-enterprise fleet
    scale. An appliance whose `site_appliances.agent_public_key` is set
    SHOULD be signing every heartbeat (daemon code at
    `appliance/internal/daemon/phonehome.go:827 SystemInfoSigned()` does
    this when the evidence-submitter's signing key is non-nil). If 12+
    consecutive heartbeats in the last 60 minutes arrive with NULL
    `agent_signature`, the daemon is silently NOT signing — potentially
    compromised, version-rolled-back, or daemon-bug. Sev2 because:
      * The unsigned heartbeats are still data the substrate accepts.
      * Forward operation continues — checkin is soft-verified.
      * But the cryptographic-attestation-chain claim (master BAA
        Article 3.2) is undermined for the affected appliance.

    Threshold per D1 protocol round-table 2026-05-13: 12 consecutive at
    ~5-min cadence ≈ 60 minutes of silent unsigned heartbeats.

    Resolves automatically once the appliance emits a signed heartbeat.
    """
    rows = await conn.fetch(
        """
        WITH recent_per_appliance AS (
            SELECT
                ah.site_id,
                ah.appliance_id,
                COUNT(*) FILTER (WHERE ah.agent_signature IS NULL) AS unsigned_count,
                COUNT(*) AS total_count,
                MAX(ah.observed_at) AS last_seen_at
              FROM appliance_heartbeats ah
              JOIN site_appliances sa
                ON sa.appliance_id = ah.appliance_id
               AND sa.site_id = ah.site_id
             WHERE ah.observed_at > NOW() - INTERVAL '60 minutes'
               AND sa.agent_public_key IS NOT NULL
               AND sa.agent_public_key <> ''
             GROUP BY ah.site_id, ah.appliance_id
        )
        SELECT site_id, appliance_id, unsigned_count, total_count, last_seen_at
          FROM recent_per_appliance
         WHERE unsigned_count >= 12
         ORDER BY unsigned_count DESC
         LIMIT 50
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "appliance_id": str(r["appliance_id"]),
                "unsigned_count": r["unsigned_count"],
                "total_count": r["total_count"],
                "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
                "interpretation": (
                    f"Appliance {r['appliance_id']} at site {r['site_id']} "
                    f"has agent_public_key on file but emitted "
                    f"{r['unsigned_count']} of {r['total_count']} heartbeats "
                    f"in the last 60 minutes with NULL agent_signature. "
                    f"Daemon is silently not signing — investigate version "
                    f"+ evidence-submitter signing key state."
                ),
                "remediation": (
                    "1. Check daemon version on the appliance — should be "
                    "≥0.4.x with D1 signing path implemented (phonehome.go:827). "
                    "2. Verify evidence_submitter.SigningKey() returns non-nil "
                    "in daemon.go:867 runCheckin. "
                    "3. If daemon is current AND signing key is present but "
                    "signature is empty, check signing-loop errors in daemon "
                    "logs (slog warning 'heartbeat signing failed'). "
                    "4. If appliance was recently re-flashed, verify "
                    "site_appliances.agent_public_key matches the new daemon's "
                    "key (rotation grace is 15 minutes)."
                ),
            },
        )
        for r in rows
    ]


async def _check_daemon_heartbeat_signature_invalid(conn: asyncpg.Connection) -> List[Violation]:
    """Sev1 — Appliance signature is present but does NOT verify under
    any known pubkey.

    Counsel Rule 4 compromise-detection class at multi-device-enterprise
    fleet scale. When `signature_valid=FALSE` for ≥3 heartbeats in the
    last 15 minutes, either (a) the appliance's signing key has been
    replaced by an attacker (compromise), or (b) the canonical-payload
    format has drifted between daemon and backend lockstep (engineering
    bug). Sev1 because either explanation requires immediate operator
    attention — compromise is bad; drift means signature verification
    is broken platform-wide.

    Threshold per D1 protocol round-table 2026-05-13: 3 consecutive at
    ~5-min cadence ≈ 15 minutes of invalid signatures.

    Resolves automatically once the appliance emits a signed heartbeat
    that DOES verify under a known pubkey (either current or
    previous-within-grace).
    """
    rows = await conn.fetch(
        """
        SELECT
            ah.site_id,
            ah.appliance_id,
            COUNT(*) FILTER (WHERE ah.signature_valid = FALSE) AS invalid_count,
            COUNT(*) AS total_count,
            MAX(ah.observed_at) AS last_seen_at
          FROM appliance_heartbeats ah
         WHERE ah.observed_at > NOW() - INTERVAL '15 minutes'
           AND ah.signature_valid IS NOT NULL
         GROUP BY ah.site_id, ah.appliance_id
        HAVING COUNT(*) FILTER (WHERE ah.signature_valid = FALSE) >= 3
         ORDER BY invalid_count DESC
         LIMIT 50
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "appliance_id": str(r["appliance_id"]),
                "invalid_count": r["invalid_count"],
                "total_count": r["total_count"],
                "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
                "interpretation": (
                    f"Appliance {r['appliance_id']} at site {r['site_id']} "
                    f"emitted {r['invalid_count']} of {r['total_count']} "
                    f"heartbeats in the last 15 minutes with "
                    f"signature_valid=FALSE. Either the signing key has "
                    f"been replaced (compromise) OR the canonical-payload "
                    f"format drifted between daemon and backend."
                ),
                "remediation": (
                    "1. SEV1 — escalate to operator immediately. "
                    "2. Inspect the appliance's agent_public_key vs the "
                    "daemon's actual signing key (SSH the appliance and "
                    "check evidence-submitter state). "
                    "3. If the keys match, the canonical-payload format "
                    "has drifted — diff phonehome.go:837 vs "
                    "signature_auth.py::_heartbeat_canonical_payload. "
                    "All 4 lockstep surfaces (daemon, backend verifier, "
                    "this runbook, auditor kit verify.sh) MUST agree. "
                    "4. If the keys do NOT match, treat as potential "
                    "compromise: rotate the appliance's agent_public_key "
                    "via the standard rotation path, isolate, investigate."
                ),
            },
        )
        for r in rows
    ]


async def _check_daemon_on_legacy_path_b(conn: asyncpg.Connection) -> List[Violation]:
    """Sev3-info until 2026-08-13, then sev2 — appliance is using legacy
    path B for heartbeat verification (daemon did NOT supply
    heartbeat_timestamp; backend reconstructed ±60s window).

    D1 protocol round-table 2026-05-13 chose hybrid (option c): path A
    (daemon-supplied heartbeat_timestamp) for daemon v0.5.0+; path B
    (backend reconstruction) for backward-compat with pre-v0.5.0
    daemons. After the 2026-08-13 deprecation deadline (90 days from
    launch), every appliance should have rolled forward to v0.5.0+.
    Daemons still on path B past that date are stuck on legacy protocol
    and should be upgraded.

    Sev3-info today (informational, no operator action required).
    Auto-escalates to sev2 after 2026-08-13. Tracks fleet-rollout
    progress on the substrate dashboard.
    """
    from datetime import date
    DEPRECATION_DATE = date(2026, 8, 13)
    today = date.today()
    is_past_deprecation = today >= DEPRECATION_DATE

    rows = await conn.fetch(
        """
        SELECT
            ah.site_id,
            ah.appliance_id,
            COUNT(*) AS path_b_count,
            MAX(ah.observed_at) AS last_seen_at
          FROM appliance_heartbeats ah
         WHERE ah.observed_at > NOW() - INTERVAL '24 hours'
           AND ah.signature_canonical_format = 'v1b-reconstruct'
         GROUP BY ah.site_id, ah.appliance_id
        HAVING COUNT(*) >= 12
         ORDER BY path_b_count DESC
         LIMIT 50
        """
    )
    days_until_deprecation = (DEPRECATION_DATE - today).days

    return [
        Violation(
            site_id=r["site_id"],
            details={
                "appliance_id": str(r["appliance_id"]),
                "path_b_count": r["path_b_count"],
                "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
                "deprecation_deadline": DEPRECATION_DATE.isoformat(),
                "days_until_deprecation": days_until_deprecation,
                "is_past_deprecation": is_past_deprecation,
                "interpretation": (
                    f"Appliance {r['appliance_id']} at site {r['site_id']} "
                    f"emitted {r['path_b_count']} heartbeats in the last "
                    f"24h using legacy path B (backend reconstructed the "
                    f"±60s timestamp window because the daemon did not "
                    f"supply heartbeat_timestamp natively). Daemon should "
                    f"be upgraded to v0.5.0+ before "
                    f"{DEPRECATION_DATE.isoformat()} "
                    f"({'PAST' if is_past_deprecation else f'{days_until_deprecation} days remaining'})."
                ),
                "remediation": (
                    "Upgrade the daemon on this appliance to v0.5.0 or later. "
                    "The v0.5.0 daemon includes the HeartbeatTimestamp field "
                    "in CheckinRequest, enabling path A verification "
                    "(daemon-supplied timestamp; deterministic; not "
                    "skew-window-dependent). Path B is the fastest credible "
                    "stopgap for the legacy fleet, but path A is the "
                    "auditor-preferred verification mode."
                ),
            },
        )
        for r in rows
    ]


async def _check_canonical_compliance_score_drift(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """sev2 — Counsel Rule 1 runtime drift detector (Task #64 Phase 2c).

    For each recent customer-facing sample in `canonical_metric_samples`
    (last 15 minutes, classification='customer-facing'), recompute the
    canonical helper with the SAME kwargs the endpoint used + compare to
    the captured value. Differences >0.5 indicate a non-canonical code
    path produced a different value than the canonical helper would —
    Rule 1 runtime violation.

    Three-layer defense-in-depth (Carol Gate A v4):
      1. CHECK constraint blocks invalid `classification` writes (mig 314)
      2. Partial index `WHERE classification='customer-facing'` physically
         excludes operator-internal samples from drift-scan
      3. The WHERE clause below explicitly filters to customer-facing —
         this lens

    Cache-bypass + tolerance per Gate A v4:
      - `_skip_cache=True` — substrate's recompute must NOT hit the 60s
        TTL cache, or the comparison collapses to a no-op within TTL
        window
      - tolerance `0.5` — accommodates legitimate boundary-NOW-shift
        variability (sample captured at t=0, recompute at t=N seconds
        later; the window has slid by N seconds; small numeric drift is
        legitimate). Still tight enough to catch real non-canonical-path
        drift (typically >1.0).

    Runbook: substrate_runbooks/canonical_compliance_score_drift.md.
    """
    rows = await conn.fetch(
        """
        SELECT sample_id, tenant_id, captured_at, captured_value,
               endpoint_path, helper_input
          FROM canonical_metric_samples
         WHERE metric_class = 'compliance_score'
           AND classification = 'customer-facing'
           AND captured_at > NOW() - INTERVAL '15 minutes'
           AND captured_value IS NOT NULL
         ORDER BY captured_at DESC
         LIMIT 50
        """
    )
    out: List[Violation] = []
    for r in rows:
        helper_input = r["helper_input"] or {}
        if isinstance(helper_input, str):
            import json as _json
            try:
                helper_input = _json.loads(helper_input)
            except Exception:
                continue
        site_ids = helper_input.get("site_ids") or []
        if not site_ids:
            continue
        window_days = helper_input.get("window_days", 30)
        include_incidents = bool(helper_input.get("include_incidents", False))
        try:
            from compliance_score import compute_compliance_score
        except ImportError:
            from .compliance_score import compute_compliance_score  # type: ignore
        try:
            helper_result = await compute_compliance_score(
                conn, site_ids=site_ids,
                window_days=window_days,
                include_incidents=include_incidents,
                _skip_cache=True,
            )
        except Exception:
            continue  # helper error is not drift; substrate skips
        helper_score = helper_result.overall_score
        if helper_score is None or r["captured_value"] is None:
            continue
        try:
            captured_value = float(r["captured_value"])
            helper_score_f = float(helper_score)
        except (TypeError, ValueError):
            continue
        if abs(helper_score_f - captured_value) > 0.5:
            out.append(
                Violation(
                    site_id=(site_ids[0] if site_ids else None),
                    details={
                        "sample_id": str(r["sample_id"]),
                        "tenant_id": str(r["tenant_id"]),
                        "endpoint_path": r["endpoint_path"],
                        "captured_value": captured_value,
                        "canonical_value": helper_score_f,
                        "delta": round(helper_score_f - captured_value, 2),
                        "captured_at": r["captured_at"].isoformat(),
                        "interpretation": (
                            f"Endpoint {r['endpoint_path']} returned "
                            f"{captured_value} for tenant "
                            f"{r['tenant_id']} but canonical helper "
                            f"produces {helper_score_f} for the same "
                            f"inputs. Non-canonical computation path "
                            f"is in use OR a bug exists between the "
                            f"helper and the endpoint's response shape."
                        ),
                        "remediation": (
                            f"Inspect {r['endpoint_path']} source: it "
                            f"should delegate to "
                            f"compliance_score.compute_compliance_score. "
                            f"Likely uses one of the allowlist "
                            f"`migrate`-class entries (db_queries, "
                            f"frameworks, etc.) — drive-down PR migrates "
                            f"that path to canonical helper."
                        ),
                    },
                )
            )
    return out


async def _check_daemon_heartbeat_signature_unverified(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Sev1 — Verifier-crashed-silently class. Closes the orphan-coverage
    gap that masked D1 inert state for ~13 days pre-fix 2026-05-13.

    Existing sibling invariants are BLIND to this specific failure mode:
    - daemon_heartbeat_unsigned queries `agent_signature IS NULL`
    - daemon_heartbeat_signature_invalid filters `signature_valid IS NOT NULL`
    - But `agent_signature IS NOT NULL AND signature_valid IS NULL`
      (verifier hit exception, soft-failed, stored NULL) — neither fires.

    Counsel Rule 4 PRIMARY (orphan coverage). Counsel Rule 3 SECONDARY
    (chain-of-custody for signature chain). Sev1 because the unverified
    state is at least as serious as known-invalid — legitimate-but-
    unverified and attacker-but-unverified are indistinguishable rows.

    Threshold (per sev1 sibling parity with daemon_heartbeat_signature_invalid):
    ≥3 unverified heartbeats in the last 15 minutes.

    Pre-D1 daemons + dev appliances that never registered a key are
    excluded via JOIN site_appliances on agent_public_key IS NOT NULL
    (same guard as daemon_heartbeat_unsigned).
    """
    rows = await conn.fetch(
        """
        SELECT
            ah.site_id,
            ah.appliance_id,
            COUNT(*) FILTER (WHERE ah.agent_signature IS NOT NULL
                              AND ah.signature_valid IS NULL) AS unverified_count,
            COUNT(*) AS total_count,
            MAX(ah.observed_at) AS last_seen_at
          FROM appliance_heartbeats ah
          JOIN site_appliances sa ON sa.id = ah.appliance_id
         WHERE ah.observed_at > NOW() - INTERVAL '15 minutes'
           AND sa.agent_public_key IS NOT NULL
           AND sa.deleted_at IS NULL
         GROUP BY ah.site_id, ah.appliance_id
        HAVING COUNT(*) FILTER (WHERE ah.agent_signature IS NOT NULL
                                 AND ah.signature_valid IS NULL) >= 3
         ORDER BY unverified_count DESC
         LIMIT 50
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "appliance_id": str(r["appliance_id"]),
                "unverified_count": r["unverified_count"],
                "total_count": r["total_count"],
                "last_seen_at": (
                    r["last_seen_at"].isoformat() if r["last_seen_at"] else None
                ),
                "interpretation": (
                    f"Appliance {r['appliance_id']} at site {r['site_id']} "
                    f"emitted {r['unverified_count']} of {r['total_count']} "
                    f"heartbeats in the last 15 minutes with "
                    f"agent_signature present but signature_valid stored "
                    f"as NULL. Backend's verifier hit an exception while "
                    f"validating + soft-failed. This is the detection gap "
                    f"that masked D1 inert state for ~13 days pre-fix "
                    f"adb7671a — neither daemon_heartbeat_unsigned (NULL "
                    f"signature) nor daemon_heartbeat_signature_invalid "
                    f"(FALSE) catches this state."
                ),
                "remediation": (
                    "1. Check mcp-server logs for ImportError, "
                    "ModuleNotFoundError, or other exceptions inside "
                    "sites.py:appliance_checkin around the signature "
                    "verification path. "
                    "2. Verify site_appliances.agent_public_key for the "
                    "affected appliance is non-NULL + parseable as Ed25519. "
                    "3. If verification is failing for a known reason "
                    "(e.g., new payload format), the canonical-payload "
                    "reconstruction in signature_auth.verify_heartbeat_"
                    "signature may need a code update. "
                    "4. If unverified rows persist >1h, escalate — this "
                    "is the chain-of-custody integrity class."
                ),
            },
        )
        for r in rows
    ]


async def _check_canonical_devices_freshness(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """sev2 — Counsel Rule 1 sibling of canonical_compliance_score_drift.

    The reconciliation loop that maintains canonical_devices (Task #73
    Phase 1, mig 319) ticks every 60s. If a site has discovered_devices
    rows recently updated but canonical_devices.reconciled_at > 60min
    stale, the loop has stalled OR is wedged on that site. Customers
    may see stale device counts in their monthly compliance packet
    PDFs (compliance_packet.py emits the canonical count post-migration)
    + the device-inventory page (device_sync.get_site_devices reads
    canonical post-migration).

    Per discovered_devices_freshness sibling: filter site_appliances
    where status='online' and deleted_at IS NULL — only active sites.
    """
    rows = await conn.fetch(
        """
        WITH active_sites AS (
            SELECT DISTINCT sa.site_id
              FROM site_appliances sa
             WHERE sa.deleted_at IS NULL
               AND sa.status = 'online'
        ),
        site_freshness AS (
            SELECT s.site_id,
                   MAX(cd.reconciled_at) AS last_reconciled_at,
                   COUNT(cd.canonical_id) AS canonical_row_count
              FROM active_sites s
         LEFT JOIN canonical_devices cd ON cd.site_id = s.site_id
             GROUP BY s.site_id
        )
        SELECT site_id, last_reconciled_at, canonical_row_count,
               CASE
                 WHEN last_reconciled_at IS NULL THEN NULL
                 ELSE EXTRACT(EPOCH FROM (NOW() - last_reconciled_at))/60
               END AS minutes_stale
          FROM site_freshness
         WHERE last_reconciled_at IS NULL
            OR last_reconciled_at < NOW() - INTERVAL '60 minutes'
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "last_reconciled_at": (
                    r["last_reconciled_at"].isoformat()
                    if r["last_reconciled_at"] else None
                ),
                "canonical_row_count": r["canonical_row_count"],
                "minutes_stale": (
                    float(r["minutes_stale"]) if r["minutes_stale"] is not None else None
                ),
                "interpretation": (
                    "Canonical devices reconciliation loop has not "
                    "updated this site in >60 minutes. Customer-facing "
                    "device counts (compliance_packet PDF + device "
                    "inventory page) may be stale."
                ),
                "remediation": (
                    "Check /admin/substrate-health for bg_loop_silent. "
                    "Inspect mcp-server logs for ERROR-level "
                    "'canonical_devices' UPSERT failures. If wedged on "
                    "a specific site, restart mcp-server to re-init "
                    "background_tasks."
                ),
            },
        )
        for r in rows
    ]


# --- Engine ----------------------------------------------------------


# Minutes an open violation must sit without a refresh before the engine
# will mark it resolved. Prevents open→resolve→open thrash when a check
# returns the same underlying problem but the row-set composition shifts
# tick-to-tick (e.g. `status` briefly flips during a checkin UPDATE, or a
# boundary like `last_checkin > NOW()-1h` clips one row out for one tick).
# Observed in prod 2026-04-21: 4 invariants flipping 12–60× / day on stable
# state. 5 min is large enough to cover checkin jitter (appliances check in
# at ~10s cadence), small enough that a genuine recovery still clears within
# one human-scale glance at the dashboard.
RESOLVE_HYSTERESIS_MINUTES = 5


async def run_assertions_once(pool) -> Dict[str, int]:
    """Run every registered assertion exactly once. UPSERTs new
    violations, marks resolved any open rows whose violations no
    longer appear (after RESOLVE_HYSTERESIS_MINUTES of no refresh).
    Returns a {opened, refreshed, resolved, held, errors} counters
    dict for observability.

    Per-assertion isolation (2026-05-11 Gate A APPROVE-WITH-FIXES,
    audit/coach-substrate-per-assertion-refactor-gate-a-2026-05-11.md):
    each assertion's check + open_rows fetch + UPSERT/INSERT/RESOLVE
    runs inside its OWN `admin_transaction(pool)` block. Replaces the
    prior single-outer-conn design where one check's asyncpg
    InterfaceError poisoned every subsequent check in the same tick
    (cascade-fail class observed 2026-05-11, 7 errors per 10min in
    prod, mitigated defensively by commit b55846cb).

    Under per-assertion conns, one timeout costs 1 assertion's data
    (1.6% of tick fidelity), not all 60+. The defensive `conn_dead`
    band-aid from b55846cb is REMOVED in this commit per Gate A P0-5
    — under per-assertion isolation the flag would skip valid work
    for no reason.
    """
    import asyncpg
    from .tenant_middleware import admin_transaction

    counters = {"opened": 0, "refreshed": 0, "resolved": 0, "held": 0, "errors": 0}

    for a in ALL_ASSERTIONS:
        # Gate A P0-2: the entire per-assertion body (check + open_rows
        # fetch + UPSERT/INSERT/RESOLVE) MUST run inside ONE
        # admin_transaction so read-then-write consistency is preserved
        # within the tick. Wrapping only `a.check(conn)` would create a
        # TOCTOU window vs concurrent UPSERTs from a previous tick.
        try:
            async with admin_transaction(pool) as conn:
                try:
                    current = await a.check(conn)
                except asyncpg.InterfaceError as e:
                    # Per-assertion isolation: one InterfaceError costs
                    # 1 assertion. Subsequent assertions get a fresh
                    # conn from admin_transaction on the next iteration.
                    logger.warning(
                        "assertion %s hit InterfaceError — fresh conn next iteration. %s",
                        a.name, str(e)[:200],
                    )
                    counters["errors"] += 1
                    continue
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
                # only its own counter. (Under per-assertion admin_transaction
                # outer, conn.transaction() now opens a true SAVEPOINT —
                # behavior preserved per Gate A P0-1.)
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
                                # Task #66 B1: synthetic marker derived at
                                # INSERT time from site_id pattern. NOT
                                # threaded through the Violation dataclass —
                                # the SQL itself is the source of truth so
                                # callers can't forget to set it. mig 323
                                # added the column NOT NULL DEFAULT FALSE.
                                await conn.execute(
                                    """
                                    INSERT INTO substrate_violations
                                          (invariant_name, severity, site_id, details, synthetic)
                                    VALUES ($1, $2, $3, $4::jsonb,
                                            $3 LIKE 'synthetic-%')
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
                                result = await conn.execute(
                                    """
                                    UPDATE substrate_violations
                                       SET resolved_at = NOW()
                                     WHERE id = $1
                                       AND last_seen_at < NOW() - make_interval(mins => $2)
                                    """,
                                    row_id, RESOLVE_HYSTERESIS_MINUTES,
                                )
                            # asyncpg returns 'UPDATE <rowcount>'. Parse the count so
                            # we can distinguish a true resolve from a hysteresis hold.
                            rowcount = 0
                            try:
                                rowcount = int(result.split()[-1])
                            except (ValueError, IndexError):
                                pass
                            if rowcount >= 1:
                                counters["resolved"] += 1
                                logger.info(
                                    "substrate violation RESOLVED: invariant=%s site=%s id=%s",
                                    a.name,
                                    site_key,
                                    row_id,
                                )
                            else:
                                counters["held"] += 1
                        except Exception:
                            logger.error(
                                "substrate resolve failed: invariant=%s site=%s id=%s",
                                a.name, site_key, row_id, exc_info=True,
                            )
                            counters["errors"] += 1
        except asyncpg.InterfaceError as e:
            # Outer admin_transaction itself failed (pool exhausted /
            # PgBouncer outage). Count + continue — next iteration
            # acquires a fresh conn.
            logger.warning(
                "assertion %s admin_transaction failed: %s",
                a.name, str(e)[:200],
            )
            counters["errors"] += 1
            continue

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
    via health_monitor's broader background_tasks orchestration.

    Gate A P0-4 refactor (2026-05-11): hands `pool` to
    `run_assertions_once` (per-assertion admin_transaction inside),
    and runs `_ttl_sweep` in its OWN admin_transaction block — so
    a poisoned per-assertion conn no longer suppresses the sweep
    (the prior `if errors == 0` short-circuit silently dropped the
    TTL reclaim on any tick where one of the 60+ assertions hit an
    InterfaceError, growing sigauth_observations unboundedly)."""
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_transaction

    await asyncio.sleep(120)  # Let pool + migrations settle on cold start.
    logger.info("Substrate Integrity Engine started (interval=60s, assertions=%d)",
                len(ALL_ASSERTIONS))

    while True:
        # Heartbeat (Session 213 P3 — round-table flagged the substrate
        # engine itself as missing instrumentation. If THIS loop hangs,
        # nothing detects substrate violations — it's the meta-loop).
        try:
            from .bg_heartbeat import record_heartbeat
            record_heartbeat("substrate_assertions")
        except Exception:
            pass

        deleted = 0
        counters = {"opened": 0, "refreshed": 0, "resolved": 0,
                    "held": 0, "errors": 0}
        try:
            pool = await get_pool()
            counters = await run_assertions_once(pool)
            # TTL sweep is independent of per-assertion state — runs
            # in its OWN admin_transaction block. Errors in one tick's
            # assertions MUST NOT suppress the sweep (would let
            # sigauth_observations grow unboundedly).
            try:
                async with admin_transaction(pool) as sweep_conn:
                    deleted = await _ttl_sweep(sweep_conn)
            except Exception:
                logger.error("ttl_sweep failed", exc_info=True)
            if counters["opened"] or counters["resolved"] or deleted:
                logger.info(
                    "assertions tick: opened=%d refreshed=%d resolved=%d held=%d errors=%d sigauth_swept=%d",
                    counters["opened"], counters["refreshed"],
                    counters["resolved"], counters["held"],
                    counters["errors"], deleted,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("assertions_loop tick failed", exc_info=True)

        await asyncio.sleep(60)
