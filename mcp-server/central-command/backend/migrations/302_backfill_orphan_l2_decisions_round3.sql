-- Migration 302: backfill round-3 L2-orphan from /api/agent/l2/plan endpoint
-- (Session 219 round-3, found 2026-05-11).
--
-- The 4th callsite of resolution_tier='L2' lives in the DAEMON: when
-- the Go daemon calls /api/agent/l2/plan and receives a runbook back,
-- it updates the incident locally with resolution_tier='L2' via a
-- separate endpoint. If the server-side record_l2_decision() inside
-- the /l2/plan endpoint silently failed (try/except logger.error),
-- the daemon still got the plan and still set resolution_tier='L2'.
--
-- Forward fix shipped this commit: /api/agent/l2/plan now:
--   1. Tracks l2_decision_recorded boolean
--   2. Falls through to action='escalate' if record failed
--   3. Returns runbook_id="" if record failed
--   4. Exposes l2_decision_recorded in response so daemon can also gate
--
-- This migration backfills the 1 orphan that slipped through.
-- Idempotent via NOT EXISTS.

BEGIN;

INSERT INTO l2_decisions (
    incident_id, site_id, runbook_id, reasoning, confidence,
    pattern_signature, llm_model, llm_latency_ms,
    requires_human_review, created_at, escalation_reason, prompt_version
)
SELECT
    i.id::varchar,
    i.site_id,
    NULL,
    'Backfill (mig 302, Session 219 round-3): incident `' || i.id::text ||
    '` carried resolution_tier=L2 (daemon-set via /api/agent/l2/plan '
    'response) with no matching l2_decisions row. Root cause: server-'
    'side record_l2_decision() inside agent_l2_plan endpoint silently '
    'failed (try/except logger.error); the daemon still received the '
    'runbook in the response and set resolution_tier=L2 locally. '
    'Forward fix shipped same commit: response gates on l2_decision_'
    'recorded flag (action="escalate", runbook_id="" when record fails) '
    'and exposes the flag so daemon can also gate. Reasoning + '
    'runbook_id NULL — original LLM response was not persisted.' AS reasoning,
    0.0,
    'L2-ORPHAN-BACKFILL-MIG-302',
    'backfill_synthetic',
    0,
    FALSE,
    COALESCE(i.resolved_at, i.reported_at),
    'backfill',
    'mig-302'
FROM incidents i
WHERE i.resolution_tier = 'L2'
  AND NOT EXISTS (SELECT 1 FROM l2_decisions d WHERE d.incident_id = i.id::varchar);

INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'system:mig-302', 'backfill_l2_orphans_round3', 'l2_decisions',
    jsonb_build_object(
        'migration', '302_backfill_orphan_l2_decisions_round3',
        'session', 'Session 219 round-3 (2026-05-11)',
        'reason', 'agent_l2_plan endpoint was the missed 4th callsite — daemon set resolution_tier=L2 from response without checking record_l2_decision succeeded',
        'forward_fix', 'agent_api.agent_l2_plan now gates response on l2_decision_recorded boolean'
    ),
    NOW()
);

COMMIT;
