-- Migration 013: Multi-Framework Compliance Support
-- Enables per-appliance framework selection and multi-framework evidence tagging
--
-- Design: Same infrastructure check -> multiple framework mappings
-- One backup verification satisfies HIPAA 164.308(a)(7), SOC 2 A1.2, PCI DSS 12.10.1, etc.
--
-- Created: 2026-01-11
-- Author: OsirisCare Engineering

-- =============================================================================
-- APPLIANCE FRAMEWORK CONFIGURATION
-- =============================================================================

-- Per-appliance framework configuration
-- Allows each appliance to report against different frameworks
CREATE TABLE IF NOT EXISTS appliance_framework_configs (
    id SERIAL PRIMARY KEY,
    appliance_id VARCHAR(255) NOT NULL,
    site_id VARCHAR(255) NOT NULL,

    -- Enabled frameworks (array of: hipaa, soc2, pci_dss, nist_csf, cis)
    enabled_frameworks TEXT[] NOT NULL DEFAULT ARRAY['hipaa'],

    -- Primary framework for dashboard display
    primary_framework VARCHAR(50) NOT NULL DEFAULT 'hipaa',

    -- Industry for recommendations
    industry VARCHAR(100) DEFAULT 'healthcare',

    -- Framework-specific metadata (JSON)
    -- e.g., {"pci_dss": {"merchant_level": 4, "saq_type": "A"}}
    framework_metadata JSONB DEFAULT '{}',

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Each appliance has one config
    UNIQUE (appliance_id)
);

-- Index for querying by framework
CREATE INDEX IF NOT EXISTS idx_appliance_fw_frameworks
    ON appliance_framework_configs USING GIN (enabled_frameworks);

-- Index for querying by site
CREATE INDEX IF NOT EXISTS idx_appliance_fw_site
    ON appliance_framework_configs (site_id);

-- Index for querying by industry
CREATE INDEX IF NOT EXISTS idx_appliance_fw_industry
    ON appliance_framework_configs (industry);


-- =============================================================================
-- EVIDENCE FRAMEWORK MAPPINGS
-- =============================================================================

-- Links evidence bundles to framework controls
-- One evidence bundle can satisfy multiple controls across multiple frameworks
CREATE TABLE IF NOT EXISTS evidence_framework_mappings (
    id SERIAL PRIMARY KEY,

    -- Reference to evidence bundle
    bundle_id VARCHAR(255) NOT NULL,

    -- Framework and control
    framework VARCHAR(50) NOT NULL,  -- hipaa, soc2, pci_dss, nist_csf, cis
    control_id VARCHAR(100) NOT NULL,  -- e.g., "164.308(a)(7)", "CC6.1"

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- One bundle can only map to each control once per framework
    UNIQUE (bundle_id, framework, control_id)
);

-- Index for compliance score queries (framework + control)
CREATE INDEX IF NOT EXISTS idx_evidence_fw_framework
    ON evidence_framework_mappings (framework, control_id);

-- Index for bundle lookup
CREATE INDEX IF NOT EXISTS idx_evidence_fw_bundle
    ON evidence_framework_mappings (bundle_id);


-- =============================================================================
-- FRAMEWORK COMPLIANCE SCORES (Materialized View for Performance)
-- =============================================================================

-- Pre-computed compliance scores per appliance per framework
CREATE TABLE IF NOT EXISTS compliance_scores (
    id SERIAL PRIMARY KEY,
    appliance_id VARCHAR(255) NOT NULL,
    site_id VARCHAR(255) NOT NULL,
    framework VARCHAR(50) NOT NULL,

    -- Score metrics
    total_controls INTEGER NOT NULL DEFAULT 0,
    passing_controls INTEGER NOT NULL DEFAULT 0,
    failing_controls INTEGER NOT NULL DEFAULT 0,
    unknown_controls INTEGER NOT NULL DEFAULT 0,
    score_percentage DECIMAL(5,2) DEFAULT 0.00,

    -- Compliance status
    is_compliant BOOLEAN DEFAULT FALSE,  -- score >= 80%
    at_risk BOOLEAN DEFAULT FALSE,        -- score < 70%

    -- Score period
    evidence_window_days INTEGER DEFAULT 30,
    calculated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Unique per appliance/framework combination
    UNIQUE (appliance_id, framework)
);

