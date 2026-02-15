-- Migration 044: Flywheel promotion fixes
--
-- Fixes:
-- 1. Extends pattern_signature columns from VARCHAR(64) to VARCHAR(255) to prevent truncation
-- 2. Adds tracking columns to l1_rules (match_count, success_count, failure_count, success_rate)
-- 3. Enables all promoted L1 rules that were incorrectly created as disabled
-- 4. Updates promotion_eligible flag on aggregated_pattern_stats for qualifying patterns

BEGIN;

-- ============================================================================
-- 1. Fix pattern_signature column truncation (VARCHAR(64) → VARCHAR(255))
-- ============================================================================

ALTER TABLE aggregated_pattern_stats
    ALTER COLUMN pattern_signature TYPE VARCHAR(255);

ALTER TABLE promoted_rules
    ALTER COLUMN pattern_signature TYPE VARCHAR(255);

ALTER TABLE learning_promotion_candidates
    ALTER COLUMN pattern_signature TYPE VARCHAR(255);

ALTER TABLE promoted_rule_deployments
    ALTER COLUMN pattern_signature TYPE VARCHAR(255);

-- ============================================================================
-- 2. Add tracking columns to l1_rules (safe: IF NOT EXISTS)
-- ============================================================================

ALTER TABLE l1_rules ADD COLUMN IF NOT EXISTS match_count INTEGER DEFAULT 0;
ALTER TABLE l1_rules ADD COLUMN IF NOT EXISTS success_count INTEGER DEFAULT 0;
ALTER TABLE l1_rules ADD COLUMN IF NOT EXISTS failure_count INTEGER DEFAULT 0;
ALTER TABLE l1_rules ADD COLUMN IF NOT EXISTS success_rate FLOAT DEFAULT 0.0;

-- Add check_type for aggregated_pattern_stats (some envs may already have this)
ALTER TABLE aggregated_pattern_stats ADD COLUMN IF NOT EXISTS check_type VARCHAR(100);

-- ============================================================================
-- 3. Enable all promoted L1 rules (were incorrectly disabled)
-- ============================================================================

UPDATE l1_rules
SET enabled = true
WHERE promoted_from_l2 = true AND enabled = false;

-- ============================================================================
-- 4. Update promotion_eligible for qualifying patterns
--    Criteria: >=5 occurrences, >=90% success rate, last seen within 7 days
-- ============================================================================

UPDATE aggregated_pattern_stats
SET promotion_eligible = true
WHERE total_occurrences >= 5
  AND success_rate >= 0.90
  AND last_seen > NOW() - INTERVAL '7 days'
  AND promotion_eligible = false;

-- Recreate index for enabled rules (was dropped in migration 039)
CREATE INDEX IF NOT EXISTS idx_l1_rules_enabled_v2
    ON l1_rules(enabled) WHERE enabled = true;

COMMIT;
