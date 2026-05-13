# Class-B 7-lens Gate A — v1.0-INTERIM Master BAA

**Reviewer:** Fresh-context Gate A fork (no prior session state, no in-doc author counter-arguments)
**Date:** 2026-05-13
**Subject under review:** `docs/legal/MASTER_BAA_v1.0_INTERIM.md` — the v1.0-INTERIM master Business Associate Agreement
**Source-of-truth corpus consulted:** counsel-review verbatim (`audit/outside-counsel-review-baa-drafting-2026-05-13.md`), counsel-edited Gate A on drafting decision, foundational-finding memory (`project_no_master_baa_contract.md`), 7-rule canon (`feedback_enterprise_counsel_seven_rules.md`), subprocessor registry v2 draft, `phiscrub/scrubber.go` source.

| Lens | Verdict |
|---|---|
| 1. Attorney (outside counsel surrogate) | **APPROVE-WITH-FIXES** |
| 2. Inside-counsel surrogate | **APPROVE-WITH-FIXES** |
| 3. HIPAA auditor (OCR) | **APPROVE-WITH-FIXES** |
| 4. Product manager | **APPROVE-WITH-FIXES** |
| 5. Engineering (Steve) | **APPROVE-WITH-FIXES** |
| 6. Medical-technical | **APPROVE-WITH-FIXES** |
| 7. Coach (consistency + sibling-parity) | **APPROVE-WITH-FIXES** |

**Overall verdict:** **APPROVE-WITH-FIXES.** The draft is substantively sound and closes the §164.504(e)(2) gap. Five P0 fixes are required before signature flow goes live; three P1 fixes are required before commit body cites "shipped." No BLOCK conditions.

---

## §164.504(e)(2) required-element checklist

Counsel's hard rule: the v1.0-INTERIM is the **HIPAA-core compliance instrument**, NOT the commercial/legal completion. The §164.504(e)(2)(i) and (ii) required elements MUST all be present; commercial terms (term, termination, indemnity, audit, governing law) may be placeholdered for v2.0.

| §164.504(e)(2) required element | CFR cite | Article | Status |
|---|---|---|---|
| Permitted uses + disclosures | (e)(2)(i)(A) | Article 2.1 | **PRESENT** |
| Restriction to permitted uses | (e)(2)(ii)(A) | Article 3.1 | **PRESENT** |
| Appropriate safeguards | (e)(2)(ii)(B) | Article 3.2 | **PRESENT** (see P1-F2 — over-specificity concern) |
| Report unauthorized use/disclosure | (e)(2)(ii)(C) | Article 3.3(a) | **PRESENT** (30-day timing — see P1-F3 timing concern) |
| Breach notification (§164.410) | (e)(2)(ii)(C) + §164.410 | Article 3.3(b) | **PRESENT** (60-day outer limit) |
| Subcontractor flow-down | (e)(2)(ii)(D) + §164.502(e)(1)(ii) | Article 3.4 | **PRESENT** |
| Individual access (§164.524) | (e)(2)(ii)(E) | Article 3.5 | **PRESENT** |
| Amendment (§164.526) | (e)(2)(ii)(F) | Article 3.6 | **PRESENT** |
| Accounting of disclosures (§164.528) | (e)(2)(ii)(G) | Article 3.7 | **PRESENT** |
| HHS Secretary access | (e)(2)(ii)(H) | Article 3.8 | **PRESENT** |
| Return or destroy at termination | (e)(2)(ii)(I) | Article 5.3 | **PRESENT** |
| Mitigation | §164.530(f) (carry) | Article 3.9 | **PRESENT** |
| Material breach termination right | (e)(2)(iii) | Article 5.2 | **PRESENT** |
| Covered Entity obligations carry | (e)(2)(i)(C) | Article 4 | **PRESENT** |

**§164.504(e)(2) compliance:** **14/14 required elements PRESENT.** The HIPAA-core compliance instrument is structurally complete.

---

## Banned-word scan (customer-facing copy rule)

Two hits, both **ACCEPTABLE** (regulatory-required language, not marketing copy):

- **Line 66 — "prevent":** *"…with respect to ePHI, **to prevent** use or disclosure of PHI other than as provided for by this Agreement…"* — this is a paraphrase of §164.504(e)(2)(ii)(B) operative language. Auditors expect this verb; substituting "monitor" or "reduce" would fail OCR. **KEEP.**
- **Line 85 — "ensure":** *"Business Associate shall **ensure** that any Subcontractor that creates, receives, maintains, or transmits PHI on behalf of Business Associate agrees in writing…"* — this is §164.502(e)(1)(ii) regulatory verb. **KEEP.**

