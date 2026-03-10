-- Migration 078: Row-Level Security + Tenant Isolation
--
-- Priority 1 of database hardening plan:
-- 1. Enable RLS on all multi-tenant tables
-- 2. Create tenant isolation policies using current_setting('app.current_tenant')
-- 3. Admin bypass via current_setting('app.is_admin')
-- 4. Add site_id to l2_decisions (backfill from incidents)
-- 5. Append-only trigger on admin_audit_log
--
-- Middleware sets SET LOCAL app.current_tenant = '<site_id>' per transaction.
-- FORCE ROW LEVEL SECURITY ensures policies apply even to table owner (mcp user).
--
-- Rollback: ALTER TABLE <name> DISABLE ROW LEVEL SECURITY; DROP POLICY IF EXISTS ...;

-- ============================================================================
-- 0. Ensure GUC defaults exist (prevents errors on unset settings)
-- ============================================================================

-- Set defaults so current_setting() doesn't error when not set.
-- CRITICAL: app.is_admin defaults to 'true' so existing code that doesn't
-- use tenant_connection() yet continues to work (admin bypass = see all rows).
-- As endpoints are migrated to use tenant_connection(), they explicitly set
-- app.is_admin = 'false' + app.current_tenant = '<site_id>'.
-- Once ALL endpoints are migrated, flip this default to 'false'.
ALTER DATABASE mcp SET app.current_tenant = '';
ALTER DATABASE mcp SET app.is_admin = 'true';

-- ============================================================================
-- 1. Add site_id to l2_decisions
-- ============================================================================

ALTER TABLE l2_decisions ADD COLUMN IF NOT EXISTS site_id VARCHAR(255);

-- Backfill site_id from incidents table (incidents already has site_id)
UPDATE l2_decisions ld
SET site_id = i.site_id
FROM incidents i
WHERE ld.incident_id = i.incident_id
  AND ld.site_id IS NULL;

-- Index for RLS performance
CREATE INDEX IF NOT EXISTS idx_l2_decisions_site_id ON l2_decisions(site_id);

-- ============================================================================
-- 2. Ensure site_id indexes exist on all RLS target tables
-- ============================================================================

-- These are idempotent — IF NOT EXISTS prevents errors on existing indexes
CREATE INDEX IF NOT EXISTS idx_compliance_bundles_site ON compliance_bundles(site_id);
CREATE INDEX IF NOT EXISTS idx_orders_site ON orders(site_id);
CREATE INDEX IF NOT EXISTS idx_site_credentials_site ON site_credentials(site_id);
CREATE INDEX IF NOT EXISTS idx_evidence_bundles_site ON evidence_bundles(site_id);
CREATE INDEX IF NOT EXISTS idx_escalation_tickets_site ON escalation_tickets(site_id);
CREATE INDEX IF NOT EXISTS idx_go_agents_site ON go_agents(site_id);
CREATE INDEX IF NOT EXISTS idx_go_agent_checks_site ON go_agent_checks(site_id);
CREATE INDEX IF NOT EXISTS idx_device_compliance_details_site ON device_compliance_details(site_id);
CREATE INDEX IF NOT EXISTS idx_admin_orders_site ON admin_orders(site_id);

-- ============================================================================
-- 3. Enable RLS + Create Policies on Multi-Tenant Tables
-- ============================================================================

-- Helper: reusable policy creation function
-- Each table gets two policies:
--   1. tenant_isolation: USING (site_id = current_setting('app.current_tenant'))
--   2. admin_bypass: USING (current_setting('app.is_admin', true)::boolean = true)

