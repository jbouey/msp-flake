# Gate A — MTTR Soak v2 Phase B — 7-lens Class-B verdict

**Reviewer:** Coach (fork-mode 7-lens adversarial)
**Date:** 2026-05-14
**Subject:** Phase B of `audit/substrate-mttr-soak-v2-design-2026-05-13.md` (§7 Phase B + §4 filter rollout + Appendix A artifacts)
**Task:** #66 — synthetic=FALSE filter rollout + CI ratchet + injector
**Prior state:** Phase A SHIPPED (mig 315 + commit 508c5922). Phase A Gate A v2 = APPROVE-WITH-FIXES (3 P0 closed in mig 315 as shipped).
**Verdict:** **APPROVE-WITH-FIXES — split into B1 (filter rollout + 2 CI ratchets) and B2 (injector + analyzer)**

---

## 250-word summary

Phase B is the read-side rollout (synthetic=FALSE filter) plus the write-side injector. Phase A shipped the schema correctly: mig 315 verified — `sites.synthetic` column, `substrate_synthetic_seeds` with the FK to `sites(site_id)` AND the `site_id LIKE 'synthetic-%'` CHECK, `substrate_mttr_soak_runs_v2`, and the `compliance_bundles no_synthetic_bundles` NOT VALID CHECK. The status-flip was correctly removed from the migration — the injector owns it. So Phase A's three P0s are genuinely closed in the shipped artifact.

The dominant Phase B finding: the design's "35-50 callsites" and Phase A Gate A's ">212" are both wrong framings. Source-walk gives **214 `FROM sites` occurrences → 169 site_id-scoped → 24 org/partner-scoped (synthetic naturally excluded, no client_org_id) → 21 truly-unscoped enumeration callsites that need an explicit `synthetic = FALSE`**. That's the real CI ratchet baseline target, not 100+. The ratchet should count the 21, not all 214.

The hard P0 is **sequencing**: the injector flips `sites.status='active'`. If it ships in the same phase as the filter rollout, an injector run before the filter code is deploy-verified re-opens the exact v1 contamination class. **B1 (filters + `test_synthetic_site_filter_universality.py` + `test_auditor_kit_refuses_synthetic_site.py`) must deploy-verify green BEFORE B2 (injector) can flip status.** That is the Phase A P0-CROSS-2 deferred-flip contract — Phase B must honor it structurally, not by convention.

Carry-forward P1s from Phase A Gate A v2 (substrate_violations synthetic marker, injector PHI-payload freeze, 4h-monitor automation, drop sev1/sev3 columns) all land in Phase B scope and are re-confirmed below. None block; all must be in the B1/B2 commits or named followup tasks per the two-gate rule.

---

## State verification (what's actually on disk)

| Item | Status |
|---|---|
| mig 315 `substrate_mttr_soak_v2.sql` | SHIPPED — verified: `sites.synthetic` col + partial idx, `substrate_synthetic_seeds` (FK `REFERENCES sites(site_id)` + CHECK `site_id LIKE 'synthetic-%'`), `substrate_mttr_soak_runs_v2`, `compliance_bundles no_synthetic_bundles CHECK ... NOT VALID`, audit-log row as `jbouey2006@gmail.com`. status flip correctly ABSENT. |
| `test_synthetic_site_filter_universality.py` | **DOES NOT EXIST** — Phase B deliverable |
| `test_auditor_kit_refuses_synthetic_site.py` | **DOES NOT EXIST** — Phase B deliverable |
| `test_l2_orphan_invariant_includes_synthetic_sites.py` | **DOES NOT EXIST** — Phase B deliverable (design Q5) |
| `scripts/substrate_mttr_soak_inject_v2.py` | **DOES NOT EXIST** — Phase B deliverable |
| `scripts/substrate_mttr_soak_report_v2.py` | **DOES NOT EXIST** — Phase B deliverable |
| mig 315 audit-log references `coach-substrate-mttr-soak-v3-gate-a-2026-05-13.md` | exists on disk |

---

