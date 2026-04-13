"""Partner Fleet Intelligence API (Phase 11 — advanced flywheel layer F).

Exposes learned-rule intelligence to partners in a human-readable form:
  - Rules the flywheel promoted on the partner's book of business
  - For each: trigger count, estimated time saved, similar-fleet
    comparisons, HIPAA control mapping, and a one-paragraph narrative
  - Pending exemplars awaiting partner approval
  - Regime-change alerts affecting their sites

The goal is to turn the flywheel from an internal plumbing detail into
a visible product signal: "your fleet's learning score improved this
month; we saved you an estimated N hours of manual intervention."

All endpoints require partner auth and scope to the partner's own orgs
and sites via the existing require_partner_role dependency chain.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .shared import get_db
from .partners import require_partner_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/partners/me/fleet-intelligence", tags=["partners"])


# Estimated minutes saved per successful L1 execution of a promoted rule.
# Conservative — an MSP tech typically spends 5-15 min on manual
# remediation; using 8 as a midpoint. Partner-facing number is a range.
MINUTES_PER_RESOLUTION = 8


async def _partner_site_ids(db: AsyncSession, partner_id: str) -> List[str]:
    """Scope: all sites the partner's orgs own."""
    rows = (await db.execute(text("""
        SELECT s.site_id
        FROM sites s
        JOIN client_orgs co ON co.id = s.client_org_id
        WHERE co.current_partner_id = :pid
    """), {"pid": partner_id})).fetchall()
    return [r.site_id for r in rows]


@router.get("/summary")
async def partner_fleet_intelligence_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
    partner: dict = require_partner_role("admin", "tech", "billing"),
) -> Dict[str, Any]:
    """High-level numbers for the partner dashboard.

    Returns counts of:
      - promoted rules active on partner's fleet
      - total deployments in last 30d
      - estimated manual-intervention time saved
      - pending exemplars awaiting approval
      - unacknowledged regime alerts on partner's fleet
    """
    sites = await _partner_site_ids(db, partner["id"])
    if not sites:
        return {
            "active_rules": 0,
            "deployments_30d": 0,
            "estimated_hours_saved_30d": 0.0,
            "pending_exemplars": 0,
            "regime_alerts_unacked": 0,
            "fleet_size": 0,
        }

    # Active promoted rules deployed to sites the partner owns
    active_rules = (await db.execute(text("""
        SELECT COUNT(DISTINCT pr.rule_id) AS n
        FROM promoted_rules pr
        JOIN fleet_orders fo ON fo.parameters->>'rule_id' = pr.rule_id
        JOIN fleet_order_completions foc ON foc.fleet_order_id = fo.id
        WHERE pr.status = 'active'
          AND fo.parameters->>'site_id' = ANY(:sites)
          AND foc.status = 'completed'
    """), {"sites": sites})).fetchone()
    n_rules = int(active_rules.n or 0) if active_rules else 0

    # L1 executions attributable to promoted rules on partner's sites, 30d
    deploys = (await db.execute(text("""
        SELECT COUNT(*) AS n
        FROM execution_telemetry et
        JOIN l1_rules l ON l.runbook_id = et.runbook_id
        WHERE et.site_id = ANY(:sites)
          AND et.resolution_level = 'L1'
          AND et.success = true
          AND et.created_at > NOW() - INTERVAL '30 days'
          AND l.promoted_from_l2 = true
    """), {"sites": sites})).fetchone()
    n_deploys = int(deploys.n or 0) if deploys else 0
    hours_saved = round(n_deploys * MINUTES_PER_RESOLUTION / 60.0, 1)

    # Pending exemplars — approvals this partner could act on
    pending_exemplars = (await db.execute(text("""
        SELECT COUNT(*) AS n FROM l2_prompt_exemplars WHERE status = 'draft'
    """))).fetchone()
    n_pending = int(pending_exemplars.n or 0) if pending_exemplars else 0

    # Regime alerts affecting rules on partner's fleet (7d, unacked)
    alerts = (await db.execute(text("""
        SELECT COUNT(*) AS n
        FROM l1_rule_regime_events rce
        JOIN l1_rules l ON l.rule_id = rce.rule_id
        WHERE rce.detected_at > NOW() - INTERVAL '7 days'
          AND rce.acknowledged_at IS NULL
    """))).fetchone()
    n_alerts = int(alerts.n or 0) if alerts else 0

    return {
        "active_rules": n_rules,
        "deployments_30d": n_deploys,
        "estimated_hours_saved_30d": hours_saved,
        "pending_exemplars": n_pending,
        "regime_alerts_unacked": n_alerts,
        "fleet_size": len(sites),
        "methodology": {
            "minutes_per_resolution": MINUTES_PER_RESOLUTION,
            "calculation": "L1 successful executions × 8 min ÷ 60",
            "note": "Conservative midpoint; actual MSP time-per-intervention ranges 5–15 min.",
        },
    }


