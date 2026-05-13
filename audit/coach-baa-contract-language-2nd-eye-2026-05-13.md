# 2nd-eye adversarial review — BAA contract-language questions for inside counsel
**Reviewer:** Fresh-context fork (Inside-counsel + Attorney + Engineering + HIPAA-auditor + PM + Coach)
**Date:** 2026-05-13
**Source artifacts:** `audit/coach-baa-expiry-enforcement-gate-a-2026-05-13.md` (Class-B 7-lens Gate A, BLOCK), `feedback_round_table_at_gates_enterprise.md` §"Inside-counsel vs outside-counsel routing", `privileged_access_attestation.ALLOWED_EVENTS`, mig 224 / mig 283.

**Verdict per question:**
| # | Question | Verdict |
|---|----------|---------|
| 1 | Grace-period default | **NEEDS-REFRAMING** (sneaks statutory-interpretation; ALSO partially self-answerable via BAA template read) |
| 2 | Sensitive-workflow scope enumeration | **READY-TO-SEND** with one missing item (in-flight order completion) |
| 3 | Notification recipient + timing | **SPLIT** — recipient = inside-counsel; timing/cadence = PM-decided with inside-counsel review |
| 4 | Shadow-mode duration before enforce-flip | **WRONG-ROUTING** — this is engineering + PM, not legal. Inside counsel will return "no opinion." |

**Overall verdict: APPROVE-WITH-FIXES** — the question SET is well-shaped, but Q1 needs reframing, Q3 needs splitting, Q4 should be re-routed back to engineering+PM. Two MISSING questions (Q5, Q6 below) should be co-asked. Recommended packet structure: **single packet, 4 reframed questions** (Q4 removed, Q1 reframed, Q3 split-then-bundled, Q5+Q6 added), sent as Wave 1.

---

## Question 1 — Grace-period default

### Inside-counsel lens
"What grace-period default should BAA-expiry machine-enforcement use" is contract-language-shaped on its face — counsel can read the master BAA template renewal-window clause and answer. **But** the question's option list ("immediate-cutoff, 7-day, 30-day, or read against BAA template's renewal-window language") implicitly asks counsel to ratify an engineering policy default IF the template is silent. That's a hybrid question: contract-read THEN policy-default. Inside counsel will likely return: "the BAA template at clause [X] says [Y]; if [Y] is configurable per signed BAA, default it to the most conservative interpretation." A clean READY answer requires engineering to first attach the master BAA template + the executed BAAs of all current orgs as exhibits, so counsel can verdict against actual contract corpus, not abstract policy.

### Attorney lens (outside-counsel mindset)
This is the question that smells statutory. **§164.504(e)(2)(ii)(A)** requires the BAA to specify the terms under which the BA must return / destroy PHI on termination. A "grace period" during which the BA continues to receive ingest after expiration is functionally an **extension of the BAA**, not a wind-down. If the BAA contains no renewal-window clause AND we apply a 14-day grace by engineering policy, the platform is **operating as a BA without a current BAA** for 14 days — that's an OCR-finding-class exposure. Outside counsel would likely BLOCK and say: "no grace period without contractual basis; either the BAA renews automatically (in which case there's no 'expired' state to grace), or it doesn't (in which case T-0 cutoff is the legal floor)." **This question, despite its contract-language shape, has statutory teeth.** Recommend routing to outside counsel OR explicitly framing as "inside-counsel verdict against the master BAA template, with outside-counsel escalation reserved if template is silent."

### Engineering lens (Steve)
The question gives inside counsel adequate technical context BUT misses two material runtime constraints: (a) the platform supports **per-org `baa_grace_days` configurability** (mig 309 design) — counsel needs to know that, because their verdict shape may be "default X but allow per-org override down to 0"; (b) the platform's daemon-side fallback is **`200 + ingest_paused` body, not 401/403** — counsel needs this because it changes the contract-state characterization (the platform is REFUSING to act as BA, not silently degrading). Both are missing from the engineering framing as written.

