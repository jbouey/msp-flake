# P1 Persistence-Drift / L2 Routing Drift — Research

**Date:** 2026-05-12
**Scope:** 320 L1 resolutions in 7d for `windows_update` / `defender_exclusions` / `rogue_scheduled_tasks` (`incidents.resolution_tier='L1'`), **zero** `l2_decisions` rows with `escalation_reason='recurrence'`.

## Prod verification (VPS, mcp DB)

```
incident_type         | tier | count  (7d)
windows_update        | L1   | 69
defender_exclusions   | L1   | 93
rogue_scheduled_tasks | L1   | 158
                      Σ=320

l2_decisions 7d escalation_reason histogram:
  l1_failed_fallback | 13
  backfill           | 10
  normal             | 2
  recurrence         | 0   ← the bug
```

`incident_recurrence_velocity` (the dashboard rollup) **correctly** lights `is_chronic=TRUE` for all 3 check_types on `north-valley-branch-2` (4h counts: defender 5, rogue 4, win_update 3). The velocity loop sees the recurrence. The in-flight detector does not.

## Where the routing breaks

**File:** `mcp-server/central-command/backend/agent_api.py`
**Two callsites, same bug:**
- Line **1014-1024** (`report_incident` — new-incident branch)
- Line **823-833** (`report_incident` — dedup-reopen branch)

Both detector queries filter:

```sql
SELECT COUNT(*) FROM incidents
WHERE appliance_id = :appliance_id      -- ← granularity bug
  AND incident_type = :incident_type
  AND status = 'resolved'
  AND resolved_at > NOW() - INTERVAL '4 hours'
```

…then gate `if recurrence_count >= 3` to bypass L1 + populate `recurrence_context` + force the L2 path with `escalation_reason="recurrence"`.

## Why it breaks

**Single-site multi-appliance partitioning.**  `north-valley-branch-2` has **3 registered daemons** (`c61c06c6…`, `7fa6b2c6…`, `5b9f5cee…`) — chaos-lab VMs each running independent driftscans. All three scan the **same target host** (`192.168.88.251`). When defender_exclusions drift fires, all three daemons independently report `incident_type='defender_exclusions'` to `POST /incidents`, each tagged with its **own** `appliance_id`.

The recurrence detector's `WHERE appliance_id = :appliance_id` slices the count per-daemon. Per-daemon-per-4h is typically 1-2 (resolutions are spaced ~hourly), so the `>= 3` threshold **never trips**. The L1 path runs, fix is applied, incident resolved, dedup_key (`SHA256(site_id:incident_type:hostname)`) is the same across daemons → next time another daemon opens the same incident, dedup REOPEN-path runs the same per-appliance count → still under threshold.

Meanwhile `background_tasks.py::recurrence_velocity_loop` (line 1154) GROUPs BY **`i.site_id, i.incident_type`** — aggregating across all 3 daemons — and correctly flags `is_chronic=TRUE`. The two code paths have inconsistent granularity. **The flywheel's dashboard signal is right; the flywheel's routing signal is wrong.**

Secondary contributing fact (not the bug itself): line 1018 uses `appliance_id` even when the actual remediation is dispatched against a `hostname` field that's identical across all 3 daemons. Same root: `incidents` is keyed by daemon, but the recurrence concept is per-site-per-target.

## Persistence runbooks — wiring status (not the issue)

`RB-WIN-PERSIST-001`, `RB-WIN-PERSIST-002` are L2 planner picks (`l2_planner.py:634/641`). `L1-PERSIST-TASK-001` is a Go-daemon builtin (`appliance/internal/healing/builtin_rules.go:616`) that maps `check_type='scheduled_task_persistence'` (a different check type from `rogue_scheduled_tasks`) → `RB-WIN-SEC-018`. They exist + enabled + correctly tagged with "for recurring issues use RB-WIN-PERSIST-001". **They are wired correctly.** The L2 planner never gets called for these incidents because the recurrence gate that routes to L2 never fires.