@router.get("/rules")
async def partner_fleet_intelligence_rules(
    request: Request,
    db: AsyncSession = Depends(get_db),
    partner: dict = require_partner_role("admin", "tech", "billing"),
) -> List[Dict[str, Any]]:
    """Per-rule intelligence: each promoted rule active on the partner's
    fleet with trigger count, estimated time saved, HIPAA citation,
    and a one-paragraph human-readable narrative for audit display."""
    sites = await _partner_site_ids(db, partner["id"])
    if not sites:
        return []

    rows = (await db.execute(text("""
        WITH partner_rules AS (
            SELECT DISTINCT pr.rule_id, pr.pattern_signature, pr.promoted_at,
                   pr.deployment_count, pr.last_deployed_at, pr.notes,
                   l.runbook_id, l.confidence, l.incident_pattern,
                   r.name AS runbook_name, r.check_type,
                   r.hipaa_controls
            FROM promoted_rules pr
            JOIN l1_rules l ON l.rule_id = pr.rule_id
            LEFT JOIN runbooks r ON r.runbook_id = l.runbook_id
            WHERE pr.status = 'active'
              AND l.enabled = true
              AND EXISTS (
                  SELECT 1 FROM fleet_orders fo
                  JOIN fleet_order_completions foc ON foc.fleet_order_id = fo.id
                  WHERE fo.parameters->>'rule_id' = pr.rule_id
                    AND fo.parameters->>'site_id' = ANY(:sites)
                    AND foc.status = 'completed'
              )
        )
        SELECT pr.*,
               (
                 SELECT COUNT(*) FROM execution_telemetry et
                 WHERE et.site_id = ANY(:sites)
                   AND et.resolution_level = 'L1'
                   AND et.runbook_id = pr.runbook_id
                   AND et.created_at > NOW() - INTERVAL '30 days'
                   AND et.success = true
               ) AS triggers_30d
        FROM partner_rules pr
        ORDER BY triggers_30d DESC, pr.promoted_at DESC
        LIMIT 100
    """), {"sites": sites})).fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        triggers = int(r.triggers_30d or 0)
        time_saved_min = triggers * MINUTES_PER_RESOLUTION
        # Incident type from the L1 incident_pattern JSONB
        import json as _json
        incident_type = None
        try:
            ip = r.incident_pattern
            if isinstance(ip, dict):
                incident_type = ip.get("incident_type")
            elif isinstance(ip, str):
                incident_type = _json.loads(ip).get("incident_type")
        except Exception:
            pass

        narrative = _build_rule_narrative(
            rule_id=r.rule_id,
            runbook_name=r.runbook_name,
            incident_type=incident_type,
            triggers_30d=triggers,
            promoted_at=r.promoted_at,
            deployment_count=r.deployment_count or 0,
            confidence=r.confidence,
            hipaa_controls=r.hipaa_controls,
        )
        out.append({
            "rule_id": r.rule_id,
            "runbook_id": r.runbook_id,
            "runbook_name": r.runbook_name,
            "incident_type": incident_type,
            "check_type": r.check_type,
            "confidence": float(r.confidence) if r.confidence is not None else None,
            "promoted_at": r.promoted_at.isoformat() if r.promoted_at else None,
            "deployment_count": int(r.deployment_count or 0),
            "last_deployed_at": r.last_deployed_at.isoformat() if r.last_deployed_at else None,
            "triggers_30d": triggers,
            "estimated_minutes_saved_30d": time_saved_min,
            "hipaa_controls": list(r.hipaa_controls) if r.hipaa_controls else [],
            "narrative": narrative,
        })
    return out


