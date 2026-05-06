# orders_stuck_acknowledged

**Severity:** sev2
**Display name:** Orders stuck in acknowledged/executing past timeout

## What this means (plain English)

`orders.status` is stuck in `acknowledged` (>30 min) or `executing`
(>1 hour) without ever reaching a terminal state (`completed`,
`failed`, or `expired`). The agent ack'd the order but never
reported execution telemetry that would transition the row.

Pre-mig-286, `orders.status` had NO code path that transitioned
it past `acknowledged` — every ack'd order would sit forever, and
the order-completion dashboard (`db_queries.py:1875`) showed 0%
completion as a result. Migration 286 added the
`auto_complete_order_on_telemetry` trigger that handles the
nominal happy path; this invariant catches edge cases where
either:

- The agent's telemetry path isn't reaching the backend
  (network gap, crash, missing `order_id` in metadata).
- The `sweep_stuck_orders()` function isn't running (bg task offline).
- A systemic issue is producing more stuck orders than the
  sweeper can clear in its tick.

## Root cause categories

- Agent crashed mid-execution; never wrote telemetry.
- Agent wrote telemetry but `order_id` was missing from the
  metadata payload (regression in agent telemetry serialization).
- Backend agent_api.py's telemetry-write endpoint is rate-limiting
  / failing.
- `sweep_stuck_orders_loop` background task is offline.
- The order's intended action genuinely takes >1 hour AND the
  agent's design doesn't emit progress telemetry; consider
  `executing` timeout extension for that order class (rare).

## Immediate action

1. Confirm volume + identify affected orders:

   ```sql
   SELECT order_id,
          status,
          appliance_id,
          site_id,
          acknowledged_at,
          NOW() - acknowledged_at AS stuck_for
     FROM orders
    WHERE (status = 'acknowledged' AND acknowledged_at < NOW() - INTERVAL '30 minutes')
       OR (status = 'executing' AND acknowledged_at < NOW() - INTERVAL '1 hour')
    ORDER BY acknowledged_at ASC
    LIMIT 50;
   ```

2. Check whether the affected appliance is reporting any telemetry
   at all in the last hour:

   ```sql
   SELECT appliance_id,
          COUNT(*) AS rows_last_hour,
          MAX(created_at) AS last_telemetry
     FROM execution_telemetry
    WHERE appliance_id IN (<list-from-step-1>)
      AND created_at > NOW() - INTERVAL '1 hour'
    GROUP BY appliance_id;
   ```

   - If `rows_last_hour = 0`: appliance is offline / network gap.
     Triage that as a separate incident class (mesh / liveness).
   - If telemetry IS arriving but no rows have `order_id`
     metadata: agent regression — the telemetry payload is
     missing the order_id field. Check agent version + recent
     deploys.

3. Verify the sweeper is running:

   ```bash
   docker logs mcp-server 2>&1 | grep "sweep_stuck_orders" | tail -5
   ```

   Expected: a log line every ~5 minutes. If absent, the bg
   task loop is offline; restart mcp-server.

4. Manually clear the backlog (idempotent):

   ```sql
   SELECT * FROM sweep_stuck_orders();
   ```

   Returns the count of rows transitioned. Re-run the §1 query
   to confirm the count dropped.

## Verification

After remediation:

```sql
SELECT COUNT(*)
  FROM orders
 WHERE (status = 'acknowledged' AND acknowledged_at < NOW() - INTERVAL '30 minutes')
    OR (status = 'executing' AND acknowledged_at < NOW() - INTERVAL '1 hour');
```

Should return `0` (or near-zero — there's always a small in-flight
window). The invariant clears within ~60s.

## Escalation

If the count climbs back up after `sweep_stuck_orders()` clears it,
the backend isn't writing telemetry properly OR the agent isn't
emitting `order_id`. P1 escalation; page on-call substrate engineer
+ assign to the agent telemetry path owner.

## Related runbooks

- `appliance_offline_for_24h.md` — sibling sev2 (appliance gone)
- `mesh_consistency_check_loop` related class

## Related

- Round-table: RT-DM data-model audit 2026-05-06
- Trigger: `auto_complete_order_on_telemetry` (mig 286)
- Sweeper function: `sweep_stuck_orders()` (mig 286)
- orders schema: mig 002
- execution_telemetry schema: mig 031 (+ mig 052 metadata column)

## Change log

- 2026-05-06: invariant introduced alongside RT-DM Issue #3
  hardening (non-consensus). Sev2 because order-completion
  dashboards become unreliable when stuck orders accumulate.
