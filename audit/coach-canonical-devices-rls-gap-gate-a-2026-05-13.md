# Gate A — canonical_devices RLS appliance-bearer policy gap (mig 320)

**Task #75 dependency | Date 2026-05-13 | Lenses: Steve / Maya / Carol / Coach / PM / Counsel (OCR N/A — operational; no customer-facing artifact)**

Per Task #75 Gate A finding (audit/coach-sites-5644-hotpath-gate-a-2026-05-13.md): sites.py:5644 hot-path migration to canonical_devices is BLOCKED by an RLS gap. canonical_devices (mig 319) ships with 3 policies (admin / tenant_org / partner). The sites.py:5644 callsite runs under `tenant_connection(pool, site_id=...)` which sets `app.current_tenant` — **NONE of the 3 existing canonical_devices policies fire on that variable.** Read would silently return zero rows.

---

## Verdict: APPROVE-WITH-FIXES (1 P0, 1 P1)

## 200-Word Summary

The brief's premise was correct in direction but had one factual error: the RLS variable is **`app.current_tenant`** (not `app.current_site_id`). Verified by reading `tenant_middleware.py:105` — `tenant_connection` issues `SET LOCAL app.current_tenant = '<safe_id>'`. The sibling policy is `discovered_devices_tenant_isolation` (mig 080 lines 167-171) which admits rows where `site_id = current_setting('app.current_tenant', true)`. canonical_devices (mig 319) skipped this 4th policy — the design doc's P0-C claim of "RLS parity with discovered_devices" is partial (admin + tenant_org + partner, but missing the original mig-080 `tenant_isolation`). Mig 320 (next free integer past disk-high 319 AND past ledger-reserved 318) adds the missing policy plus `FORCE ROW LEVEL SECURITY` parity. Policy SQL below mirrors mig 080's discovered_devices shape exactly. One P0 (variable-name correction from brief), one P1 (FORCE RLS parity). Unblocks Task #75 hot-path soak. Effort: 30 min for the migration + 15 min for ledger row update; backport-to-mig-319 NOT recommended (mig 319 already shipped; additive forward-migration is the cleaner reversible path).

---

## Sibling policy shape (Steve — verified against mig 080 production state)

discovered_devices has 4 policies in production:

| Policy | Migration | Trigger variable | Shape |
|--------|-----------|------------------|-------|
| `discovered_devices_tenant_isolation` | 080 line 167 | `app.current_tenant` OR `app.is_admin='true'` | `site_id = current_setting('app.current_tenant', true)` |
| `org_isolation` (legacy) | 087 line 177 | `app.current_org` (UUID cast) | `site_id IN (SELECT ... WHERE client_org_id = ...)` |
| `tenant_org_isolation` | 278 line 120 | `app.current_org` | `rls_site_belongs_to_current_org(site_id)` |
| `tenant_partner_isolation` | 297 | `app.current_partner_id` | `rls_site_belongs_to_current_partner(site_id)` |

