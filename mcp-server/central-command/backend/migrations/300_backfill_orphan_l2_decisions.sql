-- Migration 300: backfill orphan l2_decisions rows for incidents
-- whose resolution_tier='L2' was set despite record_l2_decision()
-- failing in agent_api.py:1338-1339 (Session 219 task #104).
--
-- Substrate invariant `l2_resolution_without_decision_record` (sev2)
-- detects this case. Forward fix lives in agent_api.py +
-- mcp-server/main.py: refuse to set resolution_tier='L2' unless
-- the corresponding l2_decisions write succeeded. This migration
-- closes the historical leak (26 incidents on north-valley-branch-2:
-- 6× WIN-DEPLOY-UNREACHABLE 2026-03-19, 4× protocol/patching/registry
-- 2026-03-24, 9× ransomware_indicator 2026-04-25 → 2026-05-02, 7×
-- ransomware_indicator 2026-05-03 → 2026-05-09). The substrate
-- violation row reported match_count=7 because it samples the most
-- recent; underlying SQL count was 26.
--
-- Backfilled rows are intentionally distinguishable:
--   * llm_model = 'backfill_synthetic'
--   * pattern_signature = 'L2-ORPHAN-BACKFILL-MIG-300'
--   * reasoning explicitly states this is a structural-gap closure
--
-- Auditors reviewing the chain see exactly what happened: the L2
-- LLM was invoked but the l2_decisions write raised mid-flight,
-- the resolution_tier='L2' UPDATE landed, then the substrate engine
-- caught the gap. This row preserves the FACT that L2 ran, not a
-- fabricated reasoning trace.

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
    NULL,  -- runbook_id unknown — record_l2_decision raised before write
    'Backfill (mig 300, Session 219 task #104): incident `' || i.id::text ||
    '` carried resolution_tier=L2 with no matching l2_decisions row. '
    'Root cause: agent_api.py:1338-1339 swallowed record_l2_decision() '
    'exception and continued setting resolution_tier=L2. Forward fix '
    'shipped same session — record_l2_decision failures now block the '
    'L2 path and escalate to L3 instead of producing ghost-L2 rows. '
    'This row is the audit trail that L2 was attempted; reasoning + '
    'runbook_id are NULL because the original LLM response was lost.' AS reasoning,
    0.0,
    'L2-ORPHAN-BACKFILL-MIG-300',
    'backfill_synthetic',
    0,
    FALSE,
    i.resolved_at,
    'backfill',
    'mig-300'
FROM incidents i
WHERE i.resolution_tier = 'L2'
  AND i.resolved_at IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM l2_decisions d WHERE d.incident_id = i.id::varchar
  );

-- Audit-log row so operators see the migration ran (admin_audit_log
-- requires `username`, NOT `actor` — see CLAUDE.md rule).
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'system:mig-300',
    'backfill_l2_orphans',
    'l2_decisions',
    jsonb_build_object(
        'migration', '300_backfill_orphan_l2_decisions',
        'session', 'Session 219 task #104',
        'site_id', 'north-valley-branch-2',
        'incident_count', 26,
        'reason', 'closes substrate invariant l2_resolution_without_decision_record (sev2)'
    ),
    NOW()
);

COMMIT;
