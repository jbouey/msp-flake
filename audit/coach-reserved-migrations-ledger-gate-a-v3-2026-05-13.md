# Gate A v3 — RESERVED_MIGRATIONS Ledger + CI Collision Gate (Task #59)

**Date:** 2026-05-13
**Design:** `audit/reserved-migrations-ledger-design-2026-05-13.md` (v3)
**Prior verdicts:** v1 BLOCK, v2 BLOCK
**Fork lenses:** Steve / Maya / Carol / Coach / Auditor / PM / Attorney
**Verdict:** **APPROVE**

---

## 250-word summary

v3 closes both v2 P0s and the v2 P1 cleanly. The line-anchored, task-sigiled, fence-stripped, filename-filtered claim marker structurally eliminates the self-eating-regex class that broke v2: the design doc was empirically tested against its own v3 regex and returned **0 hits**, and a tree-wide sweep of `audit/*.md` (98 files) also returned **0 hits** — there is no pre-existing literal claim marker to collide with, and the design's own §10 piece-by-piece description does NOT reassemble under the line-anchor regex (verified: 0 matches against the assembled-piece prose). The per-row stale-justification regex (`_PER_ROW_JUSTIFICATION_RE.search(row_line)` scoped to the matched line) closes the doc-scoped exemption hole — one stale row's justification can no longer silently exempt the other 29 rows. The 100..999 range bound (`[1-9]\d{2}`) is enforced via empirical test (099 rejected, 100 accepted, 999 accepted, 1000 rejected). The `_row_line_for` helper's `0*` zero-padding tolerance is empirically safe (matches `| 315 |`, `| 0315 |`; does NOT match `| 3150 |` or `| 31500 |` because of the trailing `\s*\|` anchor). The 4-commit ordering, BLOCKED lifecycle state, 30-row hard cap, and 30-day stale-warn threshold all stand from v2. Counsel Rules 4+5 remain closed structurally. Two minor P2 nits surface but neither is gate-blocking. **Proceed to ship via the 4-commit sequence in §4.**

---

## v2 finding closure matrix

| ID | Description | v3 mechanism | Verified? |
|----|-------------|--------------|-----------|
| P0-NEW #1 | Self-eating regex (v2 captured own examples) | Line-anchored regex + mandatory `task:#NN` sigil + `_CODE_FENCE_RE` strip + `coach-*-gate-{a,b}*.md` filename filter. §10 prose uses piece-by-piece string-of-backticks form that does NOT reassemble under the line-anchor MULTILINE regex. | **YES** — empirical: 0 hits on design doc; 0 hits on full `audit/*.md` sweep |
| P0-NEW #2 | Doc-scoped stale justification (one comment exempted all rows) | `_row_line_for(n)` extracts only the matched row's full Markdown line; `_PER_ROW_JUSTIFICATION_RE.search(row_line)` checks ONLY that row's Notes column | **YES** — design code in §3 lines 242-279 |
| P1-NEW #3 | Range bound `099` accepted | Regex `[1-9]\d{2}` enforces 100..999 | **YES** — empirical: 099 rejects, 100/999 accept, 1000 rejects |

---

## Empirical regex re-verification (executed pre-write)

```
$ grep -E "^<!--[[:space:]]*mig-claim:" audit/*.md
(zero output, exit 0)

$ python3 (v3 regex over the design doc itself)
design doc self-capture count: 0

$ python3 (v3 regex + fence-strip over all audit/*.md)
Total claims found across audit/*.md: 0

$ python3 edge-case probes:
  count: 2 (two markers in one doc — both captured: 317 + 318)
  piece-by-piece §10 reassembly count: 0
  inline-backtick same-line count: 0
  099: 0 match
  100: 1 match
  999: 1 match
  1000: 0 match

$ python3 _row_line_for probes:
  | 315 |: MATCH
  | 0315 |: MATCH (acceptable — zero-padding tolerated)
  | 3150 |: NO MATCH (trailing \s*\| prevents over-capture)
  | 31500 |: NO MATCH
```

All probes pass. The §10 piece-by-piece description is structurally inert: the backticks split the marker into separate code-spans, none of which is a complete `<!-- ... -->` line in the source.

---

## Per-lens verdict

### 1. Engineering (Steve) — **APPROVE**

Empirical regex test executed: 0 hits on the design doc itself, 0 hits on full `audit/*.md` sweep. The §10 piece-by-piece description (`` `<` `!` `-` `-` ` mig-claim:` ... ``) does NOT reassemble under the line-anchor regex — those are 13 separate inline code-spans on a single line of prose, none of which is a `<!-- ... -->` complete line, and the line-anchor `^...$` requires the WHOLE LINE to be the marker. Inline-backtick wrapping like `` `<!-- mig-claim: 315 task:#98 -->` `` cannot satisfy the line-anchor either, because the line contains surrounding prose text. The triple-fence stripping is belt-and-suspenders for the case where a future design adds a fenced code-example showing the literal marker — that's exactly the case v2 broke on. Sound.

### 2. Database (Maya) — **APPROVE**

`_LEDGER_ROW_RE` (line 111-115) captures `(number, status, claimed_at, expected_ship)` from a 7-column markdown table. The Notes column is `.*?\|\s*(\d{4}-\d{2}-\d{2}|—|TBD)\s*\|` consumed via `.*?` before the date — wait, re-tracing: the regex captures groups 1-4 (number, status, claimed_at, expected_ship). The `.*?` between status and claimed_at lazy-matches the Claimed-by + Claimed-at boundary; that's the design intent. HTML comments `<!-- stale-justification: ... -->` inside the Notes column are inline-safe in GFM tables — markdown parsers treat HTML comments as inline pass-through, no rendering breakage. Per-row scope via `_row_line_for(n)` is correct.