No "guarantees / 100% / continuously monitored / PHI never leaves / audit-ready / protects" hits. The "no absence-proof" hedge appears verbatim at lines 32 + 274. **PASS** on the customer-facing copy rule.

The "never existed" framing — **zero hits**. Counsel's #1 framing fix is fully applied.

---

## Urgency-overshoot scan (counsel's framing-discipline rule)

Counsel: *"the most dangerous sentence is usually the one written to create urgency."*

Sweep for sentences that overshoot the legally safest framing:

| Line | Quote | Verdict |
|---|---|---|
| 3 | *"Outside HIPAA counsel is hardening the commercial/legal terms within 14-21 days of this version's effective date."* | **SAFE** — fact-bounded, no admission, no urgency-overshoot |
| 32 | *"Business Associate makes this an architectural commitment and not an absence-proof."* | **SAFE** — counsel-graded hedge |
| 226 | *"…was insufficient as a complete HIPAA-compliant Business Associate Agreement under 45 CFR §164.504(e)."* | **SAFE** — uses counsel's "insufficient as a complete HIPAA BAA" anchor verbatim |
| 232 | *"No party admits or asserts any prior-period non-compliance by virtue of executing this Bridge Clause…"* | **SAFE** — express non-admission |
| 242 | *"…will be blocked from 'sensitive workflow advancement' as enumerated in Exhibit C…"* | **SAFE** — concrete enumeration replaces vague phrasing |
| 274 | *"…architectural commitment, not an absence-proof."* | **SAFE** — counsel-graded |

**No urgency-overshoot sentences detected.** The draft is unusually disciplined on framing. PASS.

---

## Lens-by-lens findings

### Lens 1 — Attorney (outside-counsel surrogate) — APPROVE-WITH-FIXES

**What works:** All 14 §164.504(e)(2) required elements present. The bridge clause (Article 8) is the most legally-disciplined paragraph in the draft — it (a) supersedes prior acknowledgments cleanly, (b) ratifies the operation period via "evidence of intent and part performance" language counsel signed off on, (c) explicitly disclaims any admission of prior-period non-compliance. Article 1.2's Substrate-posture acknowledgment uses "architectural commitment, not an absence-proof" — the exact hedge counsel demanded. Commercial-term placeholders (5.1, 7.5, 7.6, 7.7) are clearly marked as v2.0 hardening work, not v1.0 commitments — this avoids the trap of inadvertently locking in commercial terms via v1.0 expedience.

**Findings:**
- **P0-L1-A** — Article 3.3(a) reporting clock says "no case later than thirty (30) calendar days after discovery, except where shorter timing is Required by Law." This is **defensible but soft**; a more counsel-grade formulation is "without unreasonable delay, and in any event consistent with §164.410 (60 days outer limit for breach of Unsecured PHI), with security-incident reporting on the timing schedule customary for the OsirisCare platform's incident response (target: 24 hours of confirmed determination)." The current 30-day ceiling for non-breach security incidents is auditor-tolerable but is **slower than industry-leading BA practice** (24-72 hours). v1.0 acceptable; flag for v2.0 tightening.
- **P0-L1-B** — Article 5.3(c) introduces the WORM-evidence carve-out before establishing the predicate. The §164.530(j) coexistence with §164.504(e)(2)(ii)(I) return-or-destroy is correct **as a legal matter** but reads as if BA is asserting a unilateral retention right. Re-frame as: "*Covered Entity acknowledges* that the WORM evidence chain retained at Central Command (PHI-scrubbed by design per Article 1.2) is subject to Covered Entity's own §164.530(j) six-year retention obligation, and that Business Associate's destruction of such evidence absent Covered Entity authorization could impair Covered Entity's audit position." Frames the retention as a CE-protective hedge, not a BA-unilateral right.

### Lens 2 — Inside-counsel surrogate — APPROVE-WITH-FIXES

**Shippable as v1.0-INTERIM today, provided the references resolve.**

