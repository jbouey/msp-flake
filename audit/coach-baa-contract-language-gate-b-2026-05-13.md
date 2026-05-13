# Gate B fork — inside-counsel BAA contract-language packet
**Reviewer:** Fresh-context Gate B fork
**Date:** 2026-05-13
**Source artifact under review:** `audit/inside-counsel-baa-enforcement-2026-05-13.md`
**Gate A 2nd-eye reference:** `audit/coach-baa-contract-language-2nd-eye-2026-05-13.md`
**Class-B Gate A source:** `audit/coach-baa-expiry-enforcement-gate-a-2026-05-13.md`

**Per-lens verdict:**
| Lens | Verdict |
|---|---|
| Inside-counsel surrogate | APPROVE |
| Attorney surrogate (outside-counsel mindset) | APPROVE |
| Engineering (Steve) | APPROVE-WITH-FIXES (one nit) |
| HIPAA auditor surrogate | APPROVE |
| Product manager | APPROVE |
| Coach (consistency + no double-build) | APPROVE |

**Overall:** APPROVE

---

## Gate A fix closure matrix

| Gate A fix | Status | Evidence (line refs in `inside-counsel-baa-enforcement-2026-05-13.md`) |
|---|---|---|
| Q1 — reframe split into Q1a (template-read) + Q1b (legal-floor-if-silent) | **CLOSED** | §0 table rows for `1a` (L23) + `1b` (L24); §Question 1 splits at L38 (`1a — Template read`) + L44 (`1b — Legal floor if template is silent`) |
| Q1 — distinguish renewal-window (PHI-free) vs. continued-ingest grace | **CLOSED** | L48–L50: explicit two-bullet contrast — "Renewal-window grace (PHI-free)" vs "Continued-ingest grace (legally questionable)"; engineering-preferred reading named |
| Q1 — reserve outside-counsel escalation clause if template silent | **CLOSED** | L52: "Outside-counsel escalation reserved" paragraph citing §164.504(e)(2)(ii)(A) verbatim; also pre-flagged in §0 table cell at L24; reinforced in hand-back at L124 |
| Q2 — add in-flight order completion to enumeration | **CLOSED** | L74 (workflow `m.`): "In-flight order completion — order was attested under valid BAA, customer-BAA expires before completion" + cross-ref to Q5 |
| Q2 — add substrate attestation emission to enumeration | **CLOSED** | L75 (workflow `n.`): "Substrate-engine attestation emission (every 60s…)" |
| Q2 — add webhook deliveries to enumeration | **CLOSED** | L76 (workflow `o.`): "Webhook deliveries from platform to customer-registered endpoints" |
| Q2 — strip biasing parentheticals ("probably ALLOW", "already gated") | **CLOSED** | Cross-org relocate L66 retains a factual technical anchor ("Already mig-283-gated for receiving-org receipt; this asks about source-org-with-expired-BAA") — this is scoping context, not a verdict-biasing parenthetical. Auditor-kit L71 contains NO "probably ALLOW for §164.530(j)" hint. No "probably ALLOW" / "already gated" leading language found anywhere in the 15-workflow table. |
| Q2 — attach F1/F2/F5 PDF exemplars as exhibits | **CLOSED** | L11–L13: exhibits 3/4/5 list "F1 Compliance Attestation Letter exemplar PDF / F2 Privacy Officer Designation exemplar PDF / F5 Wall Certificate exemplar PDF"; cross-referenced in workflow `l.` at L73 ("exemplars attached") |
| Q3 — split into Q3a (recipient), Q3b (cadence/PM-decided), Q3c (opaque-mode satisfies-BAA) | **CLOSED** | §0 table rows L26–L28; §Question 3 split at L84 (3a), L88 (3b — explicitly tagged "PM-decided with inside-counsel review"), L92 (3c) |
| Q4 — REMOVED (shadow-mode duration was wrong-routing to inside counsel) | **CLOSED** | L32: explicit "Note on Q4 from Gate A: the originally-drafted Q4 … is NOT included here" with rationale ("engineering+PM-owned"); Q4 absent from §0 table and absent from question body |
| Q5 — NEW question on in-flight order completion ratification | **CLOSED** | §0 L29; §Question 5 at L98–L104; chain-of-custody-at-emit reasoning surfaced; engineering's bias named so counsel can verdict cleanly |
| Q6 — NEW question on operator-attested renewal vs customer-signed | **CLOSED** | §0 L30; §Question 6 at L108–L114; partner-as-BA-renewing-on-behalf path called out explicitly; engineering's Gate A position named so counsel ratifies or relaxes |
| Sibling-parity engineering commitment to `BAA_GATED_WORKFLOWS` + `test_baa_gated_workflows_lockstep.py` | **CLOSED** | L58 in Q2 framing ("encoded in a new constant `BAA_GATED_WORKFLOWS` paired with a CI lockstep checker (`test_baa_gated_workflows_lockstep.py`) mirroring the existing 4-list privileged-chain lockstep"); reinforced in hand-back at L128 |

