-- Migration 244 — Session 210-B 2026-04-25 hardening #3
--
-- Add UNIQUE constraints and GC primitives for tables that today suffer
-- from accumulating duplicates / stale rows. These bugs HID UNDER
-- correct-looking code today: discovered_devices_freshness fired 4
-- spurious violations because each MAC had multiple stale-vs-fresh rows
-- in discovered_devices, with no UNIQUE(MAC, site_id) preventing them.
--
-- Three constraints added:
--   1. discovered_devices: UNIQUE on (LOWER(mac_address), site_id)
--      — the canonical dedup key the device-sync code already
--      effectively assumes. Backfill collapses pre-existing dups by
--      keeping MAX(last_seen_at) per group.
--   2. install_sessions: pre-emptive GC function for rows older than
--      30 days (currently no GC — manual cleanup only).
--   3. nonces: pre-emptive GC function. Already uses TTL via the
--      `created_at > NOW() - $TTL` filter on read but rows accumulate
--      forever otherwise.
--
-- Each GC function is callable from a background_tasks loop or a cron
-- job. We add the FUNCTION here but don't schedule it from this
-- migration — scheduler wiring lives in Python.

BEGIN;

-- ---------------------------------------------------------------------
-- 1. discovered_devices UNIQUE constraint
-- ---------------------------------------------------------------------
-- The application code at sites.py and device_sync.py treats each
-- (mac, site_id) as a single record (UPSERT semantics). The schema
-- never enforced that — INSERT-then-UPDATE flows ended up writing
-- multiple rows for the same MAC over time, especially when site_id
-- got renamed during cleanup or the appliance bounced between sites.
--
-- We collapse pre-existing duplicates by keeping the row with the
-- newest last_seen_at per (LOWER(mac), site_id). Older rows are
-- copied to discovered_devices_archive then deleted from the main
-- table. Archive table is plain (no constraints) so the data is
-- preserved for audit.

CREATE TABLE IF NOT EXISTS discovered_devices_archive (
    LIKE discovered_devices INCLUDING DEFAULTS,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archive_reason TEXT NOT NULL
);

DO $$
DECLARE
    dup_count INT;
    keeper_pks BIGINT[];
BEGIN
    -- Identify the keeper PK per (LOWER(mac), site_id): the row with
    -- the newest last_seen_at, with id as tiebreaker so the choice is
    -- stable across re-runs.
    SELECT array_agg(pk) INTO keeper_pks
      FROM (
        SELECT DISTINCT ON (LOWER(mac_address), site_id) id AS pk
          FROM discovered_devices
         ORDER BY LOWER(mac_address), site_id, last_seen_at DESC NULLS LAST, id DESC
      ) s;

    -- Archive non-keepers.
    IF keeper_pks IS NOT NULL THEN
        WITH non_keepers AS (
            SELECT * FROM discovered_devices
             WHERE NOT (id = ANY (keeper_pks))
        )
        INSERT INTO discovered_devices_archive
        SELECT *,
               NOW() AS archived_at,
               'migration_244_dedup' AS archive_reason
          FROM non_keepers;

        GET DIAGNOSTICS dup_count = ROW_COUNT;
        RAISE NOTICE 'Migration 244: archived % duplicate discovered_devices rows', dup_count;

        DELETE FROM discovered_devices
         WHERE NOT (id = ANY (keeper_pks));
    END IF;
END $$;

-- Now safe to add the constraint.
DROP INDEX IF EXISTS discovered_devices_unique_mac_site_idx;
CREATE UNIQUE INDEX discovered_devices_unique_mac_site_idx
    ON discovered_devices (LOWER(mac_address), site_id);

-- ---------------------------------------------------------------------
-- 2. install_sessions GC
-- ---------------------------------------------------------------------
-- install_sessions has expires_at but nothing has been deleting rows
-- since Migration 206 set up the TTL. Rows accumulate, the
-- installer_halted_early invariant gets misleading old data.

CREATE OR REPLACE FUNCTION prune_install_sessions(retention_days INTEGER DEFAULT 30)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    -- Hard-delete rows older than retention_days where the installer
    -- never advanced past live_usb (they're truly stale; an installer
    -- that successfully reached `installed` becomes a site_appliances
    -- row anyway).
    DELETE FROM install_sessions
     WHERE first_seen < NOW() - (retention_days * INTERVAL '1 day')
       AND install_stage IN ('live_usb', 'completed', NULL);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END $$;

-- ---------------------------------------------------------------------
-- 3. nonces GC
-- ---------------------------------------------------------------------
-- nonces is consulted via `created_at > NOW() - <TTL>` on every
-- signed checkin. 2-hour TTL means rows older than that are dead
-- weight. Bound table size + speed up the lookup.

CREATE OR REPLACE FUNCTION prune_nonces(retention_hours INTEGER DEFAULT 4)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    -- Keep an extra 2h beyond NONCE_TTL (which is 2h) so any in-flight
    -- request about to verify has its nonce still around. Then delete.
    DELETE FROM nonces
     WHERE created_at < NOW() - (retention_hours * INTERVAL '1 hour');
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END $$;

-- ---------------------------------------------------------------------
-- 4. discovered_devices GC (orphaned rows)
-- ---------------------------------------------------------------------
-- Rows where last_seen_at hasn't been updated in 60 days are
-- effectively dead — either the device was removed, or the scanning
-- appliance was decommissioned. Archive then delete (preserves the
-- audit trail in discovered_devices_archive).

CREATE OR REPLACE FUNCTION prune_discovered_devices(retention_days INTEGER DEFAULT 60)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    WITH stale AS (
        SELECT * FROM discovered_devices
         WHERE last_seen_at < NOW() - (retention_days * INTERVAL '1 day')
    )
    INSERT INTO discovered_devices_archive
    SELECT *, NOW() AS archived_at, 'gc_stale' AS archive_reason FROM stale;

    DELETE FROM discovered_devices
     WHERE last_seen_at < NOW() - (retention_days * INTERVAL '1 day');
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END $$;

COMMIT;
