-- Migration 099: Incident remediation state tracking
-- Prevents infinite L2 loops, enforces attempt budgets

ALTER TABLE incidents ADD COLUMN IF NOT EXISTS remediation_attempts INTEGER DEFAULT 0;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS remediation_history JSONB DEFAULT '[]'::jsonb;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS remediation_exhausted BOOLEAN DEFAULT FALSE;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS context_hash VARCHAR(64);

COMMENT ON COLUMN incidents.remediation_attempts IS 'Total L1+L2 remediation attempts for this incident';
COMMENT ON COLUMN incidents.remediation_history IS 'Array of {tier, runbook_id, result, error, timestamp} entries';
COMMENT ON COLUMN incidents.remediation_exhausted IS 'True when attempt budget exhausted — stop processing';
COMMENT ON COLUMN incidents.context_hash IS 'SHA256 of incident context — skip L2 if unchanged';

CREATE INDEX IF NOT EXISTS idx_incidents_exhausted
ON incidents (site_id, remediation_exhausted)
WHERE remediation_exhausted = false AND status != 'resolved';
