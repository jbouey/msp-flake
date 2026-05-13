# Class-B 7-lens round-table — counsel-engagement-packet-2026-05-13
**Reviewer:** Fresh-context fork (7 lenses: Legal-internal / Medical-technical / HIPAA-auditor / Attorney / PM / Engineering / Coach)
**Date:** 2026-05-13
**Scope:** Pressure-test the option-A/B/C space across §-Q's 1-5. Copy-level findings already addressed in the prior 2-lens fork (`audit/coach-counsel-packet-review-2026-05-13.md`) are NOT re-litigated — this review focuses on the OPTION SPACE.

**Verdict (per lens):**
- Lens 1 Legal-internal: APPROVE-WITH-FIXES
- Lens 2 Medical-technical: BLOCK
- Lens 3 HIPAA-auditor: BLOCK
- Lens 4 Attorney: APPROVE-WITH-FIXES
- Lens 5 Product manager: APPROVE-WITH-FIXES
- Lens 6 Engineering (Steve): BLOCK
- Lens 7 Coach (consistency / double-build): BLOCK

**Overall:** **BLOCK** — three lenses (medical-technical, HIPAA-auditor, engineering, coach) independently identified the same root cause: **§-Q 5 Option A is a double-build of `org_deprovisioned`**, which already exists as an Ed25519-attested ALLOWED_EVENTS entry (Maya P1-1 closure 2026-05-04). The packet describes the proposed event as if green-field. Counsel sign-off on Option A as currently written commits engineering to building parallel infrastructure that duplicates an extant chain event. This is the bedrock-process failure mode the Coach lens exists to catch.

Five P0s below. None require packet redesign. ~45 min of edits closes them all.

---

## Lens 1 — Legal-internal (Maya + Carol combined)
**Verdict:** APPROVE-WITH-FIXES

The earlier 2-lens fork (`audit/coach-counsel-packet-review-2026-05-13.md`) covered banned-words + voice + §-citation narrowness. Banned-word grep re-run against the current packet text: **zero hits.** The cleaned copy carries through this draft.

**Findings:**

- **P1 — §-Q 5 sub-question (a) line 196 still over-claims posture asymmetrically.** The fix from the prior fork landed ("by design, PHI is scrubbed at appliance egress … we are not claiming an absence-proof") — that lands at line 196 in the current file and reads well. **No action needed; flagged for cross-lens corroboration only.**
- **P1 — §-Q 3 line 134's "platform engineering only" phrasing is precise but it implicitly invites counsel to ask: "why isn't the practice notified?"** That is a real §164.504(e)(2)(ii)(D) tension and counsel WILL ask. Recommend pre-empting in the §-question framing: add one sentence under "Engineering posture today" — "The practice does not see substrate-integrity events today; whether they should is partially what we're asking."
- **P2 — §-Q 4 Option C copy line 178 says "8 disclosures to insurance-underwriter@bigcarrier.com … re-credentialing review."** A concrete fictional example is fine but counsel norm is to anonymize: "Carrier A" / "Carrier B" rather than a fictional address — keeps focus on the legal shape, not the example.

---

## Lens 2 — Medical-technical
**Verdict:** BLOCK

A clinic-operations review of each option-A/B/C asks: does this survive contact with a real practice? Three options fail this test.

**Findings:**

