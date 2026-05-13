# Gate A v2 — Load-Harness v2 Adversarial Review

**Design under review:** `audit/load-harness-v2-design-2026-05-13.md`
**Date:** 2026-05-13
**Reviewers:** Steve (Engineering), Maya (Database), Carol (Security), Coach (Consistency), Auditor (OCR), PM, Attorney
**Verdict:** **BLOCK — 2 P0s (MIGRATION-NUMBER COLLISION + LOAD-HARNESS-SHARES-PROD-DAEMON-BEARER-PATH) + 5 P1s.** Resolvable with focused revision; recommend v2.1 reissue with mig 311→315 renumber and Carol's bearer-class clarification before any code lands.

---

## 250-Word Summary

v2 design is a substantial step up from v1. The author's three claimed P0
closures are technically sound: (P0-1) all 8 Wave-1 endpoint paths are
grep-verified against the live router decorators
(`agent_api.py:362,1878,2069,3036`, `journal_api.py:71` with `/api/journal`
prefix, `device_sync.py:1088` with `/api/devices` prefix, `log_ingest.py:57`
with `/api/logs` prefix); (P0-2) `compliance_bundles` is no longer in the
write path; (P0-3) the synthetic-marker generalization is the correct
mechanism for unifying with plan-24.

**However, Gate A blocks on two new P0 findings.** P0-A: the design claims
migration number 310, but `310_close_l2esc_in_immutable_list.sql` is already
SHIPPED in main as of `4b9b6d35` window. The next free number is
**311** (gap between shipped 310 and shipped 312/313). This is the same
collision class that has blocked tasks #50, #98, P-F9 today. P0-B: §8 Q3's
"Vault-Transit-issued dedicated bearer" creates a new privileged-credential
class (signing-capable Ed25519 keyed to a real `site_appliances` row) that
itself needs explicit registration in the privileged-chain four-list lockstep
(`fleet_cli.PRIVILEGED_ORDER_TYPES` / `ALLOWED_EVENTS` / immutable function
list / Python-only allowlist) — or an explicit carve-out attestation. The
design treats this as a Carol-decision Q3 but it's actually a Counsel-Rule-3
constraint that must be resolved in-design.

Five P1s span isolation-leak risk, alertmanager pattern reuse, fixture
ratchet edge cases, sequencing, and a Coach finding on double-build risk vs.
MTTR-soak-v2.

---

## Per-Lens Verdicts

### 1. Engineering (Steve) — APPROVE-WITH-FIXES
**Wave-1 endpoint verification** (grep-confirmed 2026-05-13):
- `agent_api.py:362` `@router.post("/checkin")` ✓
- `agent_api.py:1878` `@router.post("/api/agent/sync/pattern-stats")` ✓
- `agent_api.py:2069` `@router.post("/api/agent/executions")` ✓
- `agent_api.py:3036` `@router.post("/api/appliances/checkin")` ✓
- `journal_api.py:71` `@journal_api_router.post("/upload")` (prefix `/api/journal`) ✓ → final path `/api/journal/upload`
- `device_sync.py:1088` `@device_sync_router.post("/sync")` (prefix `/api/devices`) ✓ → final path `/api/devices/sync`
- `log_ingest.py:57` `@router.post("/ingest")` (prefix `/api/logs`) ✓ → final path `/api/logs/ingest`
- `/health` — infrastructure baseline, labeling correct.

All 8 paths real. **P0-1 closed.**

**k6 + goja for 30-clinic sim is realistic.** Empirical VU ceiling math
(~500 VUs/2vCPU) is correct for goja's GC behavior. CX22→CX32 two-stage
hardware sizing is pragmatic; not pre-committing the CX32 spend is correct
business-hygiene.

