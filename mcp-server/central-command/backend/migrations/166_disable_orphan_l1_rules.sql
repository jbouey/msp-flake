-- Migration 166: Disable L1 rules pointing to runbook_ids that don't exist
-- in the runbooks library.
--
-- Session 205 audit found 20 `promoted_from_l2=true, enabled=true` rules
-- whose runbook_id has no matching row in `runbooks`. These rules are NOT
-- executable — when the appliance daemon tries to run them it looks up
-- the runbook_id and finds nothing. They inflate the
-- osiriscare_flywheel_orphan_runbooks gauge without adding value.
--
-- The orphan runbook_ids (and rule counts):
--   general (8 rules)
--   L1-NET-DNS-001, L1-NET-PORTS-001, L1-NET-REACH-001 (2 each)
--   L1-WIN-ROGUE-TASKS-001, RB-WIN-SEC-028 (2 each)
--   L1-LIN-UPGRADES-001, L1-LIN-USERS-001 (1 each)
--
-- These all come from a 2026-04-09 manual backfill batch that wrote L1
-- rules before the corresponding runbook entries existed. Disabling the
-- rules is the truthful fix: the system should not claim an L1 remediation
-- exists when there is no executable runbook for it. The rules remain in
-- the table (history preserved, promoted_from_l2=true); they just won't
-- fire. If a runbook is later added for one of these IDs, re-enable that
-- specific rule.
--
-- This migration:
--   1. Disables the orphan L1 rules
--   2. Writes a single admin_audit_log entry with the full rule list
--   3. Is idempotent: re-running is a no-op (already disabled rules stay
--      disabled; the audit log INSERT always writes a new row which is
--      fine for a repeated ops action)

BEGIN;

WITH orphans AS (
    SELECT l.rule_id, l.runbook_id
    FROM l1_rules l
    LEFT JOIN runbooks r ON r.runbook_id = l.runbook_id
    WHERE l.promoted_from_l2 = true
      AND l.enabled = true
      AND r.runbook_id IS NULL
), disabled AS (
    UPDATE l1_rules
       SET enabled = false
     WHERE rule_id IN (SELECT rule_id FROM orphans)
    RETURNING rule_id, runbook_id
)
INSERT INTO admin_audit_log (username, action, target, details, created_at)
SELECT 'system',
       'ORPHAN_L1_RULES_DISABLED',
       'l1_rules',
       jsonb_build_object(
           'reason', 'Runbook_id has no matching runbooks row — rule not executable',
           'phase', 'session_205_followup_1',
           'rules', jsonb_agg(jsonb_build_object('rule_id', rule_id, 'runbook_id', runbook_id))
       ),
       NOW()
  FROM disabled
 HAVING COUNT(*) > 0;

COMMIT;
