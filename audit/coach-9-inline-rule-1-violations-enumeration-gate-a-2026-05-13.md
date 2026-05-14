# Class-B 7-lens Gate A — Task #83 (9 inline Rule-1 violations enumeration)

**Date:** 2026-05-13
**Author/scope:** Fork-based adversarial review (Steve / Maya / Carol / Coach / OCR / PM / Counsel)
**Subject:** Empirically verify Task #67 Gate A's claim of "9 inline Rule-1 violations" + propose drive-down plan with per-callsite migration cost, windowing semantics, batching strategy.
**Pre-state:** Task #67 Gate A (`audit/coach-phase-2b-rollout-gate-a-2026-05-13.md`) enumerated 6 routes.py + 1 org_management + others. This Gate A re-greps source independently, treats the prior enumeration as a hypothesis, not ground truth.

---

## 300-word summary

**APPROVE-WITH-FIXES — but the prior enumeration UNDERCOUNTED.** Independent source-walk of `mcp-server/central-command/backend/` finds **11 inline Rule-1 violations**, not 9. The two missed callsites are `routes.py:3203` (`compliance_now` for fleet stats-deltas, 24h window) and `routes.py:3216` (`compliance_prev` for 7-day-ago comparison). Both compute `passed/total*100` directly against `compliance_bundles` cross `jsonb_array_elements(checks)` — identical shape to the 9 the fork already named.

**Verified-real violations (11 total):**
| File | Line | Endpoint | Window | Customer-facing? |
|---|---|---|---|---|
| routes.py | 3203 | /admin/stats-deltas (compliance_now) | 24h | operator |
| routes.py | 3216 | /admin/stats-deltas (compliance_prev) | 24h ending 7d ago | operator |
| routes.py | 3337 | /fleet-posture (site_compliance CTE) | 24h | operator |
| routes.py | 3729 | /stats/{site_id} ClientStats | 24h | operator (admin client view) |
| routes.py | 4875 | /organizations/{org_id} via get_all_compliance_scores | 24h | operator |
| routes.py | 5732/5767 | /sites/{site_id}/compliance-health (admin) | 30d trend + go-agent live | operator |
| routes.py | 7867 | /admin monthly-report compliance_score | fixed-month | operator + auditor-PDF input |
| routes.py | 8678 | /admin/sites/{site_id}/compliance-packet | 30d | operator + auditor-PDF input |
| org_management.py | 1118 | /api/orgs/{org_id}/compliance-packet | fixed-month | operator + auditor-PDF input |
| client_quarterly_summary.py | 345 | quarterly summary | fixed-quarter | customer-facing (F3 PDF) |
| portal.py | 1308 | /api/portal/site/{site_id}/home | stored read | customer-facing (read-from-table) |

**Key Maya finding:** Helper `compute_compliance_score(conn, site_ids, *, include_incidents, window_days)` supports a relative lookback only — `window_days=N` translates to `NOW() - INTERVAL '{N} days'`. **4 callsites need a fixed-window param** (monthly-report 7867, both compliance-packets 8678 + org_management:1118, quarterly summary). Helper enhancement is prerequisite, not optional.

**Counsel-OCR:** 4 of 11 are customer-facing or feed auditor-grade PDF artifacts (3 compliance-packets + quarterly summary). Per Counsel Rule 1, these are P0. The remaining 7 are operator-internal but still violate Rule 1 in spirit (substrate-invariant signal pollution if sampled).

**Final verdict: APPROVE batch plan in 3 phases.** Phase A = canonical-helper enhancement (fixed-window param). Phase B = 4 customer-facing/PDF callsites (per-callsite Gate A — windowing semantics differ enough). Phase C = 7 operator-internal (mechanical batch).

---

## Per-lens verdicts

### Steve (Engineering) — APPROVE batch plan, REVISE enumeration

**Empirical verification of prior fork's enumeration:**