**P1-S1 (P1):** §2 line 67 claims `/api/appliances/checkin` cadence is
"every 60s × N appliances → 100 req/min for 100-appliance fleet." Math
is wrong: 100 appliances × (1 checkin / 60s) = 100 req/**60s** = **1.67
req/s ≈ 100 req/min** is actually correct on rereading. **Withdrawn —
math is fine; the unit confusion was reviewer-side.**

**P1-S2 (P1):** Phase 1 §7 pass criterion "0 5xx at 100 req/min" is too
generous. Production p95 baselines on `/checkin` are not given in the design
— without a pre-test baseline, "p95 < 200ms" is an arbitrary number. Add a
Phase 0 step: measure current real-customer-probe p95 on each Wave-1 endpoint
for 24h pre-run, and define Phase 1 SLAs as baseline + delta, not absolute.

### 2. Database (Maya) — APPROVE-WITH-FIXES (CONDITIONAL ON RENUMBER)
**Mig 310 collision — P0-M1.** Migration directory listing 2026-05-13:
```
310_close_l2esc_in_immutable_list.sql  (SHIPPED — Carol P1 from
                                         coach-enterprise-readiness-2026-05-12)
312_baa_signatures_acknowledgment_only_flag.sql  (SHIPPED)
313_d1_heartbeat_verification.sql  (SHIPPED)
```
**311 is the next free slot.** v2 design's "mig 310" is dead-on-arrival.
Cross-cutting context confirms 4 designs collide in the 310s today
(#50 mig 314, #98 originally 311, P-F9 originally 314+315, this one 310).
**A central renumber authority is missing** — the migrations directory needs
a `RESERVED_MIGRATIONS.md` ledger or a CI gate that fails if any in-progress
design's mig number is already shipped.

**JSONB transition shape during partial-rollout window** (P1-M1): The mig
310 (v2's number, to be renumbered) backfill `details = details - 'soak_test'
|| jsonb_build_object('synthetic', 'mttr_soak')` is single-statement and
atomic per row. **But:** plan-24's substrate engine still writes
`soak_test='true'` between migration apply and Phase 0 deploy (whichever
ships second). During that window, the partial index
`idx_incidents_synthetic` exists but the engine writes the old key. **Fix
required:** add to mig (renumbered) a forward-compat trigger
`BEFORE INSERT ON incidents` that rewrites `details->>'soak_test'='true'`
→ `details->>'synthetic'='mttr_soak'` for 30 days, then drop. OR ship the
plan-24 v2 code change in the same git push as the migration.

**Site-rename interaction** (P1-M2): The design states "site_id unchanged
for chain idempotency — same well-known UUID" — correct. But the
`clinic_name` UPDATE on `sites` does NOT go through `rename_site()` (it's
not a site_id rename, just a column update), so it's safe under the
"site rename is multi-table" rule. **Confirm** the `audit_log` row in mig
step 5 captures the old clinic_name for traceability.

**Index DROP+CREATE** (Maya P0 prior class) is non-CONCURRENTLY in v2's
sketch — fine inside a `BEGIN`/`COMMIT` block IF the table is small.
`incidents` is 1.2M+ rows (per recent prometheus_metrics output). **Fix:**
either use `CREATE INDEX CONCURRENTLY` (move out of the txn — see CLAUDE.md
"CREATE INDEX CONCURRENTLY = single-statement file" rule) or accept a
~30s table-lock at migration apply. Recommend CONCURRENTLY split into a
follow-on migration.

**substrate_mttr_soak_runs → synthetic_runs** rename: `ALTER TABLE
... RENAME TO` cascades all FKs/indices correctly in Postgres ≥ 10.
Add a `_pg.py` fixture that asserts both names resolve to the same OID
during the window any in-flight transaction references the old name —
shouldn't matter, but worth pinning.

### 3. Security (Carol) — BLOCK
**P0-C1: Bearer storage is a privileged-credential class.** §8 Q3 punts
"Vault-Transit-issued dedicated bearer keyed to synthetic-load-and-soak" as
a decision question, but it's an architectural requirement. A bearer that
authenticates as a `site_appliances` row CAN sign `/api/agent/executions`
rows that write `execution_telemetry`, `incidents`, etc. The bearer is
indistinguishable from a production daemon bearer at the auth layer. **Two
required gates before Phase 0:**
1. The bearer issuance MUST register in the privileged-chain lockstep
   (`fleet_cli.PRIVILEGED_ORDER_TYPES` does NOT need it because it's not a
   fleet-order; but `ALLOWED_EVENTS` SHOULD gain a `load_test_bearer_issued`
   entry so the issuance writes an attestation row).
2. The bearer MUST be scoped at the auth layer to the synthetic site
   (already implied — but make it an explicit DB CHECK constraint:
   `bearer_revoked_at` cannot be NULL for site_id != 'synthetic-load-and-soak'
   bearers issued via the load-test path).

**P0-C2 (related): "site_appliances.bearer_revoked_at" new column** in §8 Q3
intersects with site_appliances' existing schema. site_appliances is in the
immutable-tables list for `rename_site()` (CLAUDE.md confirms). Adding a
column is fine; but per the "schema-evolution" guard the migration must
add a partial index `WHERE bearer_revoked_at IS NOT NULL` (kept small) AND
the `auth.py` check must use `execute_with_retry()` per the PgBouncer rule.

**Three-layer kill-switch alertmanager pattern reuse** (P1-C1): §6 Phase 0
introduces alert rule `load_test_5xx_storm` that flips kill flag via webhook.
This MUST reuse the same pattern as `SUBSTRATE_ALERT_SOAK_SUPPRESS` env-flag
(see `alertmanager_webhook.py:122`). The design doesn't mention the env-flag
inheritance — without it, the webhook needs a new authentication path. Fix:
add to design "load-test alerts use the same `labels.synthetic` allowlist
filter as soak alerts; the webhook env-flag becomes
`SUBSTRATE_ALERT_SYNTHETIC_SUPPRESS` covering both classes."

**Production endpoint hit-with-synthetic-site-id risk** (P1-C2): Header
+ auth-site marker injection is a two-key gate (design §3 lines 122-128).
But the design doesn't address: what happens if a real production daemon
accidentally sets `X-Synthetic-Run: load_test` (e.g. via header injection
through a proxy)? Add explicit Carol fix: the header is accepted ONLY when
auth-site = 'synthetic-load-and-soak'; for ANY other auth-site, the header
is logged at WARN, dropped, and 400 returned. Pin this in a new gate
`test_synthetic_header_only_for_synthetic_site.py`.

### 4. Coach (Consistency) — BLOCK
**P0-CH1: Double-build risk vs. MTTR-soak-v2.** Task #98 (MTTR soak v2)
is in_progress per the cross-cutting context. The design at §8 Q5
acknowledges sequencing dependency but does NOT call out the risk that
MTTR-soak-v2 may independently propose a `sites.synthetic BOOLEAN` column
(per the original task framing — confirmed in task-list line 98). If both
designs land independently, the load-harness has `details->>'synthetic'` AND
the MTTR-soak-v2 has `sites.synthetic` — two parallel infrastructures, the
exact thing P0-3 was supposed to prevent.

**Fix:** v2 design MUST explicitly take ownership of the synthetic-marker
unification across BOTH classes. Either (a) commit that MTTR-soak-v2 will
inherit mig (renumbered) rather than introducing `sites.synthetic`, or (b)
move the marker to a `sites.synthetic_class` column and have both designs
write to it. Recommend (a) — fewer migrations, fewer query-shape changes.

**P1-CH1: §8 Q6 says Gate B must run full pre-push sweep (Session 220
lock-in).** Good. **Add:** Gate B must ALSO verify that mig (renumbered)
applies cleanly against a fresh DB AND against a DB with plan-24 mig 303+304
already applied (forward-from-plan-24 path). The Gate B verdict file MUST
cite the apply-success on both paths.

### 5. Auditor (OCR) — APPROVE-WITH-FIXES
**P1-A1 — synthetic-data auditor isolation evidence:** The design has
the right elements (status='inactive' quarantine, isolation marker, separate
synthetic site_id), but the AUDITOR-FACING evidence story is implicit. Add
a §"Audit trail for load-test runs" section:
- Each Phase 1/Phase 2 run writes a `load_test_run` row to `admin_audit_log`
  with `run_id, started_at, ended_at, target_endpoints[], peak_rps,
  total_requests, capacity_number_published`.
- The `synthetic_runs` table (post-rename) keeps the row-level ledger.
- Auditor-kit walks SKIP synthetic-marker rows entirely (already-true
  via mig 304 status='inactive' + universal filter test).
- The "≥30 simultaneous clinics" published number MUST be backed by a
  signed evidence bundle (Ed25519, NOT in compliance_bundles — write a
  new `capacity_claims` table) so the sales artifact has cryptographic
  provenance, not a screenshot.

**P2-A1: "30 clinics simultaneously" auditability —** counsel-rule check:
the number itself is fine but the methodology behind it (Scenario C
inflection × 0.7 safety margin) MUST be documented in the public sales
artifact, not hidden behind "we tested it." Three lines of methodology
disclosure plus the run timestamp.

### 6. PM — APPROVE
Phasing realistic: Phase 0 (1d) → Phase 1 (1.5d) → Phase 2 (24h soak, only
if Phase 1 justifies) is well-paced. Cost discipline is good (CX22 €4/mo
default, CX32 €8/mo only if needed = max €12/mo for production-grade
characterization).

**P2-PM1 (P2):** Add explicit "abort criteria" for each Phase — when does
the operator say "Phase 1 is good enough, skip Phase 2"? Today the design
implies it but doesn't define it. Recommend: if Phase 1 publishes "≥30
simultaneous clinics measured" AND real-customer-probe degradation < 50ms
delta, Phase 2 is OPTIONAL not REQUIRED.

### 7. Attorney (Counsel) — APPROVE-WITH-FIXES
**Banned-word check on "≥30 simultaneous clinics":** the word "clinics" is
fine. The framing "we can take ≥30 simultaneous" risks an implied
guarantee. Counsel-safe rewrite: "**measured capacity for 30+ concurrent
clinic onboarding workflows under synthetic load on current infrastructure
(see methodology in <link>)**." NEVER say "supports 30+ clinics" without
the "measured under synthetic load" qualifier — that crosses into capability
claim territory which under Rule 10 (no clinical authority implications)
and Rule 1 (canonical-source registry) requires a canonical source declaration.

**Counsel Rule 1 trigger:** the "≥30 simultaneous clinics" published number
IS a customer-facing metric per Rule 1. Per task #50 (canonical-source
registry, in_progress, mig 314), this number MUST be registered. The v2
design SHOULD reference task #50 and pre-register the metric as
`load_capacity.simultaneous_clinic_max` with source =
`synthetic_runs WHERE run_type='load_test' AND status='completed' ORDER BY ended_at DESC LIMIT 1`.

**Counsel Rule 2 (no PHI across appliance boundary):** load test uses
synthetic data only, generates no PHI — clean.

---

## v1 P0/P1 Closure Matrix

| v1 finding | v2 closure | Verdict |
|---|---|---|
| **P0-1** Fake endpoint paths (`/api/checkin`, `/api/heartbeat`) | §2 grep-verified 8 canonical paths; new CI gate `test_load_harness_path_freshness.py` | **CLOSED** |
| **P0-2** Writing to `compliance_bundles` | `/evidence/upload` dropped from Wave 1; chain-anchored tables never touched | **CLOSED** |
| **P0-3** Parallel synthetic infrastructure duplicating plan-24 | Mig (renumbered) unifies marker; `synthetic-mttr-soak` site renamed; `substrate_mttr_soak_runs` renamed to `synthetic_runs`; single CI gate supersedes plan-24's | **CLOSED (conditional on renumber + Coach P0-CH1 ownership clarification with MTTR-soak-v2)** |
| **P1-1** 4 missed high-volume bearer endpoints | §2 added `/api/agent/executions`, `/api/devices/sync`, `/api/agent/sync/pattern-stats`, `/api/logs/ingest`, `/api/journal/upload` (5 added — exceeds ask) | **CLOSED** |
| **P1-2** No kill-switch | §6 three-layer kill-switch + AlertManager auto-trip | **CLOSED** (P1-C1 carryforward on alertmanager pattern reuse) |
| **P1-3** Cost unbounded | §5 CX22 (€4) → CX32 (€8) two-stage hardware decision | **CLOSED** |
| **P1-4** No real-customer probe SLA | §6 soft (+100ms 5min) / hard (+500ms 2min) probe + auto-abort | **CLOSED** |
| **P1-5** Bearer rotation underspecified | §8 Q3 four-point spec (Vault Transit, 1Password, 30d rotation, bearer_revoked_at column) | **PARTIAL — escalated to P0-C1 (privileged-class registration)** |

**Net v1 closures:** 7 of 8 fully closed; 1 escalated to a new P0.

---

## Cross-Lens Findings

### P0 (block — fix before any code lands)
1. **P0-A / Maya / Coach — Migration number collision.** Renumber mig 310 → **mig 315** (or whatever is next-free at design-publish time; coordinate with task #50 at mig 314 + #98 + P-F9). Add a `RESERVED_MIGRATIONS.md` ledger to the migrations directory in the same PR. Until renumber, the SQL block on lines 184-221 of the design is dead-on-arrival.

2. **P0-B / Carol — Load-test bearer is a privileged credential class needing chain registration.** Either:
   - (a) Add `load_test_bearer_issued` to `ALLOWED_EVENTS` and write an attestation row on issuance; OR
   - (b) Add an explicit design-document carve-out signed by Carol stating why this bearer class is NOT privileged (with the rationale tied to the synthetic-site-id scoping + DB CHECK constraint).
   Either path closes Counsel Rule 3. Today's design treats this as a Q3 user-decision but it's not user-decidable — it's a Counsel-Rule-3 design constraint.

3. **P0-CH1 / Coach — Synthetic-marker double-build risk with MTTR-soak-v2 (task #98).** v2 design MUST explicitly take ownership of synthetic-marker unification across both load-harness AND MTTR-soak-v2 classes. Add §3.5 "Coordination with task #98" stating that MTTR-soak-v2 will inherit the same marker pattern (no `sites.synthetic BOOLEAN`).

### P1 (close or carry as named tasks in same commit)
1. **P1-M1 / Maya — JSONB transition partial-rollout window.** Add forward-compat INSERT trigger OR ship migration + plan-24 code change in same git push.
2. **P1-M2 / Maya — CREATE INDEX CONCURRENTLY split.** Move index drop+create into a separate single-statement migration to avoid 30s table-lock on `incidents`.
3. **P1-C1 / Carol — AlertManager pattern reuse.** Inherit `SUBSTRATE_ALERT_SOAK_SUPPRESS` pattern; rename env-flag to `SUBSTRATE_ALERT_SYNTHETIC_SUPPRESS`.
4. **P1-C2 / Carol — Header-on-prod-site rejection.** New CI gate `test_synthetic_header_only_for_synthetic_site.py` pinning that `X-Synthetic-Run` from a non-synthetic auth-site = 400 + WARN log.
5. **P1-CH1 / Coach — Gate B migration-apply path verification.** Gate B verdict must cite fresh-DB + plan-24-applied apply tests.
6. **P1-A1 / Auditor — Capacity-claim evidence bundle.** New `capacity_claims` table; "≥30 clinics" number signed.
7. **P1-S2 / Steve — Baseline-relative Phase 1 SLAs.** Pre-run 24h baseline measurement of real-customer probe per endpoint; SLAs as baseline+delta.

### P2 (nice-to-have / followup)
1. **P2-A1 / Auditor — Methodology disclosure on public sales artifact.** Three-line method note + run timestamp.
2. **P2-PM1 / PM — Phase 2 abort criteria.** Define when Phase 1 is "enough."
3. **P2-COUNSEL1 / Attorney — Register `load_capacity.simultaneous_clinic_max` in task #50 canonical-source registry.**

---

## Migration-Numbering Explicit Verdict

**MIG 310 IS COLLIDED.** `310_close_l2esc_in_immutable_list.sql` is shipped
in main (committed during the 2026-05-12 enterprise-readiness sweep). The
v2 design MUST be reissued with a renumbered migration. Verified directory
state 2026-05-13:

```
303_substrate_mttr_soak.sql          (shipped — plan-24)
304_quarantine_synthetic_mttr_soak.sql (shipped — plan-24)
305_delegate_signing_key_privileged.sql (shipped — Session 220)
[306 missing]
307_ots_proofs_status_check.sql      (shipped)
308_l2_escalations_missed.sql        (shipped)
309_l2_decisions_site_reason_idx.sql (shipped)
310_close_l2esc_in_immutable_list.sql (SHIPPED — COLLIDES)
[311 missing — first available]
312_baa_signatures_acknowledgment_only_flag.sql (shipped)
313_d1_heartbeat_verification.sql    (shipped)
[314 reserved for task #50 canonical-source registry — in_progress]
```

**Recommended renumber: mig 311** (lowest available). If task #98 (MTTR
soak v2) also wants mig 311, coordinate per Coach P0-CH1 — they should
share one migration anyway. After this renumber, the next free is 315.

**Add to the migrations directory in the same PR:** `RESERVED_MIGRATIONS.md`
listing in-progress migration designs with their assigned numbers to prevent
the recurrent collision class. This is the THIRD migration-collision Gate-A
finding today (this design, P-F9, task #98) — pattern is clear.

---

## Top 3 P0s Before Any Code Lands

1. **Renumber mig 310 → mig 311.** Update lines 105, 165, 179, 182-221, 279, 443-445 of the design. Verify against shipped migrations directory at design re-publish.

2. **Reclassify load-test bearer as privileged-credential class.** Add §3.6 with `ALLOWED_EVENTS` entry + attestation issuance flow OR an explicit Carol-signed non-privileged carve-out memo. Either decision must be in-design, not deferred to Q3.

3. **Take ownership of synthetic-marker unification with task #98 MTTR-soak-v2.** Add §3.5 declaring that MTTR-soak-v2 inherits this marker; coordinate via shared migration; both designs land in lockstep or load-harness lands first and #98 conforms.

---

## Final Overall Verdict

**BLOCK — re-issue as v2.1 addressing 3 P0s above. APPROVE-WITH-FIXES on
P1s carried as named TaskCreate followups in the same commit that ships
Phase 0.**

Gate A v2.1 re-review can be a focused fork (the 3 P0s above + verification
P1s landed as tasks) — not a full 7-lens rerun. **Gate B is still required
before Phase 1 numbers are published** per Session 220 two-gate lock-in.

Strong design otherwise. v1→v2 lift is substantial. Author is correctly
applying the no-author-counter-arguments rule (§8 questions are explicit
user-gate input, not in-doc rationalization). With 3 P0s closed, this is
ready to ship.
