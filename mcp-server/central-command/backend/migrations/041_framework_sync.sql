-- Framework Sync: Live compliance framework control catalog
-- Stores control definitions synced from official sources (NIST OSCAL, etc.)
-- Enables automatic detection of framework version changes and coverage gap analysis

CREATE TABLE IF NOT EXISTS framework_controls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    framework VARCHAR(30) NOT NULL,
    control_id VARCHAR(50) NOT NULL,
    control_name VARCHAR(500),
    description TEXT,
    category VARCHAR(200),
    subcategory VARCHAR(200),
    parent_control_id VARCHAR(50),
    severity VARCHAR(20),
    required BOOLEAN DEFAULT true,
    source_url TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(framework, control_id)
);

CREATE TABLE IF NOT EXISTS framework_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    framework VARCHAR(30) NOT NULL UNIQUE,
    display_name VARCHAR(100) NOT NULL,
    current_version VARCHAR(50),
    source_type VARCHAR(30) NOT NULL,
    source_url TEXT,
    last_sync_at TIMESTAMPTZ,
    last_sync_status VARCHAR(20),
    total_controls INTEGER DEFAULT 0,
    our_coverage INTEGER DEFAULT 0,
    coverage_pct DECIMAL(5,2) DEFAULT 0,
    notes TEXT,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS framework_crosswalks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_framework VARCHAR(30) NOT NULL,
    source_control_id VARCHAR(50) NOT NULL,
    target_framework VARCHAR(30) NOT NULL,
    target_control_id VARCHAR(50) NOT NULL,
    mapping_type VARCHAR(20) DEFAULT 'equivalent',
    source_reference TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_framework, source_control_id, target_framework, target_control_id)
);

CREATE TABLE IF NOT EXISTS check_control_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    check_id VARCHAR(100) NOT NULL,
    framework VARCHAR(30) NOT NULL,
    control_id VARCHAR(50) NOT NULL,
    mapping_source VARCHAR(30) DEFAULT 'manual',
    confidence DECIMAL(3,2) DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(check_id, framework, control_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_fw_controls_framework ON framework_controls(framework);
CREATE INDEX IF NOT EXISTS idx_fw_controls_category ON framework_controls(framework, category);
CREATE INDEX IF NOT EXISTS idx_fw_crosswalks_source ON framework_crosswalks(source_framework, source_control_id);
CREATE INDEX IF NOT EXISTS idx_fw_crosswalks_target ON framework_crosswalks(target_framework, target_control_id);
CREATE INDEX IF NOT EXISTS idx_check_mappings_check ON check_control_mappings(check_id);
CREATE INDEX IF NOT EXISTS idx_check_mappings_framework ON check_control_mappings(framework, control_id);

-- Seed framework versions
INSERT INTO framework_versions (framework, display_name, current_version, source_type, source_url, enabled) VALUES
    ('nist_800_53', 'NIST SP 800-53', 'Rev 5', 'oscal', 'https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json', true),
    ('nist_csf', 'NIST Cybersecurity Framework', '2.0', 'oscal', 'https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/CSF/2.0/json/NIST_CSF_2.0.json', true),
    ('hipaa', 'HIPAA Security Rule', '2013 (2025 NPRM pending)', 'manual', 'https://www.hhs.gov/hipaa/for-professionals/security/index.html', true),
    ('soc2', 'SOC 2 Type II', '2024', 'manual', 'https://www.aicpa.org/topic/audit-assurance/audit-and-assurance-greater-than-soc-2', true),
    ('pci_dss', 'PCI DSS', '4.0.1', 'manual', 'https://www.pcisecuritystandards.org/document_library/', true),
    ('cis', 'CIS Critical Security Controls', 'v8.1', 'manual', 'https://www.cisecurity.org/controls', true),
    ('nist_800_171', 'NIST SP 800-171', 'Rev 3', 'oscal', 'https://github.com/usnistgov/oscal-content', true),
    ('cmmc', 'CMMC', '2.0', 'manual', 'https://dodcio.defense.gov/CMMC/', true),
    ('gdpr', 'GDPR', '2018', 'manual', 'https://gdpr-info.eu/', true),
    ('iso_27001', 'ISO/IEC 27001', '2022', 'manual', 'https://www.iso.org/standard/27001', true),
    ('sox', 'Sarbanes-Oxley Act', '2002', 'manual', 'https://www.congress.gov/bill/107th-congress/house-bill/3763', true)
ON CONFLICT (framework) DO NOTHING;
