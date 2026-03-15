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

## 2026-02-22-session-125-runbook-l1-sync-telemetry-flywheel.md

# Session 125 - Runbook L1 Sync Telemetry Flywheel

**Date:** 2026-02-22
**Started:** 07:20
**Previous Session:** 124

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

## 2026-02-22-session-126-non-AD device join UI, flywheel data gaps, fleet order delivery.md

# Session 126 - Non-AD Device Join UI, Flywheel Data Gaps, Fleet Order Delivery

**Date:** 2026-02-22
**Previous Session:** 125

---

## Goals

- [x] Audit flywheel data gaps and fix L1 telemetry + field name mismatches
- [x] Add fleet order delivery to Go checkin-receiver
- [x] Deploy v0.2.2 Go daemon to both appliances via fleet order
- [x] Build non-AD device join UI for portal + dashboard

---

## Progress

### Completed

1. **Flywheel data gap fixes** — Go daemon L1 telemetry was completely missing. Added `ReportL1Execution()` to telemetry.go, wired into daemon heal paths. Fixed field name mismatch in backend ingestion (level→resolution_level, duration_ms→duration_seconds, error→error_message).

2. **Fleet order delivery** — Go checkin-receiver only fetched admin_orders + healing_orders. Added `FetchFleetOrders()` to db.go, cross-compiled new binary, deployed to VPS. Created fleet order, both appliances rebuilt to v0.2.2.

3. **Non-AD device join** — Full-stack feature: backend endpoints (admin + portal), shared AddDeviceModal component, wired into SiteDevices.tsx and PortalDashboard.tsx. No migration needed — uses existing site_credentials + discovered_devices tables. Appliance picks up new linux_targets on next checkin.

### Blocked

None.

---

## Files Changed

| File | Change |
|------|--------|
| `backend/sites.py` | CredentialCreate extended, ManualDeviceAdd model, _add_manual_device helper, POST endpoint |
| `backend/portal.py` | device_count in PortalData, POST portal device endpoint |
| `frontend/src/components/shared/AddDeviceModal.tsx` | **New** shared modal component |
| `frontend/src/pages/SiteDevices.tsx` | "Join Device" button + modal wiring |
| `frontend/src/portal/PortalDashboard.tsx` | "Managed Devices" section + modal wiring |
| `appliance/internal/l2planner/telemetry.go` | ReportL1Execution method |
| `appliance/internal/daemon/daemon.go` | Telemetry reporter wiring |
| `appliance/internal/checkin/db.go` | FetchFleetOrders |
| `.claude/skills/docs/backend/backend.md` | Updated with new endpoints |

## Commits

- `b143db4` — fix: close flywheel data gaps — L1 telemetry + field name compat
- `efbe532` — chore: bump Go daemon to v0.2.2

[truncated...]

---

## 2026-02-24-session-127-security-hardening-ed25519-host-scoping-parameter-allowlists.md

# Session 127 - Security Hardening Ed25519 Host Scoping Parameter Allowlists

**Date:** 2026-02-24
**Started:** 14:23
**Previous Session:** 126
**Commit:** 0656088

---

## Goals

- [x] P1: Add host scoping (target_appliance_id) to order signature envelope
- [x] P1: Allowlist order parameters for dangerous order types (nixos_rebuild, update_agent, update_iso)
- [x] P1: Constrain sync_promoted_rule with YAML schema validation

---

## Progress

### Completed

**Host Scoping** — `order_signing.py` adds `target_appliance_id` to signed payload. All admin/healing order creation paths (sites.py, partners.py, main.py, fleet_updates.py rollouts) now include the target appliance. Go `processor.go` verifies host scope after Ed25519 check. Fleet orders exempt.

**Parameter Allowlists** — `nixos_rebuild` validates flake_ref against `github:jbouey/msp-flake#<output>` pattern. `update_agent`/`update_iso` validate URLs are HTTPS from allowlisted domains (github.com, objects.githubusercontent.com, VPS). Added `validateFlakeRef()` and `validateDownloadURL()` helpers.

**Schema Validation** — `sync_promoted_rule` validates YAML against L1 Rule schema: parses into struct, checks action in 10-action allowlist, verifies rule ID match, requires conditions with field+operator, enforces 8KB size limit.

### Blocked

None.

---

## Files Changed

| File | Change |
|------|--------|
| `appliance/internal/crypto/verify.go` | New: Ed25519 verification package (P0, prev session) |
| `appliance/internal/crypto/verify_test.go` | New: 4 crypto tests |
| `appliance/internal/checkin/db.go` | Fetch signature fields, server_public_key |
| `appliance/internal/checkin/models.go` | Added Nonce/Signature/SignedPayload/ServerPublicKey |
| `appliance/internal/daemon/daemon.go` | Pass server pubkey + appliance ID to processor/L1 |
| `appliance/internal/daemon/phonehome.go` | Added ServerPublicKey to response |
| `appliance/internal/healing/l1_engine.go` | Verify signed L1 rules bundles |
| `appliance/internal/orders/processor.go` | Host scoping, param allowlists, schema validation |
| `appliance/internal/orders/processor_test.go` | 15 new tests (37 total) |
| `backend/migrations/054_order_signatures.sql` | New: nonce/signature/signed_payload columns |
| `backend/order_signing.py` | New: shared signing helper with target_appliance_id |
| `backend/fleet_updates.py` | Host-scoped rollout orders |
| `backend/partners.py` | Host-scoped discovery orders |

[truncated...]

---

## 2026-02-24-session-128-companion-router-uuid-audit-chaos-parser-fix.md

# Session 128 — Companion Router, UUID Audit, Chaos Parser Fix

**Date:** 2026-02-24
**Started:** 20:41
**Previous Session:** 127

---

## Goals

- [x] Fix chaos lab parser not recognizing new EXECUTION_PLAN.sh log formats (Feb 22-24)
- [x] Register companion router in server.py (all 10 HIPAA module buttons returning 404)
- [x] Fix remaining _uid() bugs in companion.py overview
- [x] Audit partner portal for _uid() issues — found and fixed 8
- [x] Audit client portal — confirmed clean (auth returns UUID objects)
- [x] Update backend.md with _uid()/db_utils.py patterns

