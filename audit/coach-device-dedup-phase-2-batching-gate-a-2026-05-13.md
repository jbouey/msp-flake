# Gate A — Phase 2 Batching Strategy: canonical_devices reader migration

**Date:** 2026-05-13
**Reviewer:** Class-B 7-lens fork (Steve / Maya / Carol / Coach / Auditor n/a / PM / Attorney n/a)
**Scope:** Task #74 — migrate 17 customer-facing `FROM discovered_devices` readers (per `BASELINE_MAX=22` in the ratchet test; brief enumerated 21 with 2 in EXEMPT) to canonical_devices.
**Phase 1 precedent:** commit `0eae08cc` (compliance_packet + device_sync) shipped 2026-05-13 with CTE-JOIN pattern.

---

## 200-WORD VERDICT SUMMARY

**APPROVE-WITH-FIXES — recommend Option C (customer-vs-operator split, 2 commits) with one structural change: extract a `_FRESHEST_DD_FROM_CANONICAL_CTE` SQL helper-string in `canonical_metrics.py` to prevent per-callsite drift.**

The Phase 1 CTE-JOIN pattern adapts mechanically to ~14 of the enumerated callsites. The remaining 7 are not uniform: **2 are reconciliation/writer (EXEMPT, already in test allowlist), 1 is a primary-key lookup for a write path (SKIP class — id-based, not count-based), 3 are simple `COUNT(*)` queries that should rewrite to `FROM canonical_devices` directly without the JOIN-back (per the brief's hint), and 1 is a deploy-credential JOIN inside the checkin hot path that needs medium care.** Treating all 21 as the same mechanical migration is the BUG-class Gate B would catch.

Option A concentrates risk (1 BLOCK reverts 21 callsites). Option B over-Gates (6 Gate Bs for ~3-callsite commits is enterprise-grade theater at this maturity). Option C cuts on the natural seam: customer-facing impact lands Day 1 (partners + portal + client_portal = 9 readers, the actual ghost-count surface), admin/internal trails behind. Option D's pre-sample is implicitly satisfied by Phase 1 already shipping 2 production callsites under the same pattern — re-sampling is redundant.

---

## PER-CALLSITE MIGRATION RISK ASSESSMENT

Classification rubric: **SIMPLE** = drop-in CTE-JOIN (Phase 1 pattern), **COUNT-ONLY** = rewrite as `FROM canonical_devices` directly (no JOIN-back needed, per brief's hint), **MEDIUM** = needs JOIN to extra tables (sites, v_appliances_current, site_appliances) but still mechanical, **COMPLEX** = hot path / write path / non-standard shape, **SKIP** = not a Phase 2 target (write path / PK lookup / already exempt).

### Customer-facing (Batch 1 — 9 readers)

| Callsite | Shape | Class | Notes |
|----------|-------|-------|-------|
| `partners.py:1892` | SELECT list (id/hostname/ip/type/last_seen/status/owner) LIMIT 200 + ordering | **SIMPLE** | tenant_connection; `mesh assignments` builder; standard Phase 1 CTE shape applies cleanly. owner_appliance_id stays attached via `dd.*` in CTE. |
| `partners.py:2587` | SELECT list w/ JOIN sites + summary aggregate (Carol's RLS path: admin_connection) | **MEDIUM** | Need to JOIN sites onto the CTE result. Pattern: wrap CTE → outer SELECT joins sites + LIMIT/OFFSET. |
| `partners.py:2595` | `SELECT count(*) FROM discovered_devices WHERE site_id = ANY($1)` | **COUNT-ONLY** | Rewrite directly: `SELECT count(*) FROM canonical_devices WHERE site_id = ANY($1)`. NO CTE needed — that's the **whole point** of having a canonical table. |
| `partners.py:2602` | Aggregate FILTER (compliance_status buckets) `FROM discovered_devices WHERE site_id = ANY($1)` | **MEDIUM** | compliance_status is a discovered_devices field (canonical_devices doesn't carry it). Must use CTE-JOIN. Phase 1 precedent in compliance_packet.py:1185 has the deprecated-compliance-status noqa marker pattern. |
| `portal.py:1251` | `COUNT(*) FROM discovered_devices WHERE ... device_type IN ('workstation','server') AND last_seen_at > NOW() - INTERVAL '7 days'` | **MEDIUM** | last_seen_at + device_type both live on canonical_devices. Rewrite as `FROM canonical_devices` directly — NO JOIN-back needed. Brief's exact hint case. |
| `portal.py:2137` | Aggregate compliance_status FILTERs `WHERE site_id = ANY($1)` | **MEDIUM** | Same as partners.py:2602 — compliance_status forces JOIN-back. |
| `client_portal.py:1289` | SELECT list (hostname/ip/type/os_name/compliance_status) for enrichment dict | **SIMPLE** | org_connection (RLS path — Carol's lens applies, verified canonical_devices_tenant_org_isolation policy mig 319:65 covers). |
| `client_portal.py:4732` | Worklist filter (device_status + device_type + compliance_status + hostname NOT LIKE) ORDER BY last_seen_at DESC | **COMPLEX** | org_connection (RLS); has the deprecated-compliance-status noqa marker already. CTE-JOIN pattern works but the WHERE-clause filter has 4 predicates against discovered_devices fields (device_status, device_type, compliance_status, hostname). All survive the CTE — just need to place filters in the outer SELECT, not the CTE inner. |
| `client_portal.py:4805` | **NOT A MIGRATION TARGET** | **SKIP** | `WHERE id = $1 AND site_id = $2` — primary-key lookup for write-path (`register_device` POST). discovered_devices.id ≠ canonical_devices.canonical_id; the client UI was given the discovered_devices.id and expects to write against that row. **Add to test_no_raw_discovered_devices_count EXEMPT_FILES classification as `write_path`, or add inline noqa marker.** |

**Batch 1 total: 8 migrations + 1 SKIP (mark as exempt, baseline -1).**

### Admin / operator (Batch 2 — 8 readers + 2 EXEMPT)

| Callsite | Shape | Class | Notes |
|----------|-------|-------|-------|
| `routes.py:5313` | SELECT list + JOIN sites + LEFT JOIN v_appliances_current | **MEDIUM** | admin_transaction (wave-15). Owner-site JOIN survives via owner_appliance_id in CTE. Same shape as partners.py:2587. |
| `routes.py:5322` | `SELECT count(*) WHERE site_id = ANY($1)` | **COUNT-ONLY** | Same as partners.py:2595. |
| `routes.py:5331` | Aggregate FILTER compliance_status | **MEDIUM** | Same as partners.py:2602. |
| `routes.py:5762` | `COUNT(*) FILTER (device_status='agent_active')` + `(device_status NOT IN ('ignored','archived'))` — network coverage gauge | **MEDIUM** | device_status is discovered_devices-only. CTE-JOIN required. Customer-visible (network coverage % on site dashboard). |
| `routes.py:5857` | hostname/ip/type/os_name/compliance_status enrichment | **SIMPLE** | Identical shape to client_portal.py:1289. |
| `routes.py:6396` | SELECT list (id/mac/ip/hostname/type/vendor/compliance/first_seen/last_seen) ORDER BY last_seen DESC | **MEDIUM** | Note: query uses `first_seen`/`last_seen` (no `_at` suffix) — verify column names on canonical_devices vs discovered_devices BEFORE migration. If discovered_devices columns are `first_seen`/`last_seen` (no suffix) and canonical_devices is `_at`-suffixed, the JOIN-back via CTE picks up the right field automatically (`dd.*`). |
| `routes.py:8592` | SELECT (hostname/os_type/compliance_status/last_seen) for compliance packet | **SIMPLE** | Phase 1 compliance_packet precedent applies almost verbatim — actually, this MAY BE the same handler as compliance_packet's already-migrated reader. Verify it's not a dupe / dead path. |
| `sites.py:1897` | SELECT list w/ LEFT JOIN site_appliances (mesh assignments debug view) | **MEDIUM** | tenant_connection. dd.owner_appliance_id → sa.id JOIN survives CTE. |
| `sites.py:5644` | **DEPLOY HOT PATH** — checkin's pending_deploy step. JOIN site_credentials by hostname pattern | **COMPLEX** | This is inside the customer-facing checkin handler. The query selects `dd.local_device_id` — verify that column exists / is preserved in CTE. The `dd.hostname` is used in a `LIKE` pattern against `sc.credential_name`. CTE-JOIN works but **this is the deploy critical path and warrants a separate test-vector**. |
| `sites.py:7271` | search_site results — hostname/ip/mac ILIKE pattern + LIMIT | **SIMPLE** | tenant_connection. Search is read-only; standard Phase 1 pattern. |
| `background_tasks.py:340` | **EXEMPT** | **SKIP** | reconciliation writer (in EXEMPT_FILES). Brief mis-enumerated. |
| `background_tasks.py:1159` | **EXEMPT** | **SKIP** | unregistered-device-alert email cron — file is EXEMPT in `_EXEMPT_FILES`. Brief mis-enumerated, but **operator-side alert may still benefit from canonical reads** for consistent count vs portal. Recommend: leave as-is for Phase 2 (file is exempt), revisit in Phase 3 if alert/portal counts diverge in soak. |

**Batch 2 total: 9 migrations + 2 EXEMPT confirmed (no baseline impact for these).**

### Brief enumeration vs ratchet baseline reconciliation
- Brief listed 21 callsites to migrate.
- `BASELINE_MAX=22` in test (post-Phase-1).
- After Phase 2 migrations land + SKIPs marked exempt: target = **0** (per BASELINE drive-down plan).
- Actual migrations: 8 (Batch 1) + 9 (Batch 2) = **17**.
- SKIPs: client_portal.py:4805 (1 inline noqa marker added — counts as migrated to baseline ratchet) + 2 background_tasks (already in EXEMPT_FILES — no ratchet impact).
- Final: 17 migrations + 1 SKIP-with-marker + 4 inline-callsite drops in EXEMPT files we don't count → baseline drops 18 (from 22 to 4). Remaining 4 = ones the brief or I haven't enumerated; need 5-line context re-grep before Batch 2 Gate B claims completion.

---

## PER-LENS VERDICT

### 1. Engineering (Steve) — APPROVE-WITH-FIXES

Sampled 3 callsites (partners.py:1892, routes.py:5313, sites.py:1897) + 4 additional shape-sweeps (partners.py:2587/2595/2602/2602, portal.py:1251/2137, client_portal.py:1289/4732/4805). Findings:

- **Phase 1 pattern adapts mechanically to ~80% of callsites.** Five-column CTE-JOIN is the right default.
- **Three callsites are pure COUNT(*)** (partners.py:2595, routes.py:5322, portal.py:1251). For these, JOINing back to discovered_devices is **measurably worse** than just reading `canonical_devices` directly — the CTE forces an extra join that returns the same count. **REQUIRED FIX:** these three rewrite as `SELECT count(*) FROM canonical_devices WHERE ...` with no CTE.
- **One callsite is a PK lookup** (client_portal.py:4805) that should NOT migrate — it's resolving a discovered_devices.id the client UI received. **REQUIRED FIX:** add to EXEMPT_FILES classification or `# noqa: canonical-write-path-lookup` marker.
- **Two callsites are in EXEMPT_FILES** (background_tasks.py × 2). Brief mis-enumerated these. **REQUIRED FIX:** drop them from Phase 2 scope.
- **One callsite (sites.py:5644) is deploy hot path.** CTE-JOIN works but warrants a dedicated test vector. Recommend splitting it into its own micro-commit OR sampling the actual checkin flow against a canonical_devices fixture before merge.

**Recommendation: extract `_FRESHEST_DD_FROM_CANONICAL_CTE` SQL helper-string** in `canonical_metrics.py` so 14+ callsites share the exact CTE shape. Drift between hand-written copies of the CTE is a Class-B bug class waiting for Gate B BLOCK #2.

### 2. Database (Maya) — APPROVE-WITH-FIXES

Performance assessment of CTE-JOIN at scale:

- **canonical_devices_site_ip_mac_idx** (UNIQUE on site_id, ip_address, mac_dedup_key) covers the JOIN predicate. Index-only scan on canonical side + index nested-loop into discovered_devices on (site_id, ip_address, mac_address).
- **Phase 1 pattern adds 1 nested-loop join per row.** At north-valley-branch-2 scale (22 canonical, 36 discovered): negligible. At enterprise scale (~5000 devices/site): 5K × 1 inner-loop step ≈ <50ms. Acceptable.
- **Hot-path concerns:**
  - `portal.py:1251` is in the dashboard's per-page-load hit. Today: 1 SQL hit on discovered_devices with a partial index on `(site_id) WHERE device_type IN ...`. Migrating to canonical without JOIN-back is **net faster** (smaller table, no over-count gymnastics). ✓
  - `routes.py:5762` (network coverage gauge) hit on every site dashboard render. CTE-JOIN doubles SQL cost from ~3ms to ~6ms. Acceptable but worth caching in a future canonical_metrics emitter (task already planned, #50).
  - `sites.py:5644` is in the checkin path — fires once per appliance per ~60s. Adding a CTE-JOIN at this frequency on a busy multi-appliance site = ~25 extra ms/min. Acceptable; not a load-bearing concern.
- **COUNT(*) rewrites (3 callsites)** are net faster — canonical is smaller and statement-cacheable.

**REQUIRED FIX:** add `EXPLAIN (ANALYZE, BUFFERS)` evidence in the Phase 2 commit body for at least 1 representative callsite (suggest partners.py:2587 — most complex shape) so Gate B has runtime evidence, not just diff review.

### 3. Security (Carol) — APPROVE

Sampled 2 RLS-path callsites:

- **client_portal.py:1289** under `org_connection` (RLS). Migration 319 added `canonical_devices_tenant_org_isolation` policy (mig 319:65) using the same `rls_site_belongs_to_current_org(site_id::text)` function as discovered_devices' policy (mig 278). Read MUST succeed — RLS parity verified.
- **client_portal.py:4732** same org_connection RLS path. Same verification.
- **partners.py:2587** uses `admin_connection` — admin bypass policy (mig 319:55) covers.
- **sites.py:1897** uses `tenant_connection(pool, site_id=site_id)` — verify `app.current_site` setting routes through the appropriate canonical_devices policy. Mig 319's policies key on `app.is_admin` and `app.current_org` / `app.current_partner_id`, **NOT `app.current_site`**. tenant_connection sets app.current_org from the site's org — verified path is fine.

**No findings.** Phase 1's mig 319 RLS work was thorough.

### 4. Coach — APPROVE-WITH-FIXES (with required tooling)

Phase 1 produced 3 Gate B BLOCKs (BUG-3 ratchet drift, RLS policy gap on canonical_devices, SQL GroupingError from initial backfill SQL). Phase 2's risk profile is similar but lower-magnitude:

- **Probable Class-B regressions:**
  1. **CTE drift** between 14 hand-written copies → required fix above (helper string).
  2. **Ratchet baseline math errors** if SKIP vs migrated isn't categorized cleanly. Required fix: each Phase 2 commit must update BASELINE_MAX in lockstep with the actual count, AND assert the SKIP files are added to EXEMPT_FILES, not silently allowed.
  3. **GROUP BY/aggregate column-membership errors** when migrating compliance_status FILTER aggregates through the CTE (3 callsites: partners.py:2602, portal.py:2137, routes.py:5331). Hand-rolled FILTER expressions on the CTE result are easy to mis-thread.
  4. **Forgetting to update the `dd_freshest` CTE column list** when an outer SELECT references a discovered_devices column not in the CTE projection. Phase 1 worked around this with `dd.*` — should be the default in Phase 2.

**REQUIRED TOOLING (Gate B prerequisites):**
- (a) `_FRESHEST_DD_FROM_CANONICAL_CTE` helper-string in `canonical_metrics.py` — single source of CTE truth.
- (b) Gate B runs `bash .githooks/full-test-sweep.sh` (per Session 220 lock-in), not just diff-scoped review. Cite pass/fail count in verdict.
- (c) For each commit, runtime verification: at least 1 callsite's response body diffed pre/post against a stable fixture site (north-valley-branch-2 is the canonical test target — 22 canonical / 36 raw → pre-migration shows ghost count, post-migration shows clean count).

### 5. Auditor (OCR) — N/A
Pure refactor of internal data path. No §164.* implication beyond the Counsel Rule 1 invariant already established by Phase 1's canonical-source declaration.

### 6. PM — APPROVE Option C

Time + risk math:
- **Option A** (1 commit, 1 Gate B): ~2.5h coding + ~1h Gate B + BLOCK-revert blast radius across 17 readers if Gate B finds even one Class-B regression. **HIGH variance.** Estimated p50 = 4h, p90 = 8h+ (one Gate B BLOCK + rework).
- **Option B** (6 commits, 6 Gate Bs): ~5h coding + 6 × 30min Gate B forks + 6 × restart overhead. **OVER-GATED.** Estimated p50 = 9h. Each commit's blast radius is small but the cumulative Gate B work is ~3h of pure review for diminishing risk reduction.
- **Option C** (2 commits, 2 Gate Bs): ~3h coding + 2 × 45min Gate B. Customer-visible reads land Day 1; admin/operator reads land Day 1+. **LOW variance + fast customer-facing fix.** Estimated p50 = 5h, p90 = 7h.
- **Option D** (sample-first): adds ~1h sample-validate-fork. Phase 1 IS the sample (2 callsites in prod for 12h). Marginal value: low.

**Recommendation: Option C.** Customer-facing batch lands first (this is the actual ghost-count user complaint surface — partners + portal + client_portal). Admin/operator batch follows with confidence built from Batch 1 production telemetry. User has been on this multi-hour session — Option C balances speed with safety.

### 7. Attorney — N/A
Internal refactor. No customer-facing contract impact.

---

## RECOMMENDED PLAN (Option C — APPROVE-WITH-FIXES)

### Pre-flight (required before any commit)
1. **Extract `_FRESHEST_DD_FROM_CANONICAL_CTE` helper-string** in `canonical_metrics.py` with the 5-line Phase 1 CTE shape + docstring documenting which fields canonical_devices carries vs which are JOIN-back-from-discovered.
2. **Re-grep** to enumerate all `FROM discovered_devices` in scope (5-line marker window from the ratchet test) — current Phase 2 estimate is 17, brief said 21. Reconcile.
3. **Identify the 3 COUNT-ONLY callsites** (partners.py:2595, routes.py:5322, portal.py:1251) — these rewrite without CTE.
4. **Mark client_portal.py:4805 as exempt** (write-path PK lookup) — either inline `# noqa: canonical-write-path-lookup` or add to EXEMPT_FILES with classification.
5. **Confirm background_tasks.py callsites are exempt** — already in EXEMPT_FILES; no action needed. Drop from Phase 2 scope.

### Batch 1 — Customer-facing (9 callsites: partners ×4, portal ×2, client_portal ×3)
- 8 migrations + 1 SKIP (client_portal.py:4805 marker).
- Update `BASELINE_MAX = 22 → 14` in ratchet test.
- Gate B: full sweep + 1 runtime evidence curl (suggest `/api/client/dashboard` against north-valley-branch-2 — show ghost-count gone).
- Customer impact: ghost device counts on partner book-of-business, client portal dashboard, unregistered-devices worklist all corrected Day 1.

### Batch 2 — Admin/operator (8 callsites: routes ×7, sites ×3, minus the 2 background_tasks EXEMPT)
- 9 migrations (note: sites.py has 3 in-scope at 1897/5644/7271; routes.py has 7 at 5313/5322/5331/5762/5857/6396/8592).
- Update `BASELINE_MAX = 14 → 0` (or to whatever residual EXEMPT exempted-but-uncounted leaves).
- Gate B: full sweep + 1 runtime evidence (suggest deploy-path verification at sites.py:5644 — fire a checkin against a canonical-only test site, verify pending_deploys list is correct).
- Admin impact: org-level admin views consistent with customer-facing views.

### Per-commit Gate B requirements (per Session 220 lock-in)
- Full pre-push sweep cited (`bash .githooks/full-test-sweep.sh` pass count).
- 4-lens fork verdict (Steve/Maya/Carol/Coach) at `audit/coach-device-dedup-phase-2-batch{1|2}-gate-b-2026-05-13.md`.
- Runtime evidence (curl + JSON diff or psql before/after row count).
- Ratchet baseline updated in lockstep with actual migration count.

---

## FINAL VERDICT — APPROVE-WITH-FIXES

**Required fixes before Gate B:**

1. **[P0]** Extract `_FRESHEST_DD_FROM_CANONICAL_CTE` helper-string in `canonical_metrics.py` — single CTE source of truth across 14+ callsites.
2. **[P0]** 3 COUNT-only callsites (partners.py:2595, routes.py:5322, portal.py:1251) rewrite as `FROM canonical_devices` directly, **NO CTE-JOIN.**
3. **[P0]** client_portal.py:4805 is SKIP class — add `# noqa: canonical-write-path-lookup` marker + assertion in ratchet test that the marker exists.
4. **[P0]** Brief mis-enumerated 2 callsites in background_tasks.py — these are already in EXEMPT_FILES. Drop from Phase 2 scope.
5. **[P1]** Re-grep enumeration before each batch commit — actual count = 17 not 21.
6. **[P1]** sites.py:5644 (deploy hot path) gets a dedicated runtime test vector at Gate B.
7. **[P1]** Each Phase 2 commit body cites EXPLAIN ANALYZE for ≥1 representative shape.
8. **[P2]** Verify routes.py:8592 isn't a duplicate of compliance_packet's already-migrated reader (dead-path scan).

**Approved batching:** Option C (customer-vs-operator, 2 commits, 2 Gate Bs).

**P0s gate the batch — do not start Batch 1 coding until 1-4 are addressed.**

— end Gate A verdict —
