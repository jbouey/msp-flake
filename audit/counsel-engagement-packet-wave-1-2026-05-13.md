"u# Counsel Engagement Packet — Wave 1 — Concrete §-Questions (2026-05-13)

**For:** Outside HIPAA counsel
**From:** OsirisCare engineering, on behalf of the privacy officer
**Date:** 2026-05-13
**Version:** Wave 1 v1 (post Class-B 7-lens internal round-table + 2-wave restructuring per counsel guidance 2026-05-13)
**Engagement type:** Wave 1 of a 2-wave engagement. **Wave 1 contains three concrete, artifact/workflow-shaped §-questions** with less interpretive load. **Wave 2** (queued for separate engagement) contains two densely-coupled §164.528-interpretation questions on F6 phase 2 federation accounting + §164.528 path (b) — engineering will send Wave 2 as its own packet so counsel can verdict each wave on its own merit without the dense pair blocking the concrete items.
**Status:** Engineering posture is complete + reversible for every item below. No feature touched in this packet ships until counsel returns a written verdict on its corresponding §-question. The RT21 cross-org relocate feature remains flag-disabled awaiting your sign-off on v2.3 §-Q's 1-3 (item 4 below; informational addendum only).

**Companion artifacts (sent with this Wave 1 packet):**

1. `21-counsel-briefing-packet-v2.4-2026-05-09.md` — RT21 runtime-evidence addendum. **Informational; no new ask.** Documents that the v2.3 engineering remains live-and-flag-disabled exactly as you approved it on 2026-05-06.
2. `34-counsel-queue-deferred-2026-05-08.md` — v1 source of items 1-3 below (source items 1, 2, 4 map to Wave-1 items 1, 2, 3 respectively).
3. `docs/HIPAA_FRAMEWORK.md` — substrate posture overview (unchanged since prior engagements).

> **Cover posture:** This is an evidence-grade compliance attestation substrate. Engineering has documented opinions on each of the three §-questions below — those opinions are recorded as "Proposed direction" under each item — but we recognize the legal interpretation is yours. Each item is independently severable. All three §-questions operate under the v2.3 §1.5 data-classification framing (compliance metadata treated conservatively as PHI-adjacent; PHI scrubbed at appliance egress). We are NOT requesting a re-design of any feature shipped under the precedent set by your 2026-05-06 sign-off (v2.3), but counsel may, in the course of verdicting these items, conclude that a v2.3 framing needs revision — we will follow counsel's lead.

> **Operating framework:** All proposed directions below operate under the seven hard rules you laid down 2026-05-13 for enterprise-scale close: (1) no non-canonical metric leaves the building; (2) no raw PHI crosses the appliance boundary; (3) no privileged action without attested chain of custody; (4) no segmentation design that creates silent orphan coverage; (5) no stale document outranks the current posture overlay; (6) no legal/BAA state lives only in human memory; (7) no unauthenticated channel gets meaningful context by default. Each §-question below cross-references which rules its proposed direction implicates so you can verdict with the rule-set in view.

