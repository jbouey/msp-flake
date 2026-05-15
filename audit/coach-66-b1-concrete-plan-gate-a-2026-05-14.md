# Gate A — Task #66 B1 (concrete sub-phase plan) — 7-lens Class-B verdict

**Reviewer:** Coach (fork-mode 7-lens adversarial)
**Date:** 2026-05-14
**Subject:** B1 sub-phase ONLY of MTTR Soak v2 Phase B — the build-ready filter rollout + migration + 3 CI ratchets
**Task:** #66
**Parent:** `audit/coach-mttr-soak-phase-b-gate-a-2026-05-14.md` (APPROVE-WITH-FIXES, mandated B1/B2 split)
**Verdict:** **APPROVE-WITH-FIXES** — build-ready as specified below; 3 P0 corrections to the parent's plan must be folded in before B1 ships.

---

## 300-word summary

The parent Phase B Gate A approved a B1/B2 split and gave a 21-callsite estimate against a *stale* line-numbered tree. This B1 review re-walked the **current** source (211 `FROM sites` occurrences) and the count holds *as a ballpark* but the specific list is materially different — and three of the parent's named callsites are **false positives** that would have caused wrong edits:

1. **`assertions.py:2810` is a comment string** (the `recommended_action` prose of the `org_isolation` invariant), not a query. Do NOT edit.
2. **`assertions.py:942` (`_check_cross_org_relocate_chain_orphan`) and `assertions.py:1377`** are *substrate-engine* queries. The engine MUST tick on the synthetic site — filtering here blinds it. These get `# noqa` carve-outs, never filters. The parent flagged `:2802` for this; the real line is `:942`/`:1377`.
3. **`background_tasks.py:1986` / `:2011` and `partners.py:3761`** are partner-scoped (`WHERE partner_id = $1`) — synthetic site has NULL `partner_id`, self-excludes. Defense-in-depth only.

The verified **truly-unscoped enumeration set is 14 callsites** (not 21) needing a load-bearing `synthetic` filter, plus ~5 defense-in-depth org/partner-scoped and 2 engine carve-outs. The parent over-counted by folding org-scoped lines into the load-bearing set.

**Carol's P0 stands and is the single most important correction:** mig 315 made `sites.synthetic` `NOT NULL DEFAULT FALSE` — so `= FALSE` is *safe today*. But the universality CI gate must mandate **`IS NOT TRUE`** (or `NOT synthetic`) as the canonical form, because a future nullable sibling column or a `LEFT JOIN sites` producing NULL rows would make `= FALSE` silently drop real sites — a Counsel Rule 4 orphan-coverage failure. Migration claimed: **321**.

B1 is **~1.0 eng-day**. Deploy-gate before B2: `curl /api/version` runtime_sha == B1 commit SHA.

---

## State verification (on disk, current tree)

