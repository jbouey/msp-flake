# Current Tasks & Priorities

**Last Updated:** 2026-01-15 (Session 39 - $params_Hostname Bug Fix)
**Sprint:** Phase 12 - Launch Readiness (Agent v1.0.34, ISO v33, 43 Runbooks, OTS Anchoring, Linux+Windows Support, Windows Sensors, Partner Escalations, RBAC, Multi-Framework, Cloud Integrations, Microsoft Security Integration, L1 JSON Rule Loading, Chaos Lab Automated, Network Compliance Check, Extended Check Types, Workstation Compliance, RMM Comparison Engine, Workstation Discovery Config, **$params_Hostname Fix**)

---

## Session 39 (2026-01-15) - $params_Hostname Bug Fix + ISO v33 Deployment

### 1. $params_Hostname Variable Injection Bug Fix
**Status:** COMPLETE
**Root Cause:** WindowsExecutor.run_script() injects variables with `$params_` prefix, but workstation scripts used bare `$Hostname`.
**Files Modified:**
- `packages/compliance-agent/src/compliance_agent/workstation_discovery.py` - Changed all 3 check scripts:
  - `PING_CHECK_SCRIPT`: `$Hostname` → `$params_Hostname`
  - `WMI_CHECK_SCRIPT`: `$Hostname` → `$params_Hostname`
  - `WINRM_CHECK_SCRIPT`: `$Hostname` → `$params_Hostname`
- `packages/compliance-agent/setup.py` - Version 1.0.34

### 2. ISO v33 Built and Deployed
**Status:** COMPLETE
**Details:**
- Built on VPS: `/root/msp-iso-build/result-v33/iso/osiriscare-appliance.iso`
- Downloaded to MacBook: `/tmp/osiriscare-appliance-v33.iso`
- Copied to iMac: `~/Downloads/osiriscare-appliance-v33.iso`
- Physical appliance flashed with ISO v33

### 3. Workstation Discovery Testing
**Status:** PARTIAL (blocked by DC)
**Results:**
- ✅ Direct WinRM to NVWS01 from VM appliance: WORKS (returned "Hostname: NVWS01")
- ✅ AD enumeration from DC: WORKS (found NVWS01 at 192.168.88.251)
- ❌ Test-NetConnection from DC: TIMED OUT (DC was restoring from chaos lab snapshot)

### 4. Documentation Created
**Status:** COMPLETE
**Files:**
- `.agent/PROJECT_SUMMARY.md` - New comprehensive project documentation
- `CLAUDE.md` - Updated with current version (v1.0.34) and test count (778+)

### 5. Git Commits Pushed
- `4db0207` - fix: Use $params_Hostname for workstation online detection
- `2b245b6` - docs: Add PROJECT_SUMMARY.md and update CLAUDE.md
- `5c6c5c5` - docs: Update claude.md with current version and project summary link

### 6. Known Issue: Overlay Module Import
**Status:** OPEN
**Error:** `ModuleNotFoundError: No module named 'compliance_agent.appliance_agent'`
**Location:** `/var/lib/msp/run_agent_overlay.py` on appliance
**Notes:** Overlay mechanism needs to properly include appliance_agent module.

---

## Session 38 (2026-01-15) - Workstation Discovery Config

### 1. Workstation Discovery Config Fields
**Status:** COMPLETE
**Files Modified:**
- `packages/compliance-agent/src/compliance_agent/appliance_config.py` - Added:
  - `workstation_enabled` (bool)
  - `domain_controller` (str)
  - `dc_username` (str)
  - `dc_password` (str)
- `packages/compliance-agent/src/compliance_agent/appliance_agent.py` - Updated `_get_dc_credentials()`
- `packages/compliance-agent/setup.py` - Version 1.0.33

### 2. NVWS01 WinRM Connectivity
**Status:** COMPLETE
**Details:** User manually enabled WinRM on NVWS01 workstation VM.

### 3. WinRM Port Check for Online Detection
**Status:** COMPLETE
**File:** `packages/compliance-agent/src/compliance_agent/workstation_discovery.py`
**Changes:**
- Added `WINRM_CHECK_SCRIPT` using Test-NetConnection port 5985
- Changed default method from "ping" to "winrm"

