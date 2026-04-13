-- Migration 162: Backfill synthetic L2-* runbook_ids to canonical runbooks
--
-- Session 205 audit found two historical pattern_keys in platform_pattern_stats
-- with synthetic L2-* runbook_ids that were never present in the runbooks
-- library. The current L2 planner validates and rejects synthetic IDs at
-- emission time, but legacy rows still exist and trigger
-- "Skipping platform promotion: invalid runbook_id" warnings every 30 min.
--
-- This migration maps each synthetic ID to its semantic canonical runbook
-- so the patterns become eligible for auto-promotion on the next scan.
--
-- Mapping rationale (validated against runbooks table 2026-04-13):
--   L2-restore_firewall_baseline → RB-FIREWALL-001 (Firewall Baseline,
--                                  check_type=firewall, severity=critical)
--   L2-run_backup_job            → RB-BACKUP-001   (Backup Verification,
--                                  check_type=backup, severity=high)
--
-- Both targets exist; verified with:
--   SELECT runbook_id FROM runbooks WHERE runbook_id IN
--      ('RB-FIREWALL-001','RB-BACKUP-001');
--
-- This migration is idempotent: rows already mapped to the canonical IDs
-- are unaffected; only the synthetic L2-* rows are rewritten.

BEGIN;

-- Update platform_pattern_stats — flywheel auto-promote reads from this
UPDATE platform_pattern_stats
SET runbook_id = 'RB-FIREWALL-001',
    pattern_key = REPLACE(pattern_key, ':L2-restore_firewall_baseline', ':RB-FIREWALL-001')
WHERE runbook_id = 'L2-restore_firewall_baseline';

UPDATE platform_pattern_stats
SET runbook_id = 'RB-BACKUP-001',
    pattern_key = REPLACE(pattern_key, ':L2-run_backup_job', ':RB-BACKUP-001')
WHERE runbook_id = 'L2-run_backup_job';

-- Update aggregated_pattern_stats — alternate aggregation table
UPDATE aggregated_pattern_stats
SET runbook_id = 'RB-FIREWALL-001'
WHERE runbook_id = 'L2-restore_firewall_baseline';

UPDATE aggregated_pattern_stats
SET runbook_id = 'RB-BACKUP-001'
WHERE runbook_id = 'L2-run_backup_job';

-- Update legacy patterns table for completeness
UPDATE patterns
SET pattern_signature = REPLACE(pattern_signature, ':L2-restore_firewall_baseline', ':RB-FIREWALL-001')
WHERE pattern_signature LIKE '%:L2-restore_firewall_baseline%';

UPDATE patterns
SET pattern_signature = REPLACE(pattern_signature, ':L2-run_backup_job', ':RB-BACKUP-001')
WHERE pattern_signature LIKE '%:L2-run_backup_job%';

COMMIT;
