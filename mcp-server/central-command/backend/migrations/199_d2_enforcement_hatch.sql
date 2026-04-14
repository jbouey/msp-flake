-- Migration 199: D2 — controlled flip for cross-appliance UPDATE enforcement.
--
-- Session 206 D2 shipped as AUDIT-ONLY (Migration 197). This migration
-- upgrades the same trigger to support an enforcement switch driven by
-- a transaction-local setting:
--
--   SET LOCAL app.cross_appliance_enforce = 'audit'   -- current behavior
--   SET LOCAL app.cross_appliance_enforce = 'reject'  -- enforce mode
--
-- Admin-context (is_admin=true) always bypasses. Legitimate multi-site
-- operations can opt in to REJECT selectively.
--
-- Enforcement is NOT turned on globally by this migration — callers that
-- want strict mode set the flag per-transaction. Once every writer sets
-- actor_appliance_id reliably, we'll flip the default.

BEGIN;

CREATE OR REPLACE FUNCTION audit_cross_appliance_update()
RETURNS TRIGGER AS $$
DECLARE
    actor_id      TEXT;
    is_admin      TEXT;
    enforce_mode  TEXT;
BEGIN
    actor_id     := current_setting('app.actor_appliance_id', TRUE);
    is_admin     := current_setting('app.is_admin', TRUE);
    enforce_mode := COALESCE(current_setting('app.cross_appliance_enforce', TRUE), 'audit');

    -- Admin context always bypasses — same as the original Migration 197.
    IF is_admin = 'true' THEN
        RETURN NEW;
    END IF;

    -- Untagged transaction: can't distinguish legitimate from cross-appliance.
    IF actor_id IS NULL OR actor_id = '' THEN
        IF enforce_mode = 'reject' THEN
            RAISE EXCEPTION 'site_appliances UPDATE without app.actor_appliance_id '
                'blocked under enforce mode. Set LOCAL app.actor_appliance_id '
                'to the authenticated appliance, or use admin context.'
                USING ERRCODE = 'insufficient_privilege',
                      HINT = 'Session 206 D2 invariant.';
        END IF;
        -- audit mode: let it through
        RETURN NEW;
    END IF;

    -- Actor tagged: verify it matches the target row.
    IF NEW.appliance_id IS NOT NULL AND NEW.appliance_id != actor_id THEN
        IF enforce_mode = 'reject' THEN
            RAISE EXCEPTION 'cross-appliance UPDATE blocked: actor=% target=%',
                actor_id, NEW.appliance_id
                USING ERRCODE = 'insufficient_privilege',
                      HINT = 'Session 206 D2 invariant.';
        END IF;
        -- audit mode: record the event
        INSERT INTO admin_audit_log
            (username, action, target, details, success)
        VALUES (
            'trigger:audit_cross_appliance_update',
            'CROSS_APPLIANCE_UPDATE_AUDIT',
            NEW.appliance_id,
            jsonb_build_object(
                'actor_appliance_id', actor_id,
                'target_appliance_id', NEW.appliance_id,
                'table', TG_TABLE_NAME,
                'site_id', NEW.site_id,
                'enforce_mode', enforce_mode
            ),
            true
        );
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION audit_cross_appliance_update() IS
    'Session 206 D2 (Migration 199): AUDIT + optional REJECT. Enforcement '
    'is per-transaction via SET LOCAL app.cross_appliance_enforce = ''reject''. '
    'Admin (is_admin=true) always passes. Intended flow: flip to reject '
    'globally once every writer sets actor_appliance_id.';

COMMIT;
