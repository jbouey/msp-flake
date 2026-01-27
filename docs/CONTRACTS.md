# API Contracts & Type Definitions

**Last Updated:** 2026-01-27
**Last Verified:** 2026-01-27

This document defines the type contracts between Python backend and TypeScript frontend.

---

## Quick Reference

| Layer | File | Types |
|-------|------|-------|
| Python Agent | `packages/compliance-agent/src/compliance_agent/_types.py` | Single source of truth |
| Python Agent Models | `packages/compliance-agent/src/compliance_agent/models.py` | Evidence bundles, MCP orders |
| Python Dashboard | `mcp-server/central-command/backend/models.py` | Dashboard API Pydantic |
| Python Database | `mcp-server/database/models.py` | SQLAlchemy ORM |
| TypeScript Frontend | `mcp-server/central-command/frontend/src/types/index.ts` | Frontend interfaces |
| gRPC Proto | `proto/compliance.proto` | Go agent communication |

---

## Core Enums

These enums must stay synchronized between Python and TypeScript.

### Severity

| Python (`_types.py`) | TypeScript (`types/index.ts`) | API Value |
|---------------------|------------------------------|-----------|
| `Severity.CRITICAL` | `'critical'` | `"critical"` |
| `Severity.HIGH` | `'high'` | `"high"` |
| `Severity.MEDIUM` | `'medium'` | `"medium"` |
| `Severity.LOW` | `'low'` | `"low"` |

### CheckType

| Python | TypeScript | Description |
|--------|------------|-------------|
| `PATCHING` | `'patching'` | OS/software patches |
| `ANTIVIRUS` | `'antivirus'` | AV software status |
| `BACKUP` | `'backup'` | Backup verification |
| `LOGGING` | `'logging'` | Audit log status |
| `FIREWALL` | `'firewall'` | Firewall configuration |
| `ENCRYPTION` | `'encryption'` | Disk/data encryption |
| `NETWORK` | `'network'` | Network security posture |
| `NTP_SYNC` | `'ntp_sync'` | Time synchronization |
| `CERTIFICATE_EXPIRY` | `'certificate_expiry'` | SSL certificate status |
| `DATABASE_CORRUPTION` | `'database_corruption'` | DB integrity |
| `MEMORY_PRESSURE` | `'memory_pressure'` | Memory usage |
| `WINDOWS_DEFENDER` | `'windows_defender'` | Defender status |
| `DISK_SPACE` | `'disk_space'` | Disk utilization |
| `SERVICE_HEALTH` | `'service_health'` | Critical services |
| `PROHIBITED_PORT` | `'prohibited_port'` | Port scanning |
| `WORKSTATION` | `'workstation'` | Workstation overall |
| `BITLOCKER` | `'bitlocker'` | BitLocker status |
| `DEFENDER` | `'defender'` | Defender (Go agent) |
| `PATCHES` | `'patches'` | Patches (Go agent) |
| `SCREEN_LOCK` | `'screen_lock'` | Screen lock policy |

### ResolutionLevel

| Python | TypeScript | Description |
|--------|------------|-------------|
| `L1` | `'L1'` | Deterministic rules (<100ms, $0) |
| `L2` | `'L2'` | LLM-assisted (2-5s, ~$0.001) |
| `L3` | `'L3'` | Human escalation |

### HealthStatus

| Python | TypeScript | Description |
|--------|------------|-------------|
| `HEALTHY` | `'healthy'` | Score > 80 |
| `WARNING` | `'warning'` | Score 60-80 |
| `CRITICAL` | `'critical'` | Score < 60 |

---

## Core Models

### Incident

```typescript
// TypeScript (frontend)
interface Incident {
  id: number;              // Changed from string in some contexts
  site_id: string;
  hostname: string;
  check_type: CheckType;
  severity: Severity;
  resolution_level?: ResolutionLevel;
  resolved: boolean;
  resolved_at?: string;    // ISO 8601
  hipaa_controls: string[];
  created_at: string;      // ISO 8601
}
```

```python
# Python (_types.py)
@dataclass
class Incident:
    incident_id: str = field(default_factory=generate_id)
    site_id: str = ""
    hostname: str = ""
    check_type: str = ""
    severity: str = "medium"
    status: str = "open"
    source: str = "drift_detection"
    details: Dict[str, Any] = field(default_factory=dict)
    hipaa_controls: List[str] = field(default_factory=list)
    resolution_level: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=now_utc)
```

**Note:** ID type mismatch - Python uses string UUIDs, some TypeScript expects numbers.
The API should always return string IDs.

### Health Metrics

```typescript
// TypeScript
interface HealthMetrics {
  connectivity: ConnectivityMetrics;
  compliance: ComplianceMetrics;
  overall: number;  // 0-100
  status: HealthStatus;
}

interface ConnectivityMetrics {
  checkin_freshness: number;
  healing_success_rate: number;
  order_execution_rate: number;
  score: number;
}

interface ComplianceMetrics {
  patching: number;
  antivirus: number;
  backup: number;
  logging: number;
  firewall: number;
  encryption: number;
  network: number;
  score: number;
}
```

