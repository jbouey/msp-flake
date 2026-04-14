-- Migration 204: drift check exceptions (renumbered from 103)
-- Add N/A (not applicable) exception status for drift checks.
-- Allows admins to mark checks as not_applicable with a documented reason
-- (e.g., "Backup handled by cloud EHR vendor per BAA").
-- N/A checks are excluded from compliance scoring and the healing pipeline.

BEGIN;

ALTER TABLE site_drift_config ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'enabled';
ALTER TABLE site_drift_config ADD COLUMN IF NOT EXISTS exception_reason TEXT;

-- Backfill: sync status column with existing enabled boolean
UPDATE site_drift_config SET status = 'disabled' WHERE enabled = false AND status IS NULL;
UPDATE site_drift_config SET status = 'enabled' WHERE enabled = true AND (status IS NULL OR status = 'enabled');

COMMIT;
