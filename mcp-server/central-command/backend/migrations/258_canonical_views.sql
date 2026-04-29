-- Migration 258: canonical-aliasing read views (Session 213 F1-followup).
--
-- Companion to migration 256's `canonical_site_id()` function. These
-- views give callers a syntactically-cleaner read path: filter or JOIN
-- by `canonical_site_id` instead of writing `WHERE canonical_site_id(t.site_id) = $1`
-- everywhere.
--
-- DESIGN:
--   Each view projects the underlying table's columns AS-IS plus a new
--   `canonical_site_id` column resolved through the chain. Callers that
--   want canonical semantics filter by `canonical_site_id`; callers that
--   want raw semantics still filter by `site_id`. New columns added to
--   the underlying tables flow through automatically (`SELECT t.*` in
--   the view body), preserving forward-compat.
--
-- SCOPE — operational tables only:
--   * execution_telemetry  (fed into flywheel aggregator every 30 min)
--   * incidents            (incident timeline)
--   * l2_decisions         (planner audit trail)
--   * aggregated_pattern_stats (already keyed on canonical because the
--     flywheel aggregator writes canonical — but a view makes the
--     intent explicit)
--
-- INTENTIONALLY EXCLUDED — cryptographic / audit-class tables:
--   compliance_bundles, evidence_bundles, ots_proofs, audit_packages,
--   admin/client/partner audit logs, baa_signatures, etc. Their site_id
--   is part of an immutable record; canonical aliasing would
--   misrepresent the source-of-truth. CI gate
--   `tests/test_canonical_not_used_for_compliance_bundles.py` enforces
--   the boundary at the code level.

-- READ-ONLY ENFORCEMENT (Round-table P0-DBA-1 ship-now):
-- Postgres makes `SELECT t.*, f(col) AS extra FROM t` views auto-
-- updatable when the underlying table has a single base relation.
-- Without explicit DO INSTEAD NOTHING rules, an UPDATE/INSERT/DELETE
-- against the view would silently route to the underlying table,
-- bypassing the row-guard (mig 192) and audit triggers. We forbid
-- write operations at the view layer with INSTEAD NOTHING rules
-- (PostgreSQL prefers the rule over the auto-updatable rewrite) AND
-- revoke write privileges from mcp_app + PUBLIC at the role layer.
-- security_barrier=true blocks function-pushdown sneaking around the
-- rule.

CREATE OR REPLACE VIEW v_canonical_telemetry AS
SELECT
    et.*,
    canonical_site_id(et.site_id) AS canonical_site_id
  FROM execution_telemetry et;
ALTER VIEW v_canonical_telemetry SET (security_barrier = true);
CREATE OR REPLACE RULE v_canonical_telemetry_no_insert AS
    ON INSERT TO v_canonical_telemetry DO INSTEAD NOTHING;
CREATE OR REPLACE RULE v_canonical_telemetry_no_update AS
    ON UPDATE TO v_canonical_telemetry DO INSTEAD NOTHING;
CREATE OR REPLACE RULE v_canonical_telemetry_no_delete AS
    ON DELETE TO v_canonical_telemetry DO INSTEAD NOTHING;
REVOKE INSERT, UPDATE, DELETE ON v_canonical_telemetry FROM PUBLIC;

COMMENT ON VIEW v_canonical_telemetry IS
    'execution_telemetry projected with canonical_site_id resolution. Callers that need orphan-site rows aggregated under their canonical (post-relocate) site_id should SELECT/JOIN/FILTER on canonical_site_id. Raw site_id remains the immutable physical key. WARNING: per-row canonical_site_id() resolution; ad-hoc/aggregation use only — do NOT use in hot paths (1M+ rows = 1M function calls; STABLE caching is per-expression, not per-row). Session 213 F1-followup, mig 258.';

CREATE OR REPLACE VIEW v_canonical_incidents AS
SELECT
    i.*,
    canonical_site_id(i.site_id) AS canonical_site_id
  FROM incidents i;
ALTER VIEW v_canonical_incidents SET (security_barrier = true);
CREATE OR REPLACE RULE v_canonical_incidents_no_insert AS
    ON INSERT TO v_canonical_incidents DO INSTEAD NOTHING;
