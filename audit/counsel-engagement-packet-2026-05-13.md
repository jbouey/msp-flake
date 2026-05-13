# Counsel Engagement Packet — Consolidated §-Questions (2026-05-13)

**For:** Outside HIPAA counsel
**From:** OsirisCare engineering, on behalf of the privacy officer
**Date:** 2026-05-13
**Version:** v2 (post Class-B 7-lens internal round-table)
**Engagement type:** Single consolidated engagement — five independently-severable §-questions plus one informational status addendum (RT21 v2.4, no new ask). Items 1 and 4 share the §164.528 interpretive question; counsel may verdict them jointly.
**Status:** Engineering posture is complete + reversible for every item below. No feature touched in this packet ships until counsel returns a written verdict on its corresponding §-question. The RT21 cross-org relocate feature remains flag-disabled awaiting your sign-off on v2.3 §-Q's 1-3 (item 6 below; informational addendum only).

**Companion artifacts (sent with this packet):**

1. `21-counsel-briefing-packet-v2.4-2026-05-09.md` — RT21 runtime-evidence addendum. **Informational; no new ask.** Documents that the v2.3 engineering remains live-and-flag-disabled exactly as you approved it on 2026-05-06.
2. `34-counsel-queue-deferred-2026-05-08.md` — v1 source of items 2-5 (renumbered for packet sequencing — source items 1, 2, 3, 4 map to packet items 2, 3, 4, 5 respectively).
3. `f6-phase-2-enforcement-deferred.md` — v1 source of item 1 below.
4. `docs/HIPAA_FRAMEWORK.md` — substrate posture overview (unchanged since prior engagements).

