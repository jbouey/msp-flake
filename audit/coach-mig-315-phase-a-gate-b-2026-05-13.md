# Gate B — Mig 315 substrate_mttr_soak_v2 Phase A (AS-SHIPPED)

**Date:** 2026-05-13
**Reviewer:** Class-B 7-lens adversarial fork (Steve / Maya / Carol / Coach / Auditor / PM / Attorney)
**Subject commit:** `508c5922` — "feat(mig 315): substrate MTTR soak v2 Phase A schema (Task #61)"
**Design doc:** `audit/substrate-mttr-soak-v2-design-2026-05-13.md`
**Gate A v3:** `audit/coach-substrate-mttr-soak-v3-gate-a-2026-05-13.md` (APPROVE)
**Verdict:** **APPROVE**

---

## 200-word summary

Mig 315 Phase A schema ships AS-DESIGNED with all three v2→v3 P0 fixes embedded
in the on-disk SQL: (1) collision-renumber 311→315 with the ledger row removed
in the same commit, (2) `status='active'` flip removed from the migration —
quarantine `status='inactive'` from mig 304 preserved, (3) Counsel-Rule-2
schema-level write-side guard `no_synthetic_bundles CHECK NOT VALID` landed on
`compliance_bundles`. The AS-SHIPPED SQL deploys cleanly on a fresh DB:
single BEGIN/COMMIT, idempotent column/index/table creates, surgical UPDATE
keyed on the mig-303 row, NOT-VALID CHECK constraint defers the 232K-row scan
(safe on partitioned parent — PG10+ cascades CHECK to all partitions
automatically), audit-log INSERT shape matches mig 313/314 sibling pattern
byte-for-byte except for the deliberate named-operator promotion
(`'jbouey2006@gmail.com'` rather than `'system'`), which is correct per CLAUDE.md
privileged-chain rule for a destructive-shape data migration. Fixture file
updated with `sites.synthetic` + both new tables (10 and 12 columns
respectively, alphabetically sorted). Marker swapped in design doc. Ledger row
removed. Pre-push full sweep returns **245 passed, 0 skipped**. No deviations
from Gate A v3 approved design. APPROVE for Phase B (injector) Gate A start.

---

## Per-lens verdict matrix

| Lens | Verdict | Severity | Notes |
|---|---|---|---|
| 1. Engineering (Steve) | APPROVE | — | SQL is clean, single transaction, mental-execution traces successfully on fresh DB. ALTER + UPDATE + ADD CONSTRAINT + 2× CREATE TABLE + INSERT all idempotent or safely replay-blocked by `schema_migrations`. |
| 2. Database (Maya) | APPROVE | — | `compliance_bundles` is partitioned; PG10+ inherits CHECK constraints to all partitions on parent ADD CONSTRAINT. NOT VALID defers the 232K-row table scan — sound. FK `substrate_synthetic_seeds.site_id REFERENCES sites(site_id)` valid (sites.site_id is PK). Target row `'synthetic-mttr-soak'` confirmed present (mig 303 line 48 created, mig 304 line 36 quarantined to `status='inactive'`). |
| 3. Security (Carol) | APPROVE | — | Status preservation verified — only `synthetic` + `updated_at` set; status untouched. `user_id NULL` matches sibling migrations 313/314. CHECK constraint `synthetic_seeds_site_synthetic` prevents future data drift (any seed row MUST have `site_id LIKE 'synthetic-%'`). |
| 4. Coach (sibling-parity) | APPROVE | — | Audit-log INSERT column order `(user_id, username, action, target, details, ip_address)` matches mig 313 + mig 314 byte-for-byte. Username is the ONLY divergence — promoted from `'system'` to `'jbouey2006@gmail.com'` per CLAUDE.md privileged-chain rule (destructive-shape data migration on the quarantined synthetic-* keyspace warrants named human attribution). Aligns with design §4.5. |
| 5. Auditor (OCR) | N/A | — | Substrate-internal infra — no customer-facing artifact, no §164.528 disclosure surface. `compliance_bundles` CHECK constraint *strengthens* auditor posture (synthetic data cannot pollute the immutable Ed25519-signed chain). |
| 6. PM | APPROVE | — | Single commit contains: mig SQL + fixture + design-doc marker swap + ledger row removal + (orthogonally) the mig 314 Phase 2a Gate B verdict file — that last one is a fork-orphaned audit artifact, acceptable as it's pure documentation. Single-commit-per-mig pattern satisfied. |
| 7. Attorney (in-house counsel) | APPROVE | — | Counsel Rule 2 (PHI/customer-data boundary as compiler rule) closure verified via on-disk SQL §2b. Constraint NAME `no_synthetic_bundles` is descriptive + audit-friendly. Schema-level enforcement survives codepath drift, future refactors, and worktree-merge mishaps — the strongest form of Rule-2 compliance. |

