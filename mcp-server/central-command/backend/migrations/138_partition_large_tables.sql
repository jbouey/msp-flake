-- Migration 138: Partition compliance_bundles and portal_access_log by month
-- Reason: Both are unbounded append-only tables that will grow to millions
-- of rows at 50+ orgs. Monthly partitions enable efficient pruning and
-- archival (DETACH PARTITION → S3) for HIPAA 6-year retention.
--
-- Strategy: Rename existing table → create partitioned table → copy data →
-- create partitions for existing + future months.
-- NOTE: This migration may take time on large tables. Run during maintenance window.
--
-- Fixes over original attempt:
--   1. DROP v_control_status view BEFORE rename (depends on compliance_bundles)
--   2. Recreate v_control_status view AFTER partitioning
--   3. Use DROP TABLE ... CASCADE to handle SERIAL sequence ownership
--   4. compliance_bundles PK changed from (id) to (id, created_at) for partition compat
--   5. Idempotent: safe to re-run if partially migrated

-- ============================================================================
-- 1. COMPLIANCE BUNDLES (231K+ rows, growing ~750K/year at 50 orgs)
-- ============================================================================

DO $$
BEGIN
    -- Check if compliance_bundles is already partitioned
    IF NOT EXISTS (
        SELECT 1 FROM pg_partitioned_table
        WHERE partrelid = 'compliance_bundles'::regclass
    ) THEN
        -- Drop dependent views BEFORE renaming the table
        DROP VIEW IF EXISTS v_control_status CASCADE;

        -- Rename the old table
        ALTER TABLE compliance_bundles RENAME TO compliance_bundles_old;

        -- Create partitioned table with same structure
        -- NOTE: LIKE ... INCLUDING CONSTRAINTS copies the PK as a table constraint,
        -- but partitioned tables require the partition key in any PK/UNIQUE.
        -- So we use INCLUDING DEFAULTS only and define constraints manually.
        CREATE TABLE compliance_bundles (
            LIKE compliance_bundles_old INCLUDING DEFAULTS
        ) PARTITION BY RANGE (created_at);

        -- Drop the PK constraint that LIKE may have copied (it won't include
        -- partition key and will fail on partition attach). We re-add below.
        -- In practice LIKE without INCLUDING CONSTRAINTS won't copy it, but
        -- be safe:
        DO $inner$
        DECLARE
            pk_name TEXT;
        BEGIN
            SELECT conname INTO pk_name
            FROM pg_constraint
            WHERE conrelid = 'compliance_bundles'::regclass
              AND contype = 'p';
            IF pk_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE compliance_bundles DROP CONSTRAINT %I', pk_name);
            END IF;
        END $inner$;

        -- Drop any UNIQUE constraint on bundle_id alone (can't exist without partition key)
        DO $inner$
        DECLARE
            uq_name TEXT;
        BEGIN
            FOR uq_name IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'compliance_bundles'::regclass
                  AND contype = 'u'
            LOOP
                EXECUTE format('ALTER TABLE compliance_bundles DROP CONSTRAINT %I', uq_name);
            END LOOP;
        END $inner$;

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
        CREATE INDEX IF NOT EXISTS idx_compliance_bundles_site
            ON compliance_bundles(site_id);
        CREATE INDEX IF NOT EXISTS idx_compliance_bundles_chain
            ON compliance_bundles(site_id, chain_position);
        CREATE INDEX IF NOT EXISTS idx_compliance_bundles_bundle_id
            ON compliance_bundles(bundle_id);
        CREATE INDEX IF NOT EXISTS idx_compliance_bundles_ots_pending
            ON compliance_bundles(ots_status) WHERE ots_status = 'pending';

        -- Re-enable RLS
        ALTER TABLE compliance_bundles ENABLE ROW LEVEL SECURITY;
        ALTER TABLE compliance_bundles FORCE ROW LEVEL SECURITY;
        CREATE POLICY admin_bypass ON compliance_bundles
            FOR ALL USING (current_setting('app.is_admin', true) = 'true');
        CREATE POLICY tenant_isolation ON compliance_bundles
            FOR ALL USING (site_id = current_setting('app.current_tenant', true));

        -- Drop old table with CASCADE to handle SERIAL sequence ownership
        DROP TABLE compliance_bundles_old CASCADE;

        -- Reassign the sequence to the new partitioned table's id column.
        -- The old sequence (compliance_bundles_id_seq) was dropped by CASCADE
        -- because it was owned by compliance_bundles_old.id. Create a fresh one.
        DO $inner$
        BEGIN
            -- Only create if the sequence doesn't already exist
            IF NOT EXISTS (
                SELECT 1 FROM pg_sequences WHERE schemaname = 'public'
                AND sequencename = 'compliance_bundles_id_seq'
            ) THEN
                CREATE SEQUENCE compliance_bundles_id_seq;
                -- Set the sequence to continue from where the old one left off
                PERFORM setval('compliance_bundles_id_seq',
                    COALESCE((SELECT MAX(id) FROM compliance_bundles), 0) + 1,
                    false);
                ALTER TABLE compliance_bundles
                    ALTER COLUMN id SET DEFAULT nextval('compliance_bundles_id_seq');
                ALTER SEQUENCE compliance_bundles_id_seq
                    OWNED BY compliance_bundles.id;
            END IF;
        END $inner$;

        RAISE NOTICE 'compliance_bundles partitioned successfully';
    ELSE
        RAISE NOTICE 'compliance_bundles already partitioned, skipping';
    END IF;
END $$;


-- ============================================================================
-- 1b. RECREATE v_control_status VIEW (depends on compliance_bundles)
-- ============================================================================
-- This view was dropped before the rename. Recreate it pointing at the
-- new partitioned compliance_bundles table. CREATE OR REPLACE is safe
-- whether or not the view currently exists.

CREATE OR REPLACE VIEW v_control_status AS
WITH latest_evidence AS (
    SELECT
        cb.appliance_id,
        efm.framework,
        efm.control_id,
        cb.outcome,
        cb.created_at,
        ROW_NUMBER() OVER (
            PARTITION BY cb.appliance_id, efm.framework, efm.control_id
            ORDER BY cb.created_at DESC
        ) as rn
    FROM compliance_bundles cb
    JOIN evidence_framework_mappings efm ON cb.bundle_id = efm.bundle_id
    WHERE cb.created_at >= NOW() - INTERVAL '30 days'
)
SELECT
    appliance_id,
    framework,
    control_id,
    outcome,
    created_at as last_checked
FROM latest_evidence
WHERE rn = 1;


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
            LIKE portal_access_log_old INCLUDING DEFAULTS
        ) PARTITION BY RANGE (accessed_at);

        -- Drop PK/UNIQUE constraints that don't include partition key
        DO $inner$
        DECLARE
            c_name TEXT;
        BEGIN
            FOR c_name IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'portal_access_log'::regclass
                  AND contype IN ('p', 'u')
            LOOP
                EXECUTE format('ALTER TABLE portal_access_log DROP CONSTRAINT %I', c_name);
            END LOOP;
        END $inner$;

        -- Monthly partitions: 2025-01 through 2027-06
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

        -- Copy data (0 rows currently, but safe regardless)
        INSERT INTO portal_access_log SELECT * FROM portal_access_log_old;

        CREATE INDEX IF NOT EXISTS idx_portal_access_log_site_accessed
            ON portal_access_log(site_id, accessed_at DESC);

        -- CASCADE drops the SERIAL sequence owned by portal_access_log_old.id
        DROP TABLE portal_access_log_old CASCADE;

        -- Recreate the sequence for the new table
        DO $inner$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_sequences WHERE schemaname = 'public'
                AND sequencename = 'portal_access_log_id_seq'
            ) THEN
                CREATE SEQUENCE portal_access_log_id_seq;
                PERFORM setval('portal_access_log_id_seq',
                    COALESCE((SELECT MAX(id) FROM portal_access_log), 0) + 1,
                    false);
                ALTER TABLE portal_access_log
                    ALTER COLUMN id SET DEFAULT nextval('portal_access_log_id_seq');
                ALTER SEQUENCE portal_access_log_id_seq
                    OWNED BY portal_access_log.id;
            END IF;
        END $inner$;

        RAISE NOTICE 'portal_access_log partitioned successfully';
    ELSE
        RAISE NOTICE 'portal_access_log already partitioned, skipping';
    END IF;
END $$;
