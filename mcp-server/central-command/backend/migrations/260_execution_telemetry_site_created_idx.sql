-- Migration 260: (site_id, created_at DESC) composite index on
-- execution_telemetry (Session 213 F7-followup, round-table P1).
--
-- BACKGROUND:
--   F7 round-table flagged that the flywheel-diagnostic endpoint
--   runs three count queries per call against execution_telemetry,
--   filtering by `site_id = $1 AND created_at > NOW() - INTERVAL
--   '24 hours'`. Existing indexes:
--     * idx_execution_telemetry_created_at — covers created_at
--     * idx_execution_telemetry_appliance — covers (appliance_id,
--       created_at DESC)
--     * idx_execution_telemetry_failures — partial on (site_id,
--       failure_type) WHERE success=false AND failure_type IS NOT NULL
--     * idx_execution_telemetry_runbook — (runbook_id, success)
--
--   None of these support a "rows for this site_id in last 24h" scan.
--   This index PRIMARILY targets the diagnostic endpoint (3 queries
--   with `site_id = $1` as leading equality + created_at range —
--   exactly the index's strong shape).
--
--   The flywheel_orphan_telemetry substrate invariant query
--   (assertions.py:1469-1499) has `created_at > NOW() - INTERVAL '24h'`
--   as its leading predicate and a `site_id NOT IN (...)` anti-join,
--   so it benefits LESS directly — the planner will likely still pick
--   idx_execution_telemetry_created_at for the leading scan and use
--   this index only for the GROUP BY post-filter. Acceptable; the
--   diagnostic endpoint is the load-bearing caller.
--
-- DESIGN:
--   * Composite (site_id, created_at DESC) — covers the diagnostic
--     query AND the orphan-telemetry detection.
--   * Plain btree, no partial filter — every site_id is a legitimate
--     filter target, and the orphan detector specifically scans for
--     site_ids NOT in site_appliances (so a `WHERE site_id IN (live)`
--     partial wouldn't help).
--   * CREATE INDEX (no CONCURRENTLY) — table is 41 MB / 29K rows.
--     Concurrently is for tables where a brief lock is unacceptable;
--     at this scale the plain create finishes in well under a second.
--     For tables >100MB or with high write volume, CONCURRENTLY is
--     the right call.

CREATE INDEX IF NOT EXISTS idx_execution_telemetry_site_created
    ON execution_telemetry (site_id, created_at DESC);

COMMENT ON INDEX idx_execution_telemetry_site_created IS
    'Supports flywheel-diagnostic per-site 24h count queries (3 callers in flywheel_diagnostic.py with site_id=$1 + created_at range). flywheel_orphan_telemetry invariant has created_at as leading predicate so benefits indirectly via GROUP BY post-filter. Session 213 F7-followup, mig 260.';

-- Audit-log entry.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
SELECT
    'substrate.index_added',
    'execution_telemetry',
    'jbouey2006@gmail.com',
    jsonb_build_object(
        'migration', '260',
        'index', 'idx_execution_telemetry_site_created',
        'columns', ARRAY['site_id', 'created_at DESC'],
        'session', '213',
        'related_findings', ARRAY['F7-followup'],
        'reason', 'Supports the GET /api/admin/sites/{id}/flywheel-diagnostic endpoint + flywheel_orphan_telemetry invariant per-site count queries.'
    ),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM admin_audit_log
     WHERE action = 'substrate.index_added'
       AND target = 'execution_telemetry'
       AND details->>'index' = 'idx_execution_telemetry_site_created'
);
