-- Migration 100: Flywheel/Promotion System Improvements
-- 1. promotion_audit_log table (append-only, WORM-style telemetry archive)
-- 2. Append-only trigger to prevent UPDATE/DELETE on promotion_audit_log

BEGIN;

-- Promotion audit log: immutable record of every promotion decision
CREATE TABLE IF NOT EXISTS promotion_audit_log (
    id BIGSERIAL PRIMARY KEY,
    promotion_id UUID NOT NULL DEFAULT gen_random_uuid(),
    event_type VARCHAR(30) NOT NULL,  -- 'approved', 'rejected', 'auto_promoted', 'auto_disabled', 'synced'
    rule_id VARCHAR(255),
    pattern_signature VARCHAR(255),
    check_type VARCHAR(100),
    site_id VARCHAR(255),
    confidence_score FLOAT,
    success_rate FLOAT,
    l2_resolutions INTEGER,
    total_occurrences INTEGER,
    source VARCHAR(30),  -- 'partner', 'platform', 'auto'
    actor VARCHAR(255),  -- partner_id or 'system'
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_promotion_audit_log_rule ON promotion_audit_log(rule_id);
CREATE INDEX IF NOT EXISTS idx_promotion_audit_log_site ON promotion_audit_log(site_id);
CREATE INDEX IF NOT EXISTS idx_promotion_audit_log_type ON promotion_audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_promotion_audit_log_created ON promotion_audit_log(created_at);

-- Append-only trigger: prevent UPDATE/DELETE on promotion_audit_log
CREATE OR REPLACE FUNCTION prevent_promotion_audit_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'promotion_audit_log is append-only: % not allowed', TG_OP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_promotion_audit_immutable ON promotion_audit_log;
CREATE TRIGGER trg_promotion_audit_immutable
    BEFORE UPDATE OR DELETE ON promotion_audit_log
    FOR EACH ROW EXECUTE FUNCTION prevent_promotion_audit_mutation();

COMMIT;