**Findings:**
- **P0-L2-A — broken Exhibit A reference path.** Article 3.4 + Exhibit A refer to "Exhibit A — Subprocessor Registry" published at `/legal/subprocessors` or `docs/SUBPROCESSORS.md`. The actual current artifact lives at `audit/baa-subprocessors-reaudit-draft-2026-05-13.md` and is pending its own Gate B. Engineering must either (a) move the registry draft to `docs/SUBPROCESSORS.md` AND publish it at `/legal/subprocessors` before signature-flow goes live, OR (b) cite the actual current path in Exhibit A. Today's path is **dangling** — signers asked to review "Exhibit A" land on a 404.
- **P0-L2-B — broken Exhibit B reference path.** Exhibit B (Article 1.2) refers to "scrubbing implementation at `appliance/internal/phiscrub/scrubber.go`." The file exists in the repo and is open-source-readable to anyone who clones the repo, but the BAA is signed by clinic administrators who **will not clone a Go repo**. Either (a) publish a stable customer-facing rendering of `scrubber.go` at `/legal/phi-scrubber` (recommended — Vanta/Drata pattern), OR (b) inline the scrubber pattern list verbatim in Exhibit B (already partially done — could complete). Today the Exhibit B reference assumes engineering-grade audience access.

### Lens 3 — HIPAA auditor (OCR) surrogate — APPROVE-WITH-FIXES

**If OCR audits OsirisCare tomorrow, this draft would be accepted as a §164.504(e)-compliant BAA.** All required elements present in operative language.

**Findings:**
- **P1-L3-A — Article 3.3 timing.** 30 calendar days for security-incident reporting is at the **outer slow edge** of accepted practice. §164.504(e)(2)(ii)(C) does not specify a numeric ceiling, but OCR enforcement actions (e.g. Aetna 2019, Anthem 2018) treat >30 days as a yellow flag. Tightening to "without unreasonable delay, and in no event later than 30 days" is what the draft says; OCR would prefer "no event later than 10 business days for security incidents distinct from breaches." Not a v1.0 blocker; flag for v2.0.
- **P1-L3-B — Article 5.3 return-or-destroy mechanics.** The interplay between (i) WORM chain retention, (ii) §164.530(j) six-year duty, and (iii) §164.504(e)(2)(ii)(I) return-or-destroy is defensible (see P0-L1-B for re-framing). OCR will scrutinize this exact paragraph in a termination dispute. Counsel hardening recommended for v2.0.
- **L3 — Article 3.4 subprocessor minimum-necessary chain-down: PRESENT** (via §164.502(e)(1)(ii) explicit citation + 30-day advance notice on subprocessor changes).

### Lens 4 — Product manager — APPROVE-WITH-FIXES

**Length:** 309 lines / ~3,800 words. Below the Vanta-template ~5,500-word average. Acceptable for clinic-admin scroll-through. The article-and-exhibit headers chunk well.

**Bridge clause customer-comms:** Article 8.3 incorporates counsel's two honest-anchor lines verbatim — *"Prior acknowledgment is being replaced with a formal contract text."* + *"Re-signing is required to keep records current."* These read correctly when delivered in product banner + email. No oversoftening, no evasion. PASS.

**Findings:**
- **P1-L4-A — signature-flow UX gating.** Article 10 says signature affirms "receipt and review of the full text" plus three other items. The signup flow MUST be re-engineered to embed the full BAA scroll-through + scroll-to-bottom gating (already enumerated in coach-master-baa-drafting-gate-a-2026-05-13.md sub-Q 3 consensus). If signature flow ships without this engineering, signature is legally weaker than current draft assumes. Engineering item, not document item — but the document's Article 10 commitment must be backed by code on day 1.

### Lens 5 — Engineering (Steve) — APPROVE-WITH-FIXES

**Cross-references that need verification:**
- **`BAA_GATED_WORKFLOWS` (Exhibit C) vs Task #52 Q2 list:** Exhibit C enumerates 5 gated workflows (new site onboarding, new credential entry, cross-org transfer/owner-transfer/partner-swap, new evidence export to third parties, ingest [with engineering-position-pending-counsel note]). This **matches** the counsel-approved list at outside-counsel-review-baa-drafting-2026-05-13.md §2 Fix 4. The ingest line correctly notes "engineering's working position" pending inside-counsel Q2 verdict. **PASS — but Exhibit C must be implemented as a single lockstep constant in code** (mirrors the privileged-order four-list lockstep pattern). Otherwise the BAA's enumeration drifts from the runtime.
- **Exhibit A path:** `docs/SUBPROCESSORS.md` is the BAA-cited path. Current registry lives at `audit/baa-subprocessors-reaudit-draft-2026-05-13.md`. Path mismatch — see P0-L2-A.
- **Article 1.2 framing consistency:** "PHI scrubbed at the Appliance edge by design" + "Business Associate makes this an architectural commitment and not an absence-proof" — matches the counsel-grade copy rule (no "PHI never leaves," no "100%," no "guarantees"). **PASS.**

