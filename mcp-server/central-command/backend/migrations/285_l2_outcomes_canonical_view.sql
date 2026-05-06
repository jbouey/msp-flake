-- Migration 285: canonical view for L2 decision-to-outcome JOIN
--
-- Outside-audit finding (2026-05-06, RT-DM Issue #2):
-- L2 truth is split. `l2_decisions` (mig 061) records the LLM's
-- decision for an incident; `incidents.resolution_tier` (mig 106)
-- records what tier ultimately resolved the incident. Dashboards
-- consuming `l2_decisions` alone see DECISIONS without OUTCOMES;
-- dashboards consuming `incidents.resolution_tier` alone see
-- RESOLUTION TIER without the LLM reasoning + cost + cache-hit
-- info. No canonical SQL exposed both joined.
--
-- This migration ships:
--   - `v_l2_outcomes` view: every l2_decisions row LEFT JOINed to
--     its incident, with a derived `is_l2_success` boolean.
--   - `compute_l2_success_rate(window_days)` SQL function returning
--     the canonical L2 success rate over the window.
--   - Both correctly handle CACHE HITS — when l2_planner caches a
--     prior decision, the new incident still gets tier='L2' AND
--     references the cached l2_decisions row. Cache hits count
--     toward L2 success without double-counting LLM cost.

-- ─────────────────────────────────────────────────────────────────
-- 1. Canonical view
-- ─────────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW v_l2_outcomes AS
SELECT
    -- Decision side (l2_decisions)
    ld.id              AS decision_id,
    ld.incident_id,
    ld.runbook_id      AS decided_runbook_id,
    ld.reasoning,
    ld.confidence,
    ld.pattern_signature,
    ld.llm_model,
    ld.llm_latency_ms,
    ld.created_at      AS decided_at,

    -- Outcome side (incidents)
    i.id               AS incident_pk,
    i.site_id,
    i.appliance_id,
    i.host_id,
    i.incident_type,
    i.severity,
    i.status           AS incident_status,
    i.resolution_tier,
    i.created_at       AS incident_created_at,
    i.resolved_at      AS incident_resolved_at,

    -- Derived: did L2 actually resolve this incident?
    -- Incident must (a) have tier='L2' (the L2 path was the resolver)
    -- AND (b) status='resolved'. NULL when the incident no longer
    -- exists (orphan decisions are flagged by the
    -- `l2_decision_orphan_incident` substrate invariant — separate).
    (
        i.id IS NOT NULL
        AND i.resolution_tier = 'L2'
        AND i.status = 'resolved'
    ) AS is_l2_success
FROM l2_decisions ld
LEFT JOIN incidents i
    ON i.id::text = ld.incident_id;
-- Maya 2nd-eye fix (2026-05-06): the original draft included an OR
-- leg `i.incident_id = ld.incident_id`. Verified against the rest of
-- the backend: `incidents.incident_id` is NOT a column. The only
-- references to `i.incident_id` in the codebase were in this view
-- and the parallel substrate invariant. Dropped — single canonical
-- JOIN on `i.id::text = ld.incident_id`.

COMMENT ON VIEW v_l2_outcomes IS
    'Migration 285 (RT-DM Issue #2, 2026-05-06): canonical join of '
    'l2_decisions to incidents. Dashboards must consume this view '
    'for L2 success-rate metrics, NOT the underlying tables in '
    'isolation. Pinned by tests/test_l2_canonical_view_used.py.';

-- ─────────────────────────────────────────────────────────────────
-- 2. Canonical L2 success-rate function
-- ─────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION compute_l2_success_rate(
    window_days INT DEFAULT 30
)
RETURNS TABLE (
    decision_count BIGINT,
    success_count BIGINT,
    success_rate NUMERIC
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)                                          AS decision_count,
        COUNT(*) FILTER (WHERE is_l2_success)             AS success_count,
        CASE
            WHEN COUNT(*) = 0 THEN 0::numeric
            ELSE ROUND(
                100.0 * COUNT(*) FILTER (WHERE is_l2_success)
                      / NULLIF(COUNT(*), 0),
                2
            )
        END                                               AS success_rate
    FROM v_l2_outcomes
    WHERE decided_at > NOW() - make_interval(days => window_days);
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION compute_l2_success_rate IS
    'Migration 285: canonical L2 success-rate calculation. Use this '
    'function for dashboard metrics; do NOT inline a passed/total '
    'computation against the underlying tables. Pinned by '
    'tests/test_l2_canonical_view_used.py.';

-- ─────────────────────────────────────────────────────────────────
-- 3. Rollback
-- ─────────────────────────────────────────────────────────────────

-- Rollback (manual; do NOT include in this migration's idempotent
-- run because that would drop the function on every applied-state
-- check). To roll back:
--   DROP FUNCTION IF EXISTS compute_l2_success_rate(INT);
--   DROP VIEW IF EXISTS v_l2_outcomes;
-- The view + function are read-only; no data loss on drop.
