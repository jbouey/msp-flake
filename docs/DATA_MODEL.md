# Data Model Documentation

<!-- updated 2026-05-16 — Session-220 doc refresh -->

> **Last verified:** 2026-05-16 (Session 220 close — canonical_devices,
> baa_signatures, substrate_violations.synthetic, and 6 new substrate
> invariants).
>
> **Canonical current-state authority:** `OsirisCare_Owners_Manual_
> and_Auditor_Packet.pdf` (in `~/Downloads/`, generated 2026-05-06)
> + the Master BAA v1.0-INTERIM (`docs/legal/MASTER_BAA_v1.0_
> INTERIM.md`, effective 2026-05-13). Where this doc and the
> binding instruments disagree, the binding instruments win.

## Overview

This document describes the central database schema, data flows, and naming conventions to prevent future mismatches between components. Latest applied migration as of this update: **323** (`substrate_violations.synthetic` marker).

---

## Schema-of-Record Tables

### Evidence & Attestation Chain (Tier-1 SLO scope)

| Table | Purpose | Writer | Key Fields | Notes |
|-------|---------|--------|------------|-------|
| `compliance_bundles` | **THE** evidence table — Ed25519 signed, hash-chained, OTS-anchored, partitioned by month | Appliance Agent (PHI-scrubbed) | `bundle_id`, `site_id`, `check_type`, `signature_valid`, `created_at` | **Schema-of-record.** PARTITIONED (mig 138). Use DELETE+INSERT upsert (no ON CONFLICT on partitioned tables). `canonical_site_id()` is FORBIDDEN here — Ed25519+OTS bind to original site_id. CI gate `test_canonical_not_used_for_compliance_bundles.py` |
| `ots_proofs` | OpenTimestamps proof files | Merkle batch worker | `bundle_id`, `status`, `anchored_at` | CHECK constraint (mig 307) locks out ad-hoc 'verified' |
| `l2_decisions` | LLM L2 planner decision audit rows | `record_l2_decision()` | `incident_id`, `pattern_signature`, `llm_model`, `runbook_id` | **Schema-of-record for L2 audit.** `pattern_signature='L2-ORPHAN-BACKFILL-MIG-300'` + `llm_model='backfill_synthetic'` distinguish backfilled historical rows (mig 300/301/302) |
| `baa_signatures` | E-signed BAA execution rows (Master BAA v1.0-INTERIM) | Signup-flow `sign-baa` atomic-FK path | `client_org_id` (FK to `client_orgs.id`, mig 321), `email`, `baa_version`, `signed_at` | **Schema-of-record for BAA execution.** Append-only (`trg_baa_no_update`); acknowledgment-only flag for legacy rows (mig 312) |
| `feature_flags` | Attested, dual-admin feature flags (e.g. cross-org-relocate enablement) | `/api/admin/feature-flags/*` | `flag_name`, `enabled`, `proposer`, `approver`, `attestation_bundle_id` | CHECK: `lower(approver) <> lower(proposer)` (mig 282) |
| `substrate_violations` | Substrate Integrity Engine invariant violations | Substrate engine 60s tick | `invariant_name`, `severity`, `site_id`, `synthetic`, `detected_at`, `resolved_at` | `synthetic` marker (mig 323) carves out soak-rig data from customer-facing readers; derived at INSERT-time from `site_id LIKE 'synthetic-%'` |
| `admin_audit_log` | Append-only admin / system action log | All admin endpoints | `username` (NOT `actor`), `action`, `target`, `details`, `ip_address`, `created_at` | `auditor_kit_download` rows denormalize `site_id` + `client_org_id` at write time so the BAA-enforcement invariant skips the JOIN |
| `appliance_heartbeats` | D1 heartbeat-signature verification log (mig 313) | Appliance Agent → backend verify path | `appliance_id`, `signature_valid`, `created_at` | Tier-1.5 SLO scope; PRE-1 for Master BAA v2.0 (≥7d ≥99% `signature_valid IS TRUE`) |

