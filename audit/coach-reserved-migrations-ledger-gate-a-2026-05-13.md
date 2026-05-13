# Coach Gate A verdict — RESERVED_MIGRATIONS ledger + CI collision gate (Task #59)

**Reviewed:** `audit/reserved-migrations-ledger-design-2026-05-13.md`
**Date:** 2026-05-13
**Reviewer:** Class-B 7-lens fork — adversarial Gate A
**Final verdict:** **BLOCK** — design is structurally sound, but the regex shape is catastrophically false-positive (78 unique mig refs in `audit/` would fire on day 1, 308 shipped migs to collide against), the verdict-doc filter has a hole (17 coach docs that aren't `gate-a/b` are unfiltered), and the implementation-plan ordering buries 3 must-renumber design docs behind the gate that would block them.

---

## 250-word summary

The motivation is real and the structural fix is the right shape — 3 collisions in 6 designs in a single day proves a coordination surface is overdue. Markdown-ledger precedent (`MEMORY.md`, `SUBPROCESSORS.md`) is legitimate; ledger format is clean. **But the design fails Gate A on three concrete grounds.**

**(1) Regex over-capture.** `\bmig(ration)?\s+\d{3}\b` matches every historical reference — "mig 012 stores", "mig 257 site-rename allowlist", "mig 283 BAA-receipt", "mig 138 partitioned `compliance_bundles`". A grep of `audit/*.md` returns **78 unique mig numbers**, of which **308 are already shipped** — every historical reference in every coach-* doc fires `test_no_audit_doc_claims_shipped_migration`. Need a CLAIM-shape regex (e.g. `(introduces|adds|claims|new)\s+mig\s+\d{3}` or an explicit `<!-- mig-claim: NNN -->` marker).

**(2) Verdict-doc filter has a hole.** Carve-out is `coach-*-gate-{a,b}-*.md`, but **17 of 98 coach docs don't match** — `coach-baa-contract-language-2nd-eye-*`, `coach-counsel-packet-review-*`, `coach-15-commit-adversarial-audit-*`, `coach-d1-heartbeat-timestamp-protocol-*`, `coach-enterprise-backlog-*`, `coach-e2e-attestation-audit-*`. All cite shipped migs historically. Filter must broaden to `coach-*.md` or use a doc-front-matter marker.

**(3) Ordering trap.** §5 ships the gate in one commit with 3 known-failing design docs un-renumbered. Day-1 push fails CI. Must renumber FIRST, ledger SECOND, gate THIRD — or ratchet-baseline with today's 3 collisions and drive-down.

P0s: regex shape, verdict-doc filter, ordering. P1s: stale-reservation cleanup needed Day 1 (counsel Rule 5), no `appliance/`/`agent/` migration spaces exist so scope decision is "backend-only" — document it. P2: no multi-repo concern today.

---

## Per-lens verdict

### Lens 1 — Engineering (Steve): **BLOCK**

**Finding:** The regex `\bmig(?:ration)?\s+(\d{3})\b` is catastrophically over-broad. Empirical grep of `audit/*.md`:

```
grep -rEho '\bmig(ration)?\s+[0-9]{3}\b' audit/*.md | sort -u | wc -l
→ 78 unique mig-numbers referenced
```

Of those 78, **at least 50** are HISTORICAL references to shipped migrations (003, 011, 012, 013, 031, 078, 106, 109, 119, 126, 137, 138, 142, 146, 151, 156, 157, 175, 176, 177, 182, 213, 218, 223, 224, 233, 234, 235, 251, 256, 257, 258, 259, 261, 263, 264, 268, 271, 273, 274, 281, 282, 283, 284, 285, 289, 290, 292, 294, 295, 296, 297, 298, 300, 302, 303, 304, 305, 306, 307, 308, 309, 312, 313). All 308 currently-shipped migrations.

`test_no_audit_doc_claims_shipped_migration` would fire on EVERY ONE of these references the moment the gate lands. Concrete false-positive examples:

- `audit/coach-baa-expiry-enforcement-gate-a-2026-05-13.md:7` — "mig 146, 2026-03" (HISTORICAL fact, not a claim)
- `audit/coach-canonical-metric-drift-invariant-gate-a-2026-05-13.md:104` — "mig 012 stores the bytes the Ed25519 signature covers" (HISTORICAL reference)
- `audit/coach-baa-contract-language-2nd-eye-2026-05-13.md:36` — "mig 283 (BAA-relocate-receipt) has NO grace period" (HISTORICAL reference)
- `audit/canonical-source-registry-design-2026-05-13.md:181` — "JOIN via runbooks.agent_runbook_id column, mig 284" (HISTORICAL reference)

The design's open question (b) flags this — but the answer is NOT "let the renumber-on-collision pattern handle it." The answer is: **the regex must capture CLAIMS, not REFERENCES.**

**Required fix:** explicit-claim marker. Two viable shapes:

- **Shape A (HTML-comment marker):** `<!-- mig-claim: 315 -->` in the design's §Schema block. Regex: `<!--\s*mig-claim:\s*(\d{3})\s*-->`.
- **Shape B (heading-based):** require designs to have a `### Schema (mig NNN — …)` heading. Regex: `^###\s+Schema\s+\(mig\s+(\d{3})`.

Both shapes are unambiguous about intent (CLAIM vs reference). Both are zero-false-positive on current audit/ corpus. **Shape A is preferred** — it's invisible in rendered Markdown and survives doc reorgs.

**Verdict:** **BLOCK** until regex shape resolves to claim-marker.

### Lens 2 — Database (Maya): **APPROVE-WITH-FIXES**

**Finding:** Confirmed there is NO other migration number-space in the repo:

```
find appliance/ agent/ -name "*.sql"
→ (empty)
```

The Go appliance and Go workstation agent do not have their own migration spaces — both target Postgres on Central Command. The single migration directory at `mcp-server/central-command/backend/migrations/` is THE space.

**Open question (e) resolved:** No multi-repo scope needed. But the ledger should explicitly document this fact (a future appliance-local SQLite migration space would otherwise silently escape the gate). One-liner at the top of `RESERVED_MIGRATIONS.md`:

> Scope: this ledger covers `mcp-server/central-command/backend/migrations/` only. The Go daemon and Go workstation agent do not have their own migration spaces today. If that changes, this ledger must be split or renamed.

**Partition-table maintainer check:** `background_tasks.py::partition_maintainer_loop` creates partitions for `promoted_rule_events` (verified via search). It does NOT create new migration files — partitions are `CREATE TABLE … PARTITION OF`, no mig file. No conflict with the gate.

**`compliance_bundles` IMMUTABLE-list interaction:** The ledger is operator-internal Markdown — does NOT touch compliance bundles. No Rule 1 / mig 257 implications.

**Verdict:** **APPROVE-WITH-FIXES** — add the scope clarification line to the ledger header.

### Lens 3 — Security (Carol): **APPROVE-WITH-FIXES**

**Finding:** Ledger lives in `mcp-server/central-command/backend/migrations/RESERVED_MIGRATIONS.md`. Same directory as the SQL migration files. Edit permissions are the same as the SQL files — anyone who can land a migration can land a ledger row.

**Bad-actor inflate attack:** an attacker with write access could PR a ledger row claiming migs 320-999 ("reserved for vault rotation 2026-Q4") to block legitimate work. Mitigation:

- The ledger is git-tracked — every row is a reviewable diff with author + commit.
- The CI gate checks ROW EXISTS, not ROW IS BLESSED — but a 100-row PR would be obvious in review.
- **P1 finding:** add a soft cap (e.g., ledger.size <= 30 rows) enforced by the test, with a clear error message. Forces stale-reservation cleanup as a side-effect.

**Privilege concern:** the ledger does NOT carry signing material or chain-of-custody data. No Rule 3 implications. No PHI. Operator-internal coordination surface.

**Verdict:** **APPROVE-WITH-FIXES** — add ledger-row-count cap (≤ 30 unclaimed reservations) to the CI gate.

### Lens 4 — Coach: **BLOCK**

**Finding 1 — verdict-doc filter has a hole.** Design §3 carves out `coach-*-gate-{a,b}-*.md`. Empirical:

```
ls audit/coach-* | wc -l       → 98
ls audit/coach-*gate-[ab]* | wc -l → 81
17 docs ECHO design claims but DON'T match the filter
```

Unfiltered docs include:
- `coach-baa-contract-language-2nd-eye-2026-05-13.md` (echoes mig 224, 283, 309 from sibling designs)
- `coach-counsel-packet-review-2026-05-13.md` (echoes mig 261)
- `coach-15-commit-adversarial-audit-2026-05-09.md` (echoes mig 294)
- `coach-d1-heartbeat-timestamp-protocol-2026-05-13.md` (echoes mig 251, 281, 282, 313)
- `coach-enterprise-backlog-2026-05-12.md` (echoes mig 119, 126, 213)
- `coach-e2e-attestation-audit-2026-05-08.md`
- `coach-canonical-source-registry-gate-b-redo-2026-05-13.md` ("-redo" doesn't match gate-{a,b} suffix)

The `-redo` variant is the most insidious — it IS a gate-b verdict but the filename suffix differs. The filter shape is fragile.

**Required fix:** broaden filter to `coach-*.md` (all coach docs ECHO designs, none CLAIM migs). OR — better — adopt the claim-marker approach from Lens 1 (Shape A). With explicit `<!-- mig-claim: NNN -->` markers, the verdict-doc filter becomes UNNECESSARY: coach docs don't include claim markers because they don't claim, they reference. This is the cleaner design.

**Finding 2 — double-build check.** Searched `.agent/scripts/`, `claude-progress.json`, `docs/` for existing reservation registry. None exists. `MEMORY.md` + `SUBPROCESSORS.md` precedent is legitimate — both are pure-Markdown registries that CI gates can parse. The ledger is NOT double-build.

**Finding 3 — anti-pattern probe.** The design is itself a coordination surface. At enterprise scale, the question is whether N designs/week justify the ledger maintenance cost (every design now PRs both a doc AND a ledger row). At today's velocity (~5 designs/week), the maintenance cost is ~2 min/design + occasional cleanup ≈ 15 min/week. Today's collision cost is 3 Gate A cycles ≈ 15 min × 3 = 45 min/day. **Ledger pays for itself in <1 day at current velocity.** APPROVE on cost-benefit.

**Verdict:** **BLOCK** — adopt claim-marker approach (Shape A from Lens 1) which simultaneously fixes Lens 1 AND eliminates the verdict-doc filter entirely.

### Lens 5 — Auditor (OCR): **N/A** (confirmed)

Operator-internal CI gate. No customer-facing artifact. No auditor-kit interaction. No §164.528 disclosure-accounting touch. Confirmed N/A.

### Lens 6 — PM: **APPROVE-WITH-FIXES**

**Finding 1 — implementation cost.** Design implies 1-day work. Realistic:

- Ledger + 5 rows: 15 min
- CI gate (with revised regex per Lens 1): 1-2 hours
- Pre-push allowlist entry: 5 min
- CLAUDE.md rule entry: 10 min
- Companion renumber commits (3 design docs): 30 min each = 90 min

Total: ~4-5 hours of work. Half-day, not full day. Reasonable.

**Finding 2 — churn risk.** With CORRECT regex (claim-marker), zero false-positive churn. With AS-WRITTEN regex, every design-doc commit hits 5-20 false-positive failures. **The choice of regex shape is the difference between net-positive and net-negative ROI.**

**Finding 3 — stale-reservation cleanup (open question d).** Today's 5 reservations are dated 2026-05-13. If the gate ships without a cleanup mechanism, a year from now the ledger has 200+ rows and engineers ignore it (Rule 5 stale-doc-as-authority class). **Required Day 1:**

- Add `expected_ship_date` column to ledger rows.
- CI gate warns (not blocks) on rows older than 30 days without ship.
- Carry the row-count cap from Lens 3 (≤ 30 unclaimed).

**Finding 4 — phasing.** §9 ships single-phase. With renumbers as separate commits per §5.5. Ordering is wrong — see Cross-lens finding below.

**Verdict:** **APPROVE-WITH-FIXES** — add `expected_ship_date` column + row-count cap + fix ordering.

### Lens 7 — Attorney (in-house counsel): **APPROVE-WITH-FIXES**

**Counsel-rule check:**

- **Rule 1 (no non-canonical metric):** N/A — no customer-facing metric.
- **Rule 2 (no raw PHI):** N/A — no data flow.
- **Rule 3 (no privileged action w/o chain):** N/A — no privileged action.
- **Rule 4 (no orphan coverage):** Verified — no other migration number-space in the repo (Lens 2). The ledger covers 100% of the surface. If a future appliance-local migration space appears, this rule applies. Add the scope-clarification line from Lens 2.
- **Rule 5 (no stale doc as authority):** **THIS RULE APPLIES.** The ledger BECOMES authority. Without `expected_ship_date` + cleanup mechanism, in 6 months the ledger is itself stale. **Required fix:** Lens 6 stale-reservation mechanism is mandatory, not optional.
- **Rule 6 (no BAA state in human memory):** N/A.
- **Rule 7 (no unauthenticated context):** N/A — internal CI.

**Counsel-grade observation:** the design's §7 "no stale doc" claim ("the ledger IS the new authority") is correct in the abstract but DEFEATS itself without a cleanup mechanism. Counsel-rule compliance requires the stale-reservation mechanism land Day 1.

**Verdict:** **APPROVE-WITH-FIXES** — Rule 5 mandates Day 1 stale-reservation cleanup.

---

## Cross-lens findings

### P0 #1 — Regex over-capture (Lens 1, reinforced by Lens 4)

**Finding:** `\bmig(?:ration)?\s+\d{3}\b` matches 78 unique mig numbers across audit/, including ~50 references to shipped migrations. Day-1 push fails on every existing design + verdict doc.

**Fix:** Adopt explicit claim-marker shape:

```python
_MIG_CLAIM_RE = re.compile(r"<!--\s*mig-claim:\s*(\d{3})\s*-->")
```

Update design template to include `<!-- mig-claim: NNN -->` in §Schema. Eliminates false positives AND eliminates the verdict-doc filter (Lens 4 hole).

### P0 #2 — Verdict-doc filter has a hole (Lens 4)

**Finding:** 17 of 98 `coach-*` docs don't match `coach-*-gate-{a,b}-*.md` filter. Includes `-redo` variants, `-2nd-eye`, `-review`, `-roundtable`, `-audit-round2`.

**Fix:** Either broaden to `coach-*.md` OR adopt claim-marker approach (P0 #1) which makes the filter unnecessary.

### P0 #3 — Ordering trap (Lens 6 + PM)

**Finding:** §5 ships the gate in ONE commit. Day-1 push fails because 3 known-failing design docs haven't been renumbered yet.

**Fix:** Adopt this ordering:

1. **Commit 1:** Companion renumbers — update MTTR soak v2, load harness v2.1, P-F9 v2 to use the new ledger numbers (315, 316, 317/318). NO gate enabled yet.
2. **Commit 2:** Ledger creation (`RESERVED_MIGRATIONS.md` with 5 rows, scope-clarification header, `expected_ship_date` column).
3. **Commit 3:** CI gate (`tests/test_migration_number_collision.py`) with the claim-marker regex.
4. **Commit 4:** Pre-push allowlist + CLAUDE.md rule entry.

OR: ratchet-baseline approach — gate fires as warning-only on commit 1, hard-fails after a 7-day grace + cleanup. Worse, because warnings get ignored.

**Recommend Option 1 (4 commits in order).**

### P1 #1 — Stale-reservation cleanup Day 1 (Lens 6 + Lens 7, counsel Rule 5)

**Finding:** Without `expected_ship_date` + 30-day-warn + ≤30-row cap, the ledger is itself a future Rule 5 violation.

**Fix:** Add to ledger schema:

```markdown
| Number | Status | Claimed-by (design doc) | Claimed-at | Expected-ship | Task |
```

CI gate emits warning (not failure) on rows older than 30 days without ship. Hard cap at ≤30 unclaimed rows.

### P1 #2 — Scope-clarification line missing (Lens 2 + Lens 7, counsel Rule 4)

**Finding:** Ledger header doesn't document that appliance/agent migration spaces don't exist today and that this ledger is backend-only.

**Fix:** Add the one-liner from Lens 2 to the ledger header.

### P2 #1 — BLOCKED-status support (User question 3)

**Finding:** User asked whether ledger should support `BLOCKED` status (e.g., "mig 311 reserved by Vault P0 #43, BLOCKED on staging precondition").

**Recommendation:** YES — add as a fourth lifecycle state. Status enum: `reserved | in_progress | blocked | shipped`. The `blocked` status carries a `blocked_reason` field. The 30-day stale-warn skips `blocked` rows (they're knowingly parked). Vault P0 #43 mig 311 is the canonical first user.

---

## Regex-sufficiency adversarial probe — full grep report

```
grep -rEho '\bmig(ration)?\s+[0-9]{3}\b' audit/*.md | sort -u | head
mig 003 mig 011 mig 012 mig 013 mig 031 mig 078 mig 106 mig 109 mig 119 mig 126
...
(78 unique mig numbers total; 308 shipped on disk)
```

**False-positive shape categories observed:**

1. **Historical fact references:** "mig 146, 2026-03" — past tense, no claim.
2. **Architectural references:** "mig 138 partitioned `compliance_bundles`" — describing CURRENT system state.
3. **Cross-reference citations:** "per mig 257 site-rename allowlist" — citing prior infrastructure.
4. **Lockstep peer enumeration:** "the 4 lockstep peers (privileged-chain, ALLOWED_EVENTS, BAA-gated, mig 257 rename allowlist)" — listing existing patterns.
5. **Error-message string content:** `Exception("mig 256 missing")` inside a code-block test fixture quotation.

ALL 5 categories fire on the as-written regex. **The regex cannot disambiguate intent from token presence.** Only an explicit claim-marker resolves this.

---

## Multi-repo-scope decision

Confirmed via `find appliance/ agent/ -name "*.sql"` returning empty. Today: backend-only is the correct scope. Documented in the ledger header per Lens 2 fix. If `appliance/internal/sqlitemigrations/` or similar appears in the future (Go appliance local cache), this ledger must split — note it in CLAUDE.md rule entry.

---

## Ordering recommendation

**FOUR commits, in this order:**

1. **Companion renumbers** (tasks #58 / #61 / #62) — update mig refs in 3 design docs from {310, 311, 314, 315} → {315, 316, 317, 318}. Each design gets a `<!-- mig-claim: NNN -->` marker added to its §Schema. NO ledger, NO gate yet. **Reviewed independently per its own Gate A.**

2. **Ledger creation** — `RESERVED_MIGRATIONS.md` with 5 rows + scope-clarification + `expected_ship_date` column + BLOCKED-status support.

3. **CI gate** — `tests/test_migration_number_collision.py` with claim-marker regex (NOT the as-designed `\bmig\s+\d{3}\b`). Pre-push allowlist add.

4. **CLAUDE.md Rules entry + design-doc template update** — one-liner pointing at the ledger; template includes `<!-- mig-claim: NNN -->` placeholder in §Schema.

DO NOT ship as one commit per §9. The §5 enumeration is correct intent but §9 wraps them into one — the wrap is the bug.

---

## Final overall verdict

**BLOCK pending P0 #1, P0 #2, P0 #3 closure.**

The design's motivation is correct and the structural shape is sound. But the regex shape would create more churn than it saves (5-20 false positives per future design commit), the verdict-doc filter has a 17-doc hole, and the ordering ships 3 known-failing design docs behind the gate that blocks them.

**Closure path to APPROVE:**
1. Adopt `<!-- mig-claim: NNN -->` explicit-marker regex (closes P0 #1 + #2 simultaneously).
2. Add `expected_ship_date` column + ≤30-row cap + BLOCKED status to ledger (closes P1 #1 + P2 #1).
3. Add scope-clarification header line (closes P1 #2).
4. Split implementation into 4 commits in the order above (closes P0 #3).

Re-issue as design v2 with these changes; expect APPROVE on second-pass Gate A.

**Coach's 250-word summary above stands as the load-bearing verdict surface.**
