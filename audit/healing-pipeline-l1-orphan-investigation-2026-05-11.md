# L1-orphan root-cause investigation (2026-05-11) — REVISED

Phase 2A of the L1-orphan 3-phase plan. Read-only diagnostic. No code
change in this commit.

**Status:** REVISED after Gate B v1 BLOCK
(`audit/coach-l1-orphan-phase2a-diagnostic-gate-b-2026-05-11.md`) +
deep SQL+source trace
(`audit/coach-l1-orphan-net-ports-trace-2026-05-11.md`).

## TL;DR — ONE bug, TWO classes

Both orphan classes (`rogue_scheduled_tasks` + `net_unexpected_ports`)
share the SAME root cause:

**`Action: "escalate"` rules on the daemon fire a false-heal path.**

1. Daemon's built-in L1 ruleset has 9 `Action: "escalate"` rules
   (`builtin_rules.go` lines 161, 215, 712, 732, 823, 988, 1008,
   1028, 1048).
2. When one matches, `healing_executor.go:92-98` returns
   `{"escalated": true, "reason": ...}` with **NO `"success"` key**.
3. `l1_engine.go:327-334` defaults `result.Success = true` when the
   key is missing → silent escalate→heal promotion.
4. `daemon.go:1692` enters the success branch.
5. `daemon.go:1706` calls `ReportHealed(hostname, checkType, "L1", ruleID)`
   — `"L1"` is a hardcoded literal regardless of the action.
6. Backend `main.py:4870` persists `resolution_tier='L1'` via
   `body.get("resolution_tier", "L1")` with no validation. No
   `incident_remediation_steps` row written. → ORPHAN.

The CRITICAL bug is the missing `"success"` key in
`healing_executor.go:92` for the escalate case. The fix-of-record at
that line cascades through 9 rule definitions.

## Evidence

### Prod sample (north-valley-branch-2, past 2 days)

| incident_type | resolution_tier | has_step | count | rule action |
|---|---|---|---|---|
| `rogue_scheduled_tasks` | L1 | f | **49** | `escalate` (rule at builtin_rules.go:823) |
| `net_unexpected_ports` | L1 | f | **46** | `escalate` (rule at builtin_rules.go:988) |
| `defender_exclusions` | L1 | t | 31 | runbook (has step from agent_api.py:1240 dispatch) |
| `registry_run_persistence` | L1 | t | 31 | runbook (has step) |
| `windows_update` | L1 | t | 27 | runbook (has step) |
| `ransomware_indicator` | L2 | t | 2 | L2 path (different) |

The discriminator is **rule action type**, not monitoring-only-vs-not
(my v1 theory was wrong about that — `rogue_scheduled_tasks` has
`monitoring_only=false` per mig 157:46).

### Trace verification (from forked SQL+source investigation)

- All 5 sample `net_unexpected_ports` orphan IDs appear in
  `docker logs --since 24h mcp-server | grep "Incident resolved by type"`
  with caller IP `172.25.0.7` (WireGuard appliance).
- `"Incident resolved by type"` is emitted at exactly one source —
  `main.py:4899-4900`. So the resolver is `main.py:4835 /incidents/resolve`.
- Telemetry rows show `runbook_id=l1-drift-<host>-<check>-<ts>` —
  generated ONLY at `daemon.go:1702 ReportL1Execution`. So the daemon
  L1 engine fired.
- `order_id IS NULL` on all rows → rules out fleet-completion hook
  at `sites.py:2947` / `main.py` equivalent.
- No triggers on `incidents` table (verified via
  `information_schema.triggers`).
- `health_monitor.py` auto-resolve paths write `'monitoring'` not
  `'L1'` (verified by source-read).

### Ruled-out candidates (source-verified)

- `agent_api.py` router (dead per CLAUDE.md Session 213 P1).
- Fleet-completion hook (order_id null).
- `health_monitor.py` (writes monitoring).
- `evidence_chain.py:1502` (writes `'recovered'` not `'L1'`).
- `main.py:4807 /incidents/{id}/resolve` (different log event).
- Chaos-lab orchestrator (does NOT call back into backend).
- L2 cache-hit / planner re-classification paths.

