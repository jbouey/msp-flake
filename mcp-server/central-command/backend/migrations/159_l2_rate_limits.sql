-- Migration 159: Per-(site, incident_type) L2 rate limits
--
-- Session 205 emergency: in erratic customer environments, the same
-- incident_type can recur hundreds of times per day. Each recurrence
-- that misses L1 was hitting L2 — driving unbounded API spend with
-- zero learning (no promotions because the runbook never stops the
-- pattern).
--
-- Fix: cap L2 calls per (site, incident_type, day) at a low number.
-- After N calls, fall back to L3 human review without an LLM call.
-- This guarantees: even a worst-case pattern storm cannot exceed
-- site_count * pattern_count * N_daily calls per day.
--
-- Worst case at default (N=3): 100 customers × 10 recurring patterns
-- × 3 L2 calls = 3000 calls/day = ~$3/day fleet-wide. Bounded.

CREATE TABLE IF NOT EXISTS l2_rate_limits (
    site_id VARCHAR(255) NOT NULL,
    incident_type VARCHAR(255) NOT NULL,
    day DATE NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0,
    first_call_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_call_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_runbook_id VARCHAR(255),
    last_confidence FLOAT,
    total_cost_usd NUMERIC(10, 6) DEFAULT 0,
    PRIMARY KEY (site_id, incident_type, day)
);

-- Enable row-level security (admin bypass + tenant isolation)
ALTER TABLE l2_rate_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE l2_rate_limits FORCE ROW LEVEL SECURITY;

CREATE POLICY admin_bypass ON l2_rate_limits
    FOR ALL USING (current_setting('app.is_admin', true) = 'true');

CREATE POLICY tenant_isolation ON l2_rate_limits
    FOR ALL USING (site_id = current_setting('app.current_tenant', true));

-- Index for the "has this pattern exhausted its budget today?" check
CREATE INDEX IF NOT EXISTS idx_l2_rate_limits_lookup
    ON l2_rate_limits(site_id, incident_type, day);

-- Index for cost reporting (sum by day across the fleet)
CREATE INDEX IF NOT EXISTS idx_l2_rate_limits_day
    ON l2_rate_limits(day DESC, total_cost_usd DESC);

COMMENT ON TABLE l2_rate_limits IS
    'Per-(site, incident_type, day) L2 call budget. Session 205: protects '
    'against unbounded API spend in erratic customer environments. After '
    'call_count reaches MAX_L2_CALLS_PER_PATTERN_PER_DAY (env, default 3), '
    'L2 returns L3 escalation without calling the LLM.';

COMMENT ON COLUMN l2_rate_limits.last_runbook_id IS
    'Runbook L2 recommended on the most recent call. If this same runbook '
    'comes back multiple times for the same pattern, L1 flywheel should '
    'promote it — it is our best answer, no need to re-ask the LLM.';
