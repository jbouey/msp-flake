# Gate B verdict — Task #93 v2 Option E **Commit 1**

**Date:** 2026-05-15
**Reviewer:** Class-B 7-lens worktree-isolated fork (Coach lens leading)
**Artifact under review:** uncommitted parent-tree diff
  - `mcp-server/central-command/backend/migrations/321_baa_signatures_client_org_id_fk.sql` (NEW)
  - `mcp-server/central-command/backend/client_signup.py` (sign_baa endpoint + `_materialize_self_serve_tenant` ON CONFLICT)
  - `mcp-server/central-command/backend/tests/test_no_baa_signatures_trigger_disable_outside_migrations.py` (NEW)
  - `.githooks/pre-push` (gate wired)

**Final verdict: BLOCK — 1 P0 (schema-fixture forward-merge omitted) + 1 P1 (status CHECK constraint Gate-A recommendation dropped). Both fixable in ≤30min before commit lands.**

---

## 300-word summary

Commit 1 implements Option E faithfully on the legal+architectural axis: pre-generates the `client_orgs` UUID Python-side at `/signup/sign-baa`, materializes both `client_orgs` (status='pending_provisioning') and `baa_signatures` (with the new `client_org_id` FK) in the same `admin_transaction`, and the webhook's `_materialize_self_serve_tenant` ON CONFLICT branch correctly promotes `pending OR pending_provisioning → active` when the BAA is confirmed. Mig 321 SQL is well-scoped: trigger disable is bracketed precisely around the backfill UPDATE, ENABLE TRIGGER fires BEFORE COMMIT, orphan-abort check + NOT NULL + FK with `ON DELETE RESTRICT` (correctly NOT CASCADE) all inside the same txn, two indexes (full + partial), admin_audit_log row cites v2 doc, audit-trail entry references task #93. The AST gate at `test_no_baa_signatures_trigger_disable_outside_migrations.py` is well-constructed: 2 tests (scan + sanity-floor), `_is_migration_sql` correctly restricts via `endswith(".sql") and "/migrations/" in rel`, exempts only the test file itself.

**However:** the full pre-push sweep **fails** with 1/262 file failed — `test_sql_columns_match_schema.py` reports `INSERT INTO baa_signatures references unknown column(s) ['client_org_id']`. The implementation overlooked the docstring §(b) requirement to forward-merge schema fixtures (`prod_columns.json` + `prod_column_types.json`) when shipping a migration in the same commit. This is a CI-hard BLOCKER — push will fail. Additionally, Gate A v2 explicitly identified P0-M1: "verify the prod-current `client_orgs.status` CHECK constraint allows the new `pending_provisioning` value. If the constraint exists, mig 321 must `DROP CONSTRAINT … ADD CONSTRAINT …`. If no constraint exists, add one (defensive)." The as-shipped mig adds NO CHECK constraint. Empirically the column has no CHECK today (verified via mig 029_client_portal.sql) — so insertion succeeds, but the defensive-add recommended by Gate A was dropped without justification.

