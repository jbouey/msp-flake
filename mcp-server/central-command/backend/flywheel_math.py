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


# ─── Phase 15 closing: absolute-floor regime detection ─────────────
#
# The delta-based regime detector misses rules that were BAD FROM
# DAY 1 (the 48h canary's job) because rate_7 ≈ rate_30 means delta
# ≈ 0, so classify_regime_delta returns None. This left rules like
# L1-AUTO-SCREEN-LOCK-POLICY at 0%/31 still enabled because:
#   - canary only watches the first 48h, this rule is older
#   - regime detector skips zero-delta cases
#
# The fix: an ABSOLUTE-FLOOR check that flags regardless of delta.
# Conservative thresholds so we don't fire on rules with low
# sample sizes — N>=20 is a real-data minimum.
ABSOLUTE_LOW_RATE_CEILING = 0.30
ABSOLUTE_LOW_MIN_SAMPLES = 20
ABSOLUTE_LOW_RULE_AGE_HOURS = 24  # let canary handle the first day


def classify_absolute_floor(
    rate_7: float, n_7: int, rule_age_hours: float,
) -> str | None:
    """Return 'absolute_low' if a rule's 7-day success rate is below
    the absolute floor (regardless of delta vs baseline), else None.

    Tuned to NOT overlap with the 48h canary — only fires if
    rule_age_hours > 24, so canary owns days 0-1, this owns days 1+.

    Args:
      rate_7:           current 7-day rolling success rate
      n_7:              number of executions in the last 7 days
      rule_age_hours:   how long the rule has been active

    Returns:
      'absolute_low' — rule is bad and has been long enough to know
      None           — within tolerance, or not enough data, or canary territory
    """
    if rule_age_hours <= ABSOLUTE_LOW_RULE_AGE_HOURS:
        return None
    if n_7 < ABSOLUTE_LOW_MIN_SAMPLES:
        return None
    if rate_7 >= ABSOLUTE_LOW_RATE_CEILING:
        return None
    return "absolute_low"


# ─── Phase 15 closing: rule-yaml action normalization ──────────────
#
# Round-table audit discovered that every promoted_rules row in prod
# had `action: execute_runbook` in its YAML — but the Go daemon's
# allowedRuleActions whitelist (processor.go) only accepts
# `run_windows_runbook`, `run_linux_runbook`, `escalate`, etc. Every
# fleet_order shipping a promoted rule was rejected at the appliance
# with "action X not in allowed actions", and deployment_count stayed
# at 0 fleet-wide.
#
# This helper translates based on runbook_id prefix so the promotion
# writers + reconcile script emit daemon-compatible YAML. Pure function
# so it's trivially testable offline.

def normalize_rule_action(runbook_id: str) -> str:
    """Translate a runbook_id prefix into the daemon's whitelisted action.

    Raises ValueError on unknown prefixes — we'd rather fail loudly at
    promotion time than ship an order the daemon will reject.

    Known prefixes (from inspection of prod promoted_rules + runbook
    registry):
      LIN-*, L1-LIN-*, L1-NET-*, L1-SUID-*  → run_linux_runbook
      RB-WIN-*, L1-WIN-*, WIN-*              → run_windows_runbook
      MAC-*, L1-MAC-*                        → run_macos_runbook (not in
                                               daemon whitelist yet — raise)
      RB-DRIFT-*, general, ''                → ValueError (must be
                                               classified by promoter)
    """
    if not runbook_id:
        raise ValueError("runbook_id required for action classification")
    rb = runbook_id.strip().upper()
    # Strip L1- prefix so both `L1-WIN-*` and `WIN-*` hit the same branch
    if rb.startswith("L1-"):
        rb = rb[3:]
    if rb.startswith(("LIN-", "LINUX-", "NET-", "SUID-")):
        return "run_linux_runbook"
    if rb.startswith(("WIN-", "RB-WIN-", "WINDOWS-")):
        return "run_windows_runbook"
    raise ValueError(
        f"runbook_id {runbook_id!r} has no known platform prefix; "
        f"cannot classify as linux/windows. Add to flywheel_math.normalize_rule_action."
    )


def normalize_rule_yaml_action(rule_yaml: str, runbook_id: str) -> str:
    """Rewrite `action: execute_runbook` → daemon-whitelisted action
    based on runbook_id prefix. No-op if the YAML already has a
    whitelisted action. Used by promotion writers + the backfill
    reconcile script."""
    target = normalize_rule_action(runbook_id)
    lines = rule_yaml.splitlines()
    out = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("action:"):
            # Extract the current action and only rewrite if it's
            # the legacy execute_runbook value.
            current = stripped[len("action:"):].strip()
            if current == "execute_runbook":
                out.append(line.replace("execute_runbook", target))
                replaced = True
                continue
        out.append(line)
    # Preserve trailing newline if the input had one
    result = "\n".join(out)
    if rule_yaml.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def build_daemon_valid_rule_yaml(
    rule_id: str,
    runbook_id: str,
    incident_type: str,
    name: str | None = None,
    description: str | None = None,
) -> str:
    """Build a complete daemon-valid rule YAML from the L1 rule's
    metadata.

    The Go daemon's order processor (appliance/internal/orders/processor.go)
    requires:
      - `action` in allowedRuleActions whitelist
      - `conditions` array with len >= 1
      - `id` matching ^[A-Za-z0-9_-]{3,64}$

    The historical promoted_rules table stored stub YAML with
    `action: execute_runbook` and NO conditions block, so every
    sync_promoted_rule order was rejected. This builder synthesizes
    a proper rule body that the daemon accepts.

    The condition matches on incident_type — same predicate the L2
    planner uses to identify which rule should fire on a given
    incident. Mirrors the format of the standard rules in
    agent_api.py:agent_sync_rules.
    """
    action = normalize_rule_action(runbook_id)
    if not incident_type:
        raise ValueError("incident_type is required to build a valid rule")
    name = name or rule_id.lower().replace("l1-auto-", "").replace("-", "_")
    description = description or f"Auto-promoted L1 rule for {incident_type}"

    # Hand-format YAML to keep the output stable and grep-friendly
    # (no PyYAML dependency, deterministic key order).
    yaml = (
        f"id: {rule_id}\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"conditions:\n"
        f"  - field: incident_type\n"
        f"    operator: eq\n"
        f"    value: {incident_type}\n"
        f"action: {action}\n"
        f"action_params:\n"
        f"  runbook_id: {runbook_id}\n"
        f"enabled: true\n"
    )
    return yaml


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
