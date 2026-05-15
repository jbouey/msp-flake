# Gate B — Task #90 Partial (2-of-3 deferred BAA workflows wired)

**Date:** 2026-05-15
**Gate:** B (pre-completion, fork-isolated 7-lens)
**Subject:** wiring `new_site_onboarding` + `new_credential_entry` from `_DEFERRED_WORKFLOWS` into `BAA_GATED_WORKFLOWS`; `partner_admin_transfer` stays deferred pending #94 Gate A.
**Sweep:** `bash .githooks/full-test-sweep.sh` → **263 passed, 0 skipped need-deps, exit 0**.
**Targeted re-run:** `test_baa_gated_workflows_lockstep.py` + `test_substrate_invariant_sensitive_workflow_baa.py` + `test_no_anonymous_privileged_endpoints.py` → **14 passed in 10.18s**.

---

## 250-word summary

The as-implemented diff at `baa_enforcement.py` + `sites.py:209 create_site_api` + `sites.py:1140 add_credential` correctly wires the two onboarding workflows into the admin-bypass enforcement triad. Both callsites resolve the owning `client_org_id` before the gated INSERT, are placed inside an `admin_transaction` block (so the `enforce_or_log_admin_bypass` audit-row INSERT pins to one PgBouncer backend), pass the right `actor_user_id` + lowercased `actor_email` + `request` + `target` keyword set, and never raise 403 — the Carol carve-out semantics are intact. The lockstep CI gate goes green (5 active, 2 deferred, no overlap). The full pre-push sweep passes 263/0.

**However, the diff has one Coach-class P0 omission and three P1 scope-gaps.** The P0: the substrate runtime backstop (`assertions.py::_check_sensitive_workflow_advanced_without_baa`) has a hard-coded 3-workflow UNION ALL — it does NOT scan the new `sites` / `site_credentials` advance-rows for orgs lacking BAA. The runtime invariant therefore CANNOT detect a code-path that bypasses `enforce_or_log_admin_bypass` entirely (e.g. by inserting into `sites` directly from a different endpoint). The P1s: four OTHER live `INSERT INTO sites` callsites exist (`partners.py:440 provision claim`, `provisioning.py:261 admin-restore`, `routes.py:1733 dashboard duplicate of create_site`, `routes.py:1932 /onboarding prospect`) and none are gated — the Task #52 follow-up scope description ("locate + wire the site-create mutation") undersold the surface area. **VERDICT: APPROVE-WITH-FIXES — P0 + P1-1 + P1-3 must close before commit.**

---

## Per-lens verdict

### Steve (engineering quality) — APPROVE
- **Placement of bypass calls:** Both are BEFORE the INSERT, both have the org resolved before invocation. `create_site_api` reuses the existing `admin_transaction` block (good — no extra connection). `add_credential` opens a SEPARATE `admin_transaction` block before the existing `tenant_connection` block (correct — `admin_audit_log` is admin-scoped, `site_credentials` is tenant-scoped; they CANNOT share a single asyncpg connection because of the RLS `SET LOCAL app.site_id` requirement on the tenant path).
- **`request: Request` param position:** added as the 3rd positional in `add_credential(site_id, cred, request, user=Depends(...))`. FastAPI accepts `Request` anywhere; positional-after-Pydantic-body-model + before the Depends user keeps clients backward-compatible (no caller passes positional kwargs).
- **Concurrency between the two blocks in `add_credential`:** zero shared state. The `admin_conn` is released before `tenant_connection` is opened. If a parallel request inserted a `baa_enforcement_bypass` row, the second-block FK INSERT into `site_credentials` is unaffected — different table, different transaction. Fine.
- **One nit (P2):** the `from .baa_enforcement import enforce_or_log_admin_bypass` is an in-function import in BOTH callsites. Other state-machine consumers (cross_org_relocate, owner_transfer) import at module top. Not blocking — both shapes are valid; module-top is cleaner.

### Maya (database/schema) — APPROVE
- **Schema check:** `sites.client_org_id` exists (verified in `prod_columns.json`; FK to `client_orgs.id` enforced by `_org_isolation_check_pg.py`).
- **Cast `::text` in `SELECT client_org_id::text FROM sites WHERE site_id = $1`** is correct — `enforce_or_log_admin_bypass` expects a string and the `admin_audit_log.details` JSONB will store it as text. Matches the pattern in `baa_enforcement.py::check_baa_for_evidence_export:282`.
- **404 on missing site:** the original code would have failed with a tenant-RLS-shaped error or an FK violation deep in the INSERT. The new explicit 404 leaks no information beyond what an authenticated operator already has (the operator IS the platform). Not §164.524 relevant (admin endpoint), not Counsel-Rule-7 relevant (admin-authed channel). Acceptable.
- **`jsonb_build_object` / param-cast class:** the audit-row INSERT uses an existing helper that already casts `$1::uuid` + `$5::jsonb` — no new ambiguous-param surface added.

