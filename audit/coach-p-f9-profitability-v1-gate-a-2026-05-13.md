# Coach Verdict — P-F9 Estimated Profitability Packet v1 (Gate A)

**Date:** 2026-05-13
**Design under review:** `audit/p-f9-estimated-profitability-design-2026-05-13.md`
**Format:** Class-B 7-lens fork-based adversarial Gate A
**Lenses:** Steve / Maya / Carol / Coach / OCR / PM / Attorney
**Verdict:** **BLOCK — redesign + revisions before any code lands**

---

## 250-word executive summary

P-F9 v1 sets out to ship Brendan-the-CFO an estimated-profitability PDF without waiting on Stripe Connect — sensible scoping, well-positioned as the 12-property F-series sibling, well-disclaimed at "ESTIMATED — INTERNAL USE ONLY." The author did the right work picking partner-internal posture (no public `/verify`), the right work pinning Ed25519 + StrictUndefined + `_sanitize_partner_text` + opaque-mode emails, and the right work flagging the cost-assumption versioning + OsirisCare-cost-tier shape as user-gates.

But the design ships with **three independently sufficient BLOCKS** before any code can land:

1. **Migration-number collision with Task #50 mig 314 (`canonical_metric_samples`).** Task #50 is in_progress, Counsel Priority #4 Rule 1, design at `audit/canonical-metric-drift-invariant-design-2026-05-13.md` §2 already locks 314. P-F9 design also claims 314. **Hard.** Next available pair is **316 + 317** (305, 307, 308–310, 312, 313 used; 306, 311 not yet present so verify before use).
2. **`compute_partner_rate_bps()` does NOT return NULL** the way the design claims. Mig 233:103-105 falls through to `COALESCE(v_flat_pct, 4000)` — a hardcoded 40%. P-F9's "no fabricated number EVER" promise + the "rate_unavailable=true → em-dash" UX branch is structurally unreachable on a valid partner_id. The current `/me/commission` Python-side rate_unavailable check is a vestigial belt-and-suspenders, not a load-bearing branch.
3. **Sibling-parity header naming drift.** Real siblings emit `X-Letter-Valid-Until`. Design names `X-Valid-Until` + `X-Artifact-Version` — neither exists.

Plus 2 secondary P0s + 5 P1s. Redesign required; ~0.5 day of re-spec.

---

## Per-lens verdict

### Lens 1 — Engineering (Steve) — **BLOCK**

**P0-Steve-1: Migration-number collision.** Latest applied migrations are `…305, 307, 308, 309, 310, 312, 313`. Design claims mig 314 for `partner_profitability_assumptions` and mig 315 for `partner_profitability_packets`. **Mig 314 is already locked** for Task #50's `canonical_metric_samples` per `audit/canonical-metric-drift-invariant-design-2026-05-13.md` §2 and `audit/coach-canonical-compliance-score-drift-v2-gate-a-2026-05-13.md` §"Phase 2a — mig 314 + partition wiring". Task #50 is in_progress (Counsel Priority #4, Rule 1 enforcement — high priority, won't yield). **Action:** renumber P-F9 to mig 316 (assumptions) + mig 317 (packets). Also verify 306 and 311 are not held by another in-flight design — quick grep before claiming them as the next free pair.

**P0-Steve-2: `compute_partner_rate_bps()` does not behave as the design claims.** Design §2.4 states: *"If it returns NULL → `rate_unavailable = true` → PDF renders '—' + an explicit operator-action footnote."* Verified at `migrations/233_partner_revenue_tiers.sql:79-107`: the function has **four** resolution arms — per-partner override → platform default → legacy flat % → **hardcoded `COALESCE(v_flat_pct, 4000)` (40%) fallback**. Given that mig 233 also inserts platform-default rows (3000 / 3500 / 4000 / 4500 bps for 0/10/25/50+ clinics), the function will essentially never return NULL on a valid `partner_id`. The Python-side `rate_unavailable` check at `partners.py:3344` is a defensive vestige, NOT a load-bearing branch. P-F9's "no fabricated number EVER" is therefore overstated — the platform fabricates a fallback today. **Action:** EITHER (a) tighten mig 233 to actually return NULL when no per-partner row + no platform default + no legacy flat % exists (strip the `COALESCE(…, 4000)` arm), THEN P-F9 can faithfully render em-dash + footnote, OR (b) acknowledge the design's claim is wrong and rewrite §2.4 to say "the platform's hardcoded 40% fallback is what renders when no rate is configured; design assumes this is acceptable for v1 because /me/commission has shipped with it for months." Cleanest fix: (a) plus a one-line CI gate that the legacy fallback is gone.