> **Per-§-Q rule cross-reference:** §-Q 1 → rules 6 (continuing-obligation through retention window is BAA-scope question), 7 (opaque magic-link). §-Q 2 → rules 1 (cover sheet's status statement is a canonical-source claim), 9 (provenance of integrity-event window). §-Q 3 → rules 6 (BAA termination is the machine event we already emit as `org_deprovisioned`), 9 (provenance of destruction-attestation).

---

## §0 — Three §-questions at a glance

| # | §-question | Section | Engineering posture today | Proposed direction (PENDING COUNSEL) |
|---|---|---|---|---|
| 1 | §164.524 individual-access-right — does a covered entity retain an obligation to provide the auditor kit (or any portion) to a former workforce member after offboarding, and is that obligation a one-time fulfillment or continuing through the §164.530(j) retention window? | §1 | Auditor kit gated by `require_evidence_view_access`; former workforce loses access on offboarding. Zero PHI in the kit by design. | A: build former-workforce magic-link access (7-day token PER REQUEST, continuing-obligation framing through retention window) / B: practice-owner-archives-on-offboarding workflow / C: no §164.524 obligation, no engineering. |
| 2 | §164.504(e)(2)(ii)(D) — does the auditor kit need a cover sheet flagging "this kit was generated during an open integrity-event window" so the recipient sees the disclosure obligation up-front? | §2 | Substrate invariants run every 60s; failures visible at `/admin/substrate-health` to platform engineering only — the practice does not see substrate-integrity events today, and whether they should is partially what we're asking. Kit is byte-deterministic. | A: automated cover sheet inside the ZIP with deterministic event-time timestamp (preserves byte-determinism) PLUS counsel-supplied explanation copy / B: manual privacy-officer cover sheet upload / C: no cover sheet; existing disclaimer suffices. |
| 3 | §164.504(e)(2)(ii)(J) — at BAA termination, the existing `org_deprovisioned` chain event is already live; what counsel-approved LETTER content + chain-anchor + destruction-attestation copy should accompany it? Engineering commits near-term to workflows 1+2 (owner-MSP-swap, practice-closure-owner-intact); workflow 3 (owner-unavailable) is counsel-scoped and contingent on counsel's guidance for estate-executor / state-regulator / successor-entity recipient authentication. | §3 | `org_deprovisioned` event LIVE in `privileged_access_attestation.py:156` ALLOWED_EVENTS with Ed25519-chained emissions at `org_management.py:339, 379, 416` since Maya P1-1 closure 2026-05-04. **No letter exists today.** PHI is scrubbed at appliance egress by design. | A: extend existing `org_deprovisioned` with a new F-series mode (reusing F1 template infrastructure) rendering a §164.504(e)(2)(ii)(J)-compliant Deprovision Notice; engineering ships workflows 1+2 first, workflow 3 contingent / B: A + 30-day-grace WORM-redaction (dis-preferred per §164.530(j) conflict) / C: counsel rules `org_deprovisioned` alone (no letter) satisfies §164.504(e)(2)(ii)(J). |
| 4 | (Informational) RT21 cross-org relocate v2.3 §-Q's 1-3 still open from 2026-05-06 packet | §4 (companion v2.4) | Feature flag-disabled awaiting your sign-off on §164.504(e) permitted-use scope + §164.528 substantive completeness + receiving-org BAA scope. | No new ask; awaiting your written sign-off. |

---

## §1 — §164.524 former-workforce kit access

### Plain-English summary

When a workforce member of a covered entity (a practice owner who originally designated the Privacy Officer; an employed physician with §164.524 access right; a former employee with continuing §164.524 access entitlement) leaves the practice or has their portal access revoked, the question is whether the practice retains a HIPAA §164.524 individual-access-right obligation to provide that former workforce member with the auditor kit (or any portion of it).

Real-world request cadence: former workforce §164.524 requests typically arrive **months to years** after offboarding (a departing physician building a malpractice defense; a former PO compelled by state board 14 months later; a former employee responding to an OCR subpoena). The framing of "one-time fulfillment" vs "continuing obligation through retention window" is itself a §-question.

The auditor kit is "audit-supportive technical evidence" — established in v2.3 §1.5 and pinned in OsirisCare engineering's customer-facing-copy standards; it is NOT a §164.528 disclosure accounting. It contains: bundle hashes, pubkeys, OpenTimestamps receipts, identity chain, ISO CA bundle, README explaining how to verify. **It contains zero PHI by design.**

### The §-question

> **Does a covered entity retain a §164.524 individual-access-right obligation to provide the auditor kit (or any portion of it) to a former workforce member after offboarding, and is that obligation a one-time fulfillment or continuing through the §164.530(j) retention window?**

### Sub-questions

(a) Is the auditor kit a §164.524 "designated record set" subject to individual access right?

(b) If yes: the §164.524 right is to *the individual's PHI*; the kit contains zero PHI by design. Does the access right still attach to a zero-PHI artifact derived from the workforce member's interactions with the substrate?

(c) If §164.524 does not attach: is the BA's §164.530(j) obligation to retain its OWN records (which differ from the practice's records) implicated by former-workforce kit access requests?

