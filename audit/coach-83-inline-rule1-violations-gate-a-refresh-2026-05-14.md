# Class-B 7-lens Gate A REFRESH — Task #83 (inline Counsel-Rule-1 score violations)

**Date:** 2026-05-14
**Author/scope:** Fork-based adversarial review (Steve / Maya / Carol / Coach / OCR-Auditor / PM / Counsel)
**Supersedes:** `audit/coach-9-inline-rule-1-violations-enumeration-gate-a-2026-05-13.md` (the "11 violations" Gate A)
**Trigger:** Codebase moved since 2026-05-13 — commits #65a, #70, #75, #77, #87 shipped 2026-05-14. Line numbers in the prior Gate A's matrix have drifted; re-establish ground truth.

---

## 350-word summary

**APPROVE-WITH-FIXES — prior Gate A's count holds at 11, but TWO of its line numbers are stale AND it under-reported the single most important finding: the F3 divergence is not a windowing gap, it is an ALGORITHM gap.**

Independent re-grep of `mcp-server/central-command/backend/` confirms **11 inline compliance-score computations** (+ 1 stored-value read in `portal.py`, separate class). Line drift since 2026-05-13: `routes.py:7867 → 7885`, `routes.py:8678 → 8712`. All other lines stable. No new violations introduced by today's commits; no violations closed.

**THE headline finding (Auditor + Counsel P0):** there are **three different compliance-score algorithms** live in production right now:
- **Canonical** (`compute_compliance_score`): `passed / total * 100` — warnings sit in the denominator only.
- **F3 quarterly PDF** (`client_quarterly_summary.py:345`) **and admin compliance-packet** (`routes.py:8712`): `(passed + 0.5·warnings) / total * 100` — warnings count as **half a pass**.
- The other 9 callsites use the canonical *math* inline (`passed/total*100`), just not the canonical *function*.

For any site with even one `warning` check, F3's PDF and the admin packet show a **structurally higher** number than the customer's dashboard. A customer holding their Q1 PDF next to their portal sees two different compliance percentages — a direct Counsel Rule 1 violation with customer-trust + auditor-artifact impact. This is P0-1, above all windowing concerns.

**Maya:** helper signature unchanged since 2026-05-13 — still `window_days`-only. The 4 fixed-window callsites (F3, both packets, monthly-report) still need `window_start`/`window_end` kwargs. Spec below.

**Coach:** the gate (`test_canonical_metrics_registry.py`) is **registry-count-based, not AST-based** — it counts allowlist entries, never greps source. The 11 violations are entirely outside its scan scope. Gate-scope-widening is mandatory #83 scope, not optional.

**Carol:** N/A confirmed — read paths, no auth/PHI surface. One cross-reference note to Counsel.

**Verdict: APPROVE the 3-phase plan with a revised Phase B1 (F3) elevated to algorithm-reconciliation, and a mandatory Phase A.5 = widen the CI gate to AST-scan.**

---

## Per-lens verdicts

### Steve (Engineering) — APPROVE, re-enumeration with corrected line numbers

**Re-grep of `mcp-server/central-command/backend/` for inline compliance-score arithmetic NOT routed through `compute_compliance_score()`:**

Grep patterns used: `passed.*/.*max(.*total`, `/ NULLIF(COUNT(*) FILTER...* 100`, `(passes + 0.5 * warnings) / denom`, `compliance_score = round`.