| Item | Status |
|---|---|
| mig 315 | SHIPPED. `sites.synthetic BOOLEAN **NOT NULL DEFAULT FALSE**` confirmed (line 314) — **not nullable**, Carol's worst case is averted *for this column*. |
| `tests/test_mttr_soak_filter_universality.py` | **EXISTS** — Phase-4-v1-era SOFT gate. `test_universal_enumeration_filter_sweep_baseline` is print-only (logs, doesn't fail). B1 must HARDEN this file, not create a new one. Parent Gate A's `test_synthetic_site_filter_universality.py` name is wrong — the file already exists under a different name. |
| `tests/test_auditor_kit_refuses_synthetic_site.py` | DOES NOT EXIST — B1 deliverable. |
| `tests/test_l2_orphan_invariant_includes_synthetic_sites.py` | DOES NOT EXIST — B1 deliverable. |
| `substrate_violations` schema (mig 207) | `id, invariant_name, severity, site_id VARCHAR(50), detected_at, last_seen_at, resolved_at, details JSONB, notification_id`. **No synthetic marker.** Open-row partial unique index on `(invariant_name, COALESCE(site_id,''))`. |
| substrate engine INSERT path | `assertions.py:6548` — `INSERT INTO substrate_violations (invariant_name, severity, site_id, details) VALUES ($1,$2,$3,$4::jsonb)`. `v.site_id` is in scope. Single insertion point — clean stamp site. |
| Highest mig on disk | 320. Ledger reserves 316/317/318 (311 BLOCKED). **Next free: 321.** |

---

## LENS 1 — Steve (Engineering): the concrete callsite checklist — APPROVE-WITH-FIXES

Re-walk of the **current** tree. Classification: `S` = site_id-scoped (no filter), `O` = org/partner-scoped self-exclude (defense-in-depth optional), `E` = substrate engine (carve-out, NEVER filter), `U` = **truly unscoped — load-bearing filter required**.

### U — the 14 load-bearing callsites (apply `AND ... IS NOT TRUE` — see Carol)

| # | File:line | Query shape | Filter to add |
|---|-----------|-------------|---------------|
| 1 | `routes.py:162` | `/api/fleet` — `FROM sites s LEFT JOIN site_appliances` ; admin path `where_clause = "WHERE s.status != 'inactive'"` (org path is scoped, admin path is NOT) | append `AND s.synthetic IS NOT TRUE` to **both** branches of `where_clause` (lines ~149/151) |
| 2 | `routes.py:1555` | onboarding pipeline — `FROM sites WHERE onboarding_stage NOT IN (...)` | `AND synthetic IS NOT TRUE` |
| 3 | `routes.py:1637` | `SELECT onboarding_stage, COUNT(*) FROM sites GROUP BY onboarding_stage` | `WHERE synthetic IS NOT TRUE` before `GROUP BY` |
| 4 | `routes.py:1665` | `AVG(... shipped_at - lead_at) FROM sites WHERE shipped_at IS NOT NULL AND lead_at IS NOT NULL` | `AND synthetic IS NOT TRUE` |
| 5 | `routes.py:1671` | `AVG(... active_at - lead_at) FROM sites WHERE active_at IS NOT NULL ...` | `AND synthetic IS NOT TRUE` |
| 6 | `routes.py:1676` | `COUNT(*) FROM sites WHERE onboarding_stage NOT IN (...) AND created_at < ...` | `AND synthetic IS NOT TRUE` |
| 7 | `routes.py:1683` | `COUNT(*) FROM sites WHERE onboarding_stage = 'connectivity' AND ...` | `AND synthetic IS NOT TRUE` |
| 8 | `routes.py:2679` | `COUNT(*) FROM sites WHERE status != 'inactive'` — **the canonical v1 leak shape** | `AND synthetic IS NOT TRUE` |
| 9 | `routes.py:3261` | `SELECT COUNT(*) cnt FROM sites` (client-count delta, "now") | `WHERE synthetic IS NOT TRUE` |
| 10 | `routes.py:3266` | `COUNT(*) cnt FROM sites WHERE created_at <= NOW() - INTERVAL '7 days'` | `AND synthetic IS NOT TRUE` |
| 11 | `routes.py:3301` | `/api/fleet-posture` CTE `site_health` — `FROM sites s LEFT JOIN site_appliances` , no WHERE at all | add `WHERE s.synthetic IS NOT TRUE` before `GROUP BY s.site_id` |
| 12 | `db_queries.py:438` | `SELECT COUNT(*) as total FROM sites` (platform total for admin dashboard) | `WHERE synthetic IS NOT TRUE` |
| 13 | `partners.py:3661` | `SELECT COUNT(*) FROM sites WHERE partner_id IS NOT NULL` (admin partner-list total) — `IS NOT NULL` is **not** scoping | `AND synthetic IS NOT TRUE` |
| 14 | `partners.py:3674` | `SELECT partner_id, COUNT(*) FROM sites WHERE partner_id IS NOT NULL GROUP BY partner_id` (admin per-partner site_count rollup) | `AND synthetic IS NOT TRUE` before `GROUP BY` |

> **Note on `routes.py:4681`** (`SELECT site_id, client_org_id FROM sites WHERE client_org_id IS NOT NULL`): synthetic site has NULL `client_org_id`, so it **self-excludes** — reclassified `O`, not `U`. The parent listed it as `U`; it is not load-bearing. Optional defense-in-depth.
> **Note on `client_portal.py:4220`**: `WHERE s.org_id = $1::uuid` — org-scoped, `O`. Parent listed as `U`; reclassified.
> **Note on `notifications.py:505`**: `WHERE s.id = $1` — site-id-scoped (by surrogate PK), `S`. Parent listed as `U`; reclassified — **no filter needed**.

### E — substrate-engine carve-outs (2 callsites — `# noqa: synthetic-allowlisted`, NEVER a filter)

| File:line | Why carve-out |
|-----------|---------------|
| `assertions.py:942` | `_check_cross_org_relocate_chain_orphan` — `FROM sites s WHERE s.prior_client_org_id IS NOT NULL`. The engine MUST be able to tick on the synthetic site. Synthetic site has NULL `prior_client_org_id` so it naturally won't match *this* invariant — but the universality gate will flag the bare `FROM sites s` and demand either a filter or a carve-out. **Carve-out, with comment:** `-- # noqa: synthetic-allowlisted — substrate engine MUST tick on synthetic site (Task #66 B1)`. |
| `assertions.py:1377` | Inside the `org_isolation` invariant SQL — `SELECT COUNT(*) FROM compliance_bundles WHERE site_id IN (SELECT site_id FROM sites WHERE client_org_id = current_setting('app.current_org', true))`. Org-scoped already (synthetic has NULL org), AND it is engine code. **Carve-out**, same comment. |

> **`assertions.py:2810` — DO NOT TOUCH.** This is a Python string literal — the `recommended_action` prose of the `org_isolation` invariant registry entry. It contains the *text* "SELECT site_id FROM sites WHERE client_org_id..." as documentation. Editing it changes operator-facing copy for no reason. The universality gate's `_FROM_RE` regex *will* match it — it must be added to the gate's `ALLOWLIST` with rationale `comment-string-not-a-query`.

### O — defense-in-depth (optional `# noqa`-credited filter; not load-bearing)

`flywheel_federation_admin.py:201` + `:276` (`WHERE s.client_org_id IS NOT NULL` — synthetic has NULL org), `partners.py:3761` (`WHERE partner_id = $1`), `background_tasks.py:1986` / `:2011` (`WHERE s.partner_id = $1`), `background_tasks.py:1128` (`JOIN discovered_devices` — synthetic has none), `integrations/tenant_isolation.py:97` (`SELECT id FROM sites` admin-all — synthetic site *would* surface here; **recommend U-grade filter** — see P1-S2), `provisioning.py:130` (`WHERE wg_ip IS NOT NULL` — synthetic has no wg_ip), `routes.py:4681`, `client_portal.py:4220`.

**P0-S1 — parent's count is wrong; the build target is 14 load-bearing + 2 engine carve-outs + ~9 optional.** Do NOT blind-apply `synthetic = FALSE` to 21 lines from the parent's stale list — 3 are false positives (`assertions.py:2810` comment, `notifications.py:505` site-scoped, plus the `:2802`→`:942` line drift) and several are org-scoped non-load-bearing. Use the table above.

**P1-S1 — `integrations/tenant_isolation.py:97`.** `if user_role == "admin": SELECT id FROM sites` returns *every* site id to an admin's accessible-set. The synthetic site WOULD appear here once `status='active'`. This is not customer-facing (it's an internal access-set), but for consistency promote it to a **load-bearing filter** (`WHERE synthetic IS NOT TRUE`) — call it callsite #15 if the implementer wants the round number. Low risk, include it.

