-- Migration 212: per-appliance signature_enforcement column
--
-- Week 5 of the composed identity stack.
-- Adds the toggle that gates whether a missing/invalid signature on
-- a checkin returns 401 (enforce) or just logs an observation
-- (observe).
--
-- Default = 'observe' for ALL existing rows. The auto-promotion
-- worker (Phase 5B) flips to 'enforce' only after N hours of
-- zero-fail signature observations per appliance — no daemon ever
-- gets locked out of the platform without first proving it can
-- sign correctly.
--
-- Manual override via /api/admin/sigauth/{promote,demote}/{appliance_id}.
-- Operators can hard-revert any individual appliance instantly.
-- Migration 192 row-guard is satisfied because the API does
-- per-appliance UPDATEs (one row at a time).

BEGIN;

ALTER TABLE site_appliances
    ADD COLUMN IF NOT EXISTS signature_enforcement TEXT
        NOT NULL DEFAULT 'observe'
        CHECK (signature_enforcement IN ('observe', 'enforce'));

COMMENT ON COLUMN site_appliances.signature_enforcement IS
    'Week 5: observe = log signatures, accept all bearer-authed checkins; '
    'enforce = require valid X-Appliance-Signature on every checkin '
    '(returns 401 otherwise). Default observe — auto-promotion only '
    'after sustained valid-signature evidence.';

-- Add a metadata column for promotion provenance (who/when/why
-- last toggled). Lightweight — populated by the API endpoints.
ALTER TABLE site_appliances
    ADD COLUMN IF NOT EXISTS signature_enforcement_changed_at TIMESTAMPTZ;
ALTER TABLE site_appliances
    ADD COLUMN IF NOT EXISTS signature_enforcement_changed_by VARCHAR(100);
ALTER TABLE site_appliances
    ADD COLUMN IF NOT EXISTS signature_enforcement_reason TEXT;

-- Fast scan for the auto-promotion worker.
CREATE INDEX IF NOT EXISTS site_appliances_sigauth_observe_idx
    ON site_appliances (signature_enforcement, last_checkin DESC)
    WHERE signature_enforcement = 'observe' AND deleted_at IS NULL;

COMMIT;

SELECT 'Migration 212 signature_enforcement_per_appliance complete' AS status;
