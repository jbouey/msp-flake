-- Migration 000a: Legacy `sites` table bootstrap.
--
-- Migration 001_portal_tables FK's to sites(site_id), but the prod
-- `sites` table was created BEFORE the migration ledger existed —
-- there's no migration that creates it. Production already has it,
-- but a fresh CI Postgres has no `sites` table and migration 001
-- aborts with `relation "sites" does not exist`. This kept the
-- test_lifespan_pg_smoke (#154) gate self-skipping.
--
-- This migration creates a MINIMAL stub of `sites` — only the columns
-- migrations 001-N FK against (site_id, plus the trivial id PK so the
-- table is valid). It's idempotent (CREATE TABLE IF NOT EXISTS) so on
-- prod where the table already has the full 60+ columns, it's a no-op.
-- Subsequent migrations ALTER TABLE … ADD COLUMN IF NOT EXISTS the
-- richer columns the application needs, so a fresh CI Postgres ends
-- up with the same shape after `cmd_up` finishes.
--
-- Numbering: `000a` sorts after `000_schema_migrations` and before
-- `001_portal_tables` (the migrate.py regex was extended to match
-- `\d{3}[a-z]?` so this filename is recognised as version "000a").

CREATE TABLE IF NOT EXISTS sites (
    -- Primary key — uuid_generate_v4 may not exist yet on a fresh
    -- DB (uuid-ossp extension not installed), so use gen_random_uuid
    -- which is built-in on PG 13+. Prod's row uses a different
    -- default but the column type matches.
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The FK target. UNIQUE so foreign keys can reference it.
    -- Prod uses VARCHAR(50); preserved here.
    site_id VARCHAR(50) NOT NULL UNIQUE,

    -- Minimum required NOT NULL columns prod has — without these,
    -- INSERTs from app code fail. Any default value the app expects
    -- to be auto-populated is fine here; richer columns get added
    -- by later migrations via ADD COLUMN IF NOT EXISTS.
    clinic_name VARCHAR(255) NOT NULL DEFAULT '',
    client_org_id UUID,

    -- Migration 067 (org_site_wiring) INSERTs into (site_id,
    -- clinic_name, status, onboarding_stage). Without these two
    -- columns being present at fresh-CI bootstrap time, 067 aborts
    -- with `column "status" of relation "sites" does not exist`.
    -- They are nullable here (no defaults) — 067 supplies values for
    -- new rows. Subsequent migrations may ALTER constraints; we
    -- intentionally don't over-specify shape here.
    status VARCHAR(50),
    onboarding_stage VARCHAR(50),

    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- The actual FK reference shape migrations 001+ use is sites(site_id);
-- having the UNIQUE on site_id above satisfies that constraint.
--
-- Note: apply_migration in migrate.py writes the schema_migrations
-- ledger row with the REAL checksum after this body runs. We do NOT
-- write our own row here — that would lock the checksum to 'initial'
-- and produce a mismatch warning on every prod startup.
--
-- HOW TO EXTEND THIS STUB:
-- If a fresh-CI `cmd_up()` fails with
--   `column "X" does not exist of relation "sites"`
-- it means a future migration references a legacy `sites` column
-- that this stub doesn't include. Add it as a nullable column above
-- (no default unless the app code actually relies on one). Don't
-- copy the prod column shape verbatim — only what the migration
-- ledger actually needs. The point of this file is "minimum surface,"
-- not "prod schema mirror."

-- DOWN
-- DROP TABLE IF EXISTS sites; -- only safe if the prod table is empty