**P1-S2 — the universality gate must classify, not count-all.** The existing `test_mttr_soak_filter_universality.py` already has the right architecture (`_SOAK_EXCLUSION_PATTERNS` with site-id-scoped regexes that auto-skip). B1 hardens it: flip the soft baseline test to a hard ratchet, and the exclusion-pattern list must accept `synthetic IS NOT TRUE` / `NOT s?.?synthetic` / `# noqa: synthetic-allowlisted` as valid. Baseline = the count of bare `FROM sites` lines *not* matching any exclusion pattern after B1's edits land = **target 0** (every U gets a filter, every E gets a noqa, every S/O already matches a site-id/status pattern). See Coach lens.

---

## LENS 2 — Maya (Database): the `substrate_violations` synthetic-marker migration — APPROVE

**Claimed migration number: 321.** (Highest on disk 320; ledger reserves 316/317/318; 311 BLOCKED. 321 is the next free integer.) Ledger row + `<!-- mig-claim:321 task:#66 -->` marker in this design doc — both below.

### Cleanest shape

```sql
-- Migration 321: substrate_violations synthetic marker (Task #66 B1)
BEGIN;

-- Nullable-free boolean, matches the sites.synthetic convention (mig 315).
-- NOT NULL DEFAULT FALSE so the universality / IS-NOT-TRUE discipline
-- carries: a future reader can never get a NULL surprise.
ALTER TABLE substrate_violations
    ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT FALSE;

-- Partial index: the soak analyzer + the audit-exclusion queries only
-- ever care about the synthetic rows. Mirrors idx_sites_synthetic.
CREATE INDEX IF NOT EXISTS idx_substrate_violations_synthetic
    ON substrate_violations (synthetic) WHERE synthetic = TRUE;

INSERT INTO admin_audit_log (user_id, username, action, target, details, ip_address)
VALUES (NULL, 'jbouey2006@gmail.com', 'substrate_violations_synthetic_marker',
    'mig:321',
    jsonb_build_object('migration','321_substrate_violations_synthetic_marker',
        'task','#66','design_doc','audit/coach-66-b1-concrete-plan-gate-a-2026-05-14.md'),
    NULL);

COMMIT;
```

