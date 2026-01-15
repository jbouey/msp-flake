-- Migration 017: Workstation Discovery and Compliance Tracking
-- Adds tables for tracking discovered workstations and their compliance status
-- Part of Phase 1: Complete Workstation Coverage

-- ============================================================================
-- Discovered Workstations Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS workstations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id VARCHAR(100) NOT NULL,

    -- Identity
    hostname VARCHAR(255) NOT NULL,
    distinguished_name TEXT,
    ip_address VARCHAR(45),
    mac_address VARCHAR(17),

    -- System Info
    os_name VARCHAR(100),
    os_version VARCHAR(50),

    -- Status
    online BOOLEAN DEFAULT FALSE,
    last_seen TIMESTAMP WITH TIME ZONE,
    last_logon TIMESTAMP WITH TIME ZONE,

    -- Compliance
    compliance_status VARCHAR(20) DEFAULT 'unknown'
        CHECK (compliance_status IN ('compliant', 'drifted', 'error', 'unknown', 'offline')),
    last_compliance_check TIMESTAMP WITH TIME ZONE,
    compliance_percentage DECIMAL(5,2) DEFAULT 0.0,

    -- Metadata
    discovered_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- Unique constraint: one workstation per site by hostname
    UNIQUE(site_id, hostname)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_workstations_site_id ON workstations(site_id);
CREATE INDEX IF NOT EXISTS idx_workstations_compliance ON workstations(site_id, compliance_status);
CREATE INDEX IF NOT EXISTS idx_workstations_online ON workstations(site_id, online);
CREATE INDEX IF NOT EXISTS idx_workstations_last_seen ON workstations(last_seen DESC);