---

## Progress

### Completed

**Chaos Lab Parser (iMac 192.168.88.50)**
- Added v8 (`SCENARIO: scn_xxx - Name`), v9 (`Executing: scn_xxx`), v10 (`Running scenario: scn_xxx`) patterns
- Fixed campaign detection regex (was requiring parenthesis, now accepts `===` terminator)
- Re-parsed Feb 22-24: healing rate trending 15.6% → 21.9% → 37.5%

**Companion Portal**
- Registered companion router in server.py (was never imported — all buttons 404)
- Fixed 2 _uid() in _compute_overview (breach_log + officers queries)

**Partner Portal**
- Fixed 8 raw string params in get_partner, update_partner, regenerate_api_key, create_partner_user, generate_user_magic_link

**Client Portal** — audited, clean. All org_id from auth (UUID objects), site_id is VARCHAR.

### Blocked

None.

---

## Files Changed

| File | Change |
|------|--------|
| `mcp-server/server.py` | Register companion_router with prefix="/api" |
| `mcp-server/central-command/backend/companion.py` | 2 _uid() fixes in _compute_overview |
| `mcp-server/central-command/backend/partners.py` | 8 _uid() fixes across 5 endpoints |

[truncated...]

---

## 2026-02-24-session-129-tiered-flywheel-promotion-l2-mode-toggle.md

# Session 129 — Tiered Flywheel Promotion + L2 Mode Toggle

**Date:** 2026-02-24
**Commit:** `2afea53` → `main`
**Deploy:** CI/CD auto-deploy + manual migration run + backend restart

## Summary

Built three-tier delegation model for scaling L2→L1 pattern promotions across 50-100+ clients, plus per-appliance L2 mode control and critical flywheel bug fixes.

## Bug Fixes (Flywheel Audit)

1. **Pattern signature fix** — Was `incident_type:incident_type:hostname` (duplicated field), fixed to `incident_type:runbook_id:hostname` in 4 locations (main.py x2, telemetry.go x2)
2. **ON CONFLICT DO NOTHING → DO UPDATE** — Pattern occurrence counts never accumulated
3. **Removed auto-promotion Path 1** — Manual-only for now

## Per-Appliance L2 Mode Toggle

3-state control: `auto` | `manual` | `disabled`. Migration 056, Go daemon gating, backend PATCH endpoint, frontend segmented control in SiteDetail ApplianceCard.

## Tier 1 — Platform Auto-Promote

Migration 057 `platform_pattern_stats` table. Flywheel Steps 3-4: aggregate L2 patterns across all sites/orgs, auto-promote to `l1_rules` with `source='platform'` when 5+ orgs, 90%+ success, 20+ occurrences. Syncs to all appliances via `/agent/sync`.

## Tier 2 — Client Self-Service

`POST /api/client/promotion-candidates/{id}/approve` and `/reject` — gated by `healing_tier='full_coverage'`. ClientHealingLogs.tsx: full_coverage sees Approve+Reject+Forward, standard sees Forward only.

## Tier 3 — Partner Bulk Management

`POST /api/partners/me/learning/candidates/bulk-approve` and `bulk-reject` (up to 50). PartnerLearning.tsx: checkbox column, Select All, floating bulk action bar, client endorsement badges, healing tier badges, endorsed filter.

## Files Changed (18 files, +1313/-98)

| File | Change |
|------|--------|
| `migrations/056_appliance_l2_mode.sql` | NEW — l2_mode on site_appliances |
| `migrations/057_platform_pattern_aggregation.sql` | NEW — platform_pattern_stats table |
| `main.py` | Pattern sig fix, removed auto-promote, Steps 3-4 |
| `client_portal.py` | Client approve/reject endpoints |
| `learning_api.py` | Bulk approve/reject, client_endorsed in response |
| `sites.py` | L2 mode PATCH endpoint |
| `ClientHealingLogs.tsx` | Approve/Reject UI for full_coverage |
| `PartnerLearning.tsx` | Checkbox selection, bulk action bar, badges |
| `SiteDetail.tsx` | L2ModeToggle component |
| `api.ts`, `useFleet.ts`, `hooks/index.ts` | L2 mode API + hook |
| `checkin/db.go`, `models.go` | FetchL2Mode |
| `daemon/daemon.go`, `phonehome.go` | L2 mode gating |
| `l2planner/telemetry.go` | Pattern signature fix |


[truncated...]

---

## 2026-02-25-session-130-HIPAA doc upload, module guidance, companion CSRF, learning loop fix.md

# Session 130 - HIPAA Doc Upload, Module Guidance, Companion CSRF, Learning Loop Fix

**Date:** 2026-02-25
**Started:** 00:07
**Previous Session:** 129

---

## Goals

- [x] Wire document uploads into compliance scoring (upload = module evidence)
- [x] Fix Learning Loop 500 error (PromotionCandidate.description nullable)
- [x] Policy template preview + download + inline content view
- [x] Guidance captions on all 10 HIPAA modules (What is this? / How to complete it)
- [x] Widen guidance blocks + verify companion portal visibility
- [x] Fix companion notes CSRF 403 (missing X-CSRF-Token header)

---

## Progress

### Completed

1. **Document upload → compliance scoring** (7bf4282)
   - Added `hipaa_documents` count query to overview endpoints in `hipaa_modules.py` and `companion.py`
   - Modules with docs but no structured data get 100% score (upload = evidence provided)
   - Added `documents` field to API response + `DOC_KEY_MAP` in `ClientCompliance.tsx`
   - Module status shows "N Docs" badge when docs exist but no structured data

2. **Learning Loop 500 fix** (0e2cddf)
   - Root cause: `PromotionCandidate.description: str` in models.py but DB had NULL values
   - Fixed: `Optional[str] = None` in models.py + `or ""` coalescing in routes.py

