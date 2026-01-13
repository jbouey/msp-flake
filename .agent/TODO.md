# Current Tasks & Priorities

**Last Updated:** 2026-01-13 (Session 29 Continued - Learning Flywheel + Portal Link)
**Sprint:** Phase 12 - Launch Readiness (Agent v1.0.26, 43 Runbooks, OTS Anchoring, Linux+Windows Support, Windows Sensors, Partner Escalations, RBAC, Multi-Framework, Cloud Integrations, **Pattern Reporting**)

---

## Session 29 Continued (2026-01-13) - Learning Flywheel Pattern Reporting

### 1. Learning Flywheel Pattern Reporting
**Status:** COMPLETE
**Details:** Implemented full pipeline for L2→L1 pattern promotion.

#### Agent-side Changes (appliance_agent.py)
- [x] Added `report_pattern()` calls after successful L1/L2 healing
- [x] Four locations updated: local drift, Windows healing, Linux healing, network posture
- [x] Patterns include: check_type, issue_signature, resolution_steps, success, execution_time_ms

#### Server-side Endpoint (main.py on VPS)
- [x] Added `/agent/patterns` POST endpoint
- [x] Creates new patterns or updates existing (increments occurrences, tracks success_rate)
- [x] Uses generated `success_rate` column (calculated from occurrences/success_count)
- [x] Pattern ID generated from SHA256(pattern_signature)[:16]

#### Client-side Changes (appliance_client.py)
- [x] Updated `report_pattern()` to use `/agent/patterns` endpoint
- [x] Non-critical failures logged at debug level

#### Verified Working
```bash
curl -X POST 'http://178.156.162.116:8000/agent/patterns' \
  -H 'Content-Type: application/json' \
  -d '{"site_id":"test-site","check_type":"firewall",...}'
# Response: {"pattern_id":"cd070e6eb7f1c476","status":"created","occurrences":1,"success_rate":100.0}
```

### 2. Generate Portal Link Button
**Status:** COMPLETE
**Details:** Added button to SiteDetail page for generating client portal links.

- [x] Button in SiteDetail header calls `POST /api/portal/sites/{site_id}/generate-token`
- [x] Modal displays portal URL with copy-to-clipboard functionality
- [x] Security note about read-only access and regeneration
- [x] Deployed to VPS

**Files Modified:**
| File | Change |
|------|--------|
| `packages/compliance-agent/src/compliance_agent/appliance_agent.py` | Added `report_pattern()` calls |
| `packages/compliance-agent/src/compliance_agent/appliance_client.py` | Updated `report_pattern()` function |
| `mcp-server/main.py` (VPS) | Added `/agent/patterns` endpoint |
| `mcp-server/central-command/frontend/src/pages/SiteDetail.tsx` | Added Generate Portal Link button |

---

## Session 29 (2026-01-13) - Earlier

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

## Session 29 Continued - ISO v26 Deployment

### 1. Build ISO v26 with L2 JSON Fix
**Status:** COMPLETE
**Details:**
- Updated version in appliance-image.nix from 1.0.23 to 1.0.26
- Built ISO v26 on VPS: `/root/msp-iso-build/result-iso-v26/`
- Verified fix is in nix store: `compliance-agent-1.0.26`

### 2. Deploy to VM Appliance
**Status:** COMPLETE
**Details:**
- Transferred ISO to iMac gateway
- Attached ISO v26 to VirtualBox VM
- Booted VM with new ISO
- Configured L2 API key in `/var/lib/msp/config.yaml`

### 3. Verify L2 Working
**Status:** COMPLETE - L2 VERIFIED WORKING
**Details:**
- L2 successfully parsing Claude responses (no more "Extra data" errors)
- Observed L2 decisions with confidence scores:
  ```
  bitlocker_status → L2 decision: escalate (confidence: 0.90) → L3
  backup_status → L2 decision: run_backup_job (confidence: 0.80)
  ```
- L2 LLM planner making intelligent routing decisions

---

## Immediate (Next Session)

### 1. Build ISO v27 with Pattern Reporting
**Status:** PENDING
**Details:**
- Agent code updated with `report_pattern()` calls
- Server-side endpoint already deployed and working
- Build new ISO to include pattern reporting in agent
- Version bump to 1.0.27 needed

### 2. Deploy ISO v27 to Appliances
**Status:** PENDING
**Details:**
- Deploy to VM first (192.168.88.247), then physical appliance (192.168.88.246)
- Verify patterns flowing to Learning dashboard after healing events
- Patterns with 5+ occurrences and 90%+ success become promotion candidates

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
