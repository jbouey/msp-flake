-- Migration 032: Learning Promotion System
-- Adds promoted_rules table for storing server-generated rules from pattern promotions

-- Table for storing generated rules that get deployed to agents
CREATE TABLE IF NOT EXISTS promoted_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id VARCHAR(50) UNIQUE NOT NULL,  -- L1-PROMOTED-ABC12345
    pattern_signature VARCHAR(64) NOT NULL,
    site_id VARCHAR(255) NOT NULL,
    partner_id UUID NOT NULL REFERENCES partners(id),
    rule_yaml TEXT NOT NULL,
    rule_json JSONB NOT NULL,
    promoted_by UUID,  -- partner user who approved (optional)
    promoted_at TIMESTAMPTZ DEFAULT NOW(),
    status VARCHAR(20) DEFAULT 'active',  -- active, disabled, archived
    deployment_count INTEGER DEFAULT 0,
    last_deployed_at TIMESTAMPTZ,
    notes TEXT,  -- approval notes from partner
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for quick lookups
CREATE INDEX IF NOT EXISTS idx_promoted_rules_site ON promoted_rules(site_id);
CREATE INDEX IF NOT EXISTS idx_promoted_rules_partner ON promoted_rules(partner_id);
CREATE INDEX IF NOT EXISTS idx_promoted_rules_pattern ON promoted_rules(pattern_signature);
CREATE INDEX IF NOT EXISTS idx_promoted_rules_active ON promoted_rules(status) WHERE status = 'active';

-- Add approval fields to learning_promotion_candidates if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'learning_promotion_candidates'
                   AND column_name = 'custom_rule_name') THEN
        ALTER TABLE learning_promotion_candidates ADD COLUMN custom_rule_name VARCHAR(255);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'learning_promotion_candidates'
                   AND column_name = 'approval_notes') THEN
        ALTER TABLE learning_promotion_candidates ADD COLUMN approval_notes TEXT;
    END IF;
END $$;

-- Add unique constraint for upsert on approval (site_id + pattern_signature)
-- This allows ON CONFLICT to work when updating approval status from dashboard
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'learning_promotion_candidates_site_pattern_unique'
    ) THEN
        ALTER TABLE learning_promotion_candidates
        ADD CONSTRAINT learning_promotion_candidates_site_pattern_unique
        UNIQUE (site_id, pattern_signature);
    END IF;
END $$;

-- Make columns nullable for dashboard-initiated approvals
-- (dashboard approvals don't have appliance context like agent-reported candidates)
ALTER TABLE learning_promotion_candidates ALTER COLUMN appliance_id DROP NOT NULL;
ALTER TABLE learning_promotion_candidates ALTER COLUMN recommended_action DROP NOT NULL;
ALTER TABLE learning_promotion_candidates ALTER COLUMN confidence_score DROP NOT NULL;
ALTER TABLE learning_promotion_candidates ALTER COLUMN success_rate DROP NOT NULL;
ALTER TABLE learning_promotion_candidates ALTER COLUMN total_occurrences DROP NOT NULL;
ALTER TABLE learning_promotion_candidates ALTER COLUMN l2_resolutions DROP NOT NULL;

-- View: Partner-scoped promotion candidates with site info
CREATE OR REPLACE VIEW v_partner_promotion_candidates AS
SELECT
    aps.id,
    aps.site_id,
    s.clinic_name as site_name,
    s.partner_id,
    aps.pattern_signature,
    aps.total_occurrences,
    aps.l1_resolutions,
    aps.l2_resolutions,
    aps.l3_resolutions,
    aps.success_count,
    aps.success_rate,
    aps.avg_resolution_time_ms,
    aps.recommended_action,
    aps.first_seen,
    aps.last_seen,
    aps.promotion_eligible,
    COALESCE(lpc.approval_status, 'not_submitted') as approval_status,
    lpc.approved_at,
    lpc.rejection_reason
FROM aggregated_pattern_stats aps
JOIN sites s ON s.site_id = aps.site_id
LEFT JOIN learning_promotion_candidates lpc
    ON lpc.pattern_signature = aps.pattern_signature
    AND lpc.site_id = aps.site_id
WHERE aps.promotion_eligible = TRUE
ORDER BY aps.success_rate DESC, aps.total_occurrences DESC;

-- View: Partner learning stats summary
CREATE OR REPLACE VIEW v_partner_learning_stats AS
SELECT
    s.partner_id,
    COUNT(DISTINCT CASE WHEN aps.promotion_eligible AND COALESCE(lpc.approval_status, 'not_submitted') = 'not_submitted' THEN aps.id END) as pending_candidates,
    COUNT(DISTINCT pr.id) FILTER (WHERE pr.status = 'active') as active_promoted_rules,
    COALESCE(AVG(aps.success_rate) FILTER (WHERE aps.promotion_eligible), 0) as avg_success_rate,
    COALESCE(SUM(aps.l1_resolutions), 0) as total_l1_resolutions,
    COALESCE(SUM(aps.l2_resolutions), 0) as total_l2_resolutions,
    COALESCE(SUM(aps.l3_resolutions), 0) as total_l3_resolutions,
    COALESCE(SUM(aps.total_occurrences), 0) as total_incidents
FROM sites s
LEFT JOIN aggregated_pattern_stats aps ON aps.site_id = s.site_id
LEFT JOIN learning_promotion_candidates lpc
    ON lpc.pattern_signature = aps.pattern_signature
    AND lpc.site_id = aps.site_id
LEFT JOIN promoted_rules pr ON pr.partner_id = s.partner_id
WHERE s.partner_id IS NOT NULL
GROUP BY s.partner_id;
