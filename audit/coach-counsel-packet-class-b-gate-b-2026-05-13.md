# Gate B Class-B 7-lens re-review — counsel-engagement-packet-2026-05-13 (v2)

**Reviewer:** Fresh-context Gate B fork
**Date:** 2026-05-13
**Scope:** Verify the 5 Gate A P0s are closed + scan for new regressions introduced by the rewrite. Copy-level findings from the prior 2-lens fork + Gate A 7-lens fork are NOT re-litigated except to confirm closure.

**Verdict (per lens):**

- Lens 1 Legal-internal: APPROVE
- Lens 2 Medical-technical: APPROVE
- Lens 3 HIPAA-auditor: APPROVE
- Lens 4 Attorney: APPROVE
- Lens 5 Product manager: APPROVE
- Lens 6 Engineering (Steve): APPROVE-WITH-FIXES (one P2 — lockstep count terminology drift)
- Lens 7 Coach: APPROVE-WITH-FIXES (one P2 — residual "Engineering's read" voice slip)

**Overall:** **APPROVE-WITH-FIXES** — all 5 Gate A P0s genuinely closed; no new regressions introduced; two P2 nits the author can fix in 5 min or send as-is without harm. Packet is **sendable to counsel** if author elects to land the P2 cleanups, OR sendable as-is given P2 severity.

---

## Gate A P0 closure verification

| # | Topic | Status | Evidence |
|---|---|---|---|
| 1 | §-Q 5 `org_deprovisioned` double-build | **CLOSED** | Packet line 202 explicitly says "A live, Ed25519-attested chain event for organization deprovisioning **already exists** in the substrate" with file:line citation. Verified: `privileged_access_attestation.py:156` contains `"org_deprovisioned"` in `ALLOWED_EVENTS`; `org_management.py` emits at lines 339, 379, 416. Option A rewritten as "extend existing `org_deprovisioned` with a new F-series mode" (line 236) — asks counsel about the LETTER, not the event. Templates dir verified at `backend/templates/attestation_letter/letter.html.j2` + `kind` pattern verified at `client_attestation_letter.py:360`. |
| 2 | §-Q 4 CSV-only inadequate | **CLOSED** | Option A renamed "**A+** — counsel approves raw-CSV + synthesis-template path (combined)" (line 188). Plain-English summary at line 163 explicitly states "A 5-physician practice does not have an in-house privacy-officer capable of synthesizing a §164.528 accounting from a CSV — they retain OsirisCare/the MSP because they CAN'T." Sub-question (d) at line 177 added for disclaimer revision. RLS posture explicit: "goes through `org_connection` with `tenant_org_isolation` RLS policy on the underlying audit-log view" (line 188). |
| 3 | §-Q 3 determinism-contract wrong | **CLOSED** | Option A at line 149 now says cover sheet is "inside the ZIP using a deterministic timestamp derived from the most recent open-event's `created_at` (not wall-clock). This preserves the byte-determinism contract — two consecutive downloads with no new integrity events produce identical ZIPs." Option B at line 151 clearly delivers sidecar "separately from the kit ZIP." The earlier "sidecar in ZIP, NOT in determinism hash" mis-framing is gone. Verified `_kit_zwrite` primitive at `auditor_kit_zip_primitives.py:45-66` matches the packet's description (`date_time`, `ZIP_DEFLATED`, fixed permissions). |
| 4 | §-Q 1 lockstep-target conflict | **CLOSED** (P2 nit) | Line 65 explicitly resolves: "**Lockstep target:** the proposed `federation_disclosure` event_type is targeted at the existing **`promoted_rule_events` four-list lockstep** (Python EVENT_TYPES in `flywheel_state.py` + `_DISPLAY_METADATA` + runbook + DB CHECK `promoted_rule_events_event_type_check`), NOT the `privileged_access_attestation.ALLOWED_EVENTS` system." Verified `flywheel_state.py:45-48` has `EVENT_TYPES` frozenset with explicit lockstep comment to the CHECK constraint. **P2 nit:** packet says "four-list" while f6 source doc line 68 says "three-list lockstep (Assertion + _DISPLAY_METADATA + runbook + CHECK)." This is counting-convention difference (whether Assertion is counted as a list-runner or as a separate list) not contradictory; engineering should harmonize during implementation, doesn't block counsel send. |
| 5 | §-Q 5 Option C inadequate | **CLOSED** | Gate A recommendation was "remove OR reframe as 'counsel rules deprovision is per-site-status under the existing `org_deprovisioned` event, no new letter required.'" Author chose reframe at line 240: Option C now reads "counsel rules existing `org_deprovisioned` event ALONE (no new letter) satisfies §164.504(e)(2)(ii)(J)" + explicitly acknowledges auditor weakness "Our read: this is auditor-unfriendly... We surface this option only because counsel may rule it sufficient under the audit-supportive-evidence framing." Acceptable per Gate A's "OR reframe" branch. |

