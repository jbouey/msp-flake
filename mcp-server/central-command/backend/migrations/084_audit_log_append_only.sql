-- Migration 084: Add append-only triggers to remaining audit tables
-- HIPAA §164.312(b) — audit log integrity
--
-- prevent_audit_modification() already exists (migration 015).
-- Two tables already have it: integration_audit_log, partner_activity_log.
-- Four tables are missing it.

CREATE TRIGGER update_audit_log_immutable
    BEFORE UPDATE OR DELETE ON update_audit_log
    FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_modification();

CREATE TRIGGER exception_audit_log_immutable
    BEFORE UPDATE OR DELETE ON exception_audit_log
    FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_modification();

CREATE TRIGGER portal_access_log_immutable
    BEFORE UPDATE OR DELETE ON portal_access_log
    FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_modification();

CREATE TRIGGER companion_activity_log_immutable
    BEFORE UPDATE OR DELETE ON companion_activity_log
    FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_modification();