| Prior fork's claim | Empirical reality | Verdict |
|---|---|---|
| routes.py:3398 /fleet-posture inline | TRUE — line 3337 CTE `passed/total*100`, line 3398 is the SELECT. 24h window. operator (`require_auth`). | **REAL Rule-1 violation** |
| routes.py:4875 /organizations/{org_id} | TRUE — uses `get_all_compliance_scores(db)`, which is itself on the allowlist (`migrate` class). 24h window. operator. | **REAL Rule-1 violation** — downstream of helper-level allowlist entry |
| routes.py:5774 bespoke compliance-health | TRUE — `/sites/{site_id}/compliance-health` admin endpoint computes per-category breakdown lines 5710-5745 + go-agent score lines 5732 + trend at 5767. 30d window + 24h go-agent. operator. | **REAL Rule-1 violation** (multiple sub-aggregations in one endpoint) |
| routes.py:7851 admin monthly-report | TRUE — line 7867 inline SQL `passed/total*100`. **Fixed-month window** (`created_at >= $2::date AND < $2::date + interval '1 month'`). operator + feeds JSON evidence packet (auditor-grade artifact). | **REAL Rule-1 violation — P0** (auditor-PDF input) |
| org_management.py:1118 /compliance-packet | TRUE — line 1110-1118 inline SQL `passed/total*100`. **Fixed-month window** (same shape as 7867). admin auth via `require_auth`. operator + auditor-grade PDF feed. | **REAL Rule-1 violation — P0** |
| portal.py:1308 stored-value read | TRUE — reads `compliance_packets.compliance_score` column. **NOT a live compute** — this is a read-from-stored. Different class than the inline-aggregation Rule-1 violation; falls under "non-canonical column accessor" which is a *separate* Rule-1 sub-class. | **STORED-VALUE READ** — different remediation pattern (delete row, recompute via helper, persist). |
| 3 phantom line numbers | Prior fork named `routes.py:5786` + `:7627` as phantom. Confirmed phantom. **BUT prior fork MISSED `routes.py:3203 + :3216` (stats-deltas compliance_now/compliance_prev) and `routes.py:3729` (ClientStats /stats/{site_id}) and `routes.py:8678` (/admin/sites/{site_id}/compliance-packet — different endpoint than 7867) and `client_quarterly_summary.py:345`.** | **5 NEW REAL violations** |

**Steve P0 — undercount:** Prior fork's "9 inline Rule-1 violations" enumeration was **incomplete**. Actual count is **11** (10 inline + 1 stored-value-read in portal.py:1308 which is a different Rule-1 sub-class). The phantom claim was correct (2 of the 11 brief items were not Rule-1 violations); the missing identification of 5 additional violations is the more serious gap.

**Steve P0 — auditor-PDF feed:** **3 of 11 callsites feed auditor-grade PDF artifacts** — `routes.py:7867` (admin monthly report), `routes.py:8678` (/admin/sites/{site_id}/compliance-packet JSON evidence packet), `org_management.py:1118` (/api/orgs/{org_id}/compliance-packet). These are Counsel Rule 1 P0 because the compliance number on a PDF that goes to an auditor MUST match the canonical helper output. Today they don't — they produce a different number than `compute_compliance_score()` because the inline `passed/total*100` doesn't use the latest-per-(check_type,hostname) dedup that the canonical does.

**Steve P0 — customer-facing quarterly summary:** `client_quarterly_summary.py:345` is **customer-facing** (F3 PDF). Comment in source explains "inline because compute_compliance_score uses NOW()-window_days, which can't take a fixed past quarter" — this is a legitimate canonical-helper feature gap. Per Counsel Rule 1, customer-facing inline aggregation is the highest-priority drive-down target. Helper enhancement (fixed-window from-date param) is the prerequisite.

**Steve P1 — downstream-of-helper subtlety:** `routes.py:4875` calls `get_all_compliance_scores(db)`, which is itself on the `migrate` allowlist in `canonical_metrics.py:83`. Driving down the helper closes 4 callsites in one shot (routes.py:178, :4687, :4846, :5010). Don't treat the 4 routes.py callers as 4 separate migration items — they collapse into one when the helper migrates.

