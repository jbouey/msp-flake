-- Migration 249: Bring site_credentials schema in line with the partner
-- UI code paths that have been dead-on-arrival since v1.
--
-- partners.py:3517 (POST /me/sites/{id}/credentials) and
-- partners.py:3578 (POST .../validate) reference columns that never
-- existed in prod: name, domain, username, password_encrypted,
-- is_primary, validation_status, last_validated_at, validation_details.
-- Every call would 500 with `column "X" of relation site_credentials
-- does not exist`. Caught by the SQL column-vs-schema linter on the
-- 2026-04-25 baseline-grind pass.
--
-- The existing 5 prod rows were created via a different path (admin /
-- fleet_cli) and use credential_name + encrypted_data. We keep those
-- in place; the new columns are additive and nullable, so old rows
-- stay valid.

ALTER TABLE site_credentials
    ADD COLUMN IF NOT EXISTS name TEXT,
    ADD COLUMN IF NOT EXISTS domain TEXT,
    ADD COLUMN IF NOT EXISTS username TEXT,
    ADD COLUMN IF NOT EXISTS password_encrypted BYTEA,
    ADD COLUMN IF NOT EXISTS is_primary BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS validation_status TEXT,
    ADD COLUMN IF NOT EXISTS last_validated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS validation_details JSONB;

-- Backfill `name` from credential_name for any pre-existing row, so a
-- partner viewing the credential list sees a non-null label.
UPDATE site_credentials
   SET name = credential_name
 WHERE name IS NULL
   AND credential_name IS NOT NULL;
