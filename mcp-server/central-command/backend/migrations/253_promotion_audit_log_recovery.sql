-- Migration 253: Audit dead-letter queue for promotion_audit_log.
--
-- Round-table 2026-04-28 finding (Angle 3, P0): the savepoint added
-- in commit efe413cf around `INSERT INTO promotion_audit_log` traded
-- transaction-poison for SILENT audit-row loss. promotion_audit_log
-- is a HIPAA §164.312(b) chain-of-custody artifact for L1 rule
-- promotions; a missing row breaks "who promoted what when." Logging
-- to stderr is not recovery — log shipper has finite retention and
-- no replay mechanism.
--
-- This table is the recovery queue: when the savepoint catches the
-- failed INSERT, the except path writes the same payload here, on a
-- fresh asyncpg connection (so the outer txn poison doesn't carry).
-- A substrate invariant (sigauth-runbook style) fires whenever any
-- row is present so on-call reconciles and re-INSERTs into
-- promotion_audit_log via a recovery script.
--
-- INSERT-only by trigger. UPDATE/DELETE blocked. Mirrors the
-- HIPAA-7yr append-only retention posture. The whole point is that
-- it's a durable evidence trail for things that didn't make it to
-- the main audit log.

CREATE TABLE IF NOT EXISTS promotion_audit_log_recovery (
    id BIGSERIAL PRIMARY KEY,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Mirror promotion_audit_log columns so a recovery script can
    -- INSERT directly. JSONB blob would be looser but harder to
    -- audit-dump.
    event_type TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    pattern_signature TEXT,
    check_type TEXT,
    site_id TEXT,
    confidence_score DOUBLE PRECISION,
    success_rate DOUBLE PRECISION,
    l2_resolutions INTEGER,
    total_occurrences INTEGER,
    source TEXT,
    actor TEXT,
    metadata JSONB,
    -- Diagnostic context — what the INSERT raised in the original
    -- savepoint. Helps the operator triage.
    failure_reason TEXT,
    failure_class TEXT,  -- exception class name (e.g. CheckViolation)
    -- Recovery state
    recovered BOOLEAN NOT NULL DEFAULT FALSE,
    recovered_at TIMESTAMPTZ,
    recovered_by TEXT,
    -- Nullable shape-only FK pointer to promotion_audit_log.id once
    -- recovered. NOT a real FK constraint — promotion_audit_log is
    -- partitioned and the recovery script writes to whichever monthly
    -- partition is current; a hard FK would couple the recovery
    -- queue's lifecycle to partition maintenance and that's not the
    -- value tradeoff we want.
    recovery_audit_log_id BIGINT,
    -- Defense-in-depth length cap. Writer truncates to 500; the
    -- schema tolerates a 4x slop in case a future writer forgets.
    CONSTRAINT chk_palr_failure_reason_length CHECK (
        failure_reason IS NULL OR length(failure_reason) <= 2000
    )
);

CREATE INDEX IF NOT EXISTS idx_palr_unrecovered
    ON promotion_audit_log_recovery (queued_at DESC)
    WHERE recovered = FALSE;

CREATE INDEX IF NOT EXISTS idx_palr_rule_id
    ON promotion_audit_log_recovery (rule_id);

-- INSERT-only on the recovery table itself. UPDATE allowed only for
-- the recovery-flag flip (recovered=true + bookkeeping), never for
-- the audit payload. DELETE blocked outright.
--
-- LOCKSTEP: any new column added to this table MUST be added to
-- either the immutable-payload check below OR the explicit
-- mutable-recovery-state allowlist (recovered/recovered_at/
-- recovered_by/recovery_audit_log_id). A new column not in either
-- list is silently mutable and breaks the audit-immutability
-- guarantee. test_promotion_audit_log_recovery_trigger_lockstep
-- enforces this at CI time.
CREATE OR REPLACE FUNCTION enforce_promotion_audit_log_recovery_integrity()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'promotion_audit_log_recovery is INSERT-only — DELETE blocked'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        -- Only the recovery-state columns can change. The audit
        -- payload is immutable.
        IF NEW.event_type        IS DISTINCT FROM OLD.event_type
            OR NEW.rule_id            IS DISTINCT FROM OLD.rule_id
            OR NEW.pattern_signature  IS DISTINCT FROM OLD.pattern_signature
            OR NEW.check_type         IS DISTINCT FROM OLD.check_type
            OR NEW.site_id            IS DISTINCT FROM OLD.site_id
            OR NEW.confidence_score   IS DISTINCT FROM OLD.confidence_score
            OR NEW.success_rate       IS DISTINCT FROM OLD.success_rate
            OR NEW.l2_resolutions     IS DISTINCT FROM OLD.l2_resolutions
            OR NEW.total_occurrences  IS DISTINCT FROM OLD.total_occurrences
            OR NEW.source             IS DISTINCT FROM OLD.source
            OR NEW.actor              IS DISTINCT FROM OLD.actor
            OR NEW.metadata           IS DISTINCT FROM OLD.metadata
            OR NEW.failure_reason     IS DISTINCT FROM OLD.failure_reason
            OR NEW.failure_class      IS DISTINCT FROM OLD.failure_class
            OR NEW.queued_at          IS DISTINCT FROM OLD.queued_at
        THEN
            RAISE EXCEPTION 'promotion_audit_log_recovery: audit payload is immutable; only recovery-state columns may be UPDATEd'
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_palr_integrity ON promotion_audit_log_recovery;
CREATE TRIGGER trg_palr_integrity
    BEFORE UPDATE OR DELETE ON promotion_audit_log_recovery
    FOR EACH ROW EXECUTE FUNCTION enforce_promotion_audit_log_recovery_integrity();

COMMENT ON TABLE promotion_audit_log_recovery IS
    'Dead-letter queue for promotion_audit_log INSERTs that failed inside flywheel_promote.promote_candidate Step 7 savepoint. Substrate invariant promotion_audit_log_recovery_pending fires when any row.recovered = false. HIPAA §164.312(b) chain-of-custody durability requirement (Session 212 round-table P0).';
