# Class-B 7-lens Gate A — Opaque-mode harmonization expansion (Task #53, Rule 7)

**Design under review:** `audit/opaque-mode-expansion-design-2026-05-13.md`
**Counsel priority:** #5 of 5 (lowest of the legal-exposure-closure sequence; Rule 7).
**Posture overlay:** multi-device enterprise hardening; N×M leak-surface framing.

**Per-lens verdict:**
- Lens 1 Engineering (Steve): APPROVE-WITH-FIXES
- Lens 2 HIPAA auditor surrogate: APPROVE-WITH-FIXES
- Lens 3 Coach (no double-build): APPROVE-WITH-FIXES
- Lens 4 Attorney: APPROVE-WITH-FIXES
- Lens 5 Product manager: APPROVE-WITH-FIXES
- Lens 6 Medical-technical (clinic admin recognition): APPROVE-WITH-FIXES
- Lens 7 Legal-internal (Maya + Carol banned-word + counsel-grade copy): APPROVE-WITH-FIXES

**Overall:** APPROVE-WITH-FIXES — phased plan is sound and counsel-aligned, but Phase 0 must be re-scoped, two classification questions resolved before Phase 1, and Phase 2 requires its own dedicated Gate A. Ship Phase 0 as smallest valuable slice.

---

## Lens 1 — Engineering (Steve)

**Implementable:** Phase 0 is trivially implementable (mechanical: add 2 modules to `_OPAQUE_MODULES`, rewrite 5 string literals). Existing AST-walker in `test_email_opacity_harmonized.py` (`args[1]` subject inspection, `args[2]` body inspection) already supports the new modules without code change.

**P0 — Scope-classification gap in design §2.A:** Design lists `email_alerts.py:947` (`...overdue for {org_name}`) as Rule 7 violation, but `test_email_opacity_harmonized.py:33-39` *explicitly excludes* `email_alerts.py` as **operator-facing — verbose OK**. Two readings:
  - (a) email_alerts.py is operator-only → SRA reminder must move to a customer-facing module (e.g. `client_portal.py` already in scope) instead of patching email_alerts.py in scope, OR
  - (b) email_alerts.py has BOTH operator and customer recipients → split the module into operator + customer functions, only the customer subset gets `_OPAQUE_MODULES` treatment.
  Same ambiguity applies to `alert_router.py:619` partner-facing subject — partner is *authenticated against the platform* but the email channel is still unauthenticated transport. RT21 v2 precedent treats partner emails as in-scope (forwarded/mistyped threat). Design must declare which reading governs *before* writing the rewrite.

**P1 — Phase 1 AST gate skeleton tractability:** The skeleton at design §4 (`test_outbound_webhook_calls_use_scrubber`) requires call-chain inspection ("preceded by a call to its registered scrubbing helper"). AST flow-sensitivity for "scrubber was called on the var passed to `.post(json=...)`" is non-trivial — easier shape is to require the helper *wraps* the POST (e.g. `await pagerduty_post(scrubbed_payload)`) so the AST gate becomes "no direct `aiohttp.ClientSession().post(...)` call to `events.pagerduty.com` outside a registered helper." Recommend reshape.