### Execution & Healing

| Table | Purpose | Writer | Key Fields |
|-------|---------|--------|------------|
| `execution_telemetry` | Agent execution logs (L1/L2/L3 healing) | Appliance Agent | `runbook_id`, `resolution_level`, `success`, `order_id` (fwd-compat, mig 286) |
| `incidents` | Incident tracking & lifecycle | Appliance Agent, Dashboard | `resolution_tier`, `status`, `severity`, `check_type` |
| `orders` | Server-initiated commands | Dashboard/API | `runbook_id`, `status`, `result` |
| `fleet_orders` | Fleet-wide signed orders (NO `site_id`/`appliance_id` columns; scoping via signed `target_appliance_id`) | `order_signing.sign_*()` | `order_id`, `order_type`, `payload` (signed) |

### Runbooks & Rules

| Table | Purpose | Writer | Key Fields |
|-------|---------|--------|------------|
| `runbooks` | Runbook definitions (HIPAA mappings) | Seed data, Admin | `runbook_id`, `agent_runbook_id` (bridge, mig 284), `category`, `steps` |
| `l1_rules` | Deterministic L1 rules | Learning promotion | `rule_id`, `pattern_signature` |
| `promoted_rules` | Per-site rule rollout state | `flywheel_promote.py` | `(site_id, rule_id)` UNIQUE (mig 247) — natural key is the composite, NOT `rule_id` alone |
| `patterns` | Learned patterns awaiting promotion | Agent sync | `pattern_signature`, `status`, `success_rate` |
| `check_type_registry` | **Single source of truth** for check names, categories, HIPAA controls, monitoring-only flags (mig 157) | Migration | `check_type`, `category`, `monitoring_only` |

### Appliances, Sites & Devices

| Table | Purpose | Writer | Key Fields |
|-------|---------|--------|------------|
| `sites` | Client sites | Admin, Stripe webhook | `site_id`, `client_org_id`, `partner_id`, `status`, `prior_client_org_id` (mig 280), `synthetic` (mig 315) |
| `site_appliances` | Per-appliance state (NOT `sites.agent_public_key`) | Provisioning, checkin | `appliance_id`, `site_id`, `agent_public_key` (per-appliance, Session 196), `deleted_at` (filter on JOIN line per Session 218 RT33 P1) |
| `site_canonical_mapping` | Alias map for site renames (mig 256/257) | `rename_site()` SQL fn ONLY | `original_site_id`, `canonical_site_id` |
| `canonical_devices` | **Deduplicated device source-of-truth** (mig 319) — collapses ARP-scan duplicates from multi-appliance sites | `reconcile_canonical_devices()` | `canonical_id`, `site_id`, `ip_address`, `mac_address`, `observed_by_appliances UUID[]`. Used for `device_count_per_site` canonical metric (Counsel Rule 1) |
| `discovered_devices` | Per-appliance raw scan results | Appliance Agent | `appliance_id`, `ip_address`, `mac_address` |
| `go_agents` | Workstation agents (Windows/macOS/Linux) | gRPC enrollment | `site_id` (FK → sites ON DELETE CASCADE, mig 144) |
| `appliance_provisioning` | Provisioning state per site | Provisioning endpoint | `site_id`, `appliance_mac` |

### Org-Level State Machines

| Table | Purpose | Writer | Key Fields |
|-------|---------|--------|------------|
| `client_orgs` | Customer covered-entity org | Stripe webhook, admin | `id`, `primary_email`, `baa_on_file`, `status` (CHECK constraint, mig 322) |
| `client_org_owner_transfer_requests` | 6-event state machine (mig 273) | `client_owner_transfer.py` | `transfer_id`, 24h cooling-off, magic-link target accept |
| `partner_admin_transfer_requests` | 4-event state machine (mig 274) | `partner_admin_transfer.py` | `transfer_id`, immediate-completion, OAuth re-auth |
| `cross_org_site_relocate_requests` | 3-actor state machine (mig 279) | `cross_org_site_relocate.py` | `request_id`, pinned `expected_*_email` columns, 24h cooling-off CHECK |
| `canonical_metric_samples` | Counsel Rule 1 metric-class samples (mig 314) — partitioned by month | Canonical metric sampler | `tenant_id`, `metric_class`, `classification`, `captured_at` |

