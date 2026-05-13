# Substrate-MTTR Soak — v2 Design

**Status:** DRAFT for Gate A (pre-execution adversarial review)
**Author:** plan-24 v2 author
**Date:** 2026-05-13
**Supersedes:** `.agent/plans/24-substrate-mttr-soak-2026-05-11.md` (v1, Gate A BLOCK 2026-05-11)
**Prior review:** `audit/coach-phase4-mttr-soak-review-2026-05-11.md` (6 P0 + 8 P1 + 5 P2)

---

## §0 — Pre-amble: what v1 got wrong (one paragraph)

v1 injected raw `incidents` rows + auto-UPDATEd them to `status='resolved'` on
a per-severity window and called the wall-clock delta "MTTR." Coach review proved
this measured the INJECTOR'S OWN scheduling latency, not the substrate loop:
(a) no substrate invariant fires on raw `incidents` (the closest, `l2_resolution_without_decision_record`, only fires on `resolution_tier='L2'` which the injector never set), so the engine never opened a `substrate_violations` row to MEASURE in the first place; (b) the auto-resolve window was 24×–180× smaller than the SLA window, so every measurement was mechanically bounded below SLA regardless of engine health; (c) mig 303 placed the synthetic site at `status='online'` and contaminated `/api/fleet`, `/admin/metrics`, `recurrence_velocity_loop`, and the federation tier-org enumeration with no query-path filters in place. **v2 inverts the design: instead of injecting incidents and hoping the engine sees them, v2 directly seeds the SHAPE that the engine queries against, then measures the wall-clock from seed → `substrate_violations.detected_at` → `last_seen_at` → `resolved_at`.**

---

## §1 — Scope (what changed from v1)

### v1 → v2 deltas

| Dimension | v1 | v2 |
|---|---|---|
| What we inject | Raw `incidents` rows | An `l2_decisions`-orphan SHAPE that `_check_l2_resolution_without_decision_record` actually fires on |
| What we measure | `resolved_at - reported_at` on `incidents` | `substrate_violations.detected_at - synthetic_seed.created_at` AND `resolved_at - last_seen_at` |
| Closure path | Injector UPDATEs `incidents.status='resolved'` directly | Injector deletes the seed; engine sees the invariant query return empty; hysteresis + tick closes the violation |
| Isolation | Synthetic site `status='online'` (LEAKED everywhere) | New table `substrate_synthetic_seeds` + dedicated `synthetic = TRUE` flag on `sites`; ALL admin/flywheel/federation queries filter via CI gate |
| Alert suppression | Promised env (unimplemented in v1) | `SUBSTRATE_ALERT_SOAK_SUPPRESS` SHIPPED in `alertmanager_webhook.py:122` post-v1 — v2 reuses it |
| Auto-resolve window | 10min/30min/4h per sev (bounded SLA) | NO auto-resolve. Seeds are deleted on a *separately tunable* lifecycle (default: never within the soak); hysteresis (`RESOLVE_HYSTERESIS_MINUTES=5`) is the engine constant we're measuring against |

### IN scope
- Prove the substrate engine opens a `substrate_violations` row within one tick (≤60s + hysteresis) of a synthetic seed appearing.
- Prove `_check_substrate_sla_breach` (the META-invariant) correctly fires sev2 when a synthetic violation is held open past its severity SLA.
- Prove `alertmanager_webhook` honors the `SUBSTRATE_ALERT_SOAK_SUPPRESS` env (no operator paging).
- Verify the engine resolves the violation within `RESOLVE_HYSTERESIS_MINUTES + 1 tick` (≤6min) of seed removal.

