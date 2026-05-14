# Gate A — Task #87: Fold `prod_column_widths.json` into the combined schema-fixture regen

**Date:** 2026-05-14 | **Gate:** A (pre-execution) | **Scope:** test-infra / schema metadata only — no row data
**Lenses run:** Steve, Maya (DB), Coach, PM | Carol / Auditor / Counsel — N/A (no PHI, no customer-facing surface)

## 200-word summary

**Verdict: APPROVE-WITH-FIXES (one P1 design correction, no blockers).**

The task is sound and ~30min / single commit. Three corrections to the task brief:

1. **The "231 vs 330" framing is wrong.** `prod_column_widths.json` is NOT stale by 99 tables. Widths only covers tables that have ≥1 length-constrained column, so its natural cardinality is ~232, not 330. A fresh prod pull today returns **232 tables** vs the on-disk **231** — the only delta is one new VIEW (`v_l2_outcomes`), **zero column/width changes** on existing tables. The fixture is barely stale.

2. **Recommended regen shape: single richer query, derive all three locally.** Change the inner agg to `json_object_agg(column_name, json_build_array(data_type, character_maximum_length))`, then derive `types`, `names`, AND `widths` in the one Python post-step. Avoids a second psql round-trip and a second source-of-truth — keeps the "can't drift" guarantee that motivated Phase B-lite.

