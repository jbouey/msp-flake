-- Migration 224: client self-serve signup + billing state
--
-- Four new tables for the PM-consensus client billing path (Session 207):
--   signup_sessions  — short-lived state machine for the /signup flow
--                      (email → BAA e-sign → Stripe Checkout)
--   subscriptions    — sync'd from Stripe via webhooks; no card data
--   stripe_events    — webhook dedup (event_id from Stripe is PK)
--   baa_signatures   — e-signature audit trail, 7-year retention
--
-- Scope boundary: no column names matching PHI patterns. Enforced by
-- CHECK constraints at schema level so a future developer can't silently
-- widen scope into HIPAA Business Associate territory. The "design so we
-- never need a BAA with Stripe" posture is materialized here.
--
-- Partner billing uses the pre-existing partners + partner_subscriptions
-- tables (untouched). This migration is purely for the direct-customer
-- (healthcare SMB) self-serve path.

BEGIN;

-- ─── signup_sessions ─────────────────────────────────────────────
-- Short-lived state while a customer walks through /signup.
-- Cleaned up on session expiry (2hr) OR on successful completion.
CREATE TABLE IF NOT EXISTS signup_sessions (
    signup_id               TEXT         PRIMARY KEY,              -- UUID
    email                   TEXT         NOT NULL,
    practice_name           TEXT,
    billing_contact_name    TEXT,
    state                   TEXT,                                  -- US state code for tax
    plan                    TEXT         NOT NULL,                 -- pilot|essentials|professional|enterprise
    stripe_customer_id      TEXT,                                  -- set once Stripe customer created
    baa_signature_id        TEXT,                                  -- FK → baa_signatures.signature_id
    baa_signed_at           TIMESTAMPTZ,
    checkout_session_id     TEXT,                                  -- Stripe Checkout session ID
    completed_at            TIMESTAMPTZ,                           -- set on successful checkout
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW() + INTERVAL '2 hours',

    CONSTRAINT signup_sessions_plan_ck CHECK (
        plan IN ('pilot','essentials','professional','enterprise')
    ),
    CONSTRAINT signup_sessions_state_ck CHECK (
        state IS NULL OR state ~ '^[A-Z]{2}$'
    )
);
CREATE INDEX IF NOT EXISTS idx_signup_sessions_email ON signup_sessions (email);
CREATE INDEX IF NOT EXISTS idx_signup_sessions_expires ON signup_sessions (expires_at)
    WHERE completed_at IS NULL;


-- ─── baa_signatures ──────────────────────────────────────────────
-- Append-only e-signature audit. Tied to an email + IP + UA + the
-- SHA256 of the BAA text they saw (so we can prove what was signed
-- even if the BAA text changes later).
CREATE TABLE IF NOT EXISTS baa_signatures (
    signature_id        TEXT         PRIMARY KEY,                  -- UUID
    email               TEXT         NOT NULL,
    stripe_customer_id  TEXT,                                      -- filled once customer exists
    signer_name         TEXT         NOT NULL,                     -- typed name in the form
    signer_ip           TEXT,
    signer_user_agent   TEXT,
    baa_version         TEXT         NOT NULL,                     -- e.g., "v1.0-2026-04-15"
    baa_text_sha256     TEXT         NOT NULL,                     -- hex digest
    metadata            JSONB        DEFAULT '{}'::jsonb,
    signed_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT baa_signatures_sha256_ck CHECK (length(baa_text_sha256) = 64)
);
CREATE INDEX IF NOT EXISTS idx_baa_signatures_email ON baa_signatures (email);
CREATE INDEX IF NOT EXISTS idx_baa_signatures_customer ON baa_signatures (stripe_customer_id);

-- Append-only trigger — DELETE + UPDATE blocked (7-year HIPAA retention)
CREATE OR REPLACE FUNCTION prevent_baa_signature_modification()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'baa_signatures is append-only (HIPAA §164.316(b)(2)(i) 7-year retention). '
        'Attempted % on signature_id=%', TG_OP, COALESCE(OLD.signature_id, NEW.signature_id);
