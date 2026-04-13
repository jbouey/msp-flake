-- Phase 15 closing — enterprise appliance offline detection.
--
-- Before this migration, `site_appliances.status` only moved to 'online'
-- during a successful check-in and no loop marked it 'offline' when
-- check-ins stopped. The dashboard derived offline on-the-fly from
-- last_checkin age, but:
--   - no state-transition event was recorded
--   - no notification fired
--   - different UI surfaces read `status` vs `live_status` inconsistently
--
-- Adds:
--   * index for the every-2-min stale-detection query
--   * `recovered_at` column so we can observe MTTR per appliance
--   * CHECK constraint narrowing valid statuses (catches drift via raw SQL)

BEGIN;

-- Narrow valid status values. 'pending' is set on row create before first
-- checkin; 'online' on successful checkin; 'offline' by the stale loop;
-- 'auth_failed' when checkins reach us but fail auth (already used in
-- calculate_live_status — status field now tracks it too).
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.check_constraints
    WHERE constraint_name = 'site_appliances_status_check'
  ) THEN
    ALTER TABLE site_appliances
      ADD CONSTRAINT site_appliances_status_check
      CHECK (status IN ('pending', 'online', 'offline', 'auth_failed', 'decommissioned'));
  END IF;
END $$;

-- Index supports the 2-min stale-detection scan:
--   UPDATE site_appliances
--   SET status='offline', offline_since=NOW()
--   WHERE status != 'offline' AND last_checkin < NOW() - '5 min'
CREATE INDEX IF NOT EXISTS idx_site_appliances_stale_detect
  ON site_appliances (last_checkin)
  WHERE status != 'offline' AND deleted_at IS NULL;

-- Track recovery timestamp for MTTR observability. NULL until we've
-- seen one offline→online transition.
ALTER TABLE site_appliances
  ADD COLUMN IF NOT EXISTS recovered_at TIMESTAMPTZ;

-- Total offline transitions — for reliability SLO computation.
-- Increments on every offline detection (not just the first).
ALTER TABLE site_appliances
  ADD COLUMN IF NOT EXISTS offline_event_count INTEGER NOT NULL DEFAULT 0;

COMMIT;