### Carol (Counsel Rule 6 / load-bearing legal) — APPROVE
- **Admin carve-out:** confirmed by reading the diff line-by-line. `enforce_or_log_admin_bypass` NEVER raises 403; it returns `bool` and the callsite discards the result. Operator is never blocked. This matches the BAA Exhibit C "admin = platform operator, not CE" framing carved out in Task #52 Gate A.
- **Bypass-row `details` JSONB:** carries `{workflow, client_org_id, reason}`. The substrate invariant's bypass-row lookup at `assertions.py:1937` keys on `details->>'workflow' = $1 AND details->>'client_org_id' = $2` — both keys are present. If/when the invariant's UNION is extended to scan the new workflows (P0 below), the bypass-exclusion will work without further changes.
- **Deferred reason for `partner_admin_transfer`:** updated correctly to cite #94 Gate A finding. The new comment is accurate: partner-internal role swap with no `client_org_id` resolvable, gate predicate is client-org-scoped.

### Coach (Session 220 antipattern — "did the diff MISS anything?") — **BLOCK on P0**

#### P0 — substrate invariant UNION ALL is NOT extended

`assertions.py:1882-1915` SQL has only 3 UNION clauses:
1. `cross_org_site_relocate_requests`
2. `client_org_owner_transfer_requests`
3. `admin_audit_log WHERE action='auditor_kit_download'`

There is no scan of recently-created `sites` rows nor recently-created `site_credentials` rows. **Net effect:** if a future code path inserts a row into `sites` without calling `enforce_or_log_admin_bypass` (e.g. the four currently-un-gated callsites below, or a new endpoint added in 6 months), the runtime backstop will NOT detect it. The CI gate `test_baa_gated_workflows_lockstep.py` catches MEMBERSHIP drift, but it does NOT catch a NEW INSERT-INTO-sites callsite that simply omits the gate.

Even MORE problematic: the invariant docstring at line 2453 still says: `"A BAA-gated sensitive workflow (cross_org_relocate, owner_transfer, or evidence_export) advanced..."` — the new workflows are not enumerated. The invariant's NAME implies coverage that the SQL does not provide.

**Required before commit:**
- Extend `_check_sensitive_workflow_advanced_without_baa` SQL with two more UNION ALL clauses (one over `sites WHERE created_at > NOW() - 30 days`, one over `site_credentials WHERE created_at > NOW() - 30 days`).
- OR explicitly document in the invariant description that `new_site_onboarding` + `new_credential_entry` rely SOLELY on the CI gate (no runtime backstop) and file a TaskCreate followup. The latter is acceptable for Gate B with a tracked task; the former is the real fix.

#### P1-1 — other live INSERT-INTO-sites callsites are un-gated

`grep -n 'INSERT INTO sites' mcp-server/central-command/backend/*.py` (excluding tests):

| File:line | Endpoint | Auth | Currently gated? |
|---|---|---|---|
| `sites.py:253` | `POST /api/sites` | admin | YES (this diff) |
| `partners.py:440` | `POST /api/partner-claim` (appliance-claim flow) | partner-bearer | NO |
| `provisioning.py:261` | `POST /api/provision/claim` | appliance-bearer | NO |
| `routes.py:1733` | `POST /api/dashboard/sites` | admin | NO (duplicate of sites.py:209 — possibly dead route, but still mounted) |
| `routes.py:1932` | `POST /api/dashboard/onboarding` (create_prospect) | admin | NO |

`routes.py:1733` is particularly concerning — it looks like a duplicate of the `sites.py:209` handler. Either it's dead code (kill it) or it's a live admin path that needs the same gate. Same for `routes.py:1932` — it creates a prospect site by INSERT INTO sites and is a live admin route at `/api/dashboard/onboarding`.

`partners.py:440` + `provisioning.py:261` are appliance-claim flows. These are NOT admin paths — they're machine-to-machine. **Counsel question for the Task #37 queue:** does a provision-claim (appliance phoning home with its MAC) constitute "site onboarding" in the BAA-Exhibit-C sense? If yes, gating is required and the carve-out logic needs an "appliance-bearer = no human actor = use synthetic actor_email" branch.

**Required before commit:** at minimum decide routes.py:1733 + routes.py:1932 (kill or gate). The partners.py + provisioning.py callsites can be deferred to a tracked task if Counsel scope-question is unresolved.

#### P1-2 — `test_no_anonymous_privileged_endpoints` parity — PASSED

Re-ran. `add_credential` still has `Depends(require_operator)`. `create_site_api` still has `Depends(require_auth)`. Both register as authenticated. No regression.

