# Device Inventory De-duplication — Architectural Design v3 (Task #73, ENTERPRISE STRUCTURAL)

<!-- mig 319 SHIPPED 2026-05-13 — see migrations/319_canonical_devices.sql -->

> **v3 changes (Implementation Gate A APPROVE-WITH-FIXES → 3 P0s + 2 P1s applied 2026-05-13):**
> - **P0-A (mig number):** 316 was ALREADY claimed by Task #38; 317+318 by Task #58. Next free verified as **319**. Updated throughout. RESERVED_MIGRATIONS row added. Marker `<!-- mig 319 SHIPPED 2026-05-13 — see migrations/319_canonical_devices.sql -->` added above.
> - **P0-B (CI ratchet ordering):** baseline computed AFTER compliance_packet migration lands. Customer-facing reader count = **9 - 1 (compliance_packet migrated) = 8 allowlisted**. Phase 2 drives to 0. Non-customer-facing reads (appliance_trace, freshness invariants, write paths, DISTINCT-aggregations) live in static EXEMPT list classified `operator_only` or `write_path`.
> - **P0-C (RLS parity):** mig 319 MUST replicate `tenant_org_isolation` + `partner_isolation` policies from `discovered_devices` onto `canonical_devices`. Without it, Phase 2 readers under `org_connection` silent-zero.
> - **P1 (majority-vote tiebreaker):** SQL must `ORDER BY vote_count DESC, device_type ASC NULLS LAST` for deterministic UTF-8 codepoint tiebreaker.
> - **P1 (methodology version):** `compliance_packet.py` has no `_KIT_VERSION` symbol today. Introduce `_PACKET_METHODOLOGY_VERSION = "2.0"` (not kit_version bump — that's for auditor_kit which doesn't emit device count).
>
> **v2 changes (Gate A APPROVE-WITH-FIXES → user decisions + 6 mandatory P0s applied 2026-05-13):**
>
> **User decisions (AskUserQuestion 2026-05-13):**
> - **Architecture:** Option B — canonical_devices table (full enterprise solution)
> - **Tiebreaker when appliances disagree on device_type:** **most-confident (majority vote across appliances)**. Multi-way ties broken deterministically by alphabetical device_type sort (UTF-8 codepoint order).
> - **Past PDFs:** kit_version bump + methodology note in next compliance packet. Past Ed25519-signed packets stay immutable; disclosure note added to all future packets noting methodology updated 2026-05-13.
>
> **Gate A P0s applied:**
> - **P0-1 (Maya schema syntax):** `UNIQUE (..., COALESCE(...))` is invalid as a table constraint. Fix: use `CREATE UNIQUE INDEX ... ON (site_id, ip_address, COALESCE(mac_address, ''))` as a separate statement, OR add a generated column.
> - **P0-2 (Counsel Rule 1):** canonical_metrics.py must gain `device_count_per_site` entry — canonical helper = `get_canonical_device_count(site_id)` reading from canonical_devices. Migration callsites listed in allowlist with `migrate` classification.
> - **P0-3 (CI gate):** new `tests/test_no_raw_discovered_devices_count.py` ratchets every `FROM discovered_devices` callsite that doesn't dedup; drives baseline to 0 across Phase 2 commits.
> - **P0-4 (compliance_packet.py:1167):** MUST migrate same-commit as Phase 1 — emits `total_devices` into customer-issued monthly PDF; customers have been receiving ~63% over-counted totals.
> - **P0-5 (compliance_status verification):** read of `compliance_status` column in `_get_device_inventory` must align with BUG-3 resolution (task #23 in_progress).
> - **P0-6 (substrate invariant):** `canonical_devices_freshness` sev2 — fires if no canonical row updated in 60min for any active site. Pairs with existing `discovered_devices_freshness`.
>
> **Open questions answered or deferred per user-gate:**
> - (c) tiebreaker: ANSWERED — majority vote
> - (d) confidence_score: DEFERRED to v2 of canonical_devices (Phase 3); Phase 1 ships without it
> - (e) compliance packet methodology bump: APPROVED — going-forward note only
> - (f) phased rollout: implicit YES — Phase 1 ships canonical + critical readers; Phase 2 migrates remaining 8

> **Motivation:** user-reported 2026-05-13. Device Inventory at `/sites/north-valley-branch-2/devices` shows the same physical machines 3× each (IP 192.168.88.50 appears 6×, 192.168.88.250 appears 7×). Empirical DB state: **36 total rows / 22 unique (ip, mac) pairs / 14 duplicates**, all from 3 appliances at the site each independently ARP-scanning the same physical /24 network. User has explicitly flagged this is NOT a hotfix class: "we should always implement long lasting enterprise solutions."

> **Round-table needed** on architectural choice — this design proposes options + asks Gate A fork to recommend.

---

## §1 — What's happening (root cause)

`discovered_devices` schema is **appliance-scoped**:
- Primary key: `id BIGSERIAL`
- Natural uniqueness: `(appliance_id, local_device_id)` (each appliance numbers devices locally)
- The same physical IP `192.168.88.50` gets a separate row from each appliance that sees it
- North Valley Branch 2 has 3 appliances → 3 rows per IP

`get_site_devices()` at `device_sync.py:1114` does a FLAT `SELECT d.* FROM discovered_devices d WHERE site_id=$1` with no dedup. Each ARP scan duplicates every IP.

Two distinct deduplication architectures are possible:

---

## §2 — Option A: read-time `DISTINCT ON` (no schema change)

**Mechanism:** rewrite `get_site_devices()` to use:

```sql
SELECT DISTINCT ON (ip_address, COALESCE(mac_address, ''))
       d.*, a.host_id as appliance_hostname, ...
  FROM discovered_devices d
  ...
 WHERE site_id = $1
 ORDER BY ip_address, COALESCE(mac_address, ''),
          last_seen_at DESC,    -- prefer most-recent observation
          last_scan_at DESC NULLS LAST  -- tiebreaker
```

**Pros:**
- Single-file change (~10 lines)
- No schema migration
- No backfill
- Reversible (revert the query)

**Cons:**
- Stats elsewhere may still count rows not devices (`/api/devices/sites/{id}/summary`, prometheus metrics, audit-kit exports, etc.) — drift class
- Future analytics that read `discovered_devices` directly will still see duplicates
- `(ip, mac)` is the dedup natural key, but mac is often NULL on cold ARP scans → tied to `COALESCE(mac, '')` which creates a single "no-mac" bucket per IP
- Multiple appliances genuinely seeing the SAME physical device through SEPARATE interfaces (rare but possible at a multi-segmented site) would silently collapse to one row, losing the per-appliance observation source

**Effort:** ~0.5 day. Single file. Read-only. Reversible.

---

## §3 — Option B: write-side canonical_devices table + maintenance loop

**Mechanism:** mig 319 (next free after collision-ledger renumbering — verify) introduces:

```sql
CREATE TABLE canonical_devices (
    canonical_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id         TEXT NOT NULL REFERENCES sites(site_id),
    ip_address      TEXT NOT NULL,
    mac_address     TEXT NULL,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    observed_by_appliances UUID[] NOT NULL DEFAULT '{}',
    -- Other canonical fields synthesized from latest discovered_devices row...
    UNIQUE (site_id, ip_address, COALESCE(mac_address, ''))
);

-- background reconciliation loop (every 60s) consolidates discovered_devices
-- rows into canonical_devices using (site_id, ip, mac) natural key
```

`get_site_devices()` reads from `canonical_devices` instead of `discovered_devices`. Pages that need per-appliance observation history (e.g., audit-kit) read the original table directly.

**Pros:**
- Single source of truth for "what physical devices exist at this site"
- Every counting/summary surface uses the same canonical view
- Per-appliance source-of-record preserved in `discovered_devices` (no information loss)
- Future analytics + audit exports point at canonical_devices, can't accidentally double-count

**Cons:**
- Schema migration (mig 319 against shipped 312-315)
- New background reconciliation loop (60s tick)
- New CI gate to ensure all device-list endpoints read canonical, not raw
- Backfill required (~22 canonical rows for north-valley-branch-2; small)
- More moving parts to maintain long-term

**Effort:** ~2-3 days incl. Gate A/B + backfill + CI gate.

---

## §4 — Option C: hybrid — Option A short-term, Option B medium-term

Ship Option A first (1-day) to stop the customer-visible duplicates immediately. Open Task #74 for Option B medium-term canonical table. Treat A as "stop-the-bleed", B as "enterprise-final".

**Pros:** customer sees the fix today; architecture lands by end-of-week.
**Cons:** "ship-and-iterate" pattern — user explicitly flagged 2026-05-13 they don't want hotfixes; this is a Class-A vs Class-B debate.

---

## §5 — Recommendation (author's lean — fork decides)

**Lean: Option B (canonical_devices table)** per user's explicit directive "long lasting enterprise solutions." The hybrid (Option C) is a hotfix-disguised-as-strategy and the user has flagged against that pattern.

But Option A has merit if the dedup-at-read-time is sufficient and other surfaces (audit-kit, summary endpoints) ALREADY handle the duplicate-count correctly (need verification — Gate A probe).

---

## §6 — Multi-device-enterprise lens

At enterprise scale (5+ appliances per multi-segment site, federated dental groups with 10-20 sites), the canonical_devices approach scales linearly with PHYSICAL devices (the actual count), not with appliance×device cross-product. Option A's DISTINCT ON is O(rows-with-duplicates) → still O(appliances × devices) at scan time. For 20 appliances × 200 devices = 4000 rows scanned per page-load even though only 200 are returned. Performance debt that compounds.

Canonical-table approach is O(canonical-rows) at read time. The 60s reconciliation loop is O(new-or-updated-discovered_devices-rows) — small.

---

## §7 — Counsel-rule check

- **Rule 1 (canonical-source registry):** the device count IS a customer-facing metric ("37 devices on protected network"). It needs to declare a canonical source. Today: `len(discovered_devices)` per site. With Option B: `len(canonical_devices)` per site. Add to `CANONICAL_METRICS` in `canonical_metrics.py` either way.
- **Rule 4 (no orphan coverage):** Option A risks "I changed the page but the summary endpoint still shows wrong count" class. Option B closes the class structurally.
- **Rule 5 (no stale doc as authority):** today's "37 devices" claim in the UI is stale-as-built — it's not a doc issue, but the read-time dedup must not regress the moment we stop watching.

---

## §8 — Open questions for Gate A fork

1. **(a)** Are there OTHER surfaces beyond `get_site_devices()` that count `discovered_devices` rows and would show wrong totals? grep `FROM discovered_devices` across the backend and enumerate.
2. **(b)** Does Option A's `(ip, mac)` dedup handle the multi-NIC case (one physical machine with 2 NICs, hence 2 IPs, hence appears as 2 devices)? Acceptable — those are legitimately 2 IP endpoints.
3. **(c)** Per `appliance_id` source-of-record: when 3 appliances see the same IP and report DIFFERENT device_type values (e.g., one says "workstation" the other says "unknown"), which wins? Most-recent `last_seen_at`? Best-source (e.g., WinRM probe wins over ARP)? This decision affects BOTH options.
4. **(d)** Should canonical_devices include a `confidence_score` derived from how many appliances independently observed the same physical device? Surfacing observation-redundancy could be useful for compliance audits.
5. **(e)** What's the canonical "single device" definition — `(ip, mac)` or just `(ip)`? IPs can change via DHCP; macs are stable. But macs are often NULL on cold scans. Trade-off.
6. **(f)** Effort estimate alignment — is Option B realistically 2-3 days or am I underestimating the test/CI/gate overhead?
7. **(g)** Backfill safety: when canonical_devices first populates, does the substrate-invariant `discovered_devices_freshness` need an analog? (`canonical_devices_freshness`?)

---

## §9 — Recommended scope of Gate A fork

Brief fork (~10 min compute) to:
- Verify the empirical numbers (36/22/14) match my probe
- grep for sibling counting surfaces (Question (a))
- Recommend A / B / C with explicit rationale
- Identify any Gate A blockers (e.g., a sibling table that needs migrating first)
- Note open questions that should escalate to user-decision

Implementation Gate A on the chosen option happens AFTER the fork's recommendation.
