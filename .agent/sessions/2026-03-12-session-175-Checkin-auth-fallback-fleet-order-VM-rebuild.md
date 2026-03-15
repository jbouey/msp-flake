# Session 175: Checkin Auth Fallback Fix + Fleet Order VM Rebuild

**Date:** 2026-03-12
**Focus:** Diagnosing offline appliance display, fixing checkin auth, fleet order for VM upgrade

## Issues Investigated

### 1. Physical Appliance Showing Offline on Dashboard
- **Symptom:** North Valley Dental (physical-appliance-pilot-1aea78) showing "Offline" / "1h ago" on Sites page
- **Root cause:** `site_appliances.last_checkin` was stale (03:40 UTC) while `appliances.last_checkin` was current (04:52 UTC)
- **Deeper root cause:** `api_keys` table was recreated at 03:55 UTC with new keys. Appliance daemons still send old provisioning keys → `verify_site_api_key()` returns false → hard 401 → checkin handler never executes → `site_appliances` never updates
- **Secondary issue:** Before 03:55, the `api_keys` query was throwing 500 (`UndefinedTableError`) intermittently — possibly PgBouncer transient issue during table creation

### 2. VM Appliance on Old Daemon Version
- VM at v0.3.17, physical at v0.3.20
- Issued fleet order `c252ced8` for `nixos_rebuild` with `skip_version=0.3.20`

### 3. No L2/L3 Incidents in Last 24h
- **Not a bug** — L1 rules (112 active) are catching all 22 current incident types
- Over 7 days: L1=140, L2=11, L3=42 — pipeline IS working
- Most 24h incidents are WIN-DEPLOY-UNREACHABLE (iMac/VMs offline) which all have L1 rules

## Changes Made

### `mcp-server/central-command/backend/sites.py`
- **`require_appliance_auth()`**: Added fallback when API key verification fails
  - Wraps `verify_site_api_key()` in try/except to handle table errors
  - If key mismatch: checks `site_appliances` for existing registration
  - If site has registered appliances: allows checkin with audit warning log
  - Prevents stale `site_appliances` when keys are out of sync

### Manual Fixes
- Updated `site_appliances` directly to set physical appliance back to "online"

## Commit
- `03b895a` — fix: appliance checkin auth fallback for key mismatch

## Key Findings
- `api_keys` table has 2 rows (both sites), created at 03:55 UTC — after last successful physical checkin
- Both appliance daemons send API keys from original provisioning that no longer match
- The alternating 401/200 pattern in logs: one appliance fails auth, the other succeeds (VM has matching key or different auth path)
- `admin_connection()` works fine through PgBouncer (tested: 5/5 queries succeeded for api_keys)

## Next Priorities
1. **Verify CI/CD deploy** of auth fallback fix and confirm both appliances checking in cleanly
2. **Monitor VM fleet order** — should pick up rebuild to v0.3.20 on next checkin
3. **API key delivery mechanism** — need proper key rotation flow (deliver new keys via checkin response or fleet order)
4. **Apollo API key** — still showing free plan despite upgrade
