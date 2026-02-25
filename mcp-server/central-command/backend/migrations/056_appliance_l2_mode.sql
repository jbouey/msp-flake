-- Migration 056: Per-appliance L2 healing mode toggle
--
-- Allows partners to control L2 (LLM) healing behavior per appliance:
--   'auto'     — L2 plans execute automatically (default)
--   'manual'   — L2 plans require human approval before execution
--   'disabled' — L2 planning is skipped entirely, only L1 deterministic runs
--
-- The checkin response returns this value so the Go daemon can respect it.

ALTER TABLE site_appliances
    ADD COLUMN IF NOT EXISTS l2_mode VARCHAR(10) DEFAULT 'auto'
    CHECK (l2_mode IN ('auto', 'manual', 'disabled'));

COMMENT ON COLUMN site_appliances.l2_mode IS
    'L2 healing mode: auto (execute immediately), manual (queue for approval), disabled (L1 only)';

-- Index for quick lookups when building checkin response
CREATE INDEX IF NOT EXISTS idx_site_appliances_l2_mode
    ON site_appliances(l2_mode) WHERE l2_mode != 'auto';

-- Rollback:
-- ALTER TABLE site_appliances DROP COLUMN IF EXISTS l2_mode;
