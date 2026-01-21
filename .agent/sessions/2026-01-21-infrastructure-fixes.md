# Session 56: Infrastructure Fixes & Full Coverage Enabled

**Date:** 2026-01-21
**Status:** COMPLETE
**Agent Version:** 1.0.44
**ISO Version:** v44
**Phase:** 13 (Zero-Touch Update System)

---

## Session Summary

This session focused on infrastructure fixes and enabling Full Coverage healing mode on the physical appliance. Key accomplishments include fixing the api_base_url bug in appliance_agent.py, correcting chaos lab workstation credentials, enabling Full Coverage healing mode (21 rules) via browser automation, and fixing the deployment-status HTTP 500 error by applying database migrations and correcting asyncpg syntax errors.

---

## Tasks Completed

### 1. Lab Credentials Prominently Placed
- **Purpose:** Ensure future AI sessions always see lab credentials upfront
- **Changes:**
  - Added "Lab Credentials (MUST READ)" section to CLAUDE.md
  - Added quick reference table with DC, WS, appliance, and VPS credentials
  - Updated packages/compliance-agent/CLAUDE.md to reference LAB_CREDENTIALS.md

### 2. api_base_url Bug Fixed
- **File:** `packages/compliance-agent/src/compliance_agent/appliance_agent.py`
- **Lines:** 2879-2891
- **Problem:** UpdateAgent initialization used non-existent config attributes
- **Solution:**
  - Changed `self.config.api_base_url` → `self.config.mcp_url`
  - Changed `self.config.api_key` → read from `self.config.mcp_api_key_file`
  - Changed `self.config.appliance_id` → `self.config.host_id`

### 3. Chaos Lab WS Credentials Fixed
- **File:** `~/chaos-lab/config.env` on iMac (192.168.88.50)
- **Problem:** WS_USER was set to `NORTHVALLEY\Administrator` instead of `localadmin`
- **Solution:** Changed WS_USER to `localadmin`
- **Verification:** WinRM connectivity to both DC and WS confirmed working

### 4. Full Coverage Healing Mode Enabled
- **Method:** Browser automation via Claude-in-Chrome
- **Target:** Physical Appliance Pilot 1Aea78
- **Action:** Changed Healing Mode dropdown from "Standard (4 rules)" to "Full Coverage (21 rules)"
- **Result:** Physical appliance now running with 21 L1 healing rules

### 5. Deployment-Status HTTP 500 Fixed
- **Root Cause 1:** Missing database columns (migration 020 not applied)
  - Applied migration `020_zero_friction.sql` to VPS database
  - Added columns: `discovered_domain`, `domain_discovery_at`, `awaiting_credentials`, `credentials_submitted_at`

- **Root Cause 2:** asyncpg syntax errors in sites.py
  - **Error:** `asyncpg.exceptions.DataError: invalid input for query argument $1: ['site_id'] (expected str, got list)`
  - **Problem:** Code passed `[site_id]` as a list instead of `site_id` as positional argument
  - **Fix:** Changed 14+ instances from `""", [site_id])` to `""", site_id)`
  - **Multi-param Fix:** Changed `[site_id, timestamp]` to `site_id, timestamp`

- **Deployment:** Updated sites.py deployed to VPS via volume mount at `/opt/mcp-server/dashboard_api_mount`

---

## Files Modified

| File | Change |
|------|--------|
| `CLAUDE.md` | Added Lab Credentials section with quick reference table |
| `packages/compliance-agent/CLAUDE.md` | Added LAB_CREDENTIALS.md reference |
| `packages/compliance-agent/src/compliance_agent/appliance_agent.py` | Fixed api_base_url bug (lines 2879-2891) |
| `mcp-server/central-command/backend/sites.py` | Fixed asyncpg syntax (14+ instances) |

---

## VPS Changes

| Change | Description |
|--------|-------------|
| Migration 020 | Applied to add discovered_domain, awaiting_credentials columns |
| Volume mount | Created `/opt/mcp-server/dashboard_api_mount` for hot deployment |
| Permissions | Set chmod 755 on mounted volume |

---

## Infrastructure State After Session

### Physical Appliance (192.168.88.246)
- **Status:** Online
- **Agent:** v1.0.43 (ISO v44 ready for upgrade)
- **Healing Mode:** **Full Coverage (21 rules)**
- **gRPC:** Port 50051 listening

### Windows Lab (Chaos Lab)
- **DC (NVDC01):** 192.168.88.250 - WinRM working
- **WS (NVWS01):** 192.168.88.251 - WinRM working (using localadmin)

### VPS (178.156.162.116)
- **Status:** Online
- **Migration 020:** Applied
- **sites.py:** Fixed and deployed

---

## Key Technical Details

### asyncpg Positional Arguments
asyncpg uses positional arguments, not a list. The pattern is:
```python
# WRONG - passes list
await conn.fetchrow("""SELECT * FROM table WHERE id = $1""", [id])

# CORRECT - positional argument
await conn.fetchrow("""SELECT * FROM table WHERE id = $1""", id)

# CORRECT - multiple positional arguments
await conn.fetchrow("""SELECT * FROM table WHERE id = $1 AND ts = $2""", id, timestamp)
```

### Full Coverage L1 Rules (21 total)
Enabled rules include: firewall, defender, bitlocker, password_policy, audit_policy, smb_signing, ntlm, uac, nla, event_log, credential_guard, unauthorized_users, screen_lock, time_service, dns_client, patching, and more.

---

## Next Session Priorities

1. Deploy ISO v44 to physical appliance (A/B partition update system)
2. Test full update cycle in VM
3. Monitor Full Coverage healing in action
4. Fix evidence bundle 502 error

---

## Session Artifacts

- **Commit:** (pending - to be committed with session docs)
- **Documentation Updated:**
  - `.agent/TODO.md`
  - `.agent/CONTEXT.md`
  - `docs/SESSION_HANDOFF.md`
  - `docs/SESSION_COMPLETION_STATUS.md`
  - `.agent/sessions/2026-01-21-infrastructure-fixes.md` (this file)
