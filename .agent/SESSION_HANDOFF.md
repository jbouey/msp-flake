# Session Handoff - 2026-01-16

**Session:** 41 - VM Network/AD Configuration + Go Agent Dashboard Deployment
**Agent Version:** v1.0.34
**ISO Version:** v33 (deployed), v35 pending (with gRPC server)
**Go Agent Binaries:** Built on VPS (`/root/msp-iso-build/agent/`)
**Last Updated:** 2026-01-16

---

## Session 41 Accomplishments

### 1. VM Network Configuration
Fixed all VMs to be on the 192.168.88.x subnet with DNS pointing to Windows DC.

**Network Status:**
| VM | IP | Status | Notes |
|----|-----|--------|-------|
| NVDC01 (DC) | 192.168.88.250 | ✅ Online | ICMP enabled |
| NVWS01 (Workstation) | 192.168.88.251 | ✅ Online | ICMP enabled after Windows Updates |
| NVSRV01 (Server) | 192.168.88.244 | ✅ Online | Windows Server Core, ICMP enabled |
| northvalley-linux | 192.168.88.x | ✅ Online | Changed from NAT to bridged mode |
| osiriscare-appliance | 192.168.88.246 | ✅ Online | Physical HP T640 |

### 2. AD Domain Verification
All 3 Windows machines properly domain-joined to `northvalley.local`:
- NVDC01 (DC) - DNS 127.0.0.1 (self)
- NVWS01 - DNS 192.168.88.250, domain joined
- NVSRV01 - DNS 192.168.88.250, domain joined, nltest confirms DC connectivity

### 3. Service Account WinRM Permissions Fixed
**Issue:** `svc.monitoring` service account couldn't connect via WinRM.
**Fix:** Added `svc.monitoring` to:
- Remote Management Users group (on DC)
- Domain Admins group (for full access)

**Verified:** svc.monitoring can now connect to all 3 Windows machines via WinRM.

### 4. VPS Deployment (Earlier in Session)
- Deployed Go Agents frontend to VPS (`index-CBjgnJ2z.js`)
- Ran database migration `019_go_agents.sql` - created 4 tables + 2 views

### 5. Compliance Agent Ready
With WinRM permissions fixed, the compliance agent on the appliance can now:
- Enumerate AD computers via svc.monitoring
- Connect to workstations via WinRM for compliance checks
- Report drift events to Central Command

---

## Session 40 Accomplishments

### 1. Go Agent for Workstation-Scale Compliance
Implemented Go agent that pushes drift events to appliance via gRPC, solving the scalability problem of polling 25-50 workstations per site via WinRM.

**Architecture:**
```
Windows Workstation          NixOS Appliance
┌─────────────────┐         ┌─────────────────────┐
│  Go Agent       │ gRPC    │  Python Agent       │
│  - 6 checks     │────────►│  - gRPC Server      │
│  - SQLite queue │ :50051  │  - Sensor API :8080 │
│  - RMM detect   │         │  - Three-tier heal  │
└─────────────────┘         └─────────────────────┘
```

**Files Created:**
- `agent/` - Complete Go agent implementation (14 Go files)
- `agent/proto/compliance.proto` - gRPC protocol definitions
- `agent/flake.nix` - Nix cross-compilation for Windows
- `packages/compliance-agent/src/compliance_agent/grpc_server.py` - Python gRPC server
- `packages/compliance-agent/tests/test_grpc_server.py` - 12 tests

### 2. Appliance Agent gRPC Integration
**File Modified:** `packages/compliance-agent/src/compliance_agent/appliance_agent.py`
- Import grpc_server module
- Add gRPC server config (grpc_enabled, grpc_port=50051)
- Start/stop gRPC server alongside sensor API

### 3. Git Commits
- `8422638` - feat: Add Go agent for workstation-scale compliance monitoring
- `37b018c` - feat: Integrate gRPC server into appliance agent for Go agent support

### 4. Tests: 786 passed, 11 skipped

---

## Session 39 Accomplishments

