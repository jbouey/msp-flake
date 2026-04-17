-- Migration 227: MFA enforcement default for partners + grace window
--
-- Partners are privileged actors: an MSP login controls the entire clinic
-- fleet under that partner_id. Before this migration, `partners.mfa_required`
-- defaulted to false and zero partners in production had it set. That left
-- the whole partner admin surface on passwords alone.
--
-- What this migration does:
--   1. Flips the column default to TRUE so every NEW partner row requires MFA.
--   2. Adds `mfa_grace_period_until` — a nullable timestamp. Existing partners
--      get a 14-day grace window; login is allowed without MFA during grace
--      but the UI nags them to enroll. After grace expires, the existing
--      `if mfa_required and not mfa_enabled` gate in partner_auth.py
--      returns 403 until they enroll.
--   3. Sets mfa_required=true + grace window for every existing partner so
--      the policy is universally applied.
--   4. Mirrors the same change on partner_users (per-user MFA enforcement).
--
-- Non-operator posture: partners hold the operator credential chain; strong
-- authentication on that chain is table stakes for the subcontractor BAA
-- liability boundary. This is belt AND suspenders to the Stripe-Connect /
-- fleet-order privileged-chain safeguards that run below.
--
-- Rollback: setting mfa_required=false for a partner is always possible
-- from the admin UI; the column default is purely for new rows.

BEGIN;

-- ─── partners: flip default + add grace window ──────────────────────

ALTER TABLE partners
    ALTER COLUMN mfa_required SET DEFAULT true;

ALTER TABLE partners
    ADD COLUMN IF NOT EXISTS mfa_grace_period_until TIMESTAMPTZ;

-- Every existing partner gets mfa_required=true effective immediately,
-- with a 14-day grace window to enroll. Partners who already have
-- mfa_enabled=true are unaffected by the grace window (the gate only
-- trips when required=true AND enabled=false).
UPDATE partners
   SET mfa_required = true,
       mfa_grace_period_until = COALESCE(mfa_grace_period_until, NOW() + INTERVAL '14 days')
 WHERE mfa_required IS DISTINCT FROM true
    OR mfa_grace_period_until IS NULL;

CREATE INDEX IF NOT EXISTS idx_partners_mfa_grace
    ON partners (mfa_grace_period_until)
    WHERE mfa_required = true AND mfa_enabled = false;

COMMENT ON COLUMN partners.mfa_grace_period_until IS
    'When set, login is permitted without MFA until this timestamp even if '
    'mfa_required=true. Post-grace, the partner_auth gate returns 403 until '
    'they enroll. Added by Migration 227 during the April-2026 MFA rollout.';


-- ─── partner_users: same policy ─────────────────────────────────────

ALTER TABLE partner_users
    ALTER COLUMN mfa_enabled SET DEFAULT false;  -- unchanged, documented

-- partner_users doesn't currently have mfa_required — add it so
-- per-user enforcement is possible (e.g., partner admins MFA-required,
-- billing-only users optional per partner policy).
ALTER TABLE partner_users
    ADD COLUMN IF NOT EXISTS mfa_required BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE partner_users
    ADD COLUMN IF NOT EXISTS mfa_grace_period_until TIMESTAMPTZ;

UPDATE partner_users
   SET mfa_required = true,
       mfa_grace_period_until = COALESCE(mfa_grace_period_until, NOW() + INTERVAL '14 days')
 WHERE mfa_required IS DISTINCT FROM true
    OR mfa_grace_period_until IS NULL;

CREATE INDEX IF NOT EXISTS idx_partner_users_mfa_grace
    ON partner_users (mfa_grace_period_until)
    WHERE mfa_required = true AND mfa_enabled = false;

COMMENT ON COLUMN partner_users.mfa_required IS
    'Enforced by partner_auth gate. Grace window governed by mfa_grace_period_until.';

COMMIT;
