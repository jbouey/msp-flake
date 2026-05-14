# Gate A — sites.py:5644 hot-path canonical_devices carve-out

**Task #75 | Date 2026-05-13 | Lenses: Steve / Maya / Carol / Coach / PM (OCR + Counsel N/A — operational hot-path, no customer artifact, no PHI)**

Per Phase 2 Batch 2 Gate A (audit/coach-device-dedup-phase-2-batch-2-gate-a-2026-05-13.md) P0-3 explicit carve-out requirement: own Gate A + 24h soak + own Gate B. This is Gate A.

---

## Verdict: APPROVE-WITH-FIXES

## 200-Word Summary

sites.py:5644 is the `pending_deploy` poller inside the appliance_checkin handler — a mixed READ-then-WRITE pattern that fires every checkin (~12 appliances × 12/hr = 144 hits/hr today, scaling linearly with fleet). The SELECT returns `local_device_id` (a dd-specific PK), feeds a list passed to `encrypt_credentials`, then a sibling UPDATE on dd transitions `device_status='pending_deploy' → 'deploying'` keyed by the same `local_device_id`. Both statements run inside one `tenant_connection(pool, site_id=...)` async block, so RLS pins the read to the site's row-set.

Classification: **CTE-JOIN-back single-site** using `FRESHEST_DD_FROM_CANONICAL_CTE` from helpers (already proven across ~10 callsites in Batch 1+2). The CTE narrows to canonical rows for `$1`, JOINs back the freshest dd observation, and we SELECT `local_device_id` from `dd_freshest` — **the UPDATE on dd 2 lines later is UNCHANGED** because `local_device_id` is dd-row-anchored, not canonical-anchored. LIMIT 5 semantics shift from "5 dd rows" to "5 canonical devices" — for `pending_deploy` candidates this is identical at steady state (deploys are 1:1 dd:canonical pre-takeover). Three P0 fixes required: explicit p99 latency SLO, write-coupling regression test, 24h soak with pending_deploys-per-checkin metric.

---

## Steve (Engineering) — SQL classification + risk

**Current SQL (lines 5654-5663):**
```sql
SELECT dd.local_device_id, dd.ip_address, dd.hostname, dd.os_name,
       sc.encrypted_data, sc.credential_type
FROM discovered_devices dd
JOIN site_credentials sc ON sc.site_id = $1
    AND sc.credential_name LIKE dd.hostname || ' (%'
WHERE dd.site_id = $1
    AND dd.device_status = 'pending_deploy'
LIMIT 5
```

**Migrated CTE-JOIN-back shape:**
```sql
WITH dd_freshest AS (
    SELECT DISTINCT ON (cd.canonical_id)
           cd.canonical_id, cd.site_id AS cd_site_id,
           cd.ip_address AS cd_ip, cd.mac_address AS cd_mac,
           dd.*
      FROM canonical_devices cd
      JOIN discovered_devices dd
        ON dd.site_id = cd.site_id
       AND dd.ip_address = cd.ip_address
       AND COALESCE(dd.mac_address, '') = cd.mac_dedup_key
     WHERE cd.site_id = $1
     ORDER BY cd.canonical_id, dd.last_seen_at DESC
)
SELECT dd_freshest.local_device_id,
       dd_freshest.ip_address,
       dd_freshest.hostname,
       dd_freshest.os_name,
       sc.encrypted_data,
       sc.credential_type
FROM dd_freshest
JOIN site_credentials sc ON sc.site_id = $1
    AND sc.credential_name LIKE dd_freshest.hostname || ' (%'
WHERE dd_freshest.device_status = 'pending_deploy'
LIMIT 5
```

**Classification: CTE-JOIN-back single-site.** Use the existing `FRESHEST_DD_FROM_CANONICAL_CTE` constant from `canonical_devices_helpers` — DO NOT inline a hand-written CTE (Phase 2 design intent: 1 shape, 1 helper, no drift).

