-- Migration 277: client_user email rename + onboarding auto-provision
--
-- Round-table 2026-05-05
-- (.agent/plans/22-client-user-email-rename-roundtable-2026-05-05.md).
-- Closes two gaps simultaneously:
--   (P1) onboarding-no-login: Stripe self-serve signup never auto-mints
--        a client_users row, leaving customers stranded. This adds the
--        per-partner toggle that gates auto-provision at signup-completion.
--   (P2) email-immutable: client_users.email had zero mutator endpoints
--        anywhere — partner, substrate, or self-service. North Valley
--        2026-05-05 was unreachable except via psql. This adds the audit
--        ledger; endpoints land in the same commit as this migration.
--
-- Posture: email rename is administrative on a substrate-managed table,
-- NOT operational/clinical. Same posture as the MFA admin overrides
-- (mig 276): substrate provides the recovery primitive, partner remains
-- operator. Three cohabiting actor classes:
--   self      → user changes their own (with magic-link confirm to NEW)
--   partner   → partner_role='admin' acts on a client_user in a partner
--               org (≥20ch reason, immediate-completion)
--   substrate → admin_users acts on any client_user (≥40ch reason, P0
--               operator-alert to the partner who owns the org)
--
-- 4 new ALLOWED_EVENTS land alongside this mig (45 → 49):
--   client_user_email_changed_by_self
--   client_user_email_changed_by_partner
--   client_user_email_changed_by_substrate
--   client_user_email_change_reversed   (kept in case future reversal
--                                        flow lands; not used by v1
--                                        per Maya P1 — drop the window)

-- ─── Onboarding auto-provision toggle ─────────────────────────────

ALTER TABLE partners
    ADD COLUMN IF NOT EXISTS auto_provision_owner_on_signup
        BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN partners.auto_provision_owner_on_signup IS
'Default true (Maya enterprise-grade-default posture). When true, '
'client_signup._complete_signup auto-INSERTs a client_users(role=owner) '
'row for the signup email and emails a "set your password" magic link. '
'When false, the partner manually invites — preserves operator-in-the-'
'loop posture for partners who want it.';

-- ─── Email-change audit ledger ────────────────────────────────────

CREATE TABLE IF NOT EXISTS client_user_email_change_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    client_user_id UUID NOT NULL,
    -- Org snapshot at time-of-change (denormalized so retired orgs
    -- still show up correctly in audit forensics; client_users.client_org_id
    -- is the live FK).
    client_org_id UUID NOT NULL,

    old_email TEXT NOT NULL,
    new_email TEXT NOT NULL,

    changed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- Three discrete actor classes per round-table.
    changed_by_kind TEXT NOT NULL
        CHECK (changed_by_kind IN ('self', 'partner', 'substrate')),
    -- Email of the actor (for 'self' this equals old_email; for partner
    -- the partner-admin's email; for substrate the admin_user's email).
    changed_by_email TEXT NOT NULL,

    -- Reason from the actor. Friction asymmetry per round-table:
    -- self ≥ 0   (user changing own email is operational, not privileged)
    -- partner ≥ 20  (matches privileged-action chain default)
    -- substrate ≥ 40 (matches MFA-revoke higher friction — substrate
    --                 acts only in recovery cases that need full context)
    -- The numeric threshold is enforced at the endpoint layer; CHECK
    -- here just guarantees non-empty for non-self kinds.
    reason TEXT NOT NULL,
    CONSTRAINT chk_email_change_reason_length
        CHECK (
            (changed_by_kind = 'self' AND LENGTH(reason) >= 0)
            OR (changed_by_kind = 'partner' AND LENGTH(reason) >= 20)
            OR (changed_by_kind = 'substrate' AND LENGTH(reason) >= 40)
        ),

    -- Cryptographic chain anchor (event written by the same txn that
    -- mutates client_users.email).
    attestation_bundle_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_email_change_user
    ON client_user_email_change_log (client_user_id, changed_at DESC);

CREATE INDEX IF NOT EXISTS idx_email_change_org
    ON client_user_email_change_log (client_org_id, changed_at DESC);

COMMENT ON TABLE client_user_email_change_log IS
'Append-only audit ledger of client_users.email mutations. Three actor '
'classes (self/partner/substrate) reflect the round-table 2026-05-05 '
'verdict that all three need rename access without breaking operator '
'posture. Auditor kit walks rows by client_user_id to surface "Identity '
'changes" history. Reversal-window is intentionally absent (Maya P1 '
'2026-05-05 — re-running the rename is the undo path; a window adds '
'complexity with no benefit since the new email is itself a rename '
'target if mistaken). NEVER mutate post-INSERT — append-only enforced '
'by trg_prevent_email_change_log_deletion.';

-- ─── Append-only enforcement ──────────────────────────────────────

CREATE OR REPLACE FUNCTION prevent_email_change_log_deletion()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'client_user_email_change_log is append-only audit-class. '
        'DELETE blocked. Email rename is a chain-of-custody event; '
        'rows persist for the life of the database.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_email_change_log_deletion
    ON client_user_email_change_log;
CREATE TRIGGER trg_prevent_email_change_log_deletion
    BEFORE DELETE ON client_user_email_change_log
    FOR EACH ROW EXECUTE FUNCTION prevent_email_change_log_deletion();

-- Same protection on UPDATE — once a rename is recorded the row is
-- frozen.
CREATE OR REPLACE FUNCTION prevent_email_change_log_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'client_user_email_change_log is append-only. UPDATE blocked.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_email_change_log_mutation
    ON client_user_email_change_log;
CREATE TRIGGER trg_prevent_email_change_log_mutation
    BEFORE UPDATE ON client_user_email_change_log
    FOR EACH ROW EXECUTE FUNCTION prevent_email_change_log_mutation();
