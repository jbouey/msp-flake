# Gate A — Substrate-MTTR Soak v2 — 7-lens Class-B verdict

**Reviewer:** Coach (fork-mode 7-lens adversarial)
**Date:** 2026-05-13
**Subject:** `audit/substrate-mttr-soak-v2-design-2026-05-13.md`
**Prior state:** v1 Gate A BLOCK (6 P0, 8 P1, 5 P2) on 2026-05-11
**Verdict:** **APPROVE-WITH-FIXES** — v1's three load-bearing failure modes are CLOSED. Three new P0s surfaced (mig-number collision, isolation-ordering race, missing pre-mig schema guard). All three are mechanical, not design-level. Close before mig lands; do not redesign.

---

## 250-word summary

v2 inverts v1's broken measurement model and the inversion is correct. v1 wrote `incidents` rows then auto-resolved them on its own schedule — measuring the injector, not the engine. v2 seeds the exact orphan SHAPE that `_check_l2_resolution_without_decision_record` (assertions.py:1101-1137) already queries, and waits for the engine to open a `substrate_violations` row. Source-verification confirms every load-bearing claim: `RESOLVE_HYSTERESIS_MINUTES = 5` at assertions.py:6076, the `(invariant, site_id)` collapse at 6137-6155, `_check_substrate_sla_breach` with sev1/sev2/sev3 SLA at 240/1440/43200 minutes (line 615), and `SUBSTRATE_ALERT_SOAK_SUPPRESS` shipped at alertmanager_webhook.py:122. All three v1 P0 root-causes (incidents-invisible-to-engine, fabricated-SLA-bound, contamination) are CLOSED in v2. Three NEW P0s surfaced that block mig land — **NOT redesign blockers, but commit blockers**: (1) the migration is numbered 311 throughout the doc, but Task #43 already reserves mig 311 for `vault_signing_key_versions`; user explicitly said "before mig 314 lands" — renumber required; (2) v2 flips the synthetic site `status='active'` BEFORE the `synthetic = FALSE` filter ships across ~30+ callsites, opening a contamination re-introduction window equal to deploy-lag; (3) Q4 (compliance_bundles CHECK constraint) is left "open" — but rule 2 + rule 3 of Counsel's 7 demand a schema-level write-side guard; defer to follow-up is acceptable ONLY if the gate is decided before injector starts. Other P0/P1 detailed below. Smoke run (Phase C) is a real gate — keep it.

---

## Per-lens verdict

### 1. Engineering (Steve) — **APPROVE-WITH-FIXES**

**Verified against source:**
- `_check_l2_resolution_without_decision_record` at assertions.py:1101-1137 queries the EXACT shape v2 seeds. Run on every tick via `run_assertions_once` at line 6079. ✅
- Engine UPSERTs `substrate_violations` and uses `RESOLVE_HYSTERESIS_MINUTES = 5` (line 6076) as the resolve gate (line 6253-6255). ✅
- `(invariant_name, site_id)` collapse logic at 6137-6155 means multiple seeds → single violation row with `details.matches[]` accumulator. The analyzer's plan to join via `details->'matches'` is correct. ✅

