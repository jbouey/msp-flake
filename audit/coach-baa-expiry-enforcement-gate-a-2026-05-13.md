# Class-B 7-lens Gate A — BAA-expiry machine-enforcement (Task #52, Counsel Priority #1, Rule 6)

**Reviewer:** Fresh-context Gate A fork (Class-B 7-lens — per `feedback_round_table_at_gates_enterprise.md`)
**Date:** 2026-05-13
**Scope:** Design-stage adversarial review BEFORE any migration / middleware / invariant lands. Pressure-tests the 10 open design questions counsel left unanswered.
**Discovery anchors (verified before review):**
- `baa_expiration_date DATE` exists on `client_orgs` (mig 146, 2026-03). Only consumer today: `prometheus_metrics.py` exposes T-30 expiring + expired counters. **Zero functional enforcement anywhere in the platform.**
- `baa_signatures` table (mig 224) is append-only, email-keyed, FK'd from `signup_sessions.baa_signature_id` and `client_orgs.baa_relocate_receipt_signature_id`.
- `org_deprovisioned` is a privileged-chain event (set in `privileged_access_attestation.ALLOWED_EVENTS`); deprovision flow flips `deprovisioned_at` + emits Ed25519-signed attestation (`org_management.py:339-416`).
- ~70 substrate invariants in `assertions.py`; one BAA-related (`cross_org_relocate_baa_receipt_unauthorized`, mig 283).
- ~114 endpoints behind `require_appliance_bearer`. Primary ingest paths: `agent_api.py:362,3036` (checkin), routes.py:6860 (site provision), iso_ca.py:301 (claim-v2), partners.py:2794 (provisions).
- `compliance_score.compute_compliance_score()` is the canonical helper (4 callers); no BAA dimension today.

**Verdict (per lens):**
| Lens | Verdict |
|------|---------|
| 1. Legal-internal (Maya + Carol) | APPROVE-WITH-FIXES |
| 2. Medical-technical | APPROVE-WITH-FIXES |
| 3. HIPAA-auditor | BLOCK |
| 4. Attorney | APPROVE-WITH-FIXES |
| 5. Product manager | BLOCK |
| 6. Engineering (Steve) | APPROVE-WITH-FIXES |
| 7. Coach | BLOCK |

**Overall: BLOCK** — three lenses (Auditor / PM / Coach) BLOCK on independent P0s that are not addressable in implementation alone. The user (operator) + counsel MUST verdict on 4 questions before this design can be approved-to-build (see "Open questions for user-gate" §).

---

## Counsel-rule binding

Rule 6 (counsel verbatim, 2026-05-13):
> "BAA state must gate functionality, not just paperwork. No receiving-org behavior without explicit BAA / eligibility satisfaction. Expired BAA must block new ingest or sensitive workflow advancement. Legal status should be machine-enforced where possible, not operator-remembered. At enterprise scale, 'we assumed the paper was handled' is not good enough."

Counsel's priority ordering (2026-05-13 followup): Rule 6 is **#1 of 5**. Rule 6 is positioned as the platform's chief maturity-attack surface — Rules 3 (chain of custody), 9 (provenance), 10 (no clinical drift) are bedrock-strong; Rule 6 is the soft underbelly auditors will target. The fork agrees with that framing.

Sister-rule context the fork takes into account:
- **Rule 3 (chain of custody)** — privileged-chain events MUST be Ed25519-signed + hash-chained. Any state change that "BAA is expired → block ingest" is itself a privileged-class transition and must be attested.
- **Rule 7 (notification)** — expiry notice to practice owner is the customer-facing channel. Opaque-mode email rule applies (`feedback_email_opacity_harmonized.md` precedent — RT21 v2.3).
- **Rule 1 (canonical-source)** — if BAA state becomes a compliance dimension, `compute_compliance_score()` must own it or a parallel canonical helper must exist.

---

## Lens 1 — Legal-internal (Maya + Carol)

**Verdict: APPROVE-WITH-FIXES**