### Billing & Provisioning

| Table | Purpose | Writer | Notes |
|-------|---------|--------|-------|
| `stripe_subscriptions` | Stripe billing state | Stripe webhook | PHI-free CHECK enforced; Stripe products use `lookup_keys` not price IDs |
| `appliance_provisions` | Cold-onboarding idempotency (mig 296) | Provisioning endpoint | Prevents Stripe webhook double-fire |

### Logging & Telemetry

| Table | Purpose | Notes |
|-------|---------|-------|
| `log_entries` | Logshipper-aggregated logs from appliances (partitioned monthly, ~4.2M rows) | `SELECT COUNT(*)` triggers statement timeout — use `SUM(reltuples)` from `pg_class` (Session 219 `prometheus_metrics.py:521` fix) |
| `aggregated_pattern_stats` | Per-site flywheel learning stats | Site-id-keyed — must migrate in lockstep with `rename_site()` |
| `appliance_status_rollup` (MV) | Performance MV for appliance status (mig 193) | **DO NOT** read from client/partner portal endpoints — MVs don't inherit RLS; query `site_appliances` directly with inline LATERAL heartbeat join (Session 218 RT33 P2 Steve veto) |

### Legacy / Deprecated

| Table | Status | Notes |
|-------|--------|-------|
| `evidence_bundles` | **LEGACY** (1 row) | Superseded by `compliance_bundles`. Do not write. |
| `site_go_agent_summaries` | **STALE** | Computed live on read by `get_site_go_agents()` (Session 202) — do not consume |
| `compliance_scores` | **DENORMALIZED ROLLUP** | Operator-only; not the canonical compliance-score source (canonical = `compliance_score.compute_compliance_score()`, Session 217 RT25 + RT30) |

---

## Resolution Tier Tracking

### L2 Decisions Tracking — RESOLVED 2026-05-06 (mig 285) + HARDENED 2026-05-09 (Session 219 mig 300)

> **Historical context (2026-01-31):** original finding was that
> `incidents.resolution_tier` showed 0 L2 records. The 2026-05-06
> resolution shipped `v_l2_outcomes` view + `compute_l2_success_rate()`
> function. The 2026-05-09 hardening (Session 219) closed a separate
> ghost-L2 audit gap: 26 incidents tagged `resolution_tier='L2'` with
> no matching `l2_decisions` row because both `agent_api.py:1338` and
> `main.py:4530` swallowed `record_l2_decision()` exceptions and
> continued setting `resolution_tier='L2'` anyway.

**Current state (post-mig-300):** `l2_decision_recorded: bool`
gate refuses to set `resolution_tier='L2'` without a successful
`record_l2_decision()` call — escalates to L3 instead. Substrate
invariant `l2_resolution_without_decision_record` (sev2) catches
regressions. **Never** set `resolution_tier='L2'` (Python literal OR
SQL UPDATE) without an `l2_decision_recorded` reference within 80
lines above. Pinned by `tests/test_l2_resolution_requires_decision_
record.py`. Mig 300/301/302 backfill synthetic `l2_decisions` rows
with `pattern_signature='L2-ORPHAN-BACKFILL-MIG-300'` so auditors
can distinguish historical from prospective.

```sql
-- Current canonical L2 success rate (works as of mig 285):
SELECT * FROM compute_l2_success_rate(window_days := 30);
```

### Runbook ID Mismatch — RESOLVED 2026-05-06 (mig 284)

