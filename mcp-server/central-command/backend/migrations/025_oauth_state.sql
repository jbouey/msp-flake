-- Migration: 025_oauth_state.sql
-- Separate OAuth State Storage
--
-- The partner_auth.py was trying to store OAuth state in partner_sessions
-- with partner_id=NULL, but that column has a NOT NULL constraint.
-- This creates a dedicated table for OAuth state tokens.

-- =============================================================================
-- OAUTH STATE TABLE (for PKCE flow state storage)
-- =============================================================================
CREATE TABLE IF NOT EXISTS oauth_partner_state (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    state_token VARCHAR(255) NOT NULL UNIQUE,
    provider VARCHAR(50) NOT NULL,
    code_verifier TEXT NOT NULL,
    redirect_after TEXT DEFAULT '/partner/dashboard',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- Index for state lookup
CREATE INDEX IF NOT EXISTS idx_oauth_partner_state_token ON oauth_partner_state(state_token);

-- Index for cleanup of expired states
CREATE INDEX IF NOT EXISTS idx_oauth_partner_state_expires ON oauth_partner_state(expires_at);

-- =============================================================================
-- CLEANUP FUNCTION FOR EXPIRED STATE TOKENS
-- =============================================================================
CREATE OR REPLACE FUNCTION cleanup_expired_oauth_state()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM oauth_partner_state WHERE expires_at < NOW();
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- COMMENTS
-- =============================================================================
COMMENT ON TABLE oauth_partner_state IS 'Temporary storage for OAuth PKCE state during authentication flow';
COMMENT ON COLUMN oauth_partner_state.state_token IS 'Random state token sent to OAuth provider';
COMMENT ON COLUMN oauth_partner_state.code_verifier IS 'PKCE code verifier for S256 challenge';