## Callsite map

| File:Line | Action | Step write? | Issue |
|---|---|---|---|
| `agent_api.py:977-984` + `main.py:4236` | Monitoring-only creation → tier='monitoring', status='open' | NO | Correct |
| `agent_api.py:1240` | L1 dispatch → `result='order_created'` step INSERT | YES | Correct |
| `appliance/internal/healing/builtin_rules.go:980-999` | L1-NET-PORTS-001 `Action: "escalate"` | — | Correct rule definition |
| `appliance/internal/daemon/healing_executor.go:92-98` | Action executor returns `{escalated: true}` NO success key | — | **PRIMARY BUG** — missing `"success": false` |
| `appliance/internal/healing/l1_engine.go:327-334` | Defaults `Success=true` if key missing | — | **SECONDARY BUG** — should fail-closed |
| `appliance/internal/daemon/daemon.go:1706` | `ReportHealed(host, check, "L1", ruleID)` hardcoded | — | **TERTIARY BUG** — should derive from rule.Action |
| `appliance/internal/daemon/incident_reporter.go:170` | POST `/incidents/resolve` with daemon-supplied tier | — | Faithful messenger |
| `mcp-server/main.py:4835` `/incidents/resolve` (by-type) | `resolution_tier = body.get(..., "L1")` default | NO | **DEFENSIVE GAP** — trusts daemon for label |
| `mcp-server/main.py:4870` UPDATE | persists tier without checking monitoring-only registry | NO | **DEFENSIVE GAP** |

## Sibling builtin rules — empirical blast radius (90d prod data)

9 `Action: "escalate"` rules exist in `builtin_rules.go` (lines 161,
215, 712, 732, 823, 988, 1008, 1028, 1048). Gate B v2 P1 required
prod-grounding before Phase 3 — query result:

| check_type | L1 total | has_step | **orphan** | orphan % | monitoring-only? |
|---|---|---|---|---|---|
| `rogue_scheduled_tasks` (L823) | 650 | 140 | **510** | 78% | NO |
| `net_unexpected_ports` (L988) | 439 | 35 | **404** | 92% | YES |
| `net_host_reachability` (L1028) | 327 | 104 | **223** | 68% | YES |
| `net_dns_resolution` (L1048) | 1 | 1 | 0 | — | YES |
| `encryption` (L161) | 0 | — | — | — | — |
| `service_crash` (L215) | 0 | — | — | — | — |
| `net_expected_service` (L1008) | 0 | — | — | — | — |
| L712 + L732 (rule IDs not enumerated) | (not in 90d sample) | — | — | — | — |

**Total prod-observed orphans: 1,137** across 3 classes (rogue +
net_unexpected_ports + net_host_reachability). The other 6 escalate
rules either haven't fired in 90 days (chaos-lab doesn't inject those
attack types) or their orphan rate is below the sampling resolution.

Mig 306's UPDATE 2 IN-list MUST reflect this empirical set, NOT the
broader 9-rule theoretical set. Backfill IN-list:
- `('net_unexpected_ports', 'net_host_reachability')` → tier='monitoring' (monitoring-only)
- `('rogue_scheduled_tasks')` → tier='L3' (escalate-not-monitoring)

Substrate invariant `l1_resolution_without_remediation_step`
(Phase 1) catches new escalate-rule classes post-hoc; Phase 3 closes
the source for ALL escalate-action rules at the Layer 1 daemon fix.

## Phase 3 design

### Layer 1 (daemon, primary fix)

Edit `healing_executor.go:92-98` to return `success: false` on
escalate:

```go
case "escalate":
    return map[string]interface{}{
        "success":   false,
        "escalated": true,
        "reason":    reason,
    }, nil
```

Result: `l1_engine.go:328-334` sees `success: false`, sets
`result.Success = false`, `daemon.go:1692` does NOT enter the success
branch, `daemon.go:1706 ReportHealed` does NOT fire. The L2 cascade
path picks up the incident instead (current behavior for non-success
returns).

