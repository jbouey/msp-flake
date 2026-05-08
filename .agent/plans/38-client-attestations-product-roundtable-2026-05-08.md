# Product round-table — ClientAttestations divergence-from-PartnerAttestations UX

**For:** ClientAttestations build (in flight as fork at session-end). Sprint-N+2-class follow-up.
**Date:** 2026-05-08.
**Format:** Product-manager-led adversarial round-table. Industry-expert voices (Maria-practice-owner / Diane-CPA / Janet-OCR-investigator / Brian-MSP-partner) + engineering voices (Steve / Maya / Carol / Coach) + product voice (Sarah-PM).
**Status:** Round-table verdicts inform iteration of the fork's partner-mirror output. Fork commits the baseline; this doc plus a fix-up commit shapes it into the client-appropriate UX.

---

## Why a divergence round-table

The PartnerAttestations tab (Sprint-N+1, commit `de252f7a`) was built for the operator audience — Lisa-MSP-MD on a Monday-morning support call, Tony-MSP-HIPAA-lead pulling a BAA roster for an auditor handoff. It is dense, utility-focused, technician-grade. Its 2-card stacked layout works because the operator already knows what a "portfolio attestation" is.

ClientAttestations serves Maria — a small-practice owner. Maria's mental model is: "I have an insurance underwriter asking for proof I'm HIPAA-compliant." She is NOT a technician. The partner-mirror would feel intimidating, dense, and overly-technical for her. **The same backend artifacts, surfaced differently.**

The user's directive 2026-05-08: *"the client portal will want it displayed differently than a partner — roundtable with product manager for how that should look using industry experts in the field adversarial view"*. This doc IS that round-table.

---

## Design decisions

### D1 — Entry point + nav placement

**Question:** Where does Maria click to find the F1/F3/F5 artifacts?

**Options considered:**
- (a) Top-level nav item `/client/attestations` (same as partner).
- (b) Sub-tab inside `/client/reports` next to "Auditor Kit" + "Monthly Reports".
- (c) Dashboard hero card "Compliance Attestation Letter" with primary CTA + smaller link to all 3 artifacts page.
- (d) Re-frame `/client/reports` itself as the home for ALL artifacts; add 3 sub-tabs.

**Round-table:**
- **Sarah-PM:** PREFER (c) **dashboard hero card** + secondary route. Maria opens the dashboard daily; a hero card with "Issue your latest Compliance Attestation Letter" with a 1-click flow puts the artifact in her path without making her hunt for it. The dedicated `/client/attestations` page exists for power-users who want all 3 artifacts side-by-side.
- **Maria:** STRONG (c). "I don't go to a 'Reports' page. I go to the dashboard. If you want me to print my compliance letter, put it on the front page."
- **Steve:** APPROVE (c) — but lean: don't bloat the dashboard. ONE hero card for F1; a single "View all attestations" link going to the dedicated route. Carol-approved one-liner under the card.
- **Coach:** APPROVE-with-condition: divergence from partner UX MUST be deliberate + documented. Partner has 2-cards-stacked because operators sweep; client has 1-hero-card-with-spillover because owners flow. Pin this in `feedback_consistency_coach_pre_completion_gate.md` as "sibling parity at the BACKEND level (header parity, anchor parity, hash parity); UX divergence at the PRESENTATION level is allowed when justified."

**Verdict: (c) DASHBOARD HERO CARD + DEDICATED ROUTE.** Hero card is the primary path; `/client/attestations` is the secondary "see everything" page.

---

### D2 — Visual density: card layout

**Question:** Partner uses 2 dense cards. Client?

**Options considered:**
- (a) 1:1 mirror partner — 2 cards stacked.
- (b) Single primary card (F1) + secondary lighter "More artifacts" expandable.
- (c) Three separate cards in a 3-column grid for desktop, stacked on mobile.
- (d) Wizard-style flow ("What do you want to do? [issue compliance letter] [see quarterly snapshot] [print wall certificate]").

