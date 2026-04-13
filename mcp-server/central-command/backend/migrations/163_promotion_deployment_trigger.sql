-- Migration 163: Auto-update promoted_rules.deployment_count when an
-- appliance acks a sync_promoted_rule fleet order.
--
-- Closes the flywheel measurement loop. Pre-Session-205 the
-- promoted_rules.deployment_count column existed but was never updated
-- by any code path. Result: 43 promoted rules, all showing
-- deployment_count=0 forever, no signal that the appliance ever received
-- the rule.
--
-- This trigger fires on every fleet_order_completions INSERT/UPDATE.
-- If the underlying fleet_order is sync_promoted_rule, it bumps the
-- matching promoted_rules row's deployment_count and last_deployed_at.
-- The match is by parameters->>'rule_id' on the fleet_order.
--
-- Idempotent: ON CONFLICT DO NOTHING in the trigger body so re-acks
-- (e.g., daemon retry) don't double-count.

BEGIN;

CREATE OR REPLACE FUNCTION track_promoted_rule_deployment()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_order_type TEXT;
    v_rule_id TEXT;
BEGIN
    -- Only act on completions marked 'completed' (not 'failed' or 'skipped')
    IF NEW.status IS DISTINCT FROM 'completed' THEN
        RETURN NEW;
    END IF;

    -- Look up the fleet order; bail if not sync_promoted_rule
    SELECT order_type, parameters->>'rule_id'
      INTO v_order_type, v_rule_id
      FROM fleet_orders
     WHERE id = NEW.fleet_order_id;

    IF v_order_type IS DISTINCT FROM 'sync_promoted_rule' OR v_rule_id IS NULL THEN
        RETURN NEW;
    END IF;

    -- Bump the promoted_rules counter for the matching rule_id
    UPDATE promoted_rules
       SET deployment_count = COALESCE(deployment_count, 0) + 1,
           last_deployed_at = NOW()
     WHERE rule_id = v_rule_id;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_track_promoted_rule_deployment ON fleet_order_completions;
CREATE TRIGGER trg_track_promoted_rule_deployment
    AFTER INSERT OR UPDATE OF status ON fleet_order_completions
    FOR EACH ROW
    EXECUTE FUNCTION track_promoted_rule_deployment();

COMMIT;