(d) If Option A below ships: counsel's view on the practice owner's continuing-approval workload (potentially many requests over the 6-year retention window from multiple former workforce members) — is there a counsel-approved limit on request frequency, or does the right run unbounded?

### Engineering posture today

- Auditor kit gated by a 5-branch authorization function: admin session / client-session cookie + org-owns-site / partner-session + role∈{admin,tech} + sites.partner_id match / legacy portal-session cookie / legacy `?token=` query param (both legacy paths scheduled for deprecation; deprecation telemetry emits on every legacy-path resolution).
- A former workforce member loses session + cookie + token on offboarding. They cannot pull the kit through any extant code path.
- Per-(site, caller) rate limits are bucketed per-caller — an admin's investigation, a partner's pull, and an auditor's download don't compete for the same 10/hr cap.

### Proposed direction (PENDING COUNSEL)

**Option A — counsel approves continuing §164.524 obligation through the §164.530(j) retention window:** Engineering would build a former-workforce personal-access-grants table + magic-link-style endpoint that mints a 7-day token PER REQUEST bound to a former-workforce email, validates against the table, serves the kit (kit-only — no portal-write access). The 7-day window is for a SINGLE fulfillment; the practice's obligation to honor repeat requests runs **continuing** through the §164.530(j) 6-year retention window. Counsel: confirm or override the continuing-obligation framing. **Spec scope counsel should be aware of:** the new table itself is privileged-access-class (it grants kit reads), so it requires (i) RLS treatment paralleling other client-portal tables, (ii) explicit retention policy (we propose: rows persist 6 years from offboarding, aligned with §164.530(j)), (iii) explicit revocation flow if a former member's grant must be withdrawn (our position: practice owner can revoke at any time). **Operational note:** the practice owner approves former-workforce access requests as they arrive; over a 6-year retention window with multiple former workforce members, request volume may accumulate the practice should plan for. **Boundary — engineering does NOT pre-commit to a 6-year serviced-access product obligation unless counsel verdicts Option A.** This is an "if and only if" commitment.

**Option B — counsel approves no §164.524 obligation but §164.530(j) retention applies:** Practice owner downloads the kit on offboarding day, archives it under their own records-retention obligation. No new engineering. Document the practice-side workflow in `docs/RUNBOOKS.md`.

**Option C — counsel approves neither §164.524 nor §164.530(j):** No engineering. Document the legal opinion so future deferred-item triage doesn't re-litigate.

---

## §2 — §164.504(e)(2)(ii)(D) auditor kit cover sheet

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

## §3 — §164.504(e)(2)(ii)(J) deprovision-notice — counsel-approved LETTER content

### Plain-English summary

§164.504(e)(2)(ii)(J) requires BA agreements to obligate the BA to "return or destroy all PHI received from, or created or received by the BA on behalf of, the CE that the BA still maintains in any form" at termination of the contract.

**A live, Ed25519-attested chain event for organization deprovisioning already exists in the substrate.** `org_deprovisioned` is registered in `privileged_access_attestation.py:156` `ALLOWED_EVENTS` and emits from `org_management.py:339, 379, 416` (since Maya P1-1 closure 2026-05-04). What does NOT exist is a customer-facing LETTER that accompanies the event — a counsel-approved §164.504(e)(2)(ii)(J)-compliant artifact the practice owner can hand to a successor BA, an auditor, or a regulator.

This §-question therefore asks counsel **about the LETTER**, not about creating the event. The packet does not ask counsel to bless infrastructure engineering has already shipped under prior precedent.

Three distinct recipient workflows are real and should not be collapsed:

1. **Owner-MSP-swap.** Owner intact, just changing BA. Deprovision letter goes to the practice owner who hands it to the successor BA.
2. **Practice-closure with owner intact.** Practice winds down, owner retains records-retention obligation. Letter goes to the owner who archives it.
3. **Practice-closure with owner incapacitated / deceased / unavailable.** Recipient is the estate executor / state regulator / successor entity. Authentication of the destruction-attestation receipt is non-trivial in this case.

