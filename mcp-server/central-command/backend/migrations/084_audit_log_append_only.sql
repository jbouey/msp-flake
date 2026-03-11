-- Migration 084: Add append-only triggers to remaining audit tables
-- HIPAA §164.312(b) — audit log integrity
--
-- prevent_audit_modification() already exists (migration 015).
-- Two tables already have it: integration_audit_log, partner_activity_log.
-- Four tables are missing it.

-- Ensure portal_access_log exists (migration 001 may not have run fully)
CREATE TABLE IF NOT EXISTS portal_access_log (
    id SERIAL PRIMARY KEY,
    site_id VARCHAR(50) REFERENCES sites(site_id) ON DELETE CASCADE,
    accessed_at TIMESTAMP DEFAULT NOW(),
    ip_address VARCHAR(45),
    user_agent TEXT,
    endpoint VARCHAR(256)
);
CREATE INDEX IF NOT EXISTS idx_portal_access_site ON portal_access_log(site_id, accessed_at DESC);

DO $$ BEGIN
    CREATE TRIGGER update_audit_log_immutable
        BEFORE UPDATE OR DELETE ON update_audit_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TRIGGER exception_audit_log_immutable
        BEFORE UPDATE OR DELETE ON exception_audit_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TRIGGER portal_access_log_immutable
        BEFORE UPDATE OR DELETE ON portal_access_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TRIGGER companion_activity_log_immutable
        BEFORE UPDATE OR DELETE ON companion_activity_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
