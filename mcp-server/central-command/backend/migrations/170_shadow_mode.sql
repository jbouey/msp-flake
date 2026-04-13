-- Migration 170: Shadow mode for candidate promotions (Phase 9)
--
-- Before a candidate auto-promotes, we "shadow-evaluate" it against the
-- last N incidents with the same incident_type: simulate whether the
-- candidate rule's incident_pattern would have matched, and compare
-- that prediction against the actual resolution (L1 hit / L2 LLM call /
-- L3 escalation). If shadow agreement ≥ 90%, promote. If < 90%, hold
-- the candidate back and log the mismatch — the pre-promotion estimator
-- is over-confident.
--
-- This catches the class of failure where pattern_stats shows "this
-- runbook has 786 successes for incident_type=firewall" but the actual
-- trigger conditions the candidate rule would use wouldn't ACTUALLY
-- fire on the real incident records. Protects against confidence
-- inflation from mis-labeled telemetry.
--
-- shadow_evaluations captures each evaluation's outcome so we can
-- audit why a candidate was blocked. Feeds into Phase 11 partner UI.

BEGIN;

CREATE TABLE IF NOT EXISTS shadow_evaluations (
    id                 BIGSERIAL    PRIMARY KEY,
    pattern_key        VARCHAR(255) NOT NULL,
    incident_type      VARCHAR(100) NOT NULL,
    runbook_id         VARCHAR(255) NOT NULL,
    -- Evaluation window (inclusive / exclusive as tstzrange)
    eval_window_start  TIMESTAMPTZ  NOT NULL,
    eval_window_end    TIMESTAMPTZ  NOT NULL,
    -- Counts from the evaluation
    incidents_considered INTEGER    NOT NULL,
    would_have_matched   INTEGER    NOT NULL,
    actually_resolved_l1 INTEGER    NOT NULL,
    -- Agreement = overlap of (would_match, actually_resolved_l1) divided
    -- by larger of the two. Range [0.0, 1.0].
    agreement_rate       NUMERIC(4,3) NOT NULL,
    -- Decision: 'promote' | 'hold' | 'insufficient_data'
    decision             VARCHAR(20)  NOT NULL,
    hold_reason          TEXT,
    evaluated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_shadow_eval_pattern
    ON shadow_evaluations (pattern_key, evaluated_at DESC);

CREATE INDEX IF NOT EXISTS idx_shadow_eval_recent
    ON shadow_evaluations (evaluated_at DESC);

-- shadow_mode_config: feature gate. Start with OFF per incident_type
-- until the mechanism is proven. Ops enables via this table, not code.
CREATE TABLE IF NOT EXISTS shadow_mode_config (
    incident_type      VARCHAR(100) PRIMARY KEY,
    enabled            BOOLEAN     NOT NULL DEFAULT false,
    min_agreement_rate NUMERIC(4,3) NOT NULL DEFAULT 0.90,
    min_sample_size    INTEGER     NOT NULL DEFAULT 10,
    eval_window_days   INTEGER     NOT NULL DEFAULT 14,
    notes              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Default OFF so existing auto-promote path is unaffected until ops
-- explicitly enables shadow for a category.
INSERT INTO shadow_mode_config (incident_type, enabled, notes)
VALUES ('__default__', false, 'Shadow mode disabled by default; enable per incident_type to activate')
ON CONFLICT (incident_type) DO NOTHING;

COMMIT;
