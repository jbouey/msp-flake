"""Prometheus-compatible metrics endpoint.

Exposes platform health metrics in Prometheus text exposition format.
Accepts the Prometheus scraper bearer (PROMETHEUS_SCRAPE_TOKEN env) OR
admin authentication (cookie / user Bearer). Generates text format
manually — no prometheus_client dependency needed.
"""

import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(tags=["metrics"])


async def require_scrape_or_admin(request: Request) -> dict[str, Any]:
    """Accept Prometheus scraper bearer OR admin auth.

    Prometheus scrapers send a static `Authorization: Bearer <TOKEN>`
    where TOKEN = PROMETHEUS_SCRAPE_TOKEN env. The token lives in env
    only (never in DB) and is compared with hmac.compare_digest to
    avoid timing oracles. When the env var is unset, this dependency
    degrades to admin-only auth, preserving pre-Session-209 behavior.
    """
    expected = os.getenv("PROMETHEUS_SCRAPE_TOKEN", "").strip()
    if expected:
        header = request.headers.get("authorization", "")
        if header.startswith("Bearer "):
            submitted = header[7:].strip()
            if submitted and hmac.compare_digest(submitted, expected):
                return {"username": "prometheus_scraper", "role": "scraper"}
    # Fall through to admin auth. require_auth raises 401 on failure.
    return await require_auth(request)

# =============================================================================
# Prometheus text format helpers
# =============================================================================

PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _format_metric(
    name: str,
    help_text: str,
    metric_type: str,
    values: list[tuple[dict[str, str], float]],
) -> str:
    """Format a single Prometheus metric block (HELP + TYPE + sample lines)."""
    lines = [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} {metric_type}",
    ]
    for labels, value in values:
        if labels:
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"{name}{{{label_str}}} {value}")
        else:
            lines.append(f"{name} {value}")
    return "\n".join(lines)


def _gauge(name: str, help_text: str, values: list[tuple[dict[str, str], float]]) -> str:
    return _format_metric(name, help_text, "gauge", values)


def _counter(name: str, help_text: str, values: list[tuple[dict[str, str], float]]) -> str:
    return _format_metric(name, help_text, "counter", values)


