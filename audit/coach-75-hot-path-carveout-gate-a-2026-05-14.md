# Gate A — Task #75: sites.py hot-path `pending_deploys` reader carve-out

**Task #75 | Date 2026-05-14 | Lenses: Steve / Maya / Carol / Coach / Auditor / PM / Counsel**
**Prior Gate A:** `audit/coach-device-dedup-phase-2-batch-2-gate-a-2026-05-13.md` (APPROVE-WITH-FIXES, carve-out mandated as P0-3)

---

## Verdict: APPROVE-WITH-FIXES

## 300-Word Summary

The "sites.py:5644" target is a **stale line number** — the prior Gate A was written against a pre-shift revision. The actual hot-path reader is the **STEP 7c `pending_deploys` query at sites.py:5654–5663**, inside the appliance checkin handler. Line 5644 today is mid-`encrypt_credentials`, unrelated. Implementation must target 5654.

The query: `SELECT dd.local_device_id, dd.ip_address, dd.hostname, dd.os_name, sc.encrypted_data, sc.credential_type FROM discovered_devices dd JOIN site_credentials sc ON ... WHERE dd.site_id=$1 AND dd.device_status='pending_deploy' LIMIT 5`, immediately followed (5688) by `UPDATE discovered_devices SET device_status='deploying' WHERE site_id=$1 AND local_device_id=ANY($2)`. It runs on **every appliance checkin (~144 hits/hr at current 12-appliance fleet, scales linearly)** under `tenant_connection(pool, site_id=checkin.site_id)`.

**The standard CTE-JOIN-back pattern applies cleanly** — the already-shipped sibling at sites.py:7287 (search handler) is the exact mirror. The dedup is *correct and wanted here*: at a 3-appliance site the same `pending_deploy` device appears 3× in `discovered_devices`, so today the handler can emit the same device to `pending_deploys` 3× and the `LIMIT 5` silently starves real devices. Dedup fixes a latent bug, not just cosmetics.

**Carol:** mig 320's `canonical_devices_tenant_isolation` policy (`app.current_tenant = site_id`) is the one that fires for this path — confirmed present, this is exactly why mig 320 was a #75 prerequisite. RLS context is correct.

**Maya:** no new index needed — `canonical_devices_site_ip_mac_idx` + `canonical_devices_site_last_seen_idx` cover the CTE. +8-12% cold / +0-2% warm, on a sub-millisecond query against ~22 rows/site. Negligible.

**FIXES (3 P0):** (1) target line 5654 not 5644; (2) `LIMIT 5` semantics — `local_device_id` write-coupling needs the CTE to expose `dd.local_device_id` and the UPDATE to stay keyed on it; (3) the credential JOIN (`sc.credential_name LIKE dd.hostname || ' (%'`) must survive the CTE — keep it *outside* the CTE.

**24h soak: NOT warranted.** Downgrade to 4h bake. Semantic regression test is mockable and deterministic.

---

## Steve (Engineering)

### What / where / why hot

- **Function:** `appliance_checkin` handler (the `checkin` POST). STEP 7c, "Query devices pending deployment for this site."
- **Exact current query (sites.py:5654–5663):**

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

- **Followed at 5688–5692 by:**

```sql
UPDATE discovered_devices SET device_status = 'deploying',
    agent_deploy_attempted_at = NOW()
WHERE site_id = $1 AND local_device_id = ANY($2::text[])
```

- **Why "hot path":** request *frequency*, not cost. Every appliance checks in every ~5min. 12 appliances → ~144 executions/hr today; scales linearly with fleet. The query itself is cheap (site-scoped, indexed, `LIMIT 5`). It is hot because it is *unconditional on every checkin* and it is *coupled to a write* — a regression here doesn't just slow a dashboard, it can mis-transition device provisioning state fleet-wide.

### Does the standard CTE-JOIN-back apply?

