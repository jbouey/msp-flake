-- Migration 118: Auth failure tracking for auto-rekey support
-- Tracks persistent 401 failures so dashboard can show "Auth Failed" status
-- and health monitor can alert on it.

ALTER TABLE site_appliances ADD COLUMN IF NOT EXISTS auth_failure_since TIMESTAMPTZ;
ALTER TABLE site_appliances ADD COLUMN IF NOT EXISTS auth_failure_count INTEGER DEFAULT 0;
ALTER TABLE site_appliances ADD COLUMN IF NOT EXISTS last_auth_failure TIMESTAMPTZ;
