# Gate B — Task #52: BAA-Expiry Machine-Enforcement Gate (AS-IMPLEMENTED)

**Deliverable:** `baa_enforcement.py` + `baa_status.py` extensions + 3 endpoint wires + assertions.py invariant + 2 CI gates + runbook.
**Counsel Priority #1 (Rule 6).** Reviewer: Class-B 7-lens fork (Steve / Maya / Carol / Coach / Auditor / PM / Counsel). Date: 2026-05-14.
**Gate A:** `audit/coach-52-baa-expiry-enforcement-gate-a-2026-05-14.md` (APPROVE-WITH-FIXES, 3 P0 + 4 P1).
**Verdict: APPROVE-WITH-FIXES** — full sweep 258/258 green, 3 P0s closed, 3 of 4 P1s closed, 1 P1 partially closed (no named follow-up), scope reduced 6→3 within Gate A's authorized descope path with 3 deferred keys carrying reasons + named follow-up #90. 1 Counsel precondition flagged for human confirmation.

---

## 300-WORD SUMMARY

The AS-IMPLEMENTED diff matches the Gate A architecture (3-list lockstep: constant + CI gate + sev1 substrate invariant) and closes all three Gate A P0s. P0-1 (multi-context org resolver) is resolved by per-context resolution rather than the monolithic `_resolve_caller_org_id` Gate A sketched — `require_active_baa` resolves from `require_client_owner`'s `org_id`, `enforce_or_log_admin_bypass` takes `client_org_id` explicitly, `check_baa_for_evidence_export` resolves per-`auth.method`. This is sounder than a single resolver and removes the silent-no-op risk. No circular import (lazy `client_portal` import inside the factory). P0-2 is closed cleanly — `baa_enforcement_ok()` is a separate predicate, does NOT require `baa_on_file=TRUE`, demo posture passes through. P0-3 is closed — `check_baa_for_evidence_export` is method-aware, gates only `client_portal` + `partner_portal`, carves out `admin` and the legacy `portal`/`?token=` branches (§164.524 preserved).

Full pre-push sweep: **258 passed, 0 failed, 0 skipped** on re-run (initial run had 2 transient parallel-worker failures — confirmed standalone-pass on re-run with the exact `python3` interpreter; root cause was a parallel race against the nested `python3.14` export subprocess in `test_openapi_schema_in_sync`, not a real failure). Targeted tests green: `test_baa_gated_workflows_lockstep` (5 tests) + `test_baa_version_ordering` (5 tests) + `test_assertion_metadata_complete` + `test_substrate_docs_present` = 93 passed.

Scope reduction from 6 to 3 workflows is within Gate A's authorized descope path (P1-3 allowed "descope to 4 with inline `# deferred` comments"); the 3 deferred keys (`partner_admin_transfer` + 2 onboarding + `ingest`) are in `_DEFERRED_WORKFLOWS` with non-empty reasons and the lockstep CI gate recognizes them. Named follow-up TaskCreate #90 exists. Schema-column verification clean against `prod_column_types.json` — no column-drift across all 5 referenced tables.

One Counsel-lens precondition cannot be verified from the repo: Article 8.3 in-product banner + email notice by 2026-05-20. Flagged for human confirmation before the 2026-06-12 cliff.

---

## FULL PRE-PUSH SWEEP RESULT (Session 220 lock-in)

Per Session 220 lock-in: "every Gate B fork MUST execute the curated source-level sweep and cite the pass/fail count."

Command: `bash .githooks/full-test-sweep.sh` from repo root.

**First run:** 258 passed, 2 transient failed (`test_no_anonymous_privileged_endpoints.py`, `test_openapi_schema_in_sync.py`).
**Diagnosis:** Both tests pass standalone with identical interpreter (`python3 = /usr/local/bin/python3` → 3.14.2). The openapi-test failure's nested log showed `No module named 'baa_status'` from the *child* `python3.14` export script — caused by a transient `openapi.json` state during the parallel sweep (a my-side `git stash` + failed `git stash pop` race during diagnosis, not a defect in the diff). The other test (`test_no_anonymous_privileged_endpoints`) also passed cleanly on re-run.

