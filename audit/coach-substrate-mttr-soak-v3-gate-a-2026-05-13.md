# Gate A v3 — Substrate-MTTR Soak v2 Design (P0-fix verification)

**Date:** 2026-05-13
**Reviewer:** 7-lens fork (Steve / Maya / Carol / Coach / Auditor / PM / Counsel)
**Mode:** TIGHT v3 verification — confirm the 3 v2 P0s closed cleanly, no scope-creep
**Prior verdict:** `audit/coach-substrate-mttr-soak-v2-gate-a-2026-05-13.md` (APPROVE-WITH-FIXES: 3 P0 + 5 P1 + 2 P2)
**Design under review:** `audit/substrate-mttr-soak-v2-design-2026-05-13.md` (patched in-place)

---

## 200-word summary

All 3 v2 P0s are materially closed in the mig 315 SQL block. P0-CROSS-1: zero stale `mig 311` references inside the SQL; `INSERT INTO admin_audit_log ... target='mig:315'` at line 156. P0-CROSS-2: the `UPDATE sites` at lines 100-103 sets only `synthetic = TRUE, updated_at = NOW()` — `status` is intentionally not touched, with a comment-block citing the deploy-gate-on-CI-green ordering. P0-CROSS-3: `ALTER TABLE compliance_bundles ADD CONSTRAINT no_synthetic_bundles CHECK (site_id NOT LIKE 'synthetic-%') NOT VALID` at lines 110-112 — exact name, exact predicate, NOT VALID clause as specified. Mental DB execution succeeds on a fresh run.

