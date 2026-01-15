# OsirisCare Development Roadmap - Integrated

**Created:** 2026-01-14
**Based On:** User roadmap + System audit
**Current State:** Agent v1.0.30, ISO v29, 656 tests

---

## System Audit Summary

### What Exists Today

| Component | Status | Details |
|-----------|--------|---------|
| **Windows Server Monitoring** | ✅ Complete | 28 runbooks via WinRM, dual-mode (polling + sensor) |
| **Linux Server Monitoring** | ✅ Complete | 16 runbooks via SSH, sensor support |
| **3-Tier Healing** | ✅ Complete | L1 deterministic (70-80%), L2 LLM, L3 escalation |
| **Evidence Generation** | ✅ Complete | Ed25519 signing, OTS anchoring, hash chains |
| **Learning Flywheel** | ✅ Complete | L2→L1 promotion (2,269 patterns promoted) |
| **Multi-Framework** | ✅ Complete | HIPAA, SOC2, PCI-DSS, NIST CSF, CIS |
| **Cloud Integrations** | ✅ Partial | AWS, Okta, Azure AD, Google Workspace (no Microsoft Graph/Defender) |
| **Partner Infrastructure** | ✅ Complete | QR provisioning, escalation channels |

### What's Missing (Gap Analysis)

| Roadmap Item | Current Gap | Priority |
|--------------|-------------|----------|
| **Workstation Discovery (AD)** | Only manual target config, no AD enumeration | HIGH |
| **Workstation WMI Checks** | Have WinRM but queries are server-focused | HIGH |
| **Go Agent** | Using PowerShell sensor (functional but not scalable) | MEDIUM |
| **Microsoft Graph/Defender** | Not implemented (Azure AD exists but not Defender/Intune) | MEDIUM |
| **Datto RMM Integration** | Not started | LOW |
| **L2 on Central Command only** | L2 runs on BOTH appliance and CC currently | LOW |

---

## Phase 1: Workstation Coverage (Priority: HIGH)

### Gap Analysis

**Current State:**
- Appliance monitors servers via WinRM polling (60s cycle)
- Windows sensor (PowerShell) exists for push-based drift
- No Active Directory enumeration for auto-discovery
- No workstation-specific compliance checks (BitLocker, Defender, patches, firewall, screen lock)

**What Needs Building:**

```
packages/compliance-agent/src/compliance_agent/
├── workstation_discovery.py     # NEW - AD enumeration
├── workstation_checks.py        # NEW - 5 WMI checks
├── workstation_evidence.py      # NEW - Evidence aggregation
└── appliance_agent.py           # MODIFY - Add workstation scan cycle
```

### 1.1 Workstation Discovery via AD

**File:** `packages/compliance-agent/src/compliance_agent/workstation_discovery.py`

**Implementation Notes:**
- Use existing WinRM infrastructure (`executor.py` at line 559)
- Query AD via LDAP or PowerShell `Get-ADComputer`
- Filter by OS: `OperatingSystem -like "*Windows 10*" -or "*Windows 11*"`
- Reuse credential-pull architecture (already implemented in Session 9)

**Key Integration Points:**
- `appliance_agent.py:_update_windows_targets_from_response()` - Already handles target updates
- Add new `workstation_targets` field alongside `windows_targets` (servers)

### 1.2 WMI Compliance Checks

**File:** `packages/compliance-agent/src/compliance_agent/workstation_checks.py`

**Existing Code to Leverage:**
- `runbooks/windows/security.py` has BitLocker, Defender, Firewall runbooks
- `runbooks/windows/executor.py` has WinRM session management
- Just need WMI-specific queries (not remediation-focused)

**5 Checks Mapped to Existing Runbooks:**

| Check | Existing Runbook | WMI Class | HIPAA Control |
|-------|------------------|-----------|---------------|
| BitLocker | `RB-WIN-SEC-005` | `Win32_EncryptableVolume` | §164.312(a)(2)(iv) |
| Defender | `RB-WIN-SEC-006` | `MSFT_MpComputerStatus` | §164.308(a)(5)(ii)(B) |
| Patches | `RB-WIN-UPD-001` | `Win32_QuickFixEngineering` | §164.308(a)(5)(ii)(B) |
| Firewall | `RB-WIN-SEC-001` | `MSFT_NetFirewallProfile` | §164.312(a)(1) |
| Screen Lock | NEW | Registry query | §164.312(a)(2)(iii) |

### 1.3 Evidence Generation

**Existing Infrastructure:**
- `evidence.py` (700 lines) - Already handles bundle creation, signing, dedup
- Just need workstation-specific bundle type and site-level aggregation

**Integration:**
- Add `device_type: "workstation"` to evidence bundles
- Add `SiteWorkstationSummary` model for aggregated view

### 1.4 Dashboard Integration

**File:** `mcp-server/central-command/frontend/src/pages/SiteWorkstations.tsx`

**Existing Patterns:**
- `SiteDetail.tsx` shows server compliance - extend for workstations
- `IncidentRow.tsx` already handles check types dynamically

### 1.5 Agent Integration