### 3. Security (Carol) — **APPROVE**

`_row_line_for` uses `^\|\s*0*{n}\s*\|`. Empirically tested above: `| 315 |` matches, `| 0315 |` matches (zero-pad tolerance — acceptable), `| 3150 |` and `| 31500 |` do NOT match because the trailing `\s*\|` requires the number to be followed only by whitespace and a pipe, not another digit. Bounded correctly. No false-positive collisions.

### 4. Coach — **APPROVE** (3 layers ARE all needed)

The three layers (line-anchor + task-sigil + fence-strip + filename-filter) are NOT redundant. Each closes a distinct attack:

- **Line-anchor:** prevents inline-prose mentions ("the marker `<!-- mig-claim: 315 -->` is...") from counting.
- **Task-sigil:** prevents a future v3 design doc from accidentally claim-marking when echoing an EXAMPLE of the older shape — the sigil is the opt-in.
- **Fence-strip:** lets the design doc itself show a code-fenced literal example for human readers without self-capture.
- **Filename-filter:** belt-and-suspenders for verdict docs (coach-*-gate-{a,b}*.md) which by convention echo claims in prose. Without it, a coach doc that quoted a full marker line outside a fence (e.g., reviewer-suggested edit) would self-capture as a claim.

The filename-filter IS load-bearing because coach docs CAN have line-anchored task-sigiled markers if a reviewer literally quotes one. Removing the filter would re-open the v2 P0 #2 verdict-doc echo class. Keep all 3 layers.

### 5. Auditor (OCR) — **N/A**

Operator-internal coordination artifact. No customer-facing surface, no §164.528 disclosure-accounting impact, no Ed25519 chain. Confirmed N/A.

### 6. PM — **APPROVE**

4-commit ordering still correct and still ~1 day. v3 added ~30 LoC (line-anchor + task-sigil regex + `_row_line_for` helper + `_PER_ROW_JUSTIFICATION_RE`) — within the noise of a 280-line gate file. No slide to 1.5d. Sequence: (1) renumber 5 design docs with markers → (2) ledger drop → (3) CI gate enable + pre-push hook → (4) template + memory. Each commit green at HEAD.

### 7. Attorney (in-house counsel) — **APPROVE**

Rule 5 (no stale doc as authority) is now structurally closed via per-row stale justification (v2 P0 #2 fix) + 30-day warn threshold + hard 30-row cap. The 30-row cap is itself a forcing function — it cannot grow into a stale authority because CI fails at 31. Adding a soft-warn threshold BELOW 30 (e.g., warn at 25) is a P2 nicety, not gate-blocking — CI's hard-fail at 31 catches the breakdown class before it metastasizes.

---

## NEW cross-lens findings

### P2 #1 (Coach, minor) — Filter scope for non-gate coach docs

The filename filter only excludes `coach-*-gate-{a,b}*.md`. Other coach docs that ECHO claim markers in prose (e.g., `coach-enterprise-backlog-2026-05-12.md`, `coach-15-commit-adversarial-audit-*.md`, `coach-heartbeat-timestamp-protocol-*.md`) could in theory self-capture if a reviewer literally quotes a full line-anchored, task-sigiled marker outside any fence. The line-anchor + task-sigil layers should still catch these in practice (echoes are conventionally inside backticks or prose), but defense-in-depth would broaden the filter to `coach-*.md`. **Not gate-blocking** — adopt as a 5-LoC tightening in commit 3 or as a P2 followup.

### P2 #2 (PM, minor) — Soft-warn before 30-row hard cap

Hard-fail at 31 rows is correct but produces a sharp edge. A soft-warn at 25 rows (CI logs warning, doesn't fail) gives a 5-row buffer for coordination. **Not gate-blocking** — adopt opportunistically.

### P2 #3 (Maya, minor) — `_LEDGER_ROW_RE` and column reordering

The current `_LEDGER_ROW_RE` assumes 7-column shape with date at position 4. If a future maintainer adds an 8th column or reorders, the regex silently stops matching rows — `_ledger_rows()` returns `[]` and the row-count test passes. A header-validation assert (parse the table header line and assert column names match expectation) would catch the silent-drift class. **Not gate-blocking** — file as P2 followup.

---

## Final overall verdict

**APPROVE.**

All 3 v2 findings (2 P0s + 1 P1) are closed structurally and verified empirically. No new P0s surface. Three minor P2s noted above but none gate-block. Empirical pre-check returns 0 hits across the design doc and the full `audit/*.md` tree — the self-capture class is closed.

**Proceed to Gate B prerequisites:** ship the 4-commit sequence in §4. Gate B (pre-completion) must verify:

1. All 6 tests in `test_migration_number_collision.py` pass green on HEAD after commit 3.
2. `find appliance/ agent/ -name "*.sql"` re-verified empty (lock the backend-only scope at ship time).
3. CLAUDE.md "Rules" section gets the one-liner pointer.
4. `feedback_migration_number_claim_marker.md` memory file lands in commit 4.
5. Full pre-push test sweep (`bash .githooks/full-test-sweep.sh`) green — diff-only review is NOT sufficient per Session 220 lock-in.

---

**Fork composition:** Steve / Maya / Carol / Coach / Auditor / PM / Attorney
**Verdict location:** `audit/coach-reserved-migrations-ledger-gate-a-v3-2026-05-13.md`
**Empirical evidence:** captured inline in §"Empirical regex re-verification"
