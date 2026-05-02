-- Migration 271 — Per-control granularity for compliance scoring
--
-- Closes the D1 score-whiplash class: BUNDLE-level cb.check_result was
-- assigned to all controls a bundle mapped to via DISTINCT ON. A single
-- failing host on a bundle covering 5 controls under-reported all 5
-- controls as failing.
--
-- Design doc: .agent/plans/d1-per-control-granularity-design.md (APPROVED
-- 2026-05-01). Round-table-approved with 3 final deltas applied at impl:
--   - Brian: defensive `if not statuses` guard in writer (Python side)
--   - Diana: idx_efm_status_lookup DROPPED — redundant given existing
--     UNIQUE(bundle_id, framework, control_id). Add only if EXPLAIN
--     ANALYZE post-deploy shows planner doesn't pick the existing UNIQUE.
--   - Steve: backfill within 24h + data_completeness field (separate post)
--
-- Pre-existing: evidence_framework_mappings is a DERIVED projection used
-- for score aggregation, NOT chain-of-custody audit. Source-of-truth is
-- compliance_bundles (Ed25519 + OTS-anchored). Re-ingest of a bundle
-- SHOULD overwrite the projection — no append-only triggers here.
--
-- Pre-backfill behavior: function's `WHERE efm.check_status IS NOT NULL`
-- excludes un-backfilled rows. Sites' scores show only NEW bundles
-- until the backfill script catches up. Per round-table: ship the migration,
-- then immediately run backfill. The Steve delta requires it within 24h.

BEGIN;

-- ── Schema: add per-control status column ───────────────────────────

ALTER TABLE evidence_framework_mappings
    ADD COLUMN IF NOT EXISTS check_status VARCHAR(20);

-- D6 gate (test_check_constraint_fits_column.py) validates the CHECK's
-- max literal length (5 chars: 'unknown' is 7 chars max actually) fits
-- VARCHAR(20). Width: 20 vs max literal 7 → safe.
--
-- DROP+ADD pattern for re-run idempotency (mig 267 sibling — coach #3
-- in the design doc):
ALTER TABLE evidence_framework_mappings
    DROP CONSTRAINT IF EXISTS efm_check_status_valid;

ALTER TABLE evidence_framework_mappings
    ADD CONSTRAINT efm_check_status_valid
    CHECK (
        check_status IS NULL
        OR check_status IN ('pass', 'fail', 'unknown')
    );

COMMENT ON COLUMN evidence_framework_mappings.check_status IS
'Per-control aggregated status — derived from per-host statuses of all '
'checks that map to this control under the framework crosswalk. NULL = '
'pre-backfill row (function ignores via IS NOT NULL filter). Possible '
'values: pass, fail, unknown. Aggregation rule (matches writer taxonomy '
'PASSING={pass,compliant,warning}, FAILING={fail,non_compliant}): ANY '
'status in FAILING → fail; ELSE ANY in PASSING → pass; ELSE unknown.';

-- ── Function: calculate_compliance_score per-control rewrite ─────────

CREATE OR REPLACE FUNCTION calculate_compliance_score(
    p_appliance_id VARCHAR,
    p_framework VARCHAR,
    p_window_days INTEGER DEFAULT 30
) RETURNS TABLE(
    total_controls INTEGER,
    passing_controls INTEGER,
    failing_controls INTEGER,
    unknown_controls INTEGER,
    score_percentage NUMERIC
) AS $$
DECLARE
    v_site_id VARCHAR;
BEGIN
    -- Resolve the appliance's site_id via natural-key chain (mig 268 sibling).
    -- compliance_bundles.site_id is the canonical join key; appliance_id +
    -- outcome columns on compliance_bundles are DEPRECATED (NULL on all rows).
    SELECT sa.site_id INTO v_site_id
    FROM site_appliances sa
    WHERE sa.appliance_id = p_appliance_id
    LIMIT 1;

    IF v_site_id IS NULL THEN
        RETURN QUERY SELECT 0, 0, 0, 0, NULL::NUMERIC;
        RETURN;
    END IF;

    RETURN QUERY
    WITH control_status AS (
        -- One row per control_id, picking the LATEST bundle's per-control
        -- status. The DISTINCT ON ordering is bundle-recency descending.
        SELECT DISTINCT ON (efm.control_id)
            efm.control_id,
            efm.check_status
        FROM compliance_bundles cb
        JOIN evidence_framework_mappings efm
          ON efm.bundle_id = cb.bundle_id
        WHERE cb.site_id = v_site_id
          AND efm.framework = p_framework
          AND cb.created_at >= NOW() - make_interval(days => p_window_days)
          AND efm.check_status IS NOT NULL  -- skip pre-backfill rows
        ORDER BY efm.control_id, cb.created_at DESC
    )
    SELECT
        COUNT(*)::INTEGER AS total_controls,
        COUNT(*) FILTER (WHERE check_status = 'pass')::INTEGER AS passing_controls,
        COUNT(*) FILTER (WHERE check_status = 'fail')::INTEGER AS failing_controls,
        COUNT(*) FILTER (WHERE check_status = 'unknown')::INTEGER AS unknown_controls,
        ROUND(
            COUNT(*) FILTER (WHERE check_status = 'pass')::NUMERIC
            / NULLIF(COUNT(*), 0) * 100,
            2
        ) AS score_percentage
    FROM control_status;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION calculate_compliance_score(VARCHAR, VARCHAR, INTEGER) IS
'Per-control compliance score (D1 fix 2026-05-02). Replaces mig 268''s '
'bundle-level scoring with per-control granularity from '
'evidence_framework_mappings.check_status. Pre-backfill rows '
'(check_status IS NULL) are excluded — sites with un-backfilled mappings '
'see partial scores until scripts/backfill_efm_check_status.py runs.';

COMMIT;
