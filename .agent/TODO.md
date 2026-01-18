# Current Tasks & Priorities

**Last Updated:** 2026-01-18 (Session 53 - Complete)
**Sprint:** Phase 12 - Launch Readiness (Agent v1.0.43, ISO v43, 43 Runbooks, Go Agent Deployed to NVWS01, gRPC Integration Complete)

---

## Session 53 (2026-01-17/18) - Go Agent Deployment & gRPC Fixes - COMPLETE

### Completed This Session

#### 1. Go Agent Deployment to NVWS01
**Status:** COMPLETE
- Uploaded `osiris-agent.exe` (16.6MB) to appliance web server
- Deployed to NVWS01 (192.168.88.251) via WinRM from appliance
- Installed as Windows scheduled task (runs as SYSTEM at startup)
- Agent running and sending drift events

#### 2. gRPC Integration Verified WORKING
**Status:** COMPLETE
- Go agent connects to appliance on port 50051
- Drift events received and processed:
  - `NVWS01/firewall passed=False` → L1-FIREWALL-001 → RB-WIN-FIREWALL-001 ✅
  - `NVWS01/defender passed=False` → L1-DEFENDER-001 → RB-WIN-SEC-006 ✅
  - `NVWS01/bitlocker passed=False` → L1-BITLOCKER-001 ✅
  - `NVWS01/screenlock passed=False` → L1-SCREENLOCK-001 ✅

#### 3. L1 Rule Matching Fix
**Status:** COMPLETE
- **Root Cause:** Go Agent incidents missing `status` field required by L1 rules
- **Fix:** Added `"status": "fail"` to incident raw_data in grpc_server.py
- **Also Fixed:** Removed bad `RB-AUTO-FIREWALL` rule (had empty conditions, matched ALL incidents)
- **Added:** Proper L1 rules for Go Agent check types (L1-DEFENDER-001, L1-BITLOCKER-001, L1-SCREENLOCK-001)

#### 4. ISO v43 Built and Deployed
**Status:** COMPLETE
- Built on VPS: `/root/msp-iso-build/result-iso-v43/iso/osiriscare-appliance.iso`
- Transferred to iMac via relay
- Physical appliance (192.168.88.246) reflashed and running v1.0.43
- Internal SSD corruption from earlier dd operation fixed by user

#### 5. Zero-Friction Updates Documentation
**Status:** COMPLETE
- Created `docs/ZERO_FRICTION_UPDATES.md` - Phase 13 architecture
- A/B partition scheme for appliances
- Remote ISO deployment via Central Command
- Auto-rollback on failed updates
- Database schema for update_releases, update_rollouts, appliance_updates
- Rollout stages: Canary (5%) → Early Adopters (25%) → Full Fleet (100%)

### Files Modified This Session
| File | Change |
|------|--------|
| `packages/compliance-agent/src/compliance_agent/grpc_server.py` | Added `status: "fail"` to incident raw_data |
| `packages/compliance-agent/setup.py` | Version bump to 1.0.43 |
| `docs/ZERO_FRICTION_UPDATES.md` | NEW - Phase 13 zero-touch update architecture |

---

## Next Session Priorities

### 1. Phase 13: Zero-Touch Update System
**Status:** PLANNED (docs ready at `docs/ZERO_FRICTION_UPDATES.md`)
**Details:**
- Implement A/B partition scheme for appliances
- Remote ISO deployment via Central Command
- Auto-rollback on failed updates
- Database migrations for update management

### 2. Fix VPS 502 Error
**Status:** PENDING
**Details:**
- Evidence submission returning 502
- Need to investigate Central Command backend logs

### 3. Deploy Security Fixes to VPS
**Status:** PENDING
**Details:**
- Run database migration 021_healing_tier.sql
- Set required env vars: `SESSION_TOKEN_SECRET`, `API_KEY_SECRET`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`

---

## Quick Reference

**Run tests:**
```bash
cd /Users/dad/Documents/Msp_Flakes/packages/compliance-agent
source venv/bin/activate
python -m pytest tests/ -v --tb=short
```

**SSH to VPS:**
```bash
ssh root@178.156.162.116
```

**SSH to Physical Appliance:**
```bash
ssh root@192.168.88.246
```

**SSH to iMac Gateway:**
```bash
ssh jrelly@192.168.88.50
```

**Go Agent on NVWS01:**
```bash
# Check status via WinRM from appliance
Get-ScheduledTask -TaskName "OsirisCareAgent"
Get-Process -Name "osiris-agent"
```