## Real enumeration-callsite count

Source-walk of `mcp-server/central-command/backend/**/*.py` (excluding tests):

- **214** total `FROM sites` occurrences
- **169** are site_id-scoped (`WHERE site_id = $1`, `JOIN ... ON s.site_id =`, `site_id IN (...)`) — synthetic row never enumerated; **no filter needed**
- **24** are org/partner-scoped (`WHERE client_org_id = $1` / `partner_id = $1`) — synthetic site has NULL `client_org_id` and NULL `partner_id`, so it is **naturally excluded**; a `synthetic = FALSE` filter here is defense-in-depth only, NOT load-bearing
- **21** are **truly-unscoped enumeration callsites** that WILL surface the synthetic row once `status='active'` — these are the real rollout targets

The 21 (2 design-doc false positives — `cross_org_site_relocate.py:6` and `routes.py:1662` are comments — excluded):

```
partners.py:3661          COUNT(*) WHERE partner_id IS NOT NULL   (partner_id IS NOT NULL ≠ scoped)
db_queries.py:438         COUNT(*) FROM sites                      (platform total)
assertions.py:2802        IN (SELECT site_id FROM sites WHERE client_org_id::text ...)  ← VERIFY: substrate engine — may be intentional
client_portal.py:4220     FROM sites s
notifications.py:505      SELECT s.client_org_id FROM sites s
flywheel_federation_admin.py:201   FROM sites s   (federation tier-org — design §4 names this)
flywheel_federation_admin.py:276   FROM sites s
provisioning.py:130       SELECT wg_ip ... ORDER BY wg_ip DESC LIMIT 1   (WG IP allocation — synthetic has no wg_ip, low risk but include)
routes.py:1555            FROM sites WHERE onboarding_stage NOT IN (...)   (onboarding pipeline)
routes.py:1637            GROUP BY onboarding_stage
routes.py:1665/1671       AVG ship/active time
routes.py:1676/1683       COUNT(*)
routes.py:2679            COUNT(*) WHERE status != 'inactive'      ← the canonical leak shape from v1
routes.py:3261/3266       COUNT(*) cnt
routes.py:4681            SELECT site_id, client_org_id WHERE client_org_id IS NOT NULL
integrations/tenant_isolation.py:97/104/118   SELECT id FROM sites
```

**CI ratchet baseline recommendation: 21** (the truly-unscoped set), NOT 100+ and NOT 214. The ratchet scans only enumeration-shape callsites; site_id-scoped and org-scoped lines get an auto-skip in the gate (and the org-scoped 24 get an optional `# noqa: synthetic-allowlisted` for defense-in-depth credit). Design Q2 best-guess "35-50" is the closest of the prior estimates but still high — 21 is the verified number.

Two callsites need a human decision, not a blind filter:
- `assertions.py:2802` — this is the **substrate engine itself**. Per design §4.3 the engine MUST tick on the synthetic site. Adding `synthetic = FALSE` here could blind the engine. **VERIFY this is not the L2-orphan path before filtering — likely needs the `# noqa: synthetic-allowlisted` carve-out, not a filter.**
- `provisioning.py:130` — WG IP allocator. Synthetic site has no `wg_ip` so it self-excludes via `WHERE wg_ip IS NOT NULL`. Filter is harmless but document as defense-in-depth.

---

## Per-lens verdict

### 1. Engineering (Steve) — APPROVE-WITH-FIXES

The filter rollout: real target is **21 enumeration callsites**, not the design's 35-50 nor Gate A v2's ">212." 169 are site_id-scoped (irrelevant), 24 are org-scoped (synthetic self-excludes via NULL `client_org_id`). The CI ratchet must classify, not count-all: scan `FROM sites`, auto-skip site_id-scoped and org-scoped windows, ratchet only the enumeration remainder. A naive "every `FROM sites` needs `synthetic`" gate would force 193 pointless edits and bury the 21 that matter.