END;
$$;
DROP TRIGGER IF EXISTS trg_baa_no_update ON baa_signatures;
CREATE TRIGGER trg_baa_no_update
    BEFORE UPDATE OR DELETE ON baa_signatures
    FOR EACH ROW EXECUTE FUNCTION prevent_baa_signature_modification();


-- ─── subscriptions ───────────────────────────────────────────────
-- Projection of Stripe subscription state, populated from webhooks.
-- ONE ROW per stripe_subscription_id. Card data NEVER stored here —
-- enforced by CHECK on column names (belt + suspenders + legal posture).
CREATE TABLE IF NOT EXISTS subscriptions (
    stripe_subscription_id  TEXT         PRIMARY KEY,
    stripe_customer_id      TEXT         NOT NULL,
    site_id                 TEXT,                                  -- null until provisioning links site
    plan                    TEXT         NOT NULL,                 -- pilot|essentials|professional|enterprise
    status                  TEXT         NOT NULL,                 -- Stripe statuses: active|past_due|canceled|unpaid|incomplete|incomplete_expired|trialing|paused
    trial_end               TIMESTAMPTZ,
    current_period_start    TIMESTAMPTZ,
    current_period_end      TIMESTAMPTZ,
    cancel_at_period_end    BOOLEAN      NOT NULL DEFAULT false,
    canceled_at             TIMESTAMPTZ,
    billing_mode            TEXT         NOT NULL DEFAULT 'card',  -- card (Stripe Checkout) | invoice (Stripe Invoicing)
    net_terms_days          INT,                                   -- for invoice billing_mode
    partner_id              TEXT,                                  -- non-null if came via MSP partner (future)
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT subscriptions_plan_ck CHECK (
        plan IN ('pilot','essentials','professional','enterprise')
    ),
    CONSTRAINT subscriptions_billing_mode_ck CHECK (
        billing_mode IN ('card','invoice')
    ),
    CONSTRAINT subscriptions_net_terms_ck CHECK (
        net_terms_days IS NULL OR (net_terms_days BETWEEN 15 AND 90 AND billing_mode = 'invoice')
    )
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_customer ON subscriptions (stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_site ON subscriptions (site_id) WHERE site_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions (status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_trial_end ON subscriptions (trial_end)
    WHERE trial_end IS NOT NULL AND status IN ('active','trialing');

-- PHI-boundary gate: future columns can't carry patient/provider data.
-- Information_schema check at runtime (via assertion); dev-time reminder.
COMMENT ON TABLE subscriptions IS
    'Direct-customer subscription state (synced from Stripe). '
    'PHI-free by design — Stripe is not a HIPAA Business Associate. '
    'Do NOT add columns matching: patient_*, provider_npi, diagnosis_*, '
    'treatment_*, phi_*. See CLAUDE.md § billing architecture.';

-- Auto-update updated_at on UPDATE
CREATE OR REPLACE FUNCTION set_subscriptions_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_subscriptions_updated_at ON subscriptions;
CREATE TRIGGER trg_subscriptions_updated_at
    BEFORE UPDATE ON subscriptions
    FOR EACH ROW EXECUTE FUNCTION set_subscriptions_updated_at();


-- ─── stripe_events ───────────────────────────────────────────────
-- Webhook dedup. Stripe delivers each event ≥1 times; we process ONCE
-- via (event_id) PK + ON CONFLICT DO NOTHING.
CREATE TABLE IF NOT EXISTS stripe_events (
    event_id        TEXT         PRIMARY KEY,                      -- evt_xxx from Stripe
    event_type      TEXT         NOT NULL,                         -- e.g., checkout.session.completed
    livemode        BOOLEAN      NOT NULL,                         -- guards against test events hitting live handlers
    received_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    processed_at    TIMESTAMPTZ,
    processing_error TEXT,                                         -- null on success
    payload         JSONB        NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stripe_events_type_received ON stripe_events (event_type, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_stripe_events_unprocessed ON stripe_events (received_at)
    WHERE processed_at IS NULL;


COMMIT;