**Engineering's near-term commitment under Option A is to workflows 1 and 2 only**, where the recipient is a deterministically-authenticated practice owner. **Workflow 3 is counsel-scoped and contingent on counsel-specific guidance for the estate-executor / state-regulator / successor-entity recipient class** — engineering treats it as phase-2.

### The §-question

> **What counsel-approved LETTER content, chain-anchor policy, and destruction-attestation copy should accompany the existing `org_deprovisioned` event to satisfy §164.504(e)(2)(ii)(J) for workflows 1 and 2 (owner-MSP-swap / practice-closure-owner-intact), and what authentication pattern would counsel require for workflow 3 (owner-unavailable, recipient is estate-executor / state-regulator / successor-entity)?**

### Sub-questions

(a) Is "return PHI" achievable when PHI is scrubbed at the appliance edge before egress? Our position: by design, PHI is scrubbed at appliance egress before any data reaches OsirisCare central command; central command holds no PHI under normal operation. We are not claiming an absence-proof; we are claiming a designed-and-tested boundary. Counsel: confirm or override.

(b) For "destroy PHI" — does counsel accept the existing Ed25519-signed `org_deprovisioned` chain event + appliance-side wipe receipt as sufficient destruction-attestation, OR does counsel require WORM-deletion of every substrate evidence bundle that referenced the practice's site identifier?

(c) For workflow 3 (owner unavailable), what authentication pattern should the recipient use to retrieve and verify the deprovision letter? Options engineering considered: (i) mirror v2.3's BAA-receipt-auth pattern (mig 283) with the receiving party signing receipt; (ii) use a separate attestation channel with state-regulator-specific authentication; (iii) rely on the public verify endpoint with chain-of-custody verification (lowest engineering, but lowest recipient-side authentication assurance). Counsel's view shapes the engineering.

(d) Should the LETTER be a new MODE of the existing F1 Compliance Attestation Letter template family (counsel-approved content, F1 chain-anchor + presenter-snapshot pattern, served from existing infrastructure) OR a wholly separate template family? Our position: reuse F1 (no double-build); counsel may have a posture preference.

### Engineering posture today

- `org_deprovisioned` chain event: LIVE in `privileged_access_attestation.py:156` ALLOWED_EVENTS. Ed25519-chained. Anchored at the org's primary site (or `client_org:<id>` synthetic anchor for org-level events).
- Emission sites: `org_management.py:339, 379, 416` (and failure paths at 397, 404 with `org_deprovisioned_attestation_failed` / `_attestation_unexpected` alert classes).
- PHI scrubbing at appliance edge is the BAA-level boundary by design. Central command is PHI-free under normal operation.
- WORM evidence is never deleted by design. Per HIPAA §164.530(j) the practice's records-retention obligation is 6 years; the substrate cannot unilaterally delete WORM evidence the practice's auditors may need.
- **What does NOT exist:** customer-facing LETTER, F-series mode for deprovision, workflow-specific recipient differentiation, destruction-attestation copy. This is the engineering scope counsel's verdict commits to building — workflows 1+2 first; workflow 3 contingent.

### Proposed direction (PENDING COUNSEL)

**Option A — counsel approves "existing `org_deprovisioned` event + new F-series LETTER mode is sufficient destruction-attestation":** Engineering would extend the existing F1 Compliance Attestation Letter template (`backend/templates/attestation_letter/letter.html.j2`) with a new mode `kind='deprovision_notice'` — reusing F1's Ed25519-signing + chain-anchor + presenter-snapshot infrastructure (NO new F-series artifact; this is a MODE of F1, not a sixth artifact). The letter renders counsel-approved §164.504(e)(2)(ii)(J)-compliant language. Public verify endpoint extends to verify the deprovision-notice's chain-binding via the existing F4 `/verify/{hash}` pattern.

