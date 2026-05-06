# l2_resolution_without_decision_record

**Severity:** sev2
**Display name:** L2 resolution without LLM decision record

## What this means (plain English)

An incident has `resolution_tier = 'L2'` (the L2 LLM-planner path
was the resolver) AND `status = 'resolved'`, but no row in
`l2_decisions` references the incident. Integrity gap between the
resolution tier and the LLM decision record — auditors would
flag this either as decision-without-record OR record-without-
decision (both classes are bad).

L2 success-rate dashboards consume `v_l2_outcomes` (mig 285) which
JOINs `l2_decisions` to `incidents`. When this invariant fires,
the canonical view is unreliable for the affected incidents.

## Root cause categories

- A code path in `agent_api.py` set `resolution_tier='L2'` but
  did not call `record_l2_decision()`. Recent regression in the
  L2 cascade flow.
- An incident was tier-set manually (DB surgery) for ops reasons
  without inserting a paired `l2_decisions` row.
- The cache-hit code path (`lookup_cached_l2_decision`) returned a
  hit but the cached decision was deleted from `l2_decisions`
  later (e.g., retention cleanup).

## Immediate action

1. Identify the affected incidents:

   ```sql
   SELECT i.incident_id,
          i.site_id,
          i.appliance_id,
          i.incident_type,
          i.resolution_tier,
          i.resolved_at,
          i.created_at
     FROM incidents i
    WHERE i.resolution_tier = 'L2'
      AND i.status = 'resolved'
      AND i.resolved_at > NOW() - INTERVAL '7 days'
      AND NOT EXISTS (
          SELECT 1 FROM l2_decisions ld
           WHERE ld.incident_id = i.id::text
              OR ld.incident_id = i.incident_id
      )
    ORDER BY i.resolved_at DESC;
   ```

2. For each affected incident, check what code path resolved it:

   ```bash
   docker logs mcp-server 2>&1 | grep "<incident_id>" | head -20
   ```

   Look for `record_l2_decision called` or `l2_planner.analyze_incident`
   log lines. Their absence confirms the regression.

3. **If recent regression:** open a P1 engineering ticket;
   `agent_api.py` has multiple L2-tier-set paths and at least one
   is missing the `record_l2_decision` call. Pin the fix with a
   CI gate that asserts the two operations are co-located in the
   same code block.

4. **If pre-mig-264 historical:** older `incidents.resolution_tier`
   values may not have a paired `l2_decisions` row because the
   pairing convention wasn't enforced then. Short-term: add a
   reason='backfill / convention pre-existing' note in
   `l2_decisions` for the affected incidents to clear the
   invariant. Long-term: tighten the time window in the invariant
   query to exclude pre-mig-264 incidents.

## Verification

After remediation:

```sql
SELECT COUNT(*) AS gap_count
  FROM incidents i
 WHERE i.resolution_tier = 'L2'
   AND i.status = 'resolved'
   AND i.resolved_at > NOW() - INTERVAL '7 days'
   AND NOT EXISTS (
       SELECT 1 FROM l2_decisions ld
        WHERE ld.incident_id = i.id::text
           OR ld.incident_id = i.incident_id
   );
```

Should return `0`.

## Escalation

If the regression is real (code path missing `record_l2_decision`),
it's a P1 chain-of-decision-record gap. Page on-call substrate
engineer + assign to the agent_api owner.

## Related runbooks

- `l2_decisions_stalled.md` — sibling sev2 invariant (no L2
  decisions at all)

## Related

- Round-table: RT-DM data-model audit 2026-05-06
- Canonical view: `v_l2_outcomes` (mig 285)
- Canonical function: `compute_l2_success_rate(window_days)` (mig 285)
- Module: `mcp-server/central-command/backend/agent_api.py` L2
  cascade paths
- L2 planner: `mcp-server/central-command/backend/l2_planner.py`

## Change log

- 2026-05-06: invariant introduced alongside RT-DM Issue #2
  hardening (non-consensus). Sev2 because L2 success-rate
  dashboards become unreliable when the gap exists.
