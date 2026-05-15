# Gate A — Task #52: BAA-Expiry Machine-Enforcement Gate

**Deliverable:** `BAA_GATED_WORKFLOWS` lockstep constant + CI gate + substrate invariant + enforcement wiring.
**Counsel Priority #1 (Rule 6).** Reviewer: Class-B 7-lens fork. Date: 2026-05-14.
**Verdict: APPROVE-WITH-FIXES** (3 P0, 4 P1 — all closeable before build; none are redesigns).

---

## 350-WORD SUMMARY

Task #52 builds the machine-enforcement layer the v1.0-INTERIM master BAA Exhibit C explicitly names. Good news: the substrate is already 70% in place. `baa_signatures` exists (append-only, mig 224), `is_acknowledgment_only` distinguishes click-through from formal BAA (mig 312), `baa_status.is_baa_on_file_verified()` is the canonical reader, and `client_orgs` already carries `baa_effective_date` / `baa_expiration_date`. **No new schema is strictly required for v1** — the enforcement primitive can be built entirely on existing columns. A migration number is claimed defensively (321) only for an optional `v_baa_enforcement_state` convenience view; if the view is dropped from scope, release it.

The enforcement primitive is a FastAPI dependency `require_active_baa(workflow: str)` — a factory mirroring `require_partner_role`. It resolves the caller's `client_org_id`, calls a new `baa_status.baa_enforcement_ok()`, and raises 403 `BAA_NOT_ON_FILE` when fail-closed. It wires onto 6 confirmed mutation endpoints across `cross_org_site_relocate.py`, `client_owner_transfer.py`, `partner_admin_transfer.py`, and `evidence_chain.py` (auditor-kit). **Ingest-blocking is DEFERRED** per the Counsel lens — BAA Exhibit C itself says ingest is "pending inside-counsel verdict," and `/api/appliances/checkin` blocking risks orphaning a paying customer's appliance fleet mid-stream. Ship the 4 confirmed workflow classes; carry ingest as a named follow-up gated on Task #37's counsel queue.

The lockstep is a **3-list** structure: the `BAA_GATED_WORKFLOWS` constant, a CI gate (`test_baa_gated_workflows_lockstep.py`) that asserts every enumerated workflow's endpoint actually calls the dependency, and a sev1 substrate invariant (`sensitive_workflow_advanced_without_baa`) that detects runtime bypass. Fail-closed carve-outs: the entire BAA-signing flow, all GET/read endpoints, admin-context callers, and the auditor-kit's legacy `?token=` auditor path (an external auditor has no org session — blocking them would be a §164.524 access-right violation).

**Cliff: 2026-06-12** (30 days from 2026-05-13 effective date). Effort ≈ 2.5–3.5 days. Runway is adequate but **#52 is on the critical path** — it must clear its own Gate B and deploy by ~2026-06-10 to leave a 48h verification margin.

---

## LENS 1 — STEVE (Principal SWE): enforcement mechanism

**The primitive.** A dependency factory in a new module `baa_enforcement.py`, mirroring `partners.py::require_partner_role`:

```python
def require_active_baa(workflow: str):
    """Dependency factory. workflow MUST be a member of BAA_GATED_WORKFLOWS.
    Resolves the caller's client_org_id from whichever auth context is
    present, then fail-closed checks baa_status.baa_enforcement_ok()."""
    if workflow not in BAA_GATED_WORKFLOWS:
        raise RuntimeError(f"unregistered BAA-gated workflow: {workflow}")
    async def _check(request: Request, ...):
        org_id = _resolve_caller_org_id(request, ...)   # see P0-1
        async with admin_transaction(pool) as conn:
            ok = await baa_status.baa_enforcement_ok(conn, org_id)
        if not ok:
            raise HTTPException(403, detail={
                "error": "BAA_NOT_ON_FILE",
                "message": "This action requires a current signed Business "
                           "Associate Agreement. Existing data access is "
                           "unaffected. Sign at /portal/baa to continue.",
                "workflow": workflow,
            })
        return org_id
    return Depends(_check)
```

