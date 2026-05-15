# Coach Verdict — Task #58 P-F9 Profitability v1 Gate A BLOCK — Redesign Feasibility (Gate A)

**Date:** 2026-05-14
**Task:** #58 — "P-F9 profitability v1 — Gate A BLOCK redesign"
**Format:** Class-B 7-lens fork-style adversarial Gate A — **research + redesign-feasibility**, not implementation Gate A
**Lenses:** Steve / Maya (DB) / Carol (Security) / Coach / Auditor (OCR) / PM / Counsel (Attorney)
**Docs under review:** `audit/p-f9-estimated-profitability-design-2026-05-13.md` (v1, BLOCKED) · `audit/p-f9-partner-profitability-design-2026-05-09.md` (v0, never gated) · `audit/coach-p-f9-profitability-v1-gate-a-2026-05-13.md` (the original BLOCK)
**Verdict:** **VIABLE REDESIGN EXISTS — proceed to v2 spec → fresh Gate A. But DEFER start behind #52 + #56 (PM call).**

---

## 300-word summary

P-F9 is the partner-internal "profitability packet" — an Ed25519-signed PDF that shows an MSP CFO (persona "Brendan") the estimated economics of their OsirisCare channel. **v1 was correctly BLOCKED** on 2026-05-13. The BLOCK was not a "the numbers don't exist" rejection and not a Counsel-rule rejection of the *concept* — it was **10 P0s of design-execution drift**, three of them independently sufficient: (1) migration-number collision with Task #50's mig 314, (2) the design's "no fabricated number EVER" promise is structurally false — `compute_partner_rate_bps()` (mig 233:105) falls through to a hardcoded `COALESCE(…, 4000)` = 40%, verified still true today, and (3) sibling-parity header names were invented (`X-Valid-Until` / `X-Artifact-Version`) when real siblings emit `X-Attestation-Hash` + `X-Letter-Valid-Until`.

**The data IS canonical-sourceable.** Revenue = `subscriptions` ledger + `PARTNER_PLAN_CATALOG` (Stripe `lookup_keys`). Commission rate = `compute_partner_rate_bps()`. Cost-to-serve = operator-supplied assumptions row (explicitly non-canonical, must be marked so per Counsel Rule 1 — and v1 already does this honestly). No PHI (subscription metadata only). The artifact is partner-internal, no public `/verify`. So Counsel Rule 1, Rule 2, Rule 10 are all *satisfiable* — v1 just shipped factual drift against the as-built SQL.

**Two BLOCKS are now structurally resolved** by work that landed after the BLOCK: Task #59's RESERVED_MIGRATIONS ledger already reserves **mig 317 + 318** for P-F9 v2. The header-name fix is a 5-minute correction. The `compute_partner_rate_bps` reconciliation is the only one needing a real decision.

