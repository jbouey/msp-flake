-- Migration: 024_partner_oauth.sql
-- Partner OAuth Authentication (M365 / Google Workspace)
--
-- Enables MSPs to sign up and authenticate using their existing
-- Microsoft Entra ID or Google Workspace identity, eliminating
-- manual API key provisioning.

-- =============================================================================
-- ADD OAUTH FIELDS TO PARTNERS TABLE
-- =============================================================================

-- Auth provider (microsoft, google, or null for API key auth)
ALTER TABLE partners ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(50)
    CHECK (auth_provider IS NULL OR auth_provider IN ('microsoft', 'google', 'api_key'));

-- OAuth subject ID (the unique user ID from the identity provider)
ALTER TABLE partners ADD COLUMN IF NOT EXISTS oauth_subject VARCHAR(255);

-- OAuth tenant ID (Azure AD tenant ID or Google Workspace domain)
ALTER TABLE partners ADD COLUMN IF NOT EXISTS oauth_tenant_id VARCHAR(255);

-- Email from OAuth identity (may differ from contact_email)
ALTER TABLE partners ADD COLUMN IF NOT EXISTS oauth_email VARCHAR(255);

-- Display name from OAuth identity
ALTER TABLE partners ADD COLUMN IF NOT EXISTS oauth_name VARCHAR(255);

-- Encrypted OAuth tokens (for future API access to M365/Google on behalf of partner)
ALTER TABLE partners ADD COLUMN IF NOT EXISTS oauth_access_token_encrypted BYTEA;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS oauth_refresh_token_encrypted BYTEA;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS oauth_token_expires_at TIMESTAMPTZ;

-- Last login timestamp
ALTER TABLE partners ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;

-- Make api_key_hash nullable (OAuth partners won't have one initially)
ALTER TABLE partners ALTER COLUMN api_key_hash DROP NOT NULL;

-- =============================================================================
-- PARTNER SESSIONS TABLE (Cookie-based auth for OAuth partners)
-- =============================================================================
CREATE TABLE IF NOT EXISTS partner_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id UUID NOT NULL REFERENCES partners(id) ON DELETE CASCADE,

    -- Session token (hashed for lookup)
    session_token_hash VARCHAR(255) NOT NULL UNIQUE,

    -- Session metadata
    ip_address INET,
    user_agent TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    last_used_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- INDEXES
-- =============================================================================

-- Unique constraint on OAuth identity (provider + subject)
CREATE UNIQUE INDEX IF NOT EXISTS idx_partners_oauth_identity
ON partners(auth_provider, oauth_subject)
WHERE auth_provider IS NOT NULL AND oauth_subject IS NOT NULL;

-- Index for session lookup
CREATE INDEX IF NOT EXISTS idx_partner_sessions_token ON partner_sessions(session_token_hash);

-- Index for session cleanup
CREATE INDEX IF NOT EXISTS idx_partner_sessions_expires ON partner_sessions(expires_at);

-- Index for partner session lookup
CREATE INDEX IF NOT EXISTS idx_partner_sessions_partner ON partner_sessions(partner_id);

-- =============================================================================
-- CLEANUP FUNCTION FOR EXPIRED SESSIONS
-- =============================================================================
CREATE OR REPLACE FUNCTION cleanup_expired_partner_sessions()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM partner_sessions WHERE expires_at < NOW();
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- COMMENTS
-- =============================================================================
COMMENT ON COLUMN partners.auth_provider IS 'OAuth provider: microsoft, google, or api_key (null = legacy API key auth)';
COMMENT ON COLUMN partners.oauth_subject IS 'Unique user ID from identity provider (Azure AD oid or Google sub)';
COMMENT ON COLUMN partners.oauth_tenant_id IS 'Azure AD tenant ID or Google Workspace domain';
COMMENT ON COLUMN partners.oauth_email IS 'Email address from OAuth identity';
COMMENT ON COLUMN partners.oauth_name IS 'Display name from OAuth identity';
COMMENT ON TABLE partner_sessions IS 'Cookie-based sessions for OAuth-authenticated partners';