### `BOOLEAN NOT NULL DEFAULT FALSE` vs `synthetic_run_id UUID NULL`

The parent floated both. **Ruling: `BOOLEAN NOT NULL DEFAULT FALSE`.** Rationale:
- It mirrors `sites.synthetic` (mig 315) exactly — one convention, not two.
- `NOT NULL DEFAULT FALSE` keeps the `IS NOT TRUE` discipline intact for any future reader of `substrate_violations`.
- The soak analyzer does NOT need `synthetic_run_id` *on this table* to do its join — it joins `substrate_synthetic_seeds` (which already has `soak_run_id`) ↔ `substrate_violations` via `(invariant_name, site_id)` + `details->'matches'` containment (per parent P1-CROSS-3). The run linkage lives in `substrate_synthetic_seeds`, not here. Adding a `synthetic_run_id` to `substrate_violations` would duplicate state and risk drift. The boolean is a pure *audit-exclusion* marker — "this row was opened by the soak, exclude it from any Q2-2026 MTTR question." That's all it needs to be.

### How `assertions.py` populates it — derived at insert time, NOT per-Violation

**Do NOT add a `synthetic` field to the `Violation` dataclass.** Every invariant returns `Violation` objects; threading a synthetic flag through 60+ invariant functions is error-prone and pointless — the engine doesn't know or care. Instead, **derive it at the single INSERT site** (`assertions.py:6548`):

```python
# assertions.py ~6548 — the ONE INSERT INTO substrate_violations
await conn.execute(
    """
    INSERT INTO substrate_violations
          (invariant_name, severity, site_id, details, synthetic)
    VALUES ($1, $2, $3, $4::jsonb,
            COALESCE($3 LIKE 'synthetic-%', FALSE))
    """,
    a.name, a.severity, v.site_id, json.dumps(v.details),
)
```

`$3 LIKE 'synthetic-%'` derives directly from `v.site_id` — no extra query, no FK to `sites`, no dataclass change. The `synthetic-` prefix is the v2 isolation contract (mig 315 CHECK `site_id LIKE 'synthetic-%'` on `substrate_synthetic_seeds` makes it a hard guarantee). `COALESCE(... , FALSE)` guards `v.site_id IS NULL` (site-less invariants like `email_dlq_growing`) — a NULL site_id can never be synthetic.

