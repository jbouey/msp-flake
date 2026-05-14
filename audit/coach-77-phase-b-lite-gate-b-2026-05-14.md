# Gate B — Task #77 Phase B-lite: param-cast type-mismatch CI gate

**Date:** 2026-05-14 | **Gate:** B (pre-completion) | **Reviewer:** fork-based 7-lens (Steve / Maya / Carol / Coach / Auditor / PM / Counsel)
**Artifact:** AS-IMPLEMENTED uncommitted diff on `main`, `mcp-server/central-command/backend/`
**Predecessors:** Gate A `audit/coach-cast-gate-phase-b-gate-a-2026-05-14.md` (BLOCKED full sqlparse, approved B-lite) + Gate A DELTA `audit/coach-77-phase-b-lite-approach-delta-gate-a-2026-05-14.md` (chose sidecar fixture, 2 drift-guard P0s)

---

## 250-word summary

**VERDICT: APPROVE-WITH-FIXES.** The Phase B-lite gate is well-built and the design intent from both Gate A reviews is faithfully implemented. The regex is sound for its stated scope, the type-family maps are complete (all 18 prod `data_type` strings classified or deliberately routed to OTHER→skip), `BASELINE_MAX=0` is genuinely true (manually re-counted: 0 violations), the two drift-guard P0s from the Gate A delta are both present and passing, and Phase A's 6-column hard pin is untouched and still green — confirmed defense-in-depth, since `appliance_id` (the original outage column) is multi-class `{TEXT,UUID}` and correctly falls to Phase A.

**The full pre-push sweep initially FAILED 1/256** — `test_pre_push_ci_parity.py` flagged the 2 new test files as referenced-but-untracked. This is the exact Session-220 "diff missed an addition" class the sweep exists to catch. It is **not a code defect** — it resolves with `git add` of the 3 new files (the gate asks git, not the filesystem). After staging: **256 passed, 0 failed, 0 skipped.** This is the one mandatory fix: **all 3 new files MUST be `git add`ed in the commit** (the test files + `prod_column_types.json`). With that done the artifact is commit-ready.

One P1 (non-blocking, carry as followup): `prod_column_widths.json` is now stale at 231 tables vs 330 — its consumer still passes (missing tables = unchecked, not failed) but the regen should be folded into the combined command. SELECT_BASELINE_MAX 9→10 bump is verified-correct walker imprecision.

---

## 1. Full pre-push sweep — MANDATORY

Command: `bash .githooks/full-test-sweep.sh` from repo root.

| Run | Result |
|-----|--------|
| Before `git add` of new files | **1 failed / 255 passed** — `test_pre_push_ci_parity.py::test_pre_push_allowlist_only_references_git_tracked_files` |
| After `git add` of 3 new files | **256 passed, 0 failed, 0 skipped** |

The failure: `.githooks/pre-push` SOURCE_LEVEL_TESTS now references `test_no_param_cast_against_mismatched_column.py` + `test_schema_fixture_parity.py`, but both were `??` untracked. `test_pre_push_ci_parity.py` asks **git** (not the filesystem) whether allowlist entries are tracked — exactly to catch dev-disk-vs-CI divergence. Staging the files (option (a) from the test's own message) clears it. Re-ran `test_pre_push_ci_parity.py` standalone after staging: **4 passed**.

**This is a real Gate B finding** — the artifact as-handed-to-review was NOT commit-ready. It is a process miss, not a logic bug, and the fix is mechanical, hence APPROVE-WITH-FIXES rather than BLOCK.

---

## 2. Per-lens verdicts

### Steve (regex soundness, ambiguity-skip, BASELINE_MAX) — PASS

- **Regex** `\b(?:[a-z_][a-z_0-9]*\.)?([a-z_][a-z_0-9]+)\s*=\s*\$\d+::(\w+)` — sound for the stated scope. Matches `col = $N::type` and `qualifier.col = $N::type`. Honest, documented limitations: no FROM-clause parse (hence the multi-class skip), only `col = $N` shape (not `$N = col`, not `col IN (...)`, not function-wrapped). Gate A already accepted these as the explicit scope reduction vs the BLOCKED full sqlparse build. No false-positive holes found — comment lines are skipped, unknown cast tokens are skipped, non-prod columns are skipped.
- **Multi-class skip** does silently skip `appliance_id` — which IS the original-outage column (`{character varying ×50, text ×14, uuid ×10}` → `{TEXT,UUID}` → ambiguous). **This is correct by design**: Phase A's `test_no_uuid_cast_on_text_column.py` hard-pins exactly those 6 multi-class columns. Verified Phase A untouched (`git status` clean on that file) and passing (2/2). Defense-in-depth holds.
- **BASELINE_MAX=0** — re-ran the gate's own internals (`_column_family_map` + `_file_violations` over all 154 root `.py` files): **TOTAL VIOLATIONS: 0**. Genuinely true zero-baseline ratchet. `test_baseline_is_zero` pins it.
- Minor note (not a finding): gate globs `_BACKEND.glob('*.py')` — root only, not subdirs. Consistent with Phase A and the sibling `test_sql_columns_match_schema.py` walkers; backend SQL lives in root `.py` files. Acceptable, matches house convention.

### Maya (type-family map completeness/correctness) — PASS

- 18 distinct `data_type` strings in `prod_column_types.json`: `ARRAY, bigint, boolean, bytea, character, character varying, date, double precision, inet, integer, jsonb, numeric, real, smallint, text, timestamp with time zone, timestamp without time zone, uuid`.
- Every one maps to a family OR is deliberately unmapped: only `ARRAY` is unmapped → `_column_family_map` routes it to the `OTHER` sentinel → forces the column multi-class → skipped. No type silently mis-resolves. `character` (rare bpchar) correctly → TEXT. `numeric`/`real`/`double precision` → FLOAT. All correct.
- `_CAST_TYPE_FAMILY` covers the SQL-side spellings (`varchar`, `int4`, `timestamptz`, `bpchar`, etc.) — superset of what's needed; unknown cast tokens fall to `continue` (don't guess). Sound.
- **SELECT_BASELINE_MAX 9→10**: verified. `client_owner_transfer.py:557` SELECT references `t.id, t.client_org_id, t.initiated_by_user_id, t.target_email, t.reason, t.expires_at, t.status` — all 7 confirmed present in `client_org_owner_transfer_requests` per the typed fixture (table has 18 cols incl. all 7). The table became newly-checkable only because the fixture refresh added it. `test_every_python_select_references_real_columns` passes at 10. Bump is genuine walker imprecision, same false-positive class as the existing baseline entries, correctly documented inline.