**P0-Steve-3: PARTNER_PLAN_CATALOG refactor — wrong second consumer named.** Design §2.1 + §7 Phase 0 say "factor out from `client_signup.py` + `partners.py`." Verified: the real second consumer is `billing.py:46` (`PARTNER_PLAN_CATALOG`). `partners.py:3296` *inlines* `MONTHLY_AMOUNT_CENTS` with an explicit comment *"Inlined here to avoid a cross-module import cycle"* — there is a real reason it's inlined, and a naïve refactor that swaps the inline for `from backend.plan_catalog import PARTNER_PLAN_CATALOG` will reintroduce the cycle. **Action:** Phase 0 must enumerate the import-cycle constraint, propose the resolution (e.g. `plan_catalog.py` imports nothing from `client_signup` or `partners` — pure constants module), and verify with `pylint --enable=cyclic-import` or `python -c "import backend.plan_catalog; import backend.client_signup; import backend.partners; import backend.billing"` in a clean process.

**P1-Steve-1: Mig 235 framing.** Design treats Stripe Connect as "not yet activated" but `migrations/235_partner_stripe_connect.sql` is applied and `stripe_connect.py` ships the Express-onboarding scaffold (`stripe_connect.py:107` create-account flow, `:526` rate-bps query). The honest v1/v2 framing is "**mig 235 shipped, but no partner has completed Stripe Connect onboarding AND no `partner_invoices` paid history exists yet** — v1 ships subscription-ledger-only until at least one partner crosses both gates." Tighten §6 + §2.3 wording.

**P1-Steve-2: `lifetime_paid_cents = 0` assumption.** Design §1 says "v1 reports `lifetime_paid_cents = 0` exactly like `/me/commission` does today." Verify the assumption isn't broken by future partial Stripe Connect onboarding — what does P-F9 v1 render if Stripe Connect IS active for the partner and `partner_invoices` HAS one paid row? Recommendation: P-F9 v1 should source `lifetime_paid_cents` from `partner_invoices` if any rows exist for the partner, NOT hardcode 0. Aligns with current `/me/commission` shape and avoids a stale-zero embarrassment.

---

### Lens 2 — Database (Maya) — **APPROVE-WITH-FIXES**

**P0-Maya-1: Mig 315 (P-F9 renumber: mig 317) — `kind` pre-bake + immutability.** Design §8 Q5 asks "pre-bake `kind` column?" Recommendation is yes, and Maya concurs — but ALSO ensure the v1→v2 immutability story is explicit: once a row is `kind='estimated'` + signed, it MUST NOT be UPDATE-able to `kind='verified'`. The v2 path is INSERT a new row that supersedes the v1 row (the v1 row stays untouched, signed, in history). Add a `BEFORE UPDATE` trigger that REJECTS any UPDATE on `kind` (or on `attestation_hash` / `ed25519_signature` / `canonical_payload`). Mirrors mig 175 `trg_enforce_privileged_chain` posture: schema-level last-line-defense.

**P1-Maya-1: `partner_profitability_assumptions` CHECK 0..10M cents — sensible upper bound, but missing context.** $100,000/mo/seat is plausibly higher than any real OsirisCare wholesale price. Document the chosen ceiling inline: `-- $100,000/mo per seat — defends against operator typo; raise if needed.` Also: add a `BEFORE INSERT OR UPDATE` trigger that REJECTS if `essentials_cost_cents > professional_cost_cents` or `professional_cost_cents > enterprise_cost_cents` (sanity invariant — costs should not invert across tiers). OR make this a soft warning surfaced in the PDF assumptions-footnote rather than a hard DB CHECK (operator might genuinely have a clinic-mix where essentials seats cost MORE — keep it advisory).