**Yes, cleanly.** No materialized view, no cache, no narrower dedup needed. The hot-path nature does **not** demand a different approach — the query is already `LIMIT 5` and site-scoped; the CTE adds one indexed JOIN against a ~22-row/site table. The shipped sibling at **sites.py:7287** (the `search_site` device reader, Task #74 Batch 2) is the exact pattern to mirror — same handler shape, same `dd.*` projection, same write-adjacent caution comment.

### Why dedup is *correct* here (not just cosmetic)

At a 3-appliance site, one physical `pending_deploy` workstation has **3 rows** in `discovered_devices` (one per scanning appliance). Today's query can return that same device 3×, and with `LIMIT 5` that means **2 real devices get starved** out of the deploy batch every checkin. The CTE collapses to 1 canonical row → `LIMIT 5` now means 5 *distinct* devices. This migration **fixes a latent provisioning-starvation bug**, it is not cosmetic parity.

### Concrete before/after SQL

**BEFORE** — as above (5654–5663).

**AFTER** (mirror sites.py:7287; the credential JOIN stays *outside* the CTE so it's not duplicated per-observation):

```python
# canonical-migration: device_count_per_site — Phase 2 Batch 2 carve-out (Task #75)
# Hot path (every appliance checkin). CTE-JOIN-back collapses multi-appliance
# same-(ip,mac) pending_deploy duplicates so LIMIT 5 counts DISTINCT devices,
# not observations. local_device_id stays from discovered_devices — the
# UPDATE 30 lines below transitions device_status keyed on it.
pending_rows = await deploy_conn.fetch("""
    WITH dd_freshest AS (
        SELECT DISTINCT ON (cd.canonical_id)
               cd.canonical_id, dd.*
          FROM canonical_devices cd
          JOIN discovered_devices dd
            ON dd.site_id = cd.site_id
           AND dd.ip_address = cd.ip_address
           AND COALESCE(dd.mac_address, '') = cd.mac_dedup_key
         WHERE cd.site_id = $1
         ORDER BY cd.canonical_id, dd.last_seen_at DESC
    )
    SELECT dd.local_device_id, dd.ip_address, dd.hostname, dd.os_name,
           sc.encrypted_data, sc.credential_type
    FROM dd_freshest dd
    JOIN site_credentials sc ON sc.site_id = $1
        AND sc.credential_name LIKE dd.hostname || ' (%'
    WHERE dd.device_status = 'pending_deploy'
    LIMIT 5
""", checkin.site_id)
```

The `UPDATE` at 5688 is **unchanged** — `local_device_id` is still the discovered_devices PK and the CTE passes it through verbatim via `dd.*`. The `device_ids` list is built from `p["device_id"]` (= `row["local_device_id"]`) exactly as today.

> Note: cannot use the `FRESHEST_DD_FROM_CANONICAL_CTE` constant from `canonical_devices_helpers.py` *directly* as an f-string here because the helper's CTE re-aliases `cd.last_seen_at AS cd_last_seen_at` etc. and projects extra `cd_*` columns — harmless but the inline minimal form above (matching sites.py:7287's actual shipped shape) is cleaner for this callsite. **Recommend mirroring 7287 verbatim** rather than the helper constant, since 7287 is the proven hot-adjacent precedent. Acceptable either way; pick one and note it.

---

## Maya (Database)

- **Indexes on `canonical_devices` (mig 319 + 320):**
  - `canonical_devices_site_ip_mac_idx` UNIQUE `(site_id, ip_address, mac_dedup_key)`
  - `canonical_devices_site_last_seen_idx` `(site_id, last_seen_at DESC)`
  - `canonical_devices_reconciled_idx` `(reconciled_at)`
  - mig 320 added no indexes (RLS-only).
- **Does the hot path need a new index?** **No.** The CTE's `WHERE cd.site_id = $1` is a direct prefix hit on `canonical_devices_site_ip_mac_idx` (and `_site_last_seen_idx`). The `DISTINCT ON (cd.canonical_id)` sorts a per-site batch of ~22 rows — trivial. The JOIN back to `discovered_devices` uses `(site_id, ip_address, mac_address)` — `discovered_devices` already has its site-scoped index from mig 080. No new index.
- **Added cost:** Phase 1 profiling pinned CTE-vs-raw at **+8–12% on cold cache, +0–2% on warm cache** for single-site reads. On a query that today runs sub-millisecond against a `LIMIT 5` site-scoped scan, the absolute add is microseconds. The checkin handler issues ~15+ queries already; this is noise.
- **`LIMIT 5` interaction:** the `LIMIT` is now applied *after* the `DISTINCT ON` collapse — this is the semantic *fix*, but Maya flags it as the thing the regression test must pin (see Coach). No PgBouncer statement-cache concern: the query has one `$1` param of one type (`text`), no mixed-type `$1` reuse.
- **No partition / COUNT-timeout concern** — `canonical_devices` is unpartitioned, site-scoped reads, no `COUNT(*)`.

**Maya verdict: APPROVE.** No new index. Cost negligible. The one watch-item (LIMIT-after-DISTINCT) is a test concern, not a perf concern.

---

## Carol (Security)

- **Connection context:** the query runs under `async with tenant_connection(pool, site_id=checkin.site_id) as deploy_conn` (sites.py:5653). `tenant_connection()` sets `app.current_tenant` to the site_id literal — it does **not** set `app.current_org` or `app.is_admin`.
- **Which RLS policy fires:** mig 319's three policies (`admin_all`, `tenant_org_isolation`, `partner_isolation`) all key off `app.is_admin` / `app.current_org` / `app.current_partner_id` — **none of which `tenant_connection` sets.** The policy that fires is **mig 320's `canonical_devices_tenant_isolation`**: `USING (app.is_admin = 'true' OR site_id = current_setting('app.current_tenant'))`. This is *exactly* the gap mig 320 was created to close, and exactly why "mig 320 MUST ship + verify deploy BEFORE Task #75 implements" is written into mig 320's header. **Confirmed: mig 320 is shipped (Task #85 completed), the 4th policy exists, the hot-path read is correctly gated.**
- **Is this an admin path that doesn't need dedup?** **No** — it is an *appliance-bearer* path, the most security-sensitive of the device readers. Dedup is needed here for *correctness* (Steve's starvation bug), and RLS coverage is *mandatory* — without mig 320 this query would return **zero rows** under appliance-bearer auth and silently break all device provisioning. The carve-out's RLS prerequisite is satisfied.
- **No PHI:** `canonical_devices` and `discovered_devices` carry IP/MAC/hostname/device_type/os_name — operational network metadata, not PHI. The query also pulls `sc.encrypted_data` (Fernet-encrypted credentials) — unchanged by this migration, still encrypted, still site-scoped via the `sc.site_id = $1` JOIN predicate which the CTE preserves.
- **Cross-site spoofing:** `_enforce_site_id` is enforced upstream in the checkin handler; the CTE's `WHERE cd.site_id = $1` + the RLS policy are belt-and-suspenders.

