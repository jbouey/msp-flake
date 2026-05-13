# Counsel Engagement Packet — Wave 2 — §164.528-Coupled Interpretive Pair (QUEUED, send after Wave 1 verdict)

**For:** Outside HIPAA counsel
**From:** OsirisCare engineering, on behalf of the privacy officer
**Date:** [SEND DATE — after Wave 1 verdict received]
**Version:** Wave 2 v1 (Class-B 7-lens internally-reviewed; routed as separate wave per counsel guidance 2026-05-13)
**Engagement type:** Wave 2 of a 2-wave engagement. **Wave 2 contains the two densely-coupled §164.528-interpretation questions** that share the same statutory reading (Tier 2 federation per-deployment logging vs path-(a)/(b) accounting shape). Counsel may verdict them jointly.
**Status:** QUEUED. **DO NOT SEND** until Wave 1 verdict has been received and engineering has had opportunity to revise these items against any §164.528 framing counsel established in Wave 1.

**Reason for separation from Wave 1:** Per counsel's 2026-05-13 guidance, sending these dense interpretive questions in the same packet as Wave 1's concrete artifact-shaped questions risks slow-walking the entire engagement. Wave 2 is sent as its own focused packet so each wave can verdict on its own merit.

**Companion artifacts (sent with this Wave 2 packet):**

1. Wave 1 packet `counsel-engagement-packet-wave-1-2026-05-13.md` (informational — counsel's Wave 1 verdict may set framing precedent for Wave 2).
2. `f6-phase-2-enforcement-deferred.md` — v1 source of item 1 below.
3. `34-counsel-queue-deferred-2026-05-08.md` — v1 source of item 2 (source item 3 maps to Wave-2 item 2).

> **Cover posture:** This is an evidence-grade compliance attestation substrate. Items 1 and 2 below share the §164.528 interpretive question; counsel may verdict them jointly. All proposed directions operate under counsel's seven hard rules laid down 2026-05-13.

---

## §0 — Two §-questions at a glance (jointly verdictable)

| # | §-question | Section | Engineering posture today | Proposed direction (PENDING COUNSEL) |
|---|---|---|---|---|
| 1 | F6 phase 2 Tier 2 platform-aggregated federation — is the act of training-on-Org-A and deploying-to-Org-B a §164.528-eligible disclosure of derived information, AND if so, may the §164.528 accounting be satisfied by a per-deployment logging entry rather than a per-patient accounting? Does BAA language need amendment to authorize cross-customer pattern federation? | §1 | Tier 0 + Tier 1 read-path live; Tier 2 WRITE-path **not shipped** awaiting this verdict. | A: counsel approves with §164.528 disclosure-accounting (per-deployment logging via the `promoted_rule_events` ledger lockstep) + BAA addendum template / B: approves with logging-only, no addendum / C: declines — Tier 0 + Tier 1 continue; "cross-customer pattern learning" marketing copy revised. |
| 2 | §164.528 disclosure-accounting — if a practice owner requests an accounting via OsirisCare, do we build path (a) standard or path (b) repetitive-disclosure summary? Does the existing "NOT a §164.528 accounting" disclaimer on F1/P-F6/auditor-kit need revision if path (a) or (b) ships? | §2 | The privileged-access audit log records every privileged action. No disclosure-recipient or disclosure-purpose columns today. No accounting endpoint today. | **A+: raw-CSV endpoint + §164.528(a)(2)-mapped synthesis template (engineering's preferred steady state — tooling-plus-template posture)** / B: OsirisCare produces accounting directly with PO sign-off workflow (strategic shift toward managed compliance operations; engineering does NOT pursue without explicit business-strategy direction) / C: path (b) summary template only. |

---

## §1 — F6 phase 2 Tier 2 federation — §164.528 disclosure-accounting

[Engineering will reproduce the §1 content from the original v2 consolidated packet here, with any framing adjustments learned from Wave 1 counsel verdict.]

**Hold for Wave 1 verdict before drafting final §1 content.** The Wave 1 verdict on §164.504(e)(2)(ii)(D) cover sheet (item 2) and §164.504(e)(2)(ii)(J) deprovision-notice (item 3) may establish framing patterns that should propagate forward into Wave 2's §-question phrasing.

---

## §2 — §164.528 disclosure-accounting path (b)

[Engineering will reproduce the §4 content from the original v2 consolidated packet here. Engineering's preferred steady state is **Option A+** (tooling-plus-template). Option B is a managed-compliance-operations shift that engineering will NOT pursue without business-strategy direction.]

**Hold for Wave 1 verdict before drafting final §2 content.**

---

## §3 — Engagement logistics

**Scope:** Two §-questions (items 1-2 above), one engagement. Items are jointly verdictable on the shared §164.528 interpretive question; engineering will not ship either in any form until counsel returns written verdict.

**Severability note:** Engineering acknowledges items 1 and 2 share the §164.528 interpretive question; counsel may verdict them jointly. Severability claim is narrowed compared to Wave 1.

**Wave 1 precedence:** Engineering treats any §164.528 framing established in Wave 1's verdict (particularly cover-sheet status statements or deprovision-attestation copy) as precedent that Wave 2's directions must conform to.

---

— OsirisCare engineering
   on behalf of the privacy officer
   [SEND DATE — after Wave 1 verdict]