- **P0 — §-Q 2 Option A's 7-day-token model fails the actual former-physician workflow.** Real-world §164.524 requests from former workforce arrive **months** after offboarding (departing physician building a malpractice defense; former PO compelled by state board 14 months later; former employee responding to OCR subpoena). A 7-day token approach means: (1) former workforce member emails the practice owner, (2) owner approves, (3) token mints, (4) former member has 7 days to fetch, (5) if they miss the window they request again. This is fine for a SINGLE request, but the packet describes it as "one 7-day fetch per request satisfies the right" — which is engineering's read of statute, not how §164.524 actually works in practice. OCR's view is the **right is continuing** for the §164.530(j) retention window (6 years). Counsel will likely reject Option A's "one fetch per request" framing as substantively inadequate; engineering needs to soften this to "a 7-day token PER REQUEST, with the practice obliged to honor repeat requests within the retention window" — same engineering, different framing.
- **P0 — §-Q 5 Option A's "OsirisCare Deprovision Notice" letter has a workflow gap: who hands it to whom?** The packet says "letter the practice owner can hand to their successor BA / auditor." Real workflow at practice closure: the OWNER is the one whose access has been revoked. If the practice closed because of owner death, owner incapacity, or owner buy-out — there IS no practice owner to receive the letter. The successor BA scenario assumes a practice-MSP-swap (owner intact, just changing BA); the practice-closure scenario assumes the OWNER is gone. The packet treats these as one workflow. They are not. Counsel will ask: "what's the deprovision flow when the receiving party is the estate executor / successor entity / state regulator? Who authenticates the destruction-attestation receipt?"
- **P1 — §-Q 3 Option A's automated cover sheet is technically possible but operationally questionable in a clinic context.** A real practice receiving an auditor kit with an attached cover sheet saying "this kit was generated during an open §164.504(e)(2)(ii)(D)-eligible event window" needs to know: do they have a notification obligation? When does the clock start? What is "open" — has the substrate resolved the event since the kit was generated? Auto-generation **without an explanation paragraph** is worse than no cover sheet; it creates clinic-side ambiguity. Recommend Option A be conditional on counsel-supplying the cover-sheet copy that includes the disposition framework, not just the status statement.
- **P1 — §-Q 4 Option A's "raw CSV the practice synthesizes" wildly underestimates clinic capability.** A 5-physician practice does not have a privacy-officer in-house who can synthesize a §164.528 accounting from a CSV — they hire OsirisCare/the MSP because they CAN'T. Option A as proposed is "we give them a database export and they figure it out." This will fail at the first request. Either we provide the synthesis OR we provide a template that converts the CSV. Bare CSV is a non-deliverable in primary care.

---

## Lens 3 — HIPAA auditor surrogate
**Verdict:** BLOCK

An external HIPAA auditor evaluating each option asks: "would this satisfy me at an OCR audit?" Two options fail.

**Findings:**

- **P0 — §-Q 5 Option C ("no notice beyond `status='inactive'`") creates a §164.504(e)(2)(ii)(J) audit gap that no OCR auditor would accept.** The BAA termination obligation is explicit: BA must "return or destroy" PHI. A row update from `status='active'` to `status='inactive'` is not destroy-attestation; it's a soft delete with no destruction certification. If the packet presents Option C as a real option, counsel is being given a "legally null" choice that will get checked anyway. Recommend removing Option C entirely OR re-framing it as "Option C — counsel rules deprovision is per-site-status, not per-org-letter" with the same chain-event requirement.
- **P0 — §-Q 4 Option A's CSV-only deliverable will be cited as inadequate in an OCR audit.** §164.528(a)(2) requires the accounting to include 6 specific elements: date, name of recipient, address, brief description of PHI disclosed, brief statement of purpose, plus the alternative formats in §164.528(b). A CSV of audit-log rows does not produce a §164.528-compliant accounting unless the practice manually synthesizes the recipient/purpose for every row — which means the deliverable depends on the practice's own data quality. An auditor will ask: "show me the §164.528 accounting" and a CSV is not one. Option A needs at minimum a synthesis-template Option A+ that maps CSV → §164.528 format.
- **P1 — §-Q 3 sub-question (b) at line 128 — "which classes" — invites a long counsel response that engineering then has to encode.** Auditor surrogate: counsel will write a list of integrity-event classes; engineering then has to keep that list in lockstep across substrate invariants, cover-sheet builder, and auditor-kit rendering. This is a NEW LOCKSTEP LIST not flagged in the packet. Either commit to it explicitly (recommend) OR scope it tighter in the sub-question.
- **P1 — Auditor kit framing already includes the disclaimer "audit-supportive technical evidence; NOT a §164.528 disclosure accounting" — Q4 Option A creates a contradiction.** If OsirisCare provides a `disclosure-accounting/raw` endpoint, the disclaimer that says "this is NOT a §164.528 accounting" must be revised — because the endpoint exists precisely to support that accounting. Counsel will flag this internal inconsistency. Add to sub-questions: "(d) Does the existing 'NOT a §164.528 accounting' disclaimer language need revision if Option A or B is approved?"

---

## Lens 4 — Attorney surrogate
**Verdict:** APPROVE-WITH-FIXES

Outside-counsel reading the packet asks: are the §-questions phrased the way I'd phrase them? Are the options legally severable? Does engineering's framing of "we are NOT asking for a re-design" hold up?

**Findings:**

