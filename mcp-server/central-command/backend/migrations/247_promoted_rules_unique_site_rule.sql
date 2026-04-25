-- Migration 247 — Session 210-B 2026-04-25
--
-- promoted_rules tracks rule rollouts per-site, so the same rule_id
-- legitimately appears in multiple rows (one per site it was rolled
-- out to). Today's data: 43 rows, 28 distinct rule_ids — 5 platform
-- rules with 2 site-rollouts each.
--
-- The flywheel_promote.py upsert tried `ON CONFLICT (rule_id)
-- DO UPDATE` which fails with InvalidColumnReferenceError because
-- rule_id has no unique constraint (only the PK on id is unique).
-- Approving any candidate from /learning fails 500 because of this.
--
-- Fix: add UNIQUE(site_id, rule_id) — the actual natural key. Verified
-- empirically (zero (site_id, rule_id) duplicates in current data) so
-- the constraint adds cleanly.
--
-- The flywheel_promote.py code is updated in the same commit to use
-- `ON CONFLICT (site_id, rule_id)` so a re-promotion under the same
-- (site, rule) updates rather than errors.

BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS promoted_rules_site_rule_uniq
    ON promoted_rules (site_id, rule_id);

COMMIT;
