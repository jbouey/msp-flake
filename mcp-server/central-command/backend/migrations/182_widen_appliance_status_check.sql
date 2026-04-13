-- Widen site_appliances.status CHECK to include 'auth_failed' and 'decommissioned'.
--
-- Migration 180 tried to add these values via DO $$ IF NOT EXISTS $$ guard
-- — but the guard saw the existing CHECK constraint (with only 3 values:
-- pending/online/offline) and skipped the widening. Result: 180 claimed
-- to support 'decommissioned' but the actual constraint kept rejecting it.
--
-- Caught on 2026-04-13 when the round-table audit tried to soft-delete
-- osiriscare-3 with status='decommissioned' — CHECK violation.
--
-- Fix: DROP + re-ADD unconditionally. Safe because no prod row currently
-- uses the new values until this migration lands.

BEGIN;

ALTER TABLE site_appliances
    DROP CONSTRAINT IF EXISTS site_appliances_status_check;

ALTER TABLE site_appliances
    ADD CONSTRAINT site_appliances_status_check
    CHECK (status IN ('pending', 'online', 'offline', 'auth_failed', 'decommissioned'));

COMMIT;
