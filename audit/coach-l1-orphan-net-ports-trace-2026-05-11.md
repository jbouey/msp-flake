# net_unexpected_ports L1-orphan SQL trace (2026-05-11)

## Smoking gun

Two-source chain, not one:

1. **DAEMON SIDE — false-positive `result.Success=true` for escalate action:**
   - `appliance/internal/healing/builtin_rules.go:980-999` defines built-in L1 rule `L1-NET-PORTS-001` matching `check_type == "net_unexpected_ports"` with `Action: "escalate"`.
   - `appliance/internal/daemon/healing_executor.go:92-98` executes action `"escalate"` and returns `{"escalated": true, "reason": ...}, nil` — NO `"success"` key.
   - `appliance/internal/healing/l1_engine.go:327-334` parses the output: when `output["success"]` is missing, it falls through to the `else` branch and sets `result.Success = true`.
   - `appliance/internal/daemon/daemon.go:1692-1707` then enters the success branch and at **line 1706** calls `d.incidents.ReportHealed(req.Hostname, req.CheckType, "L1", match.Rule.ID)` — POSTs to `/incidents/resolve` with `resolution_tier="L1"` (hardcoded literal at the callsite).

2. **BACKEND SIDE — the resolver that writes the row:**
   - `mcp-server/main.py:4835-4901` — `@app.post("/incidents/resolve") resolve_incident_by_type()`. Line 4846: `resolution_tier = body.get("resolution_tier", "L1")`. Line 4867-4885: `UPDATE incidents SET resolved_at=NOW(), status='resolved', resolution_tier=:resolution_tier ...`.

The daemon supplies `"L1"` from `daemon.go:1706`; the backend persists it at `main.py:4870`. No `incident_remediation_steps` row is written by either path (the daemon emits `ReportL1Execution` telemetry but never POSTs a relational step for `escalate` actions; the backend resolve endpoint does not synthesize one).

## Evidence

### Prod DB — incident rows
```
id (5 sample)                          | type                 | tier | order_id | span_sec
170aa0bf-d779-449b-aac9-1a873d049887  | net_unexpected_ports | L1   | NULL     | 676
2a3fdc6d-f7db-46bc-b4f6-cdb8cc2e1777  | net_unexpected_ports | L1   | NULL     | 676
e66e3c95-0972-42a4-a06b-5cda2cde6aa3  | net_unexpected_ports | L1   | NULL     | 557
2190b561-5eeb-4206-9df6-44c218ffcb73  | net_unexpected_ports | L1   | NULL     | 496
4d92a3c7-0ef8-430a-9919-98c9aa850113  | net_unexpected_ports | L1   | NULL     | 496
```
- `order_id IS NULL` on all 5 → rules out the fleet-completion hook + `healing_executor.go:649`.
- `incidents` has no `updated_at` column; no history table. Trigger query returned 0 rows (no triggers on `incidents`).

### Prod logs — direct match of all 5 incident IDs
`docker logs --since 24h mcp-server | grep "Incident resolved"` shows each of the 5 sample IDs with caller IP `172.25.0.7` (WireGuard appliance) and log event `"Incident resolved by type"` — the literal string emitted by `main.py:4899-4900`. Example:
```
{"site_id":"north-valley-branch-2","host_id":"north-valley-branch-2-appliance",
 "check_type":"net_unexpected_ports","tier":"L1",
 "incident_id":"170aa0bf-d779-449b-aac9-1a873d049887",
 "event":"Incident resolved by type","timestamp":"2026-05-11T09:33:13.872Z"}
```
This eliminates `main.py:4807 /incidents/{id}/resolve` (different log event) and the `sites.py:3005` healing path (different code path, different log).

### Prod logs — telemetry confirms L1 engine fired
```
{"event":"Execution telemetry recorded:
  l1-drift-north-valley-branch-2-appliance-net_unexpected_ports-1778509857411-1778509857413
  (success=True)","timestamp":"2026-05-11T14:30:57.614Z"}
```
The runbook_id format `l1-drift-<hostname>-<check_type>-<ts1>-<ts2>` is generated only at `daemon.go:1702 ReportL1Execution` — confirms the L1 engine matched and the daemon path executed.

