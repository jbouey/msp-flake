-- Migration 028: Learning Promotion Reports & Approval Workflow
-- Stores promotion reports and tracks site owner approval for L2->L1 promotions

-- Main reports table (audit trail)
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

-- Individual promotion candidates requiring approval
CREATE TABLE IF NOT EXISTS learning_promotion_candidates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    report_id UUID REFERENCES learning_promotion_reports(id) ON DELETE CASCADE,
    site_id VARCHAR(255) NOT NULL,
    appliance_id VARCHAR(255) NOT NULL,
    pattern_signature VARCHAR(32) NOT NULL,
    recommended_action VARCHAR(255) NOT NULL,
    confidence_score DECIMAL(5,4) NOT NULL,
    success_rate DECIMAL(5,4) NOT NULL,
    total_occurrences INTEGER NOT NULL,
    l2_resolutions INTEGER NOT NULL,
    promotion_reason TEXT,

    -- Approval workflow
    approval_status VARCHAR(20) DEFAULT 'pending',  -- pending, approved, rejected
    approved_by UUID REFERENCES admin_users(id),
    approved_at TIMESTAMPTZ,
    rejection_reason TEXT,

    -- Tracking
    created_at TIMESTAMPTZ DEFAULT NOW(),
    notified_at TIMESTAMPTZ,  -- When site owner was notified

    CONSTRAINT valid_approval_status CHECK (approval_status IN ('pending', 'approved', 'rejected'))
);

-- Indexes for reports
CREATE INDEX IF NOT EXISTS idx_promotion_reports_site ON learning_promotion_reports(site_id);
CREATE INDEX IF NOT EXISTS idx_promotion_reports_appliance ON learning_promotion_reports(appliance_id);
CREATE INDEX IF NOT EXISTS idx_promotion_reports_pending ON learning_promotion_reports(candidates_pending) WHERE candidates_pending > 0;
CREATE INDEX IF NOT EXISTS idx_promotion_reports_created ON learning_promotion_reports(created_at DESC);

-- Indexes for candidates
CREATE INDEX IF NOT EXISTS idx_promotion_candidates_site ON learning_promotion_candidates(site_id);
CREATE INDEX IF NOT EXISTS idx_promotion_candidates_status ON learning_promotion_candidates(approval_status);
CREATE INDEX IF NOT EXISTS idx_promotion_candidates_pending ON learning_promotion_candidates(site_id, approval_status) WHERE approval_status = 'pending';
CREATE INDEX IF NOT EXISTS idx_promotion_candidates_approved ON learning_promotion_candidates(site_id, approval_status) WHERE approval_status = 'approved';

-- Comments
COMMENT ON TABLE learning_promotion_reports IS 'Audit trail of L2->L1 promotion checks from appliance learning systems';
COMMENT ON TABLE learning_promotion_candidates IS 'Individual promotion candidates requiring site owner approval';
COMMENT ON COLUMN learning_promotion_candidates.approval_status IS 'pending=awaiting review, approved=ready to promote, rejected=declined by owner';
