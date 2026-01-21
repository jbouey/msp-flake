-- Migration: 023_oauth_login.sql
-- OAuth Login Support for Google and Microsoft
-- Created: 2026-01-21

-- =============================================================================
-- OAUTH IDENTITIES TABLE (links OAuth providers to admin users)
-- =============================================================================
CREATE TABLE IF NOT EXISTS admin_oauth_identities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,

    -- OAuth Provider Info
    provider VARCHAR(50) NOT NULL CHECK (provider IN ('google', 'microsoft')),
    provider_user_id VARCHAR(255) NOT NULL,  -- 'sub' claim from ID token
    provider_email VARCHAR(255) NOT NULL,    -- Email from provider

    -- Additional Profile Data (from ID token)
    provider_name VARCHAR(255),              -- Display name from provider
    provider_picture_url TEXT,               -- Profile picture URL

    -- Metadata
    linked_at TIMESTAMPTZ DEFAULT NOW(),
    last_login_at TIMESTAMPTZ,

    -- Constraints
    UNIQUE (user_id, provider),              -- One identity per provider per user
    UNIQUE (provider, provider_user_id)      -- One user per provider identity
);

-- =============================================================================
-- OAUTH CONFIGURATION TABLE (global settings per provider)
-- =============================================================================
CREATE TABLE IF NOT EXISTS oauth_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider VARCHAR(50) NOT NULL UNIQUE CHECK (provider IN ('google', 'microsoft')),

    -- OAuth App Credentials (client_secret is encrypted)
    client_id VARCHAR(255) NOT NULL,
    client_secret_encrypted BYTEA NOT NULL,

    -- Provider-specific config
    tenant_id VARCHAR(255),  -- For Microsoft: Azure AD tenant ID ('common' for multi-tenant)

    -- Feature Flags
    enabled BOOLEAN DEFAULT FALSE,
    allow_registration BOOLEAN DEFAULT TRUE,   -- Allow new users to register via OAuth
    default_role VARCHAR(50) DEFAULT 'readonly' CHECK (default_role IN ('admin', 'operator', 'readonly')),
    require_admin_approval BOOLEAN DEFAULT TRUE,  -- New OAuth users need admin approval

    -- Domain Restriction (empty array = allow all)
    allowed_domains TEXT[] DEFAULT '{}',  -- e.g., ARRAY['company.com', 'contractor.company.com']

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- MODIFY ADMIN_USERS TABLE (add OAuth-related columns)
-- =============================================================================
ALTER TABLE admin_users
    ADD COLUMN IF NOT EXISTS pending_approval BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS approved_by UUID REFERENCES admin_users(id),
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;

-- =============================================================================
-- INDEXES
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_oauth_identities_user ON admin_oauth_identities(user_id);
CREATE INDEX IF NOT EXISTS idx_oauth_identities_provider ON admin_oauth_identities(provider, provider_user_id);
CREATE INDEX IF NOT EXISTS idx_oauth_identities_email ON admin_oauth_identities(provider_email);
CREATE INDEX IF NOT EXISTS idx_admin_users_pending ON admin_users(pending_approval) WHERE pending_approval = TRUE;

-- =============================================================================
-- TRIGGER: Update oauth_config.updated_at on modification
-- =============================================================================
CREATE OR REPLACE FUNCTION update_oauth_config_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS oauth_config_updated ON oauth_config;
CREATE TRIGGER oauth_config_updated
    BEFORE UPDATE ON oauth_config
    FOR EACH ROW EXECUTE FUNCTION update_oauth_config_timestamp();

-- =============================================================================
-- AUDIT LOG EVENTS (add OAuth-specific event types)
-- =============================================================================
-- The admin_audit_log table already exists with flexible JSONB details field
-- OAuth events will use these action types:
-- - OAUTH_LOGIN_INITIATED
-- - OAUTH_LOGIN_SUCCESS
-- - OAUTH_LOGIN_FAILED
-- - OAUTH_ACCOUNT_LINKED
-- - OAUTH_ACCOUNT_UNLINKED
-- - OAUTH_USER_CREATED
-- - OAUTH_USER_APPROVED
-- - OAUTH_USER_REJECTED
-- - OAUTH_CONFIG_UPDATED

-- =============================================================================
-- SEED DEFAULT CONFIG (disabled by default)
-- =============================================================================
INSERT INTO oauth_config (provider, client_id, client_secret_encrypted, enabled, require_admin_approval, default_role)
VALUES
    ('google', 'not-configured', E'\\x00', FALSE, TRUE, 'readonly'),
    ('microsoft', 'not-configured', E'\\x00', FALSE, TRUE, 'readonly')
ON CONFLICT (provider) DO NOTHING;

COMMENT ON TABLE admin_oauth_identities IS 'Links OAuth provider identities to admin user accounts';
COMMENT ON TABLE oauth_config IS 'OAuth provider configuration (Google, Microsoft) with feature flags';
COMMENT ON COLUMN oauth_config.allowed_domains IS 'Email domains allowed for OAuth login (empty = all domains allowed)';
COMMENT ON COLUMN oauth_config.require_admin_approval IS 'If true, new OAuth users are created with pending_approval=true';