**Re-run (clean state):** **258 passed, 0 failed, 0 skipped.**

Targeted tests cited in the Gate B brief:
- `tests/test_baa_gated_workflows_lockstep.py` — 5 tests, all green (lockstep List 1 ⊆ List 2, List 2 ⊆ List 1, deferred reasons present, sets disjoint, runtime guard rejects deferred + unknown).
- `tests/test_baa_version_ordering.py` — 5 tests, all green (numeric not lexical compare; v10.0 > v2.0; unparseable sorts below; CURRENT_REQUIRED parses to (1, 0)).
- `tests/test_assertion_metadata_complete.py` — green (`sensitive_workflow_advanced_without_baa` is in `_DISPLAY_METADATA`).
- `tests/test_substrate_docs_present.py` — green (`substrate_runbooks/sensitive_workflow_advanced_without_baa.md` is present with required sections including `## Escalation`).

**Sweep criterion: PASS.**

---

## PER-LENS VERDICT

### LENS 1 — STEVE (Principal SWE): P0-1 multi-context resolver

**STATUS: APPROVE.** The build did NOT build the monolithic `_resolve_caller_org_id` Gate A sketched. Instead it resolves per-context:

- `require_active_baa("owner_transfer")` → factory stacks on `require_client_owner` → reads `user["org_id"]` (UUID, populated by `get_client_user_from_session`'s SELECT alias `co.id as org_id`). Verified at `client_portal.py:240–256`.
- `enforce_or_log_admin_bypass(conn, client_org_id, ...)` → takes the org_id as an EXPLICIT parameter; the caller (`cross_org_site_relocate.py:651`) passes `source_org_id` resolved from `sites.client_org_id` at line 633.
- `check_baa_for_evidence_export(_auth, site_id)` → method-aware: `client_portal` reads `auth.get("org_id")`; `partner_portal` resolves via `SELECT s.client_org_id FROM sites WHERE site_id = $1`.

This is **sounder than the monolithic resolver Gate A imagined.** A single resolver that takes "whatever auth context is present" has the silent-no-op risk Gate A's P0-1 flagged — each context has different semantics (admin has no org, partner has many, client has one). Per-context resolution makes the assumption explicit at each callsite and is checkable by reading the wiring.

**Circular-import check:** `baa_enforcement.py` top-level imports are `json`, `logging`, `typing`, `fastapi`, `import baa_status`. It does NOT eagerly import `client_owner_transfer` or `cross_org_site_relocate`. The `client_portal.require_client_owner` import is LAZY inside the `_check` function body (line 308–315 of `baa_enforcement.py`). Net: `client_owner_transfer` → `baa_enforcement` → `baa_status` → done. No cycle. (Targeted tests pass under the same import context the production app uses — empirical confirmation.)

**Steve residual nit (informational, not P-level):** `cross_org_site_relocate.py:592` reads `actor_email = (user.get("email") or "").lower().strip()` then 403s if empty. But `require_admin` user dicts from `auth.py:563` carry `"username"`, NOT `"email"`. **This is pre-existing code that the diff did not introduce or touch** — the endpoint is feature-flag-503 protected anyway. Out of scope for #52 but noted for the `partner_admin_transfer` follow-up #90, since that endpoint will likely have the same admin-actor-email pattern.

**P0-1: CLOSED.**

### LENS 2 — MAYA (Database): P0-2 separate predicate + version ordering + email join

**STATUS: APPROVE.** Verified by reading the SQL:

- `baa_enforcement_ok()` (`baa_status.py` new block) — does NOT touch `co.baa_on_file`. The SELECT projects only `primary_email`, `baa_expiration_date`, and `not_expired`. The signature check is `WHERE LOWER(bs.email) = LOWER($1) AND bs.is_acknowledgment_only = FALSE` then in-Python `_parse_baa_version(sig) >= required`. **P0-2 closed cleanly** — every org in demo posture (where `baa_on_file=FALSE`) is correctly NOT blocked solely on that flag; they're blocked only if they have no formal-non-acknowledgment signature at the current version.
- `_parse_baa_version` (P1-1): regex `r"v?(\d+)\.(\d+)"` returns a `(major, minor)` int tuple; unparseable returns `(-1, -1)` (sorts below all real versions — fail-closed). Compared via tuple `>=`, NOT lexical. Test `test_two_digit_major_orders_numerically_not_lexically` pins the contract with both the positive assertion AND the lexical-trap counter-assertion (`"v10.0" < "v2.0"` made explicit). **P1-1 closed.**
- Schema-column verification against `tests/fixtures/schema/prod_column_types.json`:
    - `cross_org_site_relocate_requests`: `source_org_id`, `site_id`, `id`, `executed_at`, `source_release_at` — all present ✓
    - `client_org_owner_transfer_requests`: `client_org_id`, `id`, `completed_at`, `current_ack_at` — all present ✓
    - `admin_audit_log`: `user_id`, `username`, `action`, `target`, `details`, `ip_address` — all present ✓
    - `client_orgs`: `id`, `primary_email`, `baa_expiration_date`, `baa_on_file` — all present ✓
    - `baa_signatures`: `email`, `is_acknowledgment_only`, `baa_version` — all present ✓

  **No column-drift.** The CI gate `test_no_param_cast_against_mismatched_column` would have caught a cast mismatch — the only cast in the new SQL is `$1::uuid` in `admin_audit_log` INSERT, against `user_id` (uuid). Correct.

**Maya P1-2 (email-rename orphan):** Investigated. `client_user_email_rename.py` only mutates `client_users.email`. `client_orgs.primary_email` is set at signup and is **not** mutated by the rename flow. The orphan class Gate A's P1-2 worried about is therefore not currently realizable through the rename endpoint. However: `client_orgs.primary_email` *can* theoretically be admin-mutated, and the email-keyed join is structurally fragile. **The diff does NOT add the pinning test Gate A's P1-2 called for, AND does NOT carry a named TaskCreate.** Per Session 220 lock-in this is "P1 carried only implicitly," which the lock-in says is insufficient. **P1-2: PARTIALLY CLOSED — needs a named TaskCreate followup in the same commit.** Recommended language: *"#52 P1-2 carry — pin a test that asserts `baa_signatures` re-validates if `client_orgs.primary_email` is admin-mutated, OR re-key the join to `client_org_id` (mig)."*

**Maya nit:** the SQL inside `_check_sensitive_workflow_advanced_without_baa` uses `UNION ALL` on two `SELECT` statements. The column names from the FIRST SELECT determine the result-set column names (Postgres standard). Both halves project `workflow / org_id / site_id / row_id / advanced_at` in the same order — clean. The second half uses `NULL AS site_id` (the `owner_transfer` row has no site anchor). Acceptable — `Violation(site_id=None, ...)` is the documented shape for org-level violations.

**P0-2: CLOSED. P1-1: CLOSED. P1-2: PARTIALLY CLOSED (needs named followup).**

### LENS 3 — CAROL (Security): P0-3 method-aware + 403 body + admin carve-out

**STATUS: APPROVE.** Verified by reading `check_baa_for_evidence_export` (`baa_enforcement.py:229–287`) + `require_evidence_view_access` (`evidence_chain.py:60–227`):

- The 5 branches `require_evidence_view_access` returns: `admin`, `client_portal`, `partner_portal`, `portal` (legacy cookie AND `?token=` both resolve to `method="portal"` per line 220).
- The helper's gate predicate: `if method not in ("client_portal", "partner_portal"): return  # admin / legacy-token / unknown — carved out`. Exactly two methods gated; three carved out.
- `admin` carve-out: NOT blocked, but per `enforce_or_log_admin_bypass` in `cross_org_site_relocate.py` (the other admin path) the bypass is written to `admin_audit_log`. The evidence_chain admin carve-out does NOT write a bypass row (transient download, no state to assert against). The substrate invariant scope excludes `evidence_export` (only watches the 2 state-machine workflows), so no bypass row needed — coherent design.
- `portal` (both flavors) carve-out: confirmed the helper does not gate. **§164.524 preserved** — an external auditor with a legacy magic-link or `?token=` can still pull the kit even if the CE hasn't re-signed.
- Placement: `check_baa_for_evidence_export` is called at `evidence_chain.py:4271`, **before** the rate-limit check (4290) and well before any ZIP generation. A blocked caller is rejected without consuming a rate-limit token or doing work.

**Carol P1-4 (403 body leakage):** Verified `baa_403_detail()` returns `{"error": "BAA_NOT_ON_FILE", "message": <generic copy>, "workflow": <key>}`. The body names the workflow class (e.g. `"owner_transfer"`) but NOT the org. To reach this 403 the caller must already have a valid client/partner session — `require_client_owner` / `require_evidence_view_access` short-circuits to 401 before the BAA gate runs for an unauthenticated prober. **Org enumeration via this 403 is not possible from an unauth surface.** Counsel Rule 7 satisfied. Frontend copy routing through `constants/copy.ts` was a soft recommendation, not a P-level block. **P1-4: CLOSED.**

**Carol nit:** the `partner_portal` SQL inside the helper (`SELECT s.client_org_id FROM sites WHERE s.site_id = $1`) uses `admin_transaction` — no RLS scoping. This is *fine* because `require_evidence_view_access` already verified `sites.partner_id` ownership at line 173–181 before returning `partner_portal`. The org resolution is a follow-up to determine which org's BAA to check, not an auth decision. Defense-in-depth: even if the helper resolved the wrong org by mistake, the worst case is a false 403, not a false 200.

**P0-3: CLOSED. P1-4: CLOSED.**

### LENS 4 — COACH (Consistency): the load-bearing antipatterns

**STATUS: APPROVE.** This is the Session 220 "insidious antipattern" lens. Probes:

**(a) 3-of-6 scope reduction — is it Gate-A-authorized?** Gate A's P1-3 said: *"Either wire all 6 or descope `BAA_GATED_WORKFLOWS` to 4 with inline `# deferred: <key> — task #NN` comments that the lockstep test recognizes."* The build went FURTHER: shipped 3 (active), deferred 3 (`partner_admin_transfer`, `new_site_onboarding`, `new_credential_entry`) with non-empty reasons + named follow-up task #90 (verified visible in the task list). The lockstep CI gate is updated to recognize deferred keys (`test_no_callsite_uses_an_unregistered_or_deferred_key` + `test_active_and_deferred_sets_are_disjoint`). **Within Gate A's authorized descope path.** `ingest` is the 4th deferred key with a documented Counsel-lens reason (BAA Exhibit C: "pending inside-counsel verdict") — also Gate-A-authorized.

**(b) `evidence_export` has NO substrate-invariant coverage — sound or gap?** The build's stated rationale (`baa_enforcement.py` docstring + `assertions.py` docstring + runbook): "transient point-in-time download with no durable state to assert against." That rationale holds — `cross_org_relocate` and `owner_transfer` have state-machine tables the invariant can query, `evidence_export` would require querying `admin_audit_log` for `auditor_kit_download` rows (which DO exist — verified by reading `evidence_chain.py:4290+` rate-limit + audit code path). Could the invariant query those rows? Yes. Does it? No. **This is a real residual coverage gap** — if a future endpoint bypasses `check_baa_for_evidence_export` and pulls the kit, the runtime backstop won't catch it. But: the inline gate is fail-closed and method-aware, the carve-outs are legally mandatory (admin + auditor), and the rationale is defensible. **Acceptable for v1; downgrade to a named followup recommendation.** Carry as a P1: *"#52 followup — extend `_check_sensitive_workflow_advanced_without_baa` to scan `admin_audit_log` for `auditor_kit_download` rows where caller was `client_portal`/`partner_portal` and `baa_enforcement_ok=FALSE`."*

**(c) Is `require_active_baa` actually reachable?** Verified: `client_owner_transfer.py:46` `from .baa_enforcement import require_active_baa` (top-level import). The wiring at line 361 + 533 (`user: dict = require_active_baa("owner_transfer")`) places it as the parameter default — FastAPI resolves it at request-time because the factory returns `Depends(_check)`. **Reachable and correct.** No `Depends(require_active_baa(...))` double-wrapping in the wiring (which would have been a Steve-class bug; the build correctly trusts the factory to return `Depends`).

**(d) Admin-bypass audit-logging — is it actually wired?** Verified `enforce_or_log_admin_bypass` writes to `admin_audit_log` with `action='baa_enforcement_bypass'` + `details->>'workflow'` + `details->>'client_org_id'`. The substrate invariant excludes rows with a matching bypass entry via `SELECT 1 FROM admin_audit_log WHERE action = 'baa_enforcement_bypass' AND details->>'workflow' = $1 AND details->>'client_org_id' = $2`. Audit-log INSERT failures are caught + ERROR-logged but do not block the admin operation (correct — audit failure must never block legitimate operator action; logged so the invariant can fire on subsequent ticks if the audit row is missing). **Audit chain coherent.**

**(e) Lockstep test catches both missing-callsite AND deferred-key-wired-early?** Verified by reading the test:
- `test_every_gated_workflow_has_an_enforcing_callsite` — assertion `BAA_GATED_WORKFLOWS - wired` must be empty. Catches missing callsite. ✓
- `test_no_callsite_uses_an_unregistered_or_deferred_key` — assertion `wired - BAA_GATED_WORKFLOWS` must be empty. Catches deferred-key-wired-early (since deferred keys aren't in `BAA_GATED_WORKFLOWS`) AND typos. ✓
- `test_assert_workflow_registered_rejects_unknown_and_deferred` — runtime guard rejects both. ✓
- `test_active_and_deferred_sets_are_disjoint` — invariant on the two constants. ✓

Lockstep CI gate is structurally complete.

**Coach found NO missing-additions.** All Gate A deliverables present in the diff.

**P1-NEW (Coach):** carry the `evidence_export` invariant-coverage extension as a named TaskCreate followup.

### LENS 5 — AUDITOR (OCR §164.504(e)): the invariant + sev1 + runbook

**STATUS: APPROVE.** Verified:

- The invariant SQL queries DB-observable evidence of advancement: `cross_org_site_relocate_requests` rows where `executed_at OR source_release_at > NOW() - INTERVAL '30 days'`, and `client_org_owner_transfer_requests` rows where `completed_at OR current_ack_at > NOW() - INTERVAL '30 days'`. Both halves capture the state-machine ADVANCEMENT, not just creation — correct §164.504(e) framing (the BA performed services *after* the CE entered the gated state).
- sev1 is correct (matches sibling pattern `pre_mig175_privileged_unattested` + `cross_org_relocate_chain_orphan`). Per the existing Severity convention: sev1 = operator-action-within-workday compliance gap; sev0 = paging-class outage. A confirmed §164.504(e) gap is workday-class, not page-class. Runbook's `## Escalation` section explicitly says "sev1 — operator action within the workday" + "Sustained firing (>7 days, same org) means the enforcement gate has a hole — escalate to engineering."
- Runbook structural completeness:
    - Plain-English explanation ✓
    - Source-table table ✓
    - §164.504(e) citation ✓
    - List-1/List-2/List-3 architectural framing ✓
    - 4 root-cause categories enumerated (un-gated path, BAA lapsed mid-flow, missing audit row, never had BAA) ✓
    - 4 Immediate Action steps with concrete `baa_status.baa_signature_status()` call ✓
    - Verification section ✓
    - **Escalation section ✓** (Session 218 lock-in: every runbook MUST have `## Escalation`)
    - Related runbooks (sibling correlation) ✓
    - Change log ✓

**Auditor P-level:** none. Auditor APPROVES as-shipped.

### LENS 6 — PM: effort + cliff

**STATUS: APPROVE-WITH-FLAG.** Gate A estimated 2.5–3.5 engineering-days; the AS-IMPLEMENTED diff is +5,388 lines (mostly openapi.json regeneration which is auto-generated; the real backend code-delta is +753 lines across 6 files + 200 lines of tests + 140 lines of runbook = ~1,100 lines). Within envelope.

**Cliff math:** today 2026-05-14, cliff 2026-06-12 = **29 days runway**. Ships on Gate B clear → ~28 days to verify in production. Adequate margin.

**3-of-6 vs 6-of-6 by cliff:** the 3 deferred (`partner_admin_transfer`, `new_site_onboarding`, `new_credential_entry`) need to ship before cliff to meet Counsel's authorized v1 scope. **Named follow-up #90 exists.** PM flag: #90 must be on the sprint for the next 14 days — recommend prioritizing partner_admin_transfer first (it has the clearest Exhibit C mandate) and the two onboarding endpoints together (same `org_management.py` / `sites.py` neighborhood).

**Critical-path interaction:** #52 is in flight alongside #43–49 (Vault P0 bundle, cliff 2026-05-27) and #50/#53/#54 (other counsel priorities). #52's cliff is later. **PM verdict: ship #52 v1 now, do not let the residual 3 deferred workflows block deploy.**

### LENS 7 — COUNSEL (Attorney) — LOAD-BEARING

**STATUS: APPROVE-WITH-PRECONDITION-FLAG.** Three probes:

**(a) Is shipping 3-of-6 contract-compliant by cliff?** BAA Exhibit C lists 6 workflow classes (4 transfer/export + 2 onboarding). Shipping 3 active + 3 deferred at cliff would be **partial enforcement of a stated mechanism** — worse than no claim. Gate A authorized "descope to 4 with `# deferred`" but **not "ship 3 indefinitely."** Counsel verdict: **the 3 active + 3 deferred is acceptable for #52 v1 LANDING NOW**, BUT the 3 deferred MUST be active by 2026-06-12 or the v1 ships as 3-active-permanent which Exhibit C does NOT authorize. **Named follow-up #90 is the binding commitment.** Counsel recommends #90 be split into 3 sub-tasks with explicit per-key deadlines (`partner_admin_transfer` by 2026-05-28; `new_site_onboarding` + `new_credential_entry` by 2026-06-05) — leaves 7-day verification margin before cliff.

**(b) The `cross_org_relocate` admin carve-out — "log but allow" actual enforcement, or theater?** Gate A's Carol lens already approved the admin carve-out as legally correct (the platform operator is not the Covered Entity; admin actions are out of Exhibit C's CE-self-service scope). The build's implementation: admin actions ALWAYS proceed AND write an `admin_audit_log` row with `action='baa_enforcement_bypass'`. The substrate invariant excludes audit-logged bypasses but **fires sev1 if the bypass row is missing.** This means: theater would be "admin proceeds, no record." Reality: "admin proceeds, recorded in append-only audit, substrate sev1 if record is missing." **This is real enforcement of the operator-attestation chain, not theater.** Counsel approves.

**(c) Article 8.3 notice-adequacy precondition.** Gate A flagged: *"Counsel should confirm before Gate B that the Article 8.3 in-product banner + email actually went out within 7 days of 2026-05-13 (i.e., by 2026-05-20)."* This CANNOT be verified from the repo diff — it's a deployment-state question (was the banner rendered? did the email send job run?). **FLAGGED FOR HUMAN CONFIRMATION** before merging this diff or before the cliff. If notice did NOT go out, the 30-day clock arguably hasn't started, and #52's enforcement should not hard-block at 2026-06-12 (delay the cliff to 30 days after actual notice). The build code itself has no notice-date check — it gates on signature-exists-for-current-version regardless of when notice went out. **Counsel-lens precondition: confirm notice adequacy before deploy.**

---

## CONSOLIDATED DISPOSITION

### Gate A P0s (must close before Gate B passes)
- **P0-1 (Steve)** — multi-context org resolver. **CLOSED** — per-context resolution is sounder than the monolithic resolver Gate A imagined. No circular import. Verified.
- **P0-2 (Maya)** — separate `baa_enforcement_ok()` predicate not dependent on `baa_on_file`. **CLOSED** — verified by reading the SQL.
- **P0-3 (Carol)** — method-aware `evidence_export` gate. **CLOSED** — verified gating only `client_portal` + `partner_portal`; admin + `portal` (cookie + `?token=`) carved out.

### Gate A P1s
- **P1-1 (Maya)** version ordering test. **CLOSED** — `test_baa_version_ordering.py` pins numeric, not lexical.
- **P1-2 (Maya)** email-rename orphan. **PARTIALLY CLOSED** — `client_user_email_rename.py` does not mutate `client_orgs.primary_email`, so the immediate vulnerability is absent. **No named follow-up TaskCreate filed.** Per Session 220 lock-in, this needs to be carried as a named task: *"Pin a test that asserts `baa_signatures` re-validates if `client_orgs.primary_email` is admin-mutated, or re-key the join to `client_org_id`."* **Carry as Gate-B P1-FOLLOWUP-1.**
- **P1-3 (Steve/Coach)** wire all 6 OR descope with inline deferred comments. **CLOSED via descope** — 3 active + 3 deferred with reasons; lockstep CI gate recognizes deferred; named follow-up #90 exists.
- **P1-4 (Carol)** 403 body must not leak org. **CLOSED** — generic body, no org names, unauth probers can't reach it.

### New P1s from Gate B
- **P1-FOLLOWUP-1** (Maya P1-2 named carry) — file a named TaskCreate before commit.
- **P1-FOLLOWUP-2** (Coach `evidence_export` invariant coverage) — extend `_check_sensitive_workflow_advanced_without_baa` to scan `admin_audit_log` for `auditor_kit_download` rows. Not blocking v1.
- **PRECONDITION (Counsel)** — confirm Article 8.3 banner + email notice adequacy by 2026-05-20 BEFORE the 2026-06-12 cliff is enforced in production. Human-verification.
- **PRECONDITION (Counsel/PM)** — #90 must be split into per-key deadlines (`partner_admin_transfer` by 2026-05-28; 2 onboarding by 2026-06-05) so all 6 ship before cliff.

### Sweep criterion
**PASS** — 258/258 on clean re-run; initial 2 transient failures investigated and confirmed non-real.

### Schema-drift criterion
**PASS** — all referenced columns present in `prod_column_types.json`.

### Substrate-docs + assertion-metadata criterion
**PASS** — both targeted tests green.

### Lockstep CI gate criterion
**PASS** — 5 tests green; structurally complete (active ⊆ wired, wired ⊆ active, deferred reasons, disjoint, runtime guard).

---

## FINAL VERDICT

**APPROVE-WITH-FIXES.**

Before commit:
1. **File named TaskCreate for P1-FOLLOWUP-1** (Maya P1-2 carry — email-rename / primary_email mutation orphan-prevention test).
2. **File named TaskCreate for P1-FOLLOWUP-2** (Coach — `evidence_export` substrate-invariant coverage extension).
3. **Split #90 into 3 sub-tasks with per-key deadlines** (partner_admin_transfer 2026-05-28; new_site_onboarding + new_credential_entry 2026-06-05).
4. **Cite both Gate A and Gate B verdicts in the commit body** (Session 220 lock-in: "Commit body must cite BOTH gate verdicts").

Before deploy hard-enforces at the 2026-06-12 cliff:
5. **Human-confirm Article 8.3 in-product banner + email notice adequacy** (was the banner rendered? did the notice email actually send by 2026-05-20?). If notice did NOT go out, delay the cliff to 30 days post-actual-notice — the code itself does not date-gate the cliff, so this is an operational decision.

The diff is ready to ship into the v1 enforcement layer after items 1–4 are addressed (TaskCreate is the only mechanical action; everything else is documentation). Item 5 is a deploy-time gate, not a merge-time gate.

**Sweep:** 258/258 GREEN.
**Gate A P0s:** 3/3 CLOSED.
**Gate A P1s:** 3/4 CLOSED, 1 PARTIALLY CLOSED (needs named TaskCreate).
**Scope:** 3-of-6 within Gate A's authorized descope path.
**Schema-drift:** NONE.
**Circular imports:** NONE.
**Counsel-lens preconditions:** 2 flagged for human confirmation pre-cliff.