**All 5 P0s closed.** No PARTIAL, no NEW-REGRESSION. Author also closed multiple prior-fork P1s in the same pass:

- Carol P0 #1 (compound §-Q 1) — line 48 now reads "Is the act of training-on-Org-A and deploying-to-Org-B a §164.528-eligible disclosure of derived information, **AND if so, may the §164.528 accounting be satisfied by a per-deployment logging entry**..." ✓
- Carol P0 #2 (v2.3 §1.5 framing inheritance) — line 17 explicit + line 56 explicit ✓
- Cover-posture renumbering map (line 13) ✓
- "Our position" voice convergence — 4/6 instances converged; 2 residual slips noted below
- v2.4 §F + §E acknowledged (line 250) ✓
- Severability claim narrowed to "Items 2, 3, 5 are independently severable. Items 1 and 4 share the §164.528 interpretive question" (line 273) ✓
- §-Q 2 attorney-surrogate reframe of (c) to BA-side §164.530(j) (line 101) ✓ ("Reframed per attorney-surrogate note — keep the question on OsirisCare's BA-side obligation, not the CE's")

---

## Lens 1-7 NEW findings (post-rewrite scan)

### Lens 1 Legal-internal — **APPROVE**

Banned-word scan re-run: **1 hit** at line 63 — "100%-false-positive failure mode." This is **NOT** the banned-word class CLAUDE.md targets (legal-narrowness rule prohibits "100%" in **outcome** claims like "100% safe / PHI never leaves"). Here "100%" is a quantitative description of a known design defect's failure rate, which is the **opposite** of the banned class — it's engineering owning a flaw to counsel, not over-claiming a virtue. Maya's Gate A P1 #5 explicitly called for raising this severity language, and the author did so. **Verdict: not a banned-word violation.** Carol would approve.

No other new legal-internal findings.

### Lens 2 Medical-technical — **APPROVE**

§-Q 2 Option A now correctly frames the obligation as "**continuing** through the §164.530(j) 6-year retention window" (line 113) — Gate A medical-technical's P0 closed. The three recipient workflows for §-Q 5 are explicitly enumerated at lines 208-210 (owner-MSP-swap / practice-closure-owner-intact / practice-closure-owner-incapacitated-deceased-unavailable) — Gate A medical-technical's other P0 closed. §-Q 3 Option A now explicitly requires "counsel-supplied explanation copy ... accompanies the status statement" (line 149) addressing the prior "auto-generation without explanation" concern. §-Q 4 plain-English summary at line 163 directly addresses clinic-capability reality. No new findings.

### Lens 3 HIPAA-auditor — **APPROVE**