### 1. $params_Hostname Variable Injection Bug Fix
**Root Cause:** WindowsExecutor.run_script() injects script_params variables with `$params_` prefix, but workstation discovery scripts used bare `$Hostname`.

**Files Modified:**
- `packages/compliance-agent/src/compliance_agent/workstation_discovery.py`:
  - `PING_CHECK_SCRIPT`: `$Hostname` → `$params_Hostname`
  - `WMI_CHECK_SCRIPT`: `$Hostname` → `$params_Hostname`
  - `WINRM_CHECK_SCRIPT`: `$Hostname` → `$params_Hostname`
- `packages/compliance-agent/setup.py` - Bumped version to 1.0.34

### 2. ISO v33 Built and Deployed
- Built on VPS: `/root/msp-iso-build/result-v33/iso/osiriscare-appliance.iso`
- Downloaded to MacBook: `/tmp/osiriscare-appliance-v33.iso`
- Copied to iMac: `~/Downloads/osiriscare-appliance-v33.iso`
- Physical appliance flashed with ISO v33

### 3. Workstation Discovery Testing
**Results:**
- ✅ Direct WinRM to NVWS01 from VM appliance: **WORKS** (returned "Hostname: NVWS01")
- ✅ AD enumeration from DC: **WORKS** (found NVWS01 at 192.168.88.251)
- ❌ Test-NetConnection from DC: **TIMED OUT** (DC was restoring from chaos lab snapshot)

### 4. Documentation Created
- `.agent/PROJECT_SUMMARY.md` - Comprehensive project overview for Claude Code website
- `CLAUDE.md` - Updated with current version (v1.0.34) and test count (778+)

### 5. Git Commits Pushed
- `4db0207` - fix: Use $params_Hostname for workstation online detection
- `2b245b6` - docs: Add PROJECT_SUMMARY.md and update CLAUDE.md
- `5c6c5c5` - docs: Update claude.md with current version and project summary link

---

## Known Issues

### 1. Overlay Module Import Error
**Error:** `ModuleNotFoundError: No module named 'compliance_agent.appliance_agent'`
**Location:** `/var/lib/msp/run_agent_overlay.py` on appliance
**Notes:** The overlay mechanism doesn't properly include the appliance_agent module. Needs investigation.

### 2. Test-NetConnection Timeout
**Issue:** Online status check via Test-NetConnection times out when running from DC to workstations.
**Possible Causes:**
- DC was restoring from chaos lab snapshot
- Test-NetConnection may have long default timeout
- Network latency or firewall issues

**Workaround:** Direct WinRM connection to workstations works. Consider checking online status directly from appliance instead of via DC.

---

## Physical Appliance Configuration

**Config at `/var/lib/msp/config.yaml`:**
```yaml
site_id: physical-appliance-pilot-1aea78
api_key: 4Rpwd6tFOUs9JlanSFEwbjNRcBN2gH3kgr0LKDp6mTQ
api_endpoint: https://api.osiriscare.net

# Workstation Discovery
workstation_enabled: true
domain_controller: 192.168.88.250
dc_username: NORTHVALLEY\svc.monitoring
dc_password: SvcAccount2024!
```

---

## What's Working

### Cloud Integrations (5 providers)
| Provider | Status | Resources |
|----------|--------|-----------|
| AWS | ✅ | IAM users, EC2, S3, CloudTrail |
| Google Workspace | ✅ | Users, Devices, OAuth apps |
| Okta | ✅ | Users, Groups, Apps, Policies |
| Azure AD | ✅ | Users, Groups, Apps, Devices |
| Microsoft Security | ✅ | Defender alerts, Intune, Secure Score |

### Phase 1 Workstation Coverage
- AD workstation discovery via PowerShell Get-ADComputer
- 5 WMI compliance checks: BitLocker, Defender, Patches, Firewall, Screen Lock
- HIPAA control mappings for each check
- Frontend: SiteWorkstations.tsx page

### Three-Tier Auto-Healing
- L1 Deterministic: 70-80%, <100ms, $0
- L2 LLM Planner: 15-20%, 2-5s, ~$0.001
- L3 Human Escalation: 5-10%

---

## Next Session Tasks