**The refresh-path UPDATE at `assertions.py:6577` does NOT need to touch `synthetic`** — `synthetic` is immutable for the life of a row (a row's site_id never changes), and the open-row partial unique index means a refresh always hits the row that was inserted with the correct `synthetic` value. Confirmed: only the INSERT needs the change.

**P1-M1 — backfill is a no-op, by contract.** The v2 isolation contract guarantees zero existing `synthetic-`-prefixed `substrate_violations` rows (mig 304 quarantine kept the site `inactive`; the engine never opened a row for it). So mig 321 needs no `UPDATE ... SET synthetic = TRUE` backfill. If the implementer wants belt-and-suspenders: `UPDATE substrate_violations SET synthetic = TRUE WHERE site_id LIKE 'synthetic-%'` is a safe no-op — include it, costs nothing.

**P2-M1 — `site_id VARCHAR(50)`.** `'synthetic-mttr-soak'` is 19 chars — well within 50. No issue. Noted only because the parent didn't.

No RLS regression — `substrate_violations` is admin-context, no org/partner policy.

---

## LENS 3 — Carol (Security): `= FALSE` vs `IS NOT TRUE` — APPROVE-WITH-FIXES

**This is the load-bearing security finding for B1.**

mig 315 line 314: `ALTER TABLE sites ADD COLUMN IF NOT EXISTS synthetic BOOLEAN **NOT NULL DEFAULT FALSE**`. So **`sites.synthetic` is NOT nullable** — Carol's nightmare ("`= FALSE` silently drops NULL rows") **does not apply to the column itself today.** Good. Phase A got this right.

**But `= FALSE` is still the wrong form to mandate, for three reasons:**

1. **`LEFT JOIN sites` produces NULL `synthetic` even though the column is NOT NULL.** Callsites #1 (`routes.py:162`) and #11 (`routes.py:3301`) and #3301 are `LEFT JOIN site_appliances` *with* `sites` as the left table, so `sites.synthetic` is non-NULL there — but the *general pattern* the CI gate enforces will eventually hit a query where `sites` is the RIGHT side of a LEFT JOIN (e.g. `FROM site_appliances sa LEFT JOIN sites s`). There, `s.synthetic` is NULL for orphan appliances, and `s.synthetic = FALSE` drops them. `s.synthetic IS NOT TRUE` keeps them. For an *orphan-coverage* gate that is exactly the failure mode you must not have.
2. **A future sibling column.** If anyone ever adds `sites.synthetic_v2` or a nullable variant, a copy-paste of the `= FALSE` idiom inherits the bug. `IS NOT TRUE` is bug-immune by construction.
3. **It's free.** `IS NOT TRUE` and `= FALSE` have identical planner cost against a `NOT NULL` column and identical index usage against `idx_sites_synthetic WHERE synthetic = TRUE`.

**RULING: B1 mandates `AND s.synthetic IS NOT TRUE` (or `AND NOT COALESCE(s.synthetic, FALSE)` where an alias is awkward) as the canonical form.** The universality CI gate's accepted-pattern regex must accept `IS NOT TRUE` and `NOT synthetic` and explicitly **reject a bare `synthetic = FALSE`** with a message pointing here — or at minimum warn. This is a Counsel Rule 4 (no silent orphan coverage) compiler-rule, not a style preference.

**P0-C1 — gate enforces `IS NOT TRUE`, not `= FALSE`.** Bake it into `test_mttr_soak_filter_universality.py`'s pattern list.

Otherwise: B1 ships no new endpoints, no new data egress, no PHI surface. The migration is admin-context. `substrate_violations.synthetic` is an internal audit marker. No Rule 2 / Rule 7 surface. APPROVE on everything except the `IS NOT TRUE` mandate.

---

## LENS 4 — Coach: the 3 CI ratchets — APPROVE-WITH-FIXES

The parent named two ("`test_synthetic_site_filter_universality.py`" + "`test_auditor_kit_refuses_synthetic_site.py`") and a third ("`test_l2_orphan_invariant_includes_synthetic_sites.py`"). **Corrections:** the first already exists under the name `test_mttr_soak_filter_universality.py` — B1 *hardens* it, doesn't create it.

### Ratchet 1 — `test_mttr_soak_filter_universality.py` (HARDEN existing)

- **What it pins:** every `FROM sites` enumeration in admin/federation/flywheel/onboarding context carries a synthetic-exclusion predicate (`synthetic IS NOT TRUE` / `NOT synthetic`), a site-id/status scope, or a `# noqa: synthetic-allowlisted` carve-out.
- **Change from today:** `test_universal_enumeration_filter_sweep_baseline` is currently a **soft print-only** test. B1 converts it to a **hard `assert`**. Add `synthetic IS NOT TRUE` and `NOT s?.?synthetic` and `# noqa: synthetic-allowlisted` to `_SOAK_EXCLUSION_PATTERNS`. Per Carol P0-C1, *also* add a check that flags a bare `synthetic\s*=\s*FALSE` as a soft warning (mandate `IS NOT TRUE`).
- **Scan-file list:** current list is `routes.py, flywheel_federation_admin.py, background_tasks.py, fleet.py, org_management.py`. **Extend** to add `db_queries.py`, `partners.py`, `client_portal.py`, `notifications.py`, `provisioning.py`, `integrations/tenant_isolation.py`, `assertions.py`.
- **`ALLOWLIST`:** add `assertions.py: {2810}` (comment-string false positive) with rationale.
- **Baseline value:** **0** unfiltered after B1's 14 edits + 2 engine carve-outs + extending the exclusion-pattern list to recognize the existing site-id-scoped queries. The gate is "0 bare enumerations" not "ratchet a number down" — the existing file's `_SOAK_EXCLUSION_PATTERNS` already auto-skips the 169 site-id-scoped lines, so the residual after B1 is genuinely 0. If a stray remains, allowlist it with rationale.
- **Sibling pattern mirrored:** `test_client_portal_filters_soft_deletes.py` (anchor-on-the-line, line-anchored carve-outs by content not number) and `test_no_unfiltered_site_appliances_select.py`. Reuse the `CARVED_OUT_PATTERN_FRAGMENTS`-by-content idiom for the `# noqa` carve-outs.

### Ratchet 2 — `test_auditor_kit_refuses_synthetic_site.py` (NEW)

- **What it pins:** the auditor-kit auth/entry path (`require_evidence_view_access` and the kit endpoints in `evidence_chain.py` / `audit_package_api.py`) refuses a `synthetic-`-prefixed `site_id` in the URL — a synthetic site can never produce a downloadable evidence kit. Per design §4.6.
- **Shape:** source-scan gate (mirror `test_client_portal_filters_soft_deletes.py`) — assert `require_evidence_view_access` (or the kit route) contains a `synthetic` / `LIKE 'synthetic-%'` refusal check. B1 must also *add* that check to the source if it's not there (the design says "a new `synthetic=FALSE` check at the require_evidence_view_access entry" — verify and implement).
- **Baseline:** binary pass/fail (presence of the refusal check).

### Ratchet 3 — `test_l2_orphan_invariant_includes_synthetic_sites.py` (NEW)

- **What it pins:** the **inverse** of the others — `_check_l2_resolution_without_decision_record`'s query body MUST NOT exclude synthetic sites (the engine must tick on them; that's the whole soak). Design §8 Q5.
- **Shape:** source-scan `assertions.py` — locate the `_check_l2_resolution_without_decision_record` function body, assert its SQL does **not** contain `synthetic` / `NOT LIKE 'synthetic-%'` / `status != 'inactive'` exclusions. A future migration that narrows the invariant would trip this.
- **Baseline:** binary pass/fail.

