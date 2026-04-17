-- Migration 229: partner_invites — partner→clinic onboarding flow
--
-- The distribution bottleneck the audit surfaced: partners cannot onboard
-- a clinic without OsirisCare intervention. This table provides a signed,
-- single-use invite token that lets the partner bootstrap a clinic signup
-- flow tied to their partner_id + plan.
--
-- Flow (enforced by endpoints in Batch C):
--   1. Partner (with active MSA+BAA+Reseller) calls POST /api/partners/
--      invites/create → generates a random 32-byte token, stores SHA256(token),
--      returns plaintext ONCE.
--   2. Partner sends the invite URL (with ?token=) to the clinic via their
--      own email/communication (OsirisCare is not in the loop).
--   3. Clinic opens the URL, hits GET /api/partners/invites/{token}/validate
--      to see the branded landing (partner brand, plan, BAA version).
--   4. Clinic runs through the normal /signup flow; on success, the webhook
--      handler matches signup_id → invite_id (stored via metadata), marks
--      the invite consumed, and sets the resulting subscription's partner_id.
--
-- Idempotency: consumed_at is single-use. Repeat consumption → 409 Conflict.
-- Expiry: 14 days from creation (configurable per-invite).

BEGIN;

CREATE TABLE IF NOT EXISTS partner_invites (
    invite_id            TEXT         PRIMARY KEY,                   -- UUID
    partner_id           UUID         NOT NULL,
    token_sha256         TEXT         NOT NULL UNIQUE,                -- hex digest; plaintext never stored
    clinic_email         TEXT,                                        -- optional — invite can be unscoped
    clinic_name          TEXT,                                        -- optional — for the branded landing page
    plan                 TEXT         NOT NULL,                       -- one of PARTNER_PLAN_CATALOG keys
    partner_brand        TEXT,                                        -- overrides default OsirisCare brand on landing
    created_by_user_id   UUID,                                        -- partner_users.id that issued the invite
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW() + INTERVAL '14 days',
    consumed_at          TIMESTAMPTZ,
    consumed_signup_id   TEXT,                                        -- signup_sessions.signup_id that consumed this
    consumed_ip          TEXT,
    consumed_user_agent  TEXT,
    revoked_at           TIMESTAMPTZ,                                 -- partner-initiated revoke
    revoke_reason        TEXT,
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT partner_invites_plan_ck CHECK (
        plan IN ('pilot','essentials','professional','enterprise')
    ),
    CONSTRAINT partner_invites_token_len CHECK (length(token_sha256) = 64),
    CONSTRAINT partner_invites_partner_fk FOREIGN KEY (partner_id)
        REFERENCES partners(id) ON DELETE CASCADE,
    CONSTRAINT partner_invites_user_fk FOREIGN KEY (created_by_user_id)
        REFERENCES partner_users(id) ON DELETE SET NULL,
    CONSTRAINT partner_invites_consume_exclusive CHECK (
        NOT (consumed_at IS NOT NULL AND revoked_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_partner_invites_partner
    ON partner_invites (partner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_partner_invites_token
    ON partner_invites (token_sha256)
    WHERE consumed_at IS NULL AND revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_partner_invites_expires
    ON partner_invites (expires_at)
    WHERE consumed_at IS NULL AND revoked_at IS NULL;

-- Guard: an invite cannot be re-consumed. The consumer-side endpoint
-- uses UPDATE ... WHERE consumed_at IS NULL for atomic single-use.
CREATE OR REPLACE FUNCTION prevent_partner_invite_reconsume()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    -- Allow revoke (consumed_at stays NULL, revoked_at becomes non-null).
    -- Allow first consume (consumed_at goes NULL → not-NULL).
    -- Block anything that would modify an already-consumed invite.
    IF OLD.consumed_at IS NOT NULL
       AND (NEW.consumed_at IS NULL
            OR NEW.consumed_at <> OLD.consumed_at
            OR NEW.consumed_signup_id IS DISTINCT FROM OLD.consumed_signup_id) THEN
        RAISE EXCEPTION
            'partner_invites.consumed_at is single-use; cannot re-consume invite_id=%',
            OLD.invite_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_partner_invites_single_consume ON partner_invites;
CREATE TRIGGER trg_partner_invites_single_consume
    BEFORE UPDATE ON partner_invites
    FOR EACH ROW EXECUTE FUNCTION prevent_partner_invite_reconsume();

COMMENT ON TABLE partner_invites IS
    'Partner→clinic onboarding tokens. Single-use, 14-day default TTL, '
    'partner must have active MSA+BAA+Reseller to create. Plaintext token '
    'returned only at create-time; subsequent lookups use SHA256 hash.';


INSERT INTO schema_migrations (version, applied_at, checksum)
VALUES ('229_partner_invites', NOW(), 'n/a')
ON CONFLICT (version) DO NOTHING;

COMMIT;
