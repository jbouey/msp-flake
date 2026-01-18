# Current Tasks & Priorities

**Last Updated:** 2026-01-18 (Session 54 - Complete)
**Sprint:** Phase 13 - Zero-Touch Update System DEPLOYED (Agent v1.0.43, ISO v43, Fleet Updates UI, Healing Tier Toggle, Rollout Management)

---

## Session 54 (2026-01-18) - Phase 13 Fleet Updates Deployed - COMPLETE

### Completed This Session

#### 1. Fleet Updates UI Deployed and Tested
**Status:** COMPLETE
- Navigated to dashboard.osiriscare.net/fleet-updates
- Stats cards showing: Latest Version, Active Releases, Active Rollouts, Pending Updates
- "New Release" button creates releases with version, ISO URL, SHA256, agent version, notes
- "Set as Latest" button to mark a release as the fleet default
- All features verified working in production

#### 2. Test Release v44 Created
**Status:** COMPLETE
- Created release v44 via Fleet Updates UI
- ISO URL: https://updates.osiriscare.net/v44.iso
- SHA256 checksum provided
- Agent version: 1.0.44
- Set as "Latest" version

#### 3. Rollout Management Tested
**Status:** COMPLETE
- Started staged rollout for v44 (5% → 25% → 100%)
- **Pause/Resume:** Working - tested pause and resume of rollout
- **Advance Stage:** Working - advanced from Stage 1 (5%) to Stage 2 (25%)
- Rollout data persisted correctly in database with all fields

#### 4. Healing Tier Toggle Verified
**Status:** COMPLETE
- Site Detail page shows "Healing Mode" dropdown
- Options: Standard (4 rules), Full Coverage (21 rules)
- **Bug Fixed:** `sites.py` missing `List` import causing container crash
- **API Verified:** PUT /api/sites/{site_id}/healing-tier working
- Round-trip tested: Full Coverage → Standard → verified in database

#### 5. Bug Fixes
**Status:** COMPLETE
- **Fixed:** `fleetApi` duplicate declaration in api.ts - renamed to `fleetUpdatesApi`
- **Fixed:** `List` not imported in sites.py - added to typing imports
- Both fixes deployed to VPS

### Files Modified This Session
| File | Change |
|------|--------|
| `mcp-server/central-command/backend/sites.py` | Added `List` to typing imports |
| `mcp-server/central-command/frontend/src/utils/api.ts` | Renamed update API to `fleetUpdatesApi` |
| `mcp-server/central-command/frontend/src/pages/FleetUpdates.tsx` | Updated to use `fleetUpdatesApi` |

---

## Session 53 (2026-01-17/18) - Go Agent Deployment & gRPC Fixes - COMPLETE

### Completed

#### 1. Go Agent Deployment to NVWS01
- Uploaded `osiris-agent.exe` (16.6MB) to appliance web server
- Deployed to NVWS01 (192.168.88.251) via WinRM from appliance
- Installed as Windows scheduled task (runs as SYSTEM at startup)
- Agent running and sending drift events

#### 2. gRPC Integration Verified WORKING
- Go agent connects to appliance on port 50051
- Drift events received and processed:
  - `NVWS01/firewall passed=False` → L1-FIREWALL-001 → RB-WIN-FIREWALL-001 ✅
  - `NVWS01/defender passed=False` → L1-DEFENDER-001 → RB-WIN-SEC-006 ✅
  - `NVWS01/bitlocker passed=False` → L1-BITLOCKER-001 ✅
  - `NVWS01/screenlock passed=False` → L1-SCREENLOCK-001 ✅

#### 3. L1 Rule Matching Fix
- Added `"status": "fail"` to incident raw_data in grpc_server.py
- Removed bad `RB-AUTO-FIREWALL` rule (had empty conditions)
- Added proper L1 rules for Go Agent check types

#### 4. Zero-Friction Updates Documentation
- Created `docs/ZERO_FRICTION_UPDATES.md` - Phase 13 architecture

---

## Next Session Priorities

### 1. Phase 13: A/B Partition Implementation
**Status:** PLANNED (Central Command UI complete)
**Details:**
- Implement A/B partition scheme in appliance ISO
- Update agent for partition-aware updates
- Boot health gate service
- Auto-rollback mechanism

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
