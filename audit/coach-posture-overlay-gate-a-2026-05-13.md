# Class-B 7-lens Gate A — POSTURE_OVERLAY.md draft v1

**Reviewer:** Fresh-context Gate A fork (no prior session state, no in-doc author counter-arguments)
**Date:** 2026-05-13
**Subject:** Draft at `audit/posture-overlay-draft-2026-05-13.md` proposing creation of `docs/POSTURE_OVERLAY.md` as the canonical current-truth declaration. Counsel Rule 5 structural fix. Task #51.

---

## Per-lens verdict

| Lens | Verdict |
|---|---|
| 1. Inside-counsel surrogate | APPROVE-WITH-FIXES |
| 2. Attorney (outside-counsel mindset) | APPROVE-WITH-FIXES |
| 3. HIPAA auditor (OCR) | **BLOCK** until §3 admission-vs-exposure is reworked |
| 4. Product manager | APPROVE-WITH-FIXES |
| 5. Engineering (Steve) | APPROVE-WITH-FIXES |
| 6. Coach (consistency + no double-build) | **BLOCK** on §3 single-doc registry + §7/§8 double-build |
| 7. Medical-technical | APPROVE-WITH-FIXES |

**Overall verdict:** **APPROVE-WITH-FIXES** — the overlay concept and §1/§2/§5/§6 are sound and load-bearing. The §3 registry, §4 supersession claims, and the §7/§8 implementation plan require restructuring before this lands at `docs/POSTURE_OVERLAY.md`. Two lenses BLOCK; both have concrete fixes that do not require redesign. Recommended structure shift: §3 becomes a *registry-of-pointers* with per-area sub-docs (or pinned headers in existing docs), not an enumerated table that goes stale by the day it ships.

---

## Lens 1-7 findings on the 10 sections of the draft

### Lens 1 — Inside-counsel surrogate (§2 cite-chain completeness)

**Verdict: APPROVE-WITH-FIXES.**

§2 names three load-bearing rule-sets: counsel's 7 rules, privileged-access chain of custody, Class-A-vs-B routing. The first two are correctly load-bearing. The third (Class-A-vs-B routing) is a **process** rule, not a **governing** rule — it tells you how to ROUTE legal questions, but it does not govern the platform's posture itself. Mixing process and posture rules in §2 is structurally weak.

**Missing from §2** (rule-sets that DO govern platform posture and are equally load-bearing):

1. **PHI scrubbing boundary (Rule 2, machine-enforced):** counsel's compiler-rule framing — "PHI-free Central Command is a compiler rule, not a policy preference." This is enforced at appliance egress via `phiscrub` (14 patterns). It governs every new data-emitting feature pre-merge. Strength-equal to Rule 5; should be cited in §2.

2. **Evidence chain immutability (auditor-kit determinism contract):** Session 218 round-table established that `compliance_bundles` are Ed25519-signed + hash-chained + OTS-anchored, that `canonical_site_id()` must NEVER be used against them, and that the auditor kit is byte-identical across downloads. This is the substrate's tamper-evidence load-bearing promise — and it is in `CLAUDE.md` under "Auditor-kit determinism contract." It governs how *every* evidence path operates.

3. **Banned-language rule (legal copy):** `CLAUDE.md` "Legal language" rule (no ensures/prevents/protects/guarantees/100%/audit-ready/PHI-never-leaves). This is already a CI-enforceable posture and governs every customer-facing artifact. Citing it explicitly in §2 makes it overlay-discoverable.

**Recommendation:** Replace the Class-A-vs-B routing entry with **PHI scrubbing boundary + evidence chain immutability + banned-language rule**. Move the routing rule to a new §2.5 "Process rules" sub-section. Process rules govern WORK; posture rules govern the PLATFORM — distinguish.

---

### Lens 2 — Attorney surrogate (§4 supersession defensibility)

**Verdict: APPROVE-WITH-FIXES.**

§4 row-by-row defensibility check:

