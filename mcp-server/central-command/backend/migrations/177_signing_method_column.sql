-- Migration 177: fleet_orders.signing_method (Phase 14 T3 prep)
--
-- Forward-compatible column for the HSM migration. Every order today
-- is signed by the on-disk signing.key ('file'). When we migrate to
-- AWS KMS / Google Cloud HSM / Yubikey-mediated signing, new orders
-- write 'kms', 'cloud_hsm', or 'yubikey' with the responsible key's
-- fingerprint in signing_key_fingerprint.
--
-- Adds notified_at to compliance_bundles so the notifier background
-- task (Phase 14 T2) can mark events as notified without re-sending.

BEGIN;

ALTER TABLE fleet_orders
    ADD COLUMN IF NOT EXISTS signing_method          VARCHAR(16)
        NOT NULL DEFAULT 'file',
    ADD COLUMN IF NOT EXISTS signing_key_fingerprint VARCHAR(16);

CREATE INDEX IF NOT EXISTS idx_fleet_orders_signing_method
    ON fleet_orders (signing_method)
    WHERE signing_method <> 'file';

-- Notifier state on compliance_bundles. NULL = pending notification;
-- non-null = notified (idempotent — if notifier retries, UPDATE SKIP
-- LOCKED ensures no duplicate sends).
ALTER TABLE compliance_bundles
    ADD COLUMN IF NOT EXISTS notified_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_compliance_bundles_priv_unnotified
    ON compliance_bundles (checked_at)
    WHERE check_type = 'privileged_access' AND notified_at IS NULL;

-- Policy acknowledgment of current state
COMMENT ON COLUMN fleet_orders.signing_method IS
    'Session 205 Phase 14 T3 prep. Today always ''file'' '
    '(on-disk signing.key). Future: ''kms''/''cloud_hsm''/''yubikey''.';

COMMIT;
