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
--   `column "X" does not exist of relation "<table>"`
-- it means a future migration references a legacy column that this
-- stub doesn't include. Add it as a nullable column above (no default
-- unless app code actually relies on one). Don't copy the prod
-- column shape verbatim — only what the migration ledger needs.
-- The point of this file is "minimum surface," not "prod schema mirror."

-- ============================================================================
-- runbooks: legacy (created outside the migration ledger; prod has UUID id +
-- runbook_id UNIQUE + steps JSONB; migration 005's CREATE TABLE IF NOT EXISTS
-- uses VARCHAR id; later migrations 010/027/051/076/090 INSERT (runbook_id,
-- ..., steps) which 005's poorer schema can't accept on a fresh CI Postgres).
-- ============================================================================
CREATE TABLE IF NOT EXISTS runbooks (
    -- 005 inserts string ids like 'RB-WIN-PATCH-001'; later migrations
    -- don't pass id at all and rely on a default. Use VARCHAR with a
    -- gen_random_uuid()::text default so BOTH paths work.
    id VARCHAR(50) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    -- ON CONFLICT (runbook_id) target for migrations 010+.
    runbook_id VARCHAR(100) UNIQUE,
    name VARCHAR(255) NOT NULL DEFAULT '',
    description TEXT,
    category VARCHAR(100),
    check_type VARCHAR(100),
    severity VARCHAR(50) DEFAULT 'medium',
    is_disruptive BOOLEAN DEFAULT FALSE,
    requires_maintenance_window BOOLEAN DEFAULT FALSE,
    hipaa_controls TEXT[],
    steps JSONB NOT NULL DEFAULT '[]'::jsonb,
    parameters_schema JSONB,
    enabled BOOLEAN DEFAULT TRUE,
    version VARCHAR(50) DEFAULT '1.0',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- control_runbook_mapping: legacy (no migration creates it; prod has it +
-- migration 086 INSERTs 160 mapping rows). Without this stub, migration 086
-- aborts on fresh CI with `relation "control_runbook_mapping" does not exist`.
-- ============================================================================
CREATE TABLE IF NOT EXISTS control_runbook_mapping (
    id BIGSERIAL PRIMARY KEY,
    framework_id TEXT NOT NULL,
    control_id TEXT NOT NULL,
    runbook_id TEXT NOT NULL,
    is_primary BOOLEAN DEFAULT TRUE,
    UNIQUE (framework_id, control_id, runbook_id)
);

-- DOWN
-- DROP TABLE IF EXISTS sites; -- only safe if the prod table is empty