**P0-S1 — injector status-flip location + gate.** The design (§2 mig comment, §7 Phase B) says "the injector itself flips status to 'active' at startup, gated on CI green." But Phase B as written bundles injector + filters in one phase. Where does the gate *live*? "CI green" is not a runtime check the injector can perform. The injector needs a concrete precondition: **(a) a sentinel — e.g. the injector refuses to start unless `git rev-parse HEAD` of the deployed backend matches a commit at-or-after the filter-rollout commit, verified via `/api/version` runtime_sha; OR (b) simpler: B1 ships and deploy-verifies first, B2's injector is written assuming filters are already live.** Recommend (b) — see Coach lens. The injector must `UPDATE sites SET status='active' WHERE site_id='synthetic-mttr-soak' AND synthetic=TRUE` at startup and `UPDATE ... SET status='inactive'` in its cleanup/atexit handler, so a crashed injector doesn't leave the site active.

**P1-S1 — `assertions.py:2802` carve-out.** The substrate engine must tick on the synthetic site. Confirm `:2802` is not on the `_check_l2_resolution_without_decision_record` path; if it is, it gets `# noqa: synthetic-allowlisted`, not a filter. The CI gate `test_l2_orphan_invariant_includes_synthetic_sites.py` (design Q5) must pin that the L2-orphan query body does NOT exclude synthetic sites.

**P1-S2 — injector cleanup idempotency.** Injector must be crash-safe: atexit + signal handler that flips status back to `inactive` and stamps `removed_at` on any seed it created. A `SIGKILL`-mid-run leaves the synthetic site `active` with the filters as the only safety net — acceptable, but the next injector run must reconcile (DELETE orphan incidents rows for `synthetic-mttr-soak`, reset status) before seeding.

### 2. Database (Maya) — APPROVE

mig 315 verified as shipped. `substrate_synthetic_seeds`:
- `site_id TEXT NOT NULL REFERENCES sites(site_id)` — FK present and correct. ✅
- `CONSTRAINT synthetic_seeds_site_synthetic CHECK (site_id LIKE 'synthetic-%')` — present. ✅ Belt-and-suspenders with the FK: the seed table can only ever reference a `synthetic-`-prefixed site.
- `incident_id UUID` nullable, no FK to `incidents.id` — **intentional and correct.** A hard FK would block the injector's DELETE of the underlying `incidents` row (the closure mechanism) unless `ON DELETE SET NULL`. The design's round-trip audit reads `detected_at`/`resolved_at` which the analyzer populates from `substrate_violations`, so the `incident_id` link is correlation-only. Accept as-is. Minor: consider `ON DELETE SET NULL` if a FK is ever wanted, but nullable-no-FK is fine for a soak-scoped table.
- `idx_synthetic_seeds_run` on `(soak_run_id, seeded_at)` — correct for the analyzer's per-run join. ✅

`substrate_mttr_soak_runs_v2` — p50/p95/p99 columns, `status` CHECK includes `'quarantined'`, `config`/`summary` JSONB. Schema-OK. ✅

**P1-M1 (write-side) — injector INSERT into `incidents` must be transaction-wrapped.** The injector INSERTs an `incidents` row + an `substrate_synthetic_seeds` row. These two must be atomic — a seed row with no matching `incidents` row (or vice versa) corrupts the analyzer join. Wrap in a single `async with conn.transaction():`. Same for the closure path (DELETE incidents row + UPDATE `removed_at`).

**P1-M2 — no `ON CONFLICT` on `substrate_synthetic_seeds`.** `seed_id` is `gen_random_uuid()` PK so collisions are impossible, but if the injector ever retries a seed it must generate a fresh `seed_id`, not reuse. Document in the injector that each seed attempt is a fresh row.

No RLS regression — `substrate_synthetic_seeds` is admin-context, synthetic site has no org/partner. The `compliance_bundles no_synthetic_bundles` CHECK is `NOT VALID` — note for the team: it enforces on new writes immediately but the existing 232K rows are unvalidated. A manual `VALIDATE CONSTRAINT` is a separate (optional) follow-up; not a Phase B blocker since the v2 isolation contract guarantees zero existing `synthetic-` bundles.

