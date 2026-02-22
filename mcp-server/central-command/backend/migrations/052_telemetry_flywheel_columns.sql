-- Migration 052: Add flywheel columns to execution_telemetry
--
-- Problem: Go daemon sends cost_usd, tokens (input+output), confidence, and
-- reasoning in telemetry but these fields are NOT stored in execution_telemetry.
-- The learning flywheel can't calculate ROI or track L2 cost savings.
-- Also missing: pattern_signature for correlating executions with patterns.
--
-- Fixes:
--   A. Add cost_usd, input_tokens, output_tokens columns
--   B. Add pattern_signature for flywheel correlation
--   C. Add reasoning column for L2 decision audit trail
--   D. Add chaos_campaign_id for chaos lab → flywheel bridge
--   E. Add telemetry_archive table for long-term retention

BEGIN;

-- ============================================================================
-- A. Cost and token tracking
-- ============================================================================

ALTER TABLE execution_telemetry ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10, 6) DEFAULT 0;
ALTER TABLE execution_telemetry ADD COLUMN IF NOT EXISTS input_tokens INTEGER DEFAULT 0;
ALTER TABLE execution_telemetry ADD COLUMN IF NOT EXISTS output_tokens INTEGER DEFAULT 0;

-- ============================================================================
-- B. Pattern signature for flywheel correlation
-- ============================================================================

ALTER TABLE execution_telemetry ADD COLUMN IF NOT EXISTS pattern_signature VARCHAR(255);

-- ============================================================================
-- C. Reasoning from L2 decisions
-- ============================================================================

ALTER TABLE execution_telemetry ADD COLUMN IF NOT EXISTS reasoning TEXT;

-- ============================================================================
-- D. Chaos campaign tagging (for lab → flywheel bridge)
-- ============================================================================

ALTER TABLE execution_telemetry ADD COLUMN IF NOT EXISTS chaos_campaign_id VARCHAR(255);

-- ============================================================================
-- E. Telemetry archive table for long-term retention
--    Before 90-day purge, aggregate stats are rolled into this table.
--    Keeps per-pattern aggregated data indefinitely for promotion audit.
-- ============================================================================

CREATE TABLE IF NOT EXISTS telemetry_archive (
    id SERIAL PRIMARY KEY,
    pattern_signature VARCHAR(255) NOT NULL,
    site_id VARCHAR(255) NOT NULL,
    runbook_id VARCHAR(255),
    incident_type VARCHAR(100),
    resolution_level VARCHAR(10),

    -- Aggregated stats for the archived period
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    total_executions INTEGER DEFAULT 0,
    successful_executions INTEGER DEFAULT 0,
    failed_executions INTEGER DEFAULT 0,

    -- Cost tracking
    total_cost_usd NUMERIC(10, 4) DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,

    -- Timing
    avg_duration_seconds FLOAT DEFAULT 0,
    min_duration_seconds FLOAT DEFAULT 0,
    max_duration_seconds FLOAT DEFAULT 0,

    -- Failure analysis
    failure_types JSONB DEFAULT '{}'::jsonb,  -- {"wrong_diagnosis": 3, "runbook_insufficient": 1}

    -- Chaos lab correlation
    chaos_validated BOOLEAN DEFAULT false,
    chaos_executions INTEGER DEFAULT 0,

    archived_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(pattern_signature, site_id, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_telemetry_archive_site ON telemetry_archive(site_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_archive_pattern ON telemetry_archive(pattern_signature);
CREATE INDEX IF NOT EXISTS idx_telemetry_archive_period ON telemetry_archive(period_start, period_end);

-- ============================================================================
-- F. Indexes for new columns
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_execution_telemetry_pattern ON execution_telemetry(pattern_signature)
    WHERE pattern_signature IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_execution_telemetry_cost ON execution_telemetry(cost_usd)
    WHERE cost_usd > 0;
CREATE INDEX IF NOT EXISTS idx_execution_telemetry_chaos ON execution_telemetry(chaos_campaign_id)
    WHERE chaos_campaign_id IS NOT NULL;

-- ============================================================================
-- G. Backfill pattern_signature from existing telemetry
--    Pattern signature format: incident_type:incident_type:hostname
-- ============================================================================

UPDATE execution_telemetry
SET pattern_signature = incident_type || ':' || incident_type || ':' || hostname
WHERE pattern_signature IS NULL
  AND incident_type IS NOT NULL
  AND hostname IS NOT NULL;

COMMIT;