`runbooks.agent_runbook_id TEXT UNIQUE` bridge column + backfilled
mapping for known L1-* IDs + placeholder rows for orphan L1-* IDs
(mig 299 added round-2 bridges for 4 unbridged agent_runbook_ids).
Substrate invariant `unbridged_telemetry_runbook_ids` (sev2) catches
future drift.

### Orders stay pending — RESOLVED 2026-05-06 (mig 286)

Explicit `/api/agent/orders/complete` endpoint as primary completion
path + `sweep_stuck_orders()` SQL function backstop. Substrate
invariant `orders_stuck_acknowledged` (sev2).

---

## Substrate invariants (Session 207 engine + Session 220 additions)

The Substrate Integrity Engine asserts ~60 invariants per 60s tick
(per-assertion `admin_transaction()` block; cascade-fail closed
Session 220 `57960d4b`). Recent additions:

| Invariant | Severity | Catches |
|-----------|----------|---------|
| `l2_resolution_without_decision_record` | sev2 | `incidents.resolution_tier='L2'` with no matching `l2_decisions` row |
| `l1_resolution_without_remediation_step` | sev2 | L1 escalate-action false-heals (1,137 historical orphans found in 3 chaos-lab check_types) |
| `daemon_heartbeat_signature_unverified` | sev2 | D1 heartbeat-verification path returned no verdict |
| `daemon_heartbeat_signature_invalid` | sev2 | D1 returned invalid signature |
| `daemon_heartbeat_signature_unsigned` | sev2 | Heartbeat lacks signature payload |
| `cross_org_relocate_chain_orphan` | sev1 | Sites with `prior_client_org_id` set but no completed relocate row |
| `sensitive_workflow_advanced_without_baa` | sev1 | 5 BAA-gated workflows advanced without an active BAA signature (excludes admin + legacy `?token=` carve-outs) |
| `l2_escalations_missed` | sev2 | L2-missed disclosure (mig 308) |

---

## Naming Conventions by Table

| Table | ID Format | Example |
|-------|-----------|---------|
| `runbooks.runbook_id` | `RB-{PLATFORM}-{CATEGORY}-{NUM}` | `RB-WIN-SEC-001` |
| `runbooks.agent_runbook_id` (bridge, mig 284) | `L1-{CATEGORY}-{NUM}` | `L1-FIREWALL-001` |
| `execution_telemetry.runbook_id` | `L1-{CATEGORY}-{NUM}` | `L1-FIREWALL-001` |
| `patterns.pattern_id` | `PAT-{HASH}` | `PAT-a1b2c3d4` |
| `l1_rules.rule_id` | `L1-{TYPE}-{NUM}` | `L1-FIREWALL-001` |
| `sites.site_id` | tenant-scoped slug; alias-rename via `site_canonical_mapping` | `north-valley-branch-2` |
| `compliance_bundles.bundle_id` | UUID | bound to original `site_id` forever (Ed25519 + OTS) |

---

## Anchor-namespace convention for cryptographic chains (Session 216)

- **Client-org events** anchor at the org's primary `site_id` via
  `SELECT … FROM sites WHERE client_org_id=$1 ORDER BY created_at
  ASC LIMIT 1`; `client_org:<id>` synthetic fallback when no sites yet.
- **Partner-org events** anchor at `partner_org:<partner_id>` synthetic.
- **NEVER** use `canonical_site_id()` for these anchors — chain is
  immutable, mapping is read-only.

---

## Data Flow Diagrams

### Incident Healing Flow

```
Appliance detects issue
        │
        ▼
┌─────────────────┐
│ incidents table │◄── status: open, severity, incident_type
└────────┬────────┘
         │
         ▼
    L1 Rule Match?
    ┌────┴────┐
   YES       NO
    │         │
    ▼         ▼
 Execute   L2 LLM
 L1 Rule   Planning ──► record_l2_decision() ──► l2_decisions row
    │         │              │ (failure here forces escalate to L3)
    └────┬────┘              ▼
         ▼              l2_decision_recorded=true gate
┌─────────────────────────┐
│ execution_telemetry     │◄── runbook_id, resolution_level, success
└─────────────────────────┘
         │
         ▼
   incidents.status = resolved
   incidents.resolution_tier = L1/L2/L3 (gated on monitoring-only
                                          check → 'monitoring')
```

