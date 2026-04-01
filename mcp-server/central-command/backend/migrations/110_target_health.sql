-- Migration 110: Target connectivity health tracking
--
-- Tracks per-target probe results reported by the appliance daemon's
-- probeTargetConnectivity at startup and on each scan cycle.
-- Covers SSH, WinRM, and SNMP reachability for every scan target.
--
-- This fills the audit gap: WinRM credential validation exists as a
-- fleet order handler, but there was no persistent tracking of
-- connectivity probe results across all protocols.
--
-- Usage: docker exec -i mcp-postgres psql -U mcp -d mcp < 110_target_health.sql

-- ============================================================================
-- TARGET HEALTH TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS target_health (
    id SERIAL PRIMARY KEY,
    site_id VARCHAR(255) NOT NULL,
    hostname VARCHAR(255) NOT NULL,          -- IP or hostname of the target
    protocol VARCHAR(20) NOT NULL,           -- ssh, winrm, snmp
    port INTEGER,                            -- 22, 5985, 5986, 161, etc.
    status VARCHAR(20) NOT NULL DEFAULT 'unknown',  -- ok, unreachable, auth_failed, timeout, error
    error TEXT,                              -- error message when status != ok
    latency_ms INTEGER,                      -- probe round-trip time
    reported_by VARCHAR(255),                -- appliance_id that reported
    last_reported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One row per (site, host, protocol, port) — upsert on conflict
    UNIQUE(site_id, hostname, protocol, port)
);

-- Fast lookups by site (dashboard) and by status (alerting)
CREATE INDEX IF NOT EXISTS idx_target_health_site
    ON target_health(site_id, last_reported_at DESC);

CREATE INDEX IF NOT EXISTS idx_target_health_unhealthy
    ON target_health(status)
    WHERE status != 'ok';

-- ============================================================================
-- RLS (matches project pattern — site_id tenant isolation)
-- ============================================================================

ALTER TABLE target_health ENABLE ROW LEVEL SECURITY;
ALTER TABLE target_health FORCE ROW LEVEL SECURITY;

-- Admin bypass
CREATE POLICY target_health_admin ON target_health
    FOR ALL
    USING (current_setting('app.is_admin', true) = 'true');

-- Tenant isolation
CREATE POLICY target_health_tenant ON target_health
    FOR ALL
    USING (site_id = current_setting('app.current_tenant', true));

-- ============================================================================
-- PERMISSIONS
-- ============================================================================

GRANT ALL ON target_health TO mcp;
GRANT ALL ON SEQUENCE target_health_id_seq TO mcp;
GRANT SELECT, INSERT, UPDATE ON target_health TO mcp_app;

SELECT 'Migration 110_target_health completed successfully' AS status;
