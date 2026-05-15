# Gate A — Task #92: extend `sensitive_workflow_advanced_without_baa` to evidence_export

**Date:** 2026-05-15
**Reviewer:** fork (Steve / Maya / Carol / Coach / PM / Counsel)
**Verdict:** **APPROVE-WITH-FIXES** — one P0 prereq (audit-row enrichment) MUST land before the SQL extension; once that ships, the extension is a ~25-line additive `UNION ALL` plus runbook update. Effort: 1 small commit, ~2-3 hrs including Gate B.

---

## 250-word summary

Task #52 shipped a runtime backstop invariant covering two durable BAA-gated workflows (`cross_org_relocate`, `owner_transfer`). Gate B (audit/coach-52-baa-enforcement-gate-b-2026-05-14.md) accepted the omission of `evidence_export` on the grounds that it has no durable state — but logged a P1 follow-up: an un-gated auditor-kit endpoint could ship in the future, and only an `admin_audit_log` scan would catch it at runtime. This task closes that gap.

The auditor-kit download writes a structured `auditor_kit_download` row to `admin_audit_log` on every successful download (evidence_chain.py:4919-4946), carrying `auth_method` (key name `"auth_method"`, NOT `"method"`) and authn identifiers in `details`. **The audit row does NOT currently carry `site_id` in `details`** — it's only in `target = f"site:{site_row.site_id}"`. Extending the invariant to JOIN-resolve `site_id → client_org_id` from `target` is fragile (string parse), so the P0 prereq is enriching the audit write to include `"site_id"` and `"client_org_id"` as top-level keys before the invariant rolls out. Once enriched, the extension is a 3rd `UNION ALL` branch that selects rows from `admin_audit_log` in the last 30 days where `action='auditor_kit_download'` AND `details->>'auth_method' IN ('client_portal','partner_portal')`, then runs them through the same `baa_enforcement_ok()` predicate. The admin and legacy-token branches are excluded by the method filter (Carol carve-out #3 + #4 preserved). No new `baa_enforcement_bypass` row applies — the existing inline gate raises 403 rather than logging a bypass, so the bypass-row exclusion is a no-op for this branch (Coach's point — correctly modeled).

---

## Findings by lens

### Steve — audit-row shape audit (P0 PREREQ)

Read evidence_chain.py:4919-4946. Audit row written on success:

```sql
INSERT INTO admin_audit_log
    (user_id, username, action, target, details, ip_address, created_at)
VALUES
    (NULL, :username, :action, :target, :details::jsonb, :ip, NOW())
```

with:
- `action = 'auditor_kit_download'` ✓
- `target = f"site:{site_row.site_id}"` — site_id ONLY in the `target` string
- `details = {auth_method, user_id, partner_id, role, bundle_count, ...}`

**Gaps the invariant needs closed:**

1. **`site_id` is in `target`, not `details`.** A SQL JOIN from `admin_audit_log` → `sites` requires either:
   - **(preferred)** Add `"site_id": site_row.site_id` and `"client_org_id": <resolved>` as top-level keys in the `details` dict.
   - **(fragile)** Extract via `SUBSTRING(target FROM 'site:(.+)')` — works today but couples the invariant to a string format that's not contract-stable.

2. **Key name is `auth_method` not `method`.** Task brief said "`details->>'method'`" — that would miss every row. Filter MUST be `details->>'auth_method' IN ('client_portal','partner_portal')`.

