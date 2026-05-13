# Gate A v2 — Device-Dedup IMPLEMENTATION Plan (Task #73, Phase 1)

**Date:** 2026-05-13
**Fork lens:** Class-B 7-lens (Steve / Maya / Carol / Coach / OCR / PM / Counsel)
**Design under review:** `audit/device-dedup-architectural-design-2026-05-13.md` (v2 — Option B selected by user 2026-05-13)
**Predecessor verdict:** `audit/coach-device-dedup-architectural-gate-a-2026-05-13.md` (v1 architectural Gate A — APPROVE-WITH-FIXES)
**Scope of THIS gate:** as-planned Phase 1 implementation (canonical_devices table + reconciliation + 1 reader migrated + CI gate + substrate invariant + canonical_metrics entry + kit_version bump).

**Verdict: APPROVE-WITH-FIXES — Phase 1 may proceed once 3 P0s + 2 P1s land in the commit body.**

---

## §0 — 250-word executive summary

User decisions correctly applied in v2: Option B (canonical_devices table), majority-vote tiebreaker with alphabetical-codepoint tiebreaker for multi-way ties, kit_version bump + going-forward methodology note (past Ed25519-signed PDFs immutable). Phase 1 scope is realistic for ~1 day execution.

**Three P0s block as-stated Phase 1:**

**P0-A (Maya/Steve) — migration number is wrong.** v2 design and the prior Gate A both name `mig 316`, but `RESERVED_MIGRATIONS.md` already has 316 claimed by Task #38 (load harness v2.1) and 317–318 by Task #58 (P-F9 v2). **Next genuinely free number is 319.** Phase 1 must claim 319, add the row to `RESERVED_MIGRATIONS.md` in the same commit, and update the design doc's "mig 316" references throughout.

**P0-B (Steve) — CI-gate ratchet ordering.** Enabling `test_no_raw_discovered_devices_count.py` with baseline=today's count BEFORE migrating `compliance_packet.py:1167` means the test sees the same number after migration (the migrated callsite uses canonical, not raw, so the raw-count drops by 1). The baseline must be computed AFTER the compliance_packet migration lands within the same commit, OR an explicit `# canonical-migration: device_count_per_site` marker exempts the migrating line.

**P0-C (Carol/Maya) — RLS policy parity.** `discovered_devices` has `tenant_org_isolation` via `rls_site_belongs_to_current_org`. Migration 319 MUST replicate that policy on `canonical_devices` in the same statement-batch (per Session 217 RT30 P0 — client-portal silent-zero class). Without it, the new endpoints under `org_connection` read zero rows.

**P1s:** majority-vote SQL needs deterministic tiebreaker (alphabetical works, but MUST sort by `(count DESC, device_type ASC NULLS LAST)`); RESERVED_MIGRATIONS.md row must include `<!-- mig-claim:319 task:#73 -->` marker in the design doc.

Phase 1 is otherwise correctly scoped, sequenced, and risk-bounded. Phase 2 (8 remaining readers) properly deferred to its own Gate A/B.

---

## §1 — Per-lens verdicts

### Lens 1: Engineering (Steve) — APPROVE-WITH-FIXES

