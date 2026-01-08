-- Migration: 008_admin_auth.sql
-- Admin Authentication for Central Command Dashboard
--
-- Replaces hardcoded frontend credentials with proper backend auth:
-- - Password hashing with bcrypt
-- - Session tokens for stateless auth
-- - Audit logging for compliance

-- =============================================================================
-- ADMIN USERS TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS admin_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE,
    password_hash VARCHAR(255) NOT NULL,  -- bcrypt hash
    display_name VARCHAR(255),
    role VARCHAR(50) DEFAULT 'admin' CHECK (role IN ('admin', 'operator', 'readonly')),

    -- Session management
    last_login TIMESTAMPTZ,
    failed_login_attempts INTEGER DEFAULT 0,
    locked_until TIMESTAMPTZ,

    -- Status
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'disabled')),

    -- MFA (future)
    mfa_secret VARCHAR(255),
    mfa_enabled BOOLEAN DEFAULT FALSE,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- ADMIN SESSIONS TABLE (for token validation)
-- =============================================================================
CREATE TABLE IF NOT EXISTS admin_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL,  -- SHA-256 of session token

    -- Session info
    ip_address VARCHAR(45),
    user_agent TEXT,

    -- Expiration
    expires_at TIMESTAMPTZ NOT NULL,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- ADMIN AUDIT LOG TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id SERIAL PRIMARY KEY,
    user_id UUID REFERENCES admin_users(id),
    username VARCHAR(100),  -- Denormalized for history
    action VARCHAR(100) NOT NULL,
    target VARCHAR(255),
    details JSONB,
    ip_address VARCHAR(45),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- INDEXES
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_admin_users_username ON admin_users(username);
CREATE INDEX IF NOT EXISTS idx_admin_users_email ON admin_users(email);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_user ON admin_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_token ON admin_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires ON admin_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_admin_audit_user ON admin_audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_admin_audit_action ON admin_audit_log(action);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_log(created_at);

-- =============================================================================
-- SEED: Create default admin user
-- Password: admin (will be hashed by backend on first run)
-- IMPORTANT: Change this password immediately after deployment!
-- =============================================================================
-- Note: The actual password hash will be inserted by the backend on startup
-- because bcrypt hashing should be done in Python, not raw SQL.
-- This comment serves as documentation that a default admin should be created.

-- Cleanup expired sessions (run periodically)
-- DELETE FROM admin_sessions WHERE expires_at < NOW();