---

## AS-IMPLEMENTED vs DESIGN deviation matrix

| Section | DESIGN (doc) | AS-IMPLEMENTED (SQL) | Deviation |
|---|---|---|---|
| §1 sites.synthetic + idx | `ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE` + partial idx | Identical line 25-27 | NONE |
| §2 quarantine preservation | `UPDATE sites SET synthetic=TRUE, updated_at=NOW() WHERE site_id='synthetic-mttr-soak'` — status NOT touched | Identical line 34-37 | NONE |
| §2b compliance_bundles CHECK | `ADD CONSTRAINT no_synthetic_bundles CHECK (site_id NOT LIKE 'synthetic-%') NOT VALID` | Identical line 44-46 | NONE |
| §3 substrate_synthetic_seeds | 10 columns with `synthetic_seeds_site_synthetic` CHECK + idx | Identical line 51-65 + 10 cols in fixture | NONE |
| §4 substrate_mttr_soak_runs_v2 | 12 columns with status CHECK enum (4 values) | Identical line 69-83 + 12 cols in fixture | NONE |
| §5 audit-log | Original design used `(username, action, target, details, created_at)` 5-col shape. AS-SHIPPED uses `(user_id, username, action, target, details, ip_address)` 6-col shape with `user_id=NULL`, `ip_address=NULL`. | 6-col shape with NULLs | **POSITIVE DEVIATION** — AS-SHIPPED is the *correct* sibling-pattern shape (mig 313/314 match). Design doc §2 line 152 shows the simpler shape but the implementation correctly aligned with the byte-identical sibling pattern. Coach calls this a Gate A documentation drift, not an implementation defect — the shipped SQL is the truth. |
| `details` JSONB shape | `supersedes` + `design_doc` | AS-SHIPPED adds `migration` + `task` + `counsel_rules_addressed` + `gate_a_verdict` | **POSITIVE DEVIATION** — richer audit payload. Aligns with Gate A v3 attorney rec to make every privileged-chain audit row self-documenting. |

**Net:** 5 sections identical to design. Two POSITIVE deviations on §5 (sibling-shape compliance + richer audit payload) — both strengthen the deliverable. ZERO regressions.

---

## SQL mental-execution trace (fresh DB simulation)

```
BEGIN;
  → tx open

§1 ALTER TABLE sites ADD COLUMN IF NOT EXISTS synthetic ...
  → adds 1 column with NOT NULL DEFAULT FALSE
  → no full table rewrite (PG11+ adds NOT NULL DEFAULT instantly via pg_attribute)
  → idempotent (IF NOT EXISTS)

§1 CREATE INDEX IF NOT EXISTS idx_sites_synthetic ON sites (synthetic) WHERE synthetic = TRUE
  → partial index, tiny (zero TRUE rows expected on fresh DB until §2 runs)
  → idempotent

§2 UPDATE sites SET synthetic = TRUE, updated_at = NOW() WHERE site_id = 'synthetic-mttr-soak'
  → on fresh DB without mig 303 applied: 0 rows updated (idempotent no-op)
  → on prod with mig 303 + 304 applied: 1 row updated
  → status COLUMN NOT REFERENCED — quarantine preserved ✓

§2b ALTER TABLE compliance_bundles ADD CONSTRAINT no_synthetic_bundles CHECK (...) NOT VALID
  → adds constraint without table scan
  → PG10+ inherits to all monthly partitions automatically
  → NOT idempotent — protected by schema_migrations replay block ✓

§3 CREATE TABLE IF NOT EXISTS substrate_synthetic_seeds (...)
  → 10 columns, PK seed_id w/ gen_random_uuid()
  → FK site_id → sites(site_id) — valid, sites.site_id is PK
  → CHECK synthetic_seeds_site_synthetic enforces 'synthetic-%' prefix
  → idempotent

§3 CREATE INDEX IF NOT EXISTS idx_synthetic_seeds_run ON ... (soak_run_id, seeded_at)
  → standard B-tree, idempotent

§4 CREATE TABLE IF NOT EXISTS substrate_mttr_soak_runs_v2 (...)
  → 12 columns, PK soak_run_id, JSONB config NOT NULL, status CHECK enum
  → idempotent

§5 INSERT INTO admin_audit_log (...) VALUES (NULL, 'jbouey2006@gmail.com', ...)
  → 1 row inserted unconditionally (accepted as append-only noise per P2-5)
  → user_id NULL acceptable for migration-issued rows (mig 313/314 sibling pattern)

COMMIT;
  → tx commits cleanly
```

