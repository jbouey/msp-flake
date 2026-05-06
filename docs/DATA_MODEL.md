# Data Model Documentation

> **Last verified:** 2026-05-06 (RT-DM data-model audit cycle).
> **Three CRITICAL issues this doc previously called out have all
> been resolved in commit `4619bb07`** (migrations 284, 285, 286 +
> 3 substrate invariants + 16-check CI gate). See the resolved-
> with-context blocks below.
>
> **Canonical current-state authority:** `OsirisCare_Owners_Manual_
> and_Auditor_Packet.pdf` (in `~/Downloads/`, generated 2026-05-06).
> Where this doc and the packet disagree, the packet wins.

## Overview

This document describes the central database schema, data flows, and naming conventions to prevent future mismatches between components.

---

## Core Tables

### Execution & Healing

| Table | Purpose | Writer | Key Fields |
|-------|---------|--------|------------|
| `execution_telemetry` | Agent execution logs (L1/L2/L3 healing) | Appliance Agent | `runbook_id`, `resolution_level`, `success` |
| `incidents` | Incident tracking & lifecycle | Appliance Agent, Dashboard | `resolution_tier`, `status`, `severity` |
| `orders` | Server-initiated commands | Dashboard/API | `runbook_id`, `status`, `result` |

### Runbooks & Rules

| Table | Purpose | Writer | Key Fields |
|-------|---------|--------|------------|
| `runbooks` | Runbook definitions (HIPAA mappings) | Seed data, Admin | `runbook_id`, `category`, `steps` |
| `l1_rules` | Deterministic L1 rules | Learning promotion | `rule_id`, `pattern_signature` |
| `patterns` | Learned patterns awaiting promotion | Agent sync | `pattern_signature`, `status`, `success_rate` |

### Appliances & Sites

| Table | Purpose | Writer | Key Fields |
|-------|---------|--------|------------|
| `appliances` | Registered appliances | Provisioning | `id`, `site_id`, `last_checkin` |
| `sites` | Client sites | Admin | `id`, `name`, `partner_id` |
| `appliance_runbook_config` | Per-appliance runbook settings | Admin | `appliance_id`, `runbook_id`, `enabled` |

---

## ID Naming Conventions

### Runbook ID Mismatch — RESOLVED 2026-05-06 (mig 284)

> **Historical context (from 2026-01-31 capture):** at the time
> this doc was written, `execution_telemetry.runbook_id` (L1-*
> form) and `runbooks.runbook_id` (RB-*/LIN-* form) did not
> match. JOINs failed; per-runbook execution counts showed 0.

