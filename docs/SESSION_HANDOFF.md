# Session Handoff - MSP Compliance Platform

**Last Updated:** 2026-01-17 (Session 46)
**Current State:** L1 Platform-Specific Healing Fix Complete

---

## Quick Status

| Component | Status | Version |
|-----------|--------|---------|
| Agent | v1.0.37 | Stable |
| ISO | v37 | Built, on iMac |
| Tests | 811 passed | Healthy |
| Go Agent | Deployed to NVWS01 | Needs rebuild |
| gRPC | Fully Implemented | Both Python + Go |
| Chaos Lab | Verified | L1 healing working |
| L1 Rules | Platform-specific | 7 rules in l1_baseline.json |

---

## Session 46 Summary (2026-01-17)

### Completed
1. **NixOS Firewall Platform-Specific Rule** - Escalates to L3 instead of trying Windows runbook
2. **L1 Rules Action Format Fix** - Changed to proper action_params structure
3. **Defender Runbook ID Fix** - RB-WIN-SEC-006 -> RB-WIN-AV-001
4. **L1 Rules Saved to Codebase** - l1_baseline.json with 7 rules
5. **Chaos Lab Verification** - Firewall and Defender attacks healed successfully

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

## Next Session (47) Priorities

### L1 Rules Expansion
1. Add L1 rule for `password_policy` check type → RB-WIN-SEC-003
2. Add L1 rule for `audit_policy` check type → RB-WIN-SEC-002
3. Create runbooks for password/audit policy if not exists in ALL_RUNBOOKS

### Go Agent Integration
1. Test Go Agent gRPC communication (without --dry-run)
2. Verify drift events flowing to appliance
3. Monitor AgentRegistry for connected agents

### Verification Steps
- Check chaos lab logs: `~/chaos-lab/logs/cadence.log`
- Verify gRPC server on port 50051
- Run chaos lab with password/audit policy attacks after adding rules

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
