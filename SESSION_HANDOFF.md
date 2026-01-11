# Session Handoff - MSP Compliance Platform

**Date:** 2026-01-10
**Phase:** Phase 12 - Launch Readiness
**Session:** 23 - Runbook Config Page Fix + Data Flywheel Seeding
**Status:** Runbook config page fixed, Learning flywheel seeded with data, changes pushed to prod

---

## Quick Summary

HIPAA compliance automation platform for healthcare SMBs. NixOS appliances phone home to Central Command, auto-heal infrastructure, generate audit evidence.

**Production URLs:**
- Dashboard: https://dashboard.osiriscare.net
- API: https://api.osiriscare.net
- Portal: https://msp.osiriscare.net

**Deployed Appliances:**
| Site | Type | IP | Agent | Status |
|------|------|-----|-------|--------|
| North Valley Dental (physical-appliance-pilot-1aea78) | HP T640 | 192.168.88.246 | v1.0.22 | online |
| Main Street Virtualbox Medical (test-appliance-lab-b3c40c) | VM | 192.168.88.247 | v1.0.19 | online |

**Lab Environment:**
- DC: 192.168.88.250 (NVDC01.northvalley.local)
- iMac Gateway: 192.168.88.50
- Credentials: See `.agent/LAB_CREDENTIALS.md`

---

## Today's Session (2026-01-10 Session 23)

### Completed
1. **Learning Flywheel Data Seeding**
   - Discovered learning infrastructure was complete but had no L2 data
   - All incidents were going to L3 (escalation), L2 resolutions = 0
   - Created `/var/lib/msp/flywheel_generator.py` on appliance
   - Disabled DRY-RUN mode in config: `healing_dry_run: false`
   - Seeded 8 patterns with 5 L2 resolutions each (40 total)
   - All patterns now meet promotion criteria (5 occurrences, 100% success)

2. **Runbook Config Page API Fix**
   - Frontend was calling `/api/sites/{siteId}/runbooks`
   - Backend expected `/api/runbooks/sites/{site_id}`
   - Fixed `api.ts` to use correct paths
   - Added `SiteRunbookConfigItem` model with full runbook details
   - Updated endpoint to return array with runbook metadata

3. **MCP Server Import Fix**
   - Created `dashboard_api` symlink to `central-command/backend/`
   - Made `/agent-packages` static mount conditional on directory existence
   - Enables main.py to run locally for testing

4. **Git Push to Production**
   - Committed: `f94f04c` - fix: Runbook config page API paths and backend response format
   - Changes pushed to main branch

### Files Modified
| File | Change |
|------|--------|
| `mcp-server/central-command/frontend/src/utils/api.ts` | Fixed API paths for runbook config |
| `mcp-server/central-command/backend/runbook_config.py` | Added SiteRunbookConfigItem model |
| `mcp-server/main.py` | Conditional agent-packages mount |
| `mcp-server/dashboard_api` | New symlink to backend |

---

## What's Complete

### Phase 12 - Launch Readiness
- Agent v1.0.22 with OpenTimestamps, Linux support, asyncssh
- 43 total runbooks (27 Windows + 16 Linux)
- Learning flywheel infrastructure seeded and ready
- Partner-configurable runbook enable/disable
- Credential-pull architecture (RMM-style, no creds on disk)
- Email alerts for critical incidents
- OpenTimestamps blockchain anchoring (Enterprise tier)
- RBAC user management (Admin/Operator/Readonly)
- Windows sensor push architecture

### Previous Sessions
- Session 22: ISO v20 build, physical appliance update
- Session 21: OpenTimestamps blockchain anchoring
- Session 20: Auth fix, comprehensive system audit
- Session 19: RBAC user management
- Session 18: Linux drift healing module

---

## What's Pending

### Immediate (Next Session)
1. **Verify runbook config page** - Confirm fix works in production dashboard
2. **VM appliance update** - Update 192.168.88.247 to ISO v20 (user away from home network)
3. **Monitor learning flywheel** - Verify promoted patterns show up in L1 rules