| §4 claim | Defensibility |
|---|---|
| `HIPAA_FRAMEWORK.md` superseded "as of 2026-05-06" | **Date is wrong.** The doc's frontmatter says "Last verified 2026-01-14." Counsel's §164.504(e) framing change is from RT21 v2 packet 2026-05-06. But HIPAA_FRAMEWORK has been **stale since 2026-01-14** — RT21 didn't supersede it, RT21 RE-affirmed an existing staleness. Either claim "stale since 2026-01-14 (self-declared); counsel framing further obsoleted 2026-05-06" OR omit the date and say "PENDING REFRESH." Today's framing implies counsel triggered the staleness; the actual story is that the doc admitted its own staleness 4 months ago and nothing happened. |
| `BAA_SUBPROCESSORS.md` superseded by draft at `audit/baa-subprocessors-reaudit-draft-2026-05-13.md` | **Premature.** That draft has NOT YET passed its Class-B Gate A (per its §7 last line: "Pending dispatch"). Marking it as the supersessor before Gate A APPROVE is a chain violation against the draft's own governance. Either gate the supersession on Gate A APPROVE OR state "current doc is stale; replacement under Gate A review at <path>." |
| Banned-language docs superseded "ongoing" | **Soft phrasing — defensible but vague.** "Ongoing" is not a date. Replace with "any doc citing these phrases MUST be revised at next touch; the language itself is superseded by `CLAUDE.md` "Legal language" rule (gold authority)." |
| Master BAA framed as "Missing document" not "superseded" | **Correct legal framing.** A document that never existed cannot be superseded — there is no prior version. "Missing" is the right word. Outside counsel reading this would not flag it. However: the entry says "Customer signatures are SHA256 hashes of a 5-bullet click-through acknowledgment, not of a real BAA." This is a frank admission of a substantive Rule 6 gap and is exactly what BAA-drafting Gate A is solving (cross-consistent — see cross-fork check below). Keep this admission; it is the right posture. |

**Could outside counsel find overcommitting language?** Two spots to soften:

- §1 line 11: "operational, legal, security, and architectural posture" — counsel would prefer "platform's operational, security, and architectural posture; legal posture is governed by counsel directly (this document records counsel's directives in machine-readable form)." Avoid implying that this doc IS the legal posture; it is the engineering-side **record** of counsel's posture.

- §4 "BAA-on-file claims sit on a non-existent contract" — accurate but unnecessarily blunt for a doc that lands in `docs/` (potentially auditor-readable). Recommend softer phrasing: "Customer-facing 'BAA on file' assertions currently reference acknowledgments-of-intent that are pending remediation per Task #56." Still truthful, less self-incriminating.

---

### Lens 3 — HIPAA auditor (OCR) — BLOCK

