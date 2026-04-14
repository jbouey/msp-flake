-- Migration 193: rollup MV live_status derived from heartbeats, not last_checkin.
--
-- Session 206 H5: make the dashboard's "online/stale/offline" status
-- orthogonal to site_appliances.last_checkin. Heartbeats are the append-only
-- source of truth (Migration 191). If last_checkin disagrees with heartbeats,
-- heartbeats win — this is the layer that makes the dashboard immune to
-- future site-wide UPDATE regressions in one direction.

BEGIN;

DROP MATERIALIZED VIEW IF EXISTS appliance_status_rollup CASCADE;

CREATE MATERIALIZED VIEW appliance_status_rollup AS
SELECT
    sa.appliance_id,
    sa.site_id,
    sa.hostname,
    sa.display_name,
    sa.mac_address,
    sa.ip_addresses,
    sa.agent_version,
    -- Live status computed from HEARTBEATS (Session 206 H5).
    -- last_checkin can lie via site-wide UPDATE bugs; heartbeats can't
    -- (INSERT-only, DELETE-blocked, per-appliance attribution).
    CASE
        WHEN hb.max_observed_at IS NULL THEN 'offline'
        WHEN hb.max_observed_at > NOW() - INTERVAL '90 seconds' THEN 'online'
        WHEN hb.max_observed_at > NOW() - INTERVAL '5 minutes' THEN 'stale'
        ELSE 'offline'
    END AS live_status,
    -- Preserved for backwards compat + drift detection.
    sa.last_checkin AS cached_last_checkin,
    hb.max_observed_at AS last_heartbeat_at,
    EXTRACT(EPOCH FROM (NOW() - COALESCE(hb.max_observed_at, sa.last_checkin)))::int AS stale_seconds,
    -- Drift: if cached_last_checkin is fresh but last_heartbeat_at is stale,
    -- one of the signals is lying. Surface this so operators see it.
    EXTRACT(EPOCH FROM (sa.last_checkin - COALESCE(hb.max_observed_at, sa.last_checkin)))::int AS liveness_drift_seconds,
    COALESCE(hb.checkin_count_24h, 0) AS checkin_count_24h,
    COALESCE(hb.online_count_24h, 0) AS online_count_24h,
    COALESCE(hb.online_count_24h::float / NULLIF(hb.checkin_count_24h, 0), 1.0)
        AS uptime_ratio_24h
FROM site_appliances sa
LEFT JOIN LATERAL (
    SELECT
        MAX(observed_at) AS max_observed_at,
        COUNT(*) AS checkin_count_24h,
        COUNT(*) FILTER (WHERE status = 'online') AS online_count_24h
    FROM appliance_heartbeats
    WHERE appliance_id = sa.appliance_id
      AND observed_at > NOW() - INTERVAL '24 hours'
) hb ON true
WHERE sa.deleted_at IS NULL;

CREATE UNIQUE INDEX idx_status_rollup_appliance
    ON appliance_status_rollup(appliance_id);
CREATE INDEX idx_status_rollup_site
    ON appliance_status_rollup(site_id);
CREATE INDEX idx_status_rollup_status
    ON appliance_status_rollup(live_status, stale_seconds DESC);

COMMENT ON MATERIALIZED VIEW appliance_status_rollup IS
    'Fleet rollup (Session 206 H5): live_status derived from heartbeats, '
    'not last_checkin. liveness_drift_seconds surfaces the gap between '
    'the two signals so operators can see when last_checkin is lying.';

COMMIT;
