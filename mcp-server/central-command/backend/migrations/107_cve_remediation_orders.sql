-- ============================================================================
-- Migration 107: CVE Remediation Orders
-- ============================================================================
-- Adds remediation_order_id to cve_fleet_matches to link CVE matches
-- to the fleet healing orders dispatched for remediation.

ALTER TABLE cve_fleet_matches ADD COLUMN IF NOT EXISTS remediation_order_id UUID;

CREATE INDEX IF NOT EXISTS idx_cve_fleet_remediation_order
    ON cve_fleet_matches(remediation_order_id)
    WHERE remediation_order_id IS NOT NULL;
