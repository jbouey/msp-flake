-- Migration 018: RMM Comparison Reports
-- Phase 1 Enhancement: Track workstation comparisons with external RMM tools
-- Created: 2026-01-15

-- =============================================================================
-- RMM Comparison Reports Table
-- =============================================================================
-- Stores the results of comparing our AD-discovered workstations
-- with data exported from RMM tools (ConnectWise, Datto, NinjaRMM, etc.)

CREATE TABLE IF NOT EXISTS rmm_comparison_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id VARCHAR(255) NOT NULL,
    provider VARCHAR(50) NOT NULL DEFAULT 'manual',

    -- Summary metrics
    our_count INTEGER NOT NULL DEFAULT 0,
    rmm_count INTEGER NOT NULL DEFAULT 0,
    matched_count INTEGER NOT NULL DEFAULT 0,
    coverage_rate DECIMAL(5,2) DEFAULT 0.00,

    -- Full report data (matches, gaps, recommendations)
    report_data JSONB NOT NULL DEFAULT '{}',

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT valid_provider CHECK (provider IN (
        'connectwise', 'datto', 'ninja', 'syncro', 'manual'
    )),
    CONSTRAINT valid_counts CHECK (
        our_count >= 0 AND rmm_count >= 0 AND matched_count >= 0
    ),
    CONSTRAINT valid_coverage CHECK (
        coverage_rate >= 0 AND coverage_rate <= 100
    )
);

-- One report per site (latest wins)
CREATE UNIQUE INDEX IF NOT EXISTS idx_rmm_comparison_site
ON rmm_comparison_reports(site_id);

-- Index for finding reports by provider
CREATE INDEX IF NOT EXISTS idx_rmm_comparison_provider
ON rmm_comparison_reports(provider);

-- Index for finding recent reports
CREATE INDEX IF NOT EXISTS idx_rmm_comparison_created
ON rmm_comparison_reports(created_at DESC);

-- =============================================================================
-- RMM Comparison History (Optional - for tracking changes over time)
-- =============================================================================
-- Stores historical comparison results for trend analysis

CREATE TABLE IF NOT EXISTS rmm_comparison_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id VARCHAR(255) NOT NULL,
    provider VARCHAR(50) NOT NULL,

    -- Summary metrics at time of comparison
    our_count INTEGER NOT NULL DEFAULT 0,
    rmm_count INTEGER NOT NULL DEFAULT 0,
    matched_count INTEGER NOT NULL DEFAULT 0,
    coverage_rate DECIMAL(5,2) DEFAULT 0.00,

    -- Key gap counts
    missing_from_rmm INTEGER NOT NULL DEFAULT 0,
    missing_from_ad INTEGER NOT NULL DEFAULT 0,
    stale_entries INTEGER NOT NULL DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for finding history by site
CREATE INDEX IF NOT EXISTS idx_rmm_history_site_time
ON rmm_comparison_history(site_id, created_at DESC);

-- =============================================================================
-- Trigger: Log comparison history on insert/update
-- =============================================================================

CREATE OR REPLACE FUNCTION log_rmm_comparison_history()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO rmm_comparison_history (
        site_id, provider, our_count, rmm_count, matched_count,
        coverage_rate, missing_from_rmm, missing_from_ad, stale_entries, created_at
    )
    SELECT
        NEW.site_id,
        NEW.provider,
        NEW.our_count,
        NEW.rmm_count,
        NEW.matched_count,
        NEW.coverage_rate,
        COALESCE((NEW.report_data->'gaps')::jsonb #>> '{0}' = 'missing_from_rmm', false)::int,
        COALESCE((NEW.report_data->'gaps')::jsonb #>> '{0}' = 'missing_from_ad', false)::int,
        0,
        NEW.created_at;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_log_rmm_comparison
AFTER INSERT OR UPDATE ON rmm_comparison_reports
FOR EACH ROW
EXECUTE FUNCTION log_rmm_comparison_history();

-- =============================================================================
-- Comments
-- =============================================================================

COMMENT ON TABLE rmm_comparison_reports IS
'Latest RMM comparison report for each site. Compares our AD-discovered
workstations with data from external RMM tools to identify coverage gaps.';

COMMENT ON COLUMN rmm_comparison_reports.provider IS
'RMM tool that provided the comparison data (connectwise, datto, ninja, syncro, manual)';

COMMENT ON COLUMN rmm_comparison_reports.coverage_rate IS
'Percentage of our workstations that have matching RMM entries (0-100)';

COMMENT ON COLUMN rmm_comparison_reports.report_data IS
'Full comparison report including matches, gaps, and recommendations as JSONB';

COMMENT ON TABLE rmm_comparison_history IS
'Historical record of RMM comparisons for trend analysis';
