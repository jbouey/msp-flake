-- Portal Tables Migration
-- Run this on the VPS PostgreSQL database
--
-- Usage: docker exec -i mcp-postgres psql -U mcp -d mcp < 001_portal_tables.sql

-- ============================================================================
-- Add portal_access_token to sites table
-- ============================================================================

ALTER TABLE sites
ADD COLUMN IF NOT EXISTS portal_access_token VARCHAR(128),
ADD COLUMN IF NOT EXISTS portal_token_created_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_sites_portal_token ON sites(portal_access_token);

-- ============================================================================
-- Compliance Snapshots (from appliance phone-home)
-- ============================================================================

CREATE TABLE IF NOT EXISTS compliance_snapshots (
    id SERIAL PRIMARY KEY,
    site_id VARCHAR(50) REFERENCES sites(site_id) ON DELETE CASCADE,
    snapshot_at TIMESTAMP DEFAULT NOW(),

    -- From appliance phone-home
    flake_hash VARCHAR(64),
    patch_status JSONB,        -- {critical_pending, high_pending, last_applied, mttr_hours}
    backup_status JSONB,       -- {last_success, last_failure, last_restore_test, size_gb}
    encryption_status JSONB,   -- {luks_volumes: [], tls_certs: []}
    time_sync_status JSONB,    -- {ntp_synchronized, drift_ms, sources}
    service_health JSONB,      -- {nginx: "active", postgresql: "active", ...}

    -- Indexes for fast portal queries
    CONSTRAINT unique_site_snapshot UNIQUE (site_id, snapshot_at)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_site_time ON compliance_snapshots(site_id, snapshot_at DESC);

-- ============================================================================
-- Compliance Results (8 core controls)
-- ============================================================================

CREATE TABLE IF NOT EXISTS compliance_results (
    id SERIAL PRIMARY KEY,
    site_id VARCHAR(50) REFERENCES sites(site_id) ON DELETE CASCADE,
    rule_id VARCHAR(50) NOT NULL,  -- endpoint_drift, patch_freshness, backup_success, etc.
    status VARCHAR(10) NOT NULL,    -- pass, warn, fail
    checked_at TIMESTAMP DEFAULT NOW(),

    -- Evidence and actions
    evidence_refs TEXT[],
    auto_fix_triggered BOOLEAN DEFAULT FALSE,
    fix_job_id VARCHAR(50),
    fix_duration_sec INTEGER,
    exception_applied BOOLEAN DEFAULT FALSE,
    exception_reason TEXT,
    exception_expires TIMESTAMP,

    -- Scope details (for display)
    scope JSONB,  -- {summary: "2/2 nodes compliant", details: {...}}

    -- HIPAA control mapping
    hipaa_controls TEXT[]  -- ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"]
);

CREATE INDEX IF NOT EXISTS idx_results_site_rule ON compliance_results(site_id, rule_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_results_status ON compliance_results(status);

-- ============================================================================
-- Evidence Bundles (metadata - actual files in MinIO)
-- ============================================================================

CREATE TABLE IF NOT EXISTS evidence_bundles (
    id SERIAL PRIMARY KEY,
    bundle_id VARCHAR(50) UNIQUE NOT NULL,  -- EP-20251231-clinic001
    site_id VARCHAR(50) REFERENCES sites(site_id) ON DELETE CASCADE,
    bundle_type VARCHAR(20) NOT NULL,  -- daily, weekly, monthly
    generated_at TIMESTAMP DEFAULT NOW(),

    -- Integrity
    bundle_hash VARCHAR(64) NOT NULL,
    signature VARCHAR(512),
    signed_by VARCHAR(100),

    -- Storage
    minio_bucket VARCHAR(100) DEFAULT 'evidence',
    minio_key VARCHAR(256) NOT NULL,
    size_bytes BIGINT,

    -- Contents summary
    manifest JSONB,  -- {files: [], rule_results: {}, date_range: {...}}

    -- Retention
    retention_until TIMESTAMP,
    archived BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_bundles_site_type ON evidence_bundles(site_id, bundle_type, generated_at DESC);

-- ============================================================================
-- Site KPIs (cached for fast portal load)
-- ============================================================================

CREATE TABLE IF NOT EXISTS site_kpis (
    site_id VARCHAR(50) PRIMARY KEY REFERENCES sites(site_id) ON DELETE CASCADE,
    updated_at TIMESTAMP DEFAULT NOW(),

    -- Core KPIs
    compliance_pct DECIMAL(5,2),      -- 0-100
    patch_mttr_hours DECIMAL(8,2),    -- Mean time to remediate
    mfa_coverage_pct DECIMAL(5,2),    -- 0-100
    backup_success_rate DECIMAL(5,2), -- 0-100
    auto_fixes_24h INTEGER,           -- Count

    -- Additional metrics
    controls_passing INTEGER DEFAULT 0,
    controls_warning INTEGER DEFAULT 0,
    controls_failing INTEGER DEFAULT 0,
    last_incident_at TIMESTAMP,
    last_backup_at TIMESTAMP,

    -- Calculated on update
    health_score DECIMAL(5,2)  -- Weighted composite
);

-- ============================================================================
-- Portal Access Log (for audit)
-- ============================================================================

CREATE TABLE IF NOT EXISTS portal_access_log (
    id SERIAL PRIMARY KEY,
    site_id VARCHAR(50) REFERENCES sites(site_id) ON DELETE CASCADE,
    accessed_at TIMESTAMP DEFAULT NOW(),
    ip_address VARCHAR(45),
    user_agent TEXT,
    endpoint VARCHAR(256)
);

CREATE INDEX IF NOT EXISTS idx_portal_access_site ON portal_access_log(site_id, accessed_at DESC);

-- ============================================================================
-- Stored procedure to update site KPIs
-- ============================================================================

CREATE OR REPLACE FUNCTION update_site_kpis(p_site_id VARCHAR(50))
RETURNS VOID AS $$
DECLARE
    v_passing INTEGER;
    v_warning INTEGER;
    v_failing INTEGER;
BEGIN
    -- Count control statuses
    SELECT
        COUNT(*) FILTER (WHERE status = 'pass'),
        COUNT(*) FILTER (WHERE status = 'warn'),
        COUNT(*) FILTER (WHERE status = 'fail')
    INTO v_passing, v_warning, v_failing
    FROM (
        SELECT DISTINCT ON (rule_id) rule_id, status
        FROM compliance_results
        WHERE site_id = p_site_id
        ORDER BY rule_id, checked_at DESC
    ) latest;

    -- Upsert KPIs
    INSERT INTO site_kpis (site_id, controls_passing, controls_warning, controls_failing, updated_at)
    VALUES (p_site_id, v_passing, v_warning, v_failing, NOW())
    ON CONFLICT (site_id) DO UPDATE SET
        controls_passing = EXCLUDED.controls_passing,
        controls_warning = EXCLUDED.controls_warning,
        controls_failing = EXCLUDED.controls_failing,
        updated_at = NOW(),
        compliance_pct = CASE
            WHEN (EXCLUDED.controls_passing + EXCLUDED.controls_warning + EXCLUDED.controls_failing) > 0
            THEN (EXCLUDED.controls_passing::DECIMAL / (EXCLUDED.controls_passing + EXCLUDED.controls_warning + EXCLUDED.controls_failing)) * 100
            ELSE 100
        END;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Grant permissions
-- ============================================================================

GRANT ALL ON ALL TABLES IN SCHEMA public TO mcp;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO mcp;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO mcp;

-- ============================================================================
-- Summary
-- ============================================================================

-- Show created tables
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
AND table_name IN ('compliance_snapshots', 'compliance_results', 'evidence_bundles', 'site_kpis', 'portal_access_log');
