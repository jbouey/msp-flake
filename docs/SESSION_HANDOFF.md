# Session Handoff - MSP Compliance Platform

**Last Updated:** 2026-01-17 (Session 49)
**Current State:** ISO v38 Built - Ready for Deployment

---

## Quick Status

| Component | Status | Version |
|-----------|--------|---------|
| Agent | v1.0.38 | Stable |
| ISO | v38 | **READY** - gRPC fixed |
| Tests | 811 + 24 Go tests | Healthy |
| Go Agent | Deployed to NVWS01 | 16.6MB binary |
| gRPC | **FIXED** in ISO v38 | Ready to test |
| Chaos Lab | Verified | L1 healing working |
| L1 Rules | Platform-specific | 29 rules in l1_baseline.json |
| Security Runbooks | 13 total | All categories |

---

## Session 49 Summary (2026-01-17)

### Completed
1. **ISO v38 Built** - With gRPC fixes on physical appliance (192.168.88.246)
2. **Version Bump** - Agent v1.0.35 → v1.0.38 in setup.py and default.nix
3. **gRPC Fix Verified** - Servicer registration at lines 321 and 354
4. **pb2 Files Included** - compliance_pb2.py and compliance_pb2_grpc.py confirmed
5. **ISO Copied Local** - `iso/osiriscare-appliance-v38.iso` (1.1GB)

### Build Details
- **Built On:** Physical appliance (192.168.88.246) - 15GB tmpfs
- **VPS (159.203.186.142):** SSH unreachable during session
- **VM Appliance (192.168.88.247):** Insufficient disk space (610MB tmpfs)

### ISO v38 Location
```
Local: /Users/dad/Documents/Msp_Flakes/iso/osiriscare-appliance-v38.iso
Appliance: /root/msp-iso-build/result-iso-v38/iso/osiriscare-appliance.iso
```

### Next Steps
1. Flash ISO v38 to VM appliance (192.168.88.247)
2. Test gRPC connection from Go agent (NVWS01)
3. Verify drift events flow to appliance
4. Run end-to-end three-tier healing test

---

## Session 48 Summary (2026-01-17)

### Critical Bug Discovered
**ISO v37 gRPC is non-functional:**
1. `compliance_pb2_grpc.add_ComplianceAgentServicer_to_server()` is **commented out**
2. `compliance_pb2.py` and `compliance_pb2_grpc.py` are **not included** in ISO

**Error seen:** `rpc error: code = Unimplemented desc = Method not found!`

### Completed
1. **Config JSON key fix** - Changed `appliance_address` → `appliance_addr` on NVWS01
2. **gRPC connection verified** - Go agent connects but methods fail
3. **Go agent registry queries working** - screenlock, pending_reboot show actual values
4. **Hot-patch attempted** - Failed (NixOS read-only + relative imports)

### Go Agent Check Results on NVWS01
| Check | Status | Notes |
|-------|--------|-------|
| rmm_detection | PASS | No RMM found |
| screenlock | FAIL | Screensaver disabled (registry working) |
| defender | FAIL | AntivirusEnabled=false |
| bitlocker | FAIL | Could not read ProtectionStatus |
| firewall | FAIL | **BUG:** Service state empty |
| patches | ERROR | **BUG:** Invalid query |

### Known Go Agent Bugs
1. **Firewall service state empty** - `GetServiceState("MpsSvc")` returns ""
2. **Patches WMI query invalid** - Syntax error
3. **SQLite requires CGO** - Built with `CGO_ENABLED=0`

### Files Modified This Session
- `C:\ProgramData\OsirisCare\config.json` (NVWS01) - Fixed JSON key

---

## Session 47 Summary (2026-01-17)

### Completed
1. **WMI Registry Query Functions** - GetRegistryDWORD, GetRegistryString, RegistryKeyExists
2. **Firewall Check Fix** - Uses actual registry queries instead of hardcoded values
3. **Screen Lock Check Fix** - Queries ScreenSaveActive, ScreenSaveTimeOut, ScreenSaverIsSecure
4. **Pending Reboot Detection** - 4 detection methods (Windows Update, CBS, PendingFileRename, ComputerName)
5. **Offline Queue Size Limits** - MaxSize 10000, MaxAge 7 days, automatic pruning
6. **Queue Stats** - Count, UsageRatio, OldestAge for monitoring
7. **24 Go Tests** - checks, transport, wmi packages

