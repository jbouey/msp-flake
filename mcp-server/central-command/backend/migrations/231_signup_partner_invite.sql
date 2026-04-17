-- Migration 231: wire partner invites into the client signup flow
--
-- Session 207 landed the self-serve client signup (224). Session 208
-- landed the partner→clinic invite table (229). This migration connects
-- the two so that a clinic completing signup from a partner-invite link
-- is auto-attached to that partner's book of business.
--
-- What we add:
--   1. signup_sessions.partner_invite_token — plaintext token carried
--      through the signup funnel. Nulled after consumption so it's not
--      sitting in the row indefinitely.
--   2. signup_sessions.partner_id — set on webhook consume for audit.
--   3. subscriptions.partner_id — FK to partners(id). When NULL, the
--      subscription is direct-to-clinic (OsirisCare holds the BA
--      relationship). When set, the MSP is the BA and OsirisCare is the
--      subcontractor under the MSA+BAA+Reseller chain (Migration 228).
--
-- Why store the token (briefly): at /checkout we inject it into Stripe
-- checkout session metadata. On webhook delivery, the token in metadata
-- is the single source of truth for the consume_invite_for_signup()
-- call. signup_sessions.partner_invite_token exists only to survive the
-- /start → /checkout hop without requiring the frontend to re-post it.

BEGIN;

ALTER TABLE signup_sessions
    ADD COLUMN IF NOT EXISTS partner_invite_token TEXT;  -- plaintext, short-lived

ALTER TABLE signup_sessions
    ADD COLUMN IF NOT EXISTS partner_id UUID;

-- Partial index: only rows with an unconsumed invite token need indexing.
CREATE INDEX IF NOT EXISTS idx_signup_sessions_invite_token
    ON signup_sessions (partner_invite_token)
    WHERE partner_invite_token IS NOT NULL AND completed_at IS NULL;

COMMENT ON COLUMN signup_sessions.partner_invite_token IS
    'Plaintext partner invite token captured at /start. Nulled once the '
    'Stripe webhook consumes the invite via consume_invite_for_signup().';
COMMENT ON COLUMN signup_sessions.partner_id IS
    'Set on invite consumption — correlates this signup to the partner '
    'who issued the invite. Provenance only — subscriptions.partner_id '
    'is the operational field billing + gating read from.';


-- subscriptions.partner_id — NULL = direct-to-clinic (OsirisCare is BA).
-- Non-NULL = MSP-resold (OsirisCare is subcontractor under MSP BAA).
ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS partner_id UUID;

ALTER TABLE subscriptions
    ADD CONSTRAINT subscriptions_partner_fk
    FOREIGN KEY (partner_id) REFERENCES partners(id) ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX IF NOT EXISTS idx_subscriptions_partner
    ON subscriptions (partner_id)
    WHERE partner_id IS NOT NULL;

COMMENT ON COLUMN subscriptions.partner_id IS
    'Partner that resold this subscription (set via partner_invites '
    'consumption). NULL = direct-to-clinic. Non-NULL = MSP is the BA '
    'and OsirisCare is subcontractor under Migration 228 MSA+BAA chain.';

COMMIT;
