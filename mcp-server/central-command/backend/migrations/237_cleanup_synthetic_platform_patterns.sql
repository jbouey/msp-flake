-- Migration 237: one-shot cleanup of synthetic L2-* rows in
-- platform_pattern_stats.
--
-- Context: migration 162 backfilled the two known synthetic runbook_ids
-- (L2-restore_firewall_baseline, L2-run_backup_job) to canonical IDs on
-- 2026-04-13, but the 30-min aggregation query in background_tasks.py
-- kept re-creating them from legacy execution_telemetry rows (Jan 2026).
-- Each cycle emitted "Skipping platform promotion: invalid runbook_id"
-- warnings and kept two phantom pending candidates alive.
--
-- This migration pairs with the background_tasks.py WHERE-clause fix
-- (`AND et.runbook_id NOT LIKE 'L2-%'`) shipped in the same commit.
-- With both in place, no new L2-* rows can be created in pps and the
-- existing two are removed here.
--
-- Idempotent: re-running is a no-op once the rows are gone.

BEGIN;

DELETE FROM platform_pattern_stats
 WHERE runbook_id LIKE 'L2-%';

COMMIT;

SELECT 'Migration 237_cleanup_synthetic_platform_patterns complete' AS status;
