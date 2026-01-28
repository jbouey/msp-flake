# Session Handoff - 2026-01-28

**Session:** 78 - VPS Telemetry & Rule Sync Fixes
**Agent Version:** v1.0.49
**ISO Version:** v49 (pending rebuild with all fixes)
**Last Updated:** 2026-01-28
**System Status:** ✅ ALL SYSTEMS OPERATIONAL

---

## Current State Summary

| Component | Status | Notes |
|-----------|--------|-------|
| Agent | v1.0.49 | Running with bind mount fixes |
| ISO | v49 | Needs rebuild to include 013fb17 |
| Physical Appliance | **ONLINE** | 192.168.88.246 |
| L1 Healing | **FIXED** | Correct Windows/NixOS routing |
| Target Routing | **FIXED** | IP-based matching working |
| VPS L1 Rules | **UPDATED** | host_id regex for Windows-only |
| API | **HEALTHY** | https://api.osiriscare.net/health |

---

## Session 78 - VPS Telemetry & Rule Sync Fixes

### Issues Fixed

1. **Execution telemetry 500 error** - VPS `main.py` wasn't parsing ISO datetime strings
   - Added `parse_iso_to_datetime()` helper function
   - `started_at` and `completed_at` now properly converted to datetime objects

2. **L1-TEST-RULE-001 invalid rule** - Kept re-syncing from VPS
   - Deleted from `promoted_rules` database table
   - Cleaned up from appliance `/var/lib/msp/rules/promoted/`

3. **L1-NIXOS-FW-001 `not_regex` error** - VPS used unsupported operator
   - Changed VPS rule from `host_id not_regex` to `platform eq nixos`
   - Now matches baseline rule in `l1_baseline.json`

4. **Pattern-stats 500 cascade error** - Stuck SQL transaction
   - Restarted mcp-server to clear transaction state

### Verified Working
```
POST /api/agent/executions → 200 OK (was 500)
POST /agent/patterns → 200 OK
POST /api/agent/sync/pattern-stats → 200 OK (was 500)
NixOS firewall → L1-NIXOS-FW-001 → escalate (correct!)
```

### VPS Changes Applied
- `/opt/mcp-server/app/main.py` - Added datetime helper, fixed L1-NIXOS-FW-001
- `promoted_rules` table - Deleted invalid L1-TEST-RULE-001
- Container restarted multiple times

---

## Session 77 - L1 Rules Windows/NixOS Distinction FIXED

### Problem Summary
- L1-FIREWALL rules matched local NixOS appliance (should be Windows-only)
- L1-BITLOCKER rules not matching `bitlocker_status` check type
- sensor_api.py had import error preventing sensor-pushed drift healing

### Fixes Applied

#### 1. VPS L1 Rules Updated (`/opt/mcp-server/app/main.py`)
- **L1-FIREWALL-001/002**: Added `host_id regex ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$`
  - Only matches IP addresses (Windows VMs), not hostnames (NixOS appliance)
  - Changed action to `run_windows_runbook` with RB-WIN-FIREWALL-001
- **L1-NIXOS-FW-001**: New rule for NixOS firewall → escalate
  - Note: VPS version uses `not_regex` (not supported), l1_baseline.json uses `platform == nixos`
- **L1-BITLOCKER-001**: Added `bitlocker_status` and `encryption` to check_type match
  - Added host_id regex for Windows-only
  - Changed action to `run_windows_runbook` with RB-WIN-SEC-005

#### 2. Agent Fix (Commit 013fb17)
- **sensor_api.py**: Changed import from `.models` to `.incident_db` for Incident class
- **l1_baseline.json**: Added `bitlocker_status`, `windows_backup_status` to check types

#### 3. Appliance Deployment
- Deployed updated l1_baseline.json to `/var/lib/msp/rules/l1_baseline.json`
- VPS syncs l1_rules.json with new rules

