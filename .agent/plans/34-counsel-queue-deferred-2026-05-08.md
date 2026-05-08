# Counsel Queue — Deferred §-Questions (2026-05-08)

**For:** Outside HIPAA counsel (companion to existing engagements)
**From:** OsirisCare engineering, on behalf of the privacy officer
**Date:** 2026-05-08
**Version:** v1 (initial draft for counsel review)
**Status:** Engineering-side scaffolding complete for the queue; each item below has a concrete engineering posture today AND a proposed direction once counsel signs off. None of these items block the printable-artifact sprint shipped 2026-05-08 (F1, F2, F4, P-F5, P-F7 + today's P-F6, P-F8, F3, F5).

**Companion artifacts:**
- `21-counsel-briefing-packet-2026-05-06.md` — cross-org site relocate (RT21) v2.3 — counsel-approved precedent for how OsirisCare frames §-questions to outside HIPAA counsel.
- `docs/HIPAA_FRAMEWORK.md` — substrate posture overview.
- F1 (commit `721008af`) + P-F6 (commit `9a92b402`) — the two attested artifacts most directly affected by counsel decisions on items 1-4.

---

## Posture statement

This packet enumerates **four deferred §-questions** that surfaced during the printable-artifact sprint (F1+F2+F4+P-F5+P-F6+P-F7+P-F8+F3+F5) but which we explicitly chose NOT to ship engineering for until counsel weighs in. Engineering already has *opinions* on each — those opinions are documented as "proposed direction" — but we recognize the legal interpretation is yours. Each item is independently severable: counsel can answer Q1 without binding Q2, etc.

**What we are NOT asking:** we are NOT asking for a re-design of the engineering shipped to date. F1's "current state attestation" framing, F2's privacy-officer designation chain, F4's public-verify endpoint, P-F6's three-party-BAA-chain artifact, and P-F8's read-only timeline have all shipped under the precedent set by your 2026-05-06 cross-org-relocate approval.

**What we ARE asking:** legal interpretation on the four §-questions below so we know whether to ship engineering, modify it, or defer indefinitely.

---

## Item 1 — §164.524 ex-workforce kit access

### The §-question

When a workforce member of a covered entity (a practice owner who originally designated the Privacy Officer; an employed physician with §164.524 access right; an ex-employee with continuing §164.524 access entitlement) leaves the practice or has their portal access revoked, **does the practice retain a HIPAA §164.524 individual-access-right obligation to provide that ex-workforce member with the auditor kit (or any portion of it)?**

The auditor kit is "audit-supportive technical evidence" — it is NOT a §164.528 disclosure accounting (per CLAUDE.md "Auditor-kit framing"). It contains: bundle hashes, pubkeys, ots receipts, identity chain, ISO CA bundle, README explaining how to verify. It does NOT contain PHI.

**Sub-questions:**
- (a) Is the auditor kit a §164.524 "designated record set" subject to individual access right?
- (b) If yes, what is the §164.524 retention/access-window obligation? (The right is to *the individual's PHI*; the kit contains zero PHI per design.)
- (c) If no, is there a §164.530(j) records-retention obligation that compels the practice to keep ex-workforce-readable copies for 6 years?

### Engineering posture today

- Auditor kit is gated by `require_evidence_view_access` (5-branch auth: admin session / `osiris_client_session` cookie + org-owns-site / `osiris_partner_session` + role∈{admin,tech} + sites.partner_id match / legacy `portal_session` cookie / legacy `?token=` query param with deprecation telemetry).
- An ex-workforce member loses session + cookie + token on offboarding. They cannot pull the kit.
- Per-site rate limits are bucketed per-caller so an ex-employee returning months later via an admin-issued one-time link doesn't compete with the practice's own auditor pulls.

### Proposed direction (PENDING COUNSEL)

**Option A (counsel-approves §164.524 obligation):** Build a `client_signup_personal_access_grants` table with: `client_org_id, ex_workforce_email, granted_by_user_id, scope_kit_only_no_phi, expires_at`. Build a magic-link-style endpoint that mints a 7-day token bound to an ex-workforce-email, validates against the table, and serves the auditor kit (kit only — no portal-write access). 7-day token because §164.524 says "30 days" but our analysis is the practice's obligation is to *PROVIDE* not to *grant indefinite access*; one 7-day fetch satisfies the right.

**Option B (counsel-approves no §164.524 obligation, but §164.530(j) retention):** Practice owner downloads the kit on offboarding day, archives it under their own records-retention obligation. No new engineering. Document the practice-side workflow in `docs/RUNBOOKS.md`.

**Option C (counsel-approves neither):** No engineering. Document the legal opinion in CLAUDE.md so future deferred-item triage doesn't re-litigate.

---

## Item 2 — §164.504(e)(2)(ii)(D) auditor cover sheet

### The §-question

§164.504(e)(2)(ii)(D) requires Business Associates to **"report to the covered entity any use or disclosure of the information not provided for by its contract"** ("breach notification" in colloquial terms). When OsirisCare detects a substrate-level integrity event (an Ed25519 signature mismatch on a chain replay; an OTS anchor that fails to confirm in the expected window; a `cross_org_relocate_chain_orphan` invariant tripping), **does the auditor kit need a cover sheet flagging "this kit was generated during an open §164.504(e)(2)(ii)(D)-eligible event window" so the auditor sees the disclosure obligation up-front, before they evaluate the kit's substantive contents?**

**Sub-questions:**
- (a) Is a substrate-level integrity event automatically a §164.504(e)(2)(ii)(D) "use or disclosure not provided for by its contract"? Our position is no — a signature mismatch may be a chain-of-custody concern WITHOUT being a disclosure. We want counsel's view.
- (b) If counsel's view is that some classes of integrity events ARE §164.504(e)(2)(ii)(D)-eligible, which classes, and what is the cover-sheet copy you'd accept?
- (c) Should the cover sheet be generated automatically from substrate invariants (`docs/lessons/sessions-218.md` lists the invariants pinned today), or should the practice's privacy officer manually attach the cover sheet?

### Engineering posture today

- Substrate invariants run every 60s (`project_substrate_integrity_engine.md`). Failures are visible at `/admin/substrate-health` to platform engineering, NOT to the practice.
- Auditor kit reads: chain (live recompute) → `pubkeys.json` → `ots/*.ots` files → README. No cover-sheet generation today.
- The kit is byte-deterministic across downloads (per CLAUDE.md "Auditor-kit determinism contract") so adding a cover sheet would require versioning the determinism contract OR scoping the cover sheet OUTSIDE the deterministic ZIP envelope (e.g. as a separate file with a wall-clock timestamp, or as a JSON sidecar that's not part of the determinism hash).

### Proposed direction (PENDING COUNSEL)

**Option A (counsel-approves automated cover sheet for class C of integrity events):** Add `auditor_kit_cover_sheet.j2` template + builder that runs at kit generation time, queries open substrate-integrity events, renders a 1-page §164.504(e)(2)(ii)(D) status-of-knowledge statement, AND adds it as a `cover_sheet.pdf` SIDECAR file (NOT part of the determinism hash; signed separately with the same Ed25519 key + own attestation row in a new `auditor_kit_cover_sheets` table). Sidecar shape preserves byte-determinism for the substantive kit.

**Option B (counsel-approves manual privacy-officer cover sheet):** PO uploads a cover sheet via a new partner/portal endpoint; we attach as sidecar without auto-generation. Lighter engineering; heavier PO workflow.

**Option C (counsel-approves no cover sheet):** No engineering. The kit's existing disclaimer ("audit-supportive evidence; not a §164.528 disclosure accounting") covers the framing.

---

## Item 3 — §164.528 disclosure-accounting path (b)

### The §-question

§164.528 enumerates two paths for "accounting of disclosures": (a) the standard accounting; (b) the alternative summary for repetitive-disclosure scenarios (≥5 disclosures to the same recipient for the same purpose). The auditor kit + F1 attestation letter + P-F6 BA Compliance Letter all carry the disclaimer "this is NOT a §164.528 disclosure accounting." The disclaimer was approved by counsel in the cross-org-relocate packet (item 21) v2 round.

**The deferred §-question:** if a practice owner specifically *requests* a §164.528 accounting via OsirisCare, **does engineering build path (a) or path (b), and what is the trigger for path (b) eligibility?**

**Sub-questions:**
- (a) What is the path-(b) eligibility trigger you'd accept? Our reading: "≥5 disclosures to same recipient for same purpose within 1 calendar year" — but §164.528(b)(3) language is dense and we want your read.
- (b) For path (a), what is the OsirisCare-side data shape that satisfies the §164.528(a)(1)(i)-(vi) elements? We have `admin_audit_log` rows for every privileged access event (operator-initiated kit pulls, cross-org-relocate flag flips, BA-roster mutations); what additional columns are needed?
- (c) Is the §164.528 accounting the practice's obligation OR ours-on-behalf-of? Our reading: practice's, OsirisCare provides raw data. But this depends on the BAA scope.

### Engineering posture today

- `admin_audit_log` (mig 084-ish) records every privileged action with `(user_id, username, action, target, details, ip_address, created_at)`.
- `compliance_bundles` records every evidence event Ed25519-signed.
- Neither table has a `disclosed_to_recipient_email` column or `disclosure_purpose` column. §164.528 path (a) requires those.

### Proposed direction (PENDING COUNSEL)

**Option A (counsel-approves OsirisCare provides path (a) raw data, practice synthesizes accounting):** Build a `GET /api/client/disclosure-accounting/raw` endpoint that returns CSV of admin_audit_log rows filtered by `target` matching practice site_ids, plus a separate `disclosure_recipients` registry that the practice maintains. Practice owner produces the accounting from the CSV.

**Option B (counsel-approves OsirisCare produces path (a) accounting directly):** Add `disclosure_recipient_email` + `disclosure_purpose` to admin_audit_log + a new `disclosure_accounting_requests` workflow with PO sign-off. Heavier engineering.

**Option C (counsel-approves path (b) summary for repetitive disclosures):** Build `disclosure_accounting_summary.j2` template that aggregates "8 disclosures to insurance-underwriter@bigcarrier.com between 2026-Q1 and 2026-Q2 for purpose: re-credentialing review."

---

## Item 4 — §164.504(e)(2)(ii)(J) deprovision-notice

### The §-question

§164.504(e)(2)(ii)(J) requires BA agreements to obligate the BA to **"return or destroy all PHI received from, or created or received by the BA on behalf of, the CE that the BA still maintains in any form"** at termination of the contract.

**The deferred §-question:** When a practice (CE) terminates its OsirisCare BAA — through partner-MSP-swap, practice closure, or active opt-out — **what is engineering's deprovision-notice flow that satisfies §164.504(e)(2)(ii)(J), and how does counsel want OsirisCare to PROVE the return/destruction step to the practice owner?**

**Sub-questions:**
- (a) Is "return PHI" achievable when PHI is scrubbed at the appliance edge before egress? Our position: there is NO PHI on OsirisCare central command to return. Counsel: confirm.
- (b) For "destroy PHI" — does counsel accept Ed25519-signed substrate attestation that scrubbed-only telemetry was retained, plus appliance-side wipe receipt? OR does counsel require WORM-deletion of every substrate evidence bundle that referenced the practice's site_id?
- (c) Is the deprovision-notice chain anchored in the cryptographic evidence chain (proposed: `practice_deprovision_notice` event with `target_practice` + `effective_at` + counsel-approved language) OR in the append-only `admin_audit_log` only?

### Engineering posture today

- PHI scrubbing at appliance edge is the BAA-level boundary (CLAUDE.md "PHI scrubbing at appliance egress (Session 185)"). Central command is PHI-free.
- No deprovision-notice event today. A practice that closes its account leaves orphaned site_ids that get marked `status='inactive'`.
- WORM evidence is never deleted (compliance_bundles + WORM bucket). Per HIPAA §164.530(j) the practice's records-retention obligation is 6 years; OsirisCare cannot unilaterally delete WORM evidence the practice's auditors may need.

### Proposed direction (PENDING COUNSEL)

**Option A (counsel-approves "PHI scrubbed = nothing to return, attest-only destroy"):** Build `practice_deprovision_notice` event in `privileged_access_attestation::ALLOWED_EVENTS`, anchor at `client_org:{client_org_id}`, render a §164.504(e)(2)(ii)(J)-compliant Letter "OsirisCare Deprovision Notice" that the practice owner can hand to their successor BA / auditor as proof. Add to F1/P-F6/F-series printable-artifact ensemble.

**Option B (counsel-approves option A but ALSO requires WORM-deletion):** Build option A + a 30-day-grace WORM-redaction workflow that nukes evidence bundles after the practice's records-retention window. Substantial engineering; conflicts with §164.530(j) downstream.

**Option C (counsel-approves "no deprovision notice needed beyond status='inactive'"):** No new engineering. Document in CLAUDE.md.

---

## Hand-back — what counsel returns

For each item 1-4 above, counsel returns:
1. **Verdict:** Option A / B / C / counter-proposal.
2. **Conditions** (if any) that engineering must implement before shipping the chosen option.
3. **Copy** for any customer-facing language (cover sheets, deprovision notices, accounting templates) so engineering interpolates verbatim.
4. **§-citation justification** for the verdict — included in the artifact's disclaimer block (mirrors how cross-org-relocate v2.3 §164.504(e) language was approved).

---

## What this packet does NOT cover

- F3 (Quarterly Practice Compliance Summary) — shipped today; uses F1 framing.
- F5 (Wall Cert + ClientDashboard print stylesheet) — shipped today; reuses F1 attestation row.
- P-F9 (Partner Profitability Packet) — blocked on Stripe Connect, not on counsel.
- The cross-org-relocate v2.3 packet (item 21) — counsel-approved 2026-05-06; closed.
- Substrate-level integrity invariants — operational, not legal.

---

## Questions back to engineering

If counsel needs to clarify any of items 1-4 before responding, the engineering escalation path is:

1. Read the linked artifact (F1, P-F6, etc.) at the commit SHA cited.
2. Pull `git log --oneline | head -20` for the most recent ship state.
3. Reach out via the cross-org-relocate packet's existing channel.

We expect a counsel response within 30 days; engineering will not ship items 1-4 in any form until counsel returns. Until then, the existing disclaimers in F1/P-F6/auditor-kit hold.