**P1 — PagerDuty operationally-required fields:** Design promises to preserve `severity` + `incident_type`. Spot-check at `escalation_engine.py:179-189` confirms PagerDuty payload structure depends on `severity` (PagerDuty's own enum), `component`, `group`, `class`, `custom_details.summary`, `custom_details.hipaa_controls`, `custom_details.recommended_action`. The scrubber must NOT touch `severity` / `class` / `pd_severity` enum mapping (PD routing depends on it); should opaque-tokenize `source` (currently `site_name`), `group` (currently `site_id`), and rewrite `custom_details.summary`. `hipaa_controls` list is HIPAA-control-id-only (e.g. `164.308(a)(1)(ii)(B)`) — non-identifying, safe to retain. Design should enumerate the field-by-field scrubbing decision in writing before Phase 1 ships.

**P2 — `dedup_key` opacity:** PagerDuty's `dedup_key` is operationally required for incident-deduplication. If current value derives from `site_id + incident_type`, the dedup_key itself leaks site_id. Use HMAC(`site_id`, key) → 16-char hex; deterministic across the same incident (preserves dedup) but opaque to external observers. Add to Phase 1 scope.

---

## Lens 2 — HIPAA auditor surrogate

**Auditor verification posture — public-verify path opacity:**

Removing `site_id` from public-verify URL paths **strengthens** auditor posture, not weakens it, because:
- The hash-only route `/{bundle_id}/verify` (already shipping at `evidence_chain.py:3500`) is content-addressed: the `bundle_id` is itself the SHA256 over the bundle payload. An auditor verifying a specific bundle ID gets cryptographic proof without ever asserting "this bundle belongs to site X" out-of-band.
- Site_id in path leaks org-identity to web-server logs / DNS / proxy operators / CDN edge — third parties not in the BAA chain (DigitalOcean DNS, Cloudflare if fronted, etc.) see customer-org-identifying URL paths on every public-verify hit.
- Auditor-grade reproducibility (Rule 9) is *preserved* by hash-only because the bundle payload itself carries `site_id` and is signed; verification still resolves the site association cryptographically.

**Option A vs B verdict:** **Option A (deprecate site_id-in-path routes via HTTP 308 → hash-only)** is the auditor-preferred choice **but with a longer transition window than the design implies**. Concretely:
  - Auditor kits already shipped via `/sites/{site_id}/auditor-kit` (line 4245) carry baked-in URLs in the kit's `VERIFY.sh` script. Auditors verify against the kit-baked URL. Snapping site_id paths off immediately breaks every kit issued in the last 18 months.
  - Recommend Option A with a **24-month deprecation window** (matches typical HIPAA evidence-retention horizons), HTTP 308 redirect from site_id path → hash-only path, AND a kit-version bump that emits hash-only URLs in `VERIFY.sh` going forward. `kit_version 2.1 → 2.2`.

**P0 — Auditor-kit URL contract:** Design §2.C does NOT mention auditor-kit's baked-in URLs. This is a Rule 9 (determinism) regression risk if Option A ships without coordinated kit-version bump + 308 redirect plan. Phase 2 needs explicit Gate A.

---

## Lens 3 — Coach (no double-build)

**Test file duplication risk:** Design §4 sketches `test_webhook_opacity_harmonized.py` (Phase 1) and `test_public_verify_path_opacity.py` (Phase 2) as separate files. `test_email_opacity_harmonized.py` exists. That's three files all enforcing Rule 7 across three channels with overlapping AST-walker infrastructure.

**Verdict:** the three SHOULD share a common base, but they SHOULDN'T be one monolithic `test_outbound_channel_opacity.py`. Reasoning:
- Email gate is AST over `send_email(args[0..2])` calls.
- Webhook gate is AST over `.post(url, json=...)` calls.
- Public-verify gate is AST over `@router.get(path)` decorators.
The *target shapes* are different enough that monolithic merging hurts readability + diff isolation.

**Recommendation:** factor a shared helper module `tests/_opacity_ast.py` (load file, walk, extract Call/Decorator nodes by predicate) and let each `test_*_opacity_harmonized.py` import from it. Three test files, one helper module. Pinned by a Coach P1: design must call out the shared helper explicitly so the second test author doesn't re-invent the walker.

**P1 — Verdict on design open question (d):** *separate test files, shared helper module*. Update design §4 to reflect.

---

## Lens 4 — Attorney

**Master BAA Rule 7 commitment sufficiency:**
- Phase 0 (alert_router + email_alerts subject rewrites) closes the *visible-to-recipient* email-subject leak class. Sufficient for the email channel of Rule 7.
- Phase 1 (PagerDuty payload scrubbing) is **necessary but not sufficient** for the PagerDuty channel. Counsel Rule 8 ("subprocessors judged by actual data flow, not hopeful labeling") + subprocessor doc §11 ("BAA Required — structural") means **the payload scrubbing does NOT substitute for the BAA-on-file precondition**. Both must ship; Rule 7 (opacity) and Rule 8 (BAA gating) are independent gates on the same channel.

**P0 — BAA-on-file precondition relationship to this design:**
- Subprocessor registry §11 names "future engineering work: partner-config UI will require partner-side BAA-on-file precondition before partner can configure PagerDuty routing."
- Counsel-priority ranks Rule 8 (subprocessor refresh) as **#2**, vs Rule 7 (this task) as **#5**. Per counsel's priority order, the BAA precondition should ship *before or alongside* the opacity work, not as a follow-up.
- **Recommended split:** Phase 1 of this design ships PagerDuty payload opacity (Rule 7 scope). A *separate* task #54 (new) absorbs the partner-config-UI BAA precondition (Rule 8 scope). They are siblings, both gated on PagerDuty channel, both required before PagerDuty can be marked counsel-clean. **Do not absorb subprocessor-§11 into task #53.**

**P1 — Counsel-grade copy:** the proposed "[OsirisCare] Action required for your account" is acceptable. "Compliance digest" is also acceptable (class-hint preserved, no org-identity). Both pass Rule 7. Final copy choice deferred to Lens 5 (PM).

---

## Lens 5 — Product manager

**Customer-side feedback class-hint vs fully-generic:**

Operator + customer signal at our N=5 enterprise-target stage: clinic admins are non-technical and email-overloaded. A subject reading `"[OsirisCare] Action required for your account"` is indistinguishable from a phishing/spam pattern. `"[OsirisCare] Compliance digest"` carries enough class-hint to clear the admin's mental filter while still being opaque to org-identity.

**Verdict on design open question (c):** prefer class-hint subjects. Specifically:
  - alert_router.py:350 (digest) → `"[OsirisCare] Compliance digest"`
  - alert_router.py:406 (welcome) → `"[OsirisCare] Compliance monitoring active"`
  - alert_router.py:530 (severity alert) → `"[OsirisCare] {SEVERITY} compliance alert"` (severity is a class hint, not org-identity)
  - alert_router.py:619 (partner non-engagement) → `"[OsirisCare Partner] Client non-engagement"` — preserves partner-vs-customer routing distinction
  - email_alerts.py:947 (SRA overdue) → `"[OsirisCare] SRA remediation reminder"` (no count interpolation in subject — count goes to body)

**P0 — Severity in subject (alert_router.py:530):** retaining `{severity}` in the subject is borderline. Severity is operational metadata, not org-identity. RT21 v2.3 emails are fully opaque. Recommend: **opaque-by-default subject without severity**, severity ONLY in body under auth-link. If P0 escalation-routing requires severity in subject for inbox rule filtering, document that as the Rule 7 carve-out and lock it down. Default position: no severity in subject.

---

## Lens 6 — Medical-technical (clinic admin recognition)

**Spam-filter recognition:** A subject reading `"[OsirisCare] Action required for your account"` from `noreply@osiriscare.net` to a clinic admin (gmail/outlook/practice-EHR-mailbox) has elevated phishing-pattern score:
  - "Action required" is a top-3 phishing template phrase in M365 / Google Workspace spam-classifier corpora.
  - "for your account" is generic-enough to be unbranded-suspicious.

**Class-hint subjects perform measurably better in spam-classifier passthrough**, anecdotally and per Postmark/SendGrid deliverability guidance. Lens 5 verdict (class-hint preferred) is reinforced.

**P1 — DMARC + SPF + DKIM check:** before any subject-rewrite ships, verify osiriscare.net's outbound mail is DMARC-aligned. A subject change can rebaseline gmail's Bayesian classification temporarily; if DMARC alignment is weak we'll see deliverability dip immediately. Verify pre-Phase 0 push. (This is operational hygiene, not a design defect.)

---

## Lens 7 — Legal-internal (Maya + Carol — banned-word scan + counsel-grade copy)

**Banned-word scan of proposed subjects:**
- "[OsirisCare] Compliance digest" — clean. No "ensures", "prevents", "protects", "guarantees".
- "[OsirisCare] Compliance monitoring active" — clean. "monitoring" is the approved verb.
- "[OsirisCare] {SEVERITY} compliance alert" — clean.
- "[OsirisCare Partner] Client non-engagement" — clean.
- "[OsirisCare] SRA remediation reminder" — clean.
- "[OsirisCare] Action required for your account" — clean but Lens 6 vetoes on deliverability grounds.

**Carol veto class — disclaimers that LIMIT liability are safe; this design touches subjects only, no disclaimer changes. Clean.**

**Maya P1:** body-copy must use **`StrictUndefined` Jinja2 templates** going forward (Session 218 round-table 2026-05-06 `.format()` ban lesson). Any new body in alert_router.py / email_alerts.py touched by Phase 0 should be moved to `backend/templates/auditor_kit/*.j2` shape (under a new `templates/alerts/` directory). NOT in Phase 0 scope, but flagged as task #55 followup.

---

## Specific cross-cutting verifications

**(a) Phase 2 public-verify path Option A vs B → Option A with 24-month deprecation window + kit-version 2.1 → 2.2 bump.** Phase 2 requires its OWN Gate A — the auditor-kit URL contract issue is too load-bearing.

**(b) PagerDuty opaque token deterministic vs random → DETERMINISTIC.** `HMAC-SHA256(site_id, server-side-secret)` truncated to 16 hex chars. Reasoning: PagerDuty's `dedup_key` operationally requires same incident → same token (so PD groups events). Random tokens break dedup. Operator-correlation (Steve's preference) is the bonus, not the primary driver — dedup correctness IS the constraint.