### 3. Security (Carol) — APPROVE-WITH-FIXES

**P0-C1 — injector PHI-payload freeze (carried from Phase A Gate A v2 P1-CROSS-4, ELEVATED to P0 for Phase B because Phase B is where the injector source is written).** The injector INSERTs `incidents.details` JSONB. Counsel Rule 2 = PHI boundary is a compiler rule. The injector's `details` payload MUST be frozen to a static key allowlist: `{soak_test: true, soak_run_id, invariant_target, seed_severity}` — nothing else. No free-form fields, no hostnames, no `description` blobs that could trip the task #54 PHI-pre-merge gate's pattern scan. This must be **pinned by a CI test that opens `substrate_mttr_soak_inject_v2.py` source and asserts the `jsonb_build_object` / dict-literal keys are exactly that allowlist.** The design §2 names the marker fields (`soak_test`, `soak_run_id`) but does NOT freeze the full payload. Phase B design must add the freeze + the CI pin to Appendix A.

**P1-C1 — `details->>'soak_run_id'` must be a generated UUID, never operator-supplied free text.** Prevents an operator pasting anything PHI-shaped into a `--run-id` arg. Injector generates the run UUID internally; no CLI override.

Otherwise: synthetic site has no `client_org_id`/`partner_id` so customer endpoints self-exclude; auditor-kit refusal gate (`test_auditor_kit_refuses_synthetic_site.py`) is in Phase B scope; synthetic seeds CREATE `incidents` only, never `fleet_orders`, so the mig-175 privileged-chain trigger is untouched. ✅

### 4. Coach — APPROVE-WITH-FIXES → **SUB-PHASE B1/B2**

**P0-K1 — Phase B MUST split into B1 and B2.** This is the load-bearing finding. Phase B as written bundles (a) the 21-callsite filter rollout, (b) 3 CI ratchets, (c) the injector, (d) the analyzer into one phase. The injector flips `sites.status='active'`. If the injector lands and runs before the filter rollout is **deploy-verified green in prod**, the synthetic site surfaces on `/api/fleet`, `/admin/metrics`, federation tier-org, onboarding pipeline — the *exact* v1 contamination class. Phase A's P0-CROSS-2 deferred-flip contract said the flip is "gated on the synthetic=FALSE filter code being live in prod (deploy-verified)." A single bundled Phase B cannot honor that — there is no enforcement boundary between (a) and (c).

**Required split:**
- **B1 — filter rollout + CI ratchets.** 21 enumeration-callsite filters + `# noqa` carve-outs for `assertions.py:2802` (engine) + the org-scoped defense-in-depth. Ships `test_synthetic_site_filter_universality.py` (ratchet baseline 21), `test_auditor_kit_refuses_synthetic_site.py`, `test_l2_orphan_invariant_includes_synthetic_sites.py`. **Must be pushed, CI green, AND deploy-verified** (`curl /api/version` → runtime_sha == B1 commit) before B2.
- **B2 — injector + analyzer.** `substrate_mttr_soak_inject_v2.py` (owns the status flip, crash-safe cleanup) + `substrate_mttr_soak_report_v2.py`. The injector's first action is `UPDATE sites SET status='active'` — and B2's commit body must cite the B1 deploy-verification SHA as the precondition.

This also de-risks Gate B: B1 and B2 get **separate Gate B fork reviews** (per Session 220 two-gate rule), each scoped to a coherent diff, each running the full pre-push sweep. A bundled Phase B Gate B would be diff-sprawled across schema-read + script-write — exactly the diff-scoped-review failure class from the Session 220 lock-in.

**P1-K1 — automate the Phase D 4h monitor (carried from Phase A Gate A v2 P1-CROSS-5).** The 24h soak's "curl `/admin/substrate-health` every 4h" is human-in-the-loop. Build the assertion into the injector heartbeat — if no `substrate_violations` row appears within 2 ticks of a seed, the injector self-aborts and stamps `substrate_mttr_soak_runs_v2.status='aborted'`. This belongs in B2.

