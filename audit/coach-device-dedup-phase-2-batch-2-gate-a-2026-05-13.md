# Gate A — Phase 2 Batch 2 (admin-surface canonical_devices migration)

**Task #74 | Date 2026-05-13 | Lenses: Steve / Maya / Carol / Coach / PM (Auditor + Attorney N/A — admin-only readers, no PHI surface)**

---

## Verdict: APPROVE-WITH-FIXES

## 200-Word Summary

Batch 2 covers 10 admin-surface migrations (routes.py × 7 + sites.py × 3) that are functionally low risk: admin-context readers run under `current_setting('app.is_admin')='true'`, so the `canonical_devices_admin_all` policy pass-through is verified clean (Carol). All seven routes.py callsites are read-only handlers behind `Depends(require_auth)`. Steve classifies 4 of 10 as **COUNT-ONLY** (use `COUNT_CANONICAL_DEVICES_SQL` direct), 5 as **CTE-JOIN-back** (need `compliance_status`/`device_status`/owner-appliance fields not on canonical), and **1 SKIP** (sites.py:1897 — mesh assignments debug view, owner_appliance_id is per-observation not per-canonical-device, equivalent to the Batch 1 client_portal:4828 write-path SKIP class).

**sites.py:5644 is CARVED OUT to its own commit + 24h soak per Phase 2 Gate A P0.** It is a hot-path checkin handler reader (every appliance checkin every 5min) with a write-side UPDATE on `device_status='deploying'` two lines later. Coach P0: ship Batch 2 with **10 callsites → CI baseline 14 → 5** (one bake commit), then sites.py:5644 as its own commit + Gate B + 24h soak. Three P0 fixes required before commit; full matrix below.

---

## Per-Callsite Classification Matrix

| # | File | Line | Pattern | Caller | Decision | Rationale |
|---|------|------|---------|--------|----------|-----------|
| 1 | routes.py | 5313 | `SELECT d.id, d.site_id, …, d.compliance_status, d.device_status, d.owner_appliance_id` JOIN sites + v_appliances_current WHERE site_id = ANY($1) LIMIT/OFFSET | `GET /organizations/{org_id}/devices` | **CTE-JOIN-MULTI** | Needs compliance_status + device_status + owner_appliance_id; multi-site filter. Use `FRESHEST_DD_FROM_CANONICAL_CTE_MULTI_SITE`. |
| 2 | routes.py | 5322 | `SELECT count(*) FROM discovered_devices WHERE site_id = ANY($1)` | same handler | **COUNT-ONLY (multi)** | Use `COUNT_CANONICAL_DEVICES_MULTI_SITE_SQL`. |
| 3 | routes.py | 5331 | `count(*) … FILTER (WHERE compliance_status = …)` 5-bucket aggregation by site_id = ANY($1) | same handler | **CTE-JOIN-MULTI** | Needs compliance_status filter — canonical_devices doesn't carry it. Wrap with multi-site CTE then count filtered. |
| 4 | routes.py | 5762 | `count(*) FILTER (WHERE device_status = 'agent_active'), count(*) FILTER (WHERE device_status NOT IN ('ignored','archived'))` WHERE site_id = $1 | `GET /sites/{id}/compliance-health` (network coverage subquery) | **CTE-JOIN-back** | Needs device_status — not on canonical. Single-site CTE. |
| 5 | routes.py | 5857 | `SELECT d.hostname, d.ip_address, d.device_type, d.os_name, d.compliance_status WHERE d.site_id = $1` | `GET /sites/{id}/devices-at-risk` (enrichment dict) | **CTE-JOIN-back** | Needs compliance_status. Caught in try/except — best-effort enrichment. |
| 6 | routes.py | 6396 | `SELECT id, site_id, mac_address, ip_address, hostname, device_type, vendor, compliance_status, first_seen, last_seen WHERE site_id = $1` | site detail export | **CTE-JOIN-back** | Needs compliance_status + vendor (not on canonical). `id` is dd.id, not canonical_id — caller-visible: see P0-1. |
| 7 | routes.py | 8592 | `SELECT hostname, os_type, compliance_status, last_seen WHERE site_id = $1` | `POST /sites/{id}/compliance-packet` | **CTE-JOIN-back** | Needs compliance_status + os_type. **NOTE:** os_type vs os_name field-name drift — see P0-2. |
| 8 | sites.py | 1897 | `SELECT dd.ip_address, dd.hostname, dd.os_name, dd.owner_appliance_id, sa.hostname, sa.mac_address, dd.last_seen_at` LEFT JOIN site_appliances WHERE site_id = $1 | `GET /{site_id}/appliances/mesh/assignments` | **SKIP** | owner_appliance_id is **per-observation** (per-row in discovered_devices), not a canonical_devices field. The whole point of the endpoint is "which appliance scanned each observation" — collapsing to canonical loses the semantic. Add SKIP marker + EXEMPT_FILES entry. |
| 9 | sites.py | 5644 | `SELECT dd.local_device_id, dd.ip_address, dd.hostname, dd.os_name, sc.encrypted_data, sc.credential_type` JOIN site_credentials … WHERE device_status = 'pending_deploy' LIMIT 5 + UPDATE 2 lines later | checkin handler step 7c | **CARVE OUT** | Hot path (every appliance checkin). `local_device_id` is dd-specific PK (write-path UPDATE 2 lines later transitions device_status='deploying'). Ship as its own commit + Gate B + 24h soak. |
| 10 | sites.py | 7271 | `SELECT id::text, hostname, ip_address, mac_address, device_type WHERE site_id = $1 AND (hostname|ip_address|mac_address ILIKE $2)` | `POST /{site_id}/search` | **CTE-JOIN-back** | Multi-column search across the observation row. canonical_id is fine identity for search-result; CTE-JOIN preserves the ILIKE-able fields. Wrap try/except remains. |

