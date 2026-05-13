# Gate A — Device-Dedup Architectural Design (Task #73)

**Date:** 2026-05-13
**Fork lens:** Class-B 7-lens (Steve / Maya / Carol / Coach / OCR / PM / Counsel)
**Design under review:** `audit/device-dedup-architectural-design-2026-05-13.md`
**Verdict:** **APPROVE-WITH-FIXES — Option B (canonical_devices table)** with mandatory P0/P1 closures listed in §11.

---

## §0 — 250-word executive summary

Empirical claim verified: north-valley-branch-2 has 36 rows / 22 unique (ip,mac) / 14 duplicates. The pattern is sharper than the design states — **all 14 duplicates are mac_address=NULL rows**, and they're 7 IPs each duplicated exactly 3× (one per appliance). The 15 unique rows ALL have a populated mac. So `(ip, mac)` is the wrong natural key — `(ip, COALESCE(mac,''))` is what's needed, and the all-NULL-mac duplicate set means **the ARP-only-no-mac path is the entire bug**, not "many appliances seeing same device with different macs."

Sibling-surface enumeration found **at least 9 readers** beyond `get_site_devices()` that count or aggregate `discovered_devices` without dedup — including `compliance_packet.py:1167` (monthly evidence packet customer artifact, emits `total_devices`), `partners.py:1888 / 2583 / 2587 / 2595` (partner fleet view + portfolio), `portal.py:1251` (portal device count), `routes.py:5308 / 5322 / 5762` (admin devices, all-sites count, network-coverage scoring), `prometheus_metrics.py:611` (operator gauge), `client_portal.py:1289` (client portal site detail), `device_sync.py:913` (`get_site_device_counts` — drives the headline number).

**Option A is structurally inadequate:** it patches ONE reader. Even after Option A ships, the monthly compliance packet PDF, network-coverage scoring (`unmanaged_device_count`), partner fleet view, admin "all sites" page, and the Prometheus gauge will all still over-count by ~63% on this site. That's a Counsel Rule 1 + Rule 4 violation (non-canonical metric + orphan-coverage-via-partial-dedup).