**A viable v2 exists** — the BLOCK itself enumerates the full fix list. But P-F9 is a *partner-convenience* artifact competing with two Counsel-priority P0s (#52 BAA-expiry enforcement, #56 master BAA). **PM recommendation: DEFER #58 start until #52 + #56 clear; the redesign spec is cheap (~0.5 day) and the BLOCK already wrote most of it, but executing it now is a priority inversion.**

---

## v1 BLOCK reason — categorized

The original Gate A (`audit/coach-p-f9-profitability-v1-gate-a-2026-05-13.md`) returned **BLOCK** with 10 P0s + 15 P1s + 3 P2s. Categorizing per the task's probe:

| Category | Verdict |
|----------|---------|
| **Data-availability problem** (numbers don't exist) | **NO.** Revenue, rate, clinic counts all exist in canonical tables. Cost is operator-supplied and v1 honestly marks it non-canonical. |
| **Counsel-rule problem** (Rule 1 / 2 / 10 violation in the *concept*) | **NO.** The concept satisfies all three. v1's disclaimers, opaque-mode emails, non-canonical cost labeling were called "the right work" by the original verdict. |
| **Scope problem** | **PARTIAL.** PM lens flagged the 3-day estimate as optimistic (realistic 4–5 days) and §8 had 8 open user-gate questions where a Gate-A-ready design should have ≤2. Scope was under-estimated, not wrong. |
| **Design-execution drift** (design claims that don't match as-built code) | **YES — this is the actual BLOCK.** 3 independently-sufficient P0s are all "the design asserts X about existing code; X is false." |

**The three load-bearing BLOCKS, re-verified against today's codebase:**

1. **Migration collision.** v1 claimed mig 314 + 315. Mig 314 was locked by Task #50 (`canonical_metric_samples`). **Verified today:** `migrations/314_canonical_metric_samples.sql` and `315_substrate_mttr_soak_v2.sql` are both on disk and shipped. v1's numbers were taken. **→ RESOLVED:** the RESERVED_MIGRATIONS ledger (Task #59, shipped after the BLOCK) now reserves **mig 317 (`partner_profitability_assumptions`) + mig 318 (`partner_profitability_packets`)** for Task #58 explicitly. This BLOCK is structurally closed.

2. **`compute_partner_rate_bps()` does not return NULL.** v1 §2.4: *"If it returns NULL → `rate_unavailable = true` → PDF renders '—'… No fabricated number EVER."* **Verified today** at `migrations/233_partner_revenue_tiers.sql:103-105`: the function's 3rd arm is `SELECT COALESCE(revenue_share_percent, 40) * 100 … RETURN COALESCE(v_flat_pct, 4000)`. On a valid `partner_id` it **always** returns a number — 4000 bps (40%) worst case. The "no fabricated number" promise is false against the as-shipped SQL. Still true today. **→ NOT yet resolved.** Needs a v2 decision (see Steve lens).

3. **Header-parity drift.** v1 §4 named `X-Valid-Until` + `X-Artifact-Version`. **Verified today:** `partners.py:5674-5675, 6021-6027, 6599-6600` emit `X-Attestation-Hash` + `X-Letter-Valid-Until` (+ `X-Attestation-Id` on one sibling). `X-Artifact-Version` is emitted by nobody. `tests/test_artifact_endpoint_header_parity.py` pins this. **→ RESOLVED by a trivial spec correction** — use the real names.

---

## Per-lens — redesign feasibility

### Lens 1 — Steve (Engineering) — **VIABLE, one real decision**

The BLOCK already wrote the v2 spec — it's the 10-P0 list. Of those: #1 (mig numbers) resolved by ledger; #3 (headers) trivial; #4 (PARTNER_PLAN_CATALOG refactor — real second consumer is `billing.py:46`, with an import-cycle constraint cited inline at `partners.py:3296`) is real but well-understood plumbing; #5–#10 are all "add a CI gate / lock a §8 question / add a watermark" — mechanical.

**The one decision that's actually load-bearing: P0-Steve-2 (`compute_partner_rate_bps`).** v2 must pick a lane:
- **(a)** Strip the `COALESCE(…, 4000)` legacy arm in a Phase-0 migration so the function genuinely returns NULL when no per-partner tier + no platform default + no legacy flat % exists — then P-F9's em-dash branch becomes load-bearing and honest. *But:* mig 233 also inserts platform-default rows (3000/3500/4000/4500 bps for 0/10/25/50+ clinics), so on a real partner the platform-default arm fires first and NULL is still nearly unreachable. Stripping the legacy arm is *correct* hygiene but doesn't make the em-dash branch meaningfully reachable.
- **(b)** Rewrite v2 §2.4 to tell the truth: *"the platform's hardcoded 40% fallback is what renders for an unconfigured partner; `/me/commission` has shipped with this behavior for months; v1 accepts it."* Drop the "no fabricated number EVER" claim entirely.

Steve's recommendation: **(b) is honest and ships; (a)-as-hygiene can be a separate carried task.** The directive-citation rule (CLAUDE.md) demands the design not assert behavior the code doesn't have. v2 must not repeat the v1 sin of describing aspirational code as as-built.

**Verdict: VIABLE.** No redesign of *purpose* needed. ~0.5 day to re-spec, exactly as the original BLOCK said.

### Lens 2 — Maya (Database) — **VIABLE, data is sourceable**

Does the platform HAVE the data to compute partner profitability? **Yes, with one honest caveat:**

| Number | Source | Canonical? |
|--------|--------|------------|
| Active MRR | `subscriptions` (status IN active/trialing) × `PARTNER_PLAN_CATALOG` plan amounts | **Canonical** — Stripe is SoT for the `subscriptions` projection; plan amounts via Stripe `lookup_keys` |
| Effective commission rate | `compute_partner_rate_bps()` (mig 233) | **Canonical** — same function `/me/commission` uses (consistency non-negotiable) |
| Active clinic / site counts | `sites` ⋈ `subscriptions`, `s.status != 'inactive'` filter | **Canonical** |
| Estimated commission | `partner_payout_runs` (closed) or provisional via `compute_partner_commission()` | **Canonical**, label provisional clearly |
| **Cost-to-serve / margin** | operator-typed `partner_profitability_assumptions` row | **NON-canonical — and that's fine.** It's the operator's own input; v1 already marks it explicitly as operator-supplied with `updated_by_email` + `updated_at` audit trail. This satisfies Counsel Rule 1: a non-canonical number is *allowed* as long as it's *declared* non-authoritative. |
| Lifetime paid | `partner_invoices` / `partner_payouts` (mig 235) | **Not yet populated** — v1 correctly scopes this to v2, ships `lifetime_paid_cents = 0` until Stripe Connect onboarding completes. |

Maya's v1 lens was **APPROVE-WITH-FIXES** (not BLOCK) — the DB shape is sound. v2 carries her P0/P1s: `kind`-column immutability trigger, append-only assumptions table with `assumptions_id_snapshot` FK, RLS policies mirroring mig 289, RESTRICT (not CASCADE) on `partner_profitability_packets.partner_id`. All mechanical, all in the BLOCK already.

**Verdict: VIABLE. This is NOT a "numbers don't exist" block.** The profitability story is genuinely computable from canonical sources; the only non-canonical input (cost) is honestly disclosed.

### Lens 3 — Carol (Security) — **VIABLE, two P0s already enumerated**

**PHI risk (Rule 2): NONE.** P-F9 touches subscription metadata, plan tiers, clinic *counts*, opaque site short-ids — never PHI, never clinic legal names, never billing-contact info. The artifact stays in the "operational counts + finance" lane. v1's §3.3 "what we DO NOT print" is correct.

**Cross-partner leak risk: real but bounded, and the BLOCK caught it.** Carol's v1 lens (APPROVE-WITH-FIXES) raised two P0s that v2 must carry:
- **P0-Carol-1:** RT31-class sibling-endpoint role-gate sweep — enumerate every endpoint that SELECTs `partner_profitability_assumptions`, CI-gate that the cost figures are reachable ONLY through `admin,billing`-gated endpoints. Note: `subscriptions` has no RLS (v0 §4.2 flagged this) — every query must hard-filter `s.partner_id = $1` AND run under `admin_transaction()`.
- **P0-Carol-2:** the "first 8 chars of site_id UUID" opaque id is a false-sense-of-security shape — a partner who downloads P-F9 also has `/api/partners/me/sites` and can re-anchor. v2 must use per-packet randomized row-numbers, not site_id-prefixes. **Lock as P0 design decision, not a §8 user-gate question.**

Both are server-side-generation hardening, both fully specified in the BLOCK.

**Verdict: VIABLE.** No PHI exposure; cross-partner leak vectors are enumerated and fixable with the patterns the platform already has (RLS mirror + CI gate).

### Lens 4 — Coach (load-bearing: was the BLOCK correct?) — **BLOCK WAS CORRECT. Concept is sound, needs rescoping not cutting.**

**Was the BLOCK correct? Yes, unambiguously.** Three independently-sufficient P0s, all of which I re-verified against the codebase today (mig 233:105 still has the 40% fallback; `partners.py` still emits `X-Letter-Valid-Until` not `X-Valid-Until`; mig 314/315 shipped under other tasks). The original verdict was not over-zealous — it was a clean catch of design-vs-as-built drift, exactly the class Session 220's lockstep lessons exist for.

**Is P-F9 a good idea that needs rescoping, or a bad idea that should be cut?** **Good idea, needs rescoping + deprioritizing — not cutting.** Reasoning:
- It's the 6th member of a *shipped* artifact family (P-F5/F6/F7/F8 are live). Sibling-parity infrastructure exists. The marginal cost of P-F9 is lower than a greenfield artifact.
- The persona is real (partner round-table 2026-05-08 named Brendan-the-CFO as the 5th MSP-internal actor).
- It's *not* duplicative — Stripe Express shows payouts, but nothing shows the MSP a per-channel estimated-margin view.

**Staleness check.** v0 design = 2026-05-09. v1 design + BLOCK = 2026-05-13. Today = 2026-05-14. **Task #58 is one day stale — not stale at all.** The RESERVED_MIGRATIONS ledger still actively holds mig 317+318 for it. P-F9 has *not* gone stale; it's a freshly-blocked task with a clear fix path.

**But Coach's load-bearing concern is priority, not viability.** P-F9 is a *convenience artifact for a partner's internal finance team*. The current backlog has #52 (BAA-expiry machine-enforcement — Counsel Priority #1, Rule 6), #56 (master BAA contract — Counsel TOP-PRIORITY P0), #50 (canonical-source registry — Counsel Priority #4) all in-progress or pending. **Shipping a CFO-convenience PDF ahead of foundational BAA legal-exposure work is a priority inversion.** The BLOCK bought us the right outcome for the wrong-feeling reason: it *delayed* a low-priority artifact, which is fine.

**Verdict: BLOCK was correct. Concept survives. Rescope + defer, do not cut.**

### Lens 5 — Auditor (OCR) — **VIABLE — Counsel Rule 1 is satisfiable**

Counsel Rule 1: *every customer-facing metric declares a canonical source; anything non-canonical is hidden or marked non-authoritative.*

P-F9's metrics split cleanly:
- **Canonical metrics** (MRR, rate, clinic count, commission) — declare their source table in a footnote. Trivial.
- **The one non-canonical metric** (cost-to-serve, hence margin) — v1 *already* marks this honestly: "ESTIMATED — INTERNAL USE ONLY" banner, operator-supplied cost figures shown verbatim with `updated_by_email` audit trail, explicit footnote when a tier cost is $0. **This is Rule-1-compliant by construction** — the artifact is marked non-authoritative *as a whole*, and the non-canonical input is attributed to the operator, not to OsirisCare.

The OCR lens in the original Gate A was **APPROVE-WITH-FIXES** — the fixes were hardening (diagonal watermark to survive screenshot-crop of the banner; extended banned-words list for the financial-projections class — `EBITDA`, `net income`, `forecast`, `projection`, `bottom line`, etc.; positive-required hedging-vocabulary test). None of those are "the artifact can't be Rule-1-compliant" — they're "make the non-authoritative marking *robust*."

**Verdict: VIABLE.** The artifact CAN declare canonical sources for its canonical numbers and mark itself non-authoritative for the estimated margin. v1 already does the substance; v2 hardens the delivery.

### Lens 6 — PM — **DEFER. Redesign now is a priority inversion.**

Three states #58 could be in: redesign-now / defer / cut. **Recommendation: DEFER.**

- **Not cut:** P-F9 is viable (every other lens agrees), sibling-parity infra exists, persona is real, task is one day old. Cutting destroys real prior design work for no gain.
- **Not redesign-now:** the backlog has #56 (master BAA — Counsel TOP-PRIORITY P0), #52 (BAA-expiry enforcement — Counsel Priority #1), #50 (canonical-source registry — Counsel Priority #4) all ahead of it in legal-exposure severity. The user's own MEMORY.md flags "Formal HIPAA-complete BAA not memorialized" as "Most urgent finding." A CFO-convenience PDF does not jump that queue.
- **Defer:** the redesign *spec* is cheap — the BLOCK verdict already enumerated all 10 P0s + 15 P1s with fixes; an author could turn it into a v2 design doc in ~0.5 day. But *executing* P-F9 (Phase 0 refactor + 2 migrations + endpoints + PDF generator + tests + Gate B) is the BLOCK's realistic 4–5 days. That 4–5 days belongs to #52/#56 first.

**Concrete PM action:**
1. Leave #58 `pending`. Add a `blockedBy` relationship or at minimum a comment: "deferred until #52 + #56 clear — redesign spec is ready-to-write from `audit/coach-p-f9-profitability-v1-gate-a-2026-05-13.md`."
2. Keep mig 317+318 reserved in RESERVED_MIGRATIONS — but note the ledger's stale-rule: `expected_ship: 2026-05-30`. If #52/#56 run long, the P-F9 rows go STALE on 2026-06-29 and need a per-row stale-justification or release. **Recommend: pre-emptively add `<!-- stale-justification: deferred behind Counsel-priority #52 + #56 -->` to both rows now** so CI doesn't flag them later.
3. When #58 *does* start: the v2 design doc must pre-decide §8 Q1/Q4/Q5/Q6/Q7/Q8 (collapse 8 user-gate questions to ≤1), claim mig 317/318 from the ledger, and go through a fresh Gate A — the v1 BLOCK does not auto-clear.

### Lens 7 — Counsel (Attorney) — **VIABLE, but two outside-counsel §-Qs must bundle into #37**

Counsel Rule 1 (canonical metric) + Rule 10 (never imply authority the platform doesn't have).

**Rule 1:** satisfiable — see OCR lens. The artifact marks itself non-authoritative; canonical numbers cite sources; the estimated margin is attributed to operator input.

**Rule 10 — the real Counsel concern:** P-F9 is an *Ed25519-signed* document that a partner may show to *their* stakeholders (their CPA, a future M&A buyer doing diligence). The signature establishes provenance — normally a feature — but here it risks converting "MSP's internal spreadsheet" into "an OsirisCare-signed document the MSP relied upon for a pricing decision." If Brendan uses a P-F9 estimate to justify raising a client's renewal price, and that client later subpoenas the document, OsirisCare's signature on it is a representation-risk surface.

**Mitigation (from v1 Attorney lens, must carry to v2):**
- The canonical-JSON `signed_by` field must read `"osiriscare-substrate-system"` with explicit scope: *the signature attests integrity of the data shape, NOT financial accuracy of the underlying numbers.*
- Strengthen the banner disclaimer: *"…may differ materially from actual results… Not a forecast or projection within the meaning of any securities law."*
- Banned-words must include the GAAP/securities-adjacent class (`EBITDA`, `net income`, `gross profit`, `forecast`, `projection`, `guaranteed margin`).

**Two outside-counsel §-Qs — bundle into Task #37 (counsel-queue, 5 questions/1 engagement):**
1. Signature-scope: does an Ed25519-signed partner-internal financial estimate create representation exposure for OsirisCare if it surfaces in an M&A or customer dispute?
2. §164.504(e): does a partner-internal financial document enumerating BAA-bound clinics by opaque short-id constitute disclosure of BA-relationship metadata? (Probably no — partner knows their own roster — but it's a novel artifact class.)

**Verdict: VIABLE with mandatory disclaimer hardening + the two §-Qs routed to #37.** The concept does not create *unmanageable* representation risk — but v2 cannot ship until the signature-scope language is locked, and #37 should answer the two §-Qs before P-F9 v2 reaches Gate B.

---

## Data-availability assessment — explicit answer to the task's core probe

**Is this fundamentally a "numbers don't exist" block? NO.**

| Number | Exists? | Canonical source |
|--------|---------|------------------|
| Active MRR | YES | `subscriptions` × `PARTNER_PLAN_CATALOG` (Stripe `lookup_keys`) |
| Effective commission rate | YES | `compute_partner_rate_bps()` mig 233 — *with the 40%-fallback caveat v2 must disclose honestly* |
| Active clinic / site counts | YES | `sites` ⋈ `subscriptions` |
| Estimated / provisional commission | YES | `partner_payout_runs` + `compute_partner_commission()` |
| Cost-to-serve / margin | YES, as operator input | `partner_profitability_assumptions` (operator-typed, **non-canonical by design, honestly marked**) |
| Lifetime paid commission | NOT YET | `partner_invoices`/`partner_payouts` — mig 235 Stripe Connect, no partner onboarded yet → correctly v2-scoped |

The platform has every number P-F9 v1 needs. The only number it *doesn't* have (lifetime paid) is correctly deferred to v2. The cost number is operator-supplied and honestly disclosed as such — which Counsel Rule 1 explicitly permits ("anything non-canonical is hidden **or marked non-authoritative**").

**The block was design-execution drift, not data scarcity.** v1 described aspirational code behavior (`compute_partner_rate_bps` returning NULL) and invented header names — both falsifiable against the repo, both falsified.

---

## Redesign sketch — P-F9 v2

A viable v2 exists. It is **not a re-conception** — it's the v1 design with the 10 P0s closed. Sketch:

**Phase 0 (spec + refactor, ~1 day):**
- v2 design doc carrying every P0/P1 from `audit/coach-p-f9-profitability-v1-gate-a-2026-05-13.md`. Pre-decide §8 Q1 (randomized row-number — Carol P0), Q4 (snapshot assumptions at issuance — Maya), Q5 (pre-bake `kind` column), Q6 (pre-bake 3 wholesale-cost columns), Q7 (byte-identical canonical-JSON, semantic-identical PDF), Q8 (log issuance metadata only, never cost figures). §8 collapses to ≤1 genuine open question.
- Claim **mig 317 + 318** from RESERVED_MIGRATIONS (already reserved for #58).
- Rewrite §2.4 to match `compute_partner_rate_bps` as-shipped (Steve option (b)) — drop "no fabricated number EVER," disclose the 40% fallback honestly.
- Fix §4 headers → `X-Attestation-Hash` + `X-Letter-Valid-Until` (drop `X-Valid-Until`/`X-Artifact-Version`).
- `PARTNER_PLAN_CATALOG` → `backend/plan_catalog.py` as a pure-constants module (real 2nd consumer is `billing.py:46`; import-cycle constraint at `partners.py:3296` must be resolved + CI-gated).

**Phase 1 (data backend, ~1 day):**
- Mig 317 `partner_profitability_assumptions` — append-only, CHECK 0..10M cents, RLS policies (mirror mig 289), `BEFORE UPDATE` immutability trigger.
- Mig 318 `partner_profitability_packets` — `kind`-immutable trigger, `assumptions_id_snapshot` FK, `partner_id` ON DELETE RESTRICT, partial unique idx on `superseded_by_id IS NULL`.
- `GET`/`PUT /api/partners/me/profitability-assumptions` — `require_partner_role("admin","billing")`, audit-log each PUT.
- `compute_estimated_profitability()` helper.

**Phase 2 (PDF + endpoints, ~2 days realistic):**
- `partner_profitability_packet.py` + Jinja2 template (StrictUndefined, no `.format()`).
- Issue + re-fetch endpoints, sibling-parity headers, opaque-mode issuance email.
- OCR hardening: diagonal watermark, extended banned-words list, positive-required hedging-vocabulary test.
- Counsel hardening: `signed_by` scope language, "not a forecast or projection" disclaimer.
- CI gates: role-gate sweep, `_sanitize_partner_text` AST scan, `asyncio.to_thread` AST scan, no-`.format(` AST scan, v1/v2 co-issue gate.
- **Fresh Gate A** on the v2 design before Phase 0, **Gate B** with full pre-push sweep before complete.

**Realistic total: 4–5 days** (as the v1 PM lens said — not the 3 days v1 claimed).

---

## Final verdict — **VIABLE REDESIGN EXISTS; DEFER EXECUTION behind #52 + #56**

- **(a) Why v1 was blocked:** design-execution drift — 10 P0s, 3 independently sufficient: migration-number collision (now resolved by the RESERVED_MIGRATIONS ledger → mig 317/318), `compute_partner_rate_bps()` does not return NULL as the design claimed (verified still true at mig 233:105 — needs an honest v2 §2.4 rewrite), and invented sibling-parity header names (trivial fix). It was **not** a data-availability block and **not** a Counsel-rule rejection of the concept.
- **(b) Does a viable v2 exist:** **YES.** Every lens agrees the concept is sound — data is canonical-sourceable, no PHI, cross-partner leak vectors are enumerated and fixable, Counsel Rule 1/2/10 are all satisfiable, the persona is real, sibling-parity infra is shipped. The BLOCK verdict itself is 90% of the v2 spec.
- **(c) Redesign sketch:** provided above — v1 design + 10 P0s closed, claim mig 317/318, honest `compute_partner_rate_bps` framing, real header names, pre-decide §8. ~0.5 day to spec, 4–5 days to execute.
- **(d) Recommendation:** **DEFER, do not cut.** P-F9 is a partner *convenience* artifact; #52 (BAA-expiry enforcement, Counsel Priority #1) and #56 (master BAA, Counsel TOP-PRIORITY P0) are foundational legal-exposure work that must clear first. Executing P-F9 now is a priority inversion. Keep #58 `pending` with a comment pointing at the ready-to-write redesign spec; pre-emptively add stale-justifications to the mig 317/318 ledger rows so CI doesn't flag them during the deferral; route Counsel's two §-Qs into Task #37. When #52/#56 clear, write the v2 design doc and run a **fresh Gate A** — the v1 BLOCK does not auto-clear.
