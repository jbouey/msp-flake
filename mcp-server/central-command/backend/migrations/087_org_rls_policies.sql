-- Migration 087: Org-level RLS policies for client portal
--
-- Adds org_isolation policies to all site-level RLS tables.
-- These policies allow client portal endpoints (which use org_connection)
-- to query data across all sites belonging to their org, while still
-- enforcing tenant isolation at the database layer.
--
-- Works in conjunction with tenant_middleware.org_connection() which sets:
--   app.is_admin = 'false'
--   app.current_org = <org_id>
--
-- The sites table does NOT have RLS, so the subquery is unrestricted.
-- Rollback: DROP POLICY org_isolation ON <table>;

-- ---------- compliance_bundles ----------
DROP POLICY IF EXISTS org_isolation ON compliance_bundles;
CREATE POLICY org_isolation ON compliance_bundles
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- incidents ----------
DROP POLICY IF EXISTS org_isolation ON incidents;
CREATE POLICY org_isolation ON incidents
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- admin_orders ----------
DROP POLICY IF EXISTS org_isolation ON admin_orders;
CREATE POLICY org_isolation ON admin_orders
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- site_credentials ----------
DROP POLICY IF EXISTS org_isolation ON site_credentials;
CREATE POLICY org_isolation ON site_credentials
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- site_appliances ----------
DROP POLICY IF EXISTS org_isolation ON site_appliances;
CREATE POLICY org_isolation ON site_appliances
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- site_notification_overrides ----------
-- site_id is UUID here, cast to text to match sites.site_id (varchar)
DROP POLICY IF EXISTS org_isolation ON site_notification_overrides;
CREATE POLICY org_isolation ON site_notification_overrides
    USING (site_id::text IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- site_drift_config ----------
DROP POLICY IF EXISTS org_isolation ON site_drift_config;
CREATE POLICY org_isolation ON site_drift_config
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- go_agents ----------
DROP POLICY IF EXISTS org_isolation ON go_agents;
CREATE POLICY org_isolation ON go_agents
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- go_agent_checks ----------
DROP POLICY IF EXISTS org_isolation ON go_agent_checks;
CREATE POLICY org_isolation ON go_agent_checks
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- go_agent_orders ----------
DROP POLICY IF EXISTS org_isolation ON go_agent_orders;
CREATE POLICY org_isolation ON go_agent_orders
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- escalation_tickets ----------
-- site_id is UUID here, cast to text to match sites.site_id (varchar)
DROP POLICY IF EXISTS org_isolation ON escalation_tickets;
CREATE POLICY org_isolation ON escalation_tickets
    USING (site_id::text IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- app_protection_profiles ----------
DROP POLICY IF EXISTS org_isolation ON app_protection_profiles;
CREATE POLICY org_isolation ON app_protection_profiles
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- sensor_registry ----------
DROP POLICY IF EXISTS org_isolation ON sensor_registry;
CREATE POLICY org_isolation ON sensor_registry
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- enumeration_results ----------
DROP POLICY IF EXISTS org_isolation ON enumeration_results;
CREATE POLICY org_isolation ON enumeration_results
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- agent_deployments ----------
DROP POLICY IF EXISTS org_isolation ON agent_deployments;
CREATE POLICY org_isolation ON agent_deployments
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- execution_telemetry ----------
DROP POLICY IF EXISTS org_isolation ON execution_telemetry;
CREATE POLICY org_isolation ON execution_telemetry
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- l2_decisions ----------
DROP POLICY IF EXISTS org_isolation ON l2_decisions;
CREATE POLICY org_isolation ON l2_decisions
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- hipaa_sra_assessments ----------
-- Uses org_id (UUID) directly, not site_id
DROP POLICY IF EXISTS org_isolation ON hipaa_sra_assessments;
CREATE POLICY org_isolation ON hipaa_sra_assessments
    USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid);

-- ---------- orders ----------
DROP POLICY IF EXISTS org_isolation ON orders;
CREATE POLICY org_isolation ON orders
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- evidence_bundles ----------
DROP POLICY IF EXISTS org_isolation ON evidence_bundles;
CREATE POLICY org_isolation ON evidence_bundles
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- discovered_devices ----------
DROP POLICY IF EXISTS org_isolation ON discovered_devices;
CREATE POLICY org_isolation ON discovered_devices
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- device_compliance_details ----------
DROP POLICY IF EXISTS org_isolation ON device_compliance_details;
CREATE POLICY org_isolation ON device_compliance_details
    USING (site_id IN (
        SELECT site_id FROM sites
        WHERE client_org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
    ));

-- ---------- fleet_orders ----------
-- No site_id column — fleet_orders are global admin operations, not tenant-scoped

-- ---------- client_escalation_preferences ----------
-- Already has org-level policy from Migration 085 (client_org_id::text = app.current_org)
-- No changes needed.

-- Performance: Add index on sites.client_org_id for the subquery
CREATE INDEX IF NOT EXISTS idx_sites_client_org_id ON sites(client_org_id);
