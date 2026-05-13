# Coach Gate A v2 verdict — RESERVED_MIGRATIONS ledger + CI collision gate (Task #59)

**Reviewed:** `audit/reserved-migrations-ledger-design-2026-05-13.md` (v2)
**v1 verdict:** `audit/coach-reserved-migrations-ledger-gate-a-2026-05-13.md` (BLOCK — 3 P0 + 2 P1 + 1 P2)
**Date:** 2026-05-13
**Reviewer:** Class-B 7-lens fork — adversarial Gate A v2 (second pass)
**Final verdict:** **BLOCK** — 2 new P0s surface from empirical regex verification (the v2 fix is structurally right but the regex eats its own design + verdict docs). 1 new P1. v1 P0 #2 and v1 P1 #1 are not fully closed.

---

## 250-word summary

The v2 design correctly identifies and addresses every v1 finding at the SHAPE level — claim-marker swap eliminates the verdict-doc filter, 4-commit ordering replaces single-commit ship, `expected_ship_date` + 30-row cap + BLOCKED status all addressed. **But empirical verification of the regex on the actual repo surfaces 2 new P0s the design didn't anticipate.**

**P0-NEW #1 — Self-eating regex (code-fence + prose-example capture).** The Python regex `<!--\s*mig-claim:\s*(\d{3})\s*-->` is markdown-blind. A literal grep of `audit/*.md` shows 8 hits TODAY: 7 in the v2 design doc (including line 19's fenced code-block example + lines 253-257's commit-1 instructions) and 1 in the v1 verdict doc (line 48 prose example). On Commit 3 (gate enable), `test_no_two_docs_claim_same_unshipped_migration` FAILS on mig 315 (3 hits across 2 docs). The 4-commit ordering claim "all 6 tests pass green on first run" is empirically false.

**P0-NEW #2 — Stale-justification check is doc-scoped, not row-scoped.** `_STALE_JUSTIFICATION_RE.search(text)` searches the WHOLE ledger document. ONE `<!-- stale-justification: ... -->` anywhere satisfies ALL stale rows. The test passes when the FIRST stale row gets a justification — every subsequent stale row is silently exempt. v1 P1 #1 is not closed; it's papered over.

**P1-NEW #3 — Range bound missing.** Regex accepts `mig-claim: 999` (3-digit shape only). At enterprise scale this is fine, but a claim of `mig-claim: 001` against a long-shipped number would be silently rejected as "not in ledger" without diagnosing the human error.

Verdict: BLOCK pending exclusion mechanism for design/verdict docs that legitimately contain prose markers, and per-row stale-justification association.

---

## Empirical-verification report (regex run on real repo)

```
$ python3 -c '...regex over audit/*.md...'
mig 311: 1 hits -> ['reserved-migrations-ledger-design-2026-05-13.md']
mig 314: 1 hits -> ['reserved-migrations-ledger-design-2026-05-13.md']
mig 315: 3 hits -> ['reserved-migrations-ledger-design-2026-05-13.md', 'reserved-migrations-ledger-design-2026-05-13.md', 'coach-reserved-migrations-ledger-gate-a-2026-05-13.md']
mig 316: 1 hits -> ['reserved-migrations-ledger-design-2026-05-13.md']
mig 317: 1 hits -> ['reserved-migrations-ledger-design-2026-05-13.md']
mig 318: 1 hits -> ['reserved-migrations-ledger-design-2026-05-13.md']

$ find appliance/ agent/ -name "*.sql" -o -name migrations -type d
(empty — confirms scope-empty claim ✓)

$ ls migrations/ | grep 311_
(empty — mig 311 NOT shipped; gap in 306, 311 between 305→307 and 310→312)

$ _LEDGER_ROW_RE match against 6 proposed rows
Matches: 6/6 ✓
```

**The regex captures markers regardless of markdown context.** Code fences (line 19 of design doc) and prose examples (line 48 of v1 verdict doc, lines 253-257 of v2 design doc) are not excluded. The design doc, by enumerating its 5 Commit-1 marker examples in the body, becomes a self-firing artifact on first push.

---

## v1 P0/P1/P2 closure matrix