### OUT of scope
- L1/L2 healing-tier latency (separate task; that's the flywheel, not the substrate engine).
- Email-tier delivery latency (sev1 SMTP delivery time is bounded by sendgrid, not us).
- Sev3 paging-tier verification — by design `SUBSTRATE_ALERT_MIN_SEVERITY=sev2` drops sev3 (Session 219). v2 carves sev3 out of the email-path SLA verdict; the substrate engine still fires on sev3, just no email.
- Real-customer site impact — synthetic site has `synthetic=TRUE` and is excluded from every customer-facing surface.

---

## §2 — Synthetic-invariant SHAPE: `l2_decisions`-orphan seed

### Why this invariant

`_check_l2_resolution_without_decision_record` (assertions.py:1101) was specifically called out by Gate A as "the invariant the engine actually fires on" if you give it the right shape. The query is:

```sql
SELECT i.id::text, i.site_id, i.resolved_at
  FROM incidents i
 WHERE i.resolution_tier = 'L2'
   AND i.status = 'resolved'
   AND i.resolved_at > NOW() - INTERVAL '7 days'
   AND NOT EXISTS (SELECT 1 FROM l2_decisions ld WHERE ld.incident_id = i.id::text)
 LIMIT 50
```

To make this fire, the seed must be a row in `incidents` with:
- `resolution_tier = 'L2'`
- `status = 'resolved'`
- `resolved_at` within the last 7 days
- `site_id = 'synthetic-mttr-soak'` (already exists, quarantined)
- NO matching `l2_decisions.incident_id`
- `details->>'soak_test' = 'true'` (filter marker)
- `details->>'soak_run_id' = '<uuid>'` (run correlation)

### Why this is safer than v1

- The invariant already exists in prod; v2 does NOT add new invariants or trigger functions.
- The seed is a real INSERT into `incidents`, but `synthetic=TRUE` on the site filters it out of every customer-facing surface (see §4).
- Mig 300/301/302 already backfilled real prod orphans; the synthetic-marker carve-out is the same pattern.
- Closure is asymmetric: deleting the seed makes the engine's invariant query return empty for that key → hysteresis-timer → resolve. We never UPDATE `incidents.status` to close.

<!-- mig-claim: 315 task:#98 -->

### Migration design (mig 315 `substrate_mttr_soak_v2`)

```sql
BEGIN;

-- 1. Add synthetic flag to sites table (idempotent).
ALTER TABLE sites ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_sites_synthetic ON sites (synthetic) WHERE synthetic = TRUE;

-- 2. Mark the v1-quarantined synthetic site (mig 304 left it at status='inactive').
-- v2 flips it BACK to status='active' AND synthetic=TRUE so the substrate engine
-- ticks against its incidents (status filter in routes.py:151 still excludes it
-- because the new filter is `synthetic = FALSE` at every callsite — see §4).
UPDATE sites
   SET synthetic = TRUE,
       status = 'active',
       updated_at = NOW()
 WHERE site_id = 'synthetic-mttr-soak';

-- 3. New seed-tracking table. NOT incidents — incidents is the SHAPE we inject;
-- this table is the ROUND-TRIP audit (seed lifecycle for the analyzer).
CREATE TABLE IF NOT EXISTS substrate_synthetic_seeds (
    seed_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    soak_run_id    UUID NOT NULL,
    invariant_name TEXT NOT NULL,        -- 'l2_resolution_without_decision_record' (v2 only one)
    site_id        TEXT NOT NULL REFERENCES sites(site_id),
    incident_id    UUID,                  -- FK to incidents.id; nullable so we can record
                                          -- pre-INSERT timing if we ever need it
    severity_label TEXT NOT NULL,         -- sev1/sev2/sev3
    seeded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    removed_at     TIMESTAMPTZ,           -- set when injector deletes the underlying row
    -- Engine-observed timestamps (populated by analyzer, NOT by injector):
    detected_at    TIMESTAMPTZ,           -- substrate_violations.detected_at for the matching invariant+site
    resolved_at    TIMESTAMPTZ,           -- substrate_violations.resolved_at after removal
    CONSTRAINT synthetic_seeds_site_synthetic CHECK (site_id LIKE 'synthetic-%')
);
CREATE INDEX idx_synthetic_seeds_run ON substrate_synthetic_seeds (soak_run_id, seeded_at);

-- 4. New runs table (replaces v1's substrate_mttr_soak_runs which was schema-OK
-- but tied to the v1 measurement model). v1 table preserved for archeology.
CREATE TABLE IF NOT EXISTS substrate_mttr_soak_runs_v2 (
    soak_run_id    UUID PRIMARY KEY,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at       TIMESTAMPTZ,
    config         JSONB NOT NULL,
    status         TEXT NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running','completed','aborted','quarantined')),
    detect_p50_seconds   NUMERIC,
    detect_p95_seconds   NUMERIC,
    detect_p99_seconds   NUMERIC,
    resolve_p50_seconds  NUMERIC,
    resolve_p95_seconds  NUMERIC,
    resolve_p99_seconds  NUMERIC,
    summary        JSONB
);

-- 5. Audit-log named operator (not 'system:mig-311').
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES (
    'jbouey2006@gmail.com',
    'substrate_mttr_soak_v2_install',
    'mig:311',
    jsonb_build_object(
        'supersedes', '303_substrate_mttr_soak + 304_quarantine',
        'design_doc', 'audit/substrate-mttr-soak-v2-design-2026-05-13.md'
    ),
    NOW()
);

COMMIT;
```

**Idempotency notes:** `ALTER TABLE ... IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`, the `UPDATE sites` is keyed by `site_id` so it's a no-op on replay. The `INSERT INTO admin_audit_log` is unconditional (per P2-5 from Gate A v1: accepted as append-only noise).

---

## §3 — Auto-resolve window decoupling

### v1's bug

v1 set `RESOLUTION_WINDOW_SECONDS = {critical: 600, medium: 1800, low: 14400}` and auto-UPDATEd `incidents.status='resolved'` per-tick. The analyzer then measured `resolved_at - reported_at`. Since the resolve window was ALWAYS smaller than the SLA window, the verdict was mechanically green even if the engine was dead.

### v2's fix: the engine, not the injector, owns closure

**Seed lifecycle:**
1. **t=0:** injector INSERTs an `incidents` row with the orphan shape into `synthetic-mttr-soak` site.
2. **t=0+seed:** injector records a row in `substrate_synthetic_seeds` with `seeded_at=NOW()`.
3. **t≤60s (one tick):** substrate engine `run_assertions_once` evaluates `_check_l2_resolution_without_decision_record`, sees the synthetic key, INSERTs a `substrate_violations` row → this is `detected_at` (measure 1).
4. **t=hold_seconds:** seed lives undisturbed. The engine refreshes the violation every tick (`last_seen_at` updates). If `hold_seconds` exceeds the severity SLA (sev1=4h, sev2=24h, sev3=30d), `_check_substrate_sla_breach` fires sev2 (measure 2 — the META-invariant works).
5. **t=hold_seconds+ε:** injector DELETEs both the `incidents` row and the `l2_decisions` (none was ever created, so this is a no-op) AND sets `substrate_synthetic_seeds.removed_at=NOW()`.
6. **t=hold_seconds+ε+60s:** next tick — engine sees the invariant query no longer returns the key. The open `substrate_violations` row is NOT immediately resolved; it must wait `RESOLVE_HYSTERESIS_MINUTES=5` (assertions.py:6076).
7. **t=hold_seconds+ε+5min+1tick:** engine UPDATEs `substrate_violations.resolved_at=NOW()` (measure 3).
8. Analyzer joins `substrate_synthetic_seeds.removed_at` ↔ `substrate_violations.resolved_at` → `resolve_latency_seconds`. Expected: 300s ≤ resolve_latency ≤ 360s.

### Key invariants

- **Hold-time is per-seed configurable, NOT per-severity.** Sev3 doesn't get 30 days of hold; sev3 gets ~10 minutes just like sev1+sev2 (we're measuring the engine, not the SLA-defined deadline). The SLA verdict is "did detect latency stay below tick granularity" not "did the seed close inside its SLA."
- **No auto-UPDATE of `incidents.status`.** The injector OWNS create + delete; the ENGINE owns open + refresh + resolve. Each side proves its work.
- **Hold variation:** to actually exercise `_check_substrate_sla_breach`, ONE seed per run is intentionally held for `sev2_sla + 5min = 24h05min`. The analyzer verifies the META-invariant fired exactly once for that seed.