**Precedent patterns confirmed in-repo:**
- `partners.py:350 require_partner_role(*allowed_roles)` — factory-returns-`Depends` pattern. Direct template.
- `auth.py:748 require_auth` — base auth dependency shape.
- Privileged-chain gating (`fleet_cli.PRIVILEGED_ORDER_TYPES` + mig 175 trigger + 4-list lockstep test) — the lockstep-constant + CI-gate + invariant triad is the exact architecture #52 should clone.
- `baa_status.py` already exists as the canonical reader module — **#52 extends it, does not fork it.**

**Where BAA-signature state lives:** `baa_signatures` table (mig 224, append-only via `trg_baa_no_update`). Columns confirmed from `prod_columns.json`: `signature_id, email, signer_name, signer_ip, signer_user_agent, baa_version, baa_text_sha256, signed_at, stripe_customer_id, is_acknowledgment_only, metadata`. `client_orgs` carries `baa_on_file`, `baa_effective_date`, `baa_expiration_date`, `baa_uploaded_at` plus the relocate-receipt columns from mig 283.

**Endpoints to gate** (confirmed signatures):
| File | Endpoint | Current dep | workflow key |
|---|---|---|---|
| `cross_org_site_relocate.py:581` | `POST .../relocate` (propose) | `require_admin` | `cross_org_relocate` |
| `client_owner_transfer.py:360` | `POST .../owner-transfer/initiate` | `require_client_owner` | `owner_transfer` |
| `client_owner_transfer.py:532` | (2nd owner-transfer mutation) | `require_client_owner` | `owner_transfer` |
| `partner_admin_transfer.py:225` | `POST /initiate` | (verify) | `partner_admin_transfer` |
| `partner_admin_transfer.py:414` | `POST /{id}/accept` | (verify) | `partner_admin_transfer` |
| `evidence_chain.py:4245` | `GET /sites/{id}/auditor-kit` | `require_evidence_view_access` | `evidence_export` |

`new_site_onboarding` + `new_credential_entry` from Exhibit C are also in-scope — Steve flags as **P1-3** (endpoints not yet located in this review; the build must grep `org_management.py` / `sites.py` for the site-create + credential-add mutations and add them, or the lockstep constant lists workflows with no enforcing endpoint).

**P0-1 (Steve):** `_resolve_caller_org_id` is the riskiest piece. The 6 endpoints span **four distinct auth contexts** — `require_admin` (no org), `require_client_owner` (client session → org), partner session (partner → many orgs, org comes from request body/path), and `require_evidence_view_access` (5 branches incl. tokenless auditor). The dependency MUST resolve org consistently or it silently no-ops. This is the make-or-break and needs its own mini-design before build.

---

## LENS 2 — MAYA (Database): the BAA-state query

**"Has an active, non-expired BAA":** three-predicate AND, built on existing columns — **no schema change required for v1**:

```sql
-- baa_status.baa_enforcement_ok(conn, client_org_id) -> bool
SELECT (
    EXISTS (
        SELECT 1 FROM baa_signatures bs
         WHERE LOWER(bs.email) = LOWER(co.primary_email)
           AND bs.is_acknowledgment_only = FALSE          -- formal BAA, not click-through
           AND bs.baa_version >= $2                       -- current required version
    )
    AND (co.baa_expiration_date IS NULL
         OR co.baa_expiration_date > CURRENT_DATE)        -- not expired
) AS enforcement_ok
  FROM client_orgs co
 WHERE co.id = $1::uuid;
```

**On "expired":** two independent expiry concepts, and #52 must honor BOTH:
1. **Version-staleness** — the v1.0-INTERIM BAA has "Decay-after: 14 days" and v2.0 supersedes (target 2026-06-03). "Expired" primarily means *"no signature for the CURRENT required `baa_version`."* This is the **effective-version concept** — there must be a single source of truth for "what version is currently required." Recommend a module constant `CURRENT_REQUIRED_BAA_VERSION` in `baa_enforcement.py` (string-comparable; `v1.0` → `v2.0` ordering works lexically here but **P1-1: pin a test that asserts version ordering is monotonic** — `v10.0` would sort below `v2.0`).
2. **Date-expiry** — `client_orgs.baa_expiration_date` already exists and `prometheus_metrics.py:720` already queries it for the 30-day-warning gauge. Honor it as a hard block when set.

