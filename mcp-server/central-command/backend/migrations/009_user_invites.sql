-- Migration: 009_user_invites.sql
-- User invitation system for RBAC user management
-- Created: 2026-01-08

-- =============================================================================
-- USER INVITES TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS admin_user_invites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL CHECK (role IN ('admin', 'operator', 'readonly')),
    display_name VARCHAR(255),

    -- Invite token (hashed, never store plaintext)
    token_hash VARCHAR(255) NOT NULL,

    -- Invite metadata
    invited_by UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    invited_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,

    -- Status tracking
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'expired', 'revoked')),
    accepted_at TIMESTAMPTZ,
    accepted_user_id UUID REFERENCES admin_users(id) ON DELETE SET NULL,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- Prevent duplicate pending invites for same email
    CONSTRAINT unique_pending_invite UNIQUE (email, status)
        DEFERRABLE INITIALLY DEFERRED
);

-- =============================================================================
-- INDEXES
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_admin_invites_email ON admin_user_invites(email);
CREATE INDEX IF NOT EXISTS idx_admin_invites_token ON admin_user_invites(token_hash);
CREATE INDEX IF NOT EXISTS idx_admin_invites_status ON admin_user_invites(status);
CREATE INDEX IF NOT EXISTS idx_admin_invites_expires ON admin_user_invites(expires_at);

-- =============================================================================
-- CLEANUP FUNCTION FOR EXPIRED INVITES
-- =============================================================================
CREATE OR REPLACE FUNCTION cleanup_expired_invites()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    UPDATE admin_user_invites
    SET status = 'expired'
    WHERE status = 'pending'
      AND expires_at < NOW();

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- COMMENTS
-- =============================================================================
COMMENT ON TABLE admin_user_invites IS 'Stores user invitation tokens for email-based onboarding';
COMMENT ON COLUMN admin_user_invites.token_hash IS 'SHA-256 hash of the invite token (plaintext never stored)';
COMMENT ON COLUMN admin_user_invites.expires_at IS 'Invite expires after 7 days by default';