**Steve P1 — pattern matrix:**

| Window pattern | Callsites | Helper support today | Required helper enhancement |
|---|---|---|---|
| Relative 24h | routes.py 3203, 3216 (split window), 3337, 3729 + get_all_compliance_scores callers | YES (`window_days=1`) | None |
| Relative 30d | routes.py 5732, 5767, 8678 | YES (`window_days=30`) | None |
| Fixed-month | routes.py 7867, org_management.py 1118 | NO | **Add `window_start: date, window_end: date` params** |
| Fixed-quarter | client_quarterly_summary.py 345 | NO | Same fixed-window param |
| Split-window (24h ending 7d ago) | routes.py 3216 | NO | **Add `window_end: datetime` param** (anchor lookback to a point in time, not NOW) |
| Stored-value | portal.py 1308 | N/A | Migrate write path (compliance_packets generator) to canonical |

---

### Maya (Database) — APPROVE with helper-enhancement prerequisite

**Helper feature-gap analysis:**

The canonical helper at `compliance_score.py:157` currently exposes:
```python
async def compute_compliance_score(
    conn, site_ids: List[str], *,
    include_incidents: bool = False,
    window_days: Optional[int] = DEFAULT_WINDOW_DAYS,
    _skip_cache: bool = False,
) -> ComplianceScore:
```

`window_days=N` translates to `WHERE checked_at > NOW() - INTERVAL '{N} days'` (verified at `compliance_score.py:198-202`). **No support for**:
1. **Fixed start-date** (monthly + quarterly packets need this)
2. **Fixed end-date** (stats-deltas 7d-ago comparison needs this)
3. **Arbitrary date-range** (combination of 1 + 2)

**Maya P0 — helper enhancement design:**

Recommended signature extension (additive, backward-compatible):
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
Semantics: if `window_start` OR `window_end` is set, ignore `window_days`. If only `window_start`, end = NOW. If only `window_end`, start = `window_end - {window_days} days` (default 90). If both, use as bounds. Cache key includes the resolved start/end pair.

**Maya P1 — cache implications:** Today's cache TTL is 60s on relative-window calls. Fixed-window calls (monthly/quarterly packets) should cache LONGER (1h+) because the result is deterministic on the bounded range. Suggest cache key includes `(window_start, window_end)` tuple and a separate TTL ladder.

**Maya P1 — RLS context:** All 11 callsites use either `admin_transaction` (operator paths) or `org_connection` (would be the case after migration of customer-facing). The helper at `compliance_score.py:190-192` says "RLS-aware — caller must have set app.current_org or app.current_tenant" — so migration is a straight `conn` swap. No RLS surprises.

**Maya APPROVE:** Helper enhancement is small (~30 LOC + tests). Migration mechanically follows.

---

### Carol (Security) — N/A per brief

Per brief Carol is N/A — these are read-paths, not auth/PHI boundary changes. **Carol-NOTE:** the auditor-PDF feed (3 P0 callsites) becomes a Counsel Rule 1 + Rule 10 ("never let the platform imply clinical authority") joint risk. If the inline-computed number differs from the canonical, the auditor sees TWO different compliance scores for the same site/month across surfaces — that's an enterprise-embarrassment class. Worth flagging to Counsel as a secondary harm beyond the Rule 1 base.

---

### Coach (Process/Quality) — APPROVE with batching guidance

**Coach P0 — per-callsite Gate A is too granular.** Running 11 separate Gate A reviews would burn ~6h of fork time on mechanical migrations. **Coach's recommendation:** 3-phase batched approach.

**Phase A (1 Gate A, ~1d):** Canonical helper enhancement — add `window_start` + `window_end` params + tests + cache-key update. Closes the prerequisite.

