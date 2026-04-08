-- Migration 137: Move remediation_history from JSONB array to relational table
-- Reason: Unbounded JSONB arrays cause TOAST bloat, kill UPDATE performance,
-- and make remediation history non-queryable for HIPAA auditors.

-- Create the relational table
CREATE TABLE IF NOT EXISTS incident_remediation_steps (
    id SERIAL PRIMARY KEY,
    incident_id VARCHAR(255) NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    step_idx INTEGER NOT NULL DEFAULT 0,
    tier VARCHAR(10) NOT NULL CHECK (tier IN ('L1', 'L2', 'L3')),
    runbook_id VARCHAR(255),
    result VARCHAR(100) NOT NULL,
    confidence FLOAT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_remediation_steps_incident
    ON incident_remediation_steps(incident_id, step_idx);
CREATE INDEX IF NOT EXISTS idx_remediation_steps_created
    ON incident_remediation_steps(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_remediation_steps_tier
    ON incident_remediation_steps(tier, created_at DESC);

-- RLS: inherit incident's site_id isolation via JOIN
ALTER TABLE incident_remediation_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE incident_remediation_steps FORCE ROW LEVEL SECURITY;

CREATE POLICY admin_bypass ON incident_remediation_steps
    FOR ALL
    USING (current_setting('app.is_admin', true) = 'true');

CREATE POLICY tenant_isolation ON incident_remediation_steps
    FOR ALL
    USING (incident_id IN (
        SELECT incident_id FROM incidents
        WHERE site_id = current_setting('app.current_tenant', true)
    ));

-- Migrate existing JSONB data into the new table
INSERT INTO incident_remediation_steps (incident_id, step_idx, tier, runbook_id, result, confidence, created_at)
SELECT
    i.incident_id,
    (elem_idx.idx - 1) as step_idx,
    COALESCE(elem.value->>'tier', 'L1') as tier,
    elem.value->>'runbook_id' as runbook_id,
    COALESCE(elem.value->>'result', 'unknown') as result,
    (elem.value->>'confidence')::float as confidence,
    COALESCE(
        (elem.value->>'timestamp')::timestamptz,
        i.created_at
    ) as created_at
FROM incidents i,
    jsonb_array_elements(COALESCE(i.remediation_history, '[]'::jsonb)) WITH ORDINALITY AS elem_idx(elem, idx),
    LATERAL (SELECT elem_idx.elem AS value) AS elem
WHERE i.remediation_history IS NOT NULL
    AND jsonb_array_length(i.remediation_history) > 0
ON CONFLICT DO NOTHING;

-- Keep the old column for now (will be dropped in a future migration after
-- verifying all code paths use the new table). This avoids breaking any
-- code that hasn't been updated yet.
-- ALTER TABLE incidents DROP COLUMN remediation_history;
