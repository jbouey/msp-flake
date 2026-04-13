-- Migration 174: Privileged-access request queue (Phase 14 T1)
--
-- Implements the second-approval workflow the round table mandated:
-- a privileged-access request is initiated by a named partner admin,
-- can be configured to require a second approver (partner + client, or
-- partner admin + partner admin), and only after the full approval
-- chain is complete is the fleet order signed.
--
-- Every state change on a request is attested — the request table
-- itself is only the coordination surface; the authoritative record
-- lives in compliance_bundles (check_type='privileged_access') with
-- cumulative approval rosters.
--
-- Client-side RBAC integration: per-site or per-org, a client admin
-- can require their approval before emergency access to their own
-- infrastructure. Consent-first accountability.

BEGIN;

-- ─── Request queue ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS privileged_access_requests (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id             VARCHAR(100) NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    event_type          VARCHAR(50)  NOT NULL,
    initiator_email     VARCHAR(255) NOT NULL,
    initiator_role      VARCHAR(32)  NOT NULL,
                                    -- 'partner_admin' | 'partner_tech' | 'ops'
    reason              TEXT         NOT NULL,
    duration_minutes    INTEGER,
    requested_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ  NOT NULL,
                                    -- request stale + auto-rejected after this

    -- Approval chain (up to 2 approvers)
    partner_approver_email       VARCHAR(255),
    partner_approver_at          TIMESTAMPTZ,
    partner_approver_role        VARCHAR(32),
    client_approver_email        VARCHAR(255),
    client_approver_at           TIMESTAMPTZ,
    client_approver_role         VARCHAR(32),   -- 'client_admin'

    -- Terminal state
    status              VARCHAR(16)  NOT NULL DEFAULT 'pending',
                                -- 'pending'   = awaiting approvals
                                -- 'approved'  = fully approved, fleet order issued
                                -- 'rejected'  = explicit deny
                                -- 'expired'   = aged out
                                -- 'revoked'   = disabled mid-flight
    rejected_by         VARCHAR(255),
    rejection_reason    TEXT,

    -- Linkage
    fleet_order_id      UUID,
    attestation_bundle_id VARCHAR(50)
                                -- the compliance_bundles row recording this
);

CREATE INDEX IF NOT EXISTS idx_priv_req_status
    ON privileged_access_requests (status, expires_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_priv_req_site
    ON privileged_access_requests (site_id, requested_at DESC);

-- ─── Per-site consent config ──────────────────────────────────────
-- A client admin can require their approval before privileged access.
-- Defaults to FALSE (partner-only) for backward compat with existing
-- customers. Enterprise tier customers can flip it on via portal.
CREATE TABLE IF NOT EXISTS privileged_access_consent_config (
    site_id                           VARCHAR(100) PRIMARY KEY REFERENCES sites(site_id) ON DELETE CASCADE,
    client_approval_required          BOOLEAN     NOT NULL DEFAULT false,
    approval_timeout_minutes          INTEGER     NOT NULL DEFAULT 30,
    -- Emergency bypass: if the client admin is unreachable during a
    -- genuine incident, partner admin can invoke with RETROACTIVE
    -- client notice. Bounded by the fields below.
    emergency_bypass_allowed          BOOLEAN     NOT NULL DEFAULT true,
    emergency_bypass_max_per_month    INTEGER     NOT NULL DEFAULT 1,
    notify_client_emails              TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[],
    updated_at                        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by                        VARCHAR(255)
);

-- Everything below is append-only audit for the request state machine.
-- Separate from compliance_bundles — this is the operational queue;
-- compliance_bundles remains the cryptographic ledger.

COMMIT;