**Latency budget (per Steve hot-path target 50ms p99 for the entire checkin handler):**
- Current SELECT: ~3-8ms warm cache, single-site, indexed on (site_id, device_status).
- CTE-JOIN-back: +0-2ms warm cache per Maya's Phase 1 profile (well-indexed canonical_devices_site_last_seen_idx covers the predicate). Cold cache penalty +8-12ms is a one-time pgbouncer-statement-cache warmup, amortizes within 5 checkins per appliance.
- Net: still well within the 50ms handler budget. The dominant cost in checkin is the heartbeat write + signature verify (~15-25ms), not this query.

**Risk: NONE on the READ side after migration. Material risk on the SEMANTIC of LIMIT 5:**
- Pre-fix: "first 5 dd rows where device_status='pending_deploy'" — if two appliances independently observed the same (ip,mac) device, BOTH dd rows are in the result set, but only one canonical row exists.
- Post-fix: "first 5 canonical devices whose freshest dd observation is pending_deploy" — duplicates collapse, so you get 5 distinct devices.
- **This is a FEATURE, not a regression** — the pre-fix behavior was a latent bug: it would issue duplicate deploys for multi-observed devices, wasting credential decrypt + encrypt cycles and racing the UPDATE.

## Maya (Database) — index + lock contention

- **Index hit rate:** `canonical_devices_site_last_seen_idx (site_id, last_seen_at DESC)` covers the CTE's `WHERE cd.site_id = $1 ORDER BY ... last_seen_at DESC` predicate cleanly. dd JOIN uses `idx_discovered_devices_site_dedup` (site_id, ip_address, COALESCE(mac_address,'')) which is what the JOIN ON condition is structured to hit. Both index-only-scan-eligible paths confirmed against the Phase 1 EXPLAIN traces.
- **Lock contention with reconciliation_loop writers:** the reconciliation loop performs `INSERT ... ON CONFLICT (site_id, ip_address, mac_dedup_key) DO UPDATE` against canonical_devices every ~30s. The CTE is a pure SELECT (no row locks beyond the standard MVCC snapshot), so reader doesn't block writer. The reverse is also fine — INSERT-ON-CONFLICT-UPDATE only locks the conflicting row, not the index range. **No new contention class.**
- **PgBouncer statement cache:** the new query string is one prepared statement. First checkin per pgbouncer-pool backend pays ~8ms prepare cost, then ~3ms reads. Across the fleet of 12 appliances × 12/hr × N PgBouncer backends (~25), the prepare amortizes within the first 30min.
- **No COUNT(*) timeout concern** — this is a LIMIT 5 read, not an unbounded count. Lesson learned from prometheus_metrics.py:521 does not apply.
- **No partition concern** — canonical_devices is not partitioned; will need partitioning if it crosses 1M rows fleet-wide. Today ~30K rows.

## Carol (Security) — RLS verification

- **Read path:** `tenant_connection(pool, site_id=checkin.site_id)` is invoked at line 5653. This sets `app.current_site_id` AND opens an asyncpg transaction. The canonical_devices table has THREE policies (mig 319):
  1. `canonical_devices_admin_all` — fires only when `app.is_admin='true'`. Checkin handler does NOT set is_admin (correct).
  2. `canonical_devices_tenant_org_isolation` — fires when `app.current_org` is set + non-empty. Checkin doesn't set current_org either (it's an appliance-bearer auth path, not a client-portal path).
  3. `canonical_devices_partner_isolation` — fires when `app.current_partner_id` is set. Not set in checkin.
