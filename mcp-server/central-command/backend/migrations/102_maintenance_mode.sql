-- Migration 102: Maintenance mode for sites
-- Allows partners/admins to suppress drift detection during planned maintenance windows.
-- When maintenance_until > NOW(), incident creation is suppressed for the site.

ALTER TABLE sites ADD COLUMN IF NOT EXISTS maintenance_until TIMESTAMPTZ;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS maintenance_reason TEXT;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS maintenance_set_by TEXT;