-- Index for dashboard queries
CREATE INDEX IF NOT EXISTS idx_compliance_scores_site
    ON compliance_scores (site_id, framework);

-- Index for at-risk detection
CREATE INDEX IF NOT EXISTS idx_compliance_scores_risk
    ON compliance_scores (at_risk) WHERE at_risk = TRUE;


-- =============================================================================
-- VIEWS
-- =============================================================================

-- View for latest control status per appliance/framework
CREATE OR REPLACE VIEW v_control_status AS
WITH latest_evidence AS (
    SELECT
        cb.appliance_id,
        efm.framework,
        efm.control_id,
        cb.outcome,
        cb.created_at,
        ROW_NUMBER() OVER (
            PARTITION BY cb.appliance_id, efm.framework, efm.control_id
            ORDER BY cb.created_at DESC
        ) as rn
    FROM compliance_bundles cb
    JOIN evidence_framework_mappings efm ON cb.bundle_id = efm.bundle_id
    WHERE cb.created_at >= NOW() - INTERVAL '30 days'
)
SELECT
    appliance_id,
    framework,
    control_id,
    outcome,
    created_at as last_checked
FROM latest_evidence
WHERE rn = 1;


-- View for compliance dashboard overview
CREATE OR REPLACE VIEW v_compliance_dashboard AS
SELECT
    afc.site_id,
    afc.appliance_id,
    afc.primary_framework,
    afc.enabled_frameworks,
    afc.industry,
    cs.framework,
    cs.score_percentage,
    cs.passing_controls,
    cs.total_controls,
    cs.is_compliant,
    cs.at_risk,
    cs.calculated_at
FROM appliance_framework_configs afc
LEFT JOIN compliance_scores cs
    ON afc.appliance_id = cs.appliance_id
    AND afc.primary_framework = cs.framework;


-- =============================================================================
-- FUNCTIONS
-- =============================================================================

-- Function to calculate compliance score for an appliance/framework
CREATE OR REPLACE FUNCTION calculate_compliance_score(
    p_appliance_id VARCHAR,
    p_framework VARCHAR,
    p_window_days INTEGER DEFAULT 30
) RETURNS TABLE (
    total_controls INTEGER,
    passing_controls INTEGER,
    failing_controls INTEGER,
    unknown_controls INTEGER,
    score_percentage DECIMAL(5,2)
) AS $$
BEGIN
    RETURN QUERY
    WITH control_status AS (
        SELECT DISTINCT ON (efm.control_id)
            efm.control_id,
            cb.outcome
        FROM compliance_bundles cb
        JOIN evidence_framework_mappings efm ON cb.bundle_id = efm.bundle_id
        WHERE cb.appliance_id = p_appliance_id
          AND efm.framework = p_framework
          AND cb.created_at >= NOW() - (p_window_days || ' days')::INTERVAL
        ORDER BY efm.control_id, cb.created_at DESC
    )
    SELECT
        COUNT(*)::INTEGER as total_controls,
        COUNT(*) FILTER (WHERE outcome IN ('pass', 'remediated'))::INTEGER as passing_controls,
        COUNT(*) FILTER (WHERE outcome = 'fail')::INTEGER as failing_controls,
        COUNT(*) FILTER (WHERE outcome NOT IN ('pass', 'remediated', 'fail'))::INTEGER as unknown_controls,
        ROUND(
            COUNT(*) FILTER (WHERE outcome IN ('pass', 'remediated'))::DECIMAL /
            NULLIF(COUNT(*), 0) * 100,
            2
        ) as score_percentage
    FROM control_status;
END;
$$ LANGUAGE plpgsql;


-- Function to refresh compliance score for an appliance
CREATE OR REPLACE FUNCTION refresh_compliance_score(
    p_appliance_id VARCHAR,
    p_framework VARCHAR
) RETURNS VOID AS $$
DECLARE
    v_site_id VARCHAR;
    v_score RECORD;
