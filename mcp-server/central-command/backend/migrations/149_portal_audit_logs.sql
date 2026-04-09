-- Migration 149: client_audit_log table
--
-- Session 203 audit finding — client portal (H1) shipped with 50+
-- mutating endpoints and no audit log. HIPAA §164.308(a)(1)(ii)(D)
-- requires audit controls on client actions against PHI-adjacent
-- systems, and §164.528 requires disclosure accounting; this migration
-- creates the persistence layer so every subsequent mutation can be
-- logged.
--
-- NOTE: the partner portal already has an audit log infrastructure
-- (`partner_activity_log` + `partner_activity_logger.py`) — we extend
-- THAT instead of creating a parallel table. The `partner_audit_log`
-- table that was initially part of this migration has been removed
-- from this file; if a previous run created it, migration 150 drops it.
--
-- Schema matches `admin_audit_log` (already in place for the admin
-- dashboard) for consistency — same append-only shape, same trigger
-- preventing mutations, same indexes.

BEGIN;

-- =============================================================================
-- CLIENT AUDIT LOG
-- =============================================================================

CREATE TABLE IF NOT EXISTS client_audit_log (
    id            BIGSERIAL PRIMARY KEY,
    org_id        UUID REFERENCES client_orgs(id) ON DELETE CASCADE,
    actor_user_id UUID, -- FK omitted so magic-link sessions can still log
    actor_email   VARCHAR(255),
    action        VARCHAR(100) NOT NULL,
    target        VARCHAR(255),
    details       JSONB,
    ip_address    VARCHAR(45),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_client_audit_org     ON client_audit_log(org_id);
CREATE INDEX IF NOT EXISTS idx_client_audit_action  ON client_audit_log(action);
CREATE INDEX IF NOT EXISTS idx_client_audit_created ON client_audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_client_audit_target  ON client_audit_log(target);

COMMENT ON TABLE client_audit_log IS
    'Append-only audit trail for client portal mutations. HIPAA §164.308(a)(1)(ii)(D) + §164.528 disclosure accounting. 7-year retention target.';

-- =============================================================================
-- APPEND-ONLY ENFORCEMENT
-- =============================================================================
-- Same trigger approach as admin_audit_log — UPDATE and DELETE are blocked
-- at the row level so a compromised application user cannot silently rewrite
-- history. Only a DB superuser can DROP the trigger, and that action is
-- captured by PostgreSQL's own event log.

CREATE OR REPLACE FUNCTION prevent_portal_audit_log_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit log is append-only — % denied on %', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS enforce_client_audit_append_only ON client_audit_log;
CREATE TRIGGER enforce_client_audit_append_only
    BEFORE UPDATE OR DELETE ON client_audit_log
    FOR EACH ROW EXECUTE FUNCTION prevent_portal_audit_log_mutation();

-- =============================================================================
-- RLS (follows the existing pattern for portal-scoped tables)
-- =============================================================================

ALTER TABLE client_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_audit_log FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS client_audit_admin_bypass ON client_audit_log;
CREATE POLICY client_audit_admin_bypass ON client_audit_log
    FOR ALL
    USING ((current_setting('app.is_admin', true))::boolean = true);

-- Clients read via the app layer which filters WHERE org_id = current_org;
-- tenant_connection() sets app.current_org, so a tenant-scoped SELECT
-- policy could be added later if we want RLS to enforce it at the DB
-- level. For now the write path is admin-scoped and the read path is
-- application-scoped, matching the existing portal pattern.

-- =============================================================================
-- PERMISSIONS
-- =============================================================================

GRANT SELECT, INSERT ON partner_audit_log TO mcp_app;
GRANT SELECT, INSERT ON client_audit_log TO mcp_app;
GRANT USAGE, SELECT ON SEQUENCE partner_audit_log_id_seq TO mcp_app;
GRANT USAGE, SELECT ON SEQUENCE client_audit_log_id_seq TO mcp_app;

COMMIT;