### What this proves

A green v2 report proves: (a) the engine sees synthetic data within one tick (detection works); (b) the engine releases synthetic data within hysteresis + one tick (resolution works); (c) the META-invariant fires when ANY violation stays open past SLA (escalation works); (d) the alert path correctly suppresses synthetic alerts (`SOAK_SUPPRESS`). None of these were true in v1.

---

## §4 — Isolation: synthetic data NEVER appears in operator dashboards

### Root cause of v1 contamination

v1's mig 303 set `status='online'` and used a string-marker (`details->>'soak_test'='true'`) for filtering. The marker was promised at the design level but ZERO callsites filtered on it. Result: `/api/fleet`, `/admin/metrics`, `recurrence_velocity_loop`, federation tier-org all leaked.

### v2 isolation pattern: column-level boolean + CI ratchet

**1. Schema-level marker (preferred over JSONB):**
```sql
ALTER TABLE sites ADD COLUMN synthetic BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX idx_sites_synthetic ON sites (synthetic) WHERE synthetic = TRUE;
```
A column survives `site_id` rename (mig 257 `rename_site()` rule). A JSONB marker doesn't.

**2. Mandatory filter at every site-enumeration callsite:**
Every query that enumerates sites for ANY admin / partner / client / flywheel / federation purpose MUST add `AND s.synthetic = FALSE` (or `AND NOT s.synthetic`). The CI gate `tests/test_synthetic_site_filter_universality.py` ratchets this — baseline `N` callsites, must monotonically rise as new code adds filters, regression = test fail. The gate scans for:
- `FROM sites s` (with `s` alias) or `FROM sites ` (no alias)
- WHERE clause does NOT contain `synthetic` within the next 30 lines OR a `# noqa: synthetic-allowlisted` per-line escape