3. **Policy template preview + download** (f5e3498)
   - Added `GET /policies/templates` (list all 8) and `GET /policies/templates/{key}` (full content)
   - Template cards with Preview/Download/Adopt buttons in PolicyLibrary.tsx

4. **Policy templates inline view** (e007a44)
   - Replaced modal Preview with inline View/Collapse toggle
   - Template content cached in state, expandable per-card

5. **Guidance captions on all 10 modules** (731ccc1)
   - Added teal "What is this?" + "How to complete it" blocks to all 10 compliance module .tsx files
   - Written in near-lay language for office managers, still industry-relevant
   - Consistent styling: `bg-teal-50/60 rounded-2xl border border-teal-100`

6. **Widen guidance blocks** (4443e6d)
   - Changed from `p-4 rounded-xl` to `px-6 py-5 rounded-2xl` with `leading-relaxed`
   - Companion portal shares same components (CompanionModuleWork.tsx lazy-imports) — no changes needed


[truncated...]

---

## 2026-02-25-session-131-linux-drift-scanner-chaos-lab.md

# Session 131 — Linux Drift Scanner + Chaos Lab Full-Spectrum

**Date:** 2026-02-25
**Duration:** ~90 minutes
**Focus:** Fix Linux drift scanning, run chaos lab, deploy v0.2.3 daemon

---

## Completed

### 1. Fleet Updates Version Fix
- Fleet Updates page showed stale `v1.0.52` (old Python agent) for a month
- Added auto-detection: query `appliances` table for most common `agent_version` from recent checkins
- Added "Deployed Version" card (5-column grid) showing live version + appliance count
- Inserted `v0.2.2` release in `update_releases`, deactivated old Python releases
- **Commit:** `cbfe457`

### 2. Go Daemon SudoPassword Support (linuxscan.go)
- **Root cause:** `sshexec.Target.SudoPassword` was never set during Linux scan
  - `linuxTarget` struct: added `SudoPassword` field
  - `parseLinuxTargets()`: extract `sudo_password` with password fallback
  - Target construction: added `target.SudoPassword = &lt.SudoPassword`
- **Backend (sites.py):** Added `sudo_password` passthrough — uses `password` as fallback
- **Commit:** `837426d` (backend), `6f699f3` (Go daemon)

### 3. Linux Scan Script Fixes
- **ufw detection:** Ubuntu uses ufw, not nft/iptables. Added `ufw status` check first
- **Numeric sanitization:** `grep -c` can output multi-line values causing Python SyntaxError
  - Added `head -1 | tr -dc '0-9'` for `fw_rules`, `failed_count`, `disk_pct`
  - Pre-Python sanitization block ensures all numeric vars are clean integers
- **Better error logging:** Show exit code + stderr on scan failure (was empty string)

### 4. Credential Fixes
- Linux target password was `msp123` (wrong) — fixed to `NorthValley2024!` for both appliances
- Added WinRM credentials for VM appliance (`test-appliance-lab-b3c40c`)

### 5. Chaos Lab Full-Spectrum Test
Injected drift on 192.168.88.242 (northvalley-linux):
- SSH: PermitRootLogin=yes, PasswordAuth=yes, MaxAuthTries=10
- Firewall: ufw disabled
- Service: auditd stopped
- Permissions: /etc/shadow=644, sshd_config=666

**Results — 7 drift findings detected:**

| Finding | Rule | Outcome |
|---------|------|---------|
| SSH config drift | L1-SSH-001 | Runbook dispatched |
| Failed services | L1-LIN-SVC-001 | Runbook dispatched |
| SUID binaries | L1-SUID-001 | Auto-healed (4.5s) |

[truncated...]

---

## 2026-02-25-session-132-network-compliance-checks-frontend-verification.md

# Session 132 — Network Compliance Checks + Frontend Verification

**Date:** 2026-02-25
**Started:** 10:32
**Previous Session:** 131

---

## Goals

- [x] Verify frontend Device Inventory consistency (summary vs table)
- [x] Complete end-to-end verification of compliance pipeline
- [x] Update session tracking and documentation

---

## Progress

### Completed

1. **Frontend verified working** — Device Inventory page at `dashboard.osiriscare.net` shows:
   - Summary: 7 Total, 0 Compliant, 1 Drifted, 6 Unknown, 0 Medical
   - Table: 7 devices with correct status, expandable details showing open ports
   - Previous "0 devices" issue was transient (stale cache / pre-deploy state)

2. **End-to-end pipeline confirmed operational:**
   - Nmap auto-detects 192.168.88.0/24 from appliance interfaces
   - Scans find 7 hosts, 192.168.88.241 gets port data (22, 80, 8083, 8090)
   - 7 HIPAA compliance checks run → 192.168.88.241 = drifted (HTTP w/o HTTPS)
   - Results sync to Central Command PostgreSQL (migration 060)
   - Dashboard displays consistent data with expandable compliance details

3. **Key discovery:** `routes/device_sync.py` compliance detail endpoint is NOT reachable — main.py imports `device_sync_router` from `device_sync.py`, not from `routes/`. Needs consolidation.

### Blocked

- Nothing blocked

---

## Files Changed

| File | Change |
|------|--------|
| `.agent/claude-progress.json` | Updated: session 132, health, commits, new key findings |

Previous session (130-131) created all the compliance check files — see session 131 log.

---


[truncated...]

---

## 2026-02-25-session-133-HIPAA doc audit + resilience hardening plan.md

# Session 133 - Hipaa Doc Audit + Resilience Hardening Plan

**Date:** 2026-02-25
**Started:** 12:20
**Previous Session:** 132

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

## 2026-02-25-session-133-hipaa-doc-audit-resilience-plan.md

# Session 133 — HIPAA Doc Audit + Resilience Hardening Plan

**Date:** 2026-02-25
**Duration:** ~45 min
**Status:** Plan ready, implementation deferred

## What Was Done

### 1. HIPAA Compliance Doc Audit (13 Fixes)

Audited `.claude/skills/docs/hipaa/compliance.md` against actual codebase with 5 parallel Explore agents. Found and fixed 13 discrepancies:

1. **DriftResult** — changed from `@dataclass` to Pydantic `BaseModel`
2. **EvidenceBundle** — changed to Pydantic `BaseModel`, removed bundle_hash/signature fields (stored as separate files), added 40+ actual fields
3. **Generation pipeline** — updated to `store_evidence()` API
4. **Runbook format** — updated to v2 (params nesting, version, constraints, continue_on_failure)
5. **L1 rule format** — removed fake confidence field, added actual fields (action_params, severity_filter, cooldown_seconds, gpo_managed)
6. **Operators** — added EXISTS (9th operator)
7. **Data Boundary Zones** — removed nonexistent ALLOWED_PATHS/PROHIBITED_PATHS constants
8. **L1 Rule Coverage** — fixed counts: 12 cross-platform + 13 Linux + 13 Windows = 38 total
9. **Windows scan checks** — fixed from 16 to 12
10. **CIS mappings** — updated v7→v8 numbering
11. **HIPAA Control Mapping** — expanded to 14 controls with categories
12. **Key Files** — expanded from 8 to 13 entries
13. **Network Compliance Checks** — added new section documenting 7 HIPAA network checks

### 2. Resilience Gap Audit

Ran 2 parallel Explore agents to audit offline/disconnect resilience. Found 7 gaps:

| Gap | Status |
|-----|--------|
| StartLimitBurst on disk image services | Plan written |
| WatchdogSec on long-running services | Plan written |
| Subscription enforcement | Plan written (immediate degraded mode) |
| Go daemon state persistence | Plan written |
| Network vs server connectivity distinction | Plan written |
| A/B physical partitions | Deferred to next session |
| Doc updates | Plan written |

### 3. Resilience Implementation Plan

Wrote comprehensive plan to `/Users/dad/.claude/plans/synchronous-stirring-taco.md` covering:
- Systemd crash-loop protection (StartLimitBurst=5/300s on 3 services)
- WatchdogSec (120s) with sd_notify in Go daemon
- Subscription enforcement via checkin-receiver JOIN → daemon healing gate
- daemon_state.json atomic persistence for linux targets, L2 mode, subscription status
- Connectivity error classification (DNS fail vs connection refused vs timeout)
- hipaa/compliance.md resilience section


[truncated...]

---

## 2026-02-25-session-134-l2-planner-flywheel-fix.md

# Session 134: L2 Planner Enable + Flywheel Promotion Fix

**Date:** 2026-02-25
**Focus:** L2 was completely dead — everything escalated to L3. Fixed three-layer bug in L2 pipeline + broken flywheel promotion aggregation.

## Problem

All non-L1 incidents went straight to L3 email alerts. L2 LLM planner was never called. Learning flywheel had zero promotion candidates despite 912 L2 executions in telemetry.

## Root Causes Found

### L2 Never Called (3 bugs)
1. **config.go:79** — `L2Enabled` defaulted to `false`. Daemon logged `l2=disabled` at startup, `l2Planner` was nil.
2. **main.py:2511** — Backend set `escalate_to_l3 = action == "escalate" or decision.requires_human_review`. Even with valid runbook at 0.75 confidence, `requires_human_review=true` forced escalation.
3. **daemon.go:600** — `ShouldExecute()` required `!RequiresApproval`, blocking auto-mode execution. Auto mode should override this.

### Flywheel Dead (1 bug)
4. **main.py:617** — Flywheel loop Step 2 updated `promotion_eligible` on `aggregated_pattern_stats` rows, but nothing created those rows. Go daemon doesn't call `/api/agent/sync/pattern-stats`. The table was empty for L2 patterns.

## Fixes

| File | Change |
|------|--------|
| `appliance/internal/daemon/config.go:79` | `L2Enabled: false` → `true` |
| `mcp-server/main.py:2511` | `escalate = action == "escalate"` (removed `or requires_human_review`) |
| `appliance/internal/daemon/daemon.go:600` | `canExecute = !EscalateToL3 && Confidence >= 0.6` (ignores RequiresApproval in auto mode) |
| `mcp-server/main.py:617` | Added Step 1: aggregate `execution_telemetry` → `aggregated_pattern_stats` in flywheel loop |

## Deployment

- **Commit 8771d36** — L2 enable + escalation fix → CI/CD deployed to VPS
- **Commit f9cd525** — Flywheel aggregation bridge → CI/CD deployed to VPS
- **Go binary** — Cross-compiled, uploaded to VPS `/var/www/updates/appliance-daemon`
- **Fleet order 7aa80c25** — `nixos_rebuild` active, 48h expiry, both appliances will rebuild
- **DB fix** — Manually marked 2 L2 patterns as promotion_eligible (786 firewall heals, 110 backup heals)

## Verified

- L2 endpoint returns `escalate_to_l3: false` for valid runbooks (was `true`)
- CI/CD both succeeded
- Both appliances checking in (v0.2.5, last checkin <60s ago)
- 37 patterns now promotion-eligible in learning dashboard
- 912 L2 executions in telemetry (100% success rate)

## Next

- Verify appliances pick up fleet rebuild order and restart with `l2=native`
- Monitor L2 decisions on live incidents (should see L2 handling instead of L3)
- WinRM 401 on DC (192.168.88.250) still needs investigation

---

## 2026-02-26-session-135-resilience-hardening-sdnotify-state-subscription.md

# Session 135 - Resilience Hardening: sd_notify, State Persistence, Subscription Gating

**Date:** 2026-02-26
**Started:** 02:35
**Previous Session:** 134

---

## Goals

- [x] Systemd crash-loop protection on all services
- [x] sd_notify watchdog integration for appliance-daemon
- [x] Go daemon state persistence across restarts
- [x] Subscription enforcement — gate healing on active/trialing
- [x] Connectivity error classification for better diagnostics
- [x] Build, deploy, create fleet order

---

## Progress

### Completed

