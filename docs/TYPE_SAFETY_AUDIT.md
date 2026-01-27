# Type Safety Audit Report

**Audit Date:** 2026-01-27
**Auditor:** Claude Opus 4.5
**Scope:** Full-stack type analysis (Python backend, TypeScript frontend, gRPC proto)

---

## Executive Summary

| Category | Status | Severity |
|----------|--------|----------|
| Python Static Analysis | NOT CONFIGURED | Medium |
| TypeScript Static Analysis | PASSING | OK |
| OpenAPI Schema | DISABLED | High |
| Proto Files | OUT OF SYNC | High |
| Python/TypeScript Contract | DRIFTED | High |

**Overall Assessment:** Significant type safety gaps exist across the stack. OpenAPI is explicitly disabled preventing runtime contract validation. Proto files are 15 lines out of sync with HealCommand features missing from agent proto. Multiple ID type mismatches between Python (str) and TypeScript (number).

---

## 1. Python Backend Analysis

### 1.1 Static Type Checking (mypy)

**Status:** NOT INSTALLED

```bash
# packages/compliance-agent/venv
$ which mypy
mypy not found

# Global
$ pip3 show mypy
mypy not installed globally
```

**Issue:** No mypy installation in either the compliance-agent venv or globally. Python type hints exist but are not validated at build time.

**Recommendation:** Add mypy to requirements-dev.txt and configure in pyproject.toml:
```toml
[tool.mypy]
python_version = "3.13"
strict = true
ignore_missing_imports = true
```

### 1.2 Pydantic Models Coverage

**Location:** Three separate model files (potential duplication)

| File | Purpose | Line Count |
|------|---------|------------|
| `packages/compliance-agent/src/compliance_agent/models.py` | Agent-side evidence bundles | 482 |
| `mcp-server/database/models.py` | SQLAlchemy ORM | 383 |
| `mcp-server/central-command/backend/models.py` | Dashboard API Pydantic | 513 |

**Issue:** Type definitions are spread across three files with no shared source of truth. The CLAUDE.md references a `_types.py` single source of truth that does not exist.

**Missing `_types.py`:** The file referenced in CLAUDE.md at `packages/compliance-agent/src/compliance_agent/_types.py` does not exist despite documentation claiming it should be the single source of truth.

---

## 2. TypeScript Frontend Analysis

### 2.1 Static Type Checking (tsc)

**Status:** PASSING (no errors)

```bash
$ cd mcp-server/central-command/frontend
$ ./node_modules/.bin/tsc --noEmit
# Exits cleanly with no output
```

**TypeScript Version:** 5.2.2

### 2.2 Type Definitions

**Location:** `frontend/src/types/index.ts` (610 lines)

Well-structured with proper enums and interfaces:
- 15 enum/type definitions
- 35+ interface definitions
- Proper optional field handling
- Good JSDoc-style comments

---

## 3. OpenAPI Contract Status

### 3.1 Configuration

**Status:** EXPLICITLY DISABLED

```python
# mcp-server/main.py:410
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None,
    title="MCP Server",
    ...
)
```

**Impact:**
- No `/docs` Swagger UI
- No `/redoc` ReDoc UI
- No `/openapi.json` schema endpoint
- Cannot generate TypeScript clients from OpenAPI spec
- No runtime contract validation

**Recommendation:** Re-enable OpenAPI for development/staging:
```python
app = FastAPI(
    docs_url="/docs" if DEBUG else None,
    openapi_url="/openapi.json" if DEBUG else None,
    ...
)
```

---

## 4. gRPC Proto Analysis

### 4.1 Proto File Locations

| Location | Lines | Purpose |
|----------|-------|---------|
| `/proto/compliance.proto` | 140 | Canonical proto (NixOS appliance) |
| `/agent/proto/compliance.proto` | 125 | Go agent proto |

### 4.2 Drift Analysis

**Files are OUT OF SYNC (15 lines difference)**

Key differences in `/proto/compliance.proto` NOT in `/agent/proto/compliance.proto`:

```protobuf
// Missing from agent/proto:

// HealCommand message (lines 79-87)
message HealCommand {
  string command_id = 1;
  string check_type = 2;
  string action = 3;
  map<string, string> params = 4;
  int64 timeout_seconds = 5;
}

// DriftAck field (line 76)
HealCommand heal_command = 4;  // Optional heal command

// HealingResult field (line 97)
string command_id = 8;  // Response to HealCommand

// HeartbeatResponse field (line 118)
repeated HealCommand pending_commands = 3;
```

**Impact:** Go agents cannot receive or respond to HealCommand messages, preventing immediate healing response flow.

**Recommendation:** Sync proto files and regenerate Go/Python stubs:
```bash
cp proto/compliance.proto agent/proto/compliance.proto
cd agent && protoc --go_out=. --go-grpc_out=. proto/compliance.proto
```

---

## 5. Python/TypeScript Contract Drift