### 5. Auditor (OCR) — APPROVE-WITH-FIXES

**P0-A1 — `substrate_violations` synthetic marker (carried from Phase A Gate A v2 P1-CROSS-3, ELEVATED to P0).** mig 315 did NOT add a synthetic marker column to `substrate_violations`. The soak opens up to 481 (well — see P1-A2) violation rows in the **prod `substrate_violations` table**, mixed with real customer violations. Six months from now an auditor asking "what was your substrate MTTR distribution in Q2 2026?" gets a polluted answer. Phase B (B1) must ship a follow-on migration — **mig 316+ adding `substrate_violations.synthetic_run_id UUID NULL`** (or `synthetic BOOLEAN NOT NULL DEFAULT FALSE`), and the substrate engine's UPSERT path (`assertions.py` ~6202) must stamp it when the violation's `site_id LIKE 'synthetic-%'`. Without this, the soak permanently contaminates the audit table. This is not optional — it is the auditor-grade-evidence requirement. If it can't land in B1, it must be a **named, committed TaskCreate followup blocking B2's injector run** (not "deferred").

**P1-A2 — clarify the `(invariant, site_id)` collapse vs the 481-seed count.** Per Phase A Gate A v2 (Steve lens) and design §5, the L2-orphan invariant collapses to ONE `substrate_violations` row per `(invariant_name, site_id)`, with `details.matches[]` accumulating incident IDs. So the soak does NOT open 481 violation rows — it opens essentially ONE long-lived row whose `matches` array churns. That actually *reduces* the audit-pollution surface (P0-A1 still stands — even one synthetic row needs the marker). But the design §5 "481 seeds" and the analyzer's per-seed join logic need to be reconciled: the analyzer joins `substrate_synthetic_seeds` ↔ `substrate_violations` via `details->'matches'` containment, and per-seed `detected_at` is the tick the seed's incident_id *first appears* in `matches`, not a row-creation time. Phase B design (B2 analyzer section) must spell this out — otherwise the analyzer measures the wrong delta.

`substrate_mttr_soak_runs_v2` captures p50/p95/p99 + `summary` JSONB + timestamps — auditor-traceable. ✅

### 6. PM — APPROVE-WITH-FIXES