**Verdict: BLOCK (with concrete fix that doesn't require redesign).**

The §3 registry contains four "OVERDUE" entries (HIPAA framework, Architecture, Risk Analysis, Onboarding SOP) plus one "BLOCKED" entry (BAA). An OCR auditor reading this document — and they WILL read it, because the overlay structurally invites itself to be read as governance evidence — will treat it as a **self-disclosure of compliance gaps**.

This is not necessarily bad. **HIPAA's enforcement-discretion doctrine treats self-identified-and-remediated gaps favorably.** The BAA-drafting Gate A leaned on this exact doctrine. BUT — and this is the load-bearing finding — the overlay does not name remediation tasks or commit dates for the OVERDUE entries. It just says "OVERDUE." An auditor reading "HIPAA Framework — OVERDUE — needs verification" with no commit date or owner is reading evidence of a governance gap WITHOUT evidence of a governance response. Self-identified-WITHOUT-remediated is materially worse posture than not identifying at all.

**Specific exposure points:**

- **§164.308(a)(1)(ii)(A) Risk Analysis** listed as OVERDUE with "needs verification." An auditor probes: "When was your most recent risk analysis?" Today, the overlay's published answer is "OVERDUE / needs verification." That answer is auditor-bait.
- **HIPAA Risk Analysis cadence** — §164.308(a)(1)(ii)(A) is a *required* implementation specification. The doc EXISTS (`docs/RISK_ANALYSIS.md`, 32791 bytes, 2026-03-11). The overlay says "needs verification" without saying the file is there. Confusing.
- **Workforce Training** (§164.530(b)) — not in §3 at all. Auditor will probe; overlay is silent.
- **DR drill** (§164.308(a)(7)) — not in §3 at all.

**Auditor-credibility of §6 governance workflow** — Class-B Gate A + Gate B for overlay updates IS auditor-credible. Two-eye review with named lens-personas, written verdict, P0 closure gate is exactly the kind of governance process OCR likes to see. Keep §6.

**Fix to clear BLOCK:** Every "OVERDUE" entry in §3 MUST have either (a) a remediation Task # + target date, or (b) reclassification as "PENDING REFRESH (Task #N target YYYY-MM-DD)". The mere word "OVERDUE" with no remediation pointer is the exposure. Also: ADD Risk Analysis with its current state ("docs/RISK_ANALYSIS.md last 2026-03-11; per-§164.308(a)(8) annual review pending"), Workforce Training, DR drill, Sanction Policy (§164.308(a)(1)(ii)(C)), Information System Activity Review (§164.308(a)(1)(ii)(D)).

---

### Lens 4 — Product manager (audience + tone)

**Verdict: APPROVE-WITH-FIXES.**

The overlay is engineering-internal in tone. Phrases like "Class-B Gate A," "TaskCreate followup items," "lockstep tests," "task #51" assume a reader who knows the project's process vocabulary. An auditor reading this cold will need a glossary. A customer compliance officer will not understand half the content.

**Audience question** — who actually reads `docs/POSTURE_OVERLAY.md`?

1. **Engineering contributors** (primary): they need the §3 registry to know which doc to cite. ✓ Served.
2. **Outside counsel** (when re-engaged): they need §2 governing rules and §4 supersession. ✓ Served.
3. **HIPAA auditor (OCR)** (eventually): they need §3, §5 decay cadence, §6 governance, evidence of self-remediation. ⚠ Partly served — see lens 3.
4. **Customer compliance officer**: they need a customer-readable summary of what governs the platform. ✗ Not served.
5. **Practice owner**: probably never reads this. ✗ Not served, and that's fine.

**Recommendation:** Add §11 "Audience and how to read this document" with a 5-line summary keyed to each audience. Each audience reads a different sub-set of sections. Or: ship a customer-facing /legal/posture summary derived from this doc (decoupled — this overlay stays engineering-internal, summary doc handles the customer-facing posture story).

**Other PM concerns:**

- §1 "FIRST document any contributor or reviewer reads" — this competes with `CLAUDE.md` for first-read primacy. Resolve: CLAUDE.md is the engineering-conventions index; POSTURE_OVERLAY is the doc-authority index. They are siblings, not parent/child. Say so explicitly.

---

### Lens 5 — Engineering (Steve) — §3 current-doc claims + §7 CI gate feasibility

**Verdict: APPROVE-WITH-FIXES.**

**§3 claim-by-claim grep verification:**

| Claim | Reality |
|---|---|
| HIPAA_FRAMEWORK.md last verified 2026-01-14 | ✓ Doc header confirms |
| ARCHITECTURE.md last 2026-03-22 | ✓ Doc header confirms |
| RUNBOOKS.md "varies" | ⚠ Doc header is 2026-04-13 — registry could be more specific |
| PROVENANCE.md "recent" | ⚠ Doc header is 2025-12-03 with self-declared "stale on Vault posture" — registry's "recent" is wrong |
| DATA_MODEL.md "recent" | ✓ Doc header 2026-05-06 (RT-DM cycle) confirms |
| ROADMAP.md "recent" | ⚠ Doc mtime is 2026-03-22 — over 50 days, not "recent" |
| RISK_ANALYSIS.md "needs verification" | ⚠ Doc EXISTS at 32791 bytes 2026-03-11 — "needs verification" is misleading |
| CLIENT_ONBOARDING_SOP.md "needs verification" | ⚠ Doc header says v2.0 2025-12-31 — registry could say "stale" |
| PHI_DATA_FLOW_ATTESTATION.md "needs verification" | ⚠ Doc header v1.1 2026-05-06 — registry could say "recent" |

So the §3 registry has its OWN staleness/accuracy problem: vague descriptors ("recent," "needs verification," "varies") that don't match what the doc headers actually say. **This is the chicken-and-egg problem the §10 reviewer guidance asked about — the overlay's own §3 is stale on the day it ships.** Recommend: Last verified column be the verbatim date from each doc's frontmatter/header, not a hand-typed descriptor.

**§7 CI gate `test_no_stale_doc_citation.py` feasibility:**

- **Conceptually feasible.** Pattern matches existing `test_no_direct_site_id_update.py` (ratchet 6) + `test_no_anonymous_privileged_endpoints.py` + 8 other `test_no_*.py` gates in the backend test suite. The codebase has a strong "compiler-rule" precedent.
- **Implementation concerns:**
  - The supersession registry is in a markdown file, not a Python constants module. The CI gate must parse markdown — fragile. Recommend: machine-readable companion file at `docs/posture_overlay.json` that the gate parses, with the markdown rendering generated FROM the JSON (single source of truth). Same pattern as `check_type_registry` (Session 205, Mig 157).
  - "Greps the codebase for citations of superseded docs" — greppable forms ("HIPAA_FRAMEWORK.md", "BAA_SUBPROCESSORS.md") are easy. Allowed citation forms (`posture-overlay:CURRENT(<topic>)`, `posture-overlay:SUPERSEDED(<doc>, reason)`) are a NEW convention — no producers exist today. Adoption cost is non-trivial and per `feedback_directive_must_cite_producers_and_consumers.md` requires ≥2 producers + ≥1 consumer before being treated as project convention. **Today this is a 0-producer convention.**
  - Allowlist mechanism (for legitimate historical reference) must be in place from day 1 — otherwise the gate blocks every `audit/` file that cites the BAA-Subprocessors stale draft (which IS a SUPERSEDED reference) without the marker.

**Recommendation on §7:** ship the gate, but ratchet-style (start at "count current violations as baseline, drive down"), not strict-mode. Same pattern as `test_no_direct_site_id_update.py` ratchet 6, `test_frontend_mutation_csrf.py` ratchet 58→0.

**§8 frontmatter double-build with memory hygiene:**

The memory-hygiene infrastructure at `.agent/scripts/context-manager.py validate` enforces YAML frontmatter on every `~/.claude/projects/.../memory/*.md` file (validated check above — the validate function does check the `---` frontmatter prefix). Schema is `name / description / type / decay_after_days / last_verified` (per CLAUDE.md "Memory Hygiene" section).

The §8 proposed frontmatter for `docs/` files: `title / topic_area / last_verified / decay_after_days / supersedes / superseded_by / posture_overlay_authoritative`.

**These are 80% overlapping but not aligned.** This IS a double-build risk — see Coach lens below for the structural recommendation.

---

### Lens 6 — Coach (consistency + no over-engineering + no double-build) — BLOCK

**Verdict: BLOCK.** Three structural concerns make me block:

#### Coach concern 1: §3 single-doc registry is the WRONG structure

Enumerating every doc area in one §3 table will go stale within days. Today's draft is already wrong on 5 of 14 rows (see lens 5 grep). The fundamental problem: a single-doc registry **centralizes update friction** — every time any doc in the repo gains a new revision, this registry must be touched, and each touch requires Class-B Gate A + Gate B per §6 governance.

That update cost makes the overlay perpetually-stale-by-default. Worse than the problem it solves.

**Recommended restructure:** §3 becomes a **registry-of-pointers**, not a registry-of-current-state:

- The OVERLAY enumerates **topic areas** + which doc OWNS the topic. Just one column: "for topic X, see doc Y."
- Each owning doc carries the **last_verified / decay_after_days** in its OWN frontmatter (per the §8 standard).
- The CI gate (§7 evolved) walks the doc's frontmatter and asserts freshness; the overlay does not pin a date.

**Net effect:** the overlay only changes when a topic-area's OWNER changes (rare — sub-day cadence) or a new topic area is added (also rare). Date-staleness lives in each doc's frontmatter (where it's local to the touch). This is the **same pattern memory/MEMORY.md uses**: MEMORY.md is a pointer-index, topic files carry their own decay_after_days. The overlay should mirror this.

#### Coach concern 2: §7 CI gate + §8 frontmatter double-build memory-hygiene infrastructure

`.agent/scripts/context-manager.py validate` already:
- enforces YAML frontmatter on memory topic files
- checks `last_verified` + `decay_after_days`
- is wired to `.github/workflows/memory-hygiene.yml` on every push touching `.agent/`

The §8 proposed schema for `docs/*.md` files is 80% the same schema with renamed fields:
- `name` → `title`
- `description` → (dropped)
- `type` → `topic_area`
- `decay_after_days` → `decay_after_days` (same)
- `last_verified` → `last_verified` (same)
- (new) `supersedes`, `superseded_by`, `posture_overlay_authoritative`

**Recommendation: UNIFY.** Extend `context-manager.py validate` to also walk `docs/**/*.md` files, with topic_area + supersedes/superseded_by as docs-specific optional fields. One validator, two scopes. Avoid building `test_no_stale_doc_citation.py` as a new test — instead extend the existing memory-hygiene workflow.

This unification has a second benefit: memory files and docs files share the same staleness semantics (decay_after_days), so the operator only learns ONE pattern.

#### Coach concern 3: §6 governance over-engineering risk

Requiring full Class-B 7-lens Gate A + Gate B for **every** overlay update is overkill for routine moves (e.g., adding a new topic area pointer; bumping decay_after_days from 30 to 60). The cycle time on a Class-B 7-lens fork is hours; if the overlay needs that for trivial moves, it will go stale because nobody will pay the cost.

**Recommendation: tier the governance.**

- **Major changes** (adding/removing a rule-set in §2; changing supersession in §4 such that prior docs become invalid): Class-B Gate A + Gate B as drafted.
- **Minor changes** (adding a new topic area; bumping a decay value; updating a pointer when a doc moves): single-lens Coach pre-completion review + commit-body justification. No fork.

This matches the project's existing pattern (`feedback_consistency_coach_pre_completion_gate.md`) — full forks for material changes, single-lens for routine maintenance.

#### Coach concern 4: cross-fork consistency

The BAA-drafting Gate A's recommendation #4 ("whole-legal-document-inventory audit not commissioned") is partially subsumed by §4's "Whole-legal-document-inventory audit" foundational gap entry. **But the overlay only SURFACES the gap; it does not COMMIT engineering to closing it.** This is the right posture for an overlay (overlays catalog, they don't task-track), BUT the BAA Gate A's commitment is binding — the overlay must reference the Task # so the closure path is auditable. Currently §4 says "Sub-task of Task #56 per Class-B Gate A finding (Coach lens)." That's an acceptable pointer. ✓ Consistent with BAA Gate A.

#### Coach concern 5: the overlay is in violation of its own rules

§4 supersession registry lists `BAA_SUBPROCESSORS.md` as superseded BUT — per §7's own CI gate — the overlay itself cites this doc without the `posture-overlay:SUPERSEDED(BAA_SUBPROCESSORS.md, reason)` marker. The §7 CI gate, if applied to the overlay itself, would FAIL the overlay.

Is the overlay exempt from its own gate? **Coach says: no.** Otherwise the gate is permissive-by-convention. Fix: the §7 gate has an allowlist `OVERLAY_REGISTRY_FILES = [docs/POSTURE_OVERLAY.md]` — the overlay is the source-of-truth for the registry and may cite without the marker. Document this exemption explicitly in §7.

---

### Lens 7 — Medical-technical (clinic-context readability)

**Verdict: APPROVE-WITH-FIXES.**

A practice owner / clinic compliance officer reading this overlay cold will NOT understand what governs OsirisCare. The language is engineering-internal ("ratchet test," "lockstep," "compiler rule," "Class-B 7-lens fork"). Phrases like "the rule-sets win" make sense in engineering process language but read as legalese-with-no-citation to a clinic compliance officer.

**Recommendation:** the overlay itself does not need to be customer-readable — it is the engineering-internal authority record. BUT there should be a **customer-facing companion** at `docs/customer-posture-summary.md` (or `/legal/posture` route) that translates §2 governing rules into clinic-friendly language:

- "OsirisCare operates under 7 hard rules established by outside HIPAA counsel on 2026-05-13. Plain-language summary: (1) we only publish metrics from canonical sources; (2) PHI never leaves the on-prem appliance through any new feature — verified by a pre-merge gate; (3) privileged actions on your appliance require a cryptographic chain from your approval through the action; ..."

Without this customer-facing companion, customers will not know what governs the platform — which itself is a Rule 5 violation in the customer-facing direction (no document tells the customer "what governs OsirisCare").

This is a separate sprint, not a blocker for the overlay landing. But mark it as a near-term Task.

---

## §3 registry completeness check

**Topic areas missing from §3 that SHOULD be there:**

| Missing topic | Why it matters | Doc that probably owns it |
|---|---|---|
| **PHI scrubbing posture** | Rule 2 compiler-rule. Customer-facing claim. | `docs/PHI_DATA_FLOW_ATTESTATION.md` (exists, 2026-05-06) |
| **Workforce Training records** | §164.530(b) required for BAs | DOES NOT EXIST — gap |
| **HIPAA Risk Analysis cadence** | §164.308(a)(1)(ii)(A) annual review | `docs/RISK_ANALYSIS.md` exists 2026-03-11; cadence undocumented |
| **Sanction Policy** | §164.308(a)(1)(ii)(C) | DOES NOT EXIST — gap |
| **Information System Activity Review** | §164.308(a)(1)(ii)(D) | DOES NOT EXIST — gap |
| **DR / Contingency Plan** | §164.308(a)(7) | DOES NOT EXIST — gap |
| **Operator alert / notification channels** | Active substrate component | `docs/security/alert-runbooks.md` exists |
| **Stripe billing posture** | Active customer-facing feature | `docs/legal/billing-phi-boundary.md` exists |
| **Sub-processor list (current state)** | Rule 8 — SHOULD be in §3 with "stale" marker, not only §4 | `docs/BAA_SUBPROCESSORS.md` (stale 2026-03-11) — see also re-audit draft |
| **Privacy Policy** | Standard customer/auditor probe | UNKNOWN — verify `Legal.tsx` content |
| **Terms of Service** | Standard customer/auditor probe | UNKNOWN — verify `Legal.tsx` content |
| **Acceptable Use Policy** | Lower priority but standard | DOES NOT EXIST per BAA Gate A |
| **Framework mapping (SOC2 / GLBA / multi-framework)** | Project memory references `multi-framework compliance` | Unknown |
| **Memory-hygiene infrastructure** | Project's own self-governance | `.agent/scripts/context-manager.py validate` (script-as-doc) |
| **Vault Transit posture** | Active subsystem with separate VPS | `docs/security/vault-transit-migration.md` exists |
| **Legal-banned-language rule** | Customer-facing copy gate | `CLAUDE.md` "Legal language" section |
| **Substrate Integrity Engine** | Active runtime invariant system | Project memory `project_substrate_integrity_engine.md` |

The current §3 has 14 rows; a comprehensive registry would be 25-30 rows. Missing 11+ is substantial. Note: most of the missing rows would be marked "MISSING DOC" or "PENDING" — that's fine; the overlay's job is to ENUMERATE the universe, including gaps.

---

## §4 supersession registry accuracy check

| Claim | Defensible? | Fix |
|---|---|---|
| HIPAA_FRAMEWORK.md superseded 2026-05-06 | **No** — doc was already self-declared stale 2026-01-14; counsel framing change is a separate event 2026-05-06. | Two-line entry: "Self-declared stale 2026-01-14; counsel §164.504(e) framing further obsoleted 2026-05-06; refresh pending." |
| BAA_SUBPROCESSORS.md superseded by Class-B-Gate-A-pending draft | **Premature** — the supersessor has not passed Gate A. | "Current doc stale since 2026-03-11; replacement under Gate A review at `audit/baa-subprocessors-reaudit-draft-2026-05-13.md`. Supersession effective on Gate A APPROVE." |
| Banned-language docs superseded "ongoing" | **Vague.** | "Any doc citing banned phrases is superseded for that point by `CLAUDE.md` Legal language rule (gold authority). Per-doc revision happens at next touch." |
| Master BAA framed as Missing not Superseded | **Correct.** | Keep. |

---

## §6 governance feasibility check

The §6 workflow (Class-B Gate A + Gate B for every overlay update) produces **stale-overlay risk** when applied uniformly. See Coach concern 3 above.

**Recommended tiered governance:**

| Change type | Process |
|---|---|
| Major (rule-set add/remove in §2; structural §4 supersession that invalidates prior workflows) | Full Class-B 7-lens Gate A + Gate B |
| Minor (new topic pointer in §3; decay value bump; doc-pointer update on doc-move) | Single-lens Coach pre-completion + commit-body justification |
| Trivial (typo, link fix, frontmatter date refresh post-verification) | Direct commit, no gate |

This tiering matches `feedback_consistency_coach_pre_completion_gate.md` and `feedback_round_table_at_gates_enterprise.md` "Anti-pattern for Class A: running the 7-lens fork on a pure-legal question. Wastes voices on an answer that has no engineering/product/auditor trade-space." Same principle — don't waste 7-lens cycles on trivial overlay maintenance.

---

## §7 CI gate vs existing memory-hygiene infrastructure check

**Double-build risk: HIGH.**

| Existing infrastructure | Proposed §7/§8 | Recommendation |
|---|---|---|
| `.agent/scripts/context-manager.py validate` walks `memory/*.md`, enforces YAML frontmatter, checks decay | New CI test `test_no_stale_doc_citation.py` walks codebase, enforces overlay-citation marker | **Unify in `context-manager.py validate`.** Extend to walk `docs/**/*.md` as a separate scope. Same validator binary. |
| `memory/MEMORY.md` index of topic pointers | §3 overlay registry of topic pointers | **Mirror the pattern.** Overlay is to docs what MEMORY.md is to memory. Same shape. |
| Memory topic files have `name / description / type / decay_after_days / last_verified` | Proposed docs frontmatter `title / topic_area / last_verified / decay_after_days / supersedes / superseded_by / posture_overlay_authoritative` | **Align field names.** `title=name`, `topic_area=type`. Add docs-specific fields (`supersedes / superseded_by / posture_overlay_authoritative`) as optional. |
| `.github/workflows/memory-hygiene.yml` runs validate on `.agent/` changes | Proposed CI gate runs on overlay changes | **Same workflow.** Add `docs/**` to the path filter. |

**The §7 gate has its own additional concern (per Engineering lens):** allowed citation forms `posture-overlay:CURRENT(<topic>)` and `posture-overlay:SUPERSEDED(<doc>, reason)` are net-new conventions with **0 producers and 0 consumers** today. Per `feedback_directive_must_cite_producers_and_consumers.md`, a convention with no producers is not yet a convention. Recommend: defer the citation-form gate to a follow-up sprint once we know whether the overlay-pointer pattern is actually used in practice. Ship §3-registry + §8-frontmatter-on-docs FIRST, observe adoption, add the citation gate as a third layer.

---

## Cross-fork consistency check

**Cross-fork with BAA-drafting Gate A (`audit/coach-master-baa-drafting-gate-a-2026-05-13.md`):**

| BAA Gate A finding | Overlay draft posture | Consistent? |
|---|---|---|
| (b)-hybrid: interim BAA in 72h | Overlay §4 says "Task #56" — does not pre-commit to the (b)-hybrid path | ✓ Overlay correctly does not legislate WHICH path the BAA work takes |
| Whole-inventory audit as parallel matter | Overlay §4 "Whole-legal-document-inventory audit" foundational gap | ✓ Surfaces the gap; correctly does NOT commit engineering to a date |
| `/legal/baa` route must ship with interim BAA | Not in overlay (operational engineering action) | ✓ Out of scope for overlay |
| `is_acknowledgment_only` schema migration | Not in overlay | ✓ Out of scope |
| Customer notification path with 30-day re-sign | Not in overlay | ✓ Out of scope |

**Conclusion: no conflicts with BAA Gate A.** The overlay's §4 entry on BAA correctly defers to Task #56 without locking the path.

**Cross-fork with subprocessor re-audit draft (`audit/baa-subprocessors-reaudit-draft-2026-05-13.md`):**

| Re-audit finding | Overlay draft posture | Consistent? |
|---|---|---|
| Re-audit draft is **pending its own Class-B Gate A** | Overlay §4 says it supersedes BAA_SUBPROCESSORS.md AS OF 2026-05-13 | **CONFLICT.** Overlay claims the supersession is effective today; the re-audit draft has not been approved yet. |
| Re-audit identifies 14 subprocessors, banned-language fixes, factual corrections | Overlay does not enumerate these | ✓ Out of scope (overlay only registers pointers) |

**Fix:** §4 entry for BAA_SUBPROCESSORS should say "Supersession effective upon Class-B Gate A APPROVE of the re-audit draft" — not "2026-05-13."

---

## Specific cross-cutting pressure-tests

1. **Is the overlay itself in violation of its own rules?** YES — see Coach concern 5. §4 cites BAA_SUBPROCESSORS without the `SUPERSEDED` marker. Fix: explicit `OVERLAY_REGISTRY_FILES` allowlist in §7.

2. **Is the decay cadence in §5 right? 30 days for the overlay itself?** **Too tight.** 30 days means the overlay needs re-verification 12 times per year. With Class-B governance (per current §6), that's 12 round-tables per year for an artifact that mostly enumerates pointers — too much. Recommend **90 days for the overlay itself** when §6 governance is tiered (per Coach concern 3); the overlay's *contents* (per-doc decay) handle the high-frequency churn at the leaf level. 90 days for the overlay matches the operational-runbooks tier in §5 and aligns with the project's existing memory `decay_after_days` defaults.

3. **§3 missing topic areas?** Yes — see §3 completeness check above. 11+ missing.

4. **Should this be a single doc, or a registry pointing to per-topic sub-docs?** **Registry-of-pointers, NOT enumerated-state.** See Coach concern 1. The current draft tries to be both an index AND a state-snapshot; the state-snapshot half goes stale within days. Split: overlay = pointer index; each sub-doc carries its own state in its frontmatter; CI gate walks the frontmatter.

---

## Top 5 P0 findings ranked

1. **§3 registry: restructure from enumerated-state-table to pointer-index** — the single biggest design issue. Today's table will be stale on day 1 (5 of 14 rows are already inaccurate per the Engineering grep). Per-doc state lives in each doc's frontmatter (§8 standard), overlay only carries pointers + topic-area-owner. Matches the MEMORY.md / topic-files pattern. **Cost: redesign §3.**

2. **§7/§8 CI gate + frontmatter: unify with existing `context-manager.py validate` instead of building a parallel system** — the proposed `test_no_stale_doc_citation.py` + `posture_overlay_authoritative` frontmatter are 80% redundant with the existing memory-hygiene workflow. Extend the existing script to cover `docs/**/*.md`. Align field names (`title=name`, `topic_area=type`). **Cost: 1-2 hour extension of an existing script vs. building parallel infrastructure.**

3. **§3 OVERDUE entries lack remediation pointers (HIPAA-auditor BLOCK)** — self-disclosing gaps without naming remediation is materially WORSE auditor posture than not disclosing at all. Every OVERDUE row must carry "Task #N target YYYY-MM-DD" or reclassify as "PENDING REFRESH" with the same. Also: ADD §164.308 Risk Analysis cadence, Workforce Training, Sanction Policy, Activity Review, Contingency Plan, plus Vault Transit / Stripe billing / Privacy Policy / ToS / AUP / Substrate Integrity Engine rows. **Cost: 2 hour table extension + Task # assignment with privacy officer.**

4. **§6 governance: tier the workflow** — full Class-B Gate A + Gate B for every overlay update creates stale-by-friction. Tier: major / minor / trivial with appropriate gates. Matches `feedback_consistency_coach_pre_completion_gate.md`. **Cost: 1 paragraph rewrite of §6.**

5. **§4 supersession dates premature/inaccurate + Coach concern 5 self-violation** — BAA_SUBPROCESSORS supersession claimed effective 2026-05-13 but supersessor draft is pre-Gate-A; HIPAA_FRAMEWORK supersession date attribution mis-blames counsel; overlay itself cites superseded docs without the §7-required marker. Fixes: gate supersession on Gate A APPROVE; split HIPAA_FRAMEWORK supersession into two events; add explicit `OVERLAY_REGISTRY_FILES` allowlist exemption in §7. **Cost: rewrite §4 supersession rows + add §7 paragraph.**

---

## Final recommendation

**APPROVE-WITH-FIXES.** The overlay concept is correct, load-bearing, and the right structural response to Counsel Rule 5. §1/§2 (with the Lens 1 fix on additional governing rule-sets) and §5/§6 (with the Lens 6 tiering fix) are sound. §3/§4/§7/§8 require the restructure described in the top 5 findings.

**Recommended structure for §3: registry-of-pointers (NOT single enumerated table).** The overlay enumerates topic areas → owning doc. Each owning doc carries its own state in YAML frontmatter (using the §8 standard, which is itself unified with the existing memory-hygiene schema). The §7 CI gate (eventually) walks the frontmatter and asserts freshness — but ship this as a third-layer gate AFTER the pointer-index + frontmatter-on-docs are live and producers exist.

**The overlay's value is not in being a state-snapshot; it is in being the SINGLE place a contributor checks to know "which doc owns topic X?" The state of doc X is carried by doc X.**

**Path to Gate B:** Address the top 5 P0 findings; re-issue draft as `audit/posture-overlay-draft-2026-05-13-v2.md`; dispatch fresh-context Class-B Gate B fork to verify P0 closure. On Gate B APPROVE, move to `docs/POSTURE_OVERLAY.md` + cite both gate verdicts in commit body.

---

**File at:** `/Users/dad/Documents/Msp_Flakes/audit/coach-posture-overlay-gate-a-2026-05-13.md`
