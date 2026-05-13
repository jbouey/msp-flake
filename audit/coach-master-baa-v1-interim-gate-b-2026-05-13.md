# Class-B Gate B — Master BAA v1.0-INTERIM (post-P0-fix)

**Reviewer:** Fresh-context Gate B fork (no prior session state, no in-doc author counter-arguments)
**Date:** 2026-05-13
**Subject:** `docs/legal/MASTER_BAA_v1.0_INTERIM.md` after author applied 5 Gate A P0 fixes inline
**Corpus consulted:** Gate A verdict, counsel verbatim review, BAA-drafting Gate A (counsel-edited), `docs/SUBPROCESSORS.md` v2 (now Exhibit A), `appliance/internal/phiscrub/scrubber.go`, counsel's 7-rule canon, `feedback_round_table_at_gates_enterprise.md`.

| Lens | Verdict |
|---|---|
| 1. Attorney (outside-counsel surrogate) | **APPROVE** |
| 2. Inside-counsel surrogate | **APPROVE** |
| 3. HIPAA auditor (OCR surrogate) | **APPROVE** |
| 4. Product manager | **APPROVE** |
| 5. Engineering (Steve) | **APPROVE-WITH-FIXES** (one P1 — file path verification ratchet recommended) |
| 6. Medical-technical | **APPROVE** |
| 7. Coach (consistency + sibling-parity + no over-engineering) | **APPROVE** |

**Overall verdict:** **APPROVE.** All 5 Gate A P0 findings are CLOSED. No regressions. One non-blocking P1 carries to v2.0 hardening. Customer-signup flow may go live the moment route handler ships and re-sign mechanism is wired.

---

## §164.504(e)(2) checklist after P0 fixes (14/14 must remain PRESENT)

P0 #4 moved Article 3.2's algorithm enumeration to Exhibit B. Verified the **safeguards element** (§164.504(e)(2)(ii)(B)) is still present in operative-verb form at Article 3.2 line 66: *"Business Associate shall use appropriate administrative, physical, and technical safeguards, and comply with Subpart C of 45 CFR Part 164 (the HIPAA Security Rule) with respect to ePHI, to prevent use or disclosure of PHI other than as provided for by this Agreement."* Categories listed at high level (algorithm-agnostic). Algorithm specifics now at Exhibit B §B.4. **Element PRESENT — strengthened, not weakened.**

