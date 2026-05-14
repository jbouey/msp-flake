# Gate B — Task #70: BAA-draft sign-off gate on D1 operational soak

**Date:** 2026-05-14
**Gate:** B (pre-completion)
**Reviewers (7-lens):** Steve · Maya · Carol · Coach · Auditor (OCR) · PM · Counsel
**Subject:** AS-IMPLEMENTED diff for Task #70 — `docs/legal/v2.0-hardening-prerequisites.md` + `tests/test_baa_artifacts_no_heartbeat_verification_overclaim.py` + `.githooks/pre-push` edit.
**Authority cited:** Task #70 Gate A (`audit/coach-baa-d1-soak-gate-gate-a-2026-05-14.md`).

---

## 250-WORD SUMMARY

Full pre-push sweep post-staging: **257 passed, 0 failed, 0 skipped-without-deps**. New test runs green (3/3). The two new artifacts faithfully implement Gate A's rescoped deliverables — a v2.0 dependency-edge checklist and a thin zero-baseline regression gate — and the doc-vs-Gate-A fidelity check passes on every load-bearing point: it correctly states v1.0-INTERIM does **not** over-claim, the soak bar matches (≥7 days, ≥99% `signature_valid IS TRUE`, per-pubkeyed-appliance floor, all three sibling invariants), and the dates transcribe correctly (soak-clean 2026-05-20, v2.0 target 2026-06-03, fix `adb7671a` 2026-05-13).

**One P1 (Maya):** the embedded soak-check SQL has `GROUP BY 1, appliance_id` but `appliance_id` is **not in the SELECT list**. The query is *legal* Postgres and does not error — but it silently produces one row per (day, appliance) with `appliance_id` invisible, so the reviewer cannot actually apply the per-appliance floor the bar demands. It also **diverges from Gate A's query**, which was a fleet-aggregate daily rollup (`GROUP BY 1` only). Fix: add `appliance_id` to SELECT (and ORDER BY) so the per-appliance floor is verifiable. This is a 2-line correction to a copy-pasteable artifact — must be fixed before close.

**Two P2 polish items (Coach):** add a CLAUDE.md rule pointer for the new v2.0-prerequisites doc pattern; register the doc in the task #51 stale-doc citation registry if one exists.

Gate (Steve): regex/token-window logic sound, `parents[4]` correct, `BASELINE_MAX=0` genuinely true. Scope matches Gate A's 4 dirs exactly. PM: in envelope.

**VERDICT: APPROVE-WITH-FIXES.** One P1 (SQL GROUP BY) must close before Task #70 is marked complete; two P2s carried as polish.

---

## FULL PRE-PUSH SWEEP — POST-STAGING

Staged both new files first (`git add docs/legal/v2.0-hardening-prerequisites.md mcp-server/central-command/backend/tests/test_baa_artifacts_no_heartbeat_verification_overclaim.py`), then ran `bash .githooks/full-test-sweep.sh` from repo root:

```
✓ 257 passed, 0 skipped (need backend deps)
```

Zero failures. `test_pre_push_ci_parity.py` passes (the new test file is now staged, so it is no longer flagged as an untracked source-level test). The new test itself: `3 passed in 0.11s` (`test_scope_is_nonempty`, `test_no_heartbeat_verification_overclaim`, `test_baseline_is_zero`).

Diff-only review explicitly avoided — the full curated sweep was executed and the count is cited per the Session 220 lock-in.

---

## PER-LENS VERDICT

### 1. Steve (technical mechanism) — APPROVE

**`parents[4]` depth:** verified empirically. `tests/test_*.py` → `parents[0]=tests`, `[1]=backend`, `[2]=central-command`, `[3]=mcp-server`, `[4]=/Users/dad/Documents/Msp_Flakes`. Correct repo root.

**Regex soundness:** `_HEARTBEAT_RE = r"heartbeat"` and `_VERIFY_RE = r"verif|signed|signature|cryptograph"`, both `IGNORECASE`. `verif` covers verify/verified/verification; `cryptograph` covers cryptographic/cryptographically; `signed`/`signature` are literal. Sound coverage of the FU-4-feared vocabulary.

