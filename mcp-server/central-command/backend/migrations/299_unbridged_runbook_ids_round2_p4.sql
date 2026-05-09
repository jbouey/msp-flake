-- ============================================================================
-- Migration 299: Bridge 4 unbridged agent_runbook_id values surfaced by the
--                substrate engine post-2026-05-09 round-2 P0-RT2-A fix.
--
-- BACKGROUND
--   Round-2 audit (audit/coach-15-commit-adversarial-audit-round2-2026-05-09.md
--   P0-RT2-A) caught the substrate engine throwing TypeError every tick due to
--   broken Violation(detail=...) callsites. After the fix landed, the substrate
--   engine could finally PERSIST violation rows for invariants that had been
--   firing-but-aborting for the prior session window.
--
--   `unbridged_telemetry_runbook_ids` is one of those — sev2. It surfaces 4
--   `execution_telemetry.runbook_id` values that the agent reports but the
--   `runbooks` table has no `agent_runbook_id` bridge for. Per CLAUDE.md
--   rule "execution_telemetry.runbook_id" — agent uses internal IDs that
--   differ from backend IDs; bridge required.
--
--   Affected agent IDs (from substrate_violations details):
--     L1-NET-PORTS-001        — network port-scan check
--     L1-NET-REACH-001        — network reachability check
--     L1-WIN-ROGUE-TASKS-001  — Windows scheduled-task discovery
--     RB-WIN-SEC-019          — Windows security control variant
--
-- PATTERN (from existing bridge rows)
--     runbook_id | agent_runbook_id
--     -----------+------------------
--     AGENT-<X>  | <X>            (where <X> is the agent's L1-* ID)
--   For RB-WIN-SEC-019, follow the same shape: backend ID `AGENT-RB-WIN-SEC-019`.
--
-- IDEMPOTENCY
--   ON CONFLICT (agent_runbook_id) DO NOTHING — re-run safe given the
--   unique partial index on agent_runbook_id.
-- ============================================================================

INSERT INTO runbooks (runbook_id, name, category, severity, steps, agent_runbook_id, enabled, version)
VALUES
    ('AGENT-L1-NET-PORTS-001',
     'Agent L1: Network Port Scan',
     'network', 'medium', '[]'::jsonb,
     'L1-NET-PORTS-001', true, '1.0'),
    ('AGENT-L1-NET-REACH-001',
     'Agent L1: Network Reachability Check',
     'network', 'medium', '[]'::jsonb,
     'L1-NET-REACH-001', true, '1.0'),
    ('AGENT-L1-WIN-ROGUE-TASKS-001',
     'Agent L1: Windows Rogue Scheduled-Tasks Discovery',
     'windows', 'high', '[]'::jsonb,
     'L1-WIN-ROGUE-TASKS-001', true, '1.0'),
    ('AGENT-RB-WIN-SEC-019',
     'Agent: Windows Security Control RB-WIN-SEC-019',
     'windows', 'medium', '[]'::jsonb,
     'RB-WIN-SEC-019', true, '1.0')
ON CONFLICT (runbook_id) DO NOTHING;

-- The unique partial index on agent_runbook_id will catch any other duplicate
-- bridge attempts. Use a second ON CONFLICT for that index path.
-- (The runbooks_pkey + idx_runbooks_agent_runbook_id partial unique are
--  separate; the INSERT above won't conflict on agent_runbook_id if the
--  runbook_id is also unique. ON CONFLICT (runbook_id) covers re-run.)

-- Audit log entry capturing the substrate-driven backfill
INSERT INTO admin_audit_log (action, target, username, details, created_at)
VALUES (
    'migration_299_unbridged_runbook_ids_bridge',
    'runbooks',
    'jeff',
    jsonb_build_object(
        'migration', '299',
        'reason', 'Substrate invariant unbridged_telemetry_runbook_ids surfaced 4 agent_runbook_ids without backend bridges. Migration bridges them. Round-2 audit P0-RT2-A fix made the substrate engine able to persist this finding for the first time.',
        'audit_ref', 'audit/coach-15-commit-adversarial-audit-round2-2026-05-09.md task #104',
        'bridges_added', jsonb_build_array(
            'L1-NET-PORTS-001 -> AGENT-L1-NET-PORTS-001',
            'L1-NET-REACH-001 -> AGENT-L1-NET-REACH-001',
            'L1-WIN-ROGUE-TASKS-001 -> AGENT-L1-WIN-ROGUE-TASKS-001',
            'RB-WIN-SEC-019 -> AGENT-RB-WIN-SEC-019'
        )
    ),
    NOW()
);
