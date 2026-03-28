-- ============================================================================
-- Migration 105: CVE Auto-Remediation Support
-- ============================================================================
-- Adds remediation tracking columns to cve_fleet_matches so the CVE
-- remediation engine can record which runbooks were generated and whether
-- auto-remediation was attempted per match.

ALTER TABLE cve_fleet_matches ADD COLUMN IF NOT EXISTS remediation_status TEXT;
ALTER TABLE cve_fleet_matches ADD COLUMN IF NOT EXISTS remediation_runbook_id TEXT;
ALTER TABLE cve_fleet_matches ADD COLUMN IF NOT EXISTS remediation_attempted_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_cve_fleet_remediation_status
    ON cve_fleet_matches(remediation_status)
    WHERE remediation_status IS NOT NULL;

COMMENT ON COLUMN cve_fleet_matches.remediation_status IS 'Auto-remediation status: pending, runbook_generated, auto_remediated, skipped, failed';
COMMENT ON COLUMN cve_fleet_matches.remediation_runbook_id IS 'Generated runbook ID (RB-CVE-*) linked to this match';
COMMENT ON COLUMN cve_fleet_matches.remediation_attempted_at IS 'Timestamp of last remediation attempt';