**Phase B (3 separate Gate As — windowing differs per artifact, ~2d):** Customer-facing + auditor-PDF-feed callsites:
- B1: client_quarterly_summary.py:345 (F3 PDF — fixed-quarter) — its own Gate A because customer-facing, PDF byte-determinism, F3 artifact contract
- B2: routes.py:7867 + 8678 (admin monthly-report + admin compliance-packet — fixed-month) — joint Gate A because identical window shape, same evidence-packet class
- B3: org_management.py:1118 (org compliance-packet — fixed-month) — its own Gate A because cross-site aggregation + per-site breakdown shape

**Phase C (1 Gate A, ~1d):** Operator-internal mechanical batch — routes.py 3203, 3216, 3337, 3729, 5732, 5767 (6 callsites, all relative-window already supported by helper, mechanical swap). Plus get_all_compliance_scores helper migration (closes 4 caller-sites in one).

**Coach P0 — DO NOT migrate routes.py:5732 stats-delta 3216 first.** Both have non-standard window shapes (3216 is split-window 24h-ending-7d-ago; 5732 is go-agent specific). They need the `window_end` enhancement from Phase A to land first. Sequencing matters.

**Coach P1 — portal.py:1308 is a separate workstream.** It reads from `compliance_packets.compliance_score` column. The fix is at the WRITE path (whatever generates compliance_packets rows must use canonical helper output), not the read. Track as task #50 child but not in this batch.

**Coach P1 — Gate B for each phase MUST cite full pre-push sweep results** (Session 220 lock-in). Diff-only review is automatic BLOCK pending sweep.

---

### OCR (Counsel Rule 1 enforcement) — P0 batch identified

**Counsel Rule 1 priority ranking:**

| Priority | Callsite | Reason |
|---|---|---|
| P0-1 | client_quarterly_summary.py:345 | **Customer-facing PDF.** F3 artifact. Auditor + customer both see this number. |
| P0-2 | routes.py:8678 + 7867 | **Auditor-grade JSON evidence packet.** Compliance number is the headline metric on the packet. If it diverges from canonical, "two compliance scores for same site/month across surfaces" is the embarrassment class. |
| P0-3 | org_management.py:1118 | Same as P0-2 — org-level packet, auditor sees it. |
| P1-1 | portal.py:1308 | Customer-facing read, but read-from-stored. Real fix is at the writer. Lower urgency because the writer (compliance_packets generator) is operator-only triggered. |
| P2 | routes.py 3203, 3216, 3337, 3729, 4875, 5732, 5767 | Operator-internal. Still Rule-1 violations in spirit (substrate-invariant pollution if sampled) but no customer/auditor surface. |