- **Concern:** `tenant_connection` sets `app.current_site_id` but NONE of the three canonical_devices policies gate on `app.current_site_id`. **Need to verify the tenant_connection helper also sets one of the policy-recognized vars** OR the SET LOCAL block establishes a context-equivalent guard. Reviewed `tenant_middleware.tenant_connection`: it sets `app.current_site_id` AND `app.is_admin` (via the site->tenant resolution path that runs at line ~85). For appliance-bearer auth this falls to `is_admin=false`, and none of the three policies match, leaving the table read-blocked under default-deny RLS.

  **P0-CAROL — verify before commit:** run `SELECT * FROM canonical_devices LIMIT 1` inside a tenant_connection(site_id=...) block on prod read-replica. If returns 0 rows under RLS, we need either (a) add a `canonical_devices_site_id_match` policy that mirrors discovered_devices' equivalent, OR (b) the checkin handler explicitly SETs `app.is_admin='true'` for this read (NOT recommended — privilege escalation in a hot path).

  Discovered_devices today has a `discovered_devices_appliance_checkin` policy (verify in mig list) that allows site-id match under `app.current_site_id`. Canonical_devices needs the parity policy. **Phase 1 RLS verification was for admin-context reads (Batch 2 P0-C). Hot-path checkin runs under appliance-bearer-tenant context, which IS a different RLS branch.**

- **Write path UNCHANGED:** the UPDATE on dd at line 5688 is unchanged shape (`device_status='deploying' WHERE site_id=$1 AND local_device_id=ANY($2)`). dd RLS has the appliance-checkin policy. No new write-side risk.
- **No PHI surface** — hostnames + IPs + os_name + credential_name + encrypted credential blob. The blob is Fernet-encrypted at rest. Not PHI.

## Coach — sibling-pattern + bake-window

- **Sibling-pattern confirmation:** Phase 2 Batch 1 + Batch 2 shipped 10 CTE-JOIN-back callsites using the same `FRESHEST_DD_FROM_CANONICAL_CTE` helper. Three of those (compliance_packet.py:1187, device_sync.py:796, sites.py:7289 the /search endpoint) are structurally identical to this one: single-site, narrow column selection, LIMIT-bounded. Zero regressions observed in 4h bake post-Batch-2. The shape is proven.
- **Critical difference vs. the 10 priors:** none of the 10 were inside a hot-path checkin. The pattern is proven for cost+correctness; what we're soaking for is *checkin-handler interaction* — pgbouncer statement-cache warmup latency, RLS-policy-fire cost under appliance-bearer auth, and whether the new CTE invalidates any existing prepared-statement cache entries.
- **Bake-window prescription:** ship the migration with the existing Batch 2 commit already 4h baked. Do NOT ship as one big batch — keep this as commit 3 per the original Batch 2 P0-3. Once shipped, 24h continuous soak before Gate B. **Rationale:** 24h covers two full chaos-lab cycles (2x/day) AND a Sunday-Monday transition where appliance-checkin patterns shift (workstation power cycles, agent restarts).

## PM — schedule + 24h wait

- **Effort breakdown:**
  - Impl: 30min (1 helper-import + 1 SQL rewrite + 1 marker comment + 1 unit test for the LIMIT-5-canonical-vs-dd semantic shift)
  - Gate A: COMPLETE (this doc)
  - Pre-push CI parity sweep: ~92s (per the .githooks/full-test-sweep.sh standard)
  - Push + CI green: ~8-12min
  - Production deploy: ~5min auto-deploy
  - **24h soak: hard 24h wall-clock from prod-deploy SHA confirmed**
  - Gate B: 30min (fork-based, must cite checkin p99 latency + pending_deploys-per-checkin steady-state from telemetry)

- **24h soak schedule (proposed):**
  - T=0: commit shipped, prod SHA confirmed via `/api/version`
  - T+1h: first telemetry checkpoint — checkin handler p99 latency, query latency, error rate
  - T+4h: second checkpoint — covers a full chaos-lab cycle
  - T+12h: third checkpoint — overnight steady-state
  - T+24h: final checkpoint + Gate B fork-based adversarial review
  - Total wall-clock: 1 calendar day

- **Soak metrics to track:**
  1. **PRIMARY: appliance_checkin handler p99 latency** (Prometheus histogram, current baseline ~28ms). SLO: ≤ baseline + 5ms (33ms ceiling).
  2. **Query-specific latency for the migrated SQL** (via slog timing or pg_stat_statements). SLO: ≤ 15ms p99.
  3. **`pending_deploys` count per checkin** — should hold steady at 0-2 in chaos-lab; spike would indicate UPDATE-after-SELECT is no longer transitioning the right rows.
  4. **Sentry/error-rate for checkin handler** — no regression in 4xx/5xx.
  5. **PgBouncer prepared-statement cache hit rate** — should warm up within 30min and stay >95%.
  6. **canonical_devices row count + reconciliation_loop lag** — orthogonal but worth eyeballing.

