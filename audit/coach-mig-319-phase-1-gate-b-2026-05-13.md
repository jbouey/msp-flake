# Gate B — AS-SHIPPED Phase 1 device-dedup rollout (commit e5793fc0)

**Date:** 2026-05-13
**Scope:** AS-SHIPPED review of commit `e5793fc0` — mig 319 canonical_devices + Phase 1 dedup migration (Task #73)
**Protocol:** Class-B 7-lens Gate B per Session 220 two-gate lock-in. **Full pre-push test sweep executed** (not diff-only).
**Inputs reviewed:** 16 shipped files (sql, py, json, md, sh), 2 prior Gate A verdicts (architectural v1 + implementation v2), 1 retro Gate B on prior commit (adb7671a), commit body.

---

## 250-word summary

The AS-SHIPPED commit is **structurally sound** for the design Gate A approved: mig 319 creates `canonical_devices` with a sensible 11-column schema, a generated `mac_dedup_key` column for the UNIQUE INDEX, idempotent backfill via majority-vote-with-alphabetical-tiebreaker SQL, three RLS policies (admin + tenant_org + partner mirroring discovered_devices), a 60s `canonical_devices_reconciliation_loop` registered in `main.py:task_defs`, a substrate invariant `canonical_devices_freshness` with paired `_DISPLAY_METADATA` and a template-compliant 7-section runbook, the canonical_metrics registry entry for `device_count_per_site` with a 26-entry allowlist, the `compliance_packet._get_device_inventory` and `device_sync.get_site_devices` CTE-JOIN migrations, the `_PACKET_METHODOLOGY_VERSION = "2.0"` bump with a Methodology section in the markdown template that uses Counsel-approved "may have over-counted" framing (no banned words), the CI gate `test_no_raw_discovered_devices_count.py` with `BASELINE_MAX=22`, and the prod_columns.json fixture. **However, the pre-push sweep is RED — `test_compliance_status_not_read.py` fails 8>7 because the new compliance_packet.py:1185 CTE introduces a NEW `dd.compliance_status` reader to an existing deprecation ratchet** (task #23 BUG 3). The commit body's claim "Local: full pre-push sweep clean (rc=0)" is false at the time of this Gate B review. This is the exact "diff-only Gate B = automatic BLOCK" class Session 220 locked in. Three additional P1 findings on RLS policy parity, doc-vs-code drift in the CI gate file, and a 120s startup window where Device Inventory shows 0 devices. **VERDICT: BLOCK.**

---

## Per-lens verdict

### 1. Engineering (Steve) — BLOCK on P0, otherwise APPROVE

| Item | Status |
|---|---|
| Mig 319 SQL: single BEGIN/COMMIT, table + 3 indexes + 3 RLS policies + backfill + audit row | ✓ PASS |
| `mac_dedup_key TEXT GENERATED ALWAYS AS (COALESCE(mac_address,'')) STORED` + UNIQUE INDEX (site_id, ip_address, mac_dedup_key) | ✓ PASS — sound |
| Reconciliation loop SQL matches backfill shape with `ON CONFLICT DO UPDATE` instead of DO NOTHING + 24hr window filter | ✓ PASS |
| Loop uses `admin_transaction(pool)` not `tenant_connection` (correct — admin context for cross-site read+write) | ✓ PASS |
| compliance_packet CTE-JOIN uses `DISTINCT ON (cd.canonical_id) ORDER BY cd.canonical_id, dd.last_seen_at DESC` (freshest observation per canonical row) | ✓ PASS |
| device_sync CTE-JOIN preserves filter/LIMIT/OFFSET semantics | ✓ PASS (with P2 note below) |
| Backfill `GROUP BY ... COALESCE(dd.mac_address,'')` — handles NULL MAC consistently with mac_dedup_key | ✓ PASS |
| **P2 — `dd_freshest` CTE selects both `cd.site_id, cd.ip_address, cd.mac_address` AND `dd.*` (which contains the same columns).** PostgreSQL allows duplicate column names in subquery; outer `d.site_id` references first match. Works but fragile against future refactor. Recommend explicit column list. | P2 |
| **P1 — 120s startup window:** `await asyncio.sleep(120)` at loop entry means first 2 minutes after every backend restart show 0 devices on Device Inventory + return 0 for compliance packet. Pre-migration this was non-zero (raw discovered_devices). Customer-visible regression for the restart window. | P1 |

### 2. Database (Maya) — APPROVE-WITH-FIXES on RLS parity

| Item | Status |
|---|---|
| `rls_site_belongs_to_current_org(TEXT)` exists (mig 278) | ✓ PASS |
| `rls_site_belongs_to_current_partner(TEXT)` exists (mig 297) | ✓ PASS |
| Backfill idempotency via `ON CONFLICT (site_id, ip_address, mac_dedup_key) DO NOTHING` | ✓ PASS |
| Admin policy mirrors `current_setting('app.is_admin', true) = 'true'` pattern | ✓ PASS |
| **P1 — tenant_org policy guard mismatch:** mig 319 omits the `current_setting('app.current_org', true) IS NOT NULL AND <> ''` guard that mig 278 applies to every sibling site_table including discovered_devices. Function still returns false on empty string so result is correct; perf regression + documented-contract violation. Same for partner_isolation — mig 297's COMMENT on `rls_site_belongs_to_current_partner` explicitly says "NEVER use this with empty/NULL current_partner_id — the policy short-circuits via the IS NOT NULL guard at the policy level." Mig 319 violates that contract directly. | P1 |
| **P2 — site_id cast:** mig 278/297 explicitly cast `site_id::text`; mig 319 calls the helpers with bare `site_id`. canonical_devices.site_id IS TEXT so implicit cast is no-op — works today; fragile if column type ever changes. | P2 |
| FORCE RLS not applied — but mig 278/297 also don't apply FORCE on iterated tables; consistent pattern | ✓ PASS (no parity break) |
| `observed_by_appliances UUID[]` preserves Rule 4 per-appliance source-of-record | ✓ PASS |
| Comments on table + columns explain provenance + tiebreaker | ✓ PASS |

### 3. Security (Carol) — APPROVE-WITH-FIXES

| Item | Status |
|---|---|
| 3-layer defense (admin / tenant_org / partner) end-to-end | ✓ PASS (with Maya's P1 above) |
| Reconciliation loop runs under `admin_transaction` — won't accidentally leak across tenants | ✓ PASS |
| Per-appliance source-of-record preserved in `observed_by_appliances UUID[]` — Rule 4 not silenced | ✓ PASS |
| canonical_metrics allowlist entries classification correctness: `prometheus_metrics.*`, `appliance_trace.*`, `assertions._check_discovered_devices_freshness`, `sites.py:5090-5116` are `operator_only`; `device_sync._compute_*`, `device_sync.merge_*`, `health_monitor.*owner_appliance*` are `write_path`; 19 callsites are `migrate`. Net 4 operator_only + 3 write_path + 19 migrate = 26 entries. | ✓ PASS |
| **P3 — commit body says "20 allowlist entries" — actual is 26.** Cosmetic doc drift, no functional impact. | P3 |
| Customer-facing surface review: device_count_per_site emission from compliance_packet PDF (signed, distributed to clients) is the highest-risk surface. Migrated to canonical → safe. device_sync.get_site_devices feeds operator + client portal Device Inventory; migrated → safe. | ✓ PASS |

### 4. Coach — BLOCK on Gate-A-Phase-2-scope-drift + sweep failure

| Item | Status |
|---|---|
| Phase 1 carries Phase 2 properly: commit body cites "17 remaining customer-facing readers" but canonical_metrics allowlist has 19 `migrate` entries (partners.py×4, portal.py×2, client_portal.py×2, routes.py×7, sites.py×3, background_tasks.py×1 = 19). | P1 — task tracking should reflect 19 not 17 |
| **P0 — Pre-push sweep RED.** `test_compliance_status_not_read.py` fails 8>7 because `compliance_packet.py:1185 dd.compliance_status,` is a NEW reader on an existing deprecation ratchet (BUG 3, task #23). The new CTE-JOIN intentionally pulls `dd.compliance_status` for the `COUNT(*) FILTER (WHERE compliance_status = 'compliant') as compliant` aggregation. This was inevitable for the migration, but neither Gate A nor the author preemptively bumped the BASELINE_MAX of the unrelated ratchet. The commit body's "Local: full pre-push sweep clean (rc=0)" claim is **false at Gate B time**. This is the canonical "diff-only Gate B" failure mode Session 220 closed. | **P0** |
| Retro Gate B class regression check: adb7671a precursor had bare round() + bare imports. Phase 1 introduces no analogous bare patterns. | ✓ PASS |

### 5. Auditor (OCR) — APPROVE on copy review

| Item | Status |
|---|---|
| `_PACKET_METHODOLOGY_VERSION = "2.0"` declared + injected via `data_with_version.setdefault("packet_methodology_version", ...)` | ✓ PASS |
| Methodology section rendered in Jinja template lines 1468-1474 | ✓ PASS |
| Banned-word scan on Methodology copy: no "ensures", "prevents", "protects", "guarantees", "100%", "error", "bug", "incorrect", "wrong" | ✓ PASS |
| Counsel-approved "may have over-counted" framing present | ✓ PASS |
| "Past packets remain Ed25519-signed and immutable" — factually accurate, Counsel-aligned | ✓ PASS |
| **P2 — Methodology section hardcodes "version 2.0" in prose body** at line 1472 while `{{ packet_methodology_version }}` placeholder is dynamic at line 1470. A future bump to 3.0 must update BOTH the literal AND the placeholder — currently only one is single-source. Lockstep fragility. | P2 |
| Explicit comment at line 178 distinguishes kit_version (2.1, auditor kit) from packet methodology (2.0) — prevents lockstep confusion across artifacts | ✓ PASS |

### 6. PM — BLOCK pending P0 closure

- 16 files in one commit IS large but Gate A v1 and v2 explicitly approved. Per-file scope is appropriate.
- **Full pre-push sweep result: 249 passed, 1 failed (test_compliance_status_not_read.py), 0 skipped.** Cited per Session 220 lock-in.
- Per Session 220 lock-in: "Diff-only review = automatic BLOCK pending sweep verification." Sweep failure IS the BLOCK trigger.
- No analogous bare-round() / bare-import footguns from adb7671a class.
- Task list drift: commit body says "17 remaining for Phase 2" but allowlist says 19 + Phase 2 task description should reflect this.

### 7. Attorney (in-house counsel) — APPROVE on Methodology disclosure

- Methodology disclosure is the load-bearing legal artifact for the past-packet over-count exposure.
- Copy review: uses "may have over-counted" (factual, hedged), NOT "error/bug/wrong/incorrect" — aligns with Counsel-approved framing for prior compliance artifact corrections.
- Article 8 (Bridge Clause) reference for past-packet immutability is correct.
- Going-forward disclosure surfaces in every newly-issued packet PDF — auditors can reconcile pre/post-2026-05-13 device-count methodology.
- **No legal exposure on the disclosure mechanism itself.** Disclosure is on every customer packet from 2026-05-13 forward, surfacing in the customer-facing PDF.

---

## AS-IMPLEMENTED vs DESIGN deviation matrix (16 files)

| File | DESIGN intent | AS-SHIPPED state | Δ |
|---|---|---|---|
| `migrations/319_canonical_devices.sql` | Table + 3 idx + 3 RLS + backfill | All shipped correctly | RLS guard pattern omitted (P1, see Maya) |
| `canonical_metrics.py` | New `device_count_per_site` entry + 20 allowlist | New entry + 26 allowlist entries | Count mismatch (P3 cosmetic) |
| `assertions.py` | _check_canonical_devices_freshness + ALL_ASSERTIONS + _DISPLAY_METADATA | All 3 present | none |
| `compliance_packet.py` | _get_device_inventory CTE-JOIN + _PACKET_METHODOLOGY_VERSION 2.0 + Methodology section | All 3 present | Methodology section hardcodes "2.0" in prose (P2) |
| `device_sync.py:761` | get_site_devices CTE-JOIN | Present | Duplicate column names in CTE (P2) + introduces NEW dd.compliance_status read (**P0** breaks BUG 3 ratchet) |
| `background_tasks.py` | canonical_devices_reconciliation_loop, 60s tick, admin_transaction | All shipped | 120s startup delay = visible 0-device window (P1) |
| `main.py` | task_defs registration | Registered at line 2266 | none |
| `substrate_runbooks/canonical_devices_freshness.md` | 7-section runbook | All 7 present | none |
| `tests/test_no_raw_discovered_devices_count.py` | BASELINE_MAX baseline-after-Phase1 | Present as 22 | Docstring says 17, code says 22 (P1 doc-drift) |
| `tests/fixtures/schema/prod_columns.json` | canonical_devices entry | Present, 11 columns match mig 319 | none |
| `tests/test_canonical_metrics_registry.py` | Update for new metric | 4 lines changed (not reviewed in depth) | none |
| `.githooks/pre-push` | Add test to SOURCE_LEVEL_TESTS | 1 line added | none |
| `audit/coach-adb7671a-retro-gate-b-2026-05-13.md` | Retro Gate B doc | Present | retro audit, not Phase 1 artifact |
| `audit/coach-device-dedup-architectural-gate-a-2026-05-13.md` | Gate A v1 doc | Present | gate evidence |
| `audit/coach-device-dedup-implementation-gate-a-2026-05-13.md` | Gate A v2 doc | Present | gate evidence |
| `audit/device-dedup-architectural-design-2026-05-13.md` | Design v3 | Present | design evidence |

---

## Full pre-push sweep result

```
bash .githooks/full-test-sweep.sh
...
❌ 1 file(s) failed (out of 249 passed, 0 skipped):
  - tests/test_compliance_status_not_read.py

E   AssertionError: DEPRECATED `discovered_devices.compliance_status` reads detected: 8 found vs BASELINE_MAX=7.
    All matches:
      - partners.py:2585
      - compliance_packet.py:1185         ← NEW from this commit
      - client_portal.py:1288
      - device_sync.py:828
      - device_sync.py:1328
      - device_sync.py:1378
      - routes.py:5310
      - routes.py:5856
```

249 passed, **1 failed, 0 skipped**. Sweep ~92s.

**Root cause:** the Phase 1 CTE-JOIN at `compliance_packet.py:1167` deliberately JOINs back to discovered_devices for `os_name`, `compliance_status`, `device_status` (fields canonical doesn't carry today, Path A per Gate A v2 plan). The JOIN-back is correct design, but the new line `dd.compliance_status,` at line 1185 is a new occurrence under the BUG 3 (task #23) deprecation grep. Gate A v2 did not cross-check Phase 1's intended new readers against the BUG 3 ratchet.

---

## Methodology section copy review

Copy under review (compliance_packet.py:1468-1474):

> ## Methodology
>
> _Packet methodology version: 2.0 (updated 2026-05-13)_
>
> Device counts as of methodology version 2.0 use **per-site canonical device records** — a unique physical device identified by `(IP address, MAC address)` per site is counted once. Prior to 2026-05-13, multi-appliance sites' device counts reflected raw observations from each appliance and may have over-counted devices observed by multiple appliances on the same network. Past packets remain Ed25519-signed and immutable under the cryptographic-attestation chain.

**Banned-word scan:** ensures, prevents, protects, guarantees, 100%, error, bug, incorrect, wrong, audit-ready, PHI never leaves — all ABSENT. ✓

**Counsel-approved framing present:** "may have over-counted" + "remain Ed25519-signed and immutable" + "per-site canonical device records" — all factual + hedged + chain-aware. ✓

**Cross-artifact lockstep:** `_PACKET_METHODOLOGY_VERSION = "2.0"` is distinct from auditor-kit `kit_version = "2.1"` (explicit comment line 178). No cross-artifact confusion. ✓

**P2 lockstep fragility:** prose hardcodes "version 2.0" twice while only one is `{{ packet_methodology_version }}` placeholder — future bump must update both manually.

---

## Final verdict

# **BLOCK**

Reasons:

1. **P0 — Pre-push sweep RED.** `test_compliance_status_not_read.py` fails 8>7. Commit body claim "Local: full pre-push sweep clean (rc=0)" is false at Gate B time. Per Session 220 two-gate lock-in: "Diff-only Gate B = automatic BLOCK pending sweep verification." Sweep was run and failed.

2. **P1 cluster (must close before unblock):**
   - RLS policy guard pattern mismatch with mig 278/297 — `current_setting(...) IS NOT NULL AND <> ''` short-circuit absent in canonical_devices's tenant_org + partner policies (documented contract violation per mig 297 function COMMENT).
   - `test_no_raw_discovered_devices_count.py` docstring says `BASELINE_MAX = 17` but code says `22` — doc-vs-code drift inside the same file.
   - 120s startup window: every backend restart shows 0 devices for 2 minutes on Device Inventory + 0 for compliance packet PDF generation during that window. Pre-migration this was non-zero.
   - Task tracking drift: commit body says "17 Phase 2 readers" but allowlist enumerates 19 `migrate` callsites.

3. **P2/P3 (carry-forward, not blocker):**
   - CTE duplicate column names (device_sync.py + compliance_packet.py).
   - Methodology section prose hardcodes "version 2.0" in addition to the Jinja placeholder.
   - Commit body "20 allowlist entries" vs actual 26 (cosmetic).
   - mig 319 uses bare `site_id` not `site_id::text` cast (works today, fragile).

---

## Unblock path

1. Bump `BASELINE_MAX` in `test_compliance_status_not_read.py` from 7 → 8 **WITH** an inline TODO citing Phase 1's intentional JOIN-back at compliance_packet.py:1185, OR add a `# noqa: deprecated-compliance-status — Phase 1 canonical_devices JOIN-back per Gate A v2 plan` marker on the offending line.
2. Add the `current_setting('app.current_org'/'app.current_partner_id', true) IS NOT NULL AND <> ''` guards to mig 319 RLS policies via a follow-on mig (mig 320 — alter policies, do NOT modify 319 retroactively).
3. Fix docstring `BASELINE_MAX = 17` → `BASELINE_MAX = 22` in test_no_raw_discovered_devices_count.py (lines 24 and 132).
4. Drop the loop's `await asyncio.sleep(120)` startup delay to ~5s OR run a synchronous backfill-equivalent UPSERT on backend startup before serving traffic.
5. Update Task #73 Phase 2 description to enumerate 19 readers, not 17.

Once items 1-3 land + sweep is green + a coach re-fork verifies the patches, this commit is APPROVE.

---

**Gate B verdict:** **BLOCK**
**Sweep cited:** 249 PASS / 1 FAIL / 0 SKIP
**Author of this verdict:** Class-B 7-lens Gate B fork
**Pairs with:** `audit/coach-device-dedup-architectural-gate-a-2026-05-13.md` (v1), `audit/coach-device-dedup-implementation-gate-a-2026-05-13.md` (v2), `audit/coach-adb7671a-retro-gate-b-2026-05-13.md` (retro on precursor commit)