**Net: 10 callsites in scope, 8 migrate (5 CTE-JOIN + 2 COUNT-only + 1 multi-CTE), 1 SKIP-with-marker, 1 CARVE-OUT.**

**CI baseline drive-down:**
- Current: 14
- After Batch 2 (8 migrations + 1 SKIP marker landed): 14 → 5
- After sites.py:5644 carve-out commit: 5 → 4 (the remaining 4 are Phase 2 Batch 3 — see PM section)

---

## Top 3 P0s Before Commit

### P0-1 — routes.py:6396 returns `id` field that is a customer-observable contract break

The query returns `id` to a JSON payload consumed by the site-detail export. Today `id` is `discovered_devices.id` (auto-increment). The CTE-JOIN-back exposes `dd.id` which **changes value** when the freshest observation rolls over (multi-appliance same-IP). If any frontend / partner integration / auditor-kit consumer keys off this `id`, the migration silently breaks them.

**Fix:** Either
- (a) Add `canonical_id` as a sibling field, keep `id=dd.id` with explicit comment "per-observation row id; canonical_id is the stable device identity", OR
- (b) Switch returned `id` to `canonical_id` and bump the export's documented schema version.

Recommend (a) — non-breaking, additive. Adds 1 field to the export JSON.

### P0-2 — routes.py:8592 selects `os_type` but discovered_devices column is `os_name`

```python
SELECT hostname, os_type, compliance_status, last_seen
FROM discovered_devices
```

`discovered_devices` schema has `os_name`, not `os_type` (the workstations table has `os_type`). This is an existing latent bug that was masked by `try/except` somewhere — or 8592 currently returns NULL for os_type silently. **Verify before migration**: if the query currently works (e.g., os_type is a synonym/view col), document it; if it raises, the migration is an opportunity to fix the column name. Do NOT preserve the wrong column name through the CTE-JOIN-back.

**Fix:** Run the query against prod read-replica BEFORE migration. If 0-row or column-missing error, fix to `os_name` AS `os_type` in the migrated query (preserve JSON contract).

### P0-3 — sites.py:5644 carve-out is mandatory, not optional

Three reasons this can NOT ship with Batch 2:
1. **Hot path:** every appliance checkin executes this (~12 appliances × 12/hr = 144 hits/hr baseline; will scale with fleet).
2. **Write coupling:** the SELECT feeds a UPDATE 2 lines later that mutates `device_status`. Any CTE-based read-shape change must be verified to NOT change row-count semantics (LIMIT 5 across canonical rows vs. dd rows).
3. **No SKIP marker yet for write-path-followed-by-read SQL.** The Batch 1 SKIP class (client_portal:4828) was a pure write-path; this is a mixed read+write. Needs explicit doc-class addition to `canonical_metrics.py`.

**Fix:** Ship Batch 2 with sites.py:5644 explicitly excluded + a `# canonical-migration: device_count_per_site — DEFERRED Phase 2 Batch 3 (Task #74)` placeholder marker that does NOT drop the baseline. Carve out as commit 3 + Gate A + Gate B + 24h soak with explicit pending_deploys metric.

---

## Steve (Engineering)

- 7 routes.py callsites cluster as: 3-in-a-cluster handler at 5313/5322/5331 (single handler), then 4 standalone (5762, 5857, 6396, 8592). The cluster needs a SINGLE multi-site CTE used by all three reads; recommend computing the CTE row-set once and aggregating in-Python OR using the multi-site CTE constant from the helpers module across all three queries.
- Helper module is already shaped correctly for multi-site (`FRESHEST_DD_FROM_CANONICAL_CTE_MULTI_SITE`). Batch 2 should add a third constant if a count-multi-site variant is needed (already there: `COUNT_CANONICAL_DEVICES_MULTI_SITE_SQL`).
- routes.py:8592 is inside an 11-query handler (compliance-packet generation); the CTE-JOIN-back doesn't materially change handler cost.

## Maya (Database)