**P1-Maya-2: Append-only posture for `partner_profitability_assumptions`.** Design implies a single-row-per-partner table (UPDATE in place). But §5.4 ("auditor trail that the operator owns the cost numbers") + §8 Q4 ("snapshot at issuance time") together imply we need a history of operator's assumption-changes. **Recommendation:** make the assumptions table append-only (each `PUT` inserts a new row with `effective_from` / `effective_until` semantics), and `partner_profitability_packets.assumptions_id_snapshot` FK-references the specific assumptions row that was active at issuance. Consistent with `presenter_brand_snapshot` pattern. Today's design has `updated_at` + `updated_by_user_id` columns suggesting in-place UPDATE — fix the shape now or backfill later.

**P1-Maya-3: `partner_profitability_packets` RLS policies.** Mirror mig 289:108-120: `ENABLE ROW LEVEL SECURITY` + `partner_self_isolation` (USING `partner_id::text = current_setting('app.current_partner', true)`) + `admin_full_access`. Design §3.4 doesn't mention RLS — easy miss when "mirror mig 289" but RLS line numbers are key. Make explicit.

**P2-Maya-1: ON DELETE CASCADE for `partner_profitability_assumptions.partner_id`.** Design says `ON DELETE CASCADE`. Sibling mig 289:27 uses `ON DELETE RESTRICT` for `partner_id`. RESTRICT is correct here too: a partner DELETE should not silently nuke their packet history (which is signed/chained financial-adjacent metadata). Switch to RESTRICT for `partner_profitability_packets.partner_id`; assumptions table can stay CASCADE since it's mutable operator state.

---

### Lens 3 — Security (Carol) — **APPROVE-WITH-FIXES**

**P0-Carol-1: RT31-class role-gate sibling sweep is incomplete.** Design correctly names `require_partner_role("admin", "billing")` for P-F9. But RT31's lesson was that the threat-model is *sibling-endpoint leakage* — i.e. a partner_user with `role='tech'` accidentally pulls profitability data through some OTHER endpoint that exposes the same underlying data. **Concrete risk:** the assumptions row contains operator-typed cost figures; if any OTHER endpoint in `partners.py` returns the partners row or joined data, the cost figures could leak via the broader role gate (`admin,tech,billing`). **Action:** enumerate every endpoint that SELECTs from `partner_profitability_assumptions` or returns join-joined data; CI gate (extend `test_no_partner_mutation_uses_bare_require_partner.py` pattern) that the assumptions table is reachable ONLY through `admin,billing`-gated endpoints.

**P0-Carol-2: Opaque short-id leak via cross-reference.** Design §3.3 uses "first 8 chars of `site_id` UUID" as the opaque per-customer identifier. **Threat:** the same partner who downloads P-F9 also has access to `/api/partners/me/sites` (which returns full site_ids). They can trivially cross-reference 8-char prefix → full site_id → clinic name from their own roster. If the PDF leaks (subpoena, M&A diligence, lost laptop), the receiving party who *also* has the partner's roster reconstructs the per-clinic revenue map. §8 Q1 flags this but the recommendation is undecided. **Carol's recommendation:** per-packet sequential row-number (1, 2, 3…) randomized per issuance — NOT site_id-prefixed. The partner can still cross-reference if they keep their own ordering note, but a leaked PDF alone is unreadable. The 8-char prefix is a *false-sense-of-security* shape — looks anonymous but isn't. **Lock this as a P0 design decision before code, not a §8 user-gate question.**

**P1-Carol-1: Determinism contract — semantic-identical or byte-identical?** Design §8 Q7 punts to user-gate. Carol's stance: **byte-identical** for the canonical JSON payload (load-bearing — the Ed25519 signature IS over canonical-JSON bytes) but **semantic-identical** for the rendered PDF (WeasyPrint internal IDs may drift; pinning font hashes + WeasyPrint version is heavy for a partner-internal artifact). Document the asymmetry: re-rendering a packet by `attestation_hash` produces byte-identical canonical-JSON (signature verifies), MAY produce semantically-identical-but-byte-different PDF (cosmetic re-render). That's the right trade-off for partner-internal. Tighten §3.5 to say so explicitly.

