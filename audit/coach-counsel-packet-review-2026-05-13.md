# Carol + Maya adversarial review — counsel-engagement-packet-2026-05-13
**Reviewer:** Fresh-context fork (Carol HIPAA-counsel-surrogate lens + Maya security/privacy lens)
**Date:** 2026-05-13
**Verdict:** **APPROVE-WITH-FIXES** — six P0 / P1 issues identified; none require a packet re-design, all are surgical edits the author can land in <30 min before sending. Sending as-is risks one wasted counsel cycle (≈30 days) on Carol P0 #1 alone.

---

## Carol findings

### P0 — Item 1 §-question (line 25 + line 46) silently re-scopes the deferred question vs the f6-phase-2 source

The source doc `f6-phase-2-enforcement-deferred.md` §Q3 asks **two** things: (a) is BAA language sufficient or does it need amendment, AND (b) "**does §164.528 accounting need to be per-patient or can it be reduced to a logging requirement.**" Item 1 in the packet at lines 25 + 46 only foregrounds the disclosure-vs-non-disclosure question. The "per-patient vs logging" reduction question is buried as sub-question (b) at line 52 instead of being co-equal in the §-question header.

**Counsel impact:** Counsel reads the bold §-question first; if (b) is read as a sub-bullet, counsel may treat it as a clarification not a stand-alone interpretive ask, and return a verdict that doesn't speak to it. Engineering then has to file a follow-up packet for the missing half. This is exactly the wasted-cycle outcome the audit warned against.

**Fix:** rewrite the §-Q at line 46 as a compound: "Is the act of training-on-Org-A and deploying-to-Org-B a §164.528-eligible disclosure of derived information, AND if so, may the §164.528 accounting be satisfied by a per-deployment logging entry rather than a per-patient accounting?"

### P0 — "Audit-supportive technical evidence" framing is in quotes (line 54) but reads as packet copy, not as a CLAUDE.md-pinned framing

Line 54 says "...or is existing BAA language sufficient under the substrate's "audit-supportive technical evidence" framing?" Counsel may read this as engineering coining a framing on the fly to evade §164.528. The CLAUDE.md "Auditor-kit framing" rule and v2.3 packet §1.5 already establish this as the project's standing ontology — packet must say so explicitly to inherit the precedent.

**Fix:** rewrite line 54 to "…under the substrate's standing 'audit-supportive technical evidence' framing (established in v2.3 §1.5 and pinned in OsirisCare engineering's customer-facing-copy standards)…" Same edit at line 86 ("per CLAUDE.md 'Auditor-kit framing'") — CLAUDE.md is an internal file counsel can't grep; cite the v2.3 packet section instead.

### P1 — Inconsistent voice across items: "engineering's reading" / "engineering's position" / "Our position" mixed

- Line 40: "engineering's reading is"
- Line 108: "engineering's reading is"
- Line 128: "Our position is"
- Line 162: "Engineering's reading:"
- Line 166: "Engineering's reading:"
- Line 198: "Engineering's position:"

Carol-norm: pick **one**. Per v2.3 packet pattern (which counsel already accepted), the canonical phrasing is "**Our position**". Apply replace-all.

### P1 — Item 5 sub-question (a) at line 198 contains an over-claim that counsel will flag

Line 198: "**Our position: there is NO PHI on OsirisCare central command to return.**" Strong universal claim. The accurate claim is that PHI is scrubbed at appliance egress (best-effort, 14 patterns, never claimed 100%); central command is PHI-free by design but not by mathematical proof. A single regex miss in `phiscrub` would falsify "NO PHI." Counsel will hold the packet open until engineering walks this back.

**Fix:** rewrite as "Our position: by design, PHI is scrubbed at appliance egress before any data reaches OsirisCare central command; central command holds no PHI under normal operation. We are not claiming an absence-proof; we are claiming a designed-and-tested boundary."

### P1 — Sub-question lettering parity ASYMMETRY