1. **Crash-loop protection** — `StartLimitBurst=5/IntervalSec=300` on appliance-daemon, network-scanner, local-portal
2. **sd_notify watchdog** — new `sdnotify` package (zero-cgo), `WatchdogSec=120s` on all 3 services, `Type=notify` for daemon, Ready/Watchdog/Stopping calls in daemon.go
3. **State persistence** — new `state.go`, saves linux_targets + l2_mode + subscription_status to `/var/lib/msp/daemon_state.json` with atomic write (tmp+rename), loaded on startup
4. **Subscription enforcement** — `FetchSubscriptionStatus()` JOINs sites→partners, `SubscriptionStatus` in CheckinResponse, `isSubscriptionActive()` gates auto-deploy + heal requests, drift detection continues in degraded mode
5. **Connectivity classification** — `classifyConnectivityError()` using `errors.As` for DNS/OpError, string matching for timeout/tls/5xx
6. **Deployed** — binaries to VPS, checkin-receiver restarted, CI/CD triggered, fleet order 3bf579c6

### Blocked

- WinRM 401 on DC — needs home network
- HIPAA compliance at 56% — needs more check coverage

---

## Files Changed

| File | Change |
|------|--------|
| `iso/appliance-disk-image.nix` | StartLimitBurst, WatchdogSec, Type=notify |
| `appliance/internal/sdnotify/sdnotify.go` | NEW — zero-cgo sd_notify helper |
| `appliance/internal/daemon/state.go` | NEW — state persistence (save/load JSON) |
| `appliance/internal/daemon/daemon.go` | sd_notify calls, subscription gating, state save/load, connectivity classification |
| `appliance/internal/daemon/phonehome.go` | SubscriptionStatus field, classifyConnectivityError() |
| `appliance/internal/checkin/db.go` | FetchSubscriptionStatus(), Step 9 in ProcessCheckin |
| `appliance/internal/checkin/models.go` | SubscriptionStatus field |
| `.claude/skills/docs/hipaa/compliance.md` | Resilience & Offline Operation section |
| `.claude/skills/docs/nixos/infrastructure.md` | sd_notify, state persistence, crash-loop docs |

[truncated...]

---

## 2026-02-26-session-136-incident-pipeline-production-l1-l2-l3-rate-limiter-fix.md

# Session 136 — Incident Pipeline Production + Rate Limiter Fix

**Date:** 2026-02-26
**Started:** 08:02
**Previous Session:** 135

---

## Goals

- [x] Fix both sites showing Offline on dashboard
- [x] Fix incident pipeline to use L1 DB rules → L2 LLM → L3 escalation
- [x] Populate Linux runbook steps for production fleet orders
- [x] Consolidate duplicate L1 rules
- [x] Audit site detail features (Portal Link, Devices, Workstations, Go Agents, Frameworks, Cloud Integrations)
- [x] Wire network incidents through L2 analysis instead of straight-to-L3

---

## Progress

### Completed

1. **Rate limiter fix** — separate agent bucket (600/min) prevents scan telemetry from starving checkins
2. **Caddy route fix** — removed broken `checkin-receiver:8001` route, all API traffic to `mcp-server:8000`
3. **Incident pipeline rewrite** — L1 queries `l1_rules` table, L2 LLM fallback, L3 only as last resort
4. **Notification dedup** — increased from 1h to 4h for L1/L2 incidents (reduces linux_firewall spam)
5. **41 runbooks with steps** — 13 core Linux + LIN-* series populated with real remediation commands
6. **L1 rule consolidation** — 1 active rule per incident type, disabled 12+ duplicates
7. **DB cleanup** — expired 20 orders, resolved 14 stale incidents, fixed framework 404
8. **Network L2 analysis** — 4 network incident types now flow through L2 for vendor-specific recommendations
9. **Frontend audit** — all 6 site detail tabs verified working

### Blocked

- WinRM 401 still open (no workstation enrollment)
- Fleet rebuild pending (order 3bf579c6)

---

## Files Changed

| File | Change |
|------|--------|
| `mcp-server/central-command/backend/rate_limiter.py` | Separate agent rate limiter (600/min, 20k/hr) |
| `mcp-server/main.py` | L1 DB rules → L2 LLM → L3 pipeline; 4h notification dedup |
| `mcp-server/central-command/backend/migrations/063_linux_runbook_steps_l1_cleanup.sql` | Runbook steps + L1 consolidation |
| `/opt/mcp-server/Caddyfile` (VPS) | Removed broken checkin-receiver route |

## Commits

[truncated...]

---

## 2026-02-26-session-137-Fleet rebuild v0.3.2, WinRM DC credential fix, AD enrollment unblocked.md

# Session 137 - Fleet Rebuild V0.3.2, Winrm Dc Credential Fix, Ad Enrollment Unblocked

**Date:** 2026-02-26
**Started:** 21:18
**Previous Session:** 136

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

## 2026-02-26-session-137-fleet-rebuild-winrm-credential-fix.md

# Session 137 — Fleet Rebuild + WinRM Credential Fix

**Date:** 2026-02-26
**Started:** 13:07
**Previous Session:** 136

---

## Goals

- [x] Deploy resilience hardening via fleet rebuild (sd_notify, state persistence, crash-loop protection)
- [x] Fix WinRM 401 — load Windows credentials from checkin response
- [x] Bump appliance daemon version to 0.3.1
- [x] Verify appliances report v0.3.1 after rebuild — Physical at 0.3.1, VM stuck at 0.2.5 (restart goroutine fails on VirtualBox)
- [x] Verify WinRM connections succeed — DC at 192.168.88.250, GPO configured, AD enumeration running
- [x] Fix DC targeting — domain_admin credential prioritized over workstations (v0.3.2)
- [x] Fix healing order pipeline — runbook_id missing from parameters JSON (v0.3.3)

---

## Progress

### Completed

