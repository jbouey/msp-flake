-- Migration 165: Document deprecation of appliance_commands table
--
-- Session 205 audit found appliance_commands had 0 rows (write-only
-- graveyard, no readers). Two writers (learning_api.py:559 and :1058)
-- attempted to deliver promoted L1 rules to appliances via this table —
-- but appliances never read from it. Real delivery channel is fleet_orders,
-- which Phase 2 wired in via promote_candidate() →
-- issue_sync_promoted_rule_orders().
--
-- This migration:
--   - Adds a comment to the table marking it deprecated
--   - Does NOT drop the table (deferred to a future session per DBA
--     migration plan: 1) stop writes (this PR); 2) wait 24h confirm
--     zero new writes; 3) ALTER ... RENAME TO archive name; 4) DROP
--     after 1 week + backup)
--
-- Verification post-deploy (24h):
--   SELECT count(*) FROM appliance_commands;  -- must remain 0

BEGIN;

COMMENT ON TABLE appliance_commands IS
    'DEPRECATED 2026-04-13 (Session 205): write-only graveyard, never read. '
    'Replaced by fleet_orders (sync_promoted_rule order_type). '
    'All Python writes removed in commit fixing Phase 4. '
    'Table retained for archival; will be renamed and dropped in a future cycle.';

COMMIT;