- Item 1: (a)(b)(c)(d) — four
- Item 2: (a)(b)(c) — three
- Item 3: (a)(b)(c) — three
- Item 4: (a)(b)(c) — three
- Item 5: (a)(b)(c) — three

Item 1 has a (d) at line 56 that is NOT a §-question — it's an engineering proposal disguised as a sub-question ("engineering's mental model is X — does this match what counsel would require?"). Carol expects sub-questions to be questions, not proposals seeking ratification.

**Fix:** demote line 56 (d) out of "Sub-questions" and into "Proposed direction" as a clarifying note under Option A. Sub-question count then matches across all 5 items.

### P1 — Banned phrasing pattern: "ex-workforce" used 8 times (lines 84, 92, 96, 98, 102, 103, 108) — Carol-norm prefers "former workforce member"

Counsel-facing copy norm: "ex-workforce" reads as colloquial; counsel artifacts say "former workforce member" or "former member of the covered entity's workforce." This is not a banned-word, but it's a recurring tone slip that suggests the packet was drafted by engineering not legal review.

**Fix:** replace-all "ex-workforce member" → "former workforce member"; "ex-workforce-email" → "former-workforce email" or rename the column.

### P2 — §6 (informational, line 220-226) leaks scope creep

Line 226 says "Engineering will continue to hold the flag disabled until you provide written sign-off on whichever subset of §-Q's 1-3 you can approve. Any partial sign-off + the flag CHECK constraint mechanism lets us flip the flag per-customer rather than fleet-wide; if a phased approach helps your interpretation, we can provide that as an option."

The "per-customer flag flip" mechanism is described in v2.4 §E line 152-156 already. Repeating it here as an *option* may read as engineering re-opening a discussion that v2.4 already framed as a closed offering. Counsel asked for **no new ask** in §6.

**Fix:** trim line 226 to just "Engineering will continue to hold the flag disabled until you provide written sign-off on whichever subset of §-Q's 1-3 you can approve. The per-customer phase-in option from v2.4 §E remains available at your direction." Don't re-propose; reference.

### P2 — Cover posture paragraph (line 17) is missing the v2.3 "metadata not PHI" framing inheritance

v2.3 §1.5 establishes the data-classification ontology that the entire substrate's customer-facing copy operates under. The new packet's cover posture (line 17) doesn't inherit that framing explicitly, which means a counsel reviewer who comes to this packet *without* re-reading v2.3 may forget the ontological frame.

**Fix:** add one sentence at the end of line 17: "All five §-questions below operate under the v2.3 §1.5 data-classification framing (compliance metadata treated conservatively as PHI-adjacent; PHI scrubbed at appliance egress)."

### P2 — "We are NOT asking" line at 17 reads strong but lacks the v2.3 anchor

v2.3 packet's posture statement (line 28 of v2.3) explicitly cites "the engineering shipped to date" + the §-citation-justification mirror. The new packet says "any feature shipped before this date" — vague. Counsel asked in v2.3 for engineering not to re-litigate; this packet must mirror that exact framing.

**Fix:** rewrite line 17's closing sentence: "We are NOT asking for a re-design of any feature shipped under the precedent set by your 2026-05-06 sign-off (v2.3)."

---

## Maya findings

### P0 — Item 5 Option A line 212 claims `practice_deprovision_notice` event can be added to `privileged_access_attestation::ALLOWED_EVENTS` anchored at `client_org:{client_org_id}` — verified feasible BUT the packet doesn't warn counsel that this requires the THREE-LIST LOCKSTEP

CLAUDE.md "Three lists MUST stay in lockstep" rule: `fleet_cli.PRIVILEGED_ORDER_TYPES`, `privileged_access_attestation.ALLOWED_EVENTS`, `migration v_privileged_types`. The packet at line 212 proposes adding ONLY to ALLOWED_EVENTS. The proposed `practice_deprovision_notice` is an admin-API event, not a fleet_order — so per the existing precedent (line 79 in `privileged_access_attestation.py`: `break_glass_passphrase_retrieval` is in ALLOWED_EVENTS but NOT in the other two lists; allowed because ALLOWED_EVENTS ⊇ the others). Item 1 Option A line 68 has the same shape (`federation_disclosure`).