-- ---------- compliance_snapshots ----------
ALTER TABLE compliance_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance_snapshots FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON compliance_snapshots;
DROP POLICY IF EXISTS admin_bypass ON compliance_snapshots;
CREATE POLICY tenant_isolation ON compliance_snapshots
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON compliance_snapshots
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- compliance_results ----------
ALTER TABLE compliance_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance_results FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON compliance_results;
DROP POLICY IF EXISTS admin_bypass ON compliance_results;
CREATE POLICY tenant_isolation ON compliance_results
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON compliance_results
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- evidence_bundles ----------
ALTER TABLE evidence_bundles ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_bundles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON evidence_bundles;
DROP POLICY IF EXISTS admin_bypass ON evidence_bundles;
CREATE POLICY tenant_isolation ON evidence_bundles
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON evidence_bundles
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- site_kpis ----------
ALTER TABLE site_kpis ENABLE ROW LEVEL SECURITY;
ALTER TABLE site_kpis FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON site_kpis;
DROP POLICY IF EXISTS admin_bypass ON site_kpis;
CREATE POLICY tenant_isolation ON site_kpis
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON site_kpis
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- portal_access_log ----------
ALTER TABLE portal_access_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE portal_access_log FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON portal_access_log;
DROP POLICY IF EXISTS admin_bypass ON portal_access_log;
CREATE POLICY tenant_isolation ON portal_access_log
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON portal_access_log
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- compliance_bundles ----------
ALTER TABLE compliance_bundles ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance_bundles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON compliance_bundles;
DROP POLICY IF EXISTS admin_bypass ON compliance_bundles;
CREATE POLICY tenant_isolation ON compliance_bundles
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON compliance_bundles
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- incidents ----------
ALTER TABLE incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE incidents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON incidents;
DROP POLICY IF EXISTS admin_bypass ON incidents;
CREATE POLICY tenant_isolation ON incidents
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON incidents
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- orders ----------
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON orders;
DROP POLICY IF EXISTS admin_bypass ON orders;
CREATE POLICY tenant_isolation ON orders
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON orders
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- admin_orders ----------
ALTER TABLE admin_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_orders FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON admin_orders;
DROP POLICY IF EXISTS admin_bypass ON admin_orders;
CREATE POLICY tenant_isolation ON admin_orders
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON admin_orders
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- fleet_orders ----------
ALTER TABLE fleet_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE fleet_orders FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON fleet_orders;
DROP POLICY IF EXISTS admin_bypass ON fleet_orders;
-- fleet_orders uses site_id but some are fleet-wide (NULL site_id)
CREATE POLICY tenant_isolation ON fleet_orders
    USING (site_id IS NULL OR site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON fleet_orders
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- site_credentials ----------
ALTER TABLE site_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE site_credentials FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON site_credentials;
DROP POLICY IF EXISTS admin_bypass ON site_credentials;
CREATE POLICY tenant_isolation ON site_credentials
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON site_credentials
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- site_appliances ----------
ALTER TABLE site_appliances ENABLE ROW LEVEL SECURITY;
ALTER TABLE site_appliances FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON site_appliances;
DROP POLICY IF EXISTS admin_bypass ON site_appliances;
CREATE POLICY tenant_isolation ON site_appliances
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON site_appliances
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- site_notification_overrides ----------
ALTER TABLE site_notification_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE site_notification_overrides FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON site_notification_overrides;
DROP POLICY IF EXISTS admin_bypass ON site_notification_overrides;
CREATE POLICY tenant_isolation ON site_notification_overrides
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON site_notification_overrides
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- site_drift_config ----------
ALTER TABLE site_drift_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE site_drift_config FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON site_drift_config;
DROP POLICY IF EXISTS admin_bypass ON site_drift_config;
-- site_drift_config has '__defaults__' rows that are global
CREATE POLICY tenant_isolation ON site_drift_config
    USING (site_id = '__defaults__' OR site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON site_drift_config
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- go_agents ----------
ALTER TABLE go_agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE go_agents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON go_agents;
DROP POLICY IF EXISTS admin_bypass ON go_agents;
CREATE POLICY tenant_isolation ON go_agents
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON go_agents
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- go_agent_checks ----------
ALTER TABLE go_agent_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE go_agent_checks FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON go_agent_checks;
DROP POLICY IF EXISTS admin_bypass ON go_agent_checks;
CREATE POLICY tenant_isolation ON go_agent_checks
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON go_agent_checks
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- go_agent_orders ----------
ALTER TABLE go_agent_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE go_agent_orders FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON go_agent_orders;
DROP POLICY IF EXISTS admin_bypass ON go_agent_orders;
CREATE POLICY tenant_isolation ON go_agent_orders
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON go_agent_orders
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- escalation_tickets ----------
ALTER TABLE escalation_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE escalation_tickets FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON escalation_tickets;
DROP POLICY IF EXISTS admin_bypass ON escalation_tickets;
CREATE POLICY tenant_isolation ON escalation_tickets
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON escalation_tickets
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- app_protection_profiles ----------
ALTER TABLE app_protection_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_protection_profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON app_protection_profiles;
DROP POLICY IF EXISTS admin_bypass ON app_protection_profiles;
CREATE POLICY tenant_isolation ON app_protection_profiles
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON app_protection_profiles
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- device_compliance_details ----------
ALTER TABLE device_compliance_details ENABLE ROW LEVEL SECURITY;
ALTER TABLE device_compliance_details FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON device_compliance_details;
DROP POLICY IF EXISTS admin_bypass ON device_compliance_details;
CREATE POLICY tenant_isolation ON device_compliance_details
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON device_compliance_details
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- l2_decisions ----------
ALTER TABLE l2_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE l2_decisions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON l2_decisions;
DROP POLICY IF EXISTS admin_bypass ON l2_decisions;
-- site_id may be NULL for legacy rows without backfill match
CREATE POLICY tenant_isolation ON l2_decisions
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON l2_decisions
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- sensor_registry ----------
ALTER TABLE sensor_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE sensor_registry FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON sensor_registry;
DROP POLICY IF EXISTS admin_bypass ON sensor_registry;
CREATE POLICY tenant_isolation ON sensor_registry
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON sensor_registry
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- enumeration_results ----------
ALTER TABLE enumeration_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE enumeration_results FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON enumeration_results;
DROP POLICY IF EXISTS admin_bypass ON enumeration_results;
CREATE POLICY tenant_isolation ON enumeration_results
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON enumeration_results
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- agent_deployments ----------
ALTER TABLE agent_deployments ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_deployments FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON agent_deployments;
DROP POLICY IF EXISTS admin_bypass ON agent_deployments;
CREATE POLICY tenant_isolation ON agent_deployments
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON agent_deployments
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- execution_telemetry ----------
-- execution_telemetry has site_id column (added in migration 052)
ALTER TABLE execution_telemetry ENABLE ROW LEVEL SECURITY;
ALTER TABLE execution_telemetry FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON execution_telemetry;
DROP POLICY IF EXISTS admin_bypass ON execution_telemetry;
CREATE POLICY tenant_isolation ON execution_telemetry
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON execution_telemetry
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- hipaa_sra_assessments ----------
ALTER TABLE hipaa_sra_assessments ENABLE ROW LEVEL SECURITY;
ALTER TABLE hipaa_sra_assessments FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON hipaa_sra_assessments;
DROP POLICY IF EXISTS admin_bypass ON hipaa_sra_assessments;
CREATE POLICY tenant_isolation ON hipaa_sra_assessments
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON hipaa_sra_assessments
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- hipaa_policies ----------
ALTER TABLE hipaa_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE hipaa_policies FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON hipaa_policies;
DROP POLICY IF EXISTS admin_bypass ON hipaa_policies;
CREATE POLICY tenant_isolation ON hipaa_policies
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON hipaa_policies
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- hipaa_training_records ----------
ALTER TABLE hipaa_training_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE hipaa_training_records FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON hipaa_training_records;
DROP POLICY IF EXISTS admin_bypass ON hipaa_training_records;
CREATE POLICY tenant_isolation ON hipaa_training_records
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON hipaa_training_records
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- hipaa_baas ----------
ALTER TABLE hipaa_baas ENABLE ROW LEVEL SECURITY;
ALTER TABLE hipaa_baas FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON hipaa_baas;
DROP POLICY IF EXISTS admin_bypass ON hipaa_baas;
CREATE POLICY tenant_isolation ON hipaa_baas
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON hipaa_baas
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- hipaa_documents ----------
ALTER TABLE hipaa_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE hipaa_documents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON hipaa_documents;
DROP POLICY IF EXISTS admin_bypass ON hipaa_documents;
CREATE POLICY tenant_isolation ON hipaa_documents
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON hipaa_documents
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ============================================================================
-- 4. Append-Only Trigger on admin_audit_log
-- ============================================================================

CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'admin_audit_log is append-only. UPDATE not permitted.';
    ELSIF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'admin_audit_log is append-only. DELETE not permitted.';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS enforce_audit_append_only ON admin_audit_log;
CREATE TRIGGER enforce_audit_append_only
    BEFORE UPDATE OR DELETE ON admin_audit_log
    FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_log_mutation();

-- ============================================================================
-- 5. WORM + RLS Interaction Verification
-- ============================================================================

-- The WORM trigger (prevent_compliance_bundle_update) fires BEFORE UPDATE.
-- RLS policies filter rows BEFORE triggers see them.
-- This means:
--   - A tenant can only see their own bundles (RLS filters first)
--   - If they try to UPDATE evidence content, WORM trigger blocks it
--   - Admin bypass allows chain metadata repair (prev_hash, chain_position)
--   - Evidence content (checks, bundle_hash, signature) remains immutable for ALL roles
-- This interaction is SAFE: RLS narrows scope, WORM protects integrity.

-- ============================================================================
-- 6. Record migration
-- ============================================================================

INSERT INTO schema_migrations (version, description, applied_at)
VALUES (78, 'RLS tenant isolation + audit append-only trigger', NOW())
ON CONFLICT (version) DO NOTHING;
