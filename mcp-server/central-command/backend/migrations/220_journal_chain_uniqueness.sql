-- Migration 220: prevent hash-chain forks on concurrent journal uploads
--
-- Phase H4 gate fix. The first cut of the journal receiver fetched the
-- previous chain hash in a read outside the INSERT transaction —
-- concurrent uploads from the same appliance (timer double-fire, retry
-- after transient failure) would compute identical chain_prev_hash,
-- produce divergent chain_hashes, and both commit. Because the rows
-- are append-only via prevent_audit_deletion, the fork is permanent
-- and the chain silently bifurcates. An auditor walking the chain
-- forward from genesis sees inconsistent topology.
--
-- Fix: unique partial index on (appliance_id, chain_prev_hash) where
-- chain_prev_hash IS NOT NULL. The very first row per appliance has a
-- NULL prev and is still allowed. Second writer on the same prev is
-- rejected by the index; journal_api.py catches the UniqueViolation
-- and retries with a fresh SELECT under a tighter transaction.

BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS journal_upload_events_no_chain_fork
    ON journal_upload_events (appliance_id, chain_prev_hash)
    WHERE chain_prev_hash IS NOT NULL;

COMMIT;