**P1-Carol-2: PDF metadata strip.** WeasyPrint embeds `/CreationDate`, `/ModDate`, `/Producer` PDF metadata by default — wall-clock timestamps that will defeat byte-identical re-render claims. Sibling `partner_portfolio_attestation.py` may already handle this; verify. Either strip via `pikepdf.Pdf.open()` post-render, or use `WEASYPRINT_PDF_GENERATE_METADATA=0` env or equivalent.

**P1-Carol-3: Subpoena/discovery posture (§8 Q8) — `admin_audit_log` row content.** Design recommends "log issuance metadata but NOT cost figures or per-customer detail." Carol agrees — and lock it as P0 design (not §8 user-gate). The actor-email + hash + partner_id + issued_at goes in `admin_audit_log`; the canonical-JSON payload with cost figures stays in `partner_profitability_packets`, partner-RLS-isolated, admin-readable but never copied into `admin_audit_log`.

---

### Lens 4 — Coach (sibling-parity) — **BLOCK**

**P0-Coach-1: Sibling-parity header names are wrong in §4.** Design §4 names `X-Attestation-Hash`, `X-Attestation-Id`, `X-Valid-Until`, `X-Artifact-Version`. Verified at `partners.py:5628-5629`, `:5975-5981`, `:6553-6554`, and the parity-test fixture at `tests/test_artifact_endpoint_header_parity.py:102`: real siblings emit `X-Attestation-Hash` + `X-Letter-Valid-Until` (NOT `X-Valid-Until`). `X-Artifact-Version` is not emitted by any sibling today (verified via grep). `X-Attestation-Id` is emitted only by `/baa-compliance/issue` (one sibling, not universal). **Action:** P-F9 emits `X-Attestation-Hash` + `X-Letter-Valid-Until` minimum (matches the 4 existing siblings); `X-Attestation-Id` if a separate id column is created (recommended for re-fetch UX). DROP `X-Artifact-Version` — if we want a version header, it needs a sibling backfill (header-parity rule, cited in design itself). If the design author wants `X-Artifact-Version`, it must ship in P-F5 + P-F6 + P-F7 + P-F8 + P-F9 simultaneously per `feedback_multi_endpoint_header_parity.md`. Pick lane.

**P0-Coach-2: Sibling-parity 12-property check is informal — needs CI gate.** Design §4 presents a table with 12 properties × 6 artifacts. Today this matrix is hand-maintained prose, not a CI gate. Coach's lesson from Session 220 (commits `39c31ade`, `94339410`, `eea92d6c`) is that diff-scoped reviews miss what's MISSING. Three pre-completion belt-and-suspenders that should be in Phase 2 test list:
1. AST scan that `backend/partner_profitability_packet.py` calls `_sanitize_partner_text(...)` for every brand-text variable that reaches the template — same pattern as `tests/test_partner_brand_text_sanitized.py` (extend if exists).
2. AST scan that the issuance endpoint wraps WeasyPrint in `asyncio.to_thread(...)` — extend `tests/test_pdf_render_asyncio_thread.py` (if exists; create if not).
3. AST scan that no `.format(` call lives in any new `.py` module reachable from the issuance code path — `_AUDITOR_KIT_README` regression class.

**P1-Coach-1: v1→v2 flip path needs an explicit CI gate.** Design §6 says "v1 row stays in place as immutable history; CI gate ensures v1 and v2 versions never co-issue for the same partner at the same `period_end`." That gate is not written. Add to Phase 1 test list:
- `test_no_co_issue_estimated_and_verified_same_period_end`
- `test_kind_column_immutable_after_signature` (Maya P0-Maya-1 also wants this)

