-- Migration 136: Add missing performance indexes identified by DB round table review
-- These indexes prevent table scans on high-volume query patterns at scale

-- Incidents: timeline queries (dashboard, site detail, API)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_incidents_site_created
  ON incidents(site_id, created_at DESC);

-- Incidents: open/resolved filtering (dashboard KPIs, healing pipeline)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_incidents_site_resolved_severity
  ON incidents(site_id, resolved, severity);

-- Orders: audit trail queries by site
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_orders_site_created
  ON orders(site_id, created_at DESC);

-- Evidence bundles: retention job queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_evidence_bundles_retention
  ON evidence_bundles(site_id, retention_until)
  WHERE archived = false;

-- Portal access log: cleanup/archival queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_portal_access_log_accessed
  ON portal_access_log(accessed_at DESC);
