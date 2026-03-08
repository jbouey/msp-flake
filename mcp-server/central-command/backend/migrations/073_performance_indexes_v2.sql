-- Migration 073: Add missing indexes for production scalability
-- These prevent full table scans during appliance checkins and fleet operations

-- site_credentials: queried on every checkin for credential delivery
CREATE INDEX IF NOT EXISTS idx_site_credentials_site_type
    ON site_credentials(site_id, credential_type);

-- admin_orders: queried on every checkin for pending orders
CREATE INDEX IF NOT EXISTS idx_admin_orders_appliance_status
    ON admin_orders(appliance_id, status)
    WHERE status IN ('pending', 'active');

-- go_agents: synced on every checkin
CREATE INDEX IF NOT EXISTS idx_go_agents_site_id
    ON go_agents(site_id);

-- site_appliances: queried for fleet overview and checkin
CREATE INDEX IF NOT EXISTS idx_site_appliances_site_status
    ON site_appliances(site_id, status);

-- api_keys: checked on every appliance auth
CREATE INDEX IF NOT EXISTS idx_api_keys_site_active
    ON api_keys(site_id, active)
    WHERE active = true;

-- incidents: queried frequently with site_id + resolved filters
CREATE INDEX IF NOT EXISTS idx_incidents_site_resolved
    ON incidents(site_id, resolved, created_at DESC);

-- workstation_checks: queried per-workstation on scan summary
CREATE INDEX IF NOT EXISTS idx_workstation_checks_ws_time
    ON workstation_checks(workstation_id, checked_at DESC);