Postgres ORs policies — any matching policy admits the row. The mig-080 `tenant_isolation` is the LOAD-BEARING policy for **appliance-bearer auth** (the checkin handler's `tenant_connection(site_id=...)` path); it is also the path used by every other `tenant_connection`-wrapped read in sites.py (40+ callsites).

canonical_devices (mig 319) ships with admin_all + tenant_org_isolation + partner_isolation — **missing the mig-080-equivalent `tenant_isolation`**. The design doc said "P0-C — RLS policy parity with discovered_devices (tenant_org + partner + admin)" — that enumeration itself reveals the gap: the 4th policy was never in the list.

## Recommended mig 320 SQL (Carol — load-bearing wording)

```sql
-- Migration 320: canonical_devices tenant_isolation RLS parity gap
--
-- Task #75 Gate A blocker: sites.py:5644 hot-path runs under
-- tenant_connection(site_id=...) which sets app.current_tenant. None of
-- mig 319's 3 policies (admin_all, tenant_org_isolation, partner_isolation)
-- admits rows on app.current_tenant — the read returns zero rows.
--
-- Fix: add the mig-080-equivalent tenant_isolation policy (the appliance-
-- bearer auth path) and FORCE ROW LEVEL SECURITY for table-owner parity.
--
-- Posture: NOT broader than discovered_devices. The same site_id =
-- current_setting('app.current_tenant', true) shape that mig 080 has
-- protected discovered_devices with since Phase 4 P2. Cross-site spoofing
-- is blocked because tenant_connection validates site_id via
-- _SAFE_SITE_ID regex before interpolation (tenant_middleware.py:32).

BEGIN;

-- P1 (Carol) — FORCE RLS parity with discovered_devices (mig 080 line 165).
-- Without FORCE, table-owner connections bypass RLS. mig 319 enabled but
-- did not FORCE. Closes defense-in-depth gap.
ALTER TABLE canonical_devices FORCE ROW LEVEL SECURITY;

-- P0 — the missing 4th policy. Appliance-bearer auth path.
-- Shape MUST mirror mig 080 discovered_devices_tenant_isolation EXACTLY
-- so that any future tenant_connection callsite reads canonical_devices
-- with identical row-set semantics to discovered_devices.
CREATE POLICY canonical_devices_tenant_isolation
    ON canonical_devices FOR ALL
    USING (
        current_setting('app.is_admin', true) = 'true'
        OR site_id = current_setting('app.current_tenant', true)
    );

-- Audit-log row.
INSERT INTO admin_audit_log (
    user_id, username, action, target, details, ip_address
) VALUES (
    NULL,
    'system',
    'canonical_devices_tenant_isolation_added',
    'canonical_devices',
    jsonb_build_object(
        'migration', '320_canonical_devices_tenant_isolation',
        'task', '#75',
        'gap_class', 'rls_appliance_bearer_path_missing',
        'parity_table', 'discovered_devices',
        'parity_policy', 'discovered_devices_tenant_isolation (mig 080)',
        'unblocks', 'sites.py:5644 hot-path carve-out',
        'force_rls_added', true
    ),
    NULL
);

COMMIT;
```

**Why this exact shape (NOT the brief's `app.current_site_id`):**
- `tenant_middleware.py:105` issues `SET LOCAL app.current_tenant = '...'` — the variable is `app.current_tenant`, not `app.current_site_id`.
- grep across `backend/` + `migrations/` confirms ZERO callsites of `app.current_site_id`. Adopting that name would create a dead policy (no one sets the variable).
- The brief's suggested shape would have shipped a policy that NEVER fires — adversarial review caught this.

## Maya — policy-OR semantics + ordering

- **PG policy combination is OR (PERMISSIVE default).** Adding a 4th policy is purely additive: it CANNOT subtract from the row set the existing 3 policies already admit. Risk of regression to existing client-portal / partner-portal / admin paths: **NONE**.
- **Order doesn't matter for PERMISSIVE policies.** PG evaluates each USING predicate; if ANY returns true, the row is admitted. No ordering concern.
- **EXPLAIN-time cost:** the new predicate is `current_setting() = 'true' OR site_id = current_setting()` — both are STABLE function calls evaluated once per row scanned. With the existing `canonical_devices_site_last_seen_idx (site_id, last_seen_at DESC)` index, the planner can push `site_id = $tenant` into an index seek when tenant is set. ~0ms net.
- **No COUNT(*) class concern** — sites.py:5644 is LIMIT 5; the mig-219 partition class doesn't apply here.

## Carol — load-bearing constraint binds tightly

- **Constraint binding:** `site_id = current_setting('app.current_tenant', true)`. `current_setting(..., true)` returns NULL if the setting is unset; PG's `site_id = NULL` is NULL (never true). Therefore an unset tenant context admits NOTHING via this policy (the OR with admin_all still grants admin bypass). This is the exact safety property mig 080 relies on.
- **Cross-site spoofing path:** would require an attacker to control `app.current_tenant`. `tenant_connection()` validates the input through `_SAFE_SITE_ID = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")` BEFORE interpolation (tenant_middleware.py:32). SQL injection blocked at the SET statement; the only way to set the variable to another site's id is to authenticate as that site. The appliance bearer dependency (`require_appliance_bearer` + `_enforce_site_id`) validates that already.
- **FORCE RLS:** without `FORCE ROW LEVEL SECURITY`, the table-OWNING role bypasses RLS. mig 080 line 165 forces it on discovered_devices; mig 319 forgot. The P1 fix closes the defense-in-depth gap. Production impact today: zero (we don't run as the table-owning role under appliance auth), but it's a class of footgun where a future migration that runs as the owner could accidentally bypass.

## Coach — migration number verification + ledger hygiene

- **Disk-high:** mig 319 (verified by `ls migrations/*.sql | sort -n | tail`).
- **Reserved in RESERVED_MIGRATIONS.md:** 311 (Vault), 316 (load harness), 317 (P-F9 a), 318 (P-F9 b).
- **Next free integer past both:** 319 is taken, 320 is FREE. Use **320**.
- **Ledger update:** mig 320 does NOT need a reservation row (will ship in the same commit) — per the ledger's "Lifecycle" rule, claim-and-ship-same-commit skips the in-flight reservation.
- **NEVER backport to mig 319.** Mig 319 already shipped (canonical_devices table is in production at nvb2). Rewriting a shipped migration violates the additive-only invariant + breaks the migration checksum chain. Forward-only mig 320 is the right shape.

## PM — effort + sequencing

- **Effort:** ~30min for mig 320 SQL + ~10min for ledger no-op (skip per Coach above) + ~15min for boot smoke against staging.
- **Sequencing:** Mig 320 MUST ship and verify-deploy BEFORE Task #75 sites.py:5644 hot-path migration. Otherwise the migrated query returns zero rows from canonical_devices under tenant_connection, breaking pending_deploy provisioning fleet-wide.
- **Gate B at completion:** validate that the policy fires by running a probe via `tenant_connection(pool, site_id='north-valley-branch-2')` then `SELECT count(*) FROM canonical_devices` — must return > 0. Mirror probe with no tenant context — must return 0. Document in audit/coach-canonical-devices-rls-gap-gate-b-YYYY-MM-DD.md.

## Counsel — Rule 3 chain-of-custody implication

- Rule 3 (no privileged action without attested chain): canonical_devices is NOT a privileged-order target — this is a read-side gap, not a write-side bypass. No privileged-attestation lockstep entries required.
- Rule 4 (no orphan coverage): the missing policy creates **silent zero-row reads** for every tenant_connection-wrapped caller — exactly the "silent orphan" class Rule 4 prohibits. Mig 320 closes it.
- Rule 1 (canonical metric): canonical_devices IS the canonical source for `device_count_per_site` (Task #50). A silently-empty read would emit `total_devices: 0` for every site to compliance packets — a customer-visible truth violation. Mig 320 closes the path.

---

## P0 / P1 enumeration (binding under TWO-GATE rule)

| ID | Severity | Description | Resolution |
|----|----------|-------------|------------|
| P0-1 | P0 | Brief's variable name `app.current_site_id` is wrong — actual is `app.current_tenant`. Adopting brief verbatim would ship a dead policy. | Use the SQL above; reject brief's suggested shape. |
| P1-1 | P1 | mig 319 enabled but did not FORCE RLS on canonical_devices (parity gap vs mig 080 line 165). | Include `ALTER TABLE canonical_devices FORCE ROW LEVEL SECURITY` in mig 320. |

## Final verdict

**APPROVE-WITH-FIXES** — proceed to apply mig 320 with the SQL above. Variable name `app.current_tenant` is verified against `tenant_middleware.py:105` + `migrations/080_rls_remaining_tables.sql:170`; migration number 320 verified against disk-high + ledger; policy shape mirrors discovered_devices_tenant_isolation exactly. Run Gate B post-deploy with the row-count probe described in the PM section.