**Counsel-rule-6 latent-class shipping safety:** Commit 1 alone is SAFE because the orphan class is LATENT (no live rename path until Task #94 ships).

---

## Full pre-push sweep

`bash .githooks/full-test-sweep.sh` post-staging:

**Result: 262 PASSED / 1 FAILED / 0 SKIPPED**

Failing file: `tests/test_sql_columns_match_schema.py`

```
- mcp-server/central-command/backend/client_signup.py:332: INSERT INTO baa_signatures references unknown column(s) ['client_org_id']
assert 1 <= 0
INSERT violations=1 but INSERT_BASELINE_MAX=0
```

Targeted-test triple (commanded in brief):

```
tests/test_no_baa_signatures_trigger_disable_outside_migrations.py  2 PASS
tests/test_migration_number_collision.py                            ~ PASS
tests/test_baa_gated_workflows_lockstep.py                          ~ PASS
TOTAL: 15 passed in 16.24s
```

---

## Per-lens verdict

### Steve — APPROVE (mig SQL clean)

End-to-end read of mig 321:

- `DISABLE TRIGGER trg_baa_no_update` (line 37) precedes the synthetic-row DELETE + backfill UPDATE only, with `ENABLE TRIGGER` (line 134) BEFORE `COMMIT` (line 157). Scope is correct.
- Synthetic-row DELETE is constrained to `email LIKE 'adversarial+%@example.com'` (RFC2606 reserved) — cannot match a real customer.
- ADD COLUMN nullable first → backfill → orphan-abort DO-block → `SET NOT NULL` → `ADD FOREIGN KEY ... ON DELETE RESTRICT` (NOT CASCADE — correct; deleting a client_org with a signed BAA must be a deliberate two-step).
- Two indexes: full b-tree `idx_baa_signatures_client_org_id` + partial `WHERE is_acknowledgment_only = FALSE` for `idx_baa_signatures_client_org_id_formal` — partial-index column is `client_org_id` (correct).
- `signup_sessions.client_org_id` nullable + partial index `WHERE client_org_id IS NOT NULL` — sensible.
- All in one BEGIN/COMMIT block — txn atomicity preserved.
- admin_audit_log entries (both quarantine + schema-update) cite **v2** doc, not v1.

Minor (P3, not blocking): `signup_sessions.client_org_id` has no FK to `client_orgs(id)`. Likely intentional (signup_sessions is short-lived, TTL-pruned) but worth a comment. Not blocking.

### Maya (LOAD-BEARING) — APPROVE-WITH-FIX (status CHECK gap)

Schema shape against `prod_columns.json` / `prod_column_types.json`:

- `client_orgs` INSERT (client_signup.py:312-327): 7 column-list values `(id, name, primary_email, billing_email, state, stripe_customer_id, status)` ↔ 7 `VALUES ($1, $2, $3, $3, $4, $5, 'pending_provisioning')`. All 7 columns exist in prod (`id` uuid, `name` varchar, `primary_email` varchar, `billing_email` varchar, `state` varchar, `stripe_customer_id` varchar, `status` varchar). `$3` for both `primary_email` and `billing_email` is fine (same value, deliberate). `state` from `signup_sessions.state` (text) → `client_orgs.state` (varchar) implicit cast is safe.
- ON CONFLICT (primary_email) DO UPDATE RETURNING id — Postgres RETURNING-after-UPDATE returns the EXISTING row's id (verified by docs). Correct.
- `baa_signatures` INSERT (line 332): 10 columns including `client_org_id` — column WILL exist after mig 321 applies, but the schema fixture doesn't reflect that yet.

**P0-MAYA-1**: full sweep failure. Schema fixtures must be forward-merged. Required edits:
- `prod_columns.json` → `baa_signatures` array: insert `"client_org_id"` (sort-preserved); `signup_sessions` array: insert `"client_org_id"`
- `prod_column_types.json` → `baa_signatures` dict: `"client_org_id": "uuid"`; `signup_sessions` dict: `"client_org_id": "uuid"`
- `prod_column_widths.json` → NO entry needed (UUID has no character_maximum_length; verified by the regen script's `if v[1] is not None` filter)

**P1-MAYA-2**: Gate A v2 §FIX-M1 (P0 at design time) was: *"verify the prod-current `client_orgs.status` CHECK constraint allows the new `pending_provisioning` value. If the constraint exists, mig 321 must `DROP/ADD`. If no constraint exists, add one (defensive)."* Verified via `migrations/029_client_portal.sql:36`: `status VARCHAR(50) NOT NULL DEFAULT 'active'` — **NO CHECK constraint**. Insertion succeeds today. But the *defensive-add* recommendation from Gate A was silently dropped. Mig 321 should add:
```sql
ALTER TABLE client_orgs
    ADD CONSTRAINT client_orgs_status_ck
    CHECK (status IN ('pending', 'pending_provisioning', 'active',
                      'suspended', 'churned', 'deprovisioned'));
```
(Inventory the full list from grep against the codebase before committing — `suspended`/`churned` are in `029_client_portal.sql` comment; `deprovisioned` per the `deprovisioned_at` column.) This is a defense-in-depth recommendation; not strictly blocking the merge but the spec said add it and the implementation didn't.

### Carol (LOAD-BEARING) — APPROVE (backwards-compat verified)

Existing `baa_status.py` helpers (3 functions: lines 68-114 `is_baa_on_file_verified`, 120-162 `baa_status_for_org`, 238-294 `baa_enforcement_ok`) join via `LOWER(bs.email) = LOWER(co.primary_email)`. After mig 321 lands, the `email` column is unchanged — just NOW has a `client_org_id` sibling. Every helper continues to function correctly. **Commit 2 (24h later) migrates these helpers to the FK-based join.** Until then they remain on the email join and produce correct results for any new sign-baa row (the FK is set AND the email is set; both joins yield the same row). Backwards-compat confirmed.

### Coach (insidious-antipattern probe) — BLOCK (P0 + observations)

Three probes from the brief, plus the discovered fixture omission:

(a) **status CHECK constraint dropped from scope.** Covered under Maya P1. Today's column has no CHECK; insertion succeeds. Dropping the *defensive-add* recommendation is technically safe but Gate A explicitly required it. Tightening to a CHECK now (P1) closes the class structurally — anyone writing `status='actve'` (typo) won't silently land a bad row.

(b) **`pending_provisioning` propagation.** grep across `mcp-server/central-command/backend/` for `status = 'active'` / `status = 'pending'` filters touching `client_orgs`:
- `companion.py:368` — `SELECT COUNT(*) FROM client_orgs WHERE status = 'active'` — pending_provisioning rows correctly excluded (org not yet operational).
- All other status filters target different tables (partners, hipaa_policies, hipaa_baas, etc.).
- No frontend badge / operator dashboard / per-org status enum that needs updating today.
- Verdict: no downstream consumer break. **PASS.**

(c) **`_materialize_self_serve_tenant` ON CONFLICT trace.** Webhook fires AFTER sign_baa → signup_row.baa_signature_id IS NOT NULL → org_status='active' → ON CONFLICT (primary_email) DO UPDATE → `CASE WHEN status IN ('pending', 'pending_provisioning') AND EXCLUDED.status = 'active' THEN 'active' ELSE status END` → row in 'pending_provisioning' promotes to 'active'. **Correct.** Pre-#93 legacy path (no prior 'pending_provisioning' row): INSERT goes straight to 'active'. Both paths preserved.

(d) **AST gate's `_is_migration_sql` regex.** Read at line 53: `return rel.endswith(".sql") and "/migrations/" in rel`. Exempts only .sql files under any `/migrations/` directory. A `.py` file in `migrations_helpers/` would NOT be exempt (correct — only SQL belongs there). A `.sql` file outside `migrations/` would NOT be exempt (correct — runtime SQL strings shouldn't disable the trigger). **PASS.**

(e) **Mig 321 audit-doc citation v2.** Verified line 67: `'audit/coach-93-v2-signup-flow-reorder-gate-a-2026-05-15.md'` (line 151 similar). **PASS** on the Counsel requirement.

**P0-COACH-1 (the discovered class):** the implementation's diff missed three sibling files (schema fixtures) — exactly the "what's MISSING that should have been added" antipattern from Session 220 lock-in. This is **why** Gate B must run the full sweep, not just review the diff. Confirmed.

### Auditor — APPROVE

§164.316(b)(2)(i) retention — synthetic-row DELETE is covered by the admin_audit_log INSERT at mig 321 line 51-72. Captures: migration ID, reason (cites RFC2606 example.com test-data scope), rows_quarantined count, gate_a_artifact link. Counsel position (audit/coach-93-v2-signup-flow-reorder-gate-a-2026-05-15.md §Maya) is cited. The example.com rationale is precise — RFC2606-reserved domain cannot be a real customer email; retention scope of §164.316(b)(2)(i) does not extend to synthetic test rows in BAA tables. **PASS.**

### PM — APPROVE

~4hr coding budget per Gate A v2 §Time-budget. Actual implementation is ~190 lines mig + ~30 lines client_signup.py refactor + ~110 lines test + ~1 line pre-push wire. Fits the budget. Schema-fixture edits are <10min addition. CHECK constraint add is <5min. **PASS** after fix-up.

### Counsel (LOAD-BEARING) — APPROVE (Commit-1-alone is safe)

Rule 6 (machine-enforced BAA state):

- Post-Commit-1: FK column exists on `baa_signatures`; helpers still join by `LOWER(email)`.
- Orphan class structural closure happens in Commit 2 (helpers migrate to FK join).
- **Is Commit-1-alone safe in the interim?** YES. The orphan class is LATENT:
  - Per Task #91 (completed): no live rename path exists in code that mutates `client_orgs.primary_email` without issuing a sibling `baa_signatures` row.
  - Task #94 (the BAA-aware rename helper) is `pending` — not yet shipped — so no caller can currently trigger the orphan.
  - Task #95 (frontend Organizations.tsx silent no-op) is completed: the UI no longer accepts primary_email changes silently.
- Therefore the window between Commit 1 (FK column landed, helpers still on email join) and Commit 2 (~24h later, helpers migrate) does NOT introduce orphan exposure that wasn't already covered by the existing #91/#95 controls.
- Counsel rule 6 not violated.

**PASS** on legal safety. The 24h soak between commits is justified (allows mig 321 to apply cleanly in prod, allows any signup-flow misfire to surface via the new INSERT-time FK before helpers shift).

---

## Required fixes before commit lands

### P0 (BLOCKER — push will fail at CI)
**P0-1 (Maya/Coach)**: Forward-merge schema fixtures. Edit:
- `mcp-server/central-command/backend/tests/fixtures/schema/prod_columns.json`:
  - `baa_signatures` array → insert `"client_org_id"` (alphabetical)
  - `signup_sessions` array → insert `"client_org_id"` (alphabetical)
- `mcp-server/central-command/backend/tests/fixtures/schema/prod_column_types.json`:
  - `baa_signatures` dict → add `"client_org_id": "uuid"`
  - `signup_sessions` dict → add `"client_org_id": "uuid"`
- `prod_column_widths.json`: **no edit** (UUID has no character_maximum_length).
- Re-run `bash .githooks/full-test-sweep.sh` and confirm 263/263 PASS.

### P1 (Gate A recommendation; dropped without justification)
**P1-1 (Maya)**: Add defensive CHECK constraint on `client_orgs.status` as recommended by Gate A v2 §FIX-M1. Inside mig 321 (before final COMMIT, after step 6):
```sql
ALTER TABLE client_orgs
    ADD CONSTRAINT client_orgs_status_ck
    CHECK (status IN (
        'active', 'pending', 'pending_provisioning',
        'suspended', 'churned', 'deprovisioned'
    ));
```
Inventory the full status-value set against current prod data BEFORE adding (otherwise the CHECK rejects historical rows):
```bash
ssh root@178.156.162.116 docker exec mcp-postgres psql -U mcp -d mcp -c \
  "SELECT DISTINCT status FROM client_orgs ORDER BY 1"
```
If unknown statuses surface, add them to the IN-list. (If skipped, file as task #93-FU-E "deferred status CHECK constraint" — but explicit deferral must pass a sub-Gate-B per Session-220 lock-in. Cheaper to add it now.)

### P2 (note, not blocking)
**P2-1**: `signup_sessions.client_org_id` has no FK to `client_orgs(id)`. Likely intentional given the table's TTL-pruned nature, but a one-line comment in mig 321 §6 explaining the omission would help future readers.

### P3 (cosmetic)
**P3-1**: The 14-day `pending_provisioning_orgs_pruner` task referenced by Gate A v2 (task #93-FU-D) is not part of Commit 1. Confirmed deferred. **PASS** as a separate task.

---

## Commit-readiness checklist

- [ ] Apply P0-1 (3 fixture file edits — really 2, since widths.json is unaffected).
- [ ] Apply P1-1 (status CHECK constraint) OR file deferral with named sub-Gate-B justification.
- [ ] Re-run `bash .githooks/full-test-sweep.sh` → expect 263/263 PASS.
- [ ] Re-run targeted triple (`test_no_baa_signatures_trigger_disable_outside_migrations.py` + `test_migration_number_collision.py` + `test_baa_gated_workflows_lockstep.py`) → expect 15/15 PASS.
- [ ] Commit body cites BOTH `audit/coach-93-v2-signup-flow-reorder-gate-a-2026-05-15.md` (Gate A) AND `audit/coach-93-v2-commit1-gate-b-2026-05-15.md` (this Gate B verdict).
- [ ] Post-push, wait CI green → `curl /api/version` → confirm `runtime_sha == disk_sha == deployed_commit`.
- [ ] 24h soak before Commit 2 (helper migration to FK join).

---

## Verdict line

**Gate B BLOCK** — fix P0-1 (schema fixtures), apply or formally defer P1-1 (status CHECK), re-run full sweep, then re-submit. Estimated fix-up: 15-25min. All other 6 lenses approve. Counsel-rule-6 safety of Commit-1-alone confirmed.
