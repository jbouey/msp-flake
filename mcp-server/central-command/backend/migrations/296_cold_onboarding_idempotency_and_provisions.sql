-- ============================================================================
-- Migration 296: Cold-onboarding adversarial-walkthrough P0 + P1-6 closure.
--                Idempotency UNIQUE indexes + self-serve provision codes.
--
-- BACKGROUND
--   2026-05-09 cold-onboarding adversarial walkthrough audit
--   (audit/multi-tenant-cold-onboarding-walkthrough-2026-05-09.md)
--   surfaced 4 P0s closing the dead-end where a customer pays $499 via
--   Stripe but receives nothing. Two of the four P0s + one P1 are
--   schema work; this migration ships them in lockstep with the
--   `handle_checkout_completed_for_signup` wire-through (P0 #1+#3+#4)
--   in client_signup.py and the BAA SQL fix (P0 #2) in
--   client_attestation_letter.py.
--
-- WHAT THIS MIGRATION DOES
--   1. UNIQUE index on (email, plan_lookup_key) for non-expired
--      signup_sessions — blocks duplicate-form-submission churn that
--      would otherwise create N Stripe customers for one buyer.
--      P1-6 from the audit.
--
--   2. UNIQUE index on (email, baa_version) for baa_signatures —
--      makes the e-signature record set-of-(email,version) instead of
--      multiset. A user re-clicking "I agree" on the same BAA version
--      should hit the same row, not append a new one. The append-only
--      trigger from migration 224 still applies (no UPDATE/DELETE);
--      collision-on-conflict resolution is up to the caller (sign_baa
--      in client_signup.py uses INSERT ... ON CONFLICT DO NOTHING +
--      re-fetch). P1-6 from the audit.
--
--   3. ALTER appliance_provisions: relax `partner_id NOT NULL` to
--      nullable + add `client_org_id` column. Self-serve cold path
--      has no partner — the previous schema forced every cold-onboard
--      flow through a fake "self-serve" partner row. With this change,
--      provision codes can be issued directly to a `client_orgs` row.
--      Sites materialized from such a code carry the
--      client_org_id (sites.client_org_id is NOT NULL since mig 067).
--      A CHECK constraint enforces "exactly one of (partner_id,
--      client_org_id) is set" so a self-serve provision can never
--      silently bind to a partner record. P0 #4 from the audit.
--
-- WHY ON CONFLICT-FRIENDLY (partial UNIQUE rather than UNIQUE):
--   `signup_sessions` has rows in many states; only the active
--   non-expired set matters for dedup. A buyer whose 2-hour signup
--   expired needs to be able to start a new session for the same
--   plan. Using `WHERE status != 'expired'` fits that semantics.
--
-- ROLLBACK
--   DROP INDEX IF EXISTS uniq_signup_sessions_email_plan;
--   DROP INDEX IF EXISTS uniq_baa_signatures_email_version;
--   ALTER TABLE appliance_provisions
--       DROP CONSTRAINT IF EXISTS appliance_provisions_partner_or_org_ck;
--   ALTER TABLE appliance_provisions DROP COLUMN IF EXISTS client_org_id;
--   ALTER TABLE appliance_provisions ALTER COLUMN partner_id SET NOT NULL;
--   (Only safe if no rows have partner_id NULL — i.e. no self-serve
--   provisions have been issued yet.)
-- ============================================================================

BEGIN;

-- ─── 1. signup_sessions email+plan idempotency ───────────────────
-- `signup_sessions.plan` is the lookup key (the form's plan field
-- is enum-validated against PLAN_CATALOG in client_signup.py).
-- A `status` column was not added to that table in mig 224 — the
-- shape there is "rows live until expires_at, then prune." Use
-- completed_at as the partial-index predicate (`completed_at IS NULL`
-- = "active" row); the expired-row culling happens via a separate
-- `prune_signup_sessions()` worker rather than baked into the
-- index predicate. Postgres rejects NOW() in partial-index predicates
-- (must be IMMUTABLE) — that bit migration 276 last week and is
-- now a known-class mistake (CLAUDE.md). Active-set is enforced at
-- the prune-job level, not the index level.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_signup_sessions_email_plan
    ON signup_sessions (email, plan)
 WHERE completed_at IS NULL;
COMMENT ON INDEX uniq_signup_sessions_email_plan IS
    'Cold-onboarding P1-6: a single email cannot have two concurrently-'
    'active signup_sessions (completed_at IS NULL) for the same plan. '
    'Completed rows fall out of the partial index so a new attempt is '
    'allowed once the prior session finishes. Expired (uncompleted) '
    'rows must be cleaned up by a prune worker — Postgres partial-index '
    'predicates require IMMUTABLE expressions, NOW() is not IMMUTABLE.';


-- ─── 2. baa_signatures email+version idempotency ─────────────────
CREATE UNIQUE INDEX IF NOT EXISTS uniq_baa_signatures_email_version
    ON baa_signatures (email, baa_version);
COMMENT ON INDEX uniq_baa_signatures_email_version IS
    'Cold-onboarding P1-6: an e-signature for a given (email, BAA '
    'version) is a single row. Re-clicking "I agree" on the same '
    'version is a no-op, not a duplicate audit-row. The append-only '
    'trigger from mig 224 still blocks UPDATE/DELETE.';


-- ─── 3. appliance_provisions self-serve client_org_id path ───────
-- 3a. Drop the NOT NULL on partner_id.
ALTER TABLE appliance_provisions
    ALTER COLUMN partner_id DROP NOT NULL;

-- 3b. Add client_org_id (nullable; mutex with partner_id).
ALTER TABLE appliance_provisions
    ADD COLUMN IF NOT EXISTS client_org_id UUID
        REFERENCES client_orgs(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_appliance_provisions_client_org
    ON appliance_provisions (client_org_id)
 WHERE client_org_id IS NOT NULL;

-- 3c. CHECK: exactly one of (partner_id, client_org_id) MUST be set.
-- A provision with both would be ambiguous; with neither, has no
-- owner. NOT VALID first so the deploy doesn't block on any
-- legacy partner-scope rows that already exist (they all have
-- partner_id set, satisfying the constraint, but VALIDATE would
-- still scan the table — keep the deploy fast).
ALTER TABLE appliance_provisions
    DROP CONSTRAINT IF EXISTS appliance_provisions_partner_or_org_ck;
ALTER TABLE appliance_provisions
    ADD CONSTRAINT appliance_provisions_partner_or_org_ck
    CHECK (
        (partner_id IS NOT NULL AND client_org_id IS NULL)
        OR (partner_id IS NULL AND client_org_id IS NOT NULL)
    ) NOT VALID;
ALTER TABLE appliance_provisions
    VALIDATE CONSTRAINT appliance_provisions_partner_or_org_ck;

COMMENT ON COLUMN appliance_provisions.client_org_id IS
    'Cold-onboarding P0 #4: self-serve provision code owned by a '
    'client_orgs row directly (no MSP partner in the loop). Mutex '
    'with partner_id (CHECK constraint). Sites materialized from '
    'this provision inherit client_org_id; partner_id stays NULL.';


COMMIT;