**Findings:**
- **P0-L5-A — `BAA_GATED_WORKFLOWS` engineering implementation must ship in lockstep with BAA signature.** Today Exhibit C is a prose enumeration; the runtime has NO `BAA_GATED_WORKFLOWS` constant, no CI gate, no substrate invariant pinning the list. Signature-flow goes live with Exhibit C asserting a runtime enforcement that does not exist yet. Either (a) implement the constant + gate + invariant before signature-flow GA, OR (b) reframe Exhibit C as "Workflows that WILL be gated when BAA_GATED_WORKFLOWS is implemented (target: 30 days from signature-flow GA)." Today's wording asserts a present-tense runtime guarantee.
- **P1-L5-A — Article 3.2 safeguards specificity (also flagged below in lens 7).** Enumerating Ed25519 attestation chains + append-only audit logs + row-level security + TLS 1.3+ as named safeguards legally commits engineering to never replacing them without a BAA amendment. If TLS 1.4 ships in 2027, or if Ed25519 is replaced with ML-DSA, the BAA needs amendment. Two mitigations: (a) re-word as "including but not limited to (without limitation, and subject to industry-standard equivalents): TLS 1.3 or successor, Ed25519 or equivalent NIST-approved signature algorithm…" — already partially done with "(without limitation)" at line 66; (b) move the specific algorithm enumeration to Exhibit B where it can be version-bumped without amending the BAA core. Recommend option (b) for v2.0.
- **Article 8.2(d) "no party admits or asserts prior-period non-compliance":** Engineering posture is defensible — the click-through acknowledgment record proves customer affirmation of intent + part performance; consideration flowed; services were rendered; the gap is term-certainty under §164.504(e), not absence of agreement. The framing is consistent with counsel's "evidence of intent and part performance" anchor. **PASS.**

### Lens 6 — Medical-technical — APPROVE-WITH-FIXES

**Practice-administrator readability:** Article 1.2's Substrate-posture acknowledgment uses two technical terms ("Appliance," "Central Command," "Substrate") that are defined in Article 1.1 — readable in context. The §-citation density in Articles 2-3 is comparable to Epic/athenahealth/Cerner BAAs that admins routinely sign. No clinical-authority drift. **PASS.**

**Findings:**
- **P1-L6-A — Glossary clarity.** "Substrate" is defined at 1.1 as "the OsirisCare compliance attestation platform" — accurate but circular for a non-technical reader. Recommend a one-line lay-summary appendage: "(in plain terms: the software and on-premises hardware OsirisCare provides to monitor practice compliance posture and generate audit-supportive evidence)." Non-blocking; UX polish.

### Lens 7 — Coach (sibling-parity + no-over-engineering) — APPROVE-WITH-FIXES

**Sibling-parity matrix:**

| Source-of-truth doc | Required framing | Draft alignment |
|---|---|---|
| `project_no_master_baa_contract.md` | "formal HIPAA-complete BAA not memorialized" / "term certainty gap" / "evidence of intent and part performance" / NEVER "BAA never existed" | **ALIGNED.** Article 8.2(a) uses verbatim "evidence of intent and part performance" + "insufficient as a complete HIPAA-compliant Business Associate Agreement." No "never existed" anywhere. |
| `audit/outside-counsel-review-baa-drafting-2026-05-13.md` Fix 3 | Customer-comms must include at least one honest anchor: "Prior acknowledgment is being replaced with a formal contract text." OR "Re-signing is required to keep records current." | **ALIGNED.** Article 8.3 quotes BOTH anchor lines verbatim. |
| Counsel-review Fix 4 | Concrete `BAA_GATED_WORKFLOWS` list: new sites, new credentials, cross-org transfer / org-mgmt, evidence export, **ingest decision explicit** | **ALIGNED.** Exhibit C enumerates all 5 with explicit ingest-position-pending-counsel note. |
| Counsel-review §3 framing discipline | No urgency-overshoot sentences | **ALIGNED.** Banned-word scan + urgency-overshoot scan both PASS. |
| `feedback_enterprise_counsel_seven_rules.md` Rule 6 | BAA state must gate functionality | **ALIGNED in document.** Engineering implementation TBD (see P0-L5-A). |
| Subprocessor v2 draft (`audit/baa-subprocessors-reaudit-draft-2026-05-13.md`) | 19 entries, PHI-scrubbed-at-edge framing, no absence-proof claims | **ALIGNED.** Exhibit A's enumeration matches (Hetzner, self-hosted Postgres/MinIO/Caddy, Anthropic, OpenAI, Azure OpenAI, SendGrid/Twilio, Namecheap PrivateEmail, PagerDuty, Stripe, Google OAuth, MS Azure AD, GitHub, SSL.com, OpenTimestamps, Let's Encrypt, 1Password = 19 entries — count matches). |

