-- Migration 031: Learning System Sync Infrastructure
-- Enables bidirectional sync between agents and Central Command for learning data
-- Created: 2026-01-26

-- ============================================================================
-- AGGREGATED PATTERN STATISTICS
-- Cross-appliance pattern aggregation for L2->L1 promotion decisions
-- ============================================================================
CREATE TABLE IF NOT EXISTS aggregated_pattern_stats (
    id SERIAL PRIMARY KEY,
    site_id VARCHAR(255) NOT NULL,
    pattern_signature VARCHAR(64) NOT NULL,

    -- Aggregated counts (sum from all appliances at this site)
    total_occurrences INTEGER DEFAULT 0,
    l1_resolutions INTEGER DEFAULT 0,
    l2_resolutions INTEGER DEFAULT 0,
    l3_resolutions INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    total_resolution_time_ms FLOAT DEFAULT 0.0,

    -- Computed metrics
    success_rate FLOAT DEFAULT 0.0,
    avg_resolution_time_ms FLOAT DEFAULT 0.0,

    -- Most common successful action across all appliances
    recommended_action VARCHAR(255),
    promotion_eligible BOOLEAN DEFAULT FALSE,

    -- Timestamps
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    last_synced_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(site_id, pattern_signature)
);

COMMENT ON TABLE aggregated_pattern_stats IS 'Cross-appliance aggregated pattern statistics for L2->L1 promotion';
COMMENT ON COLUMN aggregated_pattern_stats.pattern_signature IS 'SHA256[:16] hash of normalized pattern';
COMMENT ON COLUMN aggregated_pattern_stats.promotion_eligible IS 'True if pattern meets criteria: >=5 occurrences, >=3 L2 resolutions, >=90% success';

-- ============================================================================
-- APPLIANCE PATTERN SYNC TRACKING
-- Track last sync time per appliance for delta syncs
-- ============================================================================
CREATE TABLE IF NOT EXISTS appliance_pattern_sync (
    id SERIAL PRIMARY KEY,
    appliance_id VARCHAR(255) NOT NULL,
    site_id VARCHAR(255) NOT NULL,
    synced_at TIMESTAMPTZ DEFAULT NOW(),
    patterns_received INTEGER DEFAULT 0,
    patterns_merged INTEGER DEFAULT 0,
    sync_duration_ms INTEGER,
    sync_status VARCHAR(20) DEFAULT 'success',
    error_message TEXT,

    UNIQUE(appliance_id)
);

COMMENT ON TABLE appliance_pattern_sync IS 'Track last sync time per appliance for incremental syncs';

-- ============================================================================
-- PROMOTED RULE DEPLOYMENTS
-- Audit trail of which promoted rules are deployed to which appliances
-- ============================================================================
CREATE TABLE IF NOT EXISTS promoted_rule_deployments (
    id SERIAL PRIMARY KEY,
    rule_id VARCHAR(255) NOT NULL,
    pattern_signature VARCHAR(64),
    site_id VARCHAR(255) NOT NULL,
    appliance_id VARCHAR(255) NOT NULL,
    deployed_at TIMESTAMPTZ DEFAULT NOW(),
    deployment_method VARCHAR(50) DEFAULT 'sync',  -- 'sync', 'command', 'manual'
    rule_yaml TEXT NOT NULL,
    acknowledged_at TIMESTAMPTZ,
    deployment_status VARCHAR(20) DEFAULT 'pending',  -- 'pending', 'deployed', 'failed', 'rolled_back'

    UNIQUE(rule_id, appliance_id)
);

COMMENT ON TABLE promoted_rule_deployments IS 'Audit trail of promoted rule deployments to appliances';
COMMENT ON COLUMN promoted_rule_deployments.deployment_method IS 'How rule was deployed: sync (periodic pull), command (server push), manual';

