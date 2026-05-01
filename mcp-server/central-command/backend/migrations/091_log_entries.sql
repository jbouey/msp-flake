-- Migration 091: Log entries table for centralized log aggregation
-- Partitioned by month with BRIN indexes for time-series queries
-- RLS enforced for tenant isolation

-- ============================================================================
-- 1. Create partitioned log_entries table
-- ============================================================================
CREATE TABLE IF NOT EXISTS log_entries (
    id BIGSERIAL,
    site_id VARCHAR(255) NOT NULL,
    hostname VARCHAR(255) NOT NULL,
    unit VARCHAR(128) NOT NULL,
    priority SMALLINT NOT NULL DEFAULT 6,
    timestamp TIMESTAMPTZ NOT NULL,
    message TEXT NOT NULL,
    boot_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

-- Create partitions for current and next 2 months
DO $$
DECLARE
    start_date DATE;
    end_date DATE;
    part_name TEXT;
BEGIN
    FOR i IN 0..2 LOOP
        start_date := date_trunc('month', CURRENT_DATE + (i || ' months')::interval)::date;  -- noqa: sql-fn-interval-concat — `i` is a PL/pgSQL FOR-loop counter, not asyncpg-bound; safe.
        end_date := (start_date + interval '1 month')::date;
        part_name := 'log_entries_' || to_char(start_date, 'YYYY_MM');

        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = part_name) THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF log_entries FOR VALUES FROM (%L) TO (%L)',
                part_name, start_date, end_date
            );
            RAISE NOTICE 'Created partition %', part_name;
        END IF;
    END LOOP;
END $$;

-- ============================================================================
-- 2. Indexes
-- ============================================================================

-- BRIN index on timestamp (ideal for append-only time-series, very small)
CREATE INDEX IF NOT EXISTS idx_log_entries_timestamp_brin
    ON log_entries USING BRIN (timestamp) WITH (pages_per_range = 32);

-- B-tree for per-site queries
CREATE INDEX IF NOT EXISTS idx_log_entries_site_timestamp
    ON log_entries (site_id, timestamp DESC);

-- GIN for full-text search on message
CREATE INDEX IF NOT EXISTS idx_log_entries_message_fts
    ON log_entries USING GIN (to_tsvector('english', message));

-- Unit filter
CREATE INDEX IF NOT EXISTS idx_log_entries_unit
    ON log_entries (site_id, unit, timestamp DESC);

-- ============================================================================
-- 3. RLS policies
-- ============================================================================
ALTER TABLE log_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE log_entries FORCE ROW LEVEL SECURITY;

CREATE POLICY log_entries_admin_bypass ON log_entries
    USING ((current_setting('app.is_admin', true))::boolean = true);

CREATE POLICY log_entries_tenant_isolation ON log_entries
    USING ((site_id)::text = current_setting('app.current_tenant', true));

CREATE POLICY log_entries_org_isolation ON log_entries
    USING ((site_id)::text IN (
        SELECT sites.site_id FROM sites
        WHERE sites.client_org_id = (NULLIF(current_setting('app.current_org', true), ''))::uuid
    ));

-- ============================================================================
-- 4. Grant permissions to mcp_app
-- ============================================================================
GRANT SELECT, INSERT, DELETE ON log_entries TO mcp_app;
GRANT USAGE, SELECT ON SEQUENCE log_entries_id_seq TO mcp_app;
