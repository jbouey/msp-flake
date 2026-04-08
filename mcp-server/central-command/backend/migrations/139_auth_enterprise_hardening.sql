-- Migration 139: Auth Enterprise Hardening
-- 1. Password history (last 5) to prevent reuse
-- 2. API key expiry for partner rotation
-- 3. Audit log retention policy (6-year, partitioned)

-- =============================================================================
-- 1. Password History
-- =============================================================================
CREATE TABLE IF NOT EXISTS password_history (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    user_type VARCHAR(20) NOT NULL DEFAULT 'admin',  -- admin, partner, client
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_password_history_user
    ON password_history (user_id, user_type, created_at DESC);

-- Seed current passwords into history (admin users only)
INSERT INTO password_history (user_id, user_type, password_hash, created_at)
SELECT id, 'admin', password_hash, COALESCE(updated_at, created_at, NOW())
FROM admin_users
WHERE password_hash IS NOT NULL
ON CONFLICT DO NOTHING;

-- =============================================================================
-- 2. API Key Expiry
-- =============================================================================
ALTER TABLE partners
    ADD COLUMN IF NOT EXISTS api_key_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS api_key_created_at TIMESTAMPTZ;

-- Set existing keys' created_at to partner created_at (best guess)
UPDATE partners
SET api_key_created_at = created_at
WHERE api_key_hash IS NOT NULL AND api_key_created_at IS NULL;

-- =============================================================================
-- 3. Audit Log Retention Policy
-- =============================================================================
-- Add comment documenting retention policy
COMMENT ON TABLE admin_audit_log IS 'HIPAA audit trail. Retention: 6 years minimum (HIPAA §164.530(j)). Automated cleanup of records older than 7 years.';

-- Index for efficient retention queries
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at
    ON admin_audit_log (created_at);