### Server-Initiated Commands Flow

```
Dashboard: "Execute runbook X on appliance Y"
        │
        ▼
┌─────────────────┐
│ orders table    │◄── status: pending, runbook_id, parameters
└────────┬────────┘
         │
    Appliance polls
         │
         ▼
    Execute runbook
         │
         ▼
┌─────────────────────────┐       ┌──────────────────────────────┐
│ execution_telemetry     │       │ POST /api/agent/orders/      │
└─────────────────────────┘       │ complete (mig 286)           │
                                  │  → orders.status=completed/  │
                                  │    failed                    │
                                  └──────────────────────────────┘
                                            │
                                            ▼
                                  sweep_stuck_orders() backstop
```

---

## Query Patterns (current — post-Session 220)

### Canonical compliance score (the customer-facing number)
```python
from compliance_score import compute_compliance_score
score = await compute_compliance_score(
    conn, site_ids=[site_id],
    include_incidents=False,
    window_days=30,  # default; auditor-export uses None for all-time
)
```
All three customer surfaces (`/api/client/dashboard`, `/api/client/
reports/current`, `/api/client/sites/{id}/compliance-health`)
delegate. NEVER inline `passed/total*100`.

### Canonical device count per site (post-mig-319)
```sql
SELECT site_id, COUNT(*) AS canonical_device_count
  FROM canonical_devices
 WHERE site_id = $1
 GROUP BY site_id;
```
`discovered_devices` aggregation is forbidden in customer-facing
surfaces (`test_no_raw_discovered_devices_count.py` BASELINE=0).
Use `canonical_devices` or annotate with `-- canonical-migration:
device_count_per_site` marker for operator-only/forensic reads.

### L2 success rate
```sql
SELECT * FROM compute_l2_success_rate(window_days := 30);
```

### Approximate row-counts on partitioned tables (Session 219 rule)
```sql
SELECT SUM(reltuples)::bigint AS approx_row_count
  FROM pg_class
 WHERE relname LIKE 'log_entries%'
   AND relkind IN ('r', 'p');
```

---

## Component Responsibilities

| Component | Writes To | Reads From |
|-----------|-----------|------------|
| Appliance Agent | `execution_telemetry`, `incidents`, `patterns`, `compliance_bundles`, `appliance_heartbeats` | `orders`, `appliance_runbook_config`, `l1_rules` (synced) |
| Substrate Engine | `substrate_violations` | All tables (per-assertion `admin_transaction`) |
| Dashboard API | `orders`, `admin_audit_log` | All tables |
| Learning Sync | `patterns`, `aggregated_pattern_stats`, `promoted_rules` | `execution_telemetry`, `l2_decisions` |
| Provisioning | `appliances`, `appliance_provisioning`, `appliance_provisions` (mig 296) | `sites` |
| Stripe Webhook | `client_orgs`, `stripe_subscriptions`, `sites`, `baa_signatures` (atomic-FK, mig 321) | — |
| BAA Enforcement | `admin_audit_log` (bypass events) | `baa_signatures`, `client_orgs` |
| Canonical Device Reconciler | `canonical_devices` | `discovered_devices` |

---

*Last Updated: 2026-05-16 (Session 220 close — canonical_devices, baa_signatures atomic-FK, substrate_violations.synthetic, D1 heartbeat verification, BAA enforcement triad)*
*Companion: `~/Downloads/OsirisCare_Owners_Manual_and_Auditor_Packet.pdf` + `docs/legal/MASTER_BAA_v1.0_INTERIM.md`*
