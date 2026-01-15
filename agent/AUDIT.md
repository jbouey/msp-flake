# Go Agent Implementation - Code Audit

**Date:** 2026-01-15
**Purpose:** Document existing architecture patterns before implementing Go agent

---

## Task 0.1: Appliance Agent (appliance_agent.py)

### Main Loop Structure
Location: `packages/compliance-agent/src/compliance_agent/appliance_agent.py`

```
start() → initialize components → main loop
    ↓
_run_cycle() executes one cycle:
  1. Phone-home checkin to Central Command
  2. Drift detection and evidence upload
  3. L1 rules sync (hourly)
  4. Windows device scans (every 5 min via WinRM polling)
  5. Linux device scans (every 5 min)
  6. Network posture scan (every 10 min)
  7. Workstation compliance scan (every 10 min)
  8. Process pending orders
```

### Key Patterns
- Uses FastAPI + uvicorn for sensor API on port 8080
- Three-tier healing: L1 deterministic → L2 LLM → L3 human
- Evidence deduplication with hourly heartbeat (reduces storage 99%)
- Dual-mode architecture: WinRM polling + sensor push

### Relevant Code for Go Agent Integration
```python
# Sensor API configuration (line 397-412)
configure_sensor_healing(
    auto_healer=self.auto_healer,
    windows_targets=self.windows_targets,
    incident_db=self.incident_db,
    config=self.config
)

# Sensor server runs on port 8080 (line 534)
port=self._sensor_port  # Default 8080
```

---

## Task 0.2: Sensor API (sensor_api.py)

### Endpoints
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/sensor/heartbeat` | POST | Receive heartbeats, update registry |
| `/api/sensor/drift` | POST | Receive drift events, queue for healing |
| `/api/sensor/resolved` | POST | Mark drifts as resolved |
| `/api/sensor/status` | GET | Return sensor health |
| `/api/sensor/hosts/polling` | GET | Return hosts needing WinRM polling |

### Data Models (Pydantic)
```python
class SensorHeartbeat:
    hostname: str
    domain: Optional[str]
    sensor_version: str
    timestamp: str
    drift_count: int
    has_critical: bool
    compliant: bool
    uptime_seconds: Optional[int]
    mode: str = "sensor"

class SensorDriftEvent:
    hostname: str
    domain: Optional[str]
    drift_type: str
    severity: str  # critical, high, medium, low
    details: Dict[str, Any]
    check_id: Optional[str]
    detected_at: str
    sensor_version: Optional[str]
```

### Drift Handling Flow
```
SensorDriftEvent → handle_sensor_drift() → convert to Incident
    → auto_healer.heal(incident) → WinRM remediation
    → evidence_generator.create_bundle()
```

### Pattern for Go Agent
The Go agent should mirror this REST API initially, then add gRPC for:
- Persistent connections (reduce HTTP overhead)
- Bidirectional streaming (config push)
- TLS mutual auth (better security)

---

## Task 0.3: PowerShell Sensor (OsirisSensor.ps1)

### 12 Compliance Checks
| # | Check | Drift Type | Severity | Check ID |
|---|-------|------------|----------|----------|
| 1 | Firewall | firewall_disabled | critical | RB-WIN-FIREWALL-001 |
| 2 | Windows Defender | defender_stopped | critical | RB-WIN-AV-001 |
| 3 | Print Spooler | spooler_running | high | RB-WIN-SVC-001 |
| 4 | Critical Services | critical_service_stopped | critical/high | RB-WIN-SVC-002 |
| 5 | Disk Space | low_disk_space | critical/high | RB-WIN-STOR-001 |
| 6 | Guest Account | guest_account_enabled | high | RB-WIN-SEC-002 |
| 7 | SMBv1 | smbv1_enabled | critical | RB-WIN-SEC-004 |
| 8 | Pending Reboot | pending_reboot | medium | RB-WIN-UPD-002 |
| 9 | Audit Policy | audit_policy_disabled | high | RB-WIN-SEC-005 |
| 10 | Time Sync | time_sync_failed | high | RB-WIN-NET-002 |
| 11 | Password Policy | weak_password_policy | medium | RB-WIN-SEC-003 |
| 12 | Account Lockout | no_account_lockout | medium | RB-WIN-SEC-006 |

### Limitations Addressed by Go Agent
1. **HTTP only** → Go agent uses gRPC with mTLS
2. **No persistence** → Go agent has SQLite offline queue
3. **Polling-based heartbeat** → Go agent uses streaming
4. **PowerShell dependency** → Go is cross-compiled static binary
5. **No RMM detection** → Go agent adds strategic intelligence

### HTTP POST Format (to preserve compatibility)
```json
// Drift Event
{
    "hostname": "WORKSTATION01",
    "domain": "NORTHVALLEY.LOCAL",
    "drift_type": "firewall_disabled",
    "severity": "critical",
    "details": {"disabled_profiles": ["Domain", "Private"]},
    "check_id": "RB-WIN-FIREWALL-001",
    "detected_at": "2026-01-15T10:30:00Z",
    "sensor_version": "1.0.0"
}
```

---

## Task 0.4: Evidence Generation (evidence.py)

### Bundle Structure
```
/var/lib/compliance-agent/evidence/YYYY/MM/DD/<uuid>/
├── bundle.json      # Full evidence data
├── bundle.sig       # Ed25519 detached signature
└── bundle.ots       # OpenTimestamps proof (optional)
```

### Evidence Fields
```python
create_evidence(
    check: str,              # Check type (patching, backup, etc.)
    outcome: str,            # success, failed, reverted, deferred, alert
    pre_state: Dict,         # System state before action
    post_state: Dict,        # System state after action
    actions: List,           # Actions taken
    hipaa_controls: List,    # HIPAA control citations
    ntp_verification: Dict,  # Multi-source NTP verification
    ...
)
```

### Ed25519 Signing Flow
```
ensure_signing_key() → generates key if not exists
    ↓