**P1-Coach-2: Phase 0 PARTNER_PLAN_CATALOG refactor needs its own micro-gate.** Refactoring a 3rd-consumer-triggers-rule-of-three module is exactly the class of "low-risk plumbing" that ships untested and breaks billing. Phase 0 ships an isolated PR with: import-cycle test + identity test (`PARTNER_PLAN_CATALOG['essentials']['amount_cents'] == 49900`) + a re-run of all `tests/test_billing*.py` + `tests/test_client_signup*.py` BEFORE the P-F9 module imports it.

---

### Lens 5 — OCR / Auditor — **APPROVE-WITH-FIXES**

**P0-OCR-1: 12pt red-rule banner is necessary but NOT sufficient as anti-sales-pitch defense.** Design §3.1 specifies a 12pt minimum red rule "ESTIMATED — INTERNAL USE ONLY" banner on every page. OCR concurs the banner is necessary. **NOT SUFFICIENT** — the Coach-driven adversarial reader is *"Brendan's overseas sales contractor who slices the PDF as a screenshot for a client renewal email."* The banner gets cropped. **Action:** ALSO emboss a 30%-opacity diagonal watermark across the page interior reading `ESTIMATED · INTERNAL USE ONLY · NOT A FINANCIAL STATEMENT`. WeasyPrint supports CSS `background-image` rotation. Watermark survives crops + partial screenshots. The 12pt banner + diagonal watermark together approximate due-diligence-grade "intent of confidentiality" markings.

