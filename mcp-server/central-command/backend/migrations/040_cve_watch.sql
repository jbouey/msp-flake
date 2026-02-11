-- ============================================================================
-- Migration 040: CVE Watch — Progressive Vulnerability Coverage Tracking
-- ============================================================================
-- Stores CVE data from NVD (National Vulnerability Database), maps CVEs to
-- managed fleet appliances, and tracks remediation/mitigation status.
-- Supports HIPAA 164.308(a)(1) risk analysis requirements.

-- CVE entries synced from NVD API v2.0
CREATE TABLE IF NOT EXISTS cve_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cve_id VARCHAR(20) NOT NULL UNIQUE,
    severity VARCHAR(10) NOT NULL DEFAULT 'unknown',
    cvss_score DECIMAL(3,1),
    published_date TIMESTAMPTZ,
    last_modified TIMESTAMPTZ,
    description TEXT,
    affected_cpes JSONB DEFAULT '[]'::jsonb,
    refs JSONB DEFAULT '[]'::jsonb,
    cwe_ids TEXT[] DEFAULT '{}',
    nvd_status VARCHAR(30),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- CVE-to-fleet mapping with remediation tracking
CREATE TABLE IF NOT EXISTS cve_fleet_matches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cve_id UUID NOT NULL REFERENCES cve_entries(id) ON DELETE CASCADE,
    appliance_id VARCHAR(255),
    site_id VARCHAR(255),
    match_reason TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    notes TEXT,
    mitigated_at TIMESTAMPTZ,
    mitigated_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(cve_id, appliance_id)
);

-- CVE Watch configuration (singleton row)
CREATE TABLE IF NOT EXISTS cve_watch_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nvd_api_key VARCHAR(255),
    watched_cpes JSONB NOT NULL DEFAULT '[]'::jsonb,
    sync_interval_hours INTEGER DEFAULT 6,
    last_sync_at TIMESTAMPTZ,
    last_sync_cve_count INTEGER DEFAULT 0,
    min_severity VARCHAR(10) DEFAULT 'medium',
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_cve_entries_severity ON cve_entries(severity);
CREATE INDEX IF NOT EXISTS idx_cve_entries_published ON cve_entries(published_date DESC);
CREATE INDEX IF NOT EXISTS idx_cve_entries_cve_id ON cve_entries(cve_id);
CREATE INDEX IF NOT EXISTS idx_cve_fleet_status ON cve_fleet_matches(status);
CREATE INDEX IF NOT EXISTS idx_cve_fleet_site ON cve_fleet_matches(site_id);
CREATE INDEX IF NOT EXISTS idx_cve_fleet_appliance ON cve_fleet_matches(appliance_id);

-- Default config with CPEs for lab fleet
INSERT INTO cve_watch_config (watched_cpes, sync_interval_hours, min_severity) VALUES (
    '[
        "cpe:2.3:o:microsoft:windows_server_2022:*:*:*:*:*:*:*:*",
        "cpe:2.3:o:microsoft:windows_10:*:*:*:*:*:*:*:*",
        "cpe:2.3:o:microsoft:windows_11:*:*:*:*:*:*:*:*",
        "cpe:2.3:o:canonical:ubuntu_linux:22.04:*:*:*:lts:*:*:*",
        "cpe:2.3:a:openbsd:openssh:*:*:*:*:*:*:*:*",
        "cpe:2.3:a:python:python:3.11:*:*:*:*:*:*:*",
        "cpe:2.3:a:python:python:3.13:*:*:*:*:*:*:*"
    ]'::jsonb,
    6,
    'medium'
) ON CONFLICT DO NOTHING;

COMMENT ON TABLE cve_entries IS 'CVE data synced from NVD API v2.0';
COMMENT ON TABLE cve_fleet_matches IS 'Maps CVEs to affected fleet appliances with remediation status';
COMMENT ON TABLE cve_watch_config IS 'CVE Watch configuration — CPE watch list, sync interval, API key';
