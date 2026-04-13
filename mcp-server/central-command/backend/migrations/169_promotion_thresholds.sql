-- Migration 169: Learned promotion thresholds (Phase 8)
--
-- Today the auto-promote loop uses hard-coded thresholds:
--   distinct_orgs       >= 5
--   success_rate        >= 0.90
--   total_occurrences   >= 20
--
-- These are reasonable global defaults but they ignore two realities:
--
--   1. Some incident types are safe to promote aggressively (e.g.
--      firewall_status — idempotent, reversible). Others have high
--      blast radius (e.g. bitlocker_status — disk-level). The same
--      confidence threshold is wrong for both.
--
--   2. After a rule promotes, we can MEASURE whether post-promotion
--      L1 performance matches our pre-promotion expectations. If we
--      promoted at success_rate=0.90 and the live L1 rule averages
--      0.85, the pre-promotion estimator was over-confident —
--      future promotions on this incident type should require a
--      HIGHER threshold. If the live rule averages 0.95, our
--      estimator was under-confident — we can lower the threshold
--      and promote more aggressively.
--
-- This migration sets up the persistence layer. A nightly job
-- (threshold_tuner_loop, added separately) computes post-promotion
-- observed vs threshold gap and Bayesian-updates the threshold.
-- Auto-tuning is OPT-IN per incident_type (auto_tune_enabled column)
-- so Security can gate high-risk categories to manual tuning only.

BEGIN;

CREATE TABLE IF NOT EXISTS promotion_thresholds (
    incident_type          VARCHAR(100) PRIMARY KEY,
    -- Current effective thresholds for this incident type. When any
    -- are NULL, fall back to the fleet-wide defaults from the
    -- platform_pattern_stats auto-promote query.
    min_success_rate       NUMERIC(4,3),
    min_total_occurrences  INTEGER,
    min_distinct_orgs      INTEGER,
    -- Tracking state used by the Bayesian updater
    last_observed_rate     NUMERIC(4,3),
    last_observed_n        INTEGER,
    last_tuned_at          TIMESTAMPTZ,
    tune_count             INTEGER     NOT NULL DEFAULT 0,
    auto_tune_enabled      BOOLEAN     NOT NULL DEFAULT false,
                                -- opt-in; Security gate on high-risk
                                -- categories such as bitlocker
    -- Safety bounds — the tuner will NEVER push thresholds outside
    -- these bounds regardless of observed performance.
    min_rate_floor         NUMERIC(4,3) NOT NULL DEFAULT 0.70,
    min_rate_ceiling       NUMERIC(4,3) NOT NULL DEFAULT 0.99,
    notes                  TEXT,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Seed a default row so lookup always finds something. The auto-promote
-- loop should query this table first, fall back to the hard-coded
-- defaults if no row matches.
INSERT INTO promotion_thresholds (
    incident_type, min_success_rate, min_total_occurrences, min_distinct_orgs,
    auto_tune_enabled, notes
)
VALUES (
    '__default__', 0.90, 20, 5, false,
    'Fleet-wide fallback; auto_tune off (manual tuning only)'
)
ON CONFLICT (incident_type) DO NOTHING;

-- Higher-risk categories get conservative defaults (high threshold,
-- auto-tune disabled).
INSERT INTO promotion_thresholds (
    incident_type, min_success_rate, min_total_occurrences, min_distinct_orgs,
    auto_tune_enabled, min_rate_floor, notes
) VALUES
    ('bitlocker_status', 0.95, 30, 5, false, 0.85,
     'Disk-level; high blast radius; manual tuning only'),
    ('bitlocker',        0.95, 30, 5, false, 0.85,
     'Disk-level; high blast radius; manual tuning only'),
    ('windows_update',   0.93, 25, 5, false, 0.80,
     'Reboots possible; conservative'),
    ('patching',         0.93, 25, 5, false, 0.80,
     'Reboots possible; conservative')
ON CONFLICT (incident_type) DO NOTHING;

COMMIT;
