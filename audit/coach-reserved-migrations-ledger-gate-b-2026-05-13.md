# Gate B — Reserved Migrations Ledger + CI Collision Gate (Task #59)

**Date:** 2026-05-13
**Gate:** B (pre-completion, post-push)
**Author claim:** 4-commit sequence shipped + 245 passed locally + 8 collision-gate tests pass
**Fork verdict file:** this file
**Design v3 + Gate A v3 APPROVE references:**
- `audit/reserved-migrations-ledger-design-2026-05-13.md`
- `audit/coach-reserved-migrations-ledger-gate-a-v3-2026-05-13.md`

---

## 250-word summary

Author shipped Task #59 as a 4-commit sequence (`c23e3cb4` design markers + Gate A verdicts, `94fcfd3d` ledger + claude.md rule, `5b23e56d` claude.md case-mismatch recovery, `dccee5f4` 8-test CI gate). Gate B re-verified every load-bearing claim empirically from a fresh shell at repo root, not by re-reading the design. `_REPO` path resolution lands at `/Users/dad/Documents/Msp_Flakes` exactly (5 parent-hops from `mcp-server/central-command/backend/tests/test_migration_number_collision.py`). `_claim_markers()` returned `{314: [canonical-metric-drift-invariant-design-2026-05-13.md], 315: [substrate-mttr-soak-v2-design-2026-05-13.md]}` — exact match to design v3 §-Final expectation, zero false positives across 98 audit/*.md docs. `_ledger_rows()` parsed all 6 rows including the `BLOCKED` status (mig 311 Vault) — `\w+` status capture proven against the unusual all-caps token. The ledger header literal matches `_EXPECTED_LEDGER_HEADER` byte-for-byte. All 8 gate tests pass in 0.20s. The full curated pre-push sweep (`bash .githooks/full-test-sweep.sh`) returned **245 passed, 0 skipped** — Author's claim verified. The 4-commit ordering preserved (commit-1 markers → commit-2 ledger → commit-2b case-fix → commit-3 CI gate); commit-2b is a tracked-case recovery, NOT a design violation. One P2 nit (the ledger's "See also" prose says "6 tests" but the gate ships 8) is non-blocking and a single-line fix. AS-IMPLEMENTED matches DESIGN v3 with no functional deviation. **Verdict: APPROVE** with P2 nit carried as TaskCreate followup.

---

## Per-lens verdict

### 1. Engineering (Steve) — APPROVE