- **Admin scope, ANY($1) site_id concern:** routes.py:5313/5322/5331 query org-scoped sites. For a partner with 50 sites this becomes `site_id = ANY($1)` over a 50-element array. canonical_devices is well-indexed (`canonical_devices_site_ip_mac_idx UNIQUE (site_id, ip_address, mac_dedup_key)`) — index-scan path. **No new cost concern.**
- **CTE-JOIN-back cost:** per-site, CTE materialises ~30-300 rows then JOINs dd (1:1 via the dedup key). The DISTINCT ON sort is on small per-site batches; the existing `canonical_devices_site_last_seen_idx` covers the predicate. Performance ratio CTE-vs-raw was profiled in Phase 1 at **+8-12% latency on cold cache, +0-2% on warm cache** for single-site reads. Multi-site amortises lower (per-site cost stays constant).
- **No partition concern** — canonical_devices is not partitioned today; if it grows >1M rows, partition by site_id hash. Not Batch 2 work.
- **No `COUNT(*)` timeout concern** — site-scoped COUNT against well-indexed table is fast; lesson learned from prometheus_metrics.py:521 applies only to fleet-wide unbounded counts.

## Carol (Security)

- All 10 Batch 2 callsites run under admin context (`Depends(require_auth)` → `admin_transaction()` or `tenant_connection()`). The `canonical_devices_admin_all` policy fires `WITH CHECK` on the admin setting — pass-through clean.
- **Verified non-admin spoofing risk: none.** routes.py:5313 has `_check_org_access(user, org_id)` before issuing the query. routes.py:5762/5857/6396/8592 all call `check_site_access_pool(user, site_id)`. sites.py:1897/5644/7271 use `tenant_connection(pool, site_id=...)` so even if `app.is_admin` is false the RLS tenant policy gates the read.
- **No PHI surface in either schema** — both discovered_devices and canonical_devices store IP/MAC/hostname/device_type. Not PHI; HIPAA-permissible at Central Command.
- **Owner_appliance_id is operational, not security-sensitive** — sites.py:1897 SKIP doesn't open a leak; the field is debug-view operator metadata.

## Coach

- **Strong recommendation: carve out sites.py:5644.** It is materially different from the other 9: hot-path + read-coupled-to-write + checkin-critical. Bundling it with the other 9 means a Gate B regression of any kind requires reverting the entire Batch 2, including the safe admin readers. Carve-out cost is 1 commit + 1 Gate A + 1 Gate B (~2-3h). Carve-out benefit: 9 safe migrations land independently; 1 risky migration gets its own Gate B with explicit checkin-soak metric (pending_deploys-per-checkin steady-state).
- **Bake commit gap:** ship Batch 2 (9 migrations + 1 SKIP), wait 4h for prod telemetry, THEN ship the sites.py:5644 commit. The 4h bake catches any CTE-shape pgbouncer-statement-cache pathology before the hot path inherits it.

## PM

- Batch 2: 9 migrations + 1 SKIP marker + carve-out = **~2-3h work + Gate B**. Reasonable.
- Carve-out commit: **~1h work + Gate A + Gate B + 24h soak**. Total Batch 2 calendar: 1-2 days.
- **Phase 2 Batch 3 scope** (post-Batch 2): the 4 remaining baseline (post Batch 2 → 5, post-carve-out → 4) are the SKIP-class callsites in non-EXEMPT files that need either (a) classification into EXEMPT_FILES with rationale or (b) explicit per-callsite `# canonical-migration: … SKIP` markers. Enumerate after Batch 2 lands.

## Auditor (OCR) — N/A

No customer-facing attestation or §164.528 disclosure-accounting surface touched. Admin-only operator readers.

## Attorney — N/A

Internal admin readers; no customer-visible contract change, no marketing/legal copy change, no BAA scope change.

---

## Gate A Final Verdict

**APPROVE-WITH-FIXES.** Address all 3 P0s before commit; carve out sites.py:5644 to its own commit; bake Batch 2 for 4h before shipping the hot-path commit. Gate B at completion MUST cite both the 4h bake-window prod telemetry (no regression) and the full pre-push CI parity sweep result.

**P0 acceptance criteria:**
1. routes.py:6396 export schema preserves `id` semantics (recommend additive `canonical_id` field).
2. routes.py:8592 `os_type` vs `os_name` confirmed against prod schema; query corrected to actual column name; JSON contract preserved.
3. sites.py:5644 NOT in Batch 2 commit; deferred marker added; Phase 2 Batch 3 task tracked.

**Gate B requirements:**
- CI gate `test_no_raw_discovered_devices_count.py` BASELINE_MAX: 14 → 5 (post-Batch-2) → 4 (post-carve-out).
- All 9 migrated callsites carry the `# canonical-migration: device_count_per_site — Phase 2 Batch 2 (Task #74)` marker.
- sites.py:1897 SKIP marker added with rationale comment ("owner_appliance_id is per-observation, canonical_devices doesn't carry it").
- Full pre-push CI parity sweep cited with pass count.
- Carve-out commit verdict references this Gate A doc + its own follow-up Gate A.
