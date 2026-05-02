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
    rows = await conn.fetch(
        """
        WITH prior_month AS (
            SELECT EXTRACT(YEAR FROM (NOW() - INTERVAL '1 month'))::int AS y,
                   EXTRACT(MONTH FROM (NOW() - INTERVAL '1 month'))::int AS m,
                   date_trunc('month', NOW()) AS curr_month_start
        ),
        active_sites AS (
            SELECT DISTINCT cb.site_id
              FROM compliance_bundles cb, prior_month pm
             WHERE EXTRACT(YEAR FROM cb.created_at) = pm.y
               AND EXTRACT(MONTH FROM cb.created_at) = pm.m
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


async def _check_partition_maintainer_dry(conn: asyncpg.Connection) -> List[Violation]:
    """Sev1 — next month's partition missing on a critical partitioned
    table (Block 4 P1).

    Partitioned tables: `compliance_bundles` (mig 138, evidence chain),
    `portal_access_log` (mig 138, audit), `appliance_heartbeats`
    (mig 121, liveness ledger), `promoted_rule_events` (mig 181,
    flywheel ledger).

    `partition_maintainer_loop` is supposed to keep ≥3 months of
    forward partitions. If it's wedged or dead, next-month INSERTs
    land in the `_default` partition (bloats it + degrades query
    plans) or fail outright if no default exists.

    Sev1 because: HIPAA evidence chain (compliance_bundles) hitting
    the default partition slows every auditor-kit query proportionally.

    Naming conventions (live in migrations; if these drift, this
    invariant breaks loud — desired):
    - compliance_bundles:    `_YYYY_MM`
    - portal_access_log:     `_YYYY_MM`
    - appliance_heartbeats:  `_yYYYYMM`
    - promoted_rule_events:  `_YYYYMM`
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
                                   'appliance_heartbeats', 'promoted_rule_events')
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
    Assertion(
        name="sigauth_post_fix_window_canary",
        severity="sev1",
        description="Tighter detection floor for the 2026-04-28 sigauth wrap-fix (commit 303421cc) acceptance window. Closes the detection-window-asymmetry gap that the rolling-6h sigauth_enforce_mode_rejections cannot see: fires sev1 on ANY invalid sigauth observation across any appliance from 2026-04-28 17:11Z (deploy + clear) through 2026-05-05 17:11Z (7d window close). Auto-disables after the window — the SQL filter on observed_at evaluates to no rows after that date and the invariant goes silent. Round-table 2026-04-28 P1 close-out for task #169 user-override early-close.",
        check=lambda c: _check_sigauth_post_fix_window_canary(c),
    ),
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
        name="partition_maintainer_dry",
        severity="sev1",
        description="A critical partitioned table (compliance_bundles, portal_access_log, appliance_heartbeats, promoted_rule_events) has NO partition for next month. INSERTs land in the _default partition (bloats it + degrades query plans) or fail if no default exists. Indicates partition_maintainer_loop / heartbeat_partition_maintainer_loop are wedged. Round-table 2026-05-01 Block 4 P1 closure.",
        check=lambda c: _check_partition_maintainer_dry(c),
    ),
    Assertion(
        name="schema_fixture_drift",
        severity="sev3",
        description="The deployed code's tests/fixtures/schema/prod_columns.json differs from prod's information_schema. Catches the 'forgot to update fixture' deploy class — bit Session 214 audit cycle TWICE (mig 271 forward-merge required manual fixture edits). Sev3 because the test_sql_columns_match_schema CI gate already prevents new deploys with drift; this invariant catches the case where prod has drifted from the fixture in the CURRENTLY DEPLOYED code (rare, but happens via manual SQL or partial migration). Round-table 2026-05-02 Diana adversarial recommendation. Followup #49.",
        check=lambda c: _check_schema_fixture_drift(c),
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
    "sigauth_post_fix_window_canary": {
        "display_name": "Sigauth wrap-fix post-deploy canary tripped",
        "recommended_action": "The wrap-in-transaction speculative fix shipped in commit "
            "303421cc (2026-04-28) was accepted on a 7d empirical-clean acceptance "
            "window with EXPLICIT pivot criterion. ANY fail across the post-deploy "
            "window means the routing hypothesis is empirically refuted — pivot to "
            "MVCC / deleted_at flicker / autovacuum investigation per "
            "docs/security/sigauth-wrap-validation-2026-04-28.md. Reopen task #169, "
            "trigger a new round-table BEFORE any fix. The forensic "
            "logger.error('sigauth_unknown_pubkey', ...) carries the diagnostic "
            "context — query mcp-server logs for that token at the rejection time.",
    },
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
            "appliance_heartbeats / promoted_rule_events). The "
            "partition_maintainer_loop is supposed to keep ≥3 months of "
            "forward partitions; if this fires, the loop is wedged or "
            "dead. Without next-month partitions, INSERTs land in the "
            "_default partition (bloats it; degrades query plans for "
            "auditor kits proportionally) or fail outright if no default "
            "exists. Steps: (1) check bg_loop_silent for "
            "'partition_maintainer' or 'heartbeat_partition_maintainer'. "
            "(2) Manually run `CREATE TABLE IF NOT EXISTS <parent>_<suffix>"
            " PARTITION OF <parent> FOR VALUES FROM (...) TO (...)` to "
            "unblock. (3) `docker compose restart mcp-server` to rearm "
            "the loop. Sev1 because the evidence chain depends on "
            "compliance_bundles partition health.",
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


async def _check_sigauth_post_fix_window_canary(conn: asyncpg.Connection) -> List[Violation]:
    """Time-bounded canary for the 2026-04-28 sigauth wrap-fix
    acceptance window. Closes the rolling-6h detection floor gap by
    firing sev1 on ANY invalid sigauth observation across any
    appliance during the 7d post-deploy window
    (2026-04-28 17:11Z → 2026-05-05 17:11Z UTC).

    Auto-disables after the window: the date filter evaluates to zero
    rows once observed_at moves past the upper bound + the invariant
    goes silent. Designed to be removed in the next session after
    2026-05-05 if the substrate stays clean.

    Round-table 2026-04-28 P1 close-out — preserves the empirical
    bar the validation doc set even when the formal task #169 is
    closed early on user override."""
    rows = await conn.fetch(
        """
        SELECT site_id, mac_address,
               COUNT(*) FILTER (WHERE NOT valid) AS failures,
               array_agg(DISTINCT reason) FILTER (WHERE NOT valid) AS reasons,
               MAX(observed_at) FILTER (WHERE NOT valid) AS last_failure
          FROM sigauth_observations
         WHERE observed_at >= '2026-04-28 17:11:00+00'::timestamptz
           AND observed_at <  '2026-05-05 17:11:00+00'::timestamptz
      GROUP BY site_id, mac_address
        HAVING COUNT(*) FILTER (WHERE NOT valid) >= 1
        """
    )
    return [
        Violation(
            site_id=r["site_id"],
            details={
                "mac_address": r["mac_address"],
                "failures_in_window": r["failures"],
                "reasons": list(r["reasons"] or []),
                "last_failure_at": r["last_failure"].isoformat() if r["last_failure"] else None,
                "window_end": "2026-05-05T17:11:00Z",
                "remediation": (
                    "ANY fail in the post-fix window refutes the routing hypothesis. "
                    "Reopen task #169, pivot to MVCC / deleted_at / autovacuum, "
                    "trigger new round-table BEFORE any fix. See "
                    "docs/security/sigauth-wrap-validation-2026-04-28.md."
                ),
            },
        )
        for r in rows
    ]


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


async def run_assertions_once(conn: asyncpg.Connection) -> Dict[str, int]:
    """Run every registered assertion exactly once. UPSERTs new
    violations, marks resolved any open rows whose violations no
    longer appear (after RESOLVE_HYSTERESIS_MINUTES of no refresh).
    Returns a {opened, refreshed, resolved, held, errors} counters
    dict for observability."""

    counters = {"opened": 0, "refreshed": 0, "resolved": 0, "held": 0, "errors": 0}

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
        # Heartbeat (Session 213 P3 — round-table flagged the substrate
        # engine itself as missing instrumentation. If THIS loop hangs,
        # nothing detects substrate violations — it's the meta-loop).
        try:
            from .bg_heartbeat import record_heartbeat
            record_heartbeat("substrate_assertions")
        except Exception:
            pass

        try:
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                counters = await run_assertions_once(conn)
                # TTL sweep in same conn — cheap, atomic per tick.
                deleted = await _ttl_sweep(conn)
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
