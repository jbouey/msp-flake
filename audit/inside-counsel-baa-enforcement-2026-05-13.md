# Inside Counsel — BAA Contract-Language Questions for Verdict (2026-05-13)

**For:** Inside counsel
**From:** OsirisCare engineering, on behalf of the privacy officer
**Date:** 2026-05-13
**Engagement type:** Contract-language reads + operational policy defaults for Counsel Priority #1 (Rule 6: BAA state must gate functionality). **5 questions, 1 packet.** All inside-counsel-grade per the outside-vs-inside routing rule (statutory-interpretation questions reserved for outside counsel; one Q1b escalation clause below). Class-B 7-lens Gate A returned BLOCK on engineering self-decision for these items.

**Companion exhibits (attached):**
1. Master BAA template (current).
2. Executed BAAs for all current orgs (engineering will attach via secure channel).
3. F1 Compliance Attestation Letter exemplar PDF.
4. F2 Privacy Officer Designation exemplar PDF.
5. F5 Wall Certificate exemplar PDF.
6. Gate A fork output (`audit/coach-baa-expiry-enforcement-gate-a-2026-05-13.md`) — for technical context.
7. 2nd-eye fork output (`audit/coach-baa-contract-language-2nd-eye-2026-05-13.md`) — review process artifact.

---

## §0 — Five questions at a glance

| # | Question | Asking inside counsel for |
|---|---|---|
| 1a | Template read: what does the master BAA template say about renewal window / auto-renewal / termination notice? | Contract-language read against attached template + executed BAAs |
| 1b | If template is silent on a renewal window: what is the legal floor for ingest cessation — T-0 (immediate) or template-implied window? **Outside-counsel escalation reserved if template is silent and no clear contractual floor exists** (this would cross into §164.504(e)(2) statutory territory). | Verdict + escalation flag |
| 2 | Sensitive-workflow enumeration — which of these qualify as "sensitive workflow" requiring BLOCK on expired BAA? | Workflow-by-workflow contract-language verdict (the result becomes a CI-enforced lockstep list per Coach sibling-parity rule) |
| 3a | Notification recipient — who receives expiry notifications: primary contact, BAA signer, both, role-defined recipient class? | Contract-language read of BAA notice provisions |
| 3b | Notification cadence — T-30 / T-7 / T-0 timing for the recipient determined in 3a. | PM-decided with inside-counsel review (not pure legal) |
| 3c | Does an **opaque-mode** email (subject: "BAA action required for your OsirisCare account — sign in to view"; body redirects to authenticated portal) satisfy any contractual notice obligation, or must the notice be explicit? | Contract-language verdict per Rule 7 (opaque-by-default) compatibility |
| 5 | In-flight order completion — engineering's current policy is "complete in-flight orders that were attested under a valid BAA via Rule 3 chain-of-custody-at-emit." Does the BAA contemplate that authority surviving termination? | Contract-language verdict on extant-authority semantics |
| 6 | Renewal mechanism — engineering's Gate A position is that BAA renewal requires a **customer-signed** new `baa_signatures` row; operator-attested renewal (the platform marking BAA renewed on behalf of the customer) is banned. Does the BAA template contemplate any operator-attested renewal path (e.g. via partner-as-BA-renewing-on-behalf-of-CE under their own BAA scope)? | Contract-language verdict on permitted renewal mechanisms |

**Note on Q4 from Gate A:** the originally-drafted Q4 (shadow-mode duration before enforce-flip) is **NOT** included here. The 2nd-eye fork classified it as engineering+PM-owned (software-deployment-risk-mitigation, not legal-compliance). Inside counsel adds nothing to that decision. Engineering will resolve shadow-mode duration via internal Gate B on the cutover plan.

---

## Question 1 — Grace-period semantics + legal floor

### 1a — Template read

**Question:** What does the master BAA template (and the executed BAAs of current orgs, attached) say about: (i) renewal window — is there an automatic renewal clause? (ii) termination notice — what notice period does either party owe the other? (iii) post-termination ingest — does the template explicitly forbid, permit, or stay silent on BA continuing to receive PHI after termination effective date?