**3. Invariant carve-out:** the substrate engine MUST tick on the synthetic site (that's the whole point). The L2-orphan invariant query already filters by site_id implicitly (per-row); v2 adds NO filter on `synthetic`. The carve-out is for OPERATOR-VISIBLE surfaces, not for the engine itself.

**4. Flywheel / federation explicit exclusion:**
- `background_tasks.py:recurrence_velocity_loop` → add `AND s.synthetic = FALSE` to the join.
- `flywheel_federation_admin.py:198-205` (federation tier-org enumeration) → add same filter.
- `client_portal.py` + `partners.py` site queries → MUST filter (org-RLS won't help because the synthetic site has no real partner_id/client_org_id, but defense in depth).

**5. Audit-log scrub:** every soak-related audit-log entry uses `username='jbouey2006@gmail.com'` (named human, per CLAUDE.md privileged-chain rule). No `system:soak-test`. No `system:mig-N`.

**6. Auditor-kit safety (Maya RT verification):** synthetic site has zero `compliance_bundles` rows by construction. The auditor-kit auth gate (`require_evidence_view_access`) refuses synthetic site_ids in the URL via a new `synthetic=FALSE` check at the require_evidence_view_access entry. CI test: `test_auditor_kit_refuses_synthetic_site`.

### Verification SQL (run pre-soak, post-soak)

```sql
-- Pre-soak: confirm zero customer-facing surface sees synthetic.
SELECT 'fleet_view' AS surface, COUNT(*) FROM (
    -- duplicate /api/fleet query with the new synthetic filter:
    SELECT site_id FROM sites WHERE status != 'inactive' AND synthetic = FALSE
                              AND site_id = 'synthetic-mttr-soak'
) x
UNION ALL
SELECT 'recurrence_velocity', COUNT(*) FROM incident_recurrence_velocity
 WHERE site_id = 'synthetic-mttr-soak'
UNION ALL
SELECT 'federation_candidates', COUNT(*) FROM (
    SELECT DISTINCT client_org_id FROM sites
     WHERE client_org_id IS NOT NULL AND status != 'inactive'
       AND synthetic = FALSE AND site_id = 'synthetic-mttr-soak'
) x;
-- All three MUST be 0 before the soak starts and after it ends.
```

---

## §5 — Soak profile

### Severities + cadence

| Severity | Seed rate | Hold duration | Purpose |
|---|---|---|---|
| sev1 | 1 seed every 30min (48 over 24h) | 10 min each | Tight detect+resolve loop validation |
| sev2 | 1 seed every 10min (144 over 24h) | 15 min each | Main detection coverage |
| sev2-long | 1 seed for the WHOLE run (24h05min hold) | Held past SLA | Exercises `_check_substrate_sla_breach` |
| sev3 | 1 seed every 5min (288 over 24h) | 10 min each | Volume + alertmanager-suppression validation |
| **Total** | **481 seeds** | — | ~3× v1's effective rate, but ALL in measurable shape |

Caveat: the L2-orphan invariant has a `LIMIT 50`. If we seed faster than the engine can drain (50 sites per tick), seeds queue. v2 sticks to ONE synthetic site, so only 1 violation row is open per tick regardless of seed count → seed count doesn't hit the LIMIT. Multiple-severity seeds would each appear as separate INVARIANT rows? — NO, the invariant is keyed on `(invariant_name, site_id)` per the engine's collapse logic (assertions.py:6155). v2 collapses to ONE violation row per (invariant, site), and the `details->>'matches'` array accumulates the synthetic incident_ids. The analyzer reads `details.matches` for per-seed correlation.

### Total duration

24 hours + 5min cooldown for hysteresis closure verification.

### Expected MTTR distribution

| Metric | Expected | Acceptable | Fail |
|---|---|---|---|
| detect_p50_seconds | ≤30s (half a tick) | ≤60s | >120s |
| detect_p95_seconds | ≤60s | ≤120s | >180s |
| detect_p99_seconds | ≤90s | ≤180s | >240s |
| resolve_p50_seconds | ~330s (hysteresis+half-tick) | ≤360s | >480s |
| resolve_p95_seconds | ~360s | ≤420s | >540s |
| resolve_p99_seconds | ~390s | ≤480s | >600s |
| `substrate_sla_breach` firings | EXACTLY 1 (the sev2-long seed) | 1 | 0 or ≥2 |
| `alertmanager_webhook` pages sent | 0 (SOAK_SUPPRESS on) | 0 | ≥1 |

---

## §6 — Pass/fail criteria

**PASS (all must hold):**
1. detect_p99 ≤ 180s for sev1, sev2, sev3.
2. resolve_p99 ≤ 480s for sev1, sev2, sev3.
3. EXACTLY 1 `substrate_sla_breach` violation opened during the soak, referencing the sev2-long seed.
4. ZERO `alertmanager_webhook` page-events for `labels.soak_test='true'` (SOAK_SUPPRESS verified via webhook audit log).
5. Pre-soak + post-soak verification SQL returns 0 across all 3 surfaces (fleet, recurrence_velocity, federation).
6. CI gate `test_synthetic_site_filter_universality` passes against the post-soak diff.
7. ZERO rows in `incident_recurrence_velocity` keyed by `synthetic-mttr-soak`.
8. ZERO rows in `compliance_bundles` keyed by `synthetic-mttr-soak`.
9. The synthetic site appears on `/admin/substrate-health` (it's the engine target) but NOT on `/api/fleet` or `/admin/metrics`.

**FAIL (any one triggers BLOCK + redesign):**
- Any P99 exceeds the fail threshold.
- `substrate_sla_breach` fires 0 times (META-invariant broken) OR ≥2 times (uncontrolled).
- Any alert page reaches `ALERTMANAGER_RECIPIENTS` during the soak.
- Any operator surface (CI scan or manual) shows synthetic data.

---

## §7 — Phased implementation

### Phase A — Migration + isolation
1. Write mig 315 (§2).
2. Add `synthetic` column filter to:
   - `routes.py:138-175` (/api/fleet)
   - `routes.py:2120-2143` (/admin/metrics trending)
   - `background_tasks.py:1149-1182` (recurrence_velocity_loop)
   - `flywheel_federation_admin.py:198-205` (federation tier-org)
   - Any `client_portal.py` + `partners.py` site enumeration
3. CI gate `test_synthetic_site_filter_universality.py` — ratchet baseline.
4. CI gate `test_auditor_kit_refuses_synthetic_site.py`.
5. Local pre-push: full sweep + dry-run mig 315 on a copy DB.

### Phase B — Injector + analyzer rewrite
1. `scripts/substrate_mttr_soak_inject_v2.py`:
   - INSERTs orphan-shape incidents only.
   - Tracks each seed in `substrate_synthetic_seeds`.
   - Per-seed hold timer; deletes the row when hold expires.
   - One sev2-long seed lives for run-duration + 5min.
   - End-time-anchored tick scheduling (P2-1 from v1 review).
   - REMOVES `--resume-run-id` (P1-1: was non-functional; v2 doesn't pretend).
2. `scripts/substrate_mttr_soak_report_v2.py`:
   - Joins `substrate_synthetic_seeds` ↔ `substrate_violations` on (invariant_name, site_id) and `details->'matches'` containment.
   - Computes detect_p50/p95/p99 and resolve_p50/p95/p99.
   - Counts `substrate_sla_breach` firings keyed on `details->>'breached_invariant'='l2_resolution_without_decision_record'`.
   - Queries `alertmanager_webhook` audit log for `dropped` counter under SOAK_SUPPRESS.
   - Outputs markdown + JSON.
3. `--dry-run`: injects ONE sev3 seed, waits 120s, asserts `substrate_violations` row exists, deletes seed, waits 360s, asserts `resolved_at IS NOT NULL`. THIS is what dry-run should mean (P1-8 fix).

### Phase C — Smoke run (1 hour)
1. `SUBSTRATE_ALERT_SOAK_SUPPRESS=true` set in env.
2. Run injector for 1h with reduced cadence (1 sev1, 6 sev2, 12 sev3 — 19 seeds total).
3. Run analyzer.
4. Manual review of substrate-health dashboard.
5. Verify zero alert emails sent.
6. **Gate A2:** if smoke passes, re-fork the 4-lens review (Steve/Maya/Carol/Coach) on the AS-IMPLEMENTED artifacts (mig 315 applied + injector + analyzer output). This is the Gate B Session 220 rule — design + implementation are independently reviewed.

### Phase D — 24h soak
1. Run with full profile (§5).
2. Monitor `/admin/substrate-health` every 4h via curl assertion (one named operator).
3. End-of-soak: run analyzer + post-soak verification SQL.
4. Attach report to task #98.

### Phase E — Cleanup
1. Delete all `substrate_synthetic_seeds` rows for the soak_run_id (audit-archived in `substrate_mttr_soak_runs_v2.summary`).
2. Wait `RESOLVE_HYSTERESIS_MINUTES + 60s` for the engine to close any remaining open violations.
3. Verify `substrate_violations WHERE site_id='synthetic-mttr-soak' AND resolved_at IS NULL` → 0 rows.
4. Mark run `status='completed'`.

---

## §8 — Open questions for user-gate

Before sending this to Gate A fork review:

1. **Sev2-long seed approach for `substrate_sla_breach`:** holding ONE seed for 24h05min is the minimum to exercise the META-invariant. Acceptable? Alternative: a separate 26h soak just for the META-invariant — cleaner but longer. **Recommendation: stick with the one-seed approach; if the META-invariant breaks during the 24h window we'll see it.**

2. **CI gate baseline number:** what's the initial ratchet count for `test_synthetic_site_filter_universality.py`? Need to grep all `FROM sites` callsites and audit each — Phase A deliverable. **Best-guess: 35-50 callsites; will confirm in Phase A.**

3. **Federation tier-org filter:** `flywheel_federation_admin.py:198-205` joins on `client_org_id IS NOT NULL`. Should v2 add `synthetic=FALSE` to the JOIN line per the Session 218 RT33 rule, or to a separate WHERE clause? **Recommendation: on the JOIN line (gate's window heuristic anchors there).**

4. **`compliance_bundles` write-side guard:** today nothing prevents a future endpoint from writing a synthetic-site compliance_bundle. Add a CHECK constraint? Mig 311 could include:
   ```sql
   ALTER TABLE compliance_bundles ADD CONSTRAINT no_synthetic_bundles
       CHECK (site_id NOT LIKE 'synthetic-%') NOT VALID;
   ```
   `NOT VALID` avoids the 232K-row backfill scan. **Open: should v2 include this or defer to a follow-up task?**

5. **What if `_check_l2_resolution_without_decision_record` itself changes during the soak?** Mig 300/301/302 backfilled real prod orphans into `l2_decisions`. If a future migration narrows the invariant's WHERE clause (e.g., adds `AND site_id NOT LIKE 'synthetic-%'`), v2 stops working. **Mitigation: v2 design doc + mig 315 comment block specifically calls out that the L2-orphan invariant query MUST continue to fire on `synthetic-mttr-soak` site_id; add a CI test that the invariant's query body does NOT exclude synthetic sites.**

6. **Should v2 also exercise a sev1 invariant?** All L2-orphan firings are sev2 (per assertions.py registration). To exercise the sev1 SLA (4h) path of `substrate_sla_breach`, we'd need a synthetic seed against a sev1 invariant. Candidates: `_check_install_loop` (sev1), `_check_offline_appliance_long` (sev1). **Recommendation: scope v2 to sev2 only; v3 can add a sev1 invariant once v2 ships clean. Reduces blast radius.**

7. **Concurrent forks:** Gate A fork must run with `isolation=worktree` per `feedback_parallel_fork_isolation.md`. Confirm the brief explicitly mandates repo-relative paths only (per `feedback_worktree_isolation_breach_lesson.md`).

---

## Appendix A — File touch-list

| File | Change | Why |
|---|---|---|
| `mcp-server/central-command/backend/migrations/311_substrate_mttr_soak_v2.sql` | NEW | §2 mig |
| `mcp-server/central-command/backend/routes.py` | EDIT | Add `synthetic=FALSE` filter (2 callsites) |
| `mcp-server/central-command/backend/background_tasks.py` | EDIT | Add filter to `recurrence_velocity_loop` |
| `mcp-server/central-command/backend/flywheel_federation_admin.py` | EDIT | Add filter to tier-org enumeration |
| `mcp-server/central-command/backend/client_portal.py` + `partners.py` | EDIT | Defense-in-depth filter |
| `scripts/substrate_mttr_soak_inject_v2.py` | NEW | Replaces v1 injector |
| `scripts/substrate_mttr_soak_report_v2.py` | NEW | Replaces v1 analyzer |
| `tests/test_synthetic_site_filter_universality.py` | NEW | CI gate |
| `tests/test_auditor_kit_refuses_synthetic_site.py` | NEW | CI gate |
| `tests/test_l2_orphan_invariant_includes_synthetic_sites.py` | NEW | CI gate (Q5) |
| `tests/test_substrate_mttr_soak_v2_smoke.py` | NEW | Dry-run smoke test |
| `audit/substrate-mttr-soak-v2-design-2026-05-13.md` | THIS FILE | — |

## Appendix B — Closed v1 findings table

| v1 finding | v2 closure |
|---|---|
| P0-1 (no invariant fires) | §2 — seed shape is the EXACT shape `_check_l2_resolution_without_decision_record` fires on; engine ticks against synthetic site (no carve-out at the invariant level) |
| P0-2 (auto-resolve bounds measurement) | §3 — injector no longer auto-resolves; engine owns closure via hysteresis; we measure hysteresis (5min) NOT SLA (4h-30d) |
| P0-3 (SOAK_SUPPRESS unimplemented) | SHIPPED post-v1 at `alertmanager_webhook.py:122` — v2 reuses; §6 verifies via webhook audit |
| P0-4 (admin surfaces contaminated) | §4 — column-level `synthetic` flag + CI ratchet gate + filters at 5+ callsites |
| P0-5 (flywheel recurrence pollution) | §4 — explicit `recurrence_velocity_loop` filter; §6 verifies post-soak |
| P0-6 (federation tier-org pollution) | §4 — explicit `flywheel_federation_admin` filter; §6 verifies |
| P1-1 (`--resume-run-id` broken) | §7 Phase B — flag REMOVED, not pretended |
| P1-2 (analyzer `high` count=0) | §7 Phase B — analyzer reads severity from `substrate_violations.severity` directly, no `high` bucket |
| P1-3 (detect-latency not measured) | §3 — `substrate_synthetic_seeds.detected_at` is the primary measurement |
| P1-4 (failure-mode → metric mapping) | §6 — 9 pass criteria, each mapped to a single failure mode |
| P1-5 (sev3 paging impossible) | §1 — sev3 carved out of email-path SLA; substrate detection still measured |
| P1-6 (`resolution_tier='monitoring'` chain-gap) | §3 — v2 doesn't UPDATE `resolution_tier` at all; injector only CREATEs + DELETEs |
| P1-7 (dead code in injector) | §7 — v2 injector is a rewrite; no carryover |
| P1-8 (degenerate dry-run) | §7 Phase B — dry-run waits for substrate tick + verifies open + verifies resolve |
| P2-1 (timing drift) | §7 Phase B — end-time-anchored sleep |
| P2-2 (`system:mig-303` actor) | §2 — `jbouey2006@gmail.com` for mig 315 |
| P2-3 (bogus MAC `00:00...`) | mig 304 dropped the synthetic appliance; v2 doesn't recreate it (the synthetic site has zero appliances by design) |
| P2-4 (severity-key mismatch) | §5 — table explicitly maps sev1/sev2/sev3 to seed cadences + hold durations |
| P2-5 (mig 303 audit-log not idempotent) | §2 — accepted as append-only noise per Gate A reviewer guidance |