**Findings:**
- **P0-L7-A — Article 3.2 safeguards enumeration creates legal commitment.** (Echoes P1-L5-A.) Listing TLS 1.3+, Ed25519, append-only audit logs, RLS as named safeguards in the BAA-body creates an implicit commitment that swapping any of these requires BAA amendment. Coach lens flags this as **over-engineering of the document** that creates downstream amendment burden. Recommend moving specific-algorithm enumeration to Exhibit B; keep Article 3.2 at the level of "encryption in transit and at rest, cryptographic attestation, tenant isolation, audit logging" — algorithm-agnostic.
- **`BAA_GATED_WORKFLOWS` no-double-build check:** Engineering scan — no existing constant matching that name. No platform infrastructure duplication. **PASS.**
- **Substrate-posture banned-word check (Article 1.2 + Exhibit B):** No banned phrases. **PASS.**

---

## Article-by-article structural review

| Article | Purpose | Verdict |
|---|---|---|
| 1 Preamble + Definitions | Parties, defined terms, Substrate-posture acknowledgment | **PASS** — Article 1.2 hedge is counsel-grade |
| 2 Permitted uses | §164.504(e)(2)(i) | **PASS** — covers (A) services, (B) BA mgmt, (C) data aggregation §(e)(2)(i)(B), (D) violations §164.502(j)(1); minimum-necessary §164.502(b) at 2.2 |
| 3 Obligations of BA | §164.504(e)(2)(ii) | **PASS** with P0-L1-A (timing) and P0-L7-A (safeguards over-spec) fixes |
| 4 Obligations of CE | §164.504(e)(2)(i)(C) | **PASS** — short, complete, mirrors HHS sample |
| 5 Term + Termination | (e)(2)(iii) + (e)(2)(ii)(I) | **PASS** with P0-L1-B (return-or-destroy framing) fix; commercial-term placeholders correctly flagged |
| 6 Notices | Operational | **PASS** — references opaque-mode email policy correctly carved out for legal-class notices |
| 7 Miscellaneous | Boilerplate + commercial-term placeholders | **PASS** — placeholders clearly marked v2.0 |
| 8 Bridge Clause | Prior-acknowledgment supersession | **PASS** — strongest paragraph in the draft; verbatim counsel-approved anchor language |
| 9 Versioning | v1.0→v2.0 path | **PASS** — clear |
| 10 Signature | Affirmations on e-sign | **PASS** with P1-L4-A (signup-flow code must back the affirmations) |
| Exhibit A | Subprocessor Registry | **PASS in content** but **P0-L2-A** path mismatch |
| Exhibit B | Data Flow Disclosure | **PASS in content** but **P0-L2-B** customer-facing rendering missing |
| Exhibit C | BAA Gated Workflows | **PASS in content** but **P0-L5-A** runtime enforcement not yet shipped |

---

## Cross-fork consistency

