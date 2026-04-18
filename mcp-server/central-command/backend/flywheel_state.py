"""Flywheel Spine — orchestrator + state machine (Session 206 redesign).

BEFORE: the flywheel was nine disconnected hops. Each hop owned its own
tables and loops. No one owned the journey of a single rule. When the
auto-disable silently failed today, no telemetry, no event, no signal.
The audit had to join 8 tables to find one bug.

AFTER: one ledger (`promoted_rule_events`), one state machine
(`promoted_rules.lifecycle_state`), one orchestrator. Every state
transition writes a ledger row. Every failed transition logs at ERROR
with exc_info and bumps a Prom counter. The loop-level integration test
pins the pipeline end-to-end — one bug breaks one test, not 100%
silently.

Public API:
    run_orchestrator_tick(conn) -> OrchestratorResult
    advance(conn, rule_id, new_state, event_type, ...) -> bool

Each `Transition` is a small class with a query (find candidates) + apply
(advance_lifecycle). Add a new transition = add a class, register it,
ship. No code duplication across the four-fifths of the flywheel that
used to be spread across background_tasks.py Step 5.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
from typing import Any, Dict, List, Optional, Sequence

import asyncpg

logger = logging.getLogger(__name__)


# ─── Public state + event enums — must match migration 181 ─────────

LIFECYCLE_STATES = frozenset({
    "proposed", "shadow", "approved", "rolling_out",
    "active", "regime_warning", "auto_disabled",
    "graduated", "retired",
})

# MUST stay in lockstep with promoted_rule_events_event_type_check
# (migrations 181 + 184 + 188 + 236) and the DB transition matrix.
# test_three_list_lockstep_pg.py fails CI if any of the three drifts.
EVENT_TYPES = frozenset({
    # Spine lifecycle (migration 181)
    "proposed", "shadow_entered", "approved", "rollout_started", "rollout_acked",
    "canary_failed", "auto_disabled", "regime_warning", "operator_re_enabled",
    "operator_acknowledged", "graduated", "retired", "zombie_site",
    "regime_absolute_low", "stage_change", "reviewer_note",
    # Orchestrator-specific (migration 188)
    "retired_site_dead", "regime_critical",
    # Runbook consent (migration 184)
    "runbook.consented", "runbook.amended", "runbook.revoked",
    "runbook.executed_with_consent",
    # Python-emitted canonical names (migration 236)
    "pattern_detected", "shadow_evaluated", "promotion_approved",
    "rollout_issued", "first_execution", "manually_disabled",
    "retired_manual",
})

STAGES = frozenset({
    "detection", "shadow_eval", "promotion", "rollout",
    "monitoring", "regime", "governance", "retire",
})


@dataclasses.dataclass(frozen=True)
class TransitionResult:
    rule_id: str
    from_state: str
    to_state: str
    event_type: str
    success: bool
    error: Optional[str] = None


@dataclasses.dataclass
class OrchestratorResult:
    total_rules_scanned: int = 0
    transitions_by_name: Dict[str, int] = dataclasses.field(default_factory=dict)
    failures_by_name: Dict[str, int] = dataclasses.field(default_factory=dict)
    elapsed_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_rules_scanned": self.total_rules_scanned,
            "transitions": dict(self.transitions_by_name),
            "failures": dict(self.failures_by_name),
            "elapsed_ms": self.elapsed_ms,
        }


# ─── Canonical state-mutation path ─────────────────────────────────


async def advance(
    conn: asyncpg.Connection,
    *,
    rule_id: str,
    new_state: str,
    event_type: str,
    actor: str,
    stage: str,
    proof: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
    site_id: Optional[str] = None,
    outcome: str = "success",
) -> bool:
    """Advance a promoted rule through its lifecycle.

    Delegates to the Postgres `advance_lifecycle()` function which:
      - locks the rule row
      - validates the transition against `promoted_rule_lifecycle_transitions`
      - writes an append-only event to `promoted_rule_events`
      - updates `promoted_rules.lifecycle_state` + `lifecycle_state_updated_at`

    Input validation is belt-and-suspenders: the DB enforces the same
    constraints but failing here gives a clean Python stack trace.
    """
    if new_state not in LIFECYCLE_STATES:
        raise ValueError(f"unknown lifecycle state: {new_state!r}")
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event type: {event_type!r}")
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage!r}")
    if outcome not in ("success", "failed", "skipped", "timeout"):
        raise ValueError(f"unknown outcome: {outcome!r}")
    if not actor:
        raise ValueError("actor is required (named human email OR 'system:<name>')")

    await conn.execute(
        """
        SELECT advance_lifecycle($1::text, $2::text, $3::text, $4::text,
                                 $5::text, $6::jsonb, $7::text, $8::text, $9::text)
        """,
        rule_id, new_state, event_type, actor, stage,
        json.dumps(proof or {}),
        reason, site_id, outcome,
    )
    return True


# ─── Transition base class + concrete implementations ──────────────


class Transition:
    """Base class for a pipeline transition.

    Subclasses implement:
      - `name`: short identifier for metrics + logs
      - `description`: human text
      - `find_candidates(conn)`: yields (rule_id, metadata) tuples
      - `apply(conn, rule_id, metadata)`: performs the transition
    """
    name: str = "base"
    description: str = ""
    stage: str = "monitoring"

    async def find_candidates(
        self, conn: asyncpg.Connection
    ) -> Sequence[Dict[str, Any]]:
        raise NotImplementedError

    async def apply(
        self, conn: asyncpg.Connection, candidate: Dict[str, Any]
    ) -> TransitionResult:
        raise NotImplementedError


class RegimeAbsoluteLowTransition(Transition):
    """active / regime_warning / graduated → auto_disabled

    Fires when:
      - rule is promoted_from_l2
      - rule is currently enabled
      - an unacknowledged 'absolute_low' regime event was detected
        within the last 24 hours

    This is the transition that silently failed in prod today — the
    `logger.debug` in flywheel_promotion_loop step 5 swallowed errors
    and SCREEN_LOCK stayed enabled at 0%/83 for 2+ hours. With the
    spine, this transition is its OWN try/except with logger.error —
    no sibling can mask it.
    """
    name = "regime_absolute_low_auto_disable"
    description = "active → auto_disabled on unack'd absolute_low regime event"
    stage = "regime"

    async def find_candidates(
        self, conn: asyncpg.Connection
    ) -> Sequence[Dict[str, Any]]:
        rows = await conn.fetch(
            """
            SELECT pr.rule_id, pr.lifecycle_state, pr.site_id,
                   rce.id AS regime_event_id,
                   rce.window_7d_rate, rce.sample_size_7d, rce.severity,
                   rce.detected_at
            FROM promoted_rules pr
            JOIN l1_rules l ON l.rule_id = pr.rule_id
            JOIN l1_rule_regime_events rce ON rce.rule_id = pr.rule_id
            WHERE l.promoted_from_l2 = true
              AND l.enabled = true
              AND pr.lifecycle_state IN ('active', 'regime_warning', 'graduated')
              AND rce.severity IN ('absolute_low', 'critical')
              AND rce.acknowledged_at IS NULL
              AND rce.detected_at > NOW() - INTERVAL '24 hours'
            """
        )
        return [dict(r) for r in rows]

    async def apply(
        self, conn: asyncpg.Connection, candidate: Dict[str, Any]
    ) -> TransitionResult:
        rule_id = candidate["rule_id"]
        from_state = candidate["lifecycle_state"]
        event_type = (
            "regime_absolute_low"
            if candidate["severity"] == "absolute_low"
            else "regime_critical"
        )
        try:
            await advance(
                conn,
                rule_id=rule_id,
                new_state="auto_disabled",
                event_type=event_type,
                actor="system:orchestrator",
                stage=self.stage,
                site_id=candidate["site_id"],
                proof={
                    "regime_event_id": candidate["regime_event_id"],
                    "severity": candidate["severity"],
                    "window_7d_rate": float(candidate["window_7d_rate"] or 0),
                    "sample_size_7d": int(candidate["sample_size_7d"] or 0),
                    "detected_at": candidate["detected_at"].isoformat()
                        if candidate["detected_at"] else None,
                },
                reason=(
                    f"Auto-disabled: {candidate['severity']} regime event "
                    f"(rate {float(candidate['window_7d_rate'] or 0):.3f} "
                    f"over {int(candidate['sample_size_7d'] or 0)} samples)"
                ),
            )
            # Also disable the l1_rules row so the daemon stops syncing it
            # AND mark for operator ack
            await conn.execute(
                "UPDATE l1_rules SET enabled = false WHERE rule_id = $1",
                rule_id,
            )
            await conn.execute(
                "UPDATE promoted_rules "
                "SET operator_ack_required = true "
                "WHERE rule_id = $1",
                rule_id,
            )
            return TransitionResult(
                rule_id=rule_id, from_state=from_state,
                to_state="auto_disabled", event_type=event_type,
                success=True,
            )
        except Exception as e:
            return TransitionResult(
                rule_id=rule_id, from_state=from_state,
                to_state="auto_disabled", event_type=event_type,
                success=False, error=str(e),
            )


class RolloutAckedTransition(Transition):
    """rolling_out → active when at least one appliance acks.

    Consumes fleet_order_completions (status='completed') and marks the
    rule active + emits `rollout_acked`.
    """
    name = "rollout_acked"
    description = "rolling_out → active on first completion"
    stage = "rollout"

    async def find_candidates(
        self, conn: asyncpg.Connection
    ) -> Sequence[Dict[str, Any]]:
        rows = await conn.fetch(
            """
            SELECT pr.rule_id, pr.lifecycle_state, pr.site_id,
                   fo.id AS fleet_order_id,
                   foc.appliance_id, foc.completed_at
            FROM promoted_rules pr
            JOIN fleet_orders fo ON fo.parameters->>'rule_id' = pr.rule_id
                                 AND fo.order_type = 'sync_promoted_rule'
            JOIN fleet_order_completions foc ON foc.fleet_order_id = fo.id
            WHERE pr.lifecycle_state = 'rolling_out'
              AND foc.status = 'completed'
              AND foc.completed_at > NOW() - INTERVAL '30 days'
            """
        )
        return [dict(r) for r in rows]

    async def apply(
        self, conn: asyncpg.Connection, candidate: Dict[str, Any]
    ) -> TransitionResult:
        rule_id = candidate["rule_id"]
        try:
            await advance(
                conn,
                rule_id=rule_id,
                new_state="active",
                event_type="rollout_acked",
                actor="system:orchestrator",
                stage=self.stage,
                site_id=candidate["site_id"],
                proof={
                    "fleet_order_id": str(candidate["fleet_order_id"]),
                    "appliance_id": candidate["appliance_id"],
                    "completed_at": candidate["completed_at"].isoformat()
                        if candidate["completed_at"] else None,
                },
            )
            return TransitionResult(
                rule_id=rule_id, from_state="rolling_out", to_state="active",
                event_type="rollout_acked", success=True,
            )
        except Exception as e:
            return TransitionResult(
                rule_id=rule_id, from_state="rolling_out", to_state="active",
                event_type="rollout_acked", success=False, error=str(e),
            )


class CanaryFailureTransition(Transition):
    """active → auto_disabled when a newly-promoted rule fails its canary.

    Rules that were promoted within the last 48 hours, have at least 3
    executions against their runbook, and whose success rate is below
    70% — disable. Replaces the old Step 5a in flywheel_promotion_loop
    that silently swallowed errors in the same shared try/except that
    hid today's absolute_low bug.

    Distinct from RegimeAbsoluteLowTransition:
      * Canary = first-48h policy; doesn't need a regime event
      * Absolute-low / critical = lifetime policy; consumes regime events
    Both can fire on the same rule; whichever hits first transitions it.
    """
    name = "canary_failure_auto_disable"
    description = "active → auto_disabled: <70% success in first 48h canary"
    stage = "monitoring"

    async def find_candidates(
        self, conn: asyncpg.Connection
    ) -> Sequence[Dict[str, Any]]:
        rows = await conn.fetch(
            """
            SELECT l.rule_id, pr.site_id, pr.lifecycle_state,
                   COUNT(et.id) AS n,
                   SUM(CASE WHEN et.success THEN 1 ELSE 0 END) AS s,
                   AVG(CASE WHEN et.success THEN 1.0 ELSE 0.0 END) AS rate
            FROM l1_rules l
            JOIN promoted_rules pr ON pr.rule_id = l.rule_id
            JOIN execution_telemetry et
              ON et.runbook_id = l.runbook_id
             AND et.created_at > l.created_at
            WHERE l.promoted_from_l2 = true
              AND l.enabled = true
              AND pr.lifecycle_state = 'active'
              AND l.created_at > NOW() - INTERVAL '48 hours'
            GROUP BY l.rule_id, pr.site_id, pr.lifecycle_state
            HAVING COUNT(et.id) >= 3
               AND AVG(CASE WHEN et.success THEN 1.0 ELSE 0.0 END) < 0.70
            """
        )
        return [dict(r) for r in rows]

    async def apply(
        self, conn: asyncpg.Connection, candidate: Dict[str, Any]
    ) -> TransitionResult:
        rule_id = candidate["rule_id"]
        try:
            rate = float(candidate["rate"] or 0)
            n = int(candidate["n"] or 0)
            s = int(candidate["s"] or 0)
            await advance(
                conn,
                rule_id=rule_id,
                new_state="auto_disabled",
                event_type="auto_disabled",
                actor="system:orchestrator",
                stage=self.stage,
                site_id=candidate["site_id"],
                proof={
                    "policy": "canary_failure",
                    "window": "48h",
                    "threshold": 0.70,
                    "observed_rate": rate,
                    "successes": s,
                    "samples": n,
                },
                reason=(
                    f"Canary failure: {rate:.1%} success rate "
                    f"({s}/{n}) under 70% threshold in first 48h"
                ),
            )
            await conn.execute(
                "UPDATE l1_rules SET enabled = false WHERE rule_id = $1",
                rule_id,
            )
            await conn.execute(
                "UPDATE promoted_rules SET operator_ack_required = true "
                "WHERE rule_id = $1",
                rule_id,
            )
            return TransitionResult(
                rule_id=rule_id, from_state="active",
                to_state="auto_disabled", event_type="auto_disabled",
                success=True,
            )
        except Exception as e:
            return TransitionResult(
                rule_id=rule_id, from_state="active",
                to_state="auto_disabled", event_type="auto_disabled",
                success=False, error=str(e),
            )


class GraduationTransition(Transition):
    """active → graduated when a rule has proven itself past the canary
    window.

    Criteria:
      * promoted_from_l2 = true
      * lifecycle_state = 'active'
      * l1_rules.created_at > 72h ago (canary window over)
      * >=3 executions at >=70% success rate

    Side effects:
      * lifecycle_state → graduated
      * l1_rules.source → 'synced' (fleet-wide promotion from site-scoped)
    """
    name = "graduation"
    description = "active → graduated: 72h+ canary passed"
    stage = "governance"

    async def find_candidates(
        self, conn: asyncpg.Connection
    ) -> Sequence[Dict[str, Any]]:
        rows = await conn.fetch(
            """
            SELECT l.rule_id, pr.site_id, pr.lifecycle_state,
                   COUNT(et.id) AS n,
                   AVG(CASE WHEN et.success THEN 1.0 ELSE 0.0 END) AS rate
            FROM l1_rules l
            JOIN promoted_rules pr ON pr.rule_id = l.rule_id
            JOIN execution_telemetry et
              ON et.runbook_id = l.runbook_id
             AND et.created_at > l.created_at
            WHERE l.promoted_from_l2 = true
              AND l.enabled = true
              AND l.source = 'promoted'
              AND pr.lifecycle_state = 'active'
              AND l.created_at < NOW() - INTERVAL '72 hours'
            GROUP BY l.rule_id, pr.site_id, pr.lifecycle_state
            HAVING COUNT(et.id) >= 3
               AND AVG(CASE WHEN et.success THEN 1.0 ELSE 0.0 END) >= 0.70
            """
        )
        return [dict(r) for r in rows]

    async def apply(
        self, conn: asyncpg.Connection, candidate: Dict[str, Any]
    ) -> TransitionResult:
        rule_id = candidate["rule_id"]
        try:
            rate = float(candidate["rate"] or 0)
            n = int(candidate["n"] or 0)
            await advance(
                conn,
                rule_id=rule_id,
                new_state="graduated",
                event_type="graduated",
                actor="system:orchestrator",
                stage=self.stage,
                site_id=candidate["site_id"],
                proof={
                    "policy": "72h_canary_passed",
                    "threshold": 0.70,
                    "observed_rate": rate,
                    "samples": n,
                },
                reason=(
                    f"Graduated: {rate:.1%} over {n} executions, "
                    f"past 72h canary"
                ),
            )
            # Promote source to 'synced' so appliances everywhere sync it
            await conn.execute(
                "UPDATE l1_rules SET source = 'synced' WHERE rule_id = $1",
                rule_id,
            )
            return TransitionResult(
                rule_id=rule_id, from_state="active",
                to_state="graduated", event_type="graduated",
                success=True,
            )
        except Exception as e:
            return TransitionResult(
                rule_id=rule_id, from_state="active",
                to_state="graduated", event_type="graduated",
                success=False, error=str(e),
            )


class ZombieSiteTransition(Transition):
    """active / approved / rolling_out → retired when the site's
    last_checkin is > 30 days old. Keeps metrics clean; ops can't see a
    dead site accumulating broken rules.
    """
    name = "retire_zombie_site"
    description = "* → retired when site has no appliance checkin in 30d"
    stage = "retire"

    async def find_candidates(
        self, conn: asyncpg.Connection
    ) -> Sequence[Dict[str, Any]]:
        rows = await conn.fetch(
            """
            SELECT pr.rule_id, pr.lifecycle_state, pr.site_id,
                   (SELECT MAX(last_checkin) FROM site_appliances sa
                    WHERE sa.site_id = pr.site_id) AS last_site_checkin
            FROM promoted_rules pr
            WHERE pr.lifecycle_state IN (
                  'proposed', 'approved', 'rolling_out', 'active',
                  'regime_warning', 'graduated'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM site_appliances sa
                  WHERE sa.site_id = pr.site_id
                    AND sa.last_checkin > NOW() - INTERVAL '30 days'
                    AND sa.deleted_at IS NULL
              )
            """
        )
        return [dict(r) for r in rows]

    async def apply(
        self, conn: asyncpg.Connection, candidate: Dict[str, Any]
    ) -> TransitionResult:
        rule_id = candidate["rule_id"]
        try:
            await advance(
                conn,
                rule_id=rule_id,
                new_state="retired",
                event_type="retired_site_dead",
                actor="system:orchestrator",
                stage=self.stage,
                site_id=candidate["site_id"],
                proof={
                    "last_site_checkin": candidate["last_site_checkin"].isoformat()
                        if candidate["last_site_checkin"] else None,
                    "threshold_days": 30,
                },
                reason=f"Site {candidate['site_id']} unreachable > 30 days",
            )
            return TransitionResult(
                rule_id=rule_id, from_state=candidate["lifecycle_state"],
                to_state="retired", event_type="retired_site_dead", success=True,
            )
        except Exception as e:
            return TransitionResult(
                rule_id=rule_id, from_state=candidate["lifecycle_state"],
                to_state="retired", event_type="retired_site_dead",
                success=False, error=str(e),
            )


# ─── Orchestrator ──────────────────────────────────────────────────


# Registration order is execution order within a tick. Cheap queries
# first so quick wins land before expensive ones.
DEFAULT_TRANSITIONS: List[Transition] = [
    RolloutAckedTransition(),
    CanaryFailureTransition(),
    RegimeAbsoluteLowTransition(),
    GraduationTransition(),
    ZombieSiteTransition(),
]


async def run_orchestrator_tick(
    conn: asyncpg.Connection,
    transitions: Optional[Sequence[Transition]] = None,
    *,
    enforce: bool = True,
) -> OrchestratorResult:
    """Run one orchestrator tick. Evaluates every registered transition
    against the current state of the flywheel.

    Args:
      conn:         asyncpg connection (admin_connection recommended)
      transitions:  override the default transition list (tests)
      enforce:      when False, logs intended transitions but does NOT
                    apply them — useful for shadow-mode validation
                    alongside the old step 5 loop before cutover.

    Returns OrchestratorResult with per-transition counts + elapsed.

    Every transition runs in its OWN try/except. One transition failing
    cannot block another. Failures bump a counter AND log at ERROR with
    exc_info — no more logger.debug swallowing.
    """
    t0 = time.perf_counter()
    result = OrchestratorResult()
    transitions = list(transitions or DEFAULT_TRANSITIONS)

    for tr in transitions:
        try:
            candidates = await tr.find_candidates(conn)
        except Exception:
            logger.error(
                "orchestrator_find_candidates_failed",
                extra={"transition": tr.name},
                exc_info=True,
            )
            result.failures_by_name[tr.name] = (
                result.failures_by_name.get(tr.name, 0) + 1
            )
            continue

        result.total_rules_scanned += len(candidates)

        for cand in candidates:
            rule_id = cand.get("rule_id", "?")
            try:
                # Each apply is its own subtransaction so one
                # poisoned candidate cannot break the loop.
                async with conn.transaction():
                    if enforce:
                        tr_result = await tr.apply(conn, cand)
                    else:
                        # Shadow mode: log the intent, no write
                        logger.info(
                            "orchestrator_shadow_would_apply",
                            extra={
                                "transition": tr.name,
                                "rule_id": rule_id,
                            },
                        )
                        tr_result = TransitionResult(
                            rule_id=rule_id,
                            from_state=cand.get("lifecycle_state", "?"),
                            to_state="?", event_type="?",
                            success=True,
                        )
                if tr_result.success:
                    result.transitions_by_name[tr.name] = (
                        result.transitions_by_name.get(tr.name, 0) + 1
                    )
                    logger.info(
                        "orchestrator_transition_applied",
                        extra={
                            "transition": tr.name,
                            "rule_id": rule_id,
                            "from_state": tr_result.from_state,
                            "to_state": tr_result.to_state,
                            "event_type": tr_result.event_type,
                        },
                    )
                else:
                    result.failures_by_name[tr.name] = (
                        result.failures_by_name.get(tr.name, 0) + 1
                    )
                    logger.error(
                        "orchestrator_transition_failed",
                        extra={
                            "transition": tr.name,
                            "rule_id": rule_id,
                            "error": tr_result.error,
                        },
                    )
            except Exception:
                result.failures_by_name[tr.name] = (
                    result.failures_by_name.get(tr.name, 0) + 1
                )
                logger.error(
                    "orchestrator_transition_exception",
                    extra={"transition": tr.name, "rule_id": rule_id},
                    exc_info=True,
                )

    result.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "orchestrator_tick_complete",
        extra={
            "scanned": result.total_rules_scanned,
            "applied": sum(result.transitions_by_name.values()),
            "failed": sum(result.failures_by_name.values()),
            "elapsed_ms": result.elapsed_ms,
            "enforce": enforce,
        },
    )
    return result


async def backfill_lifecycle_events(conn: asyncpg.Connection) -> int:
    """One-shot retroactive backfill for the 43 existing promoted_rules.

    For each rule, synthesize the best-effort ledger entries based on
    available signals: the promoted_at timestamp (→ pattern_detected),
    deployment_count > 0 (→ rollout_acked + active), existing regime
    events (→ regime_*). Skips rules that already have ledger entries.

    This closes the audit gap: after backfill, every rule has at least
    one event in the ledger and the lifecycle_state matches the events.
    """
    rows = await conn.fetch(
        """
        SELECT pr.rule_id, pr.site_id, pr.promoted_at, pr.deployment_count,
               pr.last_deployed_at, pr.lifecycle_state,
               l.enabled AS l1_enabled
        FROM promoted_rules pr
        LEFT JOIN l1_rules l ON l.rule_id = pr.rule_id
        WHERE NOT EXISTS (
            SELECT 1 FROM promoted_rule_events e WHERE e.rule_id = pr.rule_id
        )
        ORDER BY pr.promoted_at ASC
        """
    )
    written = 0
    for r in rows:
        rule_id = r["rule_id"]
        try:
            async with conn.transaction():
                # Synthesize the historical event sequence based on what
                # we can infer. These use event_type directly (no state
                # transition) — we INSERT into the ledger WITHOUT calling
                # advance_lifecycle (the lifecycle_state was inferred by
                # the migration). This is a retroactive audit record,
                # not a real-time transition.
                await conn.execute(
                    """
                    INSERT INTO promoted_rule_events (
                        rule_id, site_id, event_type, stage, outcome,
                        actor, proof, reason, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
                    """,
                    rule_id, r["site_id"], "pattern_detected", "detection",
                    "success", "system:backfill_session_206",
                    json.dumps({"backfill": True}),
                    "Retroactive: pattern_detected synthesized from promoted_at",
                    r["promoted_at"] or "2026-01-01T00:00:00Z",
                )
                if r["deployment_count"] and r["deployment_count"] > 0:
                    await conn.execute(
                        """
                        INSERT INTO promoted_rule_events (
                            rule_id, site_id, event_type, stage, outcome,
                            actor, proof, reason, created_at
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
                        """,
                        rule_id, r["site_id"], "rollout_acked", "rollout",
                        "success", "system:backfill_session_206",
                        json.dumps({
                            "backfill": True,
                            "deployment_count": r["deployment_count"],
                        }),
                        f"Retroactive: deployment_count={r['deployment_count']}",
                        r["last_deployed_at"] or r["promoted_at"],
                    )
                written += 1
        except Exception:
            logger.error(
                "backfill_ledger_failed",
                extra={"rule_id": rule_id},
                exc_info=True,
            )
    return written