**Implementation order is correct as drafted:** schema (mig 319) → backfill (idempotent INSERT-SELECT against discovered_devices today's 22 rows for nvb2 + per-site for all sites) → reconciliation loop registration → 1 reader migrated (`compliance_packet.py:1167`) → CI gate enabled at baseline-minus-1 → substrate invariant registered.

**P0-B — CI ratchet baseline.** Today's count of `FROM discovered_devices` outside test/script directories = **47 callsites** (raw grep). But many are legitimate raw reads:
- `assertions.py:296` (the freshness invariant — by design)
- `appliance_trace.py:97, 109` (per-appliance audit trail)
- `device_sync.py:*` (the reconciliation source itself + write paths)
- `sites.py:5090, 5100, 5116` (DISTINCT-already-deduping)

**Recommended baseline approach:** match the v1 Gate A enumeration shape. The 9 customer-facing readers identified are the ratchet target. Baseline = 9 - 1 (compliance_packet migrated in Phase 1) = **8 customer-facing offenders allowlisted**. Phase 2 drives to 0. Non-customer-facing reads (appliance_trace, freshness invariants, write paths, DISTINCT-aggregation) live in a static EXEMPT list classified `operator_only` or `write_path`.

**Majority-vote SQL shape (recommended for Phase 1 reconciliation loop):**

```sql
WITH per_appliance_observation AS (
    SELECT site_id, ip_address, COALESCE(mac_address, '') AS mac_key,
           device_type,
           appliance_id,
           last_seen_at
      FROM discovered_devices
     WHERE site_id = $1
       AND last_seen_at > NOW() - INTERVAL '24 hours'
),
type_votes AS (
    SELECT site_id, ip_address, mac_key, device_type,
           COUNT(DISTINCT appliance_id) AS vote_count
      FROM per_appliance_observation
     WHERE device_type IS NOT NULL
     GROUP BY site_id, ip_address, mac_key, device_type
),
ranked_types AS (
    SELECT site_id, ip_address, mac_key, device_type,
           ROW_NUMBER() OVER (
               PARTITION BY site_id, ip_address, mac_key
               ORDER BY vote_count DESC,
                        device_type ASC  -- alphabetical UTF-8 tiebreaker
           ) AS rn
      FROM type_votes
),
winner AS (
    SELECT site_id, ip_address, mac_key, device_type AS winning_device_type
      FROM ranked_types
     WHERE rn = 1
),
aggregated AS (
    SELECT pao.site_id, pao.ip_address, pao.mac_key,
           MAX(pao.last_seen_at) AS last_seen_at,
           MIN(pao.last_seen_at) AS first_seen_at,
           ARRAY_AGG(DISTINCT pao.appliance_id) AS observed_by_appliances,
           (SELECT winning_device_type FROM winner w
             WHERE w.site_id = pao.site_id
               AND w.ip_address = pao.ip_address
               AND w.mac_key = pao.mac_key) AS device_type
      FROM per_appliance_observation pao
     GROUP BY pao.site_id, pao.ip_address, pao.mac_key
)
INSERT INTO canonical_devices (site_id, ip_address, mac_address, device_type,
                               first_seen_at, last_seen_at, observed_by_appliances,
                               reconciled_at)
SELECT site_id, ip_address,
       NULLIF(mac_key, '') AS mac_address,
       device_type,
       first_seen_at, last_seen_at, observed_by_appliances,
       NOW()
  FROM aggregated
ON CONFLICT (site_id, ip_address, mac_dedup_key) DO UPDATE
   SET last_seen_at         = GREATEST(canonical_devices.last_seen_at, EXCLUDED.last_seen_at),
       observed_by_appliances = (
           SELECT ARRAY(SELECT DISTINCT unnest(canonical_devices.observed_by_appliances
                                              || EXCLUDED.observed_by_appliances))
       ),
       device_type          = EXCLUDED.device_type,
       reconciled_at        = NOW();
```

The `mac_dedup_key` is a GENERATED column from `COALESCE(mac_address, '')` (P0-1 schema correction from v1 Gate A — verified applied in v2 design §6).

**Reconciliation loop registration:** add to `background_tasks.py` alongside other 60s loops, supervised by the bg_heartbeat. EXPECTED_INTERVAL_S = 60. Idempotent — losing a tick is bounded by next tick. Scope: per-site or whole-fleet — recommend **per-site, iterated in the tick** to bound transaction size + log per-site reconcile-count.

**Steve verdict:** APPROVE with P0-A (mig number) + P0-B (ratchet baseline) closures. The majority-vote SQL above is a recommended starting shape; refinement at Gate B against real prod data is expected.

### Lens 2: Database (Maya) — APPROVE-WITH-FIXES

**P0-A (mig number).** `RESERVED_MIGRATIONS.md` confirms 311 BLOCKED (Vault), 316 reserved (load harness Task #38), 317–318 reserved (P-F9 Task #58). **319 is the next free number.** Task #38 owns 316 — claiming it for canonical_devices would collide. Update design doc + Gate A v1 verdict + this Gate A's commit-plan to use **319**.

**Schema correctness verified (P0-1 from v1 Gate A applied in v2):**

```sql
-- File: migrations/319_canonical_devices.sql
BEGIN;

CREATE TABLE canonical_devices (
    canonical_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id         TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    ip_address      TEXT NOT NULL,
    mac_address     TEXT NULL,
    mac_dedup_key   TEXT GENERATED ALWAYS AS (COALESCE(mac_address, '')) STORED,
    device_type     TEXT NULL,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    observed_by_appliances UUID[] NOT NULL DEFAULT '{}',
    reconciled_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX canonical_devices_site_ip_mac_idx
    ON canonical_devices (site_id, ip_address, mac_dedup_key);

CREATE INDEX canonical_devices_site_last_seen_idx
    ON canonical_devices (site_id, last_seen_at DESC);

CREATE INDEX canonical_devices_reconciled_idx
    ON canonical_devices (reconciled_at)
    WHERE reconciled_at < NOW() - INTERVAL '5 minutes';

-- P0-C (Carol) — RLS parity with discovered_devices.
ALTER TABLE canonical_devices ENABLE ROW LEVEL SECURITY;

-- Admin bypass (mirrors discovered_devices)
CREATE POLICY canonical_devices_admin_all
    ON canonical_devices
    USING (current_setting('app.is_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_admin', true) = 'true');

-- Tenant isolation by site_id (mirrors discovered_devices.tenant_org_isolation)
CREATE POLICY canonical_devices_tenant_org_isolation
    ON canonical_devices
    USING (rls_site_belongs_to_current_org(site_id));

-- Partner isolation by site_id (mirrors discovered_devices.partner_isolation)
CREATE POLICY canonical_devices_partner_isolation
    ON canonical_devices
    USING (rls_site_belongs_to_current_partner(site_id));

-- Comments
COMMENT ON TABLE canonical_devices IS
    'Canonical view of physical devices per site, deduplicated from discovered_devices. '
    'Multi-appliance same-(ip,mac) observations collapse to one row via reconciliation loop. '
    'Per Counsel Rule 1 (2026-05-13), this is the canonical source for device_count_per_site. '
    'See canonical_metrics.py and tests/test_no_raw_discovered_devices_count.py.';

COMMENT ON COLUMN canonical_devices.observed_by_appliances IS
    'Array of appliance_ids that have independently observed this (ip, mac) within the '
    'reconciliation window. Length < expected_appliances_count is a Counsel-Rule-4 '
    'coverage-degradation signal.';

COMMENT ON COLUMN canonical_devices.device_type IS
    'Majority-vote winner across observing appliances. Multi-way ties broken by '
    'alphabetical UTF-8 codepoint order (deterministic). See reconciliation loop SQL.';

-- Phase 1 backfill — idempotent INSERT-SELECT from existing discovered_devices
INSERT INTO canonical_devices (site_id, ip_address, mac_address, device_type,
                               first_seen_at, last_seen_at, observed_by_appliances,
                               reconciled_at)
SELECT
    dd.site_id,
    dd.ip_address,
    -- mac_address NULL if all observations had NULL; else first non-NULL
    (ARRAY_AGG(dd.mac_address) FILTER (WHERE dd.mac_address IS NOT NULL))[1],
    -- majority vote inline (Phase 1 simplification — single-statement backfill)
    (SELECT dt FROM (
        SELECT device_type AS dt, COUNT(DISTINCT appliance_id) AS vc
          FROM discovered_devices dd2
         WHERE dd2.site_id = dd.site_id
           AND dd2.ip_address = dd.ip_address
           AND COALESCE(dd2.mac_address, '') = COALESCE(dd.mac_address, '')
           AND dd2.device_type IS NOT NULL
         GROUP BY device_type
         ORDER BY vc DESC, device_type ASC
         LIMIT 1
    ) t),
    MIN(dd.first_seen_at) AS first_seen_at,
    MAX(dd.last_seen_at) AS last_seen_at,
    ARRAY_AGG(DISTINCT dd.appliance_id) AS observed_by_appliances,
    NOW() AS reconciled_at
FROM discovered_devices dd
GROUP BY dd.site_id, dd.ip_address, COALESCE(dd.mac_address, '')
ON CONFLICT (site_id, ip_address, mac_dedup_key) DO NOTHING;

COMMIT;
```

**Verify:** `COALESCE(mac_address, '')` is IMMUTABLE — Postgres allows it in both a `GENERATED ALWAYS AS ... STORED` column AND in a `CREATE UNIQUE INDEX` expression. Either approach works. Using the generated column is cleaner for ON CONFLICT clauses (named target column).

**Backfill scale:** at nvb2, 36 rows → 22 canonical. Across all sites in prod today, expected total backfill is ~hundreds of canonical rows from ~thousands of discovered_devices rows. Backfill is a single statement, completes in < 1s.

**Reconciliation loop atomicity:** the per-site `INSERT ... ON CONFLICT ... DO UPDATE` is one transaction. Under concurrency with discovered_devices INSERTs, the worst case is staleness bounded by the 60s tick. Acceptable.

**Maya verdict:** APPROVE WITH P0-A (mig number) + P0-C (RLS parity). Schema shape and backfill SQL above are ready-to-ship pending Gate B review against actual prod data.

### Lens 3: Security (Carol) — APPROVE-WITH-FIXES

**P0-C — RLS policy parity (LOAD-BEARING).** `discovered_devices` carries `tenant_org_isolation` + `partner_isolation` policies (per Session 217 RT30 + RT31). Any new site-keyed table read by `client_portal.py` or `partners.py` under `org_connection` MUST replicate both — otherwise reads return zero rows silently. This was a 155K-bundle silent-zero outage class.

Phase 1 only migrates `compliance_packet.py`, which runs under admin context — so the silent-zero risk doesn't fire in Phase 1. **But Phase 2 will migrate client_portal + partners callsites, and if Phase 2's Gate A comes back later and discovers RLS wasn't built into mig 319, we'd need a separate corrective migration.** Build RLS in at table-creation time (mig 319) — included in the SQL above.

**Per-appliance observation preservation verified** — `observed_by_appliances UUID[]` survives reconciliation. Counsel-Rule-4 coverage-degradation signal (`array_length < expected_appliances_count`) is available for the future operator-visibility surface.

**No PHI in canonical_devices** — same schema shape as discovered_devices, no new sensitive columns. PHI-pre-merge gate (Task #54) clean.

**Carol verdict:** APPROVE with P0-C (RLS in mig 319, not deferred). The SQL above closes this.

### Lens 4: Coach — APPROVE

**Sequencing aligned with mig 314 Phase 2a/b/c/d precedent.** That task shipped: Phase 2a = registry constants; Phase 2b = decorator + endpoint integration; Phase 2c = substrate invariant; Phase 2d = pruner. Current device-dedup plan: Phase 1 = canonical table + reconciliation + 1 reader migrated + substrate invariant + CI gate. Phase 2 = remaining 8 reader migrations + drive ratchet to 0. **Phase 2 needs its own Gate A AND Gate B per Session 220 TWO-GATE rule** — explicitly call this out in the Phase 1 commit body so the discipline carries.

**No Class-B antipatterns:**
- Not a hotfix (user explicitly named the antipattern; Option B is the enterprise solution per directive)
- Not a delegated-enumeration violation (v2 design now includes the sibling-surface table from Gate A v1)
- Not a substrate-invariant-deferred (substrate invariant is IN Phase 1 scope)

**One pattern concern:** the design v2 still has the §1-§9 architectural-discussion shape rather than a pure implementation plan. For Phase 1 execution, the commit body should cite the v2 Gate A approval + this Gate A verdict + the §3 commit-plan below. The design doc doesn't need rewriting — but the commit must NOT use the design doc as the implementation plan; use this verdict's §3 commit-plan.

**Coach verdict:** APPROVE. Cite both Gate A v1 (architectural) + this Gate A v2 (implementation) verdicts in Phase 1 commit body.

### Lens 5: Auditor (OCR) — APPROVE-WITH-FIXES

**kit_version bump verified for compliance_packet.py.** Today: `compliance_packet.py` has no `_KIT_VERSION` symbol (verified by grep). The auditor-kit deterministic contract (Session 218 round-table) pins `kit_version: '2.1'` for the auditor-kit ZIP — that's a separate artifact (`auditor_kit_zip_primitives.py`), NOT the monthly compliance packet PDF.

**Implication:** the compliance packet has its OWN methodology versioning to introduce. Recommendation:

1. Add `_PACKET_METHODOLOGY_VERSION = "2.0"` (was implicit "1.0") near the top of `compliance_packet.py`.
2. Emit it in the rendered markdown footer in a new "Methodology" section:
   ```markdown
   ## Methodology

   _Packet methodology version: 2.0 (updated 2026-05-13)_

   Device counts as of methodology version 2.0 use **per-site canonical
   device records** — a unique physical device identified by `(IP address,
   MAC address)` per site is counted once. Prior to 2026-05-13, multi-appliance
   sites' device counts reflected raw observations from each appliance and may
   have over-counted devices observed by multiple appliances on the same network.
   Past packets remain Ed25519-signed and immutable.
   ```
3. Past PDFs are immutable (Ed25519-signed, OTS-anchored). The methodology note is forward-only — exactly per user decision.

**No kit_version 2.1 → 2.2 bump** — that's the AUDITOR KIT version, not the COMPLIANCE PACKET. Confusion in design v2 needs correction: the auditor kit doesn't emit `total_devices` (verified at Gate A v1 §3 Lens 7), so its kit_version is NOT affected by this work. **Compliance packet methodology version is what bumps.**

**OCR verdict:** APPROVE with P1-D below: design v2 + commit body must distinguish `_PACKET_METHODOLOGY_VERSION` (the compliance packet's own version, bumps to 2.0) from `kit_version` (the auditor kit's, stays at 2.1). Don't conflate.

### Lens 6: PM — APPROVE

**Effort estimate alignment verified:**
- Phase 1: mig 319 (1.5h) + reconciliation loop (2h) + compliance_packet migration (0.5h) + CI gate (1.5h) + substrate invariant + runbook (1h) + canonical_metrics entry (0.5h) + tests (1.5h) + commit + Gate B = **~1 working day**. Aligned with user's "long lasting enterprise solutions" directive.
- Phase 2: remaining 8 readers × ~30min each (mostly mechanical) + ratchet drive-down + Gate A/B = **~2 days**.
- Total: **3 days for full enterprise solution** matches v2 design estimate.

**Day-1 visible fix preserved:** even though compliance_packet.py is the Phase 1 reader-migration target (not the user-reported page from `device_sync.get_site_devices`), the page-fix can be batched into Phase 1 if scope allows. Recommend: ship `device_sync.get_site_devices` migration in Phase 1 too (it's the same shape — swap `discovered_devices` for `canonical_devices`, drop the dedup-NULL-mac-coalesce). User sees the page-fix day 1 + the customer artifact (compliance_packet) fix day 1.

**PM concern:** the original v2 §5 (PM lens, Gate A v1) recommended phased delivery with day-1 page-fix. The current Phase 1 scope migrates compliance_packet only. **Suggestion (P1-D):** add `device_sync.get_site_devices` (`device_sync.py:784`) to Phase 1 same-commit — it's the user-reported surface and same migration shape. Cost: +0.5h. Benefit: customer-visible bug closed day 1.

**PM verdict:** APPROVE. Recommend P1-D additive scope of `device_sync.get_site_devices` migration in Phase 1 (preserves PM-lens v1 day-1-visible-progress).

### Lens 7: Counsel — APPROVE

**No retroactive PDF re-issue required.** Past Ed25519-signed packets are immutable; methodology note is forward-only per user decision. Legal-exposure framing:

**Acceptable disclosure language:**
- "Packet methodology version 2.0 (updated 2026-05-13)"
- "Device counts use per-site canonical device records — a unique physical device identified by (IP address, MAC address) per site is counted once."
- "Prior to 2026-05-13, multi-appliance sites' device counts reflected raw observations from each appliance and may have over-counted devices observed by multiple appliances on the same network."

**Banned language (per CLAUDE.md legal-language rule + counsel review):**
- ❌ "Corrected an error"
- ❌ "Fixed a bug" (admits defect class in writing)
- ❌ "Inaccurate counts" (admits inaccuracy as legal posture)
- ✅ "Methodology refinement" / "updated methodology" (factual; doesn't admit defect)

The proposed §5 footer language above uses "may have over-counted" — factual + bounded + does not assert universal over-count. Acceptable.

**§164.504(e) BAA scope:** device count is not a BAA-contract item (not PHI, not minimum-necessary, not §164.524 access right). No BAA disclosure required. No §164.528 disclosure-accounting impact.

**F1 attestation letter + partner portfolio attestation + auditor kit zip:** verified at Gate A v1 §3 Lens 7 — none emit raw device count. **No customer-facing-artifact impact beyond compliance_packet.py.** Bounded exposure.

**Counsel verdict:** APPROVE. The forward-only methodology disclosure language above is enterprise-grade-defensible.

---

## §2 — Implementation probes

### Probe 1: Migration number availability

```
Shipped on disk: 305, 307, 308, 309, 310, 312, 313, 314, 315
RESERVED_MIGRATIONS.md: 311 BLOCKED, 316 reserved (Task #38), 317 reserved (Task #58), 318 reserved (Task #58)
Next free: 319
```

Phase 1 must use **319**, not 316.

### Probe 2: `compliance_packet.py:1167` ready-to-ship migration

**Current shape** (lines 1156-1183):
```python
async def _get_device_inventory(self) -> Dict:
    """Device inventory summary for the reporting period."""
    try:
        result = await self.db.execute(
            text("""SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE os_name ILIKE '%windows%') as windows,
                COUNT(*) FILTER (WHERE os_name ILIKE '%linux%') as linux,
                COUNT(*) FILTER (WHERE os_name ILIKE '%mac%' OR os_name ILIKE '%darwin%') as macos,
                COUNT(*) FILTER (WHERE compliance_status = 'compliant') as compliant,
                COUNT(*) FILTER (WHERE device_status = 'agent_active') as managed
            FROM discovered_devices
            WHERE site_id = :sid"""),
            {"sid": self.site_id},
        )
        ...
```

**Phase 1 migration (proposed, ready-to-ship):**

The COUNT-based query is the over-count source. But `canonical_devices` doesn't carry `os_name`, `compliance_status`, `device_status` columns directly — those are per-discovered_devices-row fields. **Two paths:**

**Path A (recommended for Phase 1):** keep the per-OS / compliance / managed counts joining canonical_devices BACK to discovered_devices to pick up those fields (taking the freshest observation per canonical row):

```python
text("""
    WITH dd_freshest AS (
        SELECT DISTINCT ON (cd.canonical_id)
               cd.canonical_id,
               dd.os_name,
               dd.compliance_status,
               dd.device_status
          FROM canonical_devices cd
          JOIN discovered_devices dd
            ON dd.site_id = cd.site_id
           AND dd.ip_address = cd.ip_address
           AND COALESCE(dd.mac_address, '') = cd.mac_dedup_key
         WHERE cd.site_id = :sid
         ORDER BY cd.canonical_id, dd.last_seen_at DESC
    )
    SELECT
        COUNT(*) as total,
        COUNT(*) FILTER (WHERE os_name ILIKE '%windows%') as windows,
        COUNT(*) FILTER (WHERE os_name ILIKE '%linux%') as linux,
        COUNT(*) FILTER (WHERE os_name ILIKE '%mac%' OR os_name ILIKE '%darwin%') as macos,
        COUNT(*) FILTER (WHERE compliance_status = 'compliant') as compliant,
        COUNT(*) FILTER (WHERE device_status = 'agent_active') as managed
    FROM dd_freshest
""")
# canonical-migration: device_count_per_site — Phase 1 via canonical_devices CTE join (Task #73)
```

**Path B (Phase 2 evolution):** add `os_name`, `compliance_status`, `device_status` to canonical_devices schema (Phase 3 enrichment) and drop the JOIN. Defer.

Path A is the cleanest Phase 1 migration. `COUNT(*)` of `dd_freshest` = canonical row count. The FILTER aggregates pick up the freshest discovered_devices observation's OS/compliance/managed flag per canonical row.

### Probe 3: Substrate-invariant runbook stub

File: `mcp-server/central-command/backend/substrate_runbooks/canonical_devices_freshness.md`

```markdown
# canonical_devices_freshness

**Severity:** sev2
**Display name:** Canonical Devices Reconciliation Loop Stale

## What this means (plain English)

The background loop that builds the canonical view of devices per site has not
updated any row for an active site in the last 60 minutes. Customers may see
slightly stale device counts on dashboards and in their monthly compliance
packet. The underlying device data is still being collected; only the
deduplicated view is stale.

## Root cause categories

- The reconciliation background loop has stalled (check `/api/admin/substrate-health` for `bg_loop_silent`).
- Reconciliation transaction is failing for a specific site (check ERROR logs for `canonical_devices` UPSERT failures).
- A new site has appliances reporting but the loop has not yet ticked for it (transient — clears within 60s).

## Immediate action

- Panel: substrate-health row should clear on next 60s tick.
- If row persists > 5 minutes: check `backend/logs/` for ERROR-level entries containing `canonical_devices`.
- If reconciliation is wedged on a specific site, restart the loop:
  ```
  systemctl restart mcp-server  # forces background_tasks re-init
  ```

## Verification

- Panel: invariant row should clear on next 60s tick.
- CLI: `psql -c "SELECT site_id, MAX(reconciled_at) FROM canonical_devices GROUP BY site_id ORDER BY 2 ASC LIMIT 10;"` — all timestamps within last 60min for active sites.

## Escalation

NOT a security event. Operational hygiene only. If reconciliation has been
stale for > 1 hour on a customer-facing site, the monthly compliance packet
may emit slightly stale counts — escalate to engineering for loop debug, not
to security.

## Related runbooks

- `bg_loop_silent.md` — generic stuck-background-loop class.
- `discovered_devices_freshness.md` — sibling invariant on the source table.

## Change log

- 2026-05-13 — generated for Task #73 Phase 1 canonical_devices rollout.
```

### Probe 4: `canonical_metrics.py` entry shape

Insert into CANONICAL_METRICS:

```python
"device_count_per_site": {
    "canonical_helper": "canonical_devices table (mig 319) read via DISTINCT canonical_id per site",
    "permitted_inline_in_module": "canonical_devices_helpers",  # future Phase 2 helper module
    "allowlist": [
        # Operator-only callsites — Prometheus + per-appliance audit trail
        {"signature": "prometheus_metrics.*", "classification": "operator_only"},
        {"signature": "appliance_trace.*", "classification": "operator_only"},
        # Substrate invariants — raw reads are by design
        {"signature": "assertions._check_discovered_devices_freshness", "classification": "operator_only"},
        # Write paths — INSERT/UPDATE/DELETE callsites
        {"signature": "device_sync._compute_*", "classification": "write_path"},
        {"signature": "device_sync.merge_*", "classification": "write_path"},
        {"signature": "health_monitor.*owner_appliance*", "classification": "write_path"},
        # DISTINCT-aggregation callsites — already deduping
        {"signature": "sites.py:5090-5116", "classification": "operator_only"},
        # Phase 2 migration targets — remaining customer-facing readers
        {"signature": "partners.py:1892", "classification": "migrate"},
        {"signature": "partners.py:2587", "classification": "migrate"},
        {"signature": "partners.py:2595", "classification": "migrate"},
        {"signature": "partners.py:2602", "classification": "migrate"},
        {"signature": "portal.py:1251", "classification": "migrate"},
        {"signature": "portal.py:2137", "classification": "migrate"},
        {"signature": "client_portal.py:1289", "classification": "migrate"},
        {"signature": "client_portal.py:4732", "classification": "migrate"},
        {"signature": "routes.py:5313", "classification": "migrate"},
        {"signature": "routes.py:5322", "classification": "migrate"},
        {"signature": "routes.py:5331", "classification": "migrate"},
        {"signature": "routes.py:5762", "classification": "migrate"},
        {"signature": "routes.py:5857", "classification": "migrate"},
        {"signature": "routes.py:6396", "classification": "migrate"},
        {"signature": "routes.py:8592", "classification": "migrate"},
        {"signature": "sites.py:1897", "classification": "migrate"},
        {"signature": "sites.py:5644", "classification": "migrate"},
        {"signature": "sites.py:7271", "classification": "migrate"},
        {"signature": "background_tasks.py:1048", "classification": "migrate"},
    ],
    "display_null_passthrough_required": False,
},
```

---

## §3 — Phase 1 commit-plan (ordered, ready-to-execute)

**Commit body must cite both Gate A v1 (architectural, APPROVE-WITH-FIXES) + this Gate A v2 (implementation, APPROVE-WITH-FIXES) verdicts. Both verdicts' P0s must be marked closed.**

1. **`mcp-server/central-command/backend/migrations/319_canonical_devices.sql`** — full SQL per §1 Lens 2 (table + 3 indexes + 3 RLS policies + comments + idempotent backfill).
2. **`mcp-server/central-command/backend/migrations/RESERVED_MIGRATIONS.md`** — claim 319 row (Task #73, expected_ship 2026-05-20):
   ```
   | 319 | in_progress | (device-dedup canonical_devices — see audit/coach-device-dedup-implementation-gate-a-2026-05-13.md) | 2026-05-13 | 2026-05-20 | #73 | Phase 1 — canonical table + reconciliation + 1 reader migrated |
   ```
   Then REMOVE the row in the commit that ships the migration file (per ledger lifecycle rule). For Phase 1 SAME-COMMIT migration shipment, add and remove in the same commit (net: 0 rows added).
3. **`mcp-server/central-command/audit/device-dedup-architectural-design-2026-05-13.md`** — replace all "mig 316" with "mig 319"; add the `<!-- mig-claim:319 task:#73 -->` marker on a line by itself outside any code fence.
4. **`mcp-server/central-command/backend/canonical_metrics.py`** — add `device_count_per_site` entry per Probe 4 above. Verify `tests/test_canonical_metrics_registry.py` still passes (allowlist signatures exist in codebase).
5. **`mcp-server/central-command/backend/background_tasks.py`** — register `_reconcile_canonical_devices_loop` with 60s tick + bg_heartbeat supervision + per-site iteration using the majority-vote SQL from §1 Lens 1.
6. **`mcp-server/central-command/backend/compliance_packet.py`** — migrate `_get_device_inventory` per Probe 2 Path A; add `_PACKET_METHODOLOGY_VERSION = "2.0"` constant + emit "Methodology" section in `_render_markdown`; add `# canonical-migration: device_count_per_site — Phase 1 via canonical_devices CTE join (Task #73)` marker on the line that opens the new query.
7. **`mcp-server/central-command/backend/device_sync.py:784`** (P1-D, recommended for user-visible day-1 fix) — migrate `get_site_devices` to read from canonical_devices (drop DISTINCT-ON-NULL-mac hack; ORDER BY canonical row).
8. **`mcp-server/central-command/backend/assertions.py`** — add `_check_canonical_devices_freshness` (sev2, 60min staleness threshold) + register `Assertion(name="canonical_devices_freshness", severity="sev2", description="...", check=_check_canonical_devices_freshness)` after the existing `discovered_devices_freshness` entry.
9. **`mcp-server/central-command/backend/substrate_runbooks/canonical_devices_freshness.md`** — runbook stub per Probe 3 above.
10. **`mcp-server/central-command/backend/tests/test_no_raw_discovered_devices_count.py`** — NEW. Static-grep CI gate scanning backend files for `FROM discovered_devices` outside `canonical_metrics.allowlist[device_count_per_site]`. Ratchet baseline = 18 (count of `migrate`-classified entries in §2 Probe 4) post-Phase-1 migration. Drives to 0 across Phase 2.
11. **`mcp-server/central-command/backend/tests/test_canonical_devices_reconciliation.py`** — NEW. Test: (a) backfill is idempotent (run twice → same row count); (b) majority-vote winner is deterministic across mixed-vote inputs; (c) alphabetical-tiebreaker fires on 2-way ties; (d) RLS policies present.
12. **Commit body**: cite both Gate A verdicts + verdict closure on P0-A (mig 319 used) + P0-B (ratchet baseline computed post-migration) + P0-C (RLS in mig 319) + P1-D (device_sync.get_site_devices migrated).
13. **Gate B (separate session)**: fork-based 4-lens review of as-implemented artifact per Session 220 TWO-GATE rule. Gate B MUST run the full pre-push sweep (`bash .githooks/full-test-sweep.sh`), not diff-only review.

---

## §4 — CI gate baseline

**Today's raw `FROM discovered_devices` count outside test/script directories: 47.**

**Reader classification:**
- `migrate` (customer-facing, must move to canonical): 18 callsites
- `operator_only` (legitimate raw reads): ~10 callsites (appliance_trace, assertions freshness, prometheus, DISTINCT-already-aggregating sites.py lines)
- `write_path` (INSERT/UPDATE/DELETE/SELECT-id-for-mutation): ~19 callsites (device_sync.py mostly)

**Phase 1 ratchet baseline:** **18 - 1 = 17 `migrate`-classified offenders** after `compliance_packet.py` and (P1-D) `device_sync.get_site_devices` migrate. If P1-D included: **18 - 2 = 16**.

**Phase 2 target:** drive to **0** by migrating all remaining `migrate`-classified callsites.

---

## §5 — P1 / P2 / followups

**P1-D — Add `device_sync.get_site_devices` migration to Phase 1.** Preserves day-1 user-visible page-fix. +0.5h cost. Recommend approving.

**P1-E — Phase 2 Gate A trigger.** Open TaskCreate now: "Phase 2 Gate A — migrate remaining 16-17 customer-facing discovered_devices readers to canonical_devices + drive ratchet to 0." Class-B. Gate A on the as-planned Phase 2 commit-plan; Gate B on the as-implemented artifact.

**P2 — Methodology disclosure copy-edit by Counsel before first packet emission.** Once `_PACKET_METHODOLOGY_VERSION = "2.0"` lands, the first packet emitted will carry the methodology note. Recommend: ask Counsel (queue) to sign off on the exact 3-sentence forward-disclosure language before that emission. Bundle into the existing counsel-queue (Task #37).

**P3 — Phase 3 enrichment.** Add `os_name`, `compliance_status`, `device_status`, `confidence_score` as first-class canonical_devices columns. Drops the Path A JOIN-back-to-discovered_devices and makes canonical_devices fully self-sufficient. Defer to Phase 3 Gate A.

---

## §6 — Final verdict

**APPROVE-WITH-FIXES — Phase 1 may proceed once 3 P0s are addressed in the commit body:**

- **P0-A** — Use mig **319** not 316 (316 is claimed by Task #38; 317–318 by Task #58)
- **P0-B** — CI-gate ratchet baseline computed AFTER compliance_packet migration applied (baseline 17, not pre-migration 18)
- **P0-C** — RLS policies (`tenant_org_isolation` + `partner_isolation` mirrors) present in mig 319 at table-creation time, not deferred

**P1 recommended additions:**
- **P1-D** — Add `device_sync.get_site_devices` migration to Phase 1 (preserves day-1 user-visible page-fix; +0.5h)
- **P1-E** — Open Phase 2 Gate A TaskCreate item in same commit

**Approved with these closures, Phase 1 ships:**
- Migration 319 with table + RLS + indexes + idempotent backfill
- `canonical_metrics.py` `device_count_per_site` entry (Counsel Rule 1)
- Reconciliation loop registered in `background_tasks.py` with majority-vote deterministic-tiebreaker SQL
- `compliance_packet.py:1167` migrated to canonical (+ `_PACKET_METHODOLOGY_VERSION = "2.0"` + forward-only methodology note)
- (P1-D) `device_sync.get_site_devices` migrated to canonical
- Substrate invariant `canonical_devices_freshness` (sev2) + runbook stub
- CI gate `test_no_raw_discovered_devices_count.py` at ratchet baseline 17 (or 16 if P1-D included)
- Tests covering majority-vote determinism + RLS parity + backfill idempotency
- Commit body cites BOTH Gate A v1 + this Gate A v2 verdicts

**Gate B follow-up** (separate session, separate fork) on the as-implemented artifact MANDATORY per Session 220 TWO-GATE rule. Gate B MUST run `bash .githooks/full-test-sweep.sh` and cite pass/fail count — diff-only review = automatic BLOCK pending sweep verification.

---

*Verdict produced by Class-B Gate A v2 7-lens fork on the IMPLEMENTATION plan (the predecessor Gate A v1 reviewed the ARCHITECTURAL design). Per Session 220 TWO-GATE rule, this artifact must ALSO receive a Gate B fork review on the as-implemented commit before any commit body says "shipped."*
