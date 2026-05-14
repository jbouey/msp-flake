# Gate B — Task #87: Fold `prod_column_widths.json` regen into the combined schema-fixture command

**Date:** 2026-05-14 | **Gate:** B (pre-completion) | **Lenses:** Steve / Maya / Carol / Coach / Auditor / PM / Counsel
**Reviewer:** fork (fresh context) | **Branch:** main (uncommitted diff, 5 tracked files)

## 200-word summary

Full pre-push sweep: **256 passed, 0 skipped — clean.** All 5 changed files are git-tracked (no new files; `prod_column_widths.json` already existed at `ad2f3281`), so the git-tracked-parity gate is satisfied without staging.

The AS-IMPLEMENTED diff is **correct and low-risk**. Steve: the docstring's single-richer-query regen command was simulated against the SQL output shape and **deterministically reproduces all three committed fixtures byte-for-byte** — no bug in the `typed/names/widths` post-step. Maya: the 330→331 / 231→232 drift is the single benign partition table `canonical_metric_samples_2026_08` (columns + the `v_l2_outcomes` view in widths); `SELECT_BASELINE_MAX` stays at **10** and `test_baseline_doesnt_regress_silently` passes exactly — zero new SELECT violations. Coach/Maya: `test_check_constraint_fits_column.py` passes with refreshed widths and is already in pre-push SOURCE_LEVEL_TESTS. Carol/Auditor/Counsel: widths fixture is integers-only (1369 entries), pure schema metadata, no row data, no PHI surface — N/A confirmed. PM: ~2.5min implementation window.

**One real gap (P1) + two doc nits (P2):** `test_both_fixtures_exist()` was NOT extended to assert `_WIDTHS.is_file()`; two docstrings retain stale "the two" / "BOTH fixtures" language now that there are three sidecars.

**Verdict: APPROVE-WITH-FIXES.**

---

## Full pre-push sweep

```
$ bash .githooks/full-test-sweep.sh
✓ 256 passed, 0 skipped (need backend deps)
```

Diff-scoped consumer/parity run (independent verification):
```
$ python3 -m pytest tests/test_schema_fixture_parity.py tests/test_sql_columns_match_schema.py tests/test_check_constraint_fits_column.py -q
12 passed in 4.33s
```

Git-tracked-parity: all 5 changed files (`prod_columns.json`, `prod_column_types.json`, `prod_column_widths.json`, `test_schema_fixture_parity.py`, `test_sql_columns_match_schema.py`) are tracked. `git cat-file -e ad2f3281:.../prod_column_widths.json` confirms widths.json pre-existed — #77's Gate B P1 was "not in the *combined regen*", not "doesn't exist". No staging needed; gate clean.

---

## The 330→331 drift assessment

| Fixture | HEAD | Working | Delta |
|---|---|---|---|
| `prod_columns.json` | 330 | 331 | +`canonical_metric_samples_2026_08` |
| `prod_column_types.json` | 330 | 331 | +`canonical_metric_samples_2026_08` |
| `prod_column_widths.json` | 231 | 232 | +`v_l2_outcomes` (view) |