**Effort estimate (multi-day, revised from Phase A Gate A v2's 2.5-day whole-project):**
- **B1** — 21 callsite filters (mostly mechanical, ~2 need human judgment) + 3 CI gates + 1 follow-on migration (`substrate_violations` marker) + pre-push sweep + deploy-verify wait: **~1.0–1.25 eng-days.**
- **B2** — injector rewrite (crash-safe, status-flip-owning, payload-frozen, 4h-monitor auto-abort) + analyzer rewrite (collapse-aware per-seed join) + dry-run smoke test: **~1.25–1.5 eng-days.**
- **Phase C smoke (1h run) + Gate A2 re-fork:** ~0.5 day.
- **Total Phase B+C: ~3.0–3.25 eng-days.** Phase A Gate A v2's 2.5-day estimate was light — the `substrate_violations` marker migration and the crash-safe injector were under-scoped there.

**Phasing recommendation: B1 → (deploy-verify) → B2 → Phase C → Gate A2 → Phase D 24h → Phase E.** B1 and B2 each get their own Gate B. Do not let B2 start until B1's runtime_sha is confirmed deployed.

**P1-P1 — sequence the `substrate_violations` marker migration into B1, not B2.** It's a schema change; it belongs with the other schema/read-side work and must be live before the injector ever opens a violation row.

### 7. Attorney (in-house counsel) — APPROVE-WITH-FIXES

**Counsel Rule 2 (synthetic seeds must not cross PHI boundary):** The injector writes `incidents.details` JSONB. APPROVE **conditional on P0-C1** — the payload-key freeze + CI pin must be in the B2 commit. Without the freeze, a future injector edit could interpolate a hostname / description / free-text field that crosses the boundary. The freeze makes Rule 2 a compiler rule for this injector, which is the standard.

**Counsel Rule 6 (synthetic must not touch `baa_signatures` / `compliance_bundles`):** `compliance_bundles` is handled by mig 315's `no_synthetic_bundles` NOT VALID CHECK — verified shipped. The injector touches only `incidents` + `substrate_synthetic_seeds`; it does not write `baa_signatures`, `compliance_bundles`, `client_orgs`, or `clients`. APPROVE on this rule. ✅

**Counsel Rule 1 (no non-canonical metric leaves the building):** The soak's p50/p95/p99 live only in `substrate_mttr_soak_runs_v2`, never customer-facing, never on a dashboard/postcard/auditor-kit. No canonical-source-registry (task #50) impact. ✅

**Counsel Rule 7 (no unauthenticated context):** `test_auditor_kit_refuses_synthetic_site.py` (B1 deliverable) closes the auditor-kit path. Synthetic site has no portal-reachable org. ✅

One note: the soak run's audit-log entries must continue to use `jbouey2006@gmail.com` (named human) per the privileged-chain rule — mig 315 did this correctly; the injector + analyzer must too. No `system:soak` actor.

---

## Cross-lens findings (consolidated, severity-graded)

### P0 (must close before the relevant sub-phase ships)

- **P0-CROSS-1 (B1/B2 split + deferred-flip enforcement)** [Coach + Steve]: Phase B MUST split. B1 = filters + 3 CI ratchets + `substrate_violations` marker migration; deploy-verified green before B2. B2 = injector (owns status flip, crash-safe) + analyzer. The injector must not be able to flip `status='active'` until B1's runtime_sha is confirmed in prod. This is the structural enforcement of Phase A's P0-CROSS-2 deferred-flip contract.

- **P0-CROSS-2 (`substrate_violations` synthetic marker)** [OCR + PM]: mig 315 did not add it. A follow-on migration (316+) must add `substrate_violations.synthetic_run_id UUID NULL` (or boolean), and the substrate engine UPSERT must stamp it for `synthetic-` sites. Lands in B1. Without it the prod audit table is permanently polluted by the soak.

- **P0-CROSS-3 (injector PHI-payload freeze + CI pin)** [Carol + Attorney]: injector `incidents.details` payload frozen to `{soak_test, soak_run_id, invariant_target, seed_severity}`, pinned by a source-scanning CI test. Counsel Rule 2 compiler-rule discipline. Lands in B2.

### P1 (close before the sub-phase's Gate B, or carry as named followup task in the same commit)

- **P1-CROSS-1 (`assertions.py:2802` engine carve-out)** [Steve]: verify it's not the L2-orphan path; `# noqa: synthetic-allowlisted` not a filter. Pin with `test_l2_orphan_invariant_includes_synthetic_sites.py`. B1.
- **P1-CROSS-2 (injector crash-safety)** [Steve + Maya]: atexit/signal handler resets `status='inactive'`, stamps `removed_at`; next-run reconciliation of orphan rows. INSERT(incidents)+INSERT(seed) and DELETE(incidents)+UPDATE(removed_at) each in one transaction. B2.
- **P1-CROSS-3 (analyzer collapse-awareness)** [OCR]: the `(invariant, site_id)` collapse means ~1 violation row, not 481. Analyzer's per-seed `detected_at` = tick the incident_id first enters `details->'matches'`. Spell this out in the B2 analyzer design. B2.
- **P1-CROSS-4 (4h-monitor automation)** [Coach]: injector heartbeat self-aborts + stamps `status='aborted'` if no violation row within 2 ticks of a seed. B2.

### P2 (do during the B1 callsite audit)

- **P2-CROSS-1 (ratchet baseline = 21, classify don't count-all)** [Steve + PM]: `test_synthetic_site_filter_universality.py` must auto-skip site_id-scoped (169) and org-scoped (24) windows; ratchet only the ~21 enumeration callsites. A count-all-214 gate is noise.
- **P2-CROSS-2 (`compliance_bundles` NOT VALID CHECK)** [Maya]: optional manual `VALIDATE CONSTRAINT` follow-up; not a Phase B blocker.

---

## Phase B sub-phasing recommendation

**B1 — filter rollout + isolation hardening (~1.0–1.25 eng-day)**
1. Add `synthetic = FALSE` to the ~21 truly-unscoped enumeration callsites (`routes.py` ×11, `flywheel_federation_admin.py` ×2, `integrations/tenant_isolation.py` ×3, `client_portal.py`, `notifications.py`, `partners.py`, `db_queries.py`, `provisioning.py`).
2. `# noqa: synthetic-allowlisted` carve-out for `assertions.py:2802` (engine — VERIFY first) + optional defense-in-depth on the 24 org-scoped.
3. New migration (316+): `substrate_violations.synthetic_run_id UUID NULL` + engine UPSERT stamps it.
4. CI gates: `test_synthetic_site_filter_universality.py` (baseline 21, classifying), `test_auditor_kit_refuses_synthetic_site.py`, `test_l2_orphan_invariant_includes_synthetic_sites.py`.
5. Full pre-push sweep → push → CI green → **deploy-verify `curl /api/version` runtime_sha == B1 commit.**
6. Gate B fork review on the B1 diff.

**B2 — injector + analyzer (~1.25–1.5 eng-day) — BLOCKED until B1 deploy-verified**
1. `scripts/substrate_mttr_soak_inject_v2.py`: owns `status='active'` flip at startup; crash-safe cleanup resets to `inactive`; PHI-payload frozen; 4h-monitor auto-abort; one sev2-long seed; end-time-anchored scheduling; no `--resume-run-id`.
2. `scripts/substrate_mttr_soak_report_v2.py`: collapse-aware per-seed join via `details->'matches'`; p50/p95/p99 detect + resolve.
3. CI pin: injector payload-key allowlist test.
4. `--dry-run` real smoke (seed → wait → assert violation → delete → wait → assert resolved).
5. Gate B fork review on the B2 diff. Commit body cites B1 deploy-verification SHA.

Then design's Phase C (1h smoke) → Gate A2 → Phase D (24h) → Phase E (cleanup).

---

## Top P0 / P1

**Top P0:** B1/B2 split with B1-deploy-verified-before-B2-injector enforcement (P0-CROSS-1). Everything else is a smaller blast radius; this one re-opens the entire v1 contamination class if missed.

**Top P1:** `substrate_violations` synthetic marker migration in B1 (P0-CROSS-2 — graded P0 by OCR/PM but the *timing* is the P1 risk: it must be in B1, not slipped to B2, or the injector opens unmarked rows).

---

## Final overall verdict

**APPROVE-WITH-FIXES — split Phase B into B1 (filter rollout + 3 CI ratchets + `substrate_violations` marker migration) and B2 (injector + analyzer).**

Phase A's schema (mig 315) is verified correct as shipped — FK, CHECK, NOT VALID constraint, removed status-flip all confirmed on disk. The Phase B design's direction is sound; the defects are scoping and sequencing, not architecture:

1. **The real enumeration-callsite count is 21**, not the design's 35-50 nor Gate A v2's 212. The CI ratchet must classify (skip 169 site_id-scoped + 24 org-scoped), not count-all.
2. **Phase B must split.** The injector flips `status='active'`; it cannot ship in the same phase as the filters it depends on. B1 deploy-verifies green, then B2's injector runs. This is the structural form of Phase A's deferred-flip contract.
3. **Three carry-forward P1s from Phase A Gate A v2 land in Phase B** — `substrate_violations` marker (B1), injector PHI-payload freeze (B2), 4h-monitor automation (B2). All re-confirmed; none deferrable past their sub-phase's Gate B.

No redesign required. Close P0-CROSS-1/2/3 before the relevant sub-phase ships; close P1s before each sub-phase's Gate B or carry as named TaskCreate followups in the same commit. **B1 and B2 each get their own Gate B fork review** (Session 220 two-gate rule) — do not bundle.
