# Gate B — Task #65a: `canonical_metric_samples` → `partition_maintainer_dry` monitored-table list

**Date:** 2026-05-14
**Gate:** B (pre-completion), Class-B 7-lens, Session 220 two-gate lock-in
**Verdict:** **APPROVE-WITH-FIXES** — 3 P1 doc/string-drift sites the diff MISSED must be added in the same commit.

---

## 200-word summary

The as-implemented diff to `assertions.py::_check_partition_maintainer_dry` and `test_partition_maintainer_dry.py` is **functionally correct and complete**: the SQL `IN (...)` clause gains `canonical_metric_samples` as a 5th entry, `expected_patterns` correctly needs no change (mig 314 confirms `canonical_metric_samples_2026_05` — `_YYYY_MM` underscore-separated, already covered by `expected_patterns[0]`), the function docstring is updated, and all 4 test fixtures plus the two count-assertions (`==1` unaffected, `==5` updated, parent-set updated) move in lockstep. Full pre-push sweep: **254 passed, 0 failed**; `test_partition_maintainer_dry.py` 4/4 green.

**However — Session 220's insidious antipattern applies.** The diff updated what it touched (the function + its test) but MISSED three sibling surfaces that independently enumerate the monitored partitioned-table list and now drift to "4 tables":

1. `substrate_runbooks/partition_maintainer_dry.md` — operator runbook table + naming-conventions table + the SQL snippet in "Immediate action".
2. `assertions.py` line ~2232 — the `Assertion(...)` registration `description=` string.
3. `assertions.py` line ~2745 — the `_DISPLAY_METADATA["partition_maintainer_dry"]["recommended_action"]` string.

All three are operator/auditor-facing. None block the invariant from firing, hence P1 not P0 — but all must land in this commit.

---

## Pre-push test sweep (Session 220 mandatory)

```
bash .githooks/full-test-sweep.sh
→ 254 passed, 0 skipped (need backend deps), 0 failed

python3 -m pytest tests/test_partition_maintainer_dry.py -q
→ 4 passed in 0.12s
```

Diff-only review explicitly NOT relied upon — full sweep executed and cited per the Session 220 lock-in.

---

## Per-lens verdict

### Steve (Principal SWE) — APPROVE-WITH-FIXES
Core change is minimal and correct. SQL `IN` clause + docstring + fixtures all consistent. `expected_patterns` correctly untouched — `canonical_metric_samples` shares the `_YYYY_MM` shape with `compliance_bundles`/`portal_access_log`, so `expected_patterns[0]` (`f"{next_y}_{next_m:02d}"`) already matches. The `child.relname NOT LIKE '%_default%'` filter is harmless for `canonical_metric_samples` (it has no `_default` partition) — it just never excludes anything for that parent, which is fine. **P1:** the `Assertion` `description` string (line ~2232) still reads "(compliance_bundles, portal_access_log, appliance_heartbeats, promoted_rule_events)" — 4 tables. Update to 5.

### Maya (HIPAA/Legal) — APPROVE
Verified mig 314 on disk: partition children are `canonical_metric_samples_2026_05/_06/_07` — `_YYYY_MM` underscore-separated, exactly as the diff's docstring and fixtures claim. mig 314 explicitly notes "no `_default` partition" — which is precisely why this table belongs in a sev1 outcome-layer invariant (next-month INSERT fails outright, not just bloats). No PHI surface: `canonical_metric_samples` holds metric-class numeric samples + endpoint paths, no patient data; adding it to an operator-facing invariant changes nothing about the PHI boundary. The sev1 severity is inherited from the existing invariant — appropriate, and arguably this table is the *most* severe member (hard INSERT failure vs. `_default` cushion for the other 4). No §164.528 disclosure implication.

### Carol (Security) — APPROVE
No new auth surface, no new query parameterization, no injection vector — `'canonical_metric_samples'` is a string literal in a static `IN` list. The invariant runs under the existing substrate `admin_transaction` per-assertion context (Session 220 cascade-fail closure) — unchanged. No secret/credential exposure.

### Coach (Consistency) — APPROVE-WITH-FIXES — **primary finding**
Sibling-consistency probe is where this diff falls short. The naming-convention claim checks out: `appliance_heartbeats` = `_yYYYYMM`, `promoted_rule_events` = `_YYYYMM`, and `canonical_metric_samples` genuinely uses `_YYYY_MM` (underscore-separated, distinct from the other two) — verified against mig 314, and the test fixtures use the exact form `canonical_metric_samples_2026_05`. **But three sibling enumerations of the monitored-table list were not updated:**
- **P1-a:** `substrate_runbooks/partition_maintainer_dry.md` — the "What this means" table lists 4 tables; the "Naming conventions" table lists 4; the "Immediate action" SQL snippet's `IN (...)` lists 4. An operator following this runbook during an incident would not know `canonical_metric_samples` is monitored, and the copy-paste diagnostic SQL would silently omit it.
- **P1-b:** `assertions.py` `Assertion(description=...)` line ~2232 — enumerates 4 tables.
- **P1-c:** `assertions.py` `_DISPLAY_METADATA[...]["recommended_action"]` line ~2745 — enumerates 4 tables ("compliance_bundles / portal_access_log / appliance_heartbeats / promoted_rule_events"). This string is what renders on the `/admin/substrate-health` panel.

