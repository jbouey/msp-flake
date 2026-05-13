-- Migration 314: canonical_metric_samples (Task #50 Phase 2a)
--
-- Counsel Rule 1 (canonical-source registry, gold authority 2026-05-13):
-- runtime drift detector — samples customer-facing endpoint responses
-- + invariant verifies they match canonical-helper output. Pairs with
-- the static AST gate (Phase 0+1, tests/test_canonical_metrics_registry.py)
-- which catches non-canonical-delegation drift at compile time.
--
-- Design v3 + Gate A v4 APPROVE:
--   audit/canonical-metric-drift-invariant-design-2026-05-13.md
--   audit/coach-canonical-compliance-score-drift-v3-patched-gate-a-2026-05-13.md
--
-- Migration: ledger removal of mig 314 reservation lands in the SAME
-- commit per RESERVED_MIGRATIONS lifecycle rule.

BEGIN;

CREATE TABLE IF NOT EXISTS canonical_metric_samples (
    sample_id       UUID NOT NULL DEFAULT gen_random_uuid(),
    metric_class    TEXT NOT NULL,
    tenant_id       UUID NOT NULL,
    site_id         TEXT NULL,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    captured_value  NUMERIC(5,1) NULL,
    endpoint_path   TEXT NOT NULL,
    helper_input    JSONB NULL,
    classification  TEXT NOT NULL,
    CONSTRAINT canonical_metric_samples_classification_valid CHECK (
        classification IN ('customer-facing', 'operator-internal', 'partner-internal')
    ),
    PRIMARY KEY (sample_id, captured_at)
) PARTITION BY RANGE (captured_at);

-- Initial 3-month partition coverage. Phase 2d pruner (separate task)
-- adds DETACH-then-DROP for partitions older than 30 days + creates
-- new monthly partitions ahead of cliff.
CREATE TABLE IF NOT EXISTS canonical_metric_samples_2026_05
    PARTITION OF canonical_metric_samples
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

CREATE TABLE IF NOT EXISTS canonical_metric_samples_2026_06
    PARTITION OF canonical_metric_samples
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE IF NOT EXISTS canonical_metric_samples_2026_07
    PARTITION OF canonical_metric_samples
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

-- Tenant + recency lookup index (general purpose).
CREATE INDEX IF NOT EXISTS idx_canonical_metric_samples_tenant
    ON canonical_metric_samples (tenant_id, metric_class, captured_at DESC);

-- Partial index — substrate-invariant query fires only on customer-facing
-- rows; partial-index physically excludes operator-internal samples from
-- scan. Combined with the WHERE classification = 'customer-facing'
-- in the invariant SQL + the CHECK constraint above, this is 3-layer
-- defense-in-depth against operator-internal samples leaking to
-- customer-facing drift alerts.
CREATE INDEX IF NOT EXISTS idx_canonical_metric_samples_drift
    ON canonical_metric_samples (metric_class, classification, captured_at DESC)
    WHERE classification = 'customer-facing';

-- Audit trail.
INSERT INTO admin_audit_log (
    user_id, username, action, target, details, ip_address
) VALUES (
    NULL,
    'system',
    'canonical_metric_samples_table_created',
    'canonical_metric_samples',
    jsonb_build_object(
        'migration', '314_canonical_metric_samples',
        'task', '#50',
        'counsel_rule', 'Rule 1 — canonical-source registry runtime half',
        'design_doc', 'audit/canonical-metric-drift-invariant-design-2026-05-13.md',
        'gate_a_verdict', 'audit/coach-canonical-compliance-score-drift-v3-patched-gate-a-2026-05-13.md'
    ),
    NULL
);

COMMIT;
