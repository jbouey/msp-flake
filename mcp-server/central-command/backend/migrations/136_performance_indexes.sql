-- Migration 136: Add missing performance indexes identified by DB round table review
-- These indexes prevent table scans on high-volume query patterns at scale
-- NOTE: CONCURRENTLY cannot run inside a transaction; run each statement separately

-- Incidents: timeline queries (dashboard, site detail, API)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_incidents_site_created
  ON incidents(site_id, created_at DESC);

-- Incidents: resolved filtering by severity (dashboard KPIs, healing pipeline)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_incidents_site_resolved_severity
  ON incidents(site_id, severity) WHERE status = 'resolved';

-- Orders: audit trail queries by site
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_orders_site_created
  ON orders(site_id, created_at DESC);

-- Portal access log: cleanup/archival queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_portal_access_log_accessed
  ON portal_access_log(accessed_at DESC);
