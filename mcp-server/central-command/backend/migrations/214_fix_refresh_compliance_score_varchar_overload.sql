-- Migration 214: redirect refresh_compliance_score(varchar, varchar) off appliances
--
-- Migration 213 redefined the UUID overload of refresh_compliance_score but
-- missed the VARCHAR/VARCHAR overload originally created by migration 013.
-- Both live callers (evidence_chain.submit_evidence + frameworks.api
-- refresh_compliance_scores) pass VARCHAR appliance_id values, so they were
-- hitting the broken overload and would have RAISEd on the next trigger
-- path now that `appliances` is gone. No runtime damage yet because those
-- call sites gate on `appliance_framework_configs` rows existing first,
-- and most do — but the fallback branch was latent-broken.
--
-- Fix: CREATE OR REPLACE the VARCHAR overload with the identical body used
-- by the UUID overload in migration 213 — read the fallback site_id from
-- site_appliances.legacy_uuid (cast through UUID comparison handled by PG
-- coercion; legacy_uuid is UUID so we cast the VARCHAR param explicitly).

BEGIN;

CREATE OR REPLACE FUNCTION refresh_compliance_score(
    p_appliance_id VARCHAR,
    p_framework VARCHAR
) RETURNS void AS $$
DECLARE
    v_site_id VARCHAR;
    v_score RECORD;
BEGIN
    SELECT site_id INTO v_site_id
      FROM appliance_framework_configs
     WHERE appliance_id = p_appliance_id;

    IF v_site_id IS NULL THEN
        -- legacy_uuid is UUID on site_appliances; coerce the VARCHAR param.
        -- Wrap in exception handler so a malformed UUID input doesn't break
        -- the calling transaction — just leave v_site_id NULL and skip.
        BEGIN
            SELECT site_id INTO v_site_id
              FROM site_appliances
             WHERE legacy_uuid = p_appliance_id::uuid
             LIMIT 1;
        EXCEPTION WHEN invalid_text_representation THEN
            v_site_id := NULL;
        END;
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

COMMIT;
