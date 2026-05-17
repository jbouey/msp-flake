-- Migration 326 — rewrite v_control_status to use site_appliances JOIN + check_result
--
-- #122 multi-device P2-3 Phase 1 closure (audit/coach-122-compliance-
-- bundles-appliance-id-deprecation-gate-a-2026-05-16.md, P0-1 binding).
--
-- Root cause: v_control_status (mig 138) reads `cb.appliance_id` +
-- `cb.outcome` — both of which are NULL on every row (verified live
-- mig 268 root-cause analysis). The view always returns ZERO rows
-- under any WHERE filter. frameworks.py:319 reads from this view —
-- so that endpoint silently always returns empty even when bundle
-- data exists.
--
-- Fix: mirror mig 268's resolution pattern. Bind compliance_bundles
-- to per-appliance via site_appliances JOIN on site_id. Use
-- check_result (which IS populated by evidence_chain.py:1137-1142)
-- instead of `outcome`.
--
-- COLUMN RENAME (intentional): mig 138 view exposed `outcome`. Mig
-- 326 view exposes `status`. Callers were already aliasing
-- `outcome as status` (frameworks.py:322) — same commit updates
-- the caller to SELECT `status` directly. CI test
-- `test_no_compliance_bundles_appliance_id_writes.py` enforces the
-- deprecation surface.
--
-- Multi-appliance binding semantics: in multi-appliance sites the
-- view fan-outs per-appliance for the same bundle (one bundle row
-- → N appliance rows, one per active site_appliances row). This
-- matches the existing operator expectation that "each appliance
-- has its own compliance posture" — the bundle wrote one record,
-- but the question "is appliance X compliant under framework F"
-- naturally returns the latest matching bundle scoped to X's site.
--
-- Performance: ROW_NUMBER() partitions by (sa.appliance_id,
-- framework, control_id). 30-day window prunes partitioned
-- compliance_bundles via cb.created_at predicate. Inner JOIN to
-- site_appliances drops deleted_at IS NOT NULL appliances.
--
-- Idempotency: CREATE OR REPLACE VIEW is re-runnable.

BEGIN;

CREATE OR REPLACE VIEW v_control_status AS
WITH latest_evidence AS (
    SELECT
        sa.appliance_id,
        efm.framework,
        efm.control_id,
        cb.check_result AS status,
        cb.created_at,
        ROW_NUMBER() OVER (
            PARTITION BY sa.appliance_id, efm.framework, efm.control_id
            ORDER BY cb.created_at DESC
        ) AS rn
    FROM compliance_bundles cb
    JOIN site_appliances sa
      ON sa.site_id = cb.site_id
     AND sa.deleted_at IS NULL
    JOIN evidence_framework_mappings efm
      ON cb.bundle_id = efm.bundle_id
    WHERE cb.created_at >= NOW() - INTERVAL '30 days'
)
SELECT
    appliance_id,
    framework,
    control_id,
    status,
    created_at AS last_checked
FROM latest_evidence
WHERE rn = 1;

COMMENT ON VIEW v_control_status IS
    'Per-appliance latest-evidence-per-control over 30d. Rewritten '
    'mig 326 (#122 Phase 1) to bind via site_appliances JOIN + use '
    'cb.check_result (the canonical writer column per mig 268). '
    'PRE-mig-326 the view read cb.appliance_id + cb.outcome which '
    'were NULL on every row.';

COMMIT;
