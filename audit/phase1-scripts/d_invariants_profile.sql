-- §D — Substrate-engine invariant profile (3 hottest)
-- Run EXPLAIN (ANALYZE, BUFFERS) on each. Read-only; no cleanup needed.

\echo === D1: cross_org_relocate_chain_orphan (top SQL: SELECT sites WHERE prior set) ===
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT s.site_id,
       s.client_org_id::text AS current_org_id,
       s.prior_client_org_id::text AS prior_org_id
  FROM sites s
 WHERE s.prior_client_org_id IS NOT NULL;

\echo
\echo === D2: cross_org_relocate_chain_orphan (per-site lookup against requests table) ===
-- This runs once per row from D1; with N=10 customers and ~2-3 relocates,
-- this is bounded but the per-row N+1 shape is the audit P2.
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT 1
  FROM cross_org_site_relocate_requests
 WHERE site_id = 'physical-appliance-pilot-1aea78'
   AND source_org_id::text = '00000000-0000-0000-0000-000000000000'
   AND target_org_id::text = '00000000-0000-0000-0000-000000000001'
   AND status = 'completed'
 LIMIT 1;

\echo
\echo === D3: compliance_packets_stalled (cross-table window function over compliance_bundles) ===
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
WITH prior_month AS (
    SELECT EXTRACT(YEAR FROM (NOW() - INTERVAL '1 month'))::int AS y,
           EXTRACT(MONTH FROM (NOW() - INTERVAL '1 month'))::int AS m,
           date_trunc('month', NOW()) AS curr_month_start
),
active_sites AS (
    SELECT DISTINCT cb.site_id
      FROM compliance_bundles cb, prior_month pm
     WHERE EXTRACT(YEAR FROM cb.created_at) = pm.y
       AND EXTRACT(MONTH FROM cb.created_at) = pm.m
)
SELECT a.site_id, p.y AS year, p.m AS month
  FROM active_sites a, prior_month p
 WHERE NOT EXISTS (
     SELECT 1 FROM compliance_packets cp
      WHERE cp.site_id = a.site_id
        AND cp.year = p.y
        AND cp.month = p.m
        AND cp.framework = 'hipaa'
 )
   AND NOW() > p.curr_month_start + INTERVAL '24 hours'
 ORDER BY a.site_id
 LIMIT 50;

\echo
\echo === D4: heartbeat_write_divergence (LATERAL-style subquery) ===
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT
    sa.site_id,
    sa.appliance_id,
    sa.hostname,
    sa.last_checkin,
    (SELECT MAX(observed_at)
       FROM appliance_heartbeats
      WHERE appliance_id = sa.appliance_id) AS last_heartbeat
  FROM site_appliances sa
 WHERE sa.deleted_at IS NULL
   AND sa.status = 'online'
   AND sa.last_checkin > NOW() - INTERVAL '10 minutes';
