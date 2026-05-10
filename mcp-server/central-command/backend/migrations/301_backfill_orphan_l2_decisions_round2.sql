-- Migration 301: backfill round-2 of L2-orphan incidents (Session 219).
--
-- Mig 300 closed 26 historical orphans on 2026-05-09. Substrate then
-- re-fired the sev2 invariant on 2026-05-10 02:27 UTC because 5 NEW
-- orphans had appeared between 2026-05-09 20:37 and 2026-05-10 01:27.
--
-- Root cause: a THIRD callsite that mig 300 missed —
-- `sites.py:2982` (L1-failed → L2 planner fallback inside the
-- order-expiration handler) called `l2_analyze()` directly and set
-- `resolution_tier='L2'` WITHOUT calling `record_l2_decision()`.
-- The forward fixes shipped in mig 300 only covered agent_api.py
-- and main.py.
--
-- Forward fix shipped this commit:
--   * sites.py:2982 — wrap in try/record_l2_decision_asyncpg/gate
--     on l2_decision_recorded
--   * l2_planner.py — add `record_l2_decision_asyncpg` sibling
--     (the existing record_l2_decision uses SQLAlchemy `text()` and
--     is incompatible with raw asyncpg connections; sites.py uses
--     asyncpg via admin_transaction)
--   * tests/test_l2_resolution_requires_decision_record.py — add
--     sites.py to _FILES_TO_SCAN so the gate catches this class
--     structurally going forward
--
-- This migration is broad-filter (same shape as mig 300): backfills
-- ANY incident with resolution_tier='L2' AND no l2_decisions row,
-- with the same auditor-distinguishable synthetic markers.
-- Idempotent via NOT EXISTS.

BEGIN;

INSERT INTO l2_decisions (
    incident_id,
    site_id,
    runbook_id,
    reasoning,
    confidence,
    pattern_signature,
    llm_model,
    llm_latency_ms,
    requires_human_review,
    created_at,
    escalation_reason,
    prompt_version
)
SELECT
    i.id::varchar,
    i.site_id,
    NULL,
    'Backfill (mig 301, Session 219 round-2): incident `' || i.id::text ||
    '` carried resolution_tier=L2 with no matching l2_decisions row. '
    'Root cause: sites.py L1-failed→L2 fallback path issued '
    '`UPDATE incidents SET resolution_tier=L2` without recording '
    'the L2 decision. Discovered when mig 300 cleared the prior '
    'sev2 invariant but the substrate engine re-fired ~7 hours later. '
    'Forward fix shipped in same commit: sites.py:2982 now uses '
    'record_l2_decision_asyncpg + l2_decision_recorded gate. '
    'Reasoning + runbook_id are NULL because the original LLM '
    'response was not persisted at decision time.' AS reasoning,
    0.0,
    'L2-ORPHAN-BACKFILL-MIG-301',
    'backfill_synthetic',
    0,
    FALSE,
    COALESCE(i.resolved_at, i.reported_at),
    'backfill',
    'mig-301'
FROM incidents i
WHERE i.resolution_tier = 'L2'
  AND NOT EXISTS (
      SELECT 1 FROM l2_decisions d WHERE d.incident_id = i.id::varchar
  );

INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'system:mig-301',
    'backfill_l2_orphans_round2',
    'l2_decisions',
    jsonb_build_object(
        'migration', '301_backfill_orphan_l2_decisions_round2',
        'session', 'Session 219 round-2 (2026-05-10)',
        'reason', 'sites.py L1-failed L2 fallback path was the missed third callsite',
        'forward_fix', 'sites.py:2982 + l2_planner.record_l2_decision_asyncpg + tests/test_l2_resolution_requires_decision_record.py adds sites.py to scan list'
    ),
    NOW()
);

COMMIT;