**`text.split()` tokenization:** splits on any whitespace run, so a markdown table cell `| heartbeat |` becomes the token `heartbeat` (pipes are adjacent non-whitespace — actually `|heartbeat|` if no spaces, but markdown tables conventionally space-pad, giving clean `heartbeat`). A `.j2` template `{{ x }}` splits into `{{`, `x`, `}}`. The 12-token window is generous enough that "heartbeat" and "verified" on **adjacent lines** of prose WILL co-occur (newlines are whitespace, lines collapse into one token stream) — this is the intended behavior, not a bug: adjacent-line co-occurrence in customer copy IS the regression we want to catch.

**False-negative risk — HTML tag inflation:** in a `.html` file, `<span class="foo">heartbeat</span> ... <em>verified</em>` — the tags `<span`, `class="foo">heartbeat</span>` etc. are separate tokens, so tags DO inflate token distance and could push a real co-occurrence past 12 tokens. This is a theoretical weakness, but: (a) the scoped template dirs are predominantly `.j2`/`.md`/`.sh`/`.txt`, not tag-dense `.html`; (b) `BASELINE_MAX=0` with the *current* clean state means any regression starts from zero, and a copy author introducing the conflation in prose (the realistic vector) will be caught; (c) Gate A scoped this as a *backstop, not a fix*. Acceptable. Noted as a known limitation, not a blocker.

**False-positive risk:** low. "heartbeat" is a rare word in legal/attestation copy; the current scan finds zero occurrences. A future legitimate use ("the appliance sends a heartbeat every 90 seconds" with no nearby verification word) would NOT trip. A sentence like "heartbeats are not individually verified" WOULD trip — but that is correct behavior: per the test docstring, any intentional co-occurrence must first clear the PRE-1 soak bar and then get a documented carve-out. That is the designed escape hatch.

**`BASELINE_MAX=0` genuinely true:** confirmed — `test_no_heartbeat_verification_overclaim` passes, meaning zero co-occurrences across all scoped files today. Re-ran the gate standalone: green.

**Verdict: APPROVE.** Logic is sound; the HTML-tag-inflation edge is a documented limitation consistent with "backstop not fix" scoping.

### 2. Maya (database / soak-check SQL) — APPROVE-WITH-FIXES (P1)

**Schema check (against `tests/fixtures/schema/prod_column_types.json`):** `appliance_heartbeats` has `observed_at` (timestamptz), `signature_valid` (boolean), `appliance_id` (text), `agent_signature` (text), `site_id` (text). `site_appliances` has `agent_public_key` (varchar) and `appliance_id` (varchar). Every column the query references **exists**. Good.

**The GROUP BY defect — P1.** The doc query (lines 60-72):
```sql
SELECT date_trunc('day', observed_at) AS day,
       COUNT(*) AS total,
       COUNT(*) FILTER (WHERE signature_valid IS TRUE) AS verified_true,
       round(... ) AS verified_ratio
  FROM appliance_heartbeats
 ...
 GROUP BY 1, appliance_id
 ORDER BY 1;
```
`GROUP BY 1, appliance_id` — `1` is the positional ref to `date_trunc('day', observed_at)`. `appliance_id` is in GROUP BY but **NOT in the SELECT list**. This is **legal Postgres** — GROUP BY may contain columns absent from SELECT, the query will NOT error. But the *output* is one row per (day, appliance) with `appliance_id` **invisible**: the reviewer sees, e.g., 3 rows all labeled `2026-05-20` with three different ratios and no column telling them which appliance each row is. The per-appliance floor the bar demands ("≥99% per pubkeyed appliance, not fleet aggregate") is **not actually verifiable from this output**.

This also **diverges from Gate A's query** (Gate A §2 lines 40-45), which was a *fleet-aggregate daily rollup* — `GROUP BY 1` only, one row per day, columns `total | sig_valid NOT NULL | sig_valid TRUE | agent_signature NOT NULL`. Gate A's query is the fleet view; the doc tried to make it per-appliance but did so incompletely.

