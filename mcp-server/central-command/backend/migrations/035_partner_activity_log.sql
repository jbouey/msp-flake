-- Migration 035: Partner Activity Audit Log
-- HIPAA 164.312(b) - Audit controls for partner access to PHI systems
--
-- Creates append-only partner_activity_log table for tracking all partner
-- actions across OAuth, site management, credentials, provisions, etc.

-- =============================================================================
-- PARTNER ACTIVITY LOG (APPEND-ONLY)
-- =============================================================================

CREATE TABLE IF NOT EXISTS partner_activity_log (
    id BIGSERIAL PRIMARY KEY,
    partner_id UUID NOT NULL,

    -- Event details
    event_type VARCHAR(100) NOT NULL,
    event_category VARCHAR(50) NOT NULL,
    event_data JSONB DEFAULT '{}'::jsonb,

    -- Target resource
    target_type VARCHAR(50),
    target_id TEXT,

    -- Actor information
    actor_ip INET,
    actor_user_agent TEXT,

    -- Request context
    request_path TEXT,
    request_method VARCHAR(10),

    -- Result
    success BOOLEAN DEFAULT true,
    error_message TEXT,

    -- Timestamp (immutable after insert)
    created_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE partner_activity_log IS 'Append-only audit log for partner operations (HIPAA 164.312(b))';

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_partner_activity_partner ON partner_activity_log(partner_id);
CREATE INDEX IF NOT EXISTS idx_partner_activity_type ON partner_activity_log(event_type);
CREATE INDEX IF NOT EXISTS idx_partner_activity_category ON partner_activity_log(event_category);
CREATE INDEX IF NOT EXISTS idx_partner_activity_time ON partner_activity_log(created_at);
CREATE INDEX IF NOT EXISTS idx_partner_activity_target ON partner_activity_log(target_type, target_id);

-- Append-only enforcement: reuse prevent_audit_modification() from migration 015
-- If it doesn't exist yet (fresh install), create it
CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit log is append-only. Modifications are not allowed.';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS partner_activity_log_immutable ON partner_activity_log;
CREATE TRIGGER partner_activity_log_immutable
    BEFORE UPDATE OR DELETE ON partner_activity_log
    FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_modification();

-- =============================================================================
-- EXCEPTION AUDIT LOG (moved from Python code to proper migration)
-- =============================================================================

CREATE TABLE IF NOT EXISTS exception_audit_log (
    id SERIAL PRIMARY KEY,
    exception_id TEXT NOT NULL,
    action TEXT NOT NULL,
    performed_by TEXT NOT NULL,
    performed_at TIMESTAMPTZ DEFAULT NOW(),
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_exception_audit_exception ON exception_audit_log(exception_id);