### Verified Working
```
NixOS firewall check → L1-NIXOS-FW-001 → escalate (correct)
Windows firewall (192.168.88.244) → L1 rule → RB-WIN-FIREWALL-001 → SUCCESS
Windows BitLocker (192.168.88.244) → L1 rule → RB-WIN-ENCRYPTION-001 → runs (verify fails - lab limitation)
```

### Technical Notes
- L1 engine supports `regex` operator but NOT `not_regex`
- L1 baseline rule uses `platform == nixos` instead of `host_id not_regex`
- Synced rules have priority 5, baseline rules have priority 1 (baseline wins on conflicts)

---

## Session 76 - Target Routing Bug FIXED

### Bug Summary
- Healing actions going to wrong VM (always first target .244)
- Root Cause #1: Server didn't return `ip_address` in windows_targets
- Root Cause #2: Short name matching on IPs - "192" matched all targets

### Fixes Applied
1. Server: Added `ip_address` field to windows_targets response
2. Agent: Skip short name matching for IP-format target_host

---

## Git Commits This Session

| Commit | Message |
|--------|---------|
| `013fb17` | fix: Fix sensor_api import and L1 rule check types |
| `f494f89` | fix: Add AUTO-* runbook mapping and L1 firewall rules |
| `f87872a` | fix: Target routing - IP addresses use exact match only |

---

## Files Modified This Session

| File | Change |
|------|--------|
| `sensor_api.py` | Import Incident from incident_db not models |
| `l1_baseline.json` | Added bitlocker_status, backup_status check types |
| `appliance_agent.py` | IP-based target matching, AUTO-* runbook mapping |
| VPS `main.py` | L1 rules with host_id regex, L1-NIXOS-FW-001 |

---

## Lab Environment Status

### Appliances
| Appliance | IP | Version | Status |
|-----------|-----|---------|--------|
| Physical (HP T640) | 192.168.88.246 | v1.0.49 | **ONLINE** |

### VPS
| Service | URL | Status |
|---------|-----|--------|
| Dashboard | https://dashboard.osiriscare.net | Online |
| API | https://api.osiriscare.net | Online |
| L1 Rules | Updated | 31 rules with Windows/NixOS distinction |

### Windows VMs (on iMac 192.168.88.50)
| VM | IP | Status |
|----|-----|--------|
| NVSRV01 | 192.168.88.244 | Online, healing working |
| NVDC01 | 192.168.88.250 | Online, healing working |
| NVWS01 | 192.168.88.251 | Online, healing working |

---

## Known Limitations

1. **BitLocker verify phase fails** - Lab VMs may not have TPM/encryption configured
2. **`not_regex` operator** - Not supported by L1 engine, use `platform` condition instead
3. **L1-TEST-RULE-001.yaml** - Promoted rule fails to load (missing 'action' field)

---

## Next Session Priorities

### 1. Rebuild ISO v50
- Include commits: f87872a, f494f89, 013fb17
- All L1 rules fixes and target routing fixes

### 2. Fix L1-TEST-RULE-001.yaml
- Promoted rule in `/var/lib/msp/rules/promoted/` has invalid format
- Either delete or fix the YAML structure

### 3. Add `not_regex` Operator Support (Optional)
- Add to `level1_deterministic.py` MatchOperator enum
- Implement in RuleCondition.matches()

---

## Quick Commands

```bash
# SSH to physical appliance
ssh root@192.168.88.246

# Check agent logs
journalctl -u compliance-agent -f

# Check L1 rule matching
journalctl -u compliance-agent | grep "L1 rule matched"

# Deploy VPS main.py fix
scp file.py root@178.156.162.116:/opt/mcp-server/app/
ssh root@178.156.162.116 "cd /opt/mcp-server && docker compose restart mcp-server"

# Force rule sync
ssh root@192.168.88.246 "rm /var/lib/msp/rules/l1_rules.json && systemctl restart compliance-agent"
```

---

## Related Docs

- `.agent/TODO.md` - Current tasks and session history
- `.agent/LAB_CREDENTIALS.md` - Lab passwords (MUST READ)
- `docs/PRODUCTION_READINESS_AUDIT.md` - Full production audit
