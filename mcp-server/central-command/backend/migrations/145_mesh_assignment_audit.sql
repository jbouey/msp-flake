-- Migration 145: Mesh assignment audit trail
-- Session 203 enterprise hardening — persistent history of target assignments

CREATE TABLE IF NOT EXISTS mesh_assignment_audit (
    id BIGSERIAL PRIMARY KEY,
    site_id VARCHAR(128) NOT NULL,
    appliance_id VARCHAR(255) NOT NULL,
    appliance_mac VARCHAR(64) NOT NULL,
    assignment_epoch BIGINT NOT NULL,
    ring_size INT NOT NULL,
    ring_members JSONB NOT NULL,
    assigned_targets JSONB NOT NULL,
    target_count INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mesh_audit_site_created
    ON mesh_assignment_audit(site_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_mesh_audit_appliance_created
    ON mesh_assignment_audit(appliance_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_mesh_audit_epoch
    ON mesh_assignment_audit(site_id, assignment_epoch);

-- Retention: delete entries older than 30 days
-- Run via cron or manual: DELETE FROM mesh_assignment_audit WHERE created_at < NOW() - INTERVAL '30 days';

COMMENT ON TABLE mesh_assignment_audit IS 'Immutable history of mesh target assignments. Append-only audit log for answering "who scanned what when".';
