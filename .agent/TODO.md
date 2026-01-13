# Current Tasks & Priorities

**Last Updated:** 2026-01-13 (Session 29 - L1/L2/L3 Fixes + Frontend Fixes)
**Sprint:** Phase 12 - Launch Readiness (Agent v1.0.26, 43 Runbooks, OTS Anchoring, Linux+Windows Support, Windows Sensors, Partner Escalations, RBAC, Multi-Framework, Cloud Integrations)

---

## Session 29 (2026-01-13)

### L1/L2/L3 Auto-Healing Fixes + Frontend Fixes
**Status:** COMPLETE
**Details:** Fixed critical issues with auto-healing escalation system and frontend pages.

#### L1 Rule Fixes (main.py)
- [x] **Status mismatch:** Rules checked for `"non_compliant"` but SimpleDriftChecker returns `"pass"`, `"warning"`, `"fail"`, `"error"`
- [x] Fix: Changed `"operator": "eq", "value": "non_compliant"` to `"operator": "in", "value": ["warning", "fail", "error"]`
- [x] **Check type mismatch:** Fixed wrong names (e.g., `firewall_enabled` to `firewall`)
- [x] Affected rules: NTP sync, critical services, disk space, firewall, NixOS generation

#### L3 Notification Deduplication Fix (main.py:885-930)
- [x] Problem: L3 escalation notifications blocked by older incident notifications
- [x] Root cause: Dedup query matched across categories (incident vs escalation)
- [x] Fix: Added `category` filter to dedup query - now checks same category only

#### Windows Backup Check (appliance_agent.py:1247-1364)
- [x] Added `backup_status` check using `Get-WBSummary` PowerShell
- [x] Status logic: pass (<24h), warning (<72h or not installed), fail (>72h or no policy)

#### Learning Page Fix (db_queries.py:205-215)
- [x] Problem: Page showing zeros/untethered from real data
- [x] Root cause: Query filtered by `status = 'resolved'` but incidents have `resolving`/`escalated`
- [x] Fix: Changed to `resolution_tier IS NOT NULL`

#### Admin Login Fix
- [x] Reset password hash for admin user (admin / Admin123)
- [x] Verified login working

#### L2 LLM Configuration
- [x] Enabled L2 on physical appliance with Anthropic API key
- [x] L2 now initializing: "L1=True, L2=True, L3=True"
- [x] L2 calling Claude 3.5 Haiku for incident analysis

#### L2 JSON Parsing Fix (level2_llm.py:374-390)
- [x] Problem: "Failed to parse LLM response: Extra data: line 14 column 1"
- [x] Root cause: Code skipped brace-matching when text started with `{`
- [x] Fix: Always extract JSON object using brace-matching, even if text starts with `{`

#### Frameworks API Fix (main.py)
- [x] Problem: `/api/frameworks/metadata` returning 404
- [x] Root cause: `frameworks_router` not imported or mounted in main.py
- [x] Fix: Added import and `app.include_router(frameworks_router)`

#### Incidents Page Fix (Frontend)
- [x] Problem: "View All" incidents link led to blank page
- [x] Root cause: No `/incidents` route defined in App.tsx
- [x] Fix: Created `Incidents.tsx` page with all/active/resolved filters
- [x] Added route and export

**Files Modified:**
| File | Change |
|------|--------|
| `mcp-server/main.py` | Added frameworks_router import/mount |
| `mcp-server/central-command/frontend/src/pages/Incidents.tsx` | NEW - Incidents page |
| `mcp-server/central-command/frontend/src/pages/index.ts` | Added Incidents export |
| `mcp-server/central-command/frontend/src/App.tsx` | Added /incidents route |
| `packages/compliance-agent/src/compliance_agent/level2_llm.py` | L2 JSON parsing fix |
| `packages/compliance-agent/setup.py` | Version bump to 1.0.26 |
| VPS main.py | L1 rules, L3 dedup, frameworks router |
| VPS db_queries.py | Learning page query fix |

---

## Immediate (Next Session)

### 1. Deploy L2 JSON Fix to Appliances
**Status:** PENDING
**Details:**
- ISO v24 is built but uses cached agent 1.0.23
- L2 JSON fix is in local `level2_llm.py` but not in ISO
- Need to: Commit, push, pull on VPS, rebuild ISO with updated source
- Then flash to appliances

### 2. Verify L2 Resolutions Working
**Status:** PENDING
**Details:**
- After deploying fix, verify L2 is successfully parsing Claude responses
- Check incidents are being resolved via L2 (not all going to L3)
- Monitor Learning page for L2 resolution count

### 3. Connect Additional Cloud Providers
**Status:** PENDING
**Details:**
- Google Workspace OAuth
- Okta OAuth
- Azure AD OAuth

---

## Short-term

- First compliance packet generation
- 30-day monitoring period
- Evidence bundle verification in MinIO
- Test framework scoring with real appliance data

---

## Session 28 (2026-01-12)

### Cloud Integration Frontend Fixes & Verification
**Status:** COMPLETE
(See SESSION_HANDOFF.md for details)

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

**Check L2 logs on appliance:**
```bash
journalctl -u compliance-agent -f | grep -i "l2\|llm"
```

**Rebuild ISO on VPS:**
```bash
cd /root/msp-iso-build && git pull && nix build .#appliance-iso -o result-iso-v25
```
