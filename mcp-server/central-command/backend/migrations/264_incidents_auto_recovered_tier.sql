-- Migration 264 — extend incidents.resolution_tier CHECK to include
-- 'auto_recovered' for the new fail→pass real-time auto-resolve path
-- (Block-3 audit P0.2, 2026-05-01).
--
-- Context: pre-fix, the only auto-resolve path was the 7-day stale
-- sweep in `_resolve_stale_incidents` which writes
-- `resolution_tier='monitoring'`. That meant a chaos-lab Windows VM
-- that recovered after 4 hours stayed "open" in the dashboard for
-- 7 days waiting for the sweep, even though the underlying check
-- had transitioned fail→pass. The audit found no code path to close
-- on that transition.
--
-- This migration adds the 'auto_recovered' value to the
-- resolution_tier CHECK so the new post-bundle-INSERT hook can mark
-- incidents resolved with a distinguishable tier.
--
-- Forensic contract: any incident with `resolution_tier='auto_recovered'`
-- means "the underlying check transitioned to pass while this incident
-- was open; we self-resolved." Distinguishable from:
--   * 'L1'/'L2'/'L3'  — healing pipeline closed it via remediation
--   * 'monitoring'    — 7-day stale sweep gave up on it
--   * NULL            — not yet resolved, or pre-tier-tracking row
--
-- Idempotent: drops the OLD constraint name then re-adds with the
-- expanded set.

BEGIN;

ALTER TABLE incidents
    DROP CONSTRAINT IF EXISTS incidents_resolution_tier_check;

ALTER TABLE incidents
    ADD CONSTRAINT incidents_resolution_tier_check
    CHECK (
        resolution_tier IS NULL
        OR resolution_tier IN ('L1', 'L2', 'L3', 'monitoring', 'auto_recovered')
    );

COMMENT ON COLUMN incidents.resolution_tier IS
    'L1=deterministic rule, L2=LLM planner, L3=human escalation, '
    'monitoring=7d stale sweep, auto_recovered=fail→pass transition '
    'on bundle ingest (Block-3 P0.2, mig 264).';

-- Audit-log the migration per CLAUDE.md migration discipline.
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'migration:264',
    'schema.alter',
    'incidents.resolution_tier_check',
    jsonb_build_object(
        'reason', 'Add auto_recovered tier for fail→pass real-time resolve',
        'audit_block', 'Session-214 Block-3 P0.2',
        'shipped', '2026-05-01'
    ),
    NOW()
)
ON CONFLICT DO NOTHING;

COMMIT;
