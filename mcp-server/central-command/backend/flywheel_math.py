"""Pure-function flywheel math (Phase 15 A-spec).

Helpers used by the data-flywheel background loops. Keeping them in a
no-side-effects module (no DB, no env, no imports from sibling modules
with relative imports) makes them trivially unit-testable — which is
the point, per round-table QA audit feedback.

If a formula lives here, it has a matching test in
tests/test_regime_detector.py / tests/test_promotion_threshold_tuner.py
etc. Changes to constants in this file are POLICY changes, not
refactors — bumping REGIME_DROP_THRESHOLD needs a review
conversation, and the test file enforces that by failing loud.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional


# ─── Phase 6: regime-change detector ───────────────────────────────

# A 7d-vs-30d rate delta of -0.15 is the minimum to fire a regime
# event. -0.30 is critical. Anything less negative than -0.15 is
# within expected noise + seasonal variance.
REGIME_DROP_THRESHOLD = -0.15
REGIME_CRITICAL_THRESHOLD = -0.30


def classify_regime_delta(rate_7: float, rate_30: float) -> str | None:
    """Return severity label for a regime-change event, or None if
    the drop is below the noise threshold.

    Args:
      rate_7:  7-day rolling success rate (0.0..1.0)
      rate_30: 30-day baseline success rate (0.0..1.0)

    Returns:
      None          — drop below threshold (no event)
      'warning'     — -0.30 < delta < -0.15
      'critical'    — delta <= -0.30
    """
    delta = rate_7 - rate_30
    if delta >= REGIME_DROP_THRESHOLD:
        return None
    if delta <= REGIME_CRITICAL_THRESHOLD:
        return "critical"
    return "warning"


# ─── Phase 8: promotion-threshold Bayesian drift cap ───────────────

# Per-day upper bound on how much the per-incident-type promotion
# threshold can move. Prevents runaway drift when a few outlier
# events skew a single-day observation. Combined with the overall
# min/max floor/ceiling (configured per-incident) this keeps
# thresholds stable + predictable for reviewers.
THRESHOLD_DRIFT_CAP_PER_DAY = 0.02
THRESHOLD_FLOOR_DEFAULT = 0.50
THRESHOLD_CEILING_DEFAULT = 0.95


def clamp_threshold_drift(
    current: float,
    proposed: float,
    days_since_last_update: float,
    floor: float = THRESHOLD_FLOOR_DEFAULT,
    ceiling: float = THRESHOLD_CEILING_DEFAULT,
) -> float:
    """Clamp a Bayesian-proposed threshold to the drift cap + bounds.

    The tuner computes a new threshold from telemetry. If that new
    value is more than THRESHOLD_DRIFT_CAP_PER_DAY × days_elapsed
    different from the current value, we limit the move — so a single
    data-corruption day can't blow a threshold out.

    Also clamps to [floor, ceiling] regardless of how the drift
    came out.
    """
    if days_since_last_update <= 0:
        # Never-updated or same-day re-run — no drift allowed.
        days_since_last_update = 0.0
    max_move = THRESHOLD_DRIFT_CAP_PER_DAY * days_since_last_update
    if proposed > current + max_move:
        result = current + max_move
    elif proposed < current - max_move:
        result = current - max_move
    else:
        result = proposed
    # Final clamp to [floor, ceiling]
    return max(floor, min(ceiling, result))


# ─── Phase 6: temporal decay ───────────────────────────────────────


def decay_factor(days_since_last_seen: float, half_life_days: float) -> float:
    """Exponential-decay multiplier for evidence weighting.

    factor = 0.5 ^ (age / half_life)

    At age == half_life → 0.5
    At age == 2 × half_life → 0.25
    At age == 0 → 1.0
    Pure function — can run offline, in a test, or in SQL (the SQL
    version uses the same formula)."""
    if half_life_days <= 0:
        raise ValueError("half_life_days must be > 0")
    if days_since_last_seen < 0:
        # Age can't be negative; treat as zero (same as fresh).
        return 1.0
    return 0.5 ** (days_since_last_seen / half_life_days)


def decayed_count(
    current_count: float,
    days_since_last_seen: float,
    half_life_days: float,
    min_count_floor: float = 1.0,
) -> float:
    """Apply exponential decay to a count, with a floor so patterns
    that have genuinely useful cardinality don't vanish entirely."""
    factor = decay_factor(days_since_last_seen, half_life_days)
    return max(current_count * factor, min_count_floor)


# ─── Phase 9: shadow-mode agreement ratio ──────────────────────────


def shadow_agreement_ratio(
    agreements: int, disagreements: int, insufficient: int = 0,
) -> float | None:
    """Fraction of shadow-mode runs where the candidate rule agreed
    with the current policy.

    Returns None when the denominator is too small to be meaningful
    (< 10 comparisons). Keeps the 'insufficient_data' branch explicit
    so the caller doesn't divide by a small N and mistake noise for
    signal.
    """
    total = agreements + disagreements
    if total < 10:
        return None
    return agreements / total


# ─── Flywheel narrative builder ────────────────────────────────────


def build_rule_narrative(
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
    Designed for auditor + partner display. Deterministic; no LLM.
    Lives here (not in fleet_intelligence.py) so it's testable in
    isolation without dragging in the whole FastAPI/SQLAlchemy
    import graph."""
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