This is exactly the Session 220 "Gate B audits what the diff touched, not what's missing" pattern. None are functional defects — the invariant fires correctly — but all are operator/auditor-facing drift and must land in the same commit.

`schema_fixture_drift.md` line 84 references `partition_maintainer_dry.md` only generically ("partition operations are a common place for fixture drift") — no table enumeration there, no drift, no fix needed.

### Auditor — APPROVE
Confirmed this is an **extension of an existing invariant**, not a new one. `partition_maintainer_dry` already exists in the `ALL_ASSERTIONS` list (line ~2230) and in `_DISPLAY_METADATA` (line ~2745). No new `Assertion(...)` entry, no new `_DISPLAY_METADATA` key — correct, the diff does not add either, it should only *edit* the existing strings (which it currently does not — see P1-b/P1-c). The invariant remains `name="partition_maintainer_dry"`, `severity="sev1"` — unchanged. Test coverage is real: 4 tests open against `_FakeConn`, the `==5` count assertion and the parent-set assertion both genuinely exercise the 5th table.

### PM — APPROVE
Scope is tight and matches Task #86/#65a as written. Closes the real gap: Task #65's `canonical_metric_samples_pruner` (commit a4a9069d) is the loop that, if wedged, leaves `canonical_metric_samples` without next-month partitions — and unlike the other 4 tables, mig 314 gave it no `_default`, so a wedge = hard INSERT failure. This invariant is the correct outcome-layer catch. The 3 P1 doc fixes are in-scope cleanup, not scope creep — they belong in the same commit.

### Counsel (7 hard rules) — APPROVE
Rule 1 (no non-canonical metric leaves the building): `canonical_metric_samples` IS the Rule-1 runtime-sampling table; protecting its partition health is directly Rule-1-supportive. Rule 5 (no stale doc outranks current posture): **this is exactly why P1-a is not optional** — leaving `partition_maintainer_dry.md` enumerating 4 tables makes the runbook a stale doc the moment this commit lands. Counsel Rule 5 compliance *requires* the runbook update in the same commit. No other rule implicated.

---

## MISSING-additions found (Session 220 probe)

| # | Sev | Location | Drift |
|---|-----|----------|-------|
| P1-a | P1 | `substrate_runbooks/partition_maintainer_dry.md` | "What this means" table, "Naming conventions" table, and "Immediate action" SQL snippet all still list 4 tables. Add `canonical_metric_samples` row (mig 314, `_YYYY_MM`, "Counsel Rule 1 runtime sampling") to both tables; add `'canonical_metric_samples'` to the SQL `IN (...)`. Note in the table that it has no `_default` partition. |
| P1-b | P1 | `assertions.py` ~line 2232 | `Assertion(description=...)` string enumerates 4 tables. Add `canonical_metric_samples`. |
| P1-c | P1 | `assertions.py` ~line 2745 | `_DISPLAY_METADATA["partition_maintainer_dry"]["recommended_action"]` string enumerates 4 tables — renders on `/admin/substrate-health` panel. Add `canonical_metric_samples`. |

No P0s. No missing test coverage. No missing schema/migration work (mig 314 already shipped the table; this task only extends the monitored list).

Checked and clean: `expected_patterns` correctly unchanged; `schema_fixture_drift.md` reference is generic (no drift); no `ALL_ASSERTIONS` registration change needed (extending, not adding); no `_DISPLAY_METADATA` *key* addition needed (extending the existing key's value string only).

---

## Final verdict: APPROVE-WITH-FIXES

The functional core (assertions.py SQL + docstring, test fixtures + count assertions) is correct, complete, and sweep-green (254/254). Three P1 operator/auditor-facing string/doc enumerations of the monitored-table list were MISSED by the diff and must be updated **in the same commit** before Task #65a is marked complete — per Session 220 (Gate B audits what's missing, not just what's touched) and Counsel Rule 5 (no stale doc outranks current posture). No P0s; no carry-forward TaskCreate needed since all 3 fixes are same-commit edits.

Commit body must cite both Gate A (`audit/coach-65a-partition-dry-table-list-gate-a-2026-05-14.md`, APPROVE-WITH-FIXES) and this Gate B verdict.