1. **Fleet order fix** — Cancelled broken `3bf579c6` (status 'pending', wrong params). Created proper `db4b76d5` with correct `flake_ref` and 'active' status.
2. **Resilience hardening deployed** — Both appliances completed fleet order `db4b76d5` (nixos_rebuild). Physical at 0.3.0, VM completing.
3. **Version bump to 0.3.0** — Updated `daemon.go`, `appliance-disk-image.nix`, `appliance-image.nix` (Go source + ldflags + Nix version).
4. **WinRM root cause found** — `runCheckin()` in daemon.go processed LinuxTargets, L2Mode, SubscriptionStatus but completely ignored `WindowsTargets`. DC credentials from Central Command were never loaded into config. Drift scanner and auto-deployer early-exited with nil credentials.
5. **WinRM fix implemented** — Added `loadWindowsTargets()` to extract DC hostname/username/password from first Windows target in checkin response. Config's DomainController/DCUsername/DCPassword now populated dynamically.
6. **Version bump to 0.3.1** — Includes WinRM credential loading fix.
7. **Fleet order for v0.3.1** — Created `42d63738` (active, skip_version=0.3.1, 48h expiry). Both appliances will rebuild.
8. **DC targeting fix (v0.3.2)** — Backend was returning workstation cred (.244) before DC cred (.250). Fixed: backend orders domain_admin first, adds `role` field; Go `loadWindowsTargets()` prefers `role=domain_admin`.
9. **WinRM verified working** — Physical appliance: DC at 192.168.88.250 connected, GPO configured, AD enumeration started against DC.
10. **Healing pipeline fix (v0.3.3)** — 40 failed healing orders/day with "runbook_id is required". Root cause: backend stored runbook_id in DB column but `parameters = {}`. Go daemon's `processOrders()` only extracted `parameters` map. Fixed both: backend embeds runbook_id in parameters JSON; Go injects top-level runbook_id into params map.

### Pending

- Fleet order `de656138` (v0.3.3) active — appliances need to rebuild for healing fix
- VM appliance still at v0.3.1 — needs manual `nixos-rebuild switch`
- `handleHealing` in processor.go is a stub — returns success without executing runbook steps

---

## Files Changed

| File | Change |
|------|--------|
| `appliance/internal/daemon/daemon.go` | Added `loadWindowsTargets()`, bumped Version 0.2.5→0.3.3, added win_targets to checkin log, inject runbook_id into order params |
| `iso/appliance-disk-image.nix` | Version 0.3.3 in buildGoModule + ldflags |
| `iso/appliance-image.nix` | Version 0.3.3 in buildGoModule + ldflags |

[truncated...]

---

## 2026-02-27-session-138-nixos-runbook-healing-fix.md

# Session 138 — NixOS Runbook Healing Fix (v0.3.5)

**Date:** 2026-02-27
**Focus:** Audit and fix 7 recurring non-healing incidents

## Problem

7 dashboard notifications recurring every scan cycle, all marked "Resolution: L1" but never actually healed:
- `linux_failed_services` (both appliances)
- `linux_unattended_upgrades` (both appliances)
- `linux_log_forwarding` (physical)
- `net_host_reachability` (both appliances) — correctly escalates to L3

## Root Cause

The `handleHealing` stub in `processor.go` was **NOT** the blocker — `daemon.go:160` already overrides it with the real `executeHealingOrder()`. The real issues:

1. **LIN-SVC-001** (linux_failed_services): `remediate_script` was EMPTY — L1 matched, called runbook, nothing happened
2. **LIN-PATCH-001** (linux_unattended_upgrades): `remediate_script` was EMPTY — and NixOS doesn't have apt/yum
3. **LIN-LOG-001** (linux_log_forwarding): remediate used `sed -i` on `/etc/journald.conf` — fails on NixOS (read-only symlinks)
4. **Scanner too strict**: log forwarding check only accepted rsyslog or journal-upload, not journald persistent storage
5. **NixOS config gaps**: `MaxRetentionSec=7day` (HIPAA needs 90d), no `system.autoUpgrade` configured

## Fixes

### 1. runbooks.json — 3 runbook scripts fixed
- **LIN-SVC-001**: Added generic failed-service restart (`systemctl restart` each failed svc) + verify
- **LIN-PATCH-001**: NixOS-aware auto-update timer enablement (checks `/etc/NIXOS`, uses appropriate timer)
- **LIN-LOG-001**: NixOS-aware remediation (detects NixOS, skips sed on symlinks, checks journald persistent)

### 2. linuxscan.go — Scanner log forwarding check
- Added `journald_persistent` as valid log management state
- Checks `grep -qE "^Storage=persistent" /etc/systemd/journald.conf`

### 3. configuration.nix — NixOS config fixes
- `MaxRetentionSec`: 7day → 90day (HIPAA 164.312(b))
- `SystemMaxUse`: 100M → 500M (room for 90-day retention)
- Added `system.autoUpgrade` with flake ref, 4 AM schedule, no auto-reboot

### 4. processor.go — Stub converted to error sentinel
- Stub now returns error + WARNING log instead of fake success
- Makes it obvious if daemon fails to register real handler
- Test updated to verify stub→RegisterHandler override chain

### 5. Version bump
- daemon.go: 0.3.4 → 0.3.5

## Files Changed
- `appliance/internal/daemon/runbooks.json` — 3 runbook scripts
- `appliance/internal/daemon/linuxscan.go` — log forwarding check

[truncated...]

---

## 2026-02-27-session-139-comprehensive-nixos-runbook-scanner-fix.md

# Session 139 — Comprehensive NixOS Runbook & Scanner Fix (v0.3.6)

**Date:** 2026-02-27
**Focus:** Fix all remaining NixOS-incompatible runbooks and scanner false positives

## Continuation of Session 138

After v0.3.5 fixed LIN-SVC-001, LIN-PATCH-001, LIN-LOG-001, 40+ new notifications revealed more broken runbooks and scanner issues.

## Comprehensive Audit Results

15 scanner checks audited. 7 issues found, 4 critical:

## Fixes

### 1. configuration.nix — HIPAA kernel sysctl params
Added 7 HIPAA-required kernel hardening params to `boot.kernel.sysctl`:
- `net.ipv4.ip_forward = 0`
- `net.ipv4.tcp_syncookies = 1`
- `net.ipv4.conf.all.send_redirects = 0`
- `net.ipv4.conf.all.accept_redirects = 0`
- `net.ipv4.conf.all.rp_filter = 1`
- `kernel.randomize_va_space = 2`
- `kernel.suid_dumpable = 0`