**This is feasible — but the packet's silence on the asymmetry rule means counsel may approve language that engineering then has to walk back when they discover the lockstep migration is required.** The v2.3 packet §3 line 329 spent two bullet points calling out exactly this asymmetry; the new packet doesn't.

**Verification:**
```bash
grep -nA3 'ALLOWED_EVENTS\s*=' \
  /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend/privileged_access_attestation.py \
  | head -100
# Confirms `break_glass_passphrase_retrieval` is admin-API only, lockstep checker
# permits ALLOWED_EVENTS ⊇ PRIVILEGED_ORDER_TYPES + v_privileged_types.
```

**Fix:** Add one sentence under each of Item 1 Option A (line 68) and Item 5 Option A (line 212): "(Asymmetric three-list lockstep — `ALLOWED_EVENTS` only, not the fleet-order lists; mirrors the `break_glass_passphrase_retrieval` precedent.)"

### P0 — Item 2 line 102 claim of "5-branch auth" is factually correct BUT the description of branch (5) inverts the deprecation telemetry direction

Packet line 102: "legacy `?token=` query param with deprecation telemetry"

Verified against `mcp-server/central-command/backend/evidence_chain.py:204-216`: the legacy `?token=` IS supported AND it does emit deprecation telemetry. **Factually accurate.**

However, the packet doesn't tell counsel that branches (4) and (5) are scheduled for removal (per the `evidence_chain.py:213` "scheduled for removal" comment). Counsel reading this may believe the 5-branch auth is the steady-state surface. If counsel approves Option A based on a 5-branch model and engineering then deprecates branches 4 + 5, counsel's verdict may need re-litigation.

**Fix:** rewrite line 102's last clause: "...legacy `portal_session` cookie / legacy `?token=` query param (both scheduled for deprecation; deprecation telemetry emits on every legacy-path resolution per `evidence_chain.py:204`)."

### P0 — Item 1 sub-question (b) line 52 leaks an internal SQL shape `promoted_rule_events.federation_disclosure`

Maya threat-model lens: counsel-facing packets should describe **what** engineering does, not the **SQL column shape**. Line 52 names a specific column on a specific table. If this packet is ever shared with a competing vendor or an attacker who's grepping for substrate internals, this is a single grep that maps the federation event flow directly to the table they should target.

**Fix:** rewrite line 52 as "...can the accounting be reduced to a 'logging requirement' (e.g. a per-cross-org-deployment event row in the existing privileged-access audit log)..."

Apply the same rule to line 56's `client_orgs.federation_tier_2_consent JSONB`, line 68's `client_orgs.federation_tier_2_consent JSONB` again, line 142's `auditor_kit_cover_sheets` table, line 178's `disclosure_recipient_email`, and line 212's `client_org:{client_org_id}` anchor namespace. **None of these need to leak; describe the function not the table.**

### P1 — Item 3 Option A line 142 proposes a `cover_sheet.pdf` sidecar that's "NOT part of the determinism hash" — verified compatible with the determinism contract, BUT the packet doesn't tell counsel about the determinism-contract constraint

CLAUDE.md "Auditor-kit determinism contract" pins byte-determinism as the load-bearing tamper-evidence promise. Adding a sidecar PDF with a wall-clock timestamp is a substantive change to the kit's auditor-facing shape. Counsel approving Option A is implicitly approving the deviation. The packet must explicitly acknowledge: "this option adds a new file class to the auditor kit that is intentionally NOT part of the byte-determinism contract; counsel sign-off on Option A includes acknowledgement of this architectural exception."

