# Data Model Documentation

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

### CRITICAL: Runbook ID Mismatch

**Current State (BROKEN):**
```
execution_telemetry.runbook_id: L1-FIREWALL-001, L1-AUDIT-001, L1-LIN-SSH-001
runbooks.runbook_id:           RB-WIN-SVC-001, RB-WIN-SEC-001, RB-LIN-ACCT-001
```

These DO NOT match. Joins fail silently, causing 0 execution counts per runbook.

**Root Cause:** Agent uses L1 rule IDs from its local rule engine, but runbooks table uses a different naming scheme.

**Recommended Fix:** Create a mapping table or update agent to log the canonical runbook_id.

### Naming Conventions by Table

| Table | ID Format | Example |
|-------|-----------|---------|
| `runbooks.runbook_id` | `RB-{PLATFORM}-{CATEGORY}-{NUM}` | `RB-WIN-SEC-001` |
| `execution_telemetry.runbook_id` | `L1-{CATEGORY}-{NUM}` | `L1-FIREWALL-001` |
| `patterns.pattern_id` | `PAT-{HASH}` | `PAT-a1b2c3d4` |
| `l1_rules.rule_id` | `L1-{TYPE}-{NUM}` | `L1-FIREWALL-001` |

---

## Resolution Tier Tracking

### CRITICAL: L2 Decisions Split Across Tables

**Data Distribution:**
```sql
-- incidents.resolution_tier
L1: 1,089 records
L3: 442 records
L2: 0 records (!)

-- execution_telemetry.resolution_level
L1: 6,555 records
L2: 911 records
L3: 1 record
```

**Finding:** L2 decisions are ONLY recorded in `execution_telemetry`, not in `incidents`.

**Canonical Source:** `execution_telemetry.resolution_level` for all resolution tier metrics.

**Recommended Fix:** Either:
1. Update agent to also set `incidents.resolution_tier` when L2 is used
2. Always query `execution_telemetry` for resolution metrics (current workaround)

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

## Known Issues & Workarounds

### Issue 1: Runbook ID Mismatch
- **Tables:** `execution_telemetry` vs `runbooks`
- **Impact:** Per-runbook execution counts show 0
- **Workaround:** Dashboard shows total telemetry stats, not per-runbook
- **Fix Needed:** Create `runbook_id_mapping` table or update agent

### Issue 2: L2 Decisions Not in incidents
- **Tables:** `incidents.resolution_tier` missing L2 values
- **Impact:** Dashboard L2 count was 0
- **Workaround:** UNION query on both tables
- **Fix Needed:** Agent should set `incidents.resolution_tier` for all resolutions

### Issue 3: Orders Stay Pending
- **Tables:** `orders.status` never updated to completed
- **Impact:** Order-based metrics show 0% completion
- **Workaround:** Use `execution_telemetry` for execution metrics
- **Fix Needed:** Agent should update order status after execution

---

## Recommended Schema Changes

### 1. Add Runbook ID Mapping Table

```sql
CREATE TABLE runbook_id_mapping (
    l1_rule_id VARCHAR(100) PRIMARY KEY,     -- L1-FIREWALL-001
    runbook_id VARCHAR(100) NOT NULL,        -- RB-WIN-SEC-001
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (runbook_id) REFERENCES runbooks(runbook_id)
);
```

### 2. Add Trigger to Sync incidents.resolution_tier

```sql
CREATE OR REPLACE FUNCTION sync_incident_resolution_tier()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE incidents
    SET resolution_tier = NEW.resolution_level
    WHERE id::text = NEW.incident_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER sync_resolution_tier
AFTER INSERT ON execution_telemetry
FOR EACH ROW EXECUTE FUNCTION sync_incident_resolution_tier();
```

---

## Query Patterns

### Get L2 Decisions (Correct Way)
```sql
-- Query BOTH tables with UNION
SELECT tier, COUNT(*) FROM (
    SELECT resolution_tier as tier FROM incidents
    WHERE resolution_tier IS NOT NULL
    UNION ALL
    SELECT resolution_level as tier FROM execution_telemetry
    WHERE resolution_level IS NOT NULL
) combined
GROUP BY tier;
```

### Get Runbook Executions (Workaround)
```sql
-- Until ID mapping is fixed, get totals from telemetry
SELECT
    COUNT(*) as total_executions,
    COUNT(*) FILTER (WHERE success = true) as successful,
    AVG(duration_seconds) as avg_duration
FROM execution_telemetry;
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

*Last Updated: 2026-01-31*
*Session: 80 - Technical Debt Cleanup*
