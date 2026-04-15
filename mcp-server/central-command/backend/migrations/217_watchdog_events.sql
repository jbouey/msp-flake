-- Migration 217: watchdog event ledger
--
-- Phase W0 of the appliance hardening roadmap. Adds the append-only
-- audit ledger for the independent appliance-watchdog service — a
-- second systemd unit with its own Ed25519 identity + its own 2-min
-- checkin loop, consuming a tight whitelist of 6 fleet-order types
-- that recover a wedged main daemon WITHOUT requiring SSH:
--
--   watchdog_restart_daemon     — `systemctl restart appliance-daemon`
--   watchdog_refetch_config     — re-download config.yaml, atomic rename
--   watchdog_reset_pin_store    — delete /var/lib/msp/winrm_pins.json
--   watchdog_reset_api_key      — trigger /api/provision/rekey flow
--   watchdog_redeploy_daemon    — re-download + install daemon binary
--   watchdog_collect_diagnostics — bundle journal + state, upload
--
-- The watchdog's checkin payload AND every order outcome lands here
-- as an append-only row. Migration 218 extends v_privileged_types +
-- privileged-order UPDATE guard with the 6 new types so each order
-- requires the existing attestation chain (actor_email + reason +
-- compliance_bundle). assertions.py adds `watchdog_silent` (sev1)
-- and `watchdog_reports_daemon_down` (sev2) invariants that watch
-- this table for stale checkins + reported-daemon-down signals.
--
-- Together with the /api/watchdog/checkin + /api/watchdog/diagnostics
-- endpoints (watchdog_api.py) and the Go binary in Phase W1, this
-- closes the "daemon wedged, no remote recovery" brick-scenario that
-- currently forces SSH fallback.

BEGIN;

CREATE TABLE IF NOT EXISTS watchdog_events (
    id              BIGSERIAL PRIMARY KEY,
    site_id         VARCHAR(50)   NOT NULL,
    appliance_id    VARCHAR(255)  NOT NULL,
    event_type      VARCHAR(60)   NOT NULL CHECK (event_type IN (
        'checkin',
        'diagnostics_uploaded',
        'order_received',
        'order_executed',
        'order_failed'
    )),
    order_id        UUID,
    watchdog_order_type VARCHAR(60),
    payload         JSONB         NOT NULL DEFAULT '{}'::jsonb,
    chain_prev_hash TEXT,
    chain_hash      TEXT,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_watchdog_events_appliance_created
    ON watchdog_events (appliance_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_watchdog_events_order
    ON watchdog_events (order_id)
    WHERE order_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_watchdog_events_site_type_created
    ON watchdog_events (site_id, event_type, created_at DESC);

-- Append-only guard — reuses the same prevent_audit_deletion() function
-- Migration 151 installed for compliance_bundles + admin_audit_log.
DROP TRIGGER IF EXISTS prevent_watchdog_event_mutation ON watchdog_events;
CREATE TRIGGER prevent_watchdog_event_mutation
    BEFORE UPDATE OR DELETE ON watchdog_events
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_deletion();

COMMENT ON TABLE watchdog_events IS
    'Append-only event ledger for the appliance-watchdog service. '
    'Every 2-min checkin + every privileged order executed by the '
    'watchdog lands here. Hash-chained per-appliance via '
    '(chain_prev_hash, chain_hash). Read-only: UPDATE and DELETE are '
    'blocked by the prevent_audit_deletion trigger. Pair with '
    'substrate invariants watchdog_silent + watchdog_reports_daemon_down.';

COMMIT;
