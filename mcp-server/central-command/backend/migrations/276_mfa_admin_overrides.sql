-- Migration 276: MFA admin overrides — task #19 closure
--
-- Round-table 2026-05-05 (Camila + Brian + Linda + Steve + Adam +
-- Maya 2nd-eye). 3 sub-features × 2 portals = 6 endpoints + 1 new
-- table for Steve's mitigation B (24h reversible link on revoke).
--
-- Sub-features:
--   1. Toggle org-level mfa_required (no schema change — column
--      already exists on client_orgs + partners; just need endpoints
--      to UPDATE it).
--   2. Force-reset a user's MFA (clears mfa_secret + mfa_enabled;
--      user re-enrolls on next login). No schema change.
--   3. Revoke a user's MFA (clears MFA + writes a row here so the
--      user can self-restore via emailed magic link within 24h).
--      Steve P3 mitigation B: revoke is the highest-risk action
--      because it CAN be the attack itself (compromised admin
--      revokes target's MFA, takes over). The 24h reversible link
--      is the defense.
--
-- Plus the owner-transfer interlock (Steve mit D): both transfer
-- state machines (mig 273 + 274) refuse `initiate` while ANY pending
-- mfa_revocation exists for ANY in-org user. Same defensive posture
-- as the Session 213 phantom-rollout precondition.

CREATE TABLE IF NOT EXISTS mfa_revocation_pending (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Heterogeneous: target is EITHER a client_user OR a partner_user
    -- (not both). Discriminated by user_kind + the matching scope_id.
    -- We don't FK to client_users/partner_users because cross-table
    -- FK isn't supported; the application enforces existence.
    target_user_id UUID NOT NULL,
    user_kind TEXT NOT NULL
        CHECK (user_kind IN ('client_user', 'partner_user')),
    -- Scope: client_org_id when user_kind='client_user';
    --        partner_id when user_kind='partner_user'.
    -- The interlock query (refuse owner-transfer while pending)
    -- uses this column to scope.
    scope_id UUID NOT NULL,

    target_email TEXT NOT NULL,

    revoked_by_email TEXT NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- 24h restoration window (Steve mit B). Application sets this;
    -- CHECK enforces non-null. Sweep loop marks 'expired' rows whose
    -- expires_at has passed AND restored_at IS NULL.
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,

    -- SHA256(restoration_token). Plaintext sent in the email; never
    -- stored. Cleared (set to '') after restoration to prevent reuse.
    reversal_token_hash TEXT NOT NULL,

    -- Reason from the revoking actor (≥40 chars per Steve — higher
    -- friction than the ≥20ch elsewhere in the privileged chain).
    reason TEXT NOT NULL,

    -- Restoration outcome
    restored_at TIMESTAMP WITH TIME ZONE,
    restored_by_email TEXT,

    -- Array of attestation_bundle_ids: revoke + (if restored) restore.
    attestation_bundle_ids JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Validation: target_email lookup
    CONSTRAINT chk_mfa_revocation_email_format
        CHECK (POSITION('@' IN target_email) > 0),
    -- Reason length per Steve mit B (≥40ch friction)
    CONSTRAINT chk_mfa_revocation_reason_length
        CHECK (LENGTH(reason) >= 40)
);

-- One pending revocation per user. If admin re-revokes after the
-- target restored, that's a fresh row (restored_at on prior is NOT
-- NULL so this partial index doesn't catch it).
CREATE UNIQUE INDEX IF NOT EXISTS idx_mfa_revocation_one_pending_per_user
    ON mfa_revocation_pending (target_user_id)
    WHERE restored_at IS NULL AND expires_at > NOW();

-- Interlock query support: scope_id-keyed lookup with
-- "still actionable" filter (not restored, not expired).
CREATE INDEX IF NOT EXISTS idx_mfa_revocation_scope_active
    ON mfa_revocation_pending (scope_id, user_kind)
    WHERE restored_at IS NULL AND expires_at > NOW();

-- Sweep loop support: order-by-expires_at within the active window.
CREATE INDEX IF NOT EXISTS idx_mfa_revocation_sweep
    ON mfa_revocation_pending (expires_at)
    WHERE restored_at IS NULL;

-- Audit-class table: append-only.
CREATE OR REPLACE FUNCTION prevent_mfa_revocation_deletion()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'mfa_revocation_pending is append-only audit-class. '
        'DELETE blocked. Mark restored_at (via /restore endpoint) '
        'or wait for the sweep loop to expire it.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_mfa_revocation_deletion
    ON mfa_revocation_pending;
CREATE TRIGGER trg_prevent_mfa_revocation_deletion
    BEFORE DELETE ON mfa_revocation_pending
    FOR EACH ROW EXECUTE FUNCTION prevent_mfa_revocation_deletion();


COMMENT ON TABLE mfa_revocation_pending IS
'Append-only ledger of MFA revocations with 24h reversible-link '
'window (Steve mit B from task #19 round-table 2026-05-05). The '
'revoking admin clears the user''s mfa_secret + mfa_enabled in the '
'same txn that writes a row here; the user receives an email with '
'a magic link valid for 24h. Clicking the link restores MFA + '
'records the restoration. The sweep loop marks expired rows whose '
'window passed without restoration. Owner-transfer state machines '
'(mig 273 + 274) refuse initiate while any row here is still '
'actionable for any in-org user (Steve mit D anti-race posture).';

COMMENT ON COLUMN mfa_revocation_pending.user_kind IS
'Discriminator for target_user_id: client_user references '
'client_users.id, partner_user references partner_users.id. The '
'application validates existence — no cross-table FK.';

COMMENT ON COLUMN mfa_revocation_pending.scope_id IS
'When user_kind=client_user: client_users.client_org_id. '
'When user_kind=partner_user: partner_users.partner_id. Used by '
'the owner-transfer interlock to scope "any pending revocation '
'in this org".';

COMMENT ON COLUMN mfa_revocation_pending.reason IS
'Higher friction than elsewhere in the privileged-action chain '
'(≥40ch vs ≥20ch). Steve mit B rationale: revoke can be the '
'attack vector itself; require explicit elaboration of the '
'business reason so the audit row carries enough context for '
'forensic reconstruction.';
