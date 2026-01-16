-- Zero-friction deployment support
-- Migration: 020_zero_friction.sql
-- Created: 2026-01-16
-- Purpose: Support automatic domain discovery, AD enumeration, and agent deployment

-- Add domain discovery fields to sites
ALTER TABLE sites ADD COLUMN IF NOT EXISTS discovered_domain JSONB;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_discovery_at TIMESTAMPTZ;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS awaiting_credentials BOOLEAN DEFAULT false;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS credentials_submitted_at TIMESTAMPTZ;

-- Add trigger flags to site_appliances (appliances table)
ALTER TABLE site_appliances ADD COLUMN IF NOT EXISTS trigger_enumeration BOOLEAN DEFAULT false;
ALTER TABLE site_appliances ADD COLUMN IF NOT EXISTS trigger_immediate_scan BOOLEAN DEFAULT false;

-- Enumeration results table
CREATE TABLE IF NOT EXISTS enumeration_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id VARCHAR(255) NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    appliance_id VARCHAR(255) NOT NULL,
    enumeration_time TIMESTAMPTZ NOT NULL,
    total_servers INT DEFAULT 0,
    total_workstations INT DEFAULT 0,
    reachable_servers INT DEFAULT 0,
    reachable_workstations INT DEFAULT 0,
    results_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT fk_site FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE
);

-- Agent deployment tracking
CREATE TABLE IF NOT EXISTS agent_deployments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id VARCHAR(255) NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    hostname VARCHAR(255) NOT NULL,
    deployment_method VARCHAR(50), -- winrm, gpo, manual
    agent_version VARCHAR(50),
    success BOOLEAN,
    error_message TEXT,
    deployed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT fk_site_deployment FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE,
    UNIQUE(site_id, hostname)
);

-- Indexes for quick lookups
CREATE INDEX IF NOT EXISTS idx_sites_awaiting_credentials ON sites(awaiting_credentials) WHERE awaiting_credentials = true;
CREATE INDEX IF NOT EXISTS idx_enumeration_results_site ON enumeration_results(site_id);
CREATE INDEX IF NOT EXISTS idx_enumeration_results_time ON enumeration_results(enumeration_time DESC);
CREATE INDEX IF NOT EXISTS idx_agent_deployments_site ON agent_deployments(site_id);
CREATE INDEX IF NOT EXISTS idx_agent_deployments_hostname ON agent_deployments(hostname);
CREATE INDEX IF NOT EXISTS idx_agent_deployments_success ON agent_deployments(success) WHERE success = true;

-- Comments for documentation
COMMENT ON COLUMN sites.discovered_domain IS 'JSON object with domain discovery results (domain_name, domain_controllers, etc.)';
COMMENT ON COLUMN sites.awaiting_credentials IS 'True when domain discovered but credentials not yet submitted';
COMMENT ON COLUMN site_appliances.trigger_enumeration IS 'Set to true to trigger AD enumeration on next check-in';
COMMENT ON COLUMN site_appliances.trigger_immediate_scan IS 'Set to true to trigger immediate compliance scan after enumeration';