**Resolution shipped 2026-05-06 (RT-DM Issue #1):** migration 284
adds `runbooks.agent_runbook_id TEXT UNIQUE` bridge column +
backfills the mapping for known L1-* IDs + INSERTs placeholder
runbook rows for orphan L1-* IDs so EVERY agent-emitted
`runbook_id` matches a row via the bridge column. Dashboard
queries JOIN on either column. Substrate invariant
`unbridged_telemetry_runbook_ids` (sev2) catches future drift.

```sql
-- Current per-runbook execution count (works as of mig 284):
SELECT r.runbook_id,
       r.agent_runbook_id,
       COUNT(*) AS executions_7d
  FROM execution_telemetry et
  JOIN runbooks r
    ON r.agent_runbook_id = et.runbook_id
    OR r.runbook_id = et.runbook_id
 WHERE et.created_at > NOW() - INTERVAL '7 days'
 GROUP BY r.runbook_id, r.agent_runbook_id
 ORDER BY executions_7d DESC;
```

### Naming Conventions by Table

| Table | ID Format | Example |
|-------|-----------|---------|
| `runbooks.runbook_id` | `RB-{PLATFORM}-{CATEGORY}-{NUM}` | `RB-WIN-SEC-001` |
| `execution_telemetry.runbook_id` | `L1-{CATEGORY}-{NUM}` | `L1-FIREWALL-001` |
| `patterns.pattern_id` | `PAT-{HASH}` | `PAT-a1b2c3d4` |
| `l1_rules.rule_id` | `L1-{TYPE}-{NUM}` | `L1-FIREWALL-001` |

---

## Resolution Tier Tracking

### L2 Decisions Tracking — RESOLVED 2026-05-06 (mig 285)

> **Historical context (2026-01-31):** the original finding was
> that `incidents.resolution_tier` showed 0 L2 records while
> `execution_telemetry.resolution_level` showed 911. The framing
> ("missing L2 values") was incomplete: L2 was actually allowed
> in the CHECK constraint (mig 106) and `agent_api.py` writes
> tier='L2' on the resolution path. The real gap was: no
> canonical SQL view JOINing `l2_decisions` to `incidents`, so
> dashboards consuming one table without the other saw partial
> truth.

**Resolution shipped 2026-05-06 (RT-DM Issue #2):** migration 285
ships `v_l2_outcomes` view (LEFT JOIN `l2_decisions` to
`incidents`) + `compute_l2_success_rate(window_days)` SQL
function. Dashboards consume the canonical view. Underlying
tables stay intact. Substrate invariant
`l2_resolution_without_decision_record` (sev2) catches gaps
where an incident has tier='L2' but no `l2_decisions` row
references it.

```sql
-- Current canonical L2 success rate (works as of mig 285):
SELECT * FROM compute_l2_success_rate(window_days := 30);
```

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
 L1 Rule   Planning
    │         │
    └────┬────┘
         │
         ▼
┌─────────────────────────┐
│ execution_telemetry     │◄── runbook_id, resolution_level, success
└─────────────────────────┘
         │
         ▼
   incidents.status = resolved
   incidents.resolution_tier = L1/L2 (SHOULD be set, often missing)
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
┌─────────────────────────┐
│ execution_telemetry     │◄── execution results
└─────────────────────────┘
         │
         ▼
   orders.status = completed (SHOULD be updated, often stuck at pending)
   orders.result = {...}
```

---

## Table Schemas

### execution_telemetry

Primary table for all agent execution data.

```sql
CREATE TABLE execution_telemetry (
    id SERIAL PRIMARY KEY,
    execution_id VARCHAR(255) UNIQUE NOT NULL,
    incident_id VARCHAR(255),
    site_id VARCHAR(255) NOT NULL,
    appliance_id VARCHAR(255) NOT NULL,
    runbook_id VARCHAR(255) NOT NULL,        -- Uses L1-* format
    hostname VARCHAR(255) NOT NULL,
    platform VARCHAR(50),
    incident_type VARCHAR(100),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_seconds DOUBLE PRECISION,
    success BOOLEAN NOT NULL,
    resolution_level VARCHAR(10),            -- L1, L2, or L3
    state_before JSONB DEFAULT '{}',
    state_after JSONB DEFAULT '{}',
    executed_steps JSONB DEFAULT '[]',
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### incidents

Incident lifecycle tracking.

```sql
CREATE TABLE incidents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    appliance_id UUID NOT NULL REFERENCES appliances(id),
    incident_type VARCHAR(100) NOT NULL,
    severity VARCHAR(50) NOT NULL,           -- low, medium, high, critical
    check_type VARCHAR(100),
    details JSONB NOT NULL DEFAULT '{}',
    resolution_tier VARCHAR(10),             -- L1, L2, L3 (often NULL or missing L2)
    order_id UUID REFERENCES orders(id),
    status VARCHAR(50) NOT NULL DEFAULT 'open',
    reported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
```

### orders

Server-to-appliance command queue.

```sql
CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id VARCHAR(64) UNIQUE NOT NULL,
    appliance_id UUID NOT NULL REFERENCES appliances(id),
    runbook_id VARCHAR(100) NOT NULL,        -- Uses RB-* format
    parameters JSONB NOT NULL DEFAULT '{}',
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    result JSONB
);
```

### runbooks

Runbook definitions with HIPAA control mappings.

```sql
CREATE TABLE runbooks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    runbook_id VARCHAR(100) UNIQUE NOT NULL, -- Uses RB-* format
    name VARCHAR(255) NOT NULL,
    description TEXT,
    category VARCHAR(100) NOT NULL,
    severity VARCHAR(50) NOT NULL,
    hipaa_controls TEXT[],
    steps JSONB NOT NULL DEFAULT '[]',
    enabled BOOLEAN NOT NULL DEFAULT true,
    is_disruptive BOOLEAN DEFAULT false
);
```

---

## Known Issues & Workarounds — ALL THREE RESOLVED 2026-05-06

> **Note (2026-05-06):** the three issues catalogued below were
> reported by an outside auditor and ALL THREE shipped fixes in
> commit 4619bb07. This section is preserved for historical
> reference; the current state has each issue closed at the
> schema layer + application layer + substrate-invariant layer
> + CI-gate layer. See `docs/lessons/sessions-218.md` and
> `.agent/plans/RT-DM-data-model-audit-2026-05-06.md` (when
> written) for the full round-table + Maya 2nd-eye record.

### Issue 1: Runbook ID Mismatch — RESOLVED (mig 284)
- **Tables:** `execution_telemetry` vs `runbooks`
- **Resolution:** `runbooks.agent_runbook_id` bridge column +
  backfilled mapping for L1-* → LIN-*/RB-* + placeholder rows
  for orphan L1-* IDs (so EVERY agent-emitted runbook_id matches
  a row via the bridge).
- **Drift catcher:** `unbridged_telemetry_runbook_ids` substrate
  invariant (sev2).
- **CI gate:** `tests/test_data_model_audit_contract.py`
  asserts the bridge column + mappings + placeholders exist.

### Issue 2: L2 Decisions Tracking — RESOLVED (mig 285)
- **Tables:** `l2_decisions` + `incidents.resolution_tier`
- **Resolution:** `v_l2_outcomes` view JOINs the two;
  `compute_l2_success_rate(window_days)` SQL function is THE
  canonical L2 metric source. Dashboards consume the view.
- **Drift catcher:** `l2_resolution_without_decision_record`
  substrate invariant (sev2).
- **CI gate:** asserts the view + function + JOIN-correctness.

### Issue 3: Orders Stay Pending — RESOLVED (mig 286)
- **Tables:** `orders.status`
- **Resolution:** initial round-table proposed a DB trigger reading
  `execution_telemetry.metadata->>'order_id'`; **Maya 2nd-eye on
  the SHIPPED fix found `execution_telemetry` has NO metadata
  column** → trigger would silently no-op forever. Redesigned:
  - **Explicit `/api/agent/orders/complete` endpoint** as primary
    completion path. Idempotent; failure-path-aware (success=false
    → status='failed' with error_message).
  - **`sweep_stuck_orders()` SQL function** as backstop for orders
    that ack'd but never complete.
  - **`execution_telemetry.order_id` column** added forward-compat
    for when the agent emits it.
- **Drift catcher:** `orders_stuck_acknowledged` substrate
  invariant (sev2).
- **CI gate:** asserts the endpoint exists + is auth-gated +
  idempotent + handles BOTH success and failure paths.

---

## Schema changes that landed (2026-05-06 RT-DM cycle)

The "Recommended Schema Changes" section that previously lived
here proposed a `runbook_id_mapping` table + a sync trigger. The
final shipped fixes diverged from those recommendations after
the round-table:

- **Instead of a separate `runbook_id_mapping` table** (mig 284):
  added `runbooks.agent_runbook_id TEXT UNIQUE` column +
  backfilled mapping. One canonical table, one bridge column,
  no separate JOIN object.
- **Instead of a `sync_incident_resolution_tier` trigger**
  (mig 285): exposed the canonical SQL through a `v_l2_outcomes`
  view + `compute_l2_success_rate()` function. The L2 truth was
  never actually missing from `incidents` — it was already
  written by `agent_api.py`. The actual gap was the lack of a
  canonical JOIN-aware metric source. The view ships that;
  underlying tables stay intact.
- **The orders.status fix** (mig 286) used neither approach —
  Maya's post-fix 2nd-eye rejected the trigger-based design
  because `execution_telemetry` has no `metadata` column to
  correlate on. Replaced with explicit
  `/api/agent/orders/complete` endpoint + `sweep_stuck_orders()`
  function backstop.

---

## Query Patterns (current — post-2026-05-06)

### Get L2 success rate (canonical)
```sql
SELECT * FROM compute_l2_success_rate(window_days := 30);
-- Returns: decision_count, success_count, success_rate
```

### Get runbook execution count by canonical runbook
```sql
SELECT r.runbook_id,
       r.agent_runbook_id,
       r.name,
       COUNT(*) AS executions_7d
  FROM execution_telemetry et
  JOIN runbooks r
    ON r.agent_runbook_id = et.runbook_id
    OR r.runbook_id = et.runbook_id
 WHERE et.created_at > NOW() - INTERVAL '7 days'
 GROUP BY r.runbook_id, r.agent_runbook_id, r.name
 ORDER BY executions_7d DESC;
```

### Get order completion stats
```sql
SELECT status, COUNT(*) AS count
  FROM orders
 WHERE created_at > NOW() - INTERVAL '7 days'
 GROUP BY status
 ORDER BY count DESC;
-- Pre-mig-286 this was always 100% 'pending' or 'acknowledged'.
-- Post-mig-286 + agent calling /orders/complete: completed/failed appear.
```

---

## Component Responsibilities

| Component | Writes To | Reads From |
|-----------|-----------|------------|
| Appliance Agent | `execution_telemetry`, `incidents`, `patterns` | `orders`, `appliance_runbook_config` |
| Dashboard API | `orders`, `admin_audit_log` | All tables |
| Learning Sync | `patterns`, `aggregated_pattern_stats` | `execution_telemetry` |
| Provisioning | `appliances`, `appliance_provisions` | `sites` |

---

*Last Updated: 2026-05-06 (RT-DM data-model audit cycle close)*
*Original: Session 80 - Technical Debt Cleanup; refreshed Session 218 RT-DM*
*Companion: `~/Downloads/OsirisCare_Owners_Manual_and_Auditor_Packet.pdf`*