### Findings
1. **(P0-L1.a) Banned-word risk in customer-facing block copy.** Any "your BAA has expired, your appliance is now blocked" email MUST use the §164.504(e)-narrow language pattern from RT21 v2.3. Forbidden words from the legal-language rule: `ensures`, `prevents`, `protects`, `guarantees`, `audit-ready`, `PHI never leaves`, `100%`. Acceptable shape: *"Service to your organization is paused pending renewal of your Business Associate Agreement. Sign in to the portal for details."* Subject line MUST be a static string literal (no f-string interpolation per `test_email_opacity_harmonized.py`).
2. **(P0-L1.b) "Sensitive workflow" is undefined.** Counsel said "block new ingest or sensitive workflow advancement" but didn't enumerate. The fork's recommended enumeration (see Design Decisions Q1) MUST be reviewed by Maya before implementation lands — this is a §164.504(e)(2)(ii) permitted-use boundary call.
3. **(P1-L1.c) §164.530(j) wind-down read access is a legal floor.** Even a fully-expired BAA org retains, by HIPAA regulation, a right to access its own PHI for the wind-down period. Auditor-kit downloads + client-portal read-only views MUST stay accessible even when ingest blocks. The fork's design enforces this; flag for Maya confirmation.
4. **(P1-L1.d) BAA-expired ≠ deprovisioned.** Org_deprovisioned is a one-way Ed25519-signed termination. BAA-expired is a recoverable suspension. Conflating them in copy or in state-machine wording is a legal-grade error. Use distinct event names: `baa_expired_ingest_paused` vs. existing `org_deprovisioned`.
5. **(P1-L1.e) Renewal-flip audit trail.** When the operator (or customer via signed addendum) advances `baa_expiration_date`, that mutation MUST land in `admin_audit_log` AND be Ed25519-attested if it transitions the org from `paused → active`. The fork classifies this as a privileged-class transition (see Lens 6 / Q3).

---

## Lens 2 — Medical-technical

**Verdict: APPROVE-WITH-FIXES**

### Findings
1. **(P0-L2.a) Hard-cutoff at T-0 is clinically unsafe.** A practice losing appliance ingest mid-shift means a clinic at 2pm Tuesday loses drift detection, security-event ingest, and remediation acks until a partner-coordinated BAA renewal happens. For SMB dental/medical practices where the office manager IS the BAA-signer AND the practice is closed Wednesday-Friday, T-0 cutoff = 72hr blind window during which a ransomware event lands undetected. **Recommend 14-day grace** (block at T+14, with daily-escalating warnings T-30, T-14, T-7, T-3, T-1, T-0, then block).
2. **(P0-L2.b) Auditor-kit MUST remain downloadable.** §164.530(j) wind-down — agreed with Lens 1.
3. **(P1-L2.c) Read-only client portal stays open.** A practice mid-renewal needs to see what they're renewing. Blocking the portal is a UX dead-end.
4. **(P1-L2.d) Operator notification is essential.** The MSP-operator must be alerted T-30 *separately* from the customer notification, because the MSP holds the contract. The customer may have forgotten; the operator is the contractual party.
5. **(P2-L2.e) In-flight order completion.** A fleet-order signed under a valid BAA but executing during the expired window — Lens 2 says complete it (chain of custody was satisfied at emit). Don't break in-flight remediation.

---

## Lens 3 — HIPAA-auditor

**Verdict: BLOCK**

