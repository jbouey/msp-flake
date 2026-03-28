-- Migration 106: Add 'monitoring' to incidents.resolution_tier check constraint
-- The monitoring-only guard sets resolution_tier = 'monitoring' for checks that
-- can't be auto-remediated (device_unreachable, backup_not_configured, etc.)
-- but the constraint only allowed L1/L2/L3.

ALTER TABLE incidents DROP CONSTRAINT IF EXISTS incidents_resolution_tier_check;

ALTER TABLE incidents ADD CONSTRAINT incidents_resolution_tier_check
    CHECK (resolution_tier IN ('L1', 'L2', 'L3', 'monitoring'));