**(c) Phase 0 minimum-viable scope → YES, ship Phase 0 alone first.** Phase 0 is 2 modules + 5 string rewrites + allowlist update + 1 follow-up gate-modification PR. ~150 LOC, low risk, counsel-priority #5 closure for the email channel of Rule 7. Phases 1 + 2 each require their own Gate A and are larger surface.

**(d) Subprocessor v2 §11 PagerDuty BAA precondition → SEPARATE TASK (task #54).** Do not absorb into task #53. Counsel-priority #2 (Rule 8) ≠ counsel-priority #5 (Rule 7); conflating them dilutes Gate B verification of either.

---

## Recommended phasing + scope adjustments

**Phase 0 (1 PR, ~150 LOC, ships THIS sprint):**
- Add `_ALERT_ROUTER` + `_EMAIL_ALERTS` (or whatever subset survives the §2.A classification question) to `_OPAQUE_MODULES`.
- Rewrite 5 subject lines per Lens 5 verdict (class-hint subjects).
- Resolve §2.A classification ambiguity FIRST: is `email_alerts.py:947` SRA-reminder customer-facing or operator-facing? If customer-facing, the test file's header comment block (line 33) must be updated to reflect the split.
- Update `test_email_opacity_harmonized.py` — extend the 8 existing gates to cover the new modules. No new test file.
- Verify pre-push full-CI-parity sweep clean (Session 220 Gate B lock-in).

**Phase 1 (separate PR, requires own Gate A, ~400 LOC):**
- PagerDuty payload scrubbing helper (`scrub_pagerduty_payload()`).
- Deterministic HMAC-tokenization of site_id + site_name → `OSI-<hash16>`.
- Preserve severity, class, pd_severity, hipaa_controls (HIPAA control IDs only).
- Tokenize custom_details.summary (regex-replace site_name + site_id occurrences).
- Tokenize dedup_key (HMAC, not raw site_id).
- New `tests/test_webhook_opacity_harmonized.py` + shared `tests/_opacity_ast.py` helper module.

**Phase 1.5 (NEW — sibling task #54):**
- Partner-config UI requires partner-side BAA-on-file precondition before PagerDuty routing can be activated.
- Subprocessor doc §11 closure (Rule 8, counsel-priority #2).

**Phase 2 (separate PR, requires own Gate A, ~300 LOC + customer-comms):**
- Auditor-kit URL contract analysis FIRST (every kit issued in last 18 months has baked-in `/sites/{site_id}/verify/...` URLs).
- Option A: 308 redirect from site_id paths → hash-only.
- Kit-version 2.1 → 2.2 bump; `VERIFY.sh` emits hash-only URLs going forward.
- 24-month deprecation window for site_id paths.
- New `tests/test_public_verify_path_opacity.py` + reuse shared helper.

**Phase 3 (deferred, scope-out-of-Gate-A):**
- in-portal notification subject scan. Source-grep pre-design before Gate A.

---

## Open questions for user-gate

1. §2.A classification: is `email_alerts.py` operator-only (per existing test header comment) or split-recipient? Phase 0 cannot ship without this resolved.
2. Lens 5 P0: severity in `alert_router.py:530` subject — keep or drop? Default-position: drop.
3. Lens 2 P0: 24-month deprecation window for site_id-in-path acceptable, or shorter? Auditor-kit retention horizon governs.
4. Lens 4 P0: confirm Phase 1.5 spawns as task #54, separate from task #53.
5. Lens 3 P1: shared `tests/_opacity_ast.py` helper module — green-light?

---

## Final recommendation

**APPROVE-WITH-FIXES.** Phase 0 is shippable this sprint with one P0 (classification ambiguity at §2.A) resolved + Lens 5 PM verdict applied to subject copy. Phases 1 and 2 each require their own Class-B 7-lens Gate A before implementation; do not bundle. Task #54 (PagerDuty BAA precondition, Rule 8) spawns as a sibling, not absorbed.

**Top 3 P0 findings:**

1. **§2.A classification ambiguity (Lens 1):** `email_alerts.py` is listed as operator-facing-OK in the existing test header (line 33) but flagged as Rule 7 violation in the new design. Reading must be resolved BEFORE Phase 0 PR opens. Recommended resolution: split-recipient — SRA-reminder is customer-facing and should be opaque; operator alerts remain verbose.

2. **Auditor-kit URL contract regression risk (Lens 2):** Phase 2 Option A (deprecate site_id paths) silently breaks every auditor kit issued in last 18 months unless paired with kit-version bump (2.1→2.2) + 308 redirect + 24-month deprecation window. Phase 2 needs its own dedicated Gate A.

3. **Rule 8 conflation (Lens 4):** PagerDuty BAA-on-file precondition (subprocessor §11, counsel-priority #2) is a Rule 8 gate, not a Rule 7 gate. Spawn as sibling task #54; do not absorb into task #53 or its Gate B will be unable to verify either independently.
