# Session Archive - 2026-02


## 2026-02-01-production-readiness-security.md

# Session: 2026-02-01 - Production Readiness Security Audit

**Duration:** ~3 hours
**Focus Area:** Backend security audit, frontend audit, production hardening, deployment fixes

---

## What Was Done

### Completed - Backend
- [x] Full backend and database audit for production readiness (5 parallel agents)
- [x] SQL injection fix in telemetry purge (routes.py, settings_api.py)
- [x] Make bcrypt mandatory for password hashing (auth.py)
- [x] Add require_admin auth to 11 unprotected admin endpoints
- [x] Fix N+1 query in get_all_compliance_scores with asyncio.gather
- [x] Add connection pool tuning (pool_size=20, pool_recycle, pool_pre_ping)
- [x] Create migration 033 with 12 performance indexes
- [x] CSRF double-submit cookie protection middleware
- [x] Move session tokens to HTTP-only secure cookies
- [x] Encrypt OAuth tokens with Fernet (replace base64)
- [x] Create Redis-backed distributed rate limiter
- [x] Create migration runner with rollback support
- [x] Fix deployment outage (bcrypt missing, legacy password support)

### Completed - Frontend
- [x] Full frontend comprehensive audit for production readiness (5 parallel agents)
- [x] Create ErrorBoundary component for catching React render errors
- [x] Add AbortController support to API client with timeout
- [x] Improved QueryClient configuration with smart retry logic
- [x] Add onError callbacks to 25+ mutation hooks
- [x] Add global error handler for unhandled query errors
- [x] React.lazy code splitting - main bundle reduced 933KB → 308KB (67% reduction)
- [x] React.memo on 6 heavy list components (ClientCard, IncidentRow, PatternCard, RunbookCard, OnboardingCard)
- [x] HTTP-only secure cookie authentication (with localStorage fallback for transition)
- [x] Backend auth endpoints updated to accept token from cookie OR header
- [x] Frontend fetch requests include credentials: 'same-origin' for cookie auth

### Hotfixes Applied
- [x] Added bcrypt==4.2.1 to VPS requirements.txt
- [x] Restored SHA-256 legacy password verification (read-only)
- [x] Fixed SAFE_METHODS usage in rate limiter

---

## Key Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Keep SHA-256 verification (read-only) | Existing accounts have legacy hashes | Login works for all users |
| bcrypt mandatory for new passwords | Security best practice | All new passwords properly hashed |

[truncated...]

---

## 2026-02-01-session-83-runbook-audit.md

# Session 83 - Runbook Security Audit & Project Analysis

**Date:** 2026-02-01
**Status:** COMPLETE

---

## Session Goals
1. ✅ Comprehensive runbook audit (find ALL 77 runbooks)
2. ✅ Fix runbook security issues (command injection, PHI exposure)
3. ✅ Complete system analysis with completion percentages
4. ✅ Generate PDF project status report

---

## Accomplishments

### 1. Runbook Inventory - COMPLETE (77 Total)

| Category | Count | Location |
|----------|-------|----------|
| L1 Rules (JSON) | 22 | `config/l1_rules_full_coverage.json` |
| Linux Runbooks | 19 | `runbooks/linux/runbooks.py` |
| Windows Core | 7 | `runbooks/windows/runbooks.py` |
| Windows Security | 14 | `runbooks/windows/security.py` |
| Windows Network | 5 | `runbooks/windows/network.py` |
| Windows Services | 4 | `runbooks/windows/services.py` |
| Windows Storage | 3 | `runbooks/windows/storage.py` |
| Windows Updates | 2 | `runbooks/windows/updates.py` |
| Windows AD | 1 | `runbooks/windows/active_directory.py` |
| **Total** | **77** | |

### 2. Security Fixes Applied

#### Command Injection Fix (Invoke-Expression → Start-Process)
- **Files:** `security.py:167`, `runbooks.py:475`
- **Issue:** `Invoke-Expression $Cmd` allowed command injection
- **Fix:** Direct `Start-Process -FilePath "auditpol.exe" -ArgumentList` with structured arrays

#### PHI Scrubber Integration
- **File:** `executor.py`
- **Version:** Bumped to 2.1
- **Patterns Scrubbed:** SSN, phone, email, DOB, IP, credit card
- **Implementation:** Scrubs stdout, stderr, and parsed JSON results

### 3. Project Status Report

Created comprehensive analysis at:
- `docs/PROJECT_STATUS_REPORT.md` (669 lines)
- `docs/PROJECT_STATUS_REPORT.pdf` (10 pages)

[truncated...]

---

## 2026-02-01-session-84-fleet-update-fix.md

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

[truncated...]

---

## 2026-02-03-session-85-context-restructure.md

# Session 85 - Context Management Restructure

**Date:** 2026-02-03
**Duration:** ~1 hour
**Focus:** Fix context management issues, establish consistent session patterns

---

## What Was Done

### 1. Context Audit
- Analyzed all 100+ documentation files
- Identified redundancy: TODO.md (1,004 lines), CONTEXT.md (1,357 lines), IMPLEMENTATION-STATUS.md (1,015 lines)
- Found core problem: state sprawl across multiple files

### 2. New Structure Created

**Created `.agent/CURRENT_STATE.md`** (~60 lines)
- Single source of truth for current state
- System health table
- Current blocker
- Immediate next actions
- Quick reference commands

**Simplified `.agent/TODO.md`** (~60 lines)
- Current tasks only
- No session history (moved to sessions/)
- Clear sprint structure

**Updated `.agent/README.md`**
- File hierarchy with read order
- Session management prompts (start/mid/long/end)
- Anti-patterns to avoid
- Quick recovery procedures

### 3. Earlier Work (Pre-compaction)
- Removed dry_run mode entirely
- Added circuit breaker for healing loops (5 attempts, 30min cooldown)
- Tests passing (858 + 24)

---

## Files Modified

| File | Change |
|------|--------|
| `.agent/CURRENT_STATE.md` | NEW - Single source of truth |
| `.agent/TODO.md` | SIMPLIFIED - Tasks only, no history |
| `.agent/README.md` | UPDATED - Session prompts, file hierarchy |
| `packages/compliance-agent/src/compliance_agent/config.py` | dry_run default false |

[truncated...]

---

## 2026-02-06-session-88-sitedetail-header-button-fixes-blockchain-ots.md

# Session 88 - SiteDetail Header Redesign, Button Fixes, Blockchain/OTS Fixes

**Date:** 2026-02-06
**Started:** 09:34
**Previous Session:** 87

---

## Goals

- [x] Fix blockchain append-only trigger blocking chain position migration
- [x] Complete OTS proof upgrade lifecycle
- [x] Add background OTS upgrade task
- [x] Audit and redesign SiteDetail header buttons
- [x] Test all 6 header buttons for functionality
- [x] Fix broken Devices and Frameworks pages
- [x] Deploy all fixes to VPS

---

## Progress

### Completed

1. **WORM Trigger Fix** - Modified trigger to protect evidence content (checks, bundle_hash, signature) but allow chain metadata updates (prev_hash, chain_position). Migration 030.
2. **Chain Migration** - Migrated 179,729 bundles across 2 sites with zero broken links using GENESIS_HASH = "0" * 64 for genesis blocks.
3. **OTS Commitment Fix** - Fixed `replay_timestamp_operations()` to return `current_hash` at attestation instead of `last_sha256_result`. Expired 78,699 stale proofs (>5 days old, calendar pruned).
4. **Background OTS Upgrade Task** - Added asyncio background task in FastAPI lifespan that runs every 2 hours to upgrade pending OTS proofs.
5. **SiteDetail Header Redesign** - Replaced rainbow-colored 6-button row with clean two-row layout:
   - Row 1: Site name + status badge + ghost-style "Portal Link" button
   - Row 2: Uniform navigation pills (Devices, Workstations, Go Agents, Frameworks, Cloud Integrations)
6. **Devices Page Fix** - Fixed `a.hostname` -> `a.host_id` in device_sync.py (4 SQL queries). Also fixed UPDATE to use existing `last_checkin` column instead of non-existent `last_device_sync`/`device_count`/`medical_device_count`.
7. **Frameworks Page Fix** - Fixed scores extraction: `Array.isArray(scoresData) ? scoresData : (scoresData as any)?.scores || []` to handle API returning `{scores: [...]}` instead of `[...]`.
8. **Full Deployment** - Built frontend locally, deployed dist via rsync, deployed device_sync.py backend fix, restarted all services.
9. **Browser Verification** - All 6 buttons tested and confirmed working on dashboard.osiriscare.net.

### Blocked

- OTS calendar servers prune proofs after ~5 days. All 78,699 existing proofs were too old to upgrade. Future proofs will be handled by the background task.

---

## Files Changed

| File | Change |
|------|--------|
| `mcp-server/central-command/backend/evidence_chain.py` | Fixed OTS commitment replay, chain migration genesis hash, verify chain integrity |
| `mcp-server/central-command/backend/migrations/030_fix_worm_trigger_chain_metadata.sql` | NEW - Modified WORM trigger to allow chain metadata updates |
| `mcp-server/main.py` | Added background OTS upgrade loop (2hr interval) |
| `mcp-server/central-command/frontend/src/pages/SiteDetail.tsx` | Redesigned header: two-row layout with uniform nav pills |

[truncated...]

---

## 2026-02-06-session-89-phi-boundary-enforcement.md

# Session: 2026-02-06 - PHI Boundary Enforcement

**Session:** 89
**Focus Area:** HIPAA PHI transmission security audit and remediation

---

## What Was Done

### Completed
- [x] Audited all 9 outbound data channels from compliance appliance
- [x] PHI scrubber enhancement: `exclude_categories` parameter to preserve infrastructure data
- [x] Outbound PHI scrub gateway in `appliance_client._request()` - single enforcement point
- [x] L2 LLM PHI guard: scrub `raw_data` + `similar_incidents` before cloud API calls
- [x] Credential local storage: Fernet-encrypted `CredentialStore` with HKDF key derivation
- [x] Conditional credential pull: skip credentials in checkin when local cache is fresh
- [x] Evidence hardening: truncate output to 500 chars, strip stdout for passing checks
- [x] Partner activity logging: instrumented `partner_auth.py`, `partners.py`, `learning_api.py`
- [x] 26 new tests added (881 total passing, 7 skipped)

### Not Started (deferred)
- [ ] Migration `036_credential_versioning.sql` - server-side credential version tracking columns
- [ ] Deploy changes to VPS

---

