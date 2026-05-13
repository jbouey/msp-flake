-- Migration 311: vault_signing_key_versions registry table.
-- Vault Phase C P0 #2 + #5 + retro CHECK constraint.
-- Re-implementation after the 2026-05-12 revert chain.
--
-- Audit references:
--   .agent/plans/vault-p0-bundle-redesign-2026-05-13.md (design binding)
--   audit/coach-vault-p0-bundle-gate-a-redo-2-2026-05-13.md (Gate A APPROVE)
--   memory/feedback_vault_phase_c_revert_2026_05_12.md (revert case study)
--
-- Bootstrap pattern (called from INV-SIGNING-BACKEND-VAULT startup invariant):
--   1. First container observes Vault key version → INSERT … ON CONFLICT
--      DO NOTHING (P0 #3 — NEVER DO UPDATE; that's the prior side-effect).
--   2. Row enters with known_good=FALSE.
--   3. Operator runs SQL to set known_good=TRUE + approved_by + approved_at
--      — three columns must be set together (CHECK constraint).
--   4. Subsequent startups compare observed against the known_good row.
--      Mismatch → INV returns ok=False with DRIFT detail.
--
-- Global registry (NOT site-scoped). NOT site_id-bearing — does NOT
-- belong in `_rename_site_immutable_tables()` (lesson from mig 310).

BEGIN;

CREATE TABLE IF NOT EXISTS vault_signing_key_versions (
    id BIGSERIAL PRIMARY KEY,
    key_name TEXT NOT NULL,
    key_version INT NOT NULL,
    pubkey_hex TEXT NOT NULL,
    pubkey_b64 TEXT NOT NULL,
    first_observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- P0 #5: explicit NOT NULL DEFAULT FALSE. The CHECK constraint
    -- below requires this — NULL known_good would make NOT known_good
    -- evaluate UNKNOWN which CHECK treats as PASS, breaking the
    -- approval-pair invariant.
    known_good BOOLEAN NOT NULL DEFAULT FALSE,
    approved_by TEXT,                  -- operator email when known_good flipped TRUE
    approved_at TIMESTAMPTZ,           -- timestamp of approval
    UNIQUE (key_name, key_version),
    -- Retro Gate B P0 — approval-pair invariant. Prevents fast-track
    -- of unauthorized rotation: UPDATE … SET known_good=TRUE without
    -- supplying approved_by + approved_at would pass the immutable-
    -- column trigger but bypass the operator-approval ceremony.
    CONSTRAINT vault_signing_key_versions_approval_pair CHECK (
        NOT known_good OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_vault_signing_key_versions_known_good
    ON vault_signing_key_versions(key_name) WHERE known_good = TRUE;

-- Block UPDATE of immutable columns. Only known_good, approved_by,
-- approved_at, and last_observed_at may change after INSERT.
CREATE OR REPLACE FUNCTION vault_signing_key_versions_reject_immutable_update()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.key_name IS DISTINCT FROM NEW.key_name THEN
        RAISE EXCEPTION 'vault_signing_key_versions.key_name is immutable';
    END IF;
    IF OLD.key_version IS DISTINCT FROM NEW.key_version THEN
        RAISE EXCEPTION 'vault_signing_key_versions.key_version is immutable';
    END IF;
    IF OLD.pubkey_hex IS DISTINCT FROM NEW.pubkey_hex THEN
        RAISE EXCEPTION 'vault_signing_key_versions.pubkey_hex is immutable';
    END IF;
    IF OLD.pubkey_b64 IS DISTINCT FROM NEW.pubkey_b64 THEN
        RAISE EXCEPTION 'vault_signing_key_versions.pubkey_b64 is immutable';
    END IF;
    IF OLD.first_observed_at IS DISTINCT FROM NEW.first_observed_at THEN
        RAISE EXCEPTION 'vault_signing_key_versions.first_observed_at is immutable';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_vault_key_versions_reject_immutable_update ON vault_signing_key_versions;
CREATE TRIGGER trg_vault_key_versions_reject_immutable_update
    BEFORE UPDATE ON vault_signing_key_versions
    FOR EACH ROW EXECUTE FUNCTION vault_signing_key_versions_reject_immutable_update();

-- Block DELETE outright — history is forensic.
CREATE OR REPLACE FUNCTION vault_signing_key_versions_reject_delete()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'DELETE denied on vault_signing_key_versions — forensic key-rotation history is audit-class';
END;
$$;

DROP TRIGGER IF EXISTS trg_vault_key_versions_reject_delete ON vault_signing_key_versions;
CREATE TRIGGER trg_vault_key_versions_reject_delete
    BEFORE DELETE ON vault_signing_key_versions
    FOR EACH ROW EXECUTE FUNCTION vault_signing_key_versions_reject_delete();

COMMENT ON TABLE vault_signing_key_versions IS
    'Vault Transit key-version registry. INV-SIGNING-BACKEND-VAULT '
    'startup invariant compares observed key version against '
    'known_good=TRUE row; unauthorized rotation fails startup. '
    'Append-only by trigger; UPDATE limited to known_good/approved_*/'
    'last_observed_at; DELETE rejected. CHECK constraint enforces '
    'known_good=TRUE requires approved_by + approved_at together. '
    'Not site_id-bearing — does NOT belong in '
    '_rename_site_immutable_tables() (mig 310 lesson).';

COMMIT;

-- Audit log entry placed AFTER COMMIT (match mig 310 pattern per Gate A P1 carry).
INSERT INTO admin_audit_log (action, target, username, details, created_at)
VALUES (
    'migration_311_vault_signing_key_versions',
    'vault_signing_key_versions',
    'jeff',
    jsonb_build_object(
        'migration', '311',
        'reason', 'Vault Phase C Gate A redo-2 APPROVE-FOR-EXECUTION. Registry table feeds INV-SIGNING-BACKEND-VAULT startup invariant + signing_backend_drifted_from_vault substrate invariant. Bootstrap: ON CONFLICT DO NOTHING + known_good=FALSE; operator approves via SQL.',
        'audit_ref', 'audit/coach-vault-p0-bundle-gate-a-redo-2-2026-05-13.md',
        'design_doc', '.agent/plans/vault-p0-bundle-redesign-2026-05-13.md',
        'commit', 'commit 2 of 2 in Vault P0 re-implementation push',
        'lessons_from_revert', jsonb_build_array(
            'iter-1 ImportError closed by module-level imports + new CI gate',
            'iter-2 fixture column drift closed by 6 fixture updates + new CI gate',
            'iter-3 startup hang closed by asyncio.wait_for(5s) + lifespan eager-warm',
            'retro approval-pair CHECK closed by CHECK constraint above'
        )
    ),
    NOW()
);