**Fix:** Add to Option A description: "Note: the sidecar's wall-clock timestamp intentionally falls OUTSIDE the kit's determinism contract; counsel sign-off on Option A includes acknowledgement of this architectural carve-out."

### P1 — Item 1 line 64 contains a claim that has a runtime mismatch with item-1's stated "Tier 2 not shipped" framing

Line 64 says "Migration 261's CHECK constraints (`flywheel_tier_org_isolation_required`, `flywheel_tier_distinct_orgs_required_when_calibrated`) ALREADY enforce isolation at the schema level — even if engineering accidentally writes Tier-2 code, the DB rejects the row."

This is **true** but it's a weird sentence to put in a counsel-facing packet because:
1. It reads as engineering defending against its own bug class — which raises a "why would engineering accidentally write that?" question counsel doesn't need to ponder.
2. It implicitly contradicts the "WRITE-path not shipped, feature flag does not exist" framing on line 62.

**Verification:**
```bash
grep -n -E "flywheel_tier_org_isolation_required|flywheel_tier_distinct_orgs_required" \
  /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend/migrations/261_flywheel_eligibility_tiers.sql
# Confirms the CHECK constraints exist (verified line 117 referenced in mig 261).
```

Engineering's intent here is the defense-in-depth story. Carol can read this as "engineering shipped half a Tier 2 already." Maya reading: leave the defense-in-depth story for the post-counsel-approval implementation packet, NOT in the §-question packet.

**Fix:** delete line 64, or move it to a separate "Engineering defense-in-depth" sub-section *after* "Proposed direction (PENDING COUNSEL)" with a clear "this is informational only, not part of the §-question" header.

### P1 — Item 4 Option A line 176 endpoint path `GET /api/client/disclosure-accounting/raw` is a NEW endpoint that doesn't exist today and the packet doesn't say so

Maya lens: counsel may assume this endpoint exists (the packet describes it in declarative shape). If counsel approves Option A and asks "test it before flip," engineering has nothing to test. Per the runtime-evidence-required closeout rule (v2.4 §D), every engineering claim must be runtime-verifiable; "proposed direction" claims are exempt from runtime evidence but the proposal language should clearly mark the endpoint as "WOULD BE BUILT" not implicitly extant.

**Verification:**
```bash
grep -rn "disclosure-accounting" \
  /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend/ \
  2>/dev/null
# Expected: zero results — confirming the endpoint doesn't exist.
```

**Fix:** rewrite line 176 to start: "Engineering would build `GET /api/client/disclosure-accounting/raw` (does not exist today)..." Same shape for line 178 Option B columns ("Would add `disclosure_recipient_email`...").

### P1 — Item 1 line 63 references `f6-phase-2-enforcement-deferred.md §"Cross-org leak invariant — design notes"` — verified the section exists, but the packet's framing of "naive JOIN it would use has a Cartesian-product class" UNDER-states the prod severity

Source `f6-phase-2-enforcement-deferred.md` lines 254-267 quote: "false-positive rate on a 2-tenant fleet would approach 100% of all orders" + "this is exactly the wolf-crying-wolf failure mode the trip-wire is supposed to PREVENT." The new packet line 63 says "Cartesian-product class" only — too clinical. A counsel reader can't infer the operational severity.

This is a Maya-edge-case finding: the under-statement reads as engineering glossing over a known design defect when speaking to counsel. Adversarial counsel may interpret it as "what else is being under-stated?"

**Fix:** rewrite line 63 to: "...is designed but **NOT YET DEPLOYED** because a naive implementation had a 100%-false-positive failure mode on a 2-tenant fleet (see `f6-phase-2-enforcement-deferred.md` §'Cross-org leak invariant — design notes' for the correct UUID-PK JOIN that fixes it)."

### P2 — Item 5 Option B line 214 describes a "WORM-redaction" workflow that "conflicts with §164.530(j) downstream"