Two NEW non-blocking findings: (1) P2-NEW-1 the v3 narrative line 86 has a self-tautological typo (`corrected from 'mig:315' to 'mig:315'` — should read `'mig:311' to 'mig:315'`); doc-only, SQL is correct. (2) P2-NEW-2 the `ADD CONSTRAINT no_synthetic_bundles` has no `IF NOT EXISTS` (PG14+ doesn't support it for constraints anyway) — the §167 "Idempotency notes" line over-promises by implying ALTER-IF-NOT-EXISTS covers everything. Cosmetic — re-running a migration is not a supported workflow.

**Verdict: APPROVE.** Phase A (mig 315 + isolation filters + CI gates) may proceed. Carry P2-NEW-1 + P2-NEW-2 as commit-message cleanups, not blockers.

---

## v2 P0 closure matrix

| v2 P0 | Required action | v3 state | Closed? |
|---|---|---|---|
| P0-CROSS-1 (mig 311 → 315 renumber) | Zero stale `mig 311` refs in SQL; audit-log `target='mig:315'` | Grep returns 0 hits for `mig 311` or `mig:311` anywhere in file (4 narrative hits for "311 → 315" or §8 Q4 prose are legitimate historical citations, not stale refs). Line 156: `'mig:315'` confirmed in `INSERT INTO admin_audit_log`. | **YES** |
| P0-CROSS-2 (status-flip race) | UPDATE no longer sets `status='active'`; comment block explains injector-owned startup flip gated on CI green | Lines 100-103: `UPDATE sites SET synthetic = TRUE, updated_at = NOW() WHERE site_id = 'synthetic-mttr-soak';` — `status` absent. Lines 95-99 comment block cites P0-CROSS-2 fix + injector + deploy-verified-CI-green gating. | **YES** |
| P0-CROSS-3 (compliance_bundles CHECK) | ALTER ADD CONSTRAINT `no_synthetic_bundles CHECK (site_id NOT LIKE 'synthetic-%') NOT VALID` between §2 and §3 | Lines 110-112: `ALTER TABLE compliance_bundles ADD CONSTRAINT no_synthetic_bundles CHECK (site_id NOT LIKE 'synthetic-%') NOT VALID;` — exact match on constraint name + predicate + NOT VALID. Comment block at lines 105-109 cites Counsel Rule 2 PHI-boundary-as-compiler-rule rationale. | **YES** |

All 3 P0s closed.

---

## Empirical regex verification

### Probe 1: stale `mig 311` references

```
$ grep -n "status\s*=\s*'active'\|mig 311\|mig:311" \
       audit/substrate-mttr-soak-v2-design-2026-05-13.md
84:> - **P0-CROSS-2 (status-flip race):** mig no longer flips `status='active'`. Status STAYS at `'inactive'` ...
```

Single hit, in the v3 P0 narrative explicitly DOCUMENTING that `status='active'` is gone — NOT a residual SQL reference. **Inside the SQL block (lines 88-165): zero hits.** ✓

### Probe 2: new mig 315 + NOT VALID constraint

```
$ grep -n "no_synthetic_bundles\|NOT VALID\|mig 315\|mig:315" ...
81:### Migration design (mig 315 `substrate_mttr_soak_v2`, v3 P0 fixes applied)
85:> - **P0-CROSS-3 (compliance_bundles CHECK):** mig now lands `no_synthetic_bundles CHECK (site_id NOT LIKE 'synthetic-%') NOT VALID` ...
86:> - **P0-CROSS-1 (mig number 311 → 315):** already applied in Task #59 Commit 1 (audit-log reference at line 142 corrected from `'mig:315'` to `'mig:315'`).
105:--     site. NOT VALID defers the table-scan cost (zero existing synthetic
107:--     rows by §6.8 invariant) and enforces only on NEW writes. Counsel
111:    ADD CONSTRAINT no_synthetic_bundles
112:    CHECK (site_id NOT LIKE 'synthetic-%') NOT VALID;
156:    'mig:315',
312:1. Write mig 315 (§2).
345:6. **Gate A2:** ... (mig 315 applied + ...)
373:   ALTER TABLE compliance_bundles ADD CONSTRAINT no_synthetic_bundles
374:       CHECK (site_id NOT LIKE 'synthetic-%') NOT VALID;
422:| P2-2 (`system:mig-303` actor) | §2 — `jbouey2006@gmail.com` for mig 315 |
```

- Line 111-112: the §2b SQL block, exactly as specified. ✓
- Line 156: audit-log target `'mig:315'`. ✓
- Lines 373-374: §8 Q4 prose (open-question template — historical narrative explaining what to add; §2b is the answer). Acceptable as documentary drift.

---

## Mental DB execution trace (fresh-DB run)

```
BEGIN;
  ALTER TABLE sites ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE;
    -- adds column; existing rows get FALSE; safe.
  CREATE INDEX IF NOT EXISTS idx_sites_synthetic
      ON sites (synthetic) WHERE synthetic = TRUE;
    -- partial index; empty on fresh DB; safe.
  UPDATE sites SET synthetic = TRUE, updated_at = NOW()
   WHERE site_id = 'synthetic-mttr-soak';
    -- 0 or 1 rows (mig 303 created the row, mig 304 left it; mig 315 marks
    -- it synthetic). If mig 303 was rolled back, 0 rows — still safe.
    -- KEY: does NOT touch status. status stays 'inactive' (mig 304).
  ALTER TABLE compliance_bundles ADD CONSTRAINT no_synthetic_bundles
      CHECK (site_id NOT LIKE 'synthetic-%') NOT VALID;
    -- NOT VALID: deferred existing-row scan; enforces on NEW writes
    -- IMMEDIATELY (per PG14 docs, post-CREATE the check is active on
    -- INSERTs/UPDATEs even though VALIDATE has not run).
    -- compliance_bundles has zero synthetic-* rows by §6.8 invariant
    -- so VALIDATE will be trivial when it runs.
  CREATE TABLE IF NOT EXISTS substrate_synthetic_seeds (...);
    -- FK to sites(site_id), CHECK site_id LIKE 'synthetic-%'; clean.
  CREATE INDEX idx_synthetic_seeds_run ON substrate_synthetic_seeds (...);
  CREATE TABLE IF NOT EXISTS substrate_mttr_soak_runs_v2 (...);
  INSERT INTO admin_audit_log (username, action, target, details, created_at)
       VALUES ('jbouey2006@gmail.com', 'substrate_mttr_soak_v2_install',
               'mig:315', jsonb_build_object(...), NOW());
COMMIT;
```

**Result:** clean apply on a fresh DB. ✓

**Re-run hazard:** `ALTER TABLE ... ADD CONSTRAINT no_synthetic_bundles` will raise `42710 duplicate_object` on a second apply. PG14+ does NOT support `ADD CONSTRAINT IF NOT EXISTS` for CHECK constraints (only for columns + indexes). Mitigation in normal flow: `schema_migrations` table tracks applied migrations and prevents replay. P2-NEW-2 below carries this as a hygiene note, not a blocker — the project does not support re-running migrations as a workflow.

---

## 7-lens cross-check

### Steve (Engineering)
SQL is sound for PG14+. `ALTER ... ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS` all valid. Partial index `WHERE synthetic = TRUE` is correct (matches the canonical query shape). `gen_random_uuid()` requires pgcrypto, present per prior migrations. `jsonb_build_object` is fine for the audit-log INSERT. **VERDICT: clean.**

### Maya (Database)
`CHECK ... NOT VALID` syntax is correct PG14+. New writes are enforced immediately; pre-existing rows are exempt until `ALTER TABLE ... VALIDATE CONSTRAINT no_synthetic_bundles` runs. Because §6.8 invariant pins zero synthetic compliance_bundles, the eventual VALIDATE is O(scan-for-zero) — cheap. The new CHECK predicate `site_id NOT LIKE 'synthetic-%'` evaluates immediately on INSERT/UPDATE — so an audit-row INSERT in the same transaction that tried to write a synthetic-prefix `compliance_bundles` row would fail. Audit-log target `mig:315` matches sibling mig 314's pattern of citing the mig number in `target`. **VERDICT: clean. Note re-run hazard as P2-NEW-2.**

### Carol (Security)
Status-flip ordering verified:
- Sites table: `synthetic = TRUE, status = 'inactive'` post-mig 315.
- Any callsite `FROM sites WHERE status != 'inactive'` → safe (excludes synthetic).
- Any callsite `FROM sites` (no status filter) is the risk class — `_check_l2_resolution_without_decision_record` IS such a query but is intentionally allowed to fire on synthetic (substrate engine carve-out per §4 step 3).
- Customer-facing surfaces all filter `status != 'inactive'` today and will ADDITIONALLY filter `synthetic = FALSE` per §4 Phase A (defense in depth).
- The injector flips `status` to `'active'` at run-start, gated on the new `synthetic = FALSE` filter code being live in prod and verified. Backend deploy-lag window structurally closed: filter ships → CI green → operator verifies → injector starts. **VERDICT: closed.**

`compliance_bundles` CHECK is the load-bearing PHI-boundary-as-compiler-rule per Counsel Rule 2 — even if some future endpoint mis-routes a synthetic seed into the evidence table, the DB rejects it. **VERDICT: closed.**

### Coach (Scope-creep check)
Only the 3 targeted P0 edits applied. No new sections, no new tables, no scope drift. Comments in mig SQL (lines 95-99, 105-109) cite the P0 fix labels and rationale — appropriate level of in-SQL documentation. **VERDICT: clean focused patches.**

### Auditor (OCR — sibling-pattern consistency)
Audit-log INSERT at lines 152-162 matches sibling mig 314 schema: `(username, action, target, details, created_at)`. `username = 'jbouey2006@gmail.com'` (named human, per CLAUDE.md privileged-chain rule, satisfies P2-2 closure). `target = 'mig:315'` matches the convention. `details` is a `jsonb_build_object` with `supersedes` + `design_doc` keys — auditor-traceable. **VERDICT: clean.**

### PM
3 P0s applied as small targeted edits (~15min total in design-doc time). Implementation cost for Phase A unchanged. No new tasks generated. **VERDICT: tracked.**

### Counsel (in-house)
Counsel Rule 2 (no raw PHI / customer data crosses the appliance boundary, schema-enforced as compiler rule) is **materially satisfied** by the new `no_synthetic_bundles` CHECK. The constraint name `no_synthetic_bundles` is self-documenting. The predicate `site_id NOT LIKE 'synthetic-%'` correctly hides behind the namespace-prefix convention used everywhere else in the substrate (synthetic-mttr-soak, synthetic-* future). NOT VALID is acceptable because the §6.8 invariant pins zero pre-existing synthetic compliance_bundles. **VERDICT: Rule 2 closure achieved.**

---

## NEW findings (this Gate A v3 cycle)

### P2-NEW-1 — narrative self-tautology at line 86 (doc-only)

> Line 86: `(audit-log reference at line 142 corrected from `'mig:315'` to `'mig:315'`).`

Both sides of the "corrected from X to Y" are the same string `'mig:315'`. The intent was clearly `'mig:311' to 'mig:315'`. SQL is correct (line 156 has `'mig:315'`). **Fix:** edit narrative line 86 only. Not a blocker — the SQL truth is right.

### P2-NEW-2 — §167 "Idempotency notes" over-promises

> Line 167: `**Idempotency notes:** \`ALTER TABLE ... IF NOT EXISTS\`, \`CREATE TABLE IF NOT EXISTS\`, the \`UPDATE sites\` is keyed by \`site_id\` so it's a no-op on replay.`

Doesn't mention that `ALTER TABLE compliance_bundles ADD CONSTRAINT` is NOT idempotent — PG14+ disallows `ADD CONSTRAINT IF NOT EXISTS`. Replay would fail at the constraint step. Project doesn't support migration replay (schema_migrations tracks applied state), so cosmetic only. **Fix options:** (a) wrap the constraint add in a `DO $$ ... IF NOT FOUND THEN ... $$` block; (b) update §167 to note the constraint is not replay-safe and rely on schema_migrations. (a) is cleaner; (b) is honest. Defer to Phase A author.

### P2-NEW-3 — Appendix A line 390 stale filename

> `mcp-server/central-command/backend/migrations/311_substrate_mttr_soak_v2.sql | NEW`

Filename should be `315_substrate_mttr_soak_v2.sql` to match the renumber. Doc-only.

### §8 Q4 prose is now obsolete (line 371-376)

The "Open question — should v2 include this CHECK?" prose at §8 Q4 was the v2 author's open question. v3 answered it (YES, in §2b). The §8 Q4 prose now reads stale. Suggest editing §8 Q4 to: "**RESOLVED in v3: shipped as §2b.**" Doc-only.

---

## NO P0/P1 findings

The 3 v2 P0s are materially closed by the SQL changes. The 5 v2 P1s remain explicitly deferred per the brief (Phase B/C/D scope). No new blocking issues identified.

---

## Final verdict

**APPROVE.**

Phase A (mig 315 itself + isolation filters at the 5+ callsites + CI gates `test_synthetic_site_filter_universality.py` + `test_auditor_kit_refuses_synthetic_site.py`) is unblocked for implementation. Phase B (injector + analyzer rewrite) and Phase C/D/E remain on the staged plan with their own per-phase gates (per Session 220 two-gate rule — Phase C ends with Gate A2 + Phase E ends with Gate B before close-out).

**Pre-commit cleanup items (P2, optional but recommended):**
- P2-NEW-1: Edit line 86 narrative tautology — replace first `'mig:315'` with `'mig:311'`.
- P2-NEW-2: Either wrap §2b constraint add in `DO $$ ... $$` or update §167 idempotency note to disclose the constraint-add non-idempotency.
- P2-NEW-3: Update Appendix A filename to `315_substrate_mttr_soak_v2.sql`.
- §8 Q4: mark RESOLVED, point to §2b.

None of the above block Phase A execution. All 4 are eligible to ship in the same commit as the mig 315 file itself or in a follow-up doc-only commit.

---

**Gate B reminder (Session 220 two-gate rule):** before declaring Phase A complete, run a Gate B fork over the AS-IMPLEMENTED artifacts — mig 315 file as written, the diff at the 5+ filter callsites, the CI gate test output, and the local pre-push full-sweep result. Gate B verdict file: `audit/coach-substrate-mttr-soak-phase-a-gate-b-2026-05-13.md`.