`git diff ad2f3281 -- prod_columns.json` shows exactly one new table key. The new table is **`canonical_metric_samples_2026_08`** — a monthly partition of the `canonical_metric_samples` table (Task #65, mig-series). Columns: `sample_id uuid, captured_at timestamptz, captured_value numeric, classification text, endpoint_path text, helper_input jsonb, metric_class text, site_id text, tenant_id uuid`. This is a **benign auto-created partition**, not a hand-rolled table; it is internal metrics infrastructure, not referenced by customer-facing SQL queries. The widths delta `v_l2_outcomes` is a view exposing length-constrained varchar columns — correctly subset-only.

**Did it move `SELECT_BASELINE_MAX`?** No. It is `10` at HEAD and `10` in the working tree (set by #77 Phase B-lite, untouched by #87). `test_baseline_doesnt_regress_silently` asserts `sel == 10` *exactly* and **PASSED** — the 331st table introduced **zero** new SELECT/INSERT/UPDATE schema-mismatch violations. No P1 here, unlike #77's `client_owner_transfer` bump. Benign drift, fully absorbed.

---

## Per-lens verdict

### Steve — regen command correctness — PASS
Simulated the docstring's regen Python post-step against a reconstructed `d` in the exact SQL output shape `{table: {col: [data_type, character_maximum_length]}}`. Result: `typed`, `names`, `widths` all reproduce the three committed fixtures byte-identically under `json.dumps(..., indent=2, sort_keys=True)`. Trace verified:
- `typed[t] = {c: v[0] ...}` → element 0 = `data_type` ✓
- `names[t] = sorted(cols.keys())` → column-name list ✓
- `w = {c: v[1] ... if v[1] is not None}` + `if w:` → only length-constrained columns, empty tables skipped ✓
- SQL `json_build_array(data_type, character_maximum_length)` matches the `v[0]`/`v[1]` indexing.
Copy-pasting the docstring command against clean prod **would** reproduce the committed fixtures. No bug.

### Maya — drift / baseline integrity — PASS
330→331 drift is the single benign `canonical_metric_samples_2026_08` partition. `SELECT_BASELINE_MAX` unchanged at 10. `test_sql_columns_match_schema.py` passes including `test_baseline_doesnt_regress_silently` (exact-match ratchet). No new violation class. `test_check_constraint_fits_column.py` (widths consumer) passes with refreshed fixture — Gate A's CLEAN claim independently verified.

### Carol — N/A (confirmed)
Test infrastructure + schema metadata only. No tenancy/RLS surface. `prod_column_widths.json` verified integers-only (1369 entries, all `int` type) — `character_maximum_length` values, no row data.

### Coach — load-bearing MISSING-additions probe — ONE GAP FOUND (P1)
- **(a) GAP — P1:** `test_both_fixtures_exist()` (line 38) still asserts only `_NAMES.is_file()` and `_TYPES.is_file()`. It was NOT extended to assert `_WIDTHS.is_file()`. The `_WIDTHS` constant *is* defined (line 31) and used by the new `test_widths_fixture_is_subset_of_columns`, so a deleted widths file would still be caught (by `_load` erroring) — but the *dedicated existence guard* has a hole, and its name `test_both_fixtures_exist` is now semantically stale ("both" → "all three"). This is exactly the Session-220 "did the diff MISS anything" antipattern: the implementer added the constant and the new test but skipped harmonizing the sibling existence gate. **Fix:** add `assert _WIDTHS.is_file()` and rename to `test_all_fixtures_exist` (or leave name, add the assert — minimum bar is the assert).
- **(b) RESOLVED:** `test_check_constraint_fits_column.py` (the widths consumer) is already in `.githooks/pre-push` SOURCE_LEVEL_TESTS line 143; `test_schema_fixture_parity.py` (126) and `test_sql_columns_match_schema.py` (142) also present. Consumer chain covered.
- **(c) GAP — P2:** stale "two files" language in two docstrings — `test_schema_fixture_parity.py:10` ("the two ever diverge") and `test_sql_columns_match_schema.py:59` ("Edit BOTH fixtures in the same diff"). Both predate #87 and now under-count the sidecars. Mode-(b) forward-merge instructions should say "all three (keeping names↔types parity, widths ⊆ names)".

### Auditor — N/A (confirmed)
Schema metadata, no audit-chain or evidence-bundle interaction. Widths fixture contains no identifiers, no timestamps, no row data — integers only.

### PM — scope/envelope — PASS
All 4 changed files touched within a 16:54:34–16:55:17 window (~43s of edits); `ad2f3281` landed 16:10. Comfortably inside the ~30min envelope. Scope matches Gate A's APPROVE-WITH-FIXES recommendation (single richer query, locally-derived, subset parity test).

### Counsel — N/A (confirmed)
No customer-facing artifact, no legal/BAA state, no PHI boundary. Test infra.

---

## Findings

| ID | Sev | Lens | Finding | Required action |
|----|-----|------|---------|-----------------|
| FU-1 | **P1** | Coach | `test_both_fixtures_exist()` not extended to assert `_WIDTHS.is_file()`; name semantically stale | Add `assert _WIDTHS.is_file(), f"missing {_WIDTHS}"`; rename to `test_all_fixtures_exist` recommended. Close in the #87 commit OR carry as named TaskCreate followup in the same commit (Session-220 lock-in). |
| FU-2 | P2 | Coach | Stale "the two" / "BOTH fixtures" docstring language (`test_schema_fixture_parity.py:10`, `test_sql_columns_match_schema.py:59`) | Update to reference all three sidecars + the names↔types / widths⊆names distinction. Same commit. |

No P0. P1 (FU-1) MUST be closed before the commit body says "shipped"/"complete", or carried as a named followup task in the same commit per the two-gate lock-in.

---

## Final verdict: APPROVE-WITH-FIXES

The core change is correct: the regen command provably reproduces all three fixtures from one prod snapshot, the 330→331 drift is a benign partition that moved no baseline, the new subset-parity test is sound, and the widths consumer passes. The single P1 (FU-1) is a missed sibling-harmonization — the `test_both_fixtures_exist` existence gate and its name were not brought along when `_WIDTHS` joined the file. Close FU-1 (and ideally FU-2 in the same touch) before marking #87 complete. With FU-1 addressed, this fully closes #77's Gate B P1 drift risk.