| Item | v1 finding | v2 fix attempted | Closed? |
|------|------------|------------------|---------|
| **P0 #1** Steve regex over-capture | Greedy `\bmig\s+\d{3}\b` matches historical refs | Marker `<!-- mig-claim: NNN -->` | **PARTIAL** — false-positive class on historical prose is closed (✓), but a NEW false-positive class on prose-examples-of-markers is opened (P0-NEW #1) |
| **P0 #2** Coach verdict-doc filter hole | 17 docs ECHO claims, filter coverage gap | Marker eliminates need for filter | **NO** — v1 verdict doc line 48 contains a prose example of the marker (`<!-- mig-claim: 315 -->`) and the regex captures it; coach docs are NOT marker-free in practice (see P0-NEW #1) |
| **P0 #3** PM ordering trap | Single-commit lands gate + design refs together | 4-commit ordering | **PARTIAL** — order is correct, but Commit 3's claim "all 6 tests pass green" is empirically false because the v2 design doc + v1 verdict doc are themselves gate-detectable (P0-NEW #1) |
| **P1 #1** Stale-doc cleanup | Counsel Rule 5 — ledger becomes stale | `expected_ship` + 30d warn + cap | **NO** — `_STALE_JUSTIFICATION_RE.search(text)` is doc-scoped not row-scoped; one marker satisfies all stale rows (P0-NEW #2) |
| **P1 #2** Maya scope-empty | No header docs backend-only | Header line + `find` empirical | **YES** — verified empty via `find appliance/ agent/ -name "*.sql"` returns zero; header asserts backend-only correctly |
| **P2** BLOCKED status | Vault P0 #43 needs 4th state | Added to status enum | **YES** — design §5(e) names BLOCKED; mig 311 (Vault) ledger row uses it; status enum (reserved / in_progress / blocked / shipped) captured |

**Score: 2 of 6 fully closed (P1 #2, P2). 2 PARTIAL (P0 #1, P0 #3 — structurally right, empirically miss). 2 NOT closed (P0 #2, P1 #1 — the marker shape eats verdict docs; stale check is doc-scoped).**

---

## New cross-lens findings (v2 pass)

### P0-NEW #1 — Regex captures its own design + verdict prose examples

**Where:** `_CLAIM_MARKER_RE = re.compile(r"<!--\s*mig-claim:\s*(\d{3})\s*-->")` is markdown-agnostic. It matches:

- The v2 design doc's §1 fenced example (line 19): `<!-- mig-claim: 315 -->` inside a ` ```markdown ` fence.
- The v2 design doc's §4 commit-1 instructions (lines 253-257): 5 markers as literal examples of "what each design doc should add".
- The v1 verdict doc's §Steve recommendation (line 48): `<!-- mig-claim: 315 -->` as a prose example.

**On Commit 3 (gate enable):**
- `test_no_two_docs_claim_same_unshipped_migration` FAILS on mig 315 (3 hits across 2 docs: v2 design × 2 + v1 verdict × 1).
- `test_every_claim_marker_in_ledger` passes IF Commit 2's ledger has 311/314/315/316/317/318 (it does), so this one is green.
- `test_no_claim_marker_for_shipped_migration` passes (311, 314-318 not on disk).

**Fix shapes (pick one):**

- **(a) Exclude fenced code blocks + the design+verdict docs themselves.** Add explicit exclusion of `audit/reserved-migrations-ledger-design-*.md` and `audit/coach-reserved-migrations-ledger-*.md` to the audit-doc glob. Brittle (depends on file naming).
- **(b) Strip fenced code blocks before matching.** Pre-process each `audit/*.md` to remove ``` ``` ``` fences before running regex. Solves code-fence case (line 19) but NOT line 253-257 (those are plain bullet points, not in a fence) or line 48 of the v1 verdict (also plain prose).
- **(c) Make the marker unique enough that prose examples can't accidentally match.** E.g., require the marker to appear on a line BY ITSELF (no leading content): `^<!--\s*mig-claim:\s*(\d{3})\s*-->\s*$`. The design's line 253-257 violates this (`- MTTR soak v2 design: \`<!-- mig-claim: 315 -->\` (was historical claim of 311)`). The v1 verdict's line 48 violates it. Closes most cases.
- **(d) Marker requires a SIGIL.** E.g., `<!-- mig-claim: NNN | task:#NN -->` with the `task:#NN` field mandatory. Prose examples won't include arbitrary task numbers. Robust + unambiguous.

