-- Migration 268 — fix calculate_compliance_score writer/reader column
-- mismatch (Round-table 2026-05-01, Path B consensus 5/5).
--
-- Root cause: every appliance reads score_percentage = 0.00 because
-- the function joins on `compliance_bundles.appliance_id` and filters
-- on `compliance_bundles.outcome` — but the bundle-INSERT path in
-- `evidence_chain.py` NEVER populates either column. All 245,142
-- bundles have NULL appliance_id AND NULL outcome (verified live).
-- The function is reading columns the writer doesn't fill.
--
-- Round-table verdict (Brian/Diana/Camila/Steve/Priya, 5/5 Path B):
--   * Treat `compliance_bundles.appliance_id` + `outcome` as deprecated
--   * Rewrite the function to use `cb.check_result` (the canonical
--     column the writer DOES populate) and resolve appliance via
--     `site_appliances` join on (site_id, appliance_id)
--   * Backfill of 245K rows rejected — Path A would extend the
--     just-recovered prod outage and require visiting every monthly
--     partition.
--
-- Customer impact: score=0% on every clinic dashboard is the
-- worst-case-perception bug. Path B is fastest path to recovery.
--
-- Lint debt closed in same migration: switch
-- `(p_window_days || ' days')::INTERVAL` → `make_interval(days => p_window_days)`.
-- The Block-3 lint expansion didn't reach SQL function bodies; this
-- is the canary fix for that gap. Followup: extend lint to scan
-- function bodies via pg_proc.prosrc.
--
-- Idempotency: CREATE OR REPLACE FUNCTION + CREATE INDEX IF NOT EXISTS
-- + COMMENT ON COLUMN are all re-runnable.
--
-- Post-deploy verification: SELECT * FROM compliance_scores WHERE
-- site_id='north-valley-branch-2' — score_percentage should
-- transition from 0.00 to non-zero within 5 minutes of the next
-- bundle's framework-mapping pass.

BEGIN;

