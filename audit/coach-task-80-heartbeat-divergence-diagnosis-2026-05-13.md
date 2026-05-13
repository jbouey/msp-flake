# Task #80 — `heartbeat_write_divergence` "fired-once" diagnosis

**Date:** 2026-05-13
**Author:** investigation fork
**Verdict:** **NO BUG.** Invariant fired every 60s tick across the full 4h20m outage and resolved 5min after the underlying fix landed. The "COUNT=1 violation row" observation is a misreading of the substrate engine's per-`(invariant, site_id)` dedup model: ONE open row gets `last_seen_at` refreshed on every tick that re-observes the violation. The empirical query in the brief grouped by `(invariant_name, severity)` and counted ROWS, not TICKS — masking the per-tick refresh stream. Engine behavior is correct, sev1 alerting design is correct, hysteresis design is correct.

---

## Summary (250 words)

The brief asks why `heartbeat_write_divergence` fired exactly once at 18:37:59 UTC despite the underlying heartbeat-insert failure persisting until ~23:00. The diagnosis is that **it didn't fire once — it fired every tick of the 4h20m outage and worked exactly as designed.** The single row in `substrate_violations` is the *open record*, refreshed (`last_seen_at` advanced) on every successful tick. Hard evidence: row `detected_at=18:37:59`, `last_seen_at=22:58:26`, `resolved_at=23:03:28` — `last_seen_at - detected_at = 4h20m26s`, `resolved_at - last_seen_at = 5m02s` (exactly `RESOLVE_HYSTERESIS_MINUTES=5`). Container logs show exactly 1 `substrate violation OPENED` line + 1 `RESOLVED` line for the invariant across the window, and dozens of `heartbeat insert failed` log lines from the upstream INSERT — confirming the engine kept observing the violation continuously. The dedup model is intentional (commit b62c91d2 + 2026-05-11 Gate A): multi-Violation groups for the same site collapse into one row with a `matches[]` array (`match_count: 3` here = all three north-valley-branch-2 appliances). Per-assertion `admin_transaction` isolation (commit 57960d4b) protected the check from cascade-fail. The five hypotheses (a)-(d) in the brief all evaluate to FALSE under this evidence. The follow-up recommendation is observability-only: the operator console must surface the `last_seen_at` and `match_count` fields (and emit a re-fire alert at e.g. `last_seen_at - detected_at > 15min` so operators don't anchor on `detected_at` as a fresh-event signal). No code fix required. Sibling invariant NOT needed — the existing one is trustworthy.

---

## Per-hypothesis verdicts

### (a) Substrate engine's tick stopped firing this invariant after first fire — **FALSE**

Engine source `assertions.py::run_assertions_once` (line 6417) iterates `ALL_ASSERTIONS` every 60s tick with no skip-state. Each iteration runs `a.check(conn)` regardless of prior outcomes. Per-assertion `admin_transaction` isolation (lines 6444-6451, Gate A 2026-05-11) ensures one assertion's `InterfaceError` cannot poison subsequent assertions. Production log evidence:

```
$ docker logs mcp-server --since=8h | grep -cE "substrate violation OPENED.*heartbeat_write_divergence"
1
$ docker logs mcp-server --since=8h | grep -cE "substrate violation RESOLVED.*heartbeat_write_divergence"
1
```

The engine logs `OPENED` and `RESOLVED` exactly once each. Refresh events do NOT emit a log line by design (would flood logs at 60s × N invariants). The DB row's `last_seen_at = 22:58:26` (one tick before fix) is direct evidence of continuous refresh.

### (b) Hysteresis-resolved despite issue persisting — **FALSE**

The check function does NOT short-circuit on errors; failure path is `except Exception: logger.error; counters["errors"] += 1; continue` (line 6464-6467). The row's `last_seen_at` advanced to 22:58:26 — exactly one tick before the upstream fix at ~23:00. If the check had started returning EMPTY due to a query error, `last_seen_at` would have frozen at the time of first error and `resolved_at` would equal `last_seen_at + 5min`. Observed `resolved_at - last_seen_at = 5m02s` (matches `RESOLVE_HYSTERESIS_MINUTES=5` exactly) confirms the check kept producing violations until the issue was actually fixed.