**Carol verdict: APPROVE.** RLS context confirmed correct; mig 320 prerequisite satisfied; no PHI surface change.

---

## Coach

### What were the prior Gate A's FIXES?

The prior Gate A (`coach-device-dedup-phase-2-batch-2-gate-a-2026-05-13.md`) had **3 P0s**, but two of them (P0-1 routes.py:6396 `id`-contract, P0-2 routes.py:8592 `os_type`/`os_name` drift) were Batch-2 scope and are **closed by Task #76 (completed)**. The P0 that *is* Task #75 is **P0-3: "sites.py:5644 carve-out is mandatory."** Its three stated reasons:

1. **Hot path** — every appliance checkin. ✅ Addressed: this is its own commit + own Gate A (this doc) + Gate B.
2. **Write coupling** — SELECT feeds an `UPDATE device_status` 2 lines later; the read-shape change must not change row-count semantics. ✅ Addressed by the semantic regression test below + Steve's `local_device_id` pass-through.
3. **No SKIP/doc-class marker yet for write-followed-by-read SQL.** ✅ Addressed: this callsite is *not* a SKIP — it migrates. The inline `# canonical-migration:` marker drops the CI baseline 5→4.

The prior Gate A also required a **4h bake of Batch 2 before the hot-path commit** — Batch 2 + Task #76 are completed, so the bake window has elapsed. The carve-out can proceed.

