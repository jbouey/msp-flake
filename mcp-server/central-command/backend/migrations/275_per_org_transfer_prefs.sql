-- Migration 275: per-org configurable cooling-off / expiry on
-- owner+admin transfers
--
-- Followup task #20 from session-end punch list. Today the cooling-off
-- + expiry windows are hardcoded constants:
--   client_owner_transfer.DEFAULT_COOLING_OFF_HOURS = 24
--   client_owner_transfer.DEFAULT_EXPIRY_DAYS = 7
--   partner_admin_transfer.DEFAULT_COOLING_OFF_HOURS = 0  (Maya design)
--   partner_admin_transfer.DEFAULT_EXPIRY_DAYS = 7
--
-- Some practices want longer cooling-off (multi-physician partnerships
-- with deliberate process); some want 4h (small clinics where
-- ownership transfer is routine). Same for partners: 7d may be too
-- long for incident response on a partner-admin compromise.
--
-- Schema-additive change. CHECK constraints enforce reasonable bounds:
--   cooling_off_hours: 0..168 (0 = immediate accept = current partner
--                              default; max 1 week)
--   expiry_days:       1..30  (max 30 days; HIPAA evidence freshness
--                              concern beyond that)
--
-- Defaults preserve the Maya-approved status quo:
--   client_orgs.transfer_cooling_off_hours DEFAULT 24
--   partners.transfer_cooling_off_hours DEFAULT 0   (operator class)
--   both tables.transfer_expiry_days DEFAULT 7

ALTER TABLE client_orgs
    ADD COLUMN IF NOT EXISTS transfer_cooling_off_hours INT
        NOT NULL DEFAULT 24,
    ADD COLUMN IF NOT EXISTS transfer_expiry_days INT
        NOT NULL DEFAULT 7;

ALTER TABLE partners
    ADD COLUMN IF NOT EXISTS transfer_cooling_off_hours INT
        NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS transfer_expiry_days INT
        NOT NULL DEFAULT 7;

-- CHECK constraints. Idempotent via DO block (CREATE CONSTRAINT IF
-- NOT EXISTS doesn't exist in PG; this pattern is the canonical
-- equivalent — used in mig 192 row-guard precedent).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'chk_client_orgs_transfer_cooling_off'
    ) THEN
        ALTER TABLE client_orgs
            ADD CONSTRAINT chk_client_orgs_transfer_cooling_off
            CHECK (transfer_cooling_off_hours BETWEEN 0 AND 168);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'chk_client_orgs_transfer_expiry'
    ) THEN
        ALTER TABLE client_orgs
            ADD CONSTRAINT chk_client_orgs_transfer_expiry
            CHECK (transfer_expiry_days BETWEEN 1 AND 30);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'chk_partners_transfer_cooling_off'
    ) THEN
        ALTER TABLE partners
            ADD CONSTRAINT chk_partners_transfer_cooling_off
            CHECK (transfer_cooling_off_hours BETWEEN 0 AND 168);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'chk_partners_transfer_expiry'
    ) THEN
        ALTER TABLE partners
            ADD CONSTRAINT chk_partners_transfer_expiry
            CHECK (transfer_expiry_days BETWEEN 1 AND 30);
    END IF;
END $$;

COMMENT ON COLUMN client_orgs.transfer_cooling_off_hours IS
'Hours between target accepting ownership transfer and the role swap '
'completing. Default 24 (Maya client-class design). Range 0..168. '
'Set via PUT /api/client/users/transfer-prefs (owner-only, attested).';

COMMENT ON COLUMN client_orgs.transfer_expiry_days IS
'Days before a pending owner-transfer auto-expires without progression. '
'Default 7 (matches client_invites pattern). Range 1..30.';

COMMENT ON COLUMN partners.transfer_cooling_off_hours IS
'Hours between target accepting partner-admin transfer and the role '
'swap completing. Default 0 (Maya operator-class design — operators '
'need fast incident response). Range 0..168. Set via PUT '
'/api/partners/me/admin-transfer-prefs (admin-only, attested).';

COMMENT ON COLUMN partners.transfer_expiry_days IS
'Days before a pending partner-admin transfer auto-expires without '
'acceptance. Default 7. Range 1..30.';
