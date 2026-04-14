-- Partner portal weekly rollup materialized view (Session 206 round-table P2).
--
-- Replaces 7 individual aggregate queries per partner dashboard load with a
-- single pre-computed view. Refreshed every 30 min by
-- `weekly_rollup_refresh_loop` in background_tasks.py.
--
-- CONCURRENTLY refresh requires a UNIQUE index on the matview, which is
-- why `(partner_id, site_id)` is declared unique below.

BEGIN;

DROP MATERIALIZED VIEW IF EXISTS partner_site_weekly_rollup;

CREATE MATERIALIZED VIEW partner_site_weekly_rollup AS
SELECT
    s.partner_id,
    s.site_id,
    s.clinic_name,
    -- Last 7 days of incidents
    COALESCE(SUM(
        CASE WHEN i.created_at > NOW() - INTERVAL '7 days' THEN 1 ELSE 0 END
    ), 0) AS incidents_7d,
    COALESCE(SUM(
        CASE WHEN i.created_at > NOW() - INTERVAL '7 days'
              AND i.resolution_tier = 'L1' THEN 1 ELSE 0 END
    ), 0) AS l1_7d,
    COALESCE(SUM(
        CASE WHEN i.created_at > NOW() - INTERVAL '7 days'
              AND i.resolution_tier = 'L2' THEN 1 ELSE 0 END
    ), 0) AS l2_7d,
    COALESCE(SUM(
        CASE WHEN i.created_at > NOW() - INTERVAL '7 days'
              AND i.resolution_tier = 'L3' THEN 1 ELSE 0 END
    ), 0) AS l3_7d,
    -- 24h slice
    COALESCE(SUM(
        CASE WHEN i.created_at > NOW() - INTERVAL '24 hours' THEN 1 ELSE 0 END
    ), 0) AS incidents_24h,
    COALESCE(SUM(
        CASE WHEN i.created_at > NOW() - INTERVAL '24 hours'
              AND i.resolution_tier = 'L1' THEN 1 ELSE 0 END
    ), 0) AS l1_24h,
    -- Derived: self-heal %. NULLIF avoids div/0; clients with no incidents
    -- default to 100% because "nothing broke" is the happy path.
    CASE WHEN COUNT(i.id) FILTER (WHERE i.created_at > NOW() - INTERVAL '7 days') > 0
         THEN ROUND(
             100.0 * COUNT(i.id) FILTER (WHERE i.created_at > NOW() - INTERVAL '7 days'
                                            AND i.resolution_tier = 'L1')::numeric /
             NULLIF(COUNT(i.id) FILTER (WHERE i.created_at > NOW() - INTERVAL '7 days'), 0)::numeric,
             1
         )
         ELSE 100.0
    END AS self_heal_rate_7d_pct,
    NOW() AS computed_at
FROM sites s
LEFT JOIN incidents i ON i.site_id = s.site_id
WHERE s.status != 'inactive'
GROUP BY s.partner_id, s.site_id, s.clinic_name;

-- UNIQUE index required for REFRESH MATERIALIZED VIEW CONCURRENTLY
CREATE UNIQUE INDEX IF NOT EXISTS ix_partner_site_weekly_rollup_pk
    ON partner_site_weekly_rollup (partner_id, site_id);

-- Lookup index for partner-scoped reads
CREATE INDEX IF NOT EXISTS ix_partner_site_weekly_rollup_partner
    ON partner_site_weekly_rollup (partner_id);

-- Initial population
REFRESH MATERIALIZED VIEW partner_site_weekly_rollup;

COMMIT;
