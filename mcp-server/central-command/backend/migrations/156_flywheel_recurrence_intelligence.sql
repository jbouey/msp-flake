-- Migration 156: Flywheel recurrence intelligence
-- Adds infrastructure for recurrence-aware escalation, auto-promotion,
-- and cross-incident correlation.
--
-- Round Table Session 205: "The flywheel should see recurrence and think
-- 'I got something for that' — not keep repeating the same failing fix."

-- 1. Tag L2 decisions with escalation reason
ALTER TABLE l2_decisions ADD COLUMN IF NOT EXISTS escalation_reason VARCHAR(50);
COMMENT ON COLUMN l2_decisions.escalation_reason IS 'Why this went to L2: normal (no L1 match), recurrence (L1 keeps failing), keyword (fallback match failed)';

-- 2. Pre-computed recurrence velocity per (site_id, incident_type)
CREATE TABLE IF NOT EXISTS incident_recurrence_velocity (
    id SERIAL PRIMARY KEY,
    site_id VARCHAR(255) NOT NULL,
    incident_type VARCHAR(255) NOT NULL,
    -- Rolling window counts
    resolved_1h INTEGER NOT NULL DEFAULT 0,
    resolved_4h INTEGER NOT NULL DEFAULT 0,
    resolved_24h INTEGER NOT NULL DEFAULT 0,
    resolved_7d INTEGER NOT NULL DEFAULT 0,
    -- Velocity = incidents per hour over the 4h window
    velocity_per_hour FLOAT NOT NULL DEFAULT 0.0,
    -- Is this a chronic recurrence pattern?
    is_chronic BOOLEAN NOT NULL DEFAULT false,
    -- Last L1 runbook used (for recurrence bypass tracking)
    last_l1_runbook VARCHAR(255),
    -- When a recurrence-L2 fix stopped recurrence
    recurrence_broken_at TIMESTAMPTZ,
    recurrence_broken_by_runbook VARCHAR(255),
    -- Timestamps
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (site_id, incident_type)
);

CREATE INDEX IF NOT EXISTS idx_recurrence_velocity_chronic
    ON incident_recurrence_velocity(is_chronic, velocity_per_hour DESC);

-- RLS
ALTER TABLE incident_recurrence_velocity ENABLE ROW LEVEL SECURITY;
ALTER TABLE incident_recurrence_velocity FORCE ROW LEVEL SECURITY;
CREATE POLICY admin_bypass ON incident_recurrence_velocity
    FOR ALL USING (current_setting('app.is_admin', true) = 'true');
CREATE POLICY tenant_isolation ON incident_recurrence_velocity
    FOR ALL USING (site_id = current_setting('app.current_tenant', true));

-- 3. Cross-incident correlation pairs
CREATE TABLE IF NOT EXISTS incident_correlation_pairs (
    id SERIAL PRIMARY KEY,
    site_id VARCHAR(255) NOT NULL,
    -- incident A resolved, then incident B appears within correlation_window
    incident_type_a VARCHAR(255) NOT NULL,
    incident_type_b VARCHAR(255) NOT NULL,
    -- How many times this A→B pattern has occurred
    co_occurrence_count INTEGER NOT NULL DEFAULT 1,
    -- Average time between A resolution and B appearance (seconds)
    avg_gap_seconds FLOAT NOT NULL DEFAULT 0.0,
    -- Window in which we look for correlation
    correlation_window_minutes INTEGER NOT NULL DEFAULT 10,
    -- Confidence: co_occurrences / total_a_resolutions
    confidence FLOAT NOT NULL DEFAULT 0.0,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (site_id, incident_type_a, incident_type_b)
);

-- RLS
ALTER TABLE incident_correlation_pairs ENABLE ROW LEVEL SECURITY;
ALTER TABLE incident_correlation_pairs FORCE ROW LEVEL SECURITY;
CREATE POLICY admin_bypass ON incident_correlation_pairs
    FOR ALL USING (current_setting('app.is_admin', true) = 'true');
CREATE POLICY tenant_isolation ON incident_correlation_pairs
    FOR ALL USING (site_id = current_setting('app.current_tenant', true));
