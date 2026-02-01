-- Migration 033: Performance Indexes
-- Date: 2026-01-31
-- Purpose: Add missing indexes identified in production readiness audit

BEGIN;

-- =============================================================================
-- COMPLIANCE BUNDLES - Most frequently queried table
-- =============================================================================

-- Index for compliance score lookups (site_id + time ordering)
CREATE INDEX IF NOT EXISTS idx_compliance_bundles_site_time
ON compliance_bundles(site_id, checked_at DESC);

-- Index for distinct site_id queries
CREATE INDEX IF NOT EXISTS idx_compliance_bundles_site_id
ON compliance_bundles(site_id);

-- =============================================================================
-- INCIDENTS - High volume query target
-- =============================================================================

-- Index for incident lookups by appliance
CREATE INDEX IF NOT EXISTS idx_incidents_appliance_id
ON incidents(appliance_id);

-- Index for incident time-range queries
CREATE INDEX IF NOT EXISTS idx_incidents_reported_at
ON incidents(reported_at DESC);

-- Composite index for common query pattern (site + time + status)
CREATE INDEX IF NOT EXISTS idx_incidents_site_time_status
ON incidents(site_id, reported_at DESC, status);

-- =============================================================================
-- ADMIN ORDERS - Healing metrics queries
-- =============================================================================

-- Index for order status filtering
CREATE INDEX IF NOT EXISTS idx_admin_orders_site_status
ON admin_orders(site_id, status);

-- Index for pending orders (partial index for efficiency)
CREATE INDEX IF NOT EXISTS idx_admin_orders_pending
ON admin_orders(appliance_id, status)
WHERE status = 'pending';

-- =============================================================================
-- SITE APPLIANCES - Fleet overview queries
-- =============================================================================

-- Index for site_id lookups
CREATE INDEX IF NOT EXISTS idx_site_appliances_site_id
ON site_appliances(site_id);

-- Index for status filtering
CREATE INDEX IF NOT EXISTS idx_site_appliances_status
ON site_appliances(status);

-- =============================================================================
-- PATTERNS - Learning system queries
-- =============================================================================

-- Index for pattern status (pending promotions)
CREATE INDEX IF NOT EXISTS idx_patterns_status
ON patterns(status)
WHERE status = 'pending';

-- =============================================================================
-- EXECUTION TELEMETRY - Time-based purge queries
-- =============================================================================

-- Index for time-based deletion
CREATE INDEX IF NOT EXISTS idx_execution_telemetry_created_at
ON execution_telemetry(created_at);

COMMIT;

-- Add comment for audit
COMMENT ON INDEX idx_compliance_bundles_site_time IS 'Added for production readiness - compliance score lookups';
COMMENT ON INDEX idx_incidents_appliance_id IS 'Added for production readiness - incident JOIN performance';
COMMENT ON INDEX idx_admin_orders_site_status IS 'Added for production readiness - healing metrics queries';

-- DOWN
-- DROP INDEX IF EXISTS idx_compliance_bundles_site_time;
-- DROP INDEX IF EXISTS idx_compliance_bundles_site_id;
-- DROP INDEX IF EXISTS idx_incidents_appliance_id;
-- DROP INDEX IF EXISTS idx_incidents_reported_at;
-- DROP INDEX IF EXISTS idx_incidents_site_time_status;
-- DROP INDEX IF EXISTS idx_admin_orders_site_status;
-- DROP INDEX IF EXISTS idx_admin_orders_pending;
-- DROP INDEX IF EXISTS idx_site_appliances_site_id;
-- DROP INDEX IF EXISTS idx_site_appliances_status;
-- DROP INDEX IF EXISTS idx_patterns_status;
-- DROP INDEX IF EXISTS idx_execution_telemetry_created_at;