**Important — do NOT confuse with `is_baa_on_file_verified()`:** the existing helper ANDs in `co.baa_on_file = TRUE` (the admin-flipped operational flag). Per the helper's own docstring, *"we have not flipped anything"* in demo posture — so `is_baa_on_file_verified()` returns FALSE for every org today. If `require_active_baa` reused it verbatim, **every customer would be blocked from every gated workflow the instant #52 deploys.** `baa_enforcement_ok()` MUST be a *separate* predicate that does NOT require `baa_on_file` — it checks formal-signature-exists + not-expired only. **This is P0-2.**

**Migration number:** claiming **321** defensively for an optional `v_baa_enforcement_state` view (per-org materialized convenience for the substrate invariant + an operator panel). **If the invariant query inlines the predicate (preferred — see Auditor lens), release 321** with `Mig-released: 321`. Ledger row + `<!-- mig-claim:321 task:#52 -->` marker go in the design-doc commit per the RESERVED_MIGRATIONS protocol.

**P1-2 (Maya):** the `LOWER(bs.email) = LOWER(co.primary_email)` join is the same fragile email-keyed join the existing helpers use. If an org's `primary_email` is ever changed (there's a `client_user_email_rename.py` flow), the BAA signature orphans and the org is silently blocked. Recommend the build verify whether `baa_signatures` can be keyed to `client_org_id` directly, or at minimum pin a test that the rename flow re-points or re-validates.

---

## LENS 3 — CAROL (Security): fail-closed + carve-outs

**Fail-closed is correct** — no BAA → block. But the carve-out list is load-bearing; this is the restore-endpoint-auth-deadlock lesson (memory: `feedback_restore_endpoint_auth_deadlock.md`). If `require_active_baa` is applied too broadly, the org can't reach the page that fixes the problem.

**Fail-closed carve-out list (MUST NOT be gated):**
1. **The entire BAA-signing flow** — `SignupBaa.tsx` backend (`client_signup.py` BAA POST), the `/portal/baa` route, and any GET that renders the BAA text. Gating these is the deadlock.
2. **All read/GET endpoints** — BAA Exhibit C + Article 8.3 are explicit: *"Existing-data access remains unaffected."* The gate blocks **WRITES / advancement only.** `client_portal.py` dashboard reads, site-list reads, posture displays — untouched.
3. **Admin-context callers** — when the caller is an OsirisCare admin (`require_admin`), the org-resolution may yield no org or a third-party org; admin operations are out of the CE-self-service scope Exhibit C addresses. Admin-initiated cross-org relocate is the platform operator acting, not the CE — carve out, but **log it** (admin bypass must be auditable; see Auditor lens).
4. **The auditor-kit tokenless `?token=` branch** — `require_evidence_view_access` has 5 branches incl. a legacy `?token=` query param used by external auditors who have **no org session at all**. An external auditor verifying a CE's evidence has no `client_org_id` to resolve and is not the party that needs to sign the BAA. Blocking them would itself be a §164.524 individual-access-right violation. **The `evidence_export` gate applies only to the client-portal + partner-portal branches, NOT the admin or tokenless-auditor branches.** This is **P0-3** — the naive wiring (`Depends(require_active_baa("evidence_export"))` stacked on the endpoint) would catch the auditor. The gate must run *inside* the endpoint after `_auth.method` is known, or be a method-aware variant.

**Carol's residual concern (P1-4):** the 403 response body. Per Counsel Rule 7 (no unauthenticated context) the error message must not leak org names or specifics — the shape above is generic and points to the sign page. Confirm the message copy goes through `constants/copy.ts` if it surfaces in the frontend, and that the 403 doesn't differ in a way that lets an unauthenticated prober distinguish "org exists but no BAA" from "org doesn't exist."

---

## LENS 4 — COACH (Consistency): the lockstep enumeration

