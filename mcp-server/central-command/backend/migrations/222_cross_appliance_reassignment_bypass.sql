-- Migration 222: scoped bypass for sibling-reassignment writes
--
-- The D2 cross-appliance UPDATE guard (Session 206 Migration 199, flipped
-- to 'reject' by Migration 201) blocks any UPDATE on site_appliances where
-- the transaction's `app.actor_appliance_id` doesn't match NEW.appliance
-- _id. That's correct for 99% of UPDATE paths — it closed a serious
-- impersonation vector. But ONE legitimate pattern needs cross-appliance
-- writes by construction: the Phase 3 mesh reassignment in sites.py
-- STEP 3.8c, where a single checkin redistributes target assignments
-- across ALL online siblings in one transaction.
--
-- Phase 3 shipped on 2026-04-15 AM and has been failing silently in
-- production since — every checkin logs `sibling_reassignment_failed`
-- because the sibling UPDATEs trip the D2 guard. The mesh isn't actually
-- redistributing; it's falling back to the 5-min-per-node catchup mode.
--
-- Fix: add a scoped bypass flag `app.allow_cross_appliance_reassignment`.
-- When set to 'true', the trigger allows cross-appliance UPDATEs through
-- WITHOUT auditing (the caller already logs structured events). Mirrors
-- the Session 207 `app.allow_multi_row` pattern from Migration 208 — same
-- SET LOCAL discipline, same blast-radius bound.
--
-- Security analysis:
--   - Flag is SET LOCAL → automatically unset at transaction end; cannot
--     leak across connections or requests
--   - Only the checkin handler in sites.py sets it; grep-enforceable
--   - Admin context (is_admin=true) still has priority, unchanged
--   - Normal untagged / cross-appliance paths still blocked as before

BEGIN;

CREATE OR REPLACE FUNCTION audit_cross_appliance_update()
RETURNS TRIGGER AS $$
DECLARE
    actor_id        TEXT;
    is_admin        TEXT;
    enforce_mode    TEXT;
    allow_reassign  TEXT;
BEGIN
    actor_id       := current_setting('app.actor_appliance_id', TRUE);
    is_admin       := current_setting('app.is_admin', TRUE);
    enforce_mode   := COALESCE(current_setting('app.cross_appliance_enforce', TRUE), 'audit');
    allow_reassign := COALESCE(current_setting('app.allow_cross_appliance_reassignment', TRUE), 'false');

    -- Admin context always bypasses.
    IF is_admin = 'true' THEN
        RETURN NEW;
    END IF;

    -- NEW (Migration 222): scoped bypass for sibling reassignment. The
    -- caller takes responsibility for logging — we don't flood audit log.
    IF allow_reassign = 'true' THEN
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
        RETURN NEW;
    END IF;

    -- Actor tagged: verify it matches the target row.
    IF NEW.appliance_id IS NOT NULL AND NEW.appliance_id != actor_id THEN
        IF enforce_mode = 'reject' THEN
            RAISE EXCEPTION 'cross-appliance UPDATE blocked: actor=% target=%',
                actor_id, NEW.appliance_id
                USING ERRCODE = 'insufficient_privilege',
                      HINT = 'Session 206 D2 invariant. For legitimate mesh '
                             'reassignment, SET LOCAL '
                             'app.allow_cross_appliance_reassignment = ''true''.';
        END IF;
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
    'Session 207 M222. D2 cross-appliance UPDATE guard + scoped bypass '
    'via SET LOCAL app.allow_cross_appliance_reassignment=''true'' for '
    'Phase 3 sibling reassignment. Admin (is_admin=true) still bypasses '
    'unconditionally. Without the flag: untagged + cross-actor UPDATEs '
    'are rejected in reject mode, audited in audit mode.';

COMMIT;
