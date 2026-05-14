# Gate A — Task #65a: add `canonical_metric_samples` to `partition_maintainer_dry` monitored-table list

**Date:** 2026-05-14
**Scope:** TIGHT (single-line list addition + test fixture update)
**Verdict:** **APPROVE-WITH-FIXES** (one P1 — test fixture must be updated in same commit)

---

## Investigation findings

### Exact list location
`assertions.py::_check_partition_maintainer_dry` (line 1423). The monitored-table list is **hardcoded in the SQL `WHERE … IN (…)` clause** at lines 1466–1467:

```python
WHERE parent.relname IN ('compliance_bundles', 'portal_access_log',
                          'appliance_heartbeats', 'promoted_rule_events')
```

There is NO separate Python list/tuple constant — the table set lives inline in the `pg_inherits` query. The query joins `pg_inherits → pg_class` (parent + child), excludes `%_default%` children, groups by parent, then Python checks each parent's children for a next-month substring.

### Shape confirmed
- Hardcoded SQL `IN (...)` list, not a module constant.
- DOES check `pg_inherits` for next-month child existence via substring match against `expected_patterns` (lines 1454–1458).
- Docstring (lines 1427–1445) enumerates the 4 tables + their naming conventions — **must be updated too**.

### Mig 314 default-partition check — CONFIRMED ABSENT
`migrations/314_canonical_metric_samples.sql` creates only 3 explicit monthly partitions (`_2026_05/_06/_07`) and **no `DEFAULT` partition**. A wedged `canonical_metric_samples_pruner_loop` (background_tasks.py:1651, can fail with `permission denied for schema public` — same class as partition_maintainer_loop pre-#78) means next-month INSERTs **fail outright** (`no partition of relation … found for row`). This makes the sev1 invariant genuinely load-bearing — not just bloat protection.

---

## 7 lenses (brief)

**1. Steve — list location + edit shape.** Two edits in `_check_partition_maintainer_dry`: (a) add `'canonical_metric_samples'` to the SQL `IN (...)` list (line 1466–1467); (b) add `canonical_metric_samples: _YYYY_MM` to the docstring naming-convention block. The `expected_patterns` list needs NO change — `f"{next_y}_{next_m:02d}"` (YYYY_MM) already covers `canonical_metric_samples_2026_05`. Net: 1 SQL line + 1 docstring line.

**2. Maya — partition-naming compat.** `canonical_metric_samples_YYYY_MM` (mig 314 line 37, pruner regex `^canonical_metric_samples_(\d{4})_(\d{2})$` at background_tasks.py:1680). The invariant's `expected_patterns[0]` = `f"{next_y}_{next_m:02d}"` → `"2026_05"`, matched via `p in c` substring. `"2026_05" in "canonical_metric_samples_2026_05"` → True. **Fully compatible, zero pattern changes.** Same family as `compliance_bundles`/`portal_access_log`.

**3. Carol — N/A.**

**4. Coach — sibling pattern.** `appliance_heartbeats` (`_yYYYYMM`) and `promoted_rule_events` (`_YYYYMM`) were each added by extending the same inline `IN (...)` list + adding their suffix pattern to `expected_patterns` + a docstring line. This change is *simpler* than those — no new `expected_patterns` entry needed (YYYY_MM already present). Identical shape, strictly smaller.

**5. OCR — N/A.**

**6. PM — effort.** ~15 min: 1 SQL line, 1 docstring line, plus **P1: update `tests/test_partition_maintainer_dry.py`** — 4 test fixtures hardcode exactly-4-table row lists and `test_partition_maintainer_dry_fires_per_missing_parent` asserts `len == 4` + an explicit `{4 names}` set. Adding a 5th monitored table without updating fixtures = guaranteed CI failure. Fixture update is mandatory same-commit, not optional.

**7. Counsel — N/A.**

---

## Required fixes (P1 — same commit)

1. `assertions.py` line 1466–1467: add `'canonical_metric_samples'` to the `IN (...)` list.
2. `assertions.py` docstring (lines 1427–1445): add `canonical_metric_samples: _YYYY_MM` to the naming-convention block + mention in the partitioned-tables sentence.
3. `tests/test_partition_maintainer_dry.py`: add `canonical_metric_samples` rows to all 4 fixtures; bump `test_partition_maintainer_dry_fires_per_missing_parent` from `== 4` to `== 5` and add the name to the asserted parent set; ideally add a `canonical_metric_samples`-missing case mirroring the `compliance_bundles` test.

No P0s. Edit is mechanically identical to how `appliance_heartbeats`/`promoted_rule_events` were added, and *simpler* (no `expected_patterns` change). Proceed once the test fixtures are updated in lockstep.

---

## 150-word summary

Task #65a adds `canonical_metric_samples` to the `partition_maintainer_dry` sev1 invariant. The monitored-table list is a hardcoded SQL `IN (...)` clause inside `_check_partition_maintainer_dry` (assertions.py:1466), not a module constant — the edit is one SQL line plus one docstring line. Partition naming `canonical_metric_samples_YYYY_MM` is already covered by the existing `expected_patterns` YYYY_MM entry, so no pattern change is needed — strictly simpler than the `appliance_heartbeats`/`promoted_rule_events` additions, which is the established sibling pattern. Confirmed mig 314 has **no DEFAULT partition**, so a wedged pruner loop causes hard INSERT failures, making this sev1 monitoring genuinely load-bearing. **One P1:** `tests/test_partition_maintainer_dry.py` hardcodes exactly-4-table fixtures and a `len == 4` assertion — these must be updated to 5 in the same commit or CI fails. Verdict: **APPROVE-WITH-FIXES**, ~15 min effort.