### 4. ISO v33 Build
**Status:** COMPLETE (see Session 39)

### 5. Git Commits Pushed
- `c37abf1` - feat: Add workstation discovery config to appliance agent
- `13f9165` - feat: Add WinRM port check for workstation online detection

---

## Session 37 (2026-01-15) - Microsoft Security OAuth Fixes

### 1. OAuth Integration Complete
**Status:** COMPLETE
**Details:** Fixed multiple OAuth bugs for Microsoft Security integration sync.

---

## Session 36 (2026-01-15) - RMM Comparison Engine

### 1. RMM Comparison Engine
**Status:** COMPLETE
**File:** `packages/compliance-agent/src/compliance_agent/rmm_comparison.py`
**Details:** Compare AD-discovered workstations with external RMM tool data for deduplication.

**Features:**
- Multi-field matching (hostname, IP, MAC, serial)
- Confidence scoring (exact, high, medium, low)
- Gap analysis (missing from RMM, missing from AD, stale entries)
- Deduplication recommendations with priority
- Provider loaders for ConnectWise, Datto, NinjaRMM, Syncro
- CSV import support

**Data Classes:**
- `RMMDevice` - Normalized device from any RMM
- `DeviceMatch` - Match result with confidence
- `CoverageGap` - Gap with recommendation
- `ComparisonReport` - Full comparison report

### 2. RMM Comparison Tests
**Status:** COMPLETE
**File:** `packages/compliance-agent/tests/test_rmm_comparison.py`
**Details:** 24 tests covering all comparison scenarios.

### 3. Backend API Endpoints
**Status:** COMPLETE
**File:** `mcp-server/central-command/backend/sites.py`
**Endpoints:**
- `POST /api/sites/{site_id}/workstations/rmm-compare` - Compare with RMM data
- `GET /api/sites/{site_id}/workstations/rmm-compare` - Get latest report

### 4. Database Migration
**Status:** COMPLETE
**File:** `mcp-server/central-command/backend/migrations/018_rmm_comparison.sql`
**Tables:**
- `rmm_comparison_reports` - Latest comparison per site
- `rmm_comparison_history` - Historical trend data

### Test Results
```
778 passed, 7 skipped, 3 warnings
24 RMM comparison tests passing
```

---

## Session 35 (2026-01-15) - Microsoft Security Integration + Delete Button UX

### 1. Microsoft Security Integration (Phase 3)
**Status:** COMPLETE
**Files:**
- `integrations/oauth/microsoft_graph.py` - Backend (893 lines)
- `frontend/src/pages/IntegrationSetup.tsx` - Provider selection
- `frontend/src/pages/SiteDetail.tsx` - Cloud Integrations button

**Features:**
- Defender alerts collection with severity/status analysis
- Intune device compliance and encryption status
- Microsoft Secure Score posture data
- Azure AD devices for trust/compliance correlation
- HIPAA control mappings for all resource types

### 2. VPS Deployment Fixes
**Status:** COMPLETE
**Issues Fixed:**
- Added `microsoft_security` to database `valid_provider` constraint
- Fixed OAuth redirect URI to force HTTPS
- Fixed Caddy routing for `/api/*` through dashboard domain
- Fixed Caddyfile to use `mcp-server` container (not `msp-server`)
- Created OAuth callback public router (no auth required for browser redirect)

### 3. Delete Button UX Fix
**Status:** COMPLETE
**File:** `frontend/src/pages/Integrations.tsx`
**Changes:**
- Added `deletingId` state tracking in parent component
- Shows "Deleting..." feedback during delete operation
- Disables all buttons while delete is in progress
- Resets confirmation state on error for retry

### 4. Deployment Infrastructure
**Status:** COMPLETE
**Files Created:**
- `/opt/mcp-server/deploy.sh` - VPS deployment script
- `.agent/VPS_DEPLOYMENT.md` - Deployment documentation

**Quick Deploy:**
```bash
ssh root@api.osiriscare.net "/opt/mcp-server/deploy.sh"
```

### 5. Azure App Registration (User Action Required)
**Status:** PENDING USER ACTION
**Instructions:**
1. Go to Azure Portal → App registrations
2. Add redirect URI: `https://dashboard.osiriscare.net/api/integrations/oauth/callback`
3. Add API permissions (SecurityEvents, DeviceManagement, Device, SecurityActions)
4. Create new client secret and use the **VALUE** (not ID)
5. Grant admin consent

