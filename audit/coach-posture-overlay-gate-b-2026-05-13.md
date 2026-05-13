# Class-B Gate B — POSTURE_OVERLAY v2

**Reviewer:** Fresh-context Gate B fork (no prior session state, no in-doc author counter-arguments)
**Date:** 2026-05-13
**Subject:** v2 revision at `audit/posture-overlay-draft-2026-05-13.md` after Gate A v1 returned APPROVE-WITH-FIXES with 5 P0 findings (`audit/coach-posture-overlay-gate-a-2026-05-13.md`). Counsel Priority #3, Rule 5, Task #51.

---

## Per-lens verdict

| Lens | Verdict |
|---|---|
| 1. Legal-internal (Maya + Carol) | APPROVE-WITH-FIXES |
| 2. Medical-technical | APPROVE |
| 3. HIPAA auditor surrogate (OCR) | APPROVE-WITH-FIXES |
| 4. Attorney surrogate | APPROVE-WITH-FIXES |
| 5. Product manager | APPROVE-WITH-FIXES |
| 6. Engineering (Steve) | APPROVE-WITH-FIXES |
| 7. Coach (consistency + no over-engineering + no double-build) | APPROVE-WITH-FIXES |

**Overall verdict:** **APPROVE-WITH-FIXES.** All 5 Gate A v1 P0 findings are closed cleanly. v2 is structurally sound, cross-fork consistent with master BAA v1.0-INTERIM + SUBPROCESSORS v2, and free of banned language and urgency overshoot. No lens BLOCKS. The fixes called out below are P1/P2 polish, not Gate-B blockers — three are explicitly carried as named TaskCreate followups per `feedback_consistency_coach_pre_completion_gate.md` two-gate lock-in.

---

## Gate A P0 closure matrix