### Semantic regression test design

The test must pin **"dedup did not drop a real device, and did not return the same device twice."** It is mockable and deterministic (mirror `test_cross_appliance_dedup.py`'s `mock_execute` side-effect pattern). New file: `tests/test_pending_deploys_dedup.py`.

Three cases:

1. **`test_multi_appliance_pending_deploy_collapses_to_one`** — mock `canonical_devices` returning 1 canonical row whose `(ip,mac)` matches 3 `discovered_devices` rows (3 appliances). Assert the CTE result yields the device **once**, and `pending_deploys` has 1 entry, and the follow-up UPDATE's `device_ids` array has length 1 (keyed on the freshest observation's `local_device_id`).
2. **`test_limit5_counts_distinct_devices_not_observations`** — the starvation-bug guard. Mock 6 *distinct* canonical devices, each observed by 3 appliances (18 `discovered_devices` rows). Assert exactly **5** distinct devices come back (LIMIT 5 over canonical, not over observations) — pre-migration this could return as few as 2 distinct devices.
3. **`test_real_pending_device_not_dropped`** — negative control. Mock 1 canonical device, 1 observation, `device_status='pending_deploy'`, matching `site_credentials` row. Assert it appears in `pending_deploys` and is transitioned to `deploying`. Confirms the CTE-JOIN-back + credential JOIN didn't silently filter a real device.

Optionally a 4th: `test_credential_join_predicate_survives_cte` — assert a `pending_deploy` device with **no** matching `site_credentials` row is correctly *excluded* (the `LIKE` JOIN is INNER, outside the CTE).

### Is a 24h soak warranted?

**No — downgrade to a 4h bake.** Reasoning:
- The change is a **read-path SQL-shape change**, fully covered by the deterministic mockable test above. It is not a migration, not a schema change, not a write-path change (the UPDATE is byte-identical).
- 24h soaks are warranted for things that only manifest under real fleet timing/concurrency or accumulate over time — `appliance_status_dual_source_drift`, MTTR soaks, partition rollovers. This is none of those.
- The *real* risk (PgBouncer statement-cache pathology on a new CTE shape under the hot path) surfaces within **minutes** of deploy, not hours — and the sibling CTE at sites.py:7287 already shipped that exact shape into PgBouncer. There is no novel statement shape here.
- **4h bake** + a runtime check (`docker logs` for "pending deploys lookup failed" rate stays at baseline; spot a `pending_deploys`-emitting checkin in prod) is sufficient and is the same gate the prior Gate A applied to Batch 2 itself.

Gate B must still cite: (a) the 4h post-deploy bake telemetry (no new error class in checkin handler), (b) the full pre-push CI parity sweep, (c) the CI baseline drop 5→4 in `test_no_raw_discovered_devices_count.py` (`BASELINE_MAX` is currently 6 — confirm whether it needs decrementing or whether 5→4 is within the existing slack; if `BASELINE_MAX` is to be tightened it must be done in this commit).

---

## Auditor (OCR)

`pending_deploys` feeds the **appliance checkin response** — it is consumed by the Go daemon to trigger agent auto-deployment. It is **not** a customer-facing or auditor-facing surface: no portal view, no compliance packet, no auditor kit, no §164.528 disclosure accounting reads it. The customer never *sees* `pending_deploys`; they see the *eventual result* (devices with agents deployed).

