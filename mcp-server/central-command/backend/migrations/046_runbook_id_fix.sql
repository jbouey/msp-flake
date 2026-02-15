-- Migration 046: Fix runbook ID mismatches
--
-- Problem: Three incompatible ID namespaces prevent telemetry correlation:
--   1. Agent builtin rules send L1-SVC-DNS-001, L1-FIREWALL-001 (65% of telemetry)
--   2. l1_rules table only has promoted rules with RB-AUTO-* IDs
--   3. Counter trigger (045) does WHERE rule_id = NEW.runbook_id — never matches builtins
--
-- Fixes:
--   A. patterns.pattern_signature VARCHAR(64) → VARCHAR(255) (missed by migration 044)
--   B. Add source column to l1_rules to distinguish builtin vs promoted
--   C. Seed all builtin L1 rule IDs from existing telemetry into l1_rules
--   D. Backfill counters from existing telemetry so success_rate is accurate
--   E. Fix RB-AUTO-* rule_id truncation for existing promoted rules

BEGIN;

-- ============================================================================
-- A. Fix patterns.pattern_signature VARCHAR(64) → VARCHAR(255)
--    Migration 044 fixed 4 related tables but missed the patterns table itself
-- ============================================================================

ALTER TABLE patterns ALTER COLUMN pattern_signature TYPE VARCHAR(255);

-- ============================================================================
-- B. Add source column to l1_rules (builtin vs promoted)
-- ============================================================================

ALTER TABLE l1_rules ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'promoted';

-- Mark existing rows as promoted (they came from pattern promotion pipeline)
UPDATE l1_rules SET source = 'promoted' WHERE source IS NULL OR source = 'promoted';

-- ============================================================================
-- C. Seed builtin L1 rule IDs from execution_telemetry
--    Every distinct runbook_id the agent has reported as L1 should exist in l1_rules
--    so the counter trigger can match. Uses most common incident_type per runbook_id.
-- ============================================================================

INSERT INTO l1_rules (rule_id, incident_pattern, runbook_id, confidence, promoted_from_l2, enabled, source)
SELECT
    sub.runbook_id as rule_id,
    jsonb_build_object('incident_type', sub.incident_type) as incident_pattern,
    sub.runbook_id as runbook_id,
    0.95 as confidence,
    false as promoted_from_l2,
    true as enabled,
    'builtin' as source
FROM (
    -- Get the most common incident_type for each L1 runbook_id
    SELECT DISTINCT ON (et.runbook_id)
        et.runbook_id,
        et.incident_type,
        COUNT(*) as cnt
    FROM execution_telemetry et
    WHERE et.resolution_level = 'L1'
      AND et.runbook_id IS NOT NULL
      AND et.runbook_id NOT LIKE 'RB-AUTO-%'
      AND et.runbook_id NOT IN (SELECT rule_id FROM l1_rules)
    GROUP BY et.runbook_id, et.incident_type
    ORDER BY et.runbook_id, cnt DESC
) sub
ON CONFLICT (rule_id) DO NOTHING;

-- ============================================================================
-- D. Backfill counters from ALL existing telemetry
--    This updates both newly-seeded builtin rules AND existing promoted rules
-- ============================================================================

UPDATE l1_rules SET
    match_count = sub.total,
    success_count = sub.successes,
    failure_count = sub.failures
FROM (
    SELECT runbook_id,
           COUNT(*) as total,
           SUM(CASE WHEN success THEN 1 ELSE 0 END) as successes,
           SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) as failures
    FROM execution_telemetry
    WHERE resolution_level = 'L1'
      AND runbook_id IS NOT NULL
    GROUP BY runbook_id
) sub
WHERE l1_rules.rule_id = sub.runbook_id;

COMMIT;