**Context engineering provides:** The platform supports per-org `baa_grace_days` configurability at the schema level (mig 309 design — not yet shipped). The platform's daemon-side fallback when BAA is expired returns HTTP `200 + {ingest_paused: true}` (not `401`/`403`) so the substrate produces an Ed25519-attested `baa_expired_ingest_paused` event for auditor evidence. Counsel's verdict shapes whether `baa_grace_days` has a hard floor of `0` or a contract-derived non-zero default.

### 1b — Legal floor if template is silent

**Question:** If the template returns "silent" on renewal window and post-termination ingest, what is the legal floor for ingest cessation — T-0 cutoff (the moment `baa_expiration_date < now()`), or is there a template-implied window grounded in standard contractual notice conventions?

**Engineering distinguishes two semantic options for counsel:**
- **Renewal-window grace (PHI-free):** Customer can sign a renewed BAA within N days; during the window, ingest is paused (no PHI flows), customer-portal read-only access continues, customer can renew without operational disruption. Engineering-preferred reading.
- **Continued-ingest grace (legally questionable):** PHI continues to flow during the window. Per the 2nd-eye fork's HIPAA-auditor lens: this is functionally an extension of an expired BAA — the BA is operating without a current BAA. This option is presented only because the original Gate A framing conflated it with the renewal-window grace.

**Outside-counsel escalation reserved:** If template is silent AND counsel reads §164.504(e)(2)(ii)(A) as requiring contractual basis for any post-termination data flow, this crosses into statutory-interpretation territory and engineering will route to outside counsel. Inside counsel: please flag if escalation is needed.

---

## Question 2 — Sensitive-workflow scope enumeration

**Question:** For each workflow below, please verdict whether it must BLOCK on `baa_expiration_date < now()`. The verdict result will be encoded in a new constant `BAA_GATED_WORKFLOWS` paired with a CI lockstep checker (`test_baa_gated_workflows_lockstep.py`) mirroring the existing 4-list privileged-chain lockstep. Whatever counsel verdicts becomes platform-enforced.

| Workflow | Technical description |
|---|---|
| **a.** Daemon checkin (`/api/appliances/checkin`) | Primary ingest path; appliance posts heartbeat + health-check telemetry |
| **b.** Witness submit + fleet-order acknowledgment | Appliance reports completed fleet-order execution back to substrate |
| **c.** Evidence-bundle emission | Substrate writes new Ed25519-signed `compliance_bundles` rows |
| **d.** Privileged-access requests (new attestation chain entries) | Operator-initiated emergency-access / bulk-remediation requests |
| **e.** Cross-org relocate (source-org side) | Already mig-283-gated for receiving-org receipt; this asks about source-org-with-expired-BAA |
| **f.** Partner-swap initiation | Customer changes their partner BA |
| **g.** Owner-transfer initiation | Practice ownership changes to a new owner |
| **h.** New appliance enrollment | First-time appliance install + provisioning |
| **i.** New customer signup | New `client_orgs` row creation under existing partner |
| **j.** Auditor-kit downloads | Practice-owner / partner / auditor pulls Ed25519-attested evidence kit |
| **k.** Client-portal read-only views | Practice-owner reads their own dashboard, compliance scores, evidence history |
| **l.** F-series PDF generation (F1 Attestation Letter / F2 PO Designation / F5 Wall Cert) | Customer-facing PDFs that include compliance-score, BAA-on-file claim, customer-name, dates (exemplars attached) |
| **m.** In-flight order completion — order was attested under valid BAA, customer-BAA expires before completion | See Q5 for engineering's policy framing |
| **n.** Substrate-engine attestation emission (every 60s the substrate writes Ed25519-signed assertion rows) | Platform-internal self-attestation; engineering bias is this should ALLOW (not PHI-touching, not customer-facing) |
| **o.** Webhook deliveries from platform to customer-registered endpoints | If customer registered webhooks for compliance events, does the platform still emit them? |

**Each "BLOCK" verdict** will be paired with an Ed25519-attested `baa_expired_workflow_refused` event in the chain so the refusal itself is auditable evidence. Each "ALLOW" verdict will document the contract-language basis in `BAA_GATED_WORKFLOWS`.

---

## Question 3 — Notification flow

### 3a — Recipient class