# =============================================================================
# Endpoint
# =============================================================================


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics(auth: dict = Depends(require_scrape_or_admin)):
    """Return platform metrics in Prometheus text exposition format.

    Queries are executed fresh on each scrape — no background loop.
    Each metric section is wrapped in its own try/except so a single
    table being absent does not break the entire response.
    """
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import admin_connection

    sections: list[str] = []

    try:
        pool = await get_pool()
    except Exception:
        logger.exception("Failed to get database pool for metrics")
        return PlainTextResponse(
            "# Failed to connect to database\n",
            media_type=PROM_CONTENT_TYPE,
            status_code=503,
        )

    try:
        async with admin_connection(pool) as conn:
            now = datetime.now(timezone.utc)

            # --- Appliance status (gauge) ---
            try:
                rows = await conn.fetch(
                    "SELECT last_checkin FROM site_appliances"
                )
                counts = {"online": 0, "stale": 0, "offline": 0}
                for row in rows:
                    lc = row["last_checkin"]
                    if lc is None:
                        counts["offline"] += 1
                    else:
                        age = now - lc
                        if age < timedelta(minutes=15):
                            counts["online"] += 1
                        elif age < timedelta(hours=1):
                            counts["stale"] += 1
                        else:
                            counts["offline"] += 1
                sections.append(_gauge(
                    "osiriscare_appliances_total",
                    "Number of appliances by status",
                    [({"status": s}, float(c)) for s, c in counts.items()],
                ))
            except Exception:
                logger.exception("metrics: appliance status query failed")

            # --- Per-appliance offline gauge (alert surface) ---
            # F4 (Phase 15 closing): enterprise-grade alerting needs to
            # identify WHICH appliance is offline, not just the count.
            # Emit 1.0 for each appliance currently offline so Prom rules
            # can page with the display_name + site_id attached.
            try:
                offline_rows = await conn.fetch("""
                    SELECT appliance_id, site_id,
                           COALESCE(display_name, hostname, appliance_id) AS label,
                           EXTRACT(EPOCH FROM (NOW() - last_checkin))::int AS since_sec
                    FROM site_appliances
                    WHERE status = 'offline'
                      AND deleted_at IS NULL
                """)
                sections.append(_gauge(
                    "osiriscare_appliance_offline",
                    "1 if appliance is currently offline (labels: site_id, appliance_id, display_name, since_sec)",
                    [
                        (
                            {
                                "site_id": r["site_id"][:80],
                                "appliance_id": r["appliance_id"][:80],
                                "display_name": r["label"][:80],
                                "since_sec": str(r["since_sec"] or 0),
                            },
                            1.0,
                        )
                        for r in offline_rows
                    ] or [({}, 0.0)],
                ))
            except Exception:
                logger.exception("metrics: per-appliance offline gauge query failed")

            # --- Flywheel Spine: lifecycle_state distribution ---
            # Session 206 redesign: single source of truth for flywheel
            # health. Every alert fires off this metric family.
            try:
                lifecycle_rows = await conn.fetch("""
                    SELECT lifecycle_state, COUNT(*) AS n
                    FROM promoted_rules
                    GROUP BY lifecycle_state
                """)
                # Always emit all known states so dashboards don't render
                # as "no data" when there are zero rules in a state.
                all_states = [
                    "proposed", "shadow", "approved", "rolling_out",
                    "active", "regime_warning", "auto_disabled",
                    "graduated", "retired",
                ]
                counts = {r["lifecycle_state"]: int(r["n"]) for r in lifecycle_rows}
                sections.append(_gauge(
                    "osiriscare_flywheel_rules_by_state",
                    "Promoted rules per lifecycle_state (Session 206 Spine)",
                    [
                        ({"state": s}, float(counts.get(s, 0)))
                        for s in all_states
                    ],
                ))
            except Exception:
                logger.exception("metrics: flywheel lifecycle gauge query failed")

            # --- Flywheel Spine: event volume by type, last 1h ---
            try:
                evt_rows = await conn.fetch("""
                    SELECT event_type,
                           COUNT(*) FILTER (WHERE outcome = 'success') AS ok,
                           COUNT(*) FILTER (WHERE outcome = 'failed') AS failed
                    FROM promoted_rule_events
                    WHERE created_at > NOW() - INTERVAL '1 hour'
                    GROUP BY event_type
                """)
                all_types = [
                    "pattern_detected", "shadow_evaluated", "promotion_approved",
                    "rollout_issued", "rollout_acked", "first_execution",
                    "regime_warning", "regime_critical", "regime_absolute_low",
                    "auto_disabled", "manually_disabled", "graduated",
                    "retired_site_dead", "retired_manual",
                    "operator_acknowledged", "operator_re_enabled",
                ]
                ok_by = {r["event_type"]: int(r["ok"]) for r in evt_rows}
                fail_by = {r["event_type"]: int(r["failed"]) for r in evt_rows}
                sections.append(_gauge(
                    "osiriscare_flywheel_events_1h",
                    "Flywheel state-transition events in last 1h by type + outcome",
                    [
                        ({"event_type": t, "outcome": "success"},
                         float(ok_by.get(t, 0)))
                        for t in all_types
                    ] + [
                        ({"event_type": t, "outcome": "failed"},
                         float(fail_by.get(t, 0)))
                        for t in all_types
                    ],
                ))
            except Exception:
                logger.exception("metrics: flywheel events gauge query failed")

            # --- Migration 184 Phase 4 consent metrics ---
            # Grants, revokes, executed-with-consent events, and expired
            # request tokens — all labeled by class_id so the SRE
            # dashboard can spot per-class trends.
            try:
                consent_events = await conn.fetch("""
                    SELECT
                        COALESCE(SUBSTR(rule_id, 9, POSITION('@' IN rule_id) - 9), '?') AS class_id,
                        event_type,
                        COUNT(*) AS n
                    FROM promoted_rule_events
                    WHERE event_type LIKE 'runbook.%'
                      AND created_at > NOW() - INTERVAL '7 days'
                    GROUP BY 1, 2
                """)
                if consent_events:
                    grants = [
                        ({"class_id": r["class_id"]}, float(r["n"]))
                        for r in consent_events if r["event_type"] == "runbook.consented"
                    ]
                    revokes = [
                        ({"class_id": r["class_id"]}, float(r["n"]))
                        for r in consent_events if r["event_type"] == "runbook.revoked"
                    ]
                    executed_with = [
                        ({"class_id": r["class_id"], "outcome": "with_consent"},
                         float(r["n"]))
                        for r in consent_events
                        if r["event_type"] == "runbook.executed_with_consent"
                    ]
                    if grants:
                        sections.append(_gauge(
                            "osiriscare_consent_grants_7d",
                            "Class-level consent grants in last 7d by class_id",
                            grants,
                        ))
                    if revokes:
                        sections.append(_gauge(
                            "osiriscare_consent_revokes_7d",
                            "Class-level consent revokes in last 7d by class_id",
                            revokes,
                        ))
                    if executed_with:
                        sections.append(_gauge(
                            "osiriscare_consent_executed_7d",
                            "runbook.executed_with_consent events in last 7d",
                            executed_with,
                        ))
            except Exception:
                logger.exception("metrics: consent events query failed")

            try:
                # Tokens that expired unconsumed — delivery health signal
                expired_tokens = await conn.fetchval("""
                    SELECT COUNT(*) FROM consent_request_tokens
                    WHERE consumed_at IS NULL AND expires_at < NOW()
                      AND expires_at > NOW() - INTERVAL '7 days'
                """)
                pending_tokens = await conn.fetchval("""
                    SELECT COUNT(*) FROM consent_request_tokens
                    WHERE consumed_at IS NULL AND expires_at > NOW()
                """)
                sections.append(_gauge(
                    "osiriscare_consent_token_expired_7d",
                    "Consent request tokens that expired unconsumed in last 7d",
                    [({}, float(expired_tokens or 0))],
                ))
                sections.append(_gauge(
                    "osiriscare_consent_token_pending",
                    "Consent request tokens currently pending approval",
                    [({}, float(pending_tokens or 0))],
                ))
            except Exception:
                # Table missing pre-189 → silent skip
                pass

            # --- Substrate Integrity Engine: active violations (alert surface) ---
            # Session 209 wired this gauge so Prometheus/alertmanager can
            # page on sev1 invariants (`evidence_chain_stalled`,
            # `flywheel_ledger_stalled`, `provisioning_stalled`, …) without
            # a human having to refresh /admin/substrate-health. Emits one
            # sample per active violation, or a single zero-value sentinel
            # if the fleet is clean — dashboards don't render as "no data".
            try:
                sv_rows = await conn.fetch("""
                    SELECT invariant_name,
                           severity,
                           COALESCE(site_id, '') AS site_id,
                           minutes_open::float  AS minutes_open
                      FROM v_substrate_violations_active
                """)
                if sv_rows:
                    sections.append(_gauge(
                        "osiriscare_substrate_violations_active",
                        "Active substrate invariant violations "
                        "(1 sample per open violation; labels: invariant_name, severity, site_id)",
                        [
                            (
                                {
                                    "invariant_name": r["invariant_name"][:80],
                                    "severity": r["severity"][:20],
                                    "site_id": (r["site_id"] or "")[:80],
                                },
                                1.0,
                            )
                            for r in sv_rows
                        ],
                    ))
                    sections.append(_gauge(
                        "osiriscare_substrate_violation_minutes_open",
                        "Minutes since each active substrate violation opened",
                        [
                            (
                                {
                                    "invariant_name": r["invariant_name"][:80],
                                    "severity": r["severity"][:20],
                                    "site_id": (r["site_id"] or "")[:80],
                                },
                                float(r["minutes_open"] or 0.0),
                            )
                            for r in sv_rows
                        ],
                    ))
                else:
                    # Zero-rows sentinel so alertmanager can detect "all clear"
                    # and a dashboard query (sum by invariant_name) returns 0
                    # instead of no-data.
                    sections.append(_gauge(
                        "osiriscare_substrate_violations_active",
                        "Active substrate invariant violations "
                        "(1 sample per open violation; labels: invariant_name, severity, site_id)",
                        [({"invariant_name": "_none", "severity": "_none", "site_id": ""}, 0.0)],
                    ))
            except Exception:
                logger.exception("metrics: substrate_violations gauge query failed")

            # --- Flywheel Spine: stuck rules + operator_ack_required ---
            try:
                stuck = await conn.fetchval("""
                    SELECT COUNT(*) FROM promoted_rules
                    WHERE lifecycle_state IN
                          ('proposed', 'shadow', 'approved', 'rolling_out')
                      AND lifecycle_state_updated_at < NOW() - INTERVAL '3 days'
                """)
                ack_pending = await conn.fetchval("""
                    SELECT COUNT(*) FROM promoted_rules
                    WHERE operator_ack_required = TRUE
                      AND operator_ack_at IS NULL
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_stuck_rules",
                    "Rules stuck in non-terminal state > 3 days",
                    [({}, float(stuck or 0))],
                ))
                sections.append(_gauge(
                    "osiriscare_flywheel_operator_ack_pending",
                    "Auto-disabled rules awaiting operator acknowledgement",
                    [({}, float(ack_pending or 0))],
                ))
            except Exception:
                logger.exception("metrics: flywheel stuck/ack gauge query failed")

            # --- Open incidents by severity (gauge) ---
            try:
                rows = await conn.fetch("""
                    SELECT LOWER(COALESCE(severity, 'medium')) AS sev,
                           COUNT(*) AS cnt
                    FROM incidents
                    WHERE status != 'resolved'
                    GROUP BY LOWER(COALESCE(severity, 'medium'))
                """)
                sev_map = {r["sev"]: r["cnt"] for r in rows}
                values = [
                    ({"severity": sev}, float(sev_map.get(sev, 0)))
                    for sev in ("critical", "high", "medium", "low")
                ]
                sections.append(_gauge(
                    "osiriscare_incidents_open",
                    "Number of open incidents by severity",
                    values,
                ))
            except Exception:
                logger.exception("metrics: incidents query failed")

            # --- Healing execution telemetry (counter) ---
            # The table's canonical column is `resolution_level`
            # (L1/L2/L3/manual); we expose it as Prometheus label
            # `tier` for backwards compat with existing dashboards.
            # Prior to v40.4 this SELECT used `tier` directly and
            # raised UndefinedColumnError on every scrape since
            # 2026-04-21. The exception was eaten by the try/except
            # so metrics dashboards silently lost healing counters
            # for 48h+.
            try:
                rows = await conn.fetch("""
                    SELECT resolution_level AS tier, success, COUNT(*) AS cnt
                    FROM execution_telemetry
                    GROUP BY resolution_level, success
                """)
                values = []
                for row in rows:
                    tier = row["tier"] or "unknown"
                    success = "true" if row["success"] else "false"
                    values.append(({"tier": tier, "success": success}, float(row["cnt"])))
                # Ensure standard tiers always appear
                seen = {(v[0]["tier"], v[0]["success"]) for v in values}
                for tier in ("L1", "L2", "L3"):
                    for success in ("true", "false"):
                        if (tier, success) not in seen:
                            values.append(({"tier": tier, "success": success}, 0.0))
                sections.append(_counter(
                    "osiriscare_healing_executions_total",
                    "Total healing executions by tier and outcome",
                    values,
                ))
            except Exception:
                logger.exception("metrics: execution telemetry query failed")

            # --- Evidence bundles (counter) ---
            try:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) AS cnt FROM evidence_bundles"
                )
                sections.append(_counter(
                    "osiriscare_evidence_bundles_total",
                    "Total evidence bundles collected",
                    [({}, float(row["cnt"]))],
                ))
            except Exception:
                logger.exception("metrics: evidence bundles query failed")

            # --- Fleet orders pending (gauge) ---
            try:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) AS cnt FROM fleet_orders WHERE status = 'active'"
                )
                sections.append(_gauge(
                    "osiriscare_fleet_orders_pending",
                    "Number of pending/active fleet orders",
                    [({}, float(row["cnt"]))],
                ))
            except Exception:
                logger.exception("metrics: fleet orders query failed")

            # --- Checkin rate last hour (gauge) ---
            try:
                one_hour_ago = now - timedelta(hours=1)
                row = await conn.fetchrow(
                    "SELECT COUNT(*) AS cnt FROM site_appliances "
                    "WHERE last_checkin >= $1",
                    one_hour_ago,
                )
                sections.append(_gauge(
                    "osiriscare_checkin_rate_1h",
                    "Number of appliance checkins in the last hour",
                    [({}, float(row["cnt"]))],
                ))
            except Exception:
                logger.exception("metrics: checkin rate query failed")

            # --- Log entries total (counter) ---
            try:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) AS cnt FROM log_entries"
                )
                sections.append(_counter(
                    "osiriscare_log_entries_total",
                    "Total log entries ingested",
                    [({}, float(row["cnt"]))],
                ))
            except Exception:
                logger.exception("metrics: log entries query failed")

            # --- Learning system metrics (gauge) ---
            try:
                row = await conn.fetchrow("""
                    SELECT
                        (SELECT COUNT(*) FROM aggregated_pattern_stats
                         WHERE promotion_eligible = true) as eligible_patterns,
                        (SELECT COUNT(*) FROM learning_promotion_candidates
                         WHERE approval_status = 'pending') as pending_promotions,
                        (SELECT COUNT(*) FROM learning_promotion_candidates
                         WHERE approval_status = 'approved'
                           AND approved_at > NOW() - INTERVAL '30 days') as recent_promotions
                """)
                sections.append(_gauge(
                    "osiriscare_learning_eligible_patterns",
                    "Patterns eligible for L2-to-L1 promotion",
                    [({}, float(row["eligible_patterns"]))],
                ))
                sections.append(_gauge(
                    "osiriscare_learning_pending_promotions",
                    "Promotion candidates awaiting approval",
                    [({}, float(row["pending_promotions"]))],
                ))
                sections.append(_gauge(
                    "osiriscare_learning_recent_promotions",
                    "Promotions approved in last 30 days",
                    [({}, float(row["recent_promotions"]))],
                ))
            except Exception:
                logger.exception("metrics: learning system query failed")

            # --- Escalation queue metrics (gauge) ---
            try:
                rows = await conn.fetch("""
                    SELECT status, COUNT(*) as cnt,
                           EXTRACT(EPOCH FROM AVG(NOW() - created_at)) as avg_age_secs
                    FROM escalation_tickets
                    WHERE status NOT IN ('resolved', 'closed')
                    GROUP BY status
                """)
                ticket_values = []
                age_values = []
                for row in rows:
                    ticket_values.append(({"status": row["status"]}, float(row["cnt"])))
                    age_values.append(({"status": row["status"]}, float(row["avg_age_secs"] or 0)))
                if ticket_values:
                    sections.append(_gauge(
                        "osiriscare_escalation_tickets_open",
                        "Open escalation tickets by status",
                        ticket_values,
                    ))
                    sections.append(_gauge(
                        "osiriscare_escalation_ticket_age_seconds",
                        "Average age of open escalation tickets in seconds",
                        age_values,
                    ))
            except Exception:
                logger.exception("metrics: escalation queue query failed")

            # --- Device discovery metrics (gauge) ---
            try:
                row = await conn.fetchrow("""
                    SELECT
                        COUNT(*) as total_devices,
                        COUNT(*) FILTER (WHERE last_seen > NOW() - INTERVAL '24 hours') as active_24h,
                        COUNT(*) FILTER (WHERE last_seen < NOW() - INTERVAL '7 days') as stale_7d
                    FROM discovered_devices
                """)
                sections.append(_gauge(
                    "osiriscare_discovered_devices_total",
                    "Total discovered devices",
                    [({}, float(row["total_devices"]))],
                ))
                sections.append(_gauge(
                    "osiriscare_discovered_devices_active_24h",
                    "Devices seen in last 24 hours",
                    [({}, float(row["active_24h"]))],
                ))
                sections.append(_gauge(
                    "osiriscare_discovered_devices_stale_7d",
                    "Devices not seen in over 7 days",
                    [({}, float(row["stale_7d"]))],
                ))
            except Exception:
                logger.exception("metrics: device discovery query failed")

            # --- CVE watch metrics (gauge) ---
            try:
                row = await conn.fetchrow("""
                    SELECT
                        COUNT(*) as total_cves,
                        COUNT(*) FILTER (WHERE severity = 'critical') as critical_cves,
                        COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '7 days') as new_7d
                    FROM cve_entries
                """)
                sections.append(_gauge(
                    "osiriscare_cve_total",
                    "Total tracked CVEs",
                    [({}, float(row["total_cves"]))],
                ))
                sections.append(_gauge(
                    "osiriscare_cve_critical",
                    "Critical severity CVEs",
                    [({}, float(row["critical_cves"]))],
                ))
                sections.append(_gauge(
                    "osiriscare_cve_new_7d",
                    "CVEs discovered in last 7 days",
                    [({}, float(row["new_7d"]))],
                ))
            except Exception:
                logger.exception("metrics: CVE watch query failed")

            # --- Organization health (gauge) ---
            try:
                # Per-org counts
                org_stats = await conn.fetch("""
                    SELECT
                        co.id::text as org_id,
                        co.name,
                        co.status,
                        co.max_sites,
                        co.max_users,
                        (SELECT COUNT(*) FROM sites WHERE client_org_id = co.id) as site_count,
                        (SELECT COUNT(*) FROM client_users WHERE client_org_id = co.id) as user_count,
                        (SELECT COUNT(*) FROM incidents i
                         JOIN sites s ON s.site_id = i.site_id
                         WHERE s.client_org_id = co.id
                           AND i.reported_at > NOW() - INTERVAL '24 hours') as incidents_24h,
                        co.baa_expiration_date,
                        co.deprovisioned_at
                    FROM client_orgs co
                    WHERE co.deprovisioned_at IS NULL
                """)

                if org_stats:
                    sections.append(_gauge(
                        "osiriscare_orgs_total",
                        "Total active organizations",
                        [({}, float(len(org_stats)))],
                    ))
                    sections.append(_gauge(
                        "osiriscare_org_sites",
                        "Number of sites per organization",
                        [({"org": o["name"][:40]}, float(o["site_count"])) for o in org_stats],
                    ))
                    sections.append(_gauge(
                        "osiriscare_org_users",
                        "Number of client_users per organization",
                        [({"org": o["name"][:40]}, float(o["user_count"])) for o in org_stats],
                    ))
                    sections.append(_gauge(
                        "osiriscare_org_incidents_24h",
                        "Incidents per organization in last 24h",
                        [({"org": o["name"][:40]}, float(o["incidents_24h"])) for o in org_stats],
                    ))
                    # Quota usage as percentage
                    site_quota = [
                        ({"org": o["name"][:40]},
                         float(o["site_count"]) / max(o["max_sites"] or 1, 1) * 100)
                        for o in org_stats if o["max_sites"]
                    ]
                    if site_quota:
                        sections.append(_gauge(
                            "osiriscare_org_site_quota_pct",
                            "Site quota usage percentage per org (alert if >90)",
                            site_quota,
                        ))

                # BAA expiration alerts
                baa_expiring = await conn.fetchval("""
                    SELECT COUNT(*) FROM client_orgs
                    WHERE baa_expiration_date IS NOT NULL
                      AND baa_expiration_date <= CURRENT_DATE + INTERVAL '30 days'
                      AND baa_expiration_date > CURRENT_DATE
                      AND deprovisioned_at IS NULL
                """)
                baa_expired = await conn.fetchval("""
                    SELECT COUNT(*) FROM client_orgs
                    WHERE baa_expiration_date IS NOT NULL
                      AND baa_expiration_date <= CURRENT_DATE
                      AND deprovisioned_at IS NULL
                """)
                sections.append(_gauge(
                    "osiriscare_org_baa_expiring_30d",
                    "Orgs with BAA expiring in next 30 days",
                    [({}, float(baa_expiring or 0))],
                ))
                sections.append(_gauge(
                    "osiriscare_org_baa_expired",
                    "Orgs with expired BAA (CRITICAL — blocks operations)",
                    [({}, float(baa_expired or 0))],
                ))

                # Deprovisioned orgs (audit trail)
                deprov = await conn.fetchval("""
                    SELECT COUNT(*) FROM client_orgs WHERE deprovisioned_at IS NOT NULL
                """)
                sections.append(_gauge(
                    "osiriscare_orgs_deprovisioned",
                    "Orgs in deprovisioning/retention state",
                    [({}, float(deprov or 0))],
                ))
            except Exception:
                logger.exception("metrics: org query failed")

            # --- Flywheel promotion pipeline health (gauge) ---
            try:
                # Candidate pipeline stages
                cand = await conn.fetchrow("""
                    SELECT
                        COUNT(*) FILTER (WHERE approval_status = 'pending') as pending,
                        COUNT(*) FILTER (WHERE approval_status = 'approved') as approved,
                        COUNT(*) FILTER (WHERE approval_status = 'rejected') as rejected
                    FROM learning_promotion_candidates
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_candidates",
                    "Learning promotion candidates by status",
                    [
                        ({"status": "pending"}, float(cand["pending"] or 0)),
                        ({"status": "approved"}, float(cand["approved"] or 0)),
                        ({"status": "rejected"}, float(cand["rejected"] or 0)),
                    ],
                ))

                # Promoted rules by source
                pr_source = await conn.fetchrow("""
                    SELECT
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE status = 'active') as active,
                        COUNT(*) FILTER (WHERE status = 'disabled') as disabled
                    FROM promoted_rules
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_promoted_rules",
                    "Promoted rules by status",
                    [
                        ({"status": "active"}, float(pr_source["active"] or 0)),
                        ({"status": "disabled"}, float(pr_source["disabled"] or 0)),
                    ],
                ))

                # L1 rules by source
                l1_src = await conn.fetch("""
                    SELECT source, COUNT(*) as cnt
                    FROM l1_rules WHERE enabled = true
                    GROUP BY source
                """)
                if l1_src:
                    sections.append(_gauge(
                        "osiriscare_flywheel_l1_rules_by_source",
                        "Enabled L1 rules by source (built-in vs promoted vs synced)",
                        [({"source": r["source"] or "unknown"}, float(r["cnt"])) for r in l1_src],
                    ))

                # Stuck candidates (approved but no promoted_rules row) — ALERT metric
                stuck_count = await conn.fetchval("""
                    SELECT COUNT(*) FROM learning_promotion_candidates lpc
                    LEFT JOIN promoted_rules pr
                        ON pr.pattern_signature = lpc.pattern_signature
                        AND pr.site_id = lpc.site_id
                    WHERE lpc.approval_status = 'approved' AND pr.rule_id IS NULL
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_stuck_candidates",
                    "Approved candidates with no promoted_rules row (alert if >0)",
                    [({}, float(stuck_count or 0))],
                ))

                # Eligibility pipeline: how many patterns awaiting manual approval
                eligible_waiting = await conn.fetchval("""
                    SELECT COUNT(*) FROM aggregated_pattern_stats
                    WHERE promotion_eligible = true
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_eligible_waiting",
                    "Patterns eligible for promotion but not yet promoted",
                    [({}, float(eligible_waiting or 0))],
                ))

                # Promotion rate (promotions per day last 7d)
                promo_rate = await conn.fetchrow("""
                    SELECT
                        COUNT(*) FILTER (WHERE promoted_at > NOW() - INTERVAL '24 hours') as last_24h,
                        COUNT(*) FILTER (WHERE promoted_at > NOW() - INTERVAL '7 days') as last_7d
                    FROM promoted_rules
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_promotion_rate_24h",
                    "New promotions in the last 24 hours",
                    [({}, float(promo_rate["last_24h"] or 0))],
                ))
                sections.append(_gauge(
                    "osiriscare_flywheel_promotion_rate_7d",
                    "New promotions in the last 7 days",
                    [({}, float(promo_rate["last_7d"] or 0))],
                ))

                # Pipeline stall detector — time since last promotion
                stall = await conn.fetchval("""
                    SELECT EXTRACT(EPOCH FROM (NOW() - MAX(promoted_at)))
                    FROM promoted_rules
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_last_promotion_age_seconds",
                    "Seconds since the most recent promotion (alert if >604800 = 7d)",
                    [({}, float(stall or 0))],
                ))
            except Exception:
                logger.exception("metrics: flywheel query failed")

            # --- Mesh health (gauge) ---
            try:
                # Per-site mesh state: ring size, peers, assignment coverage
                rows = await conn.fetch("""
                    SELECT
                        site_id,
                        COUNT(*) as appliance_count,
                        COUNT(*) FILTER (WHERE last_checkin > NOW() - INTERVAL '5 minutes') as online_count,
                        AVG(
                            CASE WHEN daemon_health IS NOT NULL
                                THEN (daemon_health->>'mesh_ring_size')::int
                                ELSE NULL END
                        ) as avg_ring_size,
                        AVG(
                            CASE WHEN daemon_health IS NOT NULL
                                THEN (daemon_health->>'mesh_peer_count')::int
                                ELSE NULL END
                        ) as avg_peer_count
                    FROM site_appliances
                    WHERE last_checkin > NOW() - INTERVAL '10 minutes'
                    GROUP BY site_id
                    HAVING COUNT(*) > 1
                """)
                if rows:
                    sections.append(_gauge(
                        "osiriscare_mesh_appliance_count",
                        "Number of appliances per mesh site",
                        [({"site": r["site_id"][:40]}, float(r["appliance_count"])) for r in rows],
                    ))
                    sections.append(_gauge(
                        "osiriscare_mesh_online_count",
                        "Online appliances per mesh site",
                        [({"site": r["site_id"][:40]}, float(r["online_count"])) for r in rows],
                    ))
                    sections.append(_gauge(
                        "osiriscare_mesh_avg_ring_size",
                        "Average ring size reported by appliances (should equal online_count)",
                        [({"site": r["site_id"][:40]}, float(r["avg_ring_size"] or 0)) for r in rows],
                    ))
                    sections.append(_gauge(
                        "osiriscare_mesh_avg_peer_count",
                        "Average peer count per appliance (should equal ring_size - 1)",
                        [({"site": r["site_id"][:40]}, float(r["avg_peer_count"] or 0)) for r in rows],
                    ))

                # Assignment drift: ring_size vs online_count mismatch
                drift_row = await conn.fetchrow("""
                    SELECT COUNT(DISTINCT site_id) as drift_sites
                    FROM (
                        SELECT site_id,
                               COUNT(*) FILTER (WHERE last_checkin > NOW() - INTERVAL '5 minutes') as online,
                               AVG((daemon_health->>'mesh_ring_size')::int) as ring
                        FROM site_appliances
                        WHERE last_checkin > NOW() - INTERVAL '10 minutes'
                          AND daemon_health IS NOT NULL
                        GROUP BY site_id
                        HAVING COUNT(*) > 1
                    ) s
                    WHERE s.online != s.ring
                """)
                sections.append(_gauge(
                    "osiriscare_mesh_drift_sites",
                    "Sites where ring size disagrees with online appliance count (alert if >0)",
                    [({}, float(drift_row["drift_sites"] or 0))],
                ))

                # Coverage gaps: targets with wrong number of assignments
                # (should be exactly 1 owner per target in a healthy mesh)
                try:
                    gap_row = await conn.fetchrow("""
                        WITH all_targets AS (
                            SELECT site_id,
                                   jsonb_array_elements_text(assigned_targets) as target
                            FROM site_appliances
                            WHERE assigned_targets IS NOT NULL
                              AND last_checkin > NOW() - INTERVAL '10 minutes'
                        )
                        SELECT
                            COUNT(*) FILTER (WHERE assignment_count > 1) as overlaps,
                            COUNT(*) FILTER (WHERE assignment_count = 0) as orphans
                        FROM (
                            SELECT site_id, target, COUNT(*) as assignment_count
                            FROM all_targets
                            GROUP BY site_id, target
                        ) t
                    """)
                    sections.append(_gauge(
                        "osiriscare_mesh_target_overlaps",
                        "Targets assigned to multiple appliances (duplicate scans, alert if >0)",
                        [({}, float(gap_row["overlaps"] or 0))],
                    ))
                    sections.append(_gauge(
                        "osiriscare_mesh_target_orphans",
                        "Targets with no owner (coverage hole, alert if >0)",
                        [({}, float(gap_row["orphans"] or 0))],
                    ))
                except Exception:
                    pass

                # Audit log rate (assignments changing per hour)
                audit_row = await conn.fetchrow("""
                    SELECT COUNT(*) as changes_1h
                    FROM mesh_assignment_audit
                    WHERE created_at > NOW() - INTERVAL '1 hour'
                """)
                sections.append(_gauge(
                    "osiriscare_mesh_assignment_changes_1h",
                    "Mesh assignment changes in last hour (high rate = instability)",
                    [({}, float(audit_row["changes_1h"] or 0))],
                ))
            except Exception:
                logger.exception("metrics: mesh query failed")

            # --- OTS proof pipeline health (gauge) ---
            try:
                row = await conn.fetchrow("""
                    SELECT
                        COUNT(*) FILTER (WHERE status = 'anchored') as anchored,
                        COUNT(*) FILTER (WHERE status = 'pending') as pending,
                        COUNT(*) FILTER (WHERE status = 'expired') as expired,
                        COUNT(*) FILTER (WHERE status = 'verified') as verified,
                        EXTRACT(EPOCH FROM (NOW() - MIN(submitted_at))) FILTER (
                            WHERE status = 'pending'
                        ) as oldest_pending_age_seconds,
                        EXTRACT(EPOCH FROM (NOW() - MAX(anchored_at))) FILTER (
                            WHERE status = 'anchored'
                        ) as latest_anchor_age_seconds
                    FROM ots_proofs
                """)
                sections.append(_gauge(
                    "osiriscare_ots_proofs",
                    "OTS proofs by status",
                    [
                        ({"status": "anchored"}, float(row["anchored"] or 0)),
                        ({"status": "pending"}, float(row["pending"] or 0)),
                        ({"status": "expired"}, float(row["expired"] or 0)),
                        ({"status": "verified"}, float(row["verified"] or 0)),
                    ],
                ))
                # SLA metric: age of oldest pending proof (alert if > 24h)
                sections.append(_gauge(
                    "osiriscare_ots_oldest_pending_seconds",
                    "Age in seconds of the oldest pending OTS proof (alert if >86400)",
                    [({}, float(row["oldest_pending_age_seconds"] or 0))],
                ))
                # SLA metric: time since last successful anchor (alert if > 6h)
                sections.append(_gauge(
                    "osiriscare_ots_latest_anchor_age_seconds",
                    "Age in seconds since the most recent OTS anchor (alert if >21600)",
                    [({}, float(row["latest_anchor_age_seconds"] or 0))],
                ))
            except Exception:
                logger.exception("metrics: OTS proofs query failed")

            # --- OTS calendar health (gauge) ---
            try:
                # Per-calendar anchor counts in last 24h
                rows = await conn.fetch("""
                    SELECT
                        calendar_url,
                        COUNT(*) FILTER (WHERE status = 'anchored') as anchored,
                        COUNT(*) as total
                    FROM ots_proofs
                    WHERE submitted_at > NOW() - INTERVAL '24 hours'
                      AND calendar_url IS NOT NULL
                    GROUP BY calendar_url
                """)
                if rows:
                    sections.append(_gauge(
                        "osiriscare_ots_calendar_success_24h",
                        "Per-calendar anchor success count in last 24h",
                        [({"calendar": r["calendar_url"][:60]}, float(r["anchored"])) for r in rows],
                    ))
                    sections.append(_gauge(
                        "osiriscare_ots_calendar_total_24h",
                        "Per-calendar total proof count in last 24h",
                        [({"calendar": r["calendar_url"][:60]}, float(r["total"])) for r in rows],
                    ))
            except Exception:
                logger.exception("metrics: OTS calendar query failed")

            # --- Pattern sync health (gauge) ---
            try:
                row = await conn.fetchrow("""
                    SELECT
                        COUNT(*) FILTER (WHERE sync_status = 'success') as success,
                        COUNT(*) FILTER (WHERE sync_status = 'partial') as partial,
                        COUNT(*) FILTER (WHERE sync_status = 'failed') as failed
                    FROM appliance_pattern_sync
                    WHERE synced_at > NOW() - INTERVAL '24 hours'
                """)
                sections.append(_gauge(
                    "osiriscare_pattern_sync_24h",
                    "Pattern sync results in last 24 hours",
                    [
                        ({"status": "success"}, float(row["success"])),
                        ({"status": "partial"}, float(row["partial"])),
                        ({"status": "failed"}, float(row["failed"])),
                    ],
                ))
            except Exception:
                logger.exception("metrics: pattern sync query failed")

            # --- Flywheel health (3 gauges from Session 205 audit) ---
            # Why these three: each one would have caught the broken flywheel
            # weeks before the manual audit did.
            #
            # 1) L2 success ratio < 50% over 24h means LLM is producing useless
            #    output that incurs cost without resolving incidents.
            # 2) promotions_deployed_7d == 0 means the data-flywheel loop is
            #    not closed: rules may promote but never reach an appliance.
            # 3) orphan_runbooks > 0 means promoted L1 rules reference a
            #    runbook_id that doesn't exist — they will fail on execution.
            try:
                l2_row = await conn.fetchrow("""
                    SELECT
                        COUNT(*) FILTER (WHERE success) as successes,
                        COUNT(*) as total
                    FROM execution_telemetry
                    WHERE resolution_level = 'L2'
                      AND created_at > NOW() - INTERVAL '24 hours'
                """)
                total = float(l2_row["total"] or 0)
                successes = float(l2_row["successes"] or 0)
                ratio = (successes / total) if total > 0 else 1.0
                sections.append(_gauge(
                    "osiriscare_flywheel_l2_success_ratio_24h",
                    "L2 LLM success ratio over last 24 hours (1.0 = all succeeded)",
                    [({}, ratio)],
                ))
                sections.append(_gauge(
                    "osiriscare_flywheel_l2_calls_24h",
                    "L2 LLM call count over last 24 hours",
                    [({}, total)],
                ))
            except Exception:
                logger.exception("metrics: L2 success ratio query failed")

            try:
                row = await conn.fetchrow("""
                    SELECT COALESCE(SUM(deployment_count), 0) as deployments
                    FROM promoted_rules
                    WHERE last_deployed_at > NOW() - INTERVAL '7 days'
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_promotions_deployed_7d",
                    "Total promoted-rule deployments to appliances in last 7 days",
                    [({}, float(row["deployments"] or 0))],
                ))
            except Exception:
                logger.exception("metrics: promotion deployment query failed")

            try:
                row = await conn.fetchrow("""
                    SELECT COUNT(*) as orphans
                    FROM l1_rules l
                    LEFT JOIN runbooks r ON r.runbook_id = l.runbook_id
                    WHERE l.promoted_from_l2 = true
                      AND l.enabled = true
                      AND r.runbook_id IS NULL
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_orphan_runbooks",
                    "Promoted L1 rules referencing a runbook_id missing from runbooks library",
                    [({}, float(row["orphans"] or 0))],
                ))
            except Exception:
                logger.exception("metrics: orphan runbooks query failed")

            # Phase 14 T2: notifier queue depth. > 0 for more than a few
            # minutes means email delivery is stuck — investigate SMTP.
            try:
                row = await conn.fetchrow("""
                    SELECT COUNT(*) AS n FROM compliance_bundles
                    WHERE check_type = 'privileged_access'
                      AND notified_at IS NULL
                      AND checked_at > NOW() - INTERVAL '1 day'
                """)
                sections.append(_gauge(
                    "osiriscare_privileged_notifier_queue_depth",
                    "Unnotified privileged-access bundles (last 24h)",
                    [({}, float(row["n"] or 0))],
                ))
            except Exception:
                logger.exception("metrics: privileged notifier queue failed")

            # Phase 14 watchdog: chain-enforcement trigger installed?
            # This is the cryptographic floor. If 0, the DB's enforcement
            # layer is missing — PAGE immediately.
            try:
                row = await conn.fetchrow("""
                    SELECT COUNT(*) AS n FROM pg_trigger
                    WHERE tgname IN (
                        'trg_enforce_privileged_chain',
                        'trg_enforce_privileged_immutability'
                    )
                    AND NOT tgisinternal
                """)
                sections.append(_gauge(
                    "osiriscare_privileged_chain_triggers_installed",
                    "Count of privileged-chain enforcement triggers (should be 2)",
                    [({}, float(row["n"] or 0))],
                ))
            except Exception:
                logger.exception("metrics: chain trigger watchdog failed")

            # Phase 14: privileged-access event counts (7d + 24h-per-site)
            # Feeds anomaly detection: >10 fleet-wide/7d OR ≥2 per site/24h
            # are alert conditions. Source of truth = compliance_bundles
            # (same as every other evidence class).
            try:
                row = await conn.fetchrow("""
                    SELECT
                        COUNT(*) FILTER (WHERE checked_at > NOW() - INTERVAL '7 days')
                            AS events_7d,
                        COUNT(*) FILTER (WHERE checked_at > NOW() - INTERVAL '24 hours')
                            AS events_24h,
                        COUNT(DISTINCT site_id) FILTER (
                            WHERE checked_at > NOW() - INTERVAL '24 hours'
                        ) AS sites_24h
                    FROM compliance_bundles
                    WHERE check_type = 'privileged_access'
                """)
                sections.append(_gauge(
                    "osiriscare_privileged_access_events_7d",
                    "Fleet-wide privileged-access attestation events in last 7 days",
                    [({}, float(row["events_7d"] or 0))],
                ))
                sections.append(_gauge(
                    "osiriscare_privileged_access_events_24h",
                    "Fleet-wide privileged-access attestation events in last 24 hours",
                    [({}, float(row["events_24h"] or 0))],
                ))
                sections.append(_gauge(
                    "osiriscare_privileged_access_sites_24h",
                    "Distinct sites that had a privileged-access event in last 24h",
                    [({}, float(row["sites_24h"] or 0))],
                ))
            except Exception:
                logger.exception("metrics: privileged-access counts failed")

            # Phase 13: server-pubkey fingerprint divergence across the fleet.
            # We compute the fingerprint server-side (first 16 hex of the
            # current signing pubkey) and count appliances whose most
            # recently delivered fingerprint doesn't match. > 0 means
            # some appliances will reject signed fleet orders until they
            # re-sync; investigate via /api/admin/diagnostics/pubkey-divergence.
            try:
                import os as _os
                import pathlib as _p
                try:
                    from nacl.signing import SigningKey
                    from nacl.encoding import HexEncoder
                    _key_hex = _p.Path(
                        _os.getenv("SIGNING_KEY_FILE", "/app/secrets/signing.key")
                    ).read_bytes().strip()
                    _sk = SigningKey(_key_hex, encoder=HexEncoder)
                    _current_fp = _sk.verify_key.encode(encoder=HexEncoder).decode()[:16]
                except Exception:
                    _current_fp = None
                row = await conn.fetchrow("""
                    SELECT
                      COUNT(*) FILTER (WHERE server_pubkey_fingerprint_seen IS DISTINCT FROM $1) AS divergent,
                      COUNT(*) AS total
                    FROM site_appliances
                    WHERE deleted_at IS NULL
                """, _current_fp)
                sections.append(_gauge(
                    "osiriscare_appliance_server_pubkey_divergence",
                    "Appliances whose last-seen server pubkey fingerprint diverges from current signing key",
                    [({}, float(row["divergent"] or 0))],
                ))
                if _current_fp:
                    sections.append(_gauge(
                        "osiriscare_server_signing_key_current",
                        "Current server signing-key fingerprint (first 16 hex)",
                        [({"fingerprint": _current_fp}, 1.0)],
                    ))
            except Exception:
                logger.exception("metrics: server-pubkey divergence query failed")

            # v40 FIX-15 (Session 209): install-gate failing gauge.
            # Fires 1.0 for every MAC that is actively retrying installed-
            # system provisioning AND has never successfully passed the
            # 4-stage network gate (first_outbound_success_at IS NULL).
            # Mirrors the provisioning_network_fail substrate invariant
            # for scraper-side alerting; a Prom rule can page on
            # `sum(osiriscare_install_gate_failing) > 0 for 10m`.
            try:
                col_exists = await conn.fetchval(
                    """
                    SELECT 1
                      FROM information_schema.columns
                     WHERE table_name = 'install_sessions'
                       AND column_name = 'first_outbound_success_at'
                     LIMIT 1
                    """
                )
                if col_exists:
                    gate_rows = await conn.fetch(
                        """
                        SELECT iss.site_id,
                               iss.mac_address,
                               COALESCE(iss.hostname, iss.mac_address) AS label
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
                    sections.append(_gauge(
                        "osiriscare_install_gate_failing",
                        "1 per MAC with installed-system network gate failing. Labels: site_id, mac_address, label (hostname or MAC). v40 FIX-15.",
                        [
                            (
                                {
                                    "site_id": (r["site_id"] or "")[:80],
                                    "mac_address": (r["mac_address"] or "")[:20],
                                    "label": (r["label"] or "")[:80],
                                },
                                1.0,
                            )
                            for r in gate_rows
                        ] or [({}, 0.0)],
                    ))
            except Exception:
                logger.exception("metrics: install_gate_failing query failed")

            # Phase 6: regime change events in the last 7 days (unacknowledged)
            try:
                row = await conn.fetchrow("""
                    SELECT
                        COUNT(*) FILTER (WHERE severity = 'warning')  AS warnings,
                        COUNT(*) FILTER (WHERE severity = 'critical') AS criticals
                    FROM l1_rule_regime_events
                    WHERE detected_at > NOW() - INTERVAL '7 days'
                      AND acknowledged_at IS NULL
                """)
                sections.append(_gauge(
                    "osiriscare_flywheel_regime_changes_7d",
                    "L1 rule regime-change events (7d, unacknowledged) by severity",
                    [
                        ({"severity": "warning"},  float(row["warnings"] or 0)),
                        ({"severity": "critical"}, float(row["criticals"] or 0)),
                    ],
                ))
            except Exception:
                logger.exception("metrics: regime change query failed")

    except Exception:
        logger.exception("metrics: database connection failed")
        return PlainTextResponse(
            "# Database query failed\n",
            media_type=PROM_CONTENT_TYPE,
            status_code=503,
        )

    # ── Phase 15: bg_heartbeat + startup_invariants metrics ────────
    # These come from process-local state (not DB) so we produce them
    # outside the admin_connection block and they never 503 the
    # scrape even if DB is down. That is intentional — if DB is down,
    # these metrics are exactly what you want visible.
    try:
        from .bg_heartbeat import get_all_heartbeats, EXPECTED_INTERVAL_S
        import time as _time
        now = _time.time()
        hb_snapshot = get_all_heartbeats()

        # Age-since-last-heartbeat per loop (seconds). 0 when fresh;
        # larger values signal a stuck loop.
        hb_age_values = []
        for name, entry in hb_snapshot.items():
            hb_age_values.append(
                ({"loop_name": name}, float(entry["age_s"])),
            )
        if hb_age_values:
            sections.append(_gauge(
                "osiriscare_bg_loop_last_heartbeat_seconds",
                "Seconds since the named background loop last recorded a heartbeat. Higher = likely stuck.",
                hb_age_values,
            ))

        # Absolute last_seen timestamp (unix) — alert on (time() - this) > 3*interval
        last_seen_values = []
        for name, entry in hb_snapshot.items():
            last_seen_values.append(
                ({"loop_name": name}, float(entry["last_seen"])),
            )
        if last_seen_values:
            sections.append(_gauge(
                "osiriscare_bg_loop_last_heartbeat_timestamp",
                "Unix timestamp of the most recent heartbeat per loop",
                last_seen_values,
            ))

        # Iterations + errors since process start — enables rate() in alert rules
        iter_values = [
            ({"loop_name": n}, float(e["iterations"]))
            for n, e in hb_snapshot.items()
        ]
        err_values = [
            ({"loop_name": n}, float(e["errors"]))
            for n, e in hb_snapshot.items()
        ]
        if iter_values:
            sections.append(_counter(
                "osiriscare_bg_loop_iterations_total",
                "Total iterations a background loop has completed since process start",
                iter_values,
            ))
            sections.append(_counter(
                "osiriscare_bg_loop_errors_total",
                "Total iterations a background loop caught + logged an error",
                err_values,
            ))

        # Expected-interval declarative table so PromQL rules can
        # derive the alert threshold per loop.
        exp_values = [
            ({"loop_name": n}, float(v))
            for n, v in EXPECTED_INTERVAL_S.items()
        ]
        sections.append(_gauge(
            "osiriscare_bg_loop_expected_interval_seconds",
            "Declared expected interval between iterations per loop (3x this = staleness alert)",
            exp_values,
        ))
    except Exception:
        logger.exception("metrics: bg_heartbeat export failed")

    body = "\n\n".join(sections) + "\n"
    return PlainTextResponse(body, media_type=PROM_CONTENT_TYPE)
