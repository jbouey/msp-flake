-- Migration 303: substrate-MTTR soak infrastructure (Phase 4, Session 219+).
--
-- Adds:
--   1. `synthetic-mttr-soak` site row (status=active, no real appliances)
--   2. `substrate_mttr_soak_runs` table — one row per soak invocation
--   3. partial index on `incidents.details->>'soak_test'` for fast
--      filter on per-incident tracking queries
--
-- The synthetic site is intentionally NOT mapped to any
-- client_org_id so:
--   * compliance_bundles will not be generated for it (the bundle
--     generator filters by client_org_id IS NOT NULL)
--   * org_connection RLS auto-isolates it (no policy matches)
--   * auditor-kit walks never enumerate it
--
-- See .agent/plans/24-substrate-mttr-soak-2026-05-11.md for design.

BEGIN;

-- 0. Synthetic client_org for the soak site (sites.client_org_id is
-- NOT NULL). Deterministic UUID via fixed string so the row is
-- idempotently re-creatable. This org is NEVER mapped to a
-- compliance_bundles row (synthetic site has no real evidence) and
-- NEVER appears in auditor-kit output (filter on
-- `client_orgs.name LIKE '%synthetic%'` is added below to scoring
-- queries in the next CI sweep).
INSERT INTO client_orgs (
    id, name, primary_email, status, created_at, updated_at
)
VALUES (
    '00000000-0000-4000-8000-00000000ff04'::uuid,  -- well-known soak org UUID
    'SYNTHETIC-mttr-soak (substrate validation, NOT a real customer)',
    'soak-test@example.invalid',
    'active',
    NOW(),
    NOW()
)
ON CONFLICT (id) DO UPDATE SET
    name        = EXCLUDED.name,
    updated_at  = NOW();

-- 1. Synthetic site for soak. INSERT … ON CONFLICT to keep idempotent.
INSERT INTO sites (
    site_id, clinic_name, tier, industry,
    status, client_org_id, created_at, updated_at
)
VALUES (
    'synthetic-mttr-soak',
    'MTTR Soak Synthetic',
    'small',  -- sites_tier_check accepts: small|mid|large
    'synthetic',
    'online',  -- sites_status_check accepts: pending|online|offline|inactive
    '00000000-0000-4000-8000-00000000ff04'::uuid,
    NOW(),
    NOW()
)
ON CONFLICT (site_id) DO UPDATE SET
    clinic_name = EXCLUDED.clinic_name,
    client_org_id = EXCLUDED.client_org_id,
    updated_at  = NOW();

-- 2. Soak-run ledger.
CREATE TABLE IF NOT EXISTS substrate_mttr_soak_runs (
    soak_run_id     UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    config          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    status          TEXT         NOT NULL DEFAULT 'running'
                                 CHECK (status IN ('running','completed','aborted')),
    summary         JSONB,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_substrate_mttr_soak_runs_status
    ON substrate_mttr_soak_runs(status, started_at DESC);

-- 3. Fast-filter on soak_test marker. Partial index — only rows
-- carrying the marker are indexed, so production query plans are
-- unchanged.
CREATE INDEX IF NOT EXISTS idx_incidents_soak_test
    ON incidents((details->>'soak_test'))
    WHERE details->>'soak_test' = 'true';

-- 4. Audit-log row so future operators see this migration ran.
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'system:mig-303',
    'substrate_mttr_soak_setup',
    'sites',
    jsonb_build_object(
        'migration', '303_substrate_mttr_soak',
        'site_id', 'synthetic-mttr-soak',
        'session', 'Session 219 Phase 4 (2026-05-11)',
        'design_doc', '.agent/plans/24-substrate-mttr-soak-2026-05-11.md'
    ),
    NOW()
);

COMMIT;