> **Cover posture:** This is an evidence-grade compliance attestation substrate. Engineering has documented opinions on each of the five §-questions below — those opinions are recorded as "Proposed direction" under each item — but we recognize the legal interpretation is yours. Each item is independently severable with one exception (§-Q's 1 and 4 share the §164.528 interpretive question). All five §-questions operate under the v2.3 §1.5 data-classification framing (compliance metadata treated conservatively as PHI-adjacent; PHI scrubbed at appliance egress). We are NOT requesting a re-design of any feature shipped under the precedent set by your 2026-05-06 sign-off (v2.3), but counsel may, in the course of verdicting these items, conclude that a v2.3 framing needs revision — we will follow counsel's lead. We ARE asking for legal interpretation on the five §-questions so engineering knows whether to ship, modify, or defer indefinitely.

> **Operating framework:** All proposed directions below operate under counsel's seven hard rules laid down 2026-05-13 for enterprise-scale close: (1) no non-canonical metric leaves the building; (2) no raw PHI crosses the appliance boundary; (3) no privileged action without attested chain of custody; (4) no segmentation design that creates silent orphan coverage; (5) no stale document outranks the current posture overlay; (6) no legal/BAA state lives only in human memory; (7) no unauthenticated channel gets meaningful context by default. Each §-question below cross-references which rules its proposed direction implicates so counsel can verdict with the rule-set in view.

> **Per-§-Q rule cross-reference:** §-Q 1 → rules 1 (canonical disclosure logging via `promoted_rule_events`), 6 (BAA addendum gates Tier 2), 8 (subprocessor reclassification if Tier 2 ships). §-Q 2 → rules 6 (continuing-obligation through retention window is BAA-scope question), 7 (opaque magic-link). §-Q 3 → rules 1 (cover sheet's status statement is a canonical-source claim), 9 (provenance of integrity-event window). §-Q 4 → rules 1 (audit log is the canonical disclosure source), 9 (attributable accounting). §-Q 5 → rules 6 (BAA termination is the machine event we already emit as `org_deprovisioned`), 9 (provenance of destruction-attestation).

> **Engineering posture commonality:** §-Q's 1, 3, 5 use the *chain-event-plus-artifact* pattern (existing Ed25519-attested audit log + new customer-facing artifact). §-Q 4 uses a *query-endpoint* pattern (new export surface against existing audit log). §-Q 2 uses a *new-table-plus-magic-link* pattern (former-workforce grants registry + scoped read-token issuance). Each pattern is described under its item's Engineering posture today.

---

## §0 — Five §-questions at a glance

| # | §-question | Section | Engineering posture today | Proposed direction (PENDING COUNSEL) |
|---|---|---|---|---|
| 1 | F6 phase 2 Tier 2 platform-aggregated federation — is the act of training-on-Org-A and deploying-to-Org-B a §164.528-eligible disclosure of derived information, AND if so, may the §164.528 accounting be satisfied by a per-deployment logging entry rather than a per-patient accounting? Does BAA language need amendment to authorize cross-customer pattern federation? | §1 | Tier 0 + Tier 1 read-path live; Tier 2 WRITE-path **not shipped** awaiting this verdict. Foundation slice ships 21-day calibration snapshot table. | A: counsel approves with §164.528 disclosure-accounting (per-deployment logging via the `promoted_rule_events` ledger lockstep) + BAA addendum template / B: approves with logging-only, no addendum / C: declines — Tier 0 + Tier 1 continue; "cross-customer pattern learning" marketing copy revised. |
| 2 | §164.524 individual-access-right — does a covered entity retain an obligation to provide the auditor kit (or any portion) to a former workforce member after offboarding, and is that obligation a one-time fulfillment or continuing through the §164.530(j) retention window? | §2 | Auditor kit gated by `require_evidence_view_access`; former workforce loses access on offboarding. Zero PHI in the kit by design. | A: build former-workforce magic-link access (7-day token PER REQUEST, continuing-obligation framing through retention window) / B: practice-owner-archives-on-offboarding workflow / C: no §164.524 obligation, no engineering. |
| 3 | §164.504(e)(2)(ii)(D) — does the auditor kit need a cover sheet flagging "this kit was generated during an open integrity-event window" so the recipient sees the disclosure obligation up-front? | §3 | Substrate invariants run every 60s; failures visible at `/admin/substrate-health` to platform engineering only — the practice does not see substrate-integrity events today, and whether they should is partially what we're asking. Kit is byte-deterministic. | A: automated cover sheet inside the ZIP with deterministic event-time timestamp (preserves byte-determinism) PLUS counsel-supplied explanation copy / B: manual privacy-officer cover sheet upload / C: no cover sheet; existing disclaimer suffices. |
| 4 | §164.528 disclosure-accounting — if a practice owner requests an accounting via OsirisCare, do we build path (a) standard or path (b) repetitive-disclosure summary? Does the existing "NOT a §164.528 accounting" disclaimer on F1/P-F6/auditor-kit need revision if path (a) or (b) ships? | §4 | The privileged-access audit log records every privileged action. No disclosure-recipient or disclosure-purpose columns today. No accounting endpoint today. | A+: raw-CSV endpoint + §164.528(a)(2)-mapped synthesis template (clinic cannot synthesize a bare CSV) / B: OsirisCare produces accounting directly with PO sign-off workflow / C: path (b) summary template only. |
| 5 | §164.504(e)(2)(ii)(J) — at BAA termination, the existing `org_deprovisioned` chain event is already live; what counsel-approved LETTER content + chain-anchor + destruction-attestation copy should accompany it? Three recipient workflows must be covered. | §5 | `org_deprovisioned` event LIVE in `privileged_access_attestation.py:156` ALLOWED_EVENTS with Ed25519-chained emissions at `org_management.py:339, 379, 416` since Maya P1-1 closure 2026-05-04. **No letter exists today.** PHI is scrubbed at appliance egress by design. | A: extend existing `org_deprovisioned` with a new F-series mode (reusing F1 template infrastructure) rendering a §164.504(e)(2)(ii)(J)-compliant Deprovision Notice; cover three recipient workflows / B: A + 30-day-grace WORM-redaction (dis-preferred per §164.530(j) conflict) / C: counsel rules `org_deprovisioned` alone (no letter) satisfies §164.504(e)(2)(ii)(J). |
| 6 | (Informational) RT21 cross-org relocate v2.3 §-Q's 1-3 still open from 2026-05-06 packet | §6 (companion v2.4) | Feature flag-disabled awaiting your sign-off on §164.504(e) permitted-use scope + §164.528 substantive completeness + receiving-org BAA scope. | No new ask; awaiting your written sign-off. |

---

## §1 — F6 phase 2 Tier 2 federation — §164.528 disclosure-accounting

### Plain-English summary

OsirisCare's data-flywheel learns from auto-resolved incidents at each customer site and promotes high-confidence patterns into L1 deterministic rules that ship back to the same site as fleet orders. This is **Tier 0** — single-site learning, single-site deployment. Currently in production.

**Tier 1** aggregates patterns across sites *within the same client_org* (one practice owner with multiple physical locations) — this is single-BAA, single-CE, and our position is that no §164.528 question arises because no disclosure crosses a BAA boundary.

**Tier 2** aggregates patterns across DIFFERENT client_orgs and deploys learned rules to all participating orgs. This crosses BAA boundaries even though the rule artifact itself contains zero PHI (the rule is "if process X crashes on Windows Server 2022, restart service Y" — derived from de-identified telemetry).

### The §-question

> **Is the act of training-on-Org-A and deploying-to-Org-B a §164.528-eligible disclosure of derived information, AND if so, may the §164.528 accounting be satisfied by a per-deployment logging entry rather than a per-patient accounting?**

### Sub-questions

(a) Is the L1 rule artifact (a JSON document describing pattern signature, conditions, and remediation steps) a "disclosure" under §164.514(b) safe harbor, or does the de-identification standard cover this?

(b) If §164.528 applies, can the accounting be reduced to a per-cross-org-deployment event row in the existing `promoted_rule_events` flywheel ledger, or does it need to be per-patient as the standard reads literally?

(c) Does each customer BAA need an *amendment* expressly authorizing inter-customer pattern federation, or is existing BAA language sufficient under the substrate's standing 'audit-supportive technical evidence' framing (established in v2.3 §1.5 and pinned in OsirisCare engineering's customer-facing-copy standards)? If amendment is needed, we will draft a template addendum for your review.

### Engineering posture today

- Tier 0 + Tier 1 read-path **shipped** Session 214 (`a91794ce`); cross-org isolation property test in CI.
- Tier 2 WRITE-path **NOT shipped**. No feature flag exists.
- Foundation slice (read-only operator endpoint + daily snapshot table) shipped to gather 2-3 weeks of calibration data BEFORE this round-table reconvenes.
- A cross-org federation-leak substrate invariant is designed but **NOT YET DEPLOYED** because a naive implementation had a 100%-false-positive failure mode on a 2-tenant fleet (see `f6-phase-2-enforcement-deferred.md` §"Cross-org leak invariant — design notes" for the correct UUID-PK JOIN that fixes it).
- The non-operator-posture question (source `f6-phase-2-enforcement-deferred.md` Q1/Q2) is engineering-class and not asked of counsel; our position is no Tier 2 ships absent explicit per-org consent.
- **Lockstep target:** the proposed `federation_disclosure` event_type is targeted at the existing **`promoted_rule_events` lockstep** (Python EVENT_TYPES in `flywheel_state.py` + `_DISPLAY_METADATA` + runbook + DB CHECK `promoted_rule_events_event_type_check`), NOT the `privileged_access_attestation.ALLOWED_EVENTS` system. This is the canonical ledger for flywheel rule-promotion events.

### Proposed direction (PENDING COUNSEL)

**Option A — counsel approves Tier 2 with §164.528 accounting + BAA addendum:** Engineering would build (1) `federation_disclosure` event_type added to the `promoted_rule_events` lockstep; (2) per-cross-org-deployment ledger row anchored at the receiving org's primary site (mirrors the existing Privileged-Access Chain of Custody pattern: client identity → policy approval → execution → attestation); (3) per-org federation-consent attestation bundle reference; (4) BAA addendum template for your review; (5) the cross-org federation-leak substrate invariant (using the corrected UUID-PK JOIN).

**Option B — counsel approves Tier 2 with logging-only requirement:** Drop the BAA addendum requirement; keep §164.528 logging in the `promoted_rule_events` ledger. Less engineering; more legal exposure if a single-counsel interpretation changes.

**Option C — counsel declines:** Tier 2 never ships. Engineering removes the foundation-slice calibration snapshot loop. Tier 0 + Tier 1 continue as today. **Downstream impact engineering will manage:** customer-facing copy that describes "cross-customer pattern learning" or similar would be revised; Tier 0 + Tier 1 (intra-org) continue to support the within-org learning story.

### Why this matters now

If your verdict is C, we want to know before the 21-day calibration window closes so we don't waste a dedicated F6-phase-2 round-table session on a path that's been counsel-declined.

---

## §2 — §164.524 former-workforce kit access

### Plain-English summary

When a workforce member of a covered entity (a practice owner who originally designated the Privacy Officer; an employed physician with §164.524 access right; a former employee with continuing §164.524 access entitlement) leaves the practice or has their portal access revoked, the question is whether the practice retains a HIPAA §164.524 individual-access-right obligation to provide that former workforce member with the auditor kit (or any portion of it).

Real-world request cadence: former workforce §164.524 requests typically arrive **months to years** after offboarding (a departing physician building a malpractice defense; a former PO compelled by state board 14 months later; a former employee responding to an OCR subpoena). The framing of "one-time fulfillment" vs "continuing obligation through retention window" is itself a §-question.

The auditor kit is "audit-supportive technical evidence" — established in v2.3 §1.5 and pinned in OsirisCare engineering's customer-facing-copy standards; it is NOT a §164.528 disclosure accounting. It contains: bundle hashes, pubkeys, OpenTimestamps receipts, identity chain, ISO CA bundle, README explaining how to verify. **It contains zero PHI by design.**

### The §-question

> **Does a covered entity retain a §164.524 individual-access-right obligation to provide the auditor kit (or any portion of it) to a former workforce member after offboarding, and is that obligation a one-time fulfillment or continuing through the §164.530(j) retention window?**

### Sub-questions

(a) Is the auditor kit a §164.524 "designated record set" subject to individual access right?

(b) If yes: the §164.524 right is to *the individual's PHI*; the kit contains zero PHI by design. Does the access right still attach to a zero-PHI artifact derived from the workforce member's interactions with the substrate?

(c) If §164.524 does not attach: is the BA's §164.530(j) obligation to retain its OWN records (which differ from the practice's records) implicated by former-workforce kit access requests? (Reframed per attorney-surrogate note — keep the question on OsirisCare's BA-side obligation, not the CE's.)