This is engineering naming the operational conflict but burying it in 5 words. Counsel will pick up on this and ask for elaboration; if engineering has to elaborate in a follow-up, that's a wasted half-cycle. Either (a) elaborate now (one paragraph) or (b) recommend Option B's denial in the proposed direction.

**Fix:** add to Option B: "Engineering's read: Option B is dis-preferred because the WORM-redaction step conflicts with the practice's own §164.530(j) 6-year records-retention obligation — substrate cannot unilaterally delete evidence the practice may need for its own auditors."

### P2 — Three places say "pending counsel" / "PENDING COUNSEL" / "Pending counsel"

- Line 23 (header): "Proposed direction (PENDING COUNSEL)"
- Line 25 (table): "(PENDING COUNSEL)"
- Lines 66, 106, 140, 174, 210 (per-item): "(PENDING COUNSEL)"

Capitalization is consistent (good). But the table header on line 23 says "Proposed direction" while the per-item headers say "Proposed direction (PENDING COUNSEL)" — minor sloppy.

**Fix:** Make line 23 read identically: "Proposed direction (PENDING COUNSEL)".

---

## Drift from source documents

### Drift from `34-counsel-queue-deferred-2026-05-08.md`

- **Source has 4 items; packet has 5.** Source item 1 (§164.524) → packet item 2. Source item 2 (cover sheet) → packet item 3. Source item 3 (§164.528) → packet item 4. Source item 4 (deprovision) → packet item 5. Packet item 1 is NEW (from `f6-phase-2-enforcement-deferred.md`). The renumbering is intentional but the packet doesn't say so explicitly — could confuse counsel cross-referencing.
  - **Fix:** in the cover posture (line 17), add: "Items 2-5 below originate from `34-counsel-queue-deferred-2026-05-08.md` items 1-4 respectively (renumbered for packet sequencing). Item 1 originates from `f6-phase-2-enforcement-deferred.md`."

- **Source item 4 (deprovision) sub-question (b) line 121 says "OR does counsel require WORM-deletion."** Packet item 5 sub-question (b) line 200 reproduces this exactly. **No drift.** ✓

- **Source item 3 (§164.528) sub-question (a) line 91 says "≥5 disclosures to same recipient for same purpose within 1 calendar year."** Packet item 4 sub-question (a) line 162 reproduces with same numeric threshold. **No drift.** ✓

### Drift from `f6-phase-2-enforcement-deferred.md`

- **Source §"Why deferred — four open questions"** has 4 sub-questions (Q1-Q4). Packet item 1 has 4 sub-questions (a-d). The mapping isn't 1:1: source Q1 (Tier 1 non-operator-posture) doesn't appear in the packet at all. Source Q2 (Tier 2 non-operator-posture) is implicit in packet (a). Source Q3 (§164.528 disclosure accounting) is packet (a)+(b). Source Q4 (engineering-discipline framework) is omitted.

  **This is fine if intentional** — the packet is asking the §164.528 + BAA question only; the non-operator-posture question is engineering-class not legal-class. But the packet should say so explicitly.

  **Fix:** add to Item 1 "Engineering posture today" section a line: "The non-operator-posture question (source `f6-phase-2-enforcement-deferred.md` Q1/Q2) is engineering-class and not asked of counsel; engineering's verdict is no Tier 2 ships absent explicit per-org consent."

- **Source line 39 mentions "Privileged-Access Chain of Custody section in CLAUDE.md" as the pattern Tier 2 would need to mirror.** Packet doesn't reference this anywhere. **Minor drift.**
  - **Fix:** add to Item 1 Option A (line 68): "...mirrors the existing Privileged-Access Chain of Custody pattern (client identity → policy approval → execution → attestation)."

### Drift from `21-counsel-briefing-packet-v2.4-2026-05-09.md`

- **v2.4 §A condition #5 line 31 says "Opaque-mode email defaults".** Packet §6 line 222 says "§-Q #4 (opaque-mode email defaults) is closed by v2.4 §A condition #5." **No drift.** ✓

