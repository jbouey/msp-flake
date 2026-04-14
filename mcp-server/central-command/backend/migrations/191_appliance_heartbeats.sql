-- Migration 191: appliance_heartbeats event store + status rollup.
--
-- Today the only record of an appliance checkin is a single column on
-- site_appliances (last_checkin) which gets overwritten every cycle.
-- Consequences:
--   * cannot prove "appliance up 99.9% in last 30 days" for SLA / audit;
--   * cannot detect "missed N consecutive checkins" without history;
--   * dashboard polling re-reads the wide site_appliances table per viewer,
--     which doesn't scale past a couple hundred fleet × handful of viewers.
--
-- Fix: append-only, monthly-partitioned heartbeats + a 60s rollup that the
-- dashboard reads. Heartbeats keep 90 days hot; older partitions get
-- detached and copied to telemetry_archive (not in this migration —
-- archival is a separate worker).

BEGIN;

-- =============================================================================
-- appliance_heartbeats — append-only event store
-- =============================================================================

CREATE TABLE IF NOT EXISTS appliance_heartbeats (
    id              BIGSERIAL,
    site_id         TEXT NOT NULL,
    appliance_id    TEXT NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Server-assigned status at observation. Online when checkin landed in
    -- the last 90s, stale 90s-5min, offline beyond 5min.
    status          TEXT NOT NULL CHECK (status IN ('online', 'stale', 'offline')),
    -- Telemetry signal carried for diagnostics.
    agent_version   TEXT,
    boot_source     TEXT,
    primary_subnet  TEXT,
    has_anycast     BOOLEAN,
    PRIMARY KEY (id, observed_at)
) PARTITION BY RANGE (observed_at);

COMMENT ON TABLE appliance_heartbeats IS
    'Append-only checkin event log. One row per checkin per appliance. '
    'Used for cadence detection (missed >= 3 consecutive), uptime SLA, '
    'and dashboard rollups. Partitioned monthly. Older partitions are '
    'detached + archived to telemetry_archive after 90 days.';

CREATE INDEX IF NOT EXISTS idx_appliance_heartbeats_appliance_time
    ON appliance_heartbeats(appliance_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_appliance_heartbeats_site_time
    ON appliance_heartbeats(site_id, observed_at DESC);

-- Default partition catches any inserts that fall outside the named
-- monthly partitions. Operational invariant: a partition-creator
-- background task spins up next month's partition before month rollover.
CREATE TABLE IF NOT EXISTS appliance_heartbeats_default
    PARTITION OF appliance_heartbeats DEFAULT;

-- Initial partitions: current month + next month so we never write to
-- _default in normal operation.
DO $$
DECLARE
    cur_start DATE := date_trunc('month', NOW())::date;
    next_start DATE := (date_trunc('month', NOW()) + INTERVAL '1 month')::date;
    next_next_start DATE := (date_trunc('month', NOW()) + INTERVAL '2 month')::date;
    cur_name TEXT := 'appliance_heartbeats_y' || to_char(cur_start, 'YYYYmm');
    next_name TEXT := 'appliance_heartbeats_y' || to_char(next_start, 'YYYYmm');
BEGIN
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF appliance_heartbeats FOR VALUES FROM (%L) TO (%L)',
        cur_name, cur_start, next_start
    );
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF appliance_heartbeats FOR VALUES FROM (%L) TO (%L)',
        next_name, next_start, next_next_start
    );
END
$$;

-- =============================================================================
-- appliance_status_rollup — materialized view, refreshed every 60s
-- =============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS appliance_status_rollup AS
SELECT
    sa.appliance_id,
    sa.site_id,
    sa.hostname,
    sa.display_name,
    sa.mac_address,
    sa.ip_addresses,
    sa.agent_version,
    -- Live status computed from last_checkin (canonical signal).
    CASE
        WHEN sa.last_checkin > NOW() - INTERVAL '90 seconds' THEN 'online'
        WHEN sa.last_checkin > NOW() - INTERVAL '5 minutes' THEN 'stale'
        ELSE 'offline'
    END AS live_status,
    sa.last_checkin,
    EXTRACT(EPOCH FROM (NOW() - sa.last_checkin))::int AS stale_seconds,
    -- Heartbeat-derived metrics (last 24h).
    COALESCE(hb.checkin_count_24h, 0) AS checkin_count_24h,
    COALESCE(hb.online_count_24h, 0) AS online_count_24h,
    COALESCE(hb.online_count_24h::float / NULLIF(hb.checkin_count_24h, 0), 1.0)
        AS uptime_ratio_24h
FROM site_appliances sa
LEFT JOIN LATERAL (
    SELECT
        COUNT(*) AS checkin_count_24h,
        COUNT(*) FILTER (WHERE status = 'online') AS online_count_24h
    FROM appliance_heartbeats
    WHERE appliance_id = sa.appliance_id
      AND observed_at > NOW() - INTERVAL '24 hours'
) hb ON true
WHERE sa.deleted_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_status_rollup_appliance
    ON appliance_status_rollup(appliance_id);
CREATE INDEX IF NOT EXISTS idx_status_rollup_site
    ON appliance_status_rollup(site_id);
CREATE INDEX IF NOT EXISTS idx_status_rollup_status
    ON appliance_status_rollup(live_status, last_checkin DESC);

COMMENT ON MATERIALIZED VIEW appliance_status_rollup IS
    'Single-row-per-appliance rollup for the dashboard. Refreshed every 60s '
    'by a background task. SSE channel pushes deltas to subscribed viewers '
    'instead of every viewer polling site_appliances directly.';

-- =============================================================================
-- DELETE protection — append-only invariant.
-- =============================================================================
-- Heartbeats are evidence-grade for SLA claims. Treat them like
-- compliance_bundles: no DELETE allowed except via partition detachment.

CREATE OR REPLACE FUNCTION prevent_heartbeat_deletion()
RETURNS TRIGGER AS $$
BEGIN
    -- Allow partition detach (which uses DELETE under the hood with
    -- elevated context). Block direct row DELETEs.
    IF current_setting('app.allow_heartbeat_delete', TRUE) = 'true' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'appliance_heartbeats is append-only. Use partition detach + archive instead.'
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_heartbeat_deletion ON appliance_heartbeats;
CREATE TRIGGER trg_prevent_heartbeat_deletion
    BEFORE DELETE ON appliance_heartbeats
    FOR EACH ROW EXECUTE FUNCTION prevent_heartbeat_deletion();

COMMIT;
