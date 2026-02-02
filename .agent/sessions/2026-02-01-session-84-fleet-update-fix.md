# Session 84 - Fleet Update v52 Deployment & Compatibility Fix

**Date:** 2026-02-01
**Duration:** ~2 hours
**Focus:** Deploy v52 update to appliances via Fleet Updates, fix blocking issues

---

## Summary

Attempted to deploy v52 update to appliances via Central Command Fleet Updates. Fixed multiple issues but encountered a chicken-and-egg problem where appliances running v1.0.49 crash when processing update orders due to a missing config attribute. The fix is in v1.0.52, but appliances need to process an update order to get v1.0.52.

---

## Accomplishments

### 1. CSRF Exemption Fixes
- Added `/api/fleet/` to CSRF exempt paths (was blocking Advance Stage button)
- Added `/api/orders/` to CSRF exempt paths (was blocking order acknowledgement)
- Commit: `2ca89fa`, `a5c84d8`

### 2. MAC Address Format Normalization
- Problem: Database had MAC with hyphens, appliance queried with colons
- Fix: Modified `get_pending_orders` to try both formats
- Commit: `df31b46`

### 3. ISO URL Fix
- Original URL was local file path
- Copied ISO to web server: `https://updates.osiriscare.net/osiriscare-v52.iso`
- Updated `update_releases` table and all pending orders

### 4. ApplianceConfig Compatibility Fix
- Problem: `'ApplianceConfig' object has no attribute 'mcp_api_key_file'`
- Fix: Used `getattr()` for backward compatibility
- Files: `appliance_agent.py`, `evidence.py`
- Commit: `862d3f3`

---

## Blocking Issue Discovered

### The Problem
Appliances running v1.0.49 have a bug that crashes when processing `update_iso` orders:
```python
# Old code (crashes on v1.0.49):
if self.config.mcp_api_key_file and self.config.mcp_api_key_file.exists():

# Fixed code (in v1.0.52):
api_key_file = getattr(self.config, 'mcp_api_key_file', None)
if api_key_file and api_key_file.exists():
```

### Chicken-and-Egg
- To fix: Deploy v1.0.52 to appliance
- To deploy: Appliance must process `update_iso` order
- Processing order: Crashes due to bug in v1.0.49

### Solutions
1. **Manual SSH update** (recommended when user gets home)
2. Build new ISO v53 with minimal fix
3. Wait for physical appliance to come online and update manually

---

## Technical Details

### Fleet Updates System
- Uses staged rollouts: 5% → 25% → 100%
- Creates `update_iso` orders in `admin_orders` table
- Appliances poll for pending orders during check-in
- Orders contain: `iso_url`, `sha256`, `version`, `maintenance_window`

### Maintenance Window
```json
{
  "start": "02:00",
  "end": "05:00",
  "days": ["sunday", "monday", "tuesday", "wednesday", "thursday"],
  "timezone": "America/New_York"
}
```

### Appliance Status
- Physical (192.168.88.246): Offline since Jan 31
- VM (192.168.88.247): Online, v1.0.49, blocked on update

---

## Files Changed

| File | Change |
|------|--------|
| `mcp-server/central-command/backend/csrf.py` | +2 exempt prefixes |
| `mcp-server/central-command/backend/sites.py` | MAC format normalization |
| `packages/compliance-agent/src/compliance_agent/appliance_agent.py` | getattr() fix |
| `packages/compliance-agent/src/compliance_agent/evidence.py` | getattr() fix |

---

## Test Results
```
858 passed, 11 skipped, 3 warnings in 47.66s
```

---

## Next Steps

1. **When user gets home**: SSH to iMac gateway, access VM appliance, manually update agent code
2. Restart agent service to pick up fix
3. Create new update order, verify it processes successfully
4. Physical appliance needs same treatment when accessible

---

## Lessons Learned

1. **Backward compatibility is critical** - Always use `getattr()` or `hasattr()` for optional config attributes
2. **Test update flow end-to-end** - The update system has many components (CSRF, MAC format, order processing)
3. **Fleet updates need fallback** - Consider adding SSH-based emergency update capability for chicken-and-egg scenarios