### Files Created
- `agent/internal/checks/checks_test.go` - 12 tests
- `agent/internal/transport/offline_test.go` - 9 tests
- `agent/internal/wmi/wmi_test.go` - 5 tests

### Files Modified
- `agent/internal/wmi/wmi.go` - Registry query interface
- `agent/internal/wmi/wmi_windows.go` - Windows implementation using COM/OLE
- `agent/internal/wmi/wmi_other.go` - Non-Windows stubs
- `agent/internal/checks/firewall.go` - Registry-based profile detection
- `agent/internal/checks/screenlock.go` - Registry-based screen saver settings
- `agent/internal/checks/patches.go` - Pending reboot detection
- `agent/internal/transport/offline.go` - Queue limits and stats
- `agent/cmd/osiris-agent/main.go` - Build fix

### Git Commit
- `cbea2c9` - 11 files, 1030 insertions, 25 deletions

---

## Session 46 Summary (2026-01-17)

### Completed
1. **NixOS Firewall Platform-Specific Rule** - Escalates to L3 instead of trying Windows runbook
2. **L1 Rules Action Format Fix** - Changed to proper action_params structure
3. **Defender Runbook ID Fix** - RB-WIN-SEC-006 -> RB-WIN-AV-001
4. **L1 Rules Saved to Codebase** - l1_baseline.json with 29 rules (expanded from 7)
5. **Chaos Lab Verification** - Firewall and Defender attacks healed successfully
6. **Comprehensive Security Runbooks** - Added 7 new runbooks (SMB, NTLM, Users, NLA, UAC, EventLog, CredGuard)

### Chaos Lab Results
| Attack | L1 Rule | Result |
|--------|---------|--------|
| Firewall disable | L1-FIREWALL-002 | SUCCESS |
| Defender disable | L1-DEFENDER-001 | SUCCESS |
| Password policy | No L1 rule | Escalated to L3 |
| Audit policy | No L1 rule | Escalated to L3 |

### Files Modified
- `/var/lib/msp/rules/l1_rules.json` (appliance)
- `packages/compliance-agent/src/compliance_agent/rules/l1_baseline.json`
- `executor.py` - Import fix for ALL_RUNBOOKS

---

## Session 45 Summary (2026-01-16)

### Completed
1. **gRPC Protobuf Definition** - Unified `/proto/compliance.proto`
2. **Python gRPC Server** - Servicer inherits from generated code
3. **Go gRPC Client** - Uses generated protobuf types
4. **Tests Updated** - All 12 gRPC tests pass, 811 total

### Files Created/Modified
- `/proto/compliance.proto` - Unified protobuf definition
- `compliance_pb2.py`, `compliance_pb2_grpc.py` - Generated Python
- `grpc_server.py` - Rewrote to use generated servicer
- `agent/proto/*.go` - Generated Go protobuf
- `agent/internal/transport/grpc.go` - Rewrote for generated client
- `test_grpc_server.py` - Updated for sync API

---

## Session 44 Summary (2026-01-16)

### Completed
1. **Go Agent Config on NVWS01** - Created config.json
2. **Firewall Port 50051** - Hot-fix + permanent in ISO v37
3. **Go Agent Dry-Run** - 2 PASS, 3 FAIL, 1 ERROR (expected)
4. **ISO v37 Build** - With port 50051 and gRPC dependencies

---

## Session 42 Summary (2026-01-15)

### Completed
1. **Workstation Cadence Unit Tests** - 21 tests for polling intervals
2. **Chaos Lab Integration** - Monitoring script + cron automation
3. **Go Agent Deployment** - Deployed to NVWS01, dry-run tested
4. **ISO v35 Build** - gRPC server for Go Agent communication

---

## Infrastructure State

### Physical Appliance (192.168.88.246)
- **Status:** Online, running ISO v33
- **Agent:** v1.0.34
- **Pending:** ISO v35 upgrade for gRPC server

### Windows Workstations
| Machine | IP | Go Agent | Status |
|---------|-----|----------|--------|
| NVWS01 | 192.168.88.251 | Deployed | Tested dry-run |
| NVDC01 | 192.168.88.250 | - | Domain Controller |
| NVSRV01 | 192.168.88.244 | - | Server Core |