**Fix (2 lines):** add `appliance_id` to the SELECT list and to ORDER BY:
```sql
SELECT date_trunc('day', observed_at) AS day,
       appliance_id,
       COUNT(*) AS total,
       COUNT(*) FILTER (WHERE signature_valid IS TRUE) AS verified_true,
       round(COUNT(*) FILTER (WHERE signature_valid IS TRUE)::numeric
             / NULLIF(COUNT(*), 0), 4) AS verified_ratio
  FROM appliance_heartbeats
 WHERE observed_at > NOW() - INTERVAL '7 days'
   AND appliance_id IN (SELECT appliance_id FROM site_appliances
                         WHERE agent_public_key IS NOT NULL)
 GROUP BY 1, 2
 ORDER BY 2, 1;
```
Now the reviewer can read the per-appliance floor directly. The doc explicitly says "cite actual SQL output in the v2.0 sign-off" — a copy-pasteable query whose output cannot answer the bar it serves is a real defect. **Must fix before close.**

(Minor: the `WHERE observed_at > NOW() - INTERVAL '7 days'` gives a *rolling* 7-day window, fine for an ad-hoc check; the bar says "7 *consecutive* days" — the reviewer reads the per-day rows to confirm consecutiveness, so this is acceptable as long as appliance_id is visible.)

**Verdict: APPROVE-WITH-FIXES.** P1: fix the GROUP BY / SELECT mismatch so the per-appliance floor is verifiable.

### 3. Carol (security / evidence bar) — APPROVE

The doc's soak bar is runtime-proven-not-code-present, exactly Gate A's Carol bar:
- "Trailing 7 consecutive days, per pubkeyed appliance" ✓ (line 40-42)
- "Gate on `signature_valid IS TRUE`, NOT `agent_signature IS NOT NULL`" ✓ explicitly called out with the *why* (lines 45-48) — the 13-day inert window had `agent_signature` non-NULL throughout.
- "Zero open `daemon_heartbeat_signature_unverified`, `daemon_heartbeat_signature_invalid`, `daemon_heartbeat_unsigned`" ✓ all three named (lines 49-51).
- Per-appliance floor "not fleet aggregate (Counsel Rule 4 — orphan coverage)" ✓ cited (line 44).

The framing is correct: PRE-1 protects **v2.0**, not v1.0 — v1.0-INTERIM does not reference heartbeats so D1 inertia was never a v1.0 breach. The doc states this plainly (lines 30-37). No security regression. The one caveat — the SQL as written cannot demonstrate the per-appliance floor — is Maya's P1; once fixed, the evidence bar is fully operational.