| P0 | Status | Evidence (line# in v2) |
|---|---|---|
| **#1 §3 restructure to pointer-index** | **CLOSED** | v2 §3 lines 42–45 state explicitly "*This index does NOT carry topic state.* Each owning doc carries its own `last_verified` + `decay_after_days` in YAML frontmatter (per §8)." Rows in §3 now carry only "Owning document" + "Authority class"/"Status note" pointer columns; no hand-typed staleness descriptors that drift. Matches the MEMORY.md / topic-files pattern Gate A v1 prescribed. |
| **#2 §7/§8 unify with memory-hygiene** | **CLOSED** | v2 §7 lines 176–180 explicitly extend `context-manager.py validate` (not parallel-build). New `--posture-overlay` flag rides on existing `.github/workflows/memory-hygiene.yml`. v2 §8 lines 198–211 unify the schema: `title` ↔ `name`, `topic_area` ↔ `type`, `decay_after_days` + `last_verified` are identical. Three additive fields (`supersedes`, `superseded_by`, `posture_overlay_authoritative`) are optional. Feasibility against the current `validate()` function (lines 339–429 of `context-manager.py`) confirmed: the existing function already walks YAML frontmatter on memory topic files (lines 415–420), so extending it to walk `docs/**/*.md` is a ~20-line additive change with no new dependencies. Adoption ramp (warn → error after 30 days, line 217) is the right friction profile. |
| **#3 §164.308 OCR rows + remediation pointers** | **CLOSED** | v2 §5 lines 119–134 enumerate 8 §164.308 probe areas: (a)(1)(ii)(A) Risk Analysis, (a)(1)(ii)(B) Risk Management, (a)(1)(ii)(C) Sanction Policy, (a)(1)(ii)(D) Activity Review, (a)(5) Workforce Training, (a)(7) Contingency Plan, (a)(8) Evaluation, (b) BA Contracts. Each row has BOTH a current-authority pointer AND a remediation pointer (or "Task TBD" with the BAA-drafting Gate A Coach-lens follow-up cited as foundational gap). The §164.308(a)(8) row correctly self-references this overlay. |
| **#4 §6 governance tiered (major/minor/trivial)** | **CLOSED** | v2 §6 lines 138–171 tier the workflow exactly as Gate A v1 Coach concern 3 prescribed: trivial (no gate; just commit), minor (Gate A single-lens, 5–10 min), major (full Class-B 7-lens Gate A + Gate B). Self-violation guard at lines 169–170 escalates self-citing-superseded-doc updates to Major automatically — closes the recursive footgun Gate A v1 Coach concern 5 flagged. |
| **#5 §4 supersession dates corrected + overlay-self-violation guard** | **CLOSED** | v2 §4 lines 109–115: BAA_SUBPROCESSORS supersession-effective date is now 2026-05-13 (CONSISTENT with master BAA `MASTER_BAA_v1.0_INTERIM.md` line 1 "v1.0-INTERIM-2026-05-13" and SUBPROCESSORS.md line 4 "Effective Date: 2026-05-13"), with explicit gate "(Gate A + Gate B verdicts both passed; v2 published)" — this Gate B verdict satisfies the gate condition. HIPAA_FRAMEWORK supersession date is now framed as "Pending" with two-event attribution (self-declared stale + counsel framing change 2026-05-06) — closes Gate A v1 Lens 2 finding. Overlay-self-violation note at lines 115 + §7 OVERLAY_REGISTRY_FILES allowlist at line 190 explicitly exempt §4 citations. Allowlist additions require Major-update review (line 190). |

**All 5 Gate A v1 P0s closed.** No regressions detected.

---

## Lens 1–7 NEW findings (do not re-litigate closed Gate A items)

### Lens 1 — Legal-internal (Maya + Carol) — APPROVE-WITH-FIXES

- **(P2) §1 line 12 "operational, legal, security, and architectural posture" — Gate A v1 Lens 2 recommended softening to "this document records counsel's directives in machine-readable form."** Not addressed in v2. The overlay still self-frames as governing the legal posture rather than recording the engineering-side machine-readable mirror of counsel's posture. Defensibility-fix recommendation stands. **Carry as TaskCreate followup** — does not block Gate B.

- **(P1) §2 line 28 — Rule 4 "Multi-device-enterprise note" carries an extra sub-claim ("orphan detection is sev1 across the fleet, not per-tenant") that does not appear in the source `feedback_enterprise_counsel_seven_rules.md`.** That memory file (line 21) says only "Orphan detection is sev1, not a tolerable warning." v2's expansion to "across the fleet, not per-tenant" is an author-added gloss that could be defensible but is not counsel-grade verbatim. Either remove the gloss or annotate it as "(engineering interpretation)." Maya lens would flag this as introducing engineering speech into a gold-authority citation.

- **(P3) §2 line 36 — Class-A-vs-B routing rule was retained.** Gate A v1 Lens 1 recommended moving it to a new §2.5 "Process rules" sub-section to separate process from posture. v2 keeps it in §2. Mild structural fix; non-blocking.

### Lens 2 — Medical-technical — APPROVE

- v2 is engineering-internal; medical-technical readability concerns from Gate A v1 Lens 7 (customer-facing posture summary at `/legal/posture`) are correctly out-of-scope for the overlay itself. The overlay's job is to be the engineering-internal authority record; the customer-facing companion is correctly named as a near-term task, not a blocker. No new findings.

### Lens 3 — HIPAA auditor surrogate (OCR) — APPROVE-WITH-FIXES

- **(P1) §5 row count completeness gap — §164.530(b) Workforce Training is mapped to §164.308(a)(5) row, but §164.530(b) is a Privacy Rule provision distinct from the Security Rule §164.308(a)(5) Security Awareness and Training.** OCR auditors probe both. v2 collapses to one row. Either split into two rows OR rename §5 to "§164.308 + §164.530 OCR audit checklist" and add the §164.530(b) row. The §164.530(b) gap was Gate A v1 §3 completeness check item #3 — not fully closed.

- **(P2) §5 missing §164.312(a)(1) Access Control + §164.312(b) Audit Controls + §164.312(d) Person/Entity Authentication.** These are Security Rule technical-safeguard rows that OCR auditors probe as systematically as §164.308. The overlay correctly scopes §5 to administrative safeguards (§164.308) at this stage — acceptable to defer technical safeguards to a follow-up §5.1 expansion, but should call this scoping out explicitly so the gap is self-disclosed-and-remediated rather than silently-missing.

- **(P3) §5 row for §164.308(a)(1)(ii)(D) Information System Activity Review** points at `admin_audit_log` + `client_audit_log` + Substrate Engine. Good. But misses the **cadence** dimension that OCR probes ("how often is the activity review actually performed by a human?"). Add a "review cadence" sub-pointer (quarterly? monthly?) or accept as a foundational gap.

### Lens 4 — Attorney surrogate — APPROVE-WITH-FIXES

- **(P1) §4 "v2 published" claim at line 111 is asserted to make this Gate B's APPROVE the trigger.** Cross-fork consistency check: SUBPROCESSORS.md line 4 already says "Effective Date: 2026-05-13" — so SUBPROCESSORS v2 self-claims effective today. That makes v2 effectively published unilaterally regardless of this Gate B verdict. Either (a) make SUBPROCESSORS.md's effective-date contingent on Gate B APPROVE (small edit to its line 4) OR (b) reframe the overlay §4 row as "Supersession effective 2026-05-13 per SUBPROCESSORS.md v2 self-publication; overlay records this fact as Gate B fork validated cross-consistency on 2026-05-13." Defensibility outside-counsel-reading is unchanged; the framing matters for "who is the authority of record."

- **(P2) §4 row for click-through acknowledgment v1.0-2026-04-15 → MASTER_BAA_v1.0_INTERIM** "Pending v2.0 outside-counsel hardening + customer re-sign" date column is intentionally non-committal. This is the right legal framing (no premature commit). Master BAA line 249 commits "Target effective date for v2.0: 2026-06-03 (21 days)" — the overlay could mirror that target as "Pending v2.0 (target 2026-06-03)" for traceability. Cross-fork-consistency-favorable but optional.

### Lens 5 — Product manager — APPROVE-WITH-FIXES

- **(P2) §11 "Audience and how to read this document" recommended in Gate A v1 Lens 4 was NOT added.** v2 has §9 (own frontmatter) + §10 (Gate B reviewer guidance) but no audience-keyed read-guide. The doc still assumes engineering-internal vocabulary throughout. For an artifact named "POSTURE_OVERLAY" landing at `docs/POSTURE_OVERLAY.md`, this is a UX gap for outside counsel (who will be re-engaged) and OCR auditors. Add §11 (3–5 line per-audience read-guide) — recommended but non-blocking.

- **(P3) v2 still does NOT address Gate A v1 Lens 4 concern about CLAUDE.md ↔ POSTURE_OVERLAY relationship.** Are they siblings? Parent/child? Sentence at §1 line 17 says overlay is "FIRST document any contributor or reviewer reads" — competes with CLAUDE.md's de facto first-read status. One-line clarification recommended.

### Lens 6 — Engineering (Steve) — APPROVE-WITH-FIXES

- **CI gate extension feasibility against current `context-manager.py` — VERIFIED FEASIBLE.** Current `validate()` (lines 339–429) walks memory topic files for YAML frontmatter (lines 415–420). Extending to `docs/**/*.md` is additive (no schema migration needed for memory files). The `--posture-overlay` flag (parses §4 supersession registry markdown + greps codebase) adds two new methods. Both fit cleanly in the existing single-file script. Estimated effort: 1–2 hours, matches Gate A v1 Coach concern 2 estimate.

- **(P1) §7 line 192 — producer/consumer rule cites "≥2 owning docs adopt the §8 frontmatter, ≥1 consumer commit body" as ship preconditions for the citation-form gate.** Good. But v2 does NOT specify WHICH 2 owning docs are the canonical first adopters. Without naming them, the ship-condition is unbounded. Specify: master BAA v1.0-INTERIM already has frontmatter-adjacent metadata at line 1 + Article 9; SUBPROCESSORS.md v2 has effective-date metadata; both should be the named first adopters. Add this to §7 as a target adoption-pair so the gate ship can be scheduled.

- **(P2) Frontmatter schema misalignment with memory schema** — memory schema (CLAUDE.md "Memory Hygiene" section) has `name` + `description` + `type` + `decay_after_days` + `last_verified`. v2 §8 maps `title=name`, `topic_area=type`, drops `description`. Dropping `description` removes a useful field that memory infrastructure already supports. Recommend keeping `description` as an optional field in the docs schema for parity — zero cost, helps `--posture-overlay` mode produce useful diff output.

- **(P3) §7 line 187 — banned citation form "❌ Bare references to docs in the supersession-registry without `SUPERSEDED` marker"** is good but ambiguous on grep granularity. Is a reference to `docs/HIPAA_FRAMEWORK.md` in a code comment a violation? In a commit body? In another `audit/*.md` file? The gate scope needs explicit file-glob in §7 (e.g., "scope: `backend/**/*.py`, `appliance/**/*.go`, `docs/**/*.md` excluding `audit/**`; commit bodies via separate Husky-style hook"). Otherwise the gate either over-blocks (every historical `audit/` file becomes a violation) or under-blocks (depending on implementation choice).

### Lens 7 — Coach (consistency + no over-engineering + no double-build) — APPROVE-WITH-FIXES

- **§3 multi-device-enterprise-scale category coverage check:**
  - ✅ Multi-tenant load harness, substrate-MTTR soak, DR drill, cross-org RLS, per-appliance signing keys, daemon-side heartbeat signing, fleet-wide enforcement — all 7 in v2 §3 lines 95–103.
  - **(P1) Missing: cross-org governance** (RT21 cross-org site relocate IS in §3 line 63 under Legal/compliance, but cross-org governance as a pattern — dual-admin approval, two-actor state machines — is NOT separately enumerated; this is a substrate pattern, not a single-doc topic).
  - **(P1) Missing: multi-tenant PgBouncer pooling** (`admin_transaction` + `tenant_connection` are pinned in CLAUDE.md as a load-bearing invariant for the multi-tenant scale story; not in §3).
  - **(P2) Missing: multi-org evidence chain anchoring** (Session 216 "anchor-namespace convention" pinned in CLAUDE.md as `client_org:<id>` / `partner_org:<id>` synthetic anchors; this IS a multi-device-enterprise-scale architectural pattern absent from §3).
  - Add these three rows under §3 "Multi-device-enterprise scale" as TBD pointers (matches the rest of the category's "Stable" / "TBD" / "NOT INDEXED" treatment).

- **No over-engineering risk in v2.** §6 tiered governance + §7 unified-with-memory-hygiene CI gate are appropriately scoped. No double-build.

- **No over-claim of completeness.** §3 + §5 both explicitly mark TBDs rather than claiming closure. Foundational-gap admissions at §5 line 134 ("Whole-legal-document-inventory follow-up") are correctly preserved from Gate A v1 Coach lens.

- **Cross-fork consistency PASS:**
  - vs. master BAA v1.0-INTERIM: effective dates align (both 2026-05-13). Master BAA line 259 cites SUBPROCESSORS.md as the registry of record; overlay §3 line 55 cites the same. No conflict.
  - vs. SUBPROCESSORS.md v2: effective date 2026-05-13 matches overlay §4 row. 19 entries enumerated. No conflict.
  - vs. counsel-edited Gate A doc: all 5 Gate A v1 P0s are explicitly closed in v2 with line-number traceability. v2 §10 self-documents the closure (lines 244–249).

---

## Banned-word + urgency-overshoot scan

**Banned-language scan (CLAUDE.md "Legal language" rule):**
- Grep for `ensure|prevent|protect|guarantee|audit-ready|100%|PHI never|never leaves` on v2 draft → **0 hits**. Clean.

**Urgency-overshoot scan:**
- Grep for `urgent|immediately|critical|emergency|asap|must ship|must land|today only` on v2 draft → **0 hits**. Clean.

**Other quality checks:**
- v2 §1 line 12 still uses "load-bearing" framing for legal posture — defensibility concern raised in Lens 1 (P2) but not a banned-language hit.
- v2 §6 line 144 "Just commit" — informal but acceptable in engineering-internal doc.
- No emoji usage. No clinical-authority drift (Rule 10 clean).

---

## Cross-fork consistency check

| Source | v2 overlay claim | Source claim | Consistent? |
|---|---|---|---|
| `MASTER_BAA_v1.0_INTERIM.md` line 1 | v1.0-INTERIM effective 2026-05-13 (§4 row 2) | "v1.0-INTERIM-2026-05-13" | ✓ |
| `MASTER_BAA_v1.0_INTERIM.md` line 259 | SUBPROCESSORS.md is Exhibit A registry | "current Subprocessor Registry … at `docs/SUBPROCESSORS.md`" | ✓ |
| `SUBPROCESSORS.md` line 4 | Effective 2026-05-13 (§4 row 1) | "Effective Date: 2026-05-13" | ✓ |
| `SUBPROCESSORS.md` line 4 | Reframed as Exhibit A to master BAA | "Classification: Exhibit A to … Master Business Associate Agreement" | ✓ |
| Gate A v1 verdict | All 5 P0s addressed in v2 | Author-claimed closure verified line-by-line above | ✓ |
| `feedback_round_table_at_gates_enterprise.md` two-gate lock-in | Gate B fork running on v2 | This document IS the Gate B fork | ✓ |
| `feedback_consistency_coach_pre_completion_gate.md` | Mandatory full-sweep + 4-lens-fork | This doc applies 7 lenses (full Class-B); ran pre-completion against §3 completeness | ✓ |

---

## Publish-to-docs sign-off

**YES — APPROVE-WITH-FIXES with mandatory-implementation discipline.**

v2 can move from `audit/posture-overlay-draft-2026-05-13.md` → `docs/POSTURE_OVERLAY.md` PROVIDED the following are committed in lockstep per `feedback_consistency_coach_pre_completion_gate.md` two-gate lock-in:

1. **P0-closure-attestation in the publish commit body** citing BOTH Gate A v1 verdict (`audit/coach-posture-overlay-gate-a-2026-05-13.md`) AND this Gate B verdict (`audit/coach-posture-overlay-gate-b-2026-05-13.md`) — mandatory per round-table lock-in.

2. **P1 findings from this Gate B carried as named TaskCreate followups in the same commit:**
   - **TaskCreate (P1):** §5 add §164.530(b) Workforce Training row distinct from §164.308(a)(5); add §164.312 technical-safeguards rows OR add explicit "§5 scope: administrative safeguards only; technical safeguards in §5.1 follow-up" scope note.
   - **TaskCreate (P1):** §7 specify ≥2 named first-adopter owning docs for the citation-form gate ship precondition (master BAA + SUBPROCESSORS.md).
   - **TaskCreate (P1):** §3 multi-device-enterprise add three missing rows — cross-org governance, multi-tenant PgBouncer pooling (`admin_transaction`/`tenant_connection`), multi-org evidence chain anchoring (`client_org:<id>` / `partner_org:<id>`).
   - **TaskCreate (P1):** §4 row 1 either (a) edit SUBPROCESSORS.md line 4 to make effective date contingent on Gate B APPROVE, OR (b) reframe overlay §4 row to record Gate B as cross-consistency-validation rather than supersession-trigger.
   - **TaskCreate (P1):** §2 Rule 4 remove or annotate the "across the fleet, not per-tenant" gloss to match `feedback_enterprise_counsel_seven_rules.md` verbatim.

3. **P2/P3 findings carried as backlog (no commit-time TaskCreate required):**
   - §1 line 12 legal-posture framing softening
   - §2.5 process-rules sub-section split
   - §4 row 2 mirror master BAA's 2026-06-03 v2.0 target date
   - §7 explicit file-glob for citation-form gate scope
   - §8 retain optional `description` field for memory-schema parity
   - §11 audience-keyed read-guide
   - CLAUDE.md ↔ POSTURE_OVERLAY relationship one-liner

4. **No P0s.** All Gate B findings are P1 or below. Gate B does not BLOCK publication.

5. **Round-table lock-in:** mandatory-implementation per `feedback_consistency_coach_pre_completion_gate.md` two-gate rule means each P1 above MUST be either CLOSED before publication OR carried as a named TaskCreate followup in the publish commit. "Acknowledged / noted / deferred" does NOT satisfy a P1 from a Gate B verdict.

---

## Final recommendation

**APPROVE-WITH-FIXES.** v2 closes all 5 Gate A v1 P0s with line-level evidence. No banned language. No urgency overshoot. Cross-fork consistent with master BAA v1.0-INTERIM + SUBPROCESSORS.md v2. CI gate extension against `context-manager.py` is verified feasible. No P0 regressions detected.

**Top 5 P1 findings ranked (mandatory-implementation in publish commit per round-table lock-in):**

1. **§3 add 3 missing multi-device-enterprise-scale rows** (cross-org governance, multi-tenant PgBouncer pooling, multi-org evidence chain anchoring). Closes Coach §3-completeness gap; same shape as existing rows; ~5 minutes of work.

2. **§5 add §164.530(b) Workforce Training row distinct from §164.308(a)(5) Security Awareness Training + scope §5 explicitly as administrative-safeguards-only with §164.312 follow-up pointer.** OCR auditor distinguishes these; collapsing them is an auditor-credibility gap.

3. **§4 row 1 + SUBPROCESSORS.md line 4 ratchet:** either make SUBPROCESSORS.md effective-date Gate-B-contingent, or reframe overlay §4 row as cross-validation. Avoids "who is the authority of record" ambiguity for outside-counsel re-engagement.

4. **§7 name the ≥2 first-adopter owning docs** for the citation-form gate ship precondition. Otherwise the producer-consumer rule is unbounded.

5. **§2 Rule 4 verbatim-with-counsel-source.** Remove or annotate the "across the fleet, not per-tenant" engineering gloss that isn't in `feedback_enterprise_counsel_seven_rules.md`. Maya-lens defensibility.

**Path forward:** Author addresses the 5 P1s above (either close in v2.1 OR carry as named TaskCreate followups in the publish commit body) → publish v2.1 to `docs/POSTURE_OVERLAY.md` → commit body cites BOTH gate verdicts + TaskCreate IDs for any P1s carried-forward → CI/CD deploy → runtime verification (`curl /api/version` for SHA parity, file presence at `docs/POSTURE_OVERLAY.md`).

— Fresh-context Gate B fork
   2026-05-13
