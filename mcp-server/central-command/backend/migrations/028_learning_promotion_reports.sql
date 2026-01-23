-- Migration 028: Learning Promotion Reports Table
-- Stores promotion reports from appliance learning systems for audit trail and dashboard

CREATE TABLE IF NOT EXISTS learning_promotion_reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    appliance_id VARCHAR(255) NOT NULL,
    site_id VARCHAR(255) NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL,
    candidates_found INTEGER DEFAULT 0,
    candidates_promoted INTEGER DEFAULT 0,
    candidates_pending INTEGER DEFAULT 0,
    report_data JSONB,  -- Full report including candidates, rollbacks, errors
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_promotion_reports_site ON learning_promotion_reports(site_id);
CREATE INDEX IF NOT EXISTS idx_promotion_reports_appliance ON learning_promotion_reports(appliance_id);
CREATE INDEX IF NOT EXISTS idx_promotion_reports_pending ON learning_promotion_reports(candidates_pending) WHERE candidates_pending > 0;
CREATE INDEX IF NOT EXISTS idx_promotion_reports_created ON learning_promotion_reports(created_at DESC);

-- Add comment for documentation
COMMENT ON TABLE learning_promotion_reports IS 'Audit trail of L2->L1 promotion checks from appliance learning systems';
