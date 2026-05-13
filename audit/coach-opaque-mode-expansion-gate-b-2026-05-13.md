# Coach Class-B Gate B — Task #53 Opaque-Mode Expansion v2 — 2026-05-13

**Deliverable:** `audit/opaque-mode-expansion-design-2026-05-13.md` (v2)
**Gate A reference:** `audit/coach-opaque-mode-expansion-gate-a-2026-05-13.md` (APPROVE-WITH-FIXES, 3 P0s)
**Lenses applied:** Steve (SRE/operator), Maya (HIPAA-auditor + attorney), Carol (Counsel/Privacy), Coach (Consistency)
**Verdict:** **APPROVE — PUBLISH / SHIP Phase 0 ready**

---

## 100-word summary

v2 cleanly closes all three Gate A P0s without regression. The split-recipient model (P0 #1) eliminates the operator-vs-customer ambiguity by making recipient class the gating axis: customer-facing email paths join `_OPAQUE_MODULES`; operator-facing paths stay verbose and out of Rule 7 scope. The SRA-reminder reclassification is correct — practice owners are the customer surface. Phase 2 (public-verify path opacity) deferral to its own Class-B Gate A is the right move given the auditor-kit URL contract regression risk on 18 months of issued kits. Phase 3 PagerDuty-BAA reassignment to Rule 8 sibling-task respects the counsel-priority taxonomy. Class-hint subjects, separate test files with shared `_opacity_ast.py` helper, and dedup_key/severity preservation are all applied verbatim. Phase 0 is shippable; Phases 1-3 spawn separately. Ship Phase 0 this sprint.

---

## Closure matrix

| # | P0 Issue | v2 Treatment | Verified | Status |
|---|---|---|---|---|
| P0 #1 | §2.A operator-vs-customer classification ambiguity | Split-recipient model added in v2 header + §3 Phase 0 step 1; SRA-reminder (`email_alerts.py:947`) explicitly reclassified as customer-facing (sent to practice owner); operator-facing paths declared out-of-scope; `_OPAQUE_MODULES` gates customer-facing only | Header §1, §3 step 1, §2.A table footnote | **CLOSED** |
| P0 #2 | Phase 2 public-verify path opacity ships with auditor-kit URL contract regression risk | Deferred to own Class-B Gate A; preconditions enumerated (kit version 2.1→2.2 lockstep with D1 P0 #5, HTTP 308 redirect, 24-month deprecation window); explicitly removed from Phase 0/1 scope | Header §1, §3 Phase 2 block | **CLOSED** |
| P0 #3 | PagerDuty BAA-on-file precondition mis-scoped under Rule 7 | Removed from Task #53; reclassified as Rule 8 (counsel-priority #2); spawned as sibling task per subprocessor v2 §5 future-engineering item; engineering action noted (new task #57 as sibling of Task #55) | Header §1, §3 Phase 3 block | **CLOSED** |

---

## Concrete recommendations verification

| # | Recommendation | Source Lens | Verified in v2 | Status |
|---|---|---|---|---|
| R1 | Class-hint subjects over fully-generic | Lens 5 PM + Lens 6 medical-tech | §3 Phase 0 step 3 — all 5 rewrites use class hints (`"Compliance digest"`, `"Compliance monitoring active"`, `"Compliance alert"`, `"Client non-engagement"`, `"SRA remediation reminder"`); no `"Action required"` placeholder anywhere | **APPLIED** |
| R2 | 3 separate test files + shared `_opacity_ast.py` helper | Lens 3 Coach | §3 Phase 1 last bullet — `test_webhook_opacity_pagerduty.py` separate; §4 skeleton names `test_webhook_opacity_harmonized.py` + `test_public_verify_path_opacity.py`; helper named explicitly | **APPLIED** |
| R3 | PagerDuty scrubber preserves `dedup_key` + `severity` enum | Lens 1 Steve | §3 Phase 1 bullet 1 — "MUST preserve `dedup_key` + `severity` enum (Lens 1 Steve — operationally-required for routing and alert-fatigue control)"; §2.B already preserved severity but `dedup_key` is newly explicit | **APPLIED** |

---

## Adversarial 4-lens spot checks

- **Steve (SRE):** PagerDuty scrubber preserves the two fields incident commanders actually need (dedup_key for storm-suppression, severity for routing). No regression to alert ops. **OK.**
- **Maya (HIPAA-auditor + attorney):** Phase 2 deferral is correct — pulling the rug on `site_id`-in-path URLs before kit version 2.2 lockstep would brick auditor verification on already-issued kits and create a §164.524 access-right liability. The 24-month deprecation window matches counsel's prior 18-month patterns. **OK.**
- **Carol (Counsel/Privacy):** Split-recipient model is the canonically correct framing — Rule 7 protects unauthenticated channels reaching the data subject's universe; operator-internal channels are a different threat surface (Rule 9 territory). SRA-reminder going customer-facing is the conservative read; if a reviewer later argues it's operator-bound under some MSP delegation, the gate stays opaque (false-positive direction is safe). **OK.**
- **Coach (Consistency):** Three test files + shared helper matches the §6 round-table pattern from Session 218 auditor-kit determinism (multiple gate files, shared primitives module). No monolithic-gate antipattern. Phase 0 stays under 150 LOC, single PR — keeps Class-B small-batch invariant. **OK.**

---

## Residual notes (non-blocking, carry forward)

- §6 open question (b) — opaque token determinism for PagerDuty (hash vs random) — must be resolved before Phase 1 Gate A; deterministic recommended (operator correlation) with HMAC-key rotation per-quarter to bound rainbow-table risk. Carry as Phase 1 Gate A input.
- §6 open question (c) — already resolved in v2 via R1 (class-hint chosen). Recommend striking from §6 in next revision to avoid leaving a closed question listed as open.
- Phase 4 in-portal notification scan deferred pending grep — TaskCreate followup recommended.

---

## Sign-off

**Phase 0 (email subjects + `_OPAQUE_MODULES` expansion + gate extension):** **APPROVE — SHIP THIS SPRINT.**

Author may proceed to implementation. Commit body MUST cite both Gate A (`audit/coach-opaque-mode-expansion-gate-a-2026-05-13.md`) and Gate B (this file). Pre-push full-CI-parity sweep required per round-table TWO-GATE protocol. Phase 1-3 spawn their own Gate A on entry.

— Coach (Class-B Gate B, 2026-05-13)