The `BAA_GATED_WORKFLOWS` lockstep is a **3-list structure** (privileged-chain is 4-list because it spans Go; #52 is backend-only, so 3):

**List 1 — `BAA_GATED_WORKFLOWS` constant** (`baa_enforcement.py`): the canonical set of workflow keys.
```
{cross_org_relocate, owner_transfer, partner_admin_transfer,
 evidence_export, new_site_onboarding, new_credential_entry}
```
(`ingest` intentionally ABSENT — see Counsel lens. Document the absence inline, same as `dangerousOrderTypes` allowlist comments.)

**List 2 — the enforcing endpoints**: every workflow key MUST have ≥1 endpoint that calls `require_active_baa(<key>)`.

**List 3 — the substrate invariant's detection set**: the runtime invariant must check the same workflow universe (or its DB-observable proxy — completed relocate rows, owner-transfer state-machine rows, etc.).

**CI gate — `tests/test_baa_gated_workflows_lockstep.py`** (clone `test_privileged_order_four_list_lockstep.py`):
- Every member of `BAA_GATED_WORKFLOWS` appears as a `require_active_baa("<key>")` literal in at least one backend `.py` (AST or regex walk).
- Every `require_active_baa("<key>")` callsite passes a key that IS in the constant (the factory already `RuntimeError`s on this at import — the test makes it a static gate too).
- No gated workflow's endpoint is *also* in a hardcoded carve-out list without a justification comment (mirrors the `ALLOWED_GAPS` pattern).

**Coach P0/P1 calls:** Coach concurs with Steve's **P1-3** — if `new_site_onboarding` / `new_credential_entry` are in List 1 but the build can't find/wire their endpoints, the lockstep test fails (key with no enforcing callsite) — which is the test working correctly, but it means those two endpoints are **mandatory build scope, not optional.** Either wire all 6 or descope the constant to 4 with an inline `# deferred: <key> — task #NN` comment that the test recognizes.

**Coach reminder (process):** per the Session-220 lock-in, **Gate B for #52 MUST run the full pre-push test sweep**, not just review the diff — the lockstep test + invariant registration + carve-out completeness are exactly the "what's MISSING" class that diff-scoped review misses.

---

## LENS 5 — AUDITOR (OCR / §164.504(e)): the substrate invariant

§164.504(e) requires the BAA to be in place *before* the BA performs services involving PHI. A sensitive workflow advancing for a non-signed CE is a substantive compliance gap — **sev1** is correct.

**Invariant: `sensitive_workflow_advanced_without_baa` (sev1)** — register in `assertions.py` alongside the existing ~70 invariants (`name=` entries at line 1846+). Mirror the closest existing pattern: **`l2_resolution_without_decision_record`** (assertions.py:2152) and **`pre_mig175_privileged_unattested`** (`_check_pre_mig175_privileged_unattested`, line 645) — both are "an action of class X exists in the DB with no corresponding authorization row of class Y." Exact same shape here:

```
-- For each org with a workflow that ADVANCED (completed cross_org_relocate row,
-- owner_transfer in a post-initiate state, partner_admin_transfer accepted,
-- auditor_kit_download audit row) in the last 30d, assert baa_enforcement_ok()
-- was TRUE. Any FALSE → one Violation row per (org, workflow), sev1.
```

Per-row granularity (one Violation per org+workflow) matches the privileged-unattested invariant. The check should query the DB-observable evidence of advancement (audit-log rows, state-machine tables) — it does NOT need the view from mig 321, which is why Maya can likely **release 321**.

**Auditor note:** the invariant is the *backstop* — it catches a bypass (someone adds a new sensitive endpoint and forgets the dependency, or the carve-out is too wide). The CI gate catches it at build time; the invariant catches it at runtime. Both are required — Counsel Rule 6 says "machine-enforced where possible," and defense-in-depth is the standard the privileged chain already set.

**Auditor caveat:** admin-bypass (Carol carve-out #3) MUST write an `admin_audit_log` row — otherwise the invariant can't distinguish "legitimate admin-initiated relocate of a non-signed org" from "bypass." If admin bypass is audited, the invariant excludes rows with a matching admin-audit entry; if not, it false-positives. **Make admin-bypass audit-logging a hard build requirement.**

---

## LENS 6 — PM: effort + the 2026-06-12 cliff

**Cliff math:** v1.0-INTERIM effective 2026-05-13 + 30 days = **2026-06-12**. Article 8.3 + Exhibit C both reference it. **#52 IS on the critical path** — until it ships, Exhibit C says the transition is "managed operationally via in-product banner + email." That's tolerable for a few weeks but the BAA *names* the mechanism as the enforcement vehicle; shipping after the cliff means the platform is in stated-but-unenforced posture, which is a worse audit position than not having claimed it.

**Effort estimate: 2.5–3.5 engineering-days:**
- `baa_enforcement.py` module + `require_active_baa` factory + `_resolve_caller_org_id` (the P0-1 multi-context resolver): ~1 day
- `baa_status.baa_enforcement_ok()` + version-constant + tests: ~0.5 day
- Wire 6 endpoints (incl. the P0-3 method-aware auditor-kit handling + locating the 2 onboarding/credential endpoints): ~1 day
- CI lockstep gate + substrate invariant + registration: ~0.5–1 day

**Runway:** today is 2026-05-14. ~29 days to cliff. With the TWO-GATE protocol (this Gate A → build → Gate B → deploy → runtime verification) and a full-sweep Gate B, realistic ship is ~2026-05-22 to 2026-05-28 if started now. **That leaves a comfortable 2-week margin** — but #52 is competing with the Vault P0 bundle (#43–49, cliff 2026-05-27) and Task #50/#53/#54 counsel-priorities. **PM recommendation: #52 starts within 3 working days** to preserve margin. It is NOT blocked on anything — all dependencies (mig 224, 312, baa_status.py) are shipped.

**PM scope flag:** ingest-blocking deferral (Counsel lens) keeps v1 effort bounded. If ingest were in v1, add ~1.5 days + a much harder Gate A (appliance-fleet-orphan risk). Defer.

---

## LENS 7 — COUNSEL (Attorney) — LOAD-BEARING: ingest-scoping decision

**DECISION: DEFER ingest-blocking. Ship the 4 confirmed workflow classes in v1.**

The 4 confirmed: **cross-org transfer, owner-transfer, partner-swap, new evidence export.** Plus the 2 onboarding classes (new site, new credential) which Exhibit C lists as confirmed-blocked. Ingest is explicitly NOT confirmed.

**Why defer ingest:**
1. **BAA Exhibit C says so verbatim:** *"Final determination pending inside-counsel verdict on the BAA-enforcement engagement scope."* Shipping ingest-blocking ahead of the verdict means the platform's enforcement is *broader* than the contract authorizes — and a CE could argue the BA blocked a service the BAA didn't condition on re-signature.
2. **Operational catastrophe risk.** `/api/appliances/checkin` is the appliance↔Central-Command heartbeat. Blocking ingest for a non-re-signed CE doesn't just stop "new data" — it can orphan a deployed appliance fleet, break liveness detection, and stop drift detection on a *paying customer who is still within their 30-day re-sign window*. The blast radius is wildly disproportionate to the 4 workflow blocks, which are all discrete, user-initiated, deferrable actions.
3. **The counsel queue already exists** — Task #37 ("Counsel-queue bundle"). Ingest-scoping is precisely the question to route there. Carry it as a **named follow-up TaskCreate** gated on #37's verdict, cited in #52's commit body (TWO-GATE protocol requires P1s carried as named tasks).

**Does blocking the 4 workflows create contractual risk of its own?** Low, and manageable:
- Article 8.3 + Exhibit C are the *contractual basis* for the blocks — the CE signed (or was notified of) a document that says these blocks happen at Day 30. Enforcing exactly the enumerated list is honoring the contract, not breaching it.
- **Risk if the gate is too broad:** if `require_active_baa` accidentally blocks a *read* (Exhibit C: "Existing-data access remains unaffected") or blocks the *signing flow itself*, that IS a contractual problem — the CE can't cure. This is why Carol's carve-out list (LENS 3) is P0, not P1.
- **The §164.524 auditor path** (Carol P0-3) is the sharpest legal edge: blocking an external auditor's evidence pull because the *CE* hasn't re-signed would interfere with the CE's own individual-access-right compliance — the BA actively impeding the CE's HIPAA obligation. The tokenless/auditor branch carve-out is **legally mandatory, not just convenient.**
- **Notice adequacy:** Counsel should confirm before Gate B that the Article 8.3 in-product banner + email actually went out within 7 days of 2026-05-13 (i.e., by 2026-05-20). If notice didn't go out, the Day-30 enforcement clock arguably hasn't started and the gate should not hard-block at 2026-06-12. **This is a Counsel-lens precondition for #52's Gate B**, not a build blocker — but flag it now.

**Counsel verdict on scope:** APPROVE the 6-workflow v1 (4 transfer/export + 2 onboarding). DEFER ingest to #37. The deferral is itself contract-compliant because Exhibit C anticipates it.

---

## CONSOLIDATED FINDINGS

### P0 — must close before build is "done" (block Gate B)
- **P0-1 (Steve):** `_resolve_caller_org_id` multi-auth-context resolver needs its own mini-design — 4 distinct auth contexts (admin / client / partner / evidence-view 5-branch). A wrong resolution silently no-ops the gate.
- **P0-2 (Maya):** `baa_enforcement_ok()` MUST be a **new predicate separate from `is_baa_on_file_verified()`** — it must NOT require `client_orgs.baa_on_file = TRUE`, or every org is blocked the instant #52 deploys (demo posture has `baa_on_file=FALSE` everywhere).
- **P0-3 (Carol):** auditor-kit gate must be **method-aware** — `evidence_export` blocking applies only to client-portal + partner-portal branches of `require_evidence_view_access`, NEVER the admin or tokenless-`?token=` auditor branches (§164.524 violation + Carol carve-out #4).

### P1 — close OR carry as named TaskCreate in the same commit
- **P1-1 (Maya):** pin a test asserting `baa_version` ordering is monotonic / version-comparison is correct (lexical `v2.0 > v10.0` bug class).
- **P1-2 (Maya):** the `LOWER(email)` join to `baa_signatures` orphans on `client_user_email_rename` — verify the rename flow re-validates, or pin a test.
- **P1-3 (Steve/Coach):** locate + wire the `new_site_onboarding` + `new_credential_entry` endpoints, OR descope `BAA_GATED_WORKFLOWS` to 4 with inline `# deferred` comments the lockstep test recognizes. Not optional — the lockstep test fails on a key with no callsite.
- **P1-4 (Carol):** 403 body must not leak org existence/names (Rule 7); route copy through `constants/copy.ts` if frontend-surfaced.

### Named follow-ups (carry, not block)
- **Ingest-blocking** → gated on Task #37 counsel queue. New TaskCreate, cited in #52 commit body.
- **Mig 321** → claim defensively in the design-doc commit; **release if the substrate invariant inlines its query** (Auditor lens says it can).
- **Notice-adequacy check** (Counsel) → confirm Article 8.3 banner+email shipped by 2026-05-20; precondition for #52's Gate B, not for build.

### Build-scope summary
- New module `baa_enforcement.py`: `BAA_GATED_WORKFLOWS` constant, `require_active_baa()` factory, `_resolve_caller_org_id()`, `CURRENT_REQUIRED_BAA_VERSION`.
- Extend `baa_status.py`: add `baa_enforcement_ok(conn, org_id)` (distinct from `is_baa_on_file_verified`).
- Wire 6 endpoints (4 confirmed + 2 onboarding) across 4 files; auditor-kit gets method-aware handling.
- `tests/test_baa_gated_workflows_lockstep.py` — 3-list lockstep CI gate.
- `assertions.py` — `sensitive_workflow_advanced_without_baa` (sev1) invariant + registration.
- Admin-bypass MUST write `admin_audit_log` rows (Auditor requirement).
- Mig 321 claimed defensively; likely released.

---

## FINAL VERDICT

**APPROVE-WITH-FIXES.** The design is sound, the substrate is 70% pre-built, and the scoping (6 workflows, ingest deferred) is contract-correct per the Counsel lens. The 3 P0s are all concrete wiring/predicate-correctness issues, not redesigns — they are closeable inside the 2.5–3.5 day estimate. **Gate A clears for build to start, with the 3 P0s as mandatory in-scope work and the 4 P1s carried.** #52 is on the 2026-06-12 critical path; recommend build starts within 3 working days. Gate B must run the full pre-push sweep per the Session-220 lock-in and cite the notice-adequacy confirmation.