- **Rollback plan:** single revert commit (the migration is one block of contiguous SQL). Revert + force-push-to-deploy = ~10min. Hot-path means rollback decision must be made within 5min of regression detection — recommend Coach + on-call ping if PRIMARY metric breaches SLO.

## Auditor (OCR) — N/A

Operational hot-path. No customer-facing attestation, no §164.528 surface, no auditor-kit byte-identity contract touched.

## Counsel — N/A

Internal-only operator code path. No marketing/legal copy change, no BAA scope change, no privileged-chain registration.

---

## Top 3 P0s Before Commit

### P0-1 — Verify RLS allows tenant-connection read of canonical_devices

`tenant_connection(pool, site_id=...)` under appliance-bearer auth sets `app.current_site_id` and `app.is_admin='false'`. None of the three mig 319 canonical_devices policies fire on `app.current_site_id`. **Run on prod read-replica BEFORE commit:**
```sql
SET LOCAL app.current_site_id = '<test_site_id>';
SET LOCAL app.is_admin = 'false';
SELECT COUNT(*) FROM canonical_devices WHERE site_id = '<test_site_id>';
```
If 0, we need a parity policy (sibling to discovered_devices' appliance-checkin policy). If non-zero, the policies somehow do let through (worth understanding why before relying on it). **Block commit until verified.**

### P0-2 — Write-coupling regression test

Add `tests/test_sites_pending_deploy_canonical_semantics.py` that:
1. Seeds 2 appliances observing the same (ip, mac) → 2 dd rows + 1 canonical_devices row.
2. Marks both dd rows `device_status='pending_deploy'`.
3. Invokes the migrated query.
4. Asserts: exactly 1 result row (not 2 as pre-fix), local_device_id is the freshest one.
5. Asserts: UPDATE-after-SELECT transitions the freshest dd row to 'deploying', leaves the other untouched (no orphan dd row blocked from future re-deploy cycles).

This pins the LIMIT-5 semantic shift in CI. Without it, a future canonical_devices_helpers refactor could silently break the dedup.

### P0-3 — Soak-metric Prometheus histogram explicit before commit

The checkin handler Prometheus histogram `appliance_checkin_duration_seconds` exists; verify the bucket boundaries cover the 25-50ms range with adequate resolution (need at least 30ms + 40ms + 50ms buckets to detect 5ms regression). If not, ADD the bucket BEFORE commit so the 24h soak has the data to make a Gate B call. Otherwise Gate B has to fall back on slog timing aggregation, which is noisier.

---

## Gate A Final Verdict

**APPROVE-WITH-FIXES.** Three P0s before commit:
1. RLS verify (Carol P0-1)
2. Semantic regression test (P0-2)
3. Prometheus histogram bucket resolution (P0-3)

**Commit order requirement:** sites.py:5644 is COMMIT 3 in the Phase 2 sequence — Batch 1 already baked + Batch 2 must be 4h-baked before this lands. Per Phase 2 Batch 2 Gate A P0-3.

**Gate B requirements:**
- Cite all 6 soak metrics (PRIMARY checkin p99 + query-specific p99 + pending_deploys count + error rate + pgbouncer cache hit + canonical row count).
- 24h continuous wall-clock from production deploy SHA.
- Full pre-push CI parity sweep result with pass count.
- Fork-based adversarial review (4 lenses) — author cannot self-attest.
- Cite this Gate A doc + the Batch 2 Gate A doc for chain-of-custody.

**Soak schedule:**
- T=0: deploy
- T+1h, T+4h, T+12h, T+24h: telemetry checkpoints
- T+24h: Gate B fork

**Rollback trigger:** any of PRIMARY p99 > 33ms sustained 5min / pending_deploys regression / error-rate breach. Single-revert commit pre-prepared.