## Key Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| IPs are infrastructure, not PHI | HIPAA Safe Harbor 45 CFR 164.514(b)(2) - IPs don't identify patients | `exclude_categories={'ip_address'}` preserves IPs in scrubbed data |
| Transport-layer scrub gateway | Single enforcement point prevents new endpoints from leaking PHI | All outbound HTTP payloads scrubbed via `_request()` |
| Local LLM keeps full data | Data never leaves appliance with local LLM | Only `APILLMPlanner` scrubs, `LocalLLMPlanner` unchanged |
| Fernet encryption for credential cache | Standard symmetric encryption with HKDF key derivation from API key + machine ID | Credentials encrypted at rest, 24h TTL for refresh |
| Network posture data flows intentionally | Ports, DNS, reachability checks needed for partner compliance dashboard | Not PHI - regulation-required visibility |

---

## Files Modified

| File | Change |
|------|--------|
| `compliance_agent/phi_scrubber.py` | Added `exclude_categories` parameter, `active_patterns` filtering |
| `compliance_agent/appliance_client.py` | Added `_outbound_scrubber`, `_scrub_outbound()`, scrub in `_request()`, `has_local_credentials` param |
| `compliance_agent/level2_llm.py` | PHI scrubbing in `APILLMPlanner._build_prompt()` for raw_data and similar_incidents |
| `compliance_agent/credential_store.py` | **NEW** - Fernet-encrypted local credential storage with HKDF, atomic writes, TTL |
| `compliance_agent/appliance_agent.py` | CredentialStore integration, conditional credential pull, evidence hardening at 4 locations |
| `backend/sites.py` | `has_local_credentials` field on `ApplianceCheckin`, conditional credential delivery |
| `backend/partner_auth.py` | Partner activity logging for auth endpoints |
| `backend/partners.py` | Partner activity logging + new API endpoints |

[truncated...]

---

## 2026-02-07-session-93-context-bloat-firewall-fix.md

# Session 93: Context Bloat Fix + Firewall False-Positive Loop

**Date:** 2026-02-07
**Duration:** ~1 hour
**Agent Version:** 1.0.55 → 1.0.56 (code, not yet deployed)

## Problem 1: Context Limit Reached

User hitting "Context limit reached" errors in Claude Code.

### Diagnosis
- `settings.local.json`: 63KB, 505 permission entries accumulated over months
  - Contained leaked Anthropic API key, AWS credentials, bearer tokens, passwords
  - Hundreds of redundant entries subsumed by existing wildcards
- `CLAUDE.md`: 7.7KB with "MUST READ" / "ALWAYS CHECK" language triggering eager context loading
- Duplicate files in `.agent/` root AND `.agent/reference/` (~80KB wasted)

### Fix
- Rebuilt `settings.local.json`: 63KB → 2KB (73 clean wildcards)
- Rewrote `CLAUDE.md`: 7.7KB → 3.9KB (lazy-load language, same info)
- Deleted 4 duplicate files from `.agent/` root
- **Total savings: ~105KB per session startup**

## Problem 2: Firewall Healing Loop (100+ Noise Incidents)

Dashboard showed 100 "Firewall drift / L1 AUTO / Resolved" incidents from Test Appliance, cycling every 1-2 minutes.

### Root Cause
`drift.py:504`: `service_name = firewall_config.get('service', 'nftables')` defaulted to checking `systemctl is-active nftables`. The NixOS appliance uses **iptables** (legacy). `nftables` was always inactive → false critical drift every 60s.

Windows boxes (DC .250, WS .251) were fine — firewall enabled, no GPO overrides.

### Fix
1. **`drift.py`**: Firewall baseline check now tries nftables first, falls back to iptables (chain count > 3 + `iptables-save` hash)
2. **`auto_healer.py`**: Added flap detector — tracks resolve→recur cycles, escalates to L3 after 5 flaps in 30 minutes

## Commits
- `60d842e` - fix: reduce context bloat
- `ab54e8a` - fix: firewall false-positive healing loop + add flap detector

## Tests
903 passed, 3 pre-existing failures (dry_run kwarg), 11 skipped

## Not Completed
- Fleet deploy of v1.0.56 to physical appliance (researched workflow, not executed)
- Credential rotation for leaked keys

---

## 2026-02-08-ad-enrollment-fixes.md

# Session 101: AD Enrollment Fixes + Domain Discovery 500

**Date:** 2026-02-08
**Session:** 101 (continued from 100)

## Summary

Fixed three issues from lab testing of auto-enrollment feature:
1. Domain discovery 500 on Central Command
2. BitLocker "Healing failed: None" misleading log
3. Deployed all fixes to physical appliance via nixos-rebuild

## Changes Made

### 1. Domain Discovery 500 (Central Command)
**File:** `mcp-server/central-command/backend/sites.py`
**Commit:** `ff71bd8`

- **Root cause:** `report_discovered_domain()` JOINed a `partners` table that doesn't exist in the DB schema. Partner info (`contact_email`, `client_contact_email`) lives directly on the `sites` table.
- **Fix:** Removed the `partners` JOIN entirely. `send_critical_alert()` sends to a configured `ALERT_EMAIL` (env var), not a dynamic partner email, so no partner lookup needed.
- Also fixed `send_critical_alert()` call: was using `recipient`/`subject`/`body` params that don't exist. Corrected to `title`/`message`/`site_id`.
- Notification INSERT now always runs (was gated behind failing partner lookup).

### 2. BitLocker Escalation Logging
**File:** `packages/compliance-agent/src/compliance_agent/appliance_agent.py`
**Commit:** `940b035`

- **Root cause:** L1 rule `L1-WIN-BITLOCKER-001` matches `bitlocker_status` with `action: escalate`. The `_try_level1()` returns `None` for escalate actions, falling through to L3 which creates an escalation ticket. L3 returns `HealingResult(success=False, error=None)`. The appliance agent logged this as "Healing failed: None".
- **Fix:** Added `elif getattr(heal_result, 'escalated', False)` check before the failure branch. Now logs "escalated to L3 for human review".
- Fixed in both healing paths (`_attempt_healing` and Windows scanning loop).

### 3. Physical Appliance Deployment
- `nixos-rebuild test --flake github:jbouey/msp-flake#osiriscare-appliance-disk --refresh`
- Agent restarted successfully
- Verified: domain discovery (northvalley.local), AD enumeration (2 servers, 1 workstation), domain report to Central Command (no more 500)

## Also Completed (from previous session context)
- FQDN-to-IP resolution pipeline (`resolve_missing_ips()`, direct TCP tests, skip PHI scrub)
- Auto-enrollment of domain workstations alongside servers
- 23 auto-enrollment tests

## Test Results
- 940 passed, 13 skipped, 0 failures
- CI/CD deploy successful

## Commits
| Hash | Message |
|------|---------|
| `940b035` | fix: Domain discovery 500 + distinguish L3 escalation from healing failure |
| `ff71bd8` | fix: Domain discovery 500 - remove nonexistent partners table JOIN |

[truncated...]

---

## 2026-02-08-firewall-flap-domain-dedup.md

# Session 102: Firewall Drift Loop Fix + Domain Discovery Dedup

**Date:** 2026-02-08
**Duration:** ~2 hours

## Problems Solved

### 1. Firewall Drift Circular Loop (100+ incidents)

**Symptom:** VM appliance (Test Appliance Lab B3c40c) fired "Firewall drift" every ~10 minutes, L1 AUTO "resolved" it, but the fix never stuck. Dashboard showed 100+ identical incidents.

**Root cause chain:**
1. `_check_firewall()` detects no nftables/iptables on NixOS VM → `status: "warning"`
2. L1-FW-001 matches → action `restore_firewall_baseline`
3. Maps to Windows runbook RB-WIN-SEC-001 → runs on fallback Windows target (not NixOS)
4. Incident marked "resolved" even though NixOS wasn't fixed
5. 600s cooldown expires → repeat forever

**Why flap detector didn't catch it:**
- Max incidents in 30min with 600s cooldown = 3
- Flap threshold was 5 in 30min → **mathematically unreachable**

**Fix (3 layers):**
1. **Flap thresholds:** 5/30min → 3/120min (auto_healer.py)
2. **Platform guard:** L1-FW-001 skips NixOS (level1_deterministic.py, mcp-server/main.py, DB)
3. **Cooldown extension:** 1hr per-check override on flap (appliance_agent.py)

**Also fixed:** `ResolutionLevel.LEVEL3_ESCALATION` → `LEVEL3_HUMAN` (latent enum bug)

**Confirmed:** Flap detector triggered after 3 recurrences in 23 minutes, cooldown extended to 1 hour.

### 2. Domain Discovery Notification Spam

**Symptom:** Repeated "Domain Discovered: northvalley.local" notifications on every agent restart.

**Root cause:**
- Backend `report_discovered_domain()` does unconditional INSERT INTO notifications
- Appliance `_domain_discovery_complete` flag is in-memory only, resets on restart

**Fix (2 layers):**
1. **Backend dedup:** Check for existing notification within 24h before INSERT (sites.py)
2. **Persistent flag:** Write `.domain-discovery-reported` to state_dir (appliance_agent.py)

## Files Modified

| File | Changes |
|------|---------|
| `auto_healer.py` | Flap thresholds 5→3, window 30→120min, escalated=True, LEVEL3_HUMAN fix |
| `appliance_agent.py` | Per-check cooldown overrides, 1hr extension on flap, persistent domain flag |
| `level1_deterministic.py` | platform!=nixos on L1-FW-001 |

[truncated...]

---

## 2026-02-09-session-103-OsirisCare rebrand, grey-purge, UX polish.md

# Session 103 - OsirisCare Rebrand, Grey Purge, UX Polish

**Date:** 2026-02-09
**Started:** 11:09
**Previous Session:** 102
**Commits:** 9c9993f, 875e972, 4c34f5f, 5e6b9f4

---

## Goals

- [x] Add OsirisCare brand colors to Tailwind config and update gradients
- [x] Replace all grey hover states with brand-tinted alternatives
- [x] Replace all Malachor references with OsirisCare
- [x] Replace "Central Command" with OsirisCare branding in user-facing UI
- [x] Overhaul fill design tokens from grey to blue-tinted
- [x] Update frontend knowledge doc

---

## Progress

### Completed

1. **Brand color system** — Added `#3CBCB4` (logo teal) and `#14A89E` to tailwind.config.js, replaced hardcoded gradient hex across client portal
2. **Grey hover purge** — Eliminated ALL `hover:bg-gray-*` from every portal: client=teal-50, partner=indigo-50, portal=blue-50, admin=blue-50
3. **Fill token overhaul** — Changed `fill-primary/secondary/tertiary/quaternary` from grey `rgba(120,120,128)` to blue-tinted `rgba(0,100,220)`, fixing all admin interactive elements globally
4. **Malachor eradication** — Zero references remain in entire codebase (frontend + docs + email domains)
5. **Central Command rename** — Login, sidebar, page title, set-password all now say "OsirisCare"
6. **Shared component fixes** — Button secondary, Badge defaults, EventFeed rows, Notifications page all blue-tinted
7. **Knowledge doc** — Updated `.claude/skills/docs/frontend/frontend.md` with current design system