(d) If Option A below ships: counsel's view on the practice owner's continuing-approval workload (potentially many requests over the 6-year retention window from multiple former workforce members) — is there a counsel-approved limit on request frequency, or does the right run unbounded?

### Engineering posture today

- Auditor kit gated by a 5-branch authorization function: admin session / client-session cookie + org-owns-site / partner-session + role∈{admin,tech} + sites.partner_id match / legacy portal-session cookie / legacy `?token=` query param (both legacy paths scheduled for deprecation; deprecation telemetry emits on every legacy-path resolution).
- A former workforce member loses session + cookie + token on offboarding. They cannot pull the kit through any extant code path.
- Per-(site, caller) rate limits are bucketed per-caller — an admin's investigation, a partner's pull, and an auditor's download don't compete for the same 10/hr cap.

### Proposed direction (PENDING COUNSEL)

**Option A — counsel approves continuing §164.524 obligation through the §164.530(j) retention window:** Engineering would build a former-workforce personal-access-grants table + magic-link-style endpoint that mints a 7-day token PER REQUEST bound to a former-workforce email, validates against the table, serves the kit (kit-only — no portal-write access). The 7-day window is for a SINGLE fulfillment; the practice's obligation to honor repeat requests runs **continuing** through the §164.530(j) 6-year retention window. Counsel: confirm or override the continuing-obligation framing. **Spec scope counsel should be aware of:** the new table itself is privileged-access-class (it grants kit reads), so it requires (i) RLS treatment paralleling other client-portal tables, (ii) explicit retention policy (we propose: rows persist 6 years from offboarding, aligned with §164.530(j)), (iii) explicit revocation flow if a former member's grant must be withdrawn (our position: practice owner can revoke at any time). **Operational note:** the practice owner approves former-workforce access requests as they arrive; over a 6-year retention window with multiple former workforce members, request volume may accumulate the practice should plan for.

