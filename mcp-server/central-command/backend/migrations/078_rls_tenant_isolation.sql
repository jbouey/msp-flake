-- Migration 078: Row-Level Security + Tenant Isolation
--
-- Verified against VPS production schema 2026-03-10.
-- Only targets tables that EXIST and HAVE the correct tenant column.
--
-- Tables with site_id: 12 tables (direct RLS)
-- Tables with org_id: 5 HIPAA tables (RLS via org_id)
-- Tables needing site_id added: incidents, l2_decisions (denormalize via appliance)
-- Tables NOT targetable yet: orders, evidence_bundles, fleet_orders,
--   device_compliance_details (need schema changes first — Phase 4 P2)
--
-- Rollback: ALTER TABLE <name> DISABLE ROW LEVEL SECURITY; DROP POLICY ...;

-- ============================================================================
-- 0. GUC defaults — prevent errors when current_setting() called on unset vars
-- ============================================================================

-- CRITICAL: app.is_admin defaults to 'true' so existing code that doesn't
-- use tenant_connection() yet continues to work (admin bypass = see all rows).
-- Flip to 'false' once ALL endpoints use tenant_connection().
ALTER DATABASE mcp SET app.current_tenant = '';
ALTER DATABASE mcp SET app.is_admin = 'true';

-- ============================================================================
-- 1. Denormalize site_id onto incidents (via appliances.site_id)
-- ============================================================================

-- incidents.appliance_id is UUID FK to appliances.id
-- appliances has no site_id either — site_appliances has site_id
-- Need: incidents → appliances.id = site_appliances.appliance_id? No.
-- Actually: site_appliances.appliance_id is VARCHAR (like "site123-AABBCCDDEE")
-- and incidents.appliance_id is UUID FK to appliances.id (UUID).
-- The join path is: incidents.appliance_id → appliances.id,
-- but appliances table also may not have site_id.
-- Check: The working compliance-health query in routes.py uses:
--   JOIN appliances a ON a.id = i.appliance_id → a.site_id
-- So appliances DOES have site_id in the query context.
-- But VPS shows appliances doesn't have site_id column either.
-- The actual join used in routes.py is:
--   JOIN site_appliances sa ON sa.appliance_id = i.appliance_id::text
-- Let's add site_id to incidents via site_appliances lookup.

ALTER TABLE incidents ADD COLUMN IF NOT EXISTS site_id VARCHAR(255);

-- Backfill: incidents.appliance_id (UUID) needs to match something in site_appliances.
-- site_appliances.appliance_id is VARCHAR. Try casting.
UPDATE incidents i
SET site_id = sa.site_id
FROM site_appliances sa
WHERE i.appliance_id::text = sa.appliance_id
  AND i.site_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_incidents_site_id ON incidents(site_id);

-- ============================================================================
-- 2. Denormalize site_id onto l2_decisions (via incidents)
-- ============================================================================

ALTER TABLE l2_decisions ADD COLUMN IF NOT EXISTS site_id VARCHAR(255);

-- l2_decisions.incident_id is VARCHAR, incidents.id is UUID
-- The join uses incident_id which may be the UUID as string
UPDATE l2_decisions ld
SET site_id = i.site_id
FROM incidents i
WHERE ld.incident_id = i.id::text
  AND ld.site_id IS NULL
  AND i.site_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_l2_decisions_site_id ON l2_decisions(site_id);

-- ============================================================================
-- 3. Indexes for RLS performance
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_compliance_bundles_site ON compliance_bundles(site_id);
CREATE INDEX IF NOT EXISTS idx_escalation_tickets_site ON escalation_tickets(site_id);
CREATE INDEX IF NOT EXISTS idx_go_agents_site ON go_agents(site_id);
CREATE INDEX IF NOT EXISTS idx_go_agent_checks_site ON go_agent_checks(site_id);
CREATE INDEX IF NOT EXISTS idx_admin_orders_site ON admin_orders(site_id);
CREATE INDEX IF NOT EXISTS idx_hipaa_sra_org ON hipaa_sra_assessments(org_id);
CREATE INDEX IF NOT EXISTS idx_hipaa_policies_org ON hipaa_policies(org_id);
CREATE INDEX IF NOT EXISTS idx_hipaa_training_org ON hipaa_training_records(org_id);
CREATE INDEX IF NOT EXISTS idx_hipaa_baas_org ON hipaa_baas(org_id);
CREATE INDEX IF NOT EXISTS idx_hipaa_documents_org ON hipaa_documents(org_id);

-- ============================================================================
-- 4. RLS on tables with site_id (12 tables)
-- ============================================================================

-- ---------- compliance_bundles ----------
ALTER TABLE compliance_bundles ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance_bundles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON compliance_bundles;
DROP POLICY IF EXISTS admin_bypass ON compliance_bundles;
CREATE POLICY tenant_isolation ON compliance_bundles
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON compliance_bundles
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- incidents (newly has site_id) ----------
ALTER TABLE incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE incidents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON incidents;
DROP POLICY IF EXISTS admin_bypass ON incidents;
CREATE POLICY tenant_isolation ON incidents
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON incidents
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
ALTER TABLE execution_telemetry ENABLE ROW LEVEL SECURITY;
ALTER TABLE execution_telemetry FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON execution_telemetry;
DROP POLICY IF EXISTS admin_bypass ON execution_telemetry;
CREATE POLICY tenant_isolation ON execution_telemetry
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON execution_telemetry
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- l2_decisions (newly has site_id) ----------
ALTER TABLE l2_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE l2_decisions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON l2_decisions;
DROP POLICY IF EXISTS admin_bypass ON l2_decisions;
CREATE POLICY tenant_isolation ON l2_decisions
    USING (site_id = current_setting('app.current_tenant', true));