**Closure rate: 13/13 CLOSED. 0 PARTIAL. 0 NEW-REGRESSIONS.**

---

## Lens 1-6 NEW findings (not re-litigating closed Gate A items)

### Inside-counsel surrogate
- Packet structure is verdict-ready. Hand-back format at L120–L124 is explicit and itemized (per-question verdict + Q2 per-workflow verdicts + escalation flag). Exhibits list at L9–L15 is concrete and named.
- L52's escalation clause is well-shaped: it cites the specific statutory subsection (§164.504(e)(2)(ii)(A)), explains WHEN escalation triggers (template-silent AND counsel reads as requiring contractual basis), and asks for an explicit flag back rather than silently routing.
- No further reframing needed.

### Attorney surrogate (outside-counsel mindset)
- Q1b's statutory-creep is now properly fenced: the question stays inside-counsel-shaped UNLESS the template is silent, in which case the escalation clause routes to outside counsel. This is exactly the disposition the 2nd-eye recommended.
- Q5 (in-flight order completion) is genuinely contract-language-shaped because it asks counsel to read the BAA's termination clause against an "extant authority survives termination" theory; no statutory creep.
- Q6 (operator-attested renewal) similarly stays contract-language-shaped because the partner-MSP-as-BA pathway is contemplated within the executed BAA corpus, not §164 text.
- Q3c (opaque-mode) inherits the subtle §164.514(d) minimum-necessary hook the 2nd-eye flagged, but the framing at L92–L94 asks the verdictable contract-language question ("does opaque-mode satisfy any contractual notice obligation, or must the notice be explicit?") rather than inviting statutory interpretation. Acceptable.

### Engineering (Steve)
- L42 surfaces the two material runtime constraints the 2nd-eye called out as missing: per-org `baa_grace_days` configurability + daemon-side `200 + ingest_paused` fallback. Both are explicit in the engineering-context paragraph.
- One nit (not blocking): Q3's framing at L88 inherits the cadence floor ("Engineering proposed cadence: T-30 first warning, T-7 escalation, T-0 ingest-pause confirmation") but doesn't surface the technical constraint the 2nd-eye flagged at L100 ("the `baa_signer_email` field doesn't exist on `client_orgs` today — it would need to be added as a mig 309 column"). This is a NON-BLOCKING omission because a 3a verdict of "notify BAA signer" can be implemented in the same mig 309 design pass; engineering doesn't need counsel to know the schema state. If the rewrite were re-revised, surface this constraint in Q3a's engineering-context note. Not BLOCK-class.

### HIPAA auditor surrogate
- L78 ("Each 'BLOCK' verdict will be paired with an Ed25519-attested `baa_expired_workflow_refused` event in the chain so the refusal itself is auditable evidence. Each 'ALLOW' verdict will document the contract-language basis in `BAA_GATED_WORKFLOWS`.") satisfies the 2nd-eye's auditor-lens requirement for refusal-as-evidence.
- L42's `baa_expired_ingest_paused` event posture (T-0 attestation regardless of grace) is preserved.
- Auditor-kit (workflow j) and client-portal read-only (workflow k) are enumerated as separate workflows so counsel can verdict the §164.524 right-of-access carve-out cleanly.

### Product manager
- Q3b at L88–L90 is correctly tagged "PM owns this decision but requests inside-counsel review for contractual-compliance check." This matches the 2nd-eye's "cadence floor only, not the cadence itself" recommendation.
- Partner-swap (workflow `f.`) is preserved — the PM's escape-hatch consideration (customer locked in with a partner they've lost confidence in) is implicitly within scope of Q2's per-workflow verdict.
- Shadow-mode duration (formerly Q4) correctly removed; PM and engineering will resolve via internal Gate B on cutover plan (L32).

