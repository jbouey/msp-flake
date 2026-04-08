-- Migration 138: Partition compliance_bundles and portal_access_log by month
-- Reason: Both are unbounded append-only tables that will grow to millions
-- of rows at 50+ orgs. Monthly partitions enable efficient pruning and
-- archival (DETACH PARTITION → S3) for HIPAA 6-year retention.
--
-- Strategy: Rename existing table → create partitioned table → copy data →
-- create partitions for existing + future months.
-- NOTE: This migration may take time on large tables. Run during maintenance window.

-- ============================================================================
-- 1. COMPLIANCE BUNDLES (231K+ rows, growing ~750K/year at 50 orgs)
-- ============================================================================

-- Only proceed if not already partitioned
DO $$
BEGIN
    -- Check if compliance_bundles is already partitioned
    IF NOT EXISTS (
        SELECT 1 FROM pg_partitioned_table
        WHERE partrelid = 'compliance_bundles'::regclass
    ) THEN
        -- Rename the old table
        ALTER TABLE compliance_bundles RENAME TO compliance_bundles_old;

        -- Create partitioned table with same structure
        CREATE TABLE compliance_bundles (
            LIKE compliance_bundles_old INCLUDING DEFAULTS INCLUDING CONSTRAINTS
        ) PARTITION BY RANGE (created_at);

        -- Create monthly partitions: 2025-01 through 2027-12
        -- (covers historical data + 18 months forward)
        CREATE TABLE compliance_bundles_2025_01 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
        CREATE TABLE compliance_bundles_2025_02 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
        CREATE TABLE compliance_bundles_2025_03 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
        CREATE TABLE compliance_bundles_2025_04 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
        CREATE TABLE compliance_bundles_2025_05 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
        CREATE TABLE compliance_bundles_2025_06 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
        CREATE TABLE compliance_bundles_2025_07 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
        CREATE TABLE compliance_bundles_2025_08 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
        CREATE TABLE compliance_bundles_2025_09 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
        CREATE TABLE compliance_bundles_2025_10 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
        CREATE TABLE compliance_bundles_2025_11 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
        CREATE TABLE compliance_bundles_2025_12 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
        CREATE TABLE compliance_bundles_2026_01 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
        CREATE TABLE compliance_bundles_2026_02 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
        CREATE TABLE compliance_bundles_2026_03 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
        CREATE TABLE compliance_bundles_2026_04 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
        CREATE TABLE compliance_bundles_2026_05 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
        CREATE TABLE compliance_bundles_2026_06 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
        CREATE TABLE compliance_bundles_2026_07 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
        CREATE TABLE compliance_bundles_2026_08 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
        CREATE TABLE compliance_bundles_2026_09 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
        CREATE TABLE compliance_bundles_2026_10 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
        CREATE TABLE compliance_bundles_2026_11 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
        CREATE TABLE compliance_bundles_2026_12 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
        CREATE TABLE compliance_bundles_2027_01 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');
        CREATE TABLE compliance_bundles_2027_02 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');
        CREATE TABLE compliance_bundles_2027_03 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');
        CREATE TABLE compliance_bundles_2027_04 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');
        CREATE TABLE compliance_bundles_2027_05 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2027-05-01') TO ('2027-06-01');
        CREATE TABLE compliance_bundles_2027_06 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2027-06-01') TO ('2027-07-01');
        CREATE TABLE compliance_bundles_2027_07 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2027-07-01') TO ('2027-08-01');
        CREATE TABLE compliance_bundles_2027_08 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2027-08-01') TO ('2027-09-01');
        CREATE TABLE compliance_bundles_2027_09 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2027-09-01') TO ('2027-10-01');
        CREATE TABLE compliance_bundles_2027_10 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2027-10-01') TO ('2027-11-01');
        CREATE TABLE compliance_bundles_2027_11 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2027-11-01') TO ('2027-12-01');
        CREATE TABLE compliance_bundles_2027_12 PARTITION OF compliance_bundles
            FOR VALUES FROM ('2027-12-01') TO ('2028-01-01');

        -- Default partition catches any rows outside defined ranges
        CREATE TABLE compliance_bundles_default PARTITION OF compliance_bundles DEFAULT;

        -- Copy data from old table
        INSERT INTO compliance_bundles SELECT * FROM compliance_bundles_old;

        -- Recreate indexes on partitioned table
        CREATE INDEX IF NOT EXISTS idx_compliance_bundles_site_created
            ON compliance_bundles(site_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_compliance_bundles_created
            ON compliance_bundles(created_at DESC);

        -- Re-enable RLS
        ALTER TABLE compliance_bundles ENABLE ROW LEVEL SECURITY;
        ALTER TABLE compliance_bundles FORCE ROW LEVEL SECURITY;
        CREATE POLICY admin_bypass ON compliance_bundles
            FOR ALL USING (current_setting('app.is_admin', true) = 'true');
        CREATE POLICY tenant_isolation ON compliance_bundles
            FOR ALL USING (site_id = current_setting('app.current_tenant', true));

        -- Drop old table
        DROP TABLE compliance_bundles_old;

        RAISE NOTICE 'compliance_bundles partitioned successfully';
    ELSE
        RAISE NOTICE 'compliance_bundles already partitioned, skipping';
    END IF;
END $$;


-- ============================================================================
-- 2. PORTAL ACCESS LOG (unbounded, no archival)
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_partitioned_table
        WHERE partrelid = 'portal_access_log'::regclass
    ) THEN
        ALTER TABLE portal_access_log RENAME TO portal_access_log_old;

        CREATE TABLE portal_access_log (
            LIKE portal_access_log_old INCLUDING DEFAULTS INCLUDING CONSTRAINTS
        ) PARTITION BY RANGE (accessed_at);

        -- Monthly partitions: 2025-01 through 2027-12
        CREATE TABLE portal_access_log_2025_01 PARTITION OF portal_access_log
            FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');
        CREATE TABLE portal_access_log_2025_02 PARTITION OF portal_access_log
            FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');
        CREATE TABLE portal_access_log_2025_03 PARTITION OF portal_access_log
            FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');
        CREATE TABLE portal_access_log_2025_04 PARTITION OF portal_access_log
            FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');
        CREATE TABLE portal_access_log_2025_05 PARTITION OF portal_access_log
            FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
        CREATE TABLE portal_access_log_2025_06 PARTITION OF portal_access_log
            FOR VALUES FROM ('2025-06-01') TO ('2025-07-01');
        CREATE TABLE portal_access_log_2025_07 PARTITION OF portal_access_log
            FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');
        CREATE TABLE portal_access_log_2025_08 PARTITION OF portal_access_log
            FOR VALUES FROM ('2025-08-01') TO ('2025-09-01');
        CREATE TABLE portal_access_log_2025_09 PARTITION OF portal_access_log
            FOR VALUES FROM ('2025-09-01') TO ('2025-10-01');
        CREATE TABLE portal_access_log_2025_10 PARTITION OF portal_access_log
            FOR VALUES FROM ('2025-10-01') TO ('2025-11-01');
        CREATE TABLE portal_access_log_2025_11 PARTITION OF portal_access_log
            FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
        CREATE TABLE portal_access_log_2025_12 PARTITION OF portal_access_log
            FOR VALUES FROM ('2025-12-01') TO ('2026-01-01');
        CREATE TABLE portal_access_log_2026_01 PARTITION OF portal_access_log
            FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
        CREATE TABLE portal_access_log_2026_02 PARTITION OF portal_access_log
            FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
        CREATE TABLE portal_access_log_2026_03 PARTITION OF portal_access_log
            FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
        CREATE TABLE portal_access_log_2026_04 PARTITION OF portal_access_log
            FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
        CREATE TABLE portal_access_log_2026_05 PARTITION OF portal_access_log
            FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
        CREATE TABLE portal_access_log_2026_06 PARTITION OF portal_access_log
            FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
        CREATE TABLE portal_access_log_2026_07 PARTITION OF portal_access_log
            FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
        CREATE TABLE portal_access_log_2026_08 PARTITION OF portal_access_log
            FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
        CREATE TABLE portal_access_log_2026_09 PARTITION OF portal_access_log
            FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
        CREATE TABLE portal_access_log_2026_10 PARTITION OF portal_access_log
            FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
        CREATE TABLE portal_access_log_2026_11 PARTITION OF portal_access_log
            FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
        CREATE TABLE portal_access_log_2026_12 PARTITION OF portal_access_log
            FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
        CREATE TABLE portal_access_log_2027_01 PARTITION OF portal_access_log
            FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');
        CREATE TABLE portal_access_log_2027_02 PARTITION OF portal_access_log
            FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');
        CREATE TABLE portal_access_log_2027_03 PARTITION OF portal_access_log
            FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');
        CREATE TABLE portal_access_log_2027_04 PARTITION OF portal_access_log
            FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');
        CREATE TABLE portal_access_log_2027_05 PARTITION OF portal_access_log
            FOR VALUES FROM ('2027-05-01') TO ('2027-06-01');
        CREATE TABLE portal_access_log_2027_06 PARTITION OF portal_access_log
            FOR VALUES FROM ('2027-06-01') TO ('2027-07-01');
        CREATE TABLE portal_access_log_default PARTITION OF portal_access_log DEFAULT;

        INSERT INTO portal_access_log SELECT * FROM portal_access_log_old;

        CREATE INDEX IF NOT EXISTS idx_portal_access_log_site_accessed
            ON portal_access_log(site_id, accessed_at DESC);

        DROP TABLE portal_access_log_old;

        RAISE NOTICE 'portal_access_log partitioned successfully';
    ELSE
        RAISE NOTICE 'portal_access_log already partitioned, skipping';
    END IF;
END $$;