**P0-OCR-2: Banned-words coverage is incomplete.** Design §5.1 names: `ensures, prevents, protects, guarantees, audit-ready, PHI never leaves, 100%, accurate, precise, exact, certified-net-margin`. OCR adds the financial-projections class:
- `guaranteed margin` — covered as substring of `guarantees`? Tighten the test to substring-match, not word-match.
- `promised profit`, `assured return`, `committed payout`
- `actual` (singular — design has `actual profitability requires…` in the required disclosure copy, so allowlist that specific sentence but ban the bare word elsewhere)
- `net income`, `gross profit` (overloaded GAAP terms — the figures are NEITHER; they're ledger-derived estimates)
- `financial statement`, `audited financials`
- `forecast`, `projection`, `expected return`
- `bottom line`
- `EBITDA` (the figures are not EBITDA, and Brendan's CPA will assume so on sight)

**P1-OCR-1: Footer "issued_by_email" raw-display vs masked.** §3.2.6 prints `issued_by_email` in plain. A leaked PDF identifies the specific partner_user who issued it, which is correct for audit but bad for the "lost laptop" threat model. Recommendation: short-form `issued_by: b****@msp.example` (first char + asterisks + domain), with the full email captured in `admin_audit_log`. Auditor still verifies via portal; leaked-PDF doesn't dox a specific employee.

**P1-OCR-2: `valid_until` semantics for a CFO snapshot.** Design says "30-day" validity ("CFO refreshes monthly"). OCR: 30-day is fine, but be explicit about what `valid_until` MEANS for a profitability snapshot — it's not a legal expiry (like an attestation letter), it's a **freshness ceiling** ("after this date the subscription ledger has drifted enough that the snapshot is stale"). Add a footer line: *"Snapshot freshness: 30 days. After [valid_until], re-issue is recommended to reflect current subscriber state."* Distinguishes from F1/P-F5 where `valid_until` is a legal-attestation expiry.

---

### Lens 6 — PM — **BLOCK on scope estimate**

**P0-PM-1: 3-day estimate is optimistic given the actual scope.** Inventory:
- Phase 0: design (done) + Gate A (in progress) + PARTNER_PLAN_CATALOG refactor (small PR + tests + verify no import cycle) → **0.5 day if cycle is trivial, 1 day if cycle bites.**
- Phase 1: 2 migrations + assumptions endpoint GET/PUT + helper function + tests → **1 day.**
- Phase 2: PDF generator (442 LOC reference shape) + Jinja2 template + 2 endpoints (issue, fetch) + 9 named tests + Gate B → **1.5 days minimum, 2.0 days realistic.**
- **Plus:** Maya's P1-Maya-2 append-only assumptions table + assumptions_id_snapshot FK (~0.3 day if accepted), OCR's P0-OCR-1 diagonal watermark (~0.2 day), Carol's P0-Carol-2 row-number anonymization (~0.2 day), Coach's P0-Coach-1 header-name correction + parity backfill (~0.5 day if header-parity sibling backfill is in scope, ~0.05 day if just align P-F9 to sibling names), Coach's P1-Coach-1 v1/v2 immutability triggers + tests (~0.3 day).

Realistic: **4–5 days**, not 3. PM's recommendation: defer Stripe Connect mention to a one-line §6 stub, ship Maya's append-only + OCR's diagonal watermark + Carol's row-number in v1 since they're cheap and load-bearing, drop §8 Q3 SVG-chart to a v1.1 followup, ship 4 days plus 0.5-day buffer for Gate B fixes.

**P1-PM-1: §8 has 8 user-gate questions. That's too many for a "design closes Phase 0" framing.** A Gate-A-approved design should have ≤2 open questions, not 8. Reduce by pre-deciding the obvious ones:
- Q1: row-number (Carol's P0).
- Q2: pilot one-time excluded (one-time isn't MRR, fold into a "non-MRR revenue" mini-stat in the rollup if user wants visibility).
- Q3: text-table (defer SVG to v1.1).
- Q4: snapshot at issuance (Maya P1-Maya-2 — also helps audit-trail).
- Q5: pre-bake `kind` column (Maya P0-Maya-1).
- Q6: pre-bake 3 wholesale-cost columns (cheap; matches the 3-tier-cost shape; do it now).
- Q7: byte-identical canonical-JSON + semantic-identical PDF (Carol P1-Carol-1).
- Q8: log issuance metadata only (Carol P1-Carol-3).

If all 8 are pre-decided per the lens recommendations above, §8 collapses to 0–1 genuine open questions for user gate.

---

### Lens 7 — Attorney (in-house counsel) — **APPROVE-WITH-FIXES** + **two outside-counsel §-Qs**

**P1-Attorney-1: Banned-words extension for financial-projections.** Counsel concurs with OCR's expanded list (P0-OCR-2). Specifically the Securities-Act-adjacent shape: any time a private company circulates a document that projects future profitability among its own staff, there's a non-zero risk it surfaces in an investor-relations dispute as a "projection" or "forward-looking statement." The "preliminary," "estimated," "operator-supplied" hedging vocabulary in §5.2 is good and should be enforced via a positive-required test (every numeric column header MUST contain one of those words — not just a negative banned-words test).

**P1-Attorney-2: "Subject to revision" disclaimer.** §3.1 banner says *"Figures are preliminary and subject to revision when paid-commission history becomes available."* Counsel suggests strengthening to: *"Figures are preliminary, are based on operator-supplied cost assumptions, and may differ materially from actual results once Stripe Connect reconciliation is integrated. Not a forecast or projection within the meaning of any securities law."* The "Not a forecast or projection" sentence is cheap insurance against the rare case where Brendan's profitability PDF surfaces in an M&A dispute.

**Outside-counsel §-Q-1: Partner-internal estimated financial projections used as input to client renewal pricing.** If Brendan uses P-F9 estimates to decide "we're losing money on tier X, raise renewal price" and an aggrieved customer later subpoenas the document, is OsirisCare exposed for having generated/signed the projection that drove a price-increase decision? The Ed25519 signature establishes provenance — that's normally a feature, but in this scenario it converts "MSP internal spreadsheet" into "OsirisCare-signed document the MSP relied upon." Counsel question: should OsirisCare emboss the canonical-JSON `signed_by` field with `"signed_by": "osiriscare-substrate-system"` plus a disclaimer that *the signature attests integrity of the data shape, NOT financial accuracy of the underlying numbers*? Recommend outside-counsel review before sign-off on the legal language of the signature scope.

**Outside-counsel §-Q-2: §164.504(e) BAA implications.** The PDF contains subscription-ledger-derived counts of clinics that are OsirisCare BAA-bound. Counsel question: does a partner-internal financial document that enumerates BAA-bound clinics by opaque short-id (even without clinic names) constitute disclosure of BA-relationship metadata in a way that affects §164.504(e)? Probably no (the partner already knows their own roster), but the appearance of a per-clinic OsirisCare-signed financial document is a novel artifact class — worth a 1-paragraph outside-counsel write-up. Bundle into Task #37 (counsel-queue bundle, 5 questions in 1 engagement).

---

## Cross-lens findings — severity matrix

### P0 (BLOCKING before any code)
1. **Migration number collision (mig 314).** Steve P0-1. Renumber to 316/317 after verifying 306/311 are free.
2. **`compute_partner_rate_bps()` does not return NULL as designed.** Steve P0-2. Tighten mig 233 (strip the 40% legacy fallback) OR rewrite design §2.4 to acknowledge the fallback behavior.
3. **Header-naming drift.** Coach P0-1. Use `X-Attestation-Hash` + `X-Letter-Valid-Until`; drop `X-Valid-Until`, `X-Artifact-Version`.
4. **PARTNER_PLAN_CATALOG refactor missing import-cycle resolution.** Steve P0-3. Real second consumer is `billing.py` not `partners.py`; cycle constraint cited inline at `partners.py:3296`.
5. **Sibling-endpoint role-gate sweep (RT31 class).** Carol P0-1. Enumerate every endpoint reading `partner_profitability_assumptions`; CI gate the role-gate.
6. **Opaque short-id is a false-sense-of-security shape.** Carol P0-2. Use per-packet randomized row-number.
7. **Mig 315 (renumber to 317) `kind`-immutability trigger.** Maya P0-1. BEFORE UPDATE trigger rejecting `kind`/`attestation_hash`/`ed25519_signature`/`canonical_payload` changes.
8. **Sibling-parity CI gates** for `_sanitize_partner_text` + `asyncio.to_thread` + no-`.format(` (Coach P0-2). Phase 2 test list addition.
9. **Diagonal watermark** (OCR P0-1) in addition to top banner — survives screenshot/crop attack on banner.
10. **Banned-words extension** (OCR P0-2) — financial-projections class missing.

### P1 (must close in same commit, NOT deferred)
11. Mig 235 framing (Steve P1-1).
12. `lifetime_paid_cents` source (Steve P1-2).
13. Mig 317 RLS policies + RESTRICT vs CASCADE (Maya P1-3, P2-1).
14. Append-only assumptions + `assumptions_id_snapshot` FK (Maya P1-2).
15. v1→v2 flip CI gate (Coach P1-1).
16. Phase 0 refactor micro-gate (Coach P1-2).
17. PDF metadata strip (Carol P1-2).
18. Discovery-posture audit-log redaction (Carol P1-3).
19. Determinism asymmetry documented (Carol P1-1).
20. `issued_by_email` masking (OCR P1-1).
21. `valid_until` freshness-ceiling framing (OCR P1-2).
22. Scope estimate 4–5 days realistic (PM P0-1).
23. §8 user-gate questions reduced to ≤1 (PM P1-1).
24. "Not a forecast or projection" disclaimer (Attorney P1-2).
25. Banned-words enforced as positive-required hedging-vocabulary test (Attorney P1-1).

### P2 (followup tasks; carry as named TaskCreate items, can ship post-merge)
26. CHECK constraint sanity-trigger for `essentials ≤ professional ≤ enterprise` cost (Maya P1-1, advisory not mandatory).
27. Outside-counsel §-Q-1 (signature scope w.r.t. financial-accuracy claims) — bundle into Task #37.
28. Outside-counsel §-Q-2 (§164.504(e) implications of per-clinic financial PDF) — bundle into Task #37.

---

## Migration-numbering conflict check — explicit

| Mig # | Status | Reserved by |
|-------|--------|-------------|
| 305 | applied | delegate_signing_key_privileged |
| 306 | **not yet present** | possibly free — verify before claiming |
| 307 | applied | ots_proofs_status_check |
| 308 | applied | l2_escalations_missed |
| 309 | applied | l2_decisions_site_reason_idx |
| 310 | applied | close_l2esc_in_immutable_list |
| 311 | **not yet present** | reserved for Vault P0 mig 311 (Tasks #43–#48: `vault_signing_key_versions`) — `audit/coach-vault-p0-bundle-gate-a-redo-2-2026-05-13.md` confirms |
| 312 | applied | baa_signatures_acknowledgment_only_flag |
| 313 | applied | d1_heartbeat_verification |
| **314** | **CLAIMED by Task #50** | `canonical_metric_samples` per `audit/canonical-metric-drift-invariant-design-2026-05-13.md` §2 + `audit/coach-canonical-compliance-score-drift-v2-gate-a-2026-05-13.md` §"Phase 2a". Counsel Priority #4 (Rule 1). |
| 315 | likely free, but Task #50 may chain Phase 2b here | verify with Task #50 author |
| **316** | **likely free** | candidate for P-F9 `partner_profitability_assumptions` |
| **317** | **likely free** | candidate for P-F9 `partner_profitability_packets` |

**Action:** Before mig 316/317 is claimed in the revised design, grep `audit/` and `.agent/plans/` for "mig 316" / "mig 317" / "migration 316" / "migration 317" to verify no other in-flight design reserved them.

---

## Top 3 P0s before any code lands

1. **Renumber mig 314→316, mig 315→317** (or higher after verifying 311 + 315 reservations). Trivial fix but a hard BLOCK: Task #50 mig 314 is locked. (Steve P0-1)

2. **`compute_partner_rate_bps()` reality reconciliation.** Either (a) strip the hardcoded 40% fallback in mig 233 so the function actually returns NULL as the design claims, OR (b) acknowledge in §2.4 that the platform's hardcoded 40% fallback is what renders for unconfigured partners. Cannot have it both ways — the "no fabricated number EVER" promise is structurally false against the as-shipped SQL. (Steve P0-2)

3. **Carol's row-number anonymization + OCR's diagonal watermark** decided as P0 design before code, not deferred to §8 user-gate. Without these, P-F9 has a credible-deniability gap (short-id prefix re-anchors) AND a screenshot-crop attack on the banner. The artifact's "internal-use-only" posture is load-bearing on these two together. (Carol P0-2 + OCR P0-1)

---

## Final overall verdict — **BLOCK**

P-F9's overall framing is solid — partner-internal, opaque-mode emails, F-series sibling-parity, well-disclaimed, Phase 0 PARTNER_PLAN_CATALOG refactor as a rule-of-three trigger is exactly the right move. But the design ships with three independently sufficient BLOCKS (mig number collision, `compute_partner_rate_bps` reality drift, header-name parity drift) plus 7 further P0s. None require redesign of the artifact's purpose or scope; all require ~0.5 day of design revision and re-issuance for Gate A re-review.

**Author's action items before Gate A re-review:**
1. Renumber migrations (verify 311/315/316/317 reservations).
2. Rewrite §2.4 to match `compute_partner_rate_bps` as-shipped behavior OR commit to tightening mig 233 in Phase 0.
3. Correct §4 sibling-parity header names against `tests/test_artifact_endpoint_header_parity.py`.
4. Lock §8 Q1 + Q4 + Q5 + Q6 + Q7 + Q8 as P0 design (per per-lens recommendations); collapse §8 to ≤1 user-gate question.
5. Specify diagonal watermark + extended banned-words list + positive-required hedging-vocabulary test.
6. Phase 0 PARTNER_PLAN_CATALOG refactor design: pure-constants module + import-cycle CI gate.
7. Acknowledge realistic 4–5 day scope (vs design's claim of 3 days).
8. Bundle outside-counsel §-Q-1 + §-Q-2 into Task #37 (counsel-queue).

**Re-issue:** revised design → Gate A re-review → if APPROVE-WITH-FIXES with all P0s closed, author proceeds to Phase 0 refactor → Gate B at end of Phase 2 BEFORE marking complete (with full pre-push test sweep per CLAUDE.md §"Gate B MUST run the full pre-push test sweep").