---

## Session 33 (2026-01-14) - Phase 1 Workstation Coverage

### 1. Workstation Discovery Module
**Status:** COMPLETE
**File:** `packages/compliance-agent/src/compliance_agent/workstation_discovery.py`
**Details:** AD enumeration via PowerShell Get-ADComputer, online status checking, caching.

### 2. Workstation Compliance Checks
**Status:** COMPLETE
**File:** `packages/compliance-agent/src/compliance_agent/workstation_checks.py`
**Details:** 5 WMI-based compliance checks:
- BitLocker encryption (§164.312(a)(2)(iv))
- Windows Defender (§164.308(a)(5)(ii)(B))
- Patch status (§164.308(a)(5)(ii)(B))
- Firewall status (§164.312(a)(1))
- Screen lock policy (§164.312(a)(2)(iii))

### 3. Workstation Evidence Generation
**Status:** COMPLETE
**File:** `packages/compliance-agent/src/compliance_agent/workstation_evidence.py`
**Details:** Per-workstation bundles + site-level summary, hash-chained, HIPAA control mapping.

### 4. Database Migration
**Status:** COMPLETE
**File:** `mcp-server/central-command/backend/migrations/017_workstations.sql`
**Tables:**
- `workstations` - Discovered workstations
- `workstation_checks` - Individual check results
- `workstation_evidence` - Evidence bundles
- `site_workstation_summaries` - Site-level aggregation
- Views: `v_site_workstation_status`, `v_workstation_latest_checks`

### 5. Agent Integration
**Status:** COMPLETE
**File:** `packages/compliance-agent/src/compliance_agent/appliance_agent.py`
**Changes:**
- Added `_maybe_scan_workstations()` method
- 2-phase: discovery (hourly) + compliance checks (10 min)
- Added `run_script()` method to WindowsExecutor
- Agent version bumped to v1.0.32

### 6. Tests
**Status:** COMPLETE
**File:** `packages/compliance-agent/tests/test_workstation_compliance.py`
**Coverage:** 20 tests passing (754 total)

### 7. Frontend Dashboard
**Status:** COMPLETE
**Files:**
- `frontend/src/pages/SiteWorkstations.tsx` - Main workstation dashboard page
- `frontend/src/utils/api.ts` - Added workstationsApi
- `frontend/src/hooks/useFleet.ts` - Added useSiteWorkstations, useTriggerWorkstationScan
- `frontend/src/hooks/index.ts` - Export workstation hooks
- `frontend/src/pages/index.ts` - Export SiteWorkstations page
- `frontend/src/App.tsx` - Added route `/sites/:siteId/workstations`
- `frontend/src/pages/SiteDetail.tsx` - Added "Workstations" button link

### 8. Backend API
**Status:** COMPLETE
**File:** `mcp-server/central-command/backend/sites.py`
**Endpoints:**
- `GET /api/sites/{site_id}/workstations` - List workstations with summary
- `GET /api/sites/{site_id}/workstations/{workstation_id}` - Workstation detail
- `POST /api/sites/{site_id}/workstations/scan` - Trigger workstation scan

### Development Roadmap
**Status:** COMPLETE
**File:** `.agent/DEVELOPMENT_ROADMAP.md`
**Details:** Full integration of user's 4-phase roadmap with gap analysis.

---

## Session 32 (2026-01-14) - Network Compliance + Extended Check Types

### 1. Network Compliance Check Integration
**Status:** COMPLETE
**Details:** Added Network compliance check across full stack (Drata/Vanta style).

#### Changes Made
- Backend `models.py`: Added `NETWORK = "network"` to CheckType enum
- Backend `metrics.py`: Updated `calculate_compliance_score()` to include network (7 metrics avg)
- Agent `appliance_agent.py`: Changed check_type from "network_posture_{os_type}" to "network"
- Frontend `types/index.ts`: Added 'network' to CheckType union and ComplianceMetrics
- Frontend `IncidentRow.tsx`: Added 'Network' label

#### Agent Version
- Bumped to v1.0.30 for ISO compatibility

