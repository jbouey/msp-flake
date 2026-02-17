-- Migration 047: Add indexes for audit-identified slow queries
-- Improves compliance_bundles queries that filter by appliance + time + type

BEGIN;

CREATE INDEX IF NOT EXISTS idx_compliance_bundles_appliance_type
    ON compliance_bundles(appliance_id, reported_at DESC, check_type);

COMMIT;
