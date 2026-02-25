-- Migration 057: Cross-client platform pattern aggregation
--
-- Aggregates L2 execution patterns across ALL sites/clients (no site_id key).
-- When a pattern succeeds 90%+ across 5+ distinct client orgs with 20+ total
-- occurrences, it auto-promotes to a platform L1 rule (source='platform').
--
-- Platform rules sync to ALL appliances via /agent/sync — no human approval needed
-- because they're proven across many independent environments.

CREATE TABLE IF NOT EXISTS platform_pattern_stats (
    id SERIAL PRIMARY KEY,
    pattern_key VARCHAR(255) UNIQUE NOT NULL,       -- incident_type:runbook_id (no hostname)
    incident_type VARCHAR(100) NOT NULL,
    runbook_id VARCHAR(255) NOT NULL,

    -- Cross-client metrics
    distinct_sites INTEGER DEFAULT 0,               -- how many different sites saw this
    distinct_orgs INTEGER DEFAULT 0,                -- how many different client orgs
    total_occurrences INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    success_rate FLOAT DEFAULT 0.0,
    avg_resolution_time_ms FLOAT DEFAULT 0.0,

    -- Promotion tracking
    promoted_at TIMESTAMPTZ,                        -- NULL = not yet promoted
    promoted_rule_id VARCHAR(255),                  -- l1_rules.rule_id when promoted

    -- Timestamps
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW()
);

-- Partial index for efficient promotion candidate lookup
CREATE INDEX IF NOT EXISTS idx_platform_pattern_promotion
    ON platform_pattern_stats(distinct_orgs DESC, success_rate DESC)
    WHERE promoted_at IS NULL;

-- Index for looking up by incident_type (dashboard views)
CREATE INDEX IF NOT EXISTS idx_platform_pattern_type
    ON platform_pattern_stats(incident_type);

COMMENT ON TABLE platform_pattern_stats IS
    'Cross-client aggregation of L2 healing patterns. When a pattern succeeds across 5+ client orgs, it auto-promotes to a platform L1 rule.';

-- Rollback:
-- DROP TABLE IF EXISTS platform_pattern_stats;