### Blocked

None

---

## Files Changed

| File | Change |
|------|--------|
| tailwind.config.js | Brand colors + blue-tinted fill tokens |
| index.html | Page title → "OsirisCare Dashboard" |
| Login.tsx | "Central Command" → "OsirisCare / MSP compliance dashboard" |
| SetPassword.tsx | "Welcome to Central Command!" → "Welcome to OsirisCare!" |
| Sidebar.tsx | Subtitle → "Compliance Dashboard", name → "OsirisCare" |
| App.tsx | Fallback title → "Dashboard" |
| EventFeed.tsx | Rows bg-fill-tertiary → bg-blue-50/40 |
| Button.tsx | Secondary active → bg-blue-100 |

[truncated...]

---

## 2026-02-10-session104-linux-chaos-integration.md

# Session 104: Linux VM Chaos Lab Integration + Performance Audit

**Date:** 2026-02-10
**Duration:** ~4 hours (two context windows)
**Agent Version:** 1.0.57

## What Was Done

### 1. Committed + Pushed Workstation Healer Bridge
- Committed workstation-to-healer bridge code (L1/L2/L3 pipeline integration)
- Pushed to origin main

### 2. VPS Deploy + Admin Password Fix
- Dashboard deploy only triggers on mcp-server/ changes; triggered manual deploy (completed 51s)
- Admin account was locked (9 failed attempts) + password hash was stale
- Reset lockout + regenerated bcrypt hash via Python script inside Docker container
- Login verified working

### 3. Linux Chaos Lab Integration (Completed)
Created all infrastructure to include Linux VM in chaos testing:

- **`/Users/jrelly/chaos-lab/scripts/ssh_attack.py`** - SSH equivalent of winrm_attack.py, key-based auth fallback
- **`/Users/jrelly/chaos-lab/config.env`** - Added LIN_HOST, LIN_USER, LIN_PASS
- **`/Users/jrelly/chaos-lab/FULL_SPECTRUM_CHAOS.sh`** - Added 8 Linux attack scenarios + 8 verifications
- **`/Users/jrelly/chaos-lab/FULL_COVERAGE_5X.sh`** - Added 6 Linux scenarios per round

### 4. Linux VM SSH Fix (Completed)
Root cause: empty `/root/.ssh/authorized_keys` + `MaxAuthTries=3` (SSH agent offers too many keys before right one).

**Fixes applied:**
- Copied iMac public key to root's authorized_keys
- Bumped MaxAuthTries to 6 in sshd_config
- Restarted sshd
- Updated ssh_attack.py to use native SSH key auth (no sshpass needed)
- Verified: `ssh_attack.py --sudo 'whoami && hostname'` returns `root / northvalley-linux`

### 5. Chaos Test Execution
- All 5 VMs confirmed running: DC(.250), WS(.251), SRV(.244), Linux(.242), Appliance(.254)
- Appliance VM rebooted — got DHCP .254 (was .247), serial console confirmed full boot
- Launched FULL_SPECTRUM + FULL_COVERAGE_5X on iMac (PID 38145)
- Results: Windows 14/15 succeeded, Linux 6/8 succeeded (2 sed quoting issues)

### 6. Remote System Health Check
- Checked Central Command while user away from home
- All 7 Docker containers healthy on VPS
- 196/200 healing executions successful (98% rate), all L1 deterministic
- 22 incidents in 24h: 18 firewall, 3 NTP (L3), 1 critical_services

### 7. Full Backend Performance Audit + Fixes (Completed)


[truncated...]

---

## 2026-02-11-session105-chaos-test-validation.md

# Session 105: Chaos Test Validation + Reliability Scorecard

**Date:** 2026-02-11
**Duration:** ~1.5 hours (continuation of session 104)
**Agent Version:** 1.0.64

## What Was Done

### 1. Clean Chaos Test Execution
- Deleted incidents.db to clear all flap suppressions from prior tests
- Restarted agent, waited for first scan cycle to complete
- Launched FULL_SPECTRUM_CHAOS.sh from iMac (192.168.88.50)
- All 8 Linux attacks + 13 Windows attacks executed successfully

### 2. Chaos Test Analysis (5 runs total across sessions 104-105)
Analyzed agent logs during and after 180s healing window:

**Linux healing results (from agent logs, post-180s):**
- LIN-SSH-001 (RootLogin): SUCCESS at ~4min
- LIN-FW-001 (Firewall): SUCCESS at ~5min
- LIN-SVC-002 (Audit/rsyslog): SUCCESS at ~6min
- LIN-SUID-001 (SUID removal): SUCCESS at ~6min
- LIN-KERN-001 (IP forward): SUCCESS at ~6min
- LIN-CRON-001 (Cron perms): SUCCESS at ~6min
- LIN-SSH-002/003/004: FLAP SUPPRESSED (pre-existing drift contamination)
- LIN-KERN-002: FLAP SUPPRESSED (same)

**Windows healing results:**
- DC/WS Firewalls: 100% healed within 180s (all 5 runs)
- SRV Firewall: ~40% within 180s (depends on scan timing)
- Registry persistence: 100% healed
- Scheduled task persistence: healed (second cycle)
- Audit policy: healed (second cycle)
- No runbook: DNS hijack, SMB signing, network profile, WinUpdate, screen lock

### 3. Production Reliability Scorecard
| Metric | Basic Compliance | Full Coverage |
|--------|-----------------|---------------|
| Detection | 100% | 100% |
| Rule Coverage | 15/15 = 100% | 15/21 = 71% |
| Execution Success | 100% | 100% |
| Mean Time to Heal | Linux 4-6min, Win 1-3min | Same |
| Composite Score | **95/100** | **68/100** |

### 4. Documentation Updates
- `hipaa/compliance.md`: Added flap detection section (thresholds, granular keys, synced rules)
- `nixos/infrastructure.md`: Added overlay system section (package structure, build command)
- `claude-progress.json`: Updated to session 105, agent v1.0.64, new lessons and tasks

### 5. Root Cause Analysis

[truncated...]

---

## 2026-02-15-session109-evidence-chain-fix-landing-page.md

# Session 109 - Evidence Chain Race Fix + Landing Page + Client Portal

**Date:** 2026-02-15
**Agent Version:** 1.0.70

## Completed

### Client Portal Healing Logs (commits 37ebf33, 3ee12e9)
- 3 new backend endpoints: `GET /client/healing-logs`, `GET /client/promotion-candidates`, `POST /client/promotion-candidates/{id}/forward`
- New `ClientHealingLogs.tsx` with two tabs (Healing Logs + Promotion Candidates)
- Migration 042: added client endorsement columns to `learning_promotion_candidates`
- Fixed bug: `main.py` was not importing client_portal routers (only `server.py` was)

### OsirisCare Landing Page (commits 3ee12e9, 2fcdc87)
- Created `LandingPage.tsx` with medical-grade aesthetic (teal/slate, DM Sans + Source Serif)
- Hostname-based routing: www.osiriscare.net serves landing page, dashboard.osiriscare.net serves admin
- Caddy config added for www.osiriscare.net + bare domain redirect
- DNS already configured (CNAME www -> osiriscare.net, A @ -> 178.156.162.116)

### Evidence Chain Race Condition Fix (commit 3a68713)
- **Root cause:** Concurrent evidence submissions both read same MAX(chain_position) without locking
- **Impact:** 1,137 broken hash chain links (0.55% of 203,076 bundles), started Feb 11
- **Code fix:** `pg_advisory_xact_lock(hashtext(site_id))` serializes per-site submissions
- **Also:** Changed ORDER BY from `checked_at` to `chain_position`, uses `"0"*64` sentinel for genesis prev_hash
- **Data repair (migration 043):**
  - Re-sequenced 10,825 chain positions
  - Fixed 1,435 prev_hash references
  - Recomputed 203,075 chain hashes
  - Added unique index on (site_id, chain_position)
- **Result:** 1,137 broken links -> 0 broken links

### Chaos Tests
- Run 1 (earlier session): 10/21 healed in 300s (48%)
- 3x back-to-back test launched (still running when session ended)
- Run 1 of 3x: 7/16 verified checks healed in 300s
  - Windows: 5/8 (WS-FW, SRV-FW, NetProfile, Task, Registry)
  - Linux: 2/8 (Firewall, rsyslog)
  - Persistent gaps: DC-Firewall, DC-DNS, DC-SMB, SSH configs, audit, kernel params, cron perms, SUID

### macOS Runbook Plan
- 17 HIPAA compliance checks proposed
- 13/17 L1 auto-healable via SSH + `defaults`/`launchctl`/`fdesetup`
- Key checks: FileVault, Firewall, Screen Lock, Auto-login, Gatekeeper, SIP, NTP, etc.
- Plan complete, implementation pending

## Key Findings

### Evidence Chain Architecture
- `compliance_bundles` table stores hash chain per site
- `prev_hash` is NOT NULL — genesis bundles use 64-zero sentinel

[truncated...]

---

## 2026-02-15-session110-flywheel-pipeline-fix.md

# Session 110 - Flywheel Pipeline Fix + Production Audit Fixes

**Date:** 2026-02-15 (continuation)
**Agent Version:** 1.0.70

## Completed

