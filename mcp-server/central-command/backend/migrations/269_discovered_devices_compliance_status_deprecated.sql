-- Migration 269 — deprecate discovered_devices.compliance_status
-- (BUG 3 round-table 2026-05-01, fork a48dd10968aaf583c, Path C).
--
-- Root cause: `discovered_devices.compliance_status` is a denormalized
-- cache that was never wired to the bundle-ingest path. It defaulted
-- to 'unknown' at INSERT time and never got updated when compliance
-- bundles arrived. The Devices Inventory dashboard displayed
-- "Managed Fleet 0% / 28 Unscanned" while the site-level score
-- correctly showed 94% — same writer/reader divergence class as
-- BUG 2 / mig 268.
--
-- Round-table consensus 5/5 + Coach: source-of-truth is
-- `compliance_bundles` (Ed25519 + OTS-anchored chain). Per-device
-- compliance is now derived LIVE via
-- `db_queries.get_per_device_compliance` — see device_sync.py
-- changes shipped in the same commit.
--
-- This migration is documentation-only: marks the column as
-- DEPRECATED so future writers don't try to populate it, and future
-- readers know to use the helper instead. CI gate test
-- `test_compliance_status_not_read.py` enforces the read-side ban.
--
-- Idempotent: COMMENT ON COLUMN is re-runnable.

BEGIN;

COMMENT ON COLUMN discovered_devices.compliance_status IS
    'DEPRECATED 2026-05-01 (BUG 3 round-table fork a48dd10968aaf583c): '
    'never updated by bundle-ingest path; legacy cache that always '
    'reads "unknown". Use `db_queries.get_per_device_compliance(db, '
    'site_id, window_days=30)` to compute per-device status live from '
    'compliance_bundles (the cryptographically-anchored source of '
    'truth). New code reading this column fails CI via '
    'tests/test_compliance_status_not_read.py.';

-- Audit-log
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'migration:269',
    'column.deprecate',
    'discovered_devices.compliance_status',
    jsonb_build_object(
        'reason', 'Never wired to bundle-ingest; live-computed via get_per_device_compliance instead',
        'audit_block', 'BUG 3 round-table 2026-05-01',
        'consensus', 'Path C (5/5 + Coach: Brian, Diana, Camila, Steve, Priya)',
        'rejected_path_a', 'writer-side cache update — would reintroduce mig 268 writer/reader divergence class',
        'rejected_path_b', 'one-time backfill — unnecessary once Path C ships',
        'sibling_path_d', 'mig 270 archives stale orphan + relocated rows',
        'shipped', '2026-05-01'
    ),
    NOW()
)
ON CONFLICT DO NOTHING;

COMMIT;