**Question:** When BAA approaches expiry, who is the contractually-required notice recipient: (i) the primary contact on the `client_orgs` row, (ii) the named BAA signer on the `baa_signatures` row, (iii) both, (iv) a role-defined recipient class (e.g. "all org admins"), or (v) different recipients for different timing tiers (e.g. primary contact at T-30, BAA signer at T-7)?

### 3b — Cadence

**Question:** What timing cadence: T-30 / T-7 / T-0? PM owns this decision but requests inside-counsel review for contractual-compliance check. Engineering proposed cadence: T-30 first warning, T-7 escalation, T-0 ingest-pause confirmation. Inside counsel: flag if any cadence violates BAA notice provisions.

### 3c — Opaque-mode compatibility

**Question:** Per platform's Rule 7 opaque-by-default posture, the BAA-expiry notification email would have subject "BAA action required for your OsirisCare account — sign in to view" and body redirecting the recipient to the authenticated portal (no org name / customer name / expiry date in the email itself). Does opaque-mode satisfy any contractual notice obligation, or does the BAA require explicit notice (org name, expiry date, BAA effective date) in the email itself?

---

## Question 5 — In-flight order completion

**Engineering's current policy** (Gate A Q8 fork resolution; needs counsel ratification): an order that was emitted under a valid BAA (i.e. `compliance_bundles` row signed pre-expiry) is **allowed to complete** even if BAA expires during the order's lifecycle. The Rule 3 chain-of-custody-at-emit argument: the order was authorized under valid contract at attestation time; revoking authority retroactively violates the append-only chain invariant.

**Question:** Does the BAA contemplate that **extant authority survives termination** for actions emitted under valid contract pre-expiry? Specifically: an Ed25519-signed evidence-bundle / fleet-order / privileged-access attestation that was emitted at T = `expiry - 1 day` and completes at T = `expiry + 2 days` — is that completion within BAA scope or post-BAA exposure?

**Why this matters:** Engineering's bias toward "complete in-flight" is operationally clean (no half-completed-state remediation) but legally requires the BAA to grant extant-authority-survives-termination semantics. If counsel verdicts "no extant authority post-termination," engineering must build in-flight-order-cancellation logic that revokes pending orders at expiry.

---

## Question 6 — Renewal mechanism

**Engineering's Gate A position** (banned by Gate A Q6 without counsel ratification): BAA renewal requires a new **customer-signed** `baa_signatures` row. Operator-attested renewal (the platform OR the partner marking BAA renewed on behalf of the customer) is forbidden per Gate A's privileged-chain-of-custody read.

**Question:** Does the master BAA template contemplate any operator-attested renewal mechanism? Specifically: in a partner-MSP-as-BA arrangement where the partner has its own BAA with the platform AND the customer has a downstream BAA with the partner, can the partner renew the customer's BAA-with-platform on behalf of the customer (within the partner's own BAA scope), or must the customer always sign directly?

**Why this matters:** If counsel verdicts "customer must always sign directly," engineering's Gate A position is ratified. If counsel verdicts "partner-attested renewal permitted under [conditions]," engineering must build a partner-renewal flow with its own attestation chain. The platform-side schema (`baa_signatures` table) supports either pattern; the contract-language verdict shapes which one ships.

---

## Hand-back format — what inside counsel returns

For each question (1a, 1b, 2, 3a, 3b, 3c, 5, 6):

1. **Verdict** — direct answer with citation to BAA template clause or executed-BAA exhibit.
2. **Per-workflow verdicts** for Q2 — workflow-by-workflow BLOCK / ALLOW with contract-language basis.
3. **Escalation flag** — if any question crosses into §164.504(e)(2) statutory territory and requires outside-counsel routing, flag explicitly (Q1b is the most likely candidate).

**Expected timeline:** inside-counsel cycle time per the routing rule. Engineering is blocked on Task #52 design until verdicts return.

**Sibling-parity engineering commitment** (post-verdict): whatever Q2 verdicts, the result becomes a new lockstep list `BAA_GATED_WORKFLOWS` with CI gate `test_baa_gated_workflows_lockstep.py` mirroring the existing 4-list privileged-chain lockstep. Without that, BAA-state drift recurs.

---

— OsirisCare engineering
   on behalf of the privacy officer
   2026-05-13
