-- Migration 328 — schema-level attestation binding for vault_signing_key_versions
--
-- #116 Phase 1 closure per audit/coach-116-vault-admin-approval-
-- gate-a-2026-05-17.md (Gate A P0-1). Adds attestation_bundle_id
-- TEXT NULL column + extends the existing known_good CHECK to
-- require an attestation when known_good=TRUE.
--
-- Why schema-level (vs application-level only): mirrors the mig 175
-- pattern for fleet_orders → enforce_privileged_order_attestation.
-- Even if a future code path (or direct psql) flips known_good=TRUE
-- bypassing the admin endpoint, the CHECK constraint refuses the
-- UPDATE without an attestation_bundle_id. Belt + suspenders.
--
-- Anchor namespace: the attestation bundle for vault key-version
-- approvals uses synthetic site_id 'vault:<key_name>:v<key_version>'
-- per Gate A P0-3 (mirrors Session 216 'partner_org:<id>' +
-- 'client_org:<id>' synthetic anchors). The attestation lives in
-- compliance_bundles with check_type='privileged_access' but is
-- NOT bound by mig 175's trigger (which gates fleet_orders only,
-- not direct table writes).
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + DROP CONSTRAINT IF EXISTS
-- + ADD CONSTRAINT.
--
-- Phase 2 (future task — out of scope for #116): the substrate
-- invariant `vault_key_version_approved_without_attestation` (sev1,
-- this commit) scans for any row where known_good=TRUE but
-- attestation_bundle_id is NULL OR doesn't reference a real
-- compliance_bundles row. The CHECK constraint here closes the
-- NULL case at the DB; the invariant closes the dangling-reference
-- case at runtime.

BEGIN;

ALTER TABLE vault_signing_key_versions
    ADD COLUMN IF NOT EXISTS attestation_bundle_id TEXT NULL;

COMMENT ON COLUMN vault_signing_key_versions.attestation_bundle_id IS
    'Task #116 (mig 328). When known_good=TRUE, references the '
    'compliance_bundles row (check_type=''privileged_access'', '
    'site_id=''vault:<key_name>:v<key_version>'') containing the '
    'Ed25519-signed approval attestation. Enforced by the extended '
    'known_good CHECK + runtime substrate invariant '
    'vault_key_version_approved_without_attestation (sev1).';

-- Drop the old CHECK (approved_by + approved_at only) and replace
-- with the strengthened one that also requires attestation_bundle_id.
-- Mig 311 named the constraint `vault_signing_key_versions_known_good_ck`.
ALTER TABLE vault_signing_key_versions
    DROP CONSTRAINT IF EXISTS vault_signing_key_versions_known_good_ck;

ALTER TABLE vault_signing_key_versions
    ADD CONSTRAINT vault_signing_key_versions_known_good_ck CHECK (
        NOT known_good OR (
            approved_by IS NOT NULL
            AND approved_at IS NOT NULL
            AND attestation_bundle_id IS NOT NULL
        )
    );

COMMIT;
