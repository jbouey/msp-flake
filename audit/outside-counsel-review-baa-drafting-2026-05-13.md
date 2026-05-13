# Outside Counsel Review — BAA Drafting Gate A Output (2026-05-13)

**From:** Outside HIPAA counsel (gold-grade authority)
**Date:** 2026-05-13
**Subject of review:** `audit/coach-master-baa-drafting-gate-a-2026-05-13.md` — engineering's Class-B 7-lens Gate A on the master BAA drafting decision
**Verdict:** **Approve with fixes**

> *"The strategy is right. The remaining work is mostly language discipline and enforcement specificity, not theory. The file is close. The only thing that could really hurt you is leaving in the overbroad 'BAA never existed' rhetoric when your own better analysis already moved beyond that."*

---

## §1 — What counsel says is right (anchors)

- (b)-hybrid is the only realistic path
- Re-sign all existing signers
- Ship `/legal/baa` + full-text display + claim-logic fix together
- Treat whole-inventory audit as parallel, not optional

## §2 — Seven specific fixes (counsel-directed; non-negotiable)

### Fix 1 — Kill the "BAA never existed" framing

The single biggest internal contradiction. Counsel:

> *"Replace every hard 'no BAA existed' style sentence with 'formal HIPAA-complete BAA not memorialized.' [...] Use [the legally careful] framing everywhere. Do not leave the first framing in as a headline truth. That hands counsel or an adversary a clean quote against you."*

**Corrected vocabulary:**
- ❌ "the contractual instrument does not exist" / "BAA never existed"
- ✅ "formal HIPAA-complete BAA not memorialized"
- ✅ "existing acknowledgment likely constitutes evidence of intent and part performance, but is insufficient as a complete HIPAA BAA"
- ✅ "term certainty gap"

Counsel's most important contradiction to fix:
> *"Early sections say 'BAA never existed.' Later sections say 'some enforceable agreement likely existed, but not a complete HIPAA BAA.' Those cannot both stay. Keep the latter. Kill the former."*

### Fix 2 — Don't over-claim the HHS sample as a 72-hour rescue

> *"Interim BAA = HIPAA-core compliance instrument. Outside counsel hardening = commercial/legal completion. Do not imply the HHS-derived version is 'done,' only that it is the fastest credible stopgap."*

The HHS sample is a model, not a full commercial contract. It omits term, termination, indemnity limits, audit rights. The 72-hour interim landing is the HIPAA-core piece; the 14-day hardening is the commercial/legal completion. Don't conflate them.

### Fix 3 — "BAA refresh" customer-comms needs one honest anchor

Counsel: framing as "BAA upgrade" or "BAA refresh" is the right product move BUT must include an honest anchor line. Without it, the language can look evasive if scrutinized later.

**Counsel-approved anchor lines:**
- "Prior acknowledgment is being replaced with a formal contract text."
- "Re-signing is required to keep records current."

The customer-comms email MUST include at least one of these. Do not oversoften to pure spin.

### Fix 4 — Define "sensitive workflow advancement" concretely

> *"Right now it is directionally correct, but not specific enough. [...] Without that, 'sensitive workflow advancement' is just a nice phrase."*

**Counsel-approved concrete list** for the 30-day post-interim-BAA block on non-re-signed customers:

- ❌ **No new site onboarding** (block)
- ❌ **No new credential entry** (block)
- ❌ **No cross-org transfer / org-management sensitive actions** (block)
- ❌ **No new evidence export to third parties** (block)
- ⚠ **Ingest** — decide explicitly (allow or block — engineering must commit; counsel will not leave this vague)

Note: This list cross-references the Task #52 (Rule 6 BAA-expiry enforcement) inside-counsel packet's Q2 sensitive-workflow enumeration. The two should converge on a single canonical list.

### Fix 5 — Subprocessor list refresh in 72h needs explicit owner

Counsel: the 72h subprocessor refresh is right in principle BUT only works if one named owner is responsible for:
- Real current inventory
- Actual data-flow classification
- Whether BAA is required, not required, or unknown pending review

**Engineering action:** name the owner before the 72h clock starts. Otherwise the "refreshed" exhibit ships still-wrong.

### Fix 6 — Partner-side work is underweighted

> *"60-day partner re-sign may be operationally fine, but be careful: if your customer-facing artifacts or partner roster logic assume partner-side BAA truth, then delaying partner cleanup may leave a second, quieter contradiction alive."*

**Counsel-approved framing:**
- Customer-side BAA remediation FIRST.
- Partner-side representations MUST be checked for outward claims during the transition window. (Partner roster logic; P-F6 BA Compliance Letter "BAA chain on file" claims.)

Don't block customer-side remediation on partner cleanup, BUT do check for outward claims that depend on partner-side BAA truth.

### Fix 7 — "Customer-side reverse BAA" phrasing is muddled

Counsel-approved framing:
- One direct-customer BAA template MAY cover both framings for direct CE relationships
- MSP/subcontractor chain MAY need separate handling OR separate paper depending on structure

Tighten language. Don't leave readers with "is there a distinction or isn't there?"

## §3 — Counsel's overarching guidance

> *"In remediation memos like this, the most dangerous sentence is usually the one written to create urgency. That sentence often overshoots the legally safest framing and becomes the quote everyone regrets later."*

**Discipline rule for all future legal-class documents:** read every sentence that was written to create urgency. Ask: is this the legally safest framing? If the urgency sentence overshoots, soften — even if it weakens the rhetorical impact, the legal-safe framing is what survives review.

## §4 — Engineering action items derived from counsel's review

1. Update `audit/coach-master-baa-drafting-gate-a-2026-05-13.md` — apply Fixes 1, 2, 3, 4, 6, 7 (Fix 5 is a process commitment, not a doc edit).
2. Update memory `project_no_master_baa_contract.md` — replace "never existed" framing with "formal HIPAA-complete BAA not memorialized" + "term certainty gap" everywhere.
3. Update `MEMORY.md` index entry — corrected framing.
4. Define `BAA_GATED_WORKFLOWS` constant for engineering enforcement (Task #52 + Fix 4 — same canonical list).
5. Name the subprocessor-refresh owner before the 72h clock starts (operator action, not engineering).
6. Audit P-F6 BA Compliance Letter + partner_baa_roster references for partner-side BAA truth assumptions (Fix 6).
7. Tighten any future legal-class document for "urgency sentences" per §3 discipline.

---

— Outside HIPAA counsel review
   Filed 2026-05-13 by OsirisCare engineering for audit trail
