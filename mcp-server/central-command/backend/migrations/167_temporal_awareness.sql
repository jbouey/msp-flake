-- Migration 167: Temporal awareness — decay + regime-change detection
--
-- Phase 6 of the advanced flywheel. Two schema additions + supporting indices:
--
--   1. pattern_decay_config: per-incident_type half-life for evidence aging.
--      The flywheel forgets stale evidence exponentially — Windows 10 EoL
--      changes the success rate of every windows_update remediation, and
--      yesterday's 100% success on a deprecated runbook is misleading when
--      considered equally with last month's outcomes. Default half-life 90d.
--
--   2. l1_rule_regime_events: an append-only record of regime changes
--      detected on active L1 rules. Triggered when the 7-day rolling
--      success rate drops >15% vs the 30-day baseline. Does NOT auto-
--      disable — just flags for human review. Auto-disable remains the
--      existing 48h/<70% gate in background_tasks.py.
--
-- The decay itself runs in a nightly background task (add in same phase);
-- this migration only sets up the persistence layer.

BEGIN;

-- ─── pattern_decay_config ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pattern_decay_config (
    incident_type      VARCHAR(100) PRIMARY KEY,
    half_life_days     INTEGER      NOT NULL DEFAULT 90,
    decay_enabled      BOOLEAN      NOT NULL DEFAULT true,
    last_applied_at    TIMESTAMPTZ,
    -- Minimum raw count below which we don't decay further. Prevents the
    -- aggregation from disappearing entirely on very old patterns that
    -- might still be semantically relevant.
    min_count_floor    INTEGER      NOT NULL DEFAULT 5,
    notes              TEXT,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Seed a sensible default row so the decay job has something to read on
-- first run. Per-type overrides can be added by ops without code deploy.
INSERT INTO pattern_decay_config (incident_type, half_life_days, notes)
VALUES ('__default__', 90, 'Fleet-wide default half-life (Phase 6)')
ON CONFLICT (incident_type) DO NOTHING;

-- Shorter half-life for volatile categories — patches land every 30d,
-- so patching success-rate history older than 60d is usually misleading.
INSERT INTO pattern_decay_config (incident_type, half_life_days, notes) VALUES
    ('patching',        45, 'Patch cycles are ~30d; history >60d often stale'),
    ('windows_update',  45, 'Same rationale as patching'),
    ('linux_patching',  45, 'Same rationale as patching')
ON CONFLICT (incident_type) DO NOTHING;

-- ─── l1_rule_regime_events ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS l1_rule_regime_events (
    id                 BIGSERIAL    PRIMARY KEY,
    rule_id            VARCHAR(100) NOT NULL,
    detected_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    window_7d_rate     NUMERIC(4,3) NOT NULL,
    baseline_30d_rate  NUMERIC(4,3) NOT NULL,
    delta              NUMERIC(5,3) NOT NULL,
    sample_size_7d     INTEGER      NOT NULL,
    sample_size_30d    INTEGER      NOT NULL,
    severity           VARCHAR(20)  NOT NULL DEFAULT 'warning',
                -- warning = flag for review; critical = consider disable
    acknowledged_at    TIMESTAMPTZ,
    acknowledged_by    VARCHAR(100),
    resolution         VARCHAR(50)
                       -- 'false_positive' | 'true_regression' | 'auto_disabled' | 'still_investigating'
);

CREATE INDEX IF NOT EXISTS idx_regime_events_unacked
    ON l1_rule_regime_events (detected_at DESC)
    WHERE acknowledged_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_regime_events_rule
    ON l1_rule_regime_events (rule_id, detected_at DESC);

COMMIT;