BEGIN
    -- Get site_id
    SELECT site_id INTO v_site_id
    FROM appliance_framework_configs
    WHERE appliance_id = p_appliance_id;

    IF v_site_id IS NULL THEN
        -- Get from appliances table if no config yet
        SELECT site_id INTO v_site_id
        FROM appliances
        WHERE id = p_appliance_id;
    END IF;

    -- Calculate score
    SELECT * INTO v_score FROM calculate_compliance_score(p_appliance_id, p_framework);

    -- Upsert score
    INSERT INTO compliance_scores (
        appliance_id, site_id, framework,
        total_controls, passing_controls, failing_controls, unknown_controls,
        score_percentage, is_compliant, at_risk, calculated_at
    ) VALUES (
        p_appliance_id, v_site_id, p_framework,
        v_score.total_controls, v_score.passing_controls,
        v_score.failing_controls, v_score.unknown_controls,
        COALESCE(v_score.score_percentage, 0),
        COALESCE(v_score.score_percentage, 0) >= 80,
        COALESCE(v_score.score_percentage, 0) < 70,
        NOW()
    )
    ON CONFLICT (appliance_id, framework)
    DO UPDATE SET
        total_controls = EXCLUDED.total_controls,
        passing_controls = EXCLUDED.passing_controls,
        failing_controls = EXCLUDED.failing_controls,
        unknown_controls = EXCLUDED.unknown_controls,
        score_percentage = EXCLUDED.score_percentage,
        is_compliant = EXCLUDED.is_compliant,
        at_risk = EXCLUDED.at_risk,
        calculated_at = EXCLUDED.calculated_at;
END;
$$ LANGUAGE plpgsql;


-- =============================================================================
-- MIGRATE EXISTING DATA
-- =============================================================================

-- Create framework configs for existing appliances (default to HIPAA)
INSERT INTO appliance_framework_configs (appliance_id, site_id, enabled_frameworks, primary_framework, industry)
SELECT
    id as appliance_id,
    site_id,
    ARRAY['hipaa'] as enabled_frameworks,
    'hipaa' as primary_framework,
    'healthcare' as industry
FROM appliances
WHERE id NOT IN (SELECT appliance_id FROM appliance_framework_configs)
ON CONFLICT (appliance_id) DO NOTHING;


-- Migrate existing evidence bundles to framework mappings (HIPAA only for existing)
-- This extracts HIPAA controls from the hipaa_controls JSONB field
INSERT INTO evidence_framework_mappings (bundle_id, framework, control_id)
SELECT DISTINCT
    bundle_id,
    'hipaa' as framework,
    jsonb_array_elements_text(
        CASE
            WHEN hipaa_controls IS NULL THEN '[]'::jsonb
            WHEN jsonb_typeof(hipaa_controls) = 'array' THEN hipaa_controls
            ELSE '[]'::jsonb
        END
    ) as control_id
FROM compliance_bundles
WHERE hipaa_controls IS NOT NULL
  AND hipaa_controls != '[]'::jsonb
  AND bundle_id NOT IN (
      SELECT DISTINCT bundle_id FROM evidence_framework_mappings WHERE framework = 'hipaa'
  )
ON CONFLICT (bundle_id, framework, control_id) DO NOTHING;


-- =============================================================================
-- TRIGGERS
-- =============================================================================

-- Update timestamp on framework config changes
CREATE OR REPLACE FUNCTION update_framework_config_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_framework_config_timestamp ON appliance_framework_configs;
CREATE TRIGGER trg_update_framework_config_timestamp
    BEFORE UPDATE ON appliance_framework_configs
    FOR EACH ROW
    EXECUTE FUNCTION update_framework_config_timestamp();


-- =============================================================================
-- COMMENTS
-- =============================================================================

COMMENT ON TABLE appliance_framework_configs IS
    'Per-appliance framework configuration. Each appliance can report against different frameworks.';

COMMENT ON TABLE evidence_framework_mappings IS
    'Maps evidence bundles to framework controls. One bundle can satisfy multiple controls across frameworks.';

COMMENT ON TABLE compliance_scores IS
    'Pre-computed compliance scores per appliance/framework. Refreshed when evidence is added.';

COMMENT ON VIEW v_control_status IS
    'Latest control status per appliance/framework based on recent evidence.';

COMMENT ON VIEW v_compliance_dashboard IS
    'Dashboard view combining framework configs and compliance scores.';


-- =============================================================================
-- DONE
-- =============================================================================
-- Migration complete. Multi-framework compliance support is now enabled.
--
-- To use:
-- 1. Set enabled_frameworks on appliance_framework_configs
-- 2. Evidence bundles will be tagged with all applicable framework controls
-- 3. Compliance scores calculated per framework
-- 4. Dashboard shows primary framework score with option to view others
