-- Migration 324: site_appliances.bearer_revoked
--
-- Task #62 v2.1 Commit 3 (2026-05-16). Adds a bearer-revocation flag
-- to support the load harness's k6 wrapper teardown: every run gets
-- a fresh synthetic bearer, and the run's /complete endpoint flips
-- bearer_revoked=TRUE so the bearer can't be replayed.
--
-- Spec: `.agent/plans/40-load-testing-harness-design-v2.1-2026-05-16.md` §P1-5
-- Gate A: `audit/coach-62-load-harness-v1-gate-a-2026-05-16.md`
--
-- Design notes:
--   - Single boolean column, NOT NULL DEFAULT FALSE. No data
--     migration required for existing rows — every existing
--     appliance bearer is considered valid until explicitly revoked.
--   - The check is consumed by `require_appliance_bearer` in
--     `auth_appliance.py` (Commit 3): when bearer_revoked=TRUE,
--     return 401 with detail='bearer_revoked'. k6's synthetic
--     bearers MUST set this on completion; real appliances retain
--     bearer_revoked=FALSE.
--   - Index on (bearer_revoked, appliance_id) speeds the revocation
--     check during high-volume k6 traffic. Partial index is overkill;
--     the boolean is selectively-FALSE for the prod set.
--
-- NOT a privileged-chain operation: synthetic-load test bearers only.
-- Real-appliance bearer rotation has its own privileged-chain path
-- (signing_key_rotation, already registered in mig 305 etc.).

BEGIN;

ALTER TABLE site_appliances
    ADD COLUMN IF NOT EXISTS bearer_revoked BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN site_appliances.bearer_revoked IS
    'Load-harness teardown flag (Task #62 v2.1 mig 324). When TRUE, '
    'require_appliance_bearer rejects the bearer with 401. Used by '
    'k6 wrapper to invalidate synthetic load-test bearers on run '
    'completion. Real appliance bearer rotation goes through the '
    'signing_key_rotation privileged-chain path, not this flag.';

CREATE INDEX IF NOT EXISTS idx_site_appliances_bearer_revoked_appliance
    ON site_appliances (appliance_id)
    WHERE bearer_revoked = TRUE;

COMMIT;
