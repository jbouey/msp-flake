# Gate B verdict — mig 314 canonical_metric_samples (Task #50 Phase 2a)

**Date:** 2026-05-13
**Commit under review:** `56d14e22` (mig 314) + `fdcd1cdd` (orthogonal Task #59 followup)
**Gate A reference:** `audit/coach-canonical-compliance-score-drift-v3-patched-gate-a-2026-05-13.md` (APPROVE v4)
**Design doc:** `audit/canonical-metric-drift-invariant-design-2026-05-13.md`
**Verdict:** **APPROVE**

---

## 200-word summary

Phase 2a ships clean. The mig 314 SQL is a single `BEGIN/COMMIT` transaction with a `PARTITION BY RANGE (captured_at)` parent table, three monthly partitions (2026-05/06/07), and two indexes — one general (tenant + recency) and one partial (`WHERE classification = 'customer-facing'`). The partial-index-on-partitioned-table pattern has known-good precedent in this codebase: mig 138 `compliance_bundles` ships `idx_compliance_bundles_ots_pending WHERE ots_status = 'pending'` against the same shape since Session 200. PG14+ propagates the partial index to all current and future child partitions automatically — no per-partition DDL required. The `admin_audit_log` INSERT uses `NULL` user_id legally (`user_id UUID REFERENCES admin_users(id)`, no `NOT NULL`); column order matches sibling mig 313 byte-for-byte. RESERVED_MIGRATIONS.md no longer carries a row for 314 (lifecycle rule executed). prod_columns.json carries all 9 columns alphabetically. Design doc marker swapped to `<!-- mig 314 SHIPPED 2026-05-13 -->` at line 41. Pre-push full-sweep run: 245/245 pass. No design deviation detected. No P0 or P1 findings. Phase 2b/2c/2d (sampler decorator, invariant, pruner) ship under their own Gate A/B per design.

---

## Per-lens verdict

### 1. Engineering (Steve) — APPROVE

Read 314_canonical_metric_samples.sql line-by-line (82 lines, including comments).

- **Single BEGIN/COMMIT:** line 16 `BEGIN;`, line 81 `COMMIT;`. No nested transactions, no implicit autocommit traps.
- **Column types (design §2 vs as-shipped):**
  | Field | Design | Shipped | Match |
  |---|---|---|---|
  | sample_id | UUID PK | UUID NOT NULL DEFAULT gen_random_uuid() | ✓ |
  | metric_class | TEXT | TEXT NOT NULL | ✓ |
  | tenant_id | UUID | UUID NOT NULL | ✓ |
  | site_id | TEXT NULL | TEXT NULL | ✓ |
  | captured_at | TIMESTAMPTZ | TIMESTAMPTZ NOT NULL DEFAULT NOW() | ✓ |
  | captured_value | NUMERIC(5,1) NULL | NUMERIC(5,1) NULL | ✓ |
  | endpoint_path | TEXT | TEXT NOT NULL | ✓ |
  | helper_input | JSONB NULL | JSONB NULL | ✓ |
  | classification | TEXT (enum) | TEXT NOT NULL + CHECK | ✓ |
- **CHECK constraint syntax:** `CHECK (classification IN ('customer-facing', 'operator-internal', 'partner-internal'))` — valid PG enum-via-CHECK pattern.
- **PRIMARY KEY:** `(sample_id, captured_at)` — partition key MUST be part of every unique constraint on a partitioned table (PG14+ rule). Correct.
- **Partition coverage:** 3 monthly partitions for 2026-05/06/07 cover the next ~80 days. Phase 2d pruner explicitly noted as separate task.
- **Indexes:** both `IF NOT EXISTS`; the partial index physically excludes operator-internal rows from scan — matches Carol's defense-in-depth req.

No issues.

### 2. Database (Maya) — APPROVE

Critical question: **Does PG allow CREATE PARTIAL INDEX inside the same transaction that created the partitioned parent + 3 children?**

Answer: **YES.** PG14+ propagates the partial-index predicate to every current child partition automatically AND to all future partitions attached via `ATTACH PARTITION`. Verified in-codebase precedent: `migrations/138_partition_large_tables.sql` ships `CREATE INDEX IF NOT EXISTS idx_compliance_bundles_ots_pending ON compliance_bundles(ots_status) WHERE ots_status = 'pending'` against `compliance_bundles` (PARTITION BY RANGE) — production-live since Session 200, ~232K rows, no operational issues. Mig 314 uses the identical shape.

Additional Maya checks:

- **NULL user_id in admin_audit_log INSERT:** `008_admin_auth.sql:` schema is `user_id UUID REFERENCES admin_users(id)` — **no `NOT NULL`**. Pattern identical to mig 313 (shipped 2 commits prior), mig 148, mig 300, mig 308. Legal.
- **`NULL` ip_address:** column is `ip_address VARCHAR(45)` — nullable, no constraint violation. Legal.
- **`gen_random_uuid()`:** requires `pgcrypto` or PG13+ built-in. VPS is PG14, confirmed available (used in mig 138, 191, 313).
- **Partition-key in PK constraint:** correct per PG rule; would FAIL with `unique constraint on partitioned table must include all partitioning columns` if violated. Shipped form complies.
- **CONCURRENTLY NOT used:** correct — CREATE INDEX CONCURRENTLY cannot run inside BEGIN/COMMIT (per CLAUDE.md feedback rule); since this is a fresh empty table, regular CREATE INDEX is appropriate and fast.

No issues.

### 3. Security (Carol) — APPROVE

- **File location:** `mcp-server/central-command/backend/migrations/314_canonical_metric_samples.sql` — correct directory; CI migration-loader picks up `NNN_*.sql` shape.
- **RESERVED_MIGRATIONS.md row removed:** grepped — no `| 314 |` row present. Confirmed lifecycle rule executed in the same commit. Pre-fix ledger had row for 314 reserving for Task #50 P-F9 v1 — the P-F9 design renumbered to 317/318 (rows present in ledger), and Task #50 won 314 per Gate A v4.
- **Audit row payload (line 71-77):** contains `migration`, `task`, `counsel_rule`, `design_doc`, `gate_a_verdict` — all are file paths or labels, no secrets, no credentials, no PHI. Safe.
- **Defense-in-depth count:** 3 layers verified (CHECK + partial-index + invariant SQL WHERE clause noted in design Phase 2c). Carol's Gate A v3 ask satisfied.
- **PHI boundary:** `helper_input JSONB` — design specifies this is the canonical-helper arg shape (site_id list, window_days). No PHI fields. Phase 2b sampler decorator (separate ship) enforces by scrubbing helper-arg inputs before insert; the column itself is plain JSONB with no privileged-write trigger needed since the substrate-internal sampler is the only writer.

No issues.

### 4. Coach — APPROVE

Pattern parity vs `312_baa_signatures_acknowledgment_only_flag.sql`:

| Aspect | mig 312 | mig 314 | Parity |
|---|---|---|---|
| Single BEGIN/COMMIT | ✓ | ✓ | ✓ |
| Header comment block | ✓ | ✓ | ✓ |
| Counsel-rule citation | Yes (TOP-PRIORITY P0) | Yes (Rule 1 gold) | ✓ |
| Design-doc citation | Yes | Yes | ✓ |
| Gate A verdict citation | Yes | Yes | ✓ |
| `IF NOT EXISTS` everywhere | ✓ | ✓ | ✓ |
| admin_audit_log INSERT | (n/a — different shape) | Yes (identical to mig 313) | ✓ |

Pattern parity vs `313_d1_heartbeat_verification.sql` (audit-row block): **byte-identical column order** `user_id, username, action, target, details, ip_address` — same INSERT shape, same NULL/NULL bookends, same jsonb_build_object payload structure.

**Test fixture (prod_columns.json):** read offsets 722-732 → 9 columns present in alphabetical order:
```
captured_at, captured_value, classification, endpoint_path,
helper_input, metric_class, sample_id, site_id, tenant_id
```
Sort order verified `c < c < c < e < h < m < s < s < t`. Matches schema fixture alphabet rule from sibling tables (admin_audit_log at offset 2-11 same pattern).

**Design doc marker swap (line 41):**
```
<!-- mig 314 SHIPPED 2026-05-13 — see migrations/314_canonical_metric_samples.sql -->
```
Old `<!-- mig-claim:314 task:#50 -->` literal is GONE per grep. Lifecycle rule honored end-to-end (ledger row removed + design marker swapped + on-disk SQL is post-ship authority).

No issues.

### 5. Auditor (OCR) — N/A

Substrate-internal infrastructure. No customer-visible surface, no §164.528 disclosure-accounting impact, no auditor-kit content change. Phase 2b/2c will surface customer-facing drift alerts — those phases will own auditor-relevant Gate B. Confirmed N/A for Phase 2a.

### 6. PM — APPROVE

- **Commit order:** `56d14e22` (mig 314) precedes `fdcd1cdd` (orthogonal Task #59 Gate B verdict followup — doesn't touch mig 314, doesn't gate it). Order correct.
- **Commit body:** matches design — 4-phase plan acknowledged, sampler/invariant/pruner explicitly deferred, 4 Gate A iterations cited.
- **Pre-push sweep:** ran `bash .githooks/full-test-sweep.sh` — **245 passed, 0 skipped**. CI parity confirmed. Per Session 220 lock-in: Gate B sweep mandatory and executed.
- **Task #50:** still in_progress (Phase 2a only; 2b/2c/2d remain). Phase tracking accurate.

No issues.

### 7. Attorney (in-house counsel) — N/A

SQL DDL only; no customer-facing artifact, no BAA-claim change, no opaque-mode surface. The counsel-rule citation in the file header (Rule 1 gold authority) is documentation, not a legal claim — captures *why* this infra exists, not what we tell customers. Confirmed N/A.

---

## AS-IMPLEMENTED vs DESIGN — deviation matrix

| Element | Design (v3 + Gate A v4) | As-shipped (commit 56d14e22) | Deviation |
|---|---|---|---|
| Migration number | 314 | 314 | none |
| Table name | canonical_metric_samples | canonical_metric_samples | none |
| Column count | 9 | 9 | none |
| Column names | sample_id, metric_class, tenant_id, site_id, captured_at, captured_value, endpoint_path, helper_input, classification | identical | none |
| PRIMARY KEY | (sample_id, captured_at) | (sample_id, captured_at) | none |
| Partition by | RANGE(captured_at) | RANGE(captured_at) | none |
| Initial partitions | 3 monthly (May/Jun/Jul 2026) | 3 monthly (May/Jun/Jul 2026) | none |
| Indexes | 2 (general + partial classification='customer-facing') | 2 (identical) | none |
| CHECK constraint | classification IN 3 values | identical | none |
| admin_audit_log entry | YES | YES (NULL user_id, 'system' username) | none |
| Ledger row removed | YES (lifecycle rule) | YES (verified by grep) | none |
| Design marker swapped | YES (lifecycle rule) | YES (line 41 `SHIPPED 2026-05-13`) | none |
| Fixture updated | YES | YES (prod_columns.json offsets 722-732) | none |

**Net deviation: zero.** AS-SHIPPED is a verbatim materialization of design v3 + Gate A v4.

---

## SQL mental-execution trace

```
1. BEGIN;
2. CREATE TABLE IF NOT EXISTS canonical_metric_samples (...)
     PARTITION BY RANGE (captured_at);
   → table not in catalog; created. PK (sample_id, captured_at) attached.
   → CHECK constraint canonical_metric_samples_classification_valid attached.
3. CREATE TABLE IF NOT EXISTS canonical_metric_samples_2026_05
     PARTITION OF canonical_metric_samples FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
   → child partition created; inherits parent column set + PK constraint structure.
4. CREATE TABLE IF NOT EXISTS canonical_metric_samples_2026_06 (...);
   → same shape, ranges 2026-06-01 to 2026-07-01. No range overlap with #3.
5. CREATE TABLE IF NOT EXISTS canonical_metric_samples_2026_07 (...);
   → same shape, ranges 2026-07-01 to 2026-08-01. No range overlap.
6. CREATE INDEX IF NOT EXISTS idx_canonical_metric_samples_tenant
     ON canonical_metric_samples (tenant_id, metric_class, captured_at DESC);
   → PG propagates index DDL to all 3 child partitions automatically.
7. CREATE INDEX IF NOT EXISTS idx_canonical_metric_samples_drift
     ON canonical_metric_samples (metric_class, classification, captured_at DESC)
     WHERE classification = 'customer-facing';
   → partial index propagates to all 3 children (PG14+ semantic, precedent: mig 138).
8. INSERT INTO admin_audit_log (user_id, username, action, target, details, ip_address)
     VALUES (NULL, 'system', 'canonical_metric_samples_table_created', 'canonical_metric_samples',
             jsonb_build_object(...), NULL);
   → user_id NULL legal (no NOT NULL on schema). created_at filled by DEFAULT NOW().
9. COMMIT;
   → all 9 statements atomic. No statement can fail given the schema + precedent verification.
```

**Failure modes considered + dismissed:**
- `gen_random_uuid()` missing → PG14 built-in, available on VPS.
- Partition range overlap → checked manually: `[2026-05-01, 2026-06-01) [2026-06-01, 2026-07-01) [2026-07-01, 2026-08-01)` — half-open intervals, no overlap.
- Partial index on partitioned table forbidden → false on PG14+; precedent mig 138.
- admin_audit_log NOT NULL violation → schema has no NOT NULL on user_id/ip_address.
- ON CONFLICT on partitioned table → not used (no ON CONFLICT clause).

**Trace result: clean — every statement succeeds.**

---

## Pre-push sweep evidence

```
$ bash .githooks/full-test-sweep.sh
✓ 245 passed, 0 skipped (need backend deps)
```

CI-parity sweep complete. No regressions. Per Session 220 lock-in mandate satisfied.

---

## Final verdict

**APPROVE.**

Phase 2a is clean as-shipped. Zero deviation from design. SQL is sound for PG14+, partial-index-on-partitioned-table pattern has in-codebase precedent (mig 138), admin_audit_log INSERT matches sibling mig 313 byte-for-byte, lifecycle rules (ledger row removal + design marker swap) executed correctly, fixture updated in alphabetical order, pre-push sweep is 245/245 green.

**Next gates (separate Gate A/B each per design):**
- Phase 2b — sampler decorator (Python middleware)
- Phase 2c — substrate invariant `canonical_compliance_score_drift`
- Phase 2d — partition pruner (DETACH+DROP old, CREATE ahead)

No P0 or P1 findings to carry. No follow-up tasks required for Phase 2a closure.

— Gate B fork, 2026-05-13