-- ============================================================================
-- Workstation Compliance Checks Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS workstation_checks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workstation_id UUID NOT NULL REFERENCES workstations(id) ON DELETE CASCADE,
    site_id VARCHAR(100) NOT NULL,

    -- Check details
    check_type VARCHAR(50) NOT NULL
        CHECK (check_type IN ('bitlocker', 'defender', 'patches', 'firewall', 'screen_lock')),
    status VARCHAR(20) NOT NULL
        CHECK (status IN ('compliant', 'drifted', 'error', 'unknown')),
    compliant BOOLEAN NOT NULL DEFAULT FALSE,

    -- Check data (JSONB for flexibility)
    details JSONB NOT NULL DEFAULT '{}',
    error_message TEXT,

    -- HIPAA mapping
    hipaa_controls TEXT[] DEFAULT '{}',

    -- Timing
    duration_ms DECIMAL(10,2) DEFAULT 0.0,
    checked_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- Evidence linking
    evidence_bundle_id UUID
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ws_checks_workstation ON workstation_checks(workstation_id);
CREATE INDEX IF NOT EXISTS idx_ws_checks_site ON workstation_checks(site_id);
CREATE INDEX IF NOT EXISTS idx_ws_checks_type ON workstation_checks(check_type);
CREATE INDEX IF NOT EXISTS idx_ws_checks_time ON workstation_checks(checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_ws_checks_status ON workstation_checks(status);

-- ============================================================================
-- Workstation Evidence Bundles Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS workstation_evidence (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    bundle_id VARCHAR(36) NOT NULL UNIQUE,
    site_id VARCHAR(100) NOT NULL,
    workstation_id UUID REFERENCES workstations(id) ON DELETE SET NULL,

    -- Evidence type
    bundle_type VARCHAR(50) NOT NULL DEFAULT 'workstation'
        CHECK (bundle_type IN ('workstation', 'site_summary')),

    -- Workstation info (denormalized for historical tracking)
    hostname VARCHAR(255),
    ip_address VARCHAR(45),
    os_name VARCHAR(100),

    -- Compliance summary
    overall_status VARCHAR(20) NOT NULL
        CHECK (overall_status IN ('compliant', 'drifted', 'error', 'unknown')),
    compliant_count INTEGER DEFAULT 0,
    total_checks INTEGER DEFAULT 0,
    compliance_percentage DECIMAL(5,2) DEFAULT 0.0,

    -- Full check data
    checks JSONB NOT NULL DEFAULT '[]',
    hipaa_controls TEXT[] DEFAULT '{}',

    -- Evidence integrity
    evidence_hash VARCHAR(64) NOT NULL,
    previous_bundle_hash VARCHAR(64),
    signature BYTEA,

    -- Timestamps
    timestamp_start TIMESTAMP WITH TIME ZONE NOT NULL,
    timestamp_end TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ws_evidence_site ON workstation_evidence(site_id);
CREATE INDEX IF NOT EXISTS idx_ws_evidence_workstation ON workstation_evidence(workstation_id);
CREATE INDEX IF NOT EXISTS idx_ws_evidence_type ON workstation_evidence(bundle_type);
CREATE INDEX IF NOT EXISTS idx_ws_evidence_time ON workstation_evidence(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ws_evidence_hash ON workstation_evidence(evidence_hash);

-- ============================================================================
-- Site Workstation Summary Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS site_workstation_summaries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    bundle_id VARCHAR(36) NOT NULL UNIQUE,
    site_id VARCHAR(100) NOT NULL UNIQUE,

    -- Fleet statistics
    total_workstations INTEGER DEFAULT 0,
    online_workstations INTEGER DEFAULT 0,
    compliant_workstations INTEGER DEFAULT 0,
    drifted_workstations INTEGER DEFAULT 0,
    error_workstations INTEGER DEFAULT 0,
    unknown_workstations INTEGER DEFAULT 0,

    -- Per-check compliance (JSONB)
    check_compliance JSONB NOT NULL DEFAULT '{}',
    -- e.g., {"bitlocker": {"compliant": 45, "drifted": 5, "rate": 90.0}}

    -- Overall metrics
    overall_compliance_rate DECIMAL(5,2) DEFAULT 0.0,
    hipaa_controls TEXT[] DEFAULT '{}',

    -- References to individual bundles
    workstation_bundle_ids UUID[] DEFAULT '{}',

    -- Evidence integrity
    evidence_hash VARCHAR(64) NOT NULL,

    -- Timestamp
    last_scan TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_site_ws_summary_site ON site_workstation_summaries(site_id);
CREATE INDEX IF NOT EXISTS idx_site_ws_summary_time ON site_workstation_summaries(last_scan DESC);

-- ============================================================================
-- Views for Dashboard
-- ============================================================================

-- Current workstation compliance status per site
CREATE OR REPLACE VIEW v_site_workstation_status AS
SELECT
    w.site_id,
    COUNT(w.id) AS total_workstations,
    COUNT(CASE WHEN w.online THEN 1 END) AS online_count,
    COUNT(CASE WHEN w.compliance_status = 'compliant' THEN 1 END) AS compliant_count,
    COUNT(CASE WHEN w.compliance_status = 'drifted' THEN 1 END) AS drifted_count,
    ROUND(
        COUNT(CASE WHEN w.compliance_status = 'compliant' THEN 1 END)::DECIMAL /
        NULLIF(COUNT(CASE WHEN w.online THEN 1 END), 0) * 100, 1
    ) AS compliance_rate,
    MAX(w.last_compliance_check) AS last_check
FROM workstations w
GROUP BY w.site_id;

-- Latest check results per workstation
CREATE OR REPLACE VIEW v_workstation_latest_checks AS
SELECT DISTINCT ON (wc.workstation_id, wc.check_type)
    wc.workstation_id,
    w.hostname,
    w.site_id,
    wc.check_type,
    wc.status,
    wc.compliant,
    wc.details,
    wc.checked_at
FROM workstation_checks wc
JOIN workstations w ON wc.workstation_id = w.id
ORDER BY wc.workstation_id, wc.check_type, wc.checked_at DESC;

-- ============================================================================
-- Update Trigger for workstations.updated_at
-- ============================================================================

CREATE OR REPLACE FUNCTION update_workstation_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS workstations_updated_at ON workstations;
CREATE TRIGGER workstations_updated_at
    BEFORE UPDATE ON workstations
    FOR EACH ROW
    EXECUTE FUNCTION update_workstation_timestamp();

-- ============================================================================
-- Comments
-- ============================================================================

COMMENT ON TABLE workstations IS 'Discovered Windows workstations from Active Directory';
COMMENT ON TABLE workstation_checks IS 'Individual compliance check results per workstation';
COMMENT ON TABLE workstation_evidence IS 'Evidence bundles for workstation compliance';
COMMENT ON TABLE site_workstation_summaries IS 'Aggregated workstation compliance per site';
COMMENT ON VIEW v_site_workstation_status IS 'Dashboard view of workstation compliance by site';
COMMENT ON VIEW v_workstation_latest_checks IS 'Latest check result per workstation per check type';
