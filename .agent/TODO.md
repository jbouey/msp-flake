# Current Tasks & Priorities

**Last Updated:** 2026-01-18 (Session 53 - Go Agent Deployment & gRPC Fixes)
**Sprint:** Phase 12 - Launch Readiness (Agent v1.0.42, ISO v40, 43 Runbooks, Go Agent Deployed to NVWS01, gRPC Integration Complete)

---

## Session 53 (2026-01-17/18) - Go Agent Deployment & gRPC Fixes

### 1. Go Agent Deployment to NVWS01
**Status:** COMPLETE
**Details:**
- Uploaded `osiris-agent.exe` (16.6MB) to appliance web server
- Deployed to NVWS01 (192.168.88.251) via WinRM from appliance
- Installed as Windows scheduled task (runs as SYSTEM at startup)
- Agent running with PID 7804, using 9.66MB RAM
- Config at `C:\ProgramData\OsirisCare\config.json`

### 2. gRPC Connection Verified
**Status:** COMPLETE
**Details:**
- Go agent connects to appliance on port 50051
- TCP connection verified via `Test-NetConnection`
- Drift events being received every 5 minutes:
  - `NVWS01/firewall passed=False`
  - `NVWS01/bitlocker passed=False`
  - `NVWS01/defender passed=False`
  - `NVWS01/screenlock passed=False`

### 3. grpc_server.py Bug Fixes
**Status:** COMPLETE
**Details:**
| Bug | Root Cause | Fix |
|-----|------------|-----|
| Import error | `from .models import Incident` | Changed to `from .incident_db import Incident` |
| Event loop error | `asyncio.get_event_loop()` in thread pool | Changed to `asyncio.run()` with fallback |
| Method signature | `heal(incident)` object | Changed to `heal(site_id, host_id, incident_type, severity, raw_data)` |

### 4. Hot-Patch Applied to Appliance
**Status:** COMPLETE
**Details:**
- Created patched copy at `/var/lib/compliance-agent/patch/grpc_server.py`
- Applied bind mount over Nix store file
- Restarted compliance-agent service
- Verified drift events flow to healing pipeline:
  ```
  Go agent drift: NVWS01/firewall passed=False
  Processing incident INC-20260118042242-593991-8ae7 (firewall/high)
  L1 rule matched: RB-AUTO-FIREWALL -> run_runbook:AUTO-FIREWALL
  ```

### 5. Workstation Credential Fix
**Status:** COMPLETE (from Session 52 continuation)
**Details:**
- Added `domain_member` to credential type filter in `main.py` and `sites.py`
- Updated workstation credential in database with correct password (`localadmin`/`NorthValley2024!`)
- WinRM compliance checks now work on NVWS01

### Files Modified This Session
| File | Change |
|------|--------|
| `packages/compliance-agent/src/compliance_agent/grpc_server.py` | Import fix, event loop fix, method signature fix |
| `packages/compliance-agent/setup.py` | Version bump to 1.0.42 |
| `mcp-server/main.py` | Added `domain_member` to credential types |
| `mcp-server/central-command/backend/sites.py` | Added `domain_member` to credential types |

### Remaining Tasks
1. **Build ISO v42** - Make grpc_server fixes permanent
2. **Fix L1 Rule Matching** - `RB-AUTO-FIREWALL` matches all check types, needs type-specific rules
3. **Verify Workstations in Central Command** - After registration, workstations should appear

---

## Session 52 (2026-01-17) - Security Audit & Healing Tier Toggle

### 1. Healing Tier Toggle (Central Command Integration)
**Status:** COMPLETE
**Details:**
- Created database migration `021_healing_tier.sql` - Added `healing_tier` column to sites table
- Added API endpoints in `sites.py`:
  - `GET /api/sites/{site_id}/healing-tier` - Get current tier
  - `PUT /api/sites/{site_id}/healing-tier` - Update tier (standard/full_coverage)
- Added frontend UI toggle in `SiteDetail.tsx` (toggle switch under Appliances section)
- Added `useUpdateHealingTier` hook in `useFleet.ts`
- Updated `appliance_client.py` to sync tier-specific rules on check-in

### 2. Comprehensive Security Audit
**Status:** COMPLETE
**Details:** Identified 13 critical security vulnerabilities and fixed all of them.

---

## Immediate (Next Session)

### 1. Build ISO v42
**Status:** PENDING
**Command:**
```bash
ssh root@178.156.162.116
cd /root/msp-iso-build
nix build .#appliance-iso -o result-iso-v42
```

### 2. Fix L1 Rule Configuration
**Status:** PENDING
**Details:**
- Current issue: `RB-AUTO-FIREWALL` rule matches ALL incident types
- Need type-specific rules for each check type
- Rules file: `/var/lib/msp/rules/l1_rules.json`

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

**Git commit (Session 53):**
```bash
git add -A && git commit -m "feat: Go Agent deployment to NVWS01 and gRPC fixes (Session 53)"
```