**P0-S1 (this lens):** The §3 timing model claims `resolve_latency ≤ 360s`. The actual engine semantics are:
- tick N: engine sees invariant query empty for the key
- tick N: UPDATE sets `last_seen_at` is NOT touched (the key isn't in `current_keys`, so it falls into the resolve-loop at 6244-6279)
- The resolve only fires if `last_seen_at < NOW() - 5min` (line 6253-6255). `last_seen_at` was last touched at tick N-1 (the last tick the seed WAS present). So actual resolve latency floor = `(time_since_last_refresh_when_deleted)` + `(5min - time_since_last_refresh_when_deleted)` + tick granularity = `5min + tick`. Design says "300s ≤ resolve_latency ≤ 360s" — correct math, but it assumes the seed is deleted IMMEDIATELY after a refresh tick. If deleted 50s after the last refresh, resolve will fire at the very next tick (250s after deletion, not 300s). **The lower bound should be `~250s` not `300s`.** Update analyzer expected-floor to avoid false-FAIL.

**P1-S1:** Phase B claims the analyzer reads `substrate_violations.severity` directly. The substrate engine sets severity from `a.severity` (line 6207), which is the static class severity (sev2 for L2-orphan). v2 wants to differentiate sev1/sev2/sev3 seeds — but the ENGINE will tag every L2-orphan violation as sev2 regardless of v2's per-seed `severity_label`. The "sev1/sev2/sev3 detect_p99" rows in §6 are misleading: they're all the SAME invariant, all sev2. Either re-frame the soak as "single-invariant detect/resolve MTTR" (drop sev1/sev3 columns from §6) OR add a second invariant (Q6's recommendation, deferred to v3). **Recommend dropping the sev1/sev3 columns** and re-scoping v2 verdict to sev2-only.

### 2. Database (Maya) — **APPROVE-WITH-FIXES**

**RLS:** `sites` table is admin-context for the substrate engine. The synthetic site has no `partner_id` or `client_org_id` so `tenant_org_isolation` / `tenant_partner_isolation` policies naturally exclude it. ✅ No RLS regression.

**`sites.synthetic` column-add:** `ALTER TABLE … ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE` is fine at current `sites` size (~hundreds of rows), no online-DDL concerns. ✅

**Site-rename interaction:** the `_rename_site_immutable_tables()` list (mig 257) does NOT include `sites` itself for column-level data — the PK row is aliased via `site_canonical_mapping`. `synthetic=TRUE` lives on the original site row and is unaffected by future renames. ✅

**P0-M1 (this lens):** **Sequencing race.** v2 mig 311 simultaneously (a) adds `synthetic` column with default FALSE, (b) UPDATEs the v1-quarantined synthetic site to `status='active' AND synthetic=TRUE`. The CI ratchet `test_synthetic_site_filter_universality.py` is "Phase A deliverable" but the design lets it ship AT THE SAME TIME as the mig. **Code referencing `status != 'inactive'` only (current behavior at routes.py:151, partners.py:1304, background_tasks.py:1796, etc.) will see the synthetic site as soon as `status` flips to `active` — BEFORE the synthetic-filter code ships and rolls out**. Backend deploy lag is minutes; contamination window is non-zero. **Fix: split mig 311 into TWO migrations.** First migration: add `synthetic` column + add filter code in same commit. Wait for prod deploy + CI gate green. THEN ship second migration that flips `status='active'`. Or: keep one mig but DEFER the status flip — leave the site at `'inactive'` until the injector starts (the injector itself can do the flip, gated on the filter-test green).

**P1-M1:** Q4 `compliance_bundles` CHECK constraint with `NOT VALID` is the correct schema-level write-side guard. The current write-paths to `compliance_bundles` are well-known (evidence_chain.py + flywheel_promote.py); historical rows have zero `synthetic-%` site_ids per the v2 isolation contract. **Include the CHECK in mig 311.** `NOT VALID` defers the table scan and only enforces on NEW writes — exactly what's needed. Leaving this "open" violates Counsel Rule 2 (PHI/customer-data boundary as compiler rule, not posture).

**P2-M1:** Mig 311 also needs to drop the partial index `WHERE synthetic = TRUE` if any subsequent rename or backfill ever creates a second synthetic site. Partial indexes can become invalid if the predicate column type changes — but BOOLEAN is stable, so this is theoretical. Accept as-is.

### 3. Security (Carol) — **APPROVE-WITH-FIXES**

**Customer-surface leak risk:** the synthetic site has zero `client_org_id` and zero `partner_id`. Customer-facing surfaces (`/api/client/*`, `/api/partners/me/*`) all filter by `client_org_id = $1` or `partner_id = $1`. ✅ Synthetic site is naturally excluded from those endpoints.

**Operator-only surface risk:** `/api/fleet`, `/admin/metrics`, federation tier-org — these were the v1 contamination paths. v2's §4 adds explicit `synthetic = FALSE` filters. ✅ design intent is correct.

**Auditor-kit risk:** the design's mitigation is to add a `synthetic=FALSE` check at `require_evidence_view_access` entry. Verified the chokepoint exists at evidence_chain.py:60 and is used by 8+ endpoints. ✅

**Privileged-chain risk:** synthetic seeds CREATE `incidents` rows but do not trigger `fleet_orders` (no remediation runs against the synthetic site — there's no appliance, no daemon, no order target). Mig 175 trigger `trg_enforce_privileged_chain` is on `fleet_orders`, not `incidents`. ✅ no chain risk.

**P0-C1 (this lens):** **PHI-pre-merge gate (task #54 in-flight) interaction.** The synthetic seeds INSERT `incidents.details` JSONB. If the gate enumerates `incidents.details` for PHI keys, the synthetic injector's payload format must NOT include any field that could trip a future PHI-pattern false-positive (`patient`, `dx_`, `mrn`, `ssn` etc.). **Add to design §2: synthetic injector payload schema is FROZEN at `{soak_test: true, soak_run_id, invariant_target, seed_severity}` — no other fields**. Pinned by a CI test that opens the injector source and asserts the JSON-build keys are exactly that list.

**P1-C1:** The synthetic-site has no `partner_id` / `client_org_id`. If an unrelated future code path adds defense-in-depth `JOIN partners ON sites.partner_id = partners.id`, the synthetic site silently drops out — GOOD. But if any future code path uses `LEFT JOIN partners` and then INNER JOIN something downstream, the synthetic site could surface via NULL-bypass. The design's "defense in depth" filter on `synthetic = FALSE` covers this. Accept as a CI-gate-maintained invariant.

### 4. Coach — **APPROVE-WITH-FIXES**

**Double-build vs chaos-lab?** Chaos-lab attacks real VMs (AD DC, workstation, appliance at iMac). The substrate-MTTR soak measures whether the **substrate ASSERTION ENGINE** opens and resolves rows. These are different layers: chaos-lab exercises the **detection of real configuration drift on real OSes**; the soak exercises the **substrate engine's own loop latency**. ✅ NOT a double-build. They are orthogonal.

**Could the soak be a chaos-lab extension?** No — chaos-lab depends on real attack signal traversing WinRM/event-logs/daemon/network; the soak short-circuits that to DB-level seed → engine tick. Bundling them would entangle two different MTTR profiles. ✅ Keep separate.

**P0-K1 (this lens):** **Single-invariant scope.** v2 only exercises ONE invariant (`l2_resolution_without_decision_record`). 60+ invariants are registered in `ALL_ASSERTIONS`. The soak's verdict generalizes from N=1 to "the substrate engine is healthy" — that's a leap. **Mitigation A (preferred):** rename the soak to "L2-orphan invariant MTTR soak" and reframe verdict accordingly; v3 adds 2-3 more invariants. **Mitigation B:** explicitly state in §1 that "engine health" verdict requires a follow-up multi-invariant soak; v2 verdict is necessarily narrow.

**P1-K1:** Phase D step 2 ("Monitor `/admin/substrate-health` every 4h via curl") is implicit human-in-the-loop. Either automate the 4h assertion into the injector (auto-alert if violation row never appears) OR drop the step and rely on end-of-run analyzer. **Recommend automating** — 24h soak shouldn't depend on a human checking at hour 4, 8, 12, 16, 20.

### 5. Auditor (OCR) — **APPROVE-WITH-FIXES**

**Auditor-grade evidence:** `substrate_mttr_soak_runs_v2` table captures p50/p95/p99, `summary` JSONB, started/ended timestamps. Per-seed correlation via `substrate_synthetic_seeds` join. ✅ traceable.

**24h window:** sufficient for sev1 (4h SLA) and sev2 (24h SLA) verification. sev3 (30d SLA) is intentionally OUT of scope per §1. ✅

**P0-A1 (this lens):** **Audit-kit framing of the soak run.** The synthetic site is `synthetic=TRUE`, so its `compliance_bundles` row count = 0 by §6.8. The soak run produces `substrate_violations` rows that exist in the same prod table as real customer violations. An auditor reading `substrate_violations` history later could see the synthetic rows mixed in with real rows. **Fix:** add `synthetic_run_id UUID NULL` to `substrate_violations` (or a `synthetic BOOLEAN NOT NULL DEFAULT FALSE` column) and tag every soak-derived row at write time. Any future analytics over `substrate_violations` must filter `synthetic = FALSE`. Without this, an auditor's question 6 months from now ("what was your MTTR distribution Q2 2026?") will be polluted by 481 synthetic seeds.

**P1-A1:** The §6 PASS criteria assume the engine writes detect_at/resolved_at to `substrate_violations`. Verified at assertions.py:6202-6210 (insert) and 6249-6251 (resolve). ✅ But the analyzer's join `(invariant_name, site_id, details->>'matches'[contains incident_pk])` is JSONB containment over an array — not indexed. With ~481 violation events over 24h this is fine, but document the query plan in the analyzer source.

### 6. PM — **APPROVE-WITH-FIXES**

**Implementation cost:** Phase A (mig + filters + ratchet) ~1 eng-day. Phase B (injector + analyzer rewrite) ~1 eng-day. Phase C smoke ~2h. Phase D 24h soak ~0 active eng-time. Phase E cleanup ~30min. **Total ~2.5 eng-days.** Reasonable for a P0 blocker on substrate observability.

**Phasing:** A→B→C→D→E is correct order. Gate A2 (re-fork after smoke) is the right inclusion per Session 220 rule.

**Risk of false-positive noise:** ZERO real-operator pages because `SUBSTRATE_ALERT_SOAK_SUPPRESS=true`. Verified at alertmanager_webhook.py:122. ✅

**P0-P1 (this lens):** **Mig-number collision.** Design says mig 311 throughout. Task #43 reserves mig 311 for `vault_signing_key_versions DB table`. User explicitly said "before mig 314 (the soak migration) lands". **The soak migration must be renumbered.** Next free slot is 314 (305→307 skip-pattern shows 306+311 are gaps, 312 and 313 are taken). **Rename to mig 314 (or 306/311 if reclaiming gaps — but 314 is unambiguous and matches the user's instruction). Update every reference in the design doc + all referenced filenames.**

**P1-P1:** Phase A deliverable for the CI gate baseline says "best-guess 35-50 callsites". Source-walk shows >212 `FROM sites` callsites across the backend (`client_portal.py` alone has 20+). **The ratchet baseline will be much higher (~100+).** This is not a blocker but the eng-day estimate for Phase A is light by ~0.5 day if each callsite needs review. Recommend an audit pass before committing the ratchet — many callsites are already site-id-filtered (`WHERE site_id = $1`) and don't need a synthetic filter; only enumeration sites do.

### 7. Attorney (in-house counsel) — **APPROVE**

**Banned-word scan of design doc:** "ensures", "prevents", "protects", "guarantees", "100%", "audit-ready" — none of these appear in v2 (vs. v1 design had several). ✅

**Counsel Rule 6 (no BAA in memory):** synthetic seeds never touch `baa_signatures`, `compliance_bundles` (via §4 + Maya P1-M1 CHECK constraint), `client_orgs`, `clients`. ✅

**Counsel Rule 2 (PHI boundary):** synthetic injector payload contains zero PHI patterns (per Carol P0-C1 freeze). ✅ once that pin lands.

**Counsel Rule 1 (no non-canonical metric):** the soak's p50/p95/p99 are NEW metrics scoped to `substrate_mttr_soak_runs_v2`, never customer-facing. ✅ no canonical-source registry impact.

**Counsel Rule 7 (no unauthenticated context):** all soak artifacts live in admin-context tables; auditor-kit refuses synthetic site_ids per design §4.6. ✅

---

## Cross-lens findings (consolidated, severity-graded)

### P0 (block mig land)

- **P0-CROSS-1 (mig-number collision)** [PM + Steve]: design says mig 311, but Task #43 reserves 311 for vault. User said "before mig 314 lands." **Renumber to mig 314 throughout the doc + all artifact filenames.** Closes a guaranteed migration-apply failure on first push.

- **P0-CROSS-2 (status-flip ordering race)** [Maya]: mig 311 currently flips synthetic site `status='active'` SIMULTANEOUSLY with adding the `synthetic` column — but the filter code referencing `synthetic = FALSE` is "Phase A deliverable" without an explicit ordering constraint. Either split into two migrations OR keep one but DEFER the `UPDATE sites SET status='active'` to the injector itself (gated on CI green). Pre-deploy window = contamination window.

- **P0-CROSS-3 (compliance_bundles CHECK)** [Maya + Carol]: Q4 is left open. Counsel Rule 2 demands a schema-level write-side guard. **Include the `CHECK (site_id NOT LIKE 'synthetic-%') NOT VALID` constraint in mig 314.** Defers the table scan (cheap) and enforces on every new write.

### P1 (close before Phase B starts)

- **P1-CROSS-1 (severity-axis is illusory)** [Steve]: §6 reports detect_p99 per-severity but the engine tags every L2-orphan violation as the same severity (sev2). **Drop the sev1/sev3 columns from §6 OR add a second invariant.** As-written the table misleads readers.

- **P1-CROSS-2 (single-invariant scope clarity)** [Coach]: rename the soak to "L2-orphan invariant MTTR soak" so the verdict isn't over-generalized.

- **P1-CROSS-3 (`substrate_violations` synthetic marker)** [OCR]: tag synthetic-derived violation rows with a flag column so future analytics over `substrate_violations` history can exclude them. Without this, the audit table is polluted forever.

- **P1-CROSS-4 (synthetic injector PHI-payload freeze)** [Carol]: pin the JSON keys in `incidents.details` to a static allowlist + CI test. Prevents future PHI-pre-merge gate false-positives.

- **P1-CROSS-5 (4h monitor automation)** [Coach]: automate the Phase D 4h checkin into the injector heartbeat; remove human-in-the-loop from 24h soak.

### P2 (nice-to-have, do during Phase A audit)

- **P2-CROSS-1 (resolve-latency floor math)** [Steve]: adjust expected-floor from 300s to ~250s in analyzer; design's lower bound assumes worst-case last-refresh-then-delete timing.

- **P2-CROSS-2 (CI ratchet baseline calibration)** [PM]: source-walk shows >100 `FROM sites` callsites; Phase A audit pass needed before committing ratchet baseline.

---

## Top 3 P0s that MUST close before mig 314 lands

1. **Renumber design + all artifacts from mig 311 → mig 314.** Conflict with Task #43 (vault) is a guaranteed `psycopg2.errors.DuplicateObject` on apply.

2. **Fix the status-flip ordering race.** Either split mig into two migrations (column + filters ship first; status-flip ships second after CI gate green) OR move the `UPDATE sites SET status='active'` into the injector start path. As-written there is a non-zero contamination window between mig apply and filter-code deploy.

3. **Include `compliance_bundles CHECK (site_id NOT LIKE 'synthetic-%') NOT VALID` in mig 314.** Counsel Rule 2 demands schema-level write-side guards for PHI/customer-data tables. Closes Q4 as a YES.

---

## Final overall verdict

**APPROVE-WITH-FIXES.**

v1's three load-bearing failure modes (incidents-invisible-to-engine, fabricated-SLA-bound, contamination) are CLOSED in v2. Source-verification against assertions.py confirms every load-bearing claim. The design is correct.

The three P0s above are mechanical:
- mig number collision (1-line fix)
- status-flip ordering race (split mig or move flip to injector)
- compliance_bundles CHECK constraint (one ALTER TABLE statement)

None require redesign. Close all three before mig lands; close P1s before Phase B injector starts; defer P2s to Phase A audit pass.

**Gate A2 (post-implementation fork review) is correctly scheduled at end of Phase C smoke (per Session 220 two-gate lock-in). Do not skip it — design correctness ≠ implementation correctness.**
