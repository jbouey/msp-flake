-- Migration 065: Enforce NOT NULL on l1_rules.runbook_id
--
-- NULL runbook_ids break flywheel pattern matching since the pattern signature
-- format is incident_type:runbook_id:hostname. Backfill any NULLs first.

BEGIN;

-- Backfill NULL runbook_ids with a placeholder derived from the rule_id
-- These rules need manual review to assign proper runbook_ids
UPDATE l1_rules
SET runbook_id = 'UNASSIGNED-' || rule_id
WHERE runbook_id IS NULL OR runbook_id = '';

-- Now enforce the constraint
ALTER TABLE l1_rules ALTER COLUMN runbook_id SET NOT NULL;

COMMIT;