| # | File:Line (TODAY) | Prior GA line | Formula | Window | Customer-facing? | Algorithm |
|---|---|---|---|---|---|---|
| 1 | `routes.py:3203` | 3203 ✓ | `(cn.passed or 0)/max(cn.total or 1,1)*100` | 24h relative | NO — operator (`require_auth`, /admin/stats-deltas) | canonical math |
| 2 | `routes.py:3216` | 3216 ✓ | `(cp.passed or 0)/max(cp.total or 1,1)*100` | 24h ending 7d ago | NO — operator | canonical math |
| 3 | `routes.py:3337` | 3337 ✓ | SQL `COUNT(*) FILTER(pass)/NULLIF(COUNT(*) FILTER(pass,fail,warn))*100` | 24h relative | NO — operator (/fleet-posture) | canonical math |
| 4 | `routes.py:3729` | 3729 ✓ | `(comp_row.passed or 0)/max(comp_row.total or 1,1)*100` | 24h relative | NO — operator (ClientStats /stats/{id}) | canonical math |
| 5 | `routes.py:4875` | 4875 ✓ | `round(compliance_score,1)` — value from `get_all_compliance_scores(db)` | 24h relative | NO — operator (/organizations/{id}) | via allowlisted helper |
| 6 | `routes.py:5732` | 5732 ✓ | `(go_agent_checks_passed/go_agent_checks_total)*100` | go-agent live | NO — operator (admin compliance-health) | canonical math, go-agent subscore |
| 7 | `routes.py:5767` | 5767 ✓ | `(r["passed"]/r["total"])*100` | 30d trend | NO — operator | canonical math |
| 8 | `routes.py:7885` | **7867 ✗ (drifted +18)** | SQL `COUNT(*) FILTER(pass)/NULLIF(COUNT(*) FILTER(pass,fail,warn))*100` | fixed-month | NO — operator BUT feeds JSON evidence packet (auditor artifact) | canonical math |
| 9 | `routes.py:8712` | **8678 ✗ (drifted +34)** | `(passes + 0.5*warnings)/denom*100` | 30d relative | NO — operator BUT feeds /admin/sites/{id}/compliance-packet | **HALF-PASS WARNINGS — diverges from canonical** |
| 10 | `org_management.py:1118` | 1118 ✓ | SQL `.../NULLIF(COUNT(*) FILTER(...))*100` | fixed-month | NO — operator BUT feeds /api/orgs/{id}/compliance-packet | canonical math |
| 11 | `client_quarterly_summary.py:345` | 345 ✓ | `(passed + 0.5*warnings)*100.0/denom` | fixed-quarter | **YES — F3 PDF, customer + auditor** | **HALF-PASS WARNINGS — diverges from canonical** |
| (12) | `portal.py:1308` | 1308 ✓ | reads `compliance_packets.compliance_score` column | stored | YES (read-from-stored) | inherits writer's algorithm — DIFFERENT CLASS |

**Steve P0 — line drift:** prior Gate A's matrix is stale on #8 and #9. Anyone implementing against the 2026-05-13 doc would have edited the wrong lines. THIS doc's line numbers are authoritative as of 2026-05-14 (verified with `sed -n` against each file).

**Steve P0 — the divergence is algorithmic, not windowing.** The prior Gate A framed every difference as a *window* problem solvable by `window_start`/`window_end`. It is not. **#9 and #11 use a fundamentally different scoring rule** — `(passes + 0.5·warnings)/total` vs canonical `passed/total`. Even after the helper gets fixed-window kwargs, calling it from F3 will produce a *different number than F3 prints today*. The migration must explicitly decide: does the canonical number adopt half-pass-warnings, or do F3 + the admin packet drop it? (See Auditor lens — recommendation: canonical stays `passed/total`; F3/packet migrate and the score MOVES, with a documented changelog note.)

**Steve P0 — F1 proves migration is feasible.** `client_attestation_letter.py:209` already calls `compute_compliance_score(...)` and serializes its `ComplianceScore` dataclass. F1 is the working reference implementation for the F3 migration — same PDF-artifact class, same determinism contract, already canonical.

**Steve P1 — `get_all_compliance_scores` is a helper-level multiplier.** #5 isn't a standalone callsite — `routes.py:4875` consumes `db_queries.get_all_compliance_scores(db)`, itself on the `migrate` allowlist. Migrating that one helper closes #5 plus its sibling callers in one shot. Don't count it as 1 LOC of work.

**Steve P1 — count reconciliation (9 vs 11 vs 11):** Task #83's description says "9" (inherited from the Task #67 Gate A). The 2026-05-13 refresh found 11. This refresh **confirms 11** — no change. Task #83's description should be corrected to 11; cite this doc as the authoritative matrix.

---

### Maya (Database) — APPROVE with helper-enhancement prerequisite (UNCHANGED from 2026-05-13, re-verified)