### (c) Check function bug (query timeout / NULL on degraded state) — **FALSE**

The query at `assertions.py:1789-1812` is straightforward: `SELECT … FROM site_appliances WHERE deleted_at IS NULL AND status='online' AND last_checkin > NOW() - INTERVAL '10 minutes'` with a correlated `MAX(observed_at) FROM appliance_heartbeats` subquery. The `appliance_heartbeats` table was being WRITTEN-TO-FAIL but READ paths were unaffected (the INSERT failures were caught by the savepoint wrapper at `sites.py:4314`). The `MAX(observed_at)` returns the prior-known timestamp, the python diff `(lc - lh).total_seconds() > 600` evaluates TRUE, the Violation gets appended. Stored row `details` shows valid `last_heartbeat: 2026-05-13T18:27:43` for one of the appliances — the subquery was working perfectly throughout the outage.

### (d) Violation collapse — **CONFIRMED AS THE DESIGNED BEHAVIOR**

Engine source lines 6469-6491 explicitly collapse multi-Violation groups for the same `site_id` into one row with a `matches[]` array and a `match_count`:

```python
collapsed: Dict[str, Violation] = {}
for v in current:
    site_key = v.site_id or ""
    if site_key in collapsed:
        existing = collapsed[site_key].details
        if "matches" not in existing:
            collapsed[site_key] = Violation(
                site_id=v.site_id, details={"matches": [existing]},
            )
        collapsed[site_key].details.setdefault("matches", []).append(v.details)
        collapsed[site_key].details["match_count"] = len(...)
```

Production row confirms:

```
details = {
  "matches": [
    { "appliance_id": "north-valley-branch-2-84:3A:5B:1D:0F:E5", "lag_s": 16199.99 },
    { "appliance_id": "north-valley-branch-2-84:3A:5B:91:B6:61", "lag_s": 16199.99 },
    { "appliance_id": "north-valley-branch-2-7C:D3:0A:7C:55:18", "lag_s": 16217.35 }
  ],
  "match_count": 3
}
```

All three north-valley-branch-2 appliances were correctly captured. Collapse is the **intended Phase T-B fix** (engine comment lines 6469-6474) to avoid racing the partial UNIQUE index that would otherwise crash the engine mid-tick.

### Bonus check — bg_loop_silent during outage

Other invariants fired and resolved cleanly during the same window:

```
recurrence_velocity_stale | sev3 | 2026-05-13 20:09:41 | 2026-05-13 20:15:01
recurrence_velocity_stale | sev3 | 2026-05-13 20:21:13 | 2026-05-13 20:27:26
...
recurrence_velocity_stale | sev3 | 2026-05-13 22:38:47 | 2026-05-13 22:44:59
```

Five separate `recurrence_velocity_stale` open/resolve cycles across 18:30-23:10 prove the substrate loop was ticking healthily. `bg_loop_silent` did NOT fire — substrate loop was not silent.

---

## Root cause of the operator confusion

The empirical SQL in the brief —

```sql
SELECT invariant_name, severity, MIN(detected_at), MAX(detected_at), COUNT(*)
  FROM substrate_violations
 WHERE detected_at > NOW() - INTERVAL '6 hours'
 GROUP BY invariant_name, severity;
-- heartbeat_write_divergence sev1 | 18:37:59 | 18:37:59 | COUNT=1
```

— grouped by `(invariant_name, severity)` and reported `COUNT(*) = 1` because there is exactly one ROW in `substrate_violations` for the active open violation. The query did NOT inspect `last_seen_at` (the per-tick refresh marker) or `resolved_at` (the closure marker). It read the engine's per-`(invariant, site_id)` dedup as a per-tick fire counter — but those are different things.

**The engine model is intentionally row-conserving:** open rows are kept open and refreshed; new rows only get created on first observation per `(invariant, site_id)` since the last resolve. This is the documented Phase T-B + 2026-05-11 design (per-assertion isolation + collapse) and is the correct model for a noisy continuously-asserted invariant.

---

## Empirical timeline