3. **`client_org_id` resolution is one SQL hop.** Resolving inside the invariant SQL adds a JOIN to `sites`. Writing it at audit time (computed once, denormalized into the row) is the better trade — fewer joins in the invariant hot path and survives a future site-org reparenting (forensics intent: the org at the time of the download is what's auditable, not whatever the JOIN resolves to next year).

**P0 PREREQ FIX** (commit BEFORE the assertion extension):

```python
# evidence_chain.py around line 4928
"details": _json.dumps({
    "site_id": site_row.site_id,          # NEW — invariant-required
    "client_org_id": site_row.client_org_id,  # NEW — invariant-required, denormalized snapshot
    "auth_method": auth_method,
    ...
}),
```

`site_row.client_org_id` must already be selected in the site_row fetch — verify before writing the patch (one grep). If not present, add it to the SELECT and to the row mapping.

### Maya — SQL extension shape

The 3rd `UNION ALL` branch to add at assertions.py:1893 (inside the existing `conn.fetch()` block):

```sql
UNION ALL
SELECT 'evidence_export' AS workflow,
       aal.details->>'client_org_id' AS org_id,
       aal.details->>'site_id' AS site_id,
       aal.id::text AS row_id,
       aal.created_at AS advanced_at
  FROM admin_audit_log aal
 WHERE aal.action = 'auditor_kit_download'
   AND aal.created_at > NOW() - INTERVAL '30 days'
   AND aal.details->>'auth_method' IN ('client_portal','partner_portal')
```

**Column-drift check (prod schema, from 008_admin_auth.sql):**

| col | type | invariant use | OK? |
|-----|------|---------------|-----|
| `id` | SERIAL (int) | `row_id` | OK (cast `::text`) |
| `action` | VARCHAR(100) | filter | OK |
| `target` | VARCHAR(255) | NOT USED (deferred via P0 prereq) | OK |
| `details` | JSONB | extract `client_org_id`, `site_id`, `auth_method` | OK |
| `created_at` | TIMESTAMPTZ DEFAULT NOW() | `advanced_at` + window predicate | OK |

No `prod_column_types.json` fixture file is checked in to this repo (verified via `find`) — column shape verified directly from the migration. Schema mig 008 has NOT been altered (no later migration touches `admin_audit_log` table shape).

**Index check:** `idx_admin_audit_action ON admin_audit_log(action)` exists (mig 008:79). Selectivity is fine for `action='auditor_kit_download'` (low-cardinality vs the action set). The `created_at > NOW() - INTERVAL '30 days'` predicate uses a sequential scan fall-back within the action partition — acceptable for 60s tick on table at current size (≤100K rows/30d typical). If `admin_audit_log` grows beyond ~5M rows, add a partial expression index `(created_at) WHERE action='auditor_kit_download'` — flagged as P2-FOLLOWUP, not blocking.

### Carol — orphan coverage / carve-out correctness (Counsel Rule 4)

The two carve-outs from `check_baa_for_evidence_export` (baa_enforcement.py:269-271):
- `admin` — platform operator, not the CE (Carve-out #3)
- `portal` / legacy `?token=` — external auditor, blocking would itself be a §164.524 violation (Carve-out #4, legally mandatory)

The method filter `details->>'auth_method' IN ('client_portal','partner_portal')` correctly EXCLUDES both carve-outs from the invariant scan. **Verified by enumeration:**

| auth.method | inline gate | audit row written? | invariant filter | result |
|-------------|-------------|---------------------|------------------|--------|
| `admin` | bypassed | YES | NOT IN filter | EXCLUDED ✓ |
| `client_portal` | enforced (403 if no BAA) | YES (only on success) | IN filter | INCLUDED ✓ |
| `partner_portal` | enforced (403 if no BAA) | YES (only on success) | IN filter | INCLUDED ✓ |
| `portal` (legacy magic-link) | bypassed (§164.524 mandate) | YES | NOT IN filter | EXCLUDED ✓ |
| legacy `?token=` query | bypassed | YES | NOT IN filter | EXCLUDED ✓ |
| `unknown` | bypassed | YES | NOT IN filter | EXCLUDED ✓ |

The invariant therefore only flags rows where the BAA gate SHOULD have fired but the row still landed — i.e., a code path that bypassed `check_baa_for_evidence_export`, OR an org whose BAA lapsed after the download (the 30-day backward window catches the latter). Carve-out correctness preserved.

**One subtle point**: a legitimate gated download leaves an audit row WITH a passing BAA at the time of download. If the org's BAA later lapses, the invariant will flag the historical row as "advanced without BAA" — but that's the desired forensic signal, because §164.504(e) requires the BAA to be in place AT THE TIME of the action, and `baa_enforcement_ok()` returning FALSE at scan time also means the BAA is currently not in force. This matches the Task #52 behavior for the other two workflows — no special handling needed.

### Coach — bypass-row exclusion logic

Task #52's invariant excludes rows with a matching `baa_enforcement_bypass` audit row (assertions.py:1907-1918) — that's the admin carve-out for state-machine workflows where `enforce_or_log_admin_bypass()` writes the bypass row when an admin advances the workflow despite no BAA.

For `evidence_export`, the inline gate is `check_baa_for_evidence_export` which **raises 403 — does NOT write a bypass row** (baa_enforcement.py:295-302). Therefore the existing `baa_enforcement_bypass` exclusion does NOT apply to the evidence_export branch.

The carve-out for evidence_export is the **method filter itself** (admin / portal / legacy-token branches are filtered OUT before any row reaches the BAA check), not a bypass-row lookup. This is the structural difference Coach flagged in the brief — correctly modeled.

**Consequence**: the per-row loop at assertions.py:1898-1918 should SKIP the bypass-row lookup when `workflow == 'evidence_export'`. Either:

```python
if row["workflow"] != "evidence_export":
    bypass = await conn.fetchval(...)
    if bypass:
        continue
```

OR (cleaner) restructure the workflow column to carry a `needs_bypass_check` flag. The first form is 3 lines and lower-blast-radius — recommend that one.

### PM — effort + Gate B requirements

**Effort:** 1 commit, ~2-3 hours including Gate B.

| step | LOC | risk |
|------|-----|------|
| 1. P0 audit-row enrichment (evidence_chain.py:4919-4946) | +2 lines | low |
| 2. Extend `_check_sensitive_workflow_advanced_without_baa` UNION ALL branch | +10 lines | low |
| 3. Add `if workflow != 'evidence_export'` guard around bypass-row lookup | +3 lines | low |
| 4. Update `assertions.py:2423` description string to mention evidence_export | +30 chars | low |
| 5. Update `assertions.py:3293` runbook entry text similarly | +30 chars | low |
| 6. Append "evidence_export" section to `substrate_runbooks/sensitive_workflow_advanced_without_baa.md` | +20 lines markdown | low |
| 7. Test: new case in `tests/test_substrate_invariant_sensitive_workflow_baa.py` (if exists; otherwise add 2 cases — happy-path gated row excluded by passing BAA, violation case) | +40 lines | low |

**Gate B MUST cite:**

1. Full pre-push sweep pass count (CLAUDE.md lock-in 2026-05-11 — diff-scoped Gate B forbidden).
2. Runtime production query proving the new branch fires correctly: `SELECT workflow, COUNT(*) FROM (existing invariant SQL with new UNION ALL) GROUP BY 1` against prod — expect 0 evidence_export violations today (we have no failing-BAA orgs at present). If non-zero, investigate before close.
3. Confirmation the audit-row enrichment landed FIRST and at least one new `auditor_kit_download` row in prod has the new `site_id` / `client_org_id` keys.
4. Lockstep check: workflow string `'evidence_export'` does NOT conflict with the BAA-gated workflow registry list (`assert_workflow_registered` — verify the registry has an `evidence_export` entry or the invariant's workflow column is decoupled from registry — the registry is for the `require_active_baa` factory, not the invariant SQL string, so this is a documentation-only check).

### Counsel — Rule 6 soundness

Counsel Rule 6 ("No legal/BAA state may live only in human memory. BAA state gates functionality, not just paperwork. Expired BAA must block new ingest or sensitive workflow advancement. Machine-enforced where possible."): the extension makes evidence_export's runtime backstop match the build-time CI gate's coverage — the BAA-gated lockstep gains a true third surface (CI gate / inline 403 / substrate invariant), parallel to the two state-machine workflows.

Counsel Rule 4 ("No segmentation design that creates silent orphan coverage. Orphan detection is sev1, not tolerable warning."): the existing invariant is registered as **sev1** (assertions.py:2422 — verify the actual `severity=` field; if it's `sev2`, that's a separate Task #52 follow-up, NOT this task's scope). Adding the evidence_export branch preserves the same severity — the same legal class of violation gets the same alarm grade.

§164.504(e) test: a CE-affiliated identity (client_portal or partner_portal) successfully downloaded PHI-adjacent attestation evidence for an org whose BA-relationship has no current formal BAA on file. That's the exact class of audit failure §164.504(e) anticipates, and the invariant produces a producible alert for it. Sound.

**No counsel-prerequisite questions** — this is a substrate-layer hardening of an already-counsel-approved design.

---

## Final verdict: APPROVE-WITH-FIXES

**P0 (blocking — land BEFORE the assertions.py change):**
1. Enrich the `auditor_kit_download` audit-row `details` JSON with `site_id` and `client_org_id` as top-level keys (evidence_chain.py:4928). Verify `site_row.client_org_id` is already SELECT-loaded; if not, add it.

**P1 (in the same task, NOT a follow-up):**
2. Extend `_check_sensitive_workflow_advanced_without_baa` SQL with the 3rd `UNION ALL` branch (filter on `auth_method IN ('client_portal','partner_portal')`).
3. Guard the existing bypass-row exclusion with `if workflow != 'evidence_export'` — evidence_export has no bypass row by design.
4. Update assertion description (line 2423) and runbook entry (line 3293) to mention evidence_export.
5. Append "evidence_export branch" section to `substrate_runbooks/sensitive_workflow_advanced_without_baa.md` — explain: filter logic, why admin/legacy-token rows are excluded (carve-outs #3 + #4), what to do when it fires (investigate which code path bypassed `check_baa_for_evidence_export`, or check if the org's BAA expired after the download).
6. Test: positive + negative case in `tests/test_substrate_invariant_sensitive_workflow_baa.py`.

**P2 (followup, NOT blocking):**
7. If `admin_audit_log` grows past ~5M rows over the next year, add partial expression index on `(created_at) WHERE action='auditor_kit_download'`.

**Effort:** 1 commit (≤ ~80 lines net), Gate B 1 hr (sweep + runtime cite). Total: 2-3 hours.

**Severity flag for Gate B**: confirm the parent invariant is `sev1` (not `sev2`) before commit — if mismatched with Counsel Rule 4's orphan-detection mandate, escalate as a separate fix.