### 5.1 Enum Mismatches

**CheckType Enum:**

| Value | Python backend | TypeScript frontend |
|-------|----------------|---------------------|
| patching | YES | YES |
| antivirus | YES | YES |
| backup | YES | YES |
| logging | YES | YES |
| firewall | YES | YES |
| encryption | YES | YES |
| network | YES | YES |
| ntp_sync | YES | YES |
| certificate_expiry | YES | YES |
| database_corruption | YES | YES |
| memory_pressure | YES | YES |
| windows_defender | YES | YES |
| disk_space | YES | YES |
| service_health | YES | YES |
| **workstation** | NO | YES |
| **bitlocker** | NO | YES |
| **defender** | NO | YES |
| **patches** | NO | YES |
| **screen_lock** | NO | YES |
| **prohibited_port** | NO | YES |

**Issue:** 6 check types exist only in TypeScript, not in Python CheckType enum.

### 5.2 ID Type Mismatches

| Model | Python Type | TypeScript Type | Status |
|-------|-------------|-----------------|--------|
| Incident.id | `str` | `number` | MISMATCH |
| Appliance.id | `str` | `number` | MISMATCH |
| PromotionCandidate.id | `str` | `string` | OK |
| Runbook.id | `str` | `string` | OK |

**Impact:** Runtime serialization issues when API returns string IDs but frontend expects numbers.

### 5.3 Missing TypeScript Models in Python

The following TypeScript types have no Python backend equivalent:

- `GoAgent` / `GoAgentCheckResult` / `SiteGoAgentSummary`
- `Workstation` / `WorkstationCheckResult` / `SiteWorkstationSummary`
- `ComplianceEvent` (events vs incidents distinction)

These may be intentional (client-side only) or missing backend implementations.

---

## 6. Recommendations

### High Priority

1. **Sync Proto Files** - Copy canonical proto to agent directory, regenerate stubs
2. **Fix ID Type Mismatches** - Standardize on string UUIDs throughout
3. **Add Missing CheckTypes to Python** - Add workstation check types to backend enum

### Medium Priority

4. **Create Shared _types.py** - Implement the documented single source of truth
5. **Install mypy** - Add to dev dependencies, configure strict mode
6. **Re-enable OpenAPI** - At least for development/staging environments

### Low Priority

7. **Generate TypeScript Client** - Use openapi-typescript-codegen from OpenAPI spec
8. **Add Contract Tests** - Integration tests that validate Python/TS type alignment

---

## 7. Type Definition Inventory

### Python Files with Types

```
packages/compliance-agent/src/compliance_agent/
├── models.py              # Evidence, MCP orders, drift, remediation
├── config.py              # AgentConfig pydantic model
├── drift.py               # DriftCheckResult types
├── incident_db.py         # IncidentRecord types
└── learning_loop.py       # Pattern/promotion types

mcp-server/
├── database/models.py     # SQLAlchemy ORM models
└── central-command/backend/
    └── models.py          # Dashboard API Pydantic models
```

### TypeScript Files with Types

```
mcp-server/central-command/frontend/src/
├── types/index.ts         # All shared types (610 lines)
└── utils/api.ts           # API client types
```

### Proto Files

```
proto/compliance.proto     # Canonical (140 lines)
agent/proto/compliance.proto  # Go agent (125 lines) - OUT OF SYNC
```

---

## Appendix: Raw Findings

### A. Proto Diff Output

```diff
69d68
< // Can optionally include a heal command for the agent to execute immediately
74,76d72
<
<   // Optional heal command - if set, agent should execute this heal
<   HealCommand heal_command = 4;
79,88c75
< // HealCommand tells Go agent to execute a specific remediation
< message HealCommand {
<   string command_id = 1;          // Unique ID for tracking
<   string check_type = 2;          // firewall, defender, bitlocker, screenlock, patches
<   string action = 3;              // enable, start, configure, etc.
<   map<string, string> params = 4; // Action-specific parameters
<   int64 timeout_seconds = 5;      // Max execution time (default 60)
< }
<
< // HealingResult reports the outcome of self-healing or commanded healing
---
> // HealingResult reports the outcome of self-healing
97d83
<   string command_id = 8;              // If responding to HealCommand, include its ID
114c100
< // HeartbeatResponse can signal config changes and pending commands
---
> // HeartbeatResponse can signal config changes
117,118c103
<   bool config_changed = 2;           // If true, agent should re-register
<   repeated HealCommand pending_commands = 3;  // Commands queued for this agent
---
>   bool config_changed = 2;    // If true, agent should re-register
```

### B. OpenAPI Disabled Evidence

```python
# mcp-server/main.py line 410
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None,
    title="MCP Server",
    description="MSP Compliance Platform - Central Control Plane",
    version="1.0.0",
    lifespan=lifespan
)
```

---

*Generated by Type Safety Audit - 2026-01-27*