**Recommend (c)+(d) combined:** marker MUST appear on its own line (anchored `^...$` per `re.MULTILINE`) AND MUST carry a `task:#NN` field. A prose example doesn't accidentally satisfy both.

Updated regex: `r"^\s*<!--\s*mig-claim:\s*(\d{3})\s+task:#(\d+)\s*-->\s*$"` with `re.MULTILINE`.

This also enables P1 #1 row-scoping (tied to task number).

### P0-NEW #2 — Stale-justification check is doc-scoped, not row-scoped

**Where:** `test_no_stale_ledger_rows_without_justification`:

```python
for r in rows:
    # ... staleness math ...
    if not _STALE_JUSTIFICATION_RE.search(text):  # `text` = FULL LEDGER
        stale.append(f"mig {r['n']} ...")
```

Once ONE stale row gets a `<!-- stale-justification: ... -->` comment, the `_STALE_JUSTIFICATION_RE.search(text)` returns a match for EVERY row. All subsequent stale rows pass silently.

**Worked failure case:** Ledger has 10 rows, 5 of them stale. Operator adds a stale-justification for row #1. Test passes — but rows #2-#5 remain stale without justification.

**Fix:** Either (a) require the stale-justification marker on the SAME line as the row (`| 311 | ... | <!-- stale-justification: BLOCKED-on-staging --> |`) and parse it per-row, or (b) require the marker to immediately precede the row in a deterministic location and parse the (row, justification) pair together. Approach (a) keeps Markdown rendering reasonable.

This is a Rule-5-class regression. v1 P1 #1 is structurally right (`expected_ship_date` added, cap added) but the enforcement test would NOT detect stale rows in the real failure mode.

### P1-NEW #3 — No range bound on claim-marker

`_CLAIM_MARKER_RE = (\d{3})` accepts 100-999. A typo `<!-- mig-claim: 099 -->` parses as valid claim of 99; `test_no_claim_marker_for_shipped_migration` checks against on-disk `[0-9][0-9][0-9]_*.sql` — `099_*.sql` doesn't exist so this passes; `test_every_claim_marker_in_ledger` fails with a generic "mig 99 claimed by X not in ledger" — operator has no signal this was a typo vs intentional.

**Fix:** assert `n >= 314` (current top-of-ledger lower bound) OR `n > max(shipped) - 5` to allow ledger-aware claims while rejecting obvious typos. Or just emit a friendlier error: "mig {n} below highest-shipped (313) — likely typo; claims should target NNN >= 314".

This is P1 not P0 — false-cleanup ratio is low and the test still surfaces the issue, just unhelpfully.

---

## Per-lens verdict

### Lens 1 — Engineering (Steve): **BLOCK**

**Empirical regex test (the load-bearing probe):**
```
$ grep -rEho '<!--[[:space:]]*mig-claim:[[:space:]]*[0-9]{3}[[:space:]]*-->' audit/*.md | wc -l
8
```

Not zero. Eight matches across the v2 design doc + v1 verdict doc — both of which are committed to the repo TODAY. The design's claim that historical prose stays unmarked is true for HISTORICAL doc bodies (no doc pre-2026-05-13 carries the marker shape) but FALSE for the design + verdict docs that introduce the convention itself.

Code-fence edge case is real (line 19). Lines 253-257 are bullet-list prose examples. Line 48 of v1 verdict is plain prose. None excluded.

The `re.IGNORECASE` flag on both regexes is fine (no functional concern). `_LEDGER_ROW_RE` empirically matches all 6 proposed rows (verified mentally + by Python smoke). Date parsing handles ISO format only — sufficient because the regex itself constrains the date column to `\d{4}-\d{2}-\d{2}` or `—|TBD`, so non-ISO input simply doesn't match the row regex and the row is silently skipped — that's a latent class hazard but acceptable given the rigid format.