## Recommended fix shape

**One-line granularity change, not a redesign.** The recurrence detector at agent_api.py:1014 + 823 should aggregate by `(site_id, incident_type)` not `(appliance_id, incident_type)`. Pull recurrence facts from the already-computed `incident_recurrence_velocity` table (single SELECT, no COUNT) rather than running a fresh `COUNT(*)` on every incident open. This also closes the cross-daemon partitioning class and removes the per-incident O(n) scan over `incidents`.

Detector becomes a read of `incident_recurrence_velocity.resolved_4h` and `.is_chronic` keyed on `(site_id, incident_type)`. The velocity loop's 5-min freshness is acceptable for L2-routing — it's already what the customer-facing chronic dashboard trusts.

A second, narrower defense: when ≥2 daemons share a site, the recurrence count for a target should aggregate by `details->>'hostname'` too (cross-daemon dedup_key matches already prove the target host is the same).

## Risk if left unfixed

- **Customer-facing flywheel SLA gap.** Dashboard shows "chronic" + "is_chronic=TRUE" but the system never escalates to L2. The data-flywheel narrative in `CLAUDE.md` ("L1 70-80% → L2 15-20% → L3 5-10%" + "promote L2→L1") cannot run for the 3 most-recurrent check_types — auditor question "show me the L2 root-cause analysis for the 320 L1 resolutions you logged" returns empty.
- **Auto-promotion pipeline dead.** `recurrence_auto_promotion_loop` (background_tasks.py:1199) filters `WHERE d.escalation_reason = 'recurrence'`. Zero rows in → zero L1 promotions out. The flywheel **cannot learn** for this class.
- **Persistence runbooks have no live data.** `RB-WIN-PERSIST-001/002` are L2 planner picks; with zero recurrence escalations, the planner never sees the "L1 keeps failing" context that selects them over the symptom-fix runbooks. The library reads `enabled=true` but production traffic = 0.
- **Single-customer (north-valley-branch-2) issue scales with every new multi-appliance site.** Federation tier work (F6 MVP) and the substrate's multi-appliance-per-site posture make this latent on every customer with ≥2 daemons.
- **Substrate invariant gap.** Session 219 mig 300 added `l2_resolution_without_decision_record` (catches L2-without-audit-row). No symmetric invariant catches `chronic_without_l2_escalation`. New invariant would close the audit-visibility class.

## Highest-leverage NEXT actions (ranked)

1. **Switch detector to read `incident_recurrence_velocity` by `(site_id, incident_type)`** at `agent_api.py:1014` AND `:823`. ~15 lines of code, removes the per-incident `COUNT(*)` and closes the multi-daemon class in one move. Add a fresh-data guard: require `computed_at > NOW() - INTERVAL '10 minutes'` to avoid acting on stale velocity rows during loop downtime. Gate B fork required (Steve: race when velocity loop is mid-tick; Maya: §164.528 audit-trail unchanged; Carol: still per-tenant; Coach: full pre-push sweep).
2. **Backfill the 320 L1 resolutions worth of missed L2 root-cause analyses** — run `record_l2_decision()` with `escalation_reason='recurrence_backfill'` + `llm_model='backfill_synthetic'` for every `incident_recurrence_velocity` row currently `is_chronic=TRUE` (3 rows today, ~9 unique site-incident-type tuples). Migration pattern identical to Session 219 mig 300 (synthetic decisions distinguishable in audit). Maya §164.528 review on retroactive-disclosure-accounting impact.
3. **Add substrate invariant `chronic_without_l2_escalation` (sev2)** — assert `NOT EXISTS (velocity row with is_chronic=TRUE AND last computed_at > NOW() - 1h that has no matching l2_decisions row with escalation_reason='recurrence' in last 24h)`. Closes the class permanently and gives Gate B verifiable runtime evidence on every future commit.