```python
# Python (backend/models.py)
class HealthMetrics(BaseModel):
    connectivity: ConnectivityMetrics
    compliance: ComplianceMetrics
    overall: float = Field(ge=0, le=100)
    status: HealthStatus
```

### Appliance

```typescript
// TypeScript
interface Appliance {
  id: number;
  site_id: string;
  hostname: string;
  ip_address?: string;
  agent_version?: string;
  tier: string;
  is_online: boolean;
  last_checkin?: string;
  health?: HealthMetrics;
  created_at: string;
}
```

### Learning Loop Types

```typescript
// TypeScript
interface PromotionCandidate {
  id: string;
  pattern_signature: string;
  description: string;
  occurrences: number;
  success_rate: number;
  avg_resolution_time_ms: number;
  proposed_rule: string;
  first_seen: string;
  last_seen: string;
}

interface LearningStatus {
  total_l1_rules: number;
  total_l2_decisions_30d: number;
  patterns_awaiting_promotion: number;
  recently_promoted_count: number;
  promotion_success_rate: number;
  l1_resolution_rate: number;
  l2_resolution_rate: number;
}
```

---

## gRPC Protocol

The gRPC protocol is defined in `proto/compliance.proto` and used for communication between Go agents on Windows workstations and the Python compliance agent on the NixOS appliance.

### Key Messages

```protobuf
message DriftEvent {
  string agent_id = 1;
  string hostname = 2;
  string check_type = 3;
  bool passed = 4;
  string expected = 5;
  string actual = 6;
  string hipaa_control = 7;
  int64 timestamp = 8;
  map<string, string> metadata = 9;
}

message HealCommand {
  string command_id = 1;
  string check_type = 2;
  string action = 3;
  map<string, string> params = 4;
  int64 timeout_seconds = 5;
}

message HealingResult {
  string agent_id = 1;
  string hostname = 2;
  string check_type = 3;
  bool success = 4;
  string error_message = 5;
  int64 timestamp = 6;
  map<string, string> artifacts = 7;
  string command_id = 8;
}
```

### CapabilityTier Enum

```protobuf
enum CapabilityTier {
  MONITOR_ONLY = 0;      // Can only report drift
  SELF_HEAL = 1;         // Can attempt automated remediation
  FULL_REMEDIATION = 2;  // Can perform all remediations
}
```

---

## API Endpoints

### Dashboard API Base URL
- Production: `https://api.osiriscare.net`
- Local Dev: `http://localhost:8000`

### Key Endpoints

| Endpoint | Method | Request | Response |
|----------|--------|---------|----------|
| `/api/fleet/overview` | GET | - | `ClientOverview[]` |
| `/api/fleet/clients/{site_id}` | GET | - | `ClientDetail` |
| `/api/incidents` | GET | Query params | `Incident[]` |
| `/api/incidents/{id}` | GET | - | `IncidentDetail` |
| `/api/learning/status` | GET | - | `LearningStatus` |
| `/api/learning/candidates` | GET | - | `PromotionCandidate[]` |
| `/api/partners/me/learning/stats` | GET | - | Partner learning stats |

### Date/Time Format

All datetime fields use ISO 8601 format: `"2026-01-27T15:30:00Z"`

---

## Validation Rules

### Site ID
- Format: lowercase alphanumeric with hyphens
- Pattern: `^[a-z0-9][a-z0-9-]*[a-z0-9]$`
- Length: 3-50 characters

### Host ID
- Format: lowercase alphanumeric with hyphens
- Pattern: Same as Site ID

### HIPAA Control Citations
- Format: `§164.xxx(x)(x)(x)` or `164.xxx(x)(x)(x)`
- Example: `§164.312(a)(2)(iv)`

---

## Type Checking

### Python
```bash
cd packages/compliance-agent
source venv/bin/activate
mypy src/compliance_agent/_types.py src/compliance_agent/models.py --config-file pyproject.toml
```

### TypeScript
```bash
cd mcp-server/central-command/frontend
npx tsc --noEmit
```

### Full Stack
```bash
./scripts/validate-types.sh
```

---

## Known Divergences

| Issue | Python | TypeScript | Status |
|-------|--------|------------|--------|
| Incident.id type | `str` (UUID) | `number` or `string` | API uses string |
| Appliance.id type | `str` | `number` | API uses string |
| Go agent types | N/A | Defined | Frontend-only for display |

These divergences are intentional or documented. The API layer handles any necessary conversions.

---

## Adding New Types

1. **Python**: Add to `_types.py` (dataclass or enum)
2. **Pydantic**: Add to `models.py` if needed for API validation
3. **TypeScript**: Add to `types/index.ts`
4. **Proto**: Add to `proto/compliance.proto` if for gRPC
5. **Update this document**
6. **Run `./scripts/validate-types.sh`**