**Verdict: APPROVE** (contingent on Maya's P1 so the bar is *measurable*, not just *stated*).

### 4. Coach (did the diff MISS anything? — Session 220 insidious antipattern) — APPROVE-WITH-FIXES (2× P2)

**(a) All THREE invariants listed?** YES — lines 49-51 name `daemon_heartbeat_signature_unverified`, `daemon_heartbeat_signature_invalid`, `daemon_heartbeat_unsigned`. Matches Gate A §3 / §SOAK-CLEAN-EVIDENCE-BAR exactly. No miss.

**(b) Should the regression gate scan beyond the 4 Gate A dirs?** NO — and confirmed it does not. `_TEMPLATE_DIRS` = `attestation_letter`, `wall_cert`, `quarterly_summary`, `auditor_kit` + `MASTER_BAA*.md`. That is *exactly* Gate A's enumeration (§Steve option (a), §RECOMMENDED-MECHANISM line 120). Gate A deliberately scoped 4 dirs; the gate matches "no more, no less." The F1 attestation letter IS `attestation_letter/` — covered. Partner artifacts (P-F5..P-F9) were not in Gate A scope and the BAA over-claim class is specifically the *signed-claim* artifacts; out-of-scope is correct, not a miss.

**(c) CLAUDE.md / memory rule for the new doc pattern? — P2.** There is currently no CLAUDE.md rule pointing at `docs/legal/v2.0-hardening-prerequisites.md` or describing the "engineering-evidence precondition gates a contract sentence" pattern. Given CLAUDE.md is dense with exactly this kind of inviolable-pattern pointer, a one-line entry under Rules — *"v2.0 BAA language asserting a per-event/heartbeat capability MUST clear the matching PRE-N gate in `docs/legal/v2.0-hardening-prerequisites.md` (Task #70); regression-pinned by `test_baa_artifacts_no_heartbeat_verification_overclaim.py`"* — would make the doc discoverable to a future v2.0 drafter who never reads the Gate A audit file. **P2: add the CLAUDE.md pointer.** Not a blocker (the doc cross-references Gate A, the test cross-references the doc, and Task #56's description now carries the PRE-1 pointer — the chain exists), but the CLAUDE.md entry is the canonical discoverability surface.

**(d) task #51 stale-doc citation registry — P2.** Task #51 shipped `POSTURE_OVERLAY.md` + a stale-doc citation CI gate. If that gate maintains a registry of `docs/` files subject to freshness/citation checks, `v2.0-hardening-prerequisites.md` is a *living checklist* ("consult BEFORE drafting") and is a natural registry candidate — a stale v2.0-prerequisites doc that nobody refreshes is precisely the Counsel-Rule-5 failure mode. **P2: check whether task #51's gate has a registry and, if so, add the new doc.** I did not find the registry file in this review's scope; flagging for the implementer to check rather than asserting it exists.

**Coach ruling:** the diff did NOT miss any *correctness* element — all three invariants present, gate scope exact. The two P2s are *discoverability* gaps (CLAUDE.md pointer + stale-doc registry), not correctness gaps. They should be closed or carried as named followups in the same commit per the Session 220 "P1/P2 carried as named TaskCreate followups" rule — but they do not block, since the Gate-A→doc→test cross-reference chain is intact.

**Verdict: APPROVE-WITH-FIXES** (2× P2, carry as named followups if not closed in-commit).

### 5. Auditor / OCR (Counsel Rule 9 — determinism + provenance not decoration) — APPROVE

The doc asserts a *process gate* (PRE-1). Rule 9 test: is it self-consistent and does it cite its authority?
- **Cites authority:** YES — lines 17-18 explicitly cite `audit/coach-baa-d1-soak-gate-gate-a-2026-05-14.md` for the "does not over-claim" finding; line 5 cites Task #70; the test docstring cites the same Gate A file.
- **Self-consistent:** YES — the gated language (per-event/heartbeat verification), the why (13-day inert window), the measurable bar (≥7d / ≥99% / per-appliance / 3 invariants), current status (2026-05-20 earliest, 2897/2897 today), and the CI backstop are all present and mutually consistent. The "How to extend this checklist" section (lines 82-87) gives the doc a deterministic growth contract — each future PRE-N must carry the same five elements. That is provenance-as-structure, not decoration.
- One Rule 9 nit: the doc says "Current prod (2026-05-14): 2897/2897 = 100%" — this is a point-in-time snapshot embedded in a living doc and will go stale. It is *labeled with its date*, so it is honest, but a future reader may mistake it for current. Acceptable (it is dated), but the "How to extend" section could note that status snapshots are dated-and-may-be-stale. Not worth a finding.

**Verdict: APPROVE.** Rule 9 satisfied — the process gate cites its authority and is internally deterministic.

### 6. PM (effort + envelope) — APPROVE

Gate A budgeted "~1 hour + a checklist line." AS-IMPLEMENTED: one ~90-line doc, one ~140-line test (3 test functions), one pre-push line. That is squarely in the ~1hr + 10min envelope. No scope creep — the standing dashboard Gate A rejected was correctly NOT built. The two P2s (CLAUDE.md line, registry check) are ~10 min combined. The one P1 (SQL fix) is a 2-line edit. Total remaining work to close: <20 min. **In envelope. Verdict: APPROVE.**

### 7. Counsel (LOAD-BEARING — doc-vs-Gate-A fidelity) — APPROVE-WITH-FIXES

I read both `docs/legal/v2.0-hardening-prerequisites.md` and `audit/coach-baa-d1-soak-gate-gate-a-2026-05-14.md` in full. The dispositive question: does the doc faithfully transcribe Gate A's findings, the soak bar, and the dates — without itself over- or under-stating?

**"Does not over-claim" finding — faithful.** Doc lines 16-19: *"v1.0-INTERIM was checked and does **not** over-claim (see Task #70 Gate A...)."* Doc lines 30-37 correctly explain *why*: every "cryptographically signed" claim scopes to evidence bundles (`compliance_bundles`), which were continuously signed; the BAA never references heartbeats. This is an exact, non-embellished transcription of Gate A's load-bearing Counsel conclusion (Gate A §"DOES THE BAA OVER-CLAIM? — NO"). The doc does **not** over-state (it does not claim the BAA is bulletproof on other axes) nor under-state (it does not hedge the clean finding into ambiguity). Faithful.

**Soak bar — faithful on every element:**
- ≥7 consecutive days ✓ (doc line 27-28, 40; Gate A §SOAK-CLEAN line 126)
- ≥99% ✓ (doc line 42; Gate A line 127)
- `signature_valid IS TRUE` not `agent_signature IS NOT NULL` ✓ (doc lines 45-48; Gate A line 128) — and the doc reproduces Gate A's *reasoning* for the distinction.
- per-pubkeyed-appliance floor, not fleet aggregate ✓ (doc lines 41-44; Gate A line 127) — both cite Counsel Rule 4.
- all THREE sibling invariants ✓ (doc lines 49-51; Gate A line 129).
No drift on the bar's substance.

**Dates — faithful:** earliest soak-clean **2026-05-20** ✓ (doc line 53; Gate A line 131); v2.0 target **2026-06-03** ✓ (doc line 54; Gate A line 131); fix `adb7671a` deployed 2026-05-13 14:20 EDT = day 0 ✓ (doc lines 31-32, 53; Gate A line 131); 13-day inert window 2026-04-30→2026-05-13 ✓ (doc line 31; Gate A §250-word-summary). "~13 days margin" ✓ (doc line 54; Gate A line 131 "13 days margin"). No date drift.

**The one Counsel-relevant defect:** the embedded SQL (Maya's P1). The doc instructs the v2.0 sign-off reviewer to "cite actual SQL output" — but the query as written cannot demonstrate the *per-appliance floor* the bar legally requires (Counsel Rule 4 orphan-coverage). A process-gate doc whose own evidence query cannot answer its own bar is a fidelity gap between the *stated* bar and the *operable* bar. This is a P1, not a P0 — the bar is *correctly stated in prose*; only the SQL helper is incomplete, and it is a 2-line fix. But it must close before Task #70 is marked complete, because the doc will be handed to outside counsel / a v2.0 reviewer as an operable instrument.

**Counsel verdict: APPROVE-WITH-FIXES.** The doc faithfully transcribes Gate A — no factual drift on the finding, the bar, or the dates. The single P1 (SQL cannot demonstrate the per-appliance floor) must be fixed so the doc is operable, not merely accurate-in-prose.

---

## SQL-CORRECTNESS ASSESSMENT

**Will it error?** No. `GROUP BY 1, appliance_id` with `appliance_id` absent from SELECT is legal Postgres — GROUP BY may include columns not projected. All referenced columns (`observed_at`, `signature_valid`, `appliance_id` on `appliance_heartbeats`; `appliance_id`, `agent_public_key` on `site_appliances`) exist per `prod_column_types.json`. The query is **syntactically and semantically valid** and copy-pasteable.

**Is it correct for its purpose?** **No — P1.** It groups per-(day, appliance) but does not project `appliance_id`, so the output is N indistinguishable rows per day. The reviewer cannot apply the per-appliance ≥99% floor (the whole point of the bar, per Counsel Rule 4). It also diverges from Gate A's query, which was an intentional fleet-aggregate daily rollup (`GROUP BY 1` only). The doc author appears to have started from Gate A's fleet query, added `appliance_id` to GROUP BY to make it per-appliance, but forgot to add it to SELECT.

**Required fix:** add `appliance_id` to the SELECT list, change `GROUP BY 1, appliance_id` → `GROUP BY 1, 2`, change `ORDER BY 1` → `ORDER BY 2, 1`. 2-line net change. After the fix the output is one row per (appliance, day) with the appliance visible — directly readable against the per-appliance floor.

---

## DOC-VS-GATE-A FIDELITY CHECK

| Element | Gate A | v2.0-prerequisites doc | Match |
|---|---|---|---|
| BAA over-claims? | NO (load-bearing Counsel) | "does **not** over-claim" (L16-19) | ✓ |
| Why clean | claims scope to evidence bundles | same, L30-37 | ✓ |
| Soak window | 7 consecutive days | 7 consecutive days (L27, L40) | ✓ |
| Threshold | ≥99% | ≥99% (L42) | ✓ |
| Metric column | `signature_valid IS TRUE` not `agent_signature IS NOT NULL` | same + reasoning (L45-48) | ✓ |
| Floor type | per-pubkeyed-appliance, not fleet agg (Rule 4) | same (L41-44) | ✓ |
| Invariants | all 3 siblings | all 3 named (L49-51) | ✓ |
| Earliest soak-clean | 2026-05-20 | 2026-05-20 (L53) | ✓ |
| v2.0 target | 2026-06-03 | 2026-06-03 (L54) | ✓ |
| Fix commit / date | `adb7671a`, 2026-05-13 14:20 EDT | same (L31-32, L53) | ✓ |
| Margin | 13 days | ~13 days (L54) | ✓ |
| Current prod | 2897/2897 = 100% | 2897/2897 = 100% (L55-56) | ✓ |
| Soak-check SQL | fleet-aggregate `GROUP BY 1` | per-appliance attempt, `GROUP BY 1, appliance_id` — **incomplete** | ✗ P1 |

**No factual drift.** Every finding, every number, every date transcribes faithfully. The single divergence is the SQL — and it is a *completeness* defect (appliance_id not projected), not a *factual* drift. Fixing it brings the doc's SQL into a *better* state than Gate A's (Gate A's was fleet-aggregate; the doc's intent is correctly per-appliance, it just needs the SELECT fix).

---

## MISSING-ADDITIONS (Coach probe)

| Item | Severity | Status |
|---|---|---|
| All 3 sibling invariants in soak bar | — | Present (L49-51). Not missing. |
| Regression gate scans beyond 4 Gate-A dirs | — | Correctly NOT done — gate scope = Gate A scope exactly. Not missing. |
| CLAUDE.md rule pointer for v2.0-prerequisites pattern | P2 | **Missing** — add one-line Rules entry pointing at the doc + test. |
| task #51 stale-doc registry entry | P2 | **Check needed** — if task #51's gate maintains a doc registry, add `v2.0-hardening-prerequisites.md`. Implementer to verify registry exists. |
| Task #56 PRE-1 pointer | — | Done (in task store, not git diff — acceptable, task descriptions are not versioned). |

---

## FINAL VERDICT

**APPROVE-WITH-FIXES.**

All 7 lenses APPROVE or APPROVE-WITH-FIXES. No BLOCK. Full pre-push sweep: **257 passed, 0 failed** post-staging. The two artifacts faithfully implement Gate A's rescoped deliverables and the doc-vs-Gate-A fidelity check is clean on every finding, number, and date.

**Must close before Task #70 is marked complete:**
- **P1 (Maya/Counsel):** fix the soak-check SQL — add `appliance_id` to the SELECT list, `GROUP BY 1, 2`, `ORDER BY 2, 1`. The doc instructs the reviewer to cite this query's output against a per-appliance floor; as written the output cannot show per-appliance results. 2-line fix to a copy-pasteable artifact.

**Carry as named followups in the same commit (Session 220 rule) if not closed in-commit:**
- **P2 (Coach):** add a CLAUDE.md Rules entry pointing at `docs/legal/v2.0-hardening-prerequisites.md` + the regression test, so a future v2.0 drafter discovers the gate without reading the Gate A audit file.
- **P2 (Coach):** verify whether task #51's stale-doc citation gate maintains a doc registry; if so, register `v2.0-hardening-prerequisites.md` (it is a living checklist and a Counsel-Rule-5 staleness candidate).

No new P0. The P1 is a 2-line SQL correction; the two P2s are ~10 min of discoverability wiring. Total remaining work to close Task #70: <20 minutes. Commit body must cite both Gate A (`coach-baa-d1-soak-gate-gate-a-2026-05-14.md`) and this Gate B verdict.