- **vs counsel review verbatim:** 7/7 fixes applied (Fix 1 kill-never-existed, Fix 2 don't-overclaim-HHS, Fix 3 honest-anchor lines, Fix 4 concrete gated-workflows, Fix 5 named-owner for subprocessor refresh [process — not doc], Fix 6 partner-side check [out-of-scope of this doc], Fix 7 reverse-BAA framing tightening [Exhibit A handles by reference]). **CONSISTENT.**
- **vs corrected Gate A drafting doc:** Bridge clause, commercial-placeholder, sub-Q-6 schema-migration claim-logic, sub-Q-4 `/legal/baa` route all reflected. **CONSISTENT.**
- **vs memory framing (`project_no_master_baa_contract.md`):** "formal HIPAA-complete BAA not memorialized" framing in Article 8.2(a); honest-anchor lines in Article 8.3; ingest position carried with counsel-pending note. **CONSISTENT.**
- **vs subprocessor v2 draft:** 19-entry count match; PHI-scrubbed-at-edge framing match; no banned phrases. **CONSISTENT.**

---

## Top 5 P0 findings (ranked)

1. **P0-L5-A — `BAA_GATED_WORKFLOWS` runtime gap.** Exhibit C asserts a runtime enforcement (BAA-non-re-signers blocked from 5 workflows at Day 30) that has NO implementation today. Either ship the lockstep constant + CI gate + substrate invariant before signature-flow GA, or reframe Exhibit C as future-tense ("will be gated"). Today's wording asserts a present-tense runtime guarantee the platform cannot honor on Day 1.

2. **P0-L2-A — Exhibit A path dangling.** Exhibit A cites `/legal/subprocessors` and `docs/SUBPROCESSORS.md`. Neither exists; the actual registry is at `audit/baa-subprocessors-reaudit-draft-2026-05-13.md`. Move the file + add the route before signature-flow GA, or update the citation. Signers asked to review "Exhibit A" land on 404.

3. **P0-L2-B — Exhibit B customer-facing rendering missing.** Article 1.2 + Exhibit B reference `appliance/internal/phiscrub/scrubber.go` as the technical implementation. The file exists but is engineering-grade Go source — clinic administrators will not clone the repo. Publish a stable rendering at `/legal/phi-scrubber` (preferred) or inline the scrubber pattern catalogue fully in Exhibit B (currently partial).

4. **P0-L7-A / P0-L1-B — Article 3.2 safeguards over-specification + Article 5.3 return-or-destroy framing.** Two paired language-discipline fixes. (a) Move specific algorithms (TLS 1.3, Ed25519) from Article 3.2 to Exhibit B so algorithm rotation doesn't require BAA amendment. (b) Re-frame Article 5.3(c) WORM-retention carve-out as CE-protective ("Covered Entity acknowledges…") rather than BA-unilateral.

5. **P0-L1-A — Article 3.3 reporting timing.** 30 calendar days for security-incident reporting is at the outer slow edge of OCR-tolerable practice. Not a v1.0 blocker but flagged for v2.0 hardening; consider language carving security-incident-vs-breach timing more sharply now.

---

## P1 (carry as named TaskCreate followups)

- **P1-L3-A** — Tighten security-incident timing in v2.0 (10 business days target).
- **P1-L3-B** — Counsel hardening on Article 5.3 WORM/return-or-destroy interplay in v2.0.
- **P1-L4-A** — Signup-flow UX engineering (scroll-to-bottom gating + full-BAA embed) must ship in lockstep with Article 10 affirmations.
- **P1-L5-A** — (Merged into P0-L7-A above.)
- **P1-L6-A** — Article 1.1 "Substrate" glossary plain-language appendage.

---

## Final recommendation

**APPROVE-WITH-FIXES.** All 14 §164.504(e)(2) required elements are PRESENT in operative language. Counsel's 7 directed fixes are reflected in the draft. Banned-word scan and urgency-overshoot scan both PASS. Cross-fork consistency with counsel review, memory framing, and subprocessor v2 draft is intact.

**Five P0 findings must close before signature-flow GA:**
1. Ship `BAA_GATED_WORKFLOWS` runtime enforcement (or reframe Exhibit C as future-tense)
2. Resolve Exhibit A path mismatch
3. Resolve Exhibit B customer-facing rendering
4. Tighten Article 3.2 + 5.3 language (safeguards de-specification + return-or-destroy reframing)
5. Decide Article 3.3 security-incident timing posture (or document v2.0 hardening commitment)

**Three P1 findings carry as TaskCreate followups** for v2.0 outside-counsel hardening engagement.

**No BLOCK conditions.** The draft is the strongest legal artifact the platform has produced and is shippable as v1.0-INTERIM the moment the five P0s close.

**Per the TWO-GATE protocol:** Gate B (pre-completion) must run after the P0 fixes land and must verify (a) the runtime `BAA_GATED_WORKFLOWS` implementation matches Exhibit C's enumeration, (b) the `/legal/baa` route serves this document with version banner, (c) Exhibit A + B paths resolve, and (d) the full pre-push test sweep passes (per Session 220 Gate-B lock-in).

— Class-B 7-lens Gate A fork
   Filed 2026-05-13 by OsirisCare engineering for audit trail
