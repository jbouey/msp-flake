# Session Handoff - 2026-01-24

**Session:** 67 - Partner Portal Testing & Fixes
**Agent Version:** v1.0.46
**ISO Version:** v46
**Last Updated:** 2026-01-24

---

## Current State Summary

| Component | Status | Notes |
|-----------|--------|-------|
| Agent | v1.0.46 | Running on physical appliance |
| ISO | v46 | Built and deployed OTA |
| Physical Appliance | **ONLINE** | 192.168.88.246, v1.0.46 |
| Tests | 834 + 24 Go | All passing |
| OTA USB Update | **WORKING** | Download → dd to USB → reboot |
| Fleet Updates | **DEPLOYED** | v46 release added |
| Go Agents | **ALL 3 VMs** | DC, WS, SRV deployed |
| gRPC | **WORKING** | Drift → L1 → Runbook verified |
| Partner Portal | **WORKING** | API key auth tested, dashboard fixed |
| Dashboard | **WORKING** | Google button text fixed |
| Evidence Pipeline | **SECURED** | Ed25519 signatures required |

---

## Session 67 Accomplishments

### 1. Partner Dashboard Blank Page Fixed
- **Issue:** Dashboard showed blank white page after login
- **Root Cause:** `brand_name` column was NULL, causing `charAt()` error for avatar initials
- **Fix:** Set `brand_name = 'AWS Bouey'` in partners table
- Dashboard now loads correctly with stats and provision code button

### 2. Partner Account Created (API Key Method)
- **Email:** awsbouey@gmail.com
- **Partner ID:** 617f1b8b-2bfe-4c86-8fea-10ca876161a4
- **API Key:** `osk_C_1VYhgyeX5hOsacR-X4WsR6gV_jvhL8B45yCGBzi_M`
- **Note:** API key hashing uses `hashlib.sha256(f'{API_KEY_SECRET}:{api_key}'.encode()).hexdigest()`

### 3. Google OAuth Button Text Fixed
- Changed "Sign in with Google Workspace" → "Sign in with Google"
- File: `mcp-server/central-command/frontend/src/partner/PartnerLogin.tsx`
- Commit: `a8b1ad0`
- Frontend rebuilt and deployed to VPS

### 4. Google OAuth Client Status
- **Current:** OAuth client `325576460306-...` is disabled in Google Cloud Console
- **Impact:** Google OAuth signup/login not working
- **Workaround:** API key authentication works
- **TODO:** Re-enable Google OAuth client or create new one for non-Workspace Google accounts

---

## Session 66 Final Accomplishments

### 1. Version Sync Bug Fixed
- **Issue:** `__init__.py` was stuck at `0.2.0` while setup.py was at `1.0.45`
- **Impact:** Agent reported wrong version in logs despite having correct code
- **Fix:** Synced all version strings to `1.0.46`:
  - `packages/compliance-agent/src/compliance_agent/__init__.py`
  - `packages/compliance-agent/setup.py`
  - `iso/appliance-image.nix`
- **Commit:** `e37071c`

### 2. v46 ISO Built and Deployed
- Built new ISO on VPS with correct agent version
- SHA256: `f9cfb484a16e183118db2ed0246c8e0a2da17a2ae82730b5c937aa79d1d10e53`
- Added to updates server at `http://178.156.162.116:8081/osiriscare-v46.iso`
- Added release to database with `is_latest = true`

### 3. OTA USB Update Pattern Established
- **Process:**
  1. Download ISO to appliance RAM (`curl -o /tmp/osiriscare-v46.iso`)
  2. Verify SHA256 hash
  3. Write directly to USB (`dd if=/tmp/osiriscare-v46.iso of=/dev/sdb`)
  4. Reboot
- **Why it works:** Live ISO runs from tmpfs (RAM), not directly from USB
- **Result:** No physical USB swap needed for updates

### 4. Database Cleanup
- Completed old v44 rollout (was stuck at in_progress)
- Reset corrupt v44/v45 update status for physical appliance
- Updated appliance current_version to 1.0.46

---

## OTA USB Update Command Reference

```bash
# On appliance - Download, verify, flash, reboot
ssh root@192.168.88.246
cd /tmp
curl -L -o new-iso.iso http://178.156.162.116:8081/osiriscare-v46.iso
sha256sum new-iso.iso  # Verify hash
dd if=new-iso.iso of=/dev/sdb bs=4M status=progress oflag=sync
sync
reboot
```

---

## Lab Environment Status

### Windows VMs (on iMac 192.168.88.50)
| VM | IP | Go Agent | Status |
|----|-----|----------|--------|
| NVDC01 | 192.168.88.250 | Deployed | Domain Controller |
| NVWS01 | 192.168.88.251 | Deployed | Workstation |
| NVSRV01 | 192.168.88.244 | Deployed | Server Core |

### Appliances
| Appliance | IP | Version | Status |
|-----------|-----|---------|--------|
| Physical (HP T640) | 192.168.88.246 | v1.0.46 / ISO v46 | **ONLINE** |
| VM (VirtualBox) | 192.168.88.247 | v1.0.44 | Online |

### VPS
| Service | URL | Status |
|---------|-----|--------|
| Dashboard | https://dashboard.osiriscare.net | Online |
| API | https://api.osiriscare.net | Online |
| Updates | http://178.156.162.116:8081 | v46 ISO available |

---

## Next Session Priorities

### Priority 1: Fix Google OAuth Client
- Re-enable Google OAuth client in Google Cloud Console
- Or create new OAuth client for regular Google accounts (not Workspace)
- Test full OAuth signup flow

### Priority 2: Test Partner OAuth Signup Flow
- Test Microsoft OAuth partner signup (should work)
- Verify domain whitelisting auto-approval
- Verify pending partner approval workflow

### Priority 3: Set Up Persistent Data Partition
- Physical appliance runs from live USB (tmpfs)
- Config/data lost on reboot unless saved to HDD
- Create data partition on sda for `/var/lib/msp`

### Priority 4: A/B Partition System (Deferred)
- NixOS ISO initramfs doesn't support partition-based boot
- Would need custom initramfs to implement proper A/B
- OTA USB update is working alternative for now

---

## Quick Commands

```bash
# SSH to physical appliance
ssh root@192.168.88.246

# Check agent version
journalctl -u compliance-agent | grep -i version | head -3

# SSH to VPS
ssh root@178.156.162.116

# Check releases in DB
docker exec -i mcp-postgres psql -U mcp -d mcp -c "SELECT version, agent_version, is_latest FROM update_releases;"

# Build new ISO
cd /root/msp-iso-build
nix --extra-experimental-features 'nix-command flakes' build '.#nixosConfigurations.osiriscare-appliance.config.system.build.isoImage' -o result-vXX
```

---

## Related Docs

- `.agent/TODO.md` - Current tasks and session history
- `.agent/CONTEXT.md` - Full project context
- `.agent/LAB_CREDENTIALS.md` - Lab passwords (MUST READ)
- `IMPLEMENTATION-STATUS.md` - Phase tracking