§-Q 4 Option A+ deliverable shape now satisfies §164.528(a)(2) elements via the synthesis template (line 188). §-Q 5 Option C explicitly framed as auditor-unfriendly (line 240) — auditor would not pick it, but counsel may; appropriate disclosure. §-Q 3 sub-question (b) at line 135 explicitly acknowledges "**a NEW LOCKSTEP between substrate invariants + cover-sheet builder + auditor-kit rendering** — engineering will commit to keeping it in lockstep but needs counsel to scope it as tightly as possible" — addresses the Gate A auditor P1 about implicit-lockstep scope. Sub-question (d) at line 177 closes the §-Q 4 disclaimer-revision contradiction. No new findings.

### Lens 4 Attorney — **APPROVE**

"NOT requesting a re-design" softened at line 17: "We are NOT requesting a re-design of any feature shipped under the precedent set by your 2026-05-06 sign-off (v2.3), but counsel may, in the course of verdicting these items, conclude that a v2.3 framing needs revision — we will follow counsel's lead." Severability claim narrowed correctly at line 273. §-Q 2 sub-question (c) reframed to BA-side at line 101 with explicit "Reframed per attorney-surrogate note" acknowledgement. No new findings.

### Lens 5 Product manager — **APPROVE**

§-Q 1 Option C softened with marketing-copy class acknowledgement at line 73: "Downstream impact engineering will manage: customer-facing copy that describes 'cross-customer pattern learning' or similar would be revised." §-Q 2 operational note at line 113 explicitly surfaces the practice-owner approval workload over the 6-year window. §-Q 5 Option A is now a MODE-of-F1, not a sixth F-series artifact — PM consensus directly addressed: "NO new F-series artifact; this is a MODE of F1, not a sixth artifact" (line 236). §-Q 3 Option B now explicitly flagged "This is a regression to pre-substrate manual workflow — proposed only for completeness; not engineering's recommended path" (line 151). No new findings.

### Lens 6 Engineering (Steve) — **APPROVE-WITH-FIXES**

- **P2 (NEW)** — Line 65 says "**four-list lockstep**" — source doc `f6-phase-2-enforcement-deferred.md` line 68 says "**three-list lockstep**." Counting convention difference (whether the Assertion runner counts as a list or as the consumer of the lists). Not a contradiction with the underlying engineering, but if counsel reads both docs sequentially the inconsistency invites a follow-up question. Recommend harmonizing to whichever count engineering uses in the substrate-invariant code. Doesn't block counsel send; cleanup during implementation phase.
- Engineering posture commonality block at line 19 added — explicitly maps the three architectural patterns (chain-event-plus-artifact / query-endpoint / new-table-plus-magic-link) to the §-Q's. Steve approves the structural clarity.
- §-Q 4 RLS posture now explicit (line 188).
- §-Q 2 spec-scope expansion at line 113 covers RLS / retention / revocation.
- F1 template reuse claim at line 236 verified — `client_attestation_letter.py:360` does use `kind=`, dispatch pattern supports MODE extension.

### Lens 7 Coach — **APPROVE-WITH-FIXES**

