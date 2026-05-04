-- Migration 273: client_org_owner_transfer_requests + 1-owner-min trigger
--
-- Round-table 2026-05-04 (Camila/Brian/Linda/Steve/Adam) closure of
-- punch-list item #8 from the 5/4 ownership/email gaps audit. Owner
-- transfer was previously a hard blocker — no code path, no UI, no
-- audit. If a client_org owner's account was compromised or they left
-- the practice, the org was permanently locked (DB surgery only).
--
-- This migration ships the data layer for the two-step + cooling-off
-- + operator-visibility transfer flow:
--
--   pending_current_ack  →  current owner re-confirms (re-auth check)
--   pending_target_accept →  target user clicks email magic link
--   completed             →  cooling-off elapsed, role swap performed
--   canceled              →  either party (or any admin in-org) canceled
--   expired               →  expires_at passed without progression
--
-- Each transition writes a privileged-access attestation bundle
-- (Ed25519 + hash-chained + OTS-anchored) — six event_types added
-- to ALLOWED_EVENTS. NOT in PRIVILEGED_ORDER_TYPES or v_privileged_
-- types (admin-API class, not fleet_order — same asymmetry as
-- break_glass_passphrase_retrieval and fleet_healing_global_pause).

CREATE TABLE IF NOT EXISTS client_org_owner_transfer_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    client_org_id UUID NOT NULL REFERENCES client_orgs(id),
    initiated_by_user_id UUID NOT NULL REFERENCES client_users(id),

    -- Email of the proposed new owner. The accept-flow looks up
    -- (or creates, if first time) a client_users row keyed on this
    -- email. Same-org-only — cross-org ownership transfer is a
    -- different feature class entirely.
    target_email TEXT NOT NULL,
    target_user_id UUID REFERENCES client_users(id),

    status TEXT NOT NULL DEFAULT 'pending_current_ack'
        CHECK (status IN (
            'pending_current_ack',
            'pending_target_accept',
            'completed',
            'canceled',
            'expired'
        )),

    -- Reason from the initiator. Same friction as the rest of the
    -- privileged-access chain: ≥20 chars enforced application-side.
    reason TEXT NOT NULL,

    -- Magic-link token sent to target_email. Stored as SHA256 hash
    -- — never plaintext.
    accept_token_hash TEXT,

    -- Lifecycle timestamps
    current_ack_at TIMESTAMP WITH TIME ZONE,
    target_accept_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    canceled_at TIMESTAMP WITH TIME ZONE,
    canceled_by TEXT,
    cancel_reason TEXT,

    -- 7-day default expiry (matches client_invites pattern).
    -- Application sets this; CHECK enforces non-null.
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,

    -- 24-hour default cooling-off after target_accept_at. Either
    -- party (or any in-org admin) can cancel during this window.
    -- Application sets this; CHECK enforces non-null.
    cooling_off_until TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Array of attestation_bundle_ids — one per state transition.
    -- Auditor kit pulls this and walks the chain.
    attestation_bundle_ids JSONB NOT NULL DEFAULT '[]'::jsonb,

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Steve's friction: ONE active transfer per org at any time.
-- Re-initiation requires canceling/expiring the existing.
CREATE UNIQUE INDEX IF NOT EXISTS idx_owner_transfer_one_pending_per_org
    ON client_org_owner_transfer_requests (client_org_id)
    WHERE status IN ('pending_current_ack', 'pending_target_accept');

-- Triage / dashboard queries
CREATE INDEX IF NOT EXISTS idx_owner_transfer_status
    ON client_org_owner_transfer_requests (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_owner_transfer_target_email
    ON client_org_owner_transfer_requests (LOWER(target_email))
    WHERE status = 'pending_target_accept';

-- Audit-class table: append-only. UPDATE only allowed via the
-- application-layer state machine; DELETE blocked.
CREATE OR REPLACE FUNCTION prevent_owner_transfer_deletion()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'client_org_owner_transfer_requests is append-only audit-class. '
        'DELETE blocked. Use status=canceled or status=expired instead.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_owner_transfer_deletion
    ON client_org_owner_transfer_requests;
CREATE TRIGGER trg_prevent_owner_transfer_deletion
    BEFORE DELETE ON client_org_owner_transfer_requests
    FOR EACH ROW EXECUTE FUNCTION prevent_owner_transfer_deletion();


-- ─────────────────────────────────────────────────────────────────
-- Brian's non-negotiable: 1-owner-minimum invariant on client_users.
-- This is the last line of defense if application logic ever ships a
-- bug that would take an org to zero owners. Application logic SHOULD
-- never permit it; the trigger ensures it CANNOT.
-- ─────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION enforce_min_one_owner_per_org()
RETURNS TRIGGER AS $$
DECLARE
    v_remaining_owners INT;
BEGIN
    -- Only relevant when the row's pre-image was an active owner
    -- AND the operation is removing/demoting/deactivating that owner.
    IF TG_OP = 'DELETE' AND OLD.role = 'owner' AND OLD.is_active = true THEN
        SELECT COUNT(*) INTO v_remaining_owners
            FROM client_users
            WHERE client_org_id = OLD.client_org_id
              AND id <> OLD.id
              AND role = 'owner'
              AND is_active = true;
        IF v_remaining_owners = 0 THEN
            RAISE EXCEPTION
                'cannot remove last active owner of client_org %; '
                'transfer ownership first via /api/client/users/'
                'owner-transfer/initiate', OLD.client_org_id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF TG_OP = 'UPDATE' AND OLD.role = 'owner' AND OLD.is_active = true
          AND (NEW.role <> 'owner' OR NEW.is_active = false) THEN
        SELECT COUNT(*) INTO v_remaining_owners
            FROM client_users
            WHERE client_org_id = OLD.client_org_id
              AND id <> OLD.id
              AND role = 'owner'
              AND is_active = true;
        IF v_remaining_owners = 0 THEN
            RAISE EXCEPTION
                'cannot demote/deactivate last active owner of '
                'client_org %; transfer ownership first via '
                '/api/client/users/owner-transfer/initiate',
                OLD.client_org_id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_enforce_min_one_owner_per_org
    ON client_users;
CREATE TRIGGER trg_enforce_min_one_owner_per_org
    BEFORE UPDATE OR DELETE ON client_users
    FOR EACH ROW EXECUTE FUNCTION enforce_min_one_owner_per_org();


COMMENT ON TABLE client_org_owner_transfer_requests IS
'Append-only ledger of owner-transfer state machine transitions. '
'Each transition (initiate/ack/accept/complete/cancel/expire) writes '
'an Ed25519 attestation bundle whose ID is appended to '
'attestation_bundle_ids. Auditor kit walks this chain.';

COMMENT ON FUNCTION enforce_min_one_owner_per_org() IS
'Last-line defense: ensures every client_org always has ≥1 active '
'owner. Application logic should not permit zero-owner state, but '
'this trigger is the database-level invariant that catches bugs. '
'Per Brian round-table 2026-05-04: non-negotiable belt-and-suspenders.';