**File:** `packages/compliance-agent/src/compliance_agent/appliance_agent.py`

**Add to Run Cycle:**
```python
async def _maybe_scan_workstations(self):
    """
    10-minute cycle for workstation compliance.
    Separate from server scan (5-minute cycle).
    """
    # Discover from AD (hourly)
    # Check online status
    # Run 5 WMI checks on online workstations
    # Generate per-workstation evidence
    # Create site summary bundle
```

**Location:** After `_maybe_scan_windows()` around line 800

### Phase 1 Deliverables

| Deliverable | Files | Tests |
|-------------|-------|-------|
| AD Discovery | `workstation_discovery.py` | 5 tests |
| WMI Checks | `workstation_checks.py` | 10 tests (2 per check) |
| Evidence Gen | `workstation_evidence.py` | 5 tests |
| Dashboard | `SiteWorkstations.tsx`, API routes | 3 tests |
| Agent Integration | `appliance_agent.py` modifications | 5 tests |
| **Total** | 4 new files, 2 modified | **28 tests** |

---

## Phase 2: Go Agent (Priority: MEDIUM)

### Gap Analysis

**Current State:**
- PowerShell sensor (`OsirisSensor.ps1`) - 12 checks, push to appliance port 8080
- Dual-mode: sensors push, non-sensor hosts polled via WinRM
- Works but: large payload, no offline queue, no self-update, Windows-only

**Go Agent Benefits:**
- Single 5MB binary (vs PowerShell dependencies)
- gRPC streaming (persistent connection, lower overhead)
- Offline queue (SQLite)
- Self-update mechanism
- Cross-platform potential (Windows + Mac)

### Directory Structure

```
agent/                          # NEW top-level directory
├── cmd/osiris-agent/
│   ├── main.go                 # Entry point
│   └── service_windows.go      # Windows service integration
├── internal/
│   ├── checks/                 # 5 compliance checks
│   ├── transport/              # gRPC client + offline queue
│   ├── config/                 # Configuration
│   └── updater/                # Self-update
├── proto/compliance.proto      # gRPC definitions
├── go.mod
└── Makefile
```

### Integration with Existing Infrastructure

**Appliance Side:**
- Add gRPC server alongside existing FastAPI sensor API
- Both can coexist during transition
- gRPC server in `packages/compliance-agent/src/compliance_agent/grpc_server.py`

**Central Command:**
- No changes needed - evidence flows through appliance

### Phase 2 Deliverables

| Deliverable | Effort | Dependencies |
|-------------|--------|--------------|
| Go agent core | 2 weeks | None |
| gRPC proto + server | 1 week | Go agent |
| 5 compliance checks in Go | 1 week | Go agent |
| Windows service installer | 3 days | Go agent |
| Self-update mechanism | 1 week | Go agent |
| Appliance gRPC server | 1 week | Proto |
| **Total** | **6 weeks** | |

---

## Phase 3: Integration Enrichment (Priority: MEDIUM) ✅ COMPLETE

### Gap Analysis

**Already Implemented:**
- AWS integration (`integrations/aws/connector.py`) - IAM, S3, CloudTrail
- Azure AD integration (`integrations/oauth/azure_connector.py`) - users, groups
- Okta integration (`integrations/oauth/okta_connector.py`) - SSO, MFA
- Google Workspace (`integrations/oauth/google_connector.py`) - users, devices
- **Microsoft Security (`integrations/oauth/microsoft_graph.py`)** - Defender + Intune ✅ NEW

**Remaining:**
- Datto RMM API (LOW priority)
- ConnectWise RMM API (LOW priority)

### Microsoft Graph Integration

**File:** `mcp-server/central-command/backend/integrations/oauth/microsoft_graph.py`

**Extends Existing:** `azure_connector.py` (already has Azure AD OAuth)

**New Scopes:**
- `DeviceManagementManagedDevices.Read.All` - Intune devices
- `SecurityEvents.Read.All` - Defender alerts
- `Device.Read.All` - Azure AD devices

**New Endpoints:**
- `/deviceManagement/managedDevices` - Intune compliance
- `/security/alerts` - Defender alerts
- `/security/secureScores` - Security posture

### RMM Integrations

**Priority:** LOW - Most SMB healthcare practices don't have RMM

**If Needed:**
- Datto RMM: REST API with API key auth
- ConnectWise: REST API with integrator credentials

### Phase 3 Deliverables

| Deliverable | Effort | Dependencies |
|-------------|--------|--------------|
| Microsoft Graph/Defender | 1 week | Azure AD connector exists |
| Microsoft Intune | 1 week | Graph connector |
| Evidence enrichment pipeline | 1 week | Graph data |
| Datto RMM (optional) | 1 week | None |
| **Total** | **3-4 weeks** | |

---

## Phase 4: Appliance Slimming (Priority: LOW)

### Gap Analysis

**Current State:**
- L2 LLM runs on BOTH appliance (`level2_llm.py`) AND Central Command (`l2_planner.py`)
- Appliance has full Anthropic SDK (~50MB)
- Each appliance needs API key

**Target State:**
- L2 only on Central Command
- Appliance sends incidents, receives healing plan
- Smaller appliance image, centralized API key

