-- Migration 287: Privacy Officer designations (F2, round-table 2026-05-06)
--
-- The Compliance Attestation Letter (F1) pulls the Privacy Officer name
-- from a SIGNED ACCEPTANCE attestation, not from a profile field. This
-- is load-bearing per the customer round-table (Janet office-manager
-- persona): "if you're going to print my name on a federal-looking
-- document I need a checkbox at signup that says 'Janet Walsh accepts
-- Privacy Officer designation, here's the explainer of what that means.'"
--
-- §164.308(a)(2) requires the CE to "identify the security official
-- who is responsible for the development and implementation of the
-- policies and procedures." A profile field doesn't satisfy that —
-- a dated, accepted, attested designation does.
--
-- Schema:
--   - One ACTIVE designation per client_org at a time (enforced by
--     partial unique index on revoked_at IS NULL).
--   - Replacement = revoke old + insert new (transactional).
--   - Every designation event writes a chain-anchored Ed25519
--     attestation bundle (privileged_access_attestation kind).
--     `attestation_bundle_id` records the link.
--   - `explainer_version` records WHICH version of the §164.308(a)(2)
--     explainer the designee accepted — so a future explainer revision
--     doesn't retroactively invalidate prior acceptances.
--   - IP + user-agent recorded for evidence-quality forensics.
--
-- Carol-approved sign-off contract: a designation row where
-- `revoked_at IS NULL` is the *current* Privacy Officer. The Letter
-- pulls (name, title, accepted_at, explainer_version, attestation_
-- bundle_id) and embeds the bundle_id in the rendered PDF for
-- /verify lookup.

BEGIN;

CREATE TABLE IF NOT EXISTS privacy_officer_designations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_org_id UUID NOT NULL REFERENCES client_orgs(id) ON DELETE RESTRICT,

    -- The designated person
    name TEXT NOT NULL,
    title TEXT NOT NULL,
    email TEXT NOT NULL,

    -- Acceptance evidence — who clicked the checkbox + when + UA + IP
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    accepting_user_id UUID NOT NULL REFERENCES client_users(id) ON DELETE RESTRICT,
    accepting_user_email TEXT NOT NULL,
    ip_address INET,
    user_agent TEXT,

    -- The §164.308(a)(2) explainer text version the designee accepted.
    -- Future explainer revisions bump this; old acceptances retain
    -- their original explainer_version reference. NEVER rewrite history.
    explainer_version TEXT NOT NULL,

    -- Cryptographic binding — link to the chain-anchored Ed25519
    -- attestation bundle written at designation time.
    attestation_bundle_id UUID,

    -- Revocation (replacement). NULL = currently active.
    revoked_at TIMESTAMPTZ,
    revoked_by_user_id UUID REFERENCES client_users(id) ON DELETE RESTRICT,
    revoked_by_email TEXT,
    revoked_reason TEXT,
    revoked_attestation_bundle_id UUID,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Sanity invariant: revocation fields land together.
    CONSTRAINT po_revocation_fields_consistent
        CHECK (
            (revoked_at IS NULL AND revoked_by_user_id IS NULL
                AND revoked_by_email IS NULL AND revoked_reason IS NULL)
            OR
            (revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL
                AND revoked_by_email IS NOT NULL AND revoked_reason IS NOT NULL)
        ),

    -- Reason length matches the privileged-access ≥20-char convention.
    CONSTRAINT po_revoked_reason_minlen
        CHECK (revoked_reason IS NULL OR LENGTH(revoked_reason) >= 20),

    -- Email shape — basic sanity, not full RFC.
    CONSTRAINT po_email_shape CHECK (email ~ '^[^@]+@[^@]+\.[^@]+$'),
    CONSTRAINT po_accepting_email_shape CHECK (accepting_user_email ~ '^[^@]+@[^@]+\.[^@]+$')
);

-- One ACTIVE designation per org at a time. Replacement = revoke + insert.
CREATE UNIQUE INDEX IF NOT EXISTS idx_po_designations_org_active
    ON privacy_officer_designations(client_org_id)
    WHERE revoked_at IS NULL;

-- Lookup paths
CREATE INDEX IF NOT EXISTS idx_po_designations_org
    ON privacy_officer_designations(client_org_id, accepted_at DESC);
CREATE INDEX IF NOT EXISTS idx_po_designations_attestation
    ON privacy_officer_designations(attestation_bundle_id)
    WHERE attestation_bundle_id IS NOT NULL;

-- RLS — designations are tenant-scoped; follow the existing
-- client_orgs / sites org-isolation pattern.
ALTER TABLE privacy_officer_designations ENABLE ROW LEVEL SECURITY;

-- privacy_officer_designations is client_org-scoped (NOT site-scoped),
-- so use the canonical org-isolation predicate (matches mig 085, 087,
-- 100). client_orgs.id::text equality with the current_org setting is
-- the standard tenant-isolation shape for org-keyed tables.
CREATE POLICY tenant_org_isolation ON privacy_officer_designations
    FOR ALL
    USING (
        client_org_id::text = current_setting('app.current_org', true)
    );

-- Admin override (admin users see everything for support / forensics).
CREATE POLICY admin_full_access ON privacy_officer_designations
    FOR ALL
    USING (
        current_setting('app.is_admin', true) = 'true'
    );

COMMIT;