**P0-K1 — the parent named the universality gate as a file-to-create; it already exists.** B1's commit must *harden* `test_mttr_soak_filter_universality.py` (soft→hard, extend scan-files, extend patterns), not add a duplicate. A duplicate `test_synthetic_site_filter_universality.py` would be dead-code drift.

**P1-K1 — deploy-verify is the B1→B2 gate, restate in the B1 commit body.** B1's commit body must state: "B2 (injector) BLOCKED until `curl https://<vps>/api/version` returns `runtime_sha == <this commit SHA>`." Per Session 220, B1 and B2 each get their own Gate B fork review running the full pre-push sweep.

---

## LENS 5 — Auditor (OCR): customer-facing impact — N/A (confirmed)

B1 is filters + one migration + 3 CI tests. **Zero customer-facing surface change** — confirmed:
- The 14 filters *remove* the synthetic site from admin/onboarding/partner-admin enumerations. Since the synthetic site is currently `status='inactive'` (mig 304 quarantine), it is *already* excluded from every `status != 'inactive'` query — so B1's filters change **nothing observable today**. They are pre-positioning for when B2's injector flips `status='active'`. No customer sees a difference.
- `substrate_violations.synthetic` is an internal audit-table column. Not on any dashboard, postcard, auditor-kit, or portal.
- The auditor-kit refusal gate (Ratchet 2) *strengthens* an auth boundary — no customer-facing copy or behavior change for real sites.

**One forward-looking note (not a B1 blocker):** once B2 runs, `substrate_violations` will hold synthetic rows. Any future auditor-facing MTTR/substrate-health export MUST filter `synthetic IS NOT TRUE`. That filter obligation lands in B2's analyzer/reporting scope and in the `/admin/substrate-health` panel — flag it as a named B2 carry-forward, but it is **N/A for B1**.

Verdict: **N/A confirmed.** No OCR action for B1.

---

## LENS 6 — PM: effort + deploy-gate — APPROVE

### B1 effort: ~1.0 eng-day

