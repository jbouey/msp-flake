-- Migration 236 — Flywheel spine repair (three-list lockstep + proposed→rolling_out).
--
-- Runtime audit on 2026-04-18 discovered three independent spine regressions:
--
--   1. THREE-LIST LOCKSTEP BROKEN. Python EVENT_TYPES (flywheel_state.py, 16
--      entries) and the live CHECK on promoted_rule_events (22 entries from
--      migration 188) disagreed on 7 names. Python-only: pattern_detected,
--      shadow_evaluated, promotion_approved, rollout_issued, first_execution,
--      manually_disabled, retired_manual. Every advance_lifecycle() call
--      that emits one of those types would trip the CHECK and roll the
--      transaction back. The silent-swallow in safe_rollout_promoted_rule
--      downgrades that to a WARNING, so the orchestrator reported "green"
--      while writing zero ledger rows. Observed symptom: Flywheel Intelligence
--      dashboard stuck at all-zeros despite active promotions.
--
--   2. proposed → rolling_out MISSING. promoted_rules.lifecycle_state defaults
--      to 'proposed', but every promotion writer (promote_candidate,
--      learning_api.bulk_promote, client_portal.approve) immediately calls
--      safe_rollout which advances the rule to 'rolling_out'. The transition
--      matrix allows proposed→approved and approved→rolling_out as separate
--      hops. Auto-promotions skip manual approval by design (L2 already
--      evaluated the pattern) — the missing direct edge means every auto-
--      promotion since Session 206 cutover failed the transition check.
--
--   3. PARTITION EXHAUSTION IMMINENT. Only 2026-04, 2026-05, default
--      partitions exist. After 2026-05-31 every new event lands in the
--      default partition. We're adding 2026-06/07/08 now; a new partition
--      maintainer loop (background_tasks.py) takes over nightly thereafter.
--
-- Idempotent: safe to re-run. Each ALTER is gated on "is the thing already
-- done." Pattern matches migration 188.

BEGIN;

-- ─── 1. Extend event_type CHECK to union of all 29 names ──────────
--
-- The 22 existing names come from migrations 181 + 184 + 188 and are
-- in active use (retired_site_dead, regime_critical, runbook.*, etc.).
-- We add the 7 Python-only names so advance_lifecycle() from code paths
-- like ZombieSiteTransition, RegimeAbsoluteLowTransition, RolloutAckedTransition
-- can land rows without tripping the CHECK.

DO $$
DECLARE
    current_check text;
BEGIN
    SELECT pg_get_constraintdef(c.oid)
      INTO current_check
      FROM pg_constraint c
      JOIN pg_class t ON t.oid = c.conrelid
     WHERE t.relname = 'promoted_rule_events'
       AND c.conname = 'promoted_rule_events_event_type_check';

    IF current_check IS NOT NULL
       AND position('rollout_issued' in current_check) = 0 THEN
        ALTER TABLE promoted_rule_events
            DROP CONSTRAINT promoted_rule_events_event_type_check;
        ALTER TABLE promoted_rule_events
            ADD CONSTRAINT promoted_rule_events_event_type_check
            CHECK (event_type IN (
                -- Original lifecycle events (migration 181)
                'proposed','shadow_entered','approved','rollout_started','rollout_acked',
                'canary_failed','auto_disabled','regime_warning','operator_re_enabled',
                'operator_acknowledged','graduated','retired','zombie_site',
                'regime_absolute_low','stage_change','reviewer_note',
                -- Orchestrator-specific (migration 188)
                'retired_site_dead','regime_critical',
                -- Runbook consent (migration 184)
                'runbook.consented','runbook.amended','runbook.revoked',
                'runbook.executed_with_consent',
                -- Python-emitted names (migration 236 — this file)
                'pattern_detected','shadow_evaluated','promotion_approved',
                'rollout_issued','first_execution','manually_disabled',
                'retired_manual'
            ));
    END IF;
END $$;

-- ─── 2. Add proposed → rolling_out transition ─────────────────────
--
-- Auto-promotions (L2→L1) do not need the two-hop proposed→approved→rolling_out
-- path; L2 already evaluated the pattern. The direct edge preserves the
-- audit trail: one event 'rollout_issued' on the promotion boundary.
--
-- Manual-approval flows (client_portal.approve with explicit operator
-- action) still use proposed→approved→rolling_out when we add a UI for
-- that. Both edges coexist.

INSERT INTO promoted_rule_lifecycle_transitions (from_state, to_state)
VALUES ('proposed', 'rolling_out')
ON CONFLICT (from_state, to_state) DO NOTHING;

-- ─── 3. Pre-create partitions for 2026-06, 07, 08 ─────────────────
--
-- The promoted_rule_events table was created with only 2026-04, 2026-05
-- and a default partition. The default partition works as an overflow
-- safety net, but accumulating rows there defeats partition pruning and
-- makes future DETACH operations painful. Nightly partition_maintainer_loop
-- (background_tasks.py) will own the steady-state cadence; this migration
-- primes the next 3 months so a deploy delay doesn't strand rows.

CREATE TABLE IF NOT EXISTS promoted_rule_events_202606
    PARTITION OF promoted_rule_events
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE IF NOT EXISTS promoted_rule_events_202607
    PARTITION OF promoted_rule_events
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

CREATE TABLE IF NOT EXISTS promoted_rule_events_202608
    PARTITION OF promoted_rule_events
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

COMMIT;