### Findings
1. **(P0-L3.a) BLOCKER — "block at appliance checkin" is auditor-attackable.** If the appliance can't checkin during expired-BAA, then **no evidence is being produced**, which means **the auditor cannot prove the controls were operating**. An auditor sees a 30-day gap in `compliance_bundles` and asks: "Was the practice running uncontrolled during this window, or was the platform refusing to attest?" Answer must be *attestably distinguishable*. Recommendation: appliance checkin returns `200` with body `{"status": "baa_expired", "ingest_paused": true, "renewal_url": "..."}` — appliance LOGS the pause (locally signed evidence of the gap is itself audit trail), no new compliance_bundles created, but the gap is *attestable* not silent. Coupled with substrate invariant + admin_audit_log row, the chain explains the gap.
2. **(P0-L3.b) BLOCKER — F-series PDF artifacts MUST refuse generation.** Attestation Letter (F1), BA Compliance Letter (F2), Wall Cert (F5) — if any of these render while BAA is expired, the customer holds a misleading legal artifact. Refuse generation; auditor would catch this in 5 seconds. The `client_attestation_letter.py` flow already checks `baa_signatures` row existence — extend to check `baa_expiration_date >= today()`.
3. **(P0-L3.c) BLOCKER — compliance score MUST reflect BAA state.** Per Rule 1 canonical-source rule, if `baa_expiration_date < now()` the platform's customer-facing compliance score is a lie of omission. Auditor view: "your platform reported 97% compliance for an org with an expired BAA — what controls were you measuring?" The score helper (`compliance_score.compute_compliance_score()`) MUST take a `baa_state` input or refuse to compute and return a sentinel.
4. **(P1-L3.d) Acceptable enforcement evidence is the audit log + Ed25519 attestation.** An auditor will accept "BAA state expires on date X, system emitted Ed25519-signed `baa_expired_ingest_paused` event at time X, admin_audit_log row shows the cutover" as evidence of automated control. **Without the Ed25519 attestation, an auditor sees a code-only enforcement that could be patched out** — this needs the privileged-chain treatment.
5. **(P1-L3.e) "Quietly expired and quietly unblocked" is a finding.** If the operator renews the BAA + bumps `baa_expiration_date` and the system silently un-pauses, that's an undocumented control transition. Pairs with Lens 1.e.

---

## Lens 4 — Attorney

**Verdict: APPROVE-WITH-FIXES**