**Belt-and-suspenders:** also change `l1_engine.go:328` `else` branch
to default `Success = false` (fail-closed) so future actions that
forget the success key don't silently false-heal. Two changes; one
commit.

### Layer 2 (backend, defensive gate)

Edit `main.py:4870` UPDATE to refuse `resolution_tier='L1'` for
monitoring-only check types — downgrade to `'monitoring'`:

```python
# Defensive gate (Session 219 Phase 3, sibling of mig 300 L2 gate):
# the daemon's hardcoded "L1" tier for escalate-action rules
# (builtin_rules.go:92-98 → l1_engine.go:328 default Success=true →
# daemon.go:1706 hardcoded "L1") is corrected at Layer 1, but the
# backend defends against historical daemons + future regressions
# by downgrading L1 to 'monitoring' for check types the backend
# already treats as monitoring-only.
MONITORING_ONLY = await load_monitoring_only_from_registry(...)
if resolution_tier == "L1" and check_type in MONITORING_ONLY:
    resolution_tier = "monitoring"
```

### Phase 2B — mig 306 backfill (after Phase 3 lands)

Rewrite historical orphan rows under the corrected labeling regime:

```sql
-- 306_backfill_l1_orphans.sql
-- Per-class tier assignment based on the daemon rule's actual Action:
--   - monitoring-only check_type (per check_type_registry) → 'monitoring'
--   - escalate-action rule (rogue_scheduled_tasks, etc.) → 'L3'
--   - true L1 runbook orphan (none expected post-Phase 3) → 'L1'
--     + synthetic incident_remediation_steps row

-- Monitoring-only L1 orphans → 'monitoring'. Empirically grounded
-- (Gate B v2 P1, 2026-05-11 90d prod query): net_unexpected_ports
-- (404 orphans) + net_host_reachability (223 orphans). The IN-list
-- explicitly enumerates rather than subquery-joining the registry
-- to keep the backfill auditable + immune to future registry edits.
UPDATE incidents
   SET resolution_tier = 'monitoring'
 WHERE status = 'resolved'
   AND resolution_tier = 'L1'
   AND incident_type IN ('net_unexpected_ports', 'net_host_reachability')
   AND reported_at > NOW() - INTERVAL '90 days';

-- Escalate-action non-monitoring-only L1 orphans → 'L3'. Empirically
-- grounded: rogue_scheduled_tasks (510 orphans).
UPDATE incidents
   SET resolution_tier = 'L3'
 WHERE status = 'resolved'
   AND resolution_tier = 'L1'
   AND incident_type IN ('rogue_scheduled_tasks')
   AND reported_at > NOW() - INTERVAL '90 days';

-- Any residual L1 orphans get synthetic step rows (very few expected
-- post-Layer-1 + Layer-2 fix)
INSERT INTO incident_remediation_steps (incident_id, step_idx, tier,
    runbook_id, result, confidence, created_at)
SELECT i.id, 0, i.resolution_tier, 'L1-ORPHAN-BACKFILL-MIG-306',
       'backfill_synthetic', NULL, i.resolved_at
  FROM incidents i
  LEFT JOIN incident_remediation_steps irs ON irs.incident_id = i.id
 WHERE i.status = 'resolved'
   AND i.resolution_tier = 'L1'
   AND irs.id IS NULL
   AND i.reported_at > NOW() - INTERVAL '90 days'
ON CONFLICT DO NOTHING;
```

Mig 151 makes these rows IMMUTABLE — same caveat as Gate A v2 P0-2.
Ship Phase 3 first; mig 306 after Phase 3 in prod 24h with substrate
invariant showing orphan rate trending toward zero for new incidents.

## Sign-off

- **Author:** Phase 2A v2 (post-Gate-B-v1 + deep SQL+source trace).
- **Date:** 2026-05-11
- **Trace fork:** `audit/coach-l1-orphan-net-ports-trace-2026-05-11.md`
- **Gate B required:** YES per Gate A v2 P2-1.
- **Material change from v1:** the orphan class is ONE bug (escalate-
  action false-heal), not TWO separate races. Phase 3 design is now
  Layer-1-daemon + Layer-2-backend (both same commit OR sequenced).
