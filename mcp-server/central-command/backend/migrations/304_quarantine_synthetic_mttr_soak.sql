-- Migration 304: quarantine the substrate-MTTR-soak synthetic data
-- per Phase 4 coach review (audit/coach-phase4-mttr-soak-review-
-- 2026-05-11.md) BLOCK verdict.
--
-- The fork review identified 6 P0 findings — chiefly that
-- mig 303's synthetic site (status='online') was visible to
-- /api/fleet admin enumeration, /admin/metrics trending,
-- recurrence_velocity_loop, and federation tier-org candidates.
-- This migration:
--
--   1. Flips synthetic site status to 'inactive' so the existing
--      `WHERE status != 'inactive'` filters in routes.py + admin
--      surfaces already exclude it. No code changes required to
--      stop the bleed.
--   2. Keeps the row + the substrate_mttr_soak_runs table for
--      Phase 4 v2 to redesign against (re-running mig 303 is
--      idempotent via ON CONFLICT, so the row stays for v2).
--   3. Drops the synthetic appliance row outright since no v1
--      runs ever happened.
--   4. Logs the quarantine action in admin_audit_log with the
--      review reference so future operators see the why.
--
-- Phase 4 v2 will need to address ALL 6 P0 findings before
-- creating any incidents in the synthetic site. The synthetic
-- site is now QUARANTINED but PRESERVED — operators can verify
-- the synthetic data still exists for v2 redesign work, but
-- production admin surfaces no longer leak it.

BEGIN;

-- 1. Quarantine: flip status to 'inactive'. Existing filters
-- `WHERE status != 'inactive'` in routes.py:151 + others auto-exclude.
UPDATE sites
   SET status = 'inactive',
       updated_at = NOW()
 WHERE site_id = 'synthetic-mttr-soak';

-- 2. Drop the synthetic appliance row. The bogus MAC
-- '00:00:00:00:00:00' (P2-3 finding) could collide with future
-- synthetic-test rows.
DELETE FROM site_appliances
 WHERE appliance_id = 'synthetic-mttr-soak-appliance';

-- 3. Audit-log the quarantine. Uses a real operator email
-- (P2-2 finding addressed) for the named-actor rule.
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'jbouey2006@gmail.com',
    'phase4_soak_quarantine',
    'sites:synthetic-mttr-soak',
    jsonb_build_object(
        'migration', '304_quarantine_synthetic_mttr_soak',
        'session', 'Session 219 Phase 4 fork review BLOCK',
        'review_doc', 'audit/coach-phase4-mttr-soak-review-2026-05-11.md',
        'p0_findings', 6,
        'action', 'site status → inactive, synthetic appliance dropped',
        'preserved', 'sites row + client_orgs row + substrate_mttr_soak_runs table for v2 redesign'
    ),
    NOW()
);

COMMIT;
