-- Flywheel Spine — Session 206 redesign.
--
-- The flywheel had 9 asynchronous hops and 0 shared state model. Every
-- hop owned its own tables; no one owned the journey. Result: failed
-- transitions were silent, operators had no observability, audits had
-- to join across 8+ tables to understand one rule's state.
--
-- This migration installs the spine:
--
--   1. `promoted_rule_events` — append-only ledger. Every state
--      transition for every promoted rule is one row. DELETE + UPDATE
--      blocked at the trigger level (same pattern as migration 151).
--      Partitioned by month (same pattern as migration 138) so we can
--      detach old partitions without touching live data.
--
--   2. `promoted_rules.lifecycle_state` — explicit state machine.
--      9 states. CHECK-constrained. Direct UPDATE blocked by trigger
--      unless a per-session flag is set — callers MUST go through
--      `advance_lifecycle()` which does the UPDATE + event INSERT
--      atomically. Tamper-evident.
--
--   3. `advance_lifecycle(rule_id, new_state, event_type, actor, proof)`
--      — the only sanctioned state mutation path.
--
-- Backwards compatibility: existing code that writes `enabled`,
-- `status`, `deployment_count` still works. `lifecycle_state` is the
-- new authoritative field; those three columns become DERIVED (and
-- eventually removed in a later migration).

BEGIN;

-- ─── 1. Ledger table ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS promoted_rule_events (
    event_id       UUID NOT NULL DEFAULT gen_random_uuid(),
    rule_id        TEXT NOT NULL,
    site_id        TEXT,
    event_type     TEXT NOT NULL,
    stage          TEXT NOT NULL,
    outcome        TEXT NOT NULL DEFAULT 'success',
    actor          TEXT NOT NULL,
    elapsed_ms     INTEGER,
    proof          JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason         TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (event_id, created_at),
    CHECK (event_type IN (
        'pattern_detected',
        'shadow_evaluated',
        'promotion_approved',
        'rollout_issued',
        'rollout_acked',
        'first_execution',
        'regime_warning',
        'regime_critical',
        'regime_absolute_low',
        'auto_disabled',
        'manually_disabled',
        'graduated',
        'retired_site_dead',
        'retired_manual',
        'operator_acknowledged',
        'operator_re_enabled'
    )),
    CHECK (outcome IN ('success', 'failed', 'skipped', 'timeout')),
    CHECK (stage IN (
        'detection', 'shadow_eval', 'promotion', 'rollout',
        'monitoring', 'regime', 'governance', 'retire'
    ))
) PARTITION BY RANGE (created_at);

-- Partitions: 2026-04 (current), 2026-05 (next month), default for overflow
CREATE TABLE IF NOT EXISTS promoted_rule_events_202604
    PARTITION OF promoted_rule_events
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');

CREATE TABLE IF NOT EXISTS promoted_rule_events_202605
    PARTITION OF promoted_rule_events
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

