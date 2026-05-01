-- Migration 265 — fix refresh_compliance_score VARCHAR overload site_id
-- resolution. Block-3 audit P3 closure (2026-05-01).
--
-- Pre-fix behavior: the VARCHAR overload (mig 214) tried two
-- resolution paths for site_id:
--   1. appliance_framework_configs WHERE appliance_id = $1
--   2. site_appliances WHERE legacy_uuid = $1::uuid (with cast guard)
--
-- Both fail for the common VARCHAR appliance_id format (e.g.
-- 'north-valley-branch-2-84:3A:5B:91:B6:61'):
--   * Path 1: appliance_framework_configs is empty for ~most sites
--     (post-Session-203 multi-framework redesign didn't backfill).
--   * Path 2: the appliance_id isn't a UUID, the cast raises
--     invalid_text_representation, caught, v_site_id stays NULL.
--
-- Result: every INSERT into compliance_scores violates NOT NULL on
-- site_id and fails. Production state on 2026-05-01: ZERO rows in
-- compliance_scores fleet-wide despite >70K compliance_bundles
-- ingested. The dashboard's per-appliance score panel was always
-- empty. Surfaces caught by `except Exception as e: logger.debug(...)`
-- in evidence_chain.py:1625 (debug-level → invisible to log shipper).
--
-- Fix: add a third fallback — `site_appliances WHERE appliance_id =
-- $1` (VARCHAR match). This is the natural-case lookup post-mig 196
-- where site_appliances.appliance_id is the canonical natural key.
-- The legacy_uuid fallback stays for backwards-compat with old
-- appliance_id formats that ARE UUIDs.
--
-- Self-healing: subsequent bundle ingests on any active appliance
-- now successfully resolve site_id and write/update the score row.

BEGIN;

CREATE OR REPLACE FUNCTION refresh_compliance_score(
    p_appliance_id VARCHAR,
    p_framework VARCHAR
) RETURNS void AS $$
DECLARE
    v_site_id VARCHAR;
    v_score RECORD;
BEGIN
    -- Path 1: appliance_framework_configs (Session 203 H6/H8 source of truth)
    SELECT site_id INTO v_site_id
      FROM appliance_framework_configs
     WHERE appliance_id = p_appliance_id;

    -- Path 2 (fallback): site_appliances by VARCHAR natural key.
    -- This is the COMMON CASE post-mig 196 — appliance_id is the
    -- canonical natural key (e.g. 'site-id-MAC').
    IF v_site_id IS NULL THEN
        SELECT site_id INTO v_site_id
          FROM site_appliances
         WHERE appliance_id = p_appliance_id
           AND deleted_at IS NULL
         LIMIT 1;
    END IF;

    -- Path 3 (final fallback): legacy_uuid match for pre-mig-196
    -- appliance_ids that ARE UUIDs. Cast-guarded so a malformed
    -- VARCHAR doesn't poison the calling transaction.
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

    -- Defensive: if all 3 paths returned NULL, the appliance is
    -- unknown to this database. Skip silently — better than
    -- raising NOT NULL violation back to the caller. The
    -- evidence_chain caller handles this case via try/except.
    IF v_site_id IS NULL THEN
        RAISE NOTICE 'refresh_compliance_score: unknown appliance % — skipping', p_appliance_id;
        RETURN;
    END IF;

    SELECT * INTO v_score FROM calculate_compliance_score(p_appliance_id, p_framework);

    INSERT INTO compliance_scores (
        appliance_id, site_id, framework,
        total_controls, passing_controls, failing_controls, unknown_controls,
        score_percentage, is_compliant, at_risk, calculated_at
    ) VALUES (
        p_appliance_id, v_site_id, p_framework,
        v_score.total_controls, v_score.passing_controls,
        v_score.failing_controls, v_score.unknown_controls,
        COALESCE(v_score.score_percentage, 0),
        COALESCE(v_score.score_percentage, 0) >= 80,
        COALESCE(v_score.score_percentage, 0) < 70,
        NOW()
    )
    ON CONFLICT (appliance_id, framework)
    DO UPDATE SET
        total_controls = EXCLUDED.total_controls,
        passing_controls = EXCLUDED.passing_controls,
        failing_controls = EXCLUDED.failing_controls,
        unknown_controls = EXCLUDED.unknown_controls,
        score_percentage = EXCLUDED.score_percentage,
        is_compliant = EXCLUDED.is_compliant,
        at_risk = EXCLUDED.at_risk,
        calculated_at = EXCLUDED.calculated_at;
END;
$$ LANGUAGE plpgsql;

-- Audit-log
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'migration:265',
    'function.replace',
    'refresh_compliance_score(VARCHAR, VARCHAR)',
    jsonb_build_object(
        'reason', 'Add VARCHAR appliance_id fallback for site_id resolution',
        'audit_block', 'Session-214 Block-3 P3',
        'pre_fix_state', 'compliance_scores empty fleet-wide; INSERT violated NOT NULL',
        'shipped', '2026-05-01'
    ),
    NOW()
)
ON CONFLICT DO NOTHING;

COMMIT;