CREATE OR REPLACE RULE v_canonical_incidents_no_update AS
    ON UPDATE TO v_canonical_incidents DO INSTEAD NOTHING;
CREATE OR REPLACE RULE v_canonical_incidents_no_delete AS
    ON DELETE TO v_canonical_incidents DO INSTEAD NOTHING;
REVOKE INSERT, UPDATE, DELETE ON v_canonical_incidents FROM PUBLIC;

COMMENT ON VIEW v_canonical_incidents IS
    'incidents projected with canonical_site_id resolution. Same posture as v_canonical_telemetry. WARNING: per-row canonical_site_id() resolution — ad-hoc/aggregation use only, do NOT use in hot paths.';

CREATE OR REPLACE VIEW v_canonical_l2_decisions AS
SELECT
    l.*,
    canonical_site_id(l.site_id) AS canonical_site_id
  FROM l2_decisions l;
ALTER VIEW v_canonical_l2_decisions SET (security_barrier = true);
CREATE OR REPLACE RULE v_canonical_l2_decisions_no_insert AS
    ON INSERT TO v_canonical_l2_decisions DO INSTEAD NOTHING;
CREATE OR REPLACE RULE v_canonical_l2_decisions_no_update AS
    ON UPDATE TO v_canonical_l2_decisions DO INSTEAD NOTHING;
CREATE OR REPLACE RULE v_canonical_l2_decisions_no_delete AS
    ON DELETE TO v_canonical_l2_decisions DO INSTEAD NOTHING;
REVOKE INSERT, UPDATE, DELETE ON v_canonical_l2_decisions FROM PUBLIC;

COMMENT ON VIEW v_canonical_l2_decisions IS
    'l2_decisions projected with canonical_site_id resolution. Same posture as v_canonical_telemetry. WARNING: per-row canonical_site_id() resolution — ad-hoc/aggregation use only, do NOT use in hot paths.';

CREATE OR REPLACE VIEW v_canonical_aggregated_pattern_stats AS
SELECT
    aps.*,
    canonical_site_id(aps.site_id) AS canonical_site_id
  FROM aggregated_pattern_stats aps;
ALTER VIEW v_canonical_aggregated_pattern_stats SET (security_barrier = true);
CREATE OR REPLACE RULE v_canonical_aggregated_pattern_stats_no_insert AS
    ON INSERT TO v_canonical_aggregated_pattern_stats DO INSTEAD NOTHING;
CREATE OR REPLACE RULE v_canonical_aggregated_pattern_stats_no_update AS
    ON UPDATE TO v_canonical_aggregated_pattern_stats DO INSTEAD NOTHING;
CREATE OR REPLACE RULE v_canonical_aggregated_pattern_stats_no_delete AS
    ON DELETE TO v_canonical_aggregated_pattern_stats DO INSTEAD NOTHING;
REVOKE INSERT, UPDATE, DELETE ON v_canonical_aggregated_pattern_stats FROM PUBLIC;

COMMENT ON VIEW v_canonical_aggregated_pattern_stats IS
    'aggregated_pattern_stats projected with canonical_site_id resolution. Note: rows here are ALREADY keyed on canonical site_id (the flywheel aggregator in main.py writes canonical_site_id() since mig 256), so canonical_site_id and site_id should be identical here in steady state. The view exists for symmetry + forward-compat if a non-canonical write path ever leaks in. WARNING: per-row canonical_site_id() resolution — ad-hoc use only.';

-- Audit log entry.
INSERT INTO admin_audit_log (action, target, username, details, created_at)
SELECT
    'substrate.canonical_views.created',
    'system',
    'jbouey2006@gmail.com',
    jsonb_build_object(
        'migration', '258',
        'views', ARRAY[
            'v_canonical_telemetry',
            'v_canonical_incidents',
            'v_canonical_l2_decisions',
            'v_canonical_aggregated_pattern_stats'
        ],
        'related_findings', ARRAY['F1-followup'],
        'session', '213',
        'reason', 'Read-side canonical aliasing as syntactic sugar over canonical_site_id() function. Companion to migration 256.'
    ),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM admin_audit_log
     WHERE action = 'substrate.canonical_views.created'
       AND target = 'system'
);
