-- Migration 140: Flywheel Round-Table Hardening
-- RT-7: l1_rules source CHECK constraint
-- RT-6: execution_telemetry retention index

-- =============================================================================
-- 1. Source CHECK constraint on l1_rules
-- =============================================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_l1_rules_source'
    ) THEN
        -- First normalize any invalid source values
        UPDATE l1_rules SET source = 'built-in'
        WHERE source NOT IN ('built-in', 'synced', 'promoted', 'platform');

        ALTER TABLE l1_rules
            ADD CONSTRAINT chk_l1_rules_source
            CHECK (source IN ('built-in', 'synced', 'promoted', 'platform'));
    END IF;
END $$;

-- =============================================================================
-- 2. Execution telemetry retention index (for 90-day cleanup)
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_execution_telemetry_created_at
    ON execution_telemetry (created_at);

-- =============================================================================
-- 3. Promotion audit log: ensure immutable trigger exists
-- =============================================================================
-- (Migration 100 created this, but verify)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.triggers
        WHERE trigger_name = 'trg_promotion_audit_immutable'
    ) THEN
        CREATE OR REPLACE FUNCTION prevent_audit_modification()
        RETURNS TRIGGER AS $fn$
        BEGIN
            RAISE EXCEPTION 'promotion_audit_log is append-only (WORM)';
        END;
        $fn$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_promotion_audit_immutable
            BEFORE UPDATE OR DELETE ON promotion_audit_log
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();
    END IF;
END $$;

-- =============================================================================
-- 4. Platform pattern stats: add missing index
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_platform_pattern_stats_promoted
    ON platform_pattern_stats (promoted_at) WHERE promoted_at IS NULL;