**Is the dedup a silent data change needing a note?** Marginally — and the note belongs in the **commit body**, not a customer artifact. The behavioral change is: *fewer redundant deploy attempts at multi-appliance sites* (a 3× duplicate device was previously deploy-attempted up to 3× across consecutive checkins; now once). That is strictly an improvement and reduces daemon churn. No customer-visible count changes, no PDF number changes (the device-count-on-PDF fix was mig 319/compliance_packet, already shipped). **No customer note required. Commit body should state the starvation-bug fix explicitly so it's discoverable.**

**Auditor verdict: N/A for customer artifacts; commit-body note recommended.**

---

## PM

- **Effort:** ~1–1.5h. One query rewrite (mirror sites.py:7287), one inline marker, one new test file (~3 cases, mockable, ~1h — `test_cross_appliance_dedup.py` is the copy-paste skeleton). Plus Gate B.
- **Critical path:** the 24h soak was the only thing that *could* have been on a critical path — and it's being **downgraded to a 4h bake**, so #75 no longer blocks anything overnight. #75 is the last carve-out of Phase 2 Batch 2; completing it drops the CI baseline 5→4 and unblocks **Phase 2 Batch 3** scoping (the remaining 4 SKIP-class callsites). Batch 3 is not started, so there is slack.
- **Sequencing:** ship the commit → 4h bake → Gate B → mark #75 complete → scope Batch 3. Total calendar: same day.

**PM verdict: APPROVE.** ~1.5h + 4h bake, same-day, not on anyone's critical path.

---

## Counsel — N/A

`pending_deploys` is an internal appliance↔Central-Command provisioning channel. No customer-facing artifact, no marketing/legal copy, no BAA-scope change, no §164.528 surface. Counsel's 7 rules check: Rule 1 (canonical metric) is *satisfied* by this migration, not threatened — it drives the last reader onto the canonical source. Rule 2 (no raw PHI) — no PHI in scope. Nothing for Counsel here.

---

## Gate A Final Verdict

**APPROVE-WITH-FIXES.** The CTE-JOIN-back pattern applies cleanly; mig 320's RLS prerequisite is satisfied; no new index needed; the migration fixes a latent LIMIT-5 starvation bug. Three P0 fixes before commit:

### P0 acceptance criteria

1. **P0-1 — target the correct line.** The implementation must modify the STEP 7c `pending_deploys` query at **sites.py:5654–5663** (and leave the `UPDATE` at ~5688 byte-identical). "5644" in the prior Gate A is a stale line number — line 5644 today is inside `encrypt_credentials`. Verify by grepping `device_status = 'pending_deploy'` before editing.
2. **P0-2 — `local_device_id` write-coupling preserved.** The CTE must pass `dd.*` (or explicitly `dd.local_device_id`) through so the result still carries `local_device_id`; the `device_ids` list and the `UPDATE ... WHERE local_device_id = ANY($2)` stay keyed on it unchanged. Pinned by semantic test case 1.
3. **P0-3 — credential JOIN stays OUTSIDE the CTE.** The `JOIN site_credentials sc ON sc.credential_name LIKE dd.hostname || ' (%'` must be applied to `dd_freshest`, not inside the CTE — putting it inside would re-evaluate the `LIKE` per raw observation and re-introduce duplication. Pinned by optional test case 4.

### Gate B requirements

- New test file `tests/test_pending_deploys_dedup.py` with the 3 (recommend 4) cases above — all passing.
- Inline `# canonical-migration: device_count_per_site — Phase 2 Batch 2 carve-out (Task #75)` marker on the consuming line.
- `test_no_raw_discovered_devices_count.py` baseline drops 5→4 (confirm whether `BASELINE_MAX=6` needs tightening in this commit or has slack).
- **4h bake** (NOT 24h soak) — Gate B cites post-deploy checkin-handler telemetry showing no new `pending deploys lookup failed` error class + one observed prod checkin emitting `pending_deploys`.
- Full pre-push CI parity sweep cited with pass count.
- Gate B fork runs the full source-level test sweep (not diff-only) per the Session 220 lock-in.
- Commit body notes the LIMIT-5 starvation-bug fix explicitly (Auditor recommendation).
