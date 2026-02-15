-- Migration 045: Audit fixes
--
-- Fixes:
-- 1. Add missing indexes on evidence_bundles, sites, runbooks, patterns,
--    aggregated_pattern_stats, notifications
-- 2. Add trigger to update l1_rules counters from execution_telemetry inserts
--    (matches by runbook_id, uses execution_telemetry.success boolean)
-- 3. Create appliance_commands table (required by promotion deployment pipeline)
--    Columns match learning_api.py: appliance_id, command_type, params

BEGIN;

-- ============================================================================
-- 1. Add missing indexes (evidence_bundles had 518K seq scans vs 18 idx scans)
-- ============================================================================

-- evidence_bundles: hot path for every checkin cycle (keyed by appliance_id, not site_id)
CREATE INDEX IF NOT EXISTS idx_evidence_bundles_appliance_id
    ON evidence_bundles(appliance_id);
CREATE INDEX IF NOT EXISTS idx_evidence_bundles_created_at
    ON evidence_bundles(created_at DESC);

-- sites: frequently joined
CREATE INDEX IF NOT EXISTS idx_sites_partner_id
    ON sites(partner_id) WHERE partner_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sites_status
    ON sites(status);

-- runbooks: 66K seq scans
CREATE INDEX IF NOT EXISTS idx_runbooks_check_type
    ON runbooks(check_type);
CREATE INDEX IF NOT EXISTS idx_runbooks_category
    ON runbooks(category);

-- patterns: 76K seq scans (pattern_signature index)
CREATE INDEX IF NOT EXISTS idx_patterns_pattern_signature
    ON patterns(pattern_signature);

-- aggregated_pattern_stats: promotion queries
CREATE INDEX IF NOT EXISTS idx_aps_promotion_eligible
    ON aggregated_pattern_stats(promotion_eligible) WHERE promotion_eligible = true;
CREATE INDEX IF NOT EXISTS idx_aps_site_id
    ON aggregated_pattern_stats(site_id);

-- l1_rules: lookup by runbook_id (used by counter trigger)
CREATE INDEX IF NOT EXISTS idx_l1_rules_runbook_id
    ON l1_rules(runbook_id);

-- notifications: 3K seq scans
CREATE INDEX IF NOT EXISTS idx_notifications_site_id
    ON notifications(site_id);

-- ============================================================================
-- 2. Trigger to update l1_rules counters from execution_telemetry
--    execution_telemetry columns: runbook_id, resolution_level, success (bool)
--    l1_rules columns: runbook_id, match_count, success_count, failure_count
--    l1_rules.success_rate is GENERATED ALWAYS — do NOT set it explicitly
-- ============================================================================

CREATE OR REPLACE FUNCTION update_l1_rule_counters()
RETURNS TRIGGER AS $$
BEGIN
    -- Only process L1 resolution level entries with a runbook_id
    IF NEW.resolution_level = 'L1' AND NEW.runbook_id IS NOT NULL THEN
        -- Agent records rule_id in execution_telemetry.runbook_id field
        -- (see CLAUDE.md: "execution_telemetry.runbook_id uses internal IDs")
        -- Match against l1_rules.rule_id, not l1_rules.runbook_id
        -- success_rate is a GENERATED ALWAYS column (auto-computed)
        UPDATE l1_rules SET
            match_count = match_count + 1,
            success_count = success_count + CASE WHEN NEW.success THEN 1 ELSE 0 END,
            failure_count = failure_count + CASE WHEN NOT NEW.success THEN 1 ELSE 0 END
        WHERE rule_id = NEW.runbook_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_l1_counters ON execution_telemetry;
CREATE TRIGGER trg_update_l1_counters
    AFTER INSERT ON execution_telemetry
    FOR EACH ROW
    EXECUTE FUNCTION update_l1_rule_counters();

-- ============================================================================
-- 3. Create appliance_commands table (promotion deployment pipeline)
--    learning_api.py uses: INSERT INTO appliance_commands (appliance_id, command_type, params, ...)
-- ============================================================================

CREATE TABLE IF NOT EXISTS appliance_commands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    appliance_id VARCHAR(255) NOT NULL,
    command_type VARCHAR(50) NOT NULL,  -- 'sync_promoted_rule', 'update_config', etc.
    params JSONB NOT NULL DEFAULT '{}',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending, delivered, acknowledged, failed
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at TIMESTAMPTZ,
    acknowledged_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '24 hours'
);

-- Unique constraint for ON CONFLICT in learning_api.py
CREATE UNIQUE INDEX IF NOT EXISTS idx_appliance_commands_unique
    ON appliance_commands(appliance_id, command_type, params);

-- Fast lookup for pending commands per appliance
CREATE INDEX IF NOT EXISTS idx_appliance_commands_pending
    ON appliance_commands(appliance_id, status) WHERE status = 'pending';

COMMIT;
