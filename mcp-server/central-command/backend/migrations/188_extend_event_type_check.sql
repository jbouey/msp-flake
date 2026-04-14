-- Migration 188 — extend promoted_rule_events.event_type CHECK.
--
-- The orchestrator (flywheel_state.py) emits two event types that
-- migration 181's CHECK never allowed:
--   * `retired_site_dead`  — ZombieSiteTransition.apply()
--   * `regime_critical`    — RegimeAbsoluteLowTransition.apply() for
--                            severity='critical' (distinct from
--                            'absolute_low' which IS in the CHECK)
--
-- Shadow mode dodged this because it skipped advance_lifecycle() calls
-- entirely. The flip to enforce on 2026-04-14 surfaced it — every
-- transition in the first enforce tick tripped the CHECK (26/26).
--
-- Adding the missing names so enforce mode can make forward progress.
-- Keeps the spine's "three lists in lockstep" invariant (CLAUDE.md):
-- code / CHECK / transitions matrix must agree.

BEGIN;

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
       AND position('retired_site_dead' in current_check) = 0 THEN
        ALTER TABLE promoted_rule_events
            DROP CONSTRAINT promoted_rule_events_event_type_check;
        ALTER TABLE promoted_rule_events
            ADD CONSTRAINT promoted_rule_events_event_type_check
            CHECK (event_type IN (
                -- lifecycle events (spine — migration 181)
                'proposed','shadow_entered','approved','rollout_started','rollout_acked',
                'canary_failed','auto_disabled','regime_warning','operator_re_enabled',
                'operator_acknowledged','graduated','retired','zombie_site',
                'regime_absolute_low','stage_change','reviewer_note',
                -- orchestrator-specific events (migration 188 — unblocks enforce)
                'retired_site_dead','regime_critical',
                -- consent events (migration 184)
                'runbook.consented','runbook.amended','runbook.revoked',
                'runbook.executed_with_consent'
            ));
    END IF;
END $$;

COMMIT;