### 2. Extended Check Type Labels
**Status:** COMPLETE
**Details:** Added frontend labels for all chaos probe/monitoring check types.

| Check Type | Label |
|------------|-------|
| ntp_sync | NTP |
| disk_space | Disk |
| service_health | Services |
| windows_defender | Defender |
| memory_pressure | Memory |
| certificate_expiry | Cert |
| database_corruption | Database |
| prohibited_port | Port |

### 3. Learning Flywheel Pattern Endpoints
**Status:** COMPLETE (deployed)
**Details:** Pattern reporting endpoints fully deployed to VPS.
- `/agent/patterns` - Agent pattern reporting
- `/patterns` - Dashboard pattern reporting
- Tier count query fix (`resolution_tier IS NOT NULL`)

### 4. Infrastructure Fixes
**Status:** COMPLETE
- Sensor registry FK constraint fix (VARCHAR match instead of strict FK)
- FrameworkConfig API parsing fix (extract frameworks object from response)
- Dockerfile: Added asyncpg + cryptography dependencies

### 5. Chaos Lab Enhancement
**Status:** COMPLETE
**Details:** Added second daily execution at 2 PM for more system stress testing.

**New Schedule (iMac 192.168.88.50):**
| Time | Task |
|------|------|
| 6:00 AM | Execute chaos plan (morning) |
| 12:00 PM | Mid-day checkpoint |
| 2:00 PM | Execute chaos plan (afternoon) - **NEW** |
| 6:00 PM | End of day report |
| 8:00 PM | Generate next day's plan |

### 6. Git Push & VPS Deployment
**Status:** COMPLETE
**Commits pushed:**
1. `fdb99c6` - L1 legacy action mapping (Session 30)
2. `e90c52c` - L1 JSON rule loading + chaos lab fixes (Session 31)
3. `1b3e665` - Network compliance + extended check types (v1.0.30)
4. `14cac63` - Learning Flywheel pattern reporting + tier fix
5. `4bc85c2` - Sensor registry FK + FrameworkConfig API fix

**VPS Deployed & Verified:**
- Backend: models.py, metrics.py, routes.py, db_queries.py
- Frontend: Built and deployed (index-DUHCrfow.js)
- Container: Restarted, healthy

**Files Modified:**
| File | Change |
|------|--------|
| `mcp-server/central-command/backend/models.py` | Added NETWORK check type |
| `mcp-server/central-command/backend/metrics.py` | Added network to compliance score |
| `mcp-server/central-command/backend/routes.py` | Added pattern endpoints |
| `mcp-server/central-command/backend/db_queries.py` | Fixed tier count query |
| `mcp-server/central-command/frontend/src/types/index.ts` | Extended CheckType union |
| `mcp-server/central-command/frontend/src/components/incidents/IncidentRow.tsx` | Extended labels |
| `mcp-server/main.py` | Added /agent/patterns endpoint |
| `mcp-server/Dockerfile` | Added asyncpg, cryptography |
| `packages/compliance-agent/src/compliance_agent/appliance_agent.py` | v1.0.30, network check_type |
| `~/chaos-lab/` crontab (iMac) | Added 2 PM execution |

---

## Immediate (Next Session)

### 1. Build ISO v30 with Network Check Type
**Status:** PENDING
**Details:**
- Agent code at v1.0.30 with network check_type fix
- Update `iso/appliance-image.nix` version to 1.0.30
- Build ISO on VPS

### 2. Deploy ISO v30 to Appliances
**Status:** PENDING
**Details:**
- Deploy to VM first (192.168.88.247)
- User handles physical appliance (192.168.88.246)

### 3. Run Chaos Lab Cycle
**Status:** PENDING
**Details:**
- Verify extended check types display correctly
- Monitor Learning dashboard for pattern aggregation
- Check incidents page for proper labels

---

## Short-term

- First compliance packet generation
- 30-day monitoring period
- Evidence bundle verification in MinIO
- Test framework scoring with real appliance data

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

**Check chaos lab cron:**
```bash
ssh jrelly@192.168.88.50 "crontab -l | grep -A 10 'Chaos Lab'"
```

**Rebuild ISO on VPS:**
```bash
cd /root/msp-iso-build && git pull && nix build .#appliance-iso -o result-iso-v30
```