CREATE POLICY admin_bypass ON l2_decisions
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ============================================================================
-- 5. RLS on HIPAA tables (use org_id instead of site_id)
-- ============================================================================

-- These tables use org_id for tenant scoping (client_orgs).
-- RLS policy uses a separate GUC: app.current_org

ALTER DATABASE mcp SET app.current_org = '';

-- ---------- hipaa_sra_assessments ----------
ALTER TABLE hipaa_sra_assessments ENABLE ROW LEVEL SECURITY;
ALTER TABLE hipaa_sra_assessments FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON hipaa_sra_assessments;
DROP POLICY IF EXISTS admin_bypass ON hipaa_sra_assessments;
CREATE POLICY org_isolation ON hipaa_sra_assessments
    USING (org_id::text = current_setting('app.current_org', true));
CREATE POLICY admin_bypass ON hipaa_sra_assessments
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- hipaa_policies ----------
ALTER TABLE hipaa_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE hipaa_policies FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON hipaa_policies;
DROP POLICY IF EXISTS admin_bypass ON hipaa_policies;
CREATE POLICY org_isolation ON hipaa_policies
    USING (org_id::text = current_setting('app.current_org', true));
CREATE POLICY admin_bypass ON hipaa_policies
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- hipaa_training_records ----------
ALTER TABLE hipaa_training_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE hipaa_training_records FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON hipaa_training_records;
DROP POLICY IF EXISTS admin_bypass ON hipaa_training_records;
CREATE POLICY org_isolation ON hipaa_training_records
    USING (org_id::text = current_setting('app.current_org', true));
CREATE POLICY admin_bypass ON hipaa_training_records
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- hipaa_baas ----------
ALTER TABLE hipaa_baas ENABLE ROW LEVEL SECURITY;
ALTER TABLE hipaa_baas FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON hipaa_baas;
DROP POLICY IF EXISTS admin_bypass ON hipaa_baas;
CREATE POLICY org_isolation ON hipaa_baas
    USING (org_id::text = current_setting('app.current_org', true));
CREATE POLICY admin_bypass ON hipaa_baas
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ---------- hipaa_documents ----------
ALTER TABLE hipaa_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE hipaa_documents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON hipaa_documents;
DROP POLICY IF EXISTS admin_bypass ON hipaa_documents;
CREATE POLICY org_isolation ON hipaa_documents
    USING (org_id::text = current_setting('app.current_org', true));
CREATE POLICY admin_bypass ON hipaa_documents
    USING (current_setting('app.is_admin', true)::boolean = true);

-- ============================================================================
-- 6. Append-Only Trigger on admin_audit_log
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
-- 7. WORM + RLS Interaction (compliance_bundles)
-- ============================================================================

-- The WORM trigger (prevent_compliance_bundle_update) fires BEFORE UPDATE.
-- RLS policies filter rows BEFORE triggers see them.
-- Interaction is SAFE: RLS narrows scope, WORM protects integrity.
-- Admin bypass allows chain metadata repair; evidence content stays immutable.

-- ============================================================================
-- 8. Record migration
-- ============================================================================

INSERT INTO schema_migrations (version, name, applied_at, checksum, execution_time_ms)
VALUES ('078', 'rls_tenant_isolation', NOW(), 'phase4-p1-v2', 0)
ON CONFLICT (version) DO NOTHING;

-- ============================================================================
-- 9. Auto-populate site_id triggers (prevent NULL site_id on new rows)
-- ============================================================================

-- incidents: look up site_id from appliances table on INSERT
CREATE OR REPLACE FUNCTION set_incident_site_id()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.site_id IS NULL AND NEW.appliance_id IS NOT NULL THEN
        SELECT site_id INTO NEW.site_id
        FROM appliances WHERE id = NEW.appliance_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS auto_set_incident_site_id ON incidents;
CREATE TRIGGER auto_set_incident_site_id
    BEFORE INSERT ON incidents
    FOR EACH ROW
    EXECUTE FUNCTION set_incident_site_id();

-- l2_decisions: look up site_id from incidents table on INSERT
CREATE OR REPLACE FUNCTION set_l2_decision_site_id()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.site_id IS NULL AND NEW.incident_id IS NOT NULL THEN
        SELECT site_id INTO NEW.site_id
        FROM incidents WHERE id::text = NEW.incident_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS auto_set_l2_decision_site_id ON l2_decisions;
CREATE TRIGGER auto_set_l2_decision_site_id
    BEFORE INSERT ON l2_decisions
    FOR EACH ROW
    EXECUTE FUNCTION set_l2_decision_site_id();

-- ============================================================================
-- Phase 4 P2 TODO (not in this migration):
-- - Add site_id to: orders, evidence_bundles, fleet_orders, device_compliance_details
-- - Enable RLS on: orders, evidence_bundles, fleet_orders, device_compliance_details
-- - Create tables: compliance_snapshots, compliance_results, site_kpis, portal_access_log
--   (from migration 001 — may need to be re-run or tables created fresh)
-- - PgBouncer deployment on VPS (config in pgbouncer/ directory)
-- - Migrate endpoints to use tenant_connection() and flip app.is_admin default to false
-- ============================================================================
