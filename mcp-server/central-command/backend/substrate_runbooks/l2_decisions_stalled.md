# l2_decisions_stalled

**Severity:** sev2
**Display name:** L2 LLM decisions silently stalled

## What this means (plain English)

The L2 LLM planner is supposed to be running (`L2_ENABLED=true` in
the mcp-server env) and the fleet has at least one appliance actively
checking in, but fewer than 5 L2 decisions have landed in the last
48 hours. The planner is silently offline — the infrastructure says
it's on, but no work is flowing through.

This invariant was added on 2026-04-24 when L2 was re-enabled after
a 12-day kill switch. The *reason* for the kill switch was exactly
this failure mode going undetected for 14 days during Session 205.
This invariant exists so the next silent death pages inside 48h
instead of hiding for weeks.

## Root cause categories

- **LLM API credentials rotated or exhausted** — the most common cause.
  Anthropic / OpenAI / Azure OpenAI key is invalid, revoked, or out of
  credit. Every call fails, the circuit breaker opens after 5
  consecutive failures, decisions stop.
- **Circuit breaker stuck open** — after 5 consecutive API failures
  the breaker opens for 15 min. If a transient API outage lasted
  longer than 15 min the breaker may oscillate; verify the cooldown
  actually cleared.
- **Daily call cap reached** — `MAX_DAILY_L2_CALLS` is tight (100 in
  the 2026-04-24 re-enable window). If legitimate traffic keeps
  hitting the cap, raise it. If not, something is calling L2 in a
  loop that shouldn't be.
- **Zero-result circuit clamped every pattern** — after 2 consecutive
  "LLM returned null runbook" responses for a `(site, incident_type)`
  pair, that pair is paused until UTC midnight. If every active
  pattern got clamped, L2 falls silent even though the pipeline is
  healthy.
- **Kill switch still in place** — `L2_ENABLED=false` would make this
  invariant stay quiet, but if someone flipped it back OFF mid-recovery
  the invariant fires until it's flipped ON or the env var is cleared.

## Immediate action

Walk the cost-gate stack in order. Most of these are 1-line SSH commands
on the VPS.

1. **API key valid?**
   ```
   docker exec mcp-server env | grep -E "ANTHROPIC_API_KEY|OPENAI_API_KEY|AZURE_OPENAI_API_KEY"
   ```
   At least one of these must be non-empty. The dispatch order in
   `l2_planner.py:1254-1266` is Azure → OpenAI → Anthropic.

2. **Circuit breaker state?**
   ```
   docker logs mcp-server --since 2h 2>&1 | grep -E "L2 circuit breaker"
   ```
   If you see `OPENED` without a matching `CLOSED` or `cooldown expired`,
   the breaker is stuck. Fastest reset: `docker compose restart mcp-server`
   (state is in-memory).

3. **Daily call cap tripped?**
   ```
   docker logs mcp-server --since 24h 2>&1 | grep -E "daily_limit_reached|MAX_DAILY_L2_CALLS"
   ```
   If legitimate, bump `MAX_DAILY_L2_CALLS` in `mcp-server/docker-compose.yml`.

4. **Zero-result circuit clamping every pattern?**
   ```sql
   SELECT site_id, pattern_signature, COUNT(*)
     FROM l2_decisions
    WHERE runbook_id IS NULL
      AND created_at > NOW() - INTERVAL '24 hours'
    GROUP BY 1, 2
   HAVING COUNT(*) >= 2;
   ```
   Rows returned here are currently paused until UTC midnight. Consider
   raising `L2_ZERO_RESULT_CIRCUIT_THRESHOLD` if the LLM is producing
   legitimate "no action" responses that shouldn't be gating.

5. **Intentional maintenance?**
   If L2 is *meant* to be off, set `L2_ENABLED=false` in
   `mcp-server/docker-compose.yml` (and restart mcp-server). The invariant
   will go silent on the next 60s tick — no code change needed.

## Verification

- Panel: `/admin/substrate-health` — the invariant row should clear on the
  next 60s tick after the stall is resolved.
- CLI:
  ```sql
  SELECT COUNT(*), MAX(created_at) FROM l2_decisions
   WHERE created_at > NOW() - INTERVAL '48 hours';
  ```
  Expected: count ≥ 5 after a healthy run, or count = 0 + `L2_ENABLED=false`
  for intentional-off. Anything in between means the pipeline is partially
  wedged and the invariant should still be firing.

## Escalation

If none of the 5 walk-through steps produce a diagnosis, dump the recent
L2 activity to a file and escalate to a platform engineer:

```sql
\copy (
  SELECT created_at, site_id, pattern_signature, runbook_id, confidence,
         llm_model, llm_latency_ms, escalation_reason
    FROM l2_decisions
   WHERE created_at > NOW() - INTERVAL '7 days'
   ORDER BY created_at DESC
) TO '/tmp/l2_decisions_last7d.csv' WITH CSV HEADER;
```

A zero-row output means the planner code path never even *attempts* to
write a telemetry row — which narrows the bug to the `/api/agent/l2/plan`
endpoint or its callers in the Go daemon's healing loop.

## Related runbooks

- `flywheel_ledger_stalled.md` — sibling invariant. L2 decisions feed
  the flywheel, which promotes patterns into L1 rules. If both
  invariants fire together, start here (no L2 input → no flywheel
  output is the obvious chain).
- `evidence_chain_stalled.md` — unrelated system but shares the
  "something I expected to be running silently is not" class. If BOTH
  fire simultaneously, look for a common dependency (db pool, env
  regression, container restart that lost state).

## Change log

- **2026-04-24** — initial. Drafted during Session 210 as part of the
  L2 re-enable batch (docker-compose sets `L2_ENABLED=true` +
  `MAX_DAILY_L2_CALLS=100` for a conservative 72h soak). Tripwire
  first, infrastructure second.