### Coach (load-bearing — MISSING additions) — PASS WITH 1 P1

- **(a) `prod_column_widths.json` staleness:** YES, it is stale — 231 tables vs the refreshed 330. **P1, non-blocking.** Its regen is NOT folded into the combined command. Out-of-scope-for-this-task is defensible (Phase B-lite envelope was the typed fixture + gate), but it's a latent drift the next person will trip on. → **Followup task: fold `prod_column_widths.json` regen into the combined command.**
- **(b) `test_check_constraint_fits_column.py`** (consumer of widths fixture): **2 passed.** Stale widths fixture doesn't break it — missing tables are simply unchecked, not failed. Confirms (a) is P1 not P0.
- **(c) Phase A floor:** `test_no_uuid_cast_on_text_column.py` — `git status` shows it untouched, `pytest` 2/2 passed. Gate A delta's "keep the 6-column hard pin as the floor" honored.
- **(d) `test_pre_push_ci_parity.py`:** Initially FAILED (see §1) — the insidious Session-220 class. Resolves with `git add`. After staging: 4 passed. **This is the mandatory fix.** No other meta-test needs the 2 new files elsewhere — they belong only in SOURCE_LEVEL_TESTS, and the full sweep auto-discovers all `tests/test_*.py` regardless.

### Carol / Auditor / Counsel — PASS (N/A confirmed)

- `prod_column_types.json` inspected: contains ONLY `{table: {column: data_type_string}}` — schema metadata, zero row data, zero values. No PHI, no customer data, no secrets. Safe to commit. Same risk profile as the already-committed `prod_columns.json` / `prod_column_widths.json`.
- Test infrastructure only — no runtime/customer-facing/legal surface. No Counsel 7-rule interaction.

### PM (scope) — PASS

- Stayed within the ~1.5–2h Phase B-lite envelope. The fixture refresh (311→330) was a necessary side-effect of pulling a fresh typed snapshot, not scope creep — and it surfaced exactly one documented baseline bump. 5 files touched (3 new, 2 modified) + 1 hook line + 1 audit doc. Tight.

---

## 3. Fixture-refresh blast radius — VERIFIED

`prod_columns.json` 311→330 tables. All 3 consumers run clean:

| Consumer | Result |
|----------|--------|
| `test_sql_columns_match_schema.py` | passed (incl. the documented SELECT_BASELINE_MAX 9→10 bump) |
| `test_migration_view_columns_exist.py` | passed |
| `test_no_raw_discovered_devices_count.py` | passed |

(Run together: 12 passed.) No newly-failing consumer beyond the documented, verified-correct bump. Gate A delta's "all consumers iterate via `set(dict)`" claim holds.

---

## 4. Missing additions

| Item | Severity | Disposition |
|------|----------|-------------|
| 3 new files not `git add`ed → `test_pre_push_ci_parity.py` fails the sweep | **P0 (mechanical)** | **MUST `git add` `test_no_param_cast_against_mismatched_column.py` + `test_schema_fixture_parity.py` + `prod_column_types.json` in the commit.** Verified resolved on staging. |
| `prod_column_widths.json` stale (231 vs 330 tables); regen not folded into combined command | **P1** | Carry as named followup task in the same commit. Consumer still passes today. |

No P0 logic defects. No banned-word / ungated-import / SQL-shape drift found.

---

## 5. SELECT_BASELINE_MAX bump assessment

9→10 is **verified correct**. Root cause: the fixture refresh added `client_org_owner_transfer_requests` to the known-schema set, making `client_owner_transfer.py:557` newly-checkable. The flagged query references only the 7 real columns of that table (all confirmed in the typed fixture). The flag is the SELECT walker's known imprecision (it tokenizes variable names + adjacent-statement columns), identical to the pre-existing baseline-10... entries. Inline doc on the constant is accurate. Not a real bug — correct to bump.

---

## FINAL VERDICT: **APPROVE-WITH-FIXES**

**Required before commit (mechanical, verified-resolving):**
1. `git add` all 3 new files — `tests/test_no_param_cast_against_mismatched_column.py`, `tests/test_schema_fixture_parity.py`, `tests/fixtures/schema/prod_column_types.json` — so `test_pre_push_ci_parity.py` passes and CI has the files. (Confirmed: after staging, full sweep = 256 passed / 0 failed.)

**Required in the same commit (P1 carried as followup):**
2. Create a named TaskCreate followup: "Fold `prod_column_widths.json` regen into the combined schema-fixture regen command (currently stale: 231 vs 330 tables)."

**Commit body must cite both Gate A verdicts + this Gate B verdict.**

Once #1 is done and #2 is filed, the artifact is complete and shippable. Both Session-220 Gate-B mandates satisfied: full sweep was run (not diff-only) and cited; 7 lenses applied to the as-implemented artifacts.