### Built-in L1 rule (daemon-local, NOT in DB)
```go
// builtin_rules.go:980-999
{ID: "L1-NET-PORTS-001",
 Conditions: [{check_type==net_unexpected_ports}, {drift_detected==true}],
 Action: "escalate",  // <-- this is the false-heal trigger
 Enabled: true, Source: "builtin"}
```
DB `l1_rules` table has separate rules with patterns `linux_open_ports` / `network_unexpected_ports` — neither matches `net_unexpected_ports`, so the appliance's `synced/l1_rules.json` would not catch this. The match comes from the **built-in compiled Go ruleset**.

### Action-executor output gap (the actual bug)
```go
// healing_executor.go:92-98
case "escalate":
    return map[string]interface{}{"escalated": true, "reason": reason}, nil
// No "success" key.

// l1_engine.go:328-334
if s, ok := output["success"]; ok { result.Success = bv }
else { result.Success = true }   // <-- silent escalate-to-heal promotion
```

## Verification

- **DB-side resolver confirmed by log-event match**: the literal log line `"Incident resolved by type"` is emitted at exactly one source — `main.py:4899-4900`. Each of the 5 sample IDs appears in that log line. Code path therefore = `main.py:4835-4901`.
- **Daemon-side caller confirmed by host_id + log telemetry**: host_id=`north-valley-branch-2-appliance` (the appliance itself, Linux) + telemetry `runbook_id=l1-drift-...` proves `daemon.go:1675` matched + `daemon.go:1692 result.Success` was true + `daemon.go:1706 ReportHealed("...","L1",...)` fired.
- **Built-in rule confirmed in source**: `builtin_rules.go:985 Value: "net_unexpected_ports"` is the only `check_type==net_unexpected_ports` rule in the entire appliance source tree (`grep -rn "net_unexpected_ports" appliance/internal/healing/`).
- **Ruled out**: `agent_api.py` router (dead per Session 213 P1); fleet-completion hook (`order_id IS NULL`); `health_monitor.py` auto-resolve (writes `'monitoring'` not `'L1'`); `evidence_chain.py:1502` (writes `'recovered'`); `main.py:4807` /id/resolve (different log event); chaos-lab orchestrator (not needed — daemon does it autonomously).

## Recommended fix

Two layers (defense in depth):

### Layer 1 (daemon — primary fix)
Treat action=`"escalate"` as NOT-HEALED. Two equivalent options:

**Option A — `healing_executor.go:92`**: return `success=false`:
```go
case "escalate":
    return map[string]interface{}{"success": false, "escalated": true, "reason": reason}, nil
```

**Option B — `l1_engine.go:295` Execute**: skip ReportHealed flow when `match.Rule.Action == "escalate"`, return an `ExecutionResult{Success: false, Escalated: true}` and have `daemon.go:1692` short-circuit to the L2 path instead. More surgical, fewer downstream effects, matches stated rule semantics ("escalate, don't claim heal").

Either fix prevents `daemon.go:1706 ReportHealed(..., "L1", ...)` from firing.

### Layer 2 (backend — defensive gate, sibling of Session 219 mig 300 L2 gate)
At `main.py:4870`, refuse `resolution_tier='L1'` when `check_type` is in the monitoring-only registry (already loaded at startup per CLAUDE.md). If monitoring-only, downgrade to `resolution_tier='monitoring'`. This matches the policy already enforced for the *creation* path at `main.py:4236`.

### Backfill
26 (or more — query for all sites/24h) orphan rows on `north-valley-branch-2` need `resolution_tier` rewritten from `'L1'` → `'monitoring'` to restore audit integrity. Mirror Session 219 mig 300 pattern: backfill migration with audit marker.

### Sibling builtin rules to audit
`builtin_rules.go` lines 161, 215, 712, 732, 823, 988, 1008, 1028, 1048 are all `Action: "escalate"` — every one of them is firing the same false-L1 path. The fix at Layer 1 closes all of them simultaneously.
