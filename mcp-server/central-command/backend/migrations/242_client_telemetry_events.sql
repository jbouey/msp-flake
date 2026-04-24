-- Migration 242: client_telemetry_events table.
--
-- Session 210 (2026-04-24) Layer 3 of enterprise API reliability. The
-- frontend's apiFieldGuard.requireField emits a FIELD_UNDEFINED event
-- when code reads an expected field that's undefined — i.e., the
-- backend contract has drifted from what the frontend expected.
--
-- Events aggregate here. The `frontend_field_undefined_spike` substrate
-- invariant reads from this table and fires sev2 when N events land in
-- a 5-minute window.
--
-- Retention: 30 days. Telemetry is diagnostic, not evidence — no HIPAA
-- long-retention requirement. A daily prune via cron (or manual DELETE)
-- keeps the table compact.

BEGIN;

CREATE TABLE IF NOT EXISTS client_telemetry_events (
    id BIGSERIAL PRIMARY KEY,
    event_kind VARCHAR(40) NOT NULL,
    endpoint VARCHAR(200) NOT NULL,
    field_name VARCHAR(100) NOT NULL,
    component VARCHAR(100),
    observed_type VARCHAR(30) NOT NULL,
    page VARCHAR(200),
    client_ts TIMESTAMP WITH TIME ZONE,
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    user_id INTEGER,
    ip_address VARCHAR(64),
    user_agent VARCHAR(200),
    CONSTRAINT event_kind_known CHECK (
        event_kind IN ('FIELD_UNDEFINED')
    )
);

-- The invariant queries by (recorded_at window, endpoint+field group-by).
CREATE INDEX IF NOT EXISTS idx_client_telemetry_recorded_at
    ON client_telemetry_events (recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_client_telemetry_endpoint_field
    ON client_telemetry_events (endpoint, field_name, recorded_at DESC);

COMMENT ON TABLE client_telemetry_events IS
    'Browser-side contract-drift events. Ingested by /api/admin/telemetry/'
    'client-field-undefined. Read by substrate invariant '
    'frontend_field_undefined_spike. 30-day retention; prune via cron.';

COMMIT;