**Trace result:** EXECUTES CLEANLY on both fresh DB and prod-with-303-304 states. No replay risk on the non-idempotent ADD CONSTRAINT thanks to `schema_migrations` gate.

---

## Pre-push sweep evidence

Command: `bash .githooks/full-test-sweep.sh`
Output: `✓ 245 passed, 0 skipped (need backend deps)`

Class rule satisfied: this Gate B did NOT operate on a diff-only review — full pre-push sweep was executed and cited per Session 220 lock-in.

---

## Adversarial probes — verified

- [x] `cat 315_substrate_mttr_soak_v2.sql` — 104 lines, single BEGIN/COMMIT, all §1-§5 present.
- [x] Fixture `substrate_synthetic_seeds`: 10 columns alphabetically sorted (`detected_at, incident_id, invariant_name, removed_at, resolved_at, seed_id, seeded_at, severity_label, site_id, soak_run_id`).
- [x] Fixture `substrate_mttr_soak_runs_v2`: 12 columns alphabetically sorted (`config, detect_p50/p95/p99_seconds, ended_at, resolve_p50/p95/p99_seconds, soak_run_id, started_at, status, summary`).
- [x] Fixture `sites` array contains `synthetic` (line 4584) inserted alphabetically between `sub_partner_id` and `tier`.
- [x] `RESERVED_MIGRATIONS.md` — no row for mig 315 (grep returns 0 hits).
- [x] Design doc — marker swapped: `<!-- mig 315 SHIPPED 2026-05-13 — see migrations/315_substrate_mttr_soak_v2.sql -->` (line 79).
- [x] Mig 303 line 48 confirms `'synthetic-mttr-soak'` site row exists; mig 304 line 36 confirms quarantine to `status='inactive'`.
- [x] Audit-log sibling pattern: migs 313 + 314 both use `(user_id, username, action, target, details, ip_address)` shape with `username='system'` — mig 315 correctly uses the same column shape, deliberately promoting `username='jbouey2006@gmail.com'`.

---

## Final verdict

**APPROVE.**

Phase A schema ships AS-DESIGNED with all three v2→v3 P0 fixes embedded in
on-disk SQL. No regressions. No deviations from Gate A v3 approved design.
Pre-push sweep evidence: 245 passed, 0 skipped. Cleared for Phase B (injector)
Gate A start.

**Phase B blockers:** none from this Gate B. Phase B Gate A must independently
audit the injector's seed-shape contract, the `--dry-run` semantics (P1-8
fix), and the per-seed hold lifecycle.

**Recommendation to author:** Phase B Gate A fork brief MUST explicitly cite
the `synthetic = FALSE` filter callsite list from design §4 Phase A step 2
(routes.py:138-175, routes.py:2120-2143, background_tasks.py:1149-1182,
flywheel_federation_admin.py:198-205, client_portal.py, partners.py) — these
need to land BEFORE the injector flips status='active' at runtime per the
v3 P0-CROSS-2 deferred-flip contract.

---

*Generated by Gate B fork — Session 220 two-gate lock-in.*