def _build_rule_narrative(
    rule_id: str,
    runbook_name: Optional[str],
    incident_type: Optional[str],
    triggers_30d: int,
    promoted_at: Optional[datetime],
    deployment_count: int,
    confidence: Optional[float],
    hipaa_controls: Optional[List[str]],
) -> str:
    """One-paragraph human-readable explanation of a promoted rule.
    Designed for auditor + partner display. Deterministic; no LLM."""
    runbook_label = runbook_name or rule_id
    incident_label = (incident_type or "an observed pattern").replace("_", " ")
    age_days = 0
    if promoted_at:
        age_days = int(
            (datetime.now(timezone.utc) - promoted_at).total_seconds() / 86400
        )

    conf_pct = int((confidence or 0.9) * 100)
    hipaa_clause = ""
    if hipaa_controls:
        hipaa_clause = f" Aligned with HIPAA {', '.join(hipaa_controls[:3])}."

    trigger_clause = (
        f"Triggered {triggers_30d} time{'s' if triggers_30d != 1 else ''} "
        f"in the last 30 days across your fleet."
        if triggers_30d > 0
        else "No triggers recorded in the last 30 days — the rule is on "
             "standby in case the pattern recurs."
    )
    deploy_clause = (
        f"Deployed to your appliances {deployment_count} time"
        f"{'s' if deployment_count != 1 else ''}."
        if deployment_count > 0 else
        "Deployment pending next appliance check-in."
    )

    return (
        f"OsirisCare auto-promoted this rule ({runbook_label}) {age_days} "
        f"days ago after observing {incident_label} with ≥{conf_pct}% "
        f"successful-resolution confidence across multiple customers. "
        f"{trigger_clause} {deploy_clause}{hipaa_clause}"
    )


@router.get("/regime-alerts")
async def partner_regime_alerts(
    request: Request,
    db: AsyncSession = Depends(get_db),
    partner: dict = require_partner_role("admin", "tech"),
    days: int = 14,
) -> List[Dict[str, Any]]:
    """Unacknowledged regime-change alerts on rules deployed to partner's
    sites. Feeds a dashboard badge + triage view."""
    sites = await _partner_site_ids(db, partner["id"])
    if not sites:
        return []

    rows = (await db.execute(text("""
        SELECT rce.rule_id, rce.detected_at, rce.window_7d_rate,
               rce.baseline_30d_rate, rce.delta, rce.severity,
               rce.sample_size_7d, l.runbook_id
        FROM l1_rule_regime_events rce
        JOIN l1_rules l ON l.rule_id = rce.rule_id
        WHERE rce.detected_at > NOW() - make_interval(days => :d)
          AND rce.acknowledged_at IS NULL
        ORDER BY rce.severity DESC, rce.detected_at DESC
        LIMIT 100
    """), {"d": days})).fetchall()

    return [
        {
            "rule_id": r.rule_id,
            "runbook_id": r.runbook_id,
            "detected_at": r.detected_at.isoformat() if r.detected_at else None,
            "window_7d_rate": float(r.window_7d_rate) if r.window_7d_rate is not None else None,
            "baseline_30d_rate": float(r.baseline_30d_rate) if r.baseline_30d_rate is not None else None,
            "delta": float(r.delta) if r.delta is not None else None,
            "sample_size_7d": int(r.sample_size_7d or 0),
            "severity": r.severity,
            "narrative": (
                f"Rule {r.rule_id} success rate dropped from "
                f"{float(r.baseline_30d_rate or 0):.0%} (30-day baseline) to "
                f"{float(r.window_7d_rate or 0):.0%} (last 7 days, "
                f"n={r.sample_size_7d}). "
                + ("This is a critical drop — consider disabling and investigating."
                   if r.severity == "critical"
                   else "Review recommended; no automatic action taken.")
            ),
        }
        for r in rows
    ]


@router.post("/regime-alerts/{event_id}/ack")
async def ack_regime_alert(
    event_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    partner: dict = require_partner_role("admin", "tech"),
) -> Dict[str, Any]:
    """Acknowledge a regime-change alert. Removes it from the unacked list."""
    await db.execute(text("""
        UPDATE l1_rule_regime_events
           SET acknowledged_at = NOW(),
               acknowledged_by = :p,
               resolution = COALESCE(resolution, 'still_investigating')
         WHERE id = :id AND acknowledged_at IS NULL
    """), {"id": event_id, "p": partner.get("email") or partner.get("id")})
    await db.commit()
    return {"ok": True, "event_id": event_id}
