-- HIPAA §164.312(a)(2)(iii) — Automatic logoff after inactivity
-- Add last_activity_at to admin_sessions (partner_sessions and client_sessions already have it)

ALTER TABLE admin_sessions
ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ DEFAULT NOW();

-- Backfill existing sessions
UPDATE admin_sessions SET last_activity_at = created_at WHERE last_activity_at IS NULL;

-- Index for cleanup queries
CREATE INDEX IF NOT EXISTS idx_admin_sessions_last_activity ON admin_sessions(last_activity_at);
CREATE INDEX IF NOT EXISTS idx_partner_sessions_last_used ON partner_sessions(last_used_at);
CREATE INDEX IF NOT EXISTS idx_client_sessions_last_activity ON client_sessions(last_activity_at);