| Work item | Estimate |
|---|---|
| 14 load-bearing filter edits (mostly 1-line; #1 and #11 need 2-branch / CTE care) | 2.5h |
| 2 engine `# noqa` carve-outs + `assertions.py:2810` allowlist entry | 0.5h |
| mig 321 (`substrate_violations.synthetic` boolean + index + audit row) + ledger row + claim marker | 0.5h |
| `assertions.py:6548` INSERT — add `synthetic` column + `$3 LIKE 'synthetic-%'` derivation | 0.5h |
| Harden Ratchet 1 (soft→hard, extend scan-files + patterns, `IS NOT TRUE` enforcement) | 1.5h |
| Ratchet 2 (`test_auditor_kit_refuses_synthetic_site.py` + the refusal-check source edit if absent) | 1.5h |
| Ratchet 3 (`test_l2_orphan_invariant_includes_synthetic_sites.py`) | 0.75h |
| Full pre-push sweep + push + CI-green wait + deploy-verify | 0.5h + wait |

**Total: ~8.25h ≈ 1.0 eng-day** (parent's 1.0–1.25 estimate holds; the harden-not-create discovery saved a bit, the `IS NOT TRUE` discipline added a bit).

### The deploy-verification gate that MUST pass before B2 starts

```
1. B1 pushed → CI green (all 3 ratchets + full sweep pass).
2. CI/CD auto-deploys to VPS.
3. curl https://<vps>/api/version  →  assert runtime_sha == disk_sha == <B1 commit SHA>
4. Spot-check: psql → confirm `substrate_violations.synthetic` column exists
   (\d substrate_violations) and mig 321 is in schema_migrations.
5. ONLY THEN: B2 (injector) work may begin. B2's injector flips
   sites.status='active' — it must never run before step 3 confirms
   the filters are live in prod. B2's commit body cites the step-3 SHA.
```

This is the structural form of Phase A's P0-CROSS-2 deferred-flip contract. No B2 code that touches `status='active'` lands before the B1 SHA is confirmed deployed.

**P1-PM1 — B1 and B2 each get a separate Gate B fork review** (Session 220 two-gate rule). Do not bundle. B1's Gate B runs the full `tests/test_pre_push_ci_parity.py` SOURCE_LEVEL_TESTS sweep, not a diff-scoped review.

---

## LENS 7 — Counsel (Attorney): Counsel Rule 4 (orphan coverage) — APPROVE-WITH-FIXES

Rule 4: *"No segmentation design that creates silent orphan coverage. Orphan detection is sev1, not tolerable warning."* The synthetic-site filter rollout is, by definition, a segmentation design — it carves a class of site out of enumerations. The risk Rule 4 names is exactly: **a filter intended to hide the synthetic site accidentally hides a real customer site.**

**Assessment: the B1 design is Rule-4-safe, CONDITIONAL on Carol's P0-C1 (`IS NOT TRUE`).**

- `sites.synthetic` is `NOT NULL DEFAULT FALSE` (mig 315 verified). Every real customer site has `synthetic = FALSE` by default — no migration touched real rows. So `synthetic IS NOT TRUE` keeps **100%** of real sites.
- The ONE way this design could orphan a real site is the `= FALSE`-against-a-NULL-from-a-LEFT-JOIN path Carol identified. Mandating `IS NOT TRUE` closes that structurally. **With `IS NOT TRUE`, there is no input under which a real customer site is dropped** — `synthetic` is `TRUE` only for `'synthetic-mttr-soak'` (the single row mig 315's `UPDATE` touched), and `IS NOT TRUE` matches both `FALSE` and `NULL`.
- The substrate-engine carve-outs (`assertions.py:942`, `:1377`) are the *correct* Rule-4 posture: the engine that *detects* orphans must itself never be filtered. B1 keeps the engine un-filtered. Good.
- B1 does **not** add an orphan-*detection* invariant — but it doesn't need to. The synthetic site is a known, single, contract-bound row (`site_id LIKE 'synthetic-%'`, mig 315 CHECK). It is not a customer site that could be silently dropped from coverage; it is a test fixture being *kept out of* operator views. The Rule-4 concern is the inverse direction (real → hidden), and `IS NOT TRUE` + `NOT NULL DEFAULT FALSE` makes that impossible.

**P0-CL1 (= Carol P0-C1, restated for legal weight):** the `IS NOT TRUE` mandate is a Counsel Rule 4 compiler-rule, not a style choice. B1 must not ship with `= FALSE` as the idiom. The universality gate enforces it.

Counsel Rule 1/2/6/7: B1 ships no metric, no PHI surface, no BAA state, no unauthenticated channel. The `substrate_violations.synthetic` marker actually *improves* Rule 1 posture downstream (lets B2's reporting exclude non-canonical synthetic MTTR from any customer-facing number). Migration audit-log row uses `jbouey2006@gmail.com` (named human) per the privileged-chain rule — confirmed in the mig 321 spec above.

**APPROVE-WITH-FIXES** — conditional on P0-CL1.

---

## Consolidated findings (severity-graded)

### P0 — must close before B1 ships

- **P0-1 (`IS NOT TRUE` not `= FALSE`)** [Carol + Counsel]: B1 mandates `synthetic IS NOT TRUE` (or `NOT synthetic` / `NOT COALESCE(s.synthetic, FALSE)`) as the canonical filter form. The universality gate enforces it and warns on bare `= FALSE`. Counsel Rule 4 compiler-rule.
- **P0-2 (use the corrected 14-callsite list, not the parent's 21)** [Steve]: 3 of the parent's named callsites are false positives — `assertions.py:2810` (comment string — allowlist, never edit), `notifications.py:505` (site-id-scoped via `s.id = $1` — no filter), and the `:2802`→`:942` line drift. Several parent-listed callsites are org-scoped non-load-bearing. Build against the Steve-lens U-table.
- **P0-3 (harden the EXISTING universality test, don't create a duplicate)** [Coach]: `test_mttr_soak_filter_universality.py` already exists as a soft gate. B1 converts it soft→hard + extends scan-files + extends patterns. Do NOT create `test_synthetic_site_filter_universality.py` (parent's name) — that's dead-code drift.

### P1 — close before B1's Gate B, or carry as named followup in the same commit

- **P1-1 (`integrations/tenant_isolation.py:97` promote to load-bearing)** [Steve]: `if admin: SELECT id FROM sites` surfaces the synthetic site in an admin's accessible-set once `status='active'`. Add `WHERE synthetic IS NOT TRUE`. Low risk, include in B1.
- **P1-2 (`substrate_violations.synthetic` derived at INSERT, not via Violation dataclass)** [Maya]: stamp `synthetic` at the single `assertions.py:6548` INSERT via `$3 LIKE 'synthetic-%'`. Do NOT thread a flag through 60+ invariant functions.
- **P1-3 (B1 commit body states the B2 deploy-gate)** [Coach + PM]: commit body must name the `curl /api/version` runtime_sha precondition for B2.

### P2 — do during the B1 callsite audit

- **P2-1 (`assertions.py:2810` allowlist entry)** [Steve]: add `assertions.py: {2810}` to the universality gate `ALLOWLIST` with rationale `comment-string-not-a-query`.
- **P2-2 (belt-and-suspenders backfill in mig 321)** [Maya]: `UPDATE substrate_violations SET synthetic = TRUE WHERE site_id LIKE 'synthetic-%'` is a contract-guaranteed no-op — include it, costs nothing.

---

## Migration claim

`<!-- mig-claim:321 task:#66 -->`

Add to `mcp-server/central-command/backend/migrations/RESERVED_MIGRATIONS.md` in the B1 design commit:

| 321 | reserved | (MTTR soak v2 B1 — `audit/coach-66-b1-concrete-plan-gate-a-2026-05-14.md`) | 2026-05-14 | 2026-05-21 | #66 | `substrate_violations.synthetic` marker column |

Remove the row in the same commit that lands `migrations/321_substrate_violations_synthetic_marker.sql`.

---

## B1 deliverable touch-list

| File | Change |
|---|---|
| `routes.py` | 11 load-bearing filters (#1–#11 in Steve's table) |
| `db_queries.py` | 1 filter (#12) |
| `partners.py` | 2 filters (#13, #14) |
| `integrations/tenant_isolation.py` | 1 filter (P1-1, line 97) |
| `assertions.py` | 2 `# noqa: synthetic-allowlisted` carve-outs (`:942`, `:1377`) + INSERT-path `synthetic` column at `:6548` |
| `migrations/321_substrate_violations_synthetic_marker.sql` | NEW |
| `migrations/RESERVED_MIGRATIONS.md` | claim row 321 |
| `tests/test_mttr_soak_filter_universality.py` | HARDEN (soft→hard, extend scan-files + patterns, `IS NOT TRUE` enforcement, `ALLOWLIST` entry) |
| `tests/test_auditor_kit_refuses_synthetic_site.py` | NEW + the refusal-check source edit in `evidence_chain.py`/auth path if absent |
| `tests/test_l2_orphan_invariant_includes_synthetic_sites.py` | NEW |

---

## Final verdict

**APPROVE-WITH-FIXES** — B1 is build-ready as specified above. The parent Phase B Gate A's direction (split + filter rollout + 3 ratchets + marker migration) is sound; this B1 review corrects three concrete defects in the parent's plan that would have caused wrong edits:

1. **Carol/Counsel P0:** mandate `IS NOT TRUE`, not `= FALSE` — Counsel Rule 4 orphan-coverage compiler-rule. `sites.synthetic` is `NOT NULL DEFAULT FALSE` (mig 315 verified) so `= FALSE` is safe *today*, but `IS NOT TRUE` is bug-immune against LEFT-JOIN NULLs and future nullable siblings.
2. **Steve P0:** the load-bearing set is **14 callsites**, not 21 — 3 parent-named callsites are false positives (a comment string, a site-id-scoped query, a stale line number) and several are org-scoped non-load-bearing. Build against the corrected U-table.
3. **Coach P0:** the universality gate **already exists** as `test_mttr_soak_filter_universality.py` (soft) — harden it, don't create a duplicate.

Migration **321** claimed. B1 effort **~1.0 eng-day**. B1→B2 gate: `curl /api/version` runtime_sha == B1 commit SHA, confirmed in prod, before any B2 injector code touches `status='active'`. B1 and B2 each get their own Gate B fork review running the full pre-push sweep.

No redesign. Close P0-1/2/3 in the B1 build; close P1s before B1's Gate B or carry as named TaskCreate followups in the B1 commit.
