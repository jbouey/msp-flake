-- Migration 209: api_keys invariants — one-active-key + audit trail
--
-- Closes Sev-1/Sev-2 findings F2 + F4 from the 2026-04-15 audit:
--
-- F4. Three concurrent active appliance-specific keys for one
--     appliance (observed: 7C:D3:0A:7C:55:18 has rows id=3, 4, 5
--     all active=true). Provisioning code paths that don't
--     deactivate prior keys leak active rows; rotated-thinking-
--     it's-invalidated keys remain valid forever.
--
-- F2. api_keys can be deleted/deactivated with no audit trail.
--     Operators have no way to answer "who killed this key, when,
--     and why?" — the very thing that breaks the appliance.
--
-- Approach: triggers on api_keys handle both invariants centrally,
-- so EVERY INSERT path (claim, rekey, drop-ship, deployment-pack,
-- future) gets correct behavior without code-side discipline.
--
-- Trigger 1: BEFORE INSERT — auto-deactivate prior active keys
--   with the same (site_id, appliance_id). Idempotent: re-runs
--   are no-ops because there's never more than one active row.
--
-- Trigger 2: AFTER INSERT/UPDATE/DELETE — write a row to
--   admin_audit_log so we know who/when/why for every key change.
--
-- Cleanup: collapse the existing duplicate active rows down to
-- the most recent per (site_id, appliance_id). Driven from the
-- migration runner (current_user='mcp' bypasses Migration 208
-- row-guard automatically).

BEGIN;

-- ============================================================
-- Trigger 1: one-active-key per (site_id, appliance_id)
-- ============================================================

CREATE OR REPLACE FUNCTION enforce_one_active_api_key_per_appliance()
RETURNS TRIGGER AS $$
BEGIN
    -- Only enforce for active inserts; deactivations don't conflict.
    IF NEW.active = false THEN
        RETURN NEW;
    END IF;

    -- Atomically deactivate every prior active row for the same
    -- (site_id, appliance_id) tuple, treating NULL appliance_id as
    -- its own bucket (site-level keys). Excludes the current row
    -- by id so a re-INSERT-with-same-id (impossible in practice
    -- but defensive) doesn't deactivate itself.
    UPDATE api_keys
       SET active = false
     WHERE site_id = NEW.site_id
       AND active = true
       AND id IS DISTINCT FROM NEW.id
       AND ((appliance_id IS NULL AND NEW.appliance_id IS NULL)
            OR appliance_id = NEW.appliance_id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_enforce_one_active_api_key ON api_keys;
CREATE TRIGGER trg_enforce_one_active_api_key
    BEFORE INSERT ON api_keys
    FOR EACH ROW
    EXECUTE FUNCTION enforce_one_active_api_key_per_appliance();

COMMENT ON FUNCTION enforce_one_active_api_key_per_appliance() IS
    'Migration 209 (F4): on INSERT of an active api_keys row, '
    'auto-deactivate prior active rows for the same (site_id, '
    'appliance_id). Eliminates leaked active keys from sloppy '
    'provisioning code paths.';


-- ============================================================
-- Trigger 2: audit every api_keys mutation
-- ============================================================

CREATE OR REPLACE FUNCTION audit_api_keys_change()
RETURNS TRIGGER AS $$
DECLARE
    actor TEXT;
    op    TEXT;
    payload JSONB;
BEGIN
    actor := current_user;
    op    := TG_OP;

    IF TG_OP = 'DELETE' THEN
        payload := jsonb_build_object(
            'op',           'DELETE',
            'id',           OLD.id,
            'site_id',      OLD.site_id,
            'appliance_id', OLD.appliance_id,
            'key_prefix',   OLD.key_prefix,
            'was_active',   OLD.active
        );
    ELSIF TG_OP = 'UPDATE' THEN
        -- Only audit if active flag changed; description / metadata
        -- bumps are noise.
        IF OLD.active IS NOT DISTINCT FROM NEW.active THEN
            RETURN NEW;
        END IF;
        payload := jsonb_build_object(
            'op',           'UPDATE',
            'id',           NEW.id,
            'site_id',      NEW.site_id,
            'appliance_id', NEW.appliance_id,
            'key_prefix',   NEW.key_prefix,
            'old_active',   OLD.active,
            'new_active',   NEW.active
        );
    ELSE  -- INSERT
        payload := jsonb_build_object(
            'op',           'INSERT',
            'id',           NEW.id,
            'site_id',      NEW.site_id,
            'appliance_id', NEW.appliance_id,
            'key_prefix',   NEW.key_prefix,
            'description',  NEW.description,
            'active',       NEW.active
        );
    END IF;

    INSERT INTO admin_audit_log (action, target_type, target_id, actor, details, created_at)
    VALUES ('api_key.' || lower(op),
            'api_key',
            COALESCE(NEW.id::text, OLD.id::text),
            actor,
            payload,
            NOW());

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_api_keys ON api_keys;
CREATE TRIGGER trg_audit_api_keys
    AFTER INSERT OR UPDATE OR DELETE ON api_keys
    FOR EACH ROW
    EXECUTE FUNCTION audit_api_keys_change();

COMMENT ON FUNCTION audit_api_keys_change() IS
    'Migration 209 (F2): every api_keys mutation writes a row to '
    'admin_audit_log so operators can answer who/when/why when an '
    'appliance auth-fails.';


-- ============================================================
-- Cleanup: collapse existing duplicate active rows
-- ============================================================
-- Keep the newest active row per (site_id, appliance_id) and
-- deactivate the rest. Runs as `mcp` (migration runner) so the
-- Migration 208 row-guard bypass applies. Trigger above does NOT
-- fire on these UPDATEs because we set active=false, not true.

WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY site_id,
                            COALESCE(appliance_id, '__site_level__')
               ORDER BY created_at DESC
           ) AS rn
      FROM api_keys
     WHERE active = true
)
UPDATE api_keys ak
   SET active = false
  FROM ranked r
 WHERE ak.id = r.id
   AND r.rn > 1;

COMMIT;

SELECT 'Migration 209_api_keys_invariants complete' AS status;