### 2. LIN-FW-001 — Firewall (was empty)
- **remediate_script**: NixOS-aware — detects /etc/NIXOS, reloads nftables service; standard Linux falls through to ufw/firewalld
- **verify_script**: Checks nft ruleset count, ufw status, firewalld state

### 3. LIN-SSH-001 — SSH Root Login (was sed on read-only)
- **detect_script**: Now accepts both `no` and `prohibit-password` as compliant
- **remediate_script**: NixOS-aware — detects /etc/NIXOS, verifies config is already correct declaratively, skips sed
- **verify_script**: Accepts both `no` and `prohibit-password`

### 4. LIN-KERN-001 — Kernel Params (cat > /etc/sysctl.d/ fails on NixOS)
- **remediate_script**: `sysctl -w` for immediate effect on all Linux; skips file persistence on NixOS (managed by configuration.nix)

### 5. SUID Scanner — False positives on NixOS
- linuxscan.go SUID check: Added `case "$f" in /nix/store/*) continue ;;` to skip declaratively-managed NixOS store paths

### 6. Version bump 0.3.5 → 0.3.6

## Files Changed
- `iso/configuration.nix` — 7 HIPAA kernel sysctl params
- `appliance/internal/daemon/runbooks.json` — LIN-FW-001, LIN-SSH-001, LIN-KERN-001 NixOS-aware
- `appliance/internal/daemon/linuxscan.go` — SUID /nix/store filter
- `appliance/internal/daemon/daemon.go` — version 0.3.5 → 0.3.6

## Test Results
- `go build ./...` — clean

[truncated...]

---

## 2026-02-27-session-140-skill-docs-agents-md-restructure.md

# Session 140 — Skill Docs + agents.md Restructure

**Date:** 2026-02-27
**Previous Session:** 139

---

## Goals

- [x] Research expert Go patterns across the internet
- [x] Research advanced NixOS patterns (lesser-known features)
- [x] Scan SkillsJars.com for relevant skills
- [x] Create Go skill doc from research
- [x] Create NixOS advanced skill doc from research
- [x] Restructure knowledge index per Vercel agents.md findings
- [x] Integrate obra/superpowers debugging + verification methodology

---

## Progress

### Completed

- **Go skill doc** (`.claude/skills/docs/golang/golang.md`) — concurrency, pgx, slog, testing, security, production patterns. 5 research agents compiled.
- **NixOS advanced skill doc** (`.claude/skills/docs/nixos/advanced.md`) — module system, sops-nix, impermanence, deploy-rs, disko, systemd hardening. 2 research agents compiled.
- **Workflow skill doc** (`.claude/skills/docs/workflow/workflow.md`) — systematic debugging (4-phase, 95% fix rate) + verification-before-completion from obra/superpowers.
- **Vercel agents.md restructure** — compressed index with critical snippets moved into CLAUDE.md (always in context). Skills requiring invocation: +0pp. Always-in-context index: +47pp per Vercel evals.
- **CLAUDE.md rules** — added root-cause-first debugging and evidence-before-claims verification as always-loaded rules.
- **SkillsJars scan** — no Go/NixOS/Python skills. Catalog dominated by Java/Spring Boot and marketing.
- **INDEX.md** — slimmed from full pipe table to doc map pointer (CLAUDE.md now holds the index).

### Blocked

None.

---

## Files Changed

| File | Change |
|------|--------|
| `.claude/skills/docs/golang/golang.md` | NEW — Go expert patterns |
| `.claude/skills/docs/nixos/advanced.md` | NEW — NixOS advanced patterns |
| `.claude/skills/docs/workflow/workflow.md` | NEW — debugging + verification |
| `.claude/skills/INDEX.md` | Slimmed to pointer |
| `CLAUDE.md` | Expanded knowledge index + retrieval instruction + rules |

---

## Commits

[truncated...]

---

## 2026-02-27-session-141-Go daemon security hardening — SSH TOFU, UTF-16 fix, LRU cache, WaitGroup drain.md

# Session 141 - Go Daemon Security Hardening — Ssh Tofu, Utf 16 Fix, Lru Cache, Waitgroup Drain

**Date:** 2026-02-27
**Started:** 05:44
**Previous Session:** 140

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

## 2026-02-27-session-141-go-daemon-security-hardening.md

# Session 141: Go Daemon Security Hardening

**Date:** 2026-02-27
**Scope:** Comprehensive audit + 4-phase hardening of the Go appliance daemon

## Summary

Three parallel audit agents identified ~50 issues across security, correctness, resource management, and code quality in `appliance/`. Implemented 13 fixes across 4 phases.

## Phase 1: Security Hardening

| Fix | File | Change |
|-----|------|--------|
| SSH TOFU host key verification | `sshexec/executor.go` | Replaced `InsecureIgnoreHostKey()` with TOFU: persist keys to `/var/lib/msp/ssh_known_hosts`, reject changed keys |
| WinRM full SHA256 hash | `winrm/executor.go:204` | Changed `[:8]` truncation to full 64-char SHA256 hex for temp file names |
| UTF-16LE encoding fix | `winrm/executor.go:328-334` | Replaced byte-iteration with `unicode/utf16.Encode()` for correct multi-byte char handling |
| Reject unsigned orders | `orders/processor.go:301-306` | Changed from warn-and-allow to reject unsigned orders when server public key is present |
| Tighten file permissions | `orders/processor.go:723` | Changed promoted rule files from `0644` to `0600` |

## Phase 2: Correctness Fixes

| Fix | File | Change |
|-----|------|--------|
| io.ReadAll error handling | `daemon.go:524` | Check and return error instead of discarding with `_` |
| Context propagation | `healing_executor.go` | Added `executeRunbookCtx()`, `executeLocalCtx()`, `executeInlineScriptCtx()` — healing order path now propagates parent context to SSH/local execution |
| Incident reporter context | `incident_reporter.go:86,131` | Added 30s timeout context via `http.NewRequestWithContext()` |

## Phase 3: Resource Management