### Implementation

**Already Exists:**
- `mcp-server/central-command/backend/l2_planner.py` - Server-side L2

**Need to Add:**
- `packages/compliance-agent/src/compliance_agent/l2_client.py` - Client to call CC
- Modify `auto_healer.py` to route L2 to CC instead of local

**Migration Path:**
1. Add `l2_mode: local | remote` config option
2. Default to `local` (current behavior)
3. Sites can opt-in to `remote`
4. Eventually deprecate local L2

### Phase 4 Deliverables

| Deliverable | Effort | Dependencies |
|-------------|--------|--------------|
| L2 client | 3 days | None |
| Auto-healer routing | 2 days | L2 client |
| Config option | 1 day | Routing |
| Testing | 2 days | All above |
| **Total** | **1.5 weeks** | |

---

## Recommended Implementation Order

```
Phase 1 (Workstations)     ████████████████████  2 weeks    HIGH
  └─ Unlocks 50+ devices per appliance

Phase 3 (Graph/Defender)   ████████████          3 weeks    MEDIUM
  └─ Can run parallel with Phase 1 validation
  └─ Enriches evidence with cloud posture

Phase 2 (Go Agent)         ████████████████████████████████ 6 weeks MEDIUM
  └─ Start after Phase 1 complete
  └─ Proper scale architecture

Phase 4 (Slim Appliance)   ██████                1.5 weeks  LOW
  └─ Optimization, do when needed
```

### Parallel Tracks

**Track A (Immediate):** Phase 1 workstation coverage
**Track B (Week 3+):** Phase 3 Microsoft Graph (can start while validating Phase 1)
**Track C (Week 5+):** Phase 2 Go agent (longer effort, start after Phase 1 ships)

---

## File Summary

### New Files to Create

| Phase | File | Purpose |
|-------|------|---------|
| 1 | `workstation_discovery.py` | AD enumeration |
| 1 | `workstation_checks.py` | 5 WMI compliance checks |
| 1 | `workstation_evidence.py` | Workstation evidence bundles |
| 1 | `SiteWorkstations.tsx` | Dashboard workstation view |
| 2 | `agent/` directory | Go agent (entire new package) |
| 2 | `grpc_server.py` | Appliance gRPC receiver |
| 3 | `microsoft_graph.py` | Defender/Intune integration |
| 4 | `l2_client.py` | Remote L2 client |

### Files to Modify

| Phase | File | Change |
|-------|------|--------|
| 1 | `appliance_agent.py` | Add workstation scan cycle |
| 1 | `routes.py` | Add workstation API endpoints |
| 1 | `models.py` | Add WorkstationStatus model |
| 2 | `appliance_agent.py` | Add gRPC server startup |
| 3 | `sync_engine.py` | Add Graph resource sync |
| 4 | `auto_healer.py` | Add remote L2 routing |

---

## Testing Strategy

### Phase 1 Tests (28 total)

```
tests/test_workstation_discovery.py     # 5 tests
tests/test_workstation_checks.py        # 10 tests
tests/test_workstation_evidence.py      # 5 tests
tests/test_workstation_integration.py   # 8 tests
```

### Test Environments

| Environment | Purpose | Access |
|-------------|---------|--------|
| VirtualBox Windows VMs | Workstation testing | iMac gateway |
| North Valley Lab | Domain testing (NVDC01, NVWS01) | 192.168.88.250 |
| Physical appliance | Production testing | 192.168.88.246 |
| VM appliance | Development testing | 192.168.88.247 |

---

## Success Metrics

| Phase | Metric | Target |
|-------|--------|--------|
| 1 | Devices monitored per appliance | 50+ |
| 1 | Workstation compliance checks | 5 per device |
| 1 | Partner deployments | 3 |
| 2 | Go agent binary size | <5MB |
| 2 | Agent CPU usage | <1% |
| 2 | Concurrent agents per appliance | 100+ |
| 3 | Integrations connected | Graph + existing 4 |
| 4 | Appliance RAM usage | <2GB |

---

## Next Steps (This Session)

1. **Create Phase 1 file stubs** with docstrings and signatures
2. **Add database migration** for workstation tracking
3. **Update TODO.md** with Phase 1 tasks
4. **Start implementation** of `workstation_discovery.py`

---

## Appendix: Key Files Reference

**Understand These First:**
- `packages/compliance-agent/src/compliance_agent/appliance_agent.py` - Main loop (1,900 lines)
- `packages/compliance-agent/src/compliance_agent/evidence.py` - Evidence generation (700 lines)
- `packages/compliance-agent/src/compliance_agent/runbooks/windows/executor.py` - WinRM execution (559 lines)
- `mcp-server/central-command/backend/routes.py` - API endpoints (1,615 lines)
- `mcp-server/central-command/backend/integrations/api.py` - Integration patterns

**Reuse These Patterns:**
- Credential-pull from `_update_windows_targets_from_response()`
- Evidence signing from `EvidenceGenerator.create_evidence()`
- WinRM session management from `WindowsExecutor`
- OAuth flow from `integrations/oauth/base_connector.py`