**Option B — counsel approves no §164.524 obligation but §164.530(j) retention applies:** Practice owner downloads the kit on offboarding day, archives it under their own records-retention obligation. No new engineering. Document the practice-side workflow in `docs/RUNBOOKS.md`.

**Option C — counsel approves neither §164.524 nor §164.530(j):** No engineering. Document the legal opinion so future deferred-item triage doesn't re-litigate.

---

## §3 — §164.504(e)(2)(ii)(D) auditor kit cover sheet

### Plain-English summary

§164.504(e)(2)(ii)(D) requires Business Associates to "report to the covered entity any use or disclosure of the information not provided for by its contract" ("breach notification" in colloquial terms). When OsirisCare detects a substrate-level integrity event (an Ed25519 signature mismatch on a chain replay; an OpenTimestamps anchor that fails to confirm in the expected window; a cross-org-relocate chain-orphan invariant tripping), the question is whether the auditor kit needs a cover sheet flagging "this kit was generated during an open §164.504(e)(2)(ii)(D)-eligible event window" so the auditor sees the disclosure obligation up-front, before they evaluate the kit's substantive contents.

### The §-question

> **Does the auditor kit require a cover sheet flagging open §164.504(e)(2)(ii)(D)-eligible integrity-event windows, and if so, what classes of substrate events trigger inclusion?**