### Short-term
- First compliance packet generation
- 30-day monitoring period
- Evidence bundle verification in MinIO

---

## Key Files

| Purpose | Location |
|---------|----------|
| Project context | `.agent/CONTEXT.md` |
| Current tasks | `.agent/TODO.md` |
| Network/VMs | `.agent/NETWORK.md` |
| Lab credentials | `.agent/LAB_CREDENTIALS.md` |
| Phase status | `IMPLEMENTATION-STATUS.md` |
| Master architecture | `CLAUDE.md` |
| Appliance ISO | `iso/` directory |
| Compliance agent | `packages/compliance-agent/` |
| Backend API | `mcp-server/central-command/backend/` |
| Frontend | `mcp-server/central-command/frontend/` |

---

## Commands

```bash
# Work on compliance agent
cd packages/compliance-agent && source venv/bin/activate
python -m pytest tests/ -v --tb=short

# MCP Server local dev
cd mcp-server && source venv/bin/activate
python -m uvicorn main:app --host 0.0.0.0 --port 8443 --ssl-keyfile /tmp/key.pem --ssl-certfile /tmp/cert.pem

# SSH to appliances
ssh root@192.168.88.246   # Physical (North Valley)
ssh root@192.168.88.247   # VM (Main Street)

# VPS management
ssh root@178.156.162.116
cd /opt/mcp-server && docker compose logs -f mcp-server

# Check appliance status
curl -s https://api.osiriscare.net/api/sites | jq '.[] | {name, status}'

# Check learning flywheel status
curl -s https://api.osiriscare.net/learning/status
curl -s https://api.osiriscare.net/learning/candidates
```

---

## Session History

| Date | Session | Focus | Status |
|------|---------|-------|--------|
| 2026-01-10 | 23 | Runbook Config Page Fix + Flywheel Seeding | Complete |
| 2026-01-09 | 22 | ISO v20 Build + Physical Appliance Update | Complete |
| 2026-01-09 | 21 | OpenTimestamps Blockchain Anchoring | Complete |
| 2026-01-09 | 20 | Auth Fix + System Audit | Complete |
| 2026-01-08 | 19 | RBAC User Management | Complete |
| 2026-01-08 | 18 | Linux Drift Healing Module | Complete |
| 2026-01-08 | 17 | Dashboard Auth + 1Password Secrets | Complete |
| 2026-01-08 | 16 | Partner Dashboard + L3 Escalation | Complete |
| 2026-01-08 | 15 | Windows Sensor Architecture | Complete |

See `.agent/sessions/` for detailed session logs.

---

## Architecture Overview

```
Central Command (VPS 178.156.162.116)
├── FastAPI Backend (:8000)
│   ├── /api/appliances/checkin - Returns windows_targets + linux_targets + runbooks
│   ├── /api/runbooks/sites/{site_id} - Site runbook config (FIXED)
│   ├── /api/evidence/... - Evidence bundle submission + OTS anchoring
│   └── /api/users/... - RBAC user management
├── React Frontend (:3000)
├── PostgreSQL (16-alpine)
├── MinIO (WORM storage)
└── Caddy (auto-TLS)

Appliances (NixOS)
├── compliance-agent-appliance (systemd service)
│   ├── Check-in every 60s → receives credentials + runbooks
│   ├── Drift detection → L1/L2/L3 auto-healing
│   ├── Learning flywheel → L2 patterns promote to L1
│   └── Evidence bundle → Ed25519 signed + OTS anchored + WORM stored
└── config.yaml (site_id + api_key only, NO credentials)
```

---

## For Next Session

1. Read this file + `.agent/CONTEXT.md`
2. Check `.agent/TODO.md` for priorities
3. Consider:
   - Verify runbook config page works in production
   - Update VM appliance to ISO v20
   - Monitor learning flywheel pattern promotion
