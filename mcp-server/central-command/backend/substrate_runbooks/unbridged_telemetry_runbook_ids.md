# unbridged_telemetry_runbook_ids

**Severity:** sev2
**Display name:** Telemetry runbook_ids unbridged to runbooks table

## What this means (plain English)

The agent emits `execution_telemetry.runbook_id` using IDs from
`packages/compliance-agent/src/compliance_agent/rules/l1_baseline.json`
(form: `L1-SVC-DNS-001`, `L1-LIN-PERM-002`, etc.). The backend
`runbooks` table uses different IDs (`LIN-*`, `RB-*`, `WIN-*`,
`ESC-*`, etc.). Migration 284 (2026-05-06) added a bridge column
`runbooks.agent_runbook_id` and backfilled it for all known L1-*
agent rules.

This invariant fires when execution_telemetry has a runbook_id in
the last 7 days that doesn't match either `runbooks.runbook_id` OR
`runbooks.agent_runbook_id`. Means a new agent rule shipped
without a corresponding bridge row — drift between agent rule
shipments and the canonical runbook table.

Pre-mig-284, this was the silent state for months: per-runbook
execution counts on the Fleet Intelligence dashboard sat at 0
because the JOIN never found rows.

## Root cause categories

- A new L1-* rule was added to `l1_baseline.json` but the
  follow-up migration to add a bridge row was forgotten.
- An agent rule was renamed in `l1_baseline.json` and the bridge
  column wasn't updated to match.
- A telemetry row was written with a typo'd runbook_id (regex
  CHECK in the column would catch this; consider adding).

## Immediate action

1. Identify the unbridged runbook_ids:

   ```sql
   SELECT DISTINCT et.runbook_id, COUNT(*) AS occurrences
     FROM execution_telemetry et
    WHERE et.runbook_id IS NOT NULL
      AND et.runbook_id <> ''
      AND et.created_at > NOW() - INTERVAL '7 days'
      AND NOT EXISTS (
          SELECT 1 FROM runbooks r
           WHERE r.agent_runbook_id = et.runbook_id
              OR r.runbook_id = et.runbook_id
      )
    GROUP BY et.runbook_id
    ORDER BY occurrences DESC;
   ```

2. For each unbridged ID, decide its class:
   - **Existing backend runbook:** UPDATE the bridge row in a new
     migration: `UPDATE runbooks SET agent_runbook_id =
     '<unbridged-id>' WHERE runbook_id = '<canonical>'`.
   - **New agent rule with no backend counterpart:** INSERT a new
     row in a new migration: `INSERT INTO runbooks (runbook_id,
     agent_runbook_id, name, description, category, ...) VALUES
     ('AGENT-<unbridged-id>', '<unbridged-id>', ...)`.
   - **Typo in agent rule:** fix `l1_baseline.json` and re-deploy
     the agent. Old typo'd telemetry rows can be left alone or
     scrubbed.

3. Ship the migration as a numbered migration (next available);
   the substrate-assertions loop clears the violation within ~60s.

## Verification

After remediation:

```sql
SELECT COUNT(*) AS unbridged_count
  FROM execution_telemetry et
 WHERE et.runbook_id IS NOT NULL
   AND et.runbook_id <> ''
   AND et.created_at > NOW() - INTERVAL '7 days'
   AND NOT EXISTS (
       SELECT 1 FROM runbooks r
        WHERE r.agent_runbook_id = et.runbook_id
           OR r.runbook_id = et.runbook_id
   );
```

Should return `0`. The invariant clears on the next
substrate_assertions_loop iteration (~60s).

## Escalation

If volume is high (> 100 unbridged distinct IDs in 7 days), it
suggests a systematic gap (e.g., the agent shipped a new ruleset
without any bridge migration). Page on-call substrate engineer
+ open a ticket for the agent-team's PR review process to require
a corresponding migration before agent rule changes ship.

## Related runbooks

- The agent's rule registry: `packages/compliance-agent/src/
  compliance_agent/rules/l1_baseline.json`
- Bridge column home: `runbooks.agent_runbook_id` (mig 284)

## Related

- Round-table: `.agent/plans/RT-DM-data-model-audit-2026-05-06.md`
  (when written)
- Migration: `mcp-server/central-command/backend/migrations/
  284_runbook_agent_id_bridge.sql`
- CI gate: `mcp-server/central-command/backend/tests/
  test_runbook_id_translation_present.py` (when shipped)

## Change log

- 2026-05-06: invariant introduced alongside RT-DM Issue #1
  remediation (mig 284). Sev2 because per-runbook execution
  counts go to 0 silently when this drifts.