-- The passing-set MUST match the canonical writer in
-- evidence_chain.py:1137-1142 — one source of truth for what a
-- "passing" check looks like. Do NOT introduce a 3rd taxonomy.
--   passing  = {'pass', 'compliant', 'warning'}
--   failing  = {'fail', 'non_compliant'}
--   unknown  = anything else (incl. NULL, 'unknown', 'warn')
--
-- D2 round-table 2026-05-01 (3-2 WON'T_FIX, dissent Brian+Diana):
-- the bundle-writer in evidence_chain.py only emits {pass, fail, warn,
-- unknown} at the BUNDLE level. The IN-list entries 'compliant' and
-- 'warning' (passing) and 'non_compliant' (failing) are RESERVED for
-- non-bundle-writer paths (runbook_consent, privileged_access_attestation,
-- appliance_relocation) which may set check_result directly with
-- per-check-style values. Keep the broader taxonomy here so those
-- writers don't need a parallel scoring function.
CREATE OR REPLACE FUNCTION calculate_compliance_score(
    p_appliance_id VARCHAR,
    p_framework VARCHAR,
    p_window_days INTEGER DEFAULT 30
)
RETURNS TABLE(
    total_controls INTEGER,
    passing_controls INTEGER,
    failing_controls INTEGER,
    unknown_controls INTEGER,
    score_percentage NUMERIC
) AS $$
DECLARE
    v_site_id VARCHAR;
BEGIN
    -- Resolve site_id from the appliance natural key. site_appliances
    -- is the canonical source of truth post-mig 196.
    --
    -- Resolution-chain note (consistency-coach 2026-05-01): mig 265's
    -- `refresh_compliance_score` has 3 paths (appliance_framework_configs
    -- → site_appliances by appliance_id → legacy_uuid). This function
    -- DELIBERATELY OMITS the appliance_framework_configs path: site_id
    -- resolution here does NOT depend on framework config (which is
    -- empty for most sites per CLAUDE.md), so the leaner 2-path chain
    -- is correct. Path 1 in mig 265 only mattered there because that
    -- function's CALLER chose framework first; this function's caller
    -- already has framework as a parameter.
    SELECT site_id INTO v_site_id
      FROM site_appliances
     WHERE appliance_id = p_appliance_id
       AND deleted_at IS NULL
     LIMIT 1;

    -- Fallback: legacy_uuid match for pre-mig-196 appliance_ids.
    IF v_site_id IS NULL THEN
        BEGIN
            SELECT site_id INTO v_site_id
              FROM site_appliances
             WHERE legacy_uuid = p_appliance_id::uuid
             LIMIT 1;
        EXCEPTION WHEN invalid_text_representation THEN
            v_site_id := NULL;
        END;
    END IF;

    -- Unknown appliance — return zero row tuple but with NULL score
    -- (NOT 0.00) so the caller can distinguish "no controls scored
    -- yet" from "scored, all failing".
    IF v_site_id IS NULL THEN
        RETURN QUERY SELECT 0, 0, 0, 0, NULL::NUMERIC;
        RETURN;
    END IF;

    RETURN QUERY
    WITH control_status AS (
        SELECT DISTINCT ON (efm.control_id)
            efm.control_id,
            cb.check_result
          FROM compliance_bundles cb
          JOIN evidence_framework_mappings efm
            ON cb.bundle_id = efm.bundle_id
         WHERE cb.site_id = v_site_id
           AND efm.framework = p_framework
           AND cb.created_at >= NOW() - make_interval(days => p_window_days)
         ORDER BY efm.control_id, cb.created_at DESC
    )
    SELECT
        COUNT(*)::INTEGER AS total_controls,
        COUNT(*) FILTER (
            WHERE check_result IN ('pass', 'compliant', 'warning')
        )::INTEGER AS passing_controls,
        COUNT(*) FILTER (
            WHERE check_result IN ('fail', 'non_compliant')
        )::INTEGER AS failing_controls,
        COUNT(*) FILTER (
            WHERE check_result IS NULL
               OR check_result NOT IN ('pass', 'compliant', 'warning',
                                        'fail', 'non_compliant')
        )::INTEGER AS unknown_controls,
        ROUND(
            COUNT(*) FILTER (
                WHERE check_result IN ('pass', 'compliant', 'warning')
            )::DECIMAL
            / NULLIF(COUNT(*), 0) * 100,
            2
        ) AS score_percentage
      FROM control_status;
END;
$$ LANGUAGE plpgsql;

-- Mark the deprecated columns. Future writers should NOT be tempted
-- to start populating them — the canonical join is via site_id +
-- site_appliances + check_result.
COMMENT ON COLUMN compliance_bundles.appliance_id IS
    'DEPRECATED 2026-05-01: never populated by the bundle writer; '
    'retained for legacy schema compatibility. Resolve appliance via '
    'site_appliances join on (site_id, appliance_id). See mig 268.';

COMMENT ON COLUMN compliance_bundles.outcome IS
    'DEPRECATED 2026-05-01: never populated by the bundle writer; '
    'retained for legacy schema compatibility. Use cb.check_result '
    'instead — passing set = {pass, compliant, warning}; failing set '
    '= {fail, non_compliant}. See mig 268.';

COMMENT ON FUNCTION calculate_compliance_score(VARCHAR, VARCHAR, INTEGER) IS
    'Returns total/passing/failing/unknown control counts + score %. '
    'Joins compliance_bundles via site_id (resolved from p_appliance_id '
    'via site_appliances natural key OR legacy_uuid fallback). Uses '
    'cb.check_result NOT cb.outcome (deprecated column never populated). '
    'Window default 30d. Round-table mig 268, 2026-05-01.';

-- Index already exists on prod (verified) — idx_cb_site_created.
-- IF NOT EXISTS makes this a no-op on prod; defensive on fresh DBs.
CREATE INDEX IF NOT EXISTS idx_cb_site_created
    ON compliance_bundles (site_id, created_at DESC);

-- Audit-log
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'migration:268',
    'function.replace',
    'calculate_compliance_score(VARCHAR, VARCHAR, INTEGER)',
    jsonb_build_object(
        'reason', 'Writer/reader column mismatch caused score=0 fleet-wide',
        'audit_block', 'Session-214 score=0 round-table 2026-05-01',
        'consensus', 'Path B (5/5: Brian, Diana, Camila, Steve, Priya)',
        'consistency_coach', 'APPROVE_WITH_CHANGES — 2 minor applied: resolution-chain comment + forensic disclosure note',
        'rejected_path_a', 'Backfill 245K NULL appliance_ids → multi-min UPDATE on partitioned table during just-recovered prod',
        'forensic_disclosure', 'Pre-fix score_percentage=0.00 across the fleet was MEANINGLESS — writer/reader column mismatch, not a real-world failing-check rate. First post-deploy refresh will JUMP scores from 0 to real values (likely 50-100% based on observed pass/fail bundle ratios). Below public-advisory threshold per Session-203 disclosure-first commitment (data was never correct; fix does not reveal hidden state) but operator-visible UX surprise. Memory entry filed.',
        'shipped', '2026-05-01'
    ),
    NOW()
)
ON CONFLICT DO NOTHING;

COMMIT;