### HIPAA-auditor lens
If inside counsel returns "30-day grace" without a contractual basis, an OCR auditor sees the platform continued to receive PHI for 30 days post-expiration with no BAA in force. **The Ed25519-attested `baa_expired_ingest_paused` event must fire at T-0 regardless of grace**, with the grace period reflecting *operator-renewal window*, not *continued-ingest window*. Reframe Q1: distinguish "grace period during which the customer can renew without service interruption" (operator-renewal window, no PHI ingested during this window) vs. "grace period during which ingest continues" (BAA-extension, legally questionable). The current framing conflates them.

### PM lens
SMB dental/medical practice reality: office closed Wed-Fri, BAA-signer is the office manager who's at a kid's soccer game when the T-0 email fires. Customer experience of T-0 cutoff = brand-killing. PM strongly prefers 14-day grace. **But** PM concedes: this is a legal-floor question, not a customer-experience question. If counsel says T-0, PM's job is to build a UX that makes T-0 survivable (T-30 email cadence + portal banner + operator alert), not to negotiate the legal floor down.

### Coach lens
**Sibling parity check:** mig 283 (BAA-relocate-receipt) has NO grace period — receipt is required AT relocate execute time, no grace. The org_deprovisioned event has NO grace — it's instant. Adding a grace period to baa-expiry-ingest-pause creates a 3rd policy shape inconsistent with both siblings. Coach recommends: if counsel answers "T-0 cutoff," sibling parity is preserved. If counsel answers "14-day grace," engineering must document the inconsistency + justify per-state-machine policy variance.

**Anti-double-build:** This question's resolution affects the mig 309 `baa_grace_days INT DEFAULT N` column shape. Asking counsel before the column lands is correct order; asking AFTER would be brownfield.

### Cross-lens verdict
**NEEDS-REFRAMING.** Three fixes:
1. **Split into two sub-questions:** (Q1a) "What does the master BAA template say about renewal window / automatic renewal / termination notice? Attach template + executed BAAs." (Q1b) "If template is silent, what's the legal floor for ingest cessation — T-0 or template-implied window?"
2. **Distinguish renewal-window vs. continued-ingest semantics** — counsel must verdict whether the grace period is "no PHI ingested during grace, operator can renew" or "PHI continues to ingest during grace."
3. **Reserve outside-counsel escalation** if Q1b returns "the template is silent and there is no clear contractual floor" — that crosses into §164.504(e)(2) territory.

---

## Question 2 — Sensitive-workflow scope enumeration

### Inside-counsel lens
This is the cleanest of the 4 — pure contract-language read. Counsel reads the BAA's permitted-uses clause (§164.504(e)(2)(i)) and verdicts whether each enumerated workflow is in scope. Engineering's framing gives counsel adequate context: each workflow is named with a clear technical description. **One ambiguity:** "F-series PDF generation" — counsel needs to know what the PDFs CONTAIN (compliance score, BAA-on-file claim, customer-name, dates) before they can verdict whether issuing them post-expiry is misleading. Engineering should attach example F1/F2/F5 PDFs as exhibits.