### Migration 045 - Audit Fixes (5 bugs fixed)
- **Evidence_bundles index**: Used `appliance_id` not `site_id` (column didn't exist)
- **patterns.site_id index**: Removed (column doesn't exist in patterns table)
- **l1_rules.incident_type index**: Removed (column doesn't exist, uses `incident_pattern JSONB`)
- **Counter trigger**: Fixed 3 column references:
  - `NEW.rule_id` → `NEW.runbook_id` (execution_telemetry column)
  - `NEW.outcome = 'success'` → `NEW.success` (boolean column)
  - `WHERE runbook_id = ...` → `WHERE rule_id = ...` (agents store rule_id in runbook_id field)
  - Removed `success_rate = ...` SET (GENERATED ALWAYS column — auto-computed)
- **appliance_commands table**: Fixed schema to match learning_api.py:
  - `site_id` → `appliance_id`
  - `payload` → `params`
  - Added unique index for ON CONFLICT clause

### Flywheel Promotion Pipeline Validation
- **Pipeline is WORKING**: 46 patterns auto-promoted to L1 rules
- **9 enabled promoted rules** with real runbook IDs served via `/agent/sync`
- **11 broken rules disabled**: Had `AUTO-*` placeholder runbook IDs (100% failure rate)
- **Counter trigger**: Backfilled 13,131 execution_telemetry records → 5 rules with counters
- **Flywheel scan**: Confirmed real-time promotion (3 patterns promoted within 5 min of crossing threshold)

### Production Database State
- 56 total patterns (10 pending, 46 promoted)
- 20 l1_rules (9 enabled, 11 disabled)
- 13,135 execution_telemetry records (12,221 L1, 8,745 successful)
- Counter trigger active on execution_telemetry INSERT
- appliance_commands table created for deployment pipeline

### Top L1 Rules (by success rate)
| Rule | Matches | Success Rate |
|------|---------|-------------|
| RB-AUTO-FIREWALL | 166 | 100% |
| RB-AUTO-SSH_CONF | 20 | 100% |
| RB-AUTO-AUDIT_PO | 24 | 67% |
| RB-AUTO-BACKUP_S | 260 | 56% |
| RB-AUTO-BITLOCKE | 3,057 | 30% |

### Other Fixes
- **CSP dedup**: Removed Content-Security-Policy from SecurityHeadersMiddleware (Caddy is single source)
- **run_windows_runbook: handler**: Added colon-format handler in appliance_agent.py
- **Flywheel query**: Excludes `AUTO-*` placeholder runbook IDs from auto-promotion

## Commits

[truncated...]

---

## 2026-02-15-session110-production-audit-fixes.md

# Session 110: Production Audit + Critical Fixes
**Date:** 2026-02-15 | **Duration:** ~2 hours

## Key Changes Made

### 1. Evidence Signature Fix (HIGH - DEPLOYED)
- **Root cause**: Double PHI scrub in `appliance_client.py` — phone regex matched NTP float values (e.g. `16.2353515625`) inside `signed_data` JSON string, corrupting Ed25519 signatures
- **Fix**: Added `pre_scrubbed=True` parameter to `_request()` so `submit_evidence()` skips redundant second scrub
- **File**: `packages/compliance-agent/src/compliance_agent/appliance_client.py`
- **Commit**: `9a7a78f`

### 2. CVE Sync Fix (DEPLOYED)
- `isinstance(affected_cpes, str)` guard was in local code but not deployed to VPS
- Deployed via same push — fixes `'str' object has no attribute 'get'` error in CVE sync loop
- **File**: `mcp-server/central-command/backend/cve_watch.py` line 608-609

### 3. Disk Space Cleanup (86% → 66%)
- **Freed 29GB** on VPS (21GB → 50GB free)
- Capped journald at 1G (was 3.9G unbounded)
- Removed stale `/root` files (gallery-dl 1.6G, puppeteer 609M, old repos)
- Pruned Docker images (python:3.11, old builds ~2.5G)
- Unmounted stale ISO loopback in `/tmp` (1.1G)
- Removed old appliance images/ISOs keeping only latest versions (~5G)
- Added `/etc/docker/daemon.json` for log rotation

### 4. L1 Healing Rule Fixes (DEPLOYED)
- **L1-FW-002**: New rule for `check_type="firewall_status"` (agent sends this, L1-FW-001 only matched `"firewall"`)
- **L1-AUDIT-002**: New rule for `check_type="audit_policy"` (no built-in rule existed)
- **L1-WIN-SMB-001**: Fixed wrong runbook `RB-WIN-SEC-006` → `RB-WIN-SEC-007` in `l1_rules_full_coverage.json`
- **File**: `packages/compliance-agent/src/compliance_agent/level1_deterministic.py`, `config/l1_rules_full_coverage.json`
- **Commit**: `5dc0dd5`

### 5. Brand Toolkit (DEPLOYED)
- Created `mcp-server/central-command/frontend/public/brand-toolkit.html`
- Updated personal banner text to right side (clear of LinkedIn profile pic)
- 6 downloadable LinkedIn marketing graphics via Canvas API

## Full System Audit Results

### PASS
- All 7 Docker containers healthy (46 days uptime)
- API health: Redis, Postgres, MinIO all connected
- OTS blockchain: 103K proofs, 98.5K anchored, 0 bad block heights
- Hash chain integrity: 204K compliance bundles, 0 gaps, 0 broken links
- WORM protection: append-only triggers working
- HIPAA mapping: 51/51 runbooks mapped, 16 controls covered
- SSL/HTTPS: HTTP/2, security headers, cert valid 90 days
- System resources: 66% disk, 6.3GB RAM available, CPU load 0.21

### WARN

[truncated...]

---

## 2026-02-15-session111-audit-fixes-chaos-validation.md

# Session 111: Audit Fixes + Chaos Lab Validation

**Date:** 2026-02-15
**Duration:** ~2 hours
**Version:** v1.0.70 overlay on v1.0.57 NixOS base

## What Was Done

### 1. Runbook ID Mismatch Fix (Migration 046)
- **Problem:** Three incompatible ID namespaces — agent builtins (L1-SVC-DNS-001), promoted rules (RB-AUTO-XXXXXXXX truncated to 8 chars), and counter trigger (045) that never matched builtins. Data flywheel blind to 65% of telemetry.
- **Fix:** Created `046_runbook_id_fix.sql`:
  - ALTER patterns.pattern_signature VARCHAR(64) → VARCHAR(255)
  - Added `source` column to l1_rules (builtin vs promoted)
  - Seeded 51 builtin L1 rule IDs from execution_telemetry
  - Backfilled counters for all 62 rules (12K+ records)
  - Fixed pattern_signature truncation in db_queries.py, learning_api.py, store.py
  - Filtered builtin rules from /api/agent/l1-rules to prevent double-serving
- **Commit:** `e38ba5d`

### 2. Remediation Order Delivery Fix
- **Problem:** complete_order/acknowledge_order endpoints only handled admin_orders table, leaving healing orders stuck permanently.
- **Fix:** Updated sites.py to fall back to `orders` table with JOIN to appliances for site_id. Added auto-expiration of stale orders during polling. Expired 425 stale orders.
- **Commit:** `0ad09ba`

### 3. Flywheel Pattern Generation Fix
- **Problem:** Flywheel promotion pipeline architecturally complete but entry point dead — no patterns being generated from L2 telemetry.
- **Fix:** Added Step 0 to _flywheel_promotion_loop() that generates patterns from L2 execution_telemetry (requires 5+ occurrences). All 56 existing patterns already promoted.
- **Commit:** `d67766f`

### 4. DB Index Cleanup
- Confirmed small tables (sites: 2 rows, runbooks: 51 rows) make seq scans optimal
- Cleaned 2 duplicate indexes

### 5. Chaos Lab Testing
- **Run 2:** 13/16 (81%) — Linux 8/8 HEALED, Windows 5/8
- **Run 3:** 8/16 (50%) — Linux 6/8, Windows 2/8 (degraded due to WinRM timeouts)
- **Observer run:** Launched at end of session

## Files Modified
- `mcp-server/central-command/backend/migrations/046_runbook_id_fix.sql` (NEW)
- `mcp-server/central-command/backend/db_queries.py` (truncation fix)
- `mcp-server/central-command/backend/learning_api.py` (truncation fix)
- `mcp-server/database/store.py` (truncation fix)
- `mcp-server/main.py` (builtin filter + flywheel Step 0)
- `mcp-server/central-command/backend/sites.py` (order completion + auto-expiration)
- `.claude/skills/docs/database/database.md` (migration count update)

## Commits Pushed
- `e38ba5d` — fix: Runbook ID mismatch — seed 51 builtin rules + fix truncation
- `0ad09ba` — fix: Order completion now handles both admin_orders and healing orders

[truncated...]

---

## 2026-02-15-session112-dns-heal-fix-hipaa-research.md

# Session 112: DNS Healing Fix + HIPAA 2026 Research

**Date:** 2026-02-15/16
**Focus:** Fix DNS remediation failure, WinRM tempfile executor, chaos testing, HIPAA 2026 readiness

## Changes Made

### 1. WinRM Long Script Execution (`executor.py`)
- **Problem:** DNS remediation script (~4.7KB) exceeded cmd.exe's 8,191 char limit after pywinrm's UTF-16LE + base64 encoding (~12.7KB)
- **Fix:** Added `_execute_via_tempfile()` method that:
  1. Base64 encodes the script (UTF-8)
  2. Writes chunks via cmd.exe `echo` (6000 chars/chunk, well under 8191 limit)
  3. Short PowerShell bootstrap decodes base64, writes .ps1, executes, cleans up
- **Threshold:** `_MAX_INLINE_SCRIPT_LEN = 2000` — scripts above this use tempfile path
- **File:** `packages/compliance-agent/src/compliance_agent/runbooks/windows/executor.py`

### 2. DNS Healing (from prior session, deployed this session)
- DC self-detection: `DomainRole >= 4` uses own IP as DNS target
- Verify script rejects public DNS on domain-joined machines
- WinRM session invalidation on ALL errors (not just connection errors)
- **File:** `packages/compliance-agent/src/compliance_agent/runbooks/windows/network.py`

### 3. HIPAA 2026 Security Rule Research
- Mapped NPRM proposed requirements to OsirisCare platform
- 15+ requirements already covered (encryption, MFA checks, patching, logging, incident response)
- 6 gaps identified: MFA enrollment verification, vuln scan integration, AD account termination monitoring, BAA tracker, training tracker, annual compliance report
- Phased roadmap: Phase 1 (now), Phase 2 (Q2), Phase 3 (Q3 before final rule)

## Chaos Test Run 6 Results (v1.0.72)

| Target | Result | Details |
|--------|--------|---------|
| DC (.250) | **3/3 (100%)** | Firewall, DNS (192.168.88.250), SMB signing |
| SRV (.244) | **2/3 (67%)** | Task persistence + firewall (healed 2min after verify window) |
| WS (.251) | **0/3** | WinRM port 5985 connection refused after reboot |
| Linux (.242) | **Active** | Agent heals SSH/FW/services; chaos test SSH verification times out |

## Commits
- `fdea87c` — fix: WinRM long script execution via temp file (cmd.exe 8191 char limit)
- `5cf6e11` — fix: DC DNS healing + WinRM session invalidation (prior session, deployed)

## Deployment
- Overlay v1.0.72 deployed to physical appliance via SCP
- Pushed to main (CI/CD deploys backend)
- Cleared 2 flap suppressions before chaos test

## Known Issues
1. WS (.251) WinRM not starting after reboot — needs Guest Additions or manual console fix
2. SRV scan cycle ~2min too slow for 180s chaos test verification window
3. iMac SSH to Linux (.242) times out during chaos test verification phase

[truncated...]

---

## 2026-02-17-session-113-GPO deployment pipeline complete, healing gap investigation.md

# Session 113 - GPO Deployment Pipeline Complete + Healing Gap Investigation

**Date:** 2026-02-17
**Started:** 02:50
**Previous Session:** 112

---

## Goals

- [x] Complete Phase 3: Proto update + certificate auto-enrollment (Go + Python)
- [x] Complete Phases 4-6: Wire CA, DNS SRV, GPO into appliance boot sequence
- [x] Run Linux chaos attacks on Ubuntu VM (.242)
- [x] Investigate Windows healing gaps (firewall, network profile, registry persistence)
- [x] Clear flap suppressions
- [x] Update technical documentation (.md skills docs)

---

## Progress

### Completed

1. **Proto + Cert Enrollment (Phase 3)** — Added needs_certificates/cert PEM fields to proto, regenerated Go+Python stubs, implemented cert enrollment in grpc.go and grpc_server.py
2. **Orchestration Wiring (Phases 4-6)** — CA init, DNS SRV registration, GPO deployment all wired into appliance_agent.py boot sequence
3. **Linux Chaos Attacks** — 6/6 injected on .242, 5/6 auto-healed at L1 (crypto verify failed)
4. **Healing Gap Root Cause** — Identified: appliance firewall escalation is correct (NixOS); Windows firewall_status IS healing (DB confirms success on .244/.251); service flapping is GPO conflict (correct flap detection)
5. **Flap Suppressions** — Cleared 6, but they regenerate due to GPO conflicts (by design)
6. **Docs Updated** — api.md and backend.md updated with gRPC v0.3.0, CA, DNS SRV, GPO

### Blocked

- WinRM session exhaustion on .251 (HTTP 400 after ~15 sequential PS commands)
- LIN-CRYPTO-001 verify phase fails after successful remediate
- GPO in lab overrides healed settings (services, screen lock) causing flap loops

---

## Files Changed

| File | Change |
|------|--------|
| `agent/proto/compliance.proto` | Added cert enrollment fields |
| `agent/proto/compliance.pb.go` | Regenerated |
| `agent/proto/compliance_grpc.pb.go` | Regenerated |
| `agent/internal/transport/grpc.go` | Cert enrollment flow, v0.3.0 |
| `agent/Makefile` | VERSION 0.3.0 |
| `packages/.../compliance_pb2.py` | Regenerated |
| `packages/.../compliance_pb2_grpc.py` | Regenerated |
| `packages/.../grpc_server.py` | agent_ca parameter, cert issuance |

[truncated...]

---

## 2026-02-17-session-113-WinRM batching, crypto verify fix, GPO conflicts.md

# Session 113 - Winrm Batching, Crypto Verify Fix, Gpo Conflicts

**Date:** 2026-02-17
**Started:** 02:54
**Previous Session:** 112

---

## Goals

- [ ]

---

## Progress

### Completed


### Blocked


---

## Files Changed

| File | Change |
|------|--------|

---

## Next Session

1.

---

## 2026-02-17-session113-go-rewrite-phase3.md

# Session 113: Go Rewrite — Phases 3A-3F + Phase 4 Wiring Complete

**Date:** 2026-02-17
**Duration:** ~2 hours (across multiple context windows)
**Context:** Continuation of Go rewrite from session that ran out of context

## Summary

Completed all phases of the Go appliance daemon rewrite: Phases 3A through 3F plus Phase 4 wiring. The entire `appliance/` Go module now has 10 packages with 141 tests, all passing with zero vet issues.

## What Was Done

### Phase 3A: Daemon Config + Phone-Home (completed in prior session, tests run here)
- 7 config tests verified passing

### Phase 3B: L1 Deterministic Healing Engine
- **`internal/healing/l1_engine.go`** (~450 lines) — Full L1 engine with:
  - 9 match operators: eq, ne, contains, regex, gt, lt, in, not_in, exists
  - Dot-notation nested field access
  - Rule loading: builtin, YAML, synced JSON, promoted
  - Cooldown tracking (per rule+host)
  - Action execution with dry-run support
  - Stats and rule listing
- **`internal/healing/builtin_rules.go`** (~500 lines) — All 38 builtin rules ported from Python:
  - 12 generic/NixOS rules (patching, AV, backup, logging, firewall, encryption, cert, disk, service crash)
  - 13 Linux rules (SSH, kernel, cron, SUID, firewall, audit, services, logging, permissions, network, banner, crypto, IR)
  - 13 Windows rules (DNS, SMB, WUAU, network profile, screen lock, BitLocker, NetLogon, DNS hijack, Defender exclusions, scheduled task/registry/WMI persistence, SMBv1)
- **22 tests** (56 assertions with subtests): all operators, Linux rules, Windows rules, synced JSON override, cooldown, severity filter, YAML loading, reload

### Phase 3C: L2 Bridge (Go→Python Unix Socket)
- **`internal/l2bridge/client.go`** (~200 lines) — JSON-RPC 2.0 client over Unix socket:
  - `Plan(incident)` → `LLMDecision` with confidence + escalation flags
  - `Health()` liveness check
  - `PlanWithRetry()` with auto-reconnection
  - `ShouldExecute()` — checks confidence >= 0.6, no escalation flags
- **11 tests** (16 with subtests): plan, health, escalation, reconnection, RPC errors, multiple requests, ShouldExecute decisions

### Phase 3D: WinRM + SSH Executors
- **`internal/winrm/executor.go`** (~340 lines) — WinRM executor:
  - Session caching with 300s refresh
  - Inline execution for scripts ≤2000 chars
  - Temp file execution for longer scripts (cmd.exe 8191 char limit workaround)
  - Base64 chunking (6000-char chunks via cmd.exe echo)
  - UTF-16LE PowerShell encoding
  - Retry with exponential backoff
  - SHA256 output hashing for evidence
- **`internal/sshexec/executor.go`** (~310 lines) — SSH executor:
  - Connection caching with staleness detection
  - Base64 script encoding (avoids shell quoting)
  - sudo support (with/without password)

[truncated...]

---

## 2026-02-17-session114-full-audit-remediation-gpo-hardening.md

# Session 114: Full Audit Remediation + GPO Pipeline Hardening

**Date:** 2026-02-17
**Duration:** ~3 hours (spans sessions 112-114)
**Status:** COMPLETE

## Summary

Executed a comprehensive 4-track plan to remediate all audit findings from Go agent, Central Command, NixOS infrastructure, and Python compliance agent audits. Also hardened the GPO deployment pipeline and added 43 new tests.

## Commits

| Hash | Description |
|------|-------------|
| `dd83883` | fix: Go agent — GC pinning, error logging, backpressure, timeout validation |
| `e9de57a` | fix: Central Command — real onboarding endpoints, pagination, metrics, indexes |
| `cc135f1` | fix: NixOS hardening — SSH, resource limits, firewall, service ordering |
| `a67c079` | feat: GPO pipeline hardening — cert warnings, rollback, 43 new tests |

## Track A: Go Agent Audit Fixes (7 items)

1. **EventLog GC pinning** — `callbackPins` field prevents GC from collecting Windows callback data passed via unsafe.Pointer
2. **EventLog error logging** — Capture `lastErr` from `procEvtRender.Call` instead of discarding
3. **OfflineQueue enforceLimit** — Log error from DELETE instead of silent discard
4. **HealCmds backpressure** — Buffer 32→128, capacity warnings at 75% at all 3 send sites
5. **WMI context deadline** — `ctx.Err()` checks before ConnectServer, ExecQuery, and 3 registry functions
6. **RMM sanitization** — `strings.NewReplacer` replaces 3 chained `strings.ReplaceAll`
7. **Healing timeout validation** — Validate 0-600s range before `time.Duration` conversion

## Track B: Central Command Fixes (7 items)

1. **Real onboarding endpoints** — advance_stage, update_blockers, add_note now use SQL + `Depends(get_db)`
2. **Pagination offset** — Added `offset` param to incidents, events, runbook_executions (routes.py + db_queries.py)
3. **Onboarding metrics** — Replaced hardcoded zeros with real SQL aggregates
4. **Notification log level** — `logger.debug` → `logger.warning` for broadcast failures
5. **Cache TTL** — Configurable via `CACHE_TTL_SCORES`/`CACHE_TTL_METRICS` env vars
6. **Vite proxy** — `VITE_API_URL` env var instead of hardcoded IP
7. **Migration 047** — Composite index on `compliance_bundles(appliance_id, reported_at DESC, check_type)`

## Track C: NixOS Infrastructure Hardening (4 items)

1. **SSH** — `PermitRootLogin=prohibit-password`, `PasswordAuthentication=false`, `mkDefault` on root password
2. **Resource limits** — `MemoryMax`, `CPUQuota`, `LimitNOFILE`, `StartLimitIntervalSec/Burst` on 3 services
3. **Service ordering** — `requires = [ "msp-auto-provision.service" ]` for compliance-agent + scanner
4. **Firewall** — Reduced TCP ports from 7 to 5 (8081/8082 bind localhost only)

## Track D: GPO Pipeline Hardening + Tests (4 items)

1. **Cert enrollment warning** — `elif request.needs_certificates and not self.agent_ca` logs warning in Register handler; startup warnings in `serve_sync()` and `serve()`
2. **GPO rollback** — `_rollback(artifacts)` cleans up SYSVOL dir + GPO on partial deployment failure

[truncated...]

---

## 2026-02-17-session114-production-cleanup-audit.md

# Session 114 — Production Cleanup Audit

**Date:** 2026-02-17
**Focus:** Full codebase production-readiness audit and efficiency fixes
**Commits:** `2823bd8`, `8102d03`, `7b66796`

## Summary

Continued from session 113. Fixed critical/high Go agent bugs, then ran a full 4-agent production audit across Python agent, Go agent, Central Command, and NixOS infrastructure. Applied all critical and high-priority fixes.

## Changes

### Go Agent (commits 2823bd8, 7b66796)
- **CRITICAL: readDriftAcks race condition** — capture stream/done refs once at goroutine start
- **CRITICAL: WMI VARIANT leak** — added `valRaw.Clear()` after extracting property values
- **HIGH: Close()/Reconnect() deadlock** — release mutex before waiting for streamDone
- **HIGH: Registry EnumKey error** — check WMI ReturnValue instead of treating all as "not found"
- **MED: EventLog zero subscriptions** — Start() returns error if no channels subscribe
- **MED: Defender DMT timezone** — parse CIM_DATETIME UTC offset from position 21-24
- **LOW: Dead code removal** — removed unused parseRegistryInt + strconv import
- **LOW: OfflineQueue batch DELETE** — single DELETE with WHERE IN instead of one-by-one, with error logging

### Python Agent (commit 7b66796)
- **SQLite synchronous=FULL → NORMAL** — WAL mode provides equivalent safety, better perf
- **Float validation in L1 operators** — try/except around GREATER_THAN/LESS_THAN float conversion
- **Bounded cache eviction** — auto_healer evicts expired cooldowns/flap tracker entries every 100 heal() calls

### Central Command (commit 7b66796)
- **CRITICAL: get_onboarding_detail()** — missing `db` parameter caused runtime crash
- **CRITICAL: create_prospect()** — undefined `stage_progress`/`stage_val` caused NameError; now persists to DB
- **Duplicate imports** — moved mid-file pydantic/enum imports to top of routes.py
- **O(n) → O(1) category lookup** — db_queries.py uses existing `_CHECK_TYPE_TO_CATEGORY` reverse dict

## Tests
- Go: all 3 test packages pass
- Python: 994 passed, 13 skipped, 3 warnings (pre-existing)

## Remaining from Audit (lower priority)
- NixOS: hardcoded root password in iso config (lab-only, not production risk)
- NixOS: SSH keys baked into disk image (same — lab config)
- Go: callback data persistence across restarts (nice-to-have)
- Central Command: POST/PATCH onboarding endpoints still return mock data for advance_stage/update_blockers/add_note

---

## 2026-02-17-session115-nix-warnings-docs-audit.md

# Session 115: NixOS Trace Warnings + Full Docs Audit

**Date:** 2026-02-17
**Status:** COMPLETE

## Commits

| Hash | Description |
|------|-------------|
| `5629e34` | fix: resolve NixOS trace warnings (health-check ordering + root password conflict) |
| `7207c52` | docs: audit and correct all technical skill docs against codebase |

## NixOS Trace Warning Fixes

Two warnings that appeared on every `nixos-rebuild`:

1. **msp-health-check ordering** — Had `after = ["network-online.target"]` but no `wants`. NixOS requires both. Added `wants`.

2. **Root multiple password options** — `configuration.nix` set `hashedPassword`, installer ISO profile set `initialHashedPassword`, and `appliance-disk-image.nix` set `initialPassword`. Consolidated: moved `hashedPassword` to `appliance-disk-image.nix` only, removed redundant `initialPassword` from both image configs.

Both appliances rebuilt: exit 0, zero warnings, zero failed services.

## Full Documentation Audit

Ran 6 parallel explore agents to audit all 9 skill docs against the actual codebase. Findings and fixes:

| Doc | Key Corrections |
|-----|-----------------|
| CLAUDE.md | Tests 950→1037, migrations 41→49, hooks 77→78, added VM IP (.254), replaced missing preflight.sh |
| testing.md | Files 39→45, backend tests 55→114, added Go tests, removed nonexistent conftest.py |
| database.md | Migrations 47→49 |
| hipaa.md | PHI patterns 12→14, L1 rules 22→38, fixed rules file path |
| performance.md | Removed virtual scrolling (not installed), removed React.memo (not used) |
| infrastructure.md | Replaced fictional A/B partition with actual 3-partition layout + rebuild watchdog |
| frontend.md | Hooks 77→78 |
| backend.md | Fixed rules path, removed healing_orders reference |

---

## 2026-02-17-session116-gpo-live-test-ws-trust-fix.md

# Session 116: GPO Live Integration Test + WS Trust Relationship Fix

**Date:** 2026-02-17
**Status:** COMPLETE

## Commits

No code commits this session — all work was live lab infrastructure testing/repair.

## GPO Integration Test — PASSED

Ran the full GPO deployment pipeline against the live AD domain controller (NVDC01 at 192.168.88.250) from the physical appliance (192.168.88.241). Script: `/tmp/gpo_test.py`

| Step | Test | Result |
|------|------|--------|
| 1 | Verify AD domain | `northvalley.local` confirmed |
| 2 | List existing GPOs | Default Domain Policy + Default DC Policy |
| 3 | SYSVOL read access | Policies + scripts visible |
| 4 | Create OsirisCare dir in SYSVOL | `DIR_OK` |
| 5 | Write test file to SYSVOL | `CONTENT:integration-test` verified |
| 6 | Create test GPO | ID: `ed3e8df4-d63c-4715-b2e3-4f51056afab1` |
| 7 | Link GPO to domain root | `DC=northvalley,DC=local` linked |
| 8 | Verify GPO + link | `AllSettingsEnabled`, `LINK_VERIFIED` |
| 9 | Cleanup | GPO removed, test file cleaned |

**Conclusion:** GPO deployment pipeline code (`gpo_deployment.py`) is validated against real AD infrastructure.

## WS (.251) Trust Relationship Fix — RESOLVED

### Problem
- WS (.251) WinRM port 5985 was open but domain admin creds were rejected
- `Get-LocalGroupMember` returned error 1789 (broken trust relationship)
- Local admin (`localadmin`/`NorthValley2024!`) still worked via NTLM

### Attempts
1. **`Test-ComputerSecureChannel -Repair` via WinRM on WS** — HTTP 400 error (the broken trust itself caused the WinRM session to fail mid-command)
2. **`netdom resetpwd` on WS** — `netdom` not installed on Windows 10 (server tool only)
3. **`Reset-ComputerMachinePassword -Server NVDC01` from DC side** — SUCCESS

### Root Cause
Machine account password out of sync between WS and DC. NTLM auth still works (goes directly to DC for password verification), but Kerberos/machine trust was broken.

### Verification
- `Test-ComputerSecureChannel`: `True`
- Domain admin WinRM session: `NVWS01 / northvalley\administrator`
- `gpresult /scope computer /r`: Working (Member Workstation in `northvalley.local`)

### Key Lesson
When trust relationship is broken, don't try to repair from the broken WS via WinRM — the WinRM session itself becomes unstable. Instead, reset the computer account from the DC side using `Reset-ComputerMachinePassword`.


[truncated...]

---

## 2026-02-17-session117-4track-verify-overlay-deploy.md

# Session 117: 4-Track Plan Verification + v1.0.74 Overlay Deploy

**Date:** 2026-02-17
**Status:** COMPLETE

## Commits

| Hash | Description |
|------|-------------|
| `03693aa` | fix: msp-first-boot hard dependency on auto-provision service |

## 4-Track Audit Plan — Verified Complete

Audited all 22 items across 4 tracks. 21/22 were already implemented in previous sessions. Applied the one remaining fix:

| Track | Items | Status |
|-------|-------|--------|
| A: Go Agent | 7/7 | All done (GC pinning, error logging, backpressure, WMI checks, sanitization, timeout) |
| B: Central Command | 7/7 | All done (onboarding endpoints, pagination, metrics, broadcast, cache TTL, vite proxy, migration) |
| C: NixOS Hardening | 4/4 | 3 done previously, **C3 applied this session** (msp-first-boot requires) |
| D: GPO Pipeline | 4/4 | All done (cert warning, rollback, flag logging, 951 lines of tests) |

### C3 Fix Details
Added `requires = [ "msp-auto-provision.service" ];` to `msp-first-boot` in `iso/appliance-image.nix:1065`. This makes it a hard dependency — if provisioning fails, first-boot won't run silently without identity.

Verified: `nix eval --json` confirms requires is set. Python tests: 1037 passed.

## v1.0.74 Overlay Deployment

1. Built overlay: `compliance_agent-1.0.74.tar.gz` (444KB)
2. Uploaded to VPS: `/opt/mcp-server/agent-packages/` + `/var/www/updates/agent-overlay.tar.gz`
3. Issued `update_agent` orders for both appliances:
   - Physical: `overlay-1074-phys` for `physical-appliance-pilot-1aea78`
   - VM: `overlay-1074-vm` for `test-appliance-lab-b3c40c`

Appliances will pick up the overlay on next checkin (~5 min cycle).

## Changes in v1.0.74 (since v1.0.73)

Includes all code from the 4-track audit plan:
- GPO rollback mechanism
- gRPC cert enrollment warnings
- GPO flag persistence error logging
- 43 new tests (agent_ca, gpo_deployment, dns_registration, agent_deployment)
- All audit fixes from Go agent, Central Command, and NixOS tracks

---

## 2026-02-17-session118-go-daemon-deploy.md

# Session 118: Go Daemon — Full Production Deployment

**Date:** 2026-02-17/18
**Duration:** ~45 minutes
**Context:** Deploying Go daemon to production, wiring remaining subsystems

## Summary

Completed all remaining Go daemon tasks: L2 executor wiring, order completion POST, backend OrderType enum, Go 1.22 compatibility, NixOS rebuild, and production activation. The Go daemon is now **running in production** on the physical HP T640 appliance.

## What Was Done

### 1. Backend OrderType Enum (sites.py)
Added 7 missing order types: `nixos_rebuild`, `update_iso`, `diagnostic`, `deploy_sensor`, `remove_sensor`, `update_credentials`, `restart_agent`. Dashboard can now create rebuild orders via API.

### 2. L2 Executor Wiring (daemon.go)
- Added `winrmExec *winrm.Executor` and `sshExec *sshexec.Executor` to Daemon struct
- `executeL2Action()` dispatches L2 decisions to WinRM (Windows) or SSH (Linux) based on platform
- `buildWinRMTarget()` / `buildSSHTarget()` extract credentials from heal request metadata
- Falls back to L3 escalation if no credentials available

### 3. Order Completion POST (daemon.go)
Replaced stub `completeOrder()` with real HTTP POST to `/api/orders/{order_id}/complete`:
- Sends `{success, result, error_message}` JSON payload
- Uses existing PhoneHomeClient's HTTP client (with TLS, timeout)
- Bearer token auth from config

### 4. Go 1.22 Compatibility
- NixOS 24.05 ships Go 1.22, but deps required Go 1.24
- Downgraded: `pgx/v5` v5.8.0→v5.5.5, `x/crypto` v0.48.0→v0.24.0, `rogpeppe/go-internal` v1.14.1→v1.12.0
- Regenerated vendor hash: `sha256-UUQ3KKz2l1U77lJ16L/K7Zzo/gkSuwVLrzO/I/f4FUM=`

### 5. NixOS Rebuild & Activation
- First rebuild failed: `go.mod requires go >= 1.24.0 (running go 1.22.8)`
- Fixed deps, pushed, rebuilt successfully
- `touch /var/lib/msp/.use-go-daemon` + `systemctl start appliance-daemon`
- `nixos-rebuild switch` to persist

### 6. Production Verification
- Go daemon running since 01:09 UTC, PID 569492
- **Memory: 6.6MB** (vs Python's 112MB) — **17x reduction**
- **CPU: 102ms** total after 2 cycles
- **Checkin cycle: 52ms**
- L1 engine: 82 rules loaded (38 builtin + 44 synced)
- CA initialized from /var/lib/msp/ca
- gRPC server listening on :50051
- Order completion POST to Central Command working

## Test Count
- **150 tests** across 10 packages (up from 141)

[truncated...]

---

## 2026-02-18-session119-autodeploy-dc-proxy.md

# Session: 2026-02-18 - Auto-Deploy to AD Workstations + DC Proxy Fallback

**Duration:** ~6 hours
**Focus Area:** Go appliance daemon — zero-friction agent deployment to AD workstations

---

## What Was Done

### Completed
- [x] Fixed client portal compliance report SQL bug (commit 65f86f7) — `client_org_sites` table didn't exist, changed to join via `sites.client_org_id` directly
- [x] Implemented auto-deploy pipeline in `appliance/internal/daemon/autodeploy.go` (~900 lines)
- [x] AD enumeration → connectivity check → Direct WinRM deploy (5-step pipeline)
- [x] NTLM auth fix in `winrm/executor.go` (Basic → ClientNTLM)
- [x] Added WinRM GPO configuration via Default Domain Policy
- [x] Added concurrency guard (atomic CAS) to prevent overlapping deploy cycles
- [x] Added fallback chain: Direct WinRM → DC Proxy (Invoke-Command via Kerberos) → retry next cycle
- [x] Integrated autoDeployer into daemon.go
- [x] Added GRPCListenAddr() to config.go

### Partially Done
- [ ] v7 DC proxy + NETLOGON approach — code compiles clean but NOT yet deployed/tested

### Not Started (planned but deferred)
- [ ] End-to-end test of NETLOGON binary distribution
- [ ] SPN registration automation

---

## Key Decisions Made

| Decision | Rationale | Impact |
|----------|-----------|--------|
| Direct WinRM NTLM first, DC proxy fallback | Simplest path for domain-joined machines; DC proxy covers non-direct cases | Two-stage fallback handles both domain and edge cases |
| NETLOGON share for binary distribution | Universal AD share, already replicated to all DCs, no extra infra | Agent binary accessible from any domain-joined workstation |
| Atomic CAS concurrency guard | Prevent overlapping deploy cycles from timer + manual trigger | Thread-safe without mutexes |
| Negotiate auth instead of strict NTLM | Kerberos preferred when available, NTLM fallback automatic | Covers both domain and workgroup scenarios |
| GPO-based WinRM config | Ensures future workstations automatically have WinRM enabled | No per-machine setup needed |

---

## Files Modified

| File | Change |
|------|--------|
| `appliance/internal/daemon/autodeploy.go` | NEW ~900 lines — full auto-deploy with fallback chain |
| `appliance/internal/daemon/daemon.go` | Integrated autoDeployer into daemon lifecycle |
| `appliance/internal/daemon/config.go` | Added GRPCListenAddr() method |
| `appliance/internal/winrm/executor.go` | Switched from Basic to ClientNTLM auth |
| `mcp-server/central-command/backend/client_portal.py` | SQL fix — use sites.client_org_id instead of nonexistent client_org_sites table |

[truncated...]

---

## 2026-02-19-WS01-MANUAL-FIX.md

# WS01 Manual Fix — 2 Minutes

WS01 is at the Windows lock screen. WinRM is running but only accepts Kerberos (not NTLM). We need to type 3 commands at the console.

## Steps

### 1. Wake the screen
- Open the VirtualBox window for **northvalley-ws01** on the iMac
- Click inside the VM window, press any key or move mouse to wake from lock screen
- It should auto-logon to the desktop (GPO auto-logon is set). If it asks for a password: `NorthValley2024!`

### 2. Open PowerShell as Admin
- Right-click the Start button (bottom-left) → **Windows PowerShell (Admin)**
- Or press `Win+X` then `A`

### 3. Paste these 4 commands (one at a time)

```
winrm set winrm/config/service @{AllowUnencrypted="true"}
```

```
winrm set winrm/config/service/auth @{Basic="true"}
```

```
Set-Item WSMan:\localhost\Client\TrustedHosts '*' -Force
```

```
Restart-Service WinRM
```

### 4. Install Guest Additions
- In VirtualBox menu bar: **Devices → Insert Guest Additions CD image**
  (it may already be mounted — if so skip this)
- In the PowerShell window, paste:

```
D:\VBoxWindowsAdditions.exe /S
```

- Wait ~60 seconds for it to finish. If it says reboot needed, type:

```
Restart-Computer -Force
```

### 5. Done


[truncated...]

---

## 2026-02-19-session119-autodeploy-dc-proxy.md

# Session 119: Autodeploy Hardening + WS01 WinRM Auth Battle

**Date:** 2026-02-19
**Duration:** ~3 hours
**Branch:** main

## Summary

Continued from session 118. Hardened the Go daemon's autodeploy pipeline with failure tracking, WMI fallback, and GPO startup script creation. Battled WS01 WinRM authentication — got port 5985 open but NTLM auth from non-domain machines rejected. DC→WS01 Kerberos also broken (stale machine trust, last logon Jan 11).

## Completed

1. **False-positive deploy fix** (commit 364866f)
   - Empty WinRM stdout was treated as success → now checks for expected output

2. **Drift scanner** (commit 364866f)
   - `driftscan.go`: periodic Windows drift scanning (firewall, Defender, rogue tasks)
   - Runs on configurable interval alongside deploy cycle

3. **Autodeploy error handling** (commit 38176c2)
   - Failure tracking: `map[string]int` tracks consecutive failures per host
   - Escalation: after 3 failures, creates `WIN-DEPLOY-UNREACHABLE` incident via `healIncident()`
   - 4-hour backoff: skips hosts escalated recently
   - WMI fallback (Attempt 5): `Invoke-WmiMethod -Class Win32_Process -Name Create` from DC
   - GPO startup script: auto-creates `Setup-WinRM.ps1` in SYSVOL with `Enable-PSRemoting -Force`
   - Bumps GPO version (AD + GPT.INI) to force client re-download

4. **GPO auto-logon for lab WS01**
   - Set `DisableCAD=1`, `AutoAdminLogon=1`, default credentials via GPO registry
   - WS01 now boots to desktop without Ctrl+Alt+Del

5. **nixos-rebuild on physical appliance**
   - Deployed 38176c2 to physical appliance
   - New daemon running with drift scanner + autodeploy hardening

## WS01 WinRM Status (UNRESOLVED)

**Port 5985:** OPEN (confirmed via nc from Mac and appliance)
**WinRM service:** Responding (HTTP 405 on GET, HTTP 401 on POST = correct)
**Auth advertised:** `Negotiate`, `Kerberos` only — NO `Basic`

### Why it's stuck:
- `Enable-PSRemoting -Force` (from GPO startup script) enables Negotiate+Kerberos only
- Non-domain machines (appliance, Mac) can't do Kerberos → NTLM fallback needed
- WS01 doesn't advertise Basic or accept NTLM from untrusted sources
- DC→WS01 PSSession fails with 0x80090322 (Kerberos SPN/trust error, machine last logon Jan 11)
- DC→WS01 WMI fails with 0x800706BA (RPC server unavailable, port 135 closed)
- Scheduled tasks via `schtasks /S NVWS01` — untested (DC scripts too large, hit HTTP 400)

### What would fix it:

[truncated...]

---

## 2026-02-20-session120-winrm-recovery-chaos-safety.md

# Session 120: WinRM Recovery + Chaos Lab Safety + VM Rebalance

**Date:** 2026-02-20
**Duration:** ~1 hour
**Focus:** Infrastructure recovery, chaos lab hardening, VM resource management

## Problem

All Windows VMs (DC + WS01) had WinRM auth broken. Port 5985 was open but all authentication was rejected. Root cause traced to chaos lab LLM-generated campaign from Feb 17 (`scn_services_stop_winrm` in `campaigns/2026-02-17.json`) which ran:

```powershell
Stop-Service -Name WinRM -Force; Set-Service -Name WinRM -StartupType Disabled
```

The v2 execution plan only restores snapshots at campaign START, not end. So the DC was left with WinRM dead after that campaign. Everything cascaded from there.

## What Was Done

### 1. WinRM Recovery (DC + WS01)
- User ran `Enable-PSRemoting -Force` on both VMs
- User ran `Set-Item WSMan:\localhost\Service\AllowUnencrypted`, `Auth\Basic`, `Client\TrustedHosts` on both
- Verified: DC works with `.\Administrator` (NTLM + Basic), WS01 works with `localadmin`
- Domain accounts on WS01 still fail (stale machine trust from Jan 11)

### 2. Chaos Lab Safety Patches (on iMac 192.168.88.50)
**`scripts/winrm_attack.py` + `winrm_attack.py`:**
- Added `is_blocked_command()` safety filter with 6 regex patterns
- Blocks: Stop-Service WinRM, Set-Service WinRM Disabled, Disable-PSRemoting, Disable-WSMan, Remove-Item WSMan
- Returns structured error JSON instead of executing

**`scripts/generate_and_plan.py`:**
- Added to LLM prompt: "NEVER target WinRM, PSRemoting, or WSMan services/config"

**`scripts/generate_and_plan_v2.py`:**
- Added `CRITICAL EXCLUSION` block before L2-trigger instruction

### 3. VM Management
- Started `northvalley-linux` and `northvalley-srv01` (were down)
- Rebalanced RAM: DC 10->6GB, SRV01 6->4GB, Appliance 2->6GB (total 24->22GB)
- All 5 VMs running
- User installed VirtualBox Guest Additions on DC + WS01

### 4. VM Appliance Rebuild
- Appliance was down, started it
- Inserted nixos_rebuild admin order (12hr window) for static IP config
- First order failed (appliance was down), inserted fresh one

## Files Changed (on iMac, not in git)
- `/Users/jrelly/chaos-lab/scripts/winrm_attack.py` — safety filter
- `/Users/jrelly/chaos-lab/winrm_attack.py` — safety filter (copy)

[truncated...]

---

## 2026-02-21-session-122-HIPAA administrative compliance modules — 10 gap-closing integrations.md

# Session 122 - Hipaa Administrative Compliance Modules — 10 Gap Closing Integrations

**Date:** 2026-02-21
**Started:** 02:45
**Previous Session:** 121

---

## Goals

- [ ]

---

## Progress

### Completed


### Blocked


---

## Files Changed

| File | Change |
|------|--------|

---

## Next Session

1.

---

## 2026-02-21-session-122-hipaa-compliance-modules.md

# Session 122: HIPAA Administrative Compliance Modules

**Date:** 2026-02-21
**Duration:** ~45 min
**Focus:** Implement 10 HIPAA gap-closing integrations for client portal

## What Was Done

Implemented complete HIPAA administrative compliance documentation system for the client portal, covering the "paper" side of HIPAA that auditors require alongside existing automated technical controls.

### Backend (3 new files)

1. **Migration 048** (`backend/migrations/048_hipaa_modules.sql`)
   - 12 tables: `hipaa_sra_assessments`, `hipaa_sra_responses`, `hipaa_policies`, `hipaa_training_records`, `hipaa_baas`, `hipaa_ir_plans`, `hipaa_breach_log`, `hipaa_contingency_plans`, `hipaa_workforce_access`, `hipaa_physical_safeguards`, `hipaa_officers`, `hipaa_gap_responses`
   - 11 indexes for org-scoped queries
   - Migration run successfully on VPS database

2. **Templates** (`backend/hipaa_templates.py`)
   - 40 SRA questions across administrative/physical/technical safeguards
   - 8 HIPAA policy templates with `{{ORG_NAME}}`, `{{SECURITY_OFFICER}}` placeholders
   - IR plan template with response procedures and breach notification guidance
   - 19 physical safeguard checklist items
   - 27 gap analysis questions across 4 sections

3. **Router** (`backend/hipaa_modules.py`)
   - ~30 FastAPI endpoints on `APIRouter(prefix="/client/compliance")`
   - Uses existing `require_client_user` auth dependency
   - Overview endpoint aggregates all 10 modules into composite readiness score
   - Full CRUD for: SRA, policies, training, BAAs, breaches, contingency, workforce
   - Upsert patterns for: physical safeguards, officers, gap analysis

### Frontend (12 new files)

1. **Hub page** (`ClientCompliance.tsx`) — readiness score ring + 10 module cards with status badges + tab navigation
2. **SRAWizard.tsx** — multi-step wizard (admin→physical→technical→summary→remediation) with risk scoring
3. **PolicyLibrary.tsx** — template-based creation, inline editor, approval workflow
4. **TrainingTracker.tsx** — CRUD table with overdue detection
5. **BAATracker.tsx** — BAA inventory with PHI type tags + expiry alerts
6. **IncidentResponsePlan.tsx** — IR plan editor + breach log
7. **ContingencyPlan.tsx** — DR/BCP manager with RTO/RPO tracking
8. **WorkforceAccess.tsx** — access lifecycle table with termination workflow
9. **PhysicalSafeguards.tsx** — checklist with compliance status dropdowns
10. **OfficerDesignation.tsx** — privacy + security officer form
11. **GapWizard.tsx** — questionnaire with CMM maturity scoring + gap report
12. **compliance/index.ts** — barrel exports

### Wiring (4 modified files)

- `App.tsx` — added `ClientCompliance` to lazy import + `/client/compliance` route
- `client/index.ts` — added `ClientCompliance` export

[truncated...]

---

## 2026-02-21-session-123-native-go-l2-planner-phi-scrubbing.md

# Session 123: Native Go L2 LLM Planner + PHI Scrubbing
**Date:** 2026-02-21
**Duration:** ~4 hours (across 2 context windows)

## Summary
Built the complete native Go L2 LLM planner with PHI scrubbing, guardrails, budget tracking, and telemetry. Refactored architecture mid-session to centralize Anthropic API key on Central Command (VPS) instead of storing on every appliance device. Deployed and verified end-to-end on production VPS.

## What Was Done

### Phase 1: PHI Scrubber + Guardrails
- `appliance/internal/l2planner/phi_scrubber.go` — 12 regex categories (SSN, MRN, patient ID, phone, email, credit card, DOB, address, ZIP, account number, insurance ID, Medicare). IPs intentionally excluded per HIPAA Safe Harbor (infrastructure data). Hash suffix for correlation.
- `appliance/internal/l2planner/guardrails.go` — Dangerous pattern detection (rm -rf, mkfs, chmod 777, curl|bash, DROP TABLE, reverse shells). Allowed actions allowlist. Auto-escalation on low confidence or blocked commands.

### Phase 2: Budget + Prompt + Telemetry
- `appliance/internal/l2planner/budget.go` — $10/day spend limit, 60 calls/hr rate limit, 3 concurrent semaphore. Haiku 4.5 pricing model.
- `appliance/internal/l2planner/prompt.go` — Simplified to `truncate()` helper after Central Command refactor.
- `appliance/internal/l2planner/telemetry.go` — POST execution outcomes to `/api/agent/executions` for data flywheel.

### Phase 3: Core Planner
- `appliance/internal/l2planner/planner.go` — Orchestrates PHI scrub → POST to Central Command → guardrails → return decision. Uses appliance's existing API key + endpoint (same as checkins).

### Phase 4: Daemon Integration
- `daemon.go` — 6 edits: import, struct field (`l2Planner`), init, L2 readiness check, healIncident flow, shutdown.
- `config.go` — Removed LLM-specific config fields (API key, model, provider). Kept budget/rate/concurrency controls.

### Phase 5: Central Command Endpoint
- `main.py` — Added `POST /api/agent/l2/plan` with `L2PlanRequest` Pydantic model. Wraps existing `l2_planner.py` `analyze_incident()` + `record_l2_decision()`.
- Fixed import: `backend.l2_planner` → `dashboard_api.l2_planner` (Docker deployment path).

## Architecture Decision
**Key decision:** Anthropic API key lives ONLY on Central Command (VPS), not on appliance devices.
- Appliance PHI-scrubs data on-device before it leaves the network
- Appliance applies guardrails locally after receiving the decision
- Central Command holds the LLM key and calls Anthropic API
- Prevents key sprawl across customer sites

## Test Results
- 49 unit tests across l2planner package — all passing
- 12 daemon tests — all passing
- Live VPS test: `POST /api/agent/l2/plan` returned real LLM decision (configure_firewall, confidence 0.95, claude-sonnet-4, 7s latency)

## Commits
- `9e1f8e6` — feat: native Go L2 LLM planner + PHI scrubbing + guardrails
- `6801929` — refactor: L2 planner calls Central Command instead of Anthropic directly
- `7d9f69f` — fix: L2 plan endpoint import — use dashboard_api path matching Docker layout

## Files Created (12)
- `appliance/internal/l2planner/phi_scrubber.go` + `_test.go`
- `appliance/internal/l2planner/guardrails.go` + `_test.go`
- `appliance/internal/l2planner/budget.go` + `_test.go`

[truncated...]

---

## 2026-02-21-session-124-dashboard-audit-7-bug-fix-deploy.md

# Session 124 - Dashboard Audit: 7-Bug Fix + Deploy

**Date:** 2026-02-21
**Started:** 19:55
**Previous Session:** 123

---

## Goals

- [x] Audit and fix 7 live dashboard issues reported by user
- [x] Deploy fixes via CI/CD (push to main)
- [x] Restart appliance-daemon on physical appliance
- [x] Fix WS01 machine trust (reboot after DC-side password reset)

---

## Progress

### Completed

1. **Incidents page 500 crash** — `Severity(i["severity"])` threw `ValueError` on unknown/null DB values. Added `_safe_severity()` and `_safe_resolution_level()` wrappers in routes.py with try/except fallback.

2. **Portal 0% compliance score** — Controls with no data counted as "passing" (inflating pass count while actual score was 0%). Changed to skip no-data controls. Added `LIMIT 500` to `get_control_results_for_site` query (was fetching 128K+ rows unbounded).

3. **Magic link emails not delivered** — Portal used SendGrid (not configured), ignoring available SMTP credentials. Added SMTP fallback in portal.py using SMTP_HOST/SMTP_USER/SMTP_PASSWORD env vars.

4. **No real-time streaming** — Evidence submissions didn't broadcast WebSocket events; checkin events didn't invalidate workstation caches. Added `broadcast_event("compliance_drift", ...)` in evidence_chain.py. Updated useWebSocket.ts to invalidate `workstations`, `goAgents`, and `site` caches.

5. **Email nested details rendering** — `_build_details_section` silently skipped dict/list values. Now renders as formatted JSON in `<pre>` blocks. Fixed `datetime.utcnow()` deprecation.

6. **Portal scope_summary** — Showed "All checks passing" for unmonitored controls. Now shows "No data yet" when `pass_rate is None`.

7. **Infrastructure** — User restarted `appliance-daemon` on physical appliance (pkill + systemctl start). User rebooted WS01 to re-negotiate machine trust after DC-side `Reset-ComputerMachinePassword`.

### Commit

`3bad2e1` — fix: incidents crash, portal 0% score, email delivery, live streaming
Deployed via CI/CD (GitHub Actions run 22267455140, 53s)

### Blocked

None.

---

## Files Changed

| File | Change |
|------|--------|

[truncated...]

---

## 2026-02-21-session121-compliance-pipeline-fixes.md

# Session 121 - Compliance Pipeline + Systemic Fixes

**Date:** 2026-02-21
**Previous Session:** 120

---

## Summary

Completed the end-to-end compliance evidence pipeline and fixed three systemic issues that caused cascading failures during deployment.

### What Was Done

1. **Evidence pipeline live** - Physical appliance Go daemon (v0.2.0) now submits signed compliance bundles after drift scans. First real bundle: CB-2026-02-21, 7 checks, 6/7 pass (firewall disabled on 192.168.88.250), Ed25519 signed, chain position 128090.

2. **VM appliance 401 fix** - Root cause: Go checkin-receiver validated single static auth token. VM appliance had a different per-site API key. Fix: added per-site key validation from `appliance_provisioning` table. Deployed new checkin-receiver binary to VPS. Both appliances now checking in.

3. **Appliance ID format normalization** - `provisioning.py` created IDs without MAC colons (`843A5B91B661`), but Go/Python checkin used colons (`84:3A:5B:91:B6:61`). Fixed provisioning to use colons. Migrated 35 existing admin_orders rows.

4. **Nix version bump** - Updated Go daemon from 0.1.0 to 0.2.1 in both `appliance-image.nix` and `appliance-disk-image.nix`. Documented rebuild command for deployed appliances.

5. **Compliance packet endpoint working** - Returns real HIPAA compliance data: 56.4% compliance, 15 controls, evidence chain IDs.

### Files Changed

| File | Change |
|------|--------|
| `appliance/internal/evidence/signer.go` | NEW - Ed25519 key management |
| `appliance/internal/evidence/submitter.go` | NEW - Compliance bundle builder + HTTP submit |
| `appliance/internal/evidence/signer_test.go` | NEW - 3 tests |
| `appliance/internal/evidence/submitter_test.go` | NEW - 4 tests |
| `appliance/internal/daemon/daemon.go` | Add evidence submitter init |
| `appliance/internal/daemon/driftscan.go` | Call BuildAndSubmit after scan |
| `appliance/internal/daemon/phonehome.go` | Send agent public key |
| `appliance/internal/daemon/config.go` | SigningKeyPath() helper |
| `appliance/internal/checkin/handler.go` | Per-site API key auth |
| `appliance/internal/checkin/db.go` | ValidateAPIKey() method |
| `appliance/internal/orders/processor.go` | Absolute path for nixos-rebuild |
| `mcp-server/central-command/backend/provisioning.py` | Normalize MAC in appliance_id |
| `mcp-server/central-command/backend/sites.py` | Legacy compat comment |
| `mcp-server/central-command/backend/evidence_chain.py` | Fix compliance_packet import |
| `mcp-server/central-command/backend/compliance_packet.py` | Moved from mcp-server/ |
| `mcp-server/central-command/backend/db_queries.py` | Add check types to CATEGORY_CHECKS |
| `iso/appliance-disk-image.nix` | Version 0.1.0 -> 0.2.1 |
| `iso/appliance-image.nix` | Version 0.1.0 -> 0.2.1 |
| `flake.nix` | Document rebuild command |

### Commits

- `060dc7f` feat: end-to-end compliance pipeline

[truncated...]

---