**Verdict:** BLOCK — claim-marker shape needs SIGIL + LINE-ANCHOR (per P0-NEW #1 fix recommendation).

### Lens 2 — Database (Maya): **APPROVE**

**Scope-empty verification:** `find /Users/dad/Documents/Msp_Flakes/appliance /Users/dad/Documents/Msp_Flakes/agent -name "*.sql" -o -name "migrations" -type d` returns ZERO. The design's header assertion holds.

`_LEDGER_ROW_RE` correctly parses the 6 proposed rows including `—`/`TBD` placeholders. Date parsing in `test_no_stale_ledger_rows_without_justification` uses `datetime.date.fromisoformat(r["expected_ship"])` — handles `YYYY-MM-DD` correctly; non-ISO strings are skipped silently (acceptable, since the row regex pre-constrains the column).

No DB-class concern. Ledger doesn't touch `compliance_bundles` IMMUTABLE list, doesn't trigger RLS, doesn't interact with partitioning.

**Verdict:** APPROVE.

### Lens 3 — Security (Carol): **APPROVE-WITH-FIXES**

**Bad-actor-inflate attack:** The 30-row cap mitigates the bulk-claim attack. A 31-row PR fails CI. Single-row gradual inflation is theoretically possible but visible in every diff and bounded by 30 total — coordination breakdown would surface before exploitation completed.

**Edit-access model:** Ledger lives in `mcp-server/central-command/backend/migrations/RESERVED_MIGRATIONS.md` — same write-permission scope as migration files themselves. No new privilege boundary introduced. The ledger is git-tracked + reviewable diff per row + author attribution.

**No PHI / no privileged action / no signing material in ledger.** Operator-internal coordination surface. Rule 1/2/3/6/7 N/A.

**Risk rating:** Low. Cap + diff-review render the inflate-attack non-trivial without obvious detectability.

**Verdict:** APPROVE-WITH-FIXES (no new P0; carry forward P0-NEW #1 because it's a correctness bug not a security bug).

### Lens 4 — Coach: **BLOCK**

**Double-build check:** v2 doesn't introduce a parallel registry. The ledger remains pure-Markdown; `MEMORY.md` + `SUBPROCESSORS.md` precedent intact.

**Could 4-commit ordering collapse to 2 commits?** Re-tracing:

- **Commit 1 alone (renumbers + ledger + gate + template):** Gate runs. Ledger has 6 rows. Markers in design docs. Gate passes only IF NO mig-claim marker appears in v1 verdict doc or v2 design doc body — but they DO appear (P0-NEW #1). Commit 1 fails CI.
- **Commit 1 (renumbers + ledger) + Commit 2 (gate + template):** Same problem on Commit 2. Failure.

The 4-commit ordering doesn't solve the self-eating-regex problem because the gate must eventually enable, and the moment it enables, the design doc itself fires. **The ordering is correct in principle but the regex shape forces the gate to fire on the very doc that defines it.**

**Recommend:** Adopt the SIGIL fix (P0-NEW #1 recommendation) — the design's prose examples become `<!-- mig-claim: 315 task:#98 -->` only on lines where they're intended as REAL claims (none in the design doc; all 5 lines in §4 become prose like "use the form `<!-- mig-claim: 315 task:#98 -->`"). After SIGIL fix, the design + verdict docs naturally exclude themselves because they don't carry task IDs in their prose examples.

**Verdict:** BLOCK — same root cause as Steve.

### Lens 5 — Auditor (OCR): **N/A** (confirmed)

Operator-internal CI gate. No §164.528 disclosure-accounting touch. No customer-facing artifact. No auditor-kit interaction. v2 §6 correctly N/As Rule 1/2/3/6/7.

### Lens 6 — PM: **APPROVE-WITH-FIXES**

**Cost estimate:** v1 said 4-5 hours. v2 adds: SIGIL regex tightening + line-anchor (15 min), per-row stale-justification parser (30 min), test #6 rewrite (15 min), edge-case tests for self-eating-regex (15 min). Total: 5-6 hours, still 1 day reasonable.

**4-commit ordering vs 2-commit collapse:** The 4-commit ordering serves another purpose besides green-on-each-commit — it provides reviewers a clean diff per concern (renumbers / ledger / gate / template). Even if green-on-each-commit collapsed to 2 commits (renumbers+ledger / gate+template), the 4-commit shape is preferable for review hygiene. KEEP 4 commits but understand they don't structurally solve the self-eating regex.

**Verdict:** APPROVE-WITH-FIXES — cost estimate holds; ordering rationale upgraded from "must be 4 commits for green-on-each" to "should be 4 commits for review hygiene + structural-fix prerequisite".

### Lens 7 — Attorney (in-house counsel): **APPROVE-WITH-FIXES**

**Rule 5 (no stale doc as authority):** The hard 30-row cap + 30-day stale-warn structure is correct. **BUT P0-NEW #2 reveals the stale-justification enforcement is doc-scoped not row-scoped.** A ledger with 10 stale rows and 1 justification line silently passes CI. Counsel-rule compliance requires per-row association.

**Is the 30-row cap enforcement itself a Rule 5 trigger?** The cap is enforced AT a hard ceiling, not as a soft signal. If a busy sprint hits 31 rows, CI BLOCKS and the round-table is forced — that's correct Rule 5 mechanics (no silent staleness). The cap is not a Rule 5 violation; the cap PREVENTS one.

**Pure-CI hygiene or human-judgment-needed?** Hybrid. CI catches the 30-row breach and the 30-day-without-justification breach. Human judgment decides whether to ship, mark BLOCKED, or release. CI mechanics + human decision = Rule 5 compliant — once P0-NEW #2 closes.

**Verdict:** APPROVE-WITH-FIXES — Rule 5 compliance requires per-row stale-justification parsing (P0-NEW #2). The cap mechanism itself is correctly designed.

---

## Final overall verdict

**BLOCK pending P0-NEW #1 and P0-NEW #2 closure.**

The v2 design is structurally correct on every dimension v1 flagged. The marker shape eliminates the historical-prose false-positive class. The 4-commit ordering is the right review hygiene. The 30-row cap + BLOCKED status + scope header all close v1 findings cleanly.

**But empirical regex verification on the actual repo reveals the marker regex eats its own design + verdict docs.** Eight matches today, including 3 hits for mig 315 across 2 docs — gate fires on first enable. And the stale-justification enforcement is doc-scoped not row-scoped — silently exempts every stale row after the first justification.

**Closure path to APPROVE-as-is or APPROVE-WITH-FIXES:**

1. **P0-NEW #1:** Tighten claim-marker regex to require both line-anchor (`^...$` with `re.MULTILINE`) AND task-id sigil (`task:#NN`). Updated regex: `r"^\s*<!--\s*mig-claim:\s*(\d{3})\s+task:#(\d+)\s*-->\s*$"`. Rewrite design+verdict prose examples to omit the task field so they don't accidentally satisfy.
2. **P0-NEW #2:** Per-row stale-justification parsing. Move marker to same-line as the row (last column of the table or inline cell content). Test iterates rows and parses each row's justification independently.
3. **P1-NEW #3:** Add diagnostic for `n < highest_shipped - 5` to catch typos with a friendlier error.

Re-issue as design v3 with these changes; expect APPROVE on third-pass Gate A if the regex is empirically verified to return 6 hits (the 6 ledgered claims) on `audit/*.md` and zero hits on the design + verdict docs themselves.

**The 4-commit ordering should be preserved (review hygiene), but the green-on-each-commit guarantee depends on P0-NEW #1 fix landing first.**

---

## Recommendation

**Author next step:** Patch the design v2 → v3 with the two regex tightenings. Re-run the empirical grep (`python3 -c "regex over audit/*.md"`) and confirm the output is `mig 311/314/315/316/317/318: 1 hit each → reserved-migrations-ledger-design-2026-05-13.md` only (no v1 verdict, no prose-example pollution). Document the empirical-pass output in the design's §3 as evidence-of-fix. Then re-fork Gate A v3.

**Coach's 250-word summary above stands as the load-bearing verdict surface.**
