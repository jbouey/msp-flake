# bg_loop_silent

**Severity:** sev2
**Display name:** Background loop stuck (no heartbeat for 3× cadence)

## What this means (plain English)

The mcp-server runs ~50 supervised background loops (defined in
`main.py task_defs`). Each loop with a known cadence in
`bg_heartbeat.EXPECTED_INTERVAL_S` is monitored. This invariant fires
when ANY of those loops hasn't written a heartbeat in **more than 3×
its expected cadence**.

One violation row per stuck loop. `details.loop` names the offender.

Sev2 because: a stuck loop typically degrades a SINGLE feature
(flywheel promotions stop, partition maintenance lags, alert digests
miss). The `substrate_assertions_meta_silent` sev1 covers the
catastrophic case where the substrate engine itself is silent.

## Why this matters (architectural)

`_supervised` (the bg-loop wrapper at `main.py:1690`) auto-restarts a
loop on EXCEPTIONS. But a stuck `await` is not an exception — the
task hangs forever, the supervisor sees nothing, and the dashboard
shows "task running" indefinitely.

This invariant turns silent-stuck into a Sev2 within ~3 cadence-
windows of the affected loop.

## Excluded loops

- `substrate_assertions` — has its dedicated sev1 invariant
  `substrate_assertions_meta_silent`. Avoiding double-fire.
- `phantom_detector` — has its dedicated sev1 invariant
  `phantom_detector_healthy`. Avoiding double-fire.

Loops not in `EXPECTED_INTERVAL_S` (status='unknown') are skipped to
avoid noise. Backfilling that dict is the path to full coverage.

## Root cause categories

- **asyncpg pool exhaustion.** All connections checked out; the next
  `pool.acquire()` waits forever.
- **Hung HTTP fetch.** LLM call (`_l2_planner`), OTS calendar
  (`_ots_*` loops), GitHub API (`_version_drift_check`). No timeout,
  remote unresponsive.
- **Deadlock.** Two loops contending over the same advisory lock or
  row lock; PG resolves deadlocks but only after a default timeout
  of 1s. Some loops can deadlock against the application code (rare
  but documented for `_flywheel_promotion_loop` Step 5 historically).
- **Stuck on `asyncio.Event.wait()` or similar.** A signal that's
  set elsewhere but the elsewhere code path is dead.
- **Upstream dependency outage.** SMTP server (alert digests), Stripe
  webhooks, fleet API.

## Immediate action

1. **Identify the offender:** `details.loop` names it.

2. **Filter mcp-server logs by loop name:**
   ```
   ssh root@<vps> "docker compose -f /opt/mcp-server/docker-compose.yml \
     logs mcp-server --since 30m | grep -i '<loop_name>'"
   ```
   - Last log line + timestamp = when the loop went silent.
   - Look for traceback fragments leading up to the silence.

3. **Check pg_stat_activity for queries belonging to this loop:**
   ```sql
   SELECT pid, age(now(), query_start), state, query
     FROM pg_stat_activity
    WHERE datname='mcp' AND age(now(), query_start) > '5 minutes'
    ORDER BY query_start;
   ```

4. **Restart the container** if the cause isn't obvious + the
   downstream feature is operationally critical:
   ```
   ssh root@<vps> "docker compose -f /opt/mcp-server/docker-compose.yml \
     restart mcp-server"
   ```
   Note: this restarts ALL ~50 loops, not just the offender. Use
   only if the affected feature is critical and root-cause time
   exceeds tolerance.

5. **Per non-operator partner posture:** substrate exposes the
   silence; the operator decides whether the affected feature's
   degradation warrants customer notification. Most stuck loops
   self-heal on container restart and don't reach the threshold.

## Verification

- Panel: invariant row resolves on the next 60s tick after the loop
  resumes heartbeating.
- CLI:
  ```
  curl -s -H "Authorization: Bearer $TOKEN" http://VPS:8000/api/admin/health/loops \
    | jq '.loops["<loop_name>"]'
  ```

## Escalation

Sev2 — operator action expected within the workday.

If the SAME loop silences repeatedly across container restarts, the
bug is in the loop body itself, not transient — escalate to
engineering for a code fix.

If MULTIPLE loops fire this simultaneously, suspect asyncpg pool
exhaustion or PgBouncer starvation — the fault is upstream of any
single loop.

## Related runbooks

- `substrate_assertions_meta_silent.md` — sev1 sibling for the
  substrate engine's own loop
- `phantom_detector_healthy.md` — sev1 sibling for the phantom
  liveness verifier

## Change log

- 2026-05-01 — created — Session 214 Block 2 P0 closure round-table.
  Closes the silent-stuck-loop gap surfaced by the post-c270bb76
  audit Fork 1 (P0): "8 of 14 supervised loops have ZERO heartbeat
  instrumentation; stuck await is not an exception."