**Current signature (`compliance_score.py:157`, re-read 2026-05-14):**
```python
async def compute_compliance_score(
    conn, site_ids: List[str], *,
    include_incidents: bool = False,
    window_days: Optional[int] = DEFAULT_WINDOW_DAYS,   # =30
    _skip_cache: bool = False,
) -> ComplianceScore:
```
`window_days=N` → `WHERE cb.checked_at > NOW() - ($2::int * INTERVAL '1 day')` (lines 274). `window_days=None` → all-time, cache-bypassed. **No fixed-date support.** Confirmed: the `_skip_cache` kwarg (Task #64) is the only addition since the original helper — no windowing work landed.

**Maya P0 — required enhancement (additive, backward-compatible):**
```python
async def compute_compliance_score(
    conn, site_ids, *,
    include_incidents=False,
    window_days=DEFAULT_WINDOW_DAYS,
    window_start: Optional[datetime] = None,   # NEW
    window_end: Optional[datetime] = None,     # NEW
    _skip_cache=False,
) -> ComplianceScore:
```
**Semantics:**
- If neither `window_start` nor `window_end` set → current behavior (relative `window_days`).
- If `window_start` set, `window_end` None → `[window_start, NOW()]`.
- If `window_end` set, `window_start` None → `[window_end - window_days, window_end]` (covers #2's "24h ending 7d ago" — pass `window_end=NOW()-7d`, `window_days=1`).
- If both set → `[window_start, window_end]` half-open, matching F3's existing `checked_at >= $2 AND checked_at < $3` shape (`client_quarterly_summary.py:367-368`).
- `window_days` is ignored when either bound is explicit (except as the implicit span when only `window_end` is given).
- SQL: parameterize both bounds; reuse the existing `unnested`/`latest` DISTINCT-ON CTE — F3's inline query is *already byte-identical to the helper's CTE* except for the WHERE clause. The migration is mechanical once the kwargs exist.

**Maya P0 — cache-key MUST include the resolved bounds.** `_score_cache_key()` currently keys on `(site_ids, include_incidents, window_days)`. Fixed-window calls with the same `window_days` but different `(start, end)` would collide. Add the resolved `(window_start, window_end)` tuple to the key. Fixed-window results are deterministic on a closed past range — safe to cache with a LONGER TTL (1h+) than the 60s relative-window TTL. Suggest a TTL ladder keyed on "is the window closed in the past."

**Maya P1 — RLS unchanged.** All 11 callsites already run under `admin_transaction` or `org_connection`. The helper is RLS-aware (caller sets `app.current_org`/`app.current_tenant`). Migration is a straight `conn` pass-through — no RLS surprises. Confirmed `compliance_bundles` has both site-scoped and org-scoped policies (mig 278).

**Maya APPROVE.** Helper enhancement is ~30-40 LOC + ~4 unit tests (relative-unchanged, start-only, end-only, both-bounds). Small, additive, no migration.

---

### Carol (Security) — N/A confirmed

Per brief, Carol is N/A — all 11 are **read paths**, no auth-decorator change, no PHI-boundary change, no new endpoint. Re-verified: every callsite is already behind `require_auth` (operator) or org/tenant RLS context (customer-facing F3 + portal). The migration swaps an inline SQL aggregation for a helper call — it does not touch the request's auth posture.

**Carol cross-reference to Counsel:** the F3/packet algorithm divergence is a Rule 1 + Rule 10 joint finding. When a customer's auditor sees the PDF score (94, half-pass-warnings) and the dashboard score (91, passed/total) for the same site/quarter, that's not just "non-canonical metric" — it undermines the determinism-and-provenance posture (Rule 9) the whole evidence chain is built on. Worth flagging in the Counsel-queue bundle (Task #37) as a secondary harm beyond the base Rule 1.

---

### Coach (Process / Gate scope) — APPROVE with MANDATORY gate-widening as #83 scope

**Coach P0 — the gate does NOT catch these 11. It cannot. It never scans source.**

`test_canonical_metrics_registry.py` enforces three things, none of which is an AST/source grep:
1. **Registry integrity** — every `allowlist` `signature` resolves to a real symbol (catches dead-removal drift).
2. **Frozen-baseline ratchet** — counts `migrate`-class **allowlist entries** (`_count_migrate_class_allowlist_entries()`), pins at `BASELINE_MAX = 26`. This counts *dict entries in `canonical_metrics.py`*, NOT callsites in `routes.py`.
3. **PLANNED_METRICS no-customer-surface** — an explicit **stub** ("Phase 0+1 scope: this test is a stub").

So the gate's "11 violations" are **invisible to it**. The gate would stay green if all 11 inline formulas were deleted *or* if a 12th were added — because the inline callsites were never enumerated as allowlist entries in the first place. The `routes.py` / `org_management.py` / `client_quarterly_summary.py` formulas are not in the allowlist at all; only 6 *helper functions* are (`metrics.calculate_compliance_score`, `compliance_packet.ComplianceReport._calculate_compliance_score`, `db_queries.get_compliance_scores_for_site`, `db_queries.get_all_compliance_scores`, `frameworks.get_compliance_scores`, `frameworks.get_appliance_compliance_scores`).

**Coach P0 — gate-scope-widening is non-negotiable #83 scope.** A drive-down that removes the 11 inline formulas without an AST gate to *prevent the 12th* is a ratchet with no pawl. Required addition to the gate (call it `test_no_inline_compliance_score_formula` or extend the registry test):
- AST-walk every `.py` under `backend/` (excluding `compliance_score.py`, `tests/`, and the allowlisted helper modules).
- Flag: any `BinOp` of shape `... / ... * 100` or `... * 100 / ...` where a sibling identifier matches `/passed|passes|compliant/` AND `/total|denom/`; PLUS any raw-SQL string literal containing `FILTER (WHERE` ... `'pass'` ... `* 100`.
- Allow per-line `# canonical-migration: compliance_score — <reason>` markers (mechanism already designed in the registry docstring) for callsites mid-migration.
- Frozen baseline = 11 today; each Phase B/C PR decrements it. This is the *real* ratchet — entry-count is a proxy that doesn't track source.

**Coach P1 — phasing endorsement (with one revision).** The 2026-05-13 3-phase shape (A: helper enhancement → B: 4 customer/auditor-PDF callsites → C: 7 operator-internal) is sound. **Revision:** insert **Phase A.5 = gate-widening**, landed *with or immediately after* Phase A, *before* any Phase B/C migration. Migrating callsites first and widening the gate last leaves a window where a regression lands ungated.

**Coach P1 — Gate B for every phase MUST run the full pre-push sweep** (`bash .githooks/full-test-sweep.sh` or the `SOURCE_LEVEL_TESTS` array), not just diff review — Session 220 lock-in. The F3 migration in particular: Gate B must `curl` the F3 issuance endpoint AND diff the resulting `mean_score_str` against a canonical recompute, AND verify the PDF byte-determinism contract still holds across two downloads of the same quarter.

**Coach P1 — `portal.py:1308` stays out of this batch.** It's a stored-value read; the fix is at the *writer* of `compliance_packets.compliance_score`. Track as a Task #50 child. But NOTE: the writer almost certainly is one of #8/#9/#10 (the packet generators) — so fixing the writers in Phase B *automatically* fixes what `portal.py:1308` reads going forward. Old rows stay wrong until backfilled; flag a backfill sub-task.

---

### OCR / Auditor — F3 divergence VERIFIED + quantified — P0-1

**The brief's probe — "does F3 actually diverge?" — answer: YES, and worse than the prior Gate A characterized it.**

The prior Gate A treated F3 as a *windowing* problem ("compute_compliance_score uses NOW()-window_days, which can't take a fixed past quarter" — quoting F3's own source comment). That comment is **half the story**. F3 also uses a **different scoring algorithm**:

| Surface | File:Line | Formula | A `warning` check is worth... |
|---|---|---|---|
| Canonical helper | `compliance_score.py:386` | `total_passed / total * 100` | **0** (in denominator only) |
| Customer dashboard, Reports, per-site health | (all delegate to helper) | canonical | 0 |
| **F3 Quarterly PDF** | `client_quarterly_summary.py:392` | `(passed + 0.5·warnings) · 100 / denom` | **0.5 of a pass** |
| **Admin compliance-packet** | `routes.py:8712` | `(passes + 0.5·warnings) / denom · 100` | **0.5 of a pass** |
| Monthly-report, fleet-posture, org-packet, stats-deltas | `routes.py:7885,3337,3203,3216,3729`, `org_management.py:1118` | `passed / (pass+fail+warn) · 100` | 0 (canonical math, wrong function) |

**Quantified risk:** for a site with `P` passes, `F` fails, `W` warnings, the gap between F3's number and the dashboard's number is:
`F3 − dashboard = (0.5·W / (P+F+W)) · 100` percentage points.
A site with 80 pass / 5 fail / 15 warn (100 checks): dashboard = **80.0%**, F3 = **(80 + 7.5)/100·100 = 87.5%**. **A 7.5-point gap on the same site, same quarter.** The customer files the 87.5% PDF in their §164.530(j) retention archive; their portal shows 80.0%; their auditor pulls both. That is a Counsel Rule 1 violation with the worst possible blast radius — a *frozen, signed, customer-retained artifact* that disagrees with the live system.

**Auditor recommendation on the reconciliation decision:** **canonical stays `passed/total`** (warnings are not partial credit — a warning is an unresolved finding, not a half-fixed one; the canonical helper's docstring and the whole "compliance is a state" framing support this). F3 + the admin packet **migrate to canonical and their printed score MOVES** (downward, for any site with warnings). This is correct but customer-visible: the Phase B1 commit MUST ship a changelog/release note ("Quarterly Summary scoring aligned to the platform-wide canonical method; warnings no longer count as partial credit — your Q-over-Q numbers may shift") so a customer doesn't think their compliance dropped. Maya: confirm no F3 rows are mid-quarter such that a re-issue would surprise anyone — F3 only issues *past* quarters, so historical PDFs are immutable and stay as-issued; only *future* issuances change. Good.

**Auditor P1 — `test_unified_compliance_score.py` + `test_f1_pdf_score_extraction.py` exist** and pin the canonical helper + F1's use of it. There is **no equivalent test for F3** — `test_f1_pdf_score_extraction.py` should get an F3 sibling once F3 is migrated, asserting the PDF's `mean_score_str` equals a canonical recompute. Add to Phase B1.

---

### PM — Effort + phasing

| Phase | Scope | Gate As | Impl | Total |
|---|---|---|---|---|
| **A** — helper `window_start`/`window_end` kwargs + cache-key + 4 tests | 1 (helper) | 1 | ~3h | ~5h w/ Gate B |
| **A.5** — widen CI gate to AST-scan (NEW — Coach P0) | gate test | folded into A's Gate A | ~3h | ~4h |
| **B1** — F3 quarterly PDF (algorithm reconciliation + changelog note + F3 score test) | 1 | 1 | ~3h + PDF-determinism verify | ~5h |
| **B2** — `routes.py:7885` + `8712` (monthly-report + admin packet; 8712 also reconciles algorithm) | 2 | 1 (joint) | ~3h | ~5h |
| **B3** — `org_management.py:1118` (org packet, cross-site, fixed-month) | 1 | 1 | ~2h | ~4h |
| **C** — operator batch: `routes.py:3203,3216,3337,3729,5732,5767` + `get_all_compliance_scores` helper migration | 6 + helper | 1 | ~4h | ~6h |
| **Total** | **11 callsites + 1 gate + 1 helper** | **5** | — | **~29h** |

**PM notes:**
- ~5h heavier than the 2026-05-13 estimate (~24h) — the delta is Phase A.5 (gate-widening, ~4h) which the prior Gate A didn't scope, plus B1/B2 grew slightly for the algorithm-reconciliation changelog + F3 score test.
- **Phasing shape is still right** — A → A.5 → (B1 ∥ B2 ∥ B3) → C. The one structural change: A.5 is a hard gate before any B/C work (Coach P0). B-phases can run as 3 parallel forks once A+A.5 land; C is last (it's the lowest-risk mechanical batch and unblocks the Task #67 sampler integration on those 6 operator paths).
- **Budget: ~1 week**, same as before. Phase A+A.5 day 1-2, B-phases parallel day 3-4, C day 5.
- Task #83 description correction: "9" → "11 inline compliance-score computations + 1 stored-value read (separate workstream)". Cite this doc.

---

### Counsel (Rule 1 — gold authority) — APPROVE 3-phase plan, severity ranking REVISED

Counsel Rule 1: *"No non-canonical metric leaves the building. Every customer-facing metric declares a canonical source."* Ranking by customer-facing blast radius:

| Priority | Callsite(s) | Why |
|---|---|---|
| **P0-1** | `client_quarterly_summary.py:345` (F3 PDF) | **Customer-facing AND algorithm-divergent.** A frozen, signed, customer-retained PDF that prints a *structurally higher* number than the customer's own dashboard. Worst blast radius — the artifact outlives the session, goes in a retention archive, gets handed to an auditor. Hard block. |
| **P0-2** | `routes.py:8712` (admin compliance-packet) | **Auditor-grade JSON evidence packet AND algorithm-divergent.** Same half-pass-warnings divergence as F3. Auditor sees this number. |
| **P0-3** | `routes.py:7885` + `org_management.py:1118` (monthly-report + org packet) | Auditor-grade artifacts. Canonical *math* but not canonical *function* — so they'd diverge if the canonical algorithm ever changes (and B1/B2 are about to change F3/8712's algorithm — these two would then be the *only* canonical-math holdouts, an inconsistency in its own right). |
| **P1** | `portal.py:1308` | Customer-facing but read-from-stored. Real fix is the writer (#8/#9/#10). Lower urgency only because it inherits whatever the writer produces. |
| **P2** | `routes.py:3203,3216,3337,3729,4875,5732,5767` | Operator-internal. Rule 1 violations *in spirit* (and they pollute the substrate-invariant signal if sampled) but no customer/auditor surface. |

**Counsel directive — the F3/packet algorithm reconciliation is itself a Rule-1-adjacent decision and needs explicit sign-off in the B1/B2 Gate A.** Don't let the implementer silently pick "make canonical adopt half-pass-warnings" because it avoids moving F3's number — that would be choosing the *convenient* canonical, not the *correct* one. The Auditor lens's recommendation (canonical stays `passed/total`; F3 moves; ship a changelog note) is the Counsel-endorsed path.

**Counsel — surface in Task #37 (Counsel-queue bundle):** "Until Phase B closes, the Quarterly Summary PDF and the admin compliance-packet print a compliance score computed by a different algorithm than the customer's dashboard — a Rule 1 + Rule 9 (determinism/provenance) finding affecting auditor-artifact integrity."

**Counsel — after each phase, decrement the gate's frozen baseline in lockstep** (Coach's AST-gate baseline of 11). Rule 1's whole point is the ratchet can only go down.

---

## Decision summary — what changed vs the 2026-05-13 Gate A

| Item | 2026-05-13 Gate A | This refresh (2026-05-14) |
|---|---|---|
| Violation count | 11 (+1 stored) | **11 (+1 stored) — UNCHANGED** |
| `routes.py:7867` | cited | **drifted → `7885`** |
| `routes.py:8678` | cited | **drifted → `8712`** |
| F3 framing | "windowing gap" | **"ALGORITHM gap" — half-pass-warnings, quantified 7.5pt example** |
| `routes.py:8712` algorithm | not flagged | **also half-pass-warnings — joins F3 as algorithm-divergent** |
| Gate scope | not analyzed | **gate is registry-count-based, scans NO source — 11 violations invisible to it** |
| Phasing | A → B → C (4 phases) | **A → A.5 (gate-widen, NEW) → B → C (5 phases)** |
| Effort | ~24h | **~29h** (+A.5) |
| Helper signature | needs `window_start`/`window_end` | **same — re-verified, no windowing work landed since** |

---

## `compute_compliance_score()` enhancement spec (Phase A — build-ready)

```python
async def compute_compliance_score(
    conn,
    site_ids: List[str],
    *,
    include_incidents: bool = False,
    window_days: Optional[int] = DEFAULT_WINDOW_DAYS,
    window_start: Optional[datetime] = None,   # NEW — inclusive lower bound
    window_end: Optional[datetime] = None,     # NEW — exclusive upper bound
    _skip_cache: bool = False,
) -> ComplianceScore:
```

**Resolution logic (add near top, before the `if not site_ids` guard):**
- `window_start is None and window_end is None` → existing relative path (`window_days`).
- `window_start is not None and window_end is None` → `[window_start, NOW())`.
- `window_start is None and window_end is not None` → `[window_end - window_days·1day, window_end)`.
- both not None → `[window_start, window_end)` half-open.
- when either bound is explicit, `window_days` only contributes as the implicit span for the `window_end`-only case.

**SQL:** add a third branch alongside the existing `window_days is None` / `else` branches — `WHERE cb.site_id = ANY($1) AND cb.checked_at >= $2 AND cb.checked_at < $3`. The `unnested`/`latest` DISTINCT-ON CTE is byte-identical to F3's existing inline query (`client_quarterly_summary.py:359-380`) — copy it.

**Cache:** extend `_score_cache_key()` to `(... , window_start, window_end)`. `_should_cache_score()` → cache fixed-window calls too (they're deterministic on a closed past range); consider a longer TTL for windows where `window_end < NOW() - 1day`.

**`window_description`:** add a branch — `f"Latest result per (...), {window_start:%Y-%m-%d} to {window_end:%Y-%m-%d}"`.

**Algorithm:** **DO NOT** add half-pass-warnings to the helper. The canonical algorithm stays `total_passed / total * 100`. F3 + `routes.py:8712` migrate *to* this and their printed numbers move — that is the correct outcome (Auditor + Counsel concur).

**Tests (Phase A):** 4 new in `test_unified_compliance_score.py` — relative-unchanged regression, `window_start`-only, `window_end`-only, both-bounds; assert each resolves to the expected SQL bounds and the cache key differs.

---

## Gate-scope-widening recommendation (Phase A.5 — Coach P0, MANDATORY #83 scope)

The current gate (`test_canonical_metrics_registry.py`) counts **allowlist dict entries**, never source. Add an AST/source gate — `test_no_inline_compliance_score_formula.py` (or extend the registry test):

1. **AST-walk** every `backend/**/*.py` except `compliance_score.py`, `tests/`, and the 6 allowlisted helper modules.
2. **Flag** `BinOp` of shape `_ / _ * 100` (or `_ * 100 / _`) where sibling identifiers match `/(passed|passes|compliant)/` and `/(total|denom)/`; **and** raw-SQL string literals containing `FILTER (WHERE` + `'pass'` + `* 100`.
3. **Allow** per-line `# canonical-migration: compliance_score — <reason>` markers (mechanism already designed in the registry docstring).
4. **Frozen baseline = 11** today; each Phase B/C PR decrements it in lockstep. This is the real pawl — the entry-count proxy doesn't track source and let all 11 through.

Land A.5 *with* Phase A's Gate A, *before* any B/C migration — so a regression can't slip in during the multi-week drive-down.

---

## Phasing (build-ready)

1. **Phase A** — helper `window_start`/`window_end` kwargs + cache-key + 4 tests. Gate A (this doc covers it) + Gate B (full sweep, no regression on 30d/90d/None paths).
2. **Phase A.5** — widen CI gate to AST-scan, frozen baseline 11. Folded into Phase A's Gate A; ships with or right after A.
3. **Phase B1** (parallel) — F3 `client_quarterly_summary.py:345` → canonical helper w/ `window_start`/`window_end`. **Algorithm reconciliation** (drop half-pass-warnings) + customer changelog note + F3-score test (sibling to `test_f1_pdf_score_extraction.py`). Gate A: customer-facing + PDF determinism + F3 contract + the reconciliation decision. Gate B: full sweep + curl F3 + diff `mean_score_str` vs canonical + 2-download byte-identity.
4. **Phase B2** (parallel) — `routes.py:7885` + `8712`. 8712 also reconciles algorithm (drop half-pass-warnings). Gate A: auditor-JSON-packet contract + monthly/30d windows. Gate B: full sweep + curl both + diff JSON `compliance_score` vs canonical recompute.
5. **Phase B3** (parallel) — `org_management.py:1118` cross-site org packet. Gate A: cross-site aggregation + per-site breakdown preservation. Gate B: full sweep + curl + auditor-feed verify.
6. **Phase C** (last) — `routes.py:3203,3216,3337,3729,5732,5767` + `db_queries.get_all_compliance_scores` helper migration (closes #5 + siblings). Mechanical batch, 1 commit. Gate A: mechanical-batch + substrate-invariant signal expectation. Gate B: full sweep + decrement gate baseline to 0 + unblock Task #67 sampler integration on the 6 newly-canonical operator paths.
7. **Followup (not in #83):** `portal.py:1308` — Task #50 child; fixed structurally once B-phase packet writers are canonical, but old `compliance_packets` rows need a backfill sub-task.

---

## Top P0/P1

- **P0-1 (Auditor + Counsel):** F3 (`client_quarterly_summary.py:345`) and the admin packet (`routes.py:8712`) use a **different scoring algorithm** (half-pass-warnings) than the canonical helper — not just a different window. A customer's signed quarterly PDF prints a structurally higher number than their own dashboard (7.5-point gap on a realistic 80/5/15 site). Hard Rule 1 block. The migration MUST reconcile the algorithm (canonical stays `passed/total`; F3+packet move) and ship a customer changelog note.
- **P0-2 (Steve):** Prior Gate A's line numbers for #8 and #9 are stale (`7867→7885`, `8678→8712`). THIS doc's matrix is authoritative as of 2026-05-14.
- **P0-3 (Coach):** The CI gate is registry-count-based and scans NO source — all 11 violations are invisible to it. **Gate-widening (Phase A.5, AST-scan, frozen baseline 11) is mandatory #83 scope**, must land before any B/C migration.
- **P0-4 (Maya):** Helper still `window_days`-only — no windowing work landed since 2026-05-13. `window_start`/`window_end` kwargs (spec above) are the prerequisite for the 4 fixed/anchored-window callsites.
- **P1-1 (Coach):** Sequence A → A.5 → B∥ → C. `routes.py:3216` (split-window) needs the `window_end` kwarg from Phase A — don't migrate it before A lands.
- **P1-2 (Auditor):** Add an F3-score CI test (sibling to `test_f1_pdf_score_extraction.py`) in Phase B1.
- **P1-3 (Steve):** `get_all_compliance_scores` migration closes `routes.py:4875` + sibling callers in one shot — Phase C, count as one helper-level item.
- **P1-4 (Counsel):** Surface the F3/packet algorithm divergence in the Counsel-queue bundle (Task #37) as a Rule 1 + Rule 9 finding.

---

## Final overall verdict

**APPROVE-WITH-FIXES.**

The 2026-05-13 Gate A's count of **11 inline compliance-score computations (+1 stored-value read)** holds — re-verified by independent re-grep. **Two line numbers have drifted** (`routes.py:7867→7885`, `8678→8712`) and must be corrected. No commits today (#65a/#70/#75/#77/#87) added or closed a violation.

The **material new finding** is that the F3 quarterly PDF and the admin compliance-packet do not merely use a different *window* — they use a different *scoring algorithm* (warnings as half-pass), producing a structurally higher number than the customer's dashboard. This is a Counsel Rule 1 P0 with frozen-artifact blast radius. The migration must explicitly reconcile the algorithm; the canonical method (`passed/total`) is correct and F3+packet move toward it, with a customer changelog note.

Two fixes the prior Gate A missed and that gate this APPROVE:
1. **Phase A.5 — widen the CI gate to AST-scan source.** The current gate counts registry entries and never greps source; the 11 violations are invisible to it. Without an AST gate, the drive-down is a ratchet with no pawl.
2. **B1/B2 Gate As must explicitly own the algorithm-reconciliation decision** — not silently pick the convenient canonical.

**Proceed with the 5-phase plan: A → A.5 → (B1 ∥ B2 ∥ B3) → C. ~29h, ~1 week, 5 Gate As.** Phase A+A.5 unblock everything; B-phases parallel; C last and unblocks the Task #67 sampler integration on the 6 operator paths. Task #83's description should be corrected from "9" to "11" with this doc cited as the authoritative per-callsite matrix.
