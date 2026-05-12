# recurrence_velocity_stale

**Severity:** sev3
**Display name:** Recurrence-velocity loop stale (single-point-of-failure surface)

## What this means (plain English)

The recurrence-detector path in `agent_api.py::report_incident` reads
`incident_recurrence_velocity` to decide whether an incident is a chronic
pattern that should bypass L1 and route to L2. That table is populated by
`background_tasks.py::recurrence_velocity_loop` on a ~5min cadence. This
invariant fires when the most-recent `computed_at` for any row is older
than **10 minutes** — the freshness gate the detector itself enforces.

When `computed_at` ages past 10 minutes, the detector falls back to
"no recurrence" for every incoming incident — the L1 path runs unchanged,
even for genuinely chronic patterns. Today's broken-by-partitioning
class (fixed 2026-05-12) had the same failure shape; this sev3 invariant
exists per Steve P0-B (Gate A 2026-05-12) to prevent a velocity-loop outage
from silently killing L2 escalation across the entire fleet without any
dashboard signal.

## Root cause categories

- **Loop exception.** `recurrence_velocity_loop` at `background_tasks.py:1193-1194`
  catches `Exception` and logs warning. A persistent error in the INSERT-SELECT
  (e.g., temporary PG timeout, lock contention with a long-running maintenance
  job) means the loop keeps logging warnings without making progress.
- **Loop not running.** Container restarted, supervisor task crashed, asyncio
  task gathered without proper exception handling. Symptom: zero new rows for
  10+ minutes.
- **Database-side stall.** The INSERT-SELECT against `incidents` (partitioned
  table) takes longer than expected — e.g., a missing index on
  `(site_id, incident_type, status, resolved_at)`. Loop is running but cannot
  complete a tick fast enough.
- **Clock skew.** Container time drifted past UTC by >10 minutes. Rare, but
  surfaces as freshness gate false-positive.

## Immediate action

1. Inspect the loop's recent log lines:

   ```bash
   docker logs mcp-server 2>&1 | grep -i "recurrence_velocity" | tail -40
   ```

   Look for: stack traces (loop exception), absence of "tick complete" lines
   (loop not running), or slow-query logs (DB stall).

2. Confirm the loop is alive:

   ```bash
   docker exec mcp-postgres psql -U mcp -d mcp -c "
     SELECT MAX(computed_at) AS newest, NOW() - MAX(computed_at) AS age
       FROM incident_recurrence_velocity;
   "
   ```

   If `age > 10 minutes`, the loop is wedged. If `age > 1 hour`, escalate.

3. **If loop exception in logs:** check upstream resource state (PG connection
   pool, lock contention via `SELECT * FROM pg_stat_activity WHERE state =
   'active' AND query_start < NOW() - INTERVAL '5 minutes'`).

4. **If loop not running at all:** restart the mcp-server container. The
   background tasks restart with the app. (`docker compose restart mcp-server`).

5. **Confirm L2 routing recovered.** Once `computed_at` is fresh again, the
   `chronic_without_l2_escalation` sev2 invariant (companion) clears on the
   next tick if there is no historical gap. Watch the substrate panel.

## Verification

```sql
SELECT MAX(computed_at) AS newest,
       NOW() - MAX(computed_at) AS age,
       COUNT(*) AS row_count
  FROM incident_recurrence_velocity;
```

After remediation: `age < INTERVAL '10 minutes'`. The invariant clears on
the next 60s substrate tick.

## Escalation

If the loop cannot stay healthy for 1 hour after a restart, that is a P1
engineering issue — the recurrence-detector path is the L2 routing gate
for the entire fleet. Page the substrate on-call.

Note: this is **sev3** (not sev2) because the customer-facing surface stays
correct as long as the velocity loop catches up within minutes — short
stalls don't materialize as a missed-L2 incident, since the detector reads
the table at incident-open time. A sev3 here paired with sev2 on
`chronic_without_l2_escalation` is the right severity split: stale-detector
is a leading indicator, missed-escalation is the lagging contract breach.

## Related runbooks

- `chronic_without_l2_escalation.md` — downstream sev2; fires if the stale
  detector caused a chronic pattern to miss L2 escalation.
- `l2_decisions_stalled.md` — different class (L2 LLM not running AT ALL
  rather than the routing-into-L2 path being broken).

## Change log

- 2026-05-12 — invariant introduced (Session 220 P1 persistence-drift L2
  routing fix, Steve P0-B from Gate A). Sev3 single-point-of-failure surface
  for the recurrence-velocity-loop. Without this, a loop outage silently
  killed L2 escalation across the fleet (the exact failure mode the
  detector partitioning bug exhibited pre-fix; we are not paying that
  invisible-failure cost twice).