### Sub-questions

(a) Is a substrate-level integrity event automatically a §164.504(e)(2)(ii)(D) "use or disclosure not provided for by its contract"? Our position is **no** — a signature mismatch is a chain-of-custody concern WITHOUT being a disclosure of information. We want counsel's view.

(b) If counsel's view is that some classes of integrity events ARE §164.504(e)(2)(ii)(D)-eligible, which classes? The list counsel produces will become a NEW LOCKSTEP between substrate invariants + cover-sheet builder + auditor-kit rendering — engineering will commit to keeping it in lockstep but needs counsel to scope it as tightly as possible.

(c) Should the cover sheet be generated automatically from substrate invariants, or should the practice's privacy officer manually attach the cover sheet?

(d) If Option A ships: what counsel-supplied **explanation copy** should accompany the status statement — clinic-side disposition framework (when does the practice's notification clock start? what is "open" — does it close when the substrate auto-resolves?). A bare status statement without disposition framework creates clinic-side ambiguity; we'd prefer counsel-approved copy that includes the framework.

### Engineering posture today

- Substrate invariants run every 60s. Failures are visible at `/admin/substrate-health` to platform engineering, NOT to the practice. The practice does not see substrate-integrity events today; whether they should is partially what we're asking.
- Auditor kit reads: chain (live recompute) → public-key inventory → OpenTimestamps receipts → README. **No cover-sheet generation today.**
- The kit is byte-deterministic across downloads — the determinism contract is enforced at the ZIP level (every entry written via a canonical `_kit_zwrite` primitive with pinned `date_time`, `compress_type=ZIP_DEFLATED`, fixed permissions). Byte-determinism is the load-bearing tamper-evidence promise; auditors hash the kit and compare across downloads to detect substitution.

### Proposed direction (PENDING COUNSEL)

**Option A — counsel approves automated cover sheet inside the deterministic ZIP:** Engineering would build a cover-sheet template + builder that runs at kit generation time, queries open substrate-integrity events, renders a 1-page §164.504(e)(2)(ii)(D) status-of-knowledge statement, AND adds it as a `cover_sheet.pdf` entry **inside the ZIP** using a deterministic timestamp derived from the most recent open-event's `created_at` (not wall-clock). This preserves the byte-determinism contract — two consecutive downloads with no new integrity events produce identical ZIPs. The cover sheet is signed separately with the same Ed25519 key + its own attestation row in a new cover-sheet attestation table. **Counsel sign-off on Option A includes acknowledgement that the cover sheet's source-of-truth timestamp is event-time, not download-time.** Counsel-supplied explanation copy (per sub-question d) accompanies the status statement.

**Option B — counsel approves manual privacy-officer cover sheet:** PO uploads a cover sheet via a new partner/portal endpoint; we attach as a sidecar **delivered separately from the kit ZIP** (preserves kit byte-determinism by staying outside the deterministic envelope). Lighter engineering; heavier PO workflow. This is a regression to pre-substrate manual workflow — proposed only for completeness; not engineering's recommended path.

**Option C — counsel approves no cover sheet:** No engineering. The kit's existing disclaimer ("audit-supportive evidence; not a §164.528 disclosure accounting") covers the framing.

---

## §4 — §164.528 disclosure-accounting path (b)

### Plain-English summary

§164.528 enumerates two paths for "accounting of disclosures": (a) the standard accounting; (b) the alternative summary for repetitive-disclosure scenarios (≥5 disclosures to the same recipient for the same purpose). The auditor kit + F1 attestation letter + P-F6 BA Compliance Letter all carry the disclaimer "this is NOT a §164.528 disclosure accounting." The disclaimer was approved by counsel in the cross-org-relocate packet (item 21) v2 round.

A 5-physician practice does not have an in-house privacy-officer capable of synthesizing a §164.528 accounting from a CSV — they retain OsirisCare/the MSP because they CAN'T. Any deliverable that hands them raw audit data and asks them to produce an OCR-ready accounting will fail at the first request. The proposed direction reflects this clinic-capability reality.

### The §-question

> **If a practice owner specifically requests a §164.528 accounting via OsirisCare, does engineering build path (a) or path (b), and what is the trigger for path (b) eligibility? And: does the existing "NOT a §164.528 accounting" disclaimer on F1 / P-F6 / auditor-kit need revision if path (a) or (b) ships?**

### Sub-questions

(a) What is the path-(b) eligibility trigger you'd accept? Our position: "≥5 disclosures to same recipient for same purpose within 1 calendar year" — §164.528(b)(3) language is dense and we want your read.

(b) For path (a), what is the OsirisCare-side data shape that satisfies the §164.528(a)(2) elements (date / recipient name + address / brief description of PHI disclosed / brief statement of purpose)? We have privileged-access audit log rows for every privileged action (operator-initiated kit pulls, cross-org-relocate flag flips, BA-roster mutations); what additional columns are needed?

(c) Is the §164.528 accounting the practice's obligation OR ours-on-behalf-of? Our position: practice's, OsirisCare provides the deliverable. But this depends on the BAA scope.

(d) **Disclaimer revision** — if Option A+ or Option B ships, the existing "this is NOT a §164.528 disclosure accounting" disclaimer on F1 / P-F6 / auditor-kit creates an internal inconsistency (we'd be providing exactly that deliverable). Does counsel want the disclaimer reworked to clarify what the F-series IS vs what the new accounting deliverable IS, or kept as-is with the new accounting framed as additive?

### Engineering posture today

- The privileged-access audit log records every privileged action with actor identity, action, target, details, source IP, and timestamp.
- The Ed25519-signed evidence chain records every evidence event.
- Neither table has a disclosure-recipient identifier column or disclosure-purpose column. §164.528 path (a) requires those.
- Existing audit-log slices are already exported into the auditor kit's `identity_chain.json` rendering — any new "accounting" endpoint must consume the same view rather than build a parallel export path.

### Proposed direction (PENDING COUNSEL)

**Option A+ — counsel approves raw-CSV + synthesis-template path (combined). Engineering's preferred steady state.** Engineering would build (1) a new client-portal endpoint `GET /api/client/disclosure-accounting/raw` (does not exist today) that returns CSV of audit log rows filtered to the practice's site_ids; the endpoint goes through `org_connection` with `tenant_org_isolation` RLS policy on the underlying audit-log view; (2) a `disclosure_recipients` registry the practice maintains; (3) a §164.528(a)(2)-mapped synthesis template that converts CSV + recipients-registry into an OCR-ready accounting PDF. Bare CSV alone is a non-deliverable in primary care; the synthesis template is what makes it a §164.528-compliant accounting. **A+ keeps the platform in tooling-plus-template posture — engineering's preferred steady state.**

**Option B — counsel approves OsirisCare produces path (a) accounting directly with PO sign-off:** Engineering would add disclosure-recipient + disclosure-purpose columns to the audit log + a new disclosure-accounting-requests workflow with PO sign-off step. Heavier engineering. **Our position: Option B is a strategic shift toward managed compliance operations** — it moves the platform from tooling-plus-template into service-execution responsibility. We would only pursue this if business strategy intentionally shifts toward a managed-service layer; otherwise A+ is the steady-state recommendation.

**Option C — counsel approves path (b) summary for repetitive disclosures only:** Engineering would build a path-(b) summary template that aggregates "8 disclosures to Carrier A between 2026-Q1 and 2026-Q2 for purpose: re-credentialing review." Path (a) deferred.

---

## §5 — §164.504(e)(2)(ii)(J) deprovision-notice — counsel-approved LETTER content

### Plain-English summary

§164.504(e)(2)(ii)(J) requires BA agreements to obligate the BA to "return or destroy all PHI received from, or created or received by the BA on behalf of, the CE that the BA still maintains in any form" at termination of the contract.

**A live, Ed25519-attested chain event for organization deprovisioning already exists in the substrate.** `org_deprovisioned` is registered in `privileged_access_attestation.py:156` `ALLOWED_EVENTS` and emits from `org_management.py:339, 379, 416` (since Maya P1-1 closure 2026-05-04). What does NOT exist is a customer-facing LETTER that accompanies the event — a counsel-approved §164.504(e)(2)(ii)(J)-compliant artifact the practice owner can hand to a successor BA, an auditor, or a regulator.

This §-question therefore asks counsel **about the LETTER**, not about creating the event. The packet does not ask counsel to bless infrastructure engineering has already shipped under prior precedent.

Three distinct recipient workflows must be covered by the LETTER + chain anchor:

1. **Owner-MSP-swap.** Owner intact, just changing BA. Deprovision letter goes to the practice owner who hands it to the successor BA.
2. **Practice-closure with owner intact.** Practice winds down, owner retains records-retention obligation. Letter goes to the owner who archives it.
3. **Practice-closure with owner incapacitated / deceased / unavailable.** Recipient is the estate executor / state regulator / successor entity. Authentication of the destruction-attestation receipt is non-trivial in this case.

### The §-question

> **What counsel-approved LETTER content, chain-anchor policy, and destruction-attestation copy should accompany the existing `org_deprovisioned` event to satisfy §164.504(e)(2)(ii)(J), and how should the LETTER differ across the three recipient workflows (owner-MSP-swap / practice-closure-owner-intact / practice-closure-owner-unavailable)?**

### Sub-questions

(a) Is "return PHI" achievable when PHI is scrubbed at the appliance edge before egress? Our position: by design, PHI is scrubbed at appliance egress before any data reaches OsirisCare central command; central command holds no PHI under normal operation. We are not claiming an absence-proof; we are claiming a designed-and-tested boundary. Counsel: confirm or override.

(b) For "destroy PHI" — does counsel accept the existing Ed25519-signed `org_deprovisioned` chain event + appliance-side wipe receipt as sufficient destruction-attestation, OR does counsel require WORM-deletion of every substrate evidence bundle that referenced the practice's site identifier?

(c) For the three recipient workflows above, does the LETTER copy differ — and if so, how? Specifically: in workflow 3 (owner unavailable), who is the authenticated recipient? Is the LETTER addressed to a generic "successor entity / regulator" with chain-of-custody verification via the public verify endpoint? Counsel's view shapes the engineering.

(d) Should the LETTER be a new MODE of the existing F1 Compliance Attestation Letter template family (counsel-approved content, F1 chain-anchor + presenter-snapshot pattern, served from existing infrastructure) OR a wholly separate template family? Our position: reuse F1 (no double-build); counsel may have a posture preference.

### Engineering posture today

- `org_deprovisioned` chain event: LIVE in `privileged_access_attestation.py:156` ALLOWED_EVENTS. Ed25519-chained. Anchored at the org's primary site (or `client_org:<id>` synthetic anchor for org-level events per CLAUDE.md "Anchor-namespace convention").
- Emission sites: `org_management.py:339, 379, 416` (and failure paths at 397, 404 with `org_deprovisioned_attestation_failed` / `_attestation_unexpected` alert classes).
- PHI scrubbing at appliance edge is the BAA-level boundary by design. Central command is PHI-free under normal operation.
- WORM evidence is never deleted by design. Per HIPAA §164.530(j) the practice's records-retention obligation is 6 years; the substrate cannot unilaterally delete WORM evidence the practice's auditors may need.
- **What does NOT exist:** customer-facing LETTER, F-series mode for deprovision, recipient-workflow differentiation, destruction-attestation copy. This is the engineering scope counsel's verdict commits to building.

### Proposed direction (PENDING COUNSEL)

**Option A — counsel approves "existing `org_deprovisioned` event + new F-series LETTER mode is sufficient destruction-attestation":** Engineering would extend the existing F1 Compliance Attestation Letter template (`backend/templates/attestation_letter/letter.html.j2`) with a new mode `kind='deprovision_notice'` — reusing F1's Ed25519-signing + chain-anchor + presenter-snapshot infrastructure (NO new F-series artifact; this is a MODE of F1, not a sixth artifact). The letter renders counsel-approved §164.504(e)(2)(ii)(J)-compliant language. Public verify endpoint extends to verify the deprovision-notice's chain-binding via the existing F4 `/verify/{hash}` pattern.

**Engineering's near-term commitment under Option A:** workflows 1 (owner-MSP-swap) and 2 (practice-closure-owner-intact) ship first as a tight, well-scoped engineering deliverable — both have an authenticated practice-owner as the deterministic recipient. **Workflow 3 (practice-closure-owner-unavailable) is counsel-scoped and identity/auth-dependent — engineering treats it as phase-2 contingent on counsel-specific authentication guidance for the estate-executor / state-regulator / successor-entity recipient class.** Counsel: please verdict whether workflow 3's recipient authentication should mirror v2.3's BAA-receipt-auth pattern (mig 283), use a different attestation channel, or rely on the public verify endpoint with chain-of-custody verification.

**Option B — counsel approves Option A but ALSO requires WORM-redaction:** Option A + a 30-day-grace WORM-redaction workflow that nukes evidence bundles after the practice's records-retention window. Substantial engineering. **Our read: Option B is dis-preferred** because the WORM-redaction step conflicts with the practice's own §164.530(j) 6-year records-retention obligation — substrate cannot unilaterally delete evidence the practice may need for its own auditors. The CE-side retention obligation outweighs the BA-side destruction-confirmation completeness.

**Option C — counsel rules existing `org_deprovisioned` event ALONE (no new letter) satisfies §164.504(e)(2)(ii)(J):** Engineering documents counsel's interpretation. No new F-series mode. No LETTER. The append-only attested chain event + appliance-side wipe receipt + public verify endpoint are the destruction-attestation. **Our read: this is auditor-unfriendly** because a real HIPAA audit will ask "show me the destruction certificate" and a chain-event row identifier is not a customer-facing document. We surface this option only because counsel may rule it sufficient under the audit-supportive-evidence framing.

---

## §6 — Informational: RT21 cross-org relocate v2.4 status

This section is **informational only — NO NEW ASK**. It captures that the v2.3 packet's §-Q's 1-3 (§164.504(e) permitted-use scope under both BAAs; §164.528 substantive completeness; receiving-org BAA scope) remain open from your 2026-05-06 sign-off. §-Q #4 (opaque-mode email defaults) is closed by v2.4 §A condition #5. The feature is engineering-complete + deployed-but-flag-disabled.

Companion artifact: `21-counsel-briefing-packet-v2.4-2026-05-09.md` (runtime evidence addendum) — sent under separate cover 2026-05-09.

Engineering will continue to hold the flag disabled until you provide written sign-off on whichever subset of §-Q's 1-3 you can approve. The per-customer phase-in option from v2.4 §E remains available at your direction. v2.4 §F's 30-day quiet-window proposal stands; engineering has not requested counsel waiver of it.

---

## §7 — Hand-back format — what counsel returns

For each §-Q 1-5 above, counsel returns:

1. **Verdict:** Option A / A+ / B / C / counter-proposal.
2. **Conditions** (if any) that engineering must implement before shipping the chosen option.
3. **Copy** for any customer-facing language (cover sheets, deprovision-notice letter content for the three recipient workflows, accounting templates, BAA addenda) so engineering interpolates verbatim.
4. **§-citation justification** for the verdict — included in the artifact's disclaimer block (mirrors how cross-org-relocate v2.3 §164.504(e) language was approved).

For §-Q 6 (informational), if you have any reservation about the substrate's commitment to running with cross-org-relocate enabled, the v2.4 §D process commitment gives you explicit accountability surface: any drift from the v2.3 §7 conditions surfaces on the substrate dashboard within 60s and escalates within 4h via the substrate SLA invariant.

---

## §8 — Engagement logistics

**Scope:** Five §-questions (items 1-5 above), one engagement.

**Expected counsel timeline:** 30 days from this packet's receipt. Engineering will not ship items 1-5 in any form until counsel returns a written verdict on each. If counsel's response is partial (e.g. verdict on items 2-5 but item 1 deferred), engineering proceeds on the verdicted items and continues to hold item 1.

**Severability:** Items 2, 3, 5 are independently severable. Items 1 and 4 share the §164.528 interpretive question (Tier 2 federation's per-deployment logging in §-Q 1 vs path-(a)/(b) accounting shape in §-Q 4); counsel may verdict them jointly.

**Companion engagement:** This packet does NOT supersede the v2.3 RT21 packet. The five conditions on RT21 sign-off remain as you wrote them on 2026-05-06; v2.4 confirms engineering compliance with them.

**Engineering escalation:** If counsel needs to clarify any of items 1-5 before responding, the engineering escalation path is via the same channel used for the cross-org-relocate packet's v2.x rounds.

---

— OsirisCare engineering
   on behalf of the privacy officer
   2026-05-13