-- ============================================================================
-- EXECUTION TELEMETRY
-- Rich execution data from agents for learning engine analysis
-- ============================================================================
CREATE TABLE IF NOT EXISTS execution_telemetry (
    id SERIAL PRIMARY KEY,
    execution_id VARCHAR(255) UNIQUE NOT NULL,
    incident_id VARCHAR(255),
    site_id VARCHAR(255) NOT NULL,
    appliance_id VARCHAR(255) NOT NULL,
    runbook_id VARCHAR(255) NOT NULL,
    hostname VARCHAR(255) NOT NULL,
    platform VARCHAR(50),
    incident_type VARCHAR(100),

    -- Timing
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_seconds FLOAT,

    -- Results
    success BOOLEAN NOT NULL,
    status VARCHAR(20),  -- success, failure, partial
    verification_passed BOOLEAN,
    confidence FLOAT DEFAULT 0.0,
    resolution_level VARCHAR(10),  -- L1, L2, L3

    -- State capture (JSONB for flexibility)
    state_before JSONB DEFAULT '{}'::jsonb,
    state_after JSONB DEFAULT '{}'::jsonb,
    state_diff JSONB DEFAULT '{}'::jsonb,

    -- Execution trace
    executed_steps JSONB DEFAULT '[]'::jsonb,

    -- Error details
    error_message TEXT,
    error_step INTEGER,
    error_traceback TEXT,
    failure_type VARCHAR(50),  -- wrong_diagnosis, wrong_runbook, runbook_insufficient, etc.
    retry_count INTEGER DEFAULT 0,

    -- Learning signals
    was_correct_runbook BOOLEAN,
    was_correct_diagnosis BOOLEAN,
    manual_intervention_needed BOOLEAN DEFAULT FALSE,
    human_feedback TEXT,

    -- Metadata
    evidence_bundle_id VARCHAR(255),
    tags JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE execution_telemetry IS 'Rich execution data from agents for learning engine';
COMMENT ON COLUMN execution_telemetry.state_before IS 'System state captured before healing action';
COMMENT ON COLUMN execution_telemetry.state_after IS 'System state captured after healing action';
COMMENT ON COLUMN execution_telemetry.failure_type IS 'Learning classification: wrong_diagnosis, wrong_runbook, runbook_insufficient, environment_difference, external_dependency, permission_denied';

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Aggregated pattern stats indexes
CREATE INDEX IF NOT EXISTS idx_agg_patterns_site ON aggregated_pattern_stats(site_id);
CREATE INDEX IF NOT EXISTS idx_agg_patterns_eligible ON aggregated_pattern_stats(site_id, promotion_eligible)
    WHERE promotion_eligible = TRUE;
CREATE INDEX IF NOT EXISTS idx_agg_patterns_signature ON aggregated_pattern_stats(pattern_signature);
CREATE INDEX IF NOT EXISTS idx_agg_patterns_last_seen ON aggregated_pattern_stats(last_seen DESC);

-- Appliance sync tracking indexes
CREATE INDEX IF NOT EXISTS idx_appliance_sync_site ON appliance_pattern_sync(site_id);
CREATE INDEX IF NOT EXISTS idx_appliance_sync_time ON appliance_pattern_sync(synced_at DESC);

-- Rule deployments indexes
CREATE INDEX IF NOT EXISTS idx_rule_deployments_site ON promoted_rule_deployments(site_id);
CREATE INDEX IF NOT EXISTS idx_rule_deployments_appliance ON promoted_rule_deployments(appliance_id);
CREATE INDEX IF NOT EXISTS idx_rule_deployments_pending ON promoted_rule_deployments(site_id, deployment_status)
    WHERE deployment_status = 'pending';

-- Execution telemetry indexes
CREATE INDEX IF NOT EXISTS idx_execution_telemetry_site ON execution_telemetry(site_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_execution_telemetry_runbook ON execution_telemetry(runbook_id, success);
CREATE INDEX IF NOT EXISTS idx_execution_telemetry_incident ON execution_telemetry(incident_id);
CREATE INDEX IF NOT EXISTS idx_execution_telemetry_appliance ON execution_telemetry(appliance_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_execution_telemetry_failures ON execution_telemetry(site_id, failure_type)
    WHERE success = FALSE AND failure_type IS NOT NULL;

-- ============================================================================
-- VIEWS
-- ============================================================================

-- View: Patterns ready for promotion review
CREATE OR REPLACE VIEW v_promotion_ready_patterns AS
SELECT
    aps.site_id,
    aps.pattern_signature,
    aps.total_occurrences,
    aps.l2_resolutions,
    aps.success_rate,
    aps.recommended_action,
    aps.first_seen,
    aps.last_seen,
    s.clinic_name as site_name,
    CASE
        WHEN lpc.approval_status IS NOT NULL THEN lpc.approval_status
        ELSE 'not_submitted'
    END as approval_status
FROM aggregated_pattern_stats aps
LEFT JOIN sites s ON s.site_id = aps.site_id
LEFT JOIN learning_promotion_candidates lpc ON lpc.pattern_signature = aps.pattern_signature AND lpc.site_id = aps.site_id
WHERE aps.promotion_eligible = TRUE
ORDER BY aps.success_rate DESC, aps.total_occurrences DESC;

-- View: Recent execution failures for learning analysis
CREATE OR REPLACE VIEW v_learning_failures AS
SELECT
    et.execution_id,
    et.site_id,
    et.runbook_id,
    et.incident_type,
    et.failure_type,
    et.error_message,
    et.state_before,
    et.state_after,
    et.created_at,
    s.clinic_name as site_name
FROM execution_telemetry et
LEFT JOIN sites s ON s.site_id = et.site_id
WHERE et.success = FALSE
    AND et.created_at > NOW() - INTERVAL '30 days'
ORDER BY et.created_at DESC;