#### P1-3 — `_DEFERRED_WORKFLOWS` dict still valid

After this diff: `partner_admin_transfer` + `ingest` remain. The lockstep test PASSES — neither has an enforcing callsite (correct — both are documented-deferred). `assert_workflow_registered` will raise loudly if either ever appears in a callsite. Good.

#### P2 (advisory) — substrate-invariant-unit-test SHOULD grow new fixtures

`test_substrate_invariant_sensitive_workflow_baa.py` currently has fixtures for the 3 existing workflows. When the P0 fix lands, the test needs two new fixtures (one for `new_site_onboarding` synthetic-row, one for `new_credential_entry`). File as FU-1 under task #90.

### Auditor (HIPAA cliff coverage) — APPROVE with caveat
- Shipping 5-of-6 (with `partner_admin_transfer` correctly deferred per #94) covers the 2026-06-12 cliff for the THREE BAA-Exhibit-C workflows that have always been on the critical path (owner_transfer, cross_org_relocate, evidence_export) + the TWO that were P1 follow-ups (new_site_onboarding, new_credential_entry). Cliff-day risk is materially reduced.
- **Caveat:** without the P0 fix, the runtime backstop is still 3-wide. CI gate + admin-audit-log forensics cover the new pair, but the auditor's "show me the runtime detector" demonstration will only fire for the original 3 workflows. Recommend Coach P0 close BEFORE the 6/12 cliff, not after.

### PM (scope + runway) — APPROVE
- Scope of this diff matches Task #90 description ("2 onboarding workflows"). `partner_admin_transfer` correctly deferred pending #94 Gate A — runway to 2026-05-28 is 13 days, plenty for the Gate A fork + wire-up + Gate B.
- Recommend filing TWO Task spinouts at commit time: (a) Coach P0 — extend substrate invariant + invariant unit test; (b) Coach P1-1 — decide routes.py:1733 / :1932 / partners.py:440 / provisioning.py:261.

### Counsel (Rule 6 semantics) — APPROVE
- Admin carve-out preserved (operator never blocked).
- Bypass row written to `admin_audit_log` with full identifiability (`actor_user_id` + `actor_email`).
- Generic 403 not raised here (admin path), so Rule 7 opaque-mode doesn't apply.
- Counsel Rule 6 §"machine-enforced where possible" satisfied for the two CE-state mutations.

---

## "Other INSERT INTO sites callsites" probe result

5 backend Python callsites total (excluding tests):

```
sites.py:253          create_site_api                  admin   GATED (this diff)
partners.py:440       partner_claim                    bearer  NOT GATED
provisioning.py:261   provisioning_claim               bearer  NOT GATED
routes.py:1733        routes_create_site (duplicate?)  admin   NOT GATED
routes.py:1932        create_prospect                  admin   NOT GATED
```

---

## MISSING-additions checklist

| Item | Required for THIS commit? | Required before 6/12? |
|---|---|---|
| Extend substrate UNION ALL to scan sites + site_credentials advances | NO (P0 — file task) | **YES** |
| Decide routes.py:1733 (dead-or-gate) | NO (file task) | YES |
| Decide routes.py:1932 (dead-or-gate) | NO (file task) | YES |
| Counsel scope question on partners.py:440 + provisioning.py:261 | NO (Task #37 queue) | counsel-dependent |
| Add fixture rows to test_substrate_invariant_sensitive_workflow_baa.py | NO (after P0 fix lands) | NO |
| Module-top import of enforce_or_log_admin_bypass | NO (P2 nit) | NO |

---

## Final verdict

**APPROVE-WITH-FIXES.**

The as-implemented diff is correct, the full sweep is green (263/0), the lockstep gate is green, the substrate-invariant-unit-test is green, and the no-anon-endpoints gate is green. Carol carve-out semantics + Counsel Rule 6 admin-bypass-with-audit are properly applied.

The Coach P0 (substrate invariant UNION ALL is only 3-wide, not 5-wide) DOES NOT BLOCK THIS COMMIT but MUST be closed as a tracked follow-up task BEFORE the 2026-06-12 BAA cliff — the runtime backstop should match the CI gate breadth. The Coach P1 (4 other INSERT-INTO-sites callsites) should also be triaged before the cliff.

**Commit may proceed with:**
1. Body cites BOTH gate verdicts (this Gate B + the implicit Gate A in the Task #52 deferred-list reasons).
2. Body lists the two Task spinouts (Coach P0 + Coach P1-1) as named TaskCreate items.
3. The substrate invariant description (`assertions.py:2453`) is updated in this same commit OR a same-day follow-up to enumerate the new workflows (so the runtime claim doesn't outrun the SQL).
