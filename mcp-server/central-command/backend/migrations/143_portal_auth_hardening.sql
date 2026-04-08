-- Migration 143: Portal Auth Hardening
-- Add account lockout columns to partner and client tables (matches admin_users pattern)
-- Add unique constraint on pending_alerts to prevent duplicate alerts

-- Partner lockout columns
ALTER TABLE partners
    ADD COLUMN IF NOT EXISTS failed_login_attempts INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;

-- Client lockout columns
ALTER TABLE client_users
    ADD COLUMN IF NOT EXISTS failed_login_attempts INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;

-- Pending alerts dedup: prevent duplicate alerts for same incident
CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_alerts_incident_dedup
    ON pending_alerts (org_id, incident_id)
    WHERE incident_id IS NOT NULL AND dismissed_at IS NULL;
