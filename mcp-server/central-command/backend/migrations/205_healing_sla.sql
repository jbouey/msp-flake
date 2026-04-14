-- Migration 205: Per-site healing rate SLA tracking (renumbered from 111)
--
-- Tracks hourly healing rate per site against configurable SLA targets.
-- Background task in main.py upserts rows from execution_telemetry.
-- Dashboard admin endpoint exposes SLA overview with 7-period trends.
--
-- Table + policies already exist in prod (applied out-of-band pre-205);
-- DROP POLICY IF EXISTS collapses legacy names to canonical ones.

CREATE TABLE IF NOT EXISTS site_healing_sla (
    site_id VARCHAR(50) REFERENCES sites(site_id) ON DELETE CASCADE,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    total_attempts INT DEFAULT 0,
    successful_heals INT DEFAULT 0,
    healing_rate NUMERIC(5,2) DEFAULT 0,
    sla_target NUMERIC(5,2) DEFAULT 90.0,
    sla_met BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (site_id, period_start)
);

-- Fast lookups for dashboard and alerting
CREATE INDEX IF NOT EXISTS idx_healing_sla_site_period
    ON site_healing_sla (site_id, period_start DESC);

CREATE INDEX IF NOT EXISTS idx_healing_sla_breached
    ON site_healing_sla (sla_met)
    WHERE sla_met = false;

-- ============================================================================
-- RLS (matches project pattern — site_id tenant isolation)
-- ============================================================================

ALTER TABLE site_healing_sla ENABLE ROW LEVEL SECURITY;
ALTER TABLE site_healing_sla FORCE ROW LEVEL SECURITY;

-- Collapse legacy policy names (created out-of-band pre-205) + own reruns
DROP POLICY IF EXISTS admin_bypass ON site_healing_sla;
DROP POLICY IF EXISTS tenant_isolation ON site_healing_sla;
DROP POLICY IF EXISTS site_healing_sla_admin ON site_healing_sla;
DROP POLICY IF EXISTS site_healing_sla_tenant ON site_healing_sla;

CREATE POLICY site_healing_sla_admin ON site_healing_sla
    FOR ALL
    USING (current_setting('app.is_admin', true) = 'true');

-- Tenant isolation (read-only for non-admin)
CREATE POLICY site_healing_sla_tenant ON site_healing_sla
    FOR SELECT
    USING (site_id = current_setting('app.current_tenant', true));

-- ============================================================================
-- PERMISSIONS
-- ============================================================================

GRANT ALL ON site_healing_sla TO mcp;
GRANT SELECT, INSERT, UPDATE ON site_healing_sla TO mcp_app;

SELECT 'Migration 205_healing_sla completed successfully' AS status;