**OCR P0 — recommend Counsel review BEFORE Phase B closes.** The fact that compliance-packet PDFs today contain a non-canonical compliance number is a finding worth surfacing in the Counsel-queue bundle (Task #37). Not a §164 violation per se but a Rule 1 finding that affects auditor-artifact integrity.

---

### PM — Effort estimate

| Phase | Callsites | Gate As | Implementation | Total |
|---|---|---|---|---|
| A (helper enhancement) | 1 (helper) | 1 | ~3h | ~5h with Gate B |
| B1 (quarterly PDF) | 1 | 1 | ~2h + PDF-determinism verification | ~4h |
| B2 (admin packets) | 2 | 1 (joint) | ~3h | ~5h |
| B3 (org packet) | 1 | 1 | ~2h | ~4h |
| C (operator batch) | 6 + helper migration | 1 | ~4h | ~6h |
| **Total** | **11** | **5** | **~14h** | **~24h** |

**PM-NOTE:** Far smaller than the "9 separate Gate As" the prior fork implied (~6h fork-time alone). The 3-phase batching cuts review overhead by ~60% while keeping Coach's per-class-windowing discipline.

---

### Counsel (Rule 1 drive-down priority) — APPROVE 3-phase plan

**Final Counsel priority:** Phase A unblocks Phase B (auditor-PDF feed P0s). Phase B closes the customer-facing + auditor-grade exposure. Phase C is operator-internal cleanup — important for substrate-invariant signal quality but not customer/legal exposure. Sequence: A → B1+B2+B3 (parallel) → C.

**Counsel-NOTE:** After Phase B, update Task #50 (Counsel Priority #4 — Rule 1 canonical-source registry) ratchet `BASELINE_MAX` decrement. Each phase decrements the count, gates regression.

---

## Per-callsite verified shape + migration cost

| # | File:Line | Verified window | Customer-facing | Migration cost | Phase | Notes |
|---|---|---|---|---|---|---|
| 1 | routes.py:3203 | 24h (relative) | NO (operator) | LOW (helper swap) | C | mechanical |
| 2 | routes.py:3216 | 24h ending 7d ago | NO (operator) | MED (needs window_end param) | C-after-A | sequencing: Phase A first |
| 3 | routes.py:3337 | 24h (relative) | NO (operator) | LOW | C | mechanical |
| 4 | routes.py:3729 | 24h (relative) | NO (operator) | LOW | C | mechanical |
| 5 | routes.py:4875 (get_all_compliance_scores) | 24h (relative) | NO (operator) | MED (migrate helper first → closes 4 callers) | C | helper-level migration |
| 6 | routes.py:5732 | 30d trend + go-agent | NO (operator) | MED (bespoke per-category logic) | C | mechanical w/ care |
| 7 | routes.py:5767 | 30d (relative) | NO (operator) | LOW | C | mechanical |
| 8 | routes.py:7867 | fixed-month | NO (operator) BUT feeds JSON evidence packet | HIGH (needs window_start/end + auditor-PDF byte-determinism) | B2 | **P0 Counsel** |
| 9 | routes.py:8678 | 30d (relative) | NO (operator) BUT feeds /admin/sites/{id}/compliance-packet JSON | MED | B2 | **P0 Counsel** |
| 10 | org_management.py:1118 | fixed-month | NO (operator) BUT feeds /api/orgs/{id}/compliance-packet | HIGH (cross-site aggregation + fixed-month) | B3 | **P0 Counsel** |
| 11 | client_quarterly_summary.py:345 | fixed-quarter | **YES (F3 PDF)** | HIGH (customer-facing + PDF determinism + fixed-quarter) | B1 | **P0-1 Counsel** |
| (12) | portal.py:1308 | stored-value | YES (read-from-stored) | DIFFERENT CLASS — fix writer not reader | separate workstream | Not in this batch |

---

## Batching recommendation

**Phase A (this batch, FIRST):**
- Single commit: helper enhancement at `compliance_score.py:157` — add `window_start` + `window_end` params, cache-key update, 3 new unit tests
- Gate A: per-helper enhancement (windowing semantics, RLS preservation, cache TTL ladder)
- Gate B: full pre-push CI sweep + curl-test of existing customer-facing endpoints (no regression on 30d/90d/None paths)

**Phase B1 (parallel — customer-facing PDF):**
- client_quarterly_summary.py:345 migration. Drop inline SQL, call `compute_compliance_score(conn, site_ids=[site_id], window_start=q_start, window_end=q_end)`. Verify F3 PDF byte-determinism still holds (kit-determinism contract per Session 218 round-table).
- Gate A: customer-facing + PDF determinism + F3 contract
- Gate B: full pre-push sweep + curl-test F3 PDF + verify hash unchanged across 2 downloads of same quarter

**Phase B2 (parallel — admin packets):**
- routes.py:7867 + routes.py:8678 joint migration (different endpoints, similar windowing).
- Gate A: auditor-JSON-evidence-packet contract + monthly-window correctness
- Gate B: full sweep + curl-test both endpoints + diff resulting JSON compliance_score against canonical recompute

**Phase B3 (parallel — org packet):**
- org_management.py:1118 migration. Cross-site aggregation via canonical helper.
- Gate A: cross-site aggregation semantics + per-site breakdown preservation
- Gate B: full sweep + curl-test + auditor-PDF feed verification

**Phase C (after A, last):**
- 6 operator-internal callsites + get_all_compliance_scores helper migration. Mechanical batch in 1 commit.
- Gate A: mechanical-batch verification + substrate-invariant signal expectation
- Gate B: full sweep + Phase 2b sampler can now be wired for these endpoints (originally Task #67 scope)

---

## Gate A schedule

| Date | Phase | Owner |
|---|---|---|
| 2026-05-13 (today) | This Gate A enumeration (DONE) | fork |
| 2026-05-14 | Phase A Gate A — helper enhancement | next fork |
| 2026-05-15 | Phase A Gate B + ship | impl |
| 2026-05-16 (parallel) | B1, B2, B3 Gate As | 3 parallel forks (per Coach: each is its own contract surface) |
| 2026-05-17–18 | B1/B2/B3 Gate B + ship | impl |
| 2026-05-19 | Phase C Gate A (mechanical batch) | fork |
| 2026-05-20 | Phase C Gate B + ship | impl |
| 2026-05-20 | Unblock Task #67 sampler integration on the 7 newly-canonical callsites | impl |

---

## Top P0/P1

**P0-1 (Steve — undercount):** Prior fork's enumeration missed 5 violations (routes.py:3203, 3216, 3729, 8678 + client_quarterly_summary.py:345). Update Task #83 description to "11 inline Rule-1 violations" not 9, and reference this Gate A's per-callsite matrix as the authoritative list.

**P0-2 (Steve + Counsel):** 3 callsites feed auditor-grade PDF artifacts (routes.py:7867, routes.py:8678, org_management.py:1118) — Counsel Rule 1 P0. Plus client_quarterly_summary.py:345 is customer-facing PDF (F3). **4 callsites are Rule 1 P0**, not just "operator-internal cleanup."

**P0-3 (Maya):** Helper enhancement (`window_start` + `window_end` params) is the prerequisite for 4 of the 11 migrations. Land Phase A before attempting Phase B.

**P0-4 (Coach):** Per-callsite Gate A would burn 6h+ of fork overhead. 3-phase batched approach with 5 Gate As total is the correct granularity (1 per windowing class + 1 mechanical batch).

**P1-1 (Coach):** routes.py:3216 (split-window 24h-ending-7d-ago) has the same window-end requirement as Phase A — sequence it after helper enhancement.

**P1-2 (Coach):** portal.py:1308 is a stored-value read, different class — track as separate Task #50 child workstream, NOT in this batch.

**P1-3 (OCR):** Surface the Phase B finding (compliance-packet PDFs today produce non-canonical numbers) in Counsel-queue bundle (Task #37). Auditor-artifact integrity finding.

**P2-1 (PM):** Effort estimate ~24h across all 5 Gate As + 5 ship commits. Budget over 1 week.

---

## Final overall verdict

**APPROVE-WITH-FIXES.**

The 9-violation enumeration in Task #67 Gate A is empirically **undercounted by 5** (actual: 11 inline + 1 stored-value-read different-class). The fork's broad shape (multiple files, multiple windowing semantics, can't sample without migrating) is correct, but the specific callsite list needs the corrections in this Gate A's matrix.

**Proceed with 3-phase batched plan**: Phase A (helper enhancement) → Phase B (4 P0 customer-facing/auditor-PDF callsites, 3 parallel Gate As) → Phase C (7 operator-internal mechanical batch in 1 Gate A). 5 Gate As total over ~1 week. Unblocks Task #67 sampler integration on the 7 newly-canonical operator paths upon Phase C close.

**Sequencing is load-bearing**: 4 callsites cannot migrate until helper supports fixed-window params. Helper enhancement (Phase A) is the unblock. Phase B and Phase C can run parallel once A lands.

**Counsel involvement**: The auditor-PDF finding (3 callsites today produce non-canonical compliance numbers on auditor-grade artifacts) should be added to Counsel-queue bundle Task #37 as a Rule 1 + Rule 10 joint finding.

**Task #83 description update**: change "9 inline Rule-1 violations" → "11 inline Rule-1 violations + 1 stored-value-read separate workstream"; cite this Gate A as the authoritative per-callsite matrix.