- **P0 — The "NOT asking for re-design" claim at line 17 is too broad and will not hold up if counsel wants to re-open a 2026-05-06 conclusion.** Specifically: §-Q 4 (disclosure-accounting) touches a domain that v2.3 already covered in the cross-org-relocate context (§164.528 substantive completeness was open §-Q #2 in v2.3). If counsel concludes on §-Q 4 in this packet that path (b) summary is acceptable, but already-shipped artifacts say "NOT a §164.528 accounting," then v2.3's §164.528-related conclusions ARE being revisited de facto. The packet claims they aren't. Counsel will not be bound by engineering's "no re-design" framing — that's a request, not a precommitment. Soften: "We are NOT requesting a re-design but counsel may, in the course of verdicting these items, conclude that the v2.3 framing needs revision."
- **P1 — Severability claim at §8 line 246 is overstated for §-Q 1 + §-Q 4.** §-Q 1 (Tier 2 federation with §164.528 logging) and §-Q 4 (§164.528 accounting path) are NOT legally severable — both ask the same statute (§164.528) to be read in two slightly different ways. If counsel verdicts §-Q 4 path (b) summary is acceptable, that ALSO answers §-Q 1's "per-deployment logging" sub-question. Severability claim should narrow to: "Items 2, 3, 5 are independently severable. Items 1 and 4 share the §164.528 interpretive question and counsel may verdict them jointly."
- **P1 — §-Q 2 sub-question (c) at line 96 — §164.530(j) retention question — is engineering asking counsel to interpret a CE-side obligation, not a BA-side obligation.** Outside HIPAA counsel will respond: "this is the practice's question, not OsirisCare's." Recommend reframing as: "If §164.524 does not attach: is the BA's §164.530(j) obligation to retain its OWN records (which differ from the practice's records) implicated by former-workforce kit access?" — keep the question on OsirisCare's BA-side obligation.

---

## Lens 5 — Product manager
**Verdict:** APPROVE-WITH-FIXES

PM lens: does each option-A/B/C serve the customer story? Does the engineering economics work out?

**Findings:**

- **P0 — §-Q 1 Option C ("never ship Tier 2") destroys 21 days of foundation-slice work + the data-flywheel value proposition.** The flywheel narrative — "L2 patterns from one customer benefit all customers" — is one of three load-bearing claims in customer-facing copy. If counsel picks Option C, the product can survive but the marketing claim needs immediate revision. Packet should warn engineering of the marketing-copy class downstream: line 70 says "Tier 0 + Tier 1 continue as today" — true, but Tier 0 + Tier 1 alone don't support the cross-customer-learning pitch. Recommend Option C be presented softer: "Option C — counsel declines Tier 2. Tier 0 + Tier 1 (intra-org) continue as today; customer-facing copy describing 'cross-customer pattern learning' would be revised."
- **P0 — §-Q 5 Option A's "OsirisCare Deprovision Notice" letter is a NEW F-series artifact slot that competes with the existing F-series ensemble.** F1 (Attestation Letter), P-F6 (BA Compliance Letter), Wall Cert all live at `backend/templates/attestation_letter/letter.html.j2`, `backend/templates/wall_cert/letter.html.j2`. Adding "OsirisCare Deprovision Notice" creates a SIXTH F-series artifact. PM: is this an F-series-worthy customer artifact OR a one-time-event artifact that should live in the auditor-kit-style download path? The packet treats it as F-series; that's a product call, not an engineering call. Should reach PM consensus BEFORE the packet asks counsel to bless the artifact.
- **P1 — §-Q 2 Option A's "former-workforce magic-link" UX puts the practice owner on the hook for approving every former-employee access request indefinitely.** Real practice: 3-physician practice has 4 former employees per year. Over 6 retention years that's 24 potential requesters, each who can request annually. That's potentially 24+ owner-approval requests per year for the practice. This is a workflow tax that needs to be in the proposal so counsel knows what they're approving. Add: "operational note — Option A implies the practice owner approves former-workforce access requests as they arrive, which over a 6-year retention window may accumulate request volume the practice should plan for."
- **P1 — Q3 option-B "manual PO uploads cover sheet" is a regression to pre-substrate workflow.** The whole point of substrate evidence is automation; making the PO upload a cover sheet manually undoes one of the product's promises. PM recommendation: drop Option B or re-frame as a transitional state.

---

## Lens 6 — Engineering (Steve, principal SWE)
**Verdict:** BLOCK

Engineering review of each Option's "shippable today" claim against substrate invariants.

**Findings:**

- **P0 — §-Q 5 Option A double-builds `org_deprovisioned` which ALREADY EXISTS in `ALLOWED_EVENTS` at `privileged_access_attestation.py:156`.** Verified via grep: `org_deprovisioned` is in ALLOWED_EVENTS as of Maya P1-1 closure 2026-05-04; emission sites in `org_management.py:339, 379, 416` already write Ed25519-chained attestations anchored at the client-org's primary site. The packet's Option A proposes "build a new deprovision-notice event in the privileged-access audit log" — this is a duplicate of an extant, in-production event. Engineering should not be asking counsel to bless something engineering already shipped. **Rewrite Option A to reference the extant `org_deprovisioned` event and ask counsel only about the LETTER (the F-series artifact) which is genuinely new.** This is the most consequential finding in the review — counsel will not catch this, but if they sign off on Option A as-written, engineering ships parallel infrastructure.
- **P0 — §-Q 1 Option A's claim of "asymmetric three-list lockstep — ALLOWED_EVENTS only" for `federation_disclosure` is correct AS A PATTERN but the F6 source doc (`f6-phase-2-enforcement-deferred.md` Pre-condition #3 line 68) explicitly says **`federation_disclosure` is targeted at the `promoted_rule_events` three-list lockstep (Assertion + _DISPLAY_METADATA + runbook + CHECK), NOT the privileged_access ALLOWED_EVENTS lockstep.**** Two different lockstep systems. The packet conflates them. Engineering: either the source doc is correct (separate lockstep on promoted_rule_events), or the packet's framing is correct (privileged_access ALLOWED_EVENTS). Pick one BEFORE counsel sees this — the lockstep target is a load-bearing architectural commitment.
- **P0 — §-Q 3 Option A's "cover sheet as sidecar outside determinism contract" claims byte-determinism is preserved via carve-out, but `auditor_kit_zip_primitives._kit_zwrite` (CLAUDE.md auditor-kit determinism contract) is enforced for EVERY entry in the kit ZIP. A sidecar file inside the ZIP that has a wall-clock timestamp BREAKS the contract regardless of whether the cover-sheet content is hashed or not — because the ZIP itself becomes byte-non-deterministic.** The only way Option A preserves byte-determinism is if the sidecar is delivered OUTSIDE the ZIP (separate download). Packet must clarify: is the cover sheet (a) inside the ZIP with a deterministic timestamp derived from event time (preserves determinism) or (b) delivered as a separate file (preserves determinism for the kit only). Current phrasing "sidecar … NOT part of the determinism hash" is wrong — there is no per-file hash; the contract is on the whole ZIP.
- **P1 — §-Q 4 Option A's `/api/client/disclosure-accounting/raw` endpoint description does not include the RLS posture.** Every existing `/api/client/*` endpoint goes through `org_connection` with `tenant_org_isolation` RLS policies (CLAUDE.md "org_connection RLS coverage" rule). The packet treats the endpoint as if it's a new module; in reality it must integrate with the existing RLS infrastructure including the `tenant_org_isolation` policy on any underlying audit-log view. Add: "endpoint would go through `org_connection` + require `tenant_org_isolation` policy on `admin_audit_log` if not already present."
- **P1 — §-Q 2 Option A's "former-workforce personal-access-grants table" needs RLS treatment, retention treatment, and revocation treatment.** The proposal is half a spec. Engineering: this table itself is privileged-access-class (it grants kit reads), so it needs the same auditing as session creation + 5-branch auth. The packet doesn't acknowledge this scope.

---

## Lens 7 — Coach (consistency / no over-engineering / no double-build)
**Verdict:** BLOCK

Coach lens: sibling parity, no double-build, no over-engineering. Two double-build findings.

**Findings:**

- **P0 — §-Q 5 Option A's "OsirisCare Deprovision Notice" letter is a double-build vs F1 Compliance Attestation Letter.** F1 already exists at `backend/templates/attestation_letter/letter.html.j2`, generated by `client_attestation_letter.py`. F1's purpose: "branded PDF the practice owner shows the insurance carrier they can't open." Deprovision Notice's purpose: "letter the practice owner hands to the successor BA / auditor." These are the same pattern — branded PDF, Ed25519-signed, chain-linked, presenter-snapshot. **The F-series should NOT grow a sixth artifact for deprovision; the deprovision letter should be a new MODE of F1 (e.g. `kind='deprovision_notice'`) reusing the template infrastructure.** Engineering proposes new infrastructure; coach: extend F1.
- **P0 — Sibling-parity asymmetry: Option A on §-Q's 1, 3, 5 ALL invoke "new event class in audit log + sidecar artifact + new attestation table" — but §-Q 4 Option A is structurally different (existing audit log + new endpoint, no new event class, no new attestation).** This asymmetry is real (the use cases differ) BUT it reads as if the packet was drafted in isolation per §-Q rather than checking parity. Coach recommendation: add a short "Engineering posture commonality" sub-section at the top noting that §-Q's 1, 3, 5 use the chain-event pattern and §-Q 4 uses a query-endpoint pattern. Helps counsel see what they're collectively approving.
- **P1 — §-Q 3 Option A's "cover-sheet builder" potentially duplicates the existing auditor-kit Jinja2 README template at `backend/templates/auditor_kit/README.md.j2`.** The README already supports `{% raw %}`-fenced templating with StrictUndefined; adding a cover sheet PDF requires a separate render pipeline (HTML→PDF) unless the cover sheet is a Markdown section appended to the existing README. Coach: investigate whether `auditor_kit/cover_sheet.md.j2` rendered into the existing README structure satisfies the requirement before committing engineering to a new PDF generation path.
- **P1 — §-Q 4 Option A's CSV endpoint potentially duplicates the existing `admin_audit_log` raw-export path used in the auditor-kit's identity_chain.json compilation.** Auditor kit already exports audit-log slices; a new "raw CSV" endpoint should consume the same view/query rather than build a new one. Coach: link to the existing audit-log export path in the proposal.
- **P2 — Sub-question lettering parity issue from the prior fork (Carol P1 #5) was that Item 1 had (a)(b)(c)(d) while others had (a)(b)(c). Current packet: Item 1 still has (a)(b)(c) only — fix landed correctly. ✓**

---

## Cross-lens convergence — findings ≥2 lenses flagged

1. **§-Q 5 Option A double-builds `org_deprovisioned` + the F1 letter pattern.** Flagged by Medical-technical (workflow), HIPAA-auditor (Option C inadequacy implies Option A is the real path), Engineering (extant ALLOWED_EVENTS row), Coach (F-series double-build). **Four-lens convergence — highest-priority finding.**
2. **§-Q 4 Option A CSV-only is inadequate.** Flagged by Medical-technical (clinic capability), HIPAA-auditor (§164.528 compliance), Engineering (RLS posture missing), Coach (duplicates audit-log export). **Four-lens convergence.**
3. **§-Q 3 Option A's "determinism preserved via carve-out" claim.** Flagged by Medical-technical (clinic ambiguity if no explanation copy), HIPAA-auditor (lockstep list scope), Engineering (technical inaccuracy of "not part of determinism hash"), Coach (potential template duplication). **Four-lens convergence.**
4. **§-Q 1 Option C destroys marketing claim + lockstep target conflict.** Flagged by Product (marketing copy class), Engineering (lockstep target mismatch with source doc). **Two-lens convergence.**
5. **§-Q 2 Option A 7-day token UX gap.** Flagged by Medical-technical (request cadence), Product (owner workflow tax), Attorney (CE-side §164.530(j) framing). **Three-lens convergence.**

---

## Recommended option-space revisions per §-Q

**§-Q 1 (Tier 2 federation):**
- Three-lens consensus: Reconcile the lockstep-target conflict (privileged_access ALLOWED_EVENTS vs `promoted_rule_events` three-list per f6 source doc) BEFORE counsel sees this — engineering, coach, attorney all touched this. Pick the canonical lockstep target.
- Two-lens consensus: Soften Option C presentation to acknowledge marketing-copy downstream impact.
- No consensus on adding a new option.

**§-Q 2 (§164.524 former-workforce kit access):**
- Three-lens consensus: Reframe Option A's "one fetch per request satisfies the right" — practice obligation is continuing through the §164.530(j) window. Medical-technical, attorney, product all flagged.
- Two-lens consensus (engineering + product): Option A spec needs RLS + retention + revocation expansion; current proposal is half a spec.

**§-Q 3 (cover sheet):**
- Three-lens consensus: Option A "determinism preserved via carve-out" is technically wrong as written. Engineering must clarify: cover sheet is either inside the ZIP with a deterministic timestamp OR outside the ZIP. There is no middle "sidecar inside ZIP, not in determinism hash" path because the contract is on the ZIP, not per-file. Engineering + auditor + medical-technical all flagged adjacent issues.
- Two-lens consensus: Option A should include explanatory copy (not just status statement) — clinic-medical + auditor both flagged.

**§-Q 4 (§164.528 disclosure-accounting):**
- Four-lens consensus: Option A "raw CSV" is inadequate. Add **Option A+** (CSV + synthesis template that maps to §164.528(a)(2) elements) OR retire Option A in favor of Option B+ (OsirisCare produces the accounting via a templated workflow). Medical-technical, auditor, engineering, coach all converged.
- Coach finding: Add disclaimer-revision sub-question — if Option A or B is approved, the "NOT a §164.528 accounting" disclaimer on existing artifacts needs counsel-approved revised copy.

**§-Q 5 (deprovision-notice):**
- Four-lens consensus (highest priority): Rewrite Option A entirely. The `org_deprovisioned` event already exists. The packet should ask counsel about the LETTER, the chain anchor, and the destruction-attestation copy — not the event class. Engineering, coach, auditor, medical-technical all converged.
- Three-lens consensus: Option C is auditor-inadequate; either remove or reframe as "counsel's interpretation question, not engineering's deliverable."
- Medical-technical adds: Option A workflow needs to cover (a) owner-MSP-swap, (b) practice-closure-owner-intact, (c) practice-closure-owner-incapacitated/deceased. Three different recipient flows.

---

## Final overall recommendation

**BLOCK** — do not send the packet to outside counsel until the five P0 findings below are addressed. The fixes are surgical (~45 min total) and prevent two wasted counsel cycles: one on Q5 (rebuilding what we shipped) and one on Q4 (CSV that isn't an accounting).

### Top 5 P0 findings, ranked by remediation urgency

1. **§-Q 5 Option A double-build of `org_deprovisioned`** (Engineering + Coach + Auditor + Medical-technical). The event already exists in ALLOWED_EVENTS, already emits Ed25519-chained attestations from `org_management.py`. Rewrite Option A: "engineering would extend the EXISTING `org_deprovisioned` event with a new F-series mode of the Compliance Attestation Letter generator (reusing F1 template infrastructure) that renders a §164.504(e)(2)(ii)(J)-compliant Deprovision Notice." Ask counsel only about the LETTER content + the chain-anchor question — not about creating the event.

2. **§-Q 4 Option A CSV-only is auditor-inadequate** (Medical-technical + Auditor + Engineering + Coach). Add Option A+ that includes a §164.528(a)(2)-mapped synthesis template, OR retire Option A in favor of an enriched Option B with templated PO-sign-off workflow. Either way, add the disclaimer-revision sub-question.

3. **§-Q 3 Option A determinism-contract claim is technically incorrect** (Engineering + Auditor + Medical-technical + Coach). "Sidecar … not part of the determinism hash" is not how the determinism contract works — `_kit_zwrite` enforces byte-determinism at the ZIP level. Either the cover sheet has a deterministic timestamp (derived from event-time) and is inside the ZIP, or it's delivered separately. Pick one. The current framing will be rejected by Steve at implementation time.

4. **§-Q 1 lockstep-target conflict** (Engineering + Coach). Source doc says `federation_disclosure` belongs to the `promoted_rule_events` three-list lockstep (Assertion + _DISPLAY_METADATA + runbook + CHECK). Packet says it belongs to `privileged_access_attestation.ALLOWED_EVENTS`. These are different lockstep systems. Reconcile BEFORE counsel sign-off so engineering doesn't commit to the wrong substrate-invariant infrastructure.

5. **§-Q 5 Option C is §164.504(e)(2)(ii)(J)-inadequate as written** (Auditor + Medical-technical). "No deprovision notice needed beyond `status='inactive'`" will be rejected by any HIPAA auditor — a status-flag flip is not destruction-attestation. Either remove Option C or reframe it as "counsel rules deprovision is per-site-status under the existing `org_deprovisioned` event, no new letter required."

**Estimated rework effort:** 45 min for the P0 list. Recommend NOT sending until these close.

**Lock-in:** This 7-lens Class-B review is the Gate A artifact for the counsel-engagement packet. A subsequent Gate B fork should re-run after edits, confirming the four-lens convergence findings are closed and the lockstep-target reconciliation is documented.
