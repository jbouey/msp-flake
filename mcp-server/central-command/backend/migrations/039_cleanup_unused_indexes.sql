-- Migration 039: Drop unused indexes and add missing high-value indexes
-- Based on pg_stat_user_indexes analysis showing 0 scans on several indexes

-- Drop unused indexes (confirmed 0 scans in production)
DROP INDEX IF EXISTS idx_compliance_bundles_ots_pending;  -- 1.3 MB, 0 scans
DROP INDEX IF EXISTS idx_execution_telemetry_site;        -- 1.1 MB, 0 scans
DROP INDEX IF EXISTS idx_incidents_severity;              -- 48 KB, 0 scans
DROP INDEX IF EXISTS idx_appliance_fw_frameworks;         -- 24 KB, 0 scans
DROP INDEX IF EXISTS idx_audit_log_event_type;            -- 16 KB, 0 scans
DROP INDEX IF EXISTS idx_audit_log_actor;                 -- 16 KB, 0 scans
DROP INDEX IF EXISTS idx_evidence_check_type;             -- 16 KB, 0 scans
DROP INDEX IF EXISTS idx_evidence_outcome;                -- 16 KB, 0 scans
DROP INDEX IF EXISTS idx_evidence_bundle_id;              -- 16 KB, 0 scans
DROP INDEX IF EXISTS idx_l1_rules_enabled;                -- 16 KB, 0 scans
DROP INDEX IF EXISTS idx_appliances_reseller_id;          -- 16 KB, 0 scans
-- NOTE: ots_proofs_pkey kept (PK cannot be dropped safely)

-- Add missing high-value index for site_appliances JOIN
CREATE INDEX IF NOT EXISTS idx_site_appliances_site_id ON site_appliances(site_id);

-- Add composite index for incident filtering (used by fleet overview, dashboard)
CREATE INDEX IF NOT EXISTS idx_incidents_appliance_status ON incidents(appliance_id, status, reported_at DESC);

-- Add index for compliance bundle time queries
CREATE INDEX IF NOT EXISTS idx_compliance_bundles_site_checked ON compliance_bundles(site_id, checked_at DESC);
