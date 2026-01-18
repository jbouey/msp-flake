# Session Handoff - MSP Compliance Platform

**Last Updated:** 2026-01-17 (Session 53)
**Current State:** Go Agent Deployed to NVWS01, gRPC Server Bugs Fixed

---

## Quick Status

| Component | Status | Version |
|-----------|--------|---------|
| Agent | v1.0.42 | Stable |
| ISO | v40 | **DEPLOYED** - gRPC working |
| Tests | 811 + 24 Go tests | Healthy |
| Go Agent | **DEPLOYED to NVWS01** | PID 7804, Scheduled Task |
| gRPC | **3 BUG FIXES** | Import, Event Loop, Method Signature |
| Chaos Lab | **v2 Multi-VM** | Ready |
| Active Healing | **ENABLED** | HEALING_DRY_RUN=false |
| L1 Rules | 21 (full coverage) | Platform-specific |
| Security Audit | **COMPLETE** | 13 fixes |
| Healing Tier Toggle | **COMPLETE** | standard/full_coverage |

---

## Session 53 Summary (2026-01-17)

### Completed

#### 1. Workstation Credential Type Fix
- **Issue:** NVWS01 workstation not showing in site appliance despite being in database
- **Root Cause:** `domain_member` credential type wasn't in allowed SQL query types
- **Fix:** Added `domain_member` to both `mcp-server/main.py` and `mcp-server/central-command/backend/sites.py`
- **Credential:** localadmin / NorthValley2024!

#### 2. Go Agent Deployment to NVWS01
- **Method:** WinRM from appliance (local machine lacked pywinrm)
- **Binary:** `osiris-agent.exe` (16.6MB) uploaded to appliance web server
- **Installation Path:** `C:\Program Files\OsirisCare\osiris-agent.exe`
- **Config Path:** `C:\ProgramData\OsirisCare\config.json`
- **Attempts:**
  1. Windows Service - FAILED (Error 1053, no SCM integration in binary)
  2. NSSM - FAILED (nssm.cc website down)
  3. **Scheduled Task - SUCCESS** (runs at logon + every 5 minutes)
- **Status:** Running as PID 7804

#### 3. gRPC Server Bug Fixes (3 Critical)
| Bug | Location | Issue | Fix |
|-----|----------|-------|-----|
| Import Error | Line 232 | `from .models import Incident` | `from .incident_db import Incident` |
| Event Loop Error | Lines 248-257 | `asyncio.get_event_loop()` fails in thread pool | `asyncio.run()` with fallback |
| Method Signature | `_async_heal()` | `heal(incident)` not valid | `heal(site_id, host_id, incident_type, severity, raw_data)` |

#### 4. NixOS Hot-Patch via Bind Mount
- **Problem:** NixOS Nix store is read-only, cannot modify files directly
- **Solution:**
  1. Created patched file at `/var/lib/compliance-agent/patch/grpc_server.py`
  2. Used bind mount to overlay the Nix store file:
     ```bash
     mount --bind /var/lib/compliance-agent/patch/grpc_server.py \
       /nix/store/.../grpc_server.py
     ```
  3. Restarted compliance-agent service
- **Note:** Not permanent - needs ISO v42 build for permanent fix

### Files Modified This Session
| File | Change |
|------|--------|
| `packages/compliance-agent/src/compliance_agent/grpc_server.py` | 3 bug fixes |
| `packages/compliance-agent/setup.py` | Version bump to 1.0.42 |
| `mcp-server/main.py` | Added `domain_member` credential type |
| `mcp-server/central-command/backend/sites.py` | Added `domain_member` credential type |
| `.agent/TODO.md` | Session 53 details |
| `.agent/CONTEXT.md` | Updated header |

---

## Infrastructure State

### Physical Appliance (192.168.88.246)
- **Status:** Online, running ISO v40 with hot-patch
- **Agent:** v1.0.42 (patched)
- **Active Healing:** ENABLED
- **Hot-Patch:** `/var/lib/compliance-agent/patch/grpc_server.py` bind-mounted

### VM Appliance (192.168.88.247)
- **Status:** Online, running ISO v40
- **gRPC:** Verified working

### Windows Infrastructure
| Machine | IP | Go Agent | Status |
|---------|-----|----------|--------|
| NVWS01 | 192.168.88.251 | **DEPLOYED** | PID 7804, Scheduled Task |
| NVDC01 | 192.168.88.250 | - | Domain Controller |
| NVSRV01 | 192.168.88.244 | - | Server Core |

### VPS (178.156.162.116)
- **Status:** Online
- **Needs:** ISO v42 build with gRPC fixes

---

## Next Session Priorities

### 1. Build ISO v42
```bash
ssh root@178.156.162.116
cd /root/msp-iso-build && git pull
nix build .#appliance-iso -o result-iso-v42
```

### 2. Verify Go Agent Communication
- Check if drift events from NVWS01 are flowing to appliance
- Verify L1/L2 healing triggered from Go Agent events

### 3. Make Hot-Patch Permanent
- ISO v42 will include the gRPC fixes
- Flash to physical appliance to remove bind mount workaround

### 4. Run Tests
```bash
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate && python -m pytest tests/ -v --tb=short
```

---

## Quick Commands

```bash
# SSH to appliances
ssh root@192.168.88.246   # Physical appliance
ssh root@192.168.88.247   # VM appliance

# SSH to VPS
ssh root@178.156.162.116

# SSH to iMac
ssh jrelly@192.168.88.50

# Check agent status
ssh root@192.168.88.246 "journalctl -u compliance-agent -n 50"

# Check Go Agent on NVWS01 (via WinRM from appliance)
ssh root@192.168.88.246 "python3 -c \"
import winrm
s = winrm.Session('192.168.88.251', auth=('localadmin', 'NorthValley2024!'))
r = s.run_cmd('tasklist /fi \"imagename eq osiris-agent.exe\"')
print(r.std_out.decode())
\""

# Run tests locally
cd packages/compliance-agent && source venv/bin/activate && python -m pytest tests/ -v

# Git commit
git add -A && git commit -m "feat: Go Agent deployment and gRPC fixes (Session 53)"
```

---

## Known Issues

### L1 Rule `RB-AUTO-FIREWALL` Too Broad
- Currently matches ALL incident types
- Needs type-specific L1 rules for proper incident routing
- Low priority - healing still works, just not optimal routing

### Go Agent Windows Service
- Binary lacks Windows SCM integration
- Workaround: Scheduled Task (works well)
- Future: Add proper service support to Go agent

---

**For new AI sessions:**
1. Read `.agent/CONTEXT.md` for full state
2. Read `.agent/TODO.md` for current priorities
3. Check this file for handoff details