1. **Deploy Go agent to workstations** - Copy osiris-agent.exe from VPS `/root/msp-iso-build/agent/` to Windows workstations
2. **Build ISO v35** - Include gRPC server integration (`grpc_server.py`, `appliance_agent.py` changes)
3. **Test Go agent → Appliance communication** - Verify gRPC streaming works on port 50051
4. **Fix overlay module import** - Ensure appliance_agent is included in overlay
5. **Wait for DC recovery** - DC was restoring from chaos snapshot
6. **Re-test workstation online detection** - Once DC is stable

### Quick Commands
```bash
# Check agent on appliance
ssh root@192.168.88.246 "tail -50 /var/lib/msp/agent_final.log"

# Test WinRM to NVWS01 directly
ssh root@192.168.88.246 "python3 -c \"
import winrm
s = winrm.Session('http://192.168.88.251:5985/wsman', auth=('NORTHVALLEY\\\\svc.monitoring','SvcAccount2024!'), transport='ntlm')
print(s.run_ps('hostname').std_out.decode())
\""

# Check DC status
ssh root@192.168.88.246 "python3 -c \"
import winrm
s = winrm.Session('http://192.168.88.250:5985/wsman', auth=('NORTHVALLEY\\\\svc.monitoring','SvcAccount2024!'), transport='ntlm')
print(s.run_ps('Get-ADComputer -Filter * | Select Name').std_out.decode())
\""

# SSH to VPS
ssh root@178.156.162.116

# Deploy to VPS
ssh root@api.osiriscare.net "/opt/mcp-server/deploy.sh"
```

---

## Files Modified This Session

### Session 40 (Go Agent Implementation)
| File | Change |
|------|--------|
| `agent/` | Complete Go agent implementation (14 Go files) |
| `agent/proto/compliance.proto` | gRPC protocol definitions |
| `agent/flake.nix` | Fixed `licenses.proprietary` → `licenses.unfree` |
| `agent/go.mod` | Updated dependencies to valid versions |
| `agent/go.sum` | Created with verified dependency hashes |
| `packages/compliance-agent/src/compliance_agent/grpc_server.py` | Python gRPC server |
| `packages/compliance-agent/src/compliance_agent/appliance_agent.py` | gRPC server integration |
| `packages/compliance-agent/tests/test_grpc_server.py` | 12 gRPC tests |
| **Frontend** | |
| `mcp-server/.../frontend/src/types/index.ts` | Go agent types and mappings |
| `mcp-server/.../frontend/src/utils/api.ts` | goAgentsApi with CRUD endpoints |
| `mcp-server/.../frontend/src/hooks/useFleet.ts` | Go agent hooks |
| `mcp-server/.../frontend/src/pages/SiteGoAgents.tsx` | **NEW** Go agents dashboard page |
| `mcp-server/.../frontend/src/pages/SiteDetail.tsx` | Added "Go Agents" button |
| `mcp-server/.../frontend/src/App.tsx` | Route /sites/:siteId/agents |
| **Backend** | |
| `mcp-server/.../backend/migrations/019_go_agents.sql` | **NEW** Go agents database schema |
| `mcp-server/.../backend/sites.py` | Go agents API endpoints |

### Session 39 ($params_Hostname Bug Fix)
| File | Change |
|------|--------|
| `packages/compliance-agent/setup.py` | Version bump to 1.0.34 |
| `packages/compliance-agent/src/compliance_agent/workstation_discovery.py` | $params_Hostname fix in 3 scripts |
| `.agent/PROJECT_SUMMARY.md` | New comprehensive project documentation |
| `CLAUDE.md` | Updated version and test count |

---

## Related Docs

- `.agent/PROJECT_SUMMARY.md` - Comprehensive project overview
- `.agent/VPS_DEPLOYMENT.md` - Deployment guide
- `.agent/TODO.md` - Session tasks
- `.agent/CONTEXT.md` - Project context
- `.agent/DEVELOPMENT_ROADMAP.md` - Phase tracking
- `.agent/LAB_CREDENTIALS.md` - Lab credentials
- `IMPLEMENTATION-STATUS.md` - Full status