### Attorney lens
Counsel will likely verdict using a two-axis test: (axis 1) is the workflow PHI-touching at all? (axis 2) does it produce a customer-facing legal artifact? Owner-transfer + partner-swap are PHI-org-state-touching (axis 1 yes, axis 2 no → "block but not artifact-misleading"); F-series PDFs are axis-1-debatable (the PDFs themselves are PHI-free per the platform's PHI-pre-merge gate) but axis-2 yes (they ARE customer-facing legal artifacts whose validity depends on BAA state). The framing as written supports this two-axis verdict.

### Engineering lens
**Missing workflow #1:** In-flight order completion. The Gate A Q8 fork-resolution said "complete in-flight orders signed under valid BAA." Counsel hasn't verdicted that. Engineering's policy decision (complete in-flight) needs counsel ratification because it's a "PHI-touching action post-expiration" — the chain-of-custody argument (Rule 3) is engineering's defense, but counsel must validate it.

**Missing workflow #2:** Substrate-engine attestation emission. Every 60s the substrate engine emits Ed25519-signed assertion rows. Are those "sensitive workflow advancement" or "self-attestation of platform state"? Engineering's bias is the latter (they're platform-internal evidence, not customer-PHI-touching), but counsel should ratify.

**Missing workflow #3:** Webhook deliveries from the platform back to customer endpoints. If a customer has registered webhooks for compliance events and BAA expires, does the platform still send? (Coach Rule 7 / opaque-mode is the parallel rule, but this is a scope question, not a copy question.)

**Framing concern:** the question's parenthetical "already mig-283-gated at receipt time" for cross-org relocate is good; the parenthetical "probably ALLOW for §164.530(j) wind-down" for auditor-kit is a leading hint that biases counsel toward agreement. Strip the parentheticals and let counsel verdict cleanly.

### HIPAA-auditor lens
The auditor's test: "If the BAA is expired and the platform performs action X, what evidence does the platform present that action X was authorized?" For each enumerated workflow, the auditor wants to see the Ed25519 attestation. The enumeration is good auditor-shape; the only addition needed is: each "BLOCK" verdict must be paired with a `baa_expired_workflow_refused` attestation event so the refusal itself is auditable evidence.

### PM lens
The enumeration is comprehensive and customer-clear. One PM concern: "partner-swap initiation" — if BAA expired, the customer's CURRENT partner can't be replaced, which means the customer is locked-in with a partner they may have lost confidence in. PM recommends counsel consider whether partner-swap-as-renewal-mechanism (customer signing new BAA via new partner) is a permitted escape hatch.

### Coach lens
**Sibling parity:** the enumeration matches the structure of `PRIVILEGED_ORDER_TYPES` lockstep (4-list rule). Coach recommends: whatever counsel verdicts, the result becomes a 5th lockstep list — `BAA_GATED_WORKFLOWS` in a new module, paired with a CI test `test_baa_gated_workflows_lockstep.py` mirroring the privileged-chain lockstep checker. Without that, drift class repeats.

### Cross-lens verdict
**READY-TO-SEND** with three additions:
1. Add in-flight order completion (Engineering missing #1).
2. Add substrate attestation emission (Engineering missing #2).
3. Add webhook deliveries (Engineering missing #3).
4. Strip biasing parentheticals ("probably ALLOW", "already gated").
5. Attach F1/F2/F5 PDF exemplars as exhibits.

---

## Question 3 — Notification recipient + timing

### Inside-counsel lens
The question has TWO sub-questions wearing one trench-coat:
- **3a: Recipient.** "primary contact? BAA signer? both?" — pure contract-language read. The BAA's notification clause (typically §164.314(a)(2)(i)(C)-adjacent) names the recipient. Inside-counsel-grade.
- **3b: Timing.** "T-30, T-7, T-0, or different cadence?" — this is operational-policy + customer-experience, NOT contract-language. The BAA typically specifies "reasonable notice"; the cadence is a PM/UX decision against that floor.
- **3c: Opaque-mode subject.** Internal Rule 7 / RT21 v2.3 already mandates opaque-mode for customer-facing emails. Asking counsel "opaque or transparent" reopens a settled internal rule.

Inside counsel will return: "for 3a, the template says X; for 3b, no opinion (operational); for 3c, internal policy already governs." So the question as written wastes 2 of 3 sub-questions on counsel time.

### Attorney lens
Sub-question 3a is genuinely inside-counsel-grade. **Sub-question 3c (opaque vs. transparent)** has a subtle outside-counsel hook: §164.514(d) minimum-necessary applies to outbound notifications. If a BAA-expiry notice goes to the wrong recipient (e.g. office manager's personal email), it's a §164.402 breach. The opacity rule serves minimum-necessary; counsel should ratify that the opaque-mode satisfies the BAA's notification clause AT ALL (some BAAs require "specific notice of breach / termination" which opaque-mode might fail to satisfy).

### Engineering lens
The framing misses one technical constraint: **emails go through SendGrid, which is a downstream BA.** A BAA-expiry email containing org name + clinic name would be PHI-adjacent identifying data flowing to SendGrid. The opaque-mode is engineering's defense; counsel should know this constraint exists.

Also missing: **the `baa_signer_email` field doesn't exist on `client_orgs` today** — it would need to be added as a mig 309 column. Counsel verdict "notify BAA signer" implies a schema change engineering should flag in the packet.

### HIPAA-auditor lens
Auditor wants notification timing on the audit-log: "T-30 email sent to X@Y at timestamp Z, delivery confirmed." The framing should specify that the notification chain is Ed25519-attested + persisted in `admin_audit_log`. Engineering's framing as written doesn't say that explicitly.

### PM lens
The cadence (T-30, T-14, T-7, T-3, T-1, T-0) is a PM/UX decision. PM doesn't need counsel for cadence; PM needs counsel for "is the cadence floor specified in the BAA?" If yes, PM works against the floor; if no, PM picks the cadence based on customer-experience research. Don't waste counsel time on cadence.

### Coach lens
**Sibling parity:** the platform already has T-N expiry notifications for owner-transfer (mig 273) and partner-admin-transfer (mig 274). Both use a `transfer_expiry_days` configurable column. BAA-expiry notification should use the SAME mechanism (`baa_expiry_notification_days_config` column) not invent a new one. Coach flags this for engineering implementation, not for counsel.

### Cross-lens verdict
**SPLIT.**
- **3a (recipient)** → inside counsel. Pure contract-language.
- **3b (timing/cadence)** → PM-decided with inside-counsel review (cadence floor only, not the cadence itself).
- **3c (opaque-mode satisfaction)** → inside counsel, BUT with the specific question: "does the BAA's notification clause require any specific content that opaque-mode subjects + portal-auth bodies might fail to satisfy?"

Recommended re-framing of Q3 as ONE inside-counsel question: *"(a) Which recipient(s) does the BAA template name for termination/expiry notice? (b) Does the BAA template specify minimum notice timing, and if so what's the floor? (c) Does opaque-mode subject ('Renewal needed by [date]') + portal-auth body satisfy the BAA's notification-content requirement, or does the BAA mandate specific content in the email body itself?"*

---

## Question 4 — Shadow-mode duration before enforce-flip

### Inside-counsel lens
"What's the enterprise-scale precedent for BAA-enforcement shadow-mode duration?" — this is NOT a contract-language question. Inside counsel will return "no opinion; this is an operational rollout decision." The phrasing "Are there counsel-grade reasons to extend (e.g. 60-90 days)?" is engineering looking for a legal hook to justify a longer soak. There ISN'T one — shadow-mode is a software-deployment-risk-mitigation pattern, not a legal-compliance pattern.

### Attorney lens
The one legally-relevant angle: if shadow-mode is too SHORT and the platform flips enforce-mode causing false-positive blocks, customers experience service interruption that COULD be characterized as breach-of-contract under their MSA. But that's a commercial-contract risk, not a HIPAA-BAA risk. Outside counsel would say: "your MSA's uptime SLA is the relevant document; not the BAA." Inside counsel might ratify reading the MSA's uptime clause as a floor, but that's a stretch.

### Engineering lens
Shadow-mode duration is an engineering decision driven by: (a) metric volume needed to characterize false-positive rate; (b) operator-readiness for renewal-workflow drills; (c) customer-readiness for the enforcement (announce → soak → flip). 30 days is engineering's pick per Gate A. PM may want 60. **Counsel adds nothing here.**

### HIPAA-auditor lens
Auditor doesn't care about shadow-mode duration. Auditor cares about: (a) is the enforcement actually in force on the date your control matrix claims it is; (b) is the cutover documented + attested. Shadow-mode is invisible to the auditor as long as the cutover-to-enforce is a discrete attested event.

### PM lens
30 vs 60 vs 90 days is a customer-readiness call. PM should decide based on: how many existing customers have `baa_expiration_date < 60 days from now`? If that number is non-zero, shadow-mode must be ≥ (max-imminent-expiry + 14 days renewal window) to ensure no customer hits enforce-mode before they've had a chance to renew under the new flow. PM owns this calculation.

### Coach lens
**Sibling parity:** Vault Phase C shadow-mode ran ~7 days before Phase C-1 reverse-shadow (Vault primary). Mig 281 dual-admin feature flag uses a `proposed → approved → executed` 24h cooling-off, not a multi-week shadow. There IS no platform-wide shadow-mode-duration convention. Coach recommends: engineering picks a duration, documents the rationale in `audit/baa-enforce-cutover-plan.md`, runs Gate B fork on the cutover plan. Counsel out-of-scope.

**Anti-pattern flag:** asking counsel for shadow-mode duration is the antipattern named in the inside-vs-outside-counsel routing rule — "bundling questions that look jointly consequential but not jointly necessary." Q4 is jointly-consequential (it's in the same project), not jointly-necessary (counsel can verdict Q1-Q3 without it). Including it slows the verdict.

### Cross-lens verdict
**WRONG-ROUTING.** Remove Q4 from the counsel packet entirely. Decide internally: engineering + PM cutover plan with a Gate B fork verdict. If the cutover plan reveals a legal hook (e.g. the cutover itself must be attested in a specific format), file a follow-up inside-counsel question THEN.

---

## Missing questions — what Gate A missed

### Q5 (MISSING — should be in packet) — In-flight order completion under expired BAA

Gate A's Q8 fork-resolution decided "complete in-flight orders signed under valid BAA" based on chain-of-custody-at-emit reasoning. **Counsel never verdicted this.** This is a §164.504(e)(2)(i)(A) permitted-use question: does a BA's authority to act on a previously-permitted disclosure survive BAA termination if the action was initiated under valid BAA? Inside-counsel-grade contract-language read against the BAA's termination clause. Should be bundled with Q2 in the same packet.

### Q6 (MISSING — should be in packet) — Operator-flip restoration legality

Gate A Q6 fork-resolution banned operator-flip-without-signature ("Operator-flip without signature row is BANNED by middleware refusal"). **Counsel never verdicted this.** Question: can the operator administratively bump `baa_expiration_date` on behalf of the customer (e.g. customer phoned in their renewal commitment, paper BAA in mail) without a new `baa_signatures` row? This is contract-language: does the BAA contemplate operator-attested renewal vs. customer-signed renewal? Engineering's policy is "BAN operator-flip" — counsel must ratify or relax. Inside-counsel-grade. Should be bundled.

### Q7 (CANDIDATE — split decision) — BAA-expired vs deprovisioned distinction in compliance_bundles chain

Should BAA-expiry trigger a parallel update to the `compliance_bundles` chain (e.g. a `org_baa_expired` event) parallel to existing `org_deprovisioned`? This is partly engineering-architectural (Gate A Q7 already says yes), partly legal (does counsel want the chain to distinguish "expired-recoverable" vs "deprovisioned-terminal"?). **Recommendation: defer to Gate B on the implementation; do NOT add to the inside-counsel packet.** Engineering has enough signal from Gate A.

---

## Routing classification verification

| Q | Engineering's classification | Fork's verdict | Reasoning |
|---|------------------------------|----------------|-----------|
| Q1 | Inside-counsel | **PARTIALLY WRONG** — Q1a inside-counsel, Q1b reserved outside-counsel if template silent | §164.504(e)(2) statutory teeth on grace-during-which-PHI-ingests; template silence escalates |
| Q2 | Inside-counsel | **CORRECT** | Pure contract-language permitted-use read |
| Q3 | Inside-counsel | **SPLIT** — 3a inside-counsel, 3b PM-decided, 3c inside-counsel with refined framing | Cadence is operational, recipient is contractual |
| Q4 | Inside-counsel | **WRONG-ROUTING** — engineering + PM owns | Shadow-mode is software-deployment, not legal |
| Q5 (new) | n/a | **Inside-counsel** | Contract-language read on termination + chain-of-custody |
| Q6 (new) | n/a | **Inside-counsel** | Contract-language read on operator-attested renewal |

---

## Recommended packet structure

**Single inside-counsel packet, 4 questions, Wave 1 framing** (per counsel's 2026-05-13 packet-wave routing rule — concrete artifact/workflow questions go in Wave 1):

1. **Q1-reframed (grace period):** "(a) What does the master BAA template say about renewal window / automatic renewal / termination notice? Attach: template + executed BAAs for all current orgs. (b) If template is silent, is T-0 cutoff the legal floor under §164.504(e)(2), or is there a contract-implied grace window? (c) During any grace period, does PHI ingest continue (BAA-extension shape) or pause (renewal-window shape)?"

2. **Q2 (sensitive-workflow enumeration):** as drafted + 3 added workflows (in-flight orders, substrate attestation emission, webhook deliveries) + parentheticals stripped + F1/F2/F5 PDF exemplars attached.

3. **Q3-reframed (notification):** "(a) Which recipient(s) does the BAA template name for termination/expiry notice — primary contact, BAA signer, both? (b) Does the BAA template specify a minimum notice timing floor? (c) Does opaque-mode subject ('Renewal needed by [date]') + portal-auth body satisfy the BAA's notification-content requirement?"

4. **Q5 (new — in-flight orders):** "Does a BA's authority to complete a previously-emitted, valid-BAA-signed action survive BAA termination if execution falls in the post-expiration window? Reference our chain-of-custody-at-emit attestation model."

5. **Q6 (new — operator-attested renewal):** "Does the BAA contemplate operator-attested renewal (paper BAA promise, no e-sign yet) as sufficient to bump `baa_expiration_date`, or must every expiration-bump be paired with a new customer-signed BAA record?"

**Q4 removed from packet.** Decide internally via Gate B on cutover plan.

**Reserve clause:** "If any of Q1(b), Q5, or Q6 returns 'template silent / no clear answer,' escalate to outside counsel via Wave 2."

---

## Final overall recommendation

**APPROVE-WITH-FIXES.**

The 4-question packet is well-shaped in spirit and the inside-counsel-grade classification is mostly correct, but as written it has:
- One **statutory-creep** question (Q1) that needs reframing to either stay inside-counsel-shaped OR explicitly reserve outside-counsel escalation.
- One **multi-question-in-one** (Q3) that needs splitting then bundling.
- One **wrong-routing** (Q4) that should be removed from the packet and decided internally.
- Two **missing questions** (Q5 in-flight orders, Q6 operator-attested renewal) that Gate A engineered policy on without counsel ratification.

Apply the 4 fixes above. Final packet: 5 questions (Q1-reframed, Q2-with-additions, Q3-reframed, Q5-new, Q6-new), single Wave 1 inside-counsel engagement. Estimated counsel verdict cycle: 2-5 business days. After verdict, engineering proceeds to mig 309 / middleware / substrate invariant with the Gate A approve-with-fixes design as the build plan.

**Sibling-rule reminder** (Print adversarial reviews verbally rule, 2026-05-13): the user reads transcripts not audit files; the session-message summary MUST surface the routing-classification verdict + the 3 most consequential findings. See final response.