### Findings
1. **(P0-L4.a) The grace period is a contract-law question, not an engineering one.** 14 days (Lens 2's pick) is a defensible commercial-grade default but the contractual answer is **whatever the BAA itself says**. Some BAAs specify "automatic renewal unless 30 days written notice"; others "BAA terminates on the expiration date listed". The grace period MUST be configurable per-org (`client_orgs.baa_grace_days INT DEFAULT 14`) and the default MUST be reviewed by outside counsel against the master BAA template.
2. **(P0-L4.b) "Sensitive workflow" must be enumerated in the BAA itself.** The fork can't pick what's "sensitive" — counsel's contract defines it. The fork's *engineering recommendation* (Q1 below) is a candidate enumeration that goes to counsel for verdict.
3. **(P1-L4.c) Restoration via signed addendum vs. operator-flip.** Two restoration paths: (1) customer e-signs a renewal BAA → new `baa_signatures` row + auto-flip; (2) operator manually flips `baa_expiration_date` without a new signature row. Path 2 is legally weaker (paper-handling-by-trust) — recommend BLOCK it unless `baa_signatures` row exists with `baa_signed_at` newer than current `baa_expiration_date`.
4. **(P1-L4.d) Notification recipients require explicit BAA disclosure.** The BAA's notification section governs who gets the T-30 email — the org's `primary_contact_email`, the org's `baa_signer_email` (which may differ — the office manager signed; the practice owner runs the org). Counsel verdict required: do BOTH receive, or only the BAA signer?
5. **(P2-L4.e) Cross-org relocate already has receipt-side BAA gating (mig 283). Source-org BAA-expiry was not contemplated.** A source org with expired BAA should NOT be able to initiate a cross-org relocate (the data is moving OUT, but the source is no longer authorized to direct movement). Block source-org relocate if `baa_expiration_date < now()`.

---

## Lens 5 — Product manager

**Verdict: BLOCK**

### Findings
1. **(P0-L5.a) BLOCKER — false-positive risk on the day-of-expiry is enormous.** Today's prod customer `north_valley` has `baa_expiration_date` set; if the operator manages 30+ practices and one expires Sunday at midnight, the operator wakes Monday to 30 phone calls. The customer experience of "your appliance was offline all weekend because of paperwork" is brand-killing. **No deploy of this enforcement until (a) operator has a tested renewal workflow, (b) every org's `baa_expiration_date` is verified-correct, (c) shadow-mode runs for ≥30 days emitting alerts only.**
2. **(P0-L5.b) BLOCKER — operator-facing restoration UX must exist BEFORE block-mode ships.** Today there's no `/api/admin/orgs/{id}/renew-baa` endpoint. Without it, an emergency renewal requires direct DB UPDATE, which is a 3am-pager horror story.
3. **(P0-L5.c) BLOCKER — customer-facing restoration UX requires the e-sign flow to support renewals.** Today `client_signup.py` handles first-time BAA sign. A renewal flow that produces a new `baa_signatures` row + auto-advances `baa_expiration_date` doesn't exist. Building enforcement without renewal-flow is shipping the trap without the escape hatch.
4. **(P1-L5.d) The notification copy must NOT be alarmist.** "URGENT: SERVICE TERMINATED" subject lines on a soft-pause are a brand-killer. Lean on "Renewal needed by [date]" copy with the §164.530(j) wind-down read access explicitly called out.
5. **(P1-L5.e) Carry-over state.** A customer who renews 5 days AFTER expiry — what happens to those 5 days of missed evidence? PM verdict: a clear UX message in the portal ("ingest was paused 2026-05-08 → 2026-05-13 pending BAA renewal; resume normal operation") + a renewal-attestation that *names* the gap. Auditor sees the gap is documented.

---

## Lens 6 — Engineering (Steve)

**Verdict: APPROVE-WITH-FIXES**

### Findings
1. **(P0-L6.a) Middleware placement.** The enforcement MUST be middleware, not per-endpoint, OR endpoint enforcement MUST be pinned by a static-analysis gate (`test_baa_state_gates_all_ingest.py`). The "list of ingest endpoints" drift class (sibling to the privileged-chain 4-list lockstep rule) is high-risk — any new ingest endpoint without the check silently leaks. Recommend: FastAPI middleware that reads org_id from auth context + checks `baa_expiration_date` in-process cache (5min TTL — recheck cadence < grace-period TTL).
2. **(P0-L6.b) In-flight order semantics.** Counsel implicit position (Rule 3): chain of custody is set AT emit. Engineering position: complete in-flight orders that were signed under a valid BAA. Add an `emitted_baa_state` column to `fleet_orders` (or capture `baa_expiration_date` snapshot in the signed payload). Daemon checkin still receives the orders but their attestation chain is closed at emit-time.
3. **(P0-L6.c) Substrate invariant — sev1, not sev2.** Proposed: `baa_expired_with_active_ingest` (sev1) — fires when ANY `client_orgs` row has `baa_expiration_date + grace_days < now()` AND has `compliance_bundles` rows created within the last 5min. Auto-resolves once no new bundles for 5min AND middleware-block evidence row exists. **Critical:** the invariant query MUST NOT use `canonical_site_id()` because `compliance_bundles` is in the IMMUTABLE list (Rule 1 / mig 257).
4. **(P0-L6.d) Test fixture parity (per `test_pg_fixture_fleet_orders_column_parity.py` precedent).** Every `*_pg.py` test that creates a `client_orgs` row needs `baa_expiration_date` populated to a future date by default; otherwise the middleware will refuse every request in the test suite. Coach's "fixture parity" lens is invoked.
5. **(P1-L6.e) Cache poisoning risk.** A 5min in-process cache means an operator manually-flipping `baa_expiration_date` waits up to 5min for it to take effect. Recommend a Redis pub/sub invalidation channel (`baa_state_changed:<org_id>`) consumed by all FastAPI workers.
6. **(P1-L6.f) Daemon-side awareness.** The Go daemon needs to gracefully degrade when checkin returns the `ingest_paused` body — display a clear status on the appliance UI ("Service paused — BAA renewal required") rather than retrying forever. Net new daemon code path.

---

## Lens 7 — Coach

**Verdict: BLOCK**

### Findings
1. **(P0-L7.a) BLOCKER — double-build risk vs. mig 283 + org_deprovisioned.** The platform ALREADY has two BAA-state machines: (1) `baa_signatures` (sign event); (2) `baa_relocate_receipt_signature_id` (point-in-time relocate authorization, mig 283); plus the deprovision state machine. Adding a third state machine ("baa-expired-ingest-paused") without first unifying them is the antipattern that produces incoherent legal states (e.g. org is `deprovisioned` AND `baa_expired` AND has a valid `baa_relocate_receipt_signature_id`). **Recommend: define ONE `client_orgs.baa_lifecycle_state` enum** {`pending`, `active`, `expiring_soon`, `expired_paused`, `deprovisioned`} with a state-machine table + transition log + Ed25519-attested transitions. Build mig 283 + org_deprovisioned as views over this. Brownfield migration required — significant.
2. **(P0-L7.b) BLOCKER — canonical-compliance-score (Rule 1) impact.** Per Lens 3.c: if BAA state is a compliance dimension, the canonical helper changes shape. There is no other "single source of truth" for compliance score — `test_no_ad_hoc_score_formula_in_endpoints` pins it. Either (i) `compute_compliance_score()` learns BAA state, or (ii) a parallel canonical `compute_legal_state()` ships + the front-end displays both with explicit framing. NOT decided yet — Gate A cannot approve without this picked.
3. **(P0-L7.c) BLOCKER — sibling-endpoint header parity (per `feedback_multi_endpoint_header_parity.md`).** If `agent_api.py:/checkin` returns a new `X-BAA-State` header on pause, ALL ingest-class endpoints must emit it (witness, evidence emission, fleet-order ack, provision/claim-v2). Sibling parity rule cited 2026-05-13.
4. **(P1-L7.d) Banned `.format()` template class (Session 218 lock-in).** Whatever new copy ships for the customer email + portal banner MUST be Jinja2 with `StrictUndefined`, not Python `.format()`. Pinned via the auditor-kit class rule.
5. **(P1-L7.e) Pre-push python3.11 syntax check + full-sweep parity (per `feedback_three_outage_classes_2026_05_09.md`).** New invariant + new test files must pass `.githooks/full-test-sweep.sh` BEFORE push.
6. **(P1-L7.f) Adversarial Gate B will need runtime evidence.** Per Session 220 lock-in: Gate B can't be diff-scoped. The artifact MUST run with a real expired-BAA org in shadow mode for ≥7 days emitting metrics; Gate B verifies the metric distribution before block-mode is enabled. No CI-green-only Gate B accepted.
7. **(P1-L7.g) Counsel acknowledgement of priority-conflict.** Coach flags: counsel said Rule 6 is priority #1, but the 4 user-gate questions below cannot be answered by anyone except outside-counsel. Implementation that ships without those answers is "we assumed the paper was handled" — the exact failure mode Rule 6 was written to prevent. The fork's BLOCK is in service of Rule 6, not against it.

---

## Design decisions the fork makes (option-space resolution)

### Q1 — WHICH workflows block?

| Workflow | Block on expired? | Lens consensus | Dissent |
|----------|------------------|----------------|---------|
| Daemon checkin (`/api/appliances/checkin`) | PAUSE INGEST (200 + status body) | All 7 lenses | none |
| Witness submit + fleet-order acks | PAUSE | 7/7 | none |
| Evidence-bundle emission (new compliance_bundles INSERT) | PAUSE | 7/7 | none |
| Privileged-access requests | HARD BLOCK 403 | 7/7 | none |
| Cross-org relocate (source-org-side) | HARD BLOCK 403 | 7/7 | Lens 4 expanded scope |
| Cross-org relocate (target-org-side) | already blocked by mig 283 receipt check | — | — |
| Partner swap initiation | HARD BLOCK 403 | 6/7 | Lens 5 wants warning + allow |
| Owner-transfer initiation | HARD BLOCK 403 | 6/7 | Lens 5 wants warning + allow |
| New customer signup | N/A (no BAA yet) | — | — |
| Auditor-kit downloads | **ALLOW** (§164.530(j)) | 7/7 | none |
| Client-portal read-only views | **ALLOW** | 7/7 | none |
| F-series PDF generation (F1/F2/F5) | **HARD BLOCK 403** | 7/7 (Auditor lens P0) | none |
| Quarterly/wall-cert artifacts | HARD BLOCK | 6/7 | Lens 2 carve-out for "renewal in flight" — overruled |
| Compliance score recompute | RETURN SENTINEL (`baa_expired`) | 7/7 | none |

### Q2 — Grace period

Fork consensus: **14-day soft-grace, then pause**. Configurable per-org via `baa_grace_days INT DEFAULT 14`, default reviewed by outside counsel against master BAA template. Lens 4 (Attorney) flags: this is ULTIMATELY a contract-law call — flag to counsel.

### Q3 — Expiry-approach notifications (T-N email cadence)

Fork consensus: T-30, T-14, T-7, T-3, T-1, T-0, then daily after T-0 during grace. Opaque-mode email per RT21 v2.3 — static subject literal, generic body, portal-auth for details. Recipient: org `primary_contact_email` AND `baa_signer_email` if distinct. Lens 4 flags counsel verdict needed.

### Q4 — Block-notification (after grace exhausted)

Fork consensus: opaque-mode email to practice owner + operator alert (P0-CHAIN-GAP per existing operator-alert pattern). Customer email is informational ("renewal required"); operator alert is action-required.

### Q5 — Substrate invariant shape

Fork consensus: `baa_expired_with_active_ingest` (sev1) per Lens 6.c. Auto-resolve once no new bundles for 5min + middleware-block evidence row exists. Operator runbook: `RB-BAA-RENEWAL-001.md` (renew BAA → e-sign flow OR confirm wind-down OR confirm `org_deprovisioned`).

### Q6 — Restoration path

Fork consensus (lens 4-strong): ONLY via new `baa_signatures` row with `baa_signed_at > current baa_expiration_date`. Operator-flip without signature row is BANNED by middleware refusal — admin endpoint refuses the UPDATE if no matching signature row. Privileged-chain Ed25519 attestation on the `expired_paused → active` transition.

### Q7 — Deprovision intersection

Fork consensus: separate state machines, both nodes in the unified `baa_lifecycle_state` enum (Lens 7.a). `expired_paused` is recoverable; `deprovisioned` is terminal. State diagram: `pending → active → expiring_soon → active OR expired_paused → active OR deprovisioned`. NEVER allow `deprovisioned → active` (existing reprovision flow already handles via separate event).

### Q8 — In-flight orders

Fork consensus (Lens 6.b decisive): complete in-flight orders signed under valid BAA. `fleet_orders` gains `emitted_baa_state` column capturing snapshot at sign time. Daemon completes; new orders refuse to sign post-expiry.

### Q9 — Compliance-score impact

Fork consensus (Lens 3.c + Lens 7.b — BLOCKING): `compute_compliance_score()` learns BAA state, returns sentinel `{"score": null, "state": "baa_expired", "explanation": "Compliance score unavailable while BAA renewal is pending."}` when expired. Front-end displays the sentinel explicitly. Auditor-kit picks up the sentinel + the gap-attestation. **This is the load-bearing change — without it, the platform reports a lying score.**

### Q10 — Test fixtures

Fork consensus (Lens 6.d): every `*_pg.py` fixture creating `client_orgs` sets `baa_expiration_date = (CURRENT_DATE + INTERVAL '365 days')` by default. Add `test_pg_fixture_baa_expiration_parity.py` as a pin. Two new test classes: (a) `test_baa_state_gates_all_ingest.py` — static AST scan of every `@router.post` decorated route under `agent_api.py`, `iso_ca.py`, `partners.py:/me/provisions*`, `routes.py:/sites/.*/provision` and require the BAA-state-check decorator or inline call; (b) `test_baa_lifecycle_state_machine_transitions.py` — exhaustive transition gate.

---

## Implementation order (each stage gets its own Gate B fork)

1. **Migration 309** — add `baa_lifecycle_state` enum column on `client_orgs`, backfill from `baa_expiration_date`; add `emitted_baa_state` to `fleet_orders`; add `baa_grace_days` (DEFAULT 14) to `client_orgs`. → **Gate B (fork)** before push.
2. **Compliance-score helper change** — `compute_compliance_score()` returns sentinel on expired. Update 4 callers. → **Gate B**.
3. **Middleware** — FastAPI dependency `require_baa_active` + cache + Redis invalidation. Apply to ingest paths (Q1 enumerated list). → **Gate B**.
4. **Substrate invariant** — `baa_expired_with_active_ingest` (sev1). → **Gate B**.
5. **Renewal endpoint + e-sign flow** — `/api/orgs/{id}/baa/renew` + customer-portal renewal UX. → **Gate B**.
6. **Notifications** — T-N email cadence + operator alerts. Opaque-mode + Jinja2. → **Gate B**.
7. **F-series PDF refusal** — extend `client_attestation_letter.py` BAA-state check. → **Gate B**.
8. **Shadow-mode deploy** — 30 days alerts-only, NO actual block. Metrics collected. → **Gate B** before flipping to enforce mode.
9. **Enforce-mode cutover** — per-org rollout via feature-flag (mig 281 dual-admin pattern), starting with internal test orgs. → **Final Gate B**.

---

## Open questions for user-gate BEFORE implementation

These cannot be answered by the fork OR by engineering — must escalate to user + outside counsel:

1. **(COUNSEL P0) Grace-period default.** 14 days is the fork's pick; what does the master BAA template specify? Should it be 0 / 7 / 14 / 30?
2. **(COUNSEL P0) "Sensitive workflow" enumeration.** The fork's Q1 table needs counsel sign-off. Specifically: are partner-swap + owner-transfer "sensitive" under the BAA? (Lens 5 wanted warning-and-allow; fork overruled but counsel decides.)
3. **(COUNSEL P0) Notification recipient.** Org primary contact OR BAA signer OR both? (Lens 4.d)
4. **(USER P1) Shadow-mode duration.** 30 days is the fork's pick. User may want 60 or 90 given the customer-blocking blast radius.
5. **(USER P1) Existing `north_valley` org's `baa_expiration_date` correctness.** Before any enforcement ships, verify the prod row is accurate. Lens 5.a's "false-positive blast radius" is owned by user.

---

## Final verdict: BLOCK

**Top 5 P0 findings ranked by leverage:**

1. **(L3.c + L7.b)** Compliance-score canonical-source impact — score helper MUST return sentinel on expired BAA; without this, platform reports a lying score. Rule 1 violation if shipped wrong.
2. **(L7.a)** Triple-state-machine fragmentation — unify mig 283 + `org_deprovisioned` + new `baa_lifecycle_state` into ONE enum + transition table BEFORE adding the third. Otherwise incoherent legal states ship.
3. **(L5.a + L5.b + L5.c)** No restoration UX = trap without escape hatch. Renewal endpoint + customer-portal renewal flow + operator-renewal endpoint MUST exist before enforce-mode ships. Shadow-mode for ≥30 days.
4. **(L3.a)** "Block" semantics: appliance returns 200 + `ingest_paused` body, NOT 401/403. Auditor-attackable silent gap if we cut off cold. Pair with locally-signed evidence on the appliance + substrate invariant + admin_audit_log row.
5. **(L4.a + Counsel-P0 #1, #2, #3)** Grace period, sensitive-workflow enumeration, notification recipient are CONTRACT-LAW QUESTIONS the fork cannot decide. Escalate to outside counsel BEFORE implementation begins. Building under engineering's guess is the failure mode Rule 6 was written to prevent.

**Disposition:** BLOCK pending (a) user/counsel verdict on the 5 open questions; (b) explicit decision on Q9 (compliance-score sentinel design); (c) explicit decision on Q7 (unified lifecycle state vs. separate state machines). Re-run Gate A after those land. Estimated total scope to enforce-mode: 6-8 weeks of engineering + ≥30-day shadow-mode soak.