### Chaos Lab (iMac 192.168.88.50)
**Cron Schedule:**
```
6:00  - Morning chaos execution
10:00 - Workstation cadence verification (NEW)
12:00 - Mid-day checkpoint
14:00 - Afternoon chaos execution
16:00 - Workstation cadence verification (NEW)
18:00 - End of day report
20:00 - Next day planning
```

---

## Next Session (49) Priorities

### 1. Rebuild ISO v38 with gRPC Fixes (CRITICAL)
1. Fix `grpc_server.py` - uncomment servicer registration (lines 321, 354)
2. Include `compliance_pb2.py` in package
3. Include `compliance_pb2_grpc.py` in package
4. Build and deploy ISO v38 to VM appliance

### 2. Fix Go Agent Bugs
1. **Firewall service state empty** - Fix `GetServiceState("MpsSvc")` in `wmi.go`
2. **Patches WMI query invalid** - Fix WMI query syntax in `patches.go`
3. Rebuild with CGO enabled for SQLite (optional, queue works but logs error)

### 3. End-to-End gRPC Test
1. Deploy fixed Go agent to NVWS01
2. Flash ISO v38 to VM appliance
3. Run Go agent and verify drift events flow to appliance
4. Monitor AgentRegistry for connected agents
5. Verify three-tier healing processes Go agent drift

### Verification Steps
```bash
# Check gRPC methods on appliance
ssh root@192.168.88.247 "python3 -c 'from compliance_agent import compliance_pb2_grpc; print(dir(compliance_pb2_grpc))'"

# Run Go agent
C:\OsirisCare\osiris-agent.exe

# Check appliance logs for gRPC activity
ssh root@192.168.88.247 "journalctl -u compliance-agent -f | grep -i grpc"
```

---

## Key Locations

### ISO Files
| Version | Location |
|---------|----------|
| v35 (latest) | `/tmp/osiriscare-appliance-v35.iso` (local) |
| v35 (VPS) | `/root/msp-iso-build/result-iso-v35/iso/` |
| v33 (deployed) | Physical appliance |

### Go Agent
| File | Location |
|------|----------|
| Binary | NVWS01 `C:\OsirisCare\osiris-agent.exe` |
| Source | VPS `/root/msp-iso-build/agent/` |

### Tests
| Test File | Count | Purpose |
|-----------|-------|---------|
| test_workstation_cadence.py | 21 | Polling intervals |
| test_grpc_server.py | 12 | gRPC server |
| Total | 786+ | All passing |

---

## Known Issues

### Resolved This Session
1. **WinRM 401 with svc.monitoring** - Use Administrator credentials
2. **WinRM 413 payload too large** - HTTP server file transfer

### Pending
1. **ISO v35 deployment** - Blocked by network (user on different WiFi)
2. **End-to-end gRPC test** - Waiting for ISO v35 on appliance

---

## Quick Commands

```bash
# SSH to appliance
ssh root@192.168.88.246

# SSH to iMac
ssh jrelly@192.168.88.50

# Transfer ISO when back on network
scp /tmp/osiriscare-appliance-v35.iso jrelly@192.168.88.50:~/Downloads/

# Check chaos lab logs
ssh jrelly@192.168.88.50 "cat ~/chaos-lab/logs/cadence.log"

# Run tests locally
cd packages/compliance-agent && source venv/bin/activate && python -m pytest tests/ -v

# WinRM to NVWS01
# Use Administrator credentials (NORTHVALLEY\Administrator)
```

---

## Architecture Reference

```
Go Agent (NVWS01)              Appliance (ISO v35)
┌─────────────────┐           ┌─────────────────────┐
│ osiris-agent    │  gRPC     │  Python Agent       │
│ - 6 WMI checks  │──────────►│  - gRPC Server      │
│ - SQLite queue  │  :50051   │  - AgentRegistry    │
│ - RMM detect    │           │  - Three-tier heal  │
└─────────────────┘           └─────────────────────┘
         │                              │
         │ Push drift events            │ Healing actions
         ▼                              ▼
    [Compliance checks]           [L1/L2/L3 auto-heal]
```

---

**For new AI sessions:**
1. Read `.agent/CONTEXT.md` for full state
2. Read `.agent/TODO.md` for current priorities
3. Check this file for handoff details