Ed25519Signer(key_path) → loads private key
    ↓
signer.sign(data) → returns signature
    ↓
Public key logged at startup for verification
```

### Go Agent Drift → Evidence Flow
```
Go Agent DriftEvent
    ↓
gRPC to Appliance (ReportDrift)
    ↓
ComplianceAgentServicer._route_drift_to_healing()
    ↓
Convert to Incident model
    ↓
auto_healer.heal(incident)
    ↓
evidence_generator.create_bundle(...)
```

---

## Task 0.5: WindowsExecutor (executor.py)

### WinRM Session Patterns
```python
WindowsTarget:
    hostname: str
    port: int = 5986      # HTTPS default
    username: str
    password: str
    use_ssl: bool = True
    verify_ssl: bool = True
    transport: str = "ntlm"  # ntlm, kerberos, certificate
```

### Session Caching
- Sessions cached by hostname
- Max age: 300 seconds (5 minutes)
- Invalidated on connection errors

### Retry Pattern
- Default 2 retries
- Exponential backoff (1.5x multiplier)
- Initial delay: 30 seconds
- On connection error: invalidate session + retry

### Execution Flow
```python
execute_script():
    for attempt in range(retries + 1):
        try:
            full_script = f"{PS_HELPERS}\n\n{script}"
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._execute_sync, target, full_script),
                timeout=timeout
            )
            return ExecutionResult(success=True, ...)
        except:
            if attempt < retries:
                await asyncio.sleep(delay * backoff^attempt)
    return ExecutionResult(success=False, error=last_error)
```

---

## Architecture Decisions for Go Agent

### 1. Protocol
- **Primary:** gRPC with protobuf for performance + streaming
- **Fallback:** HTTP POST to existing sensor API for compatibility

### 2. Transport Security
- mTLS for gRPC (appliance issues client certs)
- Appliance CA built into agent binary

### 3. Checks Mapping
| Go Agent Check | PowerShell Equivalent | HIPAA Control |
|----------------|----------------------|---------------|
| bitlocker | (new) | 164.312(a)(2)(iv) |
| defender | Check-WindowsDefender | 164.308(a)(5)(ii)(B) |
| patches | (new) | 164.308(a)(1)(ii)(B) |
| firewall | Check-Firewall | 164.312(e)(1) |
| screenlock | (new) | 164.312(a)(2)(i) |
| rmm_detection | (new - strategic) | N/A |

### 4. Capability Tiers
```
MONITOR_ONLY = 0     # Just reports drift (MSP default)
SELF_HEAL = 1        # Can fix drift locally
FULL_REMEDIATION = 2 # Full automation
```

Tier controlled server-side to hide capabilities from MSPs.

### 5. Offline Queue
SQLite WAL mode for durability during network outages.
Queue drains when connection restored.

---

## Files to Create

| File | Purpose |
|------|---------|
| `agent/proto/compliance.proto` | gRPC protocol definition |
| `agent/cmd/osiris-agent/main.go` | Entry point |
| `agent/internal/checks/*.go` | Compliance checks |
| `agent/internal/transport/grpc.go` | gRPC client |
| `agent/internal/transport/offline.go` | SQLite queue |
| `agent/internal/config/config.go` | Configuration |
| `agent/internal/wmi/wmi.go` | WMI helpers |
| `agent/flake.nix` | Nix build config |
| `agent/Makefile` | Dev build commands |
| `grpc_server.py` | Python gRPC server |

---

## Integration Points

### Appliance Agent Changes
```python
# Add to appliance_agent.py start()
self._grpc_task = asyncio.create_task(
    grpc_serve(
        port=50051,
        agent_registry=self.agent_registry,
        mcp_client=self.mcp_client,
        healing_engine=self.healing_engine,
    )
)
```

### Port Assignments
| Port | Service | Protocol |
|------|---------|----------|
| 8080 | Sensor API (existing) | HTTP/REST |
| 50051 | Go Agent API (new) | gRPC |
| 5985 | WinRM HTTP | HTTP |
| 5986 | WinRM HTTPS | HTTPS |
