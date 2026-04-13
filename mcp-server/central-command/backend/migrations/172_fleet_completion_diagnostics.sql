-- Migration 172: diagnostic output on fleet_order_completions (Phase 12.1)
--
-- Session 205 tonight: an enable_emergency_access fleet order failed on
-- the primary appliance. fleet_order_completions captured only
-- status='failed'; no error string, no daemon stderr, no duration. We
-- had no remote path to diagnose — which is exactly when we need it
-- most. This migration adds the fields so the next time a fleet order
-- fails, the failure reason is visible in Central Command.
--
-- Columns added:
--   output          JSONB     — structured daemon payload (stdout,
--                               stderr, exit_code, execution_context)
--   error_message   TEXT      — short human-readable summary for admin UI
--   duration_ms     INTEGER   — wall-clock from daemon accept → complete
--   updated_at      TIMESTAMPTZ
--
-- The Go appliance daemon already includes `result` + `error_message`
-- in its POST /orders/{id}/complete body (per sites.py:2122-2125
-- OrderCompleteRequest schema). We just weren't persisting them on the
-- fleet-order code path. Backend-only change — no daemon update needed.
--
-- Backward compat: existing rows get NULL for the new fields. The
-- diagnostic UI renders "(no detail captured)" when NULL.

BEGIN;

ALTER TABLE fleet_order_completions
    ADD COLUMN IF NOT EXISTS output        JSONB,
    ADD COLUMN IF NOT EXISTS error_message TEXT,
    ADD COLUMN IF NOT EXISTS duration_ms   INTEGER,
    ADD COLUMN IF NOT EXISTS updated_at    TIMESTAMPTZ;

-- Index on unacked failures — primary diagnostic workflow hits this
CREATE INDEX IF NOT EXISTS idx_fleet_completions_failed
    ON fleet_order_completions (fleet_order_id)
    WHERE status = 'failed';

-- Trigger to maintain updated_at on status/output changes
CREATE OR REPLACE FUNCTION fleet_completion_touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_fleet_completion_touch ON fleet_order_completions;
CREATE TRIGGER trg_fleet_completion_touch
    BEFORE INSERT OR UPDATE ON fleet_order_completions
    FOR EACH ROW
    EXECUTE FUNCTION fleet_completion_touch_updated_at();

COMMIT;
