# Session Handoff - 2026-01-16

**Session:** 44 - Go Agent Testing & ISO v37
**Agent Version:** v1.0.37
**ISO Version:** v37 (built, on iMac at ~/osiriscare-v37.iso)
**Last Updated:** 2026-01-16

---

## Session 44 Accomplishments

### 1. Go Agent Config on NVWS01
**Status:** COMPLETE
- Created `C:\ProgramData\OsirisCare\config.json` on NVWS01 workstation
- Config: `{"appliance_addr": "192.168.88.247:50051", "data_dir": "C:\\ProgramData\\OsirisCare"}`
- Used localadmin / NorthValley2024! credentials (from LAB_CREDENTIALS.md)

### 2. Firewall Port 50051 Fix
**Status:** COMPLETE
**Root Cause:** NixOS firewall only allowed ports 80, 22, 8080 - NOT 50051 for gRPC
**Hot-fix:** `iptables -I nixos-fw 8 -p tcp --dport 50051 -j nixos-fw-accept` on running VM
**Permanent fix:** Updated `iso/appliance-image.nix`:
```nix
allowedTCPPorts = [ 80 22 8080 50051 ];  # Status + SSH + Sensor API + gRPC
```

### 3. Go Agent Dry-Run Testing
**Status:** COMPLETE
**Results on NVWS01:**
| Check | Status | Notes |
|-------|--------|-------|
| screenlock | ✅ PASS | Timeout ≤ 600s |
| rmm_detection | ✅ PASS | No RMM detected |
| bitlocker | ❌ FAIL | No encrypted volumes |
| defender | ❌ FAIL | Real-time protection off |
| firewall | ❌ FAIL | Not all profiles enabled |
| patches | ❌ ERROR | WMI error |

### 4. Go Agent Code Audit
**Status:** COMPLETE
**Findings:**
- Clean, well-organized code structure
- 6 HIPAA compliance checks working
- **Issues Identified:**
  1. SQLite offline queue uses `mattn/go-sqlite3` which requires CGO
     - Fails with `CGO_ENABLED=0` (current build setting)
     - Fix: Switch to `modernc.org/sqlite` (pure Go) or enable CGO
  2. gRPC methods are stubs - actual streaming not implemented yet

### 5. Chaos Lab Config Path Bug Fix
**Status:** COMPLETE
**File:** `~/chaos-lab/scripts/winrm_attack.py` (on iMac)
**Issue:** When called via symlink, `__file__` resolves incorrectly
**Fix:** Use `os.path.realpath(__file__)` and handle both direct and symlink calls

### 6. ISO v37 Build & Transfer
**Status:** COMPLETE
- Built on VPS with `nix build .#appliance-iso -o result-iso`
- Version: Agent v1.0.37
- Features: Port 50051 in firewall, grpcio dependencies
- Transferred to iMac: `~/osiriscare-v37.iso` (1.0G)

### 7. Production Push
**Status:** COMPLETE
**Commit:** `50f5f86`
**VPS Synced:** `git fetch && git reset --hard origin/main`

---

## Files Modified This Session

### Modified:
| File | Changes |
|------|---------|
| `iso/appliance-image.nix` | Added port 50051 to firewall rules |
| `~/chaos-lab/scripts/winrm_attack.py` (iMac) | Fixed config path resolution for symlinks |

### Created on NVWS01:
| File | Purpose |
|------|---------|
| `C:\ProgramData\OsirisCare\config.json` | Go Agent configuration |

---

## Known Issues

### 1. Go Agent CGO Dependency
- `mattn/go-sqlite3` requires CGO_ENABLED=1
- Current builds use CGO_ENABLED=0
- **Workaround:** SQLite queue not functional until fixed
- **Fix:** Switch to `modernc.org/sqlite` (pure Go)

### 2. gRPC Streaming Not Implemented
- Go Agent gRPC methods are stubs
- Python gRPC server also has stub servicer
- **Impact:** Full push-based communication not yet working

---

## Next Session Tasks

1. **Flash ISO v37 to Physical Appliance**
   - Location: `~/osiriscare-v37.iso` on iMac
   - Target: 192.168.88.246

2. **Test Go Agent gRPC (Real Mode)**
   - Run Go Agent without `-dry-run` on NVWS01
   - Verify gRPC connection to appliance port 50051
   - Monitor agent logs on appliance

3. **Fix Go Agent CGO Issue**
   - Replace `mattn/go-sqlite3` with `modernc.org/sqlite`
   - Rebuild Go Agent binaries

4. **Implement gRPC Streaming**
   - Wire up Python gRPC servicer
   - Implement Go Agent streaming client

---

## Lab Environment Status

### VMs (on iMac 192.168.88.50)
| VM | IP | Status | Notes |
|----|-----|--------|-------|
| NVDC01 | 192.168.88.250 | ✅ Online | Domain Controller |
| NVWS01 | 192.168.88.251 | ✅ Online | Windows 10 Workstation, Go Agent installed |
| NVSRV01 | 192.168.88.244 | ✅ Online | Windows Server Core |
| osiriscare-appliance (VM) | 192.168.88.247 | ✅ Online | Running ISO v36 + hot-fix |
| osiriscare-appliance (Physical) | 192.168.88.246 | ✅ Online | HP T640, needs ISO v37 |

### Go Agent Deployment
- **Binary:** `C:\OsirisCare\osiris-agent.exe` on NVWS01
- **Config:** `C:\ProgramData\OsirisCare\config.json`
- **Appliance Endpoint:** `192.168.88.247:50051`

---

## Quick Commands

```bash
# SSH to VM appliance
ssh root@192.168.88.247

# Check firewall rules on appliance
iptables -L nixos-fw -n --line-numbers | grep 50051

# Test gRPC port from NVWS01 (PowerShell)
Test-NetConnection -ComputerName 192.168.88.247 -Port 50051

# Watch agent logs on appliance
journalctl -u compliance-agent -f

# Run Go Agent (dry-run)
C:\OsirisCare\osiris-agent.exe -dry-run

# Run Go Agent (real mode)
C:\OsirisCare\osiris-agent.exe

# SSH to VPS
ssh root@178.156.162.116

# Flash ISO to USB (on Mac)
sudo dd if=~/osiriscare-v37.iso of=/dev/diskN bs=4m status=progress
```

---

## Related Docs

- `.agent/TODO.md` - Session tasks
- `.agent/CONTEXT.md` - Project context
- `.agent/LAB_CREDENTIALS.md` - Lab passwords
- `IMPLEMENTATION-STATUS.md` - Overall status
- `agent/README.md` - Go Agent documentation
