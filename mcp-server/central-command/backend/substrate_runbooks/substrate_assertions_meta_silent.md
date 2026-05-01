# substrate_assertions_meta_silent

**Severity:** sev1
**Display name:** Substrate watcher itself is silent (meta)

## What this means (plain English)

The Substrate Integrity Engine runs `assertions_loop` every 60 seconds.
Each tick it walks all 50+ invariants, refreshes/opens/resolves
violations, and writes a heartbeat to the in-process registry.

This invariant fires when **that heartbeat hasn't updated in 180+
seconds (3× the 60s cadence)**. Sev1 because: the dashboard's
"all-clear" state is meaningless while this fires — the watcher is
the thing that produces violations. If the watcher is silent, every
downstream signal (sigauth failures, evidence-chain stall, fleet-edge
liveness gaps, flywheel ledger drift) is also silent.

This is the meta-failure that hides every other failure.

## Why this matters (architectural)

Per Session 207 substrate doctrine: "if the dashboard says X, X is
true, cryptographically." That doctrine collapses if the dashboard's
data source — the substrate engine — is silently dead. The Apr-16
phantom_detector silent-crash class (closed by
`_check_phantom_detector_healthy` invariant) is reproducible at the
META layer: the substrate engine itself can hang.

`_supervised` (the bg-loop wrapper) auto-restarts on EXCEPTIONS but
a stuck `await` is not an exception. The task hangs forever, the
supervisor sees nothing, and the dashboard pins to whatever state
existed at the last successful tick.

## Root cause categories

- **asyncpg pool exhaustion.** All 25 connections checked out by
  long-running queries (e.g. a slow log_entries query, a hung tenant
  audit retrieval). The next `pool.acquire()` waits indefinitely.
- **PgBouncer transaction-pool starvation.** All 25 server-side
  connections held by other clients; pgbouncer queues the request.
- **Deadlock between substrate and another loop.** Substrate holds
  a row lock; another loop holds a different row; both wait.
- **Container resource exhaustion.** OOM-killed worker that didn't
  trigger a full container restart (rare on 4 GB+ containers).
- **Schema drift.** A migration changed a column type that an
  invariant's SQL depends on; the SQL errors silently caught by an
  outer try/except, but the loop body never advances past it.

## Immediate action

1. **Confirm the silence is real, not a false positive:**
   ```
   ssh root@<vps> "docker compose -f /opt/mcp-server/docker-compose.yml \
     logs mcp-server --since 5m | grep -E 'assertions tick|substrate_assertions'"
   ```
   Expected: ≥4 lines. If zero lines, the loop is genuinely silent.

2. **Check pg_stat_activity for stuck queries:**
   ```sql
   SELECT pid, age(now(), query_start) AS duration, state, query
     FROM pg_stat_activity
    WHERE datname='mcp' AND state='active' AND age(now(), query_start) > '60 seconds';
   ```
   Long-running queries that include "FROM substrate_violations" or
   the assertion SQL bodies are likely culprits.

3. **Check asyncpg pool saturation:**
   ```sql
   SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE '%asyncpg%';
   ```
   Above pool max = exhausted.

4. **Restart the container as a blunt-but-fast fix:**
   ```
   ssh root@<vps> "docker compose -f /opt/mcp-server/docker-compose.yml \
     restart mcp-server"
   ```
   This restarts the supervisor, which re-spawns the loop with a fresh
   asyncpg connection. The invariant should auto-resolve on the next
   60s tick.

5. **Root cause AFTER restoration.** Operator-class triage decision
   per non-operator partner posture: substrate exposes the silence;
   the operator decides whether the customer needs notification.
   For most outage windows < 15 min, no clinic notification.

## Verification

- Panel: this invariant row clears on the next 60s tick once
  `assertions_loop` resumes.
- CLI: query `bg_heartbeat` (in-process) via the admin loops endpoint:
  ```
  curl -s -H "Authorization: Bearer $TOKEN" http://VPS:8000/api/admin/health/loops \
    | jq '.loops.substrate_assertions.age_s'
  ```
  Expected: < 60 once recovered.

## Escalation

Sev1 — operator action expected within 15 min. The substrate engine
is the trust foundation; extended silence breaks the
audit-grade-evidence claim until restored.

If the loop crashes immediately on every restart, the cause is
upstream of the loop body (e.g. import-time error in a new invariant,
or a database migration the container hasn't picked up). Check
container startup logs for ImportError / OperationalError.

## Related runbooks

- `phantom_detector_healthy.md` — the orthogonal-liveness sibling that
  inspired this meta-watcher pattern (Apr-16 incident)
- `bg_loop_silent.md` — generic stuck-loop watcher (sev2 sibling)

## Change log

- 2026-05-01 — created — Session 214 Block 2 P0 closure round-table.
  Closes the substrate-meta-health gap surfaced by the post-c270bb76
  audit Fork 1 (P0). The watcher needs a watcher.