| §164.504(e)(2) element | Article | Status |
|---|---|---|
| Permitted uses + disclosures | 2.1 | PRESENT |
| Restriction to permitted uses | 3.1 | PRESENT |
| Appropriate safeguards | 3.2 + Exhibit B.4 | **PRESENT (P0 #4 re-housed; element intact)** |
| Report unauthorized use/disclosure | 3.3(a) | PRESENT (timing flagged at line 74 with bracketed v2.0 hardening note — P0 #5 closed) |
| Breach notification §164.410 | 3.3(b) | PRESENT |
| Subcontractor flow-down | 3.4 + Exhibit A | PRESENT |
| Individual access §164.524 | 3.5 | PRESENT |
| Amendment §164.526 | 3.6 | PRESENT |
| Accounting of disclosures §164.528 | 3.7 | PRESENT |
| HHS Secretary access | 3.8 | PRESENT |
| Return or destroy at termination | 5.3 | PRESENT (P0 #4 reframing — CE-protective) |
| Mitigation | 3.9 | PRESENT |
| Material breach termination right | 5.2 | PRESENT |
| Covered Entity obligations carry | Article 4 | PRESENT |

**14/14 required elements PRESENT.** §164.504(e)(2) compliance intact post-P0.

---

## Gate A P0 closure matrix

| P0 | Closure status | Evidence (line# in BAA) |
|---|---|---|
| **#1 — Exhibit C reframed FUTURE-TENSE** | **CLOSED** | Line 317 header now reads *"BAA Gated Workflows (engineering commitment, future-tense)"*. Line 319 uses *"will enforce"* (future tense). Five bullets at lines 321-325 each use *"will be blocked"* (future tense). Line 327 explicitly states *"engineering work in progress as of this Agreement's effective date; ship target: prior to the 30-day enforcement cliff"* and *"Until the enforcement mechanism ships, Business Associate manages the transition operationally via in-product banner + email reminder."* Asserts no present-tense runtime guarantee. ✅ |
| **#2 — Exhibit A path corrected** | **CLOSED** | Line 259 cites `docs/SUBPROCESSORS.md`. File now exists at that path (verified via `ls`), dated 2026-05-13, v2.0, 19 entries enumerated. SUBPROCESSORS.md line 6 cross-references back to `docs/legal/MASTER_BAA_v1.0_INTERIM.md` — bidirectional anchor intact. ✅ |
| **#3 — Exhibit B PHI scrubber catalogue INLINED** | **CLOSED** | Lines 271-299 inline a complete table: 12 regex patterns (lines 277-290) + 2 contextual patterns (lines 294-297) + per-row redaction-tag column + `{hash}` derivation explained (line 299). Clinic admins no longer need to clone the Go repo to read the catalogue. Customer-facing self-contained. ✅ |
| **#4 — Article 3.2 specifics MOVED to Exhibit B + Article 5.3(c) reframed CE-protective** | **CLOSED** | (a) Article 3.2 line 66 lists categories only (no TLS version, no Ed25519, no SHA algorithm parameters). Line 68 explicitly punts specifics to Exhibit B and states *"may be rotated or upgraded over time…without requiring amendment of this Agreement."* Exhibit B §B.4 (lines 305-313) houses the specifics. (b) Article 5.3(c) at line 152 now opens with *"Given the OsirisCare Substrate's architectural commitment under Article 1.2, the practical effect of this provision is…"* and ends with *"Business Associate maintains this WORM evidence in service of Covered Entity's retention obligation; Business Associate will not unilaterally delete such evidence prior to Covered Entity's retention period expiring without Covered Entity's written direction."* — frames retention as CE-protective hedge, not BA-unilateral right. ✅ |
| **#5 — Article 3.3 30-day timing FLAGGED for v2.0 hardening** | **CLOSED** | Line 74 carries the bracketed annotation: *"[v2.0 hardening note: the 30-day outer bound is at the slow edge of OCR-tolerable practice. Outside counsel is reviewing whether to tighten this to ten (10) business days in v2.0 commercial/legal hardening per audit-grade norms.]"* Explicit acknowledgment + named v2.0 owner. ✅ |

**5/5 P0 findings CLOSED. Zero regressions.**

---

## Lens 1-7 NEW findings (no Gate A re-litigation)

### Lens 1 — Attorney (outside-counsel surrogate) — APPROVE

Counsel's seven framing fixes survived the P0 application:

| Counsel fix | Status post-P0 |
|---|---|
| Fix 1 — Kill "BAA never existed" framing | **INTACT.** Zero hits anywhere (see scan below). |
| Fix 2 — Don't over-claim HHS sample as 72-hour rescue | **INTACT.** Line 3 explicitly says *"HIPAA-core compliance instrument… NOT the commercial/legal completion."* Line 245 reaffirms. |
| Fix 3 — Honest-anchor lines in customer comms | **INTACT.** Article 8.3 line 233-235 quotes both anchor lines verbatim. |
| Fix 4 — Concrete `BAA_GATED_WORKFLOWS` enumeration | **INTACT.** Exhibit C lines 321-325 enumerate all 5 (now future-tense per P0 #1). |
| Fix 5 — Named-owner for subprocessor refresh | Process commitment — out-of-doc. Out of scope for this gate. |
| Fix 6 — Partner-side BAA truth check | Out-of-scope of this doc (handled separately). |
| Fix 7 — Reverse-BAA framing | Handled by Exhibit A reference structure. **INTACT.** |

Bridge clause Article 8.2(a) line 221 retains the verbatim counsel language: *"evidence of intent and part performance, but was insufficient as a complete HIPAA-compliant Business Associate Agreement under 45 CFR §164.504(e)."* Article 8.2(d) line 227 retains explicit non-admission: *"No party admits or asserts any prior-period non-compliance by virtue of executing this Bridge Clause."* Article 1.2 line 32 retains the counsel-graded "architectural commitment and not an absence-proof" hedge.

**No new attorney findings. APPROVE.**

### Lens 2 — Inside-counsel surrogate — APPROVE

Both broken-reference findings from Gate A are now resolved:
- `docs/SUBPROCESSORS.md` exists at the cited path with v2.0/2026-05-13 frontmatter and 19 entries enumerated.
- Exhibit B's PHI-scrubber catalogue is inlined (lines 271-299) — clinic admins no longer need engineering-grade repo access. Cross-references to `appliance/internal/phiscrub/scrubber.go` at §B.3 are now correctly positioned as *source-of-truth pointer for engineers* rather than the customer's primary reading surface.

**Shippable to customer signup flow today** — pending only the `/legal/baa` route handler (engineering work, not document work).

### Lens 3 — HIPAA auditor (OCR) — APPROVE

P0 #1's future-tense reframing of Exhibit C is **OCR-acceptable**. The risk of a "BA over-promises" finding would arise if the BAA asserted a *present-tense* enforcement that didn't exist at runtime. Future-tense framing tied to an explicit ship target (line 327: *"prior to the 30-day enforcement cliff"*) coupled with an interim operational mechanism (*"in-product banner + email reminder"*) is a counsel-grade two-step: (a) BAA commitment, (b) realistic engineering window. OCR would read this as proactive remediation, not over-promise.

Article 3.2 + Exhibit B.4 separation actually **strengthens** OCR posture: the algorithm-agnostic safeguards-element claim survives algorithm rotation without BAA amendment, which is exactly how OCR expects mature BAs to operate (algorithm cryptography evolves faster than contract paper).

Article 5.3(c) re-framing is OCR-acceptable. The §164.530(j) / §164.504(e)(2)(ii)(I) interplay is now framed as CE-protective ("in service of Covered Entity's retention obligation"; BA "will not unilaterally delete…without Covered Entity's written direction"). Auditor reading: BA holds retained evidence in trust for CE's audit position.

**No new OCR findings. APPROVE.**

### Lens 4 — Product manager — APPROVE

Document is now 347 lines (up from 309). The 38-line growth lives in Exhibit B's inlined scrubber catalogue + Article 5.3(c) reframing. Net readability **improves** — the table at lines 271-299 is more scannable than the prior prose reference to an external file. Clinic admins encountering the BAA in the signup flow now have full self-contained reading.

Article 10 affirmations (line 333-338) still presuppose scroll-to-bottom UX gating in the signup flow. This is engineering work (per Gate A P1-L4-A), not a document gap.

**No new PM findings. APPROVE.**

### Lens 5 — Engineering (Steve) — APPROVE-WITH-FIXES (one P1)

Sub-check (a) — Article 3.2 algorithm-specifics move preserves §164.504(e)(2)(ii)(B): **YES.** Operative verb "shall use appropriate administrative, physical, and technical safeguards, and comply with Subpart C of 45 CFR Part 164 (the HIPAA Security Rule) with respect to ePHI, to prevent use or disclosure of PHI" is intact. Categories listed (Appliance-side scrubbing, encryption in transit, encryption at rest, RLS, append-only audit, attestation chains) are claim-level descriptions; specifics live at Exhibit B.4. This is the textbook split.

Sub-check (b) — Article 5.3(c) WORM-retention reframing is technically accurate vs current substrate behavior: **YES.** Today's substrate emits `org_deprovisioned` chain events (verified — that event class is registered in `privileged_access_attestation.ALLOWED_EVENTS`). Appliance-side wipe receipt is implemented (verified). WORM evidence chain at Central Command is PHI-scrubbed-by-design per Article 1.2. The phrase "OsirisCare maintains this WORM evidence in service of Covered Entity's retention obligation" matches the substrate's actual posture — bundles are tamper-evident (Ed25519 + OTS) and retention is downstream-customer-driven.

Sub-check (c) — Exhibit B §B.3 source-of-truth pointer to `appliance/internal/phiscrub/scrubber.go`: **CORRECT.** File exists at that path (verified via `ls`), 7409 bytes, last modified 2026-03-23. The catalogue inlined at §B.2 is described as a "snapshot of the v1.0-INTERIM-effective implementation" — language matches the engineering-changelog flow.

Sub-check (d) — Exhibit C future-tense framing vs v1.0-INTERIM's 30-day enforcement timeline: **NO CONFLICT.** Article 8.3 line 237 says non-re-signers "will be blocked from sensitive workflow advancement as enumerated in Exhibit C." Exhibit C's future-tense framing for `BAA_GATED_WORKFLOWS` ("will enforce") + ship target ("prior to the 30-day enforcement cliff") + interim mechanism ("in-product banner + email reminder") gives engineering an explicit 30-day window to ship the constant. Document and timeline harmonize.

**P1-GATE-B-S1 (new) — file-path verification CI gate.** The BAA references three repo paths: `docs/SUBPROCESSORS.md` (Exhibit A), `appliance/internal/phiscrub/scrubber.go` (Exhibit B.3), `/legal/baa` (signup-flow target). A CI gate `tests/test_legal_doc_references_resolve.py` should fail if any of these stops existing or moves. Mirror the existing `tests/test_legal_routes_resolve.py` pattern from the BAA-drafting Gate A action items #10. Carry as TaskCreate followup; not a Gate B blocker.

### Lens 6 — Medical-technical — APPROVE

Practice administrator reading the inlined scrubber catalogue at Exhibit B §B.2 — verdict: **useful AND auditor-friendly.** The 14-row table is the right format for explaining to a clinic's external auditor what is protected. Categories map cleanly to the HIPAA Safe-Harbor identifier list (§164.514(b)(2)). Hash-suffix explanation (line 299) is technically accessible without requiring cryptography background.

No glossary-clarity regressions from P0 fixes. Article 1.1 "Substrate" definition (line 27) still slightly circular for non-technical readers (P1-L6-A from Gate A carries forward unchanged — UX polish, non-blocking).

**APPROVE.**

### Lens 7 — Coach (consistency + sibling-parity + no over-engineering) — APPROVE

**Cross-fork parity matrix (post-P0):**

| Sibling artifact | Required alignment | Status |
|---|---|---|
| `docs/SUBPROCESSORS.md` v2 | Effective Date 2026-05-13, 19 entries, classified as Exhibit A | **ALIGNED.** SUBPROCESSORS.md line 5: "Effective Date: 2026-05-13"; line 19: "19 entries"; line 6: "Exhibit A to the OsirisCare Master Business Associate Agreement (docs/legal/MASTER_BAA_v1.0_INTERIM.md)". BAA Exhibit A (line 259) cross-references back. Bidirectional. |
| BAA-drafting Gate A (counsel-edited) | Bridge clause, "evidence of intent and part performance" anchor, honest-anchor lines, concrete `BAA_GATED_WORKFLOWS` enumeration | **ALIGNED.** Article 8.2(a) line 221 + Article 8.3 lines 233-235 + Exhibit C lines 321-325 all preserved. |
| Counsel verbatim review | Seven fixes intact | **ALIGNED.** All 7 verified intact (see Lens 1 table). |
| `feedback_enterprise_counsel_seven_rules.md` | Rule 6 — BAA state gates functionality (not just paperwork) | **ALIGNED in document; future-tense for engineering.** Exhibit C honors Rule 6 framing without overpromising present-tense enforcement. |
| Memory framing (`project_no_master_baa_contract.md`) | "Formal HIPAA-complete BAA not memorialized" / "term certainty gap" / never "BAA never existed" | **ALIGNED.** Zero "never existed" hits. Article 8.2(a) verbatim counsel-anchor. |

**No over-engineering** detected. P0 #4's safeguards-out-of-Article-3.2 move is the opposite of over-engineering — it *reduces* legal amendment burden for routine algorithm rotation. P0 #3's inlined scrubber catalogue is genuinely needed for customer self-contained reading.

**No double-build.** No new constants introduced by P0 fixes; the `BAA_GATED_WORKFLOWS` reference at Exhibit C remains a future engineering commitment, not a present-tense runtime claim.

---

## Banned-word scan

Grep against the v1.0-INTERIM master BAA for: `ensure(s|d|ing)? | prevent(s|ed|ing)? | protect(s|ed|ing)? | guarantee(s|d|ing)? | audit-ready | PHI never leaves | 100% | continuously monitored`. Per the rule, regulation-citation usage is acceptable; marketing-copy usage is not.

| Line | Hit | Verdict |
|---|---|---|
| 21 | "Protected Health Information" (defined term) | **ACCEPTABLE** — defined-term proper noun, mirrors §160.103. |
| 22 | "Electronic Protected Health Information" (defined term) | **ACCEPTABLE** — defined-term proper noun. |
| 66 | "to prevent use or disclosure of PHI" | **ACCEPTABLE** — §164.504(e)(2)(ii)(B) operative-verb paraphrase. OCR expects this verb. |
| 80 | "Business Associate shall ensure that any Subcontractor…" | **ACCEPTABLE** — §164.502(e)(1)(ii) operative verb. OCR expects this verb. |
| 150 | "extend the protections of this Agreement to such PHI" | **ACCEPTABLE** — §164.504(e)(2)(ii)(I) HHS-sample operative language for return-or-destroy infeasibility. |
| 299 | "SHA-256 hash of the matched content" | **ACCEPTABLE** — algorithm description in Exhibit B, not a marketing claim. |

Zero hits on `guarantee(s|d|ing)?`, `audit-ready`, `PHI never leaves`, `100%`, `continuously monitored`. **PASS.**

---

## Urgency-overshoot scan

Counsel's discipline rule: *"the most dangerous sentence is usually the one written to create urgency."*

Sweep for sentences that create urgency at the cost of legally safest framing. None found in the v1.0-INTERIM document. The bracketed v2.0-hardening note at line 74 is the only timing-related sentence and it is a hedge ("outer slow edge of OCR-tolerable practice…outside counsel is reviewing"), not an urgency claim. Article 9.2 line 249 is fact-bounded ("Target effective date for v2.0: 2026-06-03"), not urgency-overshoot. Article 8.3 line 237 ("Customers have thirty (30) days from notice…") is a contractual cure-period statement, not urgency rhetoric.

**PASS.**

---

## "BAA never existed" framing scan

Grep against the BAA for `never existed | did not exist | no BAA | BAA never | absence of a BAA`: **zero hits.** Counsel's Fix 1 remains fully applied. Article 8.2(a) line 221 uses the counsel-approved replacement verbatim: *"evidence of intent and part performance, but was insufficient as a complete HIPAA-compliant Business Associate Agreement under 45 CFR §164.504(e)."*

**PASS.**

---

## Cross-fork consistency

| Cross-fork pair | Check | Status |
|---|---|---|
| BAA Exhibit A line 259 ↔ SUBPROCESSORS.md line 19 | Entry count = 19 | **MATCH.** Both say 19. |
| BAA Exhibit A line 259 ↔ SUBPROCESSORS.md line 5 | Effective Date = 2026-05-13 | **MATCH.** |
| BAA line 1 (version) ↔ SUBPROCESSORS.md line 6 | BAA path = `docs/legal/MASTER_BAA_v1.0_INTERIM.md` | **MATCH.** |
| BAA Exhibit A enumeration ↔ SUBPROCESSORS.md table | Subprocessor list = Hetzner Central + Vault + self-hosted Postgres/MinIO/Caddy + Anthropic + OpenAI + Azure OpenAI + SendGrid/Twilio + Namecheap + PagerDuty + Stripe + Google + Microsoft Azure AD + GitHub + SSL.com + OpenTimestamps + Let's Encrypt + 1Password | **MATCH** (19 entries each, names align). |
| BAA Exhibit B §B.2 line 273 ↔ scrubber.go | Pattern count = 14 (12 regex + 2 contextual) | **MATCH** (gate A independently verified scrubber.go has this shape). |
| BAA Article 8.3 line 233-235 ↔ Counsel review §2 Fix 3 | Honest anchor lines (both) | **VERBATIM MATCH.** |
| BAA Exhibit C lines 321-325 ↔ Counsel review §2 Fix 4 | 5 gated-workflow categories with explicit ingest position | **MATCH** (all 5 categories present; ingest line correctly marked engineering-position-pending-counsel). |

**Full cross-fork consistency. PASS.**

---

## Customer-signup-flow readiness sign-off

**Document layer:** **READY.** The v1.0-INTERIM Master BAA is now signature-flow-ready. All five Gate A P0 findings are CLOSED. Cross-fork consistency with Exhibit A (`docs/SUBPROCESSORS.md`) is intact. Banned-word scan, urgency-overshoot scan, and "never existed" framing scan all PASS.

**Engineering dependencies that remain (NOT Gate B blockers, but signature-flow blockers):**

1. `/legal/baa` route handler must render `docs/legal/MASTER_BAA_v1.0_INTERIM.md` with version banner + PDF download. (BAA-drafting Gate A action item #1.)
2. `SignupBaa.tsx` must embed the full BAA scroll with `scrolledToBottom` gating before the e-sign button enables. (Action item #3 + Gate A Lens 4 P1-L4-A.)
3. Schema migration adding `baa_signatures.is_acknowledgment_only BOOLEAN` + backfill `true` for v1.0 (2026-04-15) rows. (Action item #4.)
4. Claim-logic update across 5 backend files. (Action item #5.)
5. `POST /api/billing/baa/resign` endpoint + UI banner + email template. (Action item #6.)
6. `BAA_GATED_WORKFLOWS` runtime enforcement (lockstep constant + CI gate + substrate invariant) — target Day 30. Exhibit C correctly future-tenses this; engineering window is intact. (Action item from BAA-drafting Gate A.)

**Sign-off:** The BAA document itself may be considered SHIPPED upon (a) the route handler going live serving this exact file content, and (b) the schema migration applying. No additional document-level edits required.

---

## P1 followups (TaskCreate)

- **P1-GATE-B-S1** — CI gate `tests/test_legal_doc_references_resolve.py` verifying the BAA's three repo-path references (`docs/SUBPROCESSORS.md`, `appliance/internal/phiscrub/scrubber.go`, `/legal/baa`) resolve. Mirrors BAA-drafting Gate A action item #10's `test_legal_routes_resolve.py` pattern.
- **P1-L3-A (carried from Gate A)** — Tighten security-incident timing in v2.0 (10 business days target per audit-grade norms). Already flagged at BAA line 74 with bracketed v2.0-hardening note.
- **P1-L3-B (carried from Gate A)** — Counsel hardening on Article 5.3 WORM/return-or-destroy in v2.0.
- **P1-L4-A (carried from Gate A)** — Signup-flow UX engineering must ship in lockstep with Article 10 affirmations.
- **P1-L6-A (carried from Gate A)** — Article 1.1 "Substrate" plain-language appendage (non-blocking UX polish).

---

## Final recommendation

**APPROVE.** All 5 Gate A P0s are closed. No regressions. Document is enterprise-grade and counsel-graded. The one new P1 (file-path CI ratchet) carries as a TaskCreate followup for the next session. Document layer is signature-flow-ready; remaining work is engineering (route handler, signup-flow UX, schema migration, re-sign endpoint, `BAA_GATED_WORKFLOWS` runtime), which is correctly future-tensed in the document itself.

Per Session 220's TWO-GATE protocol: Gate B is **PASSED.** Commit body may cite both Gate A (APPROVE-WITH-FIXES) and Gate B (APPROVE) verdicts.

— Class-B Gate B fresh-context fork
   Filed 2026-05-13 by OsirisCare engineering for audit trail