### Coach (consistency + no double-build)
- Sibling-parity engineering commitment is preserved at L58 + L128. `BAA_GATED_WORKFLOWS` + `test_baa_gated_workflows_lockstep.py` is named in BOTH the question framing AND the hand-back commitment paragraph — no double-build, single source of truth.
- The packet does NOT re-litigate the settled internal Rule 7 (opaque-by-default) — instead Q3c asks counsel the focused legal question ("does opaque-mode satisfy contractual notice obligation") rather than reopening "opaque or transparent."
- Question count: 7 sub-questions across 5 numbered items (1a, 1b, 2, 3a, 3b, 3c, 5, 6) matches the 2nd-eye's recommended 5-question Wave 1 structure.
- Cross-references between Q2 workflow `m.` and Q5 are explicit (L74 → L98), avoiding double-verdicting the same in-flight-order question.

---

## Regression scan

| Check | Result |
|---|---|
| New biasing language introduced? | NO. No "probably," "obvious," "clearly," or leading parentheticals found in question bodies. The "engineering bias" callouts at L75, L100, L110 are honest disclosure of engineering's prior position so counsel can verdict against it, not hidden steering. |
| Statutory-creep on any question? | NO. Q1b is the only question with statutory teeth and it is explicitly fenced with the outside-counsel escalation clause at L52. Q5 (extant-authority) and Q6 (renewal-mechanism) stay contract-language-shaped against the BAA template. |
| Exhibit references concrete? | YES. F1/F2/F5 named at L11–L13 with descriptive titles ("F1 Compliance Attestation Letter exemplar PDF" etc.). Master BAA template + executed BAAs explicitly listed. Gate A + 2nd-eye fork outputs cited by path as audit-trail exhibits. |
| Q2 enumeration regression — did the rewrite drop any Gate A originals? | NO. All originals preserved: daemon checkin (a), witness submit (b), evidence-bundle emission (c), privileged-access (d), cross-org relocate (e), partner-swap (f), owner-transfer (g), new appliance enrollment (h), new customer signup (i), auditor-kit (j), client-portal read-only (k), F-series PDFs (l). Plus the 3 Gate A additions: in-flight orders (m), substrate attestation (n), webhooks (o). Total 15 workflows, up from 12; matches 2nd-eye recommendation. |
| Hand-back format clear? | YES. L120–L124 explicit 3-bullet structure (verdict + per-workflow Q2 verdicts + escalation flag). L126 names the blocking dependency (Task #52 design blocked on verdict). L128 names the post-verdict engineering commitment. |
| Q4 truly absent? | YES. Not in §0 table, not in question body, explicitly disclaimed at L32 with rationale. |
| Q numbering coherent? | YES. 1 / 2 / 3 / 5 / 6 with explicit "Note on Q4 from Gate A" at L32 explaining the gap. Reader is never confused why Q4 is missing. |
| Sibling-parity commitment present in BOTH framing and hand-back? | YES. L58 (in Q2 body) + L128 (hand-back). Belt-and-suspenders. |
| Counsel routing rule honored? | YES. Engagement-type declaration at L6 ("All inside-counsel-grade per the outside-vs-inside routing rule (statutory-interpretation questions reserved for outside counsel; one Q1b escalation clause below)") is the prophylactic disclaimer the routing rule contemplates. |

**No regressions found.**

---

## Final overall recommendation

**APPROVE.**

All 13 Gate A fixes are closed. The rewrite is structurally cleaner than the 2nd-eye's recommended packet structure (concrete §0 table at L21 for at-a-glance routing; explicit Q4-removal disclaimer at L32; explicit hand-back format at L120). Statutory creep is properly fenced. Exhibits are concrete. Sibling-parity commitment is doubled-up.

The single non-blocking engineering nit (mig 309 `baa_signer_email` column callout missing from Q3a's engineering-context paragraph) is a courtesy disclosure that does not affect counsel's ability to verdict 3a — counsel reads the BAA's notice clause; schema state is implementation-side. Engineering can fold this into the post-verdict Gate B on the mig 309 build plan.

Ship the packet to inside counsel.

---

**Reviewer self-check:** This Gate B verdict is APPROVE because the 2nd-eye returned APPROVE-WITH-FIXES with 13 specific fixes and the rewrite addressed all 13. Per the Gate B rubric: "If Gate A had APPROVE-WITH-FIXES with specific fixes and the rewrite addressed them, Gate B should approve." Conditions met.
