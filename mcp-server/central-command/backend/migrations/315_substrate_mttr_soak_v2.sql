-- Migration 315: substrate MTTR soak v2 — Phase A schema
--
-- Task #61 / Phase 4 substrate soak v2. Replaces v1 (mig 303 + 304) which
-- BLOCKED at Gate A 2026-05-11 — v1's incidents-shaped seed was invisible
-- to every substrate invariant. v2 inverts the design: directly seed the
-- SHAPE that `_check_l2_resolution_without_decision_record` already
-- queries against, then measure wall-clock from seed → detected_at →
-- resolved_at.
--
-- Design v3 + Gate A v3 APPROVE (after v1 BLOCK on Phase 4 + v2
-- APPROVE-WITH-FIXES on 3 P0s + v3 APPROVE):
--   audit/substrate-mttr-soak-v2-design-2026-05-13.md
--   audit/coach-substrate-mttr-soak-v3-gate-a-2026-05-13.md
--
-- v3 P0 fixes embedded:
--   1. mig number renumbered 311 → 315 (collision with Vault P0 #43)
--   2. status='active' flip REMOVED — injector owns runtime flip gated
--      on CI green for the synthetic=FALSE filter code rollout
--   3. compliance_bundles NOT VALID CHECK constraint added — Counsel
--      Rule 2 schema-level write-side guard

BEGIN;

-- 1. Synthetic flag on sites table.
ALTER TABLE sites ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_sites_synthetic
    ON sites (synthetic) WHERE synthetic = TRUE;

-- 2. Mark the v1-quarantined synthetic site as synthetic=TRUE but LEAVE
--    status='inactive' (mig 304 quarantine preserved). Per v3 P0-CROSS-2,
--    the injector itself flips status to 'active' at startup, gated on
--    the synthetic=FALSE filter code being live in prod (deploy-verified).
--    Closes the backend-deploy-lag-induced contamination window.
UPDATE sites
   SET synthetic = TRUE,
       updated_at = NOW()
 WHERE site_id = 'synthetic-mttr-soak';

-- 2b. Counsel Rule 2 schema-level write-side guard. NOT VALID defers the
--     table-scan cost (zero existing synthetic-prefixed rows by the v2
--     isolation contract) and enforces only on NEW writes. Note: ADD
--     CONSTRAINT is NOT idempotent in PG; schema_migrations table
--     prevents replay.
ALTER TABLE compliance_bundles
    ADD CONSTRAINT no_synthetic_bundles
    CHECK (site_id NOT LIKE 'synthetic-%') NOT VALID;

-- 3. Seed-tracking table — analyzer joins this against substrate_violations
--    to compute detect/resolve latency. NOT incidents — incidents is the
--    SHAPE we inject; this table is the round-trip audit.
CREATE TABLE IF NOT EXISTS substrate_synthetic_seeds (
    seed_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    soak_run_id    UUID NOT NULL,
    invariant_name TEXT NOT NULL,
    site_id        TEXT NOT NULL REFERENCES sites(site_id),
    incident_id    UUID,
    severity_label TEXT NOT NULL,
    seeded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    removed_at     TIMESTAMPTZ,
    detected_at    TIMESTAMPTZ,
    resolved_at    TIMESTAMPTZ,
    CONSTRAINT synthetic_seeds_site_synthetic CHECK (site_id LIKE 'synthetic-%')
);
CREATE INDEX IF NOT EXISTS idx_synthetic_seeds_run
    ON substrate_synthetic_seeds (soak_run_id, seeded_at);

-- 4. Runs table v2 (v1's substrate_mttr_soak_runs preserved for
--    archeology; tied to v1's incidents-shape measurement model).
CREATE TABLE IF NOT EXISTS substrate_mttr_soak_runs_v2 (
    soak_run_id    UUID PRIMARY KEY,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at       TIMESTAMPTZ,
    config         JSONB NOT NULL,
    status         TEXT NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running','completed','aborted','quarantined')),
    detect_p50_seconds   NUMERIC,
    detect_p95_seconds   NUMERIC,
    detect_p99_seconds   NUMERIC,
    resolve_p50_seconds  NUMERIC,
    resolve_p95_seconds  NUMERIC,
    resolve_p99_seconds  NUMERIC,
    summary        JSONB
);

-- 5. Audit-log row.
INSERT INTO admin_audit_log (
    user_id, username, action, target, details, ip_address
) VALUES (
    NULL,
    'jbouey2006@gmail.com',
    'substrate_mttr_soak_v2_install',
    'mig:315',
    jsonb_build_object(
        'migration', '315_substrate_mttr_soak_v2',
        'task', '#61',
        'supersedes', '303_substrate_mttr_soak + 304_quarantine',
        'counsel_rules_addressed', jsonb_build_array('Rule 2 — PHI/customer-data boundary'),
        'design_doc', 'audit/substrate-mttr-soak-v2-design-2026-05-13.md',
        'gate_a_verdict', 'audit/coach-substrate-mttr-soak-v3-gate-a-2026-05-13.md'
    ),
    NULL
);

COMMIT;