**Option B is correct AND ALSO insufficient on its own** — it must ship with a CI gate that pins every reader to `canonical_devices` and forbids new readers of raw `discovered_devices` outside an allowlist (the per-appliance audit-trail callsites). Without that gate, the same drift class re-opens within 3 sessions. Migration number **316 is available** (305→307→308→309→310→312→313→314→315 shipped; 311 reserved per RESERVED_MIGRATIONS.md task #59).

---

## §1 — Empirical verification (Steve)

### Probe 1: row-count claim
```
site_id=north-valley-branch-2
total_rows=36  unique_pairs=22  unique_ips=22  null_macs=21
```
**Design said 22 unique pairs / 14 duplicates.** Confirmed (36 − 22 = 14).

### Probe 2: per-IP duplication distribution
```
192.168.88.50  cnt=3 appliances=3 macs=0
192.168.88.239 cnt=3 appliances=3 macs=0
192.168.88.240 cnt=3 appliances=3 macs=0
192.168.88.243 cnt=3 appliances=3 macs=0
192.168.88.244 cnt=3 appliances=3 macs=0
192.168.88.250 cnt=3 appliances=3 macs=0
192.168.88.251 cnt=3 appliances=3 macs=0
192.168.88.229 cnt=1 appliances=1 macs=1
192.168.88.232 cnt=1 appliances=1 macs=1
... (15 more IPs, all cnt=1 with macs=1)
```
**Sharper than the design states.** 7 IPs are duplicated 3× and have **zero populated mac_address values** in any of their rows. 15 IPs are NOT duplicated and ALL have macs. So the duplicate set is exactly the "ARP-only, no L2 mac yet" cold-scan rows where each appliance independently observed the IP without yet resolving a MAC. The bug is not "multi-appliance sees same device" — it's "multi-appliance sees same IP with no mac resolution, so the natural-key collapse never fires."

Note: discovered_devices has an existing UNIQUE INDEX `discovered_devices_unique_mac_site_idx ON (lower(mac_address), site_id)` — which **does not constrain NULL macs** (Postgres treats NULL as distinct). That's exactly why the dedupe works for resolved-mac rows and fails for cold-scan rows.

### Probe 3: query plan for Option A
```
Unique
  └─> Sort (Sort Key: ip_address, COALESCE(mac_address, ''), last_seen_at DESC)
        └─> Bitmap Heap Scan on discovered_devices
              └─> Bitmap Index Scan on idx_discovered_devices_site_id
```
No existing index supports the DISTINCT ON ordering. Every page-load sorts the full per-site set. On 36 rows that's free; on 4000+ rows at enterprise scale it's a per-query sort. Not catastrophic — but performance debt the design warned about is real.

### Probe 4: `get_site_devices()` exact source (device_sync.py:761–910)
- Returns FLAT `SELECT d.*` joined against workstations + go_agents + site_credentials — NO dedup
- `ORDER BY d.last_seen_at DESC LIMIT $N OFFSET $M` — pagination over duplicated rows means users page through the same physical device repeatedly
- 4 admin reads per call inside `admin_transaction` (already-correct shape)

---

## §2 — Sibling-surface enumeration (Question 8a — the load-bearing finding)

Every reader of `discovered_devices` graded by counting-vs-listing and customer-facing-vs-operator:

| # | File:line | Surface | Counts? | Customer-facing? | Affected by bug? | Option A fixes? |
|---|-----------|---------|---------|---------|---------|---------|
| 1 | device_sync.py:784 (`get_site_devices`) | `/api/devices/sites/{id}` admin device list | LIST | yes | YES | YES (the design target) |
| 2 | device_sync.py:955 (`get_site_device_counts`) | drives `/api/devices/sites/{id}/summary` total | COUNT | yes | YES | NO — separate function |
| 3 | device_sync.py:1192 / 1253 / 1267 / 1300 / 1351 | `_compute_unmanaged_*`, hostname/cred enrichment | aggregations | mixed | partial | NO |
| 4 | device_sync.py:708 (`unregistered_count`) | notification subject — "{N} devices need creds" | COUNT | YES (email + portal notif) | YES | NO |
| 5 | compliance_packet.py:1167 | **Monthly compliance packet PDF** → `total_devices` field in customer-issued evidence bundle | COUNT | **YES — customer artifact, OCR concern** | YES | NO |
| 6 | partners.py:1888 | Partner site-detail device list | LIST | yes (partner UI) | YES | NO |
| 7 | partners.py:2583 / 2587 / 2595 / 2602 | Partner fleet-wide device table + COUNT | LIST + COUNT | yes (partner UI) | YES | NO |
| 8 | portal.py:1251 | Client portal `device_count` ("4 workstations") | COUNT | YES — homepage card | YES | NO |
| 9 | portal.py:2137 | Client portal device list | LIST | yes | YES | NO |
| 10 | client_portal.py:1289 | Client portal site-detail enrichment map | LIST | yes | YES | NO (different shape — keyed by hostname) |
| 11 | client_portal.py:4732 | Client portal "unregistered devices to register" | LIST | yes | YES | NO |
| 12 | client_portal.py:4805 | Client portal device-register lookup | LOOKUP by id | no | NO (id PK lookup) | n/a |
| 13 | routes.py:5308 / 5313 / 5322 / 5331 | Admin "all sites devices" + COUNT + summary | LIST + COUNT | admin only | YES | NO |
| 14 | routes.py:5762 | **`network_coverage_pct` + `unmanaged_device_count` site-detail breakdown** | COUNT | **YES — F1-adjacent surface** | YES | NO |
| 15 | routes.py:5857 | site-detail devices-at-risk | LIST | yes | YES | NO |
| 16 | routes.py:6396 | unspecified — needs separate inspection | TBD | TBD | TBD | NO |
| 17 | routes.py:8592 | admin site-deep-detail device list | LIST | admin | YES | NO |
| 18 | prometheus_metrics.py:611 | `osiriscare_discovered_devices_total` gauge — operator-only | COUNT | NO (operator gauge) | YES | NO — but operator-only so Rule 1 doesn't fire |
| 19 | assertions.py:296 | substrate invariant `appliance_seen_recently` uses MAX(last_seen) — already dedup-aware | aggregation | no | NO (already groups) | n/a — already correct |
| 20 | health_monitor.py:786 | UPDATE owner_appliance_id ownership transfer | mutation | no | partial — operates on raw rows | n/a (write path, not display) |
| 21 | sites.py:5090 / 5100 / 5116 | SELECT DISTINCT ip_address — already dedup-aware | aggregation | n/a | n/a | n/a |
| 22 | sites.py:1897 / 5644 / 7271 | site-detail enrichment shapes | mixed | yes | likely YES | NO |
| 23 | appliance_trace.py:97 / 109 | per-appliance trace — needs RAW data | LIST | operator | NO (intentional per-appliance) | n/a — must NOT be deduped |
| 24 | background_tasks.py:1048 | unregistered-device alerter | COUNT | YES — alert email | YES | NO |
| 25 | device_sync.py:1138 / 1160 (`list_site_devices` + `summary` endpoints) | API responses | both | yes | YES | partial |
| 26 | device_sync.py:569 (DELETE/dedup) | stale-row cleanup write path | mutation | no | n/a | n/a |

**Count of customer-facing surfaces affected by the bug:** ≥9 (rows 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 17, 22, 24).

**Customer-facing PDF emission check:** `compliance_packet.py:1167` emits `total_devices` into the monthly compliance packet markdown template (line 1427: `| **Total** | **{{ device_inventory.total_devices }}** |`). The PDF IS over-counting today for any multi-appliance site. north-valley-branch-2 customers would have received packets showing **36 devices** when the truth is **22**.

**Customer-facing PDFs that DO NOT emit device count:** `client_attestation_letter.py` (F1), `partner_portfolio_attestation.py`, `auditor_kit_zip_primitives.py` — clean. The Counsel-Rule-1 exposure is bounded to compliance_packet.py.

---

## §3 — Per-lens verdicts

### Lens 1: Engineering (Steve) — **Option A INSUFFICIENT, Option B REQUIRED**

The design's own §2 cons list undersells the issue. Option A patches one function out of ~15 reader paths. Of those 15 paths, 9 are customer-facing and 6 are duplicates of the same anti-pattern in different modules (each module re-implements its own device query rather than delegating to a shared helper). Without structural enforcement, every new feature that touches devices adds another duplicating reader.

The query-plan probe shows DISTINCT ON has no index support. At 200 devices × 20 appliances scale, that's a 4000-row sort per page-load on a page that's hit by every dashboard load.

Steve verdict: **BLOCK Option A on its own. APPROVE Option B WITH a CI gate (P0 below).**

### Lens 2: Database (Maya) — **Option B viable; specific schema corrections required**

**Mig 316 availability:** verified. Last applied: 315. 311 is reserved per RESERVED_MIGRATIONS.md (task #59). Next free: 316. ✅

**Natural key correction (P0):** the design proposes `UNIQUE (site_id, ip_address, COALESCE(mac_address, ''))`. This is **invalid SQL** — Postgres unique indexes can include expressions but a UNIQUE table constraint cannot. The correct form is:
```sql
CREATE UNIQUE INDEX canonical_devices_site_ip_mac_idx
  ON canonical_devices (site_id, ip_address, COALESCE(mac_address, ''));
```
or, cleaner, store a generated dedup key:
```sql
mac_dedup_key TEXT GENERATED ALWAYS AS (COALESCE(mac_address, '')) STORED,
UNIQUE (site_id, ip_address, mac_dedup_key)
```

**Backfill semantics:** when 3 rows exist for the same (site, ip, NULL mac), which wins? Design §8 question (c) defers. **Maya recommendation:** `last_seen_at DESC` as tiebreaker, with `observed_by_appliances UUID[]` aggregating the set of appliances. The 60s reconciliation loop must implement this exact aggregation, not just pick-one.

**Reconciliation loop CPU/IO:** at 20 appliances × 200 devices = 4000 raw rows / 60s. A single `INSERT ... ON CONFLICT (site_id, ip_address, mac_dedup_key) DO UPDATE SET last_seen_at = GREATEST(...), observed_by_appliances = array_dedup(...)` per changed row. With a `WHERE sync_updated_at > last_run` filter on the source side, the steady-state cost is bounded by the actual change rate (typically <50 rows/min/site at this scale). Trivial.

**Performance gain post-Option-B:** `canonical_devices` is read-mostly and naturally indexed on `(site_id, ip_address, mac_dedup_key)`. Page-loads become an index scan with no Sort, no Bitmap Heap — order-of-magnitude faster than Option A's DISTINCT ON.

**`(ip, COALESCE(mac, ''))` semantic check:** given the empirical NULL-mac correlation in Probe 2, this collapse is correct. Multi-NIC single physical machine with 2 IPs = 2 canonical rows = legitimate (those are 2 endpoints). Multi-appliance same-IP no-mac = 1 canonical row = correct.

**Hidden footgun (P1):** the existing `discovered_devices_unique_mac_site_idx` on `(lower(mac_address), site_id)` means a mac CAN have only one row per site. So mac-resolved rows are already deduplicated by INSERT-side constraint. The duplication only exists for NULL-mac rows. **This means Option B's value isn't `(ip, mac)` dedup in general — it's specifically dedup for `(ip, NULL-mac)` rows.** A cheaper alternative not in the design: extend the existing unique index to also enforce `(site_id, ip_address) WHERE mac_address IS NULL`. That's a write-side dedup at the discovered_devices layer itself, no new table:

```sql
CREATE UNIQUE INDEX discovered_devices_unique_ip_nullmac_idx
  ON discovered_devices (site_id, ip_address)
  WHERE mac_address IS NULL;
```

Combined with the existing mac-uniqueness, this would structurally prevent the duplication at insert time. **Option B-minus** (single partial-unique-index migration) might be the right answer instead of a full new table. Maya FLAGS this as the missing Option D for user-gate decision.

Maya verdict: **APPROVE Option B with corrections. STRONGLY URGE consideration of Option D (partial unique index — write-side dedup at source table) before committing to a new canonical table — it's 1 migration with no reconciliation loop and may be enterprise-clean enough.**

### Lens 3: Security (Carol) — **Counsel Rule 4 risk on Option A; Option B/D both safe**

**Orphan-coverage-by-dedup class:** if Option A collapses `(192.168.88.50, NULL)` rows from 3 appliances to one, the customer sees "device present" with no signal that only 1 of 3 appliances is actually observing it. If appliances 2 and 3 went offline, the dashboard would still show 1 row for 192.168.88.50 as "last_seen_at=recent" — but from appliance 1 only. That's Counsel Rule 4 (no orphan coverage) directly: dedup-by-collapse hides the per-appliance coverage state.

Option B preserves this in `observed_by_appliances UUID[]`. The page can show "3/3 appliances see this device" as a coverage signal. Option D loses this information too (write-side dedup means only 1 row exists, losing per-appliance attribution) — that's its main downside vs Option B.

**Multi-appliance per-device observation is itself audit-supportive evidence.** A site running 3 appliances and only 1 seeing a critical workstation is a posture-degradation signal that should be operator-visible. Option B exposes it cleanly via `array_length(observed_by_appliances) < expected_appliances`.

Carol verdict: **Option B preferred over Option D for the per-appliance observation preservation. Option A creates a Counsel Rule 4 violation.**

### Lens 4: Coach (pattern-match) — **NEW class, NOT a repeat of "3 competing sources"**

The CLAUDE.md "3 competing sources" rule applies to `device_sync.py`'s CASE expression for sync-source-priority (netscan vs replay vs home net). That rule resolved the WHICH-SOURCE-WINS question for a single device. The current bug is a different class: SAME source (nmap ARP from each appliance) producing N rows. The existing CASE expression doesn't help — the rows are not in conflict, they're concurrent observations.

However, the design's "Option C is hotfix-disguised-as-strategy" framing IS the well-trodden hybrid antipattern from Coach memory (Counsel Rule directive "no hotfix"). Author's lean toward Option B is correctly grounded in the 2026-05-13 user directive.

Coach concern: **the design under-enumerates sibling surfaces.** §8 question (a) ASKS for the enumeration but does not perform it. A design that defers its own load-bearing enumeration to the Gate A fork is technically a Gate A blocker — the fork should not be the one discovering 9 customer-facing surfaces are affected. Author should have done that grep themselves. Flag as a Gate A process P1 — design doc must include the enumeration table next iteration.

Coach verdict: **APPROVE Option B. P1 design-quality finding: §8(a) enumeration must be in the design, not delegated to Gate A.**

### Lens 5: Auditor (OCR) — **Counsel Rule 1 violation TODAY; canonical_metrics entry mandatory**

The monthly compliance packet PDF (`compliance_packet.py:1167`) emits `total_devices` from a raw `COUNT(*) FROM discovered_devices WHERE site_id=...`. That's a customer-facing artifact (the monthly evidence bundle is the second-most-load-bearing customer document after the F1 attestation letter). It's been emitting wrong totals for any multi-appliance site for the lifetime of multi-appliance support.

**Per Counsel Rule 1, `device_count_per_site` is a customer-facing metric and MUST appear in `CANONICAL_METRICS`** with a designated canonical helper. Today's allowlist doesn't include it. Either option ships:

```python
"device_count_per_site": {
    "canonical_helper": "device_canonical.get_canonical_device_count",  # Option B
    "permitted_inline_in_module": "device_canonical",
    "allowlist": [
        # Operator-only callsites here (e.g. prometheus_metrics)
        {"signature": "prometheus_metrics.*", "classification": "operator_only"},
        # Per-appliance audit-trail callsites that MUST read raw
        {"signature": "appliance_trace.*", "classification": "operator_only"},
    ],
    "display_null_passthrough_required": False,
},
```

OCR Auditor concern about Option A: it doesn't fix the compliance packet PDF, so customer-issued artifacts continue to show wrong totals indefinitely. That's a chain-of-custody concern — past PDFs already shipped with wrong counts. Forward-fix is acceptable (no retroactive PDF re-issue) but the bug class must be closed forward.

OCR verdict: **BLOCK Option A on Counsel Rule 1 grounds. APPROVE Option B + canonical_metrics entry as joint scope.**

### Lens 6: PM — **User directive overrides PM hotfix instinct**

PM instinct on a customer-visible bug: ship the fastest correct fix. Option A delivers visible improvement in 0.5 days on the page the user reported. Option B is 2-3 days and customer continues to see duplicates on that page meanwhile.

But user directive 2026-05-13 was explicit and gold-authority: "we keep having to go back to fix it - we should always implement long lasting enterprise solutions." A PM ignoring that to ship Option A would be optimizing for the wrong metric.

**Compromise NOT recommended (Option C):** as the author flagged, hybrid is the hotfix pattern in disguise. The user named this antipattern.

**Realistic Option B timeline:** with mig 316 + reconciliation loop + CI gate + canonical_metrics entry + per-reader migration of ~9 callsites, 2-3 days is achievable if the work is sequenced as: (1) ship Option B Phase 1 = canonical table + reconciliation loop + the `get_site_devices()` reader migration (day 1, visible fix), then (2) migrate the other 8 callsites + add CI gate (days 2-3). User sees the page-fix in day 1.

PM verdict: **APPROVE Option B with phased delivery — day-1 visible fix preserves the user-visible-progress signal without sacrificing enterprise structure.**

### Lens 7: Attorney (Counsel) — **No F1 / portfolio-attestation impact; compliance packet impact bounded**

Customer-facing artifact grep:
- `client_attestation_letter.py` (F1 monthly letter): **does not emit device count.** ✅
- `partner_portfolio_attestation.py`: **does not emit device count.** ✅
- `auditor_kit_zip_primitives.py`: **does not emit raw discovered_devices count.** ✅
- `compliance_packet.py`: **emits `total_devices` in monthly evidence packet markdown template.** ❌ — bounded exposure.

Counsel concern is bounded to the monthly compliance packet. Past packets shipped to customers under-stated the unique device count by ~63% on multi-appliance sites. **Counsel recommendation:** no retroactive re-issue (the metric is informational, not the load-bearing compliance signal), but a forward-only fix paired with a `kit_version` bump on the packet to flag the methodology change. Document the change in the packet's PHI disclaimer block ("Methodology v2 — devices counted by unique (ip, mac) tuple per site").

Counsel verdict: **APPROVE Option B + canonical_metrics + packet methodology-bump note. Exposure is bounded; no §164.528 disclosure-accounting impact (device count is not PHI).**

---

## §4 — Migration number availability

```
305 delegate_signing_key_privileged.sql
306 (RESERVED — backfill ghost L1, task #117)
307 ots_proofs_status_check.sql
308 l2_escalations_missed.sql
309 l2_decisions_site_reason_idx.sql
310 close_l2esc_in_immutable_list.sql
311 (RESERVED — Vault P0 vault_signing_key_versions, task #43)
312 baa_signatures_acknowledgment_only_flag.sql
313 d1_heartbeat_verification.sql
314 canonical_metric_samples.sql
315 substrate_mttr_soak_v2.sql
```

**Next free: 316.** ✅ Available for canonical_devices table. Author should add to RESERVED_MIGRATIONS.md upon approval.

---

## §5 — Open questions for user-gate (escalated from design §8)

The fork answers what it can; these remain user-decisions:

1. **(c) when 3 appliances report DIFFERENT device_type for same (ip, mac), which wins?** Author defers. Maya recommends: `last_seen_at DESC` tiebreaker by default, with a `confidence_score` field that incorporates how many independent observations agree. **User decision needed:** is the simpler "most-recent wins" acceptable, or do we want the more complex "agreement-weighted" approach?

2. **(d) `confidence_score` field?** Maya + Carol both see value (Carol for coverage-degradation signal, Maya for analytics). **User decision needed:** include in v1, or defer to Phase 2?

3. **(NEW — Maya raised) Option D — partial unique index — should be considered.** Single migration:
   ```sql
   CREATE UNIQUE INDEX discovered_devices_unique_ip_nullmac_idx
     ON discovered_devices (site_id, ip_address) WHERE mac_address IS NULL;
   ```
   Fixes the empirical bug at write-side with NO new table, NO reconciliation loop, NO new readers to migrate. Cost: loses per-appliance observation tracking (Carol veto), loses ability to add `confidence_score` later, doesn't solve the "every reader counts wrong" class structurally (each reader still must handle multi-appliance-attribution if we want it). **User decision needed:** is per-appliance observation tracking worth the +2 days of Option B work?

4. **Compliance packet methodology-bump:** counsel recommends documenting the change in the monthly packet PHI disclaimer block. **User decision needed:** approve adding "Methodology v2" note + `kit_version` bump on packet?

5. **Phase-1 vs full-rollout timing:** PM recommends day-1 ship of canonical table + 1 reader migration (the user-reported page), days 2-3 migrate the other ~8 readers + CI gate. **User decision needed:** acceptable, or full rollout in one Gate B?

---

## §6 — Mandatory fixes before Gate B (P0)

If Option B is approved:

1. **P0-1 — Schema correction.** Replace `UNIQUE (site_id, ip_address, COALESCE(mac_address, ''))` (invalid SQL as a table constraint) with `CREATE UNIQUE INDEX ... (site_id, ip_address, COALESCE(mac_address, ''))` or a generated column.
2. **P0-2 — `device_count_per_site` canonical_metrics entry.** Must land in same commit as the canonical_devices table. Without it, Counsel Rule 1 stays violated and the CI gate doesn't enforce reader migration.
3. **P0-3 — CI gate `tests/test_no_raw_discovered_devices_count.py`.** AST or grep gate over backend forbidding `COUNT(*) FROM discovered_devices` and `SELECT ... FROM discovered_devices ... ORDER BY ... LIMIT` outside allowlist (appliance_trace, assertions.py freshness check, write paths). Pinned to the canonical_metrics entry's allowlist.
4. **P0-4 — Compliance packet PDF migration.** `compliance_packet.py:1167` is a customer-facing artifact — it MUST migrate to canonical_devices in the same commit as the new table. Cannot defer to Phase 2.
5. **P0-5 — `_get_device_inventory` in compliance_packet.py also reads `compliance_status` from discovered_devices — verify this matches the canonical_devices schema or update to read from compliance_bundles** (consistent with the BUG 3 fix already in `get_site_device_counts`).
6. **P0-6 — Substrate invariant `canonical_devices_freshness`** (design §8 question g). If reconciliation loop stalls, customers silently see stale counts. Sev2 invariant pinned to the 60s tick + 5min staleness threshold.

## §7 — Recommended P1 (should-have, may carry as TaskCreate followups)

1. **P1-1 — Per-appliance source-of-record decision.** Document in canonical_devices schema comment: which raw `discovered_devices` row's fields propagate (last_seen DESC by default; WinRM-probed > ARP-only when available).
2. **P1-2 — `observed_by_appliances UUID[]` exposed in API response.** So the UI can show "3/3 appliances see this device" coverage signal (Carol's request).
3. **P1-3 — Index strategy on canonical_devices.** B-tree on `(site_id, last_seen_at DESC)` + the dedup unique index. Plan reviewed in Gate B with EXPLAIN ANALYZE on actual prod data.
4. **P1-4 — Backfill safety.** Mig 316 backfill of canonical_devices must be idempotent + chunked for the substrate-MTTR-soak post-quarantine path. Verify mig 304 quarantine state doesn't break the join.
5. **P1-5 — Update CLAUDE.md** with the new rule once shipped: "`canonical_devices` is the device-count canonical source. Read raw `discovered_devices` only for per-appliance audit-trail callsites (appliance_trace, freshness invariants)."

## §8 — Verdict

**APPROVE-WITH-FIXES — Option B (canonical_devices table) + canonical_metrics entry + CI gate + compliance_packet.py migration in same commit.**

**Rationale:**
- User directive 2026-05-13 is gold authority — Option A is a hotfix despite the design author's lean
- Empirical sibling-surface count (9 customer-facing) makes Option A structurally inadequate
- Counsel Rule 1 mandates `device_count_per_site` canonical registry entry — that's a same-commit dependency, not optional
- Counsel Rule 4 mandates per-appliance observation preservation — Option D (partial unique index) loses this
- Migration 316 is available
- Phased delivery (PM recommendation) preserves day-1 visible progress without sacrificing structure

**BLOCK conditions** (would flip to BLOCK if any of these unresolved):
- If P0-1 schema-correction not made before mig 316 written
- If P0-2 canonical_metrics entry deferred to a later sprint
- If P0-4 compliance_packet.py migration deferred (Counsel Rule 1 continues to be violated as long as that PDF emits the wrong number)

**Author next steps:**
1. Update design doc to reflect §2 sibling-surface enumeration (the table above is the missing §8(a) answer)
2. Decide questions §5(c)–(e) with user
3. Write mig 316 with the §6 P0 corrections
4. Stage commits: (1) mig 316 + canonical_metrics + Phase-1 reader migration + CI gate; (2) remaining 8 reader migrations + compliance_packet.py PDF migration; (3) substrate invariant
5. Each commit needs its own Gate B fork — design pattern not implementation pattern

---

*Verdict produced by Gate A 7-lens fork. Per the TWO-GATE rule, this design must also receive Gate B fork review on the as-implemented artifact before any commit body says "shipped."*
