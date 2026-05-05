-- Migration 274: partner_admin_transfer_requests + 1-admin-min trigger
--
-- Maya round-table 2026-05-04 (post partner-vs-client consistency
-- audit). Closes the partner-side analog of mig 273's owner-transfer
-- gap: pre-fix, if a partner_org's admin was compromised or departed,
-- DB surgery was the only recovery path.
--
-- Per Maya: shape DIFFERS from the client-side state machine because
-- partners are operators (per feedback_non_operator_partner_posture.md).
-- Operator-class flows tolerate less friction:
--   - 2-step instead of 3-step (no ack stage — single confirm_phrase
--     covers the anti-misclick concern)
--   - NO 24h cooling-off (operators need fast incident response)
--   - NO target-accept-via-magic-link (partners use OAuth/SSO; target
--     re-authenticates in their own existing session)
--   - NO target-creation flow (target must be an existing partner_user
--     in the same partner_org with role!=admin already)
-- What stays the same:
--   - reason ≥20ch
--   - Ed25519 attestation per state transition
--   - operator-visibility email on every event
--   - 1-admin-min DB trigger (Brian's non-negotiable holds — schema-
--     level last-line defense against zero-admin state)
--   - any-current-admin-can-cancel (Steve P3 lateral defense)

CREATE TABLE IF NOT EXISTS partner_admin_transfer_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    partner_id UUID NOT NULL REFERENCES partners(id),
    initiated_by_user_id UUID NOT NULL REFERENCES partner_users(id),

    -- Email of the existing partner_user proposed as new admin.
    -- The target MUST already be a partner_user in this same partner
    -- with role!=admin (validated at endpoint layer).
    target_email TEXT NOT NULL,
    target_user_id UUID REFERENCES partner_users(id),

    status TEXT NOT NULL DEFAULT 'pending_target_accept'
        CHECK (status IN (
            'pending_target_accept',
            'completed',
            'canceled',
            'expired'
        )),

    reason TEXT NOT NULL,

    completed_at TIMESTAMP WITH TIME ZONE,
    canceled_at TIMESTAMP WITH TIME ZONE,
    canceled_by TEXT,
    cancel_reason TEXT,

    -- 7-day expiry (matches client side + partner_invites pattern).
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Array of attestation_bundle_ids — one per state transition.
    attestation_bundle_ids JSONB NOT NULL DEFAULT '[]'::jsonb,

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- ONE active transfer per partner at a time.
CREATE UNIQUE INDEX IF NOT EXISTS idx_partner_admin_transfer_one_pending
    ON partner_admin_transfer_requests (partner_id)
    WHERE status = 'pending_target_accept';

-- Triage / dashboard queries
CREATE INDEX IF NOT EXISTS idx_partner_admin_transfer_status
    ON partner_admin_transfer_requests (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_partner_admin_transfer_target_email
    ON partner_admin_transfer_requests (LOWER(target_email))
    WHERE status = 'pending_target_accept';

-- Audit-class table: append-only.
CREATE OR REPLACE FUNCTION prevent_partner_admin_transfer_deletion()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'partner_admin_transfer_requests is append-only audit-class. '
        'DELETE blocked. Use status=canceled or status=expired instead.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_partner_admin_transfer_deletion
    ON partner_admin_transfer_requests;
CREATE TRIGGER trg_prevent_partner_admin_transfer_deletion
    BEFORE DELETE ON partner_admin_transfer_requests
    FOR EACH ROW EXECUTE FUNCTION prevent_partner_admin_transfer_deletion();


-- ─────────────────────────────────────────────────────────────────
-- Brian's non-negotiable, partner-side: 1-admin-min invariant on
-- partner_users. Mirrors enforce_min_one_owner_per_org from mig 273
-- but operates on partner_users.role + partner_id.
-- ─────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION enforce_min_one_admin_per_partner()
RETURNS TRIGGER AS $$
DECLARE
    v_remaining_admins INT;
BEGIN
    -- DELETE of an active admin
    IF TG_OP = 'DELETE' AND OLD.role = 'admin'
        AND OLD.status = 'active' THEN
        SELECT COUNT(*) INTO v_remaining_admins
            FROM partner_users
            WHERE partner_id = OLD.partner_id
              AND id <> OLD.id
              AND role = 'admin'
              AND status = 'active';
        IF v_remaining_admins = 0 THEN
            RAISE EXCEPTION
                'cannot remove last active admin of partner %; '
                'transfer admin role first via /api/partners/me/'
                'admin-transfer/initiate', OLD.partner_id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    -- UPDATE that demotes/disables the only active admin
    ELSIF TG_OP = 'UPDATE' AND OLD.role = 'admin'
          AND OLD.status = 'active'
          AND (NEW.role <> 'admin' OR NEW.status <> 'active') THEN
        SELECT COUNT(*) INTO v_remaining_admins
            FROM partner_users
            WHERE partner_id = OLD.partner_id
              AND id <> OLD.id
              AND role = 'admin'
              AND status = 'active';
        IF v_remaining_admins = 0 THEN
            RAISE EXCEPTION
                'cannot demote/disable last active admin of '
                'partner %; transfer admin role first via '
                '/api/partners/me/admin-transfer/initiate',
                OLD.partner_id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_enforce_min_one_admin_per_partner
    ON partner_users;
CREATE TRIGGER trg_enforce_min_one_admin_per_partner
    BEFORE UPDATE OR DELETE ON partner_users
    FOR EACH ROW EXECUTE FUNCTION enforce_min_one_admin_per_partner();


COMMENT ON TABLE partner_admin_transfer_requests IS
'Append-only ledger of partner-admin transfer state machine '
'transitions. 4 states: pending_target_accept, completed, canceled, '
'expired. Each transition writes an Ed25519 attestation bundle. '
'Simpler shape than client_org_owner_transfer_requests (mig 273) '
'because partners are operators — no cooling-off, no magic-link, '
'no target-creation. Round-table 2026-05-04 Maya APPROVE.';

COMMENT ON FUNCTION enforce_min_one_admin_per_partner() IS
'Mirror of enforce_min_one_owner_per_org (mig 273) for partner_users. '
'Last-line defense against zero-admin partner state. If application '
'logic ships a bug that would demote/delete the only admin, this '
'trigger fires. Per Brian round-table 2026-05-04: non-negotiable.';
