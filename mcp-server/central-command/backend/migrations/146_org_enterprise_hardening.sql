-- Migration 146: Organization enterprise hardening (Session 203)
-- - Enable RLS on client_orgs itself (was missing)
-- - Add quota columns (sites, users, incidents)
-- - Add BAA effective/expiration dates
-- - Add deprovisioning state fields
-- - Add org_audit_log append-only table
-- - Add indexes for cross-org search

-- ============================================================================
-- Quota columns
-- ============================================================================
ALTER TABLE client_orgs
    ADD COLUMN IF NOT EXISTS max_sites INT DEFAULT 100,
    ADD COLUMN IF NOT EXISTS max_users INT DEFAULT 50,
    ADD COLUMN IF NOT EXISTS max_incidents_per_day INT DEFAULT 10000;

-- ============================================================================
-- BAA fields (the ones still missing)
-- ============================================================================
ALTER TABLE client_orgs
    ADD COLUMN IF NOT EXISTS baa_effective_date DATE,
    ADD COLUMN IF NOT EXISTS baa_expiration_date DATE;

-- ============================================================================
-- Deprovisioning fields
-- ============================================================================
ALTER TABLE client_orgs
    ADD COLUMN IF NOT EXISTS deprovisioned_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS deprovisioned_by VARCHAR(255),
    ADD COLUMN IF NOT EXISTS deprovision_reason TEXT,
    ADD COLUMN IF NOT EXISTS data_export_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS data_retention_until DATE;

-- ============================================================================
-- Enable RLS on client_orgs itself (was missing!)
-- ============================================================================
ALTER TABLE client_orgs ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_orgs FORCE ROW LEVEL SECURITY;

-- Admin bypass (app.is_admin = 'true')
DROP POLICY IF EXISTS client_orgs_admin_bypass ON client_orgs;
CREATE POLICY client_orgs_admin_bypass ON client_orgs
    FOR ALL
    USING (current_setting('app.is_admin', true)::boolean = true);

-- Org-scoped users see only their own org
DROP POLICY IF EXISTS client_orgs_self_read ON client_orgs;
CREATE POLICY client_orgs_self_read ON client_orgs
    FOR SELECT
    USING (id::text = current_setting('app.current_org', true));

-- Partner can see orgs they manage (via current_partner_id)
DROP POLICY IF EXISTS client_orgs_partner_read ON client_orgs;
CREATE POLICY client_orgs_partner_read ON client_orgs
    FOR SELECT
    USING (
        current_partner_id::text = current_setting('app.current_partner', true)
    );

-- ============================================================================
-- Append-only org audit log (separate from client_org_audit_log which is legacy)
-- ============================================================================
CREATE TABLE IF NOT EXISTS org_audit_log (
    id BIGSERIAL PRIMARY KEY,
    org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE CASCADE,
    event_type VARCHAR(64) NOT NULL,  -- 'provisioned', 'deprovisioned', 'quota_exceeded',
                                       -- 'sso_enforced', 'impersonation', 'data_exported', etc.
    actor VARCHAR(255),                -- username/partner_id
    actor_type VARCHAR(32),            -- 'admin', 'partner', 'system', 'client'
    target VARCHAR(255),               -- object affected (site_id, user_id, etc.)
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip_address INET,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_org_audit_org_created
    ON org_audit_log(org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_org_audit_event_type
    ON org_audit_log(event_type, created_at DESC);

COMMENT ON TABLE org_audit_log IS
    'Append-only org-level audit trail. HIPAA 6-year retention. Never DELETE rows.';

-- ============================================================================
-- Indexes for cross-org search (admin UI)
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_client_orgs_name_trgm
    ON client_orgs USING gin (name gin_trgm_ops);

-- Fallback if pg_trgm is not installed — basic lower(name) index
CREATE INDEX IF NOT EXISTS idx_client_orgs_name_lower
    ON client_orgs(lower(name));

CREATE INDEX IF NOT EXISTS idx_client_orgs_status
    ON client_orgs(status) WHERE status != 'deprovisioned';

-- ============================================================================
-- BAA expiration alert helper index
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_client_orgs_baa_expiration
    ON client_orgs(baa_expiration_date)
    WHERE baa_expiration_date IS NOT NULL;

COMMENT ON COLUMN client_orgs.max_sites IS 'Quota: max number of sites per org (0 = unlimited)';
COMMENT ON COLUMN client_orgs.max_users IS 'Quota: max number of client_users per org';
COMMENT ON COLUMN client_orgs.max_incidents_per_day IS 'Quota: max new incidents per 24h window';
COMMENT ON COLUMN client_orgs.deprovisioned_at IS 'Soft-delete timestamp. Non-null = org offline, data preserved for retention period.';
COMMENT ON COLUMN client_orgs.data_retention_until IS 'HIPAA retention end date. After this, hard delete is permitted.';
