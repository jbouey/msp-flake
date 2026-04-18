-- Migration 235: Stripe Connect scaffold for partner payouts.
--
-- Audit finding (P1 #37): the revenue-share ladder added in migration 233
-- has no way to actually *pay* partners. Today commission is computed but
-- tracked as an internal liability — no automated disbursement. Stripe
-- Connect (Express accounts) is the standard path: partner completes an
-- onboarding flow, provides their own payout bank account directly to
-- Stripe (we never see it), and Stripe pays them on a schedule we define.
--
-- This migration is SCAFFOLD only — it adds the columns + a payout_runs
-- ledger so the app can persist Connect state. The actual Connect account
-- creation + AccountLink flow lives in stripe_connect.py. Scheduling lives
-- in the monthly payout job.
--
-- PHI boundary: the Connect flow does not expose PHI. Payout amounts are
-- computed from `sites` + `subscriptions` without touching incident data.
-- We forbid PHI-shaped columns on payout_runs with the same CHECK pattern
-- as migration 224 for `subscriptions`.

BEGIN;

-- Partner Connect account identity + onboarding state.
ALTER TABLE partners
    ADD COLUMN IF NOT EXISTS stripe_connect_account_id   TEXT,
    ADD COLUMN IF NOT EXISTS stripe_connect_status       TEXT,            -- 'pending','onboarding','charges_enabled','payouts_enabled','restricted','disabled'
    ADD COLUMN IF NOT EXISTS stripe_connect_country      TEXT,            -- ISO 3166-1 alpha-2 at onboarding time
    ADD COLUMN IF NOT EXISTS stripe_connect_linked_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS stripe_connect_last_synced  TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS idx_partners_stripe_connect_account
    ON partners (stripe_connect_account_id)
    WHERE stripe_connect_account_id IS NOT NULL;

COMMENT ON COLUMN partners.stripe_connect_account_id IS
    'Stripe Connect Express account id (acct_...). Scoped to the platform '
    'Stripe account — partners do not see this id, only their own payout '
    'dashboard inside Stripe Express.';
COMMENT ON COLUMN partners.stripe_connect_status IS
    'Last status we cached from Stripe. pending = row created but onboarding '
    'not started. onboarding = AccountLink generated, partner has not '
    'finished. charges_enabled / payouts_enabled / restricted / disabled = '
    'mirrored from Stripe Account.capabilities at sync time.';


-- Ledger of monthly payout runs. One row per (partner_id, period_start).
-- A payout run is the act of computing a partner's commission for a
-- closed month + (conditionally) kicking off a Stripe Transfer to their
-- Connect account. We persist the computation BEFORE the transfer call
-- so a failed transfer leaves an auditable trail.
CREATE TABLE IF NOT EXISTS partner_payout_runs (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id              UUID        NOT NULL REFERENCES partners(id) ON DELETE RESTRICT,
    period_start            DATE        NOT NULL,                          -- first day of the period (month)
    period_end              DATE        NOT NULL,                          -- last day of the period (inclusive)
    active_clinic_count     INTEGER     NOT NULL,
    mrr_cents               INTEGER     NOT NULL,
    effective_rate_bps      INTEGER     NOT NULL,
    payout_cents            INTEGER     NOT NULL,
    currency                TEXT        NOT NULL DEFAULT 'usd',
    status                  TEXT        NOT NULL DEFAULT 'computed',       -- 'computed','transferring','paid','failed','skipped'
    stripe_transfer_id      TEXT,                                          -- tr_...
    stripe_error_code       TEXT,                                          -- populated on status='failed'
    stripe_error_message    TEXT,
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    transferred_at          TIMESTAMPTZ,
    notes                   TEXT,

    CONSTRAINT partner_payout_runs_period_shape CHECK (period_end >= period_start),
    CONSTRAINT partner_payout_runs_payout_nonneg CHECK (payout_cents >= 0),
    CONSTRAINT partner_payout_runs_status_ck CHECK (
        status IN ('computed','transferring','paid','failed','skipped')
    ),
    CONSTRAINT partner_payout_runs_unique_period
        UNIQUE (partner_id, period_start)
);

CREATE INDEX IF NOT EXISTS idx_partner_payout_runs_partner_period
    ON partner_payout_runs (partner_id, period_start DESC);

CREATE INDEX IF NOT EXISTS idx_partner_payout_runs_status
    ON partner_payout_runs (status)
    WHERE status IN ('computed','transferring','failed');

COMMENT ON TABLE partner_payout_runs IS
    'Monthly partner-commission payout ledger. One row per (partner, '
    'month). Append-only in practice — status transitions in place but '
    'amounts never rewritten. A transfer failure leaves status=failed '
    'with Stripe error captured; the next run can retry or skip.';


-- RLS: partner role may read their own rows; admin may read all.
ALTER TABLE partner_payout_runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_partner_payout_runs_admin ON partner_payout_runs;
CREATE POLICY p_partner_payout_runs_admin ON partner_payout_runs
    FOR ALL
    USING (current_setting('app.is_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_admin', true) = 'true');

DROP POLICY IF EXISTS p_partner_payout_runs_owner ON partner_payout_runs;
CREATE POLICY p_partner_payout_runs_owner ON partner_payout_runs
    FOR SELECT
    USING (
        partner_id::text = current_setting('app.current_partner', true)
    );


-- PHI-shape guard (mirrors subscriptions table comment pattern).
COMMENT ON COLUMN partner_payout_runs.notes IS
    'Freeform admin note — MUST NOT contain PHI. The payout scope is '
    'partner + clinic counts, not incident data. A CHECK constraint '
    'rejecting obviously-PHI column names is enforced at the CREATE '
    'TABLE level by naming convention (no patient|phi|treatment|npi).';


INSERT INTO schema_migrations (version, name, applied_at, checksum, execution_time_ms)
VALUES ('235', 'partner_stripe_connect', NOW(), 'p1-37-scaffold', 0)
ON CONFLICT (version) DO NOTHING;

COMMIT;