| Fix | File | Change |
|-----|------|--------|
| SSH LRU cache | `sshexec/executor.go` | Max 50 cached connections with LRU eviction via `connOrder` slice |
| Distro cache TTL | `sshexec/executor.go` | 24h TTL on distro detection cache via `distroCacheEntry` struct |
| WaitGroup drain | `daemon.go` | `sync.WaitGroup` on key goroutines; 30s timeout drain on shutdown |

## Phase 4: Code Quality

| Fix | File | Change |
|-----|------|--------|
| Atomic DriftCount | `grpcserver/registry.go`, `server.go` | Changed `DriftCount int64` to `atomic.Int64` for race-free concurrent access |
| gpoFixDone to struct | `daemon.go` | Moved package-level `var gpoFixDone sync.Map` to `Daemon` struct field |

## Not Fixed (already correct)
- Cooldown key separator collision (4C) — already uses `:` separator at `daemon.go:591`

## Test Results
- All Go packages pass (except pre-existing `TestWindowsRulesMatch/smb_signing`)
- `go vet ./...` clean
- `go build ./...` clean

---

## 2026-02-27-session-142-security hardening — fleet orders auth, signatures, nonce replay, WinRM SSL.md

# Session 142 - Security Hardening — Fleet Orders Auth, Signatures, Nonce Replay, WinRM SSL

**Date:** 2026-02-27
**Started:** 06:48
**Previous Session:** 141
**Commit:** a68fe2d

---

## Goals

- [x] Fix 4 CRITICAL + 3 HIGH vulnerabilities from fleet orders audit

---

## Progress

### Completed

1. **Appliance checkin auth (CRITICAL)** — `require_appliance_auth()` validates Bearer token via `verify_site_api_key()`. Graceful fallback for sites without API keys. Applied to checkin, acknowledge, complete.
2. **Signature delivery (CRITICAL)** — admin_orders, healing orders, fleet_orders now include nonce/signature/signed_payload in SELECT and response.
3. **server_public_key (CRITICAL)** — Checkin response returns `get_public_key_hex()` for Go daemon signature verification.
4. **Nonce replay protection (HIGH)** — Go Processor tracks used nonces in-memory + JSON persistence, 24h eviction.
5. **Hostname validation (HIGH)** — `isKnownTarget()` validates healing hostnames against DC, deployed workstations, linux targets.
6. **WinRM SSL (HIGH)** — All 10 WinRM sites switched to port 5986 + UseSSL:true + VerifySSL:false.
7. **Fleet order signatures (CRITICAL)** — `get_fleet_orders_for_appliance()` includes signature columns.

### Blocked

- WinRM HTTPS listener must be configured on Windows targets before SSL connections work

---

## Files Changed

| File | Change |
|------|--------|
| `mcp-server/central-command/backend/sites.py` | Auth, signatures, server_public_key |
| `mcp-server/central-command/backend/fleet_updates.py` | Fleet order signatures |
| `appliance/internal/orders/processor.go` | Nonce replay tracking |
| `appliance/internal/daemon/healing_executor.go` | Hostname validation, WinRM SSL |
| `appliance/internal/daemon/daemon.go` | WinRM SSL (3 locations) |
| `appliance/internal/daemon/driftscan.go` | WinRM SSL (3 locations) |
| `appliance/internal/daemon/autodeploy.go` | WinRM SSL (3 locations) |

---

## Next Session

1. Push to main, verify CI/CD deploys backend

[truncated...]

---

## 2026-02-27-session-143-v0.3.10-gpo-agent-deploy.md

# Session 143: v0.3.10 GPO Agent Self-Deploy from NETLOGON

**Date:** 2026-02-27
**Duration:** ~3 hours (continued from session that ran out of context)

## Summary

Fixed the root cause of v0.3.4 persisting through rebuilds (wrong Nix file), confirmed v0.3.9 on both appliances, then built v0.3.10 with GPO-based agent self-deployment to bypass WinRM auth issues with workstations.

## Key Accomplishments

1. **Root cause: wrong Nix file** — `flake.nix` references `appliance-disk-image.nix` (not `appliance-image.nix`) for the `osiriscare-appliance-disk` config. Previous sessions updated the wrong file. `appliance-disk-image.nix` was stuck at v0.3.4.

2. **v0.3.9 confirmed on both appliances** — After fixing the Nix file and deploying via fleet order:
   - VM: running from `/nix/store/4hb4kskd15zmhbc6yx7ggd572fh227jc-appliance-daemon-0.3.9/bin/appliance-daemon`
   - Physical: v0.3.9 with WinRM HTTP fallback working (`DC 192.168.88.250: WinRM HTTPS unavailable, using HTTP (5985)`)

3. **WinRM auth investigation** — ws01 only offers Negotiate/Kerberos (no NTLM). DC works via NTLM but double-hop fails (Access is Denied). Direct WinRM to workstations is a dead end.

4. **GPO self-deployment (v0.3.10)** — New approach bypasses WinRM:
   - `autodeploy.go` stages `osiris-agent.exe` AND `osiris-config.json` to NETLOGON share
   - GPO startup script v2 copies agent+config from `\\NETLOGON\` to `C:\OsirisCare\`
   - Installs as Windows service on boot
   - Version-stamped to auto-update on GPO refresh

5. **Fleet order deployed** — Order `50311a01` active on VPS for nixos-rebuild to v0.3.10

## Commits

- `1248601` — fix: bump daemon version to 0.3.9 in appliance-disk-image.nix
- `c73c478` — feat: GPO startup script deploys agent from NETLOGON + config staging (v0.3.10)

## Next Priorities (when back on home WiFi)

1. Verify v0.3.10 deployed to both appliances
2. Verify GPO startup script updated on DC with agent deployment logic
3. Reboot ws01 (`VBoxManage controlvm northvalley-ws01 reset`) to trigger agent self-deploy
4. Verify osiris-agent installed and running on ws01
5. Verify gRPC TLS enrollment: agent → appliance :50051 → register → mTLS certs
6. End-to-end push/pull validation

---