**Round-table:**
- **Sarah-PM:** PREFER (c) but reorder. **F5 Wall Cert FIRST** (it's the visually-impressive owner-FACING artifact — the wall cert is the only artifact designed to be hung in the practice). F1 second (the working-document letter). F3 third (the quarterly).
- **Maria:** "I want to print the wall certificate to put it in the lobby. That's the thing that says 'we take HIPAA seriously.' The other two are paperwork."
- **Diane-CPA:** AGREE on Maria's framing for owner-facing. But for an auditor handoff, F1 + F3 are the artifacts that matter. The wall cert is decorative.
- **Brian-MSP-partner:** "If Maria is hanging a wall cert, that reflects on us. Make it nice." Carol-approved presenter brand.
- **Janet-OCR-investigator:** WARNING — wall certs that LOOK official can become legal liabilities if the underlying claims drift. Make sure the wall cert disclaimer ("Issued [date]; valid until [date]; verify at [URL]") is prominent on the rendered cert.
- **Steve:** APPROVE (c) with reorder F5/F1/F3 — clean 3-card grid. Mobile: stacked F5/F1/F3.
- **Carol:** APPROVE the reorder; the wall cert disclaimer is already legally-bounded copy (verified in F5 template).
- **Coach:** APPROVE — divergence from partner's F1-then-F3-then-F5 ordering is intentional + product-justified. Pin the rationale in plan body.

**Verdict: (c) 3-CARD GRID, ORDERED F5 / F1 / F3** (wall cert / letter / quarterly). Mobile-stacked. Owner-FACING ordering by emotional resonance, not by issuance dependency.

---

### D3 — Copy register

**Question:** Partner copy is operator-grade ("Aggregate substrate posture across all clinics monitored on a continuous automated schedule"). Client copy?

**Options considered:**
- (a) Same copy as partner.
- (b) Simplified: "Your practice's compliance posture, signed and ready to share."
- (c) Customer-iterated: warmer, more reassuring, less technical.

**Round-table:**
- **Sarah-PM:** PREFER (c). Maria's voice not Lisa's voice.
- **Maria:** "Don't say 'substrate.' Don't say 'attestation' without explaining it. Say 'compliance letter' or 'compliance certificate.' I know what those are."
- **Carol:** APPROVE plain English BUT keep the legal disclaimers verbatim per CLAUDE.md banned-word rules. The card BODY copy can warm; the disclaimer block stays canonical.
- **Janet-OCR-investigator:** WARNING — "compliance certificate" might over-promise. CLAUDE.md banned word "compliant" is borderline. Recommend "compliance evidence" or "compliance attestation letter" — explicit "letter" framing keeps it from sounding like a stamp of approval.
- **Diane-CPA:** AGREE with Janet. Auditors will see this copy if Maria forwards. "Letter" + "evidence" + "monitored on a continuous automated schedule" — all canonical from F1 template.
- **Steve:** APPROVE (c) — body copy warms; disclaimer copy stays per F1 template.
- **Coach:** APPROVE — sibling parity preserved at the disclaimer level (where legal language matters); UX divergence at the body-copy level is allowed.

**Verdict: (c) WARM BODY COPY + CANONICAL DISCLAIMER COPY.** Specific copy proposals:

- Card F5 (Wall Cert): "Print this for the lobby. A landscape compliance certificate showing your practice is monitored by an Ed25519-signed substrate. Includes the verification URL anyone can check."
- Card F1 (Letter): "The working document for auditors, insurance underwriters, and counsel. Each issuance is hash-bound to your evidence chain — auditors can independently verify."
- Card F3 (Quarterly): "A quarterly aggregate of your practice's substrate evidence. File this for §164.530(j) records retention or hand to your annual auditor."

Disclaimer block at the bottom of the page (NOT per-card): canonical F1 disclaimer copy, byte-for-byte parity with the printed PDF disclaimer.

---

### D4 — Verify URL: prominence + framing

**Question:** Partner shows the verify URL with a copy-to-clipboard button as a technical metadata block. Client?

**Round-table:**
- **Sarah-PM:** Maria probably doesn't know what to do with a verify URL by itself. Frame it as "Send this URL to your auditor — they can verify the letter is genuine without contacting OsirisCare."
- **Diane-CPA:** STRONG approve. "When I get a compliance letter from a client, the FIRST thing I want is a URL I can hit to confirm it's not photoshopped. Make that URL obvious."
- **Janet-OCR-investigator:** AGREE. "The verify URL is the difference between 'we say so' and 'cryptographically verifiable.' Highlight it."
- **Maria:** "I'd put that on the cover email. 'Here's my compliance letter, here's the link to verify it.' Make it copy-pasteable."
- **Steve:** APPROVE — render the verify URL with the slug suffix (32-char hash prefix per `feedback_multi_endpoint_header_parity.md`); copy-to-clipboard button; explanatory caption "Send this URL alongside the PDF — recipient can verify cryptographically."
- **Carol:** APPROVE the caption — banned-words-clean.
- **Coach:** APPROVE — caption diverges from partner's terse "Public verify URL" label; the longer client-side caption is justified by Maria's customer mental model.

**Verdict: SURFACE the verify URL with explanatory caption.** Render with slug + copy button + 1-sentence "what this is for" caption.

---

### D5 — F5 disabled-when-no-F1 transition

**Question:** F5 wall cert requires an F1 row to exist. If Maria opens the page before issuing F1, what does she see?

**Round-table:**
- **Sarah-PM:** Wall cert card should be disabled with helpful copy. NOT hidden. Maria should see "this is here, you'll unlock it after issuing your first compliance letter."
- **Maria:** "Tell me what to do, don't just gray it out. 'Issue a compliance letter first to unlock the wall certificate.'"
- **Steve:** APPROVE — disabled button with explanatory text.
- **Coach:** APPROVE — same pattern as PartnerAttestations Card B's "no roster yet" empty-state.

**Verdict: DISABLED-with-actionable-prompt.** Don't hide the F5 card. Show it in a "locked" visual state with text: "Issue a Compliance Attestation Letter (above) to unlock the Wall Certificate. The wall certificate is an alternate render of the same signed payload."

---

### D6 — F2 Privacy Officer designation: link or co-locate

**Question:** F2 Privacy Officer is at `/client/compliance/OfficerDesignation.tsx` today. Should the Attestations page link to it or surface it as a precondition?

**Round-table:**
- **Sarah-PM:** F2 designation is a PRECONDITION for F1 (the Letter PDF requires a PO sign-off line). The Attestations page should detect "no PO designated" and PROMPT Maria to designate one before F1 issuance is even possible.
- **Carol:** STRONG approve. "Without a designated PO, the Attestation Letter is incomplete. The UI should make that gate explicit."
- **Maria:** "I designated a PO when I signed up. If I haven't, it should tell me before I click 'issue.'"
- **Steve:** APPROVE — pre-flight check on F1 issuance: GET `/api/client/privacy-officer` first; if empty, show modal "Designate your Privacy Officer first" with link to `/client/compliance`.
- **Coach:** APPROVE — sibling pattern from F1 backend (which raises 409 if no PO is designated).

**Verdict: PRE-FLIGHT GATE on F1 issuance.** Before POST, check for active PO designation; if none, route Maria to F2 designation flow with a modal explaining why.

---

### D7 — F3 cadence prompt

**Question:** F3 quarterly summary is best-issued every quarter for §164.530(j) records retention. Should the UI nudge Maria?

**Round-table:**
- **Sarah-PM:** Light nudge — yes. Heavy gamification — no. Show a small "Last issued: Q1 2026 (3 months ago)" + "Q2 2026 is now available" timestamp; let Maria decide.
- **Maria:** "Tell me which quarter I should issue. Don't make me figure it out."
- **Diane-CPA:** "An auditor reviewing §164.530(j) wants to see consistent quarterly summaries. Maria SHOULD be reminded."
- **Steve:** APPROVE — quarter-selector dropdown defaults to "previous" (the most-recently-completed quarter, which is what auditors usually want); show "Last issued" timestamp from `partner_ba_compliance_attestations` (no — that's partner; from `quarterly_practice_compliance_summaries` for client).
- **Coach:** APPROVE — divergence from partner UX (partner has no F3-equivalent on its side); justified by audit-cycle product semantics.

**Verdict: QUARTER SELECTOR + LAST-ISSUED TIMESTAMP + DEFAULT TO PREVIOUS-QUARTER.** Light nudge, not nag.

---

### D8 — Mobile + tablet considerations

**Round-table:**
- **Steve:** Maria opens the client portal on her phone after a phone call from her insurance underwriter. The dashboard hero card MUST work on mobile. The 3-card grid stacks vertically below 768px breakpoint.
- **Sarah-PM:** APPROVE — mobile-first design for the dashboard hero; desktop-grid OK for the dedicated `/client/attestations` page.
- **Maria:** "I don't print from my phone, but I want to see what's there." Read-only on mobile is fine; download starts on desktop.

**Verdict: HERO CARD MOBILE-FIRST.** Dedicated page can be desktop-optimized; mobile gets stacked cards.

---

## Summary — implementation deltas from partner-mirror baseline

When the fork returns with the partner-mirror baseline, iterate via a fix-up commit covering these 8 design decisions:

| D | Change |
|---|--------|
| D1 | Add dashboard hero card linking to `/client/attestations`; route stays. |
| D2 | Reorder cards F5 / F1 / F3 (was F1 / F3 / F5 partner-mirror). 3-column grid desktop; stacked mobile. |
| D3 | Body copy rewritten for owner audience; disclaimer block stays canonical. |
| D4 | Verify URL gets explanatory caption "send to your auditor". |
| D5 | F5 disabled-with-prompt instead of hidden. |
| D6 | F1 issuance pre-flight: check PO designation; modal redirect to F2 if missing. |
| D7 | F3 quarter selector + "Last issued" timestamp + default to "previous". |
| D8 | Mobile-first dashboard hero card. |

**Per-gate round-table on the fix-up commit** (mirrors the established rule):
- Steve, Maya, Carol, Coach + Sarah-PM at design gates; Maria + Diane + Janet voices captured here as durable design rationale.

---

## Companion artifacts

- `mcp-server/central-command/frontend/src/partner/PartnerAttestations.tsx` — partner-mirror baseline (sibling).
- `mcp-server/central-command/frontend/src/client/ClientReports.tsx` — existing 2-tab pattern; extending to 3 tabs is the alternative-route option (D1 path b).
- `feedback_round_table_at_gates_enterprise.md` — per-gate gate rule.
- `feedback_multi_endpoint_header_parity.md` — sibling-parity rule (preserved at backend; UX-divergent).
- `feedback_consistency_coach_pre_completion_gate.md` — coach 17-dim sweep applies to ship.