**Engineering's near-term commitment under Option A:** workflows 1 (owner-MSP-swap) and 2 (practice-closure-owner-intact) ship first as a tight, well-scoped engineering deliverable — both have an authenticated practice-owner as the deterministic recipient. **Workflow 3 (practice-closure-owner-unavailable) is counsel-scoped and identity/auth-dependent — engineering treats it as phase-2 contingent on counsel-specific authentication guidance for the estate-executor / state-regulator / successor-entity recipient class.**

**Option B — counsel approves Option A but ALSO requires WORM-redaction:** Option A + a 30-day-grace WORM-redaction workflow that nukes evidence bundles after the practice's records-retention window. Substantial engineering. **Our read: Option B is dis-preferred** because the WORM-redaction step conflicts with the practice's own §164.530(j) 6-year records-retention obligation — substrate cannot unilaterally delete evidence the practice may need for its own auditors. The CE-side retention obligation outweighs the BA-side destruction-confirmation completeness.

**Option C — counsel rules existing `org_deprovisioned` event ALONE (no new letter) satisfies §164.504(e)(2)(ii)(J):** Engineering documents counsel's interpretation. No new F-series mode. No LETTER. The append-only attested chain event + appliance-side wipe receipt + public verify endpoint are the destruction-attestation. **Our read: this is auditor-unfriendly** because a real HIPAA audit will ask "show me the destruction certificate" and a chain-event row identifier is not a customer-facing document. We surface this option only because counsel may rule it sufficient under the audit-supportive-evidence framing.

---

## §4 — Informational: RT21 cross-org relocate v2.4 status

This section is **informational only — NO NEW ASK**. It captures that the v2.3 packet's §-Q's 1-3 (§164.504(e) permitted-use scope under both BAAs; §164.528 substantive completeness; receiving-org BAA scope) remain open from your 2026-05-06 sign-off. §-Q #4 (opaque-mode email defaults) is closed by v2.4 §A condition #5. The feature is engineering-complete + deployed-but-flag-disabled.

Companion artifact: `21-counsel-briefing-packet-v2.4-2026-05-09.md` (runtime evidence addendum) — sent under separate cover 2026-05-09.

Engineering will continue to hold the flag disabled until you provide written sign-off on whichever subset of §-Q's 1-3 you can approve. The per-customer phase-in option from v2.4 §E remains available at your direction. v2.4 §F's 30-day quiet-window proposal stands; engineering has not requested counsel waiver of it.

---

## §5 — Hand-back format — what counsel returns

For each §-Q 1-3 above, counsel returns:

1. **Verdict:** Option A / B / C / counter-proposal.
2. **Conditions** (if any) that engineering must implement before shipping the chosen option.
3. **Copy** for any customer-facing language (cover sheets, deprovision-notice letter content for workflows 1+2 — workflow 3 follow-up if counsel-scoped, accounting templates, BAA addenda) so engineering interpolates verbatim.
4. **§-citation justification** for the verdict — included in the artifact's disclaimer block.

For §-Q 4 (informational), if you have any reservation about the substrate's commitment to running with cross-org-relocate enabled, the v2.4 §D process commitment gives you explicit accountability surface.

---

## §6 — Engagement logistics

**Scope:** Three §-questions (items 1-3 above), one engagement. Items 1, 2, 3 are independently severable.

**Wave 2 (queued separately):** Engineering is preparing a Wave 2 packet covering the two densely-coupled §164.528-interpretation questions (F6 phase 2 federation accounting + §164.528 path-b summary). Wave 2 will be sent as its own packet so this Wave 1 verdict isn't blocked by the dense interpretive pair.

**Expected counsel timeline:** 30 days from this packet's receipt. Engineering will not ship items 1-3 in any form until counsel returns a written verdict on each. If counsel's response is partial, engineering proceeds on the verdicted items.

**Companion engagement:** This packet does NOT supersede the v2.3 RT21 packet. The five conditions on RT21 sign-off remain as you wrote them on 2026-05-06; v2.4 confirms engineering compliance with them.

**Engineering escalation:** If counsel needs to clarify any of items 1-3 before responding, the engineering escalation path is via the same channel used for the cross-org-relocate packet's v2.x rounds.

---

— OsirisCare engineering
   on behalf of the privacy officer
   2026-05-13