- **v2.4 §F (line 159) proposes a 30-day quiet window before flip.** Packet §6 doesn't reference this. **Minor drift** — counsel may have read v2.4 §F as a commitment and expect to see it acknowledged.
  - **Fix:** in §6 add: "v2.4 §F's 30-day quiet-window proposal stands; engineering has not requested counsel waiver of it."

### Drift from `21-counsel-briefing-packet-2026-05-06.md` (v2.3)

- **v2.3 §"Posture" (line 28) uses the exact phrase "This is an evidence-grade compliance attestation substrate. Engineering has built and tested...".** Packet line 17 uses "This is an evidence-grade compliance attestation substrate. Engineering has *opinions*..." — **slight tonal drift.** "Has opinions" reads softer than "has built and tested." If the new packet wants to inherit the v2.3 posture exactly (and it should, per Carol P0 #2), use the v2.3 phrasing pattern.

  **Fix:** rewrite line 17 to mirror v2.3: "This is an evidence-grade compliance attestation substrate. Engineering has documented opinions on each of the five §-questions below..." (use "documented" not "has").

---

## Banned-word scan

Performed via:
```bash
grep -n -i -E "ensure(s|d|ing)?|prevent(s|ed|ing)?|protect(s|ed|ing)?|guarantee(s|d|ing)?|audit-ready|PHI never leaves|100%|continuously monitored" \
  /Users/dad/Documents/Msp_Flakes/audit/counsel-engagement-packet-2026-05-13.md
```

**Result: ZERO hits.** ✓ The packet passes the banned-word scan. (Verified separately with case-sensitive runs for "ensures", "prevents", "protects", "guarantees", "audit-ready", "PHI never leaves", "100%", "continuously monitored" — all zero.)

**Note** — line 198's "**Our position: there is NO PHI on OsirisCare central command to return.**" is a near-miss universal-claim shape that, while not a banned word, has the same risk profile (counsel will challenge as un-provable). See Carol P1 #4 above.

**Note** — the packet does say "audit-supportive" (lines 54 + 86 + 146) which IS the correct phrasing per CLAUDE.md "Legal language" rule. ✓

---

## Reproducible verification commands

```bash
# Maya P0 #1 — verify three-list lockstep is the canonical pattern (and break_glass_passphrase_retrieval precedent for admin-API-only events)
grep -nA3 'ALLOWED_EVENTS\s*=' \
  /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend/privileged_access_attestation.py | head -40

# Maya P0 #2 — verify 5-branch auth is current shape AND that branches 4+5 are scheduled for deprecation
grep -nA60 'async def require_evidence_view_access(' \
  /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend/evidence_chain.py | head -80

# Maya P1 #3 — verify mig 261 CHECK constraints exist as packet claims
grep -nE 'flywheel_tier_org_isolation_required|flywheel_tier_distinct_orgs_required' \
  /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend/migrations/261_flywheel_eligibility_tiers.sql

# Maya P1 #4 — verify packet's claimed endpoint does NOT exist today
grep -rn "disclosure-accounting" \
  /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend/ \
  2>/dev/null
# Expected output: zero matches → confirms Item 4 Option A's endpoint is proposal-only.

# Maya P1 #5 — verify Tier 2 / federation_tier_2 is NOT in the codebase
grep -rnE "tier_2|federation_tier_2" \
  /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend/ \
  2>/dev/null
# Expected: zero hits on actual implementations (only doc/comment refs allowed).

# Carol P0 #1 — verify f6-phase-2 source asks per-patient question
grep -nE "per-patient|per patient" \
  /Users/dad/Documents/Msp_Flakes/.agent/plans/f6-phase-2-enforcement-deferred.md

# Carol banned-word scan — re-run after edits
grep -n -i -E "ensure(s|d|ing)?|prevent(s|ed|ing)?|protect(s|ed|ing)?|guarantee(s|d|ing)?|audit-ready|PHI never leaves|100%|continuously monitored" \
  /Users/dad/Documents/Msp_Flakes/audit/counsel-engagement-packet-2026-05-13.md
# Expected: zero hits, both pre- and post-edit.

# Maya P0 #3 — verify WORM bucket exists (Item 5 cites it)
grep -rnE "WORM_ENABLED|WORM_RETENTION_DAYS" \
  /Users/dad/Documents/Msp_Flakes/mcp-server/central-command/backend/ \
  2>/dev/null | head -10
# Expected: confirms WORM is a live feature flag in agent_api.py + evidence_chain.py.
```

---

## Final recommendation

**APPROVE-WITH-FIXES.** The packet's spine is sound — five severable §-questions, parallel Option A/B/C structure, no banned words, inherits v2.3 posture by reference. The fixes below are surgical (<30 min total) and prevent a wasted counsel cycle on the Carol P0 #1 §-Q reshape.

### REQUIRED edits before send (P0):

1. **Carol P0 #1** — line 46: compound the §-Q to include the per-patient-vs-logging reduction question explicitly.
2. **Carol P0 #2** — lines 54 + 86: cite the v2.3 §1.5 precedent for "audit-supportive technical evidence" framing instead of bare-quote it.
3. **Maya P0 #1** — lines 68 + 212: add the asymmetric-three-list-lockstep note for Tier-2 federation event + practice-deprovision event.
4. **Maya P0 #2** — line 102: add the "scheduled for deprecation" qualifier to branches 4 + 5 of `require_evidence_view_access`.
5. **Maya P0 #3** — lines 52, 56, 68, 142, 178, 212: strip exact SQL column / table / endpoint names from §-question + sub-question prose. Describe the function not the schema. (Retain them in "Engineering posture today" sections only.)

### STRONGLY RECOMMENDED edits before send (P1):

6. **Carol P1 #3** — replace-all "engineering's reading" / "Our position" / "Engineering's position" → "Our position" (5 lines).
7. **Carol P1 #4** — line 198: walk back the "NO PHI" universal claim.
8. **Carol P1 #5** — demote line 56 (d) from sub-question to proposal-note; restores (a)(b)(c) parity across all 5 items.
9. **Carol P1 #6** — replace-all "ex-workforce" → "former workforce" (8 sites).
10. **Maya P1 #2** — line 142: add the determinism-contract carve-out acknowledgement.
11. **Maya P1 #3** — line 64: delete or relocate the mig-261 CHECK-constraint defense-in-depth bullet.
12. **Maya P1 #4** — lines 176, 178: clearly mark proposed endpoints + columns as "would be built" not implicitly extant.
13. **Maya P1 #5** — line 63: surface the 100%-false-positive operational severity from the source doc.

### NICE TO HAVE (P2):

14. **Carol P2 #7** — line 226: trim "per-customer phase-in" re-proposal; reference v2.4 §E instead.
15. **Carol P2 #8** — line 17: explicitly cite the v2.3 §1.5 metadata-vs-PHI framing.
16. **Carol P2 #9** — line 17: rewrite "any feature shipped before this date" → "any feature shipped under the precedent set by your 2026-05-06 sign-off."
17. **Carol P2 — drift fix** — line 17: explicit item-renumbering map for source-doc cross-reference.
18. **Maya P2 #6** — line 214: elaborate Option B's §164.530(j) downstream conflict.
19. **Maya P2 #7** — line 23: harmonize "Proposed direction" header capitalization with per-item subheaders.
20. **Maya P2 — drift fix** — §6: acknowledge v2.4 §F's 30-day quiet-window proposal still stands.

### Estimated rework effort: 20–30 min for the P0+P1 list. P2 list is 10 min additional.

### Strongly do NOT send the packet as-is. Carol P0 #1 alone will cost an entire 30-day counsel cycle if counsel returns an §164.528 verdict that doesn't address the per-patient-vs-logging reduction question.