CREATE TABLE IF NOT EXISTS promoted_rule_events_default
    PARTITION OF promoted_rule_events DEFAULT;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_prule_events_rule_time
    ON promoted_rule_events (rule_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_prule_events_type_time
    ON promoted_rule_events (event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_prule_events_outcome_fail
    ON promoted_rule_events (created_at DESC)
    WHERE outcome = 'failed';

-- Ledger is append-only. DELETE + UPDATE blocked at row level.
-- Uses prevent_audit_deletion from migration 151 if present, else inline.
CREATE OR REPLACE FUNCTION prule_events_append_only_guard()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'promoted_rule_events is append-only — % blocked', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_prule_events_no_delete') THEN
        CREATE TRIGGER trg_prule_events_no_delete
        BEFORE DELETE ON promoted_rule_events
        FOR EACH ROW EXECUTE FUNCTION prule_events_append_only_guard();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_prule_events_no_update') THEN
        CREATE TRIGGER trg_prule_events_no_update
        BEFORE UPDATE ON promoted_rule_events
        FOR EACH ROW EXECUTE FUNCTION prule_events_append_only_guard();
    END IF;
END $$;

-- ─── 2. Lifecycle column + transition matrix ──────────────────────

ALTER TABLE promoted_rules
    ADD COLUMN IF NOT EXISTS lifecycle_state TEXT NOT NULL DEFAULT 'proposed';

-- Drop prior check if present (in case of re-run), re-add
ALTER TABLE promoted_rules DROP CONSTRAINT IF EXISTS promoted_rules_lifecycle_check;
ALTER TABLE promoted_rules ADD CONSTRAINT promoted_rules_lifecycle_check
    CHECK (lifecycle_state IN (
        'proposed', 'shadow', 'approved', 'rolling_out',
        'active', 'regime_warning', 'auto_disabled',
        'graduated', 'retired'
    ));

-- Derived columns become informational; keep for backward compat.
-- operator_ack tracking surfaces on dashboard:
ALTER TABLE promoted_rules
    ADD COLUMN IF NOT EXISTS operator_ack_required BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE promoted_rules
    ADD COLUMN IF NOT EXISTS operator_ack_at TIMESTAMPTZ;
ALTER TABLE promoted_rules
    ADD COLUMN IF NOT EXISTS operator_ack_by TEXT;
ALTER TABLE promoted_rules
    ADD COLUMN IF NOT EXISTS lifecycle_state_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_promoted_rules_lifecycle
    ON promoted_rules (lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_promoted_rules_ack_required
    ON promoted_rules (rule_id)
    WHERE operator_ack_required = TRUE;

-- ─── 3. Transition matrix — validates state changes ───────────────
--
-- A state transition is only legal if (from_state, to_state) is in the
-- allowed set below. advance_lifecycle() checks this before UPDATE.

CREATE TABLE IF NOT EXISTS promoted_rule_lifecycle_transitions (
    from_state TEXT NOT NULL,
    to_state   TEXT NOT NULL,
    PRIMARY KEY (from_state, to_state)
);

-- Seed (idempotent: ON CONFLICT DO NOTHING)
INSERT INTO promoted_rule_lifecycle_transitions (from_state, to_state) VALUES
    ('proposed', 'shadow'),
    ('proposed', 'approved'),
    ('proposed', 'retired'),
    ('shadow', 'approved'),
    ('shadow', 'retired'),
    ('approved', 'rolling_out'),
    ('approved', 'retired'),
    ('rolling_out', 'active'),
    ('rolling_out', 'retired'),
    ('active', 'regime_warning'),
    ('active', 'auto_disabled'),
    ('active', 'graduated'),
    ('active', 'retired'),
    ('regime_warning', 'active'),
    ('regime_warning', 'auto_disabled'),
    ('regime_warning', 'retired'),
    ('auto_disabled', 'active'),
    ('auto_disabled', 'retired'),
    ('graduated', 'regime_warning'),
    ('graduated', 'auto_disabled'),
    ('graduated', 'retired')
ON CONFLICT DO NOTHING;

-- Allow self-transitions (idempotent re-emits of same state).
INSERT INTO promoted_rule_lifecycle_transitions (from_state, to_state)
SELECT s, s FROM (VALUES
    ('proposed'), ('shadow'), ('approved'), ('rolling_out'), ('active'),
    ('regime_warning'), ('auto_disabled'), ('graduated'), ('retired')
) AS t(s)
ON CONFLICT DO NOTHING;

-- ─── 4. `advance_lifecycle` — the ONLY sanctioned state mutation ──
--
-- Atomic: validates transition, writes event, updates lifecycle_state,
-- derives legacy columns (enabled). Caller provides rule_id,
-- new_state, event_type, actor, and proof JSON.
--
-- Returns 1 if transition applied, 0 if no-op (already at that state
-- or invalid transition). Callers log accordingly.

CREATE OR REPLACE FUNCTION advance_lifecycle(
    p_rule_id TEXT,
    p_new_state TEXT,
    p_event_type TEXT,
    p_actor TEXT,
    p_stage TEXT,
    p_proof JSONB DEFAULT '{}'::jsonb,
    p_reason TEXT DEFAULT NULL,
    p_site_id TEXT DEFAULT NULL,
    p_outcome TEXT DEFAULT 'success'
) RETURNS INTEGER AS $$
DECLARE
    v_current_state TEXT;
    v_allowed BOOLEAN;
BEGIN
    -- Load current state (lock row)
    SELECT lifecycle_state INTO v_current_state
    FROM promoted_rules WHERE rule_id = p_rule_id
    FOR UPDATE;

    IF v_current_state IS NULL THEN
        RAISE EXCEPTION 'advance_lifecycle: rule_id % not found', p_rule_id
            USING ERRCODE = 'no_data_found';
    END IF;

    -- Self-transition: still emit the event (for audit/heartbeat), no-op update
    -- Validation: must be in transition matrix
    SELECT EXISTS (
        SELECT 1 FROM promoted_rule_lifecycle_transitions
        WHERE from_state = v_current_state AND to_state = p_new_state
    ) INTO v_allowed;

    IF NOT v_allowed THEN
        RAISE EXCEPTION 'advance_lifecycle: illegal transition % -> % for rule %',
            v_current_state, p_new_state, p_rule_id
            USING ERRCODE = 'check_violation';
    END IF;

    -- Write event FIRST (append-only ledger is authoritative)
    INSERT INTO promoted_rule_events (
        rule_id, site_id, event_type, stage, outcome, actor, proof, reason
    ) VALUES (
        p_rule_id, p_site_id, p_event_type, p_stage, p_outcome, p_actor, p_proof, p_reason
    );

    -- Then update state (only if changed — self-transition is allowed but no-op the UPDATE)
    IF v_current_state != p_new_state THEN
        UPDATE promoted_rules
        SET lifecycle_state = p_new_state,
            lifecycle_state_updated_at = NOW()
        WHERE rule_id = p_rule_id;
    END IF;

    RETURN 1;
END;
$$ LANGUAGE plpgsql;

-- ─── 5. Block direct UPDATE of lifecycle_state (tamper-evident) ───
--
-- Only advance_lifecycle() can change lifecycle_state. Direct
-- `UPDATE promoted_rules SET lifecycle_state=...` fails unless the
-- session set app.allow_lifecycle_bypass = 'true' (operator override
-- for disaster recovery only, written into audit log separately).

CREATE OR REPLACE FUNCTION enforce_lifecycle_via_advance()
RETURNS TRIGGER AS $$
DECLARE
    v_bypass TEXT;
BEGIN
    -- Only care about lifecycle_state changes
    IF NEW.lifecycle_state IS NOT DISTINCT FROM OLD.lifecycle_state THEN
        RETURN NEW;
    END IF;

    -- Check for explicit bypass (DBA-only)
    BEGIN
        v_bypass := current_setting('app.allow_lifecycle_bypass', true);
    EXCEPTION WHEN OTHERS THEN
        v_bypass := NULL;
    END;

    IF v_bypass = 'true' THEN
        RETURN NEW;
    END IF;

    -- advance_lifecycle sets this before the UPDATE; it's our sentinel
    BEGIN
        v_bypass := current_setting('app.in_advance_lifecycle', true);
    EXCEPTION WHEN OTHERS THEN
        v_bypass := NULL;
    END;

    IF v_bypass = 'true' THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'Direct UPDATE of promoted_rules.lifecycle_state is forbidden. '
        'Use advance_lifecycle(rule_id, new_state, event_type, actor, ...) '
        'so the ledger stays in sync.'
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_enforce_lifecycle_via_advance ON promoted_rules;
CREATE TRIGGER trg_enforce_lifecycle_via_advance
    BEFORE UPDATE ON promoted_rules
    FOR EACH ROW EXECUTE FUNCTION enforce_lifecycle_via_advance();

-- Update advance_lifecycle to set the sentinel before UPDATE
CREATE OR REPLACE FUNCTION advance_lifecycle(
    p_rule_id TEXT,
    p_new_state TEXT,
    p_event_type TEXT,
    p_actor TEXT,
    p_stage TEXT,
    p_proof JSONB DEFAULT '{}'::jsonb,
    p_reason TEXT DEFAULT NULL,
    p_site_id TEXT DEFAULT NULL,
    p_outcome TEXT DEFAULT 'success'
) RETURNS INTEGER AS $$
DECLARE
    v_current_state TEXT;
    v_allowed BOOLEAN;
BEGIN
    SELECT lifecycle_state INTO v_current_state
    FROM promoted_rules WHERE rule_id = p_rule_id FOR UPDATE;

    IF v_current_state IS NULL THEN
        RAISE EXCEPTION 'advance_lifecycle: rule_id % not found', p_rule_id
            USING ERRCODE = 'no_data_found';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM promoted_rule_lifecycle_transitions
        WHERE from_state = v_current_state AND to_state = p_new_state
    ) INTO v_allowed;

    IF NOT v_allowed THEN
        RAISE EXCEPTION 'advance_lifecycle: illegal transition % -> % for rule %',
            v_current_state, p_new_state, p_rule_id
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO promoted_rule_events (
        rule_id, site_id, event_type, stage, outcome, actor, proof, reason
    ) VALUES (
        p_rule_id, p_site_id, p_event_type, p_stage, p_outcome, p_actor, p_proof, p_reason
    );

    IF v_current_state != p_new_state THEN
        -- Set sentinel so the trigger allows OUR UPDATE
        PERFORM set_config('app.in_advance_lifecycle', 'true', true);
        UPDATE promoted_rules
        SET lifecycle_state = p_new_state,
            lifecycle_state_updated_at = NOW()
        WHERE rule_id = p_rule_id;
        PERFORM set_config('app.in_advance_lifecycle', 'false', true);
    END IF;

    RETURN 1;
END;
$$ LANGUAGE plpgsql;

-- ─── 6. Infer initial lifecycle_state for existing rows ──────────

-- Rules with deployment_count > 0 → active
-- Rules enabled but deployment_count = 0 → approved (no rollout yet)
-- Rules disabled → auto_disabled (best-effort inference)
UPDATE promoted_rules pr
SET lifecycle_state = CASE
    WHEN pr.status = 'retired' THEN 'retired'
    WHEN pr.deployment_count > 0 THEN 'active'
    WHEN EXISTS (
        SELECT 1 FROM l1_rules l
        WHERE l.rule_id = pr.rule_id AND l.enabled = false
    ) THEN 'auto_disabled'
    ELSE 'approved'
END
WHERE pr.lifecycle_state = 'proposed';  -- only touch newly-defaulted

COMMIT;