3. **Coach blast-radius probe: CLEAN.** Ran `test_check_constraint_fits_column.py` against the fresh 232-table widths pull — **2 passed, zero new CHECK-too-long violations.** No latent bug surfaced (unlike #77's SELECT_BASELINE bump).

Parity test: add a SUBSET check, not full parity (widths is a subset by design).

---

## Steve — combined psql command extensibility

**Question:** second SELECT in the same `-c`, or one richer query deriving all three locally?

**Recommendation: (b) — one richer query, derive all three locally.** Exact shape:

```sql
SELECT json_object_agg(table_name, cols) FROM (
    SELECT table_name,
           json_object_agg(column_name,
               json_build_array(data_type, character_maximum_length)) AS cols
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name NOT LIKE 'pg_%'
     GROUP BY table_name) s
```

Python post-step derives all three:

```python
import json, sys
d = json.load(sys.stdin)
typed  = {k: {c: v[0] for c, v in sorted(cols.items())}
          for k, cols in sorted(d.items())}
names  = {k: sorted(cols.keys()) for k, cols in typed.items()}
widths = {k: {c: v[1] for c, v in sorted(cols.items()) if v[1] is not None}
          for k, cols in sorted(d.items())}
widths = {k: v for k, v in widths.items() if v}   # drop tables w/ no width-constrained cols
for fn, obj in [('prod_column_types.json', typed),
                ('prod_columns.json', names),
                ('prod_column_widths.json', widths)]:
    with open('tests/fixtures/schema/' + fn, 'w') as f:
        json.dump(obj, f, indent=2, sort_keys=True); f.write('\n')
```

**Why not (a) a second SELECT:** two SELECTs in one `-c` means two result blobs to split on the client, OR two `-c` flags = two queries = two snapshot moments = the exact drift window Phase B-lite closed. One query, three derived files, ONE snapshot — preserves the "can't drift by construction" property. `json_build_array(data_type, char_max_length)` is a minimal, non-fragile extension of the existing `json_object_agg`. Verified: `character_maximum_length` is a valid `information_schema.columns` column; `json_build_array` tolerates the NULL second element fine (serializes as `[..., null]`).

One nuance for the docstring: note that `widths` drops tables whose every column is unconstrained — that's why widths has ~232 tables not 330. Without that note a future maintainer will "fix" a non-bug.

## Maya (Database) — `character_maximum_length` semantics

**Confirmed.** `information_schema.columns.character_maximum_length` is **NULL** for `integer`, `uuid`, `text`, `jsonb`, `timestamptz`, `boolean`, `numeric` etc. It is non-NULL only for `character varying(n)` and `character(n)`. The widths fixture MUST filter `character_maximum_length IS NOT NULL` (the derive step above does this via `if v[1] is not None`).

**On-disk shape verified:** `{table: {col: int}}`, e.g. `admin_audit_log -> {"action": 100, "ip_address": 45, "target": 255, "username": 100}`. Cross-referenced every (table, col) in the current widths fixture against `prod_column_types.json` — **every single one is `character varying` or `character`. Zero `text`/`int`/`uuid` leakage.** `text` (no limit) is correctly excluded today.

**Fresh prod pull (2026-05-14) confirms the filter holds:** server-side `WHERE character_maximum_length IS NOT NULL` returned 232 tables; formatted shape identical to on-disk. The 12 "varchar cols missing from widths" I found cross-referencing the OLD widths against the NEW types fixture are NOT a filter bug — they're columns on tables/views (`v_l2_outcomes`, `client_approvals.site_id`, `pending_alerts.site_id`) that postdate the May-2 widths capture. Exactly the staleness #87 closes.

## Coach — sibling-consistency / widths-refresh blast radius

**Probe:** does refreshing widths surface a new `test_check_constraint_fits_column.py` failure (a latent CHECK-too-long bug like #77's SELECT_BASELINE bump)?

**Method:** pulled fresh widths from prod (`ssh root@178.156.162.116`, 232 tables), formatted to fixture shape, swapped it in, ran `pytest tests/test_check_constraint_fits_column.py`.

**Result: `2 passed in 0.37s`. CLEAN — zero blast radius.**

Diff old→new widths fixture: `only in NEW: ['v_l2_outcomes']` (a view, no CHECK constraints), `col/width changes: 0`. There is no newly-covered table that carries a migration CHECK constraint whose literal exceeds the column width. The refresh is purely additive and benign. No latent bug to flag.

Sibling note: `test_check_constraint_fits_column.py::test_schema_fixture_loaded_with_widths` asserts `incidents.resolution_tier == 10` — fresh pull preserves this (no width changes), so the canonical D6 sanity sample survives the refresh.

## PM — scope / effort

**Confirmed ~30min, single commit.** Three mechanical edits:
1. Docstring regen command in `test_sql_columns_match_schema.py` (the SQL + Python post-step above).
2. Run it once against prod, commit refreshed `prod_column_widths.json` (+ regen'd `prod_columns.json` / `prod_column_types.json` — they'll be byte-identical to current since nothing changed, but regen all three for one-snapshot integrity).
3. Add subset check to `test_schema_fixture_parity.py`.

No migration, no RESERVED_MIGRATIONS row, no Gate B migration concern. Single commit. Gate B should run the full source-level sweep per the Session 220 lock-in (diff-only review is auto-BLOCK), but that's a process note, not extra scope.

## Parity-test subset-check design (`test_schema_fixture_parity.py`)

Add ONE test. NOT full parity — widths is a SUBSET by design (only length-constrained columns; only tables that have ≥1 such column).

```python
def test_widths_fixture_is_subset_of_columns():
    """prod_column_widths.json only covers length-constrained (varchar/char)
    columns, so it is a SUBSET of prod_columns.json — not key-set identical.
    Every table in widths must exist in columns, and every (table, column)
    in widths must exist in columns. Reverse direction is NOT checked
    (columns has many tables/cols with no width limit)."""
    names  = _load(_NAMES)
    widths = _load(_SCHEMA_DIR / "prod_column_widths.json")
    orphan_tables = sorted(set(widths) - set(names))
    assert not orphan_tables, (
        "prod_column_widths.json has tables absent from prod_columns.json "
        f"(stale widths fixture — regenerate all 3 with the combined command): {orphan_tables}"
    )
    orphan_cols = []
    for table in sorted(set(widths) & set(names)):
        ncols = set(names[table])
        for col in widths[table]:
            if col not in ncols:
                orphan_cols.append(f"  {table}.{col}")
    assert not orphan_cols, (
        "prod_column_widths.json has (table,column) pairs absent from "
        "prod_columns.json — regenerate all 3 with the combined command:\n"
        + "\n".join(orphan_cols)
    )
```

Update the module docstring of `test_schema_fixture_parity.py` to say "three fixtures" and reference widths as the subset case. Note `_SCHEMA_DIR` is already defined in that file — reuse it.

## Final verdict

**APPROVE-WITH-FIXES.** No P0. One P1 (correct the "231 vs 330" misframing — widths' natural cardinality is ~232; don't let the implementer chase a phantom 99-table gap or "fix" the table-drop-on-no-width-cols behavior). Recommended regen shape: **single richer query (`json_build_array(data_type, character_maximum_length)`), derive all three files locally** — one prod snapshot, no drift window. Parity test: **subset check, not full parity.** Coach blast-radius probe ran clean (`2 passed`, zero new CHECK violations, only delta is one constraint-free view). Proceed to implementation; Gate B must run the full source-level sweep.