Opened `tests/test_migration_number_collision.py`:
- 8 test functions match design v3 §6 promise exactly:
  1. `test_no_claim_marker_for_shipped_migration` ✓
  2. `test_no_two_docs_claim_same_unshipped_migration` ✓
  3. `test_every_claim_marker_in_ledger` ✓
  4. `test_no_ledger_row_for_shipped_migration` ✓
  5. `test_ledger_row_count_under_hard_cap` (30) ✓
  6. `test_ledger_row_count_under_soft_warn` (25, P2 #2) ✓
  7. `test_no_stale_ledger_rows_without_justification` (per-row, v3 P2) ✓
  8. `test_ledger_header_unchanged` (P2 #3 column-drift) ✓

`_REPO` path resolution traced: file is at `mcp-server/central-command/backend/tests/test_migration_number_collision.py`. From the file: `parent` = `tests/`, `parent.parent` = `backend/`, `parent.parent.parent` = `central-command/`, `parent.parent.parent.parent` = `mcp-server/`, `parent.parent.parent.parent.parent` = repo root. **5 hops verified empirically:** `python3 -c "import test_migration_number_collision as t; print(t._REPO)"` → `/Users/dad/Documents/Msp_Flakes`. ✓

`_LEDGER` exists: True (verified). `_AUDIT_DIR` exists: True (verified).

`_LEDGER_ROW_RE` parses all 6 rows: empirically verified — `[(311, BLOCKED, 2026-05-12, 2026-05-27), (314, in_progress, 2026-05-13, 2026-05-20), (315, reserved, ...), (316, reserved, ...), (317, reserved, ...), (318, reserved, ...)]`. The `\w+` status capture correctly handles `BLOCKED` (all-caps) + `in_progress` (underscored lowercase) + `reserved` — both tokens are pure `[A-Za-z0-9_]+` so `\w+` is sufficient. ✓

`_EXPECTED_LEDGER_HEADER` matches the actual header byte-for-byte: `grep "^| Number"` returns `| Number | Status | Claimed-by (design doc) | Claimed-at | Expected ship | Task | Notes |` — exact match. ✓

`test_no_stale_ledger_rows_without_justification` correctly walks per-row via `_row_line_for(n)` + `_PER_ROW_JUSTIFICATION_RE.search(row_line)` — the v2 doc-scoped bug from Gate A v2 is correctly closed in v3. The mig 311 row (`expected_ship=2026-05-27`) is 14 days from today + threshold is 30d-past, so NOT yet stale — gate correctly silent. ✓

**No P0 / P1 / P2 from Steve lens.** Code is well-defended; the 4 layers of false-positive defense (line-anchor, task sigil, code-fence stripping, coach-filename filter) hold up under empirical probe.

### 2. Database (Maya) — APPROVE

Ledger file opened + parsed via the gate's own regex (independent shell):

```
6 rows, all parse cleanly:
  311  BLOCKED      2026-05-12  2026-05-27
  314  in_progress  2026-05-13  2026-05-20
  315  reserved     2026-05-13  2026-05-20
  316  reserved     2026-05-13  2026-05-23
  317  reserved     2026-05-13  2026-05-30
  318  reserved     2026-05-13  2026-05-30
```

`BLOCKED` status for mig 311 correctly captured ✓. Column header matches `_EXPECTED_LEDGER_HEADER` exactly ✓. The on-disk migrations enumerate `310, 312, 313` shipped (mig 311 BLOCKED slot correctly reserved + skipped — `313` shipped past 311 with no collision because 311 has no SQL file on disk). The cross-check `test_no_ledger_row_for_shipped_migration` correctly passes (no shipped number appears in the ledger). The reverse `test_no_claim_marker_for_shipped_migration` correctly passes (no shipped number appears in a `mig-claim:` marker — only 314 + 315 are claimed, neither shipped).

**Concern probed:** the BLOCKED row's "Claimed-by" column references `audit/coach-vault-phase-c-gate-a-2026-05-12.md` — a coach-*.md file. Does this affect `_claim_markers()`? **No** — `_claim_markers()` reads markers FROM audit/*.md files (skipping coach-*.md), not FROM the ledger. The ledger's reference to a coach file is descriptive prose, not a marker source. ✓

**No P0 / P1 / P2 from Maya lens.**

### 3. Security (Carol) — APPROVE

Probed false-positive boundary across all 98 audit/*.md docs:

```
$ grep -rn "<!--.*mig-claim" audit/ | grep -v "coach-"
audit/canonical-metric-drift-invariant-design-2026-05-13.md:32:<!-- mig-claim: 314 task:#50 -->
audit/reserved-migrations-ledger-design-2026-05-13.md:13:> - **P0 #1**: ... `<!-- mig-claim: NNN -->` HTML-comment marker  [INLINE-BACKTICKED PROSE, no task:# sigil]
audit/reserved-migrations-ledger-design-2026-05-13.md:30:Literal shape (per §10): `<!-- mig-claim:<NNN> task:#<TASK> -->`  [BACKTICKED EXAMPLE]
audit/reserved-migrations-ledger-design-2026-05-13.md:103:    r"^<!--\s*mig-claim:\s*([1-9]\d{2})\s+task:#(\d+)\s*-->\s*$"  [REGEX SOURCE, no marker shape]
audit/reserved-migrations-ledger-design-2026-05-13.md:289-293:  [BACKTICKED EXAMPLES of historical claims, no task:# sigil on most]
audit/reserved-migrations-ledger-design-2026-05-13.md:351:When v3 ships, ... `^<!--\s*mig-claim:\s*([1-9]\d{2})\s+task:#(\d+)\s*-->\s*$`  [REGEX RECITATION]
audit/substrate-mttr-soak-v2-design-2026-05-13.md:79:<!-- mig-claim: 315 task:#98 -->
```

Empirical `_claim_markers()` result: `{314: [canonical-metric-drift-invariant-design-2026-05-13.md], 315: [substrate-mttr-soak-v2-design-2026-05-13.md]}` — exactly 2 entries. ✓

**Edge cases probed:**
- Lines 13, 30, 289-293, 351 in the design doc contain marker-like prose. None match the regex because:
  - They are inline-backticked (surrounded by `` ` ``) so the WHOLE LINE is not the marker — line-anchor `^...$` fails.
  - Most omit `task:#NN` sigil.
- Coach-prefixed files (the v3 verdict + v2 verdict + v1 verdict) DO contain literal example markers in code blocks. The `coach-*.md` filename filter skips them entirely. Verified: `grep -c` against coach-*.md returns matches, but `_claim_markers()` returns zero from them.
- Code-fence stripping: `audit/reserved-migrations-ledger-design-2026-05-13.md` likely contains the regex source inside a `\`\`\`python` fence — that block IS stripped by `_CODE_FENCE_RE` before matching, so even bare HTML-comment shapes inside fences don't false-positive. ✓

**No historical references** like "mig 146", "mig 138", "mig 257" trigger anything — they don't carry the `<!-- mig-claim: ... -->` literal shape at all. Carol's original P0 concern (greedy `\bmig\s+\d{3}\b`) is structurally closed by the marker design.

**No P0 / P1 / P2 from Carol lens.**

### 4. Coach (process integrity) — APPROVE WITH NIT

4-commit ordering verified via `git log --oneline -5`:

```
dccee5f4 test(ci): migration-number collision gate (Task #59 Commit 3/4)    [3rd in time, 4th in design]
5b23e56d docs(rules): claude.md rule for mig-claim ledger (Task #59 Commit 2b/4)  [unplanned recovery]
94fcfd3d ledger: RESERVED_MIGRATIONS.md + CLAUDE.md rule (Task #59 Commit 2/4)    [2nd]
c23e3cb4 audit(design): mig-claim markers + Gate A verdicts (Task #59 Commit 1/4) [1st]
```

Reads bottom-up: Commit 1 (design-doc markers + Gate A verdicts) → Commit 2 (ledger + claude.md rule) → Commit 2b (case-mismatch recovery for claude.md edit that didn't stage) → Commit 3 (CI gate). **Ordering invariant preserved** — the design markers ship FIRST so the ledger can be defended by the gate; the gate ships LAST so it has actual data to enforce against.

Commit 2b is a tracked-case-recovery (the file is committed as lowercase `claude.md`; the author's edit shell-globbed against `CLAUDE.md` which mismatched). The recovery ADDS the rule that was conceptually part of Commit 2's design — does NOT introduce new policy. Reviewed Commit 2b's diff: single-line insertion in claude.md. The brief acknowledged this case explicitly and approved it. ✓

CI gate `dccee5f4 --stat` shows: 1 new test file (228 lines) + 1 line added to `.githooks/pre-push`. Landed exactly where designed. ✓

**P2-NEW #1 (nit, non-blocking):** `RESERVED_MIGRATIONS.md` line 46 says `(6 tests)` but the as-shipped gate has 8 tests (P2 soft-warn + header-drift were added in v3 P2). Documentation drift, not correctness. Author can fix in a 1-line follow-up commit OR in the same PR as the next ledger touch. Carry as TaskCreate followup.

### 5. Auditor (OCR) — N/A

This is an operator-internal coordination tool. No customer-facing artifact, no compliance-bundle write, no PHI surface. **Confirmed N/A.** ✓

### 6. PM — APPROVE

Total commit count: 4 (1 + 2 + 2b + 3) matches design. Commit 2b was unplanned but caught by the author + tracked + named in the body — process worked. Author cited 245 passed locally; Gate B re-ran `bash .githooks/full-test-sweep.sh` from repo root and got **`245 passed, 0 skipped (need backend deps)`** — exact match. ✓ CI will run remotely on push — we don't have CI green yet at verdict-write time but the local sweep matches CI parity per the `test_pre_push_ci_parity.py` design.

No design slip — every promise in design v3 §6 ledger-rule + §6 CI gate is realized in the as-shipped artifacts. The Gate A v3 P2 hardenings (soft-warn at 25 + header-drift gate + per-row stale justification) are all present in the as-shipped test file.

**No P0 / P1 / P2 from PM lens.**

### 7. Attorney (in-house counsel) — APPROVE

**Rule 5 (no stale doc as authority) closure check:**

The ledger MUST genuinely solve the staleness class for migration numbers. Empirical probe of the as-shipped gate:

- `test_no_stale_ledger_rows_without_justification` walks ROW-BY-ROW via `_row_line_for(n)` (parses the specific row line, then runs `_PER_ROW_JUSTIFICATION_RE` against THAT line only). This closes the v2 doc-scoped bug (where a single `<!-- stale-justification: ... -->` anywhere in the doc satisfied all stale rows). ✓
- `_STALE_WARN_DAYS = 30` matches Rule 5's 30-day staleness threshold convention.
- Per-row justification literal `<!-- stale-justification: ... -->` is the same HTML-comment shape as `<!-- mig-claim: ... -->`, consistent with the design's marker convention. ✓
- Today's 6 rows: mig 311 expected 2026-05-27 (14 days out), 314/315 expected 2026-05-20 (7 days), 316 expected 2026-05-23 (10 days), 317/318 expected 2026-05-30 (17 days). **None are stale today** — gate correctly silent. The gate will start firing on these rows starting roughly 2026-06-27 (mig 311 + 30d threshold).

The ledger itself is post-ship authority in the order: on-disk SQL file > ledger row > design-doc marker. This matches counsel's Rule 5 priority — current operational state wins over claimed state.

**No P0 / P1 / P2 from Attorney lens.**

---

## AS-IMPLEMENTED vs DESIGN v3 deviation matrix

| Promise from design v3 / Gate A v3 | As-implemented | Deviation? |
|---|---|---|
| Line-anchored regex `^<!--\s*mig-claim:\s*([1-9]\d{2})\s+task:#(\d+)\s*-->\s*$` MULTILINE | Exact match at test file line 32-35 | None |
| Code-fence stripping before match | `_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)` at line 36 | None |
| Coach-*.md filename exclusion | `if doc.name.startswith("coach-"): continue` at line 84 | None |
| 4 test functions (v1 design) | Shipped with 4 base + 4 hardenings = 8 total | Positive — Gate A v3 P2 added 3 more, plus header gate per P2 #3 |
| Hard cap 30 rows | `_MAX_LEDGER_ROWS = 30` at line 50 | None |
| Soft warn 25 rows (P2 #2) | `_SOFT_WARN_ROWS = 25` at line 51 | None |
| Stale 30-day threshold | `_STALE_WARN_DAYS = 30` at line 52 | None |
| Per-row stale justification (v3 fix to v2 doc-scoped bug) | `_PER_ROW_JUSTIFICATION_RE` + `_row_line_for()` at lines 42-44 + 94-100 | None |
| Column-header drift gate (P2 #3) | `test_ledger_header_unchanged` at lines 211-228 | None |
| 6 active reservations in ledger | 6 rows parsed (311 BLOCKED + 314 + 315 + 316 + 317 + 318) | None |
| Markers in canonical-metric-drift design + MTTR-soak design | Both present, line-anchored, task-sigiled | None |
| MTTR soak renumber 311 → 315 | Verified in commit `c23e3cb4` design-doc diff | None |
| claude.md rule | Line 256 in claude.md, in "## Rules" section, points at ledger + gate | None |
| Pre-push allowlist entry | Line 125 of `.githooks/pre-push`, alphabetically placed | None |
| 4-commit sequence | Verified via `git log --oneline -5` | None (commit 2b is recovery, not a 5th policy commit) |
| 8 Gate A verdict files landed | Verified via `ls audit/coach-*-gate-a-2026-05-13.md` + commit-1 diff stat | None |

**No functional deviation from design v3.** The only nit is the ledger's prose-line "See also: (6 tests)" which lags the as-shipped 8 tests — documentation-only.

---

## Empirical regex re-verification

Run from repo root (`/Users/dad/Documents/Msp_Flakes`):

```
$ python3 -c "import sys; sys.path.insert(0, 'mcp-server/central-command/backend/tests'); \
              import test_migration_number_collision as t; \
              print('REPO:', t._REPO); \
              print('LEDGER EXISTS:', t._LEDGER.exists()); \
              print('AUDIT EXISTS:', t._AUDIT_DIR.exists()); \
              print('MARKERS:', t._claim_markers()); \
              print('ROWS:', t._ledger_rows()); \
              print('SHIPPED MAX:', max(t._shipped_migrations()))"

REPO: /Users/dad/Documents/Msp_Flakes
LEDGER EXISTS: True
AUDIT EXISTS: True
MARKERS: {314: ['canonical-metric-drift-invariant-design-2026-05-13.md'],
          315: ['substrate-mttr-soak-v2-design-2026-05-13.md']}
ROWS:    [{'n': 311, 'status': 'BLOCKED',     'claimed_at': '2026-05-12', 'expected_ship': '2026-05-27'},
          {'n': 314, 'status': 'in_progress', 'claimed_at': '2026-05-13', 'expected_ship': '2026-05-20'},
          {'n': 315, 'status': 'reserved',    'claimed_at': '2026-05-13', 'expected_ship': '2026-05-20'},
          {'n': 316, 'status': 'reserved',    'claimed_at': '2026-05-13', 'expected_ship': '2026-05-23'},
          {'n': 317, 'status': 'reserved',    'claimed_at': '2026-05-13', 'expected_ship': '2026-05-30'},
          {'n': 318, 'status': 'reserved',    'claimed_at': '2026-05-13', 'expected_ship': '2026-05-30'}]
SHIPPED MAX: 313
```

**Matches Gate-A-v3 expectation exactly.** The marker dict equals `{314: [canonical-metric-drift], 315: [substrate-mttr-soak]}` as the brief predicted.

## 8-test gate run

```
$ python3 -m pytest mcp-server/central-command/backend/tests/test_migration_number_collision.py -v
collected 8 items
... 8 passed in 0.20s
```

## 4-commit ordering verification

```
$ git log --oneline -5
dccee5f4 test(ci): migration-number collision gate (Task #59 Commit 3/4)
5b23e56d docs(rules): claude.md rule for mig-claim ledger (Task #59 Commit 2b/4)
94fcfd3d ledger: RESERVED_MIGRATIONS.md + CLAUDE.md rule (Task #59 Commit 2/4)
c23e3cb4 audit(design): mig-claim markers + Gate A verdicts (Task #59 Commit 1/4)
c103ca31 fix(mig-312): remove UPDATE that triggered baa_signatures append-only block  [pre-existing parent]
```

Ordering preserved: Commit 1 (markers) → Commit 2 (ledger) → Commit 2b (case-mismatch recovery for claude.md) → Commit 3 (CI gate). Commit 2b is a tracked recovery NOT a 5th policy step.

## Pre-push sweep result citation

```
$ bash .githooks/full-test-sweep.sh
... [redacted intermediate]
✓ 245 passed, 0 skipped (need backend deps)
```

**Author's claim "245 passed locally" verified empirically.** This is the Session 220 lock-in requirement (Gate B must run the sweep, not just review the diff) — satisfied.

---

## P0 / P1 / P2 follow-ups

- **P0:** none
- **P1:** none
- **P2-NEW #1 (Coach lens):** `RESERVED_MIGRATIONS.md` line 46 says `(6 tests)` but the as-shipped gate has 8. One-line doc fix. Carry as TaskCreate followup OR fold into next ledger-touching commit. **Non-blocking.**

---

## Final overall verdict

**APPROVE.**

Empirical evidence:
- Marker dict matches the design-v3 expectation exactly (`{314: [...], 315: [...]}`).
- Pre-push full sweep returns **245 passed, 0 skipped** — Author's claim verified.
- 4-commit ordering preserved (commit-1 → commit-2 → commit-2b case-fix → commit-3).
- All 8 gate tests pass in 0.20s.
- All 6 ledger rows parse cleanly under the gate's own regex.
- Ledger header matches `_EXPECTED_LEDGER_HEADER` byte-for-byte.
- Zero false positives across 98 audit/*.md docs.
- Zero P0 / P1 from any lens.
- One P2 doc-prose nit (6-vs-8 tests) carried as a non-blocking follow-up.

The AS-SHIPPED artifacts match design v3 + Gate A v3 with no functional deviation. Task #59 may be marked complete; the single P2 nit is non-blocking and can be folded into the next ledger-touching commit (e.g. when mig 311 ships and its row is removed).
