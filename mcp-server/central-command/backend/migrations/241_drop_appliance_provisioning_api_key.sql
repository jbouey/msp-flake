-- Migration 241 — v40.6 Split #1 stage 2 (Principal SWE round-table 2026-04-24)
--
-- DROP the deprecated `appliance_provisioning.api_key` column.
--
-- Background: the column held a raw per-appliance api_key written at
-- initial provision and never kept in sync when the daemon's auto-
-- rekey path minted fresh keys into `api_keys` (the real source of
-- truth, hash-indexed, rotation-aware). Every reflash of a previously-
-- rekeyed appliance guaranteed an AUTH_KEY_MISMATCH 401 loop.
--
-- Stage 1 (commit a87dc9a6): all four writers converted to not touch
-- this column. `/api/provision/{mac}` mints fresh on every call and
-- writes only to `api_keys`. Admin claim / drop-ship / provisioning
-- handlers no longer populate it. No reader of the column remains.
--
-- Stage 2 (this migration): drop the column. Column has no NOT NULL
-- (migration 104 relaxed it), no UNIQUE, no FK, no index — verified
-- via pg_constraint + pg_indexes probe 2026-04-24 02:45 UTC. DROP
-- COLUMN IF EXISTS is idempotent for replay safety.
--
-- Rollback: `ALTER TABLE appliance_provisioning ADD COLUMN api_key
-- TEXT;` — but the column would be empty / useless. The only reason
-- to restore it would be resurrecting the pre-v40.6 split, which the
-- round-table explicitly rejected.

BEGIN;

ALTER TABLE appliance_provisioning DROP COLUMN IF EXISTS api_key;

COMMIT;
