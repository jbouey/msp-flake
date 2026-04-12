-- Migration 160: Time-travel reconciliation foundation (Session 205)
--
-- Adds state columns + audit table for detecting and recovering from
-- agent time-travel events: VM snapshot revert, backup restore, disk
-- image clone, power-loss journal rollback, hardware replacement.
--
-- Security model: reconcile plans are Ed25519-signed by Central Command
-- using the per-appliance signing key. Agent validates signature before
-- applying. Attacker with MITM cannot forge plans without the key.
-- Nonce epoch advances on reconcile — invalidates any captured orders
-- from the previous epoch. Single-shot replay protection.

-- 1. Append state columns to site_appliances
ALTER TABLE site_appliances
    ADD COLUMN IF NOT EXISTS boot_counter BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS generation_uuid UUID,
    ADD COLUMN IF NOT EXISTS nonce_epoch BYTEA,
    ADD COLUMN IF NOT EXISTS last_reconcile_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS reconcile_count INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN site_appliances.boot_counter IS
    'Monotonic counter the agent increments on each boot and writes to '
    '/var/lib/msp/boot_counter. Central Command tracks max seen; a lower '
    'value than last seen signals time-travel.';

COMMENT ON COLUMN site_appliances.generation_uuid IS
    'Random UUID the agent writes to /var/lib/msp/generation on each '
    '"known good" checkin. Central Command tracks current value; mismatch '
    'signals the agent reverted to an earlier state.';

COMMENT ON COLUMN site_appliances.nonce_epoch IS
    'Random 32-byte seed for order nonce namespace. Advances on every '
    'reconcile to invalidate any captured orders from the previous epoch. '
    'Single-shot replay protection with O(1) state.';

CREATE INDEX IF NOT EXISTS idx_site_appliances_boot_counter
    ON site_appliances(site_id, boot_counter DESC);


-- 2. Reconcile events audit table — forensics for every time-travel
CREATE TABLE IF NOT EXISTS reconcile_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    appliance_id VARCHAR(255) NOT NULL,
    site_id VARCHAR(255) NOT NULL,
    -- Detection state at time of event
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    detection_signals JSONB NOT NULL DEFAULT '[]'::jsonb,  -- array of signal codes
    reported_boot_counter BIGINT,
    last_known_boot_counter BIGINT,
    reported_generation_uuid UUID,
    last_known_generation_uuid UUID,
    reported_uptime_seconds BIGINT,
    clock_skew_seconds INTEGER,
    -- Response state
    plan_generated_at TIMESTAMPTZ,
    plan_runbook_ids TEXT[],             -- runbooks included in the plan
    plan_signature_hex VARCHAR(128),     -- Ed25519 signature (hex)
    plan_nonce_epoch_hex VARCHAR(64),    -- new epoch delivered (hex)
    plan_applied_at TIMESTAMPTZ,
    plan_status VARCHAR(30) NOT NULL DEFAULT 'pending',  -- pending|applied|failed|rejected
    error_message TEXT,
    -- Post-reconcile state
    post_boot_counter BIGINT,
    post_generation_uuid UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT reconcile_events_status_check CHECK (
        plan_status IN ('pending', 'applied', 'failed', 'rejected', 'expired')
    )
);

CREATE INDEX IF NOT EXISTS idx_reconcile_events_appliance
    ON reconcile_events(appliance_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_reconcile_events_site
    ON reconcile_events(site_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_reconcile_events_status
    ON reconcile_events(plan_status, detected_at DESC);

-- Append-only: DELETE trigger blocks manual tampering
CREATE OR REPLACE FUNCTION prevent_reconcile_events_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'reconcile_events is append-only — DELETE not permitted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_block_reconcile_delete ON reconcile_events;
CREATE TRIGGER trg_block_reconcile_delete
    BEFORE DELETE ON reconcile_events
    FOR EACH ROW EXECUTE FUNCTION prevent_reconcile_events_delete();

-- RLS
ALTER TABLE reconcile_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE reconcile_events FORCE ROW LEVEL SECURITY;

CREATE POLICY admin_bypass ON reconcile_events
    FOR ALL USING (current_setting('app.is_admin', true) = 'true');

CREATE POLICY tenant_isolation ON reconcile_events
    FOR ALL USING (site_id = current_setting('app.current_tenant', true));

COMMENT ON TABLE reconcile_events IS
    'Append-only audit of time-travel reconciliation events. Every detected '
    'time-travel + the plan generated + the outcome. Forensics-grade record '
    'so customer auditors can verify the platform handled restores safely.';