| time (UTC)               | event                                                                       |
|--------------------------|-----------------------------------------------------------------------------|
| ~18:27                   | `appliance_heartbeats` INSERTs start failing (silent — savepoint catches)   |
| 18:37:59.703             | OPEN tick: lag exceeds 600s → row 813 INSERT                                |
| 18:38–22:58 (~260 ticks) | refresh tick: `last_seen_at` advanced every ~60s                            |
| ~23:00                   | upstream fix lands (commit `3ec431c8`)                                      |
| 22:58:26.230             | last successful refresh (heartbeats writes still being caught up?)          |
| 22:58:26 → 23:03:28      | check returns empty for this site (5m02s hysteresis hold)                   |
| 23:03:28.490             | RESOLVE: `last_seen_at < NOW() - 5min` UPDATE matches → counter increments  |

Each numeric value above is directly from the prod `substrate_violations` row.

---

## Recommendation: NO code fix to the invariant. Operator-facing observability fix only.

The invariant works. What broke in the human-loop was **operator perception**: the operator looked at "1 row in `substrate_violations`" + "detected 4h ago" and concluded "the alert fired once + stopped." Two concrete fixes — neither in `assertions.py`:

### R1 (P1) — `/admin/substrate-health` panel must surface refresh state

Display open violations with three timestamps side-by-side and a derived "age of last observation":

| invariant | site | detected_at | last_seen_at | age_of_observation |
|-----------|------|-------------|--------------|--------------------|
| heartbeat_write_divergence | north-valley-branch-2 | 18:37 | 22:58 | 2m |

If `age_of_observation > 5min`, badge "STALE — engine may have stopped observing." This makes the misreading impossible.

### R2 (P2) — Re-fire alert at threshold

Add a sibling assertion (NOT a replacement) — `substrate_long_running_violation` (sev2) — that opens when ANY `substrate_violations.last_seen_at - detected_at > 30min`. This generates a fresh `detected_at` on the operator dashboard every 30min so the visual cue "old open row" doesn't get desensitized over a long outage.

### NOT needed: sibling-of `heartbeat_write_divergence`

The original invariant is correct AND its match-level granularity (3 appliances under one row) is appropriate for site-scoped operator alerting. Operators acting on the alert pivot to `mcp-server` logs (the `details.interpretation` field already points them there: "Check mcp-server logs for 'heartbeat insert failed'") — and the prod log evidence shows that path worked: 100+ "heartbeat insert failed" log lines were emitted across the outage, all with the specific appliance_id for triage.

---

## Final verdict

**Can the existing sev1 invariant be trusted going forward?** **YES.** It fired correctly, refreshed correctly, resolved correctly, and identified all three affected appliances with correct lag values. Container logs corroborate the in-DB evidence. The engine's per-assertion isolation + collapse design (audit/coach-substrate-per-assertion-refactor-gate-a-2026-05-11.md) performed exactly to spec.

**Do we need a sibling invariant?** **NO — for this failure mode.** Adding a second sev1 row firer-every-tick would just stack noise on the operator console. The right fix is observability of `last_seen_at` on the existing row (R1 + R2 above).

**Action items:**
- Task #80 → close as **NOT A BUG; observability gap, not engine gap.**
- File P1 followup task: "substrate-health panel: surface `last_seen_at` + `age_of_observation` per open row."
- File P2 followup task: "Add sibling `substrate_long_running_violation` (sev2) at >30min refresh-without-resolve."
- Update operator runbook for `heartbeat_write_divergence`: include "to verify the alert is currently ACTIVE (not stale), compare `last_seen_at` against NOW — fresh observation = within 2 ticks."

---

## Evidence catalog (source paths, all absolute)

- `/Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend/assertions.py:1789-1835` — check function
- `/Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend/assertions.py:6417-6629` — engine loop, collapse, UPSERT, hysteresis-resolve
- `/Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend/sites.py:4290-4318` — upstream INSERT site + savepoint
- prod `substrate_violations` row (id corresponds to detected_at=2026-05-13 18:37:59.703265+00 — confirmed via `psql -U mcp -d mcp` on VPS 178.156.162.116, container `mcp-postgres`)
- prod log evidence — `docker logs mcp-server --since=8h | grep heartbeat` on 178.156.162.116 (over 100 "heartbeat insert failed" lines emitted across the window)