- **P2 (NEW)** — Voice convergence is 4/6, not 6/6. Two residual "Engineering's read" / "engineering's read" instances remain:
  - Line 113: "(engineering's read: practice owner can revoke at any time)"
  - Line 224: "Engineering's read: reuse F1 (no double-build); counsel may have a posture preference."
  - Carol's prior-fork P1 #3 wanted convergence to "Our position." These two are clearly inside parenthetical engineering disclosures (not §-question voice), so they're tolerable as "engineering's read" tonally — but Carol's norm was strict. Recommend final replace-all `engineering's read` → `Our position` for full parity, OR explicitly carve out parenthetical engineering-asides as a permitted alternative voice. Either is fine; doesn't block counsel send.
- §-Q 3 Option B explicitly framed as "regression to pre-substrate workflow" (line 151) — Coach P1 closed.
- §-Q 4 Option A+ via existing audit-log view rather than new export path (line 188) — Coach P1 closed.
- §-Q 5 MODE-of-F1 framing closes Coach P0 #1 (line 236).
- Engineering posture commonality block (line 19) closes Coach P0 #2 (sibling parity).

---

## Regression scan — anything the rewrite broke

**None identified.** Specific candidates checked:

1. **Voice consistency** — 2 residual "engineering's read" slips (P2 only).
2. **Lockstep terminology** — four-list (packet) vs three-list (source) (P2 only).
3. **SQL-shape leakage** — packet retains some table/column names (`promoted_rule_events`, `flywheel_state.py`) but these are now explicitly carved out as "Engineering posture today" context rather than mixed into §-question prose. Maya's prior-fork P0 #3 wanted them out of §-question + sub-question; spot-check confirms they're now scoped to the Engineering posture sections + Option A description (where they're load-bearing for counsel to understand the lockstep target). Acceptable.
4. **§-Q 5 Option B WORM-redaction §164.530(j) conflict** — fully elaborated at line 238 ("CE-side retention obligation outweighs the BA-side destruction-confirmation completeness"). Maya prior-fork P2 closed.
5. **§-Q 1 line 64 mig-261 CHECK-constraint defense-in-depth** — verified deleted in rewrite (the prior fork wanted it relocated/removed). Maya P1 closed.
6. **Item 1 (d) sub-question demotion** — original had (a)(b)(c)(d); current has (a)(b)(c). Carol prior-fork P1 #5 closed.
7. **"NO PHI on OsirisCare central command" over-claim** — line 218 now reads "by design, PHI is scrubbed at appliance egress before any data reaches OsirisCare central command; central command holds no PHI under normal operation. **We are not claiming an absence-proof; we are claiming a designed-and-tested boundary.**" Carol prior-fork P1 #4 closed.
8. **"ex-workforce" → "former workforce"** — grep confirms zero "ex-workforce" instances; "former workforce" / "former-workforce" used consistently. Carol prior-fork P1 #6 closed.

No new regressions.

---

## Banned-word scan

```
$ grep -n -i -E "ensure(s|d|ing)?|prevent(s|ed|ing)?|protect(s|ed|ing)?|guarantee(s|d|ing)?|audit-ready|PHI never leaves|100%|continuously monitored" \
    /Users/dad/Documents/Msp_Flakes/audit/counsel-engagement-packet-2026-05-13.md
63:- A cross-org federation-leak substrate invariant is designed but **NOT YET DEPLOYED** because a naive implementation had a 100%-false-positive failure mode on a 2-tenant fleet (see `f6-phase-2-enforcement-deferred.md` §"Cross-org leak invariant — design notes" for the correct UUID-PK JOIN that fixes it).
```

**1 hit, NOT a violation.** The CLAUDE.md "Legal language" rule targets outcome over-claims ("100% safe", "100% effective", "PHI never leaves" as a positive virtue). Here "100%-false-positive failure rate" is engineering disclosing the **failure rate of a known design defect to counsel** — the opposite class. Maya's Gate A P1 #5 explicitly called for raising the severity of this disclosure precisely so counsel doesn't accuse engineering of glossing. Author did the right thing. **Verdict: ✓ pass.**

---

## Final overall recommendation

**APPROVE-WITH-FIXES** — packet is **sendable to counsel as-is** given P2 severity, OR author may land the two 5-minute P2 cleanups before send:

1. **P2 Lens 6** — harmonize "four-list" (packet line 65) vs "three-list" (f6 source line 68) lockstep counting convention.
2. **P2 Lens 7** — converge two residual "engineering's read" voice slips (lines 113 + 224) to "Our position" OR explicitly carve out parenthetical asides.

All 5 Gate A P0s genuinely closed. No new regressions. The rewrite is a substantive improvement that addresses 4 of 7 Gate A lenses' BLOCK verdicts (medical-technical, HIPAA-auditor, engineering, coach) without introducing any new failure modes. Recommend the author send.

**Lock-in:** This is the Gate B artifact for the counsel-engagement packet. If counsel engagement opens before P2 cleanups land, no Gate-C re-review is needed — P2s do not change counsel's verdictable surface.

— Gate B fresh-context fork
   2026-05-13
